# TASK_004 — Gestão, correções e relatórios de presença — Etapa 4

Status: implementada e aplicada no DEV (migração idempotente via restart da 9000; commits locais, sem push). Aguarda conferência do dono pelo roteiro do "Adendo de execução (DEV)".
Dependências: TASK_001, TASK_002 e TASK_003 implementadas. Confirmar código e schema atuais. A homologação física da câmera permanece pendente e não impede esta etapa.

## Objetivo

Completar a gestão das presenças dos voluntários: acompanhar o dia, lançar presença manual, corrigir registros com histórico e consultar relatórios confiáveis por período e trabalhador. Controle comunitário, sem punições, jornada ou faltas automáticas.

Usar exclusivamente o DEV existente da porta 9000, em HTTP. Não instalar nginx, TLS, certificados, CA, nova porta, túnel ou outra instância persistente. Produção já dispõe de HTTPS pelo túnel Cloudflare em https://shambala.webhop.com.br; a validação física da câmera ocorrerá após publicação autorizada, fora desta tarefa. Não publicar, fazer push ou alterar produção.

## Preparação

1. Ler AGENTS.md e normas aplicáveis; registrar este arquivo em docs/tasks/ com o número TASK_004. Preservar alterações alheias.
2. Conferir os serviços e regras existentes, relatórios, permissões e padrão de auditoria. Reutilizar a estrutura do projeto sem criar plataforma geral de auditoria.
3. Fazer backup recuperável do DEV antes de migração, conferir recuperação e registrar baseline das presenças, códigos e dados relacionados.
4. Manter as limitações anteriores: testes financeiros não foram aprovados; não declarar validação de mensalidades/fiado pela passagem do runner.

## Gestão do dia

Evoluir a tela atual de check-in, preservando o acesso da recepção pelo módulo checkin.trabalhadores:

- Data padrão: hoje em America/Sao_Paulo. Exibir a data consultada de modo destacado.
- Busca por nome e filtros simples: todos, presentes, sem registro e ausências explícitas. Agenda de hoje pode ser indicação/filtro opcional, nunca restrição de presença.
- Mostrar nome, chegada, origem e ações permitidas. Traduzir origens: QR, Código digitado, Recepção e Não informada para origem histórica NULL/legado.
- Contagem de presentes representa pessoas presentes na data, sem duplicidade. Não chamar trabalhadores sem registro de faltantes.
- Manter lançamento de chegada de hoje pela recepção utilizando o serviço existente, horário do servidor, sem exigir abertura do atendimento.
- A consulta de dias anteriores deve ser somente leitura nessa tela; correções e lançamentos retroativos usam o fluxo autorizado abaixo.
- Atualizar os dados sem exigir recarregar a página inteira, por atualização manual clara ou polling moderado somente enquanto a tela estiver visível. Não criar WebSocket se não houver necessidade. Se usar atualização automática, preservar busca e filtros e informar falha de conexão sem apagar resultados válidos.
- Tratar inativos com histórico: devem aparecer quando tiverem registro na data, identificados como inativos. A ação normal de chegada continua restrita aos ativos.

## Permissões

- Consulta e chegada de hoje continuam usando checkin.trabalhadores:ler/escrever, respectivamente.
- Histórico e relatórios mantêm as permissões já existentes, sem ampliar acesso ao cadastro financeiro.
- Introduzir permissão específica de correção, por exemplo checkin.trabalhadores.correcoes:ler/escrever, conforme o padrão real do RBAC. Administrador mantém o comportamento previsto no projeto. Não conceder correção automaticamente à recepção ou a todos os operadores.
- Leitura da permissão de correção permite consultar o histórico de alterações; escrita autoriza corrigir e lançar retroativamente. Identificar o rótulo claramente na tela de permissões. Não alterar concessões individuais/de grupos no DEV para demonstração.
- Conferir autorização no servidor em cada consulta/escrita, inclusive por acesso direto à URL. Sessão restrita do terminal não pode consultar gestão/auditoria nem corrigir presença.

## Correções e lançamentos retroativos

