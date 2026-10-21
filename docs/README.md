# Documentation

## Guides

| Document | For |
|---|---|
| [flashing.md](flashing.md) | Getting the official firmware, making and flashing the patched one, safety rules, going back to stock, recovery |
| [signalrgb.md](signalrgb.md) | Installing the plugins, using SignalRGB on the cable and over 2.4G, troubleshooting, known limits |
| [hardware-results.md](hardware-results.md) | Everything tested on a real keyboard |
| [development.md](development.md) | Building and verifying the patch, the test tools, the regression checklist |

## Engineering record

How the patch came about, in order. Each is a dated record; its status lines describe that
moment.

| Document | Content |
|---|---|
| [technical/feasibility.md](technical/feasibility.md) | Phase 1: the static analysis of the stock firmware, the four safety conditions, recovery paths |
| [technical/patch-v1.md](technical/patch-v1.md) | Phase 2: patch v1 (SignalRGB over USB): design, protocol, fallback rules, offline verification |
| [technical/patch-v2-design.md](technical/patch-v2-design.md) | Patch v2 (SignalRGB over the 2.4G receiver): how 2.4G raw HID reaches the firmware, the hooks, per-link state |
| [technical/patch-v2-verification.md](technical/patch-v2-verification.md) | Patch v2: build, static checks and 47,052 emulator checks |
| [technical/2.4g-link.md](technical/2.4g-link.md) | The receiver link: throughput, packet damage, the pacing the 2.4G plugin uses |
| [technical/power-and-sleep.md](technical/power-and-sleep.md) | When the keyboard sleeps on 2.4G, what wakes it, and what SignalRGB changes |

## Earlier file names

Code comments and older documents may use the names these files had during development:

| Earlier name | Now |
|---|---|
| `FEASIBILITY.md` | [technical/feasibility.md](technical/feasibility.md) |
| `PHASE2_REPORT.md` | [technical/patch-v1.md](technical/patch-v1.md) |
| `PATCH_V2_DESIGN.md` | [technical/patch-v2-design.md](technical/patch-v2-design.md) |
| `out_v2/OFFLINE_VERIFICATION.md` | [technical/patch-v2-verification.md](technical/patch-v2-verification.md) |
| `V2_STEP6_RESULTS.md` | [technical/2.4g-link.md](technical/2.4g-link.md) |
| `V2_STEP7_RESULTS.md` | [technical/power-and-sleep.md](technical/power-and-sleep.md) |
| `README.md` sections 1–11 (before the reorganisation) | the guides above |
| `HANDOFF.md`, `*_SESSION_PROMPT.md` | working notes, not published |
