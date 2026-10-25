# TASK_016 — Corrigir contas, destinos dos recebimentos e saldos

Status: **implementada e aplicada no DEV — homologação do dono pendente.**
Data: 25/10/2026. Commit `7eef33f`.
Relatório: `docs/investigacoes/TASK_016_CORRECOES_CONTAS.md`.
Resumo: conta de destino explícita e validada no servidor (contrato único
conta×forma), banco/poupança recebendo PIX, `areceber` fora do dinheiro,
disponível × pendente separados, conta na identidade da tentativa.

Rodada 2 completou os pontos que faltavam:
- **seleção explícita em todas as telas**: PDV, fiado (dívida + crédito),
  mensalidade, doação e fechamento legado, com o macro único
  `_conta_destino.html` — mesma organização de tela, sem novo layout;
- **fechamento legado**: o destino vem de `vendas_pdv.conta_id` (vínculo
  persistido na hora da venda); sem vínculo, o fechamento **exige a escolha do
  operador**, valida contra o contrato e nunca deduz por tipo. Nenhum histórico
  foi reescrito — as 1859 vendas antigas ficaram NULL;
- **validação de navegador** com duas contas de espécie e PIX em banco/poupança:
  39/39 verdes (`test_t016_navegador.py`);
- **matriz de cobertura** provada por rota real: 24/24
  (`test_t016_matriz.py`), incluindo individual × lote, idempotência com troca de
  conta, inativação concorrente, fechamento/cancelamento e saldo anterior ao
  período.

25 suítes rc=0; única mudança de schema é a coluna aditiva `vendas_pdv.conta_id`;
baselines do DEV idênticos; contas do DEV intactas.

Limitações registradas: defeito de layout **pré-existente** do PDV em 412px
(transborda 470px — medido contra o commit anterior, idêntico); regra de sessão
legada preservada; saldo inicial e saldos reais pendentes do dono.

## 1. Objetivo e decisão do dono

Corrigir o funcionamento das contas financeiras antes de mover seu cadastro para dentro do Financeiro. Nesta tarefa, o cadastro permanece na localização atual.

Resultados esperados: várias contas em espécie não quebram recebimentos; banco e poupança são destinos compatíveis das operações pertinentes; o usuário sabe onde o recurso entra ou sai; saldo disponível não soma obrigações pendentes.

Não renomear a Conta Espécie para Caixa da casa, nem criar contas reais pelo dono. Essa definição continua pendente e não impede corrigir o sistema.

## 2. Base de investigação obrigatória

Ler AGENTS.md, SHAMBALA_CONTEXTO.md, docs/investigacoes/INVESTIGACAO_CONTAS_FINANCEIRAS.md e as tarefas financeiras pertinentes, especialmente TASK_008, TASK_010, TASK_012 e TASK_014. Conferir Git e preservar alterações alheias, incluindo TASK_015.

O resumo fornecido pelo executor informa:

- Cadastro existente em /configuracoes/contas, com acesso pelo menu restrito a administrador.
- contas_financeiras representa destino/origem dos recursos; caixas representa ponto operacional; caixa_movimentos representa sessão. Não unificar essas entidades por semelhança de nome.
- Contas semeadas: legado, especie, pix e areceber.
- Segunda conta do tipo especie faz doação em dinheiro falhar; a rota investigada envia None ao validador mesmo quando o POST informa conta_id.
- banco/poupanca são criáveis, mas não aceitos pelo validador de recebimentos.
- Tela de contas soma pago e pendente.
- Transferência transacional existente sem identificador compartilhado; sangria possui par ligado por sangria_id.

Confirmar tudo no código atual. O defeito em doação foi reproduzido na investigação; sua extensão aos demais fluxos foi inferida por leitura e precisa de verificação. Números do resumo são referência histórica, não baseline desta execução.

Antes de editar, registrar mapa dos fluxos e sua regra atual. Não refazer o diagnóstico inteiro se as evidências existentes forem suficientes.

## 3. Contrato de contas e formas de pagamento

Forma de pagamento e conta de destino são conceitos distintos. PIX é uma forma de pagamento; o recurso vai para uma conta bancária. Preserve os identificadores e vínculos históricos existentes, inclusive a conta técnica pix, sem conversão automática de registros.

Construir matriz explícita operação × forma × contas elegíveis × exigência de sessão × efeito físico na gaveta. A matriz deve orientar serviços, rotas e formulários de maneira consistente.

Regras mínimas:

- Dinheiro recebido entra em conta elegível de espécie; PIX pode entrar em conta bancária ou poupança compatível. Preservar a compatibilidade necessária à conta pix já existente, documentando sua condição de legado técnico.
- Permitir várias contas elegíveis e exigir escolha quando houver ambiguidade. Se houver exatamente uma elegível, pode ser usada como padrão, visível na tela; mostrar o destino efetivo antes de confirmar.
- Aceitar e validar conta_id no servidor. Conta inexistente, inativa, incompatível ou não permitida deve ser recusada antes dos efeitos, com mensagem compreensível e sem substituição silenciosa por outra conta.
- Não selecionar a primeira conta por ordem/id nem usar apenas o nome para decidir sua natureza.
- Contas técnicas de obrigações ou legado não devem aparecer como destinos disponíveis de dinheiro só por estarem ativas.
- Banco/poupança não representam dinheiro físico na gaveta. Transferir dinheiro para o banco é uma operação própria; não simular isso escolhendo banco em um recebimento em espécie.
- Novas movimentações não usam conta inativa; seus registros e consultas históricas permanecem acessíveis.

Não criar formas de pagamento novas. Para formas já existentes além de espécie/PIX, preservar o comportamento legítimo e documentar compatibilidade. Ambiguidade de negócio sem solução no contrato exige pergunta objetiva ao dono; continue as partes independentes.

## 4. Aplicar o contrato aos fluxos reais

Rastrear e corrigir, onde pertinente: venda PDV, doação, mensalidade individual/em lote, recebimento de fiado, crédito antecipado e lançamentos diretos existentes. Revisar também compras e transferências para impedir que uma mudança compartilhada lhes atribua contas incompatíveis.

A conta escolhida deve chegar da tela ao serviço, ser persistida no documento/livro-caixa e reaparecer no comprovante ou detalhe existente quando esse destino já for exibido. Não criar um subsistema paralelo de recebimentos.

Preservar integralmente:

- Cálculo de preços pelo servidor e transação única da venda.
- Alocações de fiado, excedente como crédito e unicidade de mensalidades.
- Eventos de sessão, efeito físico e serialização com fechamento/cancelamento.
- Recusa de novos recebimentos em sessões legadas. Não decidir nesta tarefa sobre vendas e sangrias legadas.
- Recebimentos eletrônicos permitidos sem sessão, conforme contrato vigente. Não exigir abertura de caixa físico para um PIX apenas por introduzir seleção de conta.
- RBAC, auditoria e isolamento dos testes.

Idempotência: incluir a conta e demais campos financeiros relevantes na representação canônica da tentativa. Repetir a mesma tentativa e contexto deve manter um único efeito; trocar a conta com a mesma chave deve gerar conflito, nunca reaproveitar resultado de outro destino. Preservar isolamento por operador/operação da TASK_014. Conferir os caminhos de formulário reexibido, erro e reenvio.

Validação de elegibilidade e inativação concorrente devem ter uma política transacional coerente: não permitir que a conta seja validada, inativada e depois receba nova gravação sem coordenação. Usar as travas existentes ou a menor extensão necessária, com ordem consistente para evitar deadlocks.

## 5. Saldos e apresentação

Identificar todas as consultas afetadas no cadastro de contas, visão geral, extrato e filtros financeiros. Definir uma única regra coerente para entradas/saídas efetivadas, status, cancelamentos e compensações, evitando contar documento e evento duas vezes.

- Saldo disponível de conta de recursos considera apenas movimentações efetivadas até a data de referência.
- Pendências e obrigações devem aparecer separadamente, com rótulos adequados. A conta areceber não entra no total de recursos disponíveis por conter registros pagos ou pendentes.
- Investigar o papel dos registros pagos em areceber antes de somá-los em qualquer total. Não presumir que representam disponibilidade ou recebimento válido em conta real.
- Movimentos entre contas podem alterar os saldos individuais, mas não viram receita/despesa consolidada por essa razão.
- Saldo acumulado e movimento do período são medidas diferentes: informar claramente a abrangência. Filtrar um período não deve fazer saldo inicial desaparecer e ser apresentado como saldo atual.
- Preservar valores negativos reais e permitir sua análise. Não zerar, esconder, truncar ou ajustar saldos para fazer a tela parecer correta.
- Datas e moeda seguem os padrões brasileiros atuais; manter o layout e os ícones aprovados, sem redesign.

Saldo inicial novo, conciliação com extratos e cadastro de valores reais ficam fora desta tarefa. Se não existir mecanismo confiável, registrar a limitação; não inventar saldo inicial.

## 6. Histórico e sangrias

Investigar por leitura o saldo negativo da Conta Espécie e a relação com sangrias, transferências e ausência de destinos. Distinguir fatos demonstrados, diferenças de apresentação e lacunas históricas. A existência de saídas não prova, sozinha, que o histórico está errado.

Não alterar, apagar, redistribuir ou completar registros históricos por suposição. Não fazer backfill automático de conta, destino, transferência ou recebimento. Não corrigir os 43 recebimentos legados sem destino citados na TASK_012.

