# Patch v2 design: SignalRGB over the 2.4G receiver (step 3, not built)

Status 2026-10-20: **design only, no patch code** (approved, then built as patch v2:
out_v2/OFFLINE_VERIFICATION.md). Base: official v1.1 (`678ec144…5d4b`) + patch v1 (`c93031ed…6a79`, flashed).

Legend: **V** verified, with the method (asm = disassembly, dec = Ghidra decompile, data = parsed bytes, src = source code, hw = measured on the keyboard, log = SignalRGB's log, emu = emulator); **I** inferred, with the reasoning.

## 1. What step 2 established

- **V hw:** the receiver forwards `0x21 0x22 0x27 0x28` unchanged. The keyboard answers them
  via VIA's unknown-command path (`FF` + exact echo). VIA `0x01` round trip: 39.8 / 44.9 /
  49.9 ms (min / median / max over 20 queries). Logs: `logs/srgb_hidtest_20261020_222908_rx_viaprobe.log`.
- So the receiver needs no change. The fix is a keyboard-side hook on the radio path
  (outcome a). Step 4 (tunnelling through VIA) is not needed.

## 2. Re-verified facts the design builds on

### 2.1 How a radio packet reaches VIA

| Step | Address | What | Tag |
|---|---|---|---|
| UART | USART2 `0x40004400`, RX by DMA1 ch6 into a 148 B buffer (`0x20000DB0`), TX by DMA1 ch7 from a 64 B buffer (`0x20000E44`). Port table `0x08015C74` | Port config | V data |
| RX chunk | USART1/USART2 IRQs (`0x08003EDC` / `0x08003EF0`) both branch to the shared driver `0x08004124` with their instance (`0x20000024[0]` / `[1]`). On **IDLE** (line quiet) or buffer full, it calls the port's receive callback | A chunk = bytes until the line goes idle, at most 148 B | V asm/dec (driver); I (that the radio is USART2: DMA + 64 B TX buffer + device 2) |
| Mail | `0x080081A8` "channel sendmail": copies data into mail blocks (`+0x24` length, `+0x26` data), splits if longer than a block | | V dec |
| Radio task | `0x08015088`: loop `osSignalWait(0x1000, 1 ms)` (`0x08015130`), then **one** `osMailGet(q, 0)` → `0x08015214(mail)` → `osMailFree` | At most one chunk per loop pass, about 1 ms per pass | V asm (args); I (CMSIS-RTOS v1 names) |
| Line handler | `0x08015214`: skips leading `\r \n \0`, writes a 0 after the chunk, `strstr("System is up")`, then **`bl 0x080140AC` at `0x0801528A`** (r0 = chunk, r2 = 1) | | V asm/dec |
| `+DATA:` | `0x080140AC`: `strstr("+DATA:")` → `sscanf("+DATA:%d", &n)`, needs n > 0 → `strstr("\r\n")` → payload = the 2 bytes after it → **`bl 0x0800F620(payload, n & 0xFF, 0)` at `0x080140E2`** | | V asm/dec (`0x0800042C` = strstr) |
| Reply | Rewrites in place: `"HID="`, `n+1`, flag, payload[n], `"\r\n"`; then **`bl 0x08008050(2, line, n+8, 500)` at `0x0801413E`**, unconditionally | | V asm |
| Reply send | `0x08008050` → `0x080081A8`: queues the line as a mail to device 2's channel (waits up to 500 ms for a free block). It does **not** write the UART itself | | V dec |

Consequences:
- **Binary payloads are safe.** `strstr` finds `+DATA:` and the `\r\n` after the number
  before the payload, so `0x00`, `\r\n` or `+` inside colour data cannot confuse it. V asm.
- **Only the first `+DATA:` in a chunk is handled.** If the radio module ever sends two
  lines back to back with no idle gap, the second is lost. I: matters only for streaming
  rate; measure in step 6.
- **Payload length:** `n` comes from the module's `+DATA:%d`. V hw: the receiver's reports
  are 32 B and VIA works through it. I (strong): n = 32. A marker test (section 7) would
  prove it. The design handles 4 ≤ n ≤ 32 and passes anything else to VIA unchanged.
- VIA has **exactly two callers**: `0x080140E2` (radio, send = 0) and `0x08014D5E` (USB
  task, send = 1). The `+DATA:` handler has exactly one caller (`0x0801528A`). No pointer
  literals to either. V scan.

### 2.2 Reply flag byte

- The flag is `0x94`, `|0x40` when bit 10 of `0x20000B88` is set (giving `0xD4`), `|0x20`
  with VBUS. V asm (`0x08014108–0x08014124`).
- The keyboard-report sender to the radio, `0x08003C44` (`HID=` + report + `\r\n`, a ring of
  10, direct UART write), sets the same bits: byte 1 `|= 0x80`, `|0x40` (bit 10),
  `|0x20` (VBUS). V dec.
- I: `0x80` = "from the keyboard", `0x14` = packet type "raw HID reply". The meaning of bit 10
  is still unknown, and not needed.

### 2.3 Connection mode byte `0x20000B8C` (all writers found)

| Writer | Value | Tag |
|---|---|---|
| USB "configured" callback `0x080040EC` | 3 | V asm |
| Key `0x7F08` (Fn + Space), only if USB is live (`0x08013920`) | 3 | V asm `0x0800EA4A` |
| Key `0x7F0D` (Fn + V) | **2 = 2.4G** | V asm `0x0800E9FE` |
| Keys `0x7F09 0x7F0A 0x7F0B` (BT1–3) and `0x7E02`, if `strlen(0x20006A9A) ≥ 2` | **1 = BT** | V asm `0x0800EA2E` (`sb` = 1) |
| Key `0x7E00` | 0 | V dec |

- Bits 8–15 of the same word hold the radio channel (1–5, `SCH%d`), written by
  `0x080079B4`, `0x08007BD0` and `0x080077FE` with read-modify-write that keeps the low
  byte. V asm.
- The scan covered direct, base + offset and bit-band stores. **The earlier note "1 = BT (I)" is now
  V.**

### 2.4 What happens to replies

- V hw: 32 of 32 replies reached the host as one 32-byte IN report each, payload unchanged,
  at about 4.6 queries/s.
- Unknown: behaviour under load, and whether the receiver ever waits for a reply before
  forwarding the next OUT report (stop-and-wait). Section 5 makes reply suppression a
  runtime switch so step 6 can measure both.

## 3. Design

### 3.1 Principle: append only, v1 bytes untouched

- Patch v1's 480 bytes at `0x0801A828–0x0801AA07` stay **byte-identical**, and so does hook
  A. That covers USB receive, USB render rules and the switch-back.
- v2 adds code at `0x0801AA08`, plus three call-site changes:

| Site | Sector | Before (v1) | After (v2) |
|---|---|---|---|
| Hook A `0x08014D42` | 41 | `bl srgb_rx` | unchanged |
| Hook B `0x0801017C` | 32 | `bl 0x0801A910` (v1 `srgb_render`) | `bl srgb_render2` |
| **Hook C `0x080140E2`** | 40 | `bl 0x0800F620` (VIA) | `bl srgb_rx_radio` |
| **Hook D `0x0801413E`** | 40 | `bl 0x08008050` | `bl srgb_radio_send` |

- So the USB path keeps running the exact bytes that passed on hardware. "USB identical to
  v1" then comes down to `srgb_render2` passing control to v1 whenever the radio doesn't
  render, which the emulator can prove (section 6).

### 3.2 Per-link state (instead of one shared "enabled")

- The link-source field from the plan, in a stronger form: **each link has its own state,
  and the connection mode picks which one may render.**
- Reason: in 2.4G mode with the cable plugged in, both `1EA7:CEDD` and `6D66:8888` are
  present. SignalRGB would run two instances. With one shared flag, the USB instance's
  `0x25`/`0x26` would switch the radio stream on or off.

| Struct | Address | Fields | Writer |
|---|---|---|---|
| v1 (unchanged) | `0x20007E00` | magic `"SRGB"`, last_ms, enabled, rendering | USB task (enabled, last_ms), RGB task (rendering) |
| **radio (new)** | **`0x20007E10`**, 16 B | magic `"SRGR"`, last_ms u32, enabled u8, quiet u8, skip_reply u8 | radio task only |

- Both are in the unreferenced 584 B above the stack (`0x20007DB8–0x20007FFF`, same
  rationale as v1).
- The radio struct is initialised when its magic is wrong, as in v1.

### 3.3 `srgb_rx_radio(data, len, send)` (hook C, radio task)

- **Pass through** (tail-call VIA `0x0800F620` with r0–r2 unchanged; set skip_reply = 0)
  unless **all** hold:
  - mode byte == 2 (2.4G);
  - 4 ≤ len ≤ 32;
  - `0x21 ≤ data[0] ≤ 0x28`.

  So BT (mode 1), USB mode (3) and every non-SignalRGB packet behave exactly like v1, byte
  for byte.
- Handled (replies are written in place, `raw_hid_send` is never called; the stock handler
  sends the line):

| Cmd | Action | skip_reply |
|---|---|---|
| `0x21 0x22 0x23 0x27 0x28` | Same reply bytes as v1 USB, zero-filled to len | 0 (the reply goes out) |
| `0x24` errors | `24 FD` / `24 FE`, same rules as v1 | 0 |
| `0x24` valid | If radio enabled: `set_color` × count, stamp last_ms. Else drop | = quiet |
| `0x25` | enabled = 1, stamp last_ms, **quiet = data[1] & 1** | = quiet |
| `0x26` | enabled = 0 | = quiet |

### 3.4 `srgb_radio_send(dev, line, len, timeout)` (hook D)

- If radio skip_reply == 1: clear it and return (the line is not queued).
- Else tail-call `0x08008050` with r0–r3 unchanged.
- Hook D always runs straight after hook C in the same handler call (straight-line code
  `0x080140E6–0x0801413E`, V asm), and hook C writes skip_reply on every call. So the flag
  can't go stale.

### 3.5 `srgb_render2(effect)` (hook B, RGB task)

- If the v1 struct is valid **and** radio enabled == 1 **and** mode == 2 **and** RGB is lit
  (same test as v1) **and** less than 2000 ms since radio last_ms:
  - set v1 `rendering` = 1, iter++, task state = FLUSHING, then return. This is v1's
    "skip the effect" action.
- Else **tail-call v1 `srgb_render` `0x0801A910`** with r0 = effect. v1 then:
  - renders USB (mode 3 + VBUS + configured + fresh);
  - or, when `rendering` is still 1, clears the frame once and re-initialises the effect;
  - or calls the stock effect.
- Radio stopping (timeout, `0x26`, switch to BT/USB, RGB off) therefore uses v1's tested
  switch-back.

### 3.6 Behaviour table

| Situation | v2 |
|---|---|
| USB mode, SignalRGB on the cable | Exactly v1 |
| 2.4G, SignalRGB via the receiver | Radio stream renders; 2 s timeout |
| 2.4G + cable plugged in, both instances | Radio renders; USB stream dropped (v1 rule: mode ≠ 3) |
| 2.4G stream, switch to BT or USB | Falls back at once (mode ≠ 2), as v1 does on unplug |
| BT, anything | Exactly v1: radio packets go to VIA, `FF` replies |
| USB mode, receiver instance probing | mode ≠ 2 → VIA `FF` → handshake fails → plugin stays silent |
| 2.4G on battery | Renders at the driver's 20 % cap (VBUS-keyed, unchanged) |

## 4. Space and safety

- **Flash:** I estimate 350–500 B of new code (`srgb_rx_radio` ~200, `srgb_render2` ~90,
  `srgb_radio_send` ~24, state init ~30, literals). That ends at about `0x0801ABE0`, inside
  the 1,528 B free (`0x0801AA08–0x0801AFFF`). The linker script keeps the hard
  `≤ 0x0801B000` assert.
- **Sectors vs original:** 32, 40, 41, 53. All are rewritten by every official update.
  Sector 40 is new to the patch.
- **Conditions:**
  1. image ≤ `0x0801AFFF`: met;
  2. normal upgrade only: unchanged (your action);
  3. Gate 0: done;
  4. boot path untouched: met. The new sites sit in the radio task's `+DATA:` handler, long
     after boot. Scatter table, vectors, bootmagic, QK_BOOT and the flash-write path are
     unchanged, and selfcheck will keep checking them.
- **Calls out** (for the selfcheck allowlist): `0x0800F620` (VIA, tail call), `0x08008050`
  (tail call), `rgb_matrix_set_color`, `timer_read32`, and v1 `srgb_render`. No
  `raw_hid_send` on the radio path.
- **RAM writes:** `0x20007E10–1F` (radio struct), v1 `rendering` (`0x20007E09`),
  `0x20000AC3/ACC` (as v1), the received payload (in-place replies, ≤ 32 B).
- **Vendor frames:** the keyboard's radio path never runs the vendor CRC handler (V asm).
  The receiver answers CRC frames `0x11`–`0x17` itself (V src). A colour packet starts with
  `0x24`, so even a coincidental CRC match in bytes 6–7 (1 in 65,536 packets) can't be
  EnterDFU (`0x13 0x02`). I: negligible. The plugin could also nudge one colour LSB when
  bytes 6–7 match the CRC, removing it entirely (decide in step 8).
- **Stack:** pass-through is a tail call (no extra depth). Handled packets need a small frame
  plus `set_color`, shallower than VIA's own deep paths. I: to check in the step 5 listing.

## 5. Runtime switch: reply suppression ("quiet")

- `0x25` with byte 1 = 1 turns quiet mode on for the radio link. SRGBmods' standard `0x25`
  has a zero body, so the default stays off.
- **Off:** every radio packet gets its in-place echo, as the current firmware does. The
  echoes double the radio traffic but can act as acks for flow control.
- **On:** no reply to successful `0x24`, `0x25`, `0x26`, like USB.
- Step 6 measures both settings with the same firmware, and the plugin picks the winner.
- I: suppression shouldn't stall the receiver (HID dongles don't normally wait for IN
  reports), but that's untested.

