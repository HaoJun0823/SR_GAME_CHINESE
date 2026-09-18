# -*- coding: utf-8 -*-
# 仅对 schinese/ 中明确错译的 8 组术语做对齐修正（不动 迪克帮/摔角手/藏身处）
import re, os, glob

SCH='schinese'; SUF='_us.txt'
# 顺序很重要：先整体（长）后局部（短）
RULES=[
 ('吉尼斯碗第七届','第七届元气杯'),   # 先重排 VII
 ('吉尼斯碗','元气杯'),
 ('悲伤熊猫·飞天焰','忧郁熊猫飞天'),  # 先整体
 ('悲伤熊猫','忧郁熊猫'),             # 再独立
 ('害虫终结者','鼠辈终结者'),
 ('廉价哥','抠抠'),
 ('金钱射击','一击命中'),
 ('谋杀乱斗','屠杀格斗'),
 ('杀戮乱斗','屠杀格斗'),
 ('太空匪帮','太空黑帮'),
 ('元气教授','元气博士'),
]
LINE=re.compile(r'^(\s*"[^"]+"\s*:\s*")(.*)("\s*)$')

files=sorted(glob.glob(os.path.join(SCH,'*'+SUF)))
total_changes=0
for f in files:
    raw=open(f,encoding='utf-8').read()
    out_lines=[]
    for line in raw.split('\n'):
        m=LINE.match(line)
        if m:
            pre,val,suf=m.group(1),m.group(2),m.group(3)
            new=val
            for wrong,correct in RULES:
                if wrong in new:
                    new=new.replace(wrong,correct)
                    total_changes+= (val.count(wrong))  # 计数（仅统计一次/规则）
            out_lines.append(pre+new+suf)
        else:
            out_lines.append(line)
    new_raw='\n'.join(out_lines)
    # 保留原文件结尾换行特征
    if raw.endswith('\n'): new_raw+='\n'
    open(f,'w',encoding='utf-8').write(new_raw)
print(" applied, value-substitutions counted:", total_changes)
