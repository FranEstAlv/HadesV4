"""Servicio para generación de imágenes por IA vía OpenRouter."""

import base64
import os
import json
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime
import aiohttp

from bot.config import BASE_DIR


@dataclass
class ImageGenerationResult:
    """Resultado de una generación de imagen."""
    success: bool
    image_data: Optional[str] = None
    model: Optional[str] = None
    cost: Optional[float] = None
    error_message: Optional[str] = None
    provider: Optional[str] = None


class AIImageGenerator:
    """Generador de imágenes por IA usando OpenRouter API."""
    
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY", "")
        self.base_url = "https://openrouter.ai/api/v1/images"
        self._session: Optional[aiohttp.ClientSession] = None
        
        # Modelos recomendados
        self.models = {
            "flux_3_klein": "black-forest-labs/flux-3-image-klein",
            "flux_3": "black-forest-labs/flux-3-image",
            "recraft_v4": "recraft/recraft-v4.1-flash",
            "seedream": "bytedance-seed/seedream-5-0-flash",
            "qwen_image_3_pro": "qwen/qwen-image-3-pro",
        }
        
        # Costos por modelo (USD)
        self.model_costs = {
            "flux_3_klein": {"768": 0.008, "1K": 0.009, "1.5K": 0.011, "2K": 0.014, "4K": 0.036},
            "flux_3": {"768": 0.041, "1K": 0.048, "1.5K": 0.07, "2K": 0.1, "4K": 0.607},
            "recraft_v4": 0.007,
            "seedream": 0.018,
            "qwen_image_3_pro": 0.008,
        }
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
            )
        return self._session
    
    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
    
    async def generate_image(
        self,
        prompt: str,
        model: str = "flux_3_klein",
        resolution: str = "1K",
        aspect_ratio: str = "16:9",
        quality: str = "high",
        output_format: str = "png",
        safety_tolerance: int = 5,
    ) -> ImageGenerationResult:
        """Genera una imagen usando el modelo y parámetros especificados.
        
        Args:
            prompt: Descripción textual de la imagen deseada
            model: Modelo a usar (flux_3_klein, flux_3, recraft_v4, seedream)
            resolution: Resolución de la imagen (768, 1K, 1.5K, 2K, 4K)
            aspect_ratio: Relación de aspecto (16:9, 9:16, 1:1, etc.)
            quality: Calidad de la imagen (auto, low, medium, high)
            output_format: Formato de salida (png, jpeg, webp)
            safety_tolerance: Nivel de tolerancia a contenido (1-5, mayor = menos censura)
        
        Returns:
            ImageGenerationResult con el resultado de la generación
        """
        if not self.api_key:
            return ImageGenerationResult(
                success=False,
                error_message="No se configuró OPENROUTER_API_KEY"
            )
        
        session = await self._get_session()
        
        payload: Dict[str, Any] = {
            "model": self.models[model],
            "prompt": prompt,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "quality": quality,
            "output_format": output_format,
        }
        
        # Parámetros específicos por modelo
        if model in ("flux_3_klein", "flux_3"):
            payload["provider"] = {
                "options": {
                    "black-forest-labs": {
                        "safety_tolerance": safety_tolerance
                    }
                }
            }
        
        try:
            async with session.post(
                f"{self.base_url}",
                json=payload
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    result = data.get("data", [{}])[0]
                    image_b64 = result.get("b64_json", "")
                    
                    # Extraer costo si está disponible
                    cost = 0.0
                    usage = data.get("usage", {})
                    if usage:
                        cost = usage.get("cost", 0.0)
                    
                    return ImageGenerationResult(
                        success=True,
                        image_data=image_b64,
                        model=self.models[model],
                        cost=cost,
                        provider="OpenRouter"
                    )
                else:
                    error_text = await response.text()
                    return ImageGenerationResult(
                        success=False,
                        error_message=f"API error {response.status}: {error_text}"
                    )
        except aiohttp.ClientError as e:
            return ImageGenerationResult(
                success=False,
                error_message=f"Network error: {str(e)}"
            )
        except Exception as e:
            return ImageGenerationResult(
                success=False,
                error_message=f"Unexpected error: {str(e)}"
            )
    
    async def img_to_img(
        self,
        base64_image: str,
        prompt: str,
        model: str = "flux_3_klein",
        aspect_ratio: str = "16:9",
        quality: str = "high",
        output_format: str = "png",
        input_reference_index: int = 0,
    ) -> ImageGenerationResult:
        """Genera una imagen basada en una imagen de referencia y un prompt.
        
        Args:
            base64_image: Imagen base en base64
            prompt: Descripción de los cambios a aplicar
            model: Modelo a usar (debe soportar input_references)
            aspect_ratio: Relación de aspecto de salida
            quality: Calidad de la imagen
            output_format: Formato de salida
            input_reference_index: Índice de la referencia en la lista (por defecto 0)
        
        Returns:
            ImageGenerationResult con el resultado de la generación
        """
        if not self.api_key:
            return ImageGenerationResult(
                success=False,
                error_message="No se configuró OPENROUTER_API_KEY"
            )
        
        session = await self._get_session()
        
        payload: Dict[str, Any] = {
            "model": self.models[model],
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "output_format": output_format,
            "input_references": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{base64_image}"
                    }
                }
            ],
        }
        
        # quality no está soportado por flux_3_klein/flux_3 en img-to-img
        if model not in ("flux_3_klein", "flux_3"):
            payload["quality"] = quality
        
        # Parámetros específicos por modelo
        if model in ("flux_3_klein", "flux_3"):
            payload["provider"] = {
                "options": {
                    "black-forest-labs": {
                        "safety_tolerance": 4
                    }
                }
            }
        
        try:
            async with session.post(
                f"{self.base_url}",
                json=payload
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    result = data.get("data", [{}])[0]
                    image_b64 = result.get("b64_json", "")
                    
                    # Extraer costo si está disponible
                    cost = 0.0
                    usage = data.get("usage", {})
                    if usage:
                        cost = usage.get("cost", 0.0)
                    
                    return ImageGenerationResult(
                        success=True,
                        image_data=image_b64,
                        model=self.models[model],
                        cost=cost,
                        provider="OpenRouter"
                    )
                else:
                    error_text = await response.text()
                    return ImageGenerationResult(
                        success=False,
                        error_message=f"API error {response.status}: {error_text}"
                    )
        except aiohttp.ClientError as e:
            return ImageGenerationResult(
                success=False,
                error_message=f"Network error: {str(e)}"
            )
        except Exception as e:
            return ImageGenerationResult(
                success=False,
                error_message=f"Unexpected error: {str(e)}"
            )
    
    async def img_to_img_qwen(
        self,
        base64_image: str,
        prompt: str,
        aspect_ratio: str = "3:4",
        output_format: str = "png",
    ) -> ImageGenerationResult:
        """Genera una imagen usando qwen-image-3-pro con input_references.
        
        Este modelo es más permisivo con contenido sexual adulto.
        
        Args:
            base64_image: Imagen base en base64
            prompt: Descripción de los cambios a aplicar
            aspect_ratio: Relación de aspecto de salida
            output_format: Formato de salida
        
        Returns:
            ImageGenerationResult con el resultado
        """
        if not self.api_key:
            return ImageGenerationResult(
                success=False,
                error_message="No se configuró OPENROUTER_API_KEY"
            )
        
        session = await self._get_session()
        
        payload: Dict[str, Any] = {
            "model": "qwen/qwen-image-3-pro",
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "output_format": output_format,
            "input_references": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    }
                }
            ],
            "provider": {
                "only": ["alibaba"],
                "allow_fallbacks": False
            },
        }
        
        try:
            async with session.post(
                f"{self.base_url}",
                json=payload,
                timeout=180
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    result = data.get("data", [{}])[0]
                    image_b64 = result.get("b64_json", "")
                    
                    cost = 0.0
                    usage = data.get("usage", {})
                    if usage:
                        cost = usage.get("cost", 0.0)
                    
                    return ImageGenerationResult(
                        success=True,
                        image_data=image_b64,
                        model="qwen/qwen-image-3-pro",
                        cost=cost,
                        provider="OpenRouter"
                    )
                else:
                    error_text = await response.text()
                    return ImageGenerationResult(
                        success=False,
                        error_message=f"API error {response.status}: {error_text}"
                    )
        except aiohttp.ClientError as e:
            return ImageGenerationResult(
                success=False,
                error_message=f"Network error: {str(e)}"
            )
        except Exception as e:
            return ImageGenerationResult(
                success=False,
                error_message=f"Unexpected error: {str(e)}"
            )
    
    async def generate_and_save(
        self,
        prompt: str,
        output_path: str,
        **kwargs
    ) -> ImageGenerationResult:
        """Genera una imagen y la guarda en un archivo.
        
        Args:
            prompt: Descripción textual de la imagen
            output_path: Ruta donde guardar la imagen
            **kwargs: Parámetros para generate_image()
        
        Returns:
            ImageGenerationResult con el resultado
        """
        result = await self.generate_image(prompt, **kwargs)
        
        if result.success and result.image_data:
            try:
                # Decodificar base64 y guardar
                image_bytes = base64.b64decode(result.image_data)
                
                # Asegurar que el directorio existe
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                
                with open(output_path, "wb") as f:
                    f.write(image_bytes)
                
                result.image_data = output_path  # Ruta del archivo en vez de base64
            except Exception as e:
                result.success = False
                result.error_message = f"Failed to save image: {str(e)}"
        
        return result


# Instancia global (se inicializará al cargar el módulo)
ai_image_generator: Optional[AIImageGenerator] = None


async def initialize():
    """Inicializa el generador de imágenes."""
    global ai_image_generator
    ai_image_generator = AIImageGenerator()


async def shutdown():
    """Cierra el generador de imágenes."""
    global ai_image_generator
    if ai_image_generator:
        await ai_image_generator.close()
        ai_image_generator = None
