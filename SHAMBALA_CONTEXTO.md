# Projeto Shambala — Contexto Completo
*Atualizado em 2026-05-18*

## Repositório

- **GitHub:** https://github.com/cmartinelli-hub/shambala
- **Produção (interno):** git@leal:/home/git/projetos/shambala/

---

## O que é

Sistema de gestão de atendimentos para a **Casa Espírita Shambala**, em Volta Redonda/RJ.
Substitui uma planilha Excel e um display com controle remoto problemático.

O Centro recebe pessoas alguns dias por semana. Elas chegam, fazem check-in na recepção,
recebem **cartões físicos reutilizáveis** com um código numérico, aguardam em um auditório
assistindo a uma palestra, e são chamadas pelo código exibido num monitor via WebSocket.

---

## Infraestrutura

- **Servidor:** Raspberry Pi 2 (1 GB RAM) ou qualquer máquina com Debian 12+
- **Serviço:** systemd (`shamballa.service`), inicia automático no boot
- **Porta:** 8000, acessível em toda a rede local (`--host 0.0.0.0`)
- **Monitor de chamada:** segundo dispositivo com navegador aberto em `/chamada` em tela cheia (F11)
- **Segundo monitor PDV:** terceiro dispositivo opcional em `/caixa/qrcode` para exibir QR Code PIX
- **Banco de dados:** PostgreSQL 12+
  - Host/porta/credenciais via variáveis de ambiente (`.env`)
  - Pool de conexões: 1-10 conexões simultâneas
  - Extensão `unaccent` para buscas normalizadas com acentos
- **Backups:**
  - `pg_dump` comprimido com envio opcional via SCP (remoto)
  - Backup em pendrive/HD externo configurável via `/configuracoes/backup-pendrive`

---

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12+ + FastAPI |
| Banco | PostgreSQL 12+ (psycopg2, sem ORM) |
| Pool de conexões | SimpleConnectionPool (1-10 conns) |
| Templates | Jinja2 (server-side rendering) |
| Frontend | HTML + CSS + JS puro (sem framework) |
| Tempo real | WebSocket (tela de chamada + segundo monitor PDV) |
| Serviço | systemd |

**Idioma do código:** Português em tudo — variáveis, funções, rotas, tabelas, campos.

### Contas × formas de pagamento (TASK_016)

Contrato único em `servico_sessao.py` — `CONTAS_ELEGIVEIS`, `TIPOS_DE_RECURSO`,
`conta_de_destino()` e `contas_de_destino()`. Não reimplementar a escolha em rota.

| Forma | Contas que recebem | Sessão | Efeito na gaveta |
|---|---|---|---|
| `especie` | só `especie` | exigida | sim |
| `pix` | `pix`, `banco`, `poupanca` | opcional | não |

Regras: `areceber` (obrigação), `legado` (origem histórica) e `outro` **não** são
destino de dinheiro. Conta inexistente/inativa/incompatível é recusada
(`conta_invalida`/`conta_inativa`/`conta_incompativel`) — nunca substituída em
silêncio. Com mais de uma conta elegível, o recebimento **exige escolha**; com uma
só, ela é o padrão e a tela mostra qual. `conta_id` integra o corpo da tentativa
(trocar a conta com a mesma chave é `tentativa_conflito`).
`conta_id_por_tipo` devolve id só quando o tipo identifica UMA conta ativa — na
ambiguidade devolve `None` de propósito.

**Fechamento de sessão legado**: o destino vem de `vendas_pdv.conta_id`, gravado
no momento da venda. Venda anterior à coluna (NULL) **não é deduzida**: o
fechamento recusa (`conta_do_fechamento`), a tela pede a conta
(`conta_legado_<forma>`) e o POST valida contra o mesmo contrato. Venda a fiado vai
sempre para a conta de obrigações. Nada é atribuído retroativamente.

Seletor de conta nas telas: macro `templates/_conta_destino.html` — usado no PDV,
fiado, mensalidade, doação e fechamento. Não reimplementar o seletor à mão.

Saldo na tela de contas: **Disponível** = entrada−saída com `status='pago'`;
**Pendente** = a mesma conta com `pendente`. `areceber` aparece marcada como
obrigação e **não** entra em "Recursos disponíveis". Contas de caixa seguem
separadas das contas operacionais (`caixas`) e das sessões (`caixa_movimentos`).

### Ícones da interface (TASK_015)

Emojis de interface foram substituídos por **Lucide** (`lucide-static@0.460.0`,
licença ISC), servidos localmente em `static/icons/*.svg` — sem CDN e sem
dependência de rede. Convenção para as próximas tarefas:

| Uso | Como |
|---|---|
| Template | `{{ icone("wallet") }}` — helper em `templates_config.py` |
| Nome | o do arquivo em `static/icons/` (ex.: `calendar-days`, `triangle-alert`) |
| Decorativo (com texto ao lado) | padrão: `alt=""` + `aria-hidden="true"` |
| Controle **só** com ícone | `{{ icone("x", rotulo="Cancelar") }}` → `aria-label` obrigatório |
| Classe extra | `{{ icone("check", classe="icone-ok") }}` |
| Nome inexistente | **levanta erro** na renderização (não some em silêncio) |
| CSS | `.icone` em `static/css/base.css`; `.menu-card-icon .icone` fixa 2,2rem |

Regras: não reintroduzir emoji como ícone; **não** substituir emoji em conteúdo
digitado pelo usuário, dados históricos, documentos emitidos (cupom, carteirinha,
PDF) nem na estrela dourada da identidade. Novo ícone = baixar de
`https://unpkg.com/lucide-static@<versão>/icons/<nome>.svg` (fixar a versão) e
registrar em `static/icons/LICENSE.md`.

**Variáveis de ambiente (.env):**
```
SHAMBALA_DB_HOST=localhost
SHAMBALA_DB_PORT=5432
SHAMBALA_DB_NAME=shambala
SHAMBALA_DB_USER=shambala
SHAMBALA_DB_PASS=sua_senha_aqui
SHAMBALA_BACKUP_REMOTE_HOST=
SHAMBALA_BACKUP_REMOTE_USER=
SHAMBALA_BACKUP_REMOTE_PATH=
```

**Dependências (`requirements.txt`):**
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
psycopg2-binary>=2.9.0
python-dotenv>=1.0.0
jinja2>=3.1.0
requests>=2.31.0
python-multipart>=0.0.9
qrcode[pil]>=7.4.2
bcrypt>=4.0.0
```

---

## Tipos de atendimento

| Tipo | Descrição |
|---|---|
| **Passe** | Fila simples, qualquer médium disponível. Médiuns de passe NÃO são cadastrados. |
| **Reiki** | Fila própria, funciona como o passe mas com coluna separada no dashboard. |
| **Acolhimento Fraterno** | Triagem para novos ou sem plano ativo. Gera um plano de tratamento. |
| **Atendimento Mediúnico** | Vinculado a um médium específico. Pode ser individual ou em grupo/família. |

---

## Decisões de negócio

| Tema | Decisão |
|---|---|
| Códigos dos cartões | Sem sequência, sem prefixo, digitados manualmente |
| Cartões | Reutilizáveis — ao encerrar o dia os vínculos são desfeitos |
| Passe e Reiki | Filas simples; médiuns de passe/reiki não são cadastrados |
| Atendimento em grupo | Um único código para toda a família/grupo; acompanhantes vinculados pelo check-in |
| Plano de tratamento | Contador de sessões + agendamentos automáticos por frequência |
| Plano avulso | Criado automaticamente ao agendar avulso na Agenda; sem geração de sessões futuras |
| Sessões com passe | Configurável por plano (primeiras N sessões exigem passe, 0=nunca, -1=sempre) |
| Vagas por médium | Cada médium tem um limite de atendimentos por dia (`vagas_dia`, padrão 10) |
| Falta | 2 faltas consecutivas → plano cancelado automaticamente |
| Plano por pessoa | Um plano de tratamento ativo por vez; novo plano exige novo acolhimento fraterno |
| Prioridade na fila | Idosos 70+, deficientes, prioridade manual (flag na pessoa) |
| Senha única no dia | Não se pode reutilizar uma senha já em uso por outra pessoa no mesmo dia |
| Pessoa + passe no mesmo dia | Sim — recebe dois cartões |
| Controle de acesso | Sessão em memória, cookie httponly+samesite=lax |
| Login padrão | admin / admin (trocar após primeiro acesso) |

---

## Modelo de dados (PostgreSQL)

### `pessoas`
```
id, nome_apresentacao (NOT NULL), nome_completo, telefone, email, cpf (UNIQUE)
cep, logradouro, numero, complemento, bairro, cidade, uf
data_nascimento (YYYY-MM-DD), deficiencia (0/1), prioridade (0/1), foto_pessoa
```

### `lacos`
```
id, pessoa_id (FK), pessoa_relacionada_id (FK), tipo_laco
```
Informativo, sem impacto operacional. Ex: "esposo(a)", "filho(a)".

### `mediuns`
```
id, nome_completo (NOT NULL), telefone, email
cep, logradouro, numero, complemento, bairro, cidade, uf
ativo (0/1, padrão 1), vagas_dia (padrão 10), foto_medium
```

### `atendentes` (usuários do sistema)
```
id, nome_usuario (UNIQUE NOT NULL), nome_completo (NOT NULL)
senha_hash, telefone, email, ativo (padrão 1)
grupo_id (FK grupos, nullable)
```
> O módulo é `rotas/usuarios.py` mas a tabela no banco chama-se `atendentes`.
> A rota `/cadastros/atendentes` redireciona para `/cadastros/usuarios` (compatibilidade).
> Atendente inicial criado automaticamente: **admin / admin**

### `dias_trabalho`
```
id, data (UNIQUE), aberto (0/1)
```

### `mediuns_dia`
```
id, dia_trabalho_id (FK), medium_id (FK), vagas_dia (nullable override)  — UNIQUE(dia_trabalho_id, medium_id)
```

### `planos_tratamento`
```
id, medium_id (FK), sessoes_total, sessoes_realizadas (padrão 0)
data_inicio, concluido (0/1, legado), frequencia (semanal/quinzenal/mensal/avulso)
status (ativo/alta/cancelado, padrão ativo), sessoes_com_passe (padrão 3)
```

### `plano_pessoas`
```
id, plano_id (FK), pessoa_id (FK)  — UNIQUE(plano_id, pessoa_id)
```
Permite múltiplas pessoas no mesmo plano (família/grupo).

### `agendamentos`
```
id, plano_id (FK), data, status (agendado/realizado/faltou/cancelado)
requer_passe (0/1), encaixe (0/1)
```

### `checkins`
```
id, dia_trabalho_id (FK), pessoa_id (FK), hora_checkin
codigo_passe, codigo_acolhimento, codigo_atendimento, codigo_reiki
plano_id (FK nullable), medium_id (FK nullable), agendamento_id (FK nullable)
passe_realizado, acolhimento_realizado, atendimento_realizado, reiki_realizado
acolhimento_chamado
```

### `dias_atendimento`
```
dia_semana (PRIMARY KEY, 0-6: seg-dom), descricao (TEXT)
```
Configurável via `/configuracoes`. Padrão: segunda (0) e quarta (2).

### `trabalhadores`
```
id, nome_completo (NOT NULL), telefone, email, cpf (UNIQUE), rg, data_nascimento
endereco completo (cep, logradouro, numero, complemento, bairro, cidade, uf)
valor_mensalidade (NUMERIC 10,2), dia_vencimento (padrão 10)
ativo (0/1, padrão 1), foto_trabalhador, created_at (YYYY-MM-DD)
```

### `trabalhador_dias`
```
id, trabalhador_id (FK), dia_semana (0-6)
UNIQUE(trabalhador_id, dia_semana)
```

### `trabalhador_presenca`
```
id, trabalhador_id (FK), data (DATE), dia_trabalho_id (FK nullable)
presente (0/1), hora_chegada (TEXT nullable), hora_saida (TEXT nullable)
origem_chegada (TEXT nullable: terminal_qr | terminal_codigo | recepcao
                 | correcao_administrativa)
