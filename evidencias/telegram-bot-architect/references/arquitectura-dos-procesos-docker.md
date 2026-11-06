# Dos procesos, una imagen, un volumen: el patrón de infraestructura más simple para separar procesos

**Una forma posible de armar esto, no la única.** Si tu proyecto no
necesita dos procesos separados (Paso 0 de este skill), nada de este
archivo aplica. Si sí los necesita y el destino es un VPS con Docker
disponible, esta es la forma más simple verificada en producción — no la
obligatoria si tu caso tiene otras restricciones (necesitás escalar cada
proceso por separado, ya tenés otra convención de despliegue, etc.).

`references/lecciones-mictlan-alfa1.md` explica **por qué** conviene un
segundo proceso separado (créditos de flood-control independientes). Este
archivo documenta la forma más simple de llevarlo a `docker-compose.yml`
cuando los dos procesos son de un mismo proyecto y no necesitan escalar
por separado: **misma imagen, dos servicios, un volumen compartido para la
base de datos**. Verificado en producción (una app web + un bot de
Telegram que solo maneja confirmaciones de login, compartiendo SQLite).

```yaml
services:
  web:
    build: .
    restart: unless-stopped
    env_file: .env
    environment:
      DB_PATH: /app/data/app.db
    ports:
      - "127.0.0.1:8501:8501"
    volumes:
      - app_data:/app/data
    command: streamlit run app.py --server.port=8501 --server.address=0.0.0.0

  bot:
    build: .                     # <- misma imagen que "web", nada duplicado
    restart: unless-stopped
    env_file: .env
    environment:
      DB_PATH: /app/data/app.db  # <- mismo volumen, misma DB, otro proceso
    volumes:
      - app_data:/app/data
    command: python bot_auth.py
    healthcheck:
      disable: true              # ver por qué abajo

volumes:
  app_data:
```

## El healthcheck heredado del Dockerfile puede mentir sobre el segundo servicio

Si el `Dockerfile` define un `HEALTHCHECK` (por ejemplo, un `curl`/request
al endpoint de salud de la app web), **ambos servicios lo heredan** al
construirse de la misma imagen — Docker no sabe que el segundo servicio
corre un comando distinto (`command: python bot_auth.py`) que nunca abre
ese puerto. El resultado observado en producción: el contenedor del bot
queda marcado permanentemente `unhealthy` en `docker ps`, aunque el
proceso esté funcionando perfectamente — porque Docker sigue preguntando
por un endpoint HTTP que ese comando en particular jamás sirvió, y jamás
va a servir.

No es un bug del bot ni señal de un problema real: es el healthcheck
correcto para el servicio equivocado. La solución es `healthcheck:
disable: true` explícito en el servicio que no expone ese endpoint —
diagnosticar esto primero (mirar qué healthcheck realmente corre
`docker inspect <contenedor> --format='{{json .State.Health}}'`) antes de
asumir cualquier otra causa para un "unhealthy" persistente con memoria y
CPU estables.

## ¿Este patrón sirve para un sitio web con login vía Telegram? Sí — el framework web es una decisión aparte

Si lo que se está armando es un sitio (no un bot) que necesita "iniciar
sesión con Telegram" (sin contraseña, confirmando por botón desde un chat
privado con el bot), la pregunta no es "¿Docker o no?" sino **"¿el
servidor web y el bot pueden ser el mismo proceso?"** — y la respuesta es
no, por la misma razón de siempre: el bot necesita un loop de
polling/webhook propio y continuo, y eso no convive bien adentro del
mismo proceso que atiende requests HTTP del sitio, sin importar qué
framework web se use.

El flujo típico (verificado en producción) es:

1. El sitio muestra un botón/link que abre un chat privado con el bot
   (`t.me/tu_bot?start=<token_de_sesion>`).
2. El usuario confirma en Telegram con un botón inline (`login_ok:<token>`).
3. El **proceso bot** escribe en la base de datos compartida que ese
   `token_de_sesion` quedó autenticado como tal `user_id`.
4. El **proceso web** lee esa misma tabla (por polling corto desde el
   navegador, o quedándose esperando la próxima carga de página) para
   completar el login del lado del sitio.

Los dos procesos nunca se llaman directo entre sí — se coordinan solo por
la tabla de sesiones en la base compartida, mismo principio que
`references/lecciones-botsc.md` documenta para el canal de tareas entre
bot y userbot.

**Dónde sí importa el framework web, por lecciones reales de un proyecto
así**: si el framework reejecuta el script completo en cada actualización
de cada sesión activa (el modelo de Streamlit, por ejemplo), cualquier
caché o estado a nivel de módulo sin límite (`_cache: dict = {}` fuera de
una función) se multiplica muchísimas veces por hora — y así es como una
fuga de memoria de unos pocos KB por entrada termina siendo gigabytes por
día en producción. Esto no es un defecto exclusivo de una librería en
particular, es una consecuencia directa de "reejecutar todo el script
seguido" como modelo de ejecución. La separación en dos procesos (web +
bot) es la decisión correcta en cualquier caso; si el proyecto es nuevo y
no hay una razón de peso para atarse a un framework de ese tipo, uno
convencional de request/response (FastAPI, Flask) evita esta categoría
completa de fuga de entrada — un handler de request no vuelve a correr
"solo por que hay una sesión abierta", corre una vez por request y listo.
Si ya existe una base de código grande sobre el framework de reruns, la
mitigación no es cambiar de framework a mitad de proyecto — es la regla
ya conocida: cualquier caché/estado a nivel de módulo necesita un límite
de tamaño y una purga por TTL, nunca crecer sin tope.

## Enrutamiento de `CallbackQuery` con un solo handler por familia de prefijo, en PTB

Mismo principio que en `references/panel-admin-paginacion-roles.md`
(namespace propio por sección, un dispatcher por familia, nunca un
handler por botón), expresado con la API de PTB en vez de aiogram —
`CallbackQueryHandler` admite un `pattern` de regex que decide qué
handler recibe cada callback, sin que el bot tenga que parsear él mismo
cuál de varios handlers debería atenderlo:

```python
def main() -> None:
    app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()

    # Cada familia de botones tiene su propio prefijo Y su propio
    # handler -- "login_ok"/"login_no" nunca puede matchear por accidente
    # contra el patrón de "admin_kick"/"admin_ban", ni contra "panel:".
    app.add_handler(CallbackQueryHandler(on_login_callback, pattern=r"^login_(ok|no):"))
    app.add_handler(CallbackQueryHandler(on_admin_callback, pattern=r"^admin_(kick|ban):"))
    app.add_handler(CallbackQueryHandler(on_panel_callback, pattern=r"^panel:"))

    app.run_polling()
```

PTB prueba los `pattern` en el orden en que se registraron y entrega el
callback al primer handler cuyo patrón matchea — por eso un prefijo
específico (`^login_(ok|no):`) tiene que ser inequívoco frente a uno más
amplio (`^panel:`) para no depender del orden de registro para que
funcione bien. Es la misma disciplina de namespace que evita los
"empalmes" de `callback_data` en cualquier librería, solo que acá se
declara en el `pattern` del handler en vez de en un `if` dentro de un
dispatcher único.
