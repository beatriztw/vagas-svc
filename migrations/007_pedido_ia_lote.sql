-- Lote de compatibilidade: a IA avalia as vagas do histórico uma a uma, sem a pessoa pedir.
-- Idempotente: pode ser aplicada de novo sem erro. Sem BEGIN/COMMIT e sem CONCURRENTLY.
-- DEPENDE da 006_pedido_ia.sql (pedido_ia) e da 001_curriculo_perfil.sql (curriculo.id).

-- 0 = pedido da pessoa (clique na tela), 1 = lote. O clique passa na frente do lote na fila.
ALTER TABLE pedido_ia ADD COLUMN IF NOT EXISTS prioridade SMALLINT NOT NULL DEFAULT 0 CHECK (prioridade IN (0, 1));
-- Currículo base vigente quando o pedido foi criado: a nota só vale para essa base. Mudou a base, a
-- vaga volta para o lote. Pedido de antes desta migration fica NULL (não conta como avaliado).
ALTER TABLE pedido_ia ADD COLUMN IF NOT EXISTS curriculo_base_id BIGINT NULL REFERENCES curriculo (id) ON DELETE SET NULL;

-- Fila do worker: pendentes por prioridade e, dentro dela, do mais antigo para o mais novo.
DROP INDEX IF EXISTS pedido_ia_fila_idx;
CREATE INDEX IF NOT EXISTS pedido_ia_fila_prioridade_idx ON pedido_ia (prioridade, criado_em, id) WHERE estado = 'pendente';
-- Teto diário do lote: pedidos de prioridade 1 criados nas últimas 24 h.
CREATE INDEX IF NOT EXISTS pedido_ia_lote_idx ON pedido_ia (criado_em) WHERE prioridade = 1;
