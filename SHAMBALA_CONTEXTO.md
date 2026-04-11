# Projeto Shambala — Contexto Completo
*Atualizado em 2026-04-09*
## Git

repositório git: git@leal:/home/git/projetos/shambala/
 
---

## O que é

Sistema de gestão de atendimentos para a **Casa Espírita Shambala**, em Volta Redonda/RJ.
Substitui uma planilha Excel e um display com controle remoto problemático.

O Centro recebe pessoas alguns dias por semana. Elas chegam, fazem check-in na recepção,
recebem **cartões físicos reutilizáveis** com um código numérico, aguardam em um auditório
assistindo a uma palestra, e são chamadas pelo código exibido num monitor via WebSocket.

---

## Infraestrutura

- **Servidor:** Raspberry Pi 2 (1 GB RAM)
- **Serviço:** systemd (`shamballa.service`), inicia automático no boot
- **Porta:** 8000, acessível em toda a rede local (`--host 0.0.0.0`)
- **Monitor de chamada:** segundo Raspberry Pi conectado ao monitor do auditório, abre `/chamada` no navegador em tela cheia (F11)
- **Banco de dados:** PostgreSQL 12+ (substitui SQLite)
  - Host/porta/credenciais via variáveis de ambiente (`.env`)
  - Pool de conexões: 1-10 conexões simultâneas
  - Extensão `unaccent` para buscas normalizadas com acentos
- **Backups:** estratégia adaptada para PostgreSQL (migrado de backup SQLite local)

---

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3 + FastAPI |
| Banco | PostgreSQL 12+ (psycopg2, sem ORM) |
| Pool de conexões | SimpleConnectionPool (1-10 conns) |
| Templates | Jinja2 (server-side rendering) |
| Frontend | HTML + CSS + JS puro (sem framework) |
| Tempo real | WebSocket (tela de chamada) |
| Serviço | systemd |

**Idioma do código:** Português em tudo — variáveis, funções, rotas, tabelas, campos.

**Variáveis de ambiente (.env):**
```
SHAMBALA_DB_HOST=localhost
SHAMBALA_DB_PORT=5432
SHAMBALA_DB_NAME=shambala
SHAMBALA_DB_USER=shambala
SHAMBALA_DB_PASS=sua_senha_aqui
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
| Atendimento em grupo | Um único código para toda a família/grupo; acompanhantes são vinculados pelo check-in |
| Plano de tratamento | Contador de sessões + agendamentos automáticos por frequência |
| Plano avulso | Criado automaticamente ao agendar avulso na Agenda; sem geração de sessões futuras |
| Sessões com passe | Configurável por plano (primeiras N sessões exigem passe, 0=nunca, -1=sempre) |
| Vagas por médium | Cada médium tem um limite de atendimentos por dia (`vagas_dia`, padrão 10) |
| Falta | 3 faltas consecutivas → plano cancelado automaticamente |
| Prioridade na fila | Idosos 70+, deficientes, prioridade manual (flag na pessoa) |
| Senha única no dia | Não se pode reutilizar uma senha já em uso por outra pessoa no mesmo dia |
| Pessoa + passe no mesmo dia | Sim — recebe dois cartões |
| Controle de acesso | Todos os atendentes acessam tudo (sem níveis de permissão) |
| Relatórios | Apenas visualização na tela, sem exportação |

---

## Modelo de dados (PostgreSQL)

### `pessoas`
```
id, nome_apresentacao (NOT NULL), nome_completo, telefone, email
cep, logradouro, numero, complemento, bairro, cidade, uf
data_nascimento (YYYY-MM-DD), deficiencia (0/1), prioridade (0/1)
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
ativo (0/1, padrão 1), vagas_dia (padrão 10)
```

### `atendentes`
```
id, nome_usuario (UNIQUE NOT NULL), nome_completo (NOT NULL)
senha_hash (SHA-256), telefone, email, ativo (padrão 1)
```
Atendente inicial criado automaticamente: **admin / admin**

### `dias_trabalho`
```
id, data (UNIQUE), aberto (0/1)
```

### `mediuns_dia`
```
id, dia_trabalho_id (FK), medium_id (FK)  — UNIQUE(dia_trabalho_id, medium_id)
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
codigo_passe (nullable), codigo_acolhimento (nullable)
codigo_atendimento (nullable), codigo_reiki (nullable)
plano_id (FK nullable), medium_id (FK nullable), agendamento_id (FK nullable)
passe_realizado (0/1), acolhimento_realizado (0/1)
atendimento_realizado (0/1), reiki_realizado (0/1)
acolhimento_chamado (0/1)
```

### `dias_atendimento`
```
dia_semana (PRIMARY KEY, 0-6: seg-dom), descricao (TEXT)
```
Configurável via `/configuracoes`. Padrão: segunda (0) e quarta (2).

### `trabalhadores`
```
id, nome_completo (NOT NULL), telefone, email
ativo (0/1, padrão 1), created_at (YYYY-MM-DD)
```

### `trabalhador_dias`
```
id, trabalhador_id (FK), dia_semana (0-6)
UNIQUE(trabalhador_id, dia_semana)
```

### `trabalhador_presenca`
```
id, trabalhador_id (FK), dia_trabalho_id (FK)
presente (0/1), hora_chegada (nullable), hora_saida (nullable)
```

### `grupos` e `grupos_permissoes`
```
grupos:
  id, nome (UNIQUE NOT NULL), descricao

