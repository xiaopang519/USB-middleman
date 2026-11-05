# Pitfalls — symptom → confirm → cause → fix

Every entry here cost real time. They are all *simple* bugs wearing a
convincing disguise. Check this list **before** starting a debugging session.

---

## 1. Firmware never boots; the board vanishes from USB completely

**Symptom.** After flashing, `VID_CAFE&PID_BAF2` is absent from
`Get-PnpDevice -PresentOnly`, no `RPI-RP2` either. Windows shows no failed
device at all — the board simply is not on the bus. The mouse behind it is dead
too, because the board powers it.

**Confirm.** Look at the ELF, not the board:

```powershell
arm-none-eabi-size -A build-os\remapper.elf          # .rodata size?
arm-none-eabi-nm --defined-only build-os\remapper.elf | findstr " main$"
```

* `.rodata` = **0** → the app's own code is *not* in flash. **Broken.**
* `main` at **0x2000xxxx** → running from RAM. **Broken** (must be `0x1000xxxx`).

Also worth checking while you are there:

```powershell
arm-none-eabi-size -A build-os\remapper.elf | findstr ".heap"
```

`.heap` under ~10 KB means RAM is essentially full and the firmware will die in
`set_mapping_from_config()` before USB ever initialises. Healthy is ~116 KB.

**Cause.** `firmware/remapper_single.ld` places hid-remapper's own sources in
flash with:

```
CMakeFiles/remapper.dir/src/*.o(.text*)
```

Upstream CI builds with **Makefiles on Linux**, where objects really are
`*.o`. On Windows, CMake + **Ninja** names them `*.obj`
(`build CMakeFiles/remapper.dir/src/main.cc.obj: ...` in `build.ninja`), so the
pattern matches **nothing**. Every application object silently falls through to
the RAM `.text` output section.

The image still links, so nothing complains. The firmware just has ~9 KB of heap
instead of ~116 KB, and dies before enumerating.

**Fix.** Match both extensions, and allow a path prefix so absolute object
paths work:

```
*remapper.dir/src/*.o(.text*)
*remapper.dir/src/*.obj(.text*)
```

**Verify the fix:**

| | broken | fixed |
|---|---|---|
| `.rodata` | 0 | ~62,000 |
| `main` | 0x200003E8 (RAM) | 0x10000594 (flash) |
| `.heap` | 9,376 | 116,320 |
| UF2 size | 344,576 | 347,648 (official: 348,160) |

**Bonus trap in the same area.** Build **`MinSizeRel`**, not `Release`. `Release`
is `-O3`; with GCC 13/15 that produced a 442,880-byte UF2 (heap 6,880) versus
the official 348,160. pico-sdk defaults to `Release` when
`CMAKE_BUILD_TYPE` is unset — that default is not what you want here.

---

## 2. Board is dead and you need the bootloader

**Recovery.** Hold **BOOT**, tap **RUN**, release **BOOT** → `RPI-RP2` appears →
drag in a known-good UF2.

**While the firmware is alive you never need the buttons.** Send config command
`RESET_INTO_BOOTSEL` (1) over the config feature report and the board reboots
into the bootloader by itself:

```python
from mcu_hid import Middleman, CMD_RESET_INTO_BOOTSEL
with Middleman().open(want_monitor=False) as m:
    m.send(CMD_RESET_INTO_BOOTSEL)
```

`scripts/mcu_flash.py` wraps the whole thing: bootloader → wait for `RPI-RP2` →
copy → wait for re-enumeration.

**Important.** A UF2 flash only writes the sectors it covers. A *smaller* image
flashed over a larger one leaves the tail of the old firmware in flash — that is
harmless, but the persisted config lives at
`PICO_FLASH_SIZE_BYTES - PERSISTED_CONFIG_SIZE` and **survives reflashing**.
That is usually what you want.

---

## 3. Device enumerates, drivers bound, but no mouse/keyboard input reaches Windows

**Symptom.** All 8 interfaces present and `OK`. `HID-compliant mouse` shows
`Service=mouhid`, `ProblemCode=0`. The real mouse's movements *do* reach the
board (visible on the monitor stream). But the cursor does not move, and an
injected report does nothing either.

**Confirm — split the problem in half.** Use a low-level hook, which sees every
mouse event including injected ones, and first **validate the hook itself**:

```powershell
python scripts\mcu_hook_validate.py     # SendInput must produce 1 event
python scripts\mcu_hook_test.py         # inject via the board; 0 events?
```

Then check the firmware's own view of the link. Temporarily add to
`apply_injected_absolute()`:

```c
inject_debug(0xFFFF0017, tud_hid_n_ready(0) ? 1 : 0);
inject_debug(0xFFFF000F, tud_suspended() ? 1 : 0);
```

and to `do_send_report()`:

```c
bool queued = tud_hid_n_report(interface, report_with_id[0], report_with_id + 1, len - 1);
inject_debug(0xFFFF0014, queued ? 1 : 0);
```

Observed on this machine:

