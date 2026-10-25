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
| §3 mensalidades | Competência inferida de `data_movimentacao LIKE 'YYYY-MM%'` e do texto da descrição; `_existe_mes` + INSERT sem constraint (FIN-05) | Coluna `competencia` (`YYYY-MM`) em `financeiro_movimentacoes`; índice único parcial `(trabalhador_id, competencia)` para `categoria='mensalidade'` criado no startup, com diagnóstico de duplicidade **antes** de aplicar e sem tocar linha em conflito; serviço com checagem + `ON CONFLICT … DO NOTHING` e retorno de "quantas novas"; `POST /mensalidades/gerar` (geração manual) no lugar do GET que gerava; recusas `periodo_invalido` / `mensalidade_ja_registrada` / `sem_mensalidade_ativa`; competência, vencimento e recebimento separados na tela + faixa de conflito histórico | `tests/test_financeiro_mensalidades_task012.py` (64 asserções: chave do banco nos quatro sentidos, geração repetida e concorrente, dois trabalhadores no mesmo mês, virada de ano, mês curto e bissexto, elegibilidade, leitura sem efeito, mensagens de operador, baixa individual e em lote, lote atômico com rollback, duplicidade histórica fictícia sem perda de fatos, rotulamento que não escolhe vencedora, startup que não aborta, backfill idempotente, cancelamento, lançamento manual) | §2 |
| §4 fiados | Um recebimento quita dívidas por ordem FIFO e o excedente vira crédito **sem vínculo** entre o lançamento, as dívidas e o crédito; `fiado_pago` booleano é a única alocação | — em execução — | — | §2, §3 |
| §5 consultas | Soma de eventos com lançamentos, fórmulas de saldo divergentes por tela, GET `/financeiro/mensalidades` gerando cobrança (este último **resolvido na §3**) | — em execução — | — | §3, §4 |
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

### 3.3 Repetição e concorrência não duplicam

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