grupos_permissoes:
  id, grupo_id (FK), modulo (TEXT NOT NULL)
  UNIQUE(grupo_id, modulo)
```
Preparação para RBAC (controle de permissões por módulo).

### `configuracoes_smtp`
```
id, chave (UNIQUE NOT NULL), valor (NOT NULL DEFAULT '')
```
Armazenamento de configurações de email para mala direta.

---

## Módulos Adicionais (Fase 2)

### 📊 Financeiro (`/financeiro`)

Gestão de receitas, despesas, mensalidades e movimentações.

#### Tabelas
**`financeiro_movimentacoes`**
```
id, tipo (entrada/saida), categoria (mensalidade/doacao/livro/etc)
valor (NUMERIC 10,2), data_movimentacao (TEXT YYYY-MM-DD)
descricao (TEXT), trabalhador_id (FK nullable), pessoa_id (FK nullable)
pix_copiadecola (TEXT), status (pago/pendente/cancelado, padrão pago)
```

#### Rotas
```
GET       /financeiro                   ← dashboard com resumo mensal
GET/POST  /financeiro/nova              ← registrar movimento
GET       /financeiro/mensalidades      ← controle de mensalidades
POST      /financeiro/{id}/pagar        ← marcar como pago
POST      /financeiro/{id}/cancelar     ← cancelar movimento
GET       /financeiro/relatorio         ← relatório customizado
```

#### Funcionalidades
- Dashboard mensal (entradas, saídas, pendentes)
- Registro de movimentações com tipo/categoria
- Suporte a PIX (copia e cola)
- Filtros por período, status, tipo, pessoa/trabalhador
- Histórico completo rastreável

---

### 📚 Biblioteca (`/biblioteca`)

Gestão de acervo de livros, empréstimos e vendas.

#### Tabelas
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

#### Rotas
```
GET       /biblioteca                   ← lista de livros com busca
GET/POST  /biblioteca/livro/novo        ← cadastro novo livro (com busca ISBN)
GET       /biblioteca/livro/{id}/editar ← edição de livro
POST      /biblioteca/livro/{id}/apagar ← remover livro
GET       /biblioteca/emprestimo        ← registro de empréstimos
POST      /biblioteca/emprestimo/{id}/devolver ← marcação de devolução
GET       /biblioteca/venda             ← histórico de vendas
POST      /biblioteca/venda/nova        ← registrar venda
GET       /biblioteca/relatorio         ← relatório (acervo, empréstimos, vendas)
```

#### Funcionalidades
- Cadastro de livros (ISBN, título, autor, editora, ano, edição)
- Integração com **OpenLibrary API** para auto-preenchimento por ISBN
- Controle de quantidade (total − emprestados = disponível)
- Registro de empréstimos e devoluções com histórico
- Rastreamento de vendas com valor total
- Relatórios de acervo, empréstimos ativos, vendas por período
- Busca por título, autor, ISBN

---

### 🛒 Doações — Cesta Básica (`/doacoes`)

Gestão de distribuição de cestas básicas para famílias em dificuldade.

#### Tabelas
**`doacoes_cestas`**
```
id, pessoa_id (FK NOT NULL), data_entrega (TEXT NOT NULL)
itens (TEXT descrição do conteúdo), observacao (TEXT)
entregue (INTEGER NOT NULL DEFAULT 0) — flag de conclusão
```

#### Rotas
```
GET       /doacoes                      ← lista de doações com filtros
GET/POST  /doacoes/nova                 ← registro de nova cesta
POST      /doacoes/{id}/entregar        ← marcar como entregue
POST      /doacoes/{id}/desentrega      ← reverter entrega
GET/POST  /doacoes/{id}/editar          ← edição de doação
POST      /doacoes/{id}/apagar          ← remover registro
GET       /doacoes/relatorio            ← relatório de distribuição
```

#### Funcionalidades
- Registro de cesta básica por pessoa
- Validação de endereço completo da pessoa
- Rastreamento de entrega (data, status)
- Descrição de itens e observações
- Filtros por período, status de entrega, pessoa
- Relatório de distribuição (total, entregues, pendentes)
- Vinculação automática com pessoa cadastrada

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
GET       /cadastros/pessoas/{id}              ← ficha completa da pessoa (DEVE ser a ÚLTIMA rota)
GET       /cadastros/pessoas/buscar            ← JSON autocomplete (retorna até 10 pessoas)

GET       /cadastros/mediuns
GET/POST  /cadastros/mediuns/novo
GET/POST  /cadastros/mediuns/{id}/editar
POST      /cadastros/mediuns/{id}/toggle-ativo
GET       /cadastros/mediuns/{id}/planos
POST      /cadastros/mediuns/{id}/planos/novo
POST      /cadastros/mediuns/{id}/planos/{plano_id}/alta
POST      /cadastros/mediuns/{id}/planos/{plano_id}/cancelar
GET       /cadastros/mediuns/{id}/planos/{plano_id}/agenda
POST      /cadastros/mediuns/{id}/planos/{plano_id}/agenda/novo
POST      /cadastros/mediuns/{id}/planos/{plano_id}/agenda/{ag_id}/cancelar
POST      /cadastros/mediuns/{id}/planos/{plano_id}/agenda/{ag_id}/reagendar

GET       /cadastros/atendentes
GET/POST  /cadastros/atendentes/novo
GET/POST  /cadastros/atendentes/{id}/editar
POST      /cadastros/atendentes/{id}/toggle-ativo

GET       /cadastros/trabalhadores       ← NOVO: gestão de voluntários/trabalhadores
GET/POST  /cadastros/trabalhadores/novo
GET/POST  /cadastros/trabalhadores/{id}/editar
POST      /cadastros/trabalhadores/{id}/toggle-ativo
GET       /cadastros/trabalhadores/{id}/dias
POST      /cadastros/trabalhadores/{id}/dias/{dia_semana}/adicionar
POST      /cadastros/trabalhadores/{id}/dias/{dia_semana}/remover
GET       /cadastros/trabalhadores/checkin   ← registrar presença do dia
GET       /cadastros/trabalhadores/agenda    ← agenda de presença

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
GET       /dia/lista                    ← lista genérica por tipo (passe/reiki/acolhimento/atendimento)
GET       /dia/passe                    ← fila de passe
POST      /dia/passe/{id}/chamar
POST      /dia/passe/{id}/realizado
POST      /dia/passe/{id}/desfazer
GET       /dia/reiki                    ← fila de Reiki
POST      /dia/reiki/{id}/chamar
POST      /dia/reiki/{id}/realizado
POST      /dia/reiki/{id}/desfazer
GET       /dia/acolhimento              ← fila de acolhimento fraterno
POST      /dia/acolhimento/{id}/chamar
GET       /dia/mediuns/{medium_id}      ← fila de atendimento de um médium
POST      /dia/atendimento/{id}/chamar
POST      /dia/atendimento/{id}/realizado
POST      /dia/atendimento/{id}/desfazer
GET/POST  /dia/fraterno/{checkin_id}    ← criar plano a partir do acolhimento

GET       /agenda                       ← agenda geral por médium e período
POST      /agenda/novo                  ← cria plano avulso + agendamento

GET       /chamada                      ← tela fullscreen WebSocket (monitor do auditório)
GET       /chamada/ultimo               ← JSON {"codigo": "..."} último código chamado
WS        /chamada/ws

GET       /configuracoes                ← NOVO: configurar dias de atendimento
POST      /configuracoes/dias/adicionar
POST      /configuracoes/dias/{dia_semana}/remover

GET       /mala-direta                  ← NOVO: envio de mensagens em massa
GET       /mala-direta/resultado        ← resultado de envio via WhatsApp/email

GET       /relatorios
GET       /relatorios/por-dia
GET       /relatorios/por-pessoa
GET       /relatorios/por-medium        ← filtrável por dia da semana
GET       /relatorios/frequencia

GET       /financeiro                   ← NOVO: dashboard financeiro
GET/POST  /financeiro/nova              ← nova movimentação
GET       /financeiro/mensalidades      ← mensalidades de trabalhadores
POST      /financeiro/{id}/pagar
POST      /financeiro/{id}/cancelar

GET       /biblioteca                   ← NOVO: acervo de livros
GET/POST  /biblioteca/livro/novo        ← novo livro (com busca ISBN)
GET/POST  /biblioteca/livro/{id}/editar
POST      /biblioteca/livro/{id}/apagar
GET       /biblioteca/emprestimo        ← histórico de empréstimos
POST      /biblioteca/emprestimo/{id}/devolver
GET       /biblioteca/venda             ← histórico de vendas
POST      /biblioteca/venda/nova
GET       /biblioteca/relatorio

GET       /doacoes                      ← NOVO: cesta básica
GET/POST  /doacoes/nova                 ← nova doação/cesta
POST      /doacoes/{id}/entregar        ← marcar entregue
POST      /doacoes/{id}/desentrega      ← reverter entrega
GET/POST  /doacoes/{id}/editar
POST      /doacoes/{id}/apagar
GET       /doacoes/relatorio
```

