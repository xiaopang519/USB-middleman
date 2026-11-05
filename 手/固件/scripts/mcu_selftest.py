"""Self-test the middleman's uplink (monitor) channel -- no mouse required.

Installs a throwaway expression on the board that pushes a changing value into
the MONITOR op every frame.  If the PC can read those events, the whole
recording path is proven end to end.  The expression is removed afterwards and
never persisted, so the board's saved config is untouched.

    python mcu_selftest.py
"""
from __future__ import annotations

import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcu_hid import (  # noqa: E402
    CMD_APPEND_TO_EXPRESSION,
    CMD_CLEAR_EXPRESSIONS,
    Middleman,
    usage_name,
)

OP_PUSH = 0
OP_TIME = 6
OP_MONITOR = 44
OP_EOL = 36

TEST_USAGE = 0x0001ABCD


def build_expression(usage: int) -> bytes:
    """[TIME, PUSH usage, MONITOR, EOL] -> monitor(usage, frame_time) each frame."""
    elems = bytes([OP_TIME])
    elems += bytes([OP_PUSH]) + struct.pack("<I", usage)
    elems += bytes([OP_MONITOR, OP_EOL])
    return bytes([0, 4]) + elems          # expr 0, 4 elements


def main() -> int:
    with Middleman().open() as m:
        print("firmware config version:", m.get_version())
        print("inject support:", m.has_inject())

        m.set_monitor(True)
        time.sleep(0.05)
        # drain anything already queued
        m.read_events(50)

        print("installing test expression ...")
        m.send(CMD_CLEAR_EXPRESSIONS)
        time.sleep(0.02)
        m.send(CMD_APPEND_TO_EXPRESSION, build_expression(TEST_USAGE))
        time.sleep(0.05)

        n = 0
        t0 = time.time()
        while time.time() - t0 < 3.0:
            for ev in m.read_events(200):
                n += 1
                if n <= 8:
                    print("  event:", ev, usage_name(ev.usage))

        print("cleanup ...")
        m.send(CMD_CLEAR_EXPRESSIONS)
        m.set_monitor(False)

        print()
        if n:
            print("PASS: uplink works, %d events in 3 s" % n)
            return 0
        print("FAIL: no uplink events -- monitor endpoint not delivering")
        return 1


if __name__ == "__main__":
    sys.exit(main())
