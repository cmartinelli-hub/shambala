# TASK_010 — Corrigir isolamento dos testes, saldo zero e revalidar a TASK_008

Status: executada em 2026-10-24 e **encerrável com ressalvas** (decisão do revisor) — homologação manual do dono pendente; produção segue sem autorização de publicação. Ver "Adendo de execução" ao final.
Referências: TASK_009, parecer requer correções (commit informado aae4799); TASK_008 e revisões R1–R3; contratos TASK_006/007. Confirmar estado real e commits antes de editar.

## Objetivo

Resolver REV-01, REV-02 e REV-03 da revisão técnica. Depois, produzir evidência confiável dos fluxos da TASK_008 e concluir os pontos da revisão interrompidos pela divergência de banco. A entrega não autoriza produção nem equivale a homologação geral do financeiro.

## Ambiente e limites

- Ler normas locais/AGENTS.md se existir, SHAMBALA_CONTEXTO.md, ordens e relatórios TASK_006–009, especialmente docs/investigacoes/TASK_009_REVISAO_TECNICA_FINANCEIRO_TASK_008.md. Registrar esta ordem em docs/tasks/ e preservar alterações alheias.
- Não acessar produção, inclusive por leitura/SSH. Sem push, deploy ou alteração de infraestrutura.
- PostgreSQL nativo descartável para testes, sem Docker. Processos temporários apenas durante os testes, encerrados por seus PIDs reais. Não alterar credenciais, permissões ou dados reais para testar.
- DEV continua no serviço existente, HTTP na porta 9000, sem nginx/TLS/túnel ou segunda instância persistente. Aplicação controlada no DEV está autorizada somente após os critérios desta ordem, com backup/baseline e sem operações financeiras de demonstração.
- Não corrigir outros achados FIN-05–09 nem remodelar o financeiro. Defeitos adicionais encontrados na revalidação devem ser documentados com reprodução; não ocultar falhas para encerrar a tarefa.

## 1. REV-01 e REV-03 — identidade efetiva do ambiente

### Diagnóstico a confirmar

O runner aceita SHAMBALA_TEST_DB para recriar um banco, mas inicia a aplicação com .env.test apontando para outro. O launcher verifica variáveis/nome sem abrir a conexão que afirma validar. A existência de _test nos dois nomes não garante que preparação, aplicação e asserções usem o mesmo destino.

### Correção exigida

1. Definir uma configuração explícita por execução, consumida por runner, launcher, aplicação e suítes. Resolver/documentar precedência de arquivo e variáveis. Não permitir que .env.test ou fallback padrão substitua silenciosamente o destino solicitado.
2. Validar configuração antes de qualquer DROP/CREATE, seed, limpeza ou startup. Destino ausente, incompatível ou não descartável aborta com RC não zero e sem efeitos. A criação deve usar conexão de manutenção explicitamente validada; não tentar conectar ao banco ainda inexistente como requisito impossível para criá-lo.
3. Após criar o banco alvo, abrir conexão efetiva com a mesma configuração que o app usará e confrontar identidade esperada/observada: current_database(), servidor/porta ou socket e usuário relevantes. Não imprimir senha, DSN secreto ou conteúdo integral do env.
4. Launcher valida conexão real antes de executar uvicorn. Lifespan preserva defesa anterior a criar_tabelas/_migrar e confere o mesmo destino. Importar módulo não pode produzir DDL antes dessa validação.
5. Cada suíte deve confrontar o destino da própria conexão e o servidor HTTP efetivamente em uso antes de seed/escrita. A prova do HTTP deve corresponder ao processo/execução corrente, sem aceitar log antigo. Não criar rota pública de diagnóstico nem expor configuração no produto para resolver o teste.
6. Tratar configuração de modo consistente em processos filhos. Auditar lançadores alternativos e as suítes antigas que usam bancos próprios: cada uma pode ter seu banco isolado, mas preparação, app, verificações e limpeza daquela suíte precisam concordar explicitamente.
7. Limpeza só remove recursos reconhecidamente criados pela execução; nunca apagar banco preexistente por convenção de nome. Não matar processos alheios ao liberar porta. Falha de isolamento interrompe a execução, não vira PASS ou PULADO.

### Provas obrigatórias

