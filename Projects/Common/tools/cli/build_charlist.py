#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_charlist.py — 生成 DLL 启动时加载的 charlist.txt（字符清单）
================================================================================
用途
    把「本项目所有中文文本源」用到的非 ASCII 字符做去重 + 频率降序排列，
    产出一份纯文本清单，供 SR3R_I18N.asi / SR4R_I18N.asi 启动时加载并合并进
    字形层字符集（g_charSet / g_charFreq）。

    背景：DLL 的字形层字符集主要来自「词典加载时收集的译文用字」，但内核汉化
    固化文本、官方 le_data 用字、以及少量只出现在 le_string 译文里的字
    （如「齿」）不在词典路径上 —— charlist.txt 就是补这批缺字的通道。
    见 dllmain.cpp 的 LoadCharList()（SR3 行 612-678）。

输入（步骤 4：基于 resource\\{game}\\{lang}\\*.txt）
    Resource/{game}/{lang}/dict/*.txt          词典（le_strings 格式）
    Resource/{game}/{lang}/le_string/*.txt     le_string 译文（le_strings 格式）

输出
    release/{game}_{version}/scripts/charlist.txt

    格式（与 DLL 解析逻辑逐行对应）：
      - 纯 UTF-8（不写 BOM；DLL 两种都能读，但不写 BOM 更通用）
      - 前 4 行是 `;` 开头的注释（DLL 遇 `;` 即跳过该行余下内容）
      - 第 5 行是**一整行**字符，**按出现频率降序**排列（无分隔符）
        DLL 按字符在本行中的位置 rank 计算权重 CHARLIST_FREQ_BASE - rank，
        故**顺序即优先级** —— 越靠前的字在字号/容量不足时越优先保留。
      - 末尾一个换行

    过滤规则（与 DLL 侧一致，保证「写进去的一定会被读进来」）：
      - 只保留码位 >= 0x80 的字符（ASCII 由字体固定区覆盖，无需列）
      - 丢弃 0x200B~0x200D 零宽字符（水印/排版残留）
      - 丢弃空格、制表符
      - 丢弃控制字符

频率口径
    统计对象是**原始 UTF-8 文本的字符出现次数**，不是「译文字数」。
    理由是 DLL 只关心「字号容量不够时先保哪些字」，出现次数即最直接的近似。
    同一字符在 dict 与 le_string 中的次数**累加**（两个目录都是最终发布内容）。

退出码: 0=成功; 1=失败（供 CI 判定）。
"""
import os
import sys
import collections
import argparse

# ── 与 DLL 侧 LoadCharList() 完全一致的过滤条件 ────────────────────────────
ZERO_WIDTH = (0x200B, 0x200C, 0x200D)   # dllmain.cpp: cp >= 0x200B && cp <= 0x200D -> skip
SKIP_CHARS = frozenset(' \t')            # dllmain.cpp: ch == L' ' || ch == L'\t' -> skip

HEADER = (
    '; {tag}\n'
    '; Sources: scripts/dict + le_string (Resource/{game}/CHS)\n'
    '; {count} unique non-ASCII chars (drop: spaces/controls/zero-width), freq-desc\n'
    '; Loaded by DLL at startup, merged into g_charSet/g_charFreq\n'
)


def iter_source_files(src_dir):
    """产出 src_dir 下所有 .txt（按文件名排序，保证跨平台结果一致）。"""
    if not os.path.isdir(src_dir):
        return
    for f in sorted(os.listdir(src_dir)):
        if f.lower().endswith('.txt'):
            p = os.path.join(src_dir, f)
            if os.path.isfile(p):
                yield p


def collect(files):
    """统计所有文件中非 ASCII 字符的出现次数 -> Counter。

    返回 (counter, file_count, line_count)。
    """
    cnt = collections.Counter()
    nfiles = 0
    nlines = 0
    for p in files:
        try:
            # 用 utf-8-sig 兼容带 BOM 的文件；DLL 侧同样跳 BOM
            text = open(p, 'r', encoding='utf-8-sig').read()
        except UnicodeDecodeError as e:
            # 一个文件编码坏掉不应静默跳过 —— 那会让产出的字库悄悄缺字。
            raise ValueError(f'{p}: 不是合法 UTF-8 ({e})') from e
        nfiles += 1
        for line in text.split('\n'):
            nlines += 1
            for ch in line:
                cp = ord(ch)
                if cp < 0x80:
                    continue                     # ASCII / 控制字符
                if ZERO_WIDTH[0] <= cp <= ZERO_WIDTH[2]:
                    continue                     # 零宽字符
                if ch in SKIP_CHARS:
                    continue
                cnt[ch] += 1
    return cnt, nfiles, nlines


def sort_chars(cnt):
    """频率降序；同频按码位升序（保证构建可复现，字节级确定）。"""
    return [c for c, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], ord(kv[0])))]


def build(game, lang, out_path, tag, extra_dirs=()):
    """扫描 Resource/{game}/{lang}/ 下各目录，写出 charlist.txt。

    返回 (char_count, stat_lines)；失败抛 ValueError。
    """
    res = os.path.join('Resource', game, lang)
    dirs = ['dict', 'le_string'] + list(extra_dirs)

    all_files = []
    per_dir = []
    for d in dirs:
        fs = list(iter_source_files(os.path.join(res, d)))
        if not fs:
            raise ValueError(f'{game}/{lang}/{d}: 目录为空或不存在 ({os.path.join(res, d)})')
        per_dir.append((d, fs))
        all_files.extend(fs)

    if not all_files:
        raise ValueError(f'{game}/{lang}: 没有任何 .txt 源文件')

    cnt, nfiles, nlines = collect(all_files)
    chars = sort_chars(cnt)
    if not chars:
        raise ValueError(f'{game}/{lang}: 收集到 0 个非 ASCII 字符')

    # ── 自检 1: 写出的字符数 == 统计出的字符数 ────────────────────────────
    body = ''.join(chars)
    assert len(body) == len(chars), '内部错误: 字符序列长度不一致'

    text = HEADER.format(tag=tag, game=game, count=len(chars)) + body + '\n'

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)

    # ── 自检 2: 回读并逐一核对（不信任「无报错即成功」）────────────────────
    back = open(out_path, 'r', encoding='utf-8').read()
    got = []
    for ln in back.split('\n'):
        for ch in ln:
            if ch == ';':
                break
            cp = ord(ch)
            if cp < 0x80:
                continue
            if ZERO_WIDTH[0] <= cp <= ZERO_WIDTH[2] or ch in SKIP_CHARS:
                continue
            got.append(ch)
    if got != chars:
        raise ValueError(f'{out_path}: 回读校验失败 (写出 {len(chars)} 字, 读回 {len(got)} 字)')

    stats = [f'  {d:12s} {len(fs):3d} 文件' for d, fs in per_dir]
    stats.append(f'  {"合计":12s} {nfiles:3d} 文件 / {nlines} 行 / {len(chars)} 个非 ASCII 字符')
    return len(chars), stats


def main():
    ap = argparse.ArgumentParser(description='生成 DLL 用 charlist.txt')
    ap.add_argument('--game', required=True, choices=['SR3', 'SR4'],
                    help='游戏（SR3 / SR4，对应 Resource/<game>/）')
    ap.add_argument('--lang', default='CHS', help='语言目录（默认 CHS）')
    ap.add_argument('--out', required=True, help='输出 charlist.txt 的完整路径')
    ap.add_argument('--tag', default='', help='注释首行的标识（如版本号）')
    args = ap.parse_args()

    tag = args.tag or f'{args.game}R_I18N charset list v1'
    print(f'生成 charlist: {args.game}/{args.lang} -> {args.out}')
    try:
        n, stats = build(args.game, args.lang, args.out, tag)
    except Exception as e:
        print(f'  失败: {e}')
        return 1
    for s in stats:
        print(s)
    print(f'  已写出 {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
