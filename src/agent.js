/*
 * 深城修改器 · Frida Agent  (v4)
 * ------------------------------------------------------------
 * 走 Java 层直接读写游戏对象 —— 不需要知道"当前值"也能定位字段。
 *
 * 关键类(未混淆):
 *   com.underwater.demolisher.data.vo.SaveData   —— 存档对象(单例, 135 字段)
 *   i4/b  {long b; long c; Object d;}            —— 金币容器, 真实值 = c - b
 *   i4/a  {int  b; int  c; Object d;}            —— 钻石 / 资源数量, 真实值 = c - b
 *   materials: HashMap<String, i4.a>             —— 资源名 -> 数量容器
 *
 * 广告: AppLovin MAX
 *   com.applovin.mediation.ads.MaxInterstitialAd / MaxRewardedAd
 *   s3.a$e —— 游戏自己的广告监听器(混淆类), 发奖门槛 = 观看 > 10 秒
 *
 * 变速: libGDX
 *   com.badlogic.gdx.backends.android.b.e()  ==  Graphics.getDeltaTime()  (float, 无参)
 */
'use strict';

var SAVE = 'com.underwater.demolisher.data.vo.SaveData';
var MAX_INT = 'com.applovin.mediation.ads.MaxInterstitialAd';
var MAX_RWD = 'com.applovin.mediation.ads.MaxRewardedAd';
var GDX_GRAPHICS = 'com.badlogic.gdx.backends.android.b';   // AndroidGraphics
var GDX_DELTA = 'e';                                        // getDeltaTime()
var PRIM = ['int', 'long', 'float', 'double', 'short', 'byte', 'boolean', 'char'];

var state = {
    adHook: false,
    blockInterstitial: true,
    skipRewarded: true,
    forceReady: true,
    rwdListeners: {},
    intListeners: {},
    gameListener: null,
    events: [],
    /* 变速 */
    speedHooked: false,
    speedOn: false,
    speedMult: 1.0,
    speedErr: null,
    speedFieldNames: null,
    deltaCalls: 0,
    rawDt: null,
    scaledDt: null,
    engineHooked: false,
    speedMode: null,
    /* 机制杀助手 */
    mechOn: false,
    mechDone: [],
    poolGuarded: false,
    poolFix: 0
};

function log(s) {
    state.events.push('' + s);
    if (state.events.length > 300) state.events.splice(0, 100);
    send('[ad] ' + s);
}

/* ---------------- 工具 ---------------- */

function num(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === 'number') return v;
    if (typeof v === 'boolean') return v ? 1 : 0;
    try { return parseInt(v.toString(), 10); } catch (e) { return null; }
}

/* 沿继承链找字段(getDeclaredField 不查父类, 而 EarthMineData 的字段在父类 MineData 里) */
function findField(obj, name) {
    var c = obj.getClass();
    while (c !== null) {
        try { return c.getDeclaredField(name); } catch (e) { }
        try { c = c.getSuperclass(); } catch (e) { c = null; }
    }
    throw new Error('No field ' + name);
}

function fieldGet(obj, name) {
    var f = findField(obj, name);
    f.setAccessible(true);
    var tn = f.getType().getName();
    if (tn === 'long') return Number(f.getLong(obj));
    if (tn === 'int') return f.getInt(obj);
    if (tn === 'short') return f.getShort(obj);
    if (tn === 'byte') return f.getByte(obj);
    if (tn === 'boolean') return f.getBoolean(obj) ? 1 : 0;
    if (tn === 'float') return f.getFloat(obj);
    if (tn === 'double') return f.getDouble(obj);
    if (tn === 'char') return f.getChar(obj);
    return f.get(obj);
}

function fieldSet(obj, name, val) {
    var f = findField(obj, name);
    f.setAccessible(true);
    var tn = f.getType().getName();
    if (tn === 'long') { f.setLong(obj, val); return Number(f.getLong(obj)); }
    if (tn === 'int') { f.setInt(obj, val | 0); return f.getInt(obj); }
    if (tn === 'short') { f.setShort(obj, val | 0); return f.getShort(obj); }
    if (tn === 'byte') { f.setByte(obj, val | 0); return f.getByte(obj); }
    if (tn === 'boolean') { f.setBoolean(obj, !!val); return f.getBoolean(obj) ? 1 : 0; }
    if (tn === 'float') { f.setFloat(obj, val); return f.getFloat(obj); }
    if (tn === 'double') { f.setDouble(obj, val); return f.getDouble(obj); }
    return 'unsupported type: ' + tn;
}

/* 字段类型(沿继承链) */
function fieldType(obj, name) {
    try { return findField(obj, name).getType().getName(); } catch (e) { return null; }
}

function withSave(fn) {
    var out = null;
    Java.perform(function () {
        Java.choose(SAVE, {
            onMatch: function (inst) {
                if (out === null) {
                    try { out = fn(inst); } catch (e) { out = { __err: '' + e }; }
                }
            },
            onComplete: function () { }
        });
    });
    return out;
}

/* 支持嵌套路径, 如 "mineData.currentSegment" */
function pathParent(inst, path) {
    var parts = path.split('.');
    var o = inst;
    for (var i = 0; i < parts.length - 1; i++) {
        o = fieldGet(o, parts[i]);
        if (o === null) throw new Error('路径中断: ' + parts.slice(0, i + 1).join('.'));
    }
    return { obj: o, leaf: parts[parts.length - 1] };
}

function matMap(inst) {
    return Java.cast(fieldGet(inst, 'materials'), Java.use('java.util.HashMap'));
}

/* ---------------- 广告钩子 ---------------- */

/* 统一"深度护栏": 只在外层做副作用; 深度 >3 直接返回, 防止 this.method() 万一递归把栈打爆。
 * (2026-10-10 实测: 本环境里 this.method() 有时会再进一次替换体, 所以不能裸调 —— 见变速那节注释) */
function guardDepth(impl) {
    var depth = 0;
    return function () {
        depth++;
        try {
            if (depth > 3) return undefined;
            return impl.apply(this, arguments);
        } finally { depth--; }
    };
}

function hookAllShowAd(cls, handler) {
    var n = 0;
    cls.showAd.overloads.forEach(function (ov) {
        ov.implementation = guardDepth(function () {
            var args = Array.prototype.slice.call(arguments);
            var skip = handler(this, args);
            if (!skip) ov.apply(this, args);
        });
        n++;
    });
    return n;
}

function findWrapper(impl) {
    if (!impl) return null;
    try { return fieldGet(impl, 'listenerWrapper') || null; } catch (e) { }
    return null;
}

function asIface(o, iface) {
    if (!o) return null;
    try {
        var IF = Java.use(iface);
        if (IF.class.isInstance(o)) return Java.cast(o, IF);
    } catch (e) { }
    return null;
}

function fireOnGameListener() {
    var g = state.gameListener;
    if (!g) return 'no game listener';
    var A = asIface(g, 'com.applovin.mediation.MaxAdListener');
    var V = asIface(g, 'com.applovin.mediation.MaxRewardedAdListener');
    var r = [];
    function t(tag, fn) { try { fn(); r.push(tag + ' ok'); } catch (e) { r.push(tag + ' X ' + e); } }
    if (A) t('onAdDisplayed', function () { A.onAdDisplayed(null); });
    if (V) t('onRewardedVideoStarted', function () { V.onRewardedVideoStarted(null); });
    if (V) t('onUserRewarded', function () { V.onUserRewarded(null, null); });
    if (V) t('onRewardedVideoCompleted', function () { V.onRewardedVideoCompleted(null); });
    if (A) t('onAdHidden', function () { A.onAdHidden(null); });
    return r.join(' | ');
}

function hookGameListener() {
    try {
        var E = Java.use('s3.a$e');
        var ovsDepth = 0;
        E.onRewardedVideoStarted.implementation = guardDepth(function (ad) {
            ovsDepth++;
            try {
                if (ovsDepth === 1) {
                    try { fieldSet(this, 'b', Date.now() - 60000); log('已回拨观看起始时间 (b)'); }
                    catch (e) { log('回拨失败 ' + e); }
                }
                return this.onRewardedVideoStarted(ad);
            } finally { ovsDepth--; }
        });
        Java.choose('s3.a$e', {
            onMatch: function (o) { if (!state.gameListener) state.gameListener = o; },
            onComplete: function () { }
        });
        log('游戏广告监听器已挂钩 (listener=' + (state.gameListener ? '已抓到' : '未抓到') + ')');
        return true;
    } catch (e) {
        log('游戏广告监听器挂钩失败: ' + e);
        return false;
    }
}

