"""manual_decisions.py —— 人工定夺结论（2026-10-02，由hy3 裁决）。

裁决原则（用户 2026-10-02 明确要求）
--------------------------------------
> 「普遍按照通用词来翻译，例如 WHITE 就翻译成白色，没有问题，
>   咱也不知道具体游戏里会怎么调用。」

据此确立四条规则：

R1 **通用义优先**：颜色/形状/量词一律用通用中文词，不做特指推测。
   ``WHITE→白色``、``BLACK→黑色``、``GOLD→金色``、``SILVER→银色``。
   理由：若游戏实际用作颜色，通用词永远正确；赌特指反而可能错。

R2 **★ 但 key 前缀可证明语境时，按前缀走**（R1 的例外，且优先级更高）。
   实测铁证：
     ``CUST_RACE_WHITE→白人`` / ``CUST_RACE_BLACK→黑人``（种族）
     ``CUST_COLOR_WHITE→白色`` / ``HAIR_White→白色``（颜色）
   同一英文两种译法**都是对的**，靠 key 前缀区分。绝不能全局替换。

R3 **人种/族群用规范译法**：``ASIAN→亚裔``（非「亚洲」，后者是地域义）。
   佐证：同文件 ``CUST_RACE_ASIAN→亚裔``、``OILED ASIAN→亚裔``
   已经这么译了，现译「亚洲」是**同语境自相矛盾**。

R4 **对白不改**：同源多译若属不同角色/场景的措辞差异，一律保持原样。
   理由：对白体现角色性格，强行拉平会毁掉人物个性；且这类差异
   通常伴随语气词/程度词，不影响理解。

本文件是**纯数据 + 少量分派逻辑**，由 ``unify_terms.py`` 读取。
把裁决与代码分离，便于随时修订而不动引擎。
"""

from __future__ import annotations

import re
import sys

# ---------------------------------------------------------------- 整条替换
# 英文原文（小写匹配）-> 权威译法
FORCE_WHOLE: dict[str, str] = {
    # ---- R1 通用义优先：颜色 ----
    # silver/gold/steel 只在「无 key 前缀限定」时套用；
    #   有前缀时走 decide_by_key（奖牌名/涂装名单字保持不变）。
    #
    # ★ 颜色一律带「色」字（金色/银色/钢色），与奖牌的「金/银/钢」单字区分。
    #   实测依据：SR4/customize_us.txt 里「金色」(:1030) 与「银」(:448) 并存，
    #   同一界面里颜色体系不成样；SR3 也有 CUST_SKIN_METAL_1→银色（带色字）
    #   与 CUST_VARIANT_SILVER→银（单字）并存。统一为带「色」字。
    "white": "白色",
    "black": "黑色",
    "gold": "金色",
    "silver": "银色",
    "steel": "钢色",
    # ---- R1 通用义优先：形状/量词 ----
    "circles": "圆形",
    "flowers": "花朵",
    # ---- R1 通用义优先：通用动作/状态 ----
    "normal": "普通",
    "low": "低腰",
    "high": "高位",
    "medium": "中号",
    "mediums": "中号",
    "no": "否",
    "forward": "前",
    # ---- R2 例外：这四个由「key 前缀 / 文件」分派，不做整条统一 ----
    # BACK  UI=返回 / 纹身=背部 / 习语=背后   → 已由 L3 义项检测 + 前缀分派处理
    # BUILD 体型维度=体型 / 建造语境=建造      → 仅剩 STORE_BUILD→建造（正确）
    # LEVEL 关卡 vs 等级→ 已由 FILE_SCOPED 按文件分派
    # THE A BUTTON 跨平台按键，非译法冲突 → 保持原样
    # ---- R3 人种 ----
    "asian": "亚裔",
    # ---- 明显错译 ----
    "streaking": "裸奔",     # streak = 裸奔；「天体」是星体，误译
    "maero": "梅罗",         # 人物 Maero；「马罗」疑为错字
    "powder": "粉帮",         # Powder Gang = 粉帮；「火药」是字面义
    # ---- 专名定夺 ----
    "fight club": "搏击俱乐部",  # 官方中文版用「搏击俱乐部」
    "flamethrower": "火焰喷射器",  # 装备名统一；口语泛指亦可接受
    "auburn": "红棕发",       # 发色名，与「红棕 1..5」编号族对齐
}

