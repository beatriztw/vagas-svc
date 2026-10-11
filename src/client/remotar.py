from src.client.base import HttpGet, ProviderClient, combina_termo, get_json, montar_url, parse_data
from src.model.vaga_externa import VagaExterna

BASE_URL = "https://api.remotar.com.br/jobs"
VAGA_URL = "https://remotar.com.br/job/{id}"


class RemotarClient(ProviderClient):
    """Remotar: a busca por termo é feita pela própria API, paginada de 50 em 50.

    A busca da API é frouxa ("analista de dados" traz Analista de QA, de Salesforce...),
    então o título ainda passa por `combina_termo` aqui.
    """

    nome = "Remotar"

    def __init__(self, max_paginas: int = 3, http: HttpGet = get_json):
        super().__init__(http)
        self.max_paginas = max_paginas

    def buscar(self, termo: str) -> list[VagaExterna]:
        vagas = []
        pagina = 1
        while pagina <= self.max_paginas:
            dados = self._http(montar_url(BASE_URL, search=termo, page=pagina))
            for job in dados.get("data", []):
                if not combina_termo(job["title"], termo):
                    continue
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
                    )
                )
            if pagina >= dados.get("meta", {}).get("last_page", pagina):
                break
            pagina += 1
        return vagas
