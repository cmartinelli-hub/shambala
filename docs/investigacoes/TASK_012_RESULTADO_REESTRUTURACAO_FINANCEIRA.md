# TASK_012 — Reestruturação financeira: resultado da execução noturna

Ordem executada: `docs/tasks/TASK_012_REESTRUTURACAO_FINANCEIRA_EXECUCAO_NOTURNA.md`.
Data: noite de 2026-10-24 → 2026-10-25 (fuso do centro; o DEV atende em `America/Sao_Paulo`).
HEAD no início da execução: `b9268ec` (docs da correção visual da TASK_011).
Status geral: **PARCIALMENTE CONCLUÍDA.** §2 a §6 têm implementação e prova e
estão aplicadas no DEV (§9); ficam abertos, nomeados no §12: FIN-09 sob revisão
(a janela entre commit e arquivo não é eliminável sem arquitetura nova), FIN-06
contido por bloqueio com reembolso pendente, homologação autenticada do dono, os
43 recebimentos de fiado do legado e a publicação.

**Retomada de 2026-10-25 (manhã).** A sessão anterior foi interrompida por limite
de uso no meio da edição da limpeza de `tests/test_financeiro_consultas_task012.py`.
A retomada começou pelo que estava incompleto — a limpeza da suíte §5 (§5.6),
que apagava dados de outras execuções —, validou-a com dados alheios plantados
no mesmo banco e seguiu para a §6 (FIN-09). O que é herdado e o que é desta
retomada está separado em §11.

**Devolução do dono (2026-10-25, segunda parte).** A primeira entrega dizia
"concluída" e foi recusada com sete pontos concretos — regressão do leitor,
FIN-09 declarado resolvido sem prova da morte entre commit e arquivo, contradição
sobre a chave, concorrência de mensalidade não demonstrada, erro de dinheiro no
roteiro, redação imprecisa do DEV e matriz otimista. Todos eram procedentes e
estão respondidos: §3.3 (trava real), §5.6 (limpeza), §6.5/§6.6 (ciclo do
FIN-09), §8 (navegador da TASK_012), §9.5 (previsto × preservado, alcance do
hash, contagem 516), §10 (roteiro corrigido) e §12 (estado real).

Árvore testada: as evidências abaixo foram produzidas sobre `b9268ec` + as
alterações desta tarefa ainda não commitadas no momento das corridas — e, para
as etapas desta retomada, sobre `37f3dc4` + o trabalho em curso, com a corrida
final repetida já sobre os commits `e8d8d8e`, `c17b5cd` e `59dd73b` (logs
`runner_completo_etapa5_6_r1.txt` e `suite_etapa5_6_financeiro_r1.txt`). O código
da §2 ficou em `00617c1` (feat: TASK_012 §2 — idempotência de toda entrada de
dinheiro), o da §3 em `a4c5346` (feat: TASK_012 §3 — mensalidade passa a ter
competência) e o da §4 em `a6f0328` (feat: TASK_012 §4 — o recebimento de fiado
passa a ter destino registrado); cada log de evidência termina com o par
`antes/depois` do DEV, que amarra a corrida ao estado real do servidor no momento
em que rodou. Os logs da §4 registram no cabeçalho a árvore
`cf4d8da…-dirty` — o sufixo `-dirty` é o próprio harness dizendo que a árvore
tinha alteração não commitada (o código desta etapa); as duas corridas de
repetibilidade compartilham a mesma identidade registrada e tokens diferentes.

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
| FIN-06 estorno de fiado pago | **contido por bloqueio**, não resolvido: a TASK_007 recusa o estorno (e a §4 ampliou a recusa para recebimento alocado). O **reembolso continua pendente** — não existe caminho para devolver dinheiro de fiado já pago, e a ordem o deixa fora de escopo (§7) |
| FIN-07 consultas/datas divergentes | **resolvido no módulo financeiro** pela §5 (fórmula única no serviço, três datas rotuladas, filtros validados); conciliação bancária segue fora de escopo |
| FIN-08 constraints/autorias/FKs | **parcial** — §2/§3/§4 acrescentaram as travas do escopo; backfill de autoria histórica, `auditoria_eventos` e duplicidade histórica de mensalidade seguem abertos por decisão explícita (§6.4) |
| FIN-09 foto da compra escrita antes do commit | **em revisão** — a §6 corrigiu o ciclo (temporário + promoção atômica + recuperação no startup com limites estreitos e provados, §6.6) e provou os desfechos, mas a separação entre commit e promoção não tem atomicidade possível: o que existe é detecção e compensação (§6.5). Fica registrado como **corrigido e sob revisão**, não como fechado |
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
| Dívida de fiado | venda `forma_pagamento='fiado'` não quitada | `vendas_pdv.fiado_pago` + `total - fiado_credito_usado - alocado` (o `alocado` é da §4) | extrato do trabalhador |
| Pagamento recebido (dinheiro que entrou) | lançamento `entrada` com conta e forma | `financeiro_movimentacoes` (`recebimento_fiado`, `credito_anticipado`, `doacao`, `venda_pdv`, `mensalidade` baixada) | relatórios de resultado |
| Alocação do recebimento | qual dívida/crédito cada recebimento abateu | `fiado_alocacao` (nova nesta tarefa — vínculo durável lançamento↔dívida + linha de excedente) | consultas de fiado, extrato, cancelamento |
| Crédito do trabalhador | saldo + movimentos | `trabalhadores.fiado_credito` (saldo, com `CHECK ≥ 0` da §4) e `fiado_credito_mov` (razão, agora com `venda_id`/`financeiro_id`) | extrato; consumo não é dinheiro novo |
| Efeito na gaveta | evento por sessão | `caixa_sessao_eventos` (`identidade` UNIQUE) | fechamento/conferência; **não** somar com o lançamento correspondente |
| Tentativa de operação | chave do envio | `operacao_idempotencia` (nova nesta tarefa) | rotas de dinheiro; retenção sem purga automática |

### 1.3 Matriz etapa → contrato → alteração → teste → dependências

| Etapa | Contrato atual (antes) | Alteração entregue | Teste | Depende de |
|---|---|---|---|---|
| §2 idempotência | Reenvio produzia segundo efeito; proteção pontual só onde havia estado (`fiado_pago`, `status='aberto'`, `identidade` UNIQUE, `vendas_pdv_cancelamento.venda_id`) | Chave de tentativa gerada com a tela (`campo_idempotencia` + `static/js/tentativa.js`), tabela `operacao_idempotencia` com `chave UNIQUE`, reserva→efeitos→confirmação na mesma transação, resposta anterior devolvida antes das validações de estado, recusa legível sem escrita | `tests/test_financeiro_idempotencia_task012.py` (138 asserções: 1º efeito, reenvio byte a byte, concorrência mesma chave nova e concluída, chave+conteúdo divergente, chave nova+conteúdo idêntico, POST sem chave, revisão de preço, rollback da última escrita, corrida na reserva, lote de mensalidades, parcial com excedente, compra multipart com a nota no corpo, venda de livro, autenticação) | nada (base das demais) |
| §3 mensalidades | Competência inferida de `data_movimentacao LIKE 'YYYY-MM%'` e do texto da descrição; `_existe_mes` + INSERT sem constraint (FIN-05) | Coluna `competencia` (`YYYY-MM`) em `financeiro_movimentacoes`; índice único parcial `(trabalhador_id, competencia)` para `categoria='mensalidade'` criado no startup, com diagnóstico de duplicidade **antes** de aplicar e sem tocar linha em conflito; serviço com checagem + `ON CONFLICT … DO NOTHING` e retorno de "quantas novas"; `POST /mensalidades/gerar` (geração manual) no lugar do GET que gerava; recusas `periodo_invalido` / `mensalidade_ja_registrada` / `sem_mensalidade_ativa`; competência, vencimento e recebimento separados na tela + faixa de conflito histórico | `tests/test_financeiro_mensalidades_task012.py` (64 asserções: chave do banco nos quatro sentidos, geração repetida e concorrente, dois trabalhadores no mesmo mês, virada de ano, mês curto e bissexto, elegibilidade, leitura sem efeito, mensagens de operador, baixa individual e em lote, lote atômico com rollback, duplicidade histórica fictícia sem perda de fatos, rotulamento que não escolhe vencedora, startup que não aborta, backfill idempotente, cancelamento, lançamento manual) | §2 |
| §4 fiados | Um recebimento quita dívidas por ordem FIFO e o excedente vira crédito **sem vínculo** entre o lançamento, as dívidas e o crédito; `fiado_pago` booleano é a única alocação | Tabela `fiado_alocacao` (lançamento ↔ dívida, ou linha única de excedente) com `CHECK` de vínculo, índices únicos e FKs `RESTRICT`; `servico_fiado` (`dividas_em_aberto`/`repartir`/`aplicar_recebimento`/`extrato_fiado`) em `Decimal`; quitação só com saldo zero; excedente com lock do trabalhador; vínculo `fiado_credito_mov.venda_id`/`.financeiro_id`; guarda `fiado_credito >= 0` no banco; tela marca dívidas, mostra a distribuição antes de confirmar e lê o saldo; extrato novo `GET /caixa/fiados/extrato/{id}`; cancelamento passa a avaliar as alocações | `tests/test_fiado_alocacoes_task012.py` (91 asserções em 13 provas: dívida 40 com 15+25, 50 contra 40 com crédito 10, adiantamento e consumo parcial, duas dívidas selecionadas, pagamento×pagamento e consumo×consumo sob lock observado, reenvio pela mesma chave, última escrita falhando, legado sem backfill, cancelamento, e as cinco contas batendo por caminhos SQL independentes) + suítes alheias ajustadas sem reduzir assertiva | §2, §3 |
| §5 consultas | Soma de eventos com lançamentos, fórmulas de saldo divergentes por tela, GET `/financeiro/mensalidades` gerando cobrança (este último **resolvido na §3**) | Cinco funções de consulta no `servico_fiado` (uma conta para todas as telas: valor original − crédito usado − alocado); fiado em aberto com Recebido/Saldo e totais do recorte; Σ acumulado por conta declarado; relatórios separando recebido/pago/a receber/a pagar/cancelado; três datas rotuladas em mensalidades; `Decimal`+`moeda` em todas as telas tocadas; filtros inválidos recusados com mensagem; PIX e lançamento com o mesmo parser de valor | `tests/test_financeiro_consultas_task012.py` (74 asserções em 12 provas: saldo parcial em 4 telas, totais × página, legado sem prova, venda fiada não é receita, obrigação × dinheiro, filtros inválidos, três datas, política de valor, permissões URL+POST, GET sem efeito, apresentação TASK_011, serviço como conta única) | §3, §4 |
| §6 integridade | FIN-09: arquivo da nota escrito dentro da transação antes do commit | Arquivo fora da transação: temporário ao lado do destino (disco testado antes do commit), promoção por `os.replace` depois, remoção só do próprio temporário no rollback, compra cancelada (nunca apagada) quando a promoção falha, `_caminho_da_nota` validando `static/fotos`; injeção de falha de disco guardada por MODO_TESTE + banco com selo | `prova_nota_fora_da_transacao` em `tests/test_financeiro_idempotencia_task012.py` (7 asserções: sucesso, preparo falhando, rollback por trigger real, promoção falhando) | §2 |

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

## 3. Mensalidades por trabalhador e competência — entregue

### 3.1 A chave, documentada (o que ela abrange e o que deixa de fora)

**Coluna nova**: `financeiro_movimentacoes.competencia` (`TEXT`, formato
`YYYY-MM`), adicionada em `banco.py:560` (`_migrar.para_adicionar`) e também no
`CREATE TABLE` (`banco.py:1066`) para instalação nova. Só lançamento de
`categoria='mensalidade'` usa a coluna; o resto fica `NULL`.

**Guarda do banco**: índice único parcial criado no startup —

```sql
CREATE UNIQUE INDEX uq_mensalidade_por_competencia
    ON financeiro_movimentacoes (trabalhador_id, competencia)
    WHERE categoria = 'mensalidade'
      AND trabalhador_id IS NOT NULL
      AND competencia IS NOT NULL;
```

