"""PC-side driver for the RP2040 "middleman" (HID Remapper firmware).

Talks to the board over two vendor HID collections exposed on interface 1:

  usage_page 0xFF00 / usage 0x20  -> config   (Feature report id 100, 32 bytes)
  usage_page 0xFF00 / usage 0x21  -> monitor  (Input  report id 101, 63 bytes)

Two capabilities:

  * monitor: the board streams every raw input event it receives from the real
    mouse/keyboard.  Reading that stream gives us a lag-free recording of the
    user's play -- no Windows hooks, nothing injected into the game process.

  * inject: a firmware command (ConfigCommand::INJECT) that makes the board
    emit its own mouse/keyboard reports, i.e. gives the bot its "hand" back at
    hardware level.  Requires the patched firmware; probe with `has_inject()`.

Nothing here writes to the game. Nothing here runs unless called.
"""
from __future__ import annotations

import struct
import time
import zlib
from dataclasses import dataclass

import hid

VID = 0xCAFE
PID = 0xBAF2

USAGE_PAGE_VENDOR = 0xFF00
USAGE_CONFIG = 0x20
USAGE_MONITOR = 0x21

REPORT_ID_CONFIG = 100
REPORT_ID_MONITOR = 101

CONFIG_SIZE = 32
CONFIG_VERSION = 18

# --- ConfigCommand enum (types.h) -------------------------------------------
CMD_NO_COMMAND = 0
CMD_RESET_INTO_BOOTSEL = 1
CMD_SET_CONFIG = 2
CMD_GET_CONFIG = 3
CMD_CLEAR_MAPPING = 4
CMD_ADD_MAPPING = 5
CMD_GET_MAPPING = 6
CMD_PERSIST_CONFIG = 7
CMD_GET_OUR_USAGES = 8
CMD_GET_THEIR_USAGES = 9
CMD_SUSPEND = 10
CMD_RESUME = 11
CMD_CLEAR_EXPRESSIONS = 19
CMD_APPEND_TO_EXPRESSION = 20
CMD_GET_EXPRESSION = 21
CMD_SET_MONITOR_ENABLED = 22

# --- extensions added by our patched firmware -------------------------------
CMD_INJECT = 26          # data: int16 dx, int16 dy, int16 wheel, uint8 buttons, uint8 flags
CMD_INJECT_KB = 27       # data: uint8 n, then n * (uint8 usage, uint8 down)
CMD_SD_INFO = 28         # reserved for the SD-logging build

# marker our patched firmware returns for CMD_INJECT, proving it is installed
INJECT_MARKER = b"INJ1"

# --- usages ------------------------------------------------------------------
USAGE_X = 0x00010030
USAGE_Y = 0x00010031
USAGE_WHEEL = 0x00010038
USAGE_HWHEEL = 0x000C0238
MOUSE_BUTTONS = [0x00090001, 0x00090002, 0x00090003, 0x00090004, 0x00090005]
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_4, BTN_5 = MOUSE_BUTTONS


def usage_name(usage: int) -> str:
    """Human-readable name for the usages we care about."""
    fixed = {
        USAGE_X: "MOUSE_X",
        USAGE_Y: "MOUSE_Y",
        USAGE_WHEEL: "WHEEL",
        USAGE_HWHEEL: "HWHEEL",
        0x00090001: "BTN_LEFT",
        0x00090002: "BTN_RIGHT",
        0x00090003: "BTN_MIDDLE",
        0x00090004: "BTN_4",
        0x00090005: "BTN_5",
    }
    if usage in fixed:
        return fixed[usage]
    page, code = (usage >> 16) & 0xFFFF, usage & 0xFFFF
    if page == 0x07:
        return "KEY_%02X" % code
    if page == 0x09:
        return "BTN_%02X" % code
    if page == 0x01:
        return "GD_%02X" % code
    return "%04X:%04X" % (page, code)


