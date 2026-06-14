"""
outputs.py
==========
Génère le classeur Excel de résultats (9 onglets) à partir des résultats
d'optimisation de tous les jours simulés.

Onglets :
1. Synthèse flotte        — véhicules et postes par type et par jour
2. Synthèse chauffeurs     — un récap par poste/chauffeur
3. Tournées véhicules      — détail des opérations (séquence complète)
4. Planning chauffeurs     — horaires de poste, pause, prise/fin
5. Planning quais          — occupation des quais par site
6. Flux transportés        — chaque flux servi, son véhicule et son poste
7. Flux non servis         — flux non servis + flux incompatibles
8. Contrôles contraintes   — vérifications (capacité quai, fenêtres, désinfection)
9. Indicateurs             — KPI globaux (km, taux remplissage, temps morts)
"""

from __future__ import annotations

import io
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .fleet_generator import synthese_flotte
from .driver_scheduler import ligne_chauffeur
from .dock_scheduler import construire_planning_quais
from .time_windows import min_to_hhmm
from . import config as cfg

_POLICE = "Arial"
_ENTETE_FILL = PatternFill("solid", fgColor="1F4E78")
_ENTETE_FONT = Font(name=_POLICE, bold=True, color="FFFFFF", size=10)
_CELL_FONT = Font(name=_POLICE, size=10)
_TITRE_FONT = Font(name=_POLICE, bold=True, size=12, color="1F4E78")
_BORD = Border(*(Side(style="thin", color="D9D9D9"),) * 4)


def _ecrire_table(ws, lignes: list[dict], colonnes: list[str], depart_row=1):
    ws.append([]) if depart_row > ws.max_row else None
    r = depart_row
    for c, nom in enumerate(colonnes, start=1):
        cell = ws.cell(row=r, column=c, value=nom)
        cell.font = _ENTETE_FONT
        cell.fill = _ENTETE_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORD
    for i, ligne in enumerate(lignes, start=r + 1):
        for c, nom in enumerate(colonnes, start=1):
            cell = ws.cell(row=i, column=c, value=ligne.get(nom, ""))
            cell.font = _CELL_FONT
            cell.border = _BORD
    ws.freeze_panes = ws.cell(row=r + 1, column=1)
    # largeurs
    for c, nom in enumerate(colonnes, start=1):
        maxlen = max([len(str(nom))] + [len(str(l.get(nom, ""))) for l in lignes]) if lignes else len(str(nom))
        ws.column_dimensions[get_column_letter(c)].width = min(40, max(10, maxlen + 2))
    return r + 1 + len(lignes)


def _flux_index(ds):
    return {f.id: f for f in ds.flux}


