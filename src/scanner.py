"""内存扫描引擎: 基于 numpy 在快照上做高速匹配"""
import struct
import numpy as np

TYPES = {
    'Byte  (1)':      ('B', 1, np.uint8),
    'Short (2) LE':   ('<h', 2, np.int16),
    'Word  (2) unsigned': ('<H', 2, np.uint16),
    'Int   (4)':      ('<i', 4, np.int32),
    'Dword (4) unsigned': ('<I', 4, np.uint32),
    'Long  (8)':      ('<q', 8, np.int64),
    'Qword (8) unsigned': ('<Q', 8, np.uint64),
    'Float (4)':      ('<f', 4, np.float32),
    'Double (8)':     ('<d', 8, np.float64),
}

CMP = ['等于 =', '不等于 ≠', '大于 >', '小于 <', '大于等于 ≥', '小于等于 ≤',
       '介于 (min~max)', '数值增大了', '数值减小了', '数值变化了', '数值未变', '未知初始值']

MAX_HITS = 500000


def fmt_of(label):
    return TYPES[label][0]


def size_of(label):
    return TYPES[label][1]


def np_of(label):
    return TYPES[label][2]


def read_value(blob, off, label):
    f = fmt_of(label)
    if off < 0 or off + struct.calcsize(f) > len(blob):
        return None
    return struct.unpack_from(f, blob, off)[0]


def region_label(r):
    """给内存区域一个人类可读的名字"""
    p = r['path']
    if p.startswith('[anon:dalvik-main space') or 'region space' in p:
        return 'Java 堆 (ART region space)'
    if 'dalvik-large object space' in p:
        return 'Java 大对象 (large object space)'
    if 'dalvik-zygote space' in p:
        return 'Java 堆 (zygote space)'
    if 'dalvik' in p or 'art' in p or 'heap' in p:
        return 'ART/堆 (其它)'
    if p.startswith('[anon'):
        return '匿名内存 (anon)'
    if p.startswith('[stack'):
        return '栈 (stack)'
    if p.startswith('['):
        return p.strip('[]')
    if p.endswith('.so'):
        return '库: ' + p.rsplit('/', 1)[-1]
    if p.endswith(('.dex', '.odex', '.oat', '.art', '.vdex')):
        return '代码: ' + p.rsplit('/', 1)[-1]
    if p:
        return '文件: ' + p.rsplit('/', 1)[-1]
    return '未命名'


def filter_regions(regions, mode, min_size=4096):
    out = []
    for r in regions:
        if 'w' not in r['prot']:
            continue
        n = r['end'] - r['start']
        if n < min_size:
            continue
        p = r['path']
        if mode == 'java':
            ok = ('dalvik' in p) or ('region space' in p) or ('large object' in p)
        elif mode == 'anon':
            ok = p.startswith('[anon') and not p.startswith('[anon:dalvik')
        elif mode == 'all':
            ok = True
        elif mode == 'small':
            ok = n <= 64 * 1024 * 1024
        else:
            ok = True
        if ok:
            rr = dict(r)
            rr['label'] = region_label(r)
            out.append(rr)
    return out


def scan_snapshot(snap, label, cmp_op, v1, v2=None, addrs=None, prev=None, aligned=True):
    """
    snap: MemSnapshot
    addrs: 上一次的候选绝对地址(np.uint64 数组) - 提供则只在这些地址上比较
    prev:  上一次的值数组(用于 增大/减小/变化/未变)
    返回 (np.array 地址, np.array 值)
    """
    dt = np_of(label)
    sz = size_of(label)
    res_a, res_v = [], []

    for ri, r in enumerate(snap.regions):
        blob = snap.blobs[ri]
        base = r['start']
        if addrs is None:
            usable = (len(blob) // sz) * sz
            if usable <= sz:
                continue
            arr = np.frombuffer(blob[:usable], dtype=dt)
            idx = _match(arr, cmp_op, v1, v2)
            if idx is None or idx.size == 0:
                continue
            if idx.size > MAX_HITS:
                pos = np.linspace(0, idx.size - 1, MAX_HITS).astype(np.int64)
                idx = idx[pos]
            a = base + idx.astype(np.uint64) * sz
            v = arr[idx]
        else:
            # 只取属于本区域的候选
            sel = (addrs >= base) & (addrs < base + len(blob))
            if not np.any(sel):
                continue
            sub = addrs[sel]
            offs = (sub - base).astype(np.int64)
            ok = (offs >= 0) & (offs + sz <= len(blob))
            offs = offs[ok]
            sub = sub[ok]
            if offs.size == 0:
                continue
            # 逐字节读取(数量有限, 直接用 numpy 索引)
            raw = np.frombuffer(blob, dtype=np.uint8)
            # 构造小端读取
            cols = [raw[offs + k].astype(dt) for k in range(sz)]
            val = cols[0]
            for k in range(1, sz):
                val = val | (cols[k] << (8 * k))
            val = val.view(dt)
            if prev is not None:
                psel = prev[sel]
                psel = psel[ok]
                keep = _cmp_prev(val, psel, cmp_op)
            else:
                keep = _match(val, cmp_op, v1, v2)
            if keep is None or keep.size == 0:
                continue
            a = sub[keep]
            v = val[keep]
        res_a.append(a)
        res_v.append(v)
        if sum(x.size for x in res_a) > MAX_HITS:
            break

    if not res_a:
        return np.array([], dtype=np.uint64), np.array([], dtype=dt)
    return np.concatenate(res_a), np.concatenate(res_v)


def _match(arr, op, v1, v2):
    try:
        if op == '未知初始值':
            return np.arange(arr.size, dtype=np.int64)
        if op == '等于 =':
            return np.flatnonzero(arr == v1)
        if op == '不等于 ≠':
            return np.flatnonzero(arr != v1)
        if op == '大于 >':
            return np.flatnonzero(arr > v1)
        if op == '小于 <':
            return np.flatnonzero(arr < v1)
        if op == '大于等于 ≥':
            return np.flatnonzero(arr >= v1)
        if op == '小于等于 ≤':
            return np.flatnonzero(arr <= v1)
        if op == '介于 (min~max)':
            return np.flatnonzero((arr >= v1) & (arr <= v2))
    except Exception:
        return np.array([], dtype=np.int64)
    return np.array([], dtype=np.int64)


def _cmp_prev(cur, prev, op):
    if op == '数值增大了':
        return np.flatnonzero(cur > prev)
    if op == '数值减小了':
        return np.flatnonzero(cur < prev)
    if op == '数值变化了':
        return np.flatnonzero(cur != prev)
    if op == '数值未变':
        return np.flatnonzero(cur == prev)
    return None
