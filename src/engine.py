# -*- coding: utf-8 -*-
"""
深城修改引擎  ——  已通过实机验证

核心发现(逆向自 classes5.dex):
  游戏用混淆类 i4/b {long b, long c, Object d} 与 i4/a {int b, int c, Object d}
  保存货币, 真实值 = c - b, 其中 b 每次读取时被重新随机成 random(-50000, 50000):
      f(): c = c - b;  if (c<0) c = 0        # 归一化出真实值
      g(): b = rand(-50000,50000); c = c + b # 再叠加随机偏移
      h(): f(); g(); return c - b            # 对外返回的就是真实值
  所以金币/钻石在内存里既不是 b 也不是 c, 而是 c-b。
  ==> 修改方法: 把 b 写成 0, c 写成目标值, 游戏下次读到的就是目标值。

2026-10-08 性能重写:
  旧版 strict_filter 对每个候选调一次 klass_of -> 每次一个 adb 子进程往返。
  一个普通数值(如 12345)有 2 万个候选 -> 每个候选 4 次结构匹配 -> 几万次 adb -> 72 秒。
  现在改成从内存快照里 numpy 批量取类指针, 0 次 adb 往返, 同样场景 < 0.2 秒。
  同时全程支持协作式取消(stop 回调), 任何阶段都能随时中止。
"""
import struct
import time
import bisect

import numpy as np

import scanner
from adbmem import MemSnapshot

RAND_RANGE = 50000        # g() 里 random(-50000, 50000)
B_ABS_MAX = 200000        # 放宽一点, 容忍随机期间读到的中间态
MAX_CAND = 400000         # 单次 find 的候选上限(超过就截断, 防爆内存)


class Cancelled(Exception):
    """用户主动中止"""
    pass


def _ck(stop):
    """协作式取消检查"""
    if stop is not None and stop():
        raise Cancelled()


def parse_amount(text):
    """把 '1.5M' / '1833M' / '9999' 解析成 (lo, hi)"""
    t = text.strip().upper().replace(',', '').replace('，', '').replace(' ', '')
    mult = 1
    if t.endswith('K'):
        mult, t = 1e3, t[:-1]
    elif t.endswith('M'):
        mult, t = 1e6, t[:-1]
    elif t.endswith('B'):
        mult, t = 1e9, t[:-1]
    elif t.endswith('W'):   # 万
        mult, t = 1e4, t[:-1]
    try:
        v = float(t) * mult
    except ValueError:
        return None
    v = int(v)
    # 显示值通常被截断(1833M 实际是 1833.95M), 给一个区间
    span = max(int(mult) - 1, 1000)
    return max(0, v - 0), v + span


