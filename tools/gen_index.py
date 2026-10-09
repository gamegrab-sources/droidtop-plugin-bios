#!/usr/bin/env python3
"""Builds data/index.json: which file each system needs and where it can be downloaded and verified.

Inputs (all public reference data, fetched by whoever runs this, never by the plugin at run time):

  --platforms  droidtop-platforms' bios-database.json: per system, the files and the MD5 of every accepted dump
               (generated there from Batocera's registry; this script never invents a hash)
  --retrobios  Abdess/retrobios' database.json: every file it holds, with path, size, MD5 and SHA-256
  --ia         NAME=FILE: an Internet Archive item's <item>_files.xml (loose files with MD5 and size)
  --extras     tools/extras.json: the 3DS and Switch files the emulators ask for, named by retrobios path

A file of the database gets a source for every place that holds a dump with one of its MD5s (an exact source) and,
when none does, the sources whose file name is the same (not exact: the emulator may still take it, droidtop cannot
tell). Nothing about a file is kept that the inputs do not say.

    python3 tools/gen_index.py --platforms bios-database.json --retrobios database.json \\
        --ia retroarch_bios=retroarch_bios_files.xml --extras tools/extras.json --out data/index.json
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

# What one source row keeps, so the index stays a few hundred KB.
MAX_SOURCES_PER_FILE = 4


def load_retrobios(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    by_md5 = {}
    by_name = {}
    by_path = {}
    for entry in data["files"].values():
        source = {"kind": "retrobios", "path": entry["path"], "md5": entry["md5"], "sha256": entry["sha256"], "size": entry["size"]}
        by_path[entry["path"]] = source
        by_md5.setdefault(entry["md5"], []).append(source)
        by_name.setdefault(entry["name"].lower(), []).append(source)
    return {"generated_at": data.get("generated_at"), "total_files": data.get("total_files")}, by_md5, by_name, by_path


def load_ia(spec):
    name, _, path = spec.partition("=")
    tree = ET.parse(path)
    out = []
    for node in tree.getroot().findall("file"):
        if node.get("source") != "original":
            continue
        md5 = (node.findtext("md5") or "").lower()
        size = int(node.findtext("size") or 0)
        if md5 and "/" not in node.get("name"):
            out.append({"kind": "ia", "item": name, "name": node.get("name"), "md5": md5, "size": size})
    return out


def pick(candidates):
    """Fewest path segments first, then the path itself, so the choice is the same on every run."""
    return sorted(candidates, key=lambda s: (s.get("path", s.get("name", "")).count("/"), s.get("path", s.get("name", ""))))


def with_flag(source, exact):
    copy = dict(source)
    copy["exact"] = exact
    return copy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platforms", required=True)
    parser.add_argument("--platforms-commit", default="")
    parser.add_argument("--retrobios", required=True)
    parser.add_argument("--ia", action="append", default=[])
    parser.add_argument("--extras", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    meta, rb_md5, rb_name, rb_path = load_retrobios(args.retrobios)
    ia_files = []
    for spec in args.ia:
        ia_files.extend(load_ia(spec))
    ia_md5 = {}
    ia_name = {}
    for source in ia_files:
        ia_md5.setdefault(source["md5"], []).append(source)
        ia_name.setdefault(source["name"].lower(), []).append(source)

    with open(args.platforms, encoding="utf-8") as handle:
        platforms = json.load(handle)

    systems = {}
    exact_files = 0
    total_files = 0
    for system_id, system in sorted(platforms["systems"].items()):
        files = []
        for entry in system["files"]:
            name = os.path.basename(entry["file"])
            md5s = sorted(set(m.lower() for m in entry["md5"]))
            sources = []
            seen = set()
            for md5 in md5s:
                for source in pick(rb_md5.get(md5, [])):
                    if source["md5"] not in seen:
                        seen.add(source["md5"])
                        sources.append(with_flag(source, True))
            for md5 in md5s:
                for source in pick(ia_md5.get(md5, [])):
                    if ("ia", source["md5"]) not in seen:
                        seen.add(("ia", source["md5"]))
                        sources.append(with_flag(source, True))
            if not sources:
                for source in pick(rb_name.get(name.lower(), []))[:1]:
                    sources.append(with_flag(source, False))
                for source in pick(ia_name.get(name.lower(), []))[:1]:
                    sources.append(with_flag(source, False))
            total_files += 1
            if any(s["exact"] for s in sources):
                exact_files += 1
            row = {"name": name, "dir": os.path.dirname(entry["file"]), "md5": md5s, "src": sources[:MAX_SOURCES_PER_FILE]}
            files.append(row)
        systems[system_id] = {"name": system["name"], "files": sorted(files, key=lambda f: f["name"].lower())}

    with open(args.extras, encoding="utf-8") as handle:
        extras = json.load(handle)
    for system_id, system in sorted(extras["systems"].items()):
        files = []
        for entry in system["files"]:
            source = rb_path.get(entry["retrobios"])
            if source is None:
                sys.exit("retrobios no longer lists %s" % entry["retrobios"])
            files.append(
                {
                    "name": entry["name"],
                    "dir": entry.get("dir", ""),
                    "md5": [source["md5"]],
                    "note": entry["note"],
                    "src": [with_flag(source, True)],
                }
            )
            total_files += 1
            exact_files += 1
        systems[system_id] = {"name": system["name"], "files": files}

    index = {
        "version": 1,
        "reference": {
            "droidtop-platforms": {"file": "bios-database.json", "commit": args.platforms_commit, "source": platforms.get("source", "")},
            "retrobios": {"database": "Abdess/retrobios database.json", "generated_at": meta["generated_at"]},
            "internet-archive": [spec.partition("=")[0] for spec in args.ia],
        },
        "systems": systems,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(index, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    print("systems=%d files=%d with an exact source=%d" % (len(systems), total_files, exact_files))


if __name__ == "__main__":
    main()
