# 深城修改器 · DeepTown Trainer

针对《**深城 / Deep Town: Idle Mining Tycoon**》（`com.rockbite.deeptown`，实测版本 **5.7.8 / versionCode 398**）的 PC 端修改器。

**MuMu 模拟器 + ADB root + Frida**，不需要在模拟器里装任何东西（frida-server 由工具一次性推送）。
主界面用 PyQt5 写，所有功能都做了实机验证；文档里的结论都来自**反汇编 + 运行时直读**，不是猜的。

> 仅供个人存档研究与学习使用。请在**你自己的设备 / 你自己的账号**上使用。

---

## 一、功能

| 模块 | 说明 |
|---|---|
| 💰 货币 / 资源 / 等级 | 金币、宝石、等级、技能点、各种材料，直接填目标值（智能模式会自己读当前值） |
| 📺 广告屏蔽 / 跳过领奖 | 屏蔽插屏广告；「看广告得奖励」变成点一下就领（Hook AppLovin MAX） |
| ⏱ 倒计时 立即完成 / 加速 | 制作 / 冶炼 / 远征 / 部署 / 商店冷却**全部**列出来，可一键归零或按倍率加速 |
| 🎮 游戏变速 | 放大 Ashley `Engine.update(dt)`，给整个游戏世界调时间流速（0.05x ~ 20x） |
| 🧩 机制杀助手 | 有些街区设计上「不吃伤害」，这里自动完成前置条件并逐条列出做了什么 |
| 🔄 重启辅助 | 重启游戏 → 自动重连 → 原样挂回广告屏蔽 / 变速 / 加速 |
| ⚡ 技能冷却 | 所有已解锁法术冷却改为指定秒数 |
| 📋 存档字段总览 | 把 `SaveData` 的字段当前值全列出来 |
| 🩺 自检 | `自检.py` 一键跑通 8 项链路检查，出问题能定位到具体哪一步 |

---

## 二、快速开始

### 1. 环境

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11 |
| 模拟器 | MuMu 模拟器 12（ADB 端口 `127.0.0.1:16384`，需开启 root） |
| Python | 3.9 ~ 3.12（**不要用 3.13+**，`frida` 轮子跟不上） |
| 游戏 | 深城 5.7.8（`com.rockbite.deeptown`，versionCode 398） |

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

> **frida 必须锁 16.x**（实测 `frida==16.7.19`）。17.x 的 Java 桥接在本环境上会挂。
> ```bash
> pip install "frida==16.7.19"
> ```

### 3. 启动

1. 打开 MuMu，启动深城，进入游戏主界面
2. 双击 `启动修改器.bat`
   - 找不到解释器时：在仓库根目录建一个 `python_path.txt`，里面写一行 `pythonw.exe` 的完整路径
3. 左上角显示 `● 已连接` 后，点 **「🔌 连接智能通道（Frida）」**
   - 第一次会往模拟器推一个 `frida-server`（约 110MB，一次性，需要十几秒）
4. 搜索框输入要改的东西（如 `金币`）→ 双击列表项 → 只填目标值 → 「一键修改」

**改完点「强制保存进度」**，让游戏把内存数值写进存档。

### 4. frida-server

仓库**不含** `frida-server` 二进制（109MB，超过 GitHub 单文件限制）。
工具会自动从本地 Python 环境里找 `frida` 自带的 server 推送到模拟器；如果你要手动放，把它放到：

```
src/bin/frida-server-x86_64
```

（x86_64 架构的 Android frida-server，版本需与 Python 端 `frida` 一致）

---

## 三、目录结构

