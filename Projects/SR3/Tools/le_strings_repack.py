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
                  或 { hash u32, utf32le 文本, u32 0x00000000 }   ← 见下 ★★

★★ 载荷编码双轨 (2026-09-20): 结构完全相同, 但**字符串载荷步长可逐文件不同** ——
    SR3 实测 230 个真实文件**全部**是 UTF-16LE (2B/字符);
    SR4 common 有 20 张 UTF-32 (platform_ggp_* / platform_nx64_*),
    SR4 microsoft 全表 UTF-32。
    => 本模块**不再硬编码 UTF-16**: 读/写前一律先探测步长 (detect_file_step),
       否则面对 UTF-32 文件会静默读成垃圾 (旧版 read_with_bucket 正是如此)。
    铁律: **txt -> le_strings 之前必须先探测目标模板步长**, 按同一步长注入。

★★ 血的教训 (2026-09-20): offset 表条目是 **8 字节步长**, 不是 4 字节!
    旧版误用 4 字节步长 -> 把每条后面的「填充零 u32」也当成一个条目 ->
    真实条目的偏移被错位读取, 一半条目被读成 s_off==0 的「空槽」而被丢弃 ->
    写回文件桶内条目数减半、但 header.stringCount 不变 ->
    引擎按声明索引越界 -> 取到 NULL 字符串指针 -> 进程崩溃 (c0000005)。
    customize_us: 3061 条里 1405 条被丢, 文件从 130090B 缩水到 59020B (-55%)。
