/*
 * Patch v2 additions: addresses on the 2.4G radio path of the stock Zoom75
 * TIGA firmware v1.1 (SHA-256 678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b)
 * and the patch v1 symbols that v2 builds on (out/patched.hex, SHA-256
 * c93031edfbe3807372b5014b689165278e532de6e4f563526b0f0c6f850e6a79).
 *
 * Every value was re-checked in disassembly on 2026-10-20 (step 5); the
 * comment says where. Used together with fw.h (patch v1, unchanged).
 * Do not reuse this file with any other firmware build.
 */
#pragma once
#include <stdint.h>

/* ---- Firmware functions on the radio path (Thumb: call with bit 0 set) --- */

/* VIA dispatcher via_command(data, len, send): push.w {r4-r10,lr} at entry.
 * Exactly two callers (branch + literal scan of the original): 0x080140E2
 * (radio +DATA: handler, send = 0) and 0x08014D5E (USB HID task, send = 1).
 * Its return value is ignored at 0x080140E6 (r0 is overwritten).
 * Hook C replaces the bl at 0x080140E2 and tail-calls this unchanged. */
#define FW_VIA_COMMAND          0x0800F620u

/* Device write dev_write(dev, buf, len, timeout_ms): push {r4-r7,lr},
 * sub sp,#0x1c, then bl 0x080081A8 (queues the bytes as a mail for the
 * device's channel). Called at 0x0801413E with (2, "HID=..." line, n + 8,
 * 500); its return value is ignored (movs r0,#0 at 0x08014142). Hook D
 * replaces that one bl (the function has 7 callers; only this one changes)
 * and tail-calls this unchanged. */
#define FW_DEV_WRITE            0x08008050u

/* ---- Connection mode ----------------------------------------------------- */

/* FW_MODE_BYTE (fw.h, 0x20000B8C): 2 = 2.4G, written only by keycode 0x7F0D
 * (Fn + V): movs r0,#2; strb r0,[r6] at 0x0800E9FE, r6 = 0x20000B8C loaded at
 * 0x0800E934 and not changed in between. 1 = BT (0x0800EA2E), 3 = USB
 * (0x080040EC, 0x0800EA4A), 0 = keycode 0x7E00. */
#define MODE_24G                2u

/* ---- Patch v1 (byte-identical in v2) --------------------------------------- */

/* srgb_render(effect), from out/build/srgb.map and the flashed image:
 * stmdb sp!,{r4-r8,lr} at 0x0801A910. Hook B used to call it directly; in v2
 * srgb_render2 tail-calls it whenever the radio is not rendering. */
#define V1_SRGB_RENDER          0x0801A910u

/* v1 state struct (SRGB_STATE_ADDR in fw.h, 0x20007E00). Offsets from the
 * v1 listing: srgb_state.isra.0 (0x0801A9E8) writes +8, +9, +4, +0;
 * srgb_render sets rendering with strb r7,[r4,#9] at 0x0801A948. */
#define V1_OFF_MAGIC            0x00u   /* 0x53524742 "SRGB"                */
#define V1_OFF_RENDERING        0x09u   /* 1 while the effect is skipped    */

/* ---- Patch v2 RAM ---------------------------------------------------------- */

/* Radio-link state, 16 bytes in the same unreferenced gap above the stack top
 * (0x20007DB8-0x20007FFF) as the v1 struct, right after it
 * (0x20007E00-0x20007E0B). Not cleared at boot, so it carries a magic word.
 * Written by the radio task only (hooks C and D); the RGB task only reads it. */
#define SRGB_RADIO_STATE_ADDR   0x20007E10u
