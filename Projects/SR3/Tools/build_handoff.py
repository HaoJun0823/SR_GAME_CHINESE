# -*- coding: utf-8 -*-
"""
SRTT Remastered 汉化 - 翻译交接包构建器
========================================
输入 : unpack/text/*_us.txt (sr3le_extract.py 解包产物, "KEY": "text" 每行一条)
输出 : unpack/text/_handoff/
   README.md      交接说明(翻译规则/哨兵/回填约定)
   source.jsonl   翻译任务: 每行 {"id","file","keys","en","n","tags"}  UTF-8
   terms_cand.md  专名候选表(自动抽取, 供术语定名)

去重策略: 按 (来源文件, value) 去重 → 同一文件内同文本合一条(keys 聚合),
         不同文件同文本保留为不同行(文件即语境, 便于分译)。
回填约定: 翻译方只改 zh 字段; 空 zh 表示未译; 全部非空即完成。
"""
import glob, json, os, re, collections

TEXT_DIR = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\unpack\text"
OUT_DIR  = os.path.join(TEXT_DIR, "_handoff")

LINE_RE  = re.compile(r'^"([^"]+)": "(.*)"$', re.S)
SENTINEL_RE = re.compile(r'(\[[^\]]*\]|%[sdluS]|%ls|%\d+\$[sd]|\{\d+\}|\\n|\\t|\\x[0-9a-fA-F]{2})')
WORD_RE  = re.compile(r"[A-Z][A-Z'\-]{2,}(?: [A-Z][A-Z'\-]{2,})*|[A-Z][a-z]{2,}(?: [A-Z][a-z]{2,})+")

def parse_file(path):
    """返回 {key: text} 保序"""
    out, order = {}, []
    warn = 0
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        m = LINE_RE.match(line)
        if not m:
            warn += 1
            continue
        k, v = m.group(1), m.group(2)
        if k not in out:
            out[k] = v
            order.append(k)
    return out, order, warn