---

## Funcionalidades notáveis

### Dashboard (`/dia/dashboard`)
Tela estilo planilha com uma coluna por fila: **Passe | Reiki | Acolhimento | Médium 1 | Médium 2...**
- Código em destaque, primeiro nome, ícones de prioridade (⚑ ♿ ★)
- **Botão 📢 Chamar** — canto inferior esquerdo de cada item
- **Botão ✓ Realizado** — canto superior direito, com `confirm()` antes de executar
- Coluna Acolhimento **não tem** botão Realizado (só via form fraterno)
- Painel "Última chamada" acima das colunas, centralizado, não ocupa largura total
  - Busca último código em `/chamada/ultimo` ao carregar (sem animação)
  - Anima com `@keyframes entrada-mini` e troca de cor a cada nova chamada via WebSocket
- Auto-atualização a cada 30 segundos (pausada se cursor sobre botão)
- Botões de atalho no topo: **+ Check-in**, **+ Pessoa**, **📅 Agenda**, **← Painel**
- Realizado em atendimento propagado para todos com o mesmo `codigo_atendimento` no dia
- Fila de atendimento deduplica por `codigo_atendimento` (mostra só o titular — `MIN(id)`)

### Prioridade na fila
Expressão SQL `_ORDEM_PRIORIDADE` em `rotas/dia.py`:
- `p.prioridade` — flag manual
- `p.deficiencia` — flag de deficiência
- Cálculo de idade ≥ 70 a partir de `p.data_nascimento`
- Ordem secundária: `c.hora_checkin ASC` (chegada)

