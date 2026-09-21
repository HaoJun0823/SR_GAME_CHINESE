#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_runtime_globals.py — 验证「运行时自解引擎全局变量」机制

作用
----
dllmain.cpp 从 v1.2 起不再依赖版本 VA 表来拿 fontTab / fontCount, 改为在
AOB 定位到 FontLookup 之后, 从函数体内的两条固定指令模式反解出全局地址:

    FontLookup + 0x43   cmp  ecx, [rip+disp32]        -> fontCount
    FontLookup + 0x57   mov  rax, [rip+disp32]        -> fontTab

本脚本**直接从 dllmain.cpp 源码解析**这些模式(避免脚本与源码漂移), 在已知的
Steam / GOG / EPIC 三个构建上模拟 dllmain.cpp 的 C 逻辑, 把解出来的地址与
dllmain.cpp 里硬编码的 CFG_* VA 表逐条比对。

用法
----
    python verify_runtime_globals.py [游戏目录]

    默认游戏目录: I:\\SteamLibrary\\steamapps\\common\\Saints Row IV

退出码 0 = 全部吻合; 1 = 有不一致 (会打印差异)。
"""

import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DLLMAIN = os.path.join(HERE, "..", "SR4R_I18N", "dllmain.cpp")
DEFAULT_GAME = r"I:\SteamLibrary\steamapps\common\Saints Row IV"
BASE = 0x140000000

# 期望值: (可执行文件, FontLookup VA, fontTab VA, fontCount VA)
#   来源 = dllmain.cpp 的 CFG_STEAM / CFG_GOG / CFG_EPIC
EXPECT = [
    ("sr_hv.exe",      0x140BF8550, 0x146AE7060, 0x146AE504C),
    ("sr_hv_gog.exe",  0x140BBBBB0, 0x14698A5D0, 0x14698A5C8),
    ("sr_hv_epic.exe", 0x140BF3D00, 0x146ACD308, 0x146ACD304),
]

# ---------------- 从 dllmain.cpp 解析 C 数组 ----------------

def parse_c_array(src, name):
    """抓取 `static const uint8_t NAME[N] = { ... };` 的字节值列表。"""
    m = re.search(r"\b" + re.escape(name) + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\};",
                  src, re.S)
    if not m:
        raise SystemExit("dllmain.cpp 里找不到数组 " + name)
    body = m.group(1)
    vals = []
    for tok in body.replace("\n", " ").split(","):
        tok = tok.strip()
        if not tok:
            continue
        vals.append(int(tok, 0) & 0xFF)
    return vals

# ---------------- PE 工具 ----------------

class Pe:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        e = struct.unpack_from("<I", self.d, 0x3C)[0]
        assert self.d[e:e + 4] == b"PE\0\0", "not a PE"
        coff = e + 4
        machine, nsec, tds, _, _, optsz, _ = struct.unpack_from("<HHIIIHH", self.d, coff)
        opt = coff + 20
        self.machine, self.tds = machine, tds
        self.ep = struct.unpack_from("<I", self.d, opt + 16)[0]
        self.sizeimage = struct.unpack_from("<I", self.d, opt + 56)[0]
        secoff = opt + optsz
        self.secs = []
        for i in range(nsec):
            o = secoff + i * 40
            name = self.d[o:o + 8].rstrip(b"\0").decode("latin1")
            vsz, va, rsz, ra = struct.unpack_from("<IIII", self.d, o + 8)
            ch = struct.unpack_from("<I", self.d, o + 36)[0]
            self.secs.append((name, va, vsz, ra, rsz, ch))

    def rva2off(self, rva):
        for name, va, vsz, ra, rsz, ch in self.secs:
            if va <= rva < va + max(vsz, rsz):
                return ra + (rva - va)
        return None

    def at_va(self, va, n):
        off = self.rva2off(va - BASE)
        if off is None:
            return None
        return self.d[off:off + n]

    def exec_sections(self):
        return [(n, va, vsz, ra, rsz) for (n, va, vsz, ra, rsz, ch) in self.secs
                if ch & 0x20000000 or ch & 0x00000020]   # MEM_EXECUTE | CNT_CODE

# ---------------- 复刻 dllmain.cpp 的逻辑 ----------------

def find_masked(buf, pat, msk):
    """复刻 FindMasked(): msk[i]==0 的字节跳过; 返回首个命中下标。"""
    n = len(pat)
    if len(buf) < n:
        return None
    for i in range(len(buf) - n + 1):
        ok = True
        for k in range(n):
            if (not msk or msk[k]) and buf[i + k] != pat[k]:
                ok = False
                break
        if ok:
            return i
    return None

def aob_scan(pe, sig, msk):
    """复刻 AobScan(): 可执行段内扫描; 返回命中 VA 列表。"""
    hits = []
    n = len(sig)
    for (name, va, vsz, ra, rsz) in pe.exec_sections():
        size = vsz or rsz
        buf = pe.d[ra:ra + size]
        if len(buf) < n:
            continue
        start = 0
        while True:
            i = find_masked(buf[start:], sig, msk)
            if i is None:
                break
            hits.append(BASE + va + start + i)
            start += i + 1
    return hits

def rip_target_va(pe, va, instr_len):
    """复刻 RipTargetVa(): disp32 恒为该指令最后 4 字节。"""
    b = pe.at_va(va, instr_len)
    disp = struct.unpack_from("<i", b, instr_len - 4)[0]
    return va + instr_len + disp

def resolve(pe, font_lookup_va, pat_tab, msk_tab, pat_cnt, msk_cnt, span=0x100):
    """复刻 ResolveFontGlobalsDyn()。"""
    fn = pe.at_va(font_lookup_va, span)
    if fn is None:
        return None, None
    out = []
    for pat, msk, ilen in ((pat_tab, msk_tab, 7), (pat_cnt, msk_cnt, 6)):
        i = find_masked(fn, pat, msk)
        if i is None:
            out.append(None)
            continue
        va = rip_target_va(pe, font_lookup_va + i, ilen)
        # 复刻「落点必须在主模块映像内」的校验
        out.append(va if BASE <= va < BASE + pe.sizeimage else None)
    return out[0], out[1]

# ---------------- main ----------------

def main():
    game = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GAME
    src = open(DLLMAIN, "r", encoding="utf-8").read()

    pat_tab = parse_c_array(src, "PAT_FONTTAB")
    msk_tab = parse_c_array(src, "MSK_FONTTAB")
    pat_cnt = parse_c_array(src, "PAT_FONTCOUNT")
    msk_cnt = parse_c_array(src, "MSK_FONTCOUNT")
    sig_fl  = parse_c_array(src, "SIG_FONT_LOOKUP")
    mask_fl = None

    print("== 从 dllmain.cpp 解析到的模式 ==")
    print("  SIG_FONT_LOOKUP : " + " ".join("%02X" % b for b in sig_fl))
    print("  PAT_FONTTAB     : " + " ".join(("%02X" % b) if m else "??"
                                            for b, m in zip(pat_tab, msk_tab)))
    print("  PAT_FONTCOUNT   : " + " ".join(("%02X" % b) if m else "??"
                                            for b, m in zip(pat_cnt, msk_cnt)))
    print()

    ok_all = True
    for exe, fl_va, tab_exp, cnt_exp in EXPECT:
        path = os.path.join(game, exe)
        print("== %s ==" % exe)
        if not os.path.exists(path):
            print("   ! 文件不存在, 跳过")
            ok_all = False
            continue
        pe = Pe(path)
        print("   PE: TimeDateStamp=0x%08X EP=0x%08X SizeOfImage=0x%08X"
              % (pe.tds, pe.ep, pe.sizeimage))

        hits = aob_scan(pe, sig_fl, mask_fl)
        print("   AOB FontLookup 命中 %d 处: %s"
              % (len(hits), ", ".join("0x%X" % h for h in hits)))
        fl = hits[0] if len(hits) == 1 else fl_va
        if len(hits) != 1:
            print("   ! 唯一命中校验失败, 回落到文档 VA 0x%X" % fl_va)
        if fl != fl_va:
            print("   ! 与 CFG 表记录的 FontLookup VA 不一致 (CFG=0x%X)" % fl_va)
            ok_all = False

        tab, cnt = resolve(pe, fl, pat_tab, msk_tab, pat_cnt, msk_cnt)
        for label, got, exp in (("fontTab", tab, tab_exp), ("fontCount", cnt, cnt_exp)):
            mark = "OK " if got == exp else "MISMATCH"
            print("   %-10s 自解=%-12s CFG=0x%X   %s"
                  % (label, ("0x%X" % got) if got else "FAILED", exp, mark))
            if got != exp:
                ok_all = False
        print()

    print("== 结论: %s ==" % ("全部吻合 ✔" if ok_all else "存在不一致 �’"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
