"""Build patch v2 (SignalRGB over USB and the 2.4G receiver) for the Zoom75 TIGA v1.1.

usage: .venv/Scripts/python.exe -I build_patch_v2.py [original.hex] [--out out_v2] [--force]

Design: PATCH_V2_DESIGN.md (approved 2026-10-20). Patch v1 stays byte-identical
(480 B at 0x0801A828 + hook A); v2 appends its radio code at 0x0801AA08 and
redirects hooks B, C and D. build_patch.py (v1) is not used or changed.

Steps (each one refuses to continue on a mismatch):
  1. Check the input hex's SHA-256 against the one known v1.1 build.
  2. Check the four hook sites hold the expected original BLs and that the image
     ends at 0x0801A827 (the free tail is absent from the hex).
  3. Check patch_v2/srgb.c, fw.h, srgb.ld are the frozen v1 sources (SHA-256).
  4. Check the pinned Arm GNU Toolchain version string.
  5. Rebuild the v1 blob from patch_v2/ and require SHA-256 b9ca4c09... and the
     v1 symbol addresses that fw_radio.h relies on.
  6. Build the radio blob (patch_v2/srgb_radio.c) at 0x0801AA08, end <= 0x0801B000.
  7. Patch hooks A (as v1), B, C, D.
  8. Cross-check against the flashed v1 (out/patched.hex, SHA-256 c93031ed...):
     identical except hooks B, C, D and the bytes from 0x0801AA08.
  9. Write <out>/patched_v2.hex in the original's exact record layout (CRLF,
     upper case, 16-byte records on 16-byte boundaries, same 0x04 and start
     records), <out>/SHA256SUMS.txt, <out>/patch_report.md and <out>/build/.
     An existing patched_v2.hex with different content is not overwritten
     without --force. --out may not be out/ or original/.

Run tools/selfcheck_v2.py and tools/emutest_v2.py afterwards.
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
SRC = ROOT / "patch_v2"
TOOLCHAIN = ROOT / ".toolchain" / "arm-gnu-15.2" / "bin"
GCC_VERSION = "arm-none-eabi-gcc.exe (Arm GNU Toolchain 15.2.Rel1 (Build arm-15.86)) 15.2.1 20251203"

ORIGINAL_SHA256 = "678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b"
V1_HEX = ROOT / "out" / "patched.hex"
V1_HEX_SHA256 = "c93031edfbe3807372b5014b689165278e532de6e4f563526b0f0c6f850e6a79"
V1_BLOB_SHA256 = "b9ca4c09157a8c57f7bc27e97c1bb22b998d4b64fa04e3b8fd35dabbbf850f0b"
# Frozen v1 sources (patch/), copied unchanged into patch_v2/.
V1_SOURCES = {
    "srgb.c": "fbde5ec13733e36f5c596202472d5c5cd8ed3009f4c2153a35b45557e9bd1030",
    "fw.h": "f55a0f44ab2bbae2a1f166067044ef6559f1d177e33e7b3a5fb891756867af14",
    "srgb.ld": "24e96a6229b7f9271521d9f6e4809631b2b8418b2b87cb056fc69ee3ab654509",
}
# v1 symbols that fw_radio.h hard-codes (V1_SRGB_RENDER) or hook A targets.
V1_SYMBOLS = {"srgb_rx": 0x0801A828, "srgb_render": 0x0801A910}

FLASH_BASE = 0x08000000
IMAGE_END = 0x0801A828          # first byte after the official image
V1_END = 0x0801AA08             # first byte after the v1 code (480 B)
SECTOR_SIZE = 0x800
PATCH_LIMIT = 0x0801B000        # end of sector 53 (FEASIBILITY.md condition 1)
HEX_NAME = "patched_v2.hex"

# (hook, site, original bytes, original BL target, blob, symbol)
HOOKS = [
    ("A", 0x08014D42, bytes.fromhex("fdf763fb"), 0x0801240C, "v1", "srgb_rx"),          # USB HID task
    ("B", 0x0801017C, bytes.fromhex("00f0a2f8"), 0x080102C4, "v2", "srgb_render2"),     # rgb_matrix_task
    ("C", 0x080140E2, bytes.fromhex("fbf79dfa"), 0x0800F620, "v2", "srgb_rx_radio"),    # +DATA: -> VIA
    ("D", 0x0801413E, bytes.fromhex("f3f787ff"), 0x08008050, "v2", "srgb_radio_send"),  # reply line
]

# Same flags as build_patch.py (v1); the v1 blob must come out byte-identical.
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
    sys.exit(f"build_patch_v2: ERROR: {msg}")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def tool(name):
    return str(TOOLCHAIN / f"arm-none-eabi-{name}.exe")


def run(args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode:
        die(f"{Path(args[0]).name} failed:\n{r.stdout}{r.stderr}")
    return r.stdout


# encode_bl / decode_bl / hex_lines_patched / disasm: same logic as build_patch.py.

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


def compile_blob(build, name, source, script, load):
    """Compile one source file, link it at `load`, return (blob, symbols)."""
    elf = build / f"{name}.elf"
    run([tool("gcc"), *CFLAGS, "-T", str(script), "-Wl,--gc-sections",
         "-Wl,--build-id=none", f"-Wl,-Map={build / (name + '.map')}", "-o", str(elf), str(source)])
    blob_path = build / f"{name}.bin"
    run([tool("objcopy"), "-O", "binary", "-j", ".text", str(elf), str(blob_path)])
    syms = {}
    for line in run([tool("nm"), "-n", str(elf)]).splitlines():
        a, t, sym = line.split()
        syms[sym] = (int(a, 16), t)
    (build / f"{name}.lst").write_text(run([tool("objdump"), "-d", str(elf)]), encoding="utf-8")
    sections = run([tool("readelf"), "-S", "-W", str(elf)])
    addr = None
    for line in sections.splitlines():
        if " .text " in line:
            parts = line.split()
            addr = int(parts[parts.index(".text") + 2], 16)
    if addr != load:
        die(f"{name}: .text linked at {addr if addr is None else hex(addr)}, expected 0x{load:08X}")
    for line in sections.splitlines():
        for sec in (".data", ".bss"):
            if f" {sec} " in line:
                parts = line.split()
                if int(parts[parts.index(sec) + 4], 16):
                    die(f"{name}: non-empty {sec} section")
    return blob_path.read_bytes(), syms


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


def diff_ranges(a, b):
    """Byte ranges where b differs from a (a may be shorter: missing = differs)."""
    out, i = [], 0
    while i < len(b):
        if (a[i] if i < len(a) else None) != b[i]:
            j = i
            while j < len(b) and (a[j] if j < len(a) else None) != b[j]:
                j += 1
            out.append((FLASH_BASE + i, FLASH_BASE + j))
            i = j
        else:
            i += 1
    return out


def sectors_of(ranges):
    return sorted({(x - FLASH_BASE) // SECTOR_SIZE for s, e in ranges for x in range(s, e)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hex", nargs="?",
                    default=str(ROOT / "original" / "WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex"))
    ap.add_argument("--out", default=str(ROOT / "out_v2"))
    ap.add_argument("--force", action="store_true",
                    help=f"overwrite an existing {HEX_NAME} whose content differs")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    for forbidden in (ROOT / "out", ROOT / "original"):
        if out == forbidden.resolve() or forbidden.resolve() in out.parents:
            die(f"--out {out} is inside {forbidden.name}/, which must not be written")

    # 1-2. input
    src = Path(args.hex).read_bytes()
    if sha256(src) != ORIGINAL_SHA256:
        die(f"{args.hex}: SHA-256 {sha256(src)} is not the TIGA v1.1 image {ORIGINAL_SHA256}")
    ih = IntelHex(args.hex)
    if ih.minaddr() != FLASH_BASE or ih.maxaddr() != IMAGE_END - 1:
        die("unexpected image extent")
    if len(ih.segments()) != 1:
        die("original image is not contiguous")
    orig = bytearray(ih.tobinstr(start=FLASH_BASE, size=IMAGE_END - FLASH_BASE))
    for hook, addr, old, target, _, _ in HOOKS:
        got = bytes(orig[addr - FLASH_BASE:addr - FLASH_BASE + 4])
        if got != old or decode_bl(got, addr) != target:
            die(f"hook {hook} site 0x{addr:08X} holds {got.hex()}, expected bl 0x{target:08X}")

    # 3. frozen v1 sources
    for name, want in V1_SOURCES.items():
        got = sha256((SRC / name).read_bytes())
        if got != want:
            die(f"patch_v2/{name} is not the frozen v1 source (SHA-256 {got}, want {want})")
        if sha256((ROOT / "patch" / name).read_bytes()) != want:
            die(f"patch/{name} changed; v1 sources must stay frozen")

    # 4. toolchain
    ver = run([tool("gcc"), "--version"]).splitlines()[0].strip()
    if ver != GCC_VERSION:
        die(f"unexpected compiler (reproducibility):\n  got  {ver}\n  want {GCC_VERSION}")

    build = out / "build"
    (build / "v1").mkdir(parents=True, exist_ok=True)

    # 5. v1 blob
    v1_blob, v1_syms = compile_blob(build / "v1", "srgb", SRC / "srgb.c", SRC / "srgb.ld", IMAGE_END)
    if sha256(v1_blob) != V1_BLOB_SHA256 or IMAGE_END + len(v1_blob) != V1_END:
        die(f"rebuilt v1 blob ({len(v1_blob)} B, {sha256(v1_blob)}) is not {V1_BLOB_SHA256}")
    for sym, addr in V1_SYMBOLS.items():
        if v1_syms.get(sym, (None,))[0] != addr:
            die(f"v1 symbol {sym} is not at 0x{addr:08X}")

    # 6. radio blob
    v2_blob, v2_syms = compile_blob(build, "srgb_radio", SRC / "srgb_radio.c", SRC / "srgb_radio.ld", V1_END)
    new_end = V1_END + len(v2_blob)
    if new_end > PATCH_LIMIT:
        die(f"patch ends at 0x{new_end:08X}, past 0x{PATCH_LIMIT:08X}")

    # 7. image + hooks
    image = bytearray(orig) + v1_blob + v2_blob
    blobs = {"v1": (v1_syms, IMAGE_END, V1_END), "v2": (v2_syms, V1_END, new_end)}
    targets = {}
    for hook, addr, _, _, which, sym in HOOKS:
        syms, lo, hi = blobs[which]
        target, kind = syms.get(sym, (None, None))
        if kind != "T" or not lo <= target < hi:
            die(f"symbol {sym} is not a global function in the {which} blob")
        enc = encode_bl(addr, target)
        if decode_bl(enc, addr) != target:
            die(f"BL encoding self-test failed at 0x{addr:08X}")
        image[addr - FLASH_BASE:addr - FLASH_BASE + 4] = enc
        targets[hook] = target

    # 8. against the flashed v1
    v1_text = V1_HEX.read_bytes()
    if sha256(v1_text) != V1_HEX_SHA256:
        die(f"{V1_HEX} is not the flashed v1 build {V1_HEX_SHA256}")
    v1h = IntelHex(str(V1_HEX))
    v1_img = v1h.tobinstr(start=FLASH_BASE, size=V1_END - FLASH_BASE)
    allowed = {a + i for _, a, _, _, which, _ in HOOKS if which == "v2" for i in range(4)}
    bad = [FLASH_BASE + i for i in range(V1_END - FLASH_BASE)
           if v1_img[i] != image[i] and FLASH_BASE + i not in allowed]
    if bad or v1h.maxaddr() != V1_END - 1:
        die(f"v2 differs from v1 outside hooks B/C/D: {[hex(a) for a in bad[:8]]}")

    # 9. outputs
    hex_text, changed = hex_lines_patched(src.decode("ascii"), image, new_end)
    check = IntelHex()
    check.loadhex(io.StringIO(hex_text))
    if check.tobinstr(start=FLASH_BASE, size=new_end - FLASH_BASE) != bytes(image) \
            or check.maxaddr() != new_end - 1:
        die(f"re-parsed {HEX_NAME} does not match the patched image")
    if check.start_addr != ih.start_addr:
        die("start address record changed")
    hex_bytes = hex_text.encode("ascii")
    hex_path = out / HEX_NAME
    if hex_path.exists() and hex_path.read_bytes() != hex_bytes and not args.force:
        die(f"{hex_path} exists with different content (SHA-256 "
            f"{sha256(hex_path.read_bytes())}); back it up or pass --force")
    hex_path.write_bytes(hex_bytes)
    (build / "patched_v2.bin").write_bytes(image)
    (out / "SHA256SUMS.txt").write_text(
        f"{sha256(hex_bytes)} *{HEX_NAME}\n"
        f"{sha256(bytes(image))} *build/patched_v2.bin\n"
        f"{sha256(v1_blob)} *build/v1/srgb.bin\n"
        f"{sha256(v2_blob)} *build/srgb_radio.bin\n", encoding="utf-8", newline="\n")

    vs_orig = diff_ranges(orig, image)
    vs_v1 = diff_ranges(v1_img, image)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)

    def site_context(addr, before=3, after=3):
        """A few instructions around a site, decoded from a known-good start."""
        start = {0x08014D42: 0x08014D36, 0x0801017C: 0x08010174,
                 0x080140E2: 0x080140DA, 0x0801413E: 0x08014136}[addr]
        rows = []
        for ins in md.disasm(bytes(image[start - FLASH_BASE:start - FLASH_BASE + 32]), start):
            mark = "  <== hook" if ins.address == addr else ""
            rows.append(f"0x{ins.address:08X}  {ins.bytes.hex():<10} {ins.mnemonic:<8} {ins.op_str}{mark}")
            if ins.address > addr and len(rows) >= before + after + 1:
                break
        return rows

    r = []
    r.append("# Patch v2 build report: SignalRGB over USB and the 2.4G receiver, Zoom75 TIGA v1.1\n")
    r.append("Generated by `build_patch_v2.py`. Design: `PATCH_V2_DESIGN.md`. Run `tools/selfcheck_v2.py` "
             "and `tools/emutest_v2.py` for the independent checks.\n")
    r.append("## Inputs and outputs\n")
    r.append("| Item | SHA-256 |\n|---|---|")
    r.append(f"| original hex `{Path(args.hex).name}` | `{sha256(src)}` |")
    r.append(f"| v1 hex `out/patched.hex` (flashed, cross-checked) | `{sha256(v1_text)}` |")
    for name in ("srgb.c", "fw.h", "srgb.ld", "srgb_radio.c", "fw_radio.h", "srgb_radio.ld"):
        frozen = " (frozen v1)" if name in V1_SOURCES else ""
        r.append(f"| patch_v2/{name}{frozen} | `{sha256((SRC / name).read_bytes())}` |")
    r.append(f"| v1 blob, rebuilt ({len(v1_blob)} B at 0x{IMAGE_END:08X}) | `{sha256(v1_blob)}` |")
    r.append(f"| radio blob ({len(v2_blob)} B at 0x{V1_END:08X}) | `{sha256(v2_blob)}` |")
    r.append(f"| patched image 0x{FLASH_BASE:08X}-0x{new_end - 1:08X} ({len(image)} B) | `{sha256(bytes(image))}` |")
    r.append(f"| **out_v2/{HEX_NAME}** ({len(hex_bytes)} B) | `{sha256(hex_bytes)}` |\n")
    r.append(f"Compiler: `{ver}`\n")
    r.append("Flags (both blobs): `" + " ".join(CFLAGS) + " -T <script> -Wl,--gc-sections -Wl,--build-id=none`\n")
    r.append("## Patch symbols\n")
    for which in ("v1", "v2"):
        syms, lo, hi = blobs[which]
        r.append(f"{which} (0x{lo:08X}-0x{hi - 1:08X}):")
        for sym, (a, t) in sorted(syms.items(), key=lambda kv: kv[1][0]):
            if lo <= a < hi:
                r.append(f"- `0x{a:08X}` {t} {sym}")
        r.append("")
    r.append("## Changed byte ranges vs the original\n")
    for s, e in vs_orig:
        r.append(f"- `0x{s:08X}-0x{e - 1:08X}` ({e - s} B), sector {(s - FLASH_BASE) // SECTOR_SIZE}")
    r.append(f"\nSectors touched: {sectors_of(vs_orig)} (official image covers sectors 0-53). "
             f"Highest address written: 0x{new_end - 1:08X} (limit 0x{PATCH_LIMIT - 1:08X}).\n")
    r.append("## Changed byte ranges vs v1 (`out/patched.hex`)\n")
    for s, e in vs_v1:
        r.append(f"- `0x{s:08X}-0x{e - 1:08X}` ({e - s} B), sector {(s - FLASH_BASE) // SECTOR_SIZE}")
    r.append(f"\nSectors that differ from v1: {sectors_of(vs_v1)}.\n")
    r.append("## Hook sites\n")
    for hook, addr, old, target, which, sym in HOOKS:
        r.append(f"### Hook {hook} 0x{addr:08X} (sector {(addr - FLASH_BASE) // SECTOR_SIZE})\n")
        r.append("```")
        r.append("original: " + disasm(old, addr)[0])
        r.append("v1:       " + disasm(v1_img[addr - FLASH_BASE:addr - FLASH_BASE + 4], addr)[0])
        r.append("v2:       " + disasm(bytes(image[addr - FLASH_BASE:addr - FLASH_BASE + 4]), addr)[0]
                 + f"   ; {sym}")
        r.append("")
        r.extend(site_context(addr))
        r.append("```\n")
    r.append("## Changed hex records vs the original\n")
    r.append("```")
    for ln, old, new in changed:
        if ln is None:
            r.append(f"  new      {new}")
        else:
            r.append(f"- line {ln:<5} {old}")
            r.append(f"+ line {ln:<5} {new}")
    r.append("```\n")
    r.append("## Radio code (objdump)\n")
    r.append("```")
    r.append((build / "srgb_radio.lst").read_text(encoding="utf-8").strip())
    r.append("```")
    (out / "patch_report.md").write_text("\n".join(r) + "\n", encoding="utf-8")

    print(f"original  {sha256(src)}")
    print(f"v1 blob   {sha256(v1_blob)}  (rebuilt, = flashed v1)")
    print(f"radio     {len(v2_blob)} bytes at 0x{V1_END:08X}-0x{new_end - 1:08X}  {sha256(v2_blob)}")
    print(f"patched   {sha256(hex_bytes)}  {hex_path}")
    for hook, addr, _, _, _, sym in HOOKS:
        print(f"hook {hook}    0x{addr:08X} -> {sym} 0x{targets[hook]:08X}")
    print(f"sectors   vs original {sectors_of(vs_orig)}, vs v1 {sectors_of(vs_v1)}")
    print(f"report    {out / 'patch_report.md'}")


if __name__ == "__main__":
    main()
