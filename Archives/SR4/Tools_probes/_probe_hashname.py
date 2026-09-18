# -*- coding: utf-8 -*-
"""验证假设: le_string dump 表里的 HASH_xxxxxxxx = 原始符号名的某种哈希。
若成立, 则裸 KEY(如 MENU_BACK) 可在表中找到对应译文, 无需人工翻译。

做法: 对若干已知 KEY 计算多种候选哈希, 与 en/zh 表中 HASH_ 键比对。
"""
import os
import re
import zlib
import struct

BASE = r"G:\Projects\SR4R_DLL\resource\le_string"

# 待验证 KEY -> 观察到的候选 HASH (来自 en 表值匹配)
CASES = [
    ("MENU_BACK", "438825D7"),
    ("CONTROL_YES", None),
    ("CONTROL_NO", None),
    ("PLT_PRESS_START", None),
]


def crc32_variants(s: str):
    b = s.encode("ascii")
    out = {}
    out["crc32_be"] = "%08X" % (zlib.crc32(b) & 0xFFFFFFFF)
    out["crc32_le"] = "%08X" % (struct.unpack("<I", struct.pack(">I", zlib.crc32(b) & 0xFFFFFFFF))[0])
    # CRC32 with initial 0
    out["crc32_init0"] = "%08X" % (zlib.crc32(b, 0) & 0xFFFFFFFF)
    # FNV-1a 32
    h = 0x811C9DC5
    for c in b:
        h ^= c
        h = (h * 0x01000193) & 0xFFFFFFFF
    out["fnv1a"] = "%08X" % h
    # djb2
    h = 5381
    for c in b:
        h = ((h * 33) + c) & 0xFFFFFFFF
    out["djb2"] = "%08X" % h
    return out


def load_table(path):
    d = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r'^"([^"]+)"\s*:\s*"(.*)"\s*;?\s*$', line.rstrip("\n"))
            if m:
                d[m.group(1)] = m.group(2)
    return d


def main():
    tables = {}
    for sub in ("en", "zh"):
        for fn in os.listdir(os.path.join(BASE, sub)):
            if fn.endswith(".txt"):
                tables["%s/%s" % (sub, fn)] = load_table(os.path.join(BASE, sub, fn))
    print("tables loaded:", len(tables))
    print()

    for key, hint in CASES:
        print("### KEY =", key, ("(hint HASH_%s)" % hint) if hint else "")
        for name, hv in crc32_variants(key).items():
            hit = "HASH_" + hv
            where = [t for t, d in tables.items() if hit in d]
            flag = "  <== 命中表!" if where else ""
            print("   %-14s %s%s" % (name, hit, flag))
            if where:
                for w in where:
                    print("        值 = %r" % tables[w][hit])
        print()
        # 若已知 hint, 直接取
        if hint:
            hit = "HASH_" + hint
            for t, d in tables.items():
                if hit in d:
                    print("   [hint] %s = %r" % (t, d[hit]))
        print("-" * 60)


if __name__ == "__main__":
    main()
