from src.client.base import HttpGet, ProviderClient, contem, get_json, montar_url, parse_data
from src.model.vaga_externa import VagaExterna

BASE_URL = "https://trampos.co/api/v2/opportunities"
VAGA_URL = "https://trampos.co/oportunidades/{id}"


class TramposClient(ProviderClient):
    """Trampos.co: a API ignora o termo de busca, então percorremos as páginas e filtramos aqui."""

    nome = "Trampos"

    def __init__(self, max_paginas: int = 20, http: HttpGet = get_json):
        super().__init__(http)
        self.max_paginas = max_paginas

    def buscar(self, termo: str) -> list[VagaExterna]:
        vagas = []
        pagina = 1
        while pagina <= self.max_paginas:
            dados = self._http(montar_url(BASE_URL, page=pagina))
            for op in dados.get("opportunities", []):
                if not (contem(op["name"], termo) or contem(op.get("category_name"), termo)):
                    continue
                empresa = op.get("custom_company_name") or (op.get("company") or {}).get("name") or ""
                local = ", ".join(p for p in (op.get("city"), op.get("state")) if p) or None
                vagas.append(
                    VagaExterna(
                        titulo=op["name"].strip(),
                        empresa=empresa,
                        plataforma=self.nome,
                        url=VAGA_URL.format(id=op["id"]),
                        local=local,
                        # A API só informa se é híbrida; não há campo para remoto.
                        remoto=False if op.get("hybrid") else None,
                        publicada_em=parse_data(op.get("published_at")),
                    )
                )
            if pagina >= dados.get("pagination", {}).get("total_pages", pagina):
                break
            pagina += 1
        return vagas