function installAdHooks() {
    Java.perform(function () {
        if (state.adHook) return;
        var R = Java.use(MAX_RWD);
        R.setListener.implementation = guardDepth(function (l) {
            try { state.rwdListeners[this.getAdUnitId()] = l; log('捕获激励监听器 ' + this.getAdUnitId()); }
            catch (e) { log('setListener 捕获失败 ' + e); }
            return this.setListener(l);
        });
        R.isReady.implementation = guardDepth(function () {
            return state.forceReady ? true : this.isReady();
        });
        hookAllShowAd(R, function (self, args) {
            var id = null;
            try { id = self.getAdUnitId(); } catch (e) { }
            var w = state.rwdListeners[id] || null;
            if (!w) { try { w = findWrapper(fieldGet(self, 'd')); } catch (e) { } }
            log('激励 showAd(' + args.length + '参) unit=' + id + ' listener=' + (w ? '有' : '无'));
            if (!state.skipRewarded) return false;
            setTimeout(function () {
                Java.perform(function () { log('  ' + fireOnGameListener()); });
            }, 80);
            return true;
        });

        var I = Java.use(MAX_INT);
        I.setListener.implementation = guardDepth(function (l) {
            try { state.intListeners[this.getAdUnitId()] = l; } catch (e) { }
            return this.setListener(l);
        });
        hookAllShowAd(I, function (self, args) {
            var id = null;
            try { id = self.getAdUnitId(); } catch (e) { }
            log('插屏 showAd 已拦截 unit=' + id);
            if (!state.blockInterstitial) return false;
            var w = state.intListeners[id] || null;
            if (!w) { try { w = findWrapper(fieldGet(self, 'b')); } catch (e) { } }
            var A = asIface(w, 'com.applovin.mediation.MaxAdListener');
            if (A) {
                setTimeout(function () {
                    Java.perform(function () {
                        try { A.onAdDisplayed(null); } catch (e) { }
                        try { A.onAdHidden(null); } catch (e) { }
                    });
                }, 60);
            }
            return true;
        });
        state.adHook = true;
        hookGameListener();
        log('广告钩子已安装');
    });
}

/* ---------------- 变速（2026-10-10 重写） ----------------
 * 做法: 钩 `com.badlogic.ashley.core.e.p(float)` —— Ashley 的 `Engine.update(deltaTime)`，
 *       实测每秒 60 次；游戏**所有** ECS 系统（采矿 / 生产 / 机器人 / 动画 / Actions）
 *       都从这里取时间片。把 dt 乘倍率再转交原方法，整个游戏逻辑的时间流速就跟着变。
 *
 * ⚠ 为什么不再用「钩 AndroidGraphics.onDrawFrame + 回拨 lastFrameTime」：
 *   2026-10-10 实测（frida 16.7.19 + MuMu12/ART）——
 *   替换体里的 `this.onDrawFrame(gl)` **不会**调到原方法，而是再进一次替换体（深度 2），
 *   而且原方法始终不执行 ⇒ 渲染完全停止：
 *       deltaTime 冻结在同一值、gameplayTime 3 秒推进 0.000 秒（游戏逻辑停摆）。
 *   连"空钩子"（`function(gl){ return this.onDrawFrame(gl); }`，不做任何事）都会冻住游戏：
 *       无钩子  -> gameplayTime 3 秒推进 2.980 秒 ✔
 *       空钩子  -> gameplayTime 3 秒推进 0.000 秒 ✘
 *   所以那条路在本环境不可用（早期文档里它时灵时不灵：灵的时候是 ART 恰好把调用点解析成
 *   原方法；不灵时轻则变速无效、重则冻住游戏或 GL 线程栈溢出崩溃 —— 14:34 那次崩溃就是它）。
 *
 *   现在只用 Engine.update：确定性生效，而且**不再需要**按"值特征"猜混淆字段，
 *   顺带干掉了 `if (lv > 1e12)` 那个"模拟器刚重启就认不出 lastFrameTime"的老坑。
 *
 *   代价：`Gdx.graphics.getDeltaTime()` 本身不再被放大，所以 `SaveData.gameplayTime`
 *   这类"非 ECS 累加器"不会跟着加速；但采矿/生产/机器人/动画这些真正在玩的逻辑全在 ECS 里。
 *
 * ⚠ 绝对不要 hook System.nanoTime() —— ART 内部也在用，替换会让游戏直接崩溃（实测踩过）。
 */
var ENGINE = 'com.badlogic.ashley.core.e';
var ENGINE_UPDATE = 'p';

function installSpeedHook() {
    if (state.engineHooked) return true;
    Java.perform(function () {
        try {
            var E = Java.use(ENGINE);
            E[ENGINE_UPDATE].implementation = function (dt) {
                state.rawDt = dt;
                if (state.speedOn && state.speedMult !== 1.0) {
                    state.deltaCalls++;
                    state.scaledDt = dt * state.speedMult;
                    return this[ENGINE_UPDATE](dt * state.speedMult);
                }
                state.scaledDt = dt;
                return this[ENGINE_UPDATE](dt);
            };
            state.engineHooked = true;
            state.speedHooked = true;
            state.speedMode = 'engine';
            state.speedFieldNames = ENGINE + '.' + ENGINE_UPDATE + '(dt)';
            state.speedErr = null;
        } catch (e) { state.speedErr = 'Engine.update 挂钩失败: ' + e; }
    });
    return state.speedHooked;
}

/* 兼容旧名 */
function installEngineSpeedHook() { return installSpeedHook(); }

/* 读当前真实 deltaTime（诊断用） */
/* 诊断用: 最近一次交给 ECS 的时间片(缩放后) */
function readDelta() { return state.scaledDt; }

/* ---------------- 通用倒计时(调度器任务) ----------------
 * 结论(2026-10-10 实测 5.7.8 vc398) —— 深城里**所有"要等"的计时**都走同一个调度器,
 * 存的是**绝对墙钟 endTime**, 不是 deltaTime:
 *
 *   SaveData.universalScheduler -> a7.b   (通用调度器)
 *       b:double = cursorTime  游戏内游标(秒)
 *       c:double = wallNow     = System.currentTimeMillis() / 1000   ← 真·墙钟
 *       任务 a7/b$b: b=key(String)  c=endTime(绝对墙钟秒)  d=回调(a7.a)
 *          创建时 endTime = b + duration        (b(String,int,a7.a))
 *       i(id):float = endTime - wallNow          ← 界面上那个 00:02:00 就是它
 *       r(id,float) = 把剩余时间改成 float        (r 内部 = endTime += float - i(id))
 *       w(float dt) = 每帧 tick: b+=dt; c=墙钟; 追赶到期 -> m() -> n() -> d()(回调) -> 移除任务
 *
 *   实测任务表(2026-10-10):
 *     制作/冶炼  <建筑uID>recipie_<槽位>   回调 CraftingBuildingScript / SmeltingBuildingScript
 *     远征      <建筑uID>start-expedition  回调 CaveExpeditionBuildingScript
 *     建筑部署  <建筑uID>deploy            回调 logic.building.a
 *     增益/加速 boost<uID> / boost_*_cooldown_key
 *     机器人    bot_system_action_timer_N
 *     商店/礼包 *_mini_offer_cooldown_key / FREE_COINS_COOLDOWN_NAME / special_pack / OFFLINE_RAIN_KEY
 *
 *   ⇒ 放大 deltaTime 的「游戏变速」对这些**全部无效** ——
 *     只有动画(帧驱动)在跑快, 计时一秒还是一秒。这就是
 *     "工艺制作只加快动画、不加快制作时间" 的原因。
 *
 *   要让计时真的变快, 只能改 endTime:
 *     「立即完成」= a7.b.r(key, 0)  -> 下一帧游戏自己判定到期 -> 回调 -> 完成 + 任务移除
 *     「加速」    = 每 250ms 按倍率把 endTime 往前推(游戏自己加速生产建筑就用 r 这个 API)
 *   走的都是官方到期路径, 不是硬塞状态。
 */
var SCHED_CLS = 'a7.b';
var EXP_BASE = 'com.underwater.demolisher.ui.dialogs.buildings.ExpeditionBuildingScript';
var EXP_KEY_SUFFIX = 'start-expedition';

var TIMER_KIND_CN = {
    production: '生产/制作', expedition: '远征', deploy: '建筑部署',
    boost: '增益/加速', bot: '机器人', shop: '商店/礼包', other: '其它'
};

var timerAccel = { on: false, mult: 1, kinds: null, timer: null, last: 0, ticks: 0, keys: 0, err: null };

