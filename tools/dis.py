"""Disassemble Thumb-2 code from a flat image with capstone, resolving PC-relative literal loads.

usage: python -I tools/dis.py <image.bin> <addr> [count=40] [base=0x08000000]
"""
import struct
import sys

from capstone import CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB, Cs

data = open(sys.argv[1], "rb").read()
addr = int(sys.argv[2], 0) & ~1
count = int(sys.argv[3], 0) if len(sys.argv) > 3 else 40
base = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x08000000

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
md.detail = True


def u32(a):
    o = a - base
    return struct.unpack_from("<I", data, o)[0] if 0 <= o <= len(data) - 4 else None


n = 0
for ins in md.disasm(data[addr - base:], addr):
    note = ""
    # ldr rX, [pc, #imm]  -> show the literal
    if ins.mnemonic.startswith("ldr") and "[pc" in ins.op_str:
        try:
            imm = ins.operands[1].mem.disp
            lit = ((ins.address + 4) & ~3) + imm
            v = u32(lit)
            if v is not None:
                note = f"   ; [0x{lit:08X}] = 0x{v:08X}"
        except Exception:
            pass
    print(f"0x{ins.address:08X}  {ins.bytes.hex():<10} {ins.mnemonic:<8} {ins.op_str}{note}")
    n += 1
    if n >= count:
        break
