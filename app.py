"""Desktop interface for scanning and downloading Facebook media."""
from datetime import date
import os
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


if getattr(sys, "frozen", False):
    bundled_browsers = Path(sys.executable).resolve().parent / "ms-playwright"
    if bundled_browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)

from app_backend import login, save, scan


if getattr(sys, "frozen", False):
    data_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    SESSION_FILE = data_root / "FacebookMediaDownloader" / ".facebook-session.json"
else:
    SESSION_FILE = Path(__file__).resolve().with_name(".facebook-session.json")


class App(ttk.Frame):
    def __init__(self, root):
        super().__init__(root, padding=18)
        self.root = root
        self.url = tk.StringVar()
        self.history = tk.BooleanVar()
        self.date_from = tk.StringVar()
        self.date_to = tk.StringVar()
        self.kind = tk.StringVar(value="All media")
        self.include_undated = tk.BooleanVar()
        self.destination = tk.StringVar()
        self.status = tk.StringVar(value="Paste a Facebook link to get started.")
        self.result_count = tk.StringVar(value="0 results")
        self.rows = []
        self.matching = []
        self.busy = False
        self.events = Queue()

        root.title("Facebook Media Downloader")
        root.geometry("1000x720")
        root.minsize(740, 580)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.pack(fill="both", expand=True)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self._build()
        for variable in (self.date_from, self.date_to, self.kind, self.include_undated):
            variable.trace_add("write", self.refresh_results)
        self.destination.trace_add("write", self._update_actions)
        self.root.after(80, self._poll)

    def _build(self):
        ttk.Label(self, text="Facebook Media Downloader", font=("Segoe UI", 20, "bold")).grid(
            row=0, column=0, sticky="w")
        ttk.Label(self, text="Scan a Facebook page, pick a date range, and save the media you want.").grid(
            row=1, column=0, sticky="w", pady=(3, 14))

        source = ttk.LabelFrame(self, text="1. Facebook link", padding=10)
        source.grid(row=2, column=0, sticky="ew")
        source.columnconfigure(0, weight=1)
        self.url_entry = ttk.Entry(source, textvariable=self.url)
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.url_entry.bind("<Return>", lambda event: self.start_scan())
        self.url_entry.focus_set()
        self.scan_button = ttk.Button(source, text="Scan media", command=self.start_scan)
        self.scan_button.grid(row=0, column=1, sticky="ew")
        self.login_button = ttk.Button(source, text="Sign in (optional)", command=self.start_login)
        self.login_button.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        self.history_check = ttk.Checkbutton(source, text="Scan older posts (takes longer)", variable=self.history)
        self.history_check.grid(row=1, column=0, sticky="w", pady=(6, 0))

        filters = ttk.LabelFrame(self, text="2. Filter results", padding=10)
        filters.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        filters.columnconfigure(4, weight=1)
        self.from_box = self._date_field(filters, "From date", self.date_from, 0)
        self.to_box = self._date_field(filters, "Through date", self.date_to, 1)
        ttk.Label(filters, text="Media type").grid(row=0, column=2, sticky="w")
        self.kind_box = ttk.Combobox(filters, textvariable=self.kind, values=("All media", "Videos", "Photos"),
                                    width=14, state="readonly")
        self.kind_box.grid(row=1, column=2, sticky="w", padx=(0, 12))
        self.undated_check = ttk.Checkbutton(filters, text="Include undated in range", variable=self.include_undated)
        self.undated_check.grid(row=1, column=3, sticky="w")
        ttk.Label(filters, text="Dates use YYYY-MM-DD. Leave blank for no limit.").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(5, 0))
        ttk.Label(filters, textvariable=self.result_count).grid(row=2, column=4, sticky="e")
        self.date_error = ttk.Label(filters, foreground="#a4262c")
        self.date_error.grid(row=3, column=0, columnspan=5, sticky="w")
        self.date_error.grid_remove()

        results = ttk.Frame(self)
        results.grid(row=4, column=0, sticky="nsew", pady=(8, 10))
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(results, columns=("date", "kind", "media", "status"), show="headings",
                                  selectmode="extended", height=9)
        for key, title, width, stretch in (
            ("date", "Post date", 105, False), ("kind", "Type", 75, False),
            ("media", "Media", 370, True), ("status", "Download status", 210, True),
        ):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=65, stretch=stretch)
        self.table.grid(row=0, column=0, sticky="nsew")
        self.table.bind("<<TreeviewSelect>>", self._update_actions)
        scrollbar = ttk.Scrollbar(results, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)

        target = ttk.LabelFrame(self, text="3. Download folder", padding=10)
        target.grid(row=5, column=0, sticky="ew")
        target.columnconfigure(0, weight=1)
        self.destination_entry = ttk.Entry(target, textvariable=self.destination)
        self.destination_entry.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, 8))
        self.browse_button = ttk.Button(target, text="Browse…", command=self.browse_destination)
        self.browse_button.grid(row=0, column=2, sticky="e")
        ttk.Label(target, text="Files go in YYYY-MM folders by post date.").grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        actions = ttk.Frame(target)
        actions.grid(row=1, column=1, columnspan=2, sticky="e", pady=(6, 0))
        self.selected_button = ttk.Button(actions, text="Download selected",
                                          command=lambda: self.start_download(selected=True))
        self.selected_button.pack(side="left", padx=(0, 6))
        self.download_button = ttk.Button(actions, text="Download matches", command=self.start_download)
        self.download_button.pack(side="left")

        footer = ttk.Frame(self)
        footer.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, wraplength=650).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=120)
        self.progress.grid(row=0, column=1, sticky="e", padx=(10, 0))
        self.activity = tk.Text(footer, height=3, wrap="word", state="disabled",
                                background="#f4f5f7", borderwidth=0, padx=6, pady=3)
        self.activity.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self._update_actions()

    @staticmethod
    def _date_field(parent, label, variable, column):
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w")
        field = ttk.Combobox(parent, textvariable=variable, width=13)
        field.grid(row=1, column=column, sticky="w", padx=(0, 12))
        return field

    @staticmethod
    def _parse_date(value):
        value = value.strip()
        if not value:
            return ""
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
        return value

    def refresh_results(self, *_):
        self.matching = []
        error = ""
        try:
            start = self._parse_date(self.date_from.get())
            end = self._parse_date(self.date_to.get())
        except ValueError:
            error = "Enter valid dates in YYYY-MM-DD format."
        else:
            if start and end and start > end:
                error = "The through date must be on or after the from date."
            else:
                media_kind = {"Photos": "image", "Videos": "video"}.get(self.kind.get())
                for index, row in enumerate(self.rows):
                    if media_kind and row["kind"] != media_kind:
                        continue
                    posted = row.get("post_date", "")
                    if posted:
                        if (start and posted < start) or (end and posted > end):
                            continue
                    elif (start or end) and not self.include_undated.get():
                        continue
                    self.matching.append(index)
        if error:
            self.date_error.configure(text=error)
            self.date_error.grid()
        else:
            self.date_error.grid_remove()

        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        for index in self.matching:
            row = self.rows[index]
            description = row.get("alt") or row.get("facebook_url") or row.get("media_url", "")
            kind = "Photo" if row["kind"] == "image" else "Video"
            self.table.insert("", "end", iid=str(index),
                              values=(row.get("post_date") or "Undated", kind, description,
                                      row.get("download_status", "")))
        self.table.selection_set([item for item in selected if self.table.exists(item)])
        self.result_count.set(f"{len(self.matching):,} of {len(self.rows):,} results")
        self._update_actions()

    def _update_actions(self, *_):
        can_download = not self.busy and bool(self.destination.get().strip()) and bool(self.matching)
        self.download_button.state(["!disabled" if can_download else "disabled"])
        self.selected_button.state(["!disabled" if can_download and self.table.selection() else "disabled"])

    def browse_destination(self):
        current = Path(self.destination.get().strip()).expanduser() if self.destination.get().strip() else Path.home()
        chosen = filedialog.askdirectory(parent=self.root, title="Choose a download folder",
                                         initialdir=str(current if current.is_dir() else Path.home()), mustexist=True)
        if chosen:
            self.destination.set(chosen)

    def start_scan(self):
        if self.busy:
            return
        link = self.url.get().strip()
        if not link:
            self.status.set("Paste a Facebook link to scan.")
            self.url_entry.focus_set()
            return
        self.rows = []
        self.from_box.configure(values=())
        self.to_box.configure(values=())
        self.refresh_results()
        self._clear_activity()
        self._start("scan", scan, link, history=self.history.get())

    def start_login(self):
        if self.busy:
            return
        self._clear_activity()
        self._start("login", login, SESSION_FILE)

    def start_download(self, selected=False):
        if self.busy or not self.destination.get().strip():
            return
        indices = [int(item) for item in self.table.selection()] if selected else self.matching.copy()
        if not indices:
            return
        destination = Path(self.destination.get().strip()).expanduser()
        if not destination.is_dir():
            self.status.set("Choose an existing download folder.")
            self.destination_entry.focus_set()
            return
        self._clear_activity()
        self._start("download", save, [self.rows[index] for index in indices], destination, indices=indices)

    def _start(self, operation, function, *args, indices=None, **kwargs):
        self._set_busy(True)
        self.status.set({
            "scan": "Scanning Facebook…",
            "download": "Downloading media…",
            "login": "Waiting for Facebook sign-in…",
        }[operation])
        session = SESSION_FILE if SESSION_FILE.is_file() else None

        def worker():
            try:
                result = function(*args, session=session, log=lambda line: self.events.put(("log", line)), **kwargs)
            except Exception as error:
                self.events.put(("error", operation, str(error)))
            else:
                self.events.put((operation, result, indices))

        Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy):
        self.busy = busy
        state = ["disabled" if busy else "!disabled"]
        for widget in (self.scan_button, self.login_button, self.url_entry, self.history_check, self.from_box, self.to_box,
                       self.kind_box, self.undated_check, self.destination_entry, self.browse_button):
            widget.state(state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
        self._update_actions()

    def _poll(self):
        try:
            while True:
                event, *details = self.events.get_nowait()
                if event == "log":
                    self._log(details[0])
                    continue
                if event == "scan":
                    self._scan_finished(*details[0])
                elif event == "download":
                    self._download_finished(*details)
                elif event == "login":
                    self._login_finished()
                else:
                    operation, error = details
                    self.status.set(f"{operation.capitalize()} failed. See the activity below.")
                    self._log(error)
                self._set_busy(False)
        except Empty:
            pass
        self.root.after(80, self._poll)

    def _scan_finished(self, rows, failures):
        self.rows = list(rows)
        dates = sorted({row["post_date"] for row in rows if row.get("post_date")}, reverse=True)
        self.from_box.configure(values=dates)
        self.to_box.configure(values=dates)
        self.refresh_results()
        dated = sum(bool(row.get("post_date")) for row in rows)
        summary = f"Found {len(rows):,} media items ({dated:,} with dates)."
        if failures:
            summary += f" {len(failures):,} scan locations could not be read. See activity below."
            for location, reason in failures.items():
                self._log(f"{location}: {reason}")
        self.status.set(summary)

    def _download_finished(self, results, indices):
        saved = 0
        for index, result in zip(indices, results):
            self.rows[index] = result
            if result.get("file"):
                saved += 1
            else:
                description = result.get("facebook_url") or result.get("media_url") or "Media item"
                self._log(f"{description}: {result.get('download_status', 'Download failed')}")
        self.refresh_results()
        self.status.set(f"Saved {saved:,} of {len(results):,} media items.")

    def _login_finished(self):
        self.status.set("Facebook sign-in saved for this Windows user.")

    def _log(self, line):
        self.activity.configure(state="normal")
        self.activity.insert("end", line + "\n")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _clear_activity(self):
        self.activity.configure(state="normal")
        self.activity.delete("1.0", "end")
        self.activity.configure(state="disabled")

    def close(self):
        if self.busy and not messagebox.askokcancel(
            "Work in progress", "Closing the app will stop the current operation. Close the app?", parent=self.root
        ):
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