- Dois destinos descartáveis A e B com sentinelas distintas: solicitar A e fornecer configuração conflitante B deve abortar antes de DDL/seed e preservar as sentinelas. Exercitar o runner e o launcher, não só a função auxiliar.
- Execução positiva em banco de nome diferente do padrão: provar que criação, startup, suítes e limpeza referem-se ao mesmo alvo e que B permanece intacto. O teste negativo deve falhar se a antiga escolha fixa de .env.test for reintroduzida em cópia isolada.
- Ausência de arquivo, configuração incompleta, modo de teste ausente, destino inválido, conexão indisponível e destino efetivo divergente: saída não zero antes de startup/DDL/escrita correspondente. Simular todos os alvos inválidos em infraestrutura descartável; não apontar os testes negativos para DEV ou produção.
- Registrar configuração sanitizada e identidade observada com vínculo à execução e ao commit. Conferir encerramento também nos caminhos de erro.

## 2. REV-02 — saldo inicial e contagem não negativos

Permitir abertura com R$ 0,00 pela tela e pelo POST real, inclusive valor padrão. Dinheiro contado também admite zero. Valores de eventos de recebimento/saída continuam estritamente positivos; não relaxar globalmente moeda() para aceitar evento zero.

Usar parser/regra explícita para valores não negativos, Decimal e limite NUMERIC compatível. Rejeitar negativos antes do arredondamento, além de NaN, infinito, texto inválido e estouro. Preservar a política monetária de duas casas/ROUND_HALF_UP da TASK_008: documentar o resultado de 0,001 e limites após arredondamento, sem deixar o parser decidir por acidente. Se a interface só aceita centavos, conferir também envio direto com precisão adicional.

Cobrir zero, positivo, negativo mínimo, 0,001, valor máximo e excedente que arredonda além do teto. Recusas não criam sessão nem evento. Fechar sessão vazia com contado zero deve funcionar e produzir esperado/divergência zero, sem lançamento financeiro fictício. Validar abertura/fechamento por navegador, não apenas chamada do serviço.

## 3. Retomar a revisão financeira interrompida

Com o isolamento demonstrado, completar a matriz da TASK_009. Não inferir aprovação dos pontos ainda não exercidos apenas porque REV-01 foi resolvido.

- Reexecutar backend e navegador TASK_008, preservando TASK_006/007. Cenário misto: vendas R$ 102, espécie recebida R$ 113, PIX R$ 105, saldo inicial R$ 100 e sangria R$ 30 → físico esperado R$ 183. Conferir eventos, contas, vínculos e obrigações/crédito além da tela.
- PIX sem gaveta: testar rota e interface. Resolver a aparente divergência entre permissão do backend e bloqueio geral do PDV sem sessão. Se o bloqueio impedir o caminho aprovado, ajustar apenas o necessário para permitir PIX com conta válida e manter espécie condicionada à sessão aberta.
- Repetição de comando: distinguir identidade única por documento da idempotência do POST que cria novo documento. Exercitar venda e recebimentos após resposta perdida e em concorrência; mesma chave com conteúdo divergente não pode confirmar duas operações distintas. Não declarar risco resolvido apenas porque os IDs dos novos documentos são únicos. Caso falte proteção contratual, registrar o defeito e seu alcance para tarefa focal, sem ampliar esta entrega para uma nova arquitetura de idempotência.
- Fechamento × venda/recebimento/cancelamento, fechamento repetido e abertura concorrente: testes determinísticos com sincronização, verificando ordem de travas e ausência de efeito tardio em sessão fechada. Falha injetada na última escrita deve reverter todos os efeitos da operação.
- Sangria: provar par vinculado e saldos por conta com efeito global zero; avaliar o risco de par incompleto e a fronteira transacional. Não exigir constraint específica sem demonstrar necessidade.
- Legado: comprovar recusa de novos recebimentos por POST direto e lote, mantendo histórico e fechamento antigo isolados. Revisar vendas/sangrias novas ainda permitidas no legado à luz da TASK_008; registrar claramente eventual descumprimento e impedimento para publicação, sem converter/fechar sessão real ou alterar transição por conta própria.
- Contagem divergente, cancelamento com doação, crédito consumido, parcial com sobra e fechamento sem receita duplicada: confrontar estado persistido, resumo e PDF.
- Revisar migração repetida/instalação nova somente em bancos descartáveis. Constraints ausentes por duplicidades históricas e rotas alternativas de abertura precisam de conclusão explícita.

Se aparecer novo defeito financeiro, produzir reprodução e registrar como pendência bloqueadora quando pertinente. Esta tarefa pode concluir as correções REV-01–03 com a revisão ainda reprovada por novos achados; não transformar falha em ressalva para obter status verde.

## 4. Regressão e evidências

