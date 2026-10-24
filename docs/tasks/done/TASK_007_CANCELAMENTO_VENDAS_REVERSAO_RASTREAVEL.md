# TASK_007 — Cancelamento de vendas com reversão consistente e rastreável

Status: implementada, testada e aplicada no DEV (migração aditiva via restart do serviço existente da porta 9000, com backup recuperável e restauração validada; commits locais, sem push). Aguarda conferência do dono pelo roteiro de homologação com dados fictícios. Produção não acessada.
Referências: TASK_005 (FIN-02 e relação com FIN-06); TASK_006 (precificação no servidor, implementação a56fb57 e retrabalho e2cd0dc). Confirmar o código atual.

## Objetivo

Substituir o cancelamento genérico que apenas muda o status da venda por operação transacional, idempotente e auditada, revertendo os efeitos que possam ser comprovados. Preservar documentos históricos e impedir cancelamentos parciais ou duplicados.

Não presumir que cancelar uma venda prova devolução de dinheiro ou mercadoria. Nesta etapa, o comando representa anulação integral de uma operação registrada incorretamente, com retorno integral dos itens controlados ao estoque quando aplicável. Devolução parcial, perda/consumo de mercadoria e reembolso bancário são outros fluxos, fora do escopo.

## Ambiente e preparação

1. Ler AGENTS.md, se existir, e normas locais. Registrar TASK_007 em docs/tasks/. Preservar alterações alheias.
2. Trabalhar somente no DEV existente; sem publicação, push, SSH/leitura de produção, nginx, túnel ou instância persistente adicional.
3. Ler o diagnóstico e mapear todas as rotas genéricas/específicas de cancelamento, leitores de status, estoque, crédito, fiado, sessão e fechamento. Confirmar os efeitos reais da venda após TASK_006.
4. Antes de reproduzir escrita, verificar destino tanto da conexão do teste quanto do servidor HTTP. Configuração ausente/divergente aborta antes de seed, startup ou escrita. Não usar a conta administrativa real, trocar senha, copiar hash de produção ou reajustar sequences do DEV.
5. Reproduções/testes exclusivamente em PostgreSQL nativo descartável, sem Docker. Encerrar processos e remover bancos ao terminar. Não executar scripts contra DEV para depois tentar apagar as provas.
6. Antes de migração no DEV, produzir backup recuperável, validar restauração em banco descartável e capturar baseline próprio do DEV, incluindo fatos e vínculos afetados. Comparação com produção não substitui baseline anterior e está fora do escopo.

## Investigação focada antes de implementar

Reproduzir FIN-02 na rota autenticada real, usando produto e venda fictícios. Capturar venda/itens, movimentos e saldo de estoque, sessão e eventuais lançamentos financeiros antes/depois. Demonstrar quais efeitos permanecem indevidamente após o cancelamento antigo.

Construir matriz de elegibilidade a partir dos vínculos realmente disponíveis. O executor está autorizado a implementar os casos seguros abaixo e bloquear explicitamente os demais, sem inventar vínculos históricos por valor/data/nome e sem reconstruir todo o financeiro.

## Política desta entrega

### Casos elegíveis

- Venda concluída, não fiado, vinculada de forma comprovável à sua sessão ainda aberta, sem quitação/estorno/fechamento financeiro posterior incompatível.
- Venda fiado ainda não paga, sem recebimento posterior nem consumo de crédito antecipado, com vínculo comprovável à sessão aberta.
- Caixa de preço aberto nas mesmas condições acima: reverte o efeito da venda na sessão, sem inventar itens de catálogo/estoque ausentes.

Para fiado, fiado_pago=false isoladamente não prova ausência de pagamento: o modelo atual pode transformar recebimentos parciais em crédito sem alocação. Se não for possível comprovar que não houve recebimento/crédito posterior relevante, bloquear o caso. É aceitável bloquear todo fiado nesta etapa se o modelo não oferecer evidência suficiente; informar esse limite e não declarar FIN-06 corrigido.

### Casos bloqueados, sem efeitos parciais

- Sessão já fechada, ausente, de outro caixa ou sem vínculo seguro com a venda.
- Venda fiado paga, com pagamento parcial/recebimento posterior ou com crédito utilizado.
- Documento legado cuja baixa original de estoque, contribuição à sessão ou efeitos financeiros não possam ser determinados com segurança.
- Mercadoria não devolvida, devolução parcial e necessidade de reembolso/estorno financeiro após fechamento.

