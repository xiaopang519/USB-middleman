---
name: usb-middleman
description: Build, flash and drive an RP2040 "USB middleman" - a board that sits inline between a mouse/keyboard and the PC, forwards the real device transparently, streams every input event to the PC for lag-free recording, and can emit its own hardware-level HID reports so automation gets a real "hand". Covers the Waveshare RP2040-PiZero + PIO-USB wiring, bootstrapping an ARM toolchain on Windows without Visual Studio, compiling HID Remapper from source, and the injection/monitor wire protocol. Use for any work involving RP2040 HID Remapper, PIO-USB host, RP2040-PiZero, USB pass-through/MITM input devices, hardware mouse injection, or when a freshly compiled RP2040 firmware does not boot, the board vanishes from USB, or injected mouse input never reaches Windows.
---

# USB middleman on RP2040

A board wired inline in the mouse cable. It is a USB **host** on one side (the
real mouse) and a USB **device** on the other (the PC), so it can watch
everything and also speak for itself.

```
real mouse ──► board (PIO-USB host) ──► PC (native USB device)
                     │
                     ├─ monitor stream  : every input event → PC (recording)
                     └─ INJECT command  : board emits its own HID reports
```

Two capabilities, both already proven on this hardware:

| | how | measured |
|---|---|---|
| **看 / watch** | board streams raw input over a vendor HID input report | ~1000 events/s, no hooks, no added latency |
| **手 / hand** | `INJECT` config command → board writes a real HID report | direction correct, pixel error ~10px/150px |

---

## Read this first: the pitfalls that cost hours

Every one of these is a *simple* bug whose symptom points somewhere else.
Full diagnosis commands are in `reference/pitfalls.md`.

