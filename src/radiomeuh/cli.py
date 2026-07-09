"""Terminal UI for Radio Meuh.

Usage:
    radiomeuh                # start playing (128k mp3)
    radiomeuh --quality 64
    radiomeuh --no-meta      # skip the now-playing display
    radiomeuh --url URL      # play an arbitrary Icecast/ICY stream
    radiomeuh history [-n N] # show logged tracks
    radiomeuh stats          # show listening stats

Controls:
    Ctrl-C   stop and quit
"""

import argparse
import os
import signal
import sqlite3
import subprocess
import sys
import time

from .core import (
    STREAMS,
    MetadataReader,
    TrackStore,
    default_db_path,
    find_player,
)

# ANSI styling ---------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[38;5;150m"
PINK = "\033[38;5;211m"
YELLOW = "\033[38;5;222m"
GREY = "\033[38;5;244m"
CYAN = "\033[38;5;117m"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"

COW = r"""
        ^__^
        (oo)\_______
        (__)\       )\/\
            ||----w |
            ||     ||
"""


def supports_color() -> bool:
    return sys.stdout.isatty()


class Style:
    """Colorize helpers that no-op when output is not a TTY."""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return "".join(codes) + text + RESET


def render(
    style: Style,
    quality: str,
    reader: MetadataReader | None,
    spinner: str,
    elapsed: float,
    debug_suffix: str = "",
) -> str:
    lines = []
    lines.append(style(COW.rstrip("\n"), GREEN))
    lines.append("")
    # Law 1: a single, unrepeated tagline — the logo already says the name.
    lines.append(
        "  "
        + style("R A D I O   M E U H", BOLD, PINK)
        + "   "
        + style("independent radio", DIM, GREY)
    )
    lines.append("")

    started = reader is None or reader.started
    if not started:
        # Law 3: make the wait legible instead of a blank spinner.
        lines.append(
            "  "
            + style(spinner, PINK)
            + " "
            + style("Connecting to Radio Meuh…", YELLOW)
        )
    elif reader is not None:
        artist, title, history, changed_at = reader.snapshot()
        # Law 7: pulse a freshly-changed track for a moment so the beat lands.
        fresh = changed_at and (time.monotonic() - changed_at) < 2.5
        if artist:
            artist_style = (BOLD, PINK) if fresh else (BOLD, YELLOW)
            line = "  " + style("♪ ", PINK) + style(artist, *artist_style)
            if title:
                line += style("  —  ", GREY) + style(title, CYAN if fresh else GREY)
            lines.append(line)
        else:
            lines.append(
                "  " + style("♪ ", PINK) + style("(waiting for track info)", DIM, GREY)
            )
        # Law 10: surface the trail we used to throw away.
        for past in history:
            lines.append("     " + style("⟲ ", GREY) + style(past, DIM, GREY))
    lines.append("")

    mins, secs = divmod(int(elapsed), 60)
    status = (
        "  "
        + style(spinner, PINK)
        + " "
        + style("live", BOLD, GREEN)
        + style(f"   {quality}k mp3", GREY)
        + style(f"   {mins:02d}:{secs:02d}", DIM, GREY)
        + debug_suffix
    )
    lines.append(status)
    lines.append("")
    lines.append("  " + style("Ctrl-C to stop", DIM, GREY))
    return "\n".join(lines)


