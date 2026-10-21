/*
 * SignalRGB support for the Meletrix Zoom75 TIGA stock firmware v1.1.
 *
 * Implements the SRGBmods QMK protocol 1.0.6 (commands 0x21-0x28) on the
 * USB raw HID interface, and renders streamed colours through the firmware's
 * own rgb_matrix_set_color / flush path.
 *
 * The code lives in the free tail of flash sector 53 (from 0x0801A828) and is
 * reached from exactly two patched BL instructions:
 *
 *   0x08014D42  USB HID task:    bl 0x0801240C ("return 0" stub) -> bl srgb_rx
 *   0x0801017C  rgb_matrix_task: bl rgb_task_render              -> bl srgb_render
 *
 * srgb_rx returns 0 for every packet that is not 0x21-0x28, which is what the
 * stub returned, so the vendor and VIA handlers see those packets unchanged.
 * srgb_render calls the original rgb_task_render unless SignalRGB mode is on
 * and fresh, so the stock effects run unchanged otherwise.
 *
 * Fallback to the normal effect happens when any of these holds:
 *   - mode disabled (0x26), or never enabled since the state was initialised
 *   - no valid stream packet or enable for SRGB_TIMEOUT_MS
 *   - USB not live: connection mode is not USB (wireless), VBUS is gone, or
 *     the USB device is not in the configured state (unplugged / suspended)
 *   - RGB turned off or suspended in the firmware's own rgb_matrix state
 */
#include <stdint.h>

#include "fw.h"

/* ---- Protocol (SRGBmods QMK_Community_Module, signalrgb.h) ------------- */

#define CMD_QMK_VERSION         0x21u
#define CMD_PROTOCOL_VERSION    0x22u
#define CMD_UNIQUE_IDENTIFIER   0x23u
#define CMD_STREAM_RGB_DATA     0x24u
#define CMD_MODE_ENABLE         0x25u
#define CMD_MODE_DISABLE        0x26u
#define CMD_TOTAL_LEDS          0x27u
#define CMD_FIRMWARE_TYPE       0x28u

#define PROTOCOL_VERSION_1      1u
#define PROTOCOL_VERSION_2      0u
#define PROTOCOL_VERSION_3      6u

/* The stock firmware's QMK base version is unknown; report 0.0.0. */
#define QMK_VERSION_1           0u
#define QMK_VERSION_2           0u
#define QMK_VERSION_3           0u

#define UNIQUE_ID_1             0u
#define UNIQUE_ID_2             0u
#define UNIQUE_ID_3             0u

#define FIRMWARE_TYPE           2u      /* same value as SRGBmods */
#define ERROR_LED_BOUNDS        253u
#define ERROR_LED_COUNT         254u
#define MAX_LEDS_PER_PACKET     9u      /* SRGBmods rejects count >= 10 */

#define SRGB_TIMEOUT_MS         2000u
#define SRGB_MAGIC              0x53524742u     /* "SRGB" */

/* ---- Firmware calls ---------------------------------------------------- */

typedef void (*render_fn)(uint32_t effect);
typedef void (*set_color_fn)(uint32_t index, uint32_t r, uint32_t g, uint32_t b);
typedef void (*set_color_all_fn)(uint32_t r, uint32_t g, uint32_t b);
typedef void (*raw_hid_send_fn)(uint8_t *data, uint32_t length);
typedef uint32_t (*timer_read32_fn)(void);

/* Thumb functions: set bit 0 so BLX stays in Thumb state. */
#define FW_FN(type, addr)       ((type)((addr) | 1u))

#define REG8(addr)              (*(volatile uint8_t *)(addr))
#define REG32(addr)             (*(volatile uint32_t *)(addr))
#define RGB_BLOCK(off)          REG8(FW_RGB_BLOCK + (off))

/* ---- Patch state (uninitialised RAM, guarded by a magic word) ---------- */

typedef struct {
    uint32_t magic;
    uint32_t last_ms;       /* timer_read32() at the last enable / stream */
    uint8_t  enabled;       /* 1 after 0x25, 0 after 0x26                  */
    uint8_t  rendering;     /* 1 while srgb_render is skipping the effect  */
} srgb_state_t;

static volatile srgb_state_t *srgb_state(void)
{
    volatile srgb_state_t *s = (volatile srgb_state_t *)SRGB_STATE_ADDR;

    if (s->magic != SRGB_MAGIC) {
        s->enabled = 0;
        s->rendering = 0;
        s->last_ms = 0;
        s->magic = SRGB_MAGIC;
    }
    return s;
}

static uint32_t now_ms(void)
{
    return FW_FN(timer_read32_fn, FW_TIMER_READ32)();
}

static int usb_link_ok(void)
{
    return REG8(FW_MODE_BYTE) == MODE_USB
        && (REG32(FW_FLAGS_WORD) & FLAGS_VBUS_BIT) != 0
        && REG8(FW_USB_DEV_STATE) == USB_STATE_CONFIGURED;
}

/* RGB enabled and not suspended: the case where stock rgb_matrix_task passes
 * effect = mode; otherwise it renders effect 0 (all off) and so do we. */
