# Discovery: vagas para mais de uma pessoa

Hoje o vagas-svc atende uma pessoa só. O pedido é que outra pessoa tenha o próprio perfil, currículo,
histórico, candidaturas e notas da IA no mesmo app, sem misturar nada com o da Beatriz.

Este documento levanta onde o código assume "uma pessoa", o que é de cada pessoa e o que pode ser
compartilhado, compara três caminhos e recomenda um. Nada foi implementado ainda.

## Onde o app assume uma pessoa só

| Lugar | Como está | Efeito com duas pessoas |
|---|---|---|
| `perfil_busca` (001) | `id SMALLINT DEFAULT 1 CHECK (id = 1)`; `PerfilBuscaRepository.obter()`/`salvar()` sem parâmetro | só cabe um perfil |
| `curriculo` (001) | base vigente = a `tipo='base'` mais recente; `obter_base()` sem parâmetro | o currículo de uma pessoa vira a base da outra |
| `vaga_encontrada` (002, 004, 009) | `url` é a chave; `termos`, `vista_em` e `salva_em` são colunas da vaga | "nova", "salva" e "achada por qual termo" ficam iguais para as duas |
| `candidatura` (003) | `vaga_url UNIQUE` | só uma pessoa consegue aplicar numa vaga |
| `pedido_ia` (006, 007) | um ativo por `(vaga_url, tipo)`; `curriculo_base_id` é a base global | a nota de uma vale para a outra; o lote não sabe de quem é a vaga |
| `rotulo_ia` (006) | `vaga_url` é a chave | o rótulo de uma sobrescreve o da outra; métricas misturadas |
| `ia_lote_pausa` (010) | linha única (`id = 1`) | pausar pausa para as duas |
| `coleta_agendada` (008) / `ColetaService` | lê `perfis.obter()` e coleta os termos de um perfil | só os termos de uma pessoa são coletados |
| `HistoricoService` | janela das `TETO_VARREDURA_HISTORICO` (5000) mais recentes de `vaga_encontrada`, perfil aplicado na leitura | a janela enche com vagas achadas pelos termos da outra pessoa; `excluir`/`niveis` filtram, mas não tiram vaga que só a outra procurou |
| `PedidoIAService` | `curriculos.obter_base()` e `perfis.obter()` em 6 pontos | o worker recebe perfil e currículo errados |
| Tela e API | sem login (rede interna); `/tela/perfil`, `/curriculos/base` etc. sem dono | não há como saber quem está usando |
| `metricas_ia.py` | docstrings e nomes tratam a faixa "da Beatriz" como referência | nomes a generalizar (`faixa_pessoa`) |
| S3 (`Armazenamento.enviar`) | chave escolhida por quem chama, sem prefixo de pessoa | arquivos das duas no mesmo espaço |

Pontos de chamada que mudam: `src/controller/api.py` (rotas de perfil, currículo, `/vagas`, `criar_app`),
`src/controller/coleta.py`, `src/controller/tela*.py`, `src/service/{coleta,historico,pedido_ia}_service.py`,
`src/repository/{base,postgres,candidatura_postgres,pedido_ia_postgres}.py`.

## O que é de cada pessoa e o que é compartilhado

**De cada pessoa:** perfil, currículos (base e adaptados), quais vagas a coleta achou *para ela* (e por
quais termos dela), vista, salva, candidaturas, pedidos e notas da IA, rótulos, pausa do lote.

**Compartilhado:** o catálogo da vaga em si (`url`, título, empresa, local, descrição, requisitos,
salário, expiração). O detalhe buscado sob demanda e a expiração valem para qualquer pessoa, e
compartilhar evita buscar o mesmo detalhe duas vezes (o LinkedIn limita a partir da 4ª requisição).

**A decidir:** configuração de providers por variável de ambiente (`APIFY_LOCAIS`, `GREENHOUSE_EMPRESAS`
etc.). Começa global; vira campo do perfil só se as duas pessoas precisarem de locais/empresas diferentes.

## Caminhos

### A. Segunda instância (sem mudar código)

Outro container do vagas-svc com outro banco (ou outro `PGDATABASE`), outra porta/host e outro bucket S3.

- **Prós:** zero código, zero risco para os dados atuais, isolamento total.
- **Contras:** custo do Apify dobra (a mesma busca roda duas vezes); o worker de IA do `claude-pi` precisa
  consultar as duas filas (dois tokens, duas URLs); duas coletas agendadas no mesmo Pi; toda correção
  é implantada duas vezes (mesma imagem, então é só reiniciar); o detalhe da mesma vaga é buscado duas
  vezes no LinkedIn.
- **Esforço:** compose + secrets + ajuste do worker. Horas.

### B. Mesmo app, uma pessoa por instância lógica (`PESSOA` no ambiente)

Um campo `pessoa_id` nas tabelas, e cada container fixa a pessoa por variável de ambiente. Mesmo banco,
dois containers.

- Resolve o catálogo compartilhado e a coleta deduplicada só parcialmente (cada container coleta sozinho).
- Tem quase todo o custo do C (todas as tabelas ganham `pessoa_id`) com metade do ganho. **Não recomendado.**

### C. Multiperfil de verdade (um app, várias pessoas)

Tabela `pessoa` e `pessoa_id` em tudo que é de cada pessoa; o catálogo de vagas fica compartilhado.

Modelo de dados (uma migration nova, `013_pessoa.sql`, idempotente; não editar as publicadas):

