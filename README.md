# Telemetre Bergman

A LiDAR **cart-position readout** for the Bergman stage. A Benewake **TF02-Pro**
rangefinder on a flybar measures the distance to a light-curtain cart; the Pi
turns that into a live position and serves it to any phone/tablet on the network
so the stage manager can nudge the cart onto a mark in real time.

- Live readout in **meters**, updated ~20×/s over Server-Sent Events.
- **Set Zero** (tare) to read movement relative to a reference mark; raw distance
  shown alongside. Direction is invertible.
- Runs headless, **auto-starts at boot**, and can be **safely powered off/rebooted
  from the web page** (no unplugging).
- **OSC output** (disabled by default): streams the filtered position as
  `/telemetre/position` (float, meters) to **one or more UDP destinations** —
  e.g. the Millumin Mac and a backup machine.

## Companion app — Cadreur

**Cadreur Bergman** is the Mac-side companion (in `src/cadreur/`): it consumes
this readout's SSE stream and continuously rescales/repositions **Millumin**
layers so front- and rear-projected video stays fitted to the travelling scrim
(4+4 continuously-driven channels per beamer, front lens memories, drive-from-
Cadreur calibration). Spec: [`documentation/PRD-cadreur.md`](documentation/PRD-cadreur.md).
**Guide d'exploitation en français : [`LISEZMOI.md`](LISEZMOI.md).**

