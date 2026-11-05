# Wire protocol

Everything travels over the board's **vendor HID collections on USB interface 1**.
No CDC, no extra endpoints, no descriptor changes — which is why the injection
patch cannot break enumeration.

| collection | usage page | usage | report |
|---|---|---|---|
| **config** | `0xFF00` | `0x20` | Feature, report ID **100**, 32 bytes |
| **monitor** | `0xFF00` | `0x21` | Input, report ID **101**, 63-byte payload |

Relevant constants from the firmware:

```c
#define CONFIG_SIZE 32
#define REPORT_ID_CONFIG  100
#define REPORT_ID_MONITOR 101
const uint8_t CONFIG_VERSION = 18;
```

Byte layout and CRC are in `types.h`; the humans-readable source of truth for
the command set is the `ConfigCommand` enum.

---

## Config channel

### Request framing

`set_feature_t`, 32 bytes, packed:

| offset | size | field |
|---|---|---|
| 0 | 1 | `version` — must equal `CONFIG_VERSION` (18) |
| 1 | 1 | `command` (`ConfigCommand`, int8) |
| 2 | 26 | `data` |
| 28 | 4 | `crc32` little-endian |

CRC is plain **zlib CRC-32** over bytes 0..27 (`zlib.crc32(data) & 0xFFFFFFFF`).
`handle_set_report1` rejects the report outright if the CRC fails or the version
does not match, and sets `last_config_command = INVALID_COMMAND`.

With hidapi, the report ID is the first byte of the buffer:

```python
dev.send_feature_report(bytes([REPORT_ID_CONFIG]) + build_set_report(cmd, payload))
```

### Response framing

`get_feature_t`, also 32 bytes, but **there is no echoed command byte**:

| offset | size | field |
|---|---|---|
| 0 | 28 | command-specific `data` |
| 28 | 4 | `crc32` |

Two consequences that cost real time (see pitfall 5):

* parsing it as `{version, command, data}` "works" but yields nonsense
* **hidapi on Windows needs `CONFIG_SIZE + 1`** as the length, because Windows
  counts the report ID; with `CONFIG_SIZE` the transfer asks for 31 bytes, the
  handler returns 0, and hidapi raises `OSError: read error`

```python
raw = dev.get_feature_report(REPORT_ID_CONFIG, 33)
if len(raw) == 33:
    raw = raw[1:]                 # Windows keeps the report ID
data = raw[:28]                   # validate CRC over these 28 bytes first
```

### Which command answers what

`handle_get_report1` switches on `last_config_command`, which the *set* half set.
Notable ones:

| command | value | response |
|---|---|---|
| `GET_CONFIG` | 3 | `get_config_t`; byte 0 is the version |
| `GET_OUR_USAGES` | 8 | 3 × `{u32 usage, u32 count}` RLE runs; send `requested_index` in the payload first |
| `GET_THEIR_USAGES` | 9 | same, for the attached device |
| `GET_MAPPING` | 6 | one `mapping_config11_t` |
| `GET_EXPRESSION` | 21 | 1 byte count + expression bytecode |
| `PERSIST_CONFIG` | 7 | persist return code |
| `SUSPEND` / `RESUME` | 10 / 11 | `RESUME` sets `config_updated`, which forces `set_mapping_from_config()` to re-run — handy for re-reading mapping state without a reboot |

**Unknown commands answer with 28 bytes of `0xFF`.** Use that to tell a stock
build from a patched one — but prefer an explicit marker:

```c
case ConfigCommand::INJECT:
    config_buffer->data[0] = 'I'; config_buffer->data[1] = 'N';
    config_buffer->data[2] = 'J'; config_buffer->data[3] = '1';
    break;
```

Then `Middleman.has_inject()` is a simple `resp[:4] == b"INJ1"`.

`get_config_t` (packed, 27 bytes):

```c
uint8_t  version;                       // 18
uint8_t  flags;                         // bit4 auth, bit5 gpio, bit6 normalize
uint8_t  unmapped_passthrough_layer_mask;
uint32_t partial_scroll_timeout;
uint16_t mapping_count;
uint32_t our_usage_count;
uint32_t their_usage_count;
uint8_t  interval_override;
uint32_t tap_hold_threshold;
uint8_t  gpio_debounce_time_ms;
uint8_t  our_descriptor_number;
uint8_t  macro_entry_duration;
uint16_t quirk_count;
```

`struct.unpack_from("<BBBIHIIBIBBBH", data, 0)`.

---

## INJECT — the hand

Two added commands, placed after `GET_QUIRK = 25`:

```c
INJECT    = 26,   // data: int16 dx, int16 dy, int16 wheel, uint8 buttons, uint8 flags
INJECT_KB = 27,   // data: uint8 n, then n * { uint16 usage, uint8 down }
```

* `flags & 0x01` means "buttons field is valid"; omit it to leave the currently
  held buttons alone
* `buttons` is a bit mask, bit 0 = left … bit 4 = button 5
* `INJECT_KB` usages are keyboard usages **without** the page: `0x0004` is `A`,
  and `inject_key` ORs in `0x00070000`
* dx/dy clamp to `int16`, wheel to `int8`
* the command is *accumulated*: several `INJECT`s between two `process_mapping`
  ticks add up, and the accumulator keeps the sub-count remainder

### How it reaches the wire

