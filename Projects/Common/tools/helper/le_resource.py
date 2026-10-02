"""le_resource.py —— Resource 目录（le_string / dict）的统一读取层。

设计要点（全部来自语料实测，勿凭直觉改）：

1. 两种载体，格式不同
   - le_string: ``"HASH_XXXXXXXX": "文本"``   行尾**无**分号
   - dict:      ``"英文原文": "中文译文";``     行尾**有**分号
   解析器必须分别处理，不能共用一条正则。

2. ``dict`` 的 key 就是英文原文
   所以 ``SR3/ENG/dict`` 是空的（只有 .gitkeep）属于正常，**不是缺数据**。
   单目录即可校对，无需补英文源。

3. dict 的 key **允许内嵌中文**
   写解析器时不要假设 key 一定是 ASCII。

4. CHS 侧 le_string 文件头有 ``# TODO: ...`` / ``# Total entries: N`` 等注释行。
   按「行首（去空白后）是双引号」过滤即可，注释行自然被排除。

5. 编码一律 utf-8 / utf-8-sig 兜底；行尾一律归一到 LF 语义（不落盘）。

6. 转义：源文里常见 ``\\"`` 与字面 ``\\n``（两字符）。
   用 JSON 字符串解析可正确还原，不要手工反转义。
"""

from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass, field
from typing import Iterator

# 仓库内 Resource 根目录的默认位置（相对本文件上溯）
_HERE = os.path.dirname(os.path.abspath(__file__))
# .../Projects/Common/tools/helper/  ->  仓库根 = 上溯 4 层
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
RESOURCE_DIR = os.path.join(REPO_ROOT, "Resource")
GLOSSARY_CSV = os.path.join(REPO_ROOT, "Documents", "glossary.csv")

GAMES = ("SR3", "SR4")
LANGS = ("CHS", "ENG", "CHT")
CARRIERS = ("le_string", "dict")

# 注释行（dict 与 le_string 通用）
_COMMENT = re.compile(r"^\s*(//|#)")

# 判定「这一行是数据行」
_ENTRY_START = re.compile(r'^\s*"')


@dataclass
class Entry:
    """一条译文记录。"""

    key: str
    """le_string 的 HASH key，或 dict 的英文原文。"""

    text: str
    """译文（CHS）��"""

    file: str
    """所属文件的仓库相对路径，如 Resource/SR4/CHS/dict/voice_001.txt。"""

    line: int
    """1-based 行号。"""

    raw: str = ""
    """原始行文本（不含换行）。"""

    # 仅 le_string 有意义
    source_text: str | None = None
    """对应英文原文（仅 le_string 且存在 ENG 平行目录时有值）。"""

    source_file: str | None = None
    """英文来源文件路径。"""


@dataclass
class FileMeta:
    path: str
    game: str
    lang: str
    carrier: str
    entry_count: int = 0
    header: list[str] = field(default_factory=list)
    crlf: bool = False
    """源文件是否含 CRLF。"""


def read_text(path: str) -> str:
    """读文本并统一处理 BOM / 换行。容错到utf-8-sig。"""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _unquote(s: str) -> str:
    """把 JSON 字符串字面量（含转义）还原为真实文本。"""
    try:
        return json.loads(s)
    except Exception:
        # 容错：至少去掉外层引号与最常见的转义
        t = s[1:-1] if len(s) >= 2 and s[0] == '"' and s[-1] == '"' else s
        return t.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def _parse_line(line: str) -> tuple[str, str] | None:
    """解析一行数据行，返回 (key, text)；非数据行返回 None。

    两种载体共用一条宽松正则，靠「可选的尾部分号 + 贪婪取到末尾的引号」兼容。
    """
    m = re.match(r'^\s*("(?:[^"\\]|\\.)*")\s*:\s*("(?:[^"\\]|\\.)*")\s*;?\s*$', line)
    if m:
        return _unquote(m.group(1)), _unquote(m.group(2))
    return None


