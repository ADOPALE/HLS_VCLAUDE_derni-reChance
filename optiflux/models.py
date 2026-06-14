"""
models.py
=========
Modèles de données métier (Pydantic v2) : Site, Vehicule, Contenant, Flux,
ainsi que les objets construits par le moteur (UniteTransport, Etape, Tournee,
PosteChauffeur).

Ces modèles structurent et valident les données après nettoyage.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Données de référence
# --------------------------------------------------------------------------
class Site(BaseModel):
    id: str
    nom: str
    adresse: str = ""
    presence_quai: bool = False
    # compatibilité par type de véhicule : {nom_type: True/False}
    compat_vehicules: dict[str, bool] = Field(default_factory=dict)
    capacite_quai: int = 3


class Contenant(BaseModel):
    libelle: str
    longueur_m: float = 0.0
    largeur_m: float = 0.0
    poids_vide_t: float = 0.0
    poids_plein_t: float = 0.0

    @property
    def surface_m2(self) -> float:
        return self.longueur_m * self.largeur_m


class Vehicule(BaseModel):
    type_nom: str
    stationnement_initial: str
    longueur_m: float
    largeur_m: float
    hauteur_m: float
    poids_max_t: float
    consommation_l_km: float = 0.0
    cout_carburant_eur_km: float = 0.0
    cout_carbone_kg_km: float = 0.0
    hayon: bool = False
    temps_mise_a_quai_min: float = 3.0
    manut_sans_quai_min_par_cont: float = 0.0
    manut_avec_quai_min_par_cont: float = 0.0
    # compatibilité contenants : {libelle_contenant: True/False}
    compat_contenants: dict[str, bool] = Field(default_factory=dict)

    @property
    def surface_m2(self) -> float:
        return self.longueur_m * self.largeur_m

    def manut_min_par_cont(self, avec_quai: bool) -> float:
        v = self.manut_avec_quai_min_par_cont if avec_quai else self.manut_sans_quai_min_par_cont
        # si la valeur "avec quai" manque, on retombe sur "sans quai" et inversement
        if not v:
            v = self.manut_sans_quai_min_par_cont or self.manut_avec_quai_min_par_cont
        return v or 0.5


# --------------------------------------------------------------------------
# Flux
# --------------------------------------------------------------------------
class Flux(BaseModel):
    id: str
    fonction_support: str
    nature: str = ""
    site_depart: str
    site_arrivee: str
    contenant: str
    plein: bool = True            # True = plein, False = vide
    sale: bool = False            # True = sale, False = propre
    transport_mixte: bool = True
    regles_exclusion: str = ""    # texte brut des exclusions
    tournee_mutualisee: bool = False
    nom_tournee_mutualisee: str = ""
    quantites: dict[str, int] = Field(default_factory=dict)  # {jour: nb contenants}
    heure_min_collecte: Optional[int] = None  # minutes depuis minuit
    heure_max_livraison: Optional[int] = None
    urgent: bool = False
    commentaire: str = ""

    def quantite(self, jour: str) -> int:
        return int(self.quantites.get(jour, 0) or 0)

    def actif(self, jour: str) -> bool:
        return self.quantite(jour) > 0


# --------------------------------------------------------------------------
# Objets construits par le moteur
# --------------------------------------------------------------------------
class UniteTransport(BaseModel):
    """
    Un « morceau » de flux à transporter en un seul chargement (issu de
    l'éclatement des gros volumes). Contient n contenants identiques d'un flux.
    """
    uid: str
    flux_id: str
    fonction_support: str
    site_depart: str
    site_arrivee: str
    contenant: str
    nb_contenants: int
    sale: bool
    plein: bool
    transport_mixte: bool
    regles_exclusion: str
    heure_min_collecte: Optional[int]
    heure_max_livraison: Optional[int]
    urgent: bool = False
    volume_m2: float = 0.0   # surface au sol occupée
    poids_t: float = 0.0
    est_reliquat: bool = False  # True = reste d'éclatement (groupable look-forward)


class Etape(BaseModel):
    """Une opération élémentaire dans une tournée."""
    ordre: int
    type_operation: str        # depart_depot, trajet, mise_a_quai, chargement, ...
    site_depart: Optional[str] = None
    site_arrivee: Optional[str] = None
    heure_debut: int = 0       # minutes
    heure_fin: int = 0
    flux_ids: list[str] = Field(default_factory=list)
    contenants_charges: int = 0
    contenants_decharges: int = 0
    volume_apres: float = 0.0
    poids_apres: float = 0.0
    taux_remplissage: float = 0.0
    a_plein: bool = False
    distance_km: float = 0.0
    duree_min: float = 0.0
    etat_sanitaire: str = "propre"   # propre / sale
    desinfection: bool = False
    commentaire: str = ""


class Tournee(BaseModel):
    id: str
    vehicule_type: str
    vehicule_instance: str     # ex. "PL 12T #1"
    jour: str
    etapes: list[Etape] = Field(default_factory=list)
    heure_debut: int = 0
    heure_fin: int = 0


class PosteChauffeur(BaseModel):
    id: str
    jour: str
    vehicule_instance: str
    heure_debut: int
    heure_fin: int
    heure_pause_debut: Optional[int] = None
    tournee_ids: list[str] = Field(default_factory=list)
