#!/bin/bash
# Builds, signs and (optionally) notarizes a self-contained Cadreur.app.
#
#   ./scripts/build_app.sh              # build + sign + verify
#   ./scripts/build_app.sh --notarize   # ... then notarize and staple
#
# The result is ONE file to copy anywhere: no Python, no venv, no repo folder
# beside it. The operator's data lives in ~/Library/Application Support/Cadreur
# and is never touched by a rebuild.
#
# Notarization takes credentials one of two ways.
#
#   Locally — a keychain profile you create once:
#     xcrun notarytool store-credentials "cadreur-notary" \
#       --apple-id <your-apple-id> --team-id TVYU3CS2N7
#
#   In CI — an App Store Connect API key, via three env vars:
#     NOTARY_KEY_PATH     path to the .p8 private key
#     NOTARY_API_KEY_ID   the key's ID
#     NOTARY_API_ISSUER   the issuer UUID
#
# The API key path wins when NOTARY_KEY_PATH is set. Nothing secret is stored
# in this repo either way.
set -euo pipefail
cd "$(dirname "$0")/.."

IDENTITY="${CADREUR_IDENTITY:-Developer ID Application: Pierre-Olivier Boulant (TVYU3CS2N7)}"
PROFILE="${CADREUR_NOTARY_PROFILE:-cadreur-notary}"
BUNDLE_ID="${CADREUR_BUNDLE_ID:-com.pob31.cadreur}"
NOTARIZE=0
[ "${1:-}" = "--notarize" ] && NOTARIZE=1

APP="dist/Cadreur.app"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' cadreur-rs/Cargo.toml | head -1)"

echo "==> Building the release binary (arm64)"
(cd cadreur-rs && cargo build --release)
BIN="cadreur-rs/target/release/cadreur"
echo "    $(du -h "$BIN" | cut -f1)  $BIN"

echo "==> Assembling $APP (version $VERSION)"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/cadreur"

ICONSET="$(mktemp -d)/Cadreur.iconset"
python3 scripts/make_icon.py "$ICONSET" >/dev/null
iconutil -c icns -o "$APP/Contents/Resources/Cadreur.icns" "$ICONSET"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>                <string>Cadreur</string>
  <key>CFBundleDisplayName</key>         <string>Cadreur Bergman</string>
  <key>CFBundleIdentifier</key>          <string>${BUNDLE_ID}</string>
  <key>CFBundleVersion</key>             <string>${VERSION}</string>
  <key>CFBundleShortVersionString</key>  <string>${VERSION}</string>
  <key>CFBundleExecutable</key>          <string>cadreur</string>
  <key>CFBundleIconFile</key>            <string>Cadreur</string>
  <key>CFBundlePackageType</key>         <string>APPL</string>
  <key>NSHighResolutionCapable</key>     <true/>
  <key>LSMinimumSystemVersion</key>      <string>11.0</string>
  <key>LSApplicationCategoryType</key>   <string>public.app-category.video</string>
  <!-- macOS 15 gates LAN access. Cadreur reads the Pi's distance over the
       stage network, so the prompt explains itself in the operator's language. -->
  <key>NSLocalNetworkUsageDescription</key>
  <string>Cadreur lit la distance du tulle mesurée par le boîtier télémètre sur le réseau du théâtre.</string>
</dict>
</plist>
PLIST

echo "==> Signing with Developer ID + hardened runtime"
# One static binary and no embedded interpreter, so there are no nested
# Mach-O files to sign inside-out and no entitlements to weaken the runtime
# with. --deep is deliberately NOT used: Apple deprecated it, and there is
# nothing nested here for it to reach anyway.
codesign --force --options runtime --timestamp \
  --sign "$IDENTITY" "$APP/Contents/MacOS/cadreur"
codesign --force --options runtime --timestamp \
  --sign "$IDENTITY" "$APP"

