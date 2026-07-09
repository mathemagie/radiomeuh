"""Unit tests for radiomeuh.core."""

import io

import pytest

from radiomeuh import core


# --- Track-title parsing ----------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Daft Punk - Around the World", ("Daft Punk", "Around the World")),
        ("A – B", ("A", "B")),  # en dash
        ("A — B", ("A", "B")),  # em dash
        ("  spaced  -  out  ", ("spaced", "out")),
        ("JustOneField", ("JustOneField", "")),  # no separator -> fallback
    ],
)
def test_split_artist_title(raw, expected):
    assert core._split_artist_title(raw) == expected


def test_join():
    assert core._join("Artist", "Title") == "Artist — Title"
    assert core._join("Artist", "") == "Artist"


def test_parse_stream_title():
    block = b"StreamTitle='Artist - Song';StreamUrl='';\x00\x00"
    assert core._parse_stream_title(block) == "Artist - Song"
    assert core._parse_stream_title(b"NoMetadataHere;\x00") is None


def test_read_exactly_accumulates_partial_reads():
    class Trickle:
        def __init__(self, data):
            self._data = data
            self.i = 0

        def read(self, n):  # hand back one byte at a time
            if self.i >= len(self._data):
                return b""
            chunk = self._data[self.i : self.i + 1]
            self.i += 1
            return chunk

    assert core._read_exactly(Trickle(b"hello"), 5) == b"hello"


def test_read_exactly_raises_on_short_stream():
    resp = io.BytesIO(b"ab")
    with pytest.raises(ConnectionError):
        core._read_exactly(resp, 5)


# --- MetadataReader ---------------------------------------------------------
def test_metadata_reader_defaults():
    r = core.MetadataReader("http://x")
    assert r.url == "http://x"
    assert r.artist == "" and r.title == "" and r.history == []
    assert r.connected is False and r.started is False


def test_metadata_reader_stop_sets_event():
    r = core.MetadataReader("http://x")
    r.stop()
    assert r._stop.is_set()


def test_update_ignores_empty_and_repeats():
    r = core.MetadataReader("http://x")
    r._update("")  # empty -> ignored
    assert r._raw == ""
    r._update("A - B")
    assert (r.artist, r.title) == ("A", "B")
    r._update("A - B")  # exact repeat -> ignored (history untouched)
    assert r.history == []


def test_update_builds_and_trims_history():
    r = core.MetadataReader("http://x")
    for raw in ["A1 - t", "A2 - t", "A3 - t", "A4 - t", "A5 - t"]:
        r._update(raw)
    # current is A5; history keeps only the 3 most recent previous tracks
    assert (r.artist, r.title) == ("A5", "t")
    assert r.history == ["A4 — t", "A3 — t", "A2 — t"]


def test_update_dedupes_consecutive_history_head():
    r = core.MetadataReader("http://x")
    r._update("A - t")
    r._update("B - u")  # pushes "A — t" onto history
    assert r.history == ["A — t"]
    # Now the current track resolves back to "A — t" while _raw differs; the
    # outgoing "A — t" must not be duplicated at the head of the trail.
    r._raw = "stale-raw"
    r.artist, r.title = "A", "t"
    r._update("C - v")
    assert r.history == ["A — t"]  # head == prev -> not inserted again


def test_update_logs_to_store_and_suppresses_errors():
    class BoomStore:
        def __init__(self):
            self.calls = []

        def log(self, *a):
            self.calls.append(a)
            raise RuntimeError("db down")

    store = BoomStore()
    r = core.MetadataReader("http://x", store=store)
    r._update("A - B")  # must not raise despite the store blowing up
    assert store.calls == [("A", "B", "A - B", "Radio Meuh")]


def test_snapshot_returns_copy():
    r = core.MetadataReader("http://x")
    r._update("A - B")
    artist, title, history, changed = r.snapshot()
    history.append("mutation")
    assert r.history == []  # snapshot returned an independent list


def test_run_reconnects_then_stops(monkeypatch):
    r = core.MetadataReader("http://x")

    def boom():
        raise ConnectionError("dropped")

    def fake_sleep(_):
        r._stop.set()  # stop during the backoff so run() returns

    monkeypatch.setattr(r, "_read_loop", boom)
    monkeypatch.setattr(core.time, "sleep", fake_sleep)
    r.run()  # returns via the backoff loop
    assert r.connected is False


