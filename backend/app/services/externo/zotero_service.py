"""
Importación de una colección de Zotero (.ris o .zip con PDFs adjuntos).

Empareja cada entrada del RIS con las referencias extraídas del documento:
  1. DOI normalizado exacto                                  → confianza 1.0
  2. título normalizado (similitud ≥ 0.90) + primer autor + año → confianza 0.9

Para cada referencia emparejada:
  - Sobrescribe los metadatos del grafo con los de Zotero (más limpios que
    la extracción LLM): título, autores, año y DOI.
  - Asocia el PDF: primero el adjunto del ZIP (campo L1/L2 del RIS); si no
    hay, descarga por DOI vía Unpaywall (solo open access).
  - Indexa el texto en Supabase/pgvector para la auditoría.

El resumen se persiste en Storage (zotero/{documento_id}.json) para que el
frontend lo muestre al terminar la importación en segundo plano.
"""
import io
import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

import structlog
from unidecode import unidecode

from app.core.config import get_settings
from app.services.externo.doi_utils import normalizar_doi, doi_para_chunks
from app.services.externo.embedding_service import embedding_service
from app.services.externo.unpaywall_service import unpaywall_service
from app.services.grafo.grafo_carga_service import grafo_carga_service
from app.services.grafo.neo4j_service import neo4j_service
from app.services.storage.supabase_storage_service import storage_service
from app.services.vectorstore.supabase_service import supabase_vector_service

logger = structlog.get_logger(__name__)
settings = get_settings()

UMBRAL_TITULO = 0.90

_RE_LINEA_RIS = re.compile(r'^([A-Z][A-Z0-9])\s{2}-\s?(.*)$')
_RE_ANIO = re.compile(r'\b(19|20)\d{2}\b')


def _objeto_resultado(documento_id: str) -> str:
    return f"zotero/{documento_id}.json"


def _norm_titulo(titulo: str) -> str:
    """Título canónico para comparar: sin tildes, minúsculas, solo alfanumérico."""
    limpio = unidecode(titulo or "").lower()
    limpio = re.sub(r'[^a-z0-9]+', ' ', limpio)
    return limpio.strip()


def _apellido_primer_autor(autores: list[str]) -> str:
    if not autores:
        return ""
    return unidecode(autores[0].split(",")[0].strip().lower())


def parsear_ris(texto: str) -> list[dict]:
    """
    Parser mínimo de RIS (formato de Zotero). Cada entrada:
    {titulo, autores[], anio, doi, archivos[]}.
    """
    entradas: list[dict] = []
    actual: dict | None = None

    for linea in texto.splitlines():
        m = _RE_LINEA_RIS.match(linea.strip("\ufeff"))
        if not m:
            continue
        tag, valor = m.group(1), m.group(2).strip()

        if tag == "TY":
            actual = {"titulo": "", "autores": [], "anio": None, "doi": "", "archivos": []}
        elif actual is None:
            continue
        elif tag in ("TI", "T1") and not actual["titulo"]:
            actual["titulo"] = valor
        elif tag in ("AU", "A1"):
            actual["autores"].append(valor)
        elif tag in ("PY", "Y1", "DA") and not actual["anio"]:
            m_anio = _RE_ANIO.search(valor)
            if m_anio:
                actual["anio"] = int(m_anio.group())
        elif tag == "DO":
            actual["doi"] = normalizar_doi(valor)
        elif tag in ("L1", "L2") and valor.lower().endswith(".pdf"):
            actual["archivos"].append(valor)
        elif tag == "ER":
            if actual.get("titulo"):
                entradas.append(actual)
            actual = None

    return entradas


def _extraer_zip(contenido: bytes) -> tuple[str | None, dict[str, bytes]]:
    """Devuelve (texto del primer .ris, {ruta_normalizada: bytes de cada PDF})."""
    texto_ris = None
    pdfs: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
        for nombre in zf.namelist():
            bajo = nombre.lower()
            if bajo.endswith(".ris") and texto_ris is None:
                texto_ris = zf.read(nombre).decode("utf-8", errors="replace")
            elif bajo.endswith(".pdf"):
                pdfs[nombre.replace("\\", "/")] = zf.read(nombre)
    return texto_ris, pdfs