echo "==> Verifying the signature"
codesign --verify --strict --verbose=2 "$APP"
codesign -dv --verbose=4 "$APP" 2>&1 | grep -E "^(Identifier|Authority|TeamIdentifier|Runtime)" || true

# --- disk image -------------------------------------------------------------
# The installer the operator actually receives: open it, drag Cadreur onto
# Applications, done. Built AFTER the app is stapled so the copy inside carries
# its own ticket and validates offline once dragged out.
make_dmg() {
  local stage
  stage="$(mktemp -d)/Cadreur"
  mkdir -p "$stage"
  cp -R "$APP" "$stage/"
  ln -s /Applications "$stage/Applications"
  rm -f "$DMG"
  hdiutil create -volname "Cadreur $VERSION" -srcfolder "$stage" \
    -ov -format UDZO -quiet "$DMG"
  rm -rf "$(dirname "$stage")"
  # A signed disk image so Gatekeeper can attribute it before it is opened.
  codesign --force --timestamp --sign "$IDENTITY" "$DMG"
  echo "    $(du -h "$DMG" | cut -f1)  $DMG"
}

DMG="dist/Cadreur-${VERSION}.dmg"

notary_auth() {
  # An API key beats a keychain profile: CI has no keychain profile, and the
  # key is revocable on its own without touching the Apple ID.
  if [ -n "${NOTARY_KEY_PATH:-}" ]; then
    NOTARY_AUTH=(--key "$NOTARY_KEY_PATH"
                 --key-id "${NOTARY_API_KEY_ID:?NOTARY_API_KEY_ID is required with NOTARY_KEY_PATH}"
                 --issuer "${NOTARY_API_ISSUER:?NOTARY_API_ISSUER is required with NOTARY_KEY_PATH}")
    echo "    (App Store Connect API key)"
  else
    NOTARY_AUTH=(--keychain-profile "$PROFILE")
    echo "    (keychain profile '$PROFILE')"
  fi
}

submit() {  # submit <file> <what>
  if ! xcrun notarytool submit "$1" "${NOTARY_AUTH[@]}" --wait; then
    echo
    echo "Notarization of $2 failed. For the reason:"
    echo "  xcrun notarytool history ${NOTARY_AUTH[*]}"
    echo "  xcrun notarytool log <submission-id> ${NOTARY_AUTH[*]}"
    exit 1
  fi
}

if [ "$NOTARIZE" -eq 0 ]; then
  echo "==> Building the disk image"
  make_dmg
  echo
  echo "Built (signed, NOT notarized):"
  echo "  app: $APP"
  echo "  dmg: $DMG"
  echo "Gatekeeper will still challenge these if they are transferred by"
  echo "AirDrop, mail or download. Re-run with --notarize before sending them."
  exit 0
fi

ZIP="dist/Cadreur-${VERSION}.zip"
echo "==> Zipping the app for submission"
# ditto, not zip(1): it preserves the bundle structure and the extended
# attributes notarization expects.
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> Submitting the app to Apple"
notary_auth
submit "$ZIP" "the app"

echo "==> Stapling the ticket to the app"
# This is the step that matters for a venue with no internet: with a stapled
# ticket Gatekeeper validates offline. Without one, the first launch wants to
# reach Apple, and there is no network at the theatre.
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"

# Re-zip now that the app carries its ticket, so the zip is usable on its own.
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> Building the disk image around the stapled app"
make_dmg

echo "==> Submitting the disk image to Apple"
# The image is notarized in its own right, so the download opens without a
# warning as well as the app inside it.
submit "$DMG" "the disk image"

echo "==> Stapling the ticket to the disk image"
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"

echo "==> Final checks, as the recipient's Mac will see them"
spctl -a -vvv -t exec "$APP"
spctl -a -vvv -t open --context context:primary-signature "$DMG"

echo
echo "Notarized and stapled."
echo "  dmg: $DMG   <- send this one: the installer"
echo "  app: $APP"
echo "  zip: $ZIP   <- same app, for anyone who prefers a zip"
