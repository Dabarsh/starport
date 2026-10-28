"""Headless Starport: watch a folder and copy keepers, no menu bar. Handy for testing (milestone 1).

    python cli.py --source /Volumes/EOS_DIGITAL/DCIM --staging ~/Pictures/Starport_Staging --min 4
    python cli.py --source ... --once      # one scan, then exit
"""

from __future__ import annotations

import argparse
import os
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--source", required=True)
parser.add_argument("--staging", default="~/Pictures/Starport_Staging")
parser.add_argument("--min", type=int, default=4, dest="min_rating")
parser.add_argument("--exact", action="store_true", help="match the rating exactly instead of at-or-above")
parser.add_argument("--once", action="store_true", help="scan once and exit")
parser.add_argument("--home", help="data folder (default: a temp folder, so the app's history is untouched)")
args = parser.parse_args()

os.environ["STARPORT_HOME"] = args.home or tempfile.mkdtemp(prefix="starport-cli-")

import engine  # noqa: E402  (after STARPORT_HOME is set)


def show(e):
    for ev in reversed(e.events(20)):
        if ev["ts"] > show.since:
            stars = "" if ev["rating"] is None else f" {ev['rating']}★"
            print(f"{time.strftime('%H:%M:%S', time.localtime(ev['ts']))}  {ev['kind']:<8} {ev['file']}{stars}  {ev['detail']}")
    show.since = time.time()


show.since = 0.0

e = engine.Engine(notify=lambda title, body: print(f"[batch] {body}"), open_lightroom=lambda: print("[lightroom] open"))
e.update_settings(source_folder=args.source, staging_folder=args.staging, min_rating=args.min_rating,
                  match_mode="==" if args.exact else ">=", onboarded=True, paused=False,
                  open_lrc=False, launch_at_login=False, lrc_debounce_seconds=3)
print(f"data: {os.environ['STARPORT_HOME']}")
if args.once:
    e._run(("scan", True))
    show(e)
else:
    e.add_listener(lambda: show(e))
    e.start()
    print(f"watching {e.settings.source} -> {e.settings.staging}  (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        e.shutdown()
