"""Os providers de cada pessoa: o que vem do ambiente do servidor e o que a pessoa configurou.

Pessoa sem nenhum provider na tabela usa o ambiente (`providers_padrao`, o comportamento de antes). Com
providers na tabela, só valem os dela. Segredos (APIFY_TOKEN) e tetos de custo vêm sempre do ambiente.
"""

import logging
import os
import threading
from collections.abc import Callable, Hashable
from pathlib import Path

from src.client.base import ProviderClient, normalizar
from src.model.erros import DadoInvalido
from src.model.provider_pessoa import (
    APIFY_LIMITE_PADRAO,
    ProviderPessoa,
    chave_da_config,
    limite_apify_max,
    limite_posts_max,
    validar_provider,
)
from src.repository.pessoa_provider import PessoaProviderRepository
from src.service.busca_service import BuscaService

log = logging.getLogger(__name__)

# Chave de cache da coleta para quem usa o ambiente (ver `ProvidersDaPessoa.busca`).
PROVIDERS_DO_AMBIENTE = "ambiente"
LIMITE_POSTS_PADRAO_DA_PESSOA = 1


# --- Ambiente (antes em api.py) ---------------------------------------------------------------------------

def lista_env(nome: str) -> list[str]:
    return [p.strip() for p in os.environ.get(nome, "").split(",") if p.strip()]


def limite_apify() -> int:
    """APIFY_LIMITE inválido ou não positivo cai no padrão: um provider opcional não derruba a app."""
    try:
        limite = int(os.environ.get("APIFY_LIMITE", APIFY_LIMITE_PADRAO))
    except ValueError:
        return APIFY_LIMITE_PADRAO
    return limite if limite > 0 else APIFY_LIMITE_PADRAO


def local_antigo_apify() -> list[str]:
    """APIFY_LOCAL, anterior à APIFY_LOCAIS, era um local só e pode ter vírgula ("São Paulo, SP")."""
    return [local] if (local := os.environ.get("APIFY_LOCAL", "").strip()) else []


def locais_do_ambiente() -> list[str]:
    """APIFY_LOCAIS, ou a antiga APIFY_LOCAL (um local só, inteira), ou o padrão (em inglês, como o LinkedIn
    entende): sem local, a busca paga traria vagas do mundo todo."""
    from src.client.apify_linkedin import LOCAIS_PADRAO

    return lista_env("APIFY_LOCAIS") or local_antigo_apify() or list(LOCAIS_PADRAO)


def token_apify() -> str | None:
    """APIFY_TOKEN ou o conteúdo do arquivo em APIFY_TOKEN_FILE (secret do Swarm). Arquivo ausente,
    ilegível ou vazio = sem token: o LinkedIn fica desligado e a app sobe igual."""
    if token := os.environ.get("APIFY_TOKEN", "").strip():
        return token
    arquivo = os.environ.get("APIFY_TOKEN_FILE", "").strip()
    if not arquivo:
        return None
    try:
        return Path(arquivo).read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def limite_posts_apify() -> int:
    """APIFY_POSTS_LIMITE (posts por termo). Ausente, inválido ou não positivo = 0, desligado: o provider é
    pago e só gasta com uma decisão explícita; valor torto não derruba a app."""
    try:
        limite = int(os.environ.get("APIFY_POSTS_LIMITE", "0"))
    except ValueError:
        return 0
    return max(limite, 0)


def novo_remotar() -> ProviderClient:
    from src.client.gupy import GupyClient
    from src.client.remotar import RemotarClient

    # A vaga do Remotar que fechou no Gupy (onde a candidatura acontece) sai como expirada.
    return RemotarClient(gupy=GupyClient().encerramento)


# --- Config da pessoa -> clients ----------------------------------------------------------------------------

def _local_normalizado(local: str) -> str:
    from src.client.apify_linkedin import _local_do_linkedin

    return _local_do_linkedin(local).strip()


def _efetiva(item: ProviderPessoa) -> dict:
    """A config com os padrões preenchidos (campo ausente = o do ambiente, nunca acima do teto), na forma que
    decide o que é buscado: duas configs que buscam o mesmo dão a mesma chave de cache (e o Apify não é pago
    duas vezes)."""
    nome, config = item.provider, item.config
    if nome in ("greenhouse", "lever", "ashby"):
        return {"empresas": sorted(config["empresas"], key=str.casefold)}
    if nome == "himalayas":
        from src.client.himalayas import LIMITE_PADRAO

        return {"limite": config.get("limite", LIMITE_PADRAO)}
    if nome == "remotive":
        from src.client.remotive import LIMITE_PADRAO

        return {"limite": config.get("limite", LIMITE_PADRAO)}
    if nome == "linkedin":
        locais = config.get("locais") or locais_do_ambiente()
        # Pelo texto como o client usa (Brasil = Brazil), sem repetidos nem ordem: o custo é o mesmo.
        unicos = {normalizar(_local_normalizado(local)): _local_normalizado(local) for local in locais}
        return {
            "locais": [unicos[k] for k in sorted(unicos) if k],
            "limite": min(config.get("limite", limite_apify()), limite_apify_max()),
        }
    if nome == "linkedin_posts":
        return {"limite": min(config.get("limite", LIMITE_POSTS_PADRAO_DA_PESSOA), limite_posts_max())}
    return {}


