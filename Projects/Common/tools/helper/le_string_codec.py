# -*- coding: utf-8 -*-
"""
le_string_codec.py —— Volition `*.le_strings` 容器的唯一权威实现（SR3 + SR4 共用）

=====================================================================
一、容器格式（字节级事实，由 SR3/SR4 共 400+ 个真实文件实测得出）
=====================================================================

    ┌─ header ──────────────────────────────────────────────── 12 字节
    │  ID              u32   = 0xA84C7F73   ("le_string" 魔数)
    │  version         u16
    │  bucketCount     u16
    │  stringCount     u32   ← ★ 全文件字符串总数，写回时必须守恒
    ├─ buckets ───────────────────────────────────────── bucketCount × 16 字节
    │  count           u32
    │  (pad)           u32
    │  offTableOffset  u32   ← 该桶 offset 表的绝对文件偏移
    │  (pad)           u32
    ├─ offset tables ───────────────────────────────────────── 步长 ★ 8 字节
    │  每项 { 字符串绝对偏移 u32, 填充零 u32 }   × Σcount == stringCount
    └─ string entries ────────────────────────────────────────
       每项 { hash u32, 文本, 终止符 }
       hash     = Volition CRC32(键名小写化)  初始 0 / 无 final xor / poly 0xEDB88320
       终止符    = 2 字节 (UTF-16LE) 或 4 字节 (UTF-32LE)，随载荷步长

    ★ offset 表步长 8 字节（不是 4）是整个模块存在的第一理由。见 §三.1。

=====================================================================
二、★ 载荷编码矩阵（这是"同容器多编码"的根源，绝不能写死步长）
=====================================================================

    数据源                          载荷编码        实测依据
    ----------------------------    ------------    ---------------------------
    SR3  common                      UTF-16LE (2B)   230 个真实文件全部 UTF-16
    SR4  common                      ★ 混合          含 20 张主机分支表为 UTF-32
      ├─ 桌面/PC 表                   UTF-16LE (2B)
      └─ platform_ggp_* / _nx64_*     UTF-32LE (4B)   ← 主机移植分支
    SR4  microsoft                   UTF-32LE (4B)   全表 22 张无一例外

    结论：主机板（GGP=Stadia / NX64=Switch）一律 UTF-32；
          标称 "PC" 的表也可能混入 UTF-32 → 必须逐文件探测。

=====================================================================
三、★★★ 三条血的教训（每一条都对应一次真实崩溃 / 误判）
=====================================================================

  1) offset 表是 8 字节步长，不是 4
     症状：启动即崩 c0000005，且零错误日志。
     机理：误用 4B 步长 → 把「填充零」当成独立条目 → 一半条目读成空槽被丢弃
           → 写回时桶内条目数减半，但 header.stringCount 原封不动
           → 引擎按 header 声明索引桶数组 → 越界读到 NULL → 解引用崩溃。
           （`customize_us`: 3061 条丢 1405 条，130090B → 59020B，体积 −55%）
     红旗：★ 产物体积只有原始文件的 33% ~ 45%（本案正好落在 45%）。
     修法：OFFSET_ENTRY_SIZE = 8，且写回后强制校验 Σcount == header.stringCount。

  2) 产物校验必须显式传入「写入时用的步长」，不能让产物自证
     症状：校验器报告"全部通过"，但游戏照样崩。
     机理：① 译文是 UTF-16，而容器声明是 UTF-32 → 产物自证会选错步长；
           ② 原位覆盖（inplace）时短译文后面残留长原文的 `\x00` 尾巴
              → 探测函数把"零尾"误读成"终止符很宽"。
     修法：`read_with_bucket(path, step=...)` 接受强制步长；
           `repack()` 内部回读时**必须**传回原始 step，绝不重探测。

  3) 探测判据必须"锚定高字节"，且单侧恒零才可定案
     症状：把 UTF-16 的中文表判成 UTF-32，或把 UTF-32 判成 UTF-16。
     机理：UTF-32LE 的字节形态是 `[有效低半字][00 00]` 严格交替，例如
           '阿' U+963F → `3F 96 00 00`。于是以 2B 为单位扫描时
           「偶数序号半字恒为 0x0000，奇数序号半字有量非零」。
           ★ 若去统计「低字节是否为零」，UTF-16 的汉字（高字节必然非零）
             恰好会被误读成 UTF-32 —— 本模块第一版就踩了这个坑。
           必须锚定**高字节**，并要求"一侧恒零 + 另一侧有量"。
     修法：见 `detect_text_step()` 判据 A（强）/ A2（中）/ B（兜底）。

=====================================================================
四、charlist 映射（SR4 独有发明，SR3 未来也可能需要）
=====================================================================

    `charlist_<lang>.dat`：每行一个整数（UTF-8 文本），规则
      · 以 `//` 开头的行、`count=` 行、空行 → 跳过
      · 值 > 0x100  → 从 DEFAULT_BUCKET_FIRST_SLOT(0x100) 起按出现顺序分配递增槽位
      · 值 ≤ 0x100  → 恒等映射（槽位 == 码位）
    读取（extract）：`charmap[槽位] → 真实 Unicode 码位`
                    → 文本里的 0x0100..N 是"伪码位"，需换回真字。
    写入（repack）  ：需要反向表 `真实码位 → 槽位`；多对一（重复码位）时取**第一个**。
    注意：SR4 第 0 行是零宽水印（U+200B..U+200D），解析必须跳过（见 ZWSP 集合）。

=====================================================================
五、对外 API 速查
=====================================================================

  探测
    detect_text_step(buf, nb, buckets) -> 2|4     载荷步长（结构+长度投票）
    detect_file_step(path)             -> 2|4     txt→bin 打包前必调
  解析
    read_with_bucket(path, step=None)  -> (fid, ver, nb, nstr, entries, step)
    read_texts(path, step=None)        -> {hash: str}
  写回
    repack(in_path, pairs, out_path)             -> int      重建文件
    repack_inplace(in_path, pairs, out_path)     -> dict     原位覆盖（体积可缩）
  编码
    encode_text(text, rev_charmap=None, step=2)  -> bytes    含终止符
    parse_charlist(path)               -> {slot: codepoint}
    build_reverse_charmap(charmap)     -> {codepoint: slot}
  自检
    self_test(verbose=True)            -> bool
"""

