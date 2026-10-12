-- Detalhe completo e expiração das vagas (frente C). Idempotente: pode ser aplicada de novo sem erro.
-- O detalhe é buscado sob demanda quando a vaga é aberta na tela; `detalhada_em` é o cache (refaz
-- depois de 24 h). Texto já sanitizado pela aplicação (texto puro, nunca HTML do provider).

ALTER TABLE vaga_encontrada
    -- Texto puro, até 20 mil caracteres (a aplicação trunca antes de gravar).
    ADD COLUMN IF NOT EXISTS descricao    TEXT         NULL CHECK (char_length(descricao) <= 20000),
    -- [{"texto": "...", "obrigatorio": true}, ...]
    ADD COLUMN IF NOT EXISTS requisitos   JSONB        NULL
        CHECK (jsonb_typeof(requisitos) = 'array' AND jsonb_array_length(requisitos) <= 100),
    -- {"minimo": 350000, "maximo": 450000, "moeda": "BRL", "periodo": "monthly"} (valores em centavos);
    -- NULL = não informado.
    ADD COLUMN IF NOT EXISTS salario      JSONB        NULL CHECK (jsonb_typeof(salario) = 'object'),
    -- ["Assistência médica", ...]
    ADD COLUMN IF NOT EXISTS beneficios   JSONB        NULL
        CHECK (jsonb_typeof(beneficios) = 'array' AND jsonb_array_length(beneficios) <= 100),
    ADD COLUMN IF NOT EXISTS subtitulo    TEXT         NULL CHECK (char_length(subtitulo) <= 500),
    -- NULL = no ar. Preenchida quando o provider diz que expirou (listagem ou detalhe) ou o detalhe dá 404.
    -- A vaga expirada some da lista e do contador; `obter` por URL ainda a acha ("vaga expirada").
    ADD COLUMN IF NOT EXISTS expirada_em  TIMESTAMPTZ  NULL,
    -- Quando o detalhe foi buscado pela última vez com sucesso (cache de 24 h).
    ADD COLUMN IF NOT EXISTS detalhada_em TIMESTAMPTZ  NULL;

-- Sem índice novo de propósito: `expirada_em IS NULL` vale para quase todas as linhas e um índice
-- parcial não ajudaria a lista (que já usa os índices de primeira_vez_em da 002).