Criar interface simples, acessível a partir do registro/data do trabalhador, contendo estado atual, data, chegada, motivo obrigatório e confirmação.

Operações autorizadas:

1. Corrigir horário de chegada de uma presença existente.
2. Corrigir presente para ausência explícita e ausência explícita para presente.
3. Anular um lançamento incorreto, fazendo a data voltar a Sem registro, sem apagar a evidência da alteração.
4. Incluir presença ou ausência explícita que faltou em data passada ou hoje, com justificativa. Não permitir datas futuras.

Não mover um registro para outra pessoa/data silenciosamente: corrigir por anulação e novo lançamento, cada qual auditado. Não implementar alteração em lote.

Regras:

- A tela identifica claramente a data; chegada exige hora válida quando conhecida. Permitir horário desconhecido explicitamente, gravando NULL, sem fabricar 00:00 ou usar a hora atual para um fato passado.
- Ao marcar ausência, hora_chegada e hora_saida deixam de compor o fato atual; valores anteriores permanecem no histórico da correção. Ao anular, o registro deixa de participar das contagens atuais. Escolher modelo mínimo compatível; pode remover o fato da tabela operacional somente se a auditoria persistir com todos os dados necessários e sem FK que desapareça junto.
- Não usar o antigo upsert de ausência para sobrescrever uma presença sem motivo/auditoria. Revisar a rota atual de marcar ausente: criação explícita onde não havia registro pode continuar conforme a permissão vigente; qualquer alteração de fato já lançado exige correção autorizada. Repetição sem mudança não altera horário/origem nem fabrica uma correção.
- Primeiro horário preservado continua sendo a regra para repetições normais do terminal/recepção. Somente a correção autorizada pode alterá-lo.
- Correções não devem falsificar a origem de captura. Preservar a origem original quando apenas ajustar horário de um fato existente; usar origem administrativa clara para inclusão manual/retroativa ou conversão administrativa de ausência em presença. Estender a validação de origens no serviço e traduzir a nova origem nas telas. O terminal não pode enviar origem administrativa ou recepcao para obter tratamento privilegiado.
- Permitir corrigir histórico de trabalhador hoje inativo, sem reativá-lo ou emitir código. Isso não muda a proibição de chegada normal para inativos.
- Usar trava e verificação de versão/estado apresentado para impedir que duas correções se sobrescrevam ou que uma correção aplique sobre chegada alterada depois de abrir o formulário. Em conflito, não gravar e pedir atualização da tela.
- Auditoria e alteração do fato ocorrem na mesma transação: se uma falhar, nenhuma persiste. Sucesso somente após commit.
- Após anulação de presença de hoje, nova chegada legítima pelo terminal pode criar novamente a presença; essa ação não apaga o histórico da anulação. Documentar esse comportamento.

## Histórico simples das alterações

Persistir em estrutura própria ou padrão existente:

- Trabalhador e data do fato, independentes do ID descartável da presença.
- Operação realizada.
- Estado anterior e novo: existência, presente/ausente, horários e origem, incluindo vínculo histórico quando pertinente.
- Motivo informado.
- Operador autenticado responsável, com identificação suficiente para consulta histórica.
- Instante da correção definido pelo servidor, com precisão de segundos e fuso coerente.

Não permitir editar/apagar o histórico pela interface. Não usar ON DELETE CASCADE que apague a auditoria ao remover o fato. Não inventar auditoria retroativa para os 15 fatos legados. Apresentar as alterações em ordem temporal na ficha do trabalhador/data, com rótulos compreensíveis, preservando NULL como desconhecido.

## Relatórios

Evoluir as rotas existentes, preservando as correções de período/dia da semana das etapas anteriores:

