#!/usr/bin/env python3
"""
★ PS3 Avatar Organizer — GUI
Decrypts PS3/PSN avatar EDAT files and organizes them with a modern tkinter interface.
"""

import csv
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from pathlib import Path
from queue import Queue, Empty

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

from avatar_organizer import (
    organize_with_mode,
    _BACKEND,
)

import ctypes
import time

APP_NAME = '\u2605 PS3 Avatar Organizer'


# ============================================================
# Windows Taskbar Progress (ITaskbarList3 via raw ctypes COM)
# ============================================================

class TaskbarProgress:
    """Set progress on the Windows taskbar icon using raw ctypes COM."""
    TBPF_NOPROGRESS = 0x00
    TBPF_NORMAL = 0x02
    TBPF_ERROR = 0x04
    TBPF_PAUSED = 0x08

    def __init__(self, hwnd):
        self._hwnd = hwnd
        self._pv = None
        try:
            import ctypes.wintypes as wt
            from ctypes import byref, POINTER, HRESULT, c_void_p

            CLSID_TaskbarList = (ctypes.c_byte * 16)(
                *bytes.fromhex('44F3FD56 6DFD d011 958A 006097C9A090'
                               .replace(' ', '')))
            IID_ITaskbarList3 = (ctypes.c_byte * 16)(
                *bytes.fromhex('91FB1AEA 289E 4B86 90E9 9E9F8A5EEFAF'
                               .replace(' ', '')))

            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)
            pv = c_void_p()
            hr = ole32.CoCreateInstance(
                byref(CLSID_TaskbarList), None, 1 | 4,  # CLSCTX_ALL
                byref(IID_ITaskbarList3), byref(pv))
            if hr == 0 and pv.value:
                self._pv = pv.value
                # vtable: IUnknown(3) + ITaskbarList(4) + ITaskbarList2(1)
                # + SetProgressValue(idx 9), SetProgressState(idx 10)
                vtable = ctypes.cast(
                    self._pv, POINTER(POINTER(c_void_p)))[0]
                # SetProgressValue(hwnd, completed, total)
                SPVT = ctypes.CFUNCTYPE(HRESULT, c_void_p, wt.HWND,
                                        ctypes.c_uint64, ctypes.c_uint64)
                self._SetProgressValue = SPVT(vtable[9])
                # SetProgressState(hwnd, flags)
                SPST = ctypes.CFUNCTYPE(HRESULT, c_void_p, wt.HWND,
                                        ctypes.c_int)
                self._SetProgressState = SPST(vtable[10])
            else:
                self._pv = None
        except Exception:
            self._pv = None

    def set_progress(self, current, total):
        if self._pv is None:
            return
        try:
            self._SetProgressValue(self._pv, self._hwnd, current, total)
        except Exception:
            pass

    def set_state(self, state):
        if self._pv is None:
            return
        try:
            self._SetProgressState(self._pv, self._hwnd, state)
        except Exception:
            pass

    def clear(self):
        self.set_state(self.TBPF_NOPROGRESS)


def _try_taskbar_progress(hwnd):
    """Try to create a TaskbarProgress; returns None on failure."""
    try:
        tp = TaskbarProgress(hwnd)
        return tp if tp._pv else None
    except Exception:
        return None

# ============================================================
# Color palette
# ============================================================

BG           = '#0f0f0f'
BG_CARD      = '#1a1a24'
BG_ENTRY     = '#161622'
BG_LOG       = '#101018'
FG           = '#e0e0e0'
FG_DIM       = '#888899'
FG_ACCENT    = '#7eaaff'
FG_HEADING   = '#a0c4ff'
FG_OK        = '#66d9a0'
FG_SKIP      = '#e0c97a'
FG_FAIL      = '#ff6b6b'
FG_DONE      = '#5ce0c0'
FG_PREVIEW   = '#9cb8e8'
BORDER       = '#2a2a3a'
BTN_BG       = '#2c3e6e'
BTN_FG       = '#e0e8ff'
BTN_ACTIVE   = '#3d5599'
PROGRESS_BG  = '#1a1a2e'
PROGRESS_FG  = '#5b8aff'

