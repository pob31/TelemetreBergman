# Cadreur Bergman — Lisez-moi (français)

Guide pratique pour l'**appli Cadreur** (sur le Mac de la régie). Cadreur garde la vidéo
projetée **calée sur le tulle qui se déplace** : il lit la distance mesurée par le Pi
(télémètre) et ajuste en continu l'**échelle**, la **position horizontale** et **verticale**
des calques Millumin (face et rétro).

- Détails techniques (anglais) : [`documentation/PRD-cadreur.md`](documentation/PRD-cadreur.md)
- Appli du Pi / télémètre (anglais) : [`README.md`](README.md)

---

## Le principe en deux phrases

Le Pi mesure la distance du tulle et l'envoie en direct. Cadreur interpole, pour chaque
calque, une **échelle + position horizontale + verticale** (valeurs **0.0–1.0**) à partir de
points que **tu as calibrés** à quelques positions du tulle, et les envoie à Millumin en OSC.
Millumin traduit ce 0–1 en pixels/échelle via ses **Interactions** (0.5 = centré).

## Installation / réinstallation complète (sur un Mac)

Procédure complète, d'un Mac vierge à un poste prêt pour le spectacle.

### 1. Prérequis

- **Python 3.11 ou plus récent** — l'installeur de <https://www.python.org/downloads/>
  convient. Vérifier : `python3 --version`.
- Un accès **internet une seule fois** (installation des dépendances).

### 2. Récupérer le projet

```bash
git clone https://github.com/pob31/TelemetreBergman.git
cd TelemetreBergman
```

(ou copier le dossier depuis une sauvegarde — voir « Sauvegarde, déplacement, autre machine »).

> 📁 **Où mettre le dossier ?** Évite `~/Documents`, le Bureau, `~/Téléchargements` et
> iCloud : macOS **protège** ces dossiers et demande une autorisation à chaque app qui y
> touche (voir §5). Un emplacement comme `~/SDLVC/` évite tout ça. Le dossier reste **d'un
> seul bloc** (app, venv, spectacles, config) — rien n'est éparpillé.

### 3. Installer — une seule commande

```bash
./scripts/setup_mac.sh
```

Elle (re)crée le `.venv`, installe les dépendances, crée `cadreur.toml` depuis l'exemple et
construit `Cadreur.app`. Elle est **rejouable** sans risque.

> 💡 Si tu tapes les commandes à la main plutôt que d'utiliser le script : en zsh il faut
> **quoter** `'.[gui]'` (les crochets sont interprétés comme un motif), et coller les
> commandes **ligne par ligne** (un bloc collé en une seule ligne échoue).

### 4. Configurer et restaurer

- `cadreur.toml` → `[telemetre] url` = adresse du Pi (ex. `http://192.168.0.51`),
  `[millumin] port` = 5000.
- **Restaurer les calibrations** : recopier tes fichiers `shows/*.json` de la sauvegarde
  dans le dossier `shows/`.

### 5. Autorisations macOS

- Au premier lancement, le **pare-feu** demande si Python peut recevoir des connexions
  entrantes → **Autoriser**.
- Si `cadreur_gui.log` affiche **« Operation not permitted »**, c'est la protection de
  confidentialité (TCC) : le dossier est dans une zone protégée. Deux solutions :
  1. **déplacer le dossier** hors de `~/Documents` / Bureau / Téléchargements (le plus
     simple et définitif), puis relancer `./scripts/setup_mac.sh` ;
  2. ou Réglages Système → Confidentialité et sécurité → **Accès complet au disque** →
     ajouter `Cadreur.app` **et** le Python du venv (son chemin réel :
     `readlink -f .venv/bin/python3`), car l'app est un script qui lance Python.
- Dans tous les cas : **quitter l'app (⌘Q) puis la relancer** — les autorisations ne
  s'appliquent qu'au démarrage du processus (pas besoin de se déconnecter/redémarrer).

