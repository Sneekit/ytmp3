#!/usr/bin/env python3
"""YT -> MP3: standalone desktop app (no server, no browser).

Setup:  pip install yt-dlp imageio-ffmpeg
Run:    python ytmp3.py

Single song, Playlist and Channel tabs all add items to one shared queue,
which converts one video at a time.
"""
import os
import queue
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import imageio_ffmpeg
    from yt_dlp import YoutubeDL
except ImportError:
    sys.exit("Missing dependencies. Run:  pip install yt-dlp imageio-ffmpeg")

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
BITRATES = ["64", "96", "128", "160", "192", "256", "320"]
YT_RE = re.compile(r"^https?://(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", re.I)
CHANNEL_ROOT_RE = re.compile(r"^(https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+))/?$", re.I)


# ------------------------------------------------------------ core logic ----

def safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s or "").strip(" .") or "download"


def normalize_channel(text: str):
    """'@Handle' or a channel URL -> that channel's /videos tab URL (None if not a channel/YouTube link)."""
    text = text.strip()
    if re.fullmatch(r"@[\w.\-]+", text):
        return f"https://www.youtube.com/{text}/videos"
    if not YT_RE.match(text):
        return None
    m = CHANNEL_ROOT_RE.match(text)
    return m.group(1) + "/videos" if m else text


def expand(url: str, depth: int = 0):
    """Expand a video / playlist / channel URL -> (playlist_title | None, [{url, title}])."""
    with YoutubeDL({"quiet": True, "extract_flat": "in_playlist", "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") != "playlist":
        return None, [{"url": info.get("webpage_url") or url, "title": info.get("title") or url}]
    out = []
    for e in info.get("entries") or []:
        if not e:
            continue
        eid = e.get("id") or ""
        if e.get("ie_key") == "Youtube" or (len(eid) == 11 and e.get("_type") in (None, "url")):
            out.append({"url": f"https://www.youtube.com/watch?v={eid}", "title": e.get("title") or eid})
        elif depth < 2 and e.get("url"):  # channel root -> tabs
            if "/shorts" in e["url"] or "/streams" in e["url"]:
                continue
            out.extend(expand(e["url"], depth + 1)[1])
    return info.get("title"), out


def select(url: str, needles: tuple, max_items):
    title, entries = expand(url)
    low = [n.lower() for n in needles]
    seen, picked = set(), []
    for e in entries:
        if e["url"] in seen:
            continue
        seen.add(e["url"])
        if low and not any(n in e["title"].lower() for n in low):
            continue
        picked.append(e)
    return title, (picked[:max_items] if max_items else picked)


class Cancelled(Exception):
    pass


def convert(url, bitrate, thumbnail, outdir: Path, hook, cancel: threading.Event) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)

    def _hook(d):
        if cancel.is_set():
            raise Cancelled()
        hook(d)

    opts = {
        "quiet": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "ffmpeg_location": FFMPEG,
        "outtmpl": str(outdir / "%(title)s.%(ext)s"),
        "noprogress": True,
        "progress_hooks": [_hook],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": bitrate},
            {"key": "FFmpegMetadata"},
        ],
    }
    if thumbnail:
        opts["writethumbnail"] = True
        opts["postprocessors"].append({"key": "EmbedThumbnail"})
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info)).with_suffix(".mp3")


