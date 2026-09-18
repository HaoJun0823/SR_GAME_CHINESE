# -*- coding: utf-8 -*-
import re, os, glob

SCH='schinese'; ORG='original'
SUF='_us.txt'

EXEEMPT=set(s.upper() for s in [
 'xbox','live','tf2','gps','ok','id','npc','vip','html','url','dvd','pc','ps3','ps4','ps5',
 'steam','sr3','stag','uk','us','eu','ascii','usb','hd','sd','wifi','blu','ray','led','cpu',
 'gpu','fps','ai','rpg','pda','ui','hud','vpk','vpp','xtbl','cfo','ceo','bst','genki','saints',
 'row','vtol','de','fr','es','it','jp','pl','ru','sk','nl','cz','zh','srtt','dlc1','dlc2','dlc3',
 'nuke','reactors','apocalypse','cd','dvd','tv','dj','mc','mr','ms','dr','st','sr','wtf','lol',
 'gta','fbi','cia','kgb','uss','usa','uk','eu','nasa','sex','xxx','url','http','www','com','net',
 'achievement','trophy','challenge','bonus','pack','set','mode','level','score','rank','xp','hp',
])

def parse(p):
    s=open(p,encoding='utf-8').read().replace('\r\n','\n')
    return [(m.group(1),m.group(2)) for m in re.finditer(r'^\s*"([^"]+)":\s*"(.*)"\s*$',s,re.M)]

def strip_format(v):
    v=re.sub(r'\[/?format\]','',v)
    v=re.sub(r'\[color:[^\]]*\]','',v)
    v=re.sub(r'\[image:[^\]]*\]','',v)
    v=re.sub(r'\{[^}]*\}','',v)
    v=re.sub(r'%[sdi f%]','',v)
    v=re.sub(r'\\n','',v)
    v=re.sub(r'\\"','',v)
    return v.replace('“','').replace('”','').strip()

def has_cjk(s): return bool(re.search(r'[\u4e00-\u9fff]',s))

files=sorted(glob.glob(os.path.join(SCH,'*'+SUF)))
data={}
for f in files:
    b=os.path.basename(f)[:-len(SUF)]
    op=os.path.join(ORG,b+SUF)
    if os.path.exists(op): data[b]=(parse(op),parse(f))

# A 分流
real_miss=[]; legit_kept=[]
for b,(ol,sl) in data.items():
    od=dict(ol); sd=dict(sl)
    for k in od:
        ov=od[k]; sv=sd.get(k,'')
        if sv.strip()==ov.strip() and re.search(r'[A-Za-z]{3,}',ov):
            rem=strip_format(sv)
            words=[w.upper() for w in re.findall(r'[A-Za-z]{2,}',rem)]
            real=[w for w in words if w not in EXEEMPT]
            if real: real_miss.append((b,k,ov,real))
            else: legit_kept.append((b,k,ov))

# B 漏翻嫌疑
no_cjk_susp=[]
for b,(ol,sl) in data.items():
    od=dict(ol); sd=dict(sl)
    for k in od:
        sv=sd.get(k,'')
        rem=strip_format(sv)
        if not has_cjk(rem):
            words=[w.upper() for w in re.findall(r'[A-Za-z]{2,}',rem)]
            real=[w for w in words if w not in EXEEMPT]
            if real: no_cjk_susp.append((b,k,rem,real))

# C 空值
empty_but_src=[]
for b,(ol,sl) in data.items():
    od=dict(ol); sd=dict(sl)
    for k in od:
        if od[k].strip()!='' and sd.get(k,'').strip()=='':
            empty_but_src.append((b,k,od[k]))

# D 格式token
def fmt_counts(v):
    return (v.count('[format]'),v.count('[/format]'),
            v.count('\\"')+v.count('“')+v.count('”'),
            len(re.findall(r'\{[^}]*\}',v)))
