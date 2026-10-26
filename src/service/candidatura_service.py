from src.model.candidatura import CandidaturaComVaga, StatusCandidatura
from src.model.erros import DadoInvalido
from src.model.vaga_externa import VagaExterna
from src.repository.base import VagaEncontradaRepository
from src.repository.candidatura import CandidaturaRepository

# Plataforma e termo da vaga que não veio da coleta (achada em blog, inHire, site da empresa).
PLATAFORMA_MANUAL = "manual"
TERMO_MANUAL = "manual"


class CandidaturaService:
    """Candidaturas: marcar aplicada, listar, mudar o status e desfazer. A validação mora no repositório."""

    def __init__(self, candidaturas: CandidaturaRepository, vagas: VagaEncontradaRepository | None = None):
        self.candidaturas = candidaturas
        # Com o histórico, a vaga aplicada sai das salvas (passa para "Vagas aplicadas").
        self.vagas = vagas

    def marcar_aplicada(
        self, vaga_url: str, titulo: str | None = None, empresa: str | None = None
    ) -> tuple[CandidaturaComVaga, bool]:
        """(candidatura, criada). Marcar de novo devolve a existente com criada=False. Nos dois casos
        a vaga sai das salvas.

        Com `titulo` e `empresa`, a vaga fora do histórico entra nele antes (plataforma "manual", já
        vista); a que já estava fica como a coleta gravou."""
        externa = _vaga_manual(vaga_url, titulo, empresa)
        if externa is not None:
            if self.vagas is None:
                raise DadoInvalido("vaga manual precisa do histórico de vagas")
            if self.vagas.obter(externa.url) is None:
                self.vagas.registrar([externa], TERMO_MANUAL)
                self.vagas.marcar_vistas([externa.url])
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


def _vaga_manual(vaga_url: str, titulo: str | None, empresa: str | None) -> VagaExterna | None:
    """A vaga informada à mão, ou None sem título e empresa. Só um dos dois, vazio ou com NUL é DadoInvalido."""
    if titulo is None and empresa is None:
        return None
    campos = {"titulo": titulo, "empresa": empresa}
    for nome, valor in campos.items():
        # O Postgres não aceita NUL em TEXT.
        if not isinstance(valor, str) or not valor.strip() or "\x00" in valor:
            raise DadoInvalido(f"vaga manual: {nome} tem que ser um texto não vazio (informe título e empresa)")
    if not isinstance(vaga_url, str) or not vaga_url.strip() or "\x00" in vaga_url:
        raise DadoInvalido("vaga_url tem que ser um texto não vazio")
    return VagaExterna(titulo=titulo.strip(), empresa=empresa.strip(), plataforma=PLATAFORMA_MANUAL, url=vaga_url)
