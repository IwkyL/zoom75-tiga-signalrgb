"""Emulator tests for patch v2 (no hardware involved).

usage: .venv/Scripts/python.exe -I tools/emutest_v2.py [--v1 HEX] [--v2 HEX] [--quick]

Runs the real bytes of out_v2/patched_v2.hex (and, for comparisons, of the flashed
v1 out/patched.hex) in Unicorn. The radio path is driven for real: every radio test
enters at the stock bl 0x080140AC in the UART line handler (0x0801528A) with a
"+DATA:<n>\\r\\n<payload>\\r\\n" chunk, so the stock +DATA: handler parses it, calls
hook C, builds the "HID=" line in place and calls hook D. Stubbed: dev_write
0x08008050 (records the queued line), the five firmware functions of emutest.py,
and, for the pass-through sweep only, VIA 0x0800F620 (records its entry registers;
running the real VIA on every ID could reach its EEPROM/flash paths). The real VIA
runs only for 0x01 and 0x21-0x28.

  1. pass-through sweep, v1 vs v2: all 248 non-SignalRGB first bytes in modes
     0/1/2/3, 0x21-0x28 in modes 0/1/3 and with n outside 4..32 in mode 2, two radio
     states and two flag words: VIA entered with identical r0-r2, SP and LR (a true
     tail call), identical HID= line, return value and RAM; v2 code writes nothing
  2. real VIA for 0x01 and 0x21-0x28 in modes 0/1/3 (and 0x01 in mode 2), v1 vs v2
  3. mode 2, handled: query replies = v1's USB reply bytes inside the HID= line; the
     stream -> set_color sequence; error codes; quiet on/off -> line queued or not;
     skip_reply never stale; writes stay inside payload[0..n-1] + the radio state
  4. render: radio stream, 2 s timeout edge, resume, 0x26, mode switches 2 -> 1/3,
     both links live (2.4G + cable), RGB off/suspend, v1 state lost, timer going
     backwards; hand-back through v1's switch-back
  5. USB identical to v1: every emutest.py scenario runs on v1 and on v2 and must give
     identical return values, stub calls, registers and RAM after every step;
     srgb_render2 must enter v1 srgb_render with identical r0, SP and LR in every
     non-radio state (plus an explicit sweep of non-radio states)
  6. stack: SP balanced and r4-r11 preserved on every run; depth below hooks B/C/D
     compared with VIA's frame and with the stock reply path
--quick skips the long sweep of section 1 (runs every 7th case).
"""
import contextlib
import importlib.util
import io
import struct
import sys
from pathlib import Path

from intelhex import IntelHex
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
                               UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
                               UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
                               UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_R12, UC_ARM_REG_SP)

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
V1_HEX = ROOT / "out" / "patched.hex"
V2_HEX = ROOT / "out_v2" / "patched_v2.hex"

FLASH, RAM = 0x08000000, 0x20000000
STACK_TOP = 0x20007DB8
ENTRY_SP = STACK_TOP - 0x400            # SP at every hook site (as emutest.py)
STACK_WIN = (ENTRY_SP - 0x800, ENTRY_SP)
HID_SITE, HID_RET = 0x08014D42, 0x08014D46          # hook A (USB HID task)
RGB_SITE, RGB_RET = 0x0801017C, 0x08010180          # hook B (rgb_matrix_task)
RADIO_SITE, RADIO_RET = 0x0801528A, 0x0801528E      # bl 0x080140AC in the line handler
HOOK_C, HOOK_C_RET = 0x080140E2, 0x080140E6
HOOK_D, HOOK_D_RET = 0x0801413E, 0x08014142
VIA, DEV_WRITE, V1_RENDER = 0x0800F620, 0x08008050, 0x0801A910
PATCH_START = 0x0801A828
BUF = 0x20004000                        # USB HID buffer (firmware ZI; content irrelevant)
RADIO_BUF, RADIO_BUF_LEN = 0x20004100, 0xC0
MODE, FLAGS, USBST, RGB = 0x20000B8C, 0x20000B88, 0x20005EC2, 0x20000AC0
V1S, RADIO = 0x20007E00, 0x20007E10
SRGB, SRGR = 0x53524742, 0x53524752
VBUS, BIT10 = 1 << 9, 1 << 10
SRGB_IDS = range(0x21, 0x29)

STUBS = {0x0800FFE8: "set_color", 0x0800FFF8: "set_color_all", 0x08009F64: "raw_hid_send",
         0x08013400: "timer_read32", 0x080102C4: "render", DEV_WRITE: "dev_write"}
ARGS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
CALLEE_SAVED = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
                UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]
ALL_REGS = list(ARGS) + CALLEE_SAVED + [UC_ARM_REG_R12, UC_ARM_REG_SP]
RW = (ROOT / "analysis" / "tiga_v11_ram_rw.bin").read_bytes()

passed, failed = 0, 0


def expect(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("  FAIL", msg)
    return cond


def masked(ram):
    m = bytearray(ram)
    m[STACK_WIN[0] - RAM:STACK_WIN[1] - RAM] = bytes(STACK_WIN[1] - STACK_WIN[0])
    return bytes(m)


def first_diff(a, b):
    i = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), None)
    return "none" if i is None else f"0x{RAM + i:08X}: {a[i]:02X} vs {b[i]:02X}"


