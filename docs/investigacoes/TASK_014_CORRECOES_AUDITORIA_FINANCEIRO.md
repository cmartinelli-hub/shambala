# TASK_014 — Correções da auditoria financeira

Ordem: `docs/tasks/TASK_014_CORRECOES_AUDITORIA_FINANCEIRO.md`.
Referência auditada: TASK_013 (`docs/investigacoes/TASK_013_AUDITORIA_FINANCEIRO_ANTES_HOMOLOGACAO.md`), commit `f7ef016`.
Data: 2026-10-25.

**Status: concluída no escopo da ordem, com ressalvas registradas.**
AUD-01 corrigido e provado; AUD-02 com evidência histórica marcada e regressão
consolidada (23 suítes, `rc=0`); provas pendentes da TASK_013 reproduzidas,
inclusive morte **real** de processo; aviso/procedimento do FIN-09 entregue;
aplicado no DEV com backup e baseline idênticos. Ressalva central: o FIN-09 segue
como **protocolo mitigado**, sem atomicidade entre banco e arquivo.

---

## 1. AUD-01 — isolamento de chave por operador e operação

### Achado → causa → arquivo/função

`operacao_idempotencia` guardava `autor_id`, mas as duas funções que decidem
devolver um resultado persistido conferiam **apenas a chave**:

- `servico_idempotencia.reconhecer()` — `SELECT ... WHERE chave = %s`, sem
  comparar `operacao` nem `autor_id`;
- `servico_idempotencia.reservar()` — mesma coisa na corrida, e devolvia
  `REPETIDA` com `destino`/`documento` de quem chegou primeiro;
- `exigir_reserva()` só repassava o autor ao `INSERT`, nunca o comparava.

### Prova do defeito ANTES da correção

Contra app descartável real (porta 9018, banco `shambala_test_passes`, token
`t014-aud01`), dois atendentes distintos com permissão de escrita
(`evidencias/TASK_014/aud01_antes_da_correcao.txt`):

```
A envia: (303, '/financeiro')     → 1 lançamento, tentativa com autor_id=4 (A)
B REENVIA a mesma chave e o mesmo corpo: (303, '/financeiro')
  lançamentos depois: 1   (B não criou efeito: recebeu a resposta de A)
```

O segundo operador recebeu o destino/documento do primeiro. Chave não é
credencial — e a entropia não corrige reutilização por tela, log, suporte ou
dispositivo compartilhado.

### Correção

`servico_idempotencia.py`:

- **`_contexto_confere(registro, operacao, autor_id)`** — compara a operação da
  rota/serviço e o autor autenticado. Registro com `autor_id` nulo ou operação
  vazia **não** confere: recusa segura, sem atribuir autoria retroativa.
- **`reconhecer(..., operacao, autor_id)`** — confere o contexto **antes** de
  qualquer resposta persistida; se não bater, levanta `TentativaRecusada`
  (`tentativa_contexto`) sem revelar destino, documento, autor ou conteúdo.
- **`reservar()`** — a corrida na reserva também confere: contexto divergente
  devolve um desfecho próprio **`CONTEXTO`** (antes caía em `CONFLITO`, e a
  mensagem dizia "conteúdo divergente" quando o problema era outro operador).
- **`exigir_reserva()`** — `autor_id` passou a ser **obrigatório**: sem ele não
  há como distinguir "o dono repetindo" de "outro atendente com a chave", e a
  função prefere recusar.
- **`rotas/tentativa.resposta_de_tentativa(..., operacao, autor_id)`** — as nove
  rotas que reconhecem passaram a informar o contexto.

Unicidade global da chave preservada: a colisão de contexto **não** vira operação
nova silenciosa sob a mesma chave — é recusa.

### Matriz de rotas (autor/operação)

`evidencias/TASK_014/aud01_matriz_rotas.md` — gerada a partir do código, não à
mão. Onze rotas financeiras protegidas da TASK_012, todas com `autor_id` e com a
**mesma operação** no reconhecimento e na reserva:

| rota | operação | transições |
|---|---|---|
| `biblioteca.vender_livro` | `biblioteca_venda` | reconhece, reserva |
| `caixa.adicionar_credito_fiado` | `credito_anticipado` | reconhece, reserva |
| `caixa.finalizar_venda` | `pdv_venda` | reconhece, reserva |
| `caixa.pagar_divida_fiado` | `fiado_pagar_divida` | reconhece, reserva |
| `caixa.pagar_fiado` | `fiado_quitar_venda` | reconhece, reserva |
| `caixa.registrar_doacao` | `doacao_avulsa` | reconhece, reserva |
| `caixa.salvar_sangria` | `sangria` | reconhece, reserva |
| `compras.salvar_compra` | `compra` | reconhece, reserva |
| `financeiro.receber_mensalidades` | `mensalidade_receber` | reconhece, reserva |
| `financeiro.salvar_nova` | `lancamento_novo` | reserva |
| `financeiro.salvar_transferencia` | `transferencia` | reserva |

Duas rotas só reservam (`salvar_nova`, `salvar_transferencia`): não têm caminho
alternativo de reconhecimento, e a conferência acontece no `reservar`.

**Defeito encontrado ao montar a matriz** (e corrigido): duas rotas de `caixa.py`
ficaram com a operação trocada na primeira passada — `pagar_fiado` recebia
`credito_anticipado`. Como o reconhecimento passou a comparar a operação, isso
fez três asserções antigas falharem (`quitar venda de fiado`): o próprio
endurecimento detectou o mapeamento errado. Hoje a verificação é programática
(cada rota reconhece a MESMA operação que reserva).

### Provas DEPOIS da correção

`evidencias/TASK_014/aud01_depois_da_correcao.txt` — suíte
`test_financeiro_idempotencia_task012.py`, **182/182**:

| Prova | Resultado |
|---|---|
| A conclui e a tentativa fica com o autor dele | ok |
| B, com a mesma chave e o mesmo corpo, é recusado por contexto — sem destino e sem documento alheio | ok |
| Com destino específico (transferência), o segundo operador recebe a recusa e **não** o destino gravado do primeiro | ok |
| A recusa de B não cria efeito e não altera a tentativa do A | ok |
| A recusa não revela o operador original no corpo | ok |
| A continua podendo repetir a própria tentativa (mesmo resultado) | ok |
| Mesma chave em **outra operação**, mesmo operador: recusa sem escrita, corpo estruturalmente parecido | ok |
| Tentativa histórica com autor nulo: recusada, sem atribuir autoria retroativa e sem apagar identidade | ok |
| Concorrência (chave **nova**) entre dois contextos: um vencedor, o outro recusado | ok |
| Concorrência (chave **já concluída**): só o dono recebe o resultado; nada duplicado | ok |

Duas asserções foram endurecidas durante a prova, não afrouxadas: comparar o
`loc` com o destino do dono era fraco porque `/financeiro` é prefixo de
`/financeiro/nova` — o teste passou a usar uma operação com destino específico
(transferência) e a exigir a recusa pelo nome.

### Notas de implementação

1. **Registros históricos — e uma nota minha que precisou de correção.**

   A primeira versão desta nota dizia "o DEV tem zero tentativas de compra e as
   demais são desta e da execução anterior de testes". A segunda metade estava
   **errada e imprecisa**, e o dono pediu o esclarecimento. O que foi apurado,
   com o banco efetivamente identificado:

   | Verificação | Resultado |
   |---|---|
   | arquivo de configuração consultado | `.env` → `SHAMBALA_DB_NAME=shambala` |
   | banco **efetivo** da conexão | `current_database() = shambala`, `current_user = shambala` |
   | `operacao_idempotencia` no DEV | **0 linhas** |
   | resíduo de teste em `financeiro_movimentacoes` (prefixos `T012`/`T014`/`AUD01`/`T12`) | **0** |
   | atendentes de teste (`t014*`, `t12*`, `t010*`) | **0** |
   | trabalhadores, produtos e caixas de teste | **0**, **0**, **0** |

   Portanto: **não houve incidente** — não existem "registros de teste no DEV".
   O que ocorreu foi erro de redação meu: ao escrever sobre o DEV eu misturei, na
   mesma frase, o estado do **banco descartável** (`shambala_test_passes`, onde as
   suítes criam e apagam tentativas a cada execução) com o do DEV. Não houve
   escrita de teste no DEV, nenhum efeito a reparar e nada a apagar; e, por
   política, resíduo de teste no DEV **não** seria apagado automaticamente.

   A regra escolhida para contexto nulo — **recusa** — continua válida e não
   depende disso: ela não inventa autoria retroativa nem apaga identidade, e
   como o DEV não tem nenhuma tentativa, nada histórico fica inacessível.
