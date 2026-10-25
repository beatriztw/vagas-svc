"""API HTTP do vagas-svc. Sem autenticação: só é exposta na rede interna do Swarm."""

import logging
import os
import sys
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from src.client.armazenamento import Armazenamento
from src.controller.candidaturas import criar_router_candidaturas
from src.controller.ia_metricas import criar_router_ia_metricas
from src.controller.perfil_providers import criar_router_perfil_providers
from src.controller.pedidos_ia import criar_router_pedidos_ia
from src.controller.seguranca_json import exigir_json_mesma_origem
from src.controller.tela import criar_router_tela
from src.controller.tela_candidaturas import criar_router_tela_candidaturas
from src.controller.tela_ia import criar_router_tela_ia
from src.controller.tela_curriculos import criar_router_tela_curriculos
from src.controller.tela_perfil import criar_router_tela_perfil
from src.controller.tela_seguranca import DIRETORIO_STATIC, caminho_da_tela, eh_htmx, templates
from src.model.curriculo import Curriculo
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.model.vaga_encontrada import VagaEncontrada
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    MAX_DESLOCAMENTO_VAGAS,
    MAX_LIMITE_VAGAS,
    MAX_PUBLICADA_DESDE_DIAS,
    MAX_SALARIO_MINIMO,
    MAX_TAMANHO_BUSCA,
    CurriculoRepository,
    PerfilBuscaRepository,
    VagaEncontradaRepository,
    VagaEncontradaRepositoryMemoria,
    validar_perfil,
)
from src.repository.candidatura import CandidaturaRepository, CandidaturaRepositoryMemoria, vaga_por_url_em
from src.repository.pedido_ia import PedidoIARepository, PedidoIARepositoryMemoria
from src.repository.pessoa import PessoaRepository, PessoaRepositoryMemoria
from src.repository.pessoa_provider import PessoaProviderRepository, PessoaProviderRepositoryMemoria
from src.repository.trava import CHAVE_TRAVA_LOTE_IA, ColetaEmAndamento, TravaColeta, TravaColetaPostgres
from src.service.agendador_coleta import AgendadorColeta
from src.service.conferencia_service import ConferenciaDasPessoas, ConferenciaService
from src.service.busca_service import BuscaService
from src.service.candidatura_service import CandidaturaService
from src.service.coleta_service import ColetaDaPessoa, ColetaService, RepositoriosDaPessoa
from src.service.detalhe_service import DetalheService
from src.service.historico_service import HistoricoService
from src.service.pedido_ia_service import FabricasPorPessoa, PedidoIAService
from src.service.providers_pessoa import (
    ProvidersDaPessoa,
    limite_apify as _limite_apify,
    limite_posts_apify as _limite_posts_apify,
    lista_env as _lista_env,
    locais_do_ambiente,
    novo_remotar,
    token_apify as _token_apify,
)
from src.service.recomendacao_service import RecomendacaoService


# Em produção a pessoa é sempre esta (a dona dos dados de antes da migration 013). Os repositórios recebem a
# pessoa no construtor; quando a sessão por pessoa chegar, esta constante sai e a pessoa vem da requisição.
PESSOA_PADRAO = 1


class PerfilJson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    termos: list[str] = []
    excluir: list[str] = []
    modalidades: list[Modalidade] = []
    localidades: list[str] = []
    plataformas: list[str] = []
    niveis: list[Nivel] = []
    skills: list[str] = []
    publicada_desde_dias: int | None = Field(default=None, gt=0, le=MAX_PUBLICADA_DESDE_DIAS)
    ocultar_candidatadas: bool = True
    salario_minimo: int | None = Field(default=None, gt=0, le=MAX_SALARIO_MINIMO)

    @classmethod
    def de(cls, perfil: Perfil) -> "PerfilJson":
        return cls(**vars(perfil))

    def para_perfil(self) -> Perfil:
        return Perfil(**self.model_dump())


