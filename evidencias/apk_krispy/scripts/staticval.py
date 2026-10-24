import sys, logging
logging.getLogger("androguard").setLevel(logging.CRITICAL)
try:
    from loguru import logger; logger.remove()
except Exception: pass
from androguard.core.dex import DEX
d=DEX(open(sys.argv[1],'rb').read())
target=sys.argv[2]
for c in d.get_classes():
    if c.get_name()!=target: continue
    print("CLASS", c.get_name())
    try:
        sv = c.get_static_values()
        for k,v in (sv or {}).items():
            print("   static:", k, "=", repr(v)[:200])
    except Exception as e:
        print("   (sin static values)", e)
    for m in c.get_methods():
        if m.get_name() in ("<clinit>","<init>","getConfigId","get_configId","load"):
            code=m.get_code()
            print("="*70); print(" ", m.get_name(), m.get_descriptor())
            if not code: continue
            for ins in code.get_bc().get_instructions():
                o=ins.get_output()
                if ins.get_name().startswith("const-string") or "configId" in o or "sput" in ins.get_name() or "ConfigId" in o:
                    print(f"     {ins.get_name():18s} {o[:150]}")
