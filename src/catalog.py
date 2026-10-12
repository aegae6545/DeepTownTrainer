# -*- coding: utf-8 -*-
"""
可修改项清单: 分类 + 中文名 + 关键词

2026-10-08 修正: 旧清单里 `techLevel` / `currentSegment` 这两个字段**在 SaveData 里根本不存在**
（currentSegment 实际在 SaveData.mineData 里，是嵌套字段），所以「技术水平 / 挖掘进度」
点了没反应。现在:
  · 常用组逐项对着真实字段核对过
  · 高级组 = 直接从 SaveData 的 58 个数值字段生成，保证每一项都存在
"""
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 常用
# (中文名, SaveData 字段路径, 关键词, struct, 图标, 提示)
COMMON = [
    ('广告：屏蔽 + 跳过领奖', '__ADS__', 'ad ads 广告 视频 激励 跳过 屏蔽 领奖', 'ads', '📺',
     '彻底屏蔽插屏广告；"看广告得奖励"变成点一下就领'),
    ('倒计时：制作 / 远征 立即完成', '__TIMERS__',
     'timer countdown 倒计时 制作 工艺 冶炼 生产 远征 探险 部署 商店 冷却 立即完成 加速', 'exp', '⏱',
     '制作/冶炼/远征/部署的计时都在同一个墙钟调度器里，全局变速对它们无效；这里能直接归零或加速'),
    ('机制杀助手：街区打不动伤害', '__MECH__',
     'mechanic 机制 免伤 免疫 打不动 没伤害 卡关 boss 长老 熔岩 岩浆 lava 铁长老 铝长老 '
     'hitMod 冻结 冷冻 cryo 技术研究所 前置条件', 'exp', '🧩',
     '有些街区设计上不吃伤害（伤害 = 原始 × getHitMod()，免疫街区恒为 0）；'
     '这里自动完成前置条件并列出做了什么'),
    ('金币', 'cash', 'gold coin money 金 钱 金条', 'i4b', '💰', '底部/顶部金币数量'),
    ('钻石 / 水晶', 'crystals', 'diamond crystal gem 宝石 水晶 钻', 'i4a', '💎', '顶部绿色宝石数量'),
    ('玩家等级', 'currentLevel', 'level lv 等级 级别', 'plain', '⭐', '头像旁的等级数字'),
    ('技能点', 'spellPoints', 'spell point skill 技能 法术 点', 'plain', '✨', '技能/法术可用点数'),
    ('技能冷却全部归零', '__SPELL_CD__', 'spell cooldown 冷却 CD 技能 法术', 'spell', '⚡',
     '所有已解锁法术冷却改为指定秒数'),
    ('挖掘进度', 'mineData.currentSegment', 'mine segment depth 深度 层 进度 街区 挖掘', 'plain', '⛏',
     '当前挖掘到的街区/层数（SaveData.mineData.currentSegment）'),
    ('游戏内时间', 'gameplayTime', 'time 游戏时间 本次', 'plain', '⏱', '本次会话累计秒数（float）'),
    ('总游戏时间', 'overallGameplayTime', 'time 总时长 累计', 'plain', '🕐', '历史累计秒数（float）'),
    ('每日礼物进度', 'dailyGiftProgress', 'daily gift 每日 礼物', 'plain', '🎁', '每日礼物进度'),
    ('每日礼物天数', 'dailyGiftDay', 'daily gift day 天数', 'plain', '📅', '已领到第几天'),
    ('免费金币次数', 'shopFreeCoinsRewardedCount', 'free coin 免费金币 次数', 'plain', '🪙',
     '商店看广告领金币的次数（5 次为满）'),
    ('免费宝石进度', 'shopFreeGemsProgress', 'free gem 免费宝石 进度', 'plain', '🔮',
     '商店看广告领宝石的进度（5 次为满）'),
    ('全矿场速度倍率', 'allMiningBuildingSpeedMultilier', 'speed mining 速度 倍率 加速', 'plain', '🚀',
     '所有采矿建筑的速度倍率（float，默认 2）'),
    ('作弊标记', 'isCheater', 'cheat cheater 作弊 反作弊', 'plain', '🚨',
     '反作弊标记，改成 0 可清掉（改数值别太离谱就不会被标）'),
]