import os
import struct
import sys
from collections import OrderedDict

# ---------------------------------------------------------------- 常量

MAGIC = 0xA84C7F73
HEADER_SIZE = 12
BUCKET_SIZE = 16
OFFSET_ENTRY_SIZE = 8            # ★★ 8 字节，不是 4。见 §三.1
TERM_STEP = {2: b"\x00\x00", 4: b"\x00\x00\x00\x00"}
DEFAULT_BUCKET_FIRST_SLOT = 0x100

# charlist 里作为"水印"出现的零宽字符（SR4 第 0 行），以及 BOM，一律跳过
ZWSP_CODEPOINTS = {0x200B, 0x200C, 0x200D, 0xFEFF}

# Volition CRC32 表（初始 0，无 final xor，poly 0xEDB88320）
_CRC_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ (0xEDB88320 if (_c & 1) else 0)
    _CRC_TABLE.append(_c)


def crc_volition(name):
    """Volition 键名哈希。键名先小写化。返回 u32。"""
    if isinstance(name, bytes):
        data = name.lower()
    else:
        data = name.lower().encode("utf-8", "replace")
    c = 0
    for b in data:
        c = (c >> 8) ^ _CRC_TABLE[(c ^ b) & 0xFF]
    return c & 0xFFFFFFFF


# ---------------------------------------------------------------- 容器解析

def _bucket_table(buf, path="<buf>"):
    """读 header + bucket 表。返回 (file_id, version, nb, nstr, [(off, count), ...])"""
    if len(buf) < HEADER_SIZE:
        raise ValueError(f"{path}: 文件太短 ({len(buf)}B)，放不下 12 字节 header")
    fid, ver, nb, nstr = struct.unpack_from("<IHHI", buf, 0)
    if fid != MAGIC:
        raise ValueError(f"{path}: 魔数不符 0x{fid:08X} != 0x{MAGIC:08X}")
    need = HEADER_SIZE + BUCKET_SIZE * nb
    if len(buf) < need:
        raise ValueError(f"{path}: 文件太短，bucket 表需要 {need}B，实际 {len(buf)}B")
    buckets = []
    for i in range(nb):
        base = HEADER_SIZE + BUCKET_SIZE * i
        count, _pad0, off_table, _pad1 = struct.unpack_from("<IIII", buf, base)
        buckets.append((off_table, count))
    return fid, ver, nb, nstr, buckets


