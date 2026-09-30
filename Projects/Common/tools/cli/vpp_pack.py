#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vpp_pack.py — Volition VPP 打包器（v6 / v10 双格式合一）

取代原 `Projects/SR3/Tools/vpp_pack.py` 与 `Projects/SR4/Tools/sr4_vpp_pack.py`。

════════════════════════════════════════════════════════════════════════════
★ 设计取舍：**合一外壳，但两套块格式实现各自独立、不强行抽象公共层**
════════════════════════════════════════════════════════════════════════════
两代 vpp 的差异是结构性的，不是参数差异：

                  Version 06-x64 (SR3 Remastered)      version 10 (SR4 Re-Elected)
  header 大小     0x188B（含 65B name + 256B path）    0x28B（无名字段）
  header 字段     u64 系列 @0x150..0x180               u32 系列 @0x08..0x1C
  目录项          48B × N                              24B × N
  对齐            0x1000 chunk 对齐                    无对齐（紧凑拼接）
  压缩            LZ4 block，16B 头 {0x0FEEDBEE,
                  0x00BADBEE, lz4len, usz}            zlib 流（去掉 adler32 尾部 4B）
  块上限          32MB 分块（游戏解压器限制）           无（单流）
  未压缩标记      csz = 0xFFFFFFFFFFFFFFFF              csz = 0xFFFFFFFF（顶层全部）

★★★ v10 的**两层嵌套结构**（2026-09 实测反解，见 `_vpp_diag` 推导过程）
──────────────────────────────────────────────────────────────────────────
Saints Row IV 的 vpp_pc 是"**容器套容器**"，与 v6 的"扁平文件表"完全不同：

  顶层（外层 vpp）
    每条目录项 24B { nameOff, 0, dataOff, usz, csz, flags }
      ★ 顶层 csz 恒为 0xFFFFFFFF，flags 恒 0x10000 → 语义是"原始载荷"
      ★ 顶层 usz = 该条目的**物理字节数**（无任何对齐，条与条紧凑拼接）
      dataOff 是虚拟偏移（按 usz 累加），物理即 `names_off + name_size + Σusz`

    `blob = d[phys + data_off : phys + data_off + usz]`

    载荷有两种：
      (a) **裸文件**（如 `customize_player.asm_pc`）：以 0xBEEFFEED 开头，
          直接原样取出即可（不是 vpp）。
      (b) ***嵌套 vpp 容器***（magic 0x51890ACE，占绝大多数）：见下。

  内层（嵌套的 vpp 容器，同样 0x28 头 + 24B 目录项）
    ★ 内层条目的 usz2/csz2 是**真实**的压缩前后大小（csz2 ≠ 0xFFFFFFFF），
      flags 恒 0x100001。
    ★★ 关键：整个内层容器的**数据区是一条单独的 zlib 流**，
       解压后得到 Σusz2 字节，再按目录顺序、以 usz2 为长度切分给各内层文件。
       —— 不是"每个文件一条 zlib 流"。这是最初误判的根源：
          逐条目 zlib 只能解出第 1 个（恰好是小 .cvbm_pc），其余全部失败。
    ★ 内层数据区起点 = 0x28 + dir_size + name_size，无对齐。
    ★ 内层 zlib 流无 adler32 尾部（与 v6 的 csz=0xFFFFFFFF 语义无关）。

  实测指纹（`customize_player.vpp_pc`，414 顶层条 → 1002 内层文件）：
    顶层 414 条全为 flags=0x10000 / csz=0xFFFFFFFF；
    其中 413 条是嵌套容器，1 条是裸 .asm_pc；
    平均每条容器 2.42 个文件；内层压缩比中位 ≈ 1.9。

强行抽公共层只会得到一堆 if，反而降低可读性、增加改坏的风险。
所以本文件保留两套独立实现，仅在上层统一 CLI 与产物自检。

