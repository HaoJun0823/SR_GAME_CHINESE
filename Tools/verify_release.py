#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_release.py — 独立校验 release/{game}_{version}/ 产物完整性
================================================================================
CI 里在 build_release.py 之后**再独立跑一遍**（不复用构建脚本的内部状态），
把「包能否真的装到游戏里跑起来」这件事逐项断言清楚。

校验项（每个包）
    1. 目录结构        scripts/ 与 update/ 存在
    2. ASI 产物        scripts/{G}R_I18N.asi 存在、是 x64 PE、体积合理、
                       且**没有动态 CRT 依赖**（/MT 静态链接的证据）
    3. 配置 ini        scripts/{G}R_I18N.ini 存在，且与 asi 同基名
                       （DLL 按「自身模块名.ini」查找，改名会导致配置读不到）
    4. update/ le_strings  数量 >= 20，且**逐个做结构自检**：
                       魔数 0xA84C7F73 / bucket 条目总数 == header.stringCount /
                       offset 表长度 / 回读条目数 —— 与 8 字节步长事故同源的检查
    5. scripts/dict/  至少 1 个 txt，且每个都能按词典行语法解析出条目
    6. charlist.txt   存在；字符数落在合理区间（>=100），回读自洽
    7. 字体           至少 1 个 .ttf/.otf，且 ini 里 font_file 指向的文件确实在
    8. ASI Loader     winmm.dll 存在且 > 100 KB
    9. 说明与许可     必读说明.txt 非空；License.txt 非空且含各原始许可名

用法
    python Tools/verify_release.py release
    python Tools/verify_release.py release --strict      # 任一警告也当失败

