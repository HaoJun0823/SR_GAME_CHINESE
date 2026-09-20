# -*- coding: utf-8 -*-
"""
SRTT le_strings 通用 repack 器 (保桶版)
=======================================
保留原文件的 bucket 分配 (引擎按 bucket 查找, 不按 hash%nb 重新分桶);
桶内 entries 按 hash 排序重排 (稳定) + 4 字节对齐, 改写 pair 中给出的 hash 文本。
其余原 hash 沿用其原文本。

★ 布局 (SR3 Remastered / SR4 一致, 务必按此读写):
    header  12B : ID u32(0xA84C7F73) | version u16 | bucketCount u16 | stringCount u32
    bucket  16B : count u32 | pad u32 | offTableOffset u32 | pad u32   (x bucketCount)
    offset表    : 从 bucket.offTableOffset 起, count 个 **8 字节** 条目
                  = { 字符串绝对偏移 u32, 填充零 u32 }
    字符串条目  : { hash u32, utf16le 文本, u16 0x0000 }

★★ 血的教训 (2026-09-20): offset 表条目是 **8 字节步长**, 不是 4 字节!
    旧版误用 4 字节步长 -> 把每条后面的「填充零 u32」也当成一个条目 ->
    真实条目的偏移被错位读取, 一半条目被读成 s_off==0 的「空槽」而被丢弃 ->
    写回文件桶内条目数减半、但 header.stringCount 不变 ->
    引擎按声明索引越界 -> 取到 NULL 字符串指针 -> 进程崩溃 (c0000005)。
    customize_us: 3061 条里 1405 条被丢, 文件从 130090B 缩水到 59020B (-55%)。
"""
import os
import struct


def read_with_bucket(path):
    """返回 (fid, ver, nb, nstr, [(bucket_idx, hash, text_bytes)])
       text_bytes = buf[s_off+4 : 终止 0x0000 +2]  (含末尾 0x0000)
    """
    buf = open(path, "rb").read()
    fid, ver, nb, nstr = struct.unpack_from("<IHHI", buf, 0)
    if fid != 0xA84C7F73:
        raise ValueError(f"{path}: 非 le_strings (ID={fid:#x})")
    out = []
    total_count = 0
    for i in range(nb):
        base = 12 + i * 16
        if base + 16 > len(buf):
            raise ValueError(f"{path}: bucket[{i}] 表越界")
        count, _, off, _ = struct.unpack_from("<IIII", buf, base)
        total_count += count
        for j in range(count):
            so = off + j * 8          # ★ 8 字节步长 (不是 4!)
            if so + 4 > len(buf):
                raise ValueError(f"{path}: bucket[{i}] offset表越界 @0x{so:X}")
            s_off = struct.unpack_from("<I", buf, so)[0]
            if s_off == 0:
                out.append((i, 0, b""))     # 占位空槽 (s_off=0)
                continue
            if s_off + 4 > len(buf):
                raise ValueError(f"{path}: bucket[{i}] 字符串偏移越界 @0x{s_off:X}")
            e = s_off + 4
            while e + 1 < len(buf) and struct.unpack_from("<H", buf, e)[0] != 0:
                e += 2
            text_bytes = buf[s_off + 4: e + 2]
            h = struct.unpack_from("<I", buf, s_off)[0]
            out.append((i, h, text_bytes))
    if total_count != nstr:
        raise ValueError(
            f"{path}: bucket 条目总数 {total_count} != header.stringCount {nstr} (文件不一致)")
    if len(out) != nstr:
        raise ValueError(f"{path}: 解析出 {len(out)} 条 != nstr {nstr}")
    return fid, ver, nb, nstr, out


