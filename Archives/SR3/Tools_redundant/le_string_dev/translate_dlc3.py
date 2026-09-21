# -*- coding: utf-8 -*-
import re, io, sys

SRC = "original/dlc3_us.txt"
OUT = "schinese/dlc3_us.txt"

raw = io.open(SRC, encoding="utf-8").read().replace("\r\n", "\n")
src_lines = [l for l in raw.split("\n") if l.strip() != ""]

T = {
    "DLC3_OUTRO_08_TAG": "脱衣舞杆？",
    "DLC3_INTRO_20_JIMMY": "你至少能给我签个名吗？",
    "DLC3_INTRO_03_JIMMY": "他们辜负了你，约翰尼……但我永远不会。我可是你头号粉丝。",
    "DLC3_OUTRO_06_TAG": "盖特……朋友？",
    "UNL_DLC3_STORY03_VEHICLES": "载具 - 托比特龙",
    "UNL_DESC_DLC3_HOMIE_JOHNNY_TAG": "约翰尼·塔格可在你的手机“PHONE”菜单中找到。他将在战斗中协助圣徒。",
    "DLC3_ACHIEVE_SR3_JOHNNYGUARD": "塔格护卫队",
    "DLC3_M01_GET_BACK_TO_CLUB": "回到[format][color:location]合规特区[/format]",
    "DLC3_INTRO_18_JIMMY": "好嘞，干吧！",
    "DLC3_INTRO_15_JIMMY": "这也太带感了！",
    "DLC3_INTRO_10_PIERCE": "等等！你们克隆了盖特？",
    "DLC3_OUTRO_12_JIMMY": "完。问号？",
    "HOMIE_DESC_DLC3_JOHNNY_TAG": "和盖特那个巨型克隆体一起大杀四方！",
    "DLC3_ACHIEVE_SR3_HERES_JOHNNY": "怪奇科学",
    "DLC3_M03_DEFEND_TAG": "防守[format][color:defend]塔格[/format]",
    "DLC3_INTRO_06_JIMMY": "我还以为我收集的东西够多了，毕竟都是他碰过的。",
    "HOMIE_DLC3_AISHA_BRUTELLA": "艾莎·布鲁特拉",
    "CUST_ITEM_DLC_FOOT_WITCH": "女巫靴  ",
    "HOMIE_DLC3_JOHNNY_TAG": "约翰尼·塔格",
    "UNL_DLC3_CUST02_OUTFITS01": "行头 - 女巫",
    "DLC3_OUTRO_04_PLAYER": "我知道，我知道，约翰尼，但是——我们能把它挽回的！",
    "DLC3_OUTRO_02_PIERCE": "噢，现在你倒想跟他讲道理了。",
    "DLC3_INTRO_01_JIMMY": "那是一个风雨交加的黑夜……",
    "DLC3_OUTRO_07_PLAYER": "来吧兄弟，咱们送你回家。",
    "DLC3_INTRO_14_TAG": "翔悟去哪了？",
    "HOMIE_DESC_DLC3_AISHA_BRUTELLA": "如今她改用拳头而非歌喉，把粉丝们揍得人仰马翻。",
    "DLC3_OUTRO_10_JIMMY": "于是，我们无畏英雄的传奇就此落幕。被他们彼此之间那份……那份呃……情谊所维系。",
    "DLC3_ACHIEVE_SR3_MY_PET_MONSTER": "我的宠物怪兽",
    "UNL_DLC3_CUST02_CLOTHING": "道具 - 女巫与热狗",
    "DLC3_OUTRO_01_PLAYER": "约翰尼——拜托兄弟，我是在帮你啊！",
    "DLC3_M02_DEFEND_STAGE": "防守[format][color:defend]舞台[/format]",
    "DLC3_INTRO_04_JIMMY": "这几个月来我一直在收集那些玩意儿。他的玩意儿。",
    "DLC3_INTRO_19_PIERCE": "坐下，吉米！这事儿交给专业的人。",
    "DLC3_OUTRO_05_PIERCE": "对啊，我们是你的朋友，约翰尼。",
    "DLC3_OUTRO_03_TAG": "圣徒……辜负了我。",
    "CUST_ITEM_DLC_HAT_DH": "恶魔之角",
    "UNL_DESC_DLC3_CUST02_VEHICLES": "毕竟女巫出行只能靠扫帚。有了这载具，大家也都能体验一把了。",
    "DLC3_INTRO_07_JIMMY": "我错了。",
    "CUST_ITEM_DLC_PANTS_WITCH": "女巫裤",
    "DLC3_INTRO_02_JIMMY": "当我亵渎自然之母时，连天都落泪了。",
    "UNL_DLC3_HOMIE_AISHA_BRUTELLA": "兄弟 - 艾莎·布鲁特拉",
    "CUST_ITEM_DLC_OUTFIT_WITCH": "女巫",
    "DLC3_M03_KILL_GUARD": "击杀[format][color:kill]钢埠警卫[/format]",
    "CUST_ITEM_DLC_HAT_HALO": "圣徒光环",
    "DLC3_INTRO_11_JIMMY": "嗯。",
    "DLC3_M03_FAIL_TAG_DIED": "约翰尼·塔格阵亡！",
    "DLC3_M03_KILL_MORNINGSTAR": "击杀[format][color:kill]晨星帮[/format]",
    "DLC3_M03_GOTO_PLANET_SAINTS": "在[format][color:location]圣徒星球[/format]寻找塔格",
    "DLC3_OUTRO_11_JIMMY": "钢埠再次恢复了安宁。但谁又能说准，圣徒和他们新结盟的伙伴何时又会受召来保护她。",
    "DLC3_OUTRO_09_PLAYER": "没错兄弟，就是脱衣舞杆。",
    "DLC3_INTRO_16_JIMMY": "等等——你们要去哪？",
    "DLC3_INTRO_17_PLAYER": "我们去找回我们的朋友！",
    "DLC3_INTRO_08_JIMMY": "约翰尼？",
    "DLC3_INTRO_05_JIMMY": "墨镜、嚼过的旧口香糖、一把油腻的梳子。",
    "DLC3_ACHIEVE_SR3_SEND_CLONES": "派出克隆军团",
    "DLC3_ACHIEVE_SR3_PUBLIC_ENEMY": "头号公敌",
    "CUST_ITEM_DLC_HOTDOG": "热狗",
    "CUST_ITEM_DLC_SUIT_NINJA": "忍者",
    "UNL_DESC_DLC3_STORY03_ALERT": "你的手机中已有新任务。",
    "UNL_DLC3_CUST02_ALERT": "女巫与热狗",
    "DLC3_M03_GOTO_BRIDGE": "在[format][color:location]大桥[/format]寻找塔格",
    "DLC3_INTRO_13_JIMMY": "我正要说到那一段呢！",
    "DLC3_INTRO_12_PLAYER": "行吧，那他人呢——",
    "UNL_DESC_DLC3_CUST02_ALERT": "你的巢穴衣橱与车库中已有新的可下载内容。",
    "DLC3_INTRO_09_TAG": "艾莎！",
    "UNL_DESC_DLC3_HOMIE_AISHA_BRUTELLA": "艾莎·布鲁特拉可在你的手机“PHONE”菜单中找到。她将在战斗中协助圣徒。",
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
