# Phase 1 feasibility: SignalRGB on the Zoom75 TIGA (stock firmware v1.1)

> **Engineering record:** Phase 1, 2026-10-19/20: the static analysis that decided whether the patch was possible and safe, the four safety conditions, and the recovery paths. Status lines describe that moment. Current state: [README](../../README.md) · [all documents](../README.md).

Target: `WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex`
SHA-256 `678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b`, the official file (not in this repository).

This phase was static analysis only. Nothing was sent to the keyboard, nothing was flashed, and MeletrixID was not installed.

## Recommendation: GO for Phase 2, under four conditions

The firmware is a Keil-built QMK derivative on an Artery AT32F415. It has a free receive hook in the USB raw-HID task, QMK's own render function, and a working `rgb_matrix_set_color`, so SignalRGB can be added without changing any existing VIA command. The patch fits in flash that every official update already rewrites, needs about 8 bytes of RAM, and runs only after the boot-time recovery check.

Conditions:

1. **Keep the patched image inside sectors 0–53 (end ≤ `0x0801AFFF`).** That's the same set of 2 KB sectors the official hex covers, so the DFU tool erases nothing it wouldn't erase anyway.
2. **Flash only through the Wuque tool's normal upgrade flow.** It hard-codes "erase necessary sectors". Never use Global/Mass Erase, option bytes, read protection or sLib, in this or any other tool. Reason: a vendor-programmed region at `0x0801E000–0x0801EFFF` is on the chip but not in any hex file. The firmware won't start without it, and nothing published can restore it.
3. **Gate 0 (your action, before any patched build):** reflash the official v1.1 hex once with the Wuque tool. This proves the update and recovery path works on your unit.
4. **The patch must not touch the boot path** (reset handler, scatter-load, `keyboard_bootmagic_init`). Then the recovery key combo behaves exactly as in stock firmware.

## Legend

