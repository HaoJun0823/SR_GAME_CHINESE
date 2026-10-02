"""unify_terms.py —— 术语统一裁决引擎（只读，产出修复建议）。

为什么需要它
------------
L2 报出234 条「专名/术语级不一致」，但**不一致 ≠ 都错**。绝大多数是
「两种译法都对，只是没统一」（如 ``WHEELS`` → 车轮/轮胎、``ZOMBIE`` → 丧尸/僵尸）。
若一律"重新翻译"，等于把200+ 条**已经正确**的译文改掉 —— 那是净损失，不是修复。

裁决依据（客观、可复现，不用直觉）
--------------------------------------
用**全库词频**当裁判：
  1. 同一英文的所有候选译法，统计各自在全库 95,060 条中的出现次数；
  2. 词频最高者为**主导译法**（majority vote）；
  3. 词频显著落后者标为**罕见译法**（疑似错译）；
  4. 术语表 ``glossary.csv`` 若有权威译法，**优先于词频**；
  5. 词频接近（差距 < 2倍）且都合理 → 标``需人工定夺``，不自动改。

**这保证只改「有客观证据支持错」的那少数，其余保留。**

用法
----
    python unify_terms.py --list# 列出全部裁决建议
    python unify_terms.py --json out.json
    python unify_terms.py --apply-plan plan.jsonl   # 产出可apply 的修复单
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
import manual_decisions as M  # noqa: E402

# 判为「需人工定夺」的词频差距阈值。
#
# ⚠ 2.0 太宽松：实测 2:1 的多数是「SR3 与 SR4 各译一次」的跨代差异，
#   两边**都不错**，只是没统一（如 ``THE SLASHER`` → 屠夫×2 / 砍人魔×1）。
#   按2:1 自动改等于凭 1 条的频次差去否定另一种同样合理的译法。
#   提到 3.0：必须有明显多数才自动统一。
TIE_RATIO = 3.0
# 权威译法词频下限：低于此值且不是唯一候选 →视为罕见解法
RARE_MAX = 3
# 自动统一时，目标译法的**绝对频次下限**。
# 频次只有 1~2 时说明样本太少，裁决不可靠，一律交人工。
AUTO_MIN_ABS = 3

# ★ 危险译法：词频裁决的**越权拦截**。
#
# 词频是统计，不是语义。实测踩到的坑（词频高≠正确）：
#   ASIAN   亚洲×26  > 亚裔×1   —— 但这里指「亚裔人种」，「亚洲」是误译
#   NO      序号×N   > 否×1      —— 「序号」是 NUMBER 的串行，纯粹误译
#   STREAKING 天体×N > 裸奔×1     —— streak 是「裸奔」，「天体」是星体，误译
#   WHITE   白色×17  > 白人×1     —— 此处是「白色人种」选项，需人工确认
#   LOW     低腰×N   > 高/低位    —— 发型维度，词频会串味
# 命中此表的英文**一律不自动改**，交人工。
DANGEROUS: dict[str, str] = {
    "ASIAN": "指人种，应为「亚裔」；「亚洲」是地域义",
    "WHITE": "可能指「白色人种」选项，需确认",
    "BLACK": "可能指「黑色人种」选项，需确认",
    "CAUCASIAN": "指人种，需确认译法",
    "HISPANIC": "指人种，需确认译法",
    "NO": "「序号」疑为 NUMBER 串行误译",
    "STREAKING": "streak = 裸奔，非天体",
    "LOW": "发型维度（低腰/低位），词频易串味",
    "HIGH": "发型维度（高位/高），词频易串味",
    "NORMAL": "多个维度共用意译，需确认",
    "LEVEL": "关卡/等级两义，需确认",
    "GOLD": "金色/黄金/金三义，需确认",
    "SILVER": "银/白银/银色三义，需确认",
    "STEEL": "钢/钢色两义，需确认",
    "THE A BUTTON": "跨平台按键（A 键/右摇杆/左摇杆），不是译法冲突",
    "ZINYAK": "术语表为「齐尼亚克」，「津亚克」是错字",
    "MAERO": "「马罗」疑为误译，需查证",
    "BACK": "必须按 key 前缀分派（UI=返回 / 纹身=背部 / 习语=背后）",
    "BUILD": "必须按 key 前缀分派（体型维度=体型）",
    "FIGHT CLUB": "三者都可接受，需定一个",
    "FLAMETHROWER": "语料实测：装备名用「火焰喷射器」(×4)、口语泛指用「喷火器」(×2)，"
                "属合理语境差异，不应统一",
    # ↓ 以下为**已确认的错译**，需指定正确译法（与「需人工」不同：这些要改）
    "ZINYAK": "术语表为「齐尼亚克」；「津亚克」是错字，须改",
    "POWDER": "Powder 是「粉帮」（Powder Gang 帮派成员），非「火药」",
    "MAERO": "「马罗」疑为「梅罗」的错字",
}

# ★ 指定修正：已确认的错译，直接给正确译法（绕开词频裁决）。
# 依据必须是**语料实证或官方译名**，不能凭感觉。
FORCE_FIX: dict[str, str] = {
    "ZINYAK": "齐尼亚克",     # 术语表权威译法；语料中「齐尼亚克」为正确
    "POWDER": "粉帮",         # Powder Gang = 粉帮
}

# ★ 强制修正的**语义模式**：
#   full= 目标就是整条译文（``POWDER`` → ``粉帮``）
#   sub = 目标只是待替换的**片段**，需在原译文中做子串替换
#     （``ZINYAK STATUES DESTROYED`` → 把「津亚克」换成「齐尼亚克」，
#       整条译文是「齐尼亚克雕像已摧毁」，不能整条覆盖成「齐尼亚克」）
FORCE_MODE: dict[str, str] = {
    "ZINYAK": "sub",
    "POWDER": "full",
}


def force_fix_for(src: str) -> tuple[str, str, str] | None:
    """按**子串**匹配强制修正表。返回 (正确译法/片段, 模式, 说明)。

    实测：``ZINYAK STATUES DESTROYED`` 整串不等于 ``ZINYAK``，
    纯等值匹配会漏掉（v1 实测踩过）。故改为「英文含该词即命中」。
    """
    s = (src or "").strip().upper()
    for token, correct in FORCE_FIX.items():
        if re.search(rf"\b{re.escape(token)}\b", s):
            return correct, FORCE_MODE.get(token, "full"), token
    return None

ACTION_UNIFY = "unify"        # 统一到主导译法
ACTION_MANUAL = "manual"      # 需人工定夺，不自动改
ACTION_KEEP = "keep"          # 无需改动
ACTION_GLOSSARY = "glossary"  # 按术语表强制统一
ACTION_FORCE_SUB = "force_sub"  # 片段替换（如错字「津亚克」→「齐尼亚克」）


def build_freq() -> Counter:
    """统计所有短译文在全库的出现频次（作裁决依据）。"""
    freq: Counter = Counter()
    for g in R.GAMES:
        for c in R.CARRIERS:
            for e in R.load_entries(g, "CHS", c):
                t = e.text.strip()
                if t and len(t) <= 12:
                    freq[t] += 1
    return freq


def load_glossary_map() -> dict[str, str]:
    """英文小写 -> 权威简中（只取无 ⚠ 的干净条目）。"""
    out: dict[str, str] = {}
    for r in R.load_glossary():
        en = (r.get("英文") or "").strip()
        zh = (r.get("简中") or "").strip()
        note = r.get("备注") or ""
        if en and zh and "⚠" not in note:
            out[en.lower()] = zh
    return out


PROPER_RE = [
    re.compile(r"《[^》]+》"),
    re.compile(r"[A-Z0-9][A-Z0-9 \.\-&']{1,30}$"),
    re.compile(r"\b(MR|MS|DR|ST|MRS)\b\."),
]


def is_proper_noun(src: str) -> bool:
    s = (src or "").strip()
    return any(rx.search(s) for rx in PROPER_RE)


def collect_en2zh(games: list[str] | None = None) -> dict[str, set[str]]:
    """英文原文 -> 该英文所有中文译法集合。"""
    games = games or list(R.GAMES)
    out: dict[str, set[str]] = defaultdict(set)
    for g in games:
        for c, e in R.load_aligned(g, "le_string"):
            if e is None:
                continue
            src = e.text.strip()
            if src and re.search(r"[A-Za-z]", src):
                out[src].add(c.text.strip())
        for e in R.load_entries(g, "CHS", "dict"):
            src = e.key.strip()
            if src and re.search(r"[A-Za-z]", src):
                out[src].add(e.text.strip())
    return out


def decide(src: str, variants: set[str], freq: Counter,
           gloss: dict[str, str]) -> dict:
    """对一条英文给出裁决。"""
    # 只保留含中文的候选（未译的英文原文不算译法）
    cands = {v.strip() for v in variants if R.has_cjk(v.strip())}
    if len(cands) < 2:
        return {"action": ACTION_KEEP, "reason": "无需统一", "candidates": sorted(cands)}

    # 剥离编号后缀后若归一，说明只是编号差异
    if len({R.strip_index_suffix(c) for c in cands}) == 1:
        return {"action": ACTION_KEEP, "reason": "仅编号后缀差异",
                "candidates": sorted(cands)}

    key = src.strip().upper()

    # ★ 人工裁决（2026-10-02）：通用义优先，key 前缀可证明语境时按前缀走。
    # 放在 DANGEROUS 拦截**之前** —— 裁决就是用来接管这些危险词的。
    forced_whole = M.FORCE_WHOLE.get(src.strip().lower())
    if forced_whole:
        cands.discard(forced_whole)
        if len(cands) == 0:
            return {"action": ACTION_KEEP, "reason": "已符合人工裁决",
                    "authoritative": forced_whole, "candidates": [forced_whole]}
        return {"action": ACTION_UNIFY, "authoritative": forced_whole,
                "reason": "人工裁决（通用义优先原则）",
                "candidates": sorted(cands | {forced_whole}), "forced": True}

    # ★ 强制修正：已确认的错译（按子串匹配，覆盖整串变体）
    fx = force_fix_for(src)
    if fx:
        correct, mode, token = fx
        if mode == "sub":
            # 片段替换：候选里应含「错误片段+ 其余」的整条译文
            return {"action": ACTION_FORCE_SUB, "fragment": correct,
                    "reason": f"「{token}」为错字，片段替换为「{correct}」",
                    "candidates": sorted(cands), "forced": True}
        cands.discard(correct)
        return {"action": ACTION_UNIFY, "authoritative": correct,
                "reason": f"已确认错译（{token}），整条修正为「{correct}」",
                "candidates": sorted(cands | {correct}), "forced": True}

    # ★ 危险译法拦截：词频无权裁决的，一律交人工
    if key in DANGEROUS:
        return {"action": ACTION_MANUAL,
                "reason": f"⚠ 需人工判定（{DANGEROUS[key]}）",
                "candidates": sorted(cands)}

    scored = sorted(((c, freq.get(c, 0)) for c in cands),
                    key=lambda kv: -kv[1])
    top, top_n = scored[0]
    auth = gloss.get(src.strip().lower())

    # 术语表优先
    if auth and auth in cands:
        if len(cands) == 1 or auth == top:
            return {"action": ACTION_KEEP, "reason": "已符合术语表",
                    "authoritative": auth, "candidates": sorted(cands),
                    "freq": dict(scored)}
        return {"action": ACTION_GLOSSARY, "authoritative": auth,
                "reason": f"术语表规定为「{auth}」（词频 {freq.get(auth,0)}）",
                "candidates": sorted(cands), "freq": dict(scored)}

    # 词频裁决
    if top_n == 0:
        return {"action": ACTION_MANUAL, "reason": "所有候选词频均为 0，无法裁决",
                "candidates": sorted(cands), "freq": dict(scored)}
    second_n = scored[1][1] if len(scored) > 1 else 0
    if second_n == 0 or top_n >= second_n * TIE_RATIO:
        # 主导译法明显领先，但样本量也要够
        if top_n < AUTO_MIN_ABS:
            return {"action": ACTION_MANUAL,
                    "reason": f"样本不足（主导译法仅出现 {top_n} 次），需人工定夺",
                    "candidates": sorted(cands), "freq": dict(scored)}
        losers = [c for c, n in scored[1:] if n <= RARE_MAX]
        if not losers:
            return {"action": ACTION_KEEP,
                    "reason": f"已统一为主导译法（{top} ×{top_n}）",
                    "authoritative": top, "candidates": sorted(cands),
                    "freq": dict(scored)}
        return {"action": ACTION_UNIFY, "authoritative": top,
                "reason": f"统一到主导译法「{top}」（×{top_n}），"
                          f"罕见译法 {losers}",
                "candidates": sorted(cands), "freq": dict(scored)}

    return {"action": ACTION_MANUAL,
            "reason": f"词频接近（{top}×{top_n} vs {scored[1][0]}×{second_n}），需人工定夺",
            "candidates": sorted(cands), "freq": dict(scored)}


def run(games: list[str] | None = None,
        proper_only: bool = True) -> list[dict]:
    freq = build_freq()
    gloss = load_glossary_map()
    en2zh = collect_en2zh(games)
    out: list[dict] = []
    for src, variants in en2zh.items():
        if proper_only and not is_proper_noun(src):
            continue
        d = decide(src, variants, freq, gloss)
        if d["action"] == ACTION_KEEP:
            continue
        d["source"] = src
        out.append(d)
    out.sort(key=lambda d: (d["action"], d["source"].lower()))
    return out


def report(items: list[dict], limit: int = 0) -> None:
    c = Counter(d["action"] for d in items)
    print("=" * 72)
    print("术语统一裁决（词频客观裁决，不用直觉）")
    print("=" * 72)
    label = {ACTION_UNIFY: "统一到主导译法", ACTION_GLOSSARY: "按术语表统一",
             ACTION_FORCE_SUB: "错字片段替换",
             ACTION_MANUAL: "需人工定夺"}
    for a in (ACTION_GLOSSARY, ACTION_FORCE_SUB, ACTION_UNIFY, ACTION_MANUAL):
        if c.get(a):
            print(f"  {label[a]:16s} {c[a]:6d}")
    print("-" * 72)
    print(f"  合计 {len(items)} 条待处理\n")
    for d in (items if limit <= 0 else items[:limit]):
        print(f"  [{label[d['action']]}] {d['source'][:44]}")
        print(f"      {d['reason']}")
        print(f"      候选: {d['candidates']}")
        print()


def build_plan(items: list[dict], games: list[str] | None = None) -> list[dict]:
    """把「可自动执行」的裁决展开成逐条修复单。

    两种模式：
    - unify/glossary：整条译文替换为目标译法；
    - force_sub：把译文里的**错误片段**替换为正确片段（其余不动）。
    """
    games = games or list(R.GAMES)
    targets: dict[str, str] = {}
    subs: list[tuple[str, str]] = []   # (错误片段, 正确片段)
    for d in items:
        if d["action"] in (ACTION_UNIFY, ACTION_GLOSSARY) and d.get("authoritative"):
            targets[d["source"].strip()] = d["authoritative"]
        elif d["action"] == ACTION_FORCE_SUB and d.get("fragment"):
            subs.append(("<片段>", d["fragment"]))

    plan: list[dict] = []

    def resolve(src: str, old: str, filename: str, key: str = "") -> str | None:
        """按优先级算出目标译文；无需改动返回 None。"""
        # ⓪ key 前缀分派（R2 铁证：CUST_RACE_WHITE=白人 / CUST_COLOR_WHITE=白色）
        t = key_scoped_target(key, src) if key else None
        if t is not None:
            return None if t == old.strip() else t
        # ① 文件分派（LEVEL 1/2/3 这类同英文两义的）
        t = file_scoped_target(src, filename)
        if t is not None:
            return None if t == old else t
        # ② 词频/术语表/人工裁决出的整条目标译法
        if src in targets:
            t = targets[src]
            return None if t == old.strip() else t
        # ③ 片段替换（错字 / 术语族）
        for wrong, right in FORCE_SUB_PAIRS:
            if wrong not in old:
                continue
            # 纯英文片段需在源文里出现；带富文本标记的直接按字面替换
            if "[" in wrong or re.search(rf"\b{re.escape(wrong)}\b", src, re.I):
                return old.replace(wrong, right)
        return None

    for g in games:
        # le_string：按 key 逐个定位
        for c, e in R.load_aligned(g, "le_string"):
            if e is None:
                continue
            src = e.text.strip()
            new = resolve(src, c.text, c.file, c.key)
            if new is None:
                continue
            plan.append({
                "file": c.file, "line": c.line, "key": c.key,
                "old": c.text, "new": new,
                "source": f"unify_terms:{src[:30]}",
                "note": f"术语统一：{src[:30]} → {new[:30]}",
            })
        # dict：key 即英文
        for e in R.load_entries(g, "CHS", "dict"):
            src = e.key.strip()
            new = resolve(src, e.text, e.file, e.key)
            if new is None:
                continue
            plan.append({
                "file": e.file, "line": e.line, "key": e.key,
                "old": e.text, "new": new,
                "source": f"unify_terms:{src[:30]}",
                "note": f"术语统一：{src[:30]} → {new[:30]}",
            })
    return plan


# (错误片段, 正确片段) —— 用于整条译文内部的错字替换
FORCE_SUB_PAIRS: list[tuple[str, str]] = [
    ("津亚克", "齐尼亚克"),
]

# ★ 术语名的**全文替换表**（中文 → 权威译法）。
#
# 为什么要这一层：词频裁决只认「英文原文完全相同」的条目，
# 但语料里大量错译是**嵌在长句/装备名中**的，key 各不相同，检测不到。
#   实测：``DECKERS`` 这个 key 本身已统一为「赛博帮」，
#   但「迪克帮」仍散落**30 处**，全嵌在
#   ``DECKER KING PANTS``/``cm_dkr01_hdphones``/``A [format]DECKER[/format] IS…``
#   这类长条目里 —— 只按 key 查会全部漏掉。
# 机制：对 CHS 全文做**中文子串**替换，天然覆盖任何上下文。
GLOBAL_SUBST: list[tuple[str, str, str]] = [
    # (错误译名, 权威译名, 依据)
    ("迪克帮", "赛博帮", "SR 官方中文译名；Decker =赛博帮"),
    ("津亚克", "齐尼亚克", "术语表权威译名，「津」为错字"),
    ("根子", "源木", "术语表：Prof. Genki = 源木；语料 81:18 占绝对多数"),
    ("骇客任务", "赛博帮任务", "START DECKERS MISSION，DECKER=赛博帮，「骇客」是错译"),
    # ---- 以下为**术语族统一**（同源术语在长句里被译成两个词，需全文统一）----
    ("[color:green]女孩[/format]", "[color:green]风尘女[/format]",
     "HOS 在 SR 官方中文版为「风尘女」；「女孩」是弱化误译"),
    ("[color:teal]联系人[/format]", "[color:teal]联络人[/format]",
     "CONTACTS 官方译「联络人」（全库 80:4）；「联系人」是误译"),
    # ★ 人工裁决（2026-10-02，通用义优先原则）
    *M.FORCE_SUBST,
]


def build_subst_plan(games: list[str] | None = None) -> list[dict]:
    """按中文译名做全文替换，产出修复单（覆盖嵌入式用法）。"""
    games = games or list(R.GAMES)
    plan: list[dict] = []
    for g in games:
        for c, e in R.load_aligned(g, "le_string"):
            _scan_entry(plan, c, e.text if e else "")
        for e in R.load_entries(g, "CHS", "dict"):
            _scan_entry(plan, e, e.key)
    return plan


def _scan_entry(plan: list[dict], entry, src: str) -> None:
    old = entry.text
    if not old:
        return
    new = old
    hit = None
    for wrong, right, why in GLOBAL_SUBST:
        if wrong in new:
            new = new.replace(wrong, right)
            hit = (wrong, right, why)
            break
    if hit is None or new == old:
        return
    plan.append({
        "file": entry.file, "line": entry.line, "key": entry.key,
        "old": old, "new": new,
        "source": f"global_subst:{hit[0]}",
        "note": f"术语全文替换：{hit[0]} → {hit[1]}（{hit[2]}）",
    })


# ★ 按文件分派的术语：同一英文在不同文件里语义不同，不能一刀切。
#
# 实测证据（``LEVEL_1/2/3``）：
#   Resource/SR3/CHS/le_string/customize_us.txt:2887
#     上下文是 ``CUST_ITEM_FEMALE_LL_EARRINGS_1`` / ``CUST_ITEM_2015_SHADES``，
#     即**外观改装档次** → 应译「等级 1」
#   Resource/SR4/CHS/dict/le_data_supplement.txt:2754
#     是任务进度 → 应译「第 1 关」
# 词频裁决会把前者改成「第 1 关」，那是**在 customize 界面显示关卡**的明显退步。
FILE_SCOPED: list[dict] = [
    {
        "match": re.compile(r"^LEVEL\s*([123])$", re.I),
        "files": ("customize_us",),
        "authoritative": "等级 {n}",
    },
    {
        "match": re.compile(r"^LEVEL\s*([123])$", re.I),
        "files": ("le_data_supplement",),
        "authoritative": "第 {n} 关",
    },
]


def file_scoped_target(src: str, filename: str) -> str | None:
    """按文件分派术语；不适用返回 None。"""
    base = (filename or "").replace("\\", "/").split("/")[-1]
    for rule in FILE_SCOPED:
        m = rule["match"].match((src or "").strip())
        if not m:
            continue
        if any(base.startswith(f) or f in base for f in rule["files"]):
            return rule["authoritative"].format(n=m.group(1))
    return None


def key_scoped_target(key: str, src: str) -> str | None:
    """按 key 前缀分派（同英文不同语义，R2 铁证）。"""
    got = M.decide_by_key(key, src)
    return got[0] if got else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="术语统一裁决引擎（只读）")
    ap.add_argument("--list", action="store_true", help="打印裁决建议")
    ap.add_argument("--all", action="store_true", help="含非专名（短语/对白）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json")
    ap.add_argument("--apply-plan", help="产出可 apply 的修复单 JSONL")
    a = ap.parse_args(argv)

    items = run(proper_only=not a.all)
    if a.list or (not a.json and not a.apply_plan):
        report(items, a.limit)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1, default=str)
        print(f"JSON 已写入 {a.json}")
    if a.apply_plan:
        plan = build_plan(items)
        # 合并「中文译名全文替换」计划（覆盖嵌在长句里的错译，key 检测不到）
        sub = build_subst_plan()
        seen = {(p["file"], p["line"]) for p in plan}
        for p in sub:
            if (p["file"], p["line"]) not in seen:
                plan.append(p)
        with open(a.apply_plan, "w", encoding="utf-8") as f:
            for d in plan:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        print(f"修复单已产出：{len(plan)} 条"
              f"（词频裁决 {len(plan) - len([1 for p in sub if (p['file'],p['line']) not in seen])}"
              f" + 全文替换 {len([1 for p in sub if (p['file'],p['line']) not in seen])}）"
              f" -> {a.apply_plan}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
