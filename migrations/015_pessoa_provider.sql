-- Providers de vaga de cada pessoa: quais fontes ela usa e a config de cada uma (listas de empresas, locais
-- do LinkedIn, limite). Tabela vazia para a pessoa = vale o ambiente do servidor (providers_padrao): nada é
-- semeado aqui, então a pessoa 1 segue como antes.
-- Segredos (APIFY_TOKEN) NUNCA entram aqui: ficam no ambiente. O teto de custo do Apify (APIFY_LIMITE_MAX) é
-- conferido pela app ao gravar e ao montar os providers; o banco só garante o formato.
-- Idempotente: pode ser aplicada de novo sem erro. Sem BEGIN/COMMIT e sem CONCURRENTLY.
-- DEPENDE da 013 (tabela pessoa).

CREATE TABLE IF NOT EXISTS pessoa_provider (
    pessoa_id  BIGINT  NOT NULL REFERENCES pessoa (id),
    provider   TEXT    NOT NULL CHECK (provider IN (
        'remotar', 'trampos', 'himalayas', 'remotive', 'greenhouse', 'lever', 'ashby', 'linkedin', 'linkedin_posts'
    )),
    config     JSONB   NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    PRIMARY KEY (pessoa_id, provider)
);
