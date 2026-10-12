"""在 DEX 中定位存档加密相关代码: 谁引用了 Base64 / Cipher / Preferences key"""
import sys, os, re
from androguard.core.dex import DEX

# 解包后的 assets/dex 目录。可用环境变量 DEEPTown_UNPACKED 指定
UNPACKED = os.environ.get('DEEPTown_UNPACKED') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'unpacked')

KEYWORDS = [
    'Demolisher Preferences',
    'Base64Coder',
    'javax/crypto/Cipher',
    'SecretKeySpec',
    'IvParameterSpec',
    'AES',
    'Gdx.app.getPreferences',
]

def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else KEYWORDS
    dexfiles = sorted([f for f in os.listdir(UNPACKED) if re.match(r'classes\d*\.dex$', f)],
                      key=lambda s: len(s))
    print('dex files:', dexfiles)
    total = 0
    for fn in dexfiles:
        path = os.path.join(UNPACKED, fn)
        data = open(path, 'rb').read()
        print('--- loading %s (%.1f MB)' % (fn, len(data) / 1048576))
        sys.stdout.flush()
        try:
            d = DEX(data)
        except Exception as e:
            print('  load fail', e); continue
        try:
            methods = list(d.get_methods())
        except Exception as e:
            print('  methods fail', e); continue
        print('  methods:', len(methods))
        sys.stdout.flush()
        for m in methods:
            try:
                code = m.get_code()
                if code is None: continue
                bc = code.get_bc()
            except Exception:
                continue
            got = []
            try:
                for ins in bc.get_instructions():
                    s = ins.get_output()
                    for t in targets:
                        if t in s:
                            got.append(s.strip())
            except Exception:
                continue
            if got:
                cls = m.get_class_name()
                if '/underwater/' in cls or 'demolisher' in cls or 'SaveData' in cls:
                    total += 1
                    print('  [%s] %s %s' % (fn, m.get_class_name(), m.get_name()))
                    for g in got[:6]:
                        print('        ', g[:150])
                    if total > 120:
                        print('... too many, stop'); return
        del d
    print('done, hits:', total)

main()
