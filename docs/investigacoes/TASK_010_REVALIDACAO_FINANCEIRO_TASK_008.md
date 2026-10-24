# TASK_010 — Revalidação do financeiro da TASK_008 (complemento à revisão TASK_009)

Data: 2026-10-24 · Branch: main · Código revisado: árvore de trabalho pós-TASK_010 (base `1e57f79`)
Ordem executora: `docs/tasks/TASK_010_CORRECOES_ISOLAMENTO_SALDO_ZERO_REVALIDACAO.md`
Documento anterior: `docs/investigacoes/TASK_009_REVISAO_TECNICA_FINANCEIRO_TASK_008.md` (parecer "requer correções" — preservado, não reescrito)

## Método

Este complemento reexecuta a matriz da TASK_009 **depois** de REV-01/REV-02/REV-03
corrigidos, com o destino da execução provado por token da execução corrente
(`SHAMBALA_EXEC_TOKEN`) e por `current_database()` da conexão efetiva do próprio
servidor HTTP. Fonte primária: runner completo v2
(`docs/tasks/evidencias/TASK_010/runner_completo_v2.log`, RC=0, token
`t010-20261024203621-1103384`, banco efetivo `shambala_test_passes` confirmado
tanto pelo script quanto pelo servidor). Nada foi revalidado contra produção;
DEV apenas recebeu o código pelo procedimento do §5 da ordem (abaixo).

## Achados da TASK_009 — estado após correção

### REV-01 (crítico) — RESOLVIDO, com prova antes/depois

Causa raiz: o runner preparava um banco `_test` e a aplicação subia contra outro,
resolvido silenciosamente por `.env.test` (variável do shell não tinha autoridade
nenhuma sobre o arquivo, e o arquivo não era confrontado com o pedido).

Contrato final de configuração (vale para runner, launcher, app e suítes):
- O arquivo nomeado em `SHAMBALA_ENV_FILE` é a **autoridade** do destino; o app o
  carrega com `override=True` (`main.py`), de modo que variável homônima do shell
  não substitui sorrateiramente o arquivo — nem o arquivo silencia o pedido.
- Shell e arquivo são **confrontados** antes de qualquer efeito
  (`trava_isolamento.exigir_configuracao_consoante`): ambos presentes e
  diferentes → aborta sem DDL, sem seed, sem startup.
- A identidade provada é a da **conexão efetiva** (`current_database()` /
  `current_user()`), não o nome declarado em arquivo.
- Cada execução gera token (`t010-<data>-<pid>`) ecotado na linha
  `[TESTE] Ambiente descartável confirmado: {… 'token': …}`; as suítes só aceitam
  prova com o token da execução corrente — log antigo não vale como prova.
- O runner materializa um env temporário 0600 por execução como destino efetivo,
  exige sufixo `_test` antes de qualquer DROP/CREATE, e nunca mata processo
  alheio (só o PID que ele mesmo registrou).

Provas (todas em `docs/tasks/evidencias/TASK_010/`):
- `sentinelas_AB.log` — bancos descartáveis A e B com sentinelas próprias; os
  quatro cenários negativos (conflito pedido-vs-arquivo no runner, no launcher,
  env sem destino, destino inacessível) abortam com rc≠0 e as duas sentinelas
  permanecem intactas; a subida positiva amarra token + `banco_efetivo`.
- `mutacoes_antes_depois.log` — em **cópia isolada** (`/tmp`, produto intocado), a
  reintrodução da escolha fixa de `.env.test` faz a prova negativa falhar (rc=1,
  avançou para DDL), exatamente como a ordem §37 exige; com a correção, o runner
  recusa A/B antes de qualquer DROP.

### REV-02 (alto) — RESOLVIDO, com prova antes/depois

- `servico_sessao.moeda_nao_negativa()` aceita `0`/`"0,00"`, rejeita negativo
  **antes** do arredondamento, quantiza a centavo `ROUND_HALF_UP`
  (`0,001 → 0.00` aceito; `0,005 → 0.01`), teto `9999999999.99` aplicado **após**
  o arredondamento, `NaN`/`inf`/texto → `valor_invalido` (inclusive `1e400`, via
  `InvalidOperation`). `moeda()` permanece estritamente positiva — eventos de
  recebimento/saída não relaxam.
