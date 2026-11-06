#!/usr/bin/env python3
"""Construye la BD corregida de personal federalizado a partir de los CSV crudos.

Uso:
    python3 construir_bd_corregida.py

Lee todos los raw_p*.csv de este directorio, deduplica por fila exacta y por
CURP (una fila por persona), parsea Nombre/Paterno/Materno desde nombre_raw
(tokens iniciales TODO-MAYUSCULAS; el cargo viene en Title case y marca el
corte), deriva Fecha_Nacimiento/Sexo/Estado de la CURP, valida RFC<->CURP y
genera:
    - personal_federalizado_corregido.csv  (una fila por CURP, columnas limpias)
    - reporte_bd_corregida.json            (conteos y validacion)

Columnas de salida:
    RFC,CURP,Nombre,Apellido_Paterno,Apellido_Materno,Fecha_Nacimiento,
    Estado_Nacimiento,Edad_Actual,Sexo,Clave_CT,Turno,Entidad_Federativa
"""

import csv
import glob
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent
OUT_CSV = BASE / "personal_federalizado_corregido.csv"
OUT_JSON = BASE / "reporte_bd_corregida.json"

HOY = date(2026, 11, 2)  # America/Mexico_City, fecha de construccion

UPPER_RE = re.compile(r"^[A-ZÁÉÍÓÚÜÑ?]+$")

# Prefijos de apellido compuesto
PREF_1 = {"DE", "DEL", "SAN", "SANTA", "LA", "LOS", "LAS"}
SEGUNDO_DE_TRIO = {"LA", "LOS", "LAS", "DE", "DEL"}

ESTADOS_CURP = {
    "AS": "AGUASCALIENTES", "BC": "BAJA CALIFORNIA", "BS": "BAJA CALIFORNIA SUR",
    "CC": "CAMPECHE", "CL": "COAHUILA", "CM": "COLIMA", "CS": "CHIAPAS",
    "CH": "CHIHUAHUA", "DF": "CIUDAD DE MÉXICO", "DG": "DURANGO",
    "GT": "GUANAJUATO", "GR": "GUERRERO", "HG": "HIDALGO", "JC": "JALISCO",
    "MC": "MÉXICO", "MN": "MICHOACÁN", "MS": "MORELOS", "NT": "NAYARIT",
    "NL": "NUEVO LEÓN", "OC": "OAXACA", "PL": "PUEBLA", "QT": "QUERÉTARO",
    "QR": "QUINTANA ROO", "SP": "SAN LUIS POTOSÍ", "SL": "SINALOA",
    "SR": "SONORA", "TC": "TABASCO", "TS": "TAMAULIPAS", "TL": "TLAXCALA",
    "VZ": "VERACRUZ", "YN": "YUCATÁN", "ZS": "ZACATECAS", "NE": "NACIDO EN EL EXTRANJERO",
}


# Particulas que el RFC/CURP ignora al tomar iniciales (regla SAT)
SKIP_PARTICULA = {"DE", "DEL", "LA", "LAS", "LOS", "Y", "MC", "VON", "VAN"}
VOCALES = set("AEIOU")