- Período por data inicial/final, trabalhador opcional e filtro de origem. Oferecer todos os dias da semana, não apenas os cadastrados para atendimento, pois a presença independe dele.
- Relatório geral por trabalhador com presenças e ausências explicitamente registradas no intervalo; não contar fatos anulados, linhas de auditoria ou presenças de outro período.
- Relatório individual com data, dia da semana correto, situação, chegada, origem e indicação de ajuste quando houver, sem expor auditoria a quem não tem permissão.
- Histórico de inativos consultável quando há fatos no período, com status identificado; não fazer o passado desaparecer após inativação.
- Presenças sem dias_trabalho devem aparecer normalmente.
- Não calcular faltas a partir da agenda atual. Não apresentar percentual como assiduidade. Preferir retirar o percentual para evitar interpretação errada; se mantiver proporção entre registros explícitos, nomear e explicar o denominador claramente, sem incluir Sem registro como ausência.
- Manter impressão legível, ocultando controles, sem cortar colunas essenciais.
- Não acrescentar exportação CSV/Excel/PDF backend nesta etapa; impressão pelo navegador é suficiente.
- Validar intervalo e parâmetros no servidor: data inválida/inicial posterior à final produz mensagem clara, sem 500.

## Migração e preservação

Schema de correções/origem administrativa deve nascer corretamente em instalação nova e migrar o banco existente de forma transacional/idempotente. Não modificar códigos atuais/retirados, FK RESTRICT, carteirinhas, horários/fatos históricos ou situação dos trabalhadores como efeito de startup.

Documentar backup e recuperação, reconhecendo que restaurar um dump antigo perde escritas posteriores. Não executar restauração sobre DEV com fatos novos como se fosse rollback sem perda.

## Validação obrigatória

Testes em PostgreSQL nativo descartável, sem Docker. Processos temporários devem terminar ao final; não deixar instâncias extras de homologação.

| Cenário | Resultado esperado |
|---|---|
| Gestão sem dia aberto e sem agenda | Lista e chegada funcionam; atendimento não muda |
| Sem registro, ausência explícita e presente | Estados distintos e contagens coerentes |
| Repetição normal | Primeiro horário e origem preservados; sem auditoria falsa |
| Correção de horário/estado e inclusão retroativa | Fato e auditoria corretos com responsável e motivo |
| Motivo vazio, horário/data inválidos ou data futura | Recusa sem escrita parcial |
| Presença histórica com horário desconhecido | Continua desconhecido até correção explícita |
| Presente convertido em ausente | Horários atuais limpos; anteriores preservados na auditoria |
| Anulação e nova chegada hoje | Sem registro após anulação; nova chegada não apaga auditoria |
| Duas correções e chegada concorrentes | Sem perda silenciosa; conflito detectado quando aplicável |
| Falha ao escrever auditoria | Nenhuma alteração de presença persiste |
| Inativo com histórico | Consultável/corrigível por autorizado, sem reativação/código novo |
| Operador comum, anônimo e sessão terminal | Correções/auditoria protegidas no servidor |
| Histórico e relatório por período/origem/dia | Incluem só fatos correspondentes, inclusive sem dias_trabalho |
| Auditoria e registros anulados | Não aumentam totais de presença |
| Segunda execução da migração | Nenhum fato/código/auditoria alterado |

Executar o runner completo, incluindo as suítes das TASK_001–003, e registrar números exatos. Validar telas no navegador em desktop e smartphone: gestão do dia, formulário de correção, conflito, histórico, relatório e impressão. Usar dados fictícios nas evidências.

No DEV, preservar baseline e códigos reais; não corrigir/apagar presenças reais para provar funcionamento. Sem credenciais autorizadas, registrar a limitação e entregar roteiro de conferência, sem criar conta para contornar.

## Entrega e encerramento

Entregar implementação, migração, testes e relatório anexado a esta tarefa:

1. Modelo da auditoria e semântica de anulação/origem/correção.
2. Rotas, rótulos/permissões e comportamento de concorrência.
3. Evidências de preservação de fatos e códigos, migração e testes executados.
4. Roteiro curto no DEV para registrar hoje, corrigir, anular e consultar histórico/relatório.
5. Limitações verdadeiras, inclusive câmera física e cobertura financeira; não confundir implementação com homologação operacional.
6. Commit local somente da tarefa, sem push, confirmação de produção intocada e encerramento de recursos descartáveis.

Atualizar SHAMBALA_CONTEXTO.md. Atualizar o status e mover para docs/tasks/done/ quando os critérios estiverem atendidos. Não publicar ou criar tarefa de implantação sem autorização posterior.

