#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_probe_pe.py — 读游戏目录各 exe 的 PE 指纹（TimeDateStamp / EntryPoint / SizeOfImage）

用途: 判断某个构建能否被 SR4R_I18N 的 kBuildFp 表自动识别（否则走"未知构建自解"）。
输出直接写成 markdown 表格，并对照 dllmain.cpp 的 kBuildFp 表给出命中判断。
"""
import os
import struct
import sys

GAME = r"I:\SteamLibrary\steamapps\common\Saints Row IV"
OUT = r"G:\Projects\SR4R_DLL\.temp\_pe.txt"

EXES = ["sr_hv.exe", "sr_hv_epic.exe", "sr_hv_gog.exe", "sriv_microsoft.exe"]

# dllmain.cpp kBuildFp 表（key, name, tds, ep, soi）
KNOWN = [
    ("steam",   "Steam",   0x642AD908, 0x013635C4, 0x07CF8000),
    ("gog",     "GOG",     0x63FCCC27, 0x0130DC54, 0x07CAD000),
    ("epic",    "EPIC",    0x65DE7B82, 0x0135E464, 0x07CDE000),
    ("msstore", "MSStore", 0x5E58CEF8, 0x00FC44FC, 0x07E6D000),
]


def read_pe(path):
    """返回 (tds, ep, soi, machine, nsect, note)。不是合法 PE 时 note 说明原因。"""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(0x400)
        if len(head) < 0x40:
            return None, None, None, None, None, "文件 < 0x40 字节"
        if head[:2] != b"MZ":
            return None, None, None, None, None, "无 MZ 签名（首2字节 %s）" % head[:2].hex().upper()
        e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
        if e_lfanew + 0x108 > size:
            return None, None, None, None, None, "e_lfanew=0x%X 越界（文件 %d B，疑似加密/加壳）" % (e_lfanew, size)
        f.seek(e_lfanew)
        sig = f.read(4)
        if sig != b"PE\0\0":
            return None, None, None, None, None, "NT 签名 = %s（非 PE）" % sig.hex().upper()
        coff = f.read(20)
        machine, nsect, tds = struct.unpack_from("<HHI", coff, 0)
        opt = f.read(0xF0)
        magic = struct.unpack_from("<H", opt, 0)[0]
        if magic == 0x20B:
            ep = struct.unpack_from("<I", opt, 16)[0]
            soi = struct.unpack_from("<I", opt, 56)[0]
            note = "PE32+"
        elif magic == 0x10B:
            ep = struct.unpack_from("<I", opt, 16)[0]
            soi = struct.unpack_from("<I", opt, 56)[0]
            note = "PE32"
        else:
            ep = soi = 0
            note = "OptionalHeader.Magic=0x%X" % magic
        return tds, ep, soi, machine, nsect, note


def main():
    lines = []
    lines.append("# exe PE 指纹（对照 dllmain.cpp kBuildFp 表）\n")
    lines.append("| exe | 大小 | TDS | EntryPoint | SizeOfImage | 形态 | 指纹命中 |")
    lines.append("|---|---|---|---|---|---|---|")

    for n in EXES:
        p = os.path.join(GAME, n)
        if not os.path.exists(p):
            lines.append("| `%s` | — | — | — | — | **缺失** | — |" % n)
            continue
        sz = os.path.getsize(p)
        tds, ep, soi, machine, nsect, note = read_pe(p)
        if tds is None:
            lines.append("| `%s` | %d | — | — | — | ⚠ %s | — |" % (n, sz, note))
            continue
        hit = "**未命中**"
        if tds != 0:
            for k, nm, t, e, s in KNOWN:
                if t == tds:
                    sc = 1 + (e == ep) + (s == soi)
                    hit = "**%s** (score=%d/3)" % (nm, sc)
                    break
        else:
            for k, nm, t, e, s in KNOWN:
                if e == ep and s == soi:
                    hit = "**%s** (EP+SOI 全等)" % nm
                    break
        lines.append("| `%s` | %d | 0x%08X | 0x%08X | 0x%08X | %s, %d sect | %s |"
                     % (n, sz, tds, ep, soi, note, nsect, hit))

    lines.append("")
    lines.append("## kBuildFp 参照表")
    lines.append("")
    lines.append("| key | name | TDS | EntryPoint | SizeOfImage |")
    lines.append("|---|---|---|---|---|")
    for k, nm, t, e, s in KNOWN:
        lines.append("| `%s` | %s | 0x%08X | 0x%08X | 0x%08X |" % (k, nm, t, e, s))
    lines.append("")
    lines.append("> 未命中的构建会走「未知构建自解」；`text_dump = auto` 只在 hook 定位失败时才落盘。")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("written:", OUT)


if __name__ == "__main__":
    sys.exit(main())
