# TASK_009 — Revisão técnica do financeiro entregue na TASK_008

Status: pronta para execução.
Tipo: revisão de código, contratos e evidências; sem nova funcionalidade.
Referências: TASK_005 a TASK_008; implementação 785e228 e 6b6d8c6; revisões informadas 5c35d47, cc94a08 e 38c8dba. Confirmar os commits e o estado real da árvore.

## Objetivo

Revisar a implementação efetiva da TASK_008 e das correções R1–R3. Até aqui a revisão do dono foi documental: os resultados foram informados pelo executor, sem conferência independente do código e dos logs. Procurar defeitos e lacunas de prova, sem tomar relatório, teste verde ou nome de teste como garantia.

Entregar um parecer fundamentado sobre integridade dos novos fluxos, isolamento do legado, proteção dos testes e suficiência das evidências. Não declarar todo o financeiro homologado nem pronto para produção por consequência desta revisão.

## Limites de execução

1. Ler AGENTS.md, normas locais, SHAMBALA_CONTEXTO.md, diagnóstico TASK_005 e ordens/relatórios TASK_006–008. Registrar esta ordem em docs/tasks/ e preservar trabalho alheio.
2. Não acessar produção, nem por leitura; não fazer SSH, push, publicação ou alterar infraestrutura.
3. Não escrever no DEV, reiniciar seu serviço ou aplicar migração. Nenhuma alteração em usuários, senhas, permissões, sequences, vendas ou sessões reais. Não precisar de credencial do dono para revisar.
4. Testes e reproduções somente em PostgreSQL nativo descartável, sem Docker. App temporário só com isolamento confirmado antes do startup; encerrar os recursos próprios ao terminar. Não derrubar recursos de outras tarefas.
5. Esta tarefa autoriza relatório e testes de revisão em ambiente isolado. Não corrigir código de produto, schema ou comportamento financeiro durante a revisão. Para defeitos encontrados, registrar reprodução e correção recomendada; implementação ficará para tarefa posterior.

## 1. Estabelecer exatamente o que será revisado

- Registrar HEAD, commits revisados, diff pertinente e arquivos modificados fora da tarefa. Não fazer checkout destrutivo, reset, stash ou commit de alterações alheias.
- Comparar o contrato original da TASK_008 com o código final, incluindo as três revisões. Relacionar cada requisito relevante ao arquivo/função e à evidência que realmente o verifica.
- Ler os testes e os logs citados, incluindo runner, reprodução FIN-04, navegador e suíte financeira antiga. Conferir se correspondem à versão atual e distinguir testes, casos e asserções. A soma anunciada é 372 (226 anteriores + 118 backend + 28 navegador); verificar a unidade usada, RC e pulados reais, sem inflar cobertura por contagem de assertivas.
- Evidência inexistente, incompleta ou produzida sobre outra versão deve constar como limitação, nunca como aprovação.

## 2. Revisão do caminho financeiro completo

Seguir cada operação da rota HTTP até a transação e as tabelas, incluindo chamadas alternativas e rotas antigas:

- venda espécie, PIX, fiado e consumo de crédito;
- doação junto da venda, troco doado e doação avulsa;
- mensalidade individual/lote;
- quitação integral/parcial e sobra destinada a crédito;
- adiantamento de crédito;
- sangria/transferência;
- cancelamento, abertura e fechamento.

Para cada caminho, identificar origem persistida, valor/forma/conta/sessão, evento, efeito físico, lançamento financeiro e momento em que ele é criado. Procurar omissão, duplicidade, destino implícito e mutação parcial.

Conferir especificamente:

1. O cenário misto da TASK_008 fecha vendas comerciais em R$ 102, recebimentos novos espécie R$ 113, PIX R$ 105 e gaveta esperada R$ 183, com saldo inicial R$ 100 e sangria R$ 30. Verificar também saldo por conta e vínculos, não apenas o total da tela.
2. Crédito consumido não vira novo recebimento nem duplica vendas no resumo; fiado não recebido não vira dinheiro. Doação da venda tem classificação própria. Cancelamento compensa venda e doação uma única vez, preservando bloqueios da TASK_007.
3. PIX sem sessão física funciona pelo caminho previsto. Investigar a aparente diferença documental entre a rota que permite PIX sem sessão e a tela descrita como bloqueando todo o PDV sem caixa aberto; registrar o comportamento e sua aderência ao contrato, sem presumir defeito só pela redação.
4. Conta e sessão são validadas no servidor; sem dependência de caixa_id fixo, primeira sessão encontrada ou nome de conta. Parâmetros irrelevantes não alteram o destino e parâmetros adulterados não burlam validação.
5. A mesma entrega de dinheiro que quita dívida e gera crédito excedente tem uma única entrada. Adiantamento tem entrada financeira atômica e vínculo ao crédito; saldos históricos não ganham origem inventada.
6. Sangria interna possui origem/destino coerentes, par persistido e efeito global zero. Conferir saldo por conta, não somar valores absolutos de entradas e saídas como prova de equilíbrio.
7. Fechamento realiza somente o que ainda não foi lançado, sem receita para crédito/fiado não recebido. Contado e esperado ficam separados; divergência exige justificativa e não fabrica ajuste. Consulta/PDF conservam o fechamento original.
8. Decimal, arredondamento, valores zero/negativos/excessivos e precisão no caminho alterado. Constraints/FKs e unicidade sustentam o contrato, sem confiar exclusivamente no navegador.

## 3. Idempotência, atomicidade e concorrência

- Identidade gerada depois de um INSERT não prova idempotência do comando: dois POSTs podem criar dois IDs e duas entradas válidas. Verificar como a mesma intenção/documento é identificada antes de gravar, inclusive repetição após resposta perdida e comandos concorrentes. Testar divergência de conteúdo com a mesma chave.
- Verificar fronteiras reais de commit/rollback e exceções capturadas dentro de with conectar(). Injetar falha na última escrita e provar ausência de efeitos parciais em obrigação, crédito, estoque, evento e financeiro.
- Mapear ordem de aquisição de travas em venda, cancelamento, recebimento, fechamento e abertura. Conferir queries efetivas e dependências cruzadas, não somente comentário sobre ordem.
- Exercitar fechamento × venda/recebimento/cancelamento, fechamento repetido e abertura concorrente. Usar sincronização determinística; não aceitar sleep como única prova de interleaving.
- Verificar unicidade de sessão aberta quando duplicidades legadas impedem criar o índice: o bloqueio de aplicação protege todas as rotas de abertura? Diagnóstico é explícito e não mascara ausência da constraint?

## 4. Transição e correção R1

- Confirmar que todas as rotas de novos recebimentos recusam sessão legada antes de qualquer efeito, inclusive por POST direto, lote e crédito. Verificar também a segunda proteção em contabilizar_recebimento().
- Confirmar legado fora das opções de destino e mensagem compreensível. Não basta remover uma opção da interface.
- Revisar separadamente vendas e sangrias ainda permitidas em sessão legada. Comparar isso com a regra original de que novas operações não devem contaminar acumuladores legados. Explicar a extensão e o risco desse comportamento e apontar eventual descumprimento; não considerar decidido só porque o relatório diz que foi preservado.
- Fechamento legado isolado pode continuar pela regra antiga, com aviso e sem recálculo histórico. Conferir versionamento, consultas e comprovantes antigos.
- Migração repetida e instalação nova em bancos descartáveis: preservar fatos e não converter sessão antiga automaticamente. Não tentar remigrar o DEV.

## 5. Isolamento e correção R2

