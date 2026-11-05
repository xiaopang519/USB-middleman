"""Enumerate the HID Remapper (middleman) board's HID interfaces.

Prints every HID top-level collection belonging to the remapper VID/PID so we
can find (a) the config/control interface and (b) the monitor input interface.
"""
import sys

import hid

VID = 0xCAFE
PID = 0xBAF2


def main() -> int:
    devs = hid.enumerate(VID, PID)
    if not devs:
        print("NO remapper device found (VID %04X PID %04X)" % (VID, PID))
        print("--- all HID devices ---")
        for d in hid.enumerate():
            print(
                "%04X:%04X if=%s up=%04X u=%04X  %s"
                % (
                    d["vendor_id"],
                    d["product_id"],
                    d.get("interface_number"),
                    d.get("usage_page", 0),
                    d.get("usage", 0),
                    (d.get("product_string") or "")[:40],
                )
            )
        return 1

    print("found %d top-level collections for %04X:%04X" % (len(devs), VID, PID))
    for i, d in enumerate(devs):
        print("[%d] if=%s usage_page=%04X usage=%04X" % (
            i, d.get("interface_number"), d.get("usage_page", 0), d.get("usage", 0)))
        print("    product=%r" % (d.get("product_string"),))
        print("    serial=%r" % (d.get("serial_number"),))
        print("    mfr=%r" % (d.get("manufacturer_string"),))
        print("    path=%r" % (d.get("path"),))
    return 0


if __name__ == "__main__":
    sys.exit(main())