class Board:
    """One emulated keyboard: firmware image, RW data, junk in the RAM gap."""

    def __init__(self, hexpath, via_stub=False, real_set_color=False):
        ih = IntelHex(str(hexpath))
        self.end = ih.maxaddr() + 1
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc.mem_map(FLASH, 0x40000)
        self.uc.mem_write(FLASH, ih.tobinstr(start=FLASH, end=ih.maxaddr()))
        self.uc.mem_map(RAM, 0x8000)
        self.uc.mem_write(RAM, RW)                                  # RW data, rest zero (ZI)
        self.uc.mem_write(STACK_TOP, b"\xA5" * (RAM + 0x8000 - STACK_TOP))   # junk in the gap
        self.stubs = dict(STUBS)
        if via_stub:
            self.stubs[VIA] = "via"
        if real_set_color:
            del self.stubs[0x0800FFE8]
        for a in self.stubs:
            self.uc.hook_add(UC_HOOK_CODE, self._stub, begin=a, end=a)
        for a in (HOOK_C, HOOK_C_RET, HOOK_D, HOOK_D_RET, RGB_SITE, RGB_RET, V1_RENDER):
            self.uc.hook_add(UC_HOOK_CODE, self._mark, begin=a, end=a)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._write, begin=RAM, end=RAM + 0x7FFF)
        self.now = 1000
        self.reset_records()
        # Live USB, RGB on (mode 5), not suspended (as emutest.py).
        self.w8(MODE, 3)
        self.w32(FLAGS, VBUS)
        self.w8(USBST, 3)
        self.w8(RGB + 0x18, (5 << 2) | 1)

    def reset_records(self):
        self.calls, self.lines, self.via, self.v1_entries, self.writes = [], [], [], [], []
        self.hook_c = None                  # (r0, r1, r2, sp) at the hook C site
        self.window, self.low = None, {}    # lowest stack byte written inside each hook

    # ---- hooks ---------------------------------------------------------------
    def _stub(self, uc, addr, size, _):
        name = self.stubs[addr]
        r = [uc.reg_read(x) for x in ARGS]
        sp, lr = uc.reg_read(UC_ARM_REG_SP), uc.reg_read(UC_ARM_REG_LR)
        if name == "dev_write":
            self.lines.append((r[0], bytes(uc.mem_read(r[1], r[2])), r[2], r[3], sp, lr))
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif name == "via":
            self.via.append((r[0], r[1], r[2], sp, lr))
            if r[1]:
                uc.mem_write(r[0], b"\xFF")      # like VIA's unhandled path: data[0] = 0xFF
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif name == "timer_read32":
            self.calls.append(("timer_read32", []))
            uc.reg_write(UC_ARM_REG_R0, self.now)
        else:
            args = r[:]
            if name == "raw_hid_send":
                args.append(bytes(uc.mem_read(r[0], r[1])))
            self.calls.append((name, args))
            for x in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3):
                uc.reg_write(x, 0xDEAD0000 | x)
            uc.reg_write(UC_ARM_REG_R0, 0xDEAD0000)
        uc.reg_write(UC_ARM_REG_PC, lr | 1)

    def _mark(self, uc, addr, size, _):
        if addr == HOOK_C:
            self.hook_c = tuple(uc.reg_read(x) for x in ARGS[:3]) + (uc.reg_read(UC_ARM_REG_SP),)
            self.window = "C"
        elif addr == HOOK_D:
            self.window = "D"
        elif addr == RGB_SITE:
            self.window = "B"
        elif addr in (HOOK_C_RET, HOOK_D_RET, RGB_RET):
            self.window = None
        elif addr == V1_RENDER:
            self.v1_entries.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_SP),
                                    uc.reg_read(UC_ARM_REG_LR)))

    def _write(self, uc, access, addr, size, value, _):
        pc = uc.reg_read(UC_ARM_REG_PC)
        if PATCH_START <= pc < self.end:
            self.writes.append((pc, addr, size))
        if self.window and STACK_WIN[0] <= addr < STACK_WIN[1]:
            self.low[self.window] = min(self.low.get(self.window, addr), addr)

    # ---- memory --------------------------------------------------------------
    def r8(self, a):
        return self.uc.mem_read(a, 1)[0]

    def w8(self, a, v):
        self.uc.mem_write(a, bytes([v & 0xFF]))

    def r32(self, a):
        return struct.unpack("<I", self.uc.mem_read(a, 4))[0]

    def w32(self, a, v):
        self.uc.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))

    def ram(self):
        return bytes(self.uc.mem_read(RAM, 0x8000))

    def load(self, ram):
        self.uc.mem_write(RAM, ram)

    def radio_state(self):
        r = self.uc.mem_read(RADIO, 11)
        return dict(magic=struct.unpack_from("<I", r)[0], last_ms=struct.unpack_from("<I", r, 4)[0],
                    enabled=r[8], quiet=r[9], skip=r[10])

    def set_radio(self, enabled=0, quiet=0, skip=0, last_ms=0, magic=SRGR):
        self.uc.mem_write(RADIO, struct.pack("<IIBBB", magic, last_ms, enabled, quiet, skip))

    # ---- runs ----------------------------------------------------------------
    def _run(self, site, ret, regs):
        self.reset_records()
        canary = [0x11110000 + i for i in range(len(CALLEE_SAVED))]
        for r, v in zip(CALLEE_SAVED, canary):
            self.uc.reg_write(r, v)
        for r, v in regs.items():
            self.uc.reg_write(r, v)
        self.uc.reg_write(UC_ARM_REG_SP, ENTRY_SP)
        self.uc.emu_start(site | 1, ret, count=200000)
        ok = self.uc.reg_read(UC_ARM_REG_PC) == ret and self.uc.reg_read(UC_ARM_REG_SP) == ENTRY_SP
        for r, want in zip(CALLEE_SAVED, canary):
            ok &= self.uc.reg_read(r) == regs.get(r, want)
        self.balanced = ok
        return self.uc.reg_read(UC_ARM_REG_R0)

    def radio(self, payload, n=None):
        """One radio chunk "+DATA:<n>\\r\\n<payload>\\r\\n" through the real handler."""
        n = len(payload) if n is None else n
        head = b"+DATA:%d\r\n" % n
        chunk = head + bytes(payload) + b"\r\n"
        self.uc.mem_write(RADIO_BUF, chunk + bytes(RADIO_BUF_LEN - len(chunk)))
        self.payload = RADIO_BUF + len(head)
        self.n = n
        return self._run(RADIO_SITE, RADIO_RET, {UC_ARM_REG_R0: RADIO_BUF, UC_ARM_REG_R1: 0,
                                                 UC_ARM_REG_R2: 1})

    def usb(self, packet, length=32):
        self.uc.mem_write(BUF, bytes(packet) + bytes(32 - len(packet)))
        return self._run(HID_SITE, HID_RET, {UC_ARM_REG_R0: BUF, UC_ARM_REG_R1: length})

    def render(self, effect=5):
        self.w8(RGB + 3, 1)
        self._run(RGB_SITE, RGB_RET, {UC_ARM_REG_R0: effect, UC_ARM_REG_R4: effect})

    def names(self):
        return [c[0] for c in self.calls if c[0] != "timer_read32"]

    def regs(self):
        return [self.uc.reg_read(r) for r in ALL_REGS]