# ------------------------------------------------------------------- GUI ----

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("YouTube to MP3")
        self._blank_icon = tk.PhotoImage(width=16, height=16)  # transparent: replaces the default feather icon
        root.iconphoto(True, self._blank_icon)
        root.geometry("820x720")
        root.minsize(680, 600)
        self.ui_q: queue.Queue = queue.Queue()
        self.out_var = tk.StringVar(value=str(Path.home() / "Music" / "YTmp3"))
        self.bitrate_var = tk.StringVar(value="320")
        self.thumb_var = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="")

        # shared queue state
        self.items, self.work_q = {}, queue.Queue()
        self.cancel, self._n = threading.Event(), 0

        self._build_options()
        nb = ttk.Notebook(root)
        nb.pack(fill="x", padx=10)
        self._build_single(nb)
        self._build_playlist(nb)
        self._build_channel(nb)
        self._build_queue()

        threading.Thread(target=self._worker, daemon=True).start()
        root.after(100, self._pump)

    # -- thread -> UI plumbing --
    def ui(self, fn):
        self.ui_q.put(fn)

    def _pump(self):
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        except Exception as e:  # never let a UI callback kill the pump
            print("UI error:", e, file=sys.stderr)
        self.root.after(100, self._pump)

    def _set(self, iid, status):
        def f():
            if self.tree.exists(iid):
                self.tree.set(iid, "status", status)
        self.ui(f)

    def _title(self, iid, title):
        def f():
            if self.tree.exists(iid):
                self.tree.item(iid, text=title)
        self.ui(f)

    # -- settings --
    def _build_options(self):
        box = ttk.LabelFrame(self.root, text="Settings", padding=8)
        box.pack(fill="x", padx=10, pady=10)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Save to").grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.out_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(box, text="Browse...", command=self._browse).grid(row=0, column=2)
        ttk.Button(box, text="Open", command=lambda: self._open(self.out_var.get())).grid(row=0, column=3, padx=(4, 0))
        ttk.Label(box, text="Bitrate (kbps)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(box, textvariable=self.bitrate_var, values=BITRATES, state="readonly",
                     width=8).grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Checkbutton(box, text="Embed thumbnail as cover art", variable=self.thumb_var).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(box, text="Bitrate and cover art are locked in when items are added to the queue.",
                  foreground="gray").grid(row=3, column=0, columnspan=4, sticky="w")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()))
        if d:
            self.out_var.set(d)

    def _open(self, path):
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(p)  # Windows
        except AttributeError:
            messagebox.showinfo("Folder", str(p))

    def _settings(self):
        out = self.out_var.get().strip()
        if not out:
            messagebox.showwarning("Save to", "Choose a folder to save into.")
            return None
        return {"bitrate": self.bitrate_var.get(), "thumb": self.thumb_var.get(), "outdir": Path(out)}

    # -- input tabs --
    @staticmethod
    def _form(nb, title, rows, button_text, command, hint=None):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text=title)
        f.columnconfigure(1, weight=1)
        vars_ = []
        for r, (label, multi) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="nw" if multi else "w", pady=2)
            if multi:
                w = tk.Text(f, height=4, wrap="none")
                w.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=2)
                vars_.append(w)
            else:
                v = tk.StringVar()
                ttk.Entry(f, textvariable=v).grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=2)
                vars_.append(v)
        r = len(rows)
        if hint:
            ttk.Label(f, text=hint, foreground="gray").grid(row=r, column=1, sticky="w", padx=8)
            r += 1
        ttk.Button(f, text=button_text, command=command).grid(row=r, column=1, sticky="w", padx=8, pady=(6, 0))
        return vars_

    def _build_single(self, nb):
        (self.links,) = self._form(nb, "Single song", [("YouTube links (one per line)", True)],
                                   "Add to queue", self.add_single)

    def _build_playlist(self, nb):
        self.pl_url, self.pl_max, self.pl_folder = self._form(
            nb, "Playlist",
            [("Playlist link", False), ("Max videos (blank = all)", False), ("Folder name (blank = playlist name)", False)],
            "Add playlist to queue", self.add_playlist)

    def _build_channel(self, nb):
        self.ch_url, self.ch_term, self.ch_max, self.ch_folder = self._form(
            nb, "Channel",
            [("Channel link or @handle", False), ("Search term", False),
             ("Max videos (blank = all)", False), ("Folder name (blank = auto)", False)],
            "Add matching videos to queue", self.add_channel,
            hint="Adds every video on the channel whose title contains the search term (comma-separate several; ignores case).")

    # -- shared queue --
    def _build_queue(self):
        box = ttk.LabelFrame(self.root, text="Queue", padding=8)
        box.pack(fill="both", expand=True, padx=10, pady=10)
        bar = ttk.Frame(box)
        bar.pack(fill="x")
        ttk.Button(bar, text="Stop", command=self.stop).pack(side="left")
        ttk.Button(bar, text="Remove selected", command=self.remove).pack(side="left", padx=6)
        ttk.Button(bar, text="Clear finished", command=self.clear_done).pack(side="left")
        ttk.Button(bar, text="Open selected folder", command=self.open_selected).pack(side="left", padx=6)
        ttk.Label(box, textvariable=self.status).pack(anchor="w", pady=(6, 2))
        frame = ttk.Frame(box)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("folder", "status"), selectmode="extended", height=10)
        self.tree.heading("#0", text="Title", anchor="w")
        self.tree.heading("folder", text="Folder", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.column("#0", width=380, stretch=True)
        self.tree.column("folder", width=150, stretch=False)
        self.tree.column("status", width=170, stretch=False)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _enqueue(self, entries, s, folder=""):
        """UI thread: add [{url,title}] to the queue. Returns (added, skipped_duplicates)."""
        queued = {it["url"] for it in self.items.values()}
        added = skipped = 0
        outdir = s["outdir"] / folder if folder else s["outdir"]
        for e in entries:
            if e["url"] in queued:
                skipped += 1
                continue
            queued.add(e["url"])
            iid, self._n = f"q{self._n}", self._n + 1
            self.tree.insert("", "end", iid=iid, text=e["title"], values=(folder, "Waiting"))
            item = {"iid": iid, "url": e["url"], "bitrate": s["bitrate"], "thumb": s["thumb"],
                    "outdir": outdir, "cancel": self.cancel, "state": "queued"}
            self.items[iid] = item
            self.work_q.put(item)
            added += 1
        return added, skipped

    def add_single(self):
        urls = list(dict.fromkeys(self.links.get("1.0", "end").split()))
        good = [u for u in urls if YT_RE.match(u)]
        bad = [u for u in urls if not YT_RE.match(u)]
        if not good:
            messagebox.showwarning("Links", "Paste at least one valid YouTube link.")
            return
        s = self._settings()
        if not s:
            return
        added, skipped = self._enqueue([{"url": u, "title": u} for u in good], s)
        self.links.delete("1.0", "end")
        note = f" Skipped {skipped} already in queue." if skipped else ""
        if bad:
            self.links.insert("1.0", "\n".join(bad))
            note += f" {len(bad)} invalid link(s) left in the box."
        self.status.set(f"Added {added} video(s).{note}")

    @staticmethod
    def _max(text):
        text = text.strip()
        if text and not (text.isdigit() and int(text) > 0):
            messagebox.showwarning("Max videos", "Max videos must be a positive number (or blank).")
            return False
        return int(text) if text else None

    def add_playlist(self):
        url = self.pl_url.get().strip()
        if not YT_RE.match(url):
            messagebox.showwarning("Link", "Enter a valid YouTube playlist link.")
            return
        mx = self._max(self.pl_max.get())
        if mx is False:
            return
        self._add_remote(url, (), mx, self.pl_folder.get().strip(), "Playlist")

    def add_channel(self):
        url = normalize_channel(self.ch_url.get())
        if not url:
            messagebox.showwarning("Channel", "Enter a YouTube channel link or @handle.")
            return
        needles = tuple(n.strip() for n in self.ch_term.get().split(",") if n.strip())
        if not needles and not messagebox.askyesno(
                "No search term", "No search term entered. Add EVERY video on this channel to the queue?"):
            return
        mx = self._max(self.ch_max.get())
        if mx is False:
            return
        self._add_remote(url, needles, mx, self.ch_folder.get().strip(), "Channel")

    def _add_remote(self, url, needles, max_items, folder_field, label):
        s = self._settings()
        if not s:
            return
        self.status.set("Finding videos... (big channels can take a minute)")

        def work():
            try:
                title, entries = select(url, needles, max_items)
            except Exception as e:
                msg = str(e).split("\n")[0].replace("ERROR: ", "")[:150]
                self.ui(lambda: self.status.set("Failed: " + msg))
                return
            if not entries:
                self.ui(lambda: self.status.set("No matching videos found."))
                return
            base = title or label
            if needles and title:
                base = f"{title} - {' '.join(needles)}"
            folder = safe_name(folder_field or base)

            def add():
                queued = {it["url"] for it in self.items.values()}
                new = [e for e in entries if e["url"] not in queued]
                if not new:
                    self.status.set(f"All {len(entries)} matching video(s) are already in the queue.")
                    return
                preview = "\n".join("  - " + e["title"] for e in new[:5])
                if len(new) > 5:
                    preview += f"\n  ...and {len(new) - 5} more"
                dup = f"\n({len(entries) - len(new)} already in the queue will be skipped.)" if len(new) < len(entries) else ""
                if not messagebox.askyesno(
                        "Confirm",
                        f"Found {len(new)} video(s) to convert at {s['bitrate']} kbps.\n"
                        f"They will be saved to:\n{s['outdir'] / folder}\n\n{preview}{dup}\n\nAdd them to the queue?"):
                    self.status.set("Cancelled - nothing was added.")
                    return
                added, skipped = self._enqueue(entries, s, folder)
                note = f" ({skipped} already in queue)" if skipped else ""
                self.status.set(f"Added {added} video(s) to '{folder}'{note}.")
            self.ui(add)
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        self.cancel.set()                  # aborts the current item and skips everything queued so far
        self.cancel = threading.Event()    # items added later get a fresh flag
        self.status.set("Stopped. Add more items any time.")

    def remove(self):
        for iid in self.tree.selection():
            it = self.items.get(iid)
            if it and it["state"] != "working":
                it["state"] = "removed"
                self.tree.delete(iid)
                del self.items[iid]

    def clear_done(self):
        for iid, it in list(self.items.items()):
            if it["state"] == "done":
                self.tree.delete(iid)
                del self.items[iid]

    def open_selected(self):
        sel = self.tree.selection()
        it = self.items.get(sel[0]) if sel else None
        self._open(it["outdir"] if it else self.out_var.get())

    # -- worker --
    def _worker(self):
        while True:
            it = self.work_q.get()
            if it["state"] == "removed":
                continue
            if it["cancel"].is_set():
                it["state"] = "stopped"
                self._set(it["iid"], "Stopped")
                continue
            it["state"] = "working"
            ok = self._run_item(it)
            it["state"] = "done" if ok else "failed"

    def _run_item(self, it) -> bool:
        iid, last, titled = it["iid"], [-1], [False]

        def hook(d):
            title = (d.get("info_dict") or {}).get("title")
            if title and not titled[0]:
                titled[0] = True
                self._title(iid, title)
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    pct = int(d["downloaded_bytes"] * 100 / total)
                    if pct != last[0]:
                        last[0] = pct
                        self._set(iid, f"Downloading {pct}%")
            elif d["status"] == "finished":
                self._set(iid, "Converting...")

        self._set(iid, "Starting...")
        try:
            convert(it["url"], it["bitrate"], it["thumb"], it["outdir"], hook, it["cancel"])
            self._set(iid, "Done")
            return True
        except Exception as e:
            if it["cancel"].is_set():
                self._set(iid, "Stopped")
            else:
                self._set(iid, "Failed: " + str(e).split("\n")[0].replace("ERROR: ", "")[:90])
            return False


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
