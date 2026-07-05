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
- **OSC output** provisioned (disabled by default) for a future show-control feed.

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
Then browse to **http://192.168.1.36/** (or the Pi's IP). Give the Pi a DHCP
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
ssh bergman@192.168.1.36 "~/TelemetreBergman/scripts/deploy.sh"   # git pull + restart
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
web/             index.html app.js style.css   (EventSource UI)
systemd/         telemetre.service
scripts/         install.sh deploy.sh detect_serial.py
tests/           test_frames.py test_filters.py
documentation/   TF02-Pro datasheets/manual
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
