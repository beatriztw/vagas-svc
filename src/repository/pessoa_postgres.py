import psycopg

from src.repository.pessoa import PessoaListada, PessoaRepository


class PessoaRepositoryPostgres(PessoaRepository):
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def listar(self) -> list[int]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT id FROM pessoa ORDER BY id")
            return [linha[0] for linha in cur.fetchall()]

    def listar_com_nome(self) -> list[PessoaListada]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT id, nome_exibicao FROM pessoa ORDER BY id")
            return [PessoaListada(linha[0], linha[1]) for linha in cur.fetchall()]
