# TASK_009 — Revisão técnica independente da TASK_008

**Parecer: requer correções.** Esta revisão não homologa o financeiro nem
autoriza publicação. Estado revisado: `HEAD` com TASK_008 (`785e228`) e R1
`5c35d47`, R2 `cc94a08`, R3 `38c8dba`.

## Escopo, método e limites

Foram lidos a ordem TASK_009, TASK_005–008, `banco.py`, `servico_sessao.py`,
`servico_cancelamento.py`, rotas de caixa/financeiro, runner, trava e suítes.
Não havia `AGENTS.md`. Não acessei produção nem DEV para escrita, não iniciei
o app contra DEV e não alterei produto/schema. O único app temporário foi
encerrado; porta 9019 ficou livre.

O runner foi executado com `SHAMBALA_TEST_DB=shambala_t009_test` e porta 9019.
Ele recriou esse banco, mas o startup confirmou explicitamente o banco
**`shambala_test_passes`**, vindo de `.env.test`. Portanto a prova de ambiente
do runner não corresponde ao banco que ele anuncia/cria. A execução foi
interrompida após a divergência e os resultados subsequentes não são aceitos
como evidência da TASK_008.

## Matriz requisito → código → prova

| Requisito | Código | Resultado |
|---|---|---|
| sessão por eventos; efeito físico separado | `servico_sessao.py:209-384`, `caixa_sessao_eventos` | desenho e SQL usam identidade única e `NUMERIC`; cobertura alegada não foi revalidada pelo runner defeituoso |
| recebimento novo em sessão legada recusado | `sessao_para_recebimento` `:264-294`; `contabilizar_recebimento` `:229-260`; R1 | inspeção positiva: recusa ocorre antes do INSERT nesses caminhos |
| PIX sem gaveta | `sessao_para_recebimento` permite `None` para PIX; contas validadas em `:114-153` | comportamento aderente; a diferença com PDV exige teste de rota separado |
| fechamento por eventos | `fechar_sessao_eventos` `:386-457` | fórmula usa eventos e evita lançar fiado; pendente de reexecução confiável |
| uma sessão aberta | `abrir_sessao` `:186-207`, índice parcial em `banco.py` | lock advisory + checagem são positivos; migração tolera duplicidade legada sem constraint, exigindo cobertura de todas as rotas |
| isolamento antes de DDL | `main.py:28-35`, `tests/trava_isolamento.py` | lifecycle valida conexão antes de `criar_tabelas`; launcher/runner falham na consistência de destino (REV-01) |

## Achados reproduzidos ou demonstrados

### REV-01 — crítico: runner anuncia um banco e executa a aplicação em outro

`tests/rodar_testes.sh:20-22` aceita `SHAMBALA_TEST_DB` para DROP/CREATE,
mas inicia o app com `SHAMBALA_ENV_FILE=.env.test` (`:87-90`). `main.py` lê
esse arquivo, cujo `SHAMBALA_DB_NAME=shambala_test_passes`, substituindo o
valor usado pelo runner. Na reprodução, o runner disse criar
`shambala_t009_test`, e o log do app disse
`Ambiente descartável confirmado: {'banco': 'shambala_test_passes', ...}`.

Impacto: as suítes podem consumir estado residual de outro banco e a limpeza/
criação não é a do app sob teste; RC e contagens não comprovam o commit. A
proteção de que ambos têm `_test` reduz risco de DEV, mas não satisfaz a
identidade efetiva exigida pela própria TASK_009. Correção mínima: gerar env
temporário coerente com o banco solicitado, validar `current_database()` antes
do startup e fazer cada suíte confrontar o mesmo destino; teste de regressão
deve usar dois nomes `_test` distintos e recusar a divergência.

### REV-02 — alto: abertura padrão de caixa recusa R$ 0,00

`rotas/caixa.py:1755-1781` chama `moeda(saldo_inicial or "0")`, mas
`servico_sessao.moeda` (`:86-100`) rejeita `d <= 0`. A rota/tela tem padrão
`saldo_inicial="0"`; portanto abertura normal sem fundo de troco retorna
`valor_invalido`. Isso contraria o contrato da TASK_008 de contado zero ou
positivo e restringe operação legítima. Correção mínima: parser específico
para valores não negativos no saldo inicial/contagem, mantendo valores de
eventos estritamente positivos; cobrir 0, 0,001, negativo e máximo.

### REV-03 — médio: launcher declara confirmação de conexão que não faz

