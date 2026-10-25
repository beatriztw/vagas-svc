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
pip install -r requirements.txt -r requirements-dev.txt
```

`requirements-dev.txt` só tem o que os testes usam (o `pypdf`, que lê o texto do PDF do currículo); a imagem instala só o `requirements.txt`.

## Providers de vagas

Os clients em `src/client` usam apenas a biblioteca padrão e devolvem `VagaExterna`:

| Client | Fonte | Busca |
|---|---|---|
| `GreenhouseClient(empresas)` | API pública de boards do Greenhouse | filtra o título localmente |
| `LeverClient(empresas)` | API pública de postings do Lever | filtra o título localmente |
| `AshbyClient(empresas)` | API pública de job board do Ashby | filtra o título localmente |
| `RemotarClient()` | API do Remotar | termo enviado para a API |
| `TramposClient()` | API do Trampos.co | filtra título e categoria localmente |
| `HimalayasClient(limite)` | API pública do Himalayas (`/jobs/api/search?country=BR`), grátis e sem token | termo enviado para a API; uma página de 20 vagas |
| `RemotiveClient(limite)` | API pública do Remotive (`/api/remote-jobs`), grátis e sem token | a API ignora o termo: uma listagem guardada por 6 h e o título filtrado localmente |
| `ApifyLinkedInClient(locais, limite)` | busca pública de vagas **remotas** do LinkedIn, pelo actor `curious_coder/linkedin-jobs-scraper` do Apify | uma URL de busca por local (`f_WT=2` remoto, última semana, mais novas primeiro) |
| `ApifyLinkedInPostsClient(limite)` | vagas divulgadas em **publicações** (posts) do LinkedIn, pelo actor `harvestapi/linkedin-post-search` do Apify, sem login | uma consulta booleana por termo (`"<termo>" AND ("vaga" OR "contratando" OR "contratamos") AND ("remoto" OR "home office")`), posts das últimas 24 h, mais novos primeiro; filtra o ruído localmente |

O `ApifyLinkedInClient` precisa da variável `APIFY_TOKEN` (token da conta no Apify). Ele não usa login do LinkedIn. O Apify cobra por vaga devolvida, por isso `limite` (50 por padrão, por local) vai no input do actor e `limite × locais` vai como `maxItems` do run, que é o teto de cobrança do próprio Apify. Os locais vão em inglês, como o LinkedIn entende: com "Brasil" a busca sai sem local e traz vagas dos EUA. O padrão é `Brazil` e `Latin America` (vagas abertas para a região). Sem o token, a busca desse provider falha e aparece em `erros`.

`HimalayasClient` e `RemotiveClient` trazem vagas **remotas abertas para quem está no Brasil/América Latina**, todas com `remoto=True` e a descrição (HTML sanitizado, mesmo teto de caracteres do LinkedIn) no detalhe:

- **Himalayas**: `country=BR` já devolve só as vagas sem restrição de país ou com o Brasil em `locationRestrictions`; o client confere de novo (por palavra) e descarta o resto. A URL é a página da vaga no Himalayas (`guid`).
- **Remotive**: aceita a vaga só se `candidate_required_location` tem, por palavra normalizada, `Worldwide`, `Anywhere` (mas não "Anywhere in the US"), `LATAM`, `Latin America`, `South America`, `Americas`, `Brazil`/`Brasil` (ou América Latina/do Sul). **`North America`/`Northern America` é recusado** (na prática é EUA + Canadá), assim como `USA`, `Europe`, `UK` e local vazio. Os termos de uso do Remotive pedem o link da vaga no próprio Remotive e o Remotive citado como fonte: a URL gravada é a do Remotive e a plataforma aparece como "Remotive". Eles também pedem no máximo 4 chamadas por dia (mais de 2 por minuto é bloqueado): como a API pública ignora `search` (conferido em 12/10/2026, a listagem tem só ~15 vagas), o client faz uma chamada sem termo, guarda a listagem por 6 h e serve todos os termos dela; depois de uma chamada (boa ou com erro) a próxima espera pelo menos 60 s, e nesse meio tempo a busca falha em `erros` sem chamar a API.
- Timeout de 10 s; item torto (não é objeto, sem título ou URL, data torta) sai sozinho; erro HTTP vira erro do provider, que aparece em `erros`.
- **As vagas são em inglês**: os termos do perfil em português casam pouco (em 12/10/2026, "assistente financeiro" trouxe 1 vaga no Himalayas e nenhuma no Remotive; "bookkeeper" trouxe 10 no Himalayas). Para aproveitar esses providers, acrescente ao perfil termos em inglês, como `bookkeeper`, `accounts payable`, `finance assistant` e `administrative assistant`.

O `ApifyLinkedInPostsClient` (plataforma `LinkedIn (posts)`) usa o mesmo token e vem **desligado**: só entra com `APIFY_POSTS_LIMITE` >= 1 (posts por termo em cada busca; ausente, `0` ou inválido = desligado, sem derrubar a app). `limite` vai como `maxPosts`, como `maxItems` do run e, em dólar, como `maxTotalChargeUsd` (mínimo de US$ 0,01 exigido pelo actor), então o Apify nunca cobra além disso. Reações, comentários e o perfil completo do autor (cobrados à parte) ficam desligados. Cada post vira uma vaga com a URL estável `https://www.linkedin.com/feed/update/urn:li:activity:{id}/`, o título da primeira linha útil do texto (sem emoji e sem linha só de hashtag, até 120 caracteres; sem nenhuma, "Vaga em publicação de <empresa>"), a empresa só quando o autor é página de empresa (nome de pessoa nunca vira empresa), `remoto` pelo texto (remoto/home office = sim, híbrido/presencial = não, nada = não informado) e o texto do post como descrição. Post de quem procura emprego (`#opentowork`, "estou em busca", "procuro oportunidade", "estou disponível"), de curso/mentoria ("mentoria", "inscrições", "link na bio", "curso gratuito/online"; "curso" sozinho não, porque "curso superior completo" é requisito de vaga) ou sem verbo de contratação ("vaga", "contratando", "oportunidade", "hiring", "buscamos", "procuramos") é descartado, comparando por palavra sem acento e caixa.

