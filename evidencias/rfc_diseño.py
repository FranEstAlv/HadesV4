"""Diseno del comando /rfc para Hades.

Fuente de datos: personal_federalizado_corregido.csv
Generada desde el PDF maestro /home/olimpo/gates/personal_federalizado.pdf
con evidencias/pipeline_federalizado.py (tablas + validacion cruzada
RFC<->CURP). Columnas limpias:

    RFC,CURP,Nombre,Apellido_Paterno,Apellido_Materno,Fecha_Nacimiento,
    Estado_Nacimiento,Edad_Actual,Sexo,Clave_CT,Turno,Entidad_Federativa,
    Validacion_Nombre

Formato de salida segun especificacion del usuario (todo en blockquote,
CURP y RFC en monospace):

HADES CHK

Nombre: Delia Isabel
Paterno: Maldonado
Materno: Ramirez

Edad: 47
Sexo: MUJER
Fecha de nacimiento: 19-01-1979
Estado de nacimiento: MÉXICO
C.U.R.P: MARD790119MMCLML06
 #Curp en monospace
R.F.C: MARD790119857
 #Rfc en monospace
Homoclave: 857

HADES CHK
"""

from typing import Dict, Any

CSV_CORREGIDO = "personal_federalizado_corregido.csv"


def _split_rfc_homoclave(rfc: str):
    """Separa RFC base y homoclave. RFC persona fisica = 13 posiciones."""
    rfc = (rfc or "").strip()
    if len(rfc) == 13:
        return rfc[:-3], rfc[-3:]
    return rfc, ""


def format_rfc_output(record: Dict[str, Any]) -> str:
    """Formatea un registro de la BD corregida segun el diseno especificado."""

    nombre = record.get("Nombre", "No disponible") or "No disponible"
    paterno = record.get("Apellido_Paterno", "No disponible") or "No disponible"
    materno = record.get("Apellido_Materno", "No disponible") or "No disponible"

    fecha_nac = record.get("Fecha_Nacimiento", "No disponible") or "No disponible"
    if fecha_nac and "/" in str(fecha_nac):
        try:
            from datetime import datetime
            dt = datetime.strptime(str(fecha_nac), "%d/%m/%Y")
            fecha_nac = dt.strftime("%d-%m-%Y")
        except Exception:
            pass

    edad = record.get("Edad_Actual", "No disponible")
    sexo = str(record.get("Sexo", "No disponible") or "No disponible").upper()
    estado_nac = record.get("Estado_Nacimiento", "No disponible") or "No disponible"
    curp = record.get("CURP", "No disponible") or "No disponible"
    rfc_base, homoclave = _split_rfc_homoclave(str(record.get("RFC", "")))

    output = f"""HADES CHK

Nombre: {nombre}
Paterno: {paterno}
Materno: {materno}

Edad: {edad}
Sexo: {sexo}
Fecha de nacimiento: {fecha_nac}
Estado de nacimiento: {estado_nac}
C.U.R.P: {curp}
R.F.C: {rfc_base}
Homoclave: {homoclave}

HADES CHK"""

    return output


def format_rfc_html(record: Dict[str, Any]) -> str:
    """Formatea un registro en HTML con blockquote y monospace para CURP y RFC."""

    def escape_html(text: str) -> str:
        if not text:
            return ""
        return (
            str(text).replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    nombre = record.get("Nombre", "No disponible") or "No disponible"
    paterno = record.get("Apellido_Paterno", "No disponible") or "No disponible"
    materno = record.get("Apellido_Materno", "No disponible") or "No disponible"

    fecha_nac = record.get("Fecha_Nacimiento", "No disponible") or "No disponible"
    if fecha_nac and "/" in str(fecha_nac):
        try:
            from datetime import datetime
            dt = datetime.strptime(str(fecha_nac), "%d/%m/%Y")
            fecha_nac = dt.strftime("%d-%m-%Y")
        except Exception:
            pass

    edad = record.get("Edad_Actual", "No disponible")
    sexo = str(record.get("Sexo", "No disponible") or "No disponible").upper()
    estado_nac = record.get("Estado_Nacimiento", "No disponible") or "No disponible"
    curp = record.get("CURP", "No disponible") or "No disponible"
    rfc_base, homoclave = _split_rfc_homoclave(str(record.get("RFC", "")))

    html = f"""<blockquote><b>HADES CHK</b>

Nombre: {escape_html(nombre)}
Paterno: {escape_html(paterno)}
Materno: {escape_html(materno)}

Edad: {escape_html(str(edad))}
Sexo: {escape_html(str(sexo))}
Fecha de nacimiento: {escape_html(str(fecha_nac))}
Estado de nacimiento: {escape_html(str(estado_nac))}
C.U.R.P: <code>{escape_html(str(curp))}</code>
R.F.C: <code>{escape_html(str(rfc_base))}</code>
Homoclave: {escape_html(str(homoclave))}

HADES CHK</blockquote>"""

    return html


if __name__ == "__main__":
    import csv
    from pathlib import Path

    csv_path = Path(__file__).parent / CSV_CORREGIDO

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Validacion_Nombre") == "ok":
                sample = row
                break

    print("=== FORMATO DE SALIDA (PLANO) ===")
    print(format_rfc_output(sample))
    print("\n=== FORMATO DE SALIDA (HTML) ===")
    print(format_rfc_html(sample))