# ---------------------------------------------------------------- 按 key 前缀分派
# 同英文在不同 key 前缀下语义不同，必须分开处理。
# 格式：(key 前缀正则, 英文小写正则, 目标译法, 依据)
FORCE_BY_KEY: list[tuple[str, str, str, str]] = [
    # ---- R2 铁证：种族 vs 颜色 ----
    (r"^(CUST_RACE_|RACE_|PLAYER_MORPH_|PLAYER_CREATION_PRESET_)",
     r"^(white|black|asian|caucasian|hispanic)$", None,
     "key 前缀含 RACE/MORPH → 人种，用「白人/黑人/亚裔」"),
    (r"^(CUST_COLOR_|HAIR_|CUST_COMP_EYES_|UNLOCALIZED_)",
     r"^(white|black)$", None,
     "key 前缀含 COLOR/HAIR/EYES → 颜色，用「白色/黑色」"),
    # ---- R2 续：奖牌等级名不可改成颜色词 ----
    #实测 SR3/diversion_us.txt 同族为
    #   SURVIVAL_GOAL_NAME_BRONZE→青铜 / _SILVER→白银 / _GOLD→黄金
    #   若按通用义把「白银/黄金」改成「银色/金色」，奖牌等级体系就乱了。
    (r"^DIVERSION_SURVIVAL_GOAL_NAME_", r"^(silver|gold|bronze)$", None,
     "奖牌等级名，保持「白银/黄金/青铜」单字"),
    # ---- R2 续：载具涂装名保持单字 ----
    # 实测 SR3/customize_us.txt: VEHICLE_COLOR_SILVER→银 / _GOLD→金 / _STEEL→钢
    # 这是涂装**选项名**，与「金色/银色」不是一回事，且要与其他涂装名等宽。
    (r"^VEHICLE_COLOR_", r"^(silver|gold|steel)$", None,
     "载具涂装选项名，保持「银/金/钢」单字"),
    # ---- R2 续：发色名族 ----
    (r"^(HAIR_COLOR_|CUST_HAIR_)", r"^auburn$", None,
     "发色名，与同族「红棕 1..5」对齐用「红棕发」"),
]

# 前缀 -> 人种/颜色译名
RACE_ZH = {"white": "白人", "black": "黑人", "asian": "亚裔",
           "caucasian": "白人", "hispanic": "拉美裔"}
COLOR_ZH = {"white": "白色", "black": "黑色"}

# ★ 特定 key 前缀的「保持原样」白名单（裁决也覆盖不到的地方）。
# 这两类**必须用两张表**：同一英文词在两处的正确译法不同。
#   ``DIVERSION_SURVIVAL_GOAL_NAME_SILVER``→白银/ _GOLD→黄金 / _BRONZE→青铜
#     是**奖牌等级体系**，成体系；若按通用义改成「银色/金色」会破坏等级对应。
#   ``VEHICLE_COLOR_SILVER``→银 / _GOLD→金 / _STEEL→钢
#     是**涂装选项名**，与其他涂装名等宽排版，用单字。
# ⚠ v1 曾合用一张 KEEP_AS_IS，两处都拿到「白银/黄金」→ 涂装名被错译。
KEEP_MEDAL = {
    "silver": "白银",
    "gold": "黄金",
    "bronze": "青铜",
}
KEEP_PAINT = {
    "silver": "银",
    "gold": "金",
    "steel": "钢",
}

# ◆ 裁决复议（2026-10-02晚间修正）
#
# 上一版把 ``VEHICLE_COLOR_*`` 设为「保持单字银/金/钢」，理由是「与其他涂装名等宽」。
# 但这与 R1「颜色一律带色字」**自相矛盾** ——同一个词 silver 在
# ``CUST_SKIN_METAL_1`` 译「银色」、在 ``VEHICLE_COLOR_SILVER`` 译「银」，
# 玩家在同一个改装界面里看到两套写法。
#
# 现按 R1 统一为**带色字**：涂装名/皮肤名/颜色选项一律「银色/金色/钢色」；
# 只有**奖牌等级**保留「白银/黄金/青铜」（那是成体系的金属称谓，不是颜色）。
# 这样规则更简单、也更好维护：「色」字 = 颜色，单字金属 = 奖牌等级。
VEHICLE_COLOR_PAINT_ZH = {
    "silver": "银色",
    "gold": "金色",
    "steel": "钢色",
}


