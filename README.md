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

O estado (perfil de busca e currículos) fica num Postgres. O schema está em `migrations/*.sql` (de `001_curriculo_perfil.sql` até `004_detalhe_vaga.sql`, descritas nas seções de coleta, candidaturas e "Detalhe e expiração") e **é aplicado sozinho na subida da app**: as pendentes, em ordem lexicográfica do nome (`001_...`, `002_...`; numere com largura fixa), cada uma numa transação junto com o registro em `schema_migrations` e sob `pg_advisory_lock` (réplicas subindo juntas não aplicam duas vezes; quem chega depois espera).

- Migration com erro é desfeita (nada fica, nem registro) e a app **não sobe**: o erro aparece no log. Banco inacessível na subida é tentado de novo com backoff por ~60 s; depois disso a subida falha (o orquestrador reinicia o container). Por isso `/saude` só responde com o banco no ar.
- Escreva migration idempotente (`IF NOT EXISTS`), sem `BEGIN`/`COMMIT` e sem `CREATE INDEX CONCURRENTLY` (rodam dentro de uma transação). Não edite uma migration já publicada: crie outra.
- As 001–003 foram aplicadas à mão antes desta regra; no primeiro boot rodam de novo sem erro e passam a constar em `schema_migrations`. A coleta por linha de comando (`python -m src.controller.coleta`) não aplica migrations: só a app.
- Aplicar à mão só serve para um banco que a app ainda não tocou: `psql -f migrations/001_curriculo_perfil.sql`.

- `perfil_busca`: uma linha só (id = 1), gravada por upsert. Os CHECK de `modalidades` e `niveis` espelham `Modalidade` e `Nivel` de `src/model/vaga.py`.
- `curriculo`: `tipo` é `base` ou `adaptado`; adaptado sempre aponta para uma base (`base_id`). A base vigente é a base mais recente; as anteriores ficam como histórico.

`src/repository/conexao.py` conecta com as variáveis `PGHOST`, `PGPORT` (padrão 5432), `PGDATABASE`, `PGUSER` e a senha de `PGPASSWORD` ou do arquivo apontado por `PGPASSWORD_FILE` (no Swarm, `/run/secrets/vagas_db_password`).

## API HTTP

`src/controller/api.py` expõe a API com FastAPI (porta 8000, sem autenticação: só para a rede interna). Localmente:

```bash
uvicorn src.controller.api:app --reload
```