### 6. Lancer

- **Double-clic sur `Cadreur.app`** (le garder dans le dossier du projet ; le glisser dans
  le Dock). Journal : `cadreur_gui.log`.
- Ou en Terminal : `./.venv/bin/python -m cadreur`, puis <http://127.0.0.1:8080>.

### 7. Côté Millumin

Apprendre les Interactions de chaque calque — voir la section « Côté Millumin » plus bas.

### 8. Accès à distance (pour le support)

Pour qu'on puisse diagnostiquer/déployer à distance depuis le poste de dev :

- Réglages Système → Général → Partage → **Session à distance** activée.
- Autoriser la clé publique du poste de dev (à coller tel quel) :

```zsh
mkdir -p ~/.ssh
a=AAAAC3NzaC1lZDI1NTE5AAAAIOmdSFEE
b=+7bE8wzDnxtFC9/6skAQxUbXIxIQAh83FGN7
echo >> ~/.ssh/authorized_keys
echo ssh-ed25519 $a$b telemetre-dev-win >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
tail -1 ~/.ssh/authorized_keys
```

La dernière ligne doit afficher la clé **sur une seule ligne**. Noter ensuite le nom
d'utilisateur (`whoami`) et l'adresse (`ipconfig getifaddr en0`).

### 9. Vérification finale

1. La **distance du Pi** défile en direct dans l'interface.
2. Un canal en **Mode calibration** : les curseurs bougent bien le calque dans Millumin.
3. **Afficher** révèle le bon calque.
4. **Capturer** ajoute un point ; **Enregistrer sous** crée le fichier spectacle.
5. **ARM** : le suivi se fait tout seul quand le rideau bouge.

## Lancer l'appli

- **Double-clic sur Cadreur.app** → fenêtre native. Fermer la fenêtre arrête le serveur.
  Journal : `cadreur_gui.log`.
- Ou en ligne de commande : `./.venv/bin/python -m cadreur`, puis navigateur sur
  <http://127.0.0.1:8080>.
- Au premier lancement, macOS demande d'autoriser Python à recevoir des connexions réseau →
  **Autoriser**.

## Configuration (`cadreur.toml`)

Tout a une valeur par défaut ; les clés utiles :

- `[telemetre] url` → l'IP du Pi (ex. `http://192.168.0.51`).
- `[millumin] host` / `port` → où Millumin écoute l'OSC (par défaut `127.0.0.1:5000`).
- `[web] host` / `port` → l'interface (par défaut `127.0.0.1:8080` ; mets `0.0.0.0` pour y
  accéder depuis une tablette/un autre poste du réseau).

## L'interface

- **En haut** : état du **Pi** (distance en direct), état **Millumin**, et le gros bouton
  **ARM**.
- **Distance** : distance absolue + position plateau (repère pour l'équipe) + barre de course
  avec les points de calibration.
- **Deux colonnes FACE / RÉTRO**, chacune avec **4 canaux** (un canal = un calque Millumin).
  Sur chaque carte de canal : un chevron **▾** pour **replier/déplier**, nom (modifiable),
  **actif**, **OSC…** (adresses), **🗑**, **Mode calibration**, **Afficher** (révèle le calque
  dans Millumin), **Capturer**, tableau de points, **trim**. En mode calibration, un bouton
  **Précision** apparaît près des curseurs. **+ canal** pour en ajouter.
- **Pastilles mémoires d'objectif** (M1/M2/M3) en tête de la colonne **FACE** (elles
  n'existent que pour la face).

## Côté Millumin (à faire une fois par calque)

Cadreur envoie sur `/front/{scale,positionH,positionV}/1..4` et `/retro/…/1..4`, en **0.0–1.0**.

1. Dans Millumin, **apprends les Interactions** de chaque calque : `scale`, `positionH`,
   `positionV`.
2. Règle le **transformer** : 0 = un extrême, **0.5 = centré**, 1 = l'autre extrême. Choisis
   la **plage en pixels** par axe — **plus petite en horizontal** pour un centrage plus fin.
3. Si une adresse doit changer, utilise le bouton **OSC…** du canal dans Cadreur (pas besoin
   de toucher au code).
4. Pour le bouton **« Afficher »**, apprends aussi une Interaction sur `/front|retro/layer/N`
   (même numéro que le canal) : Cadreur envoie **l'adresse seule, sans argument** (simple
   déclencheur) pour révéler le calque.

