"""gamegrab.bios: finds the BIOS, firmware and key files an emulator needs and hands them to droidtop to download.

An unofficial droidtop plugin (gamegrab-sources, GPL-3.0). Python kind, standard library only, run contained
(docs/plugin-api.md 5.3 in droidtop): every byte it fetches goes through droidtop's net.http, every file it keeps
through droidtop's data API, and the files themselves are downloaded by droidtop (the acquire reply's `download`).

What it knows (data/index.json, built by tools/gen_index.py and embedded into the bundle by build.sh):

  * the files each system needs and the MD5 of every dump droidtop accepts: droidtop-platforms' bios-database.json,
    itself generated from Batocera's maintained registry;
  * where each of those files can be had: Abdess/retrobios on GitHub (raw files, with SHA-256 and size) and the
    LibRetro BIOS collection on the Internet Archive (loose files, MD5 and size);
  * for the 3DS and the Switch, the keys and firmware their emulators ask for, which that database has no rows for.

What it does with that, through the `library.sources` point (the only door droidtop offers a source today):

  search   the files of one system (from the system the person is in) or any file matching the words typed
  detail   one file: what it is, how it is verified, which source to take it from
  acquire  a job: the chosen source's download, returned as `download` with its SHA-256 when the source gives one

and a settings page (`ui.settings`) for the sources: retrobios on or off, more Internet Archive items, and the
person's own address templates.
"""
import base64
import json
import re
import urllib.parse

import droidtop.host

PLUGIN_ID = "gamegrab.bios"
SOURCE_ID = "bios"

RETROBIOS_RAW = "https://raw.githubusercontent.com/Abdess/retrobios/main/"
IA_DOWNLOAD = "https://archive.org/download/"
IA_METADATA = "https://archive.org/metadata/"
# The addresses the manifest's net.domains lists: only those can be asked from here (droidtop refuses the rest).
DECLARED_HOSTS = ("raw.githubusercontent.com", "archive.org")  # archive.org covers its subdomains, where downloads redirect to

STATE_FILE = "sources.json"
# The data API moves at most this much per call (docs/plugin-api.md 3 H1); the Flutter plugin uses the same size.
CHUNK = 120 * 1024

# droidtop names systems the ES-DE way; the BIOS database uses Batocera's ids. The few that differ.
ALIASES = {
    "n3ds": "3ds",
    "new3ds": "3ds",
    "scd": "segacd",
    "megacd": "segacd",
    "pcenginecd": "pcengine",
    "tg16cd": "pcengine",
    "tg-cd": "pcengine",
    "gc": "gamecube",
}

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HASH32 = re.compile(r"^[0-9a-fA-F]{32}$")
IA_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,99}$")

_EMBEDDED = None  # build.sh replaces this line with the index (see the marker below)
_index_cache = []
_listing_cache = {}


# ---------------------------------------------------------------------------------------------------------- index


def _index():
    if _index_cache:
        return _index_cache[0]
    data = _EMBEDDED
    if data is None:  # running from source (tests): the file build.sh would embed
        import os

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "index.json")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    _index_cache.append(data)
    return data


def _system_id(raw):
    key = (raw or "").strip().lower()
    key = ALIASES.get(key, key)
    return key if key in _index()["systems"] else ""


def _find_file(system, name):
    entry = _index()["systems"].get(system)
    if not entry:
        return None
    for item in entry["files"]:
        if item["name"] == name:
            return item
    return None


def _size_text(size):
    if not size:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return ("%d %s" % (value, unit)) if unit == "B" else ("%.1f %s" % (value, unit))
        value /= 1024.0
    return ""


# -------------------------------------------------------------------------------------------------- host helpers


def _ok(data=None):
    return json.dumps({"ok": True, "data": data if data is not None else {}})


def _error(code, message):
    return json.dumps({"ok": False, "error": {"code": code, "message": message}})


def _call(api, op, args=None, version=1):
    """One broker call; the reply envelope. A refusal comes back as a reply, never an exception."""
    return droidtop.host.call(api, op, args or {}, version)


