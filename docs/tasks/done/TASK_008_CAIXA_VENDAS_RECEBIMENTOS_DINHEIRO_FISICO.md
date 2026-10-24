# TASK_008 — Separar vendas, recebimentos e dinheiro físico no caixa

Status: implementada, testada e aplicada no DEV (schema aditivo idempotente com backup recuperável e restauração validada; commits locais, sem push). Aguarda conferência do dono pelo roteiro de homologação com dados fictícios. Produção não acessada.
Referências: TASK_005, FIN-04; TASK_006 (precificação); TASK_007 (cancelamento rastreável, commits informados 96e322d e 30a532a). Conferir estado real antes de alterar.

## Objetivo

Eliminar a mistura de mensalidades, doações e recebimentos de fiado com vendas da Lanchonete e impedir que PIX, fiado não recebido e consumo de crédito sejam tratados como dinheiro físico na sessão. Manter vínculo entre operação, recebimento, conta financeira e sessão responsável, evitando dupla receita no fechamento.

Não reescrever o financeiro inteiro. Esta etapa resolve a classificação e o fechamento das novas sessões; mensalidades com competência/unicidade, alocação detalhada de fiado, reembolso pós-fechamento e conciliação permanecem para tarefas posteriores.

## Ambiente e preparação

1. Ler AGENTS.md, se existir, e normas locais. Registrar TASK_008 em docs/tasks/. Preservar alterações alheias.
2. DEV existente somente, sem outra instância persistente, infraestrutura, push ou publicação. Não acessar produção, nem por leitura.
3. Confirmar destino efetivo de scripts e servidor HTTP antes de qualquer escrita de teste. Configuração ausente, incompatível ou banco não descartável deve abortar antes de seed/startup. Não mudar credenciais, usuários, permissões ou sequences do DEV para testes.
4. Testes em PostgreSQL nativo descartável, sem Docker; processos temporários somente durante execução, encerrados ao final.
5. Antes de aplicar schema no DEV, backup recuperável com restauração validada e baseline próprio dos fatos, saldos, sessões, códigos, credenciais e permissões relevantes. Não usar produção como substituto de baseline.

## Reconstrução focada

Mapear todos os escritores de total_vendas, total_sangrias, saldo_final e financeiro_movimentacoes, incluindo venda, doação junto da venda, doação avulsa, mensalidade individual/lote, fiado, crédito antecipado, sangria, cancelamento e fechamento.

Reproduzir o fechamento misto antigo com dados fictícios e demonstrar a divergência antes da correção. Inventariar valores/contas e o comportamento do caixa aberto/fechado. Não presumir que caixa_id=2 representa sempre Lanchonete ou que uma conta chamada PIX corresponde a qualquer recebimento.

Entregar no relatório o mapa operação → fato → forma → conta → sessão → efeito físico → lançamento financeiro. Usá-lo como contrato dos testes.

## Regras aprovadas para novas operações/sessões

| Operação | Venda comercial | Recebimento novo | Dinheiro físico da sessão |
|---|---|---|---|
| Venda em espécie | Sim | Valor da venda recebido em espécie | Soma valor líquido retido |
| Venda por PIX | Sim | Valor recebido na conta indicada | Não soma |
| Venda fiado sem crédito | Sim | Zero no ato | Não soma |
| Uso de crédito anterior | Sim, pelo valor do produto | Não é novo recebimento | Não soma |
| Recebimento de mensalidade | Não | Valor efetivamente recebido | Soma só se espécie entregue nessa sessão |
| Doação avulsa ou junto da venda | Não é venda de produto | Valor da doação recebido | Soma só se espécie recebida nessa sessão |
| Quitação de fiado | Não é nova venda | Valor efetivamente recebido | Soma só se espécie recebida nessa sessão |
| Crédito antecipado | Não é venda | Valor efetivamente recebido | Soma só se espécie recebida nessa sessão |
| Sangria física | Não | Saída da gaveta | Subtrai uma vez |

Separar recebimentos comerciais e não comerciais por natureza e forma. PIX deve aparecer no resumo da sessão como informação de recebimento bancário quando operado nela, mas não no saldo físico. Saldo inicial é dinheiro já existente, não receita nova. Não adicionar cartões, taxas ou pagamento combinado inexistentes.

Espécie recebido com troco: efeito físico líquido é recebido menos troco efetivamente devolvido. Se parte do troco for doada, separar venda e doação sem contar a mesma quantia duas vezes. Validar que a doação de troco não excede a sobra disponível; tratar doação independente somente conforme o fluxo existente.

Saldo físico esperado = saldo inicial + entradas físicas válidas − saídas físicas válidas. PIX e obrigações pendentes ficam fora. Não utilizar total_vendas como substituto de entrada física.

## Sessão e destino do recebimento