2. **Sem schema novo.** Todos os campos usados (`operacao`, `autor_id`) já
   existiam desde a TASK_012; nenhuma migração de identidade.
3. **Efeito colateral esperado.** Uma tela que guarde a chave e seja usada por
   outro operador agora recebe `tentativa_contexto` com orientação de recarregar
   e refazer — comportamento desejado, e a mensagem está em
   `MENSAGENS_TENTATIVA`.
4. **Revisão de preço do PDV** preservada: a comparação de contexto acontece no
   reconhecimento/reserva, antes das validações de estado; o caminho de
   reprificação não mudou (a suíte de preços continua verde).

---

## 2. AUD-02 — evidência consolidada e falhas de limpeza

### Achado

`docs/tasks/evidencias/TASK_012/runner_completo_r5_FINAL_verde.txt` terminava com
as 22 suítes em `rc=0` **contendo** erro de FK no teardown
(`fiado_credito_mov_financeiro_id_fkey`). O resumo tratava como verde porque a
falha de limpeza não afetava o código de saída.

### O que foi feito

1. **Evidência histórica preservada e marcada, não apagada**: o log continua
   intacto, com um marcador ao lado —
   `runner_completo_r5_FINAL_verde.INVALIDO.md` — explicando por que não vale como
   sucesso integral e vinculando a substituição.
2. **Causa corrigida** (commit `9c78f19`): ordem das duas FKs `RESTRICT`,
   remoção por vínculo próprio, sentinela alheia e conferência de resíduo.
3. **Falha de limpeza agora derruba o RC — provado nos dois níveis**:

| Prova | Resultado |
|---|---|
| limpeza fora da ordem (dependente apagado depois do pai) | `RuntimeError` de resíduo próprio |
| limpeza que apaga registro alheio | `RuntimeError` da sentinela |
| **suíte com falha de limpeza injetada, pelo runner** | `[limpeza] ERRO: ...` e **rc do runner = 1** |

Evidências: `TASK_012/limpeza_reprova_quando_falha.txt` e
`TASK_014/aud02_falha_limpeza_rc_nao_zero.txt`.

### Regressão consolidada (posterior às correções)

`docs/tasks/evidencias/TASK_014/regressao_consolidada.txt` — runner integral,
destino provado, **23 suítes, todas `rc=0`, `DEV inalterado: ok`**, árvore
`4931317…` e token `t010-20261025130348-858861` no cabeçalho.

| Suíte (financeiras e novas) | Contagem |
|---|---|
| idempotência (TASK_012 §2, + AUD-01) | 182 asserções |
| mensalidades (§3, + conflito histórico) | 73 asserções |
| fiado/alocações (§4, + pagamento×fechamento) | 95 asserções |
| consultas (§5) | 79 asserções |
| navegador TASK_012 | 27 verificações |
| **morte real do processo (TASK_014 §3)** | 6 verificações |
| TASK_011 navegador / consultas | 42 / 51 |

As demais suítes fecham em `rc=0` (`test_tres_passes`, `test_plano_unico`,
`test_permissoes`, `test_agenda_medium`, `test_presenca_trabalhadores` 19/19,
`test_codigo_trabalhador` 18/18, `test_terminal_presenca`, `test_terminal_leitor`
24/24, `test_correcoes_presenca` 21/21, `test_gestao_correcoes_navegador` 11/11,
`test_pdv_precos_servidor` 33/33, `test_cancelamento_vendas` 25/25,
`test_sessao_eventos_caixa`, `test_caixa_navegador_task008`,
`teste_negativo_isolamento`).