def _decode_cstr(buf, pos, step):
    """从 pos 起读一个以 step 宽 NUL 结尾的字符串，返回 (text, next_pos)。"""
    if step == 2:
        end = pos
        while end + 1 < len(buf):
            if buf[end] == 0 and buf[end + 1] == 0:
                break
            end += 2
        raw = buf[pos:end]
        return raw.decode("utf-16-le", "replace"), end + 2
    if step == 4:
        end = pos
        while end + 3 < len(buf):
            if buf[end] == 0 and buf[end + 1] == 0 and buf[end + 2] == 0 and buf[end + 3] == 0:
                break
            end += 4
        raw = buf[pos:end]
        return raw.decode("utf-32-le", "replace"), end + 4
    raise ValueError(f"非法步长 {step}")


def detect_text_step(buf, nb, buckets):
    """
    判定载荷步长 2 或 4。三重投票，按"证据强度"从强到弱。

    ─────────────────────────────────────────────────────────────
    ★★ 先想清楚 UTF-32LE 的字节长什么样（这是所有判据的基准）：

        '阿' U+963F  →  3F 96 00 00
        '尔' U+5C14  →  14 5C 00 00
        '法' U+6CD5  →  D5 6C 00 00
                         └─┬─┘ └─┬─┘
                          有效  恒为零
                           低半   高半

    于是以「2 字节半字」为单位扫描 UTF-32LE 数据，会看到严格交替：
        奇数序号半字 = 有效值（并集非零）
        偶数序号半字 = 0x0000（恒零）

    ★ 陷阱：有效值那半字本身可能"低字节为 0"（如 U+5C14 的 14 5C 里 lo=0x14…）。
      如果像很多人（包括本模块第一版）那样去统计「低字节是否为零」，
      会把 UTF-16 的中文误判成 UTF-32 —— 这正是 §三.3 记的坑。
      正确判据必须锚定在**高字节**上，见 A。
    ─────────────────────────────────────────────────────────────

    A) 【强判据】高字节恒零性
       以 2B 为单位扫描：若「偶数序号半字恒为 0x0000」且「奇数序号半字有量非零」
       → UTF-32LE。反之若偶/奇两边的**高字节都出现过非零** → 排除 UTF-32。
       ★ 方向性在这里：只看"高字节"，因为 UTF-16 的汉字高字节必然非零，
         所以 UTF-16 一定无法满足"偶数序号恒零"。
    A2)【中判据】条目长度整除性
       逐条按 2B 找到终止符，若绝大多数条目字节数是 4 的倍数，且这些条目里
       第 2、4 个半字恒零 → UTF-32。
    B) 【弱判据／兜底】解码长度分布
       同一批条目分别按 2B、4B 解码，比较"多字符条目占比"和"是否出现 U+FFFD"。
       UTF-32 数据按 2B 解码时会产出大量乱码/替换字符且条目普遍偏短。
    """
    samples = []
    for off, count in buckets:
        if count <= 0:
            continue
        for i in range(count):
            p = off + OFFSET_ENTRY_SIZE * i
            if p + OFFSET_ENTRY_SIZE > len(buf):
                break
            str_off = struct.unpack_from("<I", buf, p)[0]
            if str_off + 4 > len(buf):
                continue
            samples.append(str_off + 4)          # 跳过 hash u32
        if len(samples) >= 400:                  # 够了，别全表扫
            break
    if not samples:
        return 2                                 # 全空容器，默认 UTF-16

    # ---- A) 逐条扫描（★ 必须停在终止符，不能盲扫固定字节数）----
    #
    # ★ 第一版在这里又踩了一个坑：用 `range(s, s+64, 2)` 盲扫 64 字节。
    #   短字符串会在中途读到 **下一条记录的 hash**（一个随机的 u32），
    #   于是"偶数序号半字恒零"被这个 hash 打破 → UTF-32 被误判成 UTF-16。
    #   必须用 _decode_cstr 逐条定位终止符，只在**条目内部**统计。
    even_slot_nz = 0      # 偶数序号半字非零（UTF-32 下恒为 0）
    odd_slot_nz = 0       # 奇数序号半字非零（UTF-32 下应有量）
    pure4 = 0             # 完全符合 UTF-32 交替形态的条目数
    clean = 0             # 成功定位终止符的条目数
    for s in samples[:300]:
        _t, nxt = _decode_cstr(buf, s, 2)
        raw_len = nxt - s - 2
        if raw_len <= 0:
            continue
        clean += 1
        e_nz = o_nz = 0
        idx = 0
        for p in range(s, s + raw_len, 2):
            v = buf[p] | (buf[p + 1] << 8)
            if v:
                if idx % 2 == 0:
                    e_nz += 1
                else:
                    o_nz += 1
            idx += 1
        even_slot_nz += e_nz
        odd_slot_nz += o_nz
        if e_nz == 0 and o_nz == 0:
            pure4 += 1          # 空串，对 UTF-32 无矛盾
        elif e_nz == 0 and o_nz > 0 and raw_len % 4 == 0:
            pure4 += 1          # ★ 教科书式 UTF-32 条目
    if clean >= 10:
        # 全部条目都是 UTF-32 形态，且总量上"奇数位有量非零"
        if pure4 == clean and odd_slot_nz > 0 and even_slot_nz == 0:
            return 4
        # 镜像：偶数位恒零、奇数位有量（允许少量脏条目）
        if even_slot_nz == 0 and odd_slot_nz >= 8 and pure4 >= clean * 0.8:
            return 4

    # ---- A2) 条目长度整除性 + 条目内高半字恒零 ----
    ok4 = 0
    checked = 0
    for s in samples[:200]:
        _t, nxt = _decode_cstr(buf, s, 2)
        raw_len = nxt - s - 2
        if raw_len <= 0:
            continue
        checked += 1
        if raw_len % 4 != 0:
            continue
        hz = True
        for p in range(s + 1, s + raw_len, 2):   # 每个偶数序号半字的高字节
            if buf[p] != 0:
                hz = False
                break
        if hz:
            ok4 += 1
    if checked >= 20 and ok4 >= checked * 0.9:
        return 4

    # ---- B) ★ 条目长度分布（本容器最可靠的判据，提升为强判据）----
    #
    # ★★ 这是本模块绕过前两个坑之后找到的"锚点"，务必理解为什么它有效：
    #
    #   UTF-32LE 载荷 `3F 96 00 00 14 5C 00 00 ...` 若按 step=2 切分，
    #   会在**第一个字符的零填充处**就遇到 2 字节 NUL 而终止：
    #       '阿尔法' 按 2B 读 → raw_len = 2, text = '阿'   ← 每个条目只剩 1 字
    #       '阿尔法' 按 4B 读 → raw_len = 12, text = '阿尔法' ✅
    #
    #   于是形成一个**极干净、极稳定的指纹**：
    #       按 2B 切分 → 几乎每条 raw_len 都 == 2（单字符，被截断）
    #       按 4B 切分 → 条目正常、无 U+FFFD、长度呈自然分布
    #
    #   ★ 为什么它免疫于坑 #3？因为它不依赖"低字节是否为零"这类字节级启发，
    #     而是看**同一批数据在两种步长下的解码行为差异**——这是双向自证。
    #     若数据真是 UTF-16，按 4B 读会因半字错位产生大量 U+FFFD，显然不成立。
    trunc2 = 0        # 按 2B 读时 raw_len == 2（单个字符）的条目数
    multi2 = 0
    good4 = 0         # 按 4B 读时无替换字符且非空的条目数
    bad4 = 0
    for s in samples[:300]:
        _t2, n2 = _decode_cstr(buf, s, 2)
        r2 = n2 - s - 2
        if r2 == 2:
            trunc2 += 1
        elif r2 > 2:
            multi2 += 1

        t4, _n4 = _decode_cstr(buf, s, 4)
        if "\ufffd" in t4:
            bad4 += 1
        elif t4:
            good4 += 1
    n_walk = trunc2 + multi2
    if n_walk >= 4:
        # UTF-32 指纹：2B 几乎全被截成单字，同时 4B 解码干净
        # ★ 门限取 4 而不是 10：真实文件动辄上千条，此处只要够形成统计即可；
        #   自检样本只有 6 条，若门限过高会让判据形同虚设（本模块踩过）。
        if trunc2 >= n_walk * 0.8 and good4 >= n_walk * 0.8 and bad4 == 0:
            return 4
    return 2