退出码: 0=全部通过; 1=有失败项。
"""
import os
import re
import sys
import glob
import struct
import argparse

MAGIC = 0xA84C7F73
# 词典行格式（与 dllmain.cpp ParseLeLine 对应）：
#     "KEY": "译文";        —— 行尾分号可有可无（部分文件带，部分不带）
LINE_RE = re.compile(r'^"(.+?)":\s*"(.*)"\s*;?\s*$')

# 期望的包名 -> 说明
EXPECTED = {
    'sr3r_common': 'SR3 (Steam/GOG/EPIC)',
    'sr4r_common': 'SR4 (Steam/GOG/EPIC)',
    'sr4r_microsoft': 'SR4 (Microsoft Store / MSIXVC)',
}

RESULT_OK = 'OK'
RESULT_WARN = 'WARN'
RESULT_FAIL = 'FAIL'


class Report:
    def __init__(self):
        self.rows = []          # (pkg, item, level, detail)
        self.fail = 0
        self.warn = 0

    def add(self, pkg, item, level, detail=''):
        self.rows.append((pkg, item, level, detail))
        if level == RESULT_FAIL:
            self.fail += 1
        elif level == RESULT_WARN:
            self.warn += 1
        mark = {RESULT_OK: '  ok  ', RESULT_WARN: ' warn ', RESULT_FAIL: ' FAIL '}[level]
        line = f'{mark} [{pkg}] {item}'
        if detail:
            line += f'  — {detail}'
        print(line, flush=True)


# ══════════════════════════════════════════════════════════════════════════
# le_strings 结构自检（与 repack 模块读路径同源，但**独立实现**）
# ══════════════════════════════════════════════════════════════════════════
def check_le_strings(path):
    """返回 (ok, detail)。ok=False 时 detail 是原因。"""
    buf = open(path, 'rb').read()
    if len(buf) < 12:
        return False, f'文件过小 {len(buf)}B'
    fid, ver, nb, nstr = struct.unpack_from('<IHHI', buf, 0)
    if fid != MAGIC:
        return False, f'魔数错 0x{fid:08X}'
    need = 12 + 16 * nb + 8 * nstr
    if need > len(buf):
        return False, f'头部声明 {need}B 超出文件 {len(buf)}B (nb={nb} nstr={nstr})'
    total = 0
    for i in range(nb):
        cnt, _pad, off, _pad2 = struct.unpack_from('<IIII', buf, 12 + i * 16)
        total += cnt
        for j in range(cnt):
            so = off + j * 8                       # ★ 8 字节步长
            if so + 4 > len(buf):
                return False, f'bucket[{i}] offset 表越界 @0x{so:X}'
            s_off = struct.unpack_from('<I', buf, so)[0]
            if s_off == 0:
                continue
            if s_off + 4 > len(buf):
                return False, f'bucket[{i}] 字符串偏移越界 @0x{s_off:X}'
            e = s_off + 4
            while e + 1 < len(buf) and struct.unpack_from('<H', buf, e)[0] != 0:
                e += 2
    if total != nstr:
        return False, f'bucket 条目总数 {total} != header.stringCount {nstr}'
    return True, f'buckets={nb} strings={nstr}'


# ══════════════════════════════════════════════════════════════════════════
def is_x64_pe(path):
    buf = open(path, 'rb').read(4096)
    if len(buf) < 0x40 or buf[:2] != b'MZ':
        return False, '不是 PE (无 MZ)'
    pe = struct.unpack_from('<I', buf, 0x3c)[0]
    if pe + 6 > len(buf):
        return False, 'PE 头越界'
    if buf[pe:pe + 4] != b'PE\0\0':
        return False, '不是 PE signature'
    mach = struct.unpack_from('<H', buf, pe + 4)[0]
    if mach != 0x8664:
        return False, f'machine=0x{mach:04X} (期望 0x8664=x64)'
    opt = pe + 24
    subsys = struct.unpack_from('<H', buf, opt + 68)[0]
    return True, f'x64, Subsystem={subsys}'


def has_dynamic_crt(path):
    """检测动态 CRT 依赖；/MT 静态链接时应当没有这些导入。"""
    b = open(path, 'rb').read()
    marks = [b'VCRUNTIME140', b'MSVCP140', b'ucrtbase.dll', b'api-ms-win-crt-']
    return [m.decode() for m in marks if m in b]


def check_ini(ini_path, asi_name, scripts_dir):
    """校验 ini 与 asi 同基名，且 font_file 指向的文件存在。"""
    if not os.path.isfile(ini_path):
        return False, f'缺少 {os.path.basename(ini_path)}'
    if os.path.splitext(os.path.basename(ini_path))[0] != os.path.splitext(asi_name)[0]:
        return False, f'ini 基名与 asi 不一致：{os.path.basename(ini_path)} vs {asi_name}'
    txt = open(ini_path, 'r', encoding='utf-8-sig', errors='replace').read()
    m = re.search(r'^\s*font_file\s*=\s*(\S.*?)\s*$', txt, re.M)
    if not m:
        return False, 'ini 里找不到 font_file'
    font = m.group(1).strip()
    if not os.path.isfile(os.path.join(scripts_dir, font)):
        return False, f'font_file={font} 在 scripts/ 下不存在'
    # charlist_file 也要指向真实文件（DLL 会按此名加载）
    m2 = re.search(r'^\s*charlist_file\s*=\s*(\S.*?)\s*$', txt, re.M)
    if m2:
        cl = m2.group(1).strip()
        if cl and not os.path.isfile(os.path.join(scripts_dir, cl)):
            return False, f'charlist_file={cl} 在 scripts/ 下不存在'
    return True, f'font_file={font}'


def count_dict_entries(path):
    """按词典行语法统计可解析条目数（'KEY': 'VAL' 或 // 注释）。"""
    n = 0
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith('//') or ln.startswith(';'):
                continue
            if LINE_RE.match(ln):
                n += 1
    return n


def count_charlist_chars(path):
    """按 DLL 的 LoadCharList 规则数出有效字符数。"""
    txt = open(path, 'r', encoding='utf-8-sig', errors='replace').read()
    n = 0
    for ln in txt.split('\n'):
        for ch in ln:
            if ch == ';':
                break
            cp = ord(ch)
            if cp < 0x80:
                continue
            if 0x200B <= cp <= 0x200D or ch in ' \t':
                continue
            n += 1
    return n


