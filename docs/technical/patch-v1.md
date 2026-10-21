# Phase 2 report: SignalRGB patch for the Zoom75 TIGA (stock firmware v1.1)

> **Engineering record:** Phase 2, 2026-10-20: patch v1 (USB) as built and checked offline, before it was flashed. It was then flashed and verified on the keyboard ([hardware results](../hardware-results.md)). Status lines describe that moment. Current state: [README](../../README.md) · [all documents](../README.md).

**Status: built and statically checked. Not flashed. Waiting for your go-ahead.**
Nothing was sent to the keyboard in this phase: no HID traffic, no bootloader, no flashing. `original/` is untouched (its SHA-256 still matches `original/SHA256SUMS.txt`). Phase 3 (test script, plugin, README) is not started.

| Output | SHA-256 |
|---|---|
| `out/patched.hex` (the file you would flash) | `c93031edfbe3807372b5014b689165278e532de6e4f563526b0f0c6f850e6a79` |
| input: original v1.1 hex | `678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b` |

Legend, same as [feasibility.md](feasibility.md): **V** = verified (method given), **I** = inferred (reasoning given).

---

## 1. What the patch does

Two 4-byte instruction changes plus 480 bytes of new code in the free tail of sector 53.

| Where | Before | After | Effect |
|---|---|---|---|
| `0x08014D42` (USB HID task) | `bl 0x0801240C` (stub that returns 0) | `bl srgb_rx` (`0x0801A828`) | Sees every USB raw-HID packet first. Handles `0x21`–`0x28` and returns 1. Returns 0 for everything else, exactly like the stub, so the vendor and VIA handlers run as before. |
| `0x0801017C` (`rgb_matrix_task`, RENDERING state) | `bl rgb_task_render` (`0x080102C4`) | `bl srgb_render` (`0x0801A910`) | If SignalRGB is on and fresh: skips the effect and goes straight to FLUSHING, so the streamed colours are shown. Otherwise it tail-calls the original `rgb_task_render`. |
| `0x0801A828–0x0801AA07` | erased / not in hex | patch code (480 B) | |

Both original functions stay byte-for-byte intact. Only their single call sites changed. **V** (asm: one `bl` each, no pointer literal anywhere; `tools/selfcheck.py` checks this.)

### Protocol (SRGBmods QMK protocol 1.0.6)

| Cmd | Reply (32 bytes, rest zero) | Notes |
|---|---|---|
| `0x21` QMK version | `21 00 00 00` | Stock QMK base version unknown, so 0.0.0. The plugin only logs it. |
| `0x22` protocol | `22 01 00 06` | Same as SRGBmods. |
| `0x23` unique ID | `23 00 00 00` | Configurable in `srgb.c`. |
| `0x24` stream `[24, start, count, rgb…]` | none on success | `253` if start+count > 86, `254` if count > 9 or the packet is too short. Reply is `[24, code]`; SRGBmods leaves byte 0 stale. |
| `0x25` mode on | none | |
| `0x26` mode off | none | |
| `0x27` LED count | `27 56` (86) | |
| `0x28` firmware type | `28 02` | Same as SRGBmods (VIA type). |

Colours go through the firmware's own `rgb_matrix_set_color` (`0x0800FFE8`) → driver `0x080128D0`. So the firmware's power cap still applies (60 % on USB power, 20 % on battery). The vendor indicator LEDs (caps lock, mode and so on) are still drawn on top at flush time. **V** (asm/dec of `0x080159A8`).

### When it falls back to the normal effect

Rendering SignalRGB needs **all** of these. If any one fails, the stock effect runs:

1. Mode enabled (`0x25`, cleared by `0x26`).
2. A valid stream packet or enable within the last **2000 ms**. The timer is `timer_read32`. Unsigned arithmetic also treats a timer that went backwards (reset, wake) as stale.
3. Connection mode byte `0x20000B8C == 3` (USB). In BT or 2.4G mode it's 1 or 2.
4. VBUS present: bit 9 of `0x20000B88`.
5. USB device state `0x20005EC2 == 3` (configured). Unplugged or PC asleep (suspended = 4) fails this.
6. RGB is on and not suspended in the firmware's own `rgb_matrix` state. So your RGB-off key still turns the LEDs off.

On the switch back, the patch clears the frame buffer once and sets `last_effect`/`last_enable` to `0xFF`, the same values as at boot. That makes the stock effect re-initialise cleanly. Stream packets that arrive while the mode is off or the link isn't USB are dropped, so nothing flickers. If SignalRGB keeps streaming after a sleep or a mode switch, it picks up again by itself, with no need to re-send `0x25`.

### State (8 bytes of RAM)

The state lives in a 12-byte struct at `0x20007E00`, inside the unused 584 B above the stack top (`0x20007DB8–0x20007FFF`). It carries a magic word `"SRGB"`. Until the magic is valid, the patch treats itself as disabled and initialises the struct.