UNIQUE(trabalhador_id, data)   ← a data é própria; dia_trabalho é opcional
```
A presença não depende de existir dia de atendimento; `data` é sempre a data
de São Paulo calculada pelo servidor (`servico_presenca`).

`origem_chegada` diz **por onde a pessoa chegou**: lida no QR do terminal,
digitada no terminal, ou registrada pela recepção. Nulo = fato anterior ao
terminal, cuja origem não se conhece — nunca se inventa. A coluna é adicionada
por migração idempotente (`banco._migrar`) e só é escrita por
`servico_presenca.registrar_chegada(origem=…)`; `OrigemInvalida` recusa origem
desconhecida. Repetição não altera horário nem origem: quem já está presente
mantém a primeira chegada.

`correcao_administrativa` **não é canal de chegada**: é a marca de que o fato
foi escrito pelo serviço de correção (TASK_004). `registrar_chegada` recusa
essa origem explicitamente; só `servico_correcao_presenca.aplicar_correcao` a
produce. `registrar_ausencia_explicita` nunca rebaixa uma presença: sobre fato
presente devolve `exige_correcao` e não grava nada — alteração de fato já
lançado passa obrigatoriamente pela trilha auditada.

### `trabalhador_presenca_correcao`
```
id, trabalhador_id (FK RESTRICT), data_fato (DATE),
operacao (lancamento_retroativo | correcao_horario | conversao_ausencia
          | conversao_presenca | anulacao),
anterior_* / novo_* (presente, hora_chegada, hora_saida, origem,
                     dia_trabalho_id)   ← snapshots planos; todos os
                     anterior_* nulos = não havia registro; todos os novo_*
                     nulos = anulado (Sem registro)
motivo (TEXT NOT NULL, nunca vazio), atendente_id (FK nullable),
criado_em (TEXT com fuso de São Paulo)
```
Apêndice de auditoria: a interface **não** faz UPDATE nem DELETE aqui; cada
correção grava fato + linha de auditoria **na mesma transação** (falhou a
auditoria, o fato não persiste). A anulação é um `DELETE` da linha operacional
— como ausência de linha já significa "Sem registro" em todo o código, o
`UNIQUE(trabalhador_id, data)` fica livre e uma chegada legítima posterior
recria o fato sem apagar nada do histórico; as linhas de auditoria são
auto-suficientes (não referenciam o id descartável da presença). A escrita
concorrência é serializada por `FOR UPDATE` na linha do trabalhador (a mesma
trava da chegada do terminal) e protegida contra sobrescreimento pelo token
`estado_base` do formulário, re-confestado depois do bloqueio: divergiu,
`ConflitoCorrecao` e nada é gravado. Fatos anteriores à TASK_004 não têm linha
de auditoria — é o legado esperado, nunca inventado.

### `trabalhador_codigo`
```
codigo (TEXT PRIMARY KEY, CHECK codigo ~ '^[0-9]{4}$')
trabalhador_id (FK, ON DELETE RESTRICT)
estado (TEXT: 'ativo' | 'retirado')
atendente_id (FK nullable)     ← nulo quando a ação veio de script local
origem (TEXT: 'tela' | 'preparacao_local')
criado_em (TEXT 'AAAA-MM-DD HH:MM' São Paulo), retirado_em (TEXT nullable)
```
Uma linha por código **já emitido**. Índice único parcial
`uq_trabalhador_codigo_atual ON (trabalhador_id) WHERE estado='ativo'` garante
exatamente um código atual por pessoa. Um código `retirado` nunca volta à
circulação — nem para o mesmo trabalhador — de modo que carteirinha antiga
nunca passa a identificar outra pessoa. Os 4 caracteres são texto
(`0047` é `0047` no banco, na tela, no QR e na resolução).

A FK é **RESTRICT de propósito**: excluir fisicamente um trabalhador apagaria
juntamente os códigos dele (com CASCADE), e é justamente a existência dessas
linhas que impede a reutilização. Quem sai de circulação é **inativado** — a
linha fica reservada com a pessoa. `criar_tabelas()` troca CASCADE por RESTRICT
em banco já criado (a regra mora na constraint, então `CREATE TABLE IF NOT
EXISTS` não a corrigiria); a troca é transacional e idempotente, e não apaga,
regenera nem reordena código nenhum.

Regra única em `servico_codigo_trabalhador.py`: `garantir_codigo` (idempotente),
`trocar_codigo` (retira e insere na mesma transação),
`preparar_codigos_pendentes` (ação explícita, transação única),
`resolver_codigo` (somente leitura), `qr_data_uri` (PNG local, sem serviço
externo). Emissão por sorteio controlado com `ON CONFLICT DO NOTHING` e até 8
tentativas; `CodigosEsgotados` quando os 10.000 já saíram.

**Contrato usado pelo terminal de leitura (TASK_003):** o QR codifica
exatamente a string de 4 dígitos — sem URL, nome, documento, id interno ou
token. `resolver_codigo(conn, codigo)` devolve `estado` ∈ `invalido` (formato),
`ativo`, `inativo`, `retirado`, `inexistente` e, quando `ativo`,
`trabalhador_id` + `nome_completo`. Resolver **não** registra presença:
`servico_chegada.registrar_chegada_por_codigo()` é quem faz os dois na mesma
transação. Não existe (e não se deve criar) endpoint público de busca por código.

## Terminal de presença (`/terminal`)

Aparelho fixo na entrada da Casa. Uma pessoa autorizada **ativa** o terminal no
aparelho; depois disso o voluntário não faz login — apresenta a carteirinha ou
digita os quatro números.

- **Sessão própria e restrita** (`rotas/terminal.py`): cookie `sessao_terminal`,
  httpOnly, SameSite=Lax (Secure quando `SHAMBALA_HTTPS=true`), 24 h desde a
  ativação, emitida só por `POST /terminal/ativar` de operador autenticado com
  `checkin.trabalhadores` na ação **escrever** — a mesma permissão do check-in da
  recepção. Na transição a sessão **comum daquele navegador** é encerrada (as de
  outros aparelhos continuam); nenhuma outra rota procura `sessao_terminal`,
  então o aparelho não alcança cadastro, financeiro ou menu. `POST
  /terminal/encerrar` desliga; voltar a administrar exige login normal. Se a
  pessoa que ativou for inativada, a sessão cai junto (a autorização é conferida
  em cada requisição).
- **Um único fluxo de chegada** para QR e teclado: `POST /terminal/chegada`
  valida formato e sessão no servidor, resolve o código, trava a linha do
  trabalhador (`FOR UPDATE`, a mesma trava de `trocar_codigo`), re-resolve o
  código depois do bloqueio e grava `registrar_chegada` na mesma transação,
  confirmando antes de responder. Carteirinha retirada no meio da corrida vira
  recusa, nunca presença de outra pessoa.
- **O que a resposta contém:** `resultado`, `nome`, `foto` (só um caminho
  `/static/fotos/<nome>` validado por regex), `data` e `hora`. Recusa não devolve
  nome nem foto. CPF, telefone, mensalidade, fiado e histórico não saem.
- **Tela** (`templates/terminal/leitura.html`, `static/css/terminal.css`,
  `static/js/terminal.js`): sem navegação geral, retrato e paisagem, leitor
  **jsQR 1.4.0** vendorizado (Apache-2.0, `static/js/vendor/jsqr-1.4.0/`, com a
  licença no repositório — sem CDN, sem serviço externo, o vídeo não sai do
  aparelho). Aceita só os quatro dígitos ASCII, preservando zeros iniciais;
  conteúdo diferente é recusado no aparelho, sem abrir link. Sucesso mostra nome,
  foto (se houver) e o horário do servidor; repetição mostra "Presença já
  registrada às HH:MM" mantendo a primeira chegada; ~3 s depois a tela volta a
  observar. A mesma carteirinha no enquadro não reenvia nem toca sem parar
  (precisa sair do enquadro para rearma). Sem câmera — negada, ausente, ocupada
  ou endereço não seguro — o teclado continua operando.
- **Contexto seguro:** navegador só libera câmera em HTTPS ou localhost. No
  endereço HTTP da LAN (`http://<host>:9000`) o terminal funciona **pelo teclado**
  e avisa exatamente isso; liberar a câmera exige HTTPS nesse endereço (ver
  `SHAMBALA_HTTPS`). Nada de túnel ou domínio novo para "resolver" isso.
  Decisão do dono (24/10/2026): o DEV **permanece** em HTTP na porta 9000 — não
  se montam nginx, TLS, certificados, CA ou porta nova por causa do terminal.
  Homologa-se a digitação no acesso atual; a leitura por câmera fica pendente de
  um ambiente HTTPS **já existente**, não de um a criar.
