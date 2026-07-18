# Pruebas unitarias vigentes

Aquí viven las pruebas que se ejecutan y que deben pasar en verde. Todo lo que
esté en esta carpeta se prueba contra el código actual.

```bash
cd backend
pytest                 # ejecuta tests/unit y tests/integration
pytest tests/unit      # solo unitarias
```

## Reglas

- **Aisladas**: sin red, sin base de datos, sin llamadas reales al LLM. Todo lo
  externo se sustituye con `unittest.mock.patch`.
- **Una afirmación por prueba**: preparar, ejecutar, comprobar.
- **Nombre descriptivo**: el nombre de la función dice qué comportamiento se
  garantiza, no qué función se llama.
- Si una prueba deja de corresponderse con el código, se actualiza o se borra —
  nunca se deja fallando ni se ignora.

## Cobertura pendiente

Las pruebas anteriores están en `tests/deprecated/` (excluidas de la ejecución).
Servicios sin cobertura vigente:

- [ ] `pdf_service` — extracción de texto del PDF
- [ ] `extraccion_service` — detección de citas y referencias APA
- [ ] `grafo_carga_service` — vinculación cita → referencia (apellido + año)
- [ ] `recuperacion_service` — recuperación híbrida grafo + pgvector
- [ ] `auditoria_service` — construcción del veredicto
- [ ] `embedding_service` — troceado e indexación
- [ ] `verificacion_service` — niveles de confianza por DOI
- [ ] `metricas_service` — Kappa de Cohen y F1