static int rgb_lit(void)
{
    return RGB_BLOCK(RGB_OFF_SUSPEND) == 0
        && (RGB_BLOCK(RGB_OFF_CONFIG) & 3u) != 0;
}

static int srgb_should_render(volatile srgb_state_t *s)
{
    if (s->enabled != 1 || !rgb_lit() || !usb_link_ok())
        return 0;
    return (uint32_t)(now_ms() - s->last_ms) < SRGB_TIMEOUT_MS;
}

/* ---- Raw HID ----------------------------------------------------------- */

/* Reply in place, in the received buffer, the same way VIA does. */
static void reply(uint8_t *data, uint32_t length,
                  uint32_t b0, uint32_t b1, uint32_t b2, uint32_t b3)
{
    volatile uint8_t *p = data;     /* volatile: no memset() call emitted */
    uint32_t i;

    p[0] = (uint8_t)b0;
    p[1] = (uint8_t)b1;
    p[2] = (uint8_t)b2;
    p[3] = (uint8_t)b3;
    for (i = 4; i < length; i++)
        p[i] = 0;
    FW_FN(raw_hid_send_fn, FW_RAW_HID_SEND)(data, length);
}

static void stream_rgb_data(volatile srgb_state_t *s, uint8_t *data, uint32_t length)
{
    uint32_t start = data[1];
    uint32_t count = data[2];
    const uint8_t *rgb = &data[3];
    uint32_t i;

    if (start + count > RGB_LED_COUNT) {
        reply(data, length, CMD_STREAM_RGB_DATA, ERROR_LED_BOUNDS, 0, 0);
        return;
    }
    if (count > MAX_LEDS_PER_PACKET || 3u + 3u * count > length) {
        reply(data, length, CMD_STREAM_RGB_DATA, ERROR_LED_COUNT, 0, 0);
        return;
    }
    /* Not in SignalRGB mode, or not on a live USB link: drop the colours. */
    if (s->enabled != 1 || !usb_link_ok())
        return;

    for (i = 0; i < count; i++, rgb += 3)
        FW_FN(set_color_fn, FW_RGB_SET_COLOR)(start + i, rgb[0], rgb[1], rgb[2]);
    s->last_ms = now_ms();
}

/* Called from the USB HID task for every raw HID packet, before the vendor
 * and VIA handlers. Nonzero return = handled (they are skipped). */
uint32_t srgb_rx(uint8_t *data, uint32_t length)
{
    volatile srgb_state_t *s;
    uint32_t cmd;

    if (length < 4u)
        return 0;
    cmd = data[0];
    if (cmd < CMD_QMK_VERSION || cmd > CMD_FIRMWARE_TYPE)
        return 0;

    s = srgb_state();
    switch (cmd) {
    case CMD_QMK_VERSION:
        reply(data, length, cmd, QMK_VERSION_1, QMK_VERSION_2, QMK_VERSION_3);
        break;
    case CMD_PROTOCOL_VERSION:
        reply(data, length, cmd, PROTOCOL_VERSION_1, PROTOCOL_VERSION_2, PROTOCOL_VERSION_3);
        break;
    case CMD_UNIQUE_IDENTIFIER:
        reply(data, length, cmd, UNIQUE_ID_1, UNIQUE_ID_2, UNIQUE_ID_3);
        break;
    case CMD_STREAM_RGB_DATA:
        stream_rgb_data(s, data, length);
        break;
    case CMD_MODE_ENABLE:
        s->last_ms = now_ms();
        s->enabled = 1;
        break;
    case CMD_MODE_DISABLE:
        s->enabled = 0;
        break;
    case CMD_TOTAL_LEDS:
        reply(data, length, cmd, RGB_LED_COUNT, 0, 0);
        break;
    default: /* CMD_FIRMWARE_TYPE */
        reply(data, length, cmd, FIRMWARE_TYPE, 0, 0);
        break;
    }
    return 1;
}

/* ---- Render ------------------------------------------------------------ */

/* Called by rgb_matrix_task in its RENDERING state, in place of
 * rgb_task_render(effect). */
void srgb_render(uint32_t effect)
{
    volatile srgb_state_t *s = srgb_state();

    if (srgb_should_render(s)) {
        /* Like a no-op effect that finishes in one pass: keep the streamed
         * frame buffer and go straight to FLUSHING. */
        s->rendering = 1;
        RGB_BLOCK(RGB_OFF_ITER) = (uint8_t)(RGB_BLOCK(RGB_OFF_ITER) + 1u);
        RGB_BLOCK(RGB_OFF_TASK_STATE) = RGB_TASK_FLUSHING;
        return;
    }

    if (s->rendering) {
        /* Leaving SignalRGB mode: clear the streamed pixels and make the
         * stock effect re-initialise (the same 0xFF values as at boot). */
        s->rendering = 0;
        FW_FN(set_color_all_fn, FW_RGB_SET_COLOR_ALL)(0, 0, 0);
        RGB_BLOCK(RGB_OFF_LAST_EFFECT) = 0xFFu;
        RGB_BLOCK(RGB_OFF_LAST_ENABLE) = 0xFFu;
    }
    FW_FN(render_fn, FW_RGB_TASK_RENDER)(effect);
}