- **Sem** fila offline, reconhecimento facial, geolocalização, controle de saída
  ou punição.

### `grupos` e `grupos_permissoes`
```
grupos:
  id, nome (UNIQUE NOT NULL), descricao

grupos_permissoes:
  id, grupo_id (FK), modulo (TEXT NOT NULL)
  ler (BOOLEAN), escrever (BOOLEAN), apagar (BOOLEAN)
  UNIQUE(grupo_id, modulo)
```
Modelo Unix de permissões (ler/escrever/apagar) por módulo. A cobrança é
feita em todas as rotas por `guarda_modulo()` (`rotas/permissoes.py`):
**GET exige `ler`, POST/PUT/DELETE exigem `escrever`** — **negação por
padrão**: quem não tem a linha em `grupos_permissoes` não acessa nem vê o
card no menu. Membros do grupo **Admin (id=1)** têm bypass total. Chaves de
módulo: `ROTULOS_ROTA` em `rotas/permissoes.py` (fonte da tela de
Permissões). Seed de migração dos grupos antigos: `semear_permissoes.py`.
Módulos novos **nunca são concedidos em seed** — `checkin.trabalhadores.correcoes`
(TASK_004) nasce sem linha em nenhum grupo: só Admin enxerga, até alguém
conceder na tela de Permissões.

### `usuarios_grupos`
```
id, usuario_id (FK atendentes), grupo_id (FK grupos)
UNIQUE(usuario_id, grupo_id)
```

### `atendentes_mediuns`
```
id, atendente_id (FK atendentes, ON DELETE CASCADE),
medium_id (FK mediuns, ON DELETE CASCADE)
UNIQUE(atendente_id, medium_id)
```
Vínculo N:N entre o usuário e os médiuns cuja agenda ele gerencia, marcado em
Cadastros → Usuários → "Agendas do Médium". **É o escopo da `/agenda/medium`**:
o usuário só vê (e só lança) nas agendas marcadas; sem vínculo, a tela fica
vazia e pede para falar com o administrador. Admin é exceção — vê todas, para
conferir e corrigir.

### `configuracoes_smtp`
```
id, chave (UNIQUE NOT NULL), valor (NOT NULL DEFAULT '')
```
Chaves: smtp_servidor, smtp_porta, smtp_usuario, smtp_senha, smtp_email_de.

### `configuracoes_backup`
```
id, chave (UNIQUE NOT NULL), valor (NOT NULL DEFAULT '')
```
Chaves: backup_host, backup_user, backup_path, backup_ssh_key_path.

### `configuracoes_centro`
```
id, chave (UNIQUE NOT NULL), valor (NOT NULL DEFAULT '')
```
Chaves: `centro_nome` (nome do centro para comprovantes), `centro_logo` (nome do arquivo em `/static/logos/`).

### `configuracoes_backup_pendrive`
```
id, tipo_backup (VARCHAR 20), dispositivo (VARCHAR 100)
ponto_montagem (VARCHAR 255), ativo (INTEGER DEFAULT 0)
horario_backup (TIME), criado_em (TIMESTAMP), atualizado_em (TIMESTAMP)
```

### `backup_pendrive_historico`
```
id, data_backup (TIMESTAMP), status (VARCHAR 20)
caminho_backup (VARCHAR 255), tamanho_backup (BIGINT)
espaco_disponivel (BIGINT), mensagem_erro (TEXT)
```

---

## Módulo Financeiro (`/financeiro`)

### Tabela `financeiro_movimentacoes`
```
id, tipo (entrada/saida), categoria (mensalidade/doacao/venda_pdv/recebimento_fiado/
  credito_anticipado/sangria/transferencia/livro/etc)
valor (NUMERIC 10,2), data_movimentacao (TEXT YYYY-MM-DD)
descricao (TEXT), trabalhador_id (FK nullable), pessoa_id (FK nullable)
pix_copiadecola (TEXT), status (pago/pendente/cancelado, padrão pago)
caixa_id (FK caixas, nullable)  ← adicionado via migração
forma_pagamento (TEXT), conta_id (FK contas_financeiras), data_pagamento (TEXT)
sangria_id (FK caixa_sangrias) — TASK_008: par saída+entrada da sangria-transferência
nota_fiscal_foto (TEXT)
```

### Rotas
```
GET       /financeiro                   ← Visão Geral (TASK_011): abas, período, filtros, paginação
GET       /financeiro/lancamento/{id}   ← detalhe somente-leitura (?volta= validado, só /financeiro?…)
GET/POST  /financeiro/nova              ← registrar movimento
GET       /financeiro/mensalidades      ← controle de mensalidades
POST      /financeiro/mensalidades/gerar ← geração manual (era GET até a TASK_012 §3)
GET       /financeiro/relatorios        ← recorte com filtros validados (TASK_012 §5)
GET       /financeiro/fiados            ← fiado por trabalhador (original/recebido/saldo)
GET       /financeiro/mensalidades/relatorio ← mês por competência|vencimento|pagamento
POST      /financeiro/{id}/pagar        ← marcar como pago
POST      /financeiro/{id}/cancelar     ← cancelar movimento
```

### Contrato da Visão Geral (TASK_011)
- Universo das somas: somente `financeiro_movimentacoes`. Eventos de caixa
  (`caixa_sessao_eventos`) aparecem no detalhe por proveniência (`financeiro_id`),
  nunca somados por cima.
- Parâmetros de `GET /financeiro`: `aba` (mov|pend), `de`/`ate` (ou alias `mes=YYYY-MM`),
  `conta`, `nat`, `sentido` (entrada|saida), `sit` (pago|pendente|cancelado), `q`,
  `pagina`, `por` (25|50|100). Recusas preservam o valor digitado (banner), nada de
  "Todos" silencioso.
- Recebimentos/Pagamentos/Resultado: status pago, EXCLUINDO `categoria IN
  ('transferencia','sangria')` pelo valor armazenado e linhas da conta
  `tipo='areceber'` (obrigação nunca é dinheiro). Rótulo fixo: registro do período,
  não é saldo disponível.
- Aba pendências: `status='pendente'` por vencimento dentro do período (só o período;
  nota na tela quando há filtros extras). Fiado em aberto (`vendas_pdv`
  `forma_pagamento='fiado' AND NOT fiado_pago AND status='concluida'`) é grupo
  próprio, explicitamente FORA do indicador "A receber".
- **TASK_012 §5 (2026-10-25)**: o saldo de fiado exibido é
  `max(0, total − fiado_credito_usado − alocado)`, calculado SÓ por
  `servico_fiado` (`linha_de_saldo`, `fiados_em_aberto_para_consulta`,
  `saldo_por_trabalhador`, `historico_fiado`) — nenhuma tela tem fórmula própria.
  Totais do recorte ≠ totais da página (a lista corta em 50, os números não).
  Dívida sem alocação registrada é marcada (`sem_alocacao`) e mantém o valor de
  face: legado não é distribuído por estimativa. Cada cartão traz um `<details>`
  "O que conta"; relatórios separam **Recebido / Pago / A receber / A pagar /
  Cancelados**; o relatório de mensalidades tem seletor `data_uso`
  (comp|venc|pag) dizendo QUAL data o mês filtra; `Σ` por conta é o acumulado
  até a data final, declarado como tal (não é saldo bancário).
- Linha de conta `areceber` com status pago: preservada no banco, rotulada
  "Registro histórico — conferir origem", excluída dos totais de dinheiro.
  Conta `legado` exibida como "Conta Antiga (origem histórica)".
- Paginação no servidor (`ORDER BY data_movimentacao DESC, id DESC`), totais sobre o
  recorte inteiro; página fora do intervalo é ajustada preservando filtros.
- GETs nunca escrevem (o dashboard NÃO chama `sincronizar_mensalidades`).
- Rótulos humanos via `ROTULOS_CATEGORIA`/`ROTULOS_SITUACAO`/`ROTULOS_TIPO_CONTA`
  em `rotas/financeiro.py`; moeda formatada pelo filtro Jinja `moeda`.
- RBAC: `guarda_modulo('financeiro')` em todas as rotas, inclusive
  `/financeiro/mensalidades/buscar` (antes vazava JSON com CPF sem guarda).

---

## Módulo Caixa/PDV (`/caixa`)

### Tabelas

**`caixas`**
```
id, nome (NOT NULL UNIQUE), descricao, ativo (padrão 1)
usa_produtos (BOOLEAN, padrão TRUE; FALSE = caixa de preço aberto — Geral e Bazar no seed)
chave_pix_id (FK chaves_pix, nullable)  ← atenção: coluna no BD mas tabela chaves_pix não está em banco.py
```
Caixas padrão (seed): Geral, Lanchonete, Biblioteca, Bazar.

**`chaves_pix`**
```
id, nome, tipo (cpf/cnpj/email/telefone/aleatoria), chave, cidade, ativa (BOOLEAN)
```
> **Atenção:** a criação desta tabela e da coluna `caixas.chave_pix_id` não está em `banco.py` ainda — pendente de adicionar.

**`produtos`**
```
id, nome (NOT NULL), categoria (salgado_assado/salgado_frito/bebida/doce/outro)
preco_custo (NUMERIC 10,2), preco_venda (NUMERIC 10,2)
quantidade_estoque (INTEGER), codigo_barras (UNIQUE nullable), ativo (padrão 1)
caixa_id (FK caixas) — produto pertence a um caixa; venda cruza caixa/product é recusada
```

**`vendas_pdv`**
```
id, caixa_id (FK NOT NULL), data_venda (TEXT YYYY-MM-DD)
total (NUMERIC 10,2), forma_pagamento (especie/pix/fiado), troco (NUMERIC 10,2)
status (concluida/cancelada), atendente_id (FK atendentes nullable), observacao
caixa_movimento_id (FK caixa_movimentos), doacao_valor (NUMERIC 10,2)
fiado_trabalhador_id (FK), fiado_pago, fiado_data_pagamento, fiado_credito_usado (NUMERIC 10,2)
criada_em (TEXT, instante do servidor America/Sao_Paulo, timestamp(0)) — metadado PROSPECTIVO
  gravado só a partir da TASK_007; vendas antigas permanecem NULL e nunca são backfilled
```

