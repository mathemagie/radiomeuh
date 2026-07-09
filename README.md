# radiomeuh 🐮

Stream [Radio Meuh](https://www.radiomeuh.com) — independent French radio — from your
**terminal** or your **macOS menu bar**, with live now-playing track info and a
SQLite history of everything that played.

## Requirements

- Python 3.10+
- An audio player: **ffplay** (from ffmpeg), `mpv`, `mplayer`, or `vlc` — the first
  one found is used.

  ```sh
  brew install ffmpeg   # macOS
  ```

## Install

```sh
git clone https://github.com/mathemagie/radiomeuh.git
cd radiomeuh
python3 -m venv .venv
./.venv/bin/pip install -e '.[menubar,dev]'   # drop [menubar] if you only want the CLI
```

This installs a `radiomeuh` command into the venv.

## Project layout

```
radiomeuh/
├── src/radiomeuh/
│   ├── core.py       # domain logic: streaming, ICY metadata, SQLite store
│   ├── cli.py        # terminal UI + argparse
│   └── menubar.py    # macOS menu bar app (rumps)
├── scripts/build_app.sh   # builds RadioMeuh.app
├── .githooks/pre-commit   # ruff + black before every commit
└── pyproject.toml         # package, entry points, tool config
```

## CLI usage

```sh
radiomeuh                 # play the 128k stream + live now-playing
radiomeuh -q 64           # lower bitrate
radiomeuh --no-meta       # hide the now-playing display (still records)
radiomeuh --url URL       # play any other Icecast/ICY stream
radiomeuh --debug         # show the player + db path in the status line
```

Press **Ctrl-C** to stop. While playing you'll see the current **Artist — Title**, a
short **recently-played trail** (⟲), and a brief colour pulse when the track changes.

### Track history (SQLite)

Every track announced by the stream is logged to
`~/.local/share/radiomeuh/tracks.db` (one row per play, local + UTC timestamps).

```sh
radiomeuh history          # last 20 tracks
radiomeuh history -n 50    # last 50
radiomeuh stats            # totals + top artists
```

Options: `--db PATH` (different file), `--no-db` (don't record). The DB is plain
SQLite, so query it directly:

```sh
sqlite3 ~/.local/share/radiomeuh/tracks.db \
  "SELECT artist, COUNT(*) c FROM tracks GROUP BY artist ORDER BY c DESC LIMIT 10;"
```

## Menu bar app (macOS) 🐮

```sh
./scripts/build_app.sh    # creates RadioMeuh.app
open RadioMeuh.app        # or double-click / launch via Spotlight
```

A **🐮** appears in your menu bar (**🎧** while playing). The menu offers Play/Stop,
the current track + a Recently-played submenu, Quality (128/64), Listening stats, and
Open website. It has no Dock icon (`LSUIElement`). To start it at login, add
`RadioMeuh.app` in *System Settings → General → Login Items*.

## Development

```sh
git config core.hooksPath .githooks   # enable the pre-commit hook (once)
ruff check . && ruff format . && black src   # lint + format manually
```

The pre-commit hook runs `ruff check`, `ruff format --check`, and `black --check`,
blocking commits that don't pass.

## How it works

- Audio plays through `ffplay` (or another detected player) from the Infomaniak stream
  `https://radiomeuh.ice.infomaniak.ch/radiomeuh-128.mp3`.
- The current track is read live from the stream's ICY `StreamTitle` metadata over a
  separate lightweight connection, so it stays in sync without touching playback — and
  each new track is written to SQLite as it's announced.

## License

MIT
