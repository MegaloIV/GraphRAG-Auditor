from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
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

        - faithfulness:      ¿La respuesta está anclada en los contextos (no alucina)?
        - answer_relevancy:  ¿La respuesta es relevante al claim verificado?
        - context_precision: ¿El fragmento recuperado es pertinente para el claim?

        Se excluyen context_recall y answer_correctness porque requieren anotaciones
        humanas de ground truth que este sistema no posee.
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
                    context_precision,
                ],
                llm=self.llm,
                embeddings=self.embeddings,
            )
            scores = resultado.to_pandas().iloc[0]
            return {
                "faithfulness":      _safe_float(scores, "faithfulness"),
                "answer_relevancy":  _safe_float(scores, "answer_relevancy"),
                "context_precision": _safe_float(scores, "context_precision"),
            }
        except Exception as e:
            logger.warning("ragas_cita_fallida", error=str(e))
            return {
                "faithfulness":      None,
                "answer_relevancy":  None,
                "context_precision": None,
            }


ragas_service = RagasService()
