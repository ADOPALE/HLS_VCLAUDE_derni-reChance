"""
time_windows.py
===============
Gestion des horaires : conversion minutes <-> HH:MM, et contrôle de
faisabilité unitaire d'un flux (le temps minimal nécessaire tient-il dans
la fenêtre horaire ?).
"""

from __future__ import annotations

from .models import Vehicule, UniteTransport
from . import config as cfg


def min_to_hhmm(m: int | float | None) -> str:
    if m is None:
        return "--:--"
    m = int(round(m))
    return f"{m // 60:02d}:{m % 60:02d}"


def temps_minimal_flux(unite: UniteTransport, veh: Vehicule, duree_trajet: float,
                       presence_quai: bool) -> float:
    """
    Temps minimal incompressible pour réaliser un flux direct :
    mise à quai départ + chargement + trajet + mise à quai arrivée + déchargement.
    """
    manut = veh.manut_min_par_cont(presence_quai)
    chargement = manut * unite.nb_contenants
    dechargement = manut * unite.nb_contenants
    mise_a_quai = 2 * veh.temps_mise_a_quai_min
    return mise_a_quai + chargement + dechargement + duree_trajet


def fenetre_disponible(unite: UniteTransport, params: cfg.SimulationParams) -> tuple[int, int]:
    """Retourne (debut, fin) de la fenêtre en minutes, bornée par les plages RH."""
    debut = unite.heure_min_collecte if unite.heure_min_collecte is not None else params.heure_debut_mini_min
    fin = unite.heure_max_livraison if unite.heure_max_livraison is not None else params.heure_fin_max_min
    debut = max(debut, params.heure_debut_mini_min)
    fin = min(fin, params.heure_fin_max_min)
    return debut, fin


def flux_faisable(unite: UniteTransport, veh: Vehicule, duree_trajet: float,
                  presence_quai: bool, params: cfg.SimulationParams) -> tuple[bool, float, float]:
    """
    Le flux est-il réalisable dans sa fenêtre ? Retourne (ok, temps_min, largeur_fenetre).
    """
    debut, fin = fenetre_disponible(unite, params)
    largeur = fin - debut + getattr(params, "tolerance_fenetre_min", 0)
    tmin = temps_minimal_flux(unite, veh, duree_trajet, presence_quai)
    return (tmin <= largeur), tmin, largeur