> ⚠️ L'OSC ne renvoie pas d'erreur : si un calque **ne bouge pas**, c'est que l'Interaction
> n'est pas apprise ou que l'adresse du canal ne correspond pas.

## Calibrer (méthode « piloter depuis Cadreur »)

1. (FACE) choisis la **mémoire d'objectif** en cours.
2. Sur les canaux à régler, active **Mode calibration** : les 3 curseurs (échelle, horizontal,
   vertical) **pilotent le calque en direct**. Tu peux en calibrer plusieurs à la fois.
3. À la **position actuelle du tulle**, règle l'image de chaque calque.
4. **Capturer** (un canal) ou **Capturer tous les canaux en calibration** (tout d'un coup à la
   distance courante).
5. **Déplace le tulle** (lointain → milieu → près) et recommence.
6. **Quitte** le mode calibration.

Ensuite **ARM** → Cadreur suit la distance et interpole entre tes points. **Désarmé = aucun
envoi**, les calques restent où ils sont.

Astuces :
- Bouton **« Afficher »** (sur chaque canal) : révèle ce calque dans Millumin — pratique pour
  piloter **depuis la scène** en manipulant le rideau, sans retourner en régie.
- Bouton **« Précision »** (près des curseurs, en mode calibration) : **zoome** chaque curseur
  sur une petite plage autour de sa valeur → glissement **~10× plus fin**. Relâcher recentre
  la plage ; désactive pour retrouver toute la plage 0–1.
- Les cartes de canal se **replient/déplient** (chevron ▾) pour masquer les calques sur
  lesquels tu ne travailles pas.
- Le mapping est **quasi linéaire** : 2 points aux extrémités suffisent presque, un 3ᵉ au
  milieu affine.
- Le **trim** = petite correction en direct par axe (les pas horizontal/vertical sont très
  fins) ; « figer » l'incorpore dans les points.
- La capture est désactivée si la distance est **figée** (Pi injoignable).

## Enregistrer / ouvrir un spectacle

- **Enregistrer sous** crée un fichier dans `shows/`. Ensuite : **auto-sauvegarde** +
  sauvegarde datée à chaque démarrage.
- **Tes calibrations = ces fichiers `shows/*.json`.** C'est le principal à sauvegarder.

## Sauvegarde, déplacement, autre machine

Le dossier peut être **déplacé ou copié** (ex. `~/Documents/SDLVC/`, ou une machine de
secours). La **seule** chose à refaire est le `.venv` : il contient des **chemins absolus**,
donc un `.venv` copié ne fonctionne pas ailleurs. Sur la nouvelle machine :

```bash
cd <nouveau-dossier>
./scripts/setup_mac.sh
```

Le script **supprime le `.venv` copié**, le recrée, réinstalle et reconstruit `Cadreur.app`.
Puis **re-glisse Cadreur.app dans le Dock** depuis le nouvel emplacement.

