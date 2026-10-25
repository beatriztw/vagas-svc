import psycopg

from src.repository.pessoa import PessoaRepository


class PessoaRepositoryPostgres(PessoaRepository):
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def listar(self) -> list[int]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT id FROM pessoa ORDER BY id")
            return [linha[0] for linha in cur.fetchall()]
