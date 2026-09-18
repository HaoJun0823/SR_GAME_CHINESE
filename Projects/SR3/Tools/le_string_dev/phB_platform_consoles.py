# -*- coding: utf-8 -*-
# Phase B: 6 个主机平台文件未译补译（共享键统一翻译；纯图片 BTN/DPAD/ANALOG_IMG 保留英文）
import re, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = {
 "BTN_LTHUMB_TXT": "左摇杆",
 "BTN_RTHUMB_TXT": "右摇杆",
 "CHAT_DISABLED": "由于账号的家长控制限制，在线聊天功能已被禁用。 ",
 "CONTROL_R_STICK": "右摇杆",
 "COOP_JOIN_CHEAT": "你的合作队友正在使用作弊。之后新建的存档都会默认开启作弊。\\n自动存档已停用，你也无法再获得任何成就。",
 "DPAD_LR_TXT": "方向键左/右",
 "DPAD_TXT": "方向键",
 "HUD_PICKUP_UNNAMED_OBJECT": "拾取",
 "HUD_PICK_UP_MESSAGE": "拾取  %ls",
 "HUD_REPLACE_BLANK_WITH_BLANK_DUAL_WIELD_MESSAGE": "将双持 %ls 与双持 %ls 互换",
 "HUD_REPLACE_BLANK_WITH_BLANK_MESSAGE": "将 %ls 与 %ls 互换",
 "MAINMENU_LOGIN_CHANGE_EXPOSITION": "登录状态发生了变化。你已返回标题画面。",
 "MAINMENU_NEW_GAME_NO_SPACE_EXPOSITION": "硬盘没有新建存档所需的 %dKB 空间。\\n若继续，你将无法保存新游戏。\\n要继续吗？",
 "MAINMENU_SYSLINK": "局域网联机",
 "MPPreGame": "位于多人游戏赛前大厅",
 "MULTI_GAMETYPE_1": "系统连线",
 "MULTI_LOST_CONNECTION": "发生网络错误。\\n正在返回主菜单。",
 "MULTI_MATCHMAKING": "匹配中",
 "MULTI_MENU_SYSLINK": "局域网联机",
 "MULTI_MENU_SYSLINK_SIGN_IN_FULL": "你必须登录玩家档案才能使用系统连线。",
 "OPT_CONTROL_OVERRIDE_FEEDBACK_TEXT": "你必须在玩家档案的个人设置中开启震动选项。",
 "PS3_AUTOSAVE_WARNING": "黑道圣徒会在游戏过程中以及存档后自动保存你的用户档案。\\n当硬盘访问指示灯闪烁时，切勿关闭电源。",
 "PS3_BAD_PROFILE": "你的用户档案已损坏。\\n正在重写损坏文件，请稍候。",
 "PS3_CACHE_WARNING": "缓存期间请勿关闭主机。\\n否则你的 PS3 可能会爆炸。\\n想想八年级科技展上的火山苏打喷发吧……",
 "PS3_DISC_ERROR": "游戏光盘错误。请检查游戏光盘并重启。\\n错误代码：0x%08x",
 "PS3_DISK_EJECT": "请重新插入《黑道圣徒》光盘。",
 "PS3_FREE_HDD_SPACE": "硬盘可用空间不足。\\n要建立数据，至少还需 %d MB 空间。\\n请退出游戏腾出所需空间。",
 "PS3_INVITE_TEXT": "你收到了一局游戏的邀请。",
 "PS3_INVITE_TEXT_CONFIRM": "%s 邀请你加入一局游戏。\\n按 {PAUSE_MENU_IMG} 接受。",
 "PS3_NO_SPACE_MANAGE_MEMORY": "管理存档数据",
 "PS3_NO_SPACE_START_ANYWAY": "不保存并继续",
 "PS3_NO_SPACE_TO_AUTOSAVE": "你的硬盘已没有空间保存游戏存档。如果不先清理系统存储空间就继续，你将无法保存新游戏。",
 "PS3_NO_SPACE_TO_SAVE_NEW_GAME": "硬盘空间不足，无法保存新游戏",
 "PS3_SAVE_SELECTION_MANAGE_MEMORY": "管理存档数据",
 "SAVELOAD_IMPORT_LOADING": "正在导入内容。\\n请不要关闭主机。",
 "SAVELOAD_LOADING_MESSAGE_EXPOSITION": "正在载入内容。请不要关闭主机。",
 "SAVELOAD_SAVE_DEVICE_UNAVAILABLE_FULL": "存档失败：默认\\n存储设备已不可用。",
 "SAVELOAD_SAVE_GAME_DEVICE_FULL_EXPOSITION": "默认存储设备的剩余空间不足，\\n无法完成存档。",
 "SAVELOAD_SAVING_MESSAGE_EXPOSITION": "正在保存内容。请不要关闭主机。 ",
 "STATS_CORRUPT_BODY": "你的统计数据已损坏，必须重置。",
 "TUT_TITLE_PS3_SIXAXIS": "[color:yellow]体感控制",
 "XBOX_NOT_SIGNED_IN": "未登录",
}

def apply(base):
    SCH = os.path.join(BASE, "schinese", base + "_us.txt")
    lines = open(SCH, encoding="utf-8").read().replace("\r\n", "\n").split("\n")
    out, seen, n = [], set(), 0
    for ln in lines:
        m = re.match(r'^\s*"([^"]+)":\s*"(.*)"\s*$', ln)
        if m and m.group(1) in T:
            k = m.group(1); out.append('"%s": "%s"' % (k, T[k])); seen.add(k); n += 1
        else:
            out.append(ln)
    open(SCH, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(base, "applied", n)

for b in ["platform_ps3", "platform_ps4", "platform_ps5", "platform_xb1", "platform_xbox360", "platform_xbs"]:
    apply(b)
