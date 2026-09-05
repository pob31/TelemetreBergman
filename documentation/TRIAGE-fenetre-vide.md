# Cadreur — « fenêtre vide » (incident ouvert, septembre 2026)

**Symptôme rapporté.** Le Mac de régie est resté rangé un mois. Au retour, `Cadreur.app`
se lance et **ouvre une fenêtre vide**. Plusieurs redémarrages du Mac n'y changent rien.
Rien n'a été modifié sur la machine depuis juillet.

## Ce que ce n'est pas

- **Ni signature ni notarisation expirée.** Rien n'est compilé ici : `Cadreur.app` est un
  script bash de 12 lignes généré sur place par `scripts/make_app.sh`, qui lance
  `.venv/bin/cadreur-gui`. Python compile en bytecode à l'exécution. Aucun élément de la
  chaîne n'a de date de validité, et une signature macOS n'expire pas sur un logiciel déjà
  installé — ça marche à la première ouverture ou jamais.
- **Ni le temps de stockage en lui-même.** Le serveur a été relancé depuis le dépôt à la
  date du 5 septembre 2026 : il démarre proprement et sert l'interface (`200 OK`).

## Pour une session Claude locale sur le Mac de régie

Tu reprends un diagnostic déjà entamé à distance. Lis cette section avant d'agir.

**Déjà écarté — ne pas refaire :**

- Signature / notarisation expirée : impossible, rien n'est compilé (voir ci-dessus).
- Bug lié à la date : le serveur a été relancé depuis ce dépôt le 2026-09-05, il démarre
  proprement et sert l'interface (`200 OK`, `/api/health` répond).
- Le `.venv` : ce n'est pas la piste. Une fenêtre s'ouvre, donc Python tourne.

**Protocole, dans cet ordre :**

1. Mettre `shows/` à l'abri (section suivante). **Avant tout le reste.**
2. Poser à Stefo la question qui tranche : la fenêtre est-elle **totalement vide**, ou
   est-ce **l'interface normale** (bandeau, bouton ARM) **sans canaux ni distance** ?
3. Lancer `./scripts/diagnose_mac.sh` (lecture seule, ne lance pas Cadreur).
4. Lire l'arbre de décision ci-dessous.
5. **S'arrêter là et rendre compte.** Coller la sortie complète du script.

**Limites à respecter sur cette machine :**

- **Ne modifie pas `src/`.** Le poste doit rester identique à la version de juillet tant
  que la cause n'est pas confirmée. Les correctifs sont listés en fin de document et se
  font ailleurs, plus tard.
- **Ne relance pas l'app en boucle** pour reproduire : chaque démarrage fait tourner les
  sauvegardes et en supprime une bonne.
- **Ne « répare » aucun JSON à la main.** La seule réparation autorisée est de rouvrir le
  spectacle depuis l'interface, ou de restaurer un fichier marqué `OK` depuis
  `shows/backups/` — et seulement une fois la copie de sauvegarde faite.
- Si la distance ne défile pas, ce n'est pas forcément Cadreur : vérifier d'abord que le
  Pi répond à l'adresse de `[telemetre] url`. Un test bout-en-bout suppose aussi que
  **Millumin tourne** avec le bon projet.
- **Parle à Stefo en français.** C'est un technicien vidéo, pas un développeur : donne des
  commandes à copier-coller, pas des explications de code.

**« Réparé » veut dire :** le spectacle se rouvre, la distance du Pi défile en direct, et
en ARM les calques suivent le tulle dans Millumin.

## Mesuré en labo le 2026-09-05 — ce que chaque panne produit vraiment

Les quatre pannes « côté données » ont été **rejouées** sur une copie isolée du dépôt, avec
le vrai serveur. Résultat : **aucune ne donne une fenêtre blanche.** Toutes donnent une
interface qui s'affiche normalement, avec des canaux **aux noms d'usine** et aucun point de
calibration ; le serveur répond `{"status":"ok"}` dans les quatre cas.

