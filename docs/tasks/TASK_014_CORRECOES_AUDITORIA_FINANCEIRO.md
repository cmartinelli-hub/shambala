# TASK_014 — Corrigir isolamento da idempotência e concluir a revalidação financeira

Status: **concluída no escopo, com ressalvas** — AUD-01 corrigido e provado,
AUD-02 com evidência histórica marcada e regressão consolidada (23 suítes,
rc=0), provas pendentes da §3 reproduzidas (inclusive morte real de processo),
aviso/procedimento do FIN-09 entregue e DEV aplicado com baseline idêntico.
Ressalva central: FIN-09 é protocolo mitigado, **sem atomicidade** banco/arquivo.
Relatório: `docs/investigacoes/TASK_014_CORRECOES_AUDITORIA_FINANCEIRO.md`.
Referências: TASK_013, parecer requer correções, commit informado da910b9; TASK_012 e adendos finais. Confirmar arquivos/commits reais antes de editar.

## Objetivo

Resolver AUD-01, substituir a evidência inválida AUD-02 por regressão consolidada e completar as verificações não reproduzidas na TASK_013. Tratar AUD-03 como avaliação do protocolo de recuperação e seu limite operacional, sem exigir atomicidade inexistente entre PostgreSQL e arquivos nem implementar outbox nesta tarefa.

Entregar código corrigido, evidências independentes e parecer delimitado sobre aptidão para homologação do dono. Não ampliar o financeiro para novas funcionalidades.

## Ambiente e autorização

- Ler normas locais/AGENTS.md se houver, SHAMBALA_CONTEXTO.md, TASK_006–013 e relatórios/adendos, especialmente docs/investigacoes/TASK_013_AUDITORIA_FINANCEIRO_ANTES_HOMOLOGACAO.md. Registrar esta ordem em docs/tasks/ e preservar trabalho alheio.
- Implementação e aplicação controlada no DEV existente estão autorizadas nas condições abaixo. Não acessar produção, inclusive leitura/SSH. Sem push, publicação ou infraestrutura nova.
- DEV: HTTP porta 9000, mesmo serviço, sem nginx/TLS/túnel/segunda instância persistente. Não alterar usuários, senhas, permissões ou fatos reais para testes.
- Testes em PostgreSQL nativo descartável, sem Docker, com configuração/identidade efetiva e token confirmados antes de startup/seed/limpeza. Conferir segurança do runner e teardown antes de executá-los.
- Encerrar apenas recursos próprios. Não testar guardas ou recuperação escrevendo no DEV/produção. Não enviar cobranças/notificações nem solicitar credenciais do dono.

## 1. AUD-01 — isolamento de chave por operador e operação

### Defeito apontado

operacao_idempotencia armazena autor_id, mas reconhecer() e reservar() consultam apenas chave. Outro atendente autenticado pode reapresentar a chave/corpo e receber resultado/documento da tentativa original. Entropia da chave não substitui a validação de contexto.

### Contrato de correção

1. Autor e operação esperados vêm do contexto autenticado e da rota/serviço, nunca de campo livre do cliente. Conferir ambos antes de devolver resultado persistido ou aceitar reserva já existente.
2. Preservar unicidade global da chave existente, recusando sua reutilização em outro contexto sem revelar destino, documento, identidade do autor ou conteúdo original. Não converter colisão de contexto em uma nova operação silenciosa sob a mesma chave.
3. Conferir todas as transições: reconhecer, reservar/exigir_reserva, confirmar, anular/desfazer e eventual caminho alternativo. Não proteger só o reconhecimento inicial deixando a corrida na reserva contornar a validação.
4. Mesma chave/autor/operação/conteúdo semântico → mesmo resultado e zero novos efeitos. Mesmo contexto com conteúdo divergente → conflito sem escrita. Chave nova/conteúdo idêntico → nova operação legítima quando o estado permitir.
5. Autenticação e RBAC continuam obrigatórios antes de reconhecimento/resposta. Usuário sem acesso à rota, anônimo ou terminal não recebe resultado só porque conhece uma chave.
6. Definir comportamento de registros existentes com autor/operação nulos ou inválidos: recusa segura, sem atribuir autoria retroativa ou apagar identidade. Não alterar fatos/tentativas históricos para fazer teste passar.
7. Integrar todas as rotas financeiras protegidas da TASK_012: PDV, doação, sangria, fiado individual/lote, crédito, mensalidades, lançamento avulso, transferência, compra multipart e venda de livro. Preservar revisão de preço, respostas de repetição e protocolo de compra anulada.
8. Sem schema novo previsto: os campos já existem. Se necessário, justificar alteração mínima e compatibilidade antes de aplicar no DEV; não criar migração de identidade histórica por aproximação.

