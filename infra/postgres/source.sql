-- Store department source records separately from the Bronze/Silver/Gold outputs.
CREATE TABLE IF NOT EXISTS source_runs (
    source_run text PRIMARY KEY,
    manifest jsonb NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS source_records (
    source_run text NOT NULL REFERENCES source_runs(source_run),
    source_path text NOT NULL,
    source_line bigint NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (source_run, source_path, source_line)
);
