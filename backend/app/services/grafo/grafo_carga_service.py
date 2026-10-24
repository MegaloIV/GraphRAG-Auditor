import re
import structlog
from unidecode import unidecode

from app.core.config import get_settings
from app.services.grafo.neo4j_service import neo4j_service
from app.models.grafo import ReferenciaAPA, CitaEnTexto, ResumenGrafo, TipoCita

logger = structlog.get_logger(__name__)
settings = get_settings()

# Proporción mínima de citas vinculadas para considerar el grafo utilizable.
_MIN_TASA_VINCULACION = 0.70


def _normalizar_nombre(nombre: str) -> str:
    """
    Convierte 'García, J.' en 'garcia, j.'
    Así dos referencias que citan al mismo autor
    con distinta tilde o mayúscula apuntan al mismo nodo.
    """
    return unidecode(nombre.strip().lower())


# ── Emparejamiento cita ↔ referencia ─────────────────────────────────────────
# El texto de la cita llega ya canonizado por el detector ("(Autores, año)" o
# "Autores (año)"), así que se parsea con precisión en vez de barrer el texto
# buscando palabras capitalizadas. Eso importa: con apellidos compuestos, el
# barrido producía tokens sueltos ("Hernández", "Sampieri") que enganchaban
# referencias ajenas.

_RE_ANIO_CITA = re.compile(r"\b((?:19|20)\d{2})[a-z]?\b")
_RE_ETAL = re.compile(r"\s*,?\s*et\.?\s+al\.?\s*", re.IGNORECASE)
_RE_SEPARADOR_AUTORES = re.compile(r"\s*(?:&|;|,|\s+y\s+|\s+e\s+|\s+and\s+)\s*")


def _clave_apellido(texto: str) -> str:
    """'Johnson-Laird, P. N.' → 'johnson laird'. Guion y tilde se neutralizan."""
    apellido = texto.split(",")[0]
    apellido = unidecode(apellido).lower().replace("-", " ")
    apellido = re.sub(r"[^a-z\s]", " ", apellido)
    return re.sub(r"\s+", " ", apellido).strip()


def _apellidos_de_cita(texto_cita: str) -> tuple[list[str], int | None]:
    """Apellidos y año de una cita canónica, en orden de aparición."""
    anios = _RE_ANIO_CITA.findall(texto_cita or "")
    anio = int(anios[-1]) if anios else None

    # Quitar el año y los paréntesis: queda solo el bloque de autores.
    autores_txt = _RE_ANIO_CITA.sub("", texto_cita or "")
    autores_txt = autores_txt.replace("(", " ").replace(")", " ")
    autores_txt = _RE_ETAL.sub(" ", autores_txt)

    apellidos = []
    for parte in _RE_SEPARADOR_AUTORES.split(autores_txt):
        clave = _clave_apellido(parte)
        if clave and clave not in apellidos:
            apellidos.append(clave)
    return apellidos, anio


def _difiere_en_una_letra(a: str, b: str) -> bool:
    """
    Distancia de edición 1 entre apellidos largos: cubre erratas y letras que
    el PDF se come ("Wittman" por "Wittmann"). Se exige longitud mínima para
    no emparejar apellidos cortos que solo se parecen ("Lin" con "Liu").
    """
    if min(len(a), len(b)) < 6 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    corto, largo = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(largo)):
        if largo[:i] + largo[i + 1:] == corto:
            return True
    return False