Exibir motivo claro e orientar tratamento administrativo futuro. Não reabrir sessão, não alterar fechamento histórico, não escolher conta espécie por padrão e não gerar saída financeira genérica para compensar efeitos desconhecidos.

A política preserva casos históricos ambíguos em vez de produzir reversões falsas. Se uma condição segura exigir vínculo novo, adicionar metadados mínimos prospectivos às novas vendas na mesma transação de finalização. Não preencher vínculos antigos por heurística. O relatório deve separar casos atendidos e bloqueados.

## Serviço único e integridade

Centralizar o cancelamento em serviço reutilizado por todas as rotas capazes de cancelar venda elegível.

- Exigir motivo não vazio, confirmação explícita e operador autenticado.
- Cancelamento permitido somente a administrador nesta entrega, aproveitando a autoridade central real do projeto; não presumir ID de grupo fixo fora do padrão vigente. Não ampliar permissões automaticamente. Operador comum continua vendendo, mas não cancela por acesso direto.
- Antes da confirmação, mostrar venda, data, forma, total e efeitos propostos. Nomear como cancelamento integral com devolução dos itens ao estoque quando houver.
- Usar valores/quantidades persistidos na venda e movimentos originais; nunca preços atuais ou parâmetros de estoque/total enviados pelo navegador.
- Manter venda e itens originais; registrar cancelamento, autor, motivo e instante do servidor em America/Sao_Paulo, com precisão de segundos.
- Manter evidência durável dos efeitos revertidos e vínculo aos documentos originais, sem FK CASCADE que apague a prova com a venda.
- Registrar movimento compensatório de estoque vinculado ao cancelamento, com quantidade e saldos anterior/posterior. Não apagar o movimento original nem apenas ajustar saldo sem extrato.
- Restaurar quantidade sobre o saldo atual travado; não sobrescrever com o saldo antigo da época da venda. Produto hoje inativo pode receber a reversão, sem ser reativado. Produto removido/vínculo ausente gera bloqueio seguro.
- Reverter somente a contribuição comprovada daquela venda à sessão aberta. Não recalcular toda a sessão a partir de vendas, pois existem mensalidades/doações/recebimentos misturados ainda não corrigidos (FIN-04).
- Tratar doação vinculada: só desfazer o valor comprovadamente lançado junto daquela operação integral; não tocar doações independentes. Se a relação for ambígua, bloquear, sem inventar regra de reembolso.
- Fiado elegível deixa de compor a dívida atual por status, mantendo documento e histórico. Não alterar créditos ou recebimentos para fazê-lo caber no caso elegível.
- Venda, auditoria, reversão de estoque e ajuste de sessão na mesma transação; erro em qualquer etapa reverte tudo.
- Uma venda só pode produzir um cancelamento efetivo, com unicidade no banco. Repetição sequencial, clique duplo e concorrência retornam Já cancelada e não geram outro efeito.
- Auditar apenas o cancelamento efetivo; repetição não fabrica estornos adicionais.

## Concorrência e caminhos alternativos

Definir ordem de travas compatível com finalização da TASK_006, estoque e fechamento. Não adicionar SELECT FOR UPDATE em ordem inversa sem examinar deadlocks.

Testar cancelamento concorrente com outra venda do mesmo produto e com fechamento da sessão: ou cancela integralmente antes do fechamento ou bloqueia após ele, sem total fechado incoerente. Ajustar as travas do fechamento apenas no mínimo necessário para serializar a operação, sem reformular seu cálculo.

Mapear a rota específica de cancelamento de fiado: ela não pode continuar sendo atalho para cancelar um documento que esta política bloqueia, nem efetuar estorno incorreto conhecido de FIN-06. Encaminhar os casos elegíveis ao serviço único e recusar os demais com mensagem. Não implementar novo reembolso de fiado pago nesta tarefa.

Não considerar uma venda marcada como cancelada no legado como já revertida: responder sem novos efeitos e indicar que não existe prova de reversão quando pertinente. Não reparar automaticamente o histórico.

## Interface, relatórios e comprovante

