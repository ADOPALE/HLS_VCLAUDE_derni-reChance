"""
binpacking.py
=============
Oracle de faisabilité capacitaire 2D.

On modélise le plancher du véhicule comme un rectangle (L x l interne) et chaque
contenant comme un rectangle (longueur x largeur). On teste si l'ensemble des
contenants tient à plat, en une seule couche, avec rotation 90° autorisée.

Algorithme : First-Fit-Decreasing par « étagères » (shelf packing). C'est une
heuristique rapide et conservatrice (elle ne sur-estime jamais la capacité) :
si elle dit « ça tient », ça tient réellement ; si elle dit « ça ne tient pas »,
une disposition plus fine pourrait parfois passer, mais on reste du bon côté de
la sécurité.

Le taux d'occupation surfacique (params.taux_occupation_surface) réduit la
surface utile pour tenir compte des allées, manœuvres et calage.
"""

from __future__ import annotations


def _shelf_fit(rects: list[tuple[float, float]], bin_l: float, bin_w: float,
               rotation: bool) -> bool:
    """
    rects : liste de (longueur, largeur) de chaque contenant.
    bin_l, bin_w : dimensions internes utiles du véhicule.
    Retourne True si tout tient en une couche.
    """
    # On range les plus grands d'abord (FFD). On oriente chaque pièce pour
    # minimiser sa hauteur d'étagère.
    pieces = []
    for (l, w) in rects:
        if rotation:
            # orientation : on met la plus petite dimension en "hauteur d'étagère"
            h = min(l, w)
            ww = max(l, w)
        else:
            # sans rotation : longueur le long de la longueur du véhicule (bin_l)
            h, ww = l, w
        pieces.append((h, ww))
    pieces.sort(key=lambda p: (-p[0], -p[1]))

    used_height = 0.0       # cumul des hauteurs d'étagères (le long de bin_l)
    shelf_height = 0.0      # hauteur de l'étagère courante
    shelf_used_width = 0.0  # largeur déjà occupée sur l'étagère courante

    for (h, ww) in pieces:
        # la pièce doit tenir en largeur dans le véhicule
        if ww > bin_w + 1e-9:
            # essayer l'autre orientation si rotation
            if rotation and h <= bin_w + 1e-9 and ww <= bin_l + 1e-9:
                h, ww = ww, h
            else:
                return False
        # tient-elle sur l'étagère courante ?
        if shelf_used_width + ww <= bin_w + 1e-9 and shelf_height >= h - 1e-9:
            shelf_used_width += ww
        else:
            # nouvelle étagère
            used_height += shelf_height
            if used_height + h > bin_l + 1e-9:
                return False
            shelf_height = h
            shelf_used_width = ww
    used_height += shelf_height
    return used_height <= bin_l + 1e-9


def contenants_tiennent(rect_list: list[tuple[float, float]],
                        veh_l: float, veh_w: float,
                        taux_occupation: float, rotation: bool = True) -> bool:
    """
    Vérifie qu'une liste de contenants (longueur, largeur) tient dans un véhicule.
    On applique le taux d'occupation en réduisant la longueur utile du plancher
    (équivalent à réserver une part de surface aux allées/manœuvres).
    """
    if not rect_list:
        return True
    veh_l_utile = veh_l * max(0.05, taux_occupation)
    return _shelf_fit(rect_list, veh_l_utile, veh_w, rotation)


def capacite_max_homogene(cont_l: float, cont_w: float,
                          veh_l: float, veh_w: float,
                          taux_occupation: float, poids_unit_t: float,
                          poids_max_t: float, rotation: bool = True) -> int:
    """
    Nombre maximal de contenants IDENTIQUES tenant dans le véhicule (surface),
    plafonné par le poids. Utilisé pour l'éclatement des gros volumes.
    """
    if cont_l <= 0 or cont_w <= 0:
        return 0
    # borne surfacique théorique
    surf_utile = veh_l * veh_w * max(0.05, taux_occupation)
    par_surface = int(surf_utile // (cont_l * cont_w))
    # raffinement : on vérifie réellement par packing décroissant jusqu'à par_surface
    n = par_surface
    while n > 0:
        if contenants_tiennent([(cont_l, cont_w)] * n, veh_l, veh_w, taux_occupation, rotation):
            break
        n -= 1
    # plafond poids
    if poids_unit_t > 0:
        par_poids = int(poids_max_t // poids_unit_t)
        n = min(n, par_poids)
    return max(0, n)
