# TASK_012 — Reestruturação financeira: resultado da execução noturna

Ordem executada: `docs/tasks/TASK_012_REESTRUTURACAO_FINANCEIRA_EXECUCAO_NOTURNA.md`.
Data: noite de 2026-10-24 → 2026-10-25 (fuso do centro; o DEV atende em `America/Sao_Paulo`).
HEAD no início da execução: `b9268ec` (docs da correção visual da TASK_011).
Status geral: **em execução** — §1 e §2 concluídos; §3–§6 em andamento; §9 (DEV) ao final.

Árvore testada: as evidências abaixo foram produzidas sobre `b9268ec` + as
alterações desta tarefa ainda não commitadas no momento das corridas; o código
da §2 ficou em `00617c1` (feat: TASK_012 §2 — idempotência de toda entrada de
dinheiro), e cada log de evidência termina com o par `antes/depois` do DEV, que
amarra a corrida ao estado real do servidor no momento em que rodou.

Limites observados: sem produção, sem push, sem mensagens/cobranças, sem novos
usuários/senhas/permissões, testes exclusivamente em PostgreSQL descartável
validado pelas guardas da TASK_010, DEV apenas no serviço HTTP existente da
porta 9000, nenhuma operação de demonstração sobre fatos reais.

---

## 1. Levantamento e matriz

### 1.1 Estado real verificado antes de editar

O levantamento da TASK_005 (FIN-01…FIN-09) foi confirmado **pontualmente**
contra o código desta árvore, e vários itens já foram entregues por etapas
posteriores — assumi-los abertos teria desfazer trabalho alheio:

| Achado de TASK_005 | Estado em `b9268ec` |
|---|---|
| FIN-01 preço vindo do cliente | **resolvido** pela TASK_006 (servidor precifica; divergência reprifica a cesta e recusa a escrita) |
| FIN-02 cancelamento que não reverte | **resolvido** pela TASK_007 (`servico_cancelamento`, prova de reversão, bloqueios) |
| FIN-03 crédito antecipado sem dinheiro registrado | **resolvido** pela TASK_008 (`credito_anticipado` entra no financeiro com destino explícito) |
| FIN-04 fechamento misto (espécie/PIX/fiado) | **resolvido** pela TASK_008 (sessões `eventos` com `caixa_sessao_eventos` e efeito físico por forma) |
| FIN-05 duplicidade de mensalidade por competência | **aberto** → §3 |
| FIN-06 estorno de fiado pago | **resolvido** pela TASK_007 (recusa); reembolso continua fora de escopo (§7) |
| FIN-07 consultas/datas divergentes | **parcial** — TASK_011 tratou visão geral e cache; fórmulas de saldo por conta seguem divergentes → §5 |
| FIN-08 constraints/autorias/FKs | **parcial** (TASK_009/010 acrescentaram travas de valor e sessão) → §6 |
| FIN-09 foto da compra escrita antes do commit | **aberto** → §6 |
| Idempotência de reenvio | **inexistente como mecanismo** — só havia garantias pontuais derivadas de estado → §2 |

Garantias existentes que **não** foram reescritas nesta tarefa (e continuam
provadas pelas suítes próprias): preço no servidor + `Decimal` (TASK_006),
cancelamento seguro (TASK_007), eventos/efeito físico e recusa de sessão
legada (TASK_008), isolamento de ambiente e guardas de destino (TASK_010),
`asset_url` + apresentação da visão geral (TASK_011).

### 1.2 Fonte de verdade por domínio (o que manda em cada conta)

