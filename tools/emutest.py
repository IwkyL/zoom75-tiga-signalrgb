"""Emulate the patched firmware's SignalRGB code (no hardware involved).

usage: .venv/Scripts/python.exe -I tools/emutest.py [patched.hex]

Loads out/patched.hex into a Unicorn Cortex-M emulator, puts the firmware's
RW data into RAM, fills the unused RAM gap with junk (like after power-on),
and replaces the five firmware functions the patch calls with recording stubs.
Every test enters through the real patched BL at a hook site:
  0x08014D42 (USB HID task)  -> runs until 0x08014D46
  0x0801017C (rgb_matrix_task) -> runs until 0x08010180
and checks return value, stub calls, RAM effects, SP and callee-saved regs.
"""
import struct
import sys
from pathlib import Path

from intelhex import IntelHex
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
                               UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
                               UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
                               UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_SP)

ROOT = Path(__file__).resolve().parent.parent
FLASH, RAM = 0x08000000, 0x20000000
HID_SITE, HID_RET = 0x08014D42, 0x08014D46
RGB_SITE, RGB_RET = 0x0801017C, 0x08010180
BUF = 0x20004000            # fake HID message buffer (inside firmware ZI; content irrelevant)
STACK_TOP = 0x20007DB8      # the firmware's initial SP
STATE = 0x20007E00
MODE, FLAGS, USBST = 0x20000B8C, 0x20000B88, 0x20005EC2
RGB = 0x20000AC0

STUBS = {0x0800FFE8: "set_color", 0x0800FFF8: "set_color_all", 0x08009F64: "raw_hid_send",
         0x08013400: "timer_read32", 0x080102C4: "render"}
CALLEE_SAVED = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
                UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]

passed, failed = 0, 0


def expect(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("  FAIL", msg)


class Board:
    def __init__(self, hexpath):
        ih = IntelHex(str(hexpath))
        img = ih.tobinstr(start=FLASH, end=ih.maxaddr())
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc.mem_map(FLASH, 0x40000)
        self.uc.mem_write(FLASH, img)
        self.uc.mem_map(RAM, 0x8000)
        rw = (ROOT / "analysis" / "tiga_v11_ram_rw.bin").read_bytes()
        self.uc.mem_write(RAM, rw)                                  # RW data, rest zero (ZI)
        self.uc.mem_write(0x20007DB8, b"\xA5" * (0x8000 - 0x7DB8))  # junk in the gap
        self.now = 1000
        self.calls = []
        self.uc.hook_add(UC_HOOK_CODE, self._stub, begin=min(STUBS), end=max(STUBS) + 1)
        # Live USB, RGB on (mode 5), not suspended.
        self.w8(MODE, 3)
        self.w32(FLAGS, 1 << 9)
        self.w8(USBST, 3)
        self.w8(RGB + 0x18, (5 << 2) | 1)

    def _stub(self, uc, addr, size, _):
        name = STUBS.get(addr)
        if name is None:
            return
        args = [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        if name == "raw_hid_send":
            args.append(bytes(uc.mem_read(args[0], args[1])))
        self.calls.append((name, args))
        if name == "timer_read32":
            uc.reg_write(UC_ARM_REG_R0, self.now)
        # Scribble on the caller-saved registers like a real callee may.
        for r in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3):
            uc.reg_write(r, 0xDEAD0000 | r)
        if name != "timer_read32":
            uc.reg_write(UC_ARM_REG_R0, 0xDEAD0000)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)

    def r8(self, a):
        return self.uc.mem_read(a, 1)[0]

    def w8(self, a, v):
        self.uc.mem_write(a, bytes([v & 0xFF]))

    def r32(self, a):
        return struct.unpack("<I", self.uc.mem_read(a, 4))[0]

    def w32(self, a, v):
        self.uc.mem_write(a, struct.pack("<I", v))

    def ram(self):
        """RAM snapshot without the test's HID buffer and the stack window."""
        m = bytearray(self.uc.mem_read(RAM, 0x8000))
        for lo, hi in ((BUF, BUF + 32), (STACK_TOP - 0x500, STACK_TOP - 0x400)):
            m[lo - RAM:hi - RAM] = bytes(hi - lo)
        return bytes(m)

    def _run(self, site, ret, regs):
        self.calls = []
        canary = [0x11110000 + i for i in range(len(CALLEE_SAVED))]
        for r, v in zip(CALLEE_SAVED, canary):
            self.uc.reg_write(r, v)
        for r, v in regs.items():
            self.uc.reg_write(r, v)
        self.uc.reg_write(UC_ARM_REG_SP, STACK_TOP - 0x400)
        self.uc.emu_start(site | 1, ret, count=5000)
        expect(self.uc.reg_read(UC_ARM_REG_PC) == ret, f"returned to 0x{ret:08X}")
        expect(self.uc.reg_read(UC_ARM_REG_SP) == STACK_TOP - 0x400, "SP balanced")
        saved = [self.uc.reg_read(r) for r in CALLEE_SAVED]
        for r, v, want in zip(CALLEE_SAVED, saved, canary):
            # r4 is the effect argument at the render site; it is restored too.
            want = regs.get(r, want)
            expect(v == want, f"callee-saved reg {r} preserved")

    def hid(self, packet, length=32):
        buf = bytes(packet) + bytes(32 - len(packet))
        self.uc.mem_write(BUF, buf)
        self._run(HID_SITE, HID_RET, {UC_ARM_REG_R0: BUF, UC_ARM_REG_R1: length})
        return self.uc.reg_read(UC_ARM_REG_R0)

    def render(self, effect=5):
        # rgb_matrix_task holds the effect in r4 and passes it in r0.
        self.w8(RGB + 3, 1)
        self._run(RGB_SITE, RGB_RET, {UC_ARM_REG_R0: effect, UC_ARM_REG_R4: effect})

    def names(self):
        """Stub calls in order, ignoring timer reads."""
        return [c[0] for c in self.calls if c[0] != "timer_read32"]

    def call(self, name):
        return next((c[1] for c in self.calls if c[0] == name), [None] * 5)


