"""
fleet_generator.py
==================
Orchestration de l'optimisation par jour et synthèse de la flotte.

L'heuristique d'optimisation (optimizer.py) dérive directement le nombre minimal
de véhicules et de postes nécessaires en réutilisant les instances au plus tôt.
Ce module lance l'optimisation d'un jour et calcule les agrégats de flotte.

NOTE : l'énumération exhaustive « 0,1,2,3 véhicules par type » décrite dans le
cahier des charges est prévue dans l'architecture (paramètre max_par_type) mais
la version actuelle utilise la dérivation gloutonne, plus rapide à l'échelle du
fichier réel. Voir README, section Limites.
"""

from __future__ import annotations

from collections import defaultdict

from .models import Vehicule, Site
from .route_builder import construire_chargements, Chargement
from . import optimizer
from . import config as cfg


def optimiser(jour: str, unites, vehicules: dict[str, Vehicule], sites: dict[str, Site],
              contenants, duree, dist, params: cfg.SimulationParams):
    chargements = construire_chargements(unites, vehicules, sites, contenants, params)
    postes, non_servis = optimizer.optimiser_jour(
        jour, chargements, vehicules, sites, duree, dist, params, contenants)
    return postes, non_servis, chargements


def synthese_flotte(postes) -> dict[str, dict]:
    """Compte les véhicules (instances distinctes) et postes par type."""
    par_type = defaultdict(lambda: {"instances": set(), "postes": 0})
    for p in postes:
        par_type[p.vehicule_type]["instances"].add(p.vehicule_instance)
        par_type[p.vehicule_type]["postes"] += 1
    return {t: {"vehicules": len(d["instances"]), "postes": d["postes"]}
            for t, d in par_type.items()}
