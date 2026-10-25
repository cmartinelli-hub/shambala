# TASK_012 — Reestruturação financeira: execução noturna por etapas

Status: **parcialmente concluída** — etapas §2 a §6 com implementação e prova,
aplicadas no DEV (§9); regressão completa verde, 22 suítes incluindo os percursos
de navegador (§8). Pendentes e nomeados no §12: **FIN-09 sob revisão** (a janela
entre o commit e a gravação do arquivo não é eliminável sem arquitetura nova —
há detecção, compensação e recuperação no startup), **FIN-06 contido por
bloqueio** com reembolso pendente, homologação autenticada do dono, os 43
recebimentos de fiado do legado sem destino, decisão sobre vendas/sangrias
legadas, FIN-08 histórico e publicação. Relatório completo, evidências e
limitações em `docs/investigacoes/TASK_012_RESULTADO_REESTRUTURACAO_FINANCEIRA.md`
(§3.3 concorrência, §6.5–6.6 FIN-09, §8 navegador, §9.5 previsto × preservado,
§10 roteiro de conferência, §11 herdado × retomada, §12 estado final).
Originalmente pronta para execução pelo Qoder.
Autorização: implementar, testar e documentar autonomamente as etapas abaixo durante a noite. Entregar resultado revisável ao dono pela manhã. Não é autorização para publicar nem para declarar o financeiro inteiro homologado.
Referências: TASK_005 (diagnóstico FIN-01–09), TASK_006–011 e respectivos relatórios/revisões. Conferir o estado real antes de editar; o resumo desta ordem não substitui a investigação do código.

## Resultado esperado

Avançar substancialmente na consistência do financeiro existente: impedir duplicações por reenvio, tornar mensalidades únicas por competência, rastrear pagamentos de fiado e créditos, alinhar consultas e concluir correções técnicas de integridade que caibam no escopo. Manter a interface utilizável e oferecer ao dono um roteiro curto com mudanças concretas para conferir pela manhã.

Trabalhar em sequência, com commits locais por etapa validada e documentação consolidada nesta TASK_012. Não criar outras tasks numeradas automaticamente. O tamanho desta tarefa não autoriza mudanças sem prova, reparação de fatos antigos por aproximação ou redução de testes para obter um resultado verde.

## 0. Limites obrigatórios e modo de execução

1. Ler normas locais/AGENTS.md se houver, SHAMBALA_CONTEXTO.md, diagnóstico e relatórios TASK_005–011, inclusive ressalvas finais da TASK_010 e correção de cache da TASK_011. Mapear pendências reais antes de implementar; não assumir que todo achado permanece igual.
2. Não acessar produção, nem por leitura, SSH, backup remoto, deploy ou push. Não enviar mensagens, e-mails, cobranças ou notificações. Não criar integrações bancárias ou serviços pagos.
3. Não alterar nginx, TLS, Cloudflare, systemd global ou infraestrutura. DEV continua no serviço existente HTTP da porta 9000. Nenhuma nova instância persistente.
4. Testes exclusivamente em PostgreSQL nativo descartável, sem Docker, com destino efetivo validado pelas proteções TASK_010 antes de startup, seed e limpeza. Não testar guardas apontando para DEV/produção; usar sentinelas descartáveis.
5. Não alterar credenciais, usuários, permissões ou sequences do DEV para testes; não pedir senhas no chat. Credenciais fictícias somente em ambiente descartável.
6. Nenhuma operação de demonstração em fatos reais: não receber mensalidade, criar venda, quitar dívida, estornar, fechar sessão ou consumir crédito real no DEV. Nenhuma limpeza destrutiva de histórico para acomodar constraints novas.
7. Preservar alterações alheias. Não resetar/stashar/commitar trabalho de outras tarefas. Isolar trabalho quando necessário sem perder arquivos ou exigir nova instância persistente.
8. Decisões técnicas reversíveis dentro desta ordem são do executor. Se uma regra de negócio não estiver autorizada aqui ou no histórico verificável, registrar questão e continuar etapas independentes; não inventar decisão do dono nem parar toda a noite por uma dúvida localizada.
9. Qualquer incidente: interromper o caminho inseguro, identificar efeitos e documentar; não tentar encobri-lo com comparação a produção, mudança de senha ou restauração automática do DEV. Não executar comandos de encerramento com PID vazio/zero ou grupos alheios.