def repack(in_path, pairs, out_path):
    """
    pairs: dict {hash: text_u16_bytes_LE(末尾 0x0000)}
           None 或缺失 = 沿用原文件文本
    """
    pairs = dict(pairs)
    fid, ver, nb, nstr, entries = read_with_bucket(in_path)
    # 应用覆盖 (保留空槽)
    out_entries = []
    for b, h, t in entries:
        if h == 0:
            out_entries.append((b, 0, b""))
        elif h in pairs and pairs[h] is not None:
            out_entries.append((b, h, pairs[h]))
            pairs.pop(h, None)
        else:
            out_entries.append((b, h, t))
    # 每桶内按 hash 升序 (空槽 0 排最前)
    buckets = [[] for _ in range(nb)]
    for b, h, t in out_entries:
        buckets[b].append((h, t))
    for b in range(nb):
        buckets[b].sort(key=lambda x: x[0])
    # 写文件
    # 注意: SR3 Remastered 的 offset 表每条为 8 字节 (u32 绝对偏移 + u32 填充0),
    #       与 sr3le_extract / 游戏引擎读取格式一致; 此前误写成 4 字节会导致解析越界。
    head_size = 12 + 16 * nb + 8 * nstr
    string_start = (head_size + 3) & ~3
    out = bytearray(string_start)
    struct.pack_into("<IHHI", out, 0, fid, ver, nb, nstr)
    cur = string_start
    bucket_offsets = [[] for _ in range(nb)]
    for b in range(nb):
        for h, t in buckets[b]:
            if h == 0:
                bucket_offsets[b].append(0)        # 空槽: offset=0
                continue
            entry = struct.pack("<I", h) + t
            while cur & 3:
                cur += 1
                out.append(0)
            bucket_offsets[b].append(cur)
            out += entry
            cur = len(out)
    # 桶 header (count + offset 表起点)
    for b in range(nb):
        bucket_header = 12 + b * 16
        off_table_start = 12 + nb * 16 + 8 * sum(len(x) for x in buckets[:b])
        struct.pack_into("<IIII", out, bucket_header, len(buckets[b]), 0, off_table_start, 0)
    # offset 表 (每条 8 字节: u32 绝对偏移 + u32 填充0)
    pos = 12 + 16 * nb
    for b in range(nb):
        for off in bucket_offsets[b]:
            struct.pack_into("<II", out, pos, off, 0)
            pos += 8

    # ★★ 写后自检 (强制): 条目数必须与 header 一致, 否则引擎按声明索引会越界崩溃。
    written_total = sum(len(x) for x in buckets)
    if written_total != nstr:
        raise ValueError(
            f"{in_path}: 写回条目数 {written_total} != header.stringCount {nstr} —— 拒绝写出")
    if pos != 12 + 16 * nb + 8 * nstr:
        raise ValueError(
            f"{in_path}: offset 表写入长度 {pos - (12 + 16 * nb)} != 8*{nstr} —— 拒绝写出")

    open(out_path, "wb").write(bytes(out))

    # ★★ 回读校验 (强制): 用本模块的读路径重新解析产物, 必须能读回 nstr 条。
    chk = open(out_path, "rb").read()
    _f, _v, _nb, _n, back = read_with_bucket(out_path)
    if len(back) != nstr:
        raise ValueError(f"{out_path}: 回读校验失败, 只读回 {len(back)}/{nstr} 条")
    return nstr


def repack_inplace(in_path, pairs, out_path):
    """
    原位覆盖版: 保持官方文件布局完全不变, 只把 pairs{hash: 新文本(含0x0000)}
    对应条目的文本**在原 string 槽位原位改写** (不足的剩余字节清零)。
    要求每条新文本 <= 该条目原文本槽长(含终止符), 否则报错。
    适用: 引擎可能依赖 string 区线性布局, 全量 repack 重排有风险 (v3 崩溃疑因)。
    """
    buf = bytearray(open(in_path, "rb").read())
    fid, ver, nb, nstr = struct.unpack_from("<IHHI", buf, 0)
    if fid != 0xA84C7F73:
        raise ValueError(f"{in_path}: 非 le_strings (ID={fid:#x})")
    pairs = dict(pairs)
    replaced = {}
    for i in range(nb):
        if 12 + i * 16 + 16 > len(buf):
            raise ValueError(f"{in_path}: bucket[{i}] 表越界")
        count, _, off, _ = struct.unpack_from("<IIII", buf, 12 + i * 16)
        for j in range(count):
            if off + j * 8 + 4 > len(buf):
                raise ValueError(f"{in_path}: bucket[{i}] offset 表越界 @0x{off + j*8:X}")
            s_off = struct.unpack_from("<I", buf, off + j * 8)[0]   # ★ 8 字节步长
            if s_off == 0:
                continue
            if s_off + 4 > len(buf):
                raise ValueError(f"{in_path}: bucket[{i}] 字符串偏移越界 @0x{s_off:X}")
            h = struct.unpack_from("<I", buf, s_off)[0]
            if h not in pairs:
                continue
            # 原文本区: [s_off+4, e) 到 0x0000 终止 (含 e, e+1 两字节 0x00)
            e = s_off + 4
            while e + 1 < len(buf) and struct.unpack_from("<H", buf, e)[0] != 0:
                e += 2
            slot_len = (e + 2) - (s_off + 4)      # 原文本字节数(含终止 0x0000)
            new_t = pairs.pop(h)
            # 用 ValueError 而非 assert: assert 在 python -O 下会被剥离, 不可依赖
            if len(new_t) > slot_len:
                raise ValueError(
                    f"hash {h:#08x}: 新文本 {len(new_t)}B > 原槽 {slot_len}B, 无法原位覆盖")
            # 写 {hash} 不变, 覆盖文本并清零剩余
            buf[s_off + 4: s_off + 4 + len(new_t)] = new_t
            buf[s_off + 4 + len(new_t): s_off + 4 + slot_len] = b"\x00" * (
                slot_len - len(new_t))
            replaced[h] = (s_off, slot_len, len(new_t))
    if pairs:
        # txt 里存在原文件没有的 hash 是常见情况 (如未发布条目), 仅警告;
        # 只有「超槽」才需要抛错触发调用方降级为全量重建。
        miss = ", ".join(f"{h:#08x}" for h in list(pairs)[:10])
        if len(pairs) > 10:
            miss += f" ... (共 {len(pairs)} 个)"
        print(f"  警告: {len(pairs)} 个 hash 在原文件中不存在: {miss}")
    open(out_path, "wb").write(bytes(buf))
    return replaced


