#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_keydict.py —— 生成「本地化 KEY -> 文本」词典（通道 A 的补充层）

背景（2026-09-17 实测）
----------------------
SR4 的本地化容器有两种键形态：

  * 可读键：   "PLT_MENU_WINDOWED": "WINDOWED"      （platform_pc_us.txt / menu_us.txt 部分条目）
  * 哈希槽位： "HASH_4E94B5AD": "TELEKINESIS"       （解包器按 CRC32 给槽位起的名字）

引擎在**语言服务层**完成 KEY -> 文本解析。当解析失败（返回 NULL）时，文本对象会回退到
「原始 KEY 模板」—— 整屏 UI 直接显示 `MENU_BACK` / `PLT_MENU_WINDOWED` 这类键名。
这类键名随后进入 Format 钩子，而 project 词典是**英文原文**为键的，因此 100% miss。

本工具把 resource/le_string/{zh,en}/ 里**可读键**的条目转成词典条目：

    zh: "PLT_MENU_WINDOWED": "窗口化";
    en: "PLT_MENU_WINDOWED": "WINDOWED";     <- 取 le_string/en 的英文值（不是键名本身）

不处理的键
----------
* `HASH_xxxxxxxx` —— 引擎是按 32 位哈希查表，运行时不会以「字符串」形态把这些键送到钩子层，
  插表也无从命中，故整体跳过。
* `platform_nx64_us.txt` / `platform_ggp_us.txt` —— 主机平台的值多为单字母缩写
  （"W"/"T"/"P"...），是手柄按键提示，混进 PC 词典会造成错误替换，故整体跳过。

安全约束（2026-09-17 v1.5 崩溃复盘后加入，勿删）
-----------------------------------------------
本表的键是**裸 KEY**，引擎送裸 KEY 时通常**不带格式化参数**。而 dllmain.cpp 的
HookFormat 命中词典后是「整串替换」且**不校验占位符**：

    const DictNode* r = LookupNode(fmt, ...);
    if (r) fmt = r->trans;                 // 无 % 校验
    return g_origFormat(dst, fmt, cap, args, argc);   // args/argc 还是原来的

因此若译文里出现 `%s`/`%ls`/`%d` 这类转换符，而调用方 argc=0，引擎格式化器会去读一个
**不存在的参数**（栈上/寄存器里的野指针）-> 崩溃。故本工具强制：

  [S1] 译文（键为 KEY 时）**不得包含任何 % 转换符** -> 命中即丢弃并列出
  [S2] 值里的 `\\n` / `\\r` / `\\t` / `"` / `\\` 必须转义后写出（否则词典按行解析会截断条目）
  [S3] 写出前**用与 DLL 相同的按行规则回读校验**；任一行解析失败 -> 不写文件、退出码 2

EXTRA_KEYS（游戏资源缺失键兜底）
--------------------------------
下列 KEY 由引擎直接送入 Format，但在 PC 版 le_string 资源里**根本不存在**
（部分仅出现在主机平台表），因此上表覆盖不到，需要手工兜底。
判据来源: G:\\Downloads\\SR4R_I18N (2).log 的 wrap: 行 + scripts\\DumpText.dtxt。

用法
----
    python Tools/gen_keydict.py            # 校验通过才写 resource/dict/{zh,en}/le_string_keys.txt
    python Tools/gen_keydict.py --check    # 只统计, 不写文件
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LE_ZH = ROOT / "resource" / "le_string" / "zh"
LE_EN = ROOT / "resource" / "le_string" / "en"
OUT_ZH = ROOT / "resource" / "dict" / "zh" / "le_string_keys.txt"
OUT_EN = ROOT / "resource" / "dict" / "en" / "le_string_keys.txt"

SKIP_FILES = {"platform_nx64_us.txt", "platform_ggp_us.txt"}
SKIP_KEY_PREFIX = "HASH_"

LINE_RE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*;?\s*$')
# 与 LINE_RE 同源的严格校验：整行必须恰好是 "k": "v";
STRICT_RE = re.compile(r'^"((?:\\.|[^"\\])*)": "((?:\\.|[^"\\])*)";$')
# % 转换符: % 后跟除 % 以外的字符（%% 是字面百分号, 安全）
PCT_RE = re.compile(r"%(.)")