Run on the show Mac (Python ≥ 3.11):

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
cp cadreur.example.toml cadreur.toml   # set the Pi's URL, ports
.venv/bin/python -m cadreur            # UI on http://127.0.0.1:8080
```

Prefer a desktop window over a browser tab? Install the gui extra and use
`cadreur-gui` — same server, same UI, in a native macOS window that shuts the
server down when closed (a browser/tablet can still connect alongside it):

```bash
.venv/bin/pip install -e '.[gui]'      # adds pywebview
.venv/bin/cadreur-gui
```

No command line at show time: `./scripts/make_app.sh` builds a double-clickable
**Cadreur.app** at the repo root (keep it there; drag it to the Dock). It
launches `cadreur-gui` and logs to `cadreur_gui.log`. Python: any 3.11+ — the
plain macOS installer from <https://www.python.org/downloads/> is fine
(`python3.13` afterwards); needs internet once for the `pip install`.

### Backup and moving the folder

The code is **location-independent** (paths are resolved at runtime), so the
folder can be moved or copied anywhere — e.g. `~/Documents/SDLVC/TelemetreBergman`
— or to a backup machine. Only one thing does **not** survive a move or a copy:
**`.venv/`** (it holds absolute paths). Recreate it in place:

```bash
cd <new-folder>
rm -rf .venv
python3 -m venv .venv && ./.venv/bin/pip install -e '.[gui]'
./scripts/make_app.sh          # rebuild Cadreur.app with clean paths
```

Then re-drag `Cadreur.app` to the Dock from the new location (the old Dock
reference breaks), and either delete `cadreur_state.json` or just **Load** your
show once after launch — that file remembers the last show's *absolute* path.

What travels unchanged — and is what you actually need to back up — is
**`shows/*.json`** (your calibrations) and **`cadreur.toml`** (this machine's Pi
URL / ports); nothing in them is edited on a move. For a cold backup, copy the
folder while **excluding** `.venv/`, `__pycache__/`, and `*.log`. Since the code
lives on GitHub, a fresh machine can instead `git clone` the repo, recreate the
venv, and just copy over `shows/` + `cadreur.toml`.

Dev loop without the rig: `scripts/sim_telemetre.py` (fake Pi SSE) +
`scripts/millumin_sim.py` (fake Millumin OSC, prints all traffic).

---

## Hardware

| Part | Detail |
|------|--------|
| Computer | Raspberry Pi 5, Raspberry Pi OS 13 "Trixie" (64-bit) |
| UART HAT | Waveshare **Serial Expansion HAT** (SC16IS752 over **I²C** @ `0x48`) → `/dev/ttySC0` (ch A), `/dev/ttySC1` (ch B) |
| Sensor | Benewake TF02-Pro on **channel B → `/dev/ttySC1`**, 115200 8N1, 9-byte frames, distance in cm |

> **Note — I²C board, not SPI.** This uses the *I²C* Serial Expansion HAT. Load
> **only** the I²C overlay (`sc16is752-i2c`); do **not** load the SPI overlay
> (`sc16is75x-spi`). On this board the SPI driver registers a *phantom* `ttySC0`
> whose `close()` dead-locks the kernel in `synchronize_irq` — an unkillable
> D-state reader thread that only a reboot clears. That overlay mismatch was the
> original "no data / hangs constantly" bug. See Troubleshooting.

### Wiring (learned the hard way)
The TF02-Pro's TX/RX labels are from the **sensor's** perspective, and the HAT's
`TXD/RXD` pads are from the **HAT's**. The rule that matters: sensor TX (green)
must reach the HAT's **`RXD`** pad ("the HAT receives here").

| TF02-Pro wire | Connect to HAT channel B |
|---------------|---------------------------|
| **Green (TX)** | **`RXD`** of channel B ← the critical one |
| White (RX) | `TXD` of channel B |
| Red (VCC) | `5V` |
| Black (GND) | `GND` |

Data lines are 3.3 V on both sides — no level shifter. The TF02-Pro has **no
reverse-polarity protection**, so double-check Red=5 V / Black=GND. The HAT itself
talks to the Pi over I²C (GPIO2/SDA, GPIO3/SCL) with INT on GPIO24 — all fixed by
the HAT, nothing to wire there.

### Enable the HAT (two lines in `/boot/firmware/config.txt`, then reboot)
```
dtparam=i2c_arm=on
dtoverlay=sc16is752-i2c,int_pin=24,addr=0x48
```
`scripts/install.sh` adds these for you. `addr=0x48` is the HAT's default (A0/A1
tied high); `int_pin=24` is its INT on GPIO24. Confirm after reboot with
`ls /dev/ttySC*` and `sudo i2cdetect -y 1` (shows `UU` at `48`). Do **not** add
the SPI overlay `sc16is75x-spi` — see Troubleshooting for why.

---

## Install (on the Pi)
```bash
git clone <repo-url> ~/TelemetreBergman && cd ~/TelemetreBergman
./scripts/install.sh          # overlay, venv+deps, config.toml, sudoers, systemd
# reboot once if it reports the overlay was newly added
```
Then browse to **http://192.168.0.51/** (or the Pi's IP). Give the Pi a DHCP
reservation so that address is stable.

## Configure
Edit `config.toml` (created from `config.example.toml`). Every key has a built-in
default. Notable: `[serial] port` (defaults to `/dev/ttySC1`, the sensor's HAT
channel; `""` auto-detects ttySC1/ttySC0/…), `[filter]` smoothing,
`[position] sign`, `[web] port`, and the `[osc]` block.

## Use
- **Set Zero** — marks the cart's current spot as `0.00 m`; the big number then
  shows movement from there. **Clear Zero** reverts to absolute-from-sensor.
- **Invert Direction** — flips which way movement counts.
- **Reboot / Power Off** — confirm dialog, then the Pi reboots (page reconnects
  itself) or powers down safely.

---

## Service management
```bash
sudo systemctl status telemetre     # state
journalctl -u telemetre -f          # live logs
sudo systemctl restart telemetre
```

## Deploy an update
```bash
# from the dev machine
ssh bergman@192.168.0.51 "~/TelemetreBergman/scripts/deploy.sh"   # git pull + restart
```

## Development / tests
Pure logic (frame parsing, filters) is dependency-free and tested off-hardware:
```bash
python -m unittest discover -s tests -v
```
Find which port the sensor is on (scans ttySC1/ttySC0/… — safe with the I²C
overlay loaded):
```bash
.venv/bin/python scripts/detect_serial.py              # scan candidates
.venv/bin/python scripts/detect_serial.py /dev/ttySC1  # test one port
```

## Layout
```
src/telemetre/   frames.py filters.py serial_reader.py state.py osc_out.py config.py app.py
src/cadreur/     Mac companion (Millumin scrim tracker): engine.py millumin.py show.py … + web/
web/             index.html app.js style.css   (EventSource UI)
systemd/         telemetre.service
scripts/         install.sh deploy.sh detect_serial.py net_sniff.py
                 cadreur: make_app.sh sim_telemetre.py millumin_sim.py osc_test.py drive_demo.py analyze_points.py
tests/           test_frames.py test_filters.py test_interp.py test_smoothing.py test_show.py test_engine.py
shows/           cadreur show files (gitignored except example-show.json)
documentation/   TF02-Pro datasheets/manual · PRD-cadreur.md (Mac companion app spec)
```

## Troubleshooting — "no data"
1. `sudo i2cdetect -y 1` shows the chip at **`48`** (`UU` = driver bound)? If not,
   check `dtparam=i2c_arm=on` + the `sc16is752-i2c` overlay, then reboot.
2. `dmesg | grep sc16is` should show `1-0048: ttySC0/ttySC1 … is a SC16IS752`, and
   `ls /dev/ttySC*` should exist. Sensor is on **channel B → `/dev/ttySC1`**.
3. **Zero bytes / `connected:false`** → almost always Rx/Tx: **green (sensor TX)
   must land on the channel's `RXD` pad** (not `TXD`), on the channel you're reading.
4. INT is GPIO24 (`int_pin=24`); `grep 186: /proc/interrupts` should show label
   `1-0048` with a count that climbs when data flows.
5. **Reader wedged / web stuck "connecting", `ps` shows python in `D` state?**
   That's the phantom-port dead-lock — the **SPI** overlay (`sc16is75x-spi`) is
   loaded against this **I²C** board. Disable it, keep only `sc16is752-i2c`, reboot.

---

## Troubleshooting — one device can't load the page (but others can)

**Symptom.** One phone suddenly cannot load the readout while other devices can.
Every browser on it fails. Rebooting the phone, the router **and the Pi** changes
nothing. It still reaches the router's own admin page.

### The usual cause: the phone is on Wi-Fi *and* mobile data

The stage network has **no internet**. Phones notice ("Wi-Fi connected, no
internet"), quietly demote it and make **mobile data the default network** — so
requests for the Pi's private address (e.g. `172.22.0.254`) leave over 5G,
toward the internet, where that address goes nowhere. The first load often
succeeds; later ones don't. It looks exactly as if the Pi were blocking you.

Tell-tales that this is what you're hitting:
- turning **mobile data off** on the phone fixes it instantly
- a device with **no cellular radio** (most tablets) never shows the problem
- the phone still reaches the **router's** admin page (that address stays on-link)
- `ping deb.debian.org` fails **on the Pi** — confirming the network has no internet

Fixes, best first:
1. **Give the network real internet access.** Phones then keep Wi-Fi as their
   default network — and `apt` starts working on the Pi too.
2. Turn **mobile data off** on the phone while using the readout.
3. Android: disable *Adaptive connectivity* / *Switch to mobile data
   automatically*; when prompted "Wi-Fi has no internet access", stay connected.

> Changing the phone's MAC/IP can appear to fix this. It's a coincidence of
> re-running DHCP and resetting the phone's routing, not a cure — don't chase it.

### If that isn't it: are the packets even arriving?

**Rule the Pi out in two commands:**

```bash
sudo nft list ruleset; sudo iptables -L -n      # empty: the Pi blocks nobody
journalctl -u telemetre -b | grep 'GET / HTTP'  # blocked device never appears
```
If the device's requests never reach the access log, its packets are not arriving
at all — nothing the Pi runs can cause that.

**Prove where the packets die.** `tcpdump` is usually absent (and the Pi may have
no internet to install it), so use the bundled stdlib sniffer:

```bash
sudo systemd-run --unit=tb-sniff python3 ~/TelemetreBergman/scripts/net_sniff.py
journalctl -u tb-sniff -f      # now reproduce the fault on the device
sudo systemctl stop tb-sniff   # transient unit; also clears on reboot
```

Read it while the device is blocked:

| What the Pi sees from that device | Where the fault is |
|---|---|
| **nothing at all** | the switch/AP never forwards its frames — stale bridge-host entry, client isolation, or a VLAN mismatch |
| `ARP who-has <pi-ip>` arrives, Pi answers `is-at`, device keeps asking | the **ARP replies** aren't getting back to it |
| `SYN` arrives, Pi sends `SYN,ACK`, no final `ACK` | the **return path** is broken |

All three mean the packets die on the LAN, not on the Pi. Note the first row is
also what you see when the device sent them out of a **different interface**
entirely (the mobile-data case above) — check that first, it's far more common.

If the frames really are being dropped on the network, on a MikroTik inspect:

```
/ip arp print                    # stale, duplicate or invalid entry for that IP?
/interface bridge host print     # is that MAC learned on the wrong port?
/interface bridge print          # try hw=no: hardware offload can leave stale
                                 # L2 entries
```

Also worth giving the Pi a **fixed DHCP lease** so the readout URL stops moving.
