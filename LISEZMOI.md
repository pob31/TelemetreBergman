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

## Installation (une fois, sur le Mac)

Il faut Python ≥ 3.11 (l'installeur de <https://www.python.org/downloads/> convient).
Dans le dossier du projet :

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e '.[gui]'
cp cadreur.example.toml cadreur.toml   # règle l'IP du Pi et les ports
./scripts/make_app.sh                  # crée Cadreur.app (double-cliquable)
```

Puis glisse **Cadreur.app** dans le Dock. (Il faut internet une seule fois, pour le `pip install`.)

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
  Sur chaque carte de canal : nom (modifiable), **actif**, **OSC…** (adresses), **🗑**,
  **Mode calibration**, **Capturer**, tableau de points, **trim**. **+ canal** pour en ajouter.
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

Le dossier peut être **déplacé ou copié** (ex. `~/Documents/SDLVC/`). La **seule** chose à
refaire est le `.venv` (il contient des chemins absolus) :

```bash
cd <nouveau-dossier>
rm -rf .venv
python3 -m venv .venv && ./.venv/bin/pip install -e '.[gui]'
./scripts/make_app.sh          # reconstruit Cadreur.app
```

Puis **re-glisse Cadreur.app dans le Dock** depuis le nouvel emplacement. À vraiment
sauvegarder : **`shows/` + `cadreur.toml`**. Sur une machine neuve, on peut aussi récupérer
le code avec `git clone` (dépôt GitHub) puis recopier `shows/` + `cadreur.toml`.
(Détails : section « Backup and moving the folder » du README.)

## Dépannage rapide

| Symptôme | Cause probable / solution |
|---|---|
| « Pi hors ligne / figé » | Vérifier `[telemetre] url` et que le Pi (`192.168.0.51`) répond. L'appli garde la dernière valeur pendant la coupure. |
| Un calque ne bouge pas | Interaction non apprise dans Millumin, ou adresse OSC du canal fausse (bouton **OSC…**). |
| Rien ne défile / affichage figé | Recharger la page (**Cmd+R**) ou relancer l'app. |
| Cadreur.app ne démarre pas | Le `.venv` manque (dossier déplacé / copié) → refaire l'install ci-dessus. |
| macOS bloque le réseau | Autoriser Python à recevoir les connexions entrantes. |

## Pour aller plus loin

- Spécification technique complète (anglais) : `documentation/PRD-cadreur.md`.
- Appli du Pi (télémètre, câblage, dépannage réseau) : `README.md`.