class CurriculoJson(BaseModel):
    id: int | None
    tipo: str
    titulo: str
    conteudo_md: str
    base_id: int | None
    vaga_url: str | None
    arquivo_s3: str | None
    criado_em: datetime | None

    @classmethod
    def de(cls, curriculo: Curriculo) -> "CurriculoJson":
        return cls(**vars(curriculo))


class NovaBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titulo: str = Field(min_length=1)
    conteudo_md: str = Field(min_length=1)
    arquivo_s3: str | None = None


class NovoAdaptado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titulo: str = Field(min_length=1)
    conteudo_md: str = Field(min_length=1)
    vaga_url: str = Field(min_length=1)
    base_id: int | None = None
    # O `id` do pedido de currículo que o worker está atendendo: o servidor grava na pessoa dona do pedido.
    # Sem ele vale a pessoa da requisição.
    pedido_id: StrictInt | None = None


class VagaJson(BaseModel):
    titulo: str
    empresa: str
    plataforma: str
    url: str
    local: str | None
    remoto: bool | None
    publicada_em: date | None

    @classmethod
    def de(cls, vaga: VagaExterna) -> "VagaJson":
        return cls(**vars(vaga))


class ResultadoJson(BaseModel):
    vagas: list[VagaJson]
    erros: dict[str, str]


class VagaEncontradaJson(BaseModel):
    url: str
    titulo: str
    empresa: str
    plataforma: str
    local: str | None
    remoto: bool | None
    publicada_em: date | None
    termos: list[str]
    primeira_vez_em: datetime | None
    ultima_vez_em: datetime | None
    vista_em: datetime | None

    @classmethod
    def de(cls, vaga: VagaEncontrada) -> "VagaEncontradaJson":
        return cls(**vars(vaga))


class ColetaDoTermoJson(BaseModel):
    termo: str
    encontradas: int
    novas: int
    erros: dict[str, str]


class ResultadoColetaJson(BaseModel):
    # Só a pessoa da requisição (hoje PESSOA_PADRAO), como sempre foi: termo e contagem de uma pessoa não saem
    # na resposta para outra. Cada pessoa vê o resultado das outras só nas contagens somadas do agendador e na CLI.
    termos: list[ColetaDoTermoJson]
    novas: int


def _fabricas_da_requisicao(perfis, curriculos, encontradas) -> FabricasPorPessoa:
    """Sem fábricas por pessoa (testes com uma pessoa só): os repositórios passados valem para a PESSOA_PADRAO e
    um pedido de outra pessoa falha alto, em vez de montar o payload com os dados da pessoa errada."""

    def so_a_padrao(repositorio):
        def fabrica(pessoa_id: int):
            if pessoa_id != PESSOA_PADRAO:
                raise RuntimeError(f"sem repositórios para a pessoa {pessoa_id}")
            return repositorio

        return fabrica

    return FabricasPorPessoa(so_a_padrao(perfis), so_a_padrao(curriculos), so_a_padrao(encontradas))


# Teto do corpo de POST /vagas/vistas: uma página de GET /vagas/encontradas por vez.
MAX_URLS_VISTAS = MAX_LIMITE_VAGAS


class VagasVistas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(max_length=MAX_URLS_VISTAS)


class VistasJson(BaseModel):
    marcadas: int


# Sem CSS nem JS inline: tudo vem de /static (o htmx roda com includeIndicatorStyles=false).
CSP = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"


