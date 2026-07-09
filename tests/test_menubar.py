"""Unit tests for radiomeuh.menubar (uses the fake rumps from conftest)."""

import subprocess
import types

import pytest

from radiomeuh import menubar


# --- helpers ----------------------------------------------------------------
class FakeStore:
    def __init__(self, *a, **k):
        self.closed = False
        self._stats = {
            "total": 5,
            "distinct": 3,
            "top": [("Alpha", 3), ("Beta", 2)],
            "span": ("2024-01-01T12:00:00", "2024-01-02T12:00:00"),
        }

    def stats(self):
        return self._stats

    def close(self):
        self.closed = True

    def log(self, *a):
        pass


class FakeProc:
    def __init__(self, poll_value=None, wait_raises=False):
        self._poll = poll_value
        self.wait_raises = wait_raises
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if self.wait_raises:
            raise subprocess.TimeoutExpired("player", timeout)

    def kill(self):
        self.killed = True


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(menubar, "default_db_path", lambda: "/tmp/x.db")
    monkeypatch.setattr(menubar, "TrackStore", FakeStore)
    return menubar.RadioMeuhApp()


# --- _notify ----------------------------------------------------------------
def test_notify_fires_osascript_and_escapes_quotes(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        menubar.subprocess, "Popen", lambda args, **k: calls.setdefault("args", args)
    )
    menubar._notify('say "hi"')
    joined = " ".join(calls["args"])
    assert "osascript" in calls["args"]
    assert "display notification" in joined
    assert "say 'hi'" in joined  # user's double-quotes were escaped to single
    assert '"hi"' not in joined


# --- construction -----------------------------------------------------------
def test_init_builds_store_and_menu(app):
    assert isinstance(app.store, FakeStore)
    assert app.title == menubar.IDLE_ICON
    assert app.timer.running is True
    assert app.q128.state == 1 and app.q64.state == 0


def test_init_store_failure_sets_none(monkeypatch):
    def boom(_):
        raise RuntimeError("no db")

    monkeypatch.setattr(menubar, "default_db_path", lambda: "/tmp/x.db")
    monkeypatch.setattr(menubar, "TrackStore", boom)
    app = menubar.RadioMeuhApp()
    assert app.store is None


# --- playing / toggle -------------------------------------------------------
def test_playing_states(app):
    assert app.playing is False  # proc None
    app.proc = FakeProc(poll_value=None)
    assert app.playing is True
    app.proc = FakeProc(poll_value=0)
    assert app.playing is False


def test_toggle_play_dispatches(app, monkeypatch):
    events = []
    monkeypatch.setattr(app, "start", lambda: events.append("start"))
    monkeypatch.setattr(app, "stop", lambda: events.append("stop"))
    app.toggle_play()  # not playing -> start
    app.proc = FakeProc(poll_value=None)  # now playing -> stop
    app.toggle_play()
    assert events == ["start", "stop"]


# --- start ------------------------------------------------------------------
def test_start_no_player_notifies(app, monkeypatch):
    notes = []
    monkeypatch.setattr(menubar, "_notify", lambda m: notes.append(m))
    monkeypatch.setattr(menubar, "find_player", lambda: None)
    app.start()
    assert app.proc is None
    assert "ffmpeg" in notes[0]
    assert "Install ffmpeg" in app.now_item.title


def test_start_success(app, monkeypatch):
    proc = FakeProc(poll_value=None)
    monkeypatch.setattr(menubar, "find_player", lambda: ("ffplay", ["ffplay"]))
    monkeypatch.setattr(menubar.subprocess, "Popen", lambda *a, **k: proc)
    started = {}
    monkeypatch.setattr(
        menubar,
        "MetadataReader",
        lambda *a, **k: types.SimpleNamespace(start=lambda: started.setdefault("s", 1)),
    )
    app.start()
    assert app.proc is proc
    assert app.play_item.title == "⏸  Stop"
    assert started["s"] == 1


def test_start_popen_oserror_notifies(app, monkeypatch):
    notes = []
    monkeypatch.setattr(menubar, "_notify", lambda m: notes.append(m))
    monkeypatch.setattr(menubar, "find_player", lambda: ("ffplay", ["ffplay"]))

    def boom(*a, **k):
        raise OSError("denied")

    monkeypatch.setattr(menubar.subprocess, "Popen", boom)
    app.start()
    assert app.proc is None
    assert "Could not start playback" in notes[0]
    assert "Could not start playback" in app.now_item.title


# --- stop -------------------------------------------------------------------
def test_stop_terminates_cleanly(app):
    app.reader = types.SimpleNamespace(stopped=False)
    app.reader.stop = lambda: setattr(app.reader, "stopped", True)
    app.proc = FakeProc(poll_value=None)
    app.stop()
    assert app.proc is None
    assert app.title == menubar.IDLE_ICON
    assert app.play_item.title == "▶  Play"
    assert app.now_item.title == "Not playing"


