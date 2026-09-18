# -*- coding: utf-8 -*-
"""语义锚点探针：用函数体的『不变量』（常量 / 初始化标志）在 MS Store dump 里定位。"""
import os, re
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

ROOT = r"G:\Projects\SR4R_DLL"
DUMP = os.path.join(ROOT, r".temp\dump_ms\SR4R_dump_text.bin")
OUT = os.path.join(ROOT, r"Tools\_probe3_out.txt")
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
            roff = int.from_bytes(self.d[o+20:o+24], "little")
            ch = int.from_bytes(self.d[o+36:o+40], "little")
            self.secs.append((nm, va, vsize, roff, ch))
    def at(self, va, n):
        for nm, sva, vs, roff, ch in self.secs:
            if ch & 0x20000000 and sva <= va < sva+vs:
                return self.d[roff + (va-sva): roff + (va-sva) + n]
        return None

md = Cs(CS_ARCH_X86, CS_MODE_64); md.detail = True
steam = PE(STEAM)

def dis(buf, va, n):
    out = []
    for i in md.disasm(buf, va):
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

w = []

# ---------- 1) Steam 侧：扒 Format / Subtitle 的 call 目标与不变量 ----------
def analyse(title, va, n):
    buf = steam.at(va, n)
    ins = dis(buf, va, n)
    w.append("==== %s @%s (%d 字节窗口) ====" % (title, hex(va), n))
    calls = []
    consts = []
    for a, sz, mn, ops in ins:
        if mn == "call":
            calls.append((a - va, ops))
        mm = re.search(r"0x([0-9a-fA-F]{6,})", ops)
        if mm and mn in ("mov", "movabs", "movabs ", "cmp", "movd", "imul", "and", "or", "add", "sub"):
            if mn in ("movabs",) or "," in ops:
                consts.append((a - va, mn, ops))
    w.append("  -- call 目标 --")
    for off, t in calls[:60]:
        w.append("     +%04X  call %s" % (off, t))
    w.append("  -- 常量立即数（>=6 位十六进制） --")
    for off, mn, ops in consts[:60]:
        w.append("     +%04X  %-8s %s" % (off, mn, ops))
    # 找 ret/padding 边界
    end = None
    for i, (a, sz, mn, ops) in enumerate(ins):
        if mn == "ret":
            end = a - va
            break
    w.append("  首个 ret 偏移 = %s" % (hex(end) if end else "无"))
    return ins

analyse("Steam FORMAT", 0x140CF9A00, 0x900)
w.append("")
analyse("Steam SUBTITLE", 0x1403D18F0, 0x480)
w.append("")

# ---------- 2) 在 MS dump 里搜语义模式 ----------
w.append("======== MS Store dump 语义锚点搜索 ========")
probes = {
  "mov r8d,0xffdf  41 B8 DF FF 00 00": "41 B8 DF FF 00 00",
  "movabs r11 49 BB 00 02 00 00 01 00 FF 03": "49 BB 00 02 00 00 01 00 FF 03",
  "任意 movabs r11 49 BB": "49 BB",
  "初始化标志：80 3D ???? 00 C6 05": "80 3D ?? ?? ?? ?? 00 C6 05 ?? ?? ?? ?? 01",
  "C6 05 ???? 01 (byte flag=1)": "C6 05 ?? ?? ?? ?? 01",
  "cmp byte[rip],0 : 80 3D ???? 00 0F 84": "80 3D ?? ?? ?? ?? 00 0F 84",
  "Fmt-dup 4D 85 C0 0F 84 (test r8,r8;je)": "4D 85 C0 0F 84",
  "Sub: mov r12d,r9d 45 8B E1": "45 8B E1",
  "Sub: movaps xmm6,xmm2 0F 28 F2": "0F 28 F2",
  "Sub: movaps xmm8,xmm1 44 0F 28 C1": "44 0F 28 C1",
}
for label, p in probes.items():
    hs = find(p, 60)
    w.append("%-46s hits=%-5d %s" % (label, len(hs), [hex(x) for x in hs[:12]]))
    # 计数（不止前 60）
    b, m = parse_pat(p)
    rx = re.compile(b"".join(re.escape(bytes([c])) if mm else b"." for c, mm in zip(b, m)), re.DOTALL)
    w.append("%-46s 实际总数=%d" % ("", len(rx.findall(d))))

# ---------- 3) 对 flag 模式命中的位置做反汇编 ----------
w.append("")
w.append("======== 初始化标志模式命中处的上下文 ========")
for va in find("80 3D ?? ?? ?? ?? 00 C6 05 ?? ?? ?? ?? 01", 20):
    off = va - DUMP_VA
    w.append("---- @%s ----" % hex(va))
    w.append("   hex: " + " ".join("%02X" % c for c in d[off:off+32]))
    for a, sz, mn, ops in dis(d[off:off+48], va, 48):
        w.append("   +%03X  %-24s %s" % (a-va, mn, ops))

open(OUT, "w", encoding="utf-8").write("\n".join(w) + "\n")
print("ok")
