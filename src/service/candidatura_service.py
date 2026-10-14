from src.model.candidatura import CandidaturaComVaga, StatusCandidatura
from src.repository.base import VagaEncontradaRepository
from src.repository.candidatura import CandidaturaRepository


class CandidaturaService:
    """Candidaturas: marcar aplicada, listar, mudar o status e desfazer. A validação mora no repositório."""

    def __init__(self, candidaturas: CandidaturaRepository, vagas: VagaEncontradaRepository | None = None):
        self.candidaturas = candidaturas
        # Com o histórico, a vaga aplicada sai das salvas (passa para "Vagas aplicadas").
        self.vagas = vagas

    def marcar_aplicada(self, vaga_url: str) -> tuple[CandidaturaComVaga, bool]:
        """(candidatura, criada). Marcar de novo devolve a existente com criada=False. Nos dois casos
        a vaga sai das salvas."""
        candidatura, criada = self.candidaturas.criar(vaga_url)
        if self.vagas is not None:
            self.vagas.marcar_salva(candidatura.vaga_url, False)
        return candidatura, criada

    def contar_salvas(self) -> int | None:
        """O número da aba "Salvas", para a resposta de "Marcar como aplicada" atualizar; None sem histórico."""
        return self.vagas.contar_salvas() if self.vagas is not None else None

    def listar(self) -> list[CandidaturaComVaga]:
        return self.candidaturas.listar()

    def contar(self) -> int:
        return self.candidaturas.contar()

    def obter(self, id: int) -> CandidaturaComVaga | None:
        return self.candidaturas.obter(id)

    def obter_por_url(self, vaga_url: str) -> CandidaturaComVaga | None:
        return self.candidaturas.obter_por_url(vaga_url)

    def mudar_status(self, id: int, status: StatusCandidatura | str) -> CandidaturaComVaga | None:
        return self.candidaturas.atualizar_status(id, status)

    def desfazer(self, id: int) -> bool:
        return self.candidaturas.remover(id)
