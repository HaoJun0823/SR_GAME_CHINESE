#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
两个判定实验：
  A) 9 个"MS 独有"KEY 是否由"前缀 + 拼接"构成（找 OPTION_ / HUD_SUPER 等前缀）
  B) sriv_microsoft.exe 是不是可解的真 PE（找 MZ / PE\\0\\0 / MSIXVC 签名）
"""
import os
import re

G = r"I:\SteamLibrary\steamapps\common\Saints Row IV"
EXE = os.path.join(G, "sr_hv.exe")
MS = os.path.join(G, "sriv_microsoft.exe")
OUT = r"G:\Projects\SR4R_DLL\.temp\_probe2.txt"

# A) 前缀探测：若引擎用 "OPTION_"+x 拼 key, 则裸前缀会出现在代码/数据里
PREFIXES = [b"OPTION_", b"HUD_SUPER", b"MENU_AUDIO", b"SYSTEM_PAUSED", b"DLT_DESC",
            b"COOP_MENU", b"HORDE_MODE", b"MENU_DISPLAY", b"CONTROLS_INVERT",
            b"CONTROL_YES", b"OPTION_NO", b"MENU_PAUSE"]
# B) PE 结构签名
SIGS = [b"MZ", b"MSIXVC", b"PACKAGE", b"PK\x03\x04", b"\x02\x00\x00\x00", b"MSFT"]


def hits(path, pats, cap=60):
    res = {p: [] for p in pats}
    with open(path, "rb") as f:
        tail = b""
        off = 0
        while True:
            chunk = f.read(8 << 20)
            if not chunk:
                break
            buf = tail + chunk
            base = off - len(tail)
            for p in pats:
                s = 0
                while len(res[p]) < cap:
                    i = buf.find(p, s)
                    if i < 0:
                        break
                    res[p].append(base + i)
                    s = i + 1
            tail = buf[-64:]
            off += len(chunk)
    return res


def main():
    out = []
    out.append("=" * 92)
    out.append("A) 前缀探测：sr_hv.exe   (%d B)" % os.path.getsize(EXE))
    out.append("=" * 92)
    r = hits(EXE, PREFIXES, 40)
    for p in PREFIXES:
        hs = r[p]
        out.append("  %-20s 命中 %3d   %s" % (p.decode(), len(hs),
                   " ".join("0x%X" % h for h in hs[:6])))
    out.append("")
    out.append("=" * 92)
    out.append("B) sriv_microsoft.exe 结构探测  (%d B)" % os.path.getsize(MS))
    out.append("=" * 92)
    # 只看前 4 MB（PE 头必然在前部）
    with open(MS, "rb") as f:
        head = f.read(4 << 20)
    for p in SIGS:
        idx = []
        s = 0
        while len(idx) < 12:
            i = head.find(p, s)
            if i < 0:
                break
            idx.append(i)
            s = i + 1
        out.append("  %-12s 前4MB 命中 %2d   %s" % (repr(p), len(idx),
                   " ".join("0x%X" % h for h in idx)))
    # 熵粗估：统计前 64 KB 的字节分布熵
    import math
    cnt = [0] * 256
    sample = head[:65536]
    for b in sample:
        cnt[b] += 1
    ent = -sum((c / len(sample)) * math.log2(c / len(sample)) for c in cnt if c)
    out.append("  前 64KB 香农熵 = %.4f bit/byte  (普通 PE ≈ 6.0~6.6, 加密/压缩 ≈ 7.95+)" % ent)
    out.append("  首 16 字节 = " + " ".join("%02X" % b for b in head[:16]))

    txt = "\n".join(out)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)
    print("written")


if __name__ == "__main__":
    main()