def _puntuar(apellidos_cita: list[str], apellidos_ref: list[str]) -> tuple[float, str] | None:
    """
    Confianza del emparejamiento entre los apellidos de la cita y los de la
    referencia (el año ya se comprobó fuera). None si no hay coincidencia.

    No se compara por prefijo de cadena: "yusuf".startswith("yu") enlazaba
    (Yusuf et al., 2025) con la referencia de Yu, S. — una cita atribuida a un
    paper ajeno, que la auditoría luego verificaba contra la fuente incorrecta.
    La comparación es por palabras completas.
    """
    if not apellidos_cita or not apellidos_ref:
        return None

    if apellidos_cita[0] == apellidos_ref[0]:
        return 0.98, "primer_apellido_anio"

    if set(apellidos_cita) & set(apellidos_ref):
        return 0.90, "coautor_anio"

    # Apellido compuesto abreviado en el texto: "(Sandoval, 2023)" contra
    # "Sandoval Chunga, M. C." — y también por el SEGUNDO apellido, que en Perú
    # es de uso corriente: "(Araoz et al., 2022)" por "Estrada Araoz, E. G.".
    # Que una parte esté contenida en la otra ya es exigente: "yusuf" no está
    # contenido en "yu".
    for ac in apellidos_cita:
        for ar in apellidos_ref:
            palabras_c, palabras_r = set(ac.split()), set(ar.split())
            if not palabras_c or not palabras_r:
                continue
            if palabras_c <= palabras_r or palabras_r <= palabras_c:
                return 0.75, "apellido_compuesto_anio"

    # Último recurso: el tesista escribió nombre y apellido en otro orden
    # ("Zulham Handayani" por "Handayani, Z."). Se exige coincidencia de una
    # PALABRA COMPLETA; comparar por prefijo es lo que producía enlaces a
    # referencias ajenas.
    palabras_cita = {p for ac in apellidos_cita for p in ac.split() if len(p) > 2}
    palabras_ref = {p for ar in apellidos_ref for p in ar.split() if len(p) > 2}
    if palabras_cita & palabras_ref:
        return 0.65, "palabra_apellido_anio"

    for pc in palabras_cita:
        for pr in palabras_ref:
            if _difiere_en_una_letra(pc, pr):
                return 0.70, "apellido_aproximado_anio"
    return None


# Palabras demasiado comunes en un título para identificar una fuente por sí solas.
_PALABRAS_TITULO_GENERICAS = frozenset("""
cancer salud educacion educación sistema sistemas modelo modelos datos data
analisis análisis estudio estudios informe reporte guide guia guía review
learning machine deep intelligence artificial university universidad revista
journal international nacional research investigacion investigación
""".split())


def _coincide_con_titulo(apellidos_cita: list[str], titulo: str) -> bool:
    """
    ¿La cita nombra a la institución que da título a la referencia?

    Cubre "(IREN NORTE, 2024)" contra "Registro Hospitalario del Cancer IREN
    NORTE": la referencia la firma una persona, pero el tesista la cita por la
    institución. Se exige que TODAS las palabras del nombre citado estén en el
    título y que al menos una no sea genérica, para que "Cancer (2020)" no
    enganche cualquier título de oncología.
    """
    if not titulo:
        return False
    palabras_titulo = set(_clave_apellido(titulo).split())
    for nombre in apellidos_cita:
        palabras = [p for p in nombre.split() if len(p) > 2]
        if len(palabras) < 2:
            continue
        if not set(palabras) <= palabras_titulo:
            continue
        if any(p not in _PALABRAS_TITULO_GENERICAS for p in palabras):
            return True
    return False


def emparejar_cita(
    texto_cita: str, referencias: list[dict]
) -> tuple[str | None, float, str, list[str]]:
    """
    Elige la referencia de una cita.

    `referencias` es [{"ref_id", "anio", "autores"}]. Devuelve
    (ref_id, confianza, metodo, alternativas): `alternativas` trae los ref_id
    que empataron en la mejor puntuación. Si hay empate la cita es ambigua y
    NO debe presentarse como resuelta: el algoritmo anterior se quedaba con la
    primera que encontraba, en silencio.
    """
    apellidos_cita, anio = _apellidos_de_cita(texto_cita)
    if not apellidos_cita or anio is None:
        return None, 0.0, "sin_datos", []

    candidatos: list[tuple[float, str, str]] = []
    for ref in referencias:
        if ref.get("anio") != anio:
            continue
        apellidos_ref = [
            _clave_apellido(a) for a in (ref.get("autores") or []) if _clave_apellido(a)
        ]
        puntuacion = _puntuar(apellidos_cita, apellidos_ref)
        if not puntuacion and _coincide_con_titulo(apellidos_cita, ref.get("titulo") or ""):
            puntuacion = (0.60, "institucion_en_titulo_anio")
        if puntuacion:
            candidatos.append((puntuacion[0], puntuacion[1], ref["ref_id"]))

    if not candidatos:
        return None, 0.0, "sin_coincidencia", []

    mejor = max(c[0] for c in candidatos)
    empatados = [c for c in candidatos if c[0] == mejor]

    # El nivel por palabra suelta es un indicio débil: si además empata entre
    # varias referencias no distingue nada y enlaza cualquiera. "Cancer (2020)"
    # coincidía por la palabra "cancer" con dos institutos distintos.
    if mejor <= 0.65 and len(empatados) > 1:  # incluye el nivel institucional
        return None, 0.0, "ambiguo_descartado", [c[2] for c in empatados]
    elegido = empatados[0]
    alternativas = [c[2] for c in empatados[1:]]
    metodo = elegido[1] + ("_ambiguo" if alternativas else "")
    return elegido[2], mejor, metodo, alternativas

