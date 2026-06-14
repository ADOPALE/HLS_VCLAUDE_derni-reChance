# 🚚 OptiFLUX — Optimisation de la logistique hospitalière

OptiFLUX dimensionne une **flotte de véhicules** et construit des **tournées**
(collectes / livraisons) à partir d'un fichier Excel décrivant les flux
logistiques d'un établissement hospitalier (linge, restauration, pharmacie,
stérilisation, magasin, etc.).

L'application lit votre fichier de paramétrage, vérifie sa cohérence, vous laisse
régler les hypothèses, puis calcule pour chaque jour : le **nombre de véhicules
par type**, le **nombre de postes chauffeurs**, le détail des **tournées**, le
**planning des quais** et une série d'**indicateurs**. Tout est exportable dans
un classeur Excel de 9 onglets.

---

## 1. Installation (pas à pas, pour non-développeurs)

### a. Installer Python
Installez **Python 3.10 ou plus récent** depuis <https://www.python.org/downloads/>.
Sur Windows, cochez « Add Python to PATH » pendant l'installation.

### b. Récupérer le dossier OptiFLUX
Dézippez l'archive `optiflux.zip` quelque part (par exemple sur le Bureau).
Vous obtenez un dossier `optiflux/`.

### c. Ouvrir un terminal dans ce dossier
- **Windows** : ouvrez le dossier, tapez `cmd` dans la barre d'adresse, Entrée.
- **macOS** : clic droit sur le dossier → « Nouveau terminal au dossier ».

### d. (Recommandé) créer un environnement isolé
```bash
python -m venv .venv
# Windows :
.venv\Scripts\activate
# macOS / Linux :
source .venv/bin/activate
```

### e. Installer les dépendances
```bash
pip install -r requirements.txt
```

---

## 2. Lancer l'application

```bash
streamlit run app.py
```

Votre navigateur s'ouvre sur l'interface OptiFLUX. Si ce n'est pas le cas,
copiez l'adresse affichée dans le terminal (généralement
`http://localhost:8501`).

Pour arrêter : revenez au terminal et faites `Ctrl + C`.

---

## 3. Utilisation

1. **Importer** (panneau de gauche) : chargez votre fichier `.xlsx` puis cliquez
   sur « Importer ». Un fichier d'exemple est fourni dans `sample/`.
2. **Rapport d'import** : vérifiez la volumétrie (sites, véhicules, contenants,
   flux) et les éventuels messages **bloquants** (à corriger dans le fichier) ou
   **alertes**.
3. **Onglet 📊 Flux** : visualisez les volumes de contenants par fonction et par
   site, jour par jour.
4. **Onglet ⚙️ Paramètres** : sélectionnez les **jours à simuler**, les
   **fonctions** et **véhicules** à considérer, et réglez les hypothèses
   (taux d'occupation surfacique, facteur de circulation, durées de poste/pause,
   capacité des quais, désinfection, horizon look-forward). Cliquez sur
   « Enregistrer les paramètres ».
5. **Onglet 🚀 Optimisation** : lancez le calcul. Vous obtenez la synthèse de
   flotte, le diagramme de Gantt des postes, la liste des éventuels flux non
   servis, et le **bouton de téléchargement** du classeur de résultats.

---

## 4. Le fichier d'entrée attendu

Le classeur doit contenir les onglets suivants (noms exacts) :

| Onglet | Contenu |
|---|---|
| `param RH` | Durée de vacation, pause, plages horaires (début/fin). |
| `param Sites` | Liste des sites, présence de quai, compatibilité par type de véhicule. |
| `param Véhicules` | Types de véhicules : dimensions internes, poids max, coûts, hayon, temps de mise à quai, manutention, compatibilité contenants. |
| `param Contenants` | Contenants : dimensions (L×l) et poids vide/plein. |
| `matrice Durée` | Durées de trajet entre sites (minutes). |
| `matrice Dist` | Distances entre sites (km). |
| `M flux` | Les flux : origine, destination, fonction, contenant, plein/vide, sale/propre, mixité, exclusions, mutualisation, quantités par jour, fenêtres horaires, urgence. |
| `LISTES` | Listes de référence (optionnel). |

> 💡 Les cellules vides sont tolérées. Une matrice à `0` signifie « sites
> adjacents » (temps de trajet nul), mais le temps de mise à quai reste compté.

---

