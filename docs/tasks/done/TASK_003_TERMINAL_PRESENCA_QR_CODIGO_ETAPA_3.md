# TASK_003 — Terminal de presença por QR e código — Etapa 3

Status: pronta para execução exclusivamente no DEV.
Dependências: TASK_001 (presença por data própria) e TASK_002 (códigos e carteirinhas), incluindo a correção da FK para RESTRICT. Conferir o estado real do código; correção informada nos commits locais a2f2e98 e 1e57f79.

## Objetivo

Criar o terminal fixo de presença para smartphone ou tablet na entrada da Casa Shambala. O voluntário apresenta o QR da carteirinha ou digita os mesmos quatro números e recebe confirmação de chegada. Controle simples, comunitário e baseado na confiança; sem punição, remuneração ou controle de jornada.

Executar esta etapa até disponibilizar o terminal no DEV existente, com testes e roteiro para homologação no aparelho. Não publicar em produção, fazer push, alterar infraestrutura pública ou iniciar a etapa de gestão.

## Preparação e ambiente

1. Ler AGENTS.md e normas aplicáveis. Registrar esta tarefa em docs/tasks/ com este número e nome, preservando alterações de outras tarefas.
2. Conferir schema, autenticação, permissões e serviços implementados nas TASK_001 e TASK_002. Não refazer regras já centralizadas.
3. Usar o DEV existente da porta 9000 e o endereço de acesso DEV já configurado. Não criar outra instância persistente, porta de homologação, túnel ou domínio. Processos e bancos descartáveis de testes devem existir somente durante a execução e ser encerrados ao final.
4. Levantar a possibilidade real de acesso à câmera no endereço DEV pelo aparelho. A câmera depende de contexto seguro e permissão do navegador: se o acesso atual não atender, informar o bloqueio e a configuração mínima necessária, sem desabilitar proteções do navegador, alterar produção ou criar rota pública por conta própria. Continuar a implementação e validação independente desse bloqueio.
5. Antes de migração ou escrita de preparação no DEV, fazer backup recuperável e registrar baseline pertinente. Não criar usuários temporários ou alterar senhas/permissões de pessoas existentes para contornar falta de credenciais.

## Acesso simples e exclusivo ao terminal

Uma pessoa autorizada habilita o modo terminal no aparelho; depois, voluntários não fazem login individual.

Implementar uma sessão própria e restrita de terminal, emitida a partir de ação autenticada de operador com checkin.trabalhadores:escrever. Reutilizar a infraestrutura de sessão do projeto quando possível, sem construir gestão complexa de aparelhos, pareamento, MFA ou PIN administrativo novo.

- A sessão do terminal permite somente abrir a tela e registrar chegada por código. Não herdar permissões de administrador ou acesso ao cadastro/financeiro.
- Após ativação, não deixar no navegador uma sessão privilegiada utilizável pelo público. Encerrar a sessão comum daquele navegador durante a transição, sem encerrar sessões de outros aparelhos, ou adotar mecanismo equivalente que efetivamente elimine esse acesso.
- A validade deve comportar um dia inteiro de operação (padrão de 24 horas desde a ativação, sem depender da abertura do atendimento). Expiração deve ser comunicada claramente; exigir nova ativação por operador.
- Disponibilizar encerramento da sessão do terminal, sem revelar menu geral. Retomar a administração exige autenticação normal.
- Verificar autorização em cada requisição. Sem sessão válida, não retornar nome, foto ou registrar presença. Não criar endpoint anônimo de consulta ou identificação por código.
- Aplicar os padrões existentes de cookie e proteção das escritas. Não colocar credenciais em URL ou localStorage. A solução deve permanecer proporcional ao uso comunitário.

Documentar as rotas e o funcionamento efetivo. Não abrir cadastro ou carteirinhas à recepção como consequência dessa implementação.

## Tela e funcionamento

Página própria, sem navegação geral, adaptada a tablet e smartphone em retrato e paisagem:

- Cabeçalho com estrela dourada e Shambala - Cidade de Luz, respeitando a identidade aprovada.
- Texto principal simples: Apresente sua carteirinha.
- Área de câmera com enquadramento e indicação clara de câmera pronta, iniciando ou indisponível.
- Botão Digitar meu código, teclado numérico grande, quatro posições visíveis, apagar, limpar, confirmar e voltar à câmera. Aceitar teclado físico também. Código é identificação, não senha secreta; não exigir mascaramento.
- Somente aceitar quatro caracteres ASCII numéricos. Preservar zeros iniciais, inclusive 0000. Não converter para inteiro nem completar entradas incompletas automaticamente.
- QR contém exatamente os mesmos quatro dígitos. Recusar conteúdo diferente, inclusive URL, sem abrir links ou executar ações externas.
- Uma leitura válida envia automaticamente o código ao servidor, sem exigir confirmação adicional do voluntário.
- Durante o envio, pausar novas submissões por câmera e teclado; impedir requisições sobrepostas e respostas antigas substituindo a identificação de outra pessoa.
- Após sucesso, mostrar nome, foto existente quando disponível e horário confirmado pelo servidor, com mensagem grande Presença registrada. A foto é opcional e não usa reconhecimento facial.
- Para repetição, mostrar Presença já registrada às HH:MM, preservando a primeira chegada. Se uma presença antiga não tiver horário, mostrar confirmação sem inventar um.
- Confirmação visual acompanhada de sinal sonoro breve quando permitido pelo navegador. Habilitação de câmera/áudio pode usar um botão inicial de toque. Falha ou bloqueio de áudio não impede operação visual.
- Após cerca de três segundos, limpar nome, foto, código digitado e mensagens, retornando à leitura. Pausar a leitura enquanto exibe o resultado.
- Evitar que uma carteirinha mantida diante da câmera gere confirmações/sons incessantes: rearmar o mesmo QR após retirada do enquadramento ou por controle equivalente, permitindo atendimento imediato de outra carteirinha.
- Código inválido/inexistente: Código não encontrado. Código retirado: Carteirinha substituída; procure a recepção. Trabalhador inativo: Cadastro inativo; procure a recepção. Nenhum desses casos grava presença.
- Não mostrar lista pública de trabalhadores, pesquisa por nome, documentos, situação financeira, histórico ou dados desnecessários.

Não exigir tela cheia para funcionar; pode oferecer botão de tela cheia. Não prometer bloquear navegação do sistema operacional: modo quiosque do aparelho é configuração externa.

## Câmera e leitor

Usar leitor QR no navegador com biblioteca incorporada e versão fixada no projeto, respeitando sua licença. Não depender exclusivamente de API nativa disponível em alguns navegadores. Não usar CDN, serviço externo, envio de vídeo ao servidor ou armazenamento de imagens da câmera.

Oferecer seleção de câmera, incluindo frontal e traseira, persistindo no máximo a preferência local necessária. O aparelho fixo deve permitir ao voluntário apresentar o cartão e enxergar a confirmação. Tratar câmera ausente, permissão negada, câmera ocupada e restrição de contexto seguro com mensagem compreensível e alternativa pelo teclado.

Encerrar os tracks da câmera ao sair; evitar múltiplos loops de decodificação após alternância, erro ou retorno à aba. Recuperar funcionamento ao voltar do segundo plano ou oferecer Reiniciar câmera sem recarregar toda a aplicação. Se disponível, solicitar manutenção da tela acesa; falha nesse recurso não bloqueia o terminal.

## Backend e integridade

Criar um único fluxo de chegada por código para QR e teclado, protegido pela sessão restrita do terminal:

1. Validar formato e sessão no servidor.
2. Resolver pelo servico_codigo_trabalhador.resolver_codigo().
3. Para código atual de trabalhador ativo, chamar servico_presenca.registrar_chegada().
4. Confirmar a transação antes de devolver sucesso.

Preservar as regras da TASK_001: data/hora do servidor em America/Sao_Paulo; uma presença por trabalhador/data; primeira chegada mantida; ausência explícita convertida corretamente; agenda e dia de atendimento não restringem chegada; nenhuma abertura automática de atendimento; sem saída ou falta automática.

