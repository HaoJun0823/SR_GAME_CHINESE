"""snapshot.py —— P0 基线快照与回滚依据（只读）。

作用
----
1. ``export``  把 CHS 全部条目导出成一份 CSV 快照 + 逐文件 MD5 清单。
   有了它，任何批量修改都能diff 出「到底改了哪几条」，也能整体回滚。
2. ``verify``  比对当前状态与基线，报告哪些文件/条目发生了变化。

**绝不修改 Resource 下任何文件。**

用法
----
    python snapshot.py export                       # 建基线
    python snapshot.py verify                       # 对比基线
    python snapshot.py export --out baseline2
    python snapshot.py verify --baseline baseline
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "helper"))

import le_resource as R  # noqa: E402

DEFAULT_DIR = os.path.join(R.REPO_ROOT, "reports", "baseline")
CSV_NAME = "snapshot.csv"
MANIFEST_NAME = "manifest.json"


def file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def text_md5(path: str) -> str:
    """按规范化文本算 MD5（忽略 BOM 与 CRLF 差异），只用于「内容是否相同」。"""
    return hashlib.md5(R.read_text(path).encode("utf-8")).hexdigest()


def collect(resource_dir: str | None = None) -> list[dict]:
    """收集所有 CHS 条目。"""
    rows: list[dict] = []
    for game in R.GAMES:
        for carrier in R.CARRIERS:
            for e in R.load_entries(game, "CHS", carrier, resource_dir):
                rows.append({
                    "game": game,
                    "carrier": carrier,
                    "file": e.file,
                    "line": e.line,
                    "key": e.key,
                    "text": e.text,
                })
    rows.sort(key=lambda r: (r["file"], r["line"]))
    return rows


def build_manifest(resource_dir: str | None = None) -> dict:
    files = []
    for game in R.GAMES:
        for carrier in R.CARRIERS:
            for p in R.list_carrier_files(game, "CHS", carrier, resource_dir):
                files.append({
                    "file": R.rel(p),
                    "bytes": os.path.getsize(p),
                    "md5": file_md5(p),
                    "text_md5": text_md5(p),
                })
    rows = collect(resource_dir)
    return {
        "entry_count": len(rows),
        "file_count": len(files),
        "files": files,
    }


def do_export(out_dir: str, resource_dir: str | None = None) -> int:
    os.makedirs(out_dir, exist_ok=True)
    rows = collect(resource_dir)
    csv_path = os.path.join(out_dir, CSV_NAME)
    # utf-8-sig：Excel 直接双击可读
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["game", "carrier", "file", "line", "key", "text"])
        w.writeheader()
        w.writerows(rows)
    man = build_manifest(resource_dir)
    man_path = os.path.join(out_dir, MANIFEST_NAME)
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(f"基线已导出：{len(rows)} 条/ {man['file_count']} 文件")
    print(f"  条目快照 {csv_path}")
    print(f"  文件清单 {man_path}")
    return 0


def _load(out_dir: str) -> tuple[list[dict], dict]:
    csv_path = os.path.join(out_dir, CSV_NAME)
    man_path = os.path.join(out_dir, MANIFEST_NAME)
    if not os.path.isfile(csv_path):
        raise SystemExit(f"找不到基线快照：{csv_path}")
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    man = {}
    if os.path.isfile(man_path):
        with open(man_path, "r", encoding="utf-8") as f:
            man = json.load(f)
    return rows, man


def do_verify(out_dir: str, resource_dir: str | None = None) -> int:
    base_rows, base_man = _load(out_dir)
    base_files = {f["file"]: f for f in base_man.get("files", [])}
    cur_man = build_manifest(resource_dir)
    cur_files = {f["file"]: f for f in cur_man["files"]}

    changed_files = []
    for f, b in base_files.items():
        c = cur_files.get(f)
        if c is None:
            changed_files.append((f, "已删除"))
        elif c["text_md5"] != b["text_md5"]:
            changed_files.append((f, "已修改"))
    for f in cur_files:
        if f not in base_files:
            changed_files.append((f, "新增"))

    print("=" * 72)
    print("基线比对")
    print("=" * 72)
    print(f"  基线条目 {len(base_rows)}  当前条目 {cur_man['entry_count']}")
    if not changed_files:
        print("  ✔ 所有文件与基线一致（仅字节级 BOM/CRLF 差异不计）")
        return 0
    print(f"\n  变化文件 {len(changed_files)} 个：")
    for f, why in sorted(changed_files):
        print(f"    {why:8s} {f}")

    # 逐条目 diff（仅对已修改的文件）
    cur_rows = collect(resource_dir)
    base_map = {(r["file"], r["key"]): r["text"] for r in base_rows}
    cur_map = {(r["file"], r["key"]): r["text"] for r in cur_rows}
    n_mod = n_add = n_del = 0
    mods = []
    for k, bt in base_map.items():
        ct = cur_map.get(k)
        if ct is None:
            n_del += 1
        elif ct != bt:
            n_mod += 1
            mods.append((k, bt, ct))
    for k in cur_map:
        if k not in base_map:
            n_add += 1
    print(f"\n  条目级：修改 {n_mod}  新增 {n_add}  删除 {n_del}")
    for (f, key), bt, ct in mods[:40]:
        print(f"    {f}  {key[:40]}")
        print(f"        旧: {bt[:66]!r}")
        print(f"        新: {ct[:66]!r}")
    if len(mods) > 40:
        print(f"    ... 另有 {len(mods) - 40} 条")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CHS 基线快照/比对（只读）")
    ap.add_argument("action", choices=["export", "verify"])
    ap.add_argument("--out", default=DEFAULT_DIR, help="基线目录")
    ap.add_argument("--resource-dir")
    a = ap.parse_args(argv)
    if a.action == "export":
        return do_export(a.out, a.resource_dir)
    return do_verify(a.out, a.resource_dir)


if __name__ == "__main__":
    sys.exit(main())