- Ler tests/subir_app_descartavel.sh, trava_isolamento, lifespan e todos os lançadores de testes. Confirmar que a defesa acontece antes de criar_tabelas/_migrar e que variáveis carregadas posteriormente não trocam o destino já validado.
- Avaliar se o marcador _test é apenas convenção nominal ou proteção suficiente em conjunto com conexão/destino real. Conferir identificação do servidor/banco e configuração ausente/divergente.
- Provar recusas sem iniciar app nem executar DDL. Usar destinos sentinela descartáveis que simulem configuração inválida; jamais testar o bloqueio apontando para DEV/produção.
- Investigar se teste/runner ainda consegue contornar o lançador e iniciar uvicorn com configuração padrão. Distinguir a proteção de um wrapper da proteção estrutural de todos os caminhos temporários suportados.
- Conferir seed, limpeza, subprocessos, PIDs e teardown para evitar escrita/remoção fora do banco alvo. Relatar testes que não conseguem executar com segurança, sem contornar a proteção.

## 6. Documentação e correção R3

- Backup informado é posterior à primeira migração acidental: deve ser descrito como recuperável para aquele estado, sem promessa de restauração pré-migração. Conferir a evidência disponível, inclusive erros de privilégios no restore e seu alcance; não executar nova restauração sobre DEV.
- Não usar contagens agregadas como prova de conteúdo idêntico. Distinguir contagens/somas, comparação por linha/hash, schema, credenciais/permissões e o que efetivamente foi medido antes do incidente. Ausência de baseline anterior é limitação irrecuperável por simples redação posterior.
- Revisar roteiro de homologação: produto, trabalhador e dívida exclusivos de teste, nenhuma dívida real, identificação de todos os registros duráveis e limites de inativação. Não realizar esse roteiro no DEV nesta tarefa.
- Classificar erros da suíte financeira antiga pela exceção e pelo caminho real; não atribuir tudo à fixture sem ler o log. Não inventar tabela para fazê-la passar. Distinguir teste incompatível de falha do produto.

## 7. Método e evidências

Reexecutar as suítes diretamente pertinentes no ambiente isolado após conferir sua segurança. Novos testes devem atacar lacunas de contrato e provar defeitos, não reproduzir a implementação. Reusar evidências válidas; executar o runner completo apenas se necessário para sustentar o parecer ou por exigência local, sempre com isolamento prévio.

Para cada achado, registrar:

- severidade (crítico/alto/médio/baixo), requisito e impacto concreto;
- arquivo/função/linhas no commit revisado;
- passos determinísticos e resultado observado versus esperado;
- evidência sanitizada: request, consultas/assertivas e efeitos antes/depois no banco descartável;
- recomendação focal e teste necessário para aceitar a correção.

Não registrar suspeita como defeito comprovado. Separar achados reproduzidos, riscos por inspeção e limitações sem evidência suficiente. Incluir resultados positivos relevantes, evitando uma lista de PASS sem vínculo ao requisito.

## Entrega e conclusão

1. Relatório em docs/investigacoes/TASK_009_REVISAO_TECNICA_FINANCEIRO_TASK_008.md, com escopo/commits, matriz requisito→código→prova, achados por severidade, testes/RC/pulados e limites da conclusão.
2. Parecer explícito: aprovada, aprovada com ressalvas ou requer correções, com critérios verificáveis. Não equivale a autorização de publicação nem homologação financeira geral.
3. Se houver bloqueadores, propor a menor tarefa de correção e a ordem de tratamento; não implementar nem criar nova TASK numerada automaticamente.
4. Relatório de execução nesta ordem, atualizar seu status e movê-la para docs/tasks/done/ ao concluir a revisão, mesmo que o parecer exija correções no produto. Atualizar contexto somente com fatos verificados, sem apagar histórico.
5. Commit local apenas da documentação e dos testes de revisão pertinentes, sem push. Informar arquivos e commits, recursos próprios encerrados e o que não pôde ser verificado.

Critério de conclusão: revisão independente do código e evidências entregue com conclusões rastreáveis. Não depende de consertar o produto, acessar produção ou obter senha do dono.
