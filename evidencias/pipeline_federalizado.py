#!/usr/bin/env python3
"""Pipeline unico: PDF maestro -> BD corregida de personal federalizado.

Reemplaza a plu.py y pdf.py (ambos en /home/olimpo/gates/), que extraian
nombres por regex de linea y asumian 'primeros 2 tokens = apellidos' sin
validar contra RFC/CURP. Ese defecto genero el CSV viejo con campos
Apellido_Paterno/Apellido_Materno corruptos (ej. 'Mexico', 'Des').

Mejoras de este script:
  1. Extraccion por TABLAS (pdfplumber extract_tables), no por lineas: el
     campo Nombre viene fragmentado en 3 celdas (5/6/7) y al unirlas se
     recupera completo. Por linea se rompia el orden.
  2. Escritura INCREMENTAL con CHECKPOINT por pagina (idea rescatada de
     plu.py): si el proceso muere, se reanuda desde la ultima pagina
     procesada sin repetir trabajo (--ini/--fin + archivo .checkpoint).
  3. Parseo de nombres por PUNTAJE contra RFC/CURP: genera candidatos
     (paterno compuesto, materno ausente, homonimia MARIA/JOSE, letra
     inicial suelta) y elige el que maximiza coincidencias con los 4
     caracteres de nombre del RFC y la CURP (reglas SAT: particulas DE/LA
     ignoradas, primera vocal interna, X = faltante).
  4. Campos Fecha_Nacimiento/Sexo/Estado derivados de la CURP (posiciones
     4-9, 10, 11-12), nunca del texto OCR. Edad calculada a fecha fija.
  5. Deduplicacion en 2 niveles: fila exacta y CURP (una fila por persona;
     la primera ocurrencia conserva su Clave_CT/Turno: es la plaza base).
  6. Reporte JSON de validacion con conteos auditables.

Uso:
    # corrida completa (reasumible: si se interrumpe, correr de nuevo igual)
    python3 pipeline_federalizado.py --pdf /home/olimpo/gates/personal_federalizado.pdf

    # reprocesar solo la cola de 'revisar' con reglas nuevas (sin releer el PDF)
    python3 pipeline_federalizado.py --solo-reparsear

    # probar en N paginas
    python3 pipeline_federalizado.py --pdf ... --ini 1 --fin 5 --salida prueba.csv
"""

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

import pdfplumber

BASE = Path(__file__).parent
HOY = date(2026, 11, 2)  # America/Mexico_City, fecha de construccion

# ---------------------------------------------------------------------------
# Constantes y reglas SAT
# ---------------------------------------------------------------------------
RFC_RE = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")
UPPER_RE = re.compile(r"^[A-ZÁÉÍÓÚÜÑ?]+$")
PREF_1 = {"DE", "DEL", "SAN", "SANTA", "LA", "LOS", "LAS"}
SEGUNDO_DE_TRIO = {"LA", "LOS", "LAS", "DE", "DEL"}
SKIP_PARTICULA = {"DE", "DEL", "LA", "LAS", "LOS", "Y", "MC", "VON", "VAN"}
VOCALES = set("AEIOU")

# Tokens que nunca cierran un nombre real: preposiciones/articulos,
# fragmentos de cargo en mayusculas y palabras de cargo completas.
COLA_CARGO = {
    "DE", "DEL", "LA", "LAS", "LOS", "EL", "EN", "CON", "SIN", "POR",
    "PARA", "AL", "O", "Y", "E", "LO", "SE", "SU", "MI",
    "DO", "CENTE", "ENTE", "GR", "ADO", "AD", "CENT",
    "TRACION", "TRACIO", "SONA", "ERSO", "OMUNICACIO", "OMUNICACION",
    "DOCENTE", "DIRECTOR", "PLANTEL", "GRUPO", "ASIGNATURAS", "GRADO",
    "COMUNICACION", "ADMINISTRACION", "PERSONA", "INTENDENCIA",
    "ASISTENTE", "SERVICIOS", "PREFECTO", "SUPERVISOR", "ZONA",
    "INSPECTOR", "TRABAJO", "APOYO", "JEFE", "COORDINADOR", "TECNICO",
}

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

OUT_COLS = ["RFC", "CURP", "Nombre", "Apellido_Paterno", "Apellido_Materno",
            "Fecha_Nacimiento", "Estado_Nacimiento", "Edad_Actual", "Sexo",
            "Clave_CT", "Turno", "Entidad_Federativa", "Validacion_Nombre"]

