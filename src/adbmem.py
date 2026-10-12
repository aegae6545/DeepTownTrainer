"""
ADB 内存后端: 通过 adb exec-out + dd 读写 /proc/<pid>/mem
root(模拟器) 下无需注入任何东西, 实测 1GB 快照 ~1.7s
"""
import re
import subprocess
import os

MUMU_PORTS = [16384, 7555, 16385, 7554, 62001, 62026]

CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


def hidden_kw():
    """
    Windows 上从 GUI(pythonw) 里调 adb.exe, 默认会弹出一个控制台窗口并抢前台。
    扫描时几百次调用 = 满屏闪窗。这里用 CREATE_NO_WINDOW + SW_HIDE 彻底静默。
    """
    if os.name != 'nt':
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {'creationflags': CREATE_NO_WINDOW, 'startupinfo': si}


def guess_adb():
    """定位 MuMu 自带 adb.exe"""
    cands = []
    for drv in ('D:/', 'C:/', 'E:/'):
        for p in ('mummu/shell/adb.exe', 'MuMu/shell/adb.exe',
                  'Program Files/MuMu/shell/adb.exe',
                  'Program Files/Netease/MuMuPlayer-12.0/shell/adb.exe',
                  'mumu/shell/adb.exe'):
            cands.append(drv + p)
    cands.append(os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Android', 'Sdk', 'platform-tools', 'adb.exe'))
    for c in cands:
        if os.path.exists(c):
            return c
    return 'adb'


class Adb:
    def __init__(self, adb_path=None, serial=None):
        self.adb = adb_path or guess_adb()
        self.serial = serial

    def _cmd(self, *args, binary=False):
        base = [self.adb]
        if self.serial:
            base += ['-s', self.serial]
        base += list(args)
        if binary:
            return subprocess.run(base, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, **hidden_kw())
        return subprocess.run(base, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding='utf-8', errors='replace', **hidden_kw())

    def devices(self):
        r = self._cmd('devices')
        out = []
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == 'device':
                out.append(parts[0])
        return out

    def connect(self, host='127.0.0.1', port=16384):
        r = self._cmd('connect', '%s:%d' % (host, port))
        return r.stdout.strip()

    def root(self):
        return self._cmd('root').stdout.strip()

    def shell(self, cmd):
        return self._cmd('shell', cmd).stdout

    def shell_bytes(self, cmd):
        return self._cmd('exec-out', cmd, binary=True).stdout

    # ---------- 进程 ----------
    def processes(self):
        """返回 [(pid, name)]"""
        out = self.shell('ps -A -o PID,NAME')
        res = []
        for line in out.splitlines()[1:]:
            p = line.split()
            if len(p) >= 2 and p[0].isdigit():
                res.append((int(p[0]), p[1]))
        return res

    def pid_of(self, pkg):
        for pid, name in self.processes():
            if name == pkg:
                return pid
        # 兜底: pidof
        r = self.shell('pidof %s' % pkg).strip()
        return int(r.split()[0]) if r and r.split() else None

    def pkg_version(self, pkg):
        o = self.shell('dumpsys package %s' % pkg)
        m = re.search(r'versionName=([\d.]+)', o)
        c = re.search(r'versionCode=(\d+)', o)
        return (m.group(1) if m else '?'), (c.group(1) if c else '?')

    # ---------- 内存 ----------
    def maps(self, pid):
        raw = self.shell('cat /proc/%d/maps' % pid)
        regions = []
        for line in raw.splitlines():
            m = re.match(r'^([0-9a-f]+)-([0-9a-f]+)\s+(\S{4})\s+(\S+)\s+(\S+)\s+(\d+)?\s*(.*)$', line)
            if not m:
                continue
            s, e, prot, off, dev, inode, path = m.groups()
            regions.append({
                'start': int(s, 16), 'end': int(e, 16), 'prot': prot,
                'offset': int(off, 16), 'inode': int(inode) if inode else 0,
                'path': (path or '').strip()
            })
        return regions

    def read_mem(self, pid, addr, size):
        """精确读取(字节粒度), 用于少量读取"""
        cmd = 'dd if=/proc/%d/mem bs=1 skip=%d count=%d 2>/dev/null | base64' % (pid, addr, size)
        raw = self.shell(cmd)
        import base64
        try:
            return base64.b64decode(''.join(raw.split()))
        except Exception:
            return b''

    def read_region(self, pid, start, size, bs=1 << 20):
        """按 bs 对齐的块读取, 返回 bytes(失败块填 0)"""
        a0 = start // bs
        a1 = (start + size + bs - 1) // bs
        nblocks = a1 - a0
        # 单次 dd 最多读, 太大分片
        out = bytearray()
        CH = 256  # 256MB / 次
        pos = a0
        while pos < a1:
            cnt = min(CH, a1 - pos)
            cmd = 'dd if=/proc/%d/mem bs=%d skip=%d count=%d 2>/dev/null' % (pid, bs, pos, cnt)
            chunk = self.shell_bytes(cmd)
            if len(chunk) < cnt * bs:
                chunk += b'\x00' * (cnt * bs - len(chunk))
            out += chunk[:cnt * bs]
            pos += cnt
        off = start - a0 * bs
        return bytes(out[off:off + size])

    def write_mem(self, pid, addr, data):
        """写内存: 通过 printf + dd seek (字节粒度)"""
        hexs = ''.join('\\x%02x' % b for b in data)
        cmd = ("printf '%s' | dd of=/proc/%d/mem bs=1 seek=%d conv=notrunc 2>/dev/null; echo done"
               % (hexs, pid, addr))
        r = self.shell(cmd)
        return 'done' in r

    def write_u64(self, pid, addr, value):
        import struct
        return self.write_mem(pid, addr, struct.pack('<q', value))

    def write_u32(self, pid, addr, value):
        import struct
        return self.write_mem(pid, addr, struct.pack('<I', value & 0xffffffff))


class MemSnapshot:
    """一次内存快照: 把相邻区域合并成大块一次 dd 读取, 大幅减少 adb 往返"""

    MERGE_GAP = 32 * 1024 * 1024   # 间隙小于 32MB 就合并读

    def __init__(self, adb, pid, regions):
        self.adb = adb
        self.pid = pid
        self.regions = sorted(regions, key=lambda r: r['start'])
        self.blobs = [b'' for _ in self.regions]
        self._fetch()

    def _fetch(self):
        bs = 1 << 20
        blobs = [None] * len(self.regions)
        # 1) 分组
        groups, cur = [], []
        for i, r in enumerate(self.regions):
            if cur:
                prev = self.regions[cur[-1]]
                if r['start'] - prev['end'] > self.MERGE_GAP:
                    groups.append(cur); cur = []
            cur.append(i)
        if cur:
            groups.append(cur)

        # 2) 构造并发读取任务: 每个大组切成 128MB 的片
        BS = bs
        CH = 128
        tasks = []          # (group_buf_id, byte_offset_in_buf, block_start, nblocks)
        gbufs = []          # 每个 group 一个预分配 bytearray
        for gi, g in enumerate(groups):
            gstart = self.regions[g[0]]['start']
            gend = self.regions[g[-1]]['end']
            a0 = gstart // BS
            a1 = (gend + BS - 1) // BS
            gbufs.append(bytearray((a1 - a0) * BS))
            tasks.append({'gi': gi, 'base': a0})
            pos = a0
            while pos < a1:
                cnt = min(CH, a1 - pos)
                tasks.append({'gi': gi, 'a0': a0, 'blk': pos, 'cnt': cnt})
                pos += cnt

        jobs = [t for t in tasks if 'blk' in t]
        if jobs:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as ex:
                futs = [ex.submit(self._read_blocks, j['blk'], j['cnt'], BS) for j in jobs]
                for j, fu in zip(jobs, futs):
                    data = fu.result()
                    off = (j['blk'] - j['a0']) * BS
                    need = j['cnt'] * BS
                    if len(data) < need:
                        data = data + b'\x00' * (need - len(data))
                    gbufs[j['gi']][off:off + need] = data[:need]

        for gi, g in enumerate(groups):
            a0 = tasks[[t['gi'] for t in tasks].index(gi)]['base'] if False else None
            # 找到该组的 base
            for t in tasks:
                if t['gi'] == gi and 'base' in t:
                    a0 = t['base']; break
            base_addr = a0 * BS
            mv = memoryview(gbufs[gi])
            for i in g:
                r = self.regions[i]
                o = r['start'] - base_addr
                blobs[i] = mv[o:o + (r['end'] - r['start'])]
        self.blobs = [b if b is not None else b'' for b in blobs]

    def _read_blocks(self, blk, cnt, bs):
        # 设备端 gzip -1 压缩: 堆内存大片为零, 实测 512MB -> ~10MB, 传输快 50 倍
        cmd = 'dd if=/proc/%d/mem bs=%d skip=%d count=%d 2>/dev/null | gzip -1' % (self.pid, bs, blk, cnt)
        raw = self.adb.shell_bytes(cmd)
        if not raw:
            return b''
        try:
            import gzip as _gz
            return _gz.decompress(raw)
        except Exception:
            return b''

    def refresh(self):
        self._fetch()

    def value_at(self, region_idx, offset, fmt):
        import struct
        b = self.blobs[region_idx]
        sz = struct.calcsize(fmt)
        if offset < 0 or offset + sz > len(b):
            return None
        return struct.unpack_from(fmt, b, offset)[0]

    def total(self):
        return sum(len(b) for b in self.blobs)
