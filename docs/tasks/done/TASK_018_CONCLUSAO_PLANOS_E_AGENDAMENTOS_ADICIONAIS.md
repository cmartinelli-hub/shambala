# TASK_018 — Conclusão automática de planos e vínculo dos atendimentos adicionais

Status: **implementada e aplicada no DEV** (migração idempotente, serviço da 9000 reiniciado, fatos idênticos). **Não homologada pelo dono**; produção intocada, sem push nem deploy. Registro da implementação em §10–§18.
Data: 26/10/2026.

## 1. Objetivo e decisões aprovadas

Corrigir o ciclo dos planos para que a recepção consiga agendar pelo caminho habitual sem criar planos concorrentes e para que planos efetivamente cumpridos sejam concluídos automaticamente.

Decisões do dono:

1. Plano com cinco atendimentos previstos, todos os cinco realizados e nenhuma sessão adicional pendente deve ser encerrado como Concluído.
2. Conclusão administrativa por cumprimento é diferente de alta clínica. Usar “Concluído”, preservando alta, cancelamento e seus significados atuais.
3. Se existir plano ativo, o atendimento adicional deve ser vinculado a ele, sem outro plano silencioso. A tela informa o médium e a data de início do plano de destino.
4. Havendo vários planos ativos históricos, exigir escolha explícita entre opções compreensíveis; não selecionar por maior id, primeira linha ou simples recência.
5. Sessão adicionada antes da conclusão mantém o plano aberto até seu cumprimento. Depois de concluído, não reabrir automaticamente: seguir o fluxo autorizado de novo plano.
6. Cancelar sessão não significa realizá-la. Não concluir plano incompleto por ausência de agendamentos.
7. Não remanejar avulsos, fundir planos ou encerrar planos históricos automaticamente.

A TASK_017 foi publicada isoladamente (b5bca85 + patch 4352d92), liberando à Recepção a agenda dos planos. Não presumir que a árvore inteira do DEV está em produção. Esta tarefa não autoriza publicação.

## 2. Preparação e investigação focal

Ler AGENTS.md, SHAMBALA_CONTEXTO.md, TASK_017, regras de plano único e dos três passes/R4, e a investigação recente do bloqueio na Agenda. Preservar alterações de outras tarefas e registrar base e estado do Git.

Mapear antes de editar:

- Como se guarda a quantidade planejada, sessões adicionais e situação das sessões.
- Todos os caminhos que criam plano/sessão, marcam realizado, cancelam, registram falta, reagendam e registram atendimento concluído pelo dashboard.
- Se “avulso” hoje é sessão sem plano, cria plano de sessão única ou tem comportamento diferente conforme a rota. Demonstrar com código e reprodução, sem aceitar a nomenclatura da tela como prova.
- Qual unidade conta como um atendimento cumprido, evitando contar duas vezes sessão original e sua reposição/reagendamento.
- Como os relatórios, filtros e constraints tratam plano ativo, alta e cancelamento.

O incidente mostrou vários planos ativos da mesma pessoa sem sessões atualmente agendadas, mas com sessões realizadas/faltas/cancelamentos. Não descrever isso como “planos sem sessões”. Não acessar produção nesta tarefa; usar a evidência já disponível. Se faltar dado indispensável, registrar a lacuna.

## 3. Contrato de conclusão

Antes de implementar, registrar na tarefa uma tabela de estados e exemplos com a representação concreta adotada no schema.

A conclusão exige simultaneamente:

- Plano ativo e elegível à regra prospectiva desta tarefa.
- Quantidade originalmente prevista cumprida por atendimentos efetivamente realizados.
- Toda extensão válida do plano também cumprida.
- Nenhum atendimento adicional pendente de realização/reposição segundo as regras existentes.

Não usar “zero agendamentos” ou “todas as linhas restantes realizadas” como critério isolado. Cinco previstos, quatro realizados e um cancelado/faltou não são cinco cumpridos. Cancelamentos e faltas não satisfazem a quantidade prevista. Reposição cumpre a obrigação correspondente sem duplicá-la.

Exemplo obrigatório: 5 previstos → 4 realizados + 1 pendente = ativo; 5 realizados = concluído; sessão extra acrescentada enquanto ativo → 6 atendimentos a cumprir; após 5 realizados e a extra pendente = ativo; após cumprir a extra = concluído.

A quantidade a cumprir deve ter representação estável. Não deduzi-la apenas de um total de linhas que pode diminuir com cancelamento ou mudar com reagendamento. Se o modelo atual não distingue previsão, extensão e reposição, introduzir a menor estrutura/metadado necessário. Documentar como o cancelamento de uma sessão adicional é tratado; não considerar cancelamento como cumprimento nem reduzir silenciosamente a extensão aprovada. Caso a regra atual não resolva uma ambiguidade de negócio, perguntar pontualmente ao dono e continuar o restante.

Centralizar a avaliação em serviço compartilhado e chamá-lo nos pontos efetivos de transição. Marcar realizado pelo dashboard deve aplicar a mesma regra que os demais caminhos. Conclusão grava status próprio e instante/autoria/origem compatíveis com a auditoria existente. Não atribuir ao médium uma alta clínica automática.

Não fechar planos em GET, consulta, renderização ou startup por mera descoberta de que parecem completos. Não criar cron ou serviço permanente para isso.

Planos concluídos continuam no histórico e fora dos ativos; não permitem novos agendamentos. Preservar leitura e relatórios. Atualizar todos os consumidores do status, sem tratar status desconhecido como ativo por fallback.

## 4. Agendamento pelo caminho habitual

Reaproveitar a Agenda e seu formulário atual. O operador informa pessoa, médium e dados do atendimento como hoje.

- Sem plano ativo: preservar o fluxo existente de novo atendimento/plano e suas permissões; esta tarefa não concede à Recepção poder geral de criar plano clínico.
- Exatamente um plano ativo compatível com o médium selecionado: mostrar claramente o destino antes da confirmação e acrescentar a sessão nesse plano.
- Vários planos ativos: apresentar escolha explícita, identificada por médium, início em DD/MM/AAAA e resumo de situação das sessões. Validar no servidor a escolha e a compatibilidade. Não exigir que o operador conheça IDs internos.
- Plano ativo de outro médium: não anexar ao médium errado nem criar novo plano para contornar plano único. Explicar o conflito e oferecer acesso ao plano permitido; troca de médium continua seguindo a regra própria existente.
- Revalidar plano, pessoa, médium, status e permissão no POST. IDs enviados pelo navegador não são autorização.
- Se a seleção ou apresentação exigir uma confirmação intermediária, não escrever parcialmente nem perder os dados preenchidos.
- Uma tentativa repetida/double click não deve duplicar a sessão adicional. Reaproveitar proteção de agendamento existente; se ausente, introduzir mecanismo mínimo adequado sem importar o subsistema financeiro inteiro.

Distinguir adicional de reposição nos fluxos que já representam reposição; não aumentar automaticamente a meta ao reagendar uma sessão. Preservar dias válidos R4, piso dos três passes, requer_passe e demais regras existentes.

Mensagens e links devem usar referências humanas, por exemplo:
“Este atendimento será acrescentado ao plano com Eglaudio / Pai José, iniciado em 14/10/2026.”
Botão: “Abrir agenda deste plano”.

Reaproveitar e revisar a correção de mensagem já feita no DEV na investigação anterior. Não usar “plano 1799” como instrução operacional. IDs podem continuar internos nas rotas/auditoria.

## 5. Histórico e ativação prospectiva

Não aplicar a conclusão automática indistintamente a planos antigos no primeiro startup ou na primeira escrita de qualquer pessoa.

Definir fronteira prospectiva explícita e persistida, de modo que instalações/execuções futuras não mudem a regra por data corrente. Novos planos criados após a implantação são elegíveis; planos preexistentes permanecem legados até revisão deliberada fora desta tarefa. Se necessário, usar versão da regra/marcador com defaults e migração que distingam corretamente os dois universos.

Planos legados continuam permitindo agendamento adicional autorizado e seleção explícita quando há múltiplos, mas não são encerrados retroativamente por esta implementação. Exibir a condição de legado apenas onde ajude a entender a ausência de conclusão automática, em linguagem simples.

Não fundir nove planos do incidente, não escolher um “canônico”, não mover sessões históricas e não dar alta/cancelamento em lote. Entregar diagnóstico e proposta separada de revisão histórica, sem executá-la.

## 6. Concorrência e permissões

Adicionar sessão e registrar a última realização devem disputar uma trava comum do plano, com ordem consistente entre serviços:

- Se a inclusão confirmar primeiro, a realização deve enxergar a extensão e manter o plano ativo se ainda há pendência.
- Se a última realização concluir primeiro, a inclusão espera e recusa o plano já concluído, sem reabri-lo, sem criar outro e sem escrita parcial; orientar a recarregar.

