import sys, logging
logging.disable(logging.CRITICAL)
from androguard.core.dex import DEX
path=sys.argv[1]; needle=sys.argv[2]
d=DEX(open(path,'rb').read())
hits=[]
for c in d.get_classes():
    cn = c.get_name()
    for m in c.get_methods():
        code = m.get_code()
        if not code: continue
        bc = code.get_bc()
        out = []
        found = False
        for ins in bc.get_instructions():
            o = ins.get_output()
            out.append(f"  {ins.get_name():24s} {o}")
            if needle in o and ins.get_name().startswith('const-string'):
                found = True
        if found:
            hits.append((cn, m.get_name(), m.get_descriptor(), out))
print("METODOS:", len(hits))
for cn, mn, desc, out in hits[:4]:
    print("="*80); print(cn, mn, desc)
    for line in out[:120]: print(line)