1. Provar antes/depois dos três achados com testes pertinentes que falhem com o comportamento anterior e passem com a correção. Mutação temporária apenas em cópia isolada, sem deixar produto alterado.
2. Rodar suítes afetadas e depois runner completo com configuração comprovada. Não perseguir a contagem histórica 372: registrar casos, asserções, aprovados, falhos, pulados e RC reais por suíte, com soma coerente e sem misturar unidades.
3. Capturar logs sanitizados em docs/tasks/evidencias/TASK_010/ com commit/árvore testada, comando, destino observado e resultado. Explicar quais evidências anteriores foram substituídas; preservar o registro histórico da divergência, sem reescrever logs antigos.
4. Suíte financeira antiga executada separadamente se pertinente, classificando cada causa real; não criar auditoria_eventos para satisfazer fixture nem apresentar harness quebrado como validação do produto.
5. Navegador desktop e smartphone para abertura zero, fechamento zero, cenário misto e PIX sem sessão, além das provas já previstas. Ferramenta ausente é limitação/pulado, nunca aprovação.

## 5. Disponibilização no DEV

Após correções verificadas e testes necessários aprovados, conferir diff e impacto do startup. Fazer backup recuperável com restauração validada em descartável e baseline próprio antes de qualquer aplicação. Comparar fatos relevantes por conteúdo quando possível, não só contagens; incluir credenciais/permissões sem expor seus valores.

Reiniciar apenas o serviço existente da 9000; conferir árvore servida, login e startup, sem vendas ou outros fatos de demonstração no DEV. Não modificar a sessão legada aberta. Se houver novo achado que torne inseguro aplicar, manter o serviço como está e entregar código/evidência com bloqueio explícito. Schema adicional não é esperado; necessidade nova deve ser justificada e não aplicada automaticamente por ampliação de escopo.

## Entrega

- Adendo nesta ordem com causa raiz, contrato final de configuração, correções e arquivos, reproduções antes/depois, evidências de identidade do banco e resultados reais.
- Complemento da revisão em docs/investigacoes/TASK_010_REVALIDACAO_FINANCEIRO_TASK_008.md: matriz da TASK_009 completada, achados resolvidos/abertos e parecer fundamentado. Aprovação técnica não autoriza produção nem substitui homologação manual do dono.
- Atualizar SHAMBALA_CONTEXTO.md e referenciar o complemento na TASK_009 sem apagar o parecer histórico. Estado da TASK_008 deve refletir o resultado efetivo da revalidação.
- Roteiro curto para o dono com dados exclusivamente fictícios, discriminando registros que ficam no histórico; não executá-lo com fatos reais.
- Encerrar recursos próprios e registrar limpeza. Commit local apenas dos arquivos pertinentes, sem push. Mover esta ordem para docs/tasks/done/ quando o escopo estiver entregue e registrar separadamente correções concluídas e parecer da revisão.

Critérios mínimos: REV-01 e REV-03 demonstradamente resolvidos, REV-02 corrigido por HTTP/navegador, regressão com destino comprovado e revisão restante documentada com evidência ou limitação explícita. Novos bloqueadores impedem declarar a TASK_008 homologada, ainda que os três achados iniciais estejam corrigidos.

---

# Adendo de execução (2026-10-24)

## Causa raiz

- **REV-01**: não havia contrato entre "o destino pedido" e "o destino resolvido". `load_dotenv(.env.test)` sem `override` deixava o shell e o arquivo se ignorarem mutuamente; o runner preparava `SHAMBALA_TEST_DB` e a aplicação subia contra o banco do `.env.test`, sem confronto. As suítes "provavam" o destino lendo qualquer log de qualquer execução.
- **REV-02**: `servico_sessao.moeda()` (estritamente positiva, correta para eventos) era reused no `POST /abrir`, recusando o padrão R$ 0,00; o `POST /fechar` usava parser inline ad hoc sem teto nem política documentada.
- **REV-03**: o launcher validava apenas o nome do arquivo; importava `banco` antes do env aplicado e nunca abria a conexão efetiva antes do `exec uvicorn`.

## Contrato final de configuração

`SHAMBALA_ENV_FILE` (padrão `.env.test`) é a **autoridade** do destino: o app o carrega com `override=True` (`main.py`). O que o shell pede (`SHAMBALA_TEST_DB`/`SHAMBALA_DB_NAME`) é **confrontado** com o arquivo por `trava_isolamento.exigir_configuracao_consoante` — divergir aborta rc≠0 antes de qualquer DDL/seed/startup. A identidade provada é `current_database()`/`current_user()` da **conexão efetiva**, nunca o nome declarado. Cada execução gera `SHAMBALA_EXEC_TOKEN`, ecotado na linha `[TESTE] Ambiente descartável confirmado: {…}`; `provar_destino_do_servidor()` recusa log de outra execução ou banco efetivo divergente — falha de identidade é erro da suíte, jamais PASS/PULADO. O runner materializa env temporário 0600 por execução, exige sufixo `_test` antes de DROP/CREATE, só mata o PID que registrou, e o banco descartável carrega selo de posse `preparo_descartavel(token)` — resíduo sem selo nunca é dropado por nome.