RAW_COLS = ["pagina", "rfc", "curp", "nombre_raw", "clave_ct", "turno", "entidad"]


# ---------------------------------------------------------------------------
# Utilidades de texto y reglas SAT
# ---------------------------------------------------------------------------
def strip_acc(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def significativa(grupo: str) -> str:
    """Primera palabra con peso para iniciales (salta DE/LA/DEL...)."""
    toks = strip_acc(grupo).upper().split()
    for t in toks:
        if t not in SKIP_PARTICULA:
            return t
    return toks[0] if toks else ""


def primera_vocal_interna(palabra: str) -> str:
    for c in strip_acc(palabra).upper()[1:]:
        if c in VOCALES:
            return c
    return "X"


def apellido_compuesto(toks, i):
    if toks[i] == "DE" and i + 2 < len(toks) and toks[i + 1] in SEGUNDO_DE_TRIO:
        return " ".join(toks[i:i + 3]), i + 3
    if toks[i] in PREF_1 and i + 1 < len(toks):
        return " ".join(toks[i:i + 2]), i + 2
    return toks[i], i + 1


def tokens_nombre(nombre_raw: str):
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
                key = (sc, -s, -(len(pat.split()) - 1))
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
    nt_nom = strip_acc(nom).upper().split()
    if len(nt_nom) >= 2 and nt_nom[0] in ("MARIA", "JOSE", "MA", "J"):
        for w in nt_nom[1:]:
            if w not in SKIP_PARTICULA:
                if (len(rfc) > 3 and rfc[3] == w[0]) or curp[3] == w[0]:
                    if sc >= 6:
                        return pat, mat, nom, "ok_homonimia"
                break
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


# ---------------------------------------------------------------------------
# Fase 1: PDF -> crudo incremental con checkpoint
# ---------------------------------------------------------------------------
def cell(row, i):
    if i < len(row) and row[i]:
        return " ".join(str(row[i]).split())
    return ""


def fase_extraer(pdf_path: Path, raw_path: Path, ini: int, fin: int):
    ckpt_path = raw_path.with_suffix(".checkpoint")
    start = ini
    if ckpt_path.exists():
        try:
            start = max(ini, int(ckpt_path.read_text().strip()) + 1)
            print(f"checkpoint: reanudando desde pagina {start}")
        except ValueError:
            pass
    nuevo = not raw_path.exists() or start == ini
    total, validas, descartadas = 0, 0, 0
    with pdfplumber.open(pdf_path) as pdf:
        npages = len(pdf.pages)
        if ini < 1 or fin > npages or ini > fin:
            return {"ok": False, "error": f"rango invalido, pdf tiene {npages} paginas"}
        with open(raw_path, "a" if not nuevo else "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if nuevo:
                w.writerow(RAW_COLS)
            for p in range(start, fin + 1):
                try:
                    tables = pdf.pages[p - 1].extract_tables()
                except Exception as e:
                    print(f"pagina {p}: error de extraccion ({e}), saltando")
                    continue
                if not tables:
                    continue
                for row in tables[0][3:]:
                    total += 1
                    rfc = cell(row, 3).upper().replace(" ", "")
                    curp = cell(row, 4).upper().replace(" ", "")
                    if not RFC_RE.match(rfc) or len(curp) != 18:
                        descartadas += 1
                        continue
                    combos = [cell(row, 5), cell(row, 6), cell(row, 7)]
                    nombre_raw = " ".join(x for x in combos if x)
                    nombre_raw = " ".join(nombre_raw.split())
                    if len(nombre_raw) < 5:
                        descartadas += 1
                        continue
                    w.writerow([p, rfc, curp, nombre_raw,
                                cell(row, 1), cell(row, 2), cell(row, 0)])
                    validas += 1
                f.flush()
                os.fsync(f.fileno())
                ckpt_path.write_text(str(p))
                if p % 50 == 0 or p == fin:
                    print(f"pagina {p}/{fin} | validas: {validas}", flush=True)
    try:
        ckpt_path.unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, "total": total, "validas": validas,
            "descartadas": descartadas}


# ---------------------------------------------------------------------------
# Fase 2: crudo -> BD corregida
# ---------------------------------------------------------------------------
def fase_construir(raw_path: Path, out_csv: Path, out_json: Path):
    vistos = set()
    por_curp = {}
    stats = {
        "filas_crudos": 0, "filas_exactas_dup": 0, "personas_unicas": 0,
        "filas_multiplaza": 0, "rfc_fecha_vs_curp_fecha_mismatch": 0,
        "validacion_nombres": {}, "flags_parseo": {},
        "curp_sexo_invalido": 0, "curp_estado_desconocido": 0,
        "curp_fecha_invalida": 0, "descartadas_fecha_invalida": 0,
    }
    for r in csv.DictReader(open(raw_path, encoding="utf-8")):
        stats["filas_crudos"] += 1
        key = (r["rfc"], r["curp"], r["nombre_raw"])
        if key in vistos:
            stats["filas_exactas_dup"] += 1
            continue
        vistos.add(key)
        curp, rfc = r["curp"].strip().upper(), r["rfc"].strip().upper()
        if curp in por_curp:
            stats["filas_multiplaza"] += 1
            continue
        paterno, materno, nombre, val = parse_nombre(r["nombre_raw"], rfc, curp)
        stats["flags_parseo"][val] = stats["flags_parseo"].get(val, 0) + 1
        if rfc[4:10] != curp[4:10]:
            stats["rfc_fecha_vs_curp_fecha_mismatch"] += 1
        fecha = fecha_de_yymmdd(curp[4:10])
        if fecha is None:
            stats["curp_fecha_invalida"] += 1
            stats["descartadas_fecha_invalida"] += 1
            continue
        sexo = {"H": "HOMBRE", "M": "MUJER"}.get(curp[10], "")
        if not sexo:
            stats["curp_sexo_invalido"] += 1
        edo = ESTADOS_CURP.get(curp[11:13], "")
        if not edo:
            stats["curp_estado_desconocido"] += 1
        por_curp[curp] = {
            "RFC": rfc, "CURP": curp, "Nombre": nombre,
            "Apellido_Paterno": paterno, "Apellido_Materno": materno,
            "Fecha_Nacimiento": fecha.strftime("%d/%m/%Y"),
            "Estado_Nacimiento": edo, "Edad_Actual": edad_en(fecha),
            "Sexo": sexo, "Clave_CT": r["clave_ct"], "Turno": r["turno"],
            "Entidad_Federativa": r["entidad"], "Validacion_Nombre": val,
        }
    stats["personas_unicas"] = len(por_curp)
    n_ok = sum(1 for v in por_curp.values() if v["Validacion_Nombre"] in ("ok", "ok_homonimia"))
    stats["validacion_nombres"] = {"ok": n_ok, "revisar": len(por_curp) - n_ok}
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS)
        w.writeheader()
        for curp in sorted(por_curp):
            w.writerow(por_curp[curp])
    stats["archivo"] = out_csv.name
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=str, default="/home/olimpo/gates/personal_federalizado.pdf")
    ap.add_argument("--ini", type=int, default=1)
    ap.add_argument("--fin", type=int, default=0, help="0 = todas las paginas")
    ap.add_argument("--crudo", type=str, default="personal_federalizado_crudo.csv")
    ap.add_argument("--salida", type=str, default="personal_federalizado_corregido.csv")
    ap.add_argument("--reporte", type=str, default="reporte_bd_corregida.json")
    ap.add_argument("--solo-reparsear", action="store_true",
                    help="no toca el PDF: reconstruye la BD desde el CSV crudo")
    args = ap.parse_args()

    raw_path = BASE / args.crudo
    if not args.solo_reparsear:
        pdf_path = Path(args.pdf)
        if not pdf_path.exists():
            print(json.dumps({"ok": False, "error": f"no existe {pdf_path}"}))
            sys.exit(1)
        import fitz  # solo para contar paginas rapido
        with fitz.open(pdf_path) as doc:
            npages = doc.page_count
        fin = args.fin or npages
        print(f"fase 1/2: extrayendo paginas {args.ini}-{fin} de {npages}")
        res = fase_extraer(pdf_path, raw_path, args.ini, fin)
        print(json.dumps(res, ensure_ascii=False))
        if not res.get("ok"):
            sys.exit(1)
    print("fase 2/2: construyendo BD corregida")
    stats = fase_construir(raw_path, BASE / args.salida, BASE / args.reporte)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
