"""API HTTP do vagas-svc. Sem autenticação: só é exposta na rede interna do Swarm."""

import os
import threading
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from src.client.armazenamento import Armazenamento
from src.model.curriculo import Curriculo
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepository, PerfilBuscaRepository, validar_perfil
from src.service.busca_service import BuscaService
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
    publicada_desde_dias: int | None = Field(default=None, gt=0)
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


def criar_app(
    perfis: PerfilBuscaRepository,
    curriculos: CurriculoRepository,
    busca: BuscaService,
    armazenamento: Armazenamento | None = None,
) -> FastAPI:
    app = FastAPI(title="vagas-svc")
    app.state.armazenamento = armazenamento
    recomendacao = RecomendacaoService(busca)

    @app.exception_handler(ValueError)
    def valor_invalido(_: Request, erro: ValueError) -> JSONResponse:
        # Os repositórios validam (Curriculo.validar, enums do perfil) e levantam ValueError.
        return JSONResponse(status_code=422, content={"detail": str(erro)})

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
        # O repositório Postgres não chama validar_perfil; aqui o ValueError vira 422 em qualquer repositório.
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

    return app


class _Preguicoso:
    """Repassa cada chamada a um objeto recriado por `fabrica` a cada uso.

    Permite subir a app (e responder /saude) sem o Postgres no ar, e voltar a funcionar
    quando a conexão cai, porque a conexão é pedida de novo em cada chamada.
    """

    def __init__(self, fabrica: Callable[[], Any]):
        self._fabrica = fabrica

    def __getattr__(self, nome: str) -> Any:
        return getattr(self._fabrica(), nome)


class _ConexaoPostgres:
    def __init__(self):
        self._conn = None
        self._lock = threading.Lock()

    def obter(self):
        from src.repository.conexao import conectar

        with self._lock:
            if self._conn is None or self._conn.closed or self._conn.broken:
                self._conn = conectar()
            return self._conn


def _lista_env(nome: str) -> list[str]:
    return [p.strip() for p in os.environ.get(nome, "").split(",") if p.strip()]


def providers_padrao() -> list:
    """Remotar e Trampos sempre; Greenhouse, Lever e Ashby só com empresas em GREENHOUSE_EMPRESAS etc."""
    from src.client.ashby import AshbyClient
    from src.client.greenhouse import GreenhouseClient
    from src.client.lever import LeverClient
    from src.client.remotar import RemotarClient
    from src.client.trampos import TramposClient

    providers = [RemotarClient(), TramposClient()]
    for classe, env in ((GreenhouseClient, "GREENHOUSE_EMPRESAS"), (LeverClient, "LEVER_EMPRESAS"), (AshbyClient, "ASHBY_EMPRESAS")):
        if empresas := _lista_env(env):
            providers.append(classe(empresas))
    return providers


def criar_app_producao() -> FastAPI:
    conexao = _ConexaoPostgres()

    def perfis():
        from src.repository.postgres import PerfilBuscaRepositoryPostgres

        return PerfilBuscaRepositoryPostgres(conexao.obter())

    def curriculos():
        from src.repository.postgres import CurriculoRepositoryPostgres

        return CurriculoRepositoryPostgres(conexao.obter())

    return criar_app(
        perfis=_Preguicoso(perfis),
        curriculos=_Preguicoso(curriculos),
        busca=BuscaService(providers_padrao()),
        armazenamento=Armazenamento(),
    )


app = criar_app_producao()