- `POST /caixa/abrir` e a contagem do `POST /fechar` usam o parser novo
  (`rotas/caixa.py`); fechamento de sessão vazia com contado `0,00` fecha com
  divergência `0,00` e **zero** lançamentos financeiros fictícios.
- Provas: CASO 15 da suíte de eventos (unidade + HTTP real, incluindo `0,005`
  HALF_UP via POST direto), cenários C9 (desktop) e C10 (smartphone 412×915) do
  navegador TASK_008 — abrir R$ 0,00 e fechar sessão vazia com contado 0 apenas
  usando a tela. Evidências PNG em `docs/tasks/evidencias/TASK_008/`
  (`c9-desktop-aberto-zero.png`, `c9-desktop-fechado-zero.png`,
  `c10-smartphone-fechado-zero.png`).
- Antes/depois: mutação REV-02 em cópia isolada (`moeda` estrita de volta na
  rota) faz `abrir R$ 0,00` voltar a ser RECUSADO; com a correção é ACEITO
  (`mutacoes_antes_depois.log`).

### REV-03 (médio) — RESOLVIDO, com prova antes/depois

- `tests/subir_app_descartavel.sh` agora: aplica o arquivo nomeado como
  autoridade → importa `banco` **depois** do env aplicado → abre a **conexão
  efetiva** e valida `current_database()`/`current_user()` → só então executa o
  uvicorn. A declaração de "confirmação de conexão" deixou de ser nominal.
