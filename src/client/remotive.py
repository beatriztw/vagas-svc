import threading
import time
import urllib.parse
from collections.abc import Callable
from datetime import date, datetime
from functools import partial

from src.client.base import HttpGet, ProviderClient, get_json, montar_url, palavras, relevancia_termo
from src.client.sanitizar import MAX_CARACTERES_DESCRICAO, html_para_texto
from src.model.detalhe_vaga import DetalheVaga
from src.model.vaga_externa import VagaExterna

# Termos de uso (https://github.com/remotive-io/remote-jobs-api, para onde a api-documentation redireciona):
# - linkar a URL da vaga no Remotive e citar o Remotive como fonte: `url` é a do Remotive e a plataforma
#   aparece como "Remotive" na tela;
# - "no máximo 4 vezes por dia"; mais de 2 por minuto é bloqueado.
# A API pública ignora `search` (conferido em 12/10/2026: `search=bookkeeper`, `search=assistente financeiro`
# e sem `search` devolvem as mesmas 15 vagas), então vai UMA chamada sem termo, guardada por 6 h, e o termo
# é filtrado aqui pelo título. Assim a coleta de vários termos não multiplica as chamadas.
BASE_URL = "https://remotive.com/api/remote-jobs"
LIMITE_PADRAO = 100
CACHE_SEGUNDOS = 6 * 3600
# Depois de uma chamada (boa ou com erro), a próxima espera isto: a coleta de vários termos não martela a API.
INTERVALO_MINIMO_SEGUNDOS = 60
TIMEOUT_SEGUNDOS = 10
_HOSTS = {"remotive.com", "www.remotive.com"}

# De onde a pessoa pode estar ("candidate_required_location"), por palavra normalizada.
# "North America"/"Northern America" (EUA + Canadá) NÃO entram: "america" sozinha não basta,
# só "Americas" (o continente todo), "Latin America" e "South America".
# "Latin-America" vira uma palavra só ("latinamerica"): `palavras` junta o hífen.
_PALAVRAS_ACEITAS = {"worldwide", "latam", "americas", "brazil", "brasil", "latinamerica", "southamerica"}
_SEQUENCIAS_ACEITAS = {("latin", "america"), ("south", "america"), ("america", "latina"), ("america", "do", "sul")}


def aceita_local(local: object) -> bool:
    """True se `candidate_required_location` inclui quem está no Brasil/América Latina.

    Vazio ou ausente é recusado: sem dizer de onde, não dá para saber. "Anywhere" aceita, mas
    "Anywhere in the US" não ("anywhere in" restringe em vez de abrir).
    """
    if not isinstance(local, str):
        return False
    ps = palavras(local)
    for i, p in enumerate(ps):
        if p in _PALAVRAS_ACEITAS:
            return True
        if p == "anywhere" and ps[i + 1 : i + 2] != ["in"]:
            return True
        if any(tuple(ps[i : i + len(seq)]) == seq for seq in _SEQUENCIAS_ACEITAS):
            return True
    return False


class _DataInvalida(Exception):
    """A data veio, mas torta: só o item sai."""


def _data(valor: object) -> date | None:
    """`publication_date` é ISO 8601 sem fuso ("2026-10-08T21:47:32"). Ausente = sem data; torta levanta."""
    if valor is None or valor == "":
        return None
    if not isinstance(valor, str):
        raise _DataInvalida(repr(valor)[:50])
    try:
        return datetime.fromisoformat(valor).date()
    except ValueError as erro:
        raise _DataInvalida(valor[:50]) from erro


def _url(valor: object) -> str | None:
    """Só a página da vaga no próprio Remotive (exigência dos termos de uso)."""
    if not isinstance(valor, str):
        return None
    partes = urllib.parse.urlsplit(valor.strip())
    if partes.scheme != "https" or partes.hostname not in _HOSTS:
        return None
    return valor.strip()


class RemotiveClient(ProviderClient):
    """Remotive: vagas remotas cujo `candidate_required_location` aceita Brasil/América Latina."""

    nome = "Remotive"

    def __init__(
        self,
        limite: int = LIMITE_PADRAO,
        http: HttpGet = partial(get_json, timeout=TIMEOUT_SEGUNDOS),
        relogio: Callable[[], float] = time.monotonic,
    ):
        super().__init__(http)
        self.limite = limite
        self._relogio = relogio
        self._lock = threading.Lock()
        self._jobs: list | None = None
        self._jobs_em: float | None = None
        self._ultima_chamada: float | None = None
        self._ultimo_erro: str | None = None

    def _listagem(self) -> list:
        """A listagem do cache (até 6 h); sem cache, uma chamada, respeitando o intervalo mínimo."""
        with self._lock:
            agora = self._relogio()
            if self._jobs is not None and agora - self._jobs_em < CACHE_SEGUNDOS:
                return self._jobs
            if self._ultima_chamada is not None and agora - self._ultima_chamada < INTERVALO_MINIMO_SEGUNDOS:
                motivo = f": {self._ultimo_erro}" if self._ultimo_erro else ""
                raise RuntimeError(f"Remotive chamado há menos de {INTERVALO_MINIMO_SEGUNDOS} s{motivo}")
            self._ultima_chamada = agora
            try:
                dados = self._http(montar_url(BASE_URL, limit=self.limite))
                if not isinstance(dados, dict) or not isinstance(dados.get("jobs"), list):
                    raise ValueError(f"resposta do Remotive sem a lista de vagas: {str(dados)[:200]}")
            except Exception as erro:
                self._ultimo_erro = f"{type(erro).__name__}: {erro}"
                raise
            self._ultimo_erro = None
            self._jobs, self._jobs_em = dados["jobs"], agora
            return self._jobs

    def buscar(self, termo: str) -> list[VagaExterna]:
        vagas = []
        for item in self._listagem():
            # Um item torto descarta só ele, nunca a busca.
            if not isinstance(item, dict) or not aceita_local(item.get("candidate_required_location")):
                continue
            titulo = item.get("title")
            url = _url(item.get("url"))
            if not isinstance(titulo, str) or not titulo.strip() or url is None:
                continue
            # A API não filtra pelo termo: título sem nenhuma palavra dele sai (como no Remotar).
            if relevancia_termo(titulo, termo) == 0:
                continue
            try:
                publicada_em = _data(item.get("publication_date"))
            except _DataInvalida:
                continue
            empresa = item.get("company_name")
            descricao = html_para_texto(item.get("description"), MAX_CARACTERES_DESCRICAO)
            vagas.append(
                VagaExterna(
                    titulo=titulo.strip(),
                    empresa=empresa.strip() if isinstance(empresa, str) else "",
                    plataforma=self.nome,
                    url=url,
                    remoto=True,
                    publicada_em=publicada_em,
                    detalhe=DetalheVaga(descricao=descricao) if descricao else None,
                )
            )
        return vagas
