"""Build the SignalRGB patch for the Zoom75 TIGA stock firmware v1.1.

usage: .venv/Scripts/python.exe -I build_patch.py [original.hex] [--out out]

Steps (each one refuses to continue on a mismatch):
  1. Check the input hex's SHA-256 against the one known v1.1 build.
  2. Check the two hook sites and the free tail still hold the expected bytes.
  3. Compile patch/srgb.c with the pinned Arm GNU Toolchain (15.2.Rel1) and link
     it at 0x0801A828 with patch/srgb.ld.
  4. Replace the two BL instructions so they call srgb_rx / srgb_render.
  5. Write <out>/patched.hex in the same record layout as the original
     (CRLF, upper-case, 16-byte records on 16-byte boundaries, same start record)
     and <out>/patch_report.md with hashes, the byte diff and its disassembly.

Run tools/selfcheck.py afterwards for the independent static check.
"""
import argparse
import hashlib
import io
import struct
import subprocess
import sys
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB, Cs
from intelhex import IntelHex

ROOT = Path(__file__).resolve().parent
TOOLCHAIN = ROOT / ".toolchain" / "arm-gnu-15.2" / "bin"
GCC_VERSION = "arm-none-eabi-gcc.exe (Arm GNU Toolchain 15.2.Rel1 (Build arm-15.86)) 15.2.1 20251203"

ORIGINAL_SHA256 = "678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b"
FLASH_BASE = 0x08000000
IMAGE_END = 0x0801A828          # first byte after the official image
SECTOR_SIZE = 0x800
PATCH_LIMIT = 0x0801B000        # end of sector 53 (FEASIBILITY.md condition 1)

# Hook sites: (address, original bytes, original target, patch symbol)
HOOKS = [
    (0x08014D42, bytes.fromhex("fdf763fb"), 0x0801240C, "srgb_rx"),      # USB HID task
    (0x0801017C, bytes.fromhex("00f0a2f8"), 0x080102C4, "srgb_render"),  # rgb_matrix_task
]

CFLAGS = [
    "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-Os", "-std=c11",
    "-ffreestanding", "-fno-builtin", "-fno-common",
    "-ffunction-sections", "-fdata-sections",
    "-fno-tree-loop-distribute-patterns",
    "-fno-asynchronous-unwind-tables", "-fno-unwind-tables",
    "-Wall", "-Wextra", "-Werror",
    "-nostdlib", "-nostartfiles",
]


def die(msg):
    sys.exit(f"build_patch: ERROR: {msg}")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def tool(name):
    return str(TOOLCHAIN / f"arm-none-eabi-{name}.exe")


