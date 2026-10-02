"""lint_mechanical.py —— L1 机械质检（只读，零误报优先）。

原则
----
1. **只读**：本脚本绝不修改任何 Resource 文件。
2. **幂等**：同输入同输出，可反复跑。
3. **可定位**：每条问题附 ``file:line`` + 源文 + 译文，逐条可复核。
4. **零误报优先**：宁可少报也不误报。存疑的一律降级为 WARN 或不报。

检查项
------
ERROR 级
  untranslated  漏译：译文与源文完全相同且源文含字母
  conv_mismatch  printf 转换符（%d/%s/%%/%1$s）多重集与源文不一致
  tag_mismatch   富文本标签（[format] [color:x] [/format]）多重集不一致
  empty_translation 空译文（源文非空但译文为空串）
  key_missing_eng  CHS 有该 key 但 ENG 侧无（无法校对，且可能引擎回退显示 key）

WARN 级
  newline_mismatch换行数：源文含 \\n 而译文无（或反之）
  residual_marker 译文残留 TODO / FIXME / 待翻译 / ???
  traditional     简中目录检出繁体字
  crlf            文件含 CRLF
  dup_key         同key 在同一文件内重复出现

用法
----
    python lint_mechanical.py                      # 全量，文本报告
    python lint_mechanical.py --game SR4
    python lint_mechanical.py --severity ERROR
    python lint_mechanical.py --json out.json
    python lint_mechanical.py --strict             # 有 ERROR 就退出码 1
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

# ---------------------------------------------------------------- 模式

# printf 风格转换符。%% 是转义后的百分号，也纳入比对（必须数量一致）。
#
# ⚠ **必须排除「% + 空格 + 字母」**（``% OFF``/``% COMPLETE``/``% DISCOUNT``）。
#   源文里的英文百分数表达（``25% OFF``、``{0}%%% BONUS``）会被误判成转换符，
#   18 条误报全部由此而来（实测）。故要求 % 与字母间**无空格**。
CONV = re.compile(r"%(?:\d+\$)?[-+#0]*[\d*]*(?:\.[\d*]+)?[hlL]?[a-zA-Z%]")

# 英文百分数表达：``25%``、``{0}%%%``、``100%% sure``。
# 这类``%`` 是**字面百分号**（游戏文本里常用 ``%%%`` 表示「%」字面），
# 不是 printf 转换符，比对前须先剥掉，否则 18 条误报（实测）。
_LITERAL_PCT = re.compile(r"%{1,3}(?![\d\$a-zA-Z%])")


def _tags(text: str) -> Counter:
    return Counter(TAG.findall(text or ""))


def _convs(text: str) -> Counter:
    """提取转换符集合，先剥离字面百分数。"""
    return Counter(CONV.findall(_LITERAL_PCT.sub("", text or "")))

# 富文本标签。SR 的标签形如 [format] [color:green] [/format] [icon:x] [image:y]
TAG = re.compile(r"\[/?(?:format|color|img|image|icon|line|break|font|size)[^\]]*\]", re.I)

RESIDUAL = re.compile(r"(TODO|FIXME|XXX|待翻译|未翻译|待定|\?\?\?|【待】|<<<|>>>)")

# 同文件内允许跨文件复用的 key 段（DLC 内容大量重叠，属设计）
_ALLOW_CROSS_FILE = True

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"


class Issue:
    __slots__ = ("severity", "code", "game", "lang", "carrier", "file", "line",
                 "key", "source", "text", "detail")

    def __init__(self, severity, code, entry: R.Entry | None, detail: str = "",
                 source: str | None = None, key: str | None = None):
        self.severity = severity
        self.code = code
        self.game = entry.game if entry else ""
        self.lang = entry.lang if entry else ""
        self.carrier = entry.carrier if entry else ""
        self.file = entry.file if entry else ""
        self.line = entry.line if entry else 0
        self.key = key if key is not None else (entry.key if entry else "")
        self.source = source if source is not None else (
            entry.source_text if entry else "")
        self.text = entry.text if entry else ""
        self.detail = detail

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "game": self.game,
            "lang": self.lang,
            "carrier": self.carrier,
            "file": self.file,
            "line": self.line,
            "key": self.key,
            "source": self.source,
            "text": self.text,
            "detail": self.detail,
        }

    def __str__(self) -> str:
        loc = f"{self.file}:{self.line}"
        src = f"  源: {self.source[:70]!r}" if self.source else ""
        txt = f"  译: {self.text[:70]!r}"
        d = f"  [{self.detail}]" if self.detail else ""
        return f"{self.severity:5s} {self.code:22s} {loc}\n      key: {self.key[:60]}{src}{txt}{d}"


# 给 Entry 补上 carrier / game 标签（load_entries 不填，方便复用）
def _tag(e: R.Entry, game: str, carrier: str) -> R.Entry:
    e.game = game  # type: ignore[attr-defined]
    e.carrier = carrier  # type: ignore[attr-defined]
    e.lang = "CHS"  # type: ignore[attr-defined]
    return e


# ---------------------------------------------------------------- 各项检查

def check_untranslated(e: R.Entry) -> Issue | None:
    """漏译：译文 == 源文，且源文含字母。

    先过豁免规则（见 lint_exempt.py）：轮毂型号、电台台标、手柄按键等
    「故意保留英文」不算漏译。实测未豁免时会有 5000+ 条噪音。
    """
    src = e.source_text
    if src is None:
        return None
    if not re.search(r"[A-Za-z]", src):
        return None
    if e.text.strip() != src.strip():
        return None
    skip, why = X.should_skip_untranslated(e.key, e.text)
    if skip:
        return Issue(INFO, "kept_english", e, f"故意保留英文：{why}")

    # 文件级降级：exe_hardcoded.txt 等文件多为玩家不可见的引擎符号，
    # 降为 INFO；但命中玩家可见白名单（EXIT/BACK/SAVE…）仍报 ERROR。
    downgraded, dwhy = X.file_downgraded(e.file)
    if downgraded:
        vis, hit = X.in_visible_whitelist(e.text)
        if not vis:
            return Issue(INFO, "untranslated_lowvalue", e,
                         f"降级文件：{dwhy}")
        return Issue(ERROR, "untranslated", e,
                     f"降级文件但属玩家可见文案（{hit}）")

    return Issue(ERROR, "untranslated", e, "译文与源文完全相同")


def check_conv(e: R.Entry) -> Issue | None:
    """printf 转换符集合不一致。"""
    src = e.source_text
    if src is None:
        return None
    a = _convs(src)
    b = _convs(e.text)
    if a == b:
        return None
    return Issue(
        ERROR, "conv_mismatch", e,
        f"源{sorted(a.elements())} vs 译{sorted(b.elements())}",
    )


def check_tag(e: R.Entry) -> Issue | None:
    """富文本标签集合不一致。

    ⚠ **只在「译文缺标签」时报错**，不报「译文比源文更平衡」的情况。
    实测反例（``SR4/activity_us.txt:HASH_A2BF76D9``）：
      源 ``TAKE [format][color:green]LIN[/format] TO A [color:teal]STORE[/format]``
        —— 1 个 ``[format]`` 对 2 个 ``[/format]``，**源文自身就不平衡**（游戏原样）
      译 ``带[format][color:green]琳[/format]去[format][color:teal]商店[/format]``
        —— 2 对 2，**译文把源文的缺陷修好了**
      此时报「译文不符」是误报，强行「修」反而会把正确的译文改坏。
    """
    src = e.source_text
    if src is None:
        return None
    a, b = _tags(src), _tags(e.text)
    if a == b:
        return None
    # 译文是源文的真子集 → 译文缺标签，这才要报
    if not (b < a):
        return None
    return Issue(
        ERROR, "tag_mismatch", e,
        f"译文缺少标签：源{sorted(a.elements())} vs 译{sorted(b.elements())}",
    )


def check_newline(e: R.Entry) -> Issue | None:
    src = e.source_text
    if src is None:
        return None
    a, b = src.count("\n"), e.text.count("\n")
    if a == b:
        return None
    return Issue(WARN, "newline_mismatch", e, f"源{a}个\\n vs 译{b}个")


def check_empty(e: R.Entry) -> Issue | None:
    src = e.source_text
    if src is None:
        return None
    if src.strip() and not e.text.strip():
        return Issue(ERROR, "empty_translation", e, "译文为空")


def check_placeholder(e: R.Entry) -> Issue | None:
    """占位符被翻译文字污染。

    实测事故：曾有脚本对``BDSM -> 私享`` 做过**无边界全文替换**，
    把占位符 ``{RECRUIT_DISMISS_IMG}`` 一并打成了 ``{RECRUIT_DI私享ISS_IMG}``，
    引擎取不到该变量 → 游戏内显示乱码/空白。
    判据：``{...}`` 内出现CJK 字符即异常（占位符名恒为 ASCII）。
    """
    hits = [m.group(0) for m in re.finditer(r"\{[^}]{0,80}\}", e.text)
            if re.search(r"[\u4e00-\u9fff]", m.group(0))]
    if not hits:
        return None
    return Issue(ERROR, "placeholder_corrupted", e,
                 f"占位符含中文（疑被无边界替换误伤）：{hits[:3]}")


def check_residual(e: R.Entry) -> Issue | None:
    m = RESIDUAL.search(e.text)
    if not m:
        return None
    return Issue(WARN, "residual_marker", e, f"残留标记 {m.group(0)!r}")


def check_traditional(e: R.Entry) -> Issue | None:
    hit = sorted({c for c in e.text if c in R.TRAD_CHARS})
    if not hit:
        return None
    return Issue(WARN, "traditional", e, f"疑似繁体 {''.join(hit)}")


def lint_le_string(game: str, resource_dir: str | None = None) -> list[Issue]:
    """le_string：有ENG 平行目录，可做对照类检查。"""
    issues: list[Issue] = []
    pairs = R.load_aligned(game, "le_string", resource_dir)
    for c, e in pairs:
        _tag(c, game, "le_string")
        if e is None:
            issues.append(Issue(
                ERROR, "key_missing_eng", c,
                "CHS 有此 key 但 ENG 侧缺失，无法校对",
            ))
            continue
        for fn in (check_untranslated, check_conv, check_tag,
                   check_newline, check_empty, check_placeholder,
                   check_residual, check_traditional):
            got = fn(c)
            if got:
                issues.append(got)
    return issues


def lint_dict(game: str, resource_dir: str | None = None) -> list[Issue]:
    """dict：key 即英文原文，单目录即可校对。"""
    issues: list[Issue] = []
    for e in R.load_entries(game, "CHS", "dict", resource_dir):
        _tag(e, game, "dict")
        e.source_text = e.key  # 英文原文就是 key
        if re.search(r"[A-Za-z]", e.key):
            got = check_untranslated(e)
            if got:
                issues.append(got)
        if e.key.strip() and not e.text.strip():
            issues.append(Issue(ERROR, "empty_translation", e, "译文为空"))
        for fn in (check_placeholder, check_residual, check_traditional):
            got = fn(e)
            if got:
                issues.append(got)
    return issues


def check_file_level(resource_dir: str | None = None) -> list[Issue]:
    """文件级：CRLF、同文件内重复 key。"""
    issues: list[Issue] = []
    for game in R.GAMES:
        for carrier in R.CARRIERS:
            for meta in R.probe_meta(game, "CHS", carrier, resource_dir):
                if meta.crlf:
                    issues.append(Issue(
                        WARN, "crlf", None, "文件含 CRLF",
                        key=meta.path,
                    ))
    # 同文件内重复 key
    for game in R.GAMES:
        for carrier in R.CARRIERS:
            for p in R.list_carrier_files(game, "CHS", carrier, resource_dir):
                cnt: dict[str, int] = defaultdict(int)
                for e in R.iter_file(p, R.rel(p)):
                    cnt[e.key] += 1
                for k, n in cnt.items():
                    if n > 1:
                        issues.append(Issue(
                            WARN, "dup_key", None,
                            f"同文件内重复 {n} 次", key=f"{R.rel(p)}::{k}",
                        ))
    return issues


# ---------------------------------------------------------------- 主流程

def run(
    games: list[str] | None = None,
    resource_dir: str | None = None,
) -> list[Issue]:
    games = games or list(R.GAMES)
    issues: list[Issue] = []
    for g in games:
        issues += lint_le_string(g, resource_dir)
        issues += lint_dict(g, resource_dir)
    issues += check_file_level(resource_dir)
    # 稳定排序，保证幂等
    issues.sort(key=lambda i: (i.file, i.line, i.code, i.detail))
    return issues


def report(issues: list[Issue], severity: str = "ALL", limit: int = 0) -> None:
    order = {ERROR: 0, WARN: 1, INFO: 2}
    sel = [i for i in issues if severity == "ALL" or i.severity == severity]
    sel.sort(key=lambda i: (order.get(i.severity, 9), i.file, i.line, i.code))

    by_code: dict[tuple[str, str], int] = Counter(
        (i.severity, i.code) for i in sel
    )
    print("=" * 72)
    print("L1 机械质检报告")
    print("=" * 72)
    for (sev, code), n in sorted(by_code.items(),
                                key=lambda kv: (order.get(kv[0][0], 9), -kv[1])):
        print(f"  {sev:5s} {code:22s} {n:6d}")
    print("-" * 72)
    print(f"  合计 {len(sel)} 条")
    print()

    shown = sel if limit <= 0 else sel[:limit]
    for i in shown:
        print(str(i))
    if 0 < limit < len(sel):
        print(f"\n... 另有 {len(sel) - limit} 条（--limit 0 显示全部）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1 机械质检（只读）")
    ap.add_argument("--game", action="append", choices=list(R.GAMES),
                    help="只查指定游戏（可重复）")
    ap.add_argument("--severity", default="ALL",
                    choices=["ALL", ERROR, WARN, INFO], help="按严重度过滤")
    ap.add_argument("--limit", type=int, default=0,
                    help="每类最多显示几条，0=全部")
    ap.add_argument("--json", help="把全部结果写成 JSON")
    ap.add_argument("--resource-dir", help="覆盖 Resource 目录（默认取仓库内）")
    ap.add_argument("--strict", action="store_true",
                    help="存在 ERROR 时退出码 1（供 CI 用）")
    a = ap.parse_args(argv)

    issues = run(a.game, a.resource_dir)
    report(issues, a.severity, a.limit)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump([i.as_dict() for i in issues], f,
                      ensure_ascii=False, indent=1)
        print(f"\nJSON 已写入 {a.json}")

    n_err = sum(1 for i in issues if i.severity == ERROR)
    return 1 if (a.strict and n_err) else 0


if __name__ == "__main__":
    sys.exit(main())
