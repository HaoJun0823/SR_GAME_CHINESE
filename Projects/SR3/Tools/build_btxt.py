# -*- coding: utf-8 -*-
"""
build_btxt.py — SRTTR 外挂汉化词典编译器
=========================================
输入:
  unpack/text/le_data/original/*.txt     英文原文 (KEY: text 格式)
  unpack/text/le_data/schinese/*.txt     简中翻译 (KEY: text 格式)
输出:
  scripts/text.btxt    3DM 兼容 BEXT 词典 (英文原文 -> 中文译文)

BEXT 格式 (3dm64.dll 逆向实证):
  u32 magic "BEXT"
  u32 保留
  u32 条目数 N
  N 条: [u32 keyLen][key UTF-8 NUL][u32 origLen][orig UTF-8 NUL][u32 transLen][trans UTF-8 NUL]
  (keyLen/origLen/transLen 均含 NUL 字节数; key 为信息性字段, orig 为查找键)

字典键 = le_strings 里真正出现的英文文本 (unquote 后),
        值 = 对应中文译文。
查表时游戏侧文本与 orig 逐字节匹配 (DLL 内 CRC32 加速)。

用法: python build_btxt.py [--out 输出路径] [--report]
"""
import os
import re
import sys
import struct
import collections

BASE = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered"
ORIG_DIR = os.path.join(BASE, r"unpack\text\le_data\original")
ZH_DIR = os.path.join(BASE, r"unpack\text\le_data\schinese")
DEFAULT_OUT = os.path.join(BASE, "scripts", "text.btxt")

# 值部分外层必有一对引号: "KEY": "value" — (.*) 贪婪匹配至行尾最后一个引号前，
# 值内部的 \" 转义不会被误剥 (probe 实证 6224/6224 行结构完整，转义仅 \" \\ \n 三种)
LINE_RE = re.compile(r'^"([^"]+)": "(.*)"$')


def unquote(s: str) -> str:
    """'\\n' -> '\n', '\\\\' -> '\\', '\\"' -> '"'  (le_strings txt 转义规则, probe 实证仅这 3 种)"""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            n = s[i + 1]
            if n == 'n':
                out.append('\n'); i += 2; continue
            if n == 'r':
                out.append('\r'); i += 2; continue
            if n == 't':
                out.append('\t'); i += 2; continue
            if n == '"':
                out.append('"'); i += 2; continue
            if n == '\\':
                out.append('\\'); i += 2; continue
        # 未知转义 (如孤立反斜杠): 原样保留，DLL 侧同规则反查
        out.append(c)
        i += 1
    return ''.join(out)


def parse_txt(path: str) -> dict:
    """KEY: text -> {orig_text: zh_text} (同文本多 key 取首条, 覆盖优先短 key)"""
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\r\n')
            m = LINE_RE.match(line)
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            if not val:
                continue
            out.setdefault(key, val)   # 同 key 取首条
    return out


def main():
    out_path = DEFAULT_OUT
    report_only = False
    args = sys.argv[1:]
    if '--out' in args:
        out_path = args[args.index('--out') + 1]
    if '--report' in args:
        report_only = True

    # 1. 读英文原文 (us) 与中文
    files = sorted(set(os.listdir(ORIG_DIR)) & set(os.listdir(ZH_DIR)))
    files = [f for f in files if f.endswith('_us.txt')]
    print(f"配对文件: {len(files)} 个")

    # orig[zh_key_file] -> en text map; zh map
    en_pairs = []   # (en_text, zh_text, file)
    stats = collections.Counter()
    for fname in files:
        en_map = parse_txt(os.path.join(ORIG_DIR, fname))
        zh_map = parse_txt(os.path.join(ZH_DIR, fname))
        matched = 0
        for key, zh_raw in zh_map.items():
            if key not in en_map:
                stats['key_missing_in_en'] += 1
                continue
            en_raw = en_map[key]
            en = unquote(en_raw)
            zh = unquote(zh_raw)
            if en == zh:
                stats['identical_skip'] += 1      # 译文与原文相同 -> 无意义
                continue
            if not en.strip():
                stats['empty_en'] += 1
                continue
            if not zh.strip():
                stats['empty_zh'] += 1
            en_pairs.append((en, zh, fname))
            matched += 1
            stats['total'] += 1
        stats[f'file:{fname}'] = matched

    # 2. 去重: 同一英文原文不同译文 -> 冲突检测
    dedup = {}
    conflicts = []
    for en, zh, fname in en_pairs:
        if en in dedup:
            if dedup[en][0] != zh:
                conflicts.append((en, dedup[en][0], zh, fname))
        else:
            dedup[en] = (zh, fname)
    print(f"词典条目: {len(dedup)} (冲突 {len(conflicts)} 条, 取首条)")
    print(f"统计: {dict(stats)}")
    if conflicts:
        print("冲突样例 (前 5):")
        for en, zh1, zh2, fn in conflicts[:5]:
            print(f"  EN={en[:40]!r}\n    1={zh1[:40]!r}\n    2={zh2[:40]!r} ({fn})")

    if report_only:
        return

    # 3. 写 BEXT
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    buf = bytearray()
    buf += struct.pack('<III', 0x54584542, 0, len(dedup))
    for en, (zh, fname) in dedup.items():
        key = fname                      # key = 文件名 (信息性)
        kb = key.encode('utf-8') + b'\x00'
        ob = en.encode('utf-8') + b'\x00'
        tb = zh.encode('utf-8') + b'\x00'
        buf += struct.pack('<I', len(kb)) + kb
        buf += struct.pack('<I', len(ob)) + ob
        buf += struct.pack('<I', len(tb)) + tb
    with open(out_path, 'wb') as f:
        f.write(bytes(buf))
    print(f"写出: {out_path} ({len(buf)} bytes, {len(dedup)} 条)")


if __name__ == '__main__':
    main()