| Domínio | Fato | Onde vive | Quem deve ler |
|---|---|---|---|
| Obrigação de mensalidade | lançamento `entrada/mensalidade` com `status` | `financeiro_movimentacoes` (`data_movimentacao` fazendo as vezes de competência) | consultas a receber; §3 adiciona competência explícita |
| Dívida de fiado | venda `forma_pagamento='fiado'` não quitada | `vendas_pdv.fiado_pago` + `total - fiado_credito_usado` | extrato do trabalhador; §4 adiciona alocações |
| Pagamento recebido (dinheiro que entrou) | lançamento `entrada` com conta e forma | `financeiro_movimentacoes` (`recebimento_fiado`, `credito_anticipado`, `doacao`, `venda_pdv`, `mensalidade` baixada) | relatórios de resultado |
| Alocação do recebimento | qual dívida/credito cada recebimento abateu | **não existia** (só o booleano na venda) | §4 |
| Crédito do trabalhador | saldo + movimentos | `trabalhadores.fiado_credito` (saldo) e `fiado_credito_mov` (razão) | extrato; consumo não é dinheiro novo |
| Efeito na gaveta | evento por sessão | `caixa_sessao_eventos` (`identidade` UNIQUE) | fechamento/conferência; **não** somar com o lançamento correspondente |
| Tentativa de operação | chave do envio | `operacao_idempotencia` (nova nesta tarefa) | rotas de dinheiro; retenção sem purga automática |

### 1.3 Matriz etapa → contrato → alteração → teste → dependências

| Etapa | Contrato atual (antes) | Alteração entregue | Teste | Depende de |
|---|---|---|---|---|
| §2 idempotência | Reenvio produzia segundo efeito; proteção pontual só onde havia estado (`fiado_pago`, `status='aberto'`, `identidade` UNIQUE, `vendas_pdv_cancelamento.venda_id`) | Chave de tentativa gerada com a tela (`campo_idempotencia` + `static/js/tentativa.js`), tabela `operacao_idempotencia` com `chave UNIQUE`, reserva→efeitos→confirmação na mesma transação, resposta anterior devolvida antes das validações de estado, recusa legível sem escrita | `tests/test_financeiro_idempotencia_task012.py` (138 asserções: 1º efeito, reenvio byte a byte, concorrência mesma chave nova e concluída, chave+conteúdo divergente, chave nova+conteúdo idêntico, POST sem chave, revisão de preço, rollback da última escrita, corrida na reserva, lote de mensalidades, parcial com excedente, compra multipart com a nota no corpo, venda de livro, autenticação) | nada (base das demais) |
| §3 mensalidades | Competência inferida de `data_movimentacao LIKE 'YYYY-MM%'` e do texto da descrição; `_existe_mes` + INSERT sem constraint (FIN-05) | — em execução — | — | §2 |
| §4 fiados | Um recebimento quita dívidas por ordem FIFO e o excedente vira crédito **sem vínculo** entre o lançamento, as dívidas e o crédito; `fiado_pago` booleano é a única alocação | — em execução — | — | §2, §3 |
| §5 consultas | Soma de eventos com lançamentos, fórmulas de saldo divergentes por tela, GET `/financeiro/mensalidades` gerando cobrança | — em execução — | — | §3, §4 |
| §6 integridade | FIN-09: arquivo da nota escrito dentro da transação antes do commit | — em execução — | — | §2 |

### 1.4 Provas dos defeitos antes de corrigir (origem §1.5 da ordem)

O "antes" é estabelecido lendo a revisão anterior a esta etapa (`b9268ec`), não
por uma execução do código antigo — não subimos uma instância velha no DEV nem
fora dele (limites §0). O que a leitura mostra, rota por rota:

- `POST /caixa/doacao` (`rotas/caixa.py:877-925` em `b9268ec`): nenhuma
  identidade de tentativa; o `INSERT INTO financeiro_movimentacoes … ('entrada',
  'doacao', …)` é incondicional e o evento da gaveta usa
  `identidade=f"doacao:{fin_id}"` — como `fin_id` nasce de um `RETURNING id` a
  cada requisição, o reenvio byte a byte cria o segundo lançamento e um segundo
  evento sem violar nenhuma UNIQUE.
- `POST /financeiro/transferir` (`rotas/financeiro.py:1202-1240` em `b9268ec`):
  idem, com o par saída/entrada escrito sem condição — reenvio duplica as duas
  linhas (quatro lançamentos por duas confirmações). O valor ainda passa por
  `round(float(…))`.
