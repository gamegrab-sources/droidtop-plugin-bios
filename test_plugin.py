"""Drives the BIOS plugin through search, detail, acquire and its settings with a fake droidtop module (the real
one only exists inside droidtop's embedded interpreter): `python3 test_plugin.py` from this folder. CI runs the same.

The index is data/index.json, the file build.sh embeds; the tests also check that file against its own rules so a
regenerated index that lost a hash or a source fails here."""
import base64
import json
import os
import re
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

calls = []
store = {}
http_answers = {}


def fake_call(api, op, args=None, version=1):
    args = args or {}
    calls.append((api, op, args))
    if api == "data" and op == "write":
        raw = base64.b64decode(args["base64"])
        store[args["name"]] = (store.get(args["name"], b"") if args.get("append") else b"") + raw
        return {"ok": True, "data": {}}
    if api == "data" and op == "read":
        if args["name"] not in store:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": "no such file"}}
        raw = store[args["name"]]
        part = raw[args["offset"] : args["offset"] + args["length"]]
        return {"ok": True, "data": {"base64": base64.b64encode(part).decode("ascii"), "eof": args["offset"] + len(part) >= len(raw)}}
    if api == "net" and op == "http":
        key = (args["method"], args["url"])
        if key not in http_answers:
            return {"ok": False, "error": {"code": "PERMISSION_DENIED", "message": "not reachable in this test: %s %s" % key}}
        answer = http_answers[key]
        if isinstance(answer, dict) and "error" in answer:
            return {"ok": False, "error": answer["error"]}
        return {"ok": True, "data": answer}
    return {"ok": False, "error": {"code": "UNSUPPORTED", "message": "%s %s" % (api, op)}}


fake_host = types.ModuleType("droidtop.host")
fake_host.call = fake_call
fake_pkg = types.ModuleType("droidtop")
fake_pkg.__path__ = []
fake_pkg.host = fake_host
sys.modules["droidtop"] = fake_pkg
sys.modules["droidtop.host"] = fake_host

# TEST_BUILT=1 runs the same tests against build/plugin.py, the file that ships (index embedded).
sys.path.insert(0, os.path.join(HERE, "build" if os.environ.get("TEST_BUILT") else "src"))
import plugin  # noqa: E402


def send(point, op, **args):
    return json.loads(plugin.handle(json.dumps({"point": point, "version": 1, "op": op, "args": args})))


def job(op, **args):
    return json.loads(plugin.start_job("j1", {"point": "library.sources", "op": op, "args": args}, lambda percent, status: None))


def items(view):
    return view["sections"][0]["items"]


RETRO_SCPH = "https://raw.githubusercontent.com/Abdess/retrobios/main/bios/Sony/PlayStation/scph5500.bin"


