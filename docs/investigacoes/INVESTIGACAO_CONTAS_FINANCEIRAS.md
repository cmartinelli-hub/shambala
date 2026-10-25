# Investigação — Contas financeiras no Shambala

Data: 25/10/2026. Etapa de **diagnóstico**: nada foi alterado em produto, schema ou
dados. Ambiente de desenvolvimento apenas, com leitura agregada; reproduções de
escrita exclusivamente em PostgreSQL descartável, já limpo (§7).

Método: código, schema e testes como evidência primária; documentação só como
pista. Cada afirmação abaixo está marcada como **[comprovado]** (executado),
**[código]** (leitura de código/schema) ou **[hipótese]** (não testado).

---

## 1. Resposta direta: é possível cadastrar contas hoje?

**Sim — o cadastro existe e funciona. Ele está fora do Financeiro, escondido em
Configurações, e é justamente por isso que o dono não o encontrou.**

| Pergunta | Resposta |
|---|---|
| Existe tela para criar/inativar conta? | **Sim** — `/configuracoes/contas` **[comprovado]** |
| Onde se chega? | Menu → **Configurações** → cartão "Contas Financeiras" → "Gerenciar Contas" **[código]** |
| Qual permissão? | módulo **`configuracoes`** (`rotas/configuracoes.py:43-45`), e o link do menu só aparece para **admin** (`templates/menu.html:89-111`, `rotas/auth.py:174`) **[comprovado]** |
| Dá para editar nome/tipo? | **Não** — `GET /configuracoes/contas/5/editar` e `/configuracoes/contas/5` devolvem **404** **[comprovado]** |
| Dá para criar "Caixa da casa", "Banco", "Poupança"? | **Sim, mecanicamente** — o campo tipo é texto livre e a tela oferece `especie`, `pix`, `areceber`, `legado`, `outro` **[comprovado]** |
| Mas o dinheiro passa a poder ir para elas? | **Não** — só `especie` e `pix` recebem (`servico_sessao.py:161-198`) **[comprovado]** |
| Existe link para contas **dentro do Financeiro**? | **Não** — a nav do Financeiro (`templates/financeiro/dashboard.html:11-18`) não tem "Contas" **[código]** |

Por que o dono não acha:

1. o Financeiro **não** tem entrada para contas — quem olha o financeiro de ponta a
   ponta nunca vê a tela;
2. o caminho passa por **Configurações**, que é seção de administração do sistema
   (dias de atendimento, PIX, SMTP, backup), não do dinheiro;
3. mesmo achando a tela, **criar uma conta nova não faz o dinheiro ir para ela** —
   e, pior, cria um efeito colateral descrito em §5.

---

## 2. Mapa das entidades, vínculos e fluxos

### 2.1 Três coisas diferentes, com nomes parecidos

| Entidade | O que é | Onde | Campos-chave |
|---|---|---|---|
| **Conta financeira** | *Onde o dinheiro está* (espécie, PIX, banco) | `contas_financeiras` (`banco.py:1029-1037`) | `nome` UNIQUE, `tipo` (texto livre), `ativo`, `created_at`. **Sem coluna de saldo nem saldo inicial** |
| **Caixa operacional** | *Qual gaveta/ponto de venda* (Lanchonete, Bazar, Biblioteca) | `caixas` (`banco.py:1018-1026`) | `nome` UNIQUE, `ativo`, `usa_produtos`, `chave_pix_id` |
| **Sessão de caixa** | *Um turno de trabalho* naquele caixa | `caixa_movimentos` (`banco.py:1092-1107`) | `saldo_inicial`, `saldo_final`, `status` (aberto/fechado), `versao` (`legado`/`eventos`) |

Relações: `financeiro_movimentacoes.conta_id → contas_financeiras.id` (FK,
`banco.py:649`); `caixa_movimentos.caixa_id → caixas.id`;
`caixa_sessao_eventos.conta_id → contas_financeiras.id` (`banco.py:1252`);
`caixa_sangrias.caixa_movimento_id → caixa_movimentos.id`.

**Consequência prática**: "caixa" no módulo Caixa **não é** conta financeira. Uma
sessão da Lanchonete pode receber em espécie (conta Espécie) e em PIX (conta PIX).
As quatro contas do sistema não têm relação com os quatro caixas operacionais.

