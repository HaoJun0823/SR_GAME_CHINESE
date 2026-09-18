#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 terms_cand.md 的术语定名表 -> glossary.json (en -> 定名)
仅做解析，不触碰翻译产物。"""
import re, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERMS = os.path.join(ROOT, "terms_cand.md")
OUT = os.path.join(ROOT, ".qa", "glossary.json")

def parse():
    glos = {}          # lower_en -> 定名
    sections = {}      # section title -> list of (en, zh)
    cur_sec = "(top)"
    rows = 0
    with open(TERMS, encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\n")
            m = re.match(r"^##\s+(\d+\..+)$", s)
            if m:
                cur_sec = m.group(1).strip()
                sections.setdefault(cur_sec, [])
                continue
            if not s.strip().startswith("|"):
                continue
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            # 跳过表头与分隔行
            if cells[0] in ("英文",) or set(cells[0]) <= set("-: "):
                continue
            if cells[1] in ("定名",):
                continue
            en, zh = cells[0], cells[1]
            if not zh:
                continue
            # 多候选 "A / B"
            variants = re.split(r"\s*/\s*", en)
            for v in variants:
                v = v.strip()
                if not v:
                    continue
                key = v.lower()
                if key not in glos:
                    glos[key] = zh
            sections[cur_sec].append((en, zh))
            rows += 1
    return glos, sections, rows

if __name__ == "__main__":
    glos, sections, rows = parse()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(glos, f, ensure_ascii=False, indent=0)
    print("glossary entries:", rows, "unique keys:", len(glos))
    print("sections:", {k: len(v) for k, v in sections.items()})
    print("sample:", list(glos.items())[:5])
