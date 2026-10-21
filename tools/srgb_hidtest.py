"""HID test for the SignalRGB patch on the Zoom75 TIGA (patched stock firmware v1.1).

Talks only to the keyboard's raw HID collection (usage page 0xFF60, usage 0x61) on
one of two known devices, and only with SignalRGB commands (plus VIA 0x01 with
--via-probe). Every packet sent and received is printed and written to logs/.

  Targets (--vid/--pid; any other pair is refused):
    1EA7:CEDD   the keyboard on the USB cable (default). Raw HID must be interface 1.
    6D66:8888   the 2.4G receiver. Matched on usage page/usage only; exactly one
                collection must match. Needs patch v2 for SignalRGB (v1 answers 0xFF).
  Before opening, Windows' parsed report descriptor (HidP_GetCaps, query-only handle,
  sends nothing) must show 33-byte input and output reports with report ID 0.

  (no flag)   Read-only: sends the queries 0x21 0x22 0x23 0x27 0x28 and checks each
              reply byte for byte against PHASE2_REPORT.md section 1.
  --list      Lists the device's HID interfaces with their report lengths, and exits.
              Sends no reports.
  --via-probe Instead of the read-only checks: VIA 0x01 (get protocol version) about
              --via-count times (round-trip baseline), then 0x21 0x22 0x27 0x28, each
              between two 0x01. Classifies each reply: SignalRGB answer, VIA 'unhandled'
              (0xFF + echo), no reply, or other. With --marker-probe, the only ways this
              script sends 0x01.
  --marker-probe  Is the radio payload 32 bytes? Sends 0x01, then 0x21 with byte 31 =
              0x5A, then 0x01 (3 packets, read-only). Informative on patch v1 only: v1
              echoes the 0x21 (0xFF + echo), so the marker comes back iff n = 32. Patch v2
              answers 0x21 itself in 2.4G mode, zero-filled, so the marker never returns.
  --led       After the read-only checks pass, runs the LED tests (adds 0x24 0x25 0x26):
                errors   0x24 with bad start/count -> error replies 253 / 254
                predrop  0x26, then a red stream for 2 s: must be ignored (mode off)
                colors   0x25, solid red / green / blue
                bench    moving rainbow as fast as possible: frame rate, tearing
                walk     LEDs 0..85 one at a time, white
                timeout  stop streaming: normal effect after ~2 s; resume without 0x25
              They start with 0x26 (known state) and end with 0x26. 0x26 is also sent
              on any error or Ctrl+C once 0x25 went out. On the receiver they start with
              0x25 <quiet bit> then 0x26, which sets patch v2's quiet switch to a known
              value. Over the radio patch v2 echoes every 0x24 / 0x25 / 0x26 unless quiet
              is on: the script expects each echo, checks it, and reports missing ones.
  --quiet     Send 0x25 as 25 01 (patch v2 quiet switch: no reply line for successful
              0x24, 0x25, 0x26 over the radio). No effect on USB.
  --steps a,b Run only these LED steps (names above), e.g. --steps walk. Extra steps,
              not in the default list: hold (orange/purple for --hold-seconds, for
              testing mode switches and unplugging by hand); pace (below)
  --walk-leds 28,29,61   Walk only these indices (ranges allowed: 15-30)
  --walk-delay S         Seconds per LED in the walk (default 1.0)
  --hold-seconds S       Length of the hold step (default 60)
  --packet-gap MS        Minimum time between the starts of two packets, every LED step
              (default 0 = back to back, about 1 ms per packet). On the receiver a packet
              that follows a 0x25 or 0x26 always waits at least 100 ms, so the known-state
              prelude and each mode switch reach the keyboard one packet at a time.

  pace (receiver only; --led --steps pace): how far apart must radio packets be?
              For each gap in --pace-gaps (ms between the starts of two packets) it
              sends --pace-frames solid frames of 10 packets, a different colour per
              gap, and after each frame waits up to --pace-settle ms for its echoes.
              Bytes 30-31 (padding the firmware ignores, echoed unchanged) tag the frame,
              so every echo is matched to its frame and packet position. Reports per gap:
              frames whose packet 1..10 was echoed, TX spacing, burst and frame time, then
              the smallest loss-free gap and the full-frame rate it allows. Last, one
              diagnostic row (WHITE, --pace-diag-gap): packets with no 00 byte except
              packet 1's start. The stock radio parser uses C strings (strstr stops at 00),
              so if the loss is merged lines in one UART chunk, more than packet 1 arrives
              in this row; if the loss is upstream, only packet 1 does.
              With --quiet there are no echoes: in each frame one packet (rotating slot)
              is replaced by the query 0x27, whose reply is always sent; the replies per
              slot are the measurement.
  soak (receiver only; --led --steps soak, --packet-gap 75 or more): a long run at the
              paced rate, every reply checked. --soak-packets solid frames of tagged 0x24
              (tags as in pace), the colour cycling once per frame. Quiet off: each echo is
              compared with its packet. Quiet on: the keyboard answers a colour packet only
              if it arrived damaged (24 FD / FE for a bad start/count, a VIA reply for a bad
              byte 0), so any reply proves damage on the way TO the keyboard; a 0x22 query
              after every --soak-probe-every frames measures loss. Each unexpected reply is
              classified: down (only a packet damaged on the way to the keyboard explains
              it), up (only a reply damaged on the way back does), either (an echo is a copy:
              cannot tell), quiet-off, late, unknown; with the bytes that differ.
  partial (receiver only; --led --steps partial, --packet-gap 75 or more): the 2.4G pacing
              planned for the plugin (GroupScheduler): at most one packet per 16 ms tick, at
              least 80 ms apart; a group not sent for 2 x --refresh-ms first, then changed
              9-LED groups (round robin), then, when nothing changed, a refresh of the group
              sent longest ago once --refresh-ms (default 1500) old. Phases to watch
              (--partial-phases, default all): static (green, refresh only), dot (one white
              key walking all 86 LEDs, the others off), fn (blue while you hold Fn and press
              Caps Lock: indicators must clear), wipe (full-board colour changes).
  idle (receiver only; --led --steps idle alone, --packet-gap 75 or more): step 7, power.
              --idle-lead S of the partial pacing (whole board magenta / cyan, alternating
              every 3 s; default 10, 0 = none, and then no 0x25 either) with NO 0x26 after
              it, so the firmware's 2 s timeout must end SignalRGB mode; then NOTHING is sent
              for --idle-silent S (default 840); then one 0x22 every --idle-probe-every S
              (default 5) for --idle-probe-for S (default 300), each answered or lost, with
              wall-clock times (press a key during the probes to see what wakes it). Lost
              probes are the measurement (a sleeping keyboard does not answer), not a FAIL.
  --pace-gaps 1,2,3,5,8,12,20,30   --pace-frames 20   --pace-settle 300
  --pace-diag-gap MS     Gap of the diagnostic row (default 1; 0 = no diagnostic row)
  --pace-mode-off        Run the pace step with SignalRGB mode OFF (no 0x25 before it): patch
              v2 drops the colours but still echoes every valid 0x24, so the LEDs keep the
              normal effect and the echoes measure the link in the keyboard's normal state.
              Only with --steps pace alone.

Close VIA, the Meletrix Console, SignalRGB and both Wuque tools first: they use the
same interface (the Wuque tools lock it exclusively).

usage: python -I tools/srgb_hidtest.py [--list | --led [--quiet] [--steps ...] [--walk-leds ...]]
       python -I tools/srgb_hidtest.py --vid 0x6D66 --pid 0x8888
                                       [--list | --via-probe | --marker-probe | --led [--quiet] ...]
       python -I tools/srgb_hidtest.py --vid 0x6D66 --pid 0x8888 --led --steps pace [--quiet]
       python -I tools/srgb_hidtest.py --vid 0x6D66 --pid 0x8888 --led --steps soak|partial
                                       --packet-gap 80 [--quiet] [--soak-packets N]
       python -I tools/srgb_hidtest.py --vid 0x6D66 --pid 0x8888 --led --steps idle --packet-gap 80
                                       --quiet [--idle-lead S --idle-silent S ...]
"""
import argparse
import collections
import colorsys
import datetime as dt
import json
import sys
import time
from pathlib import Path

import hid

ROOT = Path(__file__).resolve().parent.parent
LAYOUT_JSON = ROOT / "plugin" / "tiga_layout.json"
LOG_DIR = ROOT / "logs"

VID, PID = 0x1EA7, 0xCEDD
USAGE_PAGE, USAGE, INTERFACE = 0xFF60, 0x61, 1
RECEIVER_VID, RECEIVER_PID = 0x6D66, 0x8888
# The only devices this script may open. interface: required raw HID interface number,
# or None (unknown: match on usage page/usage only, exactly one match required).
DEVICES = {
    (VID, PID): dict(name="Zoom75 TIGA, USB cable", interface=INTERFACE, radio=False, tag=""),
    (RECEIVER_VID, RECEIVER_PID): dict(name="2.4G receiver", interface=None, radio=True,
                                       tag="rx_"),
}
REPORT_LEN = 32                     # raw HID report; on the wire 0x00 report ID + 32
N_LEDS = 86
LEDS_PER_PACKET = 9
TIMEOUT_MS = 2000                   # firmware fallback (SRGB_TIMEOUT_MS)

CMD_QMK_VERSION = 0x21
CMD_PROTOCOL_VERSION = 0x22
CMD_UNIQUE_ID = 0x23
CMD_STREAM = 0x24
CMD_MODE_ON = 0x25
CMD_MODE_OFF = 0x26
CMD_TOTAL_LEDS = 0x27
CMD_FW_TYPE = 0x28

READ_ONLY_CMDS = frozenset({CMD_QMK_VERSION, CMD_PROTOCOL_VERSION, CMD_UNIQUE_ID,
                            CMD_TOTAL_LEDS, CMD_FW_TYPE})
LED_CMDS = frozenset({CMD_STREAM, CMD_MODE_ON, CMD_MODE_OFF})
assert READ_ONLY_CMDS | LED_CMDS == set(range(0x21, 0x29))
VIA_GET_PROTOCOL_VERSION = 0x01     # read-only; only with --via-probe, body all zero
VIA_PROBE_CMDS = frozenset({VIA_GET_PROTOCOL_VERSION, CMD_QMK_VERSION, CMD_PROTOCOL_VERSION,
                            CMD_TOTAL_LEDS, CMD_FW_TYPE})
MARKER_PROBE_CMDS = frozenset({VIA_GET_PROTOCOL_VERSION, CMD_QMK_VERSION})
MARKER = 0x5A                       # --marker-probe: byte 31 of the 0x21 request
MAX_ALLOWED = READ_ONLY_CMDS | LED_CMDS | {VIA_GET_PROTOCOL_VERSION}
QUIET_BIT = 0x01                    # 0x25 byte 1 bit 0: patch v2 radio quiet switch
VIA_PROTOCOL_REPLY = bytes([VIA_GET_PROTOCOL_VERSION, 0x00, 0x0B])   # VIA protocol 0x000B

# Expected replies, PHASE2_REPORT.md section 1 (rest of the 32 bytes zero).
EXPECTED = {
    CMD_QMK_VERSION: bytes([0x21, 0, 0, 0]),
    CMD_PROTOCOL_VERSION: bytes([0x22, 1, 0, 6]),
    CMD_UNIQUE_ID: bytes([0x23, 0, 0, 0]),
    CMD_TOTAL_LEDS: bytes([0x27, 86, 0, 0]),
    CMD_FW_TYPE: bytes([0x28, 2, 0, 0]),
}
ERROR_LED_BOUNDS, ERROR_LED_COUNT = 253, 254
LED_STEPS = ("errors", "predrop", "colors", "bench", "walk", "timeout", "hold", "pace", "soak", "partial",
             "idle")
DEFAULT_STEPS = LED_STEPS[:6]
RADIO_ONLY_STEPS = ("pace", "soak", "partial", "idle")
RADIO_CMD_GAP_MS = 100              # receiver: minimum gap after a 0x25 / 0x26

# pace step (receiver only)
PACE_GAPS_MS = (1, 2, 3, 5, 8, 12, 20, 30)
PACE_FRAMES = 20
PACE_SETTLE_MS = 300
PACE_DIAG_GAP_MS = 1
PACE_PACKETS = (N_LEDS + LEDS_PER_PACKET - 1) // LEDS_PER_PACKET     # 10
# One colour per gap. Each has a zero channel, so every packet holds 00 bytes as in the
# 2026-10-21 hardware run; the diagnostic row is the only one without them.
PACE_COLORS = (("RED", (255, 0, 0)), ("GREEN", (0, 255, 0)), ("BLUE", (0, 0, 255)),
               ("YELLOW", (255, 255, 0)), ("CYAN", (0, 255, 255)), ("MAGENTA", (255, 0, 255)),
               ("ORANGE", (255, 80, 0)), ("PURPLE", (140, 0, 255)))
PACE_DIAG_COLOR = ("WHITE", (255, 255, 255))
PACE_MARKER_CMD = CMD_TOTAL_LEDS    # --quiet: the query that replaces one packet per frame
PACE_MARKER_REPLY = EXPECTED[CMD_TOTAL_LEDS] + bytes(REPORT_LEN - 4)


