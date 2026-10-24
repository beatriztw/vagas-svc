import psycopg
from psycopg.types.json import Jsonb

from src.model.provider_pessoa import NOMES, ProviderPessoa, validar_providers
from src.repository.pessoa_provider import PessoaProviderRepository


class PessoaProviderRepositoryPostgres(PessoaProviderRepository):
    def __init__(self, conn: psycopg.Connection, pessoa_id: int) -> None:
        self._conn = conn
        self._pessoa_id = pessoa_id

    def listar(self) -> list[ProviderPessoa]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT provider, config FROM pessoa_provider WHERE pessoa_id = %s", (self._pessoa_id,))
            linhas = [ProviderPessoa(provider, config) for provider, config in cur.fetchall()]
        return sorted(linhas, key=lambda p: NOMES.index(p.provider))

    def salvar(self, providers: list[ProviderPessoa]) -> None:
        providers = validar_providers(providers)
        with self._conn.transaction(), self._conn.cursor() as cur:
            # Trava a linha da pessoa: dois PUT juntos não se atropelam (o segundo apaga o que o primeiro gravou).
            cur.execute("SELECT 1 FROM pessoa WHERE id = %s FOR UPDATE", (self._pessoa_id,))
            cur.execute("DELETE FROM pessoa_provider WHERE pessoa_id = %s", (self._pessoa_id,))
            for item in providers:
                cur.execute(
                    "INSERT INTO pessoa_provider (pessoa_id, provider, config) VALUES (%s, %s, %s)",
                    (self._pessoa_id, item.provider, Jsonb(item.config)),
                )