**Uma nota de honestidade sobre a primeira tentativa**: a primeira corrida teve
`test_presenca_trabalhadores` em `rc=1` com
`AmbienteInseguro: permissão negada para terminar o processo` ao apagar o banco
`shambala_presenca_mig_test` — resíduo de conexão de execução anterior, não do
código desta tarefa. A suíte isolada fechou **19/19** e a corrida integral
seguinte fechou em **rc=0**. Ficou registrado em vez de omitido.

**Separação por nível de prova** (exigência explícita desta ordem):

| Nível | O que exercita | Onde |
|---|---|---|
| função auxiliar | `reconhecer`/`reservar`/`recuperar_*` chamados direto | várias provas das suítes TASK_012 |
| HTTP com servidor vivo | rotas reais, app do runner; a morte é **simulada por exceção** | suíte de idempotência |
| **processo real** | app próprio + **SIGKILL** na janela + startup de verdade | `test_financeiro_morte_processo_task014.py` |

## 3. Provas pendentes da TASK_013 — reproduzidas

### 3.1 Mensalidade sem índice, com conflito histórico

`TASK_014/s3_mensalidade_conflito_historico.txt` (suíte 73/73). O cenário é o
real: o conflito existe, o banco **recusa** criar o índice (a exceção é a prova),
e nesse estado duas gerações simultâneas de uma competência nova produzem **uma**
obrigação — serviço × caminho alternativo, com barreira. As linhas em conflito
seguem as duas: nenhuma vencedora escolhida.

Descoberta no caminho: o índice parcial **não filtra status**
(`categoria='mensalidade' AND trabalhador_id IS NOT NULL AND competencia IS NOT
NULL`), então cancelar uma linha não a retira do índice. O encerramento da prova
passou a remover as linhas *fixture* dela (não fato real).

### 3.2 Pagamento, cancelamento e fechamento

`TASK_014/s3_pagamento_cancelamento_fechamento.txt` (suíte 95/95):

- **pagamento × fechamento**: sessão presa numa transação e recebimento esperando
  no PostgreSQL (espera em `pg_stat_activity`, sem `sleep` como prova). Depois do
  fechamento: sessão fechada e **nenhum evento registrado depois de
  `data_fechamento`** — a asserção mede delta e compara com a data de fechamento,
  porque a sessão é compartilhada pelas provas;
- **cancelamento de dívida parcial**: continua **bloqueado** e sem efeito
  (TASK_007);
- **cancelamento elegível**: aceito, sai da dívida em aberto e registra o evento.

### 3.3 Interrupções FIN-09 — níveis separados

`TASK_014/s3_fin09_morte_real_suite.txt` (6/6). Um processo do app é morto com
**SIGKILL** dentro da janela (entre commit e promoção):

1. a requisição estava congelada na janela (sentinela escrita);
2. o estado deixado é o previsto — compra `pago`, arquivo **ausente**,
   temporário no disco, tentativa `concluida` apontando para o documento;
3. o startup seguinte, processo real e **sem injeção**, cancela, anula a chave e
   remove o temporário — sem chamada manual;
4. reenviar a mesma chave não informa sucesso e não duplica.

A trava por compra **não fica presa**: ela é de sessão, solta explicitamente antes
do `putconn` e morre com o processo (é o que permite a recuperação). Compras
históricas, arquivo presente, erro de acesso e caso fora de 24 h **não** são
cancelados por varredura genérica — provado na suíte de idempotência.

## 4. AUD-03 — limite operacional do FIN-09

**Não há promessa de atomicidade.** O que existe, com precisão:

| Estado | O que o sistema faz |
|---|---|
| falha antes do commit | nada é gravado; nenhum arquivo definitivo aparece |
| morte/demora entre commit e promoção | a compra fica `pago` sem arquivo; a trava morre com o processo e o próximo startup recupera |
| promoção falha depois do commit | compensação cancela a compra e anula a chave na mesma transação |
| compensação falha | o cancelamento vale sozinho; a tentativa fica consumida com o documento e o startup fecha o resto |
| recuperação sem prova (arquivo ausente sem temporário) | **preserva** — pode ser volume não montado |
| interrupção com mais de 24 h | **preserva** e informa no log (não é recuperação automática) |

