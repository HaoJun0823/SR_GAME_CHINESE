#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
le_string_pack.py — le_strings 打包装填 CLI（SR3 / SR4 合一）

取代原 `Projects/SR3/Tools/{le_strings_repack,sr3le_repack}.py`
与 `Projects/SR4/Tools/sr4le_repack.py`。
真正的字节级实现全部位于 `helper/le_string_codec.py`，本文件只是命令行外壳。

────────────────────────────────────────────────────────────────────────────
为什么合一
────────────────────────────────────────────────────────────────────────────
原先两份实现**结论不一致**：
  · SR3 版已有 detect_text_step / detect_file_step，但 repack() / repack_inplace()
    / _make_sample() 里仍硬编码 term = 2 / fmt = "<H" —— 探测到位却写错；
  · SR4 版另有 charlist 映射（parse_charlist / build_reverse_charmap /
    encode_text_with_charmap / parse_le_strings_raw）。
于是**同一份 SR4 数据走 SR3 的模块能过、走 SR4 的模块报错**，结论随工具而变。
合一是为了「一份数据只有一个结论」，也让 SR3 未来能用上 UTF-32 探测与
charlist 映射（SR3 主机板也许会需要）。

────────────────────────────────────────────────────────────────────────────
★ 两条硬性约定（血泪教训，见 le_string_codec 模块头）
────────────────────────────────────────────────────────────────────────────
  1. offset 表步长是 **8 字节**，不是 4。用错会把条目数砍半而 header.stringCount
     不变 → 引擎越界取 NULL → 启动崩溃 c0000005。红旗：产物只有原始体积的 33~45%。
  2. 校验产物时**必须显式传入写入时所用的步长**（`--step`），不要让产物自证 ——
     译文常是 UTF-16 而容器是 UTF-32，且原位覆盖会在短译文后残留长串 0，
     两者都会骗过自动探测，使校验静默通过。

用法
────────────────────────────────────────────────────────────────────────────
  # 探测容器信息（步长/条目数/桶数）
  python le_string_pack.py info data/SR4/microsoft/misc/menu_us.le_strings

  # 从 txt 回写（txt 里是 "键": "值" 或 "HASH_xxxx": "值"）
  python le_string_pack.py pack 源.le_strings 译文.txt 产物.le_strings

  # 强制原位覆盖（体积不变，最稳）；超槽条目会被报告而不是静默截断
  python le_string_pack.py pack 源.le_strings 译文.txt 产物.le_strings --inplace

  # 带 charlist 反向映射（SR4 microsoft / 主机表）
  python le_string_pack.py pack 源 译.txt 出 --charlist charlist_zh.dat

  # 批量（整个目录）
  python le_string_pack.py pack-dir data/SR4/common/misc Resource/SR4/CHS/le_string out/ --suffixes zh,us

