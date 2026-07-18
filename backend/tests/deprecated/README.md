# Pruebas deprecadas — NO se ejecutan

Estas pruebas se escribieron contra una versión anterior del sistema y **ya no
se corresponden con el código actual**. `pytest.ini` las excluye de la
recolección (`norecursedirs = deprecated`), de modo que no aparecen al ejecutar
la suite.

Se conservan solo como referencia histórica y como material de partida para
reescribirlas: muchos de los casos que cubren siguen siendo válidos como *idea*,
aunque su implementación esté rota.

## Por qué están desfasadas

| Suponen | El sistema hoy |
|---|---|
| Veredictos `VALIDA` / `INVALIDA` | `SUPPORTS` / `REFUTES` / `NO_INFO` |
| Vector store en ChromaDB | Supabase Postgres + pgvector |
| Recuperación con `metodo="chroma"` | `hibrido` / `solo_grafo` / `solo_vectorial` / `no_encontrado` |

## Qué hacer con ellas

Reescribir por servicio, moviendo cada caso recuperado a `tests/unit/`. Una vez
migrado todo lo que valga la pena, esta carpeta se borra.

| Archivo | Casos | Estado |
|---|---|---|
| `test_extraccion_service.py` | 49 | Pendiente de revisar |
| `test_embedding_service.py` | 14 | Pendiente de revisar |
| `test_pdf_service.py` | 11 | Pendiente de revisar |
| `test_recuperacion_service.py` | 7 | Pendiente de revisar |
| `test_auditoria_service.py` | 2 | Roto: `VeredictoTipo.VALIDA` no existe |
