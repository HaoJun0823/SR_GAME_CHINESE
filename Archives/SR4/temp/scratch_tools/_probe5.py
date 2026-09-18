# -*- coding: utf-8 -*-
"""Subtitle 定位：调用者反推 + 新前导形态枚举。"""
import os, re, struct
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

ROOT = r"G:\Projects\SR4R_DLL"
DUMP = os.path.join(ROOT, r".temp\dump_ms\SR4R_dump_text.bin")
OUT = os.path.join(ROOT, r"Tools\_probe5_out.txt")
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
    def sec(self, va):
        for nm, sva, vs, roff, ch in self.secs:
            if ch & 0x20000000 and sva <= va < sva+vs:
                return nm, sva, vs, roff
        return None
    def at(self, va, n):
        s = self.sec(va)
        if not s: return None
        nm, sva, vs, roff = s
        return self.d[roff+(va-sva): roff+(va-sva)+n]
    def text(self):
        for nm, sva, vs, roff, ch in self.secs:
            if ch & 0x20000000 and nm == ".text":
                return sva, self.d[roff:roff+vs]
        return None, None

steam = PE(STEAM)

def parse_pat(txt):
    toks = txt.split()
    return (bytes(int(t,16) if t!="??" else 0 for t in toks),
            bytes(0 if t=="??" else 1 for t in toks))

def findin(buf, txt, limit=200):
    b, m = parse_pat(txt)
    rx = re.compile(b"".join(re.escape(bytes([c])) if mm else b"." for c,mm in zip(b,m)), re.DOTALL)
    return [mo.start() for mo in rx.finditer(buf)][:limit]

def dis(buf, va, n):
    out = []
    for i in md.disasm(buf, va):
        if i.address - va >= n: break
        out.append((i.address, i.size, i.mnemonic, i.op_str))
    return out

w = []
sva, stext = steam.text()

# ---- 1) 找 Steam 里 Subtitle / Format / DrawWide 的调用者 ----
w.append("======== 1) Steam 内 call 目标枚举 ========")
for label, tva in (("SUBTITLE", 0x1403D18F0), ("FORMAT", 0x140CF9A00), ("DRAWWIDE", 0x140DC36A0)):
    sites = []
    for m in re.finditer(b"\xe8", stext):
        o = m.start()
        if o + 5 > len(stext): continue
        rel = struct.unpack_from("<i", stext, o+1)[0]
        tgt = sva + o + 5 + rel
        if tgt == tva:
            sites.append(sva + o)
    w.append("%s call 点 (%d 处): %s" % (label, len(sites), [hex(x) for x in sites]))

# ---- 2) MS dump: 新前导形态枚举 ----
w.append("")
w.append("======== 2) MS dump 内『新形态大栈帧前导』枚举 ========")
pat_home4 = "4C 89 4C 24 20 4C 89 44 24 18"   # mov [rsp+20],r9 ; mov [rsp+18],r8
hits = findin(d, pat_home4, 4000)
w.append("home4 前缀 4C 89 4C 24 20 4C 89 44 24 18 命中=%d" % len(hits))

cands = []
for h in hits:
    va = DUMP_VA + h
    seg = d[h:h+0x80]
    # 要求: 紧随其后有 push 系列 + lea rbp,[rsp+disp32] + B8 imm32 + E8(chkstk) + 48 2B E0
    m = re.match(rb"(?:\x4C\x89\x4C\x24\x20\x4C\x89\x44\x24\x18)?(?:\x48\x89\x4C\x24\x08|\x48\x89\x54\x24\x10)((?:\x50|\x51|\x52|\x53|\x55|\x56|\x57|\x41\x54|\x41\x55|\x41\x56|\x41\x57){2,6})\x48\x8D\xAC\x24(....)(\xB8)(....)(\xE8)(....)(\x48\x2B\xE0)", seg, re.DOTALL)
    if not m:
        continue
    frame = struct.unpack_from("<I", m.group(2), 0)[0]
    fsz = struct.unpack_from("<I", m.group(4), 0)[0]
    prolog_len = m.end()
    # 前导之后 0x50 字节内是否有 xmm6/xmm8 保存
    tail = seg[prolog_len:prolog_len+0x60]
    xmm6 = bool(re.search(rb"\x0F\x29[\x74\x75\x5C\x73]|[\x41\x0F\x29\x73]", tail))
    xmm8 = bool(re.search(rb"\x45\x0F\x29\x43|\x44\x0F\x29", tail))
    has_cookie = bool(re.search(rb"\x48\x33\xC4|\x48\x8B\x05", tail))
    cands.append((va, frame, fsz, xmm6, xmm8, has_cookie, prolog_len))

w.append("符合『新形态大栈帧』的函数入口（%d 个）:" % len(cands))
w.append("  %-14s %-10s %-10s %-6s %-6s %-8s" % ("VA", "帧", "chkstk", "xmm6", "xmm8", "cookie"))
for va, fr, fs, a, b, c, pl in cands[:80]:
    w.append("  %-14s 0x%-8X 0x%-8X %-6s %-6s %-8s" % (hex(va), fr, fs, a, b, c))

# ---- 3) 对候选逐个看入口 0x50 字节反汇编 ----
w.append("")
w.append("======== 3) 候选入口反汇编（找 Subtitle: wchar_t*, float, double, int） ========")
for va, fr, fs, a, b, c, pl in cands[:20]:
    o = va - DUMP_VA
    w.append("---- @%s 帧=0x%X chkstk=0x%X ----" % (hex(va), fr, fs))
    w.append("   hex: " + " ".join("%02X" % x for x in d[o:o+0x70]))
    for x, sz, mn, ops in dis(d[o:o+0x70], va, 0x70):
        w.append("   +%02X  %-24s %s" % (x-va, mn, ops))

open(OUT, "w", encoding="utf-8").write("\n".join(w) + "\n")
print("ok")