def generer_classeur(resultats: dict[str, dict], ds, params: cfg.SimulationParams) -> bytes:
    """Construit le classeur et le retourne sous forme d'octets (xlsx)."""
    wb = Workbook()
    wb.remove(wb.active)
    fidx = _flux_index(ds)

    # ---------------------------------------------------------------- 1. Flotte
    ws = wb.create_sheet("1. Synthèse flotte")
    lignes_flotte = []
    for jour, r in resultats.items():
        sf = synthese_flotte(r["postes"])
        for vtype, d in sorted(sf.items()):
            lignes_flotte.append({"Jour": jour, "Type véhicule": vtype,
                                  "Nb véhicules": d["vehicules"], "Nb postes": d["postes"],
                                  "Chauffeurs/véhicule (moy.)": round(d["postes"] / max(1, d["vehicules"]), 2)})
    _ecrire_table(ws, lignes_flotte,
                  ["Jour", "Type véhicule", "Nb véhicules", "Nb postes", "Chauffeurs/véhicule (moy.)"])

    # ----------------------------------------------------------- 2. Chauffeurs
    ws = wb.create_sheet("2. Synthèse chauffeurs")
    lignes_ch = []
    for jour, r in resultats.items():
        for p in r["postes"]:
            d = ligne_chauffeur(p, params)
            d = {"Jour": jour, **d}
            lignes_ch.append(d)
    cols_ch = ["Jour", "Poste", "Véhicule", "Début", "Fin", "Durée poste (min)",
               "Conduite (min)", "Manutention (min)", "Mise à quai (min)",
               "Désinfection (min)", "Attente/inoccupé (min)", "Pause (min)",
               "Nb chargements", "Taux occupation utile %",
               "Remplissage surface %", "Remplissage poids %"]
    _ecrire_table(ws, lignes_ch, cols_ch)

    # ------------------------------------------------------- 3. Tournées détail
    ws = wb.create_sheet("3. Tournées véhicules")
    lignes_t = []
    for jour, r in resultats.items():
        for p in r["postes"]:
            for op in p.operations:
                lignes_t.append({
                    "Jour": jour, "Poste": p.id, "Véhicule": p.vehicule_instance,
                    "Opération": op.type,
                    "De": op.site_dep or "", "Vers": op.site_arr or "",
                    "Début": min_to_hhmm(op.debut), "Fin": min_to_hhmm(op.fin),
                    "Durée (min)": round(op.fin - op.debut, 1),
                    "Contenants +": op.contenants_charges,
                    "Contenants -": op.contenants_decharges,
                    "À bord après": op.nb_apres,
                    "Distance (km)": round(op.distance, 2),
                    "À plein": "oui" if op.a_plein else "",
                    "Flux": ",".join(sorted(set(op.flux_ids))),
                })
    cols_t = ["Jour", "Poste", "Véhicule", "Opération", "De", "Vers", "Début", "Fin",
              "Durée (min)", "Contenants +", "Contenants -", "À bord après",
              "Distance (km)", "À plein", "Flux"]
    _ecrire_table(ws, lignes_t, cols_t)

    # -------------------------------------------------- 4. Planning chauffeurs
    ws = wb.create_sheet("4. Planning chauffeurs")
    lignes_pc = []
    for jour, r in resultats.items():
        for p in r["postes"]:
            lignes_pc.append({
                "Jour": jour, "Poste": p.id, "Véhicule": p.vehicule_instance,
                "Prise de poste": min_to_hhmm(p.debut),
                "Pause": min_to_hhmm(p.pause_debut) if p.pause_debut else "—",
                "Fin de poste": min_to_hhmm(p.fin),
                "Nb chargements": p.nb_chargements,
                "Km total": round(p.km_total, 1),
                "Km à vide": round(p.km_vide, 1),
            })
    _ecrire_table(ws, lignes_pc,
                  ["Jour", "Poste", "Véhicule", "Prise de poste", "Pause", "Fin de poste",
                   "Nb chargements", "Km total", "Km à vide"])

    # ------------------------------------------------------- 5. Planning quais
    ws = wb.create_sheet("5. Planning quais")
    lignes_q = []
    for jour, r in resultats.items():
        lq, _ = construire_planning_quais(r["postes"], ds.sites, params)
        for row in lq:
            row = {"Jour": jour, **{k: v for k, v in row.items() if not k.startswith("_")}}
            lignes_q.append(row)
    _ecrire_table(ws, lignes_q,
                  ["Jour", "Site", "Arrivée", "Début mise à quai", "Fin opération",
                   "Départ", "Véhicule", "Chauffeur", "Opération", "Flux"])

    # ------------------------------------------------------ 6. Flux transportés
    ws = wb.create_sheet("6. Flux transportés")
    lignes_ft = []
    for jour, r in resultats.items():
        # map flux -> (poste, véhicule) via opérations de chargement
        servi = defaultdict(set)
        for p in r["postes"]:
            for op in p.operations:
                if op.type == "chargement":
                    for fid in op.flux_ids:
                        servi[fid].add(p.vehicule_instance)
        for fid, vehs in servi.items():
            f = fidx.get(fid)
            if not f:
                continue
            lignes_ft.append({
                "Jour": jour, "Flux": fid, "Fonction": f.fonction_support,
                "Départ": f.site_depart, "Destination": f.site_arrivee,
                "Contenant": f.contenant, "Quantité": f.quantite(jour),
                "Sale/propre": "sale" if f.sale else "propre",
                "Véhicule(s)": ", ".join(sorted(vehs)),
            })
    _ecrire_table(ws, lignes_ft,
                  ["Jour", "Flux", "Fonction", "Départ", "Destination", "Contenant",
                   "Quantité", "Sale/propre", "Véhicule(s)"])

    # -------------------------------------------------------- 7. Flux non servis
    ws = wb.create_sheet("7. Flux non servis")
    lignes_ns = []
    for jour, r in resultats.items():
        for n in r.get("non_servis", []):
            lignes_ns.append({"Jour": jour, "Flux": ", ".join(n.get("flux_ids", [])),
                              "Type": "Non servi", "Raison": n.get("raison", "")})
        for inc in r.get("incompatibles", []):
            lignes_ns.append({"Jour": jour, "Flux": inc.get("flux_id", ""),
                              "Type": "Incompatible (préalable)",
                              "Raison": inc.get("raison", "") + " | " + inc.get("suggestion", "")})
    if not lignes_ns:
        lignes_ns.append({"Jour": "—", "Flux": "—", "Type": "—", "Raison": "Aucun : 100 % des flux servis."})
    _ecrire_table(ws, lignes_ns, ["Jour", "Flux", "Type", "Raison"])

    # ---------------------------------------------------- 8. Contrôles contraintes
    ws = wb.create_sheet("8. Contrôles contraintes")
    lignes_ctrl = []
    for jour, r in resultats.items():
        _, depass = construire_planning_quais(r["postes"], ds.sites, params)
        for d in depass:
            lignes_ctrl.append({"Jour": jour, "Contrôle": "Capacité quai",
                                "Statut": "DÉPASSEMENT",
                                "Détail": f"{d['Site']} à {d['Heure']} : {d['Simultanés']} > {d['Capacité']}"})
        # postes dépassant la vacation (ne devrait pas arriver)
        for p in r["postes"]:
            if p.fin - p.debut > params.duree_vacation_min + 1:
                lignes_ctrl.append({"Jour": jour, "Contrôle": "Durée vacation",
                                    "Statut": "DÉPASSEMENT",
                                    "Détail": f"{p.id} : {p.fin - p.debut} min > {params.duree_vacation_min}"})
    for jour, r in resultats.items():
        lignes_ctrl.append({"Jour": jour, "Contrôle": "Flux servis",
                            "Statut": "OK" if len(r.get("non_servis", [])) == 0 else "ATTENTION",
                            "Détail": f"{len(r.get('non_servis', []))} non servis, "
                                      f"{len(r.get('incompatibles', []))} incompatibles préalables"})
        # seuil d'occupation (blocage dur)
        s = r.get("seuil_occupation")
        if s is not None:
            if s["acceptable"]:
                lignes_ctrl.append({"Jour": jour, "Contrôle": f"Seuil occupation ≥ {s['seuil']:.0f}%",
                                    "Statut": "OK",
                                    "Détail": f"Tous les postes contrôlés ({', '.join(s['types_controles'])}) "
                                              f"respectent le seuil."})
            else:
                lignes_ctrl.append({"Jour": jour, "Contrôle": f"Seuil occupation ≥ {s['seuil']:.0f}%",
                                    "Statut": "SOLUTION REFUSÉE",
                                    "Détail": f"{len(s['violations'])} poste(s) sous le seuil : " +
                                              "; ".join(f"{v['poste']} ({v['type']}) {v['occupation_pct']:.0f}%"
                                                        for v in s["violations"][:12])})
    _ecrire_table(ws, lignes_ctrl, ["Jour", "Contrôle", "Statut", "Détail"])

    # ------------------------------------------------------------ 9. Indicateurs
    ws = wb.create_sheet("9. Indicateurs")
    lignes_kpi = []
    for jour, r in resultats.items():
        postes = r["postes"]
        if not postes:
            continue
        km = sum(p.km_total for p in postes)
        km_vide = sum(p.km_vide for p in postes)
        conduite = sum(p.t_conduite for p in postes)
        manut = sum(p.t_manutention for p in postes)
        attente = sum(p.t_attente for p in postes)
        desinf = sum(p.t_desinfection for p in postes)
        sf = synthese_flotte(postes)
        rs = sum(p.rempl_surf_pct for p in postes) / len(postes)
        rp = sum(p.rempl_poids_pct for p in postes) / len(postes)
        s = r.get("seuil_occupation", {})
        lignes_kpi.append({
            "Jour": jour,
            "Véhicules": sum(d["vehicules"] for d in sf.values()),
            "Postes (chauffeurs)": len(postes),
            "Km total": round(km, 1),
            "Km à vide": round(km_vide, 1),
            "% km à vide": round(100 * km_vide / max(1, km), 1),
            "Conduite (h)": round(conduite / 60, 1),
            "Manutention (h)": round(manut / 60, 1),
            "Désinfection (h)": round(desinf / 60, 1),
            "Attente/inoccupé (h)": round(attente / 60, 1),
            "Remplissage surface moyen %": round(rs, 1),
            "Remplissage poids moyen %": round(rp, 1),
            "Solution acceptable (seuil)": "OUI" if s.get("acceptable", True) else "NON",
        })
    _ecrire_table(ws, lignes_kpi,
                  ["Jour", "Véhicules", "Postes (chauffeurs)", "Km total", "Km à vide",
                   "% km à vide", "Conduite (h)", "Manutention (h)", "Désinfection (h)",
                   "Attente/inoccupé (h)", "Remplissage surface moyen %",
                   "Remplissage poids moyen %", "Solution acceptable (seuil)"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
