CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS papers_chunks (
  id              TEXT PRIMARY KEY,
  doi             TEXT NOT NULL,
  doi_normalizado TEXT NOT NULL,
  titulo          TEXT,
  anio            INTEGER,
  pagina          INTEGER,
  chunk_index     INTEGER,
  nivel_confianza TEXT,
  referencia_id   TEXT,
  contenido       TEXT NOT NULL,
  embedding       vector(384)
);

CREATE INDEX IF NOT EXISTS idx_papers_doi
  ON papers_chunks(doi_normalizado);

CREATE INDEX IF NOT EXISTS idx_papers_embedding
  ON papers_chunks
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);
