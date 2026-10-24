# TASK_005 — Diagnóstico do financeiro existente

**Estado investigado:** `main`, commit `b5bca8540718f30424e9c3675842d0449f86753d` (2026-10-24).

## Resumo executivo

O Shambala tem três mecanismos parcialmente sobrepostos: (1) um livro simples
`financeiro_movimentacoes`; (2) documentos operacionais de PDV/fiado; e (3)
sessões de caixa. Eles não formam um livro-razão único nem têm vínculos
obrigatórios. Em particular, uma venda só vira movimento financeiro no
fechamento, enquanto mensalidade, doação e fiado atualizam `total_vendas` da
sessão da Lanchonete diretamente. Logo, `caixa` hoje significa ao mesmo tempo
**ponto/PDV** (`caixas`), **conta conceitual** (`contas_financeiras`) e
**sessão física** (`caixa_movimentos`); essas noções não são separadas.

Foram comprovidos defeitos de integridade relevantes: o PDV confia no preço e
subtotal enviados pelo navegador; cancelamento genérico só muda o status e não
reverte estoque nem total da sessão; recebimentos não comerciais contaminam
`total_vendas`; e não existe unicidade de mensalidade por trabalhador e
competência. A primeira intervenção recomendada é **suspender a ampliação de
fluxos financeiros e definir um modelo mínimo de fato realizado/obrigação/
conta/sessão**, começando por uma correção isolada de integridade do PDV e dos
cancelamentos, preservando todos os documentos existentes.

Não foi feita alteração de produto, migration, startup, push ou acesso à
produção. O DEV não concedeu `SELECT` às tabelas ao role configurado; portanto
não há afirmação sobre suas quantidades ou inconsistências de dados.

## Limites e método

* Li `README.md`, `SHAMBALA_CONTEXTO.md`, `main.py`, `banco.py`, rotas,
  templates e testes. Não há `AGENTS.md` no repositório.
* O startup chama `criar_tabelas()`, cria usuário/permissões e executa
  `gerar_todas_ativas()` (`main.py:24-41`); não foi iniciado contra DEV.
* `.env` identifica PostgreSQL local, banco `shambala`; a tentativa explícita
  `BEGIN READ ONLY` falhou por permissão na tabela financeira. Não expus senha,
  pessoas ou extratos. O processo DEV não foi localizado no namespace desta
  sessão; a configuração efetiva do serviço não pôde ser confirmada além do
  arquivo local e desse teste de acesso.
* Reproduções usaram exclusivamente PostgreSQL nativo
  `shambala_financeiro_task005_test`, criado para esta tarefa. O catálogo foi
  capturado com `scripts/task005_catalogo_financeiro.sql` e a suíte foi rodada
  sem servidor HTTP. Os dados usados foram fictícios.

## Mapa do sistema e papéis

FastAPI/Jinja/psycopg2 sem ORM; `conectar()` abre uma transação por bloco e dá
commit no fim (`banco.py:82-94`). Não há arquivos de migration: `criar_tabelas`
e `_migrar` aplicam DDL e backfills no startup (`banco.py:430-644`).

`atendentes` são usuários autenticados; grupos autorizam `financeiro`,
`financeiro.compras` e `caixa` por método HTTP (`rotas/permissoes.py:54-77`).
O admin tem bypass. O atendente é o operador, não o pagador. `trabalhador` é
voluntário e pode ser simultaneamente devedor de mensalidade e cliente de
fiado; não é uma conta nem uma pessoa financeira genérica. `pessoa` é o
frequentador e só pode aparecer numa movimentação manual. `fornecedor` não
existe como entidade: compra registra apenas descrição. Não há entidade formal
de doador, adquirente, adquirente de cartão, banco ou conciliador.

Integrações produtoras/consumidoras: trabalhador gera mensalidades; PDV gera
venda/itens/estoque e depois fechamento gera financeiro; fiado gera venda e
posteriormente recebimento; doação e compra inserem financeiro diretamente;
relatórios/dashboard leem a tabela financeira; comprovantes ESC/POS/HTML leem
venda, mensalidade ou sangria. Biblioteca possui `vendas_livros`, mas não foi
localizado escritor que a integre a `financeiro_movimentacoes`. Cestas são
doações de bens entregues, não receita financeira.

## Modelo real e fórmulas