- Remover escolha implícita da sessão da Lanchonete por ID fixo para mensalidade/doação/fiado.
- Espécie exige sessão aberta explicitamente identificada e conta financeira coerente. Se houver única opção elegível, pode sugerir e mostrar qual; mais de uma exige seleção. Não inventar sessão, não escolher silenciosamente a primeira e não receber espécie sem registrar onde ficou o dinheiro.
- PIX exige conta financeira válida de destino. Pode vincular à sessão operadora quando houver, sem obrigar abertura de gaveta para recebimento exclusivamente bancário.
- Se faltar destino válido, recusar antes de baixar obrigação, emitir venda ou mexer em crédito. Preservar o formulário com orientação clara.
- Validar forma/conta/sessão no servidor. Não confiar em IDs ou tipo da conta enviados pelo navegador; impedir sessão fechada/alheia ao caixa informado.
- Impedir múltiplas sessões abertas do mesmo caixa sob concorrência. Tratar duplicidades legadas sem fechar ou mesclar automaticamente.
- Preservar RBAC existente e conferir as rotas diretamente; terminal de presença continua sem acesso financeiro.

## Modelo mínimo e fonte de verdade

Introduzir registro detalhado de eventos de recebimento/saída da sessão, ou estrutura equivalente compatível com o projeto, evitando acumuladores independentes sem lastro. Deve permitir:

- identificar origem por tipo e ID persistido, forma, valor, conta, sessão quando aplicável, operador e instante do servidor;
- distinguir venda, doação, mensalidade, quitação de fiado, crédito antecipado e sangria;
- vincular lançamento financeiro quando já existir e impedir reaplicação do mesmo efeito com unicidade/identidade de evento;
- associar compensações ao evento original, sem apagá-lo;
- obter resumos pela mesma regra usada no fechamento e no comprovante.

Não manter duas fontes concorrentes de totais. Se acumuladores antigos continuarem por compatibilidade, documentar sua semântica e atualizá-los apenas pelo serviço central, com testes de igualdade ao detalhamento. Não reutilizar valores legados contaminados como saldo novo.

Usar Decimal/NUMERIC em todo caminho monetário alterado, limites de duas casas e regra explícita de arredondamento. Datas/instantes novos devem ter tipos adequados e fuso America/Sao_Paulo na apresentação; não converter todas as datas antigas nesta entrega.

## Integração com financeiro sem duplicidade

- Mensalidade/doação/recebimento de fiado que já geram fato financeiro devem vinculá-lo ao evento; o fechamento não cria outra receita para eles.
- Venda ainda não lançada financeiramente pode manter realização no fechamento, desde que haja vínculo persistido e geração única por origem/conta/forma. Não agregar de modo que fiado não recebido ou crédito consumido vire novo dinheiro.
- Na venda com crédito antecipado, consumo é destinação de valor recebido antes, não entrada nova no fechamento. Parte não coberta continua obrigação de fiado, não recebimento fictício.
- Crédito antecipado novo precisa passar a registrar a entrada de dinheiro e sua conta, com vínculo ao extrato de crédito e ao evento de recebimento. Isso cobre a omissão de entrada do FIN-03 no fluxo novo, sem prometer resolver toda a alocação de crédito/fiado.
- Não retroagir criando entradas para saldos de crédito antigos sem origem comprovada. Preservar o saldo e marcar a limitação histórica.
- Não remodelar pagamento parcial de fiado nesta etapa, mas contabilizar uma única vez o valor realmente recebido pelo fluxo atual, mesmo quando sua destinação vira crédito. Não lançar separadamente como recebimento e novamente como crédito se for a mesma entrega de dinheiro.
- A baixa da obrigação/registro de crédito, o evento e o lançamento financeiro correspondente devem ser atômicos; idempotência de recebimento deve impedir repetição do mesmo comando/documento. Incluir unicidade no banco, não só bloqueio do botão.
- Não transformar sangria automaticamente em despesa da casa. Conferir a semântica atual: se for transferência para outra conta da casa, registrar como transferência vinculada de efeito global zero. Se o fluxo atual não identifica destino/finalidade suficiente, exigir essa identificação antes de confirmar; não criar conta fictícia ou saída sem destino por padrão.

## Fechamento de sessão

Mostrar antes de confirmar:

- saldo inicial;
- vendas em espécie, PIX, fiado e crédito consumido, separadamente;
- doações, mensalidades, quitações e adiantamentos recebidos, por forma;
- entradas/saídas físicas e saldo físico esperado;
- campo para dinheiro físico contado e diferença contado − esperado.

Diferença deve ficar registrada como divergência de conferência com observação obrigatória quando não zero. Não alterar vendas, fabricar receita/despesa nem sobrescrever o esperado para fazer coincidir com o contado.

Fechar em transação com trava da sessão e ordem de travas compatível com TASK_006/007. Recebimento/venda/cancelamento concorrente deve ocorrer integralmente antes do fechamento ou ser recusado depois, sem efeito tardio em sessão fechada. Fechamento repetido não duplica movimentos financeiros.