class Locator:
    def __init__(self, adb, pid):
        self.adb = adb
        self.pid = pid
        self.snapshot = None
        self.regions = None
        self._starts = None

    # ---------- 快照 ----------
    def take_snapshot(self, mode='java', log=None):
        if log:
            log('   读取内存映射…')
        regions = scanner.filter_regions(self.adb.maps(self.pid), mode)
        regions.sort(key=lambda r: (r['end'] - r['start']), reverse=True)
        self.regions = regions
        if log:
            log('   抓取 %d 个内存区…' % len(regions))
        self.snapshot = MemSnapshot(self.adb, self.pid, regions)
        self._starts = np.array([r['start'] for r in self.snapshot.regions], dtype=np.int64)
        return self.snapshot

    def refresh(self):
        if self.snapshot:
            self.snapshot.refresh()

    # ---------- 快照内随机读取(不走 adb) ----------
    def snap_read(self, addr, size=4):
        snap = self.snapshot
        if snap is None or self._starts is None:
            return None
        i = int(bisect.bisect_right(self._starts, addr) - 1)
        if i < 0 or i >= len(snap.regions):
            return None
        r = snap.regions[i]
        b = snap.blobs[i]
        if not b:
            return None
        off = addr - r['start']
        if off < 0 or off + size > len(b):
            return None
        return bytes(b[off:off + size])

    def klass_batch(self, addrs):
        """
        批量读对象头 4 字节(压缩引用) —— 全部从快照里 numpy 取, 不碰 adb。
        addrs: np.int64 数组
        """
        snap = self.snapshot
        out = np.zeros(len(addrs), dtype=np.int64)
        if snap is None or self._starts is None or len(addrs) == 0:
            return out
        order = np.argsort(addrs)
        sa = addrs[order]
        idx = np.searchsorted(self._starts, sa, side='right') - 1
        for i in np.unique(idx):
            if i < 0 or i >= len(snap.regions):
                continue
            r = snap.regions[i]
            b = snap.blobs[i]
            if not b:
                continue
            sel = idx == i
            a = sa[sel]
            off = a - r['start']
            ok = (off >= 0) & (off + 4 <= len(b))
            if not ok.any():
                continue
            a2 = a[ok]
            off2 = (a2 - r['start']).astype(np.int64)
            u8 = np.frombuffer(b, dtype=np.uint8)
            k = (u8[off2].astype(np.int64)
                 | (u8[off2 + 1].astype(np.int64) << 8)
                 | (u8[off2 + 2].astype(np.int64) << 16)
                 | (u8[off2 + 3].astype(np.int64) << 24))
            out[np.flatnonzero(sel)[ok]] = k
        return out

    # ---------- 定位 ----------
    def find(self, lo, hi, structures=('i4b', 'i4a', 'long', 'int'), stop=None, log=None):
        """
        返回候选列表: [{'struct','addr','b','c','value'}, ...]
        addr 指向 b 字段的地址 (对 i4 结构), 普通类型时指向值本身
        """
        if self.snapshot is None:
            self.take_snapshot()
        out = []
        nreg = len(self.snapshot.regions)
        for ri, r in enumerate(self.snapshot.regions):
            _ck(stop)
            if log and (ri % 200 == 0):
                log('   扫描 %d/%d 区…已命中 %d' % (ri, nreg, len(out)))
            blob = self.snapshot.blobs[ri]
            base = r['start']
            if not blob:
                continue
            if 'i4b' in structures:
                n = len(blob) // 8
                if n >= 3:
                    a64 = np.frombuffer(blob[:n * 8], dtype=np.int64)
                    b, c = a64[:-1], a64[1:]
                    d = c - b
                    m = (np.abs(b) <= B_ABS_MAX) & (d >= lo) & (d <= hi)
                    for i in np.flatnonzero(m):
                        out.append({'struct': 'i4b', 'addr': base + int(i) * 8,
                                    'b': int(b[i]), 'c': int(c[i]), 'value': int(d[i])})
            if 'i4a' in structures:
                n = len(blob) // 4
                if n >= 3:
                    a32 = np.frombuffer(blob[:n * 4], dtype=np.int32).astype(np.int64)
                    b, c = a32[:-1], a32[1:]
                    d = c - b
                    m = (np.abs(b) <= B_ABS_MAX) & (d >= lo) & (d <= hi)
                    for i in np.flatnonzero(m):
                        out.append({'struct': 'i4a', 'addr': base + int(i) * 4,
                                    'b': int(b[i]), 'c': int(c[i]), 'value': int(d[i])})
            if 'long' in structures:
                n = len(blob) // 8
                if n >= 1:
                    a64 = np.frombuffer(blob[:n * 8], dtype=np.int64)
                    m = (a64 >= lo) & (a64 <= hi)
                    for i in np.flatnonzero(m):
                        out.append({'struct': 'long', 'addr': base + int(i) * 8,
                                    'b': None, 'c': int(a64[i]), 'value': int(a64[i])})
            if 'int' in structures:
                n = len(blob) // 4
                if n >= 1:
                    a32 = np.frombuffer(blob[:n * 4], dtype=np.int32).astype(np.int64)
                    m = (a32 >= lo) & (a32 <= hi) & (a32 >= 0)
                    for i in np.flatnonzero(m):
                        out.append({'struct': 'int', 'addr': base + int(i) * 4,
                                    'b': None, 'c': int(a32[i]), 'value': int(a32[i])})
            if len(out) > MAX_CAND:
                if log:
                    log('   ⚠ 候选超过 %d，已截断（这个数值太常见，建议换个值或用手动两步）' % MAX_CAND)
                break
        # 去重(同一地址被多种结构命中)
        seen = {}
        for h in out:
            k = (h['addr'], h['struct'])
            if k not in seen:
                seen[k] = h
        return list(seen.values())

    # ---------- 写入 ----------
    def _wb64(self, addr, val):
        import base64
        raw = base64.b64encode(struct.pack('<q', int(val))).decode()
        self.adb.shell('echo %s | base64 -d | dd of=/proc/%d/mem bs=1 seek=%d conv=notrunc 2>/dev/null'
                       % (raw, self.pid, int(addr)))

    def _wb32(self, addr, val):
        import base64
        raw = base64.b64encode(struct.pack('<i', int(val))).decode()
        self.adb.shell('echo %s | base64 -d | dd of=/proc/%d/mem bs=1 seek=%d conv=notrunc 2>/dev/null'
                       % (raw, self.pid, int(addr)))

    def apply(self, cand, new_value):
        """按结构写入目标值, 返回 (ok, 说明)"""
        try:
            if cand['struct'] == 'i4b':
                # b 在 addr, c 在 addr+8
                self._wb64(cand['addr'], 0)
                self._wb64(cand['addr'] + 8, new_value)
            elif cand['struct'] == 'i4a':
                self._wb32(cand['addr'], 0)
                self._wb32(cand['addr'] + 4, new_value)
            elif cand['struct'] == 'long':
                self._wb64(cand['addr'], new_value)
            else:
                self._wb32(cand['addr'], new_value)
            return True, ''
        except Exception as e:
            return False, str(e)

    def read_cand(self, cand):
        """读回候选对象的当前真实值"""
        try:
            if cand['struct'] == 'i4b':
                b = struct.unpack('<q', self.adb.read_mem(self.pid, cand['addr'], 8))[0]
                c = struct.unpack('<q', self.adb.read_mem(self.pid, cand['addr'] + 8, 8))[0]
            elif cand['struct'] == 'i4a':
                b = struct.unpack('<i', self.adb.read_mem(self.pid, cand['addr'], 4))[0]
                c = struct.unpack('<i', self.adb.read_mem(self.pid, cand['addr'] + 4, 4))[0]
            elif cand['struct'] == 'long':
                return struct.unpack('<q', self.adb.read_mem(self.pid, cand['addr'], 8))[0]
            else:
                return struct.unpack('<i', self.adb.read_mem(self.pid, cand['addr'], 4))[0]
            return c - b
        except Exception:
            return None

    def klass_of(self, cand):
        """读对象头里的类指针 (compressed ref)。优先走快照(零 adb 往返)。"""
        try:
            if cand['struct'] == 'i4b':
                off = cand['addr'] - 16          # {ref@8, b@16, c@24}
            elif cand['struct'] == 'i4a':
                off = cand['addr'] - 12          # {ref@8, b@12, c@16}
            else:
                return 0
            raw = self.snap_read(off, 4)
            if raw is None or len(raw) < 4:
                raw = self.adb.read_mem(self.pid, off, 4)
            if len(raw) < 4:
                return 0
            return struct.unpack('<I', raw)[0]
        except Exception:
            return 0


