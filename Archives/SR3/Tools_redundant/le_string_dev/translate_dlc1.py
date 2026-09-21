# -*- coding: utf-8 -*-
import re, io, sys

SRC = "original/dlc1_us.txt"
OUT = "schinese/dlc1_us.txt"

def find_val(lines, key):
    for ln in lines:
        m = re.match(r'^"([^"]+)":\s*"(.*)"\s*$', ln)
        if m and m.group(1) == key:
            return m.group(2)
    return None

# 逐行读取源，抽取 KEY（防止拼写错）
raw = io.open(SRC, encoding="utf-8").read().replace("\r\n", "\n")
src_lines = [l for l in raw.split("\n") if l.strip() != ""]

# 中文译文表（KEY 取自源，VALUE 内字面 \n 必须写作 \\n；\" 用全角引号等价）
T = {
    "DLC1_MM_01": "末日元气",
    "UNL_DLC1_VEH_MOUSE_ATV_ALERT": "害虫终结者",
    "UNL_DLC1_STORY02_VEHICLE_YRN_ALL": "载具 - 性感小猫座驾",
    "UNL_SKYBLAZING": "悲伤熊猫·飞天焰",
    "UNL_DESC_DLC1_VALVE_ALERT": "你的衣橱现已上架《团队要塞2》特别内容。",
    "DLC1_GB_OUT_PLAYER_11": "你们得追随自己的梦想，孩子们。因为你要是不追，知道会变成什么样吗？你会变成你爸妈那样。那得多丧啊？好好想想吧。",
    "UNL_DESC_APOCALYPSE": "你已解锁更多「末日元气」活动内容。\\n请查看地图确认地点。",
    "DLC1_MM_06": "吉尼斯碗闭幕式",
    "DLC1_GB_OUT_ZACH_04": "我记得那事。嘿博比——咱们去采访一下可爱的塔米·托利弗吧，她正陪着我们的新科冠军在那儿候着呢。塔米？",
    "DLC1_GB_IN_BOBBY_03": "最恶心的那种，扎克。今年也不会例外。谋杀、混乱、还有乐子，全都是吉尼斯碗不可或缺的一部分。这位天才猫人又炮制出了一串前所未见的刺激项目，保证比咱们见过的任何玩意儿都更致命！",
    "cm_valve_pyro": "TF2 烈焰兵面具",
    "UNL_DLC1_STORY02_ALERT": "吉尼斯碗第七届",
    "DLC1_GB_OUT_TAMMY_10": "这，呃，挺有启发的，不过——",
    "HOMIES_YARNBALL_DESC": "呼叫亚妮（毛线球）前来支援。",
    "HOMIES_CHEAPY_DESC": "既省你的钱，又救你的命！",
    "cm_valve_demo": "TF2 爆破手面具  ",
    "DLC1_ACHIEVE_SR3_MURDER_IN_THE_JUNGLE": "丛林杀戮",
    "DLC1_ACHIEVE_SR3_STICK_THE_LANDING": "稳稳落地",
    "UNL_DESC_DLC1_STORY02_VEHICLE_GENKI": "该载具现已加入你的巢穴车库",
    "DLC1_GB_OUT_BOBBY_01": "你敢信吗？",
    "UNL_DLC1_STORY02_HOMIE_KITTEN": "兄弟 - 性感小猫",
    "cm_valve_sniper": "TF2 狙击手面具",
    "DLC1_ACHIEVE_SR3_GET_OFF_MY_BACK": "别烦我",
    "UNL_DLC1_STORY02_VEHICLE_ATV": "载具 - 害虫终结者",
    "cm_valve_engineer": "TF2 工程师面具",
    "HOMIES_KITTEN_DESC": "和性感小猫一起找乐子。",
    "UNL_DLC1_STORY02_VEHICLE_GENKI": "载具 - 元气战车",
    "DLC1_ACT_GENKI_ESCORT_FLAMETHROWER_READY": "按住 %ls 启动火焰喷射器",
    "UNL_DLC1_ASSET07_HOMIES01": "兄弟 - 廉价哥",
    "UNL_DESC_DLC1_ASSET07_ALERT": "你的电话簿中已有新的可下载内容。",
    "cm_valve_medic": "TF2 医疗兵面具",
    "DLC1_GB_OUT_TAMMY_05": "谢了扎克。",
    "DLC1_GB_OUT_BOBBY_03": "不可思议！那绝对是——毫无争议——自从乒乓球波莉在 Safeword 周四夜场打工以来，我最惊艳的个人秀了！",
    "DLC1_GB_IN_BOBBY_08": "马上就知道。比赛要开始了，咱们下去吧。我说下去，是真正的下去——直捣现场！",
    "DLC1_TUT_TITLE_ACT_BALL_MAYHEM_SHOCKWAVE": "毛线球冲击波",
    "UNL_DLC1_VALVE_ALERT": "团队要塞2 面具",
    "UNL_DESC_SKYBLAZING": "你已解锁更多「悲伤熊猫·飞天焰」活动内容。\\n请查看地图确认地点。",
    "DLC1_MM_03": "性感小猫·毛线狂潮",
    "DLC1_TUT_ACT_BALL_MAYHEM_SHOCKWAVE": "使用 {TANK_FIRE_SECONDARY_IMG} 触发冲击波武器！",
    "HOMIES_PANDA_DESC": "搞点破坏，逗悲伤熊猫开心。",
    "UNL_DESC_PR": "你已解锁更多「超级伦理公关机会」活动内容。\\n请查看地图确认地点。",
    "UNL_PR": "超级伦理公关机会",
    "UNL_APOCALYPSE": "末日元气",
    "DLC1_GB_IN_ZACH_02": "博比——往届的吉尼斯碗可从来不缺惊喜！",
    "cm_valve_scout": "TF2 侦察兵面具",
    "DLC1_MM_04": "超级伦理公关机会",
    "DLC1_ACHIEVE_SR3_C-C-C-COMBO_BREAKER": "连-连-连击中断",
    "HOMIES_YARNBALL": "亚妮送达",
    "DLC1_ACHIEVE_SR3_CAT_ON_A_HOT_TIN_ROOF": "热铁皮屋顶上的猫",
    "UNL_DESC_DLC1_STORY02_HOMIE_TIGER": "怒虎可在你的手机“PHONE”菜单中找到。她将在战斗中协助圣徒。",
    "UNL_DESC_SKYBLAZING_ALL": "元气教授很满意 ",
    "UNL_GESCORT_02": "继续征战",
    "DLC1_GB_OUT_PLAYER_09": "因为我花了好些年磨炼我的手艺。杀人、抢劫、施暴、再杀、轻度叛国、接着再杀——全是为了等我有出头之日时，能做好准备。",
    "DLC1_GB_OUT_TAMMY_06": "你知道的——这些年来，怀疑你的大有人在。有人说你不过是踩在更厉害的人肩上当顺风车。这对你来说算是正名吗？",
    "cm_valve_soldier": "TF2 士兵面具",
    "UNL_BALLMAYHEM": "性感小猫·毛线狂潮",
    "UNL_SKYBLAZING_ALL": "吉尼斯碗第七届 冠军",
    "cm_valve_heavy": "TF2 重装兵面具",
    "UNL_DESC_DLC1_STORY02_VEHICLE_YRN_ALL": "三辆新载具现已加入你的巢穴车库",
    "DLC1_ACHIEVE_SR3_FEEDING_TIME": "喂食时间",
    "DLC1_ACHIEVE_SR3_FLAME_ON": "燃起来",
    "UNL_DLC1_STORY02_VEHICLE_YRN01": "载具 - 元气太阳能车",
    "DLC1_GB_OUT_TAMMY_08": "这结尾多棒啊！那么扎克，我——",
    "DLC1_GB_OUT_PLAYER_07": "当然。不只是为我，也为所有在家里看电视、觉得自己注定一事无成的孩子们。是啊，我只想告诉那些孩子——无论如何，努力拼搏，一切皆有可能。",
    "DLC1_GB_OUT_ZACH_02": "你看到了吗？天哪——这是载入史册的表演！我从没见过这么惊人的场面！",
    "DLC1_GB_IN_BOBBY_05": "这一点绝对毋庸置疑。",
    "DLC1_GB_IN_ZACH_06": "哇——我口水都要流下来了。天哪！",
    "UNL_DLC1_ASSET07_ALERT": "廉价哥组合包",
    "UNL_DESC_DLC1_VALVE_CLOTHING": "用这套面具彰显你对 TF2 的热爱！",
    "UNL_DLC1_VALVE_CLOTHING": "道具 - TF2 面具",
    "REMOTE_PLAYER_NOT_LICENSED": "注意",
    "DLC1_MM_05": "悲伤熊猫·飞天焰",
    "HOMIES_CHEAPY": "廉价哥",
    "UNL_DLC1_STORY02_VEHICLE_YRN03": "载具 - 元气罪犯车",
    "DLC1_MM_02": "超级伦理公关机会",
    "DLC1_ACHIEVE_SR3_STORM_THE_YARN": "猛攻毛线",
    "UNL_DESC_DLC1_STORY02_VEHICLE_YRN01": "该载具现已加入你的巢穴车库",
    "cm_valve_spy": "TF2 间谍面具",
    "DLC1_GB_IN_ZACH_04": "比第四届吉尼斯碗的灰熊牛仔竞技还离谱？",
    "UNL_DESC_GESCORT_02": "完成剩余的「吉尼斯碗第七届」活动即可加冕冠军。\\n请查看地图确认地点。",
    "REMOTE_PLAYER_NOT_LICENSED_MSG": "你的合作搭档未拥有运行该活动所需的可下载内容。双方都必须拥有该可下载内容，才能进行合作游玩。",
    "DLC1_ACT_GENKI_ESCORT_FLAMETHROWER_READY_CORRECT": "按住 %ls 启动火焰喷射器",
    "UNL_DLC1_STORY02_CLOTHING_KITTEN": "道具 - 性感小猫面具",
    "UNL_DESC_BALLMAYHEM": "你已解锁更多「性感小猫·毛线狂潮」活动内容。\\n请查看地图确认地点。",
}

# 按源行序输出
seen = set()
out_lines = []
missing = []
for ln in src_lines:
    m = re.match(r'^"([^"]+)":\s*"(.*)"\s*$', ln)
    if not m:
        continue
    k = m.group(1)
    if k in seen:
        # 重复 KEY：再取一次译文
        out_lines.append('"%s": "%s"' % (k, T.get(k, "")))
        continue
    seen.add(k)
    if k not in T:
        missing.append(k)
        continue
    out_lines.append('"%s": "%s"' % (k, T.get(k, "")))

if missing:
    sys.stderr.write("MISSING KEYS: " + ", ".join(missing) + "\n")
    sys.exit(1)

io.open(OUT, "w", encoding="utf-8").write("\n".join(out_lines) + "\n")
sys.stderr.write("written %d lines\n" % len(out_lines))
