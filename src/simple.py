# -*- coding: utf-8 -*-
"""
深城修改器 · 傻瓜版 v3
--------------------------------------------------------------------------
两种工作方式:

  智能模式(推荐, 需要 Frida)
      直接连进游戏的 Java 对象 —— 当前值由工具自己读出来, 你只填目标值。
      看不见的存档字段也能改, 还能一键看全部字段的当前值。

  手动模式(不依赖 Frida)
      原来的"两步搞定": 你照抄游戏里显示的数字 -> 工具扫内存定位 -> 改写。

启动方式: 双击 ..\\启动修改器.bat
"""
import os, sys, time, struct
import spells as SPM
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QLabel, QComboBox, QListWidget, QListWidgetItem, QDialog,
    QTextEdit, QMessageBox, QGroupBox, QGridLayout, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QInputDialog, QSlider, QDoubleSpinBox, QRadioButton, QButtonGroup
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adbmem import Adb, guess_adb, MUMU_PORTS
import engine as E
import catalog

PKG = 'com.rockbite.deeptown'
UNITS = ['无', '千 (K)', '万 (W)', '百万 (M)', '十亿 (B)']

# 远征物品/洞穴的中文名（从游戏 assets/i18n/DeepBundle_zh_CN.properties 提取）
_EXP_CN = {'items': {}, 'caves': {}}
try:
    import json as _json
    _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'expedition_cn.json')
    if os.path.exists(_p):
        _EXP_CN = _json.load(open(_p, encoding='utf-8'))
except Exception:
    pass


def exp_item_cn(item_id):
    """远征物品英文 id -> 中文名（查不到就原样返回）"""
    if not item_id:
        return ''
    return _EXP_CN.get('items', {}).get(str(item_id).lower(), item_id)


def exp_cave_cn(cave_key):
    if not cave_key:
        return ''
    return _EXP_CN.get('caves', {}).get(str(cave_key).lower(), cave_key)


def fmt_clock(sec):
    """秒 -> 00:02:00"""
    try:
        s = max(0, int(round(float(sec))))
    except Exception:
        return str(sec)
    h, r = divmod(s, 3600)
    m, s2 = divmod(r, 60)
    return '%02d:%02d:%02d' % (h, m, s2)


# 全局: 同一时刻只允许一个扫描任务（每次扫描要占 ~1.2GB 内存, 多开会把机器拖死）
_ACTIVE_JOB = None


def unit_mult(u):
    return {'无': 1, '千 (K)': 1e3, '万 (W)': 1e4, '百万 (M)': 1e6, '十亿 (B)': 1e9}[u]


def unit_tag(u):
    return {'无': '', '千 (K)': 'K', '万 (W)': 'W', '百万 (M)': 'M', '十亿 (B)': 'B'}[u]


def fmt_num(v):
    try:
        if isinstance(v, float):
            if v != v or v in (float('inf'), float('-inf')):
                return str(v)
            if abs(v - round(v)) < 1e-6:
                return '{:,}'.format(int(round(v)))
            return ('{:,.4f}'.format(v)).rstrip('0').rstrip('.')
        return '{:,}'.format(int(v))
    except Exception:
        return str(v)


def parse_target(text):
    """'1.5M' / '1000000' / '3' -> int"""
    t = str(text).strip().upper().replace(',', '').replace('，', '').replace(' ', '')
    mult = 1
    for suf, m in (('K', 1e3), ('M', 1e6), ('B', 1e9), ('W', 1e4)):
        if t.endswith(suf):
            mult, t = m, t[:-1]
            break
    v = float(t) * mult
    return int(round(v))


STYLE = """
QWidget{background:#14161c;color:#d8dbe3;font-family:'Microsoft YaHei';font-size:14px;}
QLineEdit,QComboBox,QDoubleSpinBox{background:#0d0f13;border:1px solid #333a49;border-radius:6px;padding:7px 10px;font-size:15px;color:#e8ebf2;}
QComboBox QAbstractItemView{background:#0d0f13;selection-background-color:#2d7ff9;}
QPushButton{background:#242a36;border:1px solid #37405a;border-radius:6px;padding:8px 16px;}
QPushButton:hover{background:#2f3746;}
QPushButton:disabled{color:#5a6274;}
QPushButton#go{background:#2d7ff9;border-color:#2d7ff9;color:#fff;font-weight:bold;font-size:16px;padding:12px;}
QPushButton#go:hover{background:#4a92ff;}
QPushButton#stop{background:#9b2c2c;border-color:#c53030;color:#fff;font-weight:bold;}
QPushButton#stop:hover{background:#c53030;}
QPushButton#ad{background:#c05621;border-color:#e07a3a;color:#fff;font-weight:bold;}
QPushButton#ad:hover{background:#e07a3a;}
QListWidget{background:#0d0f13;border:1px solid #262c3a;border-radius:8px;font-size:15px;}
QListWidget::item{padding:10px 8px;border-bottom:1px solid #1b2029;}
QListWidget::item:selected{background:#2d7ff9;color:#fff;}
QTextEdit{background:#0b0d11;border:1px solid #262c3a;border-radius:6px;font-family:Consolas;font-size:13px;}
QGroupBox{border:1px solid #2a3040;border-radius:8px;margin-top:16px;padding-top:12px;font-weight:bold;color:#6db3ff;}
QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 6px;}
QTableWidget{background:#0d0f13;border:1px solid #262c3a;border-radius:6px;gridline-color:#1e2430;}
QHeaderView::section{background:#1b2029;border:none;padding:6px;color:#8fa0bd;}
QCheckBox{padding:4px 0;}
QSlider::groove:horizontal{height:6px;background:#262c3a;border-radius:3px;}
QSlider::handle:horizontal{background:#2d7ff9;width:16px;margin:-6px 0;border-radius:8px;}
"""


# =====================================================================
#  Frida 智能层
# =====================================================================

class Smart(object):
    def __init__(self, log=None):
        self._log_fn = log
        self.msgs = []
        self.ch = None
        self.types = {}
        self._mats = None

    def log(self, s):
        self.msgs.append(str(s))
        if self._log_fn:
            self._log_fn(s)

    @property
    def ok(self):
        return self.ch is not None and self.ch.api is not None

    def open(self, adb_path, serial):
        if self.ok:
            return True
        import frida_client as FC
        self.ch = FC.FridaChannel(adb_path, serial)
        if not self.ch.ensure_server(self.log):
            self.ch = None
            return False
        if not self.ch.attach(None, self.log):
            self.ch = None
            return False
        try:
            self.types = {r['name']: r['type'] for r in (self.ch.api.dump() or [])}
        except Exception:
            self.types = {}
        return True

    def close(self):
        if self.ch:
            self.ch.detach()
        self.ch = None
        self._mats = None

    # ---------- 读 ----------
    def read_field(self, name):
        r = self.ch.api.get(name)
        if isinstance(r, dict) and '__err' in r:
            raise RuntimeError(r['__err'])
        return r

    def read_currency(self, which):
        r = self.ch.api.currency(which)
        return r['value'] if isinstance(r, dict) else None

    def materials(self):
        if self._mats is None:
            r = self.ch.api.materials()
            self._mats = r if isinstance(r, list) else []
        return self._mats

    def read_material(self, key):
        for m in self.materials():
            if m['key'] == key:
                return m['value']
        return None

    def matinfo(self, key):
        return self.ch.api.matinfo(key)

    def dump(self):
        return self.ch.api.dump() or []

    # ---------- 写 ----------
    def write(self, kind, key, value):
        if kind == 'currency':
            r = self.ch.api.setcurrency(key, int(value))
            return isinstance(r, dict), r
        if kind == 'material':
            r = self.ch.api.setmaterial(key, int(value))
            self._mats = None
            if isinstance(r, dict):
                return bool(r.get('ok')), r
            return False, {'ok': False, 'err': str(r)}
        r = self.ch.api.set(key, value)
        return r is not None, r

    def del_material(self, key):
        r = self.ch.api.delmaterial(key)
        self._mats = None
        return r

    # ---------- 广告 / 变速 ----------
    def ad_install(self, opts):
        return self.ch.api.adinstall(opts)

    def ad_status(self):
        return self.ch.api.adstatus()

    def ad_events(self):
        try:
            return self.ch.api.adevents() or []
        except Exception:
            return []

    def set_speed(self, mult):
        return self.ch.api.setspeed(mult)

    def speed_status(self):
        return self.ch.api.speedstatus()

    # ---------- 通用倒计时(制作/远征/部署/商店…) ----------
    def timers_list(self, kinds=None):
        return self.ch.api.timerslist(kinds)

    def timer_finish(self, key=None, kinds=None):
        return self.ch.api.timerfinish(key, kinds)

    def timer_speed(self, mult, kinds=None):
        return self.ch.api.timerspeed(mult, kinds)

    def timer_speed_status(self):
        return self.ch.api.timerspeedstatus()

    # ---------- 机制杀助手 ----------
    def mech_scan(self):
        return self.ch.api.mechscan()

    def mech_fix(self):
        return self.ch.api.mechfix()

    def mech_grant(self):
        return self.ch.api.mechgrant()

    def mech_bypass(self):
        return self.ch.api.mechbypass()

    def mech_restore(self):
        return self.ch.api.mechrestore()

    def mech_status(self):
        return self.ch.api.mechstatus()

    # ---------- 远征（旧名，内部走通用接口） ----------
    def exp_list(self):
        return self.ch.api.expslist()

    def exp_finish(self, key=None):
        return self.ch.api.expfinish(key)

    def exp_claim(self):
        return self.ch.api.expclaim()

    def exp_speed(self, mult):
        return self.ch.api.expspeed(mult)

    def exp_speed_status(self):
        return self.ch.api.expspeedstatus()


def item_kind(item):
    f = item.get('field')
    if f in ('cash', 'crystals'):
        return 'currency'
    if item.get('group') == '资源':
        return 'material'
    return 'field'


# =====================================================================
#  内存路径(手动模式)
# =====================================================================

