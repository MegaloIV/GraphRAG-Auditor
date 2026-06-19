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
        llm = ChatOpenAI(
            api_key=settings.openai_api_key,
            model="gpt-4o-mini",
            temperature=0.0,
        )
        self.llm = LangchainLLMWrapper(llm)
        embeddings = OpenAIEmbeddings(
            api_key=settings.openai_api_key,
            model="text-embedding-3-small",
        )
        self.embeddings = LangchainEmbeddingsWrapper(embeddings)

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
        try:
            data = {
                "question": [pregunta],
                "answer":   [respuesta],
                "contexts": [contextos],
            }
            dataset = Dataset.from_dict(data)
            resultado = evaluate(
                dataset,
                metrics=[
                    faithfulness,
                    answer_relevancy,
                    context_utilization,
                ],
                llm=self.llm,
                embeddings=self.embeddings,
            )
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


ragas_service = RagasService()
