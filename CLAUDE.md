# TelemetreBergman — notes for Claude

Two apps in one repo, for a theatre show (Bergman, SDLVC).

- `src/telemetre/` — runs on a **Raspberry Pi**. Reads a TF02-Pro LiDAR over serial,
  serves the scrim distance as SSE + OSC. Deployed via `scripts/install.sh` / `systemd/`.
- `src/cadreur/` — runs on the **régie Mac**. Reads that distance and drives Millumin
  layer scale/position over OSC so the projection stays locked to the moving scrim.
  `Cadreur.app` is a bash-script bundle generated locally by `scripts/make_app.sh`;
  it just execs `.venv/bin/cadreur-gui`. **Nothing is compiled, signed or notarized.**

Docs: `LISEZMOI.md` (FR, the operator runbook), `README.md` (EN, the Pi),
`documentation/PRD-cadreur.md` (EN, the spec).

Operators are French-speaking video technicians, not developers. Anything written for
them goes in French, in `LISEZMOI.md`.

## If you are running on the venue Mac (régie)

There is an open incident. Read `documentation/TRIAGE-fenetre-vide.md` first, and
start at its section **« Pour une session Claude locale sur le Mac de régie »** — it
lists what has already been ruled out, the order to work in, and where to stop.

Measured 2026-09-05: a lost or damaged show file does **not** blank the window — it
renders the normal UI with factory channel names and no calibration points. A genuinely
blank window means the server never started. Ask which one it is before theorising.

Short version: secure `shows/` first, diagnose read-only, then **report back rather
than fix**. Do not change `src/` on this machine while the cause is unconfirmed —
the point is that the running system stays identical to the one that worked in July.

**The irreplaceable asset is `shows/*.json`.** Those are hand-made calibrations, hours
of work on stage with the scrim, and they exist nowhere else. Everything else in this
folder is rebuildable in one command (`./scripts/setup_mac.sh`).

Before anything else: `cp -R shows ~/Desktop/cadreur-shows-secours`

Then:

- **Do not launch Cadreur repeatedly to reproduce a fault.** Every startup runs
  `show.startup_backup()` (`src/cadreur/show.py`), which copies the current show file
  into `shows/backups/` and prunes to the 10 newest. If the current file is damaged, a
  debugging loop overwrites the good backups with bad ones. Diagnose from the log and
  from `./scripts/diagnose_mac.sh` (read-only) instead.
- **Never write to `shows/*.json` or `shows/backups/`** to "repair" them. Restore by
  copying a known-good backup, and only after the operator has a copy elsewhere.
- **Do not rebuild `.venv` or run `setup_mac.sh` as a first move.** It is rarely the
  cause and it destroys the evidence. Confirm the failure first.
- **Do not change the show's running configuration** (`cadreur.toml`, OSC addresses)
  without the operator — Millumin Interactions are learned against those addresses.

`shows/`, `cadreur.toml`, `cadreur_state.json`, `cadreur_gui.log`, `.venv/` and
`Cadreur.app/` are all gitignored, so `git pull` is safe mid-incident: it cannot touch
the calibrations or the environment.

## Conventions

- Python ≥3.11, stdlib-first; FastAPI + uvicorn, `python-osc`, `pywebview` for the window.
- The venv is repo-local and holds absolute paths — a copied `.venv` is always broken;
  `scripts/setup_mac.sh` deletes and rebuilds it.
- Tests: `./.venv/bin/python -m pytest`.
