"""
optimizer.py
============
Moteur d'optimisation (heuristique constructive + look-forward).

Principe :
- les chargements (construits par route_builder) sont triés par heure de début ;
- on les affecte à des POSTES (1 poste = 1 véhicule conduit par 1 chauffeur,
  durée exacte = vacation RH), en minimisant le nombre de véhicules puis de
  postes ;
- chaque poste commence et finit au stationnement initial du véhicule (HSJ) ;
- on insère prise de poste, pause (au dépôt, fenêtre 2h centrée), désinfections
  (transition sale -> propre) et fin de poste ;
- on respecte les fenêtres horaires de collecte/livraison (sinon le chargement
  n'est pas accepté dans ce poste).

Le résultat est une liste de PosteSolution contenant la séquence complète et les
métriques nécessaires aux exports.

Hiérarchie d'optimisation appliquée (par construction et tri) :
1. respect des contraintes obligatoires (filtrage),
2. 100 % des flux servis (tout chargement non plaçable est reporté en non servi),
3-4. minimisation véhicules puis postes (réutilisation gloutonne),
5. minimisation des désinfections (tri sale/propre),
6-11. km, remplissage, temps morts (insertion au plus tôt).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Vehicule, Site
from .route_builder import Chargement
from . import compatibility
from . import config as cfg


@dataclass
class Operation:
    type: str
    site_dep: str | None
    site_arr: str | None
    debut: int
    fin: int
    flux_ids: list[str] = field(default_factory=list)
    contenants_charges: int = 0
    contenants_decharges: int = 0
    distance: float = 0.0
    a_plein: bool = False
    desinfection: bool = False
    charge_apres_surface: float = 0.0
    charge_apres_poids: float = 0.0
    nb_apres: int = 0


@dataclass
class PosteSolution:
    id: str
    jour: str
    vehicule_type: str
    vehicule_instance: str
    depot: str
    debut: int
    fin: int
    operations: list[Operation] = field(default_factory=list)
    pause_debut: int | None = None
    etat_sanitaire_courant: str = "propre"
    position: str = ""
    nb_chargements: int = 0
    # métriques cumulées
    km_total: float = 0.0
    km_plein: float = 0.0
    km_vide: float = 0.0
    t_conduite: float = 0.0
    t_manutention: float = 0.0
    t_quai: float = 0.0
    t_attente: float = 0.0
    t_desinfection: float = 0.0
    # taux de remplissage moyen des chargements du poste (surface au sol / poids)
    rempl_surf_pct: float = 0.0
    rempl_poids_pct: float = 0.0
    _rempl_surf_sum: float = 0.0
    _rempl_poids_sum: float = 0.0


def _remplissage_chargement(ch, veh: Vehicule, contenants_global: dict | None):
    """Taux de remplissage d'un chargement : (surface au sol %, poids %)."""
    if not contenants_global:
        return 0.0, 0.0
    surf = 0.0
    poids = 0.0
    for u in ch.unites:
        c = contenants_global.get(u.contenant)
        n = getattr(u, "nb_contenants", 1) or 1
        if c is not None:
            surf += c.surface_m2 * n
        pu = getattr(u, "poids_t", 0.0) or 0.0
        if pu:
            poids += pu
        elif c is not None:
            poids += (c.poids_plein_t if not getattr(u, "vide", False) else c.poids_vide_t) * n
    surf_pct = 100.0 * surf / veh.surface_m2 if veh.surface_m2 else 0.0
    poids_pct = 100.0 * poids / veh.poids_max_t if veh.poids_max_t else 0.0
    return min(surf_pct, 100.0), min(poids_pct, 100.0)


def occupation_utile_pct(p: PosteSolution) -> float:
    """Taux d'occupation utile = (conduite + manutention + mise à quai) / durée du poste."""
    d = p.fin - p.debut
    if d <= 0:
        return 0.0
    return 100.0 * (p.t_conduite + p.t_manutention + p.t_quai) / d


