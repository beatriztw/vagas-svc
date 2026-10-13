import http.client
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from src.client.base import USER_AGENT, HttpGet, ProviderClient, get_json, relevancia_termo, montar_url, parse_data
from src.client.gupy import ErroGupy, link_gupy
from src.client.sanitizar import MAX_CARACTERES_DESCRICAO, html_para_linha, html_para_texto
from src.model.detalhe_vaga import (
    MAX_BENEFICIOS,
    MAX_REQUISITOS,
    MAX_TEXTO_ITEM,
    MAX_TEXTO_SUBTITULO,
    DetalheVaga,
    Requisito,
    salario_de_json,
)
from src.model.vaga_externa import VagaExterna

BASE_URL = "https://api.remotar.com.br/jobs"
VAGA_URL = "https://remotar.com.br/job/{id}"
# Abrir o detalhe espera o provider: mais que isso e a tela mostra a vaga sem as seções.
TIMEOUT_DETALHE_SEGUNDOS = 5
MAX_BYTES_DETALHE = 2_000_000
ID_DA_VAGA = re.compile(r"[0-9]{1,12}")
# Páginas do Gupy abertas ao mesmo tempo numa busca.
CONSULTAS_GUPY_SIMULTANEAS = 8


class VagaNaoEncontrada(Exception):
    """O provider respondeu 404/410: a vaga saiu do ar."""


class ErroProvider(Exception):
    """Falha ao falar com o provider (rede, tempo, status, JSON): o detalhe segue sem as seções."""


