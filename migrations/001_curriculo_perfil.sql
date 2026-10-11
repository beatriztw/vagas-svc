-- Currículos da Beatriz e perfil de busca de vagas.
-- Idempotente: pode ser aplicada de novo sem erro.

CREATE TABLE IF NOT EXISTS curriculo (
    id           BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo         TEXT         NOT NULL CHECK (tipo IN ('base', 'adaptado')),
    titulo       TEXT         NOT NULL,
    conteudo_md  TEXT         NOT NULL,
    base_id      BIGINT       NULL REFERENCES curriculo (id),
    vaga_url     TEXT         NULL,
    arquivo_s3   TEXT         NULL,
    criado_em    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- Base não aponta para outra base; adaptado sempre aponta para a base de origem.
    CHECK ((tipo = 'base' AND base_id IS NULL) OR (tipo = 'adaptado' AND base_id IS NOT NULL))
);

-- A base vigente é a linha tipo='base' mais recente; as anteriores ficam como histórico.

-- Uma linha só (id = 1). Os valores dos CHECK espelham Modalidade e Nivel de src/model/vaga.py.
CREATE TABLE IF NOT EXISTS perfil_busca (
    id                    SMALLINT     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    termos                TEXT[]       NOT NULL DEFAULT '{}',
    excluir               TEXT[]       NOT NULL DEFAULT '{}',
    modalidades           TEXT[]       NOT NULL DEFAULT '{}' CHECK (modalidades <@ ARRAY['Remoto', 'Híbrido']),
    localidades           TEXT[]       NOT NULL DEFAULT '{}',
    plataformas           TEXT[]       NOT NULL DEFAULT '{}',
    niveis                TEXT[]       NOT NULL DEFAULT '{}' CHECK (niveis <@ ARRAY['Júnior', 'Pleno', 'Sênior']),
    skills                TEXT[]       NOT NULL DEFAULT '{}',
    publicada_desde_dias  INT          NULL CHECK (publicada_desde_dias BETWEEN 1 AND 3650),
    ocultar_candidatadas  BOOLEAN      NOT NULL DEFAULT TRUE,
    atualizado_em         TIMESTAMPTZ  NOT NULL DEFAULT now()
);