def evaluer_seuil_occupation(postes: list[PosteSolution], params: cfg.SimulationParams) -> dict:
    """Contrôle le seuil d'occupation (blocage dur) sur les types soumis au seuil.

    Retourne {'seuil', 'acceptable', 'violations':[...], 'types_controles'}.
    Si au moins un poste contrôlé est sous le seuil -> solution NON acceptable.
    """
    seuil = params.seuil_occupation_min_pct
    violations = []
    types_controles = sorted({p.vehicule_type for p in postes
                              if params.soumis_au_seuil(p.vehicule_type)})
    for p in postes:
        if not params.soumis_au_seuil(p.vehicule_type):
            continue
        occ = occupation_utile_pct(p)
        if occ + 1e-6 < seuil:
            violations.append({"poste": p.id, "vehicule": p.vehicule_instance,
                               "type": p.vehicule_type, "occupation_pct": round(occ, 1),
                               "manque_pts": round(seuil - occ, 1)})
    return {"seuil": seuil, "acceptable": len(violations) == 0,
            "violations": violations, "types_controles": types_controles}


def _duree(duree, params, o, d):
    return params.duree_avec_circulation(duree.get((o, d), 0.0))


def _dist(dist, o, d):
    return dist.get((o, d), 0.0)


def _sequencer_sites(depart_pos, collectes, livraisons, duree):
    """Ordonne collectes puis livraisons par plus proche voisin depuis la position."""
    def nn(pos, sites):
        restants = list(sites)
        ordre = []
        cur = pos
        while restants:
            nxt = min(restants, key=lambda s: duree.get((cur, s), 0.0))
            ordre.append(nxt)
            restants.remove(nxt)
            cur = nxt
        return ordre, cur
    ordre_c, pos1 = nn(depart_pos, collectes)
    ordre_l, pos2 = nn(pos1 if ordre_c else depart_pos, livraisons)
    return ordre_c, ordre_l, pos2


