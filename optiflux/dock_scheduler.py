"""
dock_scheduler.py
=================
Construit le planning des quais à partir des opérations de tous les postes et
contrôle la capacité simultanée par site (3 par défaut).

Une « occupation de quai » = arrivée (mise à quai) -> fin chargement/déchargement
-> départ. On compte les chevauchements par site et on signale les dépassements.
"""

from __future__ import annotations

from collections import defaultdict

from .time_windows import min_to_hhmm
from .models import Site
from . import config as cfg


def construire_planning_quais(postes, sites: dict[str, Site], params: cfg.SimulationParams):
    """Retourne (lignes_planning, depassements)."""
    lignes = []
    occupations = defaultdict(list)  # site -> [(debut, fin)]

    for p in postes:
        ops = p.operations
        # on regroupe une mise_a_quai avec le (dé)chargement qui suit sur le même site
        i = 0
        while i < len(ops):
            op = ops[i]
            if op.type == "mise_a_quai":
                site = op.site_arr
                debut_quai = op.debut
                fin_op = op.fin
                flux_ids = []
                operation = "mise à quai"
                # chercher chargement/déchargement suivant sur le même site
                if i + 1 < len(ops) and ops[i + 1].type in ("chargement", "dechargement"):
                    nxt = ops[i + 1]
                    fin_op = nxt.fin
                    flux_ids = nxt.flux_ids
                    operation = nxt.type
                    i += 1
                lignes.append({
                    "Site": site,
                    "Arrivée": min_to_hhmm(debut_quai),
                    "Début mise à quai": min_to_hhmm(debut_quai),
                    "Fin opération": min_to_hhmm(fin_op),
                    "Départ": min_to_hhmm(fin_op),
                    "Véhicule": p.vehicule_instance,
                    "Chauffeur": p.id,
                    "Opération": operation,
                    "Flux": ",".join(sorted(set(flux_ids))),
                    "_debut": debut_quai, "_fin": fin_op,
                })
                occupations[site].append((debut_quai, fin_op))
            i += 1

    # contrôle capacité simultanée
    depassements = []
    for site, intervalles in occupations.items():
        cap = params.capacite_quai_par_site.get(site, params.capacite_quai_defaut)
        evts = []
        for (a, b) in intervalles:
            evts.append((a, 1))
            evts.append((b, -1))
        evts.sort()
        courant = 0
        for (h, delta) in evts:
            courant += delta
            if courant > cap:
                depassements.append({"Site": site, "Heure": min_to_hhmm(h),
                                     "Simultanés": courant, "Capacité": cap})
                break
    return lignes, depassements
