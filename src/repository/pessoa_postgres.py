import psycopg

from src.model.erros import DadoInvalido
from src.repository.pessoa import CredencialPessoa, PessoaRepository, validar_nome


class PessoaRepositoryPostgres(PessoaRepository):
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def listar(self) -> list[int]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT id FROM pessoa ORDER BY id")
            return [linha[0] for linha in cur.fetchall()]

    def credencial_por_nome(self, nome: str) -> CredencialPessoa | None:
        # Mesma expressão do índice único `pessoa_nome_exibicao_uq`: no máximo uma linha.
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id, nome_exibicao, senha_hash FROM pessoa WHERE lower(nome_exibicao) = lower(%s)",
                (nome.strip(),),
            )
            linha = cur.fetchone()
        return CredencialPessoa(linha[0], linha[1], linha[2]) if linha else None

    def criar(self, nome: str, senha_hash: str | None) -> int:
        nome = validar_nome(nome)
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pessoa (nome_exibicao, senha_hash) VALUES (%s, %s) RETURNING id", (nome, senha_hash)
                )
                return cur.fetchone()[0]
        except psycopg.errors.UniqueViolation:
            raise DadoInvalido("já existe uma pessoa com esse nome") from None

    def definir_senha(self, pessoa_id: int, senha_hash: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("UPDATE pessoa SET senha_hash = %s WHERE id = %s", (senha_hash, pessoa_id))
            return cur.rowcount == 1
