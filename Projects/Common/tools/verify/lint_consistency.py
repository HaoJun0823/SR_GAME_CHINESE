"""lint_consistency.py —— L2 一致性冲突检查（只读）。

检查项
------
1. **同源多译**  同一英文原文 → 多个不同中文译法。
   ⚠ **必须先分三类**，否则误报淹没真问题：
     - 真冲突   ``FIGHT CLUB`` → 搏击/斗拳/格斗俱乐部  → 需定一个
     - 合理差异 ``Yep.`` → 嗯。/对。/是的。      → 不同角色语气，白名单放行
     - 编号后缀 ``decal`` → 贴花/贴花 1..10       → 自动归一，不算冲突
2. **术语表违背** 译文未命中 ``Documents/glossary.csv`` 的权威译法。
3. **跨代漂移** SR3 与 SR4 共有文本译法不一致（同世界观，理应一致）。

用法
----
    python lint_consistency.py
    python lint_consistency.py --kind conflict#术语表违背     跑指定检查
    python lint_consistency.py --json out.json
    python lint_consistency.py --strict
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
import lint_exempt as X  # noqa: E402

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"

# ---------------------------------------------------------------- 合理差异白名单
# 同源多译但**属正当差异**的（角色语气、语域差异等）。逐条写明理由。
# 这些不应被当作冲突去「统一」，强改会毁掉角色性格。
REASONABLE_DIFF: list[tuple[str, str]] = [
    (r"^Yep\.?$", "角色语气差异：对/嗯/是的"),
    (r"^Yes\.?$", "角色语气差异：对/是/什么事"),
    (r"^No\.?$", "角色语气差异：不/否/……不"),
    (r"^Okay\.?$", "角色语气差异：好/行/好的"),
    (r"^Oh\.?$", "角色语气差异：哦/噢"),
    (r"^Ah\.?\.?$", "角色语气差异：啊/嗯"),
    (r"^Right\.?$", "角色语气差异：对/好"),
    (r"^What\??$", "角色语气差异：什么/啥"),
    (r"^Really\??$", "角色语气差异：真的？/是吗"),
    (r"^Good\.$", "角色语气差异：好/不错"),
    (r"^Nice\.?$", "角色语气差异：不错/很好"),
    (r"^Come\w*\s+on[.!]?$", "角色语气差异：来吧/加油"),
    (r"^Let's\s+go[.!]?$", "角色语气差异：走吧/走"),
    (r"^Not\s+funny\.$", "角色语气差异"),
    (r"^Bullshit\.$", "角色语气差异：粗口译法多样"),
    (r"^Fuck\.\s*That\.$", "角色语气差异：粗口译法多样"),
    (r"^Easy\.\.$", "角色语气差异：放屁/胡扯"),
]

# 界面/系统层的同义差异（也可视为需统一，但优先级低，先报 WARN）
SYSTEM_SYNONYM: list[tuple[str, str]] = [
    (r"^(START|BEGIN)\s+(\w+)\s+STRONGHOLD$", "「启动/开始 + 据点」句式不统一"),
    (r"^Are\s+you\s+sure\?$", "确定吗？/你确定？"),
    (r"^'?\d\d%\s+OFF", "折扣文案句式"),
]


def _norm_for_group(s: str) -> str:
    """归一化后用于「是否只是编号差异」的比较。"""
    return R.strip_index_suffix(s).strip().lower()


class Issue:
    __slots__ = ("severity", "code", "game", "file", "line", "key",
                 "source", "text", "detail")

    def __init__(self, severity, code, detail, key="", source="", text="",
                 file="", line=0, game=""):
        self.severity = severity
        self.code = code
        self.detail = detail
        self.key = key
        self.source = source
        self.text = text
        self.file = file
        self.line = line
        self.game = game

    def as_dict(self) -> dict:
        return {
            "severity": self.severity, "code": self.code, "game": self.game,
            "file": self.file, "line": self.line, "key": self.key,
            "source": self.source, "text": self.text, "detail": self.detail,
        }

    def __str__(self) -> str:
        loc = f"{self.file}:{self.line}" if self.file else "-"
        return (f"{self.severity:5s} {self.code:20s} {loc}\n"
                f"      英文: {self.source[:70]!r}\n"
                f"      译法: {self.text[:70]!r}\n"
                f"      [{self.detail}]")


# ---------------------------------------------------------------- 1. 同源多译

def collect_en2zh(games: list[str]) -> dict[str, list[R.Entry]]:
    """汇总「英文原文 -> 该英文的所有译文出现」。"""
    out: dict[str, list[R.Entry]] = defaultdict(list)
    for g in games:
        # le_string：key 是 HASH，源文取 ENG 侧文本
        for c, e in R.load_aligned(g, "le_string"):
            if e is None:
                continue
            src = e.text.strip()
            if not src or not re.search(r"[A-Za-z]", src):
                continue
            c.game = g  # type: ignore[attr-defined]
            c.carrier = "le_string"  # type: ignore[attr-defined]
            out[src].append(c)
        # dict：key 就是英文原文
        for e in R.load_entries(g, "CHS", "dict"):
            src = e.key.strip()
            if not src or not re.search(r"[A-Za-z]", src):
                continue
            e.game = g  # type: ignore[attr-defined]
            e.carrier = "dict"  # type: ignore[attr-defined]
            out[src].append(e)
    return out


def classify_diff(src: str, variants: list[str]) -> tuple[str, str]:
    """把「同源多译」分类。返回 (类别, 说明)。

    类别：
      conflict    真冲突（专名/术语级不一致，ERROR）
      reasonable  合理差异（角色语气、拟声词，INFO）
      numbered    编号后缀（不报）
      system      系统层同义（句式不统一，WARN）
      dialogue    对白长句同源不同译（同一场景不同角色说，WARN）
    """
    s = src.strip()
    uniq = sorted({v.strip() for v in variants})
    # 先剥离编号后缀再看
    normed = {_norm_for_group(v) for v in uniq}
    if len(normed) == 1:
        return "numbered", "仅编号后缀差异（贴花 / 贴花 1..10）"
    for pat, why in REASONABLE_DIFF:
        if re.match(pat, s, re.I):
            return "reasonable", why
    for pat, why in SYSTEM_SYNONYM:
        if re.match(pat, s, re.I):
            return "system", why
    # 拟声词 / 括号内的舞台提示：*sigh*、*laughing*、[BEEP] 等
    if _is_stage_direction(s):
        return "reasonable", "拟声词/舞台提示，译法多样属正常"
    # 长对白：同一句在不同场景由不同角色说出，措辞差异合理
    if len(s.split()) > 3 or len(s) > 40:
        return "dialogue", "对白长句，同源不同场景措辞差异"
    return "conflict", f"专名/术语级不一致：{len(uniq)} 种译法"


_STAGE_PAT = re.compile(
    r"^[\s*\[(]*[a-z' ]+[\s*\])]*$", re.I
)


def _is_stage_direction(src: str) -> bool:
    """是否为拟声词/舞台提示（*sigh*、[BEEP]、(laughs) 等）。"""
    s = src.strip()
    if s.count("*") >= 2:
        return True
    m = re.fullmatch(r"\*([^*]{1,30})\*", s)
    if m:
        return True
    if re.fullmatch(r"[\[\(][^\]\)]{1,30}[\]\)]", s):
        return True
    return False


def check_same_source_conflicts(
    games: list[str],
    only_conflict: bool = True,
) -> list[Issue]:
    en2zh = collect_en2zh(games)
    issues: list[Issue] = []
    for src, entries in en2zh.items():
        variants = [e.text for e in entries if R.has_cjk(e.text)]
        if len({v.strip() for v in variants}) < 2:
            continue
        kind, why = classify_diff(src, variants)
        if kind == "numbered":
            continue
        if only_conflict and kind == "reasonable":
            continue
        sev = {
            "conflict": ERROR,
            "system": WARN,
            "dialogue": WARN,
        }.get(kind, INFO)
        code = {
            "conflict": "same_source_conflict",
            "reasonable": "same_source_reasonable",
            "system": "same_source_system",
            "dialogue": "same_source_dialogue",
        }[kind]
        uniq = sorted({v.strip() for v in variants})
        uniq_cn = [u for u in uniq if R.has_cjk(u)]
        if len(uniq_cn) < 2 and kind == "conflict":
            continue
        first = entries[0]
        issues.append(Issue(
            sev, code, f"{why}：{' / '.join(u[:24] for u in uniq[:6])}",
            key=first.key, source=src, text=" | ".join(uniq[:6]),
            file=first.file, line=first.line, game=first.game,  # type: ignore[attr-defined]
        ))
    issues.sort(key=lambda i: (i.severity != ERROR, i.source))
    return issues


# ---------------------------------------------------------------- 2. 术语表违背

def load_glossary_index() -> tuple[dict[str, dict], dict[str, dict]]:
    """建立两张索引：英文(小写)->术语行；中文->术语行。"""
    rows = R.load_glossary()
    by_en: dict[str, dict] = {}
    by_zh: dict[str, dict] = {}
    for r in rows:
        en = (r.get("英文") or "").strip()
        zh = (r.get("简中") or "").strip()
        if en:
            by_en.setdefault(en.lower(), r)
        if zh:
            by_zh.setdefault(zh, r)
    return by_en, by_zh


# 术语表条目里带 ⚠ 的，表示「已知冲突/待定」，不作为违背依据
def check_glossary(games: list[str]) -> list[Issue]:
    by_en, _ = load_glossary_index()
    en2zh = collect_en2zh(games)
    issues: list[Issue] = []
    seen: set[str] = set()
    for src, entries in en2zh.items():
        row = by_en.get(src.strip().lower())
        if not row:
            continue
        auth = (row.get("简中") or "").strip()
        if not auth or "⚠" in (row.get("备注") or ""):
            continue
        variants = {e.text.strip() for e in entries if R.has_cjk(e.text)}
        # 只在「译文与权威译法不同、且不是权威译法的子串」时报
        if auth in variants:
            continue
        if len(variants) == 1:
            only = next(iter(variants))
            # 权威译法内嵌（如同音不同写法）不报
            if auth and (auth in only or only in auth):
                continue
        sig = (src.strip().lower(), tuple(sorted(variants)))
        if sig in seen:
            continue
        seen.add(sig)
        first = entries[0]
        issues.append(Issue(
            WARN, "glossary_violation",
            f"术语表要求「{auth}」（类型 {row.get('类型','')}），"
            f"实际 {' / '.join(sorted(variants)[:4])}",
            key=first.key, source=src,
            text=" | ".join(sorted(variants)[:4]),
            file=first.file, line=first.line, game=first.game,  # type: ignore[attr-defined]
        ))
    return issues


# ---------------------------------------------------------------- 3. 跨代漂移

def check_cross_gen(games: list[str]) -> list[Issue]:
    """SR3 与 SR4 共有 key 译法不一致（同世界观，理应一致）。"""
    if len(games) < 2:
        return []
    per_game: dict[str, dict[str, str]] = {}
    for g in games:
        m: dict[str, str] = {}
        for e in R.load_entries(g, "CHS", "dict"):
            m.setdefault(e.key.strip(), e.text.strip())
        per_game[g] = m
    g0, g1 = games[0], games[1]
    common = set(per_game[g0]) & set(per_game[g1])
    issues: list[Issue] = []
    for k in common:
        a, b = per_game[g0][k], per_game[g1][k]
        if a == b:
            continue
        if not R.has_cjk(a) or not R.has_cjk(b):
            continue
        # 编号归一后相同则跳过
        if _norm_for_group(a) == _norm_for_group(b):
            continue
        kind, why = classify_diff(k, [a, b])
        if kind in ("numbered", "reasonable"):
            continue
        issues.append(Issue(
            WARN, "cross_gen_drift", f"{g0}「{a}」/ {g1}「{b}」",
            key=k, source=k, text=f"{a} | {b}",
        ))
    issues.sort(key=lambda i: i.key)
    return issues


# ---------------------------------------------------------------- 主流程

def run(games: list[str] | None = None, kinds: set[str] | None = None) -> list[Issue]:
    games = games or ["SR3", "SR4"]
    kinds = kinds or {"conflict", "glossary", "crossgen"}
    issues: list[Issue] = []
    if "conflict" in kinds:
        issues += check_same_source_conflicts(games)
    if "glossary" in kinds:
        issues += check_glossary(games)
    if "crossgen" in kinds:
        issues += check_cross_gen(games)
    issues.sort(key=lambda i: (i.severity != ERROR, i.code, i.source))
    return issues


def report(issues: list[Issue], severity: str = "ALL", limit: int = 0) -> None:
    order = {ERROR: 0, WARN: 1, INFO: 2}
    sel = [i for i in issues if severity == "ALL" or i.severity == severity]
    sel.sort(key=lambda i: (order.get(i.severity, 9), i.code, i.source))
    by = Counter((i.severity, i.code) for i in sel)
    print("=" * 72)
    print("L2 一致性冲突报告")
    print("=" * 72)
    for (sev, code), n in sorted(by.items(),
                                 key=lambda kv: (order.get(kv[0][0], 9), -kv[1])):
        print(f"  {sev:5s} {code:24s} {n:6d}")
    print("-" * 72)
    print(f"  合计 {len(sel)} 条\n")
    for i in (sel if limit <= 0 else sel[:limit]):
        print(str(i))
    if 0 < limit < len(sel):
        print(f"\n... 另有 {len(sel) - limit} 条")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L2 一致性检查（只读）")
    ap.add_argument("--kind", action="append",
                    choices=["conflict", "glossary", "crossgen"],
                    help="只跑指定检查（可重复）")
    ap.add_argument("--severity", default="ALL",
                    choices=["ALL", ERROR, WARN, INFO])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)

    issues = run(None, set(a.kind) if a.kind else None)
    report(issues, a.severity, a.limit)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump([i.as_dict() for i in issues], f,
                      ensure_ascii=False, indent=1)
        print(f"\nJSON 已写入 {a.json}")
    return 1 if (a.strict and any(i.severity == ERROR for i in issues)) else 0


if __name__ == "__main__":
    sys.exit(main())