Resolução do código e registro devem ocorrer na mesma transação com tratamento coerente de troca de código/inativação concorrentes. Usar bloqueios compatíveis com os serviços existentes, evitando condição em que código já retirado seja aceito após a troca confirmada. Não duplicar lógica SQL de presença nas rotas.

Guardar a origem da primeira chegada: terminal_qr ou terminal_codigo; recepcao para novos registros manuais e legado para fatos antigos cuja origem é desconhecida. Em ausência convertida, a origem passa a indicar a chegada efetiva. Repetição não altera origem, horário ou fatos existentes. Acrescentar o mínimo de schema necessário, com migração idempotente para banco novo/existente, sem presumir origem dos registros anteriores. Melhorias de relatório por origem ficam para a etapa de gestão.

Retornar somente nome, referência de foto necessária, data, horário e resultado. Não devolver CPF, telefone, mensalidade, fiado ou o cadastro completo. Não registrar códigos completos em logs de requisição/erro desnecessariamente.

## Falhas de rede e sessão

- Funcionamento conectado: não criar fila offline, presença provisória ou armazenamento local de códigos pendentes.
- Não mostrar sucesso antes de resposta válida do servidor com gravação confirmada.
- Timeout ou perda de resposta: informar Não foi possível confirmar. Tente novamente. A escrita pode ter ocorrido; repetição deve ser segura pela unicidade e devolver o primeiro horário.
- Mostrar estado de conexão de forma simples, sem requisições de polling excessivas. Ao recuperar, permitir nova tentativa sem reiniciar o aparelho.
- Se a sessão expirar ou o servidor devolver HTML de login em vez de JSON, apresentar Terminal precisa ser ativado novamente, sem tratar como presença registrada nem deixar carregamento infinito.
- Tratar falha 500 com mensagem clara, liberar a interface e preservar o teclado como alternativa quando a falha é só de câmera.

## Fora do escopo

- Alterar layout, códigos ou impressão das carteirinhas aprovadas.
- Reconhecimento facial, prova de identidade, antifraude, geolocalização ou punições.
- Controle de saída/jornada, cálculo de faltas pela agenda ou notificações.
- Funcionamento offline, app nativo ou instalação obrigatória de PWA.
- Nova tela de correções/auditoria ou reformulação dos relatórios (etapa 4).
- Reparar suíte financeira ou declarar validação de mensalidades/fiado pelo runner atual.
- Produção, novas instâncias persistentes e mudanças em túneis/domínios.

## Validação obrigatória

Usar banco PostgreSQL nativo descartável, sem Docker, conforme o padrão. Acrescentar testes ao runner sem mascarar ausência de ferramentas como sucesso.

| Cenário | Resultado esperado |
|---|---|
| Ativação por operador autorizado | Sessão restrita emitida; sessão comum do navegador não permanece acessível |
| Sem permissão, anônimo ou sessão expirada | Ativação/escrita recusadas; sem dados pessoais retornados |
| Terminal tenta cadastro, financeiro ou outras rotas | Acesso recusado |
| QR e teclado, código com zero inicial | Mesma pessoa, caracteres preservados |
| Código atual, retirado, inexistente, malformado e trabalhador inativo | Mensagens e escritas coerentes |
| Dia sem registro de atendimento, fechado ou fora da agenda | Presença aceita, atendimento não alterado |
| QR repetido, teclado repetido e concorrência com recepção | Uma presença, primeira chegada e origem preservadas |
| Falha após commit e antes da resposta | Repetição recupera presença sem duplicar |
| Ausência explícita seguida de chegada | Presente com horário/origem da chegada |
| Troca de código/inativação concorrente | Estado final coerente, sem aceitar código retirado após troca concluída |
| Migração aplicada duas vezes | Histórico preservado, origem antiga não inventada |
| Virada UTC/São Paulo | Data local correta |
| Câmera negada, ausente ou ocupada | Mensagem clara; digitação continua disponível |
| Alternar câmera, teclado e aba repetidamente | Sem tracks/loops duplicados nem interface travada |
| QR mantido no enquadramento e cartões consecutivos | Sem som/envio incessante; próxima pessoa atendida |
| Respostas lentas, desconexão, 500 e sessão expirada | Sem falso sucesso, mistura de pessoas ou carregamento infinito |

