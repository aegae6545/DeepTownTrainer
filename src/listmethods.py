import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dexparse

# 目标 APK。可用环境变量 DEEPTown_APK 指定
APK = os.environ.get('DEEPTown_APK') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'com.rockbite.deeptown.apk')


def all_methods(d):
    out = {}
    for i in range(d.md_size):
        c, n = d.method_name(i)
        out.setdefault(c, set()).add(n)
    return out


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else None
    ds = dexparse.load_apk(APK)
    seen = {}
    for d in ds:
        for c, names in all_methods(d).items():
            seen.setdefault(c, set()).update(names)
    print('total classes with methods:', len(seen))
    if pat:
        rx = re.compile(pat, re.I)
        hits = {c: n for c, n in seen.items() if rx.search(c)}
        for c in sorted(hits):
            print('== %s' % c)
            print('   %s' % ' | '.join(sorted(hits[c])[:35]))
        # 也按方法名搜
        mhits = {}
        for c, ns in seen.items():
            hit = [n for n in ns if rx.search(n)]
            if hit:
                mhits[c] = hit
        print('\n### 方法名命中 %d 个类' % len(mhits))
        for c in sorted(mhits)[:60]:
            print('  %-70s %s' % (c, ' | '.join(sorted(mhits[c])[:12])))


main()