@dataclass
class MonitorEvent:
    t: float
    usage: int
    value: int
    hub_port: int

    def __str__(self) -> str:
        return "%.4f %-12s %6d (port %d)" % (
            self.t, usage_name(self.usage), self.value, self.hub_port)


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def build_set_report(command: int, payload: bytes = b"", version: int = CONFIG_VERSION) -> bytes:
    """Build the 32-byte feature report payload for a config command."""
    body = bytes([version, command]) + payload
    if len(body) > CONFIG_SIZE - 4:
        raise ValueError("payload too long: %d" % len(body))
    body = body.ljust(CONFIG_SIZE - 4, b"\x00")
    return body + struct.pack("<I", _crc32(body))


def parse_get_report(data: bytes) -> bytes:
    """Validate and return the 28 bytes of data in a get-feature response.

    The response is *not* a set_feature_t: it is 28 bytes of command-specific
    data followed by a crc32.  There is no echoed command byte.
    """
    if len(data) < CONFIG_SIZE:
        raise ValueError("short response: %d" % len(data))
    if _crc32(data[: CONFIG_SIZE - 4]) != struct.unpack("<I", data[CONFIG_SIZE - 4:])[0]:
        raise ValueError("bad crc in response")
    return bytes(data[: CONFIG_SIZE - 4])


INVALID_RESPONSE = b"\xff" * (CONFIG_SIZE - 4)


