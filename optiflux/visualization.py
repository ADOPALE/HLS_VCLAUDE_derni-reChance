"""
visualization.py
================
Visualisations Plotly pour l'interface Streamlit :
- histogrammes empilés des flux (par fonction, par jour, par site) ;
- diagramme de Gantt des postes/véhicules (séquence des opérations) ;
- synthèse de flotte.

Toutes les fonctions retournent une figure Plotly (go.Figure).
"""

from __future__ import annotations

from collections import defaultdict

import plotly.express as px
import plotly.graph_objects as go

from .time_windows import min_to_hhmm
from . import config as cfg

# couleurs par grand type d'opération
_COULEURS_OP = {
    "trajet": "#4C78A8", "chargement": "#54A24B", "dechargement": "#88C999",
    "mise_a_quai": "#B279A2", "attente": "#E0E0E0", "inoccupe": "#F2F2F2",
    "pause": "#FFB000", "desinfection": "#E45756", "prise_poste": "#9D755D",
    "fin_poste": "#9D755D",
}


def histogramme_flux_par_contenant(ds, jours: list[str]) -> go.Figure:
    """Contenants par TYPE de contenant et par jour (barres empilées).

    C'est le distingo par type de contenant demandé pour la visualisation de la
    charge : on voit la part de chaque nature de contenant (armoires, rolls,
    caisses, etc.) dans le volume journalier."""
    data = defaultdict(lambda: defaultdict(int))
    for f in ds.flux:
        for j in jours:
            q = f.quantite(j)
            if q:
                data[j][f.contenant or "(non précisé)"] += q
    # tri des contenants par volume total décroissant (légende lisible)
    totaux = defaultdict(int)
    for j in data:
        for c, v in data[j].items():
            totaux[c] += v
    contenants = [c for c, _ in sorted(totaux.items(), key=lambda x: -x[1])]
    fig = go.Figure()
    for c in contenants:
        fig.add_bar(name=c, x=jours, y=[data[j].get(c, 0) for j in jours])
    fig.update_layout(barmode="stack",
                      title="Charge par type de contenant et par jour",
                      xaxis_title="Jour", yaxis_title="Nombre de contenants",
                      legend_title="Type de contenant", height=460)
    return fig


def histogramme_flux_par_fonction(ds, jours: list[str]) -> go.Figure:
    """Contenants par fonction support et par jour (barres empilées)."""
    data = defaultdict(lambda: defaultdict(int))
    for f in ds.flux:
        for j in jours:
            q = f.quantite(j)
            if q:
                data[j][f.fonction_support or "(non précisé)"] += q
    fonctions = sorted({fn for j in data for fn in data[j]})
    fig = go.Figure()
    for fn in fonctions:
        fig.add_bar(name=fn, x=jours, y=[data[j].get(fn, 0) for j in jours])
    fig.update_layout(barmode="stack", title="Contenants par fonction support et par jour",
                      xaxis_title="Jour", yaxis_title="Nombre de contenants",
                      legend_title="Fonction support", height=420)
    return fig


def histogramme_flux_par_site(ds, jour: str, top: int = 20) -> go.Figure:
    """Contenants par site de départ pour un jour (top N)."""
    data = defaultdict(int)
    for f in ds.flux:
        q = f.quantite(jour)
        if q:
            data[f.site_depart] += q
    items = sorted(data.items(), key=lambda x: -x[1])[:top]
    fig = go.Figure(go.Bar(x=[v for _, v in items], y=[k for k, _ in items],
                           orientation="h", marker_color="#4C78A8"))
    fig.update_layout(title=f"Contenants au départ par site — {jour} (top {top})",
                      xaxis_title="Contenants", yaxis_title="Site",
                      height=max(400, 22 * len(items)),
                      yaxis=dict(autorange="reversed"))
    return fig


def gantt_postes(resultat: dict, jour: str, max_postes: int | None = None) -> go.Figure:
    """Diagramme de Gantt des opérations par poste/véhicule pour un jour."""
    postes = resultat.get("postes", [])
    postes = sorted(postes, key=lambda p: (p.vehicule_type, p.debut))
    if max_postes:
        postes = postes[:max_postes]
    fig = go.Figure()
    yticks = []
    for idx, p in enumerate(postes):
        label = f"{p.id} · {p.vehicule_instance}"
        yticks.append(label)
        for op in p.operations:
            if op.fin <= op.debut:
                continue
            fig.add_trace(go.Bar(
                base=op.debut, x=[op.fin - op.debut], y=[label], orientation="h",
                marker_color=_COULEURS_OP.get(op.type, "#888"),
                name=op.type, showlegend=False,
                hovertemplate=(f"<b>{op.type}</b><br>{op.site_dep or ''} → {op.site_arr or ''}"
                               f"<br>{min_to_hhmm(op.debut)}–{min_to_hhmm(op.fin)}<extra></extra>"),
            ))
    # légende manuelle
    for t, c in _COULEURS_OP.items():
        fig.add_trace(go.Bar(x=[None], y=[None], marker_color=c, name=t, showlegend=True))
    ticks = list(range(6 * 60, 21 * 60 + 1, 60))
    fig.update_layout(
        barmode="stack", title=f"Gantt des postes — {jour}",
        height=max(400, 26 * len(yticks) + 120),
        xaxis=dict(title="Heure", tickmode="array", tickvals=ticks,
                   ticktext=[min_to_hhmm(t) for t in ticks],
                   range=[6 * 60 - 10, 21 * 60 + 10]),
        legend_title="Type d'opération", bargap=0.15,
    )
    return fig


def synthese_flotte_barres(resultats: dict[str, dict]) -> go.Figure:
    """Nombre de véhicules par type et par jour."""
    from .fleet_generator import synthese_flotte
    data = defaultdict(lambda: defaultdict(int))
    jours = list(resultats.keys())
    for j, r in resultats.items():
        sf = synthese_flotte(r["postes"])
        for vtype, d in sf.items():
            data[vtype][j] = d["vehicules"]
    fig = go.Figure()
    for vtype in sorted(data):
        fig.add_bar(name=vtype, x=jours, y=[data[vtype].get(j, 0) for j in jours])
    fig.update_layout(barmode="stack", title="Nombre de véhicules par type et par jour",
                      xaxis_title="Jour", yaxis_title="Véhicules", height=420,
                      legend_title="Type de véhicule")
    return fig