Persistir resumo/versionamento suficiente para que a impressão e consulta de sessão fechada continuem exibindo o fechamento original. Não reabrir ou recalcular sessões fechadas históricas.

## Compatibilidade com TASK_007

Adaptar o serviço de cancelamento para compensar somente eventos comprovadamente vinculados à venda, preservando estoque, idempotência, auditoria e bloqueios de fiado pago/crédito/sessão fechada. Não subtrair total+doação de um acumulador cuja semântica mudou sem compensar o detalhamento correspondente.

Se venda cancelada ainda não tinha lançamento financeiro, não criar saída artificial. Se já houver lançamento financeiro incompatível com os casos seguros da TASK_007, bloquear com explicação; não implementar reembolso novo nesta etapa.

## Transição e histórico

- Versão nova somente para sessões abertas após a implantação desta mudança. Distinguir sessões legadas explicitamente no schema/interface.
- Preservar sessões fechadas, vendas, recebimentos e comprovantes antigos. Sem backfill por heurística de valor/data/nome.
- Não converter automaticamente sessão legada aberta nem exigir que o usuário feche sessão com cálculo novo sobre dados antigos. Documentar as opções e o impedimento concreto para a transição; a efetivação em produção exige planejamento posterior e autorização própria.
- Sessões legadas permanecem consultáveis. Se mantiver fluxo antigo de fechamento para concluir legado, isolá-lo por versão e avisar que usa a regra anterior; não afirmar que FIN-04 foi corrigido retroativamente.
- Novos eventos não podem contaminar acumuladores legados por fallback. Destino incompatível bloqueia claramente antes de qualquer efeito.
- Migração idempotente/transacional para banco novo/existente, sem preencher valores desconhecidos com zero como se fossem fatos conferidos. Constraints novas não podem quebrar startup por duplicidades antigas sem diagnóstico prévio.

## Testes obrigatórios

Construir cenário misto determinístico com saldo inicial R$100:

- venda espécie R$20;
- venda PIX R$30;
- venda fiado R$40 sem crédito;
- mensalidade espécie R$50;
- mensalidade PIX R$60;
- doação espécie R$10;
- doação PIX R$15;
- quitação de dívida anterior espécie R$8;
- crédito antecipado espécie R$25;
- venda R$12 consumindo crédito anterior;
- sangria física R$30 com destino identificado.

Resultado esperado: vendas comerciais R$102; recebimentos novos espécie R$113; PIX R$105; saldo físico esperado R$183. Crédito consumido não entra novamente e fiado de R$40 não recebido não entra como dinheiro. Conferir dívidas/créditos separadamente conforme as fixtures, sem usar números do DEV.

Além desse cenário, cobrir:

1. Doar troco: R$50 entregues, venda R$20 e doação R$5 → devolução R$25 e entrada física líquida R$25.
2. Cancelamento elegível e repetição → compensação única nos eventos, estoque e resumo.
3. Fechar duas vezes/concorrentemente → nenhum lançamento financeiro duplicado.
4. Mensalidade/doação/fiado já lançados → fechamento não duplica receita.
5. Pagamento parcial que gera crédito → uma entrega de dinheiro, uma entrada.
6. Repetição de recebimento/adiantamento e falha na última escrita → idempotência/rollback.
7. Sessão fechada/errada, conta incompatível, forma inválida e parâmetros adulterados → recusa sem escrita.
8. Vendas/recebimentos/cancelamento × fechamento concorrente → resultado consistente, sem deadlocks não tratados.
9. Contado diferente do esperado → divergência registrada sem ajuste financeiro fictício.
10. PIX sem sessão física → conta correta, sem saldo físico afetado.
11. Abrir sessão em concorrência → uma aberta por caixa na versão nova.
12. Sessões legadas abertas/fechadas e migração repetida → preservação e limites de transição explícitos.
13. Transferência de sangria interna → efeito global zero e par vinculado, sem despesa falsa.
14. Autorização por rota e sessão de terminal → restrições mantidas.

Testar pelas rotas reais e verificar linhas/vínculos/totais no banco antes/depois. Acrescentar suíte ao runner e preservar TASK_006/007. Informar soma correta de testes, aprovados/falhos/pulados e RC; ausências de ferramentas não contam como PASS. Suíte antiga financeira: executar separadamente quando pertinente e classificar as falhas reais, sem inventar tabelas para satisfazer fixtures.

Validar no navegador desktop e smartphone: seleção de destino, recebimentos separados, fechamento misto, diferença de contagem, consulta/impressão de fechado e bloqueio de legado. Evidências fictícias, sem dados sensíveis. Sem credenciais DEV, registrar limitação e não criar conta ou trocar senha para contornar.

## Disponibilização e entrega

