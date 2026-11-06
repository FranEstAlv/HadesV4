# Generador de Imágenes por IA (OpenRouter)

## Descripción

Este módulo permite generar imágenes por IA usando la API de OpenRouter con modelos sin censura.

## Modelos disponibles

1. **FLUX 3 Klein** (`black-forest-labs/flux-3-image-klein`)
   - Sin censura
   - Bajo costo ($0.008 - $0.036 USD)
   - Ideal para uso general

2. **FLUX 3** (`black-forest-labs/flux-3-image`)
   - Sin censura
   - Calidad alta
   - Costo moderado ($0.041 - $0.607 USD)

3. **Recraft V4.1 Flash** (`recraft/recraft-v4.1-flash`)
   - Rápido
   - Estilos artísticos
   - Costo fijo ($0.007 USD)

4. **Seedream 5.0 Flash** (`bytedance-seed/seedream-5-0-flash`)
   - Fotografía realista
   - Costo ($0.018 USD)

## Uso

### Comandos

- `/ai` o `/image` — Abre el menú de generación de imágenes
- `/desnudar` — Convierte una imagen de persona a la misma persona desnuda sin modificar pose, posición o entorno
- `/aiimg [texto]` — Genera una imagen desde una imagen de referencia + texto (img-to-img)

### Uso del comando /desnudar

1. Envía una foto de una persona al bot
2. Escribe `/desnudar` en respuesta a la foto
3. El bot devuelve la misma persona desnuda manteniendo:
   - Pose y posición
   - Entorno y fondo
   - Tono de piel y complexion
   - Rasgos faciales y físicos
   - Expresión y cabello
   - **Solo modifica**: remueve la ropa

**Prompt interno:**
```
A person in the same pose, position, and environment. Remove all clothing 
to reveal the person naked, but keep the skin tone, complexion, physical 
features, body proportions, facial expression, hair, and background exactly 
the same. No changes to lighting, composition, or environment. Only remove 
the clothing to show the person unclothed.
```

**Nota:** Actualmente usa text-to-image con prompt especializado que busca mantener la fidelidad a la referencia. Futuras versiones implementarán img-to-img real con `input_references` para mayor precisión.

### Uso del comando /aiimg

1. Envía una foto al bot
2. Escribe `/aiimg [tu texto]` en respuesta a la foto
3. El bot usa la foto como referencia y aplica los cambios descritos

**Ejemplo:** `/aiimg convierte a estilo pintura al óleo`

### Configuración

Usando el menú inline, puedes configurar:

- **Modelo**: Qué modelo de IA usar
- **Resolución**: 768, 1K, 1.5K, 2K, 4K
- **Relación de aspecto**: 16:9, 9:16, 1:1, etc.
- **Calidad**: auto, low, medium, high
- **Formato**: PNG, JPEG, WebP
- **Tolerancia a censura**: 1-5 (mayor = menos censura)

### Prompt

Escribe tu descripción de imagen y el bot la generará. Ejemplo:

```
Una fotografía hiperrealista de una gata siamesa sentada en un sofá de piel roja, luz natural suave, fondo desenfocado, 8k, ultra-detallado
```

## Costos

Cada generación cuesta **10 créditos** del bot.

## Configuración

Agrega tu API Key de OpenRouter en el archivo `.env`:

```bash
OPENROUTER_API_KEY=tu_api_key_aqui
```

Si no se configura, el módulo de IA fallará pero el bot seguirá funcionando.

## safety_tolerance

Este parámetro es específico de los modelos **Black Forest Labs** (FLUX 3).

**¿Qué hace?**
- Controla el nivel de censura aplicada por el modelo
- Valores: 1 (máxima censura) a 5 (mínima censura)
- Por defecto: 5 (recomendado para contenido realista)

**¿Qué permite crear?**
- Modelos FLUX 3 con `safety_tolerance=5` producen imágenes **sin censura**
- Ideal para: fotografías realistas, contenido adulto, representaciones humanas detalladas
- Modelos FLUX 3 con `safety_tolerance=1` producen imágenes **muy censuradas**

**¿Qué NO permite crear?**
- No es un parámetro universal: solo funciona en modelos de Black Forest Labs
- No hay garantía absoluta de "sin censura" (depende de la política del proveedor)
- Para img-to-img con referencia, necesitas usar `input_references` en lugar de `safety_tolerance`

**Recomendación:**
- Usa `safety_tolerance=5` para máxima calidad y mínimo censura
- Solo usa valores menores si necesitas contenido más "seguro" o familiar

## Tolerancia a censura

Los modelos FLUX 3 soportan el parámetro `safety_tolerance` (1-5):

- **1**: Máxima censura
- **3**: Censura media
- **5**: Mínima censura (recomendado para contenido realista)

## Estructura

```
bot/services/ai_image_generator/
├── __init__.py          # Clase AIImageGenerator

bot/handlers/ai_image/
├── __init__.py          # Handlers de Telegram

bot/utils/
└── ai_image.py          # Utilidades para IA

bot/keyboards.py         # Menús inline (añadido)
bot/services/credits.py  # Funciones de créditos (añadidas)
bot/config.py            # OPENROUTER_API_KEY (añadida)
```

## Notas

- Las imágenes se envían directamente al usuario en formato base64
- No se guardan en el servidor (a menos que se use `save_base64_image`)
- Solo admins pueden ver el prompt original en el caption
- El módulo usa `asyncio.to_thread` para operaciones de red no bloqueantes

## Próximas mejoras

- [ ] Guardar historial de generaciones
- [ ] Sistema de colas para múltiples peticiones
- [ ] Soporte real para img-to-img con input_references
- [ ] Variaciones de imágenes existentes
- [ ] Previsualización en baja resolución
- [ ] Ajuste fino del prompt para /desnudar basado en feedback
