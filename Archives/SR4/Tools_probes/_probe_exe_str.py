# -*- coding: utf-8 -*-
"""在 sriv_microsoft.exe 里搜日志出现的 KEY 字符串, 定位其来源。
只做只读扫描, 不修改任何文件。"""
import os
import re
import sys

EXE = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sriv_microsoft.exe"

# 日志里出现的 KEY（wrap: pre=/pend=）
KEYS = [
    "MENU_BACK",
    "MENU_RESTORE_DEFAULTS",
    "MENU_CHANGES_MADE_BODY",
    "MENU_OPTIONS_GAMEPLAY_AIRPLANE_TIP",
    "MAINMENU_EXTRAS",
    "CONTROL_YES",
    "SAVE_WARNING_BOOT",
    "PLT_MENU_WINDOWED",
    "PLT_TRILINEAR",
    "PLT_QUIT_GAME_WARNING",
    "PLT_MENU_ASPECT_TOOLTIP",
    "PLT_MENU_DISPLAY_OPTIONS_CONFIRM",
]

# 对照: le_string 表里确认存在的键
CTRL_OK = ["MENU_OPTIONS", "MAINMENU_OPTIONS", "PLT_MENU_AUTO_DETECT"]


def main():
    print("exe:", EXE)
    if not os.path.exists(EXE):
        print("!! NOT FOUND")
        return 1
    size = os.path.getsize(EXE)
    print("size: %d bytes (%.1f MB)" % (size, size / 1048576.0))
    with open(EXE, "rb") as f:
        data = f.read()
    print("loaded ok")
    print()

    print("=== 日志 KEY 在 EXE 中的出现位置 (ASCII) ===")
    for k in KEYS:
        pat = k.encode("ascii")
        hits = [m.start() for m in re.finditer(re.escape(pat), data)]
        tail = "" if len(hits) <= 4 else " ..."
        locs = " ".join("0x%X" % h for h in hits[:4]) + tail
        print("%-40s  n=%d  %s" % (k, len(hits), locs))

    print()
    print("=== 对照: le_string 已存在的键 ===")
    for k in CTRL_OK:
        pat = k.encode("ascii")
        hits = [m.start() for m in re.finditer(re.escape(pat), data)]
        print("%-40s  n=%d" % (k, len(hits)))

    print()
    print("=== 附近上下文样本: MENU_BACK 首处 ===")
    hits = [m.start() for m in re.finditer(b"MENU_BACK", data)]
    for h in hits[:3]:
        a = max(0, h - 48)
        b = min(size, h + 64)
        ctx = data[a:b]
        printable = "".join(chr(c) if 32 <= c < 127 else "." for c in ctx)
        print("  @0x%X: %s" % (h, printable))
    return 0


if __name__ == "__main__":
    sys.exit(main())
