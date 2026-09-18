#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在游戏目录中原始字节流搜索裸 KEY，判断"引擎画 KEY"的来源层。

背景：客户机日志里 11 个 KEY 直接上屏（OPTION_YES / TEXT_LOCALIZED / ...），
      但在已解包的 133 个 resource 文件里完全不存在。
问题：这些 KEY 是 (A) 存在于我们未解包的 packfile 里，还是
      (B) 引擎运行时组合/硬编码？

做法：全盘原始字节扫描，带正对照（PLT_PRESS_START / MENU_BACK 已知存在）。
"""
import os
import sys
import time

ROOT = r"I:\SteamLibrary\steamapps\common\Saints Row IV"

TARGETS = [
    b"OPTION_YES",
    b"TEXT_LOCALIZED",
    b"CONTROLS_INVERT_AIRCRAFT_PITCH",
    b"MENU_AUDIO_OVERALL",
    b"HUD_SUPER_LEVEL",
    b"CUTSCENE_REMOTE_SKIPPING",
    b"COOP_MENU_FRIENDLY_FIRE",
    b"HORDE_MODE_MENU_EXIT",
    b"MENU_DISPLAY_TITLE",
    b"DLT_DESC_CASUAL",
    b"SYSTEM_PAUSED",
    # 正对照：日志里确认能正常翻译的 KEY，必然存在于资源中
    b"PLT_PRESS_START",
    b"MENU_BACK",
    b"CONTROL_NO",
]

SKIP_EXT = {".i64", ".id0", ".id1", ".id2", ".nam", ".til", ".pdb", ".obj", ".tlog", ".pch"}

HITS = {t: [] for t in TARGETS}
SCANNED = []
T0 = time.time()
TOTAL = 0


def scan_file(path):
    global TOTAL
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    TOTAL += size
    SCANNED.append((path, size))
    # 重叠窗口，避免跨块漏匹配
    maxlen = max(len(t) for t in TARGETS)
    tail = b""
    try:
        with open(path, "rb") as f:
            off = 0
            while True:
                chunk = f.read(4 << 20)
                if not chunk:
                    break
                buf = tail + chunk
                base = off - len(tail)
                for t in TARGETS:
                    start = 0
                    while True:
                        i = buf.find(t, start)
                        if i < 0:
                            break
                        abs_off = base + i
                        if len(HITS[t]) < 40:
                            HITS[t].append((path, abs_off))
                        start = i + 1
                tail = buf[-maxlen:]
                off += len(chunk)
    except OSError as e:
        print("  ! read fail %s: %s" % (path, e))


def main():
    out = []
    out.append("=" * 92)
    out.append("裸 KEY 全盘原始字节扫描   root=%s" % ROOT)
    out.append("=" * 92)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXT:
                continue
            p = os.path.join(dirpath, fn)
            scan_file(p)
            if time.time() - T0 > 540:
                out.append("!! 时间上限到，提前结束")
                break
        else:
            continue
        break

    out.append("扫描文件 %d 个，合计 %.2f GB，耗时 %.1fs" % (
        len(SCANNED), TOTAL / 1024.0 / 1024.0 / 1024.0, time.time() - T0))
    out.append("")
    for t in TARGETS:
        name = t.decode()
        hs = HITS[t]
        out.append("### %-34s 命中 %d 处" % (name, len(hs)))
        for p, o in hs[:12]:
            out.append("      %s  @0x%X" % (os.path.relpath(p, ROOT), o))
    out.append("")
    out.append("=" * 92)
    out.append("文件体积 Top 25")
    out.append("=" * 92)
    for p, s in sorted(SCANNED, key=lambda x: -x[1])[:25]:
        out.append("  %12d  %s" % (s, os.path.relpath(p, ROOT)))

    txt = "\n".join(out)
    with open(r"G:\Projects\SR4R_DLL\.temp\_scan_keys.txt", "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)


if __name__ == "__main__":
    main()