### Fluxo do check-in
1. Atendente busca pessoa por nome (normalização de acentos via `norm()`)
2. Sistema mostra situação: agendamento hoje, plano ativo, vagas do médium, sugestão de fraterno
3. Atendente digita senhas (passe, reiki, acolhimento, atendimento) conforme aplicável
4. **Validação de senha duplicada:** `_senhas_em_uso()` impede reutilizar senha já em uso por outra pessoa no mesmo dia
5. **Acompanhantes:** busca por nome (autocomplete) — recebem o mesmo `codigo_atendimento`
6. Se houver agendamento, é vinculado automaticamente

### Fluxo do acolhimento fraterno
1. Pessoa aparece na fila de acolhimento
2. Atendente abre `/dia/fraterno/{id}`
3. Vê histórico de planos anteriores e lista de médiuns ativos
4. Cria plano → gera agendamentos automáticos
5. Marca acolhimento como realizado

### Agendamentos
- **Automáticos:** `gerar_agendamentos_plano()` em `banco.py` — semanal (7d), quinzenal (14d), mensal (28d), avulso (sem geração). `_proxima_data_trabalho()` pula para segunda (0) ou quarta (2) mais próxima.
- **Avulso pela Agenda:** `POST /agenda/novo` recebe `pessoa_id` + `medium_id` + `data`, cria automaticamente um plano `avulso` (1 sessão) e o agendamento. Permite agendar **qualquer pessoa**, não apenas as que já têm plano com o médium.

### Ficha da pessoa (`/cadastros/pessoas/{id}`)
- Dados pessoais, laços familiares, histórico de planos e histórico completo de check-ins
- **ATENÇÃO:** a rota `/{id}` deve ser declarada por **último** em `rotas/pessoas.py` — antes ficam `/buscar`, `/novo`, `/{id}/editar`, `/{id}/lacos` — para evitar conflito com FastAPI interpretando "novo" como inteiro

### Backup
`backup.py` — estratégia adaptada para PostgreSQL (a ser revisada):
- **Antes (SQLite):** copia arquivo `.db` local + `.tar.gz` em pendrive
- **Agora (PostgreSQL):** necessário usar `pg_dump` para fazer dump SQL do banco
- Recomendação: implementar backup automático via `pg_dump` ou ferramentas de replicação PostgreSQL
- Script de backup deve considerar:
  - Dump do banco: `pg_dump -h SHAMBALA_DB_HOST -U SHAMBALA_DB_USER shambala`
  - Armazenamento em local seguro (local + pendrive)
  - Rotina diária ou via systemd timer
  - Compressão (gzip) para economizar espaço

### Restauração a partir do backup
```bash
# Restaurar banco PostgreSQL do dump
sudo systemctl stop shamballa

# Opcional: drop banco antigo
psql -U postgres -c "DROP DATABASE IF EXISTS shambala;"
psql -U postgres -c "CREATE DATABASE shambala OWNER shambala;"

# Restaurar do dump
psql -h localhost -U shambala shambala < backup-YYYY-MM-DD.sql

sudo systemctl start shamballa

# Verificar se funcionou
psql -h localhost -U shambala -d shambala -c "SELECT COUNT(*) FROM pessoas;"
```

---

## Arquivos-chave

