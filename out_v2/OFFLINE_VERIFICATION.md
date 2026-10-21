# Patch v2: offline verification (step 5a/5b, 2026-10-20)

Status: **built and verified offline. Not flashed.** Nothing was sent to the keyboard or the
receiver in this step. Design: `PATCH_V2_DESIGN.md` (approved). Build report with every
changed hex record and the full listing: `out_v2/patch_report.md`.

Legend as in HANDOFF.md: **V** verified (method), **I** inferred (reasoning).

## 1. The file to flash

| Item | Value |
|---|---|
| **Hex** | **`out_v2/patched_v2.hex`**, 308,302 B, 6,854 lines |
| **SHA-256** | **`1f1f1c469d437d6d8626d9f667e32f0788b4798807b25c71fe8524f4772fe59d`** |
| Image | `0x08000000–0x0801AC17` (109,592 B); limit `0x0801AFFF` |
| Patch code | v1 480 B at `0x0801A828` (byte-identical, `b9ca4c09…`) + v2 528 B at `0x0801AA08–0x0801AC17` (`20d03fff…4358`) |
| Free after v2 | 1,000 B (`0x0801AC18–0x0801AFFF`) |
| Bytes that differ from the original | 4 hook sites (15 of 16 bytes) + 1,008 B appended |
| Sectors that differ from the original | **32, 40, 41, 53** (as designed). The hex covers sectors 0–53, the same set as the official update |
| Sectors that differ from v1 (flashed) | 32, 40, 53 |
| Record layout | CRLF, upper case, 16-byte records on 16-byte boundaries, original `0x04` / start (`0x08000171`) / EOF records in order (V selfcheck_v2) |
| Recorded in | `out_v2/SHA256SUMS.txt`, `out_v2/patch_report.md` |

Rollback: flash `out/patched.hex` (v1, `c93031ed…6a79`) or the official hex the same way.

## 2. The four sites (V asm, `out_v2/patch_report.md`)

| Hook | Site (sector) | Original | v1 (flashed) | v2 |
|---|---|---|---|---|
| A | `0x08014D42` (41) | `bl 0x0801240C` | `bl 0x0801A828` srgb_rx | **unchanged** |
| B | `0x0801017C` (32) | `bl 0x080102C4` | `bl 0x0801A910` srgb_render | `bl 0x0801AB70` srgb_render2 |
| C | `0x080140E2` (40) | `bl 0x0800F620` VIA | same | `bl 0x0801AA08` srgb_rx_radio |
| D | `0x0801413E` (40) | `bl 0x08008050` dev_write | same | `bl 0x0801AB40` srgb_radio_send |

## 3. What was verified

