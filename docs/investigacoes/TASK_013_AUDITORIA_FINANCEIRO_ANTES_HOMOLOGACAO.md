# TASK_013 — Auditoria financeira antes da homologação

**Parecer: requer correções antes da homologação manual.**

## Escopo e limite

Auditei `main` no commit `f7ef01617094749278365bba3d13c71c3b634981`. A árvore
contém um log TASK_012 modificado e arquivos não rastreados alheios, inclusive
a ordem desta task. Não alterei produto, schema, DEV ou produção; produção não
foi acessada. Foram lidos TASK_005–012, serviços, rotas, migrations, runner,
launcher, fixtures e logs. Nenhum processo temporário foi iniciado.

O runner integral não foi aceito como prova suficiente: a auditoria encontrou
falha de isolamento de ator e contradição em evidência de limpeza. Nova execução
deve usar o runner TASK_010 em banco nativo selado e token da execução.

## Matriz

| Contrato | Código | Conclusão |
|---|---|---|
| Teste isolado | `tests/rodar_testes.sh`, `subir_app_descartavel.sh`, `trava_isolamento.py`, `main.py` | env autoritativo e `current_database()` antes de DDL existem; reexecução independente pendente |
| Idempotência | `servico_idempotencia.py:79-226`; rotas financeiras | falha de isolamento por autor/operação (AUD-01) |
| Mensalidade/competência | `banco.py:434-524`, `financeiro_mensalidades.py` | índice e `ON CONFLICT` existem; falta prova independente quando índice está ausente por legado |
| Sessão/eventos | `servico_sessao.py`, `rotas/caixa.py` | modelo separa físico/PIX; corrida e navegador precisam revalidação |
| FIN-09 | `rotas/compras.py:95-466,489-652`, `main.py:48-72` | compensação/recuperação existem; banco-disco permanece não atômico e limitado |

## Achados

### AUD-01 — alto: tentativa não é isolada por operador nem operação

`operacao_idempotencia` grava `autor_id`, mas `reconhecer()` busca apenas
`WHERE chave = %s` (`servico_idempotencia.py:188-205`); `reservar()` também
recupera apenas a chave (`:89-110`). `exigir_reserva()` só passa o autor ao
INSERT (`:207-226`). Logo outro atendente autenticado que obtenha/reutilize a
mesma chave e corpo recebe o destino/documento já persistidos, sem comparação
de autor ou operação.

Impacto: a chave vira capacidade global de observar/repetir resultado; entropia
não corrige reutilização por tela, log, suporte ou dispositivo compartilhado.
Correção mínima: comparar `autor_id` e `operacao` antes de devolver
destino/documento, recusando sem vazamento. Aceite: dois operadores, mesma
chave/corpo; o segundo não obtém resultado nem cria efeito; o primeiro continua
convergente e chave reutilizada em outra operação recusa sem escrita.

### AUD-02 — médio: log “final verde” contém erro de limpeza

`docs/tasks/evidencias/TASK_012/runner_completo_r5_FINAL_verde.txt` contém erro
de FK na limpeza (`fiado_credito_mov_financeiro_id_fkey`, linhas 1116+), embora
o resumo o trate como verde. Há corrida posterior com sentinela e limpeza
(`runner_completo_etapa5_6_r2_final.txt`), mas ela não torna R5 prova válida.

Aceite: corrida integral posterior ao conserto, RC não-zero para exceção de
teardown, token/commit, sentinela alheia preservada e resíduo próprio zero.

### AUD-03 — médio: FIN-09 é contingência, não atomicidade

O banco é confirmado antes de `_promover_nota` (`rotas/compras.py:602-644`);
se a promoção falha há compensação, e o startup recupera somente tentativa
recentemente interrompida. Morte entre commit e promoção deixa temporariamente
compra paga sem arquivo; fora de 24h ou sem prova de interrupção a recuperação
preserva a linha (`main.py:53-72`).

Não apresentar “nota obrigatória garantida”. Para homologar com ressalva, criar
alerta/procedimento manual para fora do prazo; garantia plena exige outbox ou
arquitetura posterior.

## Pendências e decisão

Reembolso de fiado, conciliação bancária, saldo bancário conferido,
vendas/sangrias legadas e 43 recebimentos históricos sem trabalhador continuam
fora do escopo. Concorrência sem índice de competência, pagamento×cancelamento×
fechamento e morte FIN-09 nas janelas não foram reproduzidos nesta auditoria.

Não liberar fluxos de escrita idempotente até AUD-01 ser corrigido e testado.
Depois, homologar apenas com dados fictícios exclusivos e ressalva explícita
para FIN-09; não homologar conciliação, reembolso ou conversão de histórico.