Aplicar no DEV existente somente após backup e baseline, conferindo efeitos do startup e que o processo serve a árvore correta. Não criar operações financeiras reais para demonstração. Se transição de sessão aberta impedir aplicação segura, entregar código testado e relatar o bloqueio sem encerrar a sessão real por conta própria.

Entregar relatório nesta tarefa com:

- reprodução do FIN-04 e contrato operação/forma/conta/sessão;
- modelo de eventos, vínculos financeiros, unicidade e travas;
- semântica dos totais e contagem física;
- transição legada, casos bloqueados e ausência de reparação retroativa;
- evidências do cenário misto, testes e preservação do DEV;
- roteiro curto com dados fictícios identificados;
- achados resolvidos parcialmente/integralmente e os ainda abertos, sem declarar todo o financeiro homologado.

Atualizar SHAMBALA_CONTEXTO.md e status real. Commit local somente dos arquivos da tarefa, sem push. Mover para docs/tasks/done/ ao atender os critérios; encerrar recursos descartáveis. Produção não acessada; publicação e próxima etapa dependem de autorização posterior.

---

## Adendo de design (T8.3) — contrato, modelo e travas

### Mapa da realidade antes da entrega (T8.1)

Escritores encontrados de `total_vendas` / `total_sangrias` / `saldo_final` / `financeiro_movimentacoes` e o que cada um fazia:

| Operação | Fato antigo | Efeito na sessão | Lançamento financeiro |
|---|---|---|---|
| Venda PDV espécie/PIX/fiado | `vendas_pdv` | `total_vendas += total + doação` (TODAS as formas, inclusive fiado não recebido) | nada na finalização; no fechamento uma entrada por forma agrupada — inclusive `fiado → conta 'a receber'` contada no saldo |
| Doação junto da venda | `vendas_pdv.doacao_valor` | soma em `total_vendas` misturada à venda | entra no agrupado do fechamento |
| Doação avulsa `/caixa/doacao` | `financeiro_movimentacoes (doacao)` | `total_vendas += valor` da sessão **escolhida por ID fixo `caixa_id=2`** | já lançada na operação |
| Mensalidade (lote `/financeiro/.../receber`) | update da pendente | `total_vendas += lote` na sessão implícita do caixa 2 | já lançada na operação |
| Quitação de fiado | `vendas_pdv.fiado_pago` + `recebimento_fiado` | `total_vendas += valor` (implícita) | já lançada (a venda também voltaria no agrupado se ainda concluída contada) |
| Crédito antecipado (adiantamento) | `fiado_credito_mov 'credito'` | **NENHUM** — o dinheiro físico entregue não era registrado em lugar algum (omissão FIN-03 no fluxo) | **nenhum lançamento** |
| Consumo de crédito em venda | `fiado_credito_usado` | valor volta a somar em `total_vendas` como se fosse dinheiro novo | — |
| Sangria | `caixa_sangrias` | `total_sangrias += valor` | só saída; destino da casa não recebia contrapartida |
| Cancelamento (TASK_007) | `vendas_pdv_cancelamento` | subtrai `total+doacao` do acumulador | — |
| Fechamento legado | — | `saldo_final = inicial + total_vendas − total_sangrias` e entradas por forma | agrupado `venda_pdv` por forma |

Consequência demonstrada (T8.2, transcript `docs/tasks/evidencias/task008_reproducao_fin04.log`, banco descartável `shambala_t008_test`, app 9014, prova dos dois destinos antes de qualquer escrita): fechamento da sessão do operador com `saldo_final R$ 190,00` contra gaveta real esperada R$ 95,00 — inflado em R$ 95,00 (PIX 30 + fiado não recebido 40 + consumo de crédito 25 contam como físico); a sessão da Lanchonete, onde **nada** foi operado, recebeu contabilmente mensalidade 60 + doações 25 (destino implícito por `caixa_id=2`), incluindo doação PIX como físico; adiantamento de R$ 25 em espécie nunca apareceu em `financeiro_movimentacoes` nem na sessão; a mesma dívida de fiado podia ser contada duas vezes (agrupado do fechamento + quitação).

### Contrato operação → evento (regras desta entrega)

Uma sessão nova (`versao='eventos'`) tem **fonte única**: `caixa_sessao_eventos`. `efeito_fisico` é a contribuição assinada para a gaveta; o resumo, o fechamento e a impressão usam exatamente a mesma soma (`esperado = saldo_inicial + Σ efeito_fisico`).

