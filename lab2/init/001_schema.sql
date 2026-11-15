-- Схема сохранённых профилей. Повторный запуск init безопасен.
CREATE TABLE IF NOT EXISTS profiles (
    id BIGSERIAL PRIMARY KEY,
    dataset_name TEXT NOT NULL,
    rows INTEGER NOT NULL CHECK (rows >= 0),
    column_count INTEGER NOT NULL CHECK (column_count >= 0),
    summary JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
