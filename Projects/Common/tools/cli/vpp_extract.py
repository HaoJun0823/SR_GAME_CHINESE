#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vpp_extract.py — Volition VPP 解包器（v6 / v10 双格式合一）

取代原 `Projects/SR3/Tools/vpp_extract_all.py` 与 `Projects/SR4/Tools/sr4_vpp.py`。

★ 与 vpp_pack.py 同一原则：**合一外壳，两套块格式实现各自独立**。
  两代格式的结构差异见 vpp_pack.py 模块头。

════════════════════════════════════════════════════════════════════════════
物理布局的差异（解包时最容易错的地方）
════════════════════════════════════════════════════════════════════════════
  v6 (Version06-x64)
    · 数据区起点按 0x1000 chunk 对齐：data_off = CHUNK*(1 + dir_chunks + name_chunks)
    · 每个文件块 = [16B 头 {0x0FEEDBEE, 0x00BADBEE, lz4len, usz}] + LZ4 数据
    · 文件之间按 csz 的 0x1000 对齐累积；**最后一个文件不 pad**
    · 未压缩特例：csz == 0xFFFFFFFFFFFFFFFF → 原样存放，按 usz 对齐
    · >32MB 的文件由多个子块串联（子块间不填充），循环解压直至凑够 usz

  v10 (version 10) —— ★★ 两层嵌套容器，与 v6 完全不同的模型
    · 顶层：csz 恒 0xFFFFFFFF，usz = 条目的**物理字节数**（无对齐，紧凑拼接）
    · 顶层载荷两种：
        (a) 裸文件（0xBEEFFEED 开头，如 .asm_pc）→ 直接原样取出
        (b) 嵌套 vpp 容器（magic 0x51890ACE）→ 见下
    · 嵌套容器内部：目录 + 名字区 + **一条单独的 zlib 流**
        解压 → Σusz2 字节，再按目录顺序以 usz2 切分给各内层文件。
        ★ 不是"每文件一条 zlib 流"——这是最初误判的根源。
    · 内层 zlib 流无 adler32 尾部 4B

  ★ 输出布局（v10）
    嵌套容器内的文件 → <dst>/<顶层条目名>/<内层文件名>
    若内层文件名全局唯一，另在 <dst>/ 下平铺一份，便于使用。
    裸文件          → <dst>/<顶层条目名>

════════════════════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════════════════════
  # 全量解包（自动探测版本）
  python vpp_extract.py extract game.vpp_pc out_dir/

  # 只解指定顶层条目（v10 也接受内层文件名）
  python vpp_extract.py extract game.vpp_pc out_dir/ --only customize_player

  # 只列清单不解包
  python vpp_extract.py list game.vpp_pc --limit 30

