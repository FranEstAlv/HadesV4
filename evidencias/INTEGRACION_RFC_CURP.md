# Guía de Integración del Comando /rfc para Hades

## Visión General

Este documento describe la forma correcta de integrar el nuevo comando `/rfc` al bot de Telegram Hades, que entrega datos de RFC y CURP a partir del archivo CSV `personal_federalizado_corregido.csv` (generado desde el PDF maestro con `pipeline_federalizado.py`).

> **Nota 2026-11-02**: el CSV viejo `personal_federalizado_datos_completos.csv` (20,587 filas, nombres mal parseados por regex de línea) queda obsoleto. No usarlo.

---

## 1. Estructura de Archivos

### Archivos relacionados:

- `evidencias/rfc_diseño.py` - Diseño del formato de salida y lógica de formateo
- `evidencias/pipeline_federalizado.py` - Pipeline único PDF → BD (reemplaza `plu.py`/`pdf.py`)
- `evidencias/personal_federalizado_corregido.csv` - Base de datos: 24,617 personas únicas
- `evidencias/personal_federalizado_crudo.csv` - Extracción intermedia (auditoría)
- `evidencias/reporte_bd_corregida.json` - Reporte de validación
- `bot/handlers/rfc.py` - Handler del comando /rfc (por crear)

---

## 2. Requisitos Técnicos

### 2.1. Permisos de Acceso

- **Membresía requerida**: Cualquier membresía activa (semanal, quincenal, mensual, Olimpo)
- **Admin**: Siempre puede usar el comando sin costo
- **Costo por uso**: 25 créditos

### 2.2. Cooldown

- Tiempo de espera entre usos: **5 minutos**
- Evita saturación del comando y abuso

### 2.3. Fuentes de Datos

- **Ruta del CSV**: `~/GenCookie/GenCookie/evidencias/personal_federalizado_corregido.csv`
- **Origen**: PDF maestro `/home/olimpo/gates/personal_federalizado.pdf` (910 páginas, FAEB 2do. trimestre) procesado con `evidencias/pipeline_federalizado.py`
- **Formato CSV**: RFC, CURP, Nombre, Apellido_Paterno, Apellido_Materno, Fecha_Nacimiento, Estado_Nacimiento, Edad_Actual, Sexo, Clave_CT, Turno, Entidad_Federativa, Validacion_Nombre
- **Cantidad de registros**: 24,617 personas únicas (una fila por CURP; duplicados por múltiples plazas eliminados)
- **Validación**: 96.7% con puntaje 8/8 RFC↔CURP↔nombre (`ok` + `ok_homonimia`); resto marcado `revisar` en columna `Validacion_Nombre` — el handler debe filtrar solo `ok*`

---

## 3. Formato de Salida

### 3.1. Estructura HTML (para el bot)

```
<blockquote><b>HADES CHK</b>

👤Nombre: Delia Isabel
🧑‍🍼Paterno: Maldonado
🤱Materno: Ramirez

🎂Edad: 47
🔥Sexo: MUJER
👶Fecha de nacimiento: 19-01-1979
🌎Estado de nacimiento: MÉXICO
💳C.U.R.P: <code>MARD790119MMCLML06</code>
💳R.F.C: <code>MARD790119857</code>
💳Homoclave: 857

HADES CHK</blockquote>
```

### 3.2. Estructura Plana (texto sin formato)

```
HADES CHK

👤Nombre: Delia Isabel
🧑‍🍼Paterno: Maldonado
🤱Materno: Ramirez

🎂Edad: 47
🔥Sexo: MUJER
👶Fecha de nacimiento: 19-01-1979
🌎Estado de nacimiento: MÉXICO
💳C.U.R.P: MARD790119MMCLML06
 #Curp en monospace
💳R.F.C: MARD790119857
 #Rfc en monospace 
💳Homoclave: 857

HADES CHK
```

### 3.3. Mapeo de Datos

