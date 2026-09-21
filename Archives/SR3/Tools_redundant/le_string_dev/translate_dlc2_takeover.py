# -*- coding: utf-8 -*-
# 接管重译 dlc2：读 original/dlc2_us.txt 抽 KEY，逐条重写中文。
import re, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCH = os.path.join(BASE, "schinese", "dlc2_us.txt")
ORG = os.path.join(BASE, "original", "dlc2_us.txt")

src = open(ORG, encoding="utf-8").read().replace("\r\n", "\n")
od = dict(re.findall(r'^\s*"([^"]+)":\s*"(.*)"\s*$', src, re.M))

T = {
 "UNL_DLC2_VEHICLE_ALIEN_SHIP": "载具 - AEGEAN",
 "UNL_DLC2_STORY01_ALERT": "太空黑帮",
 "CUST_ITEM_ZOMBIE_HAT_M_MASK": "丧尸面具 2",
 "DLC2_M03_GOTO_SECOND_TRANSMITTER": "前往[format][color:location]第二发射器[/format]",
 "DLC2_M03_GOTO_FIRST_TRANSMITTER": "前往[format][color:location]第一发射器[/format]",
 "DLC2_M02_DESTROY_CONSOLE": "摧毁[format][color:kill]控制台[/format]",
 "CUST_ITEM_LUSPEC_SUIT": "摔跤手帮特勤",
 "DLC2_M03_PERFORM_NEAR_CRASH": "完成一次惊险擦身",
 "DLC2_M03_UPLOAD_VIRUS": "在[format][color:protect]病毒[/format]上传期间进行防守",
 "DLC2_M01_OBJ_GO_TO_VEHICLE": "登上[format][color:use]载具[/format]",
 "UNL_DESC_DLC2_ASSET05_OUTFITS03": "现在你也能又毛又吓人——手套、面具、脚套随心混搭。",
 "CUST_ITEM_CM_DLC_SLASHER": "砍人魔",
 "CUST_ITEM_ZOMBIE_HAT_F_MASK": "丧尸面具 1",
 "DLC2_M03_FAIL_AIRCRAFT_DESTROYED": "你的飞行器被摧毁了！",
 "DLC2_M02_OBJ_MISSION_START": "前往[format][color:location]片场[/format]",
 "DLC2_M03_MISSION_NAME": "剧本里没这段！",
 "DLC2_M02_DESTORY_GENERATOR": "摧毁[format][color:kill]发电机[/format]",
 "DLC2_M02_EXPLOSION_WARNING": "退到安全距离外",
 "DLC2_M01_OBJ_JUMP": "跳跃",
 "DLC2_M01_OBJ_GO_TO_ARMORY": "前往[format][color:location]守军停机坪[/format]",
 "UNL_DLC2_ASSET05_CLOTHING": "物品 - 恐怖面具",
 "CUST_ITEM_DLC_SUIT_DKRSPCL": "赛博帮特勤",
 "CUST_ITEM_ZOMBIE_UBDY_M_SHIRT": "丧尸衬衫",
 "CUST_ITEM_DLC_HAT_SKELETON": "骷髅面具",
 "DLC2_M03_EXECUTE_VIRUS": "执行[format][color:kill]病毒[/format]",
 "DLC2_M03_DESTROY_SHIELDS": "摧毁[format][color:kill]发射器护盾[/format]",
 "DLC2_M02_PLACEHOLDER_DIALOG": "!对话占位符",
 "DLC2_M02_GO_TO_BACKSTAGE": "前往[format][color:location]后台[/format]",
 "DLC2_M01_HUD_LEAVE_HELI_AREA": "导演要你从北面进入该区域",
 "DLC2_M01_OBJ_DESTROY_PURSUERS": "消灭[format][color:kill]追兵[/format]",
 "UNL_DLC2_HOMIE_SPACE_BRUTINA": "兄弟 - 太空猛女",
 "UNL_DESC_DLC2_GANG_ALIENS": "现在你的帮派里也能招外星人了。",
 "DLC2_M02_BOARD_SAUCER": "登上[format][color:use]飞船[/format]",
 "DLC2_M02_PROGRESS_02": "改接线路中",
 "DLC2_M02_RETURN_TO_HOMIE": "回到奎拉娜身边",
 "DLC2_M01_OBJ_GO_TO_CRIB": "逃回[format][color:location]据点[/format]",
 "CUST_ITEM_MSFEM02_GLOVES": "晨星帮手套",
 "DLC2_M03_BARNSTORM_UNDER_BRIDGE": "从[format][color:location]大桥[/format]下飞过",
 "DLC2_M01_FAIL_ESCAPE_CAR_DESTROYED": "你的逃亡载具被摧毁了！",
 "DLC2_M01_NAME": "更快、更猛！",
 "CUST_ITEM_LUSPEC_OUTFIT": "摔跤手帮特勤",
 "DLC2_M02_GET_PISTOL": "拿到[format][color:use]激光手枪[/format]",
 "DLC2_M01_OBJ_DEFEND_BULLDOG": "保护[color:defend][format]载具[/format]",
 "CUST_ITEM_LUSPEC_MASK": "摔跤手帮特勤面具",
 "DLC2_M03_TUT_TITLE_NEAR_CRASH": "惊险擦身",
 "DLC2_M02_SEARCH_CODE": "寻找[format][color:use]密码[/format]",
 "DLC2_M02_PLAYER_DESTROY_MACHINE": "摧毁[format][color:kill]精神控制机[/format]",
 "DLC2_M01_HUD_JUMP": "跳跃",
 "DLC2_M03_TUT_TITLE_ALIEN_AIRCRAFT": "外星飞行器",
 "DLC2_M03_DESTROY_SHIELD": "摧毁[format][color:kill]发射器护盾[/format]",
 "DLC2_M02_GO_TO_SERVER": "前往[format][color:location]服务器[/format]",
 "DLC2_M02_MISSION_FAIL_HOMIE_DIED": "奎拉娜死了！",
 "DLC2_M01_PRE_MISSION_OBJ": "前往[format][color:location]摄影棚[/format]",
 "UNL_DESC_DLC2_ASSET05_OUTFITS01": "戴上这副面具、穿上这套行头，即刻加入不死一族。",
 "UNL_DESC_DLC2_ASSET06_VEHICLES": "元气少女载具。",
 "CUST_ITEM_MSFEM02_SUIT": "晨星帮士兵",
 "DLC2_M02_SELF_DESTRUCT": "自毁程序已启动",
 "DLC2_M01_FAIL_KWILANNA_DISMISSED": "奎拉娜离队了！",
 "DLC2_M01_OBJ_PREPARE_JUMP": "准备[color:location][format]跳跃[/format]",
 "DLC2_M01_OBJ_BREACH_HELIPORT": "攻入[format][color:location]守军停机坪[/format]",
 "UNL_DLC2_HOMIE_KWILANNA": "兄弟 - 珍妮",
 "CUST_ITEM_ZOMBIE_LBDY_M_PANTS": "丧尸长裤",
 "DLC2_M02_WAIT_FOR_HOMIE": "等待[format][color:location]奎拉娜[/format]",
 "DLC2_M02_PROGRESS_01": "破解中",
 "DLC2_M02_APPROACH_MACHINE_COOP": "前往[format][color:location]精神控制机[/format]",
 "DLC2_M02_MISSION_NAME": "18号半机库",
 "DLC2_M02_GET_FINAL_WEAPON": "拿到[format][color:use]激光加农炮[/format]",
 "DLC2_M02_GO_TO_CONSOLE": "前往[format][color:location]安保控制台[/format]",
 "DLC2_M02_GET_SMG": "拿到[format][color:use]自动激光枪[/format]",
 "DLC2_M01_OBJ_GO_TO_HELICOPTER": "登上[format][color:use]直升机[/format]",
 "UNL_DESC_DLC2_ASSET05_OUTFITS02": "多款面具加手套，凑齐一套完美的恐怖片反派行头。",
 "DLC2_M02_GO_TO_HANGER": "前往[format][color:location]机库[/format]",
 "DLC2_M02_PROTECT_HOMIE": "保护[format][color:protect]奎拉娜[/format]",
 "DLC2_M01_HUD_UNTIE_ALIEN": "给外星人松绑",
 "UNL_DLC2_ASSET05_OUTFITS01": "服装 - 食人丧尸",
 "CUST_ITEM_DLC_HAT_HOCKEY": "曲棍球面具",
 "DLC2_M03_TUT_NEAR_CRASH": "贴着建筑物飞行，即可完成一次惊险擦身。",
 "DLC2_M02_USE_REWIRE": "改接线路",
 "CUST_ITEM_DLC_OUTFIT_WEREWOLF": "狼人",
 "DLC2_M03_GOTO_THIRD_TRANSMITTER": "前往[format][color:location]第三发射器[/format]",
 "DLC2_M01_FAIL_KWILANNA_DIED": "奎拉娜死了！",
 "DLC2_M02_SECURITY_ACTIVATING": "安保系统启动中",
 "DLC2_M01_OBJ_KILL_NAT_GUARD": "消灭[format][color:kill]钢埠守军[/format]",
 "DLC2_INTRO_PC_10": "不，没事，我就是……我只是他妈的超需要提词卡而已。",
 "DLC2_M03_BARNSTORM_THROUGH_BRIDGE": "从[format][color:location]大桥[/format]中穿过",
 "DLC2_M03_PRE_CALL_OBJ": "前往[format][color:location]电影片场[/format]",
 "DLC2_M02_GO_TO_CONTROL_ROOM": "前往[format][color:location]控制室[/format]",
 "DLC2_M02_GO_TO_SERVER_ROOM": "前往[format][color:location]服务器机房[/format]",
 "DLC2_M02_REWIRE_EXPLOSIVES": "改接[format][color:use]炸药[/format]线路",
 "DLC2_M01_FAIL_DIRECTOR_UNHAPPY": "导演对这条不满意！",
 "DLC2_M02_PROGRESS_LABEL": "进度",
 "DLC2_M02_APPROACH_MACHINE_LOCAL": "前往[format][color:location]精神控制机[/format]",
 "DLC2_M01_OBJ_GO_TO_BULLDOG": "登上[format][color:use]载具[/format]",
 "CUST_ITEM_MSFEM02_EAR": "晨星帮耳环",
 "DLC2_M03_GET_TO_AIRCRAFT": "登上[format][color:use]外星飞行器[/format]",
 "DLC2_M01_OBJ_RESCUE_ALIEN": "营救[format][color:use]奎拉娜[/format]",
 "DLC2_M02_PROGRESS_03": "搜索中...",
 "DLC2_M02_GO_TO_SET": "前往[format][color:location]片场[/format]",
 "DLC2_M02_MISSION_FAIL_OUT_OF_TIME": "你的时间用完了！",
 "DLC2_M01_OBJ_ESCAPE_NG": "摆脱[format][color:location]钢埠守军[/format]",
}

lines = open(SCH, encoding="utf-8").read().replace("\r\n", "\n").split("\n")
out = []
seen = set()
for ln in lines:
    m = re.match(r'^\s*"([^"]+)":\s*"(.*)"\s*$', ln)
    if m and m.group(1) in T:
        k = m.group(1)
        out.append('"%s": "%s"' % (k, T[k]))
        seen.add(k)
    else:
        out.append(ln)
for k in T:
    if k not in seen:
        out.append('"%s": "%s"' % (k, T[k]))

open(SCH, "w", encoding="utf-8").write("\n".join(out) + "\n")
print("written", len(T), "entries; total lines", len(out))