| Operação | tipo do evento | valor | efeito_fisico | identidade UNIQUE |
|---|---|---|---|---|
| Venda espécie | `venda` (+`venda_doacao` se houver doação da venda) | total (+doação) | total (+doação) | `venda:{id}:venda` / `:doacao` |
| Venda PIX | `venda` | total | 0 | idem |
| Venda fiado (não recebida) | `venda` | total | 0 | idem |
| Consumo de crédito anterior | `venda_credito` | usado | 0 ("não é dinheiro novo") | `venda:{id}:credito` |
| Cancelamento de venda (TASK_007) | `cancelamento` | valor revertido | −valor só se espécie | `venda:{id}:cancelamento` |
| Recebimento de mensalidade (espécie em sessão) | `mensalidade` | recebido | espécie: valor; PIX: 0 | `mensalidade:{mov_id}` |
| Doação avulsa | `doacao` | valor | espécie: valor; PIX: 0 | `doacao:{fin_id}` |
| Quitação de fiado | `recebimento_fiado` | entregue | espécie: valor; PIX: 0 | `fiadopagar:{venda_id}` (por venda) / `recebimento_fiado:{fin_id}` (quitação integral) |
| Crédito antecipado (adiantamento) | `credito_anticipado` | valor | espécie: valor; PIX: 0 | `credito:{cred_id}` |
| Sangria | `sangria` | valor | −valor (uma vez) | `sangria:{sangria_id}` |

Troco: na venda em espécie a gaveta ganha exatamente `total + doação` (o `recebido − total − doação` volta ao cliente; `recebido < total+doação` → recusa `recebido_insuficiente`, a doação não pode exceder a sobra entregue). Pagamento parcial que gera crédito: **uma** entrega de dinheiro = **uma** entrada (`recebimento_fiado` pelo valor entregue; a sobra vira `fiado_credito_mov` sem segundo lançamento). Sangria v2 é **transferência interna**: par `saida sangria` + `entrada transferencia` com o mesmo `financeiro_movimentacoes.sangria_id` (efeito global zero), destino obrigatório ≠ conta espécie da gaveta, com recebedor/motivo.

### Modelo e travas

- `caixa_sessao_eventos` (banco.py:1006): identidade UNIQUE (idempotência no banco, não só no botão), `sessao_id`, `tipo/natureza/origem_tipo/origem_id`, `venda_id`, `financeiro_id` (vínculo 1:1 quando já existe lançamento), `forma/sentido/valor/efeito_fisico` (NUMERIC(12,2), CHECK valor>0), `conta_id`, `atendente_id`, `criado_em` instantâneo America/Sao_Paulo timestamp(0).
- `caixa_movimentos` ganhou `versao` (DEFAULT `'legado'` — linhas antigas nunca são recalculadas), `dinheiro_contado`, `divergencia_contagem`, `observacao_conferencia`.
- Uma sessão aberta por caixa: índice parcial único `uq_caixa_movimentos_aberto(caixa_id) WHERE status='aberto'`, criado no startup **após diagnóstico** de duplicidades legadas (se houver, o startup avisa e não altera sessão alguma); `abrir_sessao()` usa `pg_advisory_xact_lock(680000000, caixa_id)` + checagem + INSERT.
- Serviços centrais em `servico_sessao.py` (única porta): `moeda` (Decimal 2 casas ROUND_HALF_UP, teto NUMERIC(12,2)), `validar_conta_para_forma` (tipo conferido NO BANCO; nunca no dado do navegador), `sessao_para_recebimento` (espécie SEMPRE exige sessão aberta explícita travada `FOR UPDATE`; PIX pode viver sem gaveta), `registrar_evento`, `contabilizar_recebimento` (um lançamento + um evento; legado escolhido explicitamente só soma espécie), `resumo_sessao`, `fechar_sessao_eventos`.
- Fechamento `eventos` (`fechar_sessao_eventos`): trava a sessão `FOR UPDATE`; lança financeiramente **agora** apenas vendas/doações de venda ainda sem `financeiro_id` e não canceladas (uma por origem, vínculo persistido; fiado não recebido e crédito consumido nunca viram dinheiro); mensalidades/doações/quinações/adiantamentos já lançados na operação não ganham segunda entrada; `esperado` da fórmula do resumo; contado registrado ao lado com divergência e **observação obrigatória quando ≠ 0** — o esperado nunca é sobrescrito. Ordem de travas compatível com TASK_006/007 (venda → produtos ORDER BY id → caixa_movimentos): escrita concorrente entra antes do fechamento ou é recusada depois.
- Duplicidade de comando: violação de UNIQUE faz a exceção **vazar do `with conectar()`** (rollback integral — capturá-la dentro do bloco faria commit parcial).

## Adendo de execução (no DEV) — registrado pelo executor

### T8.1/T8.2 — Mapa e reprodução do FIN-04

Ambiente: banco descartável `shambala_t008_test` + app HTTP dedicado 9014 com `SHAMBALA_MODO_TESTE=1` e `.env.t008`; `tests/repro_task008_fin04.py` prova **os dois destinos** (conexão própria + linha `[TESTE] Ambiente descartável confirmado` do servidor) antes de qualquer escrita. Transcript: `docs/tasks/evidencias/task008_reproducao_fin04.log` — números do quadro acima.