# ══════════════════════════════════════════════════════════════════════════
def verify_pkg(rel, root, rep, strict):
    pkg = os.path.basename(rel.rstrip('/\\'))
    d = os.path.join(root, rel)
    scripts = os.path.join(d, 'scripts')
    update = os.path.join(d, 'update')

    # 1. 目录结构
    if not os.path.isdir(scripts):
        rep.add(pkg, '目录结构', RESULT_FAIL, '缺少 scripts/')
        return
    if not os.path.isdir(update):
        rep.add(pkg, '目录结构', RESULT_FAIL, '缺少 update/')
        return
    rep.add(pkg, '目录结构', RESULT_OK, 'scripts/ + update/')

    # 前缀 SR3R / SR4R
    prefix = 'SR3R_I18N' if pkg.startswith('sr3r') else 'SR4R_I18N'
    asi = os.path.join(scripts, f'{prefix}.asi')
    ini = os.path.join(scripts, f'{prefix}.ini')

    # 2. ASI 产物
    if not os.path.isfile(asi):
        rep.add(pkg, 'ASI 产物', RESULT_FAIL, f'缺少 scripts/{prefix}.asi')
    else:
        ok, detail = is_x64_pe(asi)
        size = os.path.getsize(asi)
        if not ok:
            rep.add(pkg, 'ASI 产物', RESULT_FAIL, detail)
        elif size < 64 * 1024:
            rep.add(pkg, 'ASI 产物', RESULT_FAIL, f'体积仅 {size:,}B，不像完整 DLL')
        else:
            crt = has_dynamic_crt(asi)
            if crt:
                rep.add(pkg, 'ASI 产物', RESULT_WARN,
                        f'{size:,}B {detail}；但存在动态 CRT 依赖 {crt}（期望 /MT 静态链接）')
            else:
                rep.add(pkg, 'ASI 产物', RESULT_OK, f'{size:,}B {detail}，静态 CRT')

    # 3. ini
    if os.path.isfile(asi):
        ok, detail = check_ini(ini, os.path.basename(asi), scripts)
        rep.add(pkg, '配置 ini', RESULT_OK if ok else RESULT_FAIL, detail)

    # 4. update/ le_strings
    les = sorted(glob.glob(os.path.join(update, '*.le_strings')))
    if len(les) < 20:
        rep.add(pkg, 'update/ le_strings', RESULT_FAIL,
                f'仅 {len(les)} 个（期望 >= 20）')
    else:
        bad = []
        for p in les:
            ok, detail = check_le_strings(p)
            if not ok:
                bad.append(f'{os.path.basename(p)}: {detail}')
        zh = len([p for p in les if p.endswith('_zh.le_strings')])
        us = len([p for p in les if p.endswith('_us.le_strings')])
        if bad:
            rep.add(pkg, 'update/ le_strings', RESULT_FAIL,
                    f'{len(bad)}/{len(les)} 结构自检失败：{bad[:2]}')
        elif zh == 0 or us == 0:
            rep.add(pkg, 'update/ le_strings', RESULT_WARN,
                    f'{len(les)} 个（_zh={zh} _us={us}，两者都应非空）')
        else:
            rep.add(pkg, 'update/ le_strings', RESULT_OK,
                    f'{len(les)} 个全部结构自检通过 (_zh={zh}, _us={us})')

    # 5. scripts/dict/
    dicts = sorted(glob.glob(os.path.join(scripts, 'dict', '*.txt')))
    if not dicts:
        rep.add(pkg, 'scripts/dict/', RESULT_FAIL, '没有 txt')
    else:
        tot = 0
        empties = []
        for p in dicts:
            c = count_dict_entries(p)
            tot += c
            if c == 0:
                empties.append(os.path.basename(p))
        if empties:
            rep.add(pkg, 'scripts/dict/', RESULT_WARN,
                    f'{len(dicts)} 个文件，其中 {len(empties)} 个没有词典条目：'
                    f'{empties[:3]}（可能是误放的字符清单，会浪费解析开销）')
        else:
            rep.add(pkg, 'scripts/dict/', RESULT_OK, f'{len(dicts)} 个文件, 共 {tot:,} 条目')

    # 6. charlist.txt
    cl = os.path.join(scripts, 'charlist.txt')
    if not os.path.isfile(cl):
        rep.add(pkg, 'charlist.txt', RESULT_FAIL, '缺少 scripts/charlist.txt')
    else:
        n = count_charlist_chars(cl)
        if n < 100:
            rep.add(pkg, 'charlist.txt', RESULT_FAIL, f'仅 {n} 个字符（期望 >= 100）')
        elif n > 8000:
            rep.add(pkg, 'charlist.txt', RESULT_WARN,
                    f'{n} 个字符（超 8192 位图上限附近，可能超出字形容量）')
        else:
            rep.add(pkg, 'charlist.txt', RESULT_OK, f'{n} 个非 ASCII 字符')

    # 7. 字体
    fonts = [f for f in sorted(os.listdir(scripts))
             if f.lower().endswith(('.ttf', '.otf'))]
    if not fonts:
        rep.add(pkg, '字体', RESULT_FAIL, 'scripts/ 下没有 .ttf/.otf')
    else:
        sizes = ', '.join(f'{f}({os.path.getsize(os.path.join(scripts, f)):,}B)'
                          for f in fonts)
        rep.add(pkg, '字体', RESULT_OK, sizes)

    # 8. ASI Loader
    wd = os.path.join(d, 'winmm.dll')
    if not os.path.isfile(wd):
        rep.add(pkg, 'ASI Loader', RESULT_FAIL, '缺少 winmm.dll')
    else:
        sz = os.path.getsize(wd)
        rep.add(pkg, 'ASI Loader',
                RESULT_OK if sz > 100 * 1024 else RESULT_FAIL,
                f'winmm.dll {sz:,}B')

    # 9. 说明与许可
    rd = os.path.join(d, '必读说明.txt')
    if not os.path.isfile(rd) or os.path.getsize(rd) < 200:
        rep.add(pkg, '必读说明.txt', RESULT_FAIL,
                '缺失或内容过少（玩家无法自助排查）')
    else:
        rep.add(pkg, '必读说明.txt', RESULT_OK, f'{os.path.getsize(rd):,}B')

    lic = os.path.join(d, 'License.txt')
    if not os.path.isfile(lic) or os.path.getsize(lic) < 1000:
        rep.add(pkg, 'License.txt', RESULT_FAIL, '缺失或内容过少')
    else:
        t = open(lic, 'r', encoding='utf-8-sig', errors='replace').read()
        need = ['ASILoader', 'MinHook', 'GPLv3']
        miss = [n for n in need if n not in t]
        if miss:
            rep.add(pkg, 'License.txt', RESULT_WARN,
                    f'{os.path.getsize(lic):,}B，但缺少 {miss}')
        else:
            rep.add(pkg, 'License.txt', RESULT_OK, f'{os.path.getsize(lic):,}B, 各许可齐全')