def test_stop_kills_on_timeout(app):
    proc = FakeProc(poll_value=None, wait_raises=True)
    app.proc = proc
    app.reader = None  # no reader branch
    app.stop()
    assert proc.terminated is True and proc.killed is True


# --- set_quality ------------------------------------------------------------
def test_set_quality_toggles_state(app):
    app.set_quality("64")
    assert app.quality == "64"
    assert app.q64.state == 1 and app.q128.state == 0


def test_set_quality_restarts_when_playing(app, monkeypatch):
    app.proc = FakeProc(poll_value=None)  # playing
    events = []
    monkeypatch.setattr(app, "stop", lambda: events.append("stop"))
    monkeypatch.setattr(app, "start", lambda: events.append("start"))
    app.set_quality("64")
    assert events == ["stop", "start"]


# --- refresh ----------------------------------------------------------------
def test_refresh_detects_dead_stream(app, monkeypatch):
    app.proc = FakeProc(poll_value=1)  # exited
    app.reader = types.SimpleNamespace(snapshot=lambda: ("", "", [], 0.0))
    stopped = []
    monkeypatch.setattr(app, "stop", lambda: stopped.append(True))
    app.refresh()
    assert stopped == [True]


def test_refresh_no_reader_is_noop(app):
    app.reader = None
    app.refresh()  # returns early, nothing to assert but must not raise


def test_refresh_updates_now_and_recent(app):
    app.proc = FakeProc(poll_value=None)
    app.reader = types.SimpleNamespace(
        snapshot=lambda: ("Artist", "Song", ["Old — One"], 0.0)
    )
    app.refresh()
    assert app.now_item.title == "♪  Artist — Song"
    assert app.recent_menu.children[0].title == "Old — One"
    # second call with same track -> no change
    app.now_item.title = "sentinel"
    app.refresh()
    assert app.now_item.title == "sentinel"


def test_refresh_waiting_when_no_artist(app):
    app.proc = FakeProc(poll_value=None)
    app.reader = types.SimpleNamespace(snapshot=lambda: ("", "", [], 0.0))
    app._last_track = "previously something"  # so the None current is a change
    app.refresh()
    assert app.now_item.title == "♪  (waiting…)"


def test_rebuild_recent_empty(app):
    app._rebuild_recent([])
    assert app.recent_menu.children[0].title == "—"


# --- show_stats -------------------------------------------------------------
def test_show_stats_no_store_notifies(app, monkeypatch):
    app.store = None
    notes = []
    monkeypatch.setattr(menubar, "_notify", lambda m: notes.append(m))
    app.show_stats()
    assert "No listening database" in notes[0]


def test_show_stats_shows_alert(app, monkeypatch):
    alerts = []
    monkeypatch.setattr(menubar.rumps, "alert", lambda *a: alerts.append(a))
    app.show_stats()
    assert alerts and "Listening stats" in alerts[0][0]
    assert "Since:" in alerts[0][1]


def test_show_stats_without_span(app, monkeypatch):
    app.store._stats = {"total": 0, "distinct": 0, "top": [], "span": (None, None)}
    alerts = []
    monkeypatch.setattr(menubar.rumps, "alert", lambda *a: alerts.append(a))
    app.show_stats()
    assert "Since:" not in alerts[0][1]


# --- open_site / quit -------------------------------------------------------
def test_open_site(app, monkeypatch):
    calls = {}
    monkeypatch.setattr(
        menubar.subprocess, "Popen", lambda args, **k: calls.setdefault("args", args)
    )
    app.open_site()
    assert calls["args"] == ["open", "https://www.radiomeuh.com"]


def test_quit_app_closes_and_quits(app, monkeypatch):
    quit_called = []
    monkeypatch.setattr(
        menubar.rumps, "quit_application", lambda: quit_called.append(1)
    )
    monkeypatch.setattr(app, "stop", lambda: None)
    store = app.store
    app.quit_app()
    assert store.closed is True and quit_called == [1]


def test_quit_app_without_store(app, monkeypatch):
    app.store = None
    monkeypatch.setattr(menubar.rumps, "quit_application", lambda: None)
    monkeypatch.setattr(app, "stop", lambda: None)
    app.quit_app()  # must not raise when there is no store


# --- main -------------------------------------------------------------------
def test_main_runs_app(monkeypatch):
    monkeypatch.setattr(menubar, "default_db_path", lambda: "/tmp/x.db")
    monkeypatch.setattr(menubar, "TrackStore", FakeStore)
    ran = {}
    monkeypatch.setattr(
        menubar.RadioMeuhApp, "run", lambda self: ran.setdefault("r", 1)
    )
    menubar.main()
    assert ran["r"] == 1