def criar_app(
    perfis: PerfilBuscaRepository,
    curriculos: CurriculoRepository,
    busca: BuscaService,
    armazenamento: Armazenamento | None = None,
    encontradas: VagaEncontradaRepository | None = None,
    candidaturas: CandidaturaRepository | None = None,
    trava: TravaColeta | None = None,
    pedidos_ia: PedidoIARepository | None = None,
    trava_lote_ia: TravaColeta | None = None,
    detalhe: DetalheService | None = None,
    ao_subir: Callable[[], object] | None = None,
    agendador: Callable[[Callable[[], object]], "AgendadorColeta | None"] | None = None,
    pessoas: PessoaRepository | None = None,
    repositorios_da_pessoa: Callable[[int], RepositoriosDaPessoa] | None = None,
    providers_da_pessoa: Callable[[int], PessoaProviderRepository] | None = None,
    fabricas: FabricasPorPessoa | None = None,
    conferencia: Callable[[VagaEncontradaRepository], ConferenciaService] | None = None,
) -> FastAPI:
    """Sem `encontradas` e `candidaturas`, o histórico da coleta e as candidaturas ficam em memória (testes);
    a produção passa os do Postgres. Sem `detalhe`, a tela não busca o detalhe no provider (testes).
    `ao_subir` roda no startup (lifespan); se levantar, a app não sobe. `agendador` recebe a coleta
    (a mesma do POST /coletas) e devolve o agendador da coleta, que o lifespan inicia depois do
    `ao_subir` e para no shutdown; sem ele (testes), nada é agendado. `conferencia` recebe o histórico de uma
    pessoa e devolve a conferência na fonte, que roda (em segundo plano, pessoa por pessoa) quando a app sobe e
    depois de cada coleta agendada; sem ela (testes), nenhuma vaga é reconsultada. A coleta roda para cada pessoa
    de `pessoas` com os repositórios de `repositorios_da_pessoa` (a produção passa os do Postgres, um por chamada);
    sem eles (testes) a única pessoa é a PESSOA_PADRAO, com `perfis` e `encontradas`. `providers_da_pessoa` dá o
    repositório das fontes de vaga de cada pessoa (sem ele, em memória); pessoa sem fontes usa `busca`, as do
    ambiente. `fabricas` dão os repositórios da
    pessoa dona de cada pedido da fila do worker (a fila é de todas as pessoas); sem elas só existe a pessoa da
    requisição, e um pedido de outra pessoa é erro em vez de ler os dados da errada."""

    @asynccontextmanager
    async def ciclo_de_vida(app_: FastAPI):
        if ao_subir is not None:
            ao_subir()
        agendado = app_.state.agendador
        if agendado is not None:
            agendado.iniciar()
        if app_.state.conferencia is not None:
            # Na subida também: um deploy já limpa as vagas que fecharam desde a última conferência.
            app_.state.conferencia.conferir_em_segundo_plano()
        try:
            yield
        finally:
            if agendado is not None:
                agendado.parar()

    # Dependência do app: rota mutável nova (JSON) já nasce protegida; /tela e /interno ficam de fora dentro dela.
    app = FastAPI(title="vagas-svc", lifespan=ciclo_de_vida, dependencies=[Depends(exigir_json_mesma_origem)])
    app.state.armazenamento = armazenamento
    recomendacao = RecomendacaoService(busca)
    encontradas = encontradas if encontradas is not None else VagaEncontradaRepositoryMemoria(PESSOA_PADRAO)
    # Lista do histórico (JSON e tela) com o perfil salvo aplicado na leitura.
    historico = HistoricoService(encontradas, perfis)
    if pedidos_ia is None:
        pedidos_ia = PedidoIARepositoryMemoria(PESSOA_PADRAO, vaga_por_url_em(encontradas))
    # Um serviço só para as rotas do worker, as da tela e as métricas.
    # Com `detalhe`, o pedido de currículo busca antes a descrição que faltar (LinkedIn sob demanda).
    if fabricas is None:
        fabricas = _fabricas_da_requisicao(perfis, curriculos, encontradas)
    servico_ia = PedidoIAService(
        pedidos_ia, encontradas, perfis, curriculos, trava_lote=trava_lote_ia, detalhe=detalhe, fabricas=fabricas
    )
    # Toda coleta (API, tela, CLI) termina mandando, para cada pessoa coletada, as vagas dela sem nota para a fila da
    # IA em lote. Falha de uma pessoa só vai para o log (ColetaService).
    def enfileirar_lote_da_pessoa(coletada: ColetaDaPessoa) -> int:
        if repositorios_da_pessoa is None:
            ia_da_pessoa, historico_da_pessoa = servico_ia, historico
        else:
            repos = repositorios_da_pessoa(coletada.pessoa_id)
            ia_da_pessoa = PedidoIAService(
                repos.pedidos_ia, repos.vagas, repos.perfis, repos.curriculos, trava_lote=trava_lote_ia, detalhe=detalhe,
                fabricas=fabricas,
            )
            historico_da_pessoa = HistoricoService(repos.vagas, repos.perfis)
        return ia_da_pessoa.enfileirar_lote(historico_da_pessoa.visiveis())

    if providers_da_pessoa is None:
        fontes_em_memoria = PessoaProviderRepositoryMemoria(PESSOA_PADRAO)
        providers_da_pessoa = lambda pessoa_id: PessoaProviderRepositoryMemoria(pessoa_id, fontes_em_memoria)  # noqa: E731
    buscas = ProvidersDaPessoa(busca, providers_da_pessoa)

    coleta = ColetaService(
        recomendacao,
        pessoas if pessoas is not None else PessoaRepositoryMemoria([PESSOA_PADRAO]),
        repositorios_da_pessoa or (lambda _: RepositoriosDaPessoa(perfis, encontradas)),
        trava,
        ao_terminar=enfileirar_lote_da_pessoa,
        buscas=buscas,
    )
    pessoas_da_coleta = pessoas if pessoas is not None else PessoaRepositoryMemoria([PESSOA_PADRAO])
    if conferencia is None:
        app.state.conferencia = None
    else:
        # Cada pessoa tem o próprio histórico: a conferência roda sobre o de cada uma.
        app.state.conferencia = ConferenciaDasPessoas(
            pessoas_da_coleta.listar,
            lambda pessoa_id: conferencia(
                repositorios_da_pessoa(pessoa_id).vagas if repositorios_da_pessoa is not None else encontradas
            ),
        )

    def coletar_e_conferir():
        """A coleta agendada e, depois dela (mesmo se falhar), a conferência das vagas no ar."""
        try:
            return coleta.coletar()
        finally:
            if app.state.conferencia is not None:
                app.state.conferencia.conferir_em_segundo_plano()

    app.state.agendador = agendador(coletar_e_conferir) if agendador is not None else None
    if candidaturas is None:
        candidaturas = CandidaturaRepositoryMemoria(PESSOA_PADRAO, vaga_por_url_em(encontradas))
    # Um serviço só, para as rotas JSON e as da tela (na produção, o repositório é o _PorChamada).
    servico_candidaturas = CandidaturaService(candidaturas, encontradas)
    # Só para os testes alcançarem o serviço; as rotas recebem o serviço explicitamente.
    app.state.candidaturas = servico_candidaturas
    app.include_router(criar_router_candidaturas(servico_candidaturas))
    app.state.pedido_ia_service = servico_ia
    app.include_router(criar_router_pedidos_ia(app.state.pedido_ia_service))
    app.include_router(criar_router_ia_metricas(pedidos_ia))

    def erro_da_tela(request: Request, mensagem: str, status: int):
        """A tela troca HTML: o erro volta como HTML, não como JSON."""
        contexto = {"mensagem": mensagem}
        if eh_htmx(request):
            # O erro seria trocado no alvo de quem chamou (uma linha de tabela, por exemplo): o
            # servidor redireciona a troca para o aviso da página.
            return templates.TemplateResponse(
                request,
                "tela/_erro.html",
                contexto,
                status_code=status,
                headers={"HX-Retarget": "#aviso", "HX-Reswap": "outerHTML"},
            )
        return templates.TemplateResponse(request, "tela/erro.html", contexto, status_code=status)

    @app.exception_handler(DadoInvalido)
    def valor_invalido(request: Request, erro: DadoInvalido):
        # Só a validação do domínio vira 422; um ValueError qualquer (PGPORT inválido, dado
        # inesperado do banco) é falha do servidor e sai como 500.
        if caminho_da_tela(request.url.path):
            return erro_da_tela(request, str(erro), 422)
        return JSONResponse(status_code=422, content={"detail": str(erro)})

    @app.middleware("http")
    async def cabecalhos_da_tela(request: Request, chamar):
        resposta = await chamar(request)
        caminho = request.url.path
        if caminho_da_tela(caminho) or caminho == "/static" or caminho.startswith("/static/"):
            resposta.headers["Content-Security-Policy"] = CSP
            resposta.headers["X-Content-Type-Options"] = "nosniff"
            resposta.headers["Referrer-Policy"] = "same-origin"
        if caminho_da_tela(caminho):
            resposta.headers["Cache-Control"] = "no-store"
        return resposta

    app.mount("/static", StaticFiles(directory=DIRETORIO_STATIC), name="static")
    app.include_router(criar_router_tela(encontradas, historico, coleta, servico_candidaturas, detalhe, servico_ia, PESSOA_PADRAO))
    app.include_router(criar_router_tela_candidaturas(servico_candidaturas))
    app.include_router(criar_router_tela_ia(app.state.pedido_ia_service))
    app.include_router(criar_router_tela_perfil(perfis, providers_da_pessoa(PESSOA_PADRAO)))
    app.include_router(criar_router_perfil_providers(providers_da_pessoa(PESSOA_PADRAO)))
    app.include_router(criar_router_tela_curriculos(curriculos))

    @app.get("/", include_in_schema=False)
    def raiz() -> RedirectResponse:
        return RedirectResponse("/tela", status_code=307)

    @app.exception_handler(ColetaEmAndamento)
    def coleta_em_andamento(request: Request, erro: ColetaEmAndamento):
        # Conflito, não validação nem falha: quem chamou tenta de novo quando a outra terminar.
        if caminho_da_tela(request.url.path):
            return erro_da_tela(request, str(erro), 409)
        return JSONResponse(status_code=409, content={"detail": str(erro)})

    @app.get("/saude")
    def saude() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/perfil")
    def obter_perfil() -> PerfilJson:
        perfil = perfis.obter()
        if perfil is None:
            raise HTTPException(404, "Nenhum perfil salvo")
        return PerfilJson.de(perfil)

    @app.put("/perfil")
    def salvar_perfil(corpo: PerfilJson) -> PerfilJson:
        # Valida aqui também: um repositório que não valide ainda devolve 422, não 500.
        perfis.salvar(validar_perfil(corpo.para_perfil()))
        return PerfilJson.de(perfis.obter())

    @app.get("/curriculos/base")
    def obter_base() -> CurriculoJson:
        base = curriculos.obter_base()
        if base is None:
            raise HTTPException(404, "Nenhum currículo base salvo")
        return CurriculoJson.de(base)

    @app.put("/curriculos/base")
    def nova_base(corpo: NovaBase) -> CurriculoJson:
        salvo = curriculos.salvar(Curriculo(tipo="base", **corpo.model_dump()))
        return CurriculoJson.de(salvo)

    @app.get("/curriculos/adaptados")
    def listar_adaptados() -> list[CurriculoJson]:
        return [CurriculoJson.de(c) for c in curriculos.listar_adaptados()]

    @app.post("/curriculos/adaptados", status_code=201)
    def novo_adaptado(corpo: NovoAdaptado) -> CurriculoJson:
        # Do worker (com `pedido_id`): a pessoa é a dona do pedido. Sem ele: a da requisição.
        curriculos_da_pessoa = (
            curriculos
            if corpo.pedido_id is None
            else servico_ia.curriculos_do_pedido(corpo.pedido_id, corpo.vaga_url)
        )
        if corpo.base_id is None:
            base = curriculos_da_pessoa.obter_base()
            if base is None:
                raise HTTPException(409, "Não há currículo base para adaptar")
        else:
            base = curriculos_da_pessoa.obter(corpo.base_id)
            if base is None or base.tipo != "base":
                raise HTTPException(404, f"Currículo base {corpo.base_id} não encontrado")
        salvo = curriculos_da_pessoa.salvar(
            Curriculo(
                tipo="adaptado",
                titulo=corpo.titulo,
                conteudo_md=corpo.conteudo_md,
                base_id=base.id,
                vaga_url=corpo.vaga_url,
            )
        )
        return CurriculoJson.de(salvo)

    @app.get("/vagas")
    def buscar_vagas(termo: str = Query(min_length=1)) -> ResultadoJson:
        # Os providers da pessoa (ou os do ambiente, se ela não configurou nenhum).
        _, busca_da_pessoa = buscas.busca(PESSOA_PADRAO)
        resultado = RecomendacaoService(busca_da_pessoa).recomendar(termo, perfis.obter())
        return ResultadoJson(vagas=[VagaJson.de(v) for v in resultado.vagas], erros=resultado.erros)

    @app.post("/coletas")
    def coletar() -> ResultadoColetaJson:
        # Sem perfil ou sem termos: DadoInvalido do serviço, 422; outra coleta rodando: 409.
        resultado = coleta.coletar()
        da_pessoa = resultado.da_pessoa(PESSOA_PADRAO)
        return ResultadoColetaJson(
            termos=[ColetaDoTermoJson(**vars(t)) for t in da_pessoa.termos],
            novas=da_pessoa.novas,
        )

    @app.get("/vagas/encontradas")
    def listar_encontradas(
        novas: bool = True,
        limite: int = Query(default=50, ge=1, le=MAX_LIMITE_VAGAS),
        q: str | None = Query(default=None, max_length=MAX_TAMANHO_BUSCA),
        deslocamento: int = Query(default=0, ge=0, le=MAX_DESLOCAMENTO_VAGAS),
    ) -> list[VagaEncontradaJson]:
        vagas = historico.listar(somente_novas=novas, limite=limite, q=q, deslocamento=deslocamento)
        return [VagaEncontradaJson.de(v) for v in vagas]

    @app.post("/vagas/vistas")
    def marcar_vistas(corpo: VagasVistas) -> VistasJson:
        return VistasJson(marcadas=encontradas.marcar_vistas(corpo.urls))

    return app


