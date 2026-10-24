"""Lógica pura del extrapolador de BINs (port de Hades V1 /extra).

Sin dependencias de Telegram: templates tipo ``NUMERO|MM|AAAA|CVV`` con
``x`` como comodín, detección de red, construcción desde BIN y
extrapolación por deltas gaussianos.
"""
from __future__ import annotations

import random
from collections import Counter
from datetime import datetime


def _suma_luhn(numero_str: str, es_segundo_inicial: bool = False) -> int:
    if not numero_str.isdigit():
        raise ValueError(f"Solo dígitos: '{numero_str}'")
    suma = 0
    segundo = es_segundo_inicial
    for c in reversed(numero_str):
        d = int(c)
        if segundo:
            d *= 2
            if d > 9:
                d -= 9
        suma += d
        segundo = not segundo
    return suma


def es_valido_luhn(numero_str: str) -> bool:
    try:
        return _suma_luhn(numero_str, False) % 10 == 0
    except ValueError:
        return False


def calcular_digito_luhn(base: str) -> str:
    suma = _suma_luhn(base, True)
    return str((10 - (suma % 10)) % 10)


def detectar_red(numero: str) -> str:
    if not numero:
        return "Desconocida"
    if numero.startswith("4"):
        return "Visa"
    if len(numero) >= 2:
        try:
            d2 = int(numero[:2])
        except ValueError:
            return "Otra"
        if numero[:2] in ("34", "37"):
            return "Amex"
        if 51 <= d2 <= 55:
            return "Mastercard"
        if numero[:2] in ("36", "38"):
            return "Diners"
        if numero[:2] == "65":
            return "Discover"
        if numero[:2] in ("50", "56", "57", "58", "63", "67"):
            return "Maestro"
    if len(numero) >= 3:
        try:
            d3 = int(numero[:3])
        except ValueError:
            return "Otra"
        if 644 <= d3 <= 649:
            return "Discover"
        if numero[:3] in ("300", "301", "302", "303", "304", "305"):
            return "Diners"
        if numero[:3] == "220":
            return "Mir"
    if len(numero) >= 4:
        try:
            d4 = int(numero[:4])
        except ValueError:
            return "Otra"
        if 2221 <= d4 <= 2720:
            return "Mastercard"
        if numero[:4] == "6011":
            return "Discover"
        if numero[:4] in ("5018", "5020", "5038", "6304"):
            return "Maestro"
        if 3528 <= d4 <= 3589:
            return "JCB"
        if numero[:4] in ("2200", "2201", "2202", "2203", "2204"):
            return "Mir"
    if len(numero) >= 6:
        try:
            d6 = int(numero[:6])
        except ValueError:
            return "Otra"
        if 622126 <= d6 <= 622925:
            return "UnionPay"
        if 624000 <= d6 <= 626999:
            return "UnionPay"
        if 628200 <= d6 <= 628899:
            return "UnionPay"
        if 621693 <= d6 <= 621699:
            return "UnionPay"
    return "Otra"


_INFO_RED: dict = {
    "Visa": {"longitudes": [13, 16], "cvv": 3},
    "Mastercard": {"longitudes": [16], "cvv": 3},
    "Amex": {"longitudes": [15], "cvv": 4},
    "Diners": {"longitudes": [14], "cvv": 3},
    "Discover": {"longitudes": [16], "cvv": 3},
    "JCB": {"longitudes": [16], "cvv": 3},
    "Maestro": {"longitudes": [13, 15, 16, 18, 19], "cvv": 3},
    "UnionPay": {"longitudes": [16, 17, 18, 19], "cvv": 3},
    "Mir": {"longitudes": [16], "cvv": 3},
    "Otra": {"longitudes": [16], "cvv": 3},
}


def info_red(bin_o_numero: str) -> dict:
    red = detectar_red(bin_o_numero)
    datos = _INFO_RED.get(red, _INFO_RED["Otra"])
    return {
        "red": red,
        "longitud": datos["longitudes"][0],
        "cvv_len": datos["cvv"],
        "longitudes": datos["longitudes"],
    }


