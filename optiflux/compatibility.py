"""
compatibility.py
================
Règles de compatibilité :
- véhicule <-> site (depuis param Sites)
- véhicule <-> contenant (depuis param Véhicules)
- mixité : propre/sale et règles d'exclusion
- état sanitaire du véhicule au fil de la tournée
"""

from __future__ import annotations

from .models import Vehicule, Site, UniteTransport


def vehicule_compatible_site(veh: Vehicule, site: Site) -> bool:
    """Le site autorise-t-il ce type de véhicule ?"""
    val = site.compat_vehicules.get(veh.type_nom)
    if val is None:
        return True  # absence d'info = autorisé
    return bool(val)


def vehicule_compatible_contenant(veh: Vehicule, contenant: str) -> bool:
    val = veh.compat_contenants.get(contenant)
    if val is None:
        return True
    return bool(val)


def _normaliser_exclusion(regle: str) -> set[str]:
    """Transforme le texte d'exclusion en tokens normalisés (sale, propre, ...)."""
    if not regle:
        return set()
    toks = set()
    s = regle.lower()
    if "sale" in s:
        toks.add("sale")
    if "propre" in s:
        toks.add("propre")
    return toks


def unites_combinables(a: UniteTransport, b: UniteTransport) -> tuple[bool, str]:
    """
    Deux unités peuvent-elles cohabiter dans le même véhicule (hors capacité) ?
    Retourne (ok, raison_si_non).
    """
    # transport mixte interdit sur l'une des deux
    if not a.transport_mixte or not b.transport_mixte:
        if a.flux_id != b.flux_id:
            return False, "transport mixte non autorisé"

    # règles d'exclusion : ce qui est listé est interdit dans le même camion
    excl_a = _normaliser_exclusion(a.regles_exclusion)
    excl_b = _normaliser_exclusion(b.regles_exclusion)
    etat_a = "sale" if a.sale else "propre"
    etat_b = "sale" if b.sale else "propre"
    if etat_b in excl_a:
        return False, f"exclusion {a.flux_id}: {etat_b} interdit"
    if etat_a in excl_b:
        return False, f"exclusion {b.flux_id}: {etat_a} interdit"

    return True, ""


def charge_compatible_etat(unites: list[UniteTransport]) -> tuple[bool, str]:
    """Vérifie qu'un ensemble d'unités est mutuellement compatible."""
    for i in range(len(unites)):
        for j in range(i + 1, len(unites)):
            ok, raison = unites_combinables(unites[i], unites[j])
            if not ok:
                return False, raison
    return True, ""


def besoin_desinfection(etat_vehicule: str, prochaine_unite_sale: bool) -> bool:
    """
    Un véhicule ayant transporté du sale doit être désinfecté avant de
    transporter du propre.
    """
    return etat_vehicule == "sale" and (not prochaine_unite_sale)
