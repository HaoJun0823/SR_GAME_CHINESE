#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sr3le_repack.py — Saints Row: The Third REMASTERED .le_strings 回写工具
=====================================================================
将翻译后的 txt 文件回写为 .le_strings 二进制格式（与 SR4 的 sr4le_repack.py 对称）。

txt 格式 (与 sr3le_extract.py 输出一致):
  "KEY": "text"
  KEY 为 xtbl <Name> 时计算 Volition CRC32 得到 hash;
  KEY 为 HASH_XXXXXXXX 时直接解析 16 进制。

Remastered 文本为裸 UTF-16LE，本工具直接按 UTF-16LE 编码写入（不做 charlist 重映射）。

用法:
  python sr3le_repack.py <原始le_strings目录> <翻译txt目录> <输出目录> [--inplace]
  对每个 txt 文件, 找到同名 .le_strings, 将翻译回写。

模式:
  默认 全量重建(repack): 保留 bucket 分配, 桶内按 hash 排序重排, 允许更长文本, 容忍多余 key。
  --inplace: 原位覆盖, 保持原文件布局完全不变, 但要求每条新文本 <= 原槽长(否则报错)。
             注: SR3 Remastered 历史上全量重排曾疑似引发 v3 崩溃, 若求最稳可改用 --inplace。
"""
import os
import re
import sys
import glob
import struct  # noqa: F401 (le_strings_repack 内部使用)

# 允许直接 import 同目录下的 le_strings_repack
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import le_strings_repack as lr


# ── Volition CRC32 ──────────────────────────────────────────────
CRC_TABLE = [0] * 256
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (0xEDB88320 ^ (_c >> 1)) if (_c & 1) else (_c >> 1)
    CRC_TABLE[_i] = _c & 0xFFFFFFFF


def crc_volition(s: str) -> int:
    """Volition CRC32: 初始 0, 无 final xor, 输入先小写化 (取各 char 低 8 位字节)。"""
    crc = 0
    for ch in s.lower():
        b = ord(ch) & 0xFF
        crc = CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFFFFFF


LINE_RE = re.compile(r'^"(.+?)":\s*"(.*)"\s*$')


def parse_txt(path):
    """解析 sr3le_extract.py 输出的 txt, 返回 {hash: text_str}。"""
    pairs = {}
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\r\n')
            if not line:
                continue
            m = LINE_RE.match(line)
            if not m:
                continue
            key = m.group(1)
            text = m.group(2)
            text = text.replace('\\n', '\n').replace('\\r', '\r') \
                       .replace('\\"', '"').replace('\\\\', '\\')
            if key.startswith('HASH_'):
                h = int(key[5:], 16)
            else:
                h = crc_volition(key)
            pairs[h] = text
    return pairs


def encode(text):
    """文本 -> UTF-16LE 字节 + 终止符 0x0000 (le_strings_repack 期望的格式)。"""
    return text.encode('utf-16-le') + b'\x00\x00'


def main():
    args = sys.argv[1:]
    if len(args) < 3:
        print(__doc__)
        return 1
    le_dir, txt_dir, out_dir = args[0], args[1], args[2]
    inplace = '--inplace' in args
    os.makedirs(out_dir, exist_ok=True)

    le_files = sorted(glob.glob(os.path.join(le_dir, '*.le_strings')))
    if not le_files:
        print('未找到 .le_strings: ' + le_dir)
        return 1

    total_files = total_pairs = 0
    fails = []
    for le in le_files:
        base = os.path.basename(le)
        stem = base[:-len('.le_strings')]
        txt_path = os.path.join(txt_dir, stem + '.txt')
        if not os.path.exists(txt_path):
            continue
        try:
            pairs_str = parse_txt(txt_path)
        except Exception as e:
            fails.append((base, 'txt解析: ' + str(e)))
            continue
        pairs = {h: encode(t) for h, t in pairs_str.items()}
        out_path = os.path.join(out_dir, base)
        try:
            if inplace:
                lr.repack_inplace(le, pairs, out_path)
            else:
                lr.repack(le, pairs, out_path)
        except Exception as e:
            fails.append((base, 'repack: ' + str(e)))
            continue
        total_files += 1
        total_pairs += len(pairs_str)
        print('%-34s OK (%d 条)' % (base, len(pairs_str)))

    print('-' * 50)
    print('完成: %d 文件, %d 条翻译%s'
          % (total_files, total_pairs, ' [inplace]' if inplace else ' [全量重建]'))
    if fails:
        print('失败:')
        for b, e in fails:
            print('  %s: %s' % (b, e))
    return 0


if __name__ == '__main__':
    sys.exit(main())
