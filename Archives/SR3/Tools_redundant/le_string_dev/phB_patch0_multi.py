# -*- coding: utf-8 -*-
# Phase B: patch0 + multiplayer 未译占位补译
import re, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def apply(base, T):
    SCH = os.path.join(BASE, "schinese", base + "_us.txt")
    lines = open(SCH, encoding="utf-8").read().replace("\r\n", "\n").split("\n")
    out, seen = [], set()
    for ln in lines:
        m = re.match(r'^\s*"([^"]+)":\s*"(.*)"\s*$', ln)
        if m and m.group(1) in T:
            k = m.group(1); out.append('"%s": "%s"' % (k, T[k])); seen.add(k)
        else:
            out.append(ln)
    open(SCH, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(base, "written", len(T))

patch0 = {
 "DLC_STORE_PURCHASE_INSTALLED_CONFIRM": "这份可下载内容已安装并可使用。仍要继续购买吗？",
 "DLC_STORE_ITEM_SEASON_PASS": "[image:ui_text_star] 季票内容",
 "DLC_STORE_SEASON_PASS_PREREQ_CONFIRM": "这份可下载内容需安装季票后才可使用。仍要继续下载吗？",
 "UNL_DESC_COMMUNITY_ASSET02": "Volition 衬衫现已可以穿戴",
 "UNL_COMMUNITY_ASSET02": "道具 - Volition 衬衫",
 "UNL_COMMUNITY_ASSET01": "道具 - 圣徒成员衬衫",
 "UNL_DESC_COMMUNITY_ASSET01": "圣徒成员衬衫现已可以穿戴",
 "UNL_COMMUNITY_ASSET03": "道具 - 圣徒鸢尾花连帽衫",
 "UNL_DESC_COMMUNITY_ASSET03": "圣徒鸢尾花连帽衫现已可以穿戴",
}
multi = {
 "MULTI_ERROR_HOST_LEFT": "主机已离开游戏。",
 "COOP_LOAD_EXPLANATION_TEXT": "将从另一个存档载入角色、服装与载具",
 "COOP_CANT_JOIN_YET_MISSION": "%ls 在你执行任务期间无法加入。\\n等你回到正常游玩后他们就会加入。",
 "MULTI_NO_PRIVILEGE_PS3": "该档案无法进行在线游玩。",
 "COOP_JOIN_REQUEST_MESSAGE": "%ls 想加入你的游戏。",
 "CONTROL_KICK_PLAYER": "踢出玩家  ",
 "MULTI_ONLINE_RESTRICTED": "无法联机",
 "FOREIGN_CHARACTER_DETECTED_WARNING_MESSAGE": "有玩家的 ID 含不受支持的字符。这些字符现显示为“_”。",
 "MULTI_ERROR_NETWORK_TROUBLE": "主机已离开游戏。",
 "COOP_CANT_JOIN_YET_ACTIVITY": "%ls 在你执行活动期间无法加入。\\n等你回到正常游玩后他们就会加入。",
 "COOP_JOIN_IS_WAITING": "%ls 正在等待加入。",
 "COOP_LOAD_TITLE_TEXT": "载入角色",
 "MULTI_ERROR_INTERNAL_ERROR": "你已与游戏断开连接。",
 "MULTI_JOIN_FRIEND": "加入好友",
 "MULTI_CHAT_DISABLED_TITLE": "无法联机",
 "COOP_JOIN_REQUEST_TITLE": "加入请求",
 "COOP_PARTNER_DISCONNECTED": "与 %ls 的连接已断开",
 "COOP_CANT_JOIN_YET_CUTSCENE": "%ls 在你观看过场动画期间无法加入。\\n等你回到正常游玩后他们就会加入。",
 "COOP_CANT_JOIN_YET_PROLOGUE": "必须先重开此任务，%ls 才能立即加入你的游戏。重开会丢失本任务当前检查点之后的所有进度。\\n仍要让他加入吗？",
 "MULTI_ERROR_SESSION_ERROR": "无法加入该游戏。",
 "MULTI_NO_ETHERNET": "发生连接错误。\\n正在将你返回主菜单。",
 "MULTI_ERROR_NO_JOIN_INFO": "无法加入该游戏。",
 "MP_JOIN_ACCEPT": "接受",
 "COOP_WAIT": "请稍候...",
 "MULTI_JOIN_DELAY_MSG": "你试图加入的游戏正处于活动、任务或根据地中。\\n等他们退出该模式后你即可加入。",
 "MULTI_CHAT_DISABLED": "无法聊天",
 "MULTI_LOST_CONNECTION_SYS": "发生连接错误。\\n正在将你返回主菜单。",
 "MP_INVITE_SENDING": "正在发送邀请...",
 "MP_INVITE_SENT_BODY": "游戏邀请已发送给 {0}。  ",
 "MULTI_NOT_SIGNED_IN": "你必须登录玩家档案才能使用此功能。",
}
apply("patch0", patch0)
apply("multiplayer", multi)