### T8.4 — Implementação

- **`servico_sessao.py`** (novo, ~470 linhas) — regras do quadro de design.
- **`banco.py`** — migração aditiva idempotente (colunas de `caixa_movimentos`, `financeiro_movimentacoes.sangria_id`, tabela `caixa_sessao_eventos`, diagnóstico de duplicidade antes do índice único).
- **`rotas/caixa.py`** — `POST /pdv/finalizar`: espécie sem sessão → `sessao_necessaria` (qualquer caixa), PIX sem sessão → lançamento imediato na conta PIX válida; sessão `eventos` grava eventos em vez de acumuladores; troco/doação pela regra da sobra. `POST /doacao`, `/fiados/{venda}/pagar`, `/fiados/{trab}/pagar-divida` (sobra → crédito sem segunda entrada), `/fiados/credito/{trab}` (adiantamento agora com lançamento financeiro + evento), `/sangria` (destino obrigatório, par vinculado), `/fechar` (GET mostra resumo para `eventos`, POST roteia por versão; legado com campo de contagem → recusa `sessao_legada`; fluxo legado antigo permanece intacto), `/fechar/{mov}/pdf`.
- **`rotas/financeiro.py`** — recebimento de mensalidades (lote) passa a exigir destino validado ANTES de baixar pendência; sessão `eventos` grava `mensalidade:{mov_id}` por pendência; legado escolhido explicitamente soma o lote uma vez.
- **`servico_cancelamento.py`** — compensa em `eventos` via `venda:{id}:cancelamento` (efeito físico só se espécie) e mantém o acumulador legado para sessões antigas; bloqueios/idempotência/travas da TASK_007 preservados.
- **Telas**: barra de destino em `fiados.html`/`mensalidades.html` (sessão/conta mostradas, escolha obrigatória quando há mais de uma; única opção é sugerida e identificada), `pdv.html` bloqueia venda sem caixa aberto com explicação, `sangria.html` exige destino, `fechamento.html` exibe prévia detalhada por tipo/forma + contagem + divergência. RBAC inalterado (terminal de presença continua sem acesso financeiro).

### T8.5 — Testes de serviço e rotas

`tests/test_sessao_eventos_caixa.py` (novo): **116 asserções, 0 falhas**, rotas HTTP reais no ambiente descartável. Cobertura: cenário misto determinístico do spec (inicial 100; venda espécie 20, PIX 30, fiado 40, mensalidade espécie 50 + PIX 60, doação espécie 10 + PIX 15, quitação espécie 8, adiantamento espécie 25, venda 12 com crédito, sangria 30 com destino → vendas comerciais 102,00; recebimentos espécie 113,00; PIX 105,00; **saldo físico esperado 183,00**; crédito consumido não reentra; fiado não recebido não vira dinheiro); CASOS 1–14 do spec (troco doado, cancelamento compensatório e repetição, fechamento duplo/concorrente sem relançamento, recebimentos já lançados não duplicam, parcial→crédito com uma entrada, idempotência + rollback na última escrita, parâmetros adulterados/sessão fechada/conta incompatível recusados sem escrita, concorrência escritas × fechamento, divergência registrada sem ajuste, PIX sem gaveta, abertura concorrente com uma sessão só, legado isolado/consultável com regra antiga, transferência da sangria com efeito global zero, autorização por rota — inclusive cancelamento restrito a admin mesmo com caixa/ler+escrever).

Runner completo `tests/rodar_testes.sh` com as duas suítes novas incluídas: **RC=0, 14 suítes, 369 testes, 0 falhas, 0 pulados** (27+12+8+10+19+18+18+24+21+11+33+25+116+27), log `docs/tasks/evidencias/task008_runner_completo.log`; TASK_006 preservada (33/33) e TASK_007 preservada (25/25, 0 pulados). Correção de isolamento exigida pela entrega: o PIX sem sessão agora cria lançamento com `caixa_id` na hora da venda, e a limpeza da suíte TASK_006 passou a remover `financeiro_movimentacoes` antes dos `caixas` (FK RESTRICT). Conferência do DEV antes/depois do runner: inalterado ("DEV inalterado: ok" no log).

Suíte financeira antiga `tests/test_financeiro_integracao.py` executada separadamente (spec): 3 testes, 7 erros — transcript `docs/tasks/evidencias/task008_suite_financeiro_antiga.log`. Classificação honesta: todas as falhas são do próprio harness antigo (limpeza referencia a tabela `auditoria_eventos`, que **não existe** — recusei criá-la para satisfazer a fixture, conforme o spec; dois testes chamam funções de rota HTTP diretamente, com `request=None` e argumentos `Form` sem injeção do FastAPI — um deles colide com o contrato da TASK_006, que reprifica e avisa divergência em vez de ignorar silenciosamente o preço enviado, e outro nem chega a executar por passar o objeto `Form` ao psycopg2). Nenhuma falha é atribuível à lógica de eventos/fechamento desta entrega, que está coberta pelas rotas reais nas suítes acima. Suíte não foi consertada aqui nem encoberta.

