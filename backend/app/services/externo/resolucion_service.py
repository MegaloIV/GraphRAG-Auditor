"""
Resolución de referencias SIN DOI contra catálogos académicos abiertos.

La verificación exige DOI porque la vieja búsqueda por título en CrossRef
devolvía el mejor resultado sin más y asociaba papers equivocados. Pero
dejar fuera todo lo que no trae DOI es carísimo: en una tesis real 68 de 94
referencias no lo tenían, y todas sus citas quedaban en NO_INFO sin haberlo
intentado.

La diferencia con aquel intento no es la fuente, es la VALIDACIÓN. OpenAlex y
Semantic Scholar devuelven metadatos estructurados (título oficial, año,
autores), así que el candidato se contrasta contra la referencia antes de
aceptarlo: las tres condiciones —título muy similar, apellido del primer autor
y año— deben cumplirse a la vez. Si falla una, se descarta y la referencia
sigue sin DOI, que es el comportamiento actual.
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import httpx
import structlog
from unidecode import unidecode

from app.core.config import get_settings
from app.services.externo.doi_utils import normalizar_doi

logger = structlog.get_logger(__name__)
settings = get_settings()

OPENALEX_URL = "https://api.openalex.org/works"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
CROSSREF_URL = "https://api.crossref.org/works"
DATACITE_URL = "https://api.datacite.org/dois"
PUBMED_BUSCAR_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_RESUMEN_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# Umbrales de aceptación. Son deliberadamente severos: un falso emparejamiento
# hace que la auditoría verifique la cita contra un paper ajeno y emita un
# veredicto con total confianza sobre la fuente equivocada.
_MIN_SIMILITUD_TITULO = 0.82
# Un Retry-After mayor que esto no se espera: la fuente se omite un tiempo.
_PAUSA_MAX_REINTENTO = 60
_BLOQUEO_MAX_SEGUNDOS = 3600
_TOLERANCIA_ANIO = 1        # erratas de un año son frecuentes en las tesis
_MAX_CANDIDATOS = 5
_MIN_PALABRAS_TITULO = 4    # un título de dos palabras no identifica nada

# OpenAlex admite ~10 peticiones por segundo en su "polite pool" (el que se
# obtiene mandando mailto). Lanzarlas seguidas devuelve 429 y la resolución
# falla en masa: sin este espaciado, de 68 referencias solo se resolvió 1.
_INTERVALO_MIN_SEGUNDOS = 0.15
_REINTENTOS = 2


@dataclass
class ResultadoResolucion:
    encontrado: bool
    doi: Optional[str] = None
    titulo_oficial: Optional[str] = None
    anio: Optional[int] = None
    fuente: Optional[str] = None       # "openalex" | "semantic_scholar"
    similitud_titulo: float = 0.0


def _normalizar(texto: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", unidecode((texto or "").lower())).strip()


def _palabras(texto: str) -> set[str]:
    return {p for p in _normalizar(texto).split() if len(p) > 2}


def _similitud_titulo(buscado: str, encontrado: str) -> float:
    """Jaccard sobre palabras significativas: penaliza tanto faltar como sobrar."""
    a, b = _palabras(buscado), _palabras(encontrado)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _apellido(nombre: str) -> str:
    """'García Pérez, J.' y 'Juan García Pérez' → 'garcia perez'."""
    base = nombre.split(",")[0] if "," in nombre else nombre
    palabras = _normalizar(base).split()
    if "," not in nombre and len(palabras) > 1:
        palabras = palabras[1:]          # descartar el nombre de pila
    return " ".join(palabras)


def _autores_coinciden(autores_ref: list[str], autores_api: list[str]) -> bool:
    """El primer autor de la referencia debe aparecer entre los del candidato."""
    if not autores_ref or not autores_api:
        return False
    esperado = set(_apellido(autores_ref[0]).split())
    if not esperado:
        return False
    for autor in autores_api:
        if esperado & set(_apellido(autor).split()):
            return True
    return False


class ResolucionService:
    """Busca el DOI de una referencia que no lo declara."""

    def __init__(self):
        # El ritmo se mide POR HOST: así varias referencias en paralelo pueden
        # consultar catálogos distintos a la vez sin pisarse. Un único
        # contador global serializaba todo y anulaba el paralelismo.
        self._ultimo_por_host: dict[str, float] = {}
        # Host → instante (monotónico) hasta el que no se le consulta. Una API
        # que devuelve 429 con Retry-After de horas (OpenAlex lo hizo con 17 h)
        # costaba ~15 s de reintentos inútiles por referencia.
        self._bloqueado_hasta: dict[str, float] = {}
        self._cerrojo = threading.Lock()
        self._cliente = httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": f"GraphRAG-Auditor (mailto:{settings.crossref_email})"},
        )

    def _get_con_ritmo(self, url: str, params: dict) -> httpx.Response | None:
        """
        GET espaciando las peticiones y reintentando ante 429 o error del
        servidor.

        Los 5xx importan tanto como los 429: OpenAlex devuelve 503 de forma
        intermitente y, al tratarlo como fallo definitivo, se abandonaba la
        fuente entera. Medido sobre una tesis real, eso hundía la resolución
        de 25 referencias a 2.

        La auditoría corre en paralelo (5 hilos), así que el espaciado se
        serializa con un cerrojo: si cada hilo midiera su propio ritmo, entre
        todos superarían el límite igual.
        """
        host = httpx.URL(url).host
        if time.monotonic() < self._bloqueado_hasta.get(host, 0.0):
            return None
        for intento in range(_REINTENTOS):
            with self._cerrojo:
                ultimo = self._ultimo_por_host.get(host, 0.0)
                espera = _INTERVALO_MIN_SEGUNDOS - (time.monotonic() - ultimo)
                self._ultimo_por_host[host] = time.monotonic() + max(espera, 0)
            if espera > 0:
                time.sleep(espera)

            try:
                respuesta = self._cliente.get(url, params=params)
            except httpx.HTTPError as e:
                logger.debug("catalogo_error_red", intento=intento + 1, error=str(e)[:80])
                time.sleep(2 ** intento)
                continue

            if respuesta.status_code != 429 and respuesta.status_code < 500:
                return respuesta

            pausa = float(respuesta.headers.get("Retry-After") or 0) or (2 ** intento)
            if respuesta.status_code == 429 and pausa > _PAUSA_MAX_REINTENTO:
                self._bloqueado_hasta[host] = time.monotonic() + min(pausa, _BLOQUEO_MAX_SEGUNDOS)
                logger.warning("catalogo_limitado_se_omite", host=host, segundos=int(pausa))
                return None
            logger.debug(
                "catalogo_reintento", status=respuesta.status_code,
                espera=pausa, intento=intento + 1,
            )
            time.sleep(min(pausa, 5))
        logger.warning("catalogo_sin_respuesta", url=url[:70])
        return None

    def resolver(
        self,
        titulo: str,
        autores: list[str],
        anio: Optional[int],
    ) -> ResultadoResolucion:
        """
        Busca la referencia por título y devuelve su DOI solo si el candidato
        supera las tres validaciones. Sin año o con un título demasiado corto
        ni se intenta: no habría con qué desempatar.
        """
        if not titulo or len(_palabras(titulo)) < _MIN_PALABRAS_TITULO:
            return ResultadoResolucion(encontrado=False)
        if anio is None:
            logger.debug("resolucion_sin_anio_no_intentada", titulo=titulo[:60])
            return ResultadoResolucion(encontrado=False)

        for buscar in (
            self._buscar_openalex,
            self._buscar_crossref,
            self._buscar_semantic_scholar,
            self._buscar_pubmed,
            self._buscar_datacite,
        ):
            try:
                for candidato in buscar(titulo, autores, anio):
                    resultado = self._validar(candidato, titulo, autores, anio)
                    if resultado.encontrado:
                        logger.info(
                            "referencia_resuelta_sin_doi",
                            doi=resultado.doi,
                            fuente=resultado.fuente,
                            similitud=round(resultado.similitud_titulo, 3),
                            titulo=titulo[:60],
                        )
                        return resultado
            except Exception as e:
                logger.warning("resolucion_fuente_fallida", error=str(e), titulo=titulo[:50])

        logger.debug("referencia_no_resuelta", titulo=titulo[:60])
        return ResultadoResolucion(encontrado=False)

    def _validar(
        self, candidato: dict, titulo: str, autores: list[str], anio: int
    ) -> ResultadoResolucion:
        """Las tres condiciones deben cumplirse a la vez."""
        doi = normalizar_doi(candidato.get("doi"))
        if not doi:
            return ResultadoResolucion(encontrado=False)

        similitud = _similitud_titulo(titulo, candidato.get("titulo") or "")
        if similitud < _MIN_SIMILITUD_TITULO:
            return ResultadoResolucion(encontrado=False)

        anio_api = candidato.get("anio")
        if anio_api is None or abs(int(anio_api) - anio) > _TOLERANCIA_ANIO:
            return ResultadoResolucion(encontrado=False)

        # Sin autores en la referencia no se puede exigir la tercera condición,
        # así que se compensa pidiendo un título prácticamente idéntico.
        if autores:
            if not _autores_coinciden(autores, candidato.get("autores") or []):
                return ResultadoResolucion(encontrado=False)
        elif similitud < 0.95:
            return ResultadoResolucion(encontrado=False)

        return ResultadoResolucion(
            encontrado=True,
            doi=doi,
            titulo_oficial=candidato.get("titulo"),
            anio=int(anio_api),
            fuente=candidato.get("fuente"),
            similitud_titulo=similitud,
        )

    @staticmethod
    def _sanear_para_filtro(titulo: str) -> str:
        """
        La sintaxis de filtros de OpenAlex usa ',' para separar filtros y ':'
        para separar clave y valor, así que un título con puntuación devuelve
        400. Se limpian esos caracteres antes de consultar.
        """
        limpio = re.sub(r"[,:;|&()\[\]{}]", " ", titulo)
        return re.sub(r"\s+", " ", limpio).strip()

    def _buscar_openalex(self, titulo: str, autores: list[str] | None = None,
                        anio: Optional[int] = None) -> list[dict]:
        respuesta = self._get_con_ritmo(
            OPENALEX_URL,
            {
                "filter": f"title.search:{self._sanear_para_filtro(titulo)[:250]}",
                "per-page": _MAX_CANDIDATOS,
                "mailto": settings.crossref_email,
            },
        )
        if respuesta is None:
            return []
        respuesta.raise_for_status()
        return [
            {
                "doi": (obra.get("doi") or "").replace("https://doi.org/", ""),
                "titulo": obra.get("display_name"),
                "anio": obra.get("publication_year"),
                "autores": [
                    a.get("author", {}).get("display_name", "")
                    for a in (obra.get("authorships") or [])
                ],
                "fuente": "openalex",
            }
            for obra in respuesta.json().get("results", [])
        ]

    def _buscar_semantic_scholar(self, titulo: str, autores: list[str] | None = None,
                        anio: Optional[int] = None) -> list[dict]:
        respuesta = self._get_con_ritmo(
            SEMANTIC_SCHOLAR_URL,
            {
                "query": titulo[:250],
                "limit": _MAX_CANDIDATOS,
                "fields": "title,year,authors,externalIds",
            },
        )
        if respuesta is None or respuesta.status_code == 429:
            logger.debug("semantic_scholar_limitado")
            return []
        respuesta.raise_for_status()
        return [
            {
                "doi": (obra.get("externalIds") or {}).get("DOI"),
                "titulo": obra.get("title"),
                "anio": obra.get("year"),
                "autores": [a.get("name", "") for a in (obra.get("authors") or [])],
                "fuente": "semantic_scholar",
            }
            for obra in respuesta.json().get("data", [])
        ]

    def resolver_lote(
        self, referencias: list[dict], max_hilos: int = 5
    ) -> dict[str, ResultadoResolucion]:
        """
        Resuelve varias referencias en paralelo. `referencias` es
        [{"referencia_id", "titulo", "autores", "anio"}].

        En serie eran ~16 s por referencia —la mayor parte esperando a
        catálogos lentos o reintentando—, lo que en una tesis con 70
        referencias sin DOI son casi veinte minutos. Como el ritmo se limita
        por host, varios hilos pueden consultar catálogos distintos a la vez
        sin superar el límite de ninguno.
        """
        if not referencias:
            return {}

        resultados: dict[str, ResultadoResolucion] = {}
        with ThreadPoolExecutor(max_workers=max_hilos) as executor:
            futuros = {
                executor.submit(
                    self.resolver, ref.get("titulo") or "",
                    ref.get("autores") or [], ref.get("anio"),
                ): ref["referencia_id"]
                for ref in referencias
            }
            for futuro in as_completed(futuros):
                ref_id = futuros[futuro]
                try:
                    resultados[ref_id] = futuro.result()
                except Exception as e:
                    logger.warning("resolucion_lote_fallida", ref_id=ref_id, error=str(e))
                    resultados[ref_id] = ResultadoResolucion(encontrado=False)

        hallados = sum(1 for r in resultados.values() if r.encontrado)
        logger.info(
            "resolucion_lote_completada", total=len(referencias), resueltas=hallados
        )
        return resultados

    def _buscar_crossref(self, titulo: str, autores: list[str] | None = None,
                         anio: Optional[int] = None) -> list[dict]:
        """
        CrossRef por título. Es la fuente con más cobertura, y ahora se puede
        usar sin riesgo porque el candidato pasa la misma validación que las
        demás: antes se aceptaba el primer resultado sin comprobar nada, que
        es lo que producía asociaciones a papers equivocados.

        Buscar solo por `query.bibliographic` rankea mal: para «Global cancer
        statistics 2018» devolvía tres «Faculty Opinions recommendation of…»
        por delante del artículo real. Acotar con el apellido del primer autor
        y una ventana de años pone el correcto en primera posición. No relaja
        nada: el candidato sigue pasando las tres validaciones.
        """
        parametros = {
            "query.bibliographic": titulo[:250],
            "rows": _MAX_CANDIDATOS,
            "select": "DOI,title,issued,author",
            "mailto": settings.crossref_email,
        }
        if autores:
            apellido = _apellido(autores[0])
            if apellido:
                parametros.pop("query.bibliographic")
                parametros["query.title"] = titulo[:250]
                parametros["query.author"] = apellido
        if anio:
            parametros["filter"] = (
                f"from-pub-date:{int(anio) - _TOLERANCIA_ANIO}-01-01,"
                f"until-pub-date:{int(anio) + _TOLERANCIA_ANIO}-12-31"
            )
        respuesta = self._get_con_ritmo(CROSSREF_URL, parametros)
        if respuesta is None or respuesta.status_code >= 400:
            return []
        candidatos = []
        for obra in respuesta.json().get("message", {}).get("items", []):
            partes = (obra.get("issued") or {}).get("date-parts") or [[]]
            candidatos.append({
                "doi": obra.get("DOI"),
                "titulo": (obra.get("title") or [""])[0],
                "anio": partes[0][0] if partes and partes[0] else None,
                "autores": [
                    f"{a.get('family','')}, {a.get('given','')}"
                    for a in (obra.get("author") or []) if a.get("family")
                ],
                "fuente": "crossref",
            })
        return candidatos

    def _buscar_pubmed(self, titulo: str, autores: list[str] | None = None,
                        anio: Optional[int] = None) -> list[dict]:
        """PubMed: cobertura biomédica, que es donde CrossRef por título flojea."""
        busqueda = self._get_con_ritmo(
            PUBMED_BUSCAR_URL,
            {
                "db": "pubmed", "term": f"{titulo[:200]}[Title]",
                "retmode": "json", "retmax": _MAX_CANDIDATOS,
                "email": settings.crossref_email,
            },
        )
        if busqueda is None or busqueda.status_code >= 400:
            return []
        ids = (busqueda.json().get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return []

        resumen = self._get_con_ritmo(
            PUBMED_RESUMEN_URL,
            {"db": "pubmed", "id": ",".join(ids), "retmode": "json",
             "email": settings.crossref_email},
        )
        if resumen is None or resumen.status_code >= 400:
            return []

        candidatos = []
        for pmid, datos in (resumen.json().get("result") or {}).items():
            if pmid == "uids" or not isinstance(datos, dict):
                continue
            doi = next(
                (i.get("value") for i in (datos.get("articleids") or [])
                 if i.get("idtype") == "doi"), None,
            )
            anio = (datos.get("pubdate") or "")[:4]
            candidatos.append({
                "doi": doi,
                "titulo": datos.get("title") or "",
                "anio": int(anio) if anio.isdigit() else None,
                "autores": [a.get("name", "") for a in (datos.get("authors") or [])],
                "fuente": "pubmed",
            })
        return candidatos

    def _buscar_datacite(self, titulo: str, autores: list[str] | None = None,
                        anio: Optional[int] = None) -> list[dict]:
        """
        DataCite: tesis, informes y repositorios institucionales, que es
        justamente lo que no está en CrossRef y abunda en las bibliografías
        latinoamericanas.
        """
        respuesta = self._get_con_ritmo(
            DATACITE_URL, {"query": titulo[:200], "page[size]": _MAX_CANDIDATOS},
        )
        if respuesta is None or respuesta.status_code >= 400:
            return []
        candidatos = []
        for obra in respuesta.json().get("data", []):
            attr = obra.get("attributes") or {}
            candidatos.append({
                "doi": attr.get("doi"),
                "titulo": (attr.get("titles") or [{}])[0].get("title", ""),
                "anio": attr.get("publicationYear"),
                "autores": [c.get("name", "") for c in (attr.get("creators") or [])],
                "fuente": "datacite",
            })
        return candidatos

    def existe_en_datacite(self, doi: str) -> Optional[dict]:
        """
        ¿El DOI está registrado en DataCite? Devuelve sus metadatos o None.

        CrossRef solo cubre editoriales; los repositorios institucionales, las
        tesis y los conjuntos de datos registran en DataCite. Dar por inexistente
        un DOI ausente de CrossRef acusa al tesista de inventárselo cuando
        muchas veces es una fuente legítima (10.7939 es la Universidad de
        Alberta, por ejemplo).
        """
        if not doi:
            return None
        respuesta = self._get_con_ritmo(f"{DATACITE_URL}/{doi}", {})
        if respuesta is None or respuesta.status_code != 200:
            return None
        attr = (respuesta.json().get("data") or {}).get("attributes") or {}
        return {
            "titulo": (attr.get("titles") or [{}])[0].get("title", ""),
            "anio": attr.get("publicationYear"),
            "editor": attr.get("publisher"),
            "url": attr.get("url"),
        }

    def cerrar(self) -> None:
        self._cliente.close()


resolucion_service = ResolucionService()
