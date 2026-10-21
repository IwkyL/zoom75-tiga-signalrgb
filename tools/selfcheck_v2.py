"""Independent static self-check of patch v2 (out_v2/patched_v2.hex).

usage: .venv/Scripts/python.exe -I tools/selfcheck_v2.py [--negative]
           [--original HEX] [--v1 HEX] [--v2 HEX]

Based on tools/selfcheck.py (v1, unchanged). Uses neither build_patch_v2.py nor the
toolchain. Checks:
  1. SHA-256 of the original and of the flashed v1; strict hex syntax, record
     checksums and layout of all three files
  2. vs the original: only the four hook BLs change; new bytes only from
     0x0801A828; image ends <= 0x0801AFFF; sectors written
  3. vs v1: identical except hooks B, C, D and the bytes from 0x0801AA08; the v1
     code 0x0801A828-0x0801AA07 and hook A byte-identical
  4. the 16 protected ranges of selfcheck.py, plus the radio path around the new
     hooks (VIA, dev_write, +DATA: handler, line handler, radio task, ...)
  5. hook sites: original BL targets; A -> v1 srgb_rx, B/C/D -> the v2 code
  6. recursive-descent disassembly of the v1 code (v1 allowlists) and of the v2
     code (v2 allowlists) from their entry points: every byte is code, a referenced
     literal, a jump table or nop padding; no forbidden instructions; every call or
     tail call goes to an allowlisted function; every store with a known address is
     allowlisted; stores through the received payload stay at offsets 0-31 (indexed
     ones are listed; tools/emutest_v2.py bounds them); every literal-pool word is
     on an explicit allowlist
--negative additionally mutates the v2 image in memory in 14 ways (vector table,
v1 code, +DATA: handler, literal to the flash controller, store target, forbidden
instruction, call target, past sector 53, vendor region, unpatched images, ...) and
requires every one to fail.
Exit status 0 only if every check passes.
"""
import hashlib
import struct
import sys
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB, Cs
from capstone.arm import (ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_LR, ARM_REG_PC, ARM_REG_R0,
                          ARM_REG_R1, ARM_REG_R2, ARM_REG_R3, ARM_REG_R12, ARM_REG_SP)

ROOT = Path(__file__).resolve().parent.parent
ORIG_HEX = ROOT / "original" / "WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex"
V1_HEX = ROOT / "out" / "patched.hex"
V2_HEX = ROOT / "out_v2" / "patched_v2.hex"
ORIG_SHA = "678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b"
V1_SHA = "c93031edfbe3807372b5014b689165278e532de6e4f563526b0f0c6f850e6a79"
BASE = 0x08000000
IMAGE_END = 0x0801A828          # end of the official image
V1_END = 0x0801AA08             # end of the v1 code
PATCH_LIMIT = 0x0801B000
SECTOR = 0x800

# site -> (original BL target, "v1" | "v2")
HOOKS = {0x08014D42: (0x0801240C, "v1"),     # A: USB HID task
         0x0801017C: (0x080102C4, "v2"),     # B: rgb_matrix_task
         0x080140E2: (0x0800F620, "v2"),     # C: +DATA: handler -> VIA
         0x0801413E: (0x08008050, "v2")}     # D: +DATA: handler -> reply line
V1_SRGB_RX, V1_SRGB_RENDER = 0x0801A828, 0x0801A910
V1_HOOK_TARGETS = {0x08014D42: V1_SRGB_RX, 0x0801017C: V1_SRGB_RENDER}