**`vendas_pdv_cancelamento`** (TASK_007 — prova durável do cancelamento efetivo)
```
id, venda_id (FK vendas_pdv, UNIQUE — um cancelamento efetivo por venda),
atendente_id (FK atendentes, autor), motivo (NOT NULL, não vazio),
criado_em (TEXT, instante SP), sessao_movimento_id (FK nullable),
valor_sessao_revertido, total_venda, doacao_venda, forma_pagamento,
estoque_revertido (JSON com saldo anterior/posterior por produto)
FKs RESTRICT: a prova sobrevive à inativação do autor; sem exclusão em cascata
```

**`vendas_pdv_itens`**
```
id, venda_id (FK ON DELETE CASCADE), produto_id (FK)
nome_produto (TEXT, snapshot), quantidade, preco_unitario, subtotal
```

**`caixa_movimentos`** (uma linha = uma sessão de gaveta)
```
id, caixa_id (FK caixas), data_abertura/data_fechamento (TEXT), saldo_inicial/saldo_final
total_vendas/total_sangrias (acumuladores da REGRA LEGADA), status (aberto/fechado),
atendente_id_abertura/atendente_id_fechamento, observacao_fechamento
versao (TASK_008: 'legado' = regra antiga com acumuladores, padrão em linhas antigas;
  'eventos' = sessões abertas após TASK_008, vivem de caixa_sessao_eventos)
dinheiro_contado/divergencia_contagem/observacao_conferencia (TASK_008: conferência
  física do fechamento 'eventos'; NULL em legado)
UNIQUE parcial: uq_caixa_movimentos_aberto(caixa_id) WHERE status='aberto'
```

**`caixa_sessao_eventos`** (TASK_008 — fonte única das sessões 'eventos')
```
id, identidade (TEXT UNIQUE — idempotência real no banco: mesmo comando/documento
  não produz dois eventos), sessao_id (FK caixa_movimentos),
tipo (venda/venda_doacao/venda_credito/cancelamento/doacao/mensalidade/
  recebimento_fiado/credito_anticipado/sangria), natureza (comercial/recebimento/retirada),
origem_tipo + origem_id (persistida), venda_id (FK nullable), financeiro_id
  (FK financeiro_movimentacoes — vínculo 1:1 quando já existe lançamento),
forma (especie/pix/fiado/credito), sentido (entrada/saida),
valor (NUMERIC(12,2) > 0), efeito_fisico (NUMERIC(12,2) — contribuição assinada
  para a gaveta; PIX, fiado não recebido e crédito consumido entram com 0),
conta_id, atendente_id, observacao,
criado_em (TEXT, instante do servidor America/Sao_Paulo timestamp(0))
```

### Rotas
```
WS        /caixa/ws                         ← WebSocket segundo monitor (QR Code)
GET       /caixa/qrcode                     ← tela segundo monitor (fullscreen)
GET       /caixa/pdv?caixa_id=N             ← PDV (terminal de venda)
POST      /caixa/pdv/finalizar              ← finalizar venda (form)
GET       /caixa/pix-qr?valor=X&caixa_id=N ← gera QR PIX + transmite ao segundo monitor
GET       /caixa/vendas?caixa_id=N&data=D   ← histórico de vendas do dia
POST      /caixa/vendas/{id}/cancelar       ← cancelamento integral rastreável (TASK_007; admin, motivo+confirmação)
GET       /caixa/vendas/{id}/cancelar       ← tela de prévia/confirmação do cancelamento
GET       /caixa/vendas/{id}/comprovante    ← comanda imprimível (popup/print)
GET       /caixa/produtos/buscar?q=X        ← JSON autocomplete + código de barras
GET       /caixa/produtos                   ← lista de produtos
GET/POST  /caixa/produtos/novo              ← cadastrar produto
GET/POST  /caixa/produtos/{id}/editar       ← editar produto
POST      /caixa/produtos/{id}/toggle-ativo ← ativar/desativar produto
GET       /caixa/caixas                     ← lista de caixas + chaves PIX
POST      /caixa/caixas/novo                ← criar caixa
POST      /caixa/caixas/{id}/pix            ← vincular chave PIX à caixa
POST      /caixa/caixas/{id}/toggle-ativo   ← ativar/desativar caixa
GET/POST  /caixa/abrir                      ← abrir sessão NOVA ('eventos'; uma por caixa — lock + índice único)
GET/POST  /caixa/fechar                     ← prévia detalhada + contagem física (eventos) ou regra antiga (legado; formulário novo é recusado)
GET       /caixa/fechar/{mov_id}/pdf        ← resumo original do fechamento
GET/POST  /caixa/doacao                     ← doação avulsa com destino explícito (espécie exige sessão)
GET       /caixa/fiados                     ← fiados/créditos com barra de destino (sessão/conta)
POST      /caixa/fiados/{venda}/pagar       ← quitação por venda (identidade fiadopagar:{venda_id})
POST      /caixa/fiados/{trabalhador}/pagar-divida ← quitação total/parcial — uma entrada; sobra vira crédito sem segundo lançamento
POST      /caixa/fiados/credito/{trabalhador}      ← adiantamento: lança entrada financeira + evento (antes não era registrado)
GET/POST  /caixa/sangria                    ← sangria com destino obrigatório (par transferência, efeito global zero)
GET       /caixa/sangria/{id}/comprovante
```

### Fluxo PDV
1. Operador seleciona caixa → entra no PDV
2. Busca produto por nome (autocomplete) ou código de barras (leitor USB)
3. Adiciona itens ao carrinho (JS puro, sem reload)
4. Ao selecionar PIX: sistema gera QR Code via `GET /caixa/pix-qr` e transmite ao segundo monitor via WebSocket
5. Finaliza venda: `POST /caixa/pdv/finalizar` (form com `itens_json`)
6. Comprovante disponível em `/caixa/vendas/{id}/comprovante` (layout 80mm para impressão)

### Finalização de venda: servidor é a única fonte de valores (TASK_006 / FIN-01)
- O navegador envia `produto_id` + `quantidade` por linha; `nome`, `preco_unitario`,
  `subtotal` enviados são **só para detecção de divergência** — nunca persistem nem somam.
- O preço vigente é consultado no banco com `FOR UPDATE` (produtos ativos, em `ORDER BY id`)
  antes de qualquer escrita; subtotal por linha e total são `Decimal` quantizado em
  ROUND_HALF_UP a centavos, e o total é a soma dos subtotais persistidos.
- Duplicatas do mesmo produto são agregadas (uma linha, quantidade somada) antes de
  validar estoque e limites; quantidade precisa ser inteiro ≥ 1 (recusa 0, negativo,
  fracionário, booleano, malformado e acima de `MAX_QUANTIDADE`); teto NUMERIC(10,2)
  → recusa `total_estourado`, sem 500.
- Preço do cadastro negativo ou zero → `produto_preco_invalido` (não vira R$ 0,00).
- Divergência entre o exibido e o servidor → a tela re-renderiza com a cesta reprificada
  (`cesta_revisar` + aviso) e **nada** é gravado (venda, estoque, sessão, crédito); o
  operador revisa e confirma de novo, e aí o servidor revalida do zero.
- Caixa com `usa_produtos = FALSE` (Geral, Bazar) aceita linha sem produto com valor
  declarado — essa é a regra real do preço aberto; misturar produto de catálogo nesse
  caixa é recusado (`produto_fora_do_caixa`).
- O snapshot histórico de nome/preço nos `vendas_pdv_itens` vem do cadastro; vendas
  antigas nunca são recalculadas pelo preço atual.
- Troco (`recebido − total`, só espécie), doação, acúmulo da sessão
  (`total_vendas += total + doação`) e política de estoque preservados como eram
  — em sessão `eventos` (TASK_008) o acúmulo foi substituído por eventos detalhados;
  o acumulador legado continua valendo só para sessões `legado`.
- Crédito usado, limite de fiado, sessão e comprovante usam exatamente o mesmo total.
- Validação completa antes de qualquer escrita; venda, itens, estoque, crédito e
  acumuladores da sessão vivem numa única transação (`conectar()`), com lock de
  trabalhador e da linha de sessão — falha em qualquer gravação reverte tudo.

### Cancelamento de venda com reversão rastreável (TASK_007 / FIN-02)
- Um único serviço (`servico_cancelamento.py`) decide e executa; as rotas
  `GET/POST /caixa/vendas/{id}/cancelar` e `/caixa/fiados/{venda}/cancelar` apenas
  delegam. Cancelar é restrito a administrador (`e_admin`), exige motivo não vazio e
  confirmação explícita após prévia dos efeitos.
- `avaliar()` classifica com os vínculos realmente persistidos: elegível só quando o
  efeito é comprovável — sessão aberta do mesmo caixa, baixas de estoque originais por
  item, fiado não pago sem recebimento nem movimento de crédito posterior à venda
  (`criada_em`). Tudo ambíguo (sessão ausente/fechada/externa, venda sem vínculo,
  fiado pago/parcial, legado sem instante) **bloqueia com motivo legível e zero efeitos**
  — nada é recalculado nem "encaixado" (recalcular a sessão inteira é FIN-04).
- `cancelar()` executa numa transação única, com travas na ordem venda → produtos
  (`ORDER BY id`) → `caixa_movimentos` (`status='aberto'`): devolve o estoque com
  movimento `entrada` compensatório sobre o saldo atual travado, subtrai `total + doação`
  do `total_vendas` da sessão, muda `status='cancelada'` e grava a prova durável em
  `vendas_pdv_cancelamento` (motivo, autor, instante, valor revertido, estoque
  revertido; UNIQUE(venda_id) dá idempotência no banco). Documentos originais nunca
  são apagados.
- Fechamento de caixa passou a travar a sessão (`FOR UPDATE`), serializando com o
  cancelamento; totais operacionais excluem `cancelada`, o histórico mostra o
  cancelamento quando existe, e o comprovante de venda cancelada traz o aviso — com a
  frase "sem prova de reversão" para o legado já cancelado.
