-- Login por pessoa: a senha (hash scrypt, formato `scrypt$n$r$p$salt_b64$hash_b64`) e as sessões.
-- `senha_hash` NULL = a pessoa não entra (nenhuma senha vale): a senha é definida pela CLI
-- (`python -m src.controller.pessoa senha --id N`), nunca por esta migration nem por tela.
-- `sessao.id` é o sha256 (hex) do token que vai no cookie: o token em si NUNCA é gravado, então um dump do
-- banco não entrega sessão nenhuma. Apagar a pessoa apaga as sessões dela.
-- O login é pelo `nome_exibicao`, então ele tem que ser único (sem diferenciar caixa).
-- Idempotente: pode ser aplicada de novo sem erro. Sem BEGIN/COMMIT e sem CONCURRENTLY.
-- DEPENDE da 013 (tabela pessoa).

ALTER TABLE pessoa ADD COLUMN IF NOT EXISTS senha_hash TEXT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS pessoa_nome_exibicao_uq ON pessoa (lower(nome_exibicao));

CREATE TABLE IF NOT EXISTS sessao (
    id         TEXT         PRIMARY KEY,
    pessoa_id  BIGINT       NOT NULL REFERENCES pessoa (id) ON DELETE CASCADE,
    criada_em  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expira_em  TIMESTAMPTZ  NOT NULL
);

CREATE INDEX IF NOT EXISTS sessao_expira_em_idx ON sessao (expira_em);
CREATE INDEX IF NOT EXISTS sessao_pessoa_idx ON sessao (pessoa_id);