class BiosPluginTest(unittest.TestCase):
    def setUp(self):
        calls.clear()
        store.clear()
        http_answers.clear()
        plugin._listing_cache.clear()

    # ---------------------------------------------------------------- search

    def test_a_systems_own_files_are_listed_from_the_system_droidtop_names(self):
        reply = send("library.sources", "search", query="", values={}, context={"system": {"id": "psx", "name": "PlayStation"}, "destination": "/x"})
        names = [r["title"] for r in reply["data"]["results"]]
        self.assertIn("scph5500.bin", names)
        self.assertTrue(all(r["platform"] == "psx" for r in reply["data"]["results"]))
        scph = [r for r in reply["data"]["results"] if r["title"] == "scph5500.bin"][0]
        self.assertEqual({"system": "psx", "file": "scph5500.bin"}, scph["ref"])
        self.assertEqual(["Checked"], scph["badges"])

    def test_droidtops_es_de_ids_reach_the_database_ids(self):
        for droidtop_id, database_id in (("n3ds", "3ds"), ("pcenginecd", "pcengine"), ("gc", "gamecube"), ("segacd", "segacd")):
            reply = send("library.sources", "search", query="", values={}, context={"system": {"id": droidtop_id, "name": ""}})
            self.assertTrue(reply["data"]["results"], droidtop_id)
            self.assertEqual(database_id, reply["data"]["results"][0]["platform"])

    def test_words_find_a_file_or_a_system_across_all_systems(self):
        by_file = send("library.sources", "search", query="scph5500", values={})["data"]["results"]
        self.assertEqual(["psx"], sorted(set(r["platform"] for r in by_file)))
        by_system = send("library.sources", "search", query="nintendo switch", values={})["data"]["results"]
        self.assertEqual({"switch"}, set(r["platform"] for r in by_system))
        self.assertEqual([], send("library.sources", "search", query="", values={})["data"]["results"])

    def test_a_file_with_no_source_still_shows_and_says_so(self):
        reply = send("library.sources", "search", query="", values={}, context={"system": {"id": "apple2", "name": "Apple II"}})
        badges = {r["title"]: r["badges"][0] for r in reply["data"]["results"]}
        self.assertEqual("No source", badges["votrsc01a.zip"])
        reply = send("library.sources", "search", query="", values={}, context={"system": {"id": "adam", "name": "Coleco Adam"}})
        badges = {r["title"]: r["badges"][0] for r in reply["data"]["results"]}
        self.assertEqual("Other dump", badges["adam_ddp.zip"], "same name, a dump droidtop does not list")

    # ---------------------------------------------------------------- detail

    def test_detail_offers_each_source_and_a_download_job(self):
        reply = send("library.sources", "detail", ref={"system": "psx", "file": "scph5500.bin"})
        rows = {r["id"]: r for r in items(reply["data"])}
        self.assertEqual("PSX", rows["system"]["value"])
        choice = rows["source"]
        self.assertEqual("0", choice["value"])
        self.assertIn("retrobios (GitHub)", choice["options"][0]["label"])
        self.assertIn("SHA-256", choice["options"][0]["label"])
        self.assertTrue(any("Internet Archive: retroarch_bios" in o["label"] for o in choice["options"]))
        action = rows["acquire"]["action"]
        self.assertEqual("job", action["kind"])
        self.assertEqual("acquire", action["op"])
        self.assertEqual({"ref": {"system": "psx", "file": "scph5500.bin"}}, action["args"])

    def test_detail_of_a_file_with_no_source_has_no_download(self):
        reply = send("library.sources", "detail", ref={"system": "apple2", "file": "votrsc01a.zip"})
        ids = [r["id"] for r in items(reply["data"])]
        self.assertIn("none", ids)
        self.assertNotIn("acquire", ids)

    def test_an_unknown_file_is_not_found(self):
        self.assertEqual("NOT_FOUND", send("library.sources", "detail", ref={"system": "psx", "file": "nope.bin"})["error"]["code"])
        self.assertEqual("NOT_FOUND", send("library.sources", "detail", ref={"system": "nowhere", "file": "x"})["error"]["code"])

    # --------------------------------------------------------------- acquire

    def test_acquire_asks_the_source_once_and_returns_a_checked_download(self):
        http_answers[("HEAD", RETRO_SCPH)] = {"status": 200, "url": RETRO_SCPH, "headers": {"content-length": "524288"}, "body": ""}
        reply = job("acquire", ref={"system": "psx", "file": "scph5500.bin"}, values={"source": "0"})
        self.assertTrue(reply["ok"], reply)
        download = json.loads(reply["values"]["download"])
        self.assertEqual(RETRO_SCPH, download["url"])
        self.assertEqual("scph5500.bin", download["fileName"])
        self.assertEqual(524288, download["size"])
        self.assertRegex(download["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(1, len([c for c in calls if c[0] == "net"]))

    def test_acquire_stops_when_the_source_no_longer_has_the_file_or_has_another_size(self):
        http_answers[("HEAD", RETRO_SCPH)] = {"status": 404, "url": RETRO_SCPH, "headers": {}, "body": ""}
        self.assertIn("no longer has", job("acquire", ref={"system": "psx", "file": "scph5500.bin"}, values={})["error"])
        http_answers[("HEAD", RETRO_SCPH)] = {"status": 200, "url": RETRO_SCPH, "headers": {"content-length": "999"}, "body": ""}
        self.assertIn("another size", job("acquire", ref={"system": "psx", "file": "scph5500.bin"}, values={})["error"])

    def test_acquire_reports_a_source_that_does_not_answer(self):
        http_answers[("HEAD", RETRO_SCPH)] = {"error": {"code": "TIMEOUT", "message": "timed out"}}
        self.assertIn("did not answer", job("acquire", ref={"system": "psx", "file": "scph5500.bin"}, values={})["error"])

    def test_an_internet_archive_choice_has_no_sha256_but_a_size(self):
        reply = send("library.sources", "detail", ref={"system": "psx", "file": "scph5500.bin"})
        options = [o for o in items(reply["data"]) if o["id"] == "source"][0]["options"]
        position = [i for i, o in enumerate(options) if "Internet Archive" in o["label"]][0]
        url = "https://archive.org/download/retroarch_bios/ps-30j.bin"
        http_answers[("HEAD", url)] = {"status": 200, "url": "https://dn1.ca.archive.org/0/items/retroarch_bios/ps-30j.bin", "headers": {"content-length": "524288"}, "body": ""}
        reply = job("acquire", ref={"system": "psx", "file": "scph5500.bin"}, values={"source": str(position)})
        download = json.loads(reply["values"]["download"])
        self.assertEqual(url, download["url"])
        self.assertEqual("scph5500.bin", download["fileName"], "the file is named as the emulator setup expects, not as the archive")
        self.assertNotIn("sha256", download)
        self.assertIn("MD5 matches", reply["values"]["message"])

    # -------------------------------------------------------------- settings

    def test_sources_can_be_switched_and_added_and_are_kept(self):
        self.assertTrue(send("ui.settings", "setRetrobios", values={"retrobios": "false"})["ok"])
        detail = send("library.sources", "detail", ref={"system": "psx", "file": "scph5500.bin"})
        labels = [o["label"] for o in [r for r in items(detail["data"]) if r["id"] == "source"][0]["options"]]
        self.assertFalse(any("retrobios" in label for label in labels))
        self.assertTrue(send("ui.settings", "addUrl", values={"new_url": "My mirror = https://example.org/bios/{system}/{file}"})["ok"])
        view = send("ui.settings", "view")["data"]
        titles = [r["title"] for r in items(view)]
        self.assertIn("Remove My mirror", titles)
        detail = send("library.sources", "detail", ref={"system": "psx", "file": "scph5500.bin"})
        labels = [o["label"] for o in [r for r in items(detail["data"]) if r["id"] == "source"][0]["options"]]
        self.assertTrue(any(label.startswith("My mirror") for label in labels))
        # A person's address is fetched by droidtop's downloader; the plugin does not ask a host it did not declare.
        mine = [i for i, label in enumerate(labels) if label.startswith("My mirror")][0]
        reply = job("acquire", ref={"system": "psx", "file": "scph5500.bin"}, values={"source": str(mine)})
        self.assertEqual("https://example.org/bios/psx/scph5500.bin", json.loads(reply["values"]["download"])["url"])
        self.assertEqual([], [c for c in calls if c[0] == "net"])
        self.assertTrue(send("ui.settings", "removeUrl", args={"position": 0})["ok"])
        self.assertNotIn("Remove My mirror", [r["title"] for r in items(send("ui.settings", "view")["data"])])

    def test_a_non_https_address_is_refused(self):
        self.assertEqual("INVALID_ARGS", send("ui.settings", "addUrl", values={"new_url": "http://example.org/{file}"})["error"]["code"])

    def test_extra_internet_archive_items_are_listed_once_and_matched_by_md5_or_name(self):
        send("ui.settings", "setItems", values={"ia_items": "https://archive.org/details/some_bios_pack, bad!item"})
        state = plugin._load_state()
        self.assertEqual(["some_bios_pack"], state["ia_items"])
        listing = {"result": [{"name": "SCPH5500.BIN", "md5": "8dd7d5296a650fac7319bce665a6a53c", "size": "524288"}, {"name": "unrelated.bin", "md5": "00", "size": "1"}]}
        http_answers[("GET", "https://archive.org/metadata/some_bios_pack/files")] = {"status": 200, "url": "x", "headers": {}, "body": json.dumps(listing), "truncated": False}
        detail = send("library.sources", "detail", ref={"system": "psx", "file": "scph5500.bin"})
        labels = [o["label"] for o in [r for r in items(detail["data"]) if r["id"] == "source"][0]["options"]]
        self.assertTrue(any("some_bios_pack" in label for label in labels))

    # ----------------------------------------------------------------- misc

    def test_unknown_points_and_ops_are_error_replies(self):
        self.assertEqual("UNSUPPORTED", send("library.sources", "lookup")["error"]["code"])
        self.assertEqual("UNSUPPORTED", send("ui.panel", "panel")["error"]["code"])
        self.assertFalse(json.loads(plugin.start_job("j2", {"point": "x", "op": "y"}, lambda p, s: None))["ok"])
        self.assertEqual("INVALID_ARGS", json.loads(plugin.handle("not json"))["error"]["code"])


class IndexAndManifestTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "data", "index.json"), encoding="utf-8") as handle:
            self.index = json.load(handle)

    def test_every_file_has_a_name_droidtop_accepts_and_every_hash_is_well_formed(self):
        for system_id, system in self.index["systems"].items():
            self.assertTrue(system["files"], system_id)
            for item in system["files"]:
                self.assertRegex(item["name"], plugin.SAFE_NAME, system_id)
                for md5 in item["md5"]:
                    self.assertRegex(md5, r"^[0-9a-f]{32}$")
                for source in item["src"]:
                    if source["kind"] == "retrobios":
                        self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
                        self.assertGreater(source["size"], 0)
                        self.assertTrue(source["path"].startswith("bios/"))
                    else:
                        self.assertEqual("ia", source["kind"])
                        self.assertRegex(source["md5"], r"^[0-9a-f]{32}$")
                    if source["exact"] and item["md5"]:
                        self.assertIn(source["md5"], item["md5"], "%s/%s" % (system_id, item["name"]))

    def test_the_systems_the_brief_names_are_there_with_a_source(self):
        for system_id in ("psx", "ps2", "saturn", "dreamcast", "nds", "gba", "3ds", "switch", "neogeo", "pcengine"):
            self.assertIn(system_id, self.index["systems"])
            self.assertTrue(any(s["exact"] for f in self.index["systems"][system_id]["files"] for s in f["src"]), system_id)

    def test_the_manifest_declares_what_the_plugin_uses_and_nothing_more(self):
        with open(os.path.join(HERE, "manifest.template.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual("python", manifest["kind"])
        self.assertEqual("gamegrab.bios", manifest["id"])
        self.assertFalse(manifest.get("requestsRoot"))
        self.assertEqual({"library.sources", "ui.settings"}, set(p["point"] for p in manifest["provides"]))
        permissions = dict((p["id"], p) for p in manifest["permissions"])
        self.assertEqual({"net.domains", "library.folders.write"}, set(permissions))
        for host in plugin.DECLARED_HOSTS:
            self.assertTrue(any(d == host or d == "*." + host for d in permissions["net.domains"]["domains"]), host)
        with open(os.path.join(HERE, "src", "plugin.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotRegex(text, r"^\s*(import|from) (socket|urllib\.request|http|ssl)\b", "a contained plugin has no sockets")
        self.assertTrue(re.search(r"^_EMBEDDED = None  # build.sh", text, re.M), "build.sh needs its marker line")


if __name__ == "__main__":
    unittest.main()
