import struct, glob, re, sys
def uleb128(d,o):
    r=0;s=0
    while True:
        b=d[o];o+=1;r|=(b&0x7F)<<s
        if not(b&0x80):break
        s+=7
    return r,o
def dex_strings(path):
    data=open(path,'rb').read()
    n=struct.unpack_from('<I',data,0x38)[0];off=struct.unpack_from('<I',data,0x3c)[0]
    for i in range(n):
        so=struct.unpack_from('<I',data,off+4*i)[0]
        _,p=uleb128(data,so);e=data.index(b'\x00',p)
        yield data[p:e].decode('utf-8','replace')
pat=re.compile(sys.argv[1], re.I); win=int(sys.argv[2]) if len(sys.argv)>2 else 6
for f in sorted(glob.glob('/home/olimpo/GenCookie/GenCookie/Krispy/apk_out/classes*.dex')):
    ss=list(dex_strings(f))
    for i,s in enumerate(ss):
        if pat.search(s) and len(s)<160:
            print(f"--- {f.split('/')[-1]} #{i}: {s}")
            for j in range(max(0,i-win), min(len(ss), i+win+1)):
                if j!=i and len(ss[j])<120: print(f"      {ss[j]}")
