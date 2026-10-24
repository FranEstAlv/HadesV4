# Guía Completa - API Leviatan CHK

**Versión:** 1.0  
**Fecha:** 12 de Abril de 2026  
**Tecnología:** Python Flask + MySQL

---

## 📋 Tabla de Contenidos

1. [Requisitos](#requisitos)
2. [Instalación Local](#instalación-local)
3. [Configuración de Base de Datos](#configuración-de-base-de-datos)
4. [Ejecución Local](#ejecución-local)
5. [Pruebas](#pruebas)
6. [Despliegue en Producción](#despliegue-en-producción)
7. [Documentación de Endpoints](#documentación-de-endpoints)
8. [Solución de Problemas](#solución-de-problemas)

---

## 🔧 Requisitos

### Sistema Operativo
- Linux (Ubuntu 20.04+) o macOS
- Windows con WSL2

### Software Requerido
- Python 3.8+
- MySQL 5.7+ o MariaDB 10.3+
- pip (gestor de paquetes de Python)
- git (opcional, para versionamiento)

### Requisitos de Hardware
- CPU: 1+ núcleo
- RAM: 512 MB mínimo
- Almacenamiento: 1 GB

---

## 📦 Instalación Local

### Paso 1: Clonar o Descargar el Proyecto

```bash
# Opción 1: Clonar desde Git
git clone https://github.com/tu-usuario/leviatan-chk-api.git
cd leviatan-chk-api

# Opción 2: Descargar archivos manualmente
# Descargar: app.py, database.sql, requirements_api.txt, .env.example
```

### Paso 2: Crear Entorno Virtual

```bash
# Crear entorno virtual
python3 -m venv venv

# Activar entorno virtual
# En Linux/macOS:
source venv/bin/activate

# En Windows:
venv\Scripts\activate
```

### Paso 3: Instalar Dependencias

```bash
# Actualizar pip
pip install --upgrade pip

# Instalar dependencias
pip install -r requirements_api.txt
```

### Paso 4: Configurar Variables de Entorno

```bash
# Copiar archivo de ejemplo
cp .env.example .env

# Editar .env con tus valores
nano .env
```

**Contenido de .env:**
```
FLASK_ENV=development
FLASK_DEBUG=True
PORT=5000

MYSQL_HOST=localhost
MYSQL_USER=leviatan_api
MYSQL_PASSWORD=tu_contraseña_aqui
MYSQL_DB=leviatan_chk

API_SECRET_KEY=tu_clave_secreta_aqui
```

---

## 🗄️ Configuración de Base de Datos

### Paso 1: Instalar MySQL

**En Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install mysql-server
sudo mysql_secure_installation
```

**En macOS (con Homebrew):**
```bash
brew install mysql
brew services start mysql
mysql_secure_installation
```

### Paso 2: Crear Base de Datos

```bash
# Conectar a MySQL
mysql -u root -p

# Ejecutar el script SQL
source database.sql;

# Verificar que se creó
SHOW DATABASES;
USE leviatan_chk;
SHOW TABLES;

# Salir
EXIT;
```

### Paso 3: Verificar Conexión

```bash
# Probar conexión con el usuario de la API
mysql -u leviatan_api -p leviatan_chk

# Si conecta correctamente, está listo
```

---

## 🚀 Ejecución Local

### Opción 1: Desarrollo (con recargas automáticas)

```bash
# Activar entorno virtual
source venv/bin/activate

# Ejecutar en modo desarrollo
python app.py
```

**Salida esperada:**
```
 * Serving Flask app 'app'
 * Debug mode: on
 * Running on http://127.0.0.1:5000
```

### Opción 2: Producción (con Gunicorn)

```bash
# Instalar Gunicorn
pip install gunicorn

# Ejecutar con Gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### Verificar que está corriendo

```bash
# En otra terminal
curl http://localhost:5000/health

# Respuesta esperada:
# {"status":"ok","timestamp":"2026-04-12T21:30:00","service":"Leviatan CHK API"}
```

---

## 🧪 Pruebas

### Ejecutar Suite de Pruebas

```bash
# En otra terminal (con la API corriendo)
python test_api_client.py
```

### Pruebas Manuales con cURL

#### 1. Health Check
```bash
curl http://localhost:5000/health
```

#### 2. Verificar Tarjeta
```bash
curl -X POST http://localhost:5000/amazon/leviatan \
  -H "Content-Type: application/json" \
  -d '{
    "card": "4532640527811643|12|2025|123",
    "cookies": "{\"ubid-main\": \"test\", \"session-id\": \"test\", \"i18n-prefs\": \"es_ES\", \"x-main\": \"test\", \"session-token\": \"test\"}"
  }'
```

#### 3. Obtener Estadísticas
```bash
curl http://localhost:5000/stats
```

### Pruebas con Postman

1. **Descargar Postman** desde https://www.postman.com/downloads/
2. **Crear nueva colección** "Leviatan CHK"
3. **Agregar requests:**

**Request 1: Health Check**
- Método: GET
- URL: `http://localhost:5000/health`

**Request 2: Check Card**
- Método: POST
- URL: `http://localhost:5000/amazon/leviatan`
- Headers: `Content-Type: application/json`
- Body (JSON):
```json
{
  "card": "4532640527811643|12|2025|123",
  "cookies": "{\"ubid-main\": \"test\", \"session-id\": \"test\", \"i18n-prefs\": \"es_ES\", \"x-main\": \"test\", \"session-token\": \"test\"}"
}
```

---

## 🌐 Despliegue en Producción

### Opción 1: Desplegar en Railway

#### 1. Preparar Repositorio Git

```bash
# Inicializar Git
git init
git add .
git commit -m "Initial commit: Leviatan CHK API v1.0"

# Crear repositorio en GitHub
# Luego:
git remote add origin https://github.com/tu-usuario/leviatan-chk-api.git
git push -u origin main
```

#### 2. Conectar a Railway

1. Ir a https://railway.app
2. Hacer login con GitHub
3. Crear nuevo proyecto
4. Seleccionar "Deploy from GitHub"
5. Autorizar y seleccionar tu repositorio

#### 3. Configurar Variables en Railway

En el dashboard de Railway:
1. Ir a "Variables"
2. Agregar:

```
FLASK_ENV=production
FLASK_DEBUG=False
PORT=5000

MYSQL_HOST=tu_host_mysql
MYSQL_USER=leviatan_api
MYSQL_PASSWORD=tu_contraseña
MYSQL_DB=leviatan_chk

API_SECRET_KEY=tu_clave_secreta_muy_segura
```

#### 4. Crear Procfile

```
web: gunicorn -w 4 -b 0.0.0.0:$PORT app:app
```

#### 5. Desplegar

Railway redesplegará automáticamente cuando hagas push a GitHub.

### Opción 2: Desplegar en Heroku

```bash
# Instalar Heroku CLI
curl https://cli-assets.heroku.com/install.sh | sh

# Hacer login
heroku login

# Crear aplicación
heroku create leviatan-chk-api

# Agregar base de datos MySQL
heroku addons:create cleardb:ignite

# Configurar variables
heroku config:set FLASK_ENV=production
heroku config:set MYSQL_HOST=tu_host
heroku config:set MYSQL_USER=tu_usuario
heroku config:set MYSQL_PASSWORD=tu_contraseña
heroku config:set MYSQL_DB=leviatan_chk

# Desplegar
git push heroku main
```

### Opción 3: Desplegar en VPS (DigitalOcean, AWS, etc.)

#### 1. Conectar al VPS

```bash
ssh root@tu_ip_vps
```

#### 2. Instalar Dependencias

```bash
apt-get update
apt-get install python3 python3-pip python3-venv mysql-server nginx
```

#### 3. Clonar Proyecto

```bash
cd /var/www
git clone https://github.com/tu-usuario/leviatan-chk-api.git
cd leviatan-chk-api
```

#### 4. Configurar Aplicación

```bash
# Crear entorno virtual
python3 -m venv venv
source venv/bin/activate

# Instalar dependencias
pip install -r requirements_api.txt

# Configurar .env
nano .env
```

#### 5. Configurar Nginx

```bash
# Crear archivo de configuración
sudo nano /etc/nginx/sites-available/leviatan-chk

# Contenido:
server {
    listen 80;
    server_name tu_dominio.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

# Habilitar sitio
sudo ln -s /etc/nginx/sites-available/leviatan-chk /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

#### 6. Crear Servicio Systemd

```bash
# Crear archivo de servicio
sudo nano /etc/systemd/system/leviatan-chk.service

# Contenido:
[Unit]
Description=Leviatan CHK API
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/leviatan-chk-api
ExecStart=/var/www/leviatan-chk-api/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target

# Habilitar servicio
sudo systemctl enable leviatan-chk
sudo systemctl start leviatan-chk
```

---

## 📚 Documentación de Endpoints

### 1. Health Check

**Endpoint:** `GET /health`

**Descripción:** Verifica que la API está activa

**Response (200):**
```json
{
  "status": "ok",
  "timestamp": "2026-04-12T21:30:00",
  "service": "Leviatan CHK API"
}
```

---

### 2. Verificar Tarjeta (Principal)

**Endpoint:** `POST /amazon/leviatan`

**Descripción:** Verifica si una tarjeta es válida en Amazon

**Request:**
```json
{
  "card": "4532640527811643|12|2025|123",
  "cookies": "{\"ubid-main\": \"...\", \"session-id\": \"...\", ...}"
}
```

**Response (200 - Aprobada):**
```json
{
  "status": "✅ Approved",
  "response": "Tarjeta ccv Decline. (Removido: ✅)",
  "card_last_4": "1643",
  "transaction_id": "abc123def456",
  "timestamp": "2026-04-12T21:30:00"
}
```

**Response (200 - Declinada):**
```json
{
  "status": "❌ Declined",
  "response": "Tarjeta Mala No Insistir. (Removido: ✅)",
  "card_last_4": "1643",
  "timestamp": "2026-04-12T21:30:00"
}
```

**Response (401 - Cookies inválidas):**
```json
{
  "status": "⚠️ Error",
  "response": "Cookies no detectados, ingresa a \"Mi cuenta\", luego a 'Seguridad' e introduce tu contraseña para verificar si vuelve a funcionar."
}
```

---

### 3. Estadísticas

**Endpoint:** `GET /stats`

**Descripción:** Obtiene estadísticas de tarjetas verificadas

**Response (200):**
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

## 🔍 Solución de Problemas

### Problema: "Connection refused"

**Causa:** MySQL no está corriendo o credenciales incorrectas

**Solución:**
```bash
# Verificar que MySQL está corriendo
sudo systemctl status mysql

# Iniciar MySQL si no está corriendo
sudo systemctl start mysql

# Verificar credenciales en .env
cat .env
```

### Problema: "No module named 'flask'"

**Causa:** Dependencias no instaladas

**Solución:**
```bash
# Activar entorno virtual
source venv/bin/activate

# Instalar dependencias
pip install -r requirements_api.txt
```

### Problema: "Error 520" en producción

**Causa:** Servidor web caído o error interno

**Solución:**
```bash
# Ver logs
tail -f api.log

# Reiniciar servicio
sudo systemctl restart leviatan-chk

# En Railway, ver logs en dashboard
```

### Problema: "Cookies no detectados"

**Causa:** Cookies de Amazon expiradas o inválidas

**Solución:**
1. Obtener nuevas cookies de Amazon
2. Verificar que las cookies están en formato correcto
3. Asegurarse que la sesión está activa

---

## 📞 Soporte

Para reportar problemas o sugerencias:
- GitHub Issues: https://github.com/tu-usuario/leviatan-chk-api/issues
- Email: tu-email@example.com
- Telegram: @LeviatanChk

---

**Versión:** 1.0  
**Última Actualización:** 12 de Abril de 2026  
**Estado:** ✅ Listo para Producción
