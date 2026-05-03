"""
Servicio de verificación de referencias via CrossRef API.
CrossRef es gratuita, no requiere API key, cubre +130M papers.

Para cada referencia extraída de la tesis:
1. Busca por título + autor + año
2. Retorna DOI, abstract y nivel de confianza
"""
import time
import httpx
import structlog
from dataclasses import dataclass
from typing import Optional
from app.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

CROSSREF_BASE_URL = "https://api.crossref.org/works"
CROSSREF_MAILTO = settings.crossref_email

@dataclass
class ResultadoCrossRef:
    encontrado: bool
    doi: Optional[str] = None
    titulo_oficial: Optional[str] = None
    autores_oficiales: list[str] = None
    anio_oficial: Optional[int] = None
    abstract: Optional[str] = None
    url_pdf: Optional[str] = None
    score_coincidencia: float = 0.0

    def __post_init__(self):
        if self.autores_oficiales is None:
            self.autores_oficiales = []


class CrossRefService:

    def __init__(self):
        self._cliente = httpx.Client(
            timeout=10.0,
            headers={"User-Agent": f"GraphRAG-Auditor/0.1 (mailto:{CROSSREF_MAILTO})"}
        )

    def buscar_referencia(
        self,
        titulo: str,
        autores: list[str],
        anio: Optional[int] = None,
        intentos: int = 3,
    ) -> ResultadoCrossRef:
        """
        Busca una referencia en CrossRef por título y autores.
        Retorna el resultado con mayor score de coincidencia.
        """
        # Construir query de búsqueda
        query_parts = [titulo]
        if autores:
            # Usar solo el primer autor para la búsqueda
            query_parts.append(autores[0].split(",")[0])

        query = " ".join(query_parts)

        params = {
            "query": query,
            "rows": 3,
            "select": "DOI,title,author,published,abstract,link",
            "mailto": CROSSREF_MAILTO,
        }

        if anio:
            params["filter"] = f"from-pub-date:{anio},until-pub-date:{anio}"

        ultimo_error = None
        for intento in range(1, intentos + 1):
            try:
                response = self._cliente.get(CROSSREF_BASE_URL, params=params)
                response.raise_for_status()
                data = response.json()

                items = data.get("message", {}).get("items", [])
                if not items:
                    logger.info("crossref_sin_resultados", titulo=titulo[:50])
                    return ResultadoCrossRef(encontrado=False)

                # Tomar el primer resultado (mayor score de CrossRef)
                mejor = items[0]
                return self._parsear_resultado(mejor, titulo)

            except httpx.TimeoutException:
                logger.warning("crossref_timeout", intento=intento)
                ultimo_error = "timeout"
                time.sleep(2 ** intento)
            except Exception as e:
                logger.error("crossref_error", error=str(e), intento=intento)
                ultimo_error = str(e)
                time.sleep(1)

        logger.error("crossref_fallido", titulo=titulo[:50], error=ultimo_error)
        return ResultadoCrossRef(encontrado=False)

    def _parsear_resultado(self, item: dict, titulo_buscado: str) -> ResultadoCrossRef:
        """Extrae los campos relevantes de la respuesta de CrossRef."""

        # Título oficial
        titulos = item.get("title", [])
        titulo_oficial = titulos[0] if titulos else None

        # Autores
        autores_raw = item.get("author", [])
        autores = [
            f"{a.get('family', '')}, {a.get('given', '')[:1]}."
            for a in autores_raw
            if a.get("family")
        ]

        # Año
        fecha = item.get("published", {})
        partes = fecha.get("date-parts", [[]])
        anio = partes[0][0] if partes and partes[0] else None

        # Abstract
        abstract = item.get("abstract", None)
        if abstract:
            # CrossRef a veces incluye tags HTML en el abstract
            import re
            abstract = re.sub(r"<[^>]+>", "", abstract).strip()

        # URL del PDF si está disponible
        links = item.get("link", [])
        url_pdf = None
        for link in links:
            if link.get("content-type") == "application/pdf":
                url_pdf = link.get("URL")
                break

        # Score de coincidencia básico por título
        score = self._calcular_score(titulo_buscado, titulo_oficial)

        logger.info(
            "crossref_encontrado",
            doi=item.get("DOI"),
            score=score,
            tiene_abstract=abstract is not None,
        )

        return ResultadoCrossRef(
            encontrado=True,
            doi=item.get("DOI"),
            titulo_oficial=titulo_oficial,
            autores_oficiales=autores,
            anio_oficial=anio,
            abstract=abstract,
            url_pdf=url_pdf,
            score_coincidencia=score,
        )

    @staticmethod
    def _calcular_score(titulo_buscado: str, titulo_encontrado: Optional[str]) -> float:
        """
        Score simple de coincidencia entre títulos.
        0.0 = nada parecido, 1.0 = idénticos.
        """
        if not titulo_encontrado:
            return 0.0

        palabras_buscado = set(titulo_buscado.lower().split())
        palabras_encontrado = set(titulo_encontrado.lower().split())

        if not palabras_buscado:
            return 0.0

        interseccion = palabras_buscado & palabras_encontrado
        return len(interseccion) / len(palabras_buscado)

    def cerrar(self):
        self._cliente.close()


# Singleton
crossref_service = CrossRefService()