- Metadado prospectivo: `vendas_pdv.criada_em` (instante America/Sao_Paulo, gravado na
  finalização). Linhas antigas ficam NULL e nunca são preenchidas por heurística.
- Fora do escopo (bloqueado, não implementado): reembolso de fiado pago (FIN-06),
  lançamento financeiro após fechamento (FIN-03/FIN-04), devolução parcial/perda.

### Sessão por eventos, destino do recebimento e conferência física (TASK_008 / FIN-04)
- Sessão aberta após a entrega nasce `versao='eventos'` e tem UMA fonte de verdade:
  `caixa_sessao_eventos`. Cada operação grava um evento com identidade UNIQUE
  (`venda:{id}:venda|doacao|credito|cancelamento`, `sangria:{id}`, `doacao:{fin_id}`,
  `mensalidade:{mov_id}`, `fiadopagar:{venda_id}`, `recebimento_fiado:{fin_id}`,
  `credito:{cred_id}`) — repetição do mesmo comando/documento é recusada PELO BANCO,
  e a exceção vaza da transação para rollback integral.
- `efeito_fisico` é a contribuição assinada para a gaveta: soma espécie recebida
  (líquida do troco; doação sai da sobra entregue, `recebido < total+doação` é recusado);
  PIX, fiado não recebido e consumo de crédito anterior entram com **zero**.
  `esperado = saldo_inicial + Σ efeito_fisico` — mesma fórmula no resumo, no fechamento
  e no PDF.
- Destino nunca é presumido: espécie exige sessão aberta explicitamente selecionada
  (e conta coerente conferida NO BANCO pelo tipo — fora de PIX o `conta_pix_id` do
  navegador é ignorado); PIX funciona sem gaveta e cria o lançamento na hora, na conta
  PIX válida. Falta de destino → recusa antes de baixar qualquer pendência.
- Uma sessão aberta por caixa: `pg_advisory_xact_lock` + checagem + INSERT e índice
  parcial único; sangria v2 é transferência interna (par saída/entrada com mesmo
  `sangria_id`, efeito global zero, destino obrigatório ≠ gaveta).
- Fechamento `eventos`: trava a sessão (`FOR UPDATE`, ordem de travas da TASK_006/007 —
  escrita concorrente entra antes ou é recusada depois), lança financeiramente agora
  apenas vendas/doações ainda sem lançamento e não canceladas (vínculo persistido,
  nunca dois lançamentos para a mesma origem), registra `dinheiro_contado` ao lado do
  esperado com divergência e observação obrigatória quando ≠ 0 — o esperado NUNCA é
  sobrescrito; fechamento repetido não duplica nada.
- Sessões `legado` (todas as anteriores + as que existiam abertas): continuam na regra
  antiga de acumuladores, intactas, consultáveis e fecháveis pelo fluxo antigo; o
  formulário novo de contagem nelas é recusado (`sessao_legada`). **Não aceitam novos
  recebimentos** (correção da revisão do dono, 2026-10-24): `sessao_para_recebimento`
  levanta `sessao_legada_recebimento` antes de qualquer escrita em mensalidade/doação/
  quitação/adiantamento, com exceção vazando da transação (rollback integral); as barras
  de destino nem listam legado. Vendas e sangrias ainda operam a gaveta antiga pela regra
  antiga. Nenhum backfill heurístico, nenhum reparo retroativo.
- Cancelamento (TASK_007) adapta-se: compensa em `eventos` via `venda:{id}:cancelamento`
  (efeito físico só se espécie) preservando estoque/idempotência/bloqueios.
- Não implementado (aberto): competência/unicidade de mensalidades, alocação detalhada
  de fiado, reembolso pós-fechamento, conciliação, cartão/taxas. Saldos de crédito
  antigos sem origem preservados sem eventos.

### PIX (geração de payload)
- Payload EMV completo com CRC16 (`_gerar_payload_pix` em `rotas/caixa.py`)
- Chave PIX buscada da tabela `chaves_pix` vinculada ao caixa selecionado
- QR transmitido ao segundo monitor (`/caixa/qrcode`) via WebSocket

---

## Módulo Biblioteca (`/biblioteca`)

### Tabelas
**`livros`**
```
id, isbn (UNIQUE nullable), titulo (NOT NULL), autor (nullable)
editora (nullable), ano (INTEGER nullable), edicao (nullable)
quantidade (INTEGER NOT NULL DEFAULT 1), preco_venda (NUMERIC 10,2 DEFAULT 0)
observacao (TEXT nullable)
```

**`emprestimos`**
```
id, livro_id (FK), pessoa_id (FK)
data_emprestimo (TEXT DEFAULT hoje), data_devolucao (TEXT nullable)
observacao (TEXT nullable)
```

**`vendas_livros`**
```
id, livro_id (FK), pessoa_id (FK nullable), quantidade (DEFAULT 1)
valor_total (NUMERIC 10,2 NOT NULL DEFAULT 0)
data_venda (TEXT DEFAULT hoje), observacao (TEXT nullable)
```

### Rotas
```
GET       /biblioteca                   ← lista de livros com busca
GET/POST  /biblioteca/livro/novo        ← cadastro novo livro (com busca ISBN/OpenLibrary)
GET       /biblioteca/livro/{id}/editar ← edição de livro
POST      /biblioteca/livro/{id}/apagar ← remover livro
GET       /biblioteca/emprestimo        ← registro de empréstimos
POST      /biblioteca/emprestimo/{id}/devolver ← marcação de devolução
GET       /biblioteca/venda             ← histórico de vendas
POST      /biblioteca/venda/nova        ← registrar venda
```

---

## Módulo Doações (`/doacoes`)

### Tabelas
**`doacoes_cestas`**
```
id, pessoa_id (FK NOT NULL), data_entrega (TEXT NOT NULL)
itens (TEXT descrição do conteúdo), observacao (TEXT)
entregue (INTEGER NOT NULL DEFAULT 0)
```

**`tipos_doacao`**
```
id, nome (UNIQUE NOT NULL), descricao, ativo (padrão 1)
```
Seeds: Cesta Básica, Roupas, Calçados, Brinquedos, Higiene Pessoal, Outros.

**`doacao_itens`**
```
id, doacao_id (FK ON DELETE CASCADE), tipo_doacao_id (FK), quantidade (padrão 1)
UNIQUE(doacao_id, tipo_doacao_id)
```

### Rotas
```
GET       /doacoes                      ← lista de doações com filtros
GET/POST  /doacoes/nova                 ← registro de nova cesta
POST      /doacoes/{id}/entregar        ← marcar como entregue
POST      /doacoes/{id}/desentrega      ← reverter entrega
GET/POST  /doacoes/{id}/editar          ← edição de doação
POST      /doacoes/{id}/apagar          ← remover registro
```

---

## Agenda do Médium (`/agenda/medium`)

Quem gerencia a agenda de um médium (o **cambona**, grupo Cambonas) mantém a
agenda **em papel**; o trabalho no sistema é transcrever essa papelada, sem
celular à mão. A tela é otimizada para esse lançamento rápido (tipografia
maior, um botão só) e tem módulo de permissão **próprio**: `agenda.medium`
("Agenda do Médium") — quem tem só essa permissão não entra na `/agenda` geral
nem em `cadastros.mediuns`.

**Escopo = vínculo** (`atendentes_mediuns`, marcado em Cadastros → Usuários):
a tela mostra e aceita lançamento **apenas** nas agendas dos médiuns do usuário.
Sem vínculo, a tela fica vazia com um aviso para falar com o administrador.
Admin é exceção: vê todas as agendas, para conferir e corrigir.

Blocos da tela:
- **Chips de médium** — só os médiuns do usuário; "Todos os meus" reúne as
  agendas dele (nunca as dos demais).
- **Próximos atendimentos** — sessões `agendado` dos próximos 30 dias,
  agrupadas por data, com a pessoa e o progresso `sessão n/total`.
- **Atendidos no último dia** — quem o médium atendeu na última
  `dias_trabalho ≤ hoje` (check-ins com `atendimento_realizado = 1` e
  `codigo_atendimento`, um titular por código de atendimento).
- **Lançar na agenda** — busca de pessoa, médium e data; quando a pessoa
  **não** tem plano ativo, entram qtde de sessões (padrão 10) e frequência.

`POST /agenda/medium/lancar` resolve o caso no servidor (a tela não precisa
conhecer as regras):
1. **plano ativo no médium escolhido** → acrescenta 1 sessão naquela data
   (mesma validação da agenda do plano: `data_atendimento_valida`, recusa
   data duplicada, `_reaplicar_piso_passe` depois);
