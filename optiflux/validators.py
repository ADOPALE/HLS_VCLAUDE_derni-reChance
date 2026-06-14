"""
validators.py
=============
Contrôles de cohérence des données importées. Produit une liste de messages
classés en trois niveaux :
- BLOQUANT : empêche le lancement de l'optimisation
- ALERTE   : à corriger mais non bloquant
- INFO     : information

Chaque message est un dict standardisé.
"""

from __future__ import annotations

from .models import Site, Vehicule, Contenant, Flux
from . import config as cfg

BLOQUANT = "BLOQUANT"
ALERTE = "ALERTE"
INFO = "INFO"


def _msg(niveau, type_, detail, ref=""):
    return {"niveau": niveau, "type": type_, "detail": detail, "ref": ref}


def valider(
    sites: dict[str, Site],
    vehicules: dict[str, Vehicule],
    contenants: dict[str, Contenant],
    flux: list[Flux],
    duree: dict[tuple[str, str], float],
    dist: dict[tuple[str, str], float],
    params: cfg.SimulationParams,
) -> list[dict]:
    messages: list[dict] = []

    # --- Sites référencés dans les flux existent ---
    sites_flux = set()
    for f in flux:
        sites_flux.add(f.site_depart)
        sites_flux.add(f.site_arrivee)
    for s in sorted(sites_flux):
        if s not in sites:
            messages.append(_msg(BLOQUANT, "site_inconnu",
                                 f"Le site '{s}' apparaît dans M flux mais est absent de param Sites.", s))

    # --- Sites présents dans les matrices ---
    sites_duree = {o for (o, d) in duree} | {d for (o, d) in duree}
    for s in sorted(sites_flux):
        if s in sites and s not in sites_duree:
            messages.append(_msg(BLOQUANT, "site_hors_matrice",
                                 f"Le site '{s}' est absent de la matrice Durée.", s))

    # --- Contenants référencés existent ---
    for f in flux:
        if f.contenant and f.contenant not in contenants:
            messages.append(_msg(ALERTE, "contenant_inconnu",
                                 f"Le contenant '{f.contenant}' (flux {f.id}) est absent de param Contenants.",
                                 f.id))

    # --- Véhicules : capacité et stationnement ---
    for v in vehicules.values():
        if v.poids_max_t <= 0 or v.surface_m2 <= 0:
            messages.append(_msg(BLOQUANT, "vehicule_sans_capacite",
                                 f"Le véhicule '{v.type_nom}' n'a pas de capacité renseignée.", v.type_nom))
        if not v.stationnement_initial:
            messages.append(_msg(BLOQUANT, "vehicule_sans_depot",
                                 f"Le véhicule '{v.type_nom}' n'a pas de stationnement initial.", v.type_nom))

    # --- Matrices complètes (au moins pour les sites des flux) ---
    sites_a_couvrir = [s for s in sorted(sites_flux) if s in sites]
    manquants = 0
    for o in sites_a_couvrir:
        for d in sites_a_couvrir:
            if o != d and (o, d) not in duree:
                manquants += 1
    if manquants:
        messages.append(_msg(ALERTE, "matrice_incomplete",
                             f"{manquants} paires de sites n'ont pas de durée définie (valeur 0 utilisée par défaut)."))

    # --- Fonctions support / véhicules sélectionnés ---
    if params.vehicules_autorises:
        inconnus = [v for v in params.vehicules_autorises if v not in vehicules]
        for v in inconnus:
            messages.append(_msg(ALERTE, "vehicule_selectionne_inconnu",
                                 f"Type de véhicule sélectionné inconnu : '{v}'.", v))

    return messages


def a_des_bloquants(messages: list[dict]) -> bool:
    return any(m["niveau"] == BLOQUANT for m in messages)
