CREATE TABLE source_assets (
    asset_id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL REFERENCES imports(import_id),
    file_name TEXT,
    content_type TEXT,
    source_path TEXT,
    file_hash TEXT NOT NULL CHECK (length(file_hash) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX source_assets_import ON source_assets (import_id);
