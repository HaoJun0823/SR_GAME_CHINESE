# -*- coding: utf-8 -*-
"""跨构建函数匹配（偏移投票法）
--------------------------------------------------------------------
思路: 编译差异只落在「立即数 / 位移 / 相对地址 / 寄存器分配」上,
      而「操作码 + ModRM + SIB」串在同族编译器下大面积保持。

  1. 对参照函数(已知构建)做反汇编, 把 imm/disp/rel 占用的字节标为 value,
     其余标为 fixed;
  2. 取所有 >=MINRUN 的 fixed 连续串, 逐个去目标 dump 里 find;
  3. 每个命中 (X, 参照内偏移 s) 投一票给 d = X - s, 权重 = 串长;
  4. 票数最高的 d 即「两构建间函数体整体平移量」, 入口 = 任一命中 - s;
  5. 用该 d 复核: 参照里每个 fixed 串是否都在 (s+d) 处原样出现 -> 覆盖率。

覆盖率 = 匹配的 fixed 字节 / 总 fixed 字节, 可作可信度判据。
"""
import os, re, sys
from collections import defaultdict
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

ROOT = r"G:\Projects\SR4R_DLL"
DUMP = os.path.join(ROOT, r".temp\dump_ms\SR4R_dump_text.bin")
OUT = os.path.join(ROOT, r"Tools\ms_match_report.txt")
DUMP_VA = 0x140001000
STEAM = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sr_hv.exe"
GOG = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sr_hv_gog.exe"
EPIC = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sr_hv_epic.exe"

MINRUN = 10
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


def fixed_mask(buf, va):
    """返回 (mask, insns): mask[i]=1 表示该字节是结构字节(操作码/ModRM/SIB)。"""
    mask = bytearray(len(buf))
    insns = []
    for i in md.disasm(buf, va):
        o = i.address - va
        if o >= len(buf):
            break
        for k in range(i.size):
            mask[o+k] = 1
        enc = i.encoding
        if enc.disp_size and enc.disp_offset >= 0:
            for k in range(enc.disp_offset, min(enc.disp_offset+enc.disp_size, i.size)):
                mask[o+k] = 0
        if enc.imm_size and enc.imm_offset >= 0:
            for k in range(enc.imm_offset, min(enc.imm_offset+enc.imm_size, i.size)):
                mask[o+k] = 0
        insns.append((o, i.size, i.mnemonic, i.op_str))
    return mask, insns


def runs_of(mask, minrun):
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i >= minrun:
                out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def vote(ref_buf, ref_va, dump, dump_va, label, w):
    mask, insns = fixed_mask(ref_buf, ref_va)
    rs = runs_of(mask, MINRUN)
    total_fixed = sum(mask)
    w.append("  %s: %d 字节, %d 条指令, fixed 字节 %d (%.0f%%), >=%d 的串 %d 条"
             % (label, len(ref_buf), len(insns), total_fixed,
                100.0*total_fixed/max(1, len(ref_buf)), MINRUN, len(rs)))
    votes = defaultdict(int)
    hitmap = []
    for (s, ln) in rs:
        pat = ref_buf[s:s+ln]
        pos = dump.find(pat)
        found = []
        while pos >= 0:
            found.append(pos)
            pos = dump.find(pat, pos+1)
        hitmap.append((s, ln, found))
        for p in found:
            votes[p - s] += ln
    top = sorted(votes.items(), key=lambda kv: -kv[1])[:6]
    w.append("  票数 top:")
    for dd, v in top:
        ent = ref_va + dd
        w.append("     d=%+#x  票=%d  推定入口=%s" % (dd, v, hex(ent)))
    if not top:
        w.append("     <无命中>")
        return None
    best_d = top[0][0]
    matched = 0
    detail = []
    for (s, ln, found) in hitmap:
        ok = (s + best_d) in found
        if ok:
            matched += ln
        detail.append((s, ln, ok))
    cov = 100.0 * matched / max(1, total_fixed)
    w.append("  ==> 选定 d=%+#x 推定入口=%s  匹配 fixed 字节 %d/%d = %.1f%%"
             % (best_d, hex(ref_va + best_d), matched, total_fixed, cov))
    miss = [(s, ln) for (s, ln, ok) in detail if not ok][:10]
    if miss:
        w.append("  未命中的串(前10) 参照偏移: " + ", ".join("+%X(%d)" % (s, ln) for s, ln in miss))
    return best_d, cov


def dump_ranges(pe, va, n, w):
    """把参照函数里 fixed 区段按 8 字节粒度列出来, 便于人工在 IDA 里定位。"""
    buf = pe.at(va, n)
    mask, insns = fixed_mask(buf, va)
    w.append("  fixed 段(>=12): " + ", ".join("+%X.%d" % (s, l) for s, l in runs_of(mask, 12)[:40]))


def main():
    dump = open(DUMP, "rb").read()
    steam = PE(STEAM)
    w = []
    w.append("======== MS Store dump 跨构建匹配 ========")
    w.append("dump=%s  %d 字节, va_base=%s" % (os.path.basename(DUMP), len(dump), hex(DUMP_VA)))
    w.append("")
    w.append("---- [验证] Steam FORMAT (0x140CF9A00, 0x790 字节) ----")
    vote(steam.at(0x140CF9A00, 0x790), 0x140CF9A00, dump, DUMP_VA, "FORMAT", w)
    w.append("  已知答案(常量锚点): MS 入口 = 0x140E92DA0 -> d = 0x1993A0")
    w.append("")
    w.append("---- [目标] Steam SUBTITLE (0x1403D18F0, 0x458 字节) ----")
    vote(steam.at(0x1403D18F0, 0x458), 0x1403D18F0, dump, DUMP_VA, "SUBTITLE", w)
    w.append("")
    w.append("---- [参考] Steam DRAW_WIDE (0x140DC36A0, 0x200 字节) ----")
    vote(steam.at(0x140DC36A0, 0x200), 0x140DC36A0, dump, DUMP_VA, "DRAW_WIDE", w)
    w.append("  已知答案: MS 入口 = 0x140EF6D40 -> d = 0x1336A0")
    open(OUT, "w", encoding="utf-8").write("\n".join(w) + "\n")
    print("ok ->", OUT)


main()
