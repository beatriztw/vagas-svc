from datetime import datetime

import psycopg

from src.repository.sessao import PessoaLogada, SessaoRepository


class SessaoRepositoryPostgres(SessaoRepository):
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def criar(self, id_hash: str, pessoa_id: int, expira_em: datetime) -> None:
        with self._conn.cursor() as cur:
            cur.execute("INSERT INTO sessao (id, pessoa_id, expira_em) VALUES (%s, %s, %s)", (id_hash, pessoa_id, expira_em))

    def obter(self, id_hash: str, agora: datetime) -> PessoaLogada | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT p.id, p.nome_exibicao FROM sessao s JOIN pessoa p ON p.id = s.pessoa_id "
                "WHERE s.id = %s AND s.expira_em > %s",
                (id_hash, agora),
            )
            linha = cur.fetchone()
        return PessoaLogada(linha[0], linha[1]) if linha else None

    def remover(self, id_hash: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM sessao WHERE id = %s", (id_hash,))

    def remover_da_pessoa(self, pessoa_id: int) -> int:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM sessao WHERE pessoa_id = %s", (pessoa_id,))
            return cur.rowcount

    def limpar_expiradas(self, agora: datetime) -> int:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM sessao WHERE expira_em <= %s", (agora,))
            return cur.rowcount