- `POST /caixa/fiados/{venda}/pagar` (em `b9268ec`): já não duplicava, mas pela
  **condição do documento** (`fiado_pago`), não pela tentativa — é o contraste
  que a matriz §1 usa para separar "protegido por estado" de "protegido por
  identidade de operação".

O "depois" é executado: `tests/test_financeiro_idempotencia_task012.py` mede o
delta real dessas mesmas rotas no banco descartável e exige zero novo efeito no
reenvio byte a byte (§2.3/§2.4). Registra-se aqui a diferença de natureza entre
as duas metades da prova, para que ninguém leia o item "antes" como execução.

---

## 2. Idempotência das operações financeiras — entregue

### 2.1 O mecanismo

- **`operacao_idempotencia`** (`banco.py`, criada por DDL idempotente no
  `criar_tabelas`, sem arquivo de migração): `chave TEXT UNIQUE`, `operacao`,
  `corpo_hash`, `status` (`em_andamento`/`concluida`), `autor_id`,
  `documento`, `destino`, `criado_em`, `concluido_em`. As linhas **não** são
  apagadas automaticamente — a retenção é o que impede reciclar chave de
  operação já confirmada (§2 da ordem).
- **`servico_idempotencia.py`** — `nova_chave()` (`secrets.token_hex(16)`),
  `normalizar_chave()` (aceita só `^[0-9a-f]{16,64}$`), `_hash_corpo()`
  (SHA-256 de JSON canônico, `sort_keys`), `reservar()` (INSERT … `ON CONFLICT
  (chave) DO NOTHING RETURNING id` e, em conflito, `SELECT … FOR UPDATE` — a
  própria espera de inserção especulativa do PostgreSQL serializa concorrentes,
  sem advisory lock novo), `confirmar()`, `liberar()`, `exigir_reserva()` e
  `reconhecer()`.
- **A chave nasce com a tela**: `campo_idempotencia()` é global do Jinja e é
  impresso dentro de cada formulário de dinheiro; `static/js/tentativa.js`
  preenche um campo vazio no envio e **nunca** regenera uma chave já existente
  (duplo clique mantém a mesma tentativa). Um POST sem chave é recusado com
  orientação legível, nunca com chave inventada.
- **Ordem dentro da rota**: reconhecimento da tentativa (leitura) → validações
  de estado → reserva (escrita) → efeitos → confirmação (última escrita da
  mesma transação). O reconhecimento antes das validações é o que faz o reenvio
  devolver *a mesma resposta* mesmo depois que o mundo mudou (dívida já
  quitada, pendência baixada); a reserva continua depois das validações para
  nenhuma chave ser consumida sem documento.
- **Canonicalização**: o corpo compara conteúdo **enviado** (valores, conta,
  sessão, forma, itens). O que o servidor recalcula (preço do catálogo,
  subtotal, troco) não entra na hash — reprecificar o catálogo não transforma
  um reenvio legítimo em conflito, e uma cesta com preço aberto divergente
  continua sendo conteúdo divergente.

### 2.2 Entradas protegidas e entradas que já estavam

