# Build chain — from a bare Windows machine to a flashed board

Target: **Waveshare RP2040-PiZero**, firmware **hid-remapper** (jfedor2),
pico-sdk **2.1.1**, no Visual Studio, no MSVC, no PlatformIO, no Arduino.

Everything lands under `D:\rp2040-build\` by default (override with `-Root`).

---

## Layout

```
D:\rp2040-build\
    dl\                       downloaded archives
    tools\
        cmake-4.4.4-windows-x86_64\
        ninja\
        xpack-arm-none-eabi-gcc-13.3.1-1.1\
    pico-tools\
        pioasm\pioasm.exe         from raspberrypi/pico-sdk-tools
        picotool\picotool.exe
    hr-src\                   hid-remapper clone WITH submodules
    picoasm_prelude.cmake     imported-target override (see below)
    env.ps1                   PATH for a build shell
    build.ps1                 one-command build
    build-os\                 -> remapper.uf2
```

---

## 1. Toolchain

| component | version | why |
|---|---|---|
| ARM GCC | **13.3.1** (xpack, win32-x64) | pico-sdk 2.1.1 is tested against GCC 13. GCC 15 works but produces much larger code, which matters because the image is `copy_to_ram` |
| CMake | 4.4.4 portable zip | no installer needed |
| Ninja | 1.12.1 | small and fast |
| pioasm / picotool | from `pico-sdk-tools` 2.3.1 | prebuilt; avoids needing a host compiler |
| Python | any 3.x on PATH | pico-sdk's find_python |

Download URLs follow `https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>`.
Grab them from the API so the tags stay current:

```powershell
Invoke-RestMethod "https://api.github.com/repos/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/latest" `
  -Headers @{'User-Agent'='ps'} |
  ForEach-Object { $_.assets | Where-Object name -like '*win32-x64.zip' } |
  Select-Object name, browser_download_url
```

`scripts/bootstrap-toolchain.ps1` does all of this, plus the clone.

**Download tip:** do not pipe `curl` through PowerShell (`| Select-Object -Last 3`),
it slows things down badly and hides progress. Write straight to a file.

**Mirrors** (if `github.com` is unreachable but `objects.githubusercontent.com`
works): prefix the release URL with `https://ghfast.top/`.

---

## 2. Source

```powershell
git clone --depth 1 --recurse-submodules --shallow-submodules `
    https://github.com/jfedor2/hid-remapper.git hr-src
```

Submodules matter — the release **zip has none**:

| path | repo |
|---|---|
| `firmware/pico-sdk` | raspberrypi/pico-sdk (2.1.1) |
| `firmware/tinyusb` | **jfedor2**/tinyusb (a fork — must be this one) |
| `firmware/Pico-PIO-USB` | **jfedor2**/Pico-PIO-USB |
| `firmware/usb_midi_host` | rppicomidi/usb_midi_host |

Verify which tinyusb the build actually used:

```powershell
Select-String -Path build-os\build.ninja -Pattern "firmware/[a-zA-Z0-9_.\-]*tinyusb" -AllMatches |
  ForEach-Object { $_.Matches } | ForEach-Object { $_.Value } | Sort-Object -Unique
# expect: D:/rp2040-build/hr-src/firmware/tinyusb
```

If it points at `pico-sdk/lib/tinyusb`, the fork override is not taking effect.

---

## 3. The host-compiler problem, and the CMake prelude

`pico-sdk/tools/Findpioasm.cmake` **always** creates an `ExternalProject` that
compiles `pioasm` with the host compiler, unless a target named `pioasm`
already exists:

```cmake
if (NOT TARGET pioasm)
    ...ExternalProject_Add(pioasmBuild...)
```

`Findpicotool.cmake` behaves the same for `picotool` (used to emit the UF2).
Neither has an "use this binary" option.

So we inject imported targets. `CMAKE_PROJECT_INCLUDE` is included at the end of
the top-level `project()` call — exactly the right moment (after `project()`, so
`add_executable` is legal; before `pico_sdk_init()`, so the SDK finds them).

`picoasm_prelude.cmake`:

```cmake
if (NOT TARGET pioasm)
    add_executable(pioasm IMPORTED GLOBAL)
    set_property(TARGET pioasm PROPERTY IMPORTED_LOCATION
        "D:/rp2040-build/pico-tools/pioasm/pioasm.exe")
endif()
set(pioasm_FOUND 1)          # also short-circuits pico_init_pioasm()'s search

if (NOT TARGET picotool)
    add_executable(picotool IMPORTED GLOBAL)
    set_property(TARGET picotool PROPERTY IMPORTED_LOCATION
        "D:/rp2040-build/pico-tools/picotool/picotool.exe")
