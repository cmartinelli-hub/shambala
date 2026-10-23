# Shambala — Etapa 1: base de presença independente do atendimento

Data: 23/10/2026
Status: pronta para execução pelo Qoder, exclusivamente no DEV.
Numeração: atribuir o próximo número disponível conforme as normas do repositório. Não presumir um número.

## Objetivo e autorização

Investigar e implementar a base de presença dos voluntários para que qualquer trabalhador ativo possa registrar sua chegada antes, durante ou depois da abertura do dia de atendimento. Executar apenas esta etapa, sem avançar para QR, códigos ou carteirinhas.

O controle é comunitário, baseado na confiança, sem remuneração ou punição associada à frequência. Não construir controle de jornada, antifraude ou autenticação individual complexa.

Produção está fora do escopo: não acessar, migrar, publicar ou reiniciar serviços de produção. Não alterar túneis ou infraestrutura pública.

## Antes de alterar

1. Ler AGENTS.md e as normas aplicáveis; conferir branch, alterações existentes e configuração efetiva do banco DEV. Preservar trabalho alheio e não fazer commit de arquivos de outra tarefa.
2. Registrar a tarefa em docs/tasks/ conforme a convenção existente, com nome sem espaços.
3. Conferir o código atual. O RELATORIO_MODULO_TRABALHADORES.md de 23/10/2026, commit analisado 7238c80, é referência de investigação, não substitui a leitura da implementação.
4. Levantar todos os leitores e escritores de trabalhador_presenca, inclusive rotas legadas, histórico individual e relatórios. Confirmar constraints, inicialização do schema e mecanismo de migração.
5. Antes da migração no DEV, produzir backup recuperável do banco DEV e registrar contagens e uma comparação dos registros antigos, incluindo ausências e horários nulos. Não pressupor que as contagens do relatório continuam atuais.

## Regras aprovadas

- Uma presença por trabalhador por data local, com unicidade garantida no banco.
- Data e horário da chegada determinados pelo servidor, usando explicitamente America/Sao_Paulo, independentemente do fuso do sistema operacional e do relógio do dispositivo.
- Apenas trabalhadores ativos podem receber uma nova presença pelo fluxo normal. Inativação não apaga o histórico.
- A agenda semanal não restringe o registro de chegada.
- Não exigir um registro em dias_trabalho, nem aberto = 1. Não criar, abrir ou reabrir dias de atendimento para registrar presença.
- Repetições e requisições concorrentes não duplicam presença nem substituem o primeiro horário de chegada. A resposta deve distinguir novo registro de presença já registrada.
- Registrar somente chegada. Não implementar saída ou alternância entrada/saída.
- Não gerar faltas automaticamente, nem ao encerrar o atendimento.
- Manter as ausências explicitamente registradas no histórico anterior. Ausência de registro não equivale a falta.
- Se já houver ausência explícita na data e a pessoa chegar, converter para presente de forma consistente e registrar a chegada atual. Se já for presente, preservar o horário existente; não inventar horário para presença histórica cujo horário seja desconhecido.

## Implementação

### 1. Modelo e migração

Preferir uma data própria de presença, com tipo DATE, desvinculada da obrigatoriedade de dias_trabalho. Escolher a adaptação mais simples compatível com o projeto e explicar a decisão no relatório. Não criar uma segunda fonte de verdade para novas presenças.

A migração deve:

- Derivar a data dos registros antigos a partir do dia de trabalho ao qual já estão vinculados.
- Preservar trabalhadores, datas, presente/ausente, chegada, saída e vínculos históricos ainda necessários.
- Não fabricar segundos, horários ou datas desconhecidos nos dados legados.
- Verificar conflitos, datas inválidas e duplicações antes de criar a nova unicidade. Diante de dados ambíguos, relatar o caso concreto e não descartar ou mesclar silenciosamente.
- Ser transacional quando suportado pelo mecanismo usado e segura em uma segunda execução, inclusive no startup se o schema for mantido por esse caminho.
- Contemplar banco existente e instalação nova. Evitar que o DDL de startup recrie a obrigatoriedade antiga ou constraints incompatíveis.

Não exigir uma reformulação geral do schema. Documentar o procedimento de recuperação do DEV e seus limites, especialmente depois de existirem registros sem dia de atendimento associado. Não executar rollback que apague esses registros.

### 2. Serviço de registro