Preservar proteção de plano único também em criações concorrentes. Validar por provas determinísticas com conexões independentes, sem depender de sleeps.

Recepção mantém os poderes da TASK_017 de agendamento. Conclusão automática é consequência do cumprimento registrado por operação já autorizada; não autoriza novas ações clínicas, alteração de realizados, exclusão ou encerramento manual pela Recepção. Preservar escopo de Cambonas e vínculo plano↔médium↔sessão. Nenhuma concessão ampla a cadastros.mediuns.

## 7. Validação necessária

Usar PostgreSQL nativo descartável e as guardas de isolamento existentes. Não inserir atendimentos fictícios no DEV real. Cobrir serviços e rotas efetivamente utilizadas:

1. Cinco previstos, quinto realizado → uma conclusão; histórico preservado, nenhum evento duplicado por repetição.
2. Quatro realizados + cancelado/falta → não concluído; reposição corretamente cumprida sem dupla contagem.
3. Adicional antes da última realização aumenta o que precisa ser cumprido e impede conclusão prematura; reagendamento não cria obrigação duplicada.
4. Plano sem sessões, incompleto ou com metadados insuficientes não recebe conclusão por suposição.
5. Agendamento habitual com um plano ativo vincula a ele; com vários exige escolha; outro médium recusa vínculo incorreto.
6. Plano concluído não reabre; tentativa obsoleta e reenvio não duplicam sessões nem criam planos indevidos.
7. Disputa inclusão × última realização nas duas ordens, com evidência da coordenação no banco e efeitos integrais.
8. Usuários Recepção, Recepção+Cambonas, Cambona vinculada, sem autorização e somente leitura; tentativas por URL/POST com pessoa/plano/médium incompatíveis.
9. Planos históricos e sessões permanecem idênticos após migração e após startup repetido. Regra prospectiva não passa a abranger legado por acidente.
10. Navegador com dados fictícios: escolha por médium/data, aviso de destino, botão de acesso, preservação do formulário e leitura do status Concluído. Desktop e celular sem redesign.
11. Regressões de três passes, plano único, agenda do médium e TASK_017. Executar runner exigido pelo projeto, registrando árvore/banco efetivos. Falhas e pulados não são aprovação.

## 8. Aplicação e limites

Autorizado implementar e disponibilizar no serviço existente do DEV, porta 9000, após validar. Antes de migração/restart: backup durável com hash e restauração conferida, baseline de planos/agendamentos/status e demais fatos pertinentes. Migração mínima, idempotente, sem alteração de fatos clínicos históricos.

Provar propriedade e configuração do processo antes de reiniciar somente o serviço existente. Conferir baseline depois e separar mudanças esperadas de schema/metadados dos fatos que devem permanecer iguais.

Não alterar credenciais, criar usuários reais, registrar/cancelar sessões reais para prova ou conceder permissões fora do escopo. Sem nginx/TLS/serviços permanentes novos. Limpar apenas recursos descartáveis próprios.

Não acessar produção nem fazer push/deploy. A autorização anterior foi exclusivamente da TASK_017; não se estende a esta tarefa.

## 9. Entrega e aceite

Registrar na própria tarefa:

- Diagnóstico de como os avulsos são gravados hoje e mapa de todos os caminhos alterados.
- Contrato dos estados, meta, adicional/reposição/cancelamento e fronteira prospectiva.
- Arquivos/migrações, eventos de auditoria e comportamento concorrente.
- Matriz de testes com resultados e logs, evidências visuais fictícias e limitações verdadeiras.
- Backup, baseline e aplicação no DEV; roteiro curto do dono com dados fictícios e indicação do histórico que permanece.
- Proposta, sem execução, para revisão dos planos legados.

Atualizar SHAMBALA_CONTEXTO.md com a regra efetiva. Commitar localmente só os arquivos desta tarefa, sem incluir evidências regeneradas de tarefas alheias. Mover para docs/tasks/done/TASK_018_CONCLUSAO_PLANOS_E_AGENDAMENTOS_ADICIONAIS.md quando implementada, distinguindo “aplicada no DEV” de “homologada pelo dono”.

Aceite: planos novos cumpridos concluem administrativamente; sessões adicionais entram no plano ativo de maneira explícita pelo caminho habitual; múltiplos planos são apresentados por referências humanas; histórico e poderes clínicos permanecem preservados. Publicação depende de autorização posterior.

---

# Registro da implementação

## 10. Diagnóstico — como os fatos são gravados hoje (T18-1, provado por código e reprodução)

Base investigada: `4a2a3a6` + alterações locais da investigação do bloqueio (DEV).

**“Avulso” não existe como sessão sem plano.** Toda sessão pertence a um plano; o que muda é a rota:

- `POST /agenda/novo` (rotas/agenda.py:180) **cria sempre um plano novo** (`planos_tratamento` + `plano_pessoas` + `gerar_agendamentos_plano`). Com `frequencia='avulso'` o plano nasce com exatamente 1 sessão (banco.py:132–138: intervalo 0). É o caminho que a tela chama de “agendar avulso”. A regra de plano único bloqueia esse caminho enquanto houver plano ativo (rotas/agenda.py:244–254) — foi o bloqueio do incidente.
- `POST /agenda/medium` “lançar” (rotas/agenda.py:579–652): Caso 1 — plano ativo no médium escolhido → acrescenta **uma linha** em `agendamentos` (encaixe=0, requer_passe=0) **sem tocar `sessoes_total`**; Caso 2 — plano ativo em outro médium → recusa; Caso 3 — nenhum plano → cria plano com `qtd_sessoes`.
- Agenda do plano (rotas/mediuns.py:526–561, “Adicionar sessão avulsa/encaixe”): insere linha com `encaixe` do formulário, **sem tocar `sessoes_total`**.

**Contas do plano:** `sessoes_total` é escrito só na criação (nenhum `UPDATE ... SET sessoes_total` no código; única mudança manual é o formulário de edição em rotas/pessoas.py:540). `sessoes_realizadas` é contador: +1 nos dois caminhos de realizado do dia (rotas/dia.py:692 e :1146) e −1 no desfazer (:726). Cancelamento/falta não mexem no contador. Consequência: sessões extras e o contador não formam uma “quantidade a cumprir” estável — o adicional não aumenta meta nenhuma, e o contador pode divergir da realidade das linhas. Reprodução: suíte `tests/repro_incidente_agenda_plano_sem_sessoes.py` (RB1–RB5).

**Transições mapeadas (todos os pontos que precisam do serviço compartilhado):**

| Fato | Onde | Efeito hoje |
|---|---|---|
| realizado (2 caminhos) | dia.py:667–699, dia.py:1127–1149 | linha→'realizado'; contador +1. **Sem guarda de idempotência**: reenvio conta duas vezes |
| desfazer | dia.py:702–733 | linha→'agendado'; contador −1 (GREATEST(0,·)) |
| falta | dia.py:295; mediuns.py:579–596 | linha→'faltou'; 2 faltas consecutivas → plano 'cancelado' (banco.py:257) |
| cancelar sessão | mediuns.py:564–576 | linha→'cancelado'; nada mais |
| alta / cancelar plano | mediuns.py:403–434; pessoas.py:579+ | status='alta' (com o legado `concluido=1`) ou 'cancelado'; pendentes 'agendado'→'cancelado' em lote |
| novo acolhimento | dia.py:~1490 | `encerrar_planos_ativos` cancela os planos ativos antigos da pessoa (banco.py:235) |
| reagendar | mediuns.py:599–660 | recalcula datas das MESMAS linhas — não cria obrigação nova |
| criação de plano | agenda.py:259/634, mediuns.py:385, dia.py:1466 | INSERT com `sessoes_total` |

**Nenhuma detecção de conclusão existe hoje.** O legado `concluido=1` significa “alta” e a sincronização banco.py:701 promove `concluido=1 AND status='ativo'` para 'alta'. Não há cron nem serviço; não fechar planos em GET/consulta/render/startup (nada disso existe e nada será criado).

**O incidente (registrado sem eufemismo):** a pessoa tem 9 planos todos `status='ativo'`, nenhum com sessão `agendado`, mas com realizadas/faltas/cancelamentos. Não são “planos sem sessões”: sessões foram **canceladas** (cancelar ≠ encerrar) e planos históricos cumpridos nunca receberam alta. São exatamente o caso que a conclusão automática prospectiva resolve para planos novos — os legados permanecem como estão (§12).

## 11. Contrato de conclusão (estados e representação concretos)

Decisões do dono registradas em 26/10/2026 além das §1–9:

- **Cancelar sessão adicional ainda não realizada revoga a extensão** correspondente (explícito, com registro no histórico — a linha fica 'cancelado' e o contador de extensões volta). Não reduz a previsão original nem elimina obrigação de reposição.
- **Desfazer reavalia o plano**: se a conclusão foi automática e o desfazer tornar o plano incompleto, a conclusão é desfeita na mesma transação, auditada. Isso corrige o fato que sustentou a conclusão; **não é reabertura por novo agendamento** (decisão 5 permanece). Alta clínica e encerramento manual nunca são revertidos automaticamente.
- Se ao reavaliar já existir **outro plano ativo** para a pessoa, a operação é **recusada sem efeitos parciais**, com orientação de revisão autorizada (não gerar dois ativos silenciosamente).

**Estados de `planos_tratamento.status` (TEXT):** `ativo`, `alta`, `cancelado` (significados atuais preservados) + **`concluido`** (novo; conclusão administrativa por cumprimento; ≠ alta). A flag legada `concluido` (INTEGER) continua pertencendo à alta — o automático NÃO a escreve, para não colidir com a sincronização banco.py:701.

**Quantidade a cumprir (representação estável, não derivada de contagem de linhas):**

```
a_cumprir = sessoes_previstas + sessoes_adicionais
cumpridas = sessoes_realizadas   (guardado contra contagem dupla)
conclui   ⟺ status='ativo' ∧ regra_conclusao=1 ∧ cumpridas ≥ a_cumprir
            ∧ nenhuma linha 'agendado' pendente ∧ a_cumprir > 0
```

Colunas novas (migração mínima idempotente em `banco._migrar`, via `para_adicionar`):

| Coluna | Sentido |
|---|---|
| `planos_tratamento.sessoes_previstas INTEGER NULL` | quota original, copiada de `sessoes_total` na criação; estável (cancelamento/reagendamento não a altera); edição manual explícita de `sessoes_total` a reespelha |
| `planos_tratamento.sessoes_adicionais INTEGER NOT NULL DEFAULT 0` | extensões líquidas válidas: +1 ao acrescentar sessão; −1 ao cancelar extensão pendente (revogação) |
| `planos_tratamento.regra_conclusao INTEGER NOT NULL DEFAULT 0` | **fronteira prospectiva persistida**: 0=legado (não conclui automaticamente), 1=nascidos após esta entrega. Backfill `=0` para todo o existente; todo INSERT de plano passa a gravar 1. A elegibilidade é do registro, nunca da data corrente |
| `planos_tratamento.concluido_em TEXT` / `.concluido_por INTEGER` / `.conclusao_origem TEXT` | auditoria da conclusão ('cumprimento_automatico'; campo preparado para revisão futura) |
| `planos_tratamento.conclusao_desfeita_em/por` | auditoria da reversão pelo desfazer |
| `agendamentos.adicionada INTEGER NOT NULL DEFAULT 0` | identifica linha de extensão (permitiu a revogação no cancelamento e não sobrecarrega `encaixe`, que é marcação de tela) |

Backfill na migração: `sessoes_previstas = sessoes_total WHERE sessoes_previstas IS NULL` (uma vez; nunca reescreve linhas futuras). Nenhum fato clínico histórico é alterado: status, linhas e contadores permanecem idênticos; a única diferença é a presença dos metadados com valores padrão.

**Exemplos obrigatórios (meta sempre = previstas + adicionais):**

| Situação | Estado |
|---|---|
| 5 previstos; 4 realizados + 1 agendado | ativo (4 < 5) |
| 5 previstos; 5 realizados, zero 'agendado' | **concluido** automaticamente |
| ativo 5/5 cumpridos? não — antes disso: acrescenta 1 extra | previstas 5, adicionais 1 → a cumprir 6; plano ativo |
| 5 realizados + extra pendente | ativo (5 < 6) |
| extra realizada | **concluido** (6 ≥ 6) |
| extra cancelada antes de realizar (plano ativo) | revoga: `sessoes_adicionais` 1 → 0, a cumprir volta a 5. **Fundamento: decisão específica desta tarefa** (§1 — a extensão só existe enquanto a sessão extra estiver de pé; cancelar antes de realizar desfaz o `+1`), que **preserva o histórico**: a linha fica registrada como `cancelado` e apenas o contador líquido recua. Não é princípio da TASK_017 — a TASK_017 liberou a agenda dos planos à Recepção e não define revogação de extensão; atribuir a regra àquela tarefa seria inventar fundamento |
| 4 realizados + 1 sessão original cancelada | ativo — cancelamento não cumpre e a previsão não se reduz (decisão 6) |
| falta | nunca cumpre; 2 faltas consecutivas seguem encerrando como 'cancelado' (regra própria intacta) |
| reagendamento | mesma linha, sem mudar previstas nem adicionais |
| **reposição** de prevista não cumprida | linha **nova** marcada `agendamentos.repoe=1`, com `adicionada=0`: cumpre a obrigação original, `sessoes_previstas` e `sessoes_adicionais` intactos (§19). Cancelar a reposição pendente reabre a vaga |
| plano 'concluido' recebe tentativa de nova sessão | recusa explícita, sem reabrir, sem criar plano na mesma operação |
| desfazer do último realizado de plano concluído automaticamente | reavalia: se incompleto e sem outro plano ativo → volta a 'ativo', conclusão marcada como desfeita; se houver outro plano ativo → recusa sem efeitos |

**Serviço único:** `servico_conclusao_planos.py` centraliza avaliação e as transições de contagem. Trava comum: toda inclusão de sessão, realizado, desfazer e cancelamento de extensão começa por `SELECT ... FOR UPDATE` na linha do plano (ordem sempre plano→sessões→contadores). Conclusão/desconclusão acontecem na mesma transação da operação autorizada que as disparou; nada roda em GET/renderação/startup.

Consumidores de status: `planos_ativos_da_pessoa` e demais filtros `status='ativo'` já excluem 'concluido' (fora dos ativos, histórico preservado); telas que rotulam status recebem o rótulo “Concluído” explicitamente (sem tratar status desconhecido como ativo). Planos concluídos não aceitam novos agendamentos (as rotas de escrita verificam 'ativo').

## 12. Mapa da implementação (o que ficou onde)

**Fonte única das transições — `servico_conclusao_planos.py` (novo, ~270 linhas):**

| Função | Linha | O que garante |
|---|---|---|
| `ReavaliarRecusado` | 20 | exceção que **anula toda a transação** quando reabrir geraria dois planos ativos |
| `estado_plano` | 44 | leitura da situação (previstas, adicionais, cumpridas, pendentes, status, origem) |
| `vagas_a_repor` | 54 | sessões previstas (`adicionada=0`, `repoe=0`) que ficaram `faltou`/`cancelado` **menos** as reposições em curso ou cumpridas (`repoe=1`, `agendado`/`realizado`) — é o que autoriza uma reposição (§19) |
| `_deve_concluir` | 77 | a conjunção da §11: `ativo ∧ regra_conclusao=1 ∧ previstas NOT NULL ∧ a_cumprir>0 ∧ cumpridas ≥ a_cumprir ∧ zero 'agendado'` |
| `reavaliar` | 110 | fecha por cumprimento ou reabre por correção de fato, sempre com auditoria; recusa quando a reabertura duplicaria ativo |
| `_travar` | 164 | `SELECT id … FOR UPDATE` na linha do plano — **toda** entrada do serviço começa por aqui |
| `marcar_realizado` | 170 | linha→'realizado' + contador +1, **idempotente** (reenvio não conta duas vezes) |
| `desfazer_realizado` | 198 | desfaz o realizado e reavalia; só reabre conclusão de origem automática |
| `TIPOS_SESSAO` | 225 | contrato fechado de tipos: `adicao` (extensão) e `reposicao` (§19); qualquer outro valor é recusado sem escrever |
| `acrescentar_sessao` | 228 | insere linha de extensão (`adicionada=1`, `+1` em `sessoes_adicionais`) **ou** de reposição (`repoe=1`, contadores intactos, exige vaga); revalida `ativo` **sob a trava** |
| `cancelar_sessao` | 275 | linha→'cancelado'; se era extensão ainda pendente, revoga o `+1` (`GREATEST(0, …-1)`); se era reposição pendente, reabre a vaga |

Ordem de travas sempre plano → sessões → contadores. `ORIGEM_AUTOMATICA = "cumprimento_automatico"` (15) marca o que o automático fez; o campo está pronto para uma origem de revisão manual futura, que **não** existe ainda. Nada no serviço é chamado em GET/render/startup.

**Migração (`banco.py`)** — oito colunas novas em `planos_tratamento` + `agendamentos.adicionada` + `agendamentos.repoe` (§19), todas por `para_adicionar` de `_migrar`: `ADD COLUMN` idempotente, sem tocar DDL de tabela existente; backfill único `sessoes_previstas = sessoes_total WHERE sessoes_previstas IS NULL`. `planos_ativos_da_pessoa` passou a expor as três colunas de contrato para as telas decidirem sem nova consulta.

