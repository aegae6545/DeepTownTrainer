# -*- coding: utf-8 -*-
"""
技能冷却(spell cooldown)修改  ——  v2 (2026-10-08 重写)

SpellData 真实内存布局 (实测 深城 5.7.8 / com.rockbite.deeptown, classes*.dex 未混淆类
com.underwater.demolisher.data.vo.spell.SpellData):

    -8  : String ref     -> 法术名字符串, 如 "fire-cannon"      ★定位钥匙
    -4  : ref
    +0  : int   cost
    +4  : int   unlock_level
    +8  : int   unlock_level (同值副本)
    +12 : float cooldown            <-- 本工具要改的字段
    +16 : int 0
    +20 : ref ...

定位方法:
  1) 在 Java 堆里搜法术名(ASCII) -> 得到 name String 对象头 (chars - 16)
  2) 扫内存找"哪个字段的值 == 该 String 对象头" -> 那个字段就是 SpellData 的 -8
  3) 该字段 +8 = cost 地址, +12 = cooldown 地址
  4) 用 (cost, unlock, unlock, cooldown) 四元组校验, 排除同名误报

【旧版为什么失效】
  旧版假设布局为 "[cooldown float][cost int][+8=堆引用]" —— 顺序和偏移都不对。
  实测 15 个法术里 11 个 hits=0, 另 4 个是浮点值巧合的误报; 且写入后不回读校验,
  所以 UI 显示"已修改"但游戏里毫无变化。

注意: 只有已解锁的法术, 其 SpellData 才会载入内存。
"""
import os, json, struct, base64, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
HEAP_MIN = 0x10000000          # 堆区地址下限(用于排除 dex 常量区的同名串)
LO, HI = 0x12000000, 0x54000000


def load_spells():
    p = os.path.join(HERE, 'spells.json')
    return json.load(open(p, encoding='utf-8'))


# ---------------------------------------------------------------- 定位

def _as_bytes(snap):
    """MemSnapshot 的 blobs 是 memoryview, 统一转成 bytes(只转一次)"""
    return [(r['start'], b if isinstance(b, bytes) else bytes(b))
            for r, b in zip(snap.regions, snap.blobs) if b]


def _views(blobs):
    """把快照切成 (base, i32, f32) 三元组, 不复制数据"""
    out = []
    for base, b in blobs:
        n = len(b) // 4
        if n < 8:
            continue
        out.append((base,
                    np.frombuffer(b[:n * 4], dtype=np.int32),
                    np.frombuffer(b[:n * 4], dtype=np.float32)))
    return out


def _name_objs(blobs, name):
    """返回该法术名在堆里的 String 对象头地址集合"""
    objs = set()
    needle = name.encode('ascii')
    for base, b in blobs:
        p = b.find(needle)
        while p >= 0:
            c = base + p
            if c >= HEAP_MIN:
                objs.add(c - 16)
            p = b.find(needle, p + 1)
    return objs


def scan_cooldowns(loc, log=print, stop=None):
    """
    返回 [{'name','cd','hits','addr','cands'}]
    addr 为 cost 字段地址, cooldown 字段在 addr + 12
    stop: 可选回调, 返回 True 时中止
    """
    def ck():
        if stop is not None and stop():
            raise SPM_Cancelled()
    spells = load_spells()
    snap = loc.take_snapshot(log=log)
    blobs = _as_bytes(snap)
    views = _views(blobs)

    # 1) 收集全部法术名的 String 对象头
    obj2name = {}
    for sp in spells:
        for o in _name_objs(blobs, sp['name']):
            obj2name[o] = sp['name']
    if not obj2name:
        return [{'name': sp['name'], 'cd': float(sp['cd']), 'hits': 0,
                 'addr': None, 'cands': []} for sp in spells]

    objs = np.array(sorted(obj2name), dtype=np.int32)

    # 2) 一遍扫描, 找所有引用这些 String 的字段
    raw_hits = {sp['name']: [] for sp in spells}
    for base, i32, f32 in views:
        ck()
        m = np.isin(i32, objs)
        if not m.any():
            continue
        for i in np.flatnonzero(m):
            i = int(i)
            ca = i + 2                       # cost 字段下标 (i32[i] 是 -8)
            if ca + 4 > i32.size:
                continue
            nm = obj2name.get(int(i32[i]))
            if nm is None:
                continue
            raw_hits[nm].append(base + ca * 4)

    # 3) 用 (cost, unlock, unlock, cd) 校验 —— 从快照里读, 零 adb 往返
    out = []
    for sp in spells:
        ck()
        nm, cd, cost, unl = sp['name'], float(sp['cd']), sp['cost'], sp.get('unlock', 0)
        good = []
        for a in raw_hits[nm]:
            vals = _read4_snap(loc, a)
            if vals is None:
                continue
            c, u1, u2, f = vals
            if c == cost and u1 == unl and u2 == unl and abs(f - cd) < 1e-3:
                good.append(a)
        # 若严格校验全失败, 退化为"结构合理"(cd 在合法区间)的候选
        if not good:
            for a in raw_hits[nm]:
                vals = _read4_snap(loc, a)
                if vals is None:
                    continue
                c, u1, u2, f = vals
                if 0.0 < f <= 3600.0 and 0 <= c <= 10000 and 0 <= u1 <= 10000:
                    good.append(a)
        out.append({'name': nm, 'cd': cd, 'hits': len(good),
                    'addr': good[0] if good else None, 'cands': good})
    return out


