-- Piso MENSAL em USD: faixa de salário base precisa alcançar esse valor.
-- NULL preserva os perfis atuais. A 016 está reservada ao login (#68).
-- Idempotente; sem BEGIN/COMMIT, como exige o runner de migrations.
ALTER TABLE perfil_busca ADD COLUMN IF NOT EXISTS salario_minimo_usd INT NULL
    CHECK (salario_minimo_usd BETWEEN 1 AND 1000000);
