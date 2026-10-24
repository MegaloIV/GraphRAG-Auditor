"""
Fuentes abiertas de texto completo que Unpaywall no cubre.

Medido sobre una tesis real, la vía Unpaywall conseguía 1 PDF de 27: las
editoriales de acceso abierto bloquean la descarga automática (MDPI responde
403 por Cloudflare), los preprints de congresos no están en Unpaywall, y las
tesis de repositorios institucionales enlazan a una ficha, no al PDF. Cada
función de este módulo cubre uno de esos huecos con una vía legítima y
abierta; con ellas la misma tesis pasó a 11 de 38.

Todas devuelven `Hallazgo` (texto, url de donde salió, fuente) o None, y
ninguna lanza: una fuente caída no debe tumbar la verificación.
"""
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
import structlog
from lxml import etree

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_UA_NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_MIN_CHARS = 2000
# pymupdf4llm dispara la memoria con documentos largos (libros, tesis de
# cientos de páginas): por encima de este número de páginas se usa la
# extracción plana de PyMuPDF; por encima del máximo se descarta.
_PAGINAS_MARKDOWN = 80
_PAGINAS_MAX = 600

EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
CORE = "https://api.core.ac.uk/v3"
DATACITE = "https://api.datacite.org/dois"

# Abreviatura del DOI de MDPI → nombre de la revista en su host de archivos.
_SLUG_MDPI = {
    "sym": "symmetry", "math": "mathematics", "app": "applsci", "s": "sensors",
    "en": "energies", "iot": "iot", "bioengineering": "bioengineering",
    "electronics": "electronics", "info": "information", "jrfm": "jrfm",
    "make": "make", "bdcc": "bdcc", "fi": "futureinternet", "ai": "ai",
    "computers": "computers", "algorithms": "algorithms", "data": "data",
    "risks": "risks", "systems": "systems", "cancers": "cancers",
    "diagnostics": "diagnostics", "jcm": "jcm", "healthcare": "healthcare",
    "ijerph": "ijerph", "life": "life", "jimaging": "jimaging",
    "technologies": "technologies", "education": "education",
    "su": "sustainability", "agronomy": "agronomy", "agriculture": "agriculture",
    "foods": "foods", "remotesensing": "remotesensing", "rs": "remotesensing",
    "ijms": "ijms", "nu": "nutrients", "medicina": "medicina",
    "biomedicines": "biomedicines", "brainsci": "brainsci", "vehicles": "vehicles",
    "drones": "drones", "robotics": "robotics", "asi": "asi", "mti": "mti",
    "digital": "digital", "encyclopedia": "encyclopedia", "publications": "publications",
    "socsci": "socsci", "admsci": "admsci", "economies": "economies",
    "jsan": "jsan", "network": "network", "telecom": "telecom", "cryptography": "cryptography",
    "forecasting": "forecasting", "modelling": "modelling", "sci": "sci",
}


@dataclass
class Hallazgo:
    texto: str
    url: str
    fuente: str                 # "mdpi-res" | "europepmc" | "arxiv" | "landing" | "repositorio" | "pdf_web"
    pdf: Optional[bytes] = None


# ── Utilidades ──────────────────────────────────────────────────────────────

def _cliente() -> httpx.Client:
    """Cliente con cookies y cabeceras de navegador: varias revistas abiertas
    (TUBITAK, OJS con Cloudflare) solo sirven el PDF a una sesión que ya
    visitó la ficha del artículo."""
    return httpx.Client(
        timeout=40.0, follow_redirects=True, verify=False,
        headers={
            "User-Agent": _UA_NAVEGADOR,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        },
    )


def normalizar_palabras(texto: str) -> set[str]:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return {p for p in re.sub(r"[^a-z0-9]+", " ", t).split() if len(p) > 2}


def _similitud(a: str, b: str) -> float:
    pa, pb = normalizar_palabras(a), normalizar_palabras(b)
    if not pa or not pb:
        return 0.0
    return len(pa & pb) / len(pa | pb)