def _buscar_pdf_en_zip(archivos_ris: list[str], pdfs: dict[str, bytes]) -> bytes | None:
    """Empareja el campo L1 del RIS con un miembro del ZIP (exacto o por sufijo)."""
    for ruta in archivos_ris:
        ruta_norm = ruta.replace("\\", "/")
        if ruta_norm in pdfs:
            return pdfs[ruta_norm]
        nombre = PurePosixPath(ruta_norm).name.lower()
        for miembro, data in pdfs.items():
            if PurePosixPath(miembro).name.lower() == nombre:
                return data
    return None


def _texto_desde_pdf(contenido: bytes) -> str | None:
    import pymupdf4llm

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contenido)
        ruta = tmp.name
    try:
        texto = pymupdf4llm.to_markdown(ruta)
        return texto if texto and len(texto.strip()) >= 100 else None
    except Exception as e:
        logger.warning("zotero_pdf_ilegible", error=str(e))
        return None
    finally:
        Path(ruta).unlink(missing_ok=True)


class ZoteroService:

    def importar(
        self,
        documento_id: str,
        contenido: bytes,
        nombre_archivo: str,
        notificar=lambda porcentaje, mensaje: None,
    ) -> dict:
        """Importa la colección y retorna el resumen (también persistido en Storage)."""
        notificar(5, "Leyendo la colección de Zotero...")

        pdfs_zip: dict[str, bytes] = {}
        if nombre_archivo.lower().endswith(".zip"):
            texto_ris, pdfs_zip = _extraer_zip(contenido)
            if texto_ris is None:
                raise ValueError("El ZIP no contiene ningún archivo .ris.")
        else:
            texto_ris = contenido.decode("utf-8", errors="replace")

        entradas = parsear_ris(texto_ris)
        if not entradas:
            raise ValueError("No se encontraron entradas válidas en el archivo RIS.")

        referencias = self._leer_referencias(documento_id)
        notificar(15, f"Emparejando {len(entradas)} entradas con {len(referencias)} referencias...")

        emparejamientos, no_emparejadas = self._emparejar(entradas, referencias)

        resumen = {
            "documento_id": documento_id,
            "entradas_ris": len(entradas),
            "emparejadas_por_doi": sum(1 for e in emparejamientos if e["metodo"] == "doi"),
            "emparejadas_por_titulo": sum(1 for e in emparejamientos if e["metodo"] == "titulo"),
            "no_emparejadas": [e["titulo"] for e in no_emparejadas],
            "con_pdf_zotero": 0,
            "descargadas_unpaywall": 0,
            "sin_texto": [],
            "importado_en": datetime.now(timezone.utc).isoformat(),
        }

        total = len(emparejamientos)
        for i, emp in enumerate(emparejamientos):
            entrada, ref = emp["entrada"], emp["referencia"]
            notificar(
                20 + int((i / max(total, 1)) * 75),
                f"Procesando {i + 1}/{total}: {entrada['titulo'][:60]}...",
            )

            # 1. Metadatos de Zotero sobrescriben los extraídos por el LLM.
            grafo_carga_service.actualizar_referencia(ref["ref_id"], {
                "titulo": entrada["titulo"],
                "anio": entrada["anio"],
                "doi": entrada["doi"] or None,
                "autores": entrada["autores"],
                "datos_incompletos": False,
            })

            # 2. Conseguir el texto del paper: adjunto del ZIP > Unpaywall por DOI.
            texto, nivel = None, None
            pdf = _buscar_pdf_en_zip(entrada["archivos"], pdfs_zip)
            if pdf:
                texto = _texto_desde_pdf(pdf)
                if texto:
                    nivel = "zotero"
                    resumen["con_pdf_zotero"] += 1
            if texto is None and entrada["doi"]:
                resultado = unpaywall_service.buscar_pdf_gratuito(entrada["doi"])
                if resultado.tiene_pdf_gratuito and resultado.texto_completo:
                    texto = resultado.texto_completo
                    nivel = "texto_completo"
                    resumen["descargadas_unpaywall"] += 1

            # 3. Indexar y actualizar la referencia.
            doi_indice = entrada["doi"] or f"manual_{ref['ref_id']}"
            if texto:
                self._limpiar_chunks_previos(ref, doi_indice)
                embedding_service.indexar_paper(
                    doi=doi_indice,
                    texto=texto,
                    metadata={
                        "doi": doi_indice,
                        "doi_normalizado": doi_para_chunks(doi_indice),
                        "titulo": entrada["titulo"],
                        "anio": str(entrada["anio"] or ""),
                        "nivel_confianza": nivel,
                        "referencia_id": ref["ref_id"],
                    },
                )
                self._actualizar_referencia_verificada(ref["ref_id"], doi_indice, nivel, emp["confianza"])
            else:
                resumen["sin_texto"].append(entrada["titulo"])
                if entrada["doi"]:
                    # DOI confiable pero sin PDF accesible: queda documentado.
                    self._actualizar_referencia_verificada(ref["ref_id"], entrada["doi"], "sin_texto", emp["confianza"])

        storage_service.subir_texto(
            _objeto_resultado(documento_id), json.dumps(resumen, ensure_ascii=False)
        )
        logger.info(
            "zotero_importado",
            doc_id=documento_id,
            emparejadas=total,
            no_emparejadas=len(no_emparejadas),
            con_pdf=resumen["con_pdf_zotero"] + resumen["descargadas_unpaywall"],
        )
        return resumen

    def leer_resultado(self, documento_id: str) -> dict | None:
        crudo = storage_service.leer_texto(_objeto_resultado(documento_id))
        return json.loads(crudo) if crudo else None

    # ── Internos ──────────────────────────────────────────────────────────────

    def _leer_referencias(self, documento_id: str) -> list[dict]:
        query = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
        OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
        RETURN r.id AS ref_id, r.titulo AS titulo, r.anio AS anio,
               r.doi AS doi, r.doi_verificado AS doi_verificado,
               collect(a.nombre) AS autores
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            return [dict(rec) for rec in session.run(query, doc_id=documento_id)]

    def _emparejar(
        self, entradas: list[dict], referencias: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """DOI exacto primero; luego título ≥ UMBRAL + primer autor + año."""
        disponibles = {r["ref_id"]: r for r in referencias}
        emparejamientos: list[dict] = []
        pendientes: list[dict] = []

        for entrada in entradas:
            ref = None
            if entrada["doi"]:
                ref = next(
                    (r for r in disponibles.values() if normalizar_doi(r.get("doi")) == entrada["doi"]),
                    None,
                )
            if ref:
                emparejamientos.append(
                    {"entrada": entrada, "referencia": ref, "metodo": "doi", "confianza": 1.0}
                )
                disponibles.pop(ref["ref_id"])
            else:
                pendientes.append(entrada)

        no_emparejadas: list[dict] = []
        for entrada in pendientes:
            titulo_e = _norm_titulo(entrada["titulo"])
            autor_e = _apellido_primer_autor(entrada["autores"])
            mejor, mejor_sim = None, 0.0
            for r in disponibles.values():
                sim = SequenceMatcher(None, titulo_e, _norm_titulo(r.get("titulo"))).ratio()
                if sim < UMBRAL_TITULO or sim <= mejor_sim:
                    continue
                if autor_e and _apellido_primer_autor(r.get("autores") or []) != autor_e:
                    continue
                if entrada["anio"] and r.get("anio") and entrada["anio"] != r["anio"]:
                    continue
                mejor, mejor_sim = r, sim
            if mejor:
                emparejamientos.append(
                    {"entrada": entrada, "referencia": mejor, "metodo": "titulo", "confianza": 0.9}
                )
                disponibles.pop(mejor["ref_id"])
            else:
                no_emparejadas.append(entrada)

        return emparejamientos, no_emparejadas

    @staticmethod
    def _limpiar_chunks_previos(ref: dict, doi_nuevo: str) -> None:
        """Si la referencia tenía otro paper indexado, borra sus chunks."""
        for doi_viejo in (ref.get("doi_verificado"), f"manual_{ref['ref_id']}"):
            doi_viejo = normalizar_doi(doi_viejo) or (doi_viejo or "")
            if doi_viejo and doi_viejo != doi_nuevo:
                supabase_vector_service.eliminar_chunks_por_doi(doi_para_chunks(doi_viejo))

    @staticmethod
    def _actualizar_referencia_verificada(
        referencia_id: str, doi: str, nivel: str, confianza: float
    ) -> None:
        query = """
        MATCH (r:Referencia {id: $ref_id})
        SET r.doi_verificado = $doi,
            r.nivel_confianza = $nivel,
            r.score_crossref = $confianza,
            r.verificado = true,
            r.verificado_en = datetime()
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            session.run(query, ref_id=referencia_id, doi=doi, nivel=nivel, confianza=confianza)


# Singleton
zotero_service = ZoteroService()