## Correções e arquivos

- `main.py` — override do env nomeado; confirmação de lifespan com token + `banco_efetivo`.
- `tests/trava_isolamento.py` — `exigir_configuracao_consoante` (com `aplicar` explícito); validação sempre com conexão; `provar_destino_do_servidor`.
- `tests/rodar_testes.sh` — env tmp por execução, `_test` antes de DROP, kill só do PID próprio, `teste_negativo_isolamento.py` na lista fixa, contagens reais por suíte.
- `tests/subir_app_descartavel.sh` — aplicar env → importar → conectar → validar → só então exec.
- `tests/preparo_descartavel.py` — selo de posse; recriação limpa de resíduo nosso; recusa de banco alheio.
- `servico_sessao.py` — `moeda_nao_negativa` (0 aceito; negativo rejeitado antes do arredondamento; HALF_UP a centavo; teto 9999999999.99 após arredondamento; `1e400`/NaN/inf/texto → `valor_invalido`); `moeda()` intocada para eventos.
- `rotas/caixa.py` — `POST /abrir` e contagem do `POST /fechar` com o parser não negativo; `GET /pdv` com `sem_sessao` quando sem movimento e conta PIX existente.
- `templates/caixa/pdv.html` — banner "apenas por PIX", espécie/fiado bloqueados sem sessão, painel PIX ativo.
- Suítes: `test_tres_passes.py` e família de prova de destino confrontam o banco efetivo em vez de forçar variável; `test_pdv_precos_servidor.py`, `test_caixa_navegador_task008.py` (C1 reescrito + C9/C10/C11 novos), `test_sessao_eventos_caixa.py` (CASO 10 estendido, LOTE legado, CASO 15) exigem token da execução corrente.
- Infra de prova: `tests/test_sentinelas_isolamento.sh`, `tests/test_mutacoes_rev01_02_03.sh`, `tests/baseline_dev.py`.

## Reproduções antes/depois (ordem §4.1)

`tests/test_mutacoes_rev01_02_03.sh` em cópia isolada (`/tmp`, produto intocado; log `docs/tasks/evidencias/TASK_010/mutacoes_antes_depois.log`, RC=0): REV-01 — com a correção o runner recusa A/B antes de DDL; reintroduzir a escolha fixa de `.env.test` faz a prova negativa falhar (avançou, rc=1). REV-02 — abrir R$ 0,00 ACEITO com a correção, RECUSADO com `moeda()` estrita de volta. REV-03 — launcher corrige aborta sem startup; a mutação nominal chega ao startup sem conexão e a prova detecta.

## Identidade do banco nas evidências

- `sentinelas_AB.log`: 4 negativos (runner e launcher reais) abortam com sentinelas SENT-A/SENT-B intactas; positivo amarra token+banco.
- `runner_completo_final.txt` (**canônica, commitável**): token `t010-20261024212542-1294779`, árvore `d12dbfcc5fb64eff17beef9b0b30ade93234d132-dirty` (sufixo apenas por rascunhos de docs não commitados), script e servidor HTTP confirmam `shambala_test_passes`; 15 suítes rc=0; sessao_eventos 161/0; navegador TASK_008 41/0; DEV inalterado. SHA-256 `0754b19026c1cac8a2f45db7d6f8a91e96a0d0f7fc749d63bd589af1932ee680`. O runner passou a imprimir o hash da árvore no cabeçalho — a identidade "base 1e57f79" usada antes era imprecisa e foi substituída.
- `runner_completo_v2.log` (RC=0, token `t010-20261024203621-1103384`) e `runner_completo.log` (v1, RC=1) — preservados como histórico das execuções prévias ao complemento do revisor.

## Resultados reais da revalidação

