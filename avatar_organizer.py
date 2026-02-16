#!/usr/bin/env python3
"""
PSN Avatar Organizer
Decrypts PSN avatar EDAT files and organizes them by Content ID.

Algorithm derived from make_npdata by Hykem (GPL v3).
Uses 'cryptography' library if available for fast AES (~100x speedup),
falls back to pure Python AES otherwise.

Usage:
    python avatar_organizer.py <input_folder> [output_folder]
"""

import os
import sys
import struct
import time
import shutil
import json
from enum import IntEnum
from pathlib import Path

# ============================================================
# AES Backend Selection
# ============================================================

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    _BACKEND = 'cryptography'

    def _aes_ecb_encrypt(key, block):
        c = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
        e = c.encryptor()
        return e.update(block) + e.finalize()

    def _aes_cbc_decrypt(key, iv, data):
        c = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        d = c.decryptor()
        return d.update(data) + d.finalize()

except ImportError:
    try:
        from Crypto.Cipher import AES as _AES

        _BACKEND = 'pycryptodome'

        def _aes_ecb_encrypt(key, block):
            return _AES.new(key, _AES.MODE_ECB).encrypt(block)

        def _aes_cbc_decrypt(key, iv, data):
            return _AES.new(key, _AES.MODE_CBC, iv).decrypt(data)

    except ImportError:
        _BACKEND = 'python'

        # ---- Pure Python AES-128 fallback ----
        _SBOX = bytes([
            0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
            0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
            0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
            0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
            0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
            0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
            0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
            0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
            0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
            0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
            0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
            0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
            0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
            0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
            0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
            0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
        ])
        _t = bytearray(256)
        for _i in range(256):
            _t[_SBOX[_i]] = _i
        _INV_SBOX = bytes(_t)
        del _t

        _RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36)

        def _gmul(a, b):
            p = 0
            for _ in range(8):
                if b & 1: p ^= a
                hi = a & 0x80
                a = (a << 1) & 0xff
                if hi: a ^= 0x1b
                b >>= 1
            return p

        _GM9  = bytes([_gmul(i, 9)  for i in range(256)])
        _GM11 = bytes([_gmul(i, 11) for i in range(256)])
        _GM13 = bytes([_gmul(i, 13) for i in range(256)])
        _GM14 = bytes([_gmul(i, 14) for i in range(256)])

        def _xtime(a):
            return ((a << 1) ^ 0x1b) & 0xff if a & 0x80 else (a << 1) & 0xff

        def _key_expansion(key):
            w = list(key)
            S = _SBOX; R = _RCON
            for i in range(4, 44):
                t = w[(i-1)*4:(i-1)*4+4]
                if i % 4 == 0:
                    t = [S[t[1]] ^ R[i//4-1], S[t[2]], S[t[3]], S[t[0]]]
                w.extend([w[(i-4)*4+j] ^ t[j] for j in range(4)])
            return bytes(w)

        def _aes_encrypt_block(ks, block):
            s = bytearray(block)
            S = _SBOX
            for i in range(16): s[i] ^= ks[i]
            for rnd in range(1, 11):
                for i in range(16): s[i] = S[s[i]]
                s[1],s[5],s[9],s[13] = s[5],s[9],s[13],s[1]
                s[2],s[6],s[10],s[14] = s[10],s[14],s[2],s[6]
                s[3],s[7],s[11],s[15] = s[15],s[3],s[7],s[11]
                if rnd < 10:
                    for c in range(4):
                        c4 = c*4
                        a0,a1,a2,a3 = s[c4],s[c4+1],s[c4+2],s[c4+3]
                        t = a0^a1^a2^a3
                        s[c4]   = a0 ^ _xtime(a0^a1) ^ t
                        s[c4+1] = a1 ^ _xtime(a1^a2) ^ t
                        s[c4+2] = a2 ^ _xtime(a2^a3) ^ t
                        s[c4+3] = a3 ^ _xtime(a3^a0) ^ t
                ro = rnd * 16
                for i in range(16): s[i] ^= ks[ro+i]
            return bytes(s)

        def _aes_decrypt_block(ks, block):
            s = bytearray(block)
            IS = _INV_SBOX
            G9,G11,G13,G14 = _GM9,_GM11,_GM13,_GM14
            for i in range(16): s[i] ^= ks[160+i]
            for rnd in range(9, -1, -1):
                s[1],s[5],s[9],s[13] = s[13],s[1],s[5],s[9]
                s[2],s[6],s[10],s[14] = s[10],s[14],s[2],s[6]
                s[3],s[7],s[11],s[15] = s[7],s[11],s[15],s[3]
                for i in range(16): s[i] = IS[s[i]]
                ro = rnd * 16
                for i in range(16): s[i] ^= ks[ro+i]
                if rnd > 0:
                    for c in range(4):
                        c4 = c*4
                        a0,a1,a2,a3 = s[c4],s[c4+1],s[c4+2],s[c4+3]
                        s[c4]   = G14[a0]^G11[a1]^G13[a2]^G9[a3]
                        s[c4+1] = G9[a0]^G14[a1]^G11[a2]^G13[a3]
                        s[c4+2] = G13[a0]^G9[a1]^G14[a2]^G11[a3]
                        s[c4+3] = G11[a0]^G13[a1]^G9[a2]^G14[a3]
            return bytes(s)

        def _aes_ecb_encrypt(key, block):
            return _aes_encrypt_block(_key_expansion(key), block)

        def _aes_cbc_decrypt(key, iv, data):
            ks = _key_expansion(key)
            out = bytearray()
            prev = iv
            for i in range(0, len(data), 16):
                ct = data[i:i+16]
                raw = _aes_decrypt_block(ks, ct)
                out.extend(bytes(a^b for a,b in zip(raw, prev)))
                prev = ct
            return bytes(out)


# ============================================================
# PS3 NPDRM / EDAT Constants
# ============================================================

KLIC_FREE  = bytes.fromhex('72F990788F9CFF745725F08E4C128387')
EDAT_KEY_0 = bytes.fromhex('BE959CA8308DEFA2E5E180C63712A9AE')
EDAT_KEY_1 = bytes.fromhex('4CA9C14B01C95309969BEC68AA0BC081')

FLAG_COMPRESSED    = 0x00000001
FLAG_0x02          = 0x00000002
FLAG_ENCRYPTED_KEY = 0x00000008
FLAG_0x10          = 0x00000010
FLAG_0x20          = 0x00000020


# ============================================================
# EDAT Decryption
# ============================================================

def decrypt_edat(edat_path, output_path):
    """
    Decrypt a PSN avatar EDAT file and extract the image.
    Returns (success: bool, message: str).
    """
    try:
        data = open(edat_path, 'rb').read()

        if len(data) < 0x100 or data[0:4] != b'NPD\x00':
            return False, "Invalid NPD file"

        npd_version = struct.unpack('>I', data[4:8])[0]
        license_type = struct.unpack('>I', data[8:12])[0]
        digest = data[0x40:0x50]
        dev_hash = data[0x60:0x70]

        flags = struct.unpack('>I', data[0x80:0x84])[0]
        block_size = struct.unpack('>I', data[0x84:0x88])[0]
        data_size = struct.unpack('>Q', data[0x88:0x90])[0]

        if not block_size or not data_size:
            return False, "Invalid block/data size"

        block_num = (data_size + block_size - 1) // block_size

        lic_type = license_type & 0x3
        if lic_type not in (0x2, 0x3):
            return False, f"Unsupported license type {license_type}"

        # Type 3 = free license (KLIC_FREE), Type 2 = local/disc license
        # For PSN avatars, KLIC_FREE works for most type 2 EDATs as well.
        crypt_key = KLIC_FREE
        edat_key = EDAT_KEY_1 if npd_version == 4 else EDAT_KEY_0
        meta_size = 0x20 if (flags & (FLAG_COMPRESSED | FLAG_0x20)) else 0x10
        meta_offset = 0x100
        dev_hash_12 = dev_hash[0:12]
        has_enc_key = bool(flags & FLAG_ENCRYPTED_KEY)
        has_0x10 = bool(flags & FLAG_0x10)
        has_0x02 = bool(flags & FLAG_0x02)
        zeros16 = bytes(16)

        output = bytearray()

        for i in range(block_num):
            is_last = (i == block_num - 1)
            this_size = (data_size % block_size) if (is_last and data_size % block_size) else block_size
            pad_len = (this_size + 15) & ~15

            if flags & FLAG_COMPRESSED:
                return False, "Compressed EDAT not supported"
            elif flags & FLAG_0x20:
                data_off = meta_offset + i * (meta_size + block_size) + meta_size
            else:
                data_off = meta_offset + i * block_size + block_num * meta_size

            enc = data[data_off:data_off + pad_len]
            if len(enc) < pad_len:
                enc += b'\x00' * (pad_len - len(enc))

            b_key = dev_hash_12 + struct.pack('>I', i)
            key_result = _aes_ecb_encrypt(crypt_key, b_key)

            if has_0x10:
                key_result = _aes_ecb_encrypt(crypt_key, key_result)

            if has_enc_key:
                key_final = _aes_cbc_decrypt(edat_key, zeros16, key_result)
                iv_final = digest
            else:
                key_final = key_result
                iv_final = digest if npd_version > 1 else zeros16

            if has_0x02:
                dec = enc
            else:
                dec = _aes_cbc_decrypt(key_final, iv_final, enc)

            output.extend(dec[:this_size])

        output = bytes(output[:data_size])

        png_off = output.find(b'\x89PNG\r\n\x1a\n')
        jpg_off = output.find(b'\xff\xd8\xff')

        if png_off >= 0:
            img_data = output[png_off:]
            ext = '.png'
        elif jpg_off >= 0:
            img_data = output[jpg_off:]
            ext = '.jpg'
        else:
            return False, "No image found"

        out_str = str(output_path)
        base = out_str.rsplit('.', 1)[0] if '.' in os.path.basename(out_str) else out_str
        output_path = Path(base + ext)

        with open(output_path, 'wb') as f:
            f.write(img_data)

        return True, f"{len(img_data):,} bytes"

    except Exception as e:
        return False, str(e)


# ============================================================
# Progress Bar
# ============================================================

class ProgressBar:
    """Simple terminal progress bar."""

    def __init__(self, total, width=40):
        self.total = max(total, 1)
        self.width = width
        self.current = 0
        self.ok = 0
        self.fail = 0
        self.skip = 0
        self.start_time = time.time()
        self._draw()

    def _draw(self):
        frac = self.current / self.total
        filled = int(self.width * frac)
        bar = '\u2588' * filled + '\u2591' * (self.width - filled)
        pct = frac * 100

        elapsed = time.time() - self.start_time
        if self.current > 0 and self.current < self.total:
            eta = elapsed / self.current * (self.total - self.current)
            time_str = f'ETA {eta:.0f}s'
        elif self.current >= self.total:
            time_str = f'{elapsed:.1f}s'
        else:
            time_str = '...'

        status = f'OK:{self.ok} Skip:{self.skip} Fail:{self.fail}'
        line = f'\r|{bar}| {self.current}/{self.total} ({pct:.0f}%) {time_str}  {status}'
        sys.stderr.write(line)
        sys.stderr.flush()

    def update(self, result='ok'):
        self.current += 1
        if result == 'ok':
            self.ok += 1
        elif result == 'skip':
            self.skip += 1
        else:
            self.fail += 1
        self._draw()

    def finish(self):
        self._draw()
        sys.stderr.write('\n')
        sys.stderr.flush()


# ============================================================
# Avatar Organization
# ============================================================

def read_content_id(filepath):
    with open(filepath, 'rb') as f:
        f.seek(0x10)
        return f.read(0x30).decode('utf-8', errors='ignore').rstrip('\x00')


def _read_license_type(filepath):
    """Read the license type field from an EDAT header."""
    try:
        with open(filepath, 'rb') as f:
            f.seek(8)
            return struct.unpack('>I', f.read(4))[0] & 0x3
    except Exception:
        return 0


# ============================================================
# Organization Modes & Region Detection
# ============================================================

class OrgMode(IntEnum):
    REGION_GAME_PNG = 1        # Region/Game [TitleID] (count)/avatar.png
    REGION_GAME_PNG_EDAT = 2   # Region/Game [TitleID] (count)/avatar.png + avatar.edat
    REGION_GAME_SEPARATED = 3  # Region/Game [TitleID] (count)/previews/ + psn_avatar/
    FLAT = 4                   # All PNGs in one folder

REGION_MAP = {
    'UP': 'US', 'US': 'US', 'UC': 'US', 'UT': 'US',
    'EP': 'EU', 'EC': 'EU', 'ET': 'EU',
    'JP': 'Japan', 'JC': 'Japan', 'JT': 'Japan',
    'HP': 'Asia', 'HC': 'Asia', 'HT': 'Asia',
    'KP': 'Korea', 'KC': 'Korea', 'KT': 'Korea',
    'IP': 'Internal',
}


def detect_region(content_id):
    """Return region name from content ID prefix (e.g. 'UP0082-...' -> 'US')."""
    prefix = content_id[:2].upper()
    return REGION_MAP.get(prefix, 'Unknown')


def extract_title_id(content_id):
    """Extract Title ID from content ID (e.g. 'UP0082-ULUS10566_00-...' -> 'ULUS10566')."""
    dash = content_id.find('-')
    if dash < 0:
        return None
    rest = content_id[dash + 1:]
    under = rest.find('_')
    if under < 0:
        return rest
    return rest[:under]


def _sanitize_filename(name):
    """Remove or replace characters illegal in Windows file/folder names."""
    # Illegal: \ / : * ? " < > |
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, '-')
    # Strip trailing dots/spaces (Windows restriction)
    name = name.rstrip('. ')
    return name


def build_folder_name(game_name, title_id, show_game, show_title_id):
    """Build game folder name from toggle options."""
    parts = []
    if show_game and game_name:
        parts.append(game_name)
    if show_title_id and title_id:
        parts.append(f'[{title_id}]')
    return ' '.join(parts) if parts else (title_id or 'Unknown')


def organize_with_mode(input_folder, output_folder, mode=None, title_lookup=None,
                       progress_cb=None, options=None):
    """
    Decrypt and organize EDAT files.

    When ``options`` dict is provided, it controls the output layout:
        content_id_in_filename (bool): include Content ID in PNG filename
        show_game_name (bool): include game name in folder
        show_title_id (bool): include [TitleID] in folder
        show_count (bool): append (count) to folder name
        separate_folders (bool): PNGs in previews/, EDATs in psn_avatar/
                                 (when off, PNGs + EDATs together)

    When ``options`` is None, falls back to legacy OrgMode enum behaviour.

    Returns:
        dict with keys: ok, skip, fail, total, failures, output_pngs
    """
    input_path = Path(input_folder)
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)
    if title_lookup is None:
        title_lookup = {}

    # Resolve options
    if options is not None:
        use_content_id = options.get('content_id_in_filename', True)
        show_game = options.get('show_game_name', True)
        show_tid = options.get('show_title_id', True)
        show_count = options.get('show_count', True)
        separate = options.get('separate_folders', False)
        # With toggles, we always sort into Region/Game folders
        use_folders = True
    elif mode is not None:
        # Legacy OrgMode support
        use_content_id = True
        show_game = True
        show_tid = True
        show_count = True
        use_folders = (mode != OrgMode.FLAT)
        separate = (mode == OrgMode.REGION_GAME_SEPARATED)
    else:
        use_content_id = True
        show_game = True
        show_tid = True
        show_count = True
        use_folders = False
        separate = False

    # Scan for EDAT files
    edat_files = []
    for pattern in ['PSNA_*.edat', 'PSNA_*.EDAT', 'psna_*.edat']:
        edat_files.extend(input_path.rglob(pattern))
    edat_files = sorted(set(edat_files))

    # Filter out Windows copy duplicates (e.g. "PSNA_xxx - Copy.edat")
    edat_files = [f for f in edat_files
                  if ' - copy' not in f.stem.lower()
                  and not f.stem.lower().endswith(' - copy')]

    # Deduplicate by stem, preferring license type 3 (free) over type 2
    seen = {}  # stem -> file path
    for f in edat_files:
        name = f.stem
        if name not in seen:
            seen[name] = f
        else:
            existing_lic = _read_license_type(seen[name])
            new_lic = _read_license_type(f)
            if new_lic > existing_lic:
                seen[name] = f
    unique_files = list(seen.values())

    total = len(unique_files)
    stats = {'ok': 0, 'skip': 0, 'fail': 0, 'total': total, 'failures': [],
             'output_pngs': []}

    if total == 0:
        return stats

    game_folder_counts = {}  # folder_key -> {'region': ..., 'count': ...}

    for idx, edat_file in enumerate(unique_files):
        psna_name = edat_file.stem

        try:
            content_id = read_content_id(edat_file)

            # Build output filename
            if use_content_id:
                output_name = _sanitize_filename(f"{content_id} - {psna_name}") + ".png"
            else:
                output_name = f"{psna_name}.png"

            if not use_folders:
                # Flat mode
                out_file = output_path / output_name

                if out_file.exists():
                    stats['skip'] += 1
                    if progress_cb:
                        progress_cb(idx + 1, total, 'skip', psna_name)
                    continue

                success, msg = decrypt_edat(edat_file, out_file)
                if success:
                    stats['ok'] += 1
                    stats['output_pngs'].append(str(out_file))
                    if progress_cb:
                        progress_cb(idx + 1, total, 'ok', psna_name)
                else:
                    stats['fail'] += 1
                    stats['failures'].append((psna_name, msg))
                    if progress_cb:
                        progress_cb(idx + 1, total, 'fail', psna_name)
            else:
                # Region/Game folder structure
                region = detect_region(content_id)
                title_id = extract_title_id(content_id)
                game_name = title_lookup.get(title_id, '') if title_id else ''
                # If no lookup hit, fall back to title_id as game_name
                if not game_name:
                    game_name = title_id or 'Unknown'

                folder_key = _sanitize_filename(build_folder_name(
                    game_name if show_game else '',
                    title_id, show_game, show_tid))
                game_base = output_path / region / folder_key

                if folder_key not in game_folder_counts:
                    game_folder_counts[folder_key] = {'region': region, 'count': 0}

                if separate:
                    png_dir = game_base / 'previews'
                    edat_dir = game_base / 'psn_avatar'
                    png_dir.mkdir(parents=True, exist_ok=True)
                    edat_dir.mkdir(parents=True, exist_ok=True)
                    out_file = png_dir / output_name
                else:
                    game_base.mkdir(parents=True, exist_ok=True)
                    out_file = game_base / output_name

                if out_file.exists():
                    game_folder_counts[folder_key]['count'] += 1
                    stats['skip'] += 1
                    if progress_cb:
                        progress_cb(idx + 1, total, 'skip', psna_name)
                    continue

                success, msg = decrypt_edat(edat_file, out_file)

                if success:
                    stats['ok'] += 1
                    stats['output_pngs'].append(str(out_file))
                    game_folder_counts[folder_key]['count'] += 1

                    # Copy EDAT alongside
                    if separate:
                        edat_dest = edat_dir / edat_file.name
                    else:
                        edat_dest = game_base / edat_file.name
                    if not edat_dest.exists():
                        shutil.copy2(edat_file, edat_dest)

                    if progress_cb:
                        progress_cb(idx + 1, total, 'ok', psna_name)
                else:
                    stats['fail'] += 1
                    stats['failures'].append((psna_name, msg))
                    if progress_cb:
                        progress_cb(idx + 1, total, 'fail', psna_name)

        except Exception as e:
            stats['fail'] += 1
            stats['failures'].append((psna_name, str(e)))
            if progress_cb:
                progress_cb(idx + 1, total, 'fail', psna_name)

    # Rename game folders to include avatar count (based on actual PNG count)
    if use_folders and show_count:
        import re
        for folder_key, info in game_folder_counts.items():
            region = info['region']
            region_dir = output_path / region

            # Find the actual folder — might already have a count suffix
            # from a previous run, e.g. "Game [ID] (50)"
            actual_folder = None
            if (region_dir / folder_key).exists():
                actual_folder = region_dir / folder_key
            else:
                # Search for folder_key with any existing (N) suffix
                pattern = re.escape(folder_key) + r' \(\d+\)$'
                if region_dir.exists():
                    for d in region_dir.iterdir():
                        if d.is_dir() and re.match(pattern, d.name):
                            actual_folder = d
                            break

            if actual_folder is None:
                continue

            # Count actual PNGs in the folder (including subfolders)
            png_count = len(list(actual_folder.rglob('*.png')))
            if png_count == 0:
                png_count = info['count']

            new_name = f"{folder_key} ({png_count})"
            new_path = region_dir / new_name
            if actual_folder.name != new_name:
                if not new_path.exists():
                    actual_folder.rename(new_path)

    return stats