| Entidade | Representação explícita | Integridade observada |
|---|---|---|
| `contas_financeiras` | nome/tipo/ativo; seeds: legado, espécie, PIX e a receber | `UNIQUE(nome)`; sem saldo inicial, moeda ou restrição de tipo único |
| `financeiro_movimentacoes` | entrada/saída, categoria texto, valor `NUMERIC(10,2)`, data TEXT, status texto, referências opcionais | PK/FKs e índice por conta; **sem CHECK** para tipo/status/valor/data, competência, par, autor ou estorno |
| mensalidade | uma movimentação `entrada/mensalidade/pendente`, vencimento em `data_movimentacao` | só índice parcial por trabalhador; não há competência nem `UNIQUE` |
| `vendas_pdv`/itens | documento comercial, total/forma/status; item guarda snapshot de nome/preço | FKs, sem CHECK de forma/status/total, nem vínculo ao lançamento financeiro |
| fiado | venda marcada `fiado`, flags `fiado_pago`, crédito no trabalhador + razão `fiado_credito_mov` | crédito não referencia pagamento/venda obrigatoriamente; pagamentos não têm alocação |
| sessão | `caixa_movimentos`: abertura, acumuladores e fechamento | FKs e índice `(caixa_id,status)`, mas permite várias abertas e acumuladores editáveis |
| sangria | documento + uma saída financeira | sem vínculo na movimentação para a sangria, sem regra de sessão aberta |
| compra | uma saída financeira paga com foto | fornecedor/obrigação/conta a pagar inexistentes; arquivo é escrito antes do commit |
| transferência | duas linhas financeiras | transação do bloco é atômica, mas não há `transferencia_id`/par reversível |

O saldo do dashboard do mês é `SUM(entrada paga) - SUM(saída paga)` por
**data_movimentacao** (`rotas/financeiro.py:36-55`). O cartão por conta mistura
pendentes e pagos e, para `areceber`, soma todas as pendências; para as outras
contas limita por mês (`:61-75`). Relatório geral nem filtra status
(`:409-428`). Assim não há uma fórmula única de saldo. O fechamento calcula
`saldo_inicial + total_vendas - total_sangrias` (`rotas/caixa.py:1685,1717`),
independentemente de espécie/PIX/fiado.

O catálogo do banco descartável confirmou 0 triggers nos quatro núcleos
financeiros. Datas monetárias relevantes são TEXT (`data_movimentacao`,
`data_pagamento`, `data_venda`, datas de sessão); o formato ISO é convenção,
não constraint. O código converte dinheiro repetidamente por `float` e só às
vezes usa `round`, por exemplo `rotas/caixa.py:135-139`, compras `:67-70` e
mensalidades `rotas/financeiro_mensalidades.py:42`.

## Fluxos ponta a ponta

| Fluxo | Tela/rota → escrita → leitura/cancelamento |
|---|---|
| trabalhador/mensalidade | cadastro/edição `trabalhadores.py:300-506` → `sincronizar_mensalidades` insere 13 pendentes em `financeiro_movimentacoes`; inativar só troca `ativo`, não decide pendências → tela/relatório mensalidade e dashboard |
| baixa mensalidade | `/financeiro/mensalidades` → `/receber` atualiza as linhas selecionadas para pago/conta/data → soma no `total_vendas` da sessão caixa 2 se aberta (`financeiro.py:280-355`) → ESC/POS; cancelar só pendente, sem motivo (`:369-383`) |
| venda à vista/PIX/doação junto | `/caixa/pdv` → `finalizar_venda` grava venda/itens/estoque e incrementa sessão; não grava financeiro → fechamento cria uma entrada agrupada por forma (`caixa.py:120-262,1700-1756`) → comprovante; cancelamento genérico só status (`:364-374`) |
| doação avulsa | `/caixa/doacao` → entrada `doacao` e aumenta `total_vendas` da Lanchonete mesmo sendo PIX (`:630-677`) → dashboard/relatório; sem estorno |
| fiado | PDV grava venda/estoque e aumenta sessão no ato, embora não recebida → `/fiados/*/pagar` muda flag e cria `recebimento_fiado`; parcial quita apenas vendas inteiras e sobra vira crédito (`:1155-1277`) → recibo; cancelamento específico é admin (`:1280-1344`) |
| crédito antecipado | `/fiados/credito/{id}` altera saldo e razão de crédito, **não** cria entrada financeira (`:1347-1374`); uso é debitado ao finalizar venda |
| compra/despesa | `/financeiro/compras` → saída paga, conta por forma e foto (`compras.py:49-131`) → dashboard/relatório; não há conta a pagar/quitação/cancelamento |
| transferência | `/financeiro/transferir` → saída+entrada no mesmo bloco (`financeiro.py:780-824`) → relatório; sem comprovante/cancelamento próprio |
| abertura/sangria/fechamento | `/caixa/abrir` insere sessão; sangria insere documento+saída; fechar grava saldo derivado e entradas financeiras agrupadas. Não há suprimento localizado. |

