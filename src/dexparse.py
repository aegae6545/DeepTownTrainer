import struct, sys, zipfile, io

def uleb128(buf, off):
    result = 0; shift = 0
    while True:
        b = buf[off]; off += 1
        result |= (b & 0x7f) << shift
        if b & 0x80 == 0: break
        shift += 7
    return result, off

class Dex:
    def __init__(self, data):
        self.d = data
        assert data[:4] == b'dex\n', 'bad magic'
        self.str_size, = struct.unpack_from('<I', data, 0x38)
        self.str_off,  = struct.unpack_from('<I', data, 0x3c)
        self.ty_size,  = struct.unpack_from('<I', data, 0x40)
        self.ty_off,   = struct.unpack_from('<I', data, 0x44)
        self.fd_size,  = struct.unpack_from('<I', data, 0x50)
        self.fd_off,   = struct.unpack_from('<I', data, 0x54)
        self.md_size,  = struct.unpack_from('<I', data, 0x58)
        self.md_off,   = struct.unpack_from('<I', data, 0x5c)
        self.cd_size,  = struct.unpack_from('<I', data, 0x60)
        self.cd_off,   = struct.unpack_from('<I', data, 0x64)

    def str_at(self, i):
        off, = struct.unpack_from('<I', self.d, self.str_off + i * 4)
        # uleb128 length then bytes
        ln, p = uleb128(self.d, off)
        return self.d[p:p + ln].decode('utf-8', 'replace')

    def type_at(self, i):
        idx, = struct.unpack_from('<I', self.d, self.ty_off + i * 4)
        return self.str_at(idx)

    def field_name(self, i):
        c, t, n = struct.unpack_from('<HHI', self.d, self.fd_off + i * 8)
        return self.type_at(c), self.type_at(t), self.str_at(n)

    def method_name(self, i):
        c, p, n = struct.unpack_from('<HHI', self.d, self.md_off + i * 8)
        return self.type_at(c), self.str_at(n)

    def class_defs(self):
        out = {}
        for i in range(self.cd_size):
            base = self.cd_off + i * 32
            ci, = struct.unpack_from('<I', self.d, base)
            cdata_off, = struct.unpack_from('<I', self.d, base + 24)
            out[self.type_at(ci)] = cdata_off
        return out

    def class_fields(self, cdata_off):
        if cdata_off == 0: return [], []
        static_n, p = uleb128(self.d, cdata_off)
        inst_n, p = uleb128(self.d, p)
        _, p = uleb128(self.d, p)   # direct methods
        _, p = uleb128(self.d, p)   # virtual methods
        sf, if_ = [], []
        prev = 0
        for _ in range(static_n):
            diff, p = uleb128(self.d, p)
            _, p = uleb128(self.d, p)
            prev += diff
            sf.append(self.field_name(prev))
        prev = 0
        for _ in range(inst_n):
            diff, p = uleb128(self.d, p)
            _, p = uleb128(self.d, p)
            prev += diff
            if_.append(self.field_name(prev))
        return sf, if_


def load_apk(path):
    z = zipfile.ZipFile(path)
    dexes = []
    for i in range(1, 20):
        n = 'classes%s.dex' % ('' if i == 1 else i)
        if n in z.namelist():
            dexes.append(Dex(z.read(n)))
    return dexes


if __name__ == '__main__':
    apk = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else ''
    dexes = load_apk(apk)
    for d in dexes:
        for cname, cdata in d.class_defs().items():
            if target and target not in cname: continue
            sf, if_ = d.class_fields(cdata)
            if not sf and not if_: continue
            print('== %s' % cname)
            for c, t, n in sf: print('  static  %-40s %s' % (t, n))
            for c, t, n in if_: print('  field   %-40s %s' % (t, n))
