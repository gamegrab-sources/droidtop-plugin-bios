# Changelog

## 0.3.2

- Declares that it serves every system (`"*"`): BIOS and firmware files exist for consoles and PCs alike.
  Needs a droidtop that reads `systems` on `library.sources` (docs/plugin-api.md 1.6); an older
  droidtop ignores the field and lists the source everywhere, as before.

## 0.3.1

- A search no longer lists a system for a word inside its name: "witch" listed
  every Nintendo Switch file in a PC search (rig, 2026-10-14). Systems match on
  the start of a word or of their id.

## 0.3.0

- Shorter text: one-line description, no About row in Settings (the catalog
  already marks the plugin Unofficial), short hints for sources and your own
  addresses, no note on how droidtop matches files.
- droidtop's contract 2.1 host name net.request (needs droidtop
  0.2.0-dev.1767 or newer) and 48 KiB data reads.

## 0.2.0

- Provides `emulator.bios@1` (list, acquire) for the Emulator setup helper; droidtop names the target and writes the file.
- Downloads carry `md5` (and `sha256` for retrobios), which the Downloads job now verifies for Internet Archive files too.

## 0.1.0

- First version: search, detail and download of BIOS, firmware and key files per system through `library.sources`;
  sources retrobios (SHA-256 checked by droidtop), the LibRetro BIOS collection and any Internet Archive item or https
  address the person adds; a settings page for the sources.