| Arquivo | Função |
|---|---|
| `banco.py` | `conectar()`, `criar_tabelas()`, `_migrar()`, `gerar_agendamentos_plano()`, `_dias_atendimento()` |
| `main.py` | FastAPI app, lifespan, monta rotas, filtro Jinja2 `data_br` |
| `templates_config.py` | Instância única de `Jinja2Templates` (importada por todas as rotas) |
| `rotas/auth.py` | Login/logout, sessões em memória `_sessoes`, `obter_atendente_logado()` |
| `rotas/dia.py` | Painel, dashboard, filas (passe/reiki/acolhimento/atendimento), fraterno |
| `rotas/checkin.py` | Busca, form check-in, `_situacao_pessoa()`, `_senhas_em_uso()` |
| `rotas/pessoas.py` | CRUD pessoas, busca JSON (`/buscar`), laços, ficha — `/{id}` deve ser a última rota; `_capitalizar_nome()` para formatação de nomes |
| `rotas/mediuns.py` | CRUD médiuns + planos de tratamento + agenda do plano |
| `rotas/agenda.py` | Agenda global; `/agenda/novo` cria plano avulso automaticamente |
| `rotas/relatorios.py` | Por dia, por médium (filtrável por dia da semana), por pessoa, frequência |
| `rotas/chamada.py` | WebSocket, `gerenciador.transmitir(codigo)`, `ultimo_codigo`, `/chamada/ultimo` |
| `rotas/configuracoes.py` | CRUD de `dias_atendimento` (dias de funcionamento do Centro) |
| `rotas/mala_direta.py` | Envio de mensagens em massa via WhatsApp e email |
| `rotas/trabalhadores.py` | CRUD trabalhadores/voluntários, dias de funcionamento, registro de presença |
| `rotas/financeiro.py` | NOVO: dashboard financeiro, movimentações, mensalidades |
| `rotas/biblioteca.py` | NOVO: acervo de livros, empréstimos, vendas — integra com OpenLibrary API |
| `rotas/doacoes.py` | NOVO: gestão de cestas básicas e doações |
| `rotas/permissoes.py` | Controle de permissões por módulo (preparação para RBAC) |
| `backup.py` | `fazer_backup()` — adaptado para PostgreSQL |
| `maiusculas.py` | Script de manutenção: capitaliza nomes respeitando partículas |
| `normalizar_telefones.py` | Script de manutenção: padroniza telefones para `(XX) XXXXX-XXXX` |
| `static/css/base.css` | Único arquivo CSS, variáveis CSS, todos os componentes |
| `static/js/cep.js` | Auto-preenchimento de endereço via ViaCEP |

---

## Convenções de código

- Código 100% em português (variáveis, rotas, funções, tabelas, comentários)
- **Nomes de pessoas:** capitalização inteligente com `_capitalizar_nome()` (partículas `a, o, e, da, do, de, das, dos, d` em minúsculas)
- Autenticação: sessão em memória (`dict[str, int]`) via cookie `httponly; samesite=lax`
- Sem JWT, sem banco de sessões
- Permissões: preparação para RBAC (tabelas `grupos` e `grupos_permissoes` criadas)
- Proteção de rota: `_guard(request)` retorna `(atendente, None)` ou `(None, RedirectResponse)`

### Banco de dados (PostgreSQL)
- Driver: `psycopg2` com pool de conexões (1-10)
- Context manager: sempre `with conectar() as conn:` — faz commit/rollback automático
- Cursor factory: `RealDictCursor` — acesso por nome de coluna
- Wrapper `_ConnCompat`: compatibilidade com interface sqlite3 (minimiza mudanças nas rotas)
- Função SQL `norm(texto)` criada via extensão `unaccent` — normaliza acentos para buscas
- Variáveis de ambiente: `.env` com `SHAMBALA_DB_*`

### Formatação de dados
- Datas armazenadas como `TEXT` no formato `YYYY-MM-DD`; exibidas via filtro Jinja2 `data_br`
- Campo de data nos formulários: `<input type="text">` com máscara JS `DD/MM/AAAA`; `_parse_data()` converte antes de salvar
- Telefones no formato `(XX) XXXXX-XXXX`; máscara JS de entrada; normalização via script `normalizar_telefones.py`
- Valores monetários: `NUMERIC(10,2)` para precisão decimal (Financeiro, Biblioteca)

---

## Demandas registradas (pós-testes reais)

Arquivo: `AJUSTES-POS-TESTE.md`

Requisitos levantados após o primeiro teste com público:

1. ✅ **Listagem dos atendidos** — mostrar quem já foi atendido por médium
2. ✅ **Reabrir atendimento** — desfazer "realizado" acidentalmente
3. ✅ **Histórico de check-ins** — na ficha da pessoa, mostrar todos os atendimentos
4. ✅ **Dashboard — botões distanciados** — evitar click acidental (realizado vs chamar)
5. ✅ **Fila de Reiki** — implementada no dashboard
6. ✅ **Acolhimento protegido** — não marcável como realizado direto (só via form fraterno)
7. ✅ **Painel "Última chamada"** — mini-monitor no dashboard mostrando último chamado
8. ✅ **Data de nascimento com máscara** — entrada DD/MM/AAAA, não seletor
9. ✅ **Acompanhantes** — autorizar múltiplas pessoas em um atendimento (mesmo código)
10. ✅ **Estatísticas discriminadas** — clicar em cada quadro do painel exibe a listagem
11. 🔄 **Relatórios por dia semana** — discriminar segunda × quarta
12. ✅ **Renomear "código" → "senha"** — em toda a UI (em progresso)
13. ✅ **Backup completo** — não só `.db`, mas também `.tar.gz` com código
14. ✅ **Agendamento avulso** — pela tela de agenda (sem plano regular)
15. 🔄 **Controle de presença de trabalhadores** — implementado (tabelas), UI em desenvolviment

---

## Deploy e operação

### Rodar em desenvolvimento
```bash
cd ~/projetos/shamballa
.venv/bin/uvicorn main:app --reload --host 0.0.0.0
# Acesso: http://localhost:8000
```

### Comandos úteis no Raspberry (produção)
```bash
sudo systemctl status shamballa     # ver status do serviço
sudo systemctl restart shamballa    # reiniciar serviço
sudo journalctl -u shamballa -f     # ver logs ao vivo
sudo systemctl stop shamballa       # parar serviço
sudo systemctl start shamballa      # iniciar serviço

# Acesso ao banco PostgreSQL
psql -h localhost -U shambala -d shambala -c "SELECT COUNT(*) FROM pessoas;"

# Backup do PostgreSQL
pg_dump -h localhost -U shambala shambala > backup-$(date +%Y-%m-%d).sql

# Restaurar backup
psql -h localhost -U shambala shambala < backup-YYYY-MM-DD.sql
```

### Debugging em desenvolvimento
```bash
cd ~/projetos/shamballa
.venv/bin/uvicorn main:app --reload --host 0.0.0.0

# Com logs detalhados
.venv/bin/uvicorn main:app --reload --host 0.0.0.0 --log-level debug
```

### Troubleshooting comum

| Problema | Causa | Solução |
|---|---|---|
| 404 em rota `/cadastros/pessoas/123` | Rota `/{id}` não está por última | Reorganizar rotas em `pessoas.py` |
| Nomes aparecem errados | Nome não foi capitalizado | Rodar `maiusculas.py --aplicar` |
| Telefones com formato ruim | Não normalizados | Rodar `normalizar_telefones.py --aplicar` |
| Dashboard vazio | Dia não foi aberto | Acessar `/dia` e abrir dia de trabalho |
| Última chamada não aparece | WebSocket desconectado | Recarregar `/chamada` no monitor |
| Erro ao conectar banco | PostgreSQL offline | Verificar: `sudo systemctl status postgresql`, variáveis `.env` |
| Erro: "relation does not exist" | Tabelas não criadas | Rodar `python3 banco.py` para criar esquema |
| Erro ao encerrar dia | Backup falhando | Verificar espaço em disco, permissões, conectividade PostgreSQL |

---

## Estrutura de diretórios