# ---- v1 code: allowlists exactly as selfcheck.py ----------------------------
V1_CALLS = {
    0x0800FFE8: "rgb_matrix_set_color",
    0x0800FFF8: "rgb_matrix_set_color_all",
    0x08009F64: "raw_hid_send",
    0x08013400: "timer_read32",
    0x080102C4: "rgb_task_render (original)",
}
V1_WRITES = {
    **{0x20007E00 + i: "v1 state" for i in range(12)},
    0x20000AC1: "rgb last_enable", 0x20000AC2: "rgb last_effect",
    0x20000AC3: "rgb task state", 0x20000ACC: "rgb effect iter",
}
V1_LITERALS = {
    0x20007E00: "v1 state", 0x53524742: "magic 'SRGB'", 0x20000A00: "base of rgb block 0x20000AC0",
    0x20000B00: "base of mode byte 0x20000B8C", 0x0800FFE9: "set_color", 0x0800FFF9: "set_color_all",
    0x08009F65: "raw_hid_send", 0x08013401: "timer_read32", 0x080102C5: "rgb_task_render",
}
# ---- v2 code (PATCH_V2_DESIGN.md section 4) ----------------------------------
V2_CALLS = {
    0x0800F620: "via_command (tail call, hook C pass-through)",
    0x08008050: "dev_write (tail call, hook D)",
    0x0800FFE8: "rgb_matrix_set_color",
    0x08013400: "timer_read32",
    0x0801A910: "v1 srgb_render (tail call, hook B hand-off)",
}
V2_WRITES = {
    **{0x20007E10 + i: "radio state" for i in range(16)},
    0x20007E09: "v1 rendering",
    0x20000AC3: "rgb task state", 0x20000ACC: "rgb effect iter",
}
V2_LITERALS = {
    0x20007E00: "base of v1 + radio state", 0x20007000: "base of v1 magic 0x20007E00",
    0x53524742: "magic 'SRGB' (compared)", 0x53524752: "magic 'SRGR'",
    0x20000A00: "base of rgb block 0x20000AC0", 0x20000B00: "base of mode byte 0x20000B8C",
    0x0800F621: "via_command", 0x08008051: "dev_write", 0x0800FFE9: "set_color",
    0x08013401: "timer_read32", 0x0801A911: "v1 srgb_render",
}
PAYLOAD_MAX = 32                # the received payload: in-place replies only

# Ranges that must be byte-identical to the original.
PROTECTED = {
    # the 16 of selfcheck.py (v1)
    "vector table": (0x08000000, 0x08000170),
    "__main / scatter-load entry": (0x08000170, 0x080001A0),
    "reset handler region": (0x080002E8, 0x08000400),
    "scatter-load decompressor": (0x08000EE8, 0x08000F80),
    "zero-init routine": (0x08005FB0, 0x08005FD0),
    "flash write path (0x08002168 page rule)": (0x08002100, 0x08002300),
    "settings writer 0x08008748": (0x08008700, 0x08008900),
    "model ID (DFU tool check) 0x08004400": (0x08004400, 0x08004408),
    "keyboard_bootmagic_init 0x08007D28": (0x08007D28, 0x08007F00),
    "bootloader_jump 0x08007CF4": (0x08007CF4, 0x08007D28),
    "QK_BOOT handler 0x0800E408": (0x0800E408, 0x0800E500),
    "init table": (0x0801A59C, 0x0801A6E0),
    "scatter table": (0x0801A6E4, 0x0801A710),
    "RW data image": (0x0801A704, 0x0801A828),
    "rgb_task_render 0x080102C4": (0x080102C4, 0x080104FE),
    "hook-A stub 0x0801240C": (0x0801240C, 0x08012410),
    # v2: the radio path around hooks C/D, and the other hook functions
    "VIA dispatcher 0x0800F620": (0x0800F620, 0x0800F7A4),
    "via_command_kb stub 0x08014DE4": (0x08014DE4, 0x08014DE8),
    "dev_write 0x08008050": (0x08008050, 0x0800807A),
    "channel sendmail 0x080081A8": (0x080081A8, 0x08008270),
    "radio task 0x08015088": (0x08015088, 0x080151FE),
    "radio line handler 0x08015214": (0x08015214, 0x080154B4),
    "+DATA: handler before hook C": (0x080140AC, 0x080140E2),
    "+DATA: handler between hooks C and D": (0x080140E6, 0x0801413E),
    "+DATA: handler after hook D": (0x08014142, 0x0801414C),
    "rgb_matrix_task before hook B": (0x0801009C, 0x0801017C),
    "rgb_matrix_task after hook B": (0x08010180, 0x080101CA),
    "USB HID task before hook A": (0x08014CDC, 0x08014D42),
    "USB HID task after hook A": (0x08014D46, 0x08014D6A),
    "LED driver set_color 0x080128D0": (0x080128D0, 0x08012930),
    "timer_read32 0x08013400": (0x08013400, 0x0801340C),
}
FORBIDDEN = {"svc", "bkpt", "cpsid", "cpsie", "msr", "mrs", "wfi", "wfe", "udf",
             "ldrex", "strex", "isb", "dsb", "dmb", "sev"}


