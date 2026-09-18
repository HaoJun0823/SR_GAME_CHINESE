# -*- coding: utf-8 -*-
"""调用者传播 + Steam Subtitle 全量反汇编找不变量。"""
import os, re, struct
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

ROOT = r"G:\Projects\SR4R_DLL"
DUMP = os.path.join(ROOT, r".temp\dump_ms\SR4R_dump_text.bin")
OUT = os.path.join(ROOT, r"Tools\_probe6_out.txt")
DUMP_VA = 0x140001000
d = open(DUMP, "rb").read()
STEAM = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sr_hv.exe"
md = Cs(CS_ARCH_X86, CS_MODE_64); md.detail = True

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
            roff = int.from_bytes(self.d[o+20:o+24], "little")
            ch = int.from_bytes(self.d[o+36:o+40], "little")
            self.secs.append((nm, va, vsize, roff, ch))
    def at(self, va, n):
        for nm, sva, vs, roff, ch in self.secs:
            if ch & 0x20000000 and sva <= va < sva+vs:
                return self.d[roff+(va-sva): roff+(va-sva)+n]
        return None
    def text(self):
        for nm, sva, vs, roff, ch in self.secs:
            if ch & 0x20000000 and nm == ".text":
                return sva, self.d[roff:roff+vs]
        return None, None

steam = PE(STEAM)
ssva, stext = steam.text()

def dis(buf, va, n):
    out = []
    for i in md.disasm(buf, va):
        if i.address - va >= n: break
        out.append((i.address, i.size, i.mnemonic, i.op_str))
    return out

def find_callers(buf, base, tva):
    res = []
    for m in re.finditer(b"\xe8", buf):
        o = m.start()
        if o + 5 > len(buf): continue
        rel = struct.unpack_from("<i", buf, o+1)[0]
        if base + o + 5 + rel == tva:
            res.append(base + o)
    return res

def func_start(va, buf, base, back=0x2000):
    """向前找 2 个连续 CC（对齐填充）后的第一个字节 = 函数入口。"""
    o = va - base
    lo = max(0, o - back)
    i = o
    while i > lo + 2:
        if buf[i-1] == 0xCC and buf[i-2] == 0xCC:
            return base + i
        i -= 1
    return None

w = []
w.append("======== A) MS Format(0x140E92DA0) 的调用者 → 校验传播法 ========")
callers_ms = find_callers(d, DUMP_VA, 0x140E92DA0)
w.append("MS 内 call Format 点: %s" % [hex(x) for x in callers_ms])
starts = []
for c in callers_ms:
    fs = func_start(c, d, DUMP_VA)
    starts.append(fs)
    o = fs - DUMP_VA
    w.append("  调用点 %s -> 函数入口 %s" % (hex(c), hex(fs) if fs else "<未找到>"))
    if fs:
        w.append("     入口 hex: " + " ".join("%02X" % x for x in d[o:o+20]))
        for a, sz, mn, ops in dis(d[o:o+0x30], fs, 0x30):
            w.append("     +%02X %-22s %s" % (a-fs, mn, ops))

w.append("")
w.append("  -- 这些入口字节在 Steam 里落点 --")
for i, fs in enumerate(starts):
    if not fs: continue
    pat = d[fs-DUMP_VA: fs-DUMP_VA+10]
    pos = stext.find(pat)
    w.append("    MS %s 前10字节 %s -> Steam 命中 %s" %
             (hex(fs), " ".join("%02X" % x for x in pat),
              [hex(ssva+p) for p in [pos] if p >= 0] if pos >= 0 else "无"))
w.append("  Steam Format 调用点参考: 0x1402a6290, 0x140cdfa5a, 0x140cdfb45, 0x140cf371a")

w.append("")
w.append("======== B) Steam SUBTITLE 全量反汇编（0x458 字节） ========")
sbuf = steam.at(0x1403D18F0, 0x458)
for a, sz, mn, ops in dis(sbuf, 0x1403D18F0, 0x458):
    w.append("   +%03X  %-24s %s" % (a-0x1403D18F0, mn, ops))

w.append("")
w.append("======== C) div3(0x55555556) / div9(0x38e38e39) 共现搜索（MS dump） ========")
p3 = [m.start()+2 for m in re.finditer(rb"\xB8\x56\x55\x55\x55", d)]
p9 = [m.start()+2 for m in re.finditer(rb"\xB8\x39\x8E\xE3\x38", d)]
w.append("div3 位置数=%d  div9 位置数=%d" % (len(p3), len(p9)))
near = []
for x in p3:
    for y in p9:
        if abs(y - x) <= 0x100:
            near.append((DUMP_VA+x, DUMP_VA+y, y-x))
w.append("相距 <=0x100 的共现（%d 组）:" % len(near))
for a, b, dd in near[:40]:
    fs = func_start(b, d, DUMP_VA)
    w.append("   div3@%s  div9@%s  Δ=%+#x   所在函数入口≈%s" % (hex(a), hex(b), dd, hex(fs) if fs else "?"))

open(OUT, "w", encoding="utf-8").write("\n".join(w) + "\n")
print("ok")