**Pontos de transição (chamadas reais, todas com `autor_id = atendente`):**

| Transição | Arquivo:linha | Comportamento novo |
|---|---|---|
| realizado (Atendimento do dia) | `rotas/dia.py:693` | pelo serviço; conclui na mesma transação |
| realizado (Fraterno/passe) | `rotas/dia.py:1150` | idem, mesmo caminho de contagem |
| desfazer realizado | `rotas/dia.py:725` | reavalia; recusa com `conn.rollback()` explícito (:728) — **sem efeitos parciais** |
| acrescentar sessão — `/agenda/novo` confirmado | `rotas/agenda.py:336` | entra no plano escolhido; o **tipo** (extra × reposição) vem da escolha feita por plano no formulário (:332); recusa plano não-ativo sem reabrir |
| acrescentar sessão — `/agenda/medium` lançar | `rotas/agenda.py:740` | mesmo serviço (Caso 1 da tela do médium), com `tipo` do formulário |
| acrescentar sessão — agenda do plano | `rotas/mediuns.py:579` | idem, com `_plano_do_medium` validando URL↔plano e `tipo` do formulário (§19) |
| cancelar sessão do plano | `rotas/mediuns.py:645` | revogação da extensão pendente (decisão §11); reposição pendente reabre a vaga (§19) |
| criação de plano (Agenda, sem plano ativo) | `rotas/agenda.py:410` | INSERT com `sessoes_previstas` e `regra_conclusao=1` |
| criação de plano (lançamento do médium, caso 3) | `rotas/agenda.py:807` | idem |
| criação de plano (acolhimento fraterno) | `rotas/dia.py:1470` | idem, sob a chave de idempotência do acolhimento |
| criação de plano (cadastro de médiuns) | `rotas/mediuns.py:386` | idem |
| edição manual de `sessoes_total` | `rotas/pessoas.py:565–568` | reespelha `sessoes_previstas` — só por edição explícita na tela |

**Telas:** `templates/agenda/confirmar_plano.html` (novo) apresenta o destino em três modos — `confirmar` (um plano compatível), `escolha` (vários, um rádio por plano, referenciados por médium + início + situação) e `conflito` (plano ativo de outro médium) — sempre preservando os campos preenchidos e escrevendo nada até confirmar; `rotas/agenda.py:189–211` (`_referencia_plano`) é o único lugar que fabrica essa referência humana, e `:48` (`_pode_abrir_agenda_plano`) decide se a porta “Abrir agenda deste plano” aparece. `templates/agenda/index.html:11–23` mostra o aviso de destino e a porta; `templates/mediuns/planos.html:151`, `templates/mediuns/agenda.html:26–28`, `templates/pessoas/ficha.html:110–113` e `templates/dia/fraterno.html:145` leem o status `concluido` como “Concluído (pelo cumprimento)”. As tabelas novas rolam dentro do próprio container `.fin-rolo` (padrão existente), sem rolagem horizontal do documento.

**Não foi tocado:** calendário de atendimento e o piso de passe (`_reaplicar_piso_passe` continua a única regra de passe), a regra das duas faltas → 'cancelado', alta/cancelamento de plano (decisão clínica), `encerrar_planos_ativos` do novo acolhimento, a proteção de plano único (preservada — agora com escolha explícita em vez de bloqueio seco) e o significado legado de `concluido=1`.

## 13. Matriz de testes (casos da §7 → teste → resultado real)

Suíte nova `tests/test_conclusao_planos_task018.py` — **19/19 aprovados** (banco selado `shambala_test_passes`, porta 9011, `SHAMBALA_MODO_TESTE=1`), no runner e isoladamente. Ela exerce **as rotas reais** (`POST /dia/atendimento/{id}/realizado`, `/desfazer`, `/cancelar`, `/falta`, `/reagendar`, `/agenda/novo`, `/agenda/medium/lancar`, `/cadastros/mediuns/{m}/planos/{p}/agenda/novo`, `/dia/fraterno/{checkin}`, `/cadastros/mediuns/{m}/planos/novo`), não só o serviço. Os quatro testes acrescentados depois da primeira entrega (K13–K16) existem porque o dono recusou a prova pelo serviço como suficiente — §19.

| Caso §7 | Teste(s) | O que mede |
|---|---|---|
| 1 | K1 | 5 previstos → o 5º realizado conclui **uma** vez; reenvio não repete efeito nem muda `concluido_em` |
| 2 | K2 (contrato no serviço) + **K14/K15/K16** (formulário e POST reais) | 4 realizados + 1 falta **não** conclui; a reposição daquela prevista cumpre a obrigação original sem mexer em `sessoes_adicionais` e conclui em 5. K2 prova o contrato; K14–K16 provam o caminho que a recepção usa (§19.2) |
| 3 | K3 | extra antes da última realização ⇒ a cumprir 6 e plano aberto; inclusão duplicada recusada; **reagendar mantém `(1, 6, 1)`**; realizar a extra conclui em 6 |
| 4 | K4 | vazio, incompleto, sem meta (`sessoes_previstas IS NULL`) e legado (`regra_conclusao=0`) — nenhum conclui por suposição |
| 5 | K5 + navegador | um plano ⇒ confirmação; vários ⇒ dois rádios referenciados por **início**; médium incompatível ⇒ conflito; `plano_destino` de outra pessoa (navegador adulterado/obsoleto) é rejeitado no servidor |
| 6 | K6 + navegador | plano `concluido` recusa inclusão pela rota **e** pelo serviço; tela lê “Concluído pelo cumprimento”; caminho habitual sem plano ativo cria plano **novo** com `regra_conclusao=1, sessoes_previstas=2` |
| 7 | K7 ordem 1 e 2 | disputa real com duas conexões do pool, travessa por `threading.Event` e evidência de espera em `pg_stat_activity` (`wait_event_type='Lock'`): realização vence ⇒ `'plano_nao_ativo'` e estado final `(5,5,0,0)`; inclusão vence ⇒ plano segue `ativo` `(6,5,1)` com 1 extensão. Nenhum efeito parcial nos dois sentidos |
| 8 | K8 + navegador (RB5) | Recepção grava; somente-leitura 403; sem módulo 403; médium trocado na URL 403; usuário “Só Agenda” recebe a página de destino **sem** a porta que ele não atravessa |
| 9 | K9 + §15 | `banco._migrar` duas vezes: as linhas de `agendamentos` **inteiras** idênticas (coluna a coluna, com `adicionada` e `repoe` já na leitura — nenhuma sessão histórica reclassificada), legado com `regra_conclusao=0` e `sessoes_previstas=sessoes_total`, alta legada preservada, legado **não** conclui sozinho; tela avisa “iniciado antes da regra de…” |
| 10 | suíte de navegador | 44 verificações aprovadas, 0 falhas, 11 PNGs — §14 |
| 11 | runner completo | §13.1 |
| — (§19.1) | K13 | cada um dos **quatro criadores** de plano pelo POST real do seu caminho: `/agenda/novo` sem plano ativo, `/agenda/medium/lancar` caso 3, acolhimento fraterno (`POST /dia/fraterno/{checkin}`) e o plano criado pela tela do médium (`POST /cadastros/mediuns/{m}/planos/novo`). Lê a linha gravada no banco — `regra_conclusao=1`, `sessoes_previstas` igual a `sessoes_total`, `status='ativo'` — e conduz um deles até a conclusão para provar que o metadado não é decorativo. Inclui a idempotência do acolhimento (reenvio não cria segundo plano) |
| — (§19.2) | K14 | reposição **pelo formulário e POST da agenda do plano**: cancelar a prevista → a vaga existe → POST com `tipo=reposicao` → linha `repoe=1`, `sessoes_adicionais` intacto, `adicionada=0` → realizar → plano conclui na quota original |
| — (§19.2) | K15 | recusas pela rota: `sem_vaga_para_repor` (sem vaga, e depois que a vaga é coberta), `tipo_invalido` (não escreve nada), cancelar a reposição pendente **reabre** a vaga, cancelar uma prevista também a abre |
| — (§19.2) | K16 | `/agenda/novo` com dois planos ativos: o tipo é escolhido **por plano** (`tipo_<id>`) e aplicado só ao plano escolhido; `/agenda/medium/lancar` repõe com a mensagem “sem virar extensão” e `GET /agenda/medium/situacao` devolve `vagas_repor` coerente com a vaga real |

