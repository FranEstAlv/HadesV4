# Instalar el bot como servicio systemd, con sudo acotado, en un VPS Ubuntu 24.04 headless

**Esto aplica solo si el destino real es un VPS sin Docker** — si el
proyecto ya se despliega en contenedores, ver
`references/arquitectura-dos-procesos-docker.md` en su lugar; no son dos
pasos a hacer los dos, son dos formas alternativas según dónde vaya a
vivir el bot. Guía de instalación verificada en producción para correr
uno o dos
procesos de un bot (por ejemplo, bot Bot API + userbot MTProto, ver
`references/lecciones-botsc.md`) como servicios systemd nativos — sin
Docker — en un VPS Ubuntu 24.04 LTS sin entorno gráfico. Todo lo de acá
asume ese entorno; en otra distro los paths de binarios (`/usr/bin/systemctl`,
etc.) pueden cambiar.

## 0. Confirmá el entorno real antes de escribir nada de infraestructura

El error más caro de esta fase es escribir el `.service`/sudoers contra
una convención "típica" en vez del entorno real. Antes de crear un solo
archivo, confirmá con el usuario o inspeccionando el VPS:

- Ruta real del proyecto (`pwd` en el checkout real) — no asumas `/opt/<algo>`.
- Usuario de sistema real que va a correr el proceso — no asumas un
  usuario de servicio nuevo sin privilegios si el proyecto ya vive bajo
  la cuenta de un usuario existente.
- Nombre real del entorno virtual — `venv/` y `.venv/` son ambas
  convenciones comunes, y un `ExecStart=` apuntando al que no es falla en
  silencio con "No such file or directory" fácil de mal-diagnosticar.

Si cualquiera de estos tres datos es una suposición y no algo confirmado,
decilo explícitamente antes de proponer los archivos de abajo.

## 1. El archivo `.service`

Un servicio por proceso (no un solo `.service` que arranque dos procesos
con `&` o un supervisor casero — systemd ya hace de supervisor):

```ini
# /etc/systemd/system/<proyecto>-bot.service
[Unit]
Description=<Proyecto> - proceso bot (Bot API)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<usuario_sistema>
Group=<usuario_sistema>
WorkingDirectory=/home/<usuario_sistema>/<proyecto>
EnvironmentFile=/home/<usuario_sistema>/<proyecto>/.env
ExecStart=/home/<usuario_sistema>/<proyecto>/venv/bin/python -m bot.main
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/<usuario_sistema>/<proyecto>/data

[Install]
WantedBy=multi-user.target
```

Dos detalles de hardening que cuestan una vuelta si no se prevén:

- **`ProtectSystem=strict`** deja todo el sistema de archivos de solo
  lectura para el proceso salvo lo que se liste en `ReadWritePaths` — el
  directorio de datos del bot (SQLite, sesión, lo que escriba en runtime)
  tiene que estar ahí explícitamente, o el proceso arranca pero falla al
  primer intento de escritura.
- **`ProtectHome=read-only`** (no `true`) es la opción correcta cuando el
  proyecto vive dentro de `/home/<usuario>/...` (común si el proceso
  corre bajo la cuenta de un usuario real, no una cuenta de servicio
  dedicada) — `ProtectHome=true` a secas deja **todo** `/home`
  inaccesible, incluida la carpeta del propio proyecto.

## 2. La regla de sudoers: acotada a comandos exactos, nunca `NOPASSWD:ALL`

Si la instalación/administración del servicio se hace desde una sesión
sin TTY interactiva (por ejemplo, una sesión de un asistente corriendo en
el propio VPS), cualquier `sudo <comando>` se cuelga esperando una
contraseña que nadie va a tipear. La solución no es correr todo como root
ni deshabilitar el pedido de contraseña en general — es una regla de
sudoers con `NOPASSWD` limitada a los comandos exactos que hacen falta,
nada más:

```
# /etc/sudoers.d/<proyecto> — instalar SIEMPRE con:
#   sudo visudo -f /etc/sudoers.d/<proyecto>
# (valida la sintaxis antes de guardar; nunca editar este archivo con un
# editor de texto suelto, un error de sintaxis acá puede romper sudo por
# completo para todo el sistema)

Cmnd_Alias PROYECTO_SYSTEMCTL = /usr/bin/systemctl start <proyecto>-bot.service, \
    /usr/bin/systemctl start <proyecto>-userbot.service, \
    /usr/bin/systemctl stop <proyecto>-bot.service, \
    /usr/bin/systemctl stop <proyecto>-userbot.service, \
    /usr/bin/systemctl restart <proyecto>-bot.service, \
    /usr/bin/systemctl restart <proyecto>-userbot.service, \
    /usr/bin/systemctl status <proyecto>-bot.service --no-pager, \
    /usr/bin/systemctl status <proyecto>-userbot.service --no-pager, \
    /usr/bin/systemctl enable <proyecto>-bot.service, \
    /usr/bin/systemctl enable <proyecto>-userbot.service, \
    /usr/bin/systemctl disable <proyecto>-bot.service, \
    /usr/bin/systemctl disable <proyecto>-userbot.service, \
    /usr/bin/systemctl daemon-reload

Cmnd_Alias PROYECTO_JOURNAL = /usr/bin/journalctl -u <proyecto>-bot.service --no-pager, \
    /usr/bin/journalctl -u <proyecto>-userbot.service --no-pager, \
    /usr/bin/journalctl -u <proyecto>-bot.service -f, \
    /usr/bin/journalctl -u <proyecto>-userbot.service -f

<usuario_sistema> ALL=(root) NOPASSWD: PROYECTO_SYSTEMCTL, PROYECTO_JOURNAL
```

