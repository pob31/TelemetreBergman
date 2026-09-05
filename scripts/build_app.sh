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
# Notarization needs a keychain profile, created once by you:
#   xcrun notarytool store-credentials "cadreur-notary" \
#     --apple-id <your-apple-id> --team-id TVYU3CS2N7
# It prompts for an app-specific password from appleid.apple.com. Nothing
# secret is stored in this repo.
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

if [ "$NOTARIZE" -eq 0 ]; then
  echo
  echo "Built (signed, NOT notarized): $APP"
  echo "Gatekeeper will still challenge this if it is transferred by AirDrop,"
  echo "mail or download. Re-run with --notarize before sending it anywhere."
  exit 0
fi

ZIP="dist/Cadreur-${VERSION}.zip"
echo "==> Zipping for submission"
# ditto, not zip(1): it preserves the bundle structure and extended attributes
# notarization expects.
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> Submitting to Apple (this waits for the verdict)"
if ! xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait; then
  echo
  echo "Notarization failed. For the reason:"
  echo "  xcrun notarytool history --keychain-profile $PROFILE"
  echo "  xcrun notarytool log <submission-id> --keychain-profile $PROFILE"
  exit 1
fi

echo "==> Stapling the ticket to the app"
# This is the step that matters for a venue with no internet: with a stapled
# ticket Gatekeeper validates offline. Without one, the first launch wants to
# reach Apple, and there is no network at the theatre.
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"

echo "==> Final check, as the recipient's Mac will see it"
spctl -a -vvv -t exec "$APP"

rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"
echo
echo "Notarized and stapled."
echo "  app: $APP"
echo "  zip: $ZIP   <- send this one; it carries the stapled ticket"