---

## Adendo de execução (DEV) — 2026-10-24

### O que foi implementado

- **Auditoria**: tabela `trabalhador_presenca_correcao` (colunas planas de
  antes/depois, `operacao` com CHECK, `motivo` obrigatório não vazio,
  `atendente_id`, `criado_em` no fuso de São Paulo), criada por
  `CREATE TABLE IF NOT EXISTS` dentro de `criar_tabelas()` — idempotente, sem
  tocar fatos, códigos ou situações. Origem administrativa
  `correcao_administrativa` entra em `ORIGENS_CHEGADA` mas é **recusada** por
  `registrar_chegada`: só `servico_correcao_presenca.aplicar_correcao` a escreve.
- **Serviço de correção** (`servico_correcao_presenca.py`): ponto único de
  escrita com `FOR UPDATE` no trabalhador (a mesma trava da chegada do
  terminal), re-confirmação do fato contra o token `estado_base` do formulário
  (`ConflitoCorrecao` sem escrita em caso de divergência), validações de
  motivo/data (recusa futuro; aceita hoje e passado)/horário (`HH:MM` ou
  branco = desconhecido declarado, nunca 00:00 nem hora atual inventada).
  Anulação = `DELETE` da linha operacional (a data volta a "Sem registro" e o
  `UNIQUE(trabalhador_id, data)` libera a recriação por chegada legítima, sem
  apagar auditoria). Fato + auditoria na **mesma transação**: provado com
  trigger de erro — falhou a auditoria, o fato não persiste.
- **`registrar_ausencia_explicita` redesenhado**: nunca mais rebaixa uma
  presença (o upsert destrutivo `ON CONFLICT SET presente=0` foi removido);
  sobre fato presente devolve `exige_correcao`, e a rota traduz isso em aviso
  que aponta o fluxo autorizado.
- **Permissão nova** `checkin.trabalhadores.correcoes` (ler/escrever) no
  catálogo `ROTULOS_ROTA`; **sem concessão automática** em seed. Confirmando
  no DEV: `concessoes=0` em `grupos_permissoes` para o módulo — só o grupo
  Admin (bypass) alcança, até o dono conceder na tela de Permissões.
- **Gestão do dia** (`/checkin-trabalhador`): data consultável (default hoje
  SP; inválida cai para hoje com aviso), busca e filtros server-side
  (`todos|presentes|sem_registro|ausencias`, `so_agenda` como indicação),
  coluna/tradução de origem, contador de presentes, inativo aparece só com
  registro na data (selo "inativo (histórico)"), **dia passado sem nenhum
  botão de escrita** (consulta somente leitura), atualização parcial por
  `GET /checkin-trabalhador/dados` (JSON) com botão ↻ e polling de 30 s
  condicionado a aba visível e data = hoje; queda de rede avisa **sem apagar**
  os últimos resultados. A agenda continua indicação, nunca restrição.
- **Correções**: `GET/POST /checkin-trabalhador/{id}/correcao?data=` com
  estado atual, operações permitidas pelo estado, motivo obrigatório,
  confirmação, token oculto `estado_base`, "Alterações desta data" na mesma
  tela e desfechos por flash (`corrigido`, `conflito`, `erro_motivo`,
  `erro_data`, `erro_horario`, `erro_operacao`, `erro_fato`). GET=ler,
  POST=escrever no servidor, inclusive por URL direta; sessão do terminal não
  alcança nada daqui.
- **Ficha** (`/cadastros/trabalhadores/{id}/presenca`): origem por registro,
  selo "✎ ajustado" e link de correção por data; seção "Histórico de
  alterações" **somente** para quem tem `correcoes:ler` — a recepção comum não
  vê nada de auditoria.
