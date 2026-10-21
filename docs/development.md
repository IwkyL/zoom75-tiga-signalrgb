# Development: building, testing, contributing

How the patch is built and verified, and how to test it on a keyboard. If you only want to
use the patch, [flashing.md](flashing.md) and [signalrgb.md](signalrgb.md) are enough.

- [Setup](#setup)
- [Repository layout](#repository-layout)
- [How the patch works](#how-the-patch-works)
- [Build and verify the firmware](#build-and-verify-the-firmware)
- [The SignalRGB plugins](#the-signalrgb-plugins)
- [HID test script (real keyboard)](#hid-test-script-real-keyboard)
- [Offline checks (no keyboard)](#offline-checks-no-keyboard)
- [Regression checklist](#regression-checklist)

## Setup

Windows, Git Bash. What the tools expect (paths relative to the repository root):

| What | Version used | Where |
|---|---|---|
| Python | 3.12, a venv with `intelhex`, `capstone`, `pefile`, `unicorn` 2.1.4, `hidapi` 0.15.0 | `.venv/` (run it with `-I`) |
| Node | 24 | on `PATH` (plugin tests) |
| Arm GNU Toolchain | 15.2.rel1 (the build pins its exact version string) | `.toolchain/arm-gnu-15.2/` |
| Ghidra (optional) | 12.1.4 with `tools/ghidra_scripts/` | for the analysis exports |

**Not in the repository** (Meletrix's files, or build output that contains them), which the
build and most checks need locally:

| Path | What |
|---|---|
| `original/WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex` | the official firmware ([flashing.md](flashing.md#1-get-the-official-firmware)) |
| `out/`, `out_v2/` | build output of `build_patch.py` / `build_patch_v2.py` |
| `analysis/tiga_v11.bin` | flat binary image of the official hex, `0x08000000`–`0x0801A827` |
| `analysis/ghidra/tiga_v11_p3/` | Ghidra exports (`tools/ghidra_scripts/ExportAll.java`) |

Never put `tools/` on `sys.path`: `tools/dis.py` shadows the standard library's `dis`. The
tools load each other by file path.

## Repository layout

```text
apply_patch_v2.py        official hex -> patched_v2.hex, no compiler (uses prebuilt/)
build_patch.py           builds patch v1 from patch/ (reproducible, pinned toolchain)
build_patch_v2.py        builds patch v2 from patch_v2/ (v1 blob must rebuild byte-identical)
patch/                   v1 source: srgb.c (USB hook + render hook), fw.h (firmware addresses), linker script
patch_v2/                v2 source: v1 files unchanged + srgb_radio.c / fw_radio.h (2.4G hooks)
prebuilt/                the compiled v1 and v2 patch code (our own), used by apply_patch_v2.py
plugin/                  the two SignalRGB plugins, their shared source (src/) and the LED table
tests/fixtures/          recorded plugin packets and the v1.0.1 plugin (test baselines)
tools/                   test, verification and analysis tools (below)
docs/                    guides and the engineering record (docs/README.md)
```

`tools/`, by purpose:

| Purpose | Files |
|---|---|
| Verify the patch | `selfcheck.py`, `selfcheck_v2.py` (static), `emutest.py`, `emutest_v2.py` (Unicorn emulator, real bytes) |
| Test on hardware | `srgb_hidtest.py` |
| Simulate hardware | `hostsim.py` (fake keyboard / receiver around the emulated patch), `plugin_bridge.mjs` |
| Plugins | `gen_plugins.py`, `plugintest.mjs`, `gen_tiga_layout.py` |
| Everything at once | `run_offline_suite.sh` |
| Analysis helpers | `dis.py`, `fn.py`, `hexinfo.py`, `bindiff.py`, `findbytes.py`, `words.py`, `strings_dump.py`, `gaps.py`, `ledconfig.py`, `usbdesc.py`, `vendortable.py`, `viajson.py`, `inittable.py`, `keil_rw_decompress.py`, `jsgrep.py`, `jsslice.py`, `page_text.py`, `ghidra_scripts/` |

## How the patch works

The stock firmware is a vendor build of QMK on an Artery AT32F415 (Cortex-M4). The patch
adds code in the unused tail of flash sector 53 and redirects four call instructions to it:

| Hook | Site | Purpose |
|---|---|---|
| A | `0x08014D42`, USB HID task | sees every USB raw HID packet first; handles `0x21`–`0x28`, passes everything else to the vendor and VIA handlers unchanged |
| B | `0x0801017C`, `rgb_matrix_task` | skips the normal effect while SignalRGB streams; the link that may render depends on the connection mode |
| C | `0x080140E2`, radio `+DATA:` handler | the same for 2.4G packets (they reach VIA directly, not hook A) |
| D | `0x0801413E`, radio reply | can suppress the reply line ("quiet", `25 01`) |

Patch v1 (USB only) is hooks A and B, 480 bytes. Patch v2 keeps those bytes and adds hooks C
and D plus a new render hook, 528 bytes more. The keyboard falls back to its own effect 2 s
after the last packet. Details: [technical/patch-v1.md](technical/patch-v1.md) and
[technical/patch-v2-design.md](technical/patch-v2-design.md).

## Build and verify the firmware

Patch v1 (the build is reproducible; a build into a new folder must give `c93031ed…6a79`):

```bash
.venv/Scripts/python.exe -I build_patch.py --out out_rebuild
.venv/Scripts/python.exe -I tools/selfcheck.py
.venv/Scripts/python.exe -I tools/emutest.py
```

Patch v2 (it rebuilds the v1 blob first and requires its SHA-256 `b9ca4c09…`; a build into a
scratch folder must reproduce `1f1f1c46…e59d`):

```bash
.venv/Scripts/python.exe -I build_patch_v2.py --out <scratch dir>
.venv/Scripts/python.exe -I tools/selfcheck_v2.py --negative
.venv/Scripts/python.exe -I tools/emutest_v2.py
```

- `selfcheck*.py` (independent of the build script): byte diff against the original and v1,
  protected ranges, full disassembly of the new code, allowed calls, RAM writes and literals,
  plus negative tests.
- `emutest*.py`: the real patch bytes in Unicorn, 3,909 (v1) and 47,052 (v2) checks.
- What each check proved: [technical/patch-v2-verification.md](technical/patch-v2-verification.md).

If you change `patch_v2/`, rebuild, copy the new blobs from the build output into `prebuilt/`,
and update the SHA-256 values and hook targets in `apply_patch_v2.py`.

## The SignalRGB plugins

Edit only `plugin/src/tiga_plugin.src.js`, then regenerate both files (`--check` tells
whether they match the source):

```bash
.venv/Scripts/python.exe -I tools/gen_plugins.py
.venv/Scripts/python.exe -I tools/gen_plugins.py --check
```

The LED table (`plugin/tiga_layout.json`) is regenerated with
`tools/gen_tiga_layout.py --json plugin/tiga_layout.json`.

Two SignalRGB facts found on hardware (both covered by the tests now):

- `device.read` does not return a plain JavaScript Array (`Array.isArray()` is false); copy
  replies by index before using array methods.
- Over 2.4G, a packet sent less than about 60 ms after a reply arrived can arrive damaged;
  the 2.4G plugin waits 150 ms after a query and 80 ms after any reply.

## HID test script (real keyboard)

`tools/srgb_hidtest.py` opens only the keyboard (`1EA7:CEDD`) or the receiver (`6D66:8888`),
interface 1 (`0xFF60`/`0x61`), and can only send SignalRGB commands: a guard in the one send
function refuses anything else, `0x24`–`0x26` only with `--led`, VIA `0x01` only with
`--via-probe`, and never a packet that forms a valid vendor-command checksum. Every packet sent
and received is printed and saved under `logs/`.

**Before every run: close VIA, the Meletrix Console, SignalRGB and the Wuque tools.** If
`--list` shows interfaces 0 and 2 but not 1, something holds interface 1 exclusively.

| Command | What it does |
|---|---|
| `srgb_hidtest.py --list` | lists the HID interfaces, sends nothing |
| `srgb_hidtest.py` | read-only queries `0x21 0x22 0x23 0x27 0x28`, each reply checked byte for byte |
| `srgb_hidtest.py --led` | LED tests (after the read-only checks pass): error replies, red while mode is off, solid colours, a 5 s rainbow at full speed, the LED walk 0–85, the 2 s timeout; the script says what you should see |
| `srgb_hidtest.py --led --steps walk --walk-leds 15-30 --walk-delay 2` | only some steps |
| `srgb_hidtest.py --led --steps hold --hold-seconds 90` | a stream for testing mode switches by hand |

(Run them as `.venv/Scripts/python.exe -I tools/srgb_hidtest.py …`.)

Through the receiver add `--vid 0x6D66 --pid 0x8888` (keyboard in 2.4G mode, cable
unplugged). The receiver drops packets sent closer than about 75–100 ms, so LED tests need a
gap: `--packet-gap 100`, or `--quiet --packet-gap 80`. Receiver-only steps (`--led --steps …`):
`pace` (loss per gap), `soak` (a long run, every reply checked for damage), `partial` (the
2.4G plugin's pacing, with phases to watch), `idle` (stream, silence, then probes: does the
keyboard sleep?). `--via-probe` measures the VIA round trip.

## Offline checks (no keyboard)

On a fresh clone, without the files listed under [Setup](#setup):

```bash
node tools/plugintest.mjs
python tools/gen_plugins.py --check
```

`plugintest.mjs` runs both plugins against a mock `device`: on the cable the patched, stock
and "no reply at first" scenarios, and every write identical to v1.0.1; on 2.4G a mock
receiver with a fake clock (handshake, pacing, loss, checksum nudge, leaving 2.4G mode and
Fn+V, a state reset, no reply, reply collisions). It records the packets into
`tests/fixtures/`.

With the local files, **everything at once** (about 12 minutes, real time, never alongside a
hardware run; every line of `summary.txt` must end in PASS, and the frozen files must be
unchanged):

```bash
bash tools/run_offline_suite.sh <scratch dir outside the repository>
```

It includes, among others:

| Command | What it checks |
|---|---|
| `tools/hostsim.py plugin tests/fixtures/plugin_packets.json` (and `…_rx.json`) | replays the plugins' packets through the real patch code (v1 on USB, v2 behind the fake receiver) |
| `tools/hostsim.py plugin2g` | the 2.4G plugin itself, in real time, against patch v2 behind a fake receiver, with and without packet loss |
| `tools/hostsim.py hidtest --led`, `stock --led`, `radio`, `radio2`, `pace`, `soak`, `partial`, `idle` | the test script against the emulated firmware, a fake stock firmware and the fake receiver |

## Regression checklist

After any firmware or plugin change. Close VIA, the Meletrix Console, SignalRGB and both Wuque
tools before every script run.

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
- [ ] Plug the cable in and out while SignalRGB runs: the cable plugin takes over, then after Fn+V the 2.4G plugin resumes.
- [ ] VIA and the Console over 2.4G (they share the interface: close SignalRGB first).
- [ ] Typing over 2.4G, with and without SignalRGB.

Other:
- [ ] Bluetooth typing.
- [ ] Mode keys (Fn+Z/X/C, Fn+V, Fn+Space) with SignalRGB running: own effect on BT; SignalRGB back on 2.4G or USB.