A app de produção lê o Postgres pelas variáveis `PG*` (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` ou `PGPASSWORD_FILE`) e conecta só no primeiro uso, então `/saude` responde mesmo sem banco. Os providers de `/vagas` são Remotar e Trampos; Greenhouse, Lever e Ashby entram quando `GREENHOUSE_EMPRESAS`, `LEVER_EMPRESAS` ou `ASHBY_EMPRESAS` têm a lista de empresas separada por vírgula, e o LinkedIn pelo Apify entra com `APIFY_TOKEN` ou com o arquivo em `APIFY_TOKEN_FILE` (secret do Swarm; a variável vale mais; arquivo ausente ou vazio deixa o LinkedIn desligado sem derrubar a app) (opcionais `APIFY_LOCAL`, padrão `Brasil`, e `APIFY_LIMITE`, padrão 50 vagas por busca, que é o que o Apify cobra). O `Armazenamento` (S3 do Garage) usa `S3_ENDPOINT` (no Pi, `http://garage:3900`: o botocore recusa host com `_`), `S3_REGION` (padrão `garage`), `S3_BUCKET` (padrão `vagas`), `S3_ACCESS_KEY_ID` e `S3_SECRET_ACCESS_KEY` ou `S3_SECRET_ACCESS_KEY_FILE`. Em testes, `criar_app(perfis, curriculos, busca)` recebe os repositórios em memória.

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

`GET /vagas` busca ao vivo a cada chamada. A coleta roda a mesma recomendação para cada termo do perfil salvo (termos repetidos por grafia, como `Front-end` e `front end`, rodam uma vez) e guarda as vagas na tabela `vaga_encontrada` de `migrations/002_vaga_encontrada.sql` (idempotente, aplicada sozinha na subida). A chave é a URL: a mesma vaga achada por dois termos é uma linha só, com os dois em `termos`; uma coleta seguinte atualiza `ultima_vez_em` e os campos que vieram preenchidos, sem contar a vaga como nova e sem mexer em `primeira_vez_em` nem em `vista_em`. Nova = ainda não marcada como vista.

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

### Meu perfil (`/tela/perfil`)

O botão "Meu perfil" do header leva a `GET /tela/perfil` (página inteira; com `HX-Request`, só o formulário). Os campos são os do perfil JSON: termos, excluir, localidades, plataformas e skills em textareas (uma entrada por linha, linha em branco ignorada), modalidades e níveis em checkboxes com os valores dos enums, `publicada_desde_dias` (vazio = sem limite) e `ocultar_candidatadas`. Sem perfil salvo o formulário vem vazio.

- Salvar é `PUT /tela/perfil` (HTMX, nunca GET), protegido pelo mesmo CSRF das outras mutações da tela. Usa o mesmo repositório e a mesma validação de `PUT /perfil` (`validar_perfil`): `DadoInvalido` vira o aviso 422 e não grava nada; repetidos saem na normalização.
- Depois de salvar, o formulário é relido do repositório e mostra "Perfil salvo" com o link de volta para a lista, que já obedece ao perfil na leitura.
- Cada lista aceita até 200 itens de até 200 caracteres. O teto está em `validar_perfil`, então vale igual no `PUT /perfil` (JSON): o que uma rota grava a outra aceita de volta, e o 422 diz qual lista passou.

## Detalhe e expiração

`migrations/004_detalhe_vaga.sql` (idempotente, `ADD COLUMN IF NOT EXISTS`) acrescenta a `vaga_encontrada`: `descricao`, `requisitos`, `salario`, `beneficios`, `subtitulo`, `expirada_em` e `detalhada_em`. Sem a 004 aplicada a coleta e a tela quebram: ela é aplicada sozinha na subida se o PR das migrations automáticas estiver na main; senão, à mão antes do merge (`psql -f migrations/004_detalhe_vaga.sql`).

- **Gravado na coleta**: a listagem do Remotar já traz descrição, requisitos, salário, benefícios, `expired` e `expiresAt`. `registrar` grava (e atualiza no conflito) esses campos, já sanitizados, e `detalhada_em`. Vaga de outro provider não apaga o que já está gravado.
- **Sob demanda (reserva)**: só quando `detalhada_em` é nulo. `GET /tela` e o clique no card NUNCA chamam o provider: o detalhe sai na hora com "Carregando a descrição…" e a seção pede `GET /tela/vagas/secoes?url=` (`hx-trigger="load"`), que busca `GET https://api.remotar.com.br/jobs/{id}` (timeout 5 s), grava e troca as seções. Falha vira aviso no lugar das seções, nunca 500; 404/410 grava `expirada_em`.
- **Só o id certo chega ao provider**: a URL da vaga tem que ser exatamente `https://remotar.com.br/job/{dígitos}` e a plataforma `Remotar`; outro host, caminho, query ou fragmento nunca chamam o provider.
- **Texto puro**: tudo passa por `src/client/sanitizar.py` (`html.parser` da stdlib): tags somem (`<script>`/`<style>` com o conteúdo), `<a>` fica só com o texto, parágrafos e `<br>` viram quebra de linha, `<li>` vira "• ", até 20 mil caracteres. A tela imprime com autoescape e `white-space: pre-line`, sem `|safe`. Campo de resposta com forma errada descarta só o campo; qualquer erro de parsing vira `ErroProvider`.
- **Salário** em centavos (confirmado em 2026-10-12 na página pública da vaga 164153: 900000 a 1300000 aparece como "R$ 9,000.00 a R$ 13,000.00"). `from`/`to` nulo ou 0 e `type: "uninformed"` = não informado; só um lado positivo vira "a partir de" ou "até".
- **Expiração (só Remotar)**: `expirada_em` = `expiresAt` (mesmo futuro) ou, com `expired: true`, agora. A vaga some de `listar`, `contar` e `listar_recentes` quando `expirada_em <= now()` (`expirada_em IS NULL OR expirada_em > now()`), sem nova coleta. `obter(url)` ainda a acha e o link direto mostra "Vaga expirada" (só quando já venceu); candidaturas continuam visíveis. Quem já expirou não ressuscita com um prazo novo nem com "sem prazo"; o prazo futuro pode mudar ou ser removido na coleta seguinte: com `expires: false` (e sem `expiresAt` nem `expired: true`) o prazo futuro gravado antes é apagado e a vaga segue no ar. Campo ausente ou torto mantém a data que estava.

## IA (compatibilidade e currículo)

A tela pede dois tipos de trabalho à IA para uma vaga: `compatibilidade` (nota, faixa e o que atende ou falta) e `curriculo` (currículo adaptado a partir da base). O vagas-svc não roda IA: guarda uma fila (`pedido_ia`, `migrations/006_pedido_ia.sql`, idempotente, aplicada sozinha na subida como as outras; o buraco no 005 é proposital) e um worker do stack `claude-pi` consome por polling. A tela só enfileira.

Fluxo: `pendente` → (`POST /pedidos-ia/proximo`) → `rodando` → (`PUT /pedidos-ia/{id}`) → `pronto` ou `erro` (os dois são finais). Só há UM pedido ativo por `(vaga_url, tipo)`: pedir de novo devolve o ativo. Depois que ele termina, pedir cria um pedido novo (reexecução); os antigos ficam, e servem de base para a métrica de estabilidade.

| Método e caminho | Quem usa | O que faz |
|---|---|---|
| `POST /pedidos-ia/proximo` | worker (Bearer) | corpo `{}`; reivindica um pedido: `200` com o pedido, `204` com a fila vazia |
| `PUT /pedidos-ia/{id}` | worker (Bearer) | devolve o resultado ou o erro: `200 {id, estado}`, `404`, `409` (não está rodando ou a tentativa não é a atual), `422` (nada muda) |
| `PUT /vagas/rotulo-ia` | JSON aberto | `{"vaga_url", "faixa": "alta"\|"média"\|"baixa"\|null}`: grava o rótulo da pessoa (`null` apaga); `422` com `media` sem acento ou vaga fora do histórico |
| `GET /ia/metricas?prompt_versao=` | JSON aberto | concordância de faixa, correlação de ordem e estabilidade de reexecução (chega com o PR das métricas) |
| `GET/POST /tela/vagas/ia`, `GET /tela/vagas/ia/{id}`, `GET /tela/vagas/ia/{id}/curriculo.md`, `POST /tela/vagas/ia/rotulo` | tela (HTMX, CSRF) | botões Compatibilidade e Gerar currículo, polling de 3 s, resultado e rótulo (chegam com o PR da tela) |

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

`descricao`, `requisitos` e `subtitulo` vêm do detalhe da vaga, buscado quando a pessoa abre a vaga na tela; sem ele saem `null`, `[]` e `null`. O `proximo` não busca nada no provider. `perfil` é `null` sem perfil salvo. `curriculo_base` é a base vigente: sem base, o pedido vira `erro` ("Não há currículo base") e o `proximo` passa ao seguinte.

### Resultado (`PUT /pedidos-ia/{id}`)

```json
{"tentativa": 1, "estado": "pronto", "prompt_versao": "compatibilidade-v1", "modelo": "claude-sonnet-5-5", "resultado": {}}
{"tentativa": 1, "estado": "erro",   "prompt_versao": "compatibilidade-v1", "modelo": "claude-sonnet-5-5", "erro": "texto curto"}
```

- O corpo recusa chaves extras. `pronto` exige `resultado` e proíbe `erro`; `erro` exige o texto e proíbe `resultado`. O servidor corta `erro` em 500 caracteres (não recusa). `prompt_versao` casa `^[a-z]+-v[0-9]{1,6}$` e começa com o tipo do pedido; `modelo` tem de 1 a 100 caracteres, sem caractere de controle (NUL viraria 500 no Postgres). `resultado` com mais de 64 KB é `422`.
- O `PUT` só vale com o pedido `rodando` e `tentativa` igual à atual; senão `409`.
- `compatibilidade-v1`: `{nota 0-100 (inteiro; bool não vale), faixa, atende[{requisito, evidencia_no_curriculo}], falta[{requisito, obrigatorio}], eliminatorios[], resumo}`, sem chaves extras, com até 30 `atende`, 30 `falta` e 20 `eliminatorios`, textos sem espaço nas pontas e não vazios. A faixa tem que bater com a nota: `alta` 70-100, `média` 40-69, `baixa` 0-39 (cortes provisórios, a calibrar pelas métricas; mudá-los exige um prompt `-v2`). A grafia é sempre `média`; só o worker normaliza `media`. O servidor grava `nota` e `faixa` nas colunas. Que cada `evidencia_no_curriculo` seja um trecho literal do currículo é regra só do worker: o servidor não tem o currículo da época.
- `curriculo-v1`: o worker grava o adaptado em `POST /curriculos/adaptados` e manda `resultado = {curriculo_id, titulo, o_que_mudou, lacunas}` (sem o `conteudo_md`). O servidor confere que `curriculo_id` existe, é `adaptado` e tem a mesma `vaga_url` do pedido (senão `422`) e grava `curriculo_id`. Se o worker morrer entre o `POST` e o `PUT`, a reentrega cria um segundo adaptado (duplicata aceita no v1).

### Pedido preso

Rodando há mais de 15 minutos (`reivindicado_em`) é pedido preso: o worker morreu. O `proximo`, numa transação, (1) marca como `erro` ("O worker não terminou depois de 3 tentativas") o preso que já gastou 3 tentativas, (2) escolhe um pendente ou preso (pendente primeiro, depois o mais antigo; `FOR UPDATE SKIP LOCKED`, então dois workers nunca pegam o mesmo) e (3) marca `rodando`, soma 1 em `tentativas` e grava `reivindicado_em`. A tentativa antiga que chegar depois no `PUT` recebe `409`.

### Lote: a IA avalia as vagas do histórico

Além do clique da pessoa, a compatibilidade roda em lote, uma vaga por vez, pela mesma fila e pelo mesmo worker (`migrations/007_pedido_ia_lote.sql`).

- **Quem dispara**: o fim de toda coleta (`POST /coletas`, o botão da tela e `python -m src.controller.coleta`, que o timer do Pi roda às 08:00 e 18:00; a CLI imprime `ia: N vagas na fila`) e o botão "Avaliar vagas com IA" da tela (`POST /tela/ia/lote`, com o CSRF da tela). Falha ao enfileirar só vai para o log: a coleta não falha por causa disso.
- **Quais vagas**: as da lista da tela (as que passam no perfil, sem as expiradas, na mesma ordem), menos as que já têm pedido de compatibilidade ativo e as que já têm nota válida.
- **Nota válida**: pronto de compatibilidade feito com o currículo base vigente (`curriculo_base_id`, gravado quando o pedido é criado) e com a versão atual do prompt (a maior `compatibilidade-vN` já vista nos prontos). Trocou a base ou subiu a versão, a vaga volta para o lote. Pedido de antes da 007 não tem base gravada e não conta.
- **Prioridade**: o clique da pessoa (`prioridade` 0) passa na frente do lote (1) na fila; pedir pela tela uma vaga que já está no lote sobe o pedido para a prioridade da pessoa.
- **Teto**: no máximo `IA_LOTE_MAX_POR_DIA` pedidos do lote criados nas últimas 24 h (padrão 40; `0` desliga o lote; valor inválido cai no padrão). A cota é a do Pro da Beatriz: cota estourada deixa o pedido `rodando` e o worker espera, como no clique.
- **Na tela**: a barra da lista mostra `IA: N avaliadas · M na fila` (entre as vagas da lista).

### Token do worker (Bearer)

`/pedidos-ia/*` exige `Authorization: Bearer <token>`. O token vem do arquivo em `VAGAS_IA_TOKEN_FILE` (no Swarm, `/run/secrets/vagas_ia_token`) ou, na falta dele, de `VAGAS_IA_TOKEN`; a comparação usa `hmac.compare_digest`. Sem token configurado (ausente, ilegível ou vazio) as rotas respondem `503` e a app continua de pé; token ausente ou errado é `401` com `WWW-Authenticate: Bearer`.

Por que Bearer, se o resto da API é aberto: no compose do claude-pi o `vagas` está em mais de uma rede e a tela é publicada na LAN, e estas rotas entregam o currículo e o perfil e aceitam escrita. Abertas, qualquer um na rede poderia reivindicar pedidos ou gravar resultados e envenenar as métricas. O `GET /curriculos/base` aberto é uma dívida que já existe, não um padrão a copiar. O worker lê o mesmo secret; o service `claude` (sandbox) não o recebe.

## Testes

```bash
python -m unittest discover -s tests -t .
```

Os testes de `tests/test_postgres.py` só rodam com `PGHOST` definido; cada um cria um schema descartável, aplica a migration e o apaga no fim. Sem `PGHOST` eles são pulados.
