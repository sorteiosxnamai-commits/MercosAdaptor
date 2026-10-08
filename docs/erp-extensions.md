# Extensões do Adaptor para o ERP Xnamai

Implementa o Prompt 1 do documento `Xnamai-ERP-Mercos-prompts-implementacao.md`. Sem commit,
push, deploy nem chamada à conta real do Mercos.

## Matriz antes → depois

| Capacidade | Antes | Depois | Estado padrão | Evidência oficial |
|---|---|---|---|---|
| 12 leituras originais | sim | **inalteradas** | ligadas | v1/v2 conforme `resources.py` |
| `GET /v1/capabilities` | não | sim (aditivo) | — | — |
| Criar/editar cliente, pedido, título | sim | **inalteradas** (chave legada mantém) | ligadas | v1clientes, v2pedidos, v1titulos |
| Criar/editar produto (`/v1/products`) | não | sim, DTO próprio | **ligada** | `POST /v1/produtos`, `PUT /v1/produtos/{id}` confirmados |
| Cancelar pedido (`POST /v1/orders/{id}/cancel`) | não | sim | **ligada** | `POST /v1/pedidos/cancelar/{id}` confirmado; 412 = pedido inexistente |
| Ajustar estoque (`PUT /v1/products/{id}/stock`) | não | sim, saldo **absoluto** | desligada | método e `novo_saldo` confirmados; **caminho não** (`ajustar_estoque` assumido) |
| Faturamento (`POST/PUT /v1/billings`) | não | sim, sem GET | desligada | métodos e campos confirmados; **caminho não**; cancelar = `PUT excluido=true` |
| Formas de pagamento (`/v1/payment-methods`) | não | leitura e escrita | desligadas | métodos confirmados; **caminho não** |
| Leitura: títulos | não | `GET /v1/titles` | desligada | `GET /v1/titulos` confirmado; **filtros/paginação não** |
| Leitura: pagamentos, promoções | não | sim | desligadas | caminho/paginação não confirmados |
| Leitura: comissões | não | sim, `ultimo_id` e `lastId` | desligada | filtros e `comissao_id` confirmados; caminho não |
| Imagens de produto | não | **não implementado** | — | a leitura devolve objeto por produto (hashes), formato diferente do envelope |
| Escrita de tabelas de preço, condições, transportadoras, categorias, segmentos | não | **não implementado** | — | métodos de escrita não confirmados |

"Desligada" significa: a rota existe, está testada, devolve 404 com o motivo e **não chama o Mercos**
até que a chave da capacidade seja listada em `MERCOS_ENABLED_EXTENSIONS`, depois da validação no
sandbox. A documentação consultada (docs.mercos.com) nem sempre informa caminho, versão e paginação;
nada foi presumido como "funciona".

## Garantias implementadas

1. **Contratos legados preservados** (23 testes originais intactos): `GET /health`,
   `GET /v1/resources` (continua com os 12), `GET /v1/{alias}` com o envelope
   `resource/count/pageCursor/nextCursor/data`, GET por ID, e as seis escritas. Parâmetros de consulta
   desconhecidos continuam ignorados nas rotas antigas e **nunca** repassados ao Mercos.
2. **Escopos por consumidor.** Chave legada = `read` + `write:customers|orders|titles`. Chave ERP
   (`MERCOS_ERP_API_KEY`) = só o que `MERCOS_ERP_SCOPES` lista (padrão `read`). Escopos desconhecidos
   são ignorados; chave ERP igual à legada não ganha poder extra. Tokens do Mercos nunca saem em respostas.
3. **Escritas sem retry** (timeout ou 429): resultado desconhecido é do consumidor reconciliar.
   `MeusPedidosID` continua exposto como `{"id": ...}`; sem header, nenhum ID é inventado.
4. **Sem proxy aberto:** rotas explícitas por operação, DTO com campos validados, filtros em
   allowlist por recurso (valor também validado) e IDs de caminho restritos a `[0-9A-Za-z_-]{1,40}`.
5. **Cota única da conta** (`app/quota.py`): gate FIFO, deadline compartilhado, pacing dentro do gate
   (antes ficava fora do lock), e depois de um 429 devolvido **nenhum** consumidor chama o Mercos
   antes do deadline (resposta imediata 429 com `Retry-After` quando a espera passa de 60 s). Com
   `MERCOS_REDIS_URL`: lock com lease + deadline no Redis, atômicos e **fail-closed** (Redis fora = 503,
   sem chamada ao Mercos). Namespace por conta via hash do token (nunca exposto).
6. **Mensagem de permissão verdadeira:** o texto "GET por ID só existe no sandbox" aparece só em
   leituras por ID; as demais operações citam método, caminho e o HTTP real do Mercos.

## Compatibilidade (antes × depois)

- Operações legadas: 10 → 10 mantidas; **9 novas** (`/v1/capabilities` e as escritas novas).
- Única mudança de comportamento em rota legada: o parâmetro de ID de caminho agora é restrito a
  `[0-9A-Za-z_-]{1,40}` (antes aceitava qualquer texto e o inseria no caminho do Mercos). IDs reais do
  Mercos são numéricos; um ID com `/`, `..`, `?` ou `%` passa a receber 422. O schema OpenAPI dessas
  quatro operações ganhou o `pattern`.
- O `asyncio.Lock` local foi substituído pelo gate. Com 1 instância o comportamento é o mesmo, com o
  deadline de 429/pacing agora valendo para todos os consumidores.
- BI e agente não precisam mudar nada. O BI continua usando só `X-API-Key` legada.

## Limites reais

- **Nada foi chamado no sandbox/produção.** Caminhos "não confirmados" acima precisam da sonda
  (`scripts/erp_sandbox_probe.py` do backend ERP e `GET /v1/capabilities`).
- **Fairness:** a fila é FIFO entre quem chama; não há cota reservada por consumidor. Uma carga
  completa do ERP concorre com o BI na mesma fila. Se isso incomodar, a próxima peça é um limite por
  consumidor (não implementado).
- **Redis testado com `fakeredis`** (dois "processos" no mesmo servidor falso). Um Redis real não foi
  exercitado nesta máquina; repita em homologação.
- Só duas chaves (legada e ERP). Mais consumidores com escopos próprios exigiriam generalizar.