```
tud_hid_n_report OK count    = 1     <- the report WAS accepted
tud_hid_n_report FAIL count  = 0
tud_hid_n_ready(0)           = 0     <- endpoint stuck busy, never polled again
```

**Cause.** The host never issues an IN token for **interface 0's EP 0x81**, so
the first queued transfer never completes and only one report can ever be sent.
Interface 1's EP 0x83 works fine (the monitor stream arrives), so the board's
USB device is healthy — the **Windows HID class stack is wedged**.

**The decisive test — do this early.** Flash the **official prebuilt UF2**. If it
fails identically, your firmware is innocent:

```powershell
python scripts\mcu_flash.py remapper_waveshare_rp2040_pizero.uf2
```

**Fix: reboot Windows.** Hardware and registry state that a reboot restores.

**What was tried and did NOT help** (so you do not repeat it):

* a different USB port
* replugging in the documented order (mouse into PIO-USB first, then the board)
* giving the device a brand-new USB serial so Windows treats it as a new device
* changing `bInterfaceProtocol` from BOOT KEYBOARD to NONE, so the host cannot
  trigger hid-remapper's descriptor swap
* `Disable-PnpDevice` / `Enable-PnpDevice` (needs admin, fails otherwise)

**Lesson.** A wedged host looks exactly like broken firmware. Check the official
build first; it is one flash and it removes an entire class of wrong theories.

---

## 4. Injected movement arrives, but the distance is wrong

**Symptom.** You ask for +150 counts and the cursor moves +234 px. Ratios are
inconsistent between samples (1.56, 2.5, 2.51, 2.5, 2.3).

**Cause.** Windows multiplies relative mouse reports by the **pointer-speed**
setting — "150 counts" is not "150 pixels". This is *not* pointer acceleration;
check which one you are looking at:

```python
class MOUSEKEYS(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("iMnt", ctypes.c_int),
                ("iMnt1", ctypes.c_int), ("iMnt2", ctypes.c_int)]
m = MOUSEKEYS(); m.cbSize = ctypes.sizeof(m)
ctypes.windll.user32.SystemParametersInfoW(0x0003, 0, ctypes.byref(m), 0)
print(m.iMnt, m.iMnt1, m.iMnt2)   # threshold1, threshold2, acceleration
```

On this machine acceleration was **off** (`iMnt2 = 0`) yet the gain was still
2.58 px/count — that is the speed slider, a constant multiplier.

**Fix.** Calibrate and work in pixels:

```python
scale = m.calibrate()             # e.g. 2.580 px per count
m.inject_mouse_px(dx=-120, dy=-90)
```

For best accuracy set the pointer-speed slider to the middle (6/11), which is
1:1. `calibrate()` returns the median of several samples because the transform
rounds per report.

**Also:** `inject_mouse` clamps to `int16` for dx/dy and `int8` for wheel. Move
in several reports if you need to cross more than ±32767 counts.

---

## 5. hidapi feature-report calls fail or return nonsense

Three separate traps in the same API:

**a) `get_feature_report(id, 32)` → `OSError: read error`.**
Windows counts the report ID in the length, so the device is asked for 31 bytes
and `handle_get_report1` returns 0 (which the stack turns into a STALL).

```python
# wrong
raw = dev.get_feature_report(REPORT_ID_CONFIG, 32)
# right
raw = dev.get_feature_report(REPORT_ID_CONFIG, 33)   # CONFIG_SIZE + 1
```

**b) The response has no command echo.** `get_feature_t` is
`uint8_t data[28]; uint32_t crc32;` — there is no `version` or `command` byte.
Parsing it as `{version, command, ...}` yields garbage that looks plausible
(reading `config version: 255`).

```python
def parse_get_report(data: bytes) -> bytes:
    if _crc32(data[:28]) != struct.unpack("<I", data[28:32])[0]:
        raise ValueError("bad crc in response")
    return bytes(data[:28])
```

`get_config()` then reads `data[0]` for the version — which is 18.

**c) Unknown commands return all-`0xFF`.** `handle_get_report1` answers an
unrecognised command with `memset(config_buffer, 0xFF, ...)`. So "is my patched
firmware installed?" cannot be answered by looking at a command byte. Make the
patch answer with a marker and test for it:

```c
case ConfigCommand::INJECT:
    config_buffer->data[0] = 'I'; config_buffer->data[1] = 'N';
    config_buffer->data[2] = 'J'; config_buffer->data[3] = '1';
    break;
```

**d) CRC is plain zlib crc32** (`zlib.crc32`) over the first `CONFIG_SIZE - 4`
bytes, stored little-endian in the last 4.

---

## 6. The monitor reader hangs on the first real event

**Symptom.** `mcu_record.py --seconds 90` records exactly 0 events even though
the mouse is moving. A self-test that synthesises events works fine.

**Cause.** `hid.device.set_nonblocking(False)` was called, then the drain loop
did `read(64, 0)`. In blocking mode hidapi's zero-timeout read does not return —
the first genuine event wedges the reader forever, so nothing is ever written.

