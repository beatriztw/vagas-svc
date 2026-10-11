from src.client.base import HttpGet, ProviderClient, contem, get_json, parse_data
from src.model.vaga_externa import VagaExterna

BASE_URL = "https://api.lever.co/v0/postings/{empresa}?mode=json"


class LeverClient(ProviderClient):
    """Páginas de carreira hospedadas no Lever. `empresas` são os identificadores do site."""

    nome = "Lever"

    def __init__(self, empresas: list[str], http: HttpGet = get_json):
        super().__init__(http)
        self.empresas = empresas

    def buscar(self, termo: str) -> list[VagaExterna]:
        vagas = []
        for empresa in self.empresas:
            for job in self._http(BASE_URL.format(empresa=empresa)):
                if not contem(job["text"], termo):
                    continue
                tipo = job.get("workplaceType")
                vagas.append(
                    VagaExterna(
                        titulo=job["text"].strip(),
                        empresa=empresa,
                        plataforma=self.nome,
                        url=job["hostedUrl"],
                        local=(job.get("categories") or {}).get("location"),
                        remoto=None if tipo in (None, "unspecified") else tipo == "remote",
                        publicada_em=parse_data(job.get("createdAt")),
                    )
                )
        return vagas