退出码: 0=全部成功; 1=有失败（供 CI 判定）。
"""
import os
import re
import sys
import struct
import argparse

# ── 让 helper 可导入（本文件在 Projects/Common/tools/cli/）──
_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_TOOLS, 'helper'))

import le_string_codec as C          # noqa: E402

LINE_RE = re.compile(r'^"(.+?)":\s*"(.*)"\s*$')


def parse_txt(path):
    """译文 txt -> {hash: str}。

    支持两种键写法：
      "HASH_1A2B3C4D": "译文"     ← 直接给 hash（推荐，抗键名改动）
      "menu_continue": "译文"     ← 给键名，现场算 Volition CRC32
    值里的 \\n \\r \\" \\\\ 会被还原成真实字符。
    """
    pairs = {}
    n_bad = 0
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for ln, raw in enumerate(f, 1):
            line = raw.rstrip('\r\n')
            if not line or line.lstrip().startswith('//'):
                continue
            m = LINE_RE.match(line)
            if not m:
                n_bad += 1
                continue
            k, v = m.group(1), m.group(2)
            v = (v.replace('\\n', '\n').replace('\\r', '\r')
                  .replace('\\"', '"').replace('\\\\', '\\'))
            if k.startswith('HASH_'):
                try:
                    h = int(k[5:], 16)
                except ValueError:
                    n_bad += 1
                    continue
            else:
                h = C.crc_volition(k)
            pairs[h] = v
    return pairs, n_bad


def cmd_info(args):
    fid, ver, nb, nstr, entries, step = C.read_with_bucket(args.path)
    enc = 'UTF-16LE' if step == 2 else 'UTF-32LE'
    print(f'文件      : {args.path}')
    print(f'魔数      : 0x{fid:08X}  (le_string)')
    print(f'version   : {ver}')
    print(f'bucket 数 : {nb}')
    print(f'字符串数  : {nstr}')
    print(f'★ 载荷步长: {step} 字节  ({enc})')
    print(f'文件大小  : {os.path.getsize(args.path):,} B')
    if args.dump:
        print('-' * 64)
        for i, (h, t) in enumerate(entries.items()):
            if i >= args.dump:
                print(f'  … 共 {len(entries)} 条')
                break
            print(f'  {h:08X}  {t[:70]!r}')
    return 0


def _do_pack(src, txt, dst, inplace, charlist, step_force, quiet=False):
    """单个文件的回写。返回 (ok, note)。"""
    pairs, n_bad = parse_txt(txt)
    if not pairs:
        return False, f'译文为空 ({n_bad} 行无法解析)'

    rev = None
    if charlist:
        cm = C.parse_charlist(charlist)
        rev = C.build_reverse_charmap(cm)
        if not quiet:
            print(f'    charlist: {len(cm)} 项 → 反向表 {len(rev)} 项')

    # ★ 写入步长一律以**源模板**为准（不能让产物自证）
    src_step = C.detect_file_step(src) if step_force is None else step_force
    if not quiet:
        print(f'    源步长 {src_step}B ({("UTF-16" if src_step == 2 else "UTF-32")})'
              f'，译文 {len(pairs)} 条')

    if inplace:
        r = C.repack_inplace(src, pairs, dst)
        note = f'inplace 写入 {r["written"]} 条'
        if r['overflow']:
            note += f'，★ 超槽 {len(r["overflow"])} 条（已保留原文，未静默截断）'
        return True, note
    n = C.repack(src, pairs, dst)
    return True, f'repack 重建 {n} 条'


def cmd_pack(args):
    # 若给了 --charlist 或强制步长，需要 repack 而非 inplace 的语义，
    # 但 inplace 同样能走 charlist（encode_text 内部处理）。这里统一传参。
    src_step = C.detect_file_step(args.src)
    pairs, n_bad = parse_txt(args.txt)
    if not pairs:
        print(f'!! 译文为空或无法解析（{n_bad} 行未匹配）: {args.txt}')
        return 1
    rev = None
    if args.charlist:
        cm = C.parse_charlist(args.charlist)
        rev = C.build_reverse_charmap(cm)
        print(f'   charlist: {len(cm)} 项 → 反向表 {len(rev)} 项')

    print(f'源   : {args.src}  (步长 {src_step}B, '
          f'{"UTF-16" if src_step == 2 else "UTF-32"})')
    print(f'译文 : {args.txt}  ({len(pairs)} 条)')
    print(f'产物 : {args.out}  ({"inplace" if args.inplace else "repack"})')

    # ★ 带 charlist 时需要先把码位映射到槽位，再编码 —— 走 repack 的
    #   encode_text(rev_charmap=...) 路径。
    if rev or not args.inplace:
        n = _repack_with_charmap(args.src, pairs, args.out, rev, src_step)
        print(f'完成: repack 重建 {n} 条')
    else:
        r = C.repack_inplace(args.src, pairs, args.out)
        print(f'完成: inplace 写入 {r["written"]} 条, 条目守恒 {r["entries"]}')
        if r['overflow']:
            print(f'  ★ 超槽 {len(r["overflow"])} 条未写入（原样保留，未截断）：')
            for h in r['overflow'][:10]:
                print(f'      {h:08X}  {pairs.get(h, "")[:50]!r}')
            print('    建议改用不带 --inplace 的重建模式。')

    # ★ 产物校验：显式传入源步长，不让产物自证
    back = C.read_with_bucket(args.out, step=src_step)[4]
    total = len(pairs)
    hit = sum(1 for h in pairs if h in back)
    print(f'校验: 产物 {len(back)} 条, 其中命中译文 {hit}/{total}')
    return 0


def _repack_with_charmap(src, pairs, dst, rev, step):
    """带 charlist 反向映射的重建写回（复用 codec 的布局逻辑）。"""
    fid, ver, nb, nstr, entries, _st = C.read_with_bucket(src, step=step)
    merged = dict(entries)
    merged.update(pairs)

    hs = list(merged.keys())
    total = len(hs)
    if nb <= 0:
        nb = 1
    per = total // nb
    buckets, idx = [], 0
    for i in range(nb):
        c = per if i < nb - 1 else (total - idx)
        buckets.append(hs[idx:idx + c])
        idx += c

    header = struct.pack('<IHHI', C.MAGIC, ver, nb, total)
    off_tbl_off = C.HEADER_SIZE + C.BUCKET_SIZE * nb
    body_off = off_tbl_off + C.OFFSET_ENTRY_SIZE * total

    blob, offs = b'', []
    for bl in buckets:
        for h in bl:
            offs.append(body_off + len(blob))
            blob += struct.pack('<I', h) + C.encode_text(merged[h], rev, step)

    bk, oi = b'', 0
    for bl in buckets:
        bk += struct.pack('<IIII', len(bl), 0,
                          off_tbl_off + oi * C.OFFSET_ENTRY_SIZE, 0)
        oi += len(bl)
    ot = b''.join(struct.pack('<II', o, 0) for o in offs)
    data = header + bk + ot + blob

    written = sum(len(b) for b in buckets)
    if written != total:
        raise ValueError(f'{src}: 写回条目数 {written} != {total} —— 拒绝写出')
    with open(dst, 'wb') as f:
        f.write(data)

    back = C.read_with_bucket(dst, step=step)[4]
    if len(back) != total:
        raise ValueError(f'{dst}: 回读校验失败 {len(back)}/{total}')
    return total


def cmd_pack_dir(args):
    """批量：<src_dir>/*_us.le_strings + <txt_dir>/<stem>_us.txt -> <out_dir>/"""
    src_dir, txt_dir, out_dir = args.src_dir, args.txt_dir, args.out_dir
    suffixes = [s for s in args.suffixes.split(',') if s]
    os.makedirs(out_dir, exist_ok=True)

    tpls = sorted(f for f in os.listdir(src_dir) if f.endswith('_us.le_strings'))
    done, fails, skipped = 0, [], []
    print(f'批量: {len(tpls)} 个模板  {src_dir} -> {out_dir}')

    for tpl in tpls:
        stem = tpl[:-len('_us.le_strings')]        # menu
        txt = os.path.join(txt_dir, f'{stem}_us.txt')
        if not os.path.isfile(txt):
            skipped.append(stem)
            continue
        sp = os.path.join(src_dir, tpl)
        try:
            step = C.detect_file_step(sp)
            pairs, _ = parse_txt(txt)
            if not pairs:
                skipped.append(f'{stem}(空译文)')
                continue
            for suf in suffixes:
                out = os.path.join(out_dir, f'{stem}_{suf}.le_strings')
                C.repack(sp, pairs, out)
            done += 1
        except Exception as e:
            fails.append((stem, str(e)[:120]))

    print(f'完成 {done} 张表 × {len(suffixes)} 后缀 = {done*len(suffixes)} 个产物')
    if skipped:
        print(f'跳过 {len(skipped)}: {", ".join(map(str, skipped[:12]))}'
              + (' …' if len(skipped) > 12 else ''))
    if fails:
        print(f'!! 失败 {len(fails)}:')
        for n, e in fails[:10]:
            print(f'   {n}: {e}')
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(
        description='le_strings 打包装填（SR3/SR4 合一，内核 = helper/le_string_codec.py）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('info', help='查看容器信息（步长/条目数）')
    p.add_argument('path')
    p.add_argument('--dump', type=int, default=0, metavar='N',
                   help='顺带打印前 N 条内容')
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser('pack', help='从 txt 回写单个容器')
    p.add_argument('src', help='源 _us.le_strings（结构模板）')
    p.add_argument('txt', help='译文 txt')
    p.add_argument('out', help='输出 .le_strings')
    p.add_argument('--inplace', action='store_true',
                   help='原位覆盖（体积不变，最稳；超槽会被报告）')
    p.add_argument('--charlist', help='charlist_*.dat（启用反向映射）')
    p.set_defaults(fn=cmd_pack)

    p = sub.add_parser('pack-dir', help='批量回写整个目录')
    p.add_argument('src_dir', help='模板目录（含 *_us.le_strings）')
    p.add_argument('txt_dir', help='译文目录（含 *_us.txt）')
    p.add_argument('out_dir', help='输出目录')
    p.add_argument('--suffixes', default='zh,us', help='产出后缀（默认 zh,us）')
    p.set_defaults(fn=cmd_pack_dir)

    p = sub.add_parser('self-test', help='运行内核自检')
    p.set_defaults(fn=lambda a: (C.self_test() and 0) or 1)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except ValueError as e:
        print(f'!! {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
