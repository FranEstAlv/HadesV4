#!/usr/bin/env python3
"""Extrae filas del PDF maestro de personal federalizado por rango de paginas.

Uso:
    python3 extraer_pdf_federalizado.py --ini 1 --fin 130 --out raw_p0001_0130.csv

Lee las tablas de cada pagina con pdfplumber, une las columnas 5+6+7 del
campo Nombre (viene fragmentado en 3 celdas) y guarda un CSV crudo.
Solo conserva filas con RFC valido; el parseo de nombres y los campos
derivados de CURP se hacen en construir_bd_corregida.py

El PDF esta en /home/olimpo/gates/personal_federalizado.pdf
La salida se escribe en el directorio de este script (evidencias/).
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import pdfplumber

PDF_PATH = Path("/home/olimpo/gates/personal_federalizado.pdf")
OUT_DIR = Path(__file__).parent

RFC_RE = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")

RAW_COLS = [
    "pagina", "entidad", "clave_ct", "turno", "rfc", "curp", "nombre_raw",
    "partida", "cod_pago", "unidad", "subunidad", "categoria", "horas",
    "plaza", "tipo_cat", "contrato_hon", "periodo_ini", "periodo_fin",
    "perc_fed", "perc_otra",
]


def cell(row, i):
    if i < len(row) and row[i]:
        return " ".join(str(row[i]).split())
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ini", type=int, required=True, help="pagina inicial (1-indexed, inclusiva)")
    ap.add_argument("--fin", type=int, required=True, help="pagina final (1-indexed, inclusiva)")
    ap.add_argument("--out", type=str, required=True, help="nombre del CSV de salida")
    args = ap.parse_args()

    out_path = OUT_DIR / args.out
    total_filas = 0
    validas = 0
    descartadas = 0

    with pdfplumber.open(PDF_PATH) as pdf:
        npages = len(pdf.pages)
        if args.ini < 1 or args.fin > npages or args.ini > args.fin:
            print(json.dumps({"ok": False, "error": f"rango invalido, pdf tiene {npages} paginas"}))
            sys.exit(1)

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(RAW_COLS)
            for p in range(args.ini, args.fin + 1):
                page = pdf.pages[p - 1]
                tables = page.extract_tables()
                if not tables:
                    continue
                for row in tables[0][3:]:  # saltar 3 filas de encabezado
                    total_filas += 1
                    rfc = cell(row, 3).upper().replace(" ", "")
                    curp = cell(row, 4).upper().replace(" ", "")
                    if not RFC_RE.match(rfc) or len(curp) != 18:
                        descartadas += 1
                        continue
                    nombre_raw = " ".join(
                        x for x in (cell(row, 5), cell(row, 6), cell(row, 7)) if x
                    )
                    nombre_raw = " ".join(nombre_raw.split())
                    if len(nombre_raw) < 5:
                        descartadas += 1
                        continue
                    w.writerow([
                        p, cell(row, 0), cell(row, 1), cell(row, 2),
                        rfc, curp, nombre_raw,
                        cell(row, 8), cell(row, 9), cell(row, 10),
                        cell(row, 11), cell(row, 12), cell(row, 13),
                        cell(row, 14), cell(row, 15), cell(row, 16),
                        cell(row, 17), cell(row, 18), cell(row, 19),
                        cell(row, 20),
                    ])
                    validas += 1

    print(json.dumps({
        "ok": True, "ini": args.ini, "fin": args.fin,
        "archivo": args.out, "total_filas": total_filas,
        "validas": validas, "descartadas": descartadas,
    }))


if __name__ == "__main__":
    main()