| Cas rejoué | Ce qu'on lit dans le journal | Interface |
|---|---|---|
| `cadreur_state.json` absent | **rien du tout** | canaux « Face 1 »… |
| spectacle disparu | `Could not load last show … : Show file not found: X.json` | idem |
| spectacle tronqué | `Could not load last show … : Unreadable show file X.json: Expecting …` | idem |
| lecture refusée (TCC, ou fichier évincé par iCloud) | `Startup backup failed … [Errno 13] Permission denied` **puis** `Could not load last show …` | idem |

Autrement dit : un spectacle perdu, tronqué ou illisible **ne peut pas** produire une
fenêtre blanche. Il produit une interface complète mais **remise à zéro**.

**Le signe qui ne trompe pas :** si les canaux s'appellent « Face 1 / Face 2 / Face 3 » et
« Lointain 1… » — les noms d'usine — le spectacle n'est pas chargé. Si Stefo avait renommé ses
canaux, leurs vrais noms doivent réapparaître une fois le bon spectacle ouvert.

## Donc : que veut dire exactement « fenêtre vide » ?

C'est la question à poser en premier, elle sépare deux familles de causes sans recouvrement.

- **L'interface s'affiche, canaux aux noms d'usine, aucun point de calibration** → côté
  **données**. Le spectacle n'est pas chargé. Rien n'est perdu : le rouvrir depuis
  l'interface (ou vérifier qu'on est bien dans le bon dossier — voir plus bas).
- **Fenêtre réellement blanche ou sombre, aucun élément d'interface** → côté **serveur**.
  Le serveur n'a pas démarré, ou `src/cadreur/gui.py` a trouvé le port 8080 déjà occupé par
  un autre logiciel et a ouvert la fenêtre sur *sa* page à lui. C'est la piste n°1 pour une
  fenêtre vraiment vide, et le journal tranche immédiatement.

## ⚠️ Avant toute chose : mettre les calibrations à l'abri

Les `shows/*.json` sont des heures de calibration sur le plateau. Ils n'existent nulle part
ailleurs. **Ne pas relancer l'app pour « voir ».**

```bash
cd <dossier du projet>
cp -R shows ~/Desktop/cadreur-shows-secours
```

Pourquoi c'est urgent : `show.startup_backup()` (`src/cadreur/show.py`) tourne à **chaque**
démarrage, copie le fichier courant dans `shows/backups/` et **ne garde que les 10 plus
récentes**. Si le fichier courant est abîmé, chaque relance pousse une bonne sauvegarde
dehors. Le filet de sécurité se vide à chaque essai.

## Diagnostic — une seule commande, en lecture seule

```bash
./scripts/diagnose_mac.sh
```

Elle ne lance pas Cadreur et n'écrit rien. Copier tout l'affichage et l'envoyer au support.

## L'arbre de décision

La question qui tranche : **la fenêtre est-elle totalement vide, ou est-ce l'interface
normale (bandeau du haut, gros bouton ARM) mais sans canaux ni distance ?**