# ---------------------------------------------------------------------------
# 手工兜底：PC 版 le_string 中不存在的 UI 键
#   key  = 引擎实际送入 Format 的串（= 原始 KEY 模板）
#   val  = 中文译文；eng = 英文原文
#   标注 [推断] 的条目语义由同族键/英文语义等价项推导, 上线前建议人工复核。
# ---------------------------------------------------------------------------
EXTRA_KEYS = {
    # [高置信] MENU_BACK = 返回按钮。英文原文 BACK；DumpText 中同屏出现 "Esc" 与其拼接。
    "MENU_BACK": ("返回", "BACK"),
    # [高置信] 同族键 MENU_RESTORE_ALL_DEFAULTS="恢复所有分类的默认设置"。
    "MENU_RESTORE_DEFAULTS": ("恢复默认设置", "RESTORE DEFAULTS"),
    # [高置信] 确认/取消按钮（CONTROL_YES 与 CONTROL_NO 成对）。
    "CONTROL_YES": ("是", "YES"),
    "CONTROL_NO": ("否", "NO"),
    # [推断] 引擎在标题/提示界面请求的开始提示键；PC 表无此键(仅 nx64 表有, 值 "P")。
    "PLT_PRESS_START": ("按开始键", "PRESS START"),
}

EXTRA_NOTE = {
    "MENU_BACK": "返回按钮（高频）",
    "MENU_RESTORE_DEFAULTS": "恢复默认设置",
    "CONTROL_YES": "确认按钮",
    "CONTROL_NO": "取消按钮（配对兜底）",
    "PLT_PRESS_START": "[推断] 开始提示",
}


def has_cjk(s: str) -> bool:
    return any(ord(c) >= 0x2E80 for c in s)


def pct_specs(s: str):
    """返回 % 转换符列表（忽略转义 %%）"""
    return [c for c in PCT_RE.findall(s) if c != "%"]


def unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}.get(n, n))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def esc(s: str) -> str:
    """写回词典行时的转义 —— 必须吃掉换行/制表, 否则按行解析会截断条目 [S2]"""
    return (s.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\r", "\\r")
             .replace("\n", "\\n")
             .replace("\t", "\\t"))


def _read_table(d: Path):
    """返回 {key: value}；跳过 SKIP_FILES。"""
    out = {}
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.txt")):
        if f.name in SKIP_FILES:
            continue
        for raw in f.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            m = LINE_RE.match(raw)
            if not m:
                continue
            k, v = unescape(m.group(1)), unescape(m.group(2))
            if k and v and not k.startswith(SKIP_KEY_PREFIX):
                out.setdefault(k, v)
    return out


def collect():
    """返回 (entries, stats)；entries[key] = (zh_val, en_val, src_file)。"""
    entries = {}
    stats = {"files": 0, "lines": 0, "named": 0, "hash": 0, "cjk": 0,
             "overwritten": 0, "no_en": 0, "dropped_pct": [], "raw": {}}

    if not LE_ZH.is_dir():
        print(f"[!] 找不到 {LE_ZH}", file=sys.stderr)
        return entries, stats

    en_tab = _read_table(LE_EN)

    for f in sorted(LE_ZH.glob("*.txt")):
        if f.name in SKIP_FILES:
            continue
        stats["files"] += 1
        for raw in f.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            stats["lines"] += 1
            m = LINE_RE.match(raw)
            if not m:
                continue
            key, val = unescape(m.group(1)), unescape(m.group(2))
            if not key or not val:
                continue
            if key.startswith(SKIP_KEY_PREFIX):
                stats["hash"] += 1
                continue
            stats["named"] += 1
            if not has_cjk(val):
                continue                     # 中文表里仍是 ASCII => 未译, 插了等于没插
            stats["cjk"] += 1
            # [S1] 裸 KEY 词典: 译文带 % 转换符 => 无参数时野指针风险, 一律丢弃
            bad_specs = pct_specs(val)
            if bad_specs:
                stats["dropped_pct"].append((f.name, key, val, bad_specs))
                continue
            if key in entries:
                stats["overwritten"] += 1
            en_val = en_tab.get(key)
            if en_val is None:
                stats["no_en"] += 1
                en_val = key
            # en 镜像同样受 [S1] 约束（其值也可能被用到）
            if pct_specs(en_val):
                en_val = re.sub(r"%(.)", lambda m: "%%" if m.group(1) != "%" else "%%", en_val)
            entries[key] = (val, en_val, f.name)
    return entries, stats


def build_lines(entries, lang: str):
    L = [
        f"// SR4R le_string_keys.txt ({lang}) —— 本地化 KEY -> {'中文' if lang == 'zh' else '英文原文'}",
        "// 生成: Tools/gen_keydict.py（勿手改，重新生成即可；兜底键改脚本里的 EXTRA_KEYS）",
        "// 用途: 引擎语言服务解析失败时, 文本对象会回退到原始 KEY 模板",
        "//       (屏幕上直接显示 MENU_BACK / PLT_xxx)。本表把这类可读 KEY 直接映射为译文。",
        "// 约束: 键为裸 KEY, 引擎送裸 KEY 时不带格式化参数 -> 译文一律不含 % 转换符 [S1]",
        "// 来源: resource/le_string/zh/*.txt + 手工兜底 EXTRA_KEYS",
        "",
        "// ---- 手工兜底: PC 版 le_string 中不存在的 UI 键 ----",
    ]
    for k in sorted(EXTRA_KEYS):
        zh, eng = EXTRA_KEYS[k]
        note = EXTRA_NOTE.get(k, "")
        if note:
            L.append(f"// {note}")
        L.append(f'"{esc(k)}": "{esc(zh if lang == "zh" else eng)}";')
    L.append("")
    L.append("// ---- le_string 可读键 (自动生成) ----")
    n = 0
    for key in sorted(entries):
        zh_val, en_val, _src = entries[key]
        v = zh_val if lang == "zh" else en_val
        L.append(f'"{esc(key)}": "{esc(v)}";')
        n += 1
    return L, n


