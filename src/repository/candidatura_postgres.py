import psycopg
from psycopg import errors
from psycopg.rows import dict_row

from src.model.candidatura import CandidaturaComVaga, StatusCandidatura
from src.model.erros import DadoInvalido
from src.repository.candidatura import (
    CandidaturaRepository,
    id_possivel,
    validar_id,
    validar_status,
    validar_vaga_url,
)

_SELECT = (
    "SELECT c.id, c.vaga_url, c.status, c.aplicada_em, c.atualizada_em, v.titulo, v.empresa "
    "FROM candidatura c JOIN vaga_encontrada v ON v.url = c.vaga_url "
)


def _candidatura(linha: dict) -> CandidaturaComVaga:
    return CandidaturaComVaga(**{**linha, "status": StatusCandidatura(linha["status"])})


class CandidaturaRepositoryPostgres(CandidaturaRepository):
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _um(self, filtro: str, params: tuple) -> CandidaturaComVaga | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"{_SELECT}WHERE {filtro}", params)
            linha = cur.fetchone()
        return _candidatura(linha) if linha else None

    def criar(self, vaga_url: str) -> tuple[CandidaturaComVaga, bool]:
        vaga_url = validar_vaga_url(vaga_url)
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                # ON CONFLICT DO NOTHING espera a transação concorrente que gravou a mesma vaga;
                # sem linha devolvida, a candidatura já existia e é relida abaixo.
                cur.execute(
                    "INSERT INTO candidatura (vaga_url) VALUES (%s) ON CONFLICT (vaga_url) DO NOTHING RETURNING id",
                    (vaga_url,),
                )
                criada = cur.fetchone() is not None
        except errors.ForeignKeyViolation:
            # Só a vaga inexistente (a única chave estrangeira da tabela); o resto sobe como 500.
            raise DadoInvalido(f"vaga não encontrada no histórico: {vaga_url}") from None
        candidatura = self.obter_por_url(vaga_url)
        if candidatura is None:
            # Removida entre o INSERT e a leitura (outra requisição desfez): a chamada seguinte recria.
            raise RuntimeError(f"candidatura de {vaga_url} sumiu logo depois de gravada")
        return candidatura, criada

    def listar(self) -> list[CandidaturaComVaga]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"{_SELECT}ORDER BY c.aplicada_em DESC, c.id DESC")
            return [_candidatura(linha) for linha in cur.fetchall()]

    def contar(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM candidatura")
            return cur.fetchone()[0]

    def obter(self, id: int) -> CandidaturaComVaga | None:
        id = validar_id(id)
        return self._um("c.id = %s", (id,)) if id_possivel(id) else None

    def obter_por_url(self, vaga_url: str) -> CandidaturaComVaga | None:
        return self._um("c.vaga_url = %s", (validar_vaga_url(vaga_url),))

    def atualizar_status(self, id: int, status: StatusCandidatura | str) -> CandidaturaComVaga | None:
        id = validar_id(id)
        status = validar_status(status)  # DadoInvalido antes de chegar ao CHECK do banco
        if not id_possivel(id):
            return None
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "UPDATE candidatura SET status = %s, atualizada_em = now() WHERE id = %s RETURNING id",
                (status.value, id),
            )
            if cur.fetchone() is None:
                return None
        return self.obter(id)

    def remover(self, id: int) -> bool:
        id = validar_id(id)
        if not id_possivel(id):
            return False
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("DELETE FROM candidatura WHERE id = %s", (id,))
            return cur.rowcount > 0
