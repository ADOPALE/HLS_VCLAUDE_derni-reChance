"""
app.py
======
Interface Streamlit d'OptiFLUX.

Lancement :  streamlit run app.py

Parcours :
1. Importer le fichier Excel de paramétrage.
2. Vérifier le rapport d'import (onglets, volumétrie, alertes).
3. Explorer les flux (histogrammes).
4. Régler les paramètres (jours, fonctions, véhicules, taux d'occupation,
   facteur circulation, durées de poste/pause, capacité des quais).
5. Lancer les contrôles préalables puis l'optimisation.
6. Visualiser (Gantt, flotte) et télécharger le classeur de résultats.
"""

from __future__ import annotations

import streamlit as st

from optiflux import config as cfg
from optiflux import pipeline, outputs, visualization as viz
from optiflux import validators

st.set_page_config(page_title="OptiFLUX", page_icon="🚚", layout="wide")


# --------------------------------------------------------------------------
# État de session
# --------------------------------------------------------------------------
def _init_state():
    st.session_state.setdefault("ds", None)
    st.session_state.setdefault("manquants", [])
    st.session_state.setdefault("resultats", None)
    st.session_state.setdefault("params", cfg.SimulationParams())


_init_state()


# --------------------------------------------------------------------------
# En-tête
# --------------------------------------------------------------------------
st.title("🚚 OptiFLUX — Optimisation de la logistique hospitalière")
st.caption("Dimensionnement de flotte et de tournées à partir du fichier de paramétrage des flux.")

# --------------------------------------------------------------------------
# 1. Import
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("1 · Fichier de paramétrage")
    fichier = st.file_uploader("Classeur Excel (.xlsx)", type=["xlsx"])
    if fichier is not None and st.button("📥 Importer / réimporter", use_container_width=True):
        try:
            ds, manquants = pipeline.charger_dataset(fichier, cfg.SimulationParams())
            st.session_state.ds = ds
            st.session_state.manquants = manquants
            st.session_state.resultats = None
            st.success("Import réussi.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Échec de l'import : {exc}")

ds = st.session_state.ds

if ds is None:
    st.info("⬅️ Importez d'abord votre fichier Excel depuis le panneau de gauche "
            "(ou utilisez le fichier d'exemple fourni dans `sample/`).")
    st.stop()

# --------------------------------------------------------------------------
# Rapport d'import
# --------------------------------------------------------------------------
st.header("Rapport d'import")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Sites", len(ds.sites))
c2.metric("Véhicules", len(ds.vehicules))
c3.metric("Contenants", len(ds.contenants))
c4.metric("Flux", len(ds.flux))

if st.session_state.manquants:
    st.error("Onglets obligatoires manquants : " + ", ".join(st.session_state.manquants))

bloquants = [m for m in ds.messages if m["niveau"] == validators.BLOQUANT]
alertes = [m for m in ds.messages if m["niveau"] == validators.ALERTE]
if bloquants:
    with st.expander(f"⛔ {len(bloquants)} message(s) bloquant(s)", expanded=True):
        for m in bloquants:
            st.write(f"- **{m['type']}** — {m['detail']}")
if alertes:
    with st.expander(f"⚠️ {len(alertes)} alerte(s)"):
        for m in alertes:
            st.write(f"- {m['detail']}")
if not bloquants and not alertes:
    st.success("Aucune anomalie détectée dans les données.")

# --------------------------------------------------------------------------
# Onglets : Flux / Paramètres / Résultats
# --------------------------------------------------------------------------
tab_flux, tab_param, tab_res = st.tabs(["📊 Flux", "⚙️ Paramètres", "🚀 Optimisation & résultats"])

# ---------------------------------------------------------------- Flux
with tab_flux:
    st.subheader("Volumétrie des flux")
    jours_dispo = list(cfg.DAYS)
    st.plotly_chart(viz.histogramme_flux_par_fonction(ds, jours_dispo), use_container_width=True)
    jour_site = st.selectbox("Jour pour la vue par site", jours_dispo, key="jour_site")
    st.plotly_chart(viz.histogramme_flux_par_site(ds, jour_site), use_container_width=True)

