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
- Todo dado é de uma pessoa (`pessoa_id`): o repositório recebe a pessoa no construtor e toda consulta filtra e grava por ela. Nenhuma vaga é compartilhada: a mesma URL de duas pessoas são duas linhas. Só a fila do worker de IA, a coleta agendada e a conferência leem todas as pessoas.
- Tabela nova que aponta para vaga usa a chave composta `(pessoa_id, vaga_url)`; nunca só a URL.
- Teste de repositório ou rota nova tem duas pessoas com a mesma URL e confere que uma não enxerga a outra.

## Sessão
- A pessoa da requisição vem só da sessão (`pessoa_da_requisicao`), nunca de parâmetro, corpo ou cabeçalho. Rota nova nasce protegida pela dependência do app; isenção só na lista explícita de `src/controller/sessao.py`.
- Senha, hash e token de sessão nunca vão para log, resposta, commit ou PR. O banco guarda só o hash do token.

## Testes
- Todo teste novo falha sem a mudança que ele cobre.
- Teste não pode passar pelo motivo errado: arquivo inexistente, objeto descartado antes do ramo testado, mock que nunca é chamado.
- Fixture que não é resposta gravada de verdade é marcada como escrita à mão.

## Não são bugs (já conferido; não reportar de novo)
- Input do actor `curious_coder/linkedin-jobs-scraper` (Apify) é `urls`/`limitPerSource` (o schema também tem `keywords`/`location`, mas só a URL leva o filtro de remoto `f_WT=2`), sem `count`. Conferido no schema gravado em `tests/fixtures/apify_linkedin_input_schema.json`.
- Local do LinkedIn em inglês (`Brazil`, `Latin America`) é de propósito: com `Brasil` a busca sai sem local (conferido em 12/10/2026).

## Dados pessoais
- O repositório é público: currículo, telefone, e-mail, nome e qualquer dado da Beatriz ou de outra pessoa cadastrada nunca entram em código, fixture, commit ou PR. Log e saída da CLI identificam a pessoa só pelo id.