| Observation | Diagnostic | Suite |
|---|---|---|
| L'interface s'affiche, canaux aux **noms d'usine** (« Face 1 »…), aucun point | Le spectacle n'est pas chargé (mesuré : c'est bien ce que ça donne) | **Ouvrir le spectacle** depuis l'interface. Si le fichier est refusé, restaurer le plus récent fichier sain de `shows/backups/`. |
| Fenêtre **totalement blanche/noire**, aucun élément | Le serveur n'a pas démarré, ou un autre logiciel occupe le port | Voir les sections `PORT` et `JOURNAL` de la sortie du script (ci-dessous). |
| Journal : `Server already running on :8080 — opening a window on it` | **Un autre programme occupe le port 8080.** `src/cadreur/gui.py` vérifie seulement que *quelque chose* répond, pas que c'est Cadreur : il n'a donc pas démarré son serveur et affiche la page d'un inconnu. | Changer le port dans `cadreur.toml` : `[web] port = 8090`, puis relancer. |
| Journal : `Could not load last show ...` | Fichier spectacle abîmé | Restaurer depuis `shows/backups/` (le script marque chaque fichier `OK` ou `ABIME`). |
| Le script liste **une autre copie du projet** contenant des spectacles | Ce n'est pas le bon dossier qui est ouvert (deuxième copie, autre compte) | Lancer `Cadreur.app` **depuis le dossier qui contient les spectacles**. Rien n'est perdu. |
| Le script marque des fichiers **EVINCE** (iCloud) | Fichiers évincés par « Optimiser le stockage » après un mois sans usage ; sans internet au théâtre ils ne peuvent pas être retéléchargés | Rebrancher internet le temps que les fichiers redescendent, puis **déplacer le dossier hors de `~/Documents`** (LISEZMOI §2). |
| `session ouverte par` ≠ `proprietaire du dossier` | Session macOS ouverte sur un autre compte : autorisations de confidentialité et Dock sont propres à chaque compte | Se reconnecter sur le compte habituel de la régie. |
| Journal : `Operation not permitted` | Dossier dans une zone protégée par macOS | Voir §5 de `LISEZMOI.md`. |

## Deux causes d'environnement à écarter avant de suspecter le code

Aucune des deux ne « périme » quoi que ce soit, mais toutes deux se déclenchent après une
longue période sans usage — ce qui colle au symptôme.

- **Éviction iCloud.** Le dossier est dans `~/Documents`, zone synchronisée. Avec
  « Optimiser le stockage du Mac », macOS libère les fichiers **non utilisés depuis
  longtemps** : un mois au placard est exactement le critère. Le fichier reste visible avec
  sa taille, mais il n'y a plus rien sur le disque, et **sans internet au théâtre il ne peut
  pas redescendre**. `load_show` échoue, l'erreur est rattrapée, l'interface s'ouvre vide.
  Le script détecte ce cas (taille non nulle, 0 bloc sur disque).
- **Mauvaise copie / mauvais compte.** S'il existe deux copies du projet sur la machine,
  celle qui s'ouvre peut être celle **sans** `shows/`. Les autorisations de confidentialité
  et le Dock étant propres à chaque compte macOS, une session ouverte sur un autre compte
  donne exactement ce résultat. Le script liste toutes les copies et le nombre de
  spectacles dans chacune.

Dans les deux cas **les calibrations sont intactes** — elles sont simplement ailleurs, ou
momentanément pas sur le disque.

## Récupération

Dans le cas le plus probable, **rien n'est perdu** : l'app a seulement oublié quel
spectacle ouvrir. Il suffit de le **rouvrir depuis l'interface** et le suivi repart.

Si un fichier est réellement abîmé : prendre le plus récent fichier marqué `OK` dans
`shows/backups/`, le copier sous le nom attendu dans `shows/`, puis rouvrir. Ne jamais
tenter de « réparer » un JSON à la main tant qu'une copie de sauvegarde n'est pas ailleurs.

## À corriger dans le code, une fois la cause connue

Cet incident est illisible à distance parce que l'app présente un échec comme une interface
vide. Trois correctifs, à faire **après** le diagnostic (ne rien changer sur la machine du
spectacle avant) :

- `src/cadreur/app.py` / `state.py` : afficher dans l'interface « aucun spectacle chargé »
  et « fichier spectacle illisible — restaurer depuis shows/backups/ », au lieu d'une simple
  ligne de journal.
- `src/cadreur/show.py` : ne pas faire tourner les sauvegardes quand le fichier copié n'est
  pas un JSON valide, pour qu'un fichier abîmé ne chasse pas les bons.
- `src/cadreur/gui.py` : vérifier `/api/health` avant d'adopter un port déjà occupé, et
  échouer visiblement si ce n'est pas Cadreur qui répond.
