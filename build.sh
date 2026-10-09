#!/usr/bin/env bash
# Builds the plugin bundle (unsigned): build/plugin.py (src/plugin.py with data/index.json embedded in place of its
# `_EMBEDDED = None` line) and build/manifest.json (the template with the payload hash and, in CI, the run number).
# A python-kind plugin loads one file, plugin.py, so the data travels inside it. Never touches a key: sign.sh does.
set -euo pipefail
cd "$(dirname "$0")"

rm -rf -- build
mkdir -p build

python3 - <<'PY'
import json

with open("data/index.json", encoding="utf-8") as handle:
    index = json.load(handle)
literal = "json.loads(" + repr(json.dumps(index, sort_keys=True, separators=(",", ":"))) + ")"
marker = "_EMBEDDED = None  # build.sh replaces this line with the index (see the marker below)"
with open("src/plugin.py", encoding="utf-8") as handle:
    source = handle.read()
if source.count(marker) != 1:
    raise SystemExit("src/plugin.py must hold the marker line exactly once")
source = source.replace(marker, "_EMBEDDED = " + literal)
with open("build/plugin.py", "w", encoding="utf-8") as handle:
    handle.write(source)

manifest = json.load(open("manifest.template.json"))
import hashlib, os
manifest["payload"] = [{"path": "plugin.py", "sha256": hashlib.sha256(open("build/plugin.py", "rb").read()).hexdigest()}]
# The published version is <declared>-<CI run> (Droidtop/tracker#126).
build = os.environ.get("DROIDTOP_PLUGIN_BUILD", "").strip()
if build:
    if not build.isdigit():
        raise SystemExit("DROIDTOP_PLUGIN_BUILD must be a CI run number, got %r" % build)
    manifest["version"] = "%s-%s" % (manifest["version"], build)
json.dump(manifest, open("build/manifest.json", "w"), indent=2, sort_keys=True)
print("payload plugin.py, id=%s, version=%s" % (manifest["id"], manifest["version"]))
PY
# The embedded file must still be valid Python, and import with the index in it.
python3 -c "import ast,sys; ast.parse(open('build/plugin.py', encoding='utf-8').read())"
echo "Built build/plugin.py and build/manifest.json (unsigned)"
