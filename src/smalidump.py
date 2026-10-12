import sys, os, re
from loguru import logger
logger.remove()
from androguard.core.dex import DEX

# 解包后的 dex 目录。可用环境变量 DEEPTown_UNPACKED 指定
UNP = os.environ.get('DEEPTown_UNPACKED') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'unpacked')


def dump(clsname, method_filter=None, max_ins=200):
    files = sorted([f for f in os.listdir(UNP) if re.match(r'classes\d*\.dex$', f)])
    for fn in files:
        data = open(os.path.join(UNP, fn), 'rb').read()
        try:
            d = DEX(data)
        except Exception:
            continue
        found = False
        for c in d.get_classes():
            if c.get_name() != clsname:
                continue
            found = True
            print('### %s  (in %s)' % (clsname, fn))
            for f in c.get_fields():
                print('  field %-30s %s' % (f.get_name(), f.get_descriptor()))
            for m in c.get_methods():
                mn = m.get_name()
                if method_filter and not re.search(method_filter, mn):
                    continue
                print('\n  --- %s%s' % (mn, m.get_descriptor()))
                code = m.get_code()
                if code is None:
                    print('      (no code)')
                    continue
                try:
                    bc = code.get_bc()
                    n = 0
                    for ins in bc.get_instructions():
                        print('      %-8s %s' % (ins.get_name(), ins.get_output()))
                        n += 1
                        if n > max_ins:
                            print('      ...')
                            break
                except Exception as e:
                    print('      disasm err', e)
        if found:
            return
    print('class not found:', clsname)


if __name__ == '__main__':
    dump(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
