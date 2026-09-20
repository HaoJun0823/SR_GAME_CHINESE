#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_release_le_strings.py — 从 data/ 原始 le_string 模板 + Resource 中文 txt 封装汉化 le_string
================================================================================================
输入:
  data/<game>/common/misc/<name>_us.le_strings   原始英文 le_string (结构模板)
  Resource/<game>/CHS/le_string/<name>_us.txt    中文翻译 (HASH_xxxx -> 中文)
输出:
  Release/<game>/common/misc/<name>_zh.le_strings   规范中文 locale 文件
  Release/<game>/common/misc/<name>_us.le_strings   覆盖英文槽的兜底(内容同 _zh; SR4 无 zh locale 时靠它生效)

流程 (自动化构建):
  1) 前置自检: 调用两侧 repack 模块的 self_test(), 验证 8 字节 offset 表读/写自洽;
  2) 构建: 对每张表优先原位覆盖(repack_inplace), 超槽则降级全量重建(不丢条目);
  3) 产物校验: 逐个文件全量结构自检 (magic / bucket 总和 == stringCount /
     offset 表长度 / 回读条目数), 任一失败则打印并 [非零退出];
  4) 汇总: 命中 CJK 数、超槽降级清单、失败清单。

退出码: 0=全部成功; 1=有文件构建或校验失败 (供 CI / 自动化判定)。

说明:
  - SR3 用 Projects/SR3/Tools/le_strings_repack (裸 UTF-16LE, 8 字节 offset 表)
  - SR4 用 Projects/SR4/Tools/sr4le_repack  (经 charlist 反向映射; 本仓库 SR4 无 charlist_zh, 传 None=裸 UTF-16LE)
  - 两个游戏同时产出 _zh 与 _us, 保证 loose 加载无论游戏 locale 如何都能显示中文。
