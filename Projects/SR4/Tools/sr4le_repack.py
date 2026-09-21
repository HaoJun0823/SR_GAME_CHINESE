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
  python sr4le_repack.py <原始le_strings目录> <翻译txt目录> <输出目录> [--inplace]
  对每个 txt 文件, 找到同名 .le_strings, 将翻译回写为 .le_strings

模式:
  默认 全量重建(repack): 保留 bucket 分配, 桶内按 hash 排序重排, 允许更长文本。
  --inplace: 原位覆盖, 保持原文件布局完全不变, 但要求每条新文本 <= 原槽长(否则报错)。

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


def encode_text_with_charmap(text, rev_charmap, step=2):
    """
    将解码后的文本反向映射回原始字符, 再按指定步长编码 + NUL。

    ★★ 2026-09-20 加入 step 参数 (2=UTF-16LE / 4=UTF-32LE):
       microsoft 商店版的 le_strings 字符串载荷是 **UTF-32LE (每码位 4 字节)**,
       其余构建是 UTF-16LE (每码位 2 字节)。若写回时不按原编码, 新旧混编
       -> 引擎按错的步长读到 0 字节即截断 -> 界面显示单字母/空白。
    """
    chars = []
    for ch in text:
        cp = ord(ch)
        if cp in rev_charmap:
            chars.append(rev_charmap[cp])
        else:
            chars.append(cp)
    if step == 4:
        return struct.pack(f'<{len(chars)}I', *chars) + b'\x00\x00\x00\x00'
    return struct.pack(f'<{len(chars)}H', *chars) + b'\x00\x00'


# ── 字符串编码步长探测 ──────────────────────────────────────────