def validate(lines, path: Path):
    """[S3] 用与 DLL 相同的按行规则回读校验。返回错误列表。"""
    errs = []
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if not s or s.startswith("//"):
            continue
        m = STRICT_RE.match(s)
        if not m:
            errs.append((i, ln[:100]))
            continue
        # 键/值里不得残留裸控制字符
        for part_name, part in (("key", m.group(1)), ("val", m.group(2))):
            if any(ch in part for ch in ("\n", "\r", "\t")):
                errs.append((i, f"{part_name} 含裸控制字符: {ln[:80]}"))
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只统计, 不写文件")
    ap.add_argument("--allow-pct", action="store_true",
                    help="调试用: 允许译文含 %% 转换符（默认禁止, 见 [S1]）")
    ap.add_argument("--strict-plain", action="store_true",
                    help="最保守: 只保留纯文本标签, 丢弃含 [format]/[color:] 标记或 {} 占位符的模板串")
    args = ap.parse_args()

    entries, st = collect()

    # 严格模式: 剔掉"模板串"(带游戏标记 / 花括号占位符 / 图片令牌)
    dropped_tpl = []
    if args.strict_plain:
        for k in list(entries):
            v = entries[k][0]
            if re.search(r"\[format\]|\[color:|\[/|\[/format\]", v) or re.search(r"\{[^}]*\}", v):
                dropped_tpl.append((k, v))
                del entries[k]

    print(f"le_string/zh: {st['files']} 文件 / {st['lines']} 行")
    print(f"  可读键 {st['named']}  ->  有中文值 {st['cjk']}  ->  "
          f"丢弃(含%) {len(st['dropped_pct'])}  ->  去重后 {len(entries)}"
          + (f"(严格模式再丢弃模板串 {len(dropped_tpl)})" if args.strict_plain else "")
          + (f"（同键覆盖 {st['overwritten']}）" if st["overwritten"] else ""))
    print(f"  跳过 HASH_ 槽位名 {st['hash']}；跳过主机平台表 {sorted(SKIP_FILES)}")
    print(f"  en 表缺对应项 {st['no_en']}（回退用键名占位）")
    print(f"  手工兜底 EXTRA_KEYS {len(EXTRA_KEYS)} 条")

    if st["dropped_pct"]:
        print(f"  --- [S1] 因译文含 % 转换符而丢弃 {len(st['dropped_pct'])} 条 ---")
        for fn, k, v, sp in st["dropped_pct"]:
            print(f"    {fn:24s} {k:44s} specs={sp}  val={v[:60]}")

    # 复核清单: 值里带游戏标记/花括号占位符 => 是"模板串"而非纯标签,
    #   引擎若拿到裸 KEY(解析失败) 则 {n} 不会展开, 会原样显示。不崩溃, 但值得过目。
    review = [(k, v) for k, (v, _e, _f) in entries.items()
              if re.search(r"\[format\]|\[color:|\[/", v) or re.search(r"\{[^}]*\}", v)]
    if review:
        print("  --- [复核] 值含游戏标记/花括号占位符 %d 条（模板串; 裸 KEY 路径下 {n} 不会展开）---"
              % len(review))
        for k, v in review[:20]:
            print(f"    {k:44s} {v[:80]}")
    if dropped_tpl:
        print("  --- [严格模式] 已丢弃的模板串 %d 条 ---" % len(dropped_tpl))
        for k, v in dropped_tpl[:20]:
            print(f"    {k:44s} {v[:80]}")

    if args.check:
        return 0

    rc = 0
    for lang, out in (("zh", OUT_ZH), ("en", OUT_EN)):
        lines, n = build_lines(entries, lang)
        errs = validate(lines, out)
        if errs:
            print(f"[FATAL] {out.name} ({lang}) 校验失败 {len(errs)} 行, 拒绝写出:", file=sys.stderr)
            for i, s in errs[:20]:
                print(f"   line {i}: {s}", file=sys.stderr)
            rc = 2
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        print(f"写出 {out.relative_to(ROOT)}  ({n} 条 le_string + {len(EXTRA_KEYS)} 条兜底)  "
              f"校验 OK, 0 bad line")
    return rc


if __name__ == "__main__":
    sys.exit(main())
