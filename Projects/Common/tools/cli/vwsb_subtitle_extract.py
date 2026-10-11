#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vwsb_subtitle_extract.py —— 从 voices.vpp_pc 的 .bnk_pc 提取语音字幕（含官方多语言）

背景
════════════════════════════════════════════════════════════════════════
游戏内语音字幕**不在** le_strings 里，而在 `voices.vpp_pc` → `voc_*_media.bnk_pc`
的 `DMAV` 元数据块中。每个事件一块，内含 14 个语言槽，槽序 = 引擎 Language 枚举：

    0=US 1=ES 2=IT 3=JP 4=DE 5=FR 6=NL 7=SE 8=DK 9=CZ 10=PL 11=SK(韩) 12=RU 13=CH

★ 非拉丁语言（日/韩/俄/中）的槽内**不是** UTF-16 明文，而是「槽码」：
  码点 >= 0x100 时需经 `misc.vpp_pc` 内的 `charlist_<code>.dat` 查表还原。
  直接按 UTF-16 读会得到 `ӑƭŁŉפƤƅşƂŪƧŁ` 这类乱码 —— 这是本文件存在的唯一理由。

结构
════════════════════════════════════════════════════════════════════════
    VWSBPC  bank 头
      +0x14  u32 事件表字节数
      +0x18  u32 事件数
      +0x20  事件表：每项 16B
             SR3R(v6)：偏移字段在 +4
             SR4 (v10)：偏移字段在 +0   ← 两者布局不同，勿混
      +0x00  "DMAV" 块
             +0x04 u32 version  +0x0C u32 voiceline_id
             +0x18 u32 subtitle_size  +0x1C u32 subtitle_offset
    字幕表 = 0x24 + subtitle_offset
             u32 version
             version==2 → 28 槽（男/女两套 14；官方未实际使用，数据为复制）
             version==3 → 14 槽
             槽表后紧跟 UTF-16 文本池

用法
════════════════════════════════════════════════════════════════════════
  # 提取单游戏（默认输出 TSV，含全部语言列）
  python vwsb_subtitle_extract.py --game SR3R \
      --vpp "I:/.../cache/voices.vpp_pc" --misc "I:/.../cache/misc.vpp_pc" \
      --out subs_sr3r.tsv

  # 与现有 dict 比对，只输出缺口
  python vwsb_subtitle_extract.py --game SR4 --vpp ... --misc ... \
      --dict-dir ../../Resource/SR4/CHS/dict --gap-only --out gap_sr4.tsv