Regras de conclusão que não têm teste próprio seriam suposição, então também estão cobertas: K10 reabertura auditada (`conclusao_desfeita_em/por` escritos), K10 alta clínica e encerramento manual **não** revertidos, K10 desfazer com outro plano ativo ⇒ recusa com `atendimento_realizado=1` e contadores intactos, K11 cancelar extensão revoga / cancelar prevista não reduz a previsão, K12 seis GETs (agenda, plano, ficha, médiuns) deixam um plano imediatamente concluível intacto.

### 13.1 Runner exigido pelo projeto (caso 11)

`bash tests/rodar_testes.sh` **depois do complemento** (§19), log completo congelado em `docs/tasks/evidencias/TASK_018/runner_completo.txt` (o runner escreve no `.log` vivo, ignorado pelo git; o `.txt` é cópia idêntica no momento da entrega — `wc -l` 1532 nos dois). Árvore e banco efetivos registrados pelo próprio runner:

```
config ..: /nc/projetos/shambala/.env.test (autoridade do destino)
banco ...: shambala_test_passes        porta ...: 9011
token ...: t010-20261026134422-2508346 árvore ..: ca54fac9…-dirty
```

**28 suítes, todas `rc=0`, nenhum `rc≠0` no sumário, script terminando com código de saída 0, 0 pulados.** As relevantes para esta entrega: `test_conclusao_planos_task018.py **19/19**`, `test_conclusao_navegador_task018.py **aprovadas=44 falhas=0**`, `test_tres_passes.py 27/27`, `test_plano_unico.py 12/12`, `test_agenda_medium.py 10/10`, `test_recepcao_planos.py 8/8` (TASK_017), `test_permissoes.py 8/8`, `test_repro_incidente… 5/5 (RB1–RB5)`. Regressões vizinhas também verdes: presença/correções/PDV/caixa/financeiro (TASK_001–012, 014, 016) e o teste negativo de isolamento (16/16 cenários recusando o banco do DEV). Conferência final do runner: DEV `pessoas=678 checkins=2920` antes e depois — **inalterado**. As duas suítes novas entraram em `tests/rodar_testes.sh` (`SUITE_PADRAO`), então passam a rodar em todo runner daqui para frente.

Ruído honesto do log: no desmonte da instância de teste o shell registra `linha 89: … Morto` — é o `uvicorn` do ambiente descartável sendo morto pelo próprio runner, não uma falha de suíte (todas as 28 fecharam `rc=0` antes disso).

Nada foi contado como aprovado por estar “quebrado de origem”: os pulados são zero e não há suíte em regime de permissão.

## 14. Evidências visuais (dados fictícios, ambiente descartável)

`docs/tasks/evidencias/TASK_018/navegador/` — Chromium real via Playwright contra a instância de teste (porta 9011, banco selado), com pessoas/médiuns/planos de prefixo `T018N`, removidos ao final e conferidos como removidos (`tests/test_conclusao_navegador_task018.py`). **Nenhum atendimento fictício entrou no DEV real.**

| Arquivo | O que mostra |
|---|---|
| `01-escolha-destino.png` | dois planos ativos da pessoa, um rádio por plano referenciado por **médium + início (12/10/2026 e 31/08/2026)** e “0 cumpridas de 4”; nada foi escrito |
| `02-destino-confirmado.png` | banner “acrescentado ao plano com … iniciado em 12/10/2026” + botão “Abrir agenda deste plano” |
| `03-conflito-medio.png` | plano ativo de **outro** médium: explica o conflito, aponta o plano permitido, não criou plano paralelo |
| `04-recusa-formulario-preservado.png` | segunda sessão na mesma data recusada com data, pessoa e médium ainda preenchidos |
| `05-agenda-plano-concluido.png` | agenda do plano “Concluído pelo cumprimento”, sem oferta de acrescentar sessão |
| `06-ficha-pessoa-concluida.png` | ficha da pessoa com a situação Concluído (distinta de Cancelado) |
| `07-destino-1366.png` / `07-destino-412.png` | mesma tela sem rolagem horizontal do documento em 1366, 1024, 768 e 412 — a tabela rola dentro do próprio container |
| `08-agenda-oferece-reposicao.png` | agenda do plano com uma prevista cancelada vencida: aviso “1 sessão prevista não foi cumprida … sem substituta”, o texto que distingue reposição de sessão extra e o `<select>` com a opção **Reposição** disponível (nada gravado ainda) |
| `09-reposicao-agendada-na-agenda.png` | depois do POST com `tipo=reposicao`: banner “Reposição agendada”, a linha nova aparece no mês dela marcada `· reposição`, a coluna Tipo do Histórico diz “Reposição”, e a oferta some (vaga coberta) |
| `10-vaga-reaberta-apos-cancelar-reposicao.png` | cancelar a reposição ainda pendente: a vaga reabre, o aviso volta e a opção fica disponível de novo — sem tocar em `sessoes_adicionais` |

Total: **11 PNGs**, 44 verificações (`aprovadas=44 falhas=0` no runner, §13.1).

## 15. Aplicação no DEV (porta 9000, serviço existente)

**Backup durável antes de qualquer migração/restart** — `docs/tasks/backups/task018-dev-2026-10-26/` (gitignore, contém dados reais) espelhado em `~/Documentos/backup-shamballa/task018-dev-2026-10-26/`, sha256 **iguais** nas duas cópias:

| Arquivo | sha256 |
|---|---|
| `shambala_pre_task018_dev.dump` (`pg_dump -Fc`, 374.069 B) | `37592da278103337e4d229f0bf19f782fb95f3de048ca75716c4da1aa30a9022` |
| `shambala_pre_task018_dev.sql` (1.162.113 B) | `ef2e472810c1ff126a3ad8c920d85177e32b883d28c87cb9b539e012dbb81955` |

**Restauração conferida** (não só o hash): o `.dump` foi restaurado no banco efêmero `shambala_verifica_backup_t018` e lido com o mesmo script da baseline (`baseline_dev.py --db …`). Conteúdo idêntico ao DEV de origem — `planos_tratamento 1912 / md5 533913a7…`, `agendamentos 2980 / md5 1cda8bfa…`, checkins 2920, pessoas 678, esquemas 9 e 6 colunas com os mesmos md5 (`docs/tasks/evidencias/TASK_018/backup_restaurado.txt`). O banco de conferência foi descartado em seguida.

**Baseline antes** — `docs/tasks/evidencias/TASK_018/dev_antes.txt`: planos 1912 (alta 17 / ativo 1866 / cancelado 29), sessões 2980 (agendado 231 / cancelado 315 / faltou 840 / realizado 1594), checkins 2920 (2198 realizadas), pessoas 678, planos de 1 sessão 1801, `plano_pessoas` 1912, esquemas de `planos_tratamento` (9 colunas) e `agendamentos` (6) sem nenhuma coluna nova.

**Posse do processo provada antes de reiniciar:** listener da 9000 = pid **1984421**, `/.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 9000`, cwd `/nc/projetos/shambala`, usuário `claudio`, log do próprio serviço em `docs/tasks/evidencias/TASK_016/dev-startup-task016c.log`. O arquivo `/tmp/shambala_dev.pid` estava **obsoleto** (3363620, processo morto) — a posse veio do socket, não do arquivo. Uma segunda instância do projeto existia na porta 9021 (pid 1284936, `SHAMBALA_MODO_TESTE=1`, `.env.test`, token `t010c-…` de 24/10) — é resto de execução de teste de outra sessão, **não** serve o DEV nem toca o banco `shambala`; não foi tocada por esta tarefa (registrada para limpeza pelo dono).

**Restart (somente o serviço existente da 9000):** `SIGTERM` no pid do listener, guarda fail-closed (não relançou se a porta continuasse ocupada — a porta ficou livre), relançamento com a **mesma linha de comando**, agora com log em `docs/tasks/evidencias/TASK_018/dev-startup-task018.txt`. pid **1984421 → 2387618 → 2389867** (segunda subida proposital, para provar idempotência contra o schema já migrado). `/tmp/shambala_dev.pid` atualizado para 2389867. Ambos os logs: `Application startup complete`, **0** ocorrências de error/traceback/exception. Migração rodou no startup (`_migrar` idempotente).

**Baseline depois** — `dev_depois.txt` e `dev_depois_2.txt` (segunda subida, **byte a byte idênticas** entre si), comparação em `dev_comparacao.txt`:

> **O que os md5 “idênticos” comparam, dito sem rodeio:** o hash de conteúdo é
> calculado sobre **as colunas históricas** de cada tabela — em
> `planos_tratamento`: `medium_id, sessoes_total, sessoes_realizadas,
> data_inicio, concluido, frequencia, status, sessoes_com_passe`; em
> `agendamentos`: `plano_id, data, status, requer_passe, encaixe`. **Não** é
> hash de todas as colunas da tabela ampliada: as colunas novas
> (`sessoes_previstas`, `sessoes_adicionais`, `regra_conclusao`,
> `concluido_em/por`, `conclusao_origem`, `conclusao_desfeita_em/por`,
> `adicionada`) foram **preenchidas com metadado** por esta entrega
> (backfill `sessoes_previstas = sessoes_total`, demais com o padrão), então um
> hash que as incluísse mudaria por definição e não diria nada sobre os fatos.
> A prova é portanto: *os fatos históricos não mudaram*; o metadado novo é
> conferido à parte, na contagem logo abaixo e no esquema (9→17 e 6→7 colunas).
> Nem “idêntico” nem “byte a byte” quer dizer *tabela inteira*: o que o script
> compara é o subconjunto listado acima, e é isso que §19 traz explicitado. No
> complemento (§19.5) a tabela `agendamentos` foi de **7 → 8 colunas** pela
> adição de `repoe`, com baseline e comparação próprias
> (`dev_antes_repoe.txt`, `dev_depois_repoe.txt`, `dev_depois_repoe_2.txt`,
> `dev_comparacao_repoe.txt`); os fatos das duas subidas continuam os mesmos.

| Fato | Antes | Depois |
|---|---|---|
| `planos_tratamento` | 1912 linhas, md5 de conteúdo `533913a7…` | **mesmas 1912, mesmo md5** |
| `agendamentos` | 2980 linhas, md5 `1cda8bfa…` | **mesmas 2980, mesmo md5** |
| status de planos | alta 17 / ativo 1866 / cancelado 29 | idêntico — **0** planos `concluido` |
| status de sessões | agendado 231 / cancelado 315 / faltou 840 / realizado 1594 | idêntico |
| checkins / pessoas / `plano_pessoas` | 2920 (2198 realizadas) / 678 / 1912 | idênticos |
| planos de 1 sessão (“avulso”) | 1801 | idêntico |
| demais 12 tabelas (financeiro, vendas, permissões, dias de atendimento, médiuns) | — | **contagens idênticas** antes e depois |
| esquema `planos_tratamento` | 9 colunas, md5 `16c043c3…` | **17 colunas**, md5 `8fe38096…` |
| esquema `agendamentos` | 6 colunas, md5 `1a3651cb…` | **7 colunas**, md5 `7a50b7c5…` |

(Nota: a tabela acima é a conferência da **primeira** aplicação. O complemento pedido depois (§19.5) acrescentou `agendamentos.repoe` e levou essa tabela a **8** colunas, com baseline e comparação próprias.)

Estado dos metadados novos no DEV: `sessoes_previstas` preenchida em 1912 (0 NULL, backfill = `sessoes_total`), `sessoes_adicionais=0` em todos, `regra_conclusao=1` em **0** (todo o existente ficou legado), `status='concluido'` 0, `concluido_em` 0, `agendamentos.adicionada=1` 0. **Nenhum plano histórico foi fechado, reaberto ou reclassificado pela migração nem pelas duas subidas** — exatamente a fronteira prospectiva da decisão 5/§11.

Smoke anônimo após cada subida: `/` 303→`/menu`, `/menu` 303→`/login`, `/login` 200, e `/agenda`, `/dia`, `/cadastros/mediuns`, `/cadastros/permissoes`, `/cadastros/mediuns/1/planos`, `/cadastros/mediuns/1/planos/1/agenda`, `/biblioteca`, `/financeiro` todos 303→`/login` — nenhum acesso anônimo novo.

**Limitação honesta da aplicação:** a conferência autenticada das telas novas no DEV não foi feita por quem implementou (não há credenciais do dono aqui); o que se provou no DEV é schema, fatos e acesso anônimo. O comportamento de tela foi provado no ambiente descartável com Chromium (§14) na mesma árvore de código.

## 16. Roteiro curto de conferência (dono / Juliana)

**A. No DEV, sem gravar nada** (a tela de destino escreve somente depois de confirmar — provado em K5/RB4/navegador):
1. Entrar com perfil que veja a Agenda; abrir `/agenda`, escolher um dia e uma pessoa que **tenha plano ativo** (ex.: a pessoa do incidente).
2. Em “Novo atendimento”, escolher o médium do plano e uma data de atendimento e enviar **sem** confirmar nada: deve aparecer “Confirmar destino do atendimento” com o nome do atendida, o médium e a data de início do plano — nunca “plano 1799”.
3. Repetir com o médium de **outro** plano ativo da pessoa: deve dizer que há plano ativo com outro médium e apontar o plano permitido (botão “Abrir agenda deste plano”), sem criar plano em segredo.
4. **Não** clicar em “Confirmar e acrescentar sessão” — a conferência aqui é só de apresentação. (Clicar gravaria uma sessão real.)
5. Abrir a agenda de um plano e a ficha da pessoa: os planos existentes aparecem como antes (a regra nova não alcança o legado).
6. Abrir num celular (412 px): a tabela de destinos rola dentro de si, a página não rola na horizontal.
7. Abrir a agenda de um plano que tenha sessão prevista em `faltou` ou `cancelado` sem substituta (medido: 743 planos ativos nessa situação, §19.5): deve aparecer o aviso “… sessão prevista não foi cumprida … sem substituta”, a distinção entre **reposição** e **sessão extra** e o seletor com a opção “Reposição”. Só ler não grava. A coluna **Tipo** do Histórico deve diferenciar Prevista / Extra / Reposição — em todo o DEV hoje só existe “Prevista”, porque `adicionada` e `repoe` estão em 0.
8. Abrir `/agenda/medium` e buscar um atendida com plano ativo: o quadro de situação deve dizer se há vaga a repor **sem** oferecer nada no lugar errado. **Não** clicar em “Lançar”: aqui a conferência também é só de apresentação.

**B. Com dados fictícios, no ambiente descartável** (nada toca o DEV): `python3 tests/test_conclusao_navegador_task018.py` — semear fixtures `T018N`, dirige Chromium, produz os PNGs de §14 (incluído o da reposição) e remove tudo ao final; ou `python3 tests/test_conclusao_planos_task018.py` para os dezessete contratos de serviço/rota. Os dois rodam pelo runner: `SHAMBALA_SUITES="test_conclusao_planos_task018.py test_conclusao_navegador_task018.py" bash tests/rodar_testes.sh`.

**C. Ciclo completo, se quiser ver a conclusão acontecer** (use uma pessoa fictícia criada para teste): plano de 5 sessões pelo caminho habitual → realizar 4 e deixar a 5ª **faltar/cancelar** → a agenda do plano passa a oferecer reposição → agendar como **reposição** e realizar → a situação vira “Concluído (pelo cumprimento)” com `sessoes_adicionais = 0` (a quota original foi cumprida, não ampliada). Acrescentar sessão nesse plano concluído é recusado; um novo atendimento para a mesma pessoa cria plano **novo**.

## 17. Proposta de revisão do histórico (**não executada**)

Nada desta seção foi aplicado. Medido no DEV em 26/10/2026, somente leitura:

| Situação | Quantidade |
|---|---|
| planos `ativo` que **já estariam** cumpridos pela regra (realizadas ≥ previstas, sem sessão `agendado`) | **1155** |
| planos `ativo` com realizadas ≥ total mas **com** sessões `agendado` pendentes | 39 |
| planos `ativo` sem nenhuma sessão registrada | **0** |
| planos `ativo` que nunca tiveram uma realização | 611 |
| pessoas com **mais de um** plano `ativo` | 370 |
| planos `ativo` em total | 1866 (1801 deles de 1 sessão — o “avulso”) |

O que isto diz do incidente: não são “planos sem sessões” (zero planos assim no banco inteiro). São planos cumpridos ou encerrados na prática que nunca receberam alta, e uma pessoa que acumulou 9 ativos porque cada atendimento avulso cria um plano fino. A conclusão automática prospectiva resolve o futuro; o passado continua precisando de decisão humana.

Proposta, em três passos, para homologação posterior e execução separada:
1. **Rótúlos por critério, sem escrita**: relatório somente leitura dos 1155 (por médium e data de início, com realizadas/previstas e a última sessão) para a Juliana marcar cada um como *concluído administrativo*, *alta clínica* ou *ainda em curso*. A diferença importa: `concluido` ≠ `alta` (§11).
2. **Escrita por lote auditado**, só depois da marcação, com `regra_conclusao` deixado em 0 para que o histórico nunca conclua sozinho e `conclusao_origem` preparada para receber uma origem de revisão manual (o campo já existe; nenhuma origem manual é emitida hoje).
3. **Os 39 com sessão pendente e os 611 sem realização** exigem decisão clínica própria — encerrar, dar alta ou reprogramar; nenhum é tocado por regra automática.

Enquanto isso, os 9 planos da pessoa do incidente permanecem como estão: continuam bloqueando a criação de novo atendimento pela proteção de plano único, e a tela agora explicita isso em referências humanas e aponta a agenda do plano permitido (correção da mensagem reaproveitada da investigação anterior, sem “plano 1799”).