## 1. Levantamento inicial e plano concreto

Registrar HEAD, árvore/alterações, rotas, tabelas e serviços afetados. Elaborar uma matriz curta etapa → contrato atual → alteração → teste → dependências. Distinguir fonte de verdade por domínio: obrigação, pagamento recebido, alocação, crédito, evento da sessão e lançamento financeiro.

Confirmar preservação das regras já entregues: preço no servidor/Decimal, cancelamento seguro TASK_007, eventos/contagem TASK_008, isolamento TASK_010 e consulta/cache TASK_011. Não refazer essas funções em serviços paralelos.

Preparar provas dos defeitos em descartável antes de corrigi-los. Seguir a ordem 2 → 3 → 4 → 5 → 6. Etapa sem decisão suficiente pode ser entregue como diagnóstico bloqueado, com o restante executado. Não usar prazo noturno como motivo para declarar etapa incompleta concluída.

## 2. Idempotência das operações financeiras

### Contrato

Uma intenção confirmada pelo operador deve produzir um único efeito, mesmo com clique duplo, reenvio após perda da resposta, timeout ou duas requisições simultâneas. Duas intenções novas legítimas, mesmo com conteúdo idêntico, continuam sendo duas operações.

Cobrir finalização de venda PDV, doação avulsa, mensalidade individual/lote, pagamento integral/parcial de fiado, adiantamento de crédito e sangria/transferência. Preservar idempotência existente de cancelamento/fechamento. Inventariar rotas alternativas para não deixar uma entrada desprotegida.

- Gerar chave da tentativa antes do envio e mantê-la durante reenvios; uma nova operação explícita recebe nova chave. Aplicar nas telas reais, não somente em teste de serviço.
- Persistir identidade com unicidade no banco, escopo de operação/ator pertinente e associação ao documento/resultado. Chave não substitui autenticação, RBAC ou validação.
- Mesma chave e mesmo conteúdo semântico → mesmo resultado/documento ou indicação clara de operação já registrada, sem novo efeito. Mesma chave com conteúdo divergente → conflito sem escrita. Canonicalizar os campos relevantes; preço e valores continuam calculados pelo servidor.
- Reservar/confirmar chave e efeitos na mesma transação, com tratamento correto de concorrência. Falha deve permitir tentativa segura; não deixar chave consumida sem documento ou documento confirmado sem proteção.
- Revisão de preço não conclui venda nem consome tentativa como sucesso; definir como confirmar cesta reprificada sem conflito e provar esse percurso.
- Caminhos sem chave não podem contornar a proteção das novas telas. Adaptar clientes internos/testes existentes; recusar confirmação sem identidade quando necessária com orientação legível, em vez de inventar chave nova a cada POST.
- Não apagar identidades recentes para facilitar testes nem reciclar chaves de operações confirmadas. Documentar retenção e compatibilidade, sem nova rotina destrutiva automática.

### Provas

Para cada família de operação: primeiro envio, reenvio byte a byte após resposta perdida, concorrência com a mesma chave, mesma chave/conteúdo divergente e nova chave/conteúdo idêntico. Conferir fatos, estoque, obrigação, crédito, sessão e financeiro pertinentes. Não usar ID criado após INSERT como prova de identidade prévia da tentativa.

## 3. Mensalidades por trabalhador e competência

