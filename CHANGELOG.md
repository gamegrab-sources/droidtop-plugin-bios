# Changelog

## 0.2.0

- Provides `emulator.bios@1` (list, acquire) for the Emulator setup helper; droidtop names the target and writes the file.
- Downloads carry `md5` (and `sha256` for retrobios), which the Downloads job now verifies for Internet Archive files too.

## 0.1.0

- First version: search, detail and download of BIOS, firmware and key files per system through `library.sources`;
  sources retrobios (SHA-256 checked by droidtop), the LibRetro BIOS collection and any Internet Archive item or https
  address the person adds; a settings page for the sources.