> ⚠️ N'exécute **pas** `./.venv/bin/pip` d'un `.venv` copié : son interpréteur pointe encore
> vers l'ancienne machine (erreur `bad interpreter: .../python3.x: no such file or
> directory`). Il faut d'abord le supprimer — c'est ce que fait le script.

À vraiment sauvegarder : **`shows/` + `cadreur.toml`**. Sur une machine neuve on peut aussi
récupérer le code avec `git clone` (dépôt GitHub) puis recopier `shows/` + `cadreur.toml`.
(Détails : section « Backup and moving the folder » du README.)

## Si le télémètre a été bougé ou déréglé

Les calibrations sont enregistrées en **distance absolue** (ex. 0,45 m /
4,12 m / 9,95 m). Le calcul est immunisé contre **Set Zero**, **Clear Zero** et
**Inverser le sens** côté Pi — ça, c'est du logiciel, et c'est prévu.

Mais si le **boîtier a physiquement bougé** (quelqu'un l'a heurté, il a été
démonté puis remonté), la même position de tulle ne donne plus la même
distance. Toutes les calibrations sont alors **décalées d'autant**, et l'image
tombe à côté.

**Comment le vérifier** : amener le tulle sur un repère connu et comparer la
distance affichée à celle du point de calibration correspondant. Un écart
constant sur tous les repères = le boîtier a bougé de cette valeur.

**Deux réparations possibles :**

1. **Le remettre exactement où il était** — le mieux, rien d'autre à refaire.
2. **Recapturer** — si la position d'origine est perdue. Ce n'est pas si long :
   le mapping est quasi linéaire, donc **2 points aux extrémités** par canal
   suffisent presque, un 3ᵉ au milieu affine. Passer les canaux en **Mode
   calibration**, régler à chaque position, **Capturer tous les canaux**.

> 💡 Une fois le boîtier définitivement fixé, noter/marquer sa position : en
> tournée il sera démonté et remonté à chaque lieu, et c'est ce repère qui
> évite de recalibrer à chaque fois.

## Dépannage rapide

| Symptôme | Cause probable / solution |
|---|---|
| « Pi hors ligne / figé » | Vérifier `[telemetre] url` et que le Pi (`192.168.0.51`) répond. L'appli garde la dernière valeur pendant la coupure. |
| Un calque ne bouge pas | Interaction non apprise dans Millumin, ou adresse OSC du canal fausse (bouton **OSC…**). |
| Rien ne défile / affichage figé | Recharger la page (**Cmd+R**) ou relancer l'app. |
| Cadreur.app ne démarre pas | Le `.venv` manque (dossier déplacé / copié) → `./scripts/setup_mac.sh`. |
| `bad interpreter: .../python3.x: no such file or directory` | `.venv` copié depuis une autre machine/dossier → `./scripts/setup_mac.sh` (il le supprime et le recrée). |
| `zsh: no matches found: .[gui]` | Les crochets doivent être quotés : `'.[gui]'` — ou lance simplement `./scripts/setup_mac.sh`. |
| macOS bloque le réseau | Autoriser Python à recevoir les connexions entrantes. |
| `Operation not permitted` dans `cadreur_gui.log` | Dossier dans une zone protégée (Documents / Bureau / Téléchargements / iCloud). Déplacer le dossier ailleurs (ex. `~/SDLVC/`) **ou** donner l'Accès complet au disque à `Cadreur.app` *et* au Python du venv (`readlink -f .venv/bin/python3`), puis **quitter (⌘Q) et relancer**. |

## La nouvelle application (version Rust) — pas encore déployée

> ⚠️ **Rien à faire pour le spectacle en cours.** L'application Python décrite
> ci-dessus reste celle qui tourne. Cette section décrit la version qui la
> remplacera, une fois validée en conditions réelles.

### Ce qui change pour toi

- **Un seul fichier à copier.** `Cadreur.app` contient tout : plus de dossier de
  projet à garder à côté, plus de `.venv`, plus de Python à installer. 1,5 Mo
  au lieu de 208 Mo.
- **Rien à installer.** Tu copies l'app où tu veux (Applications, une clé USB,
  le Bureau) et tu double-cliques.
- **Signée et notariée par Apple.** macOS ne la bloque plus, même reçue par
  AirDrop ou téléchargée. Le ticket est *agrafé* à l'application : elle
  s'ouvre même **sans internet**, ce qui est le cas au théâtre.
- **L'interface est exactement la même.** Le code de l'interface est
  littéralement le même fichier que dans la version Python — pas une copie.

### Où sont tes données

Elles ne sont **plus dans le dossier du projet** mais dans :

```
~/Library/Application Support/Cadreur/
    cadreur.toml      réglages (adresse du Pi, port…)
    shows/            TES CALIBRATIONS — c'est ça qu'on sauvegarde