def _http(url, method="GET", headers=None, timeout_ms=20000):
    """(status, final url, headers, body text, truncated) or raises HostError."""
    reply = _call("net", "http", {"url": url, "method": method, "headers": headers or {}, "as": "text", "timeoutMs": timeout_ms})
    if not reply.get("ok"):
        error = reply.get("error") or {}
        raise HostError(error.get("code", "FAILED"), error.get("message", "request failed"))
    data = reply.get("data") or {}
    return int(data.get("status") or 0), data.get("url") or url, data.get("headers") or {}, data.get("body") or "", bool(data.get("truncated"))


class HostError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


def _read_json(name):
    data = bytearray()
    while True:
        reply = _call("data", "read", {"name": name, "offset": len(data), "length": CHUNK, "as": "base64"})
        if not reply.get("ok"):
            return None  # no such file yet: the plugin starts from its defaults
        body = reply.get("data") or {}
        chunk = base64.b64decode(body.get("base64") or "")
        data.extend(chunk)
        if body.get("eof") or not chunk:
            break
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        return None


def _write_json(name, value):
    raw = json.dumps(value, sort_keys=True).encode("utf-8")
    offset = 0
    first = True
    while True:
        end = min(offset + CHUNK, len(raw))
        reply = _call("data", "write", {"name": name, "base64": base64.b64encode(raw[offset:end]).decode("ascii"), "append": not first})
        if not reply.get("ok"):
            error = reply.get("error") or {}
            raise HostError(error.get("code", "FAILED"), error.get("message", "could not save"))
        first = False
        offset = end
        if offset >= len(raw):
            break


# --------------------------------------------------------------------------------------------------------- state


def _default_state():
    return {"retrobios": True, "ia_items": [], "urls": []}


def _load_state():
    state = _default_state()
    saved = _read_json(STATE_FILE)
    if isinstance(saved, dict):
        if isinstance(saved.get("retrobios"), bool):
            state["retrobios"] = saved["retrobios"]
        state["ia_items"] = [i for i in saved.get("ia_items", []) if isinstance(i, str) and IA_ID.match(i)]
        state["urls"] = [u for u in saved.get("urls", []) if isinstance(u, dict) and isinstance(u.get("template"), str)]
    return state


# ----------------------------------------------------------------------------------------------------- sources


class Candidate(object):
    """One way to get a file: where, how sure we are that it is the right dump, what droidtop can verify."""

    def __init__(self, key, label, url, exact, size=0, sha256="", md5="", note=""):
        self.key = key
        self.label = label
        self.url = url
        self.exact = exact
        self.size = size
        self.sha256 = sha256
        self.md5 = md5
        self.note = note

    def describe(self):
        parts = [self.label]
        if self.sha256:
            parts.append("checked by SHA-256")
        elif self.exact:
            parts.append("listed with the right MD5")
        else:
            parts.append("a different dump, unchecked")
        if self.size:
            parts.append(_size_text(self.size))
        return " · ".join(parts)


def _expand(template, system, item):
    """A person's address template: {file}, {system}, {md5} stand for the file's name, system id and first MD5."""
    md5 = (item.get("md5") or [""])[0]
    out = template.replace("{file}", urllib.parse.quote(item["name"])).replace("{system}", urllib.parse.quote(system)).replace("{md5}", md5)
    return out


