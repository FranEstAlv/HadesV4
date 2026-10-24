import sys, logging
logging.getLogger("androguard").setLevel(logging.CRITICAL)
try:
    from loguru import logger; logger.remove()
except Exception: pass
from androguard.core.dex import DEX
d=DEX(open(sys.argv[1],'rb').read())
for c in d.get_classes():
    if c.get_name()!="Lcom/tr3sco/tr3scoFramework/webApi/WebApiService;": continue
    for m in c.get_methods():
        if m.get_name()!="post": continue
        print("attrs:", [a for a in dir(m) if not a.startswith("_")])
        try:
            em = m.get_method()
            print("encoded:", type(em), [a for a in dir(em) if not a.startswith("_")][:40])
            for a in ("get_annotations","get_annotation","get_access_flags"):
                pass
            ann = em.get_annotations() if hasattr(em,"get_annotations") else None
            print("ann:", ann)
        except Exception as e:
            print("err", e)