def pdf_a_texto(datos: bytes) -> Optional[str]:
    """Texto de un PDF, protegiendo la memoria con documentos muy largos."""
    if not datos or not datos.startswith(b"%PDF"):
        return None
    import fitz
    import pymupdf4llm

    ruta = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(datos)
            ruta = Path(tmp.name)
        with fitz.open(ruta) as doc:
            paginas = doc.page_count
            if paginas > _PAGINAS_MAX:
                logger.info("pdf_descartado_demasiado_largo", paginas=paginas)
                return None
            texto = "\n".join(p.get_text() for p in doc) if paginas > _PAGINAS_MARKDOWN else None
        if texto is None:
            texto = pymupdf4llm.to_markdown(str(ruta))
    except Exception as e:
        logger.warning("pdf_ilegible", error=str(e)[:80])
        return None
    finally:
        if ruta is not None:
            ruta.unlink(missing_ok=True)
    return texto if texto and len(texto.strip()) >= _MIN_CHARS else None


def titulo_poco_discriminante(titulo: str) -> bool:
    """¿El título es demasiado corto o genérico para identificar la fuente?

    «Deep learning», «Random Forests» o «How We Reason» aparecen en el cuerpo de
    cualquier trabajo del área: emparejar por ellos no distingue un documento de
    otro, y hay que apoyarse en los autores y el año.
    """
    return len(normalizar_palabras(titulo)) < 4


def concordancia(titulo: str, texto: str, autores: list[str] | None = None,
                 anio: Optional[int] = None, ventana: int = 3000) -> float:
    """
    ¿El texto recuperado es la fuente citada? Proporción de palabras del
    título que aparecen en la cabecera del documento.

    La ventana es corta a propósito: en un paper el título está en los
    primeros ~1500 caracteres, y con una ventana amplia las palabras de un
    título genérico («deep learning … detection») aparecen en el cuerpo de
    cualquier paper del área. Con 8000 caracteres se aceptaba un paper
    equivocado (DOI mal transcrito por el tesista); con 3000, no. Las páginas
    web, con menús delante del contenido, piden una ventana mayor.

    Si el título no aparece —el tesista lo tradujo al español y el paper está
    en inglés— se acepta cuando la cabecera trae los apellidos de los primeros
    autores y el año: la combinación identifica el trabajo igual de bien.
    """
    palabras = normalizar_palabras(titulo)
    cabecera = texto[:ventana] if texto else ""
    if len(palabras) < 4:
        return 1.0
    proporcion = len(palabras & normalizar_palabras(cabecera)) / len(palabras)
    if proporcion >= 0.6 or not autores:
        return proporcion
    palabras_cab = normalizar_palabras(cabecera)
    apellidos = [normalizar_palabras(a.split(",")[0]) for a in autores[:3]]
    presentes = sum(1 for ap in apellidos if ap and ap <= palabras_cab)
    anio_ok = not anio or str(anio) in cabecera
    if presentes >= min(2, len(apellidos)) and anio_ok:
        return max(proporcion, 0.6)
    return proporcion


# ── Fuentes por DOI ─────────────────────────────────────────────────────────

def desde_mdpi(doi: str) -> Optional[Hallazgo]:
    """MDPI es acceso abierto pero www.mdpi.com responde 403 (Cloudflare). Su
    host de archivos sirve el mismo PDF; la ruta se deriva del DOI:
    10.3390/sym15030568 → symmetry-15-00568."""
    m = re.match(r"10\.3390/([a-z]+)(\d+)$", (doi or "").lower())
    if not m:
        return None
    slug, num = _SLUG_MDPI.get(m.group(1)), m.group(2)
    if not slug or len(num) < 7:
        return None
    articulo, volumen = num[-4:], num[:-6]
    with _cliente() as cli:
        for vol in dict.fromkeys((volumen, volumen.zfill(2))):
            base = f"{slug}-{vol}-{articulo.zfill(5)}"
            url = f"https://mdpi-res.com/d_attachment/{slug}/{base}/article_deploy/{base}.pdf"
            try:
                r = cli.get(url)
            except Exception:
                continue
            texto = pdf_a_texto(r.content)
            if texto:
                return Hallazgo(texto, url, "mdpi-res", r.content)
    return None


