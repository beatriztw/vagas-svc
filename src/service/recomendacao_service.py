import re
from datetime import date, timedelta
from typing import TypeVar

from src.client.base import _LIGACAO, contem, normalizar, palavras, relevancia_termo
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.model.vaga_externa import VagaExterna
from src.service.busca_service import BuscaService, ResultadoBusca

# VagaExterna (busca ao vivo) ou VagaEncontrada (histórico): os filtros só leem os campos comuns.
V = TypeVar("V")


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


_SO_PAIS = {"brasil", "brazil"}


def _cidade(localidade: str) -> str:
    # "Rio de Janeiro, RJ", "Rio de Janeiro - RJ", "Rio de Janeiro-RJ" e "Rio de Janeiro, Rio de Janeiro, Brasil"
    # comparam pela cidade, e por igualdade: "Vitória" não casa com "Vitória da Conquista".
    # "-RJ" colado no fim também é UF; "Embu-Guaçu" segue sendo uma cidade só.
    cidade = " ".join(palavras(re.split(r",|\s[-–]\s|/|\(|[-–](?=\s*[A-Za-z]{2}\s*$)", localidade)[0]))
    # O LinkedIn marca a região metropolitana: "Rio de Janeiro e Região", "Greater São Paulo Area".
    # "Região Metropolitana do Rio de Janeiro", "da Baixada Santista", "de Belo Horizonte".
    cidade = re.sub(r"^(regiao metropolitana d[eoa]s?|grande|greater) ", "", cidade)
    return re.sub(r" (e regiao|metropolitan area|area)$", "", cidade)


def _no_lugar(vaga: VagaExterna, cidades: set[str]) -> bool:
    if vaga.remoto or contem(vaga.local, "remot"):
        return True
    cidade = _cidade(vaga.local or "")
    if not cidade or cidade in _SO_PAIS:
        # Sem local, ou só o país: não dá para saber a cidade, então a vaga fica.
        return True
    return cidade in cidades


def pontuar(vaga: VagaExterna, skills: list[str]) -> int:
    """Quantas skills do perfil aparecem no título (o provider não traz a descrição).

    Mesma comparação de `relevancia_termo`: palavra inteira por radical, compostos juntos
    ("front-end" = "Front End") e cargo nos dois idiomas. "Java" não pontua em
    "JavaScript", "SQL" em "NoSQL", nem "C#" em "C++".
    """
    return sum(1 for skill in skills if palavras(skill) and relevancia_termo(vaga.titulo, skill) == 1)


def filtrar_pelo_perfil(vagas: list[V], perfil: Perfil, hoje: date) -> list[V]:
    """Só as vagas que o perfil não elimina, na ordem recebida.

    É a regra única dos filtros do perfil: a busca ao vivo (`aplicar_perfil`) e o histórico
    da coleta (`HistoricoService`) passam por aqui. Serve para `VagaExterna` e `VagaEncontrada`
    (usa só `titulo`, `local`, `remoto` e `publicada_em`).
    """
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
    # Localidade que é só o país ("Brasil") não restringe cidade nenhuma.
    cidades = {c for c in (_cidade(loc) for loc in perfil.localidades) if c and c not in _SO_PAIS}
    if cidades:
        filtradas = [v for v in filtradas if _no_lugar(v, cidades)]
    return filtradas


def aplicar_perfil(vagas: list[VagaExterna], perfil: Perfil, hoje: date) -> list[VagaExterna]:
    """Elimina as vagas que o perfil exclui e ordena pelas skills; empate mantém a ordem recebida."""
    # sorted é estável: entre vagas com a mesma pontuação fica a ordem do BuscaService (mais nova antes).
    return sorted(filtrar_pelo_perfil(vagas, perfil, hoje), key=lambda v: pontuar(v, perfil.skills), reverse=True)


def relevancia_perfil(titulo: str, termos: list[str]) -> float:
    """Maior `relevancia_termo` do título entre os termos do perfil; 0 sem termo útil.

    Termo sem palavra útil ("", "de", "—") vale 1 em `relevancia_termo` para qualquer
    título: aqui ele não diz nada sobre a vaga e é ignorado, senão todo título empataria em 1.
    """
    uteis = [t for t in termos if any(p not in _LIGACAO for p in palavras(t))]
    return max((relevancia_termo(titulo, t) for t in uteis), default=0.0)


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