# ---------------------------------------------------------------- 高级
# SaveData 里**真实存在**的全部数值字段（58 个）。没有中文名的直接用字段名。
ADVANCED_LABELS = {
    'allMiningBuildingSpeedMultilier': '全矿场速度倍率',
    'basicChanceBadChance': '基础概率参数',
    'botAutoClaimStartTime': '机器人自动收取-开始时间',
    'botClaimWithTimerStartTime': '机器人定时收取-开始时间',
    'churnGiftCount': '流失礼物次数',
    'churnGiftTime': '流失礼物时间',
    'conversationIndex': '对话进度索引',
    'countOfVirstVisitedAsteroid': '首次访问小行星计数',
    'currentDailyQuestInt': '当前每日任务编号',
    'currentLevel': '玩家等级',
    'dailyGiftChristmasBookAttempts': '圣诞书尝试次数',
    'dailyGiftClaimedDay': '每日礼物已领日',
    'dailyGiftDay': '每日礼物天数',
    'dailyGiftLastClaimTime': '每日礼物上次领取时间',
    'dailyGiftProgress': '每日礼物进度',
    'dailySavedLoadLastTime': '存档上次载入时间',
    'dataVersion': '存档数据版本',
    'dollarSpent': '累计消费(美元)',
    'donateCheatLastTime': '捐赠作弊检测时间',
    'donateCounter': '捐赠/帮助次数',
    'elevatorUsed': '电梯使用次数',
    'expeditionTimeCount': '探险次数',
    'gameplayTime': '本次游戏时间',
    'installTime': '安装时间戳',
    'introShown': '已看过开场',
    'isCheater': '作弊标记',
    'isGDPRConsentReceived': 'GDPR 同意',
    'isIronBossBlockesRecovered': '铁BOSS方块已恢复',
    'isMilestone20m': '里程碑 20 分钟',
    'isMilestone2h': '里程碑 2 小时',
    'isMusicON': '音乐开关',
    'isNotificatinsON': '通知开关',
    'isPersonalizedAdsOn': '个性化广告开关',
    'isSoundON': '音效开关',
    'isTime1HD0': '时间标记 1HD0',
    'isTime2HD0': '时间标记 2HD0',
    'lastIngame': '上次在线时间戳',
    'lastRangeLogged': '上次记录区间',
    'lastReportedGameplayTime': '上次上报的游戏时间',
    'lastSaved': '上次保存时间戳',
    'miningClaimHelps': '挖矿帮助领取次数',
    'mixPanel': 'MixPanel 计数',
    'notificationData': '通知数据',
    'overallGameplayTime': '总游戏时间',
    'playerNameChangeCount': '改名次数',
    'rockerMissionDone': '摇滚任务完成',
    'seed': '随机种子（⚠ 改动可能影响世界生成）',
    'shopFreeCoinsRewardedCount': '免费金币次数',
    'shopFreeCrystalsClaimTime': '免费宝石领取时间',
    'shopFreeGemsProgress': '免费宝石进度',
    'signInEventSent': '签到事件已发送',
    'spellPoints': '技能点',
    'swipeUsed': '滑动使用次数',
    'tutorialCompleteTime': '教程完成时间',
    'tutorialState': '教程状态',
    'tutorialType': '教程类型',
    'wasCheaterDialogShown': '作弊弹窗已显示',
    'wrongTimeCount': '时间异常次数',
}

# 从 SaveData 实测 dump 得到的数值字段全集（保持稳定，不依赖运行时）
SAVE_SCALARS = [
    'allMiningBuildingSpeedMultilier', 'basicChanceBadChance', 'botAutoClaimStartTime',
    'botClaimWithTimerStartTime', 'churnGiftCount', 'churnGiftTime', 'conversationIndex',
    'countOfVirstVisitedAsteroid', 'currentDailyQuestInt', 'currentLevel',
    'dailyGiftChristmasBookAttempts', 'dailyGiftClaimedDay', 'dailyGiftDay',
    'dailyGiftLastClaimTime', 'dailyGiftProgress', 'dailySavedLoadLastTime', 'dataVersion',
    'dollarSpent', 'donateCheatLastTime', 'donateCounter', 'elevatorUsed',
    'expeditionTimeCount', 'gameplayTime', 'installTime', 'introShown', 'isCheater',
    'isGDPRConsentReceived', 'isIronBossBlockesRecovered', 'isMilestone20m', 'isMilestone2h',
    'isMusicON', 'isNotificatinsON', 'isPersonalizedAdsOn', 'isSoundON', 'isTime1HD0',
    'isTime2HD0', 'lastIngame', 'lastRangeLogged', 'lastReportedGameplayTime', 'lastSaved',
    'miningClaimHelps', 'mixPanel', 'notificationData', 'overallGameplayTime',
    'playerNameChangeCount', 'rockerMissionDone', 'seed', 'shopFreeCoinsRewardedCount',
    'shopFreeCrystalsClaimTime', 'shopFreeGemsProgress', 'signInEventSent', 'spellPoints',
    'swipeUsed', 'tutorialCompleteTime', 'tutorialState', 'tutorialType',
    'wasCheaterDialogShown', 'wrongTimeCount',
]

# 已经在「常用」里出现过的，不再重复到「高级」
_COMMON_FIELDS = set(x[1] for x in COMMON)


def build():
    items = []
    for name, field, kw, struct, icon, hint in COMMON:
        items.append({'group': '常用', 'name': name, 'field': field, 'kw': kw,
                      'struct': struct, 'icon': icon, 'hint': hint})
    # 资源
    mp = os.path.join(HERE, 'materials_cn.json')
    if os.path.exists(mp):
        for en, cn in json.load(open(mp, encoding='utf-8')):
            if not cn:
                continue
            items.append({'group': '资源', 'name': cn, 'field': en,
                          'kw': '%s %s material resource 资源 材料' % (cn, en),
                          'struct': 'i4a', 'icon': '🪨', 'hint': '仓库里的资源数量'})
    # 高级
    for f in SAVE_SCALARS:
        if f in _COMMON_FIELDS:
            continue
        label = ADVANCED_LABELS.get(f, f)
        items.append({'group': '高级', 'name': '%s（%s）' % (label, f), 'field': f,
                      'kw': label + ' ' + f, 'struct': 'plain', 'icon': '🔧',
                      'hint': '存档字段 %s' % f})
    return items


ITEMS = build()
GROUPS = ['全部'] + sorted(set(i['group'] for i in ITEMS))


def search(text, group='全部'):
    text = (text or '').strip().lower()
    out = []
    for it in ITEMS:
        if group != '全部' and it['group'] != group:
            continue
        if not text:
            out.append(it); continue
        hay = (it['name'] + ' ' + it['field'] + ' ' + it['kw']).lower()
        if text in hay:
            out.append(it)
    if text:
        out.sort(key=lambda i: (0 if i['name'].lower() == text else
                                (1 if i['name'].lower().startswith(text) else 2)))
    return out