Constantes em `banco.py:430-432` (`UQ_MENSALIDADE_COMPETENCIA` e
`PREDICADO_MENSALIDADE`); o `ON CONFLICT` do serviço reproduz o predicado
literalmente, então mudar um exige mudar o outro (é o que a asserção
*banco: segunda linha do mesmo trabalhador/competência é recusada* prova).

**A chave abrange todos os status** — `pendente`, `pago` **e** `cancelado`. Isso
é deliberado e vem da regra existente, não de invenção desta etapa:
`_existe_mes` sempre considerou "existiu QUALQUER lançamento neste mês → não se
recria", inclusive o cancelado. Se a chave fosse restrita a `pendente`, a
geração passaria a recriar mês cancelado (mudança de regra de negócio).
Registrar recebimento histórico e cancelar continuam sendo `UPDATE` da mesma
linha, nunca `INSERT`, portanto a chave não atrapalha nenhum dos dois — provado
em *recebimento preserva a competência da obrigação* e
*mês cancelado NÃO é recriado pela geração (regra existente)*.

**O que fica fora da chave, por construção**: (a) mensalidade lançada sem
trabalhador (`trabalhador_id IS NULL` — 4 linhas assim no DEV hoje):
despesa/receita avulsa etiquetada como mensalidade não é obrigação de ninguém e
não pode ser deduplicada por trabalhador; (b) mensalidade com vencimento
ilegível (`competencia IS NULL`): o rótulo do startup não adivinha período, a
linha fica visível como conflito histórico na tela em vez de ser reescrita.

### 3.2 A competência deixou de ser texto

Antes, o período vivia em dois lugares frágeis: o `LIKE 'YYYY-MM%'` sobre
`data_movimentacao` em `_existe_mes` e o trecho `" - {pref}"` colado na
descrição. Hoje a deduplicação, a ordenação e a exibição usam a coluna; a
descrição continua sendo escrita (texto legível para o operador) mas **não é
fonte de verdade**.

A segunda perna de `_existe_mes` (`competencia IS NULL AND left(data,7) = mês`)
só alcança linha legada que o rótulo não pôde rotular: serve para ela não virar
segunda cobrança, e não reinterpreta nada (prova: *vencimento ilegível NÃO é
reinterpretado (fica NULL)*).

**Backfill do startup** (`banco.py:_migrar_mensalidades_competencia`):
`competencia = left(data_movimentacao, 7)` para mensalidade ainda não rotulada
com vencimento no formato `AAAA-MM…`. Duas regras tornam esse rótulo seguro:

1. **o diagnóstico é feito sobre a competência *efetiva*** — o rótulo já
   gravado ou, nas linhas sem rótulo, o mês do vencimento legível
   (`banco.py:_comp_efetiva`). Um grupo com duas linhas do mesmo
   trabalhador/mês é conflito mesmo que nenhuma delas ainda tenha rótulo;
2. **o rotulamento nunca estoura a própria constraint**: uma linha só é
   rotulada se nenhum *outro* lançamento daquele trabalhador cai no mesmo mês
   (`NOT EXISTS` na `UPDATE`). Em grupo disputado, **as duas ficam como estão**
   — rotular uma seria escolher vencedora. O índice único então não é criado e
   o impedimento é impresso no startup, sem alterar linha alguma.

Isso importa porque startup que aborta é serviço fora do ar: a primeira versão
desta migração rotulava às cegas e quebrou no runner (um `UniqueViolation` dentro
de `_migrar` derrubava o uvicorn e contaminava todas as suítes seguintes —
evidência em §3.7, corrida r1).

Diagnóstico no DEV antes de escrever qualquer coisa —
`docs/tasks/evidencias/TASK_012/diagnostico_dev_mensalidades.txt`:
520 mensalidades, 520 com vencimento legível, **0** em que o mês do vencimento
diverge do mês já escrito na descrição, **0** grupos duplicados de
(trabalhador, mês), 4 sem trabalhador. Ou seja: rotular não reescreve fato
nenhum e o índice pode ser criado sem conflito histórico.

### 3.3 Repetição e concorrência não duplicam — e o que faltava aqui

**Correção de uma afirmação errada desta seção.** A versão anterior dizia que
"checagem + transação" protege a geração. Não protege: `_existe_mes` é uma
LEITURA. Com o índice único presente, quem impede o segundo lançamento é o banco;
**sem** o índice — o estado que o startup deixa quando encontra duplicidade
histórica e se recusa a criá-lo — não havia proteção nenhuma. Medido com o
índice derrubado e duas conexões atrás de uma barreira: as duas inseriram, 2
linhas na mesma competência do mesmo trabalhador.

A proteção agora é `_travar_geracao`: `pg_advisory_xact_lock(hashtext(
'mensalidade_geracao'), trabalhador_id)` antes de olhar o que existe. Serializa
só a geração daquele trabalhador (dois trabalhadores não esperam um pelo outro),
é liberada no commit/rollback e não depende de índice, tabela de controle nem
coluna nova. Provado em `prova_concorrencia_sem_indice`, que roda o caminho real
do serviço com o índice ausente e exige 1 linha e o par "criei"/"já existia" — e
restabelece a guarda do banco ao fim.


Serviço reescrito (`rotas/financeiro_mensalidades.py`): `inserir_mensalidade`
usa `ON CONFLICT (trabalhador_id, competencia) WHERE <predicado> DO NOTHING
RETURNING id` quando o índice existe e devolve `None` quando o mês já tinha
obrigação; `sincronizar_mensalidades` passou a **retornar quantas obrigações
novas criou** (0 = já gerada). A checagem `_existe_mes` continua primeiro — o
`ON CONFLICT` é a camada que absorve a corrida entre duas gerações simultâneas.

**Quando o índice não pôde ser criado** (duplicidade histórica real), o serviço
não perde a proteção: `_indice_ativo(conn)` detecta a ausência, o `ON CONFLICT`
é omitido (seria erro de sintaxe de constraint inexistente) e a checagem + a
transação da request sustentam a deduplicação. Provas:
*sem a constraint, o serviço ainda não duplica o mês em conflito* e
*concorrência: duas gerações simultâneas criam 13 linhas, não o dobro*.

### 3.4 Todos os caminhos que geram cobrança, e o que mudou

| Caminho | Onde | Comportamento |
|---|---|---|
| Startup | `main.py:47` → `gerar_todas_ativas()` | inalterado: só `ativo = 1 AND valor_mensalidade > 0` (regra existente preservada); não cobra inativo, não cria mês fora da janela |
| Cadastro do trabalhador | `rotas/trabalhadores.py:337` | idem, pela mesma função do serviço |
| Edição / reativação | `rotas/trabalhadores.py:502,504` | `atualizar_pendentes_futuras` (reajusta valor/vencimento **mantendo a competência**) ou `sincronizar_mensalidades` |
| **Geração manual** (botão novo) | `POST /financeiro/mensalidades/gerar` (`rotas/financeiro.py:702`) | cria a janela e informa quantos são novos; inativo/valor zero → recusa legível e **zero escrita** |
| Abertura da tela | `GET /financeiro/mensalidades` | **não gera mais nada** — era efeito colateral de ler |
| Baixa individual / lote | `POST /financeiro/mensalidades/receber` | `UPDATE` da linha selecionada; competência preservada |
| Cancelamento | `POST /financeiro/mensalidades/{id}/cancelar` | inalterado; mês cancelado não é recriado |
| Lançamento avulso | `POST /financeiro/nova` | mensalidade sem período legível é recusa; mesma chave do banco |

Limite de janela: `MESES_A_FRENTE = 12` (mês corrente + 12), com a virada de ano
provada (`2026-12 → 2027-01`, `2027-12` no fim da janela) e o dia de vencimento
truncado para o último dia do mês curto (`dia 31 → 2027-02-28`,
`dia 29 → 2028-02-29` no bissexto) sem inventar data.

### 3.5 Lote: política explícita, sem baixa parcial oculta

Recebimento em lote baixa **exatamente** as pendências selecionadas; a que já
estava paga por outra mão não é reescrita (data e forma preservadas) e não entra
no total. O total devolvido é a soma do que **foi baixado**, não do que foi
selecionado — a tela não esconde diferença. Seleção inteiramente já recebida é
recusada (`sem_itens`) sem escrever dinheiro. O lote inteiro vive uma transação:
falha no meio (a prova planta uma identidade de evento duplicada para o segundo
item) devolve erro de servidor e **não baixa nenhuma mensalidade nem grava
evento**; removido o obstáculo, o mesmo lote conclui.

### 3.6 Mensagens de operador, sem SQL

| Situação | O que o operador lê |
|---|---|
| Já gerada | "as mensalidades deste trabalhador já estão geradas para o período" |
| Inativo / sem valor | "este trabalhador não tem mensalidade ativa… nenhuma cobrança foi gerada" |
| Período inválido | "Para lançar uma mensalidade é preciso um período claro (AAAA-MM). Nada foi gravado." |
| Já registrada | "Este trabalhador já tem mensalidade nesse período. Nada foi gravado." |
| Conflito histórico | faixa âmbar "Conflito histórico nos meses … — nada foi apagado nem fundido; confira com o responsável antes de receber" |

Datas separadas na consulta: competência (mês), vencimento (`data_movimentacao`)
e recebimento (`data_pagamento`) são colunas distintas na tela, cada uma com seu
cabeçalho; linha legada sem vencimento legível não vira valor inventado.

### 3.7 Evidências

- `docs/tasks/evidencias/TASK_012/diagnostico_dev_mensalidades.txt` — estado do
  DEV **antes** da constraint (leitura pura): 520 mensalidades, 0 duplicidades,
  0 divergências entre vencimento e o período escrito.
- `docs/tasks/evidencias/TASK_012/suite_mensalidades_r1.txt` — falha de harness
  na montagem das fixtures (`usa_produtos` é `boolean`, o INSERT passava `0`).
- `docs/tasks/evidencias/TASK_012/suite_mensalidades_r2.txt` — corrida que
  **pendurou**: a suíte chamava `criar_tabelas()` dentro de uma transação aberta
  sobre `financeiro_movimentacoes` (deadlock com o próprio backfill).
- `docs/tasks/evidencias/TASK_012/suite_mensalidades_r3.txt` — com saída
  desbufferizada, localiza o pendure e mostra 2 falhas reais de asserção do
  próprio teste (reenvio medido sobre item já pago; aritmética da janela
  `MESES_A_FRENTE - 1` quando o correto é `MESES_A_FRENTE`). Nenhuma assertiva
  foi reduzida: as duas medições foram corrigidas para provar o contrato, e o
  número esperado passou a coincidir com a janela real de 13 competências.
- `docs/tasks/evidencias/TASK_012/suite_mensalidades_r4.txt` — suíte da etapa
  **62/62** verificações aprovadas, rc=0, `DEV inalterado: ok`. Cobre: chave do
  banco (aceita outro trabalhador no mesmo mês e o mesmo trabalhador em outro
  mês, cancelado também ocupa), geração repetida, geração concorrente (2
  requisições simultâneas por autenticadores distintos), limites de ano/mês e
  normalização de período, elegibilidade (inativo e valor zero), leitura sem
  efeito + geração manual + as cinco mensagens, recebimento individual, lote
  sem baixa parcial, lote atômico com rollback, duplicidade histórica fictícia
  sem perda de fatos, backfill idempotente e lançamento manual.
- `docs/tasks/evidencias/TASK_012/runner_completo_etapa3_r1.txt` — runner
  completo da etapa antes de endurecer a migração: **acha o defeito real**. A
  suíte `test_sessao_eventos_caixa` simula restart no meio (`criar_tabelas()`),
  as fixtures dela tinham duas pendências do MESMO trabalhador no MESMO mês
  (forma espécie + PIX, para medir o mix) e o rotulamento às cegas estourou
  `uq_mensalidade_por_competencia` dentro de `_migrar` → startup do app abortou
  e as 6 suítes seguintes falharam em cascata por servidor fora do ar. Mantido
  como evidência da corrida que encontrou o problema — é também a prova de que
  um conflito histórico no DEV derrubaria o serviço, coisa que a migração não
  pode fazer.
