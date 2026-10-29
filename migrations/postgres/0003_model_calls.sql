-- Model calls: the per-run cost ledger for the model port
-- Tier is stamped on every row (Law 6) and cost is a Decimal stored as text
CREATE TABLE model_calls (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    call_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    model TEXT NOT NULL,
    tier TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd TEXT NOT NULL
)