/* 按 key 归类 */
function timerKind(key) {
    var k = String(key);
    if (k.indexOf('recipie_') >= 0) return 'production';
    if (k.indexOf(EXP_KEY_SUFFIX) >= 0) return 'expedition';
    if (k.length > 6 && k.slice(-6) === 'deploy') return 'deploy';
    if (k.indexOf('boost') === 0) return 'boost';
    if (k.indexOf('bot_system_action_timer') === 0) return 'bot';
    if (k.indexOf('mini_offer') >= 0 || k.indexOf('cooldown') >= 0 ||
        k.indexOf('FREE_COINS') >= 0 || k.indexOf('OFFLINE_RAIN') >= 0 ||
        k.indexOf('special_pack') >= 0) return 'shop';
    return 'other';
}

function kindWanted(kind, kinds) {
    if (!kinds || !kinds.length) return true;
    for (var i = 0; i < kinds.length; i++) if (kinds[i] === kind) return true;
    return false;
}

/* 把外部传进来的类别列表规范化: null / 空 / 'all' -> null(全部) */
function normKinds(kinds) {
    if (!kinds) return null;
    if (typeof kinds === 'string') kinds = kinds.split(',');
    var out = [];
    for (var i = 0; i < kinds.length; i++) {
        var k = String(kinds[i]).replace(/\s/g, '');
        if (k === 'all' || k === '') continue;
        if (TIMER_KIND_CN[k]) out.push(k);
    }
    return out.length ? out : null;
}

/* 取 SaveData.universalScheduler (必须在 Java 上下文里调) */
function schedInst() {
    var s = null;
    Java.choose(SAVE, {
        onMatch: function (inst) { if (!s) { try { s = fieldGet(inst, 'universalScheduler'); } catch (e) { } } },
        onComplete: function () { }
    });
    return s;
}

/* 调度器里的全部任务 key (kinds 为 null/空 = 不过滤) */
function allTaskKeys(s, kinds) {
    var out = [];
    try {
        var HM = Java.use('java.util.HashMap');
        var h = Java.cast(fieldGet(s, 'f'), HM);
        var it = h.keySet().iterator();
        var n = 0;
        while (it.hasNext() && n < 800) {
            var k = it.next().toString();
            if (kindWanted(timerKind(k), kinds)) out.push(k);
            n++;
        }
    } catch (e) { }
    return out;
}

/* 向后兼容: 只要远征任务 */
function expTaskKeys(s) {
    return allTaskKeys(s, ['expedition']);
}

function jstr(o) {
    if (o === null || o === undefined) return null;
    try { return o.toString(); } catch (e) { return null; }
}

function timersList(kinds) {
    var out = { now: Date.now() / 1000, tasks: [], scripts: [], kinds: TIMER_KIND_CN, err: null };
    Java.perform(function () {
        try {
            var s = schedInst();
            if (!s) { out.err = '找不到 universalScheduler（智能通道连上了吗？）'; }
            else {
                var sch = Java.cast(s, Java.use(SCHED_CLS));
                var HM = Java.use('java.util.HashMap');
                var h = Java.cast(fieldGet(s, 'f'), HM);
                var it = h.keySet().iterator();
                var n = 0;
                while (it.hasNext() && n < 800) {
                    var k = it.next().toString();
                    n++;
                    var kind = timerKind(k);
                    if (!kindWanted(kind, kinds)) continue;
                    var rem = sch.i(k);
                    var cb = null;
                    try { cb = jstr(fieldGet(h.get(k), 'd')); } catch (e) { }
                    var rec = { key: k, kind: kind, cb: cb, remaining: rem,
                                endTime: rem + Date.now() / 1000, uID: null, slot: null };
                    if (kind === 'production') {
                        var m = /^(.*)recipie_(\d+)$/.exec(k);
                        if (m) { rec.uID = m[1]; rec.slot = parseInt(m[2], 10); }
                    } else if (kind === 'expedition') {
                        rec.uID = k.substring(0, k.length - EXP_KEY_SUFFIX.length);
                    } else if (kind === 'deploy') {
                        rec.uID = k.substring(0, k.length - 6);
                    }
                    out.tasks.push(rec);
                }
            }
        } catch (e) { out.err = '' + e; }
        try {
            Java.choose(EXP_BASE, {
                onMatch: function (o) {
                    try {
                        var rec = { cls: o.$className, j1: null, uID: null, caveTitle: null, state: null };
                        try { rec.j1 = o.j1().toString(); } catch (e) { }
                        try { rec.uID = jstr(fieldGet(fieldGet(o, 'g'), 'uID')); } catch (e) { }
                        try { rec.caveTitle = jstr(o.g1()); } catch (e) { }
                        try {
                            var st = fieldGet(o, 'S');
                            if (st) rec.state = {
                                inProgress: fieldGet(st, 'c') ? true : false,
                                expeditionId: jstr(fieldGet(st, 'd')),
                                itemToClaim: jstr(fieldGet(st, 'e')),
                                order: num(fieldGet(st, 'f'))
                            };
                        } catch (e) { }
                        out.scripts.push(rec);
                    } catch (e) { out.scripts.push({ err: '' + e }); }
                },
                onComplete: function () { }
            });
        } catch (e) { out.err = (out.err || '') + ' | scripts: ' + e; }
    });
    return out;
}

/* 立即完成: 把剩余时间清零(等价于倒计时自然走完)
 * 主路径 = a7.b.r(key, 0) -> 下一帧调度器判定到期 -> 回调 -> 完成 + 任务移除
 * 兜底   = 调度器里没有远征任务时, 直接调建筑脚本的 b(j1()) (游戏自己的完成入口) */
function timerFinish(key, kinds) {
    var out = { done: [], via: null, err: null };
    Java.perform(function () {
        try {
            var s = schedInst();
            if (s) {
                var sch = Java.cast(s, Java.use(SCHED_CLS));
                var keys = key ? [key] : allTaskKeys(s, kinds);
                for (var i = 0; i < keys.length; i++) {
                    var k = keys[i];
                    if (!sch.e(k)) continue;
                    var before = sch.i(k);
                    sch.r(k, 0);
                    out.done.push({ key: k, kind: timerKind(k), before: before, after: sch.i(k) });
                }
                if (out.done.length) out.via = 'scheduler.r(key,0)';
            }
        } catch (e) { out.err = '' + e; }

        /* 兜底只针对远征: 调度器里没任务但远征脚本还标着进行中 */
        if (!out.done.length && (!kinds || kindWanted('expedition', kinds))) {
            try {
                Java.choose(EXP_BASE, {
                    onMatch: function (o) {
                        try {
                            var st = fieldGet(o, 'S');
                            if (!st) return;
                            if (!fieldGet(st, 'c')) return;          // 不在进行中
                            var id = o.j1().toString();
                            if (key && id !== key) return;
                            o.b(id);                                  // == 倒计时到期的回调
                            out.done.push({ key: id, kind: 'expedition', before: -1, after: 0 });
                        } catch (e) { out.err = '' + e; }
                    },
                    onComplete: function () { }
                });
                if (out.done.length) out.via = 'script.b(j1())';
            } catch (e) { out.err = (out.err || '') + ' | fallback: ' + e; }
        }
        if (!out.done.length && !out.err) out.err = '当前筛选下没有进行中的倒计时';
    });
    return out;
}

/* 向后兼容: 只要远征 */
function expFinish(key) {
    return timerFinish(key, ['expedition']);
}

/* 领取已完成远征的奖励(等于游戏里点「领取」) */
function expClaim() {
    var out = { claimed: [], err: null };
    Java.perform(function () {
        try {
            Java.choose(EXP_BASE, {
                onMatch: function (o) {
                    try {
                        var st = fieldGet(o, 'S');
                        if (!st) return;
                        var item = jstr(fieldGet(st, 'e'));
                        if (!item) return;
                        o.b1();                       // ExpeditionBuildingScript.b1() = 领取
                        out.claimed.push({ cls: o.$className, item: item });
                    } catch (e) { out.claimed.push({ err: '' + e }); }
                },
                onComplete: function () { }
            });
        } catch (e) { out.err = '' + e; }
    });
    if (!out.claimed.length && !out.err) out.err = '没有可领取的远征奖励（先「立即完成」再领）';
    return out;
}

/* 远征加速: 每 250ms 把 endTime 按倍率往前推 —— 真实生效(与全局变速无关) */
function timerAccelTick() {
    try {
        Java.perform(function () {
            var s = schedInst();
            if (!s) { timerAccel.err = '找不到 universalScheduler'; return; }
            var sch = Java.cast(s, Java.use(SCHED_CLS));
            var keys = allTaskKeys(s, timerAccel.kinds);
            timerAccel.keys = keys.length;
            var now = Date.now() / 1000;
            var dt = timerAccel.last ? (now - timerAccel.last) : 0;
            timerAccel.last = now;
            if (dt <= 0 || dt > 10) return;
            var cut = dt * (timerAccel.mult - 1);
            for (var i = 0; i < keys.length; i++) {
                var k = keys[i];
                var rem = sch.i(k);
                if (rem > 0) sch.r(k, Math.max(0, rem - cut));
            }
            timerAccel.ticks++;
            timerAccel.err = null;
        });
    } catch (e) { timerAccel.err = '' + e; }
}

