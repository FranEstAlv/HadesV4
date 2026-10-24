"""Cliente de numeros virtuales por SMS (HeroSMS) para el paso de telefono del CVF de Amazon.

Usa la API heredada compatible con SMS-Activate:

    GET https://hero-sms.com/stubs/handler_api.php?action=<accion>&api_key=<clave>

Respuestas relevantes:
    getBalance -> ACCESS_BALANCE:<monto>
    getNumber  -> ACCESS_NUMBER:<id>:<numero>
    getStatus  -> STATUS_WAIT_CODE | STATUS_WAIT_RETRY:<codigo> | STATUS_OK:<codigo> | STATUS_CANCEL
    setStatus  -> 3=reenviar SMS, 6=completar, 8=cancelar (reembolsa)

La clave se lee de HEROSMS_API_KEY. Nunca se escribe en logs.
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

logger = logging.getLogger(__name__)

HERO_BASE = "https://hero-sms.com/stubs/handler_api.php"

# Numeros que no recibieron el SMS: HeroSMS los recicla (un numero cancelado
# vuelve al pool y se vuelve a vender) y al estar liberado ya no recibe
# mensajes. Se guardan para no volver a comprarlos.
_BAD_NUMBERS_FILE = Path(__file__).resolve().parent / "data" / "sms_bad_numbers.json"
_bad_lock = threading.Lock()

# Codigo de servicio de Amazon en HeroSMS (getServicesList -> code "am").
SERVICE_AMAZON = "am"

# Id de pais en HeroSMS (getCountries) para los paises que maneja el script.
COUNTRY_IDS = {
    "US": 187,
    "CA": 36,
    "MX": 54,
    "IT": 86,
    "ES": 56,
    "BR": 73,
}

# Prefijo telefonico por pais para normalizar el numero que entrega el proveedor.
CALLING_CODES = {
    "US": "1",
    "CA": "1",
    "MX": "52",
    "IT": "39",
    "ES": "34",
    "BR": "55",
}


class SmsError(Exception):
    """Error del proveedor de SMS (saldo, stock, formato o tiempo)."""


class HeroSms:
    """Cliente minimo de HeroSMS: comprar numero, esperar codigo, cerrar pedido."""

    def __init__(self, api_key=None, service=SERVICE_AMAZON):
        self.api_key = (api_key or os.getenv("HEROSMS_API_KEY", "")).strip()
        self.service = service

    def available(self):
        return bool(self.api_key)

    def _get(self, action, **params):
        if not self.api_key:
            raise SmsError("El proveedor de SMS no esta configurado")
        import requests

        query = {"action": action, "api_key": self.api_key}
        for k, v in params.items():
            if v is not None:
                query[k] = v
        try:
            resp = requests.get(HERO_BASE, params=query, timeout=25)
        except Exception as exc:
            raise SmsError(f"No se pudo contactar al proveedor de SMS: {exc}") from None
        if resp.status_code >= 400:
            raise SmsError(f"El proveedor de SMS respondio {resp.status_code}")
        text = (resp.text or "").strip()
        # El proveedor devuelve {"title": "NO_NUMBERS", ...} con HTTP 200.
        try:
            payload = json.loads(text)
        except Exception:
            return text
        if isinstance(payload, dict) and payload.get("title"):
            detail = payload.get("details") or ""
            raise SmsError(f"{payload.get('title')}: {detail}".strip(": "))
        return text

    def _load_bad(self):
        try:
            with open(_BAD_NUMBERS_FILE, "r", encoding="utf-8") as fh:
                return {str(x) for x in json.load(fh)}
        except Exception:
            return set()

    def _save_bad(self, numbers):
        try:
            _BAD_NUMBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_BAD_NUMBERS_FILE, "w", encoding="utf-8") as fh:
                json.dump(sorted(numbers), fh, indent=1)
        except Exception as exc:
            logger.warning("No se pudo guardar la lista de numeros fallidos: %s", exc)

    def mark_bad(self, number):
        """Marca un numero como inservible (no recibio el SMS)."""
        digits = re.sub(r"\D", "", number or "")
        if not digits:
            return
        with _bad_lock:
            bad = self._load_bad()
            if digits not in bad:
                bad.add(digits)
                self._save_bad(bad)
                logger.info("Numero marcado como fallido: %s", digits)

    def balance(self):
        raw = self._get("getBalance")
        if not raw.startswith("ACCESS_BALANCE:"):
            raise SmsError("El proveedor de SMS no devolvio el saldo")
        try:
            return float(raw.split(":", 1)[1])
        except ValueError:
            raise SmsError("El saldo del proveedor de SMS es ilegible") from None

    def buy(self, country, tries=4):
        """Compra un numero y devuelve (id_del_pedido, numero).

        HeroSMS recicla los numeros cancelados: al volver al pool ya no
        reciben SMS. Por eso se descartan los que fallaron antes.
        """
        cc = (country or "").upper()
        cid = COUNTRY_IDS.get(cc)
        if not cid:
            raise SmsError(f"No hay numeros de SMS disponibles para {cc}")
        with _bad_lock:
            bad = self._load_bad()
        last = None
        for _ in range(tries):
            raw = self._get("getNumber", service=self.service, country=cid)
            if not raw.startswith("ACCESS_NUMBER:"):
                raise SmsError(f"El proveedor de SMS no entrego numero ({raw[:60]})")
            try:
                _, activation_id, number = raw.split(":", 2)
            except ValueError:
                raise SmsError("Respuesta de numero con formato inesperado") from None
            if not activation_id.strip().isdigit():
                raise SmsError("Id de pedido de SMS invalido")
            local = self._normalize_number(number, cc)
            last = (activation_id.strip(), local)
            if local not in bad:
                return last
            logger.warning("El proveedor devolvio un numero que ya habia fallado (%s); se pide otro", local)
            self.cancel_later(activation_id.strip())
            time.sleep(1)
        if last:
            logger.warning("Se devuelve el numero %s aunque este en la lista de fallidos", last[1])
            return last
        raise SmsError("El proveedor de SMS no entrego numero")

    def status(self, activation_id):
        """Devuelve (estado, codigo). Estado: WAIT | OK | CANCEL."""
        raw = self._get("getStatus", id=activation_id)
        if raw.startswith("STATUS_OK:"):
            return "OK", raw.split(":", 1)[1].strip()
        if raw.startswith("STATUS_WAIT_RETRY:"):
            return "WAIT", raw.split(":", 1)[1].strip()
        if raw == "STATUS_CANCEL":
            return "CANCEL", ""
        return "WAIT", ""

    def finish(self, activation_id):
        """Marca el pedido como completado (conserva el gasto)."""
        self._get("setStatus", id=activation_id, status=6)

    def cancel(self, activation_id):
        """Cancela el pedido (reembolsa si el proveedor lo permite).

        HeroSMS exige que pasen al menos 2 minutos desde la compra
        (EARLY_CANCEL_DENIED); si cancela antes, el pedido se cancela en
        segundo plano para no bloquear el flujo.
        """
        try:
            self._get("setStatus", id=activation_id, status=8)
        except SmsError as exc:
            if "EARLY_CANCEL_DENIED" in str(exc):
                self.cancel_later(activation_id)
            else:
                logger.warning("No se pudo cancelar el pedido de SMS: %s", exc)
        except Exception as exc:
            logger.warning("No se pudo cancelar el pedido de SMS: %s", exc)

    def cancel_later(self, activation_id, delay=135):
        """Cancela el pedido en segundo plano cuando ya paso el minimo."""
        def _worker():
            time.sleep(delay)
            try:
                self._get("setStatus", id=activation_id, status=8)
            except Exception as exc:
                logger.warning("Cancelacion diferida del pedido de SMS fallo: %s", exc)

        threading.Thread(target=_worker, daemon=True).start()

    def wait_code(self, activation_id, timeout=180, interval=10):
        """Espera a que llegue el SMS con el codigo."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state, code = self.status(activation_id)
            if state == "OK" and code:
                return code
            if state == "CANCEL":
                raise SmsError("El proveedor de SMS cancelo el numero")
            time.sleep(interval)
        raise SmsError("No llego el codigo por SMS a tiempo")

    @staticmethod
    def _normalize_number(number, country):
        """Deja solo digitos (el prefijo del pais se conserva si viene)."""
        digits = re.sub(r"\D", "", number or "")
        if not digits:
            raise SmsError("El proveedor de SMS entrego un numero vacio")
        return digits

    @staticmethod
    def local_number(number, country):
        """Quita el prefijo del pais: Amazon pide el numero local.

        El formulario de Amazon separa el pais (cvf_phone_cc) del numero
        (cvf_phone_num), asi que se envia solo el numero local.
        """
        digits = re.sub(r"\D", "", number or "")
        cc = CALLING_CODES.get((country or "").upper(), "")
        if cc and digits.startswith(cc) and len(digits) > 10:
            digits = digits[len(cc):]
        return digits