Centralizar a regra de chegada em uma função ou serviço reutilizável pela recepção e, futuramente, pelo terminal QR/código. Utilizar o padrão transacional do projeto e a unicidade no banco para tratar concorrência.

O serviço recebe a identificação do trabalhador; a data e o horário do fluxo normal vêm do servidor. Validar existência e situação ativa. Retornar dados suficientes para a confirmação: trabalhador, data, horário e indicação de registro criado ou já existente. Manter as permissões atuais; não abrir endpoint anônimo nesta etapa.

### 3. Fluxo atual e compatibilidade

Adaptar o check-in existente para usar o serviço e funcionar sem abertura do dia. Disponibilizar busca/seleção de todos os trabalhadores ativos, mesmo sem agenda configurada, mantendo a indicação da agenda se for útil.

Eliminar a tela de bloqueio por dia fechado nesse fluxo. Preservar a separação de permissões entre recepção e cadastro/financeiro. Dar retorno claro em duplicidade e trabalhador inativo, sem substituir a primeira chegada.

Mapear rotas legadas e impedir que continuem gravando pelo modelo antigo. Conforme o uso real, remover duplicação ou encaminhar para o fluxo vigente, mantendo a permissão adequada e sem ampliar acessos.

Adaptar histórico e relatórios afetados para ler a data própria, incluindo presenças em datas sem dias_trabalho. Corrigir nesta etapa os dois defeitos diretamente relacionados: contagem fora do período no relatório geral e divergência entre weekday 0–6 e ISODOW 1–7. Não deixar relatórios quebrados até a etapa 4. A etapa posterior continua responsável por melhorias de gestão e apresentação.

Não transformar as taxas existentes em uma medição de assiduidade pela agenda. Mostrar ou explicar corretamente o universo de registros contabilizados, sem gerar faltas implícitas.

## Fora do escopo

- Gerar códigos de quatro dígitos, QR, carteirinhas ou impressão em lote.
- Criar terminal de câmera, pareamento de aparelho, funcionamento offline ou nova autenticação.
- Registrar saída, jornada, atraso, penalidades ou notificações de ausência.
- Criar escala histórica ou novo cálculo de faltas pela agenda.
- Construir nova interface de correções e auditoria; isso pertence à etapa de gestão.
- Corrigir cadastro, upload de foto ou integrações financeiras sem relação necessária com esta mudança.
- Modificar mensalidades, fiado, atendimentos ou os três passes após acolhimento.

## Validação obrigatória

Usar PostgreSQL nativo descartável conforme o padrão do projeto, sem Docker. Não executar testes de escrita em produção.

| Cenário | Resultado exigido |
|---|---|
| Data sem dias_trabalho | Registra presença; não cria dia de atendimento |
| Dia fechado | Registra presença; atendimento permanece fechado |
| Dia aberto | Registra normalmente |
| Trabalhador ativo sem agenda ou fora dela | Registra normalmente |
| Trabalhador inexistente ou inativo | Recusa nova presença, sem escrita indevida |
| Repetição e requisições concorrentes | Um registro, primeira chegada preservada |
| Ausência explícita seguida de chegada | Uma linha coerente, agora presente |
| Presença histórica sem horário | Repetição não fabrica horário de chegada |
| Virada de data UTC/São Paulo | Usa a data de São Paulo |
| Migração de dados antigos | Mesmos fatos, datas, estados e horários anteriores |
| Segunda aplicação da migração/startup | Sem duplicação, perda ou erro de schema |
| Banco novo | Schema nasce compatível com o novo fluxo |
| Relatório de período vazio | Contagens zeradas, sem trazer outros meses |
| Filtro segunda, quarta e domingo | Dias corretos, sem deslocamento |
| Presença sem dia de atendimento | Aparece no histórico e relatórios |
| Usuário sem permissão | Acesso recusado, inclusive em rotas legadas mantidas |
| Recepção | Registra sem ganhar acesso ao cadastro financeiro |

Executar também a regressão existente pertinente a trabalhadores, mensalidades, fiado e abertura/encerramento de atendimento. Relatar limitações reais de cobertura; não declarar validação que não foi executada. Verificar a tela de check-in no navegador.

## Entrega e encerramento

Entregar implementação, migração, testes e documentação. No relatório informar:

1. Diagnóstico confirmado e modelo escolhido.
2. Arquivos alterados e compatibilidade das rotas.
3. Evidências da preservação do histórico e da segunda execução da migração.
4. Resultados dos testes, inclusive concorrência e fuso horário.
5. Roteiro curto para conferir no DEV uma chegada antes da abertura do atendimento.
6. Backup e procedimento de recuperação, sem incluir segredos.
7. Commit local da tarefa, se previsto nas normas, limitações e pendências.

Concluir conforme a convenção do repositório e mover a tarefa para docs/tasks/done/ somente após cumprir os critérios de aceite. Não publicar em produção e não iniciar a etapa 2. Encerrar apresentando o resultado para revisão.

---

# Relatório de execução — concluída em 23/10/2026

## 1. Diagnóstico confirmado e modelo escolhido

**Diagnóstico** (verificado contra o código do commit `7238c80`, não apenas o relatório de referência):

- `trabalhador_presenca` não tinha data própria; a data só existia via `dias_trabalho` (`dia_trabalho_id` NOT NULL). Check-in vigente (`/checkin-trabalhador`) exigia dia com `aberto = 1` e exibia tela de bloqueio (`dia_sem_abertura.html`) — por isso a chegada era impossível sem abrir o atendimento.
- Havia **4 escritores** (2 no fluxo vigente + 2 legados em `/cadastros/trabalhadores/dia/trabalhador-checkin*`, este último com forms apontando para as rotas novas — usuário só de cadastro tomava 403) e **5 leitores** (histórico individual, tela legada, tela de check-in, relatório geral, relatório individual).
- Data e hora vinham do relógio do SO (`date.today()`/`datetime.now()`), sem America/Sao_Paulo explícito.
- Defeito 1 confirmado: no relatório geral o `BETWEEN` estava no `ON` do join com `dias_trabalho`, mas o `COUNT/SUM` contava **todo o histórico** (`LEFT JOIN trabalhador_presenca` sem filtro).
- Defeito 2 confirmado: filtro `EXTRACT(ISODOW …)` (1–7) recebia valores do select alimentado por `dias_atendimento.dia_semana` (0–6) — segunda (0) nunca casava e os demais dias deslocavam uma posição.
- Baseline do DEV na data: **15 registros** (não as contagens antigas do RELATORIO_MODULO_TRABALHADORES), todos presentes, 7 trabalhadores, datas 2026-04-06 a 2026-05-06, sem datas inválidas, sem ausências, sem horários nulos, sem conflitos de unicidade.

**Modelo escolhido** (adaptação mais simples compatível): a própria `trabalhador_presenca` ganhou coluna **`data DATE NOT NULL`** com **UNIQUE (trabalhador_id, data)**; `dia_trabalho_id` passou a **opcional** (vínculo histórico preservado nos registros antigos; novos registros **não** o preenchem). Descartou-se criar tabela nova: manteria segunda fonte de verdade e exigiria reescrever todos os leitores; a coluna própria resolve o desacoplamento com o mínimo de superfície. O dia de atendimento continua existindo só para o atendimento em si — presença não cria, abre nem reabre dia.

## 2. Arquivos alterados

| Arquivo | O que mudou |
|---|---|
| `banco.py` | DDL de banco novo (`data DATE NOT NULL`, `dia_trabalho_id` opcional, UNIQUE trabalhador+data); nova `_migrar_presenca_data()` chamada por `_migrar()` no startup (audita antes de alterar; idempotente; transacional junto com `criar_tabelas()`); removida a antiga re-assert da UNIQUE `(trabalhador_id, dia_trabalho_id)` |
| `servico_presenca.py` (novo) | Serviço único `registrar_chegada()` / `registrar_ausencia_explicita()`: data/hora do servidor em America/Sao_Paulo via SQL (`NOW() AT TIME ZONE …`), valida existência e situação ativa, `ON CONFLICT (trabalhador_id, data)` preserva a primeira chegada, converte ausência explícita em presente, nunca inventa horário para presença histórica sem horário; retorna `criado` (novo × já registrada) |
| `rotas/trabalhadores.py` | Check-in reescrito sobre o serviço: tela lista **todos os ativos** (busca funciona sem agenda; chip "📌 agenda de hoje" como indicação), sem bloqueio por dia fechado, aviso de status pós-POST; histórico passa a ler `tp.data`; **rotas legadas removidas** (GET/POST `/cadastros/trabalhadores/dia/trabalhador-checkin*` → 404) |
| `rotas/relatorios.py` | Geral e individual leem `tp.data` (presenças sem dia aparecem); defeito 1 corrigido (filtro de período dentro do join com `trabalhador_presenca`); defeito 2 corrigido (select 0–6 → ISODOW com `+1`); sem join obrigatório com `dias_trabalho` |
| `templates/trabalhadores/checkin.html` | Sem tela de bloqueio; banner de confirmação; status com horário de chegada; busca sobre todos os ativos; corrigido resíduo "None" do mapa de presença |
| `templates/trabalhadores/dia_sem_abertura.html` | **Removida** (bloqueio por dia fechado eliminado) |
| `templates/relatorios/presenca_trabalhadores.html`, `presenca_individual.html` | Nota explicando o universo contabilizado: só registros existentes; ausência de registro não é falta; taxa não mede assiduidade pela agenda |
| `tests/test_presenca_trabalhadores.py` (novo) + `tests/rodar_testes.sh` | 19 testes (serviço, migração, HTTP/relatórios); suíte adicionada ao runner |
| `RELATORIO_SISTEMA.md` | §5.8 desatualizado corrigido (rota legada e "qualquer usuário logado") — arquivo **não commitado** pois já era untracked de outra tarefa |