- `pessoa (id, nome_exibicao, criada_em)`; a linha `id = 1` é criada pela migration e recebe tudo o que já existe.
- `perfil_busca`: trocar a PK `id` (CHECK `id = 1`) por `pessoa_id REFERENCES pessoa`. Os dados atuais
  já estão em `id = 1`, então a troca não move linha.
- `curriculo`: `pessoa_id NOT NULL DEFAULT 1`; base vigente passa a ser a mais recente *da pessoa*.
- `vaga_encontrada` continua o catálogo. Nova tabela `vaga_pessoa (pessoa_id, vaga_url, termos,
  primeira_vez_em, ultima_vez_em, vista_em, salva_em, PK (pessoa_id, vaga_url))`, preenchida pela
  migration a partir das colunas atuais (todas para a pessoa 1). As colunas antigas ficam sem uso
  até uma migration de limpeza.
- `candidatura`: `pessoa_id` e `UNIQUE (pessoa_id, vaga_url)` no lugar de `UNIQUE (vaga_url)`.
- `pedido_ia`: `pessoa_id`; índice de ativo por `(pessoa_id, vaga_url, tipo)`; fila do worker segue global.
- `rotulo_ia`: PK `(pessoa_id, vaga_url)`.
- `ia_lote_pausa`: PK `pessoa_id`.
- `coleta_agendada` fica global (o horário é do app, não da pessoa).

Serviço:

- Repositórios recebem `pessoa_id` (no construtor, criado por requisição, como o `_PorChamada` já faz
  com a conexão do pool: uma conexão por chamada continua valendo).
- Coleta: junta os termos de todas as pessoas, normaliza como hoje (`front-end` = `front end`), busca
  cada termo **uma vez** nos providers e grava em `vaga_pessoa` para cada pessoa cujo perfil tem o termo.
  Isso mantém o custo do Apify igual ao de hoje para termos em comum e só soma os termos novos. O teto
  (`limite`/`maxItems`) continua por busca; vale um teto total por coleta para o caso de muitas pessoas.
- Histórico: a janela de varredura passa a ser das vagas *da pessoa* (`JOIN vaga_pessoa`), com
  vista/salva lidas de lá.
- IA: o pedido leva a pessoa; `proximo` monta o payload com o perfil e a base daquela pessoa. O payload
  do worker não muda de forma (só o conteúdo), então o worker não precisa mudar. O teto
  `IA_LOTE_MAX_POR_DIA` é cota da assinatura do worker: fica global e é dividido entre as pessoas
  (ex.: rodízio na fila), para uma pessoa não consumir a cota da outra.
- Métricas: por pessoa (o rótulo é de quem avaliou).

Identificar quem está usando (a parte que mais pesa, porque hoje não há autenticação):

1. **Seletor de pessoa na tela (cookie) sem senha.** Simples, mas qualquer um na LAN vê o currículo da
   outra pessoa. Só serve se as duas confiam uma na outra e na rede.
2. **Login com senha por pessoa (sessão em cookie `HttpOnly`, `SameSite=Strict`).** Protege o currículo
   de cada uma. A API JSON aberta (`GET /curriculos/base` já é dívida conhecida no README) passa a exigir
   sessão ou o Bearer do worker.
3. **Proxy na frente (ex.: autenticação do proxy reverso passando um cabeçalho com o usuário).** Menos
   código no app, mas depende da infraestrutura do Pi.

Recomendo o 2: o currículo e o telefone de cada pessoa passam a dividir o mesmo app, e hoje a tela é
publicada na LAN.

## Recomendação

- **Se a ideia é testar logo:** caminho A (segunda instância). Dá para usar amanhã e valida se o
  produto serve para outro perfil antes de investir na mudança.
- **Para ficar:** caminho C, em PRs pequenos, nesta ordem (cada um mantém o app funcionando só com a
  pessoa 1):
  1. Migration `013_pessoa.sql` + `pessoa_id` nos repositórios, sempre `1`. Nenhuma mudança visível.
     Testes de Postgres conferem que os dados atuais continuam na pessoa 1.
  2. `vaga_pessoa`: vista/salva/termos por pessoa; histórico lê de lá.
  3. Candidaturas, rótulos e pausa por pessoa.
  4. IA por pessoa (pedido, lote, métricas, rodízio da cota).
  5. Coleta multiperfil com termos deduplicados entre pessoas.
  6. Login e tela de "trocar de pessoa"; cadastro da segunda pessoa (perfil importado do currículo dela
     pelo fluxo de `perfil_ia` que já existe).

Estimativa grosseira: o C mexe em cerca de 15 arquivos de `src/` e na maioria dos ~45 arquivos de teste
(os construtores dos repositórios mudam). 1 e 2 são os maiores; 6 é o mais delicado (segurança).

## Perguntas em aberto

1. Teste rápido (A) ou já a versão definitiva (C)?
2. As duas pessoas podem ver os dados uma da outra na rede de casa, ou precisa de login (afeta o passo 6)?
3. A segunda pessoa procura no mesmo mercado (remoto, Brasil/América Latina) ou precisa de outros locais
   do LinkedIn e outras empresas no Greenhouse/Lever/Ashby (afeta se providers viram campo do perfil)?
4. A cota do worker de IA dá para as duas? Se não, qual divisão (meio a meio, por ordem de chegada)?

Dados pessoais da segunda pessoa (currículo, contato) seguem a mesma regra do `CLAUDE.md`: ficam só no
banco e no S3, nunca em código, fixture, commit ou PR.