"""
import os
import re
import sys
import struct
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'Projects', 'SR4', 'Tools'))
sys.path.insert(0, os.path.join(ROOT, 'Projects', 'SR3', 'Tools'))
import sr4le_repack
import le_strings_repack as sr3_lr

LINE_RE = re.compile(r'^"(.+?)":\s*"(.*)"\s*$')
MAGIC = 0xA84C7F73


def crc_volition(s):
    tbl = [0] * 256
    for i in range(256):
        c = i
        for _ in range(8):
            c = (0xEDB88320 ^ (c >> 1)) if (c & 1) else (c >> 1)
        tbl[i] = c & 0xFFFFFFFF
    crc = 0
    for ch in s.lower():
        b = ord(ch) & 0xFF
        crc = tbl[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFFFFFF


def parse_txt(path):
    """txt -> {hash: str}"""
    d = {}
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            ln = raw.rstrip('\r\n')
            if not ln:
                continue
            m = LINE_RE.match(ln)
            if not m:
                continue
            k = m.group(1)
            t = m.group(2)
            t = t.replace('\\n', '\n').replace('\\r', '\r').replace('\\"', '"').replace('\\\\', '\\')
            h = int(k[5:], 16) if k.startswith('HASH_') else crc_volition(k)
            d[h] = t
    return d


def parse_le_strings(path):
    """解析 le_strings -> (nb, nstr, [text...]); 结构非法时抛 ValueError。

    与 repack 模块读路径同源逻辑, 用于构建后独立校验 (不依赖被校验对象的自检)。
    """
    buf = open(path, 'rb').read()
    if len(buf) < 12:
        raise ValueError('文件过小 (<12B)')
    fid, ver, nb, nstr = struct.unpack_from('<IHHI', buf, 0)
    if fid != MAGIC:
        raise ValueError(f'魔数错 ID=0x{fid:08X} (期望 0x{MAGIC:08X})')
    if 12 + 16 * nb + 8 * nstr > len(buf):
        raise ValueError(f'头部声明超出文件大小 (nb={nb} nstr={nstr} size={len(buf)})')
    texts = []
    total = 0
    for i in range(nb):
        cnt, _, off, _ = struct.unpack_from('<IIII', buf, 12 + i * 16)
        total += cnt
        for j in range(cnt):
            so = off + j * 8
            if so + 4 > len(buf):
                raise ValueError(f'bucket[{i}] offset 表越界 @0x{so:X}')
            s_off = struct.unpack_from('<I', buf, so)[0]
            if s_off == 0:
                texts.append('')
                continue
            if s_off + 4 > len(buf):
                raise ValueError(f'bucket[{i}] 字符串偏移越界 @0x{s_off:X}')
            e = s_off + 4
            while e + 1 < len(buf) and struct.unpack_from('<H', buf, e)[0] != 0:
                e += 2
            texts.append(buf[s_off + 4:e].decode('utf-16-le', errors='replace'))
    if total != nstr:
        raise ValueError(f'bucket 条目总数 {total} != header.stringCount {nstr}')
    if len(texts) != nstr:
        raise ValueError(f'解析出 {len(texts)} 条 != nstr {nstr}')
    return nb, nstr, texts


def count_cjk(texts):
    """统计含 CJK/全角标点的字符串数 / 总字符串数 (用于验证汉化命中)。"""
    total = 0
    cjk = 0
    for text in texts:
        if not text:
            continue
        total += 1
        if any('\u4e00' <= c <= '\u9fff' for c in text) or \
           any('\u3000' <= c <= '\u303f' for c in text) or \
           any('\uff00' <= c <= '\uffef' for c in text):
            cjk += 1
    return cjk, total


def build_game(game, suffixes):
    data_misc = os.path.join(ROOT, 'data', game, 'common', 'misc')
    txt_dir = os.path.join(ROOT, 'Resource', game, 'CHS', 'le_string')
    out_dir = os.path.join(ROOT, 'Release', game, 'common', 'misc')

    if not os.path.isdir(data_misc):
        print(f'[SKIP] {game}: 找不到 data 模板目录 {data_misc}')
        return 0, []
    if not os.path.isdir(txt_dir):
        print(f'[SKIP] {game}: 找不到中文 txt 目录 {txt_dir}')
        return 0, []

    # 收集 _us 模板 与 中文 txt
    us_templates = {}
    for f in os.listdir(data_misc):
        if f.endswith('_us.le_strings'):
            us_templates[f[:-len('.le_strings')]] = os.path.join(data_misc, f)
    txts = {}
    for f in os.listdir(txt_dir):
        if f.endswith('.txt'):
            txts[f[:-len('.txt')]] = os.path.join(txt_dir, f)

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)

    print(f'\n===== {game} =====')
    print(f'  模板(_us): {len(us_templates)}  中文txt: {len(txts)}')

    total_done = 0
    fails = []
    _overslot = {}          # {stem: [超槽说明]} -> inplace 失败降级全量重建的记录
    for stem, tpl_path in sorted(us_templates.items()):
        if stem not in txts:
            print(f'  [跳过] {stem}: 无对应中文 txt')
            continue
        pairs = parse_txt(txts[stem])
        if not pairs:
            print(f'  [跳过] {stem}: 中文 txt 为空')
            continue
        # 桩文件检测: 若大量 value 为 <=1 字符(占位符), 视为未翻译, 跳过以免发布垃圾
        stub = sum(1 for v in pairs.values() if len(v) <= 1) / len(pairs)
        if stub > 0.5:
            print(f'  [跳过] {stem}: 检测到未翻译占位桩 (单字符值占比 {stub:.0%}), 不发布')
            continue
        # SR3 charlist_zh 可选; SR4 本仓库无 charlist_zh -> None
        charmap = None
        if game == 'SR3':
            zh_cl = os.path.join(data_misc, 'charlist_zh.dat')
            if os.path.exists(zh_cl):
                charmap = sr4le_repack.parse_charlist(zh_cl)
        # 编码 pairs: SR4 用 str, SR3 用 utf16le 字节
        #
        # ★★ 2026-09-20 修复: SR3 也改为「优先原位覆盖(repack_inplace)」。
        #   原因: 旧版直接用 repack() 全量重建, 而 repack 内部 offset 表步长曾误用
        #   4 字节, 导致一半条目被丢弃、header.stringCount 不变 -> 引擎按声明索引越界
        #   -> 取到 NULL 字符串指针 -> 启动崩溃 (c0000005)。
        #   现在两条路径都已修好步长, 且都带「写后自检 + 回读校验」。
        #   默认 inplace: 布局与原版完全一致 (字节级可控), 引擎零风险。
        #   若个别条目中文比英文长而超槽, 自动降级为全量 repack (保条目不丢, 并记录)。
        if game == 'SR4':
            repack_pairs = pairs

            def repack_fn(tp, pp, op, _cm=charmap):
                """SR4: 先试原位覆盖; 超槽则降级全量重建 (不丢条目)。"""
                try:
                    sr4le_repack.repack_inplace(tp, pp, op, charmap=_cm)
                    return 'inplace'
                except ValueError as e:
                    _overslot.setdefault(stem, []).append(str(e))
                    sr4le_repack.repack(tp, pp, op, charmap=_cm)
                    return 'repack(降级)'
        else:
            repack_pairs = {h: (t.encode('utf-16-le') + b'\x00\x00') for h, t in pairs.items()}

            def repack_fn(tp, pp, op):
                """SR3: 先试原位覆盖; 超槽则降级全量重建 (不丢条目)。"""
                try:
                    sr3_lr.repack_inplace(tp, pp, op)
                    return 'inplace'
                except ValueError as e:
                    _overslot.setdefault(stem, []).append(str(e))
                    sr3_lr.repack(tp, pp, op)
                    return 'repack(降级)'

        base_ok = True
        for suf in suffixes:
            out_name = stem[:-3] + '_' + suf + '.le_strings'   # menu_us -> menu_zh / menu_us
            out_path = os.path.join(out_dir, out_name)
            try:
                repack_fn(tpl_path, repack_pairs, out_path)
            except Exception as e:
                fails.append((out_name, str(e)))
                base_ok = False
                continue
            # ★★ 产物全量结构自检: 独立解析产出的文件, 核对内部一致性。
            #    任一不一致都说明 repack 出错, 必须暴露而不是静默发布 (2026-09-20 事故教训)。
            try:
                nb, tot, texts = parse_le_strings(out_path)
                cjk, real = count_cjk(texts)
                if suf == suffixes[0]:
                    print(f'  {stem:24s} 条目={tot:5d} 中文串={cjk:5d} '
                          f'(翻译 {len(pairs)} 条) -> {out_name} 等 {len(suffixes)} 个')
            except Exception as e:
                fails.append((out_name, '结构自检: ' + str(e)))
                base_ok = False
        if base_ok:
            total_done += 1

    print(f'  完成: {total_done} 个文件 -> {out_dir}')
    if _overslot:
        print(f'  [注意] {len(_overslot)} 个文件有中文超槽, 已自动降级为全量重建 (条目不丢):')
        for stem, msgs in sorted(_overslot.items()):
            print(f'    {stem}: {len(msgs)} 条  {msgs[0][:80]}')
    if fails:
        print('  失败:')
        for n, e in fails:
            print(f'    {n}: {e}')
    return total_done, fails


def main():
    print('构建汉化 le_string -> Release/ (区分 SR3 / SR4)')

    # ── 1) 前置自检: 两侧 repack 模块的 8 字节 offset 表读/写自洽 ──────────
    print('\n[1/3] 前置自检 (repack 模块 round-trip)')
    try:
        sr3_lr.self_test()
        sr4le_repack.self_test()
    except Exception as e:
        print(f'  前置自检失败: {e}')
        print('\n构建中止 (自检未通过, 不产出任何文件).')
        return 1

    # ── 2) 构建 ──────────────────────────────────────────────────────────
    print('\n[2/3] 构建')
    r3 = build_game('SR3', ['zh', 'us'])
    r4 = build_game('SR4', ['zh', 'us'])

    # ── 3) 汇总 ──────────────────────────────────────────────────────────
    print('\n[3/3] 汇总')
    n3 = len(os.listdir(os.path.join(ROOT, "Release", "SR3", "common", "misc"))) \
        if os.path.isdir(os.path.join(ROOT, "Release", "SR3", "common", "misc")) else 0
    n4 = len(os.listdir(os.path.join(ROOT, "Release", "SR4", "common", "misc"))) \
        if os.path.isdir(os.path.join(ROOT, "Release", "SR4", "common", "misc")) else 0
    print(f'  Release/SR3/common/misc: {n3} 文件')
    print(f'  Release/SR4/common/misc: {n4} 文件')

    all_fails = []
    for r in (r3, r4):
        if r and r[1]:
            all_fails.extend(r[1])
    if all_fails:
        print(f'\n构建完成但有 {len(all_fails)} 项失败 —— 退出码 1')
        return 1
    print('\n全部完成 (产物结构自检全部通过)。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
