# TASK_002 — Códigos e carteirinhas dos trabalhadores — Etapa 2

Data: 23/10/2026
Status: concluída no DEV em 23/10/2026 — implementação em 03014b5, relatório abaixo. Layout da carteirinha revisado no mesmo dia em 7f06841 (adendo nº 8, ao final). Pendência de integridade da FK corrigida no mesmo dia em a2f2e98 (adendo nº 9).
Dependência: TASK_001, implementação informada no commit local 9e02aef e ajustes documentais em 75d6f50. Confirmar o estado real no repositório.

## Objetivo e limites

Implementar a identificação dos voluntários por código único de quatro números e a emissão de carteirinhas individuais e em lote com QR. O mesmo código será digitado no futuro terminal quando a pessoa esquecer a carteirinha.

Trata-se de controle comunitário baseado na confiança, sem remuneração ou punições. Não implementar senha de login, biometria, criptografia de credencial, antifraude ou autenticação individual complexa. O código é uma identificação simples, consultável pelos operadores autorizados.

Executar exclusivamente no DEV. Não acessar nem alterar produção, publicar, fazer push, modificar túneis ou reiniciar serviços de produção. Não iniciar o terminal de leitura: ele pertence à TASK_003.

## Preparação

1. Ler AGENTS.md e as normas do projeto. Conferir branch, alterações existentes e banco efetivamente configurado. Preservar trabalho alheio.
2. Registrar esta tarefa com este número em docs/tasks/. Não atribuir outro número automaticamente.
3. Conferir implementação atual do cadastro, permissões, schema/startup e serviço da TASK_001 antes de alterar.
4. Fazer backup recuperável do DEV antes da migração; validar o backup pelo procedimento do projeto e registrar evidências.
5. Preservar a limitação documentada da TASK_001: mensalidades/fiado não têm validação funcional aprovada. Não declarar as falhas financeiras pré-existentes como comprovadas sem reprodução no estado anterior. Não ampliar esta tarefa para reparar aquela suíte.

## Regras do código

- Exatamente quatro caracteres ASCII de 0 a 9, inclusive zeros iniciais: 0047 é diferente de 047 e deve permanecer 0047 em todo o fluxo.
- Universo permitido: 0000 a 9999. Gerar automaticamente entre os códigos disponíveis, sem usar CPF, telefone, nascimento ou o ID do cadastro como código.
- Unicidade garantida no banco, inclusive em operações concorrentes. Usar representação textual e validação de formato.
- Cada trabalhador ativo terá um código atual. Gerar para os ativos existentes em operação explícita de preparação, e automaticamente ao cadastrar ou reativar trabalhador sem código. Não regenerar códigos já atribuídos a cada startup, edição ou impressão.
- Trabalhador inativado mantém seu código reservado; o futuro fluxo de presença recusará inativos. Reativação preserva o código existente.
- Reimprimir mantém o código. Trocar o código é ação separada, confirmada, disponível a quem pode escrever no cadastro, com aviso de que a carteirinha anterior deixará de funcionar.
- Ao trocar, retirar o antigo de circulação e não atribuí-lo a outra pessoa nem voltar a atribuí-lo ao próprio trabalhador. Isso impede que uma carteirinha antiga identifique outra pessoa. Manter uma reserva simples dos códigos já emitidos; não construir infraestrutura complexa de credenciais.
- A troca deve ser atômica: se não for possível gerar o novo código, o atual continua válido. Se os 10.000 códigos se esgotarem, informar claramente sem loop infinito ou reutilização silenciosa.
- Edições de nome, foto, mensalidade e demais dados não alteram o código. Mudanças de código não afetam identidade interna, histórico de presença, mensalidade ou fiado.

## Modelo e serviço

Escolher o modelo mínimo para código atual e reserva dos códigos emitidos, documentando a decisão. Pode utilizar uma tabela simples vinculada ao trabalhador, com códigos anteriores inativos, ou outra solução equivalente. Não criar duplicidade de cadastro de pessoas.

A migração deve atender banco novo e existente, ser idempotente e preservar todos os dados. O startup pode preparar schema, mas não deve emitir/trocar códigos como efeito de GET ou impressão. A preparação inicial dos ativos deve ocorrer por ação explícita com permissão de escrita, idempotente e com resumo dos códigos gerados e dos já existentes. Um trabalhador sem código deve ter ação explícita de geração antes da impressão.

Centralizar geração, troca e resolução do código para uso futuro pela TASK_003. A resolução deve distinguir código atual ativo, inexistente/retirado e trabalhador inativo, sem registrar presença nesta etapa. Não expor endpoint público de busca por código nem novo acesso anônimo.

Registrar de forma simples quem gerou/trocou o código e quando, aproveitando o padrão existente ou os campos necessários do registro. Não introduzir uma plataforma geral de auditoria.

## QR e contrato para a próxima etapa

O conteúdo do QR será exatamente o código atual de quatro dígitos, como texto: exemplo 0047. Não incluir URL, nome, documentos, dados financeiros, ID interno ou token adicional. Assim, QR e teclado usam a mesma identificação, conforme a decisão do usuário.

Gerar QR localmente no servidor ou com biblioteca incorporada ao projeto. Não enviar dados a serviços externos nem depender de CDN para gerar/imprimir as carteirinhas. Acrescentar dependências necessárias ao arquivo de dependências do projeto.

Documentar este contrato para a TASK_003: o leitor deve preservar os quatro caracteres e resolver o código atual; somente depois chamar o serviço de presença da TASK_001. QR por si só não abre página nem registra presença.

## Interface e permissões

Acrescentar ao cadastro de trabalhadores:

- Código atual visível na área de identificação.
- Ação de gerar código quando ausente.
- Ação de imprimir carteirinha.
- Ação separada de trocar código, com confirmação.
- Seleção de trabalhadores ativos para impressão em lote; permitir selecionar todos os ativos de forma clara.
- Ação explícita de preparar códigos que faltam, com resumo do resultado.

Reutilizar cadastros.trabalhadores: leitura para consultar/imprimir; escrita para gerar/trocar/preparar códigos. Manter as guardas no servidor, inclusive nas rotas de QR e impressão. Não conceder acesso ao cadastro para a recepção ou ampliar permissões existentes. O terminal futuro terá sua própria superfície restrita.

GET de consulta, QR e impressão não altera banco. Operações de escrita seguem o padrão de POST e proteções de formulário já adotadas pelo projeto. Tratar trabalhador inexistente e seleção vazia/inválida com mensagens claras. Impressão de inativos deve ser bloqueada nesta versão, com orientação para reativação no cadastro se apropriado.

## Carteirinha e impressão

Entregar página HTML de impressão individual e em lote A4, com CSS de impressão e opção do navegador para imprimir/salvar PDF. PDF nativo no backend não é obrigatório nesta etapa.

