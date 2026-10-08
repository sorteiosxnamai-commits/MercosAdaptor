# Mercos_Adaptor

Serviço independente que centraliza a comunicação com a API Mercos. O agente, o BI e futuros sistemas deixam de armazenar tokens Mercos e passam a consumir este adaptador por uma chave interna.

## Recursos

- API assíncrona com FastAPI e HTTPX.
- Paginação completa por `alterado_apos`.
- Retry automático para HTTP 429 e falhas transitórias.
- Leitura de clientes, produtos, pedidos e cadastros auxiliares.
- Inclusão/alteração de clientes, pedidos e títulos.
- Credenciais Mercos nunca são devolvidas nas respostas.
- Autenticação interna pelo header `X-API-Key`.
- Contrato uniforme: `data`, `count` e `nextCursor`.
- Sem dependência de Supabase: cada consumidor controla seu próprio banco e cursor.

O adaptador não oferece busca de cliente por CPF/CNPJ. `GET /v1/customers`
aceita `alterado_apos`; consumidores que precisam de lookup por documento devem
manter um índice incremental protegido, sem percorrer toda a base por conversa.
POST/PUT não são repetidos após timeout de transporte: o resultado pode ser
desconhecido e precisa de reconciliação antes de qualquer nova tentativa.
Em escritas bem-sucedidas, o id que a Mercos devolve no header `MeusPedidosID`
é exposto no corpo da resposta como `{"id": ...}`.

## Início rápido

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload
```

Configure no `.env` os tokens Mercos e uma chave interna forte. Swagger: `http://localhost:8000/docs`.

## Exemplo de consumo

```bash
curl -H "X-API-Key: SUA_CHAVE_INTERNA" \
  "http://localhost:8000/v1/orders?alterado_apos=2026-08-01T00:00:00"
```

Resposta:

```json
{
  "resource": "orders",
  "count": 2,
  "nextCursor": "2026-08-12T10:00:00",
  "data": []
}
```

O consumidor só deve salvar `nextCursor` depois que todos os registros de `data` forem persistidos com sucesso. Recomenda-se reconciliação diária dos últimos 30 dias.

## Rotas principais

| Rota | Uso |
|---|---|
| `GET /health` | Saúde e ambiente, sem revelar segredos |
| `GET /v1/resources` | Recursos suportados |
| `GET /v1/orders` | Pedidos incrementais |
| `GET /v1/orders/{mercos_id}` | Detalhe completo do pedido via API Mercos v2 |
| `GET /v1/products` | Produtos e estoque |
| `GET /v1/customers` | Clientes |
| `GET /v1/users` | Vendedores/usuários |
| `GET /v1/categories` | Categorias |
| `GET /v1/payment-conditions` | Condições de pagamento |
| `GET /v1/price-tables` | Tabelas de preço |
| `POST/PUT /v1/orders` | Criar/alterar pedido via API v2 |
| `POST/PUT /v1/customers` | Criar/alterar cliente |
| `POST/PUT /v1/titles` | Criar/alterar título |

## Extensões para o ERP (aditivas)

Tudo abaixo é novo; as rotas e respostas antigas não mudaram (ver `docs/erp-extensions.md`).

- `GET /v1/capabilities`: o que este build sabe fazer, escopos da chave que chamou, evidência oficial
  de cada linha e o que **não** está implementado. Não prova acesso da conta Mercos.
- **Chave ERP com escopos** (`MERCOS_ERP_API_KEY` + `MERCOS_ERP_SCOPES`). A chave legada continua
  com leitura e as três escritas de sempre (clientes, pedidos, títulos) e **não ganha** as novas.
- **Rotas novas, explícitas e com DTO**: `POST/PUT /v1/products`, `PUT /v1/products/{id}/stock`
  (saldo absoluto), `POST /v1/orders/{id}/cancel`, `POST/PUT /v1/billings`,
  `POST/PUT /v1/payment-methods`. Nunca encaminham verbo/caminho arbitrário.
- **Leituras novas** (`titles`, `payments`, `payment-methods`, `promotions`, `commissions`) com
  filtros em allowlist. Vêm **desligadas** até a validação no sandbox: liste a chave em
  `MERCOS_ENABLED_EXTENSIONS`.
- **Cota da conta compartilhada** entre todos os consumidores (`app/quota.py`): serializa chamadas,
  guarda um deadline único (429 e pacing valem para BI, ERP e agente) e falha rápido com
  `Retry-After` quando a espera é longa. Várias réplicas: `MERCOS_REDIS_URL` (instale `.[redis]`).

## Integração com agente e BI

Cada consumidor configura somente:

```env
MERCOS_ADAPTOR_URL=https://seu-adaptor.onrender.com
MERCOS_ADAPTOR_API_KEY=mesma-chave-interna
```

Não coloque os tokens Mercos no frontend. O frontend chama seu próprio backend; o backend chama o `Mercos_Adaptor`.

## Testes

```bash
pytest -q
```

Os testes usam transporte HTTP simulado e não acessam uma conta real da Mercos.
"# MercosAdaptor" 
