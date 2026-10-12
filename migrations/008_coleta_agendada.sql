-- Coleta agendada pela própria app (08:00 e 18:00 de Brasília): um registro por horário tentado.
-- Idempotente: pode ser aplicada de novo sem erro. Sem BEGIN/COMMIT e sem CONCURRENTLY.
-- A chave é o horário agendado (em UTC): quem consegue inserir é quem coleta, então dois processos
-- ou um reinício no meio nunca rodam o mesmo horário duas vezes.
CREATE TABLE IF NOT EXISTS coleta_agendada (
    horario       TIMESTAMPTZ PRIMARY KEY,
    iniciada_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    terminada_em  TIMESTAMPTZ NULL,
    -- Só contagens e o tipo do erro; nunca vaga, termo nem dado do perfil.
    resultado     TEXT NULL CHECK (resultado IS NULL OR length(resultado) <= 500)
);
