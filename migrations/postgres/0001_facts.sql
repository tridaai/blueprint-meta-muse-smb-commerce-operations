-- Facts: the provenance-carrying record of the store
-- One row per fact, insert-only, keyed by kind and natural key
-- The payload is JSON text in both dialects so figures read back identically
CREATE TABLE facts (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fact_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    natural_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    source_system TEXT NOT NULL,
    as_of TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE (kind, natural_key)
)