def detect_file_step(path):
    """★ txt → bin 打包前必调：判定某个 le_strings 文件的载荷步长。"""
    with open(path, "rb") as f:
        buf = f.read()
    _fid, _ver, nb, _nstr, buckets = _bucket_table(buf, path)
    return detect_text_step(buf, nb, buckets)


def read_with_bucket(path, step=None):
    """
    完整解析。返回 (file_id, version, nb, stringCount, entries, step)
    entries = OrderedDict{hash: text}，保持文件内出现顺序。

    ★ step=None 时自动探测；校验产物时**必须显式传 step**（见 §三.2）。
    """
    with open(path, "rb") as f:
        buf = f.read()
    fid, ver, nb, nstr, buckets = _bucket_table(buf, path)

    total_count = sum(c for _o, c in buckets)
    if total_count != nstr:
        raise ValueError(
            f"{path}: bucket 条目总数 {total_count} != header.stringCount {nstr} (文件不一致)"
        )

    if step is None:
        step = detect_text_step(buf, nb, buckets)
    if step not in (2, 4):
        raise ValueError(f"{path}: 非法步长 {step}")

    out = OrderedDict()
    for off, count in buckets:
        for i in range(count):
            p = off + OFFSET_ENTRY_SIZE * i
            if p + OFFSET_ENTRY_SIZE > len(buf):
                raise ValueError(f"{path}: offset 表越界 (桶内第 {i} 项, off=0x{off:X})")
            str_off = struct.unpack_from("<I", buf, p)[0]
            if str_off + 4 > len(buf):
                raise ValueError(f"{path}: 字符串偏移越界 0x{str_off:X}")
            h = struct.unpack_from("<I", buf, str_off)[0]
            text, _nxt = _decode_cstr(buf, str_off + 4, step)
            out[h] = text

    if len(out) != nstr:
        raise ValueError(f"{path}: 解析出 {len(out)} 条 != nstr {nstr}")
    return fid, ver, nb, nstr, out, step