```
DeepTownTrainer/
├─ 启动修改器.bat            可移植启动器（自动找 python）
├─ requirements.txt
├─ docs/
│   └─ 使用说明.md           完整使用手册（含逆向原理章节）
└─ src/
   ├─ simple.py              ★ 主界面（PyQt5）：智能/手动双模式 + 各功能面板
   ├─ agent.js               ★ Frida Agent：Java 层读写 + 各类 Hook（注入到游戏进程）
   ├─ engine.py              内存引擎（ADB 直读进程内存 + 快照 + 扫描）
   ├─ adbmem.py              ADB 封装（静默调用、连接、root、maps、读写）
   ├─ frida_client.py        Frida 通道封装（推 server、attach、加载 agent）
   ├─ catalog.py             可修改项清单（350 项，含搜索/分组/别名）
   ├─ spells.py              法术/技能冷却
   ├─ scanner.py             内存扫描
   ├─ main.py                早期版本 UI（备用，功能较少）
   ├─ 自检.py                8 项链路自检
   ├─ materials_cn.json      材料中文名
   ├─ expedition_cn.json     远征收藏品/洞穴中文名
   ├─ spells.json            法术数据
   ├─ dexparse.py / smalidump.py / listmethods.py / findcrypto.py
   │                         DEX 分析小工具（需自行解包 APK）
   └─ test_engine.py         内存引擎冒烟测试
```

---

## 四、技术要点（逆向笔记）

这一节是踩过的坑，也是这个项目最有价值的部分。全部经过反汇编 + 运行时验证。

### 1. 伤害结算：`伤害 = 原始伤害 × 街区.getHitMod()`

```java
// r4.i.b(z6/a dmg, F,F,F,F)  /  r4.i.U(...)
hitMod = 当前街区.getHitMod();
伤害   = dmg * hitMod;                 // z6/a 是游戏的大数类型
if (伤害.toFloat() > 0) { 扣血(); 触发 "BLOCK_HIT"; }
// 否则整段跳过 —— 连事件都不发
```

所以「打不动」= 街区的 `getHitMod()` 返回 0，**与 DPS 无关**。

| 类 | 街区 | `getHitMod()` 行为 |
|---|---|---|
| `u4.j` | 熔岩岩块 LavaBlock | `c(row)` 为假 → 强制 `return 0`（内部 `hitMod` 字段其实是 1.0） |
| `u4.i` | 铁长老 IRONBOSS（地球最底 row 1079） | 恒 `return 0`，免疫表为空 |
| `u4.a` | 铝长老 ALUMINIUM_BOSS | 恒 `return 0`，免疫 `mining-laser`/`fire-cannon` 等 |

### 2. 「倒计时」不吃变速 —— 它走墙钟

```
SaveData.universalScheduler : a7.b
   c:double = System.currentTimeMillis() / 1000     ← 真墙钟
   任务 endTime = 建任务时的墙钟 + 时长              ← 绝对结束时间(存在存档里)
   i(key) = endTime - 当前墙钟                       ← 界面上的 00:02:00
   r(key, v) = 把剩余时间改成 v                      ← 游戏自己的加速就用它
```

制作 / 冶炼 / 远征 / 部署 / 商店冷却全在这个调度器里（key 形如
`<建筑uID>recipie_<槽位>`、`<建筑uID>start-expedition`、`<建筑uID>deploy`…）。
放大 `deltaTime` 动不了墙钟，**所以全局变速对它们无效**——只有动画会变快。

### 3. 大数类型 `z6.a` 与它的对象池 bug

```
z6.a = float b + 数量级下标 c，单位表 ["","K","M","B","T","q","Q","s","S","O","N","d","U","D"]，每级 ×1000
⇒ 界面上的 "46 s" = 46 × 10^21，不是 46 秒
```

`z6.b` 是它的对象池（libGDX `Pool` 单例）。`z6.b.b()` = `obtain(); obj.d = false;` ——
`obtain()` 偶尔返回 `null`，紧跟的 `iput-boolean` 直接 NPE：

```
at z6.b.b ← z6.a.d ← z6.a.w ← r4.c.i ← r4.i.F ← r4.a.F ← r4.a.g ← r4.i.l ← r4.i.U ← i5.v(法术)
```