| Check | Result | Tag |
|---|---|---|
| v1 blob rebuilt from the frozen sources (`patch_v2/` copies, hashes = `patch/`) | `b9ca4c09…` = flashed v1 | V build |
| Reproducible | Two clean builds (`out_v2/`, scratch dir): identical hex, image, both blobs | V build |
| Build refusals | v1.0 hex, `out/patched.hex` as input, `--out out/`, `--out original/…`, overwrite of a differing `patched_v2.hex` | V run |
| **selfcheck_v2** (independent of build script and toolchain) | **PASSED**: diff vs original (only the 4 BLs), diff vs v1 (only B/C/D + appended), v1 code byte-identical, 31 protected ranges (selfcheck's 16 + 15 on the radio path, e.g. VIA, dev_write, +DATA: handler around the hooks, line handler, radio task), recursive disassembly accounts for every byte, no forbidden instructions, calls only VIA / dev_write / set_color / timer_read32 / v1 srgb_render, stores only radio state `0x20007E10–1F` / v1 rendering `0x20007E09` / `0x20000AC3` / `0x20000ACC` / payload[0–3] + one indexed loop, all 11 literal words on an explicit allowlist | V static |
| selfcheck_v2 negative tests | **14/14 caught** (vector byte, v1 byte, +DATA: byte, literal → flash controller, call → settings writer, store → `0x20000B9A`, `cpsid`, past `0x0801B000`, byte at `0x0801E000`, hook C unpatched, hook A redirected, original / v1 as v2, appended junk); the unmodified image passes | V static |
| **emutest_v2** (Unicorn, real bytes) | **47,052 checks, 0 failed** | V emu |
| — pass-through sweep | 5,128 cases (all 248 other first bytes in modes 0/1/2/3; 0x21–0x28 in modes 0/1/3; n ∉ 4..32 in mode 2; two radio states; two flag words): VIA entered once with **identical r0–r2, SP and LR = `0x080140E7`** (a true tail call), identical HID= line, return value and RAM; v2 code writes no RAM | V emu |
| — real VIA | 0x01 and 0x21–0x28 in modes 0/1/3 (+0x01 in mode 2): identical lines (`01 00 0b`, `ff` + echo) and RAM | V emu |
| — mode 2 handled | queries = v1's USB reply bytes in the HID= line (n = 4, 5, 8, 31, 32); errors 253/254 always sent; stream → exact set_color sequence; 0x25 quiet = byte 1 bit 0; line queued iff quiet off; quiet survives 0x26; skip_reply 0 after every packet and cleared after a simulated reset between C and D; patch writes only the radio state and payload[0..n-1] (memory-write hook) | V emu |
| — render | radio renders; 1999 ms fresh / 2000 ms falls back; resume without 0x25; 0x26; mode 2→1, 2→3 (with and without a USB stream); both links live (USB colours dropped, USB 0x26 doesn't stop the radio); RGB off / suspended; v1 state lost; radio state lost; timer backwards. Every hand-back goes through v1's switch-back (clear + re-init) | V emu |
| — **USB identical to v1** | every emutest.py scenario run on v1 and v2: **307/307 steps identical** (return value, stub calls with arguments, r0–r12/SP, RAM); emutest.py itself: 3,909/3,909 on both; all 30 renders enter v1 srgb_render with r0, SP, LR of the hook site; plus an explicit sweep of non-radio states | V emu |
| — mutation test of emutest_v2 | 7 one-instruction mutations of v2 (mode test, VIA argument, skip_reply, render mode test, zero-fill bound, quiet bit, timeout): **7/7 caught** | V emu |
| Stack | SP balanced and r4–r11 preserved on every run. Deepest v2 path: **44 B** below the +DATA: handler's frame (0x24 with real set_color + LED driver); VIA's own frame 32 B; the stock reply path dev_write + sendmail = 104 B below the same SP on every packet (V asm prologues). Pass-through and render hand-off: same depth as v1 | V emu + asm |
| hostsim radio2 (test script ↔ fake receiver ↔ real v2 bytes) | read-only PASS; `--via-probe`: all four IDs answered; `--marker-probe`: "v2 handled it"; `--led --steps colors`, full `--led`, full `--led --quiet`: PASS, LED timeline as announced, fallback 2006–2016 ms; quiet off 16,655/16,655 echoes; quiet on: no echo, only successful 0x24/0x25/0x26 silent | V emu |
| hostsim hidtest2 (USB, v2) | read-only and `--led` PASS | V emu |
| v1 suite (brief step 5b) | selfcheck.py PASSED; emutest.py 3,909/0; hostsim hidtest, hidtest --led, stock --led, radio (16 scenarios), plugin; plugintest.mjs: all PASS | V run |
| Frozen files | original, `out/`, `patch/`, build_patch.py, selfcheck.py, emutest.py: hashes unchanged before/after | V sha256 |

## 4. Implementation choices inside the design (not deviations)

1. Pass-through writes skip_reply = 0 only if it isn't 0 already (and the radio state is
   initialised). The post-condition is the design's ("skip_reply = 0"), and a pass-through
   packet changes no RAM at all, so modes 0/1/3 are v1 byte for byte, RAM included.
2. srgb_radio_send honours skip_reply only when the radio magic is valid, so junk RAM after
   power-on can never suppress a line.
3. srgb_render2 only reads the radio state (needs magic `"SRGR"`, never initialises it), so
   in non-radio states it writes nothing before handing off to v1.
4. `reply()` is `noinline`: 528 B instead of 612 B. The design estimated 350–500 B; the
   limit is 1,528 B.

## 5. Not verifiable offline (inferred)

- ~~I: the radio module delivers `+DATA:32`.~~ **Now V hw (2026-10-20 23:37, v1 firmware,
  through the receiver, cable plugged in, keyboard in 2.4G):** marker probe: `0x01` ok, then
  `0x21` with byte 31 = `0x5A` → `ff` + exact echo ending in `5a` (38.6 ms), then `0x01` ok.
  So all 32 bytes reach the keyboard's raw HID handler (n = 32). 3 packets sent. Log:
  `logs/srgb_hidtest_20261020_233728_rx_markerprobe.log`.
- **I:** throughput and packet loss over the radio (one `+DATA:` per UART chunk, about 1 ms per
  task pass; echoes double the traffic when quiet is off). That's step 6. The emulator has no
  radio timing.
- **I:** the receiver keeps forwarding when replies are suppressed (quiet on). Measured in 5c/6.
- **I:** the fake receiver in hostsim wraps each report as `+DATA:32\r\n…\r\n` and returns the
  HID= payload as one report. That is modelled on step 2's hardware log, not on receiver code.
- **I:** the Wuque tool accepts the file. It has the same layout as the original and v1 (v1 was
  accepted), and the model-ID bytes at `0x08004400` are unchanged.

## 6. Tool changes

- `tools/srgb_hidtest.py` (backup of the previous version:
  `backups/srgb_hidtest_rx_60c2c89b.py`):
  - `--quiet`: 0x25 is sent as `25 01`.
  - `--marker-probe`: 0x01, then 0x21 with byte 31 = 0x5A, then 0x01. Not run.
  - `--led` is allowed on the receiver; it still runs only after the read-only checks pass.
  - On the receiver: a known-state prelude (`25 <quiet>`, `26`). Each echo is predicted, and
    echoes seen / missing are reported per step; missing echoes fail every step except bench.
  - USB runs without `--quiet` send byte-identical packets to before.
- `tools/hostsim.py` (backup of the previous version: `backups/hostsim_rx_23efe279.py`):
  - `radio` gains the v1 `--led` and marker-probe scenarios and the new argument refusals.
  - New `radio2` and `hidtest2`.
- New: `build_patch_v2.py`, `patch_v2/`, `tools/selfcheck_v2.py`, `tools/emutest_v2.py`.

## 7. Hashes of the new and changed sources

| File | SHA-256 |
|---|---|
| `patch_v2/srgb_radio.c` | `f2e66bc9a4187a572f34143868b13d3ce6e7862a8091cbec7f4dc944b043b20f` |
| `patch_v2/fw_radio.h` | `dd348070586095f1c8c958f7f50ec54347e73735b4e86e372e8d0c5eb59c4019` |
| `patch_v2/srgb_radio.ld` | `1a3f8750a08ceaa94208b43de2fb92f6851b19c90ef04a4d51bbfad579a72c2d` |
| `patch_v2/srgb.c`, `fw.h`, `srgb.ld` | = `patch/` (frozen v1) |
| `build_patch_v2.py` | `fc48912640d39ed0f6ab39e3d5951ab96ebfa9a33e0397b857f95a45b178b299` |
| `tools/selfcheck_v2.py` | `ceab989cf23e54bd82aa5d722759d8f25c98f03564ddb6d1cc73175efaf5c7eb` |
| `tools/emutest_v2.py` | `4502c32ed33eac5c214e2a489ef0903787d6233ec324a1214a0311d4e896e607` |
| `tools/srgb_hidtest.py` | `fcd47ce6371f1810023567f615c1fe2e78de95cccc04558d4f35b8df4d696cb4` |
| `tools/hostsim.py` | `213fc1207bb800e84e41287a831d1b6d40c61e34a660a0301d5d8ed71da0a182` |

Re-run everything:

```
.venv/Scripts/python.exe -I build_patch_v2.py --out <scratch dir>   # must reproduce 1f1f1c46…
.venv/Scripts/python.exe -I tools/selfcheck_v2.py --negative
.venv/Scripts/python.exe -I tools/emutest_v2.py
.venv/Scripts/python.exe -I tools/hostsim.py radio2
.venv/Scripts/python.exe -I tools/hostsim.py hidtest2 --led --walk-delay 0.3
```
