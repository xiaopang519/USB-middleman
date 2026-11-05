"""Flash a .uf2 onto the middleman board over USB -- no BOOT button needed.

Asks the running firmware to reboot into the RP2040 bootloader
(ConfigCommand::RESET_INTO_BOOTSEL), waits for the RPI-RP2 mass-storage
volume, copies the file, then waits for the board to come back.

    python mcu_flash.py path\\to\\firmware.uf2
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcu_hid import CMD_RESET_INTO_BOOTSEL, Middleman  # noqa: E402


def volumes() -> set[str]:
    return {d for d in "DEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(d + ":\\")}


def find_rpi_rp2(timeout: float) -> str | None:
    """Wait for the RPI-RP2 drive and return its root (e.g. 'E:\\\\')."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            root = letter + ":\\"
            if not os.path.exists(root):
                continue
            try:
                label = None
                import ctypes
                buf = ctypes.create_unicode_buffer(261)
                fs = ctypes.create_unicode_buffer(261)
                ctypes.windll.kernel32.GetVolumeInformationW(
                    ctypes.c_wchar_p(root), buf, 261, None, None, None, fs, 261)
                label = buf.value
            except Exception:
                label = ""
            if label and "RPI-RP2" in label.upper():
                return root
        time.sleep(0.3)
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    uf2 = os.path.abspath(sys.argv[1])
    if not os.path.isfile(uf2):
        print("no such file:", uf2)
        return 2
    print("firmware: %s (%d bytes)" % (uf2, os.path.getsize(uf2)))

    before = volumes()
    print("asking the board to reboot into the bootloader ...")
    try:
        with Middleman().open(want_monitor=False) as m:
            print("  current config version:", m.get_version())
            print("  current inject support:", m.has_inject())
            m.send(CMD_RESET_INTO_BOOTSEL)
    except Exception as exc:
        print("  (could not talk to the board: %s)" % exc)
        print("  -> hold BOOT, tap RUN, release BOOT instead, then rerun.")
        return 1

    root = find_rpi_rp2(30)
    if root is None:
        print("RPI-RP2 volume did not appear; plugged-in volumes now:", sorted(volumes()))
        return 1
    print("bootloader volume at", root)

    dst = os.path.join(root, os.path.basename(uf2))
    print("copying ->", dst)
    shutil.copyfile(uf2, dst)
    print("copy done, waiting for the board to reboot ...")

    deadline = time.time() + 40
    while time.time() < deadline:
        if not os.path.exists(root):
            break
        time.sleep(0.5)
    time.sleep(3.0)

    try:
        with Middleman().open(want_monitor=False) as m:
            print("board is back. config version:", m.get_version())
            print("board is back. inject support:", m.has_inject())
    except Exception as exc:
        print("board has not re-enumerated yet (%s)" % exc)
        print("volumes now:", sorted(volumes()))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