**Design deviation from feasibility.md §3, made for safety:** §3 recommended growing the zero-init length in the scatter table. That changes data that the scatter-load routine reads at boot, which conflicts with **condition 4** ("the patch must not touch … scatter-load"). I used §3's listed alternative (the magic-guarded word) instead, so the scatter table is byte-identical. **V** (selfcheck).

---

## 2. Safety conditions

| Condition | Status | How |
|---|---|---|
| 1. Image within sectors 0–53 (≤ `0x0801AFFF`) | **Met.** Ends at `0x0801AA07`. Sectors written: 32, 41, 53. | V: selfcheck byte diff |
| 2. Flash only via the Wuque tool's normal upgrade flow | **Your action.** The hex keeps the original's exact layout: CRLF, upper-case, 16-byte records on 16-byte boundaries, same `0x04` segment records, same start record `0x08000171`. Model-ID bytes at `0x08004400` are unchanged, so the tool's model check still passes. | V: selfcheck; I: tool acceptance (its parser is Artery's native `Files.dll`) |
| 3. Gate 0: reflash official v1.1 once first | **Pending, your action.** Please do it before flashing `patched.hex`. | n/a |
| 4. Boot path untouched | **Met.** Vector table, `__main`/scatter-load, reset handler, decompressor, zero-init routine, scatter table, RW image, init table, `keyboard_bootmagic_init`, `bootloader_jump`, QK_BOOT handler and the flash-write path are all byte-identical. The patch runs only from the HID task and the RGB task, long after boot. | V: selfcheck protected-range compare |
| §8 SHA-256 gate | `build_patch.py` refuses any other input. Tested: v1.0 is rejected. | V: run |
| §9 Recovery | All recovery paths are unchanged. Vendor command `0x13 0x02` (enter DFU) still reaches the vendor handler, because the hook returns 0 for anything outside `0x21`–`0x28`. QK_BOOT hold and Space+B bytes are identical. The patch never writes flash. | V: selfcheck + emulator |

---

## 3. Verification done

**Static self-check** (`tools/selfcheck.py`, independent of the build script and the toolchain): **PASSED**
- Both hex files parse strictly: checksums, record types, CRLF, EOF, no overlaps.
- Inside the original image, only the 7 bytes of the two `bl`s differ (8 bytes, one unchanged). New bytes appear only at `0x0801A828–0x0801AA07`.
- 16 protected ranges are byte-identical (listed in §2).
- Recursive-descent disassembly from both entry points accounts for every one of the 480 bytes: code, literal, jump table or `nop` padding. Nothing unexplained.
- No forbidden instructions (svc, cpsid, msr, wfi, ldrex/strex, barriers).
- External calls go only to these 5: `rgb_matrix_set_color`, `rgb_matrix_set_color_all`, `raw_hid_send`, `timer_read32`, and the original `rgb_task_render`.
- RAM stores with a known address go only to the patch state (`0x20007E00–0B`) and `0x20000AC1/AC2/AC3/ACC` (rgb last_enable, last_effect, task state, iter). The only other stores are to the stack and the received HID buffer (replies in place, as VIA does).
- No literal points at peripherals, system registers or flash outside the allowlist.

**Negative tests** (the checks must fail): flipping one vector-table byte, pointing a literal at the flash controller, writing past sector 53, and the unpatched hex are all caught. **V**

**Emulation test** (`tools/emutest.py`, Unicorn 2.1.4 CPU emulator, no hardware): **3,909 checks passed, 0 failed.**
It runs the real bytes of `out/patched.hex`, entering through the patched `bl` at each hook site, with the five firmware functions replaced by recording stubs. Covered:
- All 248 non-SignalRGB first bytes: return 0, no calls, no RAM change.
- A short packet passes through.
- Every query reply.
- Stream before enable is dropped.
- Enable + stream: exact `set_color(index, r, g, b)` sequence.
- All error codes, including LEDs 84–85 at the boundary.
- Render while active.
- Each of the 8 fallback triggers.
- The 1999/2000 ms timeout edge.
- Wireless → USB resume, and a timer going backwards.
- On every call: SP balanced and r4–r11 preserved.

**Reproducible build:** two clean builds give an identical `patched.hex` (`c93031ed…`) and patch blob (`b9ca4c09…`). The script pins the exact compiler version string. **V**

**Stack:** `srgb_rx` uses a 40-byte frame. The vendor handler that runs at the same depth for every normal packet uses 80 bytes, so the patch adds no new peak in the USB task. **V** (asm). In the RGB task, the fallback path tail-calls the original renderer (adds 0 bytes). The one-time switch-back path peaks at about 60 bytes. **I**: that is comparable to the original renderer plus one effect; not measured.

---

## 4. Phase 2 open items (feasibility.md §11)

