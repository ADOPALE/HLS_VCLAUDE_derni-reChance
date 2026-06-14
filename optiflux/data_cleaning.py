"""
data_cleaning.py
================
Nettoie et normalise les DataFrames bruts et construit les objets métier
(Site, Vehicule, Contenant, Flux, matrices). Convertit les horaires en minutes
depuis minuit et les valeurs OUI/NON en booléens.
"""

from __future__ import annotations

import datetime as _dt
import math
import pandas as pd

from . import config as cfg
from .models import Site, Vehicule, Contenant, Flux


def _isblank(v) -> bool:
    """True si la valeur est vide / None / NaN / 'nan'."""
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    s = str(v).strip().lower()
    return s in ("", "nan", "none")


# --------------------------------------------------------------------------
# Helpers de conversion
# --------------------------------------------------------------------------
def to_minutes(val) -> int | None:
    """Convertit un horaire (datetime.time, str 'HH:MM', nombre) en minutes."""
    if val is None or val == "":
        return None
    if isinstance(val, _dt.time):
        return val.hour * 60 + val.minute
    if isinstance(val, _dt.datetime):
        return val.hour * 60 + val.minute
    if isinstance(val, (int, float)):
        # fraction de jour Excel (0..1) ou déjà des minutes
        if 0 <= val < 2:
            return int(round(val * 24 * 60))
        return int(val)
    s = str(val).strip()
    for sep in (":", "h", "H"):
        if sep in s:
            parts = s.replace("H", ":").replace("h", ":").split(":")
            try:
                hh = int(parts[0])
                mm = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
                return hh * 60 + mm
            except ValueError:
                return None
    return None


def to_bool(val, defaut: bool = False) -> bool:
    if val is None:
        return defaut
    s = str(val).strip().upper()
    return s in ("OUI", "O", "YES", "Y", "TRUE", "1", "VRAI")


def to_float(val, defaut: float = 0.0) -> float:
    if val is None or val == "" or str(val).strip().upper() in ("NC", "N/A", "NA"):
        return defaut
    try:
        return float(str(val).replace(",", ".").replace("€", "").strip())
    except (ValueError, TypeError):
        return defaut


def _time_to_min_duration(val) -> float:
    """param Véhicules stocke des durées comme des time (0:03 = 3 min, 0:00:25 = 25 s)."""
    if isinstance(val, _dt.time):
        return val.hour * 60 + val.minute + val.second / 60.0
    return to_float(val)


# --------------------------------------------------------------------------
# RH
# --------------------------------------------------------------------------
def charger_rh(feuilles: dict[str, pd.DataFrame], params: cfg.SimulationParams) -> cfg.SimulationParams:
    df = feuilles.get(cfg.SHEET_RH, pd.DataFrame())
    if df.empty:
        return params
    row = df.iloc[0]
    vac = to_minutes(row.get("Format horaire"))
    pause = to_minutes(row.get("Pause"))
    deb = to_minutes(row.get("heure début mini"))
    fin = to_minutes(row.get("heure fin max"))
    if vac:
        params.duree_vacation_min = vac
    if pause:
        params.duree_pause_min = pause
    if deb is not None:
        params.heure_debut_mini_min = deb
    if fin is not None:
        params.heure_fin_max_min = fin
    return params


# --------------------------------------------------------------------------
# Sites
# --------------------------------------------------------------------------
def charger_sites(feuilles: dict[str, pd.DataFrame], params: cfg.SimulationParams) -> dict[str, Site]:
    df = feuilles.get(cfg.SHEET_SITES, pd.DataFrame())
    sites: dict[str, Site] = {}
    if df.empty:
        return sites
    # Les colonnes après les 3 premières sont les types de véhicules
    cols = list(df.columns)
    base_cols = {"Libellé", "Adresses", "Présence de quai"}
    veh_cols = [c for c in cols if c not in base_cols and not c.startswith("_col")]
    for _, row in df.iterrows():
        libelle = row.get("Libellé")
        if _isblank(libelle):
            continue
        sid = str(libelle).strip()
        compat = {c: to_bool(row.get(c)) for c in veh_cols}
        cap = params.capacite_quai_par_site.get(sid, params.capacite_quai_defaut)
        sites[sid] = Site(
            id=sid,
            nom=sid,
            adresse=str(row.get("Adresses") or ""),
            presence_quai=to_bool(row.get("Présence de quai")),
            compat_vehicules=compat,
            capacite_quai=cap,
        )
    return sites


# --------------------------------------------------------------------------
# Contenants
# --------------------------------------------------------------------------
def charger_contenants(feuilles: dict[str, pd.DataFrame]) -> dict[str, Contenant]:
    df = feuilles.get(cfg.SHEET_CONTENANTS, pd.DataFrame())
    out: dict[str, Contenant] = {}
    if df.empty:
        return out
    for _, row in df.iterrows():
        lib = row.get("libellé")
        if _isblank(lib):
            continue
        key = str(lib).strip()
        out[key] = Contenant(
            libelle=key,
            longueur_m=to_float(row.get("dim longueur (m)")),
            largeur_m=to_float(row.get("dim largeur (m)")),
            poids_vide_t=to_float(row.get("Poids vide (T)")),
            poids_plein_t=to_float(row.get("Poids plein (T)")),
        )
    return out