- Preservar a aparência geral, acrescentando motivo/confirmação e mensagem de resultado ou bloqueio.
- Em venda cancelada, exibir status, motivo, autor e data/hora a quem pode consultar essa venda; manter limites de acesso existentes.
- Comprovante original continua consultável, claramente identificado como cancelado, sem parecer venda válida. Disponibilizar registro simples do cancelamento pelo mesmo mecanismo existente de comprovante/consulta; não exigir impressora física para funcionar.
- Listagens e relatórios operacionais devem excluir canceladas dos totais de vendas realizadas, mas permitir consulta explícita do histórico cancelado. Não mudar fórmulas gerais de saldo financeiro nesta tarefa.
- Verificar que a sessão fechada posteriormente não inclui a venda cancelada elegível. Não criar movimentação financeira adicional quando a venda ainda não havia gerado nenhuma.

## Schema e migração

Adicionar somente os vínculos/metadados necessários para cancelamento/auditoria e unicidade. DDL para banco novo e migração transacional/idempotente para existente. Preservar IDs, valores, snapshots, fotos e documentos antigos. Não fabricar autor/motivo/reversão para cancelamentos anteriores.

Não criar tabela auditoria_eventos vazia só para satisfazer fixture antiga. Reutilizar padrão real ou estrutura mínima de cancelamento documentada. Datas monetárias gerais e modelo de contas ficam fora desta entrega.

## Critérios de aceite e testes

Reproduzir pelas rotas reais, inspecionando banco antes/depois, com valores fictícios conhecidos.

| Cenário | Resultado exigido |
|---|---|
| Venda à vista elegível com vários itens | Estoque devolvido uma vez; sessão ajustada exatamente; documentos preservados |
| Produto repetido na cesta original | Reversão pela quantidade persistida agregada, sem duplicar |
| Produto hoje com outro preço/inativo | Usa fato original; não altera preço/status do produto |
| Outras vendas após a original | Reversão soma no estoque atual, preservando movimentos intermediários |
| Caixa de preço aberto | Sem movimento de estoque fictício; sessão correta |
| Doação comprovadamente vinculada | Efeito integral coerente, sem tocar doação independente |
| Fiado sem recebimentos/crédito e elegível | Dívida atual retirada, sem reembolso ou crédito artificial |
| Fiado pago/parcial/crédito usado ou vínculo incerto | Bloqueio antes de qualquer efeito |
| Sessão fechada/ausente/incompatível | Bloqueio, fechamento e financeiro intactos |
| Cancelamento legado sem prova de reversão | Sem segunda reversão ou reparação silenciosa |
| Repetição e cancelamentos concorrentes | Um registro efetivo e uma reversão |
| Cancelamento × venda / fechamento | Sem baixa perdida, deadlock não tratado ou fechamento incoerente |
| Falha em auditoria/estoque/sessão | Rollback integral, inclusive status da venda |
| Motivo vazio, sem confirmação, venda inexistente | Recusa clara e sem escrita |
| Anônimo, não admin e sessão terminal | Recusa pelo servidor, inclusive rotas específicas |
| Dados adulterados na requisição | Valor/quantidade originais usados, sem confiar no cliente |
| Consulta/comprovante/fechamento posterior | Status claro e totais operacionais coerentes |
| Migração repetida | Histórico, códigos de trabalhadores e dados alheios intactos |

Se fiado precisar ser totalmente bloqueado por falta de vínculos, testar a recusa e substituir a expectativa de êxito correspondente pela limitação explícita, sem remover os demais testes negativos.

Acrescentar testes ao runner e preservar TASK_006, inclusive R15. Executar regressão completa, informar soma correta, aprovados/falhos/pulados e RC. Ferramenta ausente não conta como PASS. Registrar as falhas da suíte financeira antiga separadamente, com causas reais, sem ampliar escopo para repará-la inteira.

Validar no navegador venda fictícia elegível, motivo, confirmação, resultado, repetição, histórico/comprovante e bloqueio de caso inelegível. Sem credenciais DEV, fazer em processo descartável e informar a limitação. Não solicitar senha no chat ou alterar usuários reais.

## Disponibilização no DEV

Conferir que o serviço existente executa a árvore corrigida. Aplicar schema/reinício somente com backup e baseline próprio e conhecimento dos efeitos do startup. Não criar fatos reais para comprovar. Se o ambiente não permitir aplicação segura, entregar a implementação testada e informar o impedimento, sem alegar disponibilidade inexistente.