"""
import os
import struct


# ── 字符串载荷步长探测 ──────────────────────────────────────────
#   ★ SR3 实测全 UTF-16, 但结构与 SR4 完全一致; 为防未来出现 UTF-32 模板
#     (或误把 SR4 的 microsoft/主机牌文件当 SR3 处理) 而静默损坏, 这里补上
#     与 sr4le_repack.py **完全相同** 的三判据探测 + 守卫。

def detect_text_step(buf, nb, buckets):
    """探测字符串载荷步长: 2 (UTF-16LE) 或 4 (UTF-32LE)。

    判据 (由强到弱, 取第一个能给出结论的):

    A) 结构判据 (首选): UTF-32LE 下每码位高 2 字节恒 0, 故从载荷起点 +4 起,
       偏移 (4k+2, 4k+3) 位置上的非零字节数恒为 0; UTF-16LE 下两组都有非零。
       奇数半字位置全零 => UTF-32。

    A2) 条目末尾终止符宽度: 以槽长界定, 看首个 u16 NUL 是否恰为末 2 字节。
       UTF-16 -> 是; UTF-32 -> 否 (后面还有 2B 的 0)。

    B) 长度判据 (兜底): 按 2B 读几乎全单字符、按 4B 读几乎全多字符 => UTF-32。
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
    #   在**本条文本自身**范围内 (到首个 u16 NUL 为止) 检查每个 2 字节单元的
    #   「高半字是否恒为 0」:
    #     UTF-32: 每个码位 = {lo16, 00 00} -> 文本区内所有奇数半字恒 0, 且长度 %4==0
    #     UTF-16: 奇数半字是码位高字节, CJK 下非零 (如 ：=U+FF1A -> 1A FF)
    #   ★ 不要用「NUL 后还剩几字节」判断: 原位覆盖会把短译文剩余槽位清零,
    #     NUL 之后可能跟 2B 残留也可能跟 4B 终止符, 不可分。
    u32_votes = u16_votes = 0
    for a in starts[:120]:
        pay = buf[a + 4:nxt.get(a, len(buf))]
        k = 0
        while k + 1 < len(pay) and struct.unpack_from('<H', pay, k)[0] != 0:
            k += 2
        body = pay[:k]
        if len(body) < 4:
            continue
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

    txt -> le_strings 之前**必须先调用本函数**知道目标模板的编码,
    再据此把译文按相同步长写入 —— 否则会出现「按 UTF-16 往 UTF-32 模板写」
    这类静默损坏 (产物大幅缩水 / 界面缺字)。

    返回 2 (UTF-16LE) 或 4 (UTF-32LE)。
    """
    buf = open(path, "rb").read()
    if len(buf) < 12:
        raise ValueError(f"{path}: 文件过小, 不是合法 le_strings")
    fid, _ver, nb, _nstr = struct.unpack_from("<IHHI", buf, 0)
    if fid != 0xA84C7F73:
        raise ValueError(f"{path}: 非 le_strings (ID={fid:#x})")
    buckets = []
    for i in range(nb):
        base = 12 + i * 16
        if base + 16 > len(buf):
            raise ValueError(f"{path}: bucket[{i}] 表越界")
        count, _, offset, _ = struct.unpack_from("<IIII", buf, base)
        buckets.append((offset, count))
    return detect_text_step(buf, nb, buckets)


def _bucket_table(buf, path):
    """读 header + bucket 表, 返回 (fid, ver, nb, nstr, [(offset, count)])。"""
    if len(buf) < 12:
        raise ValueError(f"{path}: 文件过小, 不是合法 le_strings")
    fid, ver, nb, nstr = struct.unpack_from("<IHHI", buf, 0)
    if fid != 0xA84C7F73:
        raise ValueError(f"{path}: 非 le_strings (ID={fid:#x})")
    buckets = []
    for i in range(nb):
        base = 12 + i * 16
        if base + 16 > len(buf):
            raise ValueError(f"{path}: bucket[{i}] 表越界")
        count, _, off, _ = struct.unpack_from("<IIII", buf, base)
        buckets.append((off, count))
    return fid, ver, nb, nstr, buckets


def read_with_bucket(path, step=None):
    """返回 (fid, ver, nb, nstr, [(bucket_idx, hash, text_bytes)], step)
       text_bytes = 文本区 (含末尾终止符, UTF-16 为 2B / UTF-32 为 4B)

    step: 载荷步长。None => 自动探测 (detect_file_step)。
          ★ 校验产物时必须**显式传入写入步长**, 不能让产物自证。
    """
    buf = open(path, "rb").read()
    fid, ver, nb, nstr, buckets = _bucket_table(buf, path)
    if step is None:
        step = detect_text_step(buf, nb, buckets)
    term = 2 if step == 2 else 4
    fmt = "<H" if term == 2 else "<I"
    out = []
    total_count = 0
    for i, (off, count) in enumerate(buckets):
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
            while e + term - 1 < len(buf) and struct.unpack_from(fmt, buf, e)[0] != 0:
                e += term
            text_bytes = buf[s_off + 4: e + term]
            h = struct.unpack_from("<I", buf, s_off)[0]
            out.append((i, h, text_bytes))
    if total_count != nstr:
        raise ValueError(
            f"{path}: bucket 条目总数 {total_count} != header.stringCount {nstr} (文件不一致)")
    if len(out) != nstr:
        raise ValueError(f"{path}: 解析出 {len(out)} 条 != nstr {nstr}")
    return fid, ver, nb, nstr, out, step


def repack(in_path, pairs, out_path):
    """
    pairs: dict {hash: text_bytes_LE(末尾终止符)}
           None 或缺失 = 沿用原文件文本
    ★ 载荷步长按原文件自动探测; pairs 里的 text_bytes 必须已按**同一步长**编码。
    """
    pairs = dict(pairs)
    fid, ver, nb, nstr, entries, step = read_with_bucket(in_path)
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
    #    ★ 必须显式传入原文件步长 (不能让产物自证: 中文是 UTF-16 + 原位覆盖
    #      会在长串尾留 0, 自动探测会被干扰)。
    _f, _v, _nb, _n, back, _s = read_with_bucket(out_path, step=step)
    if len(back) != nstr:
        raise ValueError(f"{out_path}: 回读校验失败, 只读回 {len(back)}/{nstr} 条")
    return nstr


def repack_inplace(in_path, pairs, out_path):
    """
    原位覆盖版: 保持官方文件布局完全不变, 只把 pairs{hash: 新文本(含终止符)}
    对应条目的文本**在原 string 槽位原位改写** (不足的剩余字节清零)。
    要求每条新文本 <= 该条目原文本槽长(含终止符), 否则报错。
    适用: 引擎可能依赖 string 区线性布局, 全量 repack 重排有风险 (v3 崩溃疑因)。
    ★ 载荷步长按原文件自动探测; pairs 里的 bytes 必须已按**同一步长**编码。
    """
    buf = bytearray(open(in_path, "rb").read())
    fid, ver, nb, nstr, buckets = _bucket_table(buf, in_path)
    step = detect_text_step(buf, nb, buckets)
    term = 2 if step == 2 else 4
    fmt = "<H" if term == 2 else "<I"
    pairs = dict(pairs)
    replaced = {}
    for i, (off, count) in enumerate(buckets):
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
            # 原文本区: [s_off+4, e) 到全零终止单元
            e = s_off + 4
            while e + term - 1 < len(buf) and struct.unpack_from(fmt, buf, e)[0] != 0:
                e += term
            slot_len = (e + term) - (s_off + 4)   # 原文本字节数(含终止符)
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


def _make_sample(step, texts, hashes):
    """构造一个最小合法 le_strings; step ∈ {2,4} 决定载荷编码。"""
    nb, nstr = 1, len(texts) + 1     # +1 为第一个空槽
    head = 12 + 16 * nb + 8 * nstr
    start = (head + 3) & ~3
    buf = bytearray(start)
    struct.pack_into("<IHHI", buf, 0, 0xA84C7F73, 1, nb, nstr)
    term = b"\x00\x00" if step == 2 else b"\x00\x00\x00\x00"
    bucket_offsets = [0]             # 第一个是空槽
    cur = start
    for text, h in zip(texts, hashes):
        while cur & 3:
            cur += 1
            buf.append(0)
        bucket_offsets.append(cur)
        if step == 2:
            pay = text.encode("utf-16-le")
        else:
            pay = struct.pack(f"<{len(text)}I", *[ord(c) for c in text])
        buf += struct.pack("<I", h) + pay + term
        cur = len(buf)
    struct.pack_into("<IIII", buf, 12, nstr, 0, 12 + 16 * nb, 0)
    pos = 12 + 16 * nb
    for off in bucket_offsets:
        struct.pack_into("<II", buf, pos, off, 0)
        pos += 8
    return bytes(buf), nstr


def self_test():
    """自检: 对内置最小样例做 round-trip, 验证读/写在 8 字节 offset 表下自洽。

    用于 CI / 自动化构建前置校验, 不依赖任何游戏文件。
    ★ 覆盖 UTF-16LE 与 UTF-32LE 双步长 (SR3 实测全 UTF-16, 但结构同 SR4,
      这里一并验证以防未来出现 UTF-32 模板时静默损坏)。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for step in (2, 4):
            src = os.path.join(td, f"t{step}_us.le_strings")
            dst = os.path.join(td, f"t{step}_zh.le_strings")
            blob, nstr = _make_sample(step, ("HELLO", "WORLD"),
                                      (0x11111111, 0x22222222))
            open(src, "wb").write(blob)

            # 探测步长必须正确
            assert detect_file_step(src) == step, \
                f"self_test: 步长探测 {detect_file_step(src)} != 期望 {step}"

            _f, _v, _nb, got, entries, det_step = read_with_bucket(src)
            assert got == nstr, f"self_test(step={step}): 读出 {got} != {nstr}"
            assert det_step == step, f"self_test: 读出步长 {det_step} != {step}"
            assert sum(1 for e in entries if e[1] == 0) == 1, "self_test: 空槽数量不对"

            # 未命中译文的条目必须原样保留 (round-trip 保指纹)
            kept = [e for e in entries if e[1] == 0x11111111][0]
            if step == 4:
                assert kept[2] == struct.pack("<5I", *[ord(c) for c in "HELLO"]) + b"\x00" * 4, \
                    "self_test: UTF-32 条目未被原样保留"

            # 原位覆盖: 新文本必须按**同一步长**编码, 且产物步长不得改变
            if step == 2:
                newb = "你好".encode("utf-16-le") + b"\x00\x00"
            else:
                newb = struct.pack("<2I", ord("你"), ord("好")) + b"\x00" * 4
            repack_inplace(src, {0x11111111: newb}, dst)
            rr = read_with_bucket(dst, step=step)      # ★ 显式传步长校验
            assert rr[3] == nstr, "self_test(inplace): 条目数不守恒"
            assert rr[5] == step, "self_test: 产物步长被改变"
            assert rr[4][1][2] == newb, "self_test: 原位覆盖文本不符"

            # 全量重建 (空覆盖 = 纯 round-trip) 也必须保条目数
            repack(src, {}, dst)
            assert read_with_bucket(dst, step=step)[3] == nstr, \
                "self_test(repack): 条目数不守恒"
    print("[self_test] le_strings_repack OK: 8 字节 offset 表读/写自洽, "
          "条目数守恒, UTF-16/UTF-32 双步长均正确")


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
    # ★ 按目标模板步长编码 (不再硬编码 UTF-16)
    step = detect_file_step(in_p)
    if step == 4:
        newb = struct.pack(f"<{len(text)}I", *[ord(c) for c in text]) + b"\x00" * 4
    else:
        newb = text.encode("utf-16-le") + b"\x00\x00"
    n = repack(in_p, {h: newb}, out_p)
    print(f"repack OK nstr={n} (step={step}) -> {out_p}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(demo())