- Reconstruir geração atual, inclusive startup, geração manual, cadastro/reativação e rotas de baixa. Competência deve ter representação inequívoca mês/ano, sem depender de texto da descrição.
- Garantir no banco e no serviço no máximo uma obrigação vigente por trabalhador/competência, sem impedir registro correto de recebimento histórico ou eventual modelo de anulação existente. Documentar a chave e quais linhas ela abrange.
- Repetição e concorrência da geração não duplicam cobrança. Startup não cria mês/valor inesperado nem cobra inativos por regra inventada. Preservar regra existente de elegibilidade e valor; se ambígua, não alterá-la silenciosamente.
- Diagnosticar duplicidades antigas antes da constraint. Nunca excluir, fundir ou escolher vencedora automaticamente, especialmente quando houver pagamento/vínculos. Em caso de conflito histórico, preferir manter a implementação pronta/testada e registrar impedimento de migração; não aplicar schema pela metade sem plano explícito.
- Receber uma mensalidade ou lote atualiza exatamente as obrigações selecionadas, preserva competência e registra conta/forma/sessão corretas. Uma seleção repetida não gera novo dinheiro. Transação do lote deve ter política explícita, sem baixa parcial oculta.
- Separar data de competência, vencimento e pagamento na consulta; não converter datas antigas em valores inventados. Status deve decorrer dos fatos válidos, preservando histórico e limitações conhecidas.
- Mostrar ao operador mensagens simples: já gerada, já recebida, período inválido ou conflito histórico, sem detalhes SQL.

Provar geração repetida/concorrente, dois trabalhadores na mesma competência, mesmo trabalhador em competências diferentes, limites de ano/mês, inativação segundo regra existente, baixa individual/lote e rollback. Testar instalação nova e atualização com duplicidades fictícias sem perda de fatos.

## 4. Fiados, pagamentos parciais e crédito rastreável

### Regra autorizada

Manter as possibilidades existentes de quitar dívida e antecipar crédito, mas substituir inferências por vínculos duráveis. Uma entrega de dinheiro tem um recebimento; pode quitar uma ou várias dívidas e deixar excedente como crédito. Consumo posterior de crédito não é nova entrada financeira.

- Registrar alocações explícitas do recebimento para cada venda/dívida e o valor destinado a crédito, com unicidade/FKs e invariantes compatíveis com o modelo. Total alocado + excedente = recebido, sem centavos perdidos.
- Quando o operador escolhe dívidas, respeitar a seleção e mostrar a distribuição antes de confirmar. Se o fluxo já distribui automaticamente, manter sua regra comprovada; na ausência dela, permitir seleção explícita em vez de inventar prioridade cronológica silenciosa.
- Uma dívida parcialmente paga deve mostrar valor original, recebido/alocado e saldo restante. Não marcar fiado_pago como verdadeiro enquanto existir saldo devedor; tratar o campo legado como compatibilidade, não fonte concorrente de valor.
- Pagamento inferior à dívida deve abatê-la quando destinado a ela; não apresentar valor apenas lançado como crédito como se tivesse quitado a dívida. Se o comportamento antigo era diferente, explicar a mudança prospectiva e preservar fatos anteriores.
- Excedente gera crédito vinculado à mesma entrega, sem segunda receita. Adiantamento sem dívida tem origem financeira e extrato. Crédito consumido é vinculado à venda, travado sob concorrência e não pode ficar negativo.
- Extrato por trabalhador com saldo devedor e crédito separados, movimentos identificáveis e links para venda/recebimento comprovados. Não apresentar saldo líquido como se extinguisse automaticamente obrigações sem alocação.
- Valores históricos sem alocação comprovada continuam com limitação explícita. Não distribuir retroativamente pelo nome, valor, data aproximada ou FIFO presumido; não criar receita para crédito antigo.
- Preservar bloqueios seguros do cancelamento TASK_007 para dívida paga/parcial/crédito ou origem ambígua. Adaptar a avaliação à existência das novas alocações para não permitir estorno indevido.

Provar dívida 40 com pagamentos 15 + 25, pagamento 50 destinado a dívida 40 com crédito 10, adiantamento e consumo parcial, duas dívidas selecionadas, concorrência pagamento/pagamento e consumo/consumo, repetição pela mesma chave, última escrita falhando e origem histórica sem backfill. Conferir dinheiro recebido, lançamentos financeiros, sessão, dívida e crédito independentemente.

## 5. Consultas, saldos e experiência integrada

