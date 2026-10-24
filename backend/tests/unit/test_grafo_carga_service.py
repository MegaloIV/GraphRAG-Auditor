"""
GrafoCargaService — vinculación automática cita → referencia.

Es la regla que decide qué cita pertenece a qué fuente, y de ella depende que la
auditoría busque evidencia en el paper correcto. Neo4j está mockeado: no se abre
ninguna conexión. Los apellidos y años son los de las referencias reales de la
tesis.
"""
from contextlib import contextmanager
from unittest.mock import patch

from app.services.grafo.grafo_carga_service import (
    GrafoCargaService,
    emparejar_cita,
    _clave_apellido,
    _normalizar_nombre,
)

_Q_CITAS = "TIENE_CITA"
_Q_REFS = "TIENE_REFERENCIA"


class _SesionFalsa:
    """Sustituye a la sesión de Neo4j: responde según la consulta y anota los MERGE."""

    def __init__(self, citas, referencias):
        self._citas = citas
        self._referencias = referencias
        self.vinculos = []  # (cita_id, ref_id, confianza, metodo, ambigua, alternativas)

    def run(self, query, **kwargs):
        if _Q_CITAS in query and "CITA_A" in query and "MERGE" not in query:
            return list(self._citas)
        if _Q_REFS in query:
            return list(self._referencias)
        if "MERGE" in query:
            self.vinculos.append((
                kwargs["cita_id"], kwargs["ref_id"], kwargs["confianza"],
                kwargs["metodo"], kwargs["ambigua"], kwargs["alternativas"],
            ))
            return []
        return []


def _driver_falso(sesion):
    @contextmanager
    def _session(**_kwargs):
        yield sesion

    class _Driver:
        session = staticmethod(_session)

    class _Neo4j:
        driver = _Driver()

    return _Neo4j()


def _vincular(citas, referencias):
    sesion = _SesionFalsa(citas, referencias)
    with patch(
        "app.services.grafo.grafo_carga_service.neo4j_service", _driver_falso(sesion)
    ):
        resumen = GrafoCargaService().vincular_citas("doc-1")
    return resumen, sesion


class TestNormalizadores:

    def test_el_nombre_pierde_tildes_y_mayusculas(self):
        assert _normalizar_nombre("García, J.") == "garcia, j."

    def test_el_apellido_se_compara_sin_tildes(self):
        assert _clave_apellido("Arévalo") == _clave_apellido("arevalo")

    def test_los_espacios_sobrantes_no_afectan(self):
        assert _clave_apellido("  Edge  ") == "edge"

    def test_se_queda_con_el_apellido_y_descarta_las_iniciales(self):
        assert _clave_apellido("Hernández Sampieri, R.") == "hernandez sampieri"

    def test_el_guion_equivale_a_un_espacio(self):
        # El markdown pierde el guion de un apellido partido entre renglones.
        assert _clave_apellido("Johnson-Laird, P. N.") == _clave_apellido("Johnson Laird")


