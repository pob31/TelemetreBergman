#!/bin/bash
# Builds a double-clickable Cadreur.app at the repo root (macOS).
#
# The bundle just launches .venv/bin/cadreur-gui from the repo, so it must
# STAY in the repo folder — drag it to the Dock for one-click access (the
# Dock keeps a reference). Built locally on purpose: a bundle generated on
# this machine carries no quarantine flag, so Gatekeeper never complains.
#
#   ./scripts/make_app.sh
set -euo pipefail
cd "$(dirname "$0")/.."

APP="Cadreur.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>           <string>Cadreur</string>
  <key>CFBundleDisplayName</key>    <string>Cadreur Bergman</string>
  <key>CFBundleIdentifier</key>     <string>local.bergman.cadreur</string>
  <key>CFBundleVersion</key>        <string>1.0</string>
  <key>CFBundleExecutable</key>     <string>cadreur</string>
  <key>CFBundlePackageType</key>    <string>APPL</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

cat > "$APP/Contents/MacOS/cadreur" <<'EOF'
#!/bin/bash
# Launched by Finder/Dock. The bundle lives at the repo root.
cd "$(dirname "$0")/../../.."
if [ ! -x .venv/bin/cadreur-gui ]; then
  osascript -e 'display dialog "Cadreur : environnement Python manquant.\n\nDans le Terminal, depuis le dossier du projet :\n  python3.13 -m venv .venv\n  .venv/bin/pip install -e '"'"'.[gui]'"'"'" buttons {"OK"} with icon stop with title "Cadreur"'
  exit 1
fi
exec .venv/bin/cadreur-gui >> cadreur_gui.log 2>&1
EOF
chmod +x "$APP/Contents/MacOS/cadreur"

echo "Built $APP — double-click it in Finder (keep it inside the repo folder)."