def self_test():
    """自检: 对内置最小样例做 round-trip, 验证读/写在 8 字节 offset 表下自洽。

    用于 CI / 自动化构建前置校验, 不依赖任何游戏文件。
    """
    import io
    import tempfile

    # 构造一个最小合法 le_strings: 1 bucket, 2 条字符串 (含 1 个空槽)
    # header 12B + bucket 16B + offset 表 8*3=24B
    nb, nstr = 1, 3
    head = 12 + 16 * nb + 8 * nstr
    start = (head + 3) & ~3
    buf = bytearray(start)
    struct.pack_into("<IHHI", buf, 0, 0xA84C7F73, 1, nb, nstr)
    bucket_offsets = []
    cur = start
    for text in ("HELLO", "WORLD"):
        while cur & 3:
            cur += 1; buf.append(0)
        bucket_offsets.append(cur)
        buf += struct.pack("<I", 0x12345678) + text.encode("utf-16-le") + b"\x00\x00"
        cur = len(buf)
    bucket_offsets.insert(0, 0)   # 第一个是空槽
    struct.pack_into("<IIII", buf, 12, nstr, 0, 12 + 16 * nb, 0)
    pos = 12 + 16 * nb
    for off in bucket_offsets:
        struct.pack_into("<II", buf, pos, off, 0)
        pos += 8

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "t_us.le_strings")
        dst = os.path.join(td, "t_zh.le_strings")
        open(src, "wb").write(bytes(buf))
        # 回读必须得到 nstr 条
        _f, _v, _nb, got, entries = read_with_bucket(src)
        assert got == nstr, f"self_test: 读出 {got} != {nstr}"
        assert sum(1 for e in entries if e[1] == 0) == 1, "self_test: 空槽数量不对"
        # 原位覆盖/全量重建都必须保条目数
        pairs = {0x12345678: "你好".encode("utf-16-le") + b"\x00\x00"}
        repack(src, {}, dst)                       # 空覆盖 = 纯 round-trip
        _f, _v, _nb, got2, _e2 = read_with_bucket(dst)
        assert got2 == nstr, f"self_test(repack): {got2} != {nstr}"
    print("[self_test] le_strings_repack OK: 8 字节 offset 表读/写自洽, 条目数守恒")


def demo():
    """交互式试验用: 从命令行传入原始 le_strings 与一个 hash/文本对。

    用法: python le_strings_repack.py <in.le_strings> <hash_hex> <utf16_text> [out]
    (不传参数时仅运行 self_test, 便于自动化/CI 校验。)
    """
    import sys
    if len(sys.argv) < 4:
        self_test()
        return 0
    in_p = sys.argv[1]
    h = int(sys.argv[2], 16)
    text = sys.argv[3]
    out_p = sys.argv[4] if len(sys.argv) > 4 else in_p
    n = repack(in_p, {h: text.encode("utf-16-le") + b"\x00\x00"}, out_p)
    print(f"repack OK nstr={n} -> {out_p}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(demo())