| # | Symptom | Actual cause | Fix |
|---|---|---|---|
| 1 | Firmware never boots, board **vanishes from USB entirely** | linker script matches `*.o` but CMake+Ninja emits **`*.obj`** on Windows → all app code lands in RAM, heap drops to ~9 KB, crash before USB init | add `*remapper.dir/src/*.obj(.text*)` to `remapper_single.ld` |
| 2 | Firmware too big / RAM nearly full | `Release` is `-O3`; upstream CI effectively builds ~`-O3` from an older GCC. GCC 13/15 `-O3` is ~26 % larger | build `-DCMAKE_BUILD_TYPE=MinSizeRel` |
| 3 | Board is gone, need the bootloader | — | hold **BOOT**, tap **RUN**, release BOOT → `RPI-RP2`. While firmware runs you can instead send config cmd `RESET_INTO_BOOTSEL` (1) — **no button needed** |
| 4 | Device enumerates, `mouhid`/`kbdhid` bound, but **no mouse/keyboard input reaches Windows** | host is not polling interface 0's EP 0x81 — the HID class stack is wedged | **reboot Windows.** Changing USB port, replugging, and giving the device a new serial all failed |
| 5 | Convinced your firmware is broken | it isn't | **always flash the official prebuilt UF2 and compare** before blaming your build |
| 6 | Firmware sends a correct report but the cursor barely moves / moves wrong | Windows multiplies relative reports by the pointer-speed setting | calibrate pixels-per-count (`Middleman.calibrate()`), or set pointer speed to the middle |
| 7 | hidapi `get_feature_report` → `OSError: read error` | Windows counts the report ID, so ask for `CONFIG_SIZE + 1` | `get_feature_report(REPORT_ID_CONFIG, 33)` |
| 8 | Parsed GET_FEATURE response looks like garbage | the response is **28 bytes of data + crc32**, with **no echoed command byte** | parse as `data[0:28]` + crc |
| 9 | Monitor reader **hangs on the first real event** | with `set_nonblocking(False)`, hidapi's `read(size, 0)` blocks forever | `set_nonblocking(True)`, then drain with `read(64, 0)` |
| 10 | `.bat` launcher garbles or drops characters | `chcp 65001` breaks GBK batch parsing; LF-only batch eats the first char | use a `.lnk` shortcut, not a `.bat` |
| 11 | Hand verifier reports a random **2/4 or 3/4** on a working board | the test measured `after - before` while a high-DPI mouse was **drifting on the desk**, and blamed the board | subtract measured idle drift, inject ±180 counts, and refuse to judge if the cursor is moving on its own |
| 12 | Detection "error" of 32 px, then 943 px, from a pixel-exact detector | target drawn relative to the **window geometry** instead of the client area (+8/+31 px), then `winfo_rootx()` read **before the window was mapped** (returns 0) | borderless fullscreen target, or `winfo_rootx()` after `root.update()` — and print the origin |
| 13 | Typed `www2`, field shows **`哇哇哇`** | the **Chinese IME** intercepts injected keys just like a real keyboard; `ImmAssociateContext` does **not** work cross-process | send a real **Shift** (or Ctrl+Space) before typing — the same key the user presses |
| 14 | Click lands 400 px off, on a wall | `ImageGrab` captures the **screen**; an overlapping window's black area was mistaken for the input box | search only a sensible sub-region, cluster solid rectangles over many rows, and **draw the points on a screenshot before clicking** |
| 15 | `calibrate()` returns 2.58 then 1.57 | a **focused game changes the pointer gain** | never cache a calibration; always close the loop on `GetCursorPos` |
| 16 | Recorded demo has 15 clicks and **zero keystrokes** | only the **mouse** is on the board's single PIO-USB port; the keyboard goes straight to the PC | add a USB hub to record both; treat recordings as mouse-only |
| 17 | Coordinate reader suddenly returns **`None`** | `X0` (digit start) was hardcoded; a shorter map name shifts the digits 36 px left. The fallback fails too: 11-row strip → 8-row glyphs vs a 5-row template library | **anchor on the colon** and step `±6 px`; never segment-and-merge (1 px inter-digit gaps) |
| 18 | "The OCR is wrong" — it wasn't | compared a live read against a screenshot taken **minutes earlier**, after the character walked | pin ONE frame; print glyphs as ASCII art; run a **no-input control** (measured: 19/19 reads, **zero drift**) |
| 19 | Same 300 px walk gives `+16`, then `+3`, then `+1` | **hold right button = run continuously**, so distance depends on hold time, not cursor distance. Releasing on a timer, or "until it stops" (= until it hits a wall), measures the wrong thing | keep direction and duration as separate constants (`DRAG_DIST` vs `DRAG_HOLD`); never calibrate scale from walk distance |
| 20 | Every direction acquires the same **southward** component | aimed from `client_centre`, but the character is drawn at `H * CHAR_CY_RATIO` = **0.385**, i.e. 140 px higher | aim from `(W//2, H*0.385)` — then 16/16 direction signs came out correct. Don't `import lb_input` for the constant; it breaks the board's `GetCursorPos` binding |
| 21 | `calib_grid.json` predicts 82.7 px/cell but also says 48 | `matrix` is leftover **rate** data (cells per px per 140 ms burst) in the same file as the **tile size**; the real code uses the tile size and ignores `matrix` | one file, one unit; name constants after their units; delete the `[0,200,0,0,"S"]` row that recorded no movement |
| 22 | Character "teleports" **21 cells** in one click; nothing is ever accurate | `1` was read as `3`. The matcher takes the top 5 rows (library is 5-row) but the HORIZONTAL box came from the full 8 rows, and a `1` has a 3 px base serif — ink ends up one pixel off, so `3` wins. Every coordinate containing a `1` was off by 20 | tighten the box **again** after cutting the rows; verify against ground truth from the **same instant**. A small constant error is far more dangerous than `None` |
| 23 | Endless oscillation; tuning hold time and click distance changes nothing | the model was assumed, not asked. The mouse **pulls**: 左键一下 = 1 cell, 右键一下 = 2, held = continuous. The step table is **exactly axis aligned**, 8/8 | `step_calib.py` measures the table by clicking; navigation is then just `click(sign(target-current))` — converged in 11 clicks, zero oscillation |

**General lesson:** when a USB device misbehaves, check the *firmware's own view*
before the host's. The board can be made to report its state over the monitor
stream — that is what turned a multi-hour guess-fest into a five-minute bisect
(`scripts/mcu_hid.py` + a temporary `monitor_usage(0xFFFF00xx, value, 0)` call).

---

## Hardware facts

**Waveshare RP2040-PiZero.** Two Type-C ports:

| port | role |
|---|---|
| **upper, labelled `PIO-USB`** | USB **host** — the real mouse/keyboard plugs in here |
| **lower, labelled `USB`** | USB **device** — cable to the PC |

| signal | pin |
|---|---|
| PIO-USB D+ | **GPIO6** |
| PIO-USB D− | **GPIO7** |
| TF card (SPI0) | SCK=18, MOSI=19, MISO=20, CS=21, CD=22 |

**Plug order matters** (documented hardware quirk — the board has unnecessary
pull-ups on the USB lines):

> 1. mouse into the board's **PIO-USB** port
> 2. **then** the board's USB cable into the PC

If the mouse dies: unplug the board, redo the sequence. Board unplugged = mouse
dead, because the mouse is powered from the board's host port.

**Rollback**: hold BOOT → tap RUN → release → `RPI-RP2` → copy a known-good UF2.

---

## The chain, end to end

### 1. Bootstrap the toolchain (Windows, no Visual Studio)

`powershell -File scripts/bootstrap-toolchain.ps1`