O identificador compartilhado de novas transferências e uma eventual conciliação histórica podem ser propostos no relatório, mas não fazem parte desta implementação. Se houver defeito adicional que impeça garantir os saldos dentro do escopo, reproduzir, delimitar e informar a dependência em vez de declarar o problema resolvido.

## 7. Provas necessárias

Usar PostgreSQL nativo descartável e launchers/guardas atuais. Conferir banco efetivo e identidade da execução antes de escrever. Não criar fatos fictícios nem alterar credenciais no DEV real.

Cobrir por serviços e rotas reais:

1. Reprodução do defeito da segunda conta espécie; após corrigir, escolha de cada conta registra o destino correto sem quebrar doação. Sem escolha ambígua, recusa limpa.
2. Matriz de todos os fluxos afetados, incluindo lotes de mensalidade e fiado com excedente. Não generalizar a prova de um fluxo aos demais.
3. PIX destinado a banco/poupança sem sessão quando permitido, sem efeito físico fictício na gaveta; destino técnico atual continua compatível quando necessário.
4. Contas inválidas, inativas e incompatíveis recusadas sem documentos, baixas, crédito, estoque, acumuladores ou eventos parciais.
5. Reenvio da mesma tentativa com mesma conta: um efeito; mesma chave com conta diferente: conflito; isolamento por operador/operação preservado.
6. Inativação concorrente e recebimento, com coordenação determinística; fechamento/cancelamento preservam as invariantes dos eventos.
7. Fixture de saldos com entradas/saídas pagas, pendentes, canceladas/compensadas e transferência: valores exatos por conta, pendências separadas, total consolidado sem duplicação. Cobrir saldo anterior ao período.
8. Snapshot do histórico antes/depois da migração, se necessária; segunda execução idempotente.
9. Navegador: seleção de destino visível, troca de forma atualiza elegíveis sem perder dados indevidamente, erro preserva formulário, desktop e celular sem regressão de layout.

Executar as suítes afetadas e o runner exigido pelo projeto. Falha, limpeza incompleta ou teste pulado não contam como aprovação. Registrar a árvore efetiva e logs; não chamar de final verde um log com erro. Não reconstruir o harness financeiro antigo apenas para mascarar incompatibilidades conhecidas.

## 8. Aplicação no DEV

É autorizada a aplicação desta correção no serviço existente da porta 9000 após validação isolada. Antes: baseline de fatos e backup durável com hash e restauração conferida. Se houver migração, preferir alteração mínima/aditiva, sem reclassificação histórica e sem emissão de movimentos.

Provar propriedade/configuração do processo antes de reiniciar. Reiniciar só o serviço existente quando necessário, sem segunda instância permanente. Comparar fatos antes/depois e explicitar alterações esperadas de schema, se houver. Nenhum lançamento real, conta real ou senha deve ser alterado para validar a entrega.

DEV continua HTTP, sem nginx/TLS/CA. Não acessar produção, mesmo por leitura. Sem push ou deploy. Limpar somente bancos, arquivos temporários e processos criados nesta execução.

## 9. Entrega

Na própria tarefa, registrar:

- Causa comprovada, mapa anterior e contrato final de elegibilidade/destino.
- Arquivos, rotas, serviços e mudanças de schema, se houver.
- Fórmula e abrangência dos saldos, tratamento de areceber, transferências e histórico.
- Matriz de provas, resultados e logs com identidade da execução.
- Evidências visuais com dados fictícios, distinguindo ambiente descartável do DEV.
- Baseline/backup/aplicação no DEV e limitações verdadeiras.
- Diagnóstico das sangrias históricas sem correção de dados e decisões ainda necessárias do dono.
- Roteiro curto de homologação; primeiros passos somente leitura. Escritas, se necessárias, exclusivamente com cadastros fictícios identificados e instrução clara sobre o histórico que permanecerá. Não tocar dívidas ou contas reais no roteiro.

Atualizar SHAMBALA_CONTEXTO.md com o contrato efetivo. Fazer commits locais apenas dos arquivos desta tarefa, sem push. Mover para docs/tasks/done/TASK_016_CORRIGIR_CONTAS_RECEBIMENTOS_SALDOS.md com status real: implementada no DEV, homologação do dono pendente.

## 10. Aceite

Múltiplas contas não quebram os recebimentos, o destino fica explícito e persistido, contas bancárias/poupança funcionam nos fluxos compatíveis, e os saldos distinguem recursos disponíveis de obrigações. Histórico preservado e proteções financeiras anteriores mantidas. O cadastro permanece em Configurações; sua mudança de localização será tratada depois. Esta entrega não autoriza produção.
