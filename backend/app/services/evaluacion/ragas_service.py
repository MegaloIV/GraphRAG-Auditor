from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_utilization,
)
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
import structlog
from app.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


def _safe_float(scores, key) -> float | None:
    try:
        v = scores[key]
        return round(float(v), 3) if v is not None else None
    except Exception:
        return None


class RagasService:
    def __init__(self):
        # Si el modelo rechaza `temperature`, se reconstruye el LLM sin él (D1).
        self._con_temperature = True
        self.llm = self._crear_llm(con_temperature=True)
        embeddings = OpenAIEmbeddings(
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
        )
        self.embeddings = LangchainEmbeddingsWrapper(embeddings)

    def _crear_llm(self, con_temperature: bool) -> LangchainLLMWrapper:
        kwargs = {
            "api_key": settings.openai_api_key,
            "model": settings.openai_model,
        }
        if con_temperature:
            kwargs["temperature"] = 0.0
        return LangchainLLMWrapper(ChatOpenAI(**kwargs))

    def evaluar_cita(
        self,
        pregunta: str,
        respuesta: str,
        contextos: list[str],
    ) -> dict:
        """
        Evalúa una cita con métricas RAGAS que no requieren ground truth externo.

        - faithfulness:        ¿La respuesta está anclada en los contextos (no alucina)?
        - answer_relevancy:    ¿La respuesta es relevante al claim verificado?
        - context_utilization: ¿El fragmento recuperado es pertinente para el claim?
                               (variante de context_precision SIN referencia: usa la
                               respuesta del auditor en lugar de un ground truth)

        Se excluyen context_recall, answer_correctness y context_precision (con
        referencia) porque requieren anotaciones humanas de ground truth que este
        sistema no posee. El resultado se expone bajo la clave 'context_precision'
        para mantener compatibilidad con el resto del pipeline.
        """
        data = {
            "question": [pregunta],
            "answer":   [respuesta],
            "contexts": [contextos],
        }
        dataset = Dataset.from_dict(data)
        try:
            try:
                resultado = self._evaluar(dataset)
            except Exception as e:
                # D1: si el modelo rechaza `temperature`, reconstruir sin él y reintentar.
                if not (self._con_temperature and "temperature" in str(e).lower()):
                    raise
                logger.info("temperature_no_soportada", modelo=settings.openai_model)
                self._con_temperature = False
                self.llm = self._crear_llm(con_temperature=False)
                resultado = self._evaluar(dataset)
            scores = resultado.to_pandas().iloc[0]
            return {
                "faithfulness":      _safe_float(scores, "faithfulness"),
                "answer_relevancy":  _safe_float(scores, "answer_relevancy"),
                # context_utilization = context precision sin ground truth
                "context_precision": _safe_float(scores, "context_utilization"),
            }
        except Exception as e:
            logger.warning("ragas_cita_fallida", error=str(e))
            return {
                "faithfulness":      None,
                "answer_relevancy":  None,
                "context_precision": None,
            }

    def _evaluar(self, dataset: Dataset):
        return evaluate(
            dataset,
            metrics=[
                faithfulness,
                answer_relevancy,
                context_utilization,
            ],
            llm=self.llm,
            embeddings=self.embeddings,
        )


ragas_service = RagasService()