def get_json_detalhe(url: str) -> object:
    """GET com tempo de 5 s; 404/410 vira `VagaNaoEncontrada`, qualquer outra falha `ErroProvider`."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_DETALHE_SEGUNDOS) as resp:
            corpo = resp.read(MAX_BYTES_DETALHE + 1)
    except urllib.error.HTTPError as erro:
        if erro.code in (404, 410):
            raise VagaNaoEncontrada(url) from erro
        raise ErroProvider(f"HTTP {erro.code}") from erro
    except (OSError, http.client.HTTPException) as erro:
        raise ErroProvider(type(erro).__name__) from erro
    if len(corpo) > MAX_BYTES_DETALHE:
        raise ErroProvider("resposta grande demais")
    try:
        return json.loads(corpo)
    except (ValueError, RecursionError) as erro:
        raise ErroProvider("JSON inválido") from erro


def _instante(valor: object) -> datetime | None:
    """ISO 8601 com ou sem fuso (sem fuso vale UTC); qualquer outra coisa é None, sem derrubar o resto."""
    if not isinstance(valor, str):
        return None
    try:
        instante = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


class RemotarClient(ProviderClient):
    """Remotar: a busca por termo é feita pela própria API, paginada de 50 em 50.

    A busca da API é frouxa e também olha a descrição: vaga cujo título não tem NENHUMA
    palavra do termo sai aqui. O resto fica; a ordem por relevância é do RecomendacaoService.
    """

    nome = "Remotar"

    def __init__(
        self,
        max_paginas: int = 3,
        http: HttpGet = get_json,
        http_detalhe: HttpGet = get_json_detalhe,
        relogio: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        gupy: Callable[[str], datetime | None] | None = None,
    ):
        super().__init__(http)
        self.max_paginas = max_paginas
        self._http_detalhe = http_detalhe
        self._relogio = relogio
        # `GupyClient.encerramento`: o Remotar segue com a vaga "ativa, sem prazo" depois que ela
        # fecha no Gupy, onde a candidatura acontece. None não consulta o Gupy.
        self._gupy = gupy

    def _encerramento_gupy(self, job: dict) -> datetime | None:
        """Quando a vaga fecha no Gupy, se o link de candidatura for de lá; None sem prazo, sem
        link do Gupy ou se a página não deu para ler (aí vale só o que o Remotar disse)."""
        link = link_gupy(job.get("externalLink")) if self._gupy is not None else None
        if link is None:
            return None
        try:
            return self._gupy(link)
        except ErroGupy:
            return None

    def _expirada_em(self, job: dict, gupy: datetime | None = None) -> datetime | None:
        """O `expiresAt` (mesmo futuro: a vaga some quando o prazo passa); `expired: true` vale no
        máximo agora. Data que não é ISO 8601 não conta, e a vaga fica como está. `gupy` (o
        encerramento lá) vale quando chega antes."""
        agora = self._relogio()
        prazo = _instante(job.get("expiresAt"))
        if job.get("expired") is True:
            prazo = min(prazo, agora) if prazo is not None else agora
        return min((p for p in (prazo, gupy) if p is not None), default=None)

    def _tem_prazo(self, job: dict, expirada_em: datetime | None) -> bool | None:
        """False só quando o Remotar diz que NÃO há prazo (`expires: false`, sem `expiresAt` e sem
        `expired: true`) e o Gupy também não deu um: aí um prazo futuro gravado numa coleta anterior
        é removido. True quando há `expirada_em`. Qualquer outra coisa (campo ausente, torto ou
        contraditório) é None e o repositório mantém a data que já estava."""
        if expirada_em is not None:
            return True
        if job.get("expires") is False and job.get("expired") is not True and job.get("expiresAt") is None:
            return False
        return None

    def _detalhe_do_job(self, job: dict, gupy: datetime | None = None) -> DetalheVaga:
        """O detalhe de um job (da listagem ou de /jobs/{id}); campo malformado descarta só o campo."""
        requisitos = []
        for item in job.get("jobRequirements") if isinstance(job.get("jobRequirements"), list) else []:
            texto = html_para_linha(item.get("description"), MAX_TEXTO_ITEM) if isinstance(item, dict) else ""
            if texto:
                requisitos.append(Requisito(texto, item.get("mandatory") is True))
        beneficios = []
        for item in job.get("jobBenefits") if isinstance(job.get("jobBenefits"), list) else []:
            texto = html_para_linha(item.get("description"), MAX_TEXTO_ITEM) if isinstance(item, dict) else ""
            if texto:
                beneficios.append(texto)
        pago = job.get("jobSalary")
        salario = None
        # CONFIRMADO em 2026-10-12 na página pública https://remotar.com.br/job/164153: from=900000 e
        # to=1300000 aparecem como "R$ 9,000.00 a R$ 13,000.00", ou seja, os valores são CENTAVOS.
        # from/to nulo ou 0 = não informado, como type "uninformed".
        if isinstance(pago, dict) and pago.get("type") != "uninformed":
            salario = salario_de_json(
                {"minimo": pago.get("from"), "maximo": pago.get("to"), "moeda": pago.get("currency"), "periodo": pago.get("type")}
            )
        return DetalheVaga(
            descricao=html_para_texto(job.get("description"), MAX_CARACTERES_DESCRICAO) or None,
            requisitos=requisitos[:MAX_REQUISITOS],
            salario=salario,
            beneficios=beneficios[:MAX_BENEFICIOS],
            subtitulo=html_para_linha(job.get("subtitle"), MAX_TEXTO_SUBTITULO) or None,
            expirada_em=(expirada_em := self._expirada_em(job, gupy)),
            tem_prazo=self._tem_prazo(job, expirada_em),
        )

    def _detalhe_seguro(self, job: dict, gupy: datetime | None) -> DetalheVaga | None:
        """O detalhe da listagem; se este job vier estranho, só ele fica sem detalhe (a busca segue)."""
        try:
            return self._detalhe_do_job(job, gupy)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError):
            return None

    def detalhar(self, id: str) -> DetalheVaga:
        """Detalhe da vaga `id` (só dígitos). `VagaNaoEncontrada` em 404; `ErroProvider` em qualquer falha
        (rede, tempo, JSON, forma inesperada): nada que o provider mande vira 500."""
        if not isinstance(id, str) or not ID_DA_VAGA.fullmatch(id):
            raise ValueError("id da vaga do Remotar tem que ter só dígitos")
        job = self._http_detalhe(f"{BASE_URL}/{id}")
        if not isinstance(job, dict):
            raise ErroProvider("resposta não é um objeto")
        try:
            return self._detalhe_do_job(job, self._encerramento_gupy(job))
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError) as erro:
            raise ErroProvider(f"resposta com forma inesperada: {type(erro).__name__}") from erro

    def buscar(self, termo: str) -> list[VagaExterna]:
        jobs = []
        pagina = 1
        while pagina <= self.max_paginas:
            dados = self._http(montar_url(BASE_URL, search=termo, page=pagina))
            jobs.extend(job for job in dados.get("data", []) if relevancia_termo(job["title"], termo) != 0)
            if pagina >= dados.get("meta", {}).get("last_page", pagina):
                break
            pagina += 1
        # Uma página do Gupy por vaga: em paralelo, senão a coleta espera uma por uma.
        with ThreadPoolExecutor(CONSULTAS_GUPY_SIMULTANEAS) as pool:
            encerramentos = list(pool.map(self._encerramento_gupy, jobs))
        vagas = []
        for job, gupy in zip(jobs, encerramentos):
            expirada_em = self._expirada_em(job, gupy)
            empresa = job.get("companyDisplayName") or (job.get("company") or {}).get("name") or ""
            local = ", ".join(p for p in (job.get("city"), job.get("state")) if p) or None
            vagas.append(
                VagaExterna(
                    titulo=job["title"].strip(),
                    empresa=empresa,
                    plataforma=self.nome,
                    url=VAGA_URL.format(id=job["id"]),
                    local=local,
                    remoto=job.get("type") == "remote",
                    publicada_em=parse_data(job.get("createdAt")),
                    expirada_em=expirada_em,
                    tem_prazo=self._tem_prazo(job, expirada_em),
                    detalhe=self._detalhe_seguro(job, gupy),
                )
            )
        return vagas
