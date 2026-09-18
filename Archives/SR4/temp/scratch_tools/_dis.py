# -*- coding: utf-8 -*-
"""候选函数反汇编 + 与 Steam 参照并排比对。"""
import os, re
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

ROOT = r"G:\Projects\SR4R_DLL"
DUMP = os.path.join(ROOT, r".temp\dump_ms\SR4R_dump_text.bin")
OUT = os.path.join(ROOT, r"Tools\_dis_out.txt")
DUMP_VA = 0x140001000
d = open(DUMP, "rb").read()

STEAM = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sr_hv.exe"

class PE:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        e = self.d.index(b"PE\0\0")
        nsec = int.from_bytes(self.d[e+6:e+8], "little")
        optsz = int.from_bytes(self.d[e+20:e+22], "little")
        self.base = int.from_bytes(self.d[e+24+24:e+24+32], "little")
        sec = e + 24 + optsz
        self.secs = []
        for i in range(nsec):
            o = sec + i*40
            nm = self.d[o:o+8].rstrip(b"\0").decode("latin1")
            vsize = int.from_bytes(self.d[o+8:o+12], "little")
            va = self.base + int.from_bytes(self.d[o+12:o+16], "little")
            rsize = int.from_bytes(self.d[o+16:o+20], "little")
            roff = int.from_bytes(self.d[o+20:o+24], "little")
            ch = int.from_bytes(self.d[o+36:o+40], "little")
            self.secs.append((nm, va, vsize, roff, rsize, ch))
    def at(self, va, n):
        for nm, sva, vs, roff, rs, ch in self.secs:
            if ch & 0x20000000 and sva <= va < sva+vs:
                o = roff + (va - sva)
                return self.d[o:o+n]
        return None

md = Cs(CS_ARCH_X86, CS_MODE_64); md.detail = True
def dis(buf, va, n):
    out = []
    for i in md.disasm(buf, va):
        if i.address - va >= n: break
        out.append((i.address-va, i.size, i.mnemonic, i.op_str))
    return out

def show(w, title, buf, va, n):
    w.append("---- %s @%s ----" % (title, hex(va)))
    w.append("   hex: " + " ".join("%02X" % c for c in buf[:min(n, len(buf))]))
    for off, size, mn, ops in dis(buf, va, n):
        w.append("   +%03X  %-26s %s" % (off, mn, ops))

def shape(title, buf, va, n):
    """指令形状指纹：imm/disp/目标值全部归一为 #"""
    toks = []
    for off, size, mn, ops in dis(buf, va, n):
        o = re.sub(r"0x[0-9a-f]+", "#", ops)
        o = re.sub(r"\brip\b", "rip", o)
        o = o.replace(" - #", "-#").replace(" + #", "+#")
        toks.append("%s %s" % (mn, o))
    return toks

w = []
steam = PE(STEAM)

# ---- Format ----
show(w, "Steam FORMAT", steam.at(0x140CF9A00, 140), 0x140CF9A00, 140)
show(w, "MS  fmt-cand", d[0x14016FE50-DUMP_VA:0x14016FE50-DUMP_VA+140], 0x14016FE50, 140)

w.append("")
w.append("== Format 形状序列比对（归一化后逐条） ==")
a = shape("s", steam.at(0x140CF9A00, 140), 0x140CF9A00, 140)
b = shape("m", d[0x14016FE50-DUMP_VA:0x14016FE50-DUMP_VA+140], 0x14016FE50, 140)
for i in range(max(len(a), len(b))):
    la = a[i] if i < len(a) else ""
    lb = b[i] if i < len(b) else ""
    w.append("  %2d  %-46s | %-46s %s" % (i, la, lb, "" if la == lb else "  <<< 差异"))

# ---- Subtitle 5 候选 ----
w.append("")
w.append("== Subtitle 候选（4C 8B DC 55 56 41 54 的 5 处命中） ==")
cands = [0x14014b8b0, 0x140821cc0, 0x140e294c0, 0x1410a6320, 0x141215da0]
sa = shape("s", steam.at(0x1403D18F0, 130), 0x1403D18F0, 130)
w.append("Steam SUBTITLE 形状: " + " ; ".join(sa[:12]))
for c in cands:
    buf = d[c-DUMP_VA:c-DUMP_VA+130]
    sb = shape("m", buf, c, 130)
    same = sum(1 for i in range(min(len(sa), len(sb))) if sa[i] == sb[i])
    w.append("")
    w.append("  @%s  形状前12条相同数=%d" % (hex(c), same))
    w.append("   hex: " + " ".join("%02X" % x for x in buf[:40]))
    for i, s in enumerate(sb[:12]):
        w.append("     %2d  %s" % (i, s))

open(OUT, "w", encoding="utf-8").write("\n".join(w) + "\n")
print("ok")