Custo dos posts (plano Free, out/2026): US$ 0,002 por post, US$ 0,00005 por run e US$ 0,001 por consulta sem resultado. Com 6 termos e 2 coletas por dia: 6 × `APIFY_POSTS_LIMITE` × 2 × 30 = 360 posts por mês para cada post por termo, ou seja **~US$ 0,72/mês com `APIFY_POSTS_LIMITE=1`** (até ~US$ 0,38 a mais se as consultas vierem vazias). O crédito Free é de US$ 5/mês e ~US$ 4,30 já ficam com o LinkedIn de vagas (`APIFY_LIMITE=6`): ligar os posts com 1 por termo pede baixar `APIFY_LIMITE` para 5 antes, senão o crédito acaba no fim do mês. Para ligar: `APIFY_POSTS_LIMITE=1` (e `APIFY_LIMITE=5`) no serviço.

### Por pessoa

Os providers acima são o **padrão do servidor** (variáveis de ambiente). Cada pessoa pode ter os seus na tabela `pessoa_provider` (`migrations/015_pessoa_provider.sql`: `pessoa_id`, `provider`, `config` JSONB). **Tabela vazia para a pessoa = vale o ambiente** (o comportamento de antes; a migration não grava seed). Com pelo menos um provider gravado, valem **só** os dela.

| `provider` | `config` |
|---|---|
| `remotar`, `trampos` | `{}` |
| `greenhouse`, `lever`, `ashby` | `{"empresas": ["slug", ...]}`: obrigatória, até 200, sem vazios, repetidos saem; slug só com letras, números, `-`, `_` e `.` |
| `himalayas` | `{"limite": 1..20}` (opcional; padrão 20) |
| `remotive` | `{"limite": 1..100}` (opcional; padrão 100) |
| `linkedin` | `{"locais": [até 5, em inglês], "limite": 1..APIFY_LIMITE_MAX}`; ausentes valem `APIFY_LOCAIS` e `APIFY_LIMITE` (este nunca acima do teto) |
| `linkedin_posts` | `{"limite": 1..APIFY_POSTS_LIMITE_MAX}`; ausente = 1 |

- Validação **antes** de gravar (`DadoInvalido`, 422; nada é gravado): provider desconhecido, chave desconhecida na config, tipo errado, lista vazia ou acima do teto.
- **Segredo nunca na tabela**: `APIFY_TOKEN`/`APIFY_TOKEN_FILE` seguem só no ambiente. Sem token, o LinkedIn da pessoa fica desligado e aparece em `erros` da busca (`APIFY_TOKEN não definido`).
- **Teto de custo do Apify**: `limite` por pessoa, no máximo `APIFY_LIMITE_MAX` (padrão = 50, o `APIFY_LIMITE_PADRAO`) para o LinkedIn e `APIFY_POSTS_LIMITE_MAX` (padrão 5) para os posts, e no máximo 5 locais (o Apify cobra `limite` por local). Config acima do teto é recusada; se o teto baixar depois, a config gravada é cortada no teto ao buscar. Valor inválido dessas variáveis cai no padrão, sem derrubar a app.
- **Cache da coleta**: a busca de um termo vale por `(config efetiva, termo)`. Duas pessoas com a mesma config (padrões já preenchidos) compartilham a busca; configs diferentes buscam cada uma, e quem usa o ambiente só compartilha com quem usa o ambiente.
- `GET/PUT /perfil/providers` (corpo `{"providers": [{"provider": "ashby", "config": {"empresas": ["posthog"]}}]}`; o `PUT` substitui tudo e `[]` volta ao padrão do servidor; mesmo CSRF das outras rotas JSON) e a seção "Fontes de vaga" em `/tela/perfil`. A coleta (app e CLI) e a busca ao vivo `GET /vagas` usam os providers da pessoa.
- **PostHog**: as vagas dele ficam no Ashby, então a fonte é `ashby` com `posthog` em `empresas`, sem client novo. **Ressalva: o slug `posthog` no Ashby não foi conferido contra a API real** (o host `api.ashbyhq.com` está bloqueado neste ambiente); os testes usam uma resposta escrita à mão.

`BuscaService` consulta todos, remove vagas repetidas pela URL e registra em `erros` os providers que falharam, sem interromper a busca:

```python
from src.client.greenhouse import GreenhouseClient
from src.client.remotar import RemotarClient
from src.service.busca_service import BuscaService

resultado = BuscaService([GreenhouseClient(["stone"]), RemotarClient()]).buscar("python")
```

## Banco

O estado (perfil de busca e currículos) fica num Postgres. O schema está em `migrations/*.sql` (de `001_curriculo_perfil.sql` até `014_pedido_ia_rodizio.sql`, descritas nas seções de coleta, candidaturas, "Detalhe e expiração" e abaixo) e **é aplicado sozinho na subida da app**: as pendentes, em ordem lexicográfica do nome (`001_...`, `002_...`; numere com largura fixa), cada uma numa transação junto com o registro em `schema_migrations` e sob `pg_advisory_lock` (réplicas subindo juntas não aplicam duas vezes; quem chega depois espera).

- Migration com erro é desfeita (nada fica, nem registro) e a app **não sobe**: o erro aparece no log. Banco inacessível na subida é tentado de novo com backoff por ~60 s; depois disso a subida falha (o orquestrador reinicia o container). Por isso `/saude` só responde com o banco no ar.
- Escreva migration idempotente (`IF NOT EXISTS`), sem `BEGIN`/`COMMIT` e sem `CREATE INDEX CONCURRENTLY` (rodam dentro de uma transação). Não edite uma migration já publicada: crie outra.
- As 001–003 foram aplicadas à mão antes desta regra; no primeiro boot rodam de novo sem erro e passam a constar em `schema_migrations`. A coleta por linha de comando (`python -m src.controller.coleta`) não aplica migrations: só a app.
- Aplicar à mão só serve para um banco que a app ainda não tocou: `psql -f migrations/001_curriculo_perfil.sql`.

