# 🚀 Leviatan CHK API - Verificador de Tarjetas Amazon

**Versión:** 1.0  
**Tecnología:** Python Flask + MySQL  
**Estado:** ✅ Listo para Producción  
**Autor:** LeviatanChk  

---

## 📋 Descripción

API REST para verificar tarjetas de crédito en Amazon. Valida tarjetas usando cookies de sesión de Amazon y devuelve el estado de la tarjeta (Aprobada, Declinada o Error).

### Características Principales

- ✅ Validación de tarjetas de crédito en Amazon
- ✅ Verificación de cookies de sesión
- ✅ Detección de proxies
- ✅ Logging completo de transacciones
- ✅ Estadísticas en tiempo real
- ✅ Base de datos MySQL integrada
- ✅ API REST con JSON
- ✅ Manejo robusto de errores

---

## 🔧 Requisitos

- Python 3.8+
- MySQL 5.7+
- pip (gestor de paquetes)
- 512 MB RAM mínimo
- 1 GB almacenamiento

---

## 📦 Instalación Rápida

### 1. Clonar Proyecto
```bash
git clone https://github.com/tu-usuario/leviatan-chk-api.git
cd leviatan-chk-api
```

### 2. Crear Entorno Virtual
```bash
python3 -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
```

### 3. Instalar Dependencias
```bash
pip install -r requirements_api.txt
```

### 4. Configurar Base de Datos
```bash
# Ejecutar script SQL
mysql -u root -p < database.sql
```

### 5. Configurar Variables de Entorno
```bash
cp .env.example .env
# Editar .env con tus credenciales de MySQL
nano .env
```

### 6. Ejecutar API
```bash
python app.py
```

La API estará disponible en `http://localhost:5000`

---

## 🧪 Pruebas

### Verificar que está corriendo
```bash
curl http://localhost:5000/health
```

### Probar verificación de tarjeta
```bash
curl -X POST http://localhost:5000/amazon/leviatan \
  -H "Content-Type: application/json" \
  -d '{
    "card": "4532640527811643|12|2025|123",
    "cookies": "{\"ubid-main\": \"test\", \"session-id\": \"test\", \"i18n-prefs\": \"es_ES\", \"x-main\": \"test\", \"session-token\": \"test\"}"
  }'
```

### Suite completa de pruebas
```bash
python test_api_client.py
```

---

## 📚 Documentación de API

### Endpoint Principal: POST /amazon/leviatan

**Parámetros:**
```json
{
  "card": "numero|mes|año|cvv",
  "cookies": "cookies_de_amazon"
}
```

**Ejemplo:**
```json
{
  "card": "4532640527811643|12|2025|123",
  "cookies": "{\"ubid-main\": \"...\", \"session-id\": \"...\", ...}"
}
```

**Respuestas Posibles:**

#### ✅ Tarjeta Aprobada
```json
{
  "status": "✅ Approved",
  "response": "Tarjeta ccv Decline. (Removido: ✅)",
  "card_last_4": "1643",
  "transaction_id": "abc123",
  "timestamp": "2026-04-12T21:30:00"
}
```

#### ❌ Tarjeta Declinada
```json
{
  "status": "❌ Declined",
  "response": "Tarjeta Mala No Insistir. (Removido: ✅)",
  "card_last_4": "1643",
  "timestamp": "2026-04-12T21:30:00"
}
```

#### ⚠️ Error
```json
{
  "status": "⚠️ Error",
  "response": "Fallo al obtener Wallet de Amazon.",
  "timestamp": "2026-04-12T21:30:00"
}
```

---

## 🌐 Otros Endpoints