### 2.2 As contas que existem no DEV **[comprovado, leitura]**

| id | Nome | tipo | ativa | movimentos |
|---|---|---|---|---|
| 1 | Conta Antiga | `legado` | sim | 77 |
| 2 | Conta Espécie | `especie` | sim | 221 |
| 3 | Conta PIX | `pix` | sim | 116 |
| 4 | Valores a Receber | `areceber` | sim | 448 |

Todas as quatro são **semeadas pelo sistema** (`banco.py:1603-1613`, `INSERT … ON
CONFLICT (nome) DO NOTHING`) e todas são **administráveis** pela tela — inclusive
`legado` e `areceber`, o que é discutível (§5). **Zero movimentos sem conta** no
DEV (0 de 862).

### 2.3 Destino de cada tipo de movimento **[código + comprovado]**

| Movimento | Conta de destino/origem | Como é escolhida |
|---|---|---|
| Venda PDV (espécie) | `especie` | `conta_id_por_tipo` (`caixa.py:384-402`) |
| Venda PDV (PIX) | `pix` | idem; PIX fora de sessão grava direto (`caixa.py:341-346`) |
| Doação em espécie | `especie` | `validar_conta_para_forma(conn, "especie", None)` (`caixa.py:976-978`) — **sempre `None`**: a tela não oferece escolha |
| Doação em PIX | `pix` | `validar_conta_para_forma` com a conta escolhida no formulário |
| Mensalidade (cobrança) | `areceber` | `financeiro_mensalidades.py:137,152` |
| Mensalidade (recebimento) | `especie` ou `pix` | `financeiro.py:915-917, 948-955` |
| Recebimento de fiado | `especie`/`pix` | `servico_fiado.aplicar_recebimento(…, conta_id=…)` (`servico_fiado.py:290,325`) |
| Crédito antecipado | `especie`/`pix` | `caixa.py:1856-1892` |
| Compra (despesa) | por forma | `conta_id_por_tipo(conn, forma)` (`compras.py:659,674-681`) |
| Sangria | **par** saída (espécie) + entrada (conta destino) | `caixa.py:2111-2162`, par ligado por `sangria_id` |
| Transferência | **par** saída + entrada | `financeiro.py:1529-1543` — **sem vínculo entre as duas linhas** |

**Vínculos são persistidos**, não deduzidos na consulta — exceto o par de
transferência, que só se reconhece pela descrição "(origem)/(destino)".

**Dinheiro e PIX estão vinculados a uma conta identificável: sim.** PIX é forma de
pagamento apontando para a conta de destino; a confusão comum é tratar PIX como
conta — no modelo ele é `forma_pagamento` + `conta_id`.

**Recebimento sem sessão aberta**: em espécie é **recusado**
(`sessao_necessaria`, comprovado); em PIX é aceito com vínculo de auditoria e
efeito físico 0 (`servico_sessao.py:311-332`).

---

## 3. Saldos e transferências

### 3.1 Como o saldo é calculado

**Calculado, nunca armazenado.** A tela usa entrada−saída somando **pago e
pendente** (`configuracoes.py:570-580`); a Visão Geral calcula um "acumulado por
conta" separado (`financeiro.py:328-360`). **As duas fórmulas divergem** para as
contas `areceber` e para o que é dinheiro de fato.

**[comprovado no DEV]** — mesma conta, dois números:

| Conta | "Saldo" da tela `/configuracoes/contas` | Só `pago` (dinheiro efetivo) |
|---|---|---|
| Conta Antiga | R$ 6.097,30 | R$ 6.097,30 |
| Conta Espécie | **−R$ 272,14** | **−R$ 272,14** |
| Conta PIX | R$ 11.707,50 | R$ 11.707,50 |
| Valores a Receber | **R$ 16.185,00** | **R$ 2.304,10** |

Duas leituras importantes:

- o **saldo negativo da Conta Espécie é dado real**, não erro de conta: 49 sangrias
  somando R$ 11.223,50 saíram dessa conta, mais R$ 5.328,64 de compras, contra
  R$ 12.895,00 de entradas. É o retrato de um modelo em que a sangria **sai** da
  conta de espécie sem que se saiba para onde foi (§3.3);
