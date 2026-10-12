-- Histórico das vagas achadas pela coleta (POST /coletas ou python -m src.controller.coleta).
-- Idempotente: pode ser aplicada de novo sem erro. Depende só de si mesma (não usa a 001).

CREATE TABLE IF NOT EXISTS vaga_encontrada (
    url              TEXT         PRIMARY KEY,
    titulo           TEXT         NOT NULL,
    empresa          TEXT         NOT NULL,
    plataforma       TEXT         NOT NULL,
    local            TEXT         NULL,
    remoto           BOOLEAN      NULL,
    publicada_em     DATE         NULL,
    -- Termos do perfil que acharam a vaga, na ordem em que acharam, sem repetir.
    termos           TEXT[]       NOT NULL DEFAULT '{}',
    primeira_vez_em  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    ultima_vez_em    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- NULL = nova (ainda não vista). Uma nova coleta não zera.
    vista_em         TIMESTAMPTZ  NULL
);

-- GET /vagas/encontradas?novas=true: não vistas, mais recentes primeiro.
CREATE INDEX IF NOT EXISTS vaga_encontrada_novas_idx
    ON vaga_encontrada (primeira_vez_em DESC)
    WHERE vista_em IS NULL;

-- Sem o filtro (novas=false): todas, mais recentes primeiro.
CREATE INDEX IF NOT EXISTS vaga_encontrada_primeira_vez_idx
    ON vaga_encontrada (primeira_vez_em DESC);