/* 兼容旧名 */
function expAccelTick() { timerAccelTick(); }

/* =====================================================================
 *  机制杀助手 (Mechanic Gate Helper)
 * =====================================================================
 * 有些街区在设计上「不吃伤害」，必须完成特定机制才能推进 —— 实机 + 反汇编确认:
 *
 *   u4.j  LavaBlock       熔岩岩块
 *         getHitMod(): c(row) 为假时强制 return 0   (内部 hitMod 字段其实是 1.0)
 *         免疫表    : [ice-cannon, miracle-gas, fire-cannon]
 *         正解      : 技术研究所(必须建在「上一段」) + 低温冷冻(耗 1 液态氮)
 *                     CryoFreezeScript.t() → ((techs.a)block).freeze()
 *                     → 写冻结标记 true + 清空免疫表 → getHitMod 恢复
 *         注意      : destroy() 会把冻结标记写回 false ⇒ 每块熔岩都要再冻一次
 *
 *   u4.i  IRON_BOSS       铁长老 (getType()=IRONBOSS, 地球最底 row 1079)
 *         getHitMod(): 恒 return 0(只有 P 标记影响动画), 免疫表为空
 *         正解      : 造完整枚火箭 + 发射剧情 → a5.e$y → u0() → U0() → D()
 *                     a5.e$a0 → B0() → S0() → ((u4.i)block).A()  ← 官方击败流程
 *
 *   u4.a  ALUMINIUM_BOSS  铝长老
 *         getHitMod(): 恒 return 0; 免疫表含 mining-laser/fire-cannon/...
 *         正解      : 剧情设计上就是「打不动、绕开它」(对话: 免疫的东西/能量护盾/离开它)
 *
 * 助手三件事:
 *   ① scan()    只读: 当前街区是什么 + 需要什么机制 + 前置状态(技研/液氮/标志位)
 *   ② fix()     按类型执行「游戏自己的」解锁动作; 认不出来的走旁路
 *   ③ restore() 撤掉旁路
 * ===================================================================== */

var BLK_BASE = 'com.underwater.demolisher.logic.blocks.a';
var BLK_LAVA = 'u4.j';                 // LavaBlock
var BLK_IRON = 'u4.i';                 // IRON_BOSS 铁长老
var BLK_ALUM = 'u4.a';                 // ALUMINIUM_BOSS 铝长老
var BLK_CORRUPT = 'com.underwater.demolisher.logic.blocks.c';
var TECHS_A = 'com.underwater.demolisher.logic.techs.a';

/* 已知机制表: 类名 -> { name, need, how } */
var MECH_TABLE = {
    'u4.j': { name: '熔岩岩块 LavaBlock',
              need: '技术研究所(建在上一段) + 低温冷冻(耗 1 液态氮)',
              how: 'freeze' },
    'u4.i': { name: '铁长老 IRON_BOSS',
              need: '终局剧情杀。原本要建 信号接收器 + 研究建筑 + 银河传送门 三栋建筑再走发射剧情；' +
                    '但一键完成会直接走存档级方案（调 a5.e.O0() 把 GALACTIC_MOVIE_SUCCEED_DONE ' +
                    '写进 SaveData.bulkConfig），不用再建建筑。⚠ 不能用伤害杀 —— 它是最底层街区, ' +
                    '打碎后背景渲染器 r4.b.d() 取不到下一层的 bgWall 纹理, 渲染线程直接 NPE 闪退',
              how: 'story' },
    'u4.a': { name: '铝长老 ALUMINIUM_BOSS',
              need: '剧情设计上打不动(对话提示"离开它")，官方没有击败流程',
              how: 'report' }
};

/* 当前街区记录(必须在 Java 上下文里调) */
function currentBlockRec() {
    var out = { found: false, err: null };
    try {
        var insts = [];
        Java.choose('r4.i', {
            onMatch: function (o) {
                try {
                    var seg = o.A();
                    if (seg > 0) insts.push(o);
                } catch (e) { }
            },
            onComplete: function () { }
        });
        if (!insts.length) { out.err = '找不到矿层实例(智能通道连上了吗？)'; return out; }
        var m = insts[0];
        out.found = true;
        out.seg = m.A();
        out.row = m.x();
        try { out.maxHp = '' + m.F(out.row); } catch (e) { }
        try { out.curHp = '' + m.E(out.row); } catch (e) { }
        var b = m.z();
        if (!b) { out.err = '当前没有街区对象'; return out; }
        out.blockRef = b;
        out.cls = b.$className;
        var B = Java.cast(b, Java.use(BLK_BASE));
        try { out.name = '' + B.getBlockName(); } catch (e) { }
        try { out.type = '' + B.getType(); } catch (e) { }
        try { out.hitMod = B.getHitMod(); } catch (e) { }
        try { out.innerHitMod = fieldGet(b, 'hitMod'); } catch (e) { }
        try {
            var im = Java.cast(B.getSpellImmunityList(), Java.use('com.badlogic.gdx.utils.a'));
            var arr = [];
            for (var i = 0; i < im.c; i++) arr.push('' + im.get(i));
            out.immunity = arr;
        } catch (e) { out.immunity = []; }
        try { out.isTechsA = Java.use(TECHS_A).class.isInstance(b); } catch (e) { }
        /* 已知机制归类 */
        var mech = MECH_TABLE[out.cls];
        if (!mech) {
            /* 基类名兜底: 铁长老 getType() 是 IRONBOSS */
            if (out.type === 'IRONBOSS') mech = MECH_TABLE['u4.i'];
        }
        out.mech = mech || null;
        /* LavaBlock 特有: 冻结标记 */
        if (out.cls === BLK_LAVA) {
            try {
                var L = Java.cast(b, Java.use(BLK_LAVA));
                out.frozen = L.b();
            } catch (e) { out.frozen = null; }
        }
        /* IRON_BOSS 特有标志位 */
        if (out.cls === BLK_IRON || out.type === 'IRONBOSS') {
            out.flags = {};
            ['M', 'O', 'P', 'R'].forEach(function (n) {
                try { out.flags[n] = fieldGet(b, n) ? true : false; } catch (e) { }
            });
            /* 终局前置清单 */
            out.endgame = endgameCheck();
        }
    } catch (e) { out.err = '' + e; }
    return out;
}

/* 无参方法调用(走反射, 避开混淆类里的重载歧义) */
function invokeNoArg(obj, name) {
    var c = obj.getClass();
    while (c !== null) {
        var ms = null;
        try { ms = c.getDeclaredMethods(); } catch (e) { }
        if (ms) {
            for (var i = 0; i < ms.length; i++) {
                if (ms[i].getName() === name && ms[i].getParameterTypes().length === 0) {
                    ms[i].setAccessible(true);
                    return ms[i].invoke(obj, []);
                }
            }
        }
        try { c = c.getSuperclass(); } catch (e) { c = null; }
    }
    throw new Error('找不到无参方法 ' + name);
}

/* ---- 旁路: 把免伤拆掉 ---- */
function installMechBypass() {
    var done = [];
    if (state.mechOn) return done;
    /* 0) 先装大数池护栏 —— 旁路会把命中路径打开, 必须先堵住游戏自身的池 bug */
    done.push(installBigPoolGuard());
    /* 1) getHitMod 恒 1.0 —— 覆盖声明它的 4 个类 */
    [BLK_BASE, BLK_ALUM, BLK_IRON, BLK_LAVA].forEach(function (cn) {
        try {
            var C = Java.use(cn);
            C.getHitMod.implementation = function () { return 1.0; };
            done.push('getHitMod → 1.0  [' + cn + ']');
        } catch (e) {
            done.push('getHitMod 挂钩失败 [' + cn + ']: ' + e);
        }
    });
    /* 2) isImmuneTo 恒 false —— 3 个重载 */
    try {
        var B = Java.use(BLK_BASE);
        var ovs = B.isImmuneTo.overloads;
        for (var i = 0; i < ovs.length; i++) {
            ovs[i].implementation = function () { return false; };
        }
        done.push('isImmuneTo → false  [' + ovs.length + ' 个重载]');
    } catch (e) {
        done.push('isImmuneTo 挂钩失败: ' + e);
    }
    /* 3) 当前街区对象的免疫表直接清空 */
    try {
        var rec = currentBlockRec();
        if (rec.found && rec.blockRef) {
            var im = Java.cast(Java.cast(rec.blockRef, Java.use(BLK_BASE)).getSpellImmunityList(),
                               Java.use('com.badlogic.gdx.utils.a'));
            im.clear();
            done.push('当前街区免疫表已清空 (原: [' + (rec.immunity || []).join(', ') + '])');
        }
    } catch (e) {
        done.push('清空免疫表失败: ' + e);
    }
    state.mechOn = true;
    state.mechDone = done;
    return done;
}

