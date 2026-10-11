from datetime import date, timedelta

from src.client.base import contem, normalizar
from src.model.perfil import Perfil
from src.model.vaga import Modalidade
from src.model.vaga_externa import VagaExterna
from src.service.busca_service import BuscaService, ResultadoBusca


def _remota(vaga: VagaExterna) -> bool:
    # Providers que não informam a modalidade (remoto=None) só passam se o local disser que é remota.
    if vaga.remoto is not None:
        return vaga.remoto
    return contem(vaga.local, "remot")


def aplicar_perfil(vagas: list[VagaExterna], perfil: Perfil, hoje: date) -> list[VagaExterna]:
    """Elimina as vagas que o perfil exclui; não muda a ordem das que ficam."""
    # Termo que fica vazio depois de normalizado ("", "—", emoji) casaria com todo título
    # (`"" in titulo`) e a busca voltaria sempre vazia: é ignorado.
    excluir = [termo for termo in perfil.excluir if normalizar(termo).strip()]
    filtradas = [v for v in vagas if not any(contem(v.titulo, termo) for termo in excluir)]
    if perfil.modalidades == [Modalidade.REMOTO]:
        filtradas = [v for v in filtradas if _remota(v)]
    if perfil.publicada_desde_dias is not None:
        limite = hoje - timedelta(days=perfil.publicada_desde_dias)
        filtradas = [v for v in filtradas if v.publicada_em is None or v.publicada_em >= limite]
    return filtradas


class RecomendacaoService:
    """Roda a busca nos providers e aplica os filtros do perfil ao resultado."""

    def __init__(self, busca: BuscaService):
        self.busca = busca

    def recomendar(self, termo: str, perfil: Perfil | None, hoje: date | None = None) -> ResultadoBusca:
        resultado = self.busca.buscar(termo)
        if perfil is None:
            return resultado
        vagas = aplicar_perfil(resultado.vagas, perfil, hoje or date.today())
        return ResultadoBusca(vagas=vagas, erros=resultado.erros)
