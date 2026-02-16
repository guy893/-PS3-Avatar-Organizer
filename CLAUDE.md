# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Single-file Python tool that decrypts PS3/PSN avatar EDAT files into PNG/JPG images. It reads the NPDRM-encrypted `PSNA_*.edat` files, extracts the Content ID from the EDAT header (offset `0x10`, 48 bytes), decrypts the payload using AES, locates the embedded image (PNG or JPEG magic bytes), and saves it with the naming format `<ContentID> - <PSNA_name>.png`.

## Running

```bash
pip install -r requirements.txt
python avatar_organizer.py <input_folder> [output_folder]
python avatar_organizer.py ./input ./organized -v   # verbose mode
```

Default output folder is `./organized_avatars`. The `-v` flag prints each filename as it's processed.

## Architecture

Everything is in `avatar_organizer.py` (~450 lines). Key sections:

- **AES Backend Selection** (top): Three-tier fallback — `cryptography` (fastest) → `pycryptodome` → pure-Python AES-128. The active backend is stored in `_BACKEND`.
- **EDAT Decryption** (`decrypt_edat`): Implements PS3 NPDRM decryption. Handles NPD version differences (v4 uses `EDAT_KEY_1`, others use `EDAT_KEY_0`), various flag combinations (encrypted key, 0x10, 0x02, 0x20), and block-by-block AES-CBC decryption. Does not support compressed EDAT.
- **Avatar Organization** (`organize_avatars`): Scans recursively for EDAT files, deduplicates by stem name, skips already-existing output files, decrypts, and reports progress.

## Key Constants

The decryption keys (`KLIC_FREE`, `EDAT_KEY_0`, `EDAT_KEY_1`) and NPD header layout are derived from `make_npdata` by Hykem (GPL v3).

## Dependencies

Only `pycryptodome` is listed in requirements.txt, but the code also supports `cryptography` as a preferred alternative, or runs with no crypto dependency at all (pure Python fallback).