class SafetyError(Exception):
    pass


class Log:
    """Prints every line and appends it to a log file. hold() buffers the console
    output (not the timing) so a benchmark is not slowed down by printing."""

    def __init__(self, path):
        self.t0 = time.perf_counter()
        self.file = open(path, "w", encoding="utf-8", newline="\n")
        self.held = None
        self.path = path

    def now_ms(self):
        return (time.perf_counter() - self.t0) * 1000.0

    def line(self, text):
        self.file.write(text + "\n")
        if self.held is not None:
            self.held.append(text)
        else:
            print(text, flush=True)

    def info(self, text=""):
        self.line(text)

    def packet(self, direction, data, t_ms=None):
        t_ms = self.now_ms() if t_ms is None else t_ms
        self.line(f"{t_ms:10.1f} ms {direction} {bytes(data).hex(' ')}")

    def hold(self):
        self.held = []

    def release(self, echo=True):
        """echo=False: the held lines stay in the log file only."""
        held, self.held = self.held, None
        for text in (held or ()) if echo else ():
            print(text)
        sys.stdout.flush()

    def close(self):
        self.file.close()


def vendor_crc(payload):
    """CRC16-CCITT-FALSE (poly 0x1021, init 0xFFFF) over the 32 bytes with bytes 6-7
    zeroed: the vendor frame check of the keyboard (0x0800AC7C) and of the receiver
    updater (analysis/wuque_dfu_v11_decomp/UBEST_DFU_TOOL/CRC16.cs, stored low byte
    first in bytes 6-7)."""
    c = 0xFFFF
    for b in payload[:6] + b"\0\0" + payload[8:]:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def is_vendor_frame(payload):
    return vendor_crc(payload) == payload[6] | payload[7] << 8


def stream_valid(payload):
    """The firmware's validity rule for a 32-byte 0x24 packet (no error reply)."""
    start, count = payload[1], payload[2]
    return start + count <= N_LEDS and count <= LEDS_PER_PACKET and 3 + 3 * count <= REPORT_LEN


def wait_until(t_next):
    """Wait until time.perf_counter() >= t_next. time.sleep overshoots by up to ~0.6 ms
    on Windows, so sleep only while more than 2 ms remain, then spin."""
    while (left := t_next - time.perf_counter()) > 0:
        if left > 0.002:
            time.sleep(left - 0.0015)


class Link:
    """The only path to the device. Refuses anything outside the allowed commands.

    radio=True (the receiver): patch v2 echoes every successful 0x24 and every 0x25 /
    0x26 unless its quiet switch is on (set by 0x25 byte 1 bit 0). The Link predicts
    each echo, removes it from what recv()/drain() return, and counts echoes seen and
    missing. Anything else that arrives is returned as before. on_echo(data, t_ms), if
    set, is called for every echo taken.

    Pacing: a packet starts at least gap_ms after the start of the previous one
    (--packet-gap; send() can override it). On the radio a packet that follows a 0x25
    or 0x26 waits at least RADIO_CMD_GAP_MS."""

    def __init__(self, dev, log, allowed, radio=False, gap_ms=0.0):
        if not allowed <= MAX_ALLOWED:
            raise SafetyError("allowed set exceeds 0x01 + 0x21-0x28")
        self.dev = dev
        self.log = log
        self.allowed = frozenset(allowed)
        self.sent = 0
        self.mode_on_sent = False
        self.radio = radio
        self.gap_ms = gap_ms
        self.t_last = None              # perf_counter() at the start of the last write
        self.last_cmd = None
        self.quiet = None               # unknown until the first 0x25
        self.pending = []               # echoes still expected, oldest first
        self.echoes = dict(expected=0, seen=0, missing=0)
        self.on_echo = None

    def send(self, payload, gap_ms=None):
        """Returns the log time (ms) at which the write started."""
        payload = bytes(payload)
        if len(payload) != REPORT_LEN:
            raise SafetyError(f"payload must be {REPORT_LEN} bytes, got {len(payload)}")
        if payload[0] not in self.allowed:
            raise SafetyError(f"refusing to send command 0x{payload[0]:02X}")
        if payload[0] == VIA_GET_PROTOCOL_VERSION and any(payload[1:]):
            raise SafetyError("VIA 0x01 must have an all-zero body")
        # Stream packets carry colours in bytes 6-7 by design; every other packet has a
        # fixed body and must not look like a CRC-framed vendor command.
        if payload[0] != CMD_STREAM and is_vendor_frame(payload):
            raise SafetyError(f"refusing 0x{payload[0]:02X}: bytes 6-7 form a valid vendor CRC")
        gap = self.gap_ms if gap_ms is None else gap_ms
        if self.radio and self.last_cmd in (CMD_MODE_ON, CMD_MODE_OFF):
            gap = max(gap, RADIO_CMD_GAP_MS)
        if gap > 0 and self.t_last is not None:
            wait_until(self.t_last + gap / 1000.0)
        self.t_last = time.perf_counter()
        t = self.log.now_ms()
        n = self.dev.write(b"\x00" + payload)
        ok = n == REPORT_LEN + 1
        self.log.packet("TX" if ok else "TX FAILED (not sent)", payload, t)
        if not ok:
            raise IOError(f"write returned {n}")
        self.sent += 1
        self.last_cmd = payload[0]
        if payload[0] == CMD_MODE_ON:
            self.mode_on_sent = True
        elif payload[0] == CMD_MODE_OFF:
            self.mode_on_sent = False
        if self.radio:
            echo = False
            if payload[0] == CMD_MODE_ON:
                self.quiet = bool(payload[1] & QUIET_BIT)
                echo = not self.quiet
            elif payload[0] == CMD_MODE_OFF:
                echo = self.quiet is False
            elif payload[0] == CMD_STREAM:
                echo = stream_valid(payload) and self.quiet is False
            if echo:
                self.pending.append(payload)
                self.echoes["expected"] += 1
        return t

    def _take_echo(self, data, t_ms):
        """True if data is an expected echo (consumed). Echoes skipped over count as missing."""
        if not self.radio or data not in self.pending:
            return False
        i = self.pending.index(data)
        self.echoes["missing"] += i
        self.echoes["seen"] += 1
        del self.pending[:i + 1]
        if self.on_echo is not None:
            self.on_echo(data, t_ms)
        return True

    def recv(self, timeout_ms):
        """One input report that is not an expected echo, or None after timeout_ms
        (>= 1; 0 would block)."""
        end = time.perf_counter() + max(1, int(timeout_ms)) / 1000.0
        while True:
            left_ms = (end - time.perf_counter()) * 1000.0
            data = self.dev.read(64, max(1, int(left_ms)))
            if not data:
                return None
            data = bytes(data)
            t = self.log.now_ms()
            if self._take_echo(data, t):
                self.log.packet("RX echo", data, t)
                if time.perf_counter() >= end:
                    return None
                continue
            self.log.packet("RX", data, t)
            return data

    def settle(self, timeout_ms=400, until=None):
        """Radio: wait up to timeout_ms for the outstanding echoes; the rest count as
        missing. Returns any other packets that arrived meanwhile. until(got), if given,
        replaces "no echo outstanding" as the condition to stop early."""
        got = []
        end = time.perf_counter() + timeout_ms / 1000.0
        while (left := end - time.perf_counter()) > 0:
            if (not self.pending) if until is None else until(got):
                break
            if (p := self.recv(max(1, left * 1000.0))) is not None:
                got.append(p)
        self.echoes["missing"] += len(self.pending)
        self.pending = []
        return got

    def drain(self):
        """Read and print anything already waiting. Returns the packets."""
        got = []
        while (p := self.recv(1)) is not None:
            got.append(p)
            if len(got) > 64:
                raise IOError("device keeps sending; is another program using it?")
        return got


def cmd_packet(cmd, *body):
    p = bytearray(REPORT_LEN)
    p[0] = cmd
    p[1:1 + len(body)] = bytes(body)
    return bytes(p)


def stream_packets(frame):
    """frame: list of 86 (r, g, b). Yields 10 packets [0x24, start, count, rgb...]."""
    for start in range(0, N_LEDS, LEDS_PER_PACKET):
        count = min(LEDS_PER_PACKET, N_LEDS - start)
        rgb = [c for px in frame[start:start + count] for c in px]
        yield cmd_packet(CMD_STREAM, start, count, *rgb)


def hid_caps(path):
    """Report lengths and report IDs of one HID collection, from Windows' parsed report
    descriptor (HidD_GetPreparsedData + HidP_GetCaps/GetValueCaps/GetButtonCaps). The
    handle is opened with access 0 (query only): no reads, no writes, nothing reaches
    the device's endpoints. Returns a dict, or {'error': text}."""
    if sys.platform != "win32":
        return {"error": "not Windows"}
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hdll = ctypes.WinDLL("hid")
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    hdll.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    hdll.HidD_GetPreparsedData.restype = wintypes.BOOLEAN
    hdll.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    hdll.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    hdll.HidP_GetCaps.restype = ctypes.c_long
    for fn in (hdll.HidP_GetValueCaps, hdll.HidP_GetButtonCaps):
        fn.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort), ctypes.c_void_p]
        fn.restype = ctypes.c_long
    HIDP_STATUS_SUCCESS = 0x00110000
    FILE_SHARE_READ_WRITE, OPEN_EXISTING = 0x3, 3
    invalid = wintypes.HANDLE(-1).value

    p = path.decode() if isinstance(path, bytes) else path
    h = k32.CreateFileW(p, 0, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None)
    if h in (None, invalid):
        return {"error": f"CreateFile (query-only) failed, Windows error {ctypes.get_last_error()}"}
    try:
        pp = ctypes.c_void_p()
        if not hdll.HidD_GetPreparsedData(h, ctypes.byref(pp)):
            return {"error": "HidD_GetPreparsedData failed"}
        try:
            # HIDP_CAPS as 32 USHORTs: [0] Usage, [1] UsagePage, [2..4] In/Out/Feature
            # ReportByteLength, [5..21] Reserved, [22] link nodes, then for input [23..25],
            # output [26..28], feature [29..31]: button caps, value caps, data indices.
            caps = (ctypes.c_ushort * 32)()
            if hdll.HidP_GetCaps(pp, caps) != HIDP_STATUS_SUCCESS:
                return {"error": "HidP_GetCaps failed"}
            out = dict(usage=caps[0], usage_page=caps[1], input_len=caps[2], output_len=caps[3],
                       feature_len=caps[4], fields=[])
            counts = {0: (caps[23], caps[24]), 1: (caps[26], caps[27]), 2: (caps[29], caps[30])}
            for rtype, (n_btn, n_val) in counts.items():
                for fn, n, kind in ((hdll.HidP_GetButtonCaps, n_btn, "button"),
                                    (hdll.HidP_GetValueCaps, n_val, "value")):
                    if not n:
                        continue
                    buf = (ctypes.c_ubyte * (72 * n))()     # HIDP_*_CAPS are 72 bytes each
                    cnt = ctypes.c_ushort(n)
                    if fn(rtype, buf, ctypes.byref(cnt), pp) != HIDP_STATUS_SUCCESS:
                        return {"error": f"HidP_Get{kind.title()}Caps failed"}
                    for i in range(cnt.value):
                        e = bytes(buf[72 * i:72 * (i + 1)])
                        f = dict(type=("input", "output", "feature")[rtype], kind=kind,
                                 report_id=e[2])
                        if kind == "value":     # BitSize at 18, ReportCount at 20, Usage at 56
                            f.update(bits=int.from_bytes(e[18:20], "little"),
                                     count=int.from_bytes(e[20:22], "little"),
                                     usage=int.from_bytes(e[56:58], "little"))
                        out["fields"].append(f)
            return out
        finally:
            hdll.HidD_FreePreparsedData(pp)
    finally:
        k32.CloseHandle(h)


def format_caps(caps):
    if "error" in caps:
        return f"report lengths unavailable: {caps['error']}"
    ids = sorted({f["report_id"] for f in caps["fields"]})
    fields = ", ".join(
        f"{f['type']} {f['kind']}" + (f" usage 0x{f['usage']:02X} {f['count']}x{f['bits']} bit"
                                      if f["kind"] == "value" else "")
        for f in caps["fields"])
    return (f"input {caps['input_len']} B, output {caps['output_len']} B, feature "
            f"{caps['feature_len']} B (with report ID byte); report IDs {ids}; {fields}")


def check_caps(caps, target, log):
    """The script writes report ID 0 + 32 bytes and expects 32-byte replies."""
    if "error" in caps:
        if target["radio"]:
            raise SafetyError(f"cannot read the receiver's report lengths ({caps['error']}); "
                              "refusing to send")
        log.info(f"  note: {format_caps(caps)}")
        return
    ids = {f["report_id"] for f in caps["fields"]}
    want = REPORT_LEN + 1
    if caps["input_len"] != want or caps["output_len"] != want or ids != {0}:
        raise SafetyError(f"raw HID collection is not 32-byte reports with report ID 0 "
                          f"({format_caps(caps)}); refusing to send")