def _criar_cliente(nome: str, efetiva: dict) -> ProviderClient:
    if nome == "remotar":
        return novo_remotar()
    if nome == "trampos":
        from src.client.trampos import TramposClient

        return TramposClient()
    if nome == "himalayas":
        from src.client.himalayas import HimalayasClient

        return HimalayasClient(limite=efetiva["limite"])
    if nome == "remotive":
        from src.client.remotive import RemotiveClient

        return RemotiveClient(limite=efetiva["limite"])
    if nome in ("greenhouse", "lever", "ashby"):
        from src.client.ashby import AshbyClient
        from src.client.greenhouse import GreenhouseClient
        from src.client.lever import LeverClient

        classe = {"greenhouse": GreenhouseClient, "lever": LeverClient, "ashby": AshbyClient}[nome]
        return classe(list(efetiva["empresas"]))
    # Sem token o client é criado igual e a busca falha ("APIFY_TOKEN não definido"): aparece em `erros`.
    token = token_apify()
    if nome == "linkedin":
        from src.client.apify_linkedin import ApifyLinkedInClient

        return ApifyLinkedInClient(locais=efetiva["locais"], limite=efetiva["limite"], token=token)
    from src.client.apify_linkedin_posts import ApifyLinkedInPostsClient

    return ApifyLinkedInPostsClient(limite=efetiva["limite"], token=token)


def _valido_para_uso(item: ProviderPessoa) -> ProviderPessoa | None:
    """Revalida o que veio do banco (alguém pode ter gravado por fora, ou o teto ter baixado). `limite` acima do
    teto atual é cortado no teto, nunca ultrapassado; o resto inválido sai, sem derrubar a busca das outras."""
    config = dict(item.config) if isinstance(item.config, dict) else item.config
    if isinstance(config, dict):
        teto = {"linkedin": limite_apify_max(), "linkedin_posts": limite_posts_max()}.get(item.provider)
        limite = config.get("limite")
        if teto is not None and isinstance(limite, int) and not isinstance(limite, bool) and limite > teto:
            config["limite"] = teto
    try:
        return validar_provider(ProviderPessoa(item.provider, config))
    except DadoInvalido:
        log.warning("provider %s da pessoa ignorado: config inválida", str(item.provider)[:40])
        return None


class ProvidersDaPessoa:
    """Monta a `BuscaService` de cada pessoa a partir da config dela; sem config, a do ambiente."""

    # Clients com estado ficam guardados por (provider, config): o Remotive guarda a listagem por 6 h e
    # espera 60 s entre chamadas (termos de uso), e isso se perderia se cada coleta criasse um client novo.
    # Os do Apify não entram: o token é lido na hora, e `descartes` do client de posts é estado de uma busca só.
    _COM_ESTADO = ("remotar", "remotive")
    _MAX_CLIENTES = 256

    def __init__(self, ambiente: BuscaService, repositorio: Callable[[int], PessoaProviderRepository]):
        self._ambiente = ambiente
        # Uma função, não um objeto: na produção cada chamada pega a própria conexão do pool.
        self._repositorio = repositorio
        self._clientes: dict[tuple[str, tuple], ProviderClient] = {}
        self._lock = threading.Lock()

    def busca(self, pessoa_id: int) -> tuple[Hashable, BuscaService]:
        """`(chave, busca)` da pessoa. A chave é `PROVIDERS_DO_AMBIENTE` quando vale o ambiente; senão uma tupla
        ordenada das configs efetivas, igual para configs iguais (a coleta reaproveita a busca do termo só entre
        quem tem a mesma chave)."""
        gravados = self._repositorio(pessoa_id).listar()
        if not gravados:
            return PROVIDERS_DO_AMBIENTE, self._ambiente
        itens = [v for item in gravados if (v := _valido_para_uso(item)) is not None]
        efetivas = [(item.provider, _efetiva(item)) for item in itens]
        chave = chave_da_config(efetivas)
        return chave, BuscaService([self._cliente(nome, efetiva) for nome, efetiva in efetivas])

    def _cliente(self, nome: str, efetiva: dict) -> ProviderClient:
        if nome not in self._COM_ESTADO:
            return _criar_cliente(nome, efetiva)
        chave = (nome, chave_da_config([(nome, efetiva)]))
        with self._lock:
            if chave not in self._clientes:
                if len(self._clientes) >= self._MAX_CLIENTES:
                    self._clientes.clear()
                self._clientes[chave] = _criar_cliente(nome, efetiva)
            return self._clientes[chave]
