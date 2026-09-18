#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sr4le_repack.py — Saints Row IV .le_strings 回写工具
=====================================================
将翻译后的 txt 文件回写为 .le_strings 二进制格式。

le_strings 格式 (与 SR3R Remastered 完全一致):
  header 12B  : ID u32(0xA84C7F73) | version u16 | bucketCount u16 | stringCount u32
  bucket 16B  : count u32 | pad u32 | offset u32 | pad u32  (x bucketCount)
  offset 表   : 从 bucket.offset 起, count 个 8B 条目 = {字符串绝对偏移 u32, 填充零 u32}
  字符串条目  : { hash u32, utf16le 文本, u16 0 }

支持两种模式:
  1) repack (全量重建): 保留原文件 bucket 分配, 桶内按 hash 排序, 8B offset 表
  2) repack_inplace (原位覆盖): 保持原文件布局不变, 只替换文本内容

用法:
  python sr4le_repack.py <原始le_strings目录> <翻译txt目录> <输出目录>
  对每个 txt 文件, 找到同名 .le_strings, 将翻译回写为 .le_strings

txt 格式 (与 sr4le_extract.py 输出一致):
  "KEY": "text"
  KEY 为 xtbl <Name> 或 HASH_XXXXXXXX
"""

import os
import re
import sys
import struct
import collections

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


# ── charlist 反向映射 ──────────────────────────────────────────

def parse_charlist(path):
    """charlist_xx.dat -> decode map (与 sr4le_extract.py 一致)。"""
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


def build_reverse_charmap(charmap):
    """
    构建 charlist 反向映射: decoded_char -> original_char。
    extract 时 charmap[orig] -> decoded, repack 时需要 decoded -> orig。
    如果多个 orig 映射到同一个 decoded, 取第一个 (charlist 设计上不会冲突)。
    """
    rev = {}
    for orig, decoded in charmap.items():
        if decoded not in rev:
            rev[decoded] = orig
    return rev


def encode_text_with_charmap(text, rev_charmap):
    """
    将解码后的文本反向映射回原始字符, 再编码为 UTF-16LE + 0x0000。
    未在 rev_charmap 中的字符保持原样。
    """
    chars = []
    for ch in text:
        cp = ord(ch)
        if cp in rev_charmap:
            chars.append(rev_charmap[cp])
        else:
            chars.append(cp)
    # 编码为 UTF-16LE
    return struct.pack(f'<{len(chars)}H', *chars) + b'\x00\x00'


# ── txt 解析 ────────────────────────────────────────────────────

def parse_txt(path):
    """
    解析 sr4le_extract.py 输出的 txt 文件。
    返回 {hash: text_str} 字典。
    KEY 为 xtbl <Name> 时计算 CRC32 得到 hash;
    KEY 为 HASH_XXXXXXXX 时直接解析。
    """
    pairs = {}
    line_re = re.compile(r'^"(.+?)":\s*"(.*)"\s*$')

    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\r\n')
            if not line:
                continue
            m = line_re.match(line)
            if not m:
                continue
            key = m.group(1)
            # 反转义
            text = m.group(2)
            text = text.replace('\\n', '\n').replace('\\r', '\r') \
                       .replace('\\"', '"').replace('\\\\', '\\')

            if key.startswith('HASH_'):
                h = int(key[5:], 16)
            else:
                h = crc_volition(key)
            pairs[h] = text
    return pairs


# ── le_strings 解析 ─────────────────────────────────────────────

def parse_le_strings_raw(path):
    """
    解析 le_strings 原始文件, 返回:
      fid, ver, nb, nstr, [(bucket_idx, hash, text_bytes, s_off, slot_len)]
    text_bytes 含末尾 0x0000; s_off 为该条目在文件中的绝对偏移;
    slot_len 为原文本区长度 (含终止 0x0000)。
    """
    buf = open(path, 'rb').read()
    fid, ver, nb, nstr = struct.unpack_from('<IHHI', buf, 0)
    if fid != 0xA84C7F73:
        raise ValueError(f'{path}: 非 le_strings (ID=0x{fid:08X})')

    entries = []
    for i in range(nb):
        base = 12 + i * 16
        count, _, offset, _ = struct.unpack_from('<IIII', buf, base)
        for j in range(count):
            so_off = offset + j * 8      # 8 字节步长
            s_off = struct.unpack_from('<I', buf, so_off)[0]
            if s_off == 0:
                entries.append((i, 0, b'', 0, 0))  # 空槽
                continue
            h = struct.unpack_from('<I', buf, s_off)[0]
            # 找文本终点 (u16 0)
            e = s_off + 4
            while e + 1 < len(buf) and struct.unpack_from('<H', buf, e)[0] != 0:
                e += 2
            text_bytes = buf[s_off + 4: e + 2]   # 含末尾 0x0000
            slot_len = (e + 2) - (s_off + 4)
            entries.append((i, h, text_bytes, s_off, slot_len))
    return fid, ver, nb, nstr, entries, buf


# ── 模式 1: 全量重建 repack ─────────────────────────────────────

def repack(in_path, pairs, out_path, charmap=None):
    """
    全量重建 le_strings: 保留原文件 bucket 分配, 桶内按 hash 排序,
    offset 表使用 8 字节步长 (u32 offset + u32 pad0)。
    pairs: {hash: text_str}  (text_str 为 Python str, 将编码为 UTF-16LE + 0x0000)
    charmap: 原始 charlist 映射 (用于反向映射文本字符)
    缺失的 hash 沿用原文本。
    """
    rev_charmap = build_reverse_charmap(charmap) if charmap else {}
    pairs = dict(pairs)
    fid, ver, nb, nstr, entries, _ = parse_le_strings_raw(in_path)

    # 应用覆盖
    out_entries = []
    for b, h, t_bytes in [(b, h, t) for b, h, t, _, _ in entries]:
        if h == 0:
            out_entries.append((b, 0, b''))
        elif h in pairs:
            new_text = pairs.pop(h)
            new_bytes = encode_text_with_charmap(new_text, rev_charmap)
            out_entries.append((b, h, new_bytes))
        else:
            out_entries.append((b, h, t_bytes))

    # 每桶内按 hash 升序 (空槽排最前)
    buckets = [[] for _ in range(nb)]
    for b, h, t in out_entries:
        buckets[b].append((h, t))
    for b in range(nb):
        buckets[b].sort(key=lambda x: x[0])

    # 计算布局
    # 头部: 12B header + 16B*nb buckets + 8B*nstr offset 表
    head_size = 12 + 16 * nb + 8 * nstr
    string_start = (head_size + 3) & ~3   # 4 字节对齐
    out = bytearray(string_start)
    struct.pack_into('<IHHI', out, 0, fid, ver, nb, nstr)

    # 写字符串区 + 记录每个条目的绝对偏移
    cur = string_start
    bucket_offsets = [[] for _ in range(nb)]
    for b in range(nb):
        for h, t in buckets[b]:
            if h == 0:
                bucket_offsets[b].append(0)   # 空槽: offset=0
                continue
            # 4 字节对齐
            while cur & 3:
                cur += 1
                out.append(0)
            bucket_offsets[b].append(cur)
            out += struct.pack('<I', h) + t
            cur = len(out)

    # 写 bucket header (count + offset 表起点)
    for b in range(nb):
        bucket_header = 12 + b * 16
        # offset 表起点 = 12 + 16*nb + 8 * (之前所有桶的条目数)
        off_table_start = 12 + 16 * nb + 8 * sum(len(buckets[j]) for j in range(b))
        struct.pack_into('<IIII', out, bucket_header, len(buckets[b]), 0, off_table_start, 0)

    # 写 offset 表 (8 字节步长: u32 offset + u32 pad0)
    pos = 12 + 16 * nb
    for b in range(nb):
        for off in bucket_offsets[b]:
            struct.pack_into('<II', out, pos, off, 0)
            pos += 8

    open(out_path, 'wb').write(bytes(out))
    return nstr, len(pairs)  # 返回总数和未匹配数


# ── 模式 2: 原位覆盖 repack_inplace ─────────────────────────────

def repack_inplace(in_path, pairs, out_path, charmap=None):
    """
    原位覆盖: 保持原文件布局完全不变, 只替换文本内容。
    新文本 (含终止 0x0000) 必须 <= 原槽位长度, 否则报错。
    charmap: 原始 charlist 映射 (用于反向映射文本字符)
    """
    rev_charmap = build_reverse_charmap(charmap) if charmap else {}
    fid, ver, nb, nstr, entries, buf = parse_le_strings_raw(in_path)
    buf = bytearray(buf)
    pairs = dict(pairs)
    replaced = {}

    for b, h, t_bytes, s_off, slot_len in entries:
        if h == 0 or h not in pairs:
            continue
        new_text = pairs.pop(h)
        new_bytes = encode_text_with_charmap(new_text, rev_charmap)
        if len(new_bytes) > slot_len:
            raise ValueError(
                f'hash 0x{h:08X}: 新文本 {len(new_bytes)}B > 原槽 {slot_len}B, '
                f'无法原位覆盖 (文件={in_path})'
            )
        # 写 {hash} 不变, 覆盖文本并清零剩余
        buf[s_off + 4: s_off + 4 + len(new_bytes)] = new_bytes
        buf[s_off + 4 + len(new_bytes): s_off + 4 + slot_len] = b'\x00' * (
            slot_len - len(new_bytes))
        replaced[h] = (s_off, slot_len, len(new_bytes))

    if pairs:
        miss = ', '.join(f'0x{h:08X}' for h in list(pairs)[:10])
        if len(pairs) > 10:
            miss += f' ... ({len(pairs)} total)'
        print(f'  警告: {len(pairs)} 个 hash 在原文件中不存在: {miss}')

    open(out_path, 'wb').write(bytes(buf))
    return replaced


# ── 批量处理 ────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 1

    le_dir, txt_dir, out_dir = sys.argv[1:4]
    os.makedirs(out_dir, exist_ok=True)

    import glob
    le_files = sorted(glob.glob(os.path.join(le_dir, '*.le_strings')))
    if not le_files:
        print(f'未找到 .le_strings: {le_dir}')
        return 1

    total_replaced = 0
    total_missing = 0
    total_files = 0
    fail_list = []

    for le in le_files:
        base = os.path.basename(le)
        stem = base[:-len('.le_strings')]
        txt_path = os.path.join(txt_dir, stem + '.txt')

        if not os.path.exists(txt_path):
            continue   # 无翻译文件, 跳过

        # 加载 charlist (与 extract 一致)
        lang = stem.rsplit('_', 1)[-1] if '_' in stem else 'us'
        charfile = os.path.join(le_dir, f'charlist_{lang}.dat')
        if not os.path.exists(charfile):
            charfile = os.path.join(le_dir, 'charlist_us.dat')
        charmap = parse_charlist(charfile) if os.path.exists(charfile) else {}

        try:
            pairs = parse_txt(txt_path)
        except Exception as e:
            fail_list.append((base, f'txt解析: {e}'))
            continue

        out_path = os.path.join(out_dir, base)

        try:
            nstr, unmatched = repack(le, pairs, out_path, charmap=charmap)
        except Exception as e:
            fail_list.append((base, f'repack: {e}'))
            continue

        replaced = nstr - unmatched
        total_replaced += replaced
        total_missing += unmatched
        total_files += 1
        print(f'{base:34s}  条目={nstr:5d}  翻译={len(pairs):5d}  未匹配={unmatched:5d}')

    print('-' * 60)
    print(f'完成: {total_files}/{len(le_files)} 个文件, '
          f'翻译 {total_replaced} 条, 未匹配 {total_missing} 条')
    if fail_list:
        print('失败:')
        for b, e in fail_list:
            print(f'  {b}: {e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
