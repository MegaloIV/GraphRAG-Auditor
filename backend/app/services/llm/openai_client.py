"""
Cliente OpenAI con reintentos automáticos.
Usar cuando se necesita mayor calidad que Groq.
"""
import time
import structlog
from openai import OpenAI

from app.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class LLMService:
    def __init__(self):
        self._cliente: OpenAI | None = None

    @property
    def cliente(self) -> OpenAI:
        if self._cliente is None:
            if not settings.openai_api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY no está configurada. "
                    "Agrégala al archivo .env"
                )
            self._cliente = OpenAI(api_key=settings.openai_api_key)
        return self._cliente

    def completar(
        self,
        system_prompt: str,
        user_prompt: str,
        intentos: int = 3,
    ) -> str:
        ultimo_error = None

        for intento in range(1, intentos + 1):
            try:
                inicio = time.perf_counter()
                response = self.cliente.chat.completions.create(
                    model=settings.openai_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.0,
                )
                elapsed = time.perf_counter() - inicio

                logger.info(
                    "llm_llamada_exitosa",
                    intento=intento,
                    tiempo_segundos=round(elapsed, 2),
                    tokens=response.usage.total_tokens,
                    costo_usd=round(response.usage.total_tokens * 0.00000015, 5),
                )

                return response.choices[0].message.content or ""

            except Exception as e:
                ultimo_error = e
                logger.warning("llm_error", intento=intento, error=str(e))
                if intento < intentos:
                    time.sleep(2 ** intento)

        raise RuntimeError(
            f"No se pudo obtener respuesta tras {intentos} intentos. "
            f"Detalle: {str(ultimo_error)}"
        )


# Singleton
llm_service = LLMService()