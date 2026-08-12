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
| `GET /v1/products` | Produtos e estoque |
| `GET /v1/customers` | Clientes |
| `GET /v1/users` | Vendedores/usuários |
| `GET /v1/categories` | Categorias |
| `GET /v1/payment-conditions` | Condições de pagamento |
| `GET /v1/price-tables` | Tabelas de preço |
| `POST/PUT /v1/orders` | Criar/alterar pedido via API v2 |
| `POST/PUT /v1/customers` | Criar/alterar cliente |
| `POST/PUT /v1/titles` | Criar/alterar título |

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