## 18. Status, desvios e pendências

**Status: complemento do dono implementado, testado e aplicado no DEV** (K13–K16 na suíte de serviço/rota, cenário `n_reposicao` no navegador, runner completo com as 28 suítes `rc=0` e 0 pulados — §13.1; segunda migração idempotente nas duas subidas, fatos idênticos, `agendamentos` 7→8 colunas, serviço da 9000 reiniciado e somente ele — §19.5). Continua **não homologada pelo dono**: a conferência operacional (§16) é de quem decide; produção **intocada**, sem push e sem deploy (a autorização da TASK_017 não se estendeu a esta tarefa).

Da primeira entrega, permanece: implementada e testada (K1–K12), aplicada no DEV com backup conferido (§15), não homologada.

Achado durante os testes, corrigido: `POST /dia/atendimento/{id}/desfazer` capturava `ReavaliarRecusado` **dentro** do `with conectar()`, e o commit do final do bloco publicava as escritas parciais (contador −1 e linha de volta a `agendado`) exatamente quando a operação devia ser recusada “sem efeitos parciais”. Agora há `conn.rollback()` explícito (`rotas/dia.py:728`); K10 cobre a recusa com `atendimento_realizado=1` e contadores intactos.

Desvio registrado no commit: a árvore continha, **ainda não commitadas no `main`**, as mudanças da TASK_017 (`rotas/auth.py`, `rotas/permissoes.py` — de onde `guarda_modulos` é importado pelas telas de plano —, `semear_permissoes.py`, `templates/menu.html`, `tests/test_recepcao_planos.py`). Sem elas o código desta tarefa não importa nem roda. Foram registradas em commit próprio, anterior, rotulado TASK_017; o commit da TASK_018 leva os arquivos desta tarefa, incluindo `rotas/mediuns.py` e `templates/mediuns/planos.html`, cujas linhas de gate da Recepção e de status concluído não são separáveis por arquivo — está dito na mensagem do commit. A documentação da TASK_017 (tarefa e evidências) ficou de fora: pertence ao fechamento daquela tarefa, com a homologação pendente.

Pendências que não são desta tarefa: homologação da TASK_017; decisão sobre os 9 planos da pessoa do incidente e os 1155 legados (§17) — agora também visíveis como **1155 vagas a repor** em 743 planos ativos (§19.4/§19.5), que a tela oferece e ninguém é obrigado a usar; publicação em produção (autorização específica).

Instância órfã da porta 9021: pid **1284936** registrado na §15 como resto de teste de outra sessão. Esta tarefa **não a encerrou** (pertence ao dono, e encerrar processo alheio não é limpeza de recurso próprio) — foi apenas conferido que ela não é o serviço da 9000 e que esta entrega usou somente a 9000 e o banco selado de teste.

## 19. Complemento pedido pelo dono antes de encerrar (26/10)

Quatro itens, todos aceitos como falha real desta entrega — não como esclarecimento. Dois eram lacuna de comportamento (1 e 2), dois eram texto que dizia mais do que provava (3 e 4).

### 19.1 Item 1 — os quatro criadores de plano, provados pelos caminhos reais

Procura por `INSERT INTO planos_tratamento` no código de aplicação dá exatamente **quatro** criadores, e os quatro já gravavam `sessoes_previstas` e `regra_conclusao=1`:

| Criador | Arquivo:linha | Prova (rota real) |
|---|---|---|
| `POST /agenda/novo` sem plano ativo | `rotas/agenda.py:410` | K13 · 1 |
| `POST /agenda/medium/lancar` (caso 3, sem plano em nenhum médium) | `rotas/agenda.py:807` | K13 · 2 |
| `POST /dia/fraterno/{checkin}` (acolhimento) | `rotas/dia.py:1470` | K13 · 3 |
| `POST /cadastros/mediuns/{id}/planos/novo` | `rotas/mediuns.py:386` | K13 · 4 |

Nenhum caminho precisou de correção — o que faltava era a **prova por rota**, que não existia (K6 tocava só o primeiro). `K13` agora percorre os quatro POSTs autenticados, para pessoas distintas, e em cada um confere: um único plano criado (`_planos_da_pessoa`), `regra_conclusao=1`, `sessoes_previstas == sessoes_total`, `sessoes_adicionais=0`, agenda gerada com o previsto e — a parte que importa, porque coluna preenchida não é comportamento — **cumprido o previsto pelas transições reais, o plano conclui** com `conclusao_origem='cumprimento_automatico'`. Reenvio do acolhimento (mesmo checkin+médium+início) continua num plano só, com a quota intacta.

### 19.2 Item 2 — a reposição existia só no serviço; a recepção não tinha como usá-la

Diagnóstico: `acrescentar_sessao(..., tipo="reposicao")` era aceito pelo serviço, mas **nenhum formulário ou POST o emitia** — as três telas passavam `tipo="adicao"` fixo. Na prática, toda reposição que a recepção marcava virava **extensão** (`sessoes_adicionais +1`), elevando a obrigação e impedindo a conclusão para sempre. O teste direto do serviço (K2) não mostrava isso porque exercia justamente o caminho que ninguém percorre.

Contrato agora explícito:

| Peça | O que é |
|---|---|
| `agendamentos.repoe INTEGER NOT NULL DEFAULT 0` | a linha **substitui** uma prevista que não aconteceu. Linhas antigas ficam 0; nada é reinterpretado |
| `vagas_a_repor(conn, plano_id)` (serviço:54) | previstas (`adicionada=0`, `repoe=0`) em `faltou`/`cancelado` **menos** reposições em curso ou cumpridas (`repoe=1` em `agendado`/`realizado`). É o que autoriza repor |
| `TIPOS_SESSAO = ("adicao", "reposicao")` | contrato fechado; valor fora disso → `'tipo_invalido'`, **sem escrever** |
| `'sem_vaga_para_repor'` | reposição sem vaga é recusada — sem vaga, o acréscimo é extensão e aumentaria a obrigação |
| `cancelar_sessao` de uma reposição pendente | reabre a vaga (a linha fica `cancelado` com `repoe=1`: histórico preservado, contadores intactos) |

Onde a recepção escolhe: (a) **agenda do plano** — `templates/mediuns/agenda.html`: o formulário passou a ter “Tipo de sessão”, com a opção *Reposição de sessão não cumprida* oferecida **somente** quando `vagas_a_repor > 0`, precedida de aviso em linguagem corrente (“1 sessão prevista não foi cumprida … agendar como reposição cumpre a obrigação original; agendar como sessão extra acrescenta uma obrigação nova”); (b) **`/agenda/novo`** — a página de confirmação traz o tipo **por plano** (`tipo_<id>`), porque com vários planos uma escolha global seria adivinhação; (c) **`/agenda/medium` lançar** — a JSON `GET /agenda/medium/situacao` passou a reportar `vagas_repor` e o formulário mostra o seletor quando o plano do médium tem vaga. Os três POSTs revalidam no servidor (a tela não é autorização) e devolvem a recusa **na tela**: `ver_agenda` aceitou `erro`/`msg` e o template os renderiza — até aqui os redirects da agenda do plano respondiam com `?erro=` que nenhuma página mostrava.

Prova (rota real, navegador real):

| Teste | O que mede |
|---|---|
| K14 | sem falta a tela **não** oferece reposição; falta pela rota abre vaga (`vagas_a_repor` 0→1) e a oferta aparece; o POST do formulário grava `repoe=1, adicionada=0`, `sessoes_adicionais` segue 0, previstas seguem 5; cumpridas as 4 previstas + a reposição o plano **conclui em 5** (não em 6), com a falta preservada como falta |
| K15 | sem vaga, o POST recusa com a explicação **renderizada na página de destino** e nada é gravado (3 linhas, 0 extras, 0 reposições); `tipo=invencao` recusado sem escrita; cancelar a reposição reabre a vaga e a tela volta a oferecê-la; cancelar a **prevista** também abre vaga sem encurtar a obrigação |
| K16 | `/agenda/novo`: a página de confirmação traz `tipo_<plano>` e o POST confirmado repõe; com **dois** planos ativos a escolha é por plano — o escolhido repõe, o outro segue com a vaga dele aberta e nenhum dos dois vira extensão; `/agenda/medium/lancar`: a JSON informa `vagas_repor=1`, o POST com `tipo=reposicao` repõe, o mesmo POST sem vaga recusa na tela, e **sem** o campo o comportamento habitual (extensão) permanece |
| navegador `n_reposicao` | 16 verificações no Chromium: cancelar a prevista vencida na tela → aviso e opção aparecem → escolher reposição e enviar → mensagem “Reposição agendada”, linha marcada “· reposição” no mês dela, opção some (vaga coberta) → cancelar a reposição → vaga reabre e a opção volta. Evidências `08-`, `09-`, `10-` |