| Item | Result | Tag |
|---|---|---|
| Which RAM flag means "USB mode" | **Resolved.** `0x20000B8C` (byte) = connection mode, 3 = USB. Set to 3 by the USB stack's "device configured" callback (`0x080040EC`, entry [2] of the user-callback table `0x08019C0C` passed to USB init at `0x08005D66`). Also set by the Fn USB key (keycode `0x7F08`) only when USB is live. The wireless channel code (`0x0800A1FC`, `0x080079B4`) treats 1 and 2 as wireless. Companion flags: bit 9 of `0x20000B88` = VBUS (copied from PA9 at `0x08005D4A`); `0x20005DB0+0x112` = USB device state. | V asm/dec; exact meaning of values 0/1/2 is I |
| Does IF1 stay enumerated in BT mode with the cable in? | **Probably yes.** The wireless-mode keys only write the mode byte and save it. I found no USB disconnect on that path. **The patch doesn't depend on the answer:** in wireless mode it ignores streams and falls back. Phase 3 can confirm on hardware. | I (absence of a disconnect call) |
| How the LED flush reads the frame buffer (DMA timing) | **Resolved.** Flush `0x080159A8` first draws the vendor indicators into the frame buffer. Then a CPU loop encodes each of the 86 LEDs into a separate DMA bit buffer (`0x08015B78`). DMA never reads the colour buffer, so a stream write during a flush can mix at most one frame. Cosmetic only. | V dec/asm |
| Effect names | Not done; not needed. | n/a |
| Chip contents `0x0801B000–0x0801DFFF` | Can't be resolved without reading the chip, which is out of bounds. The patch stays clear of it (ends `0x0801AA07`). | n/a |
| BOOT0 access | Needs a look at the PCB; not done. | n/a |

New finding: the vendor region is entered only once, at boot (`bl 0x0801E400` at `0x08007E30`, inside `keyboard_bootmagic_init`). No runtime code calls it. **V** (literal and branch scan). Nothing calls into vendor code while the patch runs.

---

## 5. Known limits (to watch in Phase 3)

- **SignalRGB "shutdown colour"** won't stick. After 2 s with no packets, the keyboard returns to its own effect, as you asked. The Phase 3 plugin should not offer that option.
- **PC sleep / USB suspend** falls back immediately. SignalRGB resumes by itself when it streams again.
- Brightness is capped by the firmware at 60 % on USB power. This is intended.
- Vendor indicator LEDs are still drawn over SignalRGB, like SRGBmods' caps-lock handling.
- **Soft reset while streaming (I):** RAM survives a soft reset. If the keyboard resets during the first 2 s of uptime while SignalRGB is on, the LEDs could stay dark for at most the rest of that window. In every other case, the old timestamp looks stale after the reset.
- Not testable without hardware: real stream throughput, the interaction of HID and RGB task priorities, and how the DFU tool accepts the file (structurally identical to the official hex). These are Phase 3 items.

---

## 6. Files

| Path | What |
|---|---|
| `patch/srgb.c` | Patch source (C) |
| `patch/fw.h` | Firmware addresses, each with how it was verified |
| `patch/srgb.ld` | Linker script: `0x0801A828`, asserts no RAM sections and end ≤ `0x0801B000` |
| `build_patch.py` | SHA-256 gate → compile → patch → `out/patched.hex` + `out/patch_report.md` |
| `tools/selfcheck.py` | Independent static check (§3) |
| `tools/emutest.py` | Emulator test (§3) |
| `out/patched.hex` | **Result** |
| `out/patch_report.md` | Hashes, byte diff, changed hex lines, full disassembly |
| `out/build/` | ELF, map, listing, flat images |

Rebuild and re-check:
```
.venv/Scripts/python.exe -I build_patch.py
.venv/Scripts/python.exe -I tools/selfcheck.py
.venv/Scripts/python.exe -I tools/emutest.py
```

Installed this phase:
- Arm GNU Toolchain 15.2.rel1 (arm-none-eabi, Windows x86_64): 296 MB zip from developer.arm.com, SHA-256 `7936cac8…9ece`, which matches Arm's published value. Unpacked to `.toolchain/arm-gnu-15.2/` (1.1 GB, no PATH or registry changes). The zip is kept in `.toolchain/dl/`.
- unicorn 2.1.4 (15.9 MB wheel) into `.venv`.

## 7. Before you flash (when you decide to)

1. **Gate 0:** reflash the official v1.1 hex once with the Wuque tool, normal upgrade flow.
2. Then flash `out/patched.hex` the same way. Check its SHA-256 is `c93031ed…6a79`. Use "erase necessary sectors" only, never global or mass erase. Keep the tool open afterwards, so you can reflash the official hex in the same session if needed (feasibility.md §9).
3. Check that the keyboard still works normally: typing, VIA, Console, knob, LCD, BT and 2.4G. Until SignalRGB sends `0x25`, the patch does nothing visible.

Phase 3 (test script, plugin, README) waits for your go-ahead.
