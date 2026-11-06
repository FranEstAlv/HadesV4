#!/usr/bin/env python3
"""TEMPORAL: vuelca los logs de Medusa a un txt para revision posterior.

Filtra el journal de gencookie-bot.service por lineas de Medusa
(bot.gates.medusa y bot.handlers.md) y guarda:
  1. Resumen: una linea por tarjeta probada (status, clase, codigo, tiempo).
  2. Log completo filtrado, en orden cronologico.

Uso:
  python3 tools/medusa_logs.py [--hours 12] [--output RUTA]
  python3 tools/medusa_logs.py --hours 48   # rango mas amplio
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MX_TZ = ZoneInfo("America/Mexico_City")
UNIT = "gencookie-bot.service"
DEFAULT_OUT_DIR = Path("data/logs")

_PAT_TARJETA = re.compile(
    r"tarjeta\s+(\S+)\s+->\s+(.+?)\s+\[(.+?)\]\s+code=(.*?)\s+en\s+([\d.]+)s"
)
_PAT_HANDLER = re.compile(r"Medusa\s+(\S+)\s+->\s+(.+?)\s+en\s+([\d.]+)s")


def _run_journal(hours: float) -> list[str]:
    cmd = [
        "journalctl",
        "-u", UNIT,
        "--since", f"{hours} hours ago",
        "--no-pager",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as exc:
        print(f"No se pudo leer journalctl: {exc}", file=sys.stderr)
        return []
    lines = []
    for line in (out.stdout or "").splitlines():
        if "medusa" in line.lower():
            lines.append(line)
    return lines


def _summarize(lines: list[str]) -> tuple[list[str], dict[str, int]]:
    rows: list[str] = []
    counts: dict[str, int] = {}
    for line in lines:
        m = _PAT_TARJETA.search(line)
        if m:
            masked, status, cls, code, secs = m.groups()
            ts = line[:15]
            rows.append(
                f"{ts} | {masked} | {status.strip()} | [{cls.strip()}] "
                f"| {code.strip()[:100]} | {secs}s"
            )
            key = status.strip().split()[0]
            counts[key] = counts.get(key, 0) + 1
            continue
        m2 = _PAT_HANDLER.search(line)
        if m2:
            masked, status, secs = m2.groups()
            ts = line[:15]
            rows.append(f"{ts} | {masked} | {status.strip()} | {secs}s")
    return rows, counts


def main() -> int:
    ap = argparse.ArgumentParser(description="Volcado temporal de logs Medusa a txt.")
    ap.add_argument("--hours", type=float, default=12,
                    help="Rango hacia atras en horas (default: 12).")
    ap.add_argument("--output", default="",
                    help="Ruta del txt (default: data/logs/medusa_logs_<fecha>.txt).")
    args = ap.parse_args()

    now_mx = datetime.now(MX_TZ)
    out_path = (
        Path(args.output)
        if args.output
        else DEFAULT_OUT_DIR / f"medusa_logs_{now_mx:%Y%m%d_%H%M%S}.txt"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = _run_journal(args.hours)
    rows, counts = _summarize(lines)

    total_line = (
        f"Live={counts.get('✅', 0)} Dead={counts.get('❌', 0)} "
        f"Error={counts.get('⚠️', 0)}"
    )
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(f"MEDUSA LOGS — generado {now_mx:%Y-%m-%d %H:%M:%S} (America/Mexico_City)\n")
        fh.write(f"Unidad: {UNIT} | Rango: ultimas {args.hours} horas\n")
        fh.write(f"Lineas Medusa: {len(lines)} | Tarjetas: {len(rows)} | {total_line}\n")
        fh.write("=" * 100 + "\n\n")
        fh.write("--- RESUMEN POR TARJETA ---\n")
        if rows:
            fh.write("\n".join(rows) + "\n")
        else:
            fh.write("(sin veredictos en el rango)\n")
        fh.write("\n--- LOG COMPLETO (filtrado Medusa) ---\n")
        fh.write("\n".join(lines) + "\n")

    print(f"OK: {out_path} ({len(lines)} lineas, {len(rows)} tarjetas, {total_line})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