def detect_text_step(buf, nb, buckets):
    """探测字符串载荷步长: 2 (UTF-16LE) 或 4 (UTF-32LE)。

    ★★ 判据 (由强到弱, 取第一个能给出结论的):

    A) **结构判据 (首选, 对单条目/短表也有效)**
       UTF-32LE 下, 每个码位占 4 字节且高 2 字节恒为 0;
       于是从载荷起点 +4 开始, 每 4 字节组的第 2、3 字节应为 0。
       UTF-16LE 下, 相邻码位的字节是 码元低/高 交替, 不会系统性为 0。
       做法: 逐条目累积「偏移 (4k+2, 4k+3) 位置上的非零字节数」与
             「偏移 (4k, 4k+1) 位置上的非零字节数」;
       若非零字节**全部**落在 (4k, 4k+1) 组内 (奇数半字位置全零) => UTF-32。

    B) **长度判据 (兜底)**
       按 2B 读几乎全是单字符、按 4B 读几乎全是多字符 => UTF-32。
    """
    starts = []
    for (off, cnt) in buckets:
        for j in range(cnt):
            so = off + j * 8
            if so + 4 > len(buf):
                continue
            a = struct.unpack_from('<I', buf, so)[0]
            if a and a + 4 <= len(buf):
                starts.append(a)
    starts = sorted(set(starts))
    if not starts:
        return 2
    nxt = {}
    for i, a in enumerate(starts):
        nxt[a] = starts[i + 1] if i + 1 < len(starts) else len(buf)

    # ── A) 结构判据 ────────────────────────────────────────────
    #   ★ 注意方向性: 「某一半字组全零」两种解释都可能成立 ——
    #       U32: 高 2B 恒 0 -> hi_nz==0 (lo 组内有内容)
    #       但纯 CJK 文本按 U16 存放时, 汉字低字节常为 0, 也可能出现 **lo_nz==0**
    #       的假象 (如 "黑" = 0x9ED1 -> 字节 D1 9E, 落在 hi 组)。
    #     => 只有「hi_nz==0 且 lo_nz 有明显量」才判 U32;
    #        「lo_nz==0 而 hi_nz 有量」不足以单独下结论 (交由 A2/B 复核)。
    lo_nz = 0      # 偶数半字位置 (4k, 4k+1) 上的非零字节
    hi_nz = 0      # 奇数半字位置 (4k+2, 4k+3) 上的非零字节
    for a in starts[:120]:
        pay = buf[a + 4:min(nxt[a], a + 4 + 256)]
        for k, c in enumerate(pay):
            if c:
                if (k % 4) < 2:
                    lo_nz += 1
                else:
                    hi_nz += 1
    if lo_nz and hi_nz == 0:
        return 4

    # ── A2) 载荷内部结构判据 (比 A 更准, 用于 A 无法定论时) ──────
    #   思路: 在**本条文本自身**范围内 (即到首个 u16 NUL 为止), 检查每个
    #   2 字节单元的「高半字是否恒为 0」。
    #     UTF-32: 每个 4 字节码位 = {lo16, 00 00}, 故文本区内**所有**奇数
    #             半字 (pay[2], pay[6], ...) 均为 0;
    #             且 NUL 之前最后一个码位的 lo16 非零。
    #     UTF-16: 文本区内奇数半字是码位的**高字节**, CJK 下非零 (如 ：=FF1A),
    #             不会全零。
    #   ★ 为什么不用「NUL 后还剩几字节」: 原位覆盖会把短译文的剩余槽位清零,
    #     使 NUL 之后**既可能**跟 2B 残留 **也可能**跟真正的 4B 终止符, 不可分。
    u32_votes = u16_votes = 0
    for a in starts[:120]:
        pay = buf[a + 4:nxt.get(a, len(buf))]
        # 只取到首个 u16 NUL 为止的文本本体 (不含任何残留/终止填充)
        k = 0
        while k + 1 < len(pay) and struct.unpack_from('<H', pay, k)[0] != 0:
            k += 2
        body = pay[:k]
        if len(body) < 4:
            continue
        # 结构化检查: 每个 4B 组的后 2B 是否为 0 (即奇数半字全零)
        odd_all_zero = all(struct.unpack_from('<H', body, m + 2)[0] == 0
                           for m in range(0, len(body) - 3, 4))
        if odd_all_zero and len(body) % 4 == 0:
            u32_votes += 1
        else:
            u16_votes += 1
    if u32_votes and u16_votes == 0:
        return 4
    if u16_votes and u32_votes == 0:
        return 2

    # ── B) 长度判据 ────────────────────────────────────────────
    n = len16one = len32gt1 = 0
    for a in starts[:60]:
        pay = buf[a + 4:nxt[a]]
        e = 0
        while e + 1 < len(pay) and struct.unpack_from('<H', pay, e)[0] != 0:
            e += 2
        l16 = e // 2
        m = 0
        while m + 3 < len(pay) and struct.unpack_from('<I', pay, m)[0] != 0:
            m += 4
        l32 = m // 4
        n += 1
        if l16 <= 1:
            len16one += 1
        if l32 > 1:
            len32gt1 += 1
    if n and len16one / n > 0.8 and len32gt1 / n > 0.8:
        return 4
    return 2


