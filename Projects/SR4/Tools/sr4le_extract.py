#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sr4le_extract.py — Saints Row IV (Re-Elected / EOS 2024+) .le_strings 解包工具
=================================================================================
SR4 的 le_strings 格式与 SR3R Remastered 完全一致：
  - header 12B  : ID u32(0xA84C7F73) | version u16 | bucketCount u16 | stringCount u32
  - bucket 16B  : count u32 | pad u32 | offset u32 | pad u32  (x bucketCount)
  - offset 表   : 从 bucket.offset 起, count 个 8B 条目 = {字符串绝对偏移 u32, 填充零 u32}
  - 字符串条目  : { hash u32, utf16le 文本, u16 0 }

用法:
  python sr4le_extract.py <misc目录> <misc_tables目录> <输出目录>
  将 misc 目录下所有 *.le_strings 解包为 <输出目录>/<同名>.txt

输出 txt 格式 (与 2013 ExtractStrings 一致):
  "KEY": "text"
  KEY 优先为 xtbl <Name> 反查结果，否则为 "HASH_XXXXXXXX"。
"""

import os
import re
import sys
import glob
import struct
import collections

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


def parse_charlist(path):
    """charlist_xx.dat -> decode map。
    规则(来自 ThomasJepp LanguageUtility)：
      数值行 v <= 0x100  -> identity (自身映射)
      数值行 v >  0x100  -> 槽位 0x100+k 映射到 v (k 递增)
    """
    mapping = {}
    next_slot = 0x100
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for raw in f:
            line = raw.strip()
            if line.startswith('//') or line.startswith('count=') or not line:
                continue
            try:
                v = int(line)
            except ValueError:
                continue
            if v > 0x100:
                mapping[next_slot] = v
                next_slot += 1
            else:
                mapping[v] = v
    return mapping


def decode_text(buf, start, end, charmap):
    """从 UTF-16LE 字节还原字符串并过字符映射。"""
    out = []
    for off in range(start, end, 2):
        ch = struct.unpack_from('<H', buf, off)[0]
        out.append(ch)
    return ''.join(chr(charmap.get(c, c)) for c in out)


def parse_le_strings(path, charmap):
    """
    解析 SR4 le_strings (与 SR3R Remastered 布局一致)。
    布局:
      header 12B  : ID u32 | version u16 | bucketCount u16 | stringCount u32
      bucket 16B  : count u32 | pad u32 | offset u32 | pad u32    (x bucketCount)
      offset 表   : 从 bucket.offset 起, count 个 8B 条目, 每个 = {字符串绝对偏移 u32, 填充零 u32}
      字符串条目  : { hash u32, utf16le 文本, u16 0 }
    返回 [(hash, text), ...] 按 bucket 顺序 (每 bucket 内按 offset 表顺序)。
    """
    buf = open(path, 'rb').read()
    fid, ver, nbuckets, nstrings = struct.unpack_from('<IHHI', buf, 0)
    if fid != 0xA84C7F73:
        raise ValueError(f'{path}: 非 le_strings 文件 (ID=0x{fid:08X})')

    entries = []          # [(hash, text)]
    per_bucket = []       # [(count, offset, pad_ok)]
    dup_stats = collections.Counter()
    skipped = 0

    for i in range(nbuckets):
        base = 12 + i * 16
        if base + 16 > len(buf):
            raise ValueError(f'{path}: bucket[{i}] 越界')
        count, pad1, offset, pad2 = struct.unpack_from('<IIII', buf, base)
        per_bucket.append((count, offset))
        # 校验 offset 表与文本区间 (8 字节步长: u32 字符串偏移 + u32 填充零)
        for j in range(count):
            so_off = offset + j * 8
            if so_off + 4 > len(buf):
                raise ValueError(f'{path}: bucket[{i}] offset表 越界 @0x{so_off:X}')
            s_off = struct.unpack_from('<I', buf, so_off)[0]
            if s_off == 0:
                # 空槽: offset=0 表示该条目无文本 (游戏内视为空/无效), 跳过
                skipped += 1
                continue
            if s_off + 4 > len(buf):
                raise ValueError(f'{path}: bucket[{i}] 字符串偏移越界 @0x{s_off:X}')
            h = struct.unpack_from('<I', buf, s_off)[0]
            # 找文本终点 (u16 0)
            e = s_off + 4
            while e + 1 < len(buf) and struct.unpack_from('<H', buf, e)[0] != 0:
                e += 2
            text = decode_text(buf, s_off + 4, e, charmap)
            entries.append((h, text))
            dup_stats[h] += 1

    real_total = sum(c for c, _ in per_bucket)
    if real_total != nstrings:
        # 仅提示：header stringCount 是"参考值"，以 bucket 实际为准
        pass
    return entries, nbuckets, nstrings, real_total, skipped, dup_stats


def load_hash_names(misc_tables_dir):
    """扫描 misc_tables/*.xtbl 的 <Name> 标签 -> {crc32: name}。"""
    name_hash = {}
    pat = re.compile(r'<Name>(.*?)</Name>', re.S)
    for xtbl in glob.glob(os.path.join(misc_tables_dir, '*.xtbl')):
        try:
            txt = open(xtbl, 'r', encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        for m in pat.finditer(txt):
            nm = m.group(1).strip()
            if not nm:
                continue
            h = crc_volition(nm)
            if h not in name_hash:
                name_hash[h] = nm
    return name_hash


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 1
    misc_dir, tables_dir, out_dir = sys.argv[1:4]
    os.makedirs(out_dir, exist_ok=True)

    name_hash = load_hash_names(tables_dir)
    print(f'XTBL 键名表: {len(name_hash)} 个 <Name>')

    le_files = sorted(glob.glob(os.path.join(misc_dir, '*.le_strings')))
    print(f'发现 {len(le_files)} 个 .le_strings')
    if not le_files:
        return 1

    fail_list = []
    grand = collections.Counter()
    for le in le_files:
        base = os.path.basename(le)
        stem = base[:-len('.le_strings')]
        lang = stem.rsplit('_', 1)[-1] if '_' in stem else 'us'
        charfile = os.path.join(misc_dir, f'charlist_{lang}.dat')
        if not os.path.exists(charfile):
            charfile = os.path.join(misc_dir, 'charlist_us.dat')
        charmap = parse_charlist(charfile) if os.path.exists(charfile) else {}

        try:
            entries, nbuckets, nstrings, real, skipped, dup_stats = parse_le_strings(le, charmap)
        except (ValueError, struct.error) as e:
            fail_list.append((base, str(e)))
            continue

        out_name = os.path.join(out_dir, stem + '.txt')
        named = 0
        with open(out_name, 'w', encoding='utf-8', newline='\r\n') as f:
            for h, text in entries:
                nm = name_hash.get(h)
                if nm:
                    key = nm
                    named += 1
                else:
                    key = f'HASH_{h:08X}'
                text_out = text.replace('\\', '\\\\').replace('"', '\\"') \
                               .replace('\r', '\\r').replace('\n', '\\n')
                f.write(f'"{key}": "{text_out}"\r\n')

        dups = sum(n - 1 for n in dup_stats.values() if n > 1)
        grand['files'] += 1
        grand['strings'] += real
        grand['named'] += named
        grand['dups'] += dups
        grand['skipped'] += skipped
        status = f'OK  strings={real:5d}  named={named:4d}  skip={skipped:4d}'
        if dups:
            status += f'  DUP={dups}'
        print(f'{base:34s} {status}')

    print('-' * 60)
    print(f'完成: {grand["files"]}/{len(le_files)} 个文件, 共 {grand["strings"]} 条字符串, '
          f'反查到名字 {grand["named"]} 条, 重复条目 {grand["dups"]} 条, '
          f'空槽跳过 {grand["skipped"]} 条')
    if fail_list:
        print('失败文件:')
        for b, e in fail_list:
            print(f'  {b}: {e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