### 19.3 Itens 3 e 4 — texto que dizia mais do que provava

- **§11**: a linha “extra cancelada antes de realizar” atribuía a revogação do `+1` a uma “prática da TASK_017”. Falso: a TASK_017 liberou a agenda dos planos à Recepção e não define revogação de extensão. Reescrita com o fundamento correto — **decisão específica desta tarefa** (§1), que preserva o histórico (a linha fica `cancelado`; só o contador líquido recua).
- **§15**: os md5 “idênticos” foram explicados: o hash de conteúdo cobre **as colunas históricas** (`planos_tratamento`: medium_id, sessoes_total, sessoes_realizadas, data_inicio, concluido, frequencia, status, sessoes_com_passe; `agendamentos`: plano_id, data, status, requer_passe, encaixe — exatamente as colunas que `baseline_dev.py` seleciona), **não** a tabela ampliada. Houve preenchimento de metadado novo, então um hash com as colunas novas mudaria por definição. O que se prova com eles é “os fatos não mudaram”; o metadado é conferido à parte.

### 19.4 Limites e achados registrados (não corrigidos aqui)

- `registrar_falta` (`rotas/mediuns.py:652`) e a marcação em bloco de faltas do dia (`rotas/dia.py:287–298`) escrevem `status='faltou'` direto, sem `reavaliar`. Consequência medida: um plano que já cumpriu a quota e cuja última linha pendente vira falta continua `ativo` até a próxima transição do serviço. **Nada é fechado por suposição** — é conclusão que tarda, não conclusão errada. Ficou fora deste complemento porque mexer no caminho das faltas toca a regra das duas faltas → `cancelado`, que a §12 declara intocada; registrado como pendência para decisão do dono.
- `GET /agenda/medium/situacao` escolhe `planos[0]` sem ordenação quando a pessoa tem vários planos ativos (comportamento anterior à TASK_018): a informação da tela pode dizer respeito a um plano diferente do que o POST vai usar. O POST, ele sim, decide com revalidação por plano e pelo `tipo_<id>` escolhido, então não há escrita errada. Registrado, não alterado.
- A coluna `repoe` é **metadado de linha**, não fato clínico: a migração a cria com 0 em todas as 2980 sessões existentes; nenhuma sessão histórica foi reclassificada como reposição.
- Medido no DEV, somente leitura, depois da migração: as **1155** vagas a repor existem hoje **só em planos legados** (`regra_conclusao=0`) — 743 planos `ativo` (1009 vagas), 28 `cancelado` (136), 9 `alta` (10); ainda não há nenhum plano prospectivo no DEV (`regra_conclusao=1` em 0). Consequência visível: a oferta de reposição aparece nessas agendas, o POST grava a linha de reposição e o plano **não** conclui por isso (a regra prospectiva continua sem alcançá-lo) — é reposição de obrigação, não gatilho de fechamento. Se o dono preferir suprimir o aviso nos legados, é decisão de tela, não de contrato, e não foi tomada aqui.
- A suíte de reprodução do incidente (`tests/repro_incidente_agenda_plano_sem_sessoes.py`) **não** está em `SUITE_PADRAO`: é rodada à parte (`SHAMBALA_SUITES=repro_incidente_agenda_plano_sem_sessoes.py bash tests/rodar_testes.sh`) — 5/5 (RB1–RB5) na árvore deste complemento, log em `repro_incidente.txt`. O uso no cabeçalho do arquivo dizia o nome sem `.py` e por isso falhava; corrigido.

### 19.5 Aplicação do complemento no DEV (segunda migração)

Mesmas regras da §8: somente o serviço existente da porta 9000, nenhum fato histórico alterado, nada de produção, nenhum push.

**Backup durável antes da migração** — `docs/tasks/backups/task018b-repoe-dev-2026-10-26/` espelhado em `~/Documentos/backup-shamballa/task018b-repoe-dev-2026-10-26/`, sha256 **iguais** nas duas cópias (arquivo `SHA256.txt` no original, `SHA256.mirror.txt` no espelho):

| Arquivo | sha256 |
|---|---|
| `shambala_pre_task018_repoe_dev.dump` (`pg_dump -Fc`, 376.338 B) | `93d245f00db7934aa52525276aa11c8516f29143725913ef49aa3562e8719d91` |
| `shambala_pre_task018_repoe_dev.sql` (1.209.308 B) | `6f471d75e0fb2b207b09bedb224ffeb738394d6985502cc39e049f21448e230f` |

**Restauração conferida, não só o hash**: o `.dump` foi restaurado no banco efêmero `shambala_verifica_backup_t018b` e lido com o mesmo `baseline_dev.py --db …`. Conteúdo idêntico ao DEV de origem — `planos_tratamento 1912 / md5 533913a7…`, `agendamentos 2980 / md5 1cda8bfa…`, checkins 2920, pessoas 678, `planos_tratamento` 17 colunas e `agendamentos` **7** (sem `repoe` — exatamente o estado pré-migração). Registro honesto em `backup_restaurado_repoe.txt`: o `pg_restore` ignorou 2 erros, ambos de `ALTER DEFAULT PRIVILEGES FOR ROLE postgres`, que o papel `shambala` não pode executar; nenhuma tabela, linha ou coluna depende dessas frases (daí os hashes idênticos). O banco de conferência foi descartado em seguida (`dropdb`, confirmado por `pg_database` = 0).

**Posse provada antes de reiniciar**: listener da 9000 = pid **2389867** (`.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 9000`, cwd `/nc/projetos/shambala`, usuário `claudio`, sem `SHAMBALA_MODO_TESTE`/`SHAMBALA_ENV_FILE` no ambiente → banco efetivo `shambala`), e o `/tmp/shambala_dev.pid` batia com o socket. A posse veio do socket, não do arquivo.

**Duas subidas, com guarda fail-closed** (a mesma da primeira aplicação: `SIGTERM`, espera, **não relança se a porta continuar ocupada**; a porta ficou livre nas duas vezes): pid **2389867 → 2549348 → 2552123**. Logs `dev-startup-task018b.txt` e `dev-startup-task018b-2.txt`: `Application startup complete`, **0** ocorrências de error/traceback/exception. `/tmp/shambala_dev.pid` atualizado para o pid vivo.

**Baseline antes/depois** — `dev_antes_repoe.txt`, `dev_depois_repoe.txt`, `dev_depois_repoe_2.txt` (segunda subida **byte a byte idêntica** à primeira), comparação e leitura em `dev_comparacao_repoe.txt`:

| | Antes | Depois (duas subidas) |
|---|---|---|
| `planos_tratamento` | 1912 linhas, md5 `533913a7…` | **mesmas 1912, mesmo md5** |
| `agendamentos` | 2980 linhas, md5 `1cda8bfa…` | **mesmas 2980, mesmo md5** |
| status de planos / sessões | alta 17 / ativo 1866 / cancelado 29 — agendado 231 / cancelado 315 / faltou 840 / realizado 1594 | idênticos, **0** planos `concluido` |
| checkins / pessoas / `plano_pessoas` / planos de 1 sessão | 2920 (2198) / 678 / 1912 / 1801 | idênticos |
| esquema `planos_tratamento` | 17 colunas, md5 `8fe38096…` | **17 colunas, mesmo md5** (nada novo nesta etapa) |
| esquema `agendamentos` | 7 colunas, md5 `7a50b7c5…` | **8 colunas**, md5 `b1745708…` — apenas `repoe INTEGER NOT NULL DEFAULT 0` |
| metadado | `adicionada=1` em 0 | `adicionada=1` 0, **`repoe=1` 0**; `regra_conclusao=1` 0, `concluido_em` 0, `sessoes_previstas` NULL 0 |

Os md5 de conteúdo cobrem as colunas históricas (§15, item 4 do pedido): não são hash da tabela ampliada. O que muda entre as duas tabelas acima é **esquema**, e nada mais.

Smoke anônimo após a segunda subida: `/` 303→`/menu`, `/menu` 303→`/login`, `/login` 200, e `/agenda`, `/dia`, `/cadastros/mediuns`, `/cadastros/permissoes`, `/cadastros/mediuns/1/planos`, `/cadastros/mediuns/1/planos/1/agenda`, `/biblioteca`, `/financeiro` todos 303→`/login` — nenhum acesso anônimo novo.

**A limitação da §15 continua valendo**: a conferência autenticada das telas no DEV é do dono (não há credenciais dele aqui); o que se provou no DEV foi schema, fatos, idempotência da migração e acesso anônimo. O comportamento de tela com login foi provado no ambiente descartável com Chromium (§14).



