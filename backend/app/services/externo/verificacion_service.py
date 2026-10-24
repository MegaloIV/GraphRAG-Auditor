"""
Servicio orquestador de verificación externa de referencias.
Une CrossRef + fuentes abiertas + Supabase/pgvector + Neo4j.

Flujo por referencia:
1. DOI declarado → se comprueba contra CrossRef que sea el del trabajo citado
   (título, o autores y año si el tesista tradujo el título). Si pertenece a
   otro trabajo se descarta y se marca la alerta `doi_no_corresponde`.
2. Sin DOI válido → resolución por título con validación estricta.
3. Con DOI → texto completo por la cascada de fuentes abiertas (Unpaywall,
   host de archivos de MDPI, Europe PMC, ficha del artículo, repositorio,
   arXiv). Todo texto conseguido debe corresponder al título citado.
4. Sin DOI → arXiv por título y, si no, la URL de la referencia (la extraída
   de la bibliografía si el parser no la trajo).
5. Supabase/pgvector (embeddings) + Neo4j (resultado, procedencia `sistema`,
   fuente y alertas) + copia en Drive.
"""
import re

import structlog

from app.services.externo.crossref_service import crossref_service
from app.services.externo.web_service import web_service
from app.services.externo.resolucion_service import resolucion_service
from app.services.externo.unpaywall_service import unpaywall_service
from app.services.externo.embedding_service import embedding_service
from app.services.externo.doi_utils import normalizar_doi, doi_para_chunks
from app.services.externo import fuentes_abiertas as fuentes
from app.services.grafo.grafo_carga_service import grafo_carga_service
from app.services.grafo.neo4j_service import neo4j_service
from app.services.storage.supabase_storage_service import storage_service
from app.services.vectorstore.supabase_service import supabase_vector_service
from app.models.grafo import ReferenciaAPA
from app.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# Proporción mínima de palabras del título citado que debe aparecer en la
# cabecera del texto conseguido para aceptarlo como la fuente.
_MIN_CONCORDANCIA = 0.6
# Las páginas web llevan menús y avisos antes del contenido.
_VENTANA_WEB = 8000


def _apellido(nombre: str) -> set[str]:
    return fuentes.normalizar_palabras((nombre or "").split(",")[0])