- `perfil_busca`: uma linha por pessoa (chave `pessoa_id`), gravada por upsert. Os CHECK de `modalidades` e `niveis` espelham `Modalidade` e `Nivel` de `src/model/vaga.py`.
- `curriculo`: `tipo` é `base` ou `adaptado`; adaptado sempre aponta para uma base (`base_id`). A base vigente é a base mais recente; as anteriores ficam como histórico.
- `013_pessoa.sql` cria `pessoa` e põe `pessoa_id` em tudo o que é de uma pessoa (vagas, candidaturas, pedidos e rótulos de IA, currículos, perfil e pausa do lote): o que já existia fica na pessoa 1, a chave de `vaga_encontrada` passa a ser `(pessoa_id, url)` e chaves estrangeiras compostas impedem uma pessoa de usar a vaga de outra. Os repositórios recebem a pessoa no construtor e todo SQL filtra por ela (a fila do worker de IA lê todas), e a pessoa de cada requisição vem do seletor da tela ou do cabeçalho `X-Person-Id`, com a 1 (`PESSOA_PADRAO` em `src/controller/api.py`) como padrão (ver "Pessoa da tela").
- `014_pedido_ia_rodizio.sql` só cria dois índices parciais em `pedido_ia` (idempotente): o do último lote reivindicado de cada pessoa, que o rodízio da cota do `proximo` consulta, e o do teto diário global do lote (ver "Lote" na seção de IA).

`src/repository/conexao.py` conecta com as variáveis `PGHOST`, `PGPORT` (padrão 5432), `PGDATABASE`, `PGUSER` e a senha de `PGPASSWORD` ou do arquivo apontado por `PGPASSWORD_FILE` (no Swarm, `/run/secrets/vagas_db_password`).

## API HTTP

`src/controller/api.py` expõe a API com FastAPI (porta 8000, sem autenticação: só para a rede interna). Localmente:

```bash
uvicorn src.controller.api:app --reload
```