## Entrega

1. Relatório anexado a esta tarefa: reprodução do FIN-02, matriz de elegibilidade, efeitos originais/reversos, travas e casos bloqueados.
2. Código, migração, testes reproduzíveis e log integral do runner, sem dados sensíveis.
3. Evidência de idempotência/atomicidade e preservação de dados; baseline próprio do DEV antes/depois.
4. Roteiro de homologação usando apenas produtos/vendas fictícios identificados. Não orientar cancelamento de venda real para demonstração.
5. Limitações e achados restantes: FIN-03–FIN-09, especialmente reembolso/fiado pago e sessão fechada; não declarar o financeiro completo homologado.
6. Atualizar SHAMBALA_CONTEXTO.md e o status real. Commit local somente desta tarefa, sem push; mover para docs/tasks/done/ após os critérios atendidos.

Encerrar recursos descartáveis. Produção não acessada. Não publicar nem iniciar a próxima tarefa sem autorização posterior.

---

## Adendo de execução (no DEV) — registrado pelo executor

### T7.1/T7.2 — Reprodução do FIN-02 nas rotas reais

Ambiente: banco descartável `shambala_t007_test` (marcador `_test`), app HTTP dedicado na porta 9013 com `SHAMBALA_MODO_TESTE=1` e `.env.t007` exclusivo. Antes de qualquer escrita o script `tests/repro_task007_fin02.py` prova **os dois destinos** — a própria conexão (`current_database` + trava de isolamento) e o banco do servidor HTTP (linha `[TESTE] Ambiente descartável confirmado` impressa pelo app no startup). Controles negativos executados: log do servidor ausente → `rc=2` sem gravar; destino divergente no log → `rc=2` sem gravar. Nenhum usuário real foi usado, nenhuma senha trocada, nada lido da produção.

Transcript integral: `docs/tasks/evidencias/task007_reproducao_fin02.log`. Fatos demonstrados com produto/venda fictícios (prefixo `T007REP`):

1. **Venda à vista com doação** (total 28,00 + doação 2,00; sessão total_vendas=30,00; estoque baixado 2×Suco+1×Água com movimentos `saida`): `POST /caixa/vendas/{id}/cancelar` → apenas `status='cancelada'`. Estoque segue 8/9, total_vendas segue 30,00, movimento de devolução inexistente.
2. **Fechamento depois do cancelamento**: sessão fecha com `saldo_final=30,00` (inclui a venda cancelada) e **nenhuma** entrada `venda_pdv` em financeiro (o agrupado filtra `status='concluida'`). Fechamento internamente incoerente.
3. **Fiado com pagamento parcial** (venda 12,50; recebido 10,00): os 10,00 viram **crédito antecipado solto** do trabalhador + entrada `recebimento_fiado` em financeiro; `POST /caixa/fiados/{venda}/cancelar` cancela a venda sem nenhum estorno — o recebimento e o crédito ficam órfãos (nexo com a venda destruído; `fiado_pago` era FALSE isoladamente não provava ausência de pagamento).
4. **Relatório de fiados** (`/financeiro/fiados?trabalhador_id=…`): soma todas as linhas fiado **sem filtrar status** — gasto/pendente 15,50 composto apenas por vendas canceladas; a venda cancelada que consumiu crédito antecipado (3,00) nunca mais devolveu o crédito.

Também confirmado no código: `caixa_movimento_id` pode ser **NULL** em vendas gravadas via PDV (a rota aceita sessão 0 e não vincula) — essas vendas não têm efeito em sessão determinável.

### T7.3 — Matriz de elegibilidade (derivada dos vínculos realmente existentes)

Efeitos persistidos na finalização (pós-TASK_006, a56fb57): linha `vendas_pdv` (com `caixa_movimento_id` nullable, `doacao_valor`, `fiado_credito_usado`), `vendas_pdv_itens` com snapshot (só caixas com catálogo), `produtos.quantidade_estoque -=` + `estoque_movimentos 'saida'` (referencia_id=venda_id, saldos explícitos) por item agregado, `caixa_movimentos.total_vendas += (total+doacao)` quando há sessão, débito de crédito antecipado quando `fiado_credito_usado>0`. Venda não gera financeiro na finalização (entrada por forma só no fechamento).