# ---------------------------------------------------------------- Paramètres
with tab_param:
    p = st.session_state.params
    st.subheader("Sélection")
    col1, col2 = st.columns(2)
    with col1:
        jours = st.multiselect("Jours à simuler", cfg.DAYS, default=cfg.DAYS)
        fonctions_all = sorted({f.fonction_support for f in ds.flux if f.fonction_support})
        fonctions = st.multiselect("Fonctions support (vide = toutes)", fonctions_all, default=[])
    with col2:
        veh_all = list(ds.vehicules.keys())
        defaut_veh = [v for v in veh_all if v != cfg.VEHICULE_EXCLU_DEFAUT]
        vehicules = st.multiselect("Types de véhicules autorisés", veh_all, default=defaut_veh)

    st.subheader("Capacité & combinaison des flux")
    col3, col4, col5 = st.columns(3)
    taux = col3.slider("Taux d'occupation surfacique", 0.30, 1.00, p.taux_occupation_surface, 0.05,
                       help="Part utile de la surface au sol prise en compte dans le bin-packing 2D.")
    rotation = col4.checkbox("Autoriser rotation 90° des contenants", value=p.autoriser_rotation_90)
    circ = col5.slider("Facteur de circulation (%)", 0, 100, int(p.facteur_circulation_pct), 5,
                       help="Majoration appliquée aux durées de trajet.")

    st.subheader("Postes chauffeurs & quais")
    col6, col7, col8, col9 = st.columns(4)
    vacation = col6.number_input("Durée vacation (min)", 120, 720, p.duree_vacation_min, 15)
    pause = col7.number_input("Pause (min)", 0, 120, p.duree_pause_min, 5)
    prise = col8.number_input("Prise de poste (min)", 0, 60, p.prise_poste_min, 5)
    cap_quai = col9.number_input("Capacité quai par défaut", 1, 10, p.capacite_quai_defaut, 1)
    col10, col11 = st.columns(2)
    desinf = col10.number_input("Durée désinfection sale→propre (min)", 0, 60, p.duree_desinfection_min, 5)
    horizon = col11.number_input("Horizon look-forward (min)", 0, 240, p.look_forward_horizon_min, 15)

    if st.button("💾 Enregistrer les paramètres", type="primary"):
        p.jours_a_simuler = jours
        p.fonctions_support = fonctions
        p.vehicules_autorises = vehicules
        p.taux_occupation_surface = taux
        p.autoriser_rotation_90 = rotation
        p.facteur_circulation_pct = float(circ)
        p.duree_vacation_min = int(vacation)
        p.duree_pause_min = int(pause)
        p.prise_poste_min = int(prise)
        p.capacite_quai_defaut = int(cap_quai)
        p.duree_desinfection_min = int(desinf)
        p.look_forward_horizon_min = int(horizon)
        st.session_state.params = p
        st.success("Paramètres enregistrés. Rendez-vous dans l'onglet « Optimisation ».")

# ---------------------------------------------------------------- Résultats
with tab_res:
    p = st.session_state.params
    st.subheader("Contrôles préalables")
    st.write(f"Jours sélectionnés : **{', '.join(p.jours_a_simuler) or '—'}**")
    st.write(f"Véhicules : **{', '.join(p.vehicules_autorises) or '—'}**")

    if bloquants:
        st.error("Des messages bloquants subsistent : corrigez le fichier avant de lancer.")

    lancer = st.button("🚀 Lancer l'optimisation", type="primary",
                       disabled=bool(bloquants) or not p.jours_a_simuler)

    if lancer:
        with st.spinner("Optimisation en cours…"):
            st.session_state.resultats = pipeline.resoudre(ds, p)
        st.success("Optimisation terminée.")

    res = st.session_state.resultats
    if res:
        # incompatibilités préalables
        total_inc = sum(len(r.get("incompatibles", [])) for r in res.values())
        total_ns = sum(len(r.get("non_servis", [])) for r in res.values())
        if total_inc:
            with st.expander(f"⚠️ {total_inc} flux incompatibles (préalable) — fenêtres à élargir", expanded=True):
                for jour, r in res.items():
                    for inc in r.get("incompatibles", []):
                        st.write(f"- **{jour} · {inc['flux_id']}** — {inc['raison']} · _{inc['suggestion']}_")

        st.subheader("Synthèse")
        st.plotly_chart(viz.synthese_flotte_barres(res), use_container_width=True)
        if total_ns == 0 and total_inc == 0:
            st.success("100 % des flux sélectionnés sont servis.")
        else:
            st.warning(f"{total_ns} flux non servis et {total_inc} incompatibles préalables (voir onglet 7 de l'export).")

        st.subheader("Gantt des postes")
        jour_gantt = st.selectbox("Jour", list(res.keys()), key="jour_gantt")
        max_p = st.slider("Nombre de postes affichés", 5, 80, 30)
        st.plotly_chart(viz.gantt_postes(res[jour_gantt], jour_gantt, max_p), use_container_width=True)

        st.subheader("Export")
        data = outputs.generer_classeur(res, ds, p)
        st.download_button("⬇️ Télécharger le classeur de résultats (9 onglets)",
                           data=data, file_name="OptiFLUX_resultats.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)
    else:
        st.info("Lancez l'optimisation pour afficher les résultats.")