def _servir_chargement(poste: PosteSolution, ch: Chargement, veh: Vehicule,
                       sites: dict[str, Site], duree, dist, params: cfg.SimulationParams):
    """
    Tente d'insérer un chargement à la suite du poste. Retourne (ok, operations_temp,
    nouvelle_fin, nouvelle_position, nouvel_etat). N'altère pas le poste si non ok.
    """
    ops: list[Operation] = []
    t = poste.fin                  # heure courante = fin du dernier op
    pos = poste.position
    etat = poste.etat_sanitaire_courant

    # désinfection si transition sale -> propre
    if compatibility.besoin_desinfection(etat, ch.sale):
        # retour dépôt si nécessaire
        if pos != poste.depot:
            dd = _duree(duree, params, pos, poste.depot)
            ops.append(Operation("trajet", pos, poste.depot, int(t), int(t + dd),
                                  distance=_dist(dist, pos, poste.depot), a_plein=False))
            t += dd
            pos = poste.depot
        ops.append(Operation("desinfection", poste.depot, poste.depot, int(t),
                             int(t + params.duree_desinfection_min), desinfection=True))
        t += params.duree_desinfection_min
        etat = "propre"

    ordre_c, ordre_l, pos_fin = _sequencer_sites(pos, ch.sites_collecte, ch.sites_livraison, duree)

    # respecter le début au plus tôt (attente si arrivée avant hmin) ;
    # tolérance : on peut collecter jusqu'à `tolerance_fenetre_min` en avance,
    # ce qui permet aux tournées serrées de boucler dans leur créneau.
    debut_min = ch.debut_au_plus_tot - params.tolerance_fenetre_min

    charge_surf = poste_charge_courante(poste)
    charge_poids = 0.0
    nb_courant = 0

    # collectes
    for s in ordre_c:
        dd = _duree(duree, params, pos, s)
        if dd > 0 or pos != s:
            ops.append(Operation("trajet", pos, s, int(t), int(t + dd),
                                  distance=_dist(dist, pos, s), a_plein=(nb_courant > 0)))
            t += dd
            pos = s
        # attente si trop tôt
        if t < debut_min:
            ops.append(Operation("attente", s, s, int(t), int(debut_min)))
            t = debut_min
        site = sites.get(s)
        quai = site.presence_quai if site else False
        ops.append(Operation("mise_a_quai", s, s, int(t), int(t + veh.temps_mise_a_quai_min)))
        t += veh.temps_mise_a_quai_min
        # contenants collectés sur ce site
        unites_site = [u for u in ch.unites if u.site_depart == s]
        nb = sum(u.nb_contenants for u in unites_site)
        manut = veh.manut_min_par_cont(quai) * nb
        nb_courant += nb
        ops.append(Operation("chargement", s, s, int(t), int(t + manut),
                             flux_ids=[u.flux_id for u in unites_site],
                             contenants_charges=nb, nb_apres=nb_courant))
        t += manut

    # livraisons
    for s in ordre_l:
        dd = _duree(duree, params, pos, s)
        if dd > 0 or pos != s:
            ops.append(Operation("trajet", pos, s, int(t), int(t + dd),
                                  distance=_dist(dist, pos, s), a_plein=(nb_courant > 0)))
            t += dd
            pos = s
        site = sites.get(s)
        quai = site.presence_quai if site else False
        ops.append(Operation("mise_a_quai", s, s, int(t), int(t + veh.temps_mise_a_quai_min)))
        t += veh.temps_mise_a_quai_min
        unites_site = [u for u in ch.unites if u.site_arrivee == s]
        nb = sum(u.nb_contenants for u in unites_site)
        manut = veh.manut_min_par_cont(quai) * nb
        nb_courant = max(0, nb_courant - nb)
        ops.append(Operation("dechargement", s, s, int(t), int(t + manut),
                             flux_ids=[u.flux_id for u in unites_site],
                             contenants_decharges=nb, nb_apres=nb_courant))
        t += manut

    # contrôle fenêtre de livraison
    if t > ch.fin_au_plus_tard + params.tolerance_fenetre_min and not _tournee_derogation(ch):
        return False, [], poste.fin, poste.position, poste.etat_sanitaire_courant

    nouvel_etat = "sale" if ch.sale else etat
    return True, ops, int(t), pos, nouvel_etat


def _tournee_derogation(ch: Chargement) -> bool:
    """Dérogation horaire tolérée uniquement pour les tournées mutualisées dédiées."""
    return len(ch.unites) > 1 and all(
        (u.heure_max_livraison is None) for u in ch.unites)


def poste_charge_courante(poste: PosteSolution) -> float:
    return 0.0  # le suivi fin de charge se fait via les opérations


def cloturer_poste(poste: PosteSolution, duree, dist, params: cfg.SimulationParams):
    """Retour dépôt + fin de poste. Pas de comblement : la durée du poste est réelle."""
    t = poste.fin
    pos = poste.position
    if pos != poste.depot:
        dd = _duree(duree, params, pos, poste.depot)
        poste.operations.append(Operation("trajet", pos, poste.depot, int(t), int(t + dd),
                                          distance=_dist(dist, pos, poste.depot), a_plein=False))
        t += dd
        pos = poste.depot
    poste.operations.append(Operation("fin_poste", poste.depot, poste.depot, int(t),
                                      int(t + params.fin_poste_min)))
    t += params.fin_poste_min
    poste.fin = int(t)
    poste.position = pos


def _pause_dans_post(poste: PosteSolution, slot_debut: int, slot_fin: int,
                     params: cfg.SimulationParams, duree, dist) -> bool:
    """Insère la pause si on a dépassé le milieu du créneau et qu'elle n'est pas posée."""
    if poste.pause_debut is not None:
        return False
    milieu = (slot_debut + slot_fin) // 2
    if poste.fin < milieu:
        return False
    t = poste.fin
    pos = poste.position
    if pos != poste.depot:
        dd = _duree(duree, params, pos, poste.depot)
        poste.operations.append(Operation("trajet", pos, poste.depot, int(t), int(t + dd),
                                          distance=_dist(dist, pos, poste.depot)))
        t += dd
        pos = poste.depot
    poste.operations.append(Operation("pause", poste.depot, poste.depot,
                                      int(t), int(t + params.duree_pause_min)))
    poste.pause_debut = int(t)
    poste.fin = int(t + params.duree_pause_min)
    poste.position = poste.depot
    return True


