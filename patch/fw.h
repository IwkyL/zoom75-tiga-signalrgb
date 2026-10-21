/*
 * Addresses inside the stock Zoom75 TIGA firmware v1.1
 * (SHA-256 678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b).
 *
 * Every value below was re-checked in disassembly during Phase 2; the
 * comment says where. Do not reuse this file with any other firmware build.
 */
#pragma once
#include <stdint.h>

/* ---- Firmware functions (Thumb: call with bit 0 set) -------------------- */

/* rgb_task_render(effect): QMK render step. Single caller: bl at 0x0801017C
 * inside rgb_matrix_task (0x0801009C). Our render hook replaces that bl. */
#define FW_RGB_TASK_RENDER      0x080102C4u

/* rgb_matrix_set_color(index, r, g, b): loads driver table 0x08018C7C[1]
 * (= 0x080128D1) and tail-calls it. The driver does NOT bounds-check index. */
#define FW_RGB_SET_COLOR        0x0800FFE8u

/* rgb_matrix_set_color_all(r, g, b): driver table [2] = 0x08012935, loops
 * index 0..85 over the driver set_color. */
#define FW_RGB_SET_COLOR_ALL    0x0800FFF8u

/* raw_hid_send(data, len): usb send on the raw HID interface (device handle
 * 0x20005DB0) followed by osDelay(2). VIA replies through the same function
 * from the same task. */
#define FW_RAW_HID_SEND         0x08009F64u

/* timer_read32(): returns the u32 millisecond counter at 0x20000938. */
#define FW_TIMER_READ32         0x08013400u

/* ---- Firmware RAM ------------------------------------------------------ */

/* QMK rgb_matrix state block (RW data, initial bytes 00 FF FF 03 ...). */
#define FW_RGB_BLOCK            0x20000AC0u
#define   RGB_OFF_SUSPEND       0x00u   /* nonzero: effect forced to 0     */
#define   RGB_OFF_LAST_ENABLE   0x01u   /* written by flush (0x0801029C)   */
#define   RGB_OFF_LAST_EFFECT   0x02u   /* written by flush                */
#define   RGB_OFF_TASK_STATE    0x03u   /* 0 start,1 render,2 flush,3 sync */
#define   RGB_OFF_ITER          0x0Cu   /* rgb_effect_params.iter          */
#define   RGB_OFF_CONFIG        0x18u   /* rgb_matrix_config: enable:2 mode:6 */
#define RGB_TASK_FLUSHING       2u

/* Connection mode byte. 3 = USB: set by the USB "device configured" user
 * callback (0x080040EC) and by the Fn USB key (keycode 0x7F08) only when USB
 * is live. Wireless channel code treats 1 and 2 as wireless. */
#define FW_MODE_BYTE            0x20000B8Cu
#define MODE_USB                3u

/* Firmware flag word; bit 9 = VBUS present (copied from PA9 at 0x08005D4A,
 * also selects the LED driver's 60 % vs 20 % brightness cap). */
#define FW_FLAGS_WORD           0x20000B88u
#define FLAGS_VBUS_BIT          (1u << 9)

/* USB device handle 0x20005DB0, +0x112 = device state
 * (1 default, 2 addressed, 3 configured, 4 suspended; see 0x08013900 and the
 * OTG IRQ handler 0x08003F90). */
#define FW_USB_DEV_STATE        (0x20005DB0u + 0x112u)
#define USB_STATE_CONFIGURED    3u

/* ---- Patch-owned RAM ---------------------------------------------------- */

/* 0x20007DB8-0x20007FFF lies above the initial stack pointer (end of the
 * zero-init region), is not referenced by any firmware literal, and is not
 * cleared at boot, so the state carries a magic word. */
#define SRGB_STATE_ADDR         0x20007E00u

#define RGB_LED_COUNT           86u
