"""
preprocessing.py
================
Pour un jour donné :
1. sélectionne les flux actifs (quantité > 0) et les fonctions support retenues ;
2. éclate chaque flux en UNITÉS DE TRANSPORT via le bin-packing 2D :
   - autant de véhicules pleins que possible avec le véhicule compatible le plus
     capacitaire ;
   - un reliquat (reste) marqué comme groupable (look-forward) ;
3. réalise le contrôle de faisabilité unitaire (fenêtre horaire).
"""

from __future__ import annotations

from .models import Vehicule, Site, Contenant, Flux, UniteTransport
from . import binpacking, compatibility, time_windows
from . import config as cfg


def vehicules_selectionnes(vehicules: dict[str, Vehicule],
                           params: cfg.SimulationParams) -> dict[str, Vehicule]:
    if not params.vehicules_autorises:
        return dict(vehicules)
    return {k: v for k, v in vehicules.items() if k in params.vehicules_autorises}


def vehicule_le_plus_capacitaire(flux: Flux, vehicules: dict[str, Vehicule],
                                 sites: dict[str, Site], contenants: dict[str, Contenant],
                                 params: cfg.SimulationParams) -> tuple[Vehicule | None, int]:
    """
    Retourne (véhicule compatible le plus capacitaire, capacité homogène) pour ce flux.
    Compatible = contenant OK + sites départ/arrivée OK.
    """
    cont = contenants.get(flux.contenant)
    if cont is None:
        return None, 0
    poids_unit = cont.poids_plein_t if flux.plein else cont.poids_vide_t
    best = None
    best_cap = 0
    for v in vehicules.values():
        if not compatibility.vehicule_compatible_contenant(v, flux.contenant):
            continue
        sd = sites.get(flux.site_depart)
        sa = sites.get(flux.site_arrivee)
        if sd and not compatibility.vehicule_compatible_site(v, sd):
            continue
        if sa and not compatibility.vehicule_compatible_site(v, sa):
            continue
        cap = binpacking.capacite_max_homogene(
            cont.longueur_m, cont.largeur_m, v.longueur_m, v.largeur_m,
            params.taux_occupation_surface, poids_unit, v.poids_max_t,
            params.autoriser_rotation_90)
        if cap > best_cap:
            best_cap = cap
            best = v
    return best, best_cap


def eclater_flux(flux: Flux, jour: str, vehicules: dict[str, Vehicule],
                 sites: dict[str, Site], contenants: dict[str, Contenant],
                 params: cfg.SimulationParams) -> tuple[list[UniteTransport], str]:
    """
    Éclate un flux du jour en unités de transport.
    Retourne (unites, message_erreur). Si message non vide -> flux infaisable.
    """
    qte = flux.quantite(jour)
    if qte <= 0:
        return [], ""
    cont = contenants.get(flux.contenant)
    if cont is None:
        return [], f"Contenant '{flux.contenant}' inconnu"

    veh, cap = vehicule_le_plus_capacitaire(flux, vehicules, sites, contenants, params)
    if veh is None or cap <= 0:
        return [], f"Aucun véhicule sélectionné ne peut transporter le contenant '{flux.contenant}'"

    poids_unit = cont.poids_plein_t if flux.plein else cont.poids_vide_t
    surf_unit = cont.surface_m2

    unites: list[UniteTransport] = []
    restant = qte
    i = 0
    while restant > 0:
        n = min(cap, restant)
        est_reliquat = (n < cap)  # le dernier morceau non plein est un reliquat
        i += 1
        unites.append(UniteTransport(
            uid=f"{flux.id}-{jour[:3]}-{i}",
            flux_id=flux.id,
            fonction_support=flux.fonction_support,
            site_depart=flux.site_depart,
            site_arrivee=flux.site_arrivee,
            contenant=flux.contenant,
            nb_contenants=n,
            sale=flux.sale,
            plein=flux.plein,
            transport_mixte=flux.transport_mixte,
            regles_exclusion=flux.regles_exclusion,
            heure_min_collecte=flux.heure_min_collecte,
            heure_max_livraison=flux.heure_max_livraison,
            urgent=flux.urgent,
            volume_m2=surf_unit * n,
            poids_t=poids_unit * n,
            est_reliquat=est_reliquat,
        ))
        restant -= n
    return unites, ""


def preparer_jour(jour: str, flux_list: list[Flux], vehicules: dict[str, Vehicule],
                  sites: dict[str, Site], contenants: dict[str, Contenant],
                  duree: dict[tuple[str, str], float], params: cfg.SimulationParams):
    """
    Prépare un jour complet. Retourne (unites, flux_incompatibles).
    flux_incompatibles : liste de dict {flux_id, raison, suggestion}.
    """
    veh_sel = vehicules_selectionnes(vehicules, params)
    unites: list[UniteTransport] = []
    incompatibles: list[dict] = []

    fonctions = set(params.fonctions_support) if params.fonctions_support else None

    for f in flux_list:
        if not f.actif(jour):
            continue
        if fonctions is not None and f.fonction_support not in fonctions:
            continue

        u_list, err = eclater_flux(f, jour, veh_sel, sites, contenants, params)
        if err:
            incompatibles.append({"flux_id": f.id, "raison": err, "suggestion": ""})
            continue

        # contrôle de faisabilité unitaire (fenêtre horaire) sur chaque unité
        veh, _ = vehicule_le_plus_capacitaire(f, veh_sel, sites, contenants, params)
        dtrajet = params.duree_avec_circulation(duree.get((f.site_depart, f.site_arrivee), 0.0))
        sd = sites.get(f.site_depart)
        presence_quai = sd.presence_quai if sd else False
        for u in u_list:
            ok, tmin, largeur = time_windows.flux_faisable(u, veh, dtrajet, presence_quai, params)
            if not ok:
                debut, fin = time_windows.fenetre_disponible(u, params)
                besoin = int(round(tmin))
                suggestion = (f"Fenêtre actuelle {time_windows.min_to_hhmm(debut)}–"
                              f"{time_windows.min_to_hhmm(fin)} ({int(largeur)} min) "
                              f"insuffisante. Besoin ≈ {besoin} min. "
                              f"Élargir à au moins {time_windows.min_to_hhmm(debut)}–"
                              f"{time_windows.min_to_hhmm(debut + besoin)}.")
                incompatibles.append({
                    "flux_id": f.id,
                    "raison": f"Temps minimal {besoin} min > fenêtre {int(largeur)} min",
                    "suggestion": suggestion,
                })
                break
        else:
            unites.extend(u_list)

    return unites, incompatibles