class Job(QThread):
    msg = pyqtSignal(str)
    done = pyqtSignal(str, object)

    def __init__(self, mode, loc, args):
        super().__init__()
        self.mode = mode
        self.loc = loc
        self.args = args
        self._stop = False

    # ---------- 取消 ----------
    def stop(self):
        self._stop = True

    def stopped(self):
        return self._stop

    def run(self):
        try:
            if self.mode == 'run':
                self.do_run()
            else:
                self.do_converge()
        except (E.Cancelled, SPM.SPM_Cancelled):
            self.msg.emit('■ 已按你的要求中止扫描。')
            self.done.emit('stopped', None)
        except Exception as e:
            import traceback; traceback.print_exc()
            self.done.emit('err', str(e))

    @staticmethod
    def _structs(hint):
        return {'i4b': ('i4b',), 'i4a': ('i4a',), 'plain': ('long', 'int')}.get(hint)

    def do_run(self):
        cur, want, struct_hint = self.args
        if struct_hint == 'spell':
            self.do_spell_cd(want); return
        rng = E.parse_amount(cur)
        w = E.parse_amount(want)
        if not rng or not w:
            self.done.emit('err', '数值格式不正确'); return
        target = w[0]
        lo, hi = rng
        if struct_hint in ('plain', 'i4a', 'i4b'):
            lo, hi = int(lo), int(hi)
        self.msg.emit('① 正在读取游戏内存…')
        self.loc.take_snapshot(log=self.msg.emit)
        if self._stop:
            raise E.Cancelled()
        self.msg.emit('② 定位 %s …' % cur)
        best, allc = E.pick(self.loc, lo, hi, structures=self._structs(struct_hint),
                            stop=self.stopped, log=self.msg.emit)
        self.msg.emit('   粗筛命中 %d 个位置(其中像对象的 %d 个)' % (len(allc), len(best)))
        if not best:
            self.done.emit('err', '没找到这个数值。\n请确认游戏里显示的就是它，或换个单位(M/K)再试'); return
        if len(best) > 12:
            self.done.emit('converge', best[:8000]); return
        self.msg.emit('③ 写入 %d 个位置…' % len(best))
        for c in best:
            if self._stop:
                raise E.Cancelled()
            self.loc.apply(c, target)
        time.sleep(0.7)
        good = 0
        for c in best:
            v = self.loc.read_cand(c)
            if v is not None and abs(v - target) <= max(3000, target // 20000):
                good += 1
        if good:
            self.msg.emit('④ 校验通过 %d/%d' % (good, len(best)))
            self.done.emit('ok', (good, len(best)))
        else:
            self.done.emit('err', '写入后校验没通过，请回游戏点一下再试')

    def do_spell_cd(self, want_text):
        try:
            target = float(str(want_text).strip())
        except (TypeError, ValueError):
            self.done.emit('err', '冷却秒数请填数字，例如 0.1'); return
        if target <= 0 or target > 600:
            self.done.emit('err', '冷却秒数请填 0.01 ~ 600 之间'); return
        self.msg.emit('① 正在扫描全部法术的冷却字段…')
        res = SPM.scan_cooldowns(self.loc, log=self.msg.emit, stop=self.stopped)
        found = [r for r in res if r['cands']]
        self.msg.emit('② 内存里找到 %d/%d 个法术，当前冷却：' % (len(found), len(res)))
        for r in res:
            if r['cands']:
                self.msg.emit('     %s：当前 %ss' % (r['name'], r['cd']))
            else:
                self.msg.emit('   ⏭ %s：未载入（未解锁）' % r['name'])
        if not found:
            self.msg.emit('提示：只有已解锁的法术才会载入内存。解锁更多法术后再试。')
            self.done.emit('err', '没有找到任何已载入的法术'); return
        self.msg.emit('③ 正在写入 %ss …' % target)
        changed, skipped = SPM.zero_cooldowns(self.loc, target, only_tight=True,
                                              log=self.msg.emit, prescan=res, stop=self.stopped)
        for nm, old, hits, ok in changed:
            self.msg.emit('   ✅ %s：%ss → %ss' % (nm, old, target))
        for nm, why in skipped:
            self.msg.emit('   ⏭ %s：%s' % (nm, why))
        if changed:
            self.msg.emit('完成！%d 个法术冷却已改为 %ss。回游戏放个技能试试。' % (len(changed), target))
            self.done.emit('ok', (len(changed), len(res)))
        else:
            self.done.emit('err', '写入后校验没通过，请回游戏点一下再试')

    def do_converge(self):
        self.done.emit('need_more', self.args[0])

    def converge_round2(self, cands, newtext, want):
        rng = E.parse_amount(newtext)
        w = E.parse_amount(want)
        if not rng or not w:
            self.done.emit('err', '数值格式不正确'); return
        lo, hi = rng
        target = w[0]
        self.msg.emit('正在刷新内存…')
        self.loc.refresh()
        if self._stop:
            self.done.emit('stopped', None); return
        now = E._dedupe_overlap(self.loc.find(lo, hi, stop=self.stopped))
        keep = set(c['addr'] for c in now)
        inter = [c for c in cands if c['addr'] in keep]
        self.msg.emit('收敛后剩 %d 个位置' % len(inter))
        if not inter:
            self.done.emit('err', '没有交集了, 可能数值没变或变错了, 请重来一次'); return
        if len(inter) > 40:
            inter = inter[:40]
        for c in inter:
            if self._stop:
                self.done.emit('stopped', None); return
            self.loc.apply(c, target)
        time.sleep(0.6)
        good = sum(1 for c in inter if abs((self.loc.read_cand(c) or 0) - target) <= max(2000, target // 50000))
        if good:
            self.done.emit('ok', (good, len(inter)))
        else:
            self.done.emit('err', '第二步校验失败')


# =====================================================================
#  广告面板
# =====================================================================

class AdDialog(QDialog):
    def __init__(self, parent, smart):
        super().__init__(parent)
        self.smart = smart
        self.setWindowTitle('广告：屏蔽 + 跳过领奖')
        self.setMinimumSize(640, 560)
        self.setStyleSheet(STYLE)

        v = QVBoxLayout(self)
        head = QLabel('📺 广告：屏蔽 + 跳过领奖')
        head.setStyleSheet('font-size:19px;font-weight:bold;color:#fff;')
        v.addWidget(head)
        tip = QLabel('插屏广告直接吞掉；"看广告得奖励"点一下就发奖（不再等视频）。\n'
                     '原理：挂钩游戏的 AppLovin MAX 回调，直接告诉游戏"视频看完了"。')
        tip.setStyleSheet('color:#7a8399;')
        tip.setWordWrap(True)
        v.addWidget(tip)

        g = QGroupBox('开关')
        f = QVBoxLayout(g)
        self.cb_int = QCheckBox('彻底屏蔽插屏广告（切场景时不再弹）')
        self.cb_int.setChecked(True)
        self.cb_rwd = QCheckBox('跳过激励视频，直接发奖励（推荐）')
        self.cb_rwd.setChecked(True)
        self.cb_ready = QCheckBox('让"看广告"按钮始终可用（不依赖广告是否加载成功）')
        self.cb_ready.setChecked(True)
        for c in (self.cb_int, self.cb_rwd, self.cb_ready):
            f.addWidget(c)
        v.addWidget(g)

        row = QHBoxLayout()
        self.btn_on = QPushButton('开启（立即生效）')
        self.btn_on.setObjectName('ad')
        self.btn_on.clicked.connect(self.on_on)
        row.addWidget(self.btn_on)
        self.btn_ref = QPushButton('刷新状态 / 抓取事件')
        self.btn_ref.clicked.connect(self.on_refresh)
        row.addWidget(self.btn_ref)
        v.addLayout(row)

        self.log = QTextEdit(); self.log.setReadOnly(True)
        v.addWidget(self.log, 1)

        note = QLabel('⚠ 开启后一直有效，直到游戏重启。重启游戏后再点一次「开启」即可。')
        note.setStyleSheet('color:#e0a04a;')
        note.setWordWrap(True)
        v.addWidget(note)

        close = QPushButton('关闭'); close.clicked.connect(self.reject)
        v.addWidget(close)
        self.on_refresh()

    def echo(self, s):
        self.log.append(s)

    def opts(self):
        return {'blockInterstitial': self.cb_int.isChecked(),
                'skipRewarded': self.cb_rwd.isChecked(),
                'forceReady': self.cb_ready.isChecked()}

    def on_on(self):
        if not self.smart.ok:
            QMessageBox.warning(self, '未连接', '智能通道没打开。\n请先点主界面的「连接智能通道」。')
            return
        self.btn_on.setEnabled(False)
        try:
            self.echo('✅ 已开启：%s' % self.smart.ad_install(self.opts()))
            self.echo('   回游戏点一下"看广告"的按钮试试 —— 应该直接发奖励。')
        except Exception as e:
            self.echo('❌ 开启失败：%s' % e)
        finally:
            self.btn_on.setEnabled(True)
            self.on_refresh()

    def on_refresh(self):
        if not self.smart.ok:
            self.echo('（智能通道未连接）')
            return
        try:
            st = self.smart.ad_status()
            self.echo('状态：钩子=%s  插屏屏蔽=%s  跳过激励=%s  激励监听器=%s'
                      % (st.get('hooked'), st.get('blockInterstitial'),
                         st.get('skipRewarded'), st.get('rwdUnits')))
        except Exception as e:
            self.echo('读取状态失败：%s' % e)
        for e in self.smart.ad_events():
            self.echo('· ' + e)


# =====================================================================
#  变速面板
# =====================================================================

class SpeedDialog(QDialog):
    PRESETS = [('0.1x  极慢', 0.1), ('0.25x  慢', 0.25), ('0.5x  偏慢', 0.5),
               ('1x  正常', 1.0), ('2x', 2.0), ('3x', 3.0), ('5x', 5.0),
               ('10x  很快', 10.0), ('20x  极快', 20.0)]

    def __init__(self, parent, smart):
        super().__init__(parent)
        self.smart = smart
        self.setWindowTitle('游戏变速（全局时间流逝）')
        self.setMinimumSize(620, 480)
        self.setStyleSheet(STYLE)

        v = QVBoxLayout(self)
        head = QLabel('游戏变速（时间流速）')
        head.setStyleSheet('font-size:19px;font-weight:bold;color:#fff;')
        v.addWidget(head)
        tip = QLabel(
            '把游戏逻辑的每帧时间片(dt)按倍率放大 —— 相当于给整个游戏世界调时间流速。\n'
            '· 做法：放大 Ashley Engine.update(dt)（每秒 60 次）。游戏所有 ECS 系统\n'
            '  （采矿 / 生产 / 机器人 / 动画 / 动作）都从这里取时间片，所以整块逻辑一起变快。\n'
            '· 生效范围：采矿/生产/机器人/动画等一切走游戏帧逻辑的东西\n'
            '· 不生效：商店冷却、每日礼物、制作进度、远征倒计时 —— 这些是"墙钟"计时，\n'
            '  要快请用「⏱ 倒计时 / 立即完成」面板\n'
            '· 状态行的「ECS 时间片 a→b」就是每帧交给游戏逻辑的 dt：10x 时 b ≈ 10×a。')
        tip.setStyleSheet('color:#7a8399;')
        tip.setWordWrap(True)
        v.addWidget(tip)

        g = QGroupBox('倍率')
        f = QGridLayout(g)
        self.cmb = QComboBox()
        for name, val in self.PRESETS:
            self.cmb.addItem(name, val)
        self.cmb.setCurrentIndex(3)
        f.addWidget(QLabel('预设'), 0, 0)
        f.addWidget(self.cmb, 0, 1, 1, 2)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(0.05, 20.0)
        self.spin.setSingleStep(0.25)
        self.spin.setValue(1.0)
        self.spin.setDecimals(2)
        f.addWidget(QLabel('自定义'), 1, 0)
        f.addWidget(self.spin, 1, 1)
        f.addWidget(QLabel('倍（0.05 ~ 20）'), 1, 2)
        v.addWidget(g)

        row = QHBoxLayout()
        b_apply = QPushButton('应用')
        b_apply.setObjectName('go')
        b_apply.clicked.connect(self.on_apply)
        row.addWidget(b_apply)
        b_reset = QPushButton('还原 1x')
        b_reset.setObjectName('stop')
        b_reset.clicked.connect(self.on_reset)
        row.addWidget(b_reset)
        b_st = QPushButton('刷新状态')
        b_st.clicked.connect(self.on_status)
        row.addWidget(b_st)
        v.addLayout(row)

        self.log = QTextEdit(); self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        note = QLabel('⚠ 变速是运行时钩子，游戏重启后失效，需要重新点「应用」。')
        note.setStyleSheet('color:#e0a04a;')
        note.setWordWrap(True)
        v.addWidget(note)
        close = QPushButton('关闭'); close.clicked.connect(self.reject)
        v.addWidget(close)

        self.cmb.currentIndexChanged.connect(
            lambda i: self.spin.setValue(self.cmb.itemData(i)))
        self.on_status()

    def echo(self, s):
        self.log.append(s)

    def on_apply(self):
        if not self.smart.ok:
            QMessageBox.warning(self, '未连接', '请先点主界面的「连接智能通道」。'); return
        m = float(self.spin.value())
        try:
            r = self.smart.set_speed(m)
            if r.get('err'):
                self.echo('❌ 挂钩失败：%s' % r['err'])
            else:
                mode = {'engine': 'ECS 时间片(Engine.update)'}.get(r.get('mode'), r.get('mode'))
                self.echo('✅ 已设为 %.2fx（方式 %s，字段 %s）'
                          % (r['mult'], mode, r.get('fields')))
        except Exception as e:
            self.echo('❌ %s' % e)
        self.on_status()

    def on_reset(self):
        self.spin.setValue(1.0)
        self.cmb.setCurrentIndex(3)
        self.on_apply()

    def on_status(self):
        if not self.smart.ok:
            self.echo('（智能通道未连接）'); return
        try:
            st = self.smart.speed_status()
            mode = {'engine': 'ECS 时间片'}.get(st.get('mode'), st.get('mode') or '—')

            def _f(v):
                return ('%.5f' % v) if isinstance(v, (int, float)) else str(v)
            self.echo('状态：钩子=%s  方式=%s  当前倍率=%s  已改帧数=%s  ECS 时间片 %s→%s'
                      % (st.get('hooked'), mode, st.get('mult'), st.get('calls'),
                         _f(st.get('rawDt')), _f(st.get('scaledDt'))))
            if st.get('err'):
                self.echo('⚠ ' + st['err'])
        except Exception as e:
            self.echo('读取状态失败：%s' % e)


# =====================================================================
#  倒计时面板（制作 / 远征 / 部署 / 商店 … 立即完成 + 加速）
# =====================================================================
#
#  深城里所有"要等"的计时都在同一个调度器（SaveData.universalScheduler / a7.b）里，
#  存的是**绝对墙钟 endTime**。所以「游戏变速」（放大 deltaTime）只让动画变快，
#  计时一秒还是一秒 —— 工艺制作的进度条不会因为你开 20 倍就 20 倍速走完。
#  要让它真的变快只能改 endTime：立即完成 = r(key,0)，加速 = 按倍率改写。

CB_CN = {
    'CraftingBuildingScript': '制作',
    'SmeltingBuildingScript': '冶炼',
    'ChemistryBuildingScript': '化工',
    'ChemistryMiningBuildingScript': '化工开采',
    'OilBuildingScript': '炼油',
    'RecipeBuildingScript': '配方生产',
    'MiningBuildingScript': '采矿',
    'GreenHouseBuildingScript': '温室',
    'ResearchBuildingScript': '研究',
    'ObservatoryBuildingScript': '天文台',
    'NuclearReactorBuildingScript': '核反应堆',
    'TerraformingBuildingScript': '地球化',
    'GalacticPortalBuildingScript': '星际传送门',
    'AsteroidMiningBuildingScript': '小行星开采',
    'TechLabBuildingScript': '科技实验室',
    'CaveExpeditionBuildingScript': '远征·洞穴',
    'IronExpeditionBuildingScript': '远征·铁',
    'UranExpeditionBuildingScript': '远征·铀',
    'PortalExpeditionBuildingScript': '远征·传送门',
}

SHOP_CN = {
    'next_mini_offer_cooldown_timer_key': '下一个迷你报价',
    'gem_mini_offer_cooldown_key': '宝石迷你报价',
    'rain_mini_offer_cooldown_key': '降雨迷你报价',
    'boost_smelting_mini_offer_cooldown_key': '冶炼加成报价',
    'boost_mines_mini_offer_cooldown_key': '矿场加成报价',
    'OFFLINE_RAIN_KEY': '离线降雨',
    'FREE_COINS_COOLDOWN_NAME': '免费金币冷却',
    'special_pack': '特殊礼包',
}

KIND_ORDER = ['production', 'expedition', 'deploy', 'boost', 'bot', 'shop', 'other']
KIND_CN = {'production': '生产/制作', 'expedition': '远征', 'deploy': '建筑部署',
           'boost': '增益/加速', 'bot': '机器人', 'shop': '商店/礼包', 'other': '其它'}


class TimerDialog(QDialog):
    SPEEDS = [2, 3, 5, 10, 20, 50, 100]

    def __init__(self, parent, smart):
        super().__init__(parent)
        self.smart = smart
        self.rows = []          # [{key,kind,label,end,status,uID}]
        self.setWindowTitle('倒计时：立即完成 / 加速（制作·远征·部署…）')
        self.resize(900, 720)
        self.setStyleSheet(STYLE)

        v = QVBoxLayout(self)
        head = QLabel('⏱ 倒计时（制作 / 远征 / 部署 / 商店）')
        head.setStyleSheet('font-size:19px;font-weight:bold;color:#fff;')
        v.addWidget(head)

        tip = QLabel(
            '为什么「游戏变速」不能让制作变快：制作/冶炼/远征的计时都在同一个调度器里，\n'
            '存的是绝对结束时间 endTime（= System.currentTimeMillis()/1000 + 时长），\n'
            '界面上的进度/倒计时 = endTime − 当前墙钟。放大 deltaTime 动不了墙钟，\n'
            '所以开 20 倍只会让动画播快，进度条一秒还是一秒。\n'
            '这里直接改 endTime：「立即完成」= 等价于计时走完；「加速」= 按倍率把 endTime 往前推。')
        tip.setStyleSheet('color:#7a8399;')
        tip.setWordWrap(True)
        v.addWidget(tip)

        row0 = QHBoxLayout()
        row0.addWidget(QLabel('筛选'))
        self.cmb_kind = QComboBox()
        self.cmb_kind.addItem('全部', None)
        for k in KIND_ORDER:
            self.cmb_kind.addItem(KIND_CN[k], k)
        self.cmb_kind.setCurrentIndex(1)          # 默认 生产/制作
        self.cmb_kind.currentIndexChanged.connect(self.reload)
        row0.addWidget(self.cmb_kind, 1)
        b_ref = QPushButton('🔄 刷新')
        b_ref.clicked.connect(self.reload)
        row0.addWidget(b_ref)
        v.addLayout(row0)

        row = QHBoxLayout()
        b_all = QPushButton('⚡ 立即完成（当前筛选）')
        b_all.setObjectName('go')
        b_all.clicked.connect(lambda: self.on_finish(None))
        row.addWidget(b_all)
        b_sel = QPushButton('⚡ 立即完成（选中）')
        b_sel.clicked.connect(lambda: self.on_finish('__SEL__'))
        row.addWidget(b_sel)
        b_claim = QPushButton('🎁 领取远征奖励')
        b_claim.setObjectName('ad')
        b_claim.clicked.connect(self.on_finish_claim)
        row.addWidget(b_claim)
        v.addLayout(row)

        self.tb = QTableWidget(0, 4)
        self.tb.setHorizontalHeaderLabels(['类别', '目标', '剩余时间', '结束时间'])
        self.tb.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tb.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (2, 3):
            self.tb.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tb.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tb.setSelectionMode(QAbstractItemView.SingleSelection)
        v.addWidget(self.tb, 1)

        g = QGroupBox('加速（作用于「当前筛选」，真实改写 endTime）')
        f = QHBoxLayout(g)
        f.addWidget(QLabel('倍率'))
        self.cmb_sp = QComboBox()
        for m in self.SPEEDS:
            self.cmb_sp.addItem('%dx' % m, m)
        self.cmb_sp.setCurrentIndex(2)
        f.addWidget(self.cmb_sp)
        b_on = QPushButton('开启加速')
        b_on.clicked.connect(self.on_accel_on)
        f.addWidget(b_on)
        b_off = QPushButton('停止加速')
        b_off.setObjectName('stop')
        b_off.clicked.connect(self.on_accel_off)
        f.addWidget(b_off)
        self.lbl_sp = QLabel('加速：关闭')
        self.lbl_sp.setStyleSheet('color:#7a8399;')
        f.addWidget(self.lbl_sp, 1)
        v.addWidget(g)

        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMinimumHeight(150)
        v.addWidget(self.log)
        note = QLabel('⚠ 立即完成 = 等价于计时自然走完（游戏自己的到期路径），会正常产出/解锁；\n'
                      '⚠ 加速与变速一样是运行时行为，游戏重启后失效。')
        note.setStyleSheet('color:#e0a04a;')
        note.setWordWrap(True)
        v.addWidget(note)
        close = QPushButton('关闭'); close.clicked.connect(self.reject)
        v.addWidget(close)

        self.tick = QTimer(self)
        self.tick.setInterval(1000)
        self.tick.timeout.connect(self.redraw_times)
        self.tick.start()
        self.reload()

    # ---------- 工具 ----------
    def echo(self, s):
        self.log.append(s)

    def kinds(self):
        k = self.cmb_kind.currentData()
        return [k] if k else None

    def kind_cn(self):
        return self.cmb_kind.currentText()

    @staticmethod
    def _cb_cn(cb):
        name = (cb or '').split('@')[0].split('.')[-1]
        return CB_CN.get(name, name or '—')

    def _label(self, t, scripts):
        kind = t.get('kind')
        cb = self._cb_cn(t.get('cb'))
        if kind == 'production':
            return '%s · 槽位 %s' % (cb, t.get('slot'))
        if kind == 'expedition':
            sc = scripts.get(t.get('uID')) or {}
            st = sc.get('state') or {}
            cave = self._cave_name(sc.get('caveTitle'))
            item = exp_item_cn(st.get('expeditionId'))
            if item and cave:
                return '%s（%s）' % (item, cave)
            return cave or '远征中'
        if kind == 'deploy':
            return '建筑部署 %s…' % str(t.get('uID'))[:8]
        if kind == 'boost':
            return '增益 %s…' % str(t.get('key'))[5:13]
        if kind == 'bot':
            return '机器人动作 %s' % str(t.get('key')).split('_')[-1]
        if kind == 'shop':
            return SHOP_CN.get(t.get('key'), t.get('key'))
        return t.get('key')

    @staticmethod
    def _cave_name(raw):
        if not raw:
            return ''
        s = str(raw)
        if s.startswith('$'):
            s = s[1:]
        if s.startswith('CAVE_') and s.endswith('_TITLE'):
            s = s[5:-6]
        return exp_cave_cn(s.lower())

    # ---------- 读 ----------
    def reload(self):
        if not self.smart.ok:
            self.echo('（智能通道未连接）'); return
        try:
            d = self.smart.timers_list(self.kinds())
        except Exception as e:
            self.echo('❌ 读取失败：%s' % e); return
        if d.get('err'):
            self.echo('⚠ ' + str(d['err']))
        scripts = {s.get('uID'): s for s in (d.get('scripts') or []) if isinstance(s, dict)}
        rows = []
        for t in (d.get('tasks') or []):
            rows.append({'key': t.get('key'), 'kind': t.get('kind'),
                         'label': self._label(t, scripts),
                         'end': t.get('endTime'), 'status': '进行中'})
        # 远征: 在跑但调度器里没任务的 / 待领取的
        if self.kinds() in (None, ['expedition']):
            have = set(t.get('uID') for t in (d.get('tasks') or []))
            for uid, sc in scripts.items():
                st = sc.get('state') or {}
                cave = self._cave_name(sc.get('caveTitle'))
                if st.get('inProgress') and uid not in have:
                    rows.append({'key': sc.get('j1'), 'kind': 'expedition',
                                 'label': '%s（%s）' % (exp_item_cn(st.get('expeditionId')), cave),
                                 'end': None, 'status': '进行中（无倒计时任务）'})
                if st.get('itemToClaim'):
                    rows.append({'key': None, 'kind': 'expedition',
                                 'label': '待领取：%s' % exp_item_cn(st.get('itemToClaim')),
                                 'end': None, 'status': '待领取'})
        rows.sort(key=lambda r: (r['end'] is None, r['end'] or 0))
        self.rows = rows
        self.tb.setRowCount(len(rows))
        for i, r in enumerate(rows):
            it0 = QTableWidgetItem(KIND_CN.get(r['kind'], r['kind']))
            it0.setData(Qt.UserRole, r)
            self.tb.setItem(i, 0, it0)
            self.tb.setItem(i, 1, QTableWidgetItem(r['label']))
            self.tb.setItem(i, 2, QTableWidgetItem('—'))
            self.tb.setItem(i, 3, QTableWidgetItem(
                time.strftime('%H:%M:%S', time.localtime(r['end'])) if r['end'] else '—'))
        self.redraw_times()
        if not rows:
            self.echo('「%s」里现在没有进行中的倒计时（换个筛选，或回游戏开一个）。' % self.kind_cn())

    def redraw_times(self):
        now = time.time()
        for i, r in enumerate(self.rows):
            item = self.tb.item(i, 2)
            if item is None:
                continue
            if r.get('end'):
                v = r['end'] - now
                item.setText(fmt_clock(v) + ('   ⏰ 已完成' if v <= 0 else ''))
            else:
                item.setText('—')

    def selected(self):
        i = self.tb.currentRow()
        if i < 0 or i >= len(self.rows):
            return None
        return self.rows[i]

    # ---------- 写 ----------
    def on_finish(self, which):
        if not self.smart.ok:
            QMessageBox.warning(self, '未连接', '请先点主界面的「连接智能通道」。'); return
        key = None
        if which == '__SEL__':
            r = self.selected()
            if not r or not r.get('key'):
                QMessageBox.information(self, '提示', '先在上面的列表里点一行，再点这个按钮。'); return
            key = r['key']
        try:
            res = self.smart.timer_finish(key, self.kinds())
        except Exception as e:
            self.echo('❌ %s' % e); return
        if res.get('err'):
            self.echo('⚠ ' + str(res['err'])); return
        for d in res.get('done') or []:
            b = d.get('before')
            self.echo('✅ [%s] 剩余 %s 已归零（%s…）'
                      % (KIND_CN.get(d.get('kind'), d.get('kind')),
                         fmt_clock(b) if (b is not None and b >= 0) else '—',
                         str(d.get('key'))[-14:]))
        self.echo('   方式：%s —— 游戏下一帧就会判定到期。' % res.get('via'))
        QTimer.singleShot(700, self.reload)

    def on_finish_claim(self):
        if not self.smart.ok:
            QMessageBox.warning(self, '未连接', '请先点主界面的「连接智能通道」。'); return
        try:
            res = self.smart.timer_finish(None, ['expedition'])
            if not res.get('err'):
                self.echo('✅ 已触发远征完成（方式 %s）' % res.get('via'))
            else:
                self.echo('（远征：%s）' % res.get('err'))
        except Exception as e:
            self.echo('❌ 完成失败：%s' % e); return
        self.echo('   等 0.7 秒让游戏跑完到期逻辑，再领奖励…')
        QTimer.singleShot(700, self._do_claim)

    def _do_claim(self):
        try:
            r = self.smart.exp_claim()
        except Exception as e:
            self.echo('❌ 领取失败：%s' % e); return
        got = r.get('claimed') or []
        for c in got:
            if c.get('err'):
                self.echo('❌ ' + str(c['err']))
            else:
                self.echo('🎁 已领取：%s' % exp_item_cn(c.get('item')))
        if not got:
            self.echo('（没有可领取的远征奖励：%s）' % r.get('err'))
        self.reload()

    def on_accel_on(self):
        if not self.smart.ok:
            QMessageBox.warning(self, '未连接', '请先点主界面的「连接智能通道」。'); return
        m = self.cmb_sp.currentData()
        try:
            r = self.smart.timer_speed(m, self.kinds())
            self.echo('✅ 加速已开启：%sx，作用于「%s」（当前 %s 个计时）'
                      % (r.get('mult'), self.kind_cn(), r.get('keys')))
            self.lbl_sp.setText('加速：%sx 运行中 · %s' % (r.get('mult'), self.kind_cn()))
            self.lbl_sp.setStyleSheet('color:#5ed6a0;font-weight:bold;')
        except Exception as e:
            self.echo('❌ %s' % e)

    def on_accel_off(self):
        try:
            r = self.smart.timer_speed(1, self.kinds())
            self.echo('已停止加速（倍率 %s）' % r.get('mult'))
        except Exception as e:
            self.echo('❌ %s' % e)
        self.lbl_sp.setText('加速：关闭')
        self.lbl_sp.setStyleSheet('color:#7a8399;')

    def done(self, code):
        try:
            self.tick.stop()
            if self.smart.ok:
                self.smart.timer_speed(1, None)
        except Exception:
            pass
        super().done(code)

    def reject(self):
        try:
            self.tick.stop()
            if self.smart.ok:
                self.smart.timer_speed(1, None)
        except Exception:
            pass
        super().reject()


# =====================================================================
#  机制杀助手
# =====================================================================

MECH_TIPS = {
    'u4.j': ('熔岩岩块 LavaBlock',
             '未冻结时 getHitMod() 强制返回 0 —— 任何攻击伤害都是 0。\n'
             '正解：技术研究所（必须建在「上一段」）+ 低温冷冻（耗 1 液态氮）。'),
    'u4.i': ('铁长老 IRON_BOSS',
             'getHitMod() 恒返回 0（地球最底 row 1079，免疫表为空）。\n'
             '正解：造完整枚火箭并发射（终局剧情）→ a5.e → u4.i.A()。'),
    'u4.a': ('铝长老 ALUMINIUM_BOSS',
             'getHitMod() 恒返回 0，还免疫 mining-laser / fire-cannon 等。\n'
             '剧情设计上就是「打不动、绕开它」。'),
}


class MechDialog(QDialog):
    """机制杀助手：检测当前街区的免伤机制，一键完成前置条件，并列出做了什么。"""

    def __init__(self, parent, smart):
        super().__init__(parent)
        self.smart = smart
        self.setWindowTitle('机制杀助手（免伤街区 → 自动完成前置条件）')
        self.resize(860, 700)
        self.setStyleSheet(STYLE)

        v = QVBoxLayout(self)
        head = QLabel('🧩 机制杀助手')
        head.setStyleSheet('font-size:19px;font-weight:bold;color:#fff;')
        v.addWidget(head)

        tip = QLabel(
            '有些街区在设计上「不吃伤害」，必须完成特定机制才能推进 —— 这不是你 DPS 不够，\n'
            '而是伤害被乘了 0（伤害 = 原始伤害 × 街区的 getHitMod()）。\n'
            '本面板：① 只读检测当前街区需要什么机制 + 终局前置清单；② 一键执行解锁动作；\n'
            '③ 逐条列出做了什么。\n'
            '⚠ 铁长老（u4.i）例外：它由终局剧情杀，**不能用伤害杀** —— 它是地球最底层街区，\n'
            '   打碎后矿区背景渲染器取不到下一层的 bgWall 纹理，渲染线程直接 NPE 闪退（已实测两次）。\n'
            '   「✅ 一键完成前置条件」会**直接走存档级方案**：调游戏自己的终局完成流程 a5.e.O0()，\n'
            '   写入 bulkConfig["GALACTIC_MOVIE_SUCCEED_DONE"]=true → 铁长老即进入已击败状态，重启仍有效。')
        tip.setWordWrap(True)
        tip.setStyleSheet('color:#9aa4b8;')
        v.addWidget(tip)

        # ---- 检测结果 ----
        self.lbl = QLabel('点下面「🔍 检测当前街区」开始。')
        self.lbl.setWordWrap(True)
        self.lbl.setStyleSheet(
            'background:#1b1f2a;border:1px solid #2c3242;border-radius:6px;'
            'padding:10px;color:#dfe5f0;font-size:13px;')
        self.lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl)

        row = QHBoxLayout()
        self.btn_scan = QPushButton('🔍 检测当前街区')
        self.btn_scan.setObjectName('ad')
        self.btn_scan.clicked.connect(self.on_scan)
        row.addWidget(self.btn_scan)

        self.btn_fix = QPushButton('✅ 一键完成前置条件')
        self.btn_fix.setObjectName('ad')
        self.btn_fix.clicked.connect(self.on_fix)
        row.addWidget(self.btn_fix)

        self.btn_grant = QPushButton('🎁 补齐终局材料')
        self.btn_grant.setToolTip('把 receiver-skeleton / radar / portal-for-mankind 各 1 个放进仓库（终局三栋建筑的造价）')
        self.btn_grant.clicked.connect(self.on_grant)
        row.addWidget(self.btn_grant)

        self.btn_bypass = QPushButton('⚠ 强制旁路（可能闪退）')
        self.btn_bypass.setObjectName('stop')
        self.btn_bypass.setToolTip('把 getHitMod 强制成 1.0。若当前街区是最底层街区（如铁长老 row 1079），'
                                   '打碎它的瞬间会渲染 NPE 闪退')
        self.btn_bypass.clicked.connect(self.on_bypass)
        row.addWidget(self.btn_bypass)

        self.btn_restore = QPushButton('↩ 撤销旁路')
        self.btn_restore.setObjectName('stop')
        self.btn_restore.clicked.connect(self.on_restore)
        row.addWidget(self.btn_restore)

        row.addStretch(1)
        self.lbl_st = QLabel('旁路：关')
        self.lbl_st.setStyleSheet('color:#7a8399;')
        row.addWidget(self.lbl_st)
        v.addLayout(row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(220)
        v.addWidget(self.log, 1)

        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        v.addWidget(close)

        if self.smart.ok:
            self.on_scan()
            self.on_status()

    # ---------- 输出 ----------
    def echo(self, s):
        self.log.append(str(s))

    def hr(self):
        self.echo('─' * 72)

    def on_status(self):
        if not self.smart.ok:
            return
        try:
            st = self.smart.mech_status()
            self.lbl_st.setText('旁路：%s' % ('开' if st.get('on') else '关'))
        except Exception:
            pass

    # ---------- 检测 ----------
    def on_scan(self):
        if not self.smart.ok:
            self.lbl.setText('智能通道未连接 —— 先点主界面的「🔌 连接智能通道（Frida）」。')
            return
        try:
            d = self.smart.mech_scan()
        except Exception as e:
            self.lbl.setText('读取失败：%s' % e)
            return
        if not d.get('found'):
            self.lbl.setText('⚠ %s' % (d.get('err') or '读不到当前街区'))
            return
        mech = d.get('mech') or {}
        lines = [
            '当前街区：段 %s / 行 %s     血量 %s' % (d.get('seg'), d.get('row'), d.get('maxHp')),
            '类型    ：%s（类 %s / getType()=%s）' % (d.get('name'), d.get('cls'), d.get('type')),
            '免伤系数：getHitMod() = %s%s' % (
                d.get('hitMod'),
                '   ← 0 = 伤害被乘 0，打不动' if d.get('hitMod') == 0 else '   ← 可以正常打'),
            '免疫表  ：%s' % (d.get('immunity') or '（空）'),
            '判定    ：%s' % d.get('verdict'),
        ]
        if mech:
            lines.append('需要的机制：%s' % mech.get('name'))
            lines.append('             %s' % mech.get('need'))
        if d.get('cls') == 'u4.j':
            p = d.get('prereq') or {}
            lines.append('前置状态：技术研究所 %s 座（段位 %s，需要段 %s）｜液态氮 %s 个｜已冻结 %s'
                         % (p.get('techLab'), p.get('techLabSegments'), p.get('needSegment'),
                            p.get('liquidNitrogen'), d.get('prereqFrozen')))
        if d.get('flags'):
            f = d['flags']
            lines.append('BOSS 标志：M(火箭任务已开)=%s  O=%s  P(已击败)=%s  R=%s'
                         % (f.get('M'), f.get('O'), f.get('P'), f.get('R')))
        eg = d.get('endgame')
        if eg:
            lines.append('')
            lines.append('【终局前置清单】发射剧情标记 GALACTIC_MOVIE_SUCCEED_DONE = %s' % eg.get('movieDone'))
            for b in eg.get('buildings') or []:
                have = b.get('matHave')
                lines.append('  %s %-8s 建筑=%s   材料 %s = %s'
                             % ('✔' if b.get('built') else '✘', b.get('cn'),
                                ('已建 %s 座' % b.get('built')) if b.get('built') else '未建',
                                b.get('matCn'), ('—' if have is None else have)))
            if eg.get('ready'):
                lines.append('  ⇒ 三栋都齐了：走完发射剧情即可（a5.e → S0 → u4.i.A()）')
            else:
                lines.append('  ⇒ 游戏原本的达成路径还缺 %d 项：%s'
                             % (len(eg.get('missing') or []), '；'.join(eg.get('missing') or [])))
                lines.append('     （已经不需要走了 —— 点「✅ 一键完成前置条件」会直接走存档级方案，')
                lines.append('       把 GALACTIC_MOVIE_SUCCEED_DONE 写进存档，铁长老即视为已击败）')
        self.lbl.setText('\n'.join(lines))
        self.hr()
        self.echo('检测：%s / %s / hitMod=%s / 机制=%s'
                  % (d.get('cls'), d.get('name'), d.get('hitMod'), mech.get('how') or '无'))

    # ---------- 一键完成 ----------
    def on_fix(self):
        if not self.smart.ok:
            QMessageBox.information(self, '先连智能通道', '这个功能需要先点「🔌 连接智能通道（Frida）」。')
            return
        self.echo('▶ 一键完成前置条件 …')
        # 铁长老是剧情杀：会直接写存档跳过终局，先确认
        try:
            _s = self.smart.mech_scan()
            if _s.get('type') == 'IRONBOSS' or _s.get('cls') == 'u4.i':
                _m = chr(10).join([
                    '铁长老不是靠打的 —— 它是终局剧情杀。',
                    '',
                    '这一步会走游戏自己的终局完成流程（a5.e.O0()），等于「终局已经走完」：',
                    '  · 存档 bulkConfig 写入 GALACTIC_MOVIE_SUCCEED_DONE = true',
                    '  · 解锁研究建筑 / 银河传送门 / radar，开启 research_building_group',
                    '  · 铁长老标记为已击败（血条消失）',
                    '  · 重启游戏后依然有效',
                    '',
                    '代价：原本的发射剧情/结局动画就跳过了。继续吗？'])
                if QMessageBox.warning(self, '铁长老：跳过终局剧情？', _m,
                                       QMessageBox.Yes | QMessageBox.No,
                                       QMessageBox.No) != QMessageBox.Yes:
                    self.echo('已取消（什么都没改）')
                    return
        except Exception:
            pass

        try:
            r = self.smart.mech_fix()
        except Exception as e:
            self.echo('❌ %s' % e)
            return
        sc = r.get('scan') or {}
        self.echo('  目标：%s / %s（段 %s 行 %s）'
                  % (sc.get('cls'), sc.get('mech') or sc.get('name'), sc.get('seg'), sc.get('row')))
        self.echo('  路径：%s' % (r.get('via') or '—'))
        self.hr()
        self.echo('【已完成的内容】')
        for i, s in enumerate(r.get('done') or [], 1):
            self.echo('  %2d. %s' % (i, s))
        if r.get('err'):
            self.echo('  ⚠ %s' % r['err'])
        self.hr()
        if r.get('via') == 'none':
            self.echo('  → 当前街区不是机制杀，没做任何改动。')
        elif r.get('via') == 'freeze':
            self.echo('  → 冻结已生效，现在回游戏直接打它即可。')
        elif r.get('via') == 'story':
            self.echo('  → 铁长老不能靠伤害杀，按上面清单补完前置才有终局。')
        elif r.get('via') == 'report':
            self.echo('  → 这个街区官方没有击败流程，什么都没改。')
        elif r.get('via') == 'bypass' or 'bypass' in str(r.get('via')):
            self.echo('  → 旁路已开：回游戏直接打它。游戏重启前一直有效。')
        self.on_status()
        QTimer.singleShot(400, self.on_scan)

    def on_bypass(self):
        if not self.smart.ok:
            return
        msg = (chr(10).join([
            '旁路会把街区的 getHitMod() 强制成 1.0，让你能打它。',
            '',
            '但如果当前街区是地球最底层（铁长老 row 1079 等），',
            '打碎它的瞬间矿区背景渲染器取不到下一层的纹理 → 渲染线程 NPE → 游戏闪退。',
            '（这条已经实测撞过两次）',
            '',
            '继续吗？']))
        r = QMessageBox.warning(self, '⚠ 强制旁路有闪退风险', msg,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        try:
            d = self.smart.mech_bypass()
        except Exception as e:
            self.echo('❌ %s' % e); return
        self.echo('▶ 强制旁路 …')
        self.echo('  ⚠ %s' % d.get('warn'))
        for s2 in d.get('done') or []:
            self.echo('  · %s' % s2)
        if d.get('err'):
            self.echo('  ❌ %s' % d['err'])
        self.on_status()

    def on_grant(self):
        if not self.smart.ok:
            QMessageBox.information(self, '先连智能通道', '这个功能需要先点「🔌 连接智能通道（Frida）」。')
            return
        self.echo('▶ 补齐终局材料（receiver-skeleton / radar / portal-for-mankind 各 1）…')
        try:
            r = self.smart.mech_grant()
        except Exception as e:
            self.echo('❌ %s' % e); return
        for s2 in r.get('granted') or []:
            self.echo('  🎁 %s' % s2)
        if r.get('err'):
            self.echo('  ⚠ %s' % r['err'])
        self.echo('  → 回游戏：仓库里能看到这 3 个材料；造好 3 栋建筑后放进矿里。')
        QTimer.singleShot(400, self.on_scan)

    def on_restore(self):
        if not self.smart.ok:
            return
        try:
            r = self.smart.mech_restore()
            for s in r.get('done') or []:
                self.echo('  ↩ %s' % s)
            if not (r.get('done') or []):
                self.echo('  （旁路本来就没开）')
            if r.get('err'):
                self.echo('  ⚠ %s' % r['err'])
        except Exception as e:
            self.echo('❌ %s' % e)
        self.on_status()

    def reject(self):
        # 旁路不随窗口关闭撤销 —— 你要关掉面板回游戏打它。
        # 想撤掉就点面板里的「↩ 撤销旁路」，或游戏重启/断开智能通道自动失效。
        super().reject()


# =====================================================================
#  存档字段总览
# =====================================================================

class SaveOverviewDialog(QDialog):
    def __init__(self, parent, smart):
        super().__init__(parent)
        self.smart = smart
        self.rows = []
        self.setWindowTitle('存档字段总览（当前值一览）')
        self.resize(760, 720)
        self.setStyleSheet(STYLE)

        v = QVBoxLayout(self)
        head = QLabel('📋 存档字段总览')
        head.setStyleSheet('font-size:19px;font-weight:bold;color:#fff;')
        v.addWidget(head)
        tip = QLabel('这里列出游戏存档里所有数值字段的当前值（工具直接读出来的）。\n'
                     '双击任意一行即可改；字符串/列表类字段不会显示。')
        tip.setStyleSheet('color:#7a8399;')
        tip.setWordWrap(True)
        v.addWidget(tip)

        row = QHBoxLayout()
        self.ed = QLineEdit(); self.ed.setPlaceholderText('🔍 筛选字段名，例如：gift / coins / level')
        self.ed.textChanged.connect(self.refill)
        row.addWidget(self.ed, 3)
        b = QPushButton('刷新'); b.clicked.connect(self.reload)
        row.addWidget(b, 1)
        v.addLayout(row)

        self.tb = QTableWidget(0, 3)
        self.tb.setHorizontalHeaderLabels(['字段', '类型', '当前值'])
        self.tb.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tb.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tb.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tb.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tb.itemDoubleClicked.connect(self.on_edit)
        v.addWidget(self.tb, 1)

        close = QPushButton('关闭'); close.clicked.connect(self.reject)
        v.addWidget(close)
        self.reload()

    def reload(self):
        if not self.smart.ok:
            QMessageBox.warning(self, '未连接', '智能通道没打开')
            self.reject(); return
        try:
            self.rows = [r for r in self.smart.dump() if r['value'] is not None]
        except Exception as e:
            QMessageBox.warning(self, '读取失败', str(e)); return
        self.refill()

    def refill(self):
        txt = self.ed.text().strip().lower()
        data = [r for r in self.rows if not txt or txt in r['name'].lower()]
        self.tb.setRowCount(len(data))
        for i, r in enumerate(data):
            it0 = QTableWidgetItem(r['name']); it0.setData(Qt.UserRole, r)
            self.tb.setItem(i, 0, it0)
            self.tb.setItem(i, 1, QTableWidgetItem(r['type']))
            self.tb.setItem(i, 2, QTableWidgetItem(fmt_num(r['value'])))

    def on_edit(self, item):
        r = self.tb.item(item.row(), 0).data(Qt.UserRole)
        cur = fmt_num(r['value'])
        txt, ok = QInputDialog.getText(self, '改字段',
                                       '字段 %s（%s）\n当前值：%s\n\n改成：' % (r['name'], r['type'], cur))
        if not ok or not txt.strip():
            return
        try:
            val = float(txt.strip()) if r['type'] in ('float', 'double') else int(float(txt.strip()))
        except ValueError:
            QMessageBox.warning(self, '格式不对', '请输入数字'); return
        try:
            res = self.smart.write('field', r['name'], val)
            QMessageBox.information(self, '已写入', '%s → %s' % (r['name'], res[1]))
        except Exception as e:
            QMessageBox.warning(self, '写入失败', str(e))
        self.reload()


# =====================================================================
#  修改对话框(智能 / 手动)
# =====================================================================

class ModifyDialog(QDialog):
    def __init__(self, parent, item, loc, smart):
        super().__init__(parent)
        self.item = item
        self.loc = loc
        self.smart = smart
        self.cands = None
        self.job = None
        self.job2 = None
        self.kind = item_kind(item)
        self.is_spell = item.get('field') == '__SPELL_CD__'
        self.is_ad = item.get('field') == '__ADS__'
        self.is_exp = item.get('field') == '__TIMERS__'
        self.is_mech = item.get('field') == '__MECH__'
        self._done = False

        self.setWindowTitle('修改：' + item['name'])
        self.setMinimumWidth(580)
        self.setStyleSheet(STYLE)

        v = QVBoxLayout(self)
        head = QLabel('%s  %s' % (item['icon'], item['name']))
        head.setStyleSheet('font-size:19px;font-weight:bold;color:#fff;')
        v.addWidget(head)
        tip = QLabel(item.get('hint', ''))
        tip.setStyleSheet('color:#7a8399;')
        tip.setWordWrap(True)
        v.addWidget(tip)

        if self.is_ad:
            v.addWidget(QLabel('这个功能在独立面板里：'))
            b = QPushButton('打开广告面板')
            b.setObjectName('ad')
            b.clicked.connect(lambda: AdDialog(self, self.smart).exec_())
            v.addWidget(b)
            close = QPushButton('关闭'); close.clicked.connect(self.reject)
            v.addWidget(close)
            return

        if self.is_exp:
            v.addWidget(QLabel('这个功能在独立面板里：'))
            b = QPushButton('打开倒计时面板（制作 / 远征 立即完成 + 加速）')
            b.setObjectName('ad')
            b.clicked.connect(lambda: TimerDialog(self, self.smart).exec_())
            v.addWidget(b)
            close = QPushButton('关闭'); close.clicked.connect(self.reject)
            v.addWidget(close)
            return

        if self.is_mech:
            v.addWidget(QLabel('这个功能在独立面板里：'))
            b = QPushButton('打开机制杀助手（自动完成前置条件 + 列出做了什么）')
            b.setObjectName('ad')
            b.clicked.connect(lambda: MechDialog(self, self.smart).exec_())
            v.addWidget(b)
            close = QPushButton('关闭'); close.clicked.connect(self.reject)
            v.addWidget(close)
            return

        # ---- 模式选择 ----
        self.mode_box = QGroupBox('工作方式')
        mb = QVBoxLayout(self.mode_box)
        self.rb_smart = QRadioButton('智能模式（推荐）— 只填目标值，当前值工具自己读')
        self.rb_smart.setChecked(smart.ok)
        self.rb_smart.setEnabled(smart.ok)
        self.rb_manual = QRadioButton('手动模式 — 我照抄游戏里显示的当前值')
        self.rb_manual.setChecked(not smart.ok)
        self.mode_grp = QButtonGroup(self)
        self.mode_grp.addButton(self.rb_smart)
        self.mode_grp.addButton(self.rb_manual)
        mb.addWidget(self.rb_smart)
        mb.addWidget(self.rb_manual)
        if not smart.ok:
            mb.addWidget(QLabel('（智能模式不可用：点主界面「连接智能通道」后重开本窗口）'))
        v.addWidget(self.mode_box)

        # ---- 数值 ----
        g = QGroupBox('数值')
        form = QGridLayout(g)
        self.lbl_cur = QLabel('① 游戏里现在是多少？')
        form.addWidget(self.lbl_cur, 0, 0)
        self.ed_cur = QLineEdit(); self.ed_cur.setPlaceholderText('照抄游戏里显示的数字')
        form.addWidget(self.ed_cur, 0, 1)
        self.cmb_u1 = QComboBox(); self.cmb_u1.addItems(UNITS)
        form.addWidget(self.cmb_u1, 0, 2)
        self.lbl_want = QLabel('② 想改成多少？')
        form.addWidget(self.lbl_want, 1, 0)
        self.ed_want = QLineEdit()
        form.addWidget(self.ed_want, 1, 1)
        self.cmb_u2 = QComboBox(); self.cmb_u2.addItems(UNITS); self.cmb_u2.setCurrentText('百万 (M)')
        form.addWidget(self.cmb_u2, 1, 2)
        v.addWidget(g)

        self.lbl_read = QLabel('')
        self.lbl_read.setStyleSheet('color:#5ed6a0;font-size:15px;')
        self.lbl_read.setWordWrap(True)
        v.addWidget(self.lbl_read)

        row = QHBoxLayout()
        self.btn = QPushButton('一键修改')
        self.btn.setObjectName('go')
        self.btn.clicked.connect(self.on_go)
        row.addWidget(self.btn, 3)
        self.btn_stop = QPushButton('■ 停止')
        self.btn_stop.setObjectName('stop')
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.on_stop)
        row.addWidget(self.btn_stop, 1)
        v.addLayout(row)

        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMinimumHeight(170)
        v.addWidget(self.log)
        close = QPushButton('关闭'); close.clicked.connect(self.reject)
        v.addWidget(close)

        if self.is_spell:
            self.mode_box.hide()
            self.lbl_cur.hide(); self.ed_cur.hide(); self.cmb_u1.hide()
            self.lbl_want.setText('改成多少秒？')
            self.ed_want.setPlaceholderText('直接填秒数，例如 0.1')
            self.cmb_u2.hide()
            tip.setText('把全部已解锁法术的冷却改成该秒数；点「一键修改」后会先列出每个法术的当前冷却。')
        elif self.rb_smart.isChecked():
            self._apply_smart_ui()
        self.rb_smart.toggled.connect(self._on_mode)
        self.rb_manual.toggled.connect(self._on_mode)

    # ---------- 关闭 = 立即停止 ----------
    def reject(self):
        self._shutdown_jobs()
        super().reject()

    def closeEvent(self, ev):
        self._shutdown_jobs()
        super().closeEvent(ev)

    def _shutdown_jobs(self):
        for j in (self.job, self.job2):
            if j is not None and j.isRunning():
                j.stop()
                if not j.wait(4000):
                    j.wait(6000)
        global _ACTIVE_JOB
        if _ACTIVE_JOB is self.job or _ACTIVE_JOB is self.job2:
            _ACTIVE_JOB = None

    def on_stop(self):
        for j in (self.job, self.job2):
            if j is not None and j.isRunning():
                j.stop()
        self.echo('■ 正在中止…（当前这一步跑完就会停）')
        self.btn_stop.setEnabled(False)

    # ---------- UI 切换 ----------
    def _on_mode(self):
        """QRadioButton 互斥由 QButtonGroup 负责, 这里只切 UI"""
        if self.rb_smart.isChecked():
            self._apply_smart_ui()
        else:
            self._apply_manual_ui()

    def _apply_smart_ui(self):
        self.lbl_cur.hide(); self.ed_cur.hide(); self.cmb_u1.hide()
        self.lbl_want.setText('改成多少？')
        self.ed_want.setPlaceholderText('只填目标值，例如 1000000 或 100M')
        self.cmb_u2.setCurrentText('无')
        self.echo('— 智能模式：工具会自己读当前值 —')
        self.read_current()

    def _apply_manual_ui(self):
        self.lbl_cur.show(); self.ed_cur.show(); self.cmb_u1.show()
        self.lbl_want.setText('② 想改成多少？')
        self.ed_want.setPlaceholderText('')
        self.cmb_u2.setCurrentText('百万 (M)')
        self.lbl_read.setText('')
        self.echo('— 手动模式：请照抄游戏里显示的当前值 —')

    # ---------- 智能读 ----------
    def read_current(self):
        if not self.smart.ok:
            return
        try:
            if self.kind == 'currency':
                v = self.smart.read_currency(self.item['field'])
                note = ''
            elif self.kind == 'material':
                v = self.smart.read_material(self.item['field'])
                note = '' if v is not None else '  （仓库里还没有这个资源，改的时候会新建）'
            else:
                v = self.smart.read_field(self.item['field'])
                note = ''
            if v is None:
                self.lbl_read.setText('⚠ 工具读不到这个字段%s' % note)
                self.lbl_read.setStyleSheet('color:#e0a04a;font-size:15px;')
            else:
                self.lbl_read.setText('当前值（工具已读出）：%s%s' % (fmt_num(v), note))
                self.lbl_read.setStyleSheet('color:#5ed6a0;font-size:16px;font-weight:bold;')
        except Exception as e:
            self.lbl_read.setText('读取失败：%s' % e)
            self.lbl_read.setStyleSheet('color:#e06c75;')

    # ---------- 执行 ----------
    def echo(self, s):
        self.log.append(s)

    def _start(self, job):
        global _ACTIVE_JOB
        # 同一时刻只跑一个扫描（每次要占 ~1.2GB 内存）
        if _ACTIVE_JOB is not None and _ACTIVE_JOB.isRunning():
            _ACTIVE_JOB.stop()
            _ACTIVE_JOB.wait(5000)
        _ACTIVE_JOB = job
        self.btn.setEnabled(False)
        self.btn_stop.setEnabled(True)
        job.msg.connect(self.echo)
        job.done.connect(self.on_done)
        job.start()

    def on_go(self):
        want = self.ed_want.text().strip()
        if not want:
            QMessageBox.information(self, '提示',
                                    '请填「改成多少秒」' if self.is_spell else '请填「改成多少」')
            return
        if self.is_spell:
            self.log.clear()
            self.job = Job('run', self.loc, ('spell', want, 'spell'))
            self._start(self.job)
            return
        if self.rb_smart.isChecked():
            self.do_smart(want)
        else:
            self.do_manual(want)

    # ---------- 智能写入 ----------
    def do_smart(self, want_text):
        try:
            target = parse_target(want_text)
        except Exception:
            QMessageBox.warning(self, '格式不对', '填数字，可带 K/M/W/B 后缀，例如 1.5M'); return
        if target < 0:
            QMessageBox.warning(self, '格式不对', '不能是负数'); return
        self.log.clear()
        try:
            self.echo('目标：%s → %s' % (self.item['name'], fmt_num(target)))
            if self.kind == 'material':
                info = self.smart.matinfo(self.item['field'])
                if not info.get('exists'):
                    r = QMessageBox.question(self, '仓库里没有这个资源',
                                             '「%s」还没获得过，仓库里没有这个条目。\n\n'
                                             '要现在新建一个吗？（新建后它会出现在仓库里）'
                                             % self.item['name'],
                                             QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                    if r != QMessageBox.Yes:
                        self.echo('已取消。'); return
            ok, res = self.smart.write(self.kind, self.item['field'], target)
            if ok:
                extra = ''
                if isinstance(res, dict) and res.get('created'):
                    extra = '（新建了条目）'
                self.echo('✅ 写入成功%s，回读 = %s' % (extra, res.get('value') if isinstance(res, dict) else res))
                QMessageBox.information(self, '成功',
                                        '改好了！\n回游戏看数值变化。\n确认无误后记得点主界面的「强制保存进度」')
                self.read_current()
            else:
                err = res.get('err') if isinstance(res, dict) else str(res)
                self.echo('❌ 失败：%s' % err)
                QMessageBox.warning(self, '没成功', str(err))
        except Exception as e:
            self.echo('❌ %s' % e)
            QMessageBox.warning(self, '没成功', str(e))

    # ---------- 手动写入 ----------
    def do_manual(self, want):
        cur = self.ed_cur.text().strip()
        if not cur:
            QMessageBox.information(self, '提示', '两个格子都要填哦'); return
        cur_full = cur + unit_tag(self.cmb_u1.currentText())
        want_full = want + unit_tag(self.cmb_u2.currentText())
        self.log.clear()
        self.echo('目标：%s → %s' % (cur_full, want_full))
        self.job = Job('run', self.loc, (cur_full, want_full, self.item.get('struct', 'auto')))
        self._start(self.job)

    def on_done(self, kind, payload):
        self.btn.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if kind == 'stopped':
            self.echo('■ 已中止。')
            return
        if kind == 'ok':
            good, tot = payload
            self.echo('✅ 修改成功！(%d/%d 个位置)' % (good, tot))
            QMessageBox.information(self, '成功', '改好了！\n回游戏看数值变化。\n确认无误后记得点「强制保存进度」')
        elif kind in ('converge', 'need_more'):
            self.cands = payload
            self.echo('⚠ 这个数值在内存里出现的位置较多(%d 个)，需要再确认一次。' % len(payload))
            self.echo('  1) 回游戏，让这个数值变一点点（买东西 / 升一次级 / 用掉几个）')
            self.echo('  2) 回来把「① 游戏里现在是多少」改成新的数字')
            self.echo('  3) 再点一次按钮')
            self.btn.setText('继续修改（第二步）')
            try:
                self.btn.clicked.disconnect()
            except Exception:
                pass
            self.btn.clicked.connect(self.on_second)
            QMessageBox.information(self, '需要再确认一步',
                                    '这个数值内存里重复较多。\n请回游戏让这个数字变一点点，\n再回来把「现在是多少」改成新数字，\n然后点「继续修改」。')
        elif kind == 'err':
            self.echo('❌ ' + str(payload))
            QMessageBox.warning(self, '没成功', str(payload))

    def on_second(self):
        cur = self.ed_cur.text().strip()
        want = self.ed_want.text().strip()
        if not cur or not want:
            return
        cur_full = cur + unit_tag(self.cmb_u1.currentText())
        want_full = want + unit_tag(self.cmb_u2.currentText())
        self.echo('--- 第二次定位：当前值改为 %s ---' % cur_full)
        self.job2 = Job('converge', self.loc, (self.cands,))
        self.job2.run = lambda: self.job2.converge_round2(self.cands, cur_full, want_full)
        self._start(self.job2)


# =====================================================================
#  重启辅助（重启游戏 -> 自动重连 -> 自动恢复所有钩子）
# =====================================================================

class RestartHelper(QThread):
    """广告屏蔽 / 变速 / 远征加速 都是**运行时钩子**，游戏一重启就没了。
    这个线程做一件事: 先记住当前挂着什么 -> 重启游戏 -> 重连 -> 原样挂回去。"""
    msg = pyqtSignal(str)
    done = pyqtSignal(bool, object)

    def __init__(self, adb, smart):
        super().__init__()
        self.adb = adb
        self.smart = smart
        self.payload = {}

    def _pid(self):
        try:
            return self.adb.pid_of(PKG)
        except Exception:
            return None

    def _wait_pid(self, timeout=90):
        t0 = time.time()
        while time.time() - t0 < timeout:
            p = self._pid()
            if p:
                return p
            time.sleep(1.0)
        return None

    def run(self):
        try:
            self._do()
            self.done.emit(True, self.payload)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.payload['err'] = str(e)
            self.done.emit(False, self.payload)

    def _do(self):
        ad_opts = None
        speed = None
        exp = None
        if self.smart.ok:
            try:
                st = self.smart.ad_status()
                if st.get('hooked'):
                    ad_opts = {'blockInterstitial': st.get('blockInterstitial'),
                               'skipRewarded': st.get('skipRewarded'),
                               'forceReady': st.get('forceReady', True)}
            except Exception as e:
                self.msg.emit('   （读广告状态失败：%s）' % e)
            try:
                s = self.smart.speed_status()
                if s.get('hooked') and s.get('on'):
                    speed = s.get('mult')
            except Exception:
                pass
            try:
                e2 = self.smart.timer_speed_status()
                if e2.get('on'):
                    exp = {'mult': e2.get('mult'), 'kinds': e2.get('kinds')}
            except Exception:
                pass
        _exptxt = ('%sx/%s' % (exp['mult'], exp['kinds'])) if isinstance(exp, dict) else (exp or '关')
        self.msg.emit('① 记录当前辅助状态：广告=%s  全局变速=%s  倒计时加速=%s'
                      % ('开' if ad_opts else '关', speed or '关', _exptxt))
        self.payload['ad_opts'] = ad_opts
        self.payload['speed'] = speed
        self.payload['exp'] = exp

        self.msg.emit('② 断开 Frida 通道（游戏重启会带走会话）…')
        try:
            self.smart.close()
        except Exception:
            pass

        self.msg.emit('③ 结束游戏进程…')
        self.adb.shell('am force-stop %s' % PKG)
        time.sleep(1.2)

        self.msg.emit('④ 重新启动游戏…')
        act = self.adb.shell('cmd package resolve-activity --brief %s' % PKG).strip().split('\n')[-1]
        if act and '/' in act:
            self.adb.shell('am start -n %s' % act)
        else:
            self.msg.emit('   ⚠ 拿不到启动 Activity，请手动点开深城')

        self.msg.emit('⑤ 等游戏进程起来（最多 90 秒）…')
        pid = self._wait_pid(90)
        if not pid:
            raise RuntimeError('90 秒内没等到游戏进程，请手动打开深城，再点「重新连接」+「连接智能通道」')
        self.payload['pid'] = pid
        self.msg.emit('   ✓ 已启动，pid = %d' % pid)

        self.msg.emit('⑥ 等游戏进主界面 / 引擎开始出帧（10 秒）…')
        time.sleep(10.0)

        self.adb.root()
        self.payload['pid'] = self._pid() or pid

        need = bool(ad_opts or speed or exp)
        if need:
            self.msg.emit('⑦ 重连 Frida 智能通道…')
            ok = self.smart.open(self.adb.adb, self.adb.serial)
            if not ok:
                self.payload['smart_ok'] = False
                self.msg.emit('   ✗ 智能通道没连上：' + ' / '.join(self.smart.msgs[-3:]))
            else:
                self.payload['smart_ok'] = True
                self.msg.emit('   ✓ 智能通道已重连')

        if self.smart.ok and ad_opts:
            self.msg.emit('⑧ 恢复广告屏蔽 / 跳过领奖…')
            try:
                self.payload['ad'] = self.smart.ad_install(ad_opts)
                self.msg.emit('   ✓ ' + str(self.payload['ad']))
            except Exception as e:
                self.msg.emit('   ✗ 广告钩子失败：%s' % e)

        if self.smart.ok and speed:
            self.msg.emit('⑨ 恢复全局变速 %sx…' % speed)
            try:
                r = self.smart.set_speed(speed)
                self.payload['speed_res'] = r
                if r.get('err'):
                    self.msg.emit('   ✗ ' + r['err'])
                else:
                    self.msg.emit('   ✓ 已设为 %sx（字段 %s）' % (r.get('mult'), r.get('fields')))
            except Exception as e:
                self.msg.emit('   ✗ 变速失败：%s' % e)

        if self.smart.ok and exp:
            self.msg.emit('⑩ 恢复倒计时加速 %sx（类别 %s）…' % (exp.get('mult'), exp.get('kinds')))
            try:
                r = self.smart.timer_speed(exp.get('mult'), exp.get('kinds'))
                self.msg.emit('   ✓ 加速已开启（%sx，当前计时 %s 个）' % (r.get('mult'), r.get('keys')))
            except Exception as e:
                self.msg.emit('   ✗ 倒计时加速失败：%s' % e)

        self.msg.emit('全部完成。')


class RestartLogDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('重启辅助')
        self.resize(680, 460)
        self.setStyleSheet(STYLE)
        v = QVBoxLayout(self)
        head = QLabel('🔄 重启辅助（重启游戏 + 自动恢复所有辅助）')
        head.setStyleSheet('font-size:17px;font-weight:bold;color:#fff;')
        v.addWidget(head)
        self.log = QTextEdit(); self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        self.btn = QPushButton('关闭')
        self.btn.clicked.connect(self.accept)
        v.addWidget(self.btn)

    def echo(self, s):
        self.log.append(s)


# =====================================================================
#  主窗口
# =====================================================================

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('深城修改器 v5')
        self.resize(940, 780)
        self.setStyleSheet(STYLE)
        self.adb = Adb(guess_adb())
        self.pid = None
        self.loc = None
        self.smart = Smart()
        self.reboot_thread = None
        self._reboot_log = None

        w = QWidget(); self.setCentralWidget(w)
        v = QVBoxLayout(w)

        top = QHBoxLayout()
        self.lbl = QLabel('未连接')
        self.lbl.setStyleSheet('color:#e06c75;font-weight:bold;font-size:16px;')
        top.addWidget(self.lbl)
        top.addStretch(1)
        self.btn_reboot = QPushButton('🔄 重启辅助')
        self.btn_reboot.setObjectName('ad')
        self.btn_reboot.setToolTip('重启游戏 → 自动重连 → 自动恢复广告屏蔽 / 变速 / 远征加速')
        self.btn_reboot.clicked.connect(self.restart_helper)
        top.addWidget(self.btn_reboot)
        for t, fn in (('重新连接', self.connect), ('强制保存进度', self.force_save),
                      ('重启游戏', self.restart), ('备份存档', self.backup)):
            b = QPushButton(t); b.clicked.connect(fn); top.addWidget(b)
        v.addLayout(top)

        top2 = QHBoxLayout()
        self.btn_smart = QPushButton('🔌 连接智能通道（Frida）')
        self.btn_smart.setObjectName('ad')
        self.btn_smart.clicked.connect(self.open_smart)
        top2.addWidget(self.btn_smart)
        self.btn_ads = QPushButton('📺 广告屏蔽 / 跳过领奖')
        self.btn_ads.clicked.connect(self.open_ads)
        top2.addWidget(self.btn_ads)
        self.btn_exp = QPushButton('⏱ 倒计时 / 立即完成')
        self.btn_exp.clicked.connect(self.open_exp)
        top2.addWidget(self.btn_exp)
        self.btn_speed = QPushButton('游戏变速（时间流速）')
        self.btn_speed.clicked.connect(self.open_speed)
        top2.addWidget(self.btn_speed)
        self.btn_ov = QPushButton('📋 存档字段总览')
        self.btn_ov.clicked.connect(self.open_overview)
        top2.addWidget(self.btn_ov)
        self.btn_mech = QPushButton('🧩 机制杀助手')
        self.btn_mech.setObjectName('ad')
        self.btn_mech.setToolTip('街区打不动伤害（免伤）？这里自动完成前置条件，并列出做了什么')
        self.btn_mech.clicked.connect(self.open_mech)
        top2.addWidget(self.btn_mech)
        top2.addStretch(1)
        self.lbl_mech = QLabel('机制旁路：关')
        self.lbl_mech.setStyleSheet('color:#7a8399;')
        top2.addWidget(self.lbl_mech)
        self.lbl_smart = QLabel('智能通道：未连接')
        self.lbl_smart.setStyleSheet('color:#7a8399;')
        top2.addWidget(self.lbl_smart)
        v.addLayout(top2)

        s = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText('🔍 输入要改的东西，比如：金币 / 钻石 / 煤 / 等级 / 广告 / 变速 / 远征')
        self.ed_search.textChanged.connect(self.refill)
        s.addWidget(self.ed_search, 3)
        self.cmb_grp = QComboBox(); self.cmb_grp.addItems(catalog.GROUPS)
        self.cmb_grp.currentTextChanged.connect(self.refill)
        s.addWidget(self.cmb_grp, 1)
        v.addLayout(s)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.open_item)
        v.addWidget(self.list, 1)

        self.lbl_cnt = QLabel('')
        self.lbl_cnt.setStyleSheet('color:#7a8399;')
        v.addWidget(self.lbl_cnt)

        self.refill()
        self.connect()

    # ---------- 连接 ----------
    def connect(self):
        for p in MUMU_PORTS:
            try:
                self.adb.connect('127.0.0.1', p)
            except Exception:
                pass
        devs = self.adb.devices()
        if not devs:
            self.lbl.setText('✗ 没找到 MuMu 模拟器，请先启动模拟器'); return
        self.adb.serial = devs[0]
        self.adb.root()
        self.pid = self.adb.pid_of(PKG)
        if not self.pid:
            self.lbl.setText('✗ 游戏没在运行，请在模拟器里打开深城'); return
        self.loc = E.Locator(self.adb, self.pid)
        ver = ''
        try:
            ver = self.adb.pkg_version(PKG)[0]
        except Exception:
            pass
        self.lbl.setText('● 已连接  MuMu  深城 %s  (pid %d)' % (ver, self.pid))
        self.lbl.setStyleSheet('color:#5ed6a0;font-weight:bold;font-size:16px;')

    # ---------- 智能通道 ----------
    def open_smart(self):
        if self.smart.ok:
            QMessageBox.information(self, '已连接', '智能通道已经在工作啦。')
            return
        if not self.pid:
            self.connect()
        if not self.pid:
            QMessageBox.warning(self, '游戏没运行', '请先在模拟器里打开深城'); return
        self.btn_smart.setEnabled(False)
        self.lbl_smart.setText('智能通道：正在连接…')
        QApplication.processEvents()
        try:
            ok = self.smart.open(self.adb.adb, self.adb.serial)
            if ok:
                self.lbl_smart.setText('智能通道：● 已连接（可以只填目标值了）')
                self.lbl_smart.setStyleSheet('color:#5ed6a0;font-weight:bold;')
                QMessageBox.information(self, '连接成功',
                                        '智能通道已打开。\n\n现在双击任何一项，都可以只填目标值 —— '
                                        '当前值由工具直接读出来。\n也可以点「存档字段总览」看全部字段的当前值。')
            else:
                self.lbl_smart.setText('智能通道：✗ 连接失败')
                self.lbl_smart.setStyleSheet('color:#e06c75;')
                QMessageBox.warning(self, '连接失败',
                                    '连不上 Frida 通道。\n\n详细过程：\n' +
                                    '\n'.join(self.smart.msgs[-8:]) +
                                    '\n\n常见原因：\n'
                                    '· 游戏没在运行\n'
                                    '· frida 版本不是 16.x（pip install "frida==16.7.19"）\n'
                                    '· 模拟器没开 root')
        finally:
            self.btn_smart.setEnabled(True)
            self.refresh_mech_lbl()

    def _need_smart(self):
        if self.smart.ok:
            return True
        QMessageBox.information(self, '先连智能通道', '这个功能需要先点「🔌 连接智能通道（Frida）」。')
        return False

    def open_ads(self):
        if self._need_smart():
            AdDialog(self, self.smart).exec_()

    def open_speed(self):
        if self._need_smart():
            SpeedDialog(self, self.smart).exec_()

    def open_exp(self):
        if self._need_smart():
            TimerDialog(self, self.smart).exec_()

    def open_overview(self):
        if self._need_smart():
            SaveOverviewDialog(self, self.smart).exec_()

    def open_mech(self):
        if self._need_smart():
            MechDialog(self, self.smart).exec_()
            self.refresh_mech_lbl()

    def refresh_mech_lbl(self):
        """主界面显示机制旁路状态（开着的话要看得见，别悄悄生效）"""
        txt, col = '机制旁路：关', '#7a8399'
        if self.smart.ok:
            try:
                st = self.smart.mech_status()
                if st.get('on'):
                    txt, col = '机制旁路：● 开（打完后点「🧩 机制杀助手」→ 撤销）', '#e8b34b'
            except Exception:
                pass
        self.lbl_mech.setText(txt)
        self.lbl_mech.setStyleSheet('color:%s;' % col)

    # ---------- 列表 ----------
    def refill(self):
        txt = self.ed_search.text()
        grp = self.cmb_grp.currentText()
        items = catalog.search(txt, grp)
        self.list.clear()
        for it in items:
            row = QListWidgetItem('%s  %s        %s' % (it['icon'], it['name'],
                                  ('（' + it['field'] + '）') if it['group'] == '资源' else ''))
            row.setData(Qt.UserRole, it)
            self.list.addItem(row)
        self.lbl_cnt.setText('共 %d 项 — 双击任意一项开始修改' % len(items))

    def open_item(self, row):
        if not self.loc:
            QMessageBox.warning(self, '未连接', '请先确保 MuMu 已启动且游戏正在运行')
            self.connect()
            return
        self.pid = self.adb.pid_of(PKG)
        if not self.pid:
            QMessageBox.warning(self, '游戏未运行', '请先在模拟器里打开深城')
            self.connect(); return
        self.loc.pid = self.pid
        it = row.data(Qt.UserRole)
        if it.get('field') == '__ADS__':
            self.open_ads(); return
        if it.get('field') == '__TIMERS__':
            self.open_exp(); return
        if it.get('field') == '__MECH__':
            self.open_mech(); return
        ModifyDialog(self, it, self.loc, self.smart).exec_()

    # ---------- 工具 ----------
    def force_save(self):
        if not self.pid:
            return
        self.adb.shell('input keyevent 3')
        time.sleep(1.0)
        act = self.adb.shell('cmd package resolve-activity --brief %s' % PKG).strip().split('\n')[-1]
        if act and '/' in act:
            self.adb.shell('am start -n %s' % act)
        QMessageBox.information(self, '已触发保存', '已切到后台再切回，游戏会把内存数值写进存档。')

    def restart(self):
        self.adb.shell('am force-stop %s' % PKG)
        time.sleep(0.8)
        act = self.adb.shell('cmd package resolve-activity --brief %s' % PKG).strip().split('\n')[-1]
        if act and '/' in act:
            self.adb.shell('am start -n %s' % act)
        QMessageBox.information(self, '已重启', '游戏已重启，稍等几秒再点「重新连接」。\n（广告屏蔽 / 变速需要重新开启）')

    # ---------- 重启辅助 ----------
    def restart_helper(self):
        if self.reboot_thread is not None and self.reboot_thread.isRunning():
            QMessageBox.information(self, '正在跑', '上一次重启辅助还没结束，等它跑完。')
            return
        if not self.pid:
            self.connect()
        if not self.pid:
            QMessageBox.warning(self, '游戏没运行', '先在模拟器里打开深城（或者先点「重新连接」）。')
            return
        if not self.smart.ok:
            r = QMessageBox.question(
                self, '智能通道没连',
                '智能通道没连上，重启后就没法自动恢复「广告屏蔽 / 变速 / 远征加速」了。\n\n'
                '要只重启游戏（不自动恢复）吗？\n（想自动恢复就先关掉本窗口，点「连接智能通道」再回来）',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        dlg = RestartLogDialog(self)
        self._reboot_log = dlg
        th = RestartHelper(self.adb, self.smart)
        self.reboot_thread = th
        th.msg.connect(dlg.echo)
        th.done.connect(lambda ok, p: self._reboot_done(ok, p, dlg))
        dlg.echo('开始…（游戏会重启，约 20~40 秒）')
        th.start()
        dlg.exec_()

    def _reboot_done(self, ok, payload, dlg):
        if not ok:
            dlg.echo('✗ 失败：%s' % payload.get('err'))
            QMessageBox.warning(self, '重启辅助失败', str(payload.get('err')))
            return
        pid = payload.get('pid')
        if pid:
            self.pid = pid
            self.loc = E.Locator(self.adb, pid)
        if self.smart.ok:
            self.lbl_smart.setText('智能通道：● 已连接（可以只填目标值了）')
            self.lbl_smart.setStyleSheet('color:#5ed6a0;font-weight:bold;')
        else:
            self.lbl_smart.setText('智能通道：未连接')
            self.lbl_smart.setStyleSheet('color:#7a8399;')
        ver = ''
        try:
            ver = self.adb.pkg_version(PKG)[0]
        except Exception:
            pass
        self.lbl.setText('● 已连接  MuMu  深城 %s  (pid %s)' % (ver, self.pid))
        self.lbl.setStyleSheet('color:#5ed6a0;font-weight:bold;font-size:16px;')
        dlg.echo('—— 已回到可用状态：游戏 pid %s，智能通道 %s ——'
                 % (self.pid, '已连接' if self.smart.ok else '未连接'))

    def backup(self):
        import subprocess, datetime
        from adbmem import hidden_kw
        dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'saves', datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
        os.makedirs(dst, exist_ok=True)
        base = [self.adb.adb]
        if self.adb.serial:
            base += ['-s', self.adb.serial]
        for f in ('Demolisher.xml', 'Demolisher Preferences.xml'):
            subprocess.run(base + ['pull', '/data/user/0/%s/shared_prefs/%s' % (PKG, f), dst],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **hidden_kw())
        QMessageBox.information(self, '备份完成', '存档已备份到：\n' + dst)

    def closeEvent(self, ev):
        global _ACTIVE_JOB
        if _ACTIVE_JOB is not None and _ACTIVE_JOB.isRunning():
            _ACTIVE_JOB.stop()
            _ACTIVE_JOB.wait(6000)
        try:
            self.smart.close()
        except Exception:
            pass
        super().closeEvent(ev)


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    m = Main(); m.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