def decide_by_key(key: str, source: str) -> tuple[str, str] | None:
    """按 key 前缀分派译法。返回 (译法, 依据) 或 None。

    ⚠ 分派必须**按前缀精确查表**，不能用「if 字符串 in 前缀正则」的 elif链：
    v1 实测踩过——``CUST_COLOR_|HAIR_...`` 那条正则里含 "COLOR"，先把
    ``VEHICLE_COLOR_SILVER`` 抢走并去查 COLOR_ZH（里面没有 silver）→返回 None，
    导致涂装名白名单静默失效。现改为「先按KEEP_AS_IS 判，再按 RACE/COLOR 判」。
    """
    k = (key or "").strip()
    s = (source or "").strip().lower()

    # ① 保持原样名单（奖牌等级名 / 载具涂装名单字）——优先级最高
    for pat, en_rx, _t, why in FORCE_BY_KEY:
        if not (re.match(pat, k, re.I) and re.match(en_rx, s, re.I)):
            continue
        if "SURVIVAL_GOAL_NAME" in pat and s in KEEP_MEDAL:
            return KEEP_MEDAL[s], why
        if pat.startswith(r"^VEHICLE_COLOR_") and s in VEHICLE_COLOR_PAINT_ZH:
            return VEHICLE_COLOR_PAINT_ZH[s], why

    # ② 人种
    for pat, en_rx, _t, why in FORCE_BY_KEY:
        if "RACE" in pat or "MORPH" in pat:
            if re.match(pat, k, re.I) and re.match(en_rx, s, re.I):
                return RACE_ZH.get(s), why

    # ③ 颜色（仅 white/black）
    for pat, en_rx, _t, why in FORCE_BY_KEY:
        if "CUST_COMP_EYES" in pat or pat.startswith(r"^CUST_COLOR_") \
                or pat.startswith(r"^HAIR_") or pat.startswith(r"^UNLOCALIZED_"):
            if re.match(pat, k, re.I) and re.match(en_rx, s, re.I):
                return COLOR_ZH.get(s), why

    # ④ 发色名族
    if re.match(r"^(HAIR_COLOR_|CUST_HAIR_)", k, re.I) and s == "auburn":
        return "红棕发", "发色名，与同族「红棕 1..5」对齐"

    return None


def _self_test() -> None:
    """前缀分派自检。这些用例每条都是实测踩过的坑，勿删。"""
    cases = [
        # (key, 英文原文, 期望译法, 说明)
        ("VEHICLE_COLOR_SILVER", "silver", "银色", "涂装名=R1 颜色带色字"),
        ("VEHICLE_COLOR_GOLD", "gold", "金色", "涂装名=R1 颜色带色字"),
        ("VEHICLE_COLOR_STEEL", "steel", "钢色", "涂装名=R1 颜色带色字"),
        ("DIVERSION_SURVIVAL_GOAL_NAME_GOLD", "gold", "黄金", "奖牌等级"),
        ("DIVERSION_SURVIVAL_GOAL_NAME_SILVER", "silver", "白银", "奖牌等级"),
        ("DIVERSION_SURVIVAL_GOAL_NAME_BRONZE", "bronze", "青铜", "奖牌等级"),
        ("CUST_RACE_WHITE", "white", "白人", "人种，非白色"),
        ("CUST_RACE_BLACK", "black", "黑人", "人种，非黑色"),
        ("RACE_ASIAN", "asian", "亚裔", "人种，非亚洲"),
        ("RACE_CAUCASIAN", "caucasian", "白人", "统一为白人"),
        ("PLAYER_MORPH_ASIAN_TEXT", "asian", "亚裔", "MORPH 前缀"),
        ("CUST_COLOR_WHITE", "white", "白色", "颜色，非白人"),
        ("HAIR_White", "white", "白色", "发色，非白人"),
        ("HAIR_COLOR_AUBURN", "auburn", "红棕发", "发色名"),
    ]
    bad = 0
    for k, s, want, why in cases:
        r = decide_by_key(k, s)
        got = r[0] if r else None
        if got != want:
            bad += 1
        mark = "OK  " if got == want else "FAIL"
        print(f"  {mark} {k:36s} -> {got!s:8s} 期望 {want!s:8s} ({why})")
    print(f"\n前缀分派自检{'通过' if bad == 0 else f'失败 {bad} 项'}")
    return bad


if __name__ == "__main__":
    sys.exit(1 if _self_test() else 0)


# ---------------------------------------------------------------- 中文片段替换
# (错误片段, 正确片段, 依据)—— 对全文做子串替换，覆盖嵌在长句里的错译。
FORCE_SUBST: list[tuple[str, str, str]] = [
    # 人种：亚洲 → 亚裔（R3；同文件已有 CUST_RACE_ASIAN→亚裔 的正确先例）
    ("[color:red]亚洲", "[color:red]亚裔", "R3 人种应为「亚裔」"),
    # 错别字
    ("马罗", "梅罗", "人物 Maero 通行译名"),
    # ---- 编号变体族（``亚洲 1``/``高加索 2`` 这类带尾号的多余变体）----
    # R1裁决：ASIAN/CAUCASIAN 统一为亚裔/白人，编号变体一并跟上。
    # ⚠ 「亚洲（不含日本和中国）」是**地区名**（SR4 发行地区表），必须保留！
    #   故亚洲只替换「亚洲+ 空格+数字」的编号变体，不动裸「亚洲」。
    ("亚洲 ", "亚裔 ", "ASIAN N → 亚裔 N（R1 人种统一）"),
    ("高加索", "白人", "CAUCASIAN 全系列 → 白人（R1 人种统一；「高加索」是旧译）"),
    # ---- Streaking 词族：全部指「裸奔」，不是天体 ----
    # 实测：``NUDE→天体``、``PEOPLE STREAKED→天体经过人数``、
    #   ``PRESS %ls AGAIN TO START STREAKING→开始天体`` 全是同一玩法。
    ("天体", "裸奔", "Streaking = 裸奔；「天体」是星体，误译"),
]