def pkt(*body, n=32):
    p = bytearray(n)
    p[:len(body)] = bytes(body)
    return bytes(p)


def flag_byte(flags):
    f = 0xD4 if flags & BIT10 else 0x94
    return f | 0x20 if flags & VBUS else f


def hid_line(reply, n, flags):
    return b"HID=" + bytes([(n + 1) & 0xFF, flag_byte(flags)]) + bytes(reply[:n]) + b"\r\n"


def ram_writes(writes):
    """Writes by patch code outside its own stack frames."""
    return [w for w in writes if not STACK_WIN[0] <= w[1] < STACK_WIN[1]]


def check_run(b, label):
    expect(b.balanced, f"{label}: returns to the caller, SP balanced, r4-r11 preserved")


# ---- 1. pass-through sweep ------------------------------------------------------

def sweep(v1hex, v2hex, quick):
    print("1. pass-through sweep (VIA stubbed): v1 vs v2")
    b1, b2 = Board(v1hex, via_stub=True), Board(v2hex, via_stub=True)
    expect(b1.ram() == b2.ram(), "v1 and v2 boards start from identical RAM")
    cases = []
    for mode in (0, 1, 2, 3):
        for first in range(256):
            if mode == 2 and first in SRGB_IDS:
                continue
            cases.append((mode, first, 32))
    for mode in (0, 1, 3):
        for first in SRGB_IDS:
            for n in (1, 3, 4, 8, 31, 33):
                cases.append((mode, first, n))
    for first in SRGB_IDS:
        for n in (1, 2, 3, 33, 34, 48, 64, 100, 140):
            cases.append((2, first, n))
    for first in (0x00, 0x01, 0x02, 0x07, 0x11, 0x13, 0x1C, 0x20, 0x29, 0xFF):
        for n in (1, 3, 4, 33, 64):
            cases.append((2, first, n))
    states = [("radio state junk (never initialised)", None),
              ("radio state initialised: enabled 1, quiet 1", dict(enabled=1, quiet=1, last_ms=0x1234))]
    flag_sets = (0, VBUS | BIT10)
    count = bad = 0
    for sname, st in states:
        for flags in flag_sets:
            for b in (b1, b2):
                b.load(Board.base_ram)
                b.w32(FLAGS, flags)
                if st:
                    b.set_radio(**st)
            base = b1.ram()
            expect(base == b2.ram(), f"[{sname}, flags 0x{flags:03X}] identical start RAM")
            for i, (mode, first, n) in enumerate(cases):
                if quick and i % 7:
                    continue
                payload = bytes([first]) + bytes((k * 37 + 11) & 0xFF for k in range(1, n))
                res = []
                for b in (b1, b2):
                    b.load(base)
                    b.w8(MODE, mode)
                    ret = b.radio(payload, n)
                    res.append(dict(ret=ret, via=list(b.via), lines=list(b.lines), calls=list(b.calls),
                                    ram=masked(b.ram()), hook_c=b.hook_c, writes=ram_writes(b.writes),
                                    ok=b.balanced, payload=b.payload))
                r1, r2 = res
                count += 1
                label = f"[{sname}, flags 0x{flags:03X}] mode {mode} id 0x{first:02X} n {n}"
                good = True
                good &= expect(len(r2["via"]) == 1 and r2["via"] == r1["via"],
                               f"{label}: VIA entered once, same r0-r2/SP/LR as v1")
                if r2["via"]:
                    v = r2["via"][0]
                    good &= expect(v[:3] == (r2["payload"], n & 0xFF, 0), f"{label}: VIA args (payload, n, 0)")
                    good &= expect(v[3] == r2["hook_c"][3] and v[4] == HOOK_C_RET | 1,
                                   f"{label}: VIA entered with the hook site's SP and LR (tail call)")
                good &= expect(r2["lines"] == r1["lines"] and len(r2["lines"]) == 1,
                               f"{label}: identical HID= line queued once")
                good &= expect(r2["ret"] == r1["ret"], f"{label}: same return value")
                good &= expect(r2["ram"] == r1["ram"], f"{label}: identical RAM ({first_diff(r1['ram'], r2['ram'])})")
                good &= expect(not r2["writes"], f"{label}: v2 code writes no RAM")
                good &= expect(not r2["calls"] and not r1["calls"], f"{label}: no other firmware calls")
                good &= expect(r1["ok"] and r2["ok"], f"{label}: SP balanced, r4-r11 preserved")
                bad += not good
    print(f"   {count} cases compared, {count - bad} identical")


# ---- 2. real VIA ----------------------------------------------------------------------

