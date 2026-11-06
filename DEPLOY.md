# DEPLOY — GenCookie Hades

Guía de operación del bot (systemd, sin Docker). Actualizado en la auditoría 2026-11-05.

## Servicios

| Servicio | Qué es | Unit file |
|---|---|---|
| `gencookie-bot.service` | Bot principal de Telegram | `/etc/systemd/system/gencookie-bot.service` |
| `emailapi-local.service` | EmailApi (correo temporal) | `/etc/systemd/system/emailapi-local.service` |
| `emailapi.service` | **OBSOLETO** — apunta a `/home/olimpo/gencookie/GenCookie` (copia vieja). `disabled` + `inactive`. No arrancar. | — |

## Comandos

```bash
# Reiniciar tras cambios de código (OBLIGATORIO — py_compile no basta)
sudo systemctl restart gencookie-bot.service
sudo systemctl restart emailapi-local.service

# Estado
systemctl status gencookie-bot.service
systemctl status emailapi-local.service

# Logs
journalctl -u gencookie-bot.service -n 50 --no-pager
journalctl -u emailapi-local.service -n 50 --no-pager

# Tiempo real
journalctl -u gencookie-bot.service -f
```

## Reglas

1. **Siempre reiniciar tras modificar código.** El proceso en memoria sigue corriendo la versión anterior.
2. **Nunca `pkill`/`kill`.** Usar `systemctl stop/restart`.
3. **No levantar segunda instancia** con el mismo token → `TelegramConflictError`.
4. **Nuevo proceso = nuevo `.service`**, nunca a mano con `python ... &`.
5. **EmailApi** solo vía `systemctl restart emailapi-local.service`.

## Diagnóstico

- `/status` (solo admins, silencio absoluto si no): BD, backup, env críticas, EmailApi.
- `/batman`: panel de administración (solo admins, silencioso si no).
- Backup BD: `data/backups/bot_database.YYYY-MM-DD.db` (diario, rotación 7).
- Rama auditoría: `auditoria-2026-11-05`. Reporte de avance: `avance-auditoria.md`.
- Hallazgos y plan original: `correccion.md`.

## Variables de entorno (`.env`, gitignored)

Ver `.env.example` (sincronizado 2026-11-05). Críticas: `TELEGRAM_BOT_TOKEN`,
`SUPER_ADMIN_IDS` (o legado `SUPER_ADMINS`), `TELCEL_DECRYPT_PASSWORD`,
`TELCEL_ENCRYPT_PASSWORD`, `TELCEL_T1_BEARER`, `TELCEL_MITELCEL_BEARER`,
costos `*_COST`, `AMAZON_*`, `IPROYAL_*`, `*_API_KEY` de captchas.

## Rollback

```bash
cd /home/olimpo/GenCookie/GenCookie
git stash list          # cambios sin commitear previos a la auditoría
git diff --stat         # qué tocó la auditoría
sudo systemctl restart gencookie-bot.service
```

## Pendientes no bloqueantes

- Rotar secrets legacy de Telcel (quitar fallback en `gates.py`).
- Agregar `5531198491` a `SUPER_ADMIN_IDS` del `.env` si aplica.
- Regenerar `requirements.txt` con `pip freeze` (versiones fijadas vs instaladas difieren).
- Definir precios finales `TELCEL_*_COST` con Fernando.