def iter_file(path: str, rel_to: str | None = None) -> Iterator[Entry]:
    """逐条产出一个文件里的所有 Entry。"""
    text = read_text(path)
    rel = rel_to or path
    for i, line in enumerate(text.split("\n"), 1):
        if not _ENTRY_START.match(line):
            continue
        parsed = _parse_line(line)
        if parsed is None:
            continue
        k, v = parsed
        yield Entry(key=k, text=v, file=rel, line=i, raw=line.rstrip("\r"))


def load_file(path: str, rel_to: str | None = None) -> list[Entry]:
    return list(iter_file(path, rel_to))


def rel(path: str, root: str | None = None) -> str:
    root = root or REPO_ROOT
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")


def carrier_dir(
    game: str,
    lang: str,
    carrier: str,
    resource_dir: str | None = None,
) -> str:
    return os.path.join(resource_dir or RESOURCE_DIR, game, lang, carrier)


def list_carrier_files(
    game: str,
    lang: str,
    carrier: str,
    resource_dir: str | None = None,
) -> list[str]:
    """列出某目录下所有 .txt，按名排序（保证幂等顺序）。"""
    d = carrier_dir(game, lang, carrier, resource_dir)
    if not os.path.isdir(d):
        return []
    return [
        os.path.join(d, f)
        for f in sorted(os.listdir(d))
        if f.endswith(".txt") and os.path.isfile(os.path.join(d, f))
    ]


def probe_meta(
    game: str,
    lang: str,
    carrier: str,
    resource_dir: str | None = None,
) -> list[FileMeta]:
    """读取目录下每个文件的元信息（不解析全部内容用于报告）。"""
    out = []
    for p in list_carrier_files(game, lang, carrier, resource_dir):
        text = read_text(p)
        meta = FileMeta(
            path=p,
            game=game,
            lang=lang,
            carrier=carrier,
            crlf=("\r\n" in text),
        )
        for line in text.split("\n"):
            if _COMMENT.match(line):
                meta.header.append(line.strip())
            elif _ENTRY_START.match(line) and _parse_line(line):
                meta.entry_count += 1
        out.append(meta)
    return out


def load_entries(
    game: str,
    lang: str,
    carrier: str,
    resource_dir: str | None = None,
) -> list[Entry]:
    """载入某 game/lang/carrier 下的全部条目。

    ⚠ 保留**全部行**，不按 key 去重。实测存在大量「同key 跨文件复用」，
    这是设计使然，不是 bug：
      - dict：`exe_hardcoded.txt` 与 `le_data_supplement.txt` 共享 key
        （如 ``FIGHT CLUB`` 在 SR4/CHS 出现 5 次）
      - le_string：DLC 6/7 互相复用 key（``HASH_EAEE44C8``、
        ``cm_suit_digidino`` 等）
    因此**凡按 key 聚合的逻辑，必须改用 (key, file) 或 (key, file, line)**。
    """
    out: list[Entry] = []
    for p in list_carrier_files(game, lang, carrier, resource_dir):
        out.extend(iter_file(p, rel(p)))
    return out


def key_occurrences(entries: list[Entry]) -> dict[str, list[Entry]]:
    """按 key 聚合全部出现位置（不做去重）。"""
    out: dict[str, list[Entry]] = {}
    for e in entries:
        out.setdefault(e.key, []).append(e)
    return out


def key_conflicts(entries: list[Entry]) -> dict[str, list[Entry]]:
    """只返回「同一 key 出现多次」的那些 key。"""
    return {k: v for k, v in key_occurrences(entries).items() if len(v) > 1}


def load_aligned(
    game: str,
    carrier: str,
    resource_dir: str | None = None,
) -> list[tuple[Entry, Entry]]:
    """载入 le_string 的 (CHS, ENG) 对齐条目。

    ⚠ 同key 跨文件复用时（见 load_entries 注释），返回的配对是
    **按文件内出现顺序**的一一对应，仅供「同一文件内比对该key」使用。
    要按 key 聚合请改用 key_occurrences。
    """
    resource_dir = resource_dir or RESOURCE_DIR
    chs_list = load_entries(game, "CHS", carrier, resource_dir)
    eng_list = load_entries(game, "ENG", carrier, resource_dir)
    eng_by_key: dict[str, list[Entry]] = {}
    for e in eng_list:
        eng_by_key.setdefault(e.key, []).append(e)
    used: dict[str, int] = {}
    pairs: list[tuple[Entry, Entry]] = []
    for c in chs_list:
        lst = eng_by_key.get(c.key)
        e = None
        if lst:
            idx = used.get(c.key, 0)
            if idx < len(lst):
                e = lst[idx]
                used[c.key] = idx + 1
        c.source_text = e.text if e else None
        c.source_file = e.file if e else None
        pairs.append((c, e))  # type: ignore[arg-type]
    return pairs


