import sys, logging
logging.getLogger("androguard").setLevel(logging.CRITICAL)
try:
    from loguru import logger; logger.remove()
except Exception: pass
from androguard.core.dex import DEX
d=DEX(open(sys.argv[1],'rb').read())
want = sys.argv[2:]   # pares "clase::metodo"
pairs = [w.split("::") for w in want]
for c in d.get_classes():
    for m in c.get_methods():
        for cls, meth in pairs:
            if cls in c.get_name() and m.get_name()==meth:
                code=m.get_code()
                print("="*80); print(c.get_name(), "|", m.get_name(), m.get_descriptor())
                if not code: print("  (sin cuerpo)"); continue
                for ins in code.get_bc().get_instructions():
                    print(f"   {ins.get_name():22s} {ins.get_output()[:160]}")