### T8.6 — Validação em navegador real (desktop e smartphone)

`tests/test_caixa_navegador_task008.py` (Playwright, banco descartável da tarefa): **27/27 aprovadas**, cenários C1–C8 com evidências em `docs/tasks/evidencias/TASK_008/`: C1 PDV bloqueado sem sessão aberta; C2 abrir sessão pela tela + venda espécie (sessão nasce `eventos`, evento +10,00); C3 recebimento de fiado sem destino alerta e nada grava, com destino escolhido na barra lança UMA entrada (+12,00 físico); C4 sangria com destino → comprovante, par vinculado efeito zero, evento −20,00; C5 fechamento misto com prévia (vendas espécie/PIX/fiado/crédito e recebimentos separados) e divergência sem observação recusada; C6 fechar com 100,00 contados vs esperado 102,00 → `saldo_final=102,00`, divergência −2,00 registrada com observação, PDF do fechado conferido; C7 sessão legada aberta: tela sem campo de contagem e envio forçado do formulário novo é recusado (`sessao_legada`) sem fechar nem alterar nada; C8 smartphone (412×915): abrir 50 + venda 10 + fechar 60,00 conferido na tela.

A suíte expôs um **bug real invisível aos testes de serviço**: a barra de destino auto-selecionava a única conta PIX e injetava `conta_pix_id` mesmo com forma espécie, e o servidor recusava o recebimento (`conta_invalida`) por confiar no parâmetro. Correção no servidor em 4 pontos (três em `rotas/caixa.py`, um em `rotas/financeiro.py`): fora de PIX, `conta_pix_id` é **ignorado** — a conta é conferida pelo tipo no banco. Backend reexecutado após a correção: 116/116.

### T8.7 — Aplicação no DEV (com backup recuperável e baseline próprio)

Sequência no serviço **existente** da porta 9000 (mesma pasta, mesmo comando; sem nova instância persistente, sem túnel, sem produção):

1. **Baseline antes**: `pessoas=678, checkins=2918, trabalhadores=70 (ativos 70), códigos=70, retirados=0, presenças=20, correções=0, vendas=1859 (soma R$ 25.886,75), itens=3090, financeiro=862 (soma R$ 69.446,04), caixas=5, sessões=58 (1 aberta), produtos=39, movimentos de estoque=3017, cancelamentos=0, eventos=0`. A sessão aberta é a legada do Bazar (id 42, aberta 2026-09-15, inicial R$ 160,00, `total_vendas` R$ 1.235,00 — coerente com suas 22 vendas concluídas, nenhuma alteração após 2026-10-08). **Nota de exatidão**: o adendo da TASK_007 registrou "sessão aberta=1 (total_vendas=R$ 35.101,70)" — R$ 35.101,70 é a soma de `total_vendas` das **58** sessões, não da aberta; não houve nenhuma mudança de fato entre as duas entregas.
2. **Backup recuperável**: `pg_dump -U shambala -d shambala --format=custom | gzip` → `docs/tasks/backups/shambala_pre_task008_20261024_1651.sql.gz` (sha256 `36eaa3d33370c9eda4e327783d096378de7a106d4e08cd44817e404edc0035ba`).
3. **Restauração validada**: dump restaurado no banco descartável `shambala_t008_restauracao_test`; contagens de 15 tabelas **e** somas (`vendas 25.886,75`, `financeiro 69.446,04`) idênticas ao DEV; únicos erros do restore: 2 `ALTER DEFAULT PRIVILEGES` negados ao role `shambala` (limitação de privilégio sem qualquer efeito sobre dados/schema do backup). Banco de validação removido em seguida.
4. **Efeitos do startup conhecidos**: apenas DDL aditivo idempotente (colunas `versao/dinheiro_contado/divergencia_contagem/observacao_conferencia`, `financeiro_movimentacoes.sangria_id`, tabela `caixa_sessao_eventos`, índices, único-por-caixa após diagnóstico) — sem UPDATE, sem tocar sequências, senhas ou documentos. No DEV este schema já estava aplicado desde o incidente abaixo (startup idempotente); o reinício apenas o reconheceu.
5. **Reinício controlado** do mesmo serviço (pid 3875652 → 316901, log em `docs/tasks/evidencias/TASK_008/dev-startup-20261024.log`): startup limpo (sem erros/avisos de migração; índice único criado sem aviso de duplicidade), `GET /login` → 200, serving da árvore correta.
6. **Baseline depois**: todos os números do item 1 **inalterados**; `eventos=0`, nenhum fato novo, nenhuma operação financeira real criada para demonstração, nenhuma credencial/usuário/permissão alterada. A sessão legada aberta permanece aberta e fechável pela regra antiga — nada foi fechado por esta entrega.