def real_via(v1hex, v2hex):
    print("2. real VIA for 0x01 and 0x21-0x28 (pass-through), v1 vs v2")
    b1, b2 = Board(v1hex), Board(v2hex)
    for flags in (0, VBUS):
        for mode in (0, 1, 2, 3):
            for first in [0x01] + ([] if mode == 2 else list(SRGB_IDS)):
                payload = pkt(first)
                res = []
                for b in (b1, b2):
                    b.load(Board.base_ram)
                    b.w32(FLAGS, flags)
                    b.w8(MODE, mode)
                    ret = b.radio(payload)
                    res.append((ret, list(b.lines), list(b.calls), masked(b.ram()), b.balanced,
                                ram_writes(b.writes)))
                (ret1, l1, c1, m1, ok1, _), (ret2, l2, c2, m2, ok2, w2) = res
                label = f"mode {mode} flags 0x{flags:03X} 0x{first:02X}"
                want = (bytes([0x01, 0x00, 0x0B]) + bytes(29)) if first == 0x01 else bytes([0xFF]) + payload[1:]
                expect(l1 == l2 and len(l2) == 1 and l2[0][1] == hid_line(want, 32, flags)
                       and l2[0][0] == 2 and l2[0][3] == 500 and l2[0][2] == 40,
                       f"{label}: identical line {'01 00 0b' if first == 1 else 'ff + echo'} (dev 2, 40 B, 500 ms)")
                expect(ret1 == ret2 and c1 == c2 and not c2, f"{label}: same return, no stub calls")
                expect(m1 == m2, f"{label}: identical RAM ({first_diff(m1, m2)})")
                expect(ok1 and ok2 and not w2, f"{label}: balanced; v2 code writes no RAM")


# ---- 3. mode 2, handled ---------------------------------------------------------------

