"""
config.py
=========
Paramètres généraux et valeurs par défaut de l'application OptiFLUX.

Toutes les valeurs « métier » réglables par l'utilisateur ont une valeur par
défaut ici. L'interface Streamlit peut les surcharger au lancement.

Les durées sont exprimées EN MINUTES partout dans le moteur (les heures du
fichier Excel sont converties en minutes depuis minuit).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Noms d'onglets attendus dans le classeur Excel
# --------------------------------------------------------------------------
SHEET_RH = "param RH"
SHEET_SITES = "param Sites"
SHEET_VEHICULES = "param Véhicules"
SHEET_CONTENANTS = "param Contenants"
SHEET_DUREE = "matrice Durée"
SHEET_DIST = "matrice Dist"
SHEET_LISTES = "LISTES"
SHEET_FLUX = "M flux"

REQUIRED_SHEETS = [
    SHEET_RH, SHEET_SITES, SHEET_VEHICULES, SHEET_CONTENANTS,
    SHEET_DUREE, SHEET_DIST, SHEET_FLUX,
]

# Jours de la semaine (ordre canonique) + colonnes quantités dans M flux
DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


@dataclass
class SimulationParams:
    """Paramètres d'une simulation. Tous réglables depuis Streamlit."""

    # --- RH / postes chauffeurs (minutes) ---
    duree_vacation_min: int = 450          # 7h30 (lu depuis param RH si dispo)
    duree_pause_min: int = 30              # pause obligatoire
    prise_poste_min: int = 15              # temps incompressible début de poste
    fin_poste_min: int = 10                # temps incompressible fin de poste
    heure_debut_mini_min: int = 360        # 06h00
    heure_fin_max_min: int = 1260          # 21h00
    fenetre_pause_min: int = 120           # fenêtre de 2h centrée sur le milieu du poste

    # --- Quais ---
    capacite_quai_defaut: int = 3          # véhicules simultanés par site
    capacite_quai_par_site: dict = field(default_factory=dict)

    # --- Circulation ---
    facteur_circulation_pct: float = 0.0   # 0 % par défaut, appliqué aux DURÉES

    # --- Capacité / bin-packing ---
    taux_occupation_surface: float = 0.80  # part utile de la surface au sol (réglable)
    autoriser_rotation_90: bool = True     # rotation des contenants dans le packing 2D

    # --- Désinfection ---
    duree_desinfection_min: int = 15

    # --- Sélection ---
    jours_a_simuler: list = field(default_factory=lambda: list(DAYS))
    vehicules_autorises: list = field(default_factory=list)   # noms de types
    fonctions_support: list = field(default_factory=list)     # vide = toutes
    max_par_type: dict = field(default_factory=dict)          # plafond par type

    # --- Solveur ---
    solver_time_limit_s: int = 30          # garde-fou de temps
    look_forward_horizon_min: int = 60     # horizon d'anticipation (regroupement)

    def duree_avec_circulation(self, minutes: float) -> float:
        """Applique le facteur circulation à une durée (0 reste 0)."""
        if minutes <= 0:
            return 0.0
        return minutes * (1.0 + self.facteur_circulation_pct / 100.0)


# Véhicule explicitement exclu par défaut dans l'analyse de référence
VEHICULE_EXCLU_DEFAUT = "SEMI-REMORQUE"
