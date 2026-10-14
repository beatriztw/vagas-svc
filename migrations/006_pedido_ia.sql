-- Fila de pedidos de IA (compatibilidade e currículo adaptado) e rótulo manual da faixa.
-- Idempotente: pode ser aplicada de novo sem erro. Sem BEGIN/COMMIT e sem CONCURRENTLY.
-- DEPENDE da 001_curriculo_perfil.sql (curriculo.id) e da 002_vaga_encontrada.sql (vaga_encontrada.url):
-- aplique as duas antes.
-- O buraco no 005 é proposital (frente cancelada); a numeração segue na 006.
-- Não cria a coluna `descricao`: ela é da 004 (detalhe da vaga).

CREATE TABLE IF NOT EXISTS pedido_ia (
    id               BIGSERIAL    PRIMARY KEY,
    tipo             TEXT         NOT NULL CHECK (tipo IN ('compatibilidade', 'curriculo')),
    vaga_url         TEXT         NOT NULL REFERENCES vaga_encontrada (url),
    estado           TEXT         NOT NULL DEFAULT 'pendente'
                     CHECK (estado IN ('pendente', 'rodando', 'pronto', 'erro')),
    tentativas       INTEGER      NOT NULL DEFAULT 0 CHECK (tentativas BETWEEN 0 AND 3),
    resultado        JSONB        NULL CHECK (resultado IS NULL OR jsonb_typeof(resultado) = 'object'),
    erro             TEXT         NULL CHECK (char_length(erro) <= 500),
    prompt_versao    TEXT         NULL CHECK (prompt_versao ~ '^[a-z]+-v[0-9]+$'),
    modelo           TEXT         NULL CHECK (char_length(modelo) <= 100),
    nota             SMALLINT     NULL CHECK (nota BETWEEN 0 AND 100),
    faixa            TEXT         NULL CHECK (faixa IN ('alta', 'média', 'baixa')),
    curriculo_id     BIGINT       NULL REFERENCES curriculo (id),
    criado_em        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    reivindicado_em  TIMESTAMPTZ  NULL,
    terminado_em     TIMESTAMPTZ  NULL,
    CHECK (estado <> 'pronto' OR (resultado IS NOT NULL AND prompt_versao IS NOT NULL)),
    CHECK (estado <> 'erro' OR erro IS NOT NULL),
    CHECK (estado <> 'rodando' OR reivindicado_em IS NOT NULL)
);

-- Um pedido ativo por (vaga, tipo): pedir de novo devolve o ativo.
CREATE UNIQUE INDEX IF NOT EXISTS pedido_ia_ativo_uq ON pedido_ia (vaga_url, tipo) WHERE estado IN ('pendente', 'rodando');
-- Fila do worker: pendentes do mais antigo para o mais novo.
CREATE INDEX IF NOT EXISTS pedido_ia_fila_idx ON pedido_ia (criado_em, id) WHERE estado = 'pendente';
-- Pedidos presos: rodando há mais do que o prazo.
CREATE INDEX IF NOT EXISTS pedido_ia_rodando_idx ON pedido_ia (reivindicado_em) WHERE estado = 'rodando';
-- Último pedido de cada tipo de uma vaga.
CREATE INDEX IF NOT EXISTS pedido_ia_vaga_idx ON pedido_ia (vaga_url, tipo, id DESC);

-- Faixa que a pessoa dá à vaga, para comparar com a da IA (métricas de acurácia).
CREATE TABLE IF NOT EXISTS rotulo_ia (
    vaga_url       TEXT         PRIMARY KEY REFERENCES vaga_encontrada (url),
    faixa          TEXT         NOT NULL CHECK (faixa IN ('alta', 'média', 'baixa')),
    atualizado_em  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
