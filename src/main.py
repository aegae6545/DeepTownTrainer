# -*- coding: utf-8 -*-
"""
DeepTown Trainer  ——  深城 (com.rockbite.deeptown) 全功能修改器
ADB root 直读进程内存, 无需在模拟器内安装任何东西。
"""
import os
import sys
import json
import time
import struct
import datetime

import numpy as np
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QFormLayout, QMessageBox, QFileDialog, QCheckBox,
    QProgressBar, QTabWidget, QTextEdit, QAbstractItemView, QMenu, QAction,
    QSpinBox, QDoubleSpinBox, QListWidget, QTreeWidget, QTreeWidgetItem,
    QDialog, QDialogButtonBox, QShortcut, QListWidgetItem
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adbmem import Adb, MemSnapshot, guess_adb, MUMU_PORTS   # noqa
import scanner                                                # noqa

APP_NAME = "DeepTown Trainer"
PKG = "com.rockbite.deeptown"
SAVE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "saves")

DARK = """
QWidget{background:#16181d;color:#d6d9e0;font-family:'Microsoft YaHei',sans-serif;font-size:13px;}
QMainWindow{background:#12141a;}
QTabWidget::pane{border:1px solid #2a2e38;background:#16181d;}
QTabBar::tab{background:#1c1f27;color:#8b93a7;padding:8px 18px;border:1px solid #2a2e38;}
QTabBar::tab:selected{background:#2d7ff9;color:#fff;}
QGroupBox{border:1px solid #2a2e38;border-radius:6px;margin-top:14px;padding-top:10px;font-weight:bold;}
QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 6px;color:#5fa8ff;}
QPushButton{background:#242833;border:1px solid #333a49;border-radius:5px;padding:6px 14px;color:#d6d9e0;}
QPushButton:hover{background:#2d3442;border-color:#4a5468;}
QPushButton:pressed{background:#2d7ff9;}
QPushButton#accent{background:#2d7ff9;border-color:#2d7ff9;color:#fff;font-weight:bold;}
QPushButton#accent:hover{background:#4a92ff;}
QPushButton#danger{background:#8a2b2b;border-color:#a33;color:#fff;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{background:#0e1014;border:1px solid #333a49;border-radius:4px;padding:5px 8px;color:#e6e9f0;selection-background-color:#2d7ff9;}
QComboBox QAbstractItemView{background:#0e1014;border:1px solid #333a49;selection-background-color:#2d7ff9;}
QTableWidget{background:#0e1014;alternate-background-color:#12141a;gridline-color:#232733;border:none;}
QHeaderView::section{background:#1c2029;color:#9aa4bd;padding:6px;border:none;border-right:1px solid #232733;border-bottom:1px solid #232733;}
QTableWidget::item:selected{background:#2d7ff9;color:#fff;}
QProgressBar{background:#0e1014;border:1px solid #333a49;border-radius:4px;text-align:center;}
QProgressBar::chunk{background:#2d7ff9;}
QTextEdit{background:#0e1014;border:1px solid #333a49;border-radius:4px;}
QStatusBar{background:#1c1f27;}
QTreeWidget{background:#0e1014;border:1px solid #333a49;}
"""


USAGE = """
<h2 style='color:#5fa8ff'>深城修改器 · 使用说明</h2>
<h3 style='color:#61d095'>标准流程(改金币/钻石等任意数值)</h3>
<ol>
<li>打开 MuMu 里的深城, 记住你要改的数值(比如金币 <b>12,345</b>)</li>
<li>本工具顶部自动连上模拟器, 进程选 <b>com.rockbite.deeptown</b></li>
<li>①内存扫描 → 数值填 <code>12345</code>, 类型选 <b>Long (8)</b>(金币用 8 字节; 钻石/等级用 Int 4), 点 <b>首次扫描</b></li>
<li>回游戏让数值变化(挖一次矿/买个东西), 再填新数值点 <b>再次扫描</b>, 反复 2~4 次直到只剩 1~5 个结果</li>
<li>双击结果(或右键→加入修改列表), 到 ②修改列表 填新值 → <b>应用全部数值</b></li>
<li>想永久生效: 切到 ③深城专用 → <b>强制保存进度</b>, 让游戏把内存写回存档</li>
</ol>
<h3 style='color:#61d095'>模糊搜索(不知道具体数值时)</h3>
<ul>
<li>首扫: 比较选 <b>未知初始值</b></li>
<li>回游戏改变数值 → 再扫选 <b>数值增大了 / 减小了 / 变化了 / 未变</b></li>
</ul>
<h3 style='color:#e0a06c'>注意事项</h3>
<ul>
<li>金币在深城里是 <b>long(8字节)</b>; 钻石/水晶、等级、法术点、深度多为 <b>int(4字节)</b>; 带小数的是 <b>float/double</b></li>
<li>数值太大时游戏会显示缩写(1.2M), 此时用"介于"或模糊搜索更稳</li>
<li>改完务必 <b>强制保存</b>, 否则重开游戏会回到存档里的旧值</li>
<li>存档是加密的(<code>Demolisher.xml</code>, base64 密文), 所以走内存改最稳; ④存档管理可整体备份/回滚</li>
<li>游戏内有 <code>isCheater</code> 字段做作弊检测, 数值不要一次改到离谱(建议先小改验证)</li>
</ul>
<h3 style='color:#61d095'>技术信息</h3>
<p>引擎: ADB root 直读 <code>/proc/&lt;pid&gt;/mem</code>, 设备端 gzip 压缩回传, numpy 本地匹配。<br>
1.2GB Java 堆快照约 <b>1.8 秒</b>, 匹配耗时 &lt;0.1 秒。模拟器内<b>不需要安装任何东西</b>, 无注入、无 hook。</p>
"""


