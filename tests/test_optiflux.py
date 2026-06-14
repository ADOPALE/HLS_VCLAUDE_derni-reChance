"""
Tests unitaires de base pour OptiFLUX.
Lancement :  python -m pytest tests/  (ou)  python tests/test_optiflux.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optiflux import config as cfg
from optiflux import binpacking, compatibility, data_cleaning, pipeline
from optiflux.models import UniteTransport

EXEMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "sample", "OptiFLUX_exemple.xlsx")


# ---------------------------------------------------------------- conversions
def test_to_minutes():
    import datetime
    assert data_cleaning.to_minutes(datetime.time(6, 30)) == 390
    assert data_cleaning.to_minutes("08:00") == 480
    assert data_cleaning.to_minutes(None) is None


def test_to_bool():
    assert data_cleaning.to_bool("OUI") is True
    assert data_cleaning.to_bool("non") is False
    assert data_cleaning.to_bool(None, defaut=True) is True


def test_isblank():
    assert data_cleaning._isblank(None)
    assert data_cleaning._isblank("nan")
    assert data_cleaning._isblank(float("nan"))
    assert not data_cleaning._isblank("HSJ")


# ---------------------------------------------------------------- bin-packing
def test_binpacking_simple():
    # 4 contenants 1x1 dans un véhicule 2x2 utile à 100 % : tient
    assert binpacking.contenants_tiennent([(1, 1)] * 4, 2, 2, 1.0, True)
    # 5 ne tiennent pas
    assert not binpacking.contenants_tiennent([(1, 1)] * 5, 2, 2, 1.0, True)


def test_binpacking_rotation():
    # un contenant 2x1 dans un véhicule 1x2 ne tient que par rotation
    assert binpacking.contenants_tiennent([(2, 1)], 1.0, 2.0, 1.0, True)
    assert not binpacking.contenants_tiennent([(2, 1)], 1.0, 2.0, 1.0, False)


def test_capacite_plafond_poids():
    # surface OK pour beaucoup, mais poids limite à 2
    n = binpacking.capacite_max_homogene(0.5, 0.5, 10, 10, 1.0,
                                         poids_unit_t=1.0, poids_max_t=2.0)
    assert n == 2


# ---------------------------------------------------------------- compatibilité
def _u(uid, sale, mixte=True, excl=""):
    return UniteTransport(uid=uid, flux_id=uid, fonction_support="X", site_depart="A",
                          site_arrivee="B", contenant="c", nb_contenants=1, sale=sale,
                          plein=True, transport_mixte=mixte, regles_exclusion=excl,
                          heure_min_collecte=None, heure_max_livraison=None)


def test_mixite_interdite():
    a = _u("F1", False, mixte=False)
    b = _u("F2", False)
    ok, _ = compatibility.unites_combinables(a, b)
    assert not ok


def test_exclusion_sale_propre():
    propre = _u("F1", False, excl="ne pas mélanger avec sale")
    sale = _u("F2", True)
    ok, _ = compatibility.unites_combinables(propre, sale)
    assert not ok


def test_desinfection():
    assert compatibility.besoin_desinfection("sale", prochaine_unite_sale=False)
    assert not compatibility.besoin_desinfection("sale", prochaine_unite_sale=True)
    assert not compatibility.besoin_desinfection("propre", prochaine_unite_sale=False)


# ---------------------------------------------------------------- bout-en-bout
def test_pipeline_exemple():
    if not os.path.exists(EXEMPLE):
        return  # fichier d'exemple absent : test ignoré
    params = cfg.SimulationParams()
    ds, manquants = pipeline.charger_dataset(EXEMPLE, params)
    assert not manquants
    assert len(ds.flux) > 0
    assert len(ds.sites) > 0
    params.vehicules_autorises = [v for v in ds.vehicules if v != "SEMI-REMORQUE"]
    res = pipeline.resoudre_jour(ds, "Lundi", params)
    # 100 % servi attendu sur l'exemple
    assert len(res["non_servis"]) == 0
    assert len(res["postes"]) > 0


# ---------------------------------------------------- refonte du moteur (v1.1)
def test_creneaux_vacation():
    p = cfg.SimulationParams()
    cr = p.creneaux_vacation()
    assert len(cr) == p.nb_vacations_max
    # 1er créneau démarre à l'heure mini, créneaux contigus
    assert cr[0][0] == p.heure_debut_mini_min
    assert cr[1][0] == cr[0][1]


def test_chainage_deux_vacations():
    if not os.path.exists(EXEMPLE):
        return
    params = cfg.SimulationParams()
    ds, _ = pipeline.charger_dataset(EXEMPLE, params)
    params.vehicules_autorises = [v for v in ds.vehicules if v != "SEMI-REMORQUE"]
    res = pipeline.resoudre_jour(ds, "Lundi", params)
    from collections import Counter
    c = Counter(p.vehicule_instance for p in res["postes"])
    # au moins un véhicule enchaîne deux postes (deux vacations)
    assert max(c.values()) == 2


def test_seuil_occupation_evaluation():
    if not os.path.exists(EXEMPLE):
        return
    params = cfg.SimulationParams()
    ds, _ = pipeline.charger_dataset(EXEMPLE, params)
    params.vehicules_autorises = [v for v in ds.vehicules if v != "SEMI-REMORQUE"]
    res = pipeline.resoudre_jour(ds, "Lundi", params)
    s = res["seuil_occupation"]
    # le frigo bio est exclu du contrôle par défaut
    assert "VL FRIGO BIO" not in s["types_controles"]
    assert "acceptable" in s and "violations" in s


def test_remplissage_calcule():
    if not os.path.exists(EXEMPLE):
        return
    params = cfg.SimulationParams()
    ds, _ = pipeline.charger_dataset(EXEMPLE, params)
    params.vehicules_autorises = [v for v in ds.vehicules if v != "SEMI-REMORQUE"]
    res = pipeline.resoudre_jour(ds, "Lundi", params)
    # les gros porteurs doivent afficher un remplissage surfacique non nul
    pl = [p for p in res["postes"] if p.vehicule_type == "PL 19T"]
    assert pl and max(p.rempl_surf_pct for p in pl) > 20


def test_histogramme_par_contenant():
    if not os.path.exists(EXEMPLE):
        return
    from optiflux import visualization as viz
    params = cfg.SimulationParams()
    ds, _ = pipeline.charger_dataset(EXEMPLE, params)
    fig = viz.histogramme_flux_par_contenant(ds, list(cfg.DAYS))
    assert len(fig.data) > 0  # au moins une série (un type de contenant)


def test_duree_poste_fixe():
    if not os.path.exists(EXEMPLE):
        return
    params = cfg.SimulationParams()
    ds, _ = pipeline.charger_dataset(EXEMPLE, params)
    params.vehicules_autorises = [v for v in ds.vehicules if v != "SEMI-REMORQUE"]
    res = pipeline.resoudre_jour(ds, "Lundi", params)
    # tous les postes durent exactement une vacation
    for p in res["postes"]:
        assert (p.fin - p.debut) == params.duree_vacation_min


def test_libelle_flux():
    if not os.path.exists(EXEMPLE):
        return
    from optiflux.outputs import libelle_flux
    params = cfg.SimulationParams()
    ds, _ = pipeline.charger_dataset(EXEMPLE, params)
    f = ds.flux[0]
    lib = libelle_flux(f)
    assert f.site_depart in lib and f.site_arrivee in lib
    assert "_" in lib


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
            ok += 1
        except Exception:  # noqa: BLE001
            print(f"  ✗ {fn.__name__}")
            traceback.print_exc()
    print(f"\n{ok}/{len(fns)} tests réussis.")