class VerificacionService:

    def verificar_referencias(
        self,
        referencias: list[ReferenciaAPA],
        documento_id: str,
        max_referencias: int = 10,
    ) -> dict:
        """
        Verifica las referencias de un documento.
        max_referencias: limita cuántas verificar (útil en desarrollo)
        """
        referencias_a_verificar = referencias[:max_referencias]
        self._titulo_tesis = self._leer_titulo_tesis(documento_id)
        # Texto de la tesis: de su bibliografía se recuperan las URLs que la
        # extracción de referencias no devolvió.
        self._texto_tesis = self._leer_texto_tesis(documento_id)
        total = len(referencias_a_verificar)
        omitidas = len(referencias) - total

        encontradas = 0
        con_abstract = 0
        con_texto_completo = 0
        no_encontradas = 0

        logger.info(
            "iniciando_verificacion",
            total=total,
            omitidas=omitidas,
            doc_id=documento_id
        )

        # Las referencias sin DOI se resuelven todas de golpe y en paralelo:
        # una a una son ~16 s cada una, casi todo esperando a los catálogos.
        sin_doi = [
            {"referencia_id": r.referencia_id, "titulo": r.titulo,
             "autores": r.autores, "anio": r.anio}
            for r in referencias_a_verificar if not normalizar_doi(r.doi)
        ]
        self._resoluciones = resolucion_service.resolver_lote(sin_doi) if sin_doi else {}
        humanas = self._referencias_con_texto_humano([r.referencia_id for r in referencias_a_verificar])
        self._biblioteca = supabase_vector_service.catalogo_texto_completo()

        for i, referencia in enumerate(referencias_a_verificar):
            # Un texto que buscó y subió una persona no se reemplaza al volver
            # a verificar: se perdería trabajo humano sin que nadie lo note.
            if referencia.referencia_id in humanas:
                logger.info("referencia_con_texto_humano_se_conserva", ref_id=referencia.referencia_id)
                encontradas += 1
                con_texto_completo += 1
                continue

            logger.info(
                "verificando_referencia",
                numero=i + 1,
                total=total,
                titulo=referencia.titulo[:50],
            )

            resultado = self._verificar_una_referencia(referencia)

            if not resultado["encontrada"]:
                no_encontradas += 1
                self._marcar_no_encontrada(
                    referencia.referencia_id,
                    motivo=resultado.get("motivo_sin_texto") or "",
                    alerta=resultado.get("alerta"),
                    url=resultado.get("url_texto"),
                )
                continue

            encontradas += 1
            if resultado["nivel_confianza"] in ("texto_completo", "pagina_web"):
                con_texto_completo += 1
            elif resultado["nivel_confianza"] == "abstract":
                con_abstract += 1

            self._actualizar_neo4j(referencia.referencia_id, resultado)

        sin_texto = encontradas - con_abstract - con_texto_completo
        resumen = {
            "total": total,
            "encontradas": encontradas,
            "no_encontradas": no_encontradas,
            "con_texto_completo": con_texto_completo,
            "con_abstract_solo": con_abstract,
            "encontradas_sin_texto": sin_texto,
            "cobertura_porcentaje": round((encontradas / max(total, 1)) * 100, 1),
        }

        logger.info("verificacion_completada", **resumen)
        logger.info(
            "cobertura_texto_para_auditoria",
            texto_completo_pct=round((con_texto_completo / max(total, 1)) * 100, 1),
            solo_abstract_pct=round((con_abstract / max(total, 1)) * 100, 1),
            sin_texto_pct=round(((no_encontradas + sin_texto) / max(total, 1)) * 100, 1),
            advertencia=(
                "Alto porcentaje sin texto completo: la auditoría producirá muchos NO_INFO"
                if (no_encontradas + sin_texto) / max(total, 1) > 0.5 else None
            ),
        )
        return resumen

    # ── Una referencia ──────────────────────────────────────────────────────

    def _verificar_una_referencia(self, referencia: ReferenciaAPA) -> dict:
        doi = normalizar_doi(referencia.doi)
        alerta: str | None = None
        resultado_crossref = None

        # 1. Un DOI declarado se comprueba antes de usarlo: si el tesista lo
        # copió mal y pertenece a otro trabajo, indexar su texto haría juzgar
        # las citas contra una fuente que no es la citada.
        if doi:
            resultado_crossref = crossref_service.obtener_por_doi(doi)
            if resultado_crossref.encontrado and not self._doi_corresponde(referencia, resultado_crossref):
                logger.info(
                    "doi_declarado_no_corresponde", ref_id=referencia.referencia_id,
                    doi=doi, titulo_citado=referencia.titulo[:60],
                    titulo_del_doi=(resultado_crossref.titulo_oficial or "")[:60],
                )
                alerta = "doi_no_corresponde"
                doi, resultado_crossref = "", None

        # 2. Sin DOI válido se intenta resolver por título contra catálogos
        # abiertos, con validación estricta (título, primer autor y año).
        resuelto_por_titulo = False
        titulo_resuelto: str | None = None
        if not doi:
            resolucion = (
                None if alerta else getattr(self, "_resoluciones", {}).get(referencia.referencia_id)
            ) or resolucion_service.resolver(
                titulo=referencia.titulo, autores=referencia.autores, anio=referencia.anio,
            )
            if resolucion.encontrado:
                doi = resolucion.doi
                resuelto_por_titulo = True
                titulo_resuelto = resolucion.titulo_oficial

        # 3. Sin DOI: primero la biblioteca propia, luego arXiv por título y,
        # si no, la URL.
        if not doi:
            en_biblioteca = self._desde_biblioteca(referencia, alerta)
            if en_biblioteca:
                return en_biblioteca
            hallazgo = fuentes.desde_arxiv(referencia.titulo)
            if not hallazgo:
                # CORE indexa repositorios institucionales, que es donde están
                # las tesis y los informes que no tienen DOI declarado.
                por_titulo = (fuentes.desde_core("", referencia.titulo, referencia.autores,
                                                referencia.anio)
                              if settings.usar_apis_academicas else None)
                if por_titulo and self._corresponde(referencia, por_titulo.texto):
                    self._indexar(referencia, referencia.referencia_id, por_titulo.texto,
                                  "texto_completo", referencia.titulo, por_titulo.pdf)
                    return {
                        "encontrada": True, "doi": "", "nivel_confianza": "texto_completo",
                        "texto": por_titulo.texto, "titulo_oficial": referencia.titulo,
                        "score_coincidencia": 1.0, "doi_resuelto_por_titulo": False,
                        "motivo_sin_texto": None, "fuente": "core",
                        "url_texto": por_titulo.url, "alerta": alerta,
                    }
            if hallazgo and self._corresponde(referencia, hallazgo.texto):
                doi_arxiv = "10.48550/arxiv." + hallazgo.url.rsplit("/", 1)[-1]
                self._indexar(referencia, doi_arxiv, hallazgo.texto, "texto_completo",
                              referencia.titulo, hallazgo.pdf)
                return {
                    "encontrada": True, "doi": doi_arxiv, "nivel_confianza": "texto_completo",
                    "texto": hallazgo.texto, "titulo_oficial": referencia.titulo,
                    "score_coincidencia": 1.0, "doi_resuelto_por_titulo": True,
                    "motivo_sin_texto": None, "fuente": "arxiv", "url_texto": hallazgo.url,
                    "alerta": alerta,
                }
            return self._verificar_por_url(referencia, alerta)

        # 4. Con DOI: metadatos oficiales y texto completo.
        if resultado_crossref is None:
            resultado_crossref = crossref_service.obtener_por_doi(doi)
        if resultado_crossref.encontrado and resultado_crossref.referencias_externas:
            grafo_carga_service.cargar_referencias_externas(
                referencia.referencia_id, resultado_crossref.referencias_externas
            )
        titulo_oficial = (
            resultado_crossref.titulo_oficial if resultado_crossref.encontrado else None
        ) or titulo_resuelto
        texto_disponible = resultado_crossref.abstract if resultado_crossref.encontrado else None
        nivel_confianza = "abstract" if texto_disponible else None

        # La descarga se salta SOLO si ya está el paper completo con el
        # chunking actual: un paper guardado como abstract en una corrida
        # previa debe reintentarse.
        if (
            embedding_service.indexado_y_actualizado(doi)
            and embedding_service.indexado_con_texto_completo(doi)
        ):
            logger.info("paper_ya_en_cache", doi=doi)
            return {
                "encontrada": True, "doi": doi, "nivel_confianza": "texto_completo",
                "texto": texto_disponible, "titulo_oficial": titulo_oficial,
                "score_coincidencia": 1.0, "doi_resuelto_por_titulo": resuelto_por_titulo,
                "fuente": "cache", "alerta": alerta,
            }

        en_biblioteca = self._desde_biblioteca(referencia, alerta, titulo_oficial)
        if en_biblioteca:
            return {**en_biblioteca, "doi_resuelto_por_titulo": resuelto_por_titulo}

        texto, fuente, url_texto, pdf, motivo_sin_texto = self._adquirir_texto(
            doi, referencia, titulo_oficial, datacite=not resultado_crossref.encontrado
        )
        if texto:
            texto_disponible, nivel_confianza = texto, "texto_completo"
            logger.info("usando_texto_completo", doi=doi, fuente=fuente)
        elif not resultado_crossref.encontrado:
            # Un DOI ausente de CrossRef todavía puede ser legítimo: los
            # repositorios institucionales y las tesis registran en DataCite.
            # Solo se declara inexistente si tampoco está allí; si no, se
            # estaría acusando al tesista de inventarse una fuente real.
            en_datacite = resolucion_service.existe_en_datacite(doi)
            if en_datacite:
                titulo_oficial = titulo_oficial or en_datacite.get("titulo")
                motivo_sin_texto = motivo_sin_texto or "sin_pdf_en_repositorio"
                logger.info("doi_hallado_en_datacite", doi=doi, editor=en_datacite.get("editor"))
            else:
                motivo_sin_texto = "doi_inexistente"

        if texto_disponible:
            self._indexar(referencia, doi, texto_disponible, nivel_confianza,
                          titulo_oficial or referencia.titulo, pdf if texto else None,
                          anio=resultado_crossref.anio_oficial)

        return {
            "encontrada": True,
            "doi": doi,
            # DOI válido pero sin texto descargable: se distingue de
            # 'no_encontrado' para que la UI explique el caso.
            "nivel_confianza": nivel_confianza or "sin_texto",
            "texto": texto_disponible,
            "titulo_oficial": titulo_oficial,
            "score_coincidencia": 1.0,
            "doi_resuelto_por_titulo": resuelto_por_titulo,
            "motivo_sin_texto": None if texto else motivo_sin_texto,
            "fuente": fuente if texto else ("crossref_abstract" if texto_disponible else None),
            "url_texto": url_texto,
            "alerta": alerta,
        }

    def _adquirir_texto(self, doi: str, referencia: ReferenciaAPA,
                        titulo_oficial: str | None, datacite: bool = False):
        """
        Texto completo por la cascada de fuentes abiertas. Devuelve
        (texto, fuente, url, pdf, motivo). Cada candidato debe corresponder al
        título citado; si no, se descarta y se prueba la fuente siguiente.
        """
        motivo = None
        res = unpaywall_service.buscar_pdf_gratuito(
            doi, titulo_tesis=getattr(self, "_titulo_tesis", "")
        )
        if res.tiene_pdf_gratuito and res.texto_completo:
            if self._corresponde(referencia, res.texto_completo, titulo_oficial):
                return res.texto_completo, "unpaywall", res.url_pdf, res.pdf, None
            motivo = "texto_no_corresponde"
        else:
            motivo = res.motivo

        # CORE va antes que la ficha del editor y que el repositorio: devuelve
        # el texto ya extraído y no se topa con el antirrobot, que es lo que
        # hacía fallar a una de cada tres descargas.
        cascada = [fuentes.desde_mdpi, fuentes.desde_europepmc]
        if settings.usar_apis_academicas:
            # Se activa solo de forma explícita: ver config.
            cascada.append(
                lambda d: fuentes.desde_core(d, titulo_oficial or referencia.titulo,
                                             referencia.autores, referencia.anio))
        cascada.append(fuentes.desde_landing)
        if datacite:
            # DOIs de DataCite suelen ser tesis de repositorios institucionales.
            cascada.append(lambda d: fuentes.desde_repositorio(f"https://doi.org/{d}"))
        cascada.append(lambda _d: fuentes.desde_arxiv(titulo_oficial or referencia.titulo))

        for buscar in cascada:
            hallazgo = buscar(doi)
            if not hallazgo:
                continue
            if not self._corresponde(referencia, hallazgo.texto, titulo_oficial):
                logger.info("texto_descartado_no_corresponde", doi=doi, fuente=hallazgo.fuente)
                motivo = "texto_no_corresponde"
                continue
            # Se guarda como el resto de papers: una segunda verificación lo
            # toma de ahí sin volver a descargar.
            storage_service.subir_texto(f"papers/{doi_para_chunks(doi)}.txt", hallazgo.texto)
            logger.info("texto_obtenido_fuente_abierta", doi=doi, fuente=hallazgo.fuente)
            return hallazgo.texto, hallazgo.fuente, hallazgo.url, hallazgo.pdf, None
        return None, None, None, None, motivo

    def _verificar_por_url(self, referencia: ReferenciaAPA, alerta: str | None) -> dict:
        """
        Fuentes sin DOI: documentación técnica, informes, tesis en
        repositorios. Si el parser no trajo la URL se busca en el texto de la
        bibliografía. El sistema no corrige URLs rotas: las reporta.
        """
        url = referencia.url or fuentes.url_desde_bibliografia(
            getattr(self, "_texto_tesis", ""), referencia.titulo
        )
        no_hallada = {"encontrada": False, "doi": None, "nivel_confianza": None, "texto": None,
                      "alerta": alerta, "url_texto": url}
        if not url:
            logger.info("referencia_sin_doi_no_verificable", ref_id=referencia.referencia_id,
                        titulo=referencia.titulo[:50])
            return {**no_hallada, "motivo_sin_texto": alerta or "sin_doi_y_no_resuelta_por_titulo"}
        if fuentes.es_url_generica(url):
            return {**no_hallada, "motivo_sin_texto": "url_generica", "alerta": alerta or "url_generica"}
        if fuentes.es_catalogo_de_libro(url):
            return {**no_hallada, "motivo_sin_texto": "libro_sin_acceso"}

        es_pagina = False
        if fuentes.es_repositorio(url):
            hallazgo = fuentes.desde_repositorio(url)
            motivo = None if hallazgo else "pdf_restringido"
        elif url.lower().split("?")[0].endswith(".pdf"):
            hallazgo = fuentes.desde_url_pdf(url)
            motivo = None if hallazgo else "descarga_fallida"
        else:
            web = web_service.obtener_texto(url)
            hallazgo = fuentes.Hallazgo(web.texto, url, "web") if web.obtenido else None
            motivo, es_pagina = web.motivo, True

        if not hallazgo:
            alerta_url = "url_rota" if motivo == "url_rota" else alerta
            return {**no_hallada, "motivo_sin_texto": motivo, "alerta": alerta_url}
        if not self._corresponde(referencia, hallazgo.texto,
                                 ventana=_VENTANA_WEB if es_pagina else 3000):
            return {**no_hallada, "motivo_sin_texto": "contenido_no_corresponde"}

        clave = f"web_{referencia.referencia_id}"
        nivel = "pagina_web" if es_pagina else "texto_completo"
        self._indexar(referencia, clave, hallazgo.texto, nivel, referencia.titulo, hallazgo.pdf)
        logger.info("referencia_url_indexada", ref_id=referencia.referencia_id,
                    fuente=hallazgo.fuente, url=url[:70])
        return {
            "encontrada": True, "doi": clave, "nivel_confianza": nivel, "texto": hallazgo.texto,
            "titulo_oficial": referencia.titulo, "score_coincidencia": 1.0,
            "motivo_sin_texto": None, "fuente": hallazgo.fuente, "url_texto": url, "alerta": alerta,
        }

    def _desde_biblioteca(self, referencia: ReferenciaAPA, alerta: str | None,
                          titulo_oficial: str | None = None) -> dict | None:
        """
        ¿El paper ya está en la biblioteca (Supabase) con texto completo?

        La biblioteca es compartida entre tesis: un paper que se consiguió una
        vez —por el sistema o subido por una persona— queda disponible para
        cualquier referencia que lo cite después. Se empareja por título y
        año con la misma tolerancia que absorbe las variaciones del parser de
        referencias (un punto final, el nombre del congreso pegado al título).
        """
        candidatos = []
        for p in getattr(self, "_biblioteca", []) or []:
            if not self._anios_compatibles(referencia.anio, p.get("anio")):
                continue
            for titulo in filter(None, (referencia.titulo, titulo_oficial)):
                sim = self._parecido_titulo(titulo, p.get("titulo") or "")
                if sim:
                    candidatos.append((sim, p))
                    break
        if not candidatos:
            return None
        candidatos.sort(key=lambda x: x[0], reverse=True)
        if len(candidatos) > 1 and candidatos[1][0] == candidatos[0][0] \
                and candidatos[1][1]["doi_normalizado"] != candidatos[0][1]["doi_normalizado"]:
            logger.info("biblioteca_ambigua", titulo=referencia.titulo[:60])
            return None
        p = candidatos[0][1]
        nivel = "pagina_web" if p.get("nivel_confianza") == "pagina_web" else "texto_completo"
        clave = p["doi_normalizado"]
        logger.info("paper_hallado_en_biblioteca", ref_id=referencia.referencia_id, clave=clave)
        self._respaldar_en_drive(clave, supabase_vector_service.texto_de_paper(clave), None, "sistema")
        return {
            "encontrada": True, "doi": clave, "nivel_confianza": nivel, "texto": None,
            "titulo_oficial": titulo_oficial or p.get("titulo") or referencia.titulo,
            "score_coincidencia": 1.0, "motivo_sin_texto": None,
            "fuente": f"biblioteca ({p.get('nivel_confianza')})", "url_texto": None,
            "alerta": alerta,
        }

    @staticmethod
    def _anios_compatibles(a, b) -> bool:
        """Mismo año ±1; si falta o no es un número («s.f.»), no descarta."""
        try:
            return abs(int(str(a)[:4]) - int(str(b)[:4])) <= 1
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _parecido_titulo(a: str, b: str) -> float:
        """Similitud de títulos tolerante: Jaccard ≥ 0,8, o uno contiene al
        otro con al menos cuatro palabras. 0 si no se parecen."""
        pa, pb = fuentes.normalizar_palabras(a), fuentes.normalizar_palabras(b)
        if len(pa) < 3 or len(pb) < 3:
            return 0.0
        inter = len(pa & pb)
        jac = inter / len(pa | pb)
        if jac >= 0.8:
            return jac
        if inter == min(len(pa), len(pb)) and inter >= 4:
            return 0.8
        return 0.0

    # ── Validaciones ────────────────────────────────────────────────────────

    @staticmethod
    def _doi_corresponde(referencia: ReferenciaAPA, crossref) -> bool:
        """
        ¿El DOI declarado es el del trabajo citado? Basta con que coincida el
        título, o el primer autor y el año (el tesista puede haber traducido
        el título al español).
        """
        oficial = crossref.titulo_oficial or ""
        if not oficial:
            return True
        pc, po = fuentes.normalizar_palabras(referencia.titulo), fuentes.normalizar_palabras(oficial)
        if pc and po:
            inter = len(pc & po)
            if inter / len(pc | po) >= 0.5 or (inter == min(len(pc), len(po)) and inter >= 4):
                return True
        if referencia.autores and crossref.autores_oficiales:
            esperado = _apellido(referencia.autores[0])
            autor_ok = any(esperado and esperado <= _apellido(a) | fuentes.normalizar_palabras(a)
                           for a in crossref.autores_oficiales)
            anio_ok = (not referencia.anio or not crossref.anio_oficial
                       or abs(int(referencia.anio) - int(crossref.anio_oficial)) <= 1)
            if autor_ok and anio_ok:
                return True
        return False

    @staticmethod
    def _corresponde(referencia: ReferenciaAPA, texto: str,
                     titulo_oficial: str | None = None, ventana: int = 3000) -> bool:
        """El texto conseguido es la fuente citada (título o autores+año en su cabecera)."""
        c = fuentes.concordancia(referencia.titulo, texto, referencia.autores,
                                 referencia.anio, ventana=ventana)
        if titulo_oficial and c < _MIN_CONCORDANCIA:
            c = max(c, fuentes.concordancia(titulo_oficial, texto, ventana=ventana))
        return c >= _MIN_CONCORDANCIA

    # ── Persistencia ────────────────────────────────────────────────────────

    def _indexar(self, referencia: ReferenciaAPA, clave: str, texto: str, nivel: str,
                 titulo: str, pdf: bytes | None, anio=None) -> None:
        embedding_service.indexar_paper(
            doi=clave,
            texto=texto,
            metadata={
                "doi": clave,
                "doi_normalizado": doi_para_chunks(clave),
                "titulo": titulo,
                "anio": str(anio or referencia.anio or ""),
                "nivel_confianza": nivel,
                "referencia_id": referencia.referencia_id,
            },
        )
        if nivel in ("texto_completo", "pagina_web"):
            self._respaldar_en_drive(clave, texto, pdf, "sistema")

    def _respaldar_en_drive(self, clave: str, texto: str, pdf: bytes | None, procedencia: str) -> None:
        """
        Copia en la carpeta de la tesis en Drive: el PDF si existe y siempre el
        texto. Nunca interrumpe: si Drive falla, el texto ya está indexado.
        """
        from app.services.storage.drive_service import drive_service

        titulo_tesis = getattr(self, "_titulo_tesis", "")
        if not drive_service.activo or not titulo_tesis:
            return
        nombre = f"[{procedencia}] {re.sub(r'[^A-Za-z0-9._-]+', '_', clave)}"
        if pdf:
            drive_service.subir_paper(titulo_tesis, f"{nombre}.pdf", pdf, "application/pdf")
        drive_service.subir_paper(titulo_tesis, f"{nombre}.md", texto.encode("utf-8"), "text/markdown")

    @staticmethod
    def _referencias_con_texto_humano(ref_ids: list[str]) -> set[str]:
        """Referencias cuyo texto aportó una persona (subida manual)."""
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                return {
                    x["id"] for x in session.run(
                        """MATCH (r:Referencia) WHERE r.id IN $ids
                           AND (r.procedencia_texto = 'humano' OR r.origen_manual = true)
                           AND r.nivel_confianza = 'manual'
                           RETURN r.id AS id""",
                        ids=ref_ids,
                    )
                }
        except Exception as e:
            logger.warning("no_se_pudo_leer_referencias_humanas", error=str(e)[:80])
            return set()

    @staticmethod
    def _leer_texto_tesis(documento_id: str) -> str:
        """Markdown de la tesis que guarda el pipeline; si no está (documentos
        cargados por otra vía), se reconstruye desde el PDF subido."""
        texto = storage_service.leer_texto(f"processed/{documento_id}.md")
        if texto:
            return texto
        try:
            from app.services.ingesta.pdf_service import pdf_service
            ruta = storage_service.obtener_local(f"uploads/{documento_id}.pdf")
            if ruta:
                texto, _ = pdf_service.extraer_texto(ruta)
                return texto or ""
        except Exception as e:
            logger.warning("texto_tesis_no_disponible", doc_id=documento_id, error=str(e)[:80])
        return ""

    @staticmethod
    def _leer_titulo_tesis(documento_id: str) -> str:
        """Título de la tesis: da nombre a su carpeta de papers en Drive."""
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                rec = session.run(
                    "MATCH (d:Documento {id: $doc_id}) "
                    "RETURN coalesce(d.titulo, d.nombre_archivo, $doc_id) AS titulo",
                    doc_id=documento_id,
                ).single()
            return (rec["titulo"] if rec else documento_id) or documento_id
        except Exception:
            return documento_id

    def _marcar_no_encontrada(self, referencia_id: str, motivo: str = "",
                              alerta: str | None = None, url: str | None = None) -> None:
        query = """
        MATCH (r:Referencia {id: $ref_id})
        SET r.nivel_confianza = 'no_encontrado',
            // Sin texto, la referencia no puede seguir apuntando a fragmentos
            // de una verificación anterior: el juez los usaría como evidencia.
            r.doi_verificado = null,
            r.motivo_sin_texto = $motivo,
            r.alerta_referencia = $alerta,
            r.url_texto = $url,
            r.procedencia_texto = null,
            r.verificado = true,
            r.verificado_en = datetime()
        """
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                session.run(query, ref_id=referencia_id, motivo=motivo, alerta=alerta, url=url)
        except Exception as e:
            logger.error("error_marcando_no_encontrada", ref_id=referencia_id, error=str(e))

    def _actualizar_neo4j(self, referencia_id: str, resultado: dict) -> None:
        """
        Actualiza el nodo Referencia en Neo4j con los resultados
        de la verificación externa.
        """
        con_texto = resultado.get("nivel_confianza") in ("texto_completo", "pagina_web", "abstract")
        query = """
        MATCH (r:Referencia {id: $ref_id})
        SET r.doi_verificado = $doi,
            r.titulo_oficial = $titulo_oficial,
            r.nivel_confianza = $nivel_confianza,
            r.score_crossref = $score,
            r.verificado = true,
            // La referencia no traía DOI: se resolvió por título. La revisión
            // humana debería confirmar que es el paper correcto.
            r.doi_resuelto_por_titulo = $doi_resuelto_por_titulo,
            // Por qué no se pudo obtener el texto. Vacío si sí se obtuvo.
            r.motivo_sin_texto = $motivo_sin_texto,
            // Quién consiguió el texto (el sistema solo, o una persona al
            // subirlo) y de qué fuente: el informe separa ambas versiones.
            r.procedencia_texto = $procedencia,
            // Un texto servido desde la caché conserva la fuente de la que se
            // obtuvo la primera vez.
            r.origen_texto = CASE WHEN $fuente = 'cache'
                                  THEN coalesce(r.origen_texto, 'cache') ELSE $fuente END,
            r.url_texto = $url_texto,
            // Problema detectado en la propia referencia (DOI de otro
            // trabajo, URL rota o genérica): es un hallazgo sobre la tesis.
            r.alerta_referencia = $alerta,
            r.verificado_en = datetime()
        """
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                session.run(
                    query,
                    ref_id=referencia_id,
                    doi=resultado.get("doi", ""),
                    titulo_oficial=resultado.get("titulo_oficial", ""),
                    doi_resuelto_por_titulo=bool(resultado.get("doi_resuelto_por_titulo")),
                    motivo_sin_texto=resultado.get("motivo_sin_texto") or "",
                    nivel_confianza=resultado.get("nivel_confianza") or "no_encontrado",
                    score=resultado.get("score_coincidencia", 0.0),
                    procedencia="sistema" if con_texto else None,
                    fuente=resultado.get("fuente"),
                    url_texto=resultado.get("url_texto"),
                    alerta=resultado.get("alerta"),
                )
        except Exception as e:
            logger.error("error_actualizando_neo4j", ref_id=referencia_id, error=str(e))


# Singleton
verificacion_service = VerificacionService()