class GrafoCargaService:

    def cargar_documento(
        self, documento_id: str, nombre_archivo: str, titulo: str = ""
    ) -> None:
        """
        Crea o actualiza el nodo Documento.

        `titulo` es el de la portada. El nombre del archivo no sirve como
        título en los informes: suele ser un código del repositorio.
        """
        query = """
        MERGE (d:Documento {id: $id})
        SET d.nombre_archivo = $nombre,
            d.titulo = CASE WHEN $titulo <> '' THEN $titulo ELSE d.titulo END,
            d.actualizado_en = datetime()
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            session.run(query, id=documento_id, nombre=nombre_archivo, titulo=titulo)
        logger.info("documento_cargado", doc_id=documento_id)

    def cargar_referencias(
        self,
        documento_id: str,
        referencias: list[ReferenciaAPA],
    ) -> int:
        """
        Carga referencias y sus autores al grafo.
        Crea relaciones TIENE_REFERENCIA y ESCRITO_POR.
        Retorna el número de referencias cargadas.
        """
        cargadas = 0
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            for ref in referencias:

                # MERGE de la referencia
                query_ref = """
                MERGE (r:Referencia {id: $ref_id})
                SET r.titulo = $titulo,
                    r.anio = $anio,
                    r.fuente = $fuente,
                    r.doi = $doi,
                    r.url = $url,
                    r.datos_incompletos = $incompleto
                WITH r
                MATCH (d:Documento {id: $doc_id})
                MERGE (d)-[:TIENE_REFERENCIA]->(r)
                """
                session.run(
                    query_ref,
                    ref_id=ref.referencia_id,
                    titulo=ref.titulo,
                    anio=ref.anio,
                    fuente=ref.fuente or "",
                    doi=ref.doi or "",
                    url=ref.url or "",
                    incompleto=ref.datos_incompletos,
                    doc_id=documento_id,
                )

                # MERGE de cada autor y relación ESCRITO_POR. El orden se
                # guarda en la relación: collect() no lo preserva y sin él no
                # se distingue al primer autor —el que APA usa en la cita— de
                # un coautor cualquiera.
                for posicion, nombre in enumerate(ref.autores):
                    nombre_norm = _normalizar_nombre(nombre)
                    query_autor = """
                    MERGE (a:Autor {nombre_normalizado: $nombre_norm})
                    SET a.nombre = $nombre
                    WITH a
                    MATCH (r:Referencia {id: $ref_id})
                    MERGE (r)-[e:ESCRITO_POR]->(a)
                    SET e.orden = $orden
                    """
                    session.run(
                        query_autor,
                        nombre_norm=nombre_norm,
                        nombre=nombre,
                        orden=posicion,
                        ref_id=ref.referencia_id,
                    )

                cargadas += 1

        logger.info("referencias_cargadas", total=cargadas, doc_id=documento_id)
        return cargadas

    def cargar_citas(
        self,
        documento_id: str,
        citas: list[CitaEnTexto],
    ) -> int:
        """
        Carga citas al grafo y las vincula al documento.
        Crea relación TIENE_CITA.
        Retorna el número de citas cargadas.
        """
        cargadas = 0
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            for cita in citas:
                query_cita = """
                MERGE (c:Cita {id: $cita_id})
                SET c.texto = $texto,
                    c.texto_original = $texto_original,
                    c.tipo = $tipo,
                    c.pagina = $pagina,
                    c.fragmento = $fragmento
                WITH c
                MATCH (d:Documento {id: $doc_id})
                MERGE (d)-[:TIENE_CITA]->(c)
                """
                session.run(
                    query_cita,
                    cita_id=cita.cita_id,
                    texto=cita.texto_cita,
                    texto_original=cita.texto_original or cita.texto_cita,
                    tipo=cita.tipo.value,
                    pagina=cita.pagina,
                    fragmento=cita.fragmento_oracion,
                    doc_id=documento_id,
                )
                cargadas += 1

        logger.info("citas_cargadas", total=cargadas, doc_id=documento_id)
        return cargadas

    def cargar_referencias_externas(
        self, referencia_id: str, referencias: list[dict]
    ) -> int:
        """
        Carga la bibliografía DEL PAPER CITADO como nodos ReferenciaExterna.

        Es lo que permite responder "¿de quién es realmente esta afirmación?"
        cuando el pasaje que la respalda es la fuente citando a otro. Llega de
        Crossref como metadato, sin descargar ningún PDF.

        La identidad del nodo es el DOI si lo hay; si no, autor+año+título, que
        es lo único estable que deposita la editorial.
        """
        if not referencias:
            return 0

        query = """
        MATCH (r:Referencia {id: $ref_id})
        MERGE (x:ReferenciaExterna {id: $id})
        SET x.doi = $doi, x.autor = $autor, x.anio = $anio, x.titulo = $titulo
        MERGE (r)-[c:CITA_EXTERNA]->(x)
        SET c.orden = $orden, c.clave = $clave
        """
        cargadas = 0
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            for ref in referencias:
                doi = (ref.get("doi") or "").strip().lower()
                if doi:
                    identidad = f"doi:{doi}"
                else:
                    firma = f"{ref.get('autor','')}|{ref.get('anio','')}|{ref.get('titulo','')}"
                    if not firma.strip("|"):
                        continue          # entrada vacía: la editorial no depositó nada
                    identidad = "txt:" + unidecode(firma.lower())[:120]
                session.run(
                    query,
                    ref_id=referencia_id,
                    id=identidad,
                    doi=doi,
                    autor=ref.get("autor") or "",
                    anio=ref.get("anio"),
                    titulo=ref.get("titulo") or ref.get("cruda") or "",
                    orden=ref.get("orden"),
                    clave=ref.get("clave") or "",
                )
                cargadas += 1

        logger.debug("referencias_externas_cargadas", ref_id=referencia_id, total=cargadas)
        return cargadas

    def vincular_citas(self, documento_id: str) -> dict:
        """
        Vincula cada Cita del documento con su Referencia creando
        (Cita)-[:CITA_A]->(Referencia).

        El emparejamiento usa los apellidos y el año parseados del texto
        canónico de la cita (ver `emparejar_cita`). Cuando varias referencias
        empatan en la mejor puntuación, la relación se marca `ambigua` y
        guarda las `alternativas`: la cita se sigue auditando contra la más
        probable, pero la revisión humana puede ver que había más de una
        candidata. Antes se elegía la primera del índice sin dejar rastro.

        Retorna {"vinculadas", "ambiguas", "no_vinculadas", "total"}.
        """
        q_citas = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
        WHERE NOT (c)-[:CITA_A]->()
        RETURN c.id AS cita_id, c.texto AS texto_cita
        """
        q_refs = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
        OPTIONAL MATCH (r)-[e:ESCRITO_POR]->(a:Autor)
        WITH r, a, e ORDER BY e.orden
        RETURN r.id AS ref_id, r.anio AS anio, r.titulo AS titulo,
               collect(a.nombre) AS autores
        """
        q_crear = """
        MATCH (c:Cita {id: $cita_id})
        MATCH (r:Referencia {id: $ref_id})
        MERGE (c)-[rel:CITA_A]->(r)
        SET rel.confianza = $confianza,
            rel.metodo = $metodo,
            rel.ambigua = $ambigua,
            rel.alternativas = $alternativas
        """

        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            citas_raw = list(session.run(q_citas, doc_id=documento_id))
            refs_raw  = list(session.run(q_refs,  doc_id=documento_id))

        if not citas_raw:
            logger.info("vinculacion_sin_citas_pendientes", doc_id=documento_id)
            return {"vinculadas": 0, "ambiguas": 0, "no_vinculadas": 0, "total": 0}

        if not refs_raw:
            logger.warning("vinculacion_sin_referencias", doc_id=documento_id)
            return {
                "vinculadas": 0, "ambiguas": 0,
                "no_vinculadas": len(citas_raw), "total": len(citas_raw),
            }

        referencias = [
            {
                "ref_id": r["ref_id"],
                "anio": r["anio"],
                "titulo": r["titulo"] or "",
                "autores": r["autores"] or [],
            }
            for r in refs_raw
        ]

        vinculadas = ambiguas = no_vinculadas = 0
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            for registro in citas_raw:
                texto = registro["texto_cita"] or ""
                ref_id, confianza, metodo, alternativas = emparejar_cita(texto, referencias)

                if not ref_id:
                    no_vinculadas += 1
                    logger.debug("cita_no_vinculada", cita=texto, motivo=metodo)
                    continue

                session.run(
                    q_crear,
                    cita_id=registro["cita_id"],
                    ref_id=ref_id,
                    confianza=confianza,
                    metodo=metodo,
                    ambigua=bool(alternativas),
                    alternativas=alternativas,
                )
                vinculadas += 1
                if alternativas:
                    ambiguas += 1
                    logger.debug(
                        "cita_vinculada_ambigua",
                        cita=texto, elegida=ref_id, alternativas=len(alternativas),
                    )

        total = vinculadas + no_vinculadas
        logger.info(
            "vinculacion_completada",
            doc_id=documento_id,
            vinculadas=vinculadas,
            ambiguas=ambiguas,
            no_vinculadas=no_vinculadas,
            tasa=f"{vinculadas/total:.0%}" if total else "0%",
        )
        return {
            "vinculadas": vinculadas,
            "ambiguas": ambiguas,
            "no_vinculadas": no_vinculadas,
            "total": total,
        }

    # ── CRUD de revisión humana ──────────────────────────────────────────────

    def _leer_cita(self, session, cita_id: str) -> CitaEnTexto | None:
        q = """
        MATCH (c:Cita {id: $cita_id})
        OPTIONAL MATCH (c)-[:CITA_A]->(r:Referencia)
        RETURN c, r.id AS referencia_id
        """
        rec = session.run(q, cita_id=cita_id).single()
        if not rec:
            return None
        c = rec["c"]
        tipo = TipoCita.PARENTETICA if c.get("tipo") == "parentetica" else TipoCita.NARRATIVA
        return CitaEnTexto(
            cita_id=c["id"],
            texto_cita=c.get("texto", ""),
            texto_original=c.get("texto_original", "") or c.get("texto", ""),
            tipo=tipo,
            pagina=c.get("pagina", 0),
            fragmento_oracion=c.get("fragmento", ""),
            referencia_id=rec["referencia_id"],
        )

    def actualizar_cita(self, cita_id: str, campos: dict) -> CitaEnTexto | None:
        """Actualiza solo los campos presentes. `campos` usa nombres del request."""
        mapa = {
            "texto_cita": "texto",
            "tipo": "tipo",
            "pagina": "pagina",
            "fragmento_oracion": "fragmento",
        }
        # referencia_id se gestiona aparte (relación CITA_A, no propiedad).
        revincular = "referencia_id" in campos
        referencia_id = campos.get("referencia_id")
        sets = {mapa[k]: v for k, v in campos.items() if k in mapa}
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            if sets:
                set_clause = ", ".join(f"c.{prop} = ${prop}" for prop in sets)
                session.run(
                    f"MATCH (c:Cita {{id: $cita_id}}) SET {set_clause}",
                    cita_id=cita_id,
                    **sets,
                )
            if revincular:
                # Eliminar el vínculo previo y, si se indicó una referencia, recrearlo.
                session.run(
                    "MATCH (c:Cita {id: $cita_id})-[rel:CITA_A]->() DELETE rel",
                    cita_id=cita_id,
                )
                if referencia_id:
                    session.run(
                        """
                        MATCH (c:Cita {id: $cita_id})
                        MATCH (r:Referencia {id: $ref_id})
                        MERGE (c)-[rel:CITA_A]->(r)
                        SET rel.confianza = 1.0, rel.metodo = 'manual'
                        """,
                        cita_id=cita_id,
                        ref_id=referencia_id,
                    )
            return self._leer_cita(session, cita_id)

    def crear_cita(self, documento_id: str, cita: CitaEnTexto) -> CitaEnTexto:
        """Crea un nodo Cita y lo vincula al documento con TIENE_CITA."""
        query = """
        MATCH (d:Documento {id: $doc_id})
        CREATE (c:Cita {
            id: $cita_id,
            texto: $texto,
            texto_original: $texto_original,
            tipo: $tipo,
            pagina: $pagina,
            fragmento: $fragmento
        })
        MERGE (d)-[:TIENE_CITA]->(c)
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            session.run(
                query,
                doc_id=documento_id,
                cita_id=cita.cita_id,
                texto=cita.texto_cita,
                texto_original=cita.texto_original or cita.texto_cita,
                tipo=cita.tipo.value,
                pagina=cita.pagina,
                fragmento=cita.fragmento_oracion,
            )
            return self._leer_cita(session, cita.cita_id)

    def eliminar_cita(self, cita_id: str) -> None:
        """Elimina la Cita y todas sus relaciones (TIENE_CITA, CITA_A)."""
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            session.run("MATCH (c:Cita {id: $cita_id}) DETACH DELETE c", cita_id=cita_id)
        logger.info("cita_eliminada", cita_id=cita_id)

    def eliminar_citas(self, documento_id: str, cita_ids: list[str]) -> int:
        """
        Elimina varias citas en una sola operación. Acotado al documento:
        ids que no pertenezcan a él se ignoran. Retorna cuántas se eliminaron.
        """
        if not cita_ids:
            return 0
        query = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
        WHERE c.id IN $cita_ids
        WITH collect(c) AS nodos
        FOREACH (n IN nodos | DETACH DELETE n)
        RETURN size(nodos) AS eliminadas
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            rec = session.run(query, doc_id=documento_id, cita_ids=cita_ids).single()
            eliminadas = rec["eliminadas"] if rec else 0
        logger.info(
            "citas_eliminadas_lote",
            doc_id=documento_id,
            solicitadas=len(cita_ids),
            eliminadas=eliminadas,
        )
        return eliminadas

    def _leer_referencia(self, session, referencia_id: str) -> ReferenciaAPA | None:
        q = """
        MATCH (r:Referencia {id: $ref_id})
        OPTIONAL MATCH (r)-[e:ESCRITO_POR]->(a:Autor)
        WITH r, a, e ORDER BY e.orden
        RETURN r, collect(a.nombre) AS autores
        """
        rec = session.run(q, ref_id=referencia_id).single()
        if not rec:
            return None
        r = rec["r"]
        return ReferenciaAPA(
            referencia_id=r["id"],
            autores=[a for a in rec["autores"] if a],
            anio=r.get("anio"),
            titulo=r.get("titulo", ""),
            fuente=r.get("fuente"),
            doi=r.get("doi_verificado") or r.get("doi"),
            datos_incompletos=r.get("datos_incompletos", False),
            campos_faltantes=[],
            nivel_confianza=r.get("nivel_confianza"),
        )

    def _set_autores(self, session, referencia_id: str, autores: list[str]) -> None:
        """Reemplaza los Autor de una referencia: borra rels previas y recrea."""
        session.run(
            "MATCH (r:Referencia {id: $ref_id})-[rel:ESCRITO_POR]->(:Autor) DELETE rel",
            ref_id=referencia_id,
        )
        for posicion, nombre in enumerate(autores):
            session.run(
                """
                MERGE (a:Autor {nombre_normalizado: $nombre_norm})
                SET a.nombre = $nombre
                WITH a
                MATCH (r:Referencia {id: $ref_id})
                MERGE (r)-[e:ESCRITO_POR]->(a)
                SET e.orden = $orden
                """,
                nombre_norm=_normalizar_nombre(nombre),
                nombre=nombre,
                orden=posicion,
                ref_id=referencia_id,
            )

    def actualizar_referencia(self, referencia_id: str, campos: dict) -> ReferenciaAPA | None:
        """Actualiza campos escalares; si `autores` está presente, recrea los nodos Autor."""
        autores = campos.pop("autores", None)
        escalares = {k: v for k, v in campos.items()
                     if k in ("anio", "titulo", "fuente", "doi", "datos_incompletos", "campos_faltantes")}
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            if escalares:
                set_clause = ", ".join(f"r.{prop} = ${prop}" for prop in escalares)
                session.run(
                    f"MATCH (r:Referencia {{id: $ref_id}}) SET {set_clause}",
                    ref_id=referencia_id,
                    **escalares,
                )
            if autores is not None:
                self._set_autores(session, referencia_id, autores)
            return self._leer_referencia(session, referencia_id)

    def crear_referencia(self, documento_id: str, ref: ReferenciaAPA) -> ReferenciaAPA:
        """Crea un nodo Referencia con sus Autores y lo vincula al documento."""
        query = """
        MATCH (d:Documento {id: $doc_id})
        CREATE (r:Referencia {
            id: $ref_id,
            titulo: $titulo,
            anio: $anio,
            fuente: $fuente,
            doi: $doi,
            datos_incompletos: $incompleto
        })
        MERGE (d)-[:TIENE_REFERENCIA]->(r)
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            session.run(
                query,
                doc_id=documento_id,
                ref_id=ref.referencia_id,
                titulo=ref.titulo,
                anio=ref.anio,
                fuente=ref.fuente or "",
                doi=ref.doi or "",
                incompleto=ref.datos_incompletos,
            )
            self._set_autores(session, ref.referencia_id, ref.autores)
            return self._leer_referencia(session, ref.referencia_id)

    def eliminar_referencia(self, referencia_id: str) -> None:
        """
        Elimina la Referencia (y sus rels TIENE_REFERENCIA / CITA_A) y luego los
        nodos Autor que queden huérfanos (sin otras referencias).
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            autores = [
                rec["nn"]
                for rec in session.run(
                    "MATCH (r:Referencia {id: $ref_id})-[:ESCRITO_POR]->(a:Autor) "
                    "RETURN a.nombre_normalizado AS nn",
                    ref_id=referencia_id,
                )
            ]
            session.run("MATCH (r:Referencia {id: $ref_id}) DETACH DELETE r", ref_id=referencia_id)
            for nn in autores:
                session.run(
                    "MATCH (a:Autor {nombre_normalizado: $nn}) "
                    "WHERE NOT (a)<-[:ESCRITO_POR]-() DETACH DELETE a",
                    nn=nn,
                )
        logger.info("referencia_eliminada", ref_id=referencia_id)

    _Q_VECINOS_DEL_DOCUMENTO = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(origen:Referencia)
    CALL (d, origen) {
        MATCH (origen)-[:ESCRITO_POR]->(:Autor)<-[:ESCRITO_POR]-(v:Referencia)<-[:TIENE_REFERENCIA]-(d)
        WHERE v.id <> origen.id
        RETURN v AS vecina
      UNION
        MATCH (origen)-[:CITA_EXTERNA]->(x:ReferenciaExterna)
        MATCH (d)-[:TIENE_REFERENCIA]->(v:Referencia)
        WHERE coalesce(x.doi,'') <> ''
          AND toLower(coalesce(v.doi_verificado, v.doi, '')) = toLower(x.doi)
          AND v.id <> origen.id
        RETURN v AS vecina
    }
    WITH origen, vecina
    WHERE coalesce(vecina.doi_verificado, '') <> ''
    RETURN origen.id AS ref_id, collect(DISTINCT vecina.doi_verificado) AS dois_vecinos
    """

    def _contar_vecinos_utilizables(self, documento_id: str) -> int:
        """
        Referencias con al menos un vecino cuyo paper está realmente indexado.

        Un vecino sin texto no sirve para nada, así que se comprueba contra el
        índice. El número crece con el uso: el índice está compartido entre
        documentos, de modo que un paper descargado para otra tesis deja de
        hacer falta descargarlo aquí.
        """
        from app.services.vectorstore.supabase_service import supabase_vector_service

        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                filas = [dict(r) for r in session.run(
                    self._Q_VECINOS_DEL_DOCUMENTO, doc_id=documento_id
                )]
        except Exception as e:
            logger.warning("error_contar_vecinos", doc_id=documento_id, error=str(e))
            return 0

        indexado: dict[str, bool] = {}
        con_vecino = 0
        for fila in filas:
            for doi in fila["dois_vecinos"]:
                clave = (doi or "").replace("/", "_").replace(".", "_")
                if clave not in indexado:
                    indexado[clave] = supabase_vector_service.paper_ya_indexado(clave)
                if indexado[clave]:
                    con_vecino += 1
                    break
        return con_vecino

    def obtener_resumen_grafo(self, documento_id: str) -> ResumenGrafo:
        conteo = neo4j_service.contar_nodos_y_relaciones(documento_id)

        total_nodos      = conteo["referencias"] + conteo["citas"] + conteo["autores"]
        total_relaciones = conteo["relaciones"]
        densidad         = round(total_relaciones / max(total_nodos, 1), 2)

        # La robustez se mide por la COBERTURA DE VINCULACIÓN, no por densidad.
        # Un grafo bibliográfico tiene ~1.2 relaciones por nodo por su propia
        # forma, así que el antiguo umbral de 3.0 era inalcanzable y la
        # advertencia saltaba siempre. Lo que de verdad determina si se puede
        # auditar es qué proporción de citas encontró su referencia: sin
        # vínculo no hay paper contra el que contrastar.
        citas          = conteo["citas"]
        vinculadas     = conteo.get("citas_vinculadas", 0)
        tasa_vinculo   = vinculadas / citas if citas else 0.0
        grafo_robusto  = citas > 0 and tasa_vinculo >= _MIN_TASA_VINCULACION

        advertencia = None
        if citas == 0:
            advertencia = "El documento no tiene citas detectadas: no hay nada que auditar."
        elif not grafo_robusto:
            advertencia = (
                f"Solo {vinculadas} de {citas} citas ({tasa_vinculo:.0%}) encontraron su "
                f"referencia en la bibliografía. Las demás no se pueden auditar: revisa "
                f"si faltan entradas en la lista de referencias o si los años no coinciden."
            )

        return ResumenGrafo(
            documento_id=documento_id,
            total_nodos=total_nodos,
            nodos_autores=conteo["autores"],
            nodos_referencias=conteo["referencias"],
            nodos_citas=conteo["citas"],
            total_relaciones=total_relaciones,
            densidad_promedio=densidad,
            grafo_robusto=grafo_robusto,
            citas_vinculadas=conteo.get("citas_vinculadas", 0),
            vecinos_utilizables=self._contar_vecinos_utilizables(documento_id),
            papers_en_corpus=self._papers_en_corpus(),
            advertencia_densidad=advertencia,
        )

    @staticmethod
    def _papers_en_corpus() -> int:
        """Papers distintos indexados en total. Es el corpus compartido entre
        documentos: cuanto más crece, menos hay que descargar por tesis."""
        from app.services.vectorstore.supabase_service import supabase_vector_service
        try:
            return supabase_vector_service.total_papers_distintos()
        except Exception:
            return 0


# Singleton
grafo_carga_service = GrafoCargaService()