- Reusar a apresentação TASK_011 e suas definições; não substituí-la por nova interface genérica. Preservar asset_url e correções responsivas, inclusive números grandes.
- Mapear telas/relatórios de mensalidades, fiados, extratos, comprovantes e resumo do caixa que leem campos alterados. Unificar regras de consulta em serviços comuns quando necessário, sem reescrever todas as páginas sem motivo.
- Diferenciar obrigação, recebimento, alocação e consumo de crédito. Não contar venda fiado como receita recebida, nem somar evento ao lançamento financeiro correspondente.
- Resultado global exclui transferências internas comprovadas; movimento por conta reflete entradas/saídas reais. Não fabricar contrapartidas históricas ou esconder anomalia como saldo conferido.
- Valores em Decimal/NUMERIC com política de arredondamento existente. Novas datas com tipos próprios, apresentação brasileira e fuso America/Sao_Paulo. Novos filtros por competência/data de pagamento/vencimento devem explicitar qual data usam.
- Manter os vínculos e retorno ao mesmo recorte de consulta; permissões conferidas em URL direta e POST. Terminal de presença permanece sem acesso financeiro.
- GETs não geram cobrança nem movimentação por efeito colateral. Identificar e remover acoplamento de leitura/escrita nos caminhos alterados sem mudar as regras de geração autorizadas.

Entregar pelo menos mudanças observáveis nas telas de mensalidades e fiados: competência clara, saldo devedor/pagamentos parciais, destino do recebimento e extrato rastreável. Sem expor jargão técnico ao operador.

## 6. Integridade técnica complementar, com escopo limitado

Rever FIN-08/09 no código atual. Implementar apenas correções focais sustentadas por reprodução:

- Constraints/FKs/checagens e trilha de autoria/origem nos fatos novos abrangidos por esta ordem; não inventar a tabela auditoria_eventos para satisfazer fixture antiga. Não apagar documento original para representar correção.
- Arquivo de compra gravado antes do commit: alinhar ciclo do arquivo e da transação para evitar órfão em falha e referência quebrada em sucesso. Preservar arquivos anteriores; não executar varredura destrutiva. Provar falhas de escrita/commit e limpeza restrita ao arquivo da tentativa, com caminhos validados.

Se o problema de arquivo exigir arquitetura ou componentes novos desproporcionais, entregar diagnóstico/reprodução e manter fora da implementação. A prioridade são etapas 2–5.

## 7. Exclusões e decisões pendentes

- Não implementar agora reembolso de fiado já pago, estorno após sessão fechada ou reabertura de caixa. São decisões específicas futuras; manter recusa clara, sem inventar saída financeira.
- Não manter ou alterar vendas/sangrias legadas alegando autorização inexistente. Estado observado: implementação atual as permite; decisão do dono continua pendente. Não converter, fechar nem reparar sessão legada real. Novos recebimentos em legado continuam bloqueados.
- Não desenvolver conciliação, importação de extratos, cartões/taxas, recorrências ou orçamento nesta tarefa.
- Não tratar teste antigo quebrado como homologação financeira. Pode corrigir seu harness para os contratos reais se isso ampliar cobertura pertinente; preservar identificação do que era defeito de fixture e do que era defeito de produto. Não reduzir assertivas para obter PASS.

## 8. Estratégia de testes e evidências

1. Primeiro comprovar isolamento e identidade da conexão/HTTP da execução corrente; sentinelas de destinos divergentes intactas. Não reutilizar log velho como prova.
2. Para cada etapa: teste que demonstra o defeito anterior e contrato final exercitado por rotas HTTP reais com consulta ao banco. Provas negativas significativas, rollback e concorrência determinística; não depender só de sleep ou resultado de uma corrida casual.
3. Preservar suítes TASK_006–011 e acrescentar as novas ao runner apropriado. Executar validações focadas durante implementação e runner completo ao final do código consolidado. Informar RC, casos/asserções por unidade, falhos e pulados corretamente; pulado não é PASS.
4. Se aparecer regressão, corrigir a causa dentro do escopo ou marcar a etapa como incompleta. Não aceitar runner verde sobre outro banco, outra árvore ou com casos financeiros omitidos.
5. Navegador desktop e smartphone em descartável, com dados fictícios: dupla confirmação/reenvio, mensalidade/lote, parcial de fiado, excedente como crédito, extrato e navegação. Testar números grandes, cache CSS e retorno ao filtro. Capturas sem dados reais/senhas.
6. Evidências em docs/tasks/evidencias/TASK_012/, preferindo relatório/log sanitizado commitável e manifesto SHA-256. Registrar commit e diff da árvore efetivamente testada; se dirty, preservar patch/identidade suficiente para reproduzir e provar equivalência ao commit final, sem exigir uma nova regressão apenas por commit de docs.
7. Não repetir todo o runner após mero ajuste documental. Repetir apenas quando código, ambiente ou achado novo invalidar a evidência.