## 6. Step 5 test plan (summary)

- **Build:** `patch_v2/` (v1 sources copied unchanged + `srgb_radio.c`). A v2 build script
  that:
  - keeps the SHA-256 gate and the record layout;
  - rebuilds v1's blob and requires `b9ca4c09…` at `0x0801A828`;
  - links the radio blob at `0x0801AA08`;
  - writes `out_v2/patched_v2.hex` (versioned name, SHA-256 recorded).
- **selfcheck v2:**
  - vs original: only the four sites + appended bytes;
  - vs v1: identical except hooks B/C/D + the bytes from `0x0801AA08`;
  - protected ranges; full disassembly of the new code; call/store allowlists;
  - `selfcheck.py` and `emutest.py` still pass on v1.
- **Emulator** (extends `hostsim.py radio`, which already runs the real `0x080140AC`):
  - every non-SignalRGB first byte (248 values), and SignalRGB IDs in modes 0/1/3, reach
    VIA with identical arguments, give identical replies and an identical `HID=` line, and
    change no RAM;
  - queries over radio → the v1 reply bytes in the `HID=` line;
  - streams → `set_color`; quiet on/off → line queued or not;
  - **differential USB test:** every USB input from `emutest.py` gives identical results on v1
    and v2, and `srgb_render2` hands off to v1 in every non-radio state;
  - mode switches, the 2 s timeout, both-links-live.
