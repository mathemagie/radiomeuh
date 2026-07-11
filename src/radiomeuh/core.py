"""Core domain logic for Radio Meuh: streaming, ICY metadata, persistence.

This module has no terminal or GUI dependencies, so it is shared by both the
CLI (:mod:`radiomeuh.cli`) and the macOS menu bar app (:mod:`radiomeuh.menubar`).
"""

import contextlib
import os
import shutil
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

STREAMS = {
    "128": "https://radiomeuh.ice.infomaniak.ch/radiomeuh-128.mp3",
    "64": "https://radiomeuh.ice.infomaniak.ch/radiomeuh-64.mp3",
}


# --- Track-title parsing ----------------------------------------------------
def _split_artist_title(raw: str) -> tuple[str, str]:
    """Split 'Artist - Title' into its parts; falls back to (raw, '')."""
    for sep in (" - ", " – ", " — "):
        if sep in raw:
            artist, title = raw.split(sep, 1)
            return artist.strip(), title.strip()
    return raw.strip(), ""


def _join(artist: str, title: str) -> str:
    return f"{artist} — {title}" if title else artist


def _parse_stream_title(block: bytes) -> str | None:
    text = block.rstrip(b"\x00").decode("utf-8", "replace")
    for part in text.split(";"):
        if part.startswith("StreamTitle="):
            return part[len("StreamTitle=") :].strip().strip("'")
    return None


def _read_exactly(resp, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = resp.read(n - len(buf))
        if not chunk:
            raise ConnectionError("stream ended")
        buf.extend(chunk)
    return bytes(buf)


# --- Live metadata reader ---------------------------------------------------
class MetadataReader(threading.Thread):
    """Continuously reads ICY StreamTitle metadata from the stream."""

    def __init__(
        self,
        url: str,
        store: "TrackStore | None" = None,
        station: str = "Radio Meuh",
    ):
        super().__init__(daemon=True)
        self.url = url
        self.store = store
        self.station = station
        self.artist = ""
        self.title = ""
        self.history: list[str] = []  # previous "Artist — Title" strings, newest first
        self.changed_at = 0.0  # monotonic time the current track started
        self.connected = False
        self.started = False  # True once the stream is confirmed live
        self._raw = ""  # last raw StreamTitle seen (for change detection)
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _update(self, raw: str):
        """Record a newly-announced track, splitting artist from title."""
        raw = raw.strip()
        if not raw or raw == self._raw:
            return  # ignore empty announcements and repeats (avoid flicker)
        with self._lock:
            if self._raw:  # push the outgoing track onto the history trail
                prev = _join(self.artist, self.title)
                if prev and (not self.history or self.history[0] != prev):
                    self.history.insert(0, prev)
                    del self.history[3:]
            self._raw = raw
            self.artist, self.title = _split_artist_title(raw)
            self.changed_at = time.monotonic()
        if self.store is not None:
            # Never let persistence interrupt playback.
            with contextlib.suppress(Exception):
                self.store.log(self.artist, self.title, raw, self.station)

    def snapshot(self):
        with self._lock:
            return self.artist, self.title, list(self.history), self.changed_at

    def run(self):
        while not self._stop.is_set():
            try:
                self._read_loop()
            except Exception:
                self.connected = False
                # brief backoff before reconnecting
                for _ in range(20):
                    if self._stop.is_set():
                        return
                    time.sleep(0.1)

    def _read_loop(self):
        req = urllib.request.Request(
            self.url,
            headers={"Icy-MetaData": "1", "User-Agent": "radiomeuh-cli/1.0"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        metaint = resp.headers.get("icy-metaint")
        self.connected = True
        self.started = True  # headers received => stream is live
        if not metaint:
            return  # no metadata available on this stream
        metaint = int(metaint)
        while not self._stop.is_set():
            _read_exactly(resp, metaint)  # skip audio payload
            length_byte = resp.read(1)
            if not length_byte:
                break
            length = length_byte[0] * 16
            if length:
                block = _read_exactly(resp, length)
                raw = _parse_stream_title(block)
                if raw is not None:
                    self._update(raw)
        resp.close()


# --- Persistence ------------------------------------------------------------
def default_db_path() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    directory = os.path.join(base, "radiomeuh")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "tracks.db")


class TrackStore:
    """Persists every announced track to SQLite (one row per play)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        # The reader thread writes; the main thread may read — share one
        # connection guarded by a lock.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                artist      TEXT NOT NULL,
                title       TEXT NOT NULL,
                raw         TEXT NOT NULL,
                played_at   TEXT NOT NULL,   -- ISO-8601, local time
                played_utc  TEXT NOT NULL,   -- ISO-8601, UTC (for sorting/portability)
                station     TEXT NOT NULL
            )
            """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracks_utc ON tracks(played_utc)"
        )
        self._conn.commit()

    # A restart re-announces the currently-playing track; treat a repeat of
    # the newest row within this window as the same play, not a new one.
    DEDUP_WINDOW = timedelta(minutes=30)

    def log(self, artist: str, title: str, raw: str, station: str):
        now_utc = datetime.now(timezone.utc)
        row = (
            artist,
            title,
            raw,
            now_utc.astimezone().isoformat(timespec="seconds"),
            now_utc.isoformat(timespec="seconds"),
            station,
        )
        with self._lock:
            last = self._conn.execute(
                "SELECT raw, played_utc FROM tracks ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if (
                last is not None
                and last[0] == raw
                and now_utc - datetime.fromisoformat(last[1]) < self.DEDUP_WINDOW
            ):
                return
            self._conn.execute(
                "INSERT INTO tracks"
                " (artist, title, raw, played_at, played_utc, station)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                row,
            )
            self._conn.commit()

    def recent(self, limit: int = 20) -> list[tuple]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT played_at, artist, title FROM tracks ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return cur.fetchall()

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            distinct = self._conn.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT artist, title FROM tracks)"
            ).fetchone()[0]
            top = self._conn.execute(
                "SELECT artist, COUNT(*) c FROM tracks"
                " GROUP BY artist ORDER BY c DESC, artist LIMIT 10"
            ).fetchall()
            span = self._conn.execute(
                "SELECT MIN(played_at), MAX(played_at) FROM tracks"
            ).fetchone()
        return {"total": total, "distinct": distinct, "top": top, "span": span}

    def close(self):
        with self._lock:
            self._conn.close()


# --- Audio player discovery -------------------------------------------------
# When launched from Finder/Dock, macOS gives the app a minimal PATH that does
# not include Homebrew's bin dirs, so we search those explicitly too.
_EXTRA_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")


def _which(name: str) -> str | None:
    """Like shutil.which, but also searches common Homebrew/MacPorts bin dirs."""
    found = shutil.which(name)
    if found:
        return found
    extra_path = os.pathsep.join(_EXTRA_BIN_DIRS)
    return shutil.which(name, path=os.environ.get("PATH", "") + os.pathsep + extra_path)


def find_player() -> tuple[str, list[str]] | None:
    """Return (name, command_prefix) for an available audio player."""
    if path := _which("ffplay"):
        return "ffplay", [path, "-nodisp", "-autoexit", "-loglevel", "quiet"]
    if path := _which("mpv"):
        return "mpv", [path, "--no-video", "--really-quiet"]
    if path := _which("mplayer"):
        return "mplayer", [path, "-really-quiet"]
    if path := _which("cvlc"):
        return "cvlc", [path, "--intf", "dummy", "--quiet"]
    return None