def show_db(command: str, db_path: str, limit: int, style: Style) -> int:
    if not os.path.exists(db_path):
        sys.stderr.write(f"No database yet at {db_path}. Play some radio first!\n")
        return 1
    store = TrackStore(db_path)
    try:
        if command == "history":
            rows = store.recent(limit)
            if not rows:
                print("No tracks recorded yet.")
                return 0
            print(style(f"  ♪ last {len(rows)} tracks on Radio Meuh", BOLD, PINK))
            print()
            for played_at, artist, title in rows:
                when = played_at.replace("T", " ")[:16]
                print(
                    "  "
                    + style(when, DIM, GREY)
                    + "  "
                    + style(artist, BOLD, YELLOW)
                    + (style("  —  " + title, GREY) if title else "")
                )
        else:  # stats
            s = store.stats()
            print(style("  ♪ Radio Meuh listening stats", BOLD, PINK))
            print()
            print("  " + style("plays logged   ", GREY) + style(str(s["total"]), BOLD))
            print(
                "  " + style("unique tracks  ", GREY) + style(str(s["distinct"]), BOLD)
            )
            if s["span"] and s["span"][0]:
                print(
                    "  "
                    + style("since          ", GREY)
                    + style(s["span"][0].replace("T", " ")[:16], BOLD)
                )
            print()
            print(style("  top artists", GREY))
            for artist, count in s["top"]:
                bar = "▮" * min(count, 30)
                print(
                    "  "
                    + style(f"{count:>3} ", DIM, GREY)
                    + style(bar, PINK)
                    + " "
                    + style(artist, YELLOW)
                )
    finally:
        store.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radiomeuh",
        description="Stream Radio Meuh in your terminal.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="play",
        choices=["play", "history", "stats"],
        help="play (default), or view your logged track history/stats",
    )
    parser.add_argument(
        "-q",
        "--quality",
        choices=["128", "64"],
        default="128",
        help="stream bitrate (default: 128)",
    )
    parser.add_argument("--url", help="override the stream URL")
    parser.add_argument(
        "--no-meta", action="store_true", help="don't display now-playing metadata"
    )
    parser.add_argument(
        "--db",
        default=default_db_path(),
        help="SQLite database path (default: XDG data dir)",
    )
    parser.add_argument(
        "--no-db", action="store_true", help="don't record tracks to the database"
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=20,
        help="number of rows for the 'history' command",
    )
    parser.add_argument(
        "--debug", action="store_true", help="show extra diagnostics (player, db path)"
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    style = Style(supports_color())

    if args.command in ("history", "stats"):
        return show_db(args.command, args.db, args.limit, style)

    url = args.url or STREAMS[args.quality]

    store = None
    if not args.no_db:
        try:
            store = TrackStore(args.db)
        except sqlite3.Error as exc:
            sys.stderr.write(
                f"Warning: could not open database ({exc}); not recording.\n"
            )

    player = find_player()
    if player is None:
        sys.stderr.write(
            "No audio player found. Please install one of: ffplay (ffmpeg), mpv, "
            "mplayer, or vlc.\n"
        )
        return 1
    player_name, cmd = player

    reader = None
    if not args.no_meta or store is not None:
        # We still want the reader when a store is active, to record tracks.
        reader = MetadataReader(url, store=store)
        reader.start()

    try:
        proc = subprocess.Popen(
            cmd + [url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        sys.stderr.write(f"Failed to launch {player_name}: {exc}\n")
        return 1

    is_tty = sys.stdout.isatty()
    spinners = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    start = time.monotonic()
    last_frame_lines = 0

    if is_tty:
        sys.stdout.write(HIDE_CURSOR)

    display_reader = reader if not args.no_meta else None
    debug_suffix = ""
    if args.debug:
        debug_suffix = style(
            f"   {player_name} · {args.db if store else 'no-db'}", DIM, GREY
        )

    def cleanup(*_):
        if is_tty:
            sys.stdout.write(SHOW_CURSOR + "\n")
            sys.stdout.flush()
        if reader:
            reader.stop()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        if store is not None:
            store.close()

    signal.signal(signal.SIGINT, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))

    try:
        i = 0
        while True:
            if proc.poll() is not None:
                # player exited (e.g. network drop); report and stop
                if is_tty:
                    sys.stdout.write(SHOW_CURSOR)
                sys.stderr.write(f"\n{player_name} stopped (stream ended?).\n")
                break

            if is_tty:
                spinner = spinners[i % len(spinners)]
                frame = render(
                    style,
                    args.quality,
                    display_reader,
                    spinner,
                    time.monotonic() - start,
                    debug_suffix,
                )
                # move cursor up to overwrite previous frame
                if last_frame_lines:
                    sys.stdout.write(f"\033[{last_frame_lines}A")
                # clear each line then print
                for line in frame.split("\n"):
                    sys.stdout.write("\033[2K" + line + "\n")
                last_frame_lines = frame.count("\n") + 1
                sys.stdout.flush()

            i += 1
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()

    return 0


if __name__ == "__main__":
    sys.exit(main())