退出码: 0=成功; 1=有失败。
"""
import os
import sys
import struct
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from vpp_pack import (          # noqa: E402
    detect_version, v6_read, v10_read, lz4_decompress,
    v10_fits_container, v10_unpack_container,
    V6_NEG, V6_MAGIC1, V6_MAGIC2, CHUNK, READERS,
)


def _rmtree_plain(p):
    """递归删除目录。

    ★ 绕开对 shutil.rmtree 的安全回收站包装（SHFileOperationW 0x78）——
      解包产物是临时目录，走回收站会让几十万个小文件把回收站塞爆。
    """
    if not os.path.isdir(p):
        return
    for root, dirs, files in os.walk(p, topdown=False):
        for fn in files:
            try:
                os.remove(os.path.join(root, fn))
            except OSError:
                pass
        for dn in dirs:
            try:
                os.rmdir(os.path.join(root, dn))
            except OSError:
                pass
    try:
        os.rmdir(p)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════
# v6 解包
# ══════════════════════════════════════════════════════════════════════════
def v6_extract(src, dst, only=None, verbose=False):
    hdr, entries, _names = v6_read(src)
    with open(src, 'rb') as f:
        d = f.read()

    if os.path.exists(dst):
        _rmtree_plain(dst)
    os.makedirs(dst)

    data_phys = hdr['data_off']
    n = len(entries)
    n_ok = n_fail = 0
    for i, e in enumerate(entries):
        if only and e['name'] not in only:
            # 仍需推进物理偏移（对齐依赖累积）
            if e['csz'] == V6_NEG:
                size = e['usz']
            else:
                size = e['csz']
            is_last = (i == n - 1)
            data_phys += size if is_last else ((size + CHUNK - 1) // CHUNK) * CHUNK
            continue

        try:
            if e['csz'] == V6_NEG:
                payload = d[data_phys:data_phys + e['usz']]
                size = e['usz']
            else:
                # 分块 LZ4：>32MB 文件由多个 [16B头 + LZ4块] 串联
                payload = bytearray()
                phys = data_phys
                while len(payload) < e['usz']:
                    if phys + 16 > len(d):
                        raise ValueError('数据区越界（文件被截断）')
                    m1, m2, clen, usz_block = struct.unpack_from('<IIII', d, phys)
                    if m1 != V6_MAGIC1 or m2 != V6_MAGIC2:
                        raise ValueError(
                            f'块魔数错 @0x{phys:X}: {m1:#x}/{m2:#x} '
                            f'(期望 {V6_MAGIC1:#x}/{V6_MAGIC2:#x})')
                    payload += lz4_decompress(d[phys + 16:phys + 16 + clen],
                                              clen, usz_block)
                    phys += 16 + clen
                payload = bytes(payload[:e['usz']])
                size = phys - data_phys
        except ValueError as ex:
            print(f'FAIL {e["name"]}: {ex}')
            n_fail += 1
            is_last = (i == n - 1)
            data_phys += size if is_last else ((size + CHUNK - 1) // CHUNK) * CHUNK
            continue

        if e['csz'] != V6_NEG and len(payload) != e['usz']:
            print(f'WARN {e["name"]}: 解压 {len(payload)} != usz {e["usz"]}')

        out = os.path.join(dst, e['name'])
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'wb') as f:
            f.write(payload)
        n_ok += 1
        if verbose:
            print(f'OK  {e["name"]} ({len(payload)}B)')

        # 文件级 0x1000 对齐（最后文件不 pad）
        is_last = (i == n - 1)
        data_phys += size if is_last else ((size + CHUNK - 1) // CHUNK) * CHUNK

    print(f'v6 解包 {n_ok} 文件, 失败 {n_fail} -> {dst}')
    return 1 if n_fail else 0


# ══════════════════════════════════════════════════════════════════════════
# v10 解包（两层嵌套容器）
# ══════════════════════════════════════════════════════════════════════════
def v10_extract(src, dst, only=None, verbose=False):
    """解包 v10：顶层条目 → 嵌套容器（展开成文件）/ 裸文件。

    输出布局：
      · 嵌套容器内的文件 → `<dst>/<顶层条目名>/<内层文件名>`
        （若内层文件名全局唯一，也额外写一份到 `<dst>/`，方便使用）
      · 裸文件（.asm_pc 等）→ `<dst>/<顶层条目名>`
    """
    hdr, entries, _names = v10_read(src)
    with open(src, 'rb') as f:
        d = f.read()
    data_base = hdr['names_off'] + hdr['name_size']    # ★ 无对齐

    if os.path.exists(dst):
        _rmtree_plain(dst)
    os.makedirs(dst)

    # 先统计内层文件名的全局唯一性
    containers = {}
    for i, e in enumerate(entries):
        oo = data_base + e['data_off']
        blob = d[oo:oo + e['usz']]
        if v10_fits_container(blob):
            try:
                containers[i] = v10_unpack_container(blob)
            except ValueError as ex:
                print(f'FAIL {e["name"]}: 容器解压失败: {ex}')

    from collections import Counter
    allnames = Counter()
    for c in containers.values():
        for k in c:
            allnames[k] += 1

    n_ok = n_fail = n_raw = 0
    for i, e in enumerate(entries):
        if only and e['name'] not in only:
            # only 也支持直接指定内层文件名
            if not any(e['name'] in str(o) or o in containers.get(i, {}) for o in only):
                continue
        oo = data_base + e['data_off']
        blob = d[oo:oo + e['usz']]
        if i in containers:
            for nm, data in containers[i].items():
                out = os.path.join(dst, e['name'], nm)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, 'wb') as f:
                    f.write(data)
                if allnames[nm] == 1:
                    flat = os.path.join(dst, nm)
                    try:
                        with open(flat, 'wb') as f:
                            f.write(data)
                    except OSError:
                        pass
                n_ok += 1
                if verbose:
                    print(f'  OK  {e["name"]}/{nm} ({len(data)}B)')
        else:
            out = os.path.join(dst, e['name'])
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'wb') as f:
                f.write(blob)
            n_raw += 1
            if verbose:
                print(f'  RAW {e["name"]} ({len(blob)}B)')

    print(f'v10 解包 {n_ok} 个内层文件 + {n_raw} 个裸文件, 失败 {n_fail} -> {dst}')
    return 1 if n_fail else 0


# ══════════════════════════════════════════════════════════════════════════
EXTRACTORS = {6: v6_extract, 10: v10_extract}


def cmd_extract(args):
    ver = args.version or detect_version(args.src)
    if ver not in EXTRACTORS:
        print(f'!! 无法判定版本（探测 {ver}）。用 --version 6|10 指定。')
        return 1
    print(f'源   : {args.src}  (版本 {ver})')
    print(f'目标 : {args.dst}')
    only = set(args.only) if args.only else None
    if only:
        print(f'仅解 : {len(only)} 个指定文件')
    return EXTRACTORS[ver](args.src, args.dst, only, args.verbose)


def cmd_list(args):
    ver = args.version or detect_version(args.path)
    if ver not in READERS:
        print(f'!! 无法判定版本（探测 {ver}）')
        return 1
    hdr, entries, _n = READERS[ver](args.path)
    print(f'{args.path}: 版本 {ver}, {len(entries)} 个顶层条目')
    total_u = total_c = 0
    for i, e in enumerate(entries):
        total_u += e['usz']
        # ★ v6 的"未压缩"与 v10 的"嵌套容器"都是 0xFFFFFFFF 系哨兵，
        #   直接累加会得到天文数字，须跳过。
        if e['csz'] == V6_NEG:
            tag = ' [未压缩]'
            csz = e['usz']
            total_c += e['usz']
        elif ver == 10 and e['csz'] == 0xFFFFFFFF:
            tag = ' [容器]'
            csz = 0            # 不计入存储合计（真实大小在容器内部）
        else:
            tag = ''
            csz = e['csz']
            total_c += csz
        if i < args.limit:
            shown = e['usz'] if csz == 0 else csz
            print(f'  {e["usz"]:>12,} -> {shown:>12,}{tag}  {e["name"]}')
    if args.limit and len(entries) > args.limit:
        print(f'  … 共 {len(entries)} 个')
    if ver == 10:
        print(f'合计 顶层载荷 {total_u:,} B（容器内部另计）')
    else:
        print(f'合计 未压缩 {total_u:,} B  存储 {total_c:,} B')
    return 0


def main():
    ap = argparse.ArgumentParser(
        description='Volition VPP 解包（v6=SR3 Remastered / v10=SR4 Re-Elected）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('extract', help='解包')
    p.add_argument('src', help='.vpp_pc')
    p.add_argument('dst', help='输出目录')
    p.add_argument('--only', action='append', help='只解这些文件（可重复）')
    p.add_argument('--version', type=int, choices=[6, 10])
    p.add_argument('-v', '--verbose', action='store_true')
    p.set_defaults(fn=cmd_extract)

    p = sub.add_parser('list', help='列出内容')
    p.add_argument('path')
    p.add_argument('--limit', type=int, default=30)
    p.add_argument('--version', type=int, choices=[6, 10])
    p.set_defaults(fn=cmd_list)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except (ValueError, FileNotFoundError) as e:
        print(f'!! {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