### Provas obrigatórias

- Dois operadores autorizados: A conclui; B reenvia chave/corpo e recebe recusa sem documento/destino alheio, com banco inalterado; A continua podendo repetir sua tentativa.
- Mesmo operador, mesma chave, outra operação: recusa sem escrita, inclusive quando os corpos forem estruturalmente semelhantes.
- Concorrência com a mesma chave entre contextos diferentes: um vencedor no máximo; perdedor não recebe resultado alheio. Exercitar tanto chave nova quanto já concluída.
- Mesmo contexto em concorrência e após resposta perdida continua produzindo um único efeito; chave/conteúdo divergente recusa. Conferir efeitos financeiros, estoque, dívida/crédito e sessão pertinentes.
- Anônimo, sem permissão e terminal; tentativa anulada; registro histórico sem contexto confiável; compra com arquivo e PDV com revisão de preço.
- Testar serviço central e rotas HTTP reais. Matriz de chamadas deve demonstrar que nenhuma rota deixou de passar autor/operação. Prova antes/depois do achado em cópia isolada, sem enfraquecer assertivas antigas.

## 2. AUD-02 — evidência consolidada e falhas de limpeza

O log runner_completo_r5_FINAL_verde.txt tem erro de FK no teardown apesar de rc=0. A limpeza foi corrigida posteriormente e houve reexecução focal relatada. Não apagar nem reescrever o log antigo: marcá-lo como evidência histórica inválida para sucesso integral e vincular sua substituição.

- Conferir a correção posterior no código: ordem das FKs, remoção por IDs/chaves próprios, sentinela alheia e ausência de resíduos. Exceção em teardown precisa afetar RC da suíte e do runner.
- Provar falha deliberada de limpeza produzindo RC não zero em execução isolada, e limpeza correta preservando sentinela e removendo somente registros próprios. Não usar contagem agregada como única prova de preservação alheia.
- Após o código final desta tarefa, executar o runner integral com destino comprovado, incluindo suítes financeiras e navegador. Log canônico sanitizado e commitável com token, identidade do banco/servidor e árvore testada reproduzível.
- Registrar RC, aprovados/falhos/pulados e contagens por suíte distinguindo casos e asserções. Não ocultar erros num sumário verde; mensagens esperadas de testes negativos devem ser identificadas como tal.
- Se precisar adaptar teste por nova assinatura autor/operação, manter a pergunta e as assertivas de negócio; não usar o endurecimento como motivo para eliminar cenários.

## 3. Completar as provas pendentes da TASK_013

Reproduzir em ambiente isolado e documentar evidência, em vez de remeter apenas à alegação da TASK_012:

### Mensalidade sem índice

Remover o índice somente no banco descartável e simular conflito histórico que impede recriá-lo. Duas conexões com barreira tentam gerar a mesma nova competência. Demonstrar trava efetiva e uma obrigação, preservando as linhas históricas em conflito. Incluir caminhos alternativos de geração que usem o serviço. Se houver falha, corrigir minimamente e testar regressão; não escolher vencedora entre fatos reais.

### Pagamento, cancelamento e fechamento

Executar pagamento de fiado/mensalidade, venda, cancelamento elegível e fechamento disputando a sessão. Sincronizar o bloqueio observado, sem sleep como única prova. Verificar efeitos integrados ou recusa após fechamento: estoque, alocação, crédito, evento, status e financeiro. Nenhum efeito tardio após a sessão fechada, nenhum reembolso inventado, nenhum deadlock ignorado.

Separar cenários de cancelamento de dívida já paga/parcial/crédito (devem permanecer bloqueados) do cancelamento elegível. Conferir saldo inicial + soma dos efeitos físicos válidos, não apenas soma dos eventos isoladamente.

### Interrupções FIN-09

Conferir o código atual e reproduzir as janelas: falha antes do commit, morte entre commit e promoção, requisição ativa × recuperador, recuperação concorrente, falha ao cancelar/anular, promoção concluída antes da resposta e reenvio. Não presumir que teste de função auxiliar equivale a morte/interleaving do processo real; explicitar o nível de cada prova.

Provar que a trava por compra não fica presa em conexão devolvida ao pool e desaparece após morte do processo; que só tentativas comprovadamente interrompidas são compensadas; que compras históricas, arquivo presente, erro de acesso/volume indisponível e caso fora de 24 horas não são cancelados por varredura genérica. Reenvio nunca informa sucesso para documento cancelado ou incompleto.