def strict_filter(loc, pool, stop=None):
    """
    三重特征筛选真货币/资源对象:
      1) 对象头的类指针有效(指向堆内) —— 巧合整数对不会有
      2) b 是 g() 生成的随机数, 落在 ±50000 内
      3) b 不为 0(排除标志位对象)
    批量取类指针, 不再每个候选一次 adb 往返。
    """
    if not pool:
        return []
    _ck(stop)
    addrs = np.array([c['addr'] - (16 if c['struct'] == 'i4b' else 12)
                      for c in pool], dtype=np.int64)
    ks = loc.klass_batch(addrs)
    out = []
    for c, k in zip(pool, ks):
        c['klass'] = int(k)
        if 0x10000000 <= k <= 0x60000000 and c['b'] not in (0, None) and abs(c['b']) <= 50000:
            out.append(c)
    return out


def pick(loc, lo, hi, structures=None, stop=None, log=None):
    """
    返回 (最佳候选列表, 全部候选)
    structures 可指定只搜某种结构(如资源只搜 i4a), 能大幅减少候选。
    """
    if structures is None:
        structures = ('i4b', 'i4a', 'long', 'int')
    cands = _dedupe_overlap(loc.find(lo, hi, structures, stop=stop, log=log))
    if log:
        log('   粗筛命中 %d 个' % len(cands))
    _ck(stop)
    i4b = [c for c in cands if c['struct'] == 'i4b']
    i4a = [c for c in cands if c['struct'] == 'i4a']
    if 'i4b' in structures:
        best = strict_filter(loc, i4b, stop)
        if best:
            return best, cands
    if 'i4a' in structures:
        best = strict_filter(loc, i4a, stop)
        if best:
            return best, cands
    return (i4b or i4a), cands


