# -*- coding: utf-8 -*-
# Phase B: dlc3 未译占位补译（术语与已译区一致：托比特龙/约翰尼·塔格/吉米/怪奇科学/圣徒星球）
import re, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCH = os.path.join(BASE, "schinese", "dlc3_us.txt")
T = {
 "UNL_DESC_DLC3_STORY03_VEHICLES": "托比特龙现已可在你的据点车库取用",
 "UNL_DLC3_HOMIE_JOHNNY_TAG": "兄弟 - 约翰尼·塔格",
 "UNL_DLC3_STORY03_ALERT": "克隆危机……",
 "DLC3_M03_TUT_SUPER_POWERS_TITLE": "你获得了超能力！",
 "DLC3_M01_ABANDONED_STRIP_CLUB": "你得在合法地带里面等吉米",
 "DLC3_M01_INTERROGATE_OWNER": "审问[format][color:use]老板[/format]",
 "CUST_ITEM_DLC_UPP_WITCH": "女巫长袍",
 "CUST_ITEM_DLC_HAT_WITCH": "女巫帽",
 "DLC3_M03_MISSION_NAME": "派出克隆军团",
 "UNL_DLC3_CUST02_VEHICLES": "载具 - 塞勒姆",
 "DLC3_M02_FAIL_ABANDONED_TRUCK": "你抛弃了托比特龙！",
 "DLC3_M01_FAILURE_CAR_DESTROYED": "吉米的车被摧毁了",
 "DLC3_M01_MISSION_NAME": "怪奇科学",
 "UNL_DESC_DLC3_CUST02_OUTFITS01": "向全世界证明，当女巫不仅是种活法，更是种时尚宣言。",
 "DLC3_WPN_FIREBALL": "火球拳",
 "DLC3_ACHIEVE_SR3_BEE_HOLDER": "观蜂者之眼",
 "DLC3_M01_INTERROGATE_GANG": "审问[format][color:use]副官[/format]",
 "DLC3_WPN_SAINTSFLOWFIST": "圣徒冲击拳",
 "DLC3_M02_FAIL_TRUCK_DEAD": "托比特龙被摧毁了！",
 "DLC3_M02_PROTECT_PIERCE": "保护[format][color:defend]皮尔斯[/format]",
 "DLC3_M01_LOOK_FOR_TAG": "调查一家[format][color:location]友军火力[/format]",
 "DLC3_ACHIEVE_SR3_SUPAA_EXCELLENT": "超级棒！",
 "DLC3_M02_FIND_TAG": "找到[format][color:location]塔格[/format]",
 "DLC3_M01_GOTO_FRIENDLY_FIRE": "前往[format][color:location]友军火力[/format]",
 "DLC3_M02_REVIVE": "救起",
 "DLC3_M02_GOTO_PLANET_SAINTS": "前往[format][color:location]圣徒星球[/format]",
 "DLC3_M03_GOTO_HELICOPTER": "登上[format][color:location]直升机[/format]",
 "DLC3_M02_RETURN_TO_TRUCK": "回到[format][color:location]托比特龙[/format]上",
 "DLC3_M02_MISSION_NAME": "环法闹剧",
 "DLC3_ACHIEVE_SR3_STING_OPERATION": "蜇刺行动",
 "DLC3_M02_FAIL_FEEDBACK": "观众搅黄了这场演出。用蜂群炮把他们从舞台边赶开。",
 "DLC3_M01_SURVIVE": " [format][color:defend]等待[/format]吉米",
 "DLC3_M03_GOTO_APARTMENT": "到[format][color:location]公寓据点[/format]找塔格",
 "DLC3_M02_OBJ_MISSION_START": "前往[format][color:location]吉米的家[/format]",
 "DLC3_M01_DESTROY_ROADBLOCK": "摧毁[format][color:kill]路障[/format]",
 "DLC3_M01_PROTECT_CAR": "保护[format][color:defend]车辆[/format]",
 "DLC3_ACHIEVE_SR3_TOUR_DE_FARCE": "环法闹剧",
 "DLC3_WPN_BEE_GUN": "蜂群炮",
 "UNL_DESC_DLC3_CUST02_CLOTHING": "为你内心的忍者、热狗、天使与魔鬼准备的服装。",
 "DLC3_M02_OBJ_CALM_TAG": "用蜂群炮安抚[format][color:kill]塔格[/format]",
 "DLC3_M02_KILL_HELIS": "消灭[format][color:kill]钢埠守军袭击者[/format]",
 "DLC3_M01_GOTO_CAR": "登上[format][color:location]吉米的车[/format]",
 "DLC3_M03_TUT_SUPER_POWERS": "按住 {SPRINT_IMG} 进行超级冲刺。\\n用 {ATTACK_SECONDARY_IMG} 投掷火球。\\n按 {ATTACK_PRIMARY_IMG} 打出附带超能力的重拳。",
 "DLC3_M02_GOTO_TRUCK": "登上[format][color:location]托比特龙[/format]",
 "DLC3_M02_OBJ_CALM_LABEL": "进度",
 "DLC3_M02_FAIL_TAG_ESCAPE": "你动作太慢了，约翰尼·塔格逃走了！",
 "DLC3_M01_GOTO_STRIP_CLUB": "前往[format][color:location]合法地带[/format]",
 "DLC3_M01_OBJ_MISSION_START": "前往[format][color:location]笑面杰克的店[/format]",
 "DLC3_M02_OBJ_GET_BEE_GUN": "从卡车上拿取[format][color:location]蜂群炮[/format]",
 "DLC3_M02_REVIVE_TAG": "救起[format][color:use]塔格[/format]",
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