Verificar o navegador em dimensões reais de smartphone/tablet, com evidências fictícias da tela de leitura, digitação, sucesso, repetição e erro. Teste automatizado com vídeo/câmera simulada deve passar pelo leitor real escolhido; não basta injetar texto no callback. Incluir códigos com zeros iniciais e QR das carteirinhas aprovadas. Não tratar simulação como prova de aparelho físico.

Executar o runner completo e reportar resultados exatos. Conferir o serviço DEV existente e a proteção das rotas; não gerar presença em trabalhador real do DEV apenas para demonstração sem necessidade. Testes de escrita usam registros fictícios identificados e limpeza controlada, preservando os dados anteriores e códigos permanentes reais. Na falta de sessão autorizada no DEV, registrar a limitação e fornecer roteiro; não criar conta para contorná-la.

## Homologação no aparelho físico

Entregar URL real do terminal no acesso DEV existente e roteiro curto para o usuário:

1. Entrar como operador autorizado e ativar o terminal no aparelho.
2. Conceder câmera e selecionar frontal/traseira adequada.
3. Ler carteirinha impressa em escala 100%, incluindo uma com zero inicial.
4. Digitar código e repetir leitura para conferir primeiro horário.
5. Simular câmera negada e queda/retorno de conexão.
6. Conferir enquadramento, iluminação, distância, som e retorno automático na posição pretendida para a entrada.

Registrar modelo do aparelho, sistema, navegador e resultados somente quando realmente testados. Se não houver aparelho disponível ao executor, concluir implementação/testes e marcar homologação física como pendente, sem declarar aceite operacional completo ou inventar resultados. Não deixar processos extras ativos aguardando esse teste.

## Entrega

Atualizar SHAMBALA_CONTEXTO.md e anexar relatório a esta tarefa com:

- Arquivos, rotas, permissão de ativação e ciclo de vida da sessão restrita.
- Biblioteca QR, versão/licença e dependências registradas.
- Schema/origem, backup e evidência da preservação dos códigos e presenças anteriores.
- Testes do backend, do leitor real no navegador, runner completo e limitações.
- Endereço DEV e roteiro no aparelho; distinguir implementação concluída de homologação física pendente.
- Confirmação de uso exclusivo do serviço DEV existente e encerramento dos recursos descartáveis.
- Commit local apenas dos arquivos da tarefa, sem push; produção intocada.

Mover para docs/tasks/done/ quando implementação e testes estiverem concluídos, registrando explicitamente eventual homologação física pendente. Não iniciar a TASK_004 ou publicar em produção.

---

# Adendo — execução da TASK_003 (24/10/2026)

## 1. O que foi entregue

**Arquivos novos**

| Arquivo | Papel |
|---|---|
| `rotas/terminal.py` | Sessão restrita do terminal e as quatro rotas `/terminal/*` |
| `servico_chegada.py` | Fluxo único de chegada por código (QR e teclado pelo mesmo caminho) |
| `templates/terminal/leitura.html` | Tela do terminal, sem navegação geral |
| `static/css/terminal.css` | Layout retrato/paisagem, smartphone e tablet |
| `static/js/terminal.js` | Leitor, teclado, feedback e rede |
| `static/js/vendor/jsqr-1.4.0/` | jsQR 1.4.0 vendorizada (Apache-2.0, LICENSE junto) |
| `tests/test_terminal_presenca.py` | Suíte de backend/sessão (T1–T12 e variações) |
| `tests/test_terminal_leitor.py` | Suíte do leitor real no navegador (L1–L24) |

**Arquivos alterados**

