# -*- coding: utf-8 -*-
"""
SRTT 汉化批量回填工具 (翻译 → le_strings)
=========================================
输入:
  source_zh.jsonl   build_handoff.py 格式 (id, file, keys, en, zh, n, tags)
                    - zh 字段非空表示已翻译
  slot_table.json   slot_table.py 生成的 字符↔槽码 映射 (U+XXXX -> 槽码 int)
  unpack/misc/*.le_strings  原文件
输出:
  unpack/misc/*.le_strings  改写后(槽码编码中文 + ASCII 原文保留)

流程:
  1. 扫描 unpack/text/*_us.txt -> 构造 {key: en_orig}
  2. 读 source_zh.jsonl -> 构造 {key: zh} (跳过 zh=null)
  3. 收集 zh 全字符集 -> 提示 slot_table 应已包含所有
  4. 对每个 *_us.le_strings:
       构造 pairs = {hash(crc(key)): slot_u16_bytes(zh)}
       le_strings_repack.repack(in, pairs, in)
  5. 打印统计 + 异常 hash 未匹配警告
"""
import sys, os, json, glob, re, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sr3le_extract import crc_volition
from le_strings_repack import repack
from slot_table import text_to_bytes


TEXT_DIR = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\unpack\text"
LE_DIR   = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\unpack\misc"
HANDOFF  = os.path.join(TEXT_DIR, "_handoff")


def parse_text_keyval(path):
    """unpack/text/<file>.txt -> {key: en_orig} (en 含转义回退)"""
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\r\n")
        m = re.match(r'^"([^"]+)": (.*)$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def load_source_zh(path):
    """source_zh.jsonl -> {key: zh}; 跳过 zh=null."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                continue
            r = json.loads(ln)
            if r.get("zh"):
                for k in r["keys"]:
                    if k not in out:                  # 同 value 多 key, 取首条
                        out[k] = r["zh"]
    return out


def main(source_zh=None, slot_table_path=None, dry_run=False):
    src_path = source_zh or os.path.join(HANDOFF, "source.jsonl")
    slt_path = slot_table_path or os.path.join(HANDOFF, "slot_table.json")
    zh_map = load_source_zh(src_path)
    if not zh_map:
        print("source_zh 未发现 zh 翻译 (zh=null), 跳过")
        return
    slot_map = json.load(open(slt_path, encoding="utf-8"))
    slot_int = {int(k[2:], 16): v for k, v in slot_map.items()}
    # 校验: 所有 zh 字符都在 slot_table
    missing = []
    for k, zh in zh_map.items():
        for ch in zh:
            ci = ord(ch)
            if 0x20 <= ci <= 0xFF:
                continue
            if ci not in slot_int:
                missing.append((k, hex(ci), ch))
                break
    if missing:
        print(f"⚠ {len(missing)} 条翻译含 slot_table 未分配字符, 前 10:")
        for k, h, c in missing[:10]:
            print(f"   key={k!r} char={h}({c})")
        print("请先在 slot_table.json 中加入这些字符, 再重跑.")
        return
    # 按 file 分配: {file_stem: {hash: bytes}}
    # 从 source.jsonl 还可得 key->file 映射
    key2file = {}
    with open(src_path, encoding="utf-8") as f:
        for ln in f:
            r = json.loads(ln)
            for k in r["keys"]:
                key2file.setdefault(k, r["file"])
    # 分组 pairs per file
    file_pairs = collections.defaultdict(dict)
    unfound_keys = []
    for k, zh in zh_map.items():
        h = crc_volition(k)
        f = key2file.get(k)
        if not f:
            unfound_keys.append(k)
            continue
        try:
            bs = text_to_bytes(zh, slot_int)
        except KeyError as e:
            print(f"!! key={k} 转槽失败: {e}")
            continue
        file_pairs[f][h] = bs
    if unfound_keys:
        print(f"⚠ {len(unfound_keys)} key 无 file 信息 (前 5): {unfound_keys[:5]}")
    # repack each file
    stats = []
    for fstem, pairs in sorted(file_pairs.items()):
        le_path = os.path.join(LE_DIR, fstem + ".le_strings")
        if not os.path.exists(le_path):
            print(f"!! {le_path} 不存在, 跳过")
            continue
        # 备份
        bak = le_path + ".bak"
        if not os.path.exists(bak):
            import shutil
            shutil.copy2(le_path, bak)
        if dry_run:
            print(f"[DRY] {fstem}: {len(pairs)} 条将改写 (备份 {bak} 已就位)")
            continue
        n = repack(le_path, pairs, le_path)
        stats.append((fstem, len(pairs), n))
        print(f"  {fstem}: 改写 {len(pairs)} 条, 新 nstr={n}")
    print(f"\n完成 {len(stats)} 个文件, 总改写 {sum(s[1] for s in stats)} 条")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None)
    ap.add_argument("--slot", default=None)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    main(args.source, args.slot, args.dry)