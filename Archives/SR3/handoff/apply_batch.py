# -*- coding: utf-8 -*-
"""
apply_batch.py — 合并翻译批次到 source.jsonl 并做哨兵校验
用法:
  python apply_batch.py <batch.json> [--dry]     # 合并+校验单个批次
  python apply_batch.py --check                   # 全量校验(不修改)
batch.json 格式: {"id": "译文", ...} 或 JSONL [{"id":..,"zh":..}]
"""
import json, re, sys, io, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "source.jsonl")

TAG_RE = re.compile(r'\[[^\]\n]*\]')          # [format] [/format] [color:red] [image:xxx]
FMT_RE = re.compile(r'%[a-zA-Z%]+')           # %s %d %ls %i %f %% %%% %%%%
BRACE_RE = re.compile(r'\{[A-Za-z0-9_:]+\}')  # {0} {1} {TAUNT_IMG} {2:text_tag_crc}

def tokens(s):
    return {
        'tags': TAG_RE.findall(s),
        'fmts': FMT_RE.findall(s),
        'braces': BRACE_RE.findall(s),
        'nl': s.count('\n'),
    }

def validate(en, zh):
    errs = []
    if en is None:
        return ['en is None']
    if not str(en).strip():
        if str(zh).strip():
            errs.append('en为空但zh非空')
        return errs
    if zh is None or not str(zh).strip():
        return ['zh为空']
    te, tz = tokens(str(en)), tokens(str(zh))
    if te['tags'] != tz['tags']:
        errs.append(f"标签不一致 en={te['tags']} zh={tz['tags']}")
    if te['fmts'] != tz['fmts']:
        errs.append(f"占位符不一致 en={te['fmts']} zh={tz['fmts']}")
    if te['braces'] != tz['braces']:
        errs.append(f"花括号不一致 en={te['braces']} zh={tz['braces']}")
    if te['nl'] != tz['nl']:
        errs.append(f"换行数不一致 en={te['nl']} zh={tz['nl']}")
    # 禁止残留大段英文单词(>3字符且全字母、非白名单) — 宽松版: 检查纯ASCII长词
    leftover = re.findall(r'[A-Za-z]{4,}', str(zh))
    whitelist = {'GIVE','DROP','GenX','GENX','K12','MIX','RADIO','AR','TEK','VTOL','FORCER','NFORCER',
                 'Sizzurp','KLASSIC','EZZZY','LEXANI','AKUSTICS','Crooks','Castles',
                 'SKIN','FLESH','GREY','Genki','Genkibowl','TF2','STAG','POW','RIM','DECAL',
                 'OPTION','VARIANT','TINT','PAINT','NUMBER','MOLE','HTTP','DECKERS','XXXI',
                 'S.E.R.C','SER C','BDSM','C-X','K12','XX','MCMANUS','ULTIMAX','KRUKOV',
                 'KOBRA','KA','K','G20','SA','AR55','Z','AKIRA','Aegean','Cypher','Money',
                 'Shot','Penthouse','MMORPG','LAN','GOG','PSN','Xbox','Live','Epic','HDR',
                 'KPI','A','B','C','D','E','F','G','H','I','J','DNA','GPS','FBI','CIA','U',
                 'TV','DJ','MC','K.O','KO','VTOL','URL','ID','OK','APP','GSM','CD','DVD',
                 'Volition','VOLITION',
                 # [format][color:xxx] 哨兵标签关键词(必须原样保留,残留扫描误报)
                 'format','color','red','green','blue','teal','orange','grey','silver',
                 'purple','em','kill','location','use','defend','white','yellow','black',
                 'image','crc','text_tag',
                 # {XXX_IMG} 按钮图哨兵关键词
                 'ACTION','ANALOG','MENU','SELECT','PICKUP','RELOAD','ATTACK',
                 'SECONDARY','TANK','FIRE','PRIMARY','LS','RS','IMG',
                 # {temp_degrees} / {N:text_tag_crc} 哨兵
                 'temp','degrees','text'}
    bad = [w for w in leftover if w not in whitelist and not re.match(r'^(R\d+|C-X\s?\d+|SKIN\d+|FLESH\d+|X{0,3}[IVX]+)$', w)]
    if bad:
        errs.append(f"疑似未译英文残留: {bad[:6]}")
    return errs

def load_batch(path):
    with io.open(path, 'r', encoding='utf-8') as f:
        content = f.read().strip()
    if content.startswith('{') and '\n' not in content:
        return json.loads(content)
    out = {}
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        out[obj['id']] = obj['zh']
    return out

def main():
    args = sys.argv[1:]
    dry = '--dry' in args
    args = [a for a in args if not a.startswith('--')]
    lines = []
    with io.open(SRC, 'r', encoding='utf-8', newline='') as f:
        raw = f.read()
    use_crlf = '\r\n' in raw
    nl = '\r\n' if use_crlf else '\n'
    for line in raw.split(nl):
        if line.strip():
            lines.append(line)
    data = [json.loads(l) for l in lines]
    by_id = {d['id']: d for d in data}

    if not args or args[0] == '--check':
        nerr = 0; ndone = 0
        for d in data:
            if d['zh'] is not None:
                ndone += 1
                errs = validate(d['en'], d['zh'])
                for e in errs:
                    nerr += 1
                    print(f"ERR id={d['id']} [{d['file']}] {e}")
                    print(f"     en: {d['en'][:100]}")
                    print(f"     zh: {str(d['zh'])[:100]}")
        print(f"--- 已译 {ndone}/{len(data)}, 错误 {nerr} ---")
        return

    batch = load_batch(args[0])
    applied = 0; errors = []
    for i, zh in batch.items():
        i = int(i)
        if i not in by_id:
            errors.append(f"id={i} 不存在")
            continue
        d = by_id[i]
        errs = validate(d['en'], zh)
        if errs:
            for e in errs:
                errors.append(f"id={i} [{d['file']}] {e}\n    en: {d['en'][:100]}\n    zh: {str(zh)[:100]}")
        else:
            d['zh'] = zh
            applied += 1
    if errors:
        print(f"!! {len(errors)} 处校验失败, 未写入任何修改:")
        for e in errors:
            print("  " + e)
        sys.exit(1)
    if dry:
        print(f"DRY OK: {applied} 条通过校验")
        return
    with io.open(SRC, 'w', encoding='utf-8', newline='') as f:
        for d in data:
            f.write(json.dumps(d, ensure_ascii=False) + nl)
    print(f"OK: 已合并 {applied} 条并写入 source.jsonl (行尾={'CRLF' if use_crlf else 'LF'})")

if __name__ == '__main__':
    main()