- Correção: diagnóstico sobre a competência efetiva + `NOT EXISTS` no
  rotulamento (grupo disputado fica intacto, índice é pulado com impedimento
  impresso) e as duas pendências da suíte da TASK_008 passaram a competências
  distintas (`tests/test_sessao_eventos_caixa.py:_mes_anterior`) — mudança de
  forma de fixture, nenhuma assertiva reduzida (o que se mede é dinheiro,
  sessão e eventos).
- `docs/tasks/evidencias/TASK_012/suite_mensalidades_r5_trio.txt` — as três
  suítes que se tocam (TASK_008 de caixa, idempotência §2 e mensalidades §3) no
  mesmo ambiente, na ordem do runner: rc=0 nas três, `DEV inalterado: ok`, e o
  startup rotulando 7 linhas deixadas pela suíte anterior sem violar nada.
- `docs/tasks/evidencias/TASK_012/suite_mensalidades_r6_duplicidade.txt` — a
  mesma suíte com o cenário de conflito ampliado (2 linhas rotuladas + 1 sem
  rótulo no mesmo mês): **64/64**, incluindo
  *startup com conflito NÃO aborta* e
  *rotulamento não escolhe vencedora*.
- `docs/tasks/evidencias/TASK_012/runner_completo_etapa3_r2.txt` — runner
  completo depois do endurecimento da migração: **todas as suítes rc=0**, com
  `DEV inalterado: ok`.
- `docs/tasks/evidencias/TASK_012/runner_completo_etapa3_r3.txt` — ver §3.9.

### 3.9 Consolidado do runner (código da etapa fechado)

`docs/tasks/evidencias/TASK_012/runner_completo_etapa3_r3.txt` — corrida completa
no ambiente descartável selado, com o código e as suítes da §3 fechados:

- **18 suítes + o teste negativo de isolamento, todos rc=0**; 661 verificações
  aprovadas (27+12+8+10+19+18+18+24+21+11+33+25+161+41+51+42+138+64) e os 16
  cenários da trava TASK_010.
- Nenhuma suíte pulou; nenhuma assertiva foi reduzida para obter PASS. As
  mudanças em suíte alheia foram (a) forma de fixture da TASK_008 de caixa
  (duas pendências do mesmo trabalhador passaram a competências distintas) e
  (b) as chaves de tentativa do §2 — nem uma nem outra toca as asserções.
- **DEV inalterado**: `pessoas=678 checkins=2918` antes e depois, `DEV
  inalterado: ok`. A coluna `competencia` e o índice único ainda **não** existem
  no DEV — são da etapa §9 (backup + hash + restauração validada + restart),
  como registrado no §0 da ordem.

### 3.8 Limitações e questões registradas

1. **`GET` deixou de gerar** — mudança de comportamento visível: um operador que
   abrisse a tela de um trabalhador nunca gerado agora precisa do botão
   "Gerar mensalidades". É o que a ordem pede (ler não cria cobrança); registrado
   aqui porque altera um hábito de uso.
2. **Mensalidade sem trabalhador fica fora da chave** — 4 linhas assim no DEV.
   Deducuplicá-las exigiria inventar um critério (descrição? conta?) que não é
   negócio autorizado; ficam rotuláveis e visíveis, não protegidas.
3. **Cancelado ocupa a chave** — é a regra existente; se o dono quiser que mês
   cancelado seja recriável, a mudança é no predicado **e** em `_existe_mes`,
   juntos (QUESTÃO para decisão do dono, não aplicada).
4. **`valor` ainda em `float`** em `sincronizar_mensalidades`
   (`rotas/financeiro_mensalidades.py:109`) e em `POST /financeiro/nova`
   (`rotas/financeiro.py:564`) — conversão para `Decimal` é a da etapa §5,
   registrada ali, não feita silenciosamente aqui.
5. **Geração não usa chave de tentativa** — `POST /mensalidades/gerar` é
   idempotente pela chave de domínio (trabalhador, competência): dois cliques
   criam zero segunda cobrança, que é o que o contrato pede. Não movimenta
   dinheiro, por isso não entrou na lista de entradas do §2.
6. **Conflito histórico no DEV: nenhum hoje** — se aparecer linha duplicada em
   outro banco (cenário fictício provado em *duplicidade histórica: o índice
   único NÃO é criado*), o startup informa o impedimento, não cria o índice e não
   altera linha alguma; a tela mostra o conflito para resolução humana.
7. **O impedimento é registro de log + faixa na tela, não tabela** — não existe
   fila de "migrações pendentes"; quem vê o impedimento é quem abre a tela de
   mensalidades do trabalhador (e o log do startup). Se o dono quiser
   acompanhamento persistido, isso é trabalho novo, não desta etapa.

## 4. Fiados, pagamentos parciais e crédito rastreável — entregue

### 4.1 O "antes", lido em `b9268ec`

Nenhum serviço de fiado existia: a lógica inteira cabia em `pagar_divida_fiado`
(`rotas/caixa.py:1624` hoje; em `b9268ec` era um bloco de ~100 linhas na rota).
O que a leitura da revisão anterior mostra, com o exemplo que a ordem pede
(dívida de 40, entrega de 15):

1. as dívidas em aberto eram lidas `ORDER BY data_venda ASC, id ASC FOR UPDATE`
   e somadas em `round(float(total) - float(fiado_credito_usado), 2)`;
2. entregue 15 < devido 40, o laço FIFO percorria as compras: a primeira não
   era coberta integralmente, então **`break`** — nenhuma quitação;
3. os 15 que sobraram (`restante >= 0.01`) iam para
   `UPDATE trabalhadores SET fiado_credito = fiado_credito + 15` com
   `fiado_credito_mov … 'Saldo de pagamento parcial de fiado'` — **sem
   referência a lançamento nem a venda**;
4. um lançamento de 15 era registrado com a descrição
   `Recebimento fiado — {nome}` — sem dizer para qual dívida;
5. a dívida de 40 continuava `fiado_pago = FALSE`, devendo 40.

Resultado no fim daquele fluxo: 15 na gaveta, 40 de saldo devedor, 15 de
crédito — três números que não se reconciliam, e nada no banco que diga que o
dinheiro tinha destino. O `fiado_alocacao` de hoje é exatamente o elo que
faltava. Na quitação individual (`POST /caixa/fiados/{venda}/pagar`), a ordem
era invertida em relação ao dinheiro: `UPDATE … SET fiado_pago = TRUE` vinha
**antes** de `contabilizar_recebimento`, e o valor cobrado era o de face
(`total - fiado_credito_usado`), ignorando o que outros recebimentos já tinham
tratado daquela dívida; o destino só era deduzível do texto
`Recebimento fiado — venda #{id}`.

### 4.2 O vínculo durável (esquema)

`fiado_alocacao` (`banco.py:1149–1191`) é criada dentro de `criar_tabelas()`,
depois de `vendas_pdv` e `financeiro_movimentacoes` (as FKs exigem a ordem):

| Coluna | Papel |
|---|---|
| `financeiro_id` | o lançamento do dinheiro recebido — `REFERENCES … ON DELETE RESTRICT` (não se apaga dinheiro que tem destino) |
| `trabalhador_id` | dono da dívida — `RESTRICT` |
| `venda_id` | a dívida abatida; `NULL` quando a linha é o excedente |
| `tipo` | `'divida'` ou `'credito'` |
| `valor` | `NUMERIC(10,2) CHECK (valor > 0)` — centavos, nunca `float` |

Três travas de forma, além das FKs: `CHECK fiado_alocacao_vinculo` exige
`tipo='divida' ⟺ venda_id NOT NULL` e `tipo='credito' ⟺ venda_id IS NULL` (o
banco recusa a linha ambígua); `uq_fiado_alocacao_divida` único em
(`financeiro_id`,`venda_id`) para `tipo='divida'` (um recebimento aloca no
máximo uma vez em cada dívida); `uq_fiado_alocacao_credito` único em
(`financeiro_id`) para `tipo='credito'` (**o excedente de um recebimento é um
só** — é a constraint que a prova da última escrita falhando usa como
obstáculo). Índices por venda e por trabalhador para as consultas do extrato.

Migrações idempotentes em `_migrar()`:

- `fiado_credito_mov.venda_id` e `fiado_credito_mov.financeiro_id`
  (`banco.py:647–653`, `para_adicionar`) — o movimento de crédito passa a apontar
  o fato que o gerou. Linhas antigas ficam `NULL`: **sem vínculo comprovado**,
  nunca preenchidas por suposição.
- `CHECK trabalhadores_fiado_credito_nao_negativo` (`banco.py:527–560`, aplicada
  em `:695`) — o consumo de crédito é limitado pelo saldo, então um negativo só
  pode ser bug de concorrência. Havendo saldo negativo no banco, o startup
  imprime o impedimento, **não cria a guarda e não altera linha alguma** (mesma
  política da §3: diagnosticar sem escolher vencedora).

### 4.3 O serviço (`servico_fiado.py`, novo)

Todo o destino do dinheiro sai daqui, em `Decimal` (`moeda()` quantiza 2 casas
com `ROUND_HALF_UP` e recusa `float`):

- `dividas_em_aberto(conn, tid, *, travar, venda_ids)` — cada dívida com
  `total` (face), `credito_usado`, `alocado` (a soma de `fiado_alocacao`),
  `saldo = max(0, face − credito_usado − alocado)` e `alocacoes_registradas`.
  Com `travar=True` percorre **sempre por id crescente** (`FOR UPDATE OF v`)
  para que duas requisições tomem a mesma ordem de locks; a ordem de exibição é
  aplicada depois. Com `venda_ids`, a seleção do operador **restringe e ordena**
  a distribuição — nada de prioridade inventada; sem seleção, vale a regra que o
  sistema já tinha e que continua a mesma: a compra mais antiga primeiro.
- `repartir(dividas, valor)` — a conta que fecha: percorre as dívidas na ordem
  recebida, abate saldo por saldo, devolve `(alocacoes, excedente)` com os
  invariantes `sum(alocacoes) + excedente == valor` (nenhum centavo perdido) e
  `0 < alocação ≤ saldo da dívida`.
- `aplicar_recebimento(...)` — a ordem das escritas é **dinheiro → destino →
  quitação → excedente**: um `recebimento_fiado` no financeiro (com sessão ou
  conta PIX, pelo `contabilizar_recebimento` da sessão), uma `fiado_alocacao`
  por dívida abatida, `fiado_pago = TRUE` **só** nas dívidas cujo saldo zerou
  (`WHERE id = ANY(…) AND fiado_pago = FALSE`), e o excedente — se houver — com
  `SELECT … FOR UPDATE` no trabalhador antes de somar `fiado_credito`, mais
  `fiado_credito_mov` com `financeiro_id` e uma linha `('credito', NULL)` em
  `fiado_alocacao`. Se `repartir` não encontra saldo algum nas dívidas escolhidas,
  recusa `sem_saldo` antes de qualquer escrita: não é receita "contra nada".
- `extrato_fiado(conn, tid)` — §4.6.

### 4.4 As telas, e a mudança prospectiva de comportamento

`listar_fiados` (`rotas/caixa.py:1437`) deixa de somar `float` e passa a montar,
por dívida, `alocado`/`saldo` a partir de `fiado_alocacao`; a lista de cada
trabalhador sai da mais antiga para a mais recente — **a ordem que o serviço usa
quando não há seleção é a ordem que a tela mostra**. Em
`templates/caixa/fiados.html`:

- cada dívida vira um `<input type="checkbox" name="venda_ids" … checked>` com
  data, **saldo**, e quando for o caso o detalhe "(face R$ …, crédito usado
  R$ …)" e "(já recebeu R$ …)";
- o painel âmbar **"Distribuição deste dinheiro:"** é recalculado a cada
  edição de valor/marcação (`atualizarReparticao`) e diz linha por linha
  `quita`, `abate parcialmente … fica devendo`, `não recebe nada`, além do
  rodapé "Sobra de R$ … vira crédito antecipado (o dinheiro entra uma única
  vez)" ou "Ainda restam R$ … em saldo devedor". É uma **ajuda de tela**: a
  autoridade continua sendo `repartir` no servidor, que decide sob lock e é o que
  as asserções medem;
