"""Bring-up trace for the injection path.

NOTE: this needs the firmware to be built WITH the temporary debug markers.
They are deliberately stripped from the shipped build (they would pollute real
recordings - see reference/pitfalls.md #7).  To use this script, re-add to
remapper.cc:

    // once, near the other helpers
    void inject_debug(uint32_t usage, int32_t value) {
        if (!monitor_enabled) return;
        static std::unordered_map<uint32_t, int32_t> last;
        auto it = last.find(usage);
        if ((it != last.end()) && (it->second == value)) return;
        last[usage] = value;
        monitor_usage(usage, value, 0);
    }
    // + a declaration in remapper.h, then calls at the points below.

Enables the monitor and then issues one INJECT command, printing the firmware's
own debug markers so we can see exactly how far the command gets:

    0xFFFF0001  dx arrived in the config handler
    0xFFFF0002  value folded into the relative accumulator (0x1xxxx = X)
    0xFFFF0003  outgoing report queue depth
    0xFFFF0004  value actually written into the mouse-X field
    0xFFFF0006  usage was not present in our_usages_flat
    0xFFFF0007  queue depth after the report loop
    0xFFFF0008  reports_sent
    0xFFFF0012  report id + first payload bytes handed to the USB stack
    0xFFFF0014  tud_hid_n_report returned true
    0xFFFF0015  tud_hid_n_report returned false
    0xFFFF0016  tud_hid_n_ready(0), sampled when a report is sent
    0xFFFF0017  tud_hid_n_ready(0), sampled every frame

Do not emit a per-frame counter with inject_debug: 28,000 events in 4 s fills the
monitor's 7-item report and starves every other marker.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcu_hid import Middleman  # noqa: E402

LABEL = {
    0xFFFF0001: "cmd received (dx)",
    0xFFFF0002: "folded into accumulator",
    0xFFFF0003: "queue depth (pre-queue)",
    0xFFFF0004: "written to mouse-X field",
    0xFFFF0006: "usage MISSING from our_usages_flat",
    0xFFFF0007: "queue depth (post-queue)",
    0xFFFF0008: "reports_sent",
    0xFFFF0009: "send_report() calls (endpoint ready)",
}


def main() -> int:
    with Middleman().open() as m:
        print("inject support:", m.has_inject())
        m.set_monitor(True)
        time.sleep(0.3)
        m.read_events(200)

        print("sending inject_mouse(dx=150, dy=80) ...")
        m.inject_mouse(dx=150, dy=80)

        seen: dict[int, int] = {}
        t0 = time.time()
        while time.time() - t0 < 2.5:
            for ev in m.read_events(150):
                if (ev.usage & 0xFFFF0000) == 0xFFFF0000:
                    seen[ev.usage] = ev.value
                    print("  %-40s = %d" % (LABEL.get(ev.usage, "?"), ev.value))
        m.set_monitor(False)

        print()
        for u in sorted(LABEL):
            mark = "yes" if u in seen else "NO "
            print("  %-42s %s %s" % (LABEL[u], mark,
                                     ("%d" % seen[u]) if u in seen else ""))
        if 0xFFFF0001 not in seen:
            print("\n=> the config handler never saw the command")
        elif 0xFFFF0002 not in seen:
            print("\n=> command received but process_mapping never applied it")
        elif 0xFFFF0004 not in seen:
            print("\n=> applied to accumulated but never written into a report")
        else:
            print("\n=> the firmware DID write the movement into the report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
