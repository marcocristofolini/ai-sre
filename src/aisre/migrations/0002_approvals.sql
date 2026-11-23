CREATE TABLE IF NOT EXISTS approvals (
    incident_id BIGINT NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    command     TEXT NOT NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (incident_id, user_id)
);
