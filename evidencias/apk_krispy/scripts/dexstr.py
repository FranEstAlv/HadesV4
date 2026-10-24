import struct, sys, glob, re

def uleb128(data, off):
    result = 0; shift = 0
    while True:
        b = data[off]; off += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80): break
        shift += 7
    return result, off

def dex_strings(path):
    data = open(path,'rb').read()
    if data[:4] != b'dex\n': return
    n = struct.unpack_from('<I', data, 0x38)[0]
    off = struct.unpack_from('<I', data, 0x3c)[0]
    for i in range(n):
        so = struct.unpack_from('<I', data, off + 4*i)[0]
        _, p = uleb128(data, so)
        e = data.index(b'\x00', p)
        try:
            yield data[p:e].decode('utf-8','replace')
        except Exception:
            pass

pat = re.compile(sys.argv[1] if len(sys.argv)>1 else r'justo', re.I)
seen=set()
for f in sorted(glob.glob('/home/olimpo/GenCookie/GenCookie/Krispy/apk_out/classes*.dex')):
    for s in dex_strings(f):
        if pat.search(s) and s not in seen:
            seen.add(s)
            print(f"STR| {s[:220]}")
