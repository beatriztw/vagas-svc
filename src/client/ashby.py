from src.client.base import HttpGet, ProviderClient, contem, get_json, parse_data
from src.model.vaga_externa import VagaExterna

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{empresa}"


class AshbyClient(ProviderClient):
    """Páginas de carreira hospedadas no Ashby. `empresas` são os identificadores do board."""

    nome = "Ashby"

    def __init__(self, empresas: list[str], http: HttpGet = get_json):
        super().__init__(http)
        self.empresas = empresas

    def buscar(self, termo: str) -> list[VagaExterna]:
        vagas = []
        for empresa in self.empresas:
            dados = self._http(BASE_URL.format(empresa=empresa))
            for job in dados.get("jobs", []):
                if not job.get("isListed", True) or not contem(job["title"], termo):
                    continue
                vagas.append(
                    VagaExterna(
                        titulo=job["title"].strip(),
                        empresa=empresa,
                        plataforma=self.nome,
                        url=job["jobUrl"],
                        local=job.get("location"),
                        remoto=job.get("isRemote"),
                        publicada_em=parse_data(job.get("publishedAt")),
                    )
                )
        return vagas
