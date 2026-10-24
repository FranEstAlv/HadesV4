from __future__ import annotations

import imaplib
import email
import re
import time
import random
import subprocess
from appium import webdriver
from appium.options import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


DEVICE_IP       = "192.168.1.X"
DEVICE_PORT     = 5555
APP_PACKAGE     = "com.ejemplo.app"
APP_ACTIVITY    = ".MainActivity"

PROXY_HOST      = "proxy.ejemplo.com"
PROXY_PORT      = 8080

IMAP_HOST       = "imap.gmail.com"
IMAP_USER       = "correo@gmail.com"
IMAP_PASS       = "app_password_aqui"
IMAP_ASUNTO     = "verifica"    

APPIUM_SERVER   = "http://localhost:4723"


REGISTRO = {
    "correo":    "nuevo_usuario@gmail.com",
    "telefono":  "5512345678",     
    "password":  "Password123!",
    "nombre":    "Juan Pérez",
}



def conectar_dispositivo():
    subprocess.run(["adb", "connect", f"{DEVICE_IP}:{DEVICE_PORT}"], check=True)
    time.sleep(2)


def limpiar_app():
    subprocess.run(["adb", "shell", "pm", "clear", APP_PACKAGE], check=True)


def set_proxy(host: str, port: int):
    subprocess.run([
        "adb", "shell", "settings", "put", "global",
        "http_proxy", f"{host}:{port}"
    ], check=True)


def quitar_proxy():
    subprocess.run([
        "adb", "shell", "settings", "put", "global",
        "http_proxy", ":0"
    ], check=True)



def crear_driver() -> webdriver.Remote:
    options = UiAutomator2Options()
    options.platform_name          = "Android"
    options.device_name            = f"{DEVICE_IP}:{DEVICE_PORT}"
    options.app_package            = APP_PACKAGE
    options.app_activity           = APP_ACTIVITY
    options.no_reset               = False
    options.auto_grant_permissions = True
    return webdriver.Remote(APPIUM_SERVER, options=options)


def encontrar(driver, by, valor, timeout=15):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, valor))
    )


def escribir(elemento, texto: str, delay: float = 0.05):
    elemento.click()
    time.sleep(random.uniform(0.3, 0.7))
    for char in texto:
        elemento.send_keys(char)
        time.sleep(random.uniform(0.03, delay))



def esperar_otp(timeout: int = 90) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST)
            mail.login(IMAP_USER, IMAP_PASS)
            mail.select("inbox")

            _, ids = mail.search(None, f'(UNSEEN SUBJECT "{IMAP_ASUNTO}")')
            if ids[0]:
                ultimo = ids[0].split()[-1]
                _, data = mail.fetch(ultimo, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])

                cuerpo = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            cuerpo = part.get_payload(decode=True).decode(errors="replace")
                            break
                else:
                    cuerpo = msg.get_payload(decode=True).decode(errors="replace")

                match = re.search(r"\b(\d{4,8})\b", cuerpo)
                if match:
                    mail.logout()
                    return match.group(1)

            mail.logout()

        except Exception as exc:
            print(f"  [imap] error: {exc}")

        time.sleep(5)

    return None



def flujo_registro(driver: webdriver.Remote) -> bool:

    boton_registro = encontrar(driver, AppiumBy.ID, "com.ejemplo.app:id/boton_registro")
    boton_registro.click()
    time.sleep(random.uniform(1.0, 2.0))

    campo_correo = encontrar(driver, AppiumBy.ID, "com.ejemplo.app:id/campo_correo")
    escribir(campo_correo, REGISTRO["correo"])

    time.sleep(random.uniform(0.5, 1.2))

    campo_password = encontrar(driver, AppiumBy.ID, "com.ejemplo.app:id/campo_password")
    escribir(campo_password, REGISTRO["password"])

    time.sleep(random.uniform(0.5, 1.2))

    campo_nombre = encontrar(driver, AppiumBy.ID, "com.ejemplo.app:id/campo_nombre")
    escribir(campo_nombre, REGISTRO["nombre"])

    time.sleep(random.uniform(0.8, 1.5))

    driver.hide_keyboard()
    time.sleep(0.5)

    boton_enviar = encontrar(driver, AppiumBy.ID, "com.ejemplo.app:id/boton_enviar")
    boton_enviar.click()

    time.sleep(3)
    contextos = driver.contexts
    print(f"  [appium] contextos: {contextos}")

    en_webview = False
    if any("WEBVIEW" in c for c in contextos):
        webview = next(c for c in contextos if "WEBVIEW" in c)
        driver.switch_to.context(webview)
        en_webview = True
        print(f"  [appium] cambiado a WebView")

    print("  [imap] esperando correo de verificación...")
    codigo = esperar_otp(timeout=90)
    if not codigo:
        print("  [imap] OTP no recibido")
        return False
    print(f"  [imap] código recibido: {codigo}")

    campo_otp = encontrar(driver, AppiumBy.ID, "com.ejemplo.app:id/campo_otp")
    escribir(campo_otp, codigo, delay=0.1)

    time.sleep(random.uniform(0.5, 1.0))

    boton_verificar = encontrar(driver, AppiumBy.ID, "com.ejemplo.app:id/boton_verificar")
    boton_verificar.click()

    time.sleep(3)

    if en_webview:
        driver.switch_to.context("NATIVE_APP")

    return True



def main():
    print("── conectando dispositivo")
    conectar_dispositivo()

    print("── limpiando app")
    limpiar_app()

    print("── configurando proxy")
    set_proxy(PROXY_HOST, PROXY_PORT)

    driver = None
    try:
        print("── iniciando Appium")
        driver = crear_driver()
        time.sleep(3)

        print("── ejecutando registro")
        exito = flujo_registro(driver)
        print(f"── resultado: {'✓ registro exitoso' if exito else '✗ registro fallido'}")

    except Exception as exc:
        print(f"── error: {exc}")

    finally:
        if driver:
            driver.quit()
        quitar_proxy()
        print("── ciclo terminado")


if __name__ == "__main__":
    main()