def strip_acc(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def significativa(grupo: str) -> str:
    """Primera palabra con peso para iniciales (salta DE/LA/DEL...)."""
    for t in strip_acc(grupo).upper().split():
        if t not in SKIP_PARTICULA:
            return t
    return strip_acc(grupo).upper().split()[0] if grupo else ""


def primera_vocal_interna(palabra: str) -> str:
    for c in strip_acc(palabra).upper()[1:]:
        if c in VOCALES:
            return c
    return "X"


def apellido_compuesto(toks, i):
    """Devuelve (apellido, siguiente_indice) consumiendo 1-3 tokens."""
    if toks[i] == "DE" and i + 2 < len(toks) and toks[i + 1] in SEGUNDO_DE_TRIO:
        return " ".join(toks[i:i + 3]), i + 3
    if toks[i] in PREF_1 and i + 1 < len(toks):
        return " ".join(toks[i:i + 2]), i + 2
    return toks[i], i + 1


# Tokens que nunca cierran un nombre real: preposiciones/articulos,
# fragmentos de cargo en mayusculas ('DO','GR','CENTE','ADO') y palabras
# de cargo completas por si alguna viene todo-mayusculas.
COLA_CARGO = {
    "DE", "DEL", "LA", "LAS", "LOS", "EL", "EN", "CON", "SIN", "POR",
    "PARA", "AL", "O", "Y", "E", "LO", "SE", "SU", "MI",
    "DO", "CENTE", "ENTE", "GR", "ADO", "AD", "CENT", "ENTE",
    "TRACION", "TRACIO", "SONA", "ERSO", "OMUNICACIO", "OMUNICACION",
    "DOCENTE", "DIRECTOR", "PLANTEL", "GRUPO", "ASIGNATURAS", "GRADO",
    "COMUNICACION", "ADMINISTRACION", "PERSONA", "INTENDENCIA",
    "ASISTENTE", "SERVICIOS", "PREFECTO", "SUPERVISOR", "ZONA",
    "INSPECTOR", "TRABAJO", "APOYO", "JEFE", "COORDINADOR", "TECNICO",
}


def tokens_nombre(nombre_raw: str):
    """Tokens TODO-MAYUSCULAS del nombre; el cargo empieza con Title case.

    Recorta desde el final: fragmento de una letra del cargo ('FERNANDO
    C omunicacio') y tokens de COLA_CARGO. Un nombre real nunca termina
    en preposicion ni en fragmento de cargo.
    """
    toks = nombre_raw.split()
    nt = []
    i = 0
    for t in toks:
        if UPPER_RE.match(t):
            nt.append(t)
            i += 1
        else:
            break
    cut_seg = toks[i] if i < len(toks) else ""
    if len(nt) >= 2 and len(nt[-1]) == 1 and cut_seg[:1].islower():
        nt = nt[:-1]  # fragmento del cargo, no parte del nombre
    while len(nt) >= 2 and strip_acc(nt[-1]).upper() in COLA_CARGO:
        nt = nt[:-1]
    return nt


def puntaje(paterno, materno, nombre, rfc, curp) -> int:
    """0-8: que tanto encajan los 4 caracteres de nombre del RFC y la CURP."""
    s = 0
    sp = significativa(paterno)
    if not sp:
        return 0
    if rfc[0] == sp[0]:
        s += 1
    if len(rfc) > 1 and rfc[1] == primera_vocal_interna(sp):
        s += 1
    if materno:
        if len(rfc) > 2 and rfc[2] == significativa(materno)[0]:
            s += 1
    elif len(rfc) > 2 and rfc[2] == "X":
        s += 1
    if nombre:
        if len(rfc) > 3 and rfc[3] == strip_acc(nombre).upper()[0]:
            s += 1
    if curp[0] == sp[0]:
        s += 1
    if curp[1] == primera_vocal_interna(sp):
        s += 1
    if materno:
        if curp[2] == significativa(materno)[0]:
            s += 1
    elif curp[2] == "X":
        s += 1
    if nombre:
        if curp[3] == strip_acc(nombre).upper()[0]:
            s += 1
    return s


def parse_nombre(nombre_raw: str, rfc: str, curp: str):
    """Separa (paterno, materno, nombre).

    Genera candidatos (inicio 0/1, paterno 1-3 tokens, materno 0-2) y elige
    el de mayor puntaje contra RFC/CURP. Devuelve (paterno, materno, nombre,
    validacion) donde validacion es 'ok' (8/8) o 'revisar'.
    """
    nt = tokens_nombre(nombre_raw)
    if not nt:
        return "", "", nombre_raw.strip(), "revisar"
    if len(nt) == 1:
        return nt[0], "", "", "revisar"
    mejor = None
    inicios = (0, 1) if len(nt[0]) == 1 else (0,)
    for s in inicios:
        if s >= len(nt):
            continue
        cands_pat = {apellido_compuesto(nt, s)}
        cands_pat.add((nt[s], s + 1))
        if s + 2 <= len(nt):
            cands_pat.add((" ".join(nt[s:s + 2]), s + 2))
        for pat, i in cands_pat:
            if i > len(nt):
                continue
            rest = nt[i:]
            cands_mat = [("", i)]
            if rest:
                cands_mat.append((rest[0], i + 1))
                mp, j = apellido_compuesto(rest, 0)
                if j > 1:
                    cands_mat.append((mp, i + j))
                if len(rest) >= 2:
                    cands_mat.append((" ".join(rest[:2]), i + 2))
            for mat, j in cands_mat:
                nom = " ".join(nt[j:])
                if not nom:
                    continue
                sc = puntaje(pat, mat, nom, rfc, curp)
                key = (sc, -(s), -(len(pat.split()) - 1))
                if mejor is None or key > mejor[0]:
                    mejor = (key, pat, mat, nom)
    if mejor is None:
        return nt[0], " ".join(nt[1:2]), " ".join(nt[2:]), "revisar"
    _, pat, mat, nom = mejor
    sc = mejor[0][0]
    if "?" in pat or "?" in mat or "?" in nom:
        return pat, mat, nom, "revisar"
    if sc == 8:
        return pat, mat, nom, "ok"
    # homonimia SAT: nombre compuesto MARIA/JOSE + palabra -> RFC/CURP usan la 2a
    nt_nom = strip_acc(nom).upper().split()
    if len(nt_nom) >= 2 and nt_nom[0] in ("MARIA", "JOSE", "MA", "J"):
        seg = nt_nom[1]
        if (len(rfc) > 3 and rfc[3] == seg[0]) or curp[3] == seg[0]:
            if sc >= 6:
                return pat, mat, nom, "ok_homonimia"
    # materno ausente confirmado por X en RFC[2] y CURP[2]
    if not mat and len(rfc) > 2 and rfc[2] == "X" and curp[2] == "X" and sc >= 6:
        return pat, mat, nom, "ok"
    return pat, mat, nom, "revisar"


def fecha_de_yymmdd(yymmdd: str):
    yy = int(yymmdd[0:2]); mm = int(yymmdd[2:4]); dd = int(yymmdd[4:6])
    anio = 2000 + yy if yy <= HOY.year % 100 else 1900 + yy
    try:
        return date(anio, mm, dd)
    except ValueError:
        return None


def edad_en(fecha: date) -> int:
    return HOY.year - fecha.year - ((HOY.month, HOY.day) < (fecha.month, fecha.day))


def main():
    archivos = sorted(glob.glob(str(BASE / "raw_p0*.csv")))
    assert archivos, "no hay raw_p*.csv"
    vistos_fila = set()
    por_curp = {}  # curp -> dict (primera ocurrencia)
    stats = {
        "filas_crudos": 0, "filas_exactas_dup": 0, "personas_unicas": 0,
        "filas_multiplaza": 0, "rfc_fecha_vs_curp_fecha_mismatch": 0,
        "validacion_nombres": {}, "flags_parseo": {},
        "curp_sexo_invalido": 0, "curp_estado_desconocido": 0,
        "curp_fecha_invalida": 0,
        "descartadas_fecha_invalida": 0,
    }

    for f in archivos:
        for r in csv.DictReader(open(f, encoding="utf-8")):
            stats["filas_crudos"] += 1
            key = (r["rfc"], r["curp"], r["nombre_raw"])
            if key in vistos_fila:
                stats["filas_exactas_dup"] += 1
                continue
            vistos_fila.add(key)
            curp = r["curp"].strip().upper()
            rfc = r["rfc"].strip().upper()
            if curp in por_curp:
                stats["filas_multiplaza"] += 1
                continue
            paterno, materno, nombre, val = parse_nombre(r["nombre_raw"], rfc, curp)
            stats["flags_parseo"][val] = stats["flags_parseo"].get(val, 0) + 1
            # diagnostico: que tanto encajan RFC<->CURP entre si
            if rfc[4:10] != curp[4:10]:
                stats["rfc_fecha_vs_curp_fecha_mismatch"] += 1
            # derivados CURP
            fecha = fecha_de_yymmdd(curp[4:10])
            if fecha is None:
                stats["curp_fecha_invalida"] += 1
                stats["descartadas_fecha_invalida"] += 1
                continue
            sexo_c = curp[10]
            sexo = {"H": "HOMBRE", "M": "MUJER"}.get(sexo_c, "")
            if not sexo:
                stats["curp_sexo_invalido"] += 1
            edo = ESTADOS_CURP.get(curp[11:13], "")
            if not edo:
                stats["curp_estado_desconocido"] += 1
            por_curp[curp] = {
                "RFC": rfc,
                "CURP": curp,
                "Nombre": nombre,
                "Apellido_Paterno": paterno,
                "Apellido_Materno": materno,
                "Fecha_Nacimiento": fecha.strftime("%d/%m/%Y"),
                "Estado_Nacimiento": edo,
                "Edad_Actual": edad_en(fecha),
                "Sexo": sexo,
                "Clave_CT": r["clave_ct"],
                "Turno": r["turno"],
                "Entidad_Federativa": r["entidad"],
                "Validacion_Nombre": val,
            }

    stats["personas_unicas"] = len(por_curp)
    n_ok = sum(1 for v in por_curp.values() if v["Validacion_Nombre"] == "ok")
    stats["validacion_nombres"] = {"ok_8_8": n_ok, "revisar": len(por_curp) - n_ok}
    cols = ["RFC", "CURP", "Nombre", "Apellido_Paterno", "Apellido_Materno",
            "Fecha_Nacimiento", "Estado_Nacimiento", "Edad_Actual", "Sexo",
            "Clave_CT", "Turno", "Entidad_Federativa", "Validacion_Nombre"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for curp in sorted(por_curp):
            w.writerow(por_curp[curp])
    stats["archivo"] = OUT_CSV.name
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
