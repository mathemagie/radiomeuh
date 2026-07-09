"""Shared test fixtures + a fake ``rumps`` so the menu bar app is testable headless.

The real ``rumps`` works headlessly for construction, but ``rumps.alert`` is a
blocking modal and ``rumps.quit_application`` would terminate the test run. We
replace the whole module with a lightweight fake *before* ``radiomeuh.menubar``
imports it, giving full control and no AppKit side effects.
"""

import sys
import types

import pytest

# --- Fake rumps (installed before any test module imports menubar) ----------
_rumps = types.ModuleType("rumps")


class _MenuItem:
    def __init__(self, title, callback=None):
        self.title = title
        self.callback = callback
        self.state = 0
        self.children = []

    def set_callback(self, cb):
        self.callback = cb

    def add(self, item):
        self.children.append(item)

    def clear(self):
        self.children = []


class _Timer:
    def __init__(self, callback, interval):
        self.callback = callback
        self.interval = interval
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class _App:
    def __init__(self, name, quit_button=None):
        self.name = name
        self.title = name
        self.quit_button = quit_button
        self.menu = None

    def run(self):
        self.ran = True


_rumps.MenuItem = _MenuItem
_rumps.Timer = _Timer
_rumps.App = _App
_rumps.alert = lambda *a, **k: 1
_rumps.notification = lambda *a, **k: None
_rumps.quit_application = lambda *a, **k: None

sys.modules["rumps"] = _rumps


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Keep every test off the real home/XDG dirs."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