A app de produção lê o Postgres pelas variáveis `PG*` (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` ou `PGPASSWORD_FILE`) e conecta só no primeiro uso, então `/saude` responde mesmo sem banco. Os providers de `/vagas` são Remotar, Trampos, Himalayas e Remotive (os dois últimos podem ser escolhidos por `LATAM_PROVIDERS`: ausente ou vazia = os dois; `nenhum` desliga; lista separada por vírgula, `himalayas` e/ou `remotive`, escolhe; valor com nome desconhecido cai no padrão, os dois, sem derrubar a app); Greenhouse, Lever e Ashby entram quando `GREENHOUSE_EMPRESAS`, `LEVER_EMPRESAS` ou `ASHBY_EMPRESAS` têm a lista de empresas separada por vírgula, e o LinkedIn pelo Apify entra com `APIFY_TOKEN` ou com o arquivo em `APIFY_TOKEN_FILE` (secret do Swarm; a variável vale mais; arquivo ausente ou vazio deixa o LinkedIn desligado sem derrubar a app) (opcionais `APIFY_LOCAIS`, separados por vírgula, padrão `Brazil,Latin America`; sem ela vale a antiga `APIFY_LOCAL`; e `APIFY_LIMITE`, padrão 50 vagas por local em cada busca, que é o que o Apify cobra); os posts do LinkedIn entram com o mesmo token e `APIFY_POSTS_LIMITE` >= 1 (padrão 0 = desligado). O `Armazenamento` (S3 do Garage) usa `S3_ENDPOINT` (no Pi, `http://garage:3900`: o botocore recusa host com `_`), `S3_REGION` (padrão `garage`), `S3_BUCKET` (padrão `vagas`), `S3_ACCESS_KEY_ID` e `S3_SECRET_ACCESS_KEY` ou `S3_SECRET_ACCESS_KEY_FILE`. Em testes, `criar_app(perfis, curriculos, busca)` recebe os repositórios em memória.

**Pessoa nas rotas JSON.** Sem o cabeçalho `X-Person-Id` (ou o cookie do seletor) vale a pessoa 1; ver "Pessoa da tela".

**CSRF nas rotas JSON.** Toda rota que muda estado fora de `/tela` (`PUT /perfil`, `PUT /curriculos/base`, `POST /curriculos/adaptados`, `POST /coletas`, `POST /vagas/vistas`, `POST`/`PATCH`/`DELETE /candidaturas`) exige `Content-Type: application/json`, mesmo sem corpo (o `DELETE` e o `POST /coletas` também), senão 415. Além disso: `Sec-Fetch-Site`, se vier, tem que ser `same-origin` ou `none`, e `Origin`, se vier, tem o mesmo host do `Host` (`Origin: null` não passa), senão 403. Sem `Origin` nem `Sec-Fetch-Site` (curl, timer) passa. Exemplo: `curl -X POST -H 'Content-Type: application/json' -d '{}' http://host:8000/coletas`. A regra é uma dependência do app (`src/controller/seguranca_json.py`), então rota JSON mutável nova já nasce protegida; `/tela` tem a proteção própria do HTMX e `/interno/*` (Bearer) fica de fora.

| Método e rota | O que faz |
|---|---|
| `GET /saude` | `{"ok": true}` |
| `GET /perfil` | perfil de busca salvo (404 se não houver) |
| `PUT /perfil` | salva o perfil; modalidades e níveis com os valores dos enums (`"Remoto"`, `"Júnior"`), senão 422 |
| `GET /curriculos/base` | currículo base vigente (404 se não houver) |
| `PUT /curriculos/base` | `{titulo, conteudo_md, arquivo_s3?}`: cria uma nova versão da base |
| `GET /curriculos/adaptados` | lista os currículos adaptados |
| `POST /curriculos/adaptados` | `{titulo, conteudo_md, vaga_url, base_id?, pedido_id?}`: sem `base_id` usa a base vigente (409 se não houver base). O worker manda também o `pedido_id` (ver "Currículo adaptado do worker"): a pessoa é a do pedido; sem ele vale a pessoa da requisição |
| `GET /vagas?termo=...` | busca nos providers (o Remotar, cuja busca é frouxa, corta título sem nenhuma palavra do termo), aplica o perfil: exclui termos do título, só remotas se a modalidade for só Remoto, corta vagas mais antigas que `publicada_desde_dias`, corta nível fora de `niveis` quando o título diz o nível, mantém só a cidade de `localidades` ou remotas (vaga sem local fica) e ordena pela relevância do título (fração das palavras do termo que ele tem), desempatando pelas `skills` que aparecem no título; devolve `{vagas, erros}` |

## Coleta

`GET /vagas` busca ao vivo a cada chamada. A coleta roda a mesma recomendação para cada termo do perfil de cada pessoa (termos repetidos por grafia, como `Front-end` e `front end`, rodam uma vez) e guarda as vagas na tabela `vaga_encontrada` de `migrations/002_vaga_encontrada.sql` (idempotente, aplicada sozinha na subida). A chave é a URL: a mesma vaga achada por dois termos é uma linha só, com os dois em `termos`; uma coleta seguinte atualiza `ultima_vez_em` e os campos que vieram preenchidos, sem contar a vaga como nova e sem mexer em `primeira_vez_em` nem em `vista_em`. Nova = ainda não marcada como vista.

**Várias pessoas.** A coleta cobre todas as pessoas da tabela `pessoa`, uma depois da outra, dentro da mesma trava; pessoa sem perfil ou sem termos é pulada e não atrapalha as outras. Cada termo (normalizado: `Front-end` = `front end`) é buscado nos providers uma vez por coleta, mesmo com várias pessoas, para não pagar o Apify duas vezes: o cache é só em memória e guarda o resultado cru, e o filtro do perfil, o registro e o lote da IA são de cada pessoa (a mesma URL achada para duas pessoas são duas linhas). A CLI imprime uma linha por pessoa e termo (`pessoa 2 | python: 20 encontradas, 3 novas`), sempre com o id, nunca o nome.

| Método e rota | O que faz |
|---|---|
| `POST /coletas` | roda a coleta (de todas as pessoas) e devolve, da pessoa da requisição, `{termos: [{termo, encontradas, novas, erros}], novas}`; 422 se nenhuma pessoa tem perfil com termos; provider que falha aparece em `erros` do termo e não interrompe |
| `GET /vagas/encontradas?novas=true&limite=50&q=&deslocamento=0` | vagas guardadas com o perfil salvo aplicado na leitura (ver "Histórico pelo perfil" abaixo); sem perfil salvo, primeiro as achadas mais recentemente; `novas=false` inclui as já vistas; `q` filtra por palavra (título, empresa, local ou plataforma: `linkedin` traz as do LinkedIn); `limite` de 1 a 500 (fora disso, 422) |
| `POST /vagas/vistas` | `{"urls": [...]}` (até 500): marca como vistas e devolve `{"marcadas": n}`, só as que ainda não estavam |

### Coleta agendada (na app)

A própria app coleta às 08:00 e às 18:00 de Brasília (`COLETA_HORARIOS`, `HH:MM` separados por vírgula; fuso `America/Sao_Paulo`). Vazia desliga; inválida gera um aviso no log e desliga, sem derrubar a app. Uma thread daemon (`src/service/agendador_coleta.py`) é iniciada no lifespan da app de produção, depois das migrations, e parada no shutdown; `criar_app` sem `agendador` (testes) não agenda nada.

O log da app (logger `src`: migrations, coleta agendada, pedidos de IA) sai no stderr em INFO, ou no nível de `LOG_NIVEL` (`DEBUG`, `INFO`, `WARNING`...; inválido cai em INFO). Só contagens e ids, nunca vaga, termo ou dado do perfil. O root e as bibliotecas ficam em WARNING: em INFO o httpx logaria a URL do Apify, que leva o token.

- É a mesma coleta do `POST /coletas`: a mesma trava (outra coleta rodando = "em andamento") e, no fim, o lote da IA de cada pessoa.
- Cada horário é reservado em `coleta_agendada` (`migrations/008_coleta_agendada.sql`) ANTES de coletar, com `INSERT ... ON CONFLICT DO NOTHING`: só quem insere coleta, então um reinício ou uma segunda réplica nunca rodam o mesmo horário.
- Recuperação: ao subir e a cada volta (no máximo 5 min), o horário mais recente que passou há até 6 h e não foi reservado roda (Pi desligado ou app reiniciando no horário). Mais velho que isso fica para o próximo.
- O registro guarda só contagens somadas de todas as pessoas, sem id nem nome (`ok termos=6 novas=12 termos_com_erro=1`) ou o motivo (`em andamento`, `sem perfil ou sem termos`, `erro <Tipo>`); o log também. Banco fora ao reservar: tenta de novo em 60 s, sem marcar o horário. Uma coleta que falha nunca derruba a thread.
- A CLI `python -m src.controller.coleta` continua para rodar à mão.

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

As vagas em que a pessoa aplicou ficam na tabela `candidatura` de `migrations/003_candidatura.sql` (idempotente, aplicada sozinha na subida, depois da 002). A chave é a URL da vaga, que tem que existir em `vaga_encontrada` (chave estrangeira): marcar de novo a mesma vaga devolve a candidatura que já existe. O status é um de `enviada` (padrão), `em_analise`, `entrevista`, `recusada`, `oferta`; fora disso, 422. Aplicada pode ser desfeita (`DELETE`).

| Método e rota | O que faz |
|---|---|
| `POST /candidaturas` | `{"vaga_url": "..."}`: 201 se criou, 200 se já existia; 422 se a vaga não está no histórico da coleta |
| `GET /candidaturas` | lista, a mais recente primeiro, com `titulo` e `empresa` da vaga e `status_rotulo` (`Em análise`) |
| `PATCH /candidaturas/{id}` | `{"status": "entrevista"}`; 404 se não existe, 422 se o status é inválido |
| `DELETE /candidaturas/{id}` | desfaz: 204, ou 404 se não existe |

## Tela

`GET /` redireciona para `/tela`, uma página HTML renderizada no servidor (Jinja2) com HTMX: `src/templates` (`base.html`, `tela/index.html` e os partials `tela/_*.html`) e `src/static` (`tela.css`, `htmx.min.js`, servidos em `/static`). O htmx 2.0.4 é servido local, com versão, licença e sha256 registrados em `src/static/README.md`. Os caminhos saem de `Path(__file__)`, não do diretório atual.

- A lista da tela é a do histórico pelo perfil (seção "Histórico pelo perfil"): a mesma leitura da rota JSON, com o mesmo contador.
- Esqueleto: header, lista, detalhe e "Vagas aplicadas" em estado vazio; "Coletar agora" e "Meu perfil" já ativos. Lista, busca, coleta e candidaturas entram nos PRs seguintes só preenchendo os partials.
- `src/controller/tela_seguranca.py` guarda o que as rotas de `/tela` compartilham: o `Jinja2Templates` (autoescape ligado), o filtro `url_segura` (só `http`/`https` com host vira `href`) e a dependência de CSRF das rotas que mudam estado (`HX-Request: true` e `Origin` igual ao host, ou, sem `Origin`, `Sec-Fetch-Site: same-origin`; senão 403).
- Respostas de `/tela` e `/static` levam `Content-Security-Policy: default-src 'self'; ...`, `X-Content-Type-Options: nosniff` e `Referrer-Policy: same-origin`; `/tela` também `Cache-Control: no-store`. Por isso não há JS nem CSS inline.
- `DadoInvalido` em rota de `/tela` vira 422 com o partial `_erro.html`; nas rotas JSON continua `{"detail": ...}`. Qualquer outro erro é 500.

### Pessoa da tela

Não há login: a tela e a API JSON mostram os dados da pessoa escolhida, e a escolha se faz assim (nesta ordem):

1. Cabeçalho `X-Person-Id: <id>` (para a API JSON e scripts, também vale em `/tela`). Se vier, vale ele; tem que ser o id inteiro positivo de uma pessoa que existe, senão 422 (`{"detail": ...}` na API, o aviso na tela). Vale mais que o cookie.
2. Cookie `vagas_pessoa`, gravado pelo seletor. Valor inválido ou de pessoa que não existe é ignorado (cai na pessoa 1), sem derrubar a tela.
3. Senão, a pessoa 1 (`PESSOA_PADRAO`). Sem cookie nem cabeçalho nada muda em relação a antes.

O **seletor** fica no cabeçalho da tela, ao lado de "Meu perfil", e só aparece com mais de uma pessoa cadastrada. Lista as pessoas pelo nome de exibição (escapado na página; o nome nunca vai para log) com a escolhida marcada. Trocar é `POST /tela/pessoa` (campo `pessoa_id`, HTMX, mesma proteção de CSRF das outras mutações de `/tela`; pessoa inexistente é 422): grava o cookie (`HttpOnly`, `SameSite=Lax`, `Path=/`, 1 ano) e responde `HX-Redirect: /tela`. Cada página da tela manda, em toda requisição do HTMX, o `X-Person-Id` da pessoa que ela mostra (`hx-headers` no `<body>`): com o seletor trocado em outra aba, o "Salvar" de uma aba aberta antes grava na pessoa que estava nela, nunca na do cookie novo.

Tudo o que é da pessoa usa a escolhida: lista, detalhe, salvas, candidaturas, IA, "Meu perfil" (inclusive as fontes de vaga), currículos e as rotas JSON `/perfil`, `/perfil/providers`, `/curriculos/*`, `/vagas`, `/vagas/encontradas`, `/vagas/vistas`, `/candidaturas`, `/vagas/rotulo-ia`, `/ia/metricas` e `POST /coletas` (coleta todas as pessoas e devolve o resultado da escolhida). Ficam como estão: `/pedidos-ia/*` (fila global do worker, Bearer), a coleta agendada, a CLI e a conferência. No `POST /curriculos/adaptados`, com `pedido_id` a pessoa é a dona do pedido; sem ele, a escolhida. Um teste percorre as rotas do app e falha se uma rota de `/tela` ou JSON (fora das exceções explícitas) não usar a dependência `pessoa_da_requisicao`.

> **Isto não é autenticação.** Quem alcança a app na rede escolhe qualquer pessoa (cookie ou cabeçalho) e vê o perfil, os currículos e as candidaturas dela. O login do PR #68 (senha e sessão) substitui este seletor.

### Vagas salvas (a bandeirinha)

Cada card e o detalhe da vaga têm uma bandeirinha (como a do Instagram) para salvar a vaga e ver depois; a aba **Salvas · N**, ao lado de Todas e Novas, lista só as salvas (`/tela?salvas=1`). `migrations/009_vaga_salva.sql` acrescenta `vaga_encontrada.salva_em` (NULL = não salva), aplicada sozinha na subida.

- `POST /tela/vagas/salva` (`url`, `salvar=1|0`) responde só com trocas OOB: a bandeirinha do card e a do detalhe dessa vaga e o número da aba. A lista não é redesenhada: na aba, a vaga tirada dos salvos fica até a próxima leitura, para um clique errado ter volta. Salvar de novo guarda a data da primeira vez.
- A aba vem da salva mais recente para a mais antiga, **sem** os filtros do perfil (salvou de propósito), sem a ordem nem o corte da IA, e **com** as expiradas, marcadas como "encerrada". A busca e o "carregar mais" funcionam nela.
- "Marcar como aplicada" (tela e `POST /candidaturas`) tira a vaga das salvas: ela passa para "Vagas aplicadas".
- A bandeirinha do card fica fora do link (botão dentro de `<a>` não é HTML válido): clicar nela não abre a vaga.

### Meu perfil (`/tela/perfil`)

O botão "Meu perfil" do header leva a `GET /tela/perfil` (página inteira; com `HX-Request`, só o formulário). Os campos são os do perfil JSON: termos, excluir, localidades, plataformas e skills em textareas (uma entrada por linha, linha em branco ignorada), modalidades e níveis em checkboxes com os valores dos enums, `publicada_desde_dias` (vazio = sem limite), `ocultar_candidatadas` e `salario_minimo` (reais por mês, só dígitos; vazio = sem piso; não filtra a coleta, vai para a IA da compatibilidade). Sem perfil salvo o formulário vem vazio.

- Salvar é `PUT /tela/perfil` (HTMX, nunca GET), protegido pelo mesmo CSRF das outras mutações da tela. Usa o mesmo repositório e a mesma validação de `PUT /perfil` (`validar_perfil`): `DadoInvalido` vira o aviso 422 e não grava nada; repetidos saem na normalização.
- Depois de salvar, o formulário é relido do repositório e mostra "Perfil salvo" com o link de volta para a lista, que já obedece ao perfil na leitura.
- Cada lista aceita até 200 itens de até 200 caracteres. O teto está em `validar_perfil`, então vale igual no `PUT /perfil` (JSON): o que uma rota grava a outra aceita de volta, e o 422 diz qual lista passou.

## Detalhe e expiração

`migrations/004_detalhe_vaga.sql` (idempotente, `ADD COLUMN IF NOT EXISTS`) acrescenta a `vaga_encontrada`: `descricao`, `requisitos`, `salario`, `beneficios`, `subtitulo`, `expirada_em` e `detalhada_em`. Sem a 004 aplicada a coleta e a tela quebram: ela é aplicada sozinha na subida se o PR das migrations automáticas estiver na main; senão, à mão antes do merge (`psql -f migrations/004_detalhe_vaga.sql`).

- **Gravado na coleta**: a listagem do Remotar já traz descrição, requisitos, salário, benefícios, `expired` e `expiresAt`; a do LinkedIn (Apify) traz a descrição (`descriptionHtml` sanitizado ou, sem ele, `descriptionText`, com o mesmo teto de caracteres), e as do Himalayas e do Remotive trazem a descrição (`description` sanitizado, mesmo teto). `registrar` grava (e atualiza no conflito) esses campos, já sanitizados, e `detalhada_em`. Vaga sem descrição (outro provider, ou item do LinkedIn sem ela) não apaga o que já está gravado.
- **Sob demanda (reserva)**: só quando `detalhada_em` é nulo. `GET /tela` e o clique no card NUNCA chamam o provider: o detalhe sai na hora com "Carregando a descrição…" e a seção pede `GET /tela/vagas/secoes?url=` (`hx-trigger="load"`), que busca `GET https://api.remotar.com.br/jobs/{id}` (timeout 5 s), grava e troca as seções. Falha vira aviso no lugar das seções, nunca 500; 404/410 grava `expirada_em`.
- **Só o id certo chega ao provider**: a URL da vaga tem que ser exatamente `https://remotar.com.br/job/{dígitos}` e a plataforma `Remotar`, ou `https://www.linkedin.com/jobs/view/{dígitos}` e a plataforma `LinkedIn (Apify)` (cada plataforma só chama o próprio provider); outro host, caminho, query ou fragmento nunca chamam o provider.
- **LinkedIn**: a coleta pelo Apify não grava a descrição; ela vem da página pública da vaga, sem login (`GET https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}`, User-Agent de navegador, timeout 5 s, só o texto de `show-more-less-html__markup`), numa requisição por vaga aberta e só quando falta a descrição. O LinkedIn limita forte (a 4ª requisição seguida já deu 429): qualquer falha, inclusive 404 e 429, vira o aviso e a próxima abertura tenta de novo, sem nunca marcar a vaga como expirada.
- **Texto puro**: tudo passa por `src/client/sanitizar.py` (`html.parser` da stdlib): tags somem (`<script>`/`<style>` com o conteúdo), `<a>` fica só com o texto, parágrafos e `<br>` viram quebra de linha, `<li>` vira "• ", até 20 mil caracteres. A tela imprime com autoescape e `white-space: pre-line`, sem `|safe`. Campo de resposta com forma errada descarta só o campo; qualquer erro de parsing vira `ErroProvider`.
- **Salário** em centavos (confirmado em 2026-10-12 na página pública da vaga 164153: 900000 a 1300000 aparece como "R$ 9,000.00 a R$ 13,000.00"). `from`/`to` nulo ou 0 e `type: "uninformed"` = não informado; só um lado positivo vira "a partir de" ou "até".
- **Expiração (só Remotar)**: `expirada_em` = `expiresAt` (mesmo futuro) ou, com `expired: true`, agora. A vaga some de `listar`, `contar` e `listar_recentes` quando `expirada_em <= now()` (`expirada_em IS NULL OR expirada_em > now()`), sem nova coleta. `obter(url)` ainda a acha e o link direto mostra "Vaga expirada" (só quando já venceu); candidaturas continuam visíveis. Quem já expirou não ressuscita com um prazo novo nem com "sem prazo"; o prazo futuro pode mudar ou ser removido na coleta seguinte: com `expires: false` (e sem `expiresAt` nem `expired: true`) o prazo futuro gravado antes é apagado e a vaga segue no ar. Campo ausente ou torto mantém a data que estava.

## IA (compatibilidade e currículo)

A tela pede dois tipos de trabalho à IA para uma vaga: `compatibilidade` (nota, faixa e o que atende ou falta) e `curriculo` (currículo adaptado a partir da base). O vagas-svc não roda IA: guarda uma fila (`pedido_ia`, `migrations/006_pedido_ia.sql`, idempotente, aplicada sozinha na subida como as outras; o buraco no 005 é proposital) e um worker do stack `claude-pi` consome por polling. A tela só enfileira.

Fluxo: `pendente` → (`POST /pedidos-ia/proximo`) → `rodando` → (`PUT /pedidos-ia/{id}`) → `pronto` ou `erro` (os dois são finais). Só há UM pedido ativo por `(vaga_url, tipo)`: pedir de novo devolve o ativo. Depois que ele termina, pedir cria um pedido novo (reexecução); os antigos ficam, e servem de base para a métrica de estabilidade.

| Método e caminho | Quem usa | O que faz |
|---|---|---|
| `POST /pedidos-ia/proximo` | worker (Bearer) | corpo `{}`; reivindica um pedido de qualquer pessoa (a fila é uma só, com rodízio no lote: ver "Rodízio da cota"): `200` com o pedido montado com os dados da pessoa dele, `204` com a fila vazia |
| `PUT /pedidos-ia/{id}` | worker (Bearer) | devolve o resultado ou o erro: `200 {id, estado}`, `404`, `409` (não está rodando ou a tentativa não é a atual), `422` (nada muda) |
| `PUT /vagas/rotulo-ia` | JSON aberto | `{"vaga_url", "faixa": "alta"\|"média"\|"baixa"\|null}`: grava o rótulo da pessoa (`null` apaga); `422` com `media` sem acento ou vaga fora do histórico |
| `GET /ia/metricas?prompt_versao=` | JSON aberto | concordância de faixa, correlação de ordem e estabilidade de reexecução (chega com o PR das métricas) |
| `GET/POST /tela/vagas/ia`, `GET /tela/vagas/ia/{id}`, `GET /tela/vagas/ia/{id}/curriculo.pdf`, `GET /tela/vagas/ia/{id}/curriculo.md`, `POST /tela/vagas/ia/rotulo` | tela (HTMX, CSRF) | botões Compatibilidade e Gerar currículo, polling de 3 s, resultado, download do currículo (PDF para ATS ou .md) e rótulo |

As rotas `/pedidos-ia/*` não aparecem no `/docs`. Respondem com `Cache-Control: no-store`, e o servidor nunca loga `resultado`, `erro`, currículo nem descrição: só id, tipo, estado e tentativa. Toda escrita da API usa `Content-Type: application/json` (`415` sem ele).

### Pedido que o worker recebe

```json
{
  "id": 12, "tipo": "compatibilidade", "tentativa": 1,
  "vaga": {"url": "...", "titulo": "...", "empresa": "...", "plataforma": "Remotar", "local": "...", "remoto": true,
           "publicada_em": "2026-10-01", "descricao": "texto puro ou null",
           "requisitos": [{"texto": "SQL avançado", "obrigatorio": true}], "subtitulo": null},
  "curriculo_base": {"id": 3, "titulo": "...", "conteudo_md": "..."},
  "perfil": {"termos": [], "excluir": [], "modalidades": [], "localidades": [], "plataformas": [], "niveis": [],
             "skills": [], "publicada_desde_dias": null, "ocultar_candidatadas": true}
}
```

`descricao`, `requisitos` e `subtitulo` vêm do detalhe da vaga, buscado quando a pessoa abre a vaga na tela; sem ele saem `null`, `[]` e `null`. O pedido de **currículo** busca antes o detalhe que faltar (o mesmo `carregar` de abrir a vaga), porque o worker adapta o currículo pelo texto e no idioma da vaga (`curriculo-v3`); se o provider falhar (ex.: LinkedIn limitando), o pedido é recusado com o aviso 422 "abra a vaga de novo mais tarde" em vez de seguir sem descrição. Plataforma sem detalhe sob demanda segue como antes. A compatibilidade (inclusive o lote) não busca nada. O `proximo` não busca nada no provider. `perfil` é `null` sem perfil salvo. `vaga`, `curriculo_base` e `perfil` são da pessoa dona do pedido reivindicado (`pedido_ia.pessoa_id`), não da pessoa da requisição: cada chamada de repositório do `proximo` usa uma conexão do pool, como o resto da API. `curriculo_base` é a base vigente dela: sem base, o pedido vira `erro` ("Não há currículo base") e o `proximo` passa ao seguinte.

### Resultado (`PUT /pedidos-ia/{id}`)

```json
{"tentativa": 1, "estado": "pronto", "prompt_versao": "compatibilidade-v1", "modelo": "claude-sonnet-5-5", "resultado": {}}
{"tentativa": 1, "estado": "erro",   "prompt_versao": "compatibilidade-v1", "modelo": "claude-sonnet-5-5", "erro": "texto curto"}
```

- O corpo recusa chaves extras. `pronto` exige `resultado` e proíbe `erro`; `erro` exige o texto e proíbe `resultado`. O servidor corta `erro` em 500 caracteres (não recusa). `prompt_versao` casa `^[a-z]+-v[0-9]{1,6}$` e começa com o tipo do pedido; `modelo` tem de 1 a 100 caracteres, sem caractere de controle (NUL viraria 500 no Postgres). `resultado` com mais de 64 KB é `422`.
- O `PUT` só vale com o pedido `rodando` e `tentativa` igual à atual; senão `409`.
- `compatibilidade-v1`: `{nota 0-100 (inteiro; bool não vale), faixa, atende[{requisito, evidencia_no_curriculo}], falta[{requisito, obrigatorio}], eliminatorios[], resumo}`, sem chaves extras, com até 30 `atende`, 30 `falta` e 20 `eliminatorios`, textos sem espaço nas pontas e não vazios. A faixa tem que bater com a nota: `alta` 70-100, `média` 40-69, `baixa` 0-39 (cortes provisórios, a calibrar pelas métricas; mudá-los exige um prompt `-v2`). A grafia é sempre `média`; só o worker normaliza `media`. O servidor grava `nota` e `faixa` nas colunas. Que cada `evidencia_no_curriculo` seja um trecho literal do currículo é regra só do worker: o servidor não tem o currículo da época.
- `curriculo-v1`: o worker grava o adaptado em `POST /curriculos/adaptados` e manda `resultado = {curriculo_id, titulo, o_que_mudou, lacunas}` (sem o `conteudo_md`). O servidor confere que `curriculo_id` existe, é `adaptado`, tem a mesma `vaga_url` do pedido e é da mesma pessoa do pedido (senão `422`) e grava `curriculo_id`. Se o worker morrer entre o `POST` e o `PUT`, a reentrega cria um segundo adaptado (duplicata aceita no v1).

### Currículo adaptado do worker (`pedido_id`)

O `POST /curriculos/adaptados` não tem Bearer nem pessoa na requisição além da padrão, e o worker atende pedidos de todas as pessoas. Por isso o worker passa a mandar o `id` do pedido que está atendendo:

```json
{"titulo": "...", "conteudo_md": "...", "vaga_url": "...", "base_id": 3, "pedido_id": 12}
```

- **Mudança no worker**: incluir `pedido_id` (o `id` que veio no `POST /pedidos-ia/proximo`) em todo `POST /curriculos/adaptados`. É o único ponto do worker que muda. O campo é opcional para não quebrar o worker atual: sem ele o currículo vai para a pessoa da requisição (hoje a pessoa 1), o que errará assim que existir um pedido de outra pessoa.
- Com `pedido_id` o servidor tira a pessoa do pedido e grava o adaptado nela, com a base dela (`base_id`, se vier, tem de ser base dessa pessoa: senão `404`).
- `422` se o pedido não existe, se é de outra `vaga_url`, se não é de currículo ou se não está `rodando` (a rota é aberta; sem isso qualquer id gravaria na pessoa dona dele).

### Currículo em PDF (ATS)

`GET /tela/vagas/ia/{id}/curriculo.pdf` gera o PDF do currículo adaptado na hora, a partir do `conteudo_md` (`src/service/curriculo_pdf.py`, `fpdf2`). O layout é o que os ATS leem sem errar: A4, uma coluna, Helvetica, texto selecionável (nada de imagem), nome no topo, contato na linha de baixo, seções em maiúsculas com uma linha fina, cargo em negrito e tópicos com hífen; sem tabela, cabeçalho, rodapé, ícone nem cor. Ênfase em par e crases viram texto puro, o link `[texto](url)` vira `texto (url)` (sem `mailto:`), e URL e código passam intactos; a fonte padrão do PDF só tem Latin-1 (cobre o português), e travessão, aspas curvas e marcador viram equivalentes ASCII. O nome do arquivo é `Curriculo - <título>.pdf` em ASCII (o `filename*` leva o título com acentos, em UTF-8, sem caractere de controle nem barra). O `.md` continua disponível.

### Pedido preso

Rodando há mais de 15 minutos (`reivindicado_em`) é pedido preso: o worker morreu. O `proximo`, numa transação, (1) marca como `erro` ("O worker não terminou depois de 3 tentativas") o preso que já gastou 3 tentativas, (2) escolhe um pendente ou preso (pendente primeiro; a prioridade; no lote, o rodízio de pessoas; depois o mais antigo; `FOR UPDATE SKIP LOCKED`, então dois workers nunca pegam o mesmo) e (3) marca `rodando`, soma 1 em `tentativas` e grava `reivindicado_em`. A tentativa antiga que chegar depois no `PUT` recebe `409`.

### Lote: a IA avalia as vagas do histórico

Além do clique da pessoa, a compatibilidade roda em lote, uma vaga por vez, pela mesma fila e pelo mesmo worker (`migrations/007_pedido_ia_lote.sql`).

- **Quem dispara**: o fim de toda coleta bem-sucedida (`POST /coletas`, o botão da tela e `python -m src.controller.coleta`, que o timer do Pi roda às 08:00 e 18:00; a CLI imprime `ia: N vagas na fila` depois das linhas dos termos, e falha do lote vai para o stderr sem mudar o código de saída) e o botão "Avaliar vagas com IA" da tela (`POST /tela/ia/lote`, com o CSRF da tela). Falha ao enfileirar só vai para o log: a coleta não falha por causa disso. Um enfileiramento por vez (trava no Postgres): quem chega com outro rodando espera a vez (até 30 s) e então enfileira as vagas dele, com o teto relido dentro da trava.
- **Rodízio da cota**: para uma pessoa não gastar a cota da outra, o `proximo` alterna as pessoas entre os pedidos do lote (prioridade 1): entre os pendentes do lote vai primeiro a pessoa cujo último pedido de lote foi reivindicado há mais tempo (ou que nunca teve um) e, dentro dela, o mais antigo. O clique da pessoa (prioridade 0) continua passando na frente de tudo e não conta no rodízio; o preso (rodando há mais de 15 min) segue a regra de sempre. Com uma pessoa só a ordem é a de antes.
- **Por pessoa**: o enfileiramento, a pausa, os rótulos, a nota válida e as métricas são da pessoa da requisição (`GET /ia/metricas` e os rótulos só enxergam os pedidos e rótulos dela). O que é global é a fila do worker, o rodízio e o teto diário.
- **Quais vagas**: as da lista da tela (as que passam no perfil, sem as expiradas, na mesma ordem), menos as que já têm pedido de compatibilidade ativo e as que já têm nota válida.
- **Nota válida**: pronto de compatibilidade feito com o currículo base vigente (`curriculo_base_id`, gravado quando o pedido é criado) e com a versão atual do prompt (a maior `compatibilidade-vN` já vista nos prontos). Trocou a base ou subiu a versão, a vaga volta para o lote. Pedido de antes da 007 não tem base gravada e não conta.
- **Prioridade**: o clique da pessoa (`prioridade` 0) passa na frente do lote (1) na fila; pedir pela tela uma vaga que já está no lote sobe o pedido para a prioridade da pessoa.
- **Teto**: por padrão não há (o lote enfileira todas as vagas que faltam). Com `IA_LOTE_MAX_POR_DIA` >= 1, no máximo tantos pedidos do lote criados nas últimas 24 h, somando TODAS as pessoas (`migrations/014_pedido_ia_rodizio.sql` traz o índice da contagem); `0` desliga o lote; ausente, vazio ou inválido cai no padrão (sem teto). A cota é a da assinatura do worker, que as pessoas dividem (por isso o teto é global e há o rodízio abaixo): cota estourada deixa o pedido `rodando` e o worker espera, como no clique.
- **Na tela**: a barra da lista mostra `IA: N avaliadas · M na fila` (entre as vagas da lista). Com teto, mostra também `restam N hoje`; teto cheio troca o "0 vagas na fila da IA" por "Limite de N avaliações da IA em 24 h atingido. Tente mais tarde." e desabilita o botão; com `0`, diz que o lote está desligado.
- **Pausar**: o botão "Pausar avaliação" (`POST /tela/ia/lote/pausar`) grava a pausa no banco (`migrations/010_ia_lote_pausa.sql`, uma linha por pessoa; sobrevive a um reinício sem deploy) e apaga, numa transação, os pedidos de compatibilidade do lote ainda **pendentes**. O que está rodando termina; o clique da pessoa ("Compatibilidade"), o currículo e o que já terminou ficam. Pausado, nem a coleta nem o botão enfileiram (o enfileiramento devolve 0), e a barra mostra `· pausada` e só "Retomar avaliação" (`POST /tela/ia/lote/retomar`), que tira a pausa e já enfileira as vagas sem nota. Pausar usa a mesma trava do enfileiramento: uma coleta enfileirando termina antes, e nada que ela criar fica para trás.
- **Ranking**: com alguma nota válida, a lista da tela vem por padrão em "Compatibilidade (IA)": as avaliadas primeiro, da maior nota para a menor (empate: a ordem do perfil), depois as sem nota na ordem do perfil. O card mostra a nota e a faixa (`82 · alta`) ou "IA na fila". `?ordem=perfil` volta à ordem do perfil; `?faixa=alta` ou `?faixa=media` deixa só as avaliadas nessa faixa ou acima (`todas` ou vazio tira o corte). A escolha viaja no card, no "carregar mais", na busca e na barra do navegador. As notas saem de uma consulta por página. Uma nota que chega entre duas páginas do "carregar mais" muda a ordem (a vaga sobe), como uma coleta no meio. A rota JSON `/vagas/encontradas` continua na ordem do perfil.

### Token do worker (Bearer)

`/pedidos-ia/*` exige `Authorization: Bearer <token>`. O token vem do arquivo em `VAGAS_IA_TOKEN_FILE` (no Swarm, `/run/secrets/vagas_ia_token`) ou, na falta dele, de `VAGAS_IA_TOKEN`; a comparação usa `hmac.compare_digest`. Sem token configurado (ausente, ilegível ou vazio) as rotas respondem `503` e a app continua de pé; token ausente ou errado é `401` com `WWW-Authenticate: Bearer`.

Por que Bearer, se o resto da API é aberto: no compose do claude-pi o `vagas` está em mais de uma rede e a tela é publicada na LAN, e estas rotas entregam o currículo e o perfil e aceitam escrita. Abertas, qualquer um na rede poderia reivindicar pedidos ou gravar resultados e envenenar as métricas. O `GET /curriculos/base` aberto é uma dívida que já existe, não um padrão a copiar. O worker lê o mesmo secret; o service `claude` (sandbox) não o recebe.

## Testes

```bash
python -m unittest discover -s tests -t .
```

Os testes de `tests/test_postgres.py` só rodam com `PGHOST` definido; cada um cria um schema descartável, aplica a migration e o apaga no fim. Sem `PGHOST` eles são pulados.