def read_texts(path, step=None):
    """便捷入口：只取 {hash: text}。"""
    return read_with_bucket(path, step=step)[4]


# ---------------------------------------------------------------- charlist

def parse_charlist(path):
    """
    解析 charlist_<lang>.dat → {slot: codepoint}。
    规则见模块头 §四。`count=` 行与注释行跳过，零宽水印跳过。
    """
    charmap = {}
    next_slot = DEFAULT_BUCKET_FIRST_SLOT
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("//") or s.startswith("count="):
                continue
            # 可能一行多个数字（容错）
            for tok in s.replace(",", " ").split():
                tok = tok.strip()
                if not tok:
                    continue
                try:
                    v = int(tok, 0)
                except ValueError:
                    continue
                if v in ZWSP_CODEPOINTS:
                    continue
                if v > 0x100:
                    while next_slot in charmap:
                        next_slot += 1
                    charmap[next_slot] = v
                    next_slot += 1
                else:
                    charmap.setdefault(v, v)
    return charmap


def build_reverse_charmap(charmap):
    """{codepoint: slot}；多对一时取第一个（最小槽位）。"""
    rev = {}
    for slot in sorted(charmap):
        cp = charmap[slot]
        if cp not in rev:
            rev[cp] = slot
    return rev


def encode_text(text, rev_charmap=None, step=2):
    """
    把 Unicode 文本编码为容器载荷（**含终止符**）。
    rev_charmap 非空时，先把真实码位换成 charlist 槽位（伪码位），再编码。
    """
    if rev_charmap:
        buf = []
        for ch in text:
            buf.append(rev_charmap.get(ord(ch), ord(ch)))
        if step == 2:
            body = b"".join(struct.pack("<H", c & 0xFFFF) for c in buf)
        else:
            body = b"".join(struct.pack("<I", c) for c in buf)
    else:
        body = text.encode("utf-16-le" if step == 2 else "utf-32-le")
    return body + TERM_STEP[step]


# ---------------------------------------------------------------- 写回