**Metadado prospectivo** (único novo vínculo, gravado na mesma transação de finalização): `vendas_pdv.criada_em TEXT` = instante do servidor em America/Sao_Paulo. Linhas antigas permanecem NULL — nunca preenchido por heurística.

| # | Caso | Decisão | Motivo / evidência usada |
|---|---|---|---|
| E1 | concluída, não fiado, `caixa_movimento_id` aponta sessão `aberto` do mesmo caixa, itens têm movimentos `saida` originais por produto | **Elegível** | Reverte estoque sobre o saldo atual travado (movimento `entrada` compensatório por item agregado) e subtrai `total+doacao` do `total_vendas` da mesma sessão; documentos preservados |
| E2 | caixa de preço aberto (sem itens de catálogo), sessão aberta comprovável | **Elegível** | Só efeito determinável é a sessão; nenhuma movimentação de estoque inventada |
| E3 | venda sem vínculo de sessão (`caixa_movimento_id` NULL) ou sessão fechada/ausente/de outro caixa | **Bloqueado** | Contribuição à sessão indeterminável; não reabrir sessão nem escolher conta por padrão |
| E4 | faltando movimento `saida` original do item (referencia_id=venda_id) | **Bloqueado** | Baixa original de estoque indeterminável (documento legado) |
| E5 | fiado `concluida`, não pago, `fiado_credito_usado=0`, com `criada_em` não nulo, sem entrada "Recebimento fiado — venda #id" e sem movimento de crédito do trabalhador posterior a `criada_em` | **Elegível** | Sai da dívida atual por status (listagens filtram `concluida`), sem mexer em crédito/recebimentos |
| E6 | fiado pago, com pagamento parcial (inclusive crédito solto gerado por FIFO), com crédito consumido ou vínculo temporal indeterminável (legado sem `criada_em`) | **Bloqueado** | FIN-02/T7.2(3) provam que recebimento posterior não é detectável com segurança; nada é "encaixado" no caso elegível |
| E7 | venda já `cancelada` com registro de cancelamento | **Sem efeito** | "Já cancelada" (unicidade no banco); repetição não fabrica estorno nem auditoria nova |
| E8 | venda `cancelada` no legado **sem** registro de cancelamento | **Sem efeito, com aviso** | Não existe prova de reversão; não se repara histórico automaticamente nem se inventa autor/motivo |
| E9 | doação avulsa (`/caixa/doacao`) | **Intocada** | Operação independente; cancelamento de venda nunca a toca |
| E10 | doação dentro da venda (`doacao_valor` persisted) | **Revertida junto** | Mesmo valor somado na finalização à mesma sessão; fica evidenciado no registro |
| E11 | pagamento/estorno financeiro após fechamento, devolução parcial/perda de mercadoria, reembolso bancário | **Fora do escopo → bloqueado** | São outros fluxos (FIN-03–FIN-09); esta entrega não os implementa |

Política de execução: serviço único; admin via `e_admin` (padrão central real, sem IDs fixos novos); motivo não vazio + confirmação explícita; valores exclusivamente persistidos (nunca do navegador); travas venda → produtos (ORDER BY id) → caixa_movimentos FOR UPDATE status='aberto'; tudo numa transação; `vendas_pdv_cancelamento` com UNIQUE(venda_id) e FK RESTRICT (prova durável: motivo, autor, instante, valor revertido, evidência de estoque). Fechamento passa a travar a sessão (FOR UPDATE) para serializar com o cancelamento. Relatórios: totais operacionais excluem `cancelada`; histórico cancelável por consulta explícita.

### T7.4 — Implementação (serviço único, schema, rotas, telas)

**`servico_cancelamento.py`** (novo, única porta de saída do cancelamento):