| Entrada (rota) | Proteção |
|---|---|
| `POST /caixa/pdv/finalizar` | tentativa nova (corpo = cesta enviada, forma, recebido, doação, sessão) |
| `POST /caixa/doacao` | tentativa nova |
| `POST /caixa/sangria` | tentativa nova |
| `POST /caixa/fiados/{venda_id}/pagar` | tentativa nova + estado `fiado_pago` |
| `POST /caixa/fiados/{trabalhador_id}/pagar-divida` | tentativa nova + estado das dívidas |
| `POST /caixa/fiados/credito/{trabalhador_id}` | tentativa nova |
| `POST /financeiro/nova` | tentativa nova (reserva já era a primeira escrita) |
| `POST /financeiro/transferir` | tentativa nova, agora em `Decimal` (par saída/entrada confirmado na mesma transação) |
| `POST /financeiro/mensalidades/receber` | tentativa nova sobre a seleção canônica de ids |
| `POST /financeiro/compras` | tentativa nova — corpo inclui o hash do arquivo da nota (reenvio da mesma nota não gera segunda despesa) |
| `POST /biblioteca/livro/{id}/vender` | tentativa nova (baixa de estoque + lançamento financeiro no mesmo documento) |
| `POST /caixa/abrir` | **já protegida** — advisory lock por caixa + `uq_caixa_movimentos_aberto` (sessão aberta única) |
| `POST /caixa/fechar` | **já protegida** — `status='aberto' … FOR UPDATE`; reenvio não encontra sessão aberta |
| `POST /caixa/vendas/{id}/cancelar`, `POST /caixa/fiados/{venda_id}/cancelar` | **já protegida** — `vendas_pdv_cancelamento.venda_id UNIQUE` (TASK_007) |
| `POST /financeiro/mensalidades/{mov_id}/cancelar` | **já protegida** por estado (`status='pendente'` no WHERE) |
| `POST /estoque/entrada`, `/estoque/perda` | fora do contrato §2: não movem dinheiro (movem estoque) — ficam sem chave para não ampliar escopo |
| rotas de presença/atendimento (`dia.py`, `checkin.py`, `agenda.py`) | não financeiras; `dia.py` mantém o plano próprio de `plano_chave_idempotencia` |

Nenhuma entrada financeira de escrita ficou desprotegida depois do inventário:
as duas que faltavam (`compras` e `biblioteca/vender`) entraram; esta última
contradiz a nota da TASK_005 que não localizou escritor de `vendas_livros`
ligado ao financeiro — `rotas/biblioteca.py:397-411` escreve os dois.

### 2.3 O que o contrato agora recusa (prova)

| Situação | Comportamento |
|---|---|
| Reenvio byte a byte (resposta perdida, duplo clique) | mesmo `Location` gravado em `destino`, **zero** delta em vendas/itens/eventos/estoque/financeiro/crédito |
| Duas requisições simultâneas, mesma chave nova | exatamente um efeito; uma linha em `operacao_idempotencia` |
| Duas requisições simultâneas, tentativa já concluída | zero efeito |
| Mesma chave, conteúdo divergente | `?erro=tentativa_conflito`, zero escrita |
| Chave nova, conteúdo idêntico | segunda operação legítima (ou recusa pelo estado do documento — nunca dinheiro novo silencioso) |
| POST sem chave | `?erro=tentativa_sem_chave`, zero escrita |
| PDV com preço divergente do catálogo | a cesta volta reprificada (200), a tentativa **não** é consumida, a tela expõe chave nova e a confirmação conclui sem conflito |
| Falha na última escrita | transação desfaz reserva e efeitos; o reenvio da mesma chave conclui a operação |
| Anônimo com chave válida | recusa por autenticação — chave não é credencial |

### 2.4 Evidências

- `docs/tasks/evidencias/TASK_012/suite_idempotencia_r3.txt` — suíte da etapa:
  **87/87** verificações aprovadas em ambiente descartável (app real + banco
  selado), com `DEV inalterado: ok` antes/depois.
- `docs/tasks/evidencias/TASK_012/suite_idempotencia_r4.txt` — mesmas famílias
  com as duas entradas do inventário que faltavam (compra multipart, venda de
  livro): **134/135**, com uma falha real de harness (a contagem de esperas em
  `pg_stat_activity` passava `params=()` e psycopg2 interpretava o `%` do `LIKE`
  como marcador → `IndexError`); corrigida na suíte, não na assertiva.
- `docs/tasks/evidencias/TASK_012/suite_idempotencia_r5.txt` — suíte após a
  correção: **138/138** aprovadas, rc=0, `DEV inalterado: ok`.