class Middleman:
    """Handle on the board. Use as a context manager."""

    def __init__(self) -> None:
        self._config = None
        self._monitor = None

    # -- lifecycle ---------------------------------------------------------
    def open(self, want_monitor: bool = True) -> "Middleman":
        devs = hid.enumerate(VID, PID)
        if not devs:
            raise RuntimeError("middleman not found (%04X:%04X)" % (VID, PID))
        cfg_path = mon_path = None
        for d in devs:
            if d.get("usage_page") != USAGE_PAGE_VENDOR:
                continue
            if d.get("usage") == USAGE_CONFIG:
                cfg_path = d["path"]
            elif d.get("usage") == USAGE_MONITOR:
                mon_path = d["path"]
        if cfg_path is None:
            raise RuntimeError("config collection (0x20) not found")
        self._config = hid.device()
        self._config.open_path(cfg_path)
        if want_monitor:
            if mon_path is None:
                raise RuntimeError("monitor collection (0x21) not found")
            self._monitor = hid.device()
            self._monitor.open_path(mon_path)
            # non-blocking: hidapi ignores the timeout in blocking mode for
            # zero-wait drains, which used to make read_events() hang forever
            # on the first real event.
            self._monitor.set_nonblocking(True)
        return self

    def close(self) -> None:
        for d in (self._monitor, self._config):
            if d is not None:
                try:
                    d.close()
                except Exception:
                    pass
        self._monitor = self._config = None

    def __enter__(self) -> "Middleman":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- config channel ----------------------------------------------------
    def send(self, command: int, payload: bytes = b"") -> None:
        """Issue a SET_FEATURE config command. No response is waited for."""
        buf = bytes([REPORT_ID_CONFIG]) + build_set_report(command, payload)
        self._config.send_feature_report(buf)

    def _get_raw(self) -> bytes:
        """Read the 32-byte get-feature response (Windows needs +1 for the id)."""
        raw = bytes(self._config.get_feature_report(REPORT_ID_CONFIG, CONFIG_SIZE + 1))
        if len(raw) == CONFIG_SIZE + 1:
            raw = raw[1:]          # hidapi keeps the report id on Windows
        return raw

    def request(self, command: int, payload: bytes = b"") -> bytes:
        """Issue a command and read back its 28 bytes of get-feature data."""
        self.send(command, payload)
        time.sleep(0.002)
        try:
            raw = self._get_raw()
        except OSError as exc:
            raise RuntimeError("no response to command %d (%s)" % (command, exc)) from exc
        return parse_get_report(raw)

    def get_version(self) -> int:
        """CONFIG_VERSION the board's firmware reports."""
        return self.request(CMD_GET_CONFIG)[0]

    def has_inject(self) -> bool:
        """True if the board runs our patched firmware (INJECT implemented).

        The stock firmware answers an unknown command with INVALID_COMMAND,
        whose whole response is 0xFF.  Our patch answers with the marker
        b"INJ1" in the first four data bytes.
        """
        try:
            resp = self.request(CMD_INJECT, b"\x00" * 9)
        except Exception:
            return False
        return resp[:4] == INJECT_MARKER

    # -- introspection -----------------------------------------------------
    def get_config(self) -> dict:
        """Read the board's runtime config summary (GET_CONFIG)."""
        d = self.request(CMD_GET_CONFIG)
        (version, flags, unmapped, partial_scroll, mapping_count,
         our_usage_count, their_usage_count, interval_override,
         tap_hold, gpio_debounce, our_descriptor_number, macro_entry_duration,
         quirk_count) = struct.unpack_from("<BBBIHIIBIBBBH", d, 0)
        return {
            "version": version,
            "flags": flags,
            "unmapped_passthrough_layer_mask": unmapped,
            "partial_scroll_timeout": partial_scroll,
            "mapping_count": mapping_count,
            "our_usage_count": our_usage_count,
            "their_usage_count": their_usage_count,
            "interval_override": interval_override,
            "tap_hold_threshold": tap_hold,
            "gpio_debounce_time_ms": gpio_debounce,
            "our_descriptor_number": our_descriptor_number,
            "macro_entry_duration": macro_entry_duration,
            "quirk_count": quirk_count,
        }

    def _indexed(self, command: int, index: int) -> bytes:
        return self.request(command, struct.pack("<I", index))

    def get_usages(self, which: str = "our") -> list[tuple[int, int]]:
        """Return the board's output (our) or input (their) usage list.

        Entries are (usage, count) runs, exactly as the config tool shows them.
        """
        command = CMD_GET_OUR_USAGES if which == "our" else CMD_GET_THEIR_USAGES
        total = self.get_config()[
            "our_usage_count" if which == "our" else "their_usage_count"]
        out: list[tuple[int, int]] = []
        index = 0
        while index < total and index < 4096:
            data = self._indexed(command, index)
            for i in range(3):
                usage, count = struct.unpack_from("<II", data, i * 8)
                if usage == 0 and count == 0:
                    continue
                out.append((usage, count))
            index += 3
        return out

    # -- monitor -----------------------------------------------------------
    def set_monitor(self, enabled: bool) -> None:
        self.send(CMD_SET_MONITOR_ENABLED, bytes([1 if enabled else 0]))

    def _parse_report(self, raw) -> list[MonitorEvent]:
        data = bytes(raw)
        # hidapi keeps the leading report id on Windows
        if len(data) == 64 and data[0] == REPORT_ID_MONITOR:
            data = data[1:]
        now = time.time()
        out: list[MonitorEvent] = []
        for i in range(0, len(data) - 8, 9):
            usage, value, hub_port = struct.unpack_from("<IiB", data, i)
            if usage == 0:
                continue
            out.append(MonitorEvent(now, usage, value, hub_port))
        return out

    def read_events(self, timeout_ms: int = 100) -> list[MonitorEvent]:
        """Return every event the board has streamed.

        Waits up to timeout_ms for the first one, then drains whatever else is
        already queued.  Never blocks longer than timeout_ms.
        """
        if self._monitor is None:
            raise RuntimeError("monitor not opened")
        out: list[MonitorEvent] = []
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            raw = self._monitor.read(64, 0)
            if raw:
                out.extend(self._parse_report(raw))
                continue
            if out or time.monotonic() >= deadline:
                return out
            time.sleep(0.001)

    # -- injection (patched firmware) --------------------------------------
    def inject_mouse(self, dx: int = 0, dy: int = 0, wheel: int = 0,
                     buttons: int | None = None) -> None:
        """Emit one relative mouse report from the board.

        buttons: bitmask (bit0=left, bit1=right, bit2=middle, bit3/4=side).
                 None keeps whatever is currently held.
        """
        dx = max(-32768, min(32767, int(dx)))
        dy = max(-32768, min(32767, int(dy)))
        wheel = max(-128, min(127, int(wheel)))
        mask = 0xFF if buttons is None else (buttons & 0x1F)
        flags = 0x01 if buttons is not None else 0x00
        self.send(CMD_INJECT, struct.pack("<hhhBB", dx, dy, wheel, mask, flags))

    def inject_key(self, keys: list[tuple[int, bool]]) -> None:
        """Press/release keyboard usages. keys = [(usage, down), ...]

        `usage` is the keyboard usage WITHOUT the page, e.g. 0x04 for 'A'
        (the firmware ORs in 0x00070000).

        The firmware reads each item as **3 bytes** - uint16 usage then uint8
        down - so packing them as 2 bytes silently turns 0x04 into 0x0104 and
        nothing is ever pressed.  Keep this in step with the INJECT_KB case in
        config.cc.
        """
        items = keys[:8]
        payload = bytes([len(items)]) + b"".join(
            struct.pack("<HB", u, 1 if d else 0) for u, d in items
        )
        self.send(CMD_INJECT_KB, payload)

    def type_text(self, text: str, delay: float = 0.02) -> None:
        """Type ASCII text through the board's emulated keyboard."""
        base = {}
        for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
            base[c] = (0x04 + i, False)
            base[c.upper()] = (0x04 + i, True)
        for i, c in enumerate("1234567890"):
            base[c] = (0x1E + i, False)
        for c in "!@#$%^&*()":
            base[c] = (base["1234567890"["!@#$%^&*()".index(c)]][0], True)
        base[" "] = (0x2C, False)
        base["\n"] = (0x28, False)
        base["\t"] = (0x2B, False)
        for c, u in {"-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30, "\\": 0x31,
                     ";": 0x33, "'": 0x34, "`": 0x35, ",": 0x36, ".": 0x37,
                     "/": 0x38}.items():
            base[c] = (u, False)
        for c, u in {"_": 0x2D, "+": 0x2E, "{": 0x2F, "}": 0x30, "|": 0x31,
                     ":": 0x33, '"': 0x34, "~": 0x35, "<": 0x36, ">": 0x37,
                     "?": 0x38}.items():
            base[c] = (u, True)

        for ch in text:
            usage, shift = base.get(ch, (None, False))
            if usage is None:
                raise ValueError("cannot type %r" % ch)
            mods = [0xE1] if shift else []
            self.inject_key([(m, True) for m in mods] + [(usage, True)])
            time.sleep(delay)
            self.inject_key([(m, False) for m in mods] + [(usage, False)])
            time.sleep(delay)

    # -- pixel-accurate moving ---------------------------------------------
    def calibrate(self, samples: int = 9) -> float:
        """Measure how many screen pixels one mouse count moves the cursor.

        Windows scales relative mouse reports by the pointer-speed setting, so
        "150 counts" is not "150 pixels".  Returns the measured ratio and caches
        it on this instance.
        """
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        def cursor_x() -> int:
            p = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
            return p.x

        sw = ctypes.windll.user32.GetSystemMetrics(0)
        ratios: list[float] = []
        for _ in range(samples):
            ctypes.windll.user32.SetCursorPos(sw // 4, 400)
            time.sleep(0.12)
            x0 = cursor_x()
            self.inject_mouse(dx=200)
            time.sleep(0.12)
            x1 = cursor_x()
            if x1 - x0 > 0:
                ratios.append((x1 - x0) / 200.0)
        if not ratios:
            raise RuntimeError("calibration failed: the board moved nothing")
        ratios.sort()
        self.pixels_per_count = ratios[len(ratios) // 2]
        return self.pixels_per_count

    def inject_mouse_px(self, dx_px: int = 0, dy_px: int = 0,
                        buttons: int | None = None) -> None:
        """Move the cursor by roughly dx_px / dy_px screen pixels."""
        scale = getattr(self, "pixels_per_count", None)
        if scale is None:
            scale = self.calibrate()
        self.inject_mouse(dx=round(dx_px / scale), dy=round(dy_px / scale),
                          buttons=buttons)


def _demo_monitor(seconds: float = 8.0) -> None:
    with Middleman().open() as m:
        print("config version:", m.get_version())
        print("inject support:", m.has_inject())
        print("enabling monitor for %.0fs -- move the mouse now" % seconds)
        m.set_monitor(enabled=True)
        t_end = time.time() + seconds
        n = 0
        while time.time() < t_end:
            for ev in m.read_events(200):
                n += 1
                if n <= 60:
                    print(ev)
        m.set_monitor(False)
        print("total events:", n)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "events":
        _demo_monitor(float(sys.argv[2]) if len(sys.argv) > 2 else 8.0)
    else:
        with Middleman().open(want_monitor=False) as m:
            print("config version:", m.get_version())
            print("inject support:", m.has_inject())