function removeMechBypass() {
    var done = [];
    if (!state.mechOn) return done;
    [BLK_BASE, BLK_ALUM, BLK_IRON, BLK_LAVA].forEach(function (cn) {
        try {
            var C = Java.use(cn);
            C.getHitMod.implementation = null;
            done.push('已还原 getHitMod  [' + cn + ']');
        } catch (e) { }
    });
    try {
        var B = Java.use(BLK_BASE);
        var ovs = B.isImmuneTo.overloads;
        for (var i = 0; i < ovs.length; i++) ovs[i].implementation = null;
        done.push('已还原 isImmuneTo');
    } catch (e) { }
    state.mechOn = false;
    state.mechDone = done;
    var pg = removeBigPoolGuard();
    if (pg) done.push(pg);
    return done;
}

/* ---- 大数对象池护栏 ----
 * 游戏 bug: z6.b.b() = pool.obtain(); obj.d = false;   —— obtain() 偶尔返回 null
 *   (z6.b 是 libGDX Pool 的单例; free 列表一旦被塞进 null / size 被并发搞乱, obtain 就吐 null)
 * 一旦为 null, 紧跟的 iput-boolean 直接 NPE → 闪退。实测崩溃栈:
 *   z6.b.b ← z6.a.d ← z6.a.w ← r4.c.i ← r4.i.F ← r4.a.F ← r4.a.g ← r4.i.l ← r4.i.U ← i5.v(法术)
 * 铁长老 hitMod 恒 0 时这条伤害路径永远走不到; 一旦旁路把 hitMod 拉成 1.0 就会踩到。
 * 护栏: 拿不到就新建一个 z6.a (构造后 d 默认 false, 与原方法语义一致)。
 * ⚠ 本环境里 this.method() 会再进一次替换体, 所以必须用深度判定。 */
function installBigPoolGuard() {
    if (state.poolGuarded) return '大数池护栏已装（跳过）';
    try {
        var ZB = Java.use('z6.b');
        var ZA = Java.use('z6.a');
        var depth = 0;
        ZB.b.implementation = function () {
            depth++;
            try {
                if (depth > 1) return ZA.$new();       // 原方法没被调到 → 直接给新的
                var r = this.b();
                if (r === null || r === undefined) {
                    state.poolFix++;
                    return ZA.$new();                  // 池吐 null → 兜底新建
                }
                return r;
            } catch (e) {
                try { return ZA.$new(); } catch (e2) { return null; }
            } finally { depth--; }
        };
        state.poolGuarded = true;
        return '已装大数对象池护栏（z6.b.b 返回 null 时兜底新建，避免伤害路径 NPE 闪退）';
    } catch (e) {
        return '大数池护栏安装失败: ' + e;
    }
}

function removeBigPoolGuard() {
    if (!state.poolGuarded) return null;
    try {
        Java.use('z6.b').b.implementation = null;
        state.poolGuarded = false;
        return '已撤掉大数池护栏';
    } catch (e) {
        return '撤掉大数池护栏失败: ' + e;
    }
}

/* ---- 终局（铁长老）前置清单 ----
 * 铁长老 u4.i 不吃伤害是设计; 它的官方击败流程在 a5.e (ECS 系统) 里:
 *   a5.e.m()     需要 receiver_building 存在(否则 building.a.y("receiver_building").get(0) 越界)
 *   a5.e$y.run() → u0() → U0() → u4.i.D()
 *   a5.e$a0.run() → B0() → S0() → u4.i.A()        ← 官方击败
 * 终局三栋建筑(全部 unlockSegment = 108):
 *   receiver_building        ← receiver-skeleton ×1      (铜钉1000 + 钛齿轮100 + 钢板100, 1h)
 *   research_building        ← radar ×1                  (磁铁15 + 光纤200 + 主板20, 3h)
 *   galactic_portal_building ← portal-for-mankind ×1     (传送门骨架1 + 大脑1 + 纳米锤1)
 * 另需终局剧情走完(发射), 结束后会写标记 GALACTIC_MOVIE_SUCCEED_DONE。
 * ⚠ 光造火箭(rocket-base-*)不够 —— 这三栋建筑没建, a5.e 根本不会启动。
 */
var ENDGAME = [
    { id: 'receiver_building',        cls: 'com.underwater.demolisher.logic.building.scripts.ReceiverBuildingScript',
      cn: '信号接收器', mat: 'receiver-skeleton',     matCn: '接收器骨架' },
    { id: 'research_building',        cls: 'com.underwater.demolisher.logic.building.scripts.ResearchBuildingScript',
      cn: '研究建筑',   mat: 'radar',                 matCn: '雷达' },
    { id: 'galactic_portal_building', cls: 'com.underwater.demolisher.logic.building.scripts.GalacticPortalBuildingScript',
      cn: '银河传送门', mat: 'portal-for-mankind',    matCn: '人类传送门' }
];

function matValueOf(inst, key) {
    try {
        var map = Java.cast(matMap(inst), Java.use('java.util.HashMap'));
        var v = map.get(key);
        if (v === null || v === undefined) return null;
        return Java.cast(v, Java.use('i4.a')).h();
    } catch (e) { return null; }
}

/* 只读: 终局前置清单 */
function endgameCheck() {
    var out = { buildings: [], missing: [], ready: false, movieDone: null, err: null };
    Java.perform(function () {
        for (var i = 0; i < ENDGAME.length; i++) {
            var e = ENDGAME[i];
            var n = 0;
            try {
                Java.choose(e.cls, { onMatch: function () { n++; }, onComplete: function () { } });
            } catch (err) { }
            var rec = { id: e.id, cn: e.cn, built: n, mat: e.mat, matCn: e.matCn, matHave: null };
            out.buildings.push(rec);
        }
        try {
            withSave(function (inst) {
                for (var i = 0; i < out.buildings.length; i++) {
                    out.buildings[i].matHave = matValueOf(inst, out.buildings[i].mat);
                }
                return true;
            });
        } catch (err) { out.err = '' + err; }
        for (var i = 0; i < out.buildings.length; i++) {
            var b = out.buildings[i];
            if (!b.built) {
                out.missing.push(b.cn + '（没建）' +
                                 (b.matHave ? '；材料已有 ' + b.matCn + '×' + b.matHave : '；材料也缺 ' + b.matCn));
            }
        }
        try {
            Java.choose('h4.d', {
                onMatch: function (o) {
                    if (out.movieDone !== null) return;
                    var C = o.getClass(), m = null, ms = null;
                    try { ms = C.getDeclaredMethods(); } catch (err) { }
                    if (ms) for (var i = 0; i < ms.length; i++) {
                        if (ms[i].getName() === 'w0' && ms[i].getParameterTypes().length === 1) { m = ms[i]; break; }
                    }
                    if (!m) return;
                    m.setAccessible(true);
                    out.movieDone = (String(m.invoke(o, ['GALACTIC_MOVIE_SUCCEED_DONE'])) === 'true');
                },
                onComplete: function () { }
            });
        } catch (err) { }
        out.ready = (out.missing.length === 0);
    });
    return out;
}

/* 一键补齐终局三件套材料（不动建筑；建筑要你自己在矿里放） */
function mechGrantEndgame() {
    var out = { granted: [], err: null };
    Java.perform(function () {
        var need = [];
        var chk = endgameCheck();
        for (var i = 0; i < chk.buildings.length; i++) {
            var b = chk.buildings[i];
            if (b.built) { out.granted.push(b.cn + ' 已经建好了，跳过'); continue; }
            need.push(b);
        }
        for (var i = 0; i < need.length; i++) {
            var b = need[i];
            try {
                var r = rpc.exports.setmaterial(b.mat, 1);
                if (r && r.ok) {
                    out.granted.push('已把 ' + b.matCn + '（' + b.mat + '）×1 放进仓库' +
                                     (r.created ? '［新建条目］' : '［覆盖原值］') +
                                     ' → 用来造 ' + b.cn);
                } else {
                    out.granted.push('写入 ' + b.mat + ' 失败: ' + ((r && r.err) || '?'));
                }
            } catch (e) { out.granted.push('写入 ' + b.mat + ' 异常: ' + e); }
        }
        if (!need.length) out.granted.push('终局三栋建筑都已建好，不需要补材料');
    });
    return out;
}

