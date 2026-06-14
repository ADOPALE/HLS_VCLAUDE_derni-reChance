"""
driver_scheduler.py
===================
Synthèse des postes chauffeurs à partir des PosteSolution.

Dans ce modèle, un poste = un chauffeur (un chauffeur conduit un seul véhicule
sur un poste). Un même véhicule peut enchaîner deux postes (deux chauffeurs),
ce qui est déjà reflété par les instances et les heures de début/fin.
"""

from __future__ import annotations

from .time_windows import min_to_hhmm
from . import config as cfg


def ligne_chauffeur(p, params: cfg.SimulationParams) -> dict:
    duree_poste = p.fin - p.debut
    t_utile = p.t_conduite + p.t_manutention + p.t_quai
    return {
        "Poste": p.id,
        "Véhicule": p.vehicule_instance,
        "Début": min_to_hhmm(p.debut),
        "Fin": min_to_hhmm(p.fin),
        "Durée poste (min)": duree_poste,
        "Prise poste (min)": params.prise_poste_min,
        "Fin poste (min)": params.fin_poste_min,
        "Pause (min)": params.duree_pause_min,
        "Conduite (min)": round(p.t_conduite),
        "Manutention (min)": round(p.t_manutention),
        "Mise à quai (min)": round(p.t_quai),
        "Désinfection (min)": round(p.t_desinfection),
        "Attente/inoccupé (min)": round(p.t_attente),
        "Nb chargements": p.nb_chargements,
        "Taux occupation utile %": round(100 * t_utile / max(1, duree_poste), 1),
        "Remplissage surface %": round(p.rempl_surf_pct, 1),
        "Remplissage poids %": round(p.rempl_poids_pct, 1),
    }