- **Hardware order** (your OK at each step):
  1. wired regression (read-only, `--led`, SignalRGB);
  2. receiver read-only;
  3. a few LEDs;
  4. the full LED test.

## 7. Optional check now (needs your OK): is the payload always 32 bytes?

- **Command:** `srgb_hidtest.py --vid 0x6D66 --pid 0x8888` with a new `--marker-probe`
  option, read-only and safe on v1.
- **Packets:** `0x01`, then `0x21` with byte 31 = `0x5A`, then `0x01` (3 in total). Byte 6–7
  CRC checked as usual.
- **Expected:** the keyboard echoes all n bytes, so the reply should end with `5A` if n = 32.
  If it ends with `00`, n < 32 and the stream format needs a second look.
- **Result (V hw, 2026-10-20 23:37, v1, user's OK):** the reply ends with `5A`: n = 32. Log
  `logs/srgb_hidtest_20261020_233728_rx_markerprobe.log`.

## 8. Open risks, carried to steps 6 and 7

- One `+DATA:` per UART chunk and one chunk per radio-task pass (about 1 ms): bursts from the
  module could merge, and the second line would be lost. Pace the plugin, and measure.
- 45 ms round trip: a stop-and-wait handshake is fine, but streaming needs one-way
  throughput. Unknown until measured.
- Key reports and replies share the UART TX and the radio link. Typing lag while streaming
  is a step 6 check.
- Sleep and idle on 2.4G: step 7.
