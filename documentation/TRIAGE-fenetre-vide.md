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

## Hypothèse principale : aucun spectacle n'est chargé

La fenêtre s'affiche, mais **vide de contenu**. Au démarrage (`src/cadreur/app.py`,
`_load_startup_show`), Cadreur rouvre le dernier spectacle utilisé — et **les deux chemins
d'échec sont volontairement silencieux** pour que le serveur démarre toujours :

1. **`cadreur_state.json` tronqué ou perdu** → Cadreur a oublié quel spectacle ouvrir.
   `src/cadreur/state.py` (`load_last_show_path`) rattrape *toute* erreur et renvoie `None`.
   Souvent **aucune trace dans le journal**. Les calibrations, elles, sont intactes.
2. **Le fichier `shows/<spectacle>.json` lui-même tronqué** → `load_show` lève une erreur,
   rattrapée et journalisée : `Could not load last show ...` dans `cadreur_gui.log`.

Les deux viennent d'un **arrêt brutal** (machine rangée alors que l'app tournait, ou
batterie à plat pendant une écriture). Les deux **survivent aux redémarrages**, puisque le
dégât est sur le disque — ce qui explique que rebooter ne change rien.

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
| L'interface s'affiche, mais **aucun canal**, aucune distance | Hypothèse principale : pas de spectacle chargé | **Ouvrir le spectacle** depuis l'interface. Si le fichier est refusé, restaurer le plus récent fichier sain de `shows/backups/`. |
| Fenêtre **totalement blanche/noire**, aucun élément | Le serveur n'a pas démarré, ou un autre logiciel occupe le port | Voir les sections `PORT` et `JOURNAL` de la sortie du script (ci-dessous). |
| Journal : `Server already running on :8080 — opening a window on it` | **Un autre programme occupe le port 8080.** `src/cadreur/gui.py` vérifie seulement que *quelque chose* répond, pas que c'est Cadreur : il n'a donc pas démarré son serveur et affiche la page d'un inconnu. | Changer le port dans `cadreur.toml` : `[web] port = 8090`, puis relancer. |
| Journal : `Could not load last show ...` | Fichier spectacle abîmé | Restaurer depuis `shows/backups/` (le script marque chaque fichier `OK` ou `ABIME`). |
| Journal : `Operation not permitted` | Dossier dans une zone protégée par macOS | Voir §5 de `LISEZMOI.md`. |

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
