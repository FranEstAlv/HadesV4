import struct, glob, re
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
host=re.compile(r'\b([a-z0-9][a-z0-9-]{2,60}\.(?:com|mx|net|io|co|dev|app|cloud|site|org))\b')
seen={}
for f in sorted(glob.glob('/home/olimpo/GenCookie/GenCookie/Krispy/apk_out/classes*.dex')):
    for s in dex_strings(f):
        for m in host.finditer(s):
            h=m.group(1)
            seen.setdefault(h, s[:150])
skip=('google','gstatic','github','schema.org','w3.org','android','facebook','adobe','mozilla','androidx','jetbrains','squareup','appboy','segment','amplitude','braze','flurry','unity','yandex','tencent','salesforce','mixpanel','leanplum','branch','adjust','appsflyer','onesignal','localytics','hockeyapp','crashlytics','amazonaws','pinterest','foursquare','braintree','stripe','adyen','javadoc','openfin','example','wix','cloudflare','akamai','shopify','mailchimp','sentry','bugsnag','instabug','clevertap','appcenter','hockey','unityads','vungle','ironsource','adcolony','mopub','inmobi','chartboost','tapjoy','fyber','applovin','pangle','mintegral','yandexads','vk','mail.ru','sber','tinkoff','tr3sco','nimbusds','journeyapps','daimajia','hbb20','usdk','futuremind','jaredrummler','drewhamilton','javax','mozilla','sun','oracle','intel','ibm','jcp','json','spotify','wikimedia')
for h in sorted(seen):
    if any(k in h for k in skip): continue
    print(f"{h:45s} | {seen[h][:90]}")
