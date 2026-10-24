import sys, logging
logging.getLogger("androguard").setLevel(logging.CRITICAL)
try:
    from loguru import logger; logger.remove()
except Exception: pass
from androguard.core.dex import DEX
d=DEX(open(sys.argv[1],'rb').read())
targets=set(sys.argv[2:])
for c in d.get_classes():
    if not any(t in c.get_name() for t in targets): continue
    print("="*80); print("CLASS", c.get_name())
    for m in c.get_methods():
        code=m.get_code()
        print("-"*60); print(" ", m.get_name(), m.get_descriptor())
        if not code: continue
        for ins in code.get_bc().get_instructions():
            print(f"     {ins.get_name():22s} {ins.get_output()[:150]}")