**Compatibilidade de rotas**: `/checkin-trabalhador` (GET/POST presente/ausente) mantém URL e permissão `checkin.trabalhadores` (recepção continua sem acesso a cadastro/financeiro; verificado em teste). As 3 URLs legadas foram removidas (404) — nenhum template ou JS as referenciava (só o menu, que aponta para a rota vigente). Histórico e relatórios mantêm URLs/permissões; passam a incluir presenças sem dia de atendimento.

## 3. Preservação do histórico e segunda execução (evidências)

**Comparação antiga × nova no DEV** — antes (baseline, 15 linhas listadas id a id neste relatório) e depois da migração: mesmas 15 linhas, mesmos `trabalhador_id`, `presente=1`, `hora_chegada` (inclusive os `00:00` legados — nada fabricado), vínculos `dia_trabalho_id` mantidos, datas derivadas exatamente do dia associado (`divergentes_do_dia=0`), sem perdas (`linhas=15`). Constraints finais no DEV: `trabalhador_presenca_data_unique UNIQUE (trabalhador_id, data)`; UNIQUE antiga removida; `dia_trabalho_id` nullable; `data` NOT NULL.

**Segunda execução**: o servidor DEV foi reiniciado uma segunda vez com o schema já migrado — startup limpo, sem alertas de migração, contagem seguiu 15. Em testes automatizados: `M2` (criar_tabelas duas vezes sobre dados antigos → 1 linha, fatos intactos) e `M5` implícito no runner (cada suíte reinicializa o schema). Ambiguidade: `M3` prova que data ilegível faz a migração **relatar o caso concreto e não alterar o schema** (sem descartar nem mesclar). Banco novo: `M4` prova que o schema nasce no formato do fluxo novo e o serviço grava sem dia de atendimento.

**Recuperação do DEV e limites**: restaurar `~/Documentos/backup-shamballa/shambala_pre_TASK001_2026-10-23.dump` (`pg_restore -d shambala --no-owner`, verificado: restore bateu todas as contagens) devolve o banco ao modelo antigo. **Limite**: a partir do momento em que existirem presenças registradas só na data própria (sem `dia_trabalho_id`), o restore do backup perde esses fatos — não há rollback que os preserve; a correção futura é pontual (etapa de gestão). Nenhum rollback foi executado.

## 4. Testes executados e resultados