/* ---- 只读检测 ---- */
function mechScan() {
    var out = { err: null };
    Java.perform(function () {
        var rec = currentBlockRec();
        out = rec;
        if (!rec.found) return;
        /* 需要什么机制 / 是否已经可以打 */
        if (rec.hitMod === 0) {
            out.verdict = '免疫 —— 现在打它伤害恒为 0';
            out.action = rec.mech ? rec.mech.how : 'bypass';
        } else {
            out.verdict = '可以正常打 (hitMod = ' + rec.hitMod + ')';
            out.action = 'none';
        }
        /* 技研/液氮 前置状态(LavaBlock 用) */
        if (rec.cls === BLK_LAVA) {
            out.prereq = {};
            try {
                var found = [];
                Java.choose('com.underwater.demolisher.logic.building.scripts.TechLabBuildingScript', {
                    onMatch: function (o) { found.push(o); },
                    onComplete: function () { }
                });
                out.prereq.techLab = found.length;
                var segs = [];
                for (var i = 0; i < found.length; i++) {
                    try {
                        var fs = found[i].getClass().getDeclaredFields();
                        for (var j = 0; j < fs.length; j++) {
                            var tn = fs[j].getType().getName();
                            if (tn.indexOf('BuildingVO') >= 0) {
                                fs[j].setAccessible(true);
                                var vo = fs[j].get(found[i]);
                                if (vo) segs.push(fieldGet(vo, 'segmentIndex'));
                            }
                        }
                    } catch (e) { }
                }
                out.prereq.techLabSegments = segs;
                out.prereq.needSegment = rec.seg - 1;
            } catch (e) { out.prereq.err = '' + e; }
            try {
                out.prereq.liquidNitrogen = withSave(function (inst) {
                    var m = Java.cast(matMap(inst), Java.use('java.util.HashMap'));
                    var v = m.get('liquid-nitrogen');
                    if (v === null || v === undefined) return 0;
                    return Java.cast(v, Java.use('i4.a')).h();
                });
            } catch (e) { }
        }
        /* 冻结状态 */
        if (rec.cls === BLK_LAVA) out.prereqFrozen = rec.frozen;
    });
    return out;
}

/* ---- 一键完成前置条件 ----
 * ⚠ 2026-10-12 实测结论: 「强制 getHitMod=1.0」这条旁路**不能用**。
 *   铁长老是地球最底层街区(row 1079)。把最底层街区打碎后, 矿区背景渲染器
 *   r4.b.d(row) 要往下多画一层, 该层的 bgWall 纹理取不到 -> getTextureRegion 返回 null
 *   -> v7.b() NPE, GLThread 直接闪退:
 *     at r4.b.d ← r4.j.l ← r4.b.c ← m5.h.c ← m5.k.r/t/s ← render ← onDrawFrame
 *   所以 mechFix 只做「游戏自己的」安全动作, 不再自动装旁路。
 *   真要强杀请用独立按钮「⚠ 强制旁路」(rpc: mechbypass), 并自行承担闪退风险。
 */
function mechFix() {
    var out = { done: [], via: null, err: null, scan: null };
    Java.perform(function () {
        var rec = currentBlockRec();
        out.scan = { cls: rec.cls, name: rec.name, type: rec.type, row: rec.row,
                     seg: rec.seg, hitMod: rec.hitMod, immunity: rec.immunity,
                     mech: rec.mech ? rec.mech.name : null };
        if (!rec.found) { out.err = rec.err || '读不到当前街区'; return; }
        if (rec.hitMod !== 0) {
            out.via = 'none';
            out.done.push('当前街区不是机制杀(' + (rec.name || rec.cls) + '，hitMod=' +
                          rec.hitMod + ')，无需处理');
            return;
        }
        var how = rec.mech ? rec.mech.how : 'bypass';

        /* ① 熔岩岩块: 执行游戏自己的低温冷冻 —— 冻结后 getHitMod() 自然恢复, 不需要任何钩子 */
        if (how === 'freeze') {
            try {
                invokeNoArg(rec.blockRef, 'freeze');
                var now = Java.cast(rec.blockRef, Java.use(BLK_BASE)).getHitMod();
                out.done.push('已执行低温冷冻 freeze()：写入冻结标记 LAVA_BLOCK_FROZEN=true');
                out.done.push('已清空免疫表 (原: [' + (rec.immunity || []).join(', ') + '])');
                out.done.push('复检：getHitMod() = ' + now + '（冻结前是 0）→ ' +
                              (now > 0 ? '现在打得动了，直接打它' : '仍为 0（确认这一格是当前正在挖的那一格）'));
                out.via = 'freeze';
            } catch (e) {
                out.err = 'freeze 调用失败: ' + e;
            }
            return;
        }

        /* ② 铁长老: 只报前置清单 + 自动补齐能补的材料, 不装任何钩子 */
        if (how === 'story') {
            out.via = 'story';
            out.done.push('⚠ 铁长老不能用伤害杀：它是地球最底层街区(row 1079)，打碎后矿区背景渲染器');
            out.done.push('   r4.b.d(row) 取不到下一层的 bgWall 纹理 → GLThread NPE 闪退（已实测两次）。');
            out.done.push('');
            out.done.push('【存档级完成终局前置】走游戏自己的终局完成流程 a5.e.O0()：');
            try {
                var er = mechCompleteEndgame();
                out.endgameFix = er;
                for (var i = 0; i < (er.done || []).length; i++) out.done.push('   ' + er.done[i]);
                if (er.err) out.done.push('   ⚠ ' + er.err);
            } catch (e) {
                out.done.push('   终局完成流程失败: ' + e);
            }
            out.done.push('');
            out.done.push('说明：这一步等于「终局已经走完」—— 存档 bulkConfig 里 GALACTIC_MOVIE_SUCCEED_DONE=true，');
            out.done.push('铁长老进入已击败状态（血条消失），重启游戏后依然有效。');
            out.done.push('下面这三栋建筑是游戏原本的达成路径，现在不需要再建了（仅作参考）：');
            var chk = null;
            try { chk = endgameCheck(); } catch (e) { }
            out.endgame = chk;
            if (chk) {
                for (var i = 0; i < chk.buildings.length; i++) {
                    var b = chk.buildings[i];
                    out.done.push('   · ' + b.cn + '：已建 ' + (b.built || 0) + ' 座（原本需要 ' + b.matCn + '×1）');
                }
            }
            return;
        }

        if (how === 'report') {
            out.via = 'report';
            out.done.push('这个街区官方没有击败流程（剧情设计上就是让你绕开/别打它）。');
            out.done.push('没有安全的「完成前置条件」动作可做，所以什么都没改。');
            out.done.push('（想硬杀请点「⚠ 强制旁路」，自行承担闪退风险）');
            return;
        }

        /* ④ 未知的免伤街区: 不动手, 只提示 */
        out.via = 'unknown';
        out.done.push('未收录的免伤街区（' + (rec.cls || '?') + '），没有已知的安全解锁动作。');
        out.done.push('没有做任何改动。想硬杀请点「⚠ 强制旁路」。');
    });
    return out;
}

/* ---- 存档级完成终局前置 ----
 * 反汇编确认: 游戏自己走完终局时调的是 a5.e.O0() ——
 *   a5.e$e0.run() -> a5.e.F0() -> a5.e.O0()
 * 它做的事(全部是官方的、写进存档的):
 *   game.q.t("research_building_group")        开任务组
 *   game.n.v5("research_building")             解锁功能
 *   game.n.v5("galactic_portal_building")
 *   game.n.x("radar")                          解锁建筑
 *   prefs.v("GALACTIC_MOVIE_SUCCEED_DONE","true")   ← 写 SaveData.bulkConfig
 *   prefs.s()                                  存盘
 *
 * 而铁长老的"已击败"判定就是读这个键:
 *   u4.i.init(I) -> game.n.w0("GALACTIC_MOVIE_SUCCEED_DONE") -> 真则 P=true
 *   h4.d.w0(k)   -> Boolean.parseBoolean(SaveData.bulkConfig.get(k))
 *   j4.b.v(k,v)  -> SaveData.bulkConfig.put(k,v); s()   ← 官方写入路径
 * ⇒ 调用 O0() 一次, 就等于把终局前置在存档里做完了。
 */