Conteúdo da carteirinha:

- Identificação da Casa Shambala, respeitando nome e identidade visual já existentes no projeto.
- Identificação como trabalhador/voluntário.
- Nome completo legível, foto existente ou placeholder discreto.
- QR em preto sobre fundo branco, com margem livre adequada.
- Código em quatro dígitos, grande e legível.

Não incluir CPF, RG, telefone, mensalidade, saldo de fiado ou dados desnecessários. Não criar exigência de foto para emitir.

Usar formato horizontal aproximado de cartão, 85,6 × 54 mm, com medidas físicas no CSS. QR com cerca de 25 mm de lado ou maior, sem decoração sobre os módulos. Organizar cartões em A4 com margens para a impressora e guias discretas de corte. Não quebrar uma carteirinha entre páginas; ocultar menus/botões na impressão. Orientar escala de 100% quando necessário.

Tratar nomes longos e acentuados sem truncamento que impeça identificar a pessoa. Conferir várias páginas, último lote incompleto, foto ausente e foto com proporção diferente. A impressão não pode trocar nome, foto e código entre cartões.

## Critérios de aceite e testes

Testar com PostgreSQL nativo descartável, sem Docker; usar dados fictícios nas evidências visuais compartilháveis.

| Cenário | Resultado esperado |
|---|---|
| Migração e segunda execução do startup | Schema compatível; dados e códigos existentes preservados |
| Preparação inicial executada duas vezes | Só gera códigos ausentes; não altera os já emitidos |
| Cadastro de ativo / reativação sem código | Código criado de modo transacional |
| Edição, reimpressão e reativação com código | Código preservado |
| Código 0047 | Banco, interface, QR e resolução preservam o zero inicial |
| Colisão e emissões concorrentes | Sem código duplicado ou duas credenciais atuais por pessoa |
| Troca de código | Novo resolve para a mesma pessoa; antigo deixa de resolver como atual |
| Código retirado e código de inativo | Não são atribuídos a outra pessoa |
| Falha ou esgotamento na troca | Mensagem clara; código atual permanece válido |
| Esgotamento do espaço | Falha controlada, sem loop infinito |
| Consulta e impressão repetidas | Nenhuma gravação ou geração implícita |
| Usuário sem escrita / sem leitura | Mutação / consulta negadas conforme a permissão |
| Carteirinha individual e lote | Nome, foto, código e QR correspondem ao mesmo trabalhador |
| Decodificação do QR gerado | Retorna exatamente os quatro dígitos impressos |
| A4 com nomes longos e várias páginas | Sem cortes, sobreposição ou cartão dividido |
| Serviço de presença da TASK_001 | Regressão pertinente permanece aprovada |

Simular esgotamento e colisões no banco de teste ou por mecanismo controlado, sem poluir o DEV. Verificar impressão no navegador, inclusive o resultado em PDF e a decodificação de QR dessa saída, não apenas do arquivo QR original. Não afirmar leitura por câmera de aparelho físico sem realizá-la; essa validação poderá ficar pendente para a TASK_003.

Executar o runner existente e informar resultados exatos, sem chamar a passagem dele de validação financeira. Inspecionar o diff do cadastro para verificar que a geração de códigos não altera a sincronização das mensalidades, os campos financeiros ou os dados do fiado.

## Entrega e encerramento

Entregar implementação, migração, testes e relatório anexado a esta tarefa. Informar:

1. Modelo escolhido e regras efetivas de geração/reserva/troca.
2. Rotas, permissões e dependências acrescentadas.
3. Resultado da preparação dos trabalhadores ativos no DEV, sem publicar a lista de códigos no relatório.
4. Testes executados, evidência da impressão A4 e da decodificação dos QR impressos em PDF.
5. Roteiro curto para gerar, imprimir, reimprimir e trocar código no DEV.
6. Backup, limitações, pendências e contrato para o terminal.
7. Commit local, sem push; produção intocada.

Atualizar o status para concluída somente depois dos critérios atendidos; mover para docs/tasks/done/ conforme o padrão. Preservar arquivos de outras tarefas. Não iniciar a TASK_003 nem publicar em produção.

---

# Relatório de execução — TASK_002

Concluída em 23/10/2026, no DEV. Produção não foi acessada, nada foi
publicado e não houve push.

## 1. Modelo escolhido e regras efetivas

**Uma única tabela como fonte do código** — `trabalhador_codigo`, uma linha por
código *já emitido*:

```sql
CREATE TABLE IF NOT EXISTS trabalhador_codigo (
    codigo          TEXT    PRIMARY KEY CHECK (codigo ~ '^[0-9]{4}$'),
    trabalhador_id  INTEGER NOT NULL REFERENCES trabalhadores(id) ON DELETE CASCADE,
    estado          TEXT    NOT NULL DEFAULT 'ativo' CHECK (estado IN ('ativo','retirado')),
    atendente_id    INTEGER REFERENCES atendentes(id),
    origem          TEXT    NOT NULL DEFAULT 'tela',
    criado_em       TEXT    NOT NULL,
    retirado_em     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_trabalhador_codigo_atual
    ON trabalhador_codigo (trabalhador_id) WHERE estado = 'ativo';
CREATE INDEX IF NOT EXISTS ix_trabalhador_codigo_trab ON trabalhador_codigo (trabalhador_id);
```

Decisões e porquês:

- **Não** foi adicionada coluna `codigo` em `trabalhadores`: isso criaria segunda
  fonte de verdade e divergência entre "atual" e "emitidos". A linha `ativo` *é*
  o código atual; as linhas `retirado` são a reserva permanente.
- **`TEXT` + regex** em vez de `CHAR(4)`: `CHAR(4)` é `bpchar` (blank-padding e
  `=` ignorando espaços à direita — armadilha silenciosa na resolução e no QR
  vindos de form ou banco). `LIKE '[0-9]…'` não existe em PostgreSQL (é T-SQL);
  usa-se `~ '^[0-9]{4}$'`.
- **Índice único parcial** = exatamente um código ativo por trabalhador, imposto
  pelo banco, e ainda assim permitindo o histórico de retirados.
- **`atendente_id` é NULLABLE** (padrão do projeto). O modelo não exige usuário
  existente, então a preparação por script local grava `atendente_id = NULL` +
  `origem = 'preparacao_local'` — ator técnico explícito, sem inventar usuário
  humano. Quem age pela tela fica registrado com o próprio atendente.
- **`criado_em`/`retirado_em`** gravados pelo serviço como texto
  `AAAA-MM-DD HH:MM` no fuso America/Sao_Paulo calculado pelo servidor
  (`to_char(NOW() AT TIME ZONE …)`) — mesmo padrão de `servico_presenca`, nunca
  o relógio do sistema do app.
