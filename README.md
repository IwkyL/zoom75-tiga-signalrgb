# Zoom75 TIGA: SignalRGB per-key lighting on the stock firmware

> **Unofficial community patch, not affiliated with Meletrix or Wuque Studio.** Flashing
> modified firmware is at your own risk. Read [section 1](#1-safety-summary-read-before-flashing)
> and [section 3](#3-flashing) before you flash anything.
>
> If this project helped you, you can support it here:
> **[buymeacoffee.com/Iwky](https://buymeacoffee.com/Iwky)**

## Quick start

1. **Get the official firmware v1.1** for the Zoom75 TIGA from Meletrix (linked from the
   [Wuque Studio / Meletrix Resource Library](https://wuque-studio.notion.site/Wuque-Studio-Meletrix-Resource-Library-e1aec540606e483f898779c25239be40)):
   `WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex`, SHA-256
   `678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b`. This repository does
   not contain Meletrix's firmware, only the patch. The file used here came from Meletrix's
   CDN (`original/SHA256SUMS.txt` has the exact URL; the downloaded name ends in
   `_5a1b53e5-…`, same content, so the SHA-256 matches).
2. **Make the patched firmware** (needs only Python 3, no compiler):

   ```bash
   python apply_patch_v2.py WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex
   ```

   It refuses any other input and writes `patched_v2.hex` only if the result is exactly the
   tested build, SHA-256 `1f1f1c469d437d6d8626d9f667e32f0788b4798807b25c71fe8524f4772fe59d`.
3. **Flash it** with Meletrix's WuqueDFUupgradeProgrammer, normal upgrade (section 3). To go
   back, flash the official hex the same way.
4. **Install both SignalRGB plugins** from `plugin/` (section 5). For 2.4G: section 5b.

This project adds SignalRGB per-key control to a **Meletrix Zoom75 TIGA** by patching its
**official firmware v1.1** (`WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex`).
Nothing else changes: VIA, the Meletrix Console, Bluetooth, 2.4G, the LCD and the knob work
as before. SignalRGB works **over the USB cable and through the 2.4G receiver** (patch v2,
section 5b; the receiver carries about one packet per 80–100 ms, so 2.4G suits static and
slow scenes). Not over Bluetooth.

How it works, in one paragraph: the firmware is a vendor build of QMK on an Artery AT32F415.
Two `bl` instructions are redirected to 480 bytes of new code in the unused tail of flash
sector 53. One hook sees every raw HID packet first and handles only the SignalRGB commands
`0x21`–`0x28` (SRGBmods QMK protocol 1.0.6). Everything else goes on to the vendor and VIA
handlers unchanged. The other hook skips the normal lighting effect while SignalRGB is
streaming. The keyboard falls back to its own effect 2 s after the last packet, and at once
when it is unplugged, suspended, switched to wireless or told `0x26`.

**Patch v2** (`out_v2/patched_v2.hex`, flashed 2026-10-21) keeps those 480 bytes and both
hooks byte-identical and appends 528 bytes: two more hooks on the radio path (the keyboard's
`+DATA:` handler, which feeds 2.4G raw HID to VIA) answer `0x21`–`0x28` in 2.4G mode and can
suppress the reply line ("quiet", `25 01`), and the render hook picks the link that may
render. On USB it behaves exactly like v1.

Details: [FEASIBILITY.md](FEASIBILITY.md) (Phase 1 analysis, safety conditions, recovery),
[PHASE2_REPORT.md](PHASE2_REPORT.md) (the v1 patch, protocol, fallback rules, limits),
[PATCH_V2_DESIGN.md](PATCH_V2_DESIGN.md) and
[out_v2/OFFLINE_VERIFICATION.md](out_v2/OFFLINE_VERIFICATION.md) (v2),
[V2_STEP6_RESULTS.md](V2_STEP6_RESULTS.md) (2.4G throughput and defaults),
[V2_STEP7_RESULTS.md](V2_STEP7_RESULTS.md) (power and sleep).

| File | What |
|---|---|
| `apply_patch_v2.py`, `prebuilt/` | **Makes patch v2 from the official hex** without a compiler: the prebuilt patch code (our own, compiled from `patch_v2/`) plus the four hook instructions |
| `original/` | The official v1.1 hex (not in the repository: Meletrix's file; download it, see Quick start). SHA-256 in `original/SHA256SUMS.txt` |
| `out_v2/patched_v2.hex` | **Patch v2: USB + 2.4G**, SHA-256 `1f1f1c469d437d6d8626d9f667e32f0788b4798807b25c71fe8524f4772fe59d` (build output, not in the repository: make it with `apply_patch_v2.py` or `build_patch_v2.py`) |
| `out/patched.hex` | Patch v1 (USB only), SHA-256 `c93031edfbe3807372b5014b689165278e532de6e4f563526b0f0c6f850e6a79` (build output, not in the repository: `build_patch.py`) |
| `patch/`, `build_patch.py` | v1 source and build script (frozen) |
| `patch_v2/`, `build_patch_v2.py` | v2 source (v1 files copied unchanged + `srgb_radio.c`) and build script |
| `plugin/Meletrix_Zoom75_TIGA_SignalRGB.js` | **SignalRGB plugin, USB cable** (`1EA7:CEDD`) |
| `plugin/Meletrix_Zoom75_TIGA_24G_SignalRGB.js` | **SignalRGB plugin, 2.4G receiver** (`6D66:8888`) |
| `plugin/src/tiga_plugin.src.js`, `tools/gen_plugins.py` | The one source both plugin files are generated from, and the generator |
| `plugin/tiga_layout.json` | LED table the plugins were generated from |
| `tools/srgb_hidtest.py` | **HID test script** for the real keyboard, on the cable or through the receiver |
| `tools/selfcheck.py`, `tools/emutest.py`, `tools/selfcheck_v2.py`, `tools/emutest_v2.py` | Static and emulator checks of v1 and v2 |
| `tools/gen_tiga_layout.py`, `tools/plugintest.mjs`, `tools/plugin_bridge.mjs`, `tools/hostsim.py` | Offline checks of the plugins and the test script |
| `tools/run_offline_suite.sh` | Every offline check in one run (section 7) |

---

## 1. Safety summary (read before flashing)

The chip holds a **vendor-programmed region at `0x0801E000–0x0801EFFF`** that is in no hex
file. The firmware will not start without it, and nothing published can restore it.
**Never erase it.** These four conditions keep it safe:

1. **The patched image stays inside sectors 0–53 (ends at or below `0x0801AFFF`).** Those
   are the same 2 KB sectors the official hex covers, so the update erases nothing an
   official update would not. v1 ends at `0x0801AA07` and differs from the original in
   sectors 32, 41, 53; v2 ends at `0x0801AC17` and differs in sectors 32, 40, 41, 53.
2. **Flash only with the Wuque tool's normal upgrade flow.** It always uses "erase necessary
   sectors". **Never** use global or mass erase, option bytes, read protection or sLib, in
   this tool or any other.
3. **Gate 0:** reflash the official v1.1 hex once with the Wuque tool before any patched
   build. That proves the update and recovery path on your unit. (Done 2026-10-20.)
4. **The patch does not touch the boot path** (reset handler, scatter-load,
   `keyboard_bootmagic_init`), so the recovery key combo works exactly as on stock.

`build_patch.py` and `build_patch_v2.py` refuse any input whose SHA-256 is not the v1.1
build (`678ec144…`), because the patch addresses only fit that exact build.

## 2. Build and verify

Run from the project root (Git Bash). Python is the project venv; always use `-I`.

Check the input and the result:

```bash
sha256sum original/*.hex out/patched.hex
```

Static self-check (independent of the build script): byte diff, protected ranges, full
disassembly of the new code, allowed calls and RAM writes:

```bash
.venv/Scripts/python.exe -I tools/selfcheck.py
```

Emulator test: runs the real patch bytes from `out/patched.hex` in Unicorn (3,909 checks):

```bash
.venv/Scripts/python.exe -I tools/emutest.py
```

Rebuild (only if you mean to; needs `.toolchain/arm-gnu-15.2/`). Building into a separate
folder leaves `out/patched.hex` alone; the build is reproducible, so the new file's SHA-256
must equal `c93031ed…6a79`:

```bash
.venv/Scripts/python.exe -I build_patch.py --out out_rebuild
```

**Patch v2** (details and every result: `out_v2/OFFLINE_VERIFICATION.md`). The build rebuilds
the v1 blob first and requires its SHA-256 `b9ca4c09…`; a rebuild into a scratch folder must
reproduce `1f1f1c46…e59d`:

```bash
.venv/Scripts/python.exe -I build_patch_v2.py --out <scratch dir>
.venv/Scripts/python.exe -I tools/selfcheck_v2.py --negative
.venv/Scripts/python.exe -I tools/emutest_v2.py
```

## 3. Flashing

Use **`WuqueDFUupgradeProgrammer.exe`** from Meletrix's `DFU.zip`
(copy in `downloads/dfu_tool/extracted/WuqueDFUupgradeProgrammer/`). It lists VID `0x1EA7`
devices on interface `mi_01`, which includes the TIGA (`1EA7:CEDD`).
**Not** `Wuque_DFU_TOOL.exe`: that one updates the 2.4G receiver (`6D66:8888`) and prints
"Illegal firmware!" for keyboard files.

1. Close VIA, the Meletrix Console (browser tab) and SignalRGB.
2. Plug the keyboard in by USB, switched to USB mode.
3. Check the file's SHA-256: `1f1f1c46…e59d` for v2 (`out_v2/patched_v2.hex`, flashed
   now), `c93031ed…6a79` for v1 (`out/patched.hex`, the rollback) (section 2).
4. In the Wuque tool, use the same normal upgrade steps as for Gate 0: let it find the
   keyboard in normal mode, choose the hex, start the download. The tool reads the
   keyboard's model ID, switches it to DFU itself (vendor command `0x13 0x02`), erases only
   the necessary sectors, writes, verifies and restarts it.
5. **Keep the tool open** until you have checked the keyboard (next step). It only accepts
   a file after it has read the model ID from the keyboard in normal mode in the same
   session; if a build ever got stuck in DFU, the same open session could still flash the
   official hex.
6. Check that typing, knob, LCD, BT and 2.4G behave normally. Until SignalRGB sends `0x25`,
   the patch does nothing visible.
7. **Then close the tool before using VIA, the Console, SignalRGB or the test script.** While
   it is open it holds the keyboard's raw HID interface **exclusively** (it opens it with
   `CreateFile` share mode 0, `analysis/dfu_decomp/HidDevice/Hid.cs`), so nothing else can
   open interface 1. Windows still lists it as working; programs just get a sharing
   violation, and hidapi leaves it out of its device list. Closing the tool is safe once the
   keyboard runs normally: a new session can read the model ID again.

To go back to stock, flash the official hex from `original/` the same way.

## 4. Recovery (FEASIBILITY.md §9)

Ways into the ROM bootloader (Artery DFU `2E3C:DF11`), most convenient first:

1. **Wuque tool upgrade with the keyboard running.** The tool sends the enter-DFU command
   itself. Needs USB HID working.
2. **Hold QK_BOOT for 3 s:** Fn + Right Shift + R by default (Fn → layer 1, Right Shift →
   layer 2, R = QK_BOOT). Works at runtime. A short tap only reboots.
3. **Reset while holding Space + B.** The check runs in `keyboard_bootmagic_init`, before the
   USB HID task starts, so a SignalRGB problem can't block it. Unplugging USB is **not** a
   reset (the battery keeps it running): use a QK_BOOT tap, a watchdog reset, or disconnect
   the battery. The keys follow your layer-0 keymap.
4. BOOT0 pin on the PCB, if reachable (not checked).

If a fresh Wuque session can't see the keyboard in normal mode, Artery's generic ISP
Programmer (same DFU library, no model check) is the fallback, with "erase necessary"
only (inferred, not tried). **Never erase globally.**

## 5. SignalRGB plugin

There are two plugin files, one per USB device, because SignalRGB matches a plugin by
`VendorId()` / `ProductId()` and documents an array only for `ProductId()`:

| File | Device | Needs |
|---|---|---|
| `plugin/Meletrix_Zoom75_TIGA_SignalRGB.js` | `1EA7:CEDD`, the keyboard on the cable | v1 or v2 |
| `plugin/Meletrix_Zoom75_TIGA_24G_SignalRGB.js` | `6D66:8888`, the 2.4G receiver | v2 |

Both are **generated** from `plugin/src/tiga_plugin.src.js` by `tools/gen_plugins.py`, so
they can't drift apart. They differ only in the header (name, VID/PID, `const Radio`). Edit
the source, then regenerate (`--check` tells whether the files match the source):

```bash
.venv/Scripts/python.exe -I tools/gen_plugins.py
.venv/Scripts/python.exe -I tools/gen_plugins.py --check
```

The cable plugin (v1.1.0) sends exactly what v1.0.1 sent; `tools/plugintest.mjs` compares
every write, read and notification with the v1.0.1 copy in
`backups/Meletrix_Zoom75_TIGA_SignalRGB_v1.0.1_e6cbbf85.js`.

**Install:** copy both plugin files into the `WhirlwindFX\Plugins`
folder inside **your Documents folder**, then restart SignalRGB. The docs give
`%USERPROFILE%\Documents\WhirlwindFX\Plugins`, or `...\OneDrive\Documents\...` when OneDrive
redirects Documents. The folder name is localised, e.g.
`C:\Users\<you>\OneDrive\Documenti\WhirlwindFX\Plugins` on an Italian Windows. SignalRGB
creates it on first start.

(Source: docs.signalrgb.com, "How is a plugin loaded?" and "File locations". Plugins there
survive SignalRGB updates and override a built-in plugin with the same VID/PID. The
`AppData\Local\VortxEngine\app-<version>\Signal-x64\Plugins` folder is for built-in plugins
and is overwritten on every update.)

From PowerShell in the project folder. This asks Windows where Documents really is:

```powershell
Copy-Item plugin\Meletrix_Zoom75_TIGA_SignalRGB.js, plugin\Meletrix_Zoom75_TIGA_24G_SignalRGB.js -Destination (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'WhirlwindFX\Plugins')
```

Exit SignalRGB from its tray icon first (closing the window only hides it).

Close VIA and the Meletrix Console while SignalRGB runs: they share the raw HID interface
(SignalRGB's docs say the same for every QMK keyboard). Close the Wuque DFU tool too: it
locks that interface while open (section 3, step 7).

**What the cable plugin does:**

- Matches VID `0x1EA7` / PID `0xCEDD` and opens only interface 1, usage page `0xFF60`,
  usage `0x61`.
- Initialize: queries `0x28 0x21 0x22 0x23 0x27`, one reply each. It streams only if the
  keyboard reports protocol 1.0.6, 86 LEDs and firmware type 1 or 2. Otherwise it shows a
  notification and sends no colour data (e.g. on stock firmware, which answers `0xFF`). It
  retries the handshake every 5 s, 5 times, in case VIA held the interface at start.
  Then it sends `0x25`.
- Render: 86 LEDs as 10 `0x24` packets (9 LEDs each, the last one 5).
- Shutdown: `0x26`. If SignalRGB keeps rendering afterwards (e.g. after the PC resumes),
  the plugin sends `0x25` again by itself.
- Logging: handshake results, mode on/off and Shutdown go to SignalRGB's log file as lines
  starting with `[TIGA]`. With SignalRGB 2.5.80 that file is in
  `%LOCALAPPDATA%\WhirlwindFX\SignalRgb\Logs`. Nothing is logged per frame.
- Options: Lighting Mode (Canvas / Forced) and Forced Color. There is **no shutdown
  colour**: the keyboard returns to its own effect 2 s after the last packet anyway.
- Compared with the SRGBmods template it was built from
  (`downloads/srgbmods/qmk-plugins/Keychron_V1_Max_QMK_ANSI_Encoder_Keyboard.js`):
  no extra "VIA" read (the patch reports type 2 but sends exactly one reply), 33-byte
  writes throughout, `ImageUrl` instead of `Image`.

**What the 2.4G plugin does differently** (same LED table, options and logging):

- Matches `6D66:8888`, interface 1, `0xFF60` / `0x61`.
- Handshake: stale reports flushed, then `0x22`, `0x28`, `0x21`, `0x27`, one at a time, up to
  250 ms per reply, the next packet at least 150 ms after a query and 80 ms after its reply.
  `0x23` is not sent. The same gate as on the cable (protocol 1.0.6, 86 LEDs, type 1 or 2).
  Then `25 01`: SignalRGB mode on with patch v2's quiet switch, so colour packets get no reply.
- Render: at most **one** packet per frame, at least 80 ms after the previous packet (100 ms
  after `0x25`/`0x26`): a 9-LED group not sent for 3 s first, else the next group that
  changed (round robin), else, in a still scene, the group sent longest ago once 1.5 s old.
- `0x25` once per session, `0x26` once at Shutdown, no periodic `0x25`: `0x23 0x25 0x26 0x27`
  are each one flipped bit away from a VIA write (V2_STEP6_RESULTS.md §3).
- Replies are read every frame:
  - `FF` + a copy of a packet = VIA got it: the keyboard is **not in 2.4G mode** (cable plugged
    in, BT, or unplugged without Fn+V). The plugin stops, shows a notice, and retries with one
    `0x22` every 5 s (five times), then every 10 s, until the handshake passes again.
  - an exact echo of a colour packet = the keyboard lost its radio state (reset): one `25 01`.
- A colour packet whose bytes 6–7 happen to form a valid vendor CRC is sent with one colour
  LSB flipped, so the receiver never sees a valid CRC-framed packet.

**LED table.** `tools/gen_tiga_layout.py` builds it from firmware data (the LED matrix in
`g_led_config` and the layer-0 keymap) plus the physical positions in the official VIA
definition. The canvas uses half-key cells (31 × 11), so no two LEDs share a cell. Names
follow SignalRGB's key-name list where one exists.

- **Why `matrix_co` row 1 skips LED 29:** `matrix_co[row][col]` is indexed by the
  *electrical* key matrix, while LED numbers follow the physical chain along each row. The
  2U Backspace switch (and the right half of a split Backspace) is wired to matrix row 3,
  column 13, a free column next to Enter. So LED 29 shows up in `matrix_co` row 3, though it
  sits in the number row between LED 28 and LED 30 (Del). The VIA definition says the same
  (`3,13` is the default Backspace; split Backspace uses `1,13` + `3,13`), and the keymap
  sends Backspace from both positions. All 86 LEDs are mapped exactly once.
- Five LEDs belong to switches that exist only in alternative layouts, so with the default
  keycaps they sit under a wider key: 28 (Backspace, split L), 61 (Left Shift, split R),
  77/79/80 (split Space and 7U Space).
- LEDs 13 and 14 are named after the keymap (`F13`, `Home`). Still to confirm with the walk.

Regenerate and re-check the table:

```bash
.venv/Scripts/python.exe -I tools/gen_tiga_layout.py --json plugin/tiga_layout.json
```

## 5b. SignalRGB over the 2.4G receiver

Needs patch v2 on the keyboard and both plugin files installed.

1. Unplug the cable, press **Fn+V** (2.4G mode). The receiver stays plugged into the PC.
2. **After plugging the cable in or out, press Fn+V again.** Plugging in switches the keyboard
   to USB mode, and unplugging does not switch it back: the radio link stays up, but the
   keyboard hands every packet to VIA. The 2.4G plugin then pauses and shows a notice; after
   Fn+V it picks up by itself within about 10 s. If it doesn't, press Fn+V once more (on
   2026-10-21 the first Fn+V after an unplug did not take, the second did).
3. Start SignalRGB. Its log (`[TIGA]` lines) shows the handshake and `Mode on, quiet (0x25 01)`.

With the cable plugged in while SignalRGB runs, the cable plugin takes over (smooth, about
50 frames/s) and the 2.4G plugin pauses.

**What to expect on 2.4G** (hardware, 2026-10-21):

- About **one packet per 80–100 ms** gets through the receiver, a tenth of a frame. A single
  key change shows within about 0.2 s, a full-board change sweeps across in about 0.8–1 s, a
  still scene is refreshed every 1.5 s. **Best for static and slow scenes**; moving effects
  follow in visible steps.
- Typing while SignalRGB streams is normal.
- On battery the keyboard caps the LEDs at 20 % (60 % with the cable), as for its own effects.
- When SignalRGB stops (exit, crash, PC sleep) the keyboard shows its own effect again within
  2 s, also without `0x26`.
- **Sleep:** on battery the keyboard sleeps (LEDs and LCD off) about 11–13 min after the last
  key press **when nothing is streaming**; SignalRGB's packets don't wake it, a key press does,
  and the 2.4G link is back about 20 s later. **While SignalRGB streams, it does not sleep**,
  so it uses battery the whole time SignalRGB runs. Use the cable or close SignalRGB to save
  battery. Details: V2_STEP7_RESULTS.md.

## 6. HID test script

`tools/srgb_hidtest.py` (needs `hidapi`, already in `.venv`). It opens only `1EA7:CEDD`
interface 1 (`0xFF60`/`0x61`) and can only send SignalRGB commands. A guard in the one send
function refuses anything else. `0x24`–`0x26` are refused unless `--led` is given. Every
packet sent and received is printed and saved under `logs/`.

**Before every run: close VIA, the Meletrix Console, SignalRGB and the Wuque DFU tool.**
If `--list` shows interfaces 0 and 2 but not 1, something holds interface 1 exclusively
(section 3, step 7).

List the keyboard's HID interfaces. Sends no reports (Windows reads the device strings):

```bash
.venv/Scripts/python.exe -I tools/srgb_hidtest.py --list
```

Read-only (default): `0x21 0x22 0x23 0x27 0x28`. Each reply must match PHASE2_REPORT §1
byte for byte, with exactly one reply per query:

```bash
.venv/Scripts/python.exe -I tools/srgb_hidtest.py
```

LED tests. They run only if the read-only checks pass, and print what you should see at
each step: `0x26` first (known state); bad `0x24` packets → error replies 253/254; red while
the mode is off (must be ignored); `0x25`; solid red, green and blue; a 5 s rainbow at full
speed (frame rate, tearing); the LED walk 0–85; the timeout check (magenta, stop, normal
effect after about 2 s, then cyan resumes without `0x25`); `0x26`:

```bash
.venv/Scripts/python.exe -I tools/srgb_hidtest.py --led
```

Only some steps, e.g. a slower walk of the number row:

```bash
.venv/Scripts/python.exe -I tools/srgb_hidtest.py --led --steps walk --walk-leds 15-30 --walk-delay 2
```

A 90 s orange/purple stream for testing by hand: switch to BT or 2.4G and back, or unplug:

```bash
.venv/Scripts/python.exe -I tools/srgb_hidtest.py --led --steps hold --hold-seconds 90
```

Exit code 0 means all automatic checks passed. What the LEDs do is for you to watch.

**Through the 2.4G receiver** (`6D66:8888`; keyboard in 2.4G mode, cable unplugged). The
script accepts only the keyboard and this receiver as `--vid/--pid`, matches the receiver's
raw HID by usage page `0xFF60` / usage `0x61` (exactly one match, interface 1), and opens it
only if Windows reports 32-byte reports with report ID 0. On the receiver `--led` needs patch
v2 and a gap: the link drops packets sent closer than about 75–100 ms. Use `--packet-gap 100`
(or `--quiet --packet-gap 80`). The receiver-only steps (`--led --steps …`, see the script's
docstring): `pace` (loss per gap), `soak` (a long run, every reply checked for damage),
`partial` (the plugin's pacing, phases to watch), `idle` (step 7: stream, silence, then
`0x22` probes: does the keyboard sleep?). `soak`, `partial` and `idle` refuse gaps below
75 ms. List only (sends nothing):

```bash
.venv/Scripts/python.exe -I tools/srgb_hidtest.py --vid 0x6D66 --pid 0x8888 --list
```

Probe: VIA `0x01` (get protocol version, read-only) 20 times for the round-trip time, then
`0x21 0x22 0x27 0x28`, each between two `0x01`, and a verdict per ID. This flag is the only
way the script sends `0x01`:

```bash
.venv/Scripts/python.exe -I tools/srgb_hidtest.py --vid 0x6D66 --pid 0x8888 --via-probe
```

Read-only through the receiver, then the full LED test at the 2.4G defaults:

```bash
.venv/Scripts/python.exe -I tools/srgb_hidtest.py --vid 0x6D66 --pid 0x8888
.venv/Scripts/python.exe -I tools/srgb_hidtest.py --vid 0x6D66 --pid 0x8888 --led --quiet --packet-gap 80
```

## 7. Offline checks (no keyboard needed)

`node tools/plugintest.mjs` and `python tools/gen_plugins.py --check` run on a fresh clone.
Everything else runs the real firmware bytes in an emulator and needs files that are not in
the repository: the official hex in `original/`, the builds in `out/` and `out_v2/`
(`build_patch.py` / `build_patch_v2.py`, Arm GNU Toolchain 15.2.rel1 in
`.toolchain/arm-gnu-15.2/`), and the flat image and a few extracts of Meletrix's tools in
`analysis/`.

**Everything at once** (about 12 minutes, real-time; never alongside a hardware run). Every
line of `summary.txt` must end in PASS and the frozen files must be unchanged:

```bash
bash tools/run_offline_suite.sh <scratch dir outside the project>
```

The single checks:

Plugins in Node with a mock `device`. Cable: the patched, stock and "no reply at first"
scenarios, and every write identical to v1.0.1. 2.4G: a mock receiver with a fake clock
(handshake, pacing, loss, CRC nudge, leaving 2.4G mode and Fn+V, radio state reset, no
reply). It records the packets of both:

```bash
node tools/plugintest.mjs
```

Replay those packets through the real patch code in the emulator:

```bash
.venv/Scripts/python.exe -I tools/hostsim.py plugin logs/plugin_packets.json
.venv/Scripts/python.exe -I tools/hostsim.py plugin logs/plugin_packets_rx.json
```

The 2.4G plugin itself, in real time, against patch v2 behind the fake receiver
(`tools/plugin_bridge.mjs`), with and without packet loss:

```bash
.venv/Scripts/python.exe -I tools/hostsim.py plugin2g
```

The test script against the emulated patch, and against a fake stock firmware:

```bash
.venv/Scripts/python.exe -I tools/hostsim.py hidtest --led --walk-delay 0.3
```

```bash
.venv/Scripts/python.exe -I tools/hostsim.py stock --led
```

The receiver options against a fake receiver, whose keyboard side runs the real v1 radio
path (`+DATA:` handler `0x080140AC` and VIA) in the emulator, plus the refusal cases:

```bash
.venv/Scripts/python.exe -I tools/hostsim.py radio
```

## 8. Known limits

From PHASE2_REPORT.md §5:

- **No shutdown colour.** 2 s after the last packet the keyboard shows its own effect.
- **PC sleep / USB suspend** falls back at once. SignalRGB picks up again when it streams.
- **Brightness is capped by the firmware**: 60 % on USB power (20 % on battery). Intended:
  it keeps the vendor's current limit.
- **Vendor indicator LEDs** (caps lock, mode and so on) are still drawn over SignalRGB.
- **Soft reset while streaming** (inferred): RAM survives a soft reset. If the keyboard
  resets in its first 2 s of uptime while SignalRGB is on, the LEDs could stay dark for at
  most the rest of that window.
- **No SignalRGB over Bluetooth.** In BT mode the keyboard ignores streamed colours.
- **2.4G is slow:** about one packet per 80–100 ms through the receiver (section 5b). Faster
  sending loses packets, and packets sent too close together arrived damaged on 2026-10-21.
  There is no checksum on the radio path, so the plugins keep the measured spacing.
- **2.4G needs Fn+V after every cable plug or unplug** (section 5b).
- **No sleep while SignalRGB streams over 2.4G** (section 5b): the battery runs as if you were
  typing with the LEDs on.

Found in Phase 3:

- The firmware's own `g_led_config` coordinates for the Shift row are about 0.9 key widths
  right of the physical keys in the VIA layout. The other rows agree within 0.25. The
  plugin uses the VIA positions. This only affects where SignalRGB samples the canvas.
- **Frame rate is capped at about 50 frames/s.** The keyboard accepts one raw HID packet
  every 2.00 ms (median, measured), and a full frame is 10 packets. Inferred cause: the
  stock firmware's USB task cadence. The patch sends no reply to colour packets, so it adds
  no delay.

## 9. Hardware results, patch v1 (2026-10-20)

Two identical `--led` runs (`logs/srgb_hidtest_20261020_203807_led.log`,
`logs/srgb_hidtest_20261020_204450_led.log`), plus one read-only run before them.

| Check | Result | How |
|---|---|---|
| Query replies `0x21 0x22 0x23 0x27 0x28` | Byte-exact, one reply each, 2–3 ms | Script, 3 runs |
| Error replies (253 / 254), valid packet with mode off → no reply | As specified | Script |
| Only `0x21`–`0x28` sent, never `0x13` | 5,512 and 5,552 packets | Log |
| Stream while mode off is ignored | Yes | You watched |
| Solid red / green / blue | Whole board | You watched |
| Frame rate | 49.0 and 49.5 frames/s (2.00 ms per packet) | Script |
| Tearing | None seen | You watched the rainbow |
| LED walk 0–85 | Every row in the expected order | You watched |
| Fallback 2 s after the last packet | Yes | You watched |
| Resume without a new `0x25` | Cyan at once | You watched |
| `0x26` → normal effect | At once | You watched |
| Wuque tool open → interface 1 locked | Windows error 32; interface back once closed | `--list` before and after |
| BT mode, cable plugged in: raw HID still enumerated | Yes, all USB interfaces present and OK | `--list` + Windows device list |
| BT and 2.4G mode, cable plugged in: USB link stays up | Yes, every write accepted, 2 ms per packet throughout | Hold test log (`..._210156_led.log`) |
| Switch to BT / 2.4G while streaming | Normal effect; streamed colours again on USB, no new `0x25` | You watched the hold test |
| Unplug while streaming | Normal effect; script stops on the failed write | You watched + log |
| Typing and mode switching during the test | Normal | You watched |
| SignalRGB 2.5.80 loads the plugin, handshake passes, `0x25` | Yes (protocol 1.0.6, 86 LEDs, type VIA) | SignalRGB log `SignalRGB_20261020_210929.log` |
| SignalRGB effects on the keyboard | Work | You watched |
| SignalRGB exit sends `0x26` | Yes, then SignalRGB exits | SignalRGB log |
| VIA and the Console afterwards | Work | You checked |
| PC sleep (S3) with SignalRGB running | Plugin sends `0x26` ("system suspending: yes"); LEDs go dark for a moment, then the normal effect | SignalRGB log `SignalRGB_20261020_212122.log` + you watched |
| PC wake | SignalRGB re-starts the plugin: handshake, `0x25`, its colours return by themselves | SignalRGB log + you watched |

All the Phase 3 hardware questions are answered. On sleep, SignalRGB 2.5.80 re-starts the
plugin once more (handshake + `0x25`, no colours) about 3 s after Shutdown, just before the
PC sleeps. The keyboard then holds its last frame for at most 2 s before the timeout hands
the LEDs back. The 2 s timeout exists for exactly this. Separately, the PC could not connect to the keyboard over Bluetooth
while your phone could. Windows had no pairing stored. The patch can't reach the radio
(section 8), so a channel already bonded to the phone is the likely cause.

## 10. Hardware results, patch v2 and SignalRGB over 2.4G (2026-10-20/21)

Details: [V2_STEP6_RESULTS.md](V2_STEP6_RESULTS.md) and
[V2_STEP7_RESULTS.md](V2_STEP7_RESULTS.md). The test logs named below are not in the
repository.

| Check | Result | How |
|---|---|---|
| Wired after flashing v2: read-only, `--led`, SignalRGB | PASS, as with v1 | Script + you watched |
| Receiver read-only queries | Byte-exact, 38–94 ms round trip | Script |
| Receiver `--led`, packets back to back | Only the first packet of each burst arrives | Script + you |
| Smallest loss-free spacing | 100 ms with replies (quiet off), 75 ms with quiet on | `--steps pace` |
| Long runs at 100 ms (quiet off) / 80 ms (quiet on) | 3,000/3,000 exact echoes; no reply to 5,167 colour packets (no damage seen), 300/300 probes answered | `--steps soak` |
| Typing while streaming | Normal | You |
| Partial updates (the plugin's pacing) | Still scene stays, indicators clear, one key walks cleanly, full-board change in ~0.8 s | `--steps partial` + you |
| SignalRGB through the receiver (plugin 1.1.0) | Handshake, `25 01`; Forced red fills the board; red → blue within ~1 s; a wave follows in steps; typing normal | SignalRGB log `SignalRGB_20261021_134129.log` + you |
| Cable plugged in while SignalRGB runs | Cable plugin takes over, smooth; 2.4G plugin pauses on VIA's reply | Log + you |
| Unplug, Fn+V | 2.4G plugin resumes at its next retry (first Fn+V did not take, second did) | Log + you |
| Exit SignalRGB | `0x26`, own effect at once | Log + you |
| Stream stops without `0x26` | Own effect back after ~2 s | `--steps idle` lead-in + you |
| Idle on battery, 2.4G, nothing sent | Asleep (LEDs and LCD off) by ~13 min; 31 `0x22` probes over 2.5 min unanswered; Shift wakes it, radio back 16–21 s later | `--steps idle` (`…_135135_rx_idlequiet.log`) + you |
| 16 min stream at the plugin's pacing, untouched | Stays awake: colours alternating to the end, 12/12 probes answered | `--steps idle --idle-lead 960` (`…_145758_rx_idlequiet.log`) + you |
| Final plugin build (retry every 10 s) | Handshake; after cable in/out and Fn+V back within ~6 s | SignalRGB log `SignalRGB_20261021_151629.log` + you |
| Two plugin bugs found on hardware, fixed the same day | Queries 80 ms apart: the second arrived as VIA `FF` → now 150 ms; `Array.isArray` false for SignalRGB's read result → replies copied by index | SignalRGB logs `…_132851`, `…_133643` |

## 11. Regression checklist (after any firmware or plugin change)

Close VIA, the Meletrix Console, SignalRGB and both Wuque tools before every script run.

Offline:
- [ ] `bash tools/run_offline_suite.sh <scratch dir>`: every line PASS, frozen files unchanged.

Cable (USB mode):
- [ ] `srgb_hidtest.py` read-only, then `--led` (all steps PASS; watch colours, walk, timeout).
- [ ] SignalRGB on the cable: effects follow; exit → own effect at once; PC sleep and wake.
- [ ] VIA and the Meletrix Console connect and change a setting; then close them.
- [ ] LCD and knob.

2.4G (cable unplugged, Fn+V):
- [ ] `srgb_hidtest.py --vid 0x6D66 --pid 0x8888` read-only.
- [ ] SignalRGB through the receiver: Forced red fills the board; a slow effect follows; exit → own effect.
- [ ] Plug the cable in and out while SignalRGB runs: cable plugin takes over, then after Fn+V the 2.4G plugin resumes.
- [ ] VIA and the Console over 2.4G (they share the interface: close SignalRGB first).
- [ ] Typing over 2.4G, with and without SignalRGB.

Other:
- [ ] BT typing (phone).
- [ ] Mode keys (Fn+Z/X/C, Fn+V, Fn+Space) with SignalRGB running: own effect on BT; SignalRGB back on 2.4G or USB.

## License and credits

- This project's own code and documents: **GPL-2.0**, see [LICENSE](LICENSE). The SignalRGB
  plugins started from SRGBmods' plugin template
  ([SRGBmods/qmk-plugins](https://github.com/SRGBmods/qmk-plugins), GPL-2.0), and the firmware
  patch implements SRGBmods' QMK protocol 1.0.6.
- **Not included and not covered by this license:** Meletrix's firmware and tools. The patch
  is applied to the official firmware you download yourself.
- Meletrix, Zoom75 TIGA, Wuque Studio, SignalRGB and VIA are trademarks of their owners.