- Prova negativa: com destino inacessível (porta 55432), o launcher corrige
  aborta com rc≠0 antes de qualquer startup ("Nenhum processo de teste foi
  iniciado"); a mutação REV-03 (validação apenas lida o arquivo) deixa o
  launcher chegar ao startup sem conexão válida — a prova detecta (rc=3), em
  cópia isolada.

## Matriz da TASK_009 reexecutada (runner v2, destino comprovado por token)

Todas as 15 suítes da lista fixa terminaram rc=0, incluindo
`teste_negativo_isolamento.py` (antes fora da lista). Contagens reais das suítes
financeiras: sessão/eventos **139 aprovadas / 0 falhas**; navegador TASK_008
**41 aprovadas / 0 falhas**. Não perseguimos a contagem histórica 372 — os
números acima são os observados nos logs.

| Item da matriz (TASK_009) | Reexecução | Resultado |
|---|---|---|
| Sessão por eventos; efeito físico separado | CASOs 1–15 (suíte sessao_eventos) | ✔ com identidade por token |
| Cenário misto 102/113/105 → 183 | bloco "CENÁRIO MISTO DETERMINÍSTICO" (inicial 100 → físico 183), via HTTP real | ✔ prévia e fechamento batem; recebimento/sangria com lançamento financeiro vinculado |
| Recebimento em sessão legada recusado | C7 (navegador) + CASO legado POST direto | ✔ `sessao_legada`; legado fora da barra de destino; nada gravado |
| Recusa de recebimento em **lote** no legado | LOTE: `POST /financeiro/mensalidades/receber` com 2 mensalidades em sessão legada | ✔ `sessao_legada_recebimento`, ambas permanecem `pendente` |
| PIX sem gaveta (backend permitia, PDV bloqueava) | ver "Divergência PIX sem sessão" abaixo | ✔ resolvida na raiz da divergência |
| Fechamento por eventos; contagem divergente | C6 + CASOs de divergência | ✔ recusado com explicação; PDF exportado (C6 `%PDF`) |
| Uma sessão aberta / aberturas concorrentes | CASO 11 (duas aberturas concorrentes → exatamente uma sessão) | ✔ |
| Fechamento duplo concorrente | CASO 3 | ✔ nada fecha de novo |
| Sangria com destino; par saída↔entrada | C4 + CASO 13 (efeito global zero, sem despesa falsa) | ✔ com risco documentado (abaixo) |
| Cancelamento × venda, crédito consumido, parcial com sobra | suíte `test_cancelamento_vendas.py` (rc=0) + M10/M11/M12/M14 | ✔ |
| Rollback de escrita parcial | R11/M14 (falha em etapa derruba transação inteira) | ✔ |
| Migração repetida / instalação nova | M1/M2 (suítes de banco dedicado `_test` selado) | ✔ segunda execução sem duplicação ou perda |
| Rotas alternativas de abertura | suíte sessao_eventos cobre POST direto, por caixa, e recusas | ✔ sem burlar o portão |
| Isolamento antes de DDL | sentinelas A/B + teste_negativo_isolamento | ✔ agora com contrato real, não nominal |

## Divergência PIX sem sessão — resolvida no PDV

A TASK_009 apontou: o backend permitia venda PIX sem sessão
(`conta_pix_solta`, `rotas/caixa.py`), mas o PDV bloqueava tudo com
"precisa abrir caixa" (`precisa_abrir` + `pdv.html`). Correção mínima:
- `GET /pdv` define `sem_sessao=True` quando não há movimento aberto **e** existe
  conta PIX válida; sem conta PIX, o portão antigo permanece.
- A tela mostra banner "Caixa sem sessão aberta: vendas disponíveis apenas por
  **PIX**", espécie/fiado ficam desabilitados, PIX pré-selecionado; o painel
  exibido segue o mesmo flag.
- Espécie sem sessão continua recusada (`sessao_necessaria`), por rota e por tela.
Provas: C1 reescrito (navegador desktop: PIX habilitado, espécie POST →
`sessao_necessaria`, nada gravado), C11 (navegador: venda refrigerante via modal
PIX sem sessão → `vendas_pdv` concluída com `caixa_movimento_id` NULL, zero
sessões criadas; PNG `c11-pix-sem-sessao.png`), CASO 10 (rota: GET responde a
tela, não o portão; POST espécie recusado).

## Identidade por comando repetido vs idempotência de POST — análise honesta

- O que existe: unicidade por **identidade do comando** nos eventos
  (`caixa_sessao_eventos`), que torna repetição sequencial e clique duplo
  convergentes (M11: um único efeito efetivo). Isso cobre reenvio do mesmo
  comando de evento.
- O que **não** existe e não deve ser fingido: dois POSTs legítimos e
  independentes a `/caixa/pdv/finalizar` criam duas vendas — não há idempotência
  de requisição HTTP no PDV, e introduzi-la agora mudaria a semântica de
  "venda" sem demanda. Fica documentado como comportamento, não como proteção.

## Sangria — par vinculado confirmado; risco remanescente documentado

O par saída↔entrada (`sangria_id`) foi conferido na execução real (CASO 13,
efeito global zero; C4 comprova destino identificado; sangria sem destino
recusada, inclusive no legado). A **ausência de constraint** no banco garantindo
o par permanece como risco documentado — sem DDL novo, conforme o limite
"não remodelar o financeiro" da ordem.

## `test_financeiro_integracao.py` — classificação honesta

A suíte antiga referencia a tabela `auditoria_eventos`, que não existe no schema
atual (a auditoria migrou para outro modelo na TASK_008). Ela **não** está na
lista fixa do runner e não foi "aprovada" de forma alguma: é harness
incompatível com o produto atual, mantida no repositório como histórico. Não
foi criada tabela alguma para satisfazer suas fixtures.

## Evidências e substituições

- `runner_completo.log` (v1, RC=1) — **preservado** como registro histórico da
  divergência encontrada no meio da execução; substituído pelo
  `runner_completo_v2.log` (RC=0) como fonte canônica das contagens.
- `sentinelas_AB.log`, `mutacoes_antes_depois.log`, `dev_baseline_antes.txt`,
  `dev_baseline_depois.txt`, `dev-startup-task010.log` — execuções desta tarefa.
- Os `.log` ficam fora do commit pela convenção gitignore; o conteúdo relevante
  está transcrito neste documento e nos adendos da ordem. Logs não contêm
  credenciais (o banco de teste usa conexão local sem senha).

## Aplicação no DEV (§5 da ordem)

- Backup: `docs/tasks/backups/shambala_pre_task010_20261024_205023.sql.gz`
  (pg_dump custom|gzip), SHA-256 `324fcd19…c468bf` registrado em
  `shambala_pre_task010_20261024_205023.sha256`.
- Restore validado em `shambala_t010_restauracao_test` (descartável): as 19
  conferências da baseline (contagens **e** somas de valor) bateram exatamente
  com o DEV de origem; os dois erros do `pg_restore` foram apenas
  `ALTER DEFAULT PRIVILEGES FOR ROLE postgres` (donos de privilégio, sem efeito
  sobre dados).
- Restart exclusivo do serviço existente da 9000 (mesmo comando plain uvicorn,
  sem env de teste; PID antigo 477635 → novo 1169423). Startup limpo; nenhuma
  linha de modo de teste no log do DEV (0 ocorrências).
- Baseline idêntica antes/depois (`dev_baseline_antes.txt` =
  `dev_baseline_depois.txt`): pessoas 678, checkins 2918, trab 70/70 ativos,
  códigos 70 (todos `ativo`), presenças 20, correções 0, vendas 1859 /
  R$ 25.886,75 (35 canceladas), financeiro 862 / R$ 69.446,04, caixas 5,
  sessões 58 (1 aberta — legado Bazar id 42, intocado), eventos 0, sangrias 49,
  atendentes 22, grupos_permissoes 23, contas 4, produtos 39, mensalidades 520.
- `GET /login` 200 após restart. Nenhuma operação financeira de demonstração foi
  executada no DEV. Sem DDL novo.

## Parecer

REV-01, REV-02 e REV-03 estão **corrigidos e provados** (comportamento, prova
negativa por sentinela e prova antes/depois por mutação em cópia isolada). A
matriz da TASK_009 foi reexecutada integralmente em banco único comprovado por
token, com RC=0 em todas as 15 suítes e contagens reais registradas. **Não
encontramos novos bloqueadores financeiros** na revalidação.

Parecer técnico: **a TASK_008 está tecnicamente aprovada para
considerarmos as correções da revisão concluídas**, com dois riscos explícitos e
não bloqueadores registrados acima (ausência de constraint do par de sangria;
ausência de idempotência HTTP no POST do PDV, documentada como comportamento).
Aprovação técnica **não** autoriza produção nem substitui a homologação manual
do dono — o roteiro abaixo é o instrumento para isso.

## Roteiro de homologação do dono (dados exclusivamente fictícios)

Nunca executar com fatos reais; usar um caixa de teste criado por você ou o
ambiente descartável. Registros criados por este roteiro **ficam no histórico**
do ambiente onde for executado (sessões, eventos, vendas e lançamentos).

1. Abra `/caixa` num navegador e crie um caixa novo chamado "Ensaios do Dono"
   (isto é dado fictício de teste, não a lanchonete real).
2. Abra sessão dele com **saldo inicial R$ 0,00** — antes da correção, o sistema
   recusava; agora deve abrir normalmente.
3. Sem sessão aberta, entre no PDV desse caixa: deve aparecer o aviso "vendas
   disponíveis apenas por PIX", PIX já selecionado, e os botões de espécie e
   fiado bloqueados. Registre uma venda PIX fictícia e confira o QR.
4. Abra a sessão; venda um item em espécie; receba uma mensalidade em PIX
   pointing ao caixa; faça uma sangria com destino escolhido. Confira o total da
   tela bater com a gaveta que você simulou.
5. Tente fechar com valor divergente: o sistema deve recusar explicando a
   diferença. Feche com o valor certo; confira o PDF e que o caixa voltou a
   "sem sessão".
6. Feche a sessão vazia do passo 2 com contagem R$ 0,00: deve fechar sem criar
   nenhum lançamento financeiro.
7. No celular (tela estreita), repita abrir com zero → vender → fechar.

Se cada passo se comportar como descrito, a TASK_008 pode ser considerada
homologada por você; qualquer desvio é evidência a registrar, não a contornar.