def main():
    hexpath = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out" / "patched.hex"
    b = Board(hexpath)

    print("non-SignalRGB packets pass through untouched")
    for first in [i for i in range(256) if not 0x21 <= i <= 0x28]:
        before = b.ram()
        ret = b.hid([first, 0x24, 0x25, 0x26])
        expect(ret == 0, f"cmd 0x{first:02X} returns 0")
        expect(b.calls == [], f"cmd 0x{first:02X} calls nothing")
        expect(b.ram() == before, f"cmd 0x{first:02X} writes no RAM except stack")
    expect(b.r32(STATE) == 0xA5A5A5A5, "state untouched by non-SignalRGB traffic")
    before = b.ram()
    expect(b.hid([0x21], length=3) == 0 and b.ram() == before, "short packet (len 3) passes through")

    print("render before any SignalRGB command runs the stock effect")
    b.render(7)
    expect(b.names() == ["render"] and b.call("render")[0] == 7, "original render(7) called")
    expect(b.r32(STATE) == 0x53524742 and b.r8(STATE + 8) == 0, "state initialised from junk, disabled")

    print("queries")
    for cmd, want in [(0x21, [0x21, 0, 0, 0]), (0x22, [0x22, 1, 0, 6]), (0x23, [0x23, 0, 0, 0]),
                      (0x27, [0x27, 86, 0, 0]), (0x28, [0x28, 2, 0, 0])]:
        ret = b.hid([cmd, 0x55, 0x66, 0x77, 0x88])
        sends = [c for c in b.calls if c[0] == "raw_hid_send"]
        expect(ret == 1, f"0x{cmd:02X} handled")
        expect(len(sends) == 1 and sends[0][1][0] == BUF and sends[0][1][1] == 32,
               f"0x{cmd:02X} replies once, 32 bytes, in place")
        expect(sends and sends[0][1][4] == bytes(want) + bytes(28), f"0x{cmd:02X} reply bytes {want}")

    print("stream before enable is dropped")
    ret = b.hid([0x24, 0, 3, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    expect(ret == 1 and b.calls == [], "no set_color, no reply")

    print("enable + stream")
    b.now = 5000
    expect(b.hid([0x25]) == 1 and b.r8(STATE + 8) == 1, "0x25 enables")
    expect(b.r32(STATE + 4) == 5000, "enable stamps the time")
    b.now = 5010
    rgb = list(range(10, 37))
    b.hid([0x24, 77, 9] + rgb)
    sc = [c[1][:4] for c in b.calls if c[0] == "set_color"]
    expect(sc == [[77 + i, rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2]] for i in range(9)],
           "9 set_color calls with index/r/g/b in order")
    expect(not any(c[0] == "raw_hid_send" for c in b.calls), "no reply on success")
    expect(b.r32(STATE + 4) == 5010, "stream stamps the time")

    print("stream errors")
    for pkt, code in [([0x24, 80, 7], 253), ([0x24, 86, 1], 253), ([0x24, 255, 255], 253),
                      ([0x24, 0, 10], 254), ([0x24, 70, 12], 254)]:
        b.hid(pkt)
        sends = [c for c in b.calls if c[0] == "raw_hid_send"]
        expect(not any(c[0] == "set_color" for c in b.calls), f"{pkt} writes no LED")
        expect(len(sends) == 1 and sends[0][1][4][:2] == bytes([0x24, code]), f"{pkt} -> error {code}")
    b.hid([0x24, 84, 2, 1, 2, 3, 4, 5, 6])
    expect([c[1][0] for c in b.calls if c[0] == "set_color"] == [84, 85], "last LEDs 84-85 accepted")
    b.hid([0x24, 0, 9] + [0] * 27, length=29)
    expect(b.calls and b.calls[-1][0] == "raw_hid_send" and b.calls[-1][1][4][:2] == bytes([0x24, 254]),
           "count that overruns the received length -> error 254")

    print("render while active skips the effect and flushes")
    b.now = 5020
    iter0 = b.r8(RGB + 0x0C)
    b.render(5)
    expect("render" not in b.names(), "original render not called")
    expect(b.r8(RGB + 3) == 2, "task state set to FLUSHING")
    expect(b.r8(RGB + 0x0C) == (iter0 + 1) & 0xFF, "iter incremented")
    expect(b.r8(STATE + 9) == 1, "rendering flag set")

    def fallback(label, setup, undo):
        b.now += 10
        b.hid([0x24, 0, 1, 9, 9, 9])           # keep the stream fresh
        b.w8(RGB + 1, 0x01)
        b.w8(RGB + 2, 0x05)
        b.render(5)
        expect("render" not in b.names(), f"{label}: active before")
        setup()
        b.render(5)
        expect(b.names() == ["set_color_all", "render"], f"{label}: clears frame then stock render")
        expect(b.call("set_color_all")[:3] == [0, 0, 0], f"{label}: clear colour is black")
        expect(b.call("render")[0] == 5, f"{label}: stock render gets the effect")
        expect(b.r8(RGB + 1) == 0xFF and b.r8(RGB + 2) == 0xFF, f"{label}: effect re-init forced")
        b.render(5)
        expect(b.names() == ["render"], f"{label}: stays on stock effect")
        undo()

    print("fallbacks")
    fallback("timeout", lambda: setattr(b, "now", b.now + 2000), lambda: None)
    fallback("wireless mode", lambda: b.w8(MODE, 1), lambda: b.w8(MODE, 3))
    fallback("2.4G mode", lambda: b.w8(MODE, 2), lambda: b.w8(MODE, 3))
    fallback("VBUS gone", lambda: b.w32(FLAGS, 0), lambda: b.w32(FLAGS, 1 << 9))
    fallback("USB suspended", lambda: b.w8(USBST, 4), lambda: b.w8(USBST, 3))
    fallback("RGB off", lambda: b.w8(RGB + 0x18, 5 << 2), lambda: b.w8(RGB + 0x18, (5 << 2) | 1))
    fallback("rgb suspend", lambda: b.w8(RGB, 1), lambda: b.w8(RGB, 0))
    fallback("mode off (0x26)", lambda: b.hid([0x26]), lambda: b.hid([0x25]))

    print("timeout edge")
    b.now += 10
    b.hid([0x24, 0, 0])
    t = b.now
    b.now = t + 1999
    b.render(5)
    expect("render" not in b.names(), "1999 ms after last packet: still active")
    b.now = t + 2000
    b.render(5)
    expect("render" in b.names(), "2000 ms after last packet: fallback")

    print("stream ignored in wireless mode, resumes on USB")
    b.w8(MODE, 1)
    b.now += 10
    b.hid([0x24, 0, 1, 1, 2, 3])
    expect(not any(c[0] == "set_color" for c in b.calls), "no LED writes in wireless mode")
    b.w8(MODE, 3)
    b.hid([0x24, 0, 1, 1, 2, 3])
    expect(any(c[0] == "set_color" for c in b.calls), "LED writes again on USB (mode still enabled)")
    b.render(5)
    expect("render" not in b.names(), "renders SignalRGB again")

    print("timer reset (e.g. after wake) cannot keep it active")
    b.now = 3
    b.render(5)
    expect("render" in b.names(), "now < last packet time -> treated as stale")

    print(f"\n{passed} checks passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
