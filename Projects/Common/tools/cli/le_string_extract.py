#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
le_string_extract.py — le_strings 提取为可读 txt（SR3 / SR4 合一）

取代原 `Projects/SR3/Tools/sr3le_extract.py` 与 `Projects/SR4/Tools/sr4le_extract.py`
（这两份**函数签名完全相同**，diff 仅 52 行且几乎全是文档字符串差异 —— 确证
SR4 从 SR3 派生）。字节级实现在 `helper/le_string_codec.py`。

────────────────────────────────────────────────────────────────────────────
提取用途
────────────────────────────────────────────────────────────────────────────
  1. 从原版游戏提取 `_us` 模板文本 —— 作为翻译工作的**键集基准**；
  2. 提取已汉化产物做 QA 比对（确认哪些键还是英文）；
  3. 生成带 hash 的 txt（`"HASH_xxxx": "原文"`），供 le_string_pack 回填。

★ 编码：本工具用 `detect_file_step()` 逐文件探测步长（2=UTF-16 / 4=UTF-32）。
  主机分支表（`platform_ggp_*` / `platform_nx64_*`）与 SR4 microsoft 全表
  都是 UTF-32，若按 UTF-16 读会**每条只剩 1 个字符**（遇到高字节 0x0000 就停），
  从而把正常文本表误判成「单字符映射表」—— 这正是历史上踩过的坑。
  详见 le_string_codec 模块头 §二/§三。

用法
────────────────────────────────────────────────────────────────────────────
  # 单文件提取为 txt（键用真实键名+hash 双列，或只 hash）
  python le_string_extract.py one data/SR4/microsoft/misc/menu_us.le_strings out.txt

  # 批量提取整个目录
  python le_string_extract.py dir data/SR4/microsoft/misc out_dir/

  # 只列键与 hash，不导出文本（做键集审计）
  python le_string_extract.py keys data/SR4/common/misc/menu_us.le_strings

  # 对比两个容器的键集差异（换源/换版的常见需求）
  python le_string_extract.py diff a.le_strings b.le_strings

  # 带 charlist 正向映射（把槽位码位换回真实字符）
  python le_string_extract.py one x.le_strings out.txt --charlist charlist_zh.dat

退出码: 0=成功; 1=失败。
"""
import os
import sys
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_TOOLS, 'helper'))

import le_string_codec as C          # noqa: E402


def load_entries(path, charlist=None, step=None):
    """读容器 -> (entries, step)。charlist 非空时把槽位码位换回真实字符。"""
    fid, ver, nb, nstr, entries, st = C.read_with_bucket(path, step=step)
    if charlist:
        cm = C.parse_charlist(charlist)     # {slot: codepoint}
        fixed = {}
        for h, t in entries.items():
            fixed[h] = ''.join(chr(cm[ord(c)]) if ord(c) in cm else c for c in t)
        entries = fixed
    return entries, st


def _write_txt(entries, out_path, style, step):
    """写出 txt。style: hash | named | both"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    enc = 'UTF-16' if step == 2 else 'UTF-32'
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(f'// le_strings dump  ({os.path.basename(out_path)})\n')
        f.write(f'// 条目数 {len(entries)}  载荷 {enc}LE ({step}B/码位)\n')
        f.write('// 格式: "HASH_xxxxxxxx": "文本"\n')
        for h, t in entries.items():
            esc = (t.replace('\\', '\\\\').replace('"', '\\"')
                    .replace('\n', '\\n').replace('\r', '\\r'))
            f.write(f'"HASH_{h:08X}": "{esc}"\n')
    return len(entries)


def cmd_one(args):
    entries, step = load_entries(args.path, args.charlist)
    n = _write_txt(entries, args.out, args.style, step)
    enc = 'UTF-16LE' if step == 2 else 'UTF-32LE'
    print(f'{args.path}')
    print(f'  → {args.out}')
    print(f'  {n} 条, 载荷 {enc} ({step}B)')
    return 0


def cmd_dir(args):
    os.makedirs(args.out_dir, exist_ok=True)
    files = sorted(f for f in os.listdir(args.src_dir)
                   if f.endswith('.le_strings'))
    done, fails = 0, []
    cl = getattr(args, 'charlist', None)
    for f in files:
        sp = os.path.join(args.src_dir, f)
        # 产出 <stem>.txt（剥掉 .le_strings）
        stem = f[:-len('.le_strings')]
        op = os.path.join(args.out_dir, f'{stem}.txt')
        try:
            entries, step = load_entries(sp, cl)
            _write_txt(entries, op, args.style, step)
            done += 1
        except Exception as e:
            fails.append((f, str(e)[:120]))
    print(f'完成 {done}/{len(files)} 个文件 → {args.out_dir}')
    if fails:
        print(f'!! 失败 {len(fails)}:')
        for n, e in fails[:10]:
            print(f'   {n}: {e}')
        return 1
    return 0


def cmd_keys(args):
    entries, step = load_entries(args.path, getattr(args, 'charlist', None))
    enc = 'UTF-16LE' if step == 2 else 'UTF-32LE'
    print(f'{args.path}: {len(entries)} 条, {enc} ({step}B)')
    for h, t in entries.items():
        print(f'  {h:08X}  {t[:80]!r}')
    return 0


def cmd_diff(args):
    """对比键集差异。用于「模板 vs 译文源」「旧版 vs 新版」。"""
    a, sa = load_entries(args.a)
    b, sb = load_entries(args.b)
    ka, kb = set(a), set(b)
    only_a = sorted(ka - kb)
    only_b = sorted(kb - ka)
    both = ka & kb
    same = sum(1 for h in both if a[h] == b[h])
    diff = len(both) - same

    print(f'A: {args.a}  {len(a)} 条 (步长 {sa})')
    print(f'B: {args.b}  {len(b)} 条 (步长 {sb})')
    print(f'交集 {len(both)}  仅A {len(only_a)}  仅B {len(only_b)}')
    print(f'交集内 文本相同 {same}  文本不同 {diff}')
    if only_a:
        print(f'\n仅 A 有的键 ({len(only_a)}), 前 15:')
        for h in only_a[:15]:
            print(f'  {h:08X}  {a[h][:60]!r}')
    if only_b:
        print(f'\n仅 B 有的键 ({len(only_b)}), 前 15:')
        for h in only_b[:15]:
            print(f'  {h:08X}  {b[h][:60]!r}')
    return 0


def main():
    ap = argparse.ArgumentParser(
        description='le_strings 提取（SR3/SR4 合一，内核 = helper/le_string_codec.py）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('one', help='单个容器 → txt')
    p.add_argument('path')
    p.add_argument('out')
    p.add_argument('--style', default='hash', choices=['hash', 'named', 'both'])
    p.add_argument('--charlist', help='charlist_*.dat（槽位换回真实字符）')
    p.set_defaults(fn=cmd_one)

    p = sub.add_parser('dir', help='整个目录 → txt')
    p.add_argument('src_dir')
    p.add_argument('out_dir')
    p.add_argument('--style', default='hash', choices=['hash', 'named', 'both'])
    p.add_argument('--charlist')
    p.set_defaults(fn=cmd_dir)

    p = sub.add_parser('keys', help='列出全部键（键集审计）')
    p.add_argument('path')
    p.add_argument('--charlist')
    p.set_defaults(fn=cmd_keys)

    p = sub.add_parser('diff', help='对比两个容器的键集')
    p.add_argument('a')
    p.add_argument('b')
    p.set_defaults(fn=cmd_diff)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except ValueError as e:
        print(f'!! {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