def _host_of(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _declared(url):
    host = _host_of(url)
    return any(host == h or host.endswith("." + h) for h in DECLARED_HOSTS)


def _ia_listing(item_id):
    """The files an Internet Archive item holds, name -> {md5, size}; None when it cannot be listed whole."""
    if item_id in _listing_cache:
        return _listing_cache[item_id]
    listing = None
    try:
        status, _url, _headers, body, truncated = _http(IA_METADATA + urllib.parse.quote(item_id) + "/files")
        if status == 200 and not truncated:
            rows = json.loads(body).get("result") or []
            listing = {}
            for row in rows:
                if isinstance(row, dict) and row.get("name"):
                    listing[row["name"]] = {"md5": (row.get("md5") or "").lower(), "size": int(row.get("size") or 0)}
    except (HostError, ValueError):
        listing = None
    _listing_cache[item_id] = listing
    return listing


def _candidates(system, item, state):
    out = []
    expected = set(m.lower() for m in item.get("md5", []))
    for src in item.get("src", []):
        if src["kind"] == "retrobios" and state["retrobios"]:
            url = RETROBIOS_RAW + urllib.parse.quote(src["path"])
            out.append(Candidate("rb:" + src["path"], "retrobios (GitHub)", url, src["exact"], src["size"], src.get("sha256", ""), src.get("md5", "")))
        elif src["kind"] == "ia":
            url = IA_DOWNLOAD + urllib.parse.quote(src["item"]) + "/" + urllib.parse.quote(src["name"])
            out.append(Candidate("ia:" + src["item"] + "/" + src["name"], "Internet Archive: " + src["item"], url, src["exact"], src["size"], "", src.get("md5", "")))
    seen = set(c.key for c in out)
    for item_id in state["ia_items"]:
        listing = _ia_listing(item_id)
        if listing is None:
            continue
        for name, info in listing.items():
            same_name = name.lower().rsplit("/", 1)[-1] == item["name"].lower()
            exact = bool(info["md5"]) and info["md5"] in expected
            if not (exact or same_name):
                continue
            key = "ia:" + item_id + "/" + name
            if key in seen:
                continue
            seen.add(key)
            url = IA_DOWNLOAD + urllib.parse.quote(item_id) + "/" + urllib.parse.quote(name)
            out.append(Candidate(key, "Internet Archive: " + item_id, url, exact, info["size"], "", info["md5"]))
    for position, entry in enumerate(state["urls"]):
        out.append(Candidate("url:%d" % position, entry.get("label") or "Your source", _expand(entry["template"], system, item), False, 0, "", "", "your own address"))
    # Right dumps first, then the ones droidtop can check itself.
    out.sort(key=lambda c: (not c.exact, not c.sha256))
    return out


# ------------------------------------------------------------------------------------------------------ views


def _view(title, items, subtitle=None):
    view = {"view": 1, "title": title, "sections": [{"id": "main", "items": items}]}
    if subtitle:
        view["subtitle"] = subtitle
    return view


def _info(item_id, title, value=None, subtitle=None):
    node = {"type": "info", "id": item_id, "title": title}
    if value:
        node["value"] = value
    if subtitle:
        node["subtitle"] = subtitle
    return node


def _best_size(item):
    sizes = [s.get("size", 0) for s in item.get("src", []) if s.get("exact")]
    return sizes[0] if sizes else 0


def _result(system_id, system, item):
    sources = item.get("src", [])
    exact = [s for s in sources if s.get("exact")]
    if exact:
        badge = "Checked" if any(s.get("sha256") for s in exact) else "Known dump"
    elif sources:
        badge = "Other dump"
    else:
        badge = "No source"
    columns = [system["name"]]
    size = _best_size(item)
    if size:
        columns.append(_size_text(size))
    return {
        "id": system_id + "/" + item["name"],
        "title": item["name"],
        "subtitle": system["name"],
        "columns": columns,
        "badges": [badge],
        "platform": system_id,
        "ref": {"system": system_id, "file": item["name"]},
    }


def _search(args):
    context = args.get("context") or {}
    values = args.get("values") or {}
    query = (args.get("query") or values.get("query") or "").strip().lower()
    system_id = _system_id((context.get("system") or {}).get("id") or args.get("platform"))
    systems = _index()["systems"]
    results = []
    if system_id:
        system = systems[system_id]
        for item in system["files"]:
            if not query or query in item["name"].lower():
                results.append(_result(system_id, system, item))
    elif query:
        for sid in sorted(systems):
            system = systems[sid]
            by_system = query in sid.lower() or query in system["name"].lower()
            for item in system["files"]:
                if by_system or query in item["name"].lower():
                    results.append(_result(sid, system, item))
                    if len(results) >= 200:
                        break
            if len(results) >= 200:
                break
    return _ok({"results": results})


def _detail(args):
    ref = args.get("ref") or {}
    system_id = _system_id(ref.get("system"))
    item = _find_file(system_id, ref.get("file")) if system_id else None
    if item is None:
        return _error("NOT_FOUND", "That file is not on this plugin's list")
    system = _index()["systems"][system_id]
    state = _load_state()
    candidates = _candidates(system_id, item, state)
    rows = [
        _info("system", "System", system["name"]),
        _info("file", "File", item["name"], "droidtop's Emulator setup helper names a file by its MD5 and then by this name"),
    ]
    if item.get("dir"):
        rows.append(_info("dir", "Usually kept in", item["dir"]))
    md5 = item.get("md5") or []
    if md5:
        rows.append(_info("md5", "Accepted dumps", "%d" % len(md5), "MD5 " + ", ".join(md5[:3]) + (", and %d more" % (len(md5) - 3) if len(md5) > 3 else "")))
    elif item.get("note"):
        rows.append(_info("note", "About this file", None, item["note"]))
    if not candidates:
        rows.append(_info("none", "No source for this file", None, "Turn on a source on this plugin's Settings page, or add an address of your own there. Otherwise give droidtop the file yourself."))
        return _ok(_view(item["name"], rows, "From BIOS and firmware"))
    options = [{"value": str(i), "label": c.describe()[:190]} for i, c in enumerate(candidates)]
    rows.append({"type": "choice", "id": "source", "title": "Get it from", "options": options[:100], "value": "0"})
    rows.append(
        {
            "type": "button",
            "id": "acquire",
            "title": "Download",
            "subtitle": "BIOS files are copyrighted: take only those of consoles you own.",
            "action": {"kind": "job", "op": "acquire", "title": "Download " + item["name"], "args": {"ref": {"system": system_id, "file": item["name"]}}},
        }
    )
    return _ok(_view(item["name"], rows, "From BIOS and firmware"))


def _settings_view(state):
    rows = [
        _info(
            "about",
            "BIOS and firmware",
            None,
            "Unofficial plugin from gamegrab-sources. It lists the files droidtop's emulator setup knows and downloads them from the sources below. Files are checked by the source's SHA-256 where it gives one.",
        ),
        {
            "type": "toggle",
            "id": "retrobios",
            "title": "retrobios on GitHub",
            "subtitle": "Single files with SHA-256, for most systems, the 3DS and the Switch",
            "value": bool(state["retrobios"]),
            "action": {"kind": "call", "op": "setRetrobios"},
        },
        {
            "type": "text",
            "id": "ia_items",
            "title": "More Internet Archive items",
            "subtitle": "Item names, separated by commas. The LibRetro BIOS collection is always included.",
            "value": ", ".join(state["ia_items"]),
            "action": {"kind": "call", "op": "setItems"},
        },
        {
            "type": "text",
            "id": "new_url",
            "title": "Add an address of your own",
            "subtitle": "https address; {file}, {system} and {md5} stand for the file's name, system and MD5. Label first: Name = https://...",
            "value": "",
            "action": {"kind": "call", "op": "addUrl"},
        },
    ]
    for position, entry in enumerate(state["urls"]):
        rows.append(
            {
                "type": "button",
                "id": "rm%d" % position,
                "title": "Remove " + (entry.get("label") or "your address"),
                "subtitle": entry["template"][:200],
                "confirm": "Remove this address?",
                "action": {"kind": "call", "op": "removeUrl", "args": {"position": position}},
            }
        )
    return _view("BIOS and firmware", rows)


def _set_items(args):
    text = str((args.get("values") or {}).get("ia_items") or "")
    items = []
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        # Accept a pasted item address as well as its name.
        match = re.search(r"archive\.org/(?:details|download)/([^/?#]+)", part)
        part = match.group(1) if match else part
        if IA_ID.match(part) and part not in items:
            items.append(part)
    state = _load_state()
    state["ia_items"] = items[:10]
    _write_json(STATE_FILE, state)
    _listing_cache.clear()
    return _ok({"message": "%d extra item(s) saved" % len(state["ia_items"])})


def _add_url(args):
    text = str((args.get("values") or {}).get("new_url") or "").strip()
    if not text:
        return _ok({"message": "Nothing to add"})
    label = ""
    if "=" in text.split("://", 1)[0]:
        label, text = text.split("=", 1)
        label, text = label.strip(), text.strip()
    parts = urllib.parse.urlsplit(text.replace("{file}", "f").replace("{system}", "s").replace("{md5}", "m"))
    if parts.scheme != "https" or not parts.hostname:
        return _error("INVALID_ARGS", "Only https addresses can be used")
    state = _load_state()
    if len(state["urls"]) >= 20:
        return _error("INVALID_ARGS", "At most 20 addresses")
    state["urls"].append({"label": (label or parts.hostname)[:60], "template": text[:500]})
    _write_json(STATE_FILE, state)
    return _ok({"message": "Added " + (label or parts.hostname)})


def _remove_url(args):
    state = _load_state()
    try:
        position = int((args.get("args") or {}).get("position", args.get("position")))
    except (TypeError, ValueError):
        return _error("INVALID_ARGS", "Nothing to remove")
    if 0 <= position < len(state["urls"]):
        state["urls"].pop(position)
        _write_json(STATE_FILE, state)
    return _ok({"message": "Removed"})


def _set_retrobios(args):
    value = (args.get("values") or {}).get("retrobios")
    state = _load_state()
    state["retrobios"] = str(value).lower() == "true"
    _write_json(STATE_FILE, state)
    return _ok({"message": "retrobios " + ("on" if state["retrobios"] else "off")})


# ------------------------------------------------------------------------------------------------- the job


def _file_name(item):
    name = item["name"]
    if not SAFE_NAME.match(name):
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip("._-") or "bios.bin"
    return name


def _acquire(args):
    """The job: pick the chosen source, ask it once whether the file is there, hand droidtop the download."""
    ref = args.get("ref") or {}
    system_id = _system_id(ref.get("system"))
    item = _find_file(system_id, ref.get("file")) if system_id else None
    if item is None:
        return {"ok": False, "error": "That file is not on this plugin's list"}
    state = _load_state()
    candidates = _candidates(system_id, item, state)
    if not candidates:
        return {"ok": False, "error": "No source is turned on for this file"}
    try:
        choice = int((args.get("values") or {}).get("source", "0"))
    except (TypeError, ValueError):
        choice = 0
    if not 0 <= choice < len(candidates):
        choice = 0
    chosen = candidates[choice]
    if _declared(chosen.url):
        try:
            status, _final, headers, _body, _t = _http(chosen.url, method="HEAD")
        except HostError as error:
            return {"ok": False, "error": "%s did not answer: %s" % (chosen.label, error.message)}
        if status == 404:
            return {"ok": False, "error": "%s no longer has this file" % chosen.label}
        length = str(headers.get("content-length", ""))
        if chosen.size and length.isdigit() and int(length) not in (0, chosen.size) and status == 200:
            return {"ok": False, "error": "%s offers a file of another size than expected (%s bytes, not %d)" % (chosen.label, length, chosen.size)}
    download = {"url": chosen.url, "fileName": _file_name(item)}
    if chosen.sha256:
        download["sha256"] = chosen.sha256
    if chosen.size:
        download["size"] = chosen.size
    note = "checked by SHA-256" if chosen.sha256 else ("MD5 matches droidtop's list" if chosen.exact else "not checked: a different dump or an address of your own")
    return {"ok": True, "values": {"message": "Downloading %s (%s)" % (item["name"], note), "download": json.dumps(download)}}


# -------------------------------------------------------------------------------------------- entry points


def handle(call_json):
    try:
        call = json.loads(call_json)
    except ValueError:
        return _error("INVALID_ARGS", "not JSON")
    point = call.get("point")
    op = call.get("op")
    args = call.get("args") or {}
    try:
        if point == "library.sources":
            if op == "search":
                return _search(args)
            if op == "detail":
                return _detail(args)
        elif point == "ui.settings":
            if op == "view":
                return _ok(_settings_view(_load_state()))
            if op == "setRetrobios":
                return _set_retrobios(args)
            if op == "setItems":
                return _set_items(args)
            if op == "addUrl":
                return _add_url(args)
            if op == "removeUrl":
                return _remove_url(args)
    except HostError as error:
        return _error("PERMISSION_DENIED" if error.code == "PERMISSION_DENIED" else "FAILED", error.message)
    return _error("UNSUPPORTED", "Unsupported point/op: %s/%s" % (point, op))


_cancelled = set()


def start_job(job_id, call, progress):
    if isinstance(call, str):
        try:
            call = json.loads(call)
        except ValueError:
            call = {}
    if job_id in _cancelled:
        _cancelled.discard(job_id)
        return json.dumps({"ok": False, "error": "cancelled"})
    if call.get("point") == "library.sources" and call.get("op") == "acquire":
        progress(10, "Asking the source")
        try:
            return json.dumps(_acquire(call.get("args") or {}))
        except HostError as error:
            return json.dumps({"ok": False, "error": error.message})
    return json.dumps({"ok": False, "error": "not offered: %s %s" % (call.get("point"), call.get("op"))})


def cancel_job(job_id):
    _cancelled.add(job_id)


def on_unload():
    pass