# ══════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description='校验 release/ 产物完整性')
    ap.add_argument('root', nargs='?', default='release', help='release 目录（默认 release）')
    ap.add_argument('--expect', action='append', choices=sorted(EXPECTED),
                    help='限定必须存在的包（默认 sr3r_common/sr4r_common/sr4r_microsoft）')
    ap.add_argument('--strict', action='store_true', help='警告也当失败')
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f'!! 找不到目录 {root}')
        return 1

    expect = args.expect or sorted(EXPECTED)
    print(f'校验 {root}')
    print(f'期望包含：{", ".join(expect)}')
    print()

    rep = Report()
    found = []
    for name in expect:
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            rep.add(name, '包存在性', RESULT_FAIL, f'缺少目录 {os.path.relpath(d, root)}')
            continue
        found.append(name)
        verify_pkg(name, root, rep, args.strict)

    # 报告目录里多出来的包
    extra = []
    if os.path.isdir(root):
        for n in sorted(os.listdir(root)):
            if os.path.isdir(os.path.join(root, n)) and n not in expect:
                extra.append(n)

    print()
    print('-' * 74)
    ok = len(rep.rows) - rep.fail - rep.warn
    print(f'通过 {ok}  警告 {rep.warn}  失败 {rep.fail}   （{len(found)}/{len(expect)} 个包）')
    if extra:
        print(f'提示：目录里还有未在期望清单里的包：{extra}')

    if rep.fail or (args.strict and rep.warn):
        print('校验未通过 —— 退出码 1')
        return 1
    print('校验通过。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
