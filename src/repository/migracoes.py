"""Aplica as migrations pendentes de `migrations/*.sql` na subida da app.

Regras:
- Ordem lexicográfica do nome do arquivo (`001_...`, `002_...`): numere com zeros à esquerda e
  largura fixa, senão `10_x` vem antes de `2_y`.
- Cada migration roda numa transação junto com o registro em `schema_migrations`: se falha, nada
  dela fica (nem tabela, nem registro) e `MigracaoFalhou` sobe, para a app não rodar com o schema
  pela metade.
- Tudo sob `pg_advisory_lock`: duas réplicas (ou a app e outro processo) subindo ao mesmo tempo
  não aplicam a mesma migration duas vezes; a segunda espera a primeira e depois vê tudo aplicado.
- Migration já aplicada nunca é reaplicada nem relida: não edite uma migration publicada, crie
  uma nova. As 001–003 foram aplicadas à mão antes desta regra; são idempotentes e rodam de novo
  no primeiro boot, passando a constar em `schema_migrations`.
- O arquivo não pode ter `BEGIN`/`COMMIT`/`ROLLBACK` (quebraria a transação que o envolve) nem
  `CREATE INDEX CONCURRENTLY` (não roda dentro de transação). Use `IF NOT EXISTS` para ser idempotente.
- Sem `lock_timeout`/`statement_timeout`: quem sobe depois espera o lock até a outra réplica
  terminar (uma migration lenta segura a subida das demais).
- Banco fora do ar na subida: `aplicar_com_retry` tenta de novo com backoff até o teto e depois
  falha. Erro de SQL numa migration não tem retry: falha na hora.
"""

import logging
import time
from collections.abc import Callable
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)

# `migrations/` na raiz do repositório (na imagem, /app/migrations), por Path(__file__), não pelo cwd.
PASTA_MIGRATIONS = Path(__file__).resolve().parent.parent.parent / "migrations"

# Chave fixa do `pg_advisory_lock` (bigint), só das migrations. A da coleta é CHAVE_TRAVA_COLETA
# (src/repository/trava.py): outro uso de advisory lock neste banco tem que escolher outra chave.
CHAVE_LOCK_MIGRATIONS = 7_650_001_002


class MigracaoFalhou(Exception):
    """Uma migration deu erro (ou a pasta não existe). Não é DadoInvalido: é falha de servidor, a subida aborta."""


def listar(pasta: Path) -> list[Path]:
    if not pasta.is_dir():
        raise MigracaoFalhou(f"pasta de migrations não encontrada: {pasta}")
    return sorted(pasta.glob("*.sql"), key=lambda p: p.name)


def aplicar_pendentes(
    pasta: Path = PASTA_MIGRATIONS,
    conectar: Callable[[], psycopg.Connection] | None = None,
) -> list[str]:
    """Aplica as pendentes em ordem e devolve os nomes aplicados agora.

    Abre uma conexão própria (fora do pool) e a fecha no fim, o que também solta o lock.
    `conectar` existe para os testes escolherem o schema; a produção usa `conexao.conectar`.
    """
    arquivos = listar(pasta)
    if conectar is None:
        from src.repository.conexao import conectar as conectar_padrao

        conectar = conectar_padrao
    aplicadas: list[str] = []
    conn = conectar()
    try:
        # Lock de sessão: fechar a conexão (abaixo) o solta, mesmo se ela cair no meio.
        conn.execute("SELECT pg_advisory_lock(%s)", (CHAVE_LOCK_MIGRATIONS,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "nome TEXT PRIMARY KEY, aplicada_em TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        # Lido depois de pegar o lock: quem esperou já enxerga o que o outro aplicou.
        ja = {linha[0] for linha in conn.execute("SELECT nome FROM schema_migrations")}
        for arquivo in arquivos:
            if arquivo.name in ja:
                continue
            _aplicar(conn, arquivo)
            aplicadas.append(arquivo.name)
    finally:
        conn.close()
    if aplicadas:
        log.info("migrations aplicadas: %s", ", ".join(aplicadas))
    else:
        log.info("migrations: nada pendente")
    return aplicadas


def _aplicar(conn: psycopg.Connection, arquivo: Path) -> None:
    try:
        with conn.transaction():
            conn.execute(arquivo.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (nome) VALUES (%s)", (arquivo.name,))
    except Exception as e:
        log.error("migration %s falhou e foi desfeita: %s", arquivo.name, e)
        raise MigracaoFalhou(f"migration {arquivo.name} falhou: {e}") from e


TETO_RETRY_S = 60.0
PAUSA_INICIAL_S = 1.0
PAUSA_MAXIMA_S = 8.0


def aplicar_com_retry(
    pasta: Path = PASTA_MIGRATIONS,
    conectar: Callable[[], psycopg.Connection] | None = None,
    teto_s: float = TETO_RETRY_S,
    dormir: Callable[[float], None] = time.sleep,
    agora: Callable[[], float] = time.monotonic,
) -> list[str]:
    """`aplicar_pendentes` com retry e backoff só para banco inacessível (`psycopg.OperationalError`
    ao conectar/travar); passado o teto, levanta `MigracaoFalhou`. `MigracaoFalhou` (erro de SQL,
    pasta ausente) sobe na hora, sem retry. `dormir` e `agora` são injetáveis para os testes."""
    limite = agora() + teto_s
    pausa = PAUSA_INICIAL_S
    while True:
        try:
            return aplicar_pendentes(pasta, conectar)
        except psycopg.OperationalError as e:
            restante = limite - agora()
            if restante <= 0:
                log.error("banco inacessível por %.0f s, migrations não aplicadas: %s", teto_s, e)
                raise MigracaoFalhou(f"banco inacessível após {teto_s:.0f} s: {e}") from e
            espera = min(pausa, restante)
            log.warning("banco inacessível (%s); nova tentativa em %.1f s", e, espera)
            dormir(espera)
            pausa = min(pausa * 2, PAUSA_MAXIMA_S)
