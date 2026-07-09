"""Radio Meuh — macOS menu bar app.

A little 🐮 in your menu bar that streams Radio Meuh, shows the current track,
keeps a recently-played list, and logs everything to the same SQLite database
used by the CLI.

Run directly for development:
    python -m radiomeuh.menubar

Or launch the bundled RadioMeuh.app (menu-bar only, no Dock icon).
"""

import subprocess

import rumps

from .core import (
    STREAMS,
    MetadataReader,
    TrackStore,
    _join,
    default_db_path,
    find_player,
)

IDLE_ICON = "🐮"
PLAY_ICON = "🎧"


class RadioMeuhApp(rumps.App):
    def __init__(self):
        super().__init__(IDLE_ICON, quit_button=None)
        self.quality = "128"
        self.proc = None
        self.reader = None
        self.store = None
        try:
            self.store = TrackStore(default_db_path())
        except Exception:
            self.store = None

        # --- Menu ---------------------------------------------------------
        self.play_item = rumps.MenuItem("▶  Play", callback=self.toggle_play)
        self.now_item = rumps.MenuItem("Not playing")
        self.now_item.set_callback(None)  # display-only
        self.recent_menu = rumps.MenuItem("Recently played")

        quality_menu = rumps.MenuItem("Quality")
        self.q128 = rumps.MenuItem(
            "128 kbps", callback=lambda s: self.set_quality("128")
        )
        self.q64 = rumps.MenuItem("64 kbps", callback=lambda s: self.set_quality("64"))
        self.q128.state = 1
        quality_menu.add(self.q128)
        quality_menu.add(self.q64)

        self.menu = [
            self.play_item,
            None,
            self.now_item,
            self.recent_menu,
            None,
            quality_menu,
            rumps.MenuItem("Listening stats…", callback=self.show_stats),
            rumps.MenuItem("Open website", callback=self.open_site),
            None,
            rumps.MenuItem("Quit Radio Meuh", callback=self.quit_app),
        ]

        # Poll metadata a few times a second to refresh the display.
        self._last_track = None
        self.timer = rumps.Timer(self.refresh, 1)
        self.timer.start()

    # --- Playback ---------------------------------------------------------
    @property
    def playing(self):
        return self.proc is not None and self.proc.poll() is None

    def toggle_play(self, _=None):
        if self.playing:
            self.stop()
        else:
            self.start()

    def start(self):
        player = find_player()
        if player is None:
            rumps.alert(
                "Radio Meuh",
                "No audio player found.\nInstall ffmpeg:  brew install ffmpeg",
            )
            return
        _, cmd = player
        url = STREAMS[self.quality]
        try:
            self.proc = subprocess.Popen(
                cmd + [url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            rumps.alert("Radio Meuh", f"Could not start playback:\n{exc}")
            return
        self.reader = MetadataReader(url, store=self.store)
        self.reader.start()
        self.title = PLAY_ICON
        self.play_item.title = "⏸  Stop"

    def stop(self):
        if self.reader:
            self.reader.stop()
            self.reader = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.title = IDLE_ICON
        self.play_item.title = "▶  Play"
        self.now_item.title = "Not playing"
        self._last_track = None

    def set_quality(self, quality):
        self.quality = quality
        self.q128.state = 1 if quality == "128" else 0
        self.q64.state = 1 if quality == "64" else 0
        if self.playing:  # re-open the stream at the new bitrate
            self.stop()
            self.start()

    # --- Display refresh --------------------------------------------------
    def refresh(self, _=None):
        # Detect a stream that died on its own (network drop, etc.).
        if self.proc is not None and self.proc.poll() is not None and self.reader:
            self.stop()
            return
        if not self.reader:
            return
        artist, title, history, _changed = self.reader.snapshot()
        current = _join(artist, title) if artist else None
        if current != self._last_track:
            self._last_track = current
            self.now_item.title = f"♪  {current}" if current else "♪  (waiting…)"
            self._rebuild_recent(history)

    def _rebuild_recent(self, history):
        self.recent_menu.clear()
        if not history:
            empty = rumps.MenuItem("—")
            empty.set_callback(None)
            self.recent_menu.add(empty)
            return
        for past in history:
            item = rumps.MenuItem(past)
            item.set_callback(None)
            self.recent_menu.add(item)

    # --- Other menu actions ----------------------------------------------
    def show_stats(self, _=None):
        if not self.store:
            rumps.alert("Radio Meuh", "No database available.")
            return
        s = self.store.stats()
        lines = [
            f"Plays logged: {s['total']}",
            f"Unique tracks: {s['distinct']}",
        ]
        if s["span"] and s["span"][0]:
            lines.append(f"Since: {s['span'][0].replace('T', ' ')[:16]}")
        lines.append("")
        lines.append("Top artists:")
        for artist, count in s["top"]:
            lines.append(f"  {count:>3}  {artist}")
        rumps.alert("Radio Meuh — Listening stats", "\n".join(lines))

    def open_site(self, _=None):
        subprocess.Popen(["open", "https://www.radiomeuh.com"])

    def quit_app(self, _=None):
        self.stop()
        if self.store:
            self.store.close()
        rumps.quit_application()


def main():
    RadioMeuhApp().run()


if __name__ == "__main__":
    main()