def desde_europepmc(doi: str) -> Optional[Hallazgo]:
    """Texto completo de Europe PMC (API abierta, sin antibot). El título va
    en <front> y el cuerpo en <body>: se juntan título, resumen y cuerpo."""
    try:
        with _cliente() as cli:
            r = cli.get(f"{EUROPEPMC}/search",
                        params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"})
            res = r.json().get("resultList", {}).get("result", [])
            if not res or not res[0].get("pmcid") or res[0].get("inEPMC") != "Y":
                return None
            pmcid = res[0]["pmcid"]
            x = cli.get(f"{EUROPEPMC}/{pmcid}/fullTextXML")
        if x.status_code != 200 or len(x.content) < 5000:
            return None
        raiz = etree.fromstring(x.content)
        cuerpo = raiz.find(".//body")
        if cuerpo is None:
            return None
        partes = [" ".join(e.itertext()) for e in (raiz.find(".//article-title"), raiz.find(".//abstract"))
                  if e is not None]
        partes.append(" ".join(cuerpo.itertext()))
        texto = re.sub(r"\s+", " ", " ".join(partes)).strip()
        if len(texto) < 3000:
            return None
        return Hallazgo(texto, f"https://europepmc.org/article/PMC/{pmcid}", "europepmc")
    except Exception as e:
        logger.debug("europepmc_fallo", doi=doi, error=str(e)[:80])
        return None


def _core_corresponde(obra: dict, titulo: str, autores: list[str] | None,
                      anio: Optional[int]) -> bool:
    """¿El trabajo que devuelve CORE es el que se pidió?

    Comparar por parecido de títulos no basta, y el precio del error es alto: con un
    umbral del 60 % se aceptaba «Ireland's facts & figures 2018» pidiendo «Cancer
    facts & figures 2018», y «Detecting credit card fraud» pidiendo «A Comparative
    Analysis of Various Credit Card Fraud Detection Techniques». Se exige, en cambio,
    que el título pedido esté **contenido** en el encontrado, y que además coincida un
    apellido o el año, porque los títulos cortos se parecen entre sí por casualidad.
    """
    pedido = normalizar_palabras(titulo)
    hallado = normalizar_palabras(obra.get("title") or "")
    if not pedido or not hallado:
        return False
    faltan = pedido - hallado
    tolerancia = 1 if len(pedido) >= 8 else 0      # títulos largos: un término suelto
    if len(faltan) > tolerancia:
        return False

    suyo = obra.get("yearPublished")
    anio_ok = bool(anio and suyo and abs(int(suyo) - int(anio)) <= 1)
    apellidos = {p for a in (autores or [])[:3] for p in normalizar_palabras(a.split(",")[0])}
    suyos = normalizar_palabras(
        " ".join((x.get("name") or "") for x in (obra.get("authors") or [])))
    autor_ok = bool(apellidos & suyos)

    if not autores and not anio:
        return True                                 # no hay con qué contrastar
    if autor_ok or anio_ok:
        return True
    # Los registros de CORE llegan con la lista de autores incompleta o sin año; si el
    # título coincide palabra por palabra se acepta, si no, no.
    return not faltan and len(pedido) >= 5 and not (apellidos and suyos)


def desde_core(doi: str, titulo: str = "", autores: list[str] | None = None,
               anio: Optional[int] = None) -> Optional[Hallazgo]:
    """Texto completo desde CORE, el agregador de repositorios abiertos.

    Resuelve dos de los motivos de fallo más frecuentes: indexa la copia depositada en
    el repositorio institucional (donde acaban las tesis y muchos artículos que en la
    web del editor están tras un muro) y sirve el texto ya extraído por su API, de modo
    que no hay PDF que descargar ni robot que bloquear. Necesita clave gratuita.

    Todo candidato se contrasta antes de devolverlo: CORE responde con trabajos
    parecidos, y quedarse con el primero es indexar una fuente que no es la citada.
    """
    clave = get_settings().core_api_key
    if not clave:
        return None
    limpio = re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", titulo or "")).strip()
    if doi:
        consulta = f'doi:"{doi}"'
    elif len(limpio) >= 12:
        # Con el título entre comillas CORE devuelve el trabajo exacto; en texto plano
        # devuelve cualquier cosa que comparta palabras. Las comillas solo funcionan si
        # antes se quitan los signos, que es lo que provocaba los errores 500.
        consulta = f'title:"{limpio}"'
    else:
        return None
    try:
        with _cliente() as cli:
            r = cli.post(f"{CORE}/search/works",
                         headers={"Authorization": f"Bearer {clave}"},
                         json={"q": consulta, "limit": 5})
        if r.status_code != 200:
            logger.debug("core_respuesta", doi=doi, estado=r.status_code)
            return None
        for obra in r.json().get("results", []):
            texto = re.sub(r"\s+", " ", (obra.get("fullText") or "")).strip()
            if len(texto) < _MIN_CHARS:
                continue
            # El DOI del registro no prueba nada en CORE: buscando el de Breiman
            # («Random Forests», 2001) devuelve, con ese mismo DOI, una tesis de
            # 2024 sobre modelos de lenguaje para predicción meteorológica y otros
            # cuatro trabajos que solo tienen en común citarlo. Por eso el título
            # y los autores deciden siempre, también cuando se buscó por DOI.
            devuelto = (obra.get("doi") or "").lower().replace("https://doi.org/", "")
            if doi and devuelto and devuelto != doi.lower():
                continue
            if titulo and not _core_corresponde(obra, titulo, autores, anio):
                logger.debug("core_descartado", pedido=(titulo or "")[:55],
                             hallado=(obra.get("title") or "")[:55])
                continue
            url = obra.get("downloadUrl") or obra.get("sourceFulltextUrls") or ""
            if isinstance(url, list):
                url = url[0] if url else ""
            return Hallazgo(texto, url or f"https://core.ac.uk/search?q={doi or limpio}", "core")
    except Exception as e:
        logger.debug("core_fallo", doi=doi, error=str(e)[:80])
    return None


def desde_landing(doi: str) -> Optional[Hallazgo]:
    """Sigue el DOI hasta la ficha del artículo y busca el PDF enlazado,
    con sesión de navegador (cookies y Referer)."""
    try:
        with _cliente() as cli:
            r = cli.get(f"https://doi.org/{doi}")
            if r.content.startswith(b"%PDF"):
                texto = pdf_a_texto(r.content)
                return Hallazgo(texto, str(r.url), "landing", r.content) if texto else None
            base = str(r.url)
            enlaces = set(re.findall(r'href="([^"]+)"', r.text, re.I))
            candidatos = [c for c in enlaces
                          if re.search(r"\.pdf|/pdf\b|download|viewcontent\.cgi|fulltext|article/view", c, re.I)]
            candidatos.sort(key=lambda c: (0 if ".pdf" in c.lower() or "viewcontent" in c else 1, len(c)))
            for c in candidatos[:8]:
                url = c.replace("&amp;", "&")
                url = url if url.startswith("http") else str(httpx.URL(base).join(url))
                try:
                    rr = cli.get(url, headers={
                        "Referer": base, "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "same-origin", "Upgrade-Insecure-Requests": "1",
                    })
                except Exception:
                    continue
                if rr.content.startswith(b"%PDF") and len(rr.content) > 45000:
                    texto = pdf_a_texto(rr.content)
                    if texto:
                        return Hallazgo(texto, url, "landing", rr.content)
    except Exception as e:
        logger.debug("landing_fallo", doi=doi, error=str(e)[:80])
    return None


# ── Fuentes por título ──────────────────────────────────────────────────────

def desde_arxiv(titulo: str) -> Optional[Hallazgo]:
    """
    Preprint en arXiv buscado por título. Cubre los papers de congresos
    (CVPR, NeurIPS…) que en la editorial son de pago. La API de arXiv limita
    mucho; sus preprints tienen DOI 10.48550/arxiv.* en DataCite, que se
    consulta en su lugar, y el PDF se baja de arxiv.org.
    """
    limpio = re.sub(r"[^A-Za-z0-9 ]", " ", titulo or "").strip()
    if len(limpio.split()) < 3:
        return None
    try:
        with _cliente() as cli:
            r = cli.get(DATACITE, params={"query": f'titles.title:"{limpio}"',
                                          "prefix": "10.48550", "page[size]": 5})
            mejor, sim_mejor = None, 0.0
            for x in r.json().get("data", []):
                a = x.get("attributes", {})
                sim = _similitud(titulo, (a.get("titles") or [{}])[0].get("title") or "")
                if sim > sim_mejor:
                    mejor, sim_mejor = a.get("doi", ""), sim
            if not mejor or sim_mejor < 0.82:
                return None
            url = f"https://arxiv.org/pdf/{mejor.lower().split('arxiv.')[-1]}"
            rr = cli.get(url)
        texto = pdf_a_texto(rr.content)
        return Hallazgo(texto, url, "arxiv", rr.content) if texto else None
    except Exception as e:
        logger.debug("arxiv_fallo", titulo=titulo[:50], error=str(e)[:80])
        return None


# ── Fuentes por URL ─────────────────────────────────────────────────────────

_RE_REPOSITORIO = re.compile(r"handle|repositorio|bitstream|dspace|/items/", re.I)


def es_repositorio(url: str) -> bool:
    return bool(_RE_REPOSITORIO.search(url or ""))


def es_url_generica(url: str) -> bool:
    """La URL es solo el dominio o su portada (p. ej. «https://www.datagy.io»):
    no identifica la fuente citada."""
    return bool(re.match(r"^https?://[^/]+/?(?:(?:es|en|news|blog|noticias)/?)?$", (url or "").strip(), re.I))


_RE_CATALOGO_LIBROS = re.compile(
    r"books\.google\.|google\.[a-z.]+/books|amazon\.[a-z.]+/|goodreads\.com|"
    r"casadellibro\.com|worldcat\.org|bookdepository\.com", re.I)


def es_catalogo_de_libro(url: str) -> bool:
    """La URL es la ficha de un libro en un catálogo o tienda (Google Books,
    Amazon…): trae título, editorial y una sinopsis, no el texto del libro.
    Indexarla haría que el juez «verificara» citas contra una contraportada."""
    return bool(_RE_CATALOGO_LIBROS.search(url or ""))


def desde_repositorio(url: str) -> Optional[Hallazgo]:
    """
    Tesis y documentos en DSpace: la URL de la bibliografía (hdl.handle.net o
    /handle/...) lleva a la ficha. El PDF está en un «bitstream», enlazado en
    la ficha (DSpace 6) o solo accesible por la API REST (DSpace 7).
    """
    try:
        with _cliente() as cli:
            r = cli.get(url)
            if r.content.startswith(b"%PDF"):
                texto = pdf_a_texto(r.content)
                return Hallazgo(texto, str(r.url), "repositorio", r.content) if texto else None
            base, html = str(r.url), r.text
            for c in re.findall(r'href="([^"]*/bitstream/[^"]+?\.pdf[^"]*)"', html, re.I):
                u = c if c.startswith("http") else str(httpx.URL(base).join(c))
                try:
                    rr = cli.get(u)
                except Exception:
                    continue
                texto = pdf_a_texto(rr.content)
                if texto:
                    return Hallazgo(texto, u, "repositorio", rr.content)
            raiz = re.match(r"https?://[^/]+", base).group(0)
            # DSpace 7 redirige la ficha a /items/<uuid>; si no, se resuelve el handle.
            m_item = re.search(r"/items/([0-9a-f-]{36})", base)
            if m_item:
                uuid = m_item.group(1)
            else:
                m = (re.search(r"/handle/(\d+(?:\.\d+)*/\d+)", base)
                     or re.search(r"(?:hdl\.handle\.net|/handle)/(\d+(?:\.\d+)*/\d+)", url))
                if not m:
                    return None
                uuid = cli.get(f"{raiz}/server/api/pid/find", params={"id": f"hdl:{m.group(1)}"}).json()["uuid"]
            bundles = cli.get(f"{raiz}/server/api/core/items/{uuid}/bundles",
                              params={"embed": "bitstreams"}).json()
            for b in bundles.get("_embedded", {}).get("bundles", []):
                if b.get("name") != "ORIGINAL":
                    continue
                for bs in b.get("_embedded", {}).get("bitstreams", {}).get("_embedded", {}).get("bitstreams", []):
                    u = bs["_links"]["content"]["href"]
                    rr = cli.get(u)
                    texto = pdf_a_texto(rr.content)
                    if texto:
                        return Hallazgo(texto, u, "repositorio", rr.content)
    except Exception as e:
        logger.debug("repositorio_fallo", url=url[:70], error=str(e)[:80])
    return None


def desde_url_pdf(url: str) -> Optional[Hallazgo]:
    """URL de la bibliografía que apunta directamente a un PDF."""
    try:
        with _cliente() as cli:
            r = cli.get(url)
        texto = pdf_a_texto(r.content)
        return Hallazgo(texto, url, "pdf_web", r.content) if texto else None
    except Exception:
        return None


# ── URL desde el texto de la bibliografía ───────────────────────────────────

_RE_URL = re.compile(r"https?://\S+")
_CONTINUA_URL = re.compile(r"^[A-Za-z0-9\-._~%/?=&#+:]+$")
_RE_ENLACE_MD = re.compile(r"\[([^\]\n]*)\]\((?:https?://|www\.)[^)\s]*\)")


def url_desde_bibliografia(texto_tesis: str, titulo: str) -> Optional[str]:
    """
    URL que acompaña a una referencia en la bibliografía de la tesis.

    La extracción de referencias (un LLM) no siempre la devuelve, y en el PDF
    suele venir partida por el salto de línea («.../survival- rates.html»).
    Se localiza la entrada por su título y se recompone la URL. Los enlaces de
    Zotero que envuelven la entrada se descartan: no son la fuente.
    """
    if not texto_tesis or not titulo:
        return None
    plano = re.sub(r"\s+", " ", _RE_ENLACE_MD.sub(r"\1", texto_tesis))
    palabras = titulo.split()[:4]
    if len(palabras) < 3:
        return None
    patron = r"\W*".join(re.escape(w) for w in palabras)
    posiciones = [m.start() for m in re.finditer(patron, plano, re.I)]
    if not posiciones:
        return None
    ventana = plano[posiciones[-1]:posiciones[-1] + 600]   # la bibliografía va al final
    m = _RE_URL.search(ventana)
    if not m:
        return None
    # Que la URL sea de esta entrada: no debe empezar otra referencia antes.
    if re.search(r"\(\d{4}[a-z]?(?:, [A-Za-z]+ \d{1,2})?\)\.", ventana[40:m.start()]):
        return None
    url, resto = m.group(0), ventana[m.end():]
    while url.endswith(("-", "/", "_", ".")) and resto.startswith(" "):
        partes = resto[1:].split(" ", 1)
        if not partes or not _CONTINUA_URL.match(partes[0]) or re.match(r"^[A-Z][a-z]+,?$", partes[0]):
            break
        url += partes[0]
        resto = " " + partes[1] if len(partes) > 1 else ""
    url = url.rstrip(".,;)")
    if "zotero.org" in url:
        return None
    return url