Cartão não é forma permitida explicitamente: mensalidade e compras aceitam só
espécie/PIX; PDV recebe texto sem whitelist mas UI deve definir o que oferece.
Pagamento combinado não existe: uma venda tem uma forma e uma mensalidade um
lote de uma forma. Estorno de venda/recebimento pago não existe de modo geral.

## Matriz de cenários e reproduções

| Cenário (expectativa antes) | Observado no banco descartável | Resultado |
|---|---|---|
| mensalidade R$50 PIX/dinheiro: só a parcela selecionada deve virar paga e conta respectiva | a rota muda status/forma/conta/data; se sessão 2 aberta adiciona R$50 a `total_vendas` | baixa funciona sequencialmente, mas contamina caixa/vendas |
| mesma baixa duas vezes: segunda não deve ter efeito | a seleção exige `status='pendente'`, então repetição sequencial não seleciona item | proteção parcial; concorrência não é atômica (`UPDATE` não repete predicado) |
| venda R$20: servidor deve precificar produto | suíte enviou 2×R$0,01 para produto de R$12,50; gravou venda R$0,02 | **FIN-01 comprovado** |
| cancelar venda: deve reverter estoque e sessão | `cancelar_venda` só `UPDATE status`; não toca itens, estoque ou `total_vendas` | **FIN-02 comprovado** |
| fiado R$20; R$8 e R$12: obrigação deve reduzir por parcela/alocação | a rota só marca vendas inteiras; R$8/R$12 ficam como crédito, `listar_fiados` ainda soma venda cheia | recebimento financeiro existe, mas dívida exibida/alocação é divergente |
| crédito antecipado R$30 e consumo R$20: deve haver entrada de R$30 e consumo não nova receita | entrada de crédito não cria `financeiro_movimentacoes`; consumo baixa crédito e cria venda | **FIN-03 comprovado** |
| transferência R$100: efeito global zero e par identificável | duas linhas inversas dentro do mesmo context manager; soma líquida zero, sem identificador comum | atomicidade de bloco, rastreabilidade insuficiente |
| fechamento misto: espécie físico não deve conter PIX/fiado | `total_vendas` acumula todas as formas; `saldo_final` usa esse total | **FIN-04 comprovado** |

Não forcei por SQL cenários que a interface não oferece (cartão, split,
estorno, quitação parcial de uma única venda). As expectativas de classificação
contábil são hipóteses de negócio a validar com tesoureiro/contador; as
gravações acima são fatos do código.

## Achados priorizados

| ID | Gravidade | Evidência e impacto |
|---|---|---|
| FIN-01 | crítica | `finalizar_venda` soma `itens_json` e persiste preço/subtotal enviados (`caixa.py:135,183-200`). Reproduzido pela suíte. Cliente autenticado pode subfaturar. |
| FIN-02 | crítica | `cancelar_venda` (`caixa.py:364-374`) não reverte estoque/sessão e não cria estorno; a venda some dos relatórios por status mas o caixa conserva total. |
| FIN-03 | alta | crédito antecipado (`caixa.py:1347-1374`) é passivo/entrada de dinheiro sem lançamento financeiro; consumo não tem vínculo de alocação. |
| FIN-04 | alta | mensalidade (`financeiro.py:341-349`), doação (`caixa.py:666-670`) e fiado (`:1145-1152`) são incluídos em `total_vendas` da Lanchonete; fechamento não distingue físico de PIX/fiado. |
| FIN-05 | alta | sem `UNIQUE(trabalhador,competência)`; `_existe_mes` + INSERT (`financeiro_mensalidades.py:28-62`) é race condition. |
| FIN-06 | alta | cancelamento de fiado pago estorna `total`, não `total-crédito`, sempre conta espécie e não reduz sessão fechada (`caixa.py:1315-1341`). |
| FIN-07 | média | relatórios têm filtros de status/data divergentes e datas TEXT; pendentes podem aparecer no relatório geral e saldo por conta é mensal, não saldo acumulado. |
| FIN-08 | média | sem CHECKs financeiros, sem auditoria geral, sem FK entre fato operacional e lançamento; edições de pendências reescrevem valor/vencimento atuais. |
| FIN-09 | média | compra grava foto no filesystem dentro da transação antes do commit (`compras.py:112-126`): erro posterior pode deixar arquivo órfão. |