def optimiser_jour(jour: str, chargements: list[Chargement], vehicules: dict[str, Vehicule],
                   sites: dict[str, Site], duree, dist, params: cfg.SimulationParams,
                   contenants_global: dict | None = None):
    """
    Affectation PAR VÉHICULE (et non plus par chargement) :

    - on ouvre un véhicule et on le REMPLIT sur ses créneaux de vacation
      (jusqu'à `nb_vacations_max`, p.ex. 06:00–13:30 puis 13:30–21:00) ;
    - à chaque étape on choisit le chargement faisable le PLUS PROCHE de la
      position courante : un retour chargé situé là où l'on vient de livrer est
      donc préféré -> les navettes deviennent bidirectionnelles (moins de km à
      vide) ;
    - on n'ouvre un nouveau véhicule que lorsqu'aucun chargement ne peut plus
      s'ajouter -> moins de véhicules, postes mieux remplis.

    Retourne (postes, non_servis).
    """
    from .route_builder import Chargement as _Ch, _finaliser, _plus_petit_vehicule_compatible

    creneaux = params.creneaux_vacation()
    postes: list[PosteSolution] = []
    non_servis: list[dict] = []
    compteur_instances: dict[str, int] = {}
    unassigned: list[Chargement] = [c for c in chargements]

    def first_pickup(ch: Chargement) -> str:
        return ch.sites_collecte[0] if ch.sites_collecte else (
            vehicules[ch.vehicule_type].stationnement_initial if ch.vehicule_type in vehicules else "HSJ")

    def construire_post(vtype: str, instance: str, slot_debut: int, slot_fin: int,
                        earliest: int = 0):
        """Construit un poste pour ce véhicule sur ce créneau ; sert le plus de
        chargements possible. Retourne (poste|None). Retire les chargements servis.

        `earliest` impose le début (chaînage : la 2e vacation commence après la 1re).
        La fin admissible est relative au chauffeur (début + durée de vacation,
        plafonnée à l'heure de fin maxi) : un poste démarré à 06:45 peut donc
        terminer une tournée jusqu'à ~14:15, ce qui évite la 'faille' entre vacations.
        """
    def construire_post(vtype: str, instance: str, slot_debut: int, slot_fin: int,
                        debut_impose: int | None = None):
        """Construit un poste de durée FIXE (= une vacation) pour ce véhicule.

        - `debut_impose` non nul : début calé (2e vacation chaînée) ;
        - sinon début flottant, calé sur le 1er chargement servable (≥ slot_debut)
          afin d'éviter l'attente initiale tout en gardant une durée constante.

        Le poste dure exactement `duree_vacation_min` : le temps non travaillé est
        comblé en 'inoccupé'. Retourne (poste|None) et retire les chargements servis.
        """
        veh = vehicules[vtype]
        depot = veh.stationnement_initial or "HSJ"

        candidats0 = [c for c in unassigned if c.vehicule_type == vtype]
        if not candidats0:
            return None

        if debut_impose is not None:
            debut = debut_impose
        else:
            # début flottant : au plus tôt servable, borné par le créneau
            debuts = []
            for c in candidats0:
                trajet = _duree(duree, params, depot, first_pickup(c))
                d = max(slot_debut, (c.debut_au_plus_tot or slot_debut)
                        - params.prise_poste_min - int(trajet))
                debuts.append(d)
            debut = min(debuts) if debuts else slot_debut
            # ne pas démarrer si tard que le poste dépasserait l'heure de fin maxi
            debut = min(debut, params.heure_fin_max_min - params.duree_vacation_min)
            debut = max(debut, slot_debut)
        fin_vacation = min(debut + params.duree_vacation_min, params.heure_fin_max_min)
        # retour dépôt strictement dans la vacation -> durée de poste EXACTE
        limite_retour = fin_vacation

        p = PosteSolution(id=f"{jour[:3]}-P{len(postes)+1:03d}", jour=jour,
                          vehicule_type=vtype, vehicule_instance=instance,
                          depot=depot, debut=debut, fin=debut, position=depot)
        p.operations.append(Operation("prise_poste", depot, depot,
                                      int(debut), int(debut + params.prise_poste_min)))
        p.fin = debut + params.prise_poste_min

        servis: list[Chargement] = []
        last_pickup = None
        while True:
            _pause_dans_post(p, debut, fin_vacation, params, duree, dist)
            meilleur = None
            meilleur_cle = None
            for ch in unassigned:
                if ch.vehicule_type != vtype:
                    continue
                ok, ops, fin, pos, etat = _servir_chargement(p, ch, veh, sites, duree, dist, params)
                if not ok:
                    continue
                retour = _duree(duree, params, pos, depot)
                if fin + retour + params.fin_poste_min > limite_retour:
                    continue
                pk = ch.sites_collecte[0] if ch.sites_collecte else depot
                deadhead = _duree(duree, params, p.position, pk)
                backhaul = deadhead <= 1e-6
                meme_navette = (last_pickup is not None and pk == last_pickup)
                cle = (0 if backhaul else 1,
                       0 if meme_navette else 1,
                       fin, deadhead, -ch.nb_contenants)
                if meilleur_cle is None or cle < meilleur_cle:
                    meilleur_cle = cle
                    meilleur = (ch, ops, fin, pos, etat)
            if meilleur is None:
                break
            ch, ops, fin, pos, etat = meilleur
            p.operations.extend(ops)
            p.fin = fin
            p.position = pos
            p.etat_sanitaire_courant = etat
            p.nb_chargements += 1
            sp, pp = _remplissage_chargement(ch, veh, contenants_global)
            p._rempl_surf_sum += sp
            p._rempl_poids_sum += pp
            last_pickup = ch.sites_collecte[0] if ch.sites_collecte else depot
            servis.append(ch)
            unassigned.remove(ch)

        if not servis:
            return None
        if p.pause_debut is None:
            _pause_dans_post(p, debut, fin_vacation, params, duree, dist)
            if p.pause_debut is None:
                _forcer_pause(p, params)
        cloturer_poste(p, duree, dist, params)
        # durée FIXE : comblement 'inoccupé' jusqu'à la fin exacte de la vacation
        if p.fin < fin_vacation:
            p.operations.append(Operation("inoccupe", depot, depot, int(p.fin), int(fin_vacation)))
        p.fin = fin_vacation
        _calculer_metriques(p, params)
        if p.nb_chargements:
            p.rempl_surf_pct = p._rempl_surf_sum / p.nb_chargements
            p.rempl_poids_pct = p._rempl_poids_sum / p.nb_chargements
        return p

    def decomposer_en_place(ch: Chargement):
        unassigned.remove(ch)
        for u in ch.unites:
            v = _plus_petit_vehicule_compatible([u], vehicules, sites, contenants_global, params)
            if v is None:
                non_servis.append({"flux_ids": [u.flux_id], "raison": "Aucun véhicule compatible"})
                continue
            unassigned.append(_finaliser(_Ch(unites=[u], vehicule_type=v.type_nom), contenants_global))

    securite = 0
    while unassigned:
        securite += 1
        if securite > 100000:
            break
        # graine = chargement le plus précoce non encore servi
        seed = min(unassigned, key=lambda c: c.debut_au_plus_tot)
        vtype = seed.vehicule_type
        if not vtype or vtype not in vehicules:
            if len(seed.unites) > 1:
                decomposer_en_place(seed)
            else:
                unassigned.remove(seed)
                non_servis.append({"flux_ids": [u.flux_id for u in seed.unites],
                                   "raison": "Aucun véhicule compatible"})
            continue

        plafond = params.max_par_type.get(vtype)
        if plafond is not None and compteur_instances.get(vtype, 0) >= plafond:
            # plus de véhicule disponible de ce type : décomposer ou non servi
            if len(seed.unites) > 1:
                decomposer_en_place(seed)
            else:
                unassigned.remove(seed)
                non_servis.append({"flux_ids": [u.flux_id for u in seed.unites],
                                   "raison": f"Plafond {vtype} atteint"})
            continue

        compteur_instances[vtype] = compteur_instances.get(vtype, 0) + 1
        instance = f"{vtype} #{compteur_instances[vtype]}"
        a_servi = False
        slot0 = creneaux[0]
        # 1re vacation : démarrage calé à l'heure mini (favorise le chaînage)
        p1 = construire_post(vtype, instance, slot0[0], slot0[1], debut_impose=slot0[0])
        if p1 is None:
            # rien de servable dès l'heure mini -> poste à début flottant (charges tardives)
            p1 = construire_post(vtype, instance, slot0[0], slot0[1])
        if p1 is not None:
            postes.append(p1)
            a_servi = True
            # vacations chaînées seulement si la 1re a démarré à l'heure mini
            prec_fin = p1.fin
            if p1.debut <= slot0[0]:
                for _ in range(1, params.nb_vacations_max):
                    if prec_fin + params.duree_vacation_min > params.heure_fin_max_min:
                        break
                    pk = construire_post(vtype, instance, prec_fin,
                                         prec_fin + params.duree_vacation_min,
                                         debut_impose=prec_fin)
                    if pk is None:
                        break
                    postes.append(pk)
                    prec_fin = pk.fin
        if not a_servi:
            # ce véhicule n'a rien pu servir : la graine est infaisable seule
            compteur_instances[vtype] -= 1
            if len(seed.unites) > 1:
                decomposer_en_place(seed)
            else:
                unassigned.remove(seed)
                non_servis.append({"flux_ids": [u.flux_id for u in seed.unites],
                                   "raison": "Fenêtre horaire infaisable"})

    return postes, non_servis


