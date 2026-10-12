import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adbmem import Adb, MemSnapshot, guess_adb
import scanner

adb = Adb(guess_adb())
print("adb:", adb.adb)
print(adb.connect('127.0.0.1', 16384))
adb.serial = '127.0.0.1:16384'
print(adb.root())
pid = adb.pid_of('com.rockbite.deeptown')
print("pid:", pid)
print("ver:", adb.pkg_version('com.rockbite.deeptown'))

t = time.time()
regions = adb.maps(pid)
print("maps:", len(regions), "%.2fs" % (time.time() - t))

for mode in ('java', 'anon', 'all'):
    sel = scanner.filter_regions(regions, mode)
    tot = sum(r['end'] - r['start'] for r in sel)
    print(mode, len(sel), "regions", "%.1f MB" % (tot / 1048576))

sel = scanner.filter_regions(regions, 'java')
sel.sort(key=lambda r: r['end'] - r['start'], reverse=True)
t = time.time()
snap = MemSnapshot(adb, pid, sel)
print("snapshot: %.2fs  %.1f MB  %d regions" % (time.time() - t, snap.total() / 1048576, len(snap.regions)))

t = time.time()
a, v = scanner.scan_snapshot(snap, 'Int   (4)', '等于 =', 1)
print("scan int==1: %d hits  %.2fs" % (len(a), time.time() - t))

t = time.time()
a2, v2 = scanner.scan_snapshot(snap, 'Long  (8)', '大于 >', 1000)
print("scan long>1000: %d hits  %.2fs" % (len(a2), time.time() - t))

# 读写测试
if len(a) > 0:
    addr = int(a[0])
    print("test addr %016X val=%s" % (addr, v[0]))
    print("read back:", adb.read_mem(pid, addr, 4).hex())
