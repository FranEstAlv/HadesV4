# AGENTS.md — Notas para futuras modificaciones

## Idioma y tono

- Todo el texto visible a usuarios debe usar **español de México**.
- **Prohibido el voseo argentino**: no usar `querés`, `tenés`, `probá`, `enviame`, `indicame`, `seleccioná`, `acá`, etc.
- Usar formas neutras mexicanas:
  - `quieres` en vez de `querés`
  - `tienes` en vez de `tenés`
  - `Selecciona` en vez de `Seleccioná`
  - `Envíame` / `Indícame` (con tilde) en vez de `enviame` / `indicame`
  - `Desde aquí` en vez de `Desde acá`
  - `prueba` / `inténtalo de nuevo` en vez de `probá`

## Comandos ocultos y seguridad

- Los comandos de administración son **ocultos** y deben mantener **silencio absoluto** si un usuario no autorizado los ejecuta.
- No responder nada (ni mensajes de error) cuando un no-admin use un comando oculto.
- El comando de panel de admin actual es `/batman`.

## Jerarquía de administradores

- Existen dos niveles internos: **super admin** y **admin**.
- En los paneles no se debe mostrar ni mencionar la diferencia.
- Solo los super admins pueden asignar o retirar administradores.
- Super admins actuales (acceso ilimitado): `6060544328`, `5531198491`.

## Persistencia temporal

- Hasta que se conecte la base de datos PostgreSQL, usuarios y admins normales se guardan en:
  - `data/users.json`
  - `data/admins.json`

## Convenciones de código

- Usar aiogram 3.
- Los botones inline usan el parámetro `style` (`primary`, `success`, `danger`) para colores.
- Los textos de perfil/precios/contacto usan `<blockquote>` en HTML parse mode.

## Despliegue y procesos — obligatorio systemd

**Todo proceso de este proyecto debe correr como servicio systemd.** No usar `python bot/main.py`, `nohup`, `&`, `screen`, `tmux` ni ningún otro método para mantener procesos vivos. Si un servicio no existe, se crea antes de encender cualquier cosa.

### Servicios registrados

| Servicio | Unit file | Descripción | Directorio de trabajo | Ejecutable |
|---|---|---|---|---|
| `gencookie-bot.service` | `/etc/systemd/system/gencookie-bot.service` | Bot principal de Telegram (Hades) | `/home/olimpo/GenCookie/GenCookie` | `/home/olimpo/GenCookie/GenCookie/venv/bin/python -m bot.main` |
| `emailapi-local.service` | `/etc/systemd/system/emailapi-local.service` | EmailApi temporal actual | `/home/olimpo/GenCookie/GenCookie/EmailApi` | `/home/olimpo/GenCookie/GenCookie/venv/bin/python /home/olimpo/GenCookie/GenCookie/EmailApi/main.py` |
| `emailapi.service` | `/etc/systemd/system/emailapi.service` | EmailApi apuntando a directorio antiguo (`/home/olimpo/gencookie/GenCookie`) | `/home/olimpo/gencookie/GenCookie/EmailApi` | `/home/olimpo/gencookie/GenCookie/venv/bin/python /home/olimpo/gencookie/GenCookie/EmailApi/main.py` |

> **Importante:** el servicio activo de EmailApi es `emailapi-local.service`. `emailapi.service` apunta a una copia anterior del proyecto y debe considerarse obsoleto a menos que se decida migrar explícitamente.

### Comandos de operación

```bash
# Recargar units después de editar un .service
sudo systemctl daemon-reload

# Reiniciar servicios (obligatorio tras cambios de código)
sudo systemctl restart gencookie-bot.service
sudo systemctl restart emailapi-local.service

# Ver estado
systemctl status gencookie-bot.service
systemctl status emailapi-local.service

# Ver logs
journalctl -u gencookie-bot.service -n 50 --no-pager
journalctl -u emailapi-local.service -n 50 --no-pager

# Ver logs en tiempo real
journalctl -u gencookie-bot.service -f
journalctl -u emailapi-local.service -f
```

### Reglas sin excepción

1. **Siempre reiniciar el servicio correspondiente después de modificar código.** Compilar con `py_compile` no alcanza; el proceso en memoria sigue corriendo la versión anterior hasta el restart.
2. **Nunca matar procesos con `pkill` o `kill` directamente** salvo emergencia. Usar `systemctl stop` o `systemctl restart`.
3. **No levantar segunda instancia del mismo servicio** con el mismo token de Telegram. Eso genera `TelegramConflictError` y deja al bot sin respuesta.
4. **Si se crea un nuevo proceso** (por ejemplo, un worker o un segundo bot), debe tener su propio archivo `.service` en `/etc/systemd/system/` y su propio nombre.
5. **EmailApi no se corre a mano** con `python EmailApi/main.py`. Siempre usar `systemctl restart emailapi-local.service`.

### Habilitar un servicio nuevo

```bash
sudo systemctl enable --now nombre-del-servicio.service
```

## Carpeta de evidencias — prohibición absoluta

**La carpeta `evidencias/` nunca debe borrarse.** Contiene scripts de referencia, versiones de respaldo y material de trazabilidad que este proyecto necesita para validar cambios, auditaciones y recuperación de lógica histórica. Bajo ninguna circunstancia se debe eliminar, renombrar ni mover su contenido total o parcialmente. Si hace falta integrar un script de `evidencias/` al código principal, se copia/consolida, no se borra el original.

## 2Captcha — protocolo correcto (verificado 2026-10-10)

- Saldo y validación de llave usan estilo CapSolver: `POST https://api.2captcha.com/getBalance` con JSON `{"clientKey": "..."}`.
- Respuesta válida: `{"errorId":0,"balance":...}`.
- El protocolo viejo (`GET .../getBalance?key=...&json=1`) devuelve `ERROR_KEY_DOES_NOT_EXIST` aunque la llave sirva y tenga saldo. Es un falso negativo: nunca declarar vencida una llave solo por esa vía sin probar el POST JSON primero.
