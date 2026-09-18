# -*- coding: utf-8 -*-
"""精确定位 MS Store 的 Format 入口 + 用语义指纹找 Subtitle。"""
import os, re
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

ROOT = r"G:\Projects\SR4R_DLL"
DUMP = os.path.join(ROOT, r".temp\dump_ms\SR4R_dump_text.bin")
OUT = os.path.join(ROOT, r"Tools\_probe4_out.txt")
DUMP_VA = 0x140001000
d = open(DUMP, "rb").read()
md = Cs(CS_ARCH_X86, CS_MODE_64); md.detail = True

def dis(va, n):
    off = va - DUMP_VA
    out = []
    for i in md.disasm(d[off:off+n], va):
        if i.address - va >= n: break
        out.append((i.address, i.size, i.mnemonic, i.op_str))
    return out

def parse_pat(txt):
    toks = txt.split()
    b = bytes(int(t, 16) if t != "??" else 0 for t in toks)
    m = bytes(0 if t == "??" else 1 for t in toks)
    return b, m

def find(txt, limit=40):
    b, m = parse_pat(txt)
    rx = re.compile(b"".join(re.escape(bytes([c])) if mm else b"." for c, mm in zip(b, m)), re.DOTALL)
    return [mo.start() + DUMP_VA for mo in rx.finditer(d)][:limit]

def allc(txt):
    b, m = parse_pat(txt)
    rx = re.compile(b"".join(re.escape(bytes([c])) if mm else b"." for c, mm in zip(b, m)), re.DOTALL)
    return len(rx.findall(d))

w = []

# ---- 1) Format 附近反汇编：找函数入口（int3 padding / ret） ----
w.append("======== 1) 0x140E92C00..0x140E92F60 反汇编（找 Format 入口） ========")
prev = None
for a, sz, mn, ops in dis(0x140E92C00, 0x360):
    mark = ""
    if mn == "int3":
        mark = "   <-- padding"
    if mn == "ret":
        mark = "   <-- ret"
    if a in (0x140E92DF4, 0x140E92E02, 0x140E92E9C, 0x140E92EA9):
        mark += "   <<<<< 锚点常量"
    w.append("  %s  %-26s %s%s" % (hex(a), mn, ops, mark))

# ---- 2) Subtitle 语义指纹 ----
w.append("")
w.append("======== 2) Subtitle 语义锚点 ========")
subs = {
  "参数搬运 45 8B E1 0F 28 F2 44 0F 28 C1": "45 8B E1 0F 28 F2 44 0F 28 C1",
  "div3 magic B8 56 55 55 55": "B8 56 55 55 55",
  "div9 magic B8 39 8E E3 39": "B8 39 8E E3 39",
  "mov eax,0x38e38e39": "B8 39 8E E3 38",
  "xmm6->[r11-x] 41 0F 29 73": "41 0F 29 73",
  "xmm8->[r11-x] 45 0F 29 43": "45 0F 29 43",
  "movaps [rbp-x],xmm6 0F 29 B5": "0F 29 B5",
}
for label, p in subs.items():
    hs = find(p, 40)
    w.append("%-40s 总数=%-5d %s" % (label, allc(p), [hex(x) for x in hs[:14]]))

w.append("")
w.append("======== 3) 『45 8B E1 0F 28 F2 44 0F 28 C1』命中处上下文 ========")
for va in find("45 8B E1 0F 28 F2 44 0F 28 C1", 20):
    st = va - 0x60
    w.append("---- anchor @%s （从 -0x60 起反汇编，找入口）----" % hex(va))
    for a, sz, mn, ops in dis(st, 0xC0):
        mark = "  <<<<< anchor" if a == va else ""
        w.append("   %s  %-24s %s%s" % (hex(a), mn, ops, mark))

open(OUT, "w", encoding="utf-8").write("\n".join(w) + "\n")
print("ok")