| Arquivo | Mudança |
|---|---|
| `main.py` | registra `rotas.terminal` |
| `banco.py` | coluna `trabalhador_presenca.origem_chegada TEXT` na lista de colunas idempotente — sem DEFAULT e sem backfill; legado continua NULL (origem desconhecida, nunca presumida) |
| `servico_presenca.py` | `registrar_chegada(origem=…)` com `OrigemInvalida`; leitura do trabalhador passa a `SELECT … FOR UPDATE` (mesma trava de `trocar_codigo`), fechando a concorrência com a recepção; upsert preserva primeira chegada e sua origem |
| `servico_codigo_trabalhador.py` | expõe `travar_trabalhador()` para resolver+gravar na mesma transação sem duplicar SQL |
| `rotas/trabalhadores.py`, `templates/trabalhadores/checkin.html`, `static/css/base.css` | botão "Ativar terminal de presença neste aparelho" na tela do check-in, visível só a quem tem `checkin.trabalhadores:escrever` |
| `tests/rodar_testes.sh` | as duas suítes novas entram no runner |
| `SHAMBALA_CONTEXTO.md` | seção "Terminal de presença (/terminal)", origem de chegada e rotas |

Nenhum `requirements.txt` novo: jsQR é arquivo vendorizado (sem CDN, sem serviço
externo, sem dependência Python adicional). A câmera nunca sai do aparelho — ao
servidor vai apenas o código de 4 dígitos; nenhuma imagem é armazenada.

## 2. Rotas, permissão e ciclo de vida da sessão

- `POST /terminal/ativar` — operador autenticado com `checkin.trabalhadores:
  escrever` (mesma permissão do check-in da recepção). Emite cookie dedicado
  `sessao_terminal` (httpOnly, SameSite=Lax, 24 h desde a ativação) e encerra a
  sessão **desse navegador** — os demais aparelhos seguem administrando. Anônimo
  → 303 `/login`; sem a permissão → 403. Uma ativação por aparelho: a sessão de
  terminal anterior daquele navegador deixa de existir antes de emitir a nova.
- `GET /terminal/` — a tela, só com sessão de terminal; sem ela, 303 → `/login`
  (e a tela, aberta com sessão encerrada, mostra "procure a recepção").
- `POST /terminal/chegada` — JSON `{codigo, origem}`; 401 `{"resultado":
  "sessao_encerrada"}` sem sessão, com todos os dados pessoais em `null`; nenhum
  dado além de nome/foto/data/hora quando há sucesso.
- `POST /terminal/encerrar` — desliga; voltar a administrar exige login normal.
- Não existe endpoint público de busca por código.

## 3. Banco, backup e preservação

- Backup antes de qualquer mudança:
  `~/Documentos/backup-shamballa/shambala-DEV-pre-TASK003-2026-10-24.dump`.
- Migração é a única adição de coluna idempotente (`origem_chegada`); aplicada
  ao subir o serviço. Conflito antes×depois no DEV: **zero** —
  `presencas=15 · com_horario=15 · presentes=15 · codigos=70 · ativos=70 ·
  retirados=0 · trabalhadores=70` identicos nos dois levantamentos, e o runner
  confere o DEV antes/depois (`pessoas=664 checkins=2807` nos dois lados).
- Nenhum código foi gerado, apagado ou reordenado; nenhum check-in real de
  trabalhador do DEV foi criado para demonstração.

## 4. Testes — resultados exatos

Runner completo (`bash tests/rodar_testes.sh`), banco descartável nativo
`shambala_test_passes`, app de teste em porta própria, DEV intocado — **RC=0,
136/136**:

| Suíte | Resultado |
|---|---|
| test_tres_passes.py | 27/27 |
| test_plano_unico.py | 12/12 |
| test_permissoes.py | 8/8 |
| test_agenda_medium.py | 10/10 |
| test_presenca_trabalhadores.py (TASK_001) | 19/19 |
| test_codigo_trabalhador.py (TASK_002) | 18/18 |
| **test_terminal_presenca.py (TASK_003)** | **18/18** |
| **test_terminal_leitor.py (TASK_003)** | **24/24** |

