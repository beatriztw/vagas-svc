-- Candidaturas: vagas em que a pessoa marcou "aplicada" (POST /candidaturas).
-- Idempotente: pode ser aplicada de novo sem erro.
-- DEPENDE da 002_vaga_encontrada.sql (a chave estrangeira aponta para vaga_encontrada(url)):
-- aplique a 002 antes.

CREATE TABLE IF NOT EXISTS candidatura (
    id            BIGSERIAL    PRIMARY KEY,
    -- Uma candidatura por vaga: marcar de novo devolve a existente.
    vaga_url      TEXT         NOT NULL UNIQUE REFERENCES vaga_encontrada (url),
    -- Mesmos valores de StatusCandidatura (src/model/candidatura.py).
    status        TEXT         NOT NULL DEFAULT 'enviada'
                  CHECK (status IN ('enviada', 'em_analise', 'entrevista', 'recusada', 'oferta')),
    aplicada_em   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    atualizada_em TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- GET /candidaturas: mais recentes primeiro (desempate pelo id).
CREATE INDEX IF NOT EXISTS candidatura_aplicada_idx
    ON candidatura (aplicada_em DESC, id DESC);
