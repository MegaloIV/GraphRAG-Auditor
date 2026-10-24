"""
Atribución en cadena: ¿de quién es realmente la afirmación?

Cuando el pasaje que respalda una cita es la fuente resumiendo trabajo ajeno
—"Boekaerts, for example, argued for three types of learning strategies"— el
tesista está citando a A por algo que A atribuye a B. Es una falta de
integridad corriente y el RAG vectorial no puede verla: el chunk habla del
tema, la similitud es alta y el juez lee una afirmación compatible. Nada en el
embedding dice "esto es de otro".

Lo que sí lo resuelve es el grafo. Crossref deposita la bibliografía del propio
paper citado (nodos ReferenciaExterna), así que el marcador que acompaña al
pasaje —"[8]", "(Boekaerts, 2002)"— se puede resolver contra ella, y después
comprobar si esa fuente primaria está o no en la bibliografía de la tesis.
"""
import re

import structlog
from unidecode import unidecode

from app.core.config import get_settings
from app.services.grafo.neo4j_service import neo4j_service

logger = structlog.get_logger(__name__)
settings = get_settings()

# Marcadores de cita dentro del pasaje transcrito.
_RE_MARCADOR_NUMERICO = re.compile(r"\[(\d{1,3})(?:[,;\s–-]+\d{1,3})*\]")
_RE_MARCADOR_AUTOR_ANIO = re.compile(
    r"\(?\b([A-ZÁÉÍÓÚÑ][\w'’\-]+)\b[^()]{0,40}?,?\s*((?:19|20)\d{2})\)?"
)
_RE_AUTOR_VERBO = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][\w'’\-]+)(?:\s+et\s+al\.?)?[^.]{0,30}?\s"
    r"(?:argued|argues|claimed|reported|found|proposed|suggested|showed|shown|"
    r"demonstrated|noted|observed)\b"
)

_Q_EXTERNAS = """
MATCH (r:Referencia {id: $ref_id})-[c:CITA_EXTERNA]->(x:ReferenciaExterna)
RETURN x.doi AS doi, x.autor AS autor, x.anio AS anio, x.titulo AS titulo,
       c.orden AS orden, c.clave AS clave
ORDER BY c.orden
"""

# ¿La fuente primaria está también en la bibliografía de la tesis?
_Q_EN_BIBLIOGRAFIA = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
WITH r, collect(a.nombre_normalizado) AS autores
WHERE ($doi <> '' AND toLower(coalesce(r.doi_verificado, r.doi, '')) = $doi)
   OR (r.anio = $anio AND any(n IN autores WHERE n STARTS WITH $apellido))
RETURN r.id AS ref_id, r.titulo AS titulo LIMIT 1
"""


def _norm(texto: str) -> str:
    return unidecode((texto or "").strip().lower())


class AtribucionService:

    def resolver(
        self, documento_id: str, referencia_id: str, cita_textual: str
    ) -> dict | None:
        """
        Resuelve a qué fuente primaria apunta el marcador del pasaje.

        Devuelve {autor, anio, titulo, doi, en_bibliografia, ref_id_propia} o
        None si el paper no depositó su bibliografía o el marcador no resuelve.
        """
        if not cita_textual or not referencia_id:
            return None

        externas = self._leer_externas(referencia_id)
        if not externas:
            return None

        primaria = (
            self._por_marcador_numerico(cita_textual, externas)
            or self._por_autor_anio(cita_textual, externas)
            or self._por_autor(cita_textual, externas)
        )
        if not primaria:
            return None

        propia = self._buscar_en_bibliografia(documento_id, primaria)
        return {
            "autor": primaria.get("autor") or "",
            "anio": primaria.get("anio"),
            "titulo": primaria.get("titulo") or "",
            "doi": primaria.get("doi") or "",
            "en_bibliografia": propia is not None,
            "ref_id_propia": (propia or {}).get("ref_id"),
        }

    # ── Estrategias de resolución ────────────────────────────────────────

    @staticmethod
    def _por_marcador_numerico(cita: str, externas: list[dict]) -> dict | None:
        """"[8]" apunta a la octava entrada de la bibliografía del paper."""
        m = _RE_MARCADOR_NUMERICO.search(cita)
        if not m:
            return None
        n = int(m.group(1))
        for ext in externas:
            # La clave depositada suele terminar en el número ("ref8", "B8").
            if ext.get("clave") and re.search(rf"\D{n}$", ext["clave"]):
                return ext
        for ext in externas:
            if ext.get("orden") == n:
                return ext
        return None

    @staticmethod
    def _por_autor_anio(cita: str, externas: list[dict]) -> dict | None:
        for m in _RE_MARCADOR_AUTOR_ANIO.finditer(cita):
            apellido, anio = _norm(m.group(1)), int(m.group(2))
            for ext in externas:
                if ext.get("anio") == anio and _norm(ext.get("autor")).startswith(apellido):
                    return ext
        return None

    @staticmethod
    def _por_autor(cita: str, externas: list[dict]) -> dict | None:
        for m in _RE_AUTOR_VERBO.finditer(cita):
            apellido = _norm(m.group(1))
            candidatos = [
                e for e in externas if _norm(e.get("autor")).startswith(apellido)
            ]
            # Solo si es inequívoco: varias entradas del mismo autor no
            # permiten saber a cuál se refiere el pasaje.
            if len(candidatos) == 1:
                return candidatos[0]
        return None

    # ── Acceso al grafo ──────────────────────────────────────────────────

    @staticmethod
    def _leer_externas(referencia_id: str) -> list[dict]:
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as ses:
                return [dict(r) for r in ses.run(_Q_EXTERNAS, ref_id=referencia_id)]
        except Exception as e:
            logger.warning("error_leer_referencias_externas", error=str(e))
            return []

    @staticmethod
    def _buscar_en_bibliografia(documento_id: str, primaria: dict) -> dict | None:
        apellido = _norm(primaria.get("autor")).split(",")[0].split()[0] if primaria.get("autor") else ""
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as ses:
                rec = ses.run(
                    _Q_EN_BIBLIOGRAFIA,
                    doc_id=documento_id,
                    doi=(primaria.get("doi") or "").lower(),
                    anio=primaria.get("anio"),
                    apellido=apellido,
                ).single()
            return dict(rec) if rec else None
        except Exception as e:
            logger.warning("error_buscar_en_bibliografia", error=str(e))
            return None


atribucion_service = AtribucionService()
