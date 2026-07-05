#!/usr/bin/env bash
# One-shot installer for Telemetre Bergman on the Raspberry Pi. Idempotent.
#
#   git clone <repo> ~/TelemetreBergman && cd ~/TelemetreBergman
#   ./scripts/install.sh
#
# Does: I2C UART HAT overlay (SC16IS752 Serial Expansion HAT), venv+deps,
#       config.toml, sudoers power rule, systemd service (enabled at boot).
#       Reboot once after first run if it reports the overlay was newly added.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
# Waveshare Serial Expansion HAT: SC16IS752 over I2C @ 0x48, INT on GPIO24 =>
# /dev/ttySC0 (ch A), /dev/ttySC1 (ch B). This is an I2C board — do NOT use the
# SPI overlay (sc16is75x-spi); it registers a phantom port whose close()
# dead-locks the kernel (unkillable D-state, needs a reboot).
I2C_PARAM='dtparam=i2c_arm=on'
OVERLAY_LINE='dtoverlay=sc16is752-i2c,int_pin=24,addr=0x48'
CONFIG_TXT=/boot/firmware/config.txt
SERVICE=/etc/systemd/system/telemetre.service
SUDOERS=/etc/sudoers.d/telemetre-power
REBOOT_NEEDED=0

echo "== [1/6] I2C UART HAT overlay (SC16IS752 @ 0x48) =="
ensure_active() {  # add exact line under [all] unless an identical ACTIVE line exists
  if ! grep -qxF "$1" "$CONFIG_TXT"; then
    printf '\n[all]\n%s\n' "$1" | sudo tee -a "$CONFIG_TXT" >/dev/null
    echo "   added: $1"; REBOOT_NEEDED=1
  else
    echo "   already active: $1"
  fi
}
if [ -f "$CONFIG_TXT" ]; then
  # An I2C board with the SPI overlay dead-locks on close() — disable it if active.
  if grep -qE '^dtoverlay=sc16is75x-spi' "$CONFIG_TXT"; then
    sudo sed -i 's/^dtoverlay=sc16is75x-spi/#&/' "$CONFIG_TXT"
    echo "   disabled stale SPI overlay (sc16is75x-spi)"; REBOOT_NEEDED=1
  fi
  ensure_active "$I2C_PARAM"
  ensure_active "$OVERLAY_LINE"
  [ "$REBOOT_NEEDED" = "1" ] && echo "   (reboot required for /dev/ttySC*)"
else
  echo "   WARN: $CONFIG_TXT not found — skipping overlay setup"
fi

echo "== [2/6] Python venv + dependencies =="
cd "$REPO_DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .
# i2c-tools provides i2cdetect, handy for verifying the HAT at 0x48 (non-fatal).
command -v i2cdetect >/dev/null 2>&1 || sudo apt-get -qq install -y i2c-tools >/dev/null 2>&1 || \
  echo "   (note: could not install i2c-tools; i2cdetect will be unavailable)"

echo "== [3/6] config.toml =="
[ -f config.toml ] || { cp config.example.toml config.toml; echo "   created from example"; }

echo "== [4/6] sudoers rule (safe power off/reboot from the web) =="
if [ ! -f "$SUDOERS" ]; then
  echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot" \
    | sudo tee "$SUDOERS" >/dev/null
  sudo chmod 440 "$SUDOERS"
  sudo visudo -cf "$SUDOERS" >/dev/null && echo "   installed + validated"
else
  echo "   already present"
fi

echo "== [5/6] systemd service =="
# Rewrite the template's default user/paths to whatever this checkout actually is.
sed -e "s#/home/bergman/TelemetreBergman#$REPO_DIR#g" \
    -e "s/^User=bergman/User=$USER_NAME/" \
    -e "s/^Group=bergman/Group=$USER_NAME/" \
    systemd/telemetre.service | sudo tee "$SERVICE" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable telemetre.service >/dev/null 2>&1 || true
echo "   enabled at boot"

echo "== [6/6] Done =="
if [ "$REBOOT_NEEDED" = "1" ]; then
  echo ">> Overlay newly added. Reboot, then: sudo systemctl start telemetre"
else
  sudo systemctl restart telemetre.service
  echo ">> Service (re)started. Browse to http://$(hostname -I | awk '{print $1}')/"
fi
