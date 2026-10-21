# Patch v2, step 7: power and sleep on 2.4G (2026-10-21)

Firmware: `out_v2/patched_v2.hex` (`1f1f1c46…e59d`), unchanged.
Static analysis of the official v1.1 image (`analysis/tiga_v11.bin`; patch v2 changes none of these
addresses), then two hardware runs on 2026-10-21 (section 5), each with the user's OK.

Legend: **V** verified, with the method (asm = disassembly, dec = Ghidra decompile, data = parsed bytes, src = source code, hw = measured on the keyboard, log = SignalRGB's log, emu = emulator); **I** inferred, with the reasoning.

## 1. Results in short

| Question | Answer | Tag |
|---|---|---|
| Does the keyboard sleep when idle on 2.4G? | **Yes.** 14 min after the last packet (and ~13 min at the latest when you looked) LEDs and LCD were dark and it answered nothing over the radio | V hw run 1 + user; V asm/dec: STOP mode |
| When? | Only on battery (PA9 / VBUS low), and on 2.4G only after the radio module reports "idle" (`+EVENT:40` or `80`), and then 60 s after the AT32's last AT command. The firmware sets the radio module to `AT+AUTOIDLE=ENABLE` on battery and `AT+IDLETIMES=600` (or `3600` with a config bit) | V asm/dec; I: 600 = seconds, so about 10–11 min. V hw: asleep by ~13 min (exact onset not measured) |
| How does it look? | **LEDs and LCD off** | V user (run 1) |
| What wakes it? | A key press (the 16 matrix columns are EXTI wake lines). An RTC alarm also wakes it, only to feed the watchdog, and it sleeps again. After Shift the LEDs came on at once; the radio answered again **16–21 s** later | V asm/dec; V hw run 1 + user |
| Does a radio packet wake it? | **No.** 31 read-only `0x22` over 2.5 min, none answered, the keyboard stayed dark | V hw run 1; I (static): USART2 is clock-gated, no radio wake line |
| Does it always sleep? | Not seen this morning: untouched on battery for hours, LEDs and LCD on. Its mode then is unknown, maybe not 2.4G; the sleep path needs the radio's idle report, which may depend on the mode | V user; I |
| Do incoming packets keep it awake? | **Yes.** A 16-min stream at the plugin's pacing, quiet on (the keyboard sent nothing back), untouched: it kept alternating colours to the end and answered 12/12 probes afterwards. Patch v2 touches none of the sleep conditions, so the radio module must count incoming packets as activity | V hw run 2 + user; V (selfcheck_v2 store allowlist); I: the radio module's rule |
| Does SignalRGB mode end so it can sleep? | **Yes:** after the stream stopped (no `0x26`) the keyboard's own effect came back ~2 s later, and it fell asleep later as usual. Sleep does not depend on SignalRGB mode at all | V hw run 1 + user; V emu (hostsim idle 2.09 s) |
| Battery cost | Section 4 | I |

## 2. The sleep path (V asm/dec)

- **Main loop** `0x0800C0B4` (one pass about every 1 ms, `osSignalWait(0x1000, 1)`) calls the idle routine
  `0x0800D9D0` only when **PA9 = 0** (VBUS absent, read through bit-band `0x42210124`) and `[0x20000954] == 0`.
- **`0x0800D9D0`** first lowers the clock on battery (saves RCC, re-inits; I: clock switch), then enters
  STOP when **all** of these hold:
  1. `timer_elapsed([0x20000A58]) >= 60000` (`0x0800DBF0` returns `0xEA60`);
  2. bit 16 of `0x20000B88` is clear. No code sets it (resolved-store scan of all 21 writers of
     `0x20000B88` and a scan for its bit-band aliases): I, always clear;
  3. `[0x20000A48] == 0` (radio not connected) **or** `[0x20000A4A] != 0` (radio idle);
  4. no AT command pending (`0x0800F50C`: the 15 command flags and `0x200069E0+0x22`).
- **`0x20000A58`** is written only by the AT-command sender `0x080149A4` (`0x08014AB0`, right after it queues
  an AT line with `0x08008050`), by the wake-up path (`0x0800DB96`) and by init (`0x0801328C`, zero).
  Raw-HID replies (hook D → `0x08008050`) and key reports (`0x08003C44`) do not stamp it.
- **Radio state bytes**, written by the `+EVENT:%x` handler in `0x08015214` (base `0x20000A44`):

  | `+EVENT:` | Effect |
  |---|---|
  | `04` | connected: `A48` = 1, `A4A` = 0 (only if at least 800 ms after an earlier timestamp) |
  | `01`, `02`, `08`, `10` | disconnected / other: `A48` = 0 |
  | **`40`, `80`** | **`A4A` = 1: radio idle** (the AT32 may now sleep) |
  | `100` | `A4A` = 0: active again |
  | `200` | `A4C` pulse (20 or 50 ms delay) |

- **AT setup** (table `0x08016CD2`, 15 sequences of 18-byte steps; sequence 0 runs at radio start):
  `AT+UART=230400`, `AT+VERSION?`, `AT+NAME?`, **`AT+AUTOIDLE=`** `ENABLE` on battery, `DISABLE` when
  bit 9 (VBUS) or bit 10 of `0x20000B88` is set (`0x08013FB4`), **`AT+IDLETIMES=`** `600`, or `3600` when bit 2 of
  byte `0x20000B86` is set (`0x080141D4`), `AT+VIDPID=1EA8CEDD`, `AT+EVENT=00FF`, `AT+MODULETYPE=09`,
  `AT+AUTOEVENT=ENABLE`, `AT+KEYBOARD?`, `AT+BATSTATE?`. Other sequences: `AT+DEEPSLEEP` (a countdown at
  `0x200069FE`), `AT+LEDPAGE?`, `AT+RSSI?`, `AT+AUTOIDLE=` + `AT+BATCHARGE=`, `AT+CONN=3`, `AT+DISCONN`, `AT+OTA`,
  `AT+FTYRESET`.
- **Entering STOP:** 200 ms delay, SysTick interrupt off, all peripheral clocks off except PWR
  (AHB = 0, APB2 = 0, APB1 = `0x10000000`), GPIO ports A–E set to analog input, then:
  - RTC on LSI with an alarm (EXTI 17, IRQ 41); its handler `0x08001880` sets `0x200008E8` = 1;
  - the 6 rows (PA15 PC9 PB1 PB0 PC5 PC4) driven, the 16 columns (PF6 PF7 PC12 PD2 PB3 PB4 PB5 PB8 PB9 PC13
    PC14 PC15 PC0 PB11 PC1 PC10) as EXTI lines 0–15, IRQs 6–10, 23, 40 enabled;
  - loop: feed the IWDG (`0xAAAA`), `SLEEPDEEP` + `WFI` (`0x08002F20`); if `0x200008E8` was set (RTC
    alarm), clear it and sleep again, otherwise (a key) wake up.
  - `0x0800DBE0` returns 0, so the optional extra wake source `0x08001980(0x100, 0xFF, 0x4000000)` is not set up.
- **Waking:** clocks and peripherals restored, 30 matrix scans 1 ms apart, the ms counter `0x20000938`
  **reset to 0**, `0x20000A58` stamped. Patch v2 copes with the counter jump: its timeout test sees an old
  `last_ms` as stale (emutest_v2 "timer backwards"), so the stream renders again from its next packet.
- **LED power** (`0x0800B86C`, every main-loop pass): bit 14 of `0x20000B88` = LED supply on; it is switched
  off 3 s after a dark state (I: the exact condition reads the indicator block at `0x20001F96`).

## 3. What this means for SignalRGB over 2.4G

- **While SignalRGB streams over 2.4G, the keyboard does not sleep** (V hw run 2). It sleeps as usual about
  11–13 min after SignalRGB stops (exit, PC sleep or shutdown) and the last key press (V hw run 1).
- Patch v2 writes only its radio state, `0x20007E09`, `0x20000AC3/ACC` and the payload (V selfcheck_v2), so the
  keep-awake comes from the radio module counting incoming packets (I). With quiet on the keyboard sends nothing
  back, so outgoing replies are not what keeps it awake (V: no reply during run 2's stream).
- If it ever sleeps anyway, a key press wakes it, the radio is back after ~20 s, and the stream resumes by itself:
  the radio state (mode on, quiet on) survives STOP (RAM is kept), and the next colour packet re-stamps the
  timeout (I; emutest_v2 "timer backwards" covers the counter reset).
- The 2.4G plugin's handshake retries (one `0x22` every 10 s while the keyboard answers through VIA) probably
  keep it awake too in that state (I, not measured).

## 4. Battery cost (I, not measured)

- On battery the LED driver caps every channel at 20 % (`0x080128D0`, V asm). SignalRGB's colours are scaled the same
  way as the built-in effects, so a SignalRGB scene costs about what a built-in effect of the same brightness costs.
- The real cost is **not sleeping** (V hw run 2): the LEDs, the AT32 at its reduced clock and the radio stay on for
  as long as SignalRGB streams, instead of the keyboard sleeping ~11–13 min after the last key press. The radio link
  itself carries 6.7 packets/s in a still scene and 12.5/s at most.
- In practice: with SignalRGB running whenever the PC is on, the keyboard stays awake while the PC is on, like a
  keyboard in constant use with its LEDs on; it sleeps after the PC goes to sleep or off. To save battery, use the
  cable or close SignalRGB.
- No capacity is documented in this project, and the firmware's `+BATSTATE` values are not readable without vendor
  commands. A measurement would be the battery level shown by the keyboard before and after a timed stream (optional).

## 5. Hardware runs (each with the user's OK)

Tool: `tools/srgb_hidtest.py --steps idle` (receiver only, alone, `--packet-gap` ≥ 75), tested offline
(`hostsim.py idle`, in the offline suite):
1. optional lead-in: `--idle-lead S` of the planned 2.4G pacing (magenta / cyan alternating every 3 s), **no `0x26`**;
2. silence: nothing sent for `--idle-silent S`;
3. probes: one read-only `0x22` every `--idle-probe-every S` for `--idle-probe-for S`, each answered or lost.

| Run | Command (all `--vid 0x6D66 --pid 0x8888 --led --quiet --steps idle --packet-gap 80`) | Question |
|---|---|---|
| 1 | defaults: `--idle-lead 10 --idle-silent 840 --idle-probe-every 5 --idle-probe-for 300` (~20 min) | Does it sleep after ~11 min idle; how it looks; do probes wake it; does a key press (during the probes) wake it |
| 2 | `--idle-lead 960 --idle-silent 0 --idle-probe-for 60` (~17 min; length from run 1) | Does a stream with quiet on keep it awake |

**Run 1** (`logs/srgb_hidtest_20261021_135135_rx_idlequiet.log`, 13:51–14:11, keyboard in 2.4G mode since
the Fn+V at 13:47, cable unplugged, untouched until 14:08:00):
- read-only checks PASS (41–49 ms); lead-in 70 colour packets, ≥ 80 ms apart, last at 13:51:46. User: magenta/cyan,
  then the keyboard's own effect ~2 s after the stream stopped.
- silence 13:51:46–14:05:46: nothing sent, 0 reports received. User at 14:04–14:05: **LEDs dark, LCD off**.
- probes every 5 s from 14:05:46: **probes 1–31 (14:05:46–14:08:16) lost**; user pressed Shift at 14:08:00, the LEDs
  came on; **probes 32–60 (14:08:21–14:10:41) answered**, 38–51 ms. 0 unexpected replies. 139 packets in all.
- So: asleep by ~13 min; radio packets don't wake it; a key does; the radio link is back 16–21 s after the key.

**Run 2** (`logs/srgb_hidtest_20261021_145758_rx_idlequiet.log`, 14:57–15:15, 2.4G, cable unplugged, untouched):
- read-only checks PASS (45–50 ms); **6,400 colour packets in 16 min** (≥ 80 ms apart, median 96 ms), quiet on,
  0 unexpected replies (no damage seen).
- User: magenta/cyan kept alternating all along, never dark.
- Probes right after the stream: **12/12 answered**, 40–51 ms. 6,421 packets in all.
- So: the stream keeps the keyboard awake (16 min > the ~13 min after which run 1 found it asleep).

**SignalRGB checks with the final plugin** (log `SignalRGB_20261021_151629.log`): handshake and `25 01`; cable in at
15:17:36 → cable plugin, 2.4G plugin paused; unplugged at 15:17:46 + Fn+V → 2.4G back at the 15:17:52 retry.

## 6. Files and hashes at the end of steps 7–9 (2026-10-21)

| File | SHA-256 |
|---|---|
| `out_v2/patched_v2.hex` (flashed, unchanged) | `1f1f1c469d437d6d8626d9f667e32f0788b4798807b25c71fe8524f4772fe59d` |
| `plugin/src/tiga_plugin.src.js` (the one plugin source) | `90b6cc6c37079fd037b22369367a20c85f1c5428e4965915aff34803e44b8fde` |
| `plugin/Meletrix_Zoom75_TIGA_SignalRGB.js` (cable, 1.1.0; installed copy identical) | `b9bfc36381f1a12da22724e18281fad6bab937592e637bf230e17ef88b0394c5` |
| `plugin/Meletrix_Zoom75_TIGA_24G_SignalRGB.js` (2.4G, 1.1.0; installed copy identical) | `bb5596500e897fdd40fa14806319eb9284f3775e95909c5bee752652f2f6f7e9` |
| `tools/gen_plugins.py` / `tools/plugintest.mjs` / `tools/plugin_bridge.mjs` | `b6a4f77d…25e5` / `1e86ab6a…786b` / `7aa5ffa7…36d0` |
| `tools/srgb_hidtest.py` / `tools/hostsim.py` / `tools/run_offline_suite.sh` | `ae6b6804…1bf9` / `2825e012…10b0` / `c51b6485…3d74` |
| `logs/plugin_packets.json` (cable, unchanged since v1.0.1) / `logs/plugin_packets_rx.json` | `76acfa5c…0205` / `9f23cc4d…a763` |

- Offline suite on these files: **20/20 PASS**, frozen files unchanged (`bash tools/run_offline_suite.sh <scratch>`).
- Local backups (not in the repository except the v1.0.1 plugin; each named with its hash): `backups/Meletrix_Zoom75_TIGA_SignalRGB_v1.0.1_e6cbbf85.js` (the
  baseline the cable plugin is compared with), the installed intermediate builds `backups/installed_*`,
  `srgb_hidtest_v2step6_9a186c5a.py`, `hostsim_v2step6_b6dccc5b.py`, `run_offline_suite_cbbe21d3.sh`,
  `plugintest_51b43a56.mjs`, `README_bc8ee7e6.md`, `HANDOFF_3893b242.md`.