def _dedupe_overlap(cands):
    """滑动窗口会把同一对象匹配多次(addr 与 addr+8), 这里去掉重叠项"""
    cands = sorted(cands, key=lambda c: (c['struct'], c['addr']))
    out = []
    for c in cands:
        if out and c['struct'] == out[-1]['struct'] and abs(c['addr'] - out[-1]['addr']) < 16:
            continue
        out.append(c)
    return out


def smart_modify(loc, current_text, want_text, log=print, stop=None):
    """傻瓜式一键修改(手动模式)"""
    rng = parse_amount(current_text)
    want = parse_amount(want_text)
    if not rng or not want:
        return 0, 0, '数值格式不正确'
    lo, hi = rng
    target = want[0]
    log('① 抓取内存快照…')
    loc.take_snapshot(log=log)
    log('② 按 4 种结构定位 %s …' % current_text)
    cands = _dedupe_overlap(loc.find(lo, hi, stop=stop))
    i4b = [c for c in cands if c['struct'] == 'i4b']       # 货币(金币)就是 i4b
    i4a = [c for c in cands if c['struct'] == 'i4a']
    plain = [c for c in cands if not c['struct'].startswith('i4')]
    log('   命中 %d 个(i4b %d / i4a %d / 普通 %d)' % (len(cands), len(i4b), len(i4a), len(plain)))

    use = strict_filter(loc, i4b, stop)
    if use:
        log('   严格特征命中 %d 个(类指针有效 + 随机偏移)' % len(use))
    else:
        use = strict_filter(loc, i4a, stop)
        if use:
            log('   严格特征命中 %d 个(i4a 结构)' % len(use))
        else:
            use = i4b or i4a or [c for c in plain if c['struct'] == 'long'] or plain
            log('   回退到宽松匹配 %d 个' % len(use))

    if not use:
        return 0, 0, '没找到匹配项。请确认游戏里显示的就是这个数值(可换 M/K 单位再试)'
    if len(use) > 12:
        return -1, len(use), 'CONVERGE'

    log('③ 写入目标值 %d 到 %d 个地址…' % (target, len(use)))
    for c in use:
        loc.apply(c, target)
    time.sleep(0.6)
    good = 0
    for c in use:
        v = loc.read_cand(c)
        if v is not None and abs(v - target) <= max(2000, target // 50000):
            good += 1
    log('④ 回读校验: %d/%d 通过' % (good, len(use)))
    if good:
        return good, len(use), '写入成功！回游戏看数值是否变化。变对了就点「强制保存」写进存档。'
    return 0, len(use), '写入未通过校验：该数值可能不是这个结构，或游戏正处于暂停状态，请回游戏点一下再重试'
