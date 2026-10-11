# vagas-svc

Projeto Python organizado em camadas dentro de `src/`:

| Pasta | Responsabilidade |
|---|---|
| `src/controller` | Entrada da aplicação: recebe as requisições e devolve as respostas |
| `src/service` | Regras de negócio |
| `src/repository` | Acesso a dados (banco, arquivos) |
| `src/client` | Integrações com APIs e serviços externos |

## Como rodar

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Providers de vagas

Os clients em `src/client` usam apenas a biblioteca padrão e devolvem `VagaExterna`:

| Client | Fonte | Busca |
|---|---|---|
| `GreenhouseClient(empresas)` | API pública de boards do Greenhouse | filtra o título localmente |
| `LeverClient(empresas)` | API pública de postings do Lever | filtra o título localmente |
| `AshbyClient(empresas)` | API pública de job board do Ashby | filtra o título localmente |
| `RemotarClient()` | API do Remotar | termo enviado para a API |
| `TramposClient()` | API do Trampos.co | filtra título e categoria localmente |

`BuscaService` consulta todos, remove vagas repetidas pela URL e registra em `erros` os providers que falharam, sem interromper a busca:

```python
from src.client.greenhouse import GreenhouseClient
from src.client.remotar import RemotarClient
from src.service.busca_service import BuscaService

resultado = BuscaService([GreenhouseClient(["stone"]), RemotarClient()]).buscar("python")
```

## API HTTP

`src/controller/api.py` expõe a API com FastAPI (porta 8000, sem autenticação: só para a rede interna). Localmente:

```bash
uvicorn src.controller.api:app --reload
```

A app de produção lê o Postgres pelas variáveis `PG*` (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` ou `PGPASSWORD_FILE`) e conecta só no primeiro uso, então `/saude` responde mesmo sem banco. Os providers de `/vagas` são Remotar e Trampos; Greenhouse, Lever e Ashby entram quando `GREENHOUSE_EMPRESAS`, `LEVER_EMPRESAS` ou `ASHBY_EMPRESAS` têm a lista de empresas separada por vírgula. Em testes, `criar_app(perfis, curriculos, busca)` recebe os repositórios em memória.

| Método e rota | O que faz |
|---|---|
| `GET /saude` | `{"ok": true}` |
| `GET /perfil` | perfil de busca salvo (404 se não houver) |
| `PUT /perfil` | salva o perfil; modalidades e níveis com os valores dos enums (`"Remoto"`, `"Júnior"`), senão 422 |
| `GET /curriculos/base` | currículo base vigente (404 se não houver) |
| `PUT /curriculos/base` | `{titulo, conteudo_md, arquivo_s3?}`: cria uma nova versão da base |
| `GET /curriculos/adaptados` | lista os currículos adaptados |
| `POST /curriculos/adaptados` | `{titulo, conteudo_md, vaga_url, base_id?}`: sem `base_id` usa a base vigente (409 se não houver base) |
| `GET /vagas?termo=...` | busca nos providers e aplica o perfil (exclui termos do título, só remotas se a modalidade for só Remoto, corta vagas mais antigas que `publicada_desde_dias`); devolve `{vagas, erros}` |

A imagem é construída pelo `Dockerfile` da raiz (`docker build -t vagas-svc .`).

## Testes

```bash
python -m unittest discover -s tests -t .
```
