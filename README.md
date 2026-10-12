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
| `ApifyLinkedInClient(local, limite)` | busca pública de vagas do LinkedIn, pelo actor `curious_coder/linkedin-jobs-scraper` do Apify | termo e local enviados ao actor |

O `ApifyLinkedInClient` precisa da variável `APIFY_TOKEN` (token da conta no Apify). Ele não usa login do LinkedIn. O Apify cobra por vaga devolvida, por isso `limite` (50 por padrão) vai no input do actor e também como `maxItems` do run, que é o teto de cobrança do próprio Apify. Sem o token, a busca desse provider falha e aparece em `erros`.

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

## API HTTP

`src/controller/api.py` expõe a API com FastAPI (porta 8000, sem autenticação: só para a rede interna). Localmente:

```bash
uvicorn src.controller.api:app --reload
```

A app de produção lê o Postgres pelas variáveis `PG*` (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` ou `PGPASSWORD_FILE`) e conecta só no primeiro uso, então `/saude` responde mesmo sem banco. Os providers de `/vagas` são Remotar e Trampos; Greenhouse, Lever e Ashby entram quando `GREENHOUSE_EMPRESAS`, `LEVER_EMPRESAS` ou `ASHBY_EMPRESAS` têm a lista de empresas separada por vírgula, e o LinkedIn pelo Apify entra com `APIFY_TOKEN` (opcionais `APIFY_LOCAL`, padrão `Brasil`, e `APIFY_LIMITE`, padrão 50 vagas por busca, que é o que o Apify cobra). O `Armazenamento` (S3 do Garage) usa `S3_ENDPOINT` (no Pi, `http://garage:3900`: o botocore recusa host com `_`), `S3_REGION` (padrão `garage`), `S3_BUCKET` (padrão `vagas`), `S3_ACCESS_KEY_ID` e `S3_SECRET_ACCESS_KEY` ou `S3_SECRET_ACCESS_KEY_FILE`. Em testes, `criar_app(perfis, curriculos, busca)` recebe os repositórios em memória.

**CSRF nas rotas JSON.** Toda rota que muda estado fora de `/tela` (`PUT /perfil`, `PUT /curriculos/base`, `POST /curriculos/adaptados`, `POST /coletas`, `POST /vagas/vistas`, `POST`/`PATCH`/`DELETE /candidaturas`) exige `Content-Type: application/json`, mesmo sem corpo (o `DELETE` e o `POST /coletas` também), senão 415. Além disso: `Sec-Fetch-Site`, se vier, tem que ser `same-origin` ou `none`, e `Origin`, se vier, tem o mesmo host do `Host` (`Origin: null` não passa), senão 403. Sem `Origin` nem `Sec-Fetch-Site` (curl, timer) passa. Exemplo: `curl -X POST -H 'Content-Type: application/json' -d '{}' http://host:8000/coletas`. A regra é uma dependência do app (`src/controller/seguranca_json.py`), então rota JSON mutável nova já nasce protegida; `/tela` tem a proteção própria do HTMX e `/interno/*` (Bearer) fica de fora.

| Método e rota | O que faz |
|---|---|
| `GET /saude` | `{"ok": true}` |
| `GET /perfil` | perfil de busca salvo (404 se não houver) |
| `PUT /perfil` | salva o perfil; modalidades e níveis com os valores dos enums (`"Remoto"`, `"Júnior"`), senão 422 |
| `GET /curriculos/base` | currículo base vigente (404 se não houver) |
| `PUT /curriculos/base` | `{titulo, conteudo_md, arquivo_s3?}`: cria uma nova versão da base |
| `GET /curriculos/adaptados` | lista os currículos adaptados |
| `POST /curriculos/adaptados` | `{titulo, conteudo_md, vaga_url, base_id?}`: sem `base_id` usa a base vigente (409 se não houver base) |
| `GET /vagas?termo=...` | busca nos providers (o Remotar, cuja busca é frouxa, corta título sem nenhuma palavra do termo), aplica o perfil: exclui termos do título, só remotas se a modalidade for só Remoto, corta vagas mais antigas que `publicada_desde_dias`, corta nível fora de `niveis` quando o título diz o nível, mantém só a cidade de `localidades` ou remotas (vaga sem local fica) e ordena pela relevância do título (fração das palavras do termo que ele tem), desempatando pelas `skills` que aparecem no título; devolve `{vagas, erros}` |

## Coleta

`GET /vagas` busca ao vivo a cada chamada. A coleta roda a mesma recomendação para cada termo do perfil salvo (termos repetidos por grafia, como `Front-end` e `front end`, rodam uma vez) e guarda as vagas na tabela `vaga_encontrada` de `migrations/002_vaga_encontrada.sql` (idempotente, aplicada à mão como a 001: `psql -f migrations/002_vaga_encontrada.sql`). A chave é a URL: a mesma vaga achada por dois termos é uma linha só, com os dois em `termos`; uma coleta seguinte atualiza `ultima_vez_em` e os campos que vieram preenchidos, sem contar a vaga como nova e sem mexer em `primeira_vez_em` nem em `vista_em`. Nova = ainda não marcada como vista.

| Método e rota | O que faz |
|---|---|
| `POST /coletas` | roda a coleta e devolve `{termos: [{termo, encontradas, novas, erros}], novas}`; 422 sem perfil salvo ou com perfil sem termos; provider que falha aparece em `erros` do termo e não interrompe |
| `GET /vagas/encontradas?novas=true&limite=50&q=&deslocamento=0` | vagas guardadas com o perfil salvo aplicado na leitura (ver "Histórico pelo perfil" abaixo); sem perfil salvo, primeiro as achadas mais recentemente; `novas=false` inclui as já vistas; `q` filtra por palavra; `limite` de 1 a 500 (fora disso, 422) |
| `POST /vagas/vistas` | `{"urls": [...]}` (até 500): marca como vistas e devolve `{"marcadas": n}`, só as que ainda não estavam |

### Histórico pelo perfil

O histórico (`GET /vagas/encontradas` e a lista da tela: contador, "carregar mais", busca `q` e Novas/Todas) obedece ao perfil salvo **no momento da leitura**, não ao perfil da época da coleta. Mudar o perfil muda na hora o que aparece, sem migration e sem recoletar.

- Filtros: os mesmos da busca ao vivo, pela mesma função (`filtrar_pelo_perfil` em `src/service/recomendacao_service.py`, que `aplicar_perfil` também usa): `excluir`, só remotas quando `modalidades` é só Remoto, `niveis`, `localidades` e `publicada_desde_dias`.
- Ordem por match: relevância do título aos termos do perfil (a maior `relevancia_termo` entre eles; termo sem palavra útil, como `de`, não conta), depois quantas `skills` aparecem no título, depois `publicada_em` mais nova (sem data por último), depois `primeira_vez_em` mais nova; a URL desempata o resto, para a paginação não repetir nem pular.
- Como: `HistoricoService` (`src/service/historico_service.py`) lê do Postgres a janela de varredura, as `TETO_VARREDURA_HISTORICO` (5000) vagas coletadas mais recentemente com o filtro Novas, filtra o `q`, aplica o perfil e a ordem em Python e só então pagina e conta. Página e contador saem da mesma leitura, então "[N] encontradas" é o que a lista mostra. Corte: com mais de 5000 vagas no histórico (ou não vistas, em Novas), as mais antigas ficam fora da lista, do contador e da busca.
- Sem perfil salvo, o comportamento antigo: mais recentes primeiro, paginação no SQL.
- Vaga escondida pelo perfil continua abrindo pelo link direto (`/tela?url=...`). `ocultar_candidatadas` ainda não é aplicado.

Sem a API (para o cron), com as mesmas variáveis `PG*` e os mesmos providers da app de produção:

```bash
python -m src.controller.coleta
```

Imprime uma linha por termo (`python: 20 encontradas, 3 novas | erros: ...`) e sai com 0. Sai com 1 quando a coleta inteira falha: sem perfil ou sem termos, banco fora do ar, ou todos os providers com erro em todos os termos. Erro de um provider só não é falha.

A imagem é construída pelo `Dockerfile` da raiz (`docker build -t vagas-svc .`).

## Candidaturas

As vagas em que a pessoa aplicou ficam na tabela `candidatura` de `migrations/003_candidatura.sql` (idempotente, aplicada à mão depois da 002: `psql -f migrations/003_candidatura.sql`). A chave é a URL da vaga, que tem que existir em `vaga_encontrada` (chave estrangeira): marcar de novo a mesma vaga devolve a candidatura que já existe. O status é um de `enviada` (padrão), `em_analise`, `entrevista`, `recusada`, `oferta`; fora disso, 422. Aplicada pode ser desfeita (`DELETE`).

| Método e rota | O que faz |
|---|---|
| `POST /candidaturas` | `{"vaga_url": "..."}`: 201 se criou, 200 se já existia; 422 se a vaga não está no histórico da coleta |
| `GET /candidaturas` | lista, a mais recente primeiro, com `titulo` e `empresa` da vaga e `status_rotulo` (`Em análise`) |
| `PATCH /candidaturas/{id}` | `{"status": "entrevista"}`; 404 se não existe, 422 se o status é inválido |
| `DELETE /candidaturas/{id}` | desfaz: 204, ou 404 se não existe |

## Tela

`GET /` redireciona para `/tela`, uma página HTML renderizada no servidor (Jinja2) com HTMX: `src/templates` (`base.html`, `tela/index.html` e os partials `tela/_*.html`) e `src/static` (`tela.css`, `htmx.min.js`, servidos em `/static`). O htmx 2.0.4 é servido local, com versão, licença e sha256 registrados em `src/static/README.md`. Os caminhos saem de `Path(__file__)`, não do diretório atual.

- A lista da tela é a do histórico pelo perfil (seção "Histórico pelo perfil"): a mesma leitura da rota JSON, com o mesmo contador.
- Esqueleto: header, lista, detalhe e "Vagas aplicadas" em estado vazio; "Coletar agora" e "Meu perfil" ainda desabilitados. Lista, busca, coleta e candidaturas entram nos PRs seguintes só preenchendo os partials.
- `src/controller/tela_seguranca.py` guarda o que as rotas de `/tela` compartilham: o `Jinja2Templates` (autoescape ligado), o filtro `url_segura` (só `http`/`https` com host vira `href`) e a dependência de CSRF das rotas que mudam estado (`HX-Request: true` e `Origin` igual ao host, ou, sem `Origin`, `Sec-Fetch-Site: same-origin`; senão 403).
- Respostas de `/tela` e `/static` levam `Content-Security-Policy: default-src 'self'; ...`, `X-Content-Type-Options: nosniff` e `Referrer-Policy: same-origin`; `/tela` também `Cache-Control: no-store`. Por isso não há JS nem CSS inline.
- `DadoInvalido` em rota de `/tela` vira 422 com o partial `_erro.html`; nas rotas JSON continua `{"detail": ...}`. Qualquer outro erro é 500.

## Testes

```bash
python -m unittest discover -s tests -t .
```

Os testes de `tests/test_postgres.py` só rodam com `PGHOST` definido; cada um cria um schema descartável, aplica a migration e o apaga no fim. Sem `PGHOST` eles são pulados.