def run(args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode:
        die(f"{Path(args[0]).name} failed:\n{r.stdout}{r.stderr}")
    return r.stdout


def encode_bl(src, dst):
    """Thumb-2 BL encoding T1 for a call from src to dst."""
    off = dst - (src + 4)
    if off & 1 or not -(1 << 24) <= off < (1 << 24):
        die(f"BL out of range: 0x{src:08X} -> 0x{dst:08X}")
    s = (off >> 24) & 1
    i1 = (off >> 23) & 1
    i2 = (off >> 22) & 1
    imm10 = (off >> 12) & 0x3FF
    imm11 = (off >> 1) & 0x7FF
    j1 = (~i1 ^ s) & 1
    j2 = (~i2 ^ s) & 1
    hw1 = 0xF000 | (s << 10) | imm10
    hw2 = 0xD000 | (j1 << 13) | (j2 << 11) | imm11
    return struct.pack("<HH", hw1, hw2)


def decode_bl(code, addr):
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    ins = next(md.disasm(code, addr), None)
    if ins is None or ins.mnemonic != "bl" or ins.size != 4:
        return None
    return int(ins.op_str.lstrip("#"), 0)


def compile_patch(build):
    ver = run([tool("gcc"), "--version"]).splitlines()[0].strip()
    if ver != GCC_VERSION:
        die(f"unexpected compiler (reproducibility):\n  got  {ver}\n  want {GCC_VERSION}")
    elf = build / "srgb.elf"
    run([tool("gcc"), *CFLAGS, "-T", str(ROOT / "patch" / "srgb.ld"), "-Wl,--gc-sections",
         "-Wl,--build-id=none", f"-Wl,-Map={build / 'srgb.map'}", "-o", str(elf),
         str(ROOT / "patch" / "srgb.c")])
    blob_path = build / "srgb.bin"
    run([tool("objcopy"), "-O", "binary", "-j", ".text", str(elf), str(blob_path)])
    syms = {}
    for line in run([tool("nm"), "-n", str(elf)]).splitlines():
        a, t, name = line.split()
        syms[name] = (int(a, 16), t)
    (build / "srgb.lst").write_text(run([tool("objdump"), "-d", str(elf)]), encoding="utf-8")
    sections = run([tool("readelf"), "-S", "-W", str(elf)])
    return ver, blob_path.read_bytes(), syms, sections


def text_section_addr(sections):
    for line in sections.splitlines():
        if " .text " in line:
            parts = line.split()
            i = parts.index(".text")
            return int(parts[i + 2], 16)
    die("no .text section in patch ELF")


def hex_lines_patched(orig_text, image, new_end):
    """Re-emit the original hex, changing only data records whose bytes differ,
    extending the image with 16-byte records up to new_end."""
    def record(addr16, data):
        body = bytes([len(data), addr16 >> 8, addr16 & 0xFF, 0]) + data
        return ":" + (body + bytes([(-sum(body)) & 0xFF])).hex().upper()

    parsed, seg = [], 0
    for idx, line in enumerate(orig_text.splitlines()):
        if not line.startswith(":"):
            die(f"bad hex line {idx + 1}")
        raw = bytes.fromhex(line[1:])
        if (sum(raw) & 0xFF) != 0:
            die(f"bad checksum in original hex line {idx + 1}")
        if raw[3] == 4:
            seg = ((raw[4] << 8) | raw[5]) << 16
        parsed.append((line, raw, seg))
    last_data = max(i for i, (_, raw, _) in enumerate(parsed) if raw[3] == 0)

    out, changed = [], []
    for idx, (line, raw, seg) in enumerate(parsed):
        if raw[3] != 0:
            out.append(line)
            continue
        n, addr16 = raw[0], (raw[1] << 8) | raw[2]
        a = seg + addr16
        if idx == last_data:
            # The last record is partial (8 bytes at 0x0801A820): fill it up to
            # its 16-byte boundary so every record stays 16-byte aligned.
            n = max(n, min(16 - a % 16, new_end - a))
        new = bytes(image[a - FLASH_BASE:a - FLASH_BASE + n])
        if new != raw[4:-1]:
            rec = record(addr16, new)
            changed.append((idx + 1, line, rec))
            out.append(rec)
        else:
            out.append(line)
        if idx == last_data:
            cur = a + n
            while cur < new_end:
                if cur >> 16 != seg >> 16:
                    die("patch would need a new extended-address record")
                take = min(16, new_end - cur)
                rec = record(cur & 0xFFFF, bytes(image[cur - FLASH_BASE:cur - FLASH_BASE + take]))
                changed.append((None, None, rec))
                out.append(rec)
                cur += take
    return "\r\n".join(out) + "\r\n", changed


def disasm(code, addr, count=None):
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    rows = []
    for ins in md.disasm(code, addr):
        rows.append(f"0x{ins.address:08X}  {ins.bytes.hex():<10} {ins.mnemonic:<8} {ins.op_str}")
        if count and len(rows) >= count:
            break
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hex", nargs="?",
                    default=str(ROOT / "original" / "WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex"))
    ap.add_argument("--out", default=str(ROOT / "out"))
    args = ap.parse_args()

    src = Path(args.hex).read_bytes()
    if sha256(src) != ORIGINAL_SHA256:
        die(f"{args.hex}: SHA-256 {sha256(src)} is not the TIGA v1.1 image {ORIGINAL_SHA256}")
    out = Path(args.out)
    build = out / "build"
    build.mkdir(parents=True, exist_ok=True)

    ih = IntelHex(args.hex)
    if ih.minaddr() != FLASH_BASE or ih.maxaddr() != IMAGE_END - 1:
        die("unexpected image extent")
    if len(ih.segments()) != 1:
        die("original image is not contiguous")
    orig = bytearray(ih.tobinstr(start=FLASH_BASE, size=IMAGE_END - FLASH_BASE))

    for addr, old, target, _ in HOOKS:
        got = bytes(orig[addr - FLASH_BASE:addr - FLASH_BASE + 4])
        if got != old or decode_bl(got, addr) != target:
            die(f"hook site 0x{addr:08X} holds {got.hex()}, expected bl 0x{target:08X}")

    ver, blob, syms, sections = compile_patch(build)
    load = text_section_addr(sections)
    if load != IMAGE_END:
        die(f"patch linked at 0x{load:08X}, expected 0x{IMAGE_END:08X}")
    new_end = load + len(blob)
    if new_end > PATCH_LIMIT:
        die(f"patch ends at 0x{new_end:08X}, past 0x{PATCH_LIMIT:08X}")

    image = bytearray(orig) + blob
    for addr, _, _, sym in HOOKS:
        target, kind = syms[sym]
        if kind != "T" or not load <= target < new_end:
            die(f"symbol {sym} at 0x{target:08X} is not in the patch")
        enc = encode_bl(addr, target)
        if decode_bl(enc, addr) != target:
            die(f"BL encoding self-test failed at 0x{addr:08X}")
        image[addr - FLASH_BASE:addr - FLASH_BASE + 4] = enc

    hex_text, changed = hex_lines_patched(src.decode("ascii"), image, new_end)
    check = IntelHex()
    check.loadhex(io.StringIO(hex_text))
    if check.tobinstr(start=FLASH_BASE, size=new_end - FLASH_BASE) != bytes(image):
        die("re-parsed patched.hex does not match the patched image")
    if check.start_addr != ih.start_addr:
        die("start address record changed")

    hex_path = out / "patched.hex"
    hex_path.write_bytes(hex_text.encode("ascii"))
    (build / "patched.bin").write_bytes(image)

    # ---- report -----------------------------------------------------------
    diffs = []
    i = 0
    while i < len(image):
        a = orig[i] if i < len(orig) else None
        if a != image[i]:
            j = i
            while j < len(image) and (orig[j] if j < len(orig) else None) != image[j]:
                j += 1
            diffs.append((FLASH_BASE + i, FLASH_BASE + j))
            i = j
        else:
            i += 1
    sectors = sorted({(a - FLASH_BASE) // SECTOR_SIZE for s, e in diffs for a in range(s, e)})

    r = []
    r.append("# Patch build report: SignalRGB for Zoom75 TIGA v1.1\n")
    r.append("Generated by `build_patch.py`. Run `tools/selfcheck.py` for the independent check.\n")
    r.append("## Inputs and outputs\n")
    r.append("| Item | SHA-256 |\n|---|---|")
    r.append(f"| original hex `{Path(args.hex).name}` | `{sha256(src)}` |")
    r.append(f"| patch/srgb.c | `{sha256((ROOT / 'patch' / 'srgb.c').read_bytes())}` |")
    r.append(f"| patch/fw.h | `{sha256((ROOT / 'patch' / 'fw.h').read_bytes())}` |")
    r.append(f"| patch/srgb.ld | `{sha256((ROOT / 'patch' / 'srgb.ld').read_bytes())}` |")
    r.append(f"| patch blob ({len(blob)} B) | `{sha256(blob)}` |")
    r.append(f"| patched image 0x{FLASH_BASE:08X}-0x{new_end - 1:08X} ({len(image)} B) | `{sha256(bytes(image))}` |")
    r.append(f"| **out/patched.hex** | `{sha256(hex_text.encode('ascii'))}` |\n")
    r.append(f"Compiler: `{ver}`\n")
    r.append("Flags: `" + " ".join(CFLAGS) + " -T patch/srgb.ld -Wl,--gc-sections -Wl,--build-id=none`\n")
    r.append("## Patch symbols\n")
    for name, (a, t) in sorted(syms.items(), key=lambda kv: kv[1][0]):
        r.append(f"- `0x{a:08X}` {t} {name}")
    r.append("")
    r.append("## Changed byte ranges\n")
    for s, e in diffs:
        r.append(f"- `0x{s:08X}-0x{e - 1:08X}` ({e - s} B), sector {(s - FLASH_BASE) // SECTOR_SIZE}")
    r.append(f"\nSectors touched: {sectors} (official image covers sectors 0-53). "
             f"Highest address written: 0x{new_end - 1:08X} (limit 0x{PATCH_LIMIT - 1:08X}).\n")
    r.append("## Hook sites\n")
    for addr, old, target, sym in HOOKS:
        new = bytes(image[addr - FLASH_BASE:addr - FLASH_BASE + 4])
        r.append(f"### 0x{addr:08X}\n")
        r.append("```")
        r.append("before: " + disasm(old, addr)[0])
        r.append("after:  " + disasm(new, addr)[0] + f"   ; {sym}")
        r.append("```\n")
    r.append("## Changed hex records\n")
    r.append("```")
    for ln, old, new in changed:
        if ln is None:
            r.append(f"  new      {new}")
        else:
            r.append(f"- line {ln:<5} {old}")
            r.append(f"+ line {ln:<5} {new}")
    r.append("```\n")
    r.append("## Patch code (objdump)\n")
    r.append("```")
    r.append((build / "srgb.lst").read_text(encoding="utf-8").strip())
    r.append("```")
    (out / "patch_report.md").write_text("\n".join(r) + "\n", encoding="utf-8")

    print(f"original  {sha256(src)}")
    print(f"patched   {sha256(hex_text.encode('ascii'))}  {hex_path}")
    print(f"patch     {len(blob)} bytes at 0x{load:08X}-0x{new_end - 1:08X}")
    for addr, _, _, sym in HOOKS:
        print(f"hook      0x{addr:08X} -> {sym} 0x{syms[sym][0]:08X}")
    print(f"sectors   {sectors}")
    print(f"report    {out / 'patch_report.md'}")


if __name__ == "__main__":
    main()