function mechCompleteEndgame() {
    var out = { done: [], err: null, verify: null };
    Java.perform(function () {
        /* 1) 官方终局完成流程 a5.e.O0() */
        var official = false;
        try {
            var found = null;
            Java.choose('a5.e', { onMatch: function (o) { if (!found) found = o; }, onComplete: function () { } });
            if (found) {
                invokeNoArg(found, 'O0');
                official = true;
                out.done.push('✔ 已执行游戏自己的终局完成流程 a5.e.O0()');
                out.done.push('   → 解锁「研究建筑 / 银河传送门」功能、开任务组 research_building_group、解锁 radar');
            } else {
                out.done.push('· 没找到 a5.e 实例（可能还没加载到终局系统），改用直写存档');
            }
        } catch (e) {
            out.done.push('· a5.e.O0() 调用失败(' + e + ')，改用直写存档');
        }

        /* 2) 兜底: 直接按官方路径写 bulkConfig（j4.b.v 内部就是 bulkConfig.put + 存盘） */
        try {
            var g = Java.use('l5.a').c();
            var pf = null, c = g.getClass();
            while (c !== null && !pf) {
                try { var f = c.getDeclaredField('p'); f.setAccessible(true); pf = Java.cast(f.get(g), Java.use('j4.b')); } catch (e) { }
                try { c = c.getSuperclass(); } catch (e) { c = null; }
            }
            if (pf) {
                pf.v('GALACTIC_MOVIE_SUCCEED_DONE', 'true');
                try { pf.s(); } catch (e) { }
                try { pf.d(); } catch (e) { }
                out.done.push((official ? '✔ 复写' : '✔ 已写入') +
                              ' 存档 SaveData.bulkConfig["GALACTIC_MOVIE_SUCCEED_DONE"] = "true" 并存盘');
            } else {
                out.err = '拿不到 prefs 对象，直写失败';
            }
        } catch (e) {
            out.err = (out.err || '') + ' 直写 bulkConfig 失败: ' + e;
        }

        /* 3) 当前铁长老立刻标记为已击败（下次进这一段时 init() 也会自动置位） */
        try {
            var rec = currentBlockRec();
            if (rec.found && rec.blockRef && (rec.cls === BLK_IRON || rec.type === 'IRONBOSS')) {
                fieldSet(rec.blockRef, 'P', true);
                out.done.push('✔ 已把当前铁长老标记为已击败（P = true，血条消失、死亡动画状态）');
            } else if (rec.found) {
                out.done.push('· 当前街区不是铁长老（' + (rec.cls || '?') + '），只写了存档标记');
            }
        } catch (e) {
            out.done.push('· 标记当前铁长老失败: ' + e);
        }

        /* 4) 复检 */
        try {
            var flag = null;
            try {
                var g2 = Java.use('l5.a').c();
                var pf2 = null, c2 = g2.getClass();
                while (c2 !== null && !pf2) {
                    try { var f2 = c2.getDeclaredField('p'); f2.setAccessible(true); pf2 = Java.cast(f2.get(g2), Java.use('j4.b')); } catch (e) { }
                    try { c2 = c2.getSuperclass(); } catch (e) { c2 = null; }
                }
                if (pf2) flag = pf2.e('GALACTIC_MOVIE_SUCCEED_DONE');
            } catch (e) { }
            var elderP = null;
            try {
                var r = currentBlockRec();
                if (r.found && r.blockRef) elderP = fieldGet(r.blockRef, 'P') ? true : false;
            } catch (e) { }
            out.verify = { flag: flag, elderP: elderP };
            out.done.push('复检：存档标记 GALACTIC_MOVIE_SUCCEED_DONE = ' + flag +
                          '；当前铁长老 P = ' + elderP);
        } catch (e) { }
    });
    return out;
}

/* ---- 强制旁路（显式、有风险） ---- */
function mechBypass() {
    var out = { done: [], warn: null, err: null };
    Java.perform(function () {
        var rec = currentBlockRec();
        if (!rec.found) { out.err = rec.err || '读不到当前街区'; return; }
        out.warn = '旁路会把 getHitMod 拉成 1.0。若当前街区是地球最底层(row 1079 等)，' +
                   '打碎它的瞬间背景渲染器会 NPE 闪退 —— 已实测两次。';
        try {
            var steps = installMechBypass();
            for (var i = 0; i < steps.length; i++) out.done.push(steps[i]);
            out.done.push('复检：getHitMod() = ' +
                          Java.cast(rec.blockRef, Java.use(BLK_BASE)).getHitMod());
        } catch (e) { out.err = '' + e; }
    });
    return out;
}

function mechRestore() {
    var out = { done: [], err: null };
    Java.perform(function () {
        try { out.done = removeMechBypass(); }
        catch (e) { out.err = '' + e; }
        try {
            var rec = currentBlockRec();
            if (rec.found && rec.blockRef) {
                out.done.push('复检：getHitMod() = ' +
                              Java.cast(rec.blockRef, Java.use(BLK_BASE)).getHitMod());
            }
        } catch (e) { }
    });
    return out;
}

/* ---------------- RPC ---------------- */

