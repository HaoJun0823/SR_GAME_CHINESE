#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KEY 词表覆盖度审计。

目标：量化"我们的 le_string_keys.txt 相对游戏真实 KEY 全集缺了多少"，
      并判断裸 KEY 是"提取遗漏"还是"该版本根本不存在"。

做法：
  1. 从指定二进制/包文件中抽取可打印 ASCII 串
  2. 过滤出 KEY_LIKE 形态（大写+下划线，>=2 段）
  3. 与 le_string_keys.txt 的 key 集合求差
"""
import os
import re
import sys

G = r"I:\SteamLibrary\steamapps\common\Saints Row IV"
KEYLIST = os.path.join(G, r"scripts\dict\le_string_keys.txt")
OUT = r"G:\Projects\SR4R_DLL\.temp\_keyaudit.txt"

TARGETS = [
    os.path.join(G, r"packfiles\pc\cache\interface_startup.vpp_pc"),
    os.path.join(G, r"packfiles\pc\cache\interface.vpp_pc"),
    os.path.join(G, r"packfiles\pc\cache\misc.vpp_pc"),
    os.path.join(G, r"packfiles\pc\cache\misc_tables.vpp_pc"),
    os.path.join(G, r"packfiles\pc\cache\da_tables.vpp_pc"),
    os.path.join(G, r"sr_hv.exe"),
]

KEYLIKE = re.compile(rb"(?<![A-Za-z0-9_])[A-Z][A-Z0-9]{1,}(?:_[A-Z0-9]+){1,}(?![A-Za-z0-9_])")
ASCII_RUN = re.compile(rb"[\x20-\x7E]{6,}")


def load_ours():
    keys = set()
    with open(KEYLIST, "rb") as f:
        data = f.read()
    for m in re.finditer(rb'"([^"]+)"\s*:', data):
        keys.add(m.group(1))
    return keys


def scan(path):
    if not os.path.exists(path):
        return None, []
    found = set()
    runs = 0
    with open(path, "rb") as f:
        tail = b""
        while True:
            chunk = f.read(8 << 20)
            if not chunk:
                break
            buf = tail + chunk
            for m in ASCII_RUN.finditer(buf):
                runs += 1
                for k in KEYLIKE.finditer(m.group(0)):
                    found.add(k.group(0))
            tail = buf[-64:]
    return runs, found


def main():
    ours = load_ours()
    out = []
    out.append("=" * 92)
    out.append("KEY 词表覆盖度审计")
    out.append("=" * 92)
    out.append("le_string_keys.txt 中的 KEY 数: %d" % len(ours))
    out.append("")

    allfound = set()
    for p in TARGETS:
        runs, found = scan(p)
        if runs is None:
            out.append("### %-28s  <缺失>" % os.path.basename(p))
            continue
        allfound |= found
        out.append("### %-28s  键形态串 %5d 条" % (os.path.basename(p), len(found)))
    out.append("")
    out.append("六源合并去重后 KEY 形态串: %d" % len(allfound))
    out.append("")

    miss = sorted(allfound - ours)
    extra = sorted(ours - allfound)
    out.append("=" * 92)
    out.append("我们在游戏里存在、但 le_string_keys.txt 未收录的 KEY: %d 条" % len(miss))
    out.append("（这些就是「引擎能给英文/裸KEY，但词典查不到」的缺口）")
    out.append("=" * 92)
    for k in miss[:150]:
        out.append("    " + k.decode("ascii", "replace"))
    if len(miss) > 150:
        out.append("    ... 其余 %d 条省略" % (len(miss) - 150))
    out.append("")
    out.append("目标 9 KEY 是否在六源中：")
    for probe in (b"OPTION_YES", b"HUD_SUPER_LEVEL", b"SYSTEM_PAUSED", b"MENU_AUDIO_OVERALL",
                  b"CONTROLS_INVERT_AIRCRAFT_PITCH", b"DLT_DESC_CASUAL",
                  b"COOP_MENU_FRIENDLY_FIRE", b"HORDE_MODE_MENU_EXIT",
                  b"MENU_DISPLAY_TITLE", b"TEXT_LOCALIZED"):
        out.append("    %-32s %s" % (probe.decode(),
                                     "存在" if probe in allfound else "不存在"))
    out.append("")
    out.append("le_string_keys.txt 有、但六源里没有的（多半来自其它 vpp 的 le_strings）: %d 条" % len(extra))
    for k in extra[:20]:
        out.append("    " + k.decode("ascii", "replace"))

    txt = "\n".join(out)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)
    print("written")


if __name__ == "__main__":
    main()
