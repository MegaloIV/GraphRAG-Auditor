import time
import structlog
from groq import Groq

from app.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class LLMService:
    """
    Cliente de Groq con reintentos automáticos.
    Si mañana cambias de proveedor, solo modificas este archivo.
    """

    def __init__(self):
        self._cliente: Groq | None = None
        # Si el modelo rechaza `temperature`, se deja de enviar (default del modelo).
        self._usar_temperature = True

    @property
    def cliente(self) -> Groq:
        if self._cliente is None:
            if not settings.groq_api_key:
                raise RuntimeError(
                    "GROQ_API_KEY no está configurada. "
                    "Agrégala al archivo .env"
                )
            self._cliente = Groq(api_key=settings.groq_api_key)
        return self._cliente

    def _crear_completion(self, messages: list[dict]):
        """Llama al API con temperature=0.0; si el modelo lo rechaza, reintenta sin él."""
        kwargs = {"model": settings.groq_model, "messages": messages}
        if self._usar_temperature:
            try:
                return self.cliente.chat.completions.create(**kwargs, temperature=0.0)
            except Exception as e:
                if "temperature" not in str(e).lower():
                    raise
                self._usar_temperature = False
                logger.info("temperature_no_soportada", modelo=settings.groq_model)
        return self.cliente.chat.completions.create(**kwargs)

    def completar(
        self,
        system_prompt: str,
        user_prompt: str,
        intentos: int = 3,
    ) -> str:
        """
        Envía un prompt a Groq y retorna el texto de respuesta.
        Reintenta automáticamente hasta 3 veces ante errores de red o rate limit.
        """
        ultimo_error = None

        for intento in range(1, intentos + 1):
            try:
                inicio = time.perf_counter()
                response = self._crear_completion(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ]
                )
                elapsed = time.perf_counter() - inicio

                logger.info(
                    "llm_llamada_exitosa",
                    intento=intento,
                    tiempo_segundos=round(elapsed, 2),
                )

                return response.choices[0].message.content or ""

            except Exception as e:
                ultimo_error = e
                logger.warning(
                    "llm_error",
                    intento=intento,
                    error=str(e),
                )
                if intento < intentos:
                    time.sleep(2 ** intento)

        raise RuntimeError(
            f"No se pudo obtener respuesta del modelo tras {intentos} intentos. "
            f"Verifica tu API key y conexión a internet. "
            f"Detalle: {str(ultimo_error)}"
        )


# Singleton
llm_service = LLMService()