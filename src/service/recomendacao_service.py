import re
from datetime import date, timedelta

from src.client.base import contem, normalizar, palavras, relevancia_termo
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.model.vaga_externa import VagaExterna
from src.service.busca_service import BuscaService, ResultadoBusca


def _remota(vaga: VagaExterna) -> bool:
    # Providers que não informam a modalidade (remoto=None) só passam se o local disser que é remota.
    if vaga.remoto is not None:
        return vaga.remoto
    return contem(vaga.local, "remot")


# Como o nível aparece nos títulos; comparado palavra a palavra, depois de normalizar.
_NIVEL_NO_TITULO = {
    Nivel.JUNIOR: {"junior", "jr"},
    Nivel.PLENO: {"pleno", "pl"},
    Nivel.SENIOR: {"senior", "sr"},
}


def _palavras_do_titulo(titulo: str) -> set[str]:
    # "PL/SQL" é linguagem, não nível Pleno.
    return set(palavras(re.sub(r"pl\s*[/-]?\s*sql", "plsql", normalizar(titulo))))


def nivel_do_titulo(titulo: str) -> Nivel | None:
    do_titulo = _palavras_do_titulo(titulo)
    achados = [nivel for nivel, rotulos in _NIVEL_NO_TITULO.items() if do_titulo & rotulos]
    # Título com dois níveis ("Pleno/Sênior") ou nenhum não decide: a vaga não é cortada.
    return achados[0] if len(achados) == 1 else None


def _cidade(localidade: str) -> str:
    # "Rio de Janeiro, RJ", "Rio de Janeiro - RJ" e "Rio de Janeiro, Rio de Janeiro, Brasil"
    # comparam pela cidade, e por igualdade: "Vitória" não casa com "Vitória da Conquista".
    return " ".join(palavras(re.split(r",|\s[-–]\s|/|\(", localidade)[0]))


def _no_lugar(vaga: VagaExterna, cidades: set[str]) -> bool:
    if vaga.remoto or contem(vaga.local, "remot"):
        return True
    if not vaga.local:
        # Sem local informado não dá para saber: a vaga fica.
        return True
    return _cidade(vaga.local) in cidades


def pontuar(vaga: VagaExterna, skills: list[str]) -> int:
    """Quantas skills do perfil aparecem no título, palavra a palavra (o provider não traz a descrição).

    "Java" não pontua em "JavaScript", "SQL" não pontua em "NoSQL", e "Power BI" exige as
    duas palavras em sequência.
    """
    do_titulo = palavras(vaga.titulo)
    pontos = 0
    for skill in skills:
        da_skill = palavras(skill)
        n = len(da_skill)
        if n and any(do_titulo[i : i + n] == da_skill for i in range(len(do_titulo) - n + 1)):
            pontos += 1
    return pontos


def aplicar_perfil(vagas: list[VagaExterna], perfil: Perfil, hoje: date) -> list[VagaExterna]:
    """Elimina as vagas que o perfil exclui e ordena pelas skills; empate mantém a ordem recebida."""
    # Termo que fica vazio depois de normalizado ("", "—", emoji) casaria com todo título
    # (`"" in titulo`) e a busca voltaria sempre vazia: é ignorado.
    excluir = [termo for termo in perfil.excluir if normalizar(termo).strip()]
    filtradas = [v for v in vagas if not any(contem(v.titulo, termo) for termo in excluir)]
    # Conjunto: perfil vindo de fora sem normalizar pode repetir a modalidade.
    if set(perfil.modalidades) == {Modalidade.REMOTO}:
        filtradas = [v for v in filtradas if _remota(v)]
    if perfil.publicada_desde_dias is not None:
        limite = hoje - timedelta(days=perfil.publicada_desde_dias)
        filtradas = [v for v in filtradas if v.publicada_em is None or v.publicada_em >= limite]
    if perfil.niveis:
        aceitos = set(perfil.niveis)
        filtradas = [v for v in filtradas if (nivel := nivel_do_titulo(v.titulo)) is None or nivel in aceitos]
    cidades = {c for c in (_cidade(loc) for loc in perfil.localidades) if c}
    if cidades:
        filtradas = [v for v in filtradas if _no_lugar(v, cidades)]
    # sorted é estável: entre vagas com a mesma pontuação fica a ordem do BuscaService (mais nova antes).
    return sorted(filtradas, key=lambda v: pontuar(v, perfil.skills), reverse=True)


class RecomendacaoService:
    """Roda a busca nos providers, aplica os filtros do perfil e ordena por relevância do título."""

    def __init__(self, busca: BuscaService):
        self.busca = busca

    def recomendar(self, termo: str, perfil: Perfil | None, hoje: date | None = None) -> ResultadoBusca:
        resultado = self.busca.buscar(termo)
        vagas = resultado.vagas
        if perfil is not None:
            vagas = aplicar_perfil(vagas, perfil, hoje or date.today())
        # Título mais parecido com o termo primeiro; sorted é estável, então dentro da mesma
        # relevância fica a ordem das skills (e depois a da data).
        vagas = sorted(vagas, key=lambda v: relevancia_termo(v.titulo, termo), reverse=True)
        return ResultadoBusca(vagas=vagas, erros=resultado.erros)