def detect_file_step(path):
    """★ 对外便捷入口: 探测某个 le_strings 文件的载荷步长 (2/4)。

    txt -> le_strings 之前**必须先调用本函数知道目标模板的编码**,
    再据此把译文按相同步长写入 —— 否则会出现「按 UTF-16 往 UTF-32 模板写」
    这类静默损坏 (每条只剩 1 字符 / 产物大幅缩水)。

    返回 2 (UTF-16LE) 或 4 (UTF-32LE)。
    """
    buf = open(path, 'rb').read()
    if len(buf) < 12:
        raise ValueError(f'{path}: 文件过小, 不是合法 le_strings')
    fid, _ver, nb, _nstr = struct.unpack_from('<IHHI', buf, 0)
    if fid != 0xA84C7F73:
        raise ValueError(f'{path}: 非 le_strings (ID=0x{fid:08X})')
    buckets = []
    for i in range(nb):
        base = 12 + i * 16
        if base + 16 > len(buf):
            raise ValueError(f'{path}: bucket[{i}] 表越界')
        count, _, offset, _ = struct.unpack_from('<IIII', buf, base)
        buckets.append((offset, count))
    return detect_text_step(buf, nb, buckets)


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
      fid, ver, nb, nstr, [(bucket_idx, hash, text_bytes, s_off, slot_len)], buf, step

    text_bytes 含末尾 NUL 终止符 (UTF-16 为 0x0000 / UTF-32 为 0x00000000);
    s_off 为该条目在文件中的绝对偏移;
    slot_len 为原文本区长度 (含终止符);
    step 为载荷步长 (2=UTF-16LE / 4=UTF-32LE) —— 由内容自动探测。
    """
    buf = open(path, 'rb').read()
    if len(buf) < 12:
        raise ValueError(f'{path}: 文件过小')
    fid, ver, nb, nstr = struct.unpack_from('<IHHI', buf, 0)
    if fid != 0xA84C7F73:
        raise ValueError(f'{path}: 非 le_strings (ID=0x{fid:08X})')

    # 先收集 bucket, 以便探测步长
    buckets = []
    for i in range(nb):
        base = 12 + i * 16
        if base + 16 > len(buf):
            raise ValueError(f'{path}: bucket[{i}] 表越界')
        count, _, offset, _ = struct.unpack_from('<IIII', buf, base)
        buckets.append((offset, count))

    step = detect_text_step(buf, nb, buckets)
    term = 2 if step == 2 else 4

    entries = []
    total_count = 0
    for i in range(nb):
        base = 12 + i * 16
        count, _, offset, _ = struct.unpack_from('<IIII', buf, base)
        total_count += count
        for j in range(count):
            so_off = offset + j * 8      # 8 字节步长
            if so_off + 4 > len(buf):
                raise ValueError(f'{path}: bucket[{i}] offset 表越界 @0x{so_off:X}')
            s_off = struct.unpack_from('<I', buf, so_off)[0]
            if s_off == 0:
                entries.append((i, 0, b'', 0, 0))  # 空槽
                continue
            if s_off + 4 > len(buf):
                raise ValueError(f'{path}: bucket[{i}] 字符串偏移越界 @0x{s_off:X}')
            h = struct.unpack_from('<I', buf, s_off)[0]
            # 找文本终点 (按 step 步长读到全零单元)
            e = s_off + 4
            fmt = '<H' if term == 2 else '<I'
            while e + term - 1 < len(buf) and \
                    struct.unpack_from(fmt, buf, e)[0] != 0:
                e += term
            text_bytes = buf[s_off + 4: e + term]   # 含末尾 NUL
            slot_len = (e + term) - (s_off + 4)
            entries.append((i, h, text_bytes, s_off, slot_len))
    # ★★ 一致性校验 (2026-09-20 事故防护): 桶条目总数必须等于 header.stringCount,
    #    否则说明解析步长/结构判断出错, 直接拒绝继续, 避免静默产出坏文件。
    if total_count != nstr:
        raise ValueError(
            f'{path}: bucket 条目总数 {total_count} != header.stringCount {nstr} (文件不一致)')
    if len(entries) != nstr:
        raise ValueError(f'{path}: 解析出 {len(entries)} 条 != nstr {nstr}')
    return fid, ver, nb, nstr, entries, buf, step


# ── 模式 1: 全量重建 repack ─────────────────────────────────────

def repack(in_path, pairs, out_path, charmap=None):
    """
    全量重建 le_strings: 保留原文件 bucket 分配, 桶内按 hash 排序,
    offset 表使用 8 字节步长 (u32 offset + u32 pad0)。
    pairs: {hash: text_str}
    charmap: 原始 charlist 映射 (用于反向映射文本字符)
    缺失的 hash 沿用原文本 (**按原文件编码原样拷贝**, 不重编码)。

    ★★ 载荷步长按原文件自动探测 (2=UTF-16LE / 4=UTF-32LE), 新译文按同一步长写入,
       以保证新旧条目编码一致 —— 否则引擎按单一步长读会立刻截断。
    """
    rev_charmap = build_reverse_charmap(charmap) if charmap else {}
    pairs = dict(pairs)
    fid, ver, nb, nstr, entries, _, step = parse_le_strings_raw(in_path)

    # 应用覆盖
    out_entries = []
    for b, h, t_bytes in [(b, h, t) for b, h, t, _, _ in entries]:
        if h == 0:
            out_entries.append((b, 0, b''))
        elif h in pairs:
            new_text = pairs.pop(h)
            new_bytes = encode_text_with_charmap(new_text, rev_charmap, step)
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

    # ★★ 写后自检 (强制): 条目数与 offset 表长度必须与 header 声明一致,
    #    否则引擎按声明索引会越界取到 NULL 指针并崩溃。
    written_total = sum(len(x) for x in buckets)
    if written_total != nstr:
        raise ValueError(
            f'{in_path}: 写回条目数 {written_total} != header.stringCount {nstr} —— 拒绝写出')
    if pos != 12 + 16 * nb + 8 * nstr:
        raise ValueError(
            f'{in_path}: offset 表写入长度 {pos - (12 + 16 * nb)} != 8*{nstr} —— 拒绝写出')

    open(out_path, 'wb').write(bytes(out))

    # ★★ 回读校验 (强制): 用本模块的读路径重新解析产物, 必须读回 nstr 条。
    _r = parse_le_strings_raw(out_path)
    back = _r[4]
    if len(back) != nstr:
        raise ValueError(f'{out_path}: 回读校验失败, 只读回 {len(back)}/{nstr} 条')
    return nstr, len(pairs)  # 返回总数和未匹配数


# ── 模式 2: 原位覆盖 repack_inplace ─────────────────────────────

def repack_inplace(in_path, pairs, out_path, charmap=None):
    """
    原位覆盖: 保持原文件布局完全不变, 只替换文本内容。
    新文本 (含终止 0x0000) 必须 <= 原槽位长度, 否则报错。
    charmap: 原始 charlist 映射 (用于反向映射文本字符)
    """
    rev_charmap = build_reverse_charmap(charmap) if charmap else {}
    fid, ver, nb, nstr, entries, buf, step = parse_le_strings_raw(in_path)
    buf = bytearray(buf)
    pairs = dict(pairs)
    replaced = {}

    for b, h, t_bytes, s_off, slot_len in entries:
        if h == 0 or h not in pairs:
            continue
        new_text = pairs.pop(h)
        new_bytes = encode_text_with_charmap(new_text, rev_charmap, step)
        if len(new_bytes) > slot_len:
            raise ValueError(
                f'hash 0x{h:08X}: 新文本 {len(new_bytes)}B > 原槽 {slot_len}B, 无法原位覆盖'
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


# ── 自检 ────────────────────────────────────────────────────────

def _make_sample(step, texts, hashes):
    """构造一个最小合法 le_strings; step ∈ {2,4} 决定载荷编码。"""
    nb, nstr = 1, len(texts) + 1     # +1 为第一个空槽
    head = 12 + 16 * nb + 8 * nstr
    start = (head + 3) & ~3
    buf = bytearray(start)
    struct.pack_into('<IHHI', buf, 0, 0xA84C7F73, 1, nb, nstr)
    bucket_offsets = [0]             # 第一个是空槽
    cur = start
    term = b'\x00\x00' if step == 2 else b'\x00\x00\x00\x00'
    for text, h in zip(texts, hashes):
        while cur & 3:
            cur += 1
            buf.append(0)
        bucket_offsets.append(cur)
        if step == 2:
            pay = text.encode('utf-16-le')
        else:
            pay = struct.pack(f'<{len(text)}I', *[ord(c) for c in text])
        buf += struct.pack('<I', h) + pay + term
        cur = len(buf)
    struct.pack_into('<IIII', buf, 12, nstr, 0, 12 + 16 * nb, 0)
    pos = 12 + 16 * nb
    for off in bucket_offsets:
        struct.pack_into('<II', buf, pos, off, 0)
        pos += 8
    return bytes(buf), nstr


def self_test():
    """自检: 对内置最小样例做 round-trip, 验证读/写在 8 字节 offset 表下自洽。

    用于 CI / 自动化构建前置校验, 不依赖任何游戏文件。
    ★★ 2026-09-20: 增加 UTF-32LE 样例 (microsoft 商店版格式) —— 旧版只测 UTF-16,
       导致商店版的 4B/字符 载荷被静默截断成单字符而无人察觉。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for step in (2, 4):
            src = os.path.join(td, f't{step}_us.le_strings')
            dst = os.path.join(td, f't{step}_zh.le_strings')
            blob, nstr = _make_sample(step, ('HELLO', 'WORLD'),
                                      (0x11111111, 0x22222222))
            open(src, 'wb').write(blob)

            r = parse_le_strings_raw(src)
            got, entries, det_step = r[3], r[4], r[6]
            assert got == nstr, f'self_test(step={step}): 读出 {got} != {nstr}'
            assert det_step == step, \
                f'self_test: 步长探测 {det_step} != 期望 {step} (UTF-{step * 8})'
            assert sum(1 for e in entries if e[1] == 0) == 1, \
                'self_test: 空槽数量不对'
            # 未命中译文的条目必须原样保留 (round-trip 保指纹)
            kept = [e for e in entries if e[1] == 0x11111111][0]
            if step == 4:
                expect = struct.pack('<5I', *[ord(c) for c in 'HELLO'])
                assert kept[2][:20] == expect, 'self_test: UTF-32 条目未被原样保留'
                assert kept[4] == 20 + 4, f'self_test: UTF-32 槽长 {kept[4]} != 24'

            # 全量重建 (空覆盖 = 纯 round-trip) 与 原位覆盖 都必须保条目数
            repack(src, {}, dst)
            assert parse_le_strings_raw(dst)[3] == nstr, \
                f'self_test(repack, step={step}): 条目数不守恒'
            repack_inplace(src, {0x11111111: '你好'}, dst)
            rr = parse_le_strings_raw(dst)
            assert rr[3] == nstr, \
                f'self_test(repack_inplace, step={step}): 条目数不守恒'
            assert rr[6] == step, 'self_test: 产物步长被改变'
            back = [e for e in rr[4] if e[1] == 0x11111111][0][2]
            if step == 2:
                assert back == '你好'.encode('utf-16-le') + b'\x00\x00'
            else:
                assert back == struct.pack('<2I', ord('你'), ord('好')) + b'\x00' * 4
    print('[self_test] sr4le_repack OK: 8 字节 offset 表读/写自洽, '
          '条目数守恒, UTF-16/UTF-32 双步长均正确')


