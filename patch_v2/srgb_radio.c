/*
 * Patch v2: SignalRGB over the 2.4G receiver for the Meletrix Zoom75 TIGA
 * stock firmware v1.1 + patch v1 (srgb.c, byte-identical at 0x0801A828).
 *
 * Design: PATCH_V2_DESIGN.md (approved 2026-10-20). The code is appended at
 * 0x0801AA08, after the v1 code, and is reached from three patched BLs:
 *
 *   0x0801017C  rgb_matrix_task:  bl v1 srgb_render    -> bl srgb_render2
 *   0x080140E2  +DATA: handler:   bl via_command       -> bl srgb_rx_radio
 *   0x0801413E  +DATA: handler:   bl dev_write         -> bl srgb_radio_send
 *
 * Hooks C and D run in the radio task, in that order, in one straight-line
 * run of the +DATA: handler 0x080140AC (0x080140E6-0x0801413E has no branch
 * out). The handler turns the received payload into the reply line in place
 * ("HID=", n + 1, flag, payload, "\r\n") and queues it with dev_write.
 *
 * srgb_rx_radio handles 0x21-0x28 only in 2.4G mode with 4 <= len <= 32;
 * everything else is tail-called to via_command with r0-r2 unchanged, so BT
 * mode, USB mode and every other packet behave exactly as before. Replies are
 * written in place for the stock handler to send; raw_hid_send is never called.
 *
 * Per-link state: the radio has its own enabled / last_ms, so a SignalRGB
 * instance on the cable cannot switch the radio stream on or off (and the v1
 * rule mode == 3 keeps the radio from driving the USB stream).
 *
 * Quiet switch: 0x25 with byte 1 bit 0 set turns off the reply line for
 * successful 0x24 and for 0x25 / 0x26. Query replies and the 0x24 errors are
 * always sent. Default (standard 0x25, zero body): every packet is echoed, as
 * the stock firmware does.
 */
#include <stdint.h>

#include "fw.h"
#include "fw_radio.h"

/* ---- Protocol (same values as patch v1, srgb.c) ------------------------ */

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
#define QMK_VERSION_1           0u
#define QMK_VERSION_2           0u
#define QMK_VERSION_3           0u
#define UNIQUE_ID_1             0u
#define UNIQUE_ID_2             0u
#define UNIQUE_ID_3             0u
#define FIRMWARE_TYPE           2u
#define ERROR_LED_BOUNDS        253u
#define ERROR_LED_COUNT         254u
#define MAX_LEDS_PER_PACKET     9u

#define RADIO_MIN_LEN           4u      /* same minimum as v1 on USB        */
#define RADIO_MAX_LEN           32u     /* the receiver's report size       */
#define MODE_QUIET_BIT          0x01u   /* 0x25 byte 1                      */

#define SRGB_TIMEOUT_MS         2000u
#define SRGB_MAGIC              0x53524742u     /* "SRGB" (v1 struct)  */
#define SRGR_MAGIC              0x53524752u     /* "SRGR" (radio)      */

/* ---- Firmware calls ---------------------------------------------------- */

typedef void (*via_command_fn)(uint8_t *data, uint32_t length, uint32_t send);
typedef uint32_t (*dev_write_fn)(uint32_t dev, uint8_t *buf, uint32_t length, uint32_t timeout);
typedef void (*render_fn)(uint32_t effect);
typedef void (*set_color_fn)(uint32_t index, uint32_t r, uint32_t g, uint32_t b);
typedef uint32_t (*timer_read32_fn)(void);

#define FW_FN(type, addr)       ((type)((addr) | 1u))

#define REG8(addr)              (*(volatile uint8_t *)(addr))
#define REG32(addr)             (*(volatile uint32_t *)(addr))
#define RGB_BLOCK(off)          REG8(FW_RGB_BLOCK + (off))

/* ---- Radio state (uninitialised RAM, guarded by a magic word) ---------- */

typedef struct {
    uint32_t magic;         /* SRGR_MAGIC once initialised                  */
    uint32_t last_ms;       /* timer_read32() at the last enable / stream   */
    uint8_t  enabled;       /* 1 after 0x25, 0 after 0x26                   */
    uint8_t  quiet;         /* 1: no reply line for 0x24 ok / 0x25 / 0x26   */
    uint8_t  skip_reply;    /* hook C -> hook D: do not queue this line     */
} radio_state_t;

#define RADIO   ((volatile radio_state_t *)SRGB_RADIO_STATE_ADDR)

static volatile radio_state_t *radio_state(void)
{
    volatile radio_state_t *s = RADIO;

    if (s->magic != SRGR_MAGIC) {
        s->enabled = 0;
        s->quiet = 0;
        s->skip_reply = 0;
        s->last_ms = 0;
        s->magic = SRGR_MAGIC;
    }
    return s;
}

static uint32_t now_ms(void)
{
    return FW_FN(timer_read32_fn, FW_TIMER_READ32)();
}

/* RGB enabled and not suspended (same test as v1 rgb_lit). */
static int rgb_lit(void)
{
    return RGB_BLOCK(RGB_OFF_SUSPEND) == 0
        && (RGB_BLOCK(RGB_OFF_CONFIG) & 3u) != 0;
}