`tests/subir_app_descartavel.sh:39-47` extrai só o nome do arquivo e chama
`exigir_ambiente_de_teste()` sem conexão; não carrega host/usuário/senha do
env. A função só confronta `current_database()` se recebe conexão
(`tests/trava_isolamento.py:60-88`). O lifecycle posteriormente protege o
startup, mas a checagem prévia do launcher não é a “segunda camada” descrita
e pode aprovar um env cujo destino efetivo é diverso. Correção: usar
`exigir_arquivo_de_configuracao`, importar `banco` só após isso e passar uma
conexão efetiva; acrescentar sentinela com nome `_test` e host/DB divergente.

## Resultados positivos e riscos não comprovados

R1 melhorou corretamente a recusa de recebimentos em legado, e a transação do
context manager dá rollback quando a exceção sai do bloco. Eventos de
recebimento têm identidade única; contudo identidade por documento não é
idempotência de um POST de venda novo, pois dois POSTs podem criar duas vendas
e duas identidades distintas. Isso é risco por inspeção, não defeito
reproduzido nesta revisão.

Sangria cria saída e entrada ligadas por `sangria_id`, mas não há constraint
que imponha exatamente o par. Fechamento seleciona eventos sem
`financeiro_id`, cria lançamentos e só então vincula; a atomicidade depende do
contexto e parece adequada, porém concorrência fechamento×venda/cancelamento
não foi reexecutada por causa de REV-01.

## Testes e evidências

O runner iniciou 14 suítes, mas foi interrompido depois de falhas em suítes
não financeiras; essas falhas não são atribuídas à TASK_008 porque a aplicação
usava `shambala_test_passes` enquanto o runner preparava outro banco. A antiga
`test_financeiro_integracao.py` não foi aceita como prova: permanece
incompatível com o schema/contratos conforme TASK_005/008.

Para aceitar correções: (1) teste do runner com destinos distintos; (2) teste
de abertura R$0; (3) reexecutar `test_sessao_eventos_caixa.py` e navegador em
um único DB comprovado; (4) reexecutar concorrências e rollback da última
escrita; (5) registrar RC e pulados reais, não a soma declarada.

## Próxima intervenção

A menor tarefa é corrigir primeiro **REV-01**, pois sem ambiente determinístico
não é possível confiar em qualquer regressão financeira. Em seguida corrigir
REV-02 e somente então reexecutar a revisão de fluxo/concorrrência. Não foram
implementadas correções nesta TASK_009.

---

## Adendo de revalidação (TASK_010) — sem reescrever o parecer acima

Em 2026-10-24 a TASK_010 executou exatamente a rota sugerida. O parecer
histórico ("requer correções") permanece íntegro acima. Estado atual, com
evidências em `docs/tasks/evidencias/TASK_010/` e detalhe em
`docs/investigacoes/TASK_010_REVALIDACAO_FINANCEIRO_TASK_008.md`:

- REV-01, REV-02 e REV-03: **corrigidos e provados** — inclusive prova negativa
  por sentinelas A/B de ponta a ponta (runner e launcher reais) e prova
  antes/depois por mutação em cópia isolada (reintroduzir a escolha fixa de
  `.env.test` volta a fazer a prova falhar, como este documento exigia).
- Os cinco critérios de aceitação listados acima foram cumpridos: (1) destinos
  distintos confrontados; (2) abertura R$ 0; (3) `test_sessao_eventos_caixa.py`
  (161/0 no runner final) e navegador TASK_008 (41/0) reexecutados num único
  banco efetivo comprovado por token da execução corrente; (4) concorrências —
  incluindo a prova nomeada de **fechamento × venda × recebimento ×
  cancelamento** no mesmo `FOR UPDATE` (CASO 8b) e o reenvio da mesma tentativa
  (CASO 16) — e rollback da última escrita reexecutados; (5) RC=0 e contagens
  reais por suíte registrados na evidência canônica commitável
  `docs/tasks/evidencias/TASK_010/runner_completo_final.txt` (árvore `d12dbfc`,
  token `t010-20261024212542-1294779`), sem perseguir a soma histórica.
- Divergência PIX sem sessão (backend × PDV) resolvida no interface; espécie
  permanece condicionada à sessão.
- Riscos não bloqueadores documentados: ausência de constraint do par de
  sangria; inexistência de idempotência HTTP no POST do PDV (comportamento, não
  proteção). `test_financeiro_integracao.py` classificado como harness
  incompatível (depende de `auditoria_eventos`, fora do schema atual).

Conclusão: nenhum novo bloqueador financeiro apareceu na revalidação; a
TASK_008 está tecnicamente aprovada, restando a homologação manual do dono
(roteiro com dados fictícios no documento da TASK_010).