class SPM_Cancelled(Exception):
    pass


def _read4_snap(loc, cost_addr):
    """从内存快照里读 SpellData 的 (cost, unlock, unlock, cooldown) —— 不走 adb"""
    try:
        raw = loc.snap_read(cost_addr, 16)
        if raw is None or len(raw) < 16:
            return _read4(loc, cost_addr)
        c, u1, u2 = struct.unpack_from('<iii', raw, 0)
        f = struct.unpack_from('<f', raw, 12)[0]
        return c, u1, u2, f
    except Exception:
        return None


def _read4(loc, cost_addr):
    """读 SpellData 的 (cost, unlock, unlock, cooldown) 四元组"""
    try:
        raw = loc.adb.read_mem(loc.pid, cost_addr, 16)
        if len(raw) < 16:
            return None
        c, u1, u2 = struct.unpack_from('<iii', raw, 0)
        f = struct.unpack_from('<f', raw, 12)[0]
        return c, u1, u2, f
    except Exception:
        return None


def _write_f32(loc, addr, val):
    raw = base64.b64encode(struct.pack('<f', float(val))).decode()
    loc.adb.shell('echo %s | base64 -d | dd of=/proc/%d/mem bs=1 seek=%d conv=notrunc 2>/dev/null'
                  % (raw, loc.pid, int(addr)))


def _read_f32(loc, addr):
    try:
        raw = loc.adb.read_mem(loc.pid, addr, 4)
        if len(raw) < 4:
            return None
        return struct.unpack('<f', raw)[0]
    except Exception:
        return None


# ---------------------------------------------------------------- 写入

def zero_cooldowns(loc, target=0.1, only_tight=True, log=print, prescan=None, stop=None):
    """
    把所有已解锁法术的冷却改成 target 秒。
    prescan: 复用 scan_cooldowns() 的结果, 避免重复扫描(约 10s)
    返回 (changed, skipped)
      changed = [(name, 原cd, 命中数, 校验是否通过)]
      skipped = [(name, 原因)]
    """
    def ck():
        if stop is not None and stop():
            raise SPM_Cancelled()
    res = prescan if prescan is not None else scan_cooldowns(loc, log, stop=stop)
    changed, skipped = [], []
    wrote = []                      # (name, cd_addr)
    for r in res:
        ck()
        if not r['cands']:
            skipped.append((r['name'], '未找到(可能未解锁)'))
            continue
        if only_tight and r['hits'] > 4:
            skipped.append((r['name'], '候选过多(%d)' % r['hits']))
            continue
        # 写入前复核地址仍指向同一个 SpellData(防 GC 移动)
        live = []
        for a in r['cands']:
            v = _read4(loc, a)
            if v is None:
                continue
            c, u1, u2, f = v
            if 0.0 < f <= 3600.0 and 0 <= c <= 10000 and 0 <= u1 <= 10000:
                live.append(a)
        if not live:
            skipped.append((r['name'], '地址已失效(被 GC 移动)，请重试'))
            continue
        for a in live:
            _write_f32(loc, a + 12, target)
            wrote.append((r['name'], a + 12))

    time.sleep(0.4)

    ok_names = set()
    for nm, cd_addr in wrote:
        v = _read_f32(loc, cd_addr)
        if v is not None and abs(v - target) < 1e-3:
            ok_names.add(nm)

    for r in res:
        if not r['cands']:
            continue
        if only_tight and r['hits'] > 4:
            continue
        if r['name'] in ok_names:
            changed.append((r['name'], r['cd'], r['hits'], True))
        else:
            skipped.append((r['name'], '写入后回读不符(可能被 GC 移动/被游戏覆盖)'))
    return changed, skipped