## 5. Comment fonctionne le moteur

Le calcul s'enchaîne ainsi :

1. **Import & nettoyage** (`data_loader`, `data_cleaning`) : lecture, conversion
   des horaires en minutes, des `OUI/NON` en booléens, gestion des cellules
   vides.
2. **Validation** (`validators`) : contrôles de cohérence (sites inconnus,
   absents des matrices, contenants/véhicules sans capacité…).
3. **Préparation par jour** (`preprocessing`) : sélection des flux actifs et
   **éclatement des gros volumes** en *unités de transport*. Le nombre de
   contenants par véhicule n'est **pas** lu dans le fichier : il est **dérivé
   par un bin-packing 2D**.
4. **Bin-packing 2D** (`binpacking`) : oracle de faisabilité capacitaire. Chaque
   contenant est un rectangle (L×l) ; on teste s'ils tiennent à plat dans le
   plancher du véhicule, **avec rotation 90°** et un **taux d'occupation
   surfacique réglable**. Cet oracle est appelé **avant toute combinaison** de
   flux pour garantir que les flux combinés tiennent réellement.
5. **Construction des chargements** (`route_builder`) : les unités pleines
   forment un chargement chacune ; les **reliquats** d'un **même corridor**
   (même origine ET même destination) avec une **fenêtre commune suffisante**
   sont consolidés dans le plus petit véhicule compatible — en revérifiant la
   compatibilité (mixité, propre/sale, exclusions) et le bin-packing. Les flux
   de corridors différents ne sont **pas** fusionnés de force : ils sont
   enchaînés par le véhicule à l'étape suivante (évite les chargements
   multi-arrêts à fenêtre nulle).
6. **Optimisation — affectation PAR VÉHICULE** (`optimizer`) : on ouvre un
   véhicule et on le **remplit** sur ses **vacations chaînées** (par défaut 2 :
   06:00→13:30 puis 13:30→21:00, deux chauffeurs successifs) **avant** d'en
   ouvrir un autre. À chaque étape, le moteur choisit le prochain chargement
   selon une **hiérarchie** :
   1. **backhaul** — charger là où l'on vient de livrer (retour chargé) ;
   2. **poursuite de la navette** — rester sur la même origine ;
   3. **finir au plus tôt** — compacter le temps.

   Cette logique produit des **navettes bidirectionnelles** (beaucoup moins de
   km à vide) et des **postes mieux remplis** (moins de véhicules). On insère
   prise de poste, **pause**, **désinfection** à chaque transition sale →
   propre, et fin de poste ; la **durée d'un poste est réelle** (pas de
   comblement artificiel). Une **tolérance de fenêtre** réglable (quelques
   minutes) permet aux navettes très serrées (trajet ≈ largeur de fenêtre,
   typiquement le bio HGRL) de s'enchaîner sur un même véhicule.
7. **Contrôle du seuil d'occupation (blocage dur)** : après résolution, le
   moteur calcule le **taux d'occupation utile** de chaque poste
   = (conduite + manutention + mise à quai) / durée du poste. Pour les **types
   de véhicules soumis au seuil** (tous sauf, par défaut, `VL FRIGO BIO` et
   `FOURGON`, dont les postes sont naturellement courts), si un poste est
   **sous le seuil réglable**, la solution est déclarée **NON acceptable** :
   les postes fautifs sont listés dans l'interface et dans l'onglet *Contrôles*.
8. **Plannings & exports** (`driver_scheduler`, `dock_scheduler`, `outputs`,
   `visualization`), avec **KPI de remplissage** (surface au sol et poids) par
   poste et en moyenne.

### Hiérarchie d'optimisation appliquée
Par construction et par tri, le moteur vise dans l'ordre :
respect des contraintes obligatoires → 100 % des flux servis → seuil
d'occupation respecté → minimisation des véhicules (remplissage par véhicule +
vacations chaînées) → navettes bidirectionnelles (moins de km à vide) →
réduction des désinfections et des temps morts.

---

## 6. Hypothèses métier (importantes)