- **Suíte nova: 19/19** (PostgreSQL nativo descartável, sem Docker, via `tests/rodar_testes.sh`): U1 data sem dias_trabalho (não cria dia) · U2/U3 inexistente/inativo recusados sem escrita · U4 repetição preserva primeira chegada e distingue "já registrada" · U5 ausência→chegada vira uma linha presente · U6 presença histórica sem horário não ganha horário · **U7 concorrência: 8 threads simultâneas → exatamente 1 registro e um único "novo"** · **U8 virada UTC/São Paulo: 02:30 UTC → data 22/10 23:30 e 03:30 UTC → 23/10 00:30, imune ao fuso da sessão (Tóquio)** · M1–M4 migração/ambiguidade/segunda execução/banco novo · H1–H9 fluxo HTTP (dia fechado registra e atendimento segue fechado; legadas 404; RBAC recepção × cadastro; período vazio zerado; filtro segunda/quarta/domingo correto; presença sem dia no histórico).
- **Regressão completa: 76/76** — `test_tres_passes 27/27`, `test_plano_unico 12/12`, `test_permissoes 8/8`, `test_agenda_medium 10/10`, `test_presenca_trabalhadores 19/19`, com o runner recriando o banco descartável e confirmando DEV inalterado antes/depois (`pessoas=664 checkins=2807` idênticos). Mensalidades/fiado cobertos pelas suítes de regressão; abertura/encerramento intocado (nenhum writer novo em `dias_trabalho`).
- **Navegador**: app real (instância de teste, código idêntico) dirigido via Chromium — login, tela sem nenhum dia aberto lista ativos, busca, clique em Presente → banner "Chegada … registrada às 13:35", reload mostra "✔ Presente — 13:35" sem os botões, relatório geral com filtro segunda renderiza. Capturas em `/tmp/task001_checkin_lista.png`, `/tmp/task001_checkin_pos_presente.png`, `/tmp/task001_relatorio_geral.png`.

**Limitações reais de cobertura**: (a) o clique no navegador foi validado na instância de teste com login de teste — a porta 9000 do DEV foi validada por HTTP (rota viva, redireciona ao login, legada 404) porque não há credenciais do dono; (b) migração com **duplicidade real** trabalhador+data derivada não é reproduzível no modelo antigo (dias_trabalho.data é UNIQUE), o teste M3 cobre a ambiguidade por data ilegível; (c) não há testes funcionais anteriores desta área para herdar — a suíte nova é a cobertura primária; (d) `hora_saida` segue órfã (só existia como NULL) — saída pertence a etapa futura; (e) os scripts legados de importação SQLite→PG (`migrar_sqlite_postgres*.py`, `scripts/migrar_sqlite_pg.py`) copiam a tabela pelo formato antigo e não servem para o schema novo — fora do escopo, documentado aqui.

## 5. Roteiro curto para conferir no DEV (chegada antes da abertura)

1. **Não** abra o dia de atendimento. Acesse `http://<dev>:9000/checkin-trabalhador` logado com perfil de recepção (`checkin.trabalhadores`).
2. A tela deve listar os trabalhadores ativos (sem bloqueio); busque pelo nome e clique **✔ Presente**.
3. Confira o banner "Chegada … registrada às HH:MM" e o chip "✔ Presente — HH:MM"; repita o clique (se possível por reload) e confirme "já estava presente hoje".
4. Em Cadastros → Trabalhadores → Presença (histórico) e em `/relatorios/presenca-trabalhador`, o registro aparece na data de hoje mesmo sem dia de atendimento; `/dia` continua mostrando o atendimento como não aberto.

## 6. Backup e recuperação

- **Backup pré-migração (recuperável, verificado por restore)**: `~/Documentos/backup-shamballa/shambala_pre_TASK001_2026-10-23.dump` (pg_dump -Fc do DEV; restore testado em banco scratch com contagens conferidas — o scratch foi removido em seguida).
- Recuperação: `pg_restore -h /var/run/postgresql -U shambala -d shambala --clean --if-exists --no-owner <arquivo>` (restaura o modelo antigo; ver limite na seção 3). Nenhum segredo incluído aqui; credenciais ficam em `.env`/`.env.test`.

## 7. Encerramento

- **Commit local**: `TASK_001` (branch `main`, sem push — publicação não foi solicitada; produção intocada).
- Convenção adotada nesta etapa: tarefas numeradas `TASK_NNN_<NOME>.md` em `docs/tasks/`, movidas para `docs/tasks/done/` ao concluir (renomeada a pedido: `TAREFA_PRESENCA_TRABALHADORES_ETAPA_1.md` → `TASK_001_…`).
- **Pendências para as próximas etapas** (fora do escopo desta, não iniciadas): terminal QR/código com o mesmo serviço, eventuais melhorias de gestão/correções/auditoria (etapa de gestão), saída/jornada se algum dia for desejada, revisão dos percentuais exibidos, e limpeza dos scripts legados de importação SQLite.
- Ferramental: `playwright` foi instalado apenas no `.venv` local para o smoke de navegador (não adicionado a `requirements.txt`).
