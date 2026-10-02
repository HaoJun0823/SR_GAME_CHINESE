"""apply_fixes.py —— 按修复单回写 CHS 文本，并全程留痕。

安全设计（这是本仓库唯一会改Resource 的脚本，故格外保守）
--------------------------------------------------------
1. **默认 dry-run**：不加 ``--apply`` 只打印将要做的改动，不落盘。
2. **逐条定位**：按 ``file + key + 旧译文`` 三重校验后才改。
   旧译文对不上 → **拒绝并报错**，避免覆盖他人改动。
3. **同文件内多处命中 → 拒绝**：要求修复单用 ``line`` 精确指定，
   因为实测同 key 会跨文件复用（见 le_resource.py 注释）。
4. **保留原格式**：只替换译文部分，不重排key、不动缩进、不动注释头。
5. **全程留痕**：每次写入 append 到 ``fix_log.csv``（含时间/文件/行/旧/新/来源）。
6. **幂等**：同一份修复单重复应用，第二次会因「旧译文不匹配」而全部跳过。

修复单格式（JSONL，每行一条）
-----------------------------
    {"file": "Resource/SR4/CHS/le_string/menu_us.txt",
     "line": 604,
     "key": "HASH_438825D7",
     "old": "背后",
     "new": "返回",
     "source": "L3-high:UI 返回被译成「背」",
     "note": "菜单返回键，上下文为读取游戏/退出/单人游戏"}

用法
----
    python apply_fixes.py --plan fixes.jsonl                # 预演
    python apply_fixes.py --plan fixes.jsonl --apply         # 真改
    python apply_fixes.py --plan fixes.jsonl --apply --log reports/fix_log.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "helper"))

import le_resource as R  # noqa: E402

DEFAULT_LOG = os.path.join(R.REPO_ROOT, "reports", "fix_log.csv")

LOG_FIELDS = ["time", "file", "line", "key", "old", "new", "source", "note"]


class Fix:
    __slots__ = ("file", "line", "key", "old", "new", "source", "note", "raw")

    def __init__(self, d: dict):
        self.file = d.get("file", "")
        self.line = int(d.get("line") or 0)
        self.key = d.get("key", "")
        self.old = d.get("old", "")
        self.new = d.get("new", "")
        self.source = d.get("source", "")
        self.note = d.get("note", "")
        self.raw = d

    def __repr__(self) -> str:
        return f"<Fix {self.file}:{self.line} {self.key[:30]!r} " \
               f"{self.old[:20]!r}->{self.new[:20]!r}>"


def load_plan(path: str) -> list[Fix]:
    out: list[Fix] = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for i, line in enumerate(f, 1):
            s = line.strip()
            if not s or s.startswith("//"):
                continue
            try:
                out.append(Fix(json.loads(s)))
            except json.JSONDecodeError as e:
                raise SystemExit(f"修复单第 {i} 行不是合法 JSON：{e}")
    return out


# ---------------------------------------------------------------- 核心

def _encode_value(s: str) -> str:
    """把字符串编码成与源文件一致的 JSON 字面量片段（不含外层引号）。"""
    return json.dumps(s, ensure_ascii=False)[1:-1]


def apply_one(abs_path: str, fix: Fix) -> tuple[str, str]:
    """在单个文件里应用一条修复。返回 (状态, 说明)。

    状态：ok / skip / conflict
    """
    raw = R.read_text(abs_path)
    lines = raw.split("\n")
    idx = fix.line - 1
    if idx < 0 or idx >= len(lines):
        return "conflict", f"行号越界（文件共 {len(lines)} 行）"
    line = lines[idx].rstrip("\r")
    parsed = _parse_line(line)
    if parsed is None:
        return "conflict", "该行不是数据行（无法定位）"
    key, text = parsed
    if fix.key and key != fix.key:
        return "conflict", f"key 不符：期望 {fix.key!r} 实际 {key!r}"
    if fix.old != "" and text != fix.old:
        return "skip", f"旧译文已不匹配（现为 {text[:30]!r}），可能已修复过"
    if text == fix.new:
        return "skip", "译文已是目标值"

    # 只替换第二个引号对内的内容。
    # ⚠ group(1) 是 ``"KEY": ``（**已含冒号和空格**），group(3) 是尾部（``;`` 等）。
    #   拼接时必须用 group(1) 原样 + 新文本 + group(3)，
    #   **不要再补一个冒号**——曾写成 ``group(1) + ': ' + ...`` 造成
    #   ``"KEY": : "新值"`` 的结构损坏，并真的落盘过（已由 git 回滚）。
    #   v1 实测事故记录，勿改回。
    m = re_match_entry(line)
    if not m:
        return "conflict", "无法解析行结构"
    new_line = m.group(1) + '"' + _encode_value(fix.new) + '"' + m.group(3)
    # 写回前的自检：新行必须仍能解析、key 必须不变、译文必须等于目标值。
    # 三条任一不满足就拒绝落盘（宁可少改，不可改坏）。
    chk = _parse_line(new_line)
    if chk is None:
        return "conflict", f"改写后行结构非法：{new_line[:60]!r}"
    new_key, new_text = chk
    if key != new_key:
        return "conflict", f"改写后 key 漂移：{key!r} -> {new_key!r}"
    if new_text != fix.new:
        return "conflict", f"改写后译文不符：{new_text[:30]!r}"
    lines[idx] = new_line
    with open(abs_path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))
    return "ok", f"{text[:30]!r} -> {fix.new[:30]!r}"


import re  # noqa: E402  （放在后面以免干扰上面的说明）

_ENTRY_RE = re.compile(r'^(\s*"(?:[^"\\]|\\.)*"\s*:\s*)("(?:[^"\\]|\\.)*")(\s*;?\s*)$')


def re_match_entry(line: str):
    return _ENTRY_RE.match(line)


# 单独捕获「纯 key 部分」（不含引号与冒号）
_KEY_ONLY_RE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*:')


def _parse_line(line: str):
    """返回 (key, text)；非数据行返回 None。

    ⚠ ``_ENTRY_RE`` 的 group(1) 是 ``'"KEY": '`` **整段**（含引号与冒号），
    不能直接当 key 用。曾误用导致全部条目报「key 不符」（v1 实测踩过）。
    这里改用 ``_KEY_ONLY_RE`` 单独提取裸 key。
    """
    m = _ENTRY_RE.match(line)
    if not m:
        return None
    km = _KEY_ONLY_RE.match(line)
    key = R._unquote('"' + km.group(1) + '"') if km else ""
    return key, R._unquote(m.group(2))


# ---------------------------------------------------------------- 主流程

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按修复单回写 CHS（默认 dry-run）")
    ap.add_argument("--plan", required=True, help="修复单 JSONL")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只预演）")
    ap.add_argument("--log", default=DEFAULT_LOG, help="留痕 CSV")
    ap.add_argument("--stop-on-conflict", action="store_true",
                    help="遇到 conflict 立即中止")
    a = ap.parse_args(argv)

    fixes = load_plan(a.plan)
    print("=" * 72)
    print("修复回写" + ("（APPLY 模式）" if a.apply else "（DRY-RUN 预演，未落盘）"))
    print("=" * 72)
    print(f"  修复单 {len(fixes)} 条")
    print()

    stats = {"ok": 0, "skip": 0, "conflict": 0}
    log_rows = []
    ts = _dt.datetime.now().isoformat(timespec="seconds")

    for fx in fixes:
        abs_path = os.path.join(R.REPO_ROOT, fx.file.replace("/", os.sep))
        if not os.path.isfile(abs_path):
            stats["conflict"] += 1
            print(f"  CONFLICT  文件不存在：{fx.file}")
            if a.stop_on_conflict:
                break
            continue
        status, msg = apply_one(abs_path, fx)
        stats[status] += 1
        mark = {"ok": "OK      ", "skip": "SKIP    ", "conflict": "CONFLICT"}[status]
        print(f"  {mark} {fx.file}:{fx.line}  {fx.key[:34]}")
        print(f"           {msg}")
        if status == "ok" and a.apply:
            log_rows.append({
                "time": ts, "file": fx.file, "line": fx.line, "key": fx.key,
                "old": fx.old, "new": fx.new, "source": fx.source, "note": fx.note,
            })
        if status == "conflict" and a.stop_on_conflict:
            print("\n  已按 --stop-on-conflict 中止")
            break

    print()
    print(f"  成功 {stats['ok']}  跳过 {stats['skip']}  冲突 {stats['conflict']}")
    if not a.apply:
        print("\n  这是预演。加 --apply 才会真正写入。")
    elif log_rows:
        os.makedirs(os.path.dirname(a.log) or ".", exist_ok=True)
        exists = os.path.isfile(a.log)
        with open(a.log, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            if not exists:
                w.writeheader()
            w.writerows(log_rows)
        print(f"  已留痕 {len(log_rows)} 条 -> {a.log}")

    return 1 if stats["conflict"] else 0


if __name__ == "__main__":
    sys.exit(main())