Matriz TASK_009 integral em banco único comprovado: misto 100+113−30 → 183 via HTTP real; recusa de legado por POST direto e em lote; PIX sem sessão no PDV (rota + navegador, espécie recusada); concorrências (duas aberturas, fechamento duplo, 8 vendas simultâneas, **CASO 8b: fechar × venda × recebimento × cancelamento no mesmo `FOR UPDATE`**) e rollback integral da última escrita; migração repetida sem duplicação; sangria com par vinculado e efeito global zero; **CASO 16: reenvio da mesma tentativa** (recebimento converge por identidade documental `mensalidade:{mov_id}`; POST do PDV não tem chave HTTP — dois envios idênticos criam duas vendas, comportamento projetado e documentado). Riscos registrados sem DDL: constraint do par de sangria ausente; reenvio cego de venda duplica. `test_financeiro_integracao.py` classificado como harness incompatível (exige `auditoria_eventos`, tabela inexistente no schema atual e já apontada como ausente antes da TASK_008 — não se afirma migração alguma); nenhuma tabela foi criada para satisfazer fixture.

## Devolução do revisor (complemento do mesmo dia)

A revisão integral apontou que o parecer não era sustentado pelo conteúdo; este complemento fecha os seis pontos **dentro da TASK_010, sem nova implementação de produto**:
- **Concorrência** — CASO 8b nomeado na suíte de eventos (fechamento concorrendo com venda, recebimento E cancelamento), com invariantes de coerência em vez de ordem esperada; verde no runner final.
- **Idempotência** — CASO 16 (reenvio byte a byte após perda de resposta, **no escopo testado**: recebimento de mensalidade 1 evento/1 baixa com recusa `erro=sem_itens` observada na 2ª tentativa; cancelamento 1 compensação; venda PDV 2 vendas legítimas) e o texto do §"Identidade por comando repetido" reescrito distinguindo identidade documental × tentativa HTTP. A duplicação por reenvio cego no PDV **continua como risco conhecido**; a proteção documental não é estendida a fluxos sem teste correspondente.
- **Legado** — registrado o estado observado com a **decisão atribuída exclusivamente ao dono**: vendas/sangrias em legado continuam permitidas pelo registro do adendo da TASK_008 (l.306), não por decisão desta revisão — alterá-la exige autorização explícita do dono. Recusas provadas apenas para novos recebimentos (`sessao_legada_recebimento`, direto e em lote) e para fechamento pela regra nova (`sessao_legada`); reconferido contra "Transição e histórico" — acumuladores legados só recebem fluxo legado (sessão `eventos` mantém `total_vendas = 0`).
- **Evidências** — `runner_completo_final.txt` canônica e commitável com hash da árvore, token, rc e contagens por suíte; v1/v2 preservados e explicados.
- **Roteiro** — corrigido: PIX sem sessão vem antes da abertura (o texto anterior abria e pressupunha ausência de sessão na mesma sequência); a divergência de contagem **com justificativa** fecha registrando-a e preservando o esperado (a regra aprovada não é "recusar sempre"); o fechamento de sessão vazia acontece num **segundo** caixa fictício.
- **`auditoria_eventos`** — alegação histórica de migração na TASK_008 removida; afirmado apenas o observável.

Produto intocado neste complemento (nenhum diff em `rotas/`, `servico_*`, `templates/` além do que a fase 1 já commitara); DEV permanece o mesmo serviço da 9000 com baseline idêntica.

**Encerramento**: o revisor declarou a TASK_010 **encerrável com ressalvas** — homologação manual do dono pendente; produção segue sem autorização de publicação; não se repetem os testes por ajustes de redação.

## DEV (§5)

Backup `docs/tasks/backups/shambala_pre_task010_20261024_205023.sql.gz` (SHA-256 `324fcd19842052f2fba575d854b04a7f400bb9172243bbd7f27f13396ec468bf`); restore validado em `shambala_t010_restauracao_test` com as 19 conferências idênticas (contagens e somas). Restart exclusivo do serviço da 9000 (plain uvicorn; 477635 → 1169423), log `dev-startup-task010.log` sem nenhum rastro de modo de teste. Baseline antes/depois idêntica (`dev_baseline_antes.txt`/`dev_baseline_depois.txt`), sessão legada Bazar id 42 intocada, nenhuma venda de demonstração, `GET /login` 200. Sem DDL novo.

## Pendências e limpeza

Nenhum novo bloqueador. Homologação manual do dono pendente (roteiro fictício em `docs/investigacoes/TASK_010_REVALIDACAO_FINANCEIRO_TASK_008.md`). Bancos de infraestrutura desta tarefa (`shambala_t010_*_test`, `shambala_t010_restauracao_test`) removidos ao encerrar; cópias isoladas em `/tmp` excluídas; processos de teste encerrados pelos PIDs reais que criamos; o serviço DEV da 9000 permanece em execução por design.