工具里带了护栏（`installBigPoolGuard`）：拿不到就 `new z6.a()` 兜底。

### 4. 存档里的「剧情标记」`SaveData.bulkConfig`

```java
h4.d.w0(k)  → Boolean.parseBoolean( SaveData.bulkConfig.get(k) )
j4.b.v(k,v) → SaveData.bulkConfig.put(k, v); s();   // s() = 存盘
```

已知键：`GALACTIC_MOVIE_SUCCEED_DONE`、`LAVA_BLOCK_FROZEN`、`hdd_was_found`、`pc_made_early`…

铁长老的「已击败」判定就读 `GALACTIC_MOVIE_SUCCEED_DONE`：
`u4.i.init()` 里为真则 `P = true`。而游戏自己走完终局时调的是 **`a5.e.O0()`**：

```java
a5.e.O0() {
    game.q.t("research_building_group");
    game.n.v5("research_building"); game.n.v5("galactic_portal_building");
    game.n.x("radar");
    prefs.v("GALACTIC_MOVIE_SUCCEED_DONE", "true");
    prefs.s();
}
```

### 5. 别把最底层街区打碎 —— 渲染层会崩

铁长老在地球最底层 `row 1079`。打碎它之后，矿区背景渲染器要往下多画一层：

```java
// r4.b.d(int row)
v1 = r4.i.J(row-1) - r4.i.K()/2;          // K() = 720.0，每行高度
v5 = q(v1);                                // 取该行所在区域
v7 = res.getTextureRegion(v5.bgWall);      // ← 返回 null
v7.b();                                    // ← NPE，GLThread 直接闪退
```

**这是游戏自身的硬限制**，所以「强制 getHitMod=1.0」这条旁路对最底层街区会闪退，
工具里只作为显式按钮提供并弹二次确认。

### 6. 变速为什么改成 Hook `Engine.update`

早期实现是 Hook `AndroidGraphics.onDrawFrame` + 回拨 `lastFrameTime`。
实测在本环境（frida 16.7.19 + MuMu12/ART）里，替换体里的 `this.onDrawFrame(gl)`
**不会调到原方法**而是再进一次替换体 → 原方法永不执行 → 渲染停摆；连「空钩子」都会冻住游戏。

现在改成放大 `com.badlogic.ashley.core.e.p(float)`（Ashley `Engine.update`），确定性生效。

### 7. 存档里的建筑

```
SaveData.ownedBuildings : gdx Array<BuildingVO>
BuildingVO { uID, blueprint, currentLevel, floor, segmentIndex, isDeployed, isUpgrading,
             progressData, progressDataDOM, activeBoostID, workerId, isBoostActive }
地下建筑 segmentIndex >= 0, floor = -1；地面建筑 segmentIndex < 0, floor = 11..13
h4/d.f(int segmentIndex, BuildingBluePrintVO bp) → 新建 VO 并加进 ownedBuildings
```

---

## 五、已知限制

- 只针对**深城 5.7.8**（`versionCode 398`）。游戏更新后混淆名会变，需要重新定位。
- 混淆名（`r4.i`、`a7.b`、`u4.j`…）是**这一版特有**的，跨版本不通用。
- 变速上限 20x（超过会被 libGDX 的 `0.3s` 帧上限夹住）。
- 机制杀助手对「最底层街区」**不能**用伤害杀（见第四节 5），只能走存档标记。
- `frida-server` 二进制未随仓库提供（体积超限），需自行准备。

---

## 六、免责声明

本项目用于**个人存档研究、逆向工程学习**。

- 请在你**自己拥有 / 自己账号**的游戏上使用；
- 使用修改器可能违反游戏的用户协议，可能导致账号受限 —— 后果由使用者自行承担；
- 作者不对任何数据丢失、账号封禁或其它后果负责；
- 本项目与 Rockbite Games 无任何关联。

---

## 七、License

[MIT](LICENSE)