- `avaliar(conn, venda_id)` devolve os efeitos comprováveis ou levanta `CancelamentoBloqueado` com a lista de motivos legíveis. Gates executados na ordem: já cancelada → `JaCancelada(com_prova)` (prova pela existência de `vendas_pdv_cancelamento`); status ≠ `concluida`; ausência de vínculo de sessão (`caixa_movimento_id` NULL — "não está vinculada a uma sessão"); sessão inexistente/"não existe mais"/de "outro caixa"/"já foi fechada"; por item de catálogo: produto sumido ou `SUM(quantidade)` dos movimentos `saida` com `referencia_id=venda_id` ≠ quantidade persistida ("A baixa original de … não é comprovável"); fiado: `fiado_pago` ("consta como pago — estorno/recebimento é outro fluxo (FIN-06)"), `criada_em` nulo ("sem instante de registro"), entrada financeira "Recebimento fiado — venda #id", e qualquer `fiado_credito_mov` do trabalhador posterior à venda — comparação com `::timestamp` e **margem de 1 s**, porque `timestamp(0)` arredonda e um pagamento do mesmo instante poderia escapar (política conservadora: sem comprovação, bloqueia).
- `cancelar(conn, venda_id, motivo, atendente_id)`: motivo vazio → recusa; trava `vendas_pdv FOR UPDATE` → `produtos` em `ORDER BY id FOR UPDATE` → `caixa_movimentos FOR UPDATE` (mesma ordem da finalização TASK_006 — sem deadlock); reavalia sob trava; devolve estoque sobre o **saldo atual travado** com movimento `entrada` "Cancelamento venda #<id> — devolução ao estoque" (inativo recebe a devolução sem ser reativado, preço não muda); subtrai `total + doacao` do `total_vendas` da sessão (só `status='aberto'`); `UPDATE … SET status='cancelada' WHERE status='concluida'`; insere a prova em `vendas_pdv_cancelamento` com instante `(CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp(0)::text`, `valor_revertido` e `estoque_revertido` (JSON). Tudo numa transação: qualquer exceção vazando do `with conectar()` reverte inclusive o status (demonstrado em teste com injeção de falha).

**Schema (aditivo, idempotente)** — `banco.py`: coluna `vendas_pdv.criada_em TEXT` via `_migrar` (escrita na INSERT de finalização em `rotas/caixa.py:248-260`, timestamp SP a segundos; linhas antigas permanecem NULL) e tabela `vendas_pdv_cancelamento` (UNIQUE(venda_id), FKs RESTRICT para venda e atendente — apagar a venda com prova existente é barrado, a prova sobrevive ao cancelamento repetido). Nenhuma `auditoria_eventos` fictícia criada.

**Rotas** (`rotas/caixa.py`): `GET /caixa/vendas/{id}/cancelar` renderiza a prévia (`templates/caixa/cancelar_venda.html`: venda, data, forma, total, efeitos propostos ou painel "Este cancelamento não pode ser executado por aqui" com os motivos); `POST` mesmo caminho exige sessão autenticada (anônimo → 303 /login), `e_admin` (não admin → 303 `?erro=sem_permissao`), motivo não vazio e `confirmar=1` sem confiar em nenhum valor de estoque/total do corpo; sucesso → 303 com banner; bloqueio → 200 com a prévia e os avisos; `JaCancelada` → 200 distinguindo "já foi cancelada com reversão registrada" de legado "não existe prova de reversão associada"; venda inexistente → 404 HTML; sessões de terminal recusadas como nas demais rotas de caixa. `/caixa/fiados/{venda}/cancelar` deixou de ser atalho: sem motivo/confirmação redireciona para a tela única e, autenticado o `POST`, delega ao mesmo serviço (êxito → `?cancelado_ok=`; bloqueio devolve o motivo) — nenhum caso bloqueado consegue cancelamento pela rota específica. Fechamento (`POST /caixa/fechar`) agora trava a linha da sessão `FOR UPDATE` antes de calcular, serializando com o cancelamento; nenhuma fórmula sua foi reformulada.

**Telas/consultas**: `vendas.html` mostra "Cancelada" com data (`data_hora_br`), autor e motivo quando há prova, "cancelamento antigo, sem prova de reversão" quando não; link Cancelar só para admin; "Total do dia" exclui canceladas. `comprovante.html` de venda cancelada exibe faixa "V E N D A C A N C E L A D A" com o registro do cancelamento (ou a nota de legado) e o texto do recibo traz o mesmo aviso. `fiados.html` (caixa) aponta o Cancelar para a tela única e o banner de sucesso fala dos efeitos comprováveis (a frase antiga "crédito usado foi estornado" era falsa — nunca foi estornado). Relatório `/financeiro/fiados`: linhas canceladas continuam listadas, marcadas "Cancelada (não somada)", e saem de gasto/pendente/pago (`rotas/financeiro.py`).

