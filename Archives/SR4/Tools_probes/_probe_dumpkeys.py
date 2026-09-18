# -*- coding: utf-8 -*-
"""从 DumpText.dtxt 提取所有「裸 KEY 形式」的未命中串。
判据: 只含 [A-Z0-9_] 且长度>=3 且含至少一个 '_' （避免误收 SHOP/INFO 等单词）。
输出: 统计 + 与现有 le_string_keys.txt 的差集（= 词典缺失的 KEY）。"""
import os
import re

DUMP = r"I:\SteamLibrary\steamapps\common\Saints Row IV\scripts\DumpText.dtxt"
KEYS_TXT = r"G:\Projects\SR4R_DLL\resource\dict\zh\le_string_keys.txt"

KEYLIKE = re.compile(r"^[A-Z0-9_]{3,}$")


def unescape(line):
    """DumpText 每行形如 "..."；取引号内内容并反转义。"""
    s = line.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')


def main():
    found = []
    seen = set()
    with open(DUMP, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            t = unescape(line)
            if not t or "\n" in t:
                continue
            if KEYLIKE.match(t) and "_" in t:
                if t not in seen:
                    seen.add(t)
                    found.append(t)

    print("=== DumpText 中的裸 KEY 形式串 (去重) ===")
    print("count =", len(found))
    for k in sorted(found):
        print("   ", k)

    # 读取现有词典键
    have = set()
    if os.path.exists(KEYS_TXT):
        with open(KEYS_TXT, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r'^"([^"]+)"\s*:', line)
                if m:
                    have.add(m.group(1))

    print()
    print("=== 词典已有 vs 缺失 ===")
    miss = [k for k in sorted(found) if k not in have]
    ok = [k for k in sorted(found) if k in have]
    print("已在词典 : %d" % len(ok))
    print("缺失     : %d" % len(miss))
    for k in miss:
        print("   MISS", k)


if __name__ == "__main__":
    main()
