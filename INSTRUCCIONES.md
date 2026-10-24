# GenCookie Hades - Instrucciones de instalacion

## 1. Requisitos

- Python 3.12
- venv
- Un VPS con IP publica fija
- Un dominio propio (recomendado .cyou, .shop, etc.)

## 2. Instalacion

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Configuracion

Copia `.env.example` a `.env` y completa tus datos:

```bash
cp .env.example .env
```

Variables obligatorias:
- `TELEGRAM_BOT_TOKEN`: token de @BotFather
- `SUPER_ADMINS`: tu ID de Telegram
- `PROXY_ROTATORY`: string de proxy (ver ejemplo en .env.example)
- `EMAILAPI_DOMAINS`: dominio para correos temporales

## 4. DNS del dominio de correo

Para que EmailApi reciba correos desde Internet, el dominio configurado en `EMAILAPI_DOMAINS` necesita estos registros DNS:

| Host | Tipo | Valor | Prioridad |
|------|------|-------|-----------|
| `@` | `A` | `IP_DE_TU_VPS` | — |
| `mail` | `A` | `IP_DE_TU_VPS` | — |
| `@` | `MX` | `mail.tudominio.cyou` | `10` |
| `@` | `TXT` | `v=spf1 ip4:IP_DE_TU_VPS ~all` | — |

Tambien se requiere redirigir el puerto 25 al puerto SMTP de EmailApi (1025 por defecto). Ejemplo con iptables:

```bash
sudo iptables -t nat -A PREROUTING -p tcp --dport 25 -j REDIRECT --to-port 1025
```

## 5. Servicios systemd

Crear los servicios `gencookie-bot.service` y `emailapi-local.service` apuntando a las rutas de este proyecto.

```bash
sudo systemctl enable --now emailapi-local.service
sudo systemctl enable --now gencookie-bot.service
```

## 6. Uso

Comandos ocultos del bot:
- `/gencookie` - panel para generar cuenta Amazon
- `/cook` - atajo a MX
- `/batman` - panel de admin (solo super admins)

## 7. Rotacion de dominios

Cuando un dominio empiece a fallar por exceso de registros, compra uno nuevo, configura los mismos registros DNS y agregalo a `EMAILAPI_DOMAINS` separado por comas:

```bash
EMAILAPI_DOMAINS=dominio1.cyou,dominio2.cyou,dominio3.cyou
```

EmailApi elegira uno al azar en cada generacion.
