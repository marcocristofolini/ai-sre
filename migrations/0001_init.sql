CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS incidents (
    id          BIGSERIAL PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    alert_name  TEXT NOT NULL,
    labels      JSONB NOT NULL DEFAULT '{}'::jsonb,
    fired_at    TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    status      TEXT NOT NULL DEFAULT 'investigating',
    error       TEXT
);
CREATE INDEX IF NOT EXISTS incidents_fingerprint_created_idx
    ON incidents (fingerprint, created_at DESC);

CREATE TABLE IF NOT EXISTS rcas (
    incident_id BIGINT PRIMARY KEY REFERENCES incidents (id) ON DELETE CASCADE,
    data        JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A dimensão (256) deve coincidir com aisre.memory.EMBEDDING_DIM.
CREATE TABLE IF NOT EXISTS memories (
    id          BIGSERIAL PRIMARY KEY,
    incident_id BIGINT NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    rca         JSONB NOT NULL,
    embedding   vector(256) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS memories_embedding_idx
    ON memories USING hnsw (embedding vector_cosine_ops);
