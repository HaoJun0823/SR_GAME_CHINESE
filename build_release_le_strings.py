#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_release_le_strings.py — 从 data/ 原始 le_string 模板 + Resource 中文 txt 封装汉化 le_string
================================================================================================
输入:
  data/<game>/<version>/misc/<name>_us.le_strings   原始英文 le_string (结构模板)
  Resource/<game>/<lang>/le_string/<name>_us.txt    中文翻译 (HASH_xxxx 或英文原文 -> 中文)

输出:
  <out_dir>/<name>_zh.le_strings   规范中文 locale 文件
  <out_dir>/<name>_us.le_strings   覆盖英文槽的兜底(内容同 _zh; 无 zh locale 时靠它生效)

  默认 out_dir = Release/<game>/<version>/misc
  CI 构建请传 --out-dir 指向 release/<game>_<version>/update

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

作为库使用:
  import build_release_le_strings as B
  ok, fails = B.build_game('SR4', 'common', ['zh','us'], out_dir=...)
"""
import os
import re
import sys
import struct
import shutil
import argparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'Projects', 'SR4', 'Tools'))
sys.path.insert(0, os.path.join(ROOT, 'Projects', 'SR3', 'Tools'))
import sr4le_repack
import le_strings_repack as sr3_lr

LINE_RE = re.compile(r'^"(.+?)":\s*"(.*)"\s*$')
MAGIC = 0xA84C7F73

# {game: [(version, [suffixes])]} — 规格: version ∈ {common, microsoft}, 目前只有 SR4 有 microsoft
GAME_VERSIONS = {
    'SR3': ['common'],
    'SR4': ['common', 'microsoft'],
}

# ── 不汉化清单 (game, version) -> {表 stem: 原因} ────────────────────────────
#   语义: 模板在这些版本下「不是自然语言文本表」, 汉化它只会破坏功能。
#         命中即跳过, 由游戏使用其自带原版文件 (loose 不覆盖即等于保留原版)。
#
#   ★★ 2026-09-20 勘误: 此清单曾误加 ('SR4','microsoft')/platform_pc ——
#      当时观察到该表「488 条值几乎全是单字符」, 据此判定为按键映射表。
#      **该观察是假象**: microsoft 版的 le_strings 字符串载荷是 **UTF-32LE
#      (每码位 4 字节)**, 按 UTF-16 读会在高字节 0x0000 处立刻截断 -> 每条
#      只剩 1 个字符。改用 UTF-32 解码后, 该表是完整正常的界面文本
#      ('LOCAL PLAY' / 'MATCHMAKING' / 'VIEW GAMERCARD' ...), 且键集与
#      common/platform_ggp 100% 重合、值 100% 相同 —— 它是 GGP(Stadia)/主机
#      分支的文本表, **应当汉化**, 译文源取 platform_ggp_us.txt 即可。
#      真正的单字符映射表只有 common 侧的 charlist 系 (另行处理)。
#      => 该条目已删除; 编码问题改由 sr4le_repack 自适应步长解决。
NO_LOCALIZE = {
}


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


def parse_le_strings(path, step=None):
    """解析 le_strings -> (nb, nstr, [text...], step); 结构非法时抛 ValueError。

    与 repack 模块读路径同源逻辑, 用于构建后独立校验 (不依赖被校验对象的自检)。
    ★★ 载荷步长 (2=UTF-16LE / 4=UTF-32LE):
       microsoft 商店版的 le_strings 是 UTF-32LE, 若按 UTF-16 读会**每条截断成
       1 个字符** (遇到高字节 0x0000 即停), 从而把正常文本表误判成「单字符映射表」。
    ★★ step 参数:
       校验产物时应**显式传入写入时所用的步长** (从源模板探测得到), 不要让
       产物自证 —— 产物里的中文是 UTF-16, 而原位覆盖会在短译文后留一长串 0,
       这些都会干扰自动探测, 造成「校验器用错步长 -> 校验形同虚设」。
       仅在 step=None 时才自动探测 (用于查看任意第三方文件)。
    """
    buf = open(path, 'rb').read()
    if len(buf) < 12:
        raise ValueError('文件过小 (<12B)')
    fid, ver, nb, nstr = struct.unpack_from('<IHHI', buf, 0)
    if fid != MAGIC:
        raise ValueError(f'魔数错 ID=0x{fid:08X} (期望 0x{MAGIC:08X})')
    if 12 + 16 * nb + 8 * nstr > len(buf):
        raise ValueError(f'头部声明超出文件大小 (nb={nb} nstr={nstr} size={len(buf)})')

    if step is None:
        buckets = []
        for i in range(nb):
            cnt, _, off, _ = struct.unpack_from('<IIII', buf, 12 + i * 16)
            buckets.append((off, cnt))
        step = sr4le_repack.detect_text_step(buf, nb, buckets)
    if step not in (2, 4):
        raise ValueError(f'非法步长 {step}')
    fmt = '<H' if step == 2 else '<I'

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
            while e + step - 1 < len(buf) and \
                    struct.unpack_from(fmt, buf, e)[0] != 0:
                e += step
            if step == 2:
                texts.append(buf[s_off + 4:e].decode('utf-16-le', errors='replace'))
            else:
                cps = [struct.unpack_from('<I', buf, m)[0]
                       for m in range(s_off + 4, e, 4)]
                texts.append(''.join(chr(c) if c < 0x110000 else '\ufffd'
                                     for c in cps))
    if total != nstr:
        raise ValueError(f'bucket 条目总数 {total} != header.stringCount {nstr}')
    if len(texts) != nstr:
        raise ValueError(f'解析出 {len(texts)} 条 != nstr {nstr}')
    return nb, nstr, texts, step


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


def template_hashes(path):
    """取 _us.le_strings 模板里全部条目的 hash 集合 (用于译文源选配)。

    ★ 必须按模板自身步长解析 —— 商店版是 UTF-32, 用 UTF-16 步长读会把
      条目边界算错, 取到的 hash 集合就是错的 (选源随之失效)。
    """
    try:
        _nb, _n, _texts, _step = parse_le_strings(path)
    except ValueError:
        return set()
    # parse_le_strings 只给文本; 这里直接复用 repack 模块的原始读路径取 hash。
    try:
        entries = sr4le_repack.parse_le_strings_raw(path)[4]
    except ValueError:
        return set()
    return {h for (_b, h, _t, _o, _l) in entries if h}


_tpl_hash_cache = {}


def _key_cover(tpl_path, pairs):
    """模板键集被译文源覆盖的比例 (0.0~1.0)。"""
    if tpl_path not in _tpl_hash_cache:
        _tpl_hash_cache[tpl_path] = template_hashes(tpl_path)
    hs = _tpl_hash_cache[tpl_path]
    if not hs or not pairs:
        return 0.0
    return len(hs & set(pairs.keys())) / len(hs)


def build_game(game, version, suffixes=('zh', 'us'), out_dir=None, quiet=False):
    """构建单个 {game}_{version} 的 le_string 产物。

    返回 (成功文件数, 失败清单[(out_name, err)])。失败不抛异常, 由调用方汇总。
    """
    data_misc = os.path.join(ROOT, 'data', game, version, 'misc')
    txt_dir = os.path.join(ROOT, 'Resource', game, 'CHS', 'le_string')
    if out_dir is None:
        out_dir = os.path.join(ROOT, 'Release', game, version, 'misc')

    def log(*a):
        if not quiet:
            print(*a)

    if not os.path.isdir(data_misc):
        log(f'[SKIP] {game}_{version}: 找不到 data 模板目录 {data_misc}')
        return 0, []
    if not os.path.isdir(txt_dir):
        log(f'[SKIP] {game}_{version}: 找不到中文 txt 目录 {txt_dir}')
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

    os.makedirs(out_dir, exist_ok=True)

    log(f'\n===== {game}_{version} =====')
    log(f'  模板({version}): {len(us_templates)}  中文txt: {len(txts)}  -> {out_dir}')

    # ── 译文源预加载：供 (C) 重合度选源使用 ────────────────────────────────
    _src_cache = {}

    def _src_pairs(name):
        if name not in _src_cache:
            _src_cache[name] = parse_txt(txts[name]) if name in txts else {}
        return _src_cache[name]

    total_done = 0
    fails = []
    _overslot = {}          # {stem: [超槽说明]} -> inplace 失败降级全量重建的记录
    _resrc = []             # [(stem, 原源, 新源, 覆盖%] -> 记录换源, 汇总时打印
    _skipped = []           # [(stem, 原因)] -> 不汉化清单命中
    _noloc = NO_LOCALIZE.get((game, version), {})

    def _purge_out(stem, why):
        """★★ 2026-09-20 修复: 跳过某模板时必须清掉同名旧产物。
        事故: 上一轮构建产出的 platform_pc_zh/us.le_strings 是错的 (UTF-16 残次品);
        本轮因桩检测走 `[跳过]` 分支没有写文件, 旧文件就原地留存进了发布包 ——
        「跳过」被误当成「不产出」, 实际是「陈旧垃圾继续发布」。
        故凡跳过路径, 一律删除 out_dir 下该 stem 的全部后缀产物。"""
        for suf in suffixes:
            p = os.path.join(out_dir, stem[:-3] + '_' + suf + '.le_strings')
            if os.path.isfile(p):
                os.remove(p)
                log(f'         └ 已清除陈旧产物 {os.path.basename(p)} ({why})')

    for stem, tpl_path in sorted(us_templates.items()):
        # ── (B) 不汉化清单 ───────────────────────────────────────────────
        #   有些 _us 模板在该 version 下并非自然语言文本表 (如 microsoft 版的
        #   platform_pc 是按键映射表), 汉化只会破坏功能 -> 跳过, 保留游戏原版。
        if stem in _noloc:
            _skipped.append((stem, _noloc[stem]))
            log(f'  [保留原版] {stem}: {_noloc[stem]}')
            _purge_out(stem, '保留原版')
            continue

        # ── (C) 译文源自动选配 ───────────────────────────────────────────
        #   问题: _us 模板与 txt 按「文件名 stem」硬配对。但同一份模板在不同
        #         version 下内容可能来自不同分支 —— 例如 SR4 microsoft 的
        #         platform_pc 模板其实是 GGP(Stadia) 分身的字符/按键表 (488 条),
        #         与 PC 版 platform_pc (541 条) 键集不同。硬配对会导致:
        #           - 模板独有的键永远拿不到译文 -> 界面显示英文原文/单个字母
        #           - 源独有的键被写入不存在的槽 -> 白译
        #   做法: 计算模板键集与各候选源的重合度;
        #         - 若同名源覆盖 >= 90%, 直接用 (常规情况, 零开销)
        #         - 否则在候选源中选覆盖最高者 (要求 >= 90%, 且严格优于同名源)
        #         候选源 = 同前缀族 (如 platform_*) 全部源 + 同名源。
        #   注: 只有当同名源存在时才做这件事; 没有同名源的模板依旧跳过,
        #       以免把无关源硬塞进模板 (那才是真的污染)。
        if stem not in txts:
            log(f'  [跳过] {stem}: 无对应中文 txt')
            _purge_out(stem, '无 txt')
            continue

        pairs = _src_pairs(stem)
        src_used = stem
        if pairs:
            cover_own = _key_cover(tpl_path, pairs)
            if cover_own < 1.0:
                # 只要同名源不是「全覆盖」，就问一句：有没有更好的源？
                # 判据: 候选源覆盖率必须 >= 99% 且严格优于同名源 —— 高门槛，
                #       避免把「也差不多」的源换上来 (换错源比不换更糟)。
                # 族名 = 去掉尾部 locale 后缀(_us/_zh/_cz...) 后的第一段
                #   例: platform_pc_us -> platform   (这样可以捞到 platform_ggp_us)
                #       dlc5_us       -> dlc        (同族 dlc1..dlc7)
                base = stem.rsplit('_', 1)[0]        # platform_pc  /  dlc5
                fam = base.rsplit('_', 1)[0] + '_'   # platform_     /  dlc_
                cands = [n for n in txts
                         if (n == stem or n.startswith(fam)) and n != stem]
                best, best_cov = None, cover_own
                for n in sorted(cands):
                    c = _key_cover(tpl_path, _src_pairs(n))
                    if c > best_cov:
                        best, best_cov = n, c
                if best and best_cov >= 0.99:
                    _resrc.append((stem, stem, best, cover_own, best_cov))
                    log(f'  [换源] {stem}: {stem}.txt 覆盖仅 {cover_own:.1%} '
                        f'-> 改用 {best}.txt (覆盖 {best_cov:.1%})')
                    src_used = best
                    pairs = _src_pairs(best)

        if not pairs:
            log(f'  [跳过] {stem}: 中文 txt 为空')
            _purge_out(stem, '空 txt')
            continue
        # 桩文件检测: 若大量 value 为 <=1 字符(占位符), 视为未翻译, 跳过以免发布垃圾
        stub = sum(1 for v in pairs.values() if len(v) <= 1) / len(pairs)
        if stub > 0.5:
            log(f'  [跳过] {stem}: 检测到未翻译占位桩 (单字符值占比 {stub:.0%}), 不发布')
            _purge_out(stem, '占位桩')
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

            # ★★ 源模板步长: 校验产物时必须沿用写入时的步长, 不能让产物自证
            #    (中文是 UTF-16 而原位覆盖会留长串 0, 会干扰自动探测)。
            tpl_step = sr4le_repack.parse_le_strings_raw(tpl_path)[6]

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
            # ★★ 2026-09-20: SR3 也按模板步长注入, 不再硬编码 UTF-16。
            #   结构与 SR4 完全一致, 只是实测文件全为 UTF-16; 一旦遇到 UTF-32
            #   模板 (或误用 SR4 的 microsoft/主机牌文件), 硬编码会静默写坏。
            tpl_step = sr3_lr.detect_file_step(tpl_path)
            if tpl_step == 4:
                repack_pairs = {
                    h: (struct.pack(f'<{len(t)}I', *[ord(c) for c in t]) + b'\x00' * 4)
                    for h, t in pairs.items()}
            else:
                repack_pairs = {
                    h: (t.encode('utf-16-le') + b'\x00\x00') for h, t in pairs.items()}

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
                mode = repack_fn(tpl_path, repack_pairs, out_path)
            except Exception as e:
                fails.append((out_name, str(e)))
                base_ok = False
                continue
            # ★★ 产物全量结构自检: 独立解析产出的文件, 核对内部一致性。
            #    任一不一致都说明 repack 出错, 必须暴露而不是静默发布 (2026-09-20 事故教训)。
            try:
                nb, tot, texts, step = parse_le_strings(out_path, step=tpl_step)
                cjk, real = count_cjk(texts)
                if step != tpl_step:
                    raise ValueError(
                        f'产物步长 {step}B != 模板步长 {tpl_step}B (写入编码被改变)')
                if suf == suffixes[0]:
                    src_tag = f'{len(pairs)} 条' + (f' 源={src_used}' if src_used != stem else '')
                    enc = 'UTF-16' if step == 2 else 'UTF-32'
                    log(f'  {stem:24s} 条目={tot:5d} 中文串={cjk:5d} '
                        f'(翻译 {src_tag}, {mode}, {enc}) -> {out_name} 等 {len(suffixes)} 个')
            except Exception as e:
                fails.append((out_name, '结构自检: ' + str(e)))
                base_ok = False
        if base_ok:
            total_done += 1

    log(f'  完成: {total_done} 个文件 -> {out_dir}')
    if _skipped:
        log(f'  [保留原版] {len(_skipped)} 张表按不汉化清单跳过 (游戏使用自带原版):')
        for stem, why in _skipped:
            log(f'    {stem}: {why}')
    if _resrc:
        log(f'  [换源] {len(_resrc)} 张表的译文源与文件名不同 (模板来自其它分支):')
        for stem, old, new, co, cn in _resrc:
            log(f'    {stem}: {old}.txt (覆盖 {co:.0%}) -> {new}.txt (覆盖 {cn:.0%})')
    if _overslot:
        log(f'  [注意] {len(_overslot)} 个文件有中文超槽, 已自动降级为全量重建 (条目不丢):')
        for stem, msgs in sorted(_overslot.items()):
            log(f'    {stem}: {len(msgs)} 条  {msgs[0][:80]}')
    if fails:
        log('  失败:')
        for n, e in fails:
            log(f'    {n}: {e}')
    return total_done, fails


def rel_out_dir(game, version):
    """默认输出目录 (相对仓库根)。"""
    return os.path.join('Release', game, version, 'misc')


def main():
    ap = argparse.ArgumentParser(description='构建汉化 le_string')
    ap.add_argument('--game', choices=['SR3', 'SR4'], action='append',
                    help='限定游戏 (可重复; 默认两个都构建)')
    ap.add_argument('--version', help='限定版本 (common / microsoft)')
    ap.add_argument('--out-dir', help='覆盖输出目录 (单游戏单版本时可用)')
    ap.add_argument('--suffixes', default='zh,us', help='产出的 locale 后缀 (默认 zh,us)')
    ap.add_argument('--no-self-test', action='store_true', help='跳过前置自检 (不建议)')
    args = ap.parse_args()

    suffixes = [s for s in args.suffixes.split(',') if s]
    games = args.game or ['SR3', 'SR4']
    print('构建汉化 le_string (区分 game / version)')

    # ── 1) 前置自检: 两侧 repack 模块的 8 字节 offset 表读/写自洽 ──────────
    if not args.no_self_test:
        print('\n[1/3] 前置自检 (repack 模块 round-trip)')
        try:
            sr3_lr.self_test()
            sr4le_repack.self_test()
        except Exception as e:
            print(f'  前置自检失败: {e}')
            print('\n构建中止 (自检未通过, 不产出任何文件).')
            return 1
    else:
        print('\n[1/3] 前置自检 —— 已跳过 (--no-self-test)')

    # ── 2) 构建 ──────────────────────────────────────────────────────────
    print('\n[2/3] 构建')
    results = []
    for g in games:
        versions = [args.version] if args.version else GAME_VERSIONS.get(g, ['common'])
        for v in versions:
            od = args.out_dir if (args.out_dir and len(games) == 1 and len(versions) == 1) else None
            results.append((g, v, build_game(g, v, suffixes, out_dir=od)))

    # ── 3) 汇总 ──────────────────────────────────────────────────────────
    print('\n[3/3] 汇总')
    all_fails = []
    for g, v, (n, f) in results:
        od = args.out_dir if (args.out_dir and len(games) == 1
                              and len([1 for gg, _, _ in results]) == 1) else None
        od = od or rel_out_dir(g, v)
        cnt = len(os.listdir(os.path.join(ROOT, od))) if os.path.isdir(os.path.join(ROOT, od)) else 0
        print(f'  {od}: {cnt} 文件')
        all_fails.extend(f)

    if all_fails:
        print(f'\n构建完成但有 {len(all_fails)} 项失败 —— 退出码 1')
        return 1
    print('\n全部完成 (产物结构自检全部通过)。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
