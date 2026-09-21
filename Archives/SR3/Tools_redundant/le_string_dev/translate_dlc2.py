# -*- coding: utf-8 -*-
import re, io, sys

SRC = "original/dlc2_us.txt"
OUT = "schinese/dlc2_us.txt"

raw = io.open(SRC, encoding="utf-8").read().replace("\r\n", "\n")
src_lines = [l for l in raw.split("\n") if l.strip() != ""]

T = {
    "UNL_DESC_DLC2_ASSET03_OUTFITS03": "伪装成一名晨星帮士兵。",
    "DLC2_OUTRO_PC_09": "哇哦！",
    "HOMIE_DLC2_KWILANNA": "珍妮",
    "UNL_DESC_DLC2_VEHICLE_ALIEN_SHIP": "爱琴号飞船现已加入你的巢穴车库",
    "DLC2_ACHIEVE_SR3_XENAPHOBE": "厌外星者",
    "DLC2_OUTRO_PC_19": "那么，呃，看来这就杀青了。",
    "DLC2_WPN_AUTOLASER_DESCRIPT": "星际亚马逊老兵的速射武器",
    "CUST_ITEM_DLC_SUIT_SLASHER": "屠夫",
    "DLC2_OUTRO_KWILANNA_03": "该死的蠢货，蠢透了……",
    "DLC2_INTRO_PC_03": "我要宰了那个狗娘养的……混……",
    "CUST_GANG_ALIENS": "外星人",
    "CM_MSSPCL_EYES": "晨星帮专家眼镜",
    "UNL_DESC_DLC2_ASSET03_OUTFITS04": "伪装成一名晨星帮专家。",
    "DLC2_OUTRO_DIRECTOR_16": "你聋了吗，你到底在搞什么鬼？！",
    "DLC2_OUTRO_KWILANNA_13": "我不过是在照你教我的做。",
    "CUST_GANG_PENTHOUSE": "阁楼",
    "HOMIE_DLC2_SPACE_BRUTINA": "太空布鲁蒂娜",
    "DLC2_ACHIEVE_SR3_CAMERAMEN_DOWN": "工会克星",
    "UNL_DLC2_ITEM_MIND_CONTROL_HELMET": "道具 - 精神控制头盔",
    "DLC2_M03_GET_IN_RANGE": "进入[format][color:location]信号发射器[/format]的辐射范围",
    "DLC2_OUTRO_PC_10": "好了珍妮！珍妮，跟我说话啊！",
    "CUST_ITEM_DLC_FOOT_SLASHER": "屠夫靴",
    "DLC2_M03_TUT_ALIEN_AIRCRAFT": "{HELI_UP_IMG} 提升高度\\n{HELI_DOWN_IMG} 降低高度\\n{VTOL_TRANSITION_IMG} 切换飞行模式",
    "DLC2_M02_USE_MACHINE_TRG_MSG": "关闭",
    "CUST_ITEM_DLC_SHOES_WEREWOLF": "狼人足",
    "UNL_DESC_DLC2_VEHICLE_SPACE_JET_BIKE": "镰刀喷气摩托现已加入你的巢穴车库",
    "UNL_DLC2_GANG_ALIENS": "帮派自定义",
    "DLC2_ACHIEVE_SR3_SHOOT_THAT_GREEN_STUFF": "领航员复仇记",
    "DLC2_INTRO_KWILANNA_16": "不是啦……是我！",
    "DLC2_INTRO_DIRECTOR_13": "这跑龙套的是谁，凭什么跟我搭话？！",
    "HOMIE_DESC_DLC2_SPACE_BRUTINA": "和这位外星美人一起，打出重磅一击。",
    "CUST_ITEM_DLC_GLOVES_WEREWOLF": "狼人手套",
    "UNL_DESC_DLC2_ASSET05_CLOTHING": "为你的恐怖组合包行头追加更多面具。",
    "UNL_DLC2_ASSET05_ALERT": "恐怖组合包",
    "UNL_DESC_DLC2_ASSET06_ALERT": "你的巢穴衣橱与车库中已有新的可下载内容。",
    "DLC2_ACHIEVE_SR3_BLOWING_THE_BUDGET": "初次接触",
    "DLC2_OUTRO_PC_02": "暂且这样。",
    "DLC2_WPN_LASERCANNON_DESCRIPT": "专为精英星际亚马逊战士打造的步枪",
    "cm_dkr01_suit": "迪克帮士兵",
    "DLC2_ACHIEVE_SR3_MAN_POWER": "战士公主",
    "DLC2_OUTRO_PC_12": "好吧——不过这好像有点过火了。",
    "DLC2_INTRO_PC_08": "呃，我也不至于太激动。镜头前的事更该是肖迪或皮尔斯的活儿。这剧本看得我一头雾水。",
    "DLC2_INTRO_KWILANNA_04": "呃，打断一下？",
    "CUST_ITEM_ALIEN_HAT": "精神控制头盔",
    "cm_msspcl_suit": "晨星帮专家",
    "DLC2_INTRO_KWILANNA_14": "噢，甄先生，我演的是奎薇拉娜……",
    "DLC2_INTRO_DIRECTOR_11": "妙极了！这真是太写实主义了！你绝对能演活！",
    "DLC2_WPN_LASERCANNON": "激光炮",
    "DLC2_ACHIEVE_SR3_LIGHTS_CAMERA_ACTION": "灯光！镜头！开拍！",
    "UNL_DESC_DLC2_ASSET03_OUTFITS01": "用这套完整行头，伪装成杀霸麾下的一名摔角手士兵。",
    "DLC2_WPN_LASERPISTOL_DESCRIPT": "星际亚马逊士兵的制式副武器",
    "CM_DKR02_OUTFIT": "迪克帮专家",
    "cm_dkr01_hdphones": "迪克帮耳机  ",
    "UNL_DLC2_ASSET05_OUTFITS02": "行头 - 屠夫",
    "DLC2_ACHIEVE_SR3_PEW_PEW_PEW": "噼！噼！噼！",
    "DLC2_ACHIEVE_SR3_NO_STUNTMAN_REQUIRED": "特技我自己来",
    "DLC2_OUTRO_KWILANNA_06": "我要宰了那个狗娘养的！",
    "DLC2_INTRO_PC_06": "那是好事还是坏事？",
    "DLC2_INTRO_PC_01": "我要宰了那个狗娘养的……",
    "UNL_DLC2_ASSET06_VEHICLES": "载具 - 元气少女载具",
    "DLC2_M03_GOTO_VIRUS_LOCATION": "前往[format][color:location]执行地点[/format]",
    "UNL_DESC_DLC2_ASSET03_OUTFITS06": "伪装成马特·米勒旗下的一名迪克帮专家。",
    "DLC2_OUTRO_KWILANNA_11": "我累了。我受够了他想杀我们，受够了他那副刻薄样，也受够了他那条蠢透了的围巾！",
    "DLC2_INTRO_DIRECTOR_18": "瞧！这位无名小卒可压不垮你，我保证。我不会让她得逞的！行吗？你只管做你自己，你绝对能演活。",
    "UNL_DESC_DLC2_GANG_PENTHOUSE": "你的帮派现在可以拥有阁楼女郎了。",
    "UNL_DLC2_GANG_PENTHOUSE": "帮派自定义",
    "CM_DKR01_OUTFIT": "迪克帮士兵",
    "DLC2_INTRO_KWILANNA_07": "天哪你是在逗我吗？我的处女作，竟然是和圣徒老大的对手戏！",
    "DLC2_M02_APPROACH_SAUCER": "抵达[format][color:location]飞船[/format]",
    "DLC2_M02_KILL_BRUTINA": "击杀外星蛮兵",
    "DLC2_WPN_AUTOLASER": "自动激光枪",
    "UNL_DESC_DLC2_HOMIE_KWILANNA": "珍妮可在你的手机“PHONE”菜单中找到。她将在战斗中协助圣徒。",
    "DLC2_OUTRO_PC_01": "地球安全了……",
    "DLC2_INTRO_DIRECTOR_15": "真的？我还以为咱们要给奎薇拉娜起个名呢。",
    "CUST_ITEM_DLC_HAT_DEVIL": "恶魔面具",
    "DLC2_M03_ESCAPE": "[format][color:location]逃离[/format]信号半径",
    "HOMIE_DESC_DLC2_KWILANNA": "和一位星际狠角色并肩作战。",
    "DLC2_OUTRO_DIRECTOR_07": "那不是你的台词！噢见鬼你到底有没有背过词？",
    "DLC2_INTRO_PC_02": "我要宰了那个狗娘养的……",
    "DLC2_M03_KILL_NATIONAL_GUARD": "摧毁[format][color:kill]钢埠警卫陷阱[/format]",
    "UNL_DESC_DLC2_ASSET03_OUTFITS05": "伪装成马特·米勒旗下的一名迪克帮士兵。",
    "UNL_DESC_DLC2_CUST01_ALERT": "你的巢穴中已有新的可下载内容。",
    "CUST_ITEM_MSFEM02_OUTFIT": "晨星帮士兵",
    "UNL_DESC_DLC2_ITEM_MIND_CONTROL_HELMET": "精神控制头盔已加入你的巢穴衣橱",
    "UNL_DESC_DLC2_ASSET03_OUTFITS02": "用这套完整行头，伪装成杀霸麾下的一名摔角手专家。",
    "DLC2_OUTRO_PC_08": "嘿珍妮，你又开始眼神发飘了。",
    "UNL_DLC2_CUST01_ALERT": "阁楼组合包",
    "CM_MSSPCL_OUTFIT": "晨星帮专家",
    "UNL_DESC_DLC2_HOMIE_SPACE_BRUTINA": "太空布鲁蒂娜可在你的手机“PHONE”菜单中找到。她将在战斗中协助圣徒。",
    "DLC2_M02_MISSION_FAIL_DIDNT_PROTECT_HOMIE": "你没能保护好奎薇拉娜！",
    "DLC2_OUTRO_PC_18": "哇。",
    "DLC2_WPN_LASERPISTOL": "激光手枪",
    "DLC2_OUTRO_DIRECTOR_05": "上面到底在搞什么名堂？珍妮，念你的台词啊！",
    "DLC2_INTRO_DIRECTOR_09": "剧本有问题？说一声就行，宝贝，咱们直接丢一边！",
    "CUST_ITEM_CM_DLC_ZOMBIE1": "食肉丧尸",
    "DLC2_ACHIEVE_SR3_MDK3000": "来个桶滚！",
    "DLC2_OUTRO_DIRECTOR_15": "你刚才那条已经废了，重来一遍！",
    "DLC2_INTRO_DIRECTOR_19": "好！开拍，把这镜头拿下！",
    "DLC2_ACHIEVE_SR3_A_LISTER": "三流名人",
    "DLC2_OUTRO_KWILANNA_17": "我们演得真带劲。",
    "UNL_DLC2_ASSET05_OUTFITS03": "行头 - 狼人",
    "UNL_DESC_DLC2_ASSET05_ALERT": "你的巢穴衣橱与车库中已有新的可下载内容。",
    "UNL_DLC2_ASSET06_ALERT": "元气少女组合包",
    "DLC2_M01_OBJ_LAND_TARGET": "降落在[format][color:location]目标点[/format]",
    "DLC2_OUTRO_PC_14": "呃，有道理。",
    "DLC2_INTRO_KWILANNA_05": "噢嗨！我是珍妮，就想告诉你，我现在超——级兴奋的！",
    "CUST_ITEM_CM_DLC_LU02": "摔角手士兵",
    "CUST_ITEM_DLC_HAT_LU02": "摔角手面具",
    "DLC2_OUTRO_PC_04": "呃……奎薇拉娜？",
    "CUST_ITEM_DLC_MASK_WEREWOLF": "狼人面具",
    "UNL_DLC2_VEHICLE_SPACE_JET_BIKE": "载具 - 镰刀",
    "UNL_DESC_DLC2_STORY01_ALERT": "你的手机中已有新任务。",
    "DLC2_INTRO_DIRECTOR_17": "真是棒极了。",
    "DLC2_INTRO_KWILANNA_12": "甄先生，幸会！",
    "CUST_ITEM_DLC_SUIT_WEREWOLF": "狼人",
    "CUST_ITEM_DLC_SUIT_LU02": "摔角手士兵",
}

seen = set()
out_lines = []
missing = []
for ln in src_lines:
    m = re.match(r'^"([^"]+)":\s*"(.*)"\s*$', ln)
    if not m:
        continue
    k = m.group(1)
    if k in seen:
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
