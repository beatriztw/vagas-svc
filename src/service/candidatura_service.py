from src.model.candidatura import CandidaturaComVaga, StatusCandidatura
from src.repository.candidatura import CandidaturaRepository


class CandidaturaService:
    """Candidaturas: marcar aplicada, listar, mudar o status e desfazer. A validação mora no repositório."""

    def __init__(self, candidaturas: CandidaturaRepository):
        self.candidaturas = candidaturas

    def marcar_aplicada(self, vaga_url: str) -> tuple[CandidaturaComVaga, bool]:
        """(candidatura, criada). Marcar de novo devolve a existente com criada=False."""
        return self.candidaturas.criar(vaga_url)

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