- a conta `areceber` mostra **R$ 16.185,00 de "saldo"**, mas é **obrigação a
  receber, não dinheiro disponível** — R$ 2.304,10 são títulos já pagos que ficaram
  registrados nessa conta. Somar essa linha como se fosse caixa seria erro grave.

### 3.2 Saldo inicial, data de referência, retroativo

- `contas_financeiras` **não tem** saldo inicial nem data de referência. O
  "saldo inicial" que existe é o da **sessão de caixa** (`caixa_movimentos`), que é
  outra coisa;
- lançamento retroativo entra na soma sem qualquer tratamento — não há corte de
  data nem fechamento de conta.

### 3.3 Transferências e sangria

| | Existe? | Vinculada? | Transacional? | Fora do resultado? |
|---|---|---|---|---|
| **Transferência** (`/financeiro/transferir`) | sim | **não** — duas linhas independentes, sem `transferencia_id` | sim (mesma transação + idempotência) | sim (`_SQL_DINHEIRO` exclui `transferencia`/`sangria`, `financeiro.py:93-99`) |
| **Sangria** (`/caixa/sangria`) | sim | **sim** — par ligado por `sangria_id` | sim | sim |

**A sangria é transferência, não retirada** — o código grava **duas** linhas:
saída da espécie e entrada na conta de destino escolhida (`caixa.py:2062`,
"par saída/entrada vinculado pelo mesmo sangria_id"). No DEV há **49 saídas e zero
entradas** de sangria, porque as entradas foram para contas que não registram
`categoria='sangria'` — o que **não** prova retirada; é limite do dado histórico.

### 3.4 Cancelamentos e inativação

- cancelamento de venda usa `servico_cancelamento` e **não** cria movimento de
  estorno na conta: o efeito é o cancelamento da venda e o evento de sessão;
- **inativação existe e preserva histórico** — `POST
  /configuracoes/contas/{id}/toggle` só troca `ativo`, e as consultas filtram
  `ativo=1` **[comprovado]**;
- porém **inativar não impede novas movimentações** de forma confiável: quem não
  valida `ativo` continua gravando (é o caso do par de transferência, que usa o id
  recebido sem checar; e a `Conta Antiga` segue ativa e recebendo).

---

## 4. Interface e permissões

**O que o dono consegue hoje pelo Financeiro**: Visão Geral (saldos e movimentos),
Mensalidades, Relatório de mensalidades, Fiados, Relatórios, Transferir, Gerar
PIX. **Nada de contas.**

- O filtro **"conta"** da Visão Geral (`financeiro.py:201-215`) corresponde a
  `contas_financeiras.id` — a mesma entidade. Ele existe e funciona.
- **PDV × livro-caixa**: mesma entidade de conta, caminhos diferentes. O PDV grava
  em `caixa_sessao_eventos.conta_id` e o fechamento converte em
  `financeiro_movimentacoes` (`servico_sessao.py:476-490`); doações, mensalidades e
  fiado gravam direto no livro-caixa. Ambos convergem para a mesma coluna.
- **Lacunas que impedem administrar Caixa da casa / Banco / Reserva**:
  1. a tela está fora do Financeiro e é admin-only;
  2. não há edição de nome/tipo;
  3. **criar conta de tipo novo não a torna destino possível** — só `especie` e
     `pix` são aceitos como destino de recebimento (§5.1);
  4. **criar uma segunda conta `especie` quebra os recebimentos em espécie** (§5.1);
  5. não há saldo inicial por conta — o saldo é sempre a soma dos lançamentos;
  6. a transferência não tem vínculo auditável entre as duas pontas.

---

## 5. Lacunas, riscos e proposta

### 5.1 Riscos encontrados (o mais grave primeiro)

**R1 — criar uma segunda conta `especie` derruba os recebimentos em espécie
[comprovado].** `validar_conta_para_forma` recusa quando há mais de uma conta do
tipo e o chamador não informa qual (`servico_sessao.py:170-175`). Só que **as rotas
de doação e de fiado chamam sempre com `None`** (`caixa.py:976-978`, `1586-1589`,
`1722-1725`). Resultado medido, no descartável:

| Estado | Doação em espécie |
|---|---|
| uma conta `especie` ativa | **aceita** (`?ok=1`) |
| depois de criar "Caixa da casa" como `especie` | **recusada** (`?erro=conta_invalida`) — inclusive informando `conta_id` no POST, porque a rota ignora o campo |

Ou seja: o dono que tentar criar "Caixa da casa" com o tipo `especie` **para o
caixa da lanchonete** até o problema ser percebido. E não há aviso na tela de
contas sobre isso.

**R2 — conta com tipo novo (`banco`, `poupanca`) não recebe dinheiro
[comprovado].** A tela aceita o tipo, mas `validar_conta_para_forma` só conhece
`especie` e `pix`; qualquer outro tipo é recusado como destino.

**R3 — "saldo" da tela de contas mistura dinheiro e obrigação [comprovado].**
`areceber` aparece com R$ 16.185,00 ao lado das contas de dinheiro, sem rótulo que
distinga. Um leitor apressado soma tudo como caixa.

**R4 — saldo negativo na conta de espécie [comprovado, dado real].** Não é bug de
cálculo: é consequência de sangrias que saem da conta sem destino registrado de
forma consistente. Qualquer tela de saldo por conta vai exibir isso.

**R5 — transferência sem vínculo [código].** Sem `transferencia_id`, o par só se
reconhece pela descrição. Um estorno de um lado deixa o outro órfão, e não há como
provar o par em auditoria.

**R6 — inativar não bloqueia tudo [código].** `validar_conta_para_forma` checa
`ativo`; a transferência direta (`financeiro.py:1529-1543`) e a migração não
checam. Conta inativada pode continuar recebendo por algum caminho.

### 5.2 Matriz das necessidades

| Necessidade do dono | Situação |
|---|---|
| Cadastrar "Caixa da casa" | **existente, mas sem acesso adequado** — a tela existe em Configurações (admin-only), sem link no Financeiro; e usar tipo `especie` dispara R1 |
| Cadastrar "Conta bancária" | **parcialmente implementada** — dá para criar com tipo `banco`/`outro`, mas **não recebe dinheiro** (R2); o papel dela hoje é só *destino de sangria* e *origem/destino de transferência* |
| Cadastrar "Poupança/Reserva" | **parcialmente implementada** — idem; serve como destino de transferência |
| Consultar saldo de cada conta | **existente, mas com a fórmula errada** para `areceber` e sem saldo inicial (R3) |
| Movimentar entre elas | **existente** (`/financeiro/transferir`), mas sem vínculo auditável (R5) e fora da área do Financeiro? não — está na nav; a lacuna é o vínculo |
| Editar/inativar conta | inativar **existe**; **editar não existe** |
| Histórico sem destino | **inexistente** como tela — mas no DEV **não há** movimento sem conta (0 de 862) |

### 5.3 Proposta — menor evolução coerente

Nada de tabela nova, nada de recategorizar histórico. Seis etapas pequenas, na
ordem em que reduzem risco:

**Etapa 1 — tornar a gestão acessível e segura (sem mudar dado).**
- mover/duplicar o link de contas para a **nav do Financeiro**, exigindo o módulo
  `financeiro` (permissão já existente), mantendo `/configuracoes/contas` como está
  para não quebrar links;
- **aviso na tela de contas**: "uma segunda conta do tipo *Espécie* exige escolher a
  conta em cada recebimento — hoje o caixa não oferece essa escolha". Bloquear, na
  tela, a criação de segunda conta `especie` enquanto R1 não for resolvido — recusa
  explícita é melhor que quebra silenciosa.

**Etapa 2 — destravar os tipos que o dono precisa.**
- **decisão do dono** (§6): ou `banco`/`poupanca` passam a ser tipos que recebem
  (exigindo escolher conta no PIX), ou ficam restritos a *destino de transferência*
  e a tela diz isso com clareza.