class _PorChamada:
    """Cada chamada usa um repository novo, com uma conexão do pool só para ela.

    Uma conexão compartilhada entre as threads do FastAPI misturaria as transações de
    requisições simultâneas: o segundo `transaction()` vira SAVEPOINT do primeiro, e o
    rollback de um desfaz a gravação do outro, que já respondeu 201.

    `pessoa_id` é a pessoa dos dados do repositório; sem ele (o registro da coleta agendada, que é do app),
    o repositório só recebe a conexão.
    """

    def __init__(self, pool: Callable[[], Any], classe: Callable[[], type], pessoa_id: int | None = None):
        self._pool = pool
        # A classe vem por função para o import do psycopg ficar para o primeiro uso.
        self._classe = classe
        self._pessoa_id = pessoa_id

    def __getattr__(self, nome: str) -> Any:
        def chamar(*args, **kwargs):
            with self._pool().connection() as conn:
                repositorio = self._classe()(conn) if self._pessoa_id is None else self._classe()(conn, self._pessoa_id)
                return getattr(repositorio, nome)(*args, **kwargs)

        return chamar


class _PoolPreguicoso:
    """Cria o pool no primeiro uso: a app sobe e responde /saude sem o Postgres no ar."""

    def __init__(self):
        self._pool = None
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            if self._pool is None:
                from psycopg_pool import ConnectionPool

                from src.repository.conexao import parametros

                self._pool = ConnectionPool(
                    kwargs={**parametros(), "autocommit": True},
                    min_size=1,
                    max_size=4,
                    # Conexão que caiu é trocada antes de ser entregue a uma requisição.
                    check=ConnectionPool.check_connection,
                    open=True,
                )
            return self._pool