def find_interface(log, target=None):
    target = target or dict(vid=VID, pid=PID, usage_page=USAGE_PAGE, usage=USAGE,
                            **DEVICES[(VID, PID)])
    vid, pid, interface = target["vid"], target["pid"], target["interface"]
    usage_page, usage = target["usage_page"], target["usage"]
    devs = hid.enumerate(vid, pid)
    log.info(f"HID interfaces of {vid:04X}:{pid:04X} ({target['name']}): {len(devs)}")
    for d in devs:
        log.info(f"  interface {d['interface_number']}  usage page 0x{d['usage_page']:04X}"
                 f"  usage 0x{d['usage']:02X}  {d['product_string']!r}  {d['path'].decode(errors='replace')}")
        if target.get("list_caps"):
            log.info(f"      {format_caps(hid_caps(d['path']))}")
    raw = [d for d in devs if d["usage_page"] == usage_page and d["usage"] == usage]
    if interface is not None and devs and not raw and not any(
            d["interface_number"] == interface for d in devs):
        log.info(f"  interface {interface} is not listed. hidapi skips interfaces it cannot open: "
                 "is another program holding it exclusively? WuqueDFUupgradeProgrammer does "
                 "while it is open (CreateFile share mode 0). Close it, VIA, the Console and "
                 "SignalRGB, then retry.")
    if interface is None and devs and not raw:
        log.info(f"  no 0x{usage_page:04X}/0x{usage:02X} collection listed. hidapi skips "
                 "collections it cannot open: is another program holding it exclusively "
                 "(e.g. Wuque_DFU_TOOL, which must not be used)? Close it, VIA, the Console "
                 "and SignalRGB, then retry.")
    if len(raw) != 1:
        raise SafetyError(f"expected exactly one raw HID interface (0x{usage_page:04X}/0x{usage:02X}), "
                          f"found {len(raw)}")
    if interface is not None and raw[0]["interface_number"] != interface:
        raise SafetyError(f"raw HID is on interface {raw[0]['interface_number']}, expected {interface}")
    if interface is None:
        log.info(f"  matched interface {raw[0]['interface_number']} by usage page and usage")
    return raw[0]


# ---- Read-only checks ---------------------------------------------------------

def read_only_checks(link, log, radio=False):
    log.info("\n== Read-only queries (expected values: PHASE2_REPORT.md section 1) ==")
    stale = link.drain()
    if stale:
        log.info(f"  note: {len(stale)} stale packet(s) were waiting before the first query")
    ok = True
    for cmd in (CMD_QMK_VERSION, CMD_PROTOCOL_VERSION, CMD_UNIQUE_ID, CMD_TOTAL_LEDS, CMD_FW_TYPE):
        link.drain()
        t0 = time.perf_counter()
        link.send(cmd_packet(cmd))
        reply = link.recv(250)
        rtt = (time.perf_counter() - t0) * 1000.0
        extra = link.recv(40)            # the patch sends exactly one reply
        want = EXPECTED[cmd] + bytes(REPORT_LEN - 4)
        if reply is None:
            log.info(f"  0x{cmd:02X}: FAIL, no reply within 250 ms")
            ok = False
        elif reply != want:
            hint = ""
            if reply[0] == 0xFF and radio:
                hint = (" (0xFF = VIA 'unhandled command': the keyboard's radio path has no "
                        "SignalRGB handler, as expected with patch v1)")
            elif reply[0] == 0xFF:
                hint = " (0xFF = VIA 'unhandled command': this looks like the stock firmware)"
            log.info(f"  0x{cmd:02X}: FAIL, got {reply[:4].hex(' ')}..., want {want[:4].hex(' ')}"
                     f" + zeros{hint}")
            ok = False
        else:
            log.info(f"  0x{cmd:02X}: ok  {reply[:4].hex(' ')} + 28 zero bytes, {rtt:.1f} ms")
        if extra is not None:
            log.info(f"  0x{cmd:02X}: FAIL, a second packet arrived: {extra[:4].hex(' ')}...")
            ok = False
    log.info("  read-only result: " + ("PASS" if ok else "FAIL"))
    return ok


# ---- VIA probe (step 2 of NEXT_SESSION_PROMPT.md) ------------------------------

PROBE_REPLY_MS = 1000               # wait for a first reply (radio latency unknown)
PROBE_SETTLE_MS = 150               # then keep listening for extra packets


def exchange(link, payload):
    """Send one packet; return (rtt_ms or None, [packets received])."""
    link.drain()
    t0 = time.perf_counter()
    link.send(payload)
    first = link.recv(PROBE_REPLY_MS)
    if first is None:
        return None, []
    rtt = (time.perf_counter() - t0) * 1000.0
    got = [first]
    end = time.perf_counter() + PROBE_SETTLE_MS / 1000.0
    while (left := end - time.perf_counter()) > 0:
        if (p := link.recv(max(1, left * 1000.0))) is not None:
            got.append(p)
    return rtt, got


def via_ok(packets):
    """One reply starting 01 00 0b. The rest should be zero (the request's body, echoed
    in place); a different tail is reported by via_probe, not treated as a failure."""
    return len(packets) == 1 and packets[0][:3] == VIA_PROTOCOL_REPLY


def classify(cmd, packets):
    request = cmd_packet(cmd)
    if not packets:
        return "none", "no reply"
    extra = "" if len(packets) == 1 else f" (+{len(packets) - 1} extra packet(s))"
    p = packets[0]
    if p[0] == 0xFF:
        echo = ("echo of the request" if p[1:] == request[1:]
                else f"NOT an exact echo: {p.hex(' ')}")
        return "unhandled", f"0xFF + {echo} = VIA 'unhandled'" + extra
    if cmd in EXPECTED and p[:4] == EXPECTED[cmd]:
        tail = "" if not any(p[4:]) else f", non-zero tail: {p.hex(' ')}"
        return "srgb", "SignalRGB answer " + p[:4].hex(" ") + tail + extra
    return "other", "unexpected: " + p.hex(" ") + extra


def via_probe(link, log, count, radio):
    log.info(f"\n== VIA 0x01 (get protocol version) x{count}: expect 01 00 0b + 29 zero bytes ==")
    stale = link.drain()
    if stale:
        log.info(f"  note: {len(stale)} stale packet(s) were waiting before the first query")
    rtts, bad = [], 0
    for _ in range(count):
        rtt, got = exchange(link, cmd_packet(VIA_GET_PROTOCOL_VERSION))
        if via_ok(got):
            rtts.append(rtt)
            if any(got[0][3:]):
                log.info(f"  0x01: note, reply tail is not zero: {got[0].hex(' ')}")
        else:
            bad += 1
            log.info(f"  0x01: FAIL, got {[p.hex(' ') for p in got] or 'no reply'}")
    if rtts:
        s = sorted(rtts)
        log.info(f"  0x01: {len(rtts)}/{count} correct; round trip min {s[0]:.1f} / median "
                 f"{s[len(s) // 2]:.1f} / max {s[-1]:.1f} ms")
    baseline = bad == 0 and bool(rtts)
    log.info("  baseline: " + ("PASS" if baseline else "FAIL"))
    if not baseline:
        log.info("  SignalRGB IDs skipped: the 0x01 baseline must pass first.")
        return False, {}

    log.info("\n== SignalRGB IDs, each between two 0x01 ==")
    outcomes = {}
    for cmd in (CMD_QMK_VERSION, CMD_PROTOCOL_VERSION, CMD_TOTAL_LEDS, CMD_FW_TYPE):
        _, before = exchange(link, cmd_packet(VIA_GET_PROTOCOL_VERSION))
        rtt, got = exchange(link, cmd_packet(cmd))
        _, after = exchange(link, cmd_packet(VIA_GET_PROTOCOL_VERSION))
        kind, text = classify(cmd, got)
        if kind == "none" and not (via_ok(before) and via_ok(after)):
            kind, text = "broken", "no reply, and 0x01 around it failed too: link problem"
        elif not (via_ok(before) and via_ok(after)):
            text += "; WARNING: a 0x01 around it failed"
        outcomes[cmd] = kind
        rtt_text = f", {rtt:.1f} ms" if rtt is not None else ""
        log.info(f"  0x{cmd:02X}: {text}{rtt_text}   [0x01 before: "
                 f"{'ok' if via_ok(before) else 'FAIL'}, after: {'ok' if via_ok(after) else 'FAIL'}]")

    kinds = set(outcomes.values())
    log.info("\n  verdict:")
    if kinds == {"unhandled"}:
        log.info("    (a) the " + ("receiver forwards" if radio else "link carries") +
                 " unknown IDs; the keyboard answers them with VIA's 'unhandled'" +
                 (" (no SignalRGB hook on its radio path)." if radio else "."))
    elif kinds == {"none"}:
        log.info("    (b) no reply to any SignalRGB ID while 0x01 before and after worked: "
                 "something between host and keyboard drops unknown IDs.")
    elif kinds == {"srgb"}:
        log.info("    SignalRGB answers: the keyboard's SignalRGB handler saw these packets" +
                 (" (not expected over the receiver with patch v1)." if radio else "."))
    else:
        log.info("    mixed or unexpected: " + ", ".join(f"0x{c:02X} {k}" for c, k in outcomes.items()))
    return True, outcomes


def marker_probe(link, log):
    """0x01, 0x21 with byte 31 = MARKER, 0x01. Shows whether all 32 bytes of a report
    reach the keyboard's raw HID handler over the radio (PATCH_V2_DESIGN.md section 7)."""
    log.info(f"\n== Marker probe: 0x01, then 0x21 with byte 31 = 0x{MARKER:02X}, then 0x01 ==")
    stale = link.drain()
    if stale:
        log.info(f"  note: {len(stale)} stale packet(s) were waiting before the first query")
    marked = bytearray(cmd_packet(CMD_QMK_VERSION))
    marked[REPORT_LEN - 1] = MARKER
    _, before = exchange(link, cmd_packet(VIA_GET_PROTOCOL_VERSION))
    rtt, got = exchange(link, bytes(marked))
    _, after = exchange(link, cmd_packet(VIA_GET_PROTOCOL_VERSION))
    around = via_ok(before) and via_ok(after)
    log.info(f"  0x01 before: {'ok' if via_ok(before) else 'FAIL'}, after: {'ok' if via_ok(after) else 'FAIL'}")
    if not got:
        verdict = "no reply to the marked 0x21" + ("" if around else " (and a 0x01 around it failed)")
    elif got[0][0] == 0xFF and got[0][1:REPORT_LEN - 1] == bytes(marked[1:REPORT_LEN - 1]):
        if got[0][REPORT_LEN - 1] == MARKER:
            verdict = (f"n = 32: the echo ends with 0x{MARKER:02X}, so all 32 bytes reach the "
                       "keyboard (VIA 'unhandled' echo, patch v1 radio path)")
        else:
            verdict = (f"n < 32: the echo ends with 0x{got[0][REPORT_LEN - 1]:02X}, not 0x{MARKER:02X}; "
                       "fewer than 32 bytes reach the keyboard")
    elif got[0][:4] == EXPECTED[CMD_QMK_VERSION]:
        verdict = ("SignalRGB answer 21 00 00 00: patch v2 handled it (2.4G mode) and zero-filled "
                   "the reply, so the marker cannot come back. Informative on patch v1 only")
    else:
        verdict = "unexpected reply: " + got[0].hex(" ")
    if len(got) > 1:
        verdict += f" (+{len(got) - 1} extra packet(s))"
    log.info(f"  marked 0x21: {got[0].hex(' ') if got else 'no reply'}"
             + (f", {rtt:.1f} ms" if rtt is not None else ""))
    log.info(f"  verdict: {verdict}")
    link.verdict = verdict
    return around and bool(got)


# ---- LED tests ----------------------------------------------------------------

def solid(rgb):
    return [rgb] * N_LEDS


def hold_frame(link, frame, seconds, period_s=0.1):
    """Stream the same frame for `seconds`, re-sending every period_s (keep-alive,
    well inside the 2 s timeout). Returns any packets the device sent meanwhile
    (there should be none)."""
    got = []
    end = time.perf_counter() + seconds
    while True:
        for p in stream_packets(frame):
            link.send(p)
        got += link.drain()
        left = end - time.perf_counter()
        if left <= period_s:
            time.sleep(max(left, 0.0))
            return got
        time.sleep(period_s)


def say(log, text):
    log.info(f"\n>> {text}")


def step_errors(link, log):
    say(log, "errors: bad 0x24 packets, no visible change expected")
    ok = True
    cases = [
        ((85, 2), ERROR_LED_BOUNDS, "start 85 + count 2 > 86"),
        ((80, 9), ERROR_LED_BOUNDS, "start 80 + count 9 > 86"),
        ((0, 10), ERROR_LED_COUNT, "count 10 > 9"),
        ((77, 9), None, "start 77 + count 9 = 86, valid (dropped: mode off)"),
    ]
    for (start, count), want, what in cases:
        link.drain()
        body = [start, count] + [0] * min(3 * count, REPORT_LEN - 3)
        link.send(cmd_packet(CMD_STREAM, *body[:REPORT_LEN - 1]))
        reply = link.recv(150)
        if want is None:
            good = reply is None
            log.info(f"  {what}: {'ok, no reply' if good else 'FAIL, unexpected reply'}")
        else:
            exp = bytes([CMD_STREAM, want]) + bytes(REPORT_LEN - 2)
            good = reply == exp
            log.info(f"  {what}: " + (f"ok, reply 24 {want:02x}" if good else
                                     f"FAIL, got {reply[:4].hex(' ') if reply else 'nothing'}"))
        ok &= good
    return ok


