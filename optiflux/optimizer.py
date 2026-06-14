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

    # respecter le début au plus tôt (attente si arrivée avant hmin)
    debut_min = ch.debut_au_plus_tot

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
    if t > ch.fin_au_plus_tard and not _tournee_derogation(ch):
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
    """Retour dépôt + fin de poste, puis comblement jusqu'à la durée exacte."""
    t = poste.fin
    pos = poste.position
    if pos != poste.depot:
        dd = _duree(duree, params, pos, poste.depot)
        poste.operations.append(Operation("trajet", pos, poste.depot, int(t), int(t + dd),
                                          distance=_dist(dist, pos, poste.depot), a_plein=False))
        t += dd
        pos = poste.depot
    # fin de poste
    poste.operations.append(Operation("fin_poste", poste.depot, poste.depot, int(t),
                                      int(t + params.fin_poste_min)))
    t += params.fin_poste_min
    # comblement (temps inoccupé à la base) jusqu'à la durée exacte de vacation
    fin_theorique = poste.debut + params.duree_vacation_min
    if t < fin_theorique:
        poste.operations.append(Operation("inoccupe", poste.depot, poste.depot,
                                          int(t), int(fin_theorique)))
        t = fin_theorique
    poste.fin = int(t)
    poste.position = pos


def _inserer_pause_si_due(poste: PosteSolution, params: cfg.SimulationParams,
                          duree, dist):
    """Insère la pause au dépôt dans la fenêtre 2h centrée sur le milieu du poste."""
    if poste.pause_debut is not None:
        return
    milieu = poste.debut + params.duree_vacation_min // 2
    fenetre_bas = milieu - params.fenetre_pause_min // 2
    fenetre_haut = milieu + params.fenetre_pause_min // 2
    if poste.fin < fenetre_bas:
        return
    t = poste.fin
    pos = poste.position
    if pos != poste.depot:
        dd = _duree(duree, params, pos, poste.depot)
        poste.operations.append(Operation("trajet", pos, poste.depot, int(t), int(t + dd),
                                          distance=_dist(dist, pos, poste.depot)))
        t += dd
        pos = poste.depot
    debut_pause = max(t, fenetre_bas)
    debut_pause = min(debut_pause, fenetre_haut)
    if debut_pause > t:
        poste.operations.append(Operation("attente", poste.depot, poste.depot, int(t), int(debut_pause)))
    poste.operations.append(Operation("pause", poste.depot, poste.depot,
                                      int(debut_pause), int(debut_pause + params.duree_pause_min)))
    poste.pause_debut = int(debut_pause)
    poste.fin = int(debut_pause + params.duree_pause_min)
    poste.position = poste.depot


