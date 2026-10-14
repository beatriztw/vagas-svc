from datetime import date, datetime, timezone
from functools import partial

from src.client.base import HttpGet, ProviderClient, get_json, montar_url, palavras
from src.client.sanitizar import MAX_CARACTERES_DESCRICAO, html_para_texto
from src.model.detalhe_vaga import DetalheVaga
from src.model.vaga_externa import VagaExterna

# API pública e gratuita, sem token (conferida em 12/10/2026). `country=BR` devolve só as vagas que
# aceitam quem está no Brasil: `locationRestrictions` vazio (qualquer lugar) ou com "Brazil".
BASE_URL = "https://himalayas.app/jobs/api/search"
PAIS = "BR"
# Uma página por termo: a API não cobra, mas a coleta não precisa de mais que isso.
LIMITE_PADRAO = 20
# Uma API lenta não pode segurar a coleta dos outros providers.
TIMEOUT_SEGUNDOS = 10
# Epoch em segundos (`pubDate`): fora disso é lixo.
_EPOCH_MINIMO, _EPOCH_MAXIMO = 946_684_800, 4_102_444_800  # 2000-01-01 a 2100-01-01


class _DataInvalida(Exception):
    """A data veio, mas torta: o item é descartado (regra do CLAUDE.md), a busca segue."""


def _data(valor: object) -> date | None:
    """`pubDate` é epoch em SEGUNDOS (conferido na resposta gravada). Ausente = sem data; outro tipo
    ou epoch fora de 2000–2100 levanta `_DataInvalida`."""
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not _EPOCH_MINIMO <= valor < _EPOCH_MAXIMO:
        raise _DataInvalida(repr(valor)[:50])
    return datetime.fromtimestamp(valor, tz=timezone.utc).date()


def _url(item: dict) -> str | None:
    # `guid` é a página da vaga no Himalayas (estável); `applicationLink` hoje é a mesma URL.
    for campo in ("guid", "applicationLink"):
        valor = item.get(campo)
        if isinstance(valor, str) and valor.strip().startswith(("https://", "http://")):
            return valor.strip()
    return None


def aceita_brasil(restricoes: object) -> bool:
    """Lista vazia = qualquer lugar; senão tem que citar o Brasil (por palavra, em inglês ou português).

    O `country=BR` da busca já filtra; isto é a segunda trava, caso a API mude o comportamento.
    Campo ausente ou que não é lista confia no filtro da busca.
    """
    if not isinstance(restricoes, list):
        return True
    paises = [p for p in restricoes if isinstance(p, str) and p.strip()]
    return not paises or any(set(palavras(p)) & {"brazil", "brasil"} for p in paises)


class HimalayasClient(ProviderClient):
    """Himalayas: vagas remotas abertas para quem está no Brasil; a busca pelo termo é da própria API."""

    nome = "Himalayas"

    def __init__(self, limite: int = LIMITE_PADRAO, http: HttpGet = partial(get_json, timeout=TIMEOUT_SEGUNDOS)):
        super().__init__(http)
        self.limite = limite

    def buscar(self, termo: str) -> list[VagaExterna]:
        dados = self._http(montar_url(BASE_URL, q=termo, country=PAIS, limit=self.limite))
        if not isinstance(dados, dict) or not isinstance(dados.get("jobs"), list):
            raise ValueError(f"resposta do Himalayas sem a lista de vagas: {str(dados)[:200]}")
        vagas = []
        for item in dados["jobs"]:
            # Um item torto descarta só ele, nunca a busca.
            if not isinstance(item, dict) or not aceita_brasil(item.get("locationRestrictions")):
                continue
            titulo = item.get("title")
            url = _url(item)
            if not isinstance(titulo, str) or not titulo.strip() or url is None:
                continue
            try:
                publicada_em = _data(item.get("pubDate"))
            except _DataInvalida:
                continue
            empresa = item.get("companyName")
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