class Checker:
    def __init__(self, quiet=False):
        self.failures = []
        self.quiet = quiet

    def __call__(self, ok, msg):
        if not self.quiet:
            print(("  ok    " if ok else "  FAIL  ") + msg)
        if not ok:
            self.failures.append(msg)
        return ok

    def note(self, text):
        if not self.quiet:
            print(text)


def parse_hex(path, check):
    """Strict Intel HEX parser: returns (bytes dict, start record, records)."""
    text = Path(path).read_bytes()
    check(text.endswith(b"\r\n") and b"\n" not in text.replace(b"\r\n", b""),
          f"{Path(path).name}: CRLF line endings throughout")
    mem, seg, start, records, eof, types = {}, 0, None, [], False, []
    for n, line in enumerate(text.decode("ascii").split("\r\n")[:-1], 1):
        if not line.startswith(":") or line[1:] != line[1:].upper():
            check(False, f"line {n}: not an upper-case record")
            continue
        raw = bytes.fromhex(line[1:])
        ln, addr, rtype, data = raw[0], (raw[1] << 8) | raw[2], raw[3], raw[4:-1]
        types.append((rtype, bytes(data) if rtype != 0 else None))
        if len(data) != ln or (sum(raw) & 0xFF):
            check(False, f"line {n}: bad length or checksum")
        if eof:
            check(False, f"line {n}: data after EOF record")
        if rtype == 0:
            a = seg + addr
            records.append((a, ln))
            for i, b in enumerate(data):
                if a + i in mem:
                    check(False, f"line {n}: overlapping data at 0x{a + i:08X}")
                mem[a + i] = b
        elif rtype == 4:
            seg = ((data[0] << 8) | data[1]) << 16
        elif rtype == 5:
            start = struct.unpack(">I", data)[0]
        elif rtype == 1:
            eof = True
        else:
            check(False, f"line {n}: unexpected record type {rtype}")
    check(eof, f"{Path(path).name}: has EOF record")
    return mem, start, records, [t for t in types if t[0] != 0]


def to_image(mem, check):
    lo, hi = min(mem), max(mem)
    check(len(mem) == hi - lo + 1, f"image 0x{lo:08X}-0x{hi:08X} has no gaps")
    return lo, bytes(mem.get(a, 0xFF) for a in range(lo, hi + 1))