- Nome `trabalhador_codigo` não colide com `checkins.codigo_passe` (códigos de
  passe avulsos, por dia — coisas distintas). Nenhuma tabela espelho em `pessoas`.

Regras efetivas, todas em `servico_codigo_trabalhador.py` (funções de módulo
recebendo `conn`, dentro da transação do chamador):

| Regra | Como ficou |
|---|---|
| Emissão | sorteio controlado: `INSERT … SELECT to_char(n,'FM0000') FROM generate_series(0,9999) … WHERE NOT EXISTS (…) ORDER BY random() LIMIT 1 ON CONFLICT (codigo) DO NOTHING RETURNING codigo`, até 8 tentativas; sem código livre → `CodigosEsgotados`. `ON CONFLICT DO NOTHING` não aborta a transação, então a tentativa seguinte já enxerga a linha do concorrente (READ COMMITTED) — sem `SAVEPOINT` e sem transação nova |
| Concorrência na mesma pessoa | `SELECT … FOR UPDATE` na linha do trabalhador em todos os caminhos de escrita: dois cliques simultâneos serializam e o segundo **preserva** o código do primeiro (um registro, uma criação). Na preparação em lote, `FOR UPDATE OF t` |
| Reserva permanente | o anti-join do sorteio cobre **todos** os estados: um código `retirado` nunca volta a ser atribuído, nem ao mesmo trabalhador |
| Troca | atômica e na ordem retirar→inserir (inserir antes violaria o índice único parcial); qualquer exceção faz `conectar()` reverter e o código atual continua válido |
| Idempotência | `garantir_codigo` preserva o código atual; `preparar_codigos_pendentes` é transação única e, na segunda execução, gera zero |
| Resolução | `resolver_codigo` é somente leitura e distingue `invalido` (formato), `ativo`, `inativo`, `retirado`, `inexistente`; nunca registra presença |
| Registro simples | `atendente_id` + `origem` + `criado_em`/`retirado_em` no próprio registro — sem plataforma de auditoria |

Onde o código nasce: `salvar_novo` chama `garantir_codigo` **na mesma transação**
do INSERT (falha na emissão desfaz o cadastro, com mensagem própria de
esgotamento); `toggle_ativo` emite ao **reativar** quem ficou sem código e **nunca
toca** no código ao inativar (ele fica reservado com a pessoa);
`salvar_editar` não toca em código. Startup (`_migrar`) é DDL puro: nunca emite
código, mesmo havendo ativos sem código — validado em K1 e comprovado no DEV.

## 2. Rotas, permissões e dependências

Rotas novas em `rotas/trabalhadores.py`, todas sob a guarda existente do módulo
`cadastros.trabalhadores` (leitura para consultar/imprimir, escrita para
gerar/trocar/preparar):

| Rota | Permissão | Efeito |
|---|---|---|
| `POST /cadastros/trabalhadores/preparar-codigos` | escrever | emite os que faltam, devolve resumo (gerados × já existiam) |
| `POST /cadastros/trabalhadores/{id}/codigo/gerar` | escrever | `garantir_codigo` |
| `POST /cadastros/trabalhadores/{id}/codigo/trocar` (`confirmar=1`) | escrever | troca atômica; sem o `confirmar` recusa com aviso |
| `GET /cadastros/trabalhadores/{id}/carteirinha` | ler | carteirinha individual; inativo ou sem código → volta à lista com orientação |
| `GET /cadastros/trabalhadores/carteirinhas?ids=…` ou `?todos=ativos` | ler | lote A4; itens bloqueados saem do lote com aviso nominal e nunca são impressos |

`listar` e `form_editar` passaram a trazer o código atual por `LEFT JOIN` na
linha `estado='ativo'`. GET de consulta, QR e impressão não escrevem nada
(provado em K11 por snapshot `count + max(criado_em)`). Não existe endpoint
público de busca por código nem acesso anônimo: as rotas novas respondem
`303 → /login` quando não autenticadas (validado no DEV por smoke anônimo).

Interface: badge do código atual (ou "sem código") em cada cartão da lista,
botão "Preparar códigos que faltam (N)" no cabeçalho, seleção por checkboxes com
"selecionar todos os ativos" e botão de impressão do lote (form `method=get`,
funciona sem JS), bloco "Código da carteirinha" somente leitura no formulário e
formulário separado de troca com checkbox de confirmação e o aviso de que a
carteirinha anterior sai de circulação. Carteirinha e lote são páginas
standalone (`_cartao.html`, `carteirinha.html`, `carteirinhas_lote.html` +
`static/css/carteirinha.css`), sem dependência de `base.html`.

**Dependências acrescentadas: nenhuma.** `qrcode[pil]>=7.4.2` já estava em
`requirements.txt:8`; o QR é gerado localmente como PNG preto/branco em
`data:` URI (`version=1, box_size=10, border=4` — margem livre de 4 módulos),
sem serviço externo e sem CDN. Para *verificar* a leitura dos QRs, a suíte usa
ferramental que continua fora de `requirements.txt` (como `opencv` e
`playwright`): `pyzbar` (leitor de referência, lib `libzbar0` já presente no
sistema) com `cv2.QRCodeDetector` como fallback.

Grade física (decisão do dono, validada em PDF): A4 retrato, `@page` com 10 mm
de margem, 2 colunas × 5 linhas de 85,6 × 54 mm, 8 mm entre colunas e 1 mm entre
linhas → 179,2 mm de largura nos 190 úteis e 274 mm de altura nos 277 úteis,
10 cartões por folha, guias de corte dentro da caixa do cartão, `break-before`
entre folhas, escala 100% orientada na barra de tela, nome em escada
12 → 10,5 → 9 pt com quebra de linha (nunca ellipsis).

## 3. Preparação dos trabalhadores ativos no DEV

Somente quantidades — a lista de códigos não é publicada aqui nem em lugar
nenhum do relatório.

| Momento | Resultado |
|---|---|
| Antes (baseline, somente leitura) | 70 trabalhadores, 70 ativos, 0 inativos, 15 registros de presença, `trabalhador_codigo` **não existia** |
| Após reinício do serviço | tabela criada com os 3 índices e os 2 checks; **0 linhas** — o startup não emitiu nada |
| 1ª execução (`--aplicar`) | **70 gerados**, 0 já existiam |
| 2ª execução (`--aplicar`) | **0 gerados**, 70 preservados — idempotência comprovada |
| Conferência final | 70 linhas, 70 códigos distintos, 70 `ativo`, 0 `retirado`, 0 fora do formato `^[0-9]{4}$`, 70 trabalhadores distintos (nenhum com dois códigos ativos), 11 códigos com zero inicial, `origem='preparacao_local'` e `atendente_id IS NULL` em todos, `criado_em` único formato (16 caracteres, hora de São Paulo) |
| Tabelas alheias | `pessoas` 664, `checkins` 2807, `trabalhador_presenca` 15, `planos_tratamento` 1855, `agendamentos` 2868, `grupos_permissoes` 25 — todas batendo exatamente com o baseline |

