-- Salário mínimo aceito (reais por mês) no perfil: a IA da compatibilidade limita a nota da vaga que
-- informa salário igual ou menor. NULL = sem piso. Não filtra a coleta.
-- Idempotente: pode ser aplicada de novo sem erro. Sem BEGIN/COMMIT e sem CONCURRENTLY.
ALTER TABLE perfil_busca ADD COLUMN IF NOT EXISTS salario_minimo INT NULL
    CHECK (salario_minimo BETWEEN 1 AND 1000000);