- **Capacité des véhicules** : déduite par bin-packing 2D **une seule couche**
  (pas d'empilement vertical), rotation 90° autorisée, plafonnée par le **poids
  max**. Le **taux d'occupation surfacique** (par défaut 80 %) réserve une part
  de la surface aux allées, manœuvres et calage ; il est **réglable** pour tester
  la robustesse en cas de variation des flux.
- **Combinaison des flux** : autorisée uniquement si « transport mixte » le
  permet, si les règles d'exclusion sont respectées, et si propre/sale est
  compatible (sinon désinfection intercalée).
- **Désinfection** : un véhicule ayant transporté du **sale** est désinfecté
  (durée réglable) avant de transporter du **propre**.
- **Postes** : un poste dure exactement la vacation RH (7 h 30 par défaut),
  pause incluse, avec prise et fin de poste incompressibles.
- **Dépôt** : chaque véhicule part et revient à son stationnement initial.
- **100 % des flux** du tableau `M flux` sont considérés (le nom du fichier
  d'exemple, « hors_BIO », n'a aucune incidence : la biologie est incluse).

---

## 7. Limites connues (transparence)

- Le moteur est une **heuristique constructive + look-forward**, pas un solveur
  exact (MILP / CP-SAT). Il fournit une solution **réalisable et cohérente**, qui
  respecte toutes les contraintes et sert 100 % des flux de l'exemple, mais
  **n'est pas garantie optimale** au véhicule près.
- L'**énumération exhaustive** « 0, 1, 2, 3 véhicules par type » évoquée dans le
  cahier des charges est prévue dans l'architecture (paramètre `max_par_type`)
  mais la version actuelle utilise une **dérivation gloutonne** du nombre de
  véhicules, plus rapide à l'échelle du fichier réel. Un solveur exact pourrait
  être branché dans `optimizer.py` sans changer le reste.
- Le bin-packing est **conservateur** : il ne surestime jamais la capacité
  (il peut, dans de rares cas, sous-estimer une disposition très optimisée).
- La faisabilité des fenêtres horaires sur les tournées multi-arrêts est vérifiée
  de bout en bout (début au plus tôt, fin au plus tard) ; les arrêts
  intermédiaires sont approchés.

---

## 8. Dépannage

| Symptôme | Solution |
|---|---|
| `streamlit : command not found` | Activez l'environnement (`.venv`) et refaites `pip install -r requirements.txt`. |
| « Onglets obligatoires manquants » | Vérifiez les **noms exacts** des onglets (voir §4). |
| Messages **bloquants** | Un site des flux est absent de `param Sites` ou de la matrice : corrigez le fichier et réimportez. |
| Des flux « non servis » | Élargissez la fenêtre horaire (voir suggestions affichées), ajoutez un type de véhicule, ou baissez le taux d'occupation. |
| Le calcul semble long | Réduisez le nombre de jours simulés, ou de fonctions. |

---

## 9. Structure du projet

```
optiflux/
├── app.py                  # Interface Streamlit (point d'entrée)
├── requirements.txt
├── README.md
├── .gitignore
├── sample/
│   └── OptiFLUX_exemple.xlsx
├── tests/
│   └── test_optiflux.py
└── optiflux/               # Le moteur (package Python)
    ├── __init__.py
    ├── config.py           # Paramètres et valeurs par défaut
    ├── models.py           # Modèles de données (Pydantic)
    ├── data_loader.py      # Lecture du classeur Excel
    ├── data_cleaning.py    # Nettoyage + construction des objets métier
    ├── validators.py       # Contrôles de cohérence
    ├── binpacking.py       # Bin-packing 2D (rotation 90°)
    ├── compatibility.py    # Règles de compatibilité / mixité / désinfection
    ├── time_windows.py     # Fenêtres horaires
    ├── preprocessing.py    # Préparation des flux par jour + éclatement
    ├── route_builder.py    # Construction des chargements combinés
    ├── optimizer.py        # Moteur d'affectation (postes / véhicules)
    ├── fleet_generator.py  # Orchestration jour + synthèse flotte
    ├── driver_scheduler.py # Synthèse des postes chauffeurs
    ├── dock_scheduler.py   # Planning des quais
    ├── outputs.py          # Export Excel (9 onglets)
    └── visualization.py    # Graphiques Plotly (Gantt, histogrammes)
```

---

## 10. Tests

```bash
python tests/test_optiflux.py
# ou, si pytest est installé :
python -m pytest tests/
```

Les tests couvrent les conversions, le bin-packing (dont la rotation et le
plafond de poids), les règles de compatibilité, et un test bout-en-bout sur le
fichier d'exemple (100 % des flux servis attendu).
