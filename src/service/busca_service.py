from dataclasses import dataclass, field
from datetime import date

from src.client.base import ProviderClient, palavras
from src.model.vaga_externa import VagaExterna


@dataclass
class ResultadoBusca:
    vagas: list[VagaExterna]
    erros: dict[str, str] = field(default_factory=dict)


def plataformas_uteis(plataformas: list[str]) -> list[list[str]]:
    """As `plataformas` do perfil como palavras; entrada sem palavra ("", "—") é ignorada."""
    return [p for p in (palavras(p) for p in plataformas) if p]


def casa_plataforma(nome: str, uteis: list[list[str]]) -> bool:
    """Por palavra, sem acento nem caixa: "linkedin" casa com "LinkedIn (Apify)"; "Remotar" não casa
    com "Remotar2". Sem plataforma útil, tudo casa."""
    return not uteis or any(set(p) <= set(palavras(nome)) for p in uteis)


class BuscaService:
    """Busca o termo nos providers. A falha de um provider não interrompe os outros."""

    def __init__(self, clients: list[ProviderClient]):
        self.clients = clients

    def clients_para(self, plataformas: list[str] = ()) -> list[ProviderClient]:
        """Os providers que as `plataformas` do perfil deixam consultar (todos, com a lista vazia).

        O `nome` do client é o `plataforma` das vagas que ele devolve: provider que o perfil
        descartaria inteiro não é chamado (e o pago, como o Apify, não é cobrado à toa).
        """
        uteis = plataformas_uteis(list(plataformas))
        return [c for c in self.clients if casa_plataforma(c.nome, uteis)]

    def buscar(self, termo: str, plataformas: list[str] = ()) -> ResultadoBusca:
        vagas: dict[str, VagaExterna] = {}
        erros: dict[str, str] = {}
        for client in self.clients_para(plataformas):
            try:
                encontradas = client.buscar(termo)
            except Exception as e:
                erros[client.nome] = f"{type(e).__name__}: {e}"
                continue
            for vaga in encontradas:
                vagas.setdefault(vaga.url, vaga)
        ordenadas = sorted(vagas.values(), key=lambda v: v.publicada_em or date.min, reverse=True)
        return ResultadoBusca(vagas=ordenadas, erros=erros)
