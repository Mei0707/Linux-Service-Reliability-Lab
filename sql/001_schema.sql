CREATE TABLE IF NOT EXISTS links (
    code varchar(32) PRIMARY KEY,
    url text NOT NULL CHECK (length(url) <= 2048),
    created_at timestamptz NOT NULL DEFAULT now(),
    idempotency_key varchar(128) UNIQUE,
    request_hash char(64) NOT NULL
);
CREATE TABLE IF NOT EXISTS link_hits (
    code varchar(32) NOT NULL REFERENCES links(code) ON DELETE CASCADE,
    bucket smallint NOT NULL CHECK (bucket BETWEEN 0 AND 63),
    hits bigint NOT NULL DEFAULT 0 CHECK (hits >= 0),
    PRIMARY KEY (code, bucket)
);
ALTER TABLE links OWNER TO lab;
ALTER TABLE link_hits OWNER TO lab;