| Campo CSV | Campo de Salida | Notas |
|-----------|-----------------|-------|
| Nombre | 👤Nombre | Nombre(s) de pila, sin cargo |
| Apellido_Paterno | 🧑‍🍼Paterno | Incluye compuestos (DE LA ISLA, DE JESUS) |
| Apellido_Materno | 🤱Materno | Vacío = X en RFC[2] y CURP[2] (confirmado) |
| Edad_Actual | 🎂Edad | Calculada a 2026-11-02 desde CURP |
| Sexo | 🔥Sexo | Derivado de CURP[10], MAYÚSCULAS |
| Fecha_Nacimiento | 👶Fecha de nacimiento | Derivada de CURP[4:10], formato DD-MM-YYYY |
| Estado_Nacimiento | 🌎Estado de nacimiento | Derivado de CURP[11:13] |
| CURP | 💳C.U.R.P | En `<code>` |
| RFC | 💳R.F.C | En `<code>`, sin homoclave |
| RFC (últimos 3 dígitos) | 💳Homoclave | Separada del RFC |

### 3.4. Regenerar la BD (si llega un PDF nuevo)

```bash
/home/olimpo/gates/venv/bin/python evidencias/pipeline_federalizado.py \
  --pdf /home/olimpo/gates/personal_federalizado.pdf
# Reasumible: si se interrumpe, correr el mismo comando de nuevo
# (checkpoint por página). Solo reconstruye con --solo-reparsear.
```

---

## 4. Paso a Paso de Integración

### Paso 1: Crear el Handler del Bot

**Ruta**: `bot/handlers/rfc.py`

```python
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

router = Router()

@router.message(Command("rfc"))
async def cmd_rfc(message: Message):
    # Implementar lógica del comando
    pass

def register_rfc_handlers(dp):
    dp.include_router(router)
```

### Paso 2: Registrar en main.py

**Ruta**: `bot/main.py`

Agregar al inicio del archivo:

```python
from bot.handlers.rfc import register_rfc_handlers
```

Agregar en `create_dispatcher()`:

```python
def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    # ... otros handlers ...
    register_rfc_handlers(dp)  # ← AGREGAR AQUÍ
    register_start_handlers(dp)
    return dp
```

### Paso 3: Formateador de Datos

**Ruta**: `bot/utils/rfc_formatter.py` (reutilizar `evidencias/rfc_diseño.py`)

Importar funciones de formateo en el handler:

```python
from evidencias.rfc_diseño import format_rfc_html
```

### Paso 4: Validaciones

Implementar en el handler:

1. **Verificar cooldown**: 5 minutos por usuario
2. **Verificar membresía**: O admin
3. **Verificar créditos**: 25 créditos por uso
4. **Cargar CSV**: Leer registro aleatorio
5. **Formatear respuesta**: Usar `format_rfc_html()`
6. **Enviar mensaje**: Con ParseMode.HTML

---

## 5. Manejo de Errores

### Errores Esperados:

| Error | Mensaje al Usuario | Acción del Sistema |
|-------|-------------------|-------------------|
| Cooldown activo | "⏱️ Estás en cooldown. Espera X minuto(s)..." | No cobrar créditos |
| Sin membresía | "❌ ACCESO RESTRINGIDO. Necesitas membresía activa" | No cobrar créditos |
| Sin créditos | "❌ CRÉDITOS INSUFICIENTES. Costo: 25, Tienes: X" | No cobrar créditos |
| CSV no encontrado | "❌ ERROR TEMPORAL. Datos no disponibles" | Log de error |
| CSV vacío | "❌ ERROR TEMPORAL. Datos no disponibles" | Log de error |
| Error inesperado | "❌ ERROR INESPERADO. Inténtalo más tarde" | Log detallado |

---

## 6. Logs y Monitorización

### Logs Requeridos:

```python
logger.info(f"Usuario {user_id} obtuvo RFC/CURP. Costo: 25 créditos")
logger.error(f"CSV no encontrado: {CSV_PATH}")
logger.error(f"Error al enviar mensaje de RFC: {e}")
logger.error(f"Error inesperado en /rfc: {e}", exc_info=True)
```

### Estadísticas Recomendadas:

- Total de usos por día
- Créditos generados por día
- Porcentaje de errores
- Usuarios que abusan del cooldown