### T7.5 — Testes e runner

Suíte nova `tests/test_cancelamento_vendas.py`, acrescentada ao `tests/rodar_testes.sh`: **25 testes pelas rotas HTTP reais** no ambiente descartável da tarefa (banco `shambala_t007_test`, app dedicado 9013, prova dos **dois destinos** antes de qualquer escrita — conexão do script e a linha `[TESTE] Ambiente descartável confirmado` do servidor), cobrindo cada linha da matriz de aceite: M1/M1b elegível multi-item com devolução, extrato e comprovante; M2 itens repetidos agregados; M3 produto inativo/com outro preço recebe devolução sem mudar cadastro; M4 outras vendas depois da original — devolução soma no saldo atual, movimentos intermediários preservados; M5 caixa de preço aberto sem movimento de estoque inventado; M6/M7 doação vinculada revertida junto, avulsa intocada, fiado elegível sai da dívida por status sem tocar crédito; M8a–M8e todos os fiados ambíguos bloqueados **antes** de qualquer efeito (pago, parcial→crédito solto, crédito consumido, legado sem instante, recebimento nominal); M9 sessão fechada/ausente/alheia bloqueada com fechamento intacto; M10 legado cancelado sem prova → sem segunda reversão; M11 repetição e clique duplo concorrente → um único efeito (unicidade no banco); M12 cancelamento × venda concorrente do mesmo produto; M13 cancelamento × fechamento (antes reverte, depois bloqueia, nunca incoerente); M14 falha em etapa derruba a transação inteira; M15 motivo vazio/sem confirmação/venda inexistente; M16 anônimo, não-admin e terminal recusados pelo servidor; M17 corpo adulterado usa os fatos persistidos; M18 `criada_em` nasce com a venda, legado nunca preenchido, migração repetida inofensiva; M19 rota de fiado delega e recusa o resto; **B1 navegador real (Playwright)**: cancelar pela tela com motivo e confirmação, conferir banner, histórico, comprovante e a prévia de um caso bloqueado. Isolamento: tudo prefixo `T007-` fictício; a suíte reabastece o estoque dos seus próprios produtos (100) por teste e limpa cancelamentos → financeiro → fixtures ao final; nenhuma credencial real tocada.

Runner completo `bash tests/rodar_testes.sh` — **RC=0, 12 suítes, 226 testes, 0 pulados**: 27+12+8+10+19+18+18+24+21+11+33+25 = 226 (a soma por suíte está nas linhas "N/N testes passaram" do log). TASK_006 preservada (33/33, inclusive R15). Log integral: `docs/tasks/evidencias/task007_runner_completo.log`. Conferência do banco de desenvolvimento antes/depois: `pessoas=678 checkins=2918` — **inalterado**. A suíte financeira antiga continua como registrado na TASK_006 (fixture que limpa `auditoria_eventos` inexistente; não foi consertada aqui nem encoberta — não se criou a tabela vazia para satisfazê-la).

### T7.6 — Aplicação no DEV (com backup recuperável e baseline próprio)

Sequência executada no serviço **existente** da porta 9000 (mesma pasta, mesmo comando; sem nova instância, túnel, nginx ou leitura de produção):

1. **Baseline antes**: `trabalhadores=70, presença=20, códigos=70, ativos=70, retirados=0, correções=0, vendas_pdv=1859, soma de vendas=R$ 25.886,75, sessão aberta=1 (total_vendas=R$ 35.101,70), produtos=39, movimentos de estoque=3.017, itens=3.090, caixas=58, financeiro=862 (soma R$ 69.446,04)`; tabela/coluna do cancelamento ainda ausentes.
2. **Backup recuperável**: `pg_dump` do `shambala` local → `docs/tasks/backups/shambala_pre_task007_20261024_1429.sql.gz` (sha256 `b8bb35dda187bcceeb2218795f27c1ebe2c81faeff2a0dc50a02341744c56603`).
3. **Restauração validada** antes de qualquer mudança: o dump foi restaurado num banco descartável próprio (`shambala_t007_restauracao`, marcador `_test`) e as contagens conferidas uma a uma contra o DEV — **idênticas** (vendas=1859 soma=25886.75 itens=3090 caixas=58 abertos=1 total_vendas=35101.70 produtos=39 est_movs=3017 financeiro=862 fin_soma=69446.04 trabalhadores=70 presenca=20 correcoes=0). Banco de validação removido em seguida.
4. **Efeitos do startup conhecidos**: `criar_tabelas()`/`_migrar()` desta entrega só adicionam a coluna `criada_em` e a tabela `vendas_pdv_cancelamento` — sem UPDATE, sem toque em sequências, senhas ou documentos.
5. **Reinício controlado** do mesmo serviço (pid 3204032 → 3875652): `GET /login` → 200.
6. **Baseline depois**: todos os números acima **inalterados**, mais `tab_cancelamento=1, cancelamentos=0, col_criada_em=1, criada_em_preenchidas=0` — schema aditivo aplicado, zero fato real tocado, zero venda nova. Nenhuma venda real foi cancelada para demonstração; nenhuma credencial alterada.