**Aviso ao operador** (§4): `GET /financeiro/compras/pendencias`, somente leitura,
restrito à permissão do módulo, sem botão de reparo nem SQL de cancelamento.
Distingue interrompida / fora do prazo / sem acesso ao armazenamento / sem prova,
com link para o lançamento e a chave da tentativa, e **não** orienta refazer a
compra em nenhum caso. `TASK_014/s4_aviso_pendencias.txt`.

**Procedimento manual**: anotar compra e arquivo esperado → conferir a situação
descrita → preservar evidências (não apagar `*.tmp-…` nem o lançamento) → se for
*interrompida*, aguardar o próximo início → nos demais casos, encaminhar para
correção assistida.

## 5. DEV

- backup novo em `docs/tasks/backups/shambala_pre_task014_dev_20261025131011.sql.gz`
  com `.sha256` conferido (`sha256sum -c` SUCESSO). **O backup original
  pré-TASK_012 foi preservado** e continua sendo o único que desfaz aquela
  migração;
- baseline próprio antes de qualquer startup que pudesse escrever;
- ao reiniciar, `recuperar_compras_sem_nota` **não agiu**: o DEV tem **0**
  tentativas de compra e as 12 notas estão em disco. O log de subida
  (`dev-startup-task014.log`) não traz linha de recuperação;
- comparação antes × depois (`TASK_014/dev_comparacao.txt`): **idêntico, 0 linhas
  de diferença** nos dois retratos; `compra: 12 | 528.64` igual. Alcance do hash
  `fm_max_id_hash`: `id:tipo:status:categoria:valor:data_movimentacao` de todas as
  linhas do livro-caixa — **não** cobre `descricao`, `competencia`, `conta_id`,
  `trabalhador_id`, `data_pagamento` nem `nota_fiscal_foto`; a igualdade integral
  de colunas **não** é afirmada a partir dele;
- endpoints: `/login` 200, `/financeiro` 303, `/financeiro/compras` 303,
  `/financeiro/compras/pendencias` 303 (login);
- nenhuma operação de demonstração no DEV; produção não acessada; sem push.

## 6. Parecer por fluxo

| Fluxo | Parecer |
|---|---|
| Idempotência (AUD-01) | **apto para homologação** — corrigido nos 11 caminhos, provado com dois operadores, outra operação, autor nulo e concorrência |
| Mensalidades por competência | **apto**, inclusive no estado sem índice por conflito histórico |
| Fiado / alocações / crédito | **apto** — inclusive pagamento × fechamento e cancelamento elegível × dívida |
| Consultas e apresentação | **apto** |
| Compra com nota (FIN-09) | **apto com ressalva** — protocolo mitigado com limite conhecido (janela banco/arquivo, recuperação em 24 h e conferência manual fora do prazo); **não** há atomicidade |
| Legado (43 recebimentos sem trabalhador), reembolso, conciliação, vendas/sangrias legadas | **fora do escopo** — não homologar |

Nenhum defeito novo encontrado nesta tarefa além dos registrados.

## 7. Roteiro curto para o dono

**Somente leitura (no DEV, sem escrever nada):**

1. Financeiro → **Visão Geral**: valores com ponto de milhar e Σ por conta.
2. Financeiro → **Fiados** → um trabalhador: *Comprado a fiado · Já recebido ·
   Saldo devedor*.
3. Financeiro → **Relatório de mensalidades**: trocar "Qual data o mês usa".
4. Financeiro → **Compras sem a nota em disco** (`/financeiro/compras/pendencias`):
   deve dizer que está tudo em ordem — é o diagnóstico do FIN-09.

**Com dados fictícios exclusivos (crie um trabalhador "Teste TASK_014"):**

5. Compra a fiado de R$ 40; receber R$ 15 escolhendo a dívida → saldo R$ 25,
   recebido R$ 15.
6. Receber mais R$ 50 na mesma dívida → dívida quitada e **R$ 25 de crédito**
   (50 − 25), não R$ 10; "Recebimentos" sobe R$ 50.
7. Detalhe do lançamento → "De onde vem este lançamento" e "Para onde foi este
   dinheiro".
8. Duplo clique em "Receber": uma única operação.
9. **Encerramento**: **inative** o cadastro "Teste TASK_014" e **preserve os
   fatos** — não exclua lançamentos, venda, crédito nem recebimento.
