from dataclasses import dataclass, field
from datetime import date

from src.client.base import ProviderClient
from src.model.vaga_externa import VagaExterna


@dataclass
class ResultadoBusca:
    vagas: list[VagaExterna]
    erros: dict[str, str] = field(default_factory=dict)


class BuscaService:
    """Busca o termo em todos os providers. A falha de um provider não interrompe os outros."""

    def __init__(self, clients: list[ProviderClient]):
        self.clients = clients

    def buscar(self, termo: str) -> ResultadoBusca:
        vagas: dict[str, VagaExterna] = {}
        erros: dict[str, str] = {}
        for client in self.clients:
            try:
                encontradas = client.buscar(termo)
            except Exception as e:
                erros[client.nome] = f"{type(e).__name__}: {e}"
                continue
            for vaga in encontradas:
                vagas.setdefault(vaga.url, vaga)
        ordenadas = sorted(vagas.values(), key=lambda v: v.publicada_em or date.min, reverse=True)
        return ResultadoBusca(vagas=ordenadas, erros=erros)
