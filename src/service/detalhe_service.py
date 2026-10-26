"""Detalhe completo da vaga (descrição, requisitos, salário, benefícios).

O normal é a coleta já ter gravado o detalhe (a listagem do Remotar o traz). Quando `detalhada_em` é
nulo (vaga de antes da 004, ou do LinkedIn, cuja coleta não traz a descrição), o detalhe vem sob
demanda, por uma chamada à parte (`carregar`), nunca na página inteira. Cada plataforma usa o próprio
provider: Remotar pelo `remotar`, LinkedIn pelo `linkedin`.
"""

import copy
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
# Idem para o LinkedIn: o formato que o `ApifyLinkedInClient` grava (`www.linkedin.com/jobs/view/{id}`).
_URL_LINKEDIN = re.compile(r"https://www\.linkedin\.com/jobs/view/([0-9]{1,20})")

PLATAFORMA_REMOTAR = "Remotar"
# O `nome` do `ApifyLinkedInClient`, que é o que a coleta grava em `plataforma`.
PLATAFORMA_LINKEDIN = "LinkedIn (Apify)"


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


def id_linkedin(url: object) -> str | None:
    """O id (só dígitos) de uma URL de vaga do LinkedIn; None para qualquer outra coisa."""
    if not isinstance(url, str):
        return None
    achado = _URL_LINKEDIN.fullmatch(url)
    return achado.group(1) if achado else None


class DetalheService:
    """`abrir` nunca fala com o provider: lê a vaga e diz se falta carregar o detalhe.

    `carregar` (a chamada das seções) é o único que busca no provider (5 s) e só quando
    `detalhada_em` é nulo. Falha do provider vira aviso, nunca 500; 404 e `expired` gravam
    `expirada_em` (o cliente do LinkedIn nunca diz 404: tudo lá é `ErroProvider`). Sem provider da
    plataforma (testes) nada sai para a rede. Cada chamada ao repositório usa a sua
    conexão do pool, e nenhuma fica aberta enquanto espera o provider.
    """

    def __init__(
        self,
        vagas: VagaEncontradaRepository,
        remotar: ProviderDeDetalhe | None = None,
        agora=lambda: datetime.now(timezone.utc),
        linkedin: ProviderDeDetalhe | None = None,
    ):
        self.vagas = vagas
        self.remotar = remotar
        self.linkedin = linkedin
        self._agora = agora

    def para(self, vagas: VagaEncontradaRepository) -> "DetalheService":
        """O mesmo serviço (providers, relógio) lendo e gravando as vagas de outra pessoa."""
        copia = copy.copy(self)
        copia.vagas = vagas
        return copia

    def _fonte(self, vaga: VagaEncontrada) -> tuple[ProviderDeDetalhe, str] | None:
        """O provider da plataforma da vaga e o id que vai para ele; None se não há o que buscar."""
        if vaga.plataforma == PLATAFORMA_REMOTAR:
            provider, id = self.remotar, id_remotar(vaga.url)
        elif vaga.plataforma == PLATAFORMA_LINKEDIN:
            provider, id = self.linkedin, id_linkedin(vaga.url)
        else:
            return None
        return (provider, id) if provider is not None and id is not None else None

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
        fonte = self._fonte(vaga)
        if not aberta.carregar or fonte is None:
            return aberta
        provider, id = fonte
        try:
            detalhe = provider.detalhar(id)
        except VagaNaoEncontrada:
            self.vagas.marcar_expirada(vaga.url)
            return self._montar(self.vagas.obter(vaga.url) or vaga)
        except ErroProvider:
            return VagaAberta(vaga, expirada=aberta.expirada, aviso=AVISO_SEM_DETALHE)
        self.vagas.gravar_detalhe(vaga.url, detalhe)
        return self._montar(self.vagas.obter(vaga.url) or vaga)

    def _montar(self, vaga: VagaEncontrada) -> VagaAberta:
        expirada = vaga.expirada_em is not None and vaga.expirada_em <= self._agora()
        buscavel = self._fonte(vaga) is not None and vaga.detalhada_em is None and not expirada
        return VagaAberta(vaga, expirada=expirada, carregar=buscavel)