/* ---- Hook C: radio raw HID --------------------------------------------- */

/* Reply in place: the stock handler sends the buffer as the "HID=" line.
 * noinline: one copy instead of seven keeps the listing short to review. */
static __attribute__((noinline)) void reply(uint8_t *data, uint32_t length,
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
}

/* Returns the skip_reply value for this packet. */
static uint32_t stream_rgb_data(volatile radio_state_t *s, uint8_t *data, uint32_t length)
{
    uint32_t start = data[1];
    uint32_t count = data[2];
    const uint8_t *rgb = &data[3];
    uint32_t i;

    if (start + count > RGB_LED_COUNT) {
        reply(data, length, CMD_STREAM_RGB_DATA, ERROR_LED_BOUNDS, 0, 0);
        return 0;
    }
    if (count > MAX_LEDS_PER_PACKET || 3u + 3u * count > length) {
        reply(data, length, CMD_STREAM_RGB_DATA, ERROR_LED_COUNT, 0, 0);
        return 0;
    }
    /* Radio mode off: drop the colours (the line is still echoed unless quiet). */
    if (s->enabled == 1) {
        for (i = 0; i < count; i++, rgb += 3)
            FW_FN(set_color_fn, FW_RGB_SET_COLOR)(start + i, rgb[0], rgb[1], rgb[2]);
        s->last_ms = now_ms();
    }
    return s->quiet;
}

/* Replaces bl via_command(data, len, 0) at 0x080140E2. */
void srgb_rx_radio(uint8_t *data, uint32_t length, uint32_t send)
{
    volatile radio_state_t *s = RADIO;
    uint32_t cmd, skip;

    if (REG8(FW_MODE_BYTE) != MODE_24G
            || length < RADIO_MIN_LEN || length > RADIO_MAX_LEN
            || data[0] < CMD_QMK_VERSION || data[0] > CMD_FIRMWARE_TYPE) {
        /* Not ours: the stock path, unchanged. Hook D must queue this line.
         * skip_reply is already 0 unless a reset hit between hooks C and D;
         * write only then, so a pass-through packet changes no RAM. */
        if (s->magic == SRGR_MAGIC && s->skip_reply != 0)
            s->skip_reply = 0;
        FW_FN(via_command_fn, FW_VIA_COMMAND)(data, length, send);
        return;
    }

    s = radio_state();
    cmd = data[0];
    skip = 0;
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
        skip = stream_rgb_data(s, data, length);
        break;
    case CMD_MODE_ENABLE:
        s->last_ms = now_ms();
        s->enabled = 1;
        s->quiet = (uint8_t)(data[1] & MODE_QUIET_BIT);
        skip = s->quiet;
        break;
    case CMD_MODE_DISABLE:
        s->enabled = 0;
        skip = s->quiet;
        break;
    case CMD_TOTAL_LEDS:
        reply(data, length, cmd, RGB_LED_COUNT, 0, 0);
        break;
    default: /* CMD_FIRMWARE_TYPE */
        reply(data, length, cmd, FIRMWARE_TYPE, 0, 0);
        break;
    }
    s->skip_reply = (uint8_t)skip;
}

/* ---- Hook D: reply line ------------------------------------------------ */

/* Replaces bl dev_write(2, line, n + 8, 500) at 0x0801413E. */
uint32_t srgb_radio_send(uint32_t dev, uint8_t *line, uint32_t length, uint32_t timeout)
{
    volatile radio_state_t *s = RADIO;

    if (s->magic == SRGR_MAGIC && s->skip_reply == 1) {
        s->skip_reply = 0;          /* one line only; the caller ignores r0 */
        return 0;
    }
    return FW_FN(dev_write_fn, FW_DEV_WRITE)(dev, line, length, timeout);
}

/* ---- Hook B: render ---------------------------------------------------- */

static int radio_should_render(volatile radio_state_t *s)
{
    if (s->magic != SRGR_MAGIC || s->enabled != 1
            || REG8(FW_MODE_BYTE) != MODE_24G || !rgb_lit())
        return 0;
    return (uint32_t)(now_ms() - s->last_ms) < SRGB_TIMEOUT_MS;
}

/* Replaces bl srgb_render(effect) at 0x0801017C (rgb_matrix_task, RENDERING). */
void srgb_render2(uint32_t effect)
{
    if (REG32(SRGB_STATE_ADDR + V1_OFF_MAGIC) == SRGB_MAGIC && radio_should_render(RADIO)) {
        /* v1's "skip the effect" action. Setting v1's rendering flag makes
         * v1's switch-back clear the frame and re-init the effect once the
         * radio stops (timeout, 0x26, mode change, RGB off). */
        REG8(SRGB_STATE_ADDR + V1_OFF_RENDERING) = 1;
        RGB_BLOCK(RGB_OFF_ITER) = (uint8_t)(RGB_BLOCK(RGB_OFF_ITER) + 1u);
        RGB_BLOCK(RGB_OFF_TASK_STATE) = RGB_TASK_FLUSHING;
        return;
    }
    FW_FN(render_fn, V1_SRGB_RENDER)(effect);
}