### GET /health
Verifica que la API está activa

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2026-04-12T21:30:00",
  "service": "Leviatan CHK API"
}
```

### GET /stats
Obtiene estadísticas de tarjetas verificadas

**Response:**
```json
{
  "total": 150,
  "approved": 45,
  "declined": 85,
  "errors": 20,
  "timestamp": "2026-04-12T21:30:00"
}
```

---

## 🚀 Despliegue en Producción

### Railway
```bash
# Conectar repositorio GitHub a Railway
# Las variables de entorno se configuran en el dashboard
```

### Heroku
```bash
heroku create leviatan-chk-api
git push heroku main
```

### VPS (DigitalOcean, AWS, etc.)
```bash
# Ver GUIA_INSTALACION_API.md para instrucciones completas
```

---

## 🔐 Seguridad

- ✅ Validación de tarjetas (16 dígitos, mes 1-12, año futuro, CVV 3-4 dígitos)
- ✅ Validación de cookies (mínimo 3 cookies requeridas)
- ✅ Detección y rechazo de proxies
- ✅ User-Agent obligatorio
- ✅ Logging de todas las transacciones
- ✅ Encriptación de cookies en base de datos
- ✅ Rate limiting (configurable)

---

## 📊 Estructura de Base de Datos

### Tablas Principales

**cards_checked** - Historial de tarjetas verificadas
- id, user_id, card_number, card_status, response_message, created_at

**amazon_sessions** - Sesiones de Amazon
- id, user_id, cookies_encrypted, is_valid, expires_at, created_at

**api_logs** - Logs de todas las peticiones
- id, user_id, endpoint, method, status_code, request_data, response_data, created_at

**statistics** - Estadísticas diarias
- id, date, total_checks, approved_count, declined_count, error_count

---

## 🛠️ Configuración

### Variables de Entorno (.env)

```
# Flask
FLASK_ENV=production
FLASK_DEBUG=False
PORT=5000

# MySQL
MYSQL_HOST=localhost
MYSQL_USER=leviatan_api
MYSQL_PASSWORD=tu_contraseña
MYSQL_DB=leviatan_chk

# API
API_SECRET_KEY=tu_clave_secreta

# Seguridad
ENABLE_PROXY_CHECK=True
MAX_REQUESTS_PER_HOUR=100
```

---

## 📝 Formatos Aceptados

### Formato de Tarjeta
```
numero|mes|año|cvv
Ejemplo: 4532640527811643|12|2025|123

- numero: 16 dígitos
- mes: 01-12
- año: 4 dígitos (futuro)
- cvv: 3-4 dígitos
```

### Formato de Cookies
```json
{
  "ubid-main": "valor",
  "session-id": "valor",
  "i18n-prefs": "valor",
  "x-main": "valor",
  "session-token": "valor"
}
```

O como string:
```
ubid-main=valor; session-id=valor; i18n-prefs=valor; ...
```

---

## 🔍 Solución de Problemas

### "Connection refused"
```bash
# Verificar que MySQL está corriendo
sudo systemctl status mysql
sudo systemctl start mysql
```

### "No module named 'flask'"
```bash
# Instalar dependencias
pip install -r requirements_api.txt
```

### "Cookies no detectados"
- Verificar que las cookies son válidas
- Asegurarse que la sesión de Amazon está activa
- Obtener nuevas cookies si están expiradas

---

## 📞 Contacto y Soporte

- **Telegram:** @LeviatanChk
- **GitHub Issues:** Reportar bugs y sugerencias
- **Email:** contacto@leviatan-chk.site

---

## 📄 Archivos Incluidos

```
leviatan-chk-api/
├── app.py                      # Aplicación principal Flask
├── database.sql                # Script de base de datos
├── requirements_api.txt        # Dependencias Python
├── .env.example                # Ejemplo de variables de entorno
├── test_api_client.py          # Suite de pruebas
├── GUIA_INSTALACION_API.md     # Guía de instalación completa
├── ANALISIS_API_LEVIATAN.md    # Análisis de la API
├── README_API.md               # Este archivo
└── .gitignore                  # Archivos a ignorar en Git
```

---

## 📊 Estadísticas

- **Endpoints:** 3 principales + 1 health check
- **Métodos HTTP:** GET, POST
- **Formatos:** JSON
- **Base de Datos:** MySQL
- **Lenguaje:** Python 3.8+
- **Framework:** Flask 2.3+

---

## 🎯 Roadmap Futuro

- [ ] Autenticación con API Key
- [ ] Rate limiting avanzado
- [ ] Webhooks para notificaciones
- [ ] Dashboard de administración
- [ ] Soporte para múltiples métodos de pago
- [ ] Caché de resultados
- [ ] Análisis de fraude

---

## 📜 Licencia

Este proyecto es de código abierto. Ver LICENSE para más detalles.

---

## ⚠️ Disclaimer

Esta API es solo para propósitos educativos. El usuario es responsable de cumplir con todas las leyes y regulaciones aplicables. No se permite usar esta API para actividades ilícitas.

---

**Versión:** 1.0  
**Última Actualización:** 12 de Abril de 2026  
**Estado:** ✅ Listo para Producción  

🚀 **¡Gracias por usar Leviatan CHK API!**