class Analyzer:
    """Recursive-descent Thumb-2 disassembly with simple constant tracking.
    Register values are ints (constants) or ('arg0', k): the hook's first argument
    (the received payload for hook C) plus k."""

    def __init__(self, image, lo, hi, calls, writes, arg0_entries=()):
        self.img, self.lo, self.hi = image, lo, hi
        self.allowed_calls, self.allowed_writes = calls, writes
        self.arg0_entries = set(arg0_entries)
        self.md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        self.md.detail = True
        self.code, self.data = {}, {}          # addr -> size / label
        self.calls, self.stores, self.issues = [], [], []
        self.visits = {}

    def u8(self, a):
        return self.img[a - BASE]

    def u32(self, a):
        return struct.unpack_from("<I", self.img, a - BASE)[0]

    def insn(self, a):
        return next(self.md.disasm(self.img[a - BASE:a - BASE + 4], a), None)

    def run(self, entry):
        regs = {ARM_REG_R0: ("arg0", 0)} if entry in self.arg0_entries else {}
        work = [(entry, regs)]
        while work:
            addr, regs = work.pop()
            key = (addr, tuple(sorted(regs.items())))
            if self.visits.get(addr, 0) >= 8 or key in self.visits:
                continue
            self.visits[key] = 1
            self.visits[addr] = self.visits.get(addr, 0) + 1
            self.block(addr, dict(regs), work)

    def inside(self, a):
        return self.lo <= a < self.hi

    @staticmethod
    def args_of(regs):
        """Argument registers passed into an internal call (r0-r3)."""
        return {r: v for r, v in regs.items() if r in (ARM_REG_R0, ARM_REG_R1, ARM_REG_R2, ARM_REG_R3)}

    def block(self, addr, regs, work):
        prev = []
        while True:
            if not self.inside(addr):
                self.issues.append(f"control flow leaves the code at 0x{addr:08X}")
                return
            ins = self.insn(addr)
            if ins is None:
                self.issues.append(f"undecodable bytes at 0x{addr:08X}")
                return
            self.code[addr] = ins.size
            m, ops = ins.mnemonic, ins.operands
            nxt = addr + ins.size
            base_m = m.split(".")[0]
            if base_m in FORBIDDEN or base_m.rstrip("b") in FORBIDDEN:
                self.issues.append(f"forbidden instruction {m} at 0x{addr:08X}")
            if m.startswith("it"):
                self.issues.append(f"IT block at 0x{addr:08X} (not modelled)")

            # literal loads
            if m.startswith("ldr") and len(ops) == 2 and ops[1].type == ARM_OP_MEM \
                    and ops[1].mem.base == ARM_REG_PC:
                lit = ((addr + 4) & ~3) + ops[1].mem.disp
                if not self.inside(lit):
                    self.issues.append(f"literal outside the code at 0x{addr:08X}")
                else:
                    for i in range(4):
                        self.data[lit + i] = "literal"
                    regs[ops[0].reg] = self.u32(lit)
                prev.append(ins)
                addr = nxt
                continue

            # table branch
            if m == "tbb":
                n = self.table_len(prev)
                if n is None:
                    self.issues.append(f"tbb at 0x{addr:08X} without bound check")
                    return
                tab = addr + 4
                for i in range(n):
                    self.data[tab + i] = "tbb table"
                    work.append((tab + 2 * self.u8(tab + i), dict(regs)))
                if n % 2:
                    self.data[tab + n] = "tbb pad"
                return

            # branches
            if m in ("b", "b.w", "bl") or (m.startswith("b") and len(ops) == 1 and ops[0].type == ARM_OP_IMM
                                           and m not in ("bx", "blx")):
                tgt = ops[0].imm
                if m == "bl":
                    if self.inside(tgt):
                        work.append((tgt, self.args_of(regs)))
                        self.calls.append((addr, tgt, "internal"))
                    else:
                        self.ext_call(addr, tgt, "bl")
                    self.clobber(regs)
                    prev.append(ins)
                    addr = nxt
                    continue
                if not self.inside(tgt):
                    self.ext_call(addr, tgt, m)
                    return
                work.append((tgt, dict(regs)))
                if m in ("b", "b.w"):
                    return
                prev.append(ins)
                addr = nxt
                continue
            if m in ("cbz", "cbnz"):
                work.append((ops[1].imm, dict(regs)))
                prev.append(ins)
                addr = nxt
                continue
            if m in ("blx", "bx"):
                r = ops[0].reg
                if m == "bx" and ins.reg_name(r) == "lr":
                    return
                tgt = regs.get(r)
                if not isinstance(tgt, int):
                    self.issues.append(f"{m} through unknown register at 0x{addr:08X}")
                else:
                    if not tgt & 1:
                        self.issues.append(f"{m} to ARM-state address 0x{tgt:08X} at 0x{addr:08X}")
                    self.ext_call(addr, tgt & ~1, m)
                if m == "bx":
                    return
                self.clobber(regs)
                prev.append(ins)
                addr = nxt
                continue
            if m in ("pop", "ldm", "ldmia", "ldmia.w", "pop.w", "ldm.w") and any(
                    o.type == ARM_OP_REG and o.reg == ARM_REG_PC for o in ops):
                return

            # stores
            if m.startswith("str") or m.startswith("stm") or m.startswith("push"):
                self.store(ins, regs)

            self.track(ins, regs)
            prev.append(ins)
            addr = nxt

    def table_len(self, prev):
        # expect: cmp rN, #k ; bhi default ; tbb [pc, rN]
        if len(prev) >= 2 and prev[-1].mnemonic == "bhi" and prev[-2].mnemonic.startswith("cmp"):
            return prev[-2].operands[1].imm + 1
        return None

    def ext_call(self, addr, tgt, how):
        name = self.allowed_calls.get(tgt)
        self.calls.append((addr, tgt, name or "NOT ALLOWLISTED"))
        if name is None:
            self.issues.append(f"{how} to non-allowlisted 0x{tgt:08X} at 0x{addr:08X}")

    @staticmethod
    def clobber(regs):
        # AAPCS: a call may change r0-r3, r12 and lr; r4-r11 are preserved.
        for r in (ARM_REG_R0, ARM_REG_R1, ARM_REG_R2, ARM_REG_R3, ARM_REG_R12, ARM_REG_LR):
            regs.pop(r, None)

    @staticmethod
    def add(v, k):
        if isinstance(v, int):
            return (v + k) & 0xFFFFFFFF
        return (v[0], v[1] + k)

    def track(self, ins, regs):
        m, ops = ins.mnemonic, ins.operands
        if not ops or ops[0].type != ARM_OP_REG:
            return
        dst = ops[0].reg
        # Stores and compares write no register, except a store's base on write-back.
        if m.startswith(("str", "stm", "push")):
            if ins.writeback:
                mem = next((o.mem for o in ops if o.type == ARM_OP_MEM), None)
                regs.pop(mem.base if mem is not None else dst, None)
            return
        if m.startswith(("cmp", "cmn", "tst", "teq")):
            return
        if m.startswith("ldm") or m.startswith("pop"):
            for o in ops:
                if o.type == ARM_OP_REG:
                    regs.pop(o.reg, None)
            return
        if m in ("mov.w", "movw", "mov", "movs") and len(ops) == 2 and ops[1].type == ARM_OP_IMM:
            regs[dst] = ops[1].imm & 0xFFFFFFFF
        elif m in ("add.w", "add", "adds", "addw") and len(ops) == 3 and ops[1].type == ARM_OP_REG \
                and ops[2].type == ARM_OP_IMM and ops[1].reg in regs:
            regs[dst] = self.add(regs[ops[1].reg], ops[2].imm)
        elif m in ("adds", "add") and len(ops) == 2 and ops[1].type == ARM_OP_IMM and dst in regs:
            regs[dst] = self.add(regs[dst], ops[1].imm)          # adds r4, #3
        elif m in ("mov",) and len(ops) == 2 and ops[1].type == ARM_OP_REG and ops[1].reg in regs:
            regs[dst] = regs[ops[1].reg]
        else:
            regs.pop(dst, None)

    def store(self, ins, regs):
        m, ops = ins.mnemonic, ins.operands
        mem = next((o.mem for o in ops if o.type == ARM_OP_MEM), None)
        if m.startswith("push") or m.startswith("stmdb") or (mem is not None and mem.base == ARM_REG_SP):
            self.stores.append((ins.address, m, None, "stack"))
            return
        if mem is None:
            self.stores.append((ins.address, m, None, "unknown"))
            self.issues.append(f"store without memory operand at 0x{ins.address:08X}")
            return
        width = 1 if m.startswith("strb") else 2 if m.startswith("strh") else 4
        base = regs.get(mem.base)
        if isinstance(base, int) and mem.index == 0:
            ea = (base + mem.disp) & 0xFFFFFFFF
            ok = all(ea + i in self.allowed_writes for i in range(width))
            self.stores.append((ins.address, m, ea, self.allowed_writes.get(ea, "NOT ALLOWLISTED")))
            if not ok:
                self.issues.append(f"store to non-allowlisted 0x{ea:08X} at 0x{ins.address:08X}")
        elif isinstance(base, tuple) and mem.index == 0:
            off = base[1] + mem.disp
            ok = 0 <= off and off + width <= PAYLOAD_MAX
            self.stores.append((ins.address, m, None, f"payload[{off}]"))
            if not ok:
                self.issues.append(f"store to payload[{off}] outside 0..{PAYLOAD_MAX - 1} at 0x{ins.address:08X}")
        elif isinstance(base, tuple):
            self.stores.append((ins.address, m, None,
                                f"payload[{base[1]} + {ins.reg_name(mem.index)}] (indexed; bounded by emutest_v2)"))
        else:
            self.stores.append((ins.address, m, None, f"UNKNOWN BASE {ins.reg_name(mem.base)}"))
            self.issues.append(f"store through an unknown base register at 0x{ins.address:08X}")