- **Relatórios**: geral e individual com dia da semana entre os **7 fixos**
  (ISODOW sobre `tp.data`, sem depender de `dias_atendimento`), filtro de
  origem (inclui "Não informada"), trabalhador opcional, **sem colunas de
  percentual** (nota explica o denominador: registros explícitos; sem registro
  não é falta), **inativos continuam no relatório** (o `WHERE t.ativo=1` que
  apagava o passado foi removido; situação vira selo), dia da semana traduzido
  em Python (não `to_char(...,'Day')`, armadilha de locale), período com hoje
  em São Paulo e validação server-side sem nunca dar 500, e CSS de impressão
  que esconde controles (`@media print`) com botão 🖨 nos dois viewports.

### Correções de defeito reais

| Defeito anterior | Estado agora |
|---|---|
| `registrar_ausencia_explicita` rebaixava presença mantendo `hora_chegada` (linha incoerente, sem auditoria) | recusa com `exige_correcao`; alteração autorizada e auditada |
| Relatório geral com `WHERE t.ativo = 1` apagava o histórico de inativados | inativo com registro no período aparece com selo de situação |
| Filtro de dia só oferecia os dias de `dias_atendimento` | os 7 dias fixos, coerente com presença independente de agenda |

### Validação executada

Runner completo (`bash tests/rodar_testes.sh`), porta 9011, banco descartável
`shambala_test_passes`, DEV conferido antes/depois — **inalterado**
(`pessoas=664 checkins=2807` idênticos), RC=0:

| Suíte | Resultado |
|---|---|
| test_tres_passes.py | 27/27 |
| test_plano_unico.py | 12/12 |
| test_permissoes.py | 8/8 |
| test_agenda_medium.py | 10/10 |
| test_presenca_trabalhadores.py (TASK_001) | 19/19 |
| test_codigo_trabalhador.py (TASK_002) | 18/18 |
| test_terminal_presenca.py (TASK_003) | 18/18 |
| test_terminal_leitor.py (TASK_003, navegador) | 24/24 |
| **test_correcoes_presenca.py (TASK_004, backend)** | **21/21** |
| **test_gestao_correcoes_navegador.py (TASK_004, navegador)** | **11/11** |

**168/168** no total, sem regressão nas etapas anteriores. A matriz cobriu os
15 cenários obrigatórios da ordem, inclusive: repetição normal sem auditoria
falsa; `exige_correcao` sobre presença; corrigir preservando origem;
presente→ausente limpando horários no fato e guardando os anteriores;
ausência→presença com origem administrativa; recusas sem escrita parcial;
desconhecido permanece desconhecido; anulação + recriação; formulário velho ×
correção nova e correção × chegada do terminal (um único efeito coerente);
falha na auditoria ⇒ fato não persiste; inativo corrigível sem reativar nem
emitir código; anônimo/operador comum/sessão-terminal barrados inclusive por
URL direta; anulados e auditoria não inflam totais; `criar_tabelas()` duas
vezes sem alteração. A suíte de navegador dirigiu as telas reais (desktop
1366×768 e smartphone 412×915) com evidências em
`~/Documentos/evidencias-task004-gestao-correcoes/` — nomes fictícios do banco
descartável.

Dois ajustes de teste feitos durante a validação (não mudam comportamento do
produto): a asserção de data inválida esperava `AAAA/MM/DD` onde a tela exibe
`DD/MM/AAAA`; e o relatório geral no smartphone vazava 1,3 px na horizontal —
corrigido de fato no template com `@media (max-width: 480px)` apertando as
colunas das duas telas de relatório.

### Migração no DEV

Backup prévio recuperável: `~/Documentos/backup-shamballa/shambala-DEV-pre-TASK004-20261024.dump`
(`pg_dump -Fc`, 335 KB, TOC lido com sucesso). A migração entrou **apenas pelo
restart do serviço existente da porta 9000** (HTTP, sem nginx/TLS/porta nova,
conforme decisão do dono):

| Verificação | Antes | 1ª subida | 2ª subida |
|---|---|---|---|
| trabalhadores | 70 | 70 | 70 |
| presenças | 15 | 15 | 15 |
| códigos (ativos / retirados) | 70 / 0 (70/0) | 70 / 0 (70/0) | 70 / 0 (70/0) |
| linhas de auditoria | tabela não existia | 0 | 0 |
| `concessoes` do módulo `correcoes` | — | 0 | 0 |

