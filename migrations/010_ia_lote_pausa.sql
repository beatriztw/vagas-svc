-- Pausa do lote da IA (botão "Pausar avaliação" da tela): com o lote pausado, nem a coleta nem o
-- botão enfileiram compatibilidade em lote; o clique numa vaga e o currículo seguem. Linha única (id=1).
-- Fica no banco, e não numa variável de ambiente, para sobreviver a um reinício sem deploy.
-- Idempotente: pode ser aplicada de novo sem erro. Sem BEGIN/COMMIT e sem CONCURRENTLY.
CREATE TABLE IF NOT EXISTS ia_lote_pausa (
    id             SMALLINT PRIMARY KEY CHECK (id = 1),
    pausado        BOOLEAN NOT NULL DEFAULT false,
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);