# --------------------------------------------------------------------------
# Véhicules
# --------------------------------------------------------------------------
def charger_vehicules(feuilles: dict[str, pd.DataFrame], contenants: dict[str, Contenant]) -> dict[str, Vehicule]:
    df = feuilles.get(cfg.SHEET_VEHICULES, pd.DataFrame())
    out: dict[str, Vehicule] = {}
    if df.empty:
        return out
    cols = list(df.columns)
    # les colonnes de compatibilité contenants = colonnes dont le nom matche un contenant
    contenant_cols = [c for c in cols if c in contenants]
    for _, row in df.iterrows():
        t = row.get("Types")
        if _isblank(t):
            continue
        tn = str(t).strip()
        compat = {c: to_bool(row.get(c)) for c in contenant_cols}
        out[tn] = Vehicule(
            type_nom=tn,
            stationnement_initial=str(row.get("Stationnement initial") or "").strip(),
            longueur_m=to_float(row.get("dim longueur interne (m)")),
            largeur_m=to_float(row.get("dim largeur interne (m)")),
            hauteur_m=to_float(row.get("dim hauteur interne (m)")),
            poids_max_t=to_float(row.get("Poids max chargement")),
            consommation_l_km=to_float(row.get("Consommation (L/km)")),
            cout_carburant_eur_km=to_float(row.get("Cout carburant (€/km)")),
            cout_carbone_kg_km=to_float(row.get("Cout carbone (kg/km)")),
            hayon=to_bool(row.get("Présence hayon")),
            temps_mise_a_quai_min=_time_to_min_duration(
                row.get("Temps de mise à quai - manœuvre, contact/admin (minutes)")),
            manut_sans_quai_min_par_cont=_time_to_min_duration(
                row.get("Manutention sans quai (minutes / contenants)")),
            manut_avec_quai_min_par_cont=_time_to_min_duration(
                row.get("Manutention avec quai (minutes / contenants)")),
            compat_contenants=compat,
        )
    return out


# --------------------------------------------------------------------------
# Matrices
# --------------------------------------------------------------------------
def charger_matrice(df: pd.DataFrame) -> dict[tuple[str, str], float]:
    """Transforme une matrice carrée (1re colonne = origine) en dict {(o,d): val}."""
    out: dict[tuple[str, str], float] = {}
    if df.empty:
        return out
    cols = list(df.columns)
    dest_cols = cols[1:]  # la 1re colonne contient les origines
    origin_col = cols[0]
    for _, row in df.iterrows():
        o = row.get(origin_col)
        if _isblank(o):
            continue
        o = str(o).strip()
        for d in dest_cols:
            if d.startswith("_col"):
                continue
            out[(o, str(d).strip())] = to_float(row.get(d))
    return out


# --------------------------------------------------------------------------
# Flux
# --------------------------------------------------------------------------
_FLUX_COLS = {
    "depart": "Point de départ",
    "dest": "Point de destination",
    "fonction": "Fonction Support associée",
    "contenant": "Nature de contenant",
    "plein": "Plein / vide",
    "sale": "Sale / propre",
    "mixte": "Transport mixte possible (OUI / NON)",
    "excl": "Règles d'exclusions si transport mixte",
    "mutu": "Tournées mutualisées ? (OUI / NON)",
    "nom_mutu": "Nom de la tournée mutualisée le cas échéant",
    "hmin": "Heure de mise à disposition min départ",
    "hmax": "Heure max de livraison à la destination",
    "urgent": "Urgence / flux prioritaire \n(Oui/Non)",
    "comm": "Commentaire",
}
_DAY_COLS = {
    "Lundi": "Quantité Lundi", "Mardi": "Quantité Mardi", "Mercredi": "Quantité Mercredi",
    "Jeudi": "Quantité Jeudi", "Vendredi": "Quantité Vendredi",
    "Samedi": "Quantité Samedi", "Dimanche": "Quantité Dimanche",
}


def charger_flux(feuilles: dict[str, pd.DataFrame]) -> list[Flux]:
    df = feuilles.get(cfg.SHEET_FLUX, pd.DataFrame())
    out: list[Flux] = []
    if df.empty:
        return out

    def col(row, key):
        return row.get(_FLUX_COLS[key])

    idx = 0
    for _, row in df.iterrows():
        dep = col(row, "depart")
        if _isblank(dep):
            continue
        idx += 1
        quantites = {}
        for jour, c in _DAY_COLS.items():
            v = row.get(c)
            quantites[jour] = int(v) if isinstance(v, (int, float)) and not pd.isna(v) else 0
        nature_libre = row.get("Nature du Flux \n(champ libre)") or ""
        out.append(Flux(
            id=f"F{idx:04d}",
            fonction_support=str(col(row, "fonction") or "").strip(),
            nature=str(nature_libre).strip(),
            site_depart=str(dep).strip(),
            site_arrivee=str(col(row, "dest") or "").strip(),
            contenant=str(col(row, "contenant") or "").strip(),
            plein=str(col(row, "plein") or "").strip().lower().startswith("plein"),
            sale=str(col(row, "sale") or "").strip().lower().startswith("sale"),
            transport_mixte=to_bool(col(row, "mixte"), defaut=True),
            regles_exclusion=str(col(row, "excl") or "").strip(),
            tournee_mutualisee=to_bool(col(row, "mutu")),
            nom_tournee_mutualisee=str(col(row, "nom_mutu") or "").strip(),
            quantites=quantites,
            heure_min_collecte=to_minutes(col(row, "hmin")),
            heure_max_livraison=to_minutes(col(row, "hmax")),
            urgent=to_bool(col(row, "urgent")),
            commentaire=str(col(row, "comm") or "").strip(),
        ))
    return out
