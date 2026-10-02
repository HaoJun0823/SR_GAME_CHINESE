"""sense_lexicon.py —— L3 义项错配检测的词表（可独立维护、便于审阅）。

原理
----
一词多义误译的形态是「**源文取了A 义项，译文落到了 B 义项**」。
于是只需：
  1. 给英文词建**义项表**，每个义项标注所属「语义域」；
  2. 给中文词标注它属于哪个「语义域」；
  3. 源文某词取域 A，而译文出现域 B 的词 → **错配**。

为什么不能只靠词表全局替换
--------------------------
实测反例（**必须靠上下文区分**）：
  - ``SR3/static_us.txt:1337  "TAT_REG_BACK": "BACK"`` → ``"背部"``
    key 是 ``TAT_REG_``（纹身部位正则），但同文件邻近全是
    ``VCUST_``/``STAT_`` 服装属性键，说明此处实为 UI「返回」，**误译**。
  - ``SR4/customize_us.txt  "WHOLE BACK"`` → ``"整个背部"``
    躯干纹身，**正确**。
两者英文同含 BACK，仅靠词表无法区分，必须看 ``key_prefix`` 与所在文件。
所以本模块只负责**产出候选**，最终判定需带上下文（本工具会一并导出）。

语义域
------
UI 导航域 = 返回/退出/取消/继续…
身体域   = 背部/胸膛/骨骼/肌肉…
建造域   = 建造/盖房/构筑/工程…
战斗域   = 命中/伤害/击杀/攻击…
移动域   = 冲刺/翻滚/爬/跳…
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 语义域定义

DOMAIN_UI = "ui_nav"
DOMAIN_BODY = "body"
DOMAIN_BUILD = "build"
DOMAIN_COMBAT = "combat"
DOMAIN_MOVE = "move"
DOMAIN_OBJECT = "object"

# ---------------------------------------------------------------- 英文义项表
# 词 -> [(义项说明, 语义域), ...]
# 一个词在不同语境属不同域，这是「一词多义」的根源。
EN_SENSE: dict[str, list[tuple[str, str]]] = {
    "BACK": [("UI 返回", DOMAIN_UI), ("背部（躯干/纹身/座椅）", DOMAIN_BODY),
             ("向后移动", DOMAIN_MOVE)],
    "RETURN": [("UI 返回/归还", DOMAIN_UI)],
    "EXIT": [("退出", DOMAIN_UI)],
    "CANCEL": [("取消", DOMAIN_UI)],
    "CONTINUE": [("继续", DOMAIN_UI)],

    "BUILD": [("建造（盖楼/施工）", DOMAIN_BUILD),
              ("培养/成长/打造（技能、装备）", DOMAIN_OBJECT),
              ("积攒（连击、资源）", DOMAIN_OBJECT)],
    "CRAFT": [("制作/锻造", DOMAIN_BUILD)],
    "FORGE": [("锻造", DOMAIN_BUILD)],
    "UPGRADE": [("升级", DOMAIN_OBJECT)],

    "HIT": [("命中/击中", DOMAIN_COMBAT), ("打击乐（曲目名）", DOMAIN_OBJECT)],
    "KILL": [("击杀/杀死", DOMAIN_COMBAT)],
    "STRIKE": [("打击/攻击", DOMAIN_COMBAT)],
    "SLASH": [("劈砍", DOMAIN_COMBAT)],
    "DAMAGE": [("伤害", DOMAIN_COMBAT)],
    "ATTACK": [("攻击", DOMAIN_COMBAT)],

    "DASH": [("冲刺", DOMAIN_MOVE), ("短跑（田径）", DOMAIN_MOVE)],
    "ROLL": [("翻滚", DOMAIN_MOVE), ("卷（材料/地毯）", DOMAIN_OBJECT)],
    "CRAWL": [("匍匐爬行", DOMAIN_MOVE)],
    "CLIMB": [("攀爬", DOMAIN_MOVE)],
    "JUMP": [("跳跃", DOMAIN_MOVE)],
    "VAULT": [("翻越", DOMAIN_MOVE)],
    "LEAP": [("跃起", DOMAIN_MOVE)],
    "DIVE": [("鱼跃/俯冲", DOMAIN_MOVE)],
    "STOMP": [("踩踏/跺脚", DOMAIN_MOVE)],

    "GUARD": [("格挡/守卫", DOMAIN_COMBAT), ("警卫（人）", DOMAIN_OBJECT)],
    "BLOCK": [("格挡", DOMAIN_COMBAT), ("方块（城市街区）", DOMAIN_OBJECT)],
    "CHARGE": [("冲锋/蓄力", DOMAIN_MOVE), ("收费/扣款", DOMAIN_OBJECT)],
    "SMASH": [("猛砸", DOMAIN_COMBAT)],
    "GRAB": [("抓取/抢夺", DOMAIN_COMBAT)],
    "SHOVE": [("推搡", DOMAIN_COMBAT)],
    "WHIP": [("鞭打", DOMAIN_COMBAT)],
    "PUNCH": [("拳击", DOMAIN_COMBAT)],
    "KICK": [("踢击", DOMAIN_COMBAT)],
    "TACKLE": [("擒抱/拦截", DOMAIN_COMBAT)],
    "STUN": [("击晕/眩晕", DOMAIN_COMBAT)],
    "TAUNT": [("嘲弄/挑衅", DOMAIN_COMBAT)],
    "FIGHT": [("战斗/打架", DOMAIN_COMBAT), ("拳赛（赛事名）", DOMAIN_OBJECT)],
    "BRAWL": [("混战", DOMAIN_COMBAT)],
    "WRECK": [("撞毁/破坏", DOMAIN_COMBAT)],

    "LOAD": [("读取/载入", DOMAIN_UI), ("装载（货物）", DOMAIN_OBJECT)],
    "SAVE": [("保存", DOMAIN_UI)],
    "STOCK": [(" stocks 存货", DOMAIN_OBJECT), (" STOCK  stocks 股份", DOMAIN_OBJECT)],
    "PICK UP": [("捡起", DOMAIN_MOVE), ("接人（劫车）", DOMAIN_OBJECT)],
    "TAKE": [("拿/取", DOMAIN_MOVE), ("任务（活动）", DOMAIN_OBJECT),
             ("承受（伤害）", DOMAIN_COMBAT)],
    "DROP": [("丢弃/掉落", DOMAIN_MOVE)],
    "PUSH": [("推动", DOMAIN_MOVE)],
    "PULL": [("拉动", DOMAIN_MOVE)],
    "TOGGLE": [("切换", DOMAIN_UI)],
    "LOCK": [("锁定", DOMAIN_UI), ("锁", DOMAIN_OBJECT)],
    "UNLOCK": [("解锁", DOMAIN_UI)],
    "SCORE": [("分数", DOMAIN_OBJECT), ("得分", DOMAIN_COMBAT)],
    "STAGE": [("阶段/关卡", DOMAIN_OBJECT), ("舞台", DOMAIN_OBJECT)],
    "POWER": [("力量/电力", DOMAIN_OBJECT)],
    "SHOOT": [("射击/开枪", DOMAIN_COMBAT)],
    "TARGET": [("目标", DOMAIN_COMBAT), ("靶子", DOMAIN_OBJECT)],
    "CASH": [("现金", DOMAIN_OBJECT)],
    "LOOK": [("看/注视", DOMAIN_MOVE), ("外表/样貌", DOMAIN_OBJECT)],
    "HOOD": [("街区", DOMAIN_OBJECT), ("兜帽", DOMAIN_OBJECT)],
    "CRIB": [("据点/安全屋", DOMAIN_OBJECT)],
    "HEAD": [("头", DOMAIN_BODY), ("首领", DOMAIN_OBJECT), ("朝前移动", DOMAIN_MOVE)],
    "HAND": [("手", DOMAIN_BODY), (" handing 递交", DOMAIN_OBJECT)],
    "FOOT": [("脚", DOMAIN_BODY)],
    "CHEST": [("胸/胸膛", DOMAIN_BODY), ("箱子", DOMAIN_OBJECT)],
    "STOMACH": [("胃/腹部", DOMAIN_BODY)],
    "FACE": [("脸", DOMAIN_BODY), ("表面", DOMAIN_OBJECT)],
    "EYE": [("眼", DOMAIN_BODY)],
    "NECK": [("脖子", DOMAIN_BODY)],
    "SHOULDER": [("肩膀", DOMAIN_BODY)],
    "KNEE": [("膝盖", DOMAIN_BODY)],
}

# ---------------------------------------------------------------- 中文域归属
# 中文词 -> 它属于哪个语义域（一个词可属多个域）
ZH_DOMAIN: dict[str, str] = {}


def _reg(d: str, words: str) -> None:
    for w in words.split():
        ZH_DOMAIN[w] = d


_reg(DOMAIN_UI, "返回 退出 取消 继续 确定 确认 选择 设置 选项 菜单 暂停 保存 读取 载入 解锁 锁定 切换 返回键")

_reg(DOMAIN_BODY,
     "背部 背 胸膛 胸 腹部 肚 肚子 腿 脚 膝盖 肩 肩膀 脖子 喉咙 下巴 屁股 手腕 "
     "手指 舌头 骨头 肋骨 脊椎 动脉 血管 神经 肌肉 骨骼 脸 眼睛 头 后背 前胸 腰")

_reg(DOMAIN_BUILD,
     "建造 盖房 盖楼 施工 构筑 盖起 盖上 施工队 建筑工地 施工场")

_reg(DOMAIN_COMBAT,
     "命中 击中 伤害 击杀 杀死 杀死 攻击 打击 劈砍 砍 射击 开枪 射 弹药 击杀数 "
     "格斗 搏击 打架 战斗 混战 撞毁 破坏 眩晕 击晕 挑衅 嘲弄 拦截 擒抱 推搡 鞭打 拳击 踢")

_reg(DOMAIN_MOVE,
     "冲刺 翻滚 滚 匍匐 爬行 攀爬 爬 跳跃 跳 跃起 翻越 鱼跃 俯冲 踩踏 跺脚 "
     "向左转 向右转 前进 后退 移动 捡起 丢弃 掉落 推动 拉动 拿 取 观看 注视")

_reg(DOMAIN_OBJECT,
     "分数 得分 关卡 阶段 舞台 力量 电力 股票 存货 股份 现金 目标 靶子 首领 头目 "
     "外表 样貌 街区 兜帽 据点 安全屋 箱子 锁 表面 招式 技能 等级 品质 稀有")

# ---------------------------------------------------------------- 强信号
# 命中这些组合时几乎必然是误译（实测验证过），单独提为 HIGH 置信。
STRONG_PAIRS: list[tuple[str, str, str, str]] = [
    # (英文词, 期望域, 中文词, 实际域, 说明)
    ("BACK", DOMAIN_UI, "背部", DOMAIN_BODY, "UI 返回被译成「背部」"),
    ("BACK", DOMAIN_UI, "背", DOMAIN_BODY, "UI 返回被译成「背」"),
    ("BUILD", DOMAIN_BUILD, "培养", DOMAIN_OBJECT, "建造被译成「培养」"),
    ("HIT", DOMAIN_COMBAT, "命中", DOMAIN_COMBAT, "正常（示例，实际不报）"),
]

# ---------------------------------------------------------------- 上下文线索
# 依据 key 前缀 + 邻近键判断「这个 BACK 到底是返回还是背部」。
# 实测：TAT_REG_*（纹身部位）在 static_us.txt 里被服装属性键包围 → 是 UI 返回。
UI_HINT_PREFIX = re.compile(
    r"^(MENU|MAINMENU|UI_|BTN_|KEY_|NAV_|BACK_|EXIT_|CANCEL_|CONFIRM_|PAUSE_|"
    r"OPTIONS?_?|SETTINGS?|LOADSAVE|SAVELOAD|MAINMENU_)",re.I)

BODY_HINT_PREFIX = re.compile(
    r"^(TAT_|TATTOO_|VCUST_|CUST_|CM_|BODY_|STAT_BODY|APPEARANCE)", re.I)

# ★ 身体部位的**英文短语**（不靠 key 前缀也能识别）。
# 实测教训：仅靠 key 前缀不够——
#   "WHOLE BACK" / "ENTIRE BACK" / "UPPER BACK" / "LOWER BACK" 的 key 无前缀，
#   但它们是躯干纹身，译「整个背部」是**正确**的，曾被误报成UI 返回错配。
BODY_EN_PHRASE = re.compile(
    r"\b(WHOLE|ENTIRE|FULL|UPPER|LOWER|LEFT|RIGHT|FRONT|BACK)\s+"
    r"(BACK|BODY|CHEST|STOMACH|LEG|ARM|FACE|HEAD|FOOT|HAND|ABDOMEN)\b"
    r"|\b(BACK|BODY)\s+(TATTOO|TATTOOS)\b"
    r"|\bWINGS?\b|\bTAIL\b",
    re.I,
)

# ★ 习语/固定搭配白名单：这些短语里的 "back"/"build" 等**不是**UI/建造义项。
#
# 实测教训（v1 误报溯源）：逐词硬匹配把下面这些**译法正确**的对白也报成了错配——
#   "Angel, watch your back, hon!"  → 「安吉尔，当心背后，亲爱的！」  ✔正确
#   "See, I had your back."          → 「瞧，我照你背后呢。」          ✔正确
#   "Sorry... I turned my back..."   → 「我就背过身一分钟…」            ✔正确
#   "climb on my back!"              → 「骑到背上来！」                  ✔正确
# 所以「单词 + 身体义项」≠ 误译；必须先排除习语与隐喻用法。
BODY_IDIOM = re.compile(
    # 覆盖动词变位：watch / watches / watched / watching + 物主代词 + back
    r"\bwatch(?:es|ed|ing)?\s+(your|his|her|their|my|our)\s+back"
    r"|\bcover(?:s|ed|ing)?\s+(your|his|her|their|my|our)\s+back"
    r"|\bhad\s+(your|his|her|their)\s+back"
    r"|\bon\s+(your|his|her|their|my)\s+back"
    r"|\bturned\s+(my|his|her|their|your)\s+back"
    r"|\bturn(?:ed|ing)?\s+back"
    r"|\bback\s+of\b"
    r"|\bclimb\s+on\s+my\s+back"
    r"|\bpat(?:s|ted|ting)?\b.{0,25}\bback\b"
    r"|\bbreak(?:s|ing)?\b.{0,20}\bback\b"
    r"|\bget\s+back\s+"
    r"|\bcome\s+back\b"
    r"|\bgo\s+back\b"
    r"|\bhold\b.{0,20}\bback\b"
    r"|\bback\s+(?:up|down|away|off|out)"
    r"|\bbehind\s+(?:you|him|her|them|me|us)"
    r"|\bback\s+(?:against|among|into|through|over|under|within|upon)"
    r"|\bfrom\s+behind\b"
    r"|\b(?:stab|stabs|stabbing|knife|knives|dagger|daggers)\w*\b.{0,30}\bback\b"
    r"|\brhinestone\b.{0,30}\bback\b"
    r"|\btattoo\w*.{0,20}back"
    r"|(?:upper|lower|whole|entire|full)\s+back",
    re.I,
)


def is_body_idiom(source: str) -> bool:
    """源文里的 back类词是否处于习语/隐喻/固定搭配中（此时译「背/背后」正确）。"""
    return bool(BODY_IDIOM.search(source or ""))


def body_context_by_source(source: str) -> bool:
    """英文源文本身是否描述身体部位/外观。"""
    return bool(BODY_EN_PHRASE.search(source or ""))


def ui_context(key: str) -> bool:
    """key 是否指向 UI 导航语境。"""
    return bool(UI_HINT_PREFIX.match((key or "").strip()))


def body_context(key: str, source: str = "") -> bool:
    """综合 key 前缀与源文判断是否身体/外观语境。

    ★ 同时排除习语：``watch your back`` / ``I had your back`` /
    ``turned my back`` 等固定搭配里译「背/背后」是**正确**的，
    不能当成UI 返回错配（v1 误报溯源，见 BODY_IDIOM 注释）。
    """
    if is_body_idiom(source):
        return True
    if BODY_HINT_PREFIX.match((key or "").strip()):
        return True
    return body_context_by_source(source)


def zh_words(text: str) -> list[str]:
    """取出文本中的已登记中文域词。"""
    out = []
    for w in ZH_DOMAIN:
        if w and w in (text or ""):
            out.append(w)
    return out


def dominant_zh_domain(text: str) -> str | None:
    """文本中占主导的中文语义域（无登记词则None）。"""
    hits: dict[str, int] = {}
    for w in zh_words(text):
        d = ZH_DOMAIN[w]
        hits[d] = hits.get(d, 0) + 1
    if not hits:
        return None
    return max(hits.items(), key=lambda kv: kv[1])[0]