def configurar_log() -> None:
    """Liga o log da app (logger `src`) em INFO, ou no nível de `LOG_NIVEL`, numa linha por evento no stderr.

    Sem isso o root fica em WARNING e somem os `log.info` (migrations, coleta agendada, pedidos de IA). Só o
    `src`, nunca o root: em INFO o httpx loga a URL de cada requisição, e a do Apify leva o token. `LOG_NIVEL`
    inválido cai em INFO. Chamar de novo não duplica o handler."""
    nivel = logging.getLevelName(os.environ.get("LOG_NIVEL", "INFO").strip().upper())
    logger = logging.getLogger("src")
    logger.setLevel(nivel if isinstance(nivel, int) else logging.INFO)
    if not any(getattr(h, "_da_app", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
        handler._da_app = True
        logger.addHandler(handler)
    logger.propagate = False


def _providers_latam() -> list:
    """LATAM_PROVIDERS: ausente ou vazia = Himalayas e Remotive; `nenhum` = nenhum; senão a lista separada
    por vírgula (`himalayas`, `remotive`, sem caixa nem acento). Nome desconhecido = valor inválido, que cai
    no padrão (os dois) sem derrubar a app."""
    from src.client.base import normalizar
    from src.client.himalayas import HimalayasClient
    from src.client.remotive import RemotiveClient

    classes = {"himalayas": HimalayasClient, "remotive": RemotiveClient}
    nomes = [normalizar(n) for n in _lista_env("LATAM_PROVIDERS")]
    if nomes == ["nenhum"]:
        return []
    if not nomes or any(n not in classes for n in nomes):
        if nomes:
            logging.getLogger(__name__).warning("LATAM_PROVIDERS inválida (%s): usando Himalayas e Remotive", os.environ.get("LATAM_PROVIDERS"))
        nomes = list(classes)
    return [classes[n]() for n in dict.fromkeys(nomes)]


def providers_padrao() -> list:
    """Remotar e Trampos sempre; Himalayas e Remotive (vagas remotas abertas ao Brasil/América Latina) por
    padrão, com opt-out por LATAM_PROVIDERS; Greenhouse, Lever e Ashby só com empresas em
    GREENHOUSE_EMPRESAS etc.; o LinkedIn pelo Apify só com APIFY_TOKEN ou APIFY_TOKEN_FILE; os posts do
    LinkedIn só com o token e APIFY_POSTS_LIMITE >= 1."""
    from src.client.ashby import AshbyClient
    from src.client.greenhouse import GreenhouseClient
    from src.client.lever import LeverClient
    from src.client.trampos import TramposClient

    providers = [novo_remotar(), TramposClient()]
    providers.extend(_providers_latam())
    for classe, env in ((GreenhouseClient, "GREENHOUSE_EMPRESAS"), (LeverClient, "LEVER_EMPRESAS"), (AshbyClient, "ASHBY_EMPRESAS")):
        if empresas := _lista_env(env):
            providers.append(classe(empresas))
    if token := _token_apify():
        from src.client.apify_linkedin import ApifyLinkedInClient

        # Cada busca custa até APIFY_LIMITE vagas por local no Apify (US$ 0,002 cada em out/2026).
        providers.append(
            # APIFY_LOCAIS, ou a antiga APIFY_LOCAL (um local só, inteira: "São Paulo, SP"), vale como foi configurada;
            # as duas vazias caem no padrão (Brasil e América Latina, em inglês, como o LinkedIn
            # entende): sem local, a busca paga traria vagas do mundo todo.
            ApifyLinkedInClient(
                locais=locais_do_ambiente(),
                limite=_limite_apify(),
                token=token,
            )
        )
        if limite_posts := _limite_posts_apify():
            from src.client.apify_linkedin_posts import ApifyLinkedInPostsClient

            # Cada busca custa até APIFY_POSTS_LIMITE posts por termo (US$ 0,002 cada em out/2026).
            providers.append(ApifyLinkedInPostsClient(limite=limite_posts, token=token))
    return providers


def _perfis_postgres() -> type:
    from src.repository.postgres import PerfilBuscaRepositoryPostgres

    return PerfilBuscaRepositoryPostgres


def _curriculos_postgres() -> type:
    from src.repository.postgres import CurriculoRepositoryPostgres

    return CurriculoRepositoryPostgres


def _encontradas_postgres() -> type:
    from src.repository.postgres import VagaEncontradaRepositoryPostgres

    return VagaEncontradaRepositoryPostgres


def _candidaturas_postgres() -> type:
    from src.repository.candidatura_postgres import CandidaturaRepositoryPostgres

    return CandidaturaRepositoryPostgres


def _pedidos_ia_postgres() -> type:
    from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres

    return PedidoIARepositoryPostgres


def _pessoas_postgres() -> type:
    from src.repository.pessoa_postgres import PessoaRepositoryPostgres

    return PessoaRepositoryPostgres


def _providers_postgres() -> type:
    from src.repository.pessoa_provider_postgres import PessoaProviderRepositoryPostgres

    return PessoaProviderRepositoryPostgres


def _coleta_agendada_postgres() -> type:
    from src.repository.coleta_agendada import RegistroColetaAgendadaPostgres

    return RegistroColetaAgendadaPostgres


def _migrar() -> list[str]:
    from src.repository.migracoes import aplicar_com_retry

    return aplicar_com_retry()


def criar_app_producao() -> FastAPI:
    from src.client.gupy import GupyClient
    from src.client.linkedin_detalhe import LinkedInDetalheClient
    from src.client.remotar import RemotarClient

    configurar_log()

    pool = _PoolPreguicoso()
    encontradas = _PorChamada(pool, _encontradas_postgres, PESSOA_PADRAO)

    def repositorios_da_pessoa(pessoa_id: int) -> RepositoriosDaPessoa:
        # Repositórios novos por pessoa, cada chamada com a própria conexão do pool.
        return RepositoriosDaPessoa(
            perfis=_PorChamada(pool, _perfis_postgres, pessoa_id),
            vagas=_PorChamada(pool, _encontradas_postgres, pessoa_id),
            curriculos=_PorChamada(pool, _curriculos_postgres, pessoa_id),
            pedidos_ia=_PorChamada(pool, _pedidos_ia_postgres, pessoa_id),
        )

    # Pedido do worker: os repositórios da pessoa do pedido, também uma conexão do pool por chamada.
    fabricas = FabricasPorPessoa(
        perfis=lambda pessoa_id: _PorChamada(pool, _perfis_postgres, pessoa_id),
        curriculos=lambda pessoa_id: _PorChamada(pool, _curriculos_postgres, pessoa_id),
        encontradas=lambda pessoa_id: _PorChamada(pool, _encontradas_postgres, pessoa_id),
    )
    return criar_app(
        perfis=_PorChamada(pool, _perfis_postgres, PESSOA_PADRAO),
        curriculos=_PorChamada(pool, _curriculos_postgres, PESSOA_PADRAO),
        busca=BuscaService(providers_padrao()),
        armazenamento=Armazenamento(),
        encontradas=encontradas,
        candidaturas=_PorChamada(pool, _candidaturas_postgres, PESSOA_PADRAO),
        trava=TravaColetaPostgres(),
        pedidos_ia=_PorChamada(pool, _pedidos_ia_postgres, PESSOA_PADRAO),
        trava_lote_ia=TravaColetaPostgres(chave=CHAVE_TRAVA_LOTE_IA),
        fabricas=fabricas,
        # LinkedIn: a coleta (Apify) não traz a descrição; a página pública da vaga dá, uma requisição
        # por vaga aberta sem descrição.
        detalhe=DetalheService(
            encontradas, RemotarClient(gupy=GupyClient().encerramento), linkedin=LinkedInDetalheClient()
        ),
        # Aplica migrations/*.sql pendentes antes de atender; banco fora além do teto ou migration com erro = não sobe.
        ao_subir=_migrar,
        # Coleta às 08:00 e 18:00 de Brasília (COLETA_HORARIOS; vazia ou inválida desliga).
        agendador=lambda coletar: AgendadorColeta.da_config(coletar, _PorChamada(pool, _coleta_agendada_postgres)),
        # A coleta roda para todas as pessoas da tabela `pessoa`; o resto do app segue na PESSOA_PADRAO.
        pessoas=_PorChamada(pool, _pessoas_postgres),
        repositorios_da_pessoa=repositorios_da_pessoa,
        providers_da_pessoa=lambda pessoa_id: _PorChamada(pool, _providers_postgres, pessoa_id),
        # Reconsulta na fonte (Remotar e LinkedIn) as vagas no ar: a encerrada some da busca e a coleta não vê.
        conferencia=lambda historico: ConferenciaService(
            historico, RemotarClient(gupy=GupyClient().encerramento), LinkedInDetalheClient()
        ),
    )


app = criar_app_producao()
