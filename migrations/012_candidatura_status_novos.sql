-- Status novos da candidatura: "teste" (Teste / case), e os de fim de processo "vaga_encerrada",
-- "sem_resposta" e "proposta_aceita". "oferta" continua com o mesmo valor (na tela, "Proposta recebida").
-- Troca só o CHECK da 003 (nome padrão do Postgres para o CHECK de coluna). Mesmos valores de
-- StatusCandidatura (src/model/candidatura.py).
-- Idempotente: pode ser aplicada de novo sem erro. Sem BEGIN/COMMIT e sem CONCURRENTLY.
ALTER TABLE candidatura DROP CONSTRAINT IF EXISTS candidatura_status_check;
ALTER TABLE candidatura ADD CONSTRAINT candidatura_status_check CHECK (status IN (
    'enviada', 'em_analise', 'teste', 'entrevista', 'oferta',
    'vaga_encerrada', 'recusada', 'sem_resposta', 'proposta_aceita'
));
