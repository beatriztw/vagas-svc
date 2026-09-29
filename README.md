# python-app

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