# ---------------------------------------------------------------- 术语表

_CJK = re.compile(r"[\u4e00-\u9fff]")
# 常见繁体字（用于简繁混用检查）。取一批高频、有明确简繁对应的，避免误报。
TRAD_CHARS = set(
    "這個們來時個為說對後從還會經麼樣點兒龍鳳愛樂機電腦網絡資訊個關開門間"
    "東車馬鳥魚長風飛食馬體黃黑藍綠紅銀鐵鋼頭臉眼耳鼻嘴"
    "們個這來時後從還經會與務動員應該樣點兒覺學實現發對戰鬥將軍"
    "聲響語話談請謝誰認證識記憶論壇續題專業務產品質"
)


def has_cjk(s: str) -> bool:
    return bool(_CJK.search(s))


def strip_index_suffix(s: str) -> str:
    """剥离尾部的编号后缀：``贴花 10`` -> ``贴花``。用于「编号不算冲突」判定。"""
    return re.sub(r"\s+\d+\s*$", "", s.strip())


def load_glossary(path: str | None = None) -> list[dict]:
    """读术语表 CSV。**必须用 utf-8-sig**（文件带 BOM，Excel 友好）。

    ⚠⚠ 血泪教训（2026-10-02）：用``encoding="utf-8"`` 读会拿到
    首列名 ``'\\ufeff类型'``，于是
        ``csv.DictWriter(f, fieldnames=['类型',...])``
    会抛 ``ValueError: dict contains fields not in fieldnames: '\\ufeff类型'``。
    更糟的是：异常发生在**写入过程中**，目标 CSV 已被打开截断 →
    **整个术语表被清空只剩表头**（164 行 → 1 行），且该文件当时未入库，
    git 无法恢复，只能从裁决配置手工重建。
    故本函数**只允许 utf-8-sig**，禁止改动。
    """
    import csv

    p = path or GLOSSARY_CSV
    if not os.path.isfile(p):
        return []
    with open(p, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    # 兜底：万一首列名带了 BOM，剥掉，避免下游 writer 报错
    if rows and rows[0]:
        first = next(iter(rows[0]))
        if first.startswith("\ufeff"):
            for r in rows:
                r.pop(first, None)
                r["类型"] = r.pop(first.lstrip("\ufeff"), "")
    return rows


def write_glossary(rows: list[dict], path: str | None = None) -> None:
    """写术语表 CSV。**唯一安全的写入口**（utf-8-sig + 先写临时文件再替换）。

    ⚠ 严禁用裸 ``open(path,"w",encoding="utf-8-sig")`` + csv.DictWriter 直接写目标文件：
    一旦中途抛异常（字段名不匹配等），目标文件已被截断，数据全丢。
    本函数先写 ``*.tmp``，成功后再 ``os.replace`` 原子替换。
    """
    import csv

    p = path or GLOSSARY_CSV
    fields = ["类型", "英文", "简中", "备注"]
    tmp = p + ".tmp"
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    os.replace(tmp, p)


if __name__ == "__main__":
    # 自检：打印规模统计
    total = 0
    for g in GAMES:
        for c in CARRIERS:
            for lang in LANGS:
                metas = probe_meta(g, lang, c)
                n = sum(m.entry_count for m in metas)
                total += n
                if n:
                    print(f"{g}/{lang}/{c:10s} {n:6d} 条  ({len(metas)} 文件)")
    print(f"合计 {total} 条")
    g = load_glossary()
    print(f"术语表 {len(g)} 条")
