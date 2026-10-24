import sys, logging
logging.getLogger("androguard").setLevel(logging.CRITICAL)
try:
    from loguru import logger; logger.remove()
except Exception: pass
from androguard.core.dex import DEX
d=DEX(open(sys.argv[1],'rb').read())
for c in d.get_classes():
    if "SplashViewModel" not in c.get_name(): continue
    for m in c.get_methods():
        if m.get_name()!="loadData": continue
        code=m.get_code()
        ins_list=list(code.get_bc().get_instructions())
        idx=[i for i,x in enumerate(ins_list) if "setConfigId" in x.get_output()]
        for i in idx:
            for j in range(max(0,i-14), min(len(ins_list), i+6)):
                mark = ">>" if j==i else "  "
                print(f"{mark} {ins_list[j].get_name():22s} {ins_list[j].get_output()[:160]}")
