# PSN Avatar Organizer

Decrypts PS3/PSN avatar EDAT files into PNG/JPG images with clean, readable filenames. Includes both a command-line tool and a GUI application.

## What it does

1. Scans input folder recursively for `PSNA_*.edat` files
2. Extracts Content ID from each EDAT header
3. Decrypts the NPDRM-encrypted payload using AES
4. Saves the embedded image with format: `<ContentID> - PSNA_****.png`

## Installation

```bash
pip install -r requirements.txt
```

### Dependencies

- `pycryptodome` - AES decryption (also supports `cryptography` package, or falls back to pure-Python AES)
- `Pillow` - Image handling (required for GUI)

## Usage

### GUI

```bash
python gui_app.py
```

### Command Line

```bash
python avatar_organizer.py <input_folder> [output_folder]
python avatar_organizer.py ./my_avatars ./organized -v   # verbose mode
```

Default output folder is `./organized_avatars`. The `-v` flag prints each filename as it's processed.

## Output Format

Files are named: `<Content-ID> - PSNA_<ID>.png`

Example:
```
IP9100-NPIA00001_00-AVTR000000000001 - PSNA_0001.png
UP9000-NPUA80662_00-AVATAR0000000001 - PSNA_0234.png
```

## Features

- NPDRM EDAT decryption (NPD v1-v4)
- Automatic Content ID extraction from EDAT headers
- GUI with image preview and progress tracking
- Command-line interface with verbose mode
- Deduplication and skip-already-processed logic
- Three-tier AES backend: `cryptography` > `pycryptodome` > pure-Python fallback

## Title Database

The included `serialstation_titles.csv` provides a lookup table mapping Content IDs to game/application titles, used by the GUI to display friendly names.

## Credits

Decryption keys and NPD header layout derived from [make_npdata](https://github.com/AceTrainerAndrew/make_npdata) by Hykem.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
