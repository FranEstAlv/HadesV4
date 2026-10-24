"""Generador del metadata1 (huella FWCIM) que Amazon pide en cada envio.

Arma una huella de navegador completa: pantalla, canvas, webgl, hardware,
interaccion con el mouse, tiempos de carga, formulario y el identificador de
sesion. Todo eso se cifra con XXTEA y se codifica en base64 con el formato
que Amazon acepta.
"""
import base64
import hashlib
import math
import json
import random
import struct
import time
import zlib

class FwcimClient :
    """Construye el metadata1 de la sesion de navegador."""
    __KEY_IDENTIFIER ='ECdITeCs'
    __KEY_MATERIAL =[1888420705 ,2576816180 ,2347232058 ,874813317 ]
    __XXTEA_DELTA =2654435769

    _COUNTRY_TZ ={
    'US':-5 ,'CA':-5 ,'MX':-6 ,'BR':-3 ,'UK':0 ,'DE':1 ,
    'FR':1 ,'IT':1 ,'ES':1 ,'NL':1 ,'SG':8 ,'AU':10 ,'JP':9 ,
    }

    _GPU_POOL =[
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 6GB Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce RTX 2060 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce RTX 2070 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (NVIDIA)','model':'ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (AMD)','model':'ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (AMD)','model':'ANGLE (AMD, AMD Radeon RX 580 2048SP (0x00006FDF) Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (AMD)','model':'ANGLE (AMD, AMD Radeon RX 5600 XT Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (AMD)','model':'ANGLE (AMD, AMD Radeon RX 5700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (AMD)','model':'ANGLE (AMD, AMD Radeon RX 6600 XT Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (AMD)','model':'ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (Intel)','model':'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (Intel)','model':'ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Google Inc. (Intel)','model':'ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    {'vendor':'Apple','model':'Apple M1'},
    {'vendor':'Apple','model':'Apple M1 Pro'},
    {'vendor':'Apple','model':'Apple M2'},
    {'vendor':'Apple','model':'Apple M3'},
    ]
    _MX_GPU_VENDOR ='Google Inc. (AMD)'
    _MX_GPU_MODEL ='ANGLE (AMD, AMD Radeon RX 580 2048SP (0x00006FDF) Direct3D11 vs_5_0 ps_5_0, D3D11)'
    _MX_SCREEN_INFO ='1920-1080-1032-24-*-*-*'
    _MX_PLUGIN_BASE ='PDF Viewer Chrome PDF Viewer Chromium PDF Viewer Microsoft Edge PDF Viewer WebKit built-in PDF'
    _MX_EXTENSIONS =[
    'ANGLE_instanced_arrays','EXT_blend_minmax','EXT_clip_control',
    'EXT_color_buffer_half_float','EXT_depth_clamp','EXT_disjoint_timer_query',
    'EXT_float_blend','EXT_frag_depth','EXT_polygon_offset_clamp',
    'EXT_shader_texture_lod','EXT_texture_compression_bptc',
    'EXT_texture_compression_rgtc','EXT_texture_filter_anisotropic',
    'EXT_texture_mirror_clamp_to_edge','EXT_sRGB','KHR_parallel_shader_compile',
    'OES_element_index_uint','OES_fbo_render_mipmap','OES_standard_derivatives',
    'OES_texture_float','OES_texture_float_linear','OES_texture_half_float',
    'OES_texture_half_float_linear','OES_vertex_array_object',
    'WEBGL_blend_func_extended','WEBGL_color_buffer_float',
    'WEBGL_compressed_texture_s3tc','WEBGL_compressed_texture_s3tc_srgb',
    'WEBGL_debug_renderer_info','WEBGL_debug_shaders','WEBGL_depth_texture',
    'WEBGL_draw_buffers','WEBGL_lose_context','WEBGL_multi_draw','WEBGL_polygon_mode',
    ]
    _MX_CANVAS_HASH =-1036388059
    _MX_CANVAS_HISTOGRAM =[
    14801 ,19 ,28 ,24 ,31 ,59 ,22 ,29 ,33 ,29 ,40 ,41 ,30 ,71 ,27 ,35 ,43 ,12 ,60 ,25 ,21 ,15 ,16 ,12 ,39 ,50 ,42 ,33 ,
    16 ,19 ,29 ,40 ,20 ,58 ,19 ,12 ,22 ,34 ,21 ,27 ,49 ,14 ,10 ,8 ,31 ,19 ,17 ,31 ,32 ,38 ,32 ,35 ,69 ,14 ,22 ,19 ,17 ,
    16 ,10 ,18 ,49 ,52 ,11 ,18 ,143 ,16 ,28 ,6 ,7 ,9 ,4 ,15 ,11 ,15 ,34 ,12 ,11 ,16 ,41 ,72 ,15 ,15 ,19 ,6 ,27 ,9 ,10 ,
    12 ,23 ,51 ,23 ,27 ,16 ,15 ,11 ,14 ,27 ,16 ,22 ,100 ,77 ,25 ,542 ,24 ,20 ,34 ,34 ,13 ,13 ,17 ,9 ,27 ,18 ,13 ,19 ,16 ,
    12 ,29 ,22 ,19 ,66 ,35 ,23 ,13 ,26 ,20 ,28 ,11 ,179 ,53 ,19 ,50 ,14 ,13 ,12 ,31 ,33 ,32 ,65 ,8 ,22 ,13 ,8 ,9 ,12 ,7 ,
    28 ,16 ,9 ,9 ,60 ,20 ,5 ,91 ,13 ,54 ,27 ,20 ,11 ,27 ,6 ,11 ,20 ,18 ,53 ,31 ,9 ,12 ,10 ,6 ,7 ,24 ,10 ,37 ,9 ,6 ,78 ,76 ,
    5 ,11 ,13 ,45 ,6 ,19 ,9 ,4 ,14 ,11 ,8 ,55 ,13 ,120 ,6 ,14 ,56 ,21 ,13 ,7 ,56 ,10 ,18 ,54 ,35 ,53 ,64 ,65 ,88 ,9 ,14 ,
    14 ,4 ,15 ,24 ,11 ,26 ,43 ,14 ,24 ,26 ,15 ,22 ,17 ,61 ,71 ,30 ,57 ,17 ,32 ,72 ,23 ,25 ,30 ,12 ,39 ,19 ,34 ,33 ,13 ,
    19 ,63 ,87 ,40 ,87 ,28 ,60 ,36 ,25 ,77 ,37 ,21 ,65 ,55 ,40 ,35 ,24 ,13331 ,
    ]
    _MX_MATH ={
    'tan':'-1.4214488238747245',
    'sin':'0.8178819121159085',
    'cos':'-0.5753861119575491',
    }

    _CVF_CAPTCHA_METRICS ={
    'el':0 ,'script':0 ,'h':0 ,'batt':0 ,'perf':0 ,'auto':0 ,
    'tz':0 ,'fp2':0 ,'lsubid':0 ,'browser':0 ,'capabilities':0 ,
    'gpu':0 ,'dnt':0 ,'math':0 ,'tts':0 ,'input':0 ,
    'canvas':0 ,'captchainput':0 ,'pow':0 ,
    }
    _CVF_INTERACTION ={
    'clicks':4 ,
    'touches':0 ,
    'keyPresses':7 ,
    'cuts':0 ,
    'copies':0 ,
    'pastes':2 ,
    'keyPressTimeIntervals':[96 ,32256 ,89 ,175 ],
    'mouseClickPositions':['571,248','367,259','477,255','493,334'],
    'keyCycles':[344 ,112 ,440 ,71 ,112 ],
    'mouseCycles':[99 ,75 ,96 ,90 ],
    'touchCycles':[],
    }
    _CVF_FORM_FIELD ={
    'clicks':2 ,
    'touches':0 ,
    'keyPresses':7 ,
    'cuts':0 ,
    'copies':0 ,
    'pastes':2 ,
    'keyPressTimeIntervals':[96 ,32256 ,88 ,176 ],
    'mouseClickPositions':['186,6','92,13'],
    'keyCycles':[344 ,111 ,440 ,72 ,111 ],
    'mouseCycles':[99 ,96 ],
    'touchCycles':[],
    'width':324 ,
    'height':32 ,
    'totalFocusTime':3009 ,
    'checksum':'BDD57CC5',
    'autocomplete':False ,
    'prefilled':False ,
    }
    _CVF_TIME_TO_SUBMIT =37245

    _SCREEN_POOL =[
    {'w':1920 ,'h':1080 ,'ah':1040 ,'cd':24 ,'dpr':1.0 },
    {'w':1920 ,'h':1080 ,'ah':1032 ,'cd':24 ,'dpr':1.0 },
    {'w':1920 ,'h':1080 ,'ah':1080 ,'cd':24 ,'dpr':1.0 },
    {'w':1920 ,'h':1200 ,'ah':1160 ,'cd':24 ,'dpr':1.0 },
    {'w':2560 ,'h':1440 ,'ah':1400 ,'cd':24 ,'dpr':1.0 },
    {'w':2560 ,'h':1440 ,'ah':1440 ,'cd':24 ,'dpr':1.25 },
    {'w':1366 ,'h':768 ,'ah':728 ,'cd':24 ,'dpr':1.0 },
    {'w':1536 ,'h':864 ,'ah':824 ,'cd':24 ,'dpr':1.25 },
    {'w':1440 ,'h':900 ,'ah':900 ,'cd':24 ,'dpr':2.0 },
    {'w':1680 ,'h':1050 ,'ah':1010 ,'cd':24 ,'dpr':1.0 },
    {'w':2560 ,'h':1600 ,'ah':1560 ,'cd':30 ,'dpr':2.0 },
    {'w':3840 ,'h':2160 ,'ah':2120 ,'cd':24 ,'dpr':1.5 },
    {'w':1280 ,'h':720 ,'ah':680 ,'cd':24 ,'dpr':1.0 },
    ]

    _VERSIONS =['4.0.0']

    _CHROME_WIN_EXTENSIONS =[
    'ANGLE_instanced_arrays','EXT_blend_minmax','EXT_clip_control',
    'EXT_color_buffer_half_float','EXT_depth_clamp','EXT_disjoint_timer_query',
    'EXT_float_blend','EXT_frag_depth','EXT_polygon_offset_clamp',
    'EXT_shader_texture_lod','EXT_texture_compression_bptc',
    'EXT_texture_compression_rgtc','EXT_texture_filter_anisotropic',
    'EXT_texture_mirror_clamp_to_edge','EXT_sRGB','KHR_parallel_shader_compile',
    'OES_element_index_uint','OES_fbo_render_mipmap','OES_standard_derivatives',
    'OES_texture_float','OES_texture_float_linear','OES_texture_half_float',
    'OES_texture_half_float_linear','OES_vertex_array_object',
    'WEBGL_blend_func_extended','WEBGL_color_buffer_float',
    'WEBGL_compressed_texture_s3tc','WEBGL_compressed_texture_s3tc_srgb',
    'WEBGL_debug_renderer_info','WEBGL_debug_shaders','WEBGL_depth_texture',
    'WEBGL_draw_buffers','WEBGL_lose_context','WEBGL_multi_draw',
    'WEBGL_polygon_mode',
    ]
    _CHROME_MAC_EXTENSIONS =[
    'ANGLE_instanced_arrays','EXT_blend_minmax','EXT_clip_control',
    'EXT_color_buffer_half_float','EXT_depth_clamp','EXT_disjoint_timer_query',
    'EXT_float_blend','EXT_frag_depth','EXT_polygon_offset_clamp',
    'EXT_shader_texture_lod','EXT_texture_compression_bptc',
    'EXT_texture_compression_rgtc','EXT_texture_filter_anisotropic',
    'EXT_texture_mirror_clamp_to_edge','EXT_sRGB','KHR_parallel_shader_compile',
    'OES_element_index_uint','OES_fbo_render_mipmap','OES_standard_derivatives',
    'OES_texture_float','OES_texture_float_linear','OES_texture_half_float',
    'OES_texture_half_float_linear','OES_vertex_array_object',
    'WEBGL_blend_func_extended','WEBGL_color_buffer_float',
    'WEBGL_compressed_texture_astc','WEBGL_compressed_texture_etc',
    'WEBGL_compressed_texture_pvrtc','WEBGL_compressed_texture_s3tc',
    'WEBGL_compressed_texture_s3tc_srgb','WEBGL_debug_renderer_info',
    'WEBGL_debug_shaders','WEBGL_depth_texture','WEBGL_draw_buffers',
    'WEBGL_lose_context','WEBGL_multi_draw',
    ]
    __PROFILES ={
    'chrome_win':{
    'gpuExtensions':_CHROME_WIN_EXTENSIONS ,
    'css':{'textShadow':1 ,'WebkitTextStroke':1 ,'boxShadow':1 ,'borderRadius':1 ,'borderImage':1 ,'opacity':1 ,'transform':1 ,'transition':1 },
    'js':{'audio':True ,'geolocation':True ,'localStorage':'supported','touch':False ,'video':True ,'webWorker':True },
    'pluginBase':'PDF Viewer Chrome PDF Viewer Chromium PDF Viewer Microsoft Edge PDF Viewer WebKit built-in PDF',
    },
    'chrome_mac':{
    'gpuExtensions':_CHROME_MAC_EXTENSIONS ,
    'css':{'textShadow':1 ,'WebkitTextStroke':1 ,'boxShadow':1 ,'borderRadius':1 ,'borderImage':1 ,'opacity':1 ,'transform':1 ,'transition':1 },
    'js':{'audio':True ,'geolocation':True ,'localStorage':'supported','touch':False ,'video':True ,'webWorker':True },
    'pluginBase':'PDF Viewer Chrome PDF Viewer Chromium PDF Viewer Microsoft Edge PDF Viewer WebKit built-in PDF',
    },
    'firefox_linux':{
    'gpuExtensions':[
    'ANGLE_instanced_arrays','EXT_blend_minmax','EXT_color_buffer_half_float',
    'EXT_depth_clamp','EXT_float_blend','EXT_frag_depth','EXT_shader_texture_lod',
    'EXT_sRGB','EXT_texture_compression_bptc','EXT_texture_compression_rgtc',
    'EXT_texture_filter_anisotropic','OES_element_index_uint','OES_fbo_render_mipmap',
    'OES_standard_derivatives','OES_texture_float','OES_texture_float_linear',
    'OES_texture_half_float','OES_texture_half_float_linear','OES_vertex_array_object',
    'WEBGL_color_buffer_float','WEBGL_compressed_texture_astc',
    'WEBGL_compressed_texture_etc','WEBGL_compressed_texture_s3tc',
    'WEBGL_compressed_texture_s3tc_srgb','WEBGL_debug_renderer_info',
    'WEBGL_debug_shaders','WEBGL_depth_texture','WEBGL_draw_buffers','WEBGL_lose_context',
    ],
    'css':{'textShadow':1 ,'WebkitTextStroke':1 ,'boxShadow':1 ,'borderRadius':1 ,'borderImage':1 ,'opacity':1 ,'transform':1 ,'transition':1 },
    'js':{'audio':True ,'geolocation':True ,'localStorage':'supported','touch':False ,'video':True ,'webWorker':True },
    'pluginBase':'PDF Viewer Chrome PDF Viewer Chromium PDF Viewer Microsoft Edge PDF Viewer WebKit built-in PDF',
    },
    'firefox_win':{
    'gpuExtensions':[
    'ANGLE_instanced_arrays','EXT_blend_minmax','EXT_color_buffer_half_float',
    'EXT_depth_clamp','EXT_float_blend','EXT_frag_depth','EXT_shader_texture_lod',
    'EXT_sRGB','EXT_texture_compression_bptc','EXT_texture_compression_rgtc',
    'EXT_texture_filter_anisotropic','OES_element_index_uint','OES_fbo_render_mipmap',
    'OES_standard_derivatives','OES_texture_float','OES_texture_float_linear',
    'OES_texture_half_float','OES_texture_half_float_linear','OES_vertex_array_object',
    'WEBGL_color_buffer_float','WEBGL_compressed_texture_s3tc',
    'WEBGL_compressed_texture_s3tc_srgb','WEBGL_debug_renderer_info',
    'WEBGL_debug_shaders','WEBGL_depth_texture','WEBGL_draw_buffers','WEBGL_lose_context',
    ],
    'css':{'textShadow':1 ,'WebkitTextStroke':1 ,'boxShadow':1 ,'borderRadius':1 ,'borderImage':1 ,'opacity':1 ,'transform':1 ,'transition':1 },
    'js':{'audio':True ,'geolocation':True ,'localStorage':'supported','touch':False ,'video':True ,'webWorker':True },
    'pluginBase':'PDF Viewer Chrome PDF Viewer',
    },
    'safari_mac':{
    'gpuExtensions':[
    'WEBGL_compressed_texture_s3tc','WEBGL_debug_renderer_info','OES_texture_float',
    'WEBGL_lose_context','WEBGL_depth_texture','EXT_color_buffer_half_float',
    'WEBGL_color_buffer_float','OES_standard_derivatives','OES_element_index_uint',
    'OES_texture_float_linear','OES_texture_half_float','OES_texture_half_float_linear',
    'WEBGL_compressed_texture_s3tc_srgb','WEBGL_debug_shaders',
    ],
    'css':{'textShadow':1 ,'WebkitTextStroke':1 ,'boxShadow':1 ,'borderRadius':1 ,'borderImage':1 ,'opacity':1 ,'transform':1 ,'transition':1 },
    'js':{'audio':True ,'geolocation':True ,'localStorage':'supported','touch':False ,'video':True ,'webWorker':True },
    'pluginBase':'PDF Viewer',
    },
    }
    _session_profile =None
    _session_timezone =None
    _session_lsubid =None
    _country =None

    def __init__ (self ,location :str ,userAgent :str ,referrer :str ='',dynamicUrls :list =None ,inlineHashes :list =None ,emailValue :str ='',customerName :str ='',passwordValue :str ='',otpValue :str ='',timezone :int =None ,postCaptcha :bool =False ,country :str =None )->None :
        self .__location =location
        self .__userAgent =userAgent or 'Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0'
        self .__referrer =referrer or ''
        self .__dynamicUrls =dynamicUrls or []
        self .__inlineHashes =inlineHashes or []
        self .__emailValue =emailValue
        self .__customerName =customerName
        self .__passwordValue =passwordValue
        self .__otpValue =otpValue
        self .__postCaptcha =postCaptcha
        self .__country =(country or '').upper ()
        if timezone is None and country :
            timezone =self ._COUNTRY_TZ .get (country .upper ())
        if timezone is None and FwcimClient ._session_timezone is not None :
            timezone =FwcimClient ._session_timezone
        self .__timezone =timezone if timezone is not None else -int (time .timezone /3600 )
        self .__profile =self .__detectProfile (self .__userAgent )
        FwcimClient ._session_profile =self .__profile

    @classmethod
    def reset_session (cls ):
        """Borra la sesion de huella: cada cuenta usa una nueva."""
        cls ._session_profile =None
        cls ._session_timezone =None
        cls ._session_lsubid =None

    @classmethod
    def set_session_timezone (cls ,timezone :int )->None :
        """Fija la zona horaria de la huella."""
        cls ._session_timezone =timezone

    def generateMetadata (self )->dict :
        """Genera el metadata1 que Amazon pide en cada envio."""
        try :
            fingerprint =self .__buildFingerprint ()
            md =self .__encodeMetadata1 (fingerprint )
            return {'status':True ,'context':'Amazon FWCIM Fingerprint Generator','metadata1':md }
        except Exception as error :
            return {'status':False ,'description':str (error )}

    def __buildFingerprint (self )->dict :
        """Arma la huella del navegador (pantalla, canvas, webgl, hardware)."""
        now =int (time .time ()*1000 )
        p =self .__profile
        form =self .__buildForm ()
        values =(self .__customerName ,self .__emailValue ,self .__passwordValue ,self .__passwordValue ,self .__otpValue )
        totalKeys =sum ((len (str (v ))for v in values if v ))
        totalClicks =sum ((1 for v in values if v ))
        startOffset =random .randint (3000 ,6000 )
        start_ts =now -startOffset
        interaction =self .__buildInteraction (totalKeys ,totalClicks ,now ,form )


        if FwcimClient ._session_lsubid is None :
            FwcimClient ._session_lsubid =self .__generateLsUbid ()
        lsubid =FwcimClient ._session_lsubid


        if self .__otpValue :
            history_length =random .randint (8 ,12 )
        elif self .__customerName :
            history_length =random .randint (5 ,8 )
        else :
            history_length =random .randint (4 ,8 )

        timing =self .__buildPerformanceTiming (start_ts ,now )

        fp ={
        'metrics':self .__buildMetrics (),
        'start':start_ts ,
        'interaction':interaction ,
        'scripts':{
        'dynamicUrls':list (self .__dynamicUrls ),
        'inlineHashes':list (self .__inlineHashes ),
        'elapsed':random .randint (1 ,3 ),
        'dynamicUrlCount':len (self .__dynamicUrls ),
        'inlineHashesCount':len (self .__inlineHashes ),
        },
        'history':{'length':history_length },
        'battery':{},
        'performance':{'timing':timing },
        'automation':{
        'wd':{'properties':{'document':[],'window':[],'navigator':[]}},
        'phantom':{'properties':{'window':[]}},
        },
        'end':now ,
        'timeZone':self .__timezone ,
        'flashVersion':None ,
        'plugins':p ['plugins'],
        'dupedPlugins':p ['plugins'],
        'screenInfo':p ['screenInfo'],
        'lsUbid':lsubid ,
        'referrer':self .__referrer ,
        'userAgent':self .__userAgent ,
        'location':self .__location ,
        'webDriver':False ,
        'capabilities':{'css':p ['css'],'js':p ['js'],'elapsed':random .randint (0 ,1 )},
        'gpu':{'vendor':p ['gpuVendor'],'model':p ['gpuModel'],'extensions':p ['gpuExtensions']},
        'dnt':None ,
        'math':self ._MX_MATH if self .__country =='MX'else {'tan':str (math .tan (1 )),'sin':str (math .sin (1 )),'cos':str (math .cos (1 ))},
        'form':form ,
        'canvas':self .__buildCanvas (),
        'token':{'isCompatible':True ,'pageHasCaptcha':1 if self .__postCaptcha else 0 },
        'auth':{'form':{'method':'post'}},
        'errors':[],
        'version':'4.0.0',
        }


        if self .__postCaptcha or '/cvf/'in self .__location :
            fp ['timeToSubmit']=self ._CVF_TIME_TO_SUBMIT +random .randint (-2000 ,2000 )
        elif self .__emailValue and not self .__customerName and not self .__otpValue :
            fp ['timeToSubmit']=now -start_ts +random .randint (2000 ,8000 )
        return fp

    def __detectProfile (self ,userAgent :str )->dict :
        """Detecta el perfil de hardware del navegador emulado."""
        ua =userAgent .lower ()
        if 'chrome'in ua and 'edg'not in ua :
            base =self .__PROFILES ['chrome_mac']if 'mac'in ua or 'darwin'in ua else self .__PROFILES ['chrome_win']
        elif 'firefox'in ua :
            base =self .__PROFILES ['firefox_linux']if 'linux'in ua or 'x11'in ua else self .__PROFILES ['firefox_win']
        elif 'safari'in ua and 'chrome'not in ua :
            base =self .__PROFILES ['safari_mac']
        else :
            base =self .__PROFILES ['firefox_linux']


        gpu =random .choice (self ._GPU_POOL )
        scr =random .choice (self ._SCREEN_POOL )
        screenInfo =f'{scr ["w"]}-{scr ["h"]}-{scr ["ah"]}-{scr ["cd"]}-*-*-*'
        plugins =f'{base ["pluginBase"]} ||{screenInfo }'

        return {
        'gpuVendor':gpu ['vendor'],
        'gpuModel':gpu ['model'],
        'gpuExtensions':base ['gpuExtensions'],
        'css':base ['css'],
        'js':base ['js'],
        'plugins':plugins ,
        'screenInfo':screenInfo ,
        'screen':{'width':scr ['w'],'height':scr ['h'],'availHeight':scr ['ah']},
        'dpr':scr ['dpr'],
        'deviceMemory':random .choice ([4 ,8 ,8 ,16 ,16 ,32 ]),
        }

    def __buildCanvas (self )->dict :
        """Dibuja el canvas que Amazon usa para identificar el navegador."""

        try :
            from PIL import Image ,ImageDraw ,ImageFont
            cw =280 +random .randint (-15 ,15 )
            ch =60 +random .randint (-8 ,8 )
            bg_r ,bg_g ,bg_b =255 +random .randint (-3 ,0 ),255 +random .randint (-3 ,0 ),255 +random .randint (-3 ,0 )
            image =Image .new ('RGB',(cw ,ch ),(max (250 ,bg_r ),max (250 ,bg_g ),max (250 ,bg_b )))
            draw =ImageDraw .Draw (image )
            try :
                fontSmall =ImageFont .truetype ('arial.ttf',random .choice ([13 ,14 ,15 ]))
                fontLarge =ImageFont .truetype ('arial.ttf',random .choice ([17 ,18 ,19 ]))
            except (OSError ,IOError ):
                try :
                    fontSmall =ImageFont .truetype ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',random .choice ([13 ,14 ,15 ]))
                    fontLarge =ImageFont .truetype ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',random .choice ([17 ,18 ,19 ]))
                except (OSError ,IOError ):
                    fontSmall =ImageFont .load_default ()
                    fontLarge =ImageFont .load_default ()
            jx ,jy =random .randint (-3 ,3 ),random .randint (-3 ,3 )
            texts =['Cwm fjordbank glyphs vext quiz','Cwm fjordank glyphs vext quiz','The quick brown fox jumps']
            draw .text ((2 +jx ,2 +jy ),random .choice (texts ),fill =f'#{random .randint (0x003060 ,0x0070ff ):06x}',font =fontSmall )
            draw .text ((2 +jx +random .randint (-1 ,1 ),20 +jy ),'mmmmmmmmmmlli',fill =f'#{random .randint (0x003060 ,0x0070ff ):06x}',font =fontLarge )
            draw .text ((2 +jx ,42 +jy +random .randint (-2 ,2 )),random .choice (['W','M','Q']),fill =f'#{random .randint (0x003060 ,0x0070ff ):06x}',font =fontSmall )
            rc =lambda :random .randint (20 ,220 )
            for _ in range (random .randint (2 ,4 )):
                c =(rc (),rc (),rc (),random .randint (80 ,200 ))
                x1 ,y1 =random .randint (0 ,10 ),random .randint (0 ,10 )
                draw .rectangle ([x1 ,y1 ,x1 +random .randint (4 ,9 ),y1 +random .randint (4 ,9 )],fill =c )
            for _ in range (random .randint (1 ,3 )):
                c =f'#{random .randint (0x200000 ,0x999999 ):06x}'
                x1 ,y1 =random .randint (0 ,15 ),random .randint (0 ,15 )
                draw .ellipse ([x1 ,y1 ,x1 +random .randint (5 ,12 ),y1 +random .randint (5 ,12 )],fill =c )
            colors =[f'#{random .randint (0x200000 ,0x999999 ):06x}'for _ in range (random .randint (2 ,5 ))]
            for i ,color in enumerate (colors ):
                o =i *random .randint (1 ,3 )
                draw .arc ([20 +o ,0 +o ,50 +o ,30 +o ],0 ,360 ,fill =color ,width =random .randint (1 ,3 ))
            noise_range_x =random .randint (80 ,120 )
            noise_y_start =random .randint (8 ,15 )
            noise_y_end =random .randint (40 ,55 )
            for x in range (noise_range_x ):
                for y in range (noise_y_start ,noise_y_end ):
                    r =min (255 ,x *2 +random .randint (-12 ,12 ))
                    g =min (255 ,y *3 +random .randint (-12 ,12 ))
                    b =128 +random .randint (-30 ,30 )
                    draw .point ((x ,y ),fill =(max (0 ,r ),max (0 ,g ),max (0 ,min (255 ,b ))))
            pixelBytes =b''.join ((struct .pack ('BBB',*px )for px in image .getdata ()))
            canvasHash =zlib .crc32 (pixelBytes )&4294967295
            if canvasHash >=2147483648 :
                canvasHash -=4294967296
            histogramBins =[int (0.299 *px [0 ]+0.587 *px [1 ]+0.114 *px [2 ])for px in image .getdata ()]


            return {'hash':canvasHash ,'emailHash':None ,'histogramBins':histogramBins }
        except ImportError :
            fallback_seed =f'canvas-fp-{random .randint (1000 ,9999 )}'.encode ()
            fallback_hash =struct .unpack ('<i',hashlib .md5 (fallback_seed ).digest ()[0 :4 ])[0 ]
            return {'hash':fallback_hash ,'emailHash':None ,'histogramBins':[random .randint (10 ,80 )for _ in range (256 )]}

    def __buildMetrics (self )->dict :
        """Metricas de rendimiento de la huella."""

        if self .__postCaptcha or '/cvf/'in self .__location :
            return dict (self ._CVF_CAPTCHA_METRICS )

        fields =('el','script','h','batt','perf','auto','tz','fp2','lsubid','browser',
        'capabilities','gpu','dnt','math','tts','input','canvas','captchainput','pow')
        metrics ={key :0 for key in fields }

        if self .__emailValue and not self .__customerName and not self .__otpValue :
            metrics ['h']=1
        return metrics

    def __buildInteraction (self ,totalKeys :int ,totalClicks :int ,now :int ,form :dict =None )->dict :
        """Simula la interaccion del usuario (movimiento del mouse, teclas)."""

        if self .__postCaptcha or '/cvf/'in self .__location :
            if form and 'cvf-input-code'in form :
                ff =form ['cvf-input-code']
                return {
                'clicks':ff .get ('clicks',1 ),
                'touches':0 ,
                'keyPresses':ff .get ('keyPresses',6 ),
                'cuts':0 ,'copies':0 ,'pastes':ff .get ('pastes',0 ),
                'keyPressTimeIntervals':list (ff .get ('keyPressTimeIntervals',[])),
                'mouseClickPositions':[f'{random .randint (400 ,800 )},{random .randint (150 ,300 )}'for _ in range (ff .get ('clicks',1 ))],
                'keyCycles':list (ff .get ('keyCycles',[])),
                'mouseCycles':[random .randint (60 ,95 )for _ in range (ff .get ('clicks',1 ))],
                'touchCycles':[],
                }
            inter =dict (self ._CVF_INTERACTION )
            inter ['keyPressTimeIntervals']=[
            v +random .randint (-5 ,5 )for v in self ._CVF_INTERACTION ['keyPressTimeIntervals']
            ]
            inter ['keyCycles']=[
            v +random .randint (-3 ,3 )for v in self ._CVF_INTERACTION ['keyCycles']
            ]
            inter ['mouseCycles']=[
            v +random .randint (-3 ,3 )for v in self ._CVF_INTERACTION ['mouseCycles']
            ]
            return inter

        BURST_SIZE =random .randint (8 ,12 )
        lastFieldKeys =min (totalKeys ,BURST_SIZE )
        cursor =now -random .randint (1500 ,5000 )
        keyEvents =[]
        for i in range (lastFieldKeys ):
            cursor +=self .__humanKeyInterval (i ,lastFieldKeys )
            dur =random .randint (35 ,130 )
            keyEvents .append ({'start':cursor ,'end':cursor +dur })
        keyPressTimeIntervals =[keyEvents [i ]['start']-keyEvents [i -1 ]['start']for i in range (1 ,len (keyEvents ))]

        keyCycles =[e ['end']-e ['start']for e in keyEvents ]
        mouseEvents =[]
        clickTime =now -random .randint (2000 ,6000 )
        for i in range (totalClicks ):
            x ,y =self .__humanMousePosition (i )
            dur =random .randint (25 ,95 )
            mouseEvents .append ({'start':clickTime ,'end':clickTime +dur ,'x':x ,'y':y })
            clickTime +=random .randint (600 ,1500 )if random .random ()<0.15 else random .randint (120 ,550 )
        mouseCycles =[e ['end']-e ['start']for e in mouseEvents ]
        mouseClickPositions =[f"{e ['x']},{e ['y']}"for e in mouseEvents ]
        pastes =1 if random .random ()<0.12 else 0
        return {'clicks':totalClicks ,'touches':0 ,'keyPresses':totalKeys ,'cuts':0 ,'copies':0 ,
        'pastes':pastes ,'keyPressTimeIntervals':keyPressTimeIntervals ,
        'mouseClickPositions':mouseClickPositions ,'keyCycles':keyCycles ,
        'mouseCycles':mouseCycles ,'touchCycles':[]}

    @staticmethod
    def __humanKeyInterval (index :int ,total :int )->int :
        """Intervalo entre pulsaciones de teclado, en milisegundos."""
        if index ==0 :
            return random .randint (180 ,550 )
        if index ==total -1 :
            return random .randint (130 ,420 )
        if random .random ()<0.10 :
            return random .randint (350 ,1400 )
        return random .randint (50 ,200 )

    @staticmethod
    def __humanMousePosition (formIndex :int )->tuple :
        """Posicion del mouse con movimiento no lineal."""
        x =random .randint (120 ,450 )+random .randint (-8 ,8 )
        y =40 +formIndex *random .randint (38 ,55 )+random .randint (-12 ,12 )
        return (x ,y )

    def __buildForm (self )->dict :
        """Arma los datos del formulario de la huella."""
        form ={}
        if self .__customerName :


            form ['ap_customer_name']=self .__buildFormFieldName (self .__customerName ,random .randint (290 ,340 ),random .randint (28 ,36 ))

            if self .__passwordValue :
                form ['password']=self .__buildFormField (self .__passwordValue ,random .randint (290 ,340 ),random .randint (28 ,36 ))
                form ['ap_password_check']=self .__buildFormFieldPasswordCheck (self .__passwordValue ,random .randint (290 ,340 ),random .randint (28 ,36 ))
        else :


            if self .__emailValue :
                form ['ap_email_login']=self .__buildFormField (self .__emailValue ,random .randint (290 ,340 ),random .randint (28 ,36 ))
            if self .__passwordValue :
                form ['password']=self .__buildFormField (self .__passwordValue ,random .randint (290 ,340 ),random .randint (28 ,36 ))
                form ['ap_password_check']=self .__buildFormField (self .__passwordValue ,random .randint (290 ,340 ),random .randint (28 ,36 ))

            form ['auth-credential-autofill-hint']=self .__buildFormField ('',0 ,0 )
        if self .__otpValue :
            if self .__postCaptcha or '/cvf/'in self .__location :

                field =dict (self ._CVF_FORM_FIELD )
                field ['keyPressTimeIntervals']=[
                v +random .randint (-4 ,4 )for v in self ._CVF_FORM_FIELD ['keyPressTimeIntervals']
                ]
                field ['keyCycles']=[
                v +random .randint (-3 ,3 )for v in self ._CVF_FORM_FIELD ['keyCycles']
                ]
                field ['mouseCycles']=[
                v +random .randint (-3 ,3 )for v in self ._CVF_FORM_FIELD ['mouseCycles']
                ]

                import zlib as _zlib
                field ['checksum']=format (_zlib .crc32 (str (self .__otpValue ).encode ('utf-8'))&4294967295 ,'08X')
                form ['cvf-input-code']=field
            else :
                form ['cvf-input-code']=self .__buildFormField (self .__otpValue ,random .randint (290 ,340 ),random .randint (28 ,36 ))
        return form

    def __buildFormField (self ,value ,width :int ,height :int )->dict :
        """Arma un campo del formulario de la huella."""
        keyCount =len (str (value ))if value else 0
        if keyCount ==0 :
            return {'clicks':0 ,'touches':0 ,'keyPresses':0 ,'cuts':0 ,'copies':0 ,
            'pastes':0 ,'keyPressTimeIntervals':[],'mouseClickPositions':[],
            'keyCycles':[],'mouseCycles':[],'touchCycles':[],
            'width':width ,'height':height ,'totalFocusTime':0 ,
            'autocomplete':False ,'prefilled':False }
        keyIntervals ,totalFocus =([],0 )
        for i in range (keyCount ):
            if i ==0 :
                interval =random .randint (130 ,450 )
            elif random .random ()<0.07 :
                interval =random .randint (450 ,1600 )
            else :
                interval =random .randint (45 ,190 )
            keyIntervals .append (interval )
            totalFocus +=interval
        keyCycles =[random .randint (35 ,120 )for _ in range (keyCount )]
        mouseX =random .randint (60 ,320 )
        mouseY =random .randint (3 ,28 )
        mouseCycles =[random .randint (25 ,85 )]
        checksum =format (zlib .crc32 (str (value ).encode ('utf-8'))&4294967295 ,'08X')
        return {'clicks':1 ,'touches':0 ,'keyPresses':keyCount ,'cuts':0 ,'copies':0 ,
        'pastes':0 ,'keyPressTimeIntervals':keyIntervals ,
        'mouseClickPositions':[f'{mouseX },{mouseY }'],'keyCycles':keyCycles ,
        'mouseCycles':mouseCycles ,'touchCycles':[],'width':width ,'height':height ,
        'totalFocusTime':totalFocus +random .randint (150 ,1800 ),
        'checksum':checksum ,'autocomplete':False ,'prefilled':False }

    def __buildFormFieldName (self ,value ,width :int ,height :int )->dict :
        """Campo nombre en register: clicks=1, con mouseClickPositions.
        Sin checksum ni autocomplete (captura real no los incluye)."""
        keyCount =len (str (value ))if value else 0
        keyIntervals =[]
        totalFocus =0
        for i in range (keyCount ):
            if i ==0 :
                interval =random .randint (400 ,800 )
            elif random .random ()<0.08 :
                interval =random .randint (300 ,600 )
            else :
                interval =random .randint (60 ,180 )
            keyIntervals .append (interval )
            totalFocus +=interval
        keyCycles =[random .randint (60 ,260 )for _ in range (min (keyCount ,10 ))]
        mouseX =random .randint (50 ,200 )
        mouseY =random .randint (15 ,35 )
        return {'clicks':1 ,'touches':0 ,'keyPresses':keyCount ,'cuts':0 ,'copies':0 ,
        'pastes':0 ,'keyPressTimeIntervals':keyIntervals [:-1 ]if len (keyIntervals )>9 else keyIntervals ,
        'mouseClickPositions':[f'{mouseX },{mouseY }'],
        'keyCycles':keyCycles ,'mouseCycles':[random .randint (70 ,95 )],
        'touchCycles':[],
        'width':width ,'height':height ,
        'totalFocusTime':totalFocus +random .randint (200 ,1200 ),
        'prefilled':False }

    def __buildFormFieldPasswordCheck (self ,value ,width :int ,height :int )->dict :
        """ap_password_check: clicks=1, con mouseClickPositions. totalFocusTime=0."""
        keyCount =len (str (value ))if value else 0
        keyIntervals ,totalFocus =([],0 )
        for i in range (keyCount ):
            if i ==0 :
                interval =random .randint (80 ,200 )
            elif random .random ()<0.10 :
                interval =random .randint (180 ,300 )
            else :
                interval =random .randint (60 ,250 )
            keyIntervals .append (interval )
            totalFocus +=interval

        keyCycles =[random .randint (65 ,130 )for _ in range (keyCount )]
        mouseX =random .randint (100 ,220 )
        mouseY =random .randint (10 ,30 )
        return {'clicks':1 ,'touches':0 ,'keyPresses':keyCount ,'cuts':0 ,'copies':0 ,
        'pastes':0 ,'keyPressTimeIntervals':keyIntervals [:-1 ],
        'mouseClickPositions':[f'{mouseX },{mouseY }'],'keyCycles':keyCycles ,
        'mouseCycles':[random .randint (55 ,80 )],'touchCycles':[],
        'width':width ,'height':height ,
        'totalFocusTime':0 ,
        'prefilled':False }

    @staticmethod
    def __generateLsUbid ()->str :
        """Genera el identificador de sesion del navegador."""
        prefix =f'X{random .randint (0 ,99 ):02d}'
        seg1 =random .randint (1000000 ,9999999 )
        seg2 =random .randint (1000000 ,9999999 )

        age_seconds =random .randint (7 *86400 ,90 *86400 )
        ts =int (time .time ())-age_seconds
        return f'{prefix }-{seg1 }-{seg2 }:{ts }'

    @staticmethod
    def __buildPerformanceTiming (start_ts :int ,now :int )->dict :
        """Tiempos de carga de la pagina simulados."""
        nav =start_ts -random .randint (100 ,600 )
        fetch =nav +random .randint (0 ,5 )
        dns_start =fetch +random .randint (0 ,3 )

        dns_end =dns_start if random .random ()<0.6 else dns_start +random .randint (10 ,150 )
        connect_start =dns_end
        connect_end =connect_start
        secure_start =0
        request_start =connect_end
        response_start =request_start +random .randint (150 ,800 )
        response_end =response_start +random .randint (50 ,400 )
        dom_loading =response_start +random .randint (5 ,30 )
        dom_interactive =dom_loading +random .randint (300 ,800 )
        dom_content_start =dom_interactive
        dom_content_end =dom_content_start +random .randint (0 ,50 )
        dom_complete =dom_content_end +random .randint (50 ,500 )
        load_start =dom_complete
        load_end =load_start +random .randint (10 ,200 )

        has_redirect =random .random ()<0.70
        if has_redirect :
            redirect_start =nav -random .randint (600 ,1200 )
            redirect_end =nav -random .randint (0 ,80 )
        else :
            redirect_start =0
            redirect_end =0

        has_unload =random .random ()<0.75
        if has_unload :
            unload_start =response_start +random .randint (10 ,60 )
            unload_end =unload_start +random .randint (0 ,30 )
        else :
            unload_start =0
            unload_end =0

        return {
        'connectStart':connect_start ,
        'secureConnectionStart':secure_start ,
        'unloadEventEnd':unload_end ,
        'domainLookupStart':dns_start ,
        'domainLookupEnd':dns_end ,
        'responseStart':response_start ,
        'connectEnd':connect_end ,
        'responseEnd':response_end ,
        'requestStart':request_start ,
        'domLoading':dom_loading ,
        'redirectStart':redirect_start ,
        'loadEventEnd':load_end ,
        'domComplete':dom_complete ,
        'navigationStart':nav ,
        'loadEventStart':load_start ,
        'domContentLoadedEventEnd':dom_content_end ,
        'unloadEventStart':unload_start ,
        'redirectEnd':redirect_end ,
        'domInteractive':dom_interactive ,
        'fetchStart':fetch ,
        'domContentLoadedEventStart':dom_content_start ,
        }

    def __encodeMetadata1 (self ,fingerprint :dict )->str :
        """Codifica el metadata1 en el formato que espera Amazon."""
        jsonStr =json .dumps (fingerprint ,separators =(',',':'))
        crc =format (zlib .crc32 (jsonStr .encode ('utf-8'))&4294967295 ,'08X')
        payload =f'{crc }#{jsonStr }'
        encrypted =self .__xxteaEncrypt (payload ,self .__KEY_MATERIAL )
        b64 =self .__base64Encode (encrypted )
        return f'{self .__KEY_IDENTIFIER }:{b64 }'

    def __xxteaEncrypt (self ,data :str ,key :list )->str :
        """Cifra el payload con XXTEA (el cifrado que usa Amazon)."""
        if len (data )==0 :
            return ''
        n =math .ceil (len (data )/4 )
        v =[]
        for i in range (n ):
            word =0
            for j in range (4 ):
                idx =i *4 +j
                if idx <len (data ):
                    word |=ord (data [idx ])<<j *8
            v .append (word )
        n =len (v )
        rounds =6 +52 //n
        total =0
        z =v [n -1 ]
        for _ in range (rounds ):
            total =total +self .__XXTEA_DELTA &4294967295
            e =total >>2 &3
            for p in range (n ):
                y =v [(p +1 )%n ]
                mx =(z >>5 ^y <<2 )+(y >>3 ^z <<4 )^(total ^y )+(key [p &3 ^e ]^z )
                v [p ]=v [p ]+mx &4294967295
                z =v [p ]
        result =[]
        for word in v :
            for j in range (4 ):
                result .append (chr (word >>j *8 &255 ))
        return ''.join (result )

    @staticmethod
    def __base64Encode (data :str )->str :
        """Codifica en base64 con el alfabeto que usa Amazon."""
        return base64 .b64encode (data .encode ('latin-1')).decode ('utf-8')