## Cobertura de testes

`tests/test_financeiro_integracao.py` é a única suíte financeira dedicada e
tem apenas três testes. Executada no banco descartável: **1 falha + 7 erros**.
Os erros são de fixture/expectativa: a suíte consulta e limpa
`auditoria_eventos`, tabela inexistente no schema atual; além disso, a rota de
mensalidade não cria tal auditoria. A falha de preço é defeito de produto, não
expectativa desatualizada. A alegada anterioridade é **não comprovada**: não
foi feito checkout isolado de referência anterior, pois a suíte já não é
executável no estado atual e não há identificação da mudança que introduziu
cada comportamento. As suítes de presença não homologam mensalidade/fiado.

Não há teste de fechamento misto, PIX versus espécie, fiado parcial, crédito,
cancelamento/estorno financeiro, compra, transferência, concorrência ou
permissões por rota financeira. O runner `tests/rodar_testes.sh` sequer inclui
`test_financeiro_integracao.py`.

## HomeBank: aproveitamento crítico

O relatório `RELATORIO_CONCEITOS_HOMEBANK.md` acertou sobre datas, contas,
conciliação, par de transferência e categorias, mas suas afirmações sobre
auditoria, preço confiável e "split corrige baixa" não são comprovadas pelo
produto. Split de categorias não resolve por si alocação de um pagamento às
obrigações; são objetos distintos.

Adotar por fases: (1) obrigação a receber/pagar; (2) recebimento/pagamento
realizado; (3) conta onde entrou/saiu; (4) categoria; (5) transferência interna
pareada; e só então conciliação/importação. Importação de extrato deve usar
identificador externo e conciliar lançamento existente, nunca criar receita
duplicada. HomeBank 5.8.6/manual oficial é referência conceitual, não decisão
de arquitetura nem substituto de escrituração formal.

## Decisões necessárias e sequência proposta

Perguntas objetivas ao responsável/contador:

1. PIX recebido no PDV integra o fechamento físico ou só a conta bancária?
2. Crédito antecipado é adiantamento reembolsável, saldo de cliente ou doação
se não usado? Como e quando pode ser devolvido?
3. Fiado deve reconhecer receita na venda, no recebimento, ou ambos em visões
separadas (competência/caixa)? Como alocar R$8 numa dívida de R$20?
4. Qual conta/sessão recebe mensalidade em dinheiro? É sempre Lanchonete?
5. Cancelamento pós-fechamento é estorno financeiro obrigatório? Quem autoriza?
6. Quais obrigações a pagar e fornecedores são necessários antes de importação?
7. O contador precisa plano de contas, centro de custo, competência ou apenas
controle gerencial? Há exigência fiscal/documental?

Sequência, uma tarefa por vez:

1. Urgente: fixar preço servidor, validar formas/status/valores e tornar
cancelamento de venda reversível e rastreável.
2. Mapear e preservar histórico; introduzir identificadores imutáveis de fato,
obrigação, liquidação e vínculo de estorno sem apagar linhas antigas.
3. Separar sessão de caixa físico de conta financeira e corrigir os acumuladores
por forma; definir política de caixa fechado.
4. Modelar mensalidade com competência, unicidade e baixa condicional atômica.
5. Modelar fiado/crédito com alocação de pagamentos, antes de conciliação.
6. Unificar fórmula de saldo e converter datas com migração validada.
7. Conciliação manual; depois importação OFX/CSV idempotente; por último tags,
orçamento, recorrências e UX.

Migração futura exige backup testado, levantamento de linhas TEXT inválidas,
reconciliação de totais atuais, mapeamento de `conta_id` legado, preservação de
IDs/comprovantes/fotos, escrituras de abertura para divergências e rollback
documentado. Não foi escrita nem aplicada migration nesta tarefa.

## Encerramento

Comprovado: os achados FIN-01 a FIN-04, ausência de constraints/triggers
centrais, cobertura financeira quebrada e ausência de um modelo único de saldo.
Incerto: estado quantitativo do DEV, regras contábeis pretendidas, cartão,
política de estorno e exigências do contador. Produção não foi acessada; não
houve alteração de dados DEV nem produto. O banco descartável deve ser removido
após registrar os resultados desta tarefa.
