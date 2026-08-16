#!/usr/bin/env python3
r"""FindAll -- an Aura (QuickOpen design system) GUI on top of the ``findall`` API.

A single Aura window, search-first: a **Search** section with a source
selector and an as-you-type (debounced, threaded) query box over a results
table with "Open file" / "Open folder" actions; a **Sources** section to add
a folder and (re)index it with a progress bar; and an **About** section.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``findall/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) — declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * Importing this module does nothing.  Only :func:`main` builds a root
    window, and it degrades gracefully (prints a message, returns 0) with no
    display or with customtkinter missing.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the
    exe directory when ``sys.frozen`` is set -- never ``__file__``.
  * Indexing and search run on background threads; results are marshalled
    back with ``self.after`` and errors are shown in the Aura status bar,
    never as a traceback.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# tkinter/customtkinter are imported lazily inside main()/build_app so merely
# importing this module (e.g. during packaging or on a headless CI box) never
# fails.

APP_NAME = "FindAll"
APP_VERSION = "1.1.0"
WINDOW_TITLE = "FindAll — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
ACCENT = "#5b86f7"      # publish/specs/find-all.json "accent": [91, 134, 247]


# ---------------------------------------------------------------------------
# Asset / frozen handling  +  small OS helpers
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build.

    For a frozen exe we look only at ``sys._MEIPASS`` and the executable's own
    directory (never ``__file__``).  From source we also consult the package
    dir, the repo root and the CWD.  Returns an absolute path or ``None``.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def human_size(num_bytes):
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"


def fmt_time(ts):
    import datetime
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


# Everything-style type filters: chip id -> extensions
KIND_EXTS = {
    "docs": {"txt", "md", "rst", "pdf", "doc", "docx", "odt", "rtf", "csv",
             "xls", "xlsx", "ods", "ppt", "pptx", "odp", "log", "json",
             "xml", "yaml", "yml", "ini", "html", "htm", "tex", "epub"},
    "images": {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff",
               "svg", "ico", "heic", "raw", "psd", "xcf"},
    "audio": {"mp3", "wav", "flac", "ogg", "m4a", "aac", "wma", "opus",
              "mid", "midi"},
    "video": {"mp4", "mkv", "avi", "mov", "webm", "wmv", "m4v", "mpg",
              "mpeg", "3gp", "flv"},
}
FILTER_CHIPS = ("All", "Documents", "Images", "Audio", "Video", "Other")
_CHIP_KIND = {"Documents": "docs", "Images": "images", "Audio": "audio",
              "Video": "video"}


def file_kind(name):
    """Classify a filename by extension: docs|images|audio|video|other."""
    ext = os.path.splitext(name or "")[1].lstrip(".").lower()
    for kind, exts in KIND_EXTS.items():
        if ext in exts:
            return kind
    return "other"


def passes_chip(name, chip):
    """Does a result filename pass the selected type-filter chip?"""
    if chip in (None, "", "All"):
        return True
    if chip == "Other":
        return file_kind(name) == "other"
    return file_kind(name) == _CHIP_KIND.get(chip)


def open_in_file_manager(path):
    """Best-effort 'reveal in file manager', guarded on every platform."""
    try:
        folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
        if hasattr(os, "startfile"):
            os.startfile(folder)  # noqa: S606 - intended
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", folder])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        return False


def open_with_default_app(path):
    """Open a file with the OS default application, guarded."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import filedialog, ttk
    import customtkinter as ctk

    from . import aura, guiconfig, index as index_mod
    # NOTE: findall/__init__ rebinds the package attribute 'search' to the
    # search() FUNCTION, so 'from . import search' would NOT get the module.
    from .search import search as run_search
    from .errors import FindAllError

    class App(aura.AuraApp):
        def __init__(self):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("find-all.png"), version=APP_VERSION,
                tagline="offline search",
                on_theme_change=guiconfig.set_theme,
                size=(1080, 680), min_size=(880, 540))

            self._results = []          # raw result dicts from the last search
            self._rows = []             # results after the type-filter chip
            self._search_after = None   # debounce handle
            self._search_seq = 0        # ignore stale threaded results
            self._img_refs_gui = []
            self._chip = "All"
            self._active_source = ""
            self._src_rows = {}

            self._set_icon()
            self._build_menu()
            self.add_section("search", "Search", "⚲", self._build_search)
            self.add_section("sources", "Sources", "▤", self._build_sources)
            self.add_section("about", "About", "◉", self._build_about)
            self._build_source_sidebar()
            self.show("search")
            self.set_status("Ready")
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.after(80, self._focus_search)

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("find-all.ico")
                if ico and os.name == "nt":
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("find-all.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs_gui.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- menu (☰ dropdown) + keyboard baseline (§7/§9)
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Add source…", accelerator="Ctrl+N",
                              command=lambda: self.show("sources"))
            filem.add_command(label="Manage sources…",
                              command=lambda: self.show("sources"))
            filem.add_separator()
            filem.add_command(label="Settings…", accelerator="Ctrl+,",
                              command=self._open_settings)
            filem.add_command(label="Exit", command=self.destroy)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Focus search", accelerator="Ctrl+F",
                              command=self._focus_search)
            viewm.add_command(label="Search again", accelerator="F5",
                              command=self._trigger_search)
            viewm.add_command(label="Toggle sidebar", accelerator="Ctrl+\\",
                              command=self.toggle_sidebar)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=lambda: self.show("about"))
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

            self.bind_all("<Control-f>",
                          lambda e: (self._focus_search(), "break")[1])
            self.bind_all("<Control-n>",
                          lambda e: (self.show("sources"), "break")[1])
            self.bind_all("<Control-comma>",
                          lambda e: (self._open_settings(), "break")[1])
            self.bind_all("<F5>",
                          lambda e: (self._trigger_search(), "break")[1])

        def _focus_search(self):
            try:
                self.show("search")
                self.search_entry.focus_set()
            except Exception:
                pass

        # ---- Settings dialog (Ctrl+,)
        def _open_settings(self):
            dlg = aura.Dialog(self, title="Settings", size=(460, 240))
            aura.SectionLabel(dlg.body, "Appearance").pack(anchor="w",
                                                           pady=(0, 2))
            trow = ctk.CTkFrame(dlg.body, fg_color="transparent")
            trow.pack(anchor="w", pady=(4, 0))
            aura.Caption(trow, "Theme").pack(side="left", padx=(0, 10))
            cur = guiconfig.get_theme()
            th = aura.AuraOption(trow, values=["System", "Light", "Dark"],
                                 width=110, height=30,
                                 command=self._set_theme_pref)
            th.set(cur.capitalize() if cur in ("light", "dark") else "System")
            th.pack(side="left")
            aura.Caption(dlg.body,
                         "System follows the OS Aura Dark/Light live.").pack(
                anchor="w", pady=(6, 0))
            aura.Caption(dlg.body,
                         "Indexes live in your home folder — nothing is "
                         "ever uploaded.").pack(anchor="w", pady=(14, 0))
            dlg.add_button("Close")

        def _set_theme_pref(self, choice):
            pref = str(choice).lower()
            if pref == "system":
                guiconfig.set_theme("system")
                self._follow_system = True
                if getattr(self, "_sys_listener", None) is None:
                    self._start_system_listener()
                self.set_theme(aura._system_theme(), _system=True)
            elif pref in ("light", "dark"):
                self.set_theme(pref)     # persists via on_theme_change

        # ---- theme: restyle the raw sidebar rows with the flip
        def set_theme(self, theme, _system=False):
            super().set_theme(theme, _system=_system)
            try:
                self._refresh_source_sidebar()
            except Exception:
                pass

        # =================================================================
        # Sidebar source library (sidebar_body)
        # =================================================================
        def _build_source_sidebar(self):
            aura.SectionLabel(self.sidebar_body, "Sources").pack(
                anchor="w", padx=6, pady=(0, 4))
            self._src_frame = ctk.CTkScrollableFrame(
                self.sidebar_body, fg_color="transparent")
            self._src_frame.pack(fill="both", expand=True)
            self._refresh_source_sidebar()

        def _refresh_source_sidebar(self):
            if not hasattr(self, "_src_frame"):
                return
            for w in list(self._src_frame.winfo_children()):
                try:
                    w.destroy()
                except Exception:
                    pass
            self._src_rows.clear()
            sources = index_mod.list_sources()
            if not sources:
                aura.Caption(self._src_frame,
                             "Folders you index appear here.").pack(
                    anchor="w", padx=6, pady=(2, 0))
                return
            pair = aura._pair
            for name, info in sources:
                active = (name == self._active_source)
                n = info.get("count", "?")
                btn = ctk.CTkButton(
                    self._src_frame, text=f"{name}   ·  {n}",
                    anchor="w", height=30,
                    corner_radius=aura.TOKENS["geometry"]["radius_button"],
                    fg_color=pair("accent_soft") if active else "transparent",
                    hover_color=(aura._pal["light"]["surface2"],
                                 aura._pal["dark"]["surface2"]),
                    text_color=pair("text") if active else pair("muted"),
                    font=aura.font(role="body"),
                    command=lambda nm=name: self._select_source(nm))
                btn.pack(fill="x", pady=1)
                aura.Tooltip(btn, info.get("root", ""))
                self._src_rows[name] = btn

        def _select_source(self, name):
            self._active_source = name
            self.source_var.set(name)
            self._refresh_source_sidebar()
            self.show("search")
            self._trigger_search()

        # =================================================================
        # Search section
        # =================================================================
        def _build_search(self, frame):
            self.source_var = tk.StringVar()

            tb = aura.Toolbar(frame)
            tb.pack(fill="x", pady=(0, 8))
            tb.add_button("＋ Add source", lambda: self.show("sources"),
                          kind="primary")
            self.search_entry = tb.add_search(
                "Search names and contents…  (Ctrl+F)",
                on_change=lambda _t: self._trigger_search(), width=340)

            # Spotlight-style type chips on their own row (never squeezed)
            chiprow = ctk.CTkFrame(frame, fg_color="transparent")
            chiprow.pack(fill="x", pady=(0, 10))
            self.chip_seg = aura.SegmentedControl(
                chiprow, values=list(FILTER_CHIPS), width=480,
                command=self._set_chip)
            self.chip_seg.set("All")
            self.chip_seg.pack(side="left")

            body = ctk.CTkFrame(frame, fg_color="transparent")
            body.pack(fill="both", expand=True)
            cols = ("name", "size", "modified", "snippet")
            self.tree = ttk.Treeview(body, columns=cols, show="headings",
                                     selectmode="browse")
            for cid, label, width, stretch in (
                ("name", "Name", 220, False),
                ("size", "Size", 80, False),
                ("modified", "Modified", 130, False),
                ("snippet", "Match / path", 420, True),
            ):
                self.tree.heading(cid, text=aura.spaced(label), anchor="w",
                                  command=lambda c=cid: self._sort_by(c))
                self.tree.column(cid, width=width, stretch=stretch, anchor="w")
            sb = aura.AuraScrollbar(body, command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.tree.pack(side="left", fill="both", expand=True)
            self.tree.bind("<Double-1>", lambda _e: self._open_selected())
            self.tree.bind("<Return>", lambda _e: self._open_selected())
            self.tree.bind("<Button-3>", self._show_row_menu)
            self._row_menu = tk.Menu(self, tearoff=0)
            aura.track(self._row_menu, "menu")
            self._sort_key = None
            self._sort_desc = False

            aura.AuraButton(self.statusbar.actions, "Open file",
                            kind="secondary", height=30,
                            command=self._open_selected).pack(side="left")
            aura.AuraButton(self.statusbar.actions, "Open folder",
                            kind="secondary", height=30,
                            command=self._reveal_selected).pack(
                side="left", padx=(8, 0))

            # empty states: no sources at all / no matches for a query
            self.empty_sources = aura.EmptyState(
                frame, title="Nothing indexed yet",
                caption="Index a folder once, then search file names and "
                        "contents instantly — everything stays on this "
                        "device.",
                action_text="＋ Add a folder",
                action=lambda: self.show("sources"),
                image=(asset_path("assets/search-empty-light.png"),
                       asset_path("assets/search-empty-dark.png")))
            self.empty_results = aura.EmptyState(
                body, glyph="⚲", title="No matches",
                caption="Try fewer words, a different type filter, or "
                        "re-index the source if files changed (F5).")

            self._reload_sources()

        # ---- type-filter chips + column sorting
        def _set_chip(self, chip):
            self._chip = chip
            self._render_results()

        def _sort_by(self, cid):
            key = {"name": "name", "size": "size", "modified": "mtime"}.get(cid)
            if key is None:
                return
            if self._sort_key == key:
                self._sort_desc = not self._sort_desc
            else:
                self._sort_key, self._sort_desc = key, False
            self._render_results()

        def _show_row_menu(self, event):
            iid = self.tree.identify_row(event.y)
            if not iid:
                return
            self.tree.selection_set(iid)
            r = self._selected_result()
            if r is None:
                return
            m = self._row_menu
            m.delete(0, "end")
            m.add_command(label="Open", command=self._open_selected)
            m.add_command(label="Open containing folder",
                          command=self._reveal_selected)
            m.add_separator()
            m.add_command(label="Copy full path",
                          command=lambda: self._copy_path(r))
            aura.style_menu(m)
            try:
                m.tk_popup(event.x_root, event.y_root)
            finally:
                m.grab_release()

        def _copy_path(self, r):
            try:
                self.clipboard_clear()
                self.clipboard_append(r.get("path") or "")
                self.set_status("Path copied")
            except Exception:
                pass

        def _update_empty_states(self):
            has_sources = bool(index_mod.list_sources())
            if has_sources:
                self.empty_sources.place_forget()
            else:
                self.empty_sources.place(relx=0, rely=0.12, relwidth=1,
                                         relheight=0.85)
                self.empty_sources.lift()
            query = self.search_entry.get().strip() \
                if hasattr(self, "search_entry") else ""
            if has_sources and query and not self._rows:
                self.empty_results.place(x=0, y=0, relwidth=1, relheight=1)
                self.empty_results.lift()
            else:
                self.empty_results.place_forget()

        # ---- sources plumbing
        def _reload_sources(self):
            names = [name for name, _info in index_mod.list_sources()]
            if names and self.source_var.get() not in names:
                self.source_var.set(names[0])
                self._active_source = names[0]
            elif not names:
                self.source_var.set("")
                self._active_source = ""
                self.set_status("No sources yet — add a folder to start "
                                "searching.")
            else:
                self._active_source = self.source_var.get()
            self._refresh_source_sidebar()
            self._update_empty_states()

        # ---- as-you-type search (SearchEntry debounces; results threaded)
        def _trigger_search(self):
            self._search_after = None
            name = self.source_var.get().strip()
            query = self.search_entry.get().strip()
            self._clear_results()
            if not name:
                self._update_empty_states()
                return
            if not query:
                self.set_status("Type to search.")
                self._update_empty_states()
                return
            self._search_seq += 1
            seq = self._search_seq
            self.set_status("Searching…", kind="working")

            def work():
                try:
                    return run_search(name, query, limit=200), None
                except FindAllError as exc:
                    return None, str(exc)
                except Exception as exc:  # never leak a traceback
                    return None, f"Unexpected error: {exc}"

            # The worker writes its result to an attribute and the MAIN
            # thread polls for it — calling ``after`` from a worker thread
            # is unsafe (and raises when the main loop is only pumped).
            def run():
                self._search_done = (seq, *work())

            self._search_done = None
            threading.Thread(target=run, daemon=True).start()
            if not getattr(self, "_search_polling", False):
                self._poll_search_result()

        def _poll_search_result(self):
            done = getattr(self, "_search_done", None)
            if done is None:            # still waiting on a live search
                self._search_polling = True
                self.after(80, self._poll_search_result)
                return
            self._search_polling = False
            self._search_done = None
            self._show_results(*done)

        def _show_results(self, seq, res, err):
            if seq != self._search_seq:
                return  # a newer search superseded this one
            if err is not None:
                self.set_error(err)
                return
            self._results = res or []
            self._render_results()

        def _render_results(self):
            """Apply the type-filter chip + sort order and redraw the rows."""
            for iid in self.tree.get_children():
                self.tree.delete(iid)
            rows = [r for r in self._results
                    if passes_chip(r.get("name") or "", self._chip)]
            if self._sort_key:
                rows.sort(key=lambda r: (r.get(self._sort_key) is None,
                                         str(r.get(self._sort_key)).lower()
                                         if self._sort_key == "name"
                                         else (r.get(self._sort_key) or 0)),
                          reverse=self._sort_desc)
            self._rows = rows
            for i, r in enumerate(rows):
                snippet = r.get("snippet") or r.get("path") or ""
                snippet = " ".join(snippet.split())
                self.tree.insert("", "end", iid=str(i), values=(
                    r.get("name") or "",
                    human_size(r.get("size")),
                    fmt_time(r.get("mtime")),
                    snippet,
                ))
            n, total = len(rows), len(self._results)
            msg = f"{n} match(es)." if n else "No matches."
            if n != total:
                msg = f"{n} of {total} match(es) ({self._chip})."
            self.set_status(msg, kind="ok" if n else "idle")
            self._update_empty_states()

        def _clear_results(self):
            self._results = []
            self._rows = []
            for iid in self.tree.get_children():
                self.tree.delete(iid)

        def _selected_result(self):
            sel = self.tree.selection()
            if not sel:
                return None
            try:
                return self._rows[int(sel[0])]
            except (ValueError, IndexError):
                return None

        def _open_selected(self):
            r = self._selected_result()
            if r and r.get("path"):
                if not open_with_default_app(r["path"]):
                    self.set_error("Could not open file.")

        def _reveal_selected(self):
            r = self._selected_result()
            if r and r.get("path"):
                if not open_in_file_manager(r["path"]):
                    self.set_error("Could not open folder.")

        # =================================================================
        # Sources section (was the "Manage sources" dialog)
        # =================================================================
        def _build_sources(self, frame):
            add = aura.Card(frame, title="Add / re-index a folder")
            add.pack(fill="x", pady=(0, 14))

            row1 = ctk.CTkFrame(add.body, fg_color="transparent")
            row1.pack(fill="x", pady=(0, 8))
            self._folder_entry = aura.AuraEntry(
                row1, placeholder="Folder to index…")
            self._folder_entry.pack(side="left", fill="x", expand=True,
                                    padx=(0, 8))
            aura.AuraButton(row1, "Browse…", kind="secondary",
                            command=self._browse_folder).pack(side="left")

            row2 = ctk.CTkFrame(add.body, fg_color="transparent")
            row2.pack(fill="x", pady=(0, 8))
            self._name_entry = aura.AuraEntry(row2, placeholder="Source name",
                                              width=220)
            self._name_entry.pack(side="left", padx=(0, 14))
            self._content_var = tk.BooleanVar(value=True)
            ctk.CTkCheckBox(row2, text="Index file contents",
                            variable=self._content_var,
                            font=aura.font()).pack(side="left")

            row3 = ctk.CTkFrame(add.body, fg_color="transparent")
            row3.pack(fill="x", pady=(0, 4))
            self._exts_entry = aura.AuraEntry(
                row3, placeholder="Only extensions — e.g. “txt md py”. "
                                  "Blank = all files.")
            self._exts_entry.pack(side="left", fill="x", expand=True)

            self._prog = aura.ProgressBar(add.body)
            self._prog.pack(fill="x", pady=(10, 4))
            self._prog_lbl = aura.Caption(add.body, "")
            self._prog_lbl.pack(anchor="w")
            self._index_btn = aura.AuraButton(add.body, "＋ Add & index",
                                              command=self._do_index)
            self._index_btn.pack(anchor="w", pady=(8, 0))

            existing = aura.Card(frame, title="Existing sources")
            existing.pack(fill="both", expand=True)
            self._src_list = tk.Listbox(existing.body, height=7,
                                        activestyle="none",
                                        exportselection=False)
            self._src_list.pack(fill="both", expand=True)
            aura.track(self._src_list, "listbox")
            btns = ctk.CTkFrame(existing.body, fg_color="transparent")
            btns.pack(fill="x", pady=(10, 0))
            aura.AuraButton(btns, "Re-index", kind="secondary",
                            command=self._reindex_selected).pack(side="left")
            aura.AuraButton(btns, "Remove", kind="danger",
                            command=self._remove_selected).pack(
                side="left", padx=(8, 0))
            self._refresh_src_list()

        @staticmethod
        def _fill(entry, text):
            entry.delete(0, "end")
            if text:
                entry.insert(0, text)

        def _browse_folder(self):
            d = filedialog.askdirectory(title="Choose a folder to index")
            if d:
                self._fill(self._folder_entry, d)
                if not self._name_entry.get().strip():
                    self._fill(self._name_entry,
                               os.path.basename(os.path.normpath(d)))

        def _refresh_src_list(self):
            self._src_list.delete(0, "end")
            for name, info in index_mod.list_sources():
                exts = ",".join(info.get("exts")) if info.get("exts") else "all"
                self._src_list.insert(
                    "end", f"{name}  —  {info.get('count', '?')} files "
                           f"[{exts}]  {info.get('root', '')}")

        def _selected_source_name(self):
            sel = self._src_list.curselection()
            if not sel:
                return None
            sources = index_mod.list_sources()
            if sel[0] < len(sources):
                return sources[sel[0]][0]
            return None

        def _set_index_progress(self, done, total, _path):
            def upd():
                self._prog.set(min(1.0, done / max(1, total)))
                self._prog_lbl.configure(text=f"Indexed {done}/{total} files")
            self.after(0, upd)

        def _do_index(self, name=None, root=None, content=None, exts=None):
            nm = (name or self._name_entry.get()).strip()
            rt = root or self._folder_entry.get().strip()
            inc = self._content_var.get() if content is None else content
            raw = exts if exts is not None else self._exts_entry.get()
            ex = ([e for e in raw.replace(",", " ").split()]
                  if isinstance(raw, str) else raw)
            if not rt:
                self.set_error("Choose a folder first.")
                return
            if not nm:
                self.set_error("Give the source a name.")
                return
            self._index_btn.state(["disabled"])
            self._prog.set(0)
            self._prog_lbl.configure(text="Starting…")
            self.set_status("Indexing…", kind="working")

            def run():
                try:
                    res = index_mod.build_index(rt, name=nm,
                                                include_content=inc,
                                                exts=(ex or None),
                                                progress=self._set_index_progress)
                    err = None
                except FindAllError as exc:
                    res, err = None, str(exc)
                except Exception as exc:
                    res, err = None, f"Unexpected error: {exc}"

                def done():
                    self._index_btn.state(["!disabled"])
                    if err:
                        self._prog_lbl.configure(text=err)
                        self.set_error(err)
                    else:
                        self._prog_lbl.configure(
                            text=f"Done — {res['count']} files indexed.")
                        self.set_success(f"Indexed {res['count']} files "
                                         f"into “{nm}”.")
                        self._refresh_src_list()
                        self._reload_sources()
                        self.source_var.set(nm)
                        self._trigger_search()
                self.after(0, done)

            threading.Thread(target=run, daemon=True).start()

        def _reindex_selected(self):
            nm = self._selected_source_name()
            if not nm:
                return
            info = index_mod.get_source(nm) or {}
            self._fill(self._folder_entry, info.get("root", ""))
            self._fill(self._name_entry, nm)
            self._content_var.set(bool(info.get("include_content", True)))
            self._fill(self._exts_entry, " ".join(info.get("exts") or []))
            self._do_index(name=nm, root=info.get("root"),
                           content=bool(info.get("include_content", True)),
                           exts=info.get("exts") or [])

        def _remove_selected(self):
            nm = self._selected_source_name()
            if not nm:
                return
            try:
                index_mod.remove_source(nm)
            except FindAllError as exc:
                self.set_error(str(exc))
                return
            self._refresh_src_list()
            self._reload_sources()
            self.set_status(f"Removed source “{nm}”.")

        # =================================================================
        # About section
        # =================================================================
        def _build_about(self, frame):
            card = aura.Card(frame, title="About FindAll")
            card.pack(fill="x")
            aura.Heading(card.body, APP_NAME).pack(anchor="w")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(
                anchor="w", pady=(0, 10))
            ctk.CTkLabel(
                card.body, font=aura.font(), justify="left", anchor="w",
                wraplength=520,
                text="Instant, fully-offline filename + full-text search. "
                     "Index a folder once, then search names and contents "
                     "with fuzzy matching and filters.\n\n"
                     "100% AI-built, open source, published on QuickOpen. "
                     "Nothing is ever uploaded anywhere.").pack(anchor="w")
            aura.Caption(card.body,
                         "Licensed under Apache-2.0. Built on Whoosh (BSD) "
                         "and CustomTkinter (MIT).").pack(
                anchor="w", pady=(10, 4))
            link = aura.AuraButton(card.body, "Project page: quickopen.ai",
                                   kind="ghost",
                                   command=lambda: open_with_default_app(
                                       PROJECT_URL))
            link.pack(anchor="w", pady=(6, 0))

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server) or without customtkinter installed, it
    prints a friendly note and returns 0 instead of raising.
    """
    try:
        import tkinter as tk
    except Exception as exc:  # tkinter missing entirely
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
    except tk.TclError as exc:
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here ({exc}). This app is intended for the Windows desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
