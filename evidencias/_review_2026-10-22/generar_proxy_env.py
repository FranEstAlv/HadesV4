"""Genera las variables de proxy para la MAQUINA 2.

Uso (Windows):
    python generar_proxy_env.py smart-y0y22rnoyvhd TU_PASSWORD mx 50
    python generar_proxy_env.py smart-y0y22rnoyvhd TU_PASSWORD us 98

Salida: las tres variables, listas para pegar en el .env de la maquina 2.
El formato lleva 'http://' explicito porque es el UNICO que entienden sin
tocar codigo tanto el CLI (normalizeProxy) como el bot (_parse_proxies).
"""
import secrets
import string
import sys

HOST = "geo.iproyal.com:12321"


def sesion_id(n=8):
    """Id de sesion alfanumerico (IPRoyal: solo letras y numeros)."""
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(n))


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        print("Ejemplo: python generar_proxy_env.py smart-y0y22rnoyvhd MI_PASSWORD mx 50")
        return 2

    usuario = sys.argv[1]
    password = sys.argv[2]
    pais = sys.argv[3].lower()
    cantidad = int(sys.argv[4]) if len(sys.argv) > 4 else 50

    lifetime = sys.argv[5] if len(sys.argv) > 5 else "10m"

    def entrada(session):
        return (
            f"http://{usuario}:{password}_country-{pais}"
            f"_session-{session}_lifetime-{lifetime}@geo.iproyal.com:12321"
        )

    mx = [entrada(sesion_id()) for _ in range(cantidad)]
    us = [entrada(sesion_id()) for _ in range(cantidad)]

    print(f"# ---- variables para la maquina 2 (pais={pais}) ----")
    print()
    print(f'PROXY_ROTATORY=http://{usuario}:{password}_country-{pais}@geo.iproyal.com:12321')
    print()
    print('PROXY_STICKY_MX="' + ",".join(mx) + '"')
    print()
    print('PROXY_STICKY_USA_3_MINUTES="' + ",".join(us) + '"')
    print()
    print()
    print("# ---------------------------------------------------------------------------")
    print("# Si el panel de smartproxy dice 'IP diferente cada vez' o 'Pais: Aleatorio',")
    print("# NO lo uses para Amazon: Arkose liga el token a la IP y ningun solver cierra")
    print("# el reto. Deja Pais fijo y Tiempo = sesion fija (10 min / 1 h).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