2. **plano ativo em OUTRO médium** → recusa orientando ("já tem plano ativo
   com X — valide com a recepção"); nunca duplica plano (regra do plano único);
3. **sem plano ativo** → cria o plano no médium (`datas_agendamento` +
   `gerar_agendamentos_plano`, `sessoes_com_passe = 0` = piso do centro),
   exatamente como `POST /agenda/novo`.

A data precisa cair em dia de atendimento (`data_atendimento_valida`) — a tela
recusa com aviso em vez de deslocar a data da papelada silenciosamente.

A busca de pessoas devolve `{id, nome, medium}` — `medium` é o médium do plano
ativo (NULL se não houver). Serve para desfazer **homônimos** ("maria cristina"
há quatro), o erro mais comum da transcrição; o dropdown mostra o médium em
segunda linha. Os demais chamadores ignoram o campo extra.

---

## Rotas completas

```
GET/POST  /login
GET       /logout
GET       /menu
GET       /

GET       /cadastros/pessoas
GET/POST  /cadastros/pessoas/novo
GET/POST  /cadastros/pessoas/{id}/editar
GET/POST  /cadastros/pessoas/{id}/lacos
GET       /cadastros/pessoas/{id}              ← ficha completa (DEVE ser a ÚLTIMA rota)
GET       /cadastros/pessoas/buscar            ← JSON autocomplete

GET       /cadastros/mediuns
GET/POST  /cadastros/mediuns/novo
GET/POST  /cadastros/mediuns/{id}/editar
POST      /cadastros/mediuns/{id}/toggle-ativo
GET       /cadastros/mediuns/{id}/planos
POST      /cadastros/mediuns/{id}/planos/novo
POST      /cadastros/mediuns/{id}/planos/{plano_id}/alta
POST      /cadastros/mediuns/{id}/planos/{plano_id}/cancelar
GET       /cadastros/mediuns/{id}/planos/{plano_id}/agenda

GET       /cadastros/usuarios                  ← gestão de atendentes
GET/POST  /cadastros/usuarios/novo
GET/POST  /cadastros/usuarios/{id}/editar
POST      /cadastros/usuarios/{id}/toggle-ativo

GET       /cadastros/trabalhadores             ← gestão de voluntários (módulo cadastros.trabalhadores)
GET/POST  /cadastros/trabalhadores/novo
GET/POST  /cadastros/trabalhadores/{id}/editar
POST      /cadastros/trabalhadores/{id}/toggle-ativo
GET       /cadastros/trabalhadores/{id}/agenda    ← dias de trabalho do voluntário
POST      /cadastros/trabalhadores/{id}/agenda
GET       /cadastros/trabalhadores/{id}/presenca  ← histórico individual
POST      /cadastros/trabalhadores/preparar-codigos  ← emite os códigos que faltam
POST      /cadastros/trabalhadores/{id}/codigo/gerar
POST      /cadastros/trabalhadores/{id}/codigo/trocar (confirmar=1)
GET       /cadastros/trabalhadores/{id}/carteirinha   ← impressão individual
GET       /cadastros/trabalhadores/carteirinhas?ids=… | ?todos=ativos  ← lote A4
GET       /checkin-trabalhador                 ← gestão do dia (módulo checkin.trabalhadores):
                                          data consultável (?data=, default hoje SP),
                                          busca + filtros server-side, dia passado é
                                          consulta somente leitura; refresco parcial
                                          sem recarregar a página
GET       /checkin-trabalhador/dados           ← JSON da grade (mesma guarda `ler`)
POST      /checkin-trabalhador/{id}/presente
POST      /checkin-trabalhador/{id}/ausente   ← nunca rebaixa presença: sobre fato
                                          presente devolve o aviso que aponta a
                                          correção autorizada
GET/POST  /checkin-trabalhador/{id}/correcao  ← correções e lançamentos retroativos
                                          (módulo checkin.trabalhadores.correcoes;
                                          GET=ler, POST=escrever; motivo obrigatório;
                                          data passada/hoje; conflito detectado)
GET       /relatorios/presenca-trabalhadores   ← período/dia (7 dias)/origem/trabalhador
                                          (parâmetro `trabalhador` = texto da lista;
                                          vazio=todos, fora da lista=recusa "Selecione
                                          um trabalhador da lista"; nomes repetidos
                                          distinguem-se pelo código do crachá); sem
                                          percentuais; inativos com fato participam

POST      /terminal/ativar               ← operador (checkin.trabalhadores:escrever) habilita
                                          o aparelho da entrada; encerra a sessão COMUM
                                          daquele navegador e emite `sessao_terminal` (24 h)
GET       /terminal[/]                   ← tela do terminal, sem navegação geral
POST      /terminal/chegada              ← JSON {codigo,origem}; QR e teclado pelo mesmo
                                          caminho; 401 sem sessão de terminal
POST      /terminal/encerrar             ← desliga o terminal; administrar de novo exige login

GET       /cadastros/permissoes                ← controle RBAC

GET       /dia                          ← abrir dia ou painel (detecta automaticamente)
POST      /dia/abrir
POST      /dia/encerrar
GET       /dia/dashboard                ← DASHBOARD — todas as filas em colunas
POST      /dia/dashboard/passe/{id}/chamar
POST      /dia/dashboard/passe/{id}/realizado
POST      /dia/dashboard/reiki/{id}/chamar
POST      /dia/dashboard/reiki/{id}/realizado
POST      /dia/dashboard/acolhimento/{id}/chamar
POST      /dia/dashboard/atendimento/{id}/chamar
POST      /dia/dashboard/atendimento/{id}/realizado
GET       /dia/checkin                  ← busca de pessoa
GET/POST  /dia/checkin/{pessoa_id}      ← formulário de check-in
GET       /dia/passe                    ← fila de passe
POST      /dia/passe/{id}/chamar/realizado/desfazer
GET       /dia/reiki                    ← fila de Reiki
POST      /dia/reiki/{id}/chamar/realizado/desfazer
GET       /dia/acolhimento              ← fila de acolhimento fraterno
POST      /dia/acolhimento/{id}/chamar
GET       /dia/mediuns/{medium_id}      ← fila de atendimento de um médium
POST      /dia/atendimento/{id}/chamar/realizado/desfazer
GET/POST  /dia/fraterno/{checkin_id}    ← criar plano a partir do acolhimento

GET       /agenda                       ← agenda geral por médium e período
POST      /agenda/novo                  ← cria plano avulso + agendamento
GET       /agenda/imprimir              ← agenda do médium em um dia (impressão)

GET       /agenda/medium                ← Agenda do Médium (módulo agenda.medium)
GET       /agenda/medium/situacao       ← JSON: plano ativo da pessoa p/ o formulário
POST      /agenda/medium/lancar         ← lança na agenda de papel (3 casos)

GET       /chamada                      ← tela fullscreen WebSocket
GET       /chamada/ultimo               ← JSON {"codigo": "..."}
WS        /chamada/ws

GET       /configuracoes                ← configurar dias de atendimento, SMTP, backup remoto, centro
POST      /configuracoes/dias/adicionar
POST      /configuracoes/dias/{dia_semana}/remover
POST      /configuracoes/smtp           ← salvar configuração SMTP
POST      /configuracoes/backup         ← salvar configuração backup remoto
POST      /configuracoes/centro         ← salvar nome e logo do centro
GET       /configuracoes/pix            ← gerenciar chaves PIX
POST      /configuracoes/pix/novo       ← cadastrar chave PIX
POST      /configuracoes/pix/{id}/toggle ← ativar/desativar chave
POST      /configuracoes/pix/{id}/remover ← remover chave
GET       /configuracoes/backup-pendrive ← configuração backup em pendrive
POST      /configuracoes/backup-pendrive ← salvar config pendrive
POST      /configuracoes/backup-pendrive/testar ← testar montagem
POST      /configuracoes/backup-pendrive/executar ← executar backup agora

GET       /mala-direta                  ← envio de mensagens em massa
GET       /mala-direta/resultado        ← resultado de envio

GET       /relatorios
GET       /relatorios/por-dia
GET       /relatorios/por-pessoa
GET       /relatorios/por-medium
GET       /relatorios/frequencia

GET       /financeiro                   ← Visão Geral (abas/filtros/paginação — contrato TASK_011)
GET       /financeiro/lancamento/{id}   ← detalhe somente-leitura
GET/POST  /financeiro/nova              ← nova movimentação
GET       /financeiro/mensalidades
POST      /financeiro/{id}/pagar/cancelar

GET       /biblioteca                   ← acervo de livros
GET/POST  /biblioteca/livro/novo
GET/POST  /biblioteca/livro/{id}/editar
POST      /biblioteca/livro/{id}/apagar
GET       /biblioteca/emprestimo
POST      /biblioteca/emprestimo/{id}/devolver
GET       /biblioteca/venda
POST      /biblioteca/venda/nova

GET       /doacoes                      ← cestas básicas
GET/POST  /doacoes/nova
POST      /doacoes/{id}/entregar/desentrega
GET/POST  /doacoes/{id}/editar
POST      /doacoes/{id}/apagar

WS        /caixa/ws                     ← WebSocket segundo monitor PDV
GET       /caixa/qrcode                 ← tela segundo monitor (QR PIX)
GET       /caixa/pdv                    ← terminal de venda
POST      /caixa/pdv/finalizar
GET       /caixa/pix-qr                 ← gera e transmite QR Code PIX
GET       /caixa/vendas                 ← histórico de vendas
GET       /caixa/vendas/{id}/cancelar   ← prévia/confirmação (TASK_007)
POST      /caixa/vendas/{id}/cancelar   ← cancelamento integral rastreável (TASK_007)
GET       /caixa/vendas/{id}/comprovante
GET       /caixa/produtos/buscar        ← autocomplete + código de barras
GET       /caixa/produtos
GET/POST  /caixa/produtos/novo
GET/POST  /caixa/produtos/{id}/editar
POST      /caixa/produtos/{id}/toggle-ativo
GET       /caixa/caixas
POST      /caixa/caixas/novo
POST      /caixa/caixas/{id}/pix
POST      /caixa/caixas/{id}/toggle-ativo
GET/POST  /caixa/abrir                  ← nova sessão 'eventos' (uma por caixa)
GET/POST  /caixa/fechar                 ← eventos: prévia + contagem; legado: regra antiga
GET       /caixa/fechar/{mov_id}/pdf
GET/POST  /caixa/doacao                 ← destino explícito (espécie exige sessão)
GET       /caixa/fiados
POST      /caixa/fiados/{venda}/pagar
POST      /caixa/fiados/{trabalhador}/pagar-divida
POST      /caixa/fiados/credito/{trabalhador}
GET/POST  /caixa/sangria                ← destino obrigatório, par transferência
GET       /caixa/sangria/{id}/comprovante
```

---

## Funcionalidades notáveis

### Dashboard (`/dia/dashboard`)
Tela estilo planilha com uma coluna por fila: **Passe | Reiki | Acolhimento | Médium 1 | Médium 2...**
- Código em destaque, primeiro nome, ícones de prioridade (⚑ ♿ ★)
- **Botão 📢 Chamar** — canto inferior esquerdo
- **Botão ✓ Realizado** — canto superior direito (distanciados para evitar click acidental)
- Coluna Acolhimento **não tem** botão Realizado (só via form fraterno)
- Painel "Última chamada" acima das colunas, com animação WebSocket
- Auto-atualização a cada 30 segundos
- Realizado em atendimento propaga para todos com mesmo `codigo_atendimento`
- Fila deduplica por `codigo_atendimento` (mostra só o titular — `MIN(id)`)

### Prioridade na fila
Expressão SQL `_ORDEM_PRIORIDADE` em `rotas/dia.py`:
- `p.prioridade` — flag manual
- `p.deficiencia` — flag de deficiência
- Cálculo de idade ≥ 70 a partir de `p.data_nascimento`
- Ordem secundária: `c.hora_checkin ASC` (chegada)

### Fluxo do check-in
1. Busca por nome (normalização de acentos via `norm()`)
2. Sistema mostra situação: agendamento hoje, plano ativo, vagas do médium
3. Digitação de senhas conforme aplicável
4. **Validação de senha duplicada:** `_senhas_em_uso()` impede reutilização no mesmo dia
5. **Acompanhantes:** busca por nome (autocomplete) — recebem o mesmo `codigo_atendimento`

### Agendamentos
- **Automáticos:** `gerar_agendamentos_plano()` em `banco.py` — semanal (7d), quinzenal (14d), mensal (28d), avulso (sem geração)
- **Avulso pela Agenda:** `POST /agenda/novo` cria plano `avulso` (1 sessão) + agendamento

### Ficha da pessoa (`/cadastros/pessoas/{id}`)
- Dados pessoais, laços familiares, histórico de planos, histórico completo de check-ins
- **ATENÇÃO:** a rota `/{id}` deve ser declarada por **último** em `rotas/pessoas.py`

### PDV / Caixa
- Carrinho em JS puro (sem reload de página)
- Busca de produto: nome (autocomplete) ou código de barras (leitor USB HID)
- PIX: `GET /caixa/pix-qr` gera payload EMV + QR e transmite via WebSocket ao segundo monitor
- Espécie: campo valor recebido → calcula troco
- Comprovante: página otimizada para impressão direta no navegador (`@media print`, tamanho 80mm)
- Texto ESC/POS formatado gerado em `_gerar_recibo()` para futura impressora térmica

---

## Arquivos-chave

| Arquivo | Função |
|---|---|
| `main.py` | App FastAPI, lifespan, monta todas as rotas, filtro `data_br` |
| `banco.py` | `conectar()`, `criar_tabelas()`, `_migrar()`, pool psycopg2, `gerar_agendamentos_plano()` |
| `templates_config.py` | Instância única de `Jinja2Templates`; filtros `data_br`, `data_hora_br`, `moeda` e o global `asset_url('css/base.css')` → `/static/css/base.css?v=<mtime>` |
| `backup.py` | Backup via `pg_dump` + envio SCP opcional |
| `backup_pendrive.py` | Backup em pendrive/HD externo (montar, desmontar, pg_dump, histórico) |
| `maiusculas.py` | Capitalização inteligente de nomes |
| `normalizar_telefones.py` | Padronização de telefones para `(XX) XXXXX-XXXX` |
| `servico_presenca.py` | Regra única da presença do trabalhador (chegada, ausência, data SP) |
| `servico_correcao_presenca.py` | Escrita corrigida única da presença (TASK_004): validar, travar, conferir o estado, aplicar e auditar na mesma transação |
| `servico_codigo_trabalhador.py` | Regra única do código de 4 dígitos: emitir, trocar, preparar, resolver, QR |
| `servico_pdv.py` | Precificação do PDV no servidor (TASK_006/FIN-01): validar quantidades, consultar preço vigente com trava, somar em Decimal ROUND_HALF_UP e detectar divergência de preço sem escrever nada |
| `servico_cancelamento.py` | Cancelamento de venda (TASK_007/FIN-02): avaliar elegibilidade pelos vínculos persistidos e reverter só efeitos comprováveis (estoque, sessão, doação) numa transação única com prova em `vendas_pdv_cancelamento` |
| `servico_sessao.py` | Regra única da sessão de caixa (TASK_008/FIN-04): eventos com identidade, destino validado no banco, resumo/fechamento/conferência física pela mesma fórmula, travas e idempotência; recebimento em sessão `legado` é recusado antes de qualquer escrita. TASK_010/REV-02: `moeda_nao_negativa` (0 aceito, negativo rejeitado antes do arredondamento, HALF_UP a centavo, teto após) para saldo inicial e dinheiro contado; `moeda()` segue estritamente positiva para eventos |
| `tests/trava_isolamento.py` | Trava que exige banco comprovadamente descartável (`*_test*` + `SHAMBALA_MODO_TESTE=1`); usada pelo lifespan do app ANTES do `criar_tabelas` e por toda suíte antes de destruir. TASK_010: `exigir_configuracao_consoante` confronta o destino pedido no shell com o arquivo `SHAMBALA_ENV_FILE` (autoridade) e aborta sem efeitos quando divergem; validação nunca mais existe sem conexão efetiva |
| `tests/subir_app_descartavel.sh` | Única forma segura de subir app temporário de teste: recusa iniciar ANTES do startup quando o env não é descartável (incidente TASK_008); `uvicorn` direto fica reservado ao serviço real da 9000. TASK_010/REV-03: importa `banco` só depois do env aplicado e valida `current_database()`/`current_user()` da CONEXÃO EFETIVA antes do exec |
| `tests/rodar_testes.sh` | Runner oficial. TASK_010/REV-01: env temporário 0600 por execução como destino efetivo, sufixo `_test` exigido antes de qualquer DROP/CREATE, mata apenas o PID que ele registrou, e vincula cada suíte ao token da execução corrente (identidade provada, nunca PASS/PULADO). Registro o hash da árvore testada (`HEAD` + `-dirty`) no cabeçalho do log para a evidência auto-identificar o código validado |
| `tests/preparo_descartavel.py` | Selo de posse `preparo_descartavel(token)`: recriar banco exige selo de execução nossa; resíduo selado é reclamado e recriado limpo; jamais DROP de banco sem selo |
| `tests/test_sentinelas_isolamento.sh` | Prova negativa A/B de ponta a ponta (runner e launcher reais): conflito de destino aborta antes de DDL/seed/startup com as sentinelas intactas |
| `tests/test_mutacoes_rev01_02_03.sh` | Provas antes/depois em CÓPIA ISOLADA: reintroduzir cada achado (REV-01/02/03) faz a prova pertinente falhar; o produto nunca é mutado |
| `preparar_codigos_trabalhadores.py` | Operação local: emite os códigos que faltam (simulação / `--aplicar`) |
| `requirements.txt` | Dependências Python do projeto |
| `instalar.sh` | Script de instalação automatizada |
| `atualizar.sh` | Script de atualização do sistema |
| `shamballa.service` | Definição do serviço systemd |

### Rotas
| Módulo | Função |
|---|---|
| `rotas/auth.py` | Login/logout, sessões em memória, `obter_atendente_logado()` |
| `rotas/pessoas.py` | CRUD pessoas, laços, ficha — `/{id}` por último |
| `rotas/mediuns.py` | CRUD médiuns, planos de tratamento, agenda |
| `rotas/usuarios.py` | Gestão de usuários/atendentes (tabela: `atendentes`) |
| `rotas/dia.py` | Painel, dashboard, todas as filas, fraterno |
| `rotas/checkin.py` | Busca, form check-in, `_situacao_pessoa()`, `_senhas_em_uso()` |
| `rotas/chamada.py` | WebSocket, transmissão de códigos |
| `rotas/agenda.py` | Agenda global, criação de plano avulso |
| `rotas/relatorios.py` | Por dia, médium, pessoa, frequência |
| `rotas/configuracoes.py` | Dias de atendimento, SMTP, backup remoto, centro, PIX, pendrive |
| `rotas/trabalhadores.py` | Voluntários: cadastro, presença, agenda, códigos e carteirinhas |
| `rotas/financeiro.py` | Movimentações, mensalidades, PIX |
| `rotas/biblioteca.py` | Acervo, ISBN/OpenLibrary, empréstimos, vendas |
| `rotas/doacoes.py` | Cestas básicas, rastreamento |
| `rotas/mala_direta.py` | Envio em massa WhatsApp/email |
| `rotas/permissoes.py` | Controle RBAC (grupos + permissões Unix ler/escrever/apagar) |
| `rotas/caixa.py` | PDV, produtos, caixas, QR Code PIX, histórico de vendas, comprovante — finalização usa `servico_pdv` como única fonte de valores; cancelamento (GET/POST) e fechamento delegam ao `servico_cancelamento` / travam a sessão. TASK_010: abertura/fechamento usam `moeda_nao_negativa`; PDV sem sessão aberta opera só PIX (banner + espécie/fiado bloqueados) quando há conta PIX válida |

---

## Convenções de código

- Código 100% em português (variáveis, rotas, funções, tabelas, comentários)
- **Nomes:** capitalização inteligente com `_capitalizar_nome()` (partículas em minúsculas)
- **Autenticação:** sessão em memória (`dict[str, int]`) via cookie `httponly; samesite=lax`
- **Sem JWT, sem banco de sessões**
- **Banco:** `psycopg2` com pool, context manager `with conectar() as conn:`, `RealDictCursor`
- **Parâmetros SQL:** `%s` (psycopg2), nunca `?` (SQLite)
- **Datas:** armazenadas como `TEXT` `YYYY-MM-DD`; exibidas via filtro Jinja2 `data_br`
  (instantes de auditoria: `data_hora_br` → `DD/MM/AAAA HH:MM:SS`, sem microssegundos)
- **Telefones:** formato `(XX) XXXXX-XXXX`
- **Ordem de rotas:** rotas com parâmetro dinâmico (`/{id}`) sempre **por último** no módulo — evita conflito com `/buscar`, `/novo` etc. (vale para `rotas/pessoas.py`, `rotas/caixa.py`, etc.)
- **Folha de estilo:** todo `<link rel="stylesheet">` usa `{{ asset_url('css/<arquivo>.css') }}`
  (global Jinja em `templates_config.py`), nunca `/static/css/…` direto. O `StaticFiles` não
  envia `Cache-Control`; sem `?v=<mtime>` o navegador serve o CSS por cache heurístico e a
  tela fica visualmente defasada em relação ao código (causa da divergência vista na
  homologação da TASK_011).

---

## Deploy e operação

### Instalação automática

```bash
git clone https://github.com/cmartinelli-hub/shambala.git
cd shambala
sudo bash instalar.sh
```

O script `instalar.sh` faz tudo: verifica Python, cria venv, instala dependências, configura systemd.

### Desenvolvimento

```bash
cd /nc/projetos/shambala
.venv/bin/uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Produção

```bash
sudo systemctl status shamballa     # ver status
sudo systemctl restart shamballa    # reiniciar
sudo journalctl -u shamballa -f     # logs ao vivo
```

### Backup

```bash
# Backup manual
pg_dump -h localhost -U shambala shambala | gzip > backup-$(date +%Y-%m-%d).sql.gz

# Restaurar
gunzip < backup-2026-05-18.sql.gz | psql -h localhost -U shambala shambala
```

### Git/GitHub
```bash
git push github main   # remote chama-se "github", não "origin"
```

### Atualização

```bash
cd /nc/projetos/shambala
git pull github main
.venv/bin/pip install -r requirements.txt
sudo systemctl restart shamballa
```

### Manutenção de dados

```bash
# Capitalizar nomes
.venv/bin/python maiusculas.py           # simulação
.venv/bin/python maiusculas.py --aplicar # aplicar

# Normalizar telefones
.venv/bin/python normalizar_telefones.py           # simulação
.venv/bin/python normalizar_telefones.py --aplicar # aplicar
```

---

## Troubleshooting

| Problema | Causa | Solução |
|---|---|---|
| 404 em `/cadastros/pessoas/123` | Rota `/{id}` não está por última | Reorganizar rotas em `pessoas.py` |
| 404 em `/caixa/produtos/buscar` | Mesma causa, em `caixa.py` | `buscar` e `novo` devem vir antes de `/{id}/editar` |
| Nomes aparecem errados | Não capitalizados | Rodar `maiusculas.py --aplicar` |
| Dashboard vazio | Dia não foi aberto | Acessar `/dia` e abrir dia de trabalho |
| Última chamada não aparece | WebSocket desconectado | Recarregar `/chamada` |
| Erro ao conectar banco | PostgreSQL offline | `sudo systemctl status postgresql` |
| "relation does not exist" | Tabelas não criadas | Rodar `python3 banco.py` |
| "relation chaves_pix does not exist" | Tabela não está em banco.py | Criar manualmente ou adicionar ao banco.py |
| Erro ao encerrar dia | Backup falhando | Verificar espaço em disco, permissões |
| QR Code PIX não aparece no segundo monitor | WebSocket desconectado | Recarregar `/caixa/qrcode` |

---

## Endereços importantes (produção)

| O quê | Endereço |
|---|---|
| Sistema (recepção) | `http://<IP-DO-SERVIDOR>:8000` |
| Dashboard | `http://<IP-DO-SERVIDOR>:8000/dia/dashboard` |
| Tela de chamada | `http://<IP-DO-SERVIDOR>:8000/chamada` |
| PDV | `http://<IP-DO-SERVIDOR>:8000/caixa/pdv` |
| Segundo monitor PDV | `http://<IP-DO-SERVIDOR>:8000/caixa/qrcode` |
| Login padrão | admin / admin |

---

## Estrutura de diretórios

```
shambala/
├── main.py                          # App FastAPI principal
├── banco.py                         # Conexões PostgreSQL, tabelas, migrações
├── templates_config.py              # Instância Jinja2 centralizada
├── backup.py                        # Backup via pg_dump + SCP remoto
├── backup_pendrive.py               # Backup em pendrive/HD externo
├── maiusculas.py                    # Script: capitaliza nomes
├── normalizar_telefones.py          # Script: padroniza telefones
├── requirements.txt                 # Dependências Python
├── instalar.sh                      # Script de instalação
├── atualizar.sh                     # Script de atualização
├── abrir-chamada.sh                 # Abre tela de chamada no navegador
├── gerar_pacote.sh                  # Gera pacote para transferência
├── .env.example                     # Template de configuração
├── .gitignore
├── shamballa.service                # Systemd service
├── rotas/
│   ├── auth.py                      # /login, /logout, sessões
│   ├── pessoas.py                   # /cadastros/pessoas
│   ├── mediuns.py                   # /cadastros/mediuns
│   ├── usuarios.py                  # /cadastros/usuarios (atendentes)
│   ├── trabalhadores.py             # /cadastros/trabalhadores, /checkin-trabalhador
│   ├── dia.py                       # /dia — painel, dashboard, filas
│   ├── checkin.py                   # /dia/checkin — entrada
│   ├── chamada.py                   # /chamada — WebSocket
│   ├── agenda.py                    # /agenda
│   ├── relatorios.py                # /relatorios
│   ├── configuracoes.py             # /configuracoes (dias, smtp, backup, centro, pix, pendrive)
│   ├── mala_direta.py               # /mala-direta
│   ├── financeiro.py                # /financeiro
│   ├── biblioteca.py                # /biblioteca
│   ├── doacoes.py                   # /doacoes
│   ├── permissoes.py                # /cadastros/permissoes (RBAC)
│   └── caixa.py                     # /caixa — PDV, produtos, vendas, QR PIX
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── menu.html
│   ├── chamada.html
│   ├── imprimir_agenda.html
│   ├── pessoas/
│   ├── mediuns/
│   ├── dia/
│   ├── checkin/
│   ├── configuracoes/
│   │   ├── index.html
│   │   ├── pix.html
│   │   └── backup_pendrive.html
│   ├── mala_direta/
│   ├── financeiro/
│   ├── biblioteca/
│   ├── doacoes/
│   └── caixa/
│       ├── pdv.html
│       ├── qrcode.html
│       ├── vendas.html
│       ├── produtos.html
│       ├── produto_form.html
│       ├── caixas.html
│       └── comprovante.html
├── static/
│   ├── css/base.css
│   ├── js/cep.js
│   └── logos/                       # Logos do centro (upload via configurações)
└── .venv/                           # Virtual environment
```

---

## Estado do projeto (2026-05-18)

**Em produção. Publicado no GitHub (GPL-3.0).**

### Funcionalidades concluídas
- ✅ Cadastros completos (pessoas, médiuns, usuários, trabalhadores)
- ✅ Dia de trabalho completo (abertura → check-in → filas → chamada → encerramento)
- ✅ Dashboard estilo planilha com Passe, Reiki, Acolhimento e colunas por médium
- ✅ Painel "Última chamada" com WebSocket
- ✅ Validação de senha duplicada e acompanhantes
- ✅ Agendamentos automáticos e planos de tratamento
- ✅ Agenda global com agendamento avulso e paginação por mês
- ✅ Relatórios completos
- ✅ Tela de chamada WebSocket em monitor dedicado
- ✅ Dias de atendimento configuráveis
- ✅ Gestão de trabalhadores com presença (TASK_001)
- ✅ Códigos de 4 dígitos e carteirinhas com QR imprimíveis (TASK_002)
- ✅ Terminal de presença fixo na entrada: QR lido por câmera e digitação (TASK_003)
- ✅ Gestão do dia, correções retroativas auditadas e relatórios de presença (TASK_004)
- ✅ PDV com preços e totais calculados no servidor; divergência de preço revisada sem escrita (TASK_006 / FIN-01)
- ✅ Cancelamento de venda com reversão rastreável dos efeitos comprováveis, idempotência pelo banco e prova durável; casos ambíguos bloqueados sem efeito (TASK_007 / FIN-02)
- ✅ Caixa por eventos: vendas, recebimentos e dinheiro físico separados; PIX/fiado não recebido/crédito consumido fora da gaveta; destino explícito e fechamento com conferência física (TASK_008 / FIN-04)
- ✅ Revisão técnica do financeiro da TASK_008 (TASK_009) e correções revalidadas (TASK_010): contrato de ambiente com arquivo de configuração autoritativo + token de execução provando o banco efetivo antes de qualquer DDL/seed/startup; `moeda_nao_negativa` permite abrir/fechar caixa com R$ 0,00 sem lançamento fictício; PDV sem sessão opera PIX (espécie segue condicionada à sessão aberta). Complemento da devolução do revisor dentro da TASK_010: prova nomeada de fechar × venda × recebimento × cancelamento no mesmo `FOR UPDATE` (CASO 8b), reenvio da mesma tentativa no escopo testado (recebimento de mensalidade e cancelamento convergem por identidade documental; ausência de chave HTTP no PDV documentada) (CASO 16), roteiro do dono coerente com a regra de divergência (justificativa registra e preserva o esperado) e evidência canônica commitável `docs/tasks/evidencias/TASK_010/runner_completo_final.txt` (árvore `d12dbfc`, token, rc e contagens por suíte). TASK_010 **encerrada com ressalvas** (revisor): TASK_008 tecnicamente aprovada, produção sem autorização de publicação; duplicação por reenvio cego no PDV continua risco conhecido. **Duas pendências distintas**: homologação manual do dono (roteiro fictício) e decisão explícita do dono sobre o legado — quanto a ele, estado observado: vendas e sangrias continuam permitidas em sessões legadas pela implementação atual; a manutenção ou alteração desse comportamento aguarda decisão explícita do dono (a linha l.306 da TASK_008 é descrição do executor, não autorização demonstrada)
- ✅ Mala direta via WhatsApp e email
- ✅ Financeiro: movimentações, mensalidades, PIX, caixa_id
- ✅ Biblioteca: acervo, ISBN, OpenLibrary, empréstimos, vendas
- ✅ Doações: cestas básicas com rastreamento
- ✅ Capitalização inteligente de nomes
- ✅ Banco PostgreSQL com pool de conexões
- ✅ RBAC: grupos e permissões Unix (ler/escrever/apagar) por módulo
- ✅ Configurações: SMTP, backup remoto, nome/logo do centro
- ✅ Backup em pendrive/HD externo (configurável via web)
- ✅ **Caixa/PDV completo** (carrinho, espécie, PIX QR Code, segundo monitor WebSocket)
- ✅ Comprovante imprimível (layout 80mm para impressora térmica)
- ✅ Gestão de chaves PIX (múltiplas, vinculadas a caixa)
- ✅ Excluir pessoa mesmo com check-in registrado

### Pendências conhecidas
- 🔧 `chaves_pix` e `caixas.chave_pix_id` não estão em `banco.py` (tabela existe mas não é criada automaticamente na instalação limpa)
- ✅ Impressora Elgin i9 (térmica ESC/POS) em `/dev/ttyUSB0` — endpoint `POST /caixa/vendas/{id}/imprimir` escreve bytes ESC/POS direto no dispositivo (negrito, alinhamento, total em tamanho duplo, corte parcial)
- 📧 Implementar envio de emails via SMTP (configuração já existe)
- 🔐 Logs de auditoria para movimentações financeiras
- 📊 Expandir relatórios com filtros de período
- 📱 PWA para acesso mobile