Correções focais de defeitos comprovados nesses contratos estão autorizadas. Se exigir alteração arquitetural ampla ou decisão de negócio, registrar bloqueador e continuar verificações independentes; não ampliar para outbox/reembolso/conciliação.

## 4. AUD-03 — limite operacional claro para FIN-09

- Não prometer atomicidade banco/arquivo ou nota obrigatória garantida. Documentar estados transitórios, compensação, recuperação e janela de 24 horas com precisão.
- Conferir diagnóstico/alerta já existente para casos fora da recuperação automática. Se houver apenas log técnico insuficiente para o operador localizar os registros, acrescentar aviso somente leitura em Compras/Relatórios, restrito às permissões existentes, com links para os documentos comprovadamente afetados.
- Arquivo ausente sozinho não comprova interrupção nem autoriza cancelamento. Texto deve distinguir problema de acesso ao armazenamento de falha comprovada da tentativa.
- Entregar procedimento manual: onde localizar, como conferir tentativa/documento/arquivo, quais evidências preservar e quando encaminhar para correção assistida. Não criar botão de reparação destrutiva, SQL genérico de cancelamento ou reparo automático de fatos antigos.
- Testar aviso/diagnóstico com interrupção antiga e armazenamento indisponível, sem falsamente apresentar tudo como compra nova a ser refeita. Não orientar operador a duplicar despesa enquanto o estado não estiver esclarecido.

## 5. Revalidação e disponibilização no DEV

Validar navegador desktop/smartphone para repetição no mesmo operador, recusa de contexto divergente quando acessível por interface, compra/alerta e regressões de fluxo financeiro. Fixtures exclusivas; screenshots sem dados pessoais reais. Testar o fluxo funcional, não só texto de classe ou existência de rota.

Aplicar no DEV somente depois de correções/provas aprovadas: backup recuperável em diretório permanente, hash e restauração validada em descartável, baseline próprio anterior a qualquer startup que possa escrever. Não sobrescrever backup original pré-TASK_012.

Conferir possíveis efeitos de startup, especialmente recuperar_compras_sem_nota, antes de reiniciar o único serviço da 9000. Não executar operações de demonstração no DEV. Comparar conteúdo dos fatos relevantes e relatar alcance dos hashes/colunas, sem alegar igualdade integral a partir de agregados. Informar se houve qualquer compensação de compra no reinício e justificá-la; ausência de operações previstas não permite omitir efeitos reais.

Se algum bloqueador tornar insegura a aplicação, entregar código/evidência e manter o DEV anterior, sem forçar a migração. Nenhuma credencial alterada para obter prévia.

## Entrega e aceite

1. Relatório nesta ordem e em docs/investigacoes/TASK_014_CORRECOES_AUDITORIA_FINANCEIRO.md: achado → causa → arquivo/função → correção → prova antes/depois → conclusão. Identificar commit/árvore final e equivalência ao código testado.
2. Matriz de rotas da idempotência com autor/operação, testes de dois operadores e concorrência, log integral posterior à limpeza corrigida, provas pendentes reproduzidas e limites FIN-09 com procedimento operacional.
3. Referenciar complemento na TASK_013 sem apagar o parecer histórico. Atualizar TASK_012/SHAMBALA_CONTEXTO.md com estados reais, sem declarar funcionalidades excluídas resolvidas.
4. Parecer fundamentado por fluxo: apto para homologação, apto com ressalvas ou bloqueado. Se houver novo defeito, registrá-lo; não transformar falta de prova em aprovação.
5. Roteiro curto para o dono, priorizando consultas sem escrita e depois dados fictícios exclusivos com valores/resultado corretos e preservação dos fatos. Homologação será feita por ele depois da revisão da entrega.
6. Commit local somente dos arquivos pertinentes, sem push; encerrar recursos próprios e registrar estado do DEV. Mover ordem para docs/tasks/done/ se o escopo estiver atendido; se parcial, manter em docs/tasks/ com bloqueios explícitos.

Critério mínimo: AUD-01 corrigido em todos os caminhos; teardown não pode falhar silenciosamente; regressão consolidada confiável; provas de concorrência e recuperação concluídas ou bloqueadores explicitados. FIN-09 pode permanecer como protocolo mitigado com limite conhecido, desde que seguro nos contratos demonstrados e sem promessa de atomicidade. Produção não acessada nem publicada.