```
shamballa/
├── main.py                          # App FastAPI principal
├── banco.py                         # Banco SQLite + funções utilitárias
├── templates_config.py              # Instância Jinja2 centralizada
├── backup.py                        # Backup automático
├── maiusculas.py                    # Script: capitaliza nomes
├── normalizar_telefones.py          # Script: padroniza telefones
├── modelos.py                       # (vazio/reservado para tipos futuros)
├── rotas/
│   ├── __init__.py
│   ├── auth.py                      # /login, /logout, sessões
│   ├── pessoas.py                   # /cadastros/pessoas
│   ├── mediuns.py                   # /cadastros/mediuns
│   ├── atendentes.py                # /cadastros/atendentes
│   ├── trabalhadores.py             # /cadastros/trabalhadores
│   ├── dia.py                       # /dia — painel, dashboard, filas
│   ├── checkin.py                   # /dia/checkin — entrada de pessoas
│   ├── chamada.py                   # /chamada — WebSocket, tela monitor
│   ├── agenda.py                    # /agenda — agenda geral
│   ├── relatorios.py                # /relatorios
│   ├── configuracoes.py             # /configuracoes — dias de atendimento
│   ├── mala_direta.py               # /mala-direta — WhatsApp + email
│   ├── financeiro.py                # /financeiro — movimentações, mensalidades
│   ├── biblioteca.py                # /biblioteca — acervo, empréstimos, vendas
│   ├── doacoes.py                   # /doacoes — cestas básicas
│   └── permissoes.py                # permissões por módulo (RBAC)
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── menu.html
│   ├── chamada.html
│   ├── imprimir_agenda.html
│   ├── pessoas/
│   │   ├── lista.html
│   │   ├── form.html
│   │   ├── lacos.html
│   │   └── ficha.html
│   ├── mediuns/
│   │   ├── lista.html
│   │   ├── form.html
│   │   ├── planos.html
│   │   └── agenda.html
│   ├── atendentes/
│   │   ├── lista.html
│   │   └── form.html
│   ├── trabalhadores/
│   │   ├── lista.html
│   │   ├── checkin.html
│   │   └── agenda.html
│   ├── dia/
│   │   ├── painel.html
│   │   ├── dashboard.html
│   │   ├── abrir.html
│   │   ├── encerrado.html
│   │   ├── passe.html
│   │   ├── reiki.html
│   │   ├── acolhimento.html
│   │   ├── atendimento.html
│   │   ├── lista.html
│   │   └── fraterno.html
│   ├── checkin/
│   │   ├── busca.html
│   │   └── form.html
│   ├── configuracoes/
│   │   └── index.html
│   ├── mala_direta/
│   │   ├── index.html
│   │   └── resultado.html
│   ├── financeiro/
│   │   ├── dashboard.html
│   │   ├── form.html
│   │   └── mensalidades.html
│   ├── biblioteca/
│   │   ├── livros.html
│   │   ├── livro_form.html
│   │   ├── emprestimos.html
│   │   ├── vendas.html
│   │   └── relatorio.html
│   └── doacoes/
│       ├── lista.html
│       ├── form.html
│       └── relatorio.html
├── static/
│   ├── css/base.css
│   └── js/cep.js
├── .env                          # Credenciais PostgreSQL (NÃO commitar)
├── .env.example                  # Template para .env
├── shamballa.service             # Systemd service
├── .venv/                        # Virtual environment
└── SHAMBALLA_CONTEXTO.md         # Este arquivo
```

---

## Endereços importantes (produção)

| O quê | Endereço |
|---|---|
| Sistema (recepção) | `http://<IP-DO-PI-SERVIDOR>:8000` |
| Dashboard | `http://<IP-DO-PI-SERVIDOR>:8000/dia/dashboard` |
| Tela de chamada | `http://<IP-DO-PI-SERVIDOR>:8000/chamada` |
| Login padrão | admin / admin |

A tela de chamada fica num **segundo Raspberry Pi** dedicado, conectado ao monitor do auditório,
abrindo `/chamada` em tela cheia (F11). Não precisa de login.

---

## Pontos importantes para nova implementação

### Ordem das rotas (FastAPI)
⚠️ **Em `rotas/pessoas.py`:** a rota `GET /cadastros/pessoas/{id}` (ficha) **DEVE ser declarada por último**.
Caso contrário, FastAPI interpreta "novo", "buscar", "editar", "lacos" como inteiros e falha.

Ordem correta:
1. `/buscar` (JSON, específica)
2. `/novo` (GET form, POST submit)
3. `/{id}/editar`
4. `/{id}/lacos`
5. `/{id}` ← **ÚLTIMA** (ficha completa)

### Templates com lógica condicional
- Templates recebem contexto com `atendente` (dict com id/nome)
- Uso de `{% if %}`, `{% for %}`, `{% block %}` para renderização
- Filtro `data_br` converte `YYYY-MM-DD` → `DD/MM/AAAA`

Exemplo:
```html
{% extends "base.html" %}
{% block content %}
  <h1>{{ titulo }}</h1>
  {% for item in items %}
    <p>{{ item.nome }} — {{ item.data|data_br }}</p>
  {% endfor %}
{% endblock %}
```

### Validação de entrada
- Nomes: `_capitalizar_nome()` em pessoas.py (aplica automaticamente)
- Datas: máscara JS `DD/MM/AAAA`, função Python `_parse_data()` converte antes de salvar
- Telefones: máscara JS `(XX) XXXXX-XXXX`, normalização via script
- Senhas: validação de unicidade por dia via `_senhas_em_uso()`

### WebSocket (tela de chamada)
```python
# Em rotas/chamada.py
gerenciador = GerenciadorChamada()
gerenciador.transmitir(codigo)        # envia para todos os clientes WebSocket
gerenciador.ultimo_codigo             # guarda o último código chamado
```

Endpoint `/chamada/ultimo` retorna JSON: `{"codigo": "12345"}`

### Transações PostgreSQL
Sempre usar context manager:
```python
with conectar() as conn:
    conn.execute(
        "INSERT INTO pessoas (nome_apresentacao) VALUES (%s)",
        ("José da Silva",)  # Note: parâmetros como tupla
    )
    # commit/rollback automático ao sair do bloco
```

**Nunca** fazer commits manuais — o context manager cuida disso.

**Importante:** psycopg2 usa `%s` para parâmetros (não `?` do SQLite). Parâmetros sempre como tupla/lista.