def fmt_addr(a):
    return "%016X" % int(a)


def fmt_val(v, label):
    if v is None:
        return "?"
    try:
        if 'Float' in label or 'Double' in label:
            return "%.6g" % float(v)
        return str(int(v))
    except Exception:
        return str(v)


# ─────────────────────────── 扫描线程 ───────────────────────────
class ScanThread(QThread):
    sig_msg = pyqtSignal(str)
    sig_done = pyqtSignal(object, object)

    def __init__(self, parent, mode, snap_holder, label, cmp_op, v1, v2, addrs, prev, region_mode):
        super().__init__(parent)
        self.mode = mode          # 'first' | 'next'
        self.holder = snap_holder  # SnapshotHolder
        self.label = label
        self.cmp_op = cmp_op
        self.v1, self.v2 = v1, v2
        self.addrs, self.prev = addrs, prev
        self.region_mode = region_mode

    def run(self):
        t0 = time.time()
        try:
            if self.mode == 'first' or self.holder.snap is None or self.holder.mode != self.region_mode:
                self.sig_msg.emit("正在抓取内存快照…")
                self.holder.rebuild(self.region_mode, self.sig_msg)
            else:
                self.sig_msg.emit("正在刷新内存快照…")
                self.holder.refresh()
            mb = self.holder.snap.total() / 1048576.0
            self.sig_msg.emit("快照 %.1f MB / %d 区域, 扫描中…" % (mb, len(self.holder.snap.regions)))
            a, v = scanner.scan_snapshot(
                self.holder.snap, self.label, self.cmp_op, self.v1, self.v2,
                self.addrs if self.mode == 'next' else None,
                self.prev if self.mode == 'next' else None)
            self.sig_msg.emit("完成: %d 个结果, 耗时 %.2fs" % (len(a), time.time() - t0))
            self.sig_done.emit(a, v)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.sig_msg.emit("扫描出错: %s" % e)
            self.sig_done.emit(None, None)


class SnapshotHolder:
    def __init__(self, adb, pid):
        self.adb, self.pid = adb, pid
        self.snap = None
        self.mode = None

    def rebuild(self, mode, msg=None):
        regions = self.adb.maps(self.pid)
        sel = scanner.filter_regions(regions, mode)
        sel.sort(key=lambda r: (r['end'] - r['start']), reverse=True)
        self.snap = MemSnapshot(self.adb, self.pid, sel)
        self.mode = mode

    def refresh(self):
        if self.snap:
            self.snap.refresh()


