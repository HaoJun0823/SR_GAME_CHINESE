#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转储 sr_hv.exe 中 OPTION_ / CONTROL_YES 附近的字节，判定其形态。"""
import os

EXE = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sr_hv.exe"
OUT = r"G:\Projects\SR4R_DLL\.temp\_optiondump.txt"

OFFS = [0x143F0CD, 0x145834D, 0x145837D, 0x14583B5, 0x14583F5, 0x1458435,
        0x1458475, 0x14584B5, 0x14584F5, 0x1458535, 0x1458575, 0x1462110]


def printable(bs):
    return "".join(chr(b) if 32 <= b < 127 else "." for b in bs)


def main():
    size = os.path.getsize(EXE)
    out = []
    out.append("=" * 96)
    out.append("sr_hv.exe OPTION_ / CONTROL_YES 邻域转储   文件大小 %d" % size)
    out.append("=" * 96)
    with open(EXE, "rb") as f:
        for off in OFFS:
            f.seek(max(0, off - 96))
            buf = f.read(256)
            out.append("")
            out.append("--- file_off 0x%X  (RVA 0x%X) ---" % (off, off - 0x400))
            for i in range(0, 192, 32):
                seg = buf[i:i + 32]
                if not seg:
                    break
                out.append("  +%03X  %-32s  %s" % (i - 96, printable(seg),
                                                   " ".join("%02X" % b for b in seg[:16])))
    out.append("")
    out.append("=" * 96)
    out.append("对照：sr_hv.exe 中 CONTROL_YES / CONTROL_NO 邻域")
    out.append("=" * 96)
    with open(EXE, "rb") as f:
        for probe in (b"CONTROL_YES", b"CONTROL_NO"):
            idx = f.read().find(probe)
            f.seek(0)
            data = f.read()
            i = data.find(probe)
            if i < 0:
                out.append("  %s  未找到" % probe)
                continue
            out.append("")
            out.append("--- %s @0x%X ---" % (probe.decode(), i))
            for j in range(4):
                st = max(0, i - 64 + j * 32)
                seg = data[st:st + 32]
                out.append("  %-32s  %s" % (printable(seg), " ".join("%02X" % b for b in seg[:16])))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("written")


if __name__ == "__main__":
    main()