fmt_bad=[]
for b,(ol,sl) in data.items():
    od=dict(ol); sd=dict(sl)
    for k in od:
        if fmt_counts(od[k])!=fmt_counts(sd.get(k,'')):
            fmt_bad.append((b,k,fmt_counts(od[k]),fmt_counts(sd.get(k,''))))

# E 术语一致性（降噪：只在"已译条目(含CJK)且源含定名英文但译文缺定名中文"时报告）
glos=[]
tg=open('terms_cand.md',encoding='utf-8').read()
for m in re.finditer(r'^\|\s*(.+?)\s*\|\s*(.+?)\s*\|',tg,re.M):
    en=m.group(1).strip(); zh=m.group(2).strip()
    if not en or not zh: continue
    if en.startswith('#') or en in ('英文','---') or set(en)<=set('-'): continue
    glos.append((en,zh))
def clean_zh(z): return re.sub(r'[《》""\'\'（）()\[\]]','',z).strip()
term_bad=[]
for en,zh in glos:
    cz=clean_zh(zh)
    if len(cz)<2: continue
    if len(en)<4: continue
    en_l=en.lower()
    # 跳过过于通用的词
    if en_l in ('saints','row','saint','the','and','for','you','your','with','this','that','have','will','from','they','them','what','when','where','who','why','how','activity','upgrade'):
        continue
    for b,(ol,sl) in data.items():
        od=dict(ol); sd=dict(sl)
        for k in od:
            ov=od[k].lower()
            if en_l in ov:
                sv=sd.get(k,'')
                if has_cjk(sv) and cz and cz not in sv:
                    term_bad.append((b,k,en,cz,sv))

# 写报告
L=[]
L.append("="*72)
L.append("SR3 简体中文 schinese/ 校对报告（修正版）")
L.append("="*72)
L.append("")
L.append("文件数:%d  总条目:%d"%(len(data),sum(len(v[1]) for v in data.values())))
L.append("A. 完全未译(zh==en): 真实漏译 %d | 合法保留 %d"%(len(real_miss),len(legit_kept)))
L.append("B. 漏翻嫌疑(去格式后纯英文): %d"%len(no_cjk_susp))
L.append("C. 空值但源有内容: %d"%len(empty_but_src))
L.append("D. 格式token失配: %d"%len(fmt_bad))
L.append("E. 术语不一致(已译条目缺定名中文): %d"%len(term_bad))
L.append("")
L.append("## A-1 真实漏译（建议补译）")
for b,k,ov,real in real_miss:
    L.append("  [%s] %s"%(b,k))
    L.append("      英文: %s"%ov[:140])
L.append("")
L.append("## A-2 合法保留英文（品牌/代码/型号，通常无需改）")
for b,k,ov in legit_kept:
    L.append("  [%s] %s : %s"%(b,k,ov[:100]))
L.append("")
L.append("## B 漏翻嫌疑（需人工确认）")
for b,k,rem,real in no_cjk_susp:
    L.append("  [%s] %s  残留:%s"%(b,k,','.join(real)))
    L.append("      内容: %s"%rem[:140])
L.append("")
L.append("## C 空值但源有内容")
for b,k,ov in empty_but_src:
    L.append("  [%s] %s : %s"%(b,k,ov[:120]))
L.append("")
L.append("## D 格式token失配")
for b,k,o,s in fmt_bad:
    L.append("  [%s] %s  orig%s sch%s"%(b,k,o,s))
L.append("")
L.append("## E 术语不一致（已译条目中源含定名英文但译文未见定名中文，供复核）")
for b,k,en,cz,sv in term_bad:
    L.append("  [%s] %s  (英:%s 应译:%s)"%(b,k,en,cz))
    L.append("      译: %s"%sv[:120])

open('proofread_findings.txt','w',encoding='utf-8').write('\n'.join(L)+'\n')
print("A真实漏译:%d  A合法保留:%d  B:%d  C:%d  D:%d  E:%d"%
      (len(real_miss),len(legit_kept),len(no_cjk_susp),len(empty_but_src),len(fmt_bad),len(term_bad)))
