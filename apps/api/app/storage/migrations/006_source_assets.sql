CREATE TABLE source_assets (
    asset_id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL REFERENCES imports(import_id),
    kind TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX source_assets_import ON source_assets (import_id);
