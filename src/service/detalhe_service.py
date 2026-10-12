"""Detalhe completo da vaga (descrição, requisitos, salário, benefícios).

O normal é a coleta já ter gravado o detalhe (a listagem do Remotar o traz). Quando `detalhada_em` é
nulo (vaga de antes da 004, ou de outro provider), o detalhe vem sob demanda, por uma chamada à parte
(`carregar`), nunca na página inteira.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from src.client.remotar import ErroProvider, VagaNaoEncontrada
from src.model.detalhe_vaga import DetalheVaga
from src.model.vaga_encontrada import VagaEncontrada
from src.repository.base import VagaEncontradaRepository

AVISO_SEM_DETALHE = "Não foi possível carregar a descrição agora. Tente abrir a vaga de novo mais tarde."

# Só a URL no formato exato que a coleta grava (`remotar.com.br/job/{id}`, https, sem query nem
# fragmento): outro host ou outro caminho nunca chega ao provider.
_URL_REMOTAR = re.compile(r"https://remotar\.com\.br/job/([0-9]{1,12})")


class ProviderDeDetalhe(Protocol):
    def detalhar(self, id: str) -> DetalheVaga: ...


@dataclass
class VagaAberta:
    vaga: VagaEncontrada
    # `expirada_em <= agora`: o prazo já passou ou o provider tirou a vaga do ar.
    expirada: bool = False
    # O detalhe ainda não foi gravado e dá para buscar: a tela pede as seções por uma chamada à parte.
    carregar: bool = False
    # Texto para a tela quando o provider falhou; None quando deu certo ou não havia o que buscar.
    aviso: str | None = None


def id_remotar(url: object) -> str | None:
    """O id (só dígitos) de uma URL de vaga do Remotar; None para qualquer outra coisa."""
    if not isinstance(url, str):
        return None
    achado = _URL_REMOTAR.fullmatch(url)
    return achado.group(1) if achado else None


class DetalheService:
    """`abrir` nunca fala com o provider: lê a vaga e diz se falta carregar o detalhe.

    `carregar` (a chamada das seções) é o único que busca no provider (5 s) e só quando
    `detalhada_em` é nulo. Falha do provider vira aviso, nunca 500; 404 e `expired` gravam
    `expirada_em`. Sem `remotar` (testes) nada sai para a rede. Cada chamada ao repositório usa a sua
    conexão do pool, e nenhuma fica aberta enquanto espera o provider.
    """

    def __init__(
        self,
        vagas: VagaEncontradaRepository,
        remotar: ProviderDeDetalhe | None = None,
        agora=lambda: datetime.now(timezone.utc),
    ):
        self.vagas = vagas
        self.remotar = remotar
        self._agora = agora

    def abrir(self, url: str) -> VagaAberta | None:
        """A vaga pela URL, como está guardada, sem tocar no provider; None se não existe."""
        vaga = self.vagas.obter(url)
        return None if vaga is None else self._montar(vaga)

    def carregar(self, url: str) -> VagaAberta | None:
        """Busca o detalhe no provider se ainda faltar, grava e devolve a vaga atualizada."""
        vaga = self.vagas.obter(url)
        if vaga is None:
            return None
        aberta = self._montar(vaga)
        if not aberta.carregar:
            return aberta
        try:
            detalhe = self.remotar.detalhar(id_remotar(vaga.url))
        except VagaNaoEncontrada:
            self.vagas.marcar_expirada(vaga.url)
            return self._montar(self.vagas.obter(vaga.url) or vaga)
        except ErroProvider:
            return VagaAberta(vaga, expirada=aberta.expirada, aviso=AVISO_SEM_DETALHE)
        self.vagas.gravar_detalhe(vaga.url, detalhe)
        return self._montar(self.vagas.obter(vaga.url) or vaga)

    def _montar(self, vaga: VagaEncontrada) -> VagaAberta:
        expirada = vaga.expirada_em is not None and vaga.expirada_em <= self._agora()
        buscavel = (
            self.remotar is not None
            and vaga.plataforma == "Remotar"
            and id_remotar(vaga.url) is not None
            and vaga.detalhada_em is None
            and not expirada
        )
        return VagaAberta(vaga, expirada=expirada, carregar=buscavel)