Por qué está armado así y no con un wildcard más corto:

- **Nada de `systemctl *` ni `journalctl *`** — un alias que matchee
  "cualquier unit" le da a quien tenga esa regla la capacidad de
  reiniciar/inspeccionar **cualquier servicio del sistema**, PostgreSQL
  incluido si corre como servicio en el mismo VPS. El alias lista cada
  unit por nombre exacto, a propósito.
- **`sudoers` matchea la línea de comando completa, no por partes.** Una
  regla escrita para `systemctl enable <proyecto>-bot.service` (una sola
  unit) **no** matchea `systemctl enable <proyecto>-bot.service
  <proyecto>-userbot.service` (dos units juntas en el mismo comando) —
  son literales distintos. Si el flujo de instalación va a habilitar más
  de una unit, o invocá los comandos de a uno, o agregá explícitamente la
  combinación exacta como otra entrada del alias.
- Si además hace falta copiar los `.service` a `/etc/systemd/system/`
  como parte de la instalación, agregá esa copia puntual (ruta origen y
  destino exactas) como su propio alias — nunca un `cp *` genérico.

**Corrección de seguridad real sobre una versión anterior de esta misma
guía** — no es teoría, se encontró en una revisión de rutina de una regla
ya instalada: `systemctl status <unit>` y `journalctl -u <unit>` (sin
`--no-pager`) abren un pager interactivo (`less`) cuando corren en una
terminal, y desde `less` se escapa a una shell con `!bash`. Como el
comando corrió vía `sudo`, esa shell sale con privilegios de **root** —
es una técnica de escalación de privilegios catalogada (buscar
"systemctl"/"journalctl" en GTFOBins), no algo hipotético. Un wildcard de
argumentos como `journalctl -u <unit> *` es exactamente lo que habilita
esto, porque cualquier argumento adicional (o ninguno) sigue matcheando
la regla. La regla de arriba ya lo evita: `--no-pager` es parte literal
del comando permitido (no un flag opcional que alguien podría omitir), y
`-f` (follow) es la única otra forma exacta permitida porque no abre
pager por diseño de systemd. **Antes de dar por buena cualquier regla de
sudoers nueva, preguntar explícitamente: ¿alguna de las formas que esto
permite invoca un pager, un editor, o cualquier otro programa
interactivo?** — es el chequeo que faltó la primera vez.

## 3. Instalar y arrancar

```bash
# 1. Copiar las units (una vez confirmada la regla de sudoers de arriba)
sudo cp /home/<usuario>/<proyecto>/deploy/systemd/<proyecto>-bot.service /etc/systemd/system/
sudo cp /home/<usuario>/<proyecto>/deploy/systemd/<proyecto>-userbot.service /etc/systemd/system/

# 2. Releer la configuración de systemd
sudo systemctl daemon-reload

# 3. Habilitar (arranque automático al reiniciar el VPS) e iniciar
sudo systemctl enable --now <proyecto>-bot.service
sudo systemctl enable --now <proyecto>-userbot.service

# 4. Confirmar que están vivos, no solo que el comando no falló
sudo systemctl status <proyecto>-bot.service --no-pager
sudo journalctl -u <proyecto>-bot.service -n 50 --no-pager
```

## 4. Antes de cualquier prueba manual en caliente: parar el servicio primero

Si en algún momento hace falta correr el proceso a mano para probar un
cambio (`python -m bot.main` directo, sin systemd), **parar el servicio
systemd correspondiente antes**, sin excepción:

```bash
sudo systemctl stop <proyecto>-bot.service
python -m bot.main   # ahora sí, sin competir con el mismo BOT_TOKEN
# ... probar ...
sudo systemctl start <proyecto>-bot.service   # al terminar
```

Saltarse esto reproduce exactamente el error de "dos pollers con el mismo
token" del Paso 2 de este skill (`TelegramConflictError`/`Conflict:
terminated by other getUpdates request`) — no por un bug del bot, sino
por apuro durante una prueba.
