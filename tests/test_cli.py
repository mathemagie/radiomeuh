"""Unit tests for radiomeuh.cli."""

import io
import sqlite3
import subprocess
import types

from radiomeuh import cli, core


# --- helpers ----------------------------------------------------------------
class FakeStdout(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


class FakeReader:
    def __init__(self, started=True, snap=("", "", [], 0.0)):
        self.started = started
        self._snap = snap

    def snapshot(self):
        return self._snap


class FakeMetaReader:
    def __init__(self, *a, **k):
        self.started = True
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def snapshot(self):
        return ("A", "B", [], 0.0)


class FakeProc:
    def __init__(self, polls=None, default=0, wait_raises=False):
        self._polls = list(polls or [])
        self._default = default
        self.wait_raises = wait_raises
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._polls.pop(0) if self._polls else self._default

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if self.wait_raises:
            raise subprocess.TimeoutExpired("player", timeout)

    def kill(self):
        self.killed = True


# --- supports_color / Style -------------------------------------------------
def test_supports_color(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdout", types.SimpleNamespace(isatty=lambda: True))
    assert cli.supports_color() is True
    monkeypatch.setattr(cli.sys, "stdout", types.SimpleNamespace(isatty=lambda: False))
    assert cli.supports_color() is False


def test_style_wraps_only_when_enabled_with_codes():
    on = cli.Style(True)
    assert on("hi", cli.BOLD).startswith(cli.BOLD)
    assert on("hi", cli.BOLD).endswith(cli.RESET)
    assert on("hi") == "hi"  # no codes -> untouched
    off = cli.Style(False)
    assert off("hi", cli.BOLD) == "hi"  # disabled -> untouched


# --- render -----------------------------------------------------------------
def test_render_reader_none():
    out = cli.render(cli.Style(True), "128", None, "*", 65.0)
    assert "R A D I O   M E U H" in out
    assert "live" in out and "01:05" in out


def test_render_connecting_when_not_started():
    out = cli.render(cli.Style(True), "128", FakeReader(started=False), "*", 1.0)
    assert "Connecting to Radio Meuh" in out


def test_render_fresh_track_with_title(monkeypatch):
    monkeypatch.setattr(cli.time, "monotonic", lambda: 100.0)
    reader = FakeReader(snap=("Artist", "Song", [], 99.0))  # 1s ago -> fresh
    out = cli.render(cli.Style(True), "128", reader, "*", 1.0)
    assert "Artist" in out and "Song" in out


def test_render_old_track_and_history(monkeypatch):
    monkeypatch.setattr(cli.time, "monotonic", lambda: 100.0)
    reader = FakeReader(snap=("Artist", "Song", ["Past — Track"], 50.0))  # not fresh
    out = cli.render(cli.Style(True), "128", reader, "*", 1.0)
    assert "Artist" in out and "Past — Track" in out


def test_render_track_without_title(monkeypatch):
    monkeypatch.setattr(cli.time, "monotonic", lambda: 100.0)
    reader = FakeReader(snap=("SoloArtist", "", [], 99.0))
    out = cli.render(cli.Style(True), "128", reader, "*", 1.0)
    assert "SoloArtist" in out


def test_render_waiting_when_no_artist(monkeypatch):
    monkeypatch.setattr(cli.time, "monotonic", lambda: 100.0)
    reader = FakeReader(snap=("", "", [], 0.0))
    out = cli.render(cli.Style(True), "128", reader, "*", 1.0)
    assert "waiting for track info" in out


# --- show_db ----------------------------------------------------------------
def test_show_db_missing_file(tmp_path, capsys):
    rc = cli.show_db("history", str(tmp_path / "nope.db"), 20, cli.Style(False))
    assert rc == 1
    assert "No database yet" in capsys.readouterr().err


def test_show_db_history_empty(tmp_path, capsys):
    path = str(tmp_path / "e.db")
    core.TrackStore(path).close()  # create the file, no rows
    rc = cli.show_db("history", path, 20, cli.Style(False))
    assert rc == 0
    assert "No tracks recorded yet." in capsys.readouterr().out


def test_show_db_history_rows(tmp_path, capsys):
    path = str(tmp_path / "h.db")
    store = core.TrackStore(path)
    store.log("Artist", "Title", "Artist - Title", "Radio Meuh")
    store.log("Solo", "", "Solo", "Radio Meuh")  # no title branch
    store.close()
    rc = cli.show_db("history", path, 20, cli.Style(False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Artist" in out and "Solo" in out


def test_show_db_stats_with_rows(tmp_path, capsys):
    path = str(tmp_path / "s.db")
    store = core.TrackStore(path)
    store.log("Artist", "Title", "Artist - Title", "Radio Meuh")
    store.close()
    rc = cli.show_db("stats", path, 20, cli.Style(False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "listening stats" in out and "since" in out and "Artist" in out


def test_show_db_stats_empty(tmp_path, capsys):
    path = str(tmp_path / "se.db")
    core.TrackStore(path).close()
    rc = cli.show_db("stats", path, 20, cli.Style(False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "plays logged" in out and "since" not in out  # no span line


# --- main -------------------------------------------------------------------
def _run_main(monkeypatch, argv):
    monkeypatch.setattr(cli.sys, "argv", ["radiomeuh", *argv])
    return cli.main()


def test_main_dispatches_history(monkeypatch):
    called = {}

    def fake_show_db(command, db, limit, style):
        called["args"] = (command, limit)
        return 0

    monkeypatch.setattr(cli, "show_db", fake_show_db)
    assert _run_main(monkeypatch, ["history", "-n", "5"]) == 0
    assert called["args"] == ("history", 5)


def test_main_no_player_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(cli, "TrackStore", lambda path: types.SimpleNamespace())
    monkeypatch.setattr(cli, "find_player", lambda: None)
    assert _run_main(monkeypatch, []) == 1
    assert "No audio player found" in capsys.readouterr().err


def test_main_store_open_failure_warns(monkeypatch, capsys):
    def boom(path):
        raise sqlite3.Error("locked")

    monkeypatch.setattr(cli, "TrackStore", boom)
    monkeypatch.setattr(cli, "find_player", lambda: None)  # short-circuit after warning
    assert _run_main(monkeypatch, []) == 1
    err = capsys.readouterr().err
    assert "could not open database" in err and "No audio player found" in err


def test_main_popen_oserror_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_player", lambda: ("ffplay", ["ffplay"]))

    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(cli.subprocess, "Popen", boom)
    # --no-meta --no-db -> reader None, store None
    assert _run_main(monkeypatch, ["--no-meta", "--no-db"]) == 1
    assert "Failed to launch ffplay" in capsys.readouterr().err


def test_main_tty_loop_until_player_exits(monkeypatch):
    store = types.SimpleNamespace(close=lambda: store.__dict__.update(closed=True))
    monkeypatch.setattr(cli, "TrackStore", lambda path: store)
    monkeypatch.setattr(cli, "find_player", lambda: ("ffplay", ["ffplay"]))
    monkeypatch.setattr(cli, "MetadataReader", FakeMetaReader)
    # two rendered frames (so the cursor-up path runs), then exit
    proc = FakeProc(polls=[None, None, 0], default=0)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setattr(cli.sys, "stdout", FakeStdout(tty=True))
    assert _run_main(monkeypatch, ["--debug"]) == 0
    assert store.__dict__.get("closed") is True


def test_main_non_tty_keyboard_interrupt_cleanup(monkeypatch):
    store = types.SimpleNamespace(closed=False)
    store.close = lambda: setattr(store, "closed", True)
    monkeypatch.setattr(cli, "TrackStore", lambda path: store)
    monkeypatch.setattr(cli, "find_player", lambda: ("ffplay", ["ffplay"]))
    reader = FakeMetaReader()
    monkeypatch.setattr(cli, "MetadataReader", lambda *a, **k: reader)
    proc = FakeProc(polls=[None], default=None, wait_raises=True)  # stays running
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", interrupt)
    monkeypatch.setattr(cli.sys, "stdout", FakeStdout(tty=False))
    # --no-meta but a store exists -> reader created, display_reader None
    assert _run_main(monkeypatch, ["--no-meta"]) == 0
    assert reader.stopped is True
    assert proc.terminated is True and proc.killed is True  # wait timed out -> kill
    assert store.closed is True