```

Le journal est dans `~/Library/Logs/Cadreur/cadreur.log`.

C'est un emplacement **non synchronisé par iCloud** et **non protégé** par les
autorisations de confidentialité macOS. Les trois pannes d'environnement
possibles (fichiers évincés par iCloud, dossier protégé, mauvaise copie du
projet) ne peuvent plus se produire. Et remplacer l'application ne touche
jamais tes spectacles.

### Autorisations macOS au premier lancement

Au tout premier démarrage, macOS pose **une** question :

> « Cadreur » souhaite accéder aux appareils de votre réseau local.

**Répondre « Autoriser ».** C'est indispensable : c'est par le réseau local que
Cadreur lit la distance mesurée par le boîtier télémètre.

> ⚠️ macOS ne pose cette question **qu'une seule fois**. Si elle est refusée ou
> fermée par erreur, le télémètre devient injoignable pour toujours et
> l'interface affiche seulement « Pi hors ligne » — impossible de deviner que
> c'est une autorisation. Cadreur détecte ce cas au premier lancement et
> affiche le chemin exact à suivre.

Pour corriger après coup :
**Réglages Système → Confidentialité et sécurité → Réseau local → activer Cadreur**,
puis **quitter Cadreur (⌘Q) et le relancer** (les autorisations ne s'appliquent
qu'au démarrage du processus).

Une seconde question apparaît **uniquement** si tu mets `[web] host = "0.0.0.0"`
dans `cadreur.toml` pour piloter depuis une tablette : le pare-feu demande
d'accepter les connexions entrantes → **Autoriser**.

Avec les réglages par défaut, il n'y a **rien d'autre** à autoriser : Millumin
est joint en local (127.0.0.1), et macOS n'exige aucune autorisation pour ça.

### ⚠️ Au premier lancement, l'app est VIDE — c'est normal

La nouvelle app démarre avec un dossier de données neuf : **aucun spectacle**,
des canaux aux noms d'usine (« Face 1 », « Lointain 1 »), aucun point de
calibration.

C'est **exactement l'aspect** qu'avait la panne de septembre. Ne pas en
conclure que l'app est cassée : elle n'a simplement pas encore tes fichiers.
Commence par la section suivante, **puis** juge.

### Reprendre les spectacles de l'installation actuelle

Deux possibilités :

1. Copier tes `shows/*.json` dans `~/Library/Application Support/Cadreur/shows/`.
2. Ou pointer l'app sur le dossier existant, sans rien déplacer :

```bash
CADREUR_DATA_DIR=~/SDLVC/TelemetreBergman /Applications/Cadreur.app/Contents/MacOS/cadreur
```

### Pour le développeur

```bash
cd cadreur-rs && cargo test        # 100 tests + les tests de parité
./scripts/build_app.sh             # construit et signe dist/Cadreur.app
./scripts/build_app.sh --notarize  # ... puis notarise et agrafe le ticket
```

Le port est vérifié contre l'implémentation Python : les instantanés `/stream`
et les 37 cas d'API (chemins d'erreur compris) donnent des réponses
identiques, et 400 cas d'interpolation tirés au hasard sont comparés aux
sorties produites par `src/cadreur/interp.py` lui-même.

## Pour aller plus loin

- Spécification technique complète (anglais) : `documentation/PRD-cadreur.md`.
- Appli du Pi (télémètre, câblage, dépannage réseau) : `README.md`.
