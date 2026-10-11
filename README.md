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

## Banco

O estado (perfil de busca e currículos) fica num Postgres. O schema está em `migrations/001_curriculo_perfil.sql`, idempotente (`CREATE TABLE IF NOT EXISTS`), aplicado à mão:

```bash
psql -f migrations/001_curriculo_perfil.sql
```

- `perfil_busca`: uma linha só (id = 1), gravada por upsert. Os CHECK de `modalidades` e `niveis` espelham `Modalidade` e `Nivel` de `src/model/vaga.py`.
- `curriculo`: `tipo` é `base` ou `adaptado`; adaptado sempre aponta para uma base (`base_id`). A base vigente é a base mais recente; as anteriores ficam como histórico.

`src/repository/conexao.py` conecta com as variáveis `PGHOST`, `PGPORT` (padrão 5432), `PGDATABASE`, `PGUSER` e a senha de `PGPASSWORD` ou do arquivo apontado por `PGPASSWORD_FILE` (no Swarm, `/run/secrets/vagas_db_password`).

## Testes

```bash
python -m unittest discover -s tests -t .
```

Os testes de `tests/test_postgres.py` só rodam com `PGHOST` definido; cada um cria um schema descartável, aplica a migration e o apaga no fim. Sem `PGHOST` eles são pulados.