def analyse(label, check, img, lo, hi, entries, calls, writes, literals, arg0_entries=()):
    an = Analyzer(img, lo, hi, calls, writes, arg0_entries)
    for e in entries:
        an.run(e)
    covered = set()
    for a, n in an.code.items():
        covered.update(range(a, a + n))
    covered.update(an.data)
    pad = [a for a in range(lo, hi) if a not in covered]
    # The linker's ALIGN(4) at the end of .text fills with zero bytes: allowed only
    # as < 4 bytes after the last covered byte, at the very end of the region.
    tail = [a for a in pad if a > max(covered, default=lo)]
    tail_ok = len(tail) < 4 and tail == list(range(hi - len(tail), hi)) \
        and all(img[a - BASE] == 0 for a in tail)
    bad_pad = [a for a in pad if not (tail_ok and a in tail)
               and img[(a & ~1) - BASE:(a & ~1) - BASE + 2] != b"\x00\xbf"]
    check(not bad_pad, f"{label}: every byte is code, literal, table or nop padding "
                       f"({len(covered)} covered, {len(pad) - len(tail) * tail_ok} nop padding"
                       + (f", {len(tail)} zero bytes of end alignment" if tail_ok and tail else "") + ")")
    for a in bad_pad[:8]:
        check.note(f"        unexplained byte at 0x{a:08X}")
    overlap = set(an.data) & {a + i for a, n in an.code.items() for i in range(n)}
    check(not overlap, f"{label}: no byte is both code and data")
    for msg in an.issues:
        check(False, f"{label}: {msg}")
    check(not an.issues, f"{label}: no forbidden instructions, unknown jumps or stray stores")
    check.note("        external calls / tail calls:")
    for a, t, name in sorted(set(an.calls)):
        if name != "internal":
            check.note(f"          0x{a:08X} -> 0x{t:08X} {name}")
    check.note("        stores:")
    for a, m, ea, what in sorted(set(an.stores)):
        where = f"0x{ea:08X}" if ea is not None else "-"
        check.note(f"          0x{a:08X} {m:<8} {where:<11} {what}")
    lits = sorted({an.u32(a) for a, k in an.data.items() if k == "literal" and (a - lo) % 4 == 0})
    check.note("        literal pool values: " + ", ".join(f"0x{v:08X}" for v in lits))
    bad_lit = [v for v in lits if v not in literals]
    for v in bad_lit:
        check.note(f"        literal 0x{v:08X} is not on the allowlist")
    check(not bad_lit, f"{label}: every literal-pool word is on the allowlist ({len(lits)} values)")
    return an