def handled(v1hex, v2hex):
    print("3. mode 2 (2.4G), SignalRGB handled by srgb_rx_radio")
    b = Board(v2hex, via_stub=True)
    u = Board(v1hex)                       # v1 on USB, for the reference reply bytes
    b.w8(MODE, 2)
    b.w32(FLAGS, 0)
    b.w8(USBST, 0)

    def send(payload, n=None, label=""):
        n = len(payload) if n is None else n
        before = b.ram()
        b.radio(payload, n)
        check_run(b, label)
        expect(not b.via, f"{label}: VIA not called")
        rs = b.radio_state()
        expect(rs["magic"] == SRGR and rs["skip"] == 0, f"{label}: radio state valid, skip_reply 0 after hook D")
        # every write by patch code: radio state, the payload's first n bytes, or the stack
        bad = [(pc, a) for pc, a, sz in b.writes
               if not (RADIO <= a and a + sz <= RADIO + 16
                       or b.payload <= a and a + sz <= b.payload + min(n, 32)
                       or STACK_WIN[0] <= a < STACK_WIN[1])]
        expect(not bad, f"{label}: patch writes only the radio state and payload[0..{n - 1}] "
                        f"({len(b.writes)} writes){'' if not bad else ' BAD ' + str(bad[:3])}")
        after = b.ram()
        outside = [RAM + i for i in range(0x8000) if before[i] != after[i]
                   and not (RADIO <= RAM + i < RADIO + 16 or RADIO_BUF <= RAM + i < RADIO_BUF + RADIO_BUF_LEN
                            or STACK_WIN[0] <= RAM + i < STACK_WIN[1])]
        expect(not outside, f"{label}: no other RAM changed ({[hex(a) for a in outside[:4]]})")
        return b.lines

    print("   queries: v1 USB reply bytes inside the HID= line")
    for cmd in (0x21, 0x22, 0x23, 0x27, 0x28):
        u.load(Board.base_ram)
        u.usb(pkt(cmd, 0x55, 0x66, 0x77, 0x88, *([0x99] * 27)))
        ref = [c[1][4] for c in u.calls if c[0] == "raw_hid_send"]
        expect(len(ref) == 1 and len(ref[0]) == 32, f"0x{cmd:02X}: v1 USB reference reply")
        for n in (4, 5, 8, 31, 32):
            body = pkt(cmd, 0x55, 0x66, 0x77, 0x88, *([0x99] * 27))[:n]
            lines = send(body, n, f"0x{cmd:02X} n {n}")
            expect(len(lines) == 1 and lines[0][1] == hid_line(ref[0], n, 0) and lines[0][2] == n + 8
                   and lines[0][0] == 2 and lines[0][3] == 500,
                   f"0x{cmd:02X} n {n}: HID= line carries v1's reply {ref[0][:4].hex(' ')} + zeros")
            expect(not b.calls, f"0x{cmd:02X} n {n}: no set_color / raw_hid_send / timer")

    print("   stream before 0x25 is dropped, but echoed (quiet off by default)")
    p = pkt(0x24, 0, 3, *range(1, 10))
    lines = send(p, label="0x24 before enable")
    expect(not b.calls, "0x24 before enable: no set_color")
    expect(len(lines) == 1 and lines[0][1] == hid_line(p, 32, 0), "0x24 before enable: exact echo queued")
    expect(b.radio_state()["enabled"] == 0, "radio still disabled")

    print("   stream errors: always replied, quiet on or off")
    errors = [((80, 7), 253, 32), ((86, 1), 253, 32), ((255, 255), 253, 32), ((0, 10), 254, 32),
              ((70, 12), 254, 32), ((0, 9), 254, 29), ((0, 1), 254, 5)]
    for quiet in (0, 1):
        b.now += 10
        send(pkt(0x25, quiet), label=f"0x25 {quiet:02x}")
        for (start, count), code, n in errors:
            p = pkt(0x24, start, count, *([0x42] * 29))[:n]
            lines = send(p, n, f"error {start}+{count} n {n} quiet {quiet}")
            expect(not b.calls, f"error {start}+{count} n {n}: no set_color")
            expect(len(lines) == 1 and lines[0][1] == hid_line(bytes([0x24, code]) + bytes(30), n, 0),
                   f"error {start}+{count} n {n} quiet {quiet}: line 24 {code:02x} + zeros")
        p = pkt(0x24, 84, 2, 1, 2, 3, 4, 5, 6)
        send(p, label=f"84+2 quiet {quiet}")
        expect([c[1][0] for c in b.calls if c[0] == "set_color"] == [84, 85], f"quiet {quiet}: LEDs 84-85 accepted")
        p = pkt(0x24, 0, 0, n=4)
        lines = send(p, 4, f"count 0, n 4, quiet {quiet}")
        expect(not [c for c in b.calls if c[0] == "set_color"] and len(lines) == 1 - quiet,
               f"count 0 with n 4: valid, no LED, line iff quiet off")

    print("   0x25 quiet bit = byte 1 bit 0; line queued iff quiet off")
    for byte1, quiet in ((0x00, 0), (0x01, 1), (0x03, 1), (0xFE, 0), (0xFF, 1), (0x00, 0)):
        b.now += 7
        p = pkt(0x25, byte1, 0x77)
        lines = send(p, label=f"0x25 {byte1:02x}")
        rs = b.radio_state()
        expect(rs["enabled"] == 1 and rs["quiet"] == quiet and rs["last_ms"] == b.now,
               f"0x25 {byte1:02x}: enabled 1, quiet {quiet}, last_ms stamped")
        expect(len(lines) == (0 if quiet else 1) and (quiet or lines[0][1] == hid_line(p, 32, 0)),
               f"0x25 {byte1:02x}: {'no line' if quiet else 'exact echo'}")

    print("   stream: set_color sequence; echo iff quiet off")
    for quiet in (0, 1):
        send(pkt(0x25, quiet), label=f"0x25 {quiet:02x}")
        for start, count in ((0, 9), (77, 9), (81, 5), (40, 1)):
            b.now += 3
            rgb = [(start * 3 + k * 5) & 0xFF for k in range(3 * count)]
            p = pkt(0x24, start, count, *rgb)
            lines = send(p, label=f"stream {start}+{count} quiet {quiet}")
            sc = [c[1][:4] for c in b.calls if c[0] == "set_color"]
            expect(sc == [[start + i, rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2]] for i in range(count)],
                   f"stream {start}+{count}: {count} set_color calls with index/r/g/b in order")
            expect(b.radio_state()["last_ms"] == b.now, f"stream {start}+{count}: last_ms stamped")
            expect(len(lines) == 1 - quiet and (quiet or lines[0][1] == hid_line(p, 32, 0)),
                   f"stream {start}+{count} quiet {quiet}: {'no line' if quiet else 'exact echo'}")
            expect(bytes(b.uc.mem_read(b.payload, 32)) == p, f"stream {start}+{count}: payload not modified")

    print("   0x26: disables; line iff quiet off; quiet survives 0x26")
    for quiet in (0, 1):
        send(pkt(0x25, quiet), label="0x25")
        p = pkt(0x26, 0x31)
        lines = send(p, label=f"0x26 quiet {quiet}")
        expect(b.radio_state()["enabled"] == 0 and b.radio_state()["quiet"] == quiet,
               f"0x26 quiet {quiet}: enabled 0, quiet kept")
        expect(len(lines) == 1 - quiet, f"0x26 quiet {quiet}: {'no line' if quiet else 'echo'}")
        lines = send(pkt(0x24, 0, 1, 1, 2, 3), label="stream after 0x26")
        expect(not b.calls and len(lines) == 1 - quiet, f"stream after 0x26 quiet {quiet}: dropped, line iff quiet off")

    print("   skip_reply is never stale")
    send(pkt(0x25, 1), label="0x25 01")
    b.set_radio(**{**b.radio_state_args(), "skip": 1})       # a reset between hooks C and D
    b.radio(pkt(0x01))
    expect(len(b.via) == 1 and len(b.lines) == 1 and b.radio_state()["skip"] == 0,
           "stale skip_reply = 1, then VIA 0x01 (pass-through): line queued, skip_reply cleared")
    b.set_radio(**{**b.radio_state_args(), "skip": 1})
    lines = send(pkt(0x27), label="0x27 after stale skip")
    expect(len(lines) == 1, "stale skip_reply = 1, then a query: its reply is queued")
    for first in (0x01, 0x24, 0x02, 0x25, 0x13):         # 0x25 01 keeps quiet on
        b.radio(pkt(first, 1, 1, 1, 2, 3))
        expect(b.radio_state()["skip"] == 0, f"after 0x{first:02X} (quiet on): skip_reply 0")
    b.w8(MODE, 1)
    b.radio(pkt(0x24, 0, 1, 1, 2, 3))
    expect(len(b.via) == 1 and len(b.lines) == 1 and not b.calls,
           "quiet on, mode 1 (BT): 0x24 goes to VIA and its line is queued")
    b.w8(MODE, 2)
    send(pkt(0x25, 0), label="0x25 00")

    print("   flag byte follows the stock rules (VBUS 0x20, bit 10 0x40)")
    for flags in (0, VBUS, BIT10, VBUS | BIT10):
        b.w32(FLAGS, flags)
        lines = send(pkt(0x21), label=f"flags 0x{flags:03X}")
        expect(len(lines) == 1 and lines[0][1][5] == flag_byte(flags), f"flags 0x{flags:03X}: flag byte "
                                                                         f"0x{flag_byte(flags):02X}")
    b.w32(FLAGS, 0)


def _radio_state_args(self):
    s = self.radio_state()
    return dict(enabled=s["enabled"], quiet=s["quiet"], skip=s["skip"], last_ms=s["last_ms"], magic=s["magic"])


Board.radio_state_args = _radio_state_args


# ---- 4. render ----------------------------------------------------------------------

