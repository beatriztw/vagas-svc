from src.client.base import HttpGet, ProviderClient, contem, get_json, parse_data
from src.model.vaga_externa import VagaExterna

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{empresa}/jobs"


class GreenhouseClient(ProviderClient):
    """Páginas de carreira hospedadas no Greenhouse. `empresas` são os identificadores do board."""

    nome = "Greenhouse"

    def __init__(self, empresas: list[str], http: HttpGet = get_json):
        super().__init__(http)
        self.empresas = empresas

    def buscar(self, termo: str) -> list[VagaExterna]:
        vagas = []
        for empresa in self.empresas:
            dados = self._http(BASE_URL.format(empresa=empresa))
            for job in dados.get("jobs", []):
                if not contem(job["title"], termo):
                    continue
                vagas.append(
                    VagaExterna(
                        titulo=job["title"].strip(),
                        empresa=empresa,
                        plataforma=self.nome,
                        url=job["absolute_url"],
                        local=(job.get("location") or {}).get("name"),
                        publicada_em=parse_data(job.get("first_published") or job.get("updated_at")),
                    )
                )
        return vagas
