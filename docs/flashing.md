# Flashing, safety and recovery

Everything you need to put the patched firmware on a Zoom75 TIGA, check it, and go back to
stock. Read the safety section first.

- [Safety: what must never happen](#safety-what-must-never-happen)
- [1. Get the official firmware](#1-get-the-official-firmware)
- [2. Make the patched firmware](#2-make-the-patched-firmware)
- [3. Flash it](#3-flash-it)
- [4. Check the keyboard](#4-check-the-keyboard)
- [Going back to stock](#going-back-to-stock)
- [Recovery](#recovery)

## Safety: what must never happen

The chip (an Artery AT32F415) holds a **vendor-programmed region at `0x0801E000–0x0801EFFF`**
that is in no hex file. The firmware will not start without it, and nothing published can
restore it. **It must never be erased.** These four conditions keep it safe:

1. **The patched image stays inside sectors 0–53 (ends at or below `0x0801AFFF`).** Those are
   the same 2 KB sectors the official hex covers, so the update erases nothing an official
   update would not. Patch v1 ends at `0x0801AA07` and differs from the original in sectors
   32, 41, 53; patch v2 ends at `0x0801AC17` and differs in sectors 32, 40, 41, 53.
2. **Flash only with the Wuque tool's normal upgrade flow.** It always uses "erase necessary
   sectors". **Never** use global or mass erase, option bytes, read protection or sLib, in
   this tool or any other.
3. **Reflash the official v1.1 hex once first** with the Wuque tool, before any patched
   build. That proves the update and recovery path on your unit.
4. **The patch does not touch the boot path** (reset handler, scatter-load,
   `keyboard_bootmagic_init`), so the recovery key combinations work exactly as on stock.

The scripts refuse any input whose SHA-256 is not the official v1.1 build, because the patch
addresses only fit that exact build. The full analysis behind these rules:
[technical/feasibility.md](technical/feasibility.md).

## 1. Get the official firmware

Download the official firmware **v1.1** for the Zoom75 TIGA from Meletrix. It is linked from
the [Wuque Studio / Meletrix Resource Library](https://wuque-studio.notion.site/Wuque-Studio-Meletrix-Resource-Library-e1aec540606e483f898779c25239be40).

| | |
|---|---|
| File | `WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex` (the download may carry a suffix such as `_5a1b53e5-407a-4b81-9e2c-9a98413384b5`; same content) |
| SHA-256 | `678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b` |
| Direct link used here | `https://cdn.shopify.com/s/files/1/0607/2474/0287/files/WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412_5a1b53e5-407a-4b81-9e2c-9a98413384b5.hex?v=1753061237` |

You also need Meletrix's flashing tool, **`WuqueDFUupgradeProgrammer.exe`**, from their
`DFU.zip` (same resource library). **Not** `Wuque_DFU_TOOL.exe`: that one updates the 2.4G
receiver (`6D66:8888`), and it prints "Illegal firmware!" for keyboard files. The receiver
needs no change and must not be modified.

## 2. Make the patched firmware

With Python 3 (nothing else needed), in a clone of this repository:

```bash
python apply_patch_v2.py WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex
```

It checks the input's SHA-256 and the original bytes at the four hook sites, adds the
prebuilt patch code from `prebuilt/` (compiled from `patch_v2/`, its SHA-256 checked too),
rewrites four call instructions, and writes `patched_v2.hex` **only if** the result is
exactly the build that was verified offline and on a keyboard:

| | SHA-256 |
|---|---|
| `patched_v2.hex` (patch v2: USB + 2.4G) | `1f1f1c469d437d6d8626d9f667e32f0788b4798807b25c71fe8524f4772fe59d` |

To build it from source instead (needs the Arm GNU Toolchain): [development.md](development.md).

## 3. Flash it

1. Close VIA, the Meletrix Console (browser tab) and SignalRGB.
2. Plug the keyboard in by USB, switched to USB mode.
3. Check the file's SHA-256 (above).
4. In the Wuque tool, use the normal upgrade steps: let it find the keyboard in normal mode,
   choose the hex, start the download. The tool reads the keyboard's model ID, switches it to
   DFU itself (vendor command `0x13 0x02`), erases only the necessary sectors, writes,
   verifies and restarts it.
5. **Keep the tool open** until you have checked the keyboard (next section). It only accepts
   a file after it has read the model ID from the keyboard in normal mode in the same
   session; if a build ever got stuck in DFU, the same open session could still flash the
   official hex.
6. **Then close the tool before using VIA, the Console, SignalRGB or the test script.** While
   it is open it holds the keyboard's raw HID interface **exclusively** (share mode 0), so
   nothing else can open interface 1. Windows still lists the device as working; programs just
   get a sharing violation. Closing the tool is safe once the keyboard runs normally: a new
   session can read the model ID again.

## 4. Check the keyboard

- Typing, knob, LCD, Bluetooth and 2.4G behave normally. Until SignalRGB starts streaming, the
  patch does nothing visible.
- Install the SignalRGB plugins: [signalrgb.md](signalrgb.md).

## Going back to stock

Flash the official hex the same way. Nothing else to undo: the patch keeps no settings.

## Recovery

Ways into the ROM bootloader (Artery DFU `2E3C:DF11`), most convenient first:

1. **Wuque tool upgrade with the keyboard running.** The tool sends the enter-DFU command
   itself. Needs USB HID working.
2. **Hold QK_BOOT for 3 s:** Fn + Right Shift + R by default (Fn → layer 1, Right Shift →
   layer 2, R = QK_BOOT). Works at runtime. A short tap only reboots.
3. **Reset while holding Space + B.** The check runs in `keyboard_bootmagic_init`, before the
   USB HID task starts, so a SignalRGB problem can't block it. Unplugging USB is **not** a
   reset (the battery keeps it running): use a QK_BOOT tap, a watchdog reset, or disconnect
   the battery. The keys follow your layer-0 keymap.
4. The BOOT0 pin on the PCB, if reachable (not checked).

If a fresh Wuque session can't see the keyboard in normal mode, Artery's generic ISP
Programmer (same DFU library, no model check) is the fallback, with "erase necessary" only
(inferred, not tried). **Never erase globally.**