def tag_class(v):
    """返回哨兵类别: plain/fmt/rich/nl/mixed"""
    has_rich = "[" in v
    has_fmt  = bool(re.search(r"%[sdluS]|%ls|%\d+\$[sd]|\{\d+\}", v))
    has_nl   = "\\n" in v
    if has_rich or has_fmt or has_nl:
        parts = []
        if has_rich: parts.append("rich")
        if has_fmt:  parts.append("fmt")
        if has_nl:   parts.append("nl")
        return "+".join(parts)
    return "plain"

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows, seen = [], set()
    total_keys = total_n = 0
    term_cnt = collections.Counter()

    for f in sorted(glob.glob(os.path.join(TEXT_DIR, "*_us.txt"))):
        stem = os.path.basename(f)[:-4]          # 如 activity_us
        tbl, order, warn = parse_file(f)
        # 文件内按 value 去重 (保首个 key 序)
        byval = collections.OrderedDict()
        for k in order:
            byval.setdefault(tbl[k], []).append(k)
        for v, keys in byval.items():
            rid = len(rows)
            rows.append({
                "id": rid,
                "file": stem,
                "keys": keys,
                "en": v,
                "zh": None,
                "n": len(keys),
                "tags": tag_class(v),
            })
            total_keys += len(keys)
            total_n += 1
        if warn:
            print("WARN %s: %d 行未匹配" % (stem, warn))
        # 专名候选统计
        for kk in order:
            txt = tbl[kk]
            for w in WORD_RE.findall(txt):
                term_cnt[w] += 1

    # 写 source.jsonl
    with open(os.path.join(OUT_DIR, "source.jsonl"), "w", encoding="utf-8") as fo:
        for r in rows:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 专名候选 (≥3 次, 全大写词或 Title Case 短语; 过滤纯数字/停用词)
    stop = {"THE","A","AN","AND","OR","OF","TO","FOR","IN","ON","AT","BY","WITH",
            "YOU","YOUR","YOURS","IT","ITS","THIS","THAT","THESE","THOSE","ALL",
            "IS","ARE","BEEN","BE","WAS","WERE","HAVE","HAS","HAD","DO","DOES",
            "DID","NOT","NO","BUT","AS","FROM","UP","DOWN","OUT","OFF","OVER",
            "INTO","NOW","NEW","GET","GOT","GO","GOES","CAN","CAN'T","WILL","WOULD",
            "SHOULD","MAY","MIGHT","MUST","MORE","MOST","SOME","ANY","EACH","ONE",
            "TWO","THREE","WHEN","WHAT","WHY","WHERE","HOW","THERE","HERE","VERY",
            "OK","PLEASE","THANKS","YEAH","YEP","HEY","WELL","RIGHT","LEFT","GOOD",
            "BAD","BIG","SMALL","LITTLE","MAN","MEN","WOMAN","WOMEN","GUY","GUYS",
            "DON'T","I'M","I'VE","I'LL","YOU'RE","IT'S","THAT'S","WHAT'S","LET'S"}
    cands = [(w, c) for w, c in term_cnt.items()
             if c >= 3 and len(w) >= 4 and w.upper() not in stop
             and not w.isdigit() and "''" not in w]
    cands.sort(key=lambda x: -x[1])
    with open(os.path.join(OUT_DIR, "terms_cand.md"), "w", encoding="utf-8") as fo:
        fo.write("# 专名候选表（自动抽取，人工定名用）\n\n")
        fo.write("> 从英文原文中提取的高频专有名词候选（全大写 / Title Case，出现 ≥3 次）。\n")
        fo.write("> 请为每个 **游戏世界观核心专名** 确定统一译名后回填下方表格，其余条目译时自定。\n\n")
        fo.write("| 英文 | 出现 | 建议译名 | 备注 |\n|---|---|---|---|\n")
        for w, c in cands[:120]:
            fo.write("| %s | %d |  |  |\n" % (w.replace("|", "\\|"), c))

    # 主题语义表
    THEME = {
        "activity":   "小游戏/活动(目标提示/结果/说明)",
        "customize":  "捏人/商店/改装(UI 选项与描述, 最大文件)",
        "diversion":  "支线/街头事件",
        "hud":        "HUD 抬头显示(状态/按键提示/小标签)",
        "menu":       "主菜单/暂停菜单/设置",
        "mission":    "主线/支线任务(简报/目标/对话字幕关联)",
        "subtitle":   "过场字幕(剧情对白)",
        "voice_script":"配音稿(空壳)",
        "static":     "静态通用文本(教程/加载提示/商店)",
        "multiplayer":"多人/合作模式",
        "patch0":     "首日补丁追加文本",
        "platform_pc":   "PC 平台(按键/系统提示)",
        "platform_ps3/ps4/ps5/xb1/xbs/xbox360": "各主机平台(按键/系统提示)",
        "dlc1/2/3":   "DLC 内容文本",
    }
    with open(os.path.join(OUT_DIR, "README.md"), "w", encoding="utf-8") as fo:
        fo.write("# SRTT Remastered 文本翻译交接包\n\n")
        fo.write("> 生成: build_handoff.py | 源: 21 个 *_us.txt (英文) | 规模: %d 条任务 / %d 个 key\n\n" % (len(rows), total_keys))
        fo.write("## 你的任务\n\n")
        fo.write("1. 打开 `source.jsonl`，逐行把 `en` 翻译成简体中文，写入该行 `zh` 字段。\n")
        fo.write("2. 完成判断: 所有行 `zh` 非空。翻译过程中**只允许改 `zh`**，其余字段(id/file/keys/en/n/tags)一律不动。\n")
        fo.write("3. 行顺序可乱、可分文件交付——回填按 `id` 合并。完成后将整包交回，执行回填脚本生成中文 le_strings。\n\n")
        fo.write("## 内容地图(文件 = 语境)\n\n| 文件 | 内容 |\n|---|---|\n")
        for stem_key, desc in THEME.items():
            if "/" in stem_key:
                for s in stem_key.split("/"):
                    fo.write("| %s_us | %s |\n" % (s, desc))
            else:
                fo.write("| %s_us | %s |\n" % (stem_key, desc))
        fo.write("\n实际以 source.jsonl 中出现的 file 为准。\n\n")
        fo.write("## 翻译规则(必读)\n\n")
        fo.write("### 1. 哨兵字符 —— 原样保留, 一字不差\n")
        fo.write("- `[format]` `[/format]` `[color:red]` 等全部 `[...]` 标签: 只译标签**外面**的文字, 标签及被标签包裹的词按需译但**括号本身不动**\n")
        fo.write("  例: `DO %ls IN [format][color:red]PROPERTY DAMAGE[/color][/format]` → 只把 PROPERTY DAMAGE 译为 财产破坏, 保留所有标签\n")
        fo.write("- 格式化占位 `%s %d %ls {0} {1}` : 保留且**顺序不变**, 数量不变\n")
        fo.write("- `\\n` 换行符: 保留(可依中文习惯调整其位置, 但每行内容需自洽)\n")
        fo.write("- `[image:xxx]` 图标引用: 原样保留\n\n")
        fo.write("### 2. 风格\n")
        fo.write("- 全大写 UI 目标/提示句 → 正常简体中文(不要保留全大写)\n")
        fo.write("- 语气: 黑道圣徒3 是恶搞风格喜剧, 俚语/粗口按中文游戏习惯适度本地化, 不逐字直译\n")
        fo.write("- HUD/目标/按钮文本**尽量短**(≤20 字, 参考英文长度), 字幕/描述可稍长\n")
        fo.write("- 保留的英文专名(如人名品牌)首译时查 `terms_cand.md`, 有定名表则用表内译名\n\n")
        fo.write("### 3. 术语\n")
        fo.write("- `terms_cand.md` 中游戏世界观核心专名(帮派/地名/人物/节目名)请统一; 表外专名自行定名后追加到表\n\n")
        fo.write("### 4. 提交前自检\n")
        fo.write("- 每个 `zh` 里的 `[` `]` `%` `{` `}` `\\n` 数量与 `en` 一致(哨兵完整)\n")
        fo.write("- 无空翻译、无未翻译行(null)\n")

    with open(os.path.join(OUT_DIR, "stats.json"), "w", encoding="utf-8") as fo:
        json.dump({
            "tasks": len(rows), "keys": total_keys,
            "uniq_texts_by_file": total_n,
            "tag_dist": dict(collections.Counter(r["tags"] for r in rows)),
            "per_file": dict(collections.Counter(r["file"] for r in rows)),
        }, fo, ensure_ascii=False, indent=1)

    print("任务条数(按文件去重):", len(rows))
    print("key 总数:", total_keys)
    print("tags 分布:", dict(collections.Counter(r["tags"] for r in rows)))
    print("专名候选:", len(cands))
    print("输出:", OUT_DIR)

if __name__ == "__main__":
    main()