def _make_sample(step, texts, hashes):
    """构造一个内存中的合法 le_strings，用于自检。单桶。

    ★ 布局（nb = 1）：
        header 12B  @0
        bucket 16B  @12   ( = HEADER_SIZE )
        offtbl 8n B @28   ( = HEADER_SIZE + BUCKET_SIZE )
        body        @28+8n
      注意：bucket 只有 1 个，所以 off_tbl 紧接在 header+bucket 之后，
            **不能再加一个 BUCKET_SIZE**（写代码时踩过一次）。
    """
    n = len(texts)
    header = struct.pack("<IHHI", MAGIC, 1, 1, n)
    bucket_off = HEADER_SIZE                     # 单桶：bucket 紧接 header
    off_tbl_off = bucket_off + BUCKET_SIZE       # offset 表紧接 bucket
    body_off = off_tbl_off + OFFSET_ENTRY_SIZE * n

    blob = b""
    offs = []
    for h, t in zip(hashes, texts):
        offs.append(body_off + len(blob))
        blob += struct.pack("<I", h) + encode_text(t, None, step)

    bucket = struct.pack("<IIII", n, 0, off_tbl_off, 0)
    off_tbl = b"".join(struct.pack("<II", o, 0) for o in offs)
    return header + bucket + off_tbl + blob


def repack(in_path, pairs, out_path):
    """
    重建式写回：按 pairs 覆盖文本，未提及的 hash 原样保留，输出到 out_path。
    pairs = {hash: 新文本}。返回写出的条目数。
    ★ 体积可增可减（重建）。
    """
    fid, ver, nb, nstr, entries, step = read_with_bucket(in_path)
    merged = OrderedDict(entries)
    for h, t in pairs.items():
        if h in merged:
            merged[h] = t
        else:
            # 新键：按 hash 升序插到尾部（引擎按 hash 查表，顺序不影响查找）
            merged[h] = t

    # 分桶：原文件几个桶就还几个桶，按 hash 顺序重新均分（保持 count 结构）
    hs = list(merged.keys())
    total = len(hs)
    if nb <= 0:
        nb = 1
    per = total // nb
    buckets = []
    idx = 0
    for i in range(nb):
        c = per if i < nb - 1 else (total - idx)
        buckets.append(hs[idx:idx + c])
        idx += c

    header = struct.pack("<IHHI", MAGIC, ver, nb, total)
    bucket_off = HEADER_SIZE
    off_tbl_off = bucket_off + BUCKET_SIZE * nb
    body_off = off_tbl_off + OFFSET_ENTRY_SIZE * total

    # 先算偏移
    blob = b""
    offs = []
    for hslist in buckets:
        for h in hslist:
            offs.append(body_off + len(blob))
            blob += struct.pack("<I", h) + encode_text(merged[h], None, step)

    bk = b""
    oi = 0
    for hslist in buckets:
        c = len(hslist)
        bk += struct.pack("<IIII", c, 0, off_tbl_off + oi * OFFSET_ENTRY_SIZE, 0)
        oi += c
    ot = b"".join(struct.pack("<II", o, 0) for o in offs)

    data = header + bk + ot + blob

    written_total = sum(len(b) for b in buckets)
    if written_total != total:
        raise ValueError(f"{in_path}: 写回条目数 {written_total} != {total} —— 拒绝写出")
    if len(data) != body_off + len(blob):
        raise ValueError(f"{in_path}: 布局自检失败 —— 拒绝写出")

    with open(out_path, "wb") as f:
        f.write(data)

    back = read_with_bucket(out_path, step=step)[4]
    if len(back) != total:
        raise ValueError(f"{out_path}: 回读校验失败, 只读回 {len(back)}/{total} 条")
    return total


