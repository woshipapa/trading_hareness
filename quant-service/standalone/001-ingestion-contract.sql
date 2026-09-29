-- Compatibility contract for a quant-only database.
-- The Feishu adapter owns the full ingestion ledger in the integrated stack.
-- Quant-only mode never writes this table; it exists solely so the historical
-- analyst_signals FK can be created while ingestion routes remain unused.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE IF NOT EXISTS public.ingestion_jobs (
    job_id uuid PRIMARY KEY
);
