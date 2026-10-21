"""Summarize a VIA v3 keyboard definition: ids, matrix, key matrix positions, menus.

usage: python -I tools/viajson.py <definition.json>
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
d = json.load(open(sys.argv[1], encoding="utf-8"))
print("name:", d.get("name"), " vendorId:", d.get("vendorId"), " productId:", d.get("productId"))
print("matrix:", d.get("matrix"))
keys = []
rows = d["layouts"]["keymap"]
for r, row in enumerate(rows):
    for item in row:
        if isinstance(item, str):
            keys.append((r, item.split("\n")[0]))
print(f"keys in KLE keymap: {len(keys)}")
print("matrix positions:", " ".join(k for _, k in keys))
labels = d["layouts"].get("labels")
if labels:
    print("layout options:", labels)


def walk(node, depth=0):
    pad = "  " * depth
    if isinstance(node, list):
        for n in node:
            walk(n, depth)
        return
    if not isinstance(node, dict):
        print(pad + repr(node))
        return
    label = node.get("label")
    typ = node.get("type")
    content = node.get("content")
    opts = node.get("options")
    extra = ""
    if typ:
        extra += f" type={typ}"
    if isinstance(content, list) and content and isinstance(content[0], str):
        extra += f" content={content}"
    if opts is not None:
        extra += f" options({len(opts)})={opts}"
    print(f"{pad}- {label!r}{extra}")
    if isinstance(content, list) and content and not isinstance(content[0], str):
        walk(content, depth + 1)


print("menus:")
walk(d.get("menus", []))