rpc.exports = {
    ping: function () {
        var ok = false;
        Java.perform(function () { try { Java.use(SAVE); ok = true; } catch (e) { ok = false; } });
        return ok;
    },

    dump: function () {
        return withSave(function (inst) {
            var Cls = Java.use(SAVE);
            var fs = Cls.class.getDeclaredFields();
            var out = [];
            for (var i = 0; i < fs.length; i++) {
                var f = fs[i];
                var tn = f.getType().getName();
                var rec = { name: f.getName(), type: tn, value: null, cls: null };
                if (PRIM.indexOf(tn) >= 0) {
                    try { rec.value = num(fieldGet(inst, rec.name)); } catch (e) { }
                } else {
                    try { var o = fieldGet(inst, rec.name); rec.cls = (o === null) ? null : o.$className; } catch (e) { }
                }
                out.push(rec);
            }
            return out;
        });
    },

    get: function (name) {
        return withSave(function (i) {
            var p = pathParent(i, name);
            return fieldGet(p.obj, p.leaf);
        });
    },
    set: function (name, value) {
        return withSave(function (i) {
            var p = pathParent(i, name);
            return fieldSet(p.obj, p.leaf, value);
        });
    },

    /* 字段存在性 + 读写往返自检(写-回读-还原, 全在一次 Java.perform 内) */
    selftest: function (name) {
        return withSave(function (inst) {
            var p;
            try { p = pathParent(inst, name); }
            catch (e) { return { name: name, ok: false, err: '' + e }; }
            var obj = p.obj, leaf = p.leaf;
            var f;
            try { f = findField(obj, leaf); }
            catch (e) { return { name: name, ok: false, err: '字段不存在' }; }
            f.setAccessible(true);
            var tn = f.getType().getName();
            if (PRIM.indexOf(tn) < 0) return { name: name, type: tn, ok: false, err: '非数值字段' };
            var before = fieldGet(obj, leaf);
            var probe;
            if (tn === 'boolean') probe = before ? 0 : 1;
            else if (tn === 'float' || tn === 'double') probe = (before || 0) + 1.5;
            else probe = (before || 0) + 1;
            var mid = fieldSet(obj, leaf, probe);
            var back = fieldSet(obj, leaf, before);
            return { name: name, type: tn, before: before, mid: mid, restored: back,
                     ok: num(mid) !== num(before) && num(back) === num(before) };
        });
    },

    /* 批量自检(一次 Java.perform 跑完, 快很多) */
    selftestbatch: function (names) {
        return withSave(function (inst) {
            var out = [];
            for (var k = 0; k < names.length; k++) {
                var name = names[k];
                try {
                    var p = pathParent(inst, name);
                    var f = findField(p.obj, p.leaf);
                    f.setAccessible(true);
                    var tn = f.getType().getName();
                    if (PRIM.indexOf(tn) < 0) { out.push({ name: name, type: tn, ok: false, err: '非数值' }); continue; }
                    var before = fieldGet(p.obj, p.leaf);
                    var probe = (tn === 'boolean') ? (before ? 0 : 1)
                        : ((tn === 'float' || tn === 'double') ? (before || 0) + 1.5 : (before || 0) + 1);
                    var mid = fieldSet(p.obj, p.leaf, probe);
                    var back = fieldSet(p.obj, p.leaf, before);
                    out.push({ name: name, type: tn, before: before, mid: mid,
                               ok: num(mid) !== num(before) && num(back) === num(before) });
                } catch (e) {
                    out.push({ name: name, ok: false, err: '' + e });
                }
            }
            return out;
        });
    },

    currency: function (which) {
        return withSave(function (inst) {
            var o = fieldGet(inst, which);
            if (o === null) return null;
            var b = num(fieldGet(o, 'b')), c = num(fieldGet(o, 'c'));
            return { b: b, c: c, value: c - b };
        });
    },
    setcurrency: function (which, target) {
        return withSave(function (inst) {
            var o = fieldGet(inst, which);
            if (o === null) return 'no object';
            fieldSet(o, 'b', 0);
            fieldSet(o, 'c', target);
            var b = num(fieldGet(o, 'b')), c = num(fieldGet(o, 'c'));
            return { b: b, c: c, value: c - b };
        });
    },

    materials: function () {
        return withSave(function (inst) {
            var map = matMap(inst);
            var it = map.keySet().iterator();
            var out = [];
            var n = 0;
            while (it.hasNext() && n < 800) {
                var k = it.next();
                var v = map.get(k);
                var val = null;
                if (v !== null) {
                    try {
                        var b = num(fieldGet(v, 'b')), c = num(fieldGet(v, 'c'));
                        val = (b === null || c === null) ? num(v.toString()) : (c - b);
                    } catch (e) { val = num(v.toString()); }
                }
                out.push({ key: k.toString(), value: val, vcls: (v === null) ? null : v.$className });
                n++;
            }
            return out;
        });
    },

    /* 改资源; 不在仓库里的会新建一个 i4.a 条目 */
    setmaterial: function (key, target) {
        return withSave(function (inst) {
            var map = matMap(inst);
            var cur = map.get(key);
            var created = false;
            if (cur === null) {
                try {
                    var A = Java.use('i4.a');
                    var o = A.$new();
                    fieldSet(o, 'b', 0);
                    fieldSet(o, 'c', target | 0);
                    map.put(key, o);
                    created = true;
                    /* 同时标记为"已见过", 仓库才认 */
                    try {
                        var seen = Java.cast(fieldGet(inst, 'seenMaterials'), Java.use('java.util.HashSet'));
                        seen.add(Java.use('java.lang.String').$new(key));
                    } catch (e) { }
                } catch (e) { return { ok: false, err: '新建失败: ' + e }; }
                cur = map.get(key);
            }
            if (cur === null) return { ok: false, err: '写入后仍取不到 ' + key };
            var vcls = cur.$className;
            if (vcls === 'i4.a' || vcls === 'i4.b') {
                fieldSet(cur, 'b', 0);
                fieldSet(cur, 'c', target);
                var b = num(fieldGet(cur, 'b')), c = num(fieldGet(cur, 'c'));
                return { ok: true, created: created, value: c - b };
            }
            if (vcls === 'java.lang.Integer') {
                map.put(key, Java.use('java.lang.Integer').$new(target | 0));
                return { ok: true, created: created, value: num(map.get(key).toString()) };
            }
            if (vcls === 'java.lang.Long') {
                map.put(key, Java.use('java.lang.Long').$new(target));
                return { ok: true, created: created, value: num(map.get(key).toString()) };
            }
            return { ok: false, err: '不支持的容器类型 ' + vcls };
        });
    },

    /* 某资源是否已在仓库里 */
    matinfo: function (key) {
        return withSave(function (inst) {
            var map = matMap(inst);
            var cur = map.get(key);
            if (cur === null) return { exists: false };
            var b = num(fieldGet(cur, 'b')), c = num(fieldGet(cur, 'c'));
            return { exists: true, value: c - b, cls: cur.$className };
        });
    },

    /* 把某个资源条目从仓库里删掉（撤回"新建"） */
    delmaterial: function (key) {
        return withSave(function (inst) {
            var map = matMap(inst);
            if (map.get(key) === null) return { ok: false, err: '不存在' };
            map.remove(key);
            try {
                var seen = Java.cast(fieldGet(inst, 'seenMaterials'), Java.use('java.util.HashSet'));
                seen.remove(Java.use('java.lang.String').$new(key));
            } catch (e) { }
            return { ok: map.get(key) === null };
        });
    },

    /* ---------------- 变速 ---------------- */
    setspeed: function (mult) {
        var m = Number(mult);
        if (!(m > 0)) m = 1.0;
        if (m > 100) m = 100;
        if (m < 0.05) m = 0.05;
        Java.perform(function () { installSpeedHook(); });
        state.speedMult = m;
        state.speedOn = Math.abs(m - 1.0) > 1e-6;
        return { hooked: state.speedHooked, on: state.speedOn, mult: state.speedMult,
                 err: state.speedErr, fields: state.speedFieldNames, mode: state.speedMode };
    },
    speedstatus: function () {
        return { hooked: state.speedHooked, on: state.speedOn, mult: state.speedMult,
                 err: state.speedErr, fields: state.speedFieldNames,
                 mode: state.speedMode, engine: state.engineHooked,
                 calls: state.deltaCalls,
                 rawDt: state.rawDt, scaledDt: state.scaledDt,
                 delta: state.scaledDt };
    },

    /* ---------------- 通用倒计时(制作/远征/部署/商店…) ---------------- */
    timerslist: function (kinds) {
        return timersList(normKinds(kinds));
    },
    timerfinish: function (key, kinds) {
        return timerFinish(key || null, normKinds(kinds));
    },
    timerspeed: function (mult, kinds) {
        var m = Number(mult);
        if (!(m > 1)) m = 1;
        if (m > 1000) m = 1000;
        timerAccel.mult = m;
        timerAccel.kinds = normKinds(kinds);
        timerAccel.on = m > 1;
        if (timerAccel.on && !timerAccel.timer) {
            timerAccel.last = Date.now() / 1000;
            timerAccel.ticks = 0;
            timerAccel.timer = setInterval(timerAccelTick, 250);
        } else if (!timerAccel.on && timerAccel.timer) {
            clearInterval(timerAccel.timer);
            timerAccel.timer = null;
        }
        /* 立刻数一次命中的任务数, 免得面板显示"0 个" */
        Java.perform(function () {
            try {
                var s = schedInst();
                if (s) timerAccel.keys = allTaskKeys(s, timerAccel.kinds).length;
            } catch (e) { }
        });
        return { on: timerAccel.on, mult: timerAccel.mult, kinds: timerAccel.kinds,
                 keys: timerAccel.keys, err: timerAccel.err };
    },
    timerspeedstatus: function () {
        return { on: timerAccel.on, mult: timerAccel.mult, kinds: timerAccel.kinds,
                 keys: timerAccel.keys, ticks: timerAccel.ticks, err: timerAccel.err };
    },

    /* ---- 兼容旧名(远征面板 / 重启辅助 仍在用) ---- */
    expslist: function () {
        return timersList(['expedition']);
    },
    expfinish: function (key) {
        return expFinish(key || null);
    },
    expclaim: function () {
        return expClaim();
    },
    expspeed: function (mult) {
        return rpc.exports.timerspeed(mult, null);
    },
    expspeedstatus: function () {
        return rpc.exports.timerspeedstatus();
    },

    /* ---------------- 广告 ---------------- */
    adinstall: function (opts) {
        opts = opts || {};
        if ('blockInterstitial' in opts) state.blockInterstitial = !!opts.blockInterstitial;
        if ('skipRewarded' in opts) state.skipRewarded = !!opts.skipRewarded;
        if ('forceReady' in opts) state.forceReady = !!opts.forceReady;
        installAdHooks();
        return { ok: state.adHook, blockInterstitial: state.blockInterstitial, skipRewarded: state.skipRewarded };
    },
    adevents: function () {
        var e = state.events.slice();
        state.events = [];
        return e;
    },
    adstatus: function () {
        return {
            hooked: state.adHook,
            rwdUnits: Object.keys(state.rwdListeners),
            intUnits: Object.keys(state.intListeners),
            blockInterstitial: state.blockInterstitial,
            skipRewarded: state.skipRewarded,
            forceReady: state.forceReady
        };
    },
    adinfo: function () {
        var out = {};
        Java.perform(function () {
            [MAX_RWD, MAX_INT].forEach(function (cn) {
                try {
                    var C = Java.use(cn).class;
                    var ms = C.getDeclaredMethods();
                    var arr = [];
                    for (var i = 0; i < ms.length; i++) {
                        var ps = ms[i].getParameterTypes();
                        var pn = [];
                        for (var k = 0; k < ps.length; k++) pn.push(ps[k].getName());
                        arr.push(ms[i].getName() + '(' + pn.join(',') + ')');
                    }
                    out[cn] = arr.join(' | ');
                } catch (e) { out[cn] = 'ERR ' + e; }
            });
        });
        return out;
    },

    /* ---------------- 机制杀助手 ---------------- */
    /* 只读检测: 当前街区是什么 + 需要什么机制 + 前置状态 */
    mechscan: function () { return mechScan(); },
    /* 一键完成前置条件 */
    mechfix: function () { return mechFix(); },
    /* 终局三件套材料补齐（铁长老用） */
    mechgrant: function () { return mechGrantEndgame(); },
    /* 强制旁路（显式、有闪退风险） */
    mechbypass: function () { return mechBypass(); },
    /* 存档级完成终局前置（调 a5.e.O0() + 写 bulkConfig） */
    mechend: function () { return mechCompleteEndgame(); },
    /* 撤掉旁路 */
    mechrestore: function () { return mechRestore(); },
    mechstatus: function () {
        return { on: state.mechOn, done: state.mechDone,
                 poolGuarded: state.poolGuarded, poolFix: state.poolFix };
    }
};
