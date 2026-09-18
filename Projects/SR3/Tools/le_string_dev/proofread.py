# -*- coding: utf-8 -*-
import re, os, glob, collections

LE = 'le_data' if False else '.'
SCH = 'schinese'
ORG = 'original'

EXEEMPT = set(s.upper() for s in [
    'xbox','live','tf2','gps','ok','id','npc','vip','html','url','dvd','pc','ps3','ps4','ps5',
    'steam','sr3','stag','uk','us','eu','ascii','usb','hd','sd','wifi','blu','ray','led','cpu',
    'gpu','fps','ai','rpg','pda','ui','hud','vpk','vpp','xtbl','cfo','ceo','bst','kil','mega',
    'giga','kb','mb','gb','cm','apocalypse','genki','saints','row','vtol','n','e','w','s','de','fr',
    'es','it','jp','pl','ru','sk','nl','cz','zh','srtt','dlc1','dlc2','dlc3','nuke','reactors',
])

def parse(p):
    """return list of (key, value) preserving order"""
    s = open(p, encoding='utf-8').read().replace('\r\n','\n')
    out = []
    for m in re.finditer(r'^\s*"([^"]+)":\s*"(.*)"\s*$', s, re.M):
        out.append((m.group(1), m.group(2)))
    return out

def strip_format(v):
    v = re.sub(r'\[/?format\]', '', v)
    v = re.sub(r'\[color:[^\]]*\]', '', v)
    v = re.sub(r'\[image:[^\]]*\]', '', v)
    v = re.sub(r'\{[^}]*\}', '', v)
    v = re.sub(r'%[sdi f%]', '', v)
    v = re.sub(r'\\n', '', v)
    v = re.sub(r'\\"', '', v)
    v = v.replace('“','').replace('”','')
    return v

def has_cjk(s):
    return bool(re.search(r'[\u4e00-\u9fff]', s))

# ---- gather all 21 files ----
files = sorted(glob.glob(os.path.join(SCH, '*_us.txt')))
data = {}   # base -> (orig_list, sch_list)
for f in files:
    base = os.path.basename(f)[:-len('_us.txt')]
    op = os.path.join(ORG, base+'_us.txt')
    if not os.path.exists(op):
        continue
    data[base] = (parse(op), parse(f))

# ---- pass 1: untranslated / no-cjk / empty ----
exact_untrans = []      # zh == en (non-empty)
no_cjk_susp = []        # stripped has alpha not in exempt, no cjk
empty_but_src = []      # sch empty, orig non-empty

for base,(ol,sl) in data.items():
    od = dict(ol); sd = dict(sl)
    keys = list(od.keys())
    for k in keys:
        ov = od[k]; sv = sd.get(k, None)
        if sv is None:
            continue
        if sv.strip() == '' and ov.strip() != '':
            empty_but_src.append((base,k,ov))
            continue
        if sv.strip() == ov.strip() and re.search(r'[A-Za-z]{3,}', ov):
            exact_untrans.append((base,k,ov))
            continue
        rem = strip_format(sv)
        if not has_cjk(rem):
            words = [w.upper() for w in re.findall(r'[A-Za-z]{2,}', rem)]
            real = [w for w in words if w not in EXEEMPT]
            if real:
                no_cjk_susp.append((base,k,rem.strip(),real))

# ---- pass 2: format token integrity (re-check) ----
def fmt_counts(v):
    return (v.count('[format]'), v.count('[/format]'),
            v.count('\\"')+v.count('“')+v.count('”'),
            len(re.findall(r'\{[^}]*\}', v)))
fmt_bad = []
for base,(ol,sl) in data.items():
    od=dict(ol); sd=dict(sl)
    for k in od:
        ov=od[k]; sv=sd.get(k,'')
        if fmt_counts(ov)!=fmt_counts(sv):
            fmt_bad.append((base,k,fmt_counts(ov),fmt_counts(sv)))

# ---- pass 3: terminology consistency vs glossary ----
glos = []
tg = open('terms_cand.md', encoding='utf-8').read()
for m in re.finditer(r'^\|\s*(.+?)\s*\|\s*(.+?)\s*\|', tg, re.M):
    en=m.group(1).strip(); zh=m.group(2).strip()
    if not en or not zh: continue
    if en.startswith('#') or en in ('英文','---'): continue
    # skip header-ish
    glos.append((en,zh))
# clean zh for matching (drop brackets/quotes)
def clean_zh(z):
    return re.sub(r'[《》""''（）()\[\]]','',z).strip()
term_bad=[]
for en,zh in glos:
    cz = clean_zh(zh)
    if len(cz)<1: continue
    en_l = en.lower()
    for base,(ol,sl) in data.items():
        od=dict(ol); sd=dict(sl)
        for k in od:
            ov=od[k].lower()
            if en_l in ov:
                # does schinese contain expected zh?
                if cz and cz not in sd.get(k,''):
                    term_bad.append((base,k,en,cz,sd.get(k,'')))

# ---- report ----
def lines():
    yield "="*70
    yield "SR3 简体中文 schinese/ 校对报告"
    yield "生成: 自动化扫描（未译/漏翻/格式/术语一致性）"
    yield "="*70
    yield ""
    yield "## 汇总"
    yield "文件数: %d | 总条目: %d" % (len(data), sum(len(v[1]) for v in data.values()))
    yield "A. 完全未译 (zh==en): %d" % len(exact_untrans)
    yield "B. 疑似漏翻 (去格式后纯英文): %d" % len(no_cjk_susp)
    yield "C. 空值但源有内容: %d" % len(empty_but_src)
    yield "D. 格式token失配: %d" % len(fmt_bad)
    yield "E. 术语不一致(源含英文定名词但译文缺对应中文): %d" % len(term_bad)
    yield ""
    yield "## A. 完全未译 (zh 与 en 完全相同，应翻译却被保留)"
    for b,k,ov in exact_untrans:
        yield "  [%s] %s" % (b,k)
        yield "      en=zh: %s" % ov[:120]
    yield ""
    yield "## B. 疑似漏翻 (去掉格式token后无中文，剩英文实词) — 需人工确认"
    for b,k,rem,real in no_cjk_susp:
        yield "  [%s] %s  <- 残留英文: %s" % (b,k,', '.join(real))
        yield "      内容: %s" % rem[:120]
    yield ""
    yield "## C. 空值但源有内容 (可能漏翻，部分KEY本就该空)"
    for b,k,ov in empty_but_src:
        yield "  [%s] %s" % (b,k)
        yield "      源: %s" % ov[:120]
    yield ""
    yield "## D. 格式token失配 (原/译 数量不一致，会导致游戏解析异常)"
    for b,k,o,s in fmt_bad:
        yield "  [%s] %s  orig%s sch%s" % (b,k,o,s)
    yield ""
    yield "## E. 术语不一致 (源含定名英文词，但译文未出现对应定名中文)"
    for b,k,en,cz,sv in term_bad:
        yield "  [%s] %s  (英:%s 应译:%s)" % (b,k,en,cz)
        yield "      译: %s" % (sv or '')[:120]

out='\n'.join(lines())
open('proofread_findings.txt','w',encoding='utf-8').write(out+'\n')
print("写报告 proofread_findings.txt")
print("A完全未译:%d  B漏翻嫌疑:%d  C空值:%d  D格式:%d  E术语:%d" %
      (len(exact_untrans),len(no_cjk_susp),len(empty_but_src),len(fmt_bad),len(term_bad)))