### Roteiro de homologação no DEV (somente dados fictícios)

Cancelamento é restrito a administrador — usar conta de admin já existente do DEV (esta entrega não cria usuário nem muda senha/planilha de permissões):

1. Criar produto fictício em `/caixa/produtos`: **"Homologacao TASK-007 (teste)"**, preço R$ 12,50, estoque 10.
2. No PDV do caixa com sessão aberta, vender 2 unidades (R$ 25,00): conferir estoque 8, sessão `+25,00` e movimentos `saida` da venda.
3. Em `/caixa/vendas`, abrir **Cancelar** na venda fictícia: a prévia deve listar os efeitos propostos (devolver 2 unidades, subtrair R$ 25,00 da sessão). Tentar sem motivo → recusado; preencher motivo e confirmar → banner "Venda #… cancelada. Os efeitos comprováveis foram revertidos".
4. Conferir: estoque volta a 10 com movimento `entrada` "Cancelamento venda #… — devolução ao estoque"; `total_vendas` da sessão menos 25,00; linha do histórico com "Cancelada" + data/autor/motivo; comprovante com a faixa "V E N D A   C A N C E L A D A"; "Total do dia" sem a venda.
5. Reenviar o cancelamento (botão de novo / mesma URL) → "já foi cancelada", nada muda (uma única prova no banco).
6. Abrir a tela de cancelar de **uma venda real qualquer** apenas para **ler** a prévia: caso legado cancelado ou sessão incompatível deve exibir bloqueio/motivo e **nenhum** botão de confirmar deve ser acionado em venda real — cancelamento demonstrativo é só da venda fictícia.
7. **Inativar** o produto fictício (não excluir — a prova do cancelamento e o snapshot histórico precisam permanecer).

### Limitações verdadeiras

- **Fiado**: só sai da dívida atual o fiado integralmente comprovável (não pago, sem recebimento nominal, sem movimento de crédito posterior, com `criada_em`). Fiado pago/parcial/crédito consumido e documentos legados sem instante ficam **bloqueados com motivo legível e zero efeitos** — nada de FIN-06 (reembolso/estorno) foi implementado; **não declarar FIN-06 corrigido**.
- **Sessão fechada**: bloqueio definitivo nesta entrega — não se reabre sessão, não se relança financeiro; reembolso após fechamento é FIN-03/FIN-04.
- **Legado**: vendas antigas sem `criada_em`/sem prova permanecem irrepresentáveis no fluxo novo; nunca preenchidos por heurística; canceladas do legado seguem "sem prova de reversão".
- Não há recálculo da sessão a partir das vendas (mistura mensalidades/doações — FIN-04); a subtração usa exatamente o valor persistido da venda (+ doação dela).
- Devolução parcial, perda/consumo de mercadoria e reembolso bancário são outros fluxos (E11), fora desta entrega.
- A validação em navegador real (B1) rodou no processo descartável com credencial de teste local — o DEV não tem credencial usável por política (sem senha no chat, sem criar usuário), então o roteiro acima fica com o dono.

### Encerramento de recursos

Após a aplicação no DEV: app dedicado da porta 9013 finalizado, banco `shambala_t007_test` removido, `.env.t007` removido, banco de validação `shambala_t007_restauracao` já removido no item 3. O DEV retorna ao estado de sempre: um serviço, porta 9000, HTTP. **Produção não acessada** nesta tarefa (nenhum SSH/leitura/deploy); nenhum push. Recursos descartáveis criados por esta entrega não permanecem ativos.
