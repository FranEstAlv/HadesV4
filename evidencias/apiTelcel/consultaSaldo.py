import sys; sys.stdout.reconfigure(encoding='utf-8')
import json, time
from curl_cffi import requests as curl


def saldo(telefono: str) -> dict:
    ts = time.time()

    try:
        headers = {'Host': 'mitelcel.smapps.mx:446','Content-Type': 'application/json','Accept': 'application/json','Accept-Language': 'es-MX,es-419;q=0.9,es;q=0.8','User-Agent': 'Mi%20Telcel/2 CFNetwork/3860.300.31 Darwin/25.2.0','Connection': 'keep-alive','Authorization': 'Bearer 479987c7-c264-375f-91d6-55346af3a529',}
        body = {'apprequesttime': f'{time.time() * 1000:.6f}','tipoRequest': '1','argumentos': {'deviceId': 'iPhone','dispositivo': 'IPH','telefono': telefono,'perfil': 'AMIGO','region': '4','tecnologia': '2','email': 'null','version': '14.9.5','so': 'iOS 26.2',},'wifi2Go': 'false',}
        r = curl.post('https://mitelcel.smapps.mx:446/MiTelcelJAVA/USUARIO/1.0.0/v4/saldo_prepago',headers=headers,json=body,verify=False,timeout=20,).json()

        if r.get('codigoRespuesta') != 0:
            return {'telefono': telefono, 'status': 'Error ⚠️', 'mensaje': r.get('mensajeRespuesta', ''), 'time': f'{round(time.time()-ts, 2)}s'}

        saldos = {s['nombre'].strip().rstrip(':'): s['valor'] for s in r.get('saldos', [])}
        extras = {s['nombre'].strip().rstrip(':'): s['valor'] for s in r.get('saldosSecundarios', [])}
        vencimiento = r['saldos'][0].get('fechaExpiracion', '') if r.get('saldos') else ''

        return {'telefono': telefono,'status': 'OK 🟢','saldo': saldos,'extras': extras,'vencimiento': vencimiento,'time': f'{round(time.time()-ts, 2)}s',}

    except Exception as e:
        return {'telefono': telefono, 'status': 'Error ⚠️', 'mensaje': str(e), 'time': f'{round(time.time()-ts, 2)}s'}


#//! ---------------------------------------- Run ------------------------------------------ !\\#

telefono = input('Número Telcel: ').strip()
r = saldo(telefono)
print(json.dumps(r, indent=4, ensure_ascii=False))