**Etapa 3 — separar dinheiro de obrigação na tela.**
- exibir as contas agrupadas: **Dinheiro** (especie, pix, banco…) e **Obrigações**
  (`areceber`), com o rótulo já usado na Visão Geral ("conta de obrigações —
  valores registrados, não dinheiro");
- corrigir a fórmula para somar **só `pago`** nas contas de dinheiro.

**Etapa 4 — saldo inicial e data de referência (se o dono quiser).**
- coluna `saldo_inicial` + `data_referencia` em `contas_financeiras`, somada ao
  cálculo. Migração simples e reversível; **não** reescreve lançamento nenhum.
- alternativa sem schema: um lançamento de abertura com categoria própria. Preferir
  a coluna, que não polui o livro-caixa.

**Etapa 5 — vínculo da transferência.**
- coluna `transferencia_id` em `financeiro_movimentacoes`, preenchida nas novas
  transferências; as antigas ficam sem vínculo e a tela diz "sem vínculo (anterior
  a esta versão)". **Sem backfill por aproximação.**

**Etapa 6 — edição de conta.**
- `POST /configuracoes/contas/{id}/editar` restrito a **nome** (tipo não muda, para
  não reinterpretar histórico).

**Impacto nos fluxos existentes**: etapas 1, 3, 5 e 6 não mudam nenhum fluxo de
dinheiro — só leitura, vínculo e cadastro. A etapa 2 é a única que mexe em regra de
recebimento, e depende de decisão do dono.

**Histórico sem destino conhecido**: hoje **não existe** no DEV. Se aparecer (outro
ambiente), a regra proposta é a mesma da TASK_012: exibir, nunca atribuir
automaticamente.

**Sessões legadas**: nada nesta proposta altera a regra; continua dependendo de
decisão explícita do dono.

---

## 6. Perguntas objetivas ao dono

Só o que a investigação **não** consegue decidir:

1. **"Caixa da casa" é a mesma coisa que "Conta Espécie" que já existe** (dinheiro
   na gaveta), ou é uma gaveta separada? A resposta decide se é conta nova (e
   dispara R1/etapa 2) ou apenas renomear a existente.
2. **Banco e Poupança vão receber dinheiro diretamente** (depósito, PIX para a
   conta do banco) ou servem só como **destino de transferência** a partir do
   caixa? Isso define a etapa 2.
3. **O saldo real de hoje de cada conta** (quanto há na gaveta, no banco, na
   poupança) — sem esse número, qualquer "saldo inicial" é invenção. Ele pode
   informar, ou autorizar começar do zero a partir de uma data.
4. **O saldo negativo da Conta Espécie (−R$ 272,14)** deve ser corrigido com saldo
   inicial, ou é reflexo de sangrias antigas que ele reconhece?
5. **Transferência precisa de vínculo auditável** (etapa 5) ou a descrição atual
   basta para a conferência dele?

---

## 7. Limitações e recursos descartáveis

**Limitações desta investigação:**

- não houve acesso autenticado ao DEV como o dono: a verificação das telas foi no
  app descartável e por leitura de código; a conferência visual com os dados dele é
  dele;
- as consultas ao DEV foram **somente leitura e agregadas** (contagens e somas por
  conta/categoria) — nenhum nome, CPF ou descrição foi lido nem transcrito;
- o "saldo negativo" e a ausência de entradas de sangria são **observação do dado**,
  não conclusão sobre a intenção original dos lançamentos;
- **R1 foi comprovado** no descartável; a extensão do efeito a todos os fluxos
  (PDV, fiado, mensalidade) é **[hipótese]** sustentada pela leitura de que todos
  chamam `validar_conta_para_forma` com a mesma assinatura.

**Recursos descartáveis usados e removidos:**

| Recurso | Estado |
|---|---|
| App em `127.0.0.1:9071` (banco `shambala_test_passes`, token `inv001`) | **encerrado** |
| 3 contas criadas (`INV001 …`) | **removidas** — 4 contas originais restauradas |
| 1 doação de teste + 1 sessão de caixa | **removidas** (dependentes primeiro: `fiado_alocacao`, `fiado_credito_mov`, `caixa_sessao_eventos`) |
| Scripts de investigação em `/tmp` | removidos ao final |
| DEV | **não** foi reiniciado, não recebeu escrita, credenciais intactas |
| Produção | **não acessada**, nem por leitura |

Nenhuma alteração de produto, schema ou dado foi feita. Nenhuma implementação foi
iniciada.