# ── 批量处理 ────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if len(args) < 3:
        print(__doc__)
        return 1

    le_dir, txt_dir, out_dir = args[0], args[1], args[2]
    inplace = '--inplace' in args
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
        charmap = parse_charlist(charfile) if os.path.exists(charfile) else None

        try:
            pairs = parse_txt(txt_path)
        except Exception as e:
            fail_list.append((base, f'txt解析: {e}'))
            continue

        out_path = os.path.join(out_dir, base)

        try:
            if inplace:
                replaced = repack_inplace(le, pairs, out_path, charmap=charmap)
                total_replaced += len(replaced)
                total_files += 1
                print(f'{base:34s}  原位覆盖 {len(replaced):5d} 条')
            else:
                nstr, unmatched = repack(le, pairs, out_path, charmap=charmap)
                total_replaced += nstr - unmatched
                total_missing += unmatched
                total_files += 1
                print(f'{base:34s}  条目={nstr:5d}  翻译={len(pairs):5d}  未匹配={unmatched:5d}')
        except Exception as e:
            fail_list.append((base, f'repack: {e}'))
            continue

    print('-' * 60)
    print(f'完成: {total_files}/{len(le_files)} 个文件, '
          f'翻译 {total_replaced} 条, 未匹配 {total_missing} 条'
          f'{" [原位覆盖]" if inplace else " [全量重建]"}')
    if fail_list:
        print('失败:')
        for b, e in fail_list:
            print(f'  {b}: {e}')
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 4:
        # 无参数: 仅运行自检, 便于自动化/CI 校验
        self_test()
        sys.exit(0)
    sys.exit(main())