**Fix.**

```python
self._monitor.set_nonblocking(True)

def read_events(self, timeout_ms=100):
    out, deadline = [], time.monotonic() + timeout_ms / 1000.0
    while True:
        raw = self._monitor.read(64, 0)
        if raw:
            out.extend(self._parse_report(raw))
            continue
        if out or time.monotonic() >= deadline:
            return out
        time.sleep(0.001)
```

**Diagnostic note.** When this happens the firmware looks perfectly healthy and
the *symptom* (0 events) suggests the board is not sending anything. A
synthetic-event self-test (`scripts/mcu_selftest.py`) separates "uplink broken"
from "no input happened".

---

## 7. Instrumentation that destroys its own signal

Two ways this bit:

**a) A change-detecting debug helper + a per-frame counter = flood.** Emitting
`send_report()`'s call count on every call produced 28,007 events in 4 s,
filling the monitor's 7-item report and starving every other marker. If the
monitor looks empty, suspect your own instrumentation first.

**b) Debug markers land in real recordings.** `monitor_usage(0xFFFF00xx, ...)`
events go into the same stream `mcu_record.py` writes to JSONL. **Strip all
instrumentation before producing a build you intend to record with.**

```c
void inject_debug(uint32_t usage, int32_t value) {
    if (!monitor_enabled) return;
    static std::unordered_map<uint32_t, int32_t> last;
    auto it = last.find(usage);
    if ((it != last.end()) && (it->second == value)) return;   // only on change
    last[usage] = value;
    monitor_usage(usage, value, 0);
}
```

---

## 8. Shell and launcher traps

**`Select-Object -Last N` buffers output.** Piping a long-running `cmake`/`curl`
through it shows nothing until it finishes, and made a fast download look like
it was running at 80 KB/s. Redirect to a file instead:

```powershell
& cmake.exe --build build --target remapper *> build.log
Get-Content build.log -Tail 20
```

**`$var` inside a `-D` argument can arrive mangled.** `-DCMAKE_BUILD_TYPE=$bt`
produced the literal `$bt` in `rules.ninja` (`rule ASM_COMPILER__..._$bt`).
Put the build in a `.ps1` file and build the argument as
`"-DCMAKE_BUILD_TYPE=" + $BuildType`.

**`.bat` launchers mangle Chinese paths and text.** `chcp 65001` breaks decoding
of the rest of a GBK batch file, and an LF-only batch file loses the first
character of each line. Use a `.lnk` shortcut pointing straight at
`python.exe` with `Arguments` and `WorkingDirectory`, or `pythonw.exe` when you
want a message box instead of a console.

**`pwsh` is not always on PATH.** Fall back to invoking the script directly.

**Slow GitHub release downloads.** `github.com` can be unreachable while
`objects.githubusercontent.com` is fine. Mirrors (`ghfast.top`,
`ghproxy.net`, `gh-proxy.com`) worked at ~250 KB/s; a plain `curl` to the
release URL varied between 80 KB/s and 24 MB/s. Always download without piping
through PowerShell.

---

## 9. Building on Windows without Visual Studio

pico-sdk wants to build `pioasm` and `picotool` with the **host** compiler,
which does not exist here (`Findpioasm.cmake` unconditionally sets up an
`ExternalProject`). Both skip the source build if a CMake target with the right
name already exists:

```cmake
# picoasm_prelude.cmake, passed as -DCMAKE_PROJECT_INCLUDE=...
if (NOT TARGET pioasm)
    add_executable(pioasm IMPORTED GLOBAL)
    set_property(TARGET pioasm PROPERTY IMPORTED_LOCATION
        "D:/rp2040-build/pico-tools/pioasm/pioasm.exe")
endif()
set(pioasm_FOUND 1)                # also skips pico_init_pioasm's search

if (NOT TARGET picotool)
    add_executable(picotool IMPORTED GLOBAL)
    set_property(TARGET picotool PROPERTY IMPORTED_LOCATION
        "D:/rp2040-build/pico-tools/picotool/picotool.exe")
endif()
set(picotool_FOUND 1)
```

Prebuilt binaries come from `raspberrypi/pico-sdk-tools`
(`pico-sdk-tools-*-x64-win.zip` is ~0.8 MB,
`picotool-*-x64-win.zip` ~1.7 MB). The 2.3.1 tools work with pico-sdk 2.1.1 —
`usb_rx.pio.h` generated by pioasm 2.3.1 is **byte-identical** to the copy
committed in `Pico-PIO-USB/src/`, and the boot2 block in the UF2 matches the
official release exactly.

**Also:** the released firmware zip contains **no submodules**. Clone
`jfedor2/hid-remapper` with `--recurse-submodules --shallow-submodules`, or the
build fails on missing `pico-sdk`, `tinyusb`, `Pico-PIO-USB`, `usb_midi_host`.

---

## 10. Trusting a device's own report of success

`do_send_report()` in upstream `main.cc` **always returns true**:

```c
bool do_send_report(uint8_t interface, const uint8_t* report_with_id, uint8_t len) {
    if (tud_suspended() && ...) {
        tud_remote_wakeup();
    } else {
        tud_hid_n_report(interface, report_with_id[0], report_with_id + 1, len - 1);
    }
    return true;  // XXX?
}
```

`send_report()` increments `reports_sent` unconditionally. So
"`reports_sent` went up" means *nothing* about delivery. Check
`tud_hid_n_report`'s real return value and `tud_hid_n_ready()`.

Likewise `Get-PnpDevice` reporting `Status OK` with `ProblemCode=0` and
`Service=mouhid` means the driver is bound — **not** that transfers are
flowing.

---

## 11. The test lied: a drifting mouse scored a working board as broken

**Symptom.** The hand verifier reports `2/4` or `3/4` on a board that demonstrably
works. Re-running gives a different score each time.

**Confirm.** Print the *absolute* cursor positions, not just the deltas:

```
要求 (+60,  +0)  before=(973, 540) after=(1341, 553) got=(368, 13)  WRONG
要求 (  +0, +50)  before=(1136, 511) after=(1185, 592) got=(49, 81)  WRONG
```

The test does `SetCursorPos(960, 540)` and then sleeps — yet `before` reads back
`(973, 540)`, `(1136, 511)`, `(947, 548)`. **The cursor is moving on its own.**

**Cause.** A high-DPI mouse resting on a desk **drifts**. The verifier measured
`after - before` and attributed all of it to the injected report, so a few tens
of pixels of drift flipped the sign of the judgement. The board was sending
exactly what it was told to.

**Fix — make the test robust, not the firmware:**

1. **Tell the user to let go of the mouse**, and detect it: measure the cursor's
   drift over the same interval and refuse to judge if it exceeds a threshold.
2. **Subtract the measured drift** from the observed movement.
3. **Inject a large movement** (±180 counts ≈ ±465 px here) so leftover noise
   cannot flip the sign, and require at least ~25 % of the expected distance.
4. Only judge the **axis you actually asked to move**.

With that, three consecutive runs gave:

```
要求 (+180,  +0)  实际 ( +463,   +0)  OK
要求 (  +0,+180)  实际 (   +0, +463)  OK
要求 (-180,-180)  实际 ( -469, -469)  OK
要求 (+180,+180)  实际 ( +469, +469)  OK
```

**Lesson.** Before believing a measurement, check that the *baseline* is stable.
A verifier that reports a random score is not a weak signal — it is noise
dressed as a result, and it cost an hour of chasing a firmware bug that did not
exist.

---

## 12. Coordinates that are quietly wrong: window frame and unmapped widgets

The closed-loop demo looked like the *vision* was inaccurate. It was not — the
detector was pixel-exact and the coordinates were wrong, twice, in two different
ways.

**a) A window's client area is not at the window's origin.**

`root.geometry("1200x800+360+140")` puts the **frame** at (360, 140). The canvas
inside it starts roughly **+8 px across and +31 px down** (title bar + border).
Drawing the target relative to the geometry origin puts it ~32 px away from where
the maths believes it is:

```
vision error (found disc vs true centre): median 32.0 px, worst 34.3 px
```

Measured against a disc that is a perfect circle. That is not a detection error.

*Fix, best:* use a **borderless fullscreen** window — `overrideredirect(True)` +
`geometry("%dx%d+0+0")` — so client origin == screen origin.
*Fix, normal windows:* ask Tk, don't assume: `canvas.winfo_rootx()/rooty()`.

**b) An unmapped widget reports 0.**

`winfo_rootx()` returns **0** until the window is actually mapped. Computed too
early, it turned the origin into `(0, 0)` and the error exploded:

```
target window client area: x=0 y=0  (geometry origin was 360,140 -> -360,-140)
vision error (found disc vs true centre): median 943.5 px, worst 1088.5 px
clicks landed on the target: 0/12
```

*Fix:* call `root.update()` **first**, then read the geometry, and **print the
origin** so a wrong value is visible instead of silently poisoning the maths.

After both fixes:

```
target window: 1920x1080, client origin (0,0)
vision error (found vs true centre): median 0.0 px, worst 0.0 px
clicks landed on the target: 12/12
```

**Lesson.** When a measurement looks imprecise, suspect the **coordinates**
before the algorithm. A detector that is exactly right and a coordinate frame
that is 32 px off is indistinguishable from a detector that is 32 px off — unless
you print the raw numbers.

---

## 13. The Chinese IME eats injected ASCII — the most expensive trap in a zh-CN locale

**Symptom.** Asked to type `www2` into a login box. The box showed **`哇哇哇`**.
Retried; it showed **`啦啦啦`**. The password (digits) went in fine.

**Cause.** Injection goes through the ordinary input path, so the **IME
intercepts it exactly like a real keyboard**. `wawawa` is valid pinyin, the IME
composed it, and the candidate became 哇哇哇. The human types the same string
successfully because their IME is in English mode.

**Every ASCII credential in a Chinese locale hits this.** It is not a firmware
bug and not a coordinate bug — the letters really were delivered.

