"""Record a human demonstration: input events AND the screen, time-aligned.

This is what makes a demo reusable.  Watching someone log in tells you the
steps; recording it *with the screen at each step* turns it into a labelled
trace you can turn into a replay script.

    python mcu_demo_record.py --seconds 120 --out demo

Writes into <out>/:
    events.jsonl    every mouse/keyboard event the board saw, with timestamps
    frames/*.jpg    screen grabs, only while something is happening
    index.csv       frame time -> file, so you can jump to "what was on screen
                    when that click happened"
    summary.txt     what was recorded, and anything that looked like typing

Notes
-----
* Input comes from the board's monitor stream, so there are no Windows hooks and
  the user's mouse keeps its normal latency.
* Frames are taken by a separate thread so grabbing never delays event reads
  (the monitor report holds only 7 items and drops the rest).
* **Typed text ends up in events.jsonl in cleartext.**  If a password was typed,
  run with --redact-keys to drop keyboard events, or delete the time range
  afterwards.  Nothing here uploads anything.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

import numpy as np
from PIL import ImageGrab

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcu_hid import Middleman, usage_name  # noqa: E402

FRAME_MIN_GAP = 0.35         # seconds between event-driven frames
HEARTBEAT = 3.0              # ... and a frame at least this often
JPEG_Q = 72


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120.0, help="0 = until Ctrl-C")
    ap.add_argument("--out", default="demo")
    ap.add_argument("--redact-keys", action="store_true",
                    help="do not write keyboard events at all")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    frames_dir = os.path.join(out, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    stop = threading.Event()
    state = {"last_activity": 0.0, "frames": 0, "events": 0}
    frames: list[tuple[float, str]] = []
    lock = threading.Lock()

    # ---------------------------------------------------------- frame thread
    def frame_loop(t0: float) -> None:
        last = 0.0
        last_beat = 0.0
        while not stop.is_set():
            time.sleep(0.08)
            now = time.time() - t0
            with lock:
                recent = state["last_activity"] > last
            due = recent and (now - last) >= FRAME_MIN_GAP
            beat = (now - last_beat) >= HEARTBEAT
            if not (due or beat):
                continue
            try:
                img = ImageGrab.grab().convert("RGB")
                # downscale a little: halves the bytes, still readable
                img = img.resize((img.width // 2, img.height // 2))
                fname = "f%08.2f.jpg" % now
                img.save(os.path.join(frames_dir, fname), "JPEG", quality=JPEG_Q)
                with lock:
                    frames.append((now, fname))
                    state["frames"] += 1
                last, last_beat = now, now
            except Exception as exc:                    # never kill the recording
                print("  (frame grab failed: %s)" % exc)

    # ------------------------------------------------------------------ main
    with Middleman().open() as m:
        print("firmware:", m.get_version(), " inject support:", m.has_inject())
        m.set_monitor(True)
        time.sleep(0.3)
        m.read_events(200)                              # drain stale events

        t0 = time.time()
        duration = 0.0
        fj = open(os.path.join(out, "events.jsonl"), "w", encoding="utf-8")
        ft = threading.Thread(target=frame_loop, args=(t0,), daemon=True)
        ft.start()

        print("RECORDING -> %s" % out)
        print("  go ahead and do the demo now%s" %
              (" (for %.0f s)" % args.seconds if args.seconds else " (Ctrl-C to stop)"))

        counts: dict[str, int] = {}
        keys_typed: list[str] = []
        try:
            while not (args.seconds and (time.time() - t0) >= args.seconds):
                got = m.read_events(120)
                if got:
                    with lock:
                        state["last_activity"] = time.time() - t0
                for ev in got:
                    name = usage_name(ev.usage)
                    counts[name] = counts.get(name, 0) + 1
                    if name.startswith("KEY_") and ev.value:
                        keys_typed.append(name[4:])
                    if args.redact_keys and name.startswith("KEY_"):
                        continue
                    fj.write(json.dumps({
                        "t": round(time.time() - t0, 4),
                        "u": "%08X" % ev.usage,
                        "n": name,
                        "v": ev.value,
                        "p": ev.hub_port,
                    }, separators=(",", ":")) + "\n")
                    with lock:
                        state["events"] += 1
                if got:
                    fj.flush()
        except KeyboardInterrupt:
            pass
        finally:
            duration = time.time() - t0
            stop.set()
            time.sleep(0.3)
            fj.close()
            m.set_monitor(False)

        with lock:
            frames_copy = list(frames)

    # ------------------------------------------------------------- index.csv
    with open(os.path.join(out, "index.csv"), "w", encoding="utf-8") as fh:
        fh.write("time_s,frame\n")
        for t, name in frames_copy:
            fh.write("%.2f,%s\n" % (t, name))

    # ----------------------------------------------------------- summary.txt
    lines = [
        "demonstration recording",
        "duration      : %.1f s" % duration,
        "events written: %d" % state["events"],
        "frames        : %d" % state["frames"],
        "keyboard redacted: %s" % args.redact_keys,
        "",
        "event kinds:",
    ]
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:25]:
        lines.append("  %-16s %7d" % (k, v))
    if keys_typed:
        lines += ["", "keys pressed (usage codes, in order):", "  " + " ".join(keys_typed)]
    with open(os.path.join(out, "summary.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print()
    print("\n".join(lines))
    print()
    print("Now look at frames/ with index.csv: each frame is what was on screen")
    print("around that time, which is what turns this into a replay script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
