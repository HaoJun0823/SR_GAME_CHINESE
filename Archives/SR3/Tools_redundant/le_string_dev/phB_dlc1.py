# -*- coding: utf-8 -*-
# Phase B: dlc1 未译占位补译（术语与已译区一致）
import re, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCH = os.path.join(BASE, "schinese", "dlc1_us.txt")
T = {
 "UNL_DESC_DLC1_STORY02_VEHICLE_YRN02": "这辆载具现已可在你的据点车库取用",
 "DLC1_ACT_PANDA_BLAZING_START": "开始忧郁熊猫飞天",
 "DLC1_GB_IN_ZACH_07": "对了博比，还有个大彩蛋——我们刚收到消息，第三街圣徒的老大今天也要来参加庆典。你觉得他们能续写连胜吗？",
 "DLC1_ACT_GENKI_ESCORT_COMPLETE_MSG": "[format][color:green]元气博士[/format] 很满意",
 "DLC1_ACT_BALL_MAYHEM_START": "开始性感小猫·毛线狂潮",
 "UNL_DESC_DLC1_STORY02_HOMIE_YARNIE": "在你手机的“电话”菜单中可以找到亚妮（毛线球）。他会在战斗中把毛线球送到你身边。",
 "DLC1_ACT_GENKI_RUN_NAME": "末日元气",
 "DLC1_ACT_GENKI_ESCORT_DRIFT_MSG": "[format][color:green]元气博士[/format]命令你用 {HANDBRAKE_IMG} 漂移 %d 秒",
 "DLC1_ACT_GENKI_ESCORT_NEAR_MISS_MSG": "[format][color:green]元气博士[/format]命令你连续完成 %d 次惊险擦身",
 "CUST_ITEM_CM_DLC_MASK_PANDA": "熊猫面具",
 "HOMIES_TIGER_DESC": "和愤怒老虎一起大杀四方。",
 "UNL_DESC_DLC1_STORY02_OUTFIT_PANDA": "熊猫装扮现已可以穿戴",
 "DLC1_ACT_GENKI_ESCORT_BURN_PEDS_MSG": "[format][color:green]元气博士[/format]命令你用火焰喷射器烤熟 %d 个人——你可能得先碾几个行人给喷火器充能",
 "DLC1_TUT_ACT_INTRO_BALL_MAYHEM": "亚妮！用巨型毛线球碾碎你的敌人！攒连击即可解锁冲击波武器！",
 "DLC1_ACT_GENKI_ESCORT_CRASH_MSG": "[format][color:green]元气博士[/format]命令你用载具对其他车辆造成 %d 点伤害",
 "CUST_ITEM_CM_DLC_SUIT_PANDA": "熊猫套装",
 "DLC1_ACT_GENKI_RUN_START": "开始末日元气",
 "UNL_DLC1_STORY02_HOMIE_YARNIE": "兄弟 - 亚妮投送",
 "DLC1_ACT_GENKI_ESCORT_START": "开始超级伦理公关机会",
 "DLC1_ACT_GENKI_ESCORT_FAIL_ANNOYED": "[format][color:green]元气博士[/format]被粉丝惹恼了，走小巷甩掉他们",
 "DLC1_ACT_GENKI_ESCORT_ANNOYANCE": "烦躁值",
 "DLC1_TUT_ACT_INTRO_GENKI_ESCORT": "用谋杀给元气博士的公开亮相攒劲——开车碾过行人给车子的火焰喷射器充能，助他更来劲！",
 "DLC1_TUT_ACT_INTRO_PANDA_BLAZING": "穿过圆环、在指定屋顶干掉吉祥物换取现金。击破气球可获得额外升力与“伦理高潮”。在最终降落区着陆，收获极致快感。",
 "DLC1_ACT_GENKI_ESCORT_ESCORT_GENKI": "[format][color:green]元气博士[/format]对谋杀很满意，但被狂热的粉丝惹恼了",
 "UNL_DESC_DLC1_STORY02_CLOTHING_KITTEN": "性感小猫面具现已可以穿戴",
 "UNL_DESC_DLC1_ASSET07_HOMIES01": "在你手机的“电话”菜单中可以找到抠抠。他会在战斗中帮助圣徒。",
 "CUST_ITEM_CM_MASK_ANGRYTIGER": "愤怒老虎面具",
 "UNL_DESC_DLC1_STORY02_HOMIE_KITTEN": "在你手机的“电话”菜单中可以找到性感小猫。她会在战斗中帮助圣徒。",
 "DLC1_ACT_BALL_MAYHEM_NAME": "性感小猫·毛线狂潮",
 "DLC1_ACT_GENKI_ESCORT_FAN_SPAWNED": "躲开那些狂热粉丝",
 "HOMIES_TIGER": "愤怒老虎",
 "UNL_DLC1_STORY02_OUTFIT_PANDA": "装扮 - 熊猫",
 "UNL_DLC1_STORY02_CLOTHING_TIGER": "道具 - 愤怒老虎面具",
 "UNL_DLC1_STORY02_HOMIE_TIGER": "兄弟 - 愤怒老虎",
 "UNL_DLC1_STORY02_VEHICLE_YRN02": "载具 - 元气皮艇",
 "UNL_DESC_DLC1_STORY02_HOMIE_PANDA": "在你手机的“电话”菜单中可以找到忧郁熊猫。她会在战斗中帮助圣徒。",
 "DLC1_PANDABLAZING_MESSAGE_STOPPED": "在倒计时结束前回到空中。",
 "DLC1_ACT_GENKI_ESCORT_NAME": "超级伦理公关机会",
 "DLC1_ACT_GENKI_ESCORT_KILL_VEHICLES_MSG": "[format][color:green]元气博士[/format]命令你消灭那些低级的[format][color:red]吉祥物[/format]",
 "DLC1_ACHIEVE_SR3_GENKI_BOWL_CHAMP": "元气杯冠军",
 "HOMIES_TAMMY_DESC": "和钢埠最爱的资讯美女一起挖独家猛料。",
 "UNL_DLC1_STORY02_HOMIE_PANDA": "兄弟 - 忧郁熊猫",
 "HOMIES_PANDA": "忧郁熊猫",
 "CUST_ITEM_CM_DLC_OUTFIT_PANDA": "熊猫装扮",
 "UNL_DESC_VEH_MOUSE_ATV_ALERT": "快速沙滩车",
 "DLC1_GB_IN_ZACH_01": "各位体育迷们大家好，欢迎来到华雷斯城外最疯狂、最野性、最血腥的狂欢盛宴——第七届元气杯！我是扎克，这位是我的黄金搭档博比，大家都很熟了吧！",
 "HOMIES_TAMMY": "塔米·托利弗",
 "UNL_DESC_DLC1_STORY02_CLOTHING_TIGER": "愤怒老虎面具现已可以穿戴",
 "DLC1_SHARK_ATTACK_KILL": "鲨鱼击杀",
 "DLC1_PANDABLAZING_FAILURE_SCORE": "穿过圆环、干掉吉祥物，赚取更多现金。",
 "HOMIES_KITTEN": "性感小猫",
 "UNL_DESC_DLC1_STORY02_VEHICLE_YRN03": "这辆载具现已可在你的据点车库取用",
 "UNL_DESC_DLC1_STORY02_VEHICLE_ATV": "这辆载具现已可在你的据点车库取用",
 "DLC1_ACT_GENKI_ESCORT_ANNOY_FAIL_WARNING": "离那些狂热粉丝远点，[format][color:green]元气博士[/format]正越来越不耐烦",
 "CUST_ITEM_MASK_SEXYKITTEN": "性感小猫面具",
 "DLC1_ACT_GENKI_ESCORT_ANNOY_WARNING": "如果粉丝离[format][color:green]元气博士[/format]太近，快感槽就会停止上涨",
 "UNL_DESC_DLC1_STORY02_HOMIE_TAMI": "在你手机的“电话”菜单中可以找到塔米·托利弗。她会在战斗中帮助圣徒。",
 "UNL_DLC1_STORY02_HOMIE_TAMI": "兄弟 - 塔米·托利弗",
 "UNL_DESC_DLC1_STORY02_ALERT": "你的手机里已解锁第七届元气杯的新任务。",
 "DLC1_ACT_GENKI_ESCORT_KILL_MSG": "[format][color:green]元气博士[/format]命令你消灭[format][color:red]目标[/format]",
 "DLC1_ACT_GENKI_ESCORT_CLIENT_MSG": "[format][color:green]元气博士[/format]命令你把他接上车",
 "DLC1_ACT_GENKI_ESCORT_SATISFIED_MSG": "[format][color:green]元气博士[/format]命令你送他去出席活动",
 "DLC1_ACHIEVE_SR3_COOKED_TO_PERFECTION": "火候完美",
 "DLC1_ACT_PANDA_BLAZING_NAME": "忧郁熊猫飞天",
 "DLC1_PANDABLAZING_FAILURE_STOPPED": "在倒计时结束前回到空中。",
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