- **V** = verified in this session, with method:
  - *asm*: disassembly (capstone, `tools/dis.py`)
  - *dec*: Ghidra decompiler, cross-checked
  - *data*: parsed from bytes
  - *src*: source code (SRGBmods C, Console JS, decompiled DFU-tool C#)
- **I** = inferred, with the reasoning given.

A previous session left the files in `analysis/` and `tools/`, and its transcript is gone. I used them only as leads; everything below was re-derived in this session. The Ghidra exports in `analysis/ghidra/tiga_v11_p3/` were made from a flat binary that `cmp` shows is identical to the original hex. Ghidra 12.1.4 and JDK 21 were already in `.toolchain/`. I added only ilspycmd 9.1.0.7988, a 4 MB NuGet package, in `.toolchain/ilspycmd/`, to decompile the DFU tool.

---

## 1. Inputs

| File | Source | Notes |
|---|---|---|
| TIGA fw v1.1 (20250412) | meletrix.com/pages/firmwares → cdn.shopify.com | Your copy, `original/` and both downloads are byte-identical. **V** data |
| TIGA fw v1.0 (20241219), SHA-256 `cd96f8c9…` | same page | Used for diffing |
| `Zoom75_Tiga_rgb-via-v3.json` | same page | VID `0x1EA7`, PID `0xCEDD`, matrix 6×15 |
| Console `index-C0phc9H3.js` (1.75 MB) | zoom75tiga.meletrix.com | |
| `DFU.zip` | same page | Rebranded Artery ISP Programmer (.NET) |
| SRGBmods `QMK_Community_Module` | GitHub | SignalRGB protocol 1.0.6 |
| `Keychron_V1_Max_QMK_ANSI_Encoder_Keyboard.js` | SRGBmods/qmk-plugins | Plugin template |
| MichiG21 notes | GitHub | The TIGA has a battery, so unplugging USB doesn't reset it |

## 2. Hardware

| Finding | Evidence | Tag |
|---|---|---|
| MCU **AT32F415RCT7** (Cortex-M4) | String at `0x0801A204`; GPIO through the bit-band alias of `0x40010800`; ROM bootloader at `0x1FFFAC00` | V data/asm |
| 256 KB flash in 2 KB sectors, 32 KB SRAM | DFU tool sector rule (≥256 KB → 2 KB); firmware page rule at `0x08002168`; RAM size from datasheet, consistent with the linker layout | V src / I (RAM) |
| Keil/armlink build with an RTOS and an auto-init table | Scatter table `0x0801A6E4`; init table `0x0801A59C–0x0801A6D4` | V data |
| Bluetooth/2.4G is a separate module on a UART, driven by AT commands | `AT+HID=`, `AT+NAME=Zoom75 Tiga`, `AT+OTA`…; BT receive path `0x080140AC` | V data/dec |
| RGB, matrix scan (and LCD) run on the AT32 in all modes; a single RGB code path | One `rgb_matrix_task` | V dec / I (LCD) |
| External W25Q64 SPI flash | Init entry "sim spi w25q64" | V data |
| **86 RGB LEDs**, GRB byte order (WS2812-style) | `rgb_matrix_set_color_all` loops `< 0x56`; the driver stores G,R,B | V asm / I (LED type) |
| "KB81" is the vendor model number, not the LED count | DFU tool `GetDeviceModel()` prints byte 2 of the model ID (`0x51` = 81) | V src/data |
| Hardware watchdog (IWDG), about 2 s | `wdg_init` `0x08015770` | V asm / I (timeout) |

## 3. Memory map and space budget

| Range | Contents | Tag |
|---|---|---|
| `0x08000000–0x0801A827` | Official image, 108,584 B (sectors 0–53) | V data |
| **`0x0801A828–0x0801AFFF`** | **2,008 B free tail of sector 53. Patch code goes here.** | V |
| `0x0801B000–0x0801DFFF` | Not referenced by code and in no hex; chip contents unknown. Avoid. | V / unknown |
| `0x0801E000–0x0801EFFF` | **Vendor region required at boot, not in any hex. Never erase.** | V asm |
| `0x0801F000`, `0x0801F010` | Settings record (the only internal-flash write path, `0x08008748` → `0x08002168`) | V dec/asm |
| `0x20007DB8–0x20007FFF` | 584 B of RAM above the stack top. Nothing references it (the code has no `movt`; literal scan clean). | V / I (32 KB RAM) |

**Budget.** I estimate the patch at under 1 KB of Thumb-2: the SignalRGB handler plus a render trampoline. It doesn't need a per-LED buffer, because colors go straight into the firmware's existing 258-byte frame buffer through `rgb_matrix_set_color`, the same way SRGBmods does it. For state it needs about 8 bytes (active flag + last-packet time). Two ways to get them:
- **Recommended:** extend the zero-init length in the scatter table (the word at `0x0801A6FC`, `0x709C` → `0x70A4`), so the bytes are cleared at boot.
- Alternative: keep a magic-guarded word in the same RAM gap.

Every sector the patch would change (32, 41 and 53) is already rewritten by an official update.

## 4. USB

| Item | Value | Tag |
|---|---|---|
| VID:PID | `0x1EA7:0xCEDD` (struct at `0x08019C00`), matches the VIA JSON | V data |
| Config descriptors | 3 variants at `0x08019DA5`/`0x08019E00`/`0x08019E5B`, 3 interfaces each | V data |
| IF0 | Keyboard (boot 8 B or NKRO 15 B), EP `0x81` | V data |
| **IF1** | **Raw HID `0xFF60/0x61`**, descriptor `0x08019D83`, 32 B, EP `0x82` IN / `0x02` OUT, 1 ms | V data |
| IF2 | 182-byte multi-report descriptor, EP `0x83` | V data |
| DFU mode | Artery ROM DFU `2E3C:DF11` | V src |

The DFU tool's `.Config` value `vid_5542&pid_03d6` doesn't match this keyboard; it's a stale default.

## 5. Code locations and hook points

### Raw HID and VIA

| Address | Role | Tag |
|---|---|---|
| `0x08014CDC` | USB HID task. Per packet: `0x08014D42 bl 0x0801240C(data,len)` → vendor CRC handler `0x0800AC7C` → VIA `0x0800F620(data,len,1)` | V asm |
| **`0x0801240C`** | **`return 0` stub, single caller, USB-only. Hook A for SignalRGB.** A nonzero return skips the vendor and VIA handlers. | V asm |
| `0x080140AC` | Bluetooth path → VIA with send=0. Doesn't pass through hook A. | V dec |
| `0x0800F620` | VIA dispatcher: protocol `0x000B`; cmds `0x01`–`0x15`; unknown → `0xFF` | V dec |
| `0x08014DE4` | `via_command_kb` weak stub (`return 0`), alternative hook | V asm |
| `0x0800AC7C` | Vendor CRC16-framed table (8 groups at `0x080172F4`: info `0x11`, control `0x13`, LCD `0x1C`…) | V dec/data |
| `0x08009F64` | `raw_hid_send(data,len)` (includes a 2 ms delay) | V dec |
| `0x08014E00` | Custom values, channel 3 = RGB Matrix (brightness/effect/speed/color) | V dec |
| `0x08014E16` | Custom values, channel 0: debounce and "keyboard speed" (4 bytes, saved to EEPROM offset `0x58`); matches VIA JSON `[0,4,1]` and `[0,4,2]` | V dec |

### RGB

| Address | Role | Tag |
|---|---|---|
| `0x0801009C` | `rgb_matrix_task` (state byte `0x20000AC3`: 0 start, 1 render, 2 flush, 3 sync); indicators run after render | V dec |
| **`0x080102C4`** | **`rgb_task_render(effect)`. Hook B:** its prologue `push.w {r4-r8,lr}` is position-independent, so it can move to a trampoline that skips the effect while SignalRGB is active | V asm |
| `0x20000AC0` | RGB state block; `rgb_matrix_config` at `0x20000AD8` (enable:2, mode:6) | V dec |
| Effects | **42 slots** (0 = none, 1–41); the VIA handler clamps writes to `< 0x2A`. VIA's 45-entry list and the Console's lists don't match the build. Slot names not mapped; SignalRGB doesn't need them. | V dec |
| `0x0800FFE8` | `rgb_matrix_set_color(i,r,g,b)` → driver table `0x08018C7C` | V asm |
| `0x080128D0` | Driver `set_color`: scales to 1/5 or 6/10 (a firmware brightness/current cap), writes GRB to `0x20006D20` (86×3) | V asm |
| `0x08018B1A` | `g_led_config`: 6×16 `matrix_co`, 86 points, all flags `0x04`; ends exactly at the driver table | V data (`tools/ledconfig.py`) |
| `0x08013400` | `timer_read32` (for the SignalRGB timeout) | V dec |

### Keymap and bootloader entry

| Address | Role | Tag |
|---|---|---|
| `0x08017A06` | Default keymap `[layer][8][17]` (lookup at `0x08007CD0`) | V asm |
| QK_BOOT | Layer 2, row 2, col 4 (R). MO(1) is the Fn key; MO(2) sits on Right Shift in layer 1. | V data |
| `0x0800E408` | QK_BOOT handler: tap = reboot, **hold 3 s = ROM DFU** | V dec |
| `0x08007CF4` | `bootloader_jump` (sets a backup-register flag, then resets) | V asm |
| Boot combo | **Space + B held during the ~0.9 s boot scan → ROM DFU.** Keys follow your layer-0 keymap. | V asm |

## 6. Console (WebHID) command set

Filter: `usagePage 0xFF60, usage 0x61`, with no VID/PID filter. **V** src. First bytes the Console uses:

- **VIA:** `0x01`, `0x05`, `0x07` (ch 0/3), `0x09`, `0x0D`, `0x0F`, `0x10`, `0x15`
- **Vendor CRC frames:** `0x11` (info) and `0x1C` (LCD/time). Bytes 6–7 hold a CRC16-CCITT; the inner LCD packets `0x34`, `0x39`, `0x58`, `0xFB`–`0xFE` use an `A5 … xor` layout.

These match the firmware's VIA dispatcher and vendor table. **V** src+dec.

## 7. SignalRGB protocol (SRGBmods, verified from source)

| ID | Command | Reply |
|---|---|---|
| `0x21` | QMK version | `[0x21,a,b,c]` |
| `0x22` | Protocol version | `[0x22,1,0,6]` |
| `0x23` | Unique ID | `[0x23,0,0,x]` |
| `0x24` | Stream `[0x24,start,count<10,rgb…]` | None on success; `253`/`254` on error |
| `0x25`/`0x26` | Mode on/off | None |
| `0x27` | LED count | `[0x27,86]` |
| `0x28` | Firmware type | `[0x28,2]` |

**No collision:** `0x21`–`0x28` aren't used by VIA, the vendor table or the Console. Hooking at `0x0801240C`, ahead of the vendor CRC check, also avoids the rare case where a packet happens to look like a valid CRC frame. The plugin template selects `interface === 1`, which is the TIGA's raw HID interface. **V** src/asm

## 8. How images are validated

| Layer | Check | Effect on a patched hex | Tag |
|---|---|---|---|
| Intel HEX | Per-record checksums | Regenerated by intelhex | V |
| Wuque DFU tool | Reads an 8-byte model ID from the running keyboard (vendor cmd `0x11 0x06`, bytes at `0x08004400`) and requires those bytes to appear in the hex data | The patch doesn't touch `0x08004400` → passes | V src |
| Wuque DFU tool | Erase = necessary sectors, verify = on, jump = on, sLib off (`FormConnection.DownloadToDevice`) | Same erase scope as an official update | V src |
| ROM DFU bootloader | Standard Artery DFU; the tool sends no key or password in this flow | None expected | I |
| Firmware self-check | No CRC unit use and no loop over app flash | None found | V (absence) |
| Boot-time vendor check | Depends on the vendor region at `0x0801E000+` | Not tied to app contents: v1.0→v1.1 changes 53 of 54 sectors and still runs | I (strong) |

The patch is tied to this exact build, because v1.0 and v1.1 differ in 53 of 54 sectors. `build_patch.py` must refuse any input whose SHA-256 isn't `678ec144…`.

## 9. Recovery

Ways into ROM DFU, most convenient first:

1. **Wuque tool "Upgrade" with the keyboard running.** It sends vendor command `0x13 0x02` (enter DFU), then flashes. Needs USB HID working. **V** src+dec
2. **Hold QK_BOOT 3 s.** Default: Fn + Right Shift + R. Works at runtime, no reset needed. **V** dec/data
3. **Reset + hold Space+B.** The check runs in `keyboard_bootmagic_init`, before the USB HID task starts, so a SignalRGB bug can't block it. Unplugging USB isn't a reset (battery). Use a QK_BOOT tap, a watchdog reset, or disconnect the battery. **V** asm/data, plus MichiG21 for the battery
4. BOOT0 pin, if reachable on the PCB. Not checked. **I**

Which tool (checked 2026-10-20 after a Gate 0 attempt):
- **Use** `WuqueDFUupgradeProgrammer.exe` from `DFU.zip` (copy in `downloads/dfu_tool/extracted/WuqueDFUupgradeProgrammer/`). It lists any VID `0x1EA7` device on interface `mi_01`, which includes the TIGA (`1EA7:CEDD`). **V** src
- **Don't use** `Wuque_DFU_TOOL.exe` (window title "UBEST_DFU_TOOL", June 2025). It updates WCH CH582/CH585 wireless parts and only lists VID `0x6D66` devices, such as the TIGA's 2.4G receiver (`6D66:8888`). Its model-ID check is what printed "Illegal firmware!" for the keyboard hex; nothing was written. **V** src + Windows device list

To reflash:
- **Keep the Wuque tool open** after flashing a test build. It only accepts a file after it has read the model ID from the keyboard in normal mode during the same session. If a build ends up stuck in DFU, the same session can still flash the original hex. **V** src
- If a fresh session can't see the keyboard in normal mode, Artery's generic ISP Programmer (same DFU library, no model check) is the fallback. Select "erase necessary" only. **I**
- Never erase globally. **V** (consequence follows from section 3)

## 10. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Vendor region erased (unrecoverable) | Low if rules are followed | Image ≤ `0x0801AFFF`; necessary-erase flow only; Gate 0 rehearsal |
| Patch bug crashes or hangs the HID/RGB task | Medium during development | Watchdog reset; boot path untouched; QK_BOOT / Space+B; disassemble-and-diff self-check |
| Wrong base firmware | Low | SHA-256 check in `build_patch.py` |
| Tearing (HID task writes the buffer while the RGB task flushes) | Medium, cosmetic | Inspect the flush path in Phase 2; acceptable if only a frame is mixed |
| Colors capped at 20%/60% by the driver | Certain | Intended: keeps the firmware's power limit |
| SignalRGB + VIA/Console open together | Medium | Same interface: close the others (as your rules already say) |
| Connection-mode flag not located yet | n/a | Phase 2 task; a 2–3 s stream timeout already covers BT/2.4G, unplug and SignalRGB exit |

## 11. Open items (not resolved in Phase 1)

- Names of the 41 compiled effects (not needed).
- Which RAM flag means "USB mode" (for an explicit wireless fallback).
- Whether IF1 stays enumerated in BT mode with the cable plugged in.
- How the LED driver's flush reads the frame buffer (DMA timing).
- Physical chip contents of `0x0801B000–0x0801DFFF`; avoided by design.
- BOOT0 accessibility.

## 12. Phase 2 sketch (for approval, not started)

- **Hook A:** `0x08014D42` → `srgb_rx(data,len)`. Handles `0x21`–`0x28` and returns 1; anything else returns 0, so stock behavior is unchanged.
- **Hook B:** at the `0x080102C4` entry, a trampoline. If SignalRGB is active and fresh, it sets the state to FLUSHING and returns; otherwise it runs the original prologue and continues. On disable or timeout it forces an effect re-init.
- **State:** 8 bytes via the ZI extension (§3).
- **Build:** C compiled with `arm-none-eabi-gcc -mthumb -mcpu=cortex-m4 -Os`, linked at `0x0801A828` against the addresses above. `build_patch.py` takes the original hex and checks its SHA-256, writes `patched.hex` plus a diff report, and disassembles the changed bytes for review.
- **Needs:** the Arm GNU Toolchain (a few hundred MB). I'll ask before installing it.

## Tools added or changed this session

- `tools/dis.py`: capstone Thumb-2 disassembler with literal-pool resolution.
- `tools/ledconfig.py`: fixed parsing of the `--json` argument.
- `analysis/dfu_decomp/`: decompiled DFU tool (local reference only).