A preparação foi feita pelo **serviço** (`preparar_codigos_pendentes`), via
`preparar_codigos_trabalhadores.py` — script local de uma entrada, simulação por
padrão e escrita só com `--aplicar`, recusando execução sob
`SHAMBALA_MODO_TESTE=1` e imprimindo apenas quantidades. O serviço DEV da porta
9000 foi reiniciado (e somente ele) para aplicar o schema; o processo atual
atende e o smoke anônimo das rotas novas devolve `303 → /login`.

**Segunda subida do serviço DEV** (prova de que o `_migrar` é idempotente contra
o schema já existente, e de que o startup não emite código): reinício extra com
log limpo — `Application startup complete`, nenhum `error`/`traceback`/`warning`,
HTTP 200 em `/login` — e, depois, as mesmas 70 linhas na `trabalhador_codigo`
(70 `ativo`, 70 distintas, 70 com `origem='preparacao_local'`), **0** com
`criado_em` posterior à preparação, com `pessoas` 664, `checkins` 2807 e
`trabalhador_presenca` 15 inalterados.

## 4. Testes, evidência de impressão e decodificação

Runner completo (`bash tests/rodar_testes.sh`), resultado exato:

```
27/27 testes passaram                       (test_tres_passes.py)
12/12 testes passaram (plano único)         (test_plano_unico.py)
 8/8  testes passaram (permissões)          (test_permissoes.py)
10/10 testes passaram (agenda do médium)    (test_agenda_medium.py)
19/19 testes passaram (presença TASK_001)   (test_presenca_trabalhadores.py)
16/16 testes passaram (TASK_002 códigos)    (test_codigo_trabalhador.py)
DEV inalterado: ok   (antes = depois = pessoas=664 checkins=2807)
```

92 testes no total, zero falhas, zero pulados. A suíte nova
`tests/test_codigo_trabalhador.py` cobre, com PostgreSQL nativo descartável
(`shambala_test_passes` + um banco dedicado `shambala_codigo_mig_test` para
esgotamento) e app na porta 9011:

| ID | O que fecha |
|---|---|
| K1 | startup cria o schema e **não emite** código nenhum, mesmo com ativos |
| K2 | preparar duas vezes: gera N, depois zero, nada muda nos já emitidos (também pelo HTTP) |
| K3 | cadastro de ativo e reativação ganham código transacional; inativação reserva; **nenhuma mensalidade criada pelo código** |
| K4 | edição, reimpressão, novo "gerar" e ciclo ativo→inativo→ativo preservam exatamente o mesmo código |
| K5 | `0047`: zeros iniciais sobrevivem ao banco, à tela, ao QR e à resolução; `'47'`, `'0047 '`, `47` e `None` são recusados como inválidos |
| K6 / K6b / K6c | 8 emissões concorrentes em pessoas diferentes (threads, conexões separadas) dão códigos distintos; 4 cliques na mesma pessoa dão **um** código e **uma** criação; 200 emissões em lote sem repetição |
| K7 | troca exige confirmação, é atômica, o novo resolve e o antigo fica `retirado` com `retirado_em`; trocar sem código dá erro claro |
| K8/K9/K10 | banco dedicado com as 10.000 linhas quase cheias: `CodigosEsgotados` sem loop, troca que falha deixa o atual válido (nenhuma linha meia-retirada), código retirado nunca é reofertado |
| K11 | GETs repetidos de carteirinha, lote e lista não escrevem nada |
| K12 | RBAC: somente-leitura não escreve, sem módulo é 403, a recepção continua sem acesso ao cadastro (mas mantém `/checkin-trabalhador`), anônimo não entra |
| K13 | cada cartão traz nome/foto/código/QR da **mesma** pessoa; nenhum CPF, RG, telefone, email, mensalidade ou fiado; nenhuma URL externa |
| K14 | QR da carteirinha individual e do lote decodifica exatamente os 4 dígitos |
| K15 | A4: 12 cartões → 2 folhas; seleção mista imprime só os aptos e **nomeia** os bloqueados; um cartão → uma folha; só bloqueados → recusa com "Nada para imprimir:" |
| K16 | **saída impressa**: 70 carteirinhas fictícias → `chromium --print-to-pdf` → 7 páginas A4 (594,96 × 841,92 pt), `pdftoppm -r 300`, leitura célula a célula pela geometria física do CSS → 70/70 QRs decodificados, cada um na célula do seu cartão, `[10] × 7` |
| K17 | regressão da TASK_001: a suíte de presença segue verde dentro do runner (19/19) |

Evidência preservada fora do repositório (dados fictícios, nada do DEV):
`~/Documentos/evidencias-task002/` — `carteirinhas.pdf` (7 folhas),
`carteirinhas.html`, `folha-1..7.png` (raster de 300 dpi),
`runner-completo.log`, `dev-baseline.txt` e `README.txt`.

Achejado relevante da verificação: medindo os dois leitores em QRs gerados pelo
próprio serviço (amostra aleatória de 400 códigos), o `cv2.QRCodeDetector` errou
2 dos 400 na leitura de um QR por imagem e 5 dos 400 quando a página inteira é
lida de uma vez, enquanto o zbar decodificou **600/600** numa amostra própria.
O defeito é do leitor leve, não do cartão — por isso a suíte lê com zbar e usa
OpenCV como fallback, e por isso a leitura da folha é feita célula a célula. Um
cartão que não abrisse em nenhum dos dois seria falha real; essa condição nunca
ocorreu.

## 5. Roteiro no DEV (para validar na tela com credenciais)

1. `/cadastros/trabalhadores` → cada cartão mostra o código atual (após a
   preparação, todos os 70 ativos têm). O botão "Preparar códigos que faltam" só
   aparece quando `N > 0` e é idempotente.
2. Abrir um trabalhador → bloco "🔢 Código da carteirinha" com o código somente
   leitura e "🖨️ Imprimir carteirinha" (abre a página do cartão).
3. Na impressão do navegador: papel A4, margens "padrão" ou "nenhuma",
   **escala 100%** (desligar "ajustar"), sem cabeçalho/rodapé.