def repack_inplace(in_path, pairs, out_path):
    """
    原位覆盖式写回：保持原文件布局与槽位，仅在原位替换文本。
    优点：不改变偏移，引擎侧最稳；缺点：新文本比旧文本长时无法容纳。
    返回 dict 统计 {'written': n, 'overflow': [hash, ...]}。
    """
    fid, ver, nb, nstr, entries, step = read_with_bucket(in_path)
    with open(in_path, "rb") as f:
        buf = bytearray(f.read())

    written, overflow = 0, []
    for off, count in _bucket_table(buf, in_path)[4]:
        for i in range(count):
            p = off + OFFSET_ENTRY_SIZE * i
            str_off = struct.unpack_from("<I", buf, p)[0]
            h = struct.unpack_from("<I", buf, str_off)[0]
            if h not in pairs:
                continue
            new_txt = pairs[h]
            if new_txt == entries.get(h):
                continue
            _old, old_next = _decode_cstr(buf, str_off + 4, step)
            new_raw = encode_text(new_txt, None, step)
            room = old_next - (str_off + 4)
            if len(new_raw) > room:
                overflow.append(h)
                continue
            buf[str_off + 4: str_off + 4 + len(new_raw)] = new_raw
            written += 1

    _purge_out(out_path)
    with open(out_path, "wb") as f:
        f.write(bytes(buf))

    back = read_with_bucket(out_path, step=step)[4]
    if len(back) != nstr:
        raise ValueError(f"{out_path}: 回读校验失败, 只读回 {len(back)}/{nstr} 条")
    return {"written": written, "overflow": overflow, "entries": nstr}


def _purge_out(path):
    """★ 跳过路径必须清同名旧产物：跳过 ≠ 不产出，旧文件会原地留存进包。"""
    if os.path.isfile(path):
        os.remove(path)


# ---------------------------------------------------------------- 自检