# --- _read_loop with a scripted fake response -------------------------------
class _FakeResp:
    def __init__(self, data, metaint="4"):
        self._buf = io.BytesIO(data)
        self.headers = {"icy-metaint": metaint} if metaint is not None else {}
        self.closed = False

    def read(self, n):
        return self._buf.read(n)

    def close(self):
        self.closed = True


def test_read_loop_no_metaint_returns_early(monkeypatch):
    resp = _FakeResp(b"", metaint=None)
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *a, **k: resp)
    r = core.MetadataReader("http://x")
    r._read_loop()
    assert r.connected is True and r.started is True


def test_read_loop_parses_blocks_then_breaks(monkeypatch):
    audio = b"\x00\x00\x00\x00"  # metaint == 4
    block32 = b"StreamTitle='A - B';".ljust(32, b"\x00")  # length byte 0x02 -> 32
    block16 = b"nostreamtitle;xx"  # length byte 0x01 -> 16, no StreamTitle
    data = (
        audio
        + b"\x02"
        + block32  # -> updates to A / B
        + audio
        + b"\x01"
        + block16  # -> parsed as None, no update
        + audio
        + b"\x00"  # length 0 -> skip block
        + audio  # next read(1) hits EOF -> break
    )
    resp = _FakeResp(data, metaint="4")
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *a, **k: resp)
    r = core.MetadataReader("http://x")
    r._read_loop()
    assert (r.artist, r.title) == ("A", "B")
    assert resp.closed is True


# --- Persistence ------------------------------------------------------------
def test_default_db_path_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    path = core.default_db_path()
    assert path.endswith("radiomeuh/tracks.db")
    assert (tmp_path / "data" / "radiomeuh").is_dir()


def test_default_db_path_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    path = core.default_db_path()
    assert str(tmp_path / "home" / ".local" / "share" / "radiomeuh") in path


def test_track_store_log_recent_stats_close(tmp_path):
    store = core.TrackStore(str(tmp_path / "t.db"))
    store.log("Alpha", "One", "Alpha - One", "Radio Meuh")
    store.log("Alpha", "Two", "Alpha - Two", "Radio Meuh")
    store.log("Beta", "Three", "Beta - Three", "Radio Meuh")

    rows = store.recent(10)
    assert len(rows) == 3
    assert rows[0][1] == "Beta"  # newest first

    s = store.stats()
    assert s["total"] == 3
    assert s["distinct"] == 3
    assert s["top"][0] == ("Alpha", 2)  # Alpha leads
    assert s["span"][0] is not None and s["span"][1] is not None
    store.close()


def test_track_store_stats_empty(tmp_path):
    store = core.TrackStore(str(tmp_path / "empty.db"))
    s = store.stats()
    assert s["total"] == 0 and s["top"] == [] and s["span"] == (None, None)
    store.close()


# --- Player discovery -------------------------------------------------------
def test_which_found_on_path(monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda name, path=None: "/usr/bin/ffplay")
    assert core._which("ffplay") == "/usr/bin/ffplay"


def test_which_found_in_extra_dirs(monkeypatch):
    def fake_which(name, path=None):
        return "/opt/homebrew/bin/mpv" if path is not None else None

    monkeypatch.setattr(core.shutil, "which", fake_which)
    assert core._which("mpv") == "/opt/homebrew/bin/mpv"


def test_which_not_found(monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda name, path=None: None)
    assert core._which("nope") is None


@pytest.mark.parametrize(
    "available, expected_name, expected_first",
    [
        ("ffplay", "ffplay", "-nodisp"),
        ("mpv", "mpv", "--no-video"),
        ("mplayer", "mplayer", "-really-quiet"),
        ("cvlc", "cvlc", "--intf"),
    ],
)
def test_find_player_picks_first_available(
    monkeypatch, available, expected_name, expected_first
):
    monkeypatch.setattr(
        core, "_which", lambda name: f"/bin/{name}" if name == available else None
    )
    name, cmd = core.find_player()
    assert name == expected_name
    assert cmd[0] == f"/bin/{available}"
    assert cmd[1] == expected_first


def test_find_player_none(monkeypatch):
    monkeypatch.setattr(core, "_which", lambda name: None)
    assert core.find_player() is None