def check_images(check, oimg, v1img, v2img, v2recs=None, starts=None):
    """All image-level checks. oimg/v1img/v2img are flat images from 0x08000000."""
    v2end = BASE + len(v2img)
    check(len(oimg) == IMAGE_END - BASE, "original image ends at 0x0801A827")
    check(len(v1img) == V1_END - BASE, "v1 image ends at 0x0801AA07")
    check(v2end <= PATCH_LIMIT, f"v2 image ends at 0x{v2end - 1:08X} (limit 0x{PATCH_LIMIT - 1:08X})")
    check(v2end > V1_END, "v2 image extends past the v1 code")
    if v2recs is not None:
        check(all(a % 16 == 0 and n <= 16 for a, n in v2recs), "v2 records are <=16 B and 16-byte aligned")
    if starts is not None:
        check(starts[0] == starts[1] == starts[2], f"start address record unchanged (0x{starts[2]:08X})")

    check.note("[2] byte diff vs the original")
    n = min(len(oimg), len(v2img))
    diff = [BASE + i for i in range(n) if oimg[i] != v2img[i]]
    allowed = {a + i for a in HOOKS for i in range(4)}
    check(set(diff) <= allowed and len(v2img) >= len(oimg),
          f"only hook-site bytes changed inside the original image ({len(diff)} bytes)")
    for a in sorted(set(diff) - allowed)[:8]:
        check.note(f"        unexpected change at 0x{a:08X}")
    sectors = sorted({(a - BASE) // SECTOR for a in diff} | {(a - BASE) // SECTOR for a in range(IMAGE_END, v2end)})
    check(set(sectors) <= set(range(54)), f"sectors written: {sectors} (all within official 0-53)")
    check(sectors == [32, 40, 41, 53], "sectors written are exactly 32, 40, 41, 53 (PATCH_V2_DESIGN.md 4)")

    check.note("[3] byte diff vs v1 (out/patched.hex)")
    n = min(len(v1img), len(v2img))
    diff1 = [BASE + i for i in range(n) if v1img[i] != v2img[i]]
    allowed1 = {a + i for a, (_, w) in HOOKS.items() if w == "v2" for i in range(4)}
    check(set(diff1) <= allowed1, f"identical to v1 except hooks B, C, D ({len(diff1)} bytes differ)")
    for a in sorted(set(diff1) - allowed1)[:8]:
        check.note(f"        unexpected change at 0x{a:08X}")
    check(v2img[IMAGE_END - BASE:V1_END - BASE] == v1img[IMAGE_END - BASE:V1_END - BASE],
          "v1 code 0x0801A828-0x0801AA07 byte-identical")
    check(v2img[0x08014D42 - BASE:0x08014D46 - BASE] == v1img[0x08014D42 - BASE:0x08014D46 - BASE],
          "hook A byte-identical to v1")

    check.note("[4] protected ranges (identical to the original)")
    for name, (s, e) in PROTECTED.items():
        check(oimg[s - BASE:e - BASE] == v2img[s - BASE:e - BASE], f"identical: {name} 0x{s:08X}-0x{e - 1:08X}")

    check.note("[5] hook sites")
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    entries = {"v1": [], "v2": []}
    by_site = {}
    for site, (old_tgt, which) in HOOKS.items():
        o = next(md.disasm(oimg[site - BASE:site - BASE + 4], site), None)
        p = next(md.disasm(v2img[site - BASE:site - BASE + 4], site), None)
        if o is None or p is None or o.mnemonic != "bl" or p.mnemonic != "bl":
            check(False, f"0x{site:08X} decodes to a bl in both images")
            continue
        ot, pt = int(o.op_str.lstrip("#"), 0), int(p.op_str.lstrip("#"), 0)
        check.note(f"        0x{site:08X}: {o.mnemonic} 0x{ot:08X}  ->  {p.mnemonic} 0x{pt:08X}")
        check(ot == old_tgt, f"original 0x{site:08X} is bl 0x{old_tgt:08X}")
        if which == "v1":
            check(p.size == 4 and pt == V1_SRGB_RX, f"0x{site:08X} is a 4-byte bl to v1 srgb_rx")
        else:
            check(p.size == 4 and V1_END <= pt < v2end, f"0x{site:08X} is a 4-byte bl into the v2 code")
        entries[which].append(pt)
        by_site[site] = pt
    check(len(set(entries["v2"])) == 3, "hooks B, C, D have three different targets")
    v1o = {s: next(md.disasm(v1img[s - BASE:s - BASE + 4], s)) for s in V1_HOOK_TARGETS}
    check(all(int(v1o[s].op_str.lstrip("#"), 0) == t for s, t in V1_HOOK_TARGETS.items()),
          "v1 image: hook A -> 0x0801A828, hook B -> 0x0801A910 (the v1 entry points)")

    check.note("[6] v1 code 0x0801A828-0x0801AA07 (selfcheck.py allowlists)")
    analyse("v1 code", check, v2img, IMAGE_END, V1_END, [V1_SRGB_RX, V1_SRGB_RENDER],
            V1_CALLS, V1_WRITES, V1_LITERALS, arg0_entries=[V1_SRGB_RX])

    check.note(f"[7] v2 code 0x{V1_END:08X}-0x{v2end - 1:08X} (PATCH_V2_DESIGN.md allowlists)")
    if len(entries["v2"]) == 3 and all(V1_END <= t < v2end for t in entries["v2"]):
        # hook C's r0 is the received payload (adds r0, r4, #2 at 0x080140E0)
        analyse("v2 code", check, v2img, V1_END, v2end, entries["v2"], V2_CALLS, V2_WRITES, V2_LITERALS,
                arg0_entries=[by_site[0x080140E2]])
    else:
        check(False, "v2 code: entry points known")


def run_files(paths):
    check = Checker()
    check.note("[1] files")
    for path, want, name in ((paths[0], ORIG_SHA, "original"), (paths[1], V1_SHA, "v1 (flashed)")):
        check(hashlib.sha256(Path(path).read_bytes()).hexdigest() == want, f"{name} SHA-256 is {want[:8]}...")
    check.note(f"        v2: {Path(paths[2]).name} SHA-256 {hashlib.sha256(Path(paths[2]).read_bytes()).hexdigest()}")
    om, ostart, _, otypes = parse_hex(paths[0], check)
    v1m, v1start, _, v1types = parse_hex(paths[1], check)
    v2m, v2start, v2recs, v2types = parse_hex(paths[2], check)
    check(otypes == v2types, "v2 has the original's non-data records (0x04 segments, start, EOF), in order")
    imgs = []
    for mem in (om, v1m, v2m):
        lo, img = to_image(mem, check)
        check(lo == BASE, "image starts at 0x08000000")
        imgs.append(img)
    check_images(check, *imgs, v2recs=v2recs, starts=(ostart, v1start, v2start))
    return check, imgs


def find_word(img, lo, hi, value):
    for a in range(lo, hi - 3, 4):
        if struct.unpack_from("<I", img, a - BASE)[0] == value:
            return a
    raise LookupError(f"0x{value:08X} not found")


def negative_tests(oimg, v1img, v2img):
    print("\n[negative] each mutation of the v2 image must fail")
    end = BASE + len(v2img)

    def put(img, addr, data):
        b = bytearray(img)
        if addr - BASE + len(data) > len(b):
            b += b"\xff" * (addr - BASE + len(data) - len(b))
        b[addr - BASE:addr - BASE + len(data)] = data
        return bytes(b)

    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    hook_d = int(next(md.disasm(v2img[0x0801413E - BASE:0x08014142 - BASE], 0x0801413E)).op_str.lstrip("#"), 0)
    send = None
    for a in range(hook_d, end - 1, 2):     # srgb_radio_send: the 'movs r0, #0' before 'strb r0'
        if v2img[a - BASE:a - BASE + 4] == bytes.fromhex("0020a076"):
            send = a
            break
    cases = [
        ("vector table byte flipped", put(v2img, 0x08000004, bytes([v2img[4] ^ 1]))),
        ("v1 code byte changed", put(v2img, 0x0801A900, bytes([v2img[0x1A900] ^ 0x10]))),
        ("+DATA: handler changed outside the hooks", put(v2img, 0x080140E6, b"\x00\xbf")),
        ("literal -> flash controller 0x40022000",
         put(v2img, find_word(v2img, V1_END, end, 0x08008051), struct.pack("<I", 0x40022000))),
        ("literal -> settings writer 0x08008749 (call target)",
         put(v2img, find_word(v2img, V1_END, end, 0x08013401), struct.pack("<I", 0x08008749))),
        ("store base in srgb_radio_send -> 0x20000B80 (mode byte area)",
         put(v2img, find_word(v2img, hook_d & ~3, end, 0x20007E00), struct.pack("<I", 0x20000B80))),
        ("forbidden instruction (cpsid i)", put(v2img, send, bytes.fromhex("72b6")) if send else None),
        ("bytes past sector 53 (0x0801B000)", put(v2img, 0x0801B000, b"\x00\xbf")),
        ("byte in the vendor region 0x0801E000", put(v2img, 0x0801E000, b"\x00")),
        ("hook C left pointing at VIA", put(v2img, 0x080140E2, bytes.fromhex("fbf79dfa"))),
        ("hook A redirected to v2", put(v2img, 0x08014D42, v2img[0x080140E2 - BASE:0x080140E6 - BASE])),
        ("unpatched original as v2", oimg),
        ("v1 as v2", v1img),
        ("unexplained bytes appended", put(v2img, end, b"\x12\x34\x56\x78")),
    ]
    ok = True
    for name, img in cases:
        if img is None:
            print(f"  FAIL  {name}: mutation site not found")
            ok = False
            continue
        c = Checker(quiet=True)
        try:
            check_images(c, oimg, v1img, img)
        except Exception as e:             # a crash also counts as "caught", but say so
            c.failures.append(f"exception {type(e).__name__}: {e}")
        caught = bool(c.failures)
        ok &= caught
        print(f"  {'ok  ' if caught else 'FAIL'}  {name}: "
              + (f"caught ({c.failures[0][:90]})" if caught else "NOT caught"))
    c = Checker(quiet=True)
    check_images(c, oimg, v1img, v2img)
    print(f"  {'ok  ' if not c.failures else 'FAIL'}  unmodified v2 image passes the same checks")
    return ok and not c.failures


def main():
    args = sys.argv[1:]
    neg = "--negative" in args
    paths = [ORIG_HEX, V1_HEX, V2_HEX]
    for i, flag in enumerate(("--original", "--v1", "--v2")):
        if flag in args:
            paths[i] = Path(args[args.index(flag) + 1])
    check, imgs = run_files(paths)
    neg_ok = negative_tests(*imgs) if neg else True
    print()
    if check.failures or not neg_ok:
        print(f"SELF-CHECK V2 FAILED ({len(check.failures)} problem(s)"
              + ("" if neg_ok else ", negative tests not all caught") + ")")
        sys.exit(1)
    print("SELF-CHECK V2 PASSED" + (" (including negative tests)" if neg else ""))


if __name__ == "__main__":
    main()
