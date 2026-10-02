"""lint_sense.py —— L3 歧义义项错配检测（只读，只出建议不改文件）。

对应需求
--------
``BACK`` 被译成「背部」、``Build`` 被译成「建造」这类**一词多义取错义项**。

核心设计：必须带上下文
----------------------
实测反例证明**不能靠词表全局替换**：

  - ``SR3/CHS/le_string/static_us.txt:1337``
    ``"TAT_REG_BACK": "BACK"``  →  ``"背部"``
    key 前缀是 ``TAT_REG_``（纹身部位正则），但**同文件邻近键全是
    ``VCUST_WHEEL_WIDTH_OPTION`` / ``STAT_WEAPON_SNIPER`` 这类服装属性**，
    说明此处 ``BACK`` 实为 UI「返回」，被纹身词表污染 → **误译**。
  - ``SR4/CHS/le_string/customize_us.txt``
    ``"WHOLE BACK"`` → ``"整个背部"``   躯干纹身 → **正确**。

两者英文都含 BACK，**只有 key 前缀 + 所在文件能区分**。所以本工具：
  1. 先按「英文词 × 中文域」做语义域错配检测；
  2. 再用 ``key_prefix`` + 邻近条目做上下文复核，压低误报；
  3. 输出 ``review_*.jsonl``，含完整上下文包，供人工/AI 逐条判定。

**本工具绝不修改 Resource 文件。**

用法
----
    python lint_sense.py
    python lint_sense.py --jsonl review.jsonl
    python lint_sense.py --min-confidence high
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "helper"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import le_resource as R  # noqa: E402
import sense_lexicon as S  # noqa: E402

HIGH = "high"
MEDIUM = "medium"
LOW = "low"

SEV = {HIGH: "ERROR", MEDIUM: "WARN", LOW: "INFO"}


class Finding:
    __slots__ = ("confidence", "code", "game", "carrier", "file", "line", "key",
                 "source", "text", "en_word", "zh_word", "expect_domain",
                 "actual_domain", "reason", "context")

    def __init__(self, confidence, code, entry: R.Entry, reason: str,
                 en_word="", zh_word="", expect_domain="", actual_domain="",
                 context=None):
        self.confidence = confidence
        self.code = code
        self.game = getattr(entry, "game", "")
        self.carrier = getattr(entry, "carrier", "")
        self.file = entry.file
        self.line = entry.line
        self.key = entry.key
        self.source = entry.source_text or ""
        self.text = entry.text
        self.en_word = en_word
        self.zh_word = zh_word
        self.expect_domain = expect_domain
        self.actual_domain = actual_domain
        self.reason = reason
        self.context = context or {}

    def as_dict(self) -> dict:
        return {
            "confidence": self.confidence,
            "severity": SEV[self.confidence],
            "code": self.code,
            "game": self.game, "carrier": self.carrier,
            "file": self.file, "line": self.line, "key": self.key,
            "source": self.source, "text": self.text,
            "en_word": self.en_word, "zh_word": self.zh_word,
            "expect_domain": self.expect_domain,
            "actual_domain": self.actual_domain,
            "reason": self.reason,
            "context": self.context,
        }

    def __str__(self) -> str:
        return (f"{SEV[self.confidence]:5s} {self.code:22s} "
                f"{self.file}:{self.line}\n"
                f"      key : {self.key[:60]}\n"
                f"      英文: {self.source[:66]!r}\n"
                f"      译文: {self.text[:66]!r}\n"
                f"      [{self.reason}]")


# ---------------------------------------------------------------- 邻近上下文

def build_file_index(games: list[str]) -> dict[str, list[R.Entry]]:
    """文件 -> 该文件全部 CHS 条目（供取邻近条目）。"""
    idx: dict[str, list[R.Entry]] = {}
    for g in games:
        for carrier in ("le_string", "dict"):
            for e in R.load_entries(g, "CHS", carrier):
                e.game = g  # type: ignore[attr-defined]
                e.carrier = carrier  # type: ignore[attr-defined]
                idx.setdefault(e.file, []).append(e)
    for v in idx.values():
        v.sort(key=lambda x: x.line)
    return idx


def neighbor_context(idx: dict[str, list[R.Entry]], entry: R.Entry,
                     window: int = 3) -> dict:
    """取同文件内前后各 window 条，供判定语境。"""
    lst = idx.get(entry.file, [])
    pos = None
    for i, e in enumerate(lst):
        if e.line == entry.line:
            pos = i
            break
    if pos is None:
        return {}
    before = [{"key": e.key[:40], "text": e.text[:40]} for e in lst[max(0, pos - window):pos]]
    after = [{"key": e.key[:40], "text": e.text[:40]} for e in lst[pos + 1:pos + 1 + window]]
    return {"before": before, "after": after}


# ---------------------------------------------------------------- 强信号检测

def check_strong_pairs(games: list[str], idx) -> list[Finding]:
    """基于 STRONG_PAIRS 的高精度检测。"""
    out: list[Finding] = []
    for g in games:
        pairs_iter = _iter_pairs(g, idx)
        for entry, src in pairs_iter:
            zh = entry.text
            for en_word, expect_d, zh_word, actual_d, why in S.STRONG_PAIRS:
                if actual_d == expect_d:
                    continue  # 示例行，不报
                if not re.search(rf"\b{re.escape(en_word)}\b", src, re.I):
                    continue
                if zh_word not in zh:
                    continue
                # 上下文复核：key 前缀或源文本身指向身体/外观语境则可能反而正确
                if S.body_context(entry.key, src):
                    out.append(Finding(
                        LOW, "sense_mismatch", entry,
                        f"{why}（但源文/key 指向外观或纹身语境，需人工确认）",
                        en_word, zh_word, expect_d, actual_d,
                        neighbor_context(idx, entry),
                    ))
                    continue
                conf = HIGH
                # key 前缀明确是 UI → 更高置信
                if S.ui_context(entry.key):
                    conf = HIGH
                out.append(Finding(
                    conf, "sense_mismatch", entry, why,
                    en_word, zh_word, expect_d, actual_d,
                    neighbor_context(idx, entry),
                ))
    return out


# ---------------------------------------------------------------- 通用域错配

def check_domain_mismatch(games: list[str], idx) -> list[Finding]:
    """通用检测：源文某英文词的义项域 vs 译文主导中文域。"""
    out: list[Finding] = []
    for g in games:
        for entry, src in _iter_pairs(g, idx):
            toks = set(re.findall(r"[A-Za-z]+", src.upper()))
            matched = [w for w in S.EN_SENSE if w in toks or
                       (len(w) > 3 and w in src.upper())]
            if not matched:
                continue
            zh_dom = S.dominant_zh_domain(entry.text)
            if not zh_dom:
                continue
            zh_hits = [w for w in S.zh_words(entry.text)
                       if S.ZH_DOMAIN[w] == zh_dom]
            for en_word in matched:
                senses = S.EN_SENSE[en_word]
                if len(senses) < 2:
                    continue  # 只有单义，不构成歧义
                # 若译文的域是某义项的合法域，则不算错
                if any(d == zh_dom for _, d in senses):
                    continue
                if S.body_context(entry.key, src) and zh_dom == S.DOMAIN_BODY:
                    continue
                out.append(Finding(
                    MEDIUM, "domain_mismatch", entry,
                    f"英文 {en_word} 的义项不属于「{zh_dom}」域，"
                    f"但译文含该域词 {'/'.join(zh_hits[:3])}",
                    en_word, "/".join(zh_hits[:3]),
                    "/".join(d for _, d in senses), zh_dom,
                    neighbor_context(idx, entry),
                ))
    return out


def _iter_pairs(g: str, idx):
    """产出 (CHS条目, 英文源文)。dict 的源文是 key，le_string 取ENG 侧。"""
    for c, e in R.load_aligned(g, "le_string"):
        if e is None:
            continue
        c.game = g  # type: ignore[attr-defined]
        c.carrier = "le_string"  # type: ignore[attr-defined]
        yield c, e.text
    for e in R.load_entries(g, "CHS", "dict"):
        e.game = g  # type: ignore[attr-defined]
        e.carrier = "dict"  # type: ignore[attr-defined]
        # dict 的 key 就是英文原文，须显式赋给 source_text，
        # 否则报告里「英文」列会是空的（v1 实测踩过）。
        e.source_text = e.key
        yield e, e.key


# ---------------------------------------------------------------- 主流程

def run(games: list[str] | None = None) -> list[Finding]:
    games = games or list(R.GAMES)
    idx = build_file_index(games)
    fs = check_strong_pairs(games, idx)
    seen = {(f.file, f.line, f.code) for f in fs}
    for f in check_domain_mismatch(games, idx):
        if (f.file, f.line, f.code) not in seen:
            fs.append(f)
    fs.sort(key=lambda f: ({HIGH: 0, MEDIUM: 1, LOW: 2}[f.confidence],
                           f.file, f.line))
    return fs


def report(fs: list[Finding], min_conf: str = LOW, limit: int = 0) -> None:
    order = {HIGH: 0, MEDIUM: 1, LOW: 2}
    sel = [f for f in fs if order[f.confidence] >= order[min_conf]]
    c = Counter(f.confidence for f in sel)
    print("=" * 72)
    print("L3 歧义义项错配报告（只出建议，不改文件）")
    print("=" * 72)
    for k in (HIGH, MEDIUM, LOW):
        if c.get(k):
            print(f"  {SEV[k]:5s} {k:8s} {c[k]:6d}")
    print("-" * 72)
    print(f"  合计 {len(sel)} 条（阈值 {min_conf}）\n")
    for f in (sel if limit <= 0 else sel[:limit]):
        print(str(f))
    if 0 < limit < len(sel):
        print(f"\n... 另有 {len(sel) - limit} 条")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L3 义项错配检测（只读）")
    ap.add_argument("--game", action="append", choices=list(R.GAMES))
    ap.add_argument("--min-confidence", default=LOW,
                    choices=[HIGH, MEDIUM, LOW])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jsonl", help="导出复核包（含上下文）")
    a = ap.parse_args(argv)

    fs = run(a.game)
    report(fs, a.min_confidence, a.limit)

    if a.jsonl:
        with open(a.jsonl, "w", encoding="utf-8") as f:
            for x in fs:
                f.write(json.dumps(x.as_dict(), ensure_ascii=False) + "\n")
        print(f"\n复核包已写入 {a.jsonl}（{len(fs)} 条，含邻近条目上下文）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
