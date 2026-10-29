-- Artefacts: computed, insert-only, content-addressed
-- The input fingerprint names the exact fact set a figure was computed from
CREATE TABLE artefacts (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    artefact_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL
)