**What does NOT work:**

```python
imm32.ImmAssociateContext(hwnd, None)   # cross-process: silently does nothing
imm32.ImmGetContext(hwnd)               # returns 0 for another process's window
```

Both look plausible and both no-op. The code even reports success.

**What works — press the key the user would press:**

```python
def ime_toggle(m, kind="shift"):
    if kind == "shift":
        m.inject_key([(0xE1, True)]); time.sleep(0.06)
        m.inject_key([(0xE1, False)])
    elif kind == "ctrlspace":
        m.inject_key([(0xE0, True), (0x2C, True)]); time.sleep(0.06)
        m.inject_key([(0x2C, False), (0xE0, False)])
    time.sleep(0.25)
```

`0xE1` = Left Shift, `0xE0` = Left Ctrl, `0x2C` = Space. Run it once before
typing and `www2` lands as `www2`. Digits and symbols are usually unaffected —
only letters that form pinyin.

**Also important:** `inject_key`'s wire format is **3 bytes per item**
(`uint16 usage` + `uint8 down`). Packing 2 bytes silently turns `0x04` ('A') into
`0x0104` and *nothing is ever pressed* — the hook simply sees no keys and it looks
like keyboard injection is broken. Keep python and `config.cc` in step.

---

## 14. An overlapping window poisons on-screen detection

**Symptom.** The input-box detector put a click at **x=1091** when the boxes were
at **x=682** — a 400 px miss that landed on a wall.

**Cause.** `ImageGrab` captures the **screen**, not the window. Another window
overlapping the target (here, a chat panel on the right, pure black background)
supplies its own large dark areas, and "find the longest dark run" happily picks
them. The detector was correct in isolation and wrong in situ.

**Fix.** Do not search the whole window rect:

* search only a **sensible sub-region** (a login form lives near the centre —
  `x in [0.30w, 0.62w]`);
* and prefer **structural** evidence over a single long run. Real input boxes are
  *solid* black rectangles, so require a run whose `(x0, x1)` stays within a few
  pixels over **many consecutive rows**. Stone textures and photographic
  backgrounds never do that.

```python
if 70 <= (x1 - x) <= 130:          # plausible box width
    runs.append((y, x, x1))
# then cluster runs sharing (x0, x1) +-6 px across consecutive rows,
# and keep only clusters spanning >= 12 rows
```

**Verify before acting.** Draw the computed points on a fresh screenshot and look
at it before clicking anything:

```python
d.line([(x-16, y), (x+16, y)], fill=(255,0,0), width=3)
d.line([(x, y-12), (x, y+12)], fill=(255,0,0), width=3)
```

One image read is far cheaper than clicking the wrong thing. This is also how the
`www2` fix was confirmed.

---

## 15. A focused game changes the pointer gain

**Symptom.** `calibrate()` returned **2.58, 1.89, 1.88, 2.44, 1.57** px/count
across consecutive runs — while the launcher window had focus.

**Cause.** Not acceleration (`SPI_GETMOUSE` reported it off). Something about the
game being foreground changes how the system scales relative reports — possibly
the game setting the system pointer speed, or clipping the cursor.

**Fix.** Never trust a cached calibration, and **always close the loop**:

```python
for _ in range(8):
    cx, cy = cursor()
    dx, dy = x - cx, y - cy
    if (dx*dx + dy*dy) ** 0.5 <= 3:
        break
    m.inject_mouse(dx=round(dx / scale), dy=round(dy / scale))
    time.sleep(0.02)
```

A wrong scale only costs extra iterations; it does not cost accuracy. Open-loop
counting with a stale scale does.

---

## 16. Only the mouse is on the board, so keystrokes are not recorded

**Symptom.** A recorded login demonstration contained **15 clicks and zero
keyboard events**, even though the user typed a username and password.

**Cause.** The board has a single PIO-USB host port and the **mouse** occupies it.
The keyboard is still plugged straight into the PC, so the board never sees it.
The recording is honest — it just has no keyboard in it.

**Fix.** Put a **USB hub** on the board's PIO-USB port and hang both devices off
it; then the monitor stream carries the full picture. Until then, treat any
recording as mouse-only, and get credentials from a config file rather than from
a recorded typing session.

**Related.** Because the camera-free recording cannot see typing, do not rely on
"record the user's demo" to capture password entry — and remember that text typed
while the monitor is on lands in `events.jsonl` in cleartext.

---

## 17. Pinning the digit position works until the map name changes length

**Symptom.** A working coordinate reader suddenly returns **`None`** for every
read. Nothing else changed.

**Cause.** `ocr_cell.json` had `X0: 98`, the x where the digits begin.

```
沃玛寺庙 一层 385:332     digits begin ~98     X0=98 correct
盟重省 234:378            digits begin ~62     X0=98 lands on the final '8'
```

The map name went from six characters to three, the digit run shifted **36 px
left**, and the fixed box now covers one glyph plus eight empties. The code even
documents the hazard and then hardcodes the value anyway:

```python
# 地图名长度会变: "比奇省"(3字) / "沃玛寺庙"(4字) ...
# 数字起点随之右移, 固定 X0 会切错位 -> 读出错坐标。
# 实测: 数字起点就是固定 X0 ... 所以这里只用固定值。
```

**The fallback also fails, for a completely different reason.** `read_coord`
falls back to segmenting the whole strip and looking for `':'` — but that strip
is 11 rows tall, so the glyphs come out **8 rows high**, while the template
library holds **5-row** glyphs, and `_match_glyph` rejects anything whose size
differs by more than 1:

```python
if abs(ww - bm.width) > 1 or abs(hh - bm.height) > 1:
    continue          # every template rejected, including the ':'
```

So one path has the right glyph height and the wrong x; the other has the right
idea and the wrong glyph height. Both return `None`, and the reader looks simply
"broken".

**Fix — anchor on the colon, not on the start.** The colon is the only glyph
1–2 px wide, so it is easy to find, and the font is a fixed **6 px pitch**, so
every digit sits on `colon_centre ± 6k`:

```python
colon = narrowest column group that has a neighbour within one pitch
for k in 1..3:  read cell at colon_centre - 6k   -> X digits (right to left)
for k in 1..3:  read cell at colon_centre + 6k   -> Y digits (left to right)
stop when a cell is empty
```

The map-name length stops mattering entirely. Measured on a frame whose truth was
`盟重省 234:378`: **`(234, 378)`, 3/3 identical, most glyphs matching at distance
0.0**.

**Do not segment and merge.** The font's inter-digit gap is exactly 1 px, so any
merge gap large enough to keep a glyph whole also fuses neighbours — `234` came
out as a single 17 px blob. Anchor instead.

---

## 18. The coordinate reader is not the flaky part — check it before blaming it

**Symptom.** The reader reports `355:233`; a screenshot clearly shows `385:332`.
Conclusion drawn: the reader is wrong.

**Cause.** The screenshot was taken **minutes earlier**, and the character had
walked in between. Both numbers were correct, for different moments.

**This was the third instance of the same mistake in one session** — comparing
two things measured at different times:

| # | The false conclusion | What was actually true |
|---|---|---|
| 1 | the board's mouse injection is broken | the physical mouse was drifting on the desk |
| 2 | the input-box detector is wrong | the window origin is not the canvas origin |
| 3 | the coordinate OCR is wrong | the screenshot was from a different moment |

**Fix — measurement hygiene, in order:**

1. **One frame.** Grab once, pin it (`vc.grab_client = lambda *a, **k: (arr, None)`),
   then read *and* display from that same pinned frame.
2. **Show the truth as text.** Printing the glyph as ASCII art makes the ground
   truth readable without a screenshot at all:
   ```python
   print("\n".join("".join("#" if bm.getpixel((x,y))>=128 else "." for x in range(bm.width))
                   for y in range(bm.height)))
   ```
3. **Control experiment.** Read the coordinate for 20 s with **no input at all**.
   Measured result: **19/19 successful reads, drift `(+0, +0)`** — so the reader
   is stable and any change during a movement test is real movement.
