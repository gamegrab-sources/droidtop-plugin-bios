#!/usr/bin/env bash
# Signs build.sh's output and packs <plugin id>.droidplugin.tar.xz, in the official shape under the gamegrab-sources
# master (as droidtop-plugin-f95): this repository's own key, derived by plugin-key-provision, signs the manifest and
# its certificate from the master travels in the bundle as origin.cert. CI writes the secrets PLUGIN_SIGNING_KEY and
# PLUGIN_SIGNING_CERT to private temporary files and passes their paths here.
set -euo pipefail
cd "$(dirname "$0")"

: "${PLUGIN_SIGNING_KEY_FILE:?set to the path of this repository's private key (PEM)}"
: "${PLUGIN_SIGNING_CERT_FILE:?set to the path of this key's certificate from the gamegrab-sources master}"
BUNDLE_DIR=build
for part in manifest.json plugin.py; do
  test -e "$BUNDLE_DIR/$part" || { echo "missing $BUNDLE_DIR/$part: run build.sh first" >&2; exit 1; }
done
PLUGIN_ID="$(python3 -c "import json; print(json.load(open('$BUNDLE_DIR/manifest.json'))['id'])")"

openssl dgst -sha256 -sign "$PLUGIN_SIGNING_KEY_FILE" "$BUNDLE_DIR/manifest.json" | base64 -w0 > "$BUNDLE_DIR/manifest.sig"
cp "$PLUGIN_SIGNING_CERT_FILE" "$BUNDLE_DIR/origin.cert"

OUT="${PLUGIN_ID}.droidplugin.tar.xz"
tar -C "$BUNDLE_DIR" --sort=name -cf - manifest.json manifest.sig origin.cert plugin.py | xz -9e > "$OUT"
echo "Signed $OUT"
sha256sum "$OUT"
