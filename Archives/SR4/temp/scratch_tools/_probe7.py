# -*- coding: utf-8 -*-
"""确认 MS Subtitle 入口边界 + 结构比对。"""
import os, re
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

ROOT = r"G:\Projects\SR4R_DLL"
DUMP = os.path.join(ROOT, r".temp\dump_ms\SR4R_dump_text.bin")
OUT = os.path.join(ROOT, r"Tools\_probe7_out.txt")
DUMP_VA = 0x140001000
d = open(DUMP, "rb").read()
md = Cs(CS_ARCH_X86, CS_MODE_64); md.detail = True

def dis(va, n):
    o = va - DUMP_VA
    out = []
    for i in md.disasm(d[o:o+n], va):
        if i.address - va >= n: break
        out.append((i.address, i.size, i.mnemonic, i.op_str))
    return out

w = []
for center, tag in ((0x1404889B0, "div3/div9 反推入口"), (0x140488A60, "CC CC 启发式给的入口")):
    st = center - 0x40
    w.append("======== %s = %s 附近反汇编 ========" % (tag, hex(center)))
    w.append("   hex: " + " ".join("%02X" % x for x in d[st-DUMP_VA: st-DUMP_VA+0xC0]))
    for a, sz, mn, ops in dis(st, 0x120):
        mk = ""
        if a == 0x140488A60: mk += "   <<< CC启发"
        if a == 0x140488B24: mk += "   <<< div3"
        if a == 0x140488B46: mk += "   <<< div9"
        w.append("   %s  %-24s %s%s" % (hex(a), mn, ops, mk))
    w.append("")

# 全段搜初始化标志（cmp byte[rip],0 ; mov byte[rip],1）在什么位置
w.append("======== 初始化标志模式 80 3D .. 00 C6 05 .. 01 全量位置 ========")
for m in re.finditer(rb"\x80\x3D(.{4})\x00\xC6\x05(.{4})\x01", d, re.DOTALL):
    va = DUMP_VA + m.start()
    w.append("   @%s   flagA=[rip+%s]  flagB=[rip+%s]" %
             (hex(va), hex(int.from_bytes(m.group(1),'little')), hex(int.from_bytes(m.group(2),'little'))))

open(OUT, "w", encoding="utf-8").write("\n".join(w) + "\n")
print("ok")