endif()
set(picotool_FOUND 1)
```

`pico_generate_pio_header()` emits `COMMAND pioasm -o ...`; because a target
named `pioasm` exists, CMake substitutes its imported location at generate time.

**Version mismatch is not a problem here.** pioasm 2.3.1 against pico-sdk 2.1.1
produced a `usb_rx.pio.h` whose instruction arrays are byte-identical to the
copy committed in `Pico-PIO-USB/src/`, and the generated UF2's boot2 block is
byte-identical to the official release's.

---

## 4. Build

```powershell
. D:\rp2040-build\env.ps1
cmake -S "$env:HR_SRC\firmware" -B build-os -G Ninja `
    "-DCMAKE_PROJECT_INCLUDE=D:/rp2040-build/picoasm_prelude.cmake" `
    "-DPICO_BOARD=waveshare_rp2040_pizero" `
    "-DPICO_PLATFORM=rp2040" `
    "-DCMAKE_BUILD_TYPE=MinSizeRel"
cmake --build build-os --target remapper
```

Notes:

* Only the `remapper` target. `remapper_dual_*` needs `srec_cat` (SRecord) and
  is not needed here.
* `PICO_BOARD` selects `firmware/src/boards/waveshare_rp2040_pizero.h`, which
  defines `PICO_DEFAULT_PIO_USB_DP_PIN 6`.
* `MinSizeRel` is deliberate — see pitfall 1.
* `env.ps1` must put ARM GCC, cmake, ninja and a Python on PATH.

### Sanity checks before you flash anything

```powershell
arm-none-eabi-size -A build-os\remapper.elf        # .rodata must be ~62 KB, heap ~116 KB
arm-none-eabi-nm --defined-only build-os\remapper.elf | findstr " main$"
                                                   # main at 0x1000xxxx
picotool info build-os\remapper.uf2                # family ID 'rp2040'
```

Expected healthy numbers (patched, GCC 13.3.1, MinSizeRel):

```
.rodata   ~61,900      app code, executed from flash (XIP)
.text     ~79,900      libraries, copied to RAM
.data     ~30,700
.bss      ~35,200
.heap    ~116,300      the number that matters
UF2       347,648      official release is 348,160
```

`remapper_single.ld` is unusual and worth understanding:

* `copy_to_ram` binary type, so `.text` (libraries) is copied from flash to RAM
* an extra `.rodata` output section in **FLASH** holds `.flashdata*` and
  hid-remapper's own `src/*.o(.text*)`, so the application runs via XIP
* everything else (`*(.rodata*)`) is pulled into `.data` in RAM "to avoid
  accidental flash accesses"
* the stack lives in `SCRATCH_X`/`SCRATCH_Y` (8 KB total), not in the main RAM
  region, so a deep stack can collide with `.heap`
* the RAM overflow assertion is nominal: `__HeapLimit` is pinned to the top of
  RAM, so `ASSERT(__StackLimit >= __HeapLimit)` always passes. **Watch `.heap`'s
  size yourself** — that is the real check.

---

## 5. Flash

Two ways in.

**Software (preferred, no buttons).** While the firmware runs, send
`RESET_INTO_BOOTSEL` (config command 1), wait for the `RPI-RP2` volume, copy the
UF2, wait for re-enumeration:

```powershell
python scripts\mcu_flash.py D:\rp2040-build\build-os\remapper.uf2
```

**Hardware.** Hold **BOOT** → tap **RUN** → release **BOOT** → `RPI-RP2` appears.
Necessary when the firmware does not boot.

Find the volume by label, not by assuming a letter (and note that
`Test-Path E:\` can fail on a volume that `Get-Volume` still lists):

```powershell
$vol = Get-Volume | Where-Object { $_.FileSystemLabel -eq 'RPI-RP2' }
Copy-Item $uf2 "$($vol.DriveLetter):\fw.uf2" -Force
```

Use an ASCII filename for the copy — it costs nothing and removes one variable.

---

## 6. Verification order

1. `mcu_probe.py` — is `VID_CAFE&PID_BAF2` there, and how many collections?
2. `mcu_hid.py` — `config version: 18`, `inject support: True/False`
3. `mcu_selftest.py` — uplink works (no user interaction, no mouse needed)
4. `mcu_inject_test.py` / `mcu_verify_hand.py` — the hand moves the cursor
5. `mcu_record.py` — real input streams through

If step 3 passes but 4 fails, the firmware is fine and the problem is on the
host — go to pitfall 3.