def organize_avatars(input_folder, output_folder, verbose=False):
    input_path = Path(input_folder)
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"Scanning {input_folder}...")

    edat_files = []
    for pattern in ['PSNA_*.edat', 'PSNA_*.EDAT', 'psna_*.edat']:
        edat_files.extend(input_path.rglob(pattern))
    edat_files = sorted(set(edat_files))

    if not edat_files:
        print("No PSNA_*.edat files found!")
        return

    print(f"Found {len(edat_files)} EDAT files  (AES backend: {_BACKEND})\n")

    seen = set()
    unique_files = []
    for f in edat_files:
        name = f.stem
        if name not in seen:
            seen.add(name)
            unique_files.append(f)

    if len(unique_files) < len(edat_files):
        print(f"  ({len(edat_files) - len(unique_files)} duplicates removed)\n")

    bar = ProgressBar(len(unique_files))
    failures = []

    for edat_file in unique_files:
        psna_name = edat_file.stem

        try:
            content_id = read_content_id(edat_file)
            output_name = f"{content_id} - {psna_name}.png"
            output_file = output_path / output_name

            if output_file.exists():
                bar.update('skip')
                continue

            success, message = decrypt_edat(edat_file, output_file)

            if success:
                bar.update('ok')
                if verbose:
                    sys.stderr.write(f'\n  OK  {output_name}\n')
            else:
                bar.update('fail')
                failures.append((psna_name, message))

        except Exception as e:
            bar.update('fail')
            failures.append((psna_name, str(e)))

    bar.finish()

    elapsed = time.time() - bar.start_time
    rate = bar.ok / elapsed if elapsed > 0 and bar.ok > 0 else 0

    print(f"\nDone in {elapsed:.1f}s ({rate:.1f} files/sec)")
    print(f"  Decrypted: {bar.ok}  |  Skipped: {bar.skip}  |  Failed: {bar.fail}")
    print(f"  Output: {output_path.absolute()}")

    if failures:
        print(f"\nFailed files:")
        for name, msg in failures[:20]:
            print(f"  {name}: {msg}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")


def main():
    if len(sys.argv) < 2:
        print("PSN Avatar Organizer")
        print()
        print("Usage:")
        print(f"  python {sys.argv[0]} <input_folder> [output_folder]")
        print()
        print("  -v  Verbose output (print each filename)")
        print()
        print("Example:")
        print(f'  python {sys.argv[0]} "/path/to/PS3/avatars" ./organized')
        sys.exit(1)

    verbose = '-v' in sys.argv
    args = [a for a in sys.argv[1:] if a != '-v']

    input_folder = args[0]
    output_folder = args[1] if len(args) > 1 else './organized_avatars'

    if not os.path.exists(input_folder):
        print(f"Error: Input folder '{input_folder}' does not exist")
        sys.exit(1)

    organize_avatars(input_folder, output_folder, verbose=verbose)


if __name__ == '__main__':
    main()