def step_predrop(link, log):
    say(log, "predrop: mode is off, streaming RED for 2 s. LOOK: the keyboard must keep "
             "its normal effect (no red)")
    got = hold_frame(link, solid((255, 0, 0)), 2.0)
    if got:
        log.info(f"  FAIL: {len(got)} unexpected packet(s)")
    return not got


def step_colors(link, log, seconds=3.0):
    ok = True
    for name, rgb in (("RED", (255, 0, 0)), ("GREEN", (0, 255, 0)), ("BLUE", (0, 0, 255))):
        say(log, f"colors: whole board {name} for {seconds:.0f} s")
        got = hold_frame(link, solid(rgb), seconds)
        if got:
            log.info(f"  FAIL: {len(got)} unexpected packet(s)")
            ok = False
    return ok


def rainbow_frame(layout_x, phase):
    out = []
    for x in layout_x:
        r, g, b = colorsys.hsv_to_rgb((x / 31.0 + phase) % 1.0, 1.0, 1.0)
        out.append((int(r * 255), int(g * 255), int(b * 255)))
    return out


def step_bench(link, log, layout_x, seconds=5.0):
    say(log, f"bench: moving rainbow, as fast as possible for {seconds:.0f} s. LOOK for "
             "tearing (keys briefly out of step with their neighbours). Packet lines are "
             "printed after the run so printing does not slow it down")
    log.hold()
    frames = 0
    worst_ms = 0.0
    got = []
    t0 = time.perf_counter()
    try:
        while (t := time.perf_counter()) - t0 < seconds:
            fs = time.perf_counter()
            for p in stream_packets(rainbow_frame(layout_x, (t - t0) * 0.5)):
                link.send(p)
            worst_ms = max(worst_ms, (time.perf_counter() - fs) * 1000.0)
            frames += 1
            if frames % (1 if link.radio else 10) == 0:     # radio: echoes fill the input buffer
                got += link.drain()
        elapsed = time.perf_counter() - t0
    finally:
        log.release()
    fps = frames / elapsed
    log.info(f"  {frames} frames in {elapsed:.2f} s = {fps:.1f} frames/s, "
             f"{fps * 10:.0f} packets/s, slowest frame {worst_ms:.1f} ms")
    if got:
        log.info(f"  FAIL: {len(got)} unexpected packet(s)")
    return not got


def step_walk(link, log, names, leds, delay):
    say(log, f"walk: one white LED at a time, {delay:.1f} s each. Note any LED whose key "
             "does not match the expected name")
    ok = True
    for i in leds:
        frame = solid((0, 0, 0))
        frame[i] = (255, 255, 255)
        log.info(f"  LED {i:2}  expected: {names[i] if names else '?'}")
        got = hold_frame(link, frame, delay, period_s=0.5)
        if got:
            log.info(f"  FAIL: {len(got)} unexpected packet(s)")
            ok = False
    return ok


def step_timeout(link, log):
    say(log, "timeout: MAGENTA for 2 s, then the script stops sending")
    got = hold_frame(link, solid((255, 0, 255)), 2.0)
    t_stop = log.now_ms()
    say(log, f"stopped streaming at {t_stop:.0f} ms. LOOK: the normal effect should "
             f"return at about {t_stop + TIMEOUT_MS:.0f} ms (~2 s). Waiting 5 s, sending nothing")
    t_end = time.perf_counter() + 5.0
    while time.perf_counter() < t_end:
        if (p := link.recv(100)) is not None:
            got.append(p)
    say(log, "resume: CYAN for 3 s WITHOUT re-sending 0x25. LOOK: cyan should appear at once")
    got += hold_frame(link, solid((0, 255, 255)), 3.0)
    if got:
        log.info(f"  FAIL: {len(got)} unexpected packet(s)")
    return not got


def step_hold(link, log, seconds):
    say(log, f"hold: ORANGE / PURPLE, switching every second, for {seconds:.0f} s. "
             "Meanwhile try: Fn mode keys to BT or 2.4G and back to USB (expected: normal "
             "effect in wireless, orange/purple again on USB), or unplug (ends the script)")
    got = []
    end = time.perf_counter() + seconds
    k = 0
    while time.perf_counter() < end:
        rgb = (255, 80, 0) if k % 2 == 0 else (140, 0, 255)
        got += hold_frame(link, solid(rgb), min(1.0, max(0.0, end - time.perf_counter())))
        k += 1
    if got:
        log.info(f"  FAIL: {len(got)} unexpected packet(s)")
    return not got


# ---- Pace step (receiver) -------------------------------------------------------

MAX_PACE_TAGS = 4095


def pace_packets(rgb, tag, nulfree):
    """The 10 packets of a solid frame, with tag 1..4095 in bytes 30-31 (padding: the
    firmware reads 3 + 3 * count bytes, and echoes a valid 0x24 unchanged). Tag bytes
    are 0xC0-0xDF / 0x80-0xFF. nulfree: the rest of the padding is 0xFF too, so the
    only 00 byte left is packet 1's start byte. No packet is a valid vendor CRC frame:
    if one would be, bit 5 of byte 30 is flipped (a one-bit change always changes the
    CRC, and no plain tag sets that bit, so tags stay unique)."""
    if not 1 <= tag <= MAX_PACE_TAGS:
        raise SafetyError(f"pace tag {tag} out of range")
    out = []
    for p in stream_packets(solid(rgb)):
        p = bytearray(p)
        if nulfree:
            p[3 + 3 * p[2]:] = b"\xFF" * (REPORT_LEN - 3 - 3 * p[2])
        p[30], p[31] = 0xC0 | (tag >> 7 & 0x1F), 0x80 | (tag & 0x7F)
        if is_vendor_frame(p):
            p[30] ^= 0x20
        if is_vendor_frame(p) or not stream_valid(p):
            raise SafetyError("pace packet failed its own check")
        out.append(bytes(p))
    return out


def _median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else float("nan")


def step_pace(link, log, gaps, frames, settle_ms, diag_gap, mode_off=False):
    quiet = bool(link.quiet)
    rows = [(float(g), *PACE_COLORS[i % len(PACE_COLORS)], False) for i, g in enumerate(gaps)]
    if diag_gap:
        rows.append((float(diag_gap), *PACE_DIAG_COLOR, True))
    if len(rows) * frames > MAX_PACE_TAGS:
        raise SafetyError("too many pace frames for the 12-bit tag")
    say(log, f"pace: {len(rows)} rows x {frames} frames of {PACE_PACKETS} packets, one colour per "
             f"gap. After each frame: wait up to {settle_ms} ms for "
             + ("the reply to the 0x27 that replaces one packet (quiet on)" if quiet else "its echoes")
             + ". Packet lines go to the log file only"
             + (". SignalRGB mode is OFF: the colours are dropped, the LEDs keep the normal effect"
                if mode_off else ""))
    index = {}                      # payload -> (row, frame, position)
    tx_at = {}                      # (row, frame, position) -> TX log time (ms)
    seen, late = {}, {}             # (row, frame, position) -> arrival log time (ms)
    rtts = collections.defaultdict(list)    # row -> round trips of the last packet (ms)
    marker = {}                     # (row, frame) -> [slot, reply log time or None]
    late_markers = []               # quiet: marker replies after the last frame's wait
    unexpected = []
    current = [None]                # (row, frame) whose replies are being collected

    def on_echo(data, t):
        key = index.get(data)
        if key is None:
            return
        if key[:2] == current[0]:
            seen[key] = t
            # Echoes are read only after the whole burst is out, so only the last
            # packet's round trip is a fair one.
            if key[2] == PACE_PACKETS - 1:
                rtts[key[0]].append(t - tx_at[key])
        else:
            late.setdefault(key, t)

    def sort_out(got, frame_key):
        for p in got:
            key = index.get(p)
            if key is not None:     # out of order (the Link gave up on it) or late
                (seen if key[:2] == frame_key else late).setdefault(key, log.now_ms())
            elif quiet and p == PACE_MARKER_REPLY:
                # Replies are identical: one counts for the frame being collected. A reply
                # that comes later than a whole wait is counted for the next frame.
                if frame_key in marker and marker[frame_key][1] is None:
                    marker[frame_key][1] = log.now_ms()
                    if marker[frame_key][0] == PACE_PACKETS - 1:
                        rtts[frame_key[0]].append(marker[frame_key][1] - tx_at[(*frame_key, PACE_PACKETS - 1)])
                else:
                    late_markers.append(log.now_ms())
            else:
                unexpected.append(p)
                log.info(f"  unexpected packet: {p.hex(' ')}")

    stats = []
    link.on_echo = on_echo
    tag = 0
    try:
        for r, (gap, name, rgb, nulfree) in enumerate(rows):
            say(log, f"pace {r + 1}/{len(rows)}: {gap:g} ms between packets, whole board {name}"
                     + (" (diagnostic: no 00 bytes in the colour packets)" if nulfree else "")
                     + (" (mode off: no visible change expected)" if mode_off
                        else f". LOOK: how much of the board turns {name.lower()}"))
            firsts, bursts, spacings = [], [], []
            log.hold()
            try:
                for f in range(frames):
                    tag += 1
                    pk = pace_packets(rgb, tag, nulfree)
                    slot = f % PACE_PACKETS if quiet else None
                    if quiet:
                        pk[slot] = cmd_packet(PACE_MARKER_CMD)
                        marker[(r, f)] = [slot, None]
                    for i, p in enumerate(pk):
                        if i != slot:
                            index[p] = (r, f, i)
                    current[0] = (r, f)
                    ts = []
                    for i, p in enumerate(pk):
                        ts.append(link.send(p, gap_ms=gap))
                        tx_at[(r, f, i)] = ts[-1]
                    firsts.append(ts[0])
                    bursts.append(ts[-1] - ts[0])
                    spacings += [b - a for a, b in zip(ts, ts[1:])]
                    until = (lambda g: PACE_MARKER_REPLY in g) if quiet else None
                    sort_out(link.settle(settle_ms, until=until), (r, f))
                    current[0] = None
            finally:
                log.release(echo=False)
            st = dict(gap=gap, name=name, nulfree=nulfree, spacing=_median(spacings),
                      spacing_min=min(spacings, default=float("nan")), burst=_median(bursts),
                      period=_median([b - a for a, b in zip(firsts, firsts[1:])]))
            stats.append(st)
        current[0] = None
        sort_out(link.settle(settle_ms, until=lambda g: False), None)     # stragglers
    finally:
        link.on_echo = None

    # ---- per-row numbers
    for r, st in enumerate(stats):
        if quiet:
            probes = [sum(1 for f in range(frames) if marker[(r, f)][0] == k) for k in range(PACE_PACKETS)]
            arrived = [sum(1 for f in range(frames) if marker[(r, f)][0] == k and marker[(r, f)][1] is not None)
                       for k in range(PACE_PACKETS)]
            n_late = 0                  # identical replies: late ones are counted in total below
        else:
            probes = [frames] * PACE_PACKETS
            arrived = [sum(1 for f in range(frames) if (r, f, k) in seen or (r, f, k) in late)
                       for k in range(PACE_PACKETS)]
            n_late = sum(1 for key in late if key[0] == r)
        st.update(probes=probes, arrived=arrived, late=n_late, rtt=_median(rtts[r]),
                  loss_free=arrived == probes, per_frame=sum(arrived) / frames)

    what = ("quiet on: replies to the 0x27 in slot k, out of the frames that had it there" if quiet
            else f"quiet off: frames (of {frames}) whose packet k was echoed")
    log.info(f"\n== pace results ({what}) ==")
    log.info("   gap  colour   spacing med/min   burst   frame  "
             + ("arrived" if quiet else "echoes/frame") + "   k=1..10" + " " * 22 + "late  rtt(k=10)")
    for st in stats:
        cells = " ".join(f"{a:>2}" if p == frames or not quiet else f"{a}/{p}"
                         for a, p in zip(st["arrived"], st["probes"]))
        per = (f"{sum(st['arrived'])}/{sum(st['probes'])}" if quiet else f"{st['per_frame']:.2f}/10")
        log.info(f"  {st['gap']:>4g}{'*' if st['nulfree'] else ' '} {st['name']:<8} "
                 f"{st['spacing']:6.1f}/{st['spacing_min']:4.1f} ms {st['burst']:6.1f} {st['period']:7.1f}  "
                 f"{per:>9}   {cells:<30} {st['late']:>4}  {st['rtt']:5.1f} ms"
                 + ("   loss-free" if st["loss_free"] else ""))
    log.info("  spacing = start to start of consecutive packets; burst = first to last packet "
             "of a frame; frame = start to start of frames (includes the wait); rtt(k=10) = median "
             "round trip of packet 10's " + ("0x27 reply" if quiet else "echo")
             + " (replies are read only after the burst). ms.")
    if diag_gap:
        log.info("  * diagnostic row: no 00 byte in the packets except packet 1's start byte")
    if quiet:
        log.info(f"  0x27 replies that came after their frame's wait or twice: {len(late_markers)}")

    main_rows = [st for st in stats if not st["nulfree"]]
    best = None
    for st in sorted(main_rows, key=lambda s: s["gap"], reverse=True):
        if not st["loss_free"]:
            break
        best = st
    result = dict(rows=stats, best_gap=None, fps=None, diag=None, unexpected=len(unexpected))
    if best is not None:
        frame_ms = PACE_PACKETS * max(best["gap"], best["spacing"])
        result.update(best_gap=best["gap"], fps=1000.0 / frame_ms)
        log.info(f"\n  smallest loss-free gap: {best['gap']:g} ms (it and every larger gap tested lost "
                 f"nothing). A full frame ({PACE_PACKETS} packets) then takes {frame_ms:.0f} ms: at most "
                 f"{1000.0 / frame_ms:.1f} frames/s without waiting for echoes.")
        lower = [st["gap"] for st in main_rows if st["loss_free"] and st["gap"] < best["gap"]]
        if lower:
            log.info(f"  note: smaller gap(s) {', '.join(f'{g:g}' for g in lower)} ms were loss-free too, "
                     "but a gap between them and this one was not")
    else:
        log.info("\n  no loss-free gap: the largest gap tested still lost packets" if main_rows
                 else "\n  no main rows")

    diag = next((st for st in stats if st["nulfree"]), None)
    if diag is not None and main_rows:
        ref = min(main_rows, key=lambda s: (abs(s["gap"] - diag["gap"]), s["gap"]))
        d_pos = {k for k, a in enumerate(diag["arrived"]) if a}
        r_pos = {k for k, a in enumerate(ref["arrived"]) if a}
        if ref["loss_free"] and diag["loss_free"]:
            kind, text = "none", "no loss at this gap, so the row cannot tell the causes apart"
        elif ref["loss_free"]:
            kind, text = "odd", "loss only without 00 bytes; not what either cause predicts"
        elif diag["loss_free"]:
            kind, text = "parser", ("no loss without 00 bytes, loss with them: points at the stock "
                                    "parser (strstr stops at 00)")
        elif d_pos - r_pos:
            kind, text = "parser", (
                f"packets {sorted(k + 1 for k in d_pos)} arrived without 00 bytes vs "
                f"{sorted(k + 1 for k in r_pos)} with them. Points at merged lines in one UART chunk: "
                "the stock +DATA: parser handles only the first line it finds, and strstr stops at 00")
        else:
            kind, text = "upstream", (
                f"the same packets arrived with and without 00 bytes ({sorted(k + 1 for k in d_pos)}). "
                "No sign of the keyboard's line parser: the loss looks upstream (receiver, radio link "
                "or the keyboard's radio module)")
        result["diag"] = kind
        log.info(f"  diagnostic (inferred, {diag['gap']:g} ms vs the {ref['gap']:g} ms row): {text}")
    if unexpected:
        log.info(f"  FAIL: {len(unexpected)} unexpected packet(s) (not an echo or reply of a pace packet)")
    link.pace_result = result
    return not unexpected


