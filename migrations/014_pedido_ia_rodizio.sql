-- Rodízio da cota do worker entre as pessoas (PR de IA por pessoa): o `proximo` olha, por pessoa, o último
-- pedido de lote reivindicado, e o teto diário do lote (IA_LOTE_MAX_POR_DIA) passa a contar o lote de todas.
-- Idempotente, sem BEGIN/COMMIT e sem CONCURRENTLY, como as outras.

-- Último lote reivindicado de cada pessoa (max(reivindicado_em) filtrado por pessoa, só no lote).
CREATE INDEX IF NOT EXISTS pedido_ia_lote_reivindicado_idx
    ON pedido_ia (pessoa_id, reivindicado_em) WHERE prioridade = 1 AND reivindicado_em IS NOT NULL;

-- Teto diário global: quantos pedidos de lote foram criados nas últimas 24 h, de qualquer pessoa.
CREATE INDEX IF NOT EXISTS pedido_ia_lote_criado_idx ON pedido_ia (criado_em) WHERE prioridade = 1;