# ─────────────────────────── 主窗口 ───────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("%s  —  深城 Deep Town 修改器" % APP_NAME)
        self.resize(1280, 820)
        self.setStyleSheet(DARK)

        self.adb = Adb(guess_adb())
        self.pid = None
        self.pkg = PKG
        self.holder = None

        self.res_addr = None
        self.res_val = None
        self.prev_val = None
        self.shown = 0
        self.scan_history = []     # 用于"撤销上一次扫描"
        self.saved = []            # 修改列表 [{desc,addr,label,value,lock}]
        self.lock_timer = QTimer(self)
        self.lock_timer.timeout.connect(self._do_lock)

        self._build_ui()
        self.statusBar().showMessage("就绪")
        QTimer.singleShot(200, self.auto_connect)

    # ── UI 构建 ──
    def _build_ui(self):
        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(8, 8, 8, 4)

        tl.addWidget(QLabel("ADB:"))
        self.lbl_adb = QLabel(self.adb.adb)
        self.lbl_adb.setStyleSheet("color:#6b7488;")
        tl.addWidget(self.lbl_adb)

        tl.addSpacing(12)
        tl.addWidget(QLabel("设备:"))
        self.cmb_dev = QComboBox()
        self.cmb_dev.setMinimumWidth(190)
        self.cmb_dev.setEditable(True)
        tl.addWidget(self.cmb_dev)
        b_ref = QPushButton("扫描设备")
        b_ref.clicked.connect(self.scan_devices)
        tl.addWidget(b_ref)

        tl.addSpacing(12)
        tl.addWidget(QLabel("进程:"))
        self.cmb_proc = QComboBox()
        self.cmb_proc.setMinimumWidth(340)
        tl.addWidget(self.cmb_proc)
        b_pr = QPushButton("刷新进程")
        b_pr.clicked.connect(self.refresh_procs)
        tl.addWidget(b_pr)

        self.lbl_state = QLabel("未连接")
        self.lbl_state.setStyleSheet("color:#e06c75;font-weight:bold;")
        tl.addWidget(self.lbl_state)
        tl.addStretch(1)
        top.setLayout(tl)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_scan(), "① 内存扫描")
        self.tabs.addTab(self._tab_list(), "② 修改列表 / 锁定")
        self.tabs.addTab(self._tab_game(), "③ 深城专用")
        self.tabs.addTab(self._tab_save(), "④ 存档管理")
        self.tabs.addTab(self._tab_mem(), "⑤ 内存查看器")
        self.tabs.addTab(self._tab_info(), "⑥ 字段手册 / 说明")

        root = QWidget()
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(top)
        rl.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

    # ── Tab1 扫描 ──
    def _tab_scan(self):
        w = QWidget()
        v = QVBoxLayout(w)

        g = QGroupBox("扫描条件")
        f = QHBoxLayout(g)
        f.addWidget(QLabel("数值:"))
        self.ed_val = QLineEdit()
        self.ed_val.setPlaceholderText("要搜索的值")
        self.ed_val.setMaximumWidth(150)
        f.addWidget(self.ed_val)
        f.addWidget(QLabel("到:"))
        self.ed_val2 = QLineEdit()
        self.ed_val2.setPlaceholderText("范围上限")
        self.ed_val2.setMaximumWidth(110)
        f.addWidget(self.ed_val2)
        f.addWidget(QLabel("类型:"))
        self.cmb_type = QComboBox()
        self.cmb_type.addItems(list(scanner.TYPES.keys()))
        self.cmb_type.setCurrentText('Int   (4)')
        f.addWidget(self.cmb_type)
        f.addWidget(QLabel("比较:"))
        self.cmb_cmp = QComboBox()
        self.cmb_cmp.addItems(scanner.CMP)
        f.addWidget(self.cmb_cmp)
        f.addWidget(QLabel("区域:"))
        self.cmb_region = QComboBox()
        self.cmb_region.addItem("Java 堆 (推荐)", 'java')
        self.cmb_region.addItem("匿名内存 anon", 'anon')
        self.cmb_region.addItem("全部可读写", 'all')
        f.addWidget(self.cmb_region)
        f.addStretch(1)

        row = QHBoxLayout()
        self.b_first = QPushButton("首次扫描")
        self.b_first.setObjectName('accent')
        self.b_first.clicked.connect(lambda: self.do_scan('first'))
        row.addWidget(self.b_first)
        self.b_next = QPushButton("再次扫描")
        self.b_next.clicked.connect(lambda: self.do_scan('next'))
        row.addWidget(self.b_next)
        b_undo = QPushButton("撤销上一步")
        b_undo.clicked.connect(self.undo_scan)
        row.addWidget(b_undo)
        b_reset = QPushButton("重置扫描")
        b_reset.clicked.connect(self.reset_scan)
        row.addWidget(b_reset)
        self.pbar = QProgressBar()
        self.pbar.setMaximum(0)
        self.pbar.setVisible(False)
        self.pbar.setMaximumWidth(180)
        row.addWidget(self.pbar)
        self.lbl_res = QLabel("结果: 0")
        row.addWidget(self.lbl_res)
        row.addStretch(1)

        # 结果表
        self.tbl_res = QTableWidget(0, 4)
        self.tbl_res.setHorizontalHeaderLabels(["地址", "类型", "当前值", "上次值"])
        self.tbl_res.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_res.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_res.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_res.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl_res.customContextMenuRequested.connect(self._menu_res)
        self.tbl_res.doubleClicked.connect(lambda: self.add_selected())
        self.tbl_res.verticalHeader().setDefaultSectionSize(22)

        b_more = QPushButton("载入更多 1000 条")
        b_more.clicked.connect(lambda: self.fill_table(self.shown + 1000))

        v.addWidget(g)
        v.addLayout(row)
        v.addWidget(self.tbl_res, 1)
        v.addWidget(b_more)
        return w

    # ── Tab2 修改列表 ──
    def _tab_list(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.tbl_list = QTableWidget(0, 6)
        self.tbl_list.setHorizontalHeaderLabels(["✔", "描述", "地址", "类型", "数值", "锁定"])
        self.tbl_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_list.verticalHeader().setDefaultSectionSize(24)
        self.tbl_list.cellChanged.connect(self._list_edited)
        v.addWidget(self.tbl_list, 1)

        h = QHBoxLayout()
        b_del = QPushButton("删除选中")
        b_del.clicked.connect(self.del_saved)
        h.addWidget(b_del)
        b_apply = QPushButton("应用全部数值")
        b_apply.setObjectName('accent')
        b_apply.clicked.connect(self.apply_all)
        h.addWidget(b_apply)
        b_lock = QPushButton("开始锁定 (每 400ms)")
        b_lock.clicked.connect(self.toggle_lock)
        self.b_lock = b_lock
        h.addWidget(b_lock)
        b_save = QPushButton("保存列表")
        b_save.clicked.connect(self.save_list)
        h.addWidget(b_save)
        b_load = QPushButton("载入列表")
        b_load.clicked.connect(self.load_list)
        h.addWidget(b_load)
        h.addStretch(1)
        v.addLayout(h)
        return w

    # ── Tab3 深城专用 ──
    def _tab_game(self):
        w = QWidget()
        v = QVBoxLayout(w)

        g = QGroupBox("一键定位 (输入游戏里看到的当前值, 自动扫描并定位)")
        form = QFormLayout(g)
        self.game_inputs = {}
        items = [
            ("coins", "金币 cash (Long 8字节)", 'Long  (8)'),
            ("crystals", "水晶/钻石 crystals (Int)", 'Int   (4)'),
            ("level", "等级 currentLevel (Int)", 'Int   (4)'),
            ("spell", "法术点 spellPoints (Int)", 'Int   (4)'),
            ("custom", "自定义数值", 'Int   (4)'),
        ]
        for key, text, typ in items:
            row = QHBoxLayout()
            ed = QLineEdit()
            ed.setMaximumWidth(140)
            cb = QComboBox()
            cb.addItems(list(scanner.TYPES.keys()))
            cb.setCurrentText(typ)
            btn = QPushButton("定位")
            btn.clicked.connect(lambda _, k=key: self.game_locate(k))
            row.addWidget(ed)
            row.addWidget(cb)
            row.addWidget(btn)
            row.addStretch(1)
            self.game_inputs[key] = (ed, cb)
            form.addRow(text, row)
        v.addWidget(g)

        g2 = QGroupBox("定位后操作")
        h2 = QHBoxLayout(g2)
        self.ed_newval = QLineEdit()
        self.ed_newval.setPlaceholderText("新数值")
        self.ed_newval.setMaximumWidth(160)
        h2.addWidget(QLabel("设为:"))
        h2.addWidget(self.ed_newval)
        b_set = QPushButton("写入选中地址")
        b_set.setObjectName('accent')
        b_set.clicked.connect(self.game_apply)
        h2.addWidget(b_set)
        for txt, val in [("×10", 10), ("×100", 100), ("999,999", 999999),
                         ("999,999,999", 999999999), ("9,999,999,999", 9999999999)]:
            b = QPushButton(txt)
            b.clicked.connect(lambda _, x=val: self.ed_newval.setText(str(x)))
            h2.addWidget(b)
        h2.addStretch(1)
        v.addWidget(g2)

        g3 = QGroupBox("游戏控制")
        h3 = QHBoxLayout(g3)
        b_force = QPushButton("强制保存进度 (回主屏再进入)")
        b_force.clicked.connect(self.force_save)
        h3.addWidget(b_force)
        b_restart = QPushButton("重启游戏")
        b_restart.clicked.connect(self.restart_game)
        h3.addWidget(b_restart)
        b_kill = QPushButton("结束进程")
        b_kill.setObjectName('danger')
        b_kill.clicked.connect(self.kill_game)
        h3.addWidget(b_kill)
        h3.addStretch(1)
        v.addWidget(g3)

        v.addStretch(1)
        return w

    # ── Tab4 存档 ──
    def _tab_save(self):
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        b_ls = QPushButton("读取存档文件列表")
        b_ls.clicked.connect(self.list_save_files)
        h.addWidget(b_ls)
        b_bk = QPushButton("备份全部存档")
        b_bk.setObjectName('accent')
        b_bk.clicked.connect(self.backup_save)
        h.addWidget(b_bk)
        b_rs = QPushButton("恢复选中备份")
        b_rs.clicked.connect(self.restore_save)
        h.addWidget(b_rs)
        b_open = QPushButton("打开备份目录")
        b_open.clicked.connect(lambda: os.startfile(SAVE_DIR) if os.path.exists(SAVE_DIR) else None)
        h.addWidget(b_open)
        h.addStretch(1)
        v.addLayout(h)

        self.lst_backups = QListWidget()
        self.lst_backups.setMaximumHeight(150)
        v.addWidget(self.lst_backups)

        self.txt_save = QTextEdit()
        self.txt_save.setReadOnly(True)
        self.txt_save.setPlaceholderText("存档内容预览 (密文 base64)")
        v.addWidget(self.txt_save, 1)
        return w

    # ── Tab5 内存查看器 ──
    def _tab_mem(self):
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        h.addWidget(QLabel("地址(hex):"))
        self.ed_hex_addr = QLineEdit()
        self.ed_hex_addr.setMaximumWidth(180)
        h.addWidget(self.ed_hex_addr)
        h.addWidget(QLabel("长度:"))
        self.sp_len = QSpinBox()
        self.sp_len.setRange(16, 65536)
        self.sp_len.setValue(512)
        h.addWidget(self.sp_len)
        b_go = QPushButton("读取")
        b_go.setObjectName('accent')
        b_go.clicked.connect(self.hex_read)
        h.addWidget(b_go)
        b_reg = QPushButton("列出内存区域")
        b_reg.clicked.connect(self.list_regions)
        h.addWidget(b_reg)
        h.addStretch(1)
        v.addLayout(h)

        split = QSplitter(Qt.Horizontal)
        self.tree_reg = QTreeWidget()
        self.tree_reg.setHeaderLabels(["区域", "起始", "结束", "大小", "权限"])
        self.tree_reg.itemDoubleClicked.connect(self._reg_picked)
        self.tree_reg.setMaximumWidth(460)
        self.txt_hex = QTextEdit()
        self.txt_hex.setReadOnly(True)
        self.txt_hex.setFont(QFont("Consolas", 11))
        split.addWidget(self.tree_reg)
        split.addWidget(self.txt_hex)
        split.setStretchFactor(1, 3)
        v.addWidget(split, 1)
        return w

    # ── Tab6 说明 ──
    def _tab_info(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.txt_info = QTextEdit()
        self.txt_info.setReadOnly(True)
        self.txt_info.setHtml(USAGE)
        v.addWidget(self.txt_info, 1)
        h = QHBoxLayout()
        b = QPushButton("载入 SaveData 字段手册 (从 APK 解析)")
        b.clicked.connect(self.load_field_doc)
        h.addWidget(b)
        b2 = QPushButton("恢复使用说明")
        b2.clicked.connect(lambda: self.txt_info.setHtml(USAGE))
        h.addWidget(b2)
        h.addStretch(1)
        v.addLayout(h)
        return w

    # ───────────── 连接 / 进程 ─────────────
    def auto_connect(self):
        self.scan_devices(quiet=True)

    def scan_devices(self, quiet=False):
        self.cmb_dev.clear()
        found = []
        for p in MUMU_PORTS:
            try:
                r = self.adb.connect('127.0.0.1', p)
                if 'connected' in r or 'already' in r:
                    found.append('127.0.0.1:%d' % p)
            except Exception:
                pass
        for d in self.adb.devices():
            if d not in found:
                found.append(d)
        self.cmb_dev.addItems(found)
        if found:
            self.cmb_dev.setCurrentIndex(0)
            self.adb.serial = found[0]
            try:
                self.adb.root()
            except Exception:
                pass
            self.lbl_state.setText("已连接")
            self.lbl_state.setStyleSheet("color:#61d095;font-weight:bold;")
            self.refresh_procs()
        elif not quiet:
            QMessageBox.warning(self, "未找到设备", "没找到 MuMu 模拟器, 请确认模拟器已启动。")

    def refresh_procs(self):
        if not self.adb.serial:
            return
        self.cmb_proc.clear()
        procs = self.adb.processes()
        pkgs = {}
        # 只显示第三方 / 有 '.' 的进程名
        cand = [(p, n) for p, n in procs if '.' in n]
        self.proc_map = {n: p for p, n in cand}
        for p, n in sorted(cand, key=lambda x: x[1]):
            mark = "  ★" if n == PKG else ""
            self.cmb_proc.addItem("%s (pid=%d)%s" % (n, p, mark), n)
        idx = self.cmb_proc.findData(PKG)
        if idx >= 0:
            self.cmb_proc.setCurrentIndex(idx)
            self.select_proc()
        self.statusBar().showMessage("进程 %d 个" % len(cand))

    def select_proc(self):
        name = self.cmb_proc.currentData()
        if not name:
            return
        self.pkg = name
        self.pid = self.proc_map.get(name)
        if self.pid:
            self.holder = SnapshotHolder(self.adb, self.pid)
            try:
                vn, vc = self.adb.pkg_version(name)
                self.statusBar().showMessage("已选 %s  pid=%d  版本 %s (%s)" % (name, self.pid, vn, vc))
            except Exception:
                pass

    # ───────────── 扫描 ─────────────
    def do_scan(self, mode):
        if not self.pid:
            self.select_proc()
        if not self.pid:
            QMessageBox.warning(self, "未选进程", "请先选择进程。")
            return
        label = self.cmb_type.currentText()
        op = self.cmb_cmp.currentText()
        try:
            v1 = float(self.ed_val.text()) if self.ed_val.text().strip() else 0
        except ValueError:
            QMessageBox.warning(self, "数值错误", "请输入合法数字。")
            return
        try:
            v2 = float(self.ed_val2.text()) if self.ed_val2.text().strip() else v1
        except ValueError:
            v2 = v1
        if op.startswith('等于') or op.startswith('不等于') or op in ('大于 >', '小于 <', '大于等于 ≥', '小于等于 ≤'):
            if 'Float' in label or 'Double' in label:
                pass
            else:
                v1 = int(v1)
        if op.startswith('介于'):
            pass

        if mode == 'next' and self.res_addr is None:
            QMessageBox.information(self, "提示", "请先做首次扫描。")
            return

        if mode == 'next':
            self.scan_history.append((self.res_addr, self.res_val, self.prev_val))
        self._old_vals = self.res_val      # 用于表格"上次值"列
        self._mode = mode

        self.pbar.setVisible(True)
        self.b_first.setEnabled(False)
        self.b_next.setEnabled(False)
        self.thread = ScanThread(self, mode, self.holder, label, op, v1, v2,
                                 self.res_addr, self.res_val, self.cmb_region.currentData())
        self.thread.sig_msg.connect(self.statusBar().showMessage)
        self.thread.sig_done.connect(self.scan_done)
        self.thread.start()

    def scan_done(self, a, v):
        self.pbar.setVisible(False)
        self.b_first.setEnabled(True)
        self.b_next.setEnabled(True)
        if a is None:
            return
        self.prev_val = self._old_vals if self._mode == 'next' else None
        self.res_addr, self.res_val = a, v
        self.shown = 0
        self.fill_table(1000)
        self.lbl_res.setText("结果: %d" % len(a))
        if len(a) > 0 and len(a) <= 20:
            self.statusBar().showMessage("只剩 %d 个候选, 可以加入修改列表了" % len(a))

    def fill_table(self, n):
        if self.res_addr is None:
            return
        n = min(n, len(self.res_addr))
        self.tbl_res.setRowCount(0)
        self.tbl_res.setRowCount(n)
        label = self.cmb_type.currentText()
        addrs = self.res_addr[:n]
        vals = self.res_val[:n]
        for i in range(self.shown, n):
            self.tbl_res.setItem(i, 0, QTableWidgetItem(fmt_addr(addrs[i])))
            self.tbl_res.setItem(i, 1, QTableWidgetItem(label.split('(')[0].strip()))
            it = QTableWidgetItem(fmt_val(vals[i], label))
            if self.prev_val is not None and i < len(self.prev_val):
                it.setForeground(QColor('#61d095') if vals[i] != self.prev_val[i] else QColor('#d6d9e0'))
            self.tbl_res.setItem(i, 2, it)
            pv = self.prev_val[i] if (self.prev_val is not None and i < len(self.prev_val)) else None
            self.tbl_res.setItem(i, 3, QTableWidgetItem(fmt_val(pv, label)))
        self.shown = n

    def undo_scan(self):
        if not self.scan_history:
            return
        self.res_addr, self.res_val, self.prev_val = self.scan_history.pop()
        self.shown = 0
        self.fill_table(1000)
        self.lbl_res.setText("结果: %s" % (len(self.res_addr) if self.res_addr is not None else 0))

    def reset_scan(self):
        self.res_addr = self.res_val = self.prev_val = None
        self.scan_history = []
        self.tbl_res.setRowCount(0)
        self.lbl_res.setText("结果: 0")
        self.statusBar().showMessage("已重置")

    # ───────────── 修改列表 ─────────────
    def _menu_res(self, pos):
        m = QMenu(self)
        m.addAction(QAction("加入修改列表", self, triggered=lambda: self.add_selected()))
        m.addAction(QAction("复制地址", self, triggered=self.copy_addr))
        m.addAction(QAction("在查看器中打开", self, triggered=self.open_in_hex))
        m.exec_(self.tbl_res.viewport().mapToGlobal(pos))

    def add_selected(self):
        rows = set(i.row() for i in self.tbl_res.selectedIndexes())
        if not rows or self.res_addr is None:
            return
        label = self.cmb_type.currentText()
        for r in sorted(rows):
            a = int(self.res_addr[r])
            self.saved.append({"desc": "addr_%08X" % (a & 0xffffffff), "addr": a,
                               "label": label, "value": fmt_val(self.res_val[r], label),
                               "lock": False, "on": True})
        self.refresh_list()
        self.tabs.setCurrentIndex(1)

    def copy_addr(self):
        r = self.tbl_res.currentRow()
        if r >= 0 and self.res_addr is not None:
            QApplication.clipboard().setText(fmt_addr(self.res_addr[r]))

    def open_in_hex(self):
        r = self.tbl_res.currentRow()
        if r >= 0 and self.res_addr is not None:
            self.ed_hex_addr.setText("%X" % int(self.res_addr[r]))
            self.tabs.setCurrentIndex(4)
            self.hex_read()

    def refresh_list(self):
        self.tbl_list.blockSignals(True)
        self.tbl_list.setRowCount(len(self.saved))
        for i, s in enumerate(self.saved):
            ck = QTableWidgetItem()
            ck.setCheckState(Qt.Checked if s.get('on', True) else Qt.Unchecked)
            self.tbl_list.setItem(i, 0, ck)
            self.tbl_list.setItem(i, 1, QTableWidgetItem(s['desc']))
            self.tbl_list.setItem(i, 2, QTableWidgetItem(fmt_addr(s['addr'])))
            self.tbl_list.setItem(i, 3, QTableWidgetItem(s['label']))
            self.tbl_list.setItem(i, 4, QTableWidgetItem(str(s['value'])))
            lk = QTableWidgetItem()
            lk.setCheckState(Qt.Checked if s.get('lock') else Qt.Unchecked)
            self.tbl_list.setItem(i, 5, lk)
        self.tbl_list.blockSignals(False)

    def _list_edited(self, r, c):
        if r >= len(self.saved):
            return
        try:
            txt = self.tbl_list.item(r, c).text()
        except Exception:
            return
        if c == 1:
            self.saved[r]['desc'] = txt
        elif c == 4:
            self.saved[r]['value'] = txt
        elif c == 0:
            self.saved[r]['on'] = self.tbl_list.item(r, c).checkState() == Qt.Checked
        elif c == 5:
            self.saved[r]['lock'] = self.tbl_list.item(r, c).checkState() == Qt.Checked

    def del_saved(self):
        rows = sorted(set(i.row() for i in self.tbl_list.selectedIndexes()), reverse=True)
        for r in rows:
            if r < len(self.saved):
                self.saved.pop(r)
        self.refresh_list()

    def _write_one(self, s):
        if not self.pid:
            return False
        label = s['label']
        try:
            if 'Float' in label:
                raw = struct.pack('<f', float(s['value']))
            elif 'Double' in label:
                raw = struct.pack('<d', float(s['value']))
            else:
                sz = scanner.size_of(label)
                v = int(float(s['value']))
                raw = v.to_bytes(sz, 'little', signed='Long' in label or 'Int' in label or 'Short' in label)
            return self.adb.write_mem(self.pid, s['addr'], raw)
        except Exception as e:
            self.statusBar().showMessage("写入失败: %s" % e)
            return False

    def apply_all(self):
        if not self.pid:
            return
        ok = 0
        for s in self.saved:
            if s.get('on', True) and self._write_one(s):
                ok += 1
        self.statusBar().showMessage("已写入 %d/%d 项" % (ok, len(self.saved)))

    def toggle_lock(self):
        if self.lock_timer.isActive():
            self.lock_timer.stop()
            self.b_lock.setText("开始锁定 (每 400ms)")
            self.statusBar().showMessage("锁定已停止")
        else:
            if not self.saved:
                return
            self.lock_timer.start(400)
            self.b_lock.setText("■ 停止锁定")
            self.statusBar().showMessage("锁定中…")

    def _do_lock(self):
        for s in self.saved:
            if s.get('lock') and s.get('on', True):
                self._write_one(s)

    def save_list(self):
        p, _ = QFileDialog.getSaveFileName(self, "保存修改列表", "deeptown_list.json", "JSON (*.json)")
        if not p:
            return
        try:
            json.dump(self.saved, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        except Exception as e:
            QMessageBox.warning(self, "失败", str(e))

    def load_list(self):
        p, _ = QFileDialog.getOpenFileName(self, "载入修改列表", "", "JSON (*.json)")
        if not p:
            return
        try:
            self.saved = json.load(open(p, 'r', encoding='utf-8'))
            self.refresh_list()
        except Exception as e:
            QMessageBox.warning(self, "失败", str(e))

    # ───────────── 深城专用 ─────────────
    def game_locate(self, key):
        ed, cb = self.game_inputs[key]
        txt = ed.text().strip()
        if not txt:
            QMessageBox.information(self, "提示", "先在游戏里看一眼当前数值, 填进来再点定位。")
            return
        self.ed_val.setText(txt)
        self.cmb_type.setCurrentText(cb.currentText())
        self.cmb_cmp.setCurrentText('等于 =')
        self.cmb_region.setCurrentIndex(0)
        self.res_addr = None
        self.res_val = None
        self.prev_val = None
        self.scan_history = []
        self.do_scan('first')
        self.tabs.setCurrentIndex(0)
        self.statusBar().showMessage("定位完成, 回游戏改一下数值后点『再次扫描』可进一步收敛")

    def game_apply(self):
        rows = sorted(set(i.row() for i in self.tbl_res.selectedIndexes()))
        if not rows or self.res_addr is None:
            QMessageBox.information(self, "提示", "请先在『内存扫描』里选中要改的地址。")
            return
        nv = self.ed_newval.text().strip()
        if not nv:
            return
        label = self.cmb_type.currentText()
        ok = 0
        for r in rows:
            a = int(self.res_addr[r])
            self.saved.append({"desc": "deeptown", "addr": a, "label": label, "value": nv, "lock": False})
            if self._write_one(self.saved[-1]):
                ok += 1
        self.refresh_list()
        self.statusBar().showMessage("写入 %d 个地址" % ok)

    def force_save(self):
        """回主屏再进入, 触发 onPause/onResume 让游戏把内存写回存档"""
        if not self.pid:
            return
        self.adb.shell('input keyevent 3')
        time.sleep(1.0)
        act = self.adb.shell('cmd package resolve-activity --brief %s' % self.pkg).strip().split('\n')[-1]
        if act and '/' in act:
            self.adb.shell('am start -n %s' % act)
        self.statusBar().showMessage("已触发保存")

    def restart_game(self):
        if not self.pkg:
            return
        self.adb.shell('am force-stop %s' % self.pkg)
        time.sleep(0.6)
        act = self.adb.shell('cmd package resolve-activity --brief %s' % self.pkg).strip().split('\n')[-1]
        if act and '/' in act:
            self.adb.shell('am start -n %s' % act)
        self.statusBar().showMessage("已重启游戏")
        QTimer.singleShot(2500, self.refresh_procs)

    def kill_game(self):
        if self.pkg:
            self.adb.shell('am force-stop %s' % self.pkg)
            self.statusBar().showMessage("已结束进程")

    # ───────────── 存档 ─────────────
    def list_save_files(self):
        if not self.pkg:
            return
        out = self.adb.shell('ls -la /data/user/0/%s/shared_prefs/ 2>&1; echo ---; ls -la /data/user/0/%s/files/ 2>&1'
                             % (self.pkg, self.pkg))
        self.txt_save.setText(out)
        self.refresh_backups()

    def refresh_backups(self):
        self.lst_backups.clear()
        if os.path.isdir(SAVE_DIR):
            for d in sorted(os.listdir(SAVE_DIR), reverse=True):
                self.lst_backups.addItem(d)

    def backup_save(self):
        if not self.pkg:
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(SAVE_DIR, ts)
        os.makedirs(dst, exist_ok=True)
        r = subprocess_pull(self.adb, '/data/user/0/%s/shared_prefs' % self.pkg,
                            os.path.join(dst, 'shared_prefs'))
        r += subprocess_pull(self.adb, '/data/user/0/%s/files' % self.pkg,
                             os.path.join(dst, 'files'))
        self.txt_save.setText(r)
        self.refresh_backups()
        self.statusBar().showMessage("备份到 %s" % dst)

    def restore_save(self):
        it = self.lst_backups.currentItem()
        if not it:
            return
        src = os.path.join(SAVE_DIR, it.text())
        # 目录里可能是 shared_prefs 子目录
        base = src if os.path.isdir(os.path.join(src, 'shared_prefs')) else src
        self.adb.shell('am force-stop %s' % self.pkg)
        r = subprocess_push(self.adb, os.path.join(src, 'shared_prefs'),
                            '/data/user/0/%s/shared_prefs' % self.pkg)
        self.adb.shell('chmod -R 660 /data/user/0/%s/shared_prefs; chown -R $(stat -c %%U /data/user/0/%s).$(stat -c %%G /data/user/0/%s) /data/user/0/%s/shared_prefs'
                       % (self.pkg, self.pkg, self.pkg, self.pkg))
        self.txt_save.setText(r)
        self.statusBar().showMessage("已恢复 %s, 重启游戏生效" % it.text())

    # ───────────── 内存查看器 ─────────────
    def hex_read(self):
        if not self.pid:
            return
        try:
            a = int(self.ed_hex_addr.text().strip() or '0', 16)
        except ValueError:
            return
        n = self.sp_len.value()
        data = self.adb.read_mem(self.pid, a, n)
        lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            hexs = ' '.join('%02X' % b for b in chunk)
            asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            lines.append('%016X  %-47s  %s' % (a + i, hexs, asc))
        self.txt_hex.setText('\n'.join(lines) or '(读不到)')

    def list_regions(self):
        if not self.pid:
            return
        self.tree_reg.clear()
        for r in self.adb.maps(self.pid):
            it = QTreeWidgetItem([scanner.region_label(r), "%016X" % r['start'], "%016X" % r['end'],
                                  "%.1f MB" % ((r['end'] - r['start']) / 1048576.0), r['prot']])
            it.setData(0, Qt.UserRole, r['start'])
            if 'w' in r['prot'] and (r['end'] - r['start']) > 1048576:
                it.setForeground(0, QColor('#61d095'))
            self.tree_reg.addTopLevelItem(it)
        self.statusBar().showMessage("共 %d 个区域" % self.tree_reg.topLevelItemCount())

    def _reg_picked(self, item, col):
        self.ed_hex_addr.setText("%X" % int(item.data(0, Qt.UserRole)))
        self.hex_read()

    # ───────────── 字段手册 ─────────────
    def load_field_doc(self):
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'work'))
            import dexparse
            apk = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'work', 'deeptown.apk')
            out = []
            if os.path.exists(apk):
                for d in dexparse.load_apk(apk):
                    for c, cd in d.class_defs().items():
                        if c == 'Lcom/underwater/demolisher/data/vo/SaveData;':
                            sf, if_ = d.class_fields(cd)
                            out.append("SaveData 实例字段 (%d):\n" % len(if_))
                            for x in if_:
                                out.append("  %-46s %s" % (x[1], x[2]))
            else:
                out.append("未找到 work/deeptown.apk, 无法解析。")
            self.txt_info.setText('\n'.join(out))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.txt_info.setText("解析失败: %s" % e)

    def closeEvent(self, e):
        self.lock_timer.stop()
        super().closeEvent(e)


def subprocess_pull(adb, remote, local):
    import subprocess
    base = [adb.adb]
    if adb.serial:
        base += ['-s', adb.serial]
    r = subprocess.run(base + ['pull', remote, local], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
    return r.stdout


def subprocess_push(adb, local, remote):
    import subprocess
    base = [adb.adb]
    if adb.serial:
        base += ['-s', adb.serial]
    r = subprocess.run(base + ['push', local, remote], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
    return r.stdout


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