# ---- Soak and partial steps (receiver) --------------------------------------------

SOAK_PACKETS = 3000
RADIO_STEP_MIN_GAP_MS = 75          # soak / partial refuse faster pacing: every damaged echo
                                    # seen so far (2026-10-21) came with overload
# 0x22 is the probe: its one-bit neighbours (02 20 23 26 2A 32 62 A2) are read-only VIA,
# SignalRGB or unknown IDs, as are those of 0x21, 0x24 and 0x28. 0x23 0x25 0x26 0x27 are
# each one flipped bit 5 away from a VIA write (03 05 06 07).
SOAK_PROBE_CMD = CMD_PROTOCOL_VERSION
SOAK_PROBE_REPLY = EXPECTED[SOAK_PROBE_CMD] + bytes(REPORT_LEN - 4)
SOAK_WINDOW = 64                    # recent packets a damaged reply is compared with
SOAK_LOG_EVENTS = 25                # unexpected replies printed in full
SOAK_PROBE_LOST_MS = 1000           # a probe unanswered this long counts as lost
REPLY_HORIZON_MS = 600              # a damaged reply is matched to packets sent this recently
# VIA commands the dispatcher 0x0800F620 implements (V dec): 0x01-0x15 except 0x0A / 0x0B,
# which fall to 'unhandled'. The write commands change keymap, macros, encoders or settings.
VIA_CMDS = frozenset(range(0x01, 0x16)) - {0x0A, 0x0B}
VIA_WRITE_CMDS = frozenset({0x03, 0x05, 0x06, 0x07, 0x09, 0x0F, 0x10, 0x13, 0x15})
FIXED_REPLIES = {c: EXPECTED[c] + bytes(REPORT_LEN - 4) for c in sorted(READ_ONLY_CMDS)}
ERROR_REPLIES = {e: bytes([CMD_STREAM, e]) + bytes(REPORT_LEN - 2)
                 for e in (ERROR_LED_BOUNDS, ERROR_LED_COUNT)}


def _hamming(a, b, start=0):
    return sum(x != y for x, y in zip(a[start:], b[start:]))


def classify_reply(p, recent, quiet):
    """What an unexpected input report proves, by patch v2's reply rules (srgb_radio.c)
    and VIA's (0x0800F620). The keyboard answers a packet it received as r with:
      valid 0x24        r itself (a copy), or nothing when quiet is on
      0x24, bad start/count   24 FD / 24 FE + zeros, always
      query 0x21-0x23 0x27 0x28   a fixed, zero-filled reply, always
      0x25 / 0x26       r (a copy) unless quiet is on after it
      anything else     VIA, in place: FF + r[1:] for IDs it does not know, else r
                        with some bytes rewritten
    kind: down = only a packet that reached the keyboard damaged explains it; up = only a
    correct reply damaged on the way back does; either = both do; quiet-off = a copy of a
    0x24 while quiet was on (the keyboard's quiet switch went off); late = an exact echo
    the script had already counted missing; unknown.
    recent: [(key, payload)] of the packets just sent, oldest first. Of equally close
    packets the oldest is taken (replies come back in order; the caller leaves out
    packets whose exact echo already arrived)."""
    ev = dict(kind="unknown", ref=None, diff=[], byte0=False, via=None, text="")
    fixed = list(FIXED_REPLIES.values()) + list(ERROR_REPLIES.values())
    near = min(fixed, key=lambda r: _hamming(p, r))
    d_fixed = _hamming(p, near)
    best = None
    for key, s in recent:
        d = _hamming(p, s, 1)
        if best is None or d < best[0]:
            best = (d, key, s)
    if d_fixed == 0:
        if p in ERROR_REPLIES.values():
            ev.update(kind="down", text=f"error reply 24 {p[1]:02x}: the keyboard received a 0x24 "
                                        "with a damaged start/count (every 0x24 sent was valid)")
        else:
            ev.update(kind="down", text=f"reply to query 0x{p[0]:02x}, which was not sent then: a packet "
                                        f"arrived with byte 0 damaged into 0x{p[0]:02x}")
        return ev
    if best is None or d_fixed < best[0]:
        ev.update(ref=near[0], diff=[(i, a, b) for i, (a, b) in enumerate(zip(near, p)) if a != b])
        if d_fixed <= 4 and near in ERROR_REPLIES.values():
            ev.update(kind="down", text="an error reply (so a damaged 0x24 reached the keyboard), "
                                        "itself damaged on the way back")
        elif d_fixed <= 4:
            ev.update(kind="up", text=f"the 0x{near[0]:02x} reply damaged on the way back (the keyboard "
                                      "generates it, it does not copy the request)")
        else:
            ev.update(text="matches no packet sent and no reply the keyboard makes")
        return ev
    d, key, s = best
    ev.update(ref=key, diff=[(i, a, b) for i, (a, b) in enumerate(zip(s, p)) if a != b],
              byte0=p[0] != s[0])
    b0 = p[0]
    if d > 8:
        ev.update(text="matches no packet sent and no reply the keyboard makes")
    elif b0 == CMD_STREAM and not ev["diff"]:
        ev.update(kind="quiet-off" if quiet else "late",
                  text="the keyboard echoed a 0x24 although quiet was on: its quiet switch was off "
                       "(a packet that arrived as 25 with byte 1 bit 0 clear, or its radio state was reset)"
                  if quiet else "an exact echo that came after the script had counted it missing")
    elif b0 == CMD_STREAM and not stream_valid(p):
        ev.update(kind="up", text="a copy of a 0x24 whose start/count the keyboard rejects (it would have "
                                  "answered 24 fd/fe, not a copy): damaged on the way back")
    elif b0 == CMD_STREAM:
        ev.update(kind="quiet-off" if quiet else "either",
                  text="a damaged copy of a 0x24 while quiet was on" if quiet else
                  "echo differs from the packet sent: damaged on the way to the keyboard or back "
                  "(an echo is a copy, so it cannot tell which)")
    elif b0 in (CMD_MODE_ON, CMD_MODE_OFF):
        if quiet and b0 == CMD_MODE_ON and not p[1] & QUIET_BIT:
            ev.update(kind="down", text="a packet arrived as 0x25 with byte 1 bit 0 clear (byte 0 damaged): "
                                        "the keyboard turned quiet off and echoed it")
        elif quiet:
            ev.update(text="a copy starting 0x25/0x26 that the keyboard does not send while quiet is on")
        else:
            ev.update(kind="either", text=f"byte 0 became 0x{b0:02x}: either the keyboard received it so "
                                          "(mode on/off) and echoed it, or the echo was damaged on the way back")
    elif b0 in READ_ONLY_CMDS:
        ev.update(kind="unknown" if quiet else "up",
                  text=f"0x{b0:02x} with a copied body: the keyboard answers a query with a fixed zero-filled "
                       "reply, so this is an echo whose byte 0 was damaged on the way back")
    elif b0 == 0xFF or b0 in VIA_CMDS:
        ev["via"] = b0
        what = ("VIA 'unhandled' (FF + the packet's bytes 1-31)" if b0 == 0xFF else
                f"VIA command 0x{b0:02x}" + (" (a WRITE: keymap, macro, encoder or setting)"
                                             if b0 in VIA_WRITE_CMDS else " (read-only)"))
        ev.update(kind="down" if quiet else "either",
                  text=f"byte 0 arrived damaged and the keyboard ran it as {what}" if quiet else
                  f"either byte 0 arrived damaged and the keyboard ran it as {what}, or an echo's byte 0 "
                  "was damaged on the way back")
    else:
        ev.update(kind="unknown" if quiet else "up",
                  text=f"no reply starts with 0x{b0:02x} (VIA answers unknown IDs with FF): an echo whose "
                       "byte 0 was damaged on the way back")
    return ev


def _event_line(ev):
    ref = (f" vs the probe after frame {ev['ref'][1] + 1}" if ev["ref"][0] == "probe" else
           f" vs frame {ev['ref'][0] + 1} packet {ev['ref'][1] + 1}") if isinstance(ev["ref"], tuple) else \
        (f" vs the 0x{ev['ref']:02x} reply" if ev["ref"] is not None else "")
    diff = ", ".join(f"[{i}] {a:02x}>{b:02x} (xor {a ^ b:02x})" for i, a, b in ev["diff"][:8])
    more = f" +{len(ev['diff']) - 8} more" if len(ev["diff"]) > 8 else ""
    return (f"  {ev['t']:10.1f} ms  {ev['kind'].upper():<9}{ref}: {ev['text']}"
            + (f"\n               bytes {diff}{more}" if diff else "")
            + (f"\n               got {ev['data'].hex(' ')}" if ev.get("data") else ""))


def _pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))] if s else float("nan")