## 9. Migração e aplicação no DEV

Tratar migrações como operação separada dos testes. Testar schema novo/existente, idempotência e diagnóstico de conflitos em descartável antes de considerar o DEV.

- Preparar SQL de auditoria somente leitura e coletar baseline próprio do DEV sem expor dados sensíveis. Não usar produção para completar baseline. Comparar conteúdo dos fatos relevantes além de agregados, incluindo credenciais/permissões sem publicar valores.
- Antes de DDL/startup que possa escrever no DEV: backup recuperável, hash e restauração validada em banco descartável; registrar erros de restore e limites com precisão.
- Aplicar no DEV existente somente etapas completas/testadas, cuja migração preserve fatos e não exija escolha de regra histórica. Se qualquer duplicidade/ambiguidade impedir transição segura, não tentar reparar automaticamente: deixar código pronto e registrar bloqueio concreto.
- Não servir código final incompatível com schema parcial. Garantir conjunto coerente aplicado, ou manter serviço anterior. Não remover constraints/guardas para conseguir subir.
- Reiniciar somente o serviço da 9000, conferir comando/árvore/modo normal, log e endpoints sem credenciais. Comparar baseline e relatar efeitos previstos/observados do startup. Nenhuma demonstração financeira real.
- A ausência de login do dono não impede validar no descartável nem justifica modificar usuários. Homologação autenticada do DEV fica com ele pela manhã.

## 10. Entrega pela manhã

Relatório nesta ordem e complemento em docs/investigacoes/TASK_012_RESULTADO_REESTRUTURACAO_FINANCEIRA.md contendo:

| Etapa | Estado a informar |
|---|---|
| Idempotência | operações protegidas e não protegidas, chave/resultado, provas concorrentes |
| Mensalidades | unicidade/geração/baixa, conflitos históricos e efeitos de migração |
| Fiados e créditos | alocações, parciais, excedente, extrato e limites históricos |
| Consultas e relatórios | telas alteradas e regras unificadas, diferenças visíveis |
| Integridade complementar | correções implementadas e motivos das pendências |
| DEV | o que foi efetivamente aplicado, backup, baseline, serviço e limitações |

Incluir arquivos/rotas/migrações, causas e testes antes/depois, resultados do runner, evidências de navegador, commits locais e recursos próprios encerrados. Não usar "concluída" para item apenas documentado ou testado por chamada simulada.

Preparar roteiro de no máximo 10 passos para o dono: primeiro consultas sem escrita no DEV; depois cenários fictícios claramente separados e com pré-requisitos, identificação dos registros duráveis e resultado esperado. Não orientar uso de dívida real ou exclusão de histórico. Não deixar serviço descartável persistente apenas para demonstração.

Atualizar SHAMBALA_CONTEXTO.md e matriz FIN-01–09: resolvido, parcial ou aberto, com justificativa. Manter pendências de homologação, legado, reembolsos e publicação explicitamente separadas. Não atribuir decisões ao dono a partir de texto do executor.

Commits locais por etapa validada, sem push; somente arquivos desta tarefa. Ao fim, status real: concluída no escopo implementado ou parcialmente concluída com bloqueios. Mover para docs/tasks/done/ somente quando os critérios implementáveis estiverem atendidos; se parcial, manter em docs/tasks/ com relatório e próximos passos precisos.

## Critério final

Entregar o máximo de etapas completas com integridade comprovada, sem ampliar regras de negócio por iniciativa do executor. O sucesso da noite é código consistente, evidência reproduzível e resultado que o dono consegue conferir; não uma declaração genérica de financeiro pronto. Produção segue fora do escopo.