- `docs/tasks/evidencias/TASK_012/task008_tentativa_harness.txt` — suíte de
  navegador da TASK_008 depois de devolver chave aos dois POSTs diretos que
  medem recusa de estado: **41 aprovadas, 0 falhas**.
- `docs/tasks/evidencias/TASK_012/runner_completo_etapa2_r2.txt` — runner
  completo com as famílias novas: 17 suítes verdes e **1 falha real**
  (`test_caixa_navegador_task008`, C1/C11 — ver limitação 6). Mantido como
  evidência da corrida que encontrou o problema.
- `docs/tasks/evidencias/TASK_012/runner_completo_etapa2_final.txt` — runner
  completo consolidado: **18/18 suítes rc=0**, 659 verificações aprovadas
  (27+12+8+10+19+18+18+24+21+11+33+25+161+41+51+42+138) mais o teste negativo
  de isolamento, com `DEV inalterado: ok` (pessoas=678, checkins=2918 antes e
  depois). Nenhum pulou; nenhuma assertiva foi reduzida para obter PASS.

### 2.5 Limitações e questões registradas

1. **Retenção**: as tentativas ficam para sempre (`operacao_idempotencia` sem
   expiração). É escolha deliberada — expurgar identidade recente é o que
   permitiria reciclar chave. Se o volume incomodar, a discussão é arquivamento,
   não DELETE.
2. **`documento`/`destino` são texto**: guardam o id do lançamento e a URL de
   sucesso para devolver exatamente a mesma resposta. Não são FK — a tentativa
   sobrevive ao documento que criou de propósito (o reenvio responde "já foi
   feito", não "o documento sumiu").
3. **Duas chaves para a mesma intenção continuada**: se o operador recarrega a
   tela, a tentativa é nova por definição (a recarga é uma nova intenção). Isso
   é o contrato do §2 — a proteção é contra *reenvio*, não contra "cliquei
   duas vezes em páginas diferentes". A segunda operação legítima só não é
   aceita onde o estado do documento a impede (dívida já quitada, pendência
   baixada).
4. **Regra de negócio não autorizada, preservada**: `POST /financeiro/nova`
   aceita `valor` em `float` (`rotas/financeiro.py:551`) e `compras` também
   (`rotas/compras.py:73`). Trocar para `Decimal` é do contrato de dinheiro,
   mas altera arredondamento de lançamentos avulsos; fica registrado para a
   decisão do dono na etapa §5, não silencioso aqui.
5. **Compatibilidade**: clientes que postem esses formulários sem o campo
   `idempotencia` passam a receber recusa legível. Nenhum cliente interno além
   das telas reais foi encontrado; as suítes que exercitam essas rotas
   (`test_sessao_eventos_caixa`, `test_pdv_precos_servidor`,
   `test_caixa_navegador_task008`, `repro_task007_fin02`, `repro_task008_fin04`)
   receberam o campo no harness, sem redução de assertiva.
6. **Precedência da recusa sem chave**: o reconhecimento da tentativa vem antes
   das validações de estado, então um POST de teste que omita a chave recebe
   `tentativa_sem_chave` em vez do erro de negócio que a asserção esperava
   (foi o caso de C1/C11 em `test_caixa_navegador_task008`, que procuravam
   `sessao_necessaria`). O harness passou a enviar chave nova nesses dois
   pontos e a assertiva original voltou a valer; a evidência da volta está em
   `docs/tasks/evidencias/TASK_012/task008_tentativa_harness.txt` (41/41).

---

## 3. Mensalidades por trabalhador e competência

_em execução_

## 4. Fiados, pagamentos parciais e crédito rastreável

_em execução_

## 5. Consultas, saldos e experiência integrada

_em execução_

## 6. Integridade técnica complementar (FIN-08/09)

_em execução_

## 9. Migração e DEV

_pendente — aplicado ao final, com backup, hash, restauração validada em banco
descartável, baseline antes/depois e restart do serviço da 9000_

## 10. Roteiro de conferência do dono

_pendente_