- o diálogo de confirmação do pagamento parcial foi reescrito com a regra nova
  ("O valor abate as dívidas marcadas, na ordem da tela; a última fica com
  saldo. Só vira crédito o que sobrar depois de cobrir todas as marcadas"), o
  botão passou a se chamar "💰 Quitar o saldo (R$ …)" e a coluna "A pagar" virou
  **Valor original / Crédito usado / Recebido / Saldo a pagar**;
- a faixa de sucesso agora repete o que aconteceu em dinheiro — "Recebido R$ … —
  R$ … abatido (N dívida(s) quitada(s)) e R$ … registrado como crédito
  antecipado" — com link para o extrato;
- mensagens novas de recusa: `selecao_invalida` (alguma dívida marcada não está
  mais em aberto — nada foi registrado) e `sem_saldo` (as marcadas já foram
  cobertas; para antecipar dinheiro sem dívida use "➕ Crédito").

**A mudança é prospectiva, e é isto que a ordem pede para ser explicado.** Antes,
entregar menos que a dívida não a abatia: o dinheiro virava crédito solto e a
dívida continuava de face. Agora, o pagamento destinado a uma dívida **abate
aquela dívida** e o saldo restante fica visível; `fiado_pago` não vira `TRUE`
enquanto existir saldo devedor. Nenhum fato anterior foi reescrito: as linhas de
`fiado_credito_mov` e de `financeiro_movimentacoes` existentes ficam como
estão, sem vínculo (ver §4.10), e a suíte da §4 prova o legado intacto.

`POST /caixa/fiados/{venda}/pagar` (`rotas/caixa.py:1514`) passa a cobrar o
**saldo** (`dividas_em_aberto(travar=True, venda_ids=[venda])`), chama
`aplicar_recebimento` — quitação e alocação na mesma transação, dinheiro antes
do flag — e trata o caso em que não há dinheiro novo: dívida já coberta por
crédito consumido no PDV (`saldo == 0`) só encerra o débito, sem lançamento
inventado. O `idempotencia` da §2 permanece (`identidade=f"fiadopagar:{venda_id}"`
no evento, chave de tentativa no corpo) e o `corpo_tentativa` de
`pagar_divida_fiado` passou a incluir `dividas`: a mesma chave com seleção
diferente é recusada como divergência, não como novo pagamento.

### 4.5 Crédito antecipado: origem, consumo, nunca negativo

- **Adiantamento sem dívida** (`POST /caixa/fiados/{tid}/credito`,
  `rotas/caixa.py:1798`) tem origem financeira explícita: o lançamento
  `credito_anticipado` existia desde a TASK_008; agora a linha de
  `fiado_credito_mov` recebe `financeiro_id` (mesmo lançamento, **sem segunda
  entrada de dinheiro**) e uma `fiado_alocacao` `('credito', NULL)`, e o
  trabalhador é travado `FOR UPDATE` **antes** de somar o saldo — duas entregas
  concorrentes não gravam mais crédito do que o dinheiro recebido.
- **Consumo no PDV** (`finalizar_venda`, `rotas/caixa.py:240–370`): o `débito`
  de `fiado_credito_mov` agora carrega `venda_id` da compra que o gerou (antes
  era só o texto `Compra #{id}`), e o `pendente` que alimenta o limite do
  trabalhador passou a descontar o que as alocações já receberam
  (`GREATEST(total − fiado_credito_usado − alocado, 0)`), com o mesmo `GREATEST`
  que impede dívida abatida em parte de contar como se estivesse inteira. O
  consumo continua `min(crédito, total)`, e a guarda do banco (§4.2) recusa o
  negativo. Evento `venda_credito` com `efeito_fisico = 0` — crédito consumido
  não é dinheiro na gaveta.
- **Excedente de um recebimento** entra como crédito pelo mesmo caminho, na
  mesma transação do lançamento que o originou (§4.3).

### 4.6 Extrato por trabalhador (dívida e crédito separados)

`GET /caixa/fiados/extrato/{trabalhador_id}` (`rotas/caixa.py:1928`) +
`templates/caixa/fiado_extrato.html` (novo), servido por
`servico_fiado.extrato_fiado`:

- **saldo devedor** e **crédito** em blocos separados, com limite — um não
  aparece como se abatesse o outro;
- cada dívida: valor original, crédito usado, recebido, saldo, e a lista dos
  lançamentos que a abateram (com link `/financeiro/lancamento/{id}`);
- cada recebimento: o dinheiro que entrou, forma, data e **para onde foi**
  (dívidas + excedente), lido de `fiado_alocacao` — não do texto;
- cada movimento de crédito: com vínculo à venda consumida ou ao lançamento que
  o originou; movimento sem vínculo continua no histórico, marcado como legado
  sem alocação comprovada;
- faixa final com `legado_sem_prova` (quantidade e valor), quando houver.

### 4.7 Cancelamento: bloqueios da TASK_007 preservados, avaliação adaptada

A ordem pede "preservar bloqueios seguros do cancelamento TASK_007… adaptar a
avaliação à existência das novas alocações". `servico_cancelamento.py:143–156`
passa a perguntar ao vínculo primeiro: se existe `fiado_alocacao` `tipo='divida'`
para a venda, o motivo é **afirmativo e com valor** — "Esta dívida já recebeu
pagamento (R$ X alocado em lançamento vinculado a ela) — estorno/recebimento é
outro fluxo". Não havendo alocação (linhas anteriores a esta entrega), as duas
heurísticas antigas continuam valendo byte a byte: a descrição exata
`Recebimento fiado — venda #{id}` e o movimento de crédito posterior à venda com
margem conservadora de 1 s. Todos os demais bloqueios estão intactos: vínculo de
sessão obrigatório, `fiado_pago` recusado, venda sem trabalhador, crédito usado
na compra, `criada_em` ausente. Teste: `prova_cancelamento_respeita_alocacao`
(§4.9) e o cenário **M8b** da suíte da TASK_007.

Sobre o M8b: a assertiva passou de "movimento de crédito / comprovável" para
"já recebeu pagamento … alocado". Isso **não** é redução de assertiva para obter
PASS — é a medição do mesmo contrato por um caminho mais forte. Antes, o bloqueio
dependia de deduzir pelo texto que existia um recebimento; agora o teste exige
que o vínculo gravado seja a causa da recusa, e o `_sem_efeitos` (nada mudou no
banco) continua idêntico. O outro lado da mudança é visível na asserção anterior,
que passou a afirmar `fiado_pago == False` pelo motivo certo: a venda está
**abatida em parte**, não "intacta porque o flag engana".

### 4.8 Concorrência determinística e rollback da última escrita

Ordem de locks, em todos os caminhos que tocam fiado: **dívidas** (`ORDER BY id`
com `FOR UPDATE OF v`) → **sessão** → **trabalhador**. Duas requisições
concorrentes caminham na mesma ordem, então não deadlockam; os saldos lidos sob
lock são a base da distribuição, então o mesmo centavo nunca é alocado duas vezes.

As duas provas de corrida não usam `sleep` às cegas: a transação concorrente é
aberta numa conexão crua do pool (`_obter_pool().getconn()` + `_ConnCompat`) e o
harness **aguarda `pg_stat_activity`** mostrar a outra sessão em
`wait_event_type='Lock' AND state='active'` sobre a tabela esperada, só então
libera — a espera é observada, não presumida.

- **pagamento × pagamento** (40 de dívida, dois recebimentos de 30): o segundo
  acorda com 10 de saldo e aloca 10 + 20 de excedente. Conferido: 2 lançamentos
  somando 60 de dinheiro, 40 alocados às dívidas, `fiado_pago = TRUE`, crédito
  20, sessão 60, e as alocações de cada lançamento exatamente
  `{divida: 30}` e `{divida: 10, credito: 20}`.
- **consumo × consumo** (crédito 50, duas vendas de fiado): a segunda compra
  bloqueia em `trabalhadores` até a primeira confirmar; no fim foram consumidos
  40 + 10 de 50, **crédito nunca negativo**, cada `fiado_credito_mov` de débito
  vinculado à sua `venda_id`, zero lançamentos financeiros novos e todos os
  eventos `venda_credito` com `efeito_fisico = 0`.
- **última escrita falhando**: dentro da mesma transação, reserva da chave →
  `aplicar_recebimento(50)` para dívida de 40 → uma segunda linha
  `('credito', NULL)` que viola `uq_fiado_alocacao_credito`. A exceção faz
  `conectar()` devolver o rollback: **zero** lançamento, **zero** evento,
  **zero** alocação, crédito 0, dívida intacta em 40, chave de tentativa **não
  consumida** (reenvio possível) — e um recebimento correto depois registra o
  dinheiro uma única vez.

### 4.9 Evidências

`tests/test_fiado_alocacoes_task012.py` (novo, 13 provas, **91 asserções**). Cada
prova confronta **cinco números por caminhos SQL independentes** — dinheiro
(`SUM` de `financeiro_movimentacoes` da descrição), lançamentos, efeito na
sessão (`SUM(efeito_fisico)` via `caixa_sessao_eventos.financeiro_id`), dívida
(`SUM(GREATEST(face − credito_usado − alocado, 0))`) e crédito (saldo + razão) —
então "bater" não é a mesma consulta repetida.

- `docs/tasks/evidencias/TASK_012/suite_fiado_alocacoes_r1_repetibilidade.txt` —
  91/91, rc=0, `DEV inalterado: ok`.
- `docs/tasks/evidencias/TASK_012/suite_fiado_alocacoes_r2_repetibilidade.txt` —
  segunda corrida isolada, **91/91 outra vez** (a suíte é repetível: fixtures com
  prefixo próprio e limpeza em ordem de dependência).
- `docs/tasks/evidencias/TASK_012/runner_completo_etapa4_r1.txt` — runner
  completo consolidado: **19 suítes + o teste negativo de isolamento, todas
  rc=0**, 752 verificações (27+12+8+10+19+18+18+24+21+11+33+25+161+41+51+42+138
  +64+91), `DEV inalterado: ok` (`pessoas=678 checkins=2918` antes e depois).
  As suítes alheias tocou só em limpeza (`test_pdv_precos_servidor.py`,
  `test_cancelamento_vendas.py`: apagar `fiado_alocacao`/`fiado_credito_mov`
  antes dos pais, porque as FKs são `RESTRICT`) e no M8b descrito em §4.7.

Cobertura, prova por prova: contrato no banco (tabela, `CHECK` de vínculo, os
dois únicos, as FKs `RESTRICT`, recusa real de linha ambígua e de `valor ≤ 0`),
dívida 40 com 15 + 25, pagamento 50 contra dívida 40 com crédito 10, duas dívidas
selecionadas (ordem respeitada, inexistente recusada, não marcadas intocadas),
adiantamento e consumo parcial, extrato separando dívida e crédito, reenvio pela
mesma chave, pagamento × pagamento, consumo × consumo, última escrita falhando,
legado sem backfill, cancelamento respeitando alocação, e o fecho em que as contas
batem para todos os trabalhadores da suíte (inclusive nenhum crédito negativo,
nenhuma alocação órfã de lançamento, todo consumo apontando a venda).

### 4.10 Limitações e questões registradas

1. **Legado sem alocação comprovada** — `recebimento_fiado` anteriores a esta
   entrega não têm como saber para onde foram; continuam no histórico, a dívida
   segue devendo o valor de face, e **nada foi distribuído retroativamente** por
   nome, valor aproximado, data ou FIFO presumido (prova: *legado sem backfill*
   + `prova_contrato_no_banco` sobre a linha crua). No DEV há **43** lançamentos
   de `recebimento_fiado` sem `trabalhador_id` — por isso a faixa do extrato é
   necessariamente um total do banco, não do trabalhador: as linhas não têm a
   quem pertencer. (QUESTÃO para o dono: quer uma tela de destino histórico para
   que alguém aponte esses 43 manualmente? Isso é trabalho novo, fora desta ordem.)
2. **As consultas do financeiro ainda não enxergam as alocações** — a lista de
   fiado (`rotas/financeiro.py:329–348`, `SUM(v.total)` com `NOT v.fiado_pago`) e
   `relatorio_fiados` (`:1242–1295`, `float` + `total_pendente` sem `alocado`)
   ainda apresentam dívida de face para uma compra já abatida em parte. É a
   etapa §5 (fórmulas de saldo divergentes por tela); as telas de caixa estão
   corretas desde já e é isso que a §4 prova.
3. **Não existe fluxo para estornar um recebimento alocado** — o cancelamento
   recusa com o motivo novo (§4.7) e nada mais faz; reembolso de fiado já pago,
   estorno após sessão fechada e reabertura de caixa continuam excluídos (§7 da
   ordem), sem saída financeira inventada.
4. **A prévia de distribuição é do navegador** — quem decide é `repartir` no
   servidor, sob lock; a tela pode mostrar uma repartição e o servidor registrar
   outra se uma dívida concorrente mudar entre as duas leituras, e nesse caso a
   recusa é `selecao_invalida` (nada gravado), nunca uma distribuição silenciosa.
5. **A seleção entra no corpo da tentativa** — reenviar a mesma chave com
   dívidas marcadas diferentes é recusado como divergência (§4.4). É proteção,
   mas significa que o operador precisa recarregar a tela depois de mudar a
   seleção de um envio já confirmado.
6. **Um recebimento, um trabalhador** — a entrega de dinheiro é por trabalhador
   pela própria tela; não há fluxo que distribua um lançamento entre dívidas de
   dois trabalhadores (teria de ser duas entregas, cada uma com seu lançamento).
7. **Nada no DEV ainda** — a tabela `fiado_alocacao`, as duas colunas de vínculo
   e a guarda de crédito negativo só existem no código e no banco descartável;
   aplicar ao DEV é a etapa §9 (backup + hash + restauração validada + baseline
   antes/depois + restart da 9000), conforme §0 da ordem.

## 5. Consultas, saldos e experiência integrada — entregue

### 5.1 A regra passou a ser uma só, no serviço

A §4 escreveu o destino do dinheiro em `fiado_alocacao`; as consultas, porém,
continuavam lendo `SUM(v.total)` com `NOT v.fiado_pago` (`rotas/financeiro.py`
em `a6f0328`) e `float` no relatório — duas telas, duas contas, e nenhuma delas
via o parcial. A §5 fecha isso com **funções de consulta no próprio
`servico_fiado`**, as mesmas que a escrita usa:

| Função | Para que serve | Quem usa |
|---|---|---|
| `alocado_por_venda(conn, ids)` | recebido por dívida, somado das alocações | `dividas_em_aberto`, telas de caixa, extrato |
| `linha_de_saldo(venda, alocado, tem_alocacao=…)` | valor original, crédito usado, recebido, saldo e `alocacoes_registradas` | todas as telas |
| `fiados_em_aberto_para_consulta(conn, nome_like, limite)` | lista + TOTAIS do recorte | Visão Geral do Financeiro |
| `saldo_por_trabalhador(conn)` | saldo por trabalhador | relatório de fiados, filtro da tela |
| `historico_fiado(conn, trabalhador_id)` | histórico com quitadas/canceladas | relatório por trabalhador |

O saldo, em toda tela, é `max(0, total − fiado_credito_usado − alocado)`. Não
existe segunda fórmula no módulo: `rotas/caixa.py` (lista de fiados e
autocomplete do PDV) e `rotas/financeiro.py` (Visão Geral e relatório por
trabalhador) chamam essas funções. Dívida sem alocação continua devendo o valor
de face e vem marcada (`sem_alocacao`), nunca corrigida por estimativa.

### 5.2 Totais do recorte não são totais da página

`fiados_em_aberto_para_consulta` corta a LISTA em 50 (`limite`) mas calcula os
totais sobre **todas** as dívidas do recorte; a nota da tela passa a dizer
quantas dívidas existem, quanto valem de origem, quanto já foi recebido e qual é
o saldo — e avisa quando a tabela é uma página. Antes, a nota dizia "Mostrando os
50 mais recentes de N (R$ X no total)", onde X era a soma das 50 visíveis e N o
total de dívidas: dois números de coisas diferentes na mesma frase.

### 5.3 O que as telas mostram agora

| Tela | Antes | Agora |
|---|---|---|
| Visão Geral → Fiado em aberto | uma coluna "Valor" (= valor de face) | **Valor original · Recebido · Saldo**, com nota do recorte e aviso de dívida histórica sem alocação; versão *mobile* em `<details>` com as mesmas linhas |
| Visão Geral → cartões por conta | só entradas/saídas do período | acrescenta **Σ acumulado até a data final**, rotulado como "todos os lançamentos pagos até X — não é saldo conferido com o banco" |
| Relatórios | "Total Entradas" somando pago+pendente+cancelado e um "Saldo" que ignorava transferências | cinco cartões separados: **Recebido (pagos)**, **Pago (saídas pagas)**, **A receber (pendentes)**, **A pagar (pendentes)**, **Cancelados (não somam)**, mais a contagem de transferência/sangria do recorte |
| Relatório de mensalidades | "Mês" sem dizer de qual data; três cartões com `replace('.', ',')` | seletor **Qual data o mês usa** (competência / vencimento / pagamento), rótulo do que o filtro pergunta, competência histórica marcada como tal, "Vencidas até hoje (data)" explicando que é sempre vencimento |
| Fiados por trabalhador | `SUM` com `float`, "Pago/Pendente" binário | **Comprado a fiado · Já recebido · Saldo devedor** (e crédito à parte), cada dívida com valor original, crédito usado, recebido, saldo e situação "Recebida em parte" |
| Detalhe do lançamento | só a linha | acrescenta competência e **"Para onde foi este dinheiro"**, lendo `fiado_alocacao` do próprio lançamento |
| Ficha do trabalhador / da pessoa | tabela de 6 colunas, `replace` de ponto | inclui **Competência**, **Recebido em**, `moeda` (ponto de milhar), versão *mobile*, e diz que a data é do vencimento |

Datas: competência, vencimento e pagamento são campos distintos e cada filtro
declara qual usa; a competência é lida da coluna própria (nunca do texto da
descrição) e o que veio do histórico entra marcado como "histórica: sem
competência registrada". Valores em `Decimal`/`NUMERIC` com arredondamento
comercial (`_dec`, `ROUND_HALF_UP`) em todos os pontos tocados; o filtro `moeda`
do template é a única formatação de dinheiro nas telas do financeiro.

### 5.4 GET não escreve, e filtro inválido é recusado

- O `GET /financeiro/mensalidades` que gerava cobrança foi eliminado na §3
  (`POST /financeiro/mensalidades/gerar`); a §5 prova por retrato de tabela que
  **dez telas** abertas duas vezes cada não criam lançamento, alocação, evento,
  venda, crédito nem cobrança.
- Filtros passaram a ser validados no servidor com mensagem de operador, sem
  esvaziar a tela: data fora de `AAAA-MM-DD`, conta não numérica/inexistente,
  `tipo` fora de entrada/saída, categoria desconhecida, `por` fora de 25/50/100,
  natureza/situação/sentido fora da lista, mês fora de `AAAA-MM`. Antes, qualquer
  um desses virava "nenhum resultado" — o operador lia ausência de dado onde
  havia erro de digitação.
- Valor de entrada de dinheiro: `POST /financeiro/nova` e
  `POST /financeiro/transferir` usam o mesmo parser (`servico_sessao.moeda`),
  com duas casas e teto do `NUMERIC`; `"1.234,56"`, `"abc"`, `"0"` e `"-10"` são
  recusados com `erro=valor_invalido` e nenhuma escrita; `"60,005"` fecha em
  `60,01`. O PIX (`GET /financeiro/pix/gerar`) recusa valor ilegível com
  mensagem em vez de derrubar a requisição com 500.

### 5.5 Provas

`tests/test_financeiro_consultas_task012.py` (nova, 74 asserções), executada pelo
runner oficial sobre banco descartável selado:

| Prova | O que mede |
|---|---|
| `p1` saldo parcial em todas as telas | dívida 40 com entrega 15 → "R$ 15,00 recebido / R$ 25,00 saldo" na Visão Geral, no relatório, no extrato (`Decimal`, não texto) e no detalhe do lançamento (`Dívida #N`) |
| `p2` totais do recorte | 52 dívidas → nota com 52 e R$ 520,00; tabela com exatamente 50 linhas |
| `p3` legado sem prova | dívida sem alocação exibe o valor de face, marcada, e o serviço não inventa abate |
| `p4` venda fiada não é receita | PDV a fiado não cria lançamento nem move "Recebimentos registrados"; a entrega do dinheiro é que move |
| `p5` obrigação × dinheiro | "Recebido" = só pago (100, não 150 nem 157); pendente e cancelado em cartões próprios; transferência contada à parte |
| `p6` filtros inválidos | cada recusa tem a própria mensagem e a tela mantém os dados |
| `p7` três datas | competência, vencimento e pagamento devolvem listas diferentes e rotuladas; "Vencidas até hoje" é sempre vencimento |
| `p8` política de valor | quatro valores recusados sem escrita, `60,005 → 60,01`, `R$ 1.234.567,89` exibido com ponto de milhar, Σ do acumulado declarado |
| `p9` permissões | Caixa não entra no Financeiro (nem por POST direto); leitura não vê o botão e não consegue postar |
| `p10` GET não movimenta | retrato de seis tabelas antes/depois de 20 aberturas de tela |
| `p11` TASK_011 preservada | `base.css?v=`, classes `fin-*`, retorno ao recorte consultado, link de volta forâneo descartado |
| `p12` serviço é a conta única | serviço, tela e extrato concordam; crédito antecipado fica em número separado |

### 5.6 A limpeza da suíte — o ponto em que a sessão anterior parou

A retomada começou exatamente aqui. A suíte tinha sido escrita mas a limpeza
estava **incompleta e perigosa**, com três defeitos distintos:

1. `DELETE FROM operacao_idempotencia WHERE nota_fiscal_foto IS NOT NULL` →
   o filtro estava na tabela errada e o efeito era **apagar a tabela inteira**,
   incluindo as chaves que outras suítes do mesmo runner estavam usando;
2. `financeiro_movimentacoes.caixa_id` — coluna que não existe (herança SQLite):
   o `DELETE` levantava erro e a limpeza parava no meio, deixando fixtures;
3. `financeiro_tentativa_documento` — tabela que nunca existiu neste esquema;
   o `try/except` silencioso escondia o problema.

Além disso a limpeza apagava "por tabela" (`caixa_sangrias` por sessão,
`estoque_movimentos` por produto) sem verificar as colunas existentes.

A limpeza agora: (a) só toca linhas que esta execução criou — marca `PREFIXO`,
ids das fixtures e as chaves de tentativa registradas em `CHAVES_USADAS` quando
`chave()` as gera; (b) consulta `information_schema` antes de citar uma coluna;
(c) informa o que removeu; (d) mede um **piso** de linhas por tabela antes de
qualquer escrita e reprova se a limpeza descer abaixo dele; (e) usa a sentinela
`tipos_doacao` (catálogo que esta suíte nunca apaga) para distinguir "eu apaguei
demais" de "outro processo recriou o banco no meio da corrida".

Prova: com dados sintéticos de outra execução plantados no **mesmo** banco
descartável (trabalhador, venda, lançamento, alocação, chave de tentativa,
evento de sessão e uma marca de catálogo), a suíte passa **74/74**, a limpeza
remove exatamente as 58 vendas, 8 trabalhadores, 7 chaves e 4 alocações que
criou, e o piso não desce. Nas corridas em que outro processo recriou o banco no
meio, a sentinela caiu e a suíte **reprovou com mensagem própria** em vez de
declarar limpeza verificada — que é o comportamento desejado.

**Uma segunda armadilha, achada pela própria conferência.** A limpeza também
usava `operacao_idempotencia.documento` como pista ("apago a chave cujo
documento é uma fixture minha"). Os ids do banco descartável são **reciclados a
cada execução** (o runner o recria), então uma chave de OUTRA suíte podia
apontar para o "documento 7" — o mesmo número de uma fixture nossa. Numa corrida
isso apagou 4 chaves alheias (11 → 7) e a conferência do universo alheio
reprovou, com número. A pista foi **removida**: a limpeza apaga só o que está em
`CHAVES_USADAS`, e a corrida seguinte fechou em 11 → 11. É o retrato do que a
ordem pede: a prova tem de ser capaz de reprovar.

### 5.7 Limitações e questões registradas

1. **Não há conciliação bancária**: o Σ acumulado por conta é a soma dos
   lançamentos pagos até a data, dito assim na tela. Saldo de extrato real
   continua fora de escopo (§7 da ordem).
2. **Conta de "areceber"** entra nos cartões por conta como qualquer outra; ela
   representa obrigação, não dinheiro — por isso a legenda do cartão diz isso e
   o resultado do período não a soma (ela é pendente).
3. **Relatório de fiados por trabalhador** deriva o saldo do serviço, mas não
   pagina: um trabalhador com centenas de vendas traz o histórico inteiro. Não é
   problema hoje (o maior tem dezenas) e paginar sem pedido mudaria o recorte.
4. **A prévia de distribuição do pagamento continua sendo do navegador** (limite
   já registrado na §4.10): a tela pode mostrar uma repartição e o servidor
   recusar com `selecao_invalida` se uma dívida mudar entre as duas leituras.
5. **A limpeza da suíte §5 depende de a suíte ter criado tudo pelas fixtures**:
   registros criados por caminhos novos que não usem `PREFIXO` nem `chave()`
   ficariam órfãos no banco descartável. É banco descartável (o runner o recria a
   cada execução), mas o contrato está escrito no docstring para a próxima
   alteração.

## 6. Integridade técnica complementar (FIN-08/09) — FIN-09 corrigido e sob revisão, FIN-08 parcial

### 6.1 FIN-09 — a nota da compra saía do ciclo do arquivo e da transação

**O defeito, lido no código** (`rotas/compras.py` em `b9268ec` e até `37f3dc4`):
a rota gravava a foto **dentre** do `with conectar()`, logo depois do `INSERT` e
antes do commit:

```python
mov_id = cur.fetchone()["id"]
nome_arquivo = _salvar_foto_nota(conteudo, mov_id, ext)   # ← disco, dentro da transação
conn.execute("UPDATE ... SET nota_fiscal_foto = %s ...", (nome_arquivo, mov_id))
confirmar(...)
```

Três desfechos ruins, todos silenciosos:

1. commit falha depois da gravação → **arquivo órfão** em `static/fotos/`, sem
   linha apontando para ele;
2. gravação falha (disco cheio, permissão) → **500 com a transação desfeita**,
   mas o nome podia ficar meio escrito no diretório;
3. reenvio com a mesma chave → o corpo da tentativa já continha o hash da nota,
   então não duplicava; mas a tentativa recusada por divergência **não limpava**
   o arquivo que já tinha sido gravado por engano em outra tentativa.

Além disso o nome do arquivo era sorteado **duas vezes** (`_salvar_foto_nota`
gerava outro `uuid`), e a primeira versão desta correção, escrita na retomada,
repetiu o defeito: gravava com um nome e registrava outro. As provas pegaram
isso (`nome=nota_26_6f165944.jpg novos=['nota_26_2e5b025f.jpg']`) e a correção
mudou o desenho, não a asserção.

**O desenho agora** (`rotas/compras.py`):

| Etapa | Quando | O que garante |
|---|---|---|
| `_nome_da_nota(mov_id, ext)` | dentro da transação, **uma vez** | o mesmo nome vai para a linha e para o arquivo |
| `_preparar_nota(conteudo, nome)` | dentro da transação, depois do `UPDATE` | grava em `…/<nome>.tmp-<rand>`, com `flush`+`fsync`: **o disco é testado antes do commit**, e o temporário não aparece como nota |
| commit | ao sair do `with` | ou a linha **e** o nome existem, ou nada existe |
| `_promover_nota(tmp, nome)` | depois do commit | `os.replace` — renomeação **atômica** no mesmo sistema de arquivos |
| rollback (`except` interno) | se o commit não acontecer | `_remover_arquivo(tmp)` remove **só o temporário desta tentativa** |
| falha da promoção | depois do commit | `_descartar_compra(mov_id)`: o lançamento é **cancelado** (nunca apagado — fica auditável) e a tentativa é liberada para reenvio; o temporário é removido |
| `_caminho_da_nota(nome)` | em toda escrita/remoção | `abspath` + prefixo obrigatório de `static/fotos`; nome fora da pasta levanta `ValueError` em vez de apagar |

O helper antigo `_salvar_foto_nota` (que gravava com um nome sorteado) foi
removido: depois da mudança ele seria código morto — e código morto que grava
arquivo é convite ao defeito de volta.

Chaves de tentativa consumidas e nunca reaproveitadas; a tentativa que falhou
por disco libera o reenvio (`_descartar_compra` marca a compra como cancelada e
o operador reenvia com a mesma chave — o corpo é o mesmo, a resposta anterior
aponta para o documento cancelado, e uma operação nova nasce em tela nova).

### 6.2 Como a falha de disco é provada (e por que precisou de injeção)

A rota roda **no processo do servidor**; um `monkeypatch` na suíte não alcança a
requisição HTTP — foi exatamente isso que as primeiras provas mostraram (o
`status 303` chegava com a exceção "injetada" sem nunca ter sido levantada no
servidor). A solução é uma injeção mínima, **guardada**:

- `SHAMBALA_FALHA_DISCO_COMPRA` é lida por `_falha_de_disco_injetada`;
- o **gatilho é o cabeçalho** `X-Tentativa-Falha-Disco: preparar|promover`,
  enviado só pela prova — outras suítes no mesmo app não são afetadas;
- só dispara com `SHAMBALA_MODO_TESTE=1` **e** banco com selo do runner
  (`trava_isolamento._falhas_de_identidade` vazio). Em DEV/produção o código é
  inerte mesmo que a variável esteja definida por engano;
- o runner habilita a variável e sinaliza `SHAMBALA_FALHA_DISCO_HABILITADA=1`;
  a suíte **reprova** se a injeção não estiver habilitada, para não "passar" sem
  exercitar nada.

O caso do rollback **não** usa injeção: um `TRIGGER` de verdade no banco
descartável recusa o `INSERT` (chamando o `confirmar` de "última escrita"
depois), o temporário já está em disco e o teste prova que ele desaparece.

### 6.3 Provas (em `tests/test_financeiro_idempotencia_task012.py`)

| Prova | Desfecho exercitado | Resultado |
|---|---|---|
| `FIN-09 sucesso` | caminho feliz | linha com `nota_fiscal_foto` e **exatamente** esse arquivo no disco |
| `FIN-09 preparo falhando` | disco, antes do commit | `500`; nenhum arquivo (nem temporário); nenhuma linha; chave não consumida com documento |
| `FIN-09 rollback` | commit recusado por trigger | `500`; nenhum arquivo nem temporário; nenhuma linha; chave livre |
| `FIN-09 promoção falhando` | disco, depois do commit | `500`; compra **cancelada** (linha preservada); nenhum arquivo órfão |

A suíte inteira: **145/145**, rc=0, `DEV inalterado: ok`. Os arquivos de nota
que a suíte cria são removidos no fim pelo caminho validado, recortando apenas o
que apareceu na janela da corrida.

### 6.5 O que a primeira versão NÃO provava (revisão do dono, e o que mudou)

A versão anterior deste relatório dizia "resolvido". O dono recusou, com quatro
razões concretas. Todas foram procedentes; o que segue é o que cada uma exigiu:

**1. "Commit e promoção são operações separadas; nada demonstra recuperação se o
processo morrer entre elas."** Verdade — e não existe transação que una banco e
sistema de arquivos. O que dá para fazer honestamente é (a) reproduzir a morte,
(b) provar o estado que ela deixa, e (c) fechar esse estado na subida seguinte.
Hoje existe `rotas.compras.recuperar_compras_sem_nota`, chamada no `lifespan`: a
compra `pago` cuja nota não está em disco é **cancelada**, a tentativa é
**anulada** e o temporário órfão é removido. A prova injeta a morte, confirma que
o estado fica inconsistente (`pago` com arquivo inexistente e `.tmp-` no disco),
roda a recuperação e exige o estado corrigido; e roda de novo para provar que é
idempotente. **Limite que continua existindo:** entre a morte e o próximo startup
o estado fica inconsistente por alguns segundos — não há como evitar sem um
journal em disco, que seria arquitetura nova (§6 da ordem manda parar antes
disso).

**2. "O relatório se contradiz sobre a chave: liberada para reenvio e depois
consumida apontando para documento cancelado."** Verdade, e o código fazia a pior
das duas: o **reenvio respondia `303 ?ok=1` para uma compra cancelada** (medido
contra app real, não deduzido). Agora a tentativa ganhou o estado próprio
`anulada` (terceiro valor da `CHECK`, recriada de forma idempotente): a linha é
preservada com a chave original, sem documento, e o reenvio recebe a recusa
legível `tentativa_anulada`. A chave **não** é reciclada para outra operação —
continua sendo a identidade daquela tentativa — e uma operação nova (tela nova)
passa normalmente. Não há mais as duas frases em conflito: existe uma frase só, e
ela é provada em dois sentidos (reenvio recusado, operação nova aceita).

**3. "Falta provar a falha da compensação que cancela a compra."** Verdade, e a
primeira tentativa de prova estava errada de duas maneiras: a falha era injetada
fora do `try` (o cancelamento era desfeito junto, e a compra voltava a `pago`) e
depois dentro de uma transação única (mesmo efeito). A compensação agora é **duas
transações, de propósito**: o cancelamento do documento confirma sozinho, porque
é o efeito que precisa valer; a anulação da tentativa é a segunda e pode falhar.
Provado: promoção falha + compensação falha → documento **cancelado** e tentativa
**consumida apontando para ele** (rastro preservado, estado coerente), e a
varredura de startup recupera o temporário.

**4. "Definir o efeito sobre estoque e financeiro."** A compra é despesa no
livro-caixa (`saida/compra`): **não** movimenta estoque nem gaveta. Isso agora é
verificado, não afirmado — a prova confere `estoque_movimentos` e
`caixa_sessao_eventos` antes/depois de todos os desfechos de falha, e confere que
as compras que falharam ficam `cancelado` (não entram como despesa paga)
enquanto só a operação boa fica `pago`.

### 6.6 Os limites da recuperação automática (segunda devolução do dono)

A primeira versão da varredura de startup era larga demais: `compra paga sem
arquivo → cancelar` alcançaria compra HISTÓRICA e confundiria "arquivo sumiu"
(volume não montado, disco removível ausente, falha de leitura) com "tentativa
interrompida". Reescrita com cinco limites, cada um com prova própria:

| Limite | Como é garantido | Prova |
|---|---|---|
| 1. só tentativa **nova** e **interrompida** | casa com tentativa `operacao='compra'` **concluída** apontando para a linha, com conclusão dentro de `INTERVALO_RECUPERACAO` (24 h); compra sem tentativa nunca é tocada | *compra com tentativa VELHA não é cancelada* |
| 2. não interfere em compra **em processamento** | tentativa `em_andamento` fica fora; a varredura inteira roda sob `pg_try_advisory_lock` — quem não pega a trava não age | *tentativa em andamento não é recuperada* + *duas recuperações simultâneas cancelam UMA vez* |
| 3. só com **prova independente** de que o arquivo nunca existiu | `os.path.exists() == False` é o mesmo sintoma de diretório não montado; o caso interrompido deixa o **temporário** ao lado do destino (conteúdo escrito antes do commit, promoção é o último passo). Sem temporário, sem recuperação | *arquivo ausente SEM temporário é preservado* + *compra com arquivo presente não é tocada* |
| 4. **um commit** para o que anda junto | cancelamento e anulação no mesmo `with conectar()`: se a anulação falhar, o estado **não muda** e a próxima subida conclui; nunca "cancelado com a chave respondendo sucesso" | *anulação falhando (TRIGGER real): estado coerente e reenvio recusado* + *recuperação posterior conclui* |
| 5. só cancela, **nunca apaga**; o que foi pulado é informado | a linha fica auditável; `puladas` vai para o log de subida | *a varredura informa o que pulou* |

**O que a recuperação NÃO faz** (e agora está explícito): não cancela por arquivo
ausente sozinho, não age em compra histórica, não remove temporário de tentativa
em voo, não apaga linha e não tenta "consertar" nada quando o diretório não pode
ser lido — nesse caso ela não tem prova e não age.

**O recuperador alterou alguma compra no DEV?** Não. Três evidências
independentes:
1. os logs dos três reinícios (`dev-startup-task012.log`, `-r2`, `-r3-final`) não
   têm nenhuma linha de recuperação — nem "tentativa interrompida", nem
   "PRESERVADAS";
2. o DEV tem **12 compras, todas `pago`, 0 canceladas**, e o agregado
   `compra: 12 | 528.64` é idêntico nos baselines antes da migração, depois dela e
   após o último restart;
3. rodando o recuperador **em modo leitura** contra o DEV atual: 12 compras pagas
   com nota, **0** com tentativa de idempotência (pré-requisito do limite 1),
   **0** com prova de interrupção e **0** com arquivo ausente. Nenhuma seria
   tocada nem pelo código novo.

As 12 notas referenciadas existem em disco (12/12 conferidas), e não há nenhuma
`.tmp-` pendente em `static/fotos`.

### 6.7 FIN-09 — estado honesto: corrigido, **sob revisão**

O defeito original (arquivo escrito dentro da transação, nome sorteado duas
vezes) está corrigido e provado. A **atomicidade entre o commit e o arquivo não
existe e não pode existir** neste desenho: o que há é ordem segura (disco testado
antes do commit, promoção atômica depois), compensação quando a promoção falha e
varredura de recuperação no startup quando o processo morre. Isso é robusto, mas
não é "sem janela" — e por isso o item **não** é declarado fechado. Um journal de
arquivos ou um outbox no banco resolveriam a janela e são arquitetura nova:
registrados como decisão futura, conforme o §6 da ordem ("se exigir arquitetura
nova, entregar diagnóstico e manter fora da implementação").

### 6.8 FIN-08 — o que fica aberto, e por quê

A §2/§3/§4 acrescentaram as travas que o escopo pedia: `chave UNIQUE` em
`operacao_idempotencia`, índice único parcial `(trabalhador_id, competencia)`
para mensalidade, `CHECK` de vínculo e índices únicos em `fiado_alocacao`, FKs
`RESTRICT`, `CHECK fiado_credito >= 0`, e `vendas_pdv_cancelamento.venda_id`
unique. O que **não** entra nesta ordem:

1. **Backfill de autoria/origem em fatos antigos** — a ordem proíbe reparar
   histórico por aproximação (§0.6, §6); os 43 `recebimento_fiado` sem
   `trabalhador_id` do DEV continuam como estão e a limitação é dita na tela;
2. **`auditoria_eventos`** — tabela citada por fixture antiga que não existe
   neste esquema; a ordem diz explicitamente para não inventá-la (§6);
3. **Duplicidade histórica de mensalidade** — a constraint é criada **só se o
   diagnóstico encontrar zero conflitos**; em caso de conflito o startup avisa e
   não cria o índice (ver §3.7). Isso é deliberado: escolher a linha "vencedora"
   seria reparação automática de fato antigo.

## 8. Provas no navegador (ordem §8.5)

`tests/test_financeiro_navegador_task012.py` — **27 verificações**, Chromium real,
banco descartável, dados fictícios com prefixo `T012N`, no runner padrão:

| Cenário | O que o navegador confirma |
|---|---|
| C1 mensalidades | as três datas nomeadas no seletor, valor em `R$ 60,00` (sem `60.00`), sem rolagem horizontal |
| C2 fiado parcial | Recebido e Saldo separados, situação "Recebida em parte", extrato com saldo devedor e crédito em cartões distintos — e a premissa provada pelo **efeito no banco** (alocação gravada), não por código HTTP |
| C3 excedente | dívida fechada com 25 alocados, **5 de crédito**, saldo devedor zerado, e o detalhe com "Para onde foi este dinheiro" apontando a dívida |
| C4 desktop | `R$ 1.234.567,89` inteiro, CSS versionado (`base.css?v=`), Σ do acumulado explicado, detalhe devolvendo ao **mesmo recorte** |
| C5 smartphone | 412×915: Visão Geral, Fiados e Relatório de mensalidades sem rolagem lateral; nenhum valor estourando o cartão |
| C6 larguras | 768 e 1024 sem rolagem horizontal |

Capturas em `docs/tasks/evidencias/TASK_012/t012-*.png`. O encerramento remove
apenas a marca `T012N` — não apaga histórico de uso real. **Limite:** o
navegador roda contra o banco descartável; a homologação com os dados reais do
DEV é do dono (§10).

## 9. Migração e DEV — aplicado e conferido

### 9.1 O que o schema desta tarefa acrescenta

DDL idempotente no `criar_tabelas` (sem arquivo de migração, como o módulo já
fazia): tabela `operacao_idempotencia` (§2), coluna `financeiro_movimentacoes.
competencia` com índice único parcial `uq_mensalidade_por_competencia` (§3),
tabela `fiado_alocacao` com 5 índices/constraints (§4), colunas
`fiado_credito_mov.venda_id`/`.financeiro_id` e `CHECK fiado_credito >= 0`.
Nenhuma linha antiga é reescrita por aproximação: a §3 só **rotula** a
competência a partir do vencimento já gravado.

### 9.2 Leitura antes de escrever (diagnóstico próprio no DEV)

| Verificação (leitura) | Resultado |
|---|---|
| identidade | banco `shambala`, usuário `shambala`, local — o DEV existente |
| mensalidades | **520** no total: **516 com trabalhador**, **4 sem trabalhador**, 0 com data ilegível |
| duplicidade por trabalhador/competência | **0 grupos, 0 linhas em excesso** → índice único pode ser criado |
| recebimentos de fiado | 43, todos sem `trabalhador_id` (legado — segue sem backfill) |
| fiado em aberto | 50 vendas; nenhum `fiado_credito` negativo |
| sessões | 1 aberta (não tocada) |
| notas fiscais | 12 referências, **12 arquivos presentes em disco**; nenhum `.tmp-` pendente |

### 9.3 Backup, hash e restauração validada

1. **DOIS backups, e a diferença entre eles importa.** O primeiro é o único que
   serve para desfazer a migração; o segundo é retrato de um DEV **já migrado**
   e não substitui o primeiro:

   | Arquivo (em `docs/tasks/backups/`) | Quando | Estado do banco | Serve para |
   |---|---|---|---|
   | `shambala_pre_task012_PRE_MIGRACAO_20261025081651.dump` (361.887 bytes, SHA-256 `658e873f…707493`) | 08:16, **antes de qualquer escrita** | **sem** o schema da TASK_012 — conferido: `pg_restore -l` não lista `operacao_idempotencia` nem `fiado_alocacao` | **voltar atrás**: é o backup original |
   | `shambala_pre_task012_dev_20261025101223.sql.gz` (224.161 bytes, SHA-256 `fbffbfaa…0c83`) | 10:12, **depois** da migração e dos reinícios | **com** as duas tabelas (conferido no `.gz`) | retrato posterior; **não** desfaz a migração |

   Ambos com `.sha256` ao lado, conferidos com `sha256sum -c` → SUCESSO. O dump
   pré-migração estava só em `/tmp` (cópia de trabalho) e foi copiado para o
   destino permanente preservando o `mtime` e o hash — `/tmp` não sobrevive a
   reboot, e esse é o arquivo que importa para reverter. O diretório
   `docs/tasks/backups/` é **ignorado pelo `.gitignore`** (não versionado, como os
   das TASK_010/011): o backup vive no servidor, não no repositório.
2. **SHA-256** calculado e reconferido: `658e873f7549241b72407387f888eeb59d2f6968b610affda60e435477707493`
   (registrado em `docs/tasks/evidencias/TASK_012/*.sha256`);
3. restauração em banco descartável `shambala_t012_restore` (criado de
   `template0`, sem tocar no DEV) com `pg_restore --no-owner`;
4. **limites do restore, registrados com precisão**: `rc=1` com 2 erros
   `permissão negada para alterar privilégios padrão` (o usuário `shambala` não
   é owner dos objetos de produção do dump). Nenhum erro de dado, tabela ou
   constraint. A validação é a comparação de conteúdo abaixo, não o rc.

Comparação de fatos entre DEV e o restaurado (contagens): `pessoas` 678=678,
`checkins` 2918=2918, `financeiro_movimentacoes` 862=862, `vendas_pdv`
1859=1859, `trabalhadores` 70=70, `caixa_movimentos` 58=58,
`caixa_sessao_eventos` 0=0, `atendentes` 22=22. Log em
`evidencias/TASK_012/dev_restore_validacao.log`.

### 9.4 Aplicação e serviço

- O DDL roda no startup. Reiniciei **apenas** o serviço da porta 9000, e a
  propriedade do processo foi provada antes do `kill`: o PID estava nomeado no
  meu próprio log de subida, com `.venv/bin/python -m uvicorn main:app --port
  9000` e cwd neste workspace. Nenhum outro processo foi tocado.
- Primeiro startup (log `dev-startup-task012.log`):
  `[startup] 517 mensalidade(s) receberam competência rotulada do vencimento já
  gravado.` — **é a única escrita**; "Application startup complete." e nada mais.
- Segundo startup (log `dev-startup-task012-r2.log`): nenhuma mensagem de
  competência e **nenhuma cobrança nova** — o caminho é idempotente.
- Schema conferido depois: `operacao_idempotencia` criada, `fiado_alocacao`
  criada com 5 índices, coluna `competencia` criada,
  `uq_mensalidade_por_competencia` criado (o nome do índice é
  `uq_mensalidade_por_competencia`), `fiado_credito_mov.venda_id` criada,
  `CHECK` de crédito presente.
- Competência rotulada: **517 linhas** — 516 das mensalidades com trabalhador e 1
  das 4 sem trabalhador (que também tem vencimento legível). As 3 restantes
  ficaram sem competência por não terem vencimento legível, e continuam assim de
  propósito: a consulta as alcança pela segunda perna da regra e as marca como
  "histórica: sem competência registrada". **0 divergências** entre a competência
  rotulada e o mês do vencimento já gravado — a rotulagem não inventou valor nem
  mudou data.

### 9.5 A alteração prevista, e o que foi preservado

São duas afirmações diferentes e ficam separadas, porque misturá-las foi erro da
versão anterior deste relatório:

**(a) O que a aplicação MUDOU (previsto e conferido):**
- DDL: tabela `operacao_idempotencia`, tabela `fiado_alocacao` (5 índices),
  coluna `financeiro_movimentacoes.competencia`, índice único
  `uq_mensalidade_por_competencia`, colunas `fiado_credito_mov.venda_id` e
  `.financeiro_id`, `CHECK fiado_credito >= 0`;
- **preenchimento de competência em 517 linhas** de mensalidade (ver §9.4). Isto
  é escrita de dado, não só schema: 517 linhas ganharam valor numa coluna que
  antes não existia. Nenhum valor, data, status ou vínculo dessas linhas foi
  alterado — a competência foi copiada do mês do vencimento já gravado.

**(b) O que foi PRESERVADO (medido antes × depois):**
`baseline_dev.py` e `baseline_financeiro_dev.py` **idênticos linha a linha**.
Nenhum lançamento criado, alterado ou apagado; nenhuma obrigação paga; nenhuma
sessão mexida.

**Alcance do hash, sem exagero.** `fm_max_id_hash` é o `md5` de
`id:tipo:status:categoria:valor:data_movimentacao` de **todas as linhas** do
livro-caixa, ordenadas por id. Ele **não** cobre as colunas todas: ficam de fora
`descricao`, `trabalhador_id`, `pessoa_id`, `conta_id`, `competencia`,
`data_pagamento`, `forma_pagamento`, `pix_copiadecola`, `nota_fiscal_foto` e
`created_at`. É prova forte de que o livro-caixa não mudou em conteúdo, tipo,
situação, categoria, valor e vencimento — e é só isso que ela prova. A
competência, justamente por mudar nesta aplicação, está **fora** do hash; por
isso ela é conferida pela contagem e pela comparação com o vencimento (§9.4), não
pelo hash.

Endpoints depois do restart: `/login` 200, `/financeiro/compras` 303 (login),
`/financeiro/mensalidades` 303 (login), `/static/css/base.css` 200. Nada de
demonstração financeira foi executado no DEV.

**Segundo restart, já com o código da devolução** (`dev-startup-task012-r3-final.log`):
o serviço da 9000 foi reiniciado de novo porque as correções pedidas mudaram o
comportamento do startup (recuperação de compra sem nota) e o vocabulário da
tabela de tentativas. O log não trouxe nem "competência rotulada" (já estavam
todas) nem "compra sem nota" (não havia nenhuma) — exatamente o esperado num
banco em dia. A `CHECK` de `operacao_idempotencia` passou a aceitar
`em_andamento | concluida | anulada`, e os dois baselines (geral e financeiro)
seguem **idênticos** aos do restart anterior: o restart não tocou fato algum.
Endpoints: `/login` 200, `/financeiro` 303, `/financeiro/compras` 303,
`/terminal` 303. Código em memória = `35f1358`.

### 9.6 Limitações do DEV

1. **A homologação autenticada é do dono** — nenhuma senha foi pedida ou
   alterada, nenhum usuário/permissão foi tocado; eu só verifiquei que as telas
   redirecionam para o login.
2. **Os 43 recebimentos de fiado do legado continuam sem destino.** Aplicar a
   tabela não conserta o passado: eles seguem no histórico, as dívidas
   correspondentes continuam devendo o valor de face, e a tela diz isso.
3. **O índice único de mensalidade só existe porque não havia duplicidade.**
   Se o DEV tivesse conflito, o startup avisaria e não criaria o índice — a
   mesma rotina que a suíte de mensalidades exercita.
4. **A sessão de caixa aberta (1) não foi tocada** e continua pelo fluxo
   legado; novos recebimentos nela continuam bloqueados (TASK_008).
5. **Produção não foi acessada nem por leitura**, e não houve push. Os dois
   backups estão em `docs/tasks/backups/` com `.sha256` ao lado (diretório **não
   versionado**, por `.gitignore`, igual às TASK_010/011). **Para reverter a
   TASK_012 use o `PRE_MIGRACAO`** — o outro já contém o schema novo. A
   restauração validada (§9.3) foi feita **sobre o dump pré-migração**, que é o
   que precisa funcionar; o retrato posterior não foi restaurado (não é preciso
   para reverter) — registrado como limite.

## 10. Roteiro de conferência do dono (máximo 10 passos)

Os passos 1–5 são **somente leitura** e podem ser feitos direto no DEV. Os
passos 6–9 criam fatos **fictícios** e explícitos, separados do uso real; o 10 é
o encerramento. Nada aqui pede dívida real nem exclusão de histórico.

| # | O que fazer | Onde | O que deve aparecer |
|---|---|---|---|
| 1 | Abrir a Visão Geral do Financeiro no mês corrente | Financeiro | Os cartões com valores em `R$ 1.234,56` (ponto de milhar) e, por conta, uma linha **Σ** dizendo até qual data o acumulado conta |
| 2 | Rolar até **Fiado em aberto** | Financeiro | Colunas **Valor original · Recebido · Saldo**; a nota abaixo soma as três; dívidas antigas vêm com o aviso "anteriores ao registro de destino do recebimento" |
| 3 | Abrir **Relatórios** com o mesmo mês | Financeiro → Relatórios | Cinco cartões separados: Recebido (pagos), Pago (saídas pagas), A receber, A pagar, Cancelados — e "Total Entradas" **não** existe mais somando pendente |
| 4 | Em **Relatório de mensalidades**, trocar "Qual data o mês usa" entre competência, vencimento e pagamento | Financeiro → Relatório de mensalidades | A lista muda conforme a pergunta, o título diz qual data está em uso e linhas antigas aparecem como "histórica: sem competência registrada" |
| 5 | Abrir a ficha de um trabalhador com fiado | Financeiro → Fiados → trabalhador | **Comprado a fiado · Já recebido · Saldo devedor** (o crédito do trabalhador aparece em cartão separado) e cada venda com Recebido/Saldo |
| 6 | *(fictício)* Cadastrar um trabalhador de teste, ex. "Teste TASK_012" | Trabalhadores | Ele passa a aparecer nas listas — **anote o nome**, é o que identifica o registro |
| 7 | *(fictício)* Lançar uma compra a fiado de R$ 40,00 para esse trabalhador e receber R$ 15,00 escolhendo a dívida | Caixa → PDV / Caixa → Fiados | Antes de confirmar, a tela mostra a distribuição; depois, a dívida fica **R$ 25,00 de saldo com R$ 15,00 recebido** (nem quitada, nem devendo 40) |
| 8 | *(fictício)* Receber mais R$ 50,00 contra a mesma dívida | Caixa → Fiados | A dívida de **R$ 25,00** que restou é quitada e os **R$ 25,00** de excedente (50 − 25) viram **crédito** do trabalhador; "Recebimentos" sobe os R$ 50,00 entregues — o crédito não é receita nova |
| 9 | *(fictício)* Abrir o detalhe do lançamento do recebimento | Financeiro → visão de lançamentos | A seção **"Para onde foi este dinheiro"** mostra a dívida abatida e o valor, ligados ao recebimento |
| 10 | Encerrar o cadastro de teste **inativando** o trabalhador "Teste TASK_012" (Trabalhadores → editar → ativo = não) e **preservando os fatos** | Trabalhadores | O cadastro sai das listas de uso sem apagar nada. **Não exclua** os lançamentos, a venda a fiado, o crédito nem o recebimento: o nome de teste identifica o registro fictício, não autoriza apagar histórico financeiro — e apagar quebraria os vínculos de alocação que esta tarefa criou |

## 11. O que é herdado e o que veio na retomada

| Item | Origem |
|---|---|
| §1 levantamento, matriz e evidências da idempotência (`416bf46`) | sessão anterior |
| §2 idempotência de toda entrada de dinheiro (`00617c1`) | sessão anterior |
| §3 mensalidade com competência (`a4c5346`, docs `cf4d8da`) | sessão anterior |
| §4 destino do recebimento de fiado (`a6f0328`, docs `37f3dc4`) | sessão anterior |
| Código da §5 (rotas, `servico_fiado`, templates) e a suíte `test_financeiro_consultas_task012.py` | escritos na sessão anterior; **não reescritos** aqui |
| Limpeza da suíte §5 | **incompleta** pela interrupção; concluída nesta retomada (§5.6) |
| Provas da §5 (74 asserções) | **executadas pela primeira vez** nesta retomada — a sessão anterior não chegou a rodá-las |
| §6 / FIN-09 (`rotas/compras.py`, prova de ciclo de arquivo, injeção guardada) | **desta retomada** |
| Abaixamento do M7 (`test_cancelamento_vendas`) e "—" na linha cancelada | **desta retomada** |
| Registro de chaves em `CHAVES_USADAS` nas duas suítes da TASK_012 | **desta retomada** |
| §5, §6, §9, §10 e §11 do relatório | **desta retomada** |
| Correção da regressão TASK_011 N1 + suítes de navegador destravadas + `--user-data-dir` do K16 | **desta retomada (2ª parte)** |
| FIN-09: estado `anulada`, `desfazer`, compensação em duas transações, `recuperar_compras_sem_nota` no startup | **desta retomada (2ª parte)** |
| Trava `pg_advisory_xact_lock` na geração de mensalidade | **desta retomada (2ª parte)** |
| Suíte de navegador `test_financeiro_navegador_task012.py` | **desta retomada (2ª parte)** |

Não reverti, reorganizei nem reescrevi nenhuma das entregas herdadas: os
commits `00617c1`, `a4c5346` e `a6f0328` estão intactos no histórico e as suas
suítes rodam verdes.

### 11.1 Commits desta retomada

| Commit | Conteúdo |
|---|---|
| `e8d8d8e` | feat §5 — consultas do financeiro na conta da §4 (código herdado + suíte com limpeza concluída) |
| `c17b5cd` | fix §6 — a foto da nota deixa de viver dentro da transação (FIN-09) |
| `59dd73b` | fix §5/§6 — venda cancelada sem saldo na ficha; limpeza limitada ao próprio rastro |
| `9d3be28` | docs §5, §6 e §9 + evidências + SHAMBALA_CONTEXTO |
| `740492a` | fix — limpeza sem pista falsa (`documento` com ids reciclados) e helper morto removido |
| `f4e0d81` | fix — regressão do leitor na Visão Geral (TASK_011 N1) e suítes de navegador destravadas |
| `ac7120c` | fix §6 — FIN-09 fecha o ciclo: morte entre commit e promoção, compensação, chave `anulada` |
| `42a551e` | fix §3 — geração de mensalidade com trava real quando falta o índice |
| `e97b0a5` | test — percursos de navegador da TASK_012 (desktop e smartphone) |

Corrida final (`runner_completo_etapa5_6_r2_final.txt`, árvore `740492a`):
as quatro suítes da TASK_012 verdes — **145/145**, **64/64**, **91/91**,
**74/74** — `test_cancelamento_vendas` de volta a verde, `DEV inalterado: ok`.
As quatro suítes vermelhas são as de ambiente listadas em §12.

## 12. Estado final e limitações remanescentes

**PARCIALMENTE CONCLUÍDA.** As etapas §2 a §6 têm implementação e prova, mas
três itens impedem declarar a tarefa concluída, e ficam nomeados:

1. **FIN-09 segue em revisão** (§6.6): a janela entre o commit e a materialização
   do arquivo não é eliminável neste desenho — há detecção e compensação, não
   atomicidade. Um journal/outbox resolveria; é arquitetura nova, fora do escopo
   autorizado.
2. **FIN-06 está contido por bloqueio**, não resolvido: o estorno é recusado e o
   **reembolso de fiado já pago continua pendente** (§1.1).
3. **A homologação autenticada no DEV é do dono.** A prova automatizada de
   navegador exigida pela ordem **existe e passa** (27 verificações, desktop e
   smartphone, §8 abaixo), mas ela roda em banco descartável com dados fictícios
   — não substitui o dono abrindo as telas no DEV com os dados dele.

E a §6 tem FIN-08 parcial por decisão da própria ordem (§6.8).

| Pendência | Natureza |
|---|---|
| **FIN-09 sob revisão** | janela entre commit e arquivo: detecção + compensação + recuperação no startup, sem atomicidade; journal/outbox seria arquitetura nova (§6.6) |
| **FIN-06 contido, não resolvido** | reembolso de fiado já pago continua pendente (§1.1) |
| Homologação autenticada no DEV | do dono (passos 6–9 do roteiro); a prova automatizada existe (§8), mas roda em descartável |
| 43 recebimentos de fiado sem trabalhador | legado; tela de destino histórico seria trabalho novo (questão registrada na §4.10) |
| Vendas/sangrias legadas permitidas | decisão do dono continua pendente (§7 da ordem) |
| Reembolso de fiado pago, estorno após sessão fechada, reabertura de caixa | excluídos pela ordem (§7) |
| Conciliação bancária / importação de extratos | fora do escopo |
| Suítes de navegador | **destravadas**: as quatro que falhavam rodaram e passaram — `test_gestao_correcoes_navegador` 11/11, `test_terminal_leitor` 24/24, `test_codigo_trabalhador` 18/18, `test_financeiro_visaogeral_navegador_task011` 42/42. Duas causas distintas: (a) o K16 chamava `chromium` pela linha de comando **sem `--user-data-dir`** e abortava sem perfil gravável — corrigido no teste; (b) as demais falhavam por escrita em `/home/claudio/Documentos` no MEU sandbox de execução, não no ambiente — os caminhos existem e são graváveis, e rodando fora da restrição passam. A suíte TASK_011 tinha, além disso, **1 falha real introduzida pela §5** (contagem de `.fin-def` com "não é saldo"), corrigida com marca própria `fin-def-resultado` |
| Publicação | fora do escopo (sem push) |