4. Voltar à lista → marcar os cartões desejados (ou "Selecionar todos os
   ativos") → "🖨️ Imprimir carteirinhas selecionadas" → lote em A4, 10 por
   folha, com guias de corte; quem estiver inativo ou sem código fica fora e é
   nominalmente avisado.
5. Trocar código → formulário separado, exige marcar a confirmação e avisa que a
   carteirinha anterior sai de circulação e não volta.
6. Inativar e reativar → o código é preservado; reimprimir nunca regenera.

## 6. Backup, limitações, pendências e contrato para a TASK_003

**Backup.** `pg_dump -Fc` do DEV em
`~/Documentos/backup-shamballa/shambala-DEV-pre-TASK002-2026-10-23.dump`
(sha256 `0b600c3575e571f75aa9ccac8b10a0678b4003bc4cd9ff0a3627499c8e602271`),
**validado por restauração** em banco descartável
(`shambala_verify_restore_test`, removido em seguida): `pg_restore` sem erros e
contagens idênticas à origem em `pessoas` 664, `checkins` 2807, `trabalhadores`
70, `trabalhador_presenca` 15, `planos_tratamento` 1855, `agendamentos` 2868,
`dias_trabalho` 71, `grupos_permissoes` 25 e as 41 tabelas do schema. O arquivo
de backup não contém segredos além das próprias linhas do banco.

**Limitações (honestas).**
- O **clique na tela do DEV não foi validado**: não há credenciais de operador
  do DEV nesta máquina. O que está validado é o fluxo HTTP completo com
  permissões no banco descartável (usuários de teste com grupos reais) e, no DEV
  apenas, o roteamento com recusa anônima (`303 → /login`). A preparação real dos
  70 códigos veio do **serviço**, por script local.
- **Mensalidades e fiado continuam sem validação funcional aprovada** —
  limitação herdada da TASK_001, não ampliada aqui. A inspeção do diff confirma
  que nada nesta tarefa toca `sincronizar_mensalidades`, campos financeiros ou
  fiado (a única ocorrência desses termos no diff é um comentário dizendo que o
  código não os altera), e K3 verifica que gerar código não cria mensalidade.
- A passagem do runner **não é** validação financeira: ela prova as suítes
  contratuais e que o DEV não mudou.
- `opencv`/`pyzbar` são ferramental de verificação e continuam fora de
  `requirements.txt`; em máquina sem esses leitores os testes de decodificação
  são **pulados** (aparecem como PULADO no resultado, não como aprovados).

**Pendências para a TASK_003.** Leitura por câmera de aparelho físico (não foi
realizada aqui), terminal de digitação dos 4 dígitos e presença por código.

**Contrato do QR** (também registrado em `SHAMBALA_CONTEXTO.md`): o QR codifica
exatamente a string de 4 caracteres do código atual — sem URL, nome, documento,
dado financeiro, id interno ou token. O leitor deve preservar os quatro
caracteres (zeros incluídos) e chamar `servico_codigo_trabalhador.resolver_codigo(conn, codigo)`,
que devolve `estado` ∈ `invalido|ativo|inativo|retirado|inexistente` e, quando
`ativo`, `trabalhador_id` e `nome_completo`. Resolver **não** registra presença:
só depois se chama `servico_presenca.registrar_chegada()`. QR por si só não abre
página nem registra presença, e não deve ser criado endpoint público de busca
por código.

## 7. Arquivos alterados e commit

| Arquivo | Mudança |
|---|---|
| `banco.py` | +32 — tabela `trabalhador_codigo`, índice único parcial e índice por trabalhador, em `_migrar` (DDL puro) |
| `servico_codigo_trabalhador.py` | novo — emitir/garantir/trocar/preparar/resolver/QR + exceções |
| `rotas/trabalhadores.py` | +260 — geração no cadastro e na reativação, código na lista e no formulário, rotas de gerar/trocar/preparar/carteirinha/lote |
| `templates/trabalhadores/lista.html`, `form.html` | +71 / +50 — badge do código, preparação, seleção de lote, bloco de código e troca confirmada |
| `templates/trabalhadores/_cartao.html`, `carteirinha.html`, `carteirinhas_lote.html`, `static/css/carteirinha.css` | novos — cartão e páginas de impressão A4 |
| `static/css/base.css` | +111 — estilos da lista/formulário |
| `preparar_codigos_trabalhadores.py` | novo — operação local idempotente (simulação / `--aplicar`) |
| `tests/test_codigo_trabalhador.py`, `tests/rodar_testes.sh` | suíte K1–K17 na lista do runner |
| `SHAMBALA_CONTEXTO.md` | tabela, rotas reais do módulo, arquivos novos e contrato do QR |

Divergências em relação ao plano aprovado, ambas por simplificação: as funções
`gerar_codigo` e a exceção `CarteirinhaBloqueada` previstas no plano não
existem — `garantir_codigo` cobre o caso (é idempotente pelo ponto de vista do
chamador) e o bloqueio de impressão de inativo/sem código é decidido nas rotas,
com mensagem nominal, sem exceção extra. Em compensação, foi acrescentado
`SELECT … FOR UPDATE` na linha do trabalhador em todos os caminhos de escrita,
que o plano não previa expressamente e que fecha o duplo-clique na mesma pessoa.

Commit local (sem push, produção intocada): **03014b5** —
`feat: códigos de 4 dígitos e carteirinhas com QR dos trabalhadores (TASK_002)`,
com os 14 arquivos acima. Nenhum arquivo de outra tarefa foi staged — os
arquivos não relacionados que já estavam no working tree (`.qoder/`,
`.reasonix/`, `CONTINUAR.md`, `PLANO_*.md`, `RELATORIO_*.md/pdf`,
`consolidar_planos.py`, `escola/`, `reasonix.toml`, `static/fotos/nota_*.jpg`)
seguem intactos. Este arquivo, com o relatório e o status atualizados, foi para
`docs/tasks/done/` no commit de documentação `5b0a11a`, mais um acompanhamento
registrando a segunda subida do serviço DEV.

---

# Adendo — revisão do layout da carteirinha (23/10/2026)

## 8. O que mudou no cartão, por quê e como foi validado

### 8.1 Pedido e decisões

O dono pediu, depois de ver as primeiras carteirinhas impressas: fora o rótulo
"Trabalhador · Voluntário" e o círculo com a inicial; nenhum espaço reservado
para foto; no alto a estrela dourada e "Shambala – Cidade de Luz"; QR com
prioridade visual, preto sobre branco e com a margem livre necessária; no rodapé
o nome completo e o código de quatro dígitos, com os zeros iniciais visíveis.
Cartão e grade não mudam: 85,6 × 54 mm, dez por folha A4, sem corte nem
sobreposição.

Duas rodadas:

1. **Primeira versão** — três faixas, com o QR a 27,5 mm. Ao medir, ficou
   evidente que o rodapé em faixa cheia de 12 mm não comportava os 50% de área
   útil pedidos inicialmente: as faixas cheias só deixam 27,5 mm de altura para
   o QR (19,4% dos 3.900,4 mm² úteis). Isso foi apresentado com a aritmética, e
   o arranjo de faixas foi aprovado sabendo disso.
2. **Ajuste aprovado** — "diminuir o nome do trabalhador e o código numérico no
   rodapé e usar o espaço liberado para aumentar o QR; reduzir a altura do
   rodapé e os espaçamentos". Aplicado: rodapé 12 → 10,5 mm e folgas
   1,5 → 1,0 mm, devolvendo os 3 mm ao QR, que foi de 27,5 para **30,5 mm de
   lado** (23,8% da área útil). O cabeçalho passou de 6,5 para 6,0 mm.

Decisões registradas: o nome da Casa no cartão é **texto fixo** — não se lê
`centro_nome`, que no DEV vale `--- Shambala --- Cidade de Luz` e continuará
alimentando as outras telas; nenhuma linha de `configuracoes_centro` foi
alterada. A estrela vem de **`centro_logo`** quando configurado e, se não
houver logo, cai em `static/logos/estrela_dourada.svg` (asset novo, paleta
amostrada do logo real: `#E8B22F` com contorno `#4B3D19`) — nunca mais no
`static/logo.svg` azul que apareceu na primeira prévia.

### 8.2 Geometria final

```
cartão 85,6 × 54 mm · padding 2,5 mm vertical / 3 mm horizontal
área útil 79,6 × 49,0 mm = 3.900,4 mm²

  topo      6,0 mm   (estrela 5,2 mm + "Shambala – Cidade de Luz" 8,5 pt)
  folga     1,0 mm
  QR       30,5 mm   (30,5² = 930 mm² = 23,8% da área útil)
  folga     1,0 mm
  rodapé   10,5 mm   (nome 9,5/8,5/7,5 pt + código 12 pt)
  ─────────
  total    49,0 mm   ← fecha exatamente com a altura útil
```

Rodapé: nome em 9,5 pt, 8,5 pt acima de 24 caracteres e 7,5 pt acima de 40, com
quebra de linha — nunca ellipsis; código em 12 pt, peso 800,
`font-variant-numeric: tabular-nums` e `letter-spacing: 1,4 mm`. Conteúdo e
geração do QR não mudaram: os mesmos quatro caracteres, `version=1`,
`border=4`, gerado localmente.

Medidas tiradas **do PDF impresso** (cartão ampliado, 600 dpi):

| Grandeza | Valor |
|---|---|
| Símbolo preto do QR | 21,99 × 21,99 mm (21 módulos de 1,047 mm) |
| Zona de silêncio dentro da imagem | 4,21 mm por lado (4 módulos, como especificado) |
| Branco livre do símbolo até o cabeçalho | 5,2 mm |
| Branco livre do símbolo até o nome | 6,3 mm |
| Branco livre à esquerda / direita do símbolo | 31,6 mm / 32,0 mm |
| Altura dos dígitos do código | 3,04 mm, última linha a 3,0 mm da borda |

Nada sobrepõe os módulos, e a margem livre exigida para leitura está preservada
com folga mesmo depois de o QR ter crescido.

### 8.3 Arquivos

| Arquivo | Mudança |
|---|---|
| `templates/trabalhadores/_cartao.html` | macro reescrita em três faixas (`cc-topo`, `cc-qr-faixa`, `cc-rodape`); saem `.cc-papel` e `.cc-foto`; nome da Casa fixo; reserva dourada no `src` do logo |
| `static/css/carteirinha.css` | alturas exatas das faixas acima; regras de foto removidas; escada do nome recalibrada para a faixa cheia; código 12 pt |
| `static/logos/estrela_dourada.svg` | novo — reserva dourada para quando não há logo configurado |
| `rotas/trabalhadores.py` | `_montar_cartao` deixa de calcular `foto_url`; `_foto_url`, sem outro uso, removida; `foto_trabalhador` saiu do `SELECT` das carteirinhas. A rota `foto-placeholder/{inicial}` fica — ainda serve a lista |

Nada de banco, serviço de códigos, rotas ou permissões mudou; nenhuma escrita
no DEV foi necessária para o layout.

### 8.4 Testes

- **K13** — passou a exigir o rótulo **ausente**, o nome fixo da Casa, a
  estrela, exatamente **2 imagens** no cartão (estrela e QR) e que não sobre
  nenhuma referência a foto (`/static/fotos/`, `foto-placeholder`), mantendo as
  vedações de CPF/RG/telefone/e-mail/mensalidade/fiado e a ausência de URL
  externa.
- **K15** — além da grade A4, lê as **alturas das faixas do próprio CSS** e
  exige que fechem 49,0 mm, que o QR tenha o lado da faixa e que seja a faixa
  dominante (≥ 29 mm e maior que topo e rodapé), e que não tenha sobrado regra
  `.cc-foto`.
- **K16** — inalterado no enunciado: 70 carteirinhas fictícias → 7 folhas A4 →
  raster 300 dpi → leitura **célula a célula** pela geometria física, com zbar
  como leitor de referência e OpenCV como fallback. É o que prova que nenhum QR
  vazou da sua célula.

Runner completo no ambiente descartável (`bash tests/rodar_testes.sh`, log em
`/tmp/runner_layout_final.log`), **exit 0**:

| Suíte | Resultado |
|---|---|
| `test_tres_passes.py` | 27/27 |
| `test_plano_unico.py` | 12/12 |
| `test_permissoes.py` | 8/8 |
| `test_agenda_medium.py` | 10/10 |
| `test_presenca_trabalhadores.py` (TASK_001) | 19/19 |
| `test_codigo_trabalhador.py` (TASK_002) | 16/16 |
| **Total** | **92/92 — 0 falhas, 0 pulados** |

Conferência de isolamento: `pessoas=664 checkins=2807` antes e depois —
**DEV inalterado**. A limitação da TASK_001 sobre mensalidades/fiado permanece
registrada no corpo deste relatório e não foi ampliada aqui.

Evidência visual (nomes fictícios, identidade real da Casa reproduzida só no
banco de teste): `~/Documentos/evidencias-task002-layout/` — `cartao_ampliado.png`
(nome de 62 caracteres, duas linhas), `cartao_nome_medio.png`,
`cartao_sem_logo.png` (a reserva dourada), `folha_completa.png`/`.pdf` e
`README.txt` com o passo a passo. Decodificação da folha impressa, célula a
célula: esperados `9634 7176 0952 4975 1800 0336 3219 7199 5031 0016` = lidos,
na mesma ordem, com os três códigos de zero inicial preservados.

### 8.5 Aplicação no DEV

O layout mexe em Python (`rotas/trabalhadores.py`), então a troca exigiu
reinício — não foi só hot-reload de template.

| Item | Valor |
|---|---|
| Serviço | o **mesmo** da porta 9000, sem segunda instância persistente |
| Comando | `.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 9000` (idêntico ao anterior) |
| pid | 1968959 → **2723704** (gravado em `/tmp/shambala_dev.pid`) |
| Log | `/tmp/uvicorn_dev2.log`, com marcador do reinício |
| Parada | `SIGTERM` no pid do listener, com guarda fail-closed: não relança se a porta continuar ocupada |

Depois do reinício:

- Smoke anônimo: `/` → 303 `/menu`; `/menu` → 303 `/login`; `/login` → 200;
  `/cadastros/trabalhadores` e `/checkin-trabalhador` → 303 `/login` (nenhum
  acesso anônimo novo).
- O CSS servido já traz o QR de 30,5 mm e `estrela_dourada.svg` responde
  200 `image/svg+xml`.
- **O startup não emitiu código** — a grava em `trabalhador_codigo` posterior ao
  reinício está zerada; seguem 70 códigos emitidos e 0 ativos sem código.
- Nada foi gravado no banco do DEV; o backup
  `~/Documentos/backup-shamballa/shambala-DEV-pre-TASK002-2026-10-23.dump`
  continua válido para esta mudança.
- Produção intocada, sem push, sem alteração de túneis.

Pendência que permanece a mesma do corpo do relatório: imprimir a carteirinha
**clicando na tela do DEV** continua sem validação, por faltar credencial de
teste — a validação do fluxo HTTP, das permissões e da impressão foi feita no
ambiente descartável. O teste físico de leitura por câmera de aparelho continua
não realizado, e pertence à TASK_003.

Commit desta revisão: **7f06841** —
`feat: layout da carteirinha em três faixas, com o QR de 30,5 mm como prioridade`
(4 arquivos alterados + o asset novo). Nenhum arquivo de outra tarefa foi
staged.


---

# Adendo — correção da FK de `trabalhador_codigo` (23/10/2026)

## 9. Correção de integridade: `ON DELETE CASCADE` → `ON DELETE RESTRICT`

### 9.1 O desvio, conferido no código e no banco

A tabela foi criada (seção 7 do corpo deste relatório, commit 03014b5) com

```sql
trabalhador_id  INTEGER NOT NULL REFERENCES trabalhadores(id) ON DELETE CASCADE
```

e o `pg_constraint` do DEV confirmava: `confdeltype = 'c'`.

Consequência concreta: excluir fisicamente um trabalhador levaria embora as
linhas dele em `trabalhador_codigo`. Como a reserva permanente de código vive
nessas linhas — inclusive nas que estão em estado `retirado`, que jamais devem
voltar a circular —,
o CASCADE desfazia exatamente a garantia de que uma carteirinha antiga não passa
a identificar outra pessoa: o código ficava livre e podia ser reemitido. Quem sai
de circulação é **inativado** (`toggle-ativo`, que nunca mexe no código — K3/K4),
não excluído. Nada no app fazia a exclusão, mas o banco permitia.

Correção pedida pelo dono, exclusivamente no DEV: RESTRICT no DDL de instalação
nova e em migração para bancos já criados, preservando integralmente os códigos
emitidos.

### 9.2 O que mudou

**`banco.py`, DDL de instalação nova** (`_migrar`, linha 568):

```sql
trabalhador_id  INTEGER NOT NULL REFERENCES trabalhadores(id) ON DELETE RESTRICT
```

**`banco.py`, migração para banco existente** (linhas 584–606). A regra de
exclusão mora na *constraint*, não numa `CHECK` — então `CREATE TABLE IF NOT
EXISTS` não a corrigiria num banco já criado. O startup consulta o catálogo e,
se a FK existir com `confdeltype <> 'r'`, troca a constraint pelo mesmo nome,
sem tocar em nenhuma linha:

```sql
DO $mig$ ... 
    IF nome IS NOT NULL AND regra <> 'r' THEN
        EXECUTE format('ALTER TABLE trabalhador_codigo DROP CONSTRAINT %%I', nome);
        EXECUTE format('ALTER TABLE trabalhador_codigo '
                       'ADD CONSTRAINT %%I FOREIGN KEY (trabalhador_id) '
                       'REFERENCES trabalhadores(id) ON DELETE RESTRICT', nome);
```

Propriedades: **transacional** (está dentro do mesmo `with conectar()` de todo o
schema; se falhar, nada fica pela metade), **idempotente** (reexecutar não
encontra mais o desvio) e **preservadora** (não apaga, regenera, reordena nem
recupera código; o nome da constraint é mantido, o que mantém estáveis as
mensagens de erro e as consultas ao catálogo). O `RAISE NOTICE` registra a troca
quando ela ocorre.

Detalhe que quase derrubou o startup: os `%%` são `%` literais. Todo texto passa
pela interpolação do psycopg2 (`_ConnCompat.execute` sempre envia `params=()`),
então `%I` dentro do `format()` era tratado como marcador de parâmetro e a
execução morria com `IndexError: tuple index out of range`. É a mesma armadilha
de sempre, agora documentada em comentário no próprio bloco.

Nada mais mudou: serviço, rotas, permissões, templates, CSS e layout aprovado
dos cartões permanecem como em 7f06841.

### 9.3 Validação no PostgreSQL descartável

Dois testes novos em `tests/test_codigo_trabalhador.py` (a suíte passou de 16
para 18; helper `_fk_trabalhador()` lê nome + `confdeltype` do catálogo):

- **K18** — instalação nova: a FK nasce `'r'`. Depois o teste **reproduz o estado
  antigo do banco**: refaz a constraint como `ON DELETE CASCADE` à mão, semeia um
  trabalhador com 1 código `ativo` + 1 `retirado`, tira o snapshot e roda
  `criar_tabelas()`. Resultado: regra vira `'r'`, **o mesmo nome** de constraint,
  snapshot idêntico byte a byte. Roda `criar_tabelas()` uma segunda vez: continua
  `'r'`, dados idênticos, nenhum erro (a segunda execução da migração é
  exatamente o cenário pedido). E o `DELETE` físico é recusado com
  `ForeignKeyViolation`, com o snapshot e o dono preservados.
- **K19** — pelo HTTP da tela: trabalhador com código atual e trabalhador com
  **somente códigos retirados** (linhas inseridas por SQL direto, com o
  comentário de que nenhum caminho da tela produz esse estado) são ambos
  recusados na exclusão. Inativar → 303 e o código **não muda**, resolvendo como
  `inativo`; reativar → 303, mesmo código, `ativo`. Trinta emissões novas nunca
  devolvem um código retirado, e quem só tem retirados continua sem código ativo.

Cobertura dos cinco cenários exigidos: exclusão com código atual recusada (K19),
exclusão com só retirados recusada (K19), inativação/reativação funcionando
(K19), retirados indisponíveis para nova emissão (K19), segunda execução da
migração sem alterar dados nem apresentar erro (K18).

**Runner completo** (`bash tests/rodar_testes.sh`, log
`/tmp/runner_fk_restrict.log`, saída 0):

| Suíte | Resultado |
|---|---|
| `test_tres_passes.py` | 27/27 |
| `test_plano_unico.py` | 12/12 |
| `test_permissoes.py` | 8/8 |
| `test_agenda_medium.py` | 10/10 |
| `test_presenca_trabalhadores.py` (TASK_001) | 19/19 — regressão preservada |
| `test_codigo_trabalhador.py` (TASK_002) | **18/18** |
| **Total** | **94/94** |

Conferência do DEV antes e depois do runner: `pessoas=664 checkins=2807` nos dois
momentos — o runner não tocou no DEV (trava de isolamento em vigor). O banco
descartável da migração (`shambala_codigo_mig_test`) foi removido pelo próprio
teste; restam apenas `shambala` (DEV) e `shambala_test_passes` (suíte).

### 9.4 Aplicação no DEV e prova de que nenhum código mudou

**Backup antes da escrita** — `pg_dump -Fc` do DEV para
`~/Documentos/backup-shamballa/shambala-DEV-pre-FKRESTRICT-2026-10-23.dump`
(335.735 bytes). Destino conferido antes do dump: o `SHAMBALA_DB_NAME` do `.env` do DEV
(`shambala`, em `/var/run/postgresql`) — o mesmo banco que atende o serviço da
porta 9000. Este relatório registra apenas metadados — tamanho,
contagens e o hash do snapshot —, sem segredos e sem a lista de códigos.
Recuperabilidade comprovada **por restauração**: o dump foi restaurado num banco
de conferência (`shambala_verifica_backup_fk`) e comparado com o DEV —
fingerprint de contagem das 42 tabelas `975faf3c64ac76e4ad9213ba244e4ac6`
idêntico dos dois lados, e snapshot de códigos idêntico. O banco de conferência
foi descartado em seguida.

**Estado antes** (catálogo e dados do DEV): `confdeltype='c'`, snapshot de
códigos `8b23c4308ac8dd2e49aae934cf0c0189`, 70 códigos.

**Aplicação** — nenhum serviço novo, nenhuma instância persistente: reiniciou-se
**somente** o serviço da porta 9000 (mesmo procedimento do adendo nº 8 — SIGTERM
no pid do listener com guarda fail-closed, espera por linha nova no log a partir
do offset de bytes gravado antes). pid **2723704 → 3363620**. A migração do
`_migrar()` rodou no startup e trocou a constraint.

**Estado depois:**

| Conferência | Antes | Depois |
|---|---|---|
| `pg_constraint` (`contype='f'` da coluna `trabalhador_id`) | `c` (CASCADE) | **`r` (RESTRICT)**, mesmo nome `trabalhador_codigo_trabalhador_id_fkey` |
| snapshot md5 dos códigos | `8b23c4308ac8dd2e49aae934cf0c0189` | **idêntico** |
| `trabalhador_codigo` | 70 linhas (70 ativos, 0 retirados) | 70 linhas (70 ativos, 0 retirados) |
| `trabalhadores` | 70 (70 ativos) | 70 (70 ativos) |

O md5 cobre `codigo`, `trabalhador_id`, `estado`, `criado_em`, `retirado_em`,
`origem` e `atendente_id` de todas as linhas, ordenado pelo código — não é
contagem, é conteúdo.

**Comportamento no DEV**, sem escrita: para isolar a FK dos códigos (o primeiro
ensaio em `BEGIN…ROLLBACK` foi barrado por
`financeiro_movimentacoes_trabalhador_id_fkey`, porque aquele trabalhador tem
mensalidades), listaram-se as seis FKs que apontam para `trabalhadores` e
escolheu-se uma pessoa referenciada **só** por `trabalhador_codigo` (id 9). No
DEV, dentro de transação com rollback:

```
ERRO:  atualização ou exclusão em tabela "trabalhadores" viola restrição de
       chave estrangeira "trabalhador_codigo_trabalhador_id_fkey" em "trabalhador_codigo"
DETALHE: Chave (id)=(9) ainda é referenciada pela tabela "trabalhador_codigo".
```

Recusada pela FK certa. Depois do rollback: `worker9=1`, 70 pessoas, 70 códigos,
mesmo md5. As demais FKs para `trabalhadores` continuam com `NO ACTION`
(`fiado_credito_mov`, `financeiro_movimentacoes`, `trabalhador_dias`,
`trabalhador_presenca`, `vendas_pdv`) — não são escopo desta correção e nada
nelas foi alterado.

**Smoke anônimo** (servidor real, sem credenciais): `/` → 303 `/menu`; `/menu` →
303 `/login`; `/login` → 200; `/cadastros/trabalhadores`,
`/checkin-trabalhador` e `/cadastros/trabalhadores/carteirinhas` → 303 `/login`.
`carteirinha.css` 200 (4.871 b) e `estrela_dourada.svg` 200 (473 b). Nenhuma
rota nova ficou acessível anonimamente.

**SHAMBALA_CONTEXTO.md**: a linha da FK passou a `ON DELETE RESTRICT` e ganhou o
parágrafo explicando por que a exclusão física não é a saída (quem sai de
circulação é inativado) e como o startup troca CASCADE por RESTRICT de forma
transacional e idempotente.

### 9.5 Arquivos e commit

| Arquivo | Mudança |
|---|---|
| `banco.py` | +36/−1 — DDL novo com `ON DELETE RESTRICT` e bloco `DO` de migração no `_migrar` |
| `tests/test_codigo_trabalhador.py` | +138 — helper `_fk_trabalhador()` e testes K18/K19 |
| `SHAMBALA_CONTEXTO.md` | +9/−1 — FK RESTRICT e a regra de preservação |
| este relatório | adendo nº 9 |

Dois commits locais, **sem push**, apenas com arquivos desta correção (os
demais arquivos já presentes no working tree, de outras tarefas, não foram
staged):

1. **implementação** — commit **a2f2e98**: `fix: FK de trabalhador_codigo passa a RESTRICT, com migração idempotente`
   (`banco.py`, `tests/test_codigo_trabalhador.py`, `SHAMBALA_CONTEXTO.md`);
2. **documentação** — este adendo, no commit seguinte.

Produção intocada, nenhum túnel alterado, TASK_003 não iniciada.

### 9.6 Limitações que continuam valendo

As deste relatório não mudam: o clique na tela do DEV para imprimir e para
preparar códigos continua sem validação por faltar credencial de teste (a
validação é do fluxo HTTP e do serviço, no descartável); a leitura por câmera de
aparelho físico não foi feita; mensalidades/fiado permanecem sem validação
funcional aprovada (limitação documentada da TASK_001, não ampliada aqui); e o
terminal de leitura é a TASK_003.