class ReplyWatch:
    """Reads replies while the soak / partial steps wait between packets, and sorts
    every one that is not an expected echo (the Link takes those) or a probe reply."""

    def __init__(self, link, log, probing=False):
        self.link, self.log = link, log
        self.quiet = bool(link.quiet)
        self.probing = probing
        self.recent = collections.deque(maxlen=SOAK_WINDOW)
        self.index = {}                 # payload -> key, for late echoes
        self.echo_at = {}               # key -> arrival (log ms) of its exact echo
        self.claimed = set()            # keys a damaged echo was matched to
        self.events = []
        self.probes = dict(sent=0, answered=0, damaged=0, lost=0, rtt=[])
        self.pending_probes = collections.deque()
        link.on_echo = self._on_echo

    def _on_echo(self, data, t):
        key = self.index.get(data)
        if key is not None:
            self.echo_at.setdefault(key, t)

    def sent(self, key, payload, t):
        self.recent.append((key, payload, t))
        if payload[0] == SOAK_PROBE_CMD and self.probing:
            self.pending_probes.append(t)
            self.probes["sent"] += 1
        else:
            self.index[payload] = key

    def handle(self, p, t):
        if self.probing and p == SOAK_PROBE_REPLY and self.pending_probes:
            while len(self.pending_probes) > 1 and t - self.pending_probes[0] > SOAK_PROBE_LOST_MS:
                self.pending_probes.popleft()
                self.probes["lost"] += 1
            self.probes["rtt"].append(t - self.pending_probes.popleft())
            self.probes["answered"] += 1
            return
        if p in self.index:             # an exact copy of a colour packet sent earlier
            key = self.index[p]
            if self.quiet:
                ev = dict(kind="quiet-off", ref=key, diff=[], byte0=False, via=None,
                          text="the keyboard echoed a 0x24 although quiet was on: its quiet switch was "
                               "off (a packet that arrived as 25 with byte 1 bit 0 clear, or its radio "
                               "state was reset)")
            else:                       # an echo the Link had already counted missing
                self.echo_at.setdefault(key, t)
                ev = dict(kind="late", ref=key, diff=[], byte0=False, via=None,
                          text="an exact echo that came after a later packet's echo")
        else:
            # Candidates: packets with no reply yet, sent within the last REPLY_HORIZON_MS
            # (echoes came back within ~200 ms on hardware), all recent ones if none is.
            open_ = [(k, s, ts) for k, s, ts in self.recent if k not in self.echo_at and k not in self.claimed]
            cand = [(k, s) for k, s, ts in open_ if t - ts <= REPLY_HORIZON_MS] or \
                   [(k, s) for k, s, _ in open_]
            ev = classify_reply(p, cand, self.quiet)
            if ev["kind"] == "down" and ev["ref"] is None and not self.quiet and cand:
                ev["ref"] = cand[0][0]          # an error reply answers the oldest open packet
            if isinstance(ev["ref"], tuple) and ev["kind"] in ("either", "up", "down"):
                self.claimed.add(ev["ref"])
            elif (self.probing and ev["kind"] == "up" and ev["ref"] == SOAK_PROBE_CMD
                  and self.pending_probes):     # a probe answered, the reply damaged
                self.pending_probes.popleft()
                self.probes["damaged"] += 1
        ev.update(t=t, data=p)
        self.events.append(ev)
        if len(self.events) <= SOAK_LOG_EVENTS:
            self.log.info(_event_line(ev))

    def poll_until(self, t_next):
        """Read replies until perf_counter() is within 15 ms of t_next; Link.send's
        wait_until does the rest precisely. (Polling to 3 ms before t_next overshot by
        5-8 ms per packet: 105-108 ms spacing for --packet-gap 100, hardware 2026-10-21.)"""
        while (left := t_next - time.perf_counter()) > 0.015:
            p = self.link.recv(max(1.0, min(left * 1000.0 - 13.0, 100.0)))
            if p is not None:
                self.handle(p, self.log.now_ms())

    def send(self, key, payload):
        """Send at the Link's pacing, reading replies while waiting. Returns the log time."""
        link = self.link
        if link.t_last is not None:
            gap = link.gap_ms
            if link.last_cmd in (CMD_MODE_ON, CMD_MODE_OFF):
                gap = max(gap, RADIO_CMD_GAP_MS)
            self.poll_until(link.t_last + gap / 1000.0)
        t = link.send(payload)
        self.sent(key, payload, t)
        return t

    def finish(self, wait_s=0.6):
        self.poll_until(time.perf_counter() + wait_s)
        for p in self.link.settle(100):
            self.handle(p, self.log.now_ms())
        while self.pending_probes:
            self.pending_probes.popleft()
            self.probes["lost"] += 1
        self.link.on_echo = None

    def summary(self):
        kinds = collections.Counter(ev["kind"] for ev in self.events)
        via = [ev["via"] for ev in self.events if ev["via"] is not None]
        return dict(events=len(self.events), kinds=dict(kinds),
                    byte0=sum(1 for ev in self.events if ev["byte0"]),
                    via=len(via), via_writes=sum(1 for v in via if v in VIA_WRITE_CMDS))

    def report(self, log):
        s = self.summary()
        k = s["kinds"]
        log.info(f"  unexpected replies: {s['events']}"
                 + (" (" + ", ".join(f"{n} {kind}" for kind, n in sorted(k.items())) + ")" if k else "")
                 + f"; byte 0 changed: {s['byte0']}; VIA would have run: {s['via']}"
                 + (f" (WRITES: {s['via_writes']})" if s["via_writes"] else ""))
        log.info("    down = proves a packet reached the keyboard damaged; up = proves a reply was "
                 "damaged on its way back; either = an echo copy, cannot tell")
        if len(self.events) > SOAK_LOG_EVENTS:
            log.info(f"  (the first {SOAK_LOG_EVENTS} are printed above in full; all are in the log)")
        for ev in self.events[:SOAK_LOG_EVENTS]:
            log.info(_event_line(ev))
        return s