`inject_mouse()` stores the pending delta. `process_mapping()` (1 kHz, driven by
a repeating timer) calls `apply_injected_relative()`, which folds the delta into
the **same `accumulated` map the normal relative mappings use**:

```c
if ((d.value != 0) && (our_usages_flat.count(d.usage) != 0)) {
    acc[d.usage] += d.value * 1000;      // accumulator is in 1/1000 count units
}
```

So the report goes out through the ordinary path — no new endpoint, no new
descriptor entry, and the emulated device descriptor stays authoritative.

Buttons and keys are absolute, so `apply_injected_absolute()` re-asserts them
onto `reports[]` every frame, just before the queueing loop.

**Why the config handler needs no locking:** `handle_set_report1` runs from
`tud_task()`, which is called from the main loop — the same context as
`process_mapping()`. If the firmware ever moves `tud_task()` into an ISR, this
needs a critical section.

### Verifying it end to end

Instrument temporarily (then strip it — see pitfall 7):

```
0xFFFF0001  dx arrived in the config handler
0xFFFF0002  folded into the accumulator
0xFFFF0004  value written into the mouse-X field
0xFFFF0012  report id + first payload bytes actually handed to the USB stack
0xFFFF0014  tud_hid_n_report returned true
0xFFFF0015  tud_hid_n_report returned false
0xFFFF0016  tud_hid_n_ready(0)
0xFFFF0017  tud_hid_n_ready(0), sampled every frame
```

A healthy trace looks like:

```
cmd received (dx)              = 150
folded into accumulator        = 0x10096
written to mouse-X field       = 150
SENT report(id|b1|b2|b3)       = 0x01009600     id=1, buttons=0, X=0x0096
tud_hid_n_report OK count      = 1
tud_hid_n_report FAIL count    = 0
```

`0x01009600` decodes as report ID 1 (mouse), then `00 96 00` = buttons 0,
X = 150 little-endian. **If that is what you see, the firmware is correct** — any
remaining problem is on the host (pitfall 3).

---

## Monitor — the eyes

Input report ID 101, 63-byte payload, `monitor_report_t`:

```c
uint8_t report_id;                 // 101
struct { uint32_t usage; int32_t value; uint8_t hub_port; } items[7];
```

64 bytes including the report ID; hidapi returns it with the ID as byte 0, so
strip it. Parse 9-byte items and skip `usage == 0`.

Enable it with `SET_MONITOR_ENABLED` (22) and a 1-byte payload, `1` or `0`.

**Semantics** (`monitor_read_input()` in `remapper.cc`):

* relative usages report only when the value is non-zero
* binary usages report only on change
* so an idle mouse produces nothing — that is what makes this a clean event log
* up to 7 items per report; when the queue is full further events are **dropped**,
  so do not leave the monitor enabled while producing high-rate debug output

Firmware-side usage identifiers worth knowing:

| usage | meaning |
|---|---|
| `0x00010030` / `0x00010031` | mouse X / Y (relative) |
| `0x00010038` / `0x000C0238` | wheel / AC pan |
| `0x00090001`…`0x00090008` | mouse buttons 1–8 |
| `0x000700xx` | keyboard |
| `0xFFF00000`-page | expressions, registers, macros, layers (internal) |

Mouse reports: `report_id 1`, payload `buttons(8×1 bit) | X(int16) | Y(int16) |
wheel(int16) | pan(int16)` — 9 bytes. X and Y are `Input (Data,Var,Rel)` with a
logical range of −32768…32767.

Report descriptor **quirks worth not "fixing"**: `bInterfaceProtocol` was set to
BOOT KEYBOARD upstream, and `tud_hid_set_protocol_cb` then makes
`tud_descriptor_report_cb` serve a *different* (keyboard-only) descriptor. The
patch sets `HID_ITF_PROTOCOL_NONE` so the host can never trigger that swap. It
was not the cause of the observed failure, but it removes a whole class of them —
and it also means `boot_kb_report_descriptor`, which upstream marks "isn't sent
to the host", now really never is.

---

## PC-side driver

`scripts/mcu_hid.py` implements all of the above. Public surface:

```python
Middleman().open(want_monitor=True)   # context manager
    .send(cmd, payload)               # set_feature_report, no response
    .request(cmd, payload) -> bytes   # set + get, validated 28-byte data
    .get_version() -> int             # 18
    .has_inject() -> bool             # patched firmware?
    .get_config() -> dict
    .get_usages("our" | "their") -> [(usage, count)]
    .set_monitor(bool)
    .read_events(timeout_ms) -> [MonitorEvent]   # non-blocking drain
    .inject_mouse(dx, dy, wheel, buttons)
    .inject_key([(usage, down), ...])
    .calibrate() -> float             # pixels per count
    .inject_mouse_px(dx_px, dy_px)
```

Open the two collections by **usage**, not by index — enumeration order is not
stable across reflashes:

```python
for d in hid.enumerate(0xCAFE, 0xBAF2):
    if d["usage_page"] == 0xFF00 and d["usage"] == 0x20: cfg_path = d["path"]
    if d["usage_page"] == 0xFF00 and d["usage"] == 0x21: mon_path = d["path"]
```

Two devices open at once is fine on Windows (hidapi opens with shared access),
but two *processes* both draining the monitor will each see a subset of events —
make sure only one recorder runs at a time.