---

## 7. Notas de Seguridad

### 7.1. Rutas de Archivos

- **NUNCA usar rutas relativas** en producción
- Usar `Path(__file__).parent.parent / "evidencias" / "personal_federalizado_datos_completos.csv"`
- Validar que el archivo exista antes de cargarlo

### 7.2. Permisos de Lectura

- El bot debe tener permisos de lectura en `evidencias/`
- Verificar: `chmod 644 evidencias/personal_federalizado_datos_completos.csv`

### 7.3. Datos Sensibles

- RFC y CURP son datos sensibles
- No guardar en logs sin enmascarar
- No almacenar en base de datos sin encriptación

---

## 8. Pruebas

### 8.1. Prueba Básica

```bash
# Verificar que el CSV sea legible
python evidencias/rfc_diseño.py
```

### 8.2. Prueba Manual en Bot

```bash
# Iniciar bot y probar
curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -d "chat_id=<USER_ID>" \
  -d "text=/rfc" \
  -d "parse_mode=HTML"
```

### 8.3. Prueba de Errores

| Escenario | Comando | Resultado Esperado |
|-----------|---------|-------------------|
| Sin membresía | /rfc | Mensaje de acceso restringido |
| Sin créditos | /rfc | Mensaje de créditos insuficientes |
| Cooldown | /rfc (dos veces en 5 min) | Segundo mensaje rechazado |
| CSV corrupto | /rfc (con CSV vacío) | Mensaje de error temporal |

---

## 9. Mantenimiento

### 9.1. Actualizar CSV

1. Reemplazar `evidencias/personal_federalizado_datos_completos.csv`
2. Reiniciar bot: `sudo systemctl restart gencookie-bot.service`
3. Verificar logs: `journalctl -u gencookie-bot.service -n 50 --no-pager`

### 9.2. Ajustar Costo

**Ruta**: `bot/handlers/rfc.py`

```python
MODULE_COST = 25  # ← CAMBIAR ESTE VALOR
```

### 9.3. Ajustar Cooldown

**Ruta**: `bot/handlers/rfc.py`

```python
MODULE_COOLDOWN_MINUTES = 5  # ← CAMBIAR ESTE VALOR
```

---

## 10. Checklist de Implementación

- [ ] Copiar `evidencias/rfc_diseño.py` a `bot/utils/rfc_formatter.py`
- [ ] Crear `bot/handlers/rfc.py` con el handler del comando
- [ ] Agregar `register_rfc_handlers(dp)` en `bot/main.py`
- [ ] Validar rutas de archivos
- [ ] Implementar validaciones (cooldown, membresía, créditos)
- [ ] Implementar formateo HTML
- [ ] Agregar logging
- [ ] Probar en entorno de desarrollo
- [ ] Desplegar a producción
- [ ] Verificar logs después del primer uso

---

## 11. Comandos Relacionados

| Comando | Descripción | Costo |
|---------|-------------|-------|
| `/rfc` | Obtiene un registro aleatorio de RFC y CURP | 25 créditos |
| `/bin` | Consulta info de un BIN | Gratis |
| `/gen` | Genera tarjetas por Luhn | Gratis |

---

## 12. Referencias

- Diseño original: `evidencias/rfc_diseño.py`
- Pipeline PDF → BD: `evidencias/pipeline_federalizado.py` (reemplaza `/home/olimpo/gates/plu.py` y `/home/olimpo/gates/pdf.py`)
- Base de datos: `evidencias/personal_federalizado_corregido.csv`
- Crudo intermedio (auditoría): `evidencias/personal_federalizado_crudo.csv`
- Reporte de validación: `evidencias/reporte_bd_corregida.json`
- Formato de mensajes: `bot/utils/formatting.py` (función `escape_html`)
- Manejo de créditos: `bot/services/credits.py`
- Manejo de membresías: `bot/services/memberships.py`

---

**Versión**: 2.0
**Fecha**: 2026-11-02
**Autor**: Arquitecto de Bots
**Estado**: Diseño (sin implementar). BD corregida lista desde PDF maestro.
