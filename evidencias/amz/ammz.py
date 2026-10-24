import asyncio
import os
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

from browser_use import Agent, BrowserSession
from browser_use.browser import BrowserProfile
from browser_use.llm import ChatOpenAI


# Se puede ajustar el agent para trabajar con openIA, Anthropic, OpenRouter, OpenCode. El agent puede ser automático para trabajar con un archivo GraphQL tipo .query o interactivo para ir haciendo ajustes en vivo. 

load_dotenv()

VENICE_BASE_URL = "https://api.venice.ai/api/v1"
VENICE_MODEL = "zai-org-glm-5-2"

BROWSERLESS_WS_URL = os.getenv(
    "BROWSERLESS_WS_URL",
    "wss://production-sfo.browserless.io",
)

BROWSERLESS_TOKEN = os.getenv("BROWSERLESS_TOKEN")
VENICE_API_KEY = os.getenv("VENICE_API_KEY")



def validate_environment():
    if not BROWSERLESS_TOKEN:
        raise RuntimeError(
            "Falta BROWSERLESS_TOKEN en el archivo .env"
        )

    if not VENICE_API_KEY:
        raise RuntimeError(
            "Falta VENICE_API_KEY en el archivo .env"
        )



def test_venice():
    print("Probando Venice AI...")

    response = requests.post(
        f"{VENICE_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {VENICE_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": VENICE_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Reply only with: OK",
                }
            ],
            "max_tokens": 10,
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    answer = data["choices"][0]["message"]["content"]

    print(f"Venice AI: {answer}")



def create_browserless_url():
    params = {
        "token": BROWSERLESS_TOKEN,

        "proxy": "residential",

        "proxyCountry": "MX",

        "solveCaptchas": "true",

        "integrations": "browseruse",
    }

    return (
        f"{BROWSERLESS_WS_URL}/chromium/stealth?"
        f"{urlencode(params)}"
    )



def show_help():
    print(
        """
## NAVEGADOR CLI CON CHROME EN STEALTH, PROXY, CAPTCHA RESOLVE 

### DESARROLLADO POR @MrMxyzptlk04 ###

Escribe cualquier instrucción que tu modelo pueda soportar:

  > abre esta pag

  > ve al login

  > dime qué información aparece en esta página

  > busca el producto lowcost

Comandos especiales:

  /help
      Mostrar esta ayuda.

  /status
      Mostrar estado de la sesión.

  /manual
      Pausar el uso del agente.
      La sesión Browserless permanece activa mientras
      el programa siga ejecutándose.

  /agent
      Volver al modo agente.

  /exit
      Cerrar el programa.

## DIVIERTETE. 
"""
    )


# ============================================================
# MAIN
# ============================================================

async def main():
    validate_environment()


    try:
        test_venice()
    except Exception as e:
        print()
        print("No fue posible conectar con Venice AI.")
        print(f"{type(e).__name__}: {e}")
        return


    llm = ChatOpenAI(
        model=VENICE_MODEL,
        api_key=VENICE_API_KEY,
        base_url=VENICE_BASE_URL,
    )


    cdp_url = create_browserless_url()

    browser_profile = BrowserProfile(
        viewport_size={
            "width": 1920,
            "height": 1080,
        },
        headless=True,
    )

    browser_session = BrowserSession(
        cdp_url=cdp_url,
        browser_profile=browser_profile,
    )

    print()
    print("Conectando con Browserless...")

    await browser_session.start()

    print("Browserless conectado.")
    print("Sesión remota iniciada.")


    mode = "agent"

    show_help()

    try:
        while True:

            print()
            print(f"[modo: {mode}]")

            command = await asyncio.to_thread(
                input,
                "> ",
            )

            command = command.strip()

            if not command:
                continue


            if command.lower() in {
                "/exit",
                "exit",
                "quit",
                "/quit",
            }:
                print("Cerrando sesión...")
                break


            if command.lower() == "/help":
                show_help()
                continue


            if command.lower() == "/status":
                print()
                print("Estado:")
                print("  Browserless: conectado")
                print("  Venice: conectado")
                print(f"  Modelo: {VENICE_MODEL}")
                print(f"  Modo: {mode}")
                continue


            if command.lower() == "/manual":
                mode = "manual"

                print()
                print("Modo manual activado.")
                print()
                print(
                    "El agente no ejecutará instrucciones "
                    "hasta que escribas /agent."
                )
                print()
                print(
                    "La sesión Browserless continúa activa."
                )
                print(
                    "Puedes utilizar las herramientas Live/Debug "
                    "de Browserless para inspeccionar o controlar "
                    "esta sesión cuando estén configuradas."
                )

                continue


            if command.lower() == "/agent":
                mode = "agent"

                print()
                print("Modo agente activado.")

                continue


            if mode == "manual":
                print()
                print(
                    "El agente está pausado."
                )
                print(
                    "Escribe /agent para devolverle el control."
                )
                continue


            print()
            print("Ejecutando instrucción...")
            print()

            try:
                agent = Agent(
                    task=command,
                    llm=llm,
                    browser_session=browser_session,
                )

                result = await agent.run()

                print()
                print("=" * 60)
                print("RESULTADO")
                print("=" * 60)
                print()
                print(result)

            except Exception as e:
                print()
                print("ERROR DEL AGENTE")
                print(
                    f"{type(e).__name__}: {e}"
                )

                print()
                print(
                    "La consola seguirá activa para que "
                    "puedas intentar otra instrucción."
                )

    finally:

        print()
        print("Cerrando Browserless...")

        try:
            await browser_session.stop()
        except Exception:
            pass

        print("Sesión finalizada.")



if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print()
        print("Programa detenido.")