def render_tests(v2hex):
    print("4. render (hook B -> srgb_render2), radio states")
    b = Board(v2hex, via_stub=True)
    b.w8(MODE, 2)
    b.w32(FLAGS, 0)                         # 2.4G on battery
    b.w8(USBST, 0)

    def frame(rgb_of):
        for start in range(0, 86, 9):
            count = min(9, 86 - start)
            b.radio(pkt(0x24, start, count, *[c for i in range(count) for c in rgb_of(start + i)]))

    def radio_renders(label):
        it = b.r8(RGB + 0x0C)
        b.render(5)
        check_run(b, label)
        ok = expect(b.names() == [] and not b.v1_entries, f"{label}: radio renders (no effect, v1 not entered)")
        expect(b.r8(RGB + 3) == 2 and b.r8(RGB + 0x0C) == (it + 1) & 0xFF and b.r8(V1S + 9) == 1,
               f"{label}: task FLUSHING, iter + 1, v1 rendering = 1")
        return ok

    def hands_back(label, clear=True):
        b.w8(RGB + 1, 0x01)
        b.w8(RGB + 2, 0x05)
        b.render(5)
        check_run(b, label)
        expect(b.v1_entries == [(5, ENTRY_SP, RGB_RET | 1)], f"{label}: v1 srgb_render entered (r0 5, same SP/LR)")
        if clear:
            sca = [c[1][:3] for c in b.calls if c[0] == "set_color_all"]
            expect(b.names() == ["set_color_all", "render"] and sca == [[0, 0, 0]],
                   f"{label}: v1 switch-back clears the frame, stock render")
            expect(b.r8(RGB + 1) == 0xFF and b.r8(RGB + 2) == 0xFF and b.r8(V1S + 9) == 0,
                   f"{label}: effect re-init forced, v1 rendering = 0")
        else:
            expect(b.names() == ["render"], f"{label}: stock render")
        b.render(5)
        expect(b.names() == ["render"], f"{label}: stays on the stock effect")

    b.render(5)
    expect(b.names() == ["render"] and b.r32(V1S) == SRGB and b.r32(RADIO) == 0xA5A5A5A5,
           "first render: stock effect; v1 initialises its state; srgb_render2 leaves the radio state alone")
    b.now = 5000
    b.radio(pkt(0x25))
    radio_renders("after radio 0x25")
    b.now = 5010
    frame(lambda i: (i, 2 * i, 255 - i))
    sc = [c[1][:4] for c in b.calls if c[0] == "set_color"]
    expect(len(sc) == 5 and sc[-1] == [85, 85, 170, 170], "last packet of a frame: LEDs 81-85")
    radio_renders("streaming")
    t = b.now
    b.now = t + 1999
    radio_renders("1999 ms after the last packet")
    b.now = t + 2000
    hands_back("2000 ms after the last packet")
    b.now += 10
    b.radio(pkt(0x24, 0, 1, 9, 9, 9))
    radio_renders("stream resumes without 0x25")
    b.radio(pkt(0x26))
    hands_back("radio 0x26")

    b.radio(pkt(0x25))
    radio_renders("0x25 again")
    b.w8(MODE, 1)
    hands_back("switch 2 -> 1 (BT)")
    b.radio(pkt(0x24, 0, 1, 1, 2, 3))
    expect(len(b.via) == 1 and not b.calls, "mode 1: radio 0x24 goes to VIA, no LED write")
    b.w8(MODE, 2)
    b.radio(pkt(0x24, 0, 1, 1, 2, 3))
    radio_renders("back to 2.4G, streaming: renders again (still enabled)")
    b.w8(MODE, 3)
    hands_back("switch 2 -> 3 (USB) with no USB stream")
    b.w8(MODE, 2)
    b.radio(pkt(0x24, 0, 1, 1, 2, 3))
    radio_renders("back to 2.4G")

    print("   USB SignalRGB on the cable while the radio streams")
    b.w32(FLAGS, VBUS)
    b.w8(USBST, 3)
    b.usb(pkt(0x25))
    b.usb(pkt(0x24, 0, 2, 7, 7, 7, 8, 8, 8))
    expect(not b.calls and b.r8(V1S + 8) == 1, "both links live (mode 2): USB 0x25 sets v1 enabled; "
                                               "USB colours dropped (v1 rule mode == 3)")
    b.radio(pkt(0x24, 0, 1, 1, 2, 3))
    expect(len([c for c in b.calls if c[0] == "set_color"]) == 1, "both links live: radio colours written")
    radio_renders("both links live: radio renders")
    b.usb(pkt(0x26))
    radio_renders("USB 0x26 does not stop the radio")
    b.usb(pkt(0x25))
    b.w8(MODE, 3)
    b.usb(pkt(0x24, 0, 1, 4, 5, 6))
    expect(len([c for c in b.calls if c[0] == "set_color"]) == 1, "switch to USB: USB colours written")
    b.render(5)
    expect(b.names() == [] and b.v1_entries == [(5, ENTRY_SP, RGB_RET | 1)] and b.r8(V1S + 9) == 1,
           "switch 2 -> 3 with a fresh USB stream: v1 renders USB, no clear, no effect")
    b.radio(pkt(0x24, 0, 1, 1, 2, 3))
    expect(len(b.via) == 1, "mode 3: radio packets go to VIA")
    b.w8(MODE, 2)
    b.radio(pkt(0x24, 0, 1, 1, 2, 3))
    radio_renders("back to 2.4G with both streams: radio renders")
    b.radio(pkt(0x26))
    hands_back("radio 0x26 with USB enabled but not live (mode 2)")
    b.usb(pkt(0x26))
    b.w32(FLAGS, 0)
    b.w8(USBST, 0)

    print("   RGB off / suspended, lost state, timer going backwards")
    b.radio(pkt(0x25))
    radio_renders("radio on")
    b.w8(RGB + 0x18, 5 << 2)
    hands_back("RGB turned off")
    b.w8(RGB + 0x18, (5 << 2) | 1)
    b.radio(pkt(0x24, 0, 1, 1, 2, 3))
    radio_renders("RGB on again")
    b.w8(RGB, 1)
    hands_back("rgb_matrix suspended")
    b.w8(RGB, 0)
    b.radio(pkt(0x24, 0, 1, 1, 2, 3))
    radio_renders("resumed")
    b.w32(V1S, 0x12345678)
    b.render(5)
    expect(b.names() == ["render"] and b.v1_entries and b.r32(V1S) == SRGB and b.r8(V1S + 9) == 0,
           "v1 state lost: hand-off; v1 re-initialises it and runs the stock effect")
    radio_renders("next frame: radio renders again")
    b.w32(RADIO, 0)
    hands_back("radio state lost while rendering")
    b.radio(pkt(0x25))
    radio_renders("radio state re-initialised by 0x25")
    b.now = 3
    hands_back("timer went backwards (now < last_ms)")
    b.now = 100000
    b.radio(pkt(0x24, 0, 1, 1, 2, 3))
    radio_renders("fresh again")
    b.render(0)
    expect(b.names() == [] and not b.v1_entries, "effect 0 passed in: radio still renders (rgb_lit is the test)")


