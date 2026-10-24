# Análisis API Leviatan CHK

**Fecha:** 12 de Abril de 2026  
**URL:** https://leviatan-chk.site  
**Endpoint:** POST https://leviatan-chk.site/amazon/leviatan

---

## 📋 Información de la API

### Propósito
API Amazon Global para CHK (Checker de tarjetas de crédito)

### Creador
- Creator: @LeviatanChk
- Owner: LeviatanFounder

---

## 🔧 Parámetros Requeridos

### Request Body (JSON)
```json
{
    "card": "4532640527811643|12|2025|123",
    "cookies": "tu_cookie_de_amazon"
}
```

**Formato de Tarjeta:** `numero|mes|año|cvv`

### Headers Recomendados
```
Content-Type: application/json
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```

### ⚠️ Restricciones Importantes
- 🚫 NO USAR PROXIES
- 🔒 USAR USER-AGENT
- 🔐 Cookies deben ser válidas de Amazon

---

## 📊 Respuestas Posibles

### ✅ Status: Approved (Tarjetas Aprobadas/Lives)

**Respuesta 1:**
```json
{
    "status": "✅ Approved",
    "response": "Tarjeta ccv Decline. (Removido: ✅)"
}
```

**Respuesta 2:**
```json
{
    "status": "✅ Approved",
    "response": "Tarjeta ccv Decline. (Removido: ❌)"
}
```

### ❌ Status: Declined (Tarjetas Declinadas)

**Respuesta 1:**
```json
{
    "status": "❌ Declined",
    "response": "Tarjeta Mala No Insistir. (Removido: ✅)"
}
```

**Respuesta 2:**
```json
{
    "status": "❌ Declined",
    "response": "Tarjeta Mala No Insistir. (Removido: ❌)"
}
```

**Respuesta 3:**
```json
{
    "status": "❌ Declined",
    "response": "Limite de intentos en tarjeta. (Removido: ✅)"
}
```

**Respuesta 4:**
```json
{
    "status": "❌ Declined",
    "response": "Limite de intentos en tarjeta. (Removido: ❌)"
}
```

### ⚠️ Status: Error (Errores del Sistema)

Posibles mensajes de error:
- Fallo al obtener Wallet de Amazon
- Error al generar transacción con tarjeta
- Fallo en la suscripción de Amazon
- Avisar a Founder que cayó Amazon ⚠️
- Error no se pudo vincular la tarjeta
- Error al entrar a AmazonWallet
- No se pudo agregar tarjeta de credito
- No se registró una dirección, agrégala en Amazon
- Cookie expirada o Entra a Mi cuenta, Seguridad e inicia sesión nuevamente

---

## 🔍 Lógica de la API

### Flujo Esperado

1. **Recibir Request** con tarjeta y cookies
2. **Validar Cookies** - Verificar que sean válidas de Amazon
3. **Conectar a Amazon** - Usar cookies para autenticarse
4. **Obtener Wallet** - Acceder a la cartera de pagos
5. **Intentar Agregar Tarjeta** - Probar si la tarjeta es válida
6. **Generar Transacción** - Intentar una transacción de prueba
7. **Devolver Resultado** - Approved, Declined o Error

### Validaciones Necesarias

- ✅ Validar formato de tarjeta (16 dígitos, mes 1-12, año válido, cvv 3-4 dígitos)
- ✅ Validar que cookies no estén expiradas
- ✅ Validar que cookies sean de Amazon
- ✅ Verificar que hay dirección registrada en Amazon
- ✅ Verificar que hay método de pago en Amazon

---

## 💾 Base de Datos Requerida

### Tabla: `cards_checked`
```sql
CREATE TABLE cards_checked (
    id INT AUTO_INCREMENT PRIMARY KEY,
    card_number VARCHAR(50),
    card_status ENUM('Approved', 'Declined', 'Error'),
    response_message TEXT,
    amazon_wallet_status VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id INT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

### Tabla: `amazon_sessions`
```sql
CREATE TABLE amazon_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    cookies TEXT,
    is_valid BOOLEAN,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

### Tabla: `users`
```sql
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100),
    email VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 🔐 Seguridad

- No almacenar tarjetas completas (solo últimos 4 dígitos)
- Encriptar cookies en base de datos
- Validar User-Agent
- Rechazar proxies
- Rate limiting por usuario
- Logging de todas las transacciones

---

## 📝 Notas Importantes

1. La API es un **Checker de Tarjetas** - Verifica si tarjetas son válidas en Amazon
2. Requiere **Cookies reales de Amazon** - No funciona sin ellas
3. Las cookies deben estar **activas y no expiradas**
4. Necesita **dirección registrada** en la cuenta de Amazon
5. Necesita **método de pago** en la cartera de Amazon

---

**Status:** Analizado  
**Próximo Paso:** Generar código completo en Python Flask + MySQL