def self_test(verbose=True):
    ok = True

    def chk(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
            if verbose:
                print(f"  [FAIL] {msg}")
        elif verbose:
            print(f"  [ ok ] {msg}")

    if verbose:
        print("[self_test] le_string_codec")

    hashes = [crc_volition(f"KEY_{i:03d}") for i in range(40)]

    # --- UTF-16 样本（★ 用足量条目：统计判据需要样本量，6 条测不出来）---
    t16 = [f"Alpha_{i}" for i in range(40)]
    s16 = _make_sample(2, t16, hashes)
    _f, _v, nb, nstr, bk = _bucket_table(s16, "<utf16>")
    step16 = detect_text_step(s16, nb, bk)
    chk(step16 == 2, f"UTF-16 样本探测步长 = {step16} (期望 2)")

    # --- UTF-32 样本（含中文，制造 CJK 假象）---
    t32 = ["阿尔法", "贝塔", "伽马", "德尔塔", "艾普西龙", "泽塔"] * 7
    t32 = t32[:40]
    s32 = _make_sample(4, t32, hashes)
    _f, _v, nb, nstr, bk = _bucket_table(s32, "<utf32>")
    step32 = detect_text_step(s32, nb, bk)
    chk(step32 == 4, f"UTF-32(中文) 样本探测步长 = {step32} (期望 4)")

    # --- ★ 纯 ASCII 的 UTF-32 样本（最容易被误判成 UTF-16 的情形）---
    t32a = [f"Alpha_{i}" for i in range(40)]
    s32a = _make_sample(4, t32a, hashes)
    _f, _v, nb, _n, bk = _bucket_table(s32a, "<utf32a>")
    chk(detect_text_step(s32a, nb, bk) == 4, "★ 纯 ASCII UTF-32 不被误判为 UTF-16")

    # --- round-trip：写盘 → 读回 ---
    import tempfile
    d = tempfile.mkdtemp(prefix="lecodec_")
    p16 = os.path.join(d, "t16.le_strings")
    with open(p16, "wb") as f:
        f.write(s16)
    fid, ver, _nb, nstr, ent, st = read_with_bucket(p16)
    chk(len(ent) == 40 and st == 2, f"UTF-16 回读 {len(ent)}/40 条, step={st}")
    chk(list(ent.values())[:2] == ["Alpha_0", "Alpha_1"], "UTF-16 内容一致")

    p32 = os.path.join(d, "t32.le_strings")
    with open(p32, "wb") as f:
        f.write(s32)
    ent32 = read_texts(p32)
    chk(list(ent32.values())[:2] == ["阿尔法", "贝塔"], "UTF-32 内容一致（未截断）")

    # --- ★ 显式步长校验：故意用错步长应读到错误内容（证明"不能自证"）---
    wrong = read_with_bucket(p32, step=2)[4]
    chk(list(wrong.values())[0] != "阿尔法",
        "★ 显式传错步长会得到不同结果（证明校验必须锁定写入步长）")

    # --- 原子性：条目数守恒 ---
    pairs = {hashes[0]: "ALPHA-新"}
    n = repack(p16, pairs, os.path.join(d, "o16.le_strings"))
    chk(n == 40, f"repack 条目数守恒 {n}/40")
    ro = read_texts(os.path.join(d, "o16.le_strings"))
    chk(ro[hashes[0]] == "ALPHA-新", "repack 覆盖生效")
    chk(ro[hashes[3]] == "Alpha_3", "repack 未提及键原样保留")

    # --- ★ 写后自检必须能拦住"条目数减半"这一经典事故 ---
    #     用 repack 输出一个文件，再手工把 header.stringCount 改大，模拟丢条目
    bad = bytearray(s16)
    struct.pack_into("<I", bad, 8, 999)      # 篡改 stringCount
    pbad = os.path.join(d, "bad.le_strings")
    with open(pbad, "wb") as f:
        f.write(bytes(bad))
    try:
        read_with_bucket(pbad)
        chk(False, "★ 篡改 stringCount 后应当抛错（但没有）")
    except ValueError:
        chk(True, "★ 篡改 stringCount 后被正确拒绝")

    # --- ★ 体积守恒检查：把"产物只有原始 45%"这一红旗变成可编程断言 ---
    #     正常写回（覆盖少量键）产物体积不应显著缩水
    sz_in = os.path.getsize(p16)
    sz_out = os.path.getsize(os.path.join(d, "o16.le_strings"))
    ratio = sz_out / sz_in
    chk(ratio > 0.9, f"★ 写回体积比 {ratio:.2%} > 90%（防空槽丢弃事故）")

    # --- ★ inplace 写回：同长度替换不应改变任何偏移 ---
    same_len = {hashes[0]: "Alpha_0"}        # 与原文完全相同 → 不改
    r = repack_inplace(p16, same_len, os.path.join(d, "ip16.le_strings"))
    chk(r["written"] == 0 and r["entries"] == 40, "inplace 无差异时不写入且条目守恒")
    # 换一个等长文本
    eq = {hashes[0]: "AlphA_0"}
    r2 = repack_inplace(p16, eq, os.path.join(d, "ip16b.le_strings"))
    chk(r2["written"] == 1 and not r2["overflow"], "inplace 等长替换成功且无溢出")
    chk(read_texts(os.path.join(d, "ip16b.le_strings"))[hashes[0]] == "AlphA_0",
        "inplace 等长替换内容正确")
    # 超长文本必须报告溢出而不是静默截断
    r3 = repack_inplace(p16, {hashes[0]: "A" * 200}, os.path.join(d, "ip16c.le_strings"))
    chk(r3["overflow"] == [hashes[0]], "★ inplace 超长文本被记为 overflow（不静默截断）")
    chk(read_texts(os.path.join(d, "ip16c.le_strings"))[hashes[0]] == "Alpha_0",
        "★ inplace 溢出时原文保持不变")

    # --- charlist 反向映射 ---
    cl = os.path.join(d, "charlist_chs.dat")
    with open(cl, "w", encoding="utf-8") as f:
        f.write("// charlist\ncount=3\n")
        f.write("\u200b\n")          # 零宽水印
        f.write("65\n")              # ≤0x100 恒等
        f.write("0x4F60\n")          # '你' > 0x100 → 槽位 0x100
        f.write("0x597D\n")          # '好'       → 槽位 0x101
    cm = parse_charlist(cl)
    rev = build_reverse_charmap(cm)
    chk(0x200B not in cm.values(), "charlist 跳过零宽水印")
    chk(rev.get(ord("你")) == 0x100, f"反向映射 你 → {hex(rev.get(ord('你'), -1))} (期望 0x100)")
    enc = encode_text("你", rev_charmap=rev, step=2)
    chk(struct.unpack_from("<H", enc, 0)[0] == 0x100, "encode_text 走 charlist 槽位")

    # --- 双步长 encode_text 终止符宽度 ---
    chk(encode_text("A", None, 2)[-2:] == b"\x00\x00", "UTF-16 终止符 2 字节")
    chk(encode_text("A", None, 4)[-4:] == b"\x00\x00\x00\x00", "UTF-32 终止符 4 字节")

    import shutil
    shutil.rmtree(d, ignore_errors=True)

    if verbose:
        print("[self_test] " + ("OK: 8 字节 offset 表读/写自洽, 条目数守恒, "
                                "UTF-16/UTF-32 双步长正确, charlist 反向映射生效"
                                if ok else "FAILED"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if self_test() else 1)