# ============================================================
# Paths & Config
# ============================================================

def _app_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _config_path():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent / 'psn_avatar_gui.json'
    return Path(__file__).parent / 'psn_avatar_gui.json'


def load_config():
    p = _config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}


def save_config(cfg):
    try:
        _config_path().write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    except Exception:
        pass


def load_titles():
    titles = {}
    csv_path = _app_dir() / 'serialstation_titles.csv'
    if csv_path.exists():
        try:
            with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    tid = row.get('title_id', '').strip()
                    name = row.get('title_name', '').strip()
                    if tid and name:
                        titles[tid] = name
                        no_dash = tid.replace('-', '')
                        if no_dash != tid:
                            titles[no_dash] = name
        except Exception:
            pass
    json_path = _app_dir() / 'titles.json'
    if json_path.exists():
        try:
            extra = json.loads(json_path.read_text(encoding='utf-8'))
            titles.update(extra)
        except Exception:
            pass
    return titles


# ============================================================
# Rounded-card helper
# ============================================================

def make_card(parent, **kw):
    """Create a dark card frame with a subtle border."""
    f = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER,
                 highlightthickness=1, **kw)
    return f


# ============================================================
# Main Application
# ============================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry('820x700')
        self.minsize(640, 540)
        self.configure(bg=BG)

        # Window icon
        icon_path = _app_dir() / 'app_icon.ico'
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

        self._cfg = load_config()
        self._titles = load_titles()
        self._queue = Queue()
        self._running = False
        self._start_time = 0.0
        self._taskbar = None

        self._setup_styles()
        self._build_ui()
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self._poll_queue()

        # Init taskbar progress after window is mapped
        self.after(500, self._init_taskbar)

        title_count = len(set(self._titles.values()))
        self._status_var.set(
            f'AES: {_BACKEND}  \u2502  {title_count:,} games in title database')

    # ---- Styles ----

    def _setup_styles(self):
        s = ttk.Style(self)
        s.theme_use('clam')

        s.configure('TFrame', background=BG)
        s.configure('Card.TFrame', background=BG_CARD)
        s.configure('TLabel', background=BG, foreground=FG,
                     font=('Segoe UI', 10))
        s.configure('Card.TLabel', background=BG_CARD, foreground=FG,
                     font=('Segoe UI', 10))
        s.configure('Dim.TLabel', background=BG, foreground=FG_DIM,
                     font=('Segoe UI', 9))
        s.configure('Heading.TLabel', background=BG, foreground=FG_HEADING,
                     font=('Segoe UI', 11, 'bold'))
        s.configure('CardHeading.TLabel', background=BG_CARD,
                     foreground=FG_HEADING, font=('Segoe UI', 10, 'bold'))
        s.configure('Status.TLabel', background='#0a0a12', foreground=FG_DIM,
                     font=('Segoe UI', 9))

        s.configure('TEntry', fieldbackground=BG_ENTRY, foreground=FG,
                     insertcolor=FG, borderwidth=1, relief='flat')

        s.configure('Accent.TButton', background=BTN_BG, foreground=BTN_FG,
                     font=('Segoe UI', 10, 'bold'), padding=(16, 8),
                     borderwidth=0)
        s.map('Accent.TButton',
              background=[('active', BTN_ACTIVE), ('disabled', '#1a1a2e')],
              foreground=[('disabled', '#555566')])

        s.configure('Browse.TButton', background='#1e2030',
                     foreground=FG_ACCENT, font=('Segoe UI', 9),
                     padding=(10, 4), borderwidth=0)
        s.map('Browse.TButton',
              background=[('active', '#2a3050')])

        s.configure('TCheckbutton', background=BG_CARD, foreground=FG,
                     font=('Segoe UI', 10))
        s.map('TCheckbutton',
              background=[('active', BG_CARD)])

        s.configure('Horizontal.TProgressbar',
                     troughcolor=PROGRESS_BG, background=PROGRESS_FG,
                     borderwidth=0, thickness=10)

    # ---- UI Construction ----

    def _build_ui(self):
        # Main container with padding
        main = tk.Frame(self, bg=BG)
        main.pack(fill='both', expand=True, padx=16, pady=12)

        # -- Header with logo --
        header = tk.Frame(main, bg=BG)
        header.pack(fill='x', pady=(0, 12))

        self._logo_photo = None
        logo_path = _app_dir() / 'app_icon.png'
        if logo_path.exists() and Image is not None:
            try:
                img = Image.open(str(logo_path))
                img.thumbnail((48, 48), Image.LANCZOS)
                self._logo_photo = ImageTk.PhotoImage(img)
                logo_lbl = tk.Label(header, image=self._logo_photo, bg=BG)
                logo_lbl.pack(side='left', padx=(0, 12))
            except Exception:
                pass

        title_lbl = tk.Label(header, text=APP_NAME, bg=BG, fg=FG_HEADING,
                             font=('Segoe UI', 18, 'bold'))
        title_lbl.pack(side='left')

        version_lbl = tk.Label(header, text='v1.0', bg=BG, fg=FG_DIM,
                               font=('Segoe UI', 10))
        version_lbl.pack(side='left', padx=(8, 0), pady=(8, 0))

        # -- Folder selectors card --
        folder_card = make_card(main)
        folder_card.pack(fill='x', pady=(0, 8))
        folder_inner = tk.Frame(folder_card, bg=BG_CARD)
        folder_inner.pack(fill='x', padx=14, pady=10)

        tk.Label(folder_inner, text='Input Folder', bg=BG_CARD, fg=FG_DIM,
                 font=('Segoe UI', 9)).grid(row=0, column=0, sticky='w')
        self._input_var = tk.StringVar(value=self._cfg.get('input', ''))
        in_entry = ttk.Entry(folder_inner, textvariable=self._input_var,
                             width=58)
        in_entry.grid(row=0, column=1, sticky='ew', padx=(8, 6))
        ttk.Button(folder_inner, text='Browse',
                   style='Browse.TButton',
                   command=self._browse_input).grid(row=0, column=2)

        tk.Label(folder_inner, text='Output Folder', bg=BG_CARD, fg=FG_DIM,
                 font=('Segoe UI', 9)).grid(row=1, column=0, sticky='w',
                                            pady=(6, 0))
        self._output_var = tk.StringVar(value=self._cfg.get('output', ''))
        out_entry = ttk.Entry(folder_inner, textvariable=self._output_var,
                              width=58)
        out_entry.grid(row=1, column=1, sticky='ew', padx=(8, 6), pady=(6, 0))
        ttk.Button(folder_inner, text='Browse',
                   style='Browse.TButton',
                   command=self._browse_output).grid(row=1, column=2,
                                                      pady=(6, 0))
        folder_inner.columnconfigure(1, weight=1)

        # -- Organization options card --
        opts_card = make_card(main)
        opts_card.pack(fill='x', pady=(0, 8))
        opts_inner = tk.Frame(opts_card, bg=BG_CARD)
        opts_inner.pack(fill='x', padx=14, pady=10)

        tk.Label(opts_inner, text='Organization Options', bg=BG_CARD,
                 fg=FG_HEADING,
                 font=('Segoe UI', 10, 'bold')).grid(
                     row=0, column=0, columnspan=2, sticky='w', pady=(0, 6))

        self._opt_content_id = tk.BooleanVar(
            value=self._cfg.get('content_id_in_filename', True))
        ttk.Checkbutton(
            opts_inner, text='Include Content ID in filenames',
            variable=self._opt_content_id,
            command=self._update_preview,
        ).grid(row=1, column=0, sticky='w', columnspan=2, pady=1)

        self._opt_game_name = tk.BooleanVar(
            value=self._cfg.get('show_game_name', True))
        ttk.Checkbutton(
            opts_inner, text='Game Name in folder',
            variable=self._opt_game_name,
            command=self._validate_folder_opts,
        ).grid(row=2, column=0, sticky='w', pady=1)

        self._opt_title_id = tk.BooleanVar(
            value=self._cfg.get('show_title_id', True))
        ttk.Checkbutton(
            opts_inner, text='Title ID in folder',
            variable=self._opt_title_id,
            command=self._validate_folder_opts,
        ).grid(row=2, column=1, sticky='w', pady=1)

        self._opt_count = tk.BooleanVar(
            value=self._cfg.get('show_count', True))
        ttk.Checkbutton(
            opts_inner, text='Avatar count in folder',
            variable=self._opt_count,
            command=self._update_preview,
        ).grid(row=3, column=0, sticky='w', pady=1)

        self._opt_separate = tk.BooleanVar(
            value=self._cfg.get('separate_folders', False))
        ttk.Checkbutton(
            opts_inner, text='Separate folders for PNGs and EDATs',
            variable=self._opt_separate,
            command=self._update_preview,
        ).grid(row=3, column=1, sticky='w', pady=1)

        # Preview
        self._preview_var = tk.StringVar()
        tk.Label(opts_inner, textvariable=self._preview_var,
                 fg=FG_PREVIEW, bg=BG_CARD,
                 font=('Consolas', 9), anchor='w',
                 justify='left').grid(
                     row=4, column=0, columnspan=2, sticky='w',
                     pady=(6, 2))
        self._update_preview()

        # -- Action bar --
        action_frame = tk.Frame(main, bg=BG)
        action_frame.pack(fill='x', pady=(0, 8))

        self._go_btn = ttk.Button(action_frame,
                                  text='\u25B6  Decrypt && Organize',
                                  style='Accent.TButton',
                                  command=self._start)
        self._go_btn.pack(side='left')

        self._pct_label = tk.Label(action_frame, text='', bg=BG, fg=FG_DIM,
                                   font=('Segoe UI', 9))
        self._pct_label.pack(side='right', padx=(8, 0))

        self._progress = ttk.Progressbar(action_frame, mode='determinate',
                                         length=300,
                                         style='Horizontal.TProgressbar')
        self._progress.pack(side='right', fill='x', expand=True, padx=(12, 0))

        # -- Log card --
        log_header = tk.Frame(main, bg=BG)
        log_header.pack(fill='x')
        tk.Label(log_header, text='Log', bg=BG, fg=FG_HEADING,
                 font=('Segoe UI', 10, 'bold')).pack(side='left')

        log_card = make_card(main)
        log_card.pack(fill='both', expand=True, pady=(4, 0))

        self._log = tk.Text(log_card, bg=BG_LOG, fg=FG,
                            font=('Consolas', 9), wrap='word',
                            state='disabled', relief='flat', bd=0,
                            padx=10, pady=8,
                            insertbackground=FG,
                            selectbackground='#264f78')
        log_scroll = ttk.Scrollbar(log_card, orient='vertical',
                                   command=self._log.yview)
        self._log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side='right', fill='y', padx=(0, 2), pady=2)
        self._log.pack(side='left', fill='both', expand=True, padx=(2, 0),
                       pady=2)

        self._log.tag_configure('ok', foreground=FG_OK)
        self._log.tag_configure('skip', foreground=FG_SKIP)
        self._log.tag_configure('fail', foreground=FG_FAIL)
        self._log.tag_configure('info', foreground=FG_ACCENT)
        self._log.tag_configure('done', foreground=FG_DONE)

        # -- Status bar --
        self._status_var = tk.StringVar(value='')
        status_bar = tk.Label(self, textvariable=self._status_var,
                              bg='#0a0a12', fg=FG_DIM,
                              font=('Segoe UI', 9), anchor='w', padx=12)
        status_bar.pack(side='bottom', fill='x', ipady=3)

    def _init_taskbar(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if not hwnd:
                hwnd = self.winfo_id()
            self._taskbar = _try_taskbar_progress(hwnd)
        except Exception:
            self._taskbar = None

    # ---- Option helpers ----

    def _validate_folder_opts(self):
        if not self._opt_game_name.get() and not self._opt_title_id.get():
            messagebox.showwarning(
                'Invalid Option',
                'At least one of Game Name or Title ID must be enabled.')
            self._opt_title_id.set(True)
        self._update_preview()

    def _update_preview(self):
        parts_folder = []
        if self._opt_game_name.get():
            parts_folder.append('Game Name')
        if self._opt_title_id.get():
            parts_folder.append('[ULUS10566]')
        folder = ' '.join(parts_folder) if parts_folder else 'ULUS10566'
        if self._opt_count.get():
            folder += ' (24)'

        if self._opt_content_id.get():
            fname = 'UP0082-ULUS10566_00-... - PSNA_000.png'
        else:
            fname = 'PSNA_000.png'

        if self._opt_separate.get():
            line1 = f'US/{folder}/previews/{fname}'
            line2 = f'US/{folder}/psn_avatar/PSNA_000.edat'
            self._preview_var.set(f'{line1}\n{line2}')
        else:
            self._preview_var.set(f'US/{folder}/{fname}  +  PSNA_000.edat')

    def _get_options(self):
        return {
            'content_id_in_filename': self._opt_content_id.get(),
            'show_game_name': self._opt_game_name.get(),
            'show_title_id': self._opt_title_id.get(),
            'show_count': self._opt_count.get(),
            'separate_folders': self._opt_separate.get(),
        }

    # ---- Log helpers ----

    def _log_write(self, text, tag=''):
        self._log.configure(state='normal')
        if tag:
            self._log.insert('end', text + '\n', tag)
        else:
            self._log.insert('end', text + '\n')
        self._log.see('end')
        self._log.configure(state='disabled')

    def _log_clear(self):
        self._log.configure(state='normal')
        self._log.delete('1.0', 'end')
        self._log.configure(state='disabled')

    # ---- Folder Browsing ----

    def _browse_input(self):
        d = filedialog.askdirectory(title='Select Input Folder',
                                    initialdir=self._input_var.get() or None)
        if d:
            self._input_var.set(d)

    def _browse_output(self):
        d = filedialog.askdirectory(title='Select Output Folder',
                                    initialdir=self._output_var.get() or None)
        if d:
            self._output_var.set(d)

    # ---- Processing ----

    def _start(self):
        inp = self._input_var.get().strip()
        out = self._output_var.get().strip()
        if not inp:
            messagebox.showwarning('Missing Input',
                                   'Please select an input folder.')
            return
        if not os.path.isdir(inp):
            messagebox.showerror('Invalid Input',
                                 f'Input folder does not exist:\n{inp}')
            return
        if not out:
            messagebox.showwarning('Missing Output',
                                   'Please select an output folder.')
            return

        self._running = True
        self._start_time = time.time()
        self._go_btn.configure(state='disabled')
        self._progress['value'] = 0
        self._pct_label.configure(text='')
        self._log_clear()
        self._status_var.set('Processing...')
        if self._taskbar:
            self._taskbar.set_state(TaskbarProgress.TBPF_NORMAL)

        opts = self._get_options()

        self._log_write(f'Input:  {inp}', 'info')
        self._log_write(f'Output: {out}', 'info')
        opt_desc = []
        if opts['content_id_in_filename']:
            opt_desc.append('Content ID in filenames')
        if opts['show_game_name']:
            opt_desc.append('Game Name')
        if opts['show_title_id']:
            opt_desc.append('Title ID')
        if opts['show_count']:
            opt_desc.append('Count')
        if opts['separate_folders']:
            opt_desc.append('Separate PNG/EDAT folders')
        self._log_write(f'Options: {", ".join(opt_desc)}', 'info')
        self._log_write('', 'info')

        self._cfg.update(input=inp, output=out, **opts)
        save_config(self._cfg)

        self._output_folder = out

        t = threading.Thread(target=self._worker, args=(inp, out, opts),
                             daemon=True)
        t.start()

    def _worker(self, inp, out, opts):
        def progress_cb(current, total, status, filename):
            self._queue.put(('progress', current, total, status, filename))

        stats = organize_with_mode(
            inp, out,
            title_lookup=self._titles,
            progress_cb=progress_cb,
            options=opts,
        )
        self._queue.put(('done', stats))

    def _poll_queue(self):
        try:
            for _ in range(200):  # process up to 200 messages per tick
                msg = self._queue.get_nowait()
                if msg[0] == 'progress':
                    _, current, total, status, filename = msg
                    if total > 0:
                        self._progress['maximum'] = total
                        self._progress['value'] = current
                        pct = int(current / total * 100)

                        # ETA calculation
                        elapsed = time.time() - self._start_time
                        if current > 0 and current < total:
                            eta = elapsed / current * (total - current)
                            if eta >= 60:
                                eta_str = f'~{eta / 60:.0f}m left'
                            else:
                                eta_str = f'~{eta:.0f}s left'
                        elif current >= total:
                            eta_str = f'{elapsed:.1f}s'
                        else:
                            eta_str = ''

                        self._pct_label.configure(
                            text=f'{current}/{total} ({pct}%)  {eta_str}')

                        # Taskbar progress
                        if self._taskbar:
                            self._taskbar.set_progress(current, total)

                    tag = status if status in ('ok', 'skip', 'fail') else ''
                    label = {'ok': 'OK', 'skip': 'SKIP', 'fail': 'FAIL'}.get(
                        status, status.upper())
                    self._log_write(f'[{label:4s}] {filename}', tag)
                    self._status_var.set(f'{label}: {filename}')
                elif msg[0] == 'done':
                    stats = msg[1]
                    self._on_done(stats)
        except Empty:
            pass
        self.after(30, self._poll_queue)

    def _on_done(self, stats):
        self._running = False
        self._go_btn.configure(state='normal')
        elapsed = time.time() - self._start_time

        # Clear taskbar progress
        if self._taskbar:
            self._taskbar.clear()

        ok = stats['ok']
        skip = stats['skip']
        fail = stats['fail']
        total = stats['total']

        if total == 0:
            self._log_write('No PSNA_*.edat files found in the input folder.',
                            'fail')
            self._status_var.set('No files found.')
            return

        if elapsed >= 60:
            time_str = f'{elapsed / 60:.1f}m'
        else:
            time_str = f'{elapsed:.1f}s'

        rate = ok / elapsed if elapsed > 0 and ok > 0 else 0
        summary = (f'Done in {time_str} ({rate:.1f}/s) \u2014 '
                   f'Decrypted: {ok}  |  Skipped: {skip}  '
                   f'|  Failed: {fail}  |  Total: {total}')
        self._log_write('')
        self._log_write(summary, 'done')
        self._status_var.set(summary)

        if stats['failures']:
            self._log_write('')
            self._log_write('Failed files:', 'fail')
            for name, msg in stats['failures']:
                self._log_write(f'  {name}: {msg}', 'fail')

        self._progress['value'] = self._progress['maximum']
        self._pct_label.configure(text='Complete')

        # Open the output folder in Explorer
        out = getattr(self, '_output_folder', None)
        if out and os.path.isdir(out):
            self.after(500, lambda: self._open_folder(out))

    def _open_folder(self, path):
        try:
            os.startfile(path)
        except Exception:
            try:
                subprocess.Popen(['explorer', path])
            except Exception:
                pass

    # ---- Cleanup ----

    def _on_close(self):
        if self._running:
            if not messagebox.askyesno('Confirm',
                                       'Processing is running. Quit anyway?'):
                return
        self.destroy()


# ============================================================
# Entry Point
# ============================================================

def main():
    app = App()
    app.mainloop()


if __name__ == '__main__':
    main()