4. **Stimulus/response beats human ground truth.** Make the character move in a
   known direction and check the reading responds correctly; that needs no human
   to read anything (see #19).

---

## 19. "Hold right button" means RUN CONTINUOUSLY — so a click does not go where you clicked

**Symptom.** A calibration sweep produced wild, irreproducible distances: the
same 300 px "walk east" gave `+16`, then `+3`, then `+1` cells. Conclusion drawn:
"east is blocked by a wall".

**Cause.** Two mistakes stacked:

1. The movement in this client is **hold-to-run** (`lb_nav.py`: "按住右键不放时，
   角色持续朝光标方向跑"). While the button is held the character runs; the
   **distance depends on how long you hold**, not on how far the cursor is.
2. The sweep released after a fixed 1.4 s — long enough sometimes, not others —
   and then a "hold until the coordinate stops changing" variant was tried, which
   is worse: **the character only stops when it hits something**, so that measures
   *which wall it reached*.

Measured: a **340 px** click moves only **2–4 cells**, where `px_per_cell = 48`
would predict ~7. A short click does **not** walk to the clicked tile.

**Consequences.**

* Distance per step is `hold_seconds × speed`. That is why the code carries
  `DRAG_DIST` (direction) and `DRAG_HOLD` (distance) as separate constants — keep
  them separate.
* **You cannot calibrate a screen↔grid scale from walk distance** unless the hold
  is controlled. Use a method that does not involve walking at all (two-frame
  cross-correlation, as `px_per_cell_x/y` originally was), or fix the hold and
  calibrate speed deliberately.

---

## 20. Aim from where the character is drawn, not from the client centre

**Symptom.** Every direction, including pure east and pure west, came back with
the same **southward** world component — which looks exactly like a bad
calibration matrix.

**Cause.** The tools aimed at `client_centre + offset`. But the character is drawn
at `(W//2, H * CHAR_CY_RATIO)` with **`CHAR_CY_RATIO = 0.385`** — well *above*
centre. So every aim point sat 140 px below the character, adding a constant
southward component to all eight directions.

Aiming from `(W//2, H*0.385)` instead produced clean data immediately: **16/16
direction signs correct**, with `E`/`W` varying only in X and `N`/`S` only in Y.

**Two notes for whoever repeats this:**

* `CHAR_CY_RATIO = 0.385` is correct — verified independently against a
  screenshot where the character is visibly at about `(610, 385)` in a 997-tall
  client. A least-squares fit of noisy walk data "recovered" 585 instead, and was
  simply wrong. **Prefer the direct observation over the fitted parameter.**
* Do **not** `import lb_input` just to read that constant. It installs
  `argtypes` on `GetCursorPos`, which then rejects the board library's own POINT
  struct: `expected LP_POINT instance instead of pointer to POINT`. Copy the
  constant.

---

## 21. Two constants with the same name and different units, in one file

`calib_grid.json` contains:

```json
"matrix": [[0.012086, 0], [0, 0.009578]]     -> implies 82.7 px per cell
"px_per_cell": 48.0, "px_per_cell_x": 48.0, "px_per_cell_y": 32.0
                                             -> implies 48 px per cell
```

**72 % apart, and they mean different things.** `matrix` is the leftover output of
`_probe_dirmatrix.py`, which measured *"how many cells does a 140 ms burst carry
you per pixel of cursor offset"* — a **rate**. `px_per_cell_x/y` is the **tile
size**, measured by two-frame cross-correlation. The steering code
(`lb_nav.screen_to_grid`) uses the latter and ignores `matrix` entirely, so
`matrix` is dead data that still invites a wrong conclusion.

The same file's direction rows include:

```json
[0, 200, 0, 0, "S"]      <- dX and dY both zero: that calibration never worked
```

**Lesson.** One file, one unit. Name things after their units
(`cells_per_px_140ms` vs `px_per_tile`), and delete the row that recorded no
movement instead of shipping it as calibration.

---

## 22. One off-by-one-pixel bug made every coordinate containing a `1` wrong by 20

**This was the actual cause of "nothing is ever accurate".** Everything
downstream - pathing, targeting, arrival checks - is built on the coordinate
readout, and the readout was silently lying on one digit.

**Symptom.** The character appeared to **teleport 21 cells** on a single click.
Sampling the coordinate at 10 Hz around one click showed the jump was
**instantaneous**, so it could not be walking:

```
idle 1s:   209,573   stable
click NE:  230,570   <- changed within one sample, then stable for 3 s
```

That sent the investigation down a completely wrong path (teleport arrays, NPC
interaction, isometric steering). None of it existed.

**The real evidence** came from grabbing the strip and the reading at the same
moment:

| screen truth | reader said | wrong digit |
|---|---|---|
| `210:570` | `230:570` | `1` -> `3` |
| `211:569` | `233:569` | both `1`s -> `3` |
| `209:571` | `209:573` | `1` -> `3` |
| `234`, `355`, `385`, `209` | correct | **no `1` in them** |

Every coordinate containing a `1` was off by 20 in that position. `210 -> 230`
is exactly the "21 cell teleport".

**Cause - the horizontal bounding box was computed over the full glyph height.**

The template library holds **5-row** glyphs, so the matcher takes the top 5 rows
of the observed glyph. But `_cell_key` had already computed the *horizontal*
box over the **full 8 rows**, and a `1` is wider at the bottom:

```
observed '1' (8 rows)      library '1' (5 rows, no base)
    .#.                        .#
    ##.                        ##
    .#.                        .#
    .#.                        .#
    .#.                        .#
    .#.
    .#.
    ###   <- base serif, 3 px wide
```

Cropping the top five rows out of a **3 px wide** box leaves the ink in the
middle (`.#.`); the template has it on the right (`.#`). Shifted one pixel, the
distance to `3` came out smaller than to `1`, and `1` lost every time.

**Fix - tighten the box again *after* cutting the rows.**

```python
top = bm.crop((0, 0, bm.width, min(top_rows, bm.height)))
a = np.asarray(top, dtype=np.int16)
cols = np.nonzero((a >= 128).any(axis=0))[0]
top = top.crop((int(cols[0]), 0, int(cols[-1]) + 1, top.height))
```

After the fix every glyph in `211:569` matched at **distance 0.0**:
`'1' '1' '2'` and `'5' '6' '9'`, 3/3 runs identical.

**How it was finally caught - and what actually worked.** Not more reasoning:
* grab the strip **and** read it from the **same** frame, then look at the two
  side by side. The truth was visible in the image; the reading was 20 off;
  nothing else needed explaining.
* print the glyph as ASCII art next to the library template. The base serif and
  the missing template rows were obvious in ten seconds.

**The wider lesson.** A reader that is wrong by a *small constant* is far more
dangerous than one that fails. `None` stops you. A plausible-looking `230`
instead of `210` sends you chasing teleports for an hour. When a measurement
"cannot possibly be right" - 21 cells in 0.06 s - **believe the impossibility and
suspect the instrument**, then verify the instrument against ground truth from
the same instant.

---

## 23. The movement model: mouse pulls, one click = one step

Getting this wrong wasted more time than any bug. The model had to be **asked
for**, and then it is trivial:

```
the mouse PULLS the character; the cursor supplies only a DIRECTION
左键点一下 = 1 step, world delta 1
右键点一下 = 1 run step, world delta 2
either button held = continuous stepping
```

**What was assumed instead (all wrong):**

* that the click distance sets the walk distance, so the cursor offset should be
  scaled by the remaining delta - it does not, the step is a fixed size;
* that the hold time sets the walk distance, so shortening the hold would shorten
  the step - it does not; tuning it changed nothing, which is exactly what the
  endless 208 <-> 230 oscillation was;
* that `48 px per cell` converts cursor offsets to cells. It is the isometric
  **tile** size, which is a different quantity.

**The measured step table is exactly axis aligned** - no isometric rotation at
all, so `screen_to_grid`'s assumption was right and the "isometric steering bug"
theory was wrong:

```
N (0,-1)   NE (+1,-1)   E (+1,0)   SE (+1,+1)
S (0,+1)   SW (-1,+1)   W (-1,0)   NW (-1,-1)
```

8/8 directions, magnitude 1.0-1.4 cells per click. Measured by clicking once per
direction and reading the delta - no constants, no assumptions:

```
python step_calib.py --button left --reps 2
```

**Navigation then needs no scale factor at all:** click once in the direction of
`sign(target - current)`, verify, repeat. That converged in **11 clicks with zero
oscillation** where the scaled-and-timed version had oscillated for 90.

**Lesson.** When a control loop cannot be made to converge, stop tuning it and go
ask what the actuator actually does. Two tunable knobs were being adjusted for an
hour; neither of them was connected to anything.

---

## 24. Mir2 obstacles are in the FOREGROUND tile layer, not the flag byte

**Symptom.** A walkability extractor (`map_walk.py`, `flag & 0x03` at tile offset
10, "validated" by "the biggest connected region should be large") produced a
**比奇 map that is 99.9 % walkable** - a blank sheet. Probing then showed that
every map it produced calls the character's real walls walkable, which looks like
"the client does not have collision data at all".

That conclusion is wrong, and it is expensive: it sends you off to build a scanner
from scratch when the data was there the whole time, in a different byte.

**The Mir2 tile is 12 bytes:**

```
0..1  wBkImg    background (0 = void, no ground)
2..3  wMidImg   middle layer
4..5  wFrImg    FOREGROUND  <- buildings, walls, trees: THE OBSTACLES
6..7  door
8..9  animation
10    btArea    <- what the old rule wrongly used as a flag
11    btLight
```

**How to tell in one look.** Render the same map under every candidate rule side
by side and compare with a map you trust (`render_rules.py`):

| rule | 0.map blocked | what it looks like |
|---|---|---|
| `flag10 & 0x03` | 0.1 % | blank sheet |
| **`wFrImg != 0`** | **5.9 %** | **walled town: central diamond of city wall, buildings inside, roads** |
| `wMidImg != 0` | 1.9 % | a few roads only |
| `wBkImg == 0` | 23.6 % | terrain void, buildings missing |

`wFrImg != 0` reproduces the operator's own 比奇 map: the city-wall diamond, the
buildings inside it, the roads, the outlying structures. `flag & 0x03` marks 60x
too little.

**Also worth knowing:** a size that is `52 + w*h*12` identifies the map, and a
700x700 map is `0.map`. The operator's reference image was 1400x1400 - exactly
double - which is what pointed at `0.map` in the first place.

**Two rules, two different questions.** Export both:

```
_obs   wFrImg != 0                    obstacles only        <- collision
_full  wFrImg != 0 or wBkImg == 0     plus off-map void     <- safe for A*
```

`export_walk.py` writes both for every map in the client, in the same BIN format
`nav.py --map` already consumes.

**Do not validate a map with "walls" that came from one stuck spot.** Five probe
labels said no map matched; four of the five were recorded at the *same*
coordinate, where the character could not move in **any** direction. That is a
dynamic blocker - an NPC, another player, or simply being wedged - not terrain.
Terrain walls come one per probe, spread out. Check the spread of your labels
before concluding a map is wrong.

---

## 25. `Select-Object -First N` in PowerShell kills the producer

**Symptom.** A batch export that had been designed to write 112 files wrote 26,
with no error.

**Cause.** `command | Select-Object -First 26` stops the pipeline as soon as it
has 26 items - and terminating the pipeline terminates the upstream process.
Here it killed the exporter partway through, silently.

**Fix.** Never pipe a long-running writer through `Select-Object -First`. Let it
finish, then read the output, or use `-Last`, or redirect to a file first. (The
same trap already bit this project once with `| Select-Object -Last N` buffering
a long-running job's output.)






