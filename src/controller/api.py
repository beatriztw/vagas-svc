"""API HTTP do vagas-svc. Sem autenticação: só é exposta na rede interna do Swarm."""

import os
import threading
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from src.client.armazenamento import Armazenamento
from src.controller.tela import criar_router_tela
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
    MAX_TAMANHO_BUSCA,
    CurriculoRepository,
    PerfilBuscaRepository,
    VagaEncontradaRepository,
    VagaEncontradaRepositoryMemoria,
    validar_perfil,
)
from src.service.busca_service import BuscaService
from src.service.coleta_service import ColetaService
from src.service.recomendacao_service import RecomendacaoService


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
    termos: list[ColetaDoTermoJson]
    novas: int


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
) -> FastAPI:
    """Sem `encontradas`, o histórico da coleta fica em memória (testes); a produção passa o do Postgres."""
    app = FastAPI(title="vagas-svc")
    app.state.armazenamento = armazenamento
    recomendacao = RecomendacaoService(busca)
    encontradas = encontradas if encontradas is not None else VagaEncontradaRepositoryMemoria()
    coleta = ColetaService(recomendacao, perfis, encontradas)

    @app.exception_handler(DadoInvalido)
    def valor_invalido(request: Request, erro: DadoInvalido):
        # Só a validação do domínio vira 422; um ValueError qualquer (PGPORT inválido, dado
        # inesperado do banco) é falha do servidor e sai como 500.
        if caminho_da_tela(request.url.path):
            # A tela troca HTML: o erro volta como HTML, não como JSON.
            contexto = {"mensagem": str(erro)}
            if eh_htmx(request):
                # O 422 é trocado no alvo de quem chamou (uma linha de tabela, por exemplo): o
                # servidor redireciona a troca para o aviso da página.
                return templates.TemplateResponse(
                    request,
                    "tela/_erro.html",
                    contexto,
                    status_code=422,
                    headers={"HX-Retarget": "#aviso", "HX-Reswap": "outerHTML"},
                )
            return templates.TemplateResponse(request, "tela/erro.html", contexto, status_code=422)
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
    app.include_router(criar_router_tela(encontradas))

    @app.get("/", include_in_schema=False)
    def raiz() -> RedirectResponse:
        return RedirectResponse("/tela", status_code=307)

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
        if corpo.base_id is None:
            base = curriculos.obter_base()
            if base is None:
                raise HTTPException(409, "Não há currículo base para adaptar")
        else:
            base = curriculos.obter(corpo.base_id)
            if base is None or base.tipo != "base":
                raise HTTPException(404, f"Currículo base {corpo.base_id} não encontrado")
        salvo = curriculos.salvar(
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
        resultado = recomendacao.recomendar(termo, perfis.obter())
        return ResultadoJson(vagas=[VagaJson.de(v) for v in resultado.vagas], erros=resultado.erros)

    @app.post("/coletas")
    def coletar() -> ResultadoColetaJson:
        # Sem perfil ou sem termos: DadoInvalido do serviço, 422.
        resultado = coleta.coletar()
        return ResultadoColetaJson(
            termos=[ColetaDoTermoJson(**vars(t)) for t in resultado.termos], novas=resultado.novas
        )

    @app.get("/vagas/encontradas")
    def listar_encontradas(
        novas: bool = True,
        limite: int = Query(default=50, ge=1, le=MAX_LIMITE_VAGAS),
        q: str | None = Query(default=None, max_length=MAX_TAMANHO_BUSCA),
        deslocamento: int = Query(default=0, ge=0, le=MAX_DESLOCAMENTO_VAGAS),
    ) -> list[VagaEncontradaJson]:
        vagas = encontradas.listar(somente_novas=novas, limite=limite, q=q, deslocamento=deslocamento)
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
    """

    def __init__(self, pool: Callable[[], Any], classe: Callable[[], type]):
        self._pool = pool
        # A classe vem por função para o import do psycopg ficar para o primeiro uso.
        self._classe = classe

    def __getattr__(self, nome: str) -> Any:
        def chamar(*args, **kwargs):
            with self._pool().connection() as conn:
                return getattr(self._classe()(conn), nome)(*args, **kwargs)

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


def _lista_env(nome: str) -> list[str]:
    return [p.strip() for p in os.environ.get(nome, "").split(",") if p.strip()]


APIFY_LIMITE_PADRAO = 50


def _limite_apify() -> int:
    """APIFY_LIMITE inválido ou não positivo cai no padrão: um provider opcional não derruba a app."""
    try:
        limite = int(os.environ.get("APIFY_LIMITE", APIFY_LIMITE_PADRAO))
    except ValueError:
        return APIFY_LIMITE_PADRAO
    return limite if limite > 0 else APIFY_LIMITE_PADRAO


def providers_padrao() -> list:
    """Remotar e Trampos sempre; Greenhouse, Lever e Ashby só com empresas em GREENHOUSE_EMPRESAS etc.;
    o LinkedIn pelo Apify só com APIFY_TOKEN."""
    from src.client.ashby import AshbyClient
    from src.client.greenhouse import GreenhouseClient
    from src.client.lever import LeverClient
    from src.client.remotar import RemotarClient
    from src.client.trampos import TramposClient

    providers = [RemotarClient(), TramposClient()]
    for classe, env in ((GreenhouseClient, "GREENHOUSE_EMPRESAS"), (LeverClient, "LEVER_EMPRESAS"), (AshbyClient, "ASHBY_EMPRESAS")):
        if empresas := _lista_env(env):
            providers.append(classe(empresas))
    if os.environ.get("APIFY_TOKEN"):
        from src.client.apify_linkedin import ApifyLinkedInClient

        # Cada busca custa até APIFY_LIMITE vagas no Apify (US$ 0,001–0,002 cada em out/2026).
        providers.append(
            # Vazio (APIFY_LOCAL=${APIFY_LOCAL} sem valor no compose) cai no padrão: sem país, a
            # busca paga traria vagas do mundo todo.
            ApifyLinkedInClient(local=os.environ.get("APIFY_LOCAL", "").strip() or "Brasil", limite=_limite_apify())
        )
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


def criar_app_producao() -> FastAPI:
    pool = _PoolPreguicoso()
    return criar_app(
        perfis=_PorChamada(pool, _perfis_postgres),
        curriculos=_PorChamada(pool, _curriculos_postgres),
        busca=BuscaService(providers_padrao()),
        armazenamento=Armazenamento(),
        encontradas=_PorChamada(pool, _encontradas_postgres),
    )


app = criar_app_producao()
