"""
route_builder.py
================
Construit des CHARGEMENTS (loads) à partir des unités de transport, puis les
séquence en mini-tournées multi-collectes / multi-livraisons.

Un chargement est un ensemble d'unités qui :
- sont mutuellement compatibles (mixité, propre/sale, exclusions) ;
- tiennent ensemble dans un véhicule (bin-packing 2D) ;
- ont des fenêtres horaires conciliables.

Les unités « pleines » (camion plein) forment chacune un chargement direct.
Les reliquats sont regroupés par logique look-forward (même origine et/ou même
destination, fenêtres proches) dans le plus petit véhicule compatible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Vehicule, Site, Contenant, UniteTransport
from . import binpacking, compatibility
from . import config as cfg


@dataclass
class Chargement:
    unites: list[UniteTransport] = field(default_factory=list)
    vehicule_type: str = ""
    sites_collecte: list[str] = field(default_factory=list)
    sites_livraison: list[str] = field(default_factory=list)
    debut_au_plus_tot: int = 0
    fin_au_plus_tard: int = 1440
    sale: bool = False
    nb_contenants: int = 0
    surface_m2: float = 0.0
    poids_t: float = 0.0


def _rects_unite(u: UniteTransport, contenants: dict[str, Contenant]) -> list[tuple[float, float]]:
    c = contenants.get(u.contenant)
    if not c:
        return []
    return [(c.longueur_m, c.largeur_m)] * u.nb_contenants


def _tiennent_ensemble(unites: list[UniteTransport], veh: Vehicule,
                       contenants: dict[str, Contenant], params: cfg.SimulationParams) -> bool:
    rects = []
    poids = 0.0
    for u in unites:
        rects.extend(_rects_unite(u, contenants))
        poids += u.poids_t
    if poids > veh.poids_max_t + 1e-9:
        return False
    return binpacking.contenants_tiennent(
        rects, veh.longueur_m, veh.largeur_m,
        params.taux_occupation_surface, params.autoriser_rotation_90)


def _fenetres_conciliables(a: UniteTransport, b: UniteTransport, horizon: int) -> bool:
    amin = a.heure_min_collecte or 0
    bmin = b.heure_min_collecte or 0
    amax = a.heure_max_livraison or 1440
    bmax = b.heure_max_livraison or 1440
    # chevauchement raisonnable des fenêtres
    if max(amin, bmin) > min(amax, bmax):
        return False
    return abs(amin - bmin) <= horizon or abs(amax - bmax) <= horizon


def _plus_petit_vehicule_compatible(unites, vehicules, sites, contenants, params):
    """Plus petit véhicule (par surface) qui accepte ce groupe."""
    candidats = []
    for v in vehicules.values():
        ok = True
        for u in unites:
            if not compatibility.vehicule_compatible_contenant(v, u.contenant):
                ok = False
                break
            sd = sites.get(u.site_depart)
            sa = sites.get(u.site_arrivee)
            if sd and not compatibility.vehicule_compatible_site(v, sd):
                ok = False
                break
            if sa and not compatibility.vehicule_compatible_site(v, sa):
                ok = False
                break
        if ok and _tiennent_ensemble(unites, v, contenants, params):
            candidats.append(v)
    if not candidats:
        return None
    return min(candidats, key=lambda v: v.surface_m2)


def _finaliser(ch: Chargement, contenants):
    sc, sl = [], []
    sale = False
    nb = 0
    surf = 0.0
    poids = 0.0
    dmin = 0
    fmax = 1440
    for u in ch.unites:
        if u.site_depart not in sc:
            sc.append(u.site_depart)
        if u.site_arrivee not in sl:
            sl.append(u.site_arrivee)
        sale = sale or u.sale
        nb += u.nb_contenants
        surf += u.volume_m2
        poids += u.poids_t
        dmin = max(dmin, u.heure_min_collecte or 0)
        fmax = min(fmax, u.heure_max_livraison or 1440)
    ch.sites_collecte = sc
    ch.sites_livraison = sl
    ch.sale = sale
    ch.nb_contenants = nb
    ch.surface_m2 = surf
    ch.poids_t = poids
    ch.debut_au_plus_tot = dmin
    ch.fin_au_plus_tard = fmax
    return ch


def construire_chargements(unites: list[UniteTransport], vehicules: dict[str, Vehicule],
                           sites: dict[str, Site], contenants: dict[str, Contenant],
                           params: cfg.SimulationParams) -> list[Chargement]:
    """
    Construit la liste des chargements à router.
    - unités non-reliquat (pleines) : un chargement chacune ;
    - reliquats : regroupement look-forward.
    """
    chargements: list[Chargement] = []

    pleins = [u for u in unites if not u.est_reliquat]
    reliquats = [u for u in unites if u.est_reliquat]

    # 1) chargements pleins (véhicule le plus capacitaire déjà choisi à l'éclatement)
    from .preprocessing import vehicule_le_plus_capacitaire
    from .models import Flux  # noqa
    for u in pleins:
        # retrouver le type de véhicule le plus capacitaire pour cette unité
        v = _plus_petit_vehicule_compatible([u], vehicules, sites, contenants, params)
        # pour un plein on veut le plus capacitaire ; on prend le plus grand compatible
        best = None
        best_s = -1
        for veh in vehicules.values():
            if compatibility.vehicule_compatible_contenant(veh, u.contenant) and \
               _tiennent_ensemble([u], veh, contenants, params):
                if veh.surface_m2 > best_s:
                    best_s = veh.surface_m2
                    best = veh
        chosen = best or v
        if chosen is None:
            continue
        ch = Chargement(unites=[u], vehicule_type=chosen.type_nom)
        chargements.append(_finaliser(ch, contenants))

    # 2) reliquats : regroupement glouton look-forward
    reliquats.sort(key=lambda u: (u.heure_min_collecte or 0, u.site_depart))
    utilises = [False] * len(reliquats)
    for i, u in enumerate(reliquats):
        if utilises[i]:
            continue
        groupe = [u]
        # fenêtre commune courante du groupe
        gmin = u.heure_min_collecte or 0
        gmax = u.heure_max_livraison or 1440
        utilises[i] = True
        for j in range(i + 1, len(reliquats)):
            if utilises[j]:
                continue
            cand = reliquats[j]
            cmin = cand.heure_min_collecte or 0
            cmax = cand.heure_max_livraison or 1440
            # la fenêtre commune doit rester valide (collecte <= livraison)
            new_min = max(gmin, cmin)
            new_max = min(gmax, cmax)
            if new_min > new_max:
                continue
            # proximité temporelle (look-forward borné)
            if cmin - gmin > params.look_forward_horizon_min:
                continue
            # compatibilité avec TOUS les membres du groupe
            if any(not compatibility.unites_combinables(m, cand)[0] for m in groupe):
                continue
            # privilégier même origine OU même destination
            if cand.site_depart != u.site_depart and cand.site_arrivee != u.site_arrivee:
                continue
            test = groupe + [cand]
            veh = _plus_petit_vehicule_compatible(test, vehicules, sites, contenants, params)
            if veh is not None:
                groupe.append(cand)
                gmin, gmax = new_min, new_max
                utilises[j] = True
        veh = _plus_petit_vehicule_compatible(groupe, vehicules, sites, contenants, params)
        if veh is None:
            veh = _plus_petit_vehicule_compatible([u], vehicules, sites, contenants, params)
            groupe = [u]
        ch = Chargement(unites=groupe, vehicule_type=(veh.type_nom if veh else ""))
        chargements.append(_finaliser(ch, contenants))

    return chargements