def step_soak(link, log, n_packets, probe_every):
    quiet = bool(link.quiet)
    frames = -(-n_packets // PACE_PACKETS)
    if frames > MAX_PACE_TAGS:
        raise SafetyError("too many soak frames for the 12-bit tag")
    probing = quiet and probe_every > 0
    n_probes = frames // probe_every if probing else 0
    est = (n_packets + n_probes) * link.gap_ms / 1000.0
    say(log, f"soak: {n_packets} colour packets ({frames} frames, each a solid colour, cycling "
             f"{', '.join(n.lower() for n, _ in PACE_COLORS)}), at least {link.gap_ms:g} ms apart"
             + (f", plus the query 0x22 after every {probe_every} frame(s) ({n_probes}; its reply is "
                "the only one quiet allows)" if probing else "")
             + f". About {est:.0f} s. Every reply is checked against what was sent. Packet lines go "
             "to the log file only. Nothing to watch (the colour changes about once a second)")
    w = ReplyWatch(link, log, probing)
    tx = []                             # (key, log ms)
    t_start = time.perf_counter()
    log.hold()
    try:
        n = 0
        for f in range(frames):
            _, rgb = PACE_COLORS[f % len(PACE_COLORS)]
            for i, p in enumerate(pace_packets(rgb, f + 1, False)):
                if n == n_packets:
                    break
                tx.append(((f, i), w.send((f, i), p)))
                n += 1
            if probing and (f + 1) % probe_every == 0:
                w.send(("probe", f), cmd_packet(SOAK_PROBE_CMD))
        w.finish()
    finally:
        log.release(echo=False)
    elapsed = time.perf_counter() - t_start
    spacing = [b - a for (_, a), (_, b) in zip(tx, tx[1:])]

    log.info("\n== soak results ==")
    log.info(f"  sent: {n_packets} colour packets" + (f" + {w.probes['sent']} 0x22 probes" if probing else "")
             + f" in {elapsed:.0f} s; spacing between colour packets min {min(spacing, default=0):.1f} / "
             f"median {_median(spacing):.1f} ms")
    res = dict(sent=n_packets, quiet=quiet, elapsed=elapsed, spacing_min=min(spacing, default=0.0))
    if not quiet:
        keys = dict(tx)
        damaged = {ev["ref"] for ev in w.events if ev["kind"] in ("either", "up", "down") and ev["ref"] in keys}
        exact = len(w.echo_at)
        none = sum(1 for key, _ in tx if key not in w.echo_at and key not in damaged)
        res["damaged_refs"] = sorted(damaged)
        lat = [w.echo_at[key] - t for key, t in tx if key in w.echo_at]
        t_next = {key: tx[j + 1][1] for j, (key, _) in enumerate(tx[:-1])}
        held = sum(1 for key, _ in tx if key in w.echo_at and key in t_next and w.echo_at[key] > t_next[key])
        log.info(f"  quiet off: exact echoes {exact}/{n_packets}, damaged replies {len(damaged)} "
                 f"(echoes that differ, or error replies), no reply at all {none} (lost on the way or back)")
        log.info(f"  echo round trip median {_median(lat):.1f} / 90 % {_pct(lat, 0.9):.1f} / max "
                 f"{max(lat, default=float('nan')):.1f} ms; {held} echo(es) arrived after the next packet "
                 "had been sent")
        res.update(exact=exact, damaged=len(damaged), lost=none, rtt_median=_median(lat), held=held)
    else:
        pr = w.probes
        log.info("  quiet on: colour packets get no reply by design"
                 + (f"; 0x22 probes answered {pr['answered']}/{pr['sent']} (reply damaged "
                    f"{pr['damaged']}, lost {pr['lost']}), round "
                    f"trip median {_median(pr['rtt']):.1f} / max {max(pr['rtt'], default=float('nan')):.1f} ms"
                    if probing else ""))
        res.update(probes=dict(pr, rtt_median=_median(pr["rtt"])))
    s = w.report(log)
    res.update(s)
    link.soak_result = res
    ok = not w.events and (quiet or res["lost"] == 0) and (not probing or w.probes["lost"] == 0)
    log.info(f"  soak: {'no damage and no loss seen' if ok else 'damage or loss seen (details above)'}")
    return ok


PARTIAL_GAP_MS = 80                 # 2.4G default with quiet on (75 ms was the measured edge)
PARTIAL_REFRESH_MS = 1500           # a group not sent for this long is sent again
PARTIAL_TICK_S = 0.016              # SignalRGB calls Render() about 60 times a second
PARTIAL_DOT_RATE = 4.0              # LEDs per second in the dot phase
PARTIAL_LEAD_S = 1.5                # dot phase: all off this long first (the whole board changes)
GREEN, WHITE, BLUE = (0, 255, 0), (255, 255, 255), (0, 0, 255)
PARTIAL_WIPE = (("RED", (255, 0, 0)), ("YELLOW", (255, 255, 0)), ("CYAN", (0, 255, 255)),
                ("MAGENTA", (255, 0, 255)))
PARTIAL_WIPE_S = 2.5
PARTIAL_PHASES = (
    ("static", 10.0, "whole board GREEN, not changing, so only refresh packets go out. LOOK: it stays "
                     "green all 10 s (no fall-back to the normal effect)"),
    ("dot", PARTIAL_LEAD_S + N_LEDS / PARTIAL_DOT_RATE + 1.5,
     f"all LEDs OFF, then after {PARTIAL_LEAD_S:g} s one WHITE key moving through all {N_LEDS} LEDs in "
     f"order (Esc, F1, ... row by row), {PARTIAL_DOT_RATE:g} per second. LOOK: one white key at a time, no white keys left behind "
     "(white on green was too faint to see on battery, hardware 2026-10-21)"),
    ("fn", 12.0, "whole board BLUE. DO: hold Fn for about 2 s and let go, then press Caps Lock twice. "
                 "LOOK: keys that light up differently (mode/lock indicators) turn blue again within about 2 s"),
    ("wipe", 4 * PARTIAL_WIPE_S, f"the colour changes every {PARTIAL_WIPE_S:g} s: red, yellow, cyan, magenta. "
                                 "LOOK: each colour fills the whole board within about a second"),
)


class GroupScheduler:
    """The 2.4G packet pacing planned for the plugin (step 8). The receiver carries about
    one packet per 75-100 ms, so a frame cannot go out whole: at most one packet per
    Render(), never closer than gap_ms to the previous packet of any kind. Which one:
      1. a group (9 LEDs, one 0x24) not sent for 2 x refresh_ms ("overdue"), so a busy
         scene cannot starve a group that never changes;
      2. else the next group whose colours differ from what was last sent for it, round
         robin from the last one sent;
      3. else (nothing changed) the group sent longest ago, once refresh_ms old: covers
         lost packets and the vendor indicators the firmware draws into the frame
         buffer, and keeps the firmware's 2 s timeout fed in a still scene;
      4. else nothing.
    (A first version put the refresh before changes: in a moving scene the refreshes then
    took most slots and changes waited up to 0.76 s, hostsim partial 2026-10-21.)"""

    def __init__(self, gap_ms=PARTIAL_GAP_MS, refresh_ms=PARTIAL_REFRESH_MS, t_last=None):
        self.gap = gap_ms / 1000.0
        self.refresh = refresh_ms / 1000.0
        self.overdue = 2.0 * self.refresh
        self.sent = [None] * PACE_PACKETS
        self.t_sent = [None] * PACE_PACKETS
        self.cursor = 0
        self.t_last = t_last

    def pick(self, now, frame):
        """Returns (group, packet, reason) to send now, or None."""
        if self.t_last is not None and now - self.t_last < self.gap:
            return None
        pk = list(stream_packets(frame))
        old = [g for g in range(PACE_PACKETS) if self.t_sent[g] is not None]
        stalest = min(old, key=lambda g: self.t_sent[g]) if old else None
        if stalest is not None and now - self.t_sent[stalest] >= self.overdue:
            return stalest, pk[stalest], "overdue"
        for k in range(PACE_PACKETS):
            g = (self.cursor + k) % PACE_PACKETS
            if pk[g] != self.sent[g]:
                return g, pk[g], "changed"
        if stalest is not None and now - self.t_sent[stalest] >= self.refresh:
            return stalest, pk[stalest], "refresh"
        return None

    def mark(self, g, packet, now):
        self.sent[g] = packet
        self.t_sent[g] = now
        self.cursor = (g + 1) % PACE_PACKETS
        self.t_last = now


PHASE_HOOK = None                   # hostsim: called with (phase name, frame) at each phase end


def partial_frame(phase, t):
    if phase == "static":
        return solid(GREEN)
    if phase == "dot":
        frame = solid((0, 0, 0))
        if t >= PARTIAL_LEAD_S:
            frame[min(N_LEDS - 1, int((t - PARTIAL_LEAD_S) * PARTIAL_DOT_RATE))] = WHITE
        return frame
    if phase == "fn":
        return solid(BLUE)
    return solid(PARTIAL_WIPE[min(len(PARTIAL_WIPE) - 1, int(t / PARTIAL_WIPE_S))][1])


def step_partial(link, log, refresh_ms, phases=None):
    gap = max(link.gap_ms, PARTIAL_GAP_MS)
    chosen = [ph for ph in PARTIAL_PHASES if phases is None or ph[0] in phases]
    say(log, f"partial: the planned 2.4G pacing. At most one packet per {PARTIAL_TICK_S * 1000:.0f} ms "
             f"tick, at least {gap:g} ms apart; changed 9-LED groups first, every group re-sent after "
             f"{refresh_ms:g} ms. {len(chosen)} phase(s), about "
             f"{sum(d for _, d, _ in chosen):.0f} s")
    w = ReplyWatch(link, log)
    sched = GroupScheduler(gap, refresh_ms, t_last=link.t_last)
    if link.last_cmd in (CMD_MODE_ON, CMD_MODE_OFF) and link.t_last is not None:
        sched.t_last = link.t_last + (RADIO_CMD_GAP_MS - gap) / 1000.0
    stats = []
    tx = []
    for n_phase, (phase, dur, text) in enumerate(chosen, 1):
        say(log, f"partial {n_phase}/{len(chosen)}: {text}")
        st = dict(phase=phase, changed=0, refresh=0, overdue=0, lag=[], lag_steady=[])
        changed_at = [None] * PACE_PACKETS
        t0 = time.perf_counter()
        log.hold()
        try:
            while (now := time.perf_counter()) - t0 < dur:
                frame = partial_frame(phase, now - t0)
                pk = list(stream_packets(frame))
                for g in range(PACE_PACKETS):
                    if pk[g] != sched.sent[g] and changed_at[g] is None:
                        changed_at[g] = now
                pick = sched.pick(now, frame)
                if pick is not None:
                    g, p, why = pick
                    t = w.send((n_phase - 1, g), p)
                    sched.mark(g, p, link.t_last)
                    tx.append(t)
                    st[why] += 1
                    if changed_at[g] is not None:
                        st["lag"].append((link.t_last - changed_at[g]) * 1000.0)
                        if changed_at[g] - t0 >= PARTIAL_LEAD_S:
                            st["lag_steady"].append(st["lag"][-1])
                        changed_at[g] = None
                w.poll_until(now + PARTIAL_TICK_S)
                wait_until(now + PARTIAL_TICK_S)
        finally:
            log.release(echo=False)
        if PHASE_HOOK is not None:
            PHASE_HOOK(phase, partial_frame(phase, dur))
        n = st["changed"] + st["refresh"] + st["overdue"]
        log.info(f"  {n} packets ({st['changed']} changed, {st['refresh']} refresh, {st['overdue']} overdue), "
                 f"{n / dur:.1f}/s; "
                 f"change to send: median {_median(st['lag']):.0f} / max "
                 f"{max(st['lag'], default=float('nan')):.0f} ms; changes after the first "
                 f"{PARTIAL_LEAD_S:g} s: max {max(st['lag_steady'], default=float('nan')):.0f} ms")
        stats.append(st)
    w.finish(0.3)
    spacing = [b - a for a, b in zip(tx, tx[1:])]
    log.info(f"\n== partial results ==\n  {len(tx)} packets, spacing min {min(spacing, default=0):.1f} / "
             f"median {_median(spacing):.1f} ms")
    s = w.report(log)
    link.partial_result = dict(stats=stats, spacing_min=min(spacing, default=0.0), **s)
    return not w.events


IDLE_LEAD_S = 10.0                  # idle step: seconds of stream before the silence
IDLE_SILENT_S = 840.0               # then nothing at all is sent for this long
IDLE_PROBE_EVERY_S = 5.0            # then one 0x22 this often
IDLE_PROBE_FOR_S = 300.0            # for this long
IDLE_SCENE = (("MAGENTA", (255, 0, 255)), ("CYAN", (0, 255, 255)))
IDLE_SCENE_S = 3.0                  # the lead-in scene alternates this often


def _clock(t_perf):
    """Wall-clock time (HH:MM:SS) of a perf_counter() value."""
    return (dt.datetime.now() + dt.timedelta(seconds=t_perf - time.perf_counter())).strftime("%H:%M:%S")


def step_idle(link, log, lead_s, silent_s, probe_every_s, probe_for_s):
    """Step 7 (power): does the keyboard sleep on 2.4G, and what keeps it awake or wakes it?
      1. lead-in (lead_s > 0): the GroupScheduler stream of step partial, the whole board
         alternating IDLE_SCENE every IDLE_SCENE_S, and NO 0x26 afterwards: the firmware's
         2 s timeout must end SignalRGB mode by itself;
      2. silence: nothing is sent for silent_s (replies are still read and logged);
      3. probes: one 0x22 every probe_every_s for probe_for_s, each answered (round trip)
         or lost (no reply before the next probe). A sleeping keyboard does not answer.
    Lost probes are the measurement, not a failure; the step fails only on unexpected
    replies (damage, or an echo while quiet is on)."""
    gap = max(link.gap_ms, PARTIAL_GAP_MS)
    t0 = time.perf_counter()
    t_quiet = t0 + lead_s
    t_probe = t_quiet + silent_s
    t_end = t_probe + probe_for_s
    names = " / ".join(n for n, _ in IDLE_SCENE)
    say(log, "idle: " + (f"{lead_s:g} s of stream until {_clock(t_quiet)} (whole board {names}, alternating "
                         f"every {IDLE_SCENE_S:g} s; no 0x26 after it, so about 2 s later the normal effect "
                         "must come back by itself), then " if lead_s else "")
        + f"NOTHING sent for {silent_s:g} s until {_clock(t_probe)}, then one 0x22 query every "
          f"{probe_every_s:g} s until {_clock(t_end)} (a sleeping keyboard does not answer)")
    w = ReplyWatch(link, log)
    res = dict(lead=0, lead_spacing_min=None, silent_rx=0, probes=[])
    if lead_s > 0:
        sched = GroupScheduler(gap, PARTIAL_REFRESH_MS, t_last=link.t_last)
        if link.last_cmd in (CMD_MODE_ON, CMD_MODE_OFF) and link.t_last is not None:
            sched.t_last = link.t_last + (RADIO_CMD_GAP_MS - gap) / 1000.0
        tx = []
        log.hold()
        try:
            while (now := time.perf_counter()) < t_quiet:
                rgb = IDLE_SCENE[int((now - t0) / IDLE_SCENE_S) % len(IDLE_SCENE)][1]
                pick = sched.pick(now, solid(rgb))
                if pick is not None:
                    g, p, _ = pick
                    tx.append(w.send(("lead", len(tx)), p))
                    sched.mark(g, p, link.t_last)
                w.poll_until(min(now + PARTIAL_TICK_S, t_quiet))
                wait_until(min(now + PARTIAL_TICK_S, t_quiet))
        finally:
            log.release(echo=False)
        spacing = [b - a for a, b in zip(tx, tx[1:])]
        res.update(lead=len(tx), lead_spacing_min=min(spacing, default=0.0), t_lead_end=link.log.now_ms())
        log.info(f"  lead-in: {len(tx)} colour packets, spacing min {min(spacing, default=0):.1f} / median "
                 f"{_median(spacing):.1f} ms; last one at {_clock(link.t_last)}")
    if silent_s > 0:
        say(log, f"silence: nothing is sent until {_clock(t_probe)}")
        n0 = len(w.events)
        while (left := t_probe - time.perf_counter()) > 0:
            p = link.recv(max(1.0, min(left * 1000.0, 250.0)))
            if p is not None:
                res["silent_rx"] += 1
                w.handle(p, log.now_ms())
        log.info(f"  silence over at {_clock(time.perf_counter())}; "
                 f"{res['silent_rx']} report(s) arrived meanwhile" + (" (see above)" if len(w.events) > n0 else ""))
    say(log, f"probes: one 0x22 every {probe_every_s:g} s until {_clock(t_end)}")
    probe = cmd_packet(SOAK_PROBE_CMD)
    k = 0
    while (t_next := t_probe + k * probe_every_s) < t_end:
        wait_until(t_next)
        t = link.send(probe)
        pr = dict(n=k + 1, clock=_clock(link.t_last), t=t, rtt=None, extra=0)
        while (left := t_next + probe_every_s - time.perf_counter()) > 0.005 and \
                time.perf_counter() < t_end + 1.0:
            p = link.recv(max(1.0, min(left * 1000.0 - 3.0, 500.0)))
            if p is None:
                continue
            if p == SOAK_PROBE_REPLY:
                if pr["rtt"] is None:
                    pr["rtt"] = log.now_ms() - t
                else:
                    pr["extra"] += 1
            else:
                w.handle(p, log.now_ms())
        log.info(f"  probe {pr['n']:3d} at {pr['clock']}: "
                 + (f"answered, {pr['rtt']:.1f} ms" if pr["rtt"] is not None else "LOST (no reply)")
                 + (f", plus {pr['extra']} more 0x22 repl{'y' if pr['extra'] == 1 else 'ies'}" if pr["extra"] else ""))
        res["probes"].append(pr)
        k += 1
    w.finish(0.3)
    probes = res["probes"]
    answered = [p for p in probes if p["rtt"] is not None]
    lost = [p for p in probes if p["rtt"] is None]
    runs, cur = [], None                # runs of lost probes: [first, last]
    for p in probes:
        if p["rtt"] is None:
            cur = [p, p] if cur is None else [cur[0], p]
        elif cur is not None:
            runs.append(cur)
            cur = None
    if cur is not None:
        runs.append(cur)
    log.info(f"\n== idle results ==\n  probes: {len(answered)}/{len(probes)} answered"
             + (f", round trip median {_median([p['rtt'] for p in answered]):.1f} / max "
                f"{max(p['rtt'] for p in answered):.1f} ms" if answered else "")
             + f"; {len(lost)} lost (lost probes are the measurement: the keyboard may be asleep)")
    for a, b in runs:
        log.info(f"  lost: probe {a['n']} ({a['clock']}) to probe {b['n']} ({b['clock']})")
    s = w.report(log)
    res.update(s, answered=len(answered), lost=len(lost),
               lost_runs=[(a["n"], b["n"]) for a, b in runs])
    link.idle_result = res
    return not w.events


def parse_leds(text):
    out = []
    for part in text.split(","):
        if "-" in part:
            a, b = (int(v) for v in part.split("-"))
            out += range(a, b + 1)
        elif part.strip():
            out.append(int(part))
    if not all(0 <= i < N_LEDS for i in out):
        raise SystemExit(f"LED indices must be 0..{N_LEDS - 1}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true", help="list HID interfaces, send nothing")
    ap.add_argument("--led", action="store_true", help="also run the LED tests (0x24-0x26)")
    ap.add_argument("--steps", default=",".join(DEFAULT_STEPS), help="LED steps to run")
    ap.add_argument("--walk-leds", default=f"0-{N_LEDS - 1}")
    ap.add_argument("--walk-delay", type=float, default=1.0)
    ap.add_argument("--hold-seconds", type=float, default=60.0)
    num = lambda s: int(s, 0)       # noqa: E731  (accepts 0x6D66 or 28006)
    ap.add_argument("--vid", type=num, default=VID, help="0x1EA7 (default) or 0x6D66")
    ap.add_argument("--pid", type=num, default=PID, help="0xCEDD (default) or 0x8888")
    ap.add_argument("--usage-page", type=num, default=USAGE_PAGE,
                    help="raw HID usage page, vendor-defined 0xFF00-0xFFFF (default 0xFF60)")
    ap.add_argument("--usage", type=num, default=USAGE, help="raw HID usage (default 0x61)")
    ap.add_argument("--via-probe", action="store_true",
                    help="VIA 0x01 baseline + 0x21 0x22 0x27 0x28 between 0x01s (sends 0x01)")
    ap.add_argument("--via-count", type=int, default=20, help="0x01 queries in the baseline")
    ap.add_argument("--marker-probe", action="store_true",
                    help="0x01, 0x21 with byte 31 = 0x5A, 0x01: is the radio payload 32 bytes?")
    ap.add_argument("--quiet", action="store_true",
                    help="with --led: send 0x25 as 25 01 (patch v2 radio quiet switch)")
    ap.add_argument("--packet-gap", type=float, default=0.0,
                    help="ms between the starts of two packets in the LED steps (default 0)")
    ap.add_argument("--pace-gaps", default=",".join(f"{g:g}" for g in PACE_GAPS_MS),
                    help="pace step: gaps to test, ms")
    ap.add_argument("--pace-frames", type=int, default=PACE_FRAMES, help="pace step: frames per gap")
    ap.add_argument("--pace-settle", type=int, default=PACE_SETTLE_MS,
                    help="pace step: ms to wait for a frame's echoes")
    ap.add_argument("--pace-diag-gap", type=float, default=PACE_DIAG_GAP_MS,
                    help="pace step: gap of the no-00-bytes diagnostic row (0 = none)")
    ap.add_argument("--pace-mode-off", action="store_true",
                    help="pace step with SignalRGB mode off (echoes only, LEDs unchanged)")
    ap.add_argument("--soak-packets", type=int, default=SOAK_PACKETS,
                    help=f"soak step: colour packets to send (default {SOAK_PACKETS})")
    ap.add_argument("--soak-probe-every", type=int, default=1,
                    help="soak step with --quiet: a 0x22 query after every N frames (default 1, 0 = none)")
    ap.add_argument("--partial-phases", default=",".join(ph[0] for ph in PARTIAL_PHASES),
                    help="partial step: phases to run (static,dot,fn,wipe)")
    ap.add_argument("--refresh-ms", type=float, default=PARTIAL_REFRESH_MS,
                    help=f"partial step: re-send a group not sent for this long (default {PARTIAL_REFRESH_MS})")
    ap.add_argument("--idle-lead", type=float, default=IDLE_LEAD_S,
                    help=f"idle step: seconds of stream first, no 0x26 after it (default {IDLE_LEAD_S:g}, 0 = none)")
    ap.add_argument("--idle-silent", type=float, default=IDLE_SILENT_S,
                    help=f"idle step: then seconds with nothing sent (default {IDLE_SILENT_S:g})")
    ap.add_argument("--idle-probe-every", type=float, default=IDLE_PROBE_EVERY_S,
                    help=f"idle step: then one 0x22 every this many seconds (default {IDLE_PROBE_EVERY_S:g})")
    ap.add_argument("--idle-probe-for", type=float, default=IDLE_PROBE_FOR_S,
                    help=f"idle step: for this many seconds (default {IDLE_PROBE_FOR_S:g})")
    args = ap.parse_args()
    steps = [s for s in args.steps.split(",") if s]
    if any(s not in LED_STEPS for s in steps):
        ap.error(f"--steps must be from {','.join(LED_STEPS)}")
    walk_leds = parse_leds(args.walk_leds)
    if (args.vid, args.pid) not in DEVICES:
        ap.error("--vid/--pid must be " + " or ".join(f"0x{v:04X}/0x{p:04X}" for v, p in DEVICES))
    if not 0xFF00 <= args.usage_page <= 0xFFFF:
        ap.error("--usage-page must be vendor-defined (0xFF00-0xFFFF)")
    target = dict(vid=args.vid, pid=args.pid, usage_page=args.usage_page, usage=args.usage,
                  **DEVICES[(args.vid, args.pid)])
    if sum((args.via_probe, args.led, args.marker_probe)) > 1:
        ap.error("--via-probe, --marker-probe and --led are separate runs")
    if args.quiet and not args.led:
        ap.error("--quiet only applies to --led (it changes the 0x25 packets)")
    if not 1 <= args.via_count <= 100:
        ap.error("--via-count must be 1..100")
    if not 0 <= args.packet_gap <= 1000:
        ap.error("--packet-gap must be 0..1000 ms")
    try:
        pace_gaps = [float(g) for g in args.pace_gaps.split(",") if g.strip()]
    except ValueError:
        ap.error("--pace-gaps must be numbers, e.g. 1,2,5")
    if not pace_gaps or not all(0 < g <= 1000 for g in pace_gaps):
        ap.error("--pace-gaps must be 1 or more gaps of 0 < gap <= 1000 ms")
    if not 1 <= args.pace_frames <= 200:
        ap.error("--pace-frames must be 1..200")
    if not 50 <= args.pace_settle <= 5000:
        ap.error("--pace-settle must be 50..5000 ms")
    if not 0 <= args.pace_diag_gap <= 1000:
        ap.error("--pace-diag-gap must be 0..1000 ms")
    if "pace" in steps:
        if not target["radio"]:
            ap.error("--steps pace is for the receiver (--vid 0x6D66 --pid 0x8888): it measures radio echoes")
        if (len(pace_gaps) + (1 if args.pace_diag_gap else 0)) * args.pace_frames > MAX_PACE_TAGS:
            ap.error(f"pace: rows x frames must be at most {MAX_PACE_TAGS}")
    if args.pace_mode_off and steps != ["pace"]:
        ap.error("--pace-mode-off needs --steps pace alone")
    for s in RADIO_ONLY_STEPS:
        if s in steps and not target["radio"]:
            ap.error(f"--steps {s} is for the receiver (--vid 0x6D66 --pid 0x8888)")
    if {"soak", "partial", "idle"} & set(steps) and args.packet_gap < RADIO_STEP_MIN_GAP_MS:
        ap.error(f"--steps soak / partial / idle need --packet-gap {RADIO_STEP_MIN_GAP_MS} or more: "
                 "the damaged echoes seen so far came with packets closer together")
    if "idle" in steps and steps != ["idle"]:
        ap.error("--steps idle runs alone")
    if not (0 <= args.idle_lead <= 3600 and 0 <= args.idle_silent <= 3600 and 0 <= args.idle_probe_for <= 3600):
        ap.error("--idle-lead / --idle-silent / --idle-probe-for must be 0..3600 s")
    if not 1 <= args.idle_probe_every <= 60:
        ap.error("--idle-probe-every must be 1..60 s")
    if not 10 <= args.soak_packets <= 40000:
        ap.error("--soak-packets must be 10..40000")
    if not 0 <= args.soak_probe_every <= 100:
        ap.error("--soak-probe-every must be 0..100")
    partial_phases = [ph for ph in args.partial_phases.split(",") if ph]
    if not partial_phases or any(ph not in [x[0] for x in PARTIAL_PHASES] for ph in partial_phases):
        ap.error("--partial-phases must be from " + ",".join(x[0] for x in PARTIAL_PHASES))
    if not 500 <= args.refresh_ms <= 1900:
        ap.error("--refresh-ms must be 500..1900 (the firmware falls back after 2000 ms)")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    led_name = (("paceoff" if args.pace_mode_off else "pace") if "pace" in steps else
                "soak" if "soak" in steps else "partial" if "partial" in steps else
                "idle" if "idle" in steps else "led")
    mode = ("list" if args.list else (led_name + "quiet" if args.quiet else led_name) if args.led
            else "viaprobe" if args.via_probe else "markerprobe" if args.marker_probe else "readonly")
    log = Log(LOG_DIR / f"srgb_hidtest_{stamp}_{target['tag']}{mode}.log")
    log.info(f"srgb_hidtest {mode}  {dt.datetime.now().isoformat(timespec='seconds')}  "
             f"hidapi {hid.version_str()}  log {log.path}")
    log.info(f"target: {target['vid']:04X}:{target['pid']:04X} {target['name']}, "
             f"usage page 0x{target['usage_page']:04X}, usage 0x{target['usage']:02X}")
    log.info("Reminder: VIA, the Meletrix Console and SignalRGB must be closed.")
    if args.led:
        log.info(f"packet gap: {args.packet_gap:g} ms" + (" (back to back)" if not args.packet_gap else "")
                 + (f"; at least {RADIO_CMD_GAP_MS} ms after every 0x25 / 0x26" if target["radio"] else ""))
        if target["radio"] and not args.packet_gap and set(steps) - {"errors", "pace"}:
            log.info("  note: back-to-back packets on the receiver. On 2026-10-21 (patch v2, quiet off) "
                     "only the first packet of each burst reached the keyboard; pass --packet-gap")

    names, layout_x = None, [i * 31 // N_LEDS for i in range(N_LEDS)]
    if LAYOUT_JSON.exists():
        lay = json.loads(LAYOUT_JSON.read_text(encoding="utf-8"))
        names = lay["names"]
        layout_x = [p[0] for p in lay["positions"]]

    dev = None
    link = None
    rc = 1
    try:
        info = find_interface(log, dict(target, list_caps=args.list))
        caps = hid_caps(info["path"])
        log.info(f"  raw HID collection: {format_caps(caps)}")
        if args.list:
            return 0
        check_caps(caps, target, log)
        dev = hid.device()
        dev.open_path(info["path"])
        if args.via_probe:
            allowed = VIA_PROBE_CMDS
        elif args.marker_probe:
            allowed = MARKER_PROBE_CMDS
        else:
            allowed = READ_ONLY_CMDS | (LED_CMDS if args.led else frozenset())
        link = Link(dev, log, allowed, radio=target["radio"], gap_ms=args.packet_gap)
        log.info("allowed commands: " + " ".join(f"0x{c:02X}" for c in sorted(allowed)))
        mode_on = cmd_packet(CMD_MODE_ON, QUIET_BIT if args.quiet else 0)

        if args.via_probe:
            ok, _ = via_probe(link, log, args.via_count, target["radio"])
        elif args.marker_probe:
            ok = marker_probe(link, log)
        else:
            ok = read_only_checks(link, log, target["radio"])
        if args.led:
            if not ok:
                log.info("\nLED tests skipped: the read-only checks did not pass.")
            else:
                results = {}
                if target["radio"]:
                    say(log, f"sending 0x25 {mode_on[1]:02x}, then {RADIO_CMD_GAP_MS} ms later 0x26: "
                             f"SignalRGB mode off, quiet {'on' if args.quiet else 'off'} (known state). "
                             "The LEDs may freeze for a moment, then show the normal effect")
                    link.send(mode_on)
                    link.send(cmd_packet(CMD_MODE_OFF))
                    if link.settle():
                        log.info("  note: unexpected packet(s) after the known-state prelude")
                else:
                    say(log, "sending 0x26 (SignalRGB mode off) to start from a known state")
                    link.send(cmd_packet(CMD_MODE_OFF))
                for s in [s for s in LED_STEPS if s in steps]:
                    e0 = dict(link.echoes)
                    if s not in ("errors", "predrop") and not link.mode_on_sent and not args.pace_mode_off                             and not (s == "idle" and not args.idle_lead):
                        say(log, f"sending 0x25{' 01 (quiet on)' if args.quiet else ''} (SignalRGB mode on)")
                        link.send(mode_on)
                    if s == "errors":
                        results[s] = step_errors(link, log)
                    elif s == "predrop":
                        results[s] = step_predrop(link, log)
                    elif s == "colors":
                        results[s] = step_colors(link, log)
                    elif s == "bench":
                        results[s] = step_bench(link, log, layout_x)
                    elif s == "walk":
                        results[s] = step_walk(link, log, names, walk_leds, args.walk_delay)
                    elif s == "timeout":
                        results[s] = step_timeout(link, log)
                    elif s == "hold":
                        results[s] = step_hold(link, log, args.hold_seconds)
                    elif s == "pace":
                        results[s] = step_pace(link, log, pace_gaps, args.pace_frames,
                                               args.pace_settle, args.pace_diag_gap, args.pace_mode_off)
                    elif s == "soak":
                        results[s] = step_soak(link, log, args.soak_packets, args.soak_probe_every)
                    elif s == "partial":
                        results[s] = step_partial(link, log, args.refresh_ms, partial_phases)
                    elif s == "idle":
                        results[s] = step_idle(link, log, args.idle_lead, args.idle_silent,
                                               args.idle_probe_every, args.idle_probe_for)
                    if link.radio:
                        extra = link.settle()
                        if extra:
                            log.info(f"  FAIL: {len(extra)} unexpected packet(s) after the step")
                            results[s] = False
                        d = {k: link.echoes[k] - e0[k] for k in e0}
                        log.info(f"  radio echoes: {d['seen']}/{d['expected']} seen, {d['missing']} missing"
                                 + (" (quiet on: none expected)" if args.quiet else ""))
                        if d["missing"] and s not in ("bench", "pace", "soak"):
                            log.info("  FAIL: missing echoes = lost packets or lost replies")
                            results[s] = False
                        elif d["missing"] and s in ("pace", "soak"):
                            log.info(f"  note: in the {s} step lost and damaged echoes are the measurement "
                                     "(results above)")
                        elif d["missing"]:
                            log.info("  note: the bench runs at full speed; throughput is step 6")
                if link.mode_on_sent:
                    say(log, "sending 0x26 (SignalRGB mode off). LOOK: normal effect at once")
                    link.send(cmd_packet(CMD_MODE_OFF))
                    time.sleep(0.2)
                    link.drain()
                log.info("\nautomatic checks: " + ", ".join(
                    f"{k} {'ok' if v else 'FAIL'}" for k, v in results.items()))
                ok = ok and all(results.values())
        if link.radio and link.echoes["expected"]:
            link.settle()
            log.info(f"radio echoes in total: {link.echoes['seen']}/{link.echoes['expected']} seen, "
                     f"{link.echoes['missing']} missing")
        if args.via_probe or args.marker_probe:
            log.info(f"\nRESULT: {'PASS' if ok else 'FAIL'} (= the 0x01 "
                     f"{'baseline' if args.via_probe else 'queries around it'}; the verdict "
                     f"above is the finding). {link.sent} packets sent.")
        else:
            log.info(f"\nRESULT: {'PASS' if ok else 'FAIL'} (automatic checks only; "
                     f"visual checks are yours). {link.sent} packets sent.")
        rc = 0 if ok else 1
    except KeyboardInterrupt:
        log.info("\ninterrupted")
    except (SafetyError, IOError, OSError) as e:
        log.info(f"\nSTOPPED: {type(e).__name__}: {e}")
    finally:
        if link is not None and link.mode_on_sent:
            try:
                log.info("cleanup: sending 0x26 (SignalRGB mode off)")
                link.send(cmd_packet(CMD_MODE_OFF))
            except Exception as e:      # still close the device and the log
                log.info(f"cleanup failed: {e}")
        if dev is not None:
            dev.close()
        log.info(f"log written to {log.path}")
        log.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