### Geração de agendamentos
```python
gerar_agendamentos_plano(
    conn=conn,
    plano_id=5,
    inicio=date.today(),
    frequencia="semanal",    # semanal, quinzenal, mensal, avulso
    total=12,                # número de sessões
    sessoes_com_passe=3      # -1=sempre, 0=nunca, N=primeiras N
)
```

Usa `_dias_atendimento()` para pular fins de semana e dias sem atendimento.

---

## Estado do projeto (2026-04-09)

**Em produção com Fase 2 de expansão.**

- **Fase 1 (SQLite):** Atendimentos, Filas, Dashboard, Agendamentos — CONCLUÍDO ✅
- **Fase 2 (PostgreSQL):** Financeiro, Biblioteca, Doações — IMPLEMENTADO ✅

Banco de dados migrado de SQLite para PostgreSQL com sucesso. Todos os módulos funcionais.

### Funcionalidades Fase 1 — Atendimentos (✅ Concluído)
- ✅ Cadastros (pessoas, médiuns, atendentes, trabalhadores) com fichas completas
- ✅ Campo data nascimento: entrada DD/MM/AAAA com máscara JS
- ✅ Laços familiares (informativo)
- ✅ Dia de trabalho completo (abertura → check-in → filas → chamada → encerramento)
- ✅ Dashboard estilo planilha com Passe, Reiki, Acolhimento e colunas por médium
- ✅ Painel "Última chamada" centralizado acima das colunas, com animação e persistência via WebSocket
- ✅ Validação de senha duplicada no check-in (`_senhas_em_uso`)
- ✅ Acompanhantes no check-in via autocomplete (não por select)
- ✅ Deduplicação de atendimento no dashboard (mostra só titular; realizado propaga para todos)
- ✅ Botão "Desfazer" realizado em passe, reiki e atendimento
- ✅ Agendamentos automáticos e controle de planos de tratamento
- ✅ Agenda global com agendamento avulso para qualquer pessoa (cria plano avulso automaticamente)
- ✅ Relatórios (por dia, por médium com filtro por dia da semana, por pessoa, frequência)
- ✅ Tela de chamada WebSocket em monitor dedicado
- ✅ Dias de atendimento configuráveis via `/configuracoes` (não hardcoded)
- ✅ Gestão de trabalhadores/voluntários com registro de presença
- ✅ Mala direta via WhatsApp e email
- ✅ Capitalização inteligente de nomes (respeita partículas)

### Funcionalidades Fase 2 — Financeiro, Biblioteca, Doações (✅ Implementado)
- ✅ Migração SQLite → PostgreSQL com pool de conexões
- ✅ **Financeiro:** Dashboard de movimentações, entrada/saída, mensalidades, suporte a PIX
- ✅ **Biblioteca:** Acervo com ISBN, integração OpenLibrary API, controle de empréstimos e devoluções
- ✅ **Biblioteca:** Histórico de vendas de livros
- ✅ **Doações:** Gestão de cestas básicas com rastreamento de entrega
- ✅ **Permissões:** Tabelas de RBAC preparadas (grupos, grupos_permissoes)
- ✅ **Configurações SMTP:** Tabela para armazenar configurações de email
- ✅ **Trabalhadores expandidos:** CPF, RG, data nascimento, endereço completo, valor mensalidade

### Melhorias pós-testes (Fase 1)
- Renomeação de "código" → "senha" em toda UI (ajustado em AJUSTES-POS-TESTE.md)
- Dashboard: botões **Chamar** e **Realizado** distanciados (inferior esquerdo × superior direito)
- Filas de Reiki funcionais (listadas no dashboard junto com Passe)
- Acolhimento com form dedicado (não marcável direto como realizado)
- Painel "Última chamada" no dashboard (mini-monitor do auditório)
- Histórico de check-ins na ficha da pessoa
- Scripts de manutenção (`maiusculas.py`, `normalizar_telefones.py`) com modo simulação

### Próximos passos recomendados
- ⚠️ Implementar backup automático com `pg_dump` (substitui backup SQLite)
- 🔄 Completar implementação de RBAC (usar tabelas `grupos` e `grupos_permissoes`)
- 📧 Implementar envio de emails via configurações SMTP
- 📊 Expandir relatórios com filtros de data e período configurável
- 🔐 Adicionar logs de auditoria para movimentações financeiras

## Scripts de manutenção

### `maiusculas.py`
```bash
# Simulação: mostra quais nomes serão alterados
python3 maiusculas.py

# Aplicação: capitaliza nomes respeitando partículas
python3 maiusculas.py --aplicar
```
Converte nomes com inteligência: "jose da silva" → "Jose da Silva"

### `normalizar_telefones.py`
```bash
# Simulação: mostra as alterações
python3 normalizar_telefones.py

# Aplicação: padroniza para (XX) XXXXX-XXXX
python3 normalizar_telefones.py --aplicar
```
Adiciona DDD 24 automaticamente se faltando. Marca anomalias (9, 12+ dígitos) para revisão manual.