def _debut_nouveau_poste(ch: Chargement, veh: Vehicule, duree, params: cfg.SimulationParams) -> int:
    depot = veh.stationnement_initial or "HSJ"
    premier = ch.sites_collecte[0] if ch.sites_collecte else depot
    trajet = int(_duree(duree, params, depot, premier))
    debut = ch.debut_au_plus_tot - params.prise_poste_min - trajet
    debut = max(debut, params.heure_debut_mini_min)
    if debut + params.duree_vacation_min > params.heure_fin_max_min:
        debut = params.heure_fin_max_min - params.duree_vacation_min
    return int(debut)


def postes_compteur_rollback(compteur, vtype):
    if compteur.get(vtype, 0) > 0:
        compteur[vtype] -= 1


def _forcer_pause(poste: PosteSolution, params: cfg.SimulationParams):
    milieu = poste.debut + params.duree_vacation_min // 2
    poste.pause_debut = milieu
    poste.operations.append(Operation("pause", poste.depot, poste.depot,
                                      int(milieu), int(milieu + params.duree_pause_min)))


def _calculer_metriques(poste: PosteSolution, params: cfg.SimulationParams):
    for op in poste.operations:
        if op.type == "trajet":
            poste.km_total += op.distance
            d = op.fin - op.debut
            poste.t_conduite += d
            if op.a_plein:
                poste.km_plein += op.distance
            else:
                poste.km_vide += op.distance
        elif op.type in ("chargement", "dechargement"):
            poste.t_manutention += op.fin - op.debut
        elif op.type == "mise_a_quai":
            poste.t_quai += op.fin - op.debut
        elif op.type in ("attente", "inoccupe"):
            poste.t_attente += op.fin - op.debut
        elif op.type == "desinfection":
            poste.t_desinfection += op.fin - op.debut
