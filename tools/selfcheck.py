"""Independent static self-check of out/patched.hex against the original v1.1 hex.

usage: .venv/Scripts/python.exe -I tools/selfcheck.py [original.hex] [patched.hex]

Does not use build_patch.py or the toolchain. Checks:
  1. original SHA-256; hex syntax, record checksums and layout of both files
  2. byte diff: only the two hook BLs change; new bytes only in the free tail
     0x0801A828-0x0801AFFF; boot-path and other protected ranges identical
  3. hook sites decode to BLs into the patch
  4. recursive-descent disassembly of the patch from both entry points:
     every byte is reachable code, a referenced literal / jump table, or padding;
     no forbidden instructions; every external call/jump goes to an allowlisted
     firmware function; every RAM store with a known address is allowlisted
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
ORIG_SHA = "678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b"
BASE = 0x08000000
IMAGE_END = 0x0801A828
PATCH_LIMIT = 0x0801B000
SECTOR = 0x800

HOOKS = {0x08014D42: 0x0801240C, 0x0801017C: 0x080102C4}   # site -> original BL target

# Firmware functions the patch may call or tail-call (Thumb addresses).
FW_CALLS = {
    0x0800FFE8: "rgb_matrix_set_color",
    0x0800FFF8: "rgb_matrix_set_color_all",
    0x08009F64: "raw_hid_send",
    0x08013400: "timer_read32",
    0x080102C4: "rgb_task_render (original)",
}
# RAM bytes the patch may write at a known address.
RAM_WRITES = {
    **{0x20007E00 + i: "patch state" for i in range(12)},
    0x20000AC1: "rgb last_enable", 0x20000AC2: "rgb last_effect",
    0x20000AC3: "rgb task state", 0x20000ACC: "rgb effect iter",
}
# Ranges that must be byte-identical (boot path and other sensitive code/data).
PROTECTED = {
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
}
# Literal-pool words that are plain values, not addresses.
VALUE_CONSTANTS = {0x53524742: "state magic 'SRGB' (compared, never dereferenced)"}
FORBIDDEN = {"svc", "bkpt", "cpsid", "cpsie", "msr", "mrs", "wfi", "wfe", "udf",
             "ldrex", "strex", "isb", "dsb", "dmb", "sev"}

failures = []


def check(ok, msg):
    print(("  ok    " if ok else "  FAIL  ") + msg)
    if not ok:
        failures.append(msg)


def parse_hex(path):
    """Strict Intel HEX parser: returns (bytes dict, start record, records)."""
    text = Path(path).read_bytes()
    check(text.endswith(b"\r\n") and b"\n" not in text.replace(b"\r\n", b""),
          f"{Path(path).name}: CRLF line endings throughout")
    mem, seg, start, records, eof = {}, 0, None, [], False
    for n, line in enumerate(text.decode("ascii").split("\r\n")[:-1], 1):
        if not line.startswith(":") or line[1:] != line[1:].upper():
            check(False, f"line {n}: not an upper-case record")
            continue
        raw = bytes.fromhex(line[1:])
        ln, addr, rtype, data = raw[0], (raw[1] << 8) | raw[2], raw[3], raw[4:-1]
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
    return mem, start, records


def to_image(mem):
    lo, hi = min(mem), max(mem)
    if len(mem) != hi - lo + 1:
        check(False, "image has gaps")
    return lo, bytes(mem[a] for a in range(lo, hi + 1))


class Analyzer:
    """Recursive-descent Thumb-2 disassembly with simple constant tracking."""

    def __init__(self, image, lo, hi):
        self.img, self.lo, self.hi = image, lo, hi
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
        work = [(entry, {})]
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

    def block(self, addr, regs, work):
        prev = []
        while True:
            if not self.inside(addr):
                self.issues.append(f"control flow leaves the patch at 0x{addr:08X}")
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
                    self.issues.append(f"literal outside patch at 0x{addr:08X}")
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
                    work.append((tab + 2 * self.u8(tab + i), {}))
                if n % 2:
                    self.data[tab + n] = "tbb pad"
                return

            # branches
            if m in ("b", "b.w", "bl") or (m.startswith("b") and len(ops) == 1 and ops[0].type == ARM_OP_IMM
                                           and m not in ("bx", "blx")):
                tgt = ops[0].imm
                if m == "bl":
                    if self.inside(tgt):
                        work.append((tgt, {}))
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
                if tgt is None:
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

            # constant tracking (very small subset)
            self.track(ins, regs)
            prev.append(ins)
            addr = nxt

    def table_len(self, prev):
        # expect: cmp rN, #k ; bhi default ; tbb [pc, rN]
        if len(prev) >= 2 and prev[-1].mnemonic == "bhi" and prev[-2].mnemonic.startswith("cmp"):
            return prev[-2].operands[1].imm + 1
        return None

    def ext_call(self, addr, tgt, how):
        name = FW_CALLS.get(tgt)
        self.calls.append((addr, tgt, name or "NOT ALLOWLISTED"))
        if name is None:
            self.issues.append(f"{how} to non-allowlisted 0x{tgt:08X} at 0x{addr:08X}")

    @staticmethod
    def clobber(regs):
        # AAPCS: a call may change r0-r3, r12 and lr; r4-r11 are preserved.
        for r in (ARM_REG_R0, ARM_REG_R1, ARM_REG_R2, ARM_REG_R3, ARM_REG_R12, ARM_REG_LR):
            regs.pop(r, None)

    def track(self, ins, regs):
        m, ops = ins.mnemonic, ins.operands
        if not ops or ops[0].type != ARM_OP_REG:
            return
        dst = ops[0].reg
        if m in ("mov.w", "movw", "mov", "movs") and len(ops) == 2 and ops[1].type == ARM_OP_IMM:
            regs[dst] = ops[1].imm & 0xFFFFFFFF
        elif m in ("add.w", "add", "adds", "addw") and len(ops) == 3 and ops[1].type == ARM_OP_REG \
                and ops[2].type == ARM_OP_IMM and ops[1].reg in regs:
            regs[dst] = (regs[ops[1].reg] + ops[2].imm) & 0xFFFFFFFF
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
        if mem.base in regs and mem.index == 0:
            ea = (regs[mem.base] + mem.disp) & 0xFFFFFFFF
            ok = all(ea + i in RAM_WRITES for i in range(width))
            self.stores.append((ins.address, m, ea, RAM_WRITES.get(ea, "NOT ALLOWLISTED")))
            if not ok:
                self.issues.append(f"store to non-allowlisted 0x{ea:08X} at 0x{ins.address:08X}")
        else:
            self.stores.append((ins.address, m, None,
                                f"via {ins.reg_name(mem.base)} (argument pointer)"))


def main():
    orig_path = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        ROOT / "original" / "WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex"
    pat_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "out" / "patched.hex"

    print("[1] files")
    check(hashlib.sha256(orig_path.read_bytes()).hexdigest() == ORIG_SHA, "original SHA-256 is TIGA v1.1")
    om, ostart, orecs = parse_hex(orig_path)
    pm, pstart, precs = parse_hex(pat_path)
    check(ostart == pstart, f"start address record unchanged (0x{pstart:08X})")
    check(all(a % 16 == 0 and n <= 16 for a, n in precs), "patched records are <=16 B and 16-byte aligned")
    olo, oimg = to_image(om)
    plo, pimg = to_image(pm)
    check(olo == plo == BASE, "both images start at 0x08000000")
    pend = plo + len(pimg)
    check(len(oimg) == IMAGE_END - BASE, "original image ends at 0x0801A827")
    check(pend <= PATCH_LIMIT, f"patched image ends at 0x{pend - 1:08X} (limit 0x{PATCH_LIMIT - 1:08X})")

    print("[2] byte diff")
    diff = [BASE + i for i in range(len(oimg)) if oimg[i] != pimg[i]]
    allowed = {a + i for a in HOOKS for i in range(4)}
    check(set(diff) <= allowed, f"only hook-site bytes changed inside the original image ({len(diff)} bytes)")
    for a in sorted(set(diff) - allowed):
        print(f"        unexpected change at 0x{a:08X}")
    sectors = sorted({(a - BASE) // SECTOR for a in diff} | {(a - BASE) // SECTOR for a in range(IMAGE_END, pend)})
    check(set(sectors) <= set(range(54)), f"sectors written: {sectors} (all within official 0-53)")
    for name, (s, e) in PROTECTED.items():
        check(oimg[s - BASE:e - BASE] == pimg[s - BASE:e - BASE], f"identical: {name} 0x{s:08X}-0x{e - 1:08X}")

    print("[3] hook sites")
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    entries = []
    for site, old_tgt in HOOKS.items():
        o = next(md.disasm(oimg[site - BASE:site - BASE + 4], site))
        p = next(md.disasm(pimg[site - BASE:site - BASE + 4], site))
        ot, pt = int(o.op_str.lstrip("#"), 0), int(p.op_str.lstrip("#"), 0)
        print(f"        0x{site:08X}: {o.mnemonic} 0x{ot:08X}  ->  {p.mnemonic} 0x{pt:08X}")
        check(o.mnemonic == "bl" and ot == old_tgt, f"original 0x{site:08X} is bl 0x{old_tgt:08X}")
        check(p.mnemonic == "bl" and p.size == 4 and IMAGE_END <= pt < pend,
              f"patched 0x{site:08X} is a 4-byte bl into the patch")
        entries.append(pt)

    print("[4] patch code")
    an = Analyzer(pimg, IMAGE_END, pend)
    for e in entries:
        an.run(e)
    covered = set()
    for a, n in an.code.items():
        covered.update(range(a, a + n))
    covered.update(an.data)
    pad = []
    for a in range(IMAGE_END, pend):
        if a in covered:
            continue
        pad.append(a)
    # padding allowed: 'nop' (bf00) halfwords only
    bad_pad = [a for a in pad if not (pimg[(a & ~1) - BASE:(a & ~1) - BASE + 2] == b"\x00\xbf")]
    check(not bad_pad, f"every patch byte is code, literal, table or nop padding "
                       f"({len(covered)} covered, {len(pad)} padding)")
    for a in bad_pad:
        print(f"        unexplained byte at 0x{a:08X}")
    overlap = set(an.data) & {a + i for a, n in an.code.items() for i in range(n)}
    check(not overlap, "no byte is both code and data")
    for msg in an.issues:
        check(False, msg)
    check(not an.issues, "no forbidden instructions, unknown jumps or stray stores")
    print("        external calls:")
    for a, t, name in sorted(set(an.calls)):
        if name != "internal":
            print(f"          0x{a:08X} -> 0x{t:08X} {name}")
    print("        stores:")
    for a, m, ea, what in sorted(set(an.stores)):
        where = f"0x{ea:08X}" if ea is not None else "-"
        print(f"          0x{a:08X} {m:<8} {where:<11} {what}")
    lits = sorted({an.u32(a) for a, k in an.data.items() if k == "literal" and (a - IMAGE_END) % 4 == 0})
    print("        literal pool values:", ", ".join(f"0x{v:08X}" for v in lits))
    bad_lit = [v for v in lits if v not in VALUE_CONSTANTS and (
               0x40000000 <= v < 0x60000000 or v >= 0xE0000000
               or (BASE <= v < 0x08040000 and (v & ~1) not in FW_CALLS))]
    for v in bad_lit:
        print(f"        suspicious literal 0x{v:08X}")
    check(not bad_lit, "no literal points at peripherals, system space or non-allowlisted flash")

    print()
    if failures:
        print(f"SELF-CHECK FAILED ({len(failures)} problem(s))")
        sys.exit(1)
    print("SELF-CHECK PASSED")


if __name__ == "__main__":
    main()