A suíte do navegador usa Chromium headless com **vídeo simulado de verdade**
(`--use-fake-device-for-media-stream`): o QR é pintado quadro a quadro e
decodificado pelo jsQR real — nada é injetado no callback. Cobre QR com zero
inicial das carteirinhas aprovadas, carteirinha mantida no enquadro, cartões
consecutivos, repetição, conteúdo estranho (URL recusada, link não aberto),
código inexistente/retirado/inativo, teclado com zeros, concorrência de envio,
timeout de 12 s, HTML de login no lugar do JSON, 500, câmera negada/ausente/
ocupada, troca de câmera/aba, cinco dimensões reais de smartphone/tablet em
retrato e paisagem, e o endereço HTTP da LAN (L24: navegador barra a câmera,
teclado registra). Evidências em `~/Documentos/evidencias-task003-terminal/`
(20 PNGs: leitura nos 5 tamanhos, digitação, sucesso com e sem foto, repetição,
cada erro). Log do runner em `/tmp/runner-task003.log`.

**Correções de produto descobertas pelos testes** (não eram defeitos teóricos):

1. Concorrência recepção×terminal: sem trava, 7 chegadas simultâneas reportavam
   todas "registrada" (`criado=True`) — o `SELECT` de estado anterior passou a
   `FOR UPDATE`. U7 da TASK_001 voltou a passar.
2. Margem padrão do `body` vazava 16 px em 360×640 (`margin: 0` na tela).
3. Teclado em paisagem não cabia em 412 px de altura: distribuição em linhas
   laterais com botões de 44 px.
4. `sem_resposta` precisa marcar o código como exibido, senão a carteirinha
   segurada no enquadro reenvia sem fim.

**Limitações**: vídeo simulado não prova aparelho físico; Chromium é o motor
dos testes, não o navegador do tablet da entrada; o runner deixa o banco
descartável para a próxima execução — foi removido ao fechar a tarefa, e nenhum
processo de teste (9011/9013) ficou ativo.

## 5. DEV — disponibilização e roteiro no aparelho

O serviço **existente** da porta 9000 foi reiniciado sozinho (nenhum outro
processo; produção intocada). Terminal disponível em
**http://192.168.15.8:9000/terminal/** — smoke anônimo confere as proteções:
`/terminal/` → 303 → `/login`, `POST /terminal/chegada` → 401.

Neste endereço **HTTP** da LAN o navegador bloqueia a câmera (contexto inseguro —
comportamento verificado em teste, L24). A tela avisa e o terminal opera
**pelo teclado** normalmente; a câmera só libera com HTTPS neste endereço
(melhoramento de infraestrutura que a tarefa não alcança).

Roteiro de homologação no aparelho físico (pendente — não havia aparelho com o
executor; nada aqui declara aceite operacional):

1. No check-in (`/checkin-trabalhador`), tocar "Ativar terminal de presença
   neste aparelho" e confirmar; a administração desse navegador encerra.
2. Na tela do terminal, tocar "Digitar meu código" e registrar uma carteirinha.
3. Apresentar a mesma carteirinha outra vez: deve dizer "já registrada às
   HH:MM" mantendo o primeiro horário.
4. Repetir com uma carteirinha de código com zero inicial (as 70 impressas já
   sorteadas no DEV).
5. Cobrir/interromper a rede e tornar a ler: deve dizer "Não foi possível
   confirmar", nunca falso sucesso.
6. Em outro navegador, entrar como operador e encerrar o terminal: a tela deve
   parar e pedir nova ativação.
7. Com HTTPS disponível, repetir com a câmera (frontal/traseira, distância,
   iluminação, som e retorno automático de ~3 s).

Registrar modelo, sistema e navegador do aparelho usado apenas quando o teste
for realmente feito.

## 6. Entrega

Commit local apenas dos arquivos desta tarefa (novo + alterados listados acima,
incluindo `SHAMBALA_CONTEXTO.md` e as suítes), sem push; produção intocada.
Implementação e testes **concluídos**; homologação física no aparelho
**pendente** (seção 5). TASK_004 não iniciada.
