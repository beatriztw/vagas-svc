-- Vagas salvas para ver depois (a bandeirinha da tela): quando a pessoa salvou; NULL = não salva.
-- Idempotente: pode ser aplicada de novo sem erro. Sem BEGIN/COMMIT e sem CONCURRENTLY.
ALTER TABLE vaga_encontrada ADD COLUMN IF NOT EXISTS salva_em TIMESTAMPTZ NULL;

-- A aba "Salvas" lê só as salvas, da salva mais recente para a mais antiga.
CREATE INDEX IF NOT EXISTS vaga_encontrada_salva_em_idx ON vaga_encontrada (salva_em DESC) WHERE salva_em IS NOT NULL;
