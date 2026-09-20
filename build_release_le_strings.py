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

说明:
  - SR3 用 Projects/SR3/Tools/le_strings_repack.repack (裸 UTF-16LE, 8 字节 offset 表)
  - SR4 用 Projects/SR4/Tools/sr4le_repack.repack (经 charlist 反向映射; 本仓库 SR4 无 charlist_zh, 传 None=裸 UTF-16LE)
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


def count_cjk(buf):
    """统计 le_strings 文件中含 CJK 的字符串数 / 总字符串数 (用于验证汉化命中)。"""
    fid, ver, nb, nstr = struct.unpack_from('<IHHI', buf, 0)
    total = 0
    cjk = 0
    for i in range(nb):
        base = 12 + i * 16
        cnt, _, off, _ = struct.unpack_from('<IIII', buf, base)
        for j in range(cnt):
            so = off + j * 8
            s_off = struct.unpack_from('<I', buf, so)[0]
            if s_off == 0:
                continue
            e = s_off + 4
            while e + 1 < len(buf) and struct.unpack_from('<H', buf, e)[0] != 0:
                e += 2
            text = buf[s_off + 4:e].decode('utf-16-le', errors='replace')
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
        return
    if not os.path.isdir(txt_dir):
        print(f'[SKIP] {game}: 找不到中文 txt 目录 {txt_dir}')
        return

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
                except AssertionError as e:
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
            # 校验: 重新解析, 统计 CJK 命中
            try:
                buf = open(out_path, 'rb').read()
                cjk, tot = count_cjk(buf)
                if suf == suffixes[0]:
                    print(f'  {stem:24s} 条目={tot:5d} 中文串={cjk:5d} '
                          f'(翻译 {len(pairs)} 条) -> {out_name} 等 {len(suffixes)} 个')
            except Exception as e:
                fails.append((out_name, '校验:' + str(e)))
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
    r3 = build_game('SR3', ['zh', 'us'])
    r4 = build_game('SR4', ['zh', 'us'])
    print('\n全部完成.')
    print(f'  Release/SR3/common/misc: {len(os.listdir(os.path.join(ROOT,"Release","SR3","common","misc"))) if os.path.isdir(os.path.join(ROOT,"Release","SR3","common","misc")) else 0} 文件')
    print(f'  Release/SR4/common/misc: {len(os.listdir(os.path.join(ROOT,"Release","SR4","common","misc"))) if os.path.isdir(os.path.join(ROOT,"Release","SR4","common","misc")) else 0} 文件')


if __name__ == '__main__':
    main()