Zero fatos corrigidos/apagados, zero códigos gerados, log de subida sem erro
nem ambiguidade, `GET /login` respondendo 200 na 9000. Produção **intocada**;
nenhum grupo do DEV recebeu a permissão nova em demonstração.

### Roteiro de conferência para o dono (no DEV, com credenciais reais)

0. Criar em Cadastros → Trabalhadores um trabalhador **fictício identificado**
   para as provas de escrita: `Homologacao TASK-004 (teste)`. Conferir que ele
   nasce sem presença. **Todos os lançamentos, correções e anulações deste
   roteiro são feitos só nesse trabalhador fictício — nos demais, nada
   corrigir nem anular: preservar os fatos reais.**
1. Entrar com usuário de recepção que **não** tenha `correcoes` → a gestão do
   dia funciona, mas não há link "✎ Corrigir/lançar" nem acesso a histórico.
2. Entrar como Admin (bypass) → gestão do dia de hoje; no fictício
   `Homologacao TASK-004 (teste)`, registrar ✔ Presente / ✕ Ausente e ver a
   coluna de origem.
3. Navegar ← para ontem → página em consulta somente leitura (sem botões);
   buscar por nome e filtrar "Sem registro"; apertar ↻ Atualizar.
4. Abrir "✎ Corrigir/lançar nesta data" do fictício com presença registrada
   hoje → corrigir o horário com motivo e confirmar; conferir o aviso, o selo
   "ajustado" e a tabela "Alterações desta data".
5. No mesmo fictício: tentar aplicar sem motivo (o navegador barra) e por URL
   com data futura → recusa com aviso claro e nada gravado.
6. Abrir a ficha de presença do fictício → selo ajustado, origem por
   registro e "Histórico de alterações" com autor e motivo.
7. Conceder `checkin.trabalhadores.correcoes` (ler/escrever) a um grupo de
   teste na tela de Permissões e conferir que o operador passa a ver só o que
   a permissão manda (leitor vê histórico sem formulário; sem escrever não há
   POST possível).
8. Relatórios → geral e individual com filtro de **trabalhador pesquisável
   por nome**, dia entre os 7 e origem; nenhum percentual; inativo com
   registro aparece com selo; "🖨 Imprimir" esconde controles no PDF do
   navegador.
9. Encerrar as provas **inativando** o fictício (não excluí-lo: enquanto houver
   histórico de correções a exclusão é barrada de propósito) e conferir que ele
   segue aparecendo nos relatórios com o selo "inativo" e o preservado.
10. Testar o terminal no endereço HTTPS existente para a câmera (fora do escopo
    desta entrega; a digitação já funciona no HTTP da LAN).

### Limitações verdadeiras

- **Câmera não é homologável no DEV HTTP**: decisão do dono manter a 9000 em
  HTTP sem nginx/TLS. A leitura por QR foi validada por teste automatizado com
  vídeo simulado (código real de carteirinha, jsQR vendorizado, `getUserMedia`
  real), não por aparelho físico neste ambiente.
- **Suítes financeiras não aprovadas nesta tarefa**: mensalidades/fiado não
  foram homologadas; o runner cobre o que roda, não o resto.
- **Registros anteriores a esta entrega não têm histórico de correções**;
  quando a origem não foi informada, as telas dizem "Não informada", nunca
  inventam.
- **Nenhuma correção real foi aplicada no DEV** de propósito: não havia
  lançamento errado a demonstrar e corrigir fatos reais não é papel de
  demonstração. O roteiro acima permite ao dono exercitar o fluxo.

Status: **concluída em DEV, commit local, sem push.** Produção não recebeu
nada; a publicação exige autorização e tarefa de implantação próprias.

## Adendo de acabamento — ajustes pedidos pelo dono (2026-10-24)

Cinco ajustes pontuais sobre a entrega acima. Nenhum esquema mudou; a subida no
DEV foi apenas o restart do serviço existente da 9000.