# ---- 5. USB identical to v1 ---------------------------------------------------------

def differential(v1hex, v2hex):
    print("5. USB identical to v1: every emutest.py scenario on v1 and on v2")
    traces, results = {}, {}
    for tag, hexpath in (("v1", v1hex), ("v2", v2hex)):
        spec = importlib.util.spec_from_file_location(f"emutest_{tag}", TOOLS / "emutest.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        trace = []
        base = mod.Board

        class Rec(base):
            def __init__(self, path):
                super().__init__(path)
                self.entries = []
                self.uc.hook_add(UC_HOOK_CODE, self._v1e, begin=V1_RENDER, end=V1_RENDER)

            def _v1e(self, uc, addr, size, _):
                self.entries.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_SP),
                                     uc.reg_read(UC_ARM_REG_LR)))

            def _record(self, op, ret):
                ram = masked(bytes(self.uc.mem_read(RAM, 0x8000)))
                regs = [self.uc.reg_read(r) for r in ALL_REGS]
                trace.append((op, ret, [(n, list(a)) for n, a in self.calls], ram, regs, list(self.entries)))

            def hid(self, packet, length=32):
                self.entries = []
                ret = super().hid(packet, length)
                self._record(("hid", bytes(packet), length), ret)
                return ret

            def render(self, effect=5):
                self.entries = []
                super().render(effect)
                self._record(("render", effect), None)

        mod.Board = Rec
        argv, sys.argv = sys.argv, ["emutest.py", str(hexpath)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                mod.main()
                code = 0
            except SystemExit as e:
                code = e.code
        sys.argv = argv
        results[tag] = (code, mod.passed, mod.failed)
        traces[tag] = trace
    for tag in ("v1", "v2"):
        code, p, f = results[tag]
        expect(code == 0 and f == 0, f"emutest.py on {tag}: {p} checks passed, {f} failed")
        print(f"   emutest.py on {tag}: {p} checks passed, {f} failed")
    t1, t2 = traces["v1"], traces["v2"]
    expect(len(t1) == len(t2) and len(t1) > 0, f"same number of steps ({len(t1)} / {len(t2)})")
    same = 0
    for i, (s1, s2) in enumerate(zip(t1, t2)):
        op, what = s1[0], s1[0][0]
        ok = s1[0] == s2[0] and s1[1] == s2[1] and s1[2] == s2[2] and s1[3] == s2[3] and s1[4] == s2[4] \
            and s1[5] == s2[5]
        if what == "render":
            ok &= s2[5] == [(op[1], ENTRY_SP, RGB_RET | 1)]
        if not ok:
            print(f"  FAIL step {i} {op[0]}: ret {s1[1] == s2[1]}, calls {s1[2] == s2[2]}, "
                  f"RAM {first_diff(s1[3], s2[3])}, regs {s1[4] == s2[4]}, v1 entry {s1[5]} / {s2[5]}")
        same += ok
    renders = sum(1 for s in t2 if s[0][0] == "render")
    summary = (f"{same}/{len(t1)} steps identical (return value, stub calls with arguments, "
               f"r0-r12/SP, RAM); all {renders} renders enter v1 srgb_render with the hook site's r0/SP/LR")
    expect(same == len(t1), summary)
    print(f"   {summary}")

    print("   explicit non-radio states: v2 render = v1 render")
    b1, b2 = Board(v1hex), Board(v2hex)
    for b in (b1, b2):
        b.render(5)                             # initialise the v1 state
    states = [
        ("radio state junk", dict(magic=0xA5A5A5A5, enabled=1), 2),
        ("radio disabled", dict(enabled=0), 2),
        ("radio enabled, mode 0", dict(enabled=1), 0),
        ("radio enabled, mode 1 (BT)", dict(enabled=1), 1),
        ("radio enabled, mode 3 (USB)", dict(enabled=1), 3),
        ("radio enabled, mode 4", dict(enabled=1), 4),
        ("radio enabled = 2 (not 1)", dict(enabled=2), 2),
    ]
    for usb_live in (False, True):
        for name, st, mode in states:
            for b in (b1, b2):
                b.load(b1.ram() if b is b2 else b.ram())
            for b in (b1, b2):
                b.set_radio(**{"last_ms": b.now, **st})
                b.w8(MODE, mode)
                if usb_live:                        # a fresh USB stream as well
                    b.w8(MODE, 3)
                    b.usb(pkt(0x25))
                    b.w8(MODE, mode)
            r = []
            for b in (b1, b2):
                b.w8(RGB + 1, 1)
                b.render(5)
                r.append((list(b.calls), masked(b.ram()), b.regs(), list(b.v1_entries)))
            label = f"{name}{', USB stream on' if usb_live else ''}"
            expect(r[0] == r[1], f"{label}: identical calls, RAM, registers ({first_diff(r[0][1], r[1][1])})")
            expect(r[1][3] == [(5, ENTRY_SP, RGB_RET | 1)], f"{label}: v1 entered with the hook site's state")
    for name, setup in (("RGB off", lambda b: b.w8(RGB + 0x18, 5 << 2)), ("suspended", lambda b: b.w8(RGB, 1)),
                        ("stale (2000 ms)", lambda b: setattr(b, "now", b.now + 2000))):
        for b in (b1, b2):
            b.load(b1.ram() if b is b2 else b.ram())
        for b in (b1, b2):
            b.w8(RGB + 0x18, (5 << 2) | 1)
            b.w8(RGB, 0)
            b.now = 9000
            b.set_radio(enabled=1, last_ms=9000)
            b.w8(MODE, 2)
            setup(b)
            b.render(5)
        timer_extra = [c for c in b2.calls if c[0] == "timer_read32"]
        expect([c for c in b2.calls if c[0] != "timer_read32"] == [c for c in b1.calls if c[0] != "timer_read32"]
               and masked(b1.ram()) == masked(b2.ram()) and b2.v1_entries == [(5, ENTRY_SP, RGB_RET | 1)],
               f"radio enabled, mode 2, {name}: hand-off; same calls and RAM as v1 "
               f"({len(timer_extra) - len([c for c in b1.calls if c[0] == 'timer_read32'])} extra timer read)")


# ---- 6. stack depth -----------------------------------------------------------------

def stack(v1hex, v2hex):
    print("6. stack depth below the hook sites (bytes)")
    b2 = Board(v2hex, real_set_color=True)
    b1 = Board(v1hex, real_set_color=True)
    for b in (b1, b2):
        b.w8(MODE, 2)
        b.w32(FLAGS, 0)
    rows = []

    def depth(b, window):
        sp = b.hook_c[3] if window in ("C", "D") else ENTRY_SP
        return sp - b.low[window] if window in b.low else 0

    b2.radio(pkt(0x25))
    fb = bytes(b2.uc.mem_read(0x20006D20, 27))
    b2.radio(pkt(0x24, 0, 9, *([200, 100, 50] * 9)))
    expect(len([c for c in b2.calls if c[0] == "set_color"]) == 0 and bytes(b2.uc.mem_read(0x20006D20, 27)) != fb,
           "real set_color ran (LED driver wrote the frame buffer, no stub)")
    rows.append(("v2 hook C, 0x24 with 9 LEDs (real set_color + LED driver)", depth(b2, "C")))
    stream_depth = depth(b2, "C")
    b2.radio(pkt(0x21))
    rows.append(("v2 hook C, query 0x21", depth(b2, "C")))
    b2.radio(pkt(0x24, 0, 10))
    rows.append(("v2 hook C, 0x24 error", depth(b2, "C")))
    b2.radio(pkt(0x21))
    rows.append(("v2 hook D (line queued; dev_write stubbed)", depth(b2, "D")))
    b2.radio(pkt(0x25, 1))
    rows.append(("v2 hook D (line skipped)", depth(b2, "D")))
    for b, tag in ((b1, "v1"), (b2, "v2")):
        b.w8(MODE, 3)
        b.radio(pkt(0x21))
        rows.append((f"{tag} hook C, mode 3, 0x21 -> real VIA (unhandled)", depth(b, "C")))
        b.radio(pkt(0x01))
        rows.append((f"{tag} hook C, mode 3, 0x01 -> real VIA", depth(b, "C")))
        b.w8(MODE, 2)
    via_depth = rows[-4][1]
    expect(rows[-2][1] == rows[-4][1] and rows[-1][1] == rows[-3][1],
           "pass-through: same depth below hook C as v1 (the v2 frame is popped before VIA)")
    b2.radio(pkt(0x24, 0, 1, 1, 2, 3))
    b2.render(5)
    rows.append(("v2 hook B, radio renders", depth(b2, "B")))
    b2.w8(MODE, 1)
    b2.render(5)
    b1.w8(MODE, 1)
    b1.render(5)
    rows.append(("v2 hook B, hand-off to v1 (stock render stubbed)", depth(b2, "B")))
    rows.append(("v1 hook B, same state", depth(b1, "B")))
    for name, d in rows:
        print(f"   {d:4}  {name}")
    print("   static: dev_write 0x08008050 frame 48 B + channel sendmail 0x080081A8 frame 56 B = 104 B,")
    print("           reached on every radio packet below the same SP (V asm prologues)")
    expect(stream_depth <= 104, f"deepest v2 radio path ({stream_depth} B) <= the stock reply path's 104 B")
    expect(stream_depth <= via_depth + 12, f"deepest v2 radio path ({stream_depth} B) is VIA's own frame "
                                           f"({via_depth} B) + at most the LED driver's 12 B")
    expect(rows[-2][1] == rows[-1][1], "hook B hand-off: same depth as v1")


def main():
    args = sys.argv[1:]
    v1hex = Path(args[args.index("--v1") + 1]) if "--v1" in args else V1_HEX
    v2hex = Path(args[args.index("--v2") + 1]) if "--v2" in args else V2_HEX
    Board.base_ram = Board(v1hex).ram()
    expect(Board(v2hex).ram() == Board.base_ram, "v1 and v2 boards start from identical RAM")
    sweep(v1hex, v2hex, "--quick" in args)
    real_via(v1hex, v2hex)
    handled(v1hex, v2hex)
    render_tests(v2hex)
    differential(v1hex, v2hex)
    stack(v1hex, v2hex)
    print(f"\n{passed} checks passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
