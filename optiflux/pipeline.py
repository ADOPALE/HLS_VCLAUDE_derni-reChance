"""
pipeline.py
===========
Chaîne complète : import -> nettoyage -> validation -> préparation par jour ->
optimisation. Expose deux objets principaux :
- Dataset : données importées et nettoyées,
- resoudre_jour() / resoudre() : exécution de l'optimisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import data_loader, data_cleaning, validators, preprocessing, fleet_generator
from . import config as cfg
from .models import Site, Vehicule, Contenant, Flux


@dataclass
class Dataset:
    sites: dict[str, Site] = field(default_factory=dict)
    vehicules: dict[str, Vehicule] = field(default_factory=dict)
    contenants: dict[str, Contenant] = field(default_factory=dict)
    flux: list[Flux] = field(default_factory=list)
    duree: dict = field(default_factory=dict)
    dist: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)


def charger_dataset(chemin_ou_buffer, params: cfg.SimulationParams) -> tuple[Dataset, list[str]]:
    """Importe et nettoie. Retourne (Dataset, onglets_manquants)."""
    feuilles = data_loader.lire_classeur(chemin_ou_buffer)
    manquants = data_loader.controler_onglets(feuilles)

    params = data_cleaning.charger_rh(feuilles, params)
    sites = data_cleaning.charger_sites(feuilles, params)
    vehicules = data_cleaning.charger_vehicules(feuilles, data_cleaning.charger_contenants(feuilles))
    contenants = data_cleaning.charger_contenants(feuilles)
    flux = data_cleaning.charger_flux(feuilles)
    duree = data_cleaning.charger_matrice(feuilles.get(cfg.SHEET_DUREE))
    dist = data_cleaning.charger_matrice(feuilles.get(cfg.SHEET_DIST))

    msgs = validators.valider(sites, vehicules, contenants, flux, duree, dist, params)
    ds = Dataset(sites=sites, vehicules=vehicules, contenants=contenants,
                 flux=flux, duree=duree, dist=dist, messages=msgs)
    return ds, manquants


def resoudre_jour(ds: Dataset, jour: str, params: cfg.SimulationParams) -> dict:
    """Optimise un jour. Retourne un dict de résultats."""
    unites, incompatibles = preprocessing.preparer_jour(
        jour, ds.flux, ds.vehicules, ds.sites, ds.contenants, ds.duree, params)

    if incompatibles:
        return {"jour": jour, "incompatibles": incompatibles, "postes": [],
                "non_servis": [], "chargements": [], "unites": unites, "ok": False}

    veh_sel = preprocessing.vehicules_selectionnes(ds.vehicules, params)
    postes, non_servis, chargements = fleet_generator.optimiser(
        jour, unites, veh_sel, ds.sites, ds.contenants, ds.duree, ds.dist, params)

    return {"jour": jour, "incompatibles": [], "postes": postes,
            "non_servis": non_servis, "chargements": chargements,
            "unites": unites, "ok": len(non_servis) == 0}


def resoudre(ds: Dataset, params: cfg.SimulationParams) -> dict[str, dict]:
    """Optimise tous les jours sélectionnés."""
    resultats = {}
    for jour in params.jours_a_simuler:
        resultats[jour] = resoudre_jour(ds, jour, params)
    return resultats
