"""Lag-free input recorder driven by the RP2040 middleman.

Why this instead of Windows hooks: the board sits *in front of* the PC on the
USB path, so it sees every mouse/keyboard report before Windows does.  It
streams those events out over a vendor HID input report; we just read them.
Nothing is installed into the game process, nothing hooks the input path, and
the user's mouse keeps its normal latency.

Output: JSONL, one event per line:
    {"t": 12.345, "u": "00010030", "n": "MOUSE_X", "v": 3, "p": 1}

Usage:
    python mcu_record.py --seconds 60 --out rec/play.jsonl
    python mcu_record.py --seconds 0            # run until Ctrl-C
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcu_hid import Middleman, usage_name  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0, help="0 = until Ctrl-C")
    ap.add_argument("--out", default="rec/play.jsonl")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with Middleman().open() as m:
        version = m.get_version()
        inject = m.has_inject()
        if not args.quiet:
            print("firmware config version %d, inject support: %s" % (version, inject))
        m.set_monitor(True)
        if not args.quiet:
            print("monitor on -> %s" % out_path)
            print("press Ctrl-C to stop" if args.seconds == 0 else
                  "recording for %.0f s" % args.seconds)

        counts: dict[str, int] = {}
        n = 0
        t0 = time.time()
        last_beat = 0.0
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                while True:
                    if args.seconds and (time.time() - t0) >= args.seconds:
                        break
                    got = m.read_events(200)
                    for ev in got:
                        name = usage_name(ev.usage)
                        counts[name] = counts.get(name, 0) + 1
                        fh.write(json.dumps({
                            "t": round(ev.t - t0, 4),
                            "u": "%08X" % ev.usage,
                            "n": name,
                            "v": ev.value,
                            "p": ev.hub_port,
                        }, separators=(",", ":")) + "\n")
                        n += 1
                    if got:
                        fh.flush()
                    if not args.quiet and (time.time() - last_beat) >= 2.0:
                        last_beat = time.time()
                        print("  %6d events  %5.1fs" % (n, time.time() - t0), flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            m.set_monitor(False)

        if not args.quiet:
            print("\nrecorded %d events in %.1f s" % (n, time.time() - t0))
            for name, c in sorted(counts.items(), key=lambda kv: -kv[1])[:20]:
                print("  %-12s %7d" % (name, c))
    return 0


if __name__ == "__main__":
    sys.exit(main())
