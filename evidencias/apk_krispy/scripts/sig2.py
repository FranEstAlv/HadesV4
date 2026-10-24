import sys, logging
logging.getLogger("androguard").setLevel(logging.CRITICAL)
try:
    from loguru import logger; logger.remove()
except Exception: pass
from androguard.core.dex import DEX
d=DEX(open(sys.argv[1],'rb').read())
for c in d.get_classes():
    if "SignatureAlgorithm" not in c.get_name(): continue
    for m in c.get_methods():
        if m.get_name() not in ("signature","joinInputList","hash","encrypt","sha","md5"): continue
        code=m.get_code()
        print("="*80); print(c.get_name(), "|", m.get_name(), m.get_descriptor())
        if not code: continue
        for ins in code.get_bc().get_instructions():
            print(f"   {ins.get_name():22s} {ins.get_output()[:160]}")
