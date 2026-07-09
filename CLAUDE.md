# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
# Setup
python3 -m venv .venv && ./.venv/bin/pip install -e '.[menubar,dev]'
git config core.hooksPath .githooks    # enable pre-commit lint/format gate (once)

# Lint / format (also what the pre-commit hook enforces)
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/black --check src scripts

# Run the CLI
.venv/bin/radiomeuh                    # play 128k stream + live now-playing
.venv/bin/radiomeuh -q 64 | --no-meta | --url URL | --debug
.venv/bin/radiomeuh history [-n N] | stats

# Run / rebuild the menu bar app
python -m radiomeuh.menubar            # run in-terminal for dev (Ctrl-C to stop)
./scripts/build_app.sh                 # (re)generate RadioMeuh.app
open RadioMeuh.app
make restart                           # kill a stuck stream/app and relaunch (= ./scripts/kill.sh)
```

There is **no test suite**; `dev` deps are only `ruff` + `black`.

## Architecture

Three modules under `src/radiomeuh/`, split by dependency weight:

- **`core.py`** — all domain logic, **no terminal or GUI imports**, so both front-ends share it. Contains `find_player`, `MetadataReader`, `TrackStore`, stream URLs, and ICY parsing.
- **`cli.py`** — terminal UI + argparse (`radiomeuh` entry point → `cli:main`).
- **`menubar.py`** — macOS menu bar app built on `rumps` (`radiomeuh-menubar` gui entry point).

### Decoupled playback vs. metadata (the core design)

Audio and now-playing info come from **two independent connections to the same stream**:

1. **Playback** is an external player subprocess (`ffplay`/`mpv`/`mplayer`/`cvlc`) — Radio Meuh never decodes audio itself.
2. **`MetadataReader`** is a separate daemon thread that opens its own `Icy-MetaData` connection and reads `StreamTitle` from the ICY byte stream. It exposes state through `snapshot()` under a lock; the reader only holds that lock briefly (never during blocking I/O), so callers on the main thread never block on it.

Each announced track is written to SQLite (`TrackStore`, one connection shared across threads under a lock) at `~/.local/share/radiomeuh/tracks.db`. Persistence failures are suppressed so they can never interrupt playback.

## Gotchas specific to this repo

- **`find_player` must search Homebrew dirs, not just `PATH`.** When the `.app` is launched from Finder/Dock, macOS gives it a minimal `PATH` that excludes `/opt/homebrew/bin`, so `shutil.which` alone fails to find `ffplay`. `find_player` augments the search path and returns the **absolute** binary path.
- **Never use blocking `rumps.alert` for errors in the menu bar app.** `rumps.alert` calls `NSAlert.runModal()`, which freezes the entire status menu (including Quit) until dismissed. Use the non-blocking `_notify()` helper (an `osascript display notification` banner) plus an inline menu hint instead. `rumps.notification` does **not** work here — the launcher runs `.venv/bin/python`, so rumps looks for `Info.plist` next to that interpreter and the notification center comes back `None`.
- **The `.app` runs the editable-installed package via the venv interpreter** (`exec .venv/bin/python -m radiomeuh.menubar`). Code edits are picked up on the next relaunch (`make restart`); rerun `build_app.sh` only when the launcher or `Info.plist` needs to change.

## Conventions

- Python ≥ 3.10, line length 88. Ruff lint rules: `E, F, I, UP, B, SIM, C4`.
- The core CLI has **zero runtime dependencies** (stdlib + an external audio player); keep it that way. `rumps` is an optional `[menubar]` extra.