Installs into `D:\rp2040-build\`:

* **GCC 13.3.1** (`xpack-arm-none-eabi-gcc`) — pico-sdk 2.1.1 is tested against
  GCC 13; GCC 15 works but changes code size a lot
* **CMake** + **Ninja** (portable zips)
* **prebuilt `pioasm` and `picotool`** from `raspberrypi/pico-sdk-tools`
* clones `jfedor2/hid-remapper` **with submodules** (the release zip has none)

No host compiler needed: pico-sdk normally compiles `pioasm`/`picotool` with
the *host* compiler, which does not exist here. It is disabled by handing the
SDK **imported targets** via a CMake prelude (`-DCMAKE_PROJECT_INCLUDE=`).
See `reference/build-chain.md`.

### 2. Apply the firmware patch

`scripts/apply-patch.ps1` applies `patches/hr-patches.diff` to the checkout.
It contains:

* the **linker-script fix** (pitfall 1) — without it the firmware does not boot
* `ConfigCommand::INJECT` (26) and `INJECT_KB` (27) plus the injection plumbing
* `HID_ITF_PROTOCOL_NONE` on interface 0, so the host can never trigger the
  boot-protocol descriptor swap

### 3. Build

`powershell -File scripts/build.ps1` → `build-os\remapper.uf2` (~348 KB,
the official release is 348,160 bytes).

Sanity check before flashing — **do this every time**:

```
arm-none-eabi-size -A build-os/remapper.elf   # .rodata must NOT be 0   (pitfall 1)
arm-none-eabi-nm --defined-only build-os/remapper.elf | findstr " main$"
                                              # main must be 0x1000xxxx, not 0x2000xxxx
```

### 4. Flash

`python scripts/mcu_flash.py <file.uf2>` — asks the running firmware to reboot
into the bootloader, waits for `RPI-RP2`, copies, waits for the board to come
back. **No button press** while the firmware is alive.

### 5. Drive it from the PC

```python
import sys; sys.path.insert(0, "<skill>/scripts")
from mcu_hid import Middleman

with Middleman().open() as m:        # monitor=True by default
    m.get_version()                  # 18
    m.has_inject()                   # True on patched firmware

    m.set_monitor(True)              # start the recording stream
    for ev in m.read_events(200):    # ev.usage, ev.value, ev.hub_port
        print(ev)

    m.calibrate()                    # pixels per mouse count
    m.inject_mouse(dx=100)           # the hand
    m.inject_mouse_px(dx=-40, dy=15) # in screen pixels
    m.inject_key([(0x0004, True)])   # 'A' down
```

`scripts/mcu_record.py --seconds 60 --out play.jsonl` records a session.
`scripts/mcu_verify_hand.py` proves the hand works and pops a message box.

---

## Automation: the closed loop

Infrastructure alone does not automate anything. The shape of the real work is
always the same, for a game and for an office app alike:

```
screenshot ─► detect target ─► compute error ─► move/click ─► verify
   (eyes)                        (brain)          (hand)
```

`scripts/loop_demo.py` proves that loop end to end against a target **it creates
itself** (a red disc at a random spot), so it touches no game and no office app
and needs no approval:

```
vision error (found vs true centre): median 0.0 px, worst 0.0 px
clicks landed on the target: 12/12      (~400 ms/trial, 4-8 controller steps)
```

Only two things change per task: **what you detect** and **what you do**. The
controller, the verification and the safety are reusable — see
`reference/closed-loop.md`.

**The board gives you an unusually good kill switch.** It sits *in front of* the
PC, so it sees the human's input as well as its own: turn the monitor on during
automation and stop the moment a real input event arrives —
*if the human touches the mouse, stop.*

And a reminder that matters: reach for the board only when the target has no API
and software automation is blocked or fingerprinted. For a web backend,
Playwright or the API is 10× faster and far more reliable.

---

## Reference

| file | contents |
|---|---|
| `reference/pitfalls.md` | every trap: symptom → how to confirm → root cause → fix |
| `reference/build-chain.md` | toolchain layout, the CMake prelude, build/flash recipes |
| `reference/protocol.md` | config feature-report framing, INJECT payloads, monitor report layout |
| `reference/closed-loop.md` | eyes→brain→hand: controller, detectors per domain, latency budget, safety |
| `patches/hr-patches.diff` | the firmware patch against upstream master |
| `scripts/` | bootstrap, build, patch, flash, driver, recorder, verifier, loop demo |

## Non-negotiables

* **Never run a real game test without explicit human approval.**
* **Always keep a known-good UF2** (`remapper_waveshare_rp2040_pizero.uf2` from
  the official release) next to whatever you flash.
* **Verify against the official firmware** before concluding your build is bad.
