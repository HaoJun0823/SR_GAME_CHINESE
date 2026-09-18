# -*- coding: utf-8 -*-
import os
ROOT = r"G:\Projects\SR4R_DLL"
DUMP = os.path.join(ROOT, r".temp\dump_ms\SR4R_dump_text.bin")
d = open(DUMP, "rb").read()
OUT = os.path.join(ROOT, r"Tools\_probe2_out.txt")
w = []
w.append("dump len = %d (0x%X)" % (len(d), len(d)))
w.append("first 32 = " + " ".join("%02X" % c for c in d[:32]))
w.append("last 32  = " + " ".join("%02X" % c for c in d[-32:]))

# 直接按 VA 取字节（映射: off = va - 0x140001000）
def at(va, n):
    o = va - 0x140001000
    return d[o:o+n] if 0 <= o < len(d) else None

targets = {
  "DRAW_WIDE": (0x140EF6D40, 48),
  "FONT_LOOKUP": (0x140E0A530, 16),
  "TEXOBJ": (0x140DEAF00, 16),
  "SRV_RESOLVE": (0x1411DFE30, 16),
  "LANG_CUR": (0x140E92770, 27),
  "LANG_TXT": (0x140E92750, 16),
  "SteamVA FORMAT": (0x140CF9A00, 16),
  "Fmt-head hit": (0x14016FE50, 16),
}
w.append("")
w.append("== 按映射取字节（这些 VA 是日志里 aob 命中的地址） ==")
for k, (va, n) in targets.items():
    b = at(va, n)
    w.append("%-16s @%s : %s" % (k, hex(va), " ".join("%02X" % c for c in b) if b else "<OOR>"))

# 直接用 find 找 DRAW_WIDE 前 30 字节
prefixes = {
  "DW30": "40 53 56 57 48 81 EC 10 01 00 00 8B BC 24 60 01 00 00 49 8B D9 0F 29 B4 24 F0 00 00 00",
  "FL16": "83 F9 FF 7D 3E 8D 81 FF FF FF 7F 83 F8 FF 7E 2E",
  "LC27": "48 8B 05 00 00 00 00 48 85 C0 74 0C 48 8B 50 08 48 85 D2 74 03 48 FF E2 33 C0 C3",
  "FMT10": "40 55 56 57 41 57 48 8D AC 24",
  "SUB14": "4C 8B DC 55 56 41 54 49 8D AB",
}
w.append("")
w.append("== 原始字节 find（无 mask） ==")
for k, hexs in prefixes.items():
    pat = bytes.fromhex(hexs.replace(" ", ""))
    hits = []
    i = d.find(pat)
    while i >= 0 and len(hits) < 8:
        hits.append(hex(0x140001000 + i))
        i = d.find(pat, i + 1)
    w.append("%-6s hits=%-3d %s" % (k, len(hits), hits))

open(OUT, "w", encoding="utf-8").write("\n".join(w) + "\n")
print("ok")