### Incidente registrado (honestidade sobre o ambiente)

Durante a preparação do app descartável da T8.5, uma subida na porta 9014 foi executada **sem** `SHAMBALA_ENV_FILE`/`SHAMBALA_MODO_TESTE` e conectou-se ao banco de desenvolvimento `shambala`. O que aconteceu nesse contato: somente o startup idempotente (`criar_tabelas`/`_migrar` aditivo — as colunas/tabela/index da TASK_008 que já figuram no baseline); nenhum endpoint de escrita foi chamado, nenhum fato alterado — verificação imediata só de leitura confirmou `pessoas=678, checkins=2918, vendas=1859, financeiro=862, sessões abertas=1` inalterados, e o processo foi morto pelo PID real da porta. O incidente está documentado aqui em vez de silenciado; nenhuma credencial foi criada ou alterada para contorná-lo. Lição aplicada: desde então todo app temporário sobe com `setsid env SHAMBALA_ENV_FILE=… SHAMBALA_MODO_TESTE=1` e a confirmação `[TESTE] Ambiente descartável confirmado` é lida no log ANTES de qualquer teste.

### Roteiro de homologação no DEV (somente dados fictícios)

Requer conta de administrador existente (esta entrega não cria usuário nem muda senha):

1. Em `/caixa/abrir`, abrir sessão do caixa desejado com inicial **R$ 50,00** — a sessão nasce na versão nova.
2. Vender **R$ 10,00 em espécie** no PDV; vender **R$ 20,00 em PIX** — na prévia de fechamento, o PIX aparece como recebimento bancário e **não** soma na gaveta.
3. Tentar receber uma quitação de fiado em espécie **sem** sessão escolhida: a tela alerta e nada é gravado; escolher o destino na barra e confirmar: uma única entrada de 12,00 (+12 físico).
4. Sangrar **R$ 30,00** com destino identificado: comprovante mostra a saída; a conta destino aparece com a entrada pareada (efeito global zero).
5. `/caixa/fechar`: prévia deve listar espécie 22,00 recebido (venda 10 + quitação 12), PIX 20,00 fora da gaveta, sangria −30,00, esperado = 50 + 22 − 30 = **R$ 42,00**. Contar 40,00 sem observação → recusado; contar 40,00 com observação → fecha com `saldo_final 42,00` e divergência −2,00 registrada (o esperado NÃO foi sobrescrito).
6. Conferir PDF/consulta da sessão fechada com o resumo original; tentar fechar de novo → nada relançado; reabrir sessão do mesmo caixa → permitida (nova).
7. Abrir a sessão legada do Bazar (id 42): tela mostra o fluxo antigo, **sem** campo de contagem; não fechar nada por aqui — apenas conferir o bloqueio e a nota explicativa.
8. Nada permanece para inativar: nenhuma operação fictícia precisa de limpeza de cadastro.

### Limitações verdadeiras

- **Sessões legadas** (incluindo a aberta do Bazar) permanecem na regra antiga e continuarão misturando PIX/fiado/destino implícito em seus acumuladores; **nenhum reparo retroativo** foi feito nem é pretendido por esta entrega — o cálculo delas permanece incorreto por definição até decisão do dono sobre transição.
- **Saldos de crédito antigos** sem origem comprovada não receberam eventos (preservados como estão; limitação histórica marcada, sem backfill heurístico).
- **Não fazem parte desta entrega** (continuam abertos, conforme o Objetivo): competência/unicidade de mensalidades, alocação detalhada de fiado (parcias), reembolso/estorno pós-fechamento, conciliação bancária, cartão/taxa/pagamento combinado (inexistentes).
- O fechamento `eventos` lança a receita de venda no fechamento (como antes) apenas para o que ainda não tinha lançamento; o contrato anti-duplicidade vale para sessões novas.
- Validação em navegador real rodou no ambiente descartável com credencial local de teste; o DEV não tem credencial usável por política (sem senha no chat, sem criar usuário), portanto o roteiro acima fica com o dono.
- A suíte financeira antiga continua quebrada por motivos próprios (fixture referencing `auditoria_eventos` inexistente + chamada direta de rotas sem Request/DI); não foi consertada nem encoberta aqui.

### Encerramento de recursos

Após a aplicação no DEV: app dedicado da porta 9014 finalizado pelo PID real da porta, banco `shambala_t008_test` removido, `.env.t008` removido, temporários `/tmp/shambala_t008_*` removidos, bancos de validação/runner removidos (o `shambala_test_passes` é recriado a cada execução do `rodar_testes.sh`). O DEV retorna ao estado de sempre: um serviço, HTTP na porta 9000. **Produção não acessada** (nenhum SSH/leitura/deploy); nenhum push. Recursos descartáveis criados por esta entrega não permanecem ativos.
