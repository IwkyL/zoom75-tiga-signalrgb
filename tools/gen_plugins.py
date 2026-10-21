"""Generate the two SignalRGB plugin files from one shared source, so they cannot drift.

  plugin/src/tiga_plugin.src.js  ->  plugin/Meletrix_Zoom75_TIGA_SignalRGB.js      (1EA7:CEDD, USB cable)
                                     plugin/Meletrix_Zoom75_TIGA_24G_SignalRGB.js  (6D66:8888, 2.4G receiver)

The source holds both code paths; each file differs only in the header tokens below
(name, VID/PID, `const Radio`, labels). SignalRGB matches a plugin by VendorId() and
ProductId(); an array is documented for ProductId() only, hence two files.

usage: python -I tools/gen_plugins.py           write both files
       python -I tools/gen_plugins.py --check   exit 1 if a file differs from the source
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "plugin" / "src" / "tiga_plugin.src.js"
VARIANTS = {
    "Meletrix_Zoom75_TIGA_SignalRGB.js": {
        "NAME": "Meletrix Zoom75 TIGA (SignalRGB patch)",
        "VID": "0x1EA7", "PID": "0xCEDD", "RADIO": "false", "LABEL": "Zoom75 TIGA",
        "VARIANT": "1EA7:CEDD, the USB cable (sends exactly what v1.0.1 sent)",
    },
    "Meletrix_Zoom75_TIGA_24G_SignalRGB.js": {
        "NAME": "Meletrix Zoom75 TIGA 2.4G (SignalRGB patch)",
        "VID": "0x6D66", "PID": "0x8888", "RADIO": "true", "LABEL": "Zoom75 TIGA 2.4G",
        "VARIANT": "6D66:8888, the 2.4G receiver (needs patch v2 on the keyboard)",
    },
}


def render(source, tokens):
    out = source
    for key, value in tokens.items():
        token = f"@{key}@"
        if token not in out:
            raise SystemExit(f"{SOURCE.name}: token {token} not found")
        out = out.replace(token, value)
    left = re.findall(r"@[A-Z]+@", out)
    if left:
        raise SystemExit(f"{SOURCE.name}: unknown token(s) {sorted(set(left))}")
    return out


def main():
    check = sys.argv[1:] == ["--check"]
    if sys.argv[1:] not in ([], ["--check"]):
        raise SystemExit(__doc__)
    source = SOURCE.read_text(encoding="utf-8")
    if "\r" in source:
        raise SystemExit(f"{SOURCE.name}: CRLF line endings; the plugin files use LF")
    bad = 0
    for name, tokens in VARIANTS.items():
        path = ROOT / "plugin" / name
        text = render(source, tokens)
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if check:
            ok = old == text
            print(f"{'ok  ' if ok else 'DIFF'}  plugin/{name}")
            bad += not ok
        elif old != text:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote plugin/{name}")
        else:
            print(f"unchanged plugin/{name}")
    if check:
        print("gen_plugins --check: " + ("PASS" if not bad else f"FAIL ({bad} file(s) differ from the source)"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