退出码: 0=成功; 1=参数/IO 错误
"""

import argparse
import os
import re
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from vpp_pack import (          # noqa: E402
    v6_read, v10_read, _v10_parse_dir, v10_fits_container, v10_unpack_container,
    lz4_decompress, CHUNK, V6_NEG, V6_MAGIC1, V6_MAGIC2,
)

# 语言槽序 = 引擎 Language 枚举（ThomasJepp.SaintsRow/Localization/Language.cs 同源）
SLOT_CODES = ["us", "es", "it", "jp", "de", "fr", "nl", "se",
              "dk", "cz", "pl", "sk", "ru", "zh"]

# 各游戏的语言索引 → 槽偏移字段位置
GAMES = {
    "SR3R": dict(vpp_version=6, offset_field=4,  subtitle_table_off=0x24),
    "SR4":  dict(vpp_version=10, offset_field=0, subtitle_table_off=0x24),
}


# ══════════════════════════════════════════════════════════════════════
# vpp 通用读取
# ══════════════════════════════════════════════════════════════════════
def _v6_phys_offsets(entries, data_off):
    """v6 物理布局：0x1000 对齐累积，**最后一个不 pad**。就地写入 phys_off。"""
    off = data_off
    n = len(entries)
    for i, e in enumerate(entries):
        e["phys_off"] = off
        size = e["csz"] if e["csz"] != V6_NEG else e["usz"]
        off += size if (i == n - 1) else ((size + CHUNK - 1) // CHUNK) * CHUNK
    return entries


def _v6_stream(ent, f):
    """v6 取条目真实字节（csz==NEG 为裸存，否则按 16B 头分块 LZ4）。"""
    f.seek(ent["phys_off"])
    if ent["csz"] == V6_NEG:
        return f.read(ent["usz"])
    blob = f.read(ent["csz"])
    out = bytearray()
    o = 0
    while o + 16 <= len(blob) and len(out) < ent["usz"]:
        m1, m2, l4l, u = struct.unpack_from("<4I", blob, o)
        if m1 != V6_MAGIC1 or m2 != V6_MAGIC2:
            break
        o += 16
        out += lz4_decompress(blob[o:o + l4l], l4l, u)
        o += l4l
    return bytes(out[:ent["usz"]])


def _v10_entry_bytes(e, data, base):
    """v10 取条目字节：裸 zlib（charlist/.le_strings 等）需按 csz 截断后解压。"""
    import zlib
    raw = data[base + e["data_off"]: base + e["data_off"] + e["usz"]]
    if raw[:2] in (b"\x78\x01", b"\x78\x5e", b"\x78\x9c", b"\x78\xda"):
        blob = data[base + e["data_off"]: base + e["data_off"] + e["csz"]]
        try:
            return zlib.decompress(blob)
        except zlib.error:
            return zlib.decompressobj().decompress(blob)
    if v10_fits_container(raw):
        inner = v10_unpack_container(raw)
        if inner:
            return next(iter(inner.values()))
    return raw


class VppReader:
    """统一封装 v6 / v10 的条目遍历与字节读取。"""

    def __init__(self, path, version):
        self.path = path
        self.version = version
        if version == 6:
            self.hdr, self.entries, _ = v6_read(path)
            _v6_phys_offsets(self.entries, self.hdr["data_off"])
            self._f = open(path, "rb")
            self._base = None
        else:
            with open(path, "rb") as f:
                self.data = f.read()
            self.hdr, self.entries = _v10_parse_dir(self.data)
            self._base = self.hdr["names_off"] + self.hdr["name_size"]
            self._f = None

    def read(self, e):
        if self.version == 6:
            return _v6_stream(e, self._f)
        return _v10_entry_bytes(e, self.data, self._base)

    def find(self, pred):
        return [e for e in self.entries if pred(e["name"])]

    def close(self):
        if self._f:
            self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# ══════════════════════════════════════════════════════════════════════
# charlist（槽码 → Unicode）
# ══════════════════════════════════════════════════════════════════════
def parse_charlist(blob):
    """charlist_<code>.dat → {槽码: 码点}

    格式：文本行，前若干行是 0x20 起的直通码（值 <= 0x100），
    其后每个 > 0x100 的十进制值按顺序占用 0x100, 0x101, ... 号槽。
    """
    m = {}
    nxt = 0x100
    for ln in blob.decode("utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("//") or ln.startswith("count="):
            continue
        try:
            v = int(ln)
        except ValueError:
            continue
        if v > 0x100:
            m[nxt] = v
            nxt += 1
        else:
            m[v] = v
    return m


def load_charlists(misc_path, version):
    """从 misc.vpp_pc 读出全部 charlist_<code>.dat → {code: map}"""
    out = {}
    with VppReader(misc_path, version) as r:
        for e in r.find(lambda n: re.match(r"^charlist_[a-z]{2}\.dat$", n.lower())):
            code = e["name"][9:11].lower()
            out[code] = parse_charlist(r.read(e))
    return out


def decode_slot(raw, code, maps):
    """槽内 UTF-16 字节 → 文本（>=0x100 的码点查 charlist）"""
    m = maps.get(code)
    out = []
    for i in range(0, len(raw) - 1, 2):
        c = struct.unpack_from("<H", raw, i)[0]
        if c == 0:
            break
        out.append(chr(m[c]) if (c >= 0x100 and m and c in m) else chr(c))
    return "".join(out)


# ══════════════════════════════════════════════════════════════════════
# 字幕提取
# ══════════════════════════════════════════════════════════════════════
def extract_subtitles(vpp_path, misc_path, game):
    """→ (rows, stats)。rows = [(bank, voiceline_id, {code: text})]"""
    cfg = GAMES[game]
    maps = load_charlists(misc_path, cfg["vpp_version"])
    off_field = cfg["offset_field"]
    rows = []
    stats = dict(banks=0, banks_with_sub=0, events=0, blocks=0, bad=0)

    with VppReader(vpp_path, cfg["vpp_version"]) as r:
        banks = r.find(lambda n: n.lower().endswith(".bnk_pc"))
        stats["banks"] = len(banks)
        for e in banks:
            d = r.read(e)
            if d[:8] != b"VWSBPC  ":
                continue
            cnt = struct.unpack_from("<I", d, 0x18)[0]
            stats["events"] += cnt
            n_sub_here = 0
            for i in range(cnt):
                off = struct.unpack_from("<I", d, 0x20 + i * 16 + off_field)[0]
                if off + 0x24 > len(d) or d[off:off + 4] != b"DMAV":
                    continue
                (_ver, _p, vl, _ls, _lo, sbs, sbo, _w) = \
                    struct.unpack_from("<IIIIIIII", d, off + 4)
                if sbs == 0:
                    continue
                blk = off + cfg["subtitle_table_off"] + sbo
                if blk + 4 > len(d):
                    continue
                bver = struct.unpack_from("<I", d, blk)[0]
                n_slot = 28 if bver == 2 else 14
                if blk + 4 + 8 * n_slot > len(d):
                    stats["bad"] += 1
                    continue
                stats["blocks"] += 1
                n_sub_here += 1
                pool = blk + 4 + 8 * n_slot
                d2 = {}
                for s in range(n_slot):
                    L, O = struct.unpack_from("<II", d, blk + 4 + 8 * s)
                    if L == 0 or L > 0x8000 or pool + O + L > len(d):
                        continue
                    code = SLOT_CODES[s % 14]
                    if code in d2:      # version 2 的 B 表是 A 表副本，跳过
                        continue
                    d2[code] = decode_slot(d[pool + O:pool + O + L], code, maps)
                if d2.get("us", "").strip():
                    rows.append((e["name"], vl, d2))
            if n_sub_here:
                stats["banks_with_sub"] += 1
    return rows, stats


# ══════════════════════════════════════════════════════════════════════
# dict 读取 + 运行时匹配规则
# ══════════════════════════════════════════════════════════════════════
_LE_LINE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _unescape(s):
    return (s.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\"))


def load_dict_keys(dict_dir, us_map=None):
    """读 dict 目录**全部** *.txt（复刻 LoadDictDir），返回键集合。

    us_map 非空时，键中 >=0x100 的字符先过 charlist_us 解码 —— 修正历史上
    提取时漏解码留下的 `ŭ`(=…) 这类死键。
    """
    keys = set()
    if not os.path.isdir(dict_dir):
        return keys
    for fn in sorted(os.listdir(dict_dir)):
        if not fn.lower().endswith(".txt"):
            continue
        p = os.path.join(dict_dir, fn)
        with open(p, encoding="utf-8-sig", errors="replace") as f:
            for ln in f:
                m = _LE_LINE.match(ln)
                if not m:
                    continue
                k = _unescape(m.group(1))
                if us_map:
                    k = "".join(chr(us_map[ord(c)]) if (ord(c) >= 0x100 and ord(c) in us_map)
                                else c for c in k)
                keys.add(k)
                keys.add(_normalize_spaces(k))
                keys.add(k.strip())
    return keys


def _normalize_spaces(s):
    """复刻 DLL 的 NormalizeKey：压连续空格（**不做**小写化）"""
    out = []
    for c in s:
        if c == " " and out and out[-1] == " ":
            continue
        out.append(c)
    return "".join(out)


def runtime_variants(s):
    """复刻 LookupNode 的尝试顺序：原样 → TrimRange → NormalizeKey"""
    v = [s]
    for alt in (s.strip(), _normalize_spaces(s)):
        if alt != s and alt not in v:
            v.append(alt)
    return v


def is_covered(text, keyset):
    return any(v in keyset for v in runtime_variants(text))


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="从 voices.vpp_pc 提取 .bnk_pc 语音字幕（含官方多语言）")
    ap.add_argument("--game", required=True, choices=sorted(GAMES))
    ap.add_argument("--vpp", required=True, help="voices.vpp_pc 路径")
    ap.add_argument("--misc", required=True, help="misc.vpp_pc 路径（取 charlist）")
    ap.add_argument("--out", required=True, help="输出 TSV 路径")
    ap.add_argument("--dict-dir", default=None,
                    help="给定则与之比对；配合 --gap-only 只输出未覆盖项")
    ap.add_argument("--gap-only", action="store_true", help="只输出 dict 未覆盖的字幕")
    ap.add_argument("--langs", default="jp,ru,de,fr,it,es,pl,nl,cz",
                    help="附加输出哪些语言列（逗号分隔；EN 恒为第一列，勿含 us）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    for p in (args.vpp, args.misc):
        if not os.path.isfile(p):
            print(f"错误: 文件不存在 {p}", file=sys.stderr)
            return 1

    langs = [c.strip().lower() for c in args.langs.split(",") if c.strip() and c.strip().lower() != "us"]
    rows, stats = extract_subtitles(args.vpp, args.misc, args.game)

    uniq = {}
    for bank, vl, d2 in rows:
        uniq.setdefault(d2["us"], (d2["us"], d2))
    if not args.quiet:
        print(f"[{args.game}] bank={stats['banks']} 含字幕={stats['banks_with_sub']} "
              f"事件={stats['events']} 字幕块={stats['blocks']} 越界={stats['bad']}")
        print(f"[{args.game}] 字幕行={len(rows)} 唯一英文={len(uniq)}")

    selected = []
    if args.dict_dir:
        with VppReader(args.misc, GAMES[args.game]["vpp_version"]) as r:
            us_map = None
            for e in r.find(lambda n: n.lower() == "charlist_us.dat"):
                us_map = parse_charlist(r.read(e))
                break
        keys = load_dict_keys(args.dict_dir, us_map)
        for k, (en, d2) in uniq.items():
            if not is_covered(en, keys):
                selected.append((en, d2))
        if not args.quiet:
            print(f"[{args.game}] dict 键={len(keys)} 需补翻={len(selected)} "
                  f"({100.0 * len(selected) / max(len(uniq), 1):.1f}%)")
    else:
        selected = [v for v in uniq.values()]
        selected.sort(key=lambda x: x[0])

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(["EN"] + [c.upper() for c in langs]) + "\r\n")
        for en, d2 in selected:
            cells = [en] + [d2.get(c, "") for c in langs]
            f.write("\t".join(
                c.replace("\t", " ").replace("\r", " ").replace("\n", "\\n")
                for c in cells) + "\r\n")
    if not args.quiet:
        print(f"输出: {args.out}  ({len(selected)} 行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
