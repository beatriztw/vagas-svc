# vagas-svc

Regras deste projeto, tiradas dos bugs que o review já achou aqui. Valem para quem
escreve código e para o review automático.

## Estrutura
- Camadas do README: `controller` (FastAPI) → `service` → `repository` (Postgres) / `client` (providers). Nomes em português.
- Ambiente: `.venv` + `requirements.txt`. Testes: `python -m unittest discover -s tests -t .` (os de Postgres rodam só com `PGHOST`).

## Validação e erros
- Validar antes de gravar, nunca só ao ler: dado inválido gravado deixa o perfil ilegível.
- Só `DadoInvalido` (`src/model/erros.py`) vira 422. Qualquer outro erro é 500, inclusive `ValueError` de configuração.
- Variável de ambiente inválida de algo opcional (ex.: provider) cai no padrão, não derruba a app.

## Dado externo (providers)
- Item que não é objeto, data em formato estranho ou epoch absurdo descarta só aquele item, nunca a busca inteira.
- Provider pago sempre com teto de custo (`limite`/`maxItems`).

## Comparar texto
- Por palavra, nunca por pedaço: `Java` ≠ `JavaScript`, `SQL` ≠ `NoSQL`, `Vitória` ≠ `Vitória da Conquista`.
- Normalizar acento, caixa, hífen (`front-end` = `front end` = `frontend`), gênero e plural. Título pode estar em inglês.
- Listas do perfil sem repetidos; termo que fica vazio depois de normalizar é ignorado.

## Banco
- Uma conexão do pool por chamada. Conexão compartilhada entre requisições mistura transações e perde gravações.

## Testes
- Todo teste novo falha sem a mudança que ele cobre.
- Teste não pode passar pelo motivo errado: arquivo inexistente, objeto descartado antes do ramo testado, mock que nunca é chamado.
- Fixture que não é resposta gravada de verdade é marcada como escrita à mão.

## Não são bugs (já conferido; não reportar de novo)
- Input do actor `curious_coder/linkedin-jobs-scraper` (Apify) é `keywords`/`location`/`limitPerSource`, sem `count`. Conferido no schema gravado em `tests/fixtures/apify_linkedin_input_schema.json`.

## Dados pessoais
- O repositório é público: currículo, telefone, e-mail e qualquer dado da Beatriz nunca entram em código, fixture, commit ou PR.