════════════════════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════════════════════
  # 自动按模板 header 探测版本
  python vpp_pack.py pack 模板.vpp_pc 源目录 输出.vpp_pc

  # 显式指定（模板损坏或想强制时）
  python vpp_pack.py pack 模板.vpp_pc 源目录 输出.vpp_pc --version 10

  # 查看 vpp 结构（版本/条目数/嵌套容器展开数/压缩方式）
  python vpp_pack.py info 某个.vpp_pc

★ v10 的源目录布局：`<源目录>/<顶层条目名>/<内层文件名>`
  也接受扁平布局（内层文件名唯一时）。

退出码: 0=成功; 1=失败。
"""
import os
import sys
import struct
import zlib
import argparse

CHUNK = 0x1000
MAGIC = 0x51890ACE

# v6 专有
V6_NEG = 0xFFFFFFFFFFFFFFFF
V6_MAGIC1 = 0x0FEEDBEE
V6_MAGIC2 = 0x00BADBEE
V6_BLOCK_USZ = 0x2000000        # 32MB —— 游戏 LZ4 解压器的 block_size

# v10 专有
V10_DIR_ENTRY = 24
V6_DIR_ENTRY = 48


# ══════════════════════════════════════════════════════════════════════════
# 版本探测
# ══════════════════════════════════════════════════════════════════════════
def detect_version(path):
    """读 header 前 8 字节判定 vpp 版本。返回 6 / 10 / None。"""
    with open(path, 'rb') as f:
        head = f.read(8)
    if len(head) < 8:
        return None
    magic, ver = struct.unpack_from('<II', head, 0)
    if magic != MAGIC:
        return None
    return ver if ver in (6, 10) else None


# ══════════════════════════════════════════════════════════════════════════
# ── 格式 A：Version 06-x64（SR3 Remastered）──────────────────────────────
# ══════════════════════════════════════════════════════════════════════════
try:
    import lz4.block as _lz4_block

    def _lz4_compress(data):
        return _lz4_block.compress(data, mode="high_compression",
                                   compression=12, store_size=False)
except ImportError:
    _lz4_block = None

    def _lz4_compress(data):
        return None


def lz4_decompress(src, s_len, dst_size):
    """纯 Python LZ4 block 解压（无 lz4 库时兜底）。"""
    out = bytearray()
    si = 0
    while si < s_len:
        token = src[si]
        si += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[si]
                si += 1
                lit += b
                if b != 0xFF:
                    break
        out += src[si:si + lit]
        si += lit
        if si >= s_len:
            break
        ml = token & 0xF
        off = src[si] | (src[si + 1] << 8)
        si += 2
        if ml == 15:
            while True:
                b = src[si]
                si += 1
                ml += b
                if b != 0xFF:
                    break
        ml += 4
        for _ in range(ml):
            out.append(out[-off])
    return bytes(out[:dst_size])


def v6_read(path):
    """解析 v6 vpp → (hdr, entries, names_bytes)"""
    with open(path, 'rb') as f:
        d = f.read()
    magic, ver = struct.unpack_from('<II', d, 0)
    if magic != MAGIC or ver != 6:
        raise ValueError(f'{path}: 不是 Version06-x64 vpp (magic=0x{magic:08X} ver={ver})')
    flags = struct.unpack_from('<I', d, 0x14C)[0]
    count, psize, dsize, nsize, udata, cdata = struct.unpack_from('<QQQQQQ', d, 0x158)

    dir_off = CHUNK
    names_off = dir_off + ((dsize + CHUNK - 1) // CHUNK) * CHUNK
    data_off = names_off + ((nsize + CHUNK - 1) // CHUNK) * CHUNK

    entries = []
    for i in range(count):
        e = dir_off + i * V6_DIR_ENTRY
        no, unk1, doff, usz, csz, unk2 = struct.unpack_from('<QQQQQQ', d, e)
        end = d.index(b'\x00', names_off + no)
        name = d[names_off + no:end].decode('ascii', 'replace')
        entries.append(dict(name=name, name_off=no, data_off=doff,
                            usz=usz, csz=csz, unk1=unk1, unk2=unk2))
    names = d[names_off:names_off + nsize]
    hdr = dict(version=6, flags=flags, count=count, dsize=dsize, nsize=nsize,
               udata=udata, cdata=cdata, dir_off=dir_off,
               names_off=names_off, data_off=data_off, raw_size=len(d))
    return hdr, entries, names


def _v6_compress_file(data):
    """v6 压缩：按 <=32MB 分块，每块独立 16B 头。返回 (is_compressed, payload)。"""
    usz = len(data)
    if _lz4_compress is None:
        return False, data
    full = _lz4_compress(data)
    if full is not None and len(full) + 16 < usz and usz <= V6_BLOCK_USZ:
        return True, struct.pack('<4I', V6_MAGIC1, V6_MAGIC2, len(full), usz) + full
    payload = bytearray()
    for start in range(0, usz, V6_BLOCK_USZ):
        chunk = data[start:start + V6_BLOCK_USZ]
        comp = _lz4_compress(chunk)
        if comp is None:
            return False, data
        payload += struct.pack('<4I', V6_MAGIC1, V6_MAGIC2, len(comp), len(chunk)) + comp
    if len(payload) < usz:
        return True, bytes(payload)
    return False, data


def v6_pack(template, src_dir, out_path, verbose=True):
    """用 src_dir 中的同名文件按 template 的顺序与 names 重打包（v6）。"""
    hdr, entries, names = v6_read(template)

    blobs = []          # (name, is_comp, csz_eff, usz, payload)
    for ent in entries:
        p = os.path.join(src_dir, ent['name'])
        if not os.path.exists(p):
            raise FileNotFoundError('缺失文件: %s' % p)
        data = open(p, 'rb').read()
        usz = len(data)
        comp, payload = _v6_compress_file(data)
        blobs.append((ent['name'], comp, len(payload) if comp else usz, usz, payload))

    # 物理布局：压缩按 csz、未压缩按 usz，均 0x1000 对齐，最后不 pad
    phys_sizes, phys_off = [], 0
    for i, (_n, _c, csz, _u, payload) in enumerate(blobs):
        is_last = (i == len(blobs) - 1)
        size = csz
        phys_sizes.append(size if is_last else ((size + CHUNK - 1) // CHUNK) * CHUNK)
        phys_off += phys_sizes[-1]
    cdata = phys_off

    # 虚拟 dataOffset：usz 对齐累积（官方语义），最后不 pad
    virt_off = 0
    for i, (_n, comp, csz, usz, _p) in enumerate(blobs):
        entries[i]['data_off'] = virt_off
        entries[i]['usz'] = usz
        entries[i]['csz'] = csz if comp else V6_NEG
        is_last = (i == len(blobs) - 1)
        virt_off += usz if is_last else ((usz + CHUNK - 1) // CHUNK) * CHUNK
    udata_total = virt_off

    n = len(entries)
    dsize = n * V6_DIR_ENTRY
    dir_chunks = (dsize + CHUNK - 1) // CHUNK
    name_chunks = (hdr['nsize'] + CHUNK - 1) // CHUNK
    data_off = CHUNK * (1 + dir_chunks + name_chunks)
    packfile_size = data_off + cdata

    with open(out_path, 'wb') as f:
        f.write(b'\x00' * CHUNK)                    # chunk0: header 占位
        for ent in entries:
            f.write(struct.pack('<QQQQQQ', ent['name_off'], 0, ent['data_off'],
                                ent['usz'], ent['csz'], 0))
        f.write(b'\x00' * (CHUNK * dir_chunks - dsize))
        f.write(names)
        f.write(b'\x00' * (CHUNK * name_chunks - hdr['nsize']))
        for i, (_n, _c, _csz, _u, payload) in enumerate(blobs):
            f.write(payload)
            pad = phys_sizes[i] - len(payload)
            if pad > 0:
                f.write(b'\x00' * pad)

    with open(out_path, 'r+b') as f:
        f.seek(0)
        hb = bytearray(CHUNK)
        struct.pack_into('<II', hb, 0x00, MAGIC, 6)
        struct.pack_into('<I', hb, 0x14C, hdr['flags'])
        struct.pack_into('<QQQQQQ', hb, 0x150, 0, n, packfile_size, dsize,
                         hdr['nsize'], udata_total)
        struct.pack_into('<Q', hb, 0x180, cdata)
        f.write(bytes(hb))

    if verbose:
        print(f'  v6 打包完成: {n} 文件, packfile={packfile_size:,} '
              f'({udata_total:,} -> {cdata:,}, 压缩比 {cdata / max(udata_total, 1):.2f})')
    return out_path


# ══════════════════════════════════════════════════════════════════════════
# ── 格式 B：version 10（SR4 Re-Elected）—— 两层嵌套容器 ──────────────────
# ══════════════════════════════════════════════════════════════════════════
V10_NESTED = 0xFFFFFFFF     # 顶层 csz 的"原始载荷"哨兵
V10_RAW_MAGIC = 0xBEEFFEED  # 内层裸文件（.asm_pc 等）的开头魔数
V10_FLAG_RAW = 0x10000      # 顶层条目 flags
V10_FLAG_INNER = 0x100001   # 内层条目 flags


def _v10_parse_dir(d, off=0x28):
    """通用 v10 目录解析（顶层与内层共用）。

    返回 (hdr_dict, entries)。entries[i] 含
    name/name_off/data_off/usz/csz/flags。
    """
    magic, ver = struct.unpack_from('<II', d, off - 0x28)
    if magic != MAGIC:
        raise ValueError('v10 magic 0x%08X != 0x%08X' % (magic, MAGIC))
    if ver != 10:
        raise ValueError('v10 version %d != 10' % ver)

    crc, unk0C, flags, count = struct.unpack_from('<4I', d, off - 0x20)
    dir_size, name_size = struct.unpack_from('<2I', d, off - 0x10)

    dir_off = off
    names_off = dir_off + count * V10_DIR_ENTRY
    entries = []
    for i in range(count):
        no, _b, doff, usz, csz, fl = struct.unpack_from('<6I', d, dir_off + i * V10_DIR_ENTRY)
        end = d.index(b'\x00', names_off + no)
        name = d[names_off + no:end].decode('utf-8', 'replace')
        entries.append(dict(name=name, name_off=no, data_off=doff,
                            usz=usz, csz=csz, flags=fl))
    hdr = dict(version=10, crc=crc, unk0C=unk0C, flags=flags, count=count,
               dir_size=dir_size, name_size=name_size, dir_off=dir_off,
               names_off=names_off, raw_size=len(d),
               name_bytes=d[names_off:names_off + name_size])
    return hdr, entries


def v10_read(path):
    """解析顶层 v10 vpp → (hdr, entries, names_bytes)"""
    with open(path, 'rb') as f:
        d = f.read()
    hdr, entries = _v10_parse_dir(d)
    return hdr, entries, hdr['name_bytes']


def v10_unpack_container(blob):
    """把一个"嵌套容器"载荷解开 → dict{name: bytes}（保持目录顺序）。

    ★★ blob 的数据区是**一条单独的 zlib 流**，解压后按 usz2 顺序切分。
    """
    if len(blob) < 0x28:
        raise ValueError('容器太短 (%d B)' % len(blob))
    cnt = struct.unpack_from('<I', blob, 0x14)[0]
    dsz = struct.unpack_from('<I', blob, 0x18)[0]
    nsz = struct.unpack_from('<I', blob, 0x1C)[0]
    inames = 0x28 + dsz
    iphys = inames + nsz
    if iphys > len(blob):
        raise ValueError('容器头部越界 (iphys=%d len=%d)' % (iphys, len(blob)))

    rows = [struct.unpack_from('<6I', blob, 0x28 + j * V10_DIR_ENTRY) for j in range(cnt)]
    total = sum(r[3] for r in rows)

    stream = bytes(blob[iphys:])
    if stream[:2] in (b'\x78\x01', b'\x78\x5e', b'\x78\x9c', b'\x78\xda'):
        raw = zlib.decompressobj().decompress(stream)
    else:
        # 未压缩容器（内层 csz 全为 usz 的情况）
        raw = stream

    if len(raw) != total:
        raise ValueError('容器解压 %d != 目录声明 %d' % (len(raw), total))

    out = {}
    off = 0
    for (no, _b, _doff2, usz2, _csz2, _fl2) in rows:
        end = blob.index(b'\x00', inames + no)
        name = blob[inames + no:end].decode('utf-8', 'replace')
        out[name] = raw[off:off + usz2]
        off += usz2
    return out


def v10_fits_container(blob):
    """判断载荷是否为嵌套 vpp 容器。"""
    return len(blob) >= 8 and blob[:4] == b'\xce\x0a\x89\x51' \
        and struct.unpack_from('<I', blob, 4)[0] == 10


def v10_pack_container(inner, template_blob):
    """用 inner{name: bytes} 重建容器；template_blob 提供目录/名字/顺序。

    ★ 内层压缩：单 zlib 流（去掉 adler32 尾部 4B）。
    ★ usz2 按**实际内容长度**回填（内容可变长）。
    """
    cnt = struct.unpack_from('<I', template_blob, 0x14)[0]
    nsz = struct.unpack_from('<I', template_blob, 0x1C)[0]
    inames = 0x28 + cnt * V10_DIR_ENTRY
    name_bytes = template_blob[inames:inames + nsz]

    rows = [struct.unpack_from('<6I', template_blob, 0x28 + j * V10_DIR_ENTRY)
            for j in range(cnt)]

    payloads = []
    raw = bytearray()
    for (no, b, _doff2, _usz2, csz2, fl2) in rows:
        end = template_blob.index(b'\x00', inames + no)
        name = template_blob[inames + no:end].decode('utf-8', 'replace')
        if name not in inner:
            raise FileNotFoundError('容器缺文件: %s' % name)
        data = inner[name]
        payloads.append((no, b, csz2, fl2, len(data)))
        raw += data

    comp = zlib.compress(bytes(raw), zlib.Z_BEST_COMPRESSION)[:-4]

    out = bytearray(0x28)
    struct.pack_into('<II', out, 0x00, MAGIC, 10)
    struct.pack_into('<I', out, 0x08, struct.unpack_from('<I', template_blob, 0x08)[0])
    struct.pack_into('<I', out, 0x0C, struct.unpack_from('<I', template_blob, 0x0C)[0])
    struct.pack_into('<I', out, 0x10, struct.unpack_from('<I', template_blob, 0x10)[0])
    struct.pack_into('<I', out, 0x14, cnt)
    struct.pack_into('<I', out, 0x18, cnt * V10_DIR_ENTRY)
    struct.pack_into('<I', out, 0x1C, nsz)

    dbuf = bytearray(cnt * V10_DIR_ENTRY)
    acc = 0
    for j, (no, b, csz2, fl2, u2) in enumerate(payloads):
        struct.pack_into('<6I', dbuf, j * V10_DIR_ENTRY, no, b, acc, u2, csz2, fl2)
        acc += u2
    out += dbuf
    out += name_bytes
    out += comp
    return bytes(out)


def v10_pack(template, src_dir, out_path, verbose=True):
    """用 src_dir 中的同名文件按 template 的**嵌套结构**重打包（v10）。

    src_dir 支持两种布局：
      · 扁平：`<src_dir>/<内层文件名>`  —— 同名文件唯一
      · 分组：`<src_dir>/<顶层条目名>/<内层文件名>`
    """
    with open(template, 'rb') as f:
        tpl = f.read()
    hdr, entries = _v10_parse_dir(tpl)
    base = hdr['names_off'] + hdr['name_size']

    def _load(name, sub):
        p = os.path.join(src_dir, sub, name) if sub else os.path.join(src_dir, name)
        if os.path.exists(p):
            return open(p, 'rb').read()
        p2 = os.path.join(src_dir, name)
        if os.path.exists(p2):
            return open(p2, 'rb').read()
        return None

    blobs = []
    n_inner = n_raw = 0
    for ent in entries:
        oo = base + ent['data_off']
        blob = tpl[oo:oo + ent['usz']]
        if v10_fits_container(blob):
            inner_tpl = v10_unpack_container(blob)
            inner = {}
            for k in inner_tpl:
                got = _load(k, ent['name'])
                if got is None:
                    raise FileNotFoundError('缺失: %s/%s' % (ent['name'], k))
                inner[k] = got
            nb = v10_pack_container(inner, blob)
            n_inner += len(inner)
        else:
            got = _load(ent['name'], None)
            if got is None:
                raise FileNotFoundError('缺失: %s' % ent['name'])
            nb = got
            n_raw += 1
        blobs.append((ent, nb))

    # 顶层 raw 长度 = usz（内含容器已重算）；这里回填实际长度
    out = bytearray(0x28)
    struct.pack_into('<II', out, 0x00, MAGIC, 10)
    struct.pack_into('<I', out, 0x08, hdr['crc'])
    struct.pack_into('<I', out, 0x0C, hdr['unk0C'])
    struct.pack_into('<I', out, 0x10, hdr['flags'])
    struct.pack_into('<I', out, 0x14, len(blobs))
    struct.pack_into('<I', out, 0x18, len(blobs) * V10_DIR_ENTRY)
    struct.pack_into('<I', out, 0x1C, hdr['name_size'])

    dbuf = bytearray(len(blobs) * V10_DIR_ENTRY)
    acc = 0
    for i, (ent, nb) in enumerate(blobs):
        struct.pack_into('<6I', dbuf, i * V10_DIR_ENTRY, ent['name_off'], 0,
                         acc, len(nb), V10_NESTED, ent['flags'])
        acc += len(nb)
    out += dbuf
    out += hdr['name_bytes']
    for _e, nb in blobs:
        out += nb

    with open(out_path, 'wb') as f:
        f.write(bytes(out))

    if verbose:
        print(f'  v10 打包完成: {len(blobs)} 顶层条目 '
              f'(展开 {n_inner} 内层文件 + {n_raw} 裸文件), 输出 {len(out):,} B')
    return out_path


# ══════════════════════════════════════════════════════════════════════════
# 统一入口
# ══════════════════════════════════════════════════════════════════════════
PACKERS = {6: v6_pack, 10: v10_pack}
READERS = {6: v6_read, 10: v10_read}


def cmd_info(args):
    ver = detect_version(args.path)
    if ver is None:
        print(f'!! {args.path}: 不是已知的 vpp（magic 不符或版本非 6/10）')
        return 1
    hdr, entries, _names = READERS[ver](args.path)
    size = os.path.getsize(args.path)
    print(f'文件      : {args.path}')
    print(f'★ 版本    : {ver}  ({"Version06-x64 / LZ4 / 0x1000 对齐" if ver == 6 else "version 10 / 嵌套容器 + zlib / 无对齐"})')
    print(f'文件数    : {hdr["count"]}')
    print(f'文件大小  : {size:,} B')
    if ver == 6:
        print(f'未压缩合计: {hdr["udata"]:,} B')
        # ★ v6 header 的 cdata 在全未压缩时也是哨兵 0xFFFFFFFFFFFFFFFF
        if hdr['cdata'] == V6_NEG:
            print(f'压缩后合计: 未压缩（全表原样存放）')
        else:
            print(f'压缩后合计: {hdr["cdata"]:,} B')
        print(f'目录/名字区: dir={hdr["dsize"]}B  names={hdr["nsize"]}B')
    else:
        print(f'目录/名字区: dir={hdr["dir_size"]}B  names={hdr["name_size"]}B')
        # ★ 统计嵌套容器
        n_nest = n_raw = n_inner = 0
        with open(args.path, 'rb') as f:
            d = f.read()
        base = hdr['names_off'] + hdr['name_size']
        for e in entries:
            blob = d[base + e['data_off']:base + e['data_off'] + e['usz']]
            if v10_fits_container(blob):
                n_nest += 1
                try:
                    n_inner += len(v10_unpack_container(blob))
                except ValueError:
                    pass
            else:
                n_raw += 1
        print(f'★ 结构    : {n_nest} 个嵌套容器（展开 {n_inner} 文件）'
              f' + {n_raw} 个裸文件')
    if args.list:
        print('-' * 64)
        for i, e in enumerate(entries):
            if i >= args.list:
                print(f'  … 共 {len(entries)} 个')
                break
            # ★ v6 用 csz == 0xFFFFFFFFFFFFFFFF 表示"未压缩"，须特判显示
            if e['csz'] == V6_NEG:
                print(f'  {e["usz"]:>10,} -> {"未压缩":>10}  {e["name"]}')
            elif ver == 10 and e['csz'] == V10_NESTED:
                print(f'  {e["usz"]:>10,} -> {"容器":>10}  {e["name"]}')
            else:
                print(f'  {e["usz"]:>10,} -> {e["csz"]:>10,}  {e["name"]}')
    return 0


def cmd_pack(args):
    ver = args.version or detect_version(args.template)
    if ver not in PACKERS:
        print(f'!! 无法判定模板 vpp 版本（探测结果 {ver}）。请用 --version 6|10 指定。')
        return 1
    tpl_names = [e['name'] for e in READERS[ver](args.template)[1]]
    print(f'模板 : {args.template}  (版本 {ver}, {len(tpl_names)} 顶层条目)')
    print(f'源   : {args.src_dir}')
    print(f'产物 : {args.out}')
    PACKERS[ver](args.template, args.src_dir, args.out)

    # ★ 产物自检：重新解析，确认版本与顶层条目数一致
    v2 = detect_version(args.out)
    if v2 != ver:
        print(f'!! 产物版本校验失败: 探测 {v2} != 写入 {ver}')
        return 1
    n2 = READERS[v2](args.out)[0]['count']
    if n2 != len(tpl_names):
        print(f'!! 产物条目数校验失败: {n2} != {len(tpl_names)}')
        return 1
    print(f'自检通过: 版本 {v2}, {n2} 个顶层条目, {os.path.getsize(args.out):,} B')
    return 0


def main():
    ap = argparse.ArgumentParser(
        description='Volition VPP 打包（v6=SR3 Remastered / v10=SR4 Re-Elected，双格式合一）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('info', help='查看 vpp 结构')
    p.add_argument('path')
    p.add_argument('--list', type=int, default=0, metavar='N',
                   help='列出前 N 个文件条目')
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser('pack', help='按模板重打包')
    p.add_argument('template', help='模板 .vpp_pc')
    p.add_argument('src_dir', help='源文件目录（须含全部同名文件）')
    p.add_argument('out', help='输出 .vpp_pc')
    p.add_argument('--version', type=int, choices=[6, 10],
                   help='强制版本（默认按模板 header 探测）')
    p.set_defaults(fn=cmd_pack)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except (ValueError, FileNotFoundError) as e:
        print(f'!! {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
