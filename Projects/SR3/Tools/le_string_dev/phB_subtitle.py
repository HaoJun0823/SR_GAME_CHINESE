# -*- coding: utf-8 -*-
# Phase B: subtitle 剩余36条未译占位补译
import re, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCH = os.path.join(BASE, "schinese", "subtitle_us.txt")
T = {
 "Story01_In_19_Josh": "操！！",
 "Story15_In_14_Matt": "多谢指点，特工肯辛顿。",
 "SH_Morningstar02_10_Player_BM": "那又怎样，我假扮成个古怪的百万富翁？",
 "Story07_In_05_Killbane": "该上高光桥段了，小子们。",
 "Story02_In_04_Shaundi": "我们被抓了。",
 "Story19_In_10_Player": "长官，我是您的铁杆粉丝。",
 "SH_Decker_12_Kinzie": "我需要那台椅子的力量才能制住赛博帮。",
 "Story07_In_12_Player_BM": "没有规则？那我可就来劲了。",
 "Story02_In_11_Phillipe": "把衣服脱了。",
 "Story01_In_17_Shaundi": "喂——别犯浑。",
 "Story17_Out_02_Pierce": "咱们是不是玩过头了。",
 "Story16_Out_05_Killbane": "我经营的公司确实要求严苛。马蒂，我很感激你把顾虑说出来。我是说，薇奥拉和琦琦处理她们……离职的方式，真让我想杀几个人泄愤，你懂吧？",
 "Story11_In_01_Player_BM": "收拾家伙，咱们得走了。",
 "Story10_In_07_Player_BM": "那你图什么？",
 "Story10_In_14_Oleg": "女士优先。",
 "Story10_In_03_Player_BM": "我可不想惹毛那个大块头。",
 "Story07_In_16_Oleg": "还有别人跟你一样恨辛迪加。我带你去见他们。",
 "Story02_In_21_Kiki": "当然，这是税前价。",
 "SH_Decker_13_Player_BM": "谢谢你。",
 "Story15_In_03_Pierce": "我可没看到床。",
 "Story08_Out_01_Killbane": "妓女刺客？！",
 "Story02_In_32_Shaundi": "强尼，你连手动挡都开不利索——还想开飞机？",
 "Story05_Out_03_Phillipe": "我正是这么想的，杀霸先生。",
 "Story01_In_15_Player_BM": "规矩你们都知道。",
 "Story10_In_04_Viola": "听我说——我不是来打架的。我们需要联手。",
 "SH_Morningstar02_02_Monica": "这次恐怖行动的元凶必将被绳之以法。",
 "Story12_In_13_Pierce": "你的名号叫血色修女长。",
 "Story01_In_11_Player_BM": "行了，各位——",
 "Story16_In_04_Player_BM": "计划不错，金吉。",
 "Story11_Out_02_Josh": "STAG 的男女将士每天都在拿命保卫你们的城市……",
 "Story08_Out_03_Matt": "两位女士，这计划真是烂透了。",
 "Story07_Out_07_Kiki": "冷静点，埃迪……",
 "Story11_In_02_Pierce": "我可是押了 2 万在这场比赛上！",
 "Story02_In_12_Player_BM": "你知道自己在跟谁叫板吗？",
 "Story01_In_08_Josh": "你们就穿着这身去抢银行？",
 "Story01_In_02_Gat": "重大盗窃罪算什么。你准备好了吗？",
}
lines = open(SCH, encoding="utf-8").read().replace("\r\n", "\n").split("\n")
out, seen = [], set()
for ln in lines:
    m = re.match(r'^\s*"([^"]+)":\s*"(.*)"\s*$', ln)
    if m and m.group(1) in T:
        k = m.group(1); out.append('"%s": "%s"' % (k, T[k])); seen.add(k)
    else:
        out.append(ln)
open(SCH, "w", encoding="utf-8").write("\n".join(out) + "\n")
print("written", len(T), "; lines", len(out))