class TestVincularCitas:

    def test_apellido_y_anio_exactos_vinculan_con_confianza_alta(self):
        resumen, sesion = _vincular(
            citas=[{"cita_id": "c1", "texto_cita": "(Edge et al., 2024)"}],
            referencias=[{"ref_id": "r1", "anio": 2024, "autores": ["Edge, D."]}],
        )
        assert resumen == {"vinculadas": 1, "ambiguas": 0, "no_vinculadas": 0, "total": 1}
        cita_id, ref_id, confianza, metodo = sesion.vinculos[0][:4]
        assert (cita_id, ref_id) == ("c1", "r1")
        assert confianza == 0.98
        assert metodo == "primer_apellido_anio"

    def test_una_cita_de_dos_autores_vincula_por_el_primero(self):
        resumen, sesion = _vincular(
            citas=[{"cita_id": "c1", "texto_cita": "(Klesel y Wittmann, 2025)"}],
            referencias=[{"ref_id": "r1", "anio": 2025, "autores": ["Klesel, M.", "Wittmann, H."]}],
        )
        assert resumen["vinculadas"] == 1
        assert sesion.vinculos[0][2] == 0.98

    def test_un_apellido_con_una_letra_de_menos_vincula_con_confianza_menor(self):
        resumen, sesion = _vincular(
            citas=[{"cita_id": "c1", "texto_cita": "(Wittman, 2025)"}],
            referencias=[{"ref_id": "r1", "anio": 2025, "autores": ["Wittmann, H."]}],
        )
        assert resumen["vinculadas"] == 1
        assert sesion.vinculos[0][2] == 0.70
        assert sesion.vinculos[0][3] == "apellido_aproximado_anio"

    def test_un_apellido_compuesto_vincula_aunque_la_cita_lo_abrevie(self):
        resumen, sesion = _vincular(
            citas=[{"cita_id": "c1", "texto_cita": "(Sampieri, 2018)"}],
            referencias=[{"ref_id": "r1", "anio": 2018, "autores": ["Hernández Sampieri, R."]}],
        )
        assert resumen["vinculadas"] == 1

    def test_no_vincula_solo_por_coincidir_el_anio(self):
        # Regresión: enlazar por año sin apellido producía falsos positivos.
        resumen, _ = _vincular(
            citas=[{"cita_id": "c1", "texto_cita": "(Zhao et al., 2024)"}],
            referencias=[{"ref_id": "r1", "anio": 2024, "autores": ["Edge, D."]}],
        )
        assert resumen == {"vinculadas": 0, "ambiguas": 0, "no_vinculadas": 1, "total": 1}

    def test_un_apellido_no_vincula_con_otro_que_lo_contiene(self):
        # Regresión: comparar por prefijo enlazaba (Yusuf et al., 2025) con la
        # referencia de Yu, S., y la auditoría verificaba contra el paper ajeno.
        resumen, _ = _vincular(
            citas=[{"cita_id": "c1", "texto_cita": "(Yusuf et al., 2025)"}],
            referencias=[{"ref_id": "r1", "anio": 2025, "autores": ["Kim, J.", "Yu, S."]}],
        )
        assert resumen["vinculadas"] == 0

    def test_dos_referencias_igual_de_probables_quedan_marcadas_ambiguas(self):
        # Dos papers del mismo autor y año: APA pide "2017a"/"2017b". Se elige
        # uno para poder auditar, pero la relación queda marcada.
        resumen, sesion = _vincular(
            citas=[{"cita_id": "c1", "texto_cita": "(Esteva et al., 2017)"}],
            referencias=[
                {"ref_id": "r1", "anio": 2017, "autores": ["Esteva, A."]},
                {"ref_id": "r2", "anio": 2017, "autores": ["Esteva, A."]},
            ],
        )
        assert resumen == {"vinculadas": 1, "ambiguas": 1, "no_vinculadas": 0, "total": 1}
        assert sesion.vinculos[0][4] is True            # ambigua
        assert sesion.vinculos[0][5] == ["r2"]          # alternativas

    def test_no_vincula_si_el_anio_no_coincide(self):
        resumen, _ = _vincular(
            citas=[{"cita_id": "c1", "texto_cita": "(Edge et al., 2020)"}],
            referencias=[{"ref_id": "r1", "anio": 2024, "autores": ["Edge, D."]}],
        )
        assert resumen["no_vinculadas"] == 1

    def test_una_cita_sin_anio_no_se_vincula(self):
        resumen, _ = _vincular(
            citas=[{"cita_id": "c1", "texto_cita": "(Edge et al.)"}],
            referencias=[{"ref_id": "r1", "anio": 2024, "autores": ["Edge, D."]}],
        )
        assert resumen["no_vinculadas"] == 1

    def test_sin_referencias_ninguna_cita_se_vincula(self):
        resumen, _ = _vincular(
            citas=[{"cita_id": "c1", "texto_cita": "(Edge et al., 2024)"}],
            referencias=[],
        )
        assert resumen == {"vinculadas": 0, "ambiguas": 0, "no_vinculadas": 1, "total": 1}

    def test_sin_citas_pendientes_no_hace_nada(self):
        resumen, sesion = _vincular(citas=[], referencias=[{"ref_id": "r1", "anio": 2024, "autores": ["Edge, D."]}])
        assert resumen == {"vinculadas": 0, "ambiguas": 0, "no_vinculadas": 0, "total": 0}
        assert sesion.vinculos == []

    def test_varias_citas_se_reparten_entre_sus_referencias(self):
        resumen, sesion = _vincular(
            citas=[
                {"cita_id": "c1", "texto_cita": "(Edge et al., 2024)"},
                {"cita_id": "c2", "texto_cita": "(Fan et al., 2024)"},
                {"cita_id": "c3", "texto_cita": "(Ansari, 2026)"},
            ],
            referencias=[
                {"ref_id": "r1", "anio": 2024, "autores": ["Edge, D."]},
                {"ref_id": "r2", "anio": 2024, "autores": ["Fan, W."]},
                {"ref_id": "r3", "anio": 2026, "autores": ["Ansari, S."]},
            ],
        )
        assert resumen["vinculadas"] == 3
        assert {(v[0], v[1]) for v in sesion.vinculos} == {("c1", "r1"), ("c2", "r2"), ("c3", "r3")}


class TestReferenciasInstitucionales:
    """
    Una referencia puede no tener autores (guías web) o estar firmada por una
    persona pero citarse por su institución. Comparar solo `autores` dejaba
    esas citas huérfanas.
    """

    REFS = [
        {"ref_id": "r1", "anio": 2024, "autores": ["Yache Cuenca, E."],
         "titulo": "Registro Hospitalario del Cancer IREN NORTE, Informe 2024"},
        {"ref_id": "r2", "anio": 2020, "autores": [],
         "titulo": "International Agency for Research on Cancer report"},
        {"ref_id": "r3", "anio": 2024, "autores": ["Smith, J."],
         "titulo": "Deep learning for skin lesion analysis"},
    ]

    def test_vincula_por_la_institucion_del_titulo(self):
        ref_id, _, metodo, _ = emparejar_cita("IREN NORTE (2024)", self.REFS)
        assert ref_id == "r1"
        assert metodo == "institucion_en_titulo_anio"

    def test_una_palabra_generica_no_engancha_cualquier_titulo(self):
        # "Cancer (2020)" coincidía con dos institutos distintos por una sola
        # palabra común; el emparejamiento por título exige más de una palabra
        # y que alguna sea distintiva.
        ref_id, _, _, _ = emparejar_cita("Cancer (2020)", self.REFS)
        assert ref_id is None

    def test_el_emparejamiento_por_autor_sigue_teniendo_prioridad(self):
        _, confianza, metodo, _ = emparejar_cita("(Smith, 2024)", self.REFS)
        assert metodo == "primer_apellido_anio"
        assert confianza == 0.98