def parsear_template(template: str) -> dict:
    partes = [p.strip() for p in template.strip().split("|")]
    numero_tpl = partes[0].lower() if len(partes) > 0 else ""
    mes_tpl = partes[1] if len(partes) > 1 else "xx"
    anio_tpl = partes[2] if len(partes) > 2 else "20xx"
    cvv_tpl = partes[3] if len(partes) > 3 else "xxx"
    prefijo_fijo = ""
    for c in numero_tpl:
        if c == "x":
            break
        prefijo_fijo += c
    x_pattern = numero_tpl[len(prefijo_fijo):]
    return {
        "numero_tpl": numero_tpl,
        "prefijo_fijo": prefijo_fijo,
        "x_pattern": x_pattern,
        "mes_tpl": mes_tpl,
        "anio_tpl": anio_tpl,
        "cvv_tpl": cvv_tpl,
        "longitud_num": len(numero_tpl),
    }


def _delta_inteligente() -> int:
    while True:
        d = int(random.gauss(0, 15))
        if d != 0 and -300 <= d <= 300:
            return d


def _delta_variado() -> int:
    while True:
        d = int(random.gauss(0, 50))
        if d != 0 and -500 <= d <= 500:
            return d


def extrapolar_templates(
    template_base: str,
    cantidad: int = 10,
    modo: str = "inteligente",
    bin_len: int = 8,
) -> list:
    template_base = template_base.strip().lower()
    p = parsear_template(template_base)
    prefijo_fijo = p["prefijo_fijo"]
    if not prefijo_fijo or not prefijo_fijo.isdigit():
        raise ValueError("El BIN necesita dígitos fijos antes de las x")
    if len(prefijo_fijo) <= bin_len:
        bin_parte = prefijo_fijo[:-2] if len(prefijo_fijo) > 2 else ""
        cuenta_parte = prefijo_fijo[-2:] if len(prefijo_fijo) > 2 else prefijo_fijo
    else:
        bin_parte = prefijo_fijo[:bin_len]
        cuenta_parte = prefijo_fijo[bin_len:]
    longitud_cuenta = len(cuenta_parte)
    cuenta_int = int(cuenta_parte)
    cuenta_max = int("9" * longitud_cuenta)
    max_posibles = cuenta_max if cuenta_max > 0 else 1
    if longitud_cuenta < 3:
        cantidad = min(cantidad, max_posibles)
    templates: set = {template_base}
    intentos = 0
    espacio = cuenta_max + 1
    factor = max(50, min(200, espacio // max(1, cantidad)))
    max_iter = cantidad * factor
    while len(templates) - 1 < cantidad and intentos < max_iter:
        intentos += 1
        delta = _delta_inteligente() if modo == "inteligente" else _delta_variado()
        nueva_int = max(0, min(cuenta_max, cuenta_int + delta))
        nueva_cuenta = str(nueva_int).zfill(longitud_cuenta)
        nuevo = (
            f"{bin_parte}{nueva_cuenta}"
            f"{p['x_pattern']}|{p['mes_tpl']}|{p['anio_tpl']}|{p['cvv_tpl']}"
        )
        if nuevo not in templates:
            templates.add(nuevo)
        if longitud_cuenta < 3 and len(templates) - 1 >= max_posibles:
            break
    resultado = [t for t in templates if t != template_base]
    return sorted(resultado)[:cantidad]


def extraer_numero(linea: str) -> str:
    return linea.strip().split("|")[0].strip()


def analizar_patron(
    entradas: list,
    umbral: float = 0.6,
    mes_tpl: str = "xx",
    anio_tpl: str = "20xx",
    cvv_tpl: str = "xxx",
) -> tuple:
    numeros = [extraer_numero(e) for e in entradas if extraer_numero(e).isdigit()]
    if len(numeros) < 2:
        raise ValueError("Se necesitan al menos 2 números válidos")
    longitud = Counter(len(n) for n in numeros).most_common(1)[0][0]
    numeros = [n for n in numeros if len(n) == longitud]
    template = ""
    reporte: dict = {}
    for pos in range(longitud):
        digitos_pos = [n[pos] for n in numeros]
        conteo = Counter(digitos_pos)
        mas_comun, freq = conteo.most_common(1)[0]
        porcentaje = freq / len(numeros)
        reporte[pos + 1] = {
            "digito_top": mas_comun,
            "frecuencia": freq,
            "total": len(numeros),
            "porcentaje": round(porcentaje * 100, 1),
            "fijo": porcentaje > umbral,
        }
        template += mas_comun if porcentaje > umbral else "x"
    bin_confiable = ""
    for c in template:
        if c == "x":
            break
        bin_confiable += c
    return f"{template}|{mes_tpl}|{anio_tpl}|{cvv_tpl}", reporte, bin_confiable


def construir_template_desde_bin(
    bin_str: str,
    cuenta_parcial: str = "",
    mes_fijo: str = "xx",
    anio_fijo: str = "20xx",
    cvv_fijo: str = "",
    longitud: int | None = None,
) -> str:
    bin_str = bin_str.strip()
    if not bin_str.isdigit():
        raise ValueError(f"BIN inválido: '{bin_str}'")
    d = info_red(bin_str)
    if longitud is None:
        longitud = d["longitud"]
    cvv_len = d["cvv_len"]
    cvv_tpl = cvv_fijo if cvv_fijo else "x" * cvv_len
    prefijo = bin_str + cuenta_parcial
    if len(prefijo) >= longitud:
        raise ValueError(
            f"Prefijo ({len(prefijo)} dígitos) >= longitud de la tarjeta ({longitud})"
        )
    x_count = longitud - len(prefijo)
    numero_tpl = prefijo + "x" * x_count
    return f"{numero_tpl}|{mes_fijo}|{anio_fijo}|{cvv_tpl}"


def umbral_por_lives(n: int) -> float:
    if n <= 2:
        return 0.99
    if n == 3:
        return 0.80
    if n == 4:
        return 0.75
    if n <= 7:
        return 0.70
    if n <= 12:
        return 0.65
    return 0.60


def detectar_fecha_comun(entradas: list) -> tuple:
    from collections import Counter as _C

    meses: list = []
    anios: list = []
    for e in entradas:
        partes = e.strip().split("|")
        if len(partes) >= 3:
            m = partes[1].strip()
            a = partes[2].strip()
            if m.isdigit():
                meses.append(m.zfill(2))
            if a.isdigit():
                anios.append(a)
    mes_auto = "xx"
    anio_auto = "20xx"
    if meses:
        mes_comun, mes_freq = _C(meses).most_common(1)[0]
        if mes_freq / len(entradas) >= 0.60:
            mes_auto = mes_comun
    if anios:
        anio_comun, anio_freq = _C(anios).most_common(1)[0]
        if anio_freq / len(entradas) >= 0.60:
            anio_auto = anio_comun
    return mes_auto, anio_auto


def red_comun_y_cvv(entradas: list) -> tuple:
    from collections import Counter as _C

    redes = [
        info_red(extraer_numero(e))["red"]
        for e in entradas
        if extraer_numero(e).isdigit()
    ]
    if not redes:
        return "Otra", "xxx"
    red = _C(redes).most_common(1)[0][0]
    cvv_len = _INFO_RED.get(red, _INFO_RED["Otra"])["cvv"]
    return red, "x" * cvv_len


def validar_bin_entrada(texto: str) -> bool:
    t = texto.strip()
    if "|" in t:
        num = t.split("|")[0].lower().strip()
        return bool(num) and all(c.isdigit() or c == "x" for c in num)
    return t.isdigit() and 6 <= len(t) <= 10


def normalizar_template_entrada(texto: str) -> str:
    t = texto.strip()
    if "|" in t:
        num = t.split("|")[0].lower().strip()
        resto = [p.strip() for p in t.split("|")[1:]]
        while len(resto) < 3:
            resto.append("xx" if len(resto) == 0 else ("20xx" if len(resto) == 1 else "xxx"))
        return f"{num}|{resto[0]}|{resto[1]}|{resto[2]}"
    return construir_template_desde_bin(t)


def anio_actual() -> int:
    return datetime.now().year