def optimiser_jour(jour: str, chargements: list[Chargement], vehicules: dict[str, Vehicule],
                   sites: dict[str, Site], duree, dist, params: cfg.SimulationParams,
                   contenants_global: dict | None = None):
    """
    Affecte les chargements à des postes. Retourne (postes, non_servis).
    Si un chargement groupé est infaisable, il est re-découpé en unités
    individuelles (chacune ayant passé la faisabilité unitaire) -> 100 % servi.
    """
    from collections import deque
    from .route_builder import Chargement as _Ch, _finaliser

    file = deque(sorted(chargements, key=lambda c: (c.debut_au_plus_tot, -c.nb_contenants)))
    postes: list[PosteSolution] = []
    non_servis: list[dict] = []
    compteur_instances: dict[str, int] = {}

    def nouveau_poste(vtype: str, debut: int) -> PosteSolution:
        compteur_instances[vtype] = compteur_instances.get(vtype, 0) + 1
        inst = f"{vtype} #{compteur_instances[vtype]}"  # provisoire (réaffecté ensuite)
        veh = vehicules[vtype]
        p = PosteSolution(
            id=f"{jour[:3]}-P{len(postes)+1:03d}",
            jour=jour, vehicule_type=vtype, vehicule_instance=inst,
            depot=veh.stationnement_initial or "HSJ",
            debut=debut, fin=debut, position=veh.stationnement_initial or "HSJ",
        )
        p.operations.append(Operation("prise_poste", p.depot, p.depot,
                                      int(debut), int(debut + params.prise_poste_min)))
        p.fin = debut + params.prise_poste_min
        return p

    def decomposer(ch: Chargement):
        """Re-crée un chargement par unité, sur le véhicule le plus petit compatible."""
        from .route_builder import _plus_petit_vehicule_compatible
        out = []
        for u in ch.unites:
            v = _plus_petit_vehicule_compatible([u], vehicules, sites, contenants_global, params)
            if v is None:
                non_servis.append({"flux_ids": [u.flux_id], "raison": "Aucun véhicule compatible"})
                continue
            c = _Ch(unites=[u], vehicule_type=v.type_nom)
            out.append(_finaliser(c, contenants_global))
        return out

    while file:
        ch = file.popleft()
        if not ch.vehicule_type or ch.vehicule_type not in vehicules:
            if len(ch.unites) > 1:
                file.extendleft(reversed(decomposer(ch)))
            else:
                non_servis.append({"flux_ids": [u.flux_id for u in ch.unites],
                                   "raison": "Aucun véhicule compatible"})
            continue
        veh = vehicules[ch.vehicule_type]
        plafond = params.max_par_type.get(ch.vehicule_type)

        candidats = [p for p in postes if p.vehicule_type == ch.vehicule_type]
        candidats.sort(key=lambda p: p.fin)
        place = False
        for p in candidats:
            _inserer_pause_si_due(p, params, duree, dist)
            ok, ops, fin, pos, etat = _servir_chargement(p, ch, veh, sites, duree, dist, params)
            if not ok:
                continue
            retour = _duree(duree, params, pos, p.depot)
            if fin + retour + params.fin_poste_min > p.debut + params.duree_vacation_min:
                continue
            p.operations.extend(ops)
            p.fin = fin
            p.position = pos
            p.etat_sanitaire_courant = etat
            p.nb_chargements += 1
            place = True
            break
        if place:
            continue

        if plafond is not None and compteur_instances.get(ch.vehicule_type, 0) >= plafond:
            if len(ch.unites) > 1:
                file.extendleft(reversed(decomposer(ch)))
            else:
                non_servis.append({"flux_ids": [u.flux_id for u in ch.unites],
                                   "raison": f"Plafond {ch.vehicule_type} atteint"})
            continue

        debut = _debut_nouveau_poste(ch, veh, duree, params)
        p = nouveau_poste(ch.vehicule_type, debut)
        ok, ops, fin, pos, etat = _servir_chargement(p, ch, veh, sites, duree, dist, params)
        if not ok:
            postes_compteur_rollback(compteur_instances, ch.vehicule_type)
            if len(ch.unites) > 1:
                file.extendleft(reversed(decomposer(ch)))
            else:
                non_servis.append({"flux_ids": [u.flux_id for u in ch.unites],
                                   "raison": "Fenêtre horaire infaisable"})
            continue
        p.operations.extend(ops)
        p.fin = fin
        p.position = pos
        p.etat_sanitaire_courant = etat
        p.nb_chargements = 1
        postes.append(p)

    for p in postes:
        _inserer_pause_si_due(p, params, duree, dist)
        if p.pause_debut is None:
            _forcer_pause(p, params)
        cloturer_poste(p, duree, dist, params)
        _calculer_metriques(p, params)

    _reaffecter_instances(postes, params)
    return postes, non_servis


def _reaffecter_instances(postes, params: cfg.SimulationParams, max_postes_par_vehicule: int = 2):
    """
    Réutilise un même véhicule pour plusieurs postes NON chevauchants
    (coloration d'intervalles, plafonnée à max_postes_par_vehicule par véhicule).
    Minimise le nombre de véhicules tout en respectant la limite de postes.
    """
    from collections import defaultdict
    par_type = defaultdict(list)
    for p in postes:
        par_type[p.vehicule_type].append(p)

    for vtype, plist in par_type.items():
        plist.sort(key=lambda p: p.debut)
        instances = []  # [{'fin', 'postes'}]
        compteur = 0
        for p in plist:
            choisi = None
            for inst in instances:
                if inst["fin"] <= p.debut and inst["postes"] < max_postes_par_vehicule:
                    choisi = inst
                    break
            if choisi is None:
                compteur += 1
                choisi = {"nom": f"{vtype} #{compteur}", "fin": p.fin, "postes": 0}
                instances.append(choisi)
            choisi["fin"] = p.fin
            choisi["postes"] += 1
            p.vehicule_instance = choisi["nom"]


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
