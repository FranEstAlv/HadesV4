import os ,re ,json ,time ,zlib ,uuid ,base64 ,random ,hashlib ,itertools 
from typing import Optional 
from urllib .parse import urlparse 
from curl_cffi import requests 
from cryptography .hazmat .primitives .ciphers .aead import AESGCM 
import requests as stdReq 

from .curl_session import ImpersonateSession 
from bot.config import AES_KEY, C_SCRYPT, C_SHA256, C_BANDWIDTH



class AwsWaf :
    aesKey =AES_KEY 
    gcm =AESGCM (aesKey )

    cScrypt =C_SCRYPT 
    cSha256 =C_SHA256 
    cBandwidth =C_BANDWIDTH 

    bwSizes ={1 :0x400 ,2 :0xA *0x400 ,3 :0x64 *0x400 ,4 :0x100000 ,5 :0xA *0x100000 }
    fpVer ='2.4.0'
    defUa ='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36'

    gpuProfiles =[
    {'vendor':'Google Inc. (Apple)','model':'ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)','ext':'ANGLE_instanced_arrays;EXT_blend_minmax;EXT_clip_control;EXT_color_buffer_half_float;EXT_depth_clamp;EXT_float_blend;EXT_frag_depth;EXT_shader_texture_lod;EXT_texture_compression_bptc;EXT_texture_compression_rgtc;EXT_texture_filter_anisotropic;EXT_sRGB;OES_element_index_uint;OES_standard_derivatives;OES_texture_float;OES_texture_float_linear;OES_texture_half_float;OES_texture_half_float_linear;OES_vertex_array_object;WEBGL_color_buffer_float;WEBGL_compressed_texture_s3tc;WEBGL_compressed_texture_s3tc_srgb;WEBGL_debug_renderer_info;WEBGL_debug_shaders;WEBGL_depth_texture;WEBGL_draw_buffers;WEBGL_lose_context;WEBGL_multi_draw'},
    {'vendor':'Google Inc. (AMD)','model':'ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)','ext':'ANGLE_instanced_arrays;EXT_blend_minmax;EXT_color_buffer_half_float;EXT_depth_clamp;EXT_float_blend;EXT_frag_depth;EXT_shader_texture_lod;EXT_texture_compression_bptc;EXT_texture_compression_rgtc;EXT_texture_filter_anisotropic;EXT_sRGB;OES_element_index_uint;OES_standard_derivatives;OES_texture_float;OES_texture_float_linear;OES_texture_half_float;OES_texture_half_float_linear;OES_vertex_array_object;WEBGL_color_buffer_float;WEBGL_compressed_texture_s3tc;WEBGL_compressed_texture_s3tc_srgb;WEBGL_debug_renderer_info;WEBGL_debug_shaders;WEBGL_depth_texture;WEBGL_draw_buffers;WEBGL_lose_context;WEBGL_multi_draw'},
    {'vendor':'Google Inc. (Intel)','model':'ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)','ext':'ANGLE_instanced_arrays;EXT_blend_minmax;EXT_color_buffer_half_float;EXT_depth_clamp;EXT_float_blend;EXT_frag_depth;EXT_shader_texture_lod;EXT_texture_compression_bptc;EXT_texture_compression_rgtc;EXT_texture_filter_anisotropic;EXT_sRGB;OES_element_index_uint;OES_standard_derivatives;OES_texture_float;OES_texture_float_linear;OES_texture_half_float;OES_texture_half_float_linear;OES_vertex_array_object;WEBGL_color_buffer_float;WEBGL_compressed_texture_s3tc;WEBGL_compressed_texture_s3tc_srgb;WEBGL_debug_renderer_info;WEBGL_debug_shaders;WEBGL_depth_texture;WEBGL_draw_buffers;WEBGL_lose_context;WEBGL_multi_draw'},
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)','ext':'ANGLE_instanced_arrays;EXT_blend_minmax;EXT_color_buffer_half_float;EXT_depth_clamp;EXT_float_blend;EXT_frag_depth;EXT_shader_texture_lod;EXT_texture_compression_bptc;EXT_texture_compression_rgtc;EXT_texture_filter_anisotropic;EXT_sRGB;OES_element_index_uint;OES_standard_derivatives;OES_texture_float;OES_texture_float_linear;OES_texture_half_float;OES_texture_half_float_linear;OES_vertex_array_object;WEBGL_color_buffer_float;WEBGL_compressed_texture_s3tc;WEBGL_compressed_texture_s3tc_srgb;WEBGL_debug_renderer_info;WEBGL_debug_shaders;WEBGL_depth_texture;WEBGL_draw_buffers;WEBGL_lose_context;WEBGL_multi_draw'},
    ]

    def __init__ (self ,websiteURL ,proxy =None ,userAgent =None ,impersonate ='chrome'):
        self .url =websiteURL 
        self .proxy =proxy 
        self .ua =userAgent or self .defUa 
        self .impersonate =impersonate 
        self .jsCfg =None 



    def solve (self ):
        try :
            t0 =time .time ()
            token ,info =self .solveChallenge ()
            return {'status':True ,'url':self .url ,'proxy':self .proxy or 'direct','token':token ,'challenge':info ,'time':f'{time .time ()-t0 :.2f}s'}
        except Exception as e :
            return {'status':False ,'description':str (e )}



    def makeSess (self ):
        kw ={'impersonate':self .impersonate }
        if self .proxy :kw ['proxies']={'https':self .proxy ,'http':self .proxy }
        return ImpersonateSession (**kw )

    def pageHdrs (self ):
        return {'accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','accept-language':'en-US,en;q=0.9','cache-control':'no-cache','sec-ch-ua':'"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"','sec-ch-ua-mobile':'?0','sec-ch-ua-platform':'"Windows"','sec-fetch-dest':'document','sec-fetch-mode':'navigate','sec-fetch-site':'none','sec-fetch-user':'?1','upgrade-insecure-requests':'1','user-agent':self .ua }

    def apiHdrs (self ,origin =None ,referer =None ):
        h ={'connection':'keep-alive','sec-ch-ua-platform':'"Windows"','user-agent':self .ua ,'sec-ch-ua':'"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"','sec-ch-ua-mobile':'?0','accept':'*/*','sec-fetch-site':'cross-site','sec-fetch-mode':'cors','sec-fetch-dest':'empty','accept-encoding':'gzip, deflate, br, zstd','accept-language':'en-US,en;q=0.9'}
        if origin :h ['origin']=origin 
        if referer :h ['referer']=referer 
        return h 



    def aesEncrypt (self ,plaintext ):
        iv =os .urandom (12 )
        cipher =self .gcm .encrypt (iv ,plaintext ,None )
        tag ,ct =cipher [-16 :],cipher [:-16 ]
        return f'{base64 .b64encode (iv ).decode ()}::{tag .hex ()}::{ct .hex ()}'

    def crc32 (self ,data ):
        return f'{zlib .crc32 (data )&0xFFFFFFFF :08X}'



    def genFp (self ):
        ts =int (time .time ()*1000 )
        gpu =random .choice (self .gpuProfiles )
        bins =[random .randrange (0 ,40 )for _ in range (256 )]
        bins [0 ]=random .randrange (14473 ,16573 )
        bins [-1 ]=random .randrange (14473 ,16573 )
        return {'metrics':{'fp2':1 ,'browser':0 ,'capabilities':1 ,'gpu':7 ,'dnt':0 ,'math':0 ,'screen':0 ,'navigator':0 ,'auto':1 ,'stealth':0 ,'subtle':0 ,'canvas':5 ,'formdetector':1 ,'be':0 },'start':ts ,'flashVersion':None ,'plugins':[{'name':'PDF Viewer','str':'PDF Viewer '},{'name':'Chrome PDF Viewer','str':'Chrome PDF Viewer '},{'name':'Chromium PDF Viewer','str':'Chromium PDF Viewer '},{'name':'Microsoft Edge PDF Viewer','str':'Microsoft Edge PDF Viewer '},{'name':'WebKit built-in PDF','str':'WebKit built-in PDF '}],'dupedPlugins':'PDF Viewer Chrome PDF Viewer Chromium PDF Viewer Microsoft Edge PDF Viewer WebKit built-in PDF ||1920-1080-1032-24-*-*-*','screenInfo':'1920-1080-1032-24-*-*-*','referrer':'','userAgent':self .ua ,'location':'','webDriver':False ,'capabilities':{'css':{'textShadow':1 ,'WebkitTextStroke':1 ,'boxShadow':1 ,'borderRadius':1 ,'borderImage':1 ,'opacity':1 ,'transform':1 ,'transition':1 },'js':{'audio':True ,'geolocation':random .choice ([True ,False ]),'localStorage':'supported','touch':False ,'video':True ,'webWorker':random .choice ([True ,False ])},'elapsed':1 },'gpu':{'vendor':gpu ['vendor'],'model':gpu ['model'],'extensions':gpu ['ext'].split (';')},'dnt':None ,'math':{'tan':'-1.4214488238747245','sin':'0.8178819121159085','cos':'-0.5753861119575491'},'automation':{'wd':{'properties':{'document':[],'window':[],'navigator':[]}},'phantom':{'properties':{'window':[]}}},'stealth':{'t1':0 ,'t2':0 ,'i':1 ,'mte':0 ,'mtd':False },
        'crypto':{'crypto':1 ,'subtle':1 ,'encrypt':True ,'decrypt':True ,'wrapKey':True ,'unwrapKey':True ,'sign':True ,'verify':True ,'digest':True ,'deriveBits':True ,'deriveKey':True ,'getRandomValues':True ,'randomUUID':True },'canvas':{'hash':random .randrange (645172295 ,735192295 ),'emailHash':None ,'histogramBins':bins },'formDetected':False ,'numForms':0 ,'numFormElements':0 ,'be':{'si':False },'end':ts +random .randint (1 ,5 ),'errors':[],'version':self .fpVer ,'id':str (uuid .uuid4 ()),}

    def buildSignals (self ):
        fp =self .genFp ()
        payload =json .dumps (fp ,separators =(',',':')).encode ('utf-8')
        checksum =self .crc32 (payload )
        combined =checksum .encode ('ascii')+b'#'+payload 
        return checksum ,self .aesEncrypt (combined )



    @staticmethod 
    def checkDiff (digest ,diff ):
        full ,rem =divmod (diff ,8 )
        if digest [:full ]!=b'\x00'*full :return False 
        if rem and (digest [full ]>>(8 -rem )):return False 
        return True 

    def solveScrypt (self ,inp ,checksum ,diff ):
        salt =checksum .encode ('utf-8')
        for n in itertools .count (0 ):
            pwd =(inp +checksum +str (n )).encode ('utf-8')
            if self .checkDiff (hashlib .scrypt (pwd ,salt =salt ,n =128 ,r =8 ,p =1 ,dklen =16 ),diff ):return str (n )

    def solveSha256 (self ,inp ,checksum ,diff ):
        base =(inp +checksum ).encode ('utf-8')
        for n in itertools .count (0 ):
            if self .checkDiff (hashlib .sha256 (base +str (n ).encode ()).digest (),diff ):return str (n )

    def solveBandwidth (self ,inp ,checksum ,diff ):
        sizes =(self .jsCfg or {}).get ('bandwidth_sizes')or self .bwSizes 
        return base64 .b64encode (b'\x00'*sizes .get (diff ,0x400 )).decode ('utf-8')



    @staticmethod 
    def buildMetrics ():
        return [
        {'name':'2','value':random .uniform (0 ,1 ),'unit':'2'},
        {'name':'100','value':0 ,'unit':'2'},{'name':'101','value':0 ,'unit':'2'},
        {'name':'102','value':0 ,'unit':'2'},{'name':'103','value':8 ,'unit':'2'},
        {'name':'104','value':0 ,'unit':'2'},{'name':'105','value':0 ,'unit':'2'},
        {'name':'106','value':0 ,'unit':'2'},{'name':'107','value':0 ,'unit':'2'},
        {'name':'108','value':1 ,'unit':'2'},{'name':'undefined','value':0 ,'unit':'2'},
        {'name':'110','value':0 ,'unit':'2'},{'name':'111','value':2 ,'unit':'2'},
        {'name':'112','value':0 ,'unit':'2'},{'name':'undefined','value':0 ,'unit':'2'},
        {'name':'3','value':4 ,'unit':'2'},{'name':'7','value':0 ,'unit':'4'},
        {'name':'1','value':random .uniform (10 ,20 ),'unit':'2'},
        {'name':'4','value':36.5 ,'unit':'2'},
        {'name':'5','value':random .uniform (0 ,1 ),'unit':'2'},
        {'name':'6','value':random .uniform (50 ,60 ),'unit':'2'},
        {'name':'0','value':random .uniform (130 ,140 ),'unit':'2'},
        {'name':'8','value':1 ,'unit':'4'},
        ]



    @staticmethod 
    def extractGoku (html ):
        m =re .search (r'window\.gokuProps\s*=\s*(\{.*?\})\s*;',html ,re .DOTALL )
        return json .loads (m .group (1 ))if m else None 

    @staticmethod 
    def extractWafHost (html ):
        m =re .search (r'src="https://([^"]+)/challenge[^"]*\.js"',html )
        if not m :raise ValueError ('challenge.js src not found')
        return m .group (1 )

    @staticmethod 
    def extractChallengeUrl (html ):
        m =re .search (r'src="(https://[^"]+/challenge[^"]*\.js)"',html )
        return m .group (1 )if m else None 

    @staticmethod 
    def extractDomain (url ):
        h =urlparse (url ).hostname or url 
        return h if h .startswith ('www.')else 'www.'+h 



    @staticmethod 
    def parseChallengeJs (js ):
        types ={}
        for m in re .finditer (r"'(h[0-9a-f]{8,})'[+].*?=\s*'((?:mp_)?verify)'",js ):
            types [m .group (1 )]=m .group (2 )
        solField ,metaField ='solution_data','solution_metadata'
        fm =re .search (r"'verify'\s*,\s*'\w+'\s*:\s*'(solution_\w+)'\s*,\s*'\w+'\s*:\s*'(solution_\w+)'",js )
        if fm :solField ,metaField =fm .group (1 ),fm .group (2 )
        bw ={}
        sm =re .search (r'case\s+0x1:return\s+(0x[0-9a-f]+);case\s+0x2:return[^;]*\((0x[0-9a-f]+),(0x[0-9a-f]+)\);case\s+0x3:return[^;]*\((0x[0-9a-f]+),(0x[0-9a-f]+)\);case\s+0x4:return[^;]*\((0x[0-9a-f]+),(0x[0-9a-f]+)\);case\s+0x5:return[^;]*\((0x[0-9a-f]+),(0x[0-9a-f]+)\)',js )
        if sm :
            bw ={1 :int (sm .group (1 ),16 ),2 :int (sm .group (2 ),16 )*int (sm .group (3 ),16 ),3 :int (sm .group (4 ),16 )*int (sm .group (5 ),16 ),4 :int (sm .group (6 ),16 )*int (sm .group (7 ),16 ),5 :int (sm .group (8 ),16 )*int (sm .group (9 ),16 )}
        return {'challenge_types':types ,'mp_field_names':(solField ,metaField ),'bandwidth_sizes':bw }



    def getEndpoint (self ,ctype ):
        if ctype ==self .cBandwidth :return 'mp_verify'
        if self .jsCfg :
            ct =self .jsCfg ['challenge_types']
            if ctype in ct :return ct [ctype ]
            for prefix ,ep in ct .items ():
                if ctype .startswith (prefix ):return ep 
        return 'verify'

    def getSolver (self ,ctype ):
        m ={self .cScrypt :(self .solveScrypt ,'scrypt'),self .cSha256 :(self .solveSha256 ,'sha256'),self .cBandwidth :(self .solveBandwidth ,'bandwidth')}
        if ctype in m :return m [ctype ]
        if self .getEndpoint (ctype )=='mp_verify':return (self .solveBandwidth ,'bandwidth')
        raise ValueError (f'Unknown challenge type: {ctype }')



    def solveChallenge (self ):
        sess =self .makeSess ()
        parsed =urlparse (self .url )
        origin =f'{parsed .scheme }://{parsed .hostname }'
        domain =self .extractDomain (self .url )

        sess .headers =self .pageHdrs ()
        resp =sess .get (self .url )
        html =resp .text 
        wafAction =resp .headers .get ('x-amzn-waf-action')
        jsUrl =self .extractChallengeUrl (html )
        if not jsUrl :raise RuntimeError (f'No AWS WAF challenge detected on {self .url }')

        goku =self .extractGoku (html )
        wafHost =self .extractWafHost (html )

        try :
            self .jsCfg =self .parseChallengeJs (sess .get (jsUrl ).text )
        except Exception :
            self .jsCfg =None 

        sess .headers =self .apiHdrs (origin =origin ,referer =self .url )
        inputs =sess .get (f'https://{wafHost }/inputs?client=browser').json ()

        checksum ,signals =self .buildSignals ()
        ctype =inputs ['challenge_type']
        diff =inputs ['difficulty']
        chall =inputs ['challenge']
        challIn =chall .get ('input','')

        solverFn ,solverName =self .getSolver (ctype )
        solution =solverFn (challIn ,checksum ,diff )
        ep =self .getEndpoint (ctype )

        sess .headers =self .apiHdrs (origin =origin ,referer =self .url )

        if ep =='mp_verify':
            solField ,metaField =self .jsCfg ['mp_field_names']if self .jsCfg else ('solution_data','solution_metadata')
            meta =json .dumps ({'challenge':chall ,'solution':None ,'signals':[{'name':'Zoey','value':{'Present':signals }}],'checksum':checksum ,'client':'Browser','domain':domain ,'metrics':self .buildMetrics (),'existing_token':None ,**({'goku_props':goku }if goku else {})},separators =(',',':'))
            px ={'https':self .proxy ,'http':self .proxy }if self .proxy else None 
            res =stdReq .post (f'https://{wafHost }/{ep }',files ={solField :(None ,solution ),metaField :(None ,meta )},headers ={'user-agent':self .ua },proxies =px ).json ()
        else :
            pl ={'challenge':chall ,'checksum':checksum ,'solution':solution ,'signals':[{'name':'Zoey','value':{'Present':signals }}],'existing_token':None ,'client':'Browser','domain':domain ,'metrics':self .buildMetrics ()}
            sess .headers ['content-type']='text/plain;charset=UTF-8'
            res =sess .post (f'https://{wafHost }/verify',json =pl ).json ()

        if 'token'not in res :raise RuntimeError (f'No token in response: {res }')
        return res ['token'],{'solver':solverName ,'endpoint':f'/{ep }','wafAction':wafAction or 'challenge'}