| # | Ajuste | Onde ficou |
|---|---|---|
| 1 | Cabeçalho dizia "pronta para execução" | Status real no topo desta ordem |
| 2 | Explicações de tela com jargão interno ("denominador", "legado esperado", "linha de auditoria") | Nota dos relatórios agora é uma frase: *Sem registro não significa falta. Lançamentos anulados não entram nos totais.* Ficha: *Somente consulta: este histórico não pode ser editado nem apagado por aqui.* Filtro de origem diz apenas "Não informada" |
| 3 | Instante da correção cru, com microssegundos | Filtro Jinja `data_hora_br` em `templates_config.py`; correção e ficha exibem `DD/MM/AAAA HH:MM:SS` |
| 4 | Campo livre "Trabalhador" (id numérico) nos relatórios | Seletor **pesquisável por nome** nos dois relatórios (`<input list>` + `datalist` e `<script type="application/json">` com a lista); nome que não bate com ninguém = nenhum filtro; id na URL volta como nome no campo |
| 5 | Roteiro de homologação expunha fatos reais a correções | Roteiro reescrito: as provas de escrita (passos 2, 4, 5, 6) correm **só sobre um trabalhador fictício identificado** — `Homologacao TASK-004 (teste)` — que no fim é **inativado** (a exclusão é barrada pela FK enquanto houver histórico, o que é o comportamento desejado) |

### Validação dos ajustes

| Suíte | Antes | Depois |
|---|---|---|
| `test_presenca_trabalhadores.py` (TASK_001) | 19 | 19 (H7 passou a medir **a tabela**, não a página inteira — o seletor novo lista toda a gente no `datalist`) |
| `test_correcoes_presenca.py` (TASK_004) | 21 | 21 (C18 idem) |
| `test_gestao_correcoes_navegador.py` (TASK_004) | 11 | 11 (N9 agora filtra pelo nome digitado, verifica o id resolvido, o nome desconhecido sem filtro e o id da URL reaparecendo como nome; N8 confere o instante com `DD/MM/AAAA HH:MM:SS`) |
| Runner completo (10 suítes) | 168/168 | **168/168, RC=0** — 27+12+8+10+19+18+18+24+21+11 |

Ajustes de código que a própria bateria exigiu: `trabalhador_id` dos relatórios
passou a ser `str` validado (vazio = todos) porque o formulário novo pode
submeter o campo em branco; a lista de opções do seletor é lida antes da
validação de período para as telas de erro continuarem com o filtro utilizável;
e os dois relatórios passaram a usar exatamente a mesma chave (`nome_completo`)
no `datalist`, no JSON embutido e no JavaScript.

**Prova de que a bateria enxerga o produto.** Ao escrever o seletor, a rota
geral devolvia `nome` e o template lia `nome_completo`: o `datalist` nasceria
vazio. O cenário N9 antigo **passava mesmo assim** (preenche o campo por
`fill()`). Acrescentamos a N9 a verificação de que o seletor carrega os 4 nomes
do cenário; reintroduzimos o defeito de propósito e **N9 falhou (10/11)**,
depois do quê a chave foi unificada e tudo voltou a verde. A medição de filtro
em H7/C18 também mudou de HTML-inteiro para **só a tabela**, porque o seletor
novo lista toda a gente na página — sem isso, "não apareceu" virava
"apareceu" por causa do JSON embutido.

### DEV depois do acabamento

| Verificação | Resultado |
|---|---|
| Subida | restart do serviço existente da 9000 (HTTP), sem nginx/TLS/porta nova; **zero DDL** |
| Baseline | `trab=70 pres=15 cod=70 ativos=70 retirados=0 corr=0` — idêntico ao registrado na migração |
| Permissão nova | `concessoes=0` em `grupos_permissoes` |
| HTTP | `GET /login` → 200; `/relatorios/presenca-trabalhadores` e `/checkin-trabalhador` → 303 (exigem login, como antes) |
| Log | `Started server process [1718027]` em diante: zero traceback/erro |

Nenhum fato real foi corrigido, anulado ou apagado. Testes financeiros
permanecem não aprovados; o `checkin.trabalhadores.correcoes` continua sem
concessão a qualquer grupo do DEV.

