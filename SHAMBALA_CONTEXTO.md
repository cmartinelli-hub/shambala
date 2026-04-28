# Projeto Shambala — Contexto Completo
*Atualizado em 2026-04-12*

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
- **Banco de dados:** PostgreSQL 12+
  - Host/porta/credenciais via variáveis de ambiente (`.env`)
  - Pool de conexões: 1-10 conexões simultâneas
  - Extensão `unaccent` para buscas normalizadas com acentos
- **Backups:** `pg_dump` comprimido com envio opcional via SCP

---

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12+ + FastAPI |
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
| Falta | 3 faltas consecutivas → plano cancelado automaticamente |
| Prioridade na fila | Idosos 70+, deficientes, prioridade manual (flag na pessoa) |
| Senha única no dia | Não se pode reutilizar uma senha já em uso por outra pessoa no mesmo dia |
| Pessoa + passe no mesmo dia | Sim — recebe dois cartões |
| Controle de acesso | Sessão em memória, cookie httponly+samesite=lax |
| Login padrão | admin / admin (trocar após primeiro acesso) |

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

### `usuarios` (atendentes)
```
id, nome_usuario (UNIQUE NOT NULL), nome_completo (NOT NULL)
senha_hash (SHA-256), telefone, email, ativo (padrão 1)
```
Atendente inicial criado automaticamente: **admin / admin**

> A rota `/cadastros/atendentes` redireciona para `/cadastros/usuarios` (compatibilidade).

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
id, nome_completo (NOT NULL), telefone, email, cpf, rg, data_nascimento
endereco completo (cep, logradouro, numero, complemento, bairro, cidade, uf)
valor_mensalidade (NUMERIC 10,2)
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

## Módulo Financeiro (`/financeiro`)

### Tabela `financeiro_movimentacoes`
```
id, tipo (entrada/saida), categoria (mensalidade/doacao/livro/etc)
valor (NUMERIC 10,2), data_movimentacao (TEXT YYYY-MM-DD)
descricao (TEXT), trabalhador_id (FK nullable), pessoa_id (FK nullable)
pix_copiadecola (TEXT), status (pago/pendente/cancelado, padrão pago)
```

### Rotas
```
GET       /financeiro                   ← dashboard com resumo mensal
GET/POST  /financeiro/nova              ← registrar movimento
GET       /financeiro/mensalidades      ← controle de mensalidades
POST      /financeiro/{id}/pagar        ← marcar como pago
POST      /financeiro/{id}/cancelar     ← cancelar movimento
```

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

### Tabela `doacoes_cestas`
```
id, pessoa_id (FK NOT NULL), data_entrega (TEXT NOT NULL)
itens (TEXT descrição do conteúdo), observacao (TEXT)
entregue (INTEGER NOT NULL DEFAULT 0)
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

GET       /cadastros/trabalhadores             ← gestão de voluntários
GET/POST  /cadastros/trabalhadores/novo
GET/POST  /cadastros/trabalhadores/{id}/editar
POST      /cadastros/trabalhadores/{id}/toggle-ativo
GET       /cadastros/trabalhadores/{id}/dias
GET       /cadastros/trabalhadores/checkin     ← registrar presença
GET       /cadastros/trabalhadores/agenda      ← agenda de presença

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

GET       /chamada                      ← tela fullscreen WebSocket
GET       /chamada/ultimo               ← JSON {"codigo": "..."}
WS        /chamada/ws

GET       /configuracoes                ← configurar dias de atendimento

GET       /mala-direta                  ← envio de mensagens em massa
GET       /mala-direta/resultado        ← resultado de envio

GET       /relatorios
GET       /relatorios/por-dia
GET       /relatorios/por-pessoa
GET       /relatorios/por-medium
GET       /relatorios/frequencia

GET       /financeiro                   ← dashboard financeiro
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

GET       /permissoes                   ← controle de permissões por grupo
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

---

## Arquivos-chave

| Arquivo | Função |
|---|---|
| `main.py` | App FastAPI, lifespan, monta todas as rotas, filtro `data_br` |
| `banco.py` | `conectar()`, `criar_tabelas()`, `_migrar()`, pool psycopg2, wrapper compatibilidade |
| `templates_config.py` | Instância única de `Jinja2Templates` |
| `backup.py` | Backup via `pg_dump` + envio SCP opcional |
| `maiusculas.py` | Capitalização inteligente de nomes |
| `normalizar_telefones.py` | Padronização de telefones para `(XX) XXXXX-XXXX` |
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
| `rotas/usuarios.py` | Gestão de usuários/atendentes |
| `rotas/dia.py` | Painel, dashboard, todas as filas, fraterno |
| `rotas/checkin.py` | Busca, form check-in, `_situacao_pessoa()`, `_senhas_em_uso()` |
| `rotas/chamada.py` | WebSocket, transmissão de códigos |
| `rotas/agenda.py` | Agenda global, criação de plano avulso |
| `rotas/relatorios.py` | Por dia, médium, pessoa, frequência |
| `rotas/configuracoes.py` | Dias de atendimento |
| `rotas/trabalhadores.py` | Voluntários, presença, agenda |
| `rotas/financeiro.py` | Movimentações, mensalidades, PIX |
| `rotas/biblioteca.py` | Acervo, ISBN/OpenLibrary, empréstimos, vendas |
| `rotas/doacoes.py` | Cestas básicas, rastreamento |
| `rotas/mala_direta.py` | Envio em massa WhatsApp/email |
| `rotas/permissoes.py` | Controle RBAC (preparação) |

---

## Convenções de código

- Código 100% em português (variáveis, rotas, funções, tabelas, comentários)
- **Nomes:** capitalização inteligente com `_capitalizar_nome()` (partículas em minúsculas)
- **Autenticação:** sessão em memória (`dict[str, int]`) via cookie `httponly; samesite=lax`
- **Sem JWT, sem banco de sessões**
- **Banco:** `psycopg2` com pool, context manager `with conectar() as conn:`, `RealDictCursor`
- **Parâmetros SQL:** `%s` (psycopg2), nunca `?` (SQLite)
- **Datas:** armazenadas como `TEXT` `YYYY-MM-DD`; exibidas via filtro Jinja2 `data_br`
- **Telefones:** formato `(XX) XXXXX-XXXX`

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
cd ~/shambala
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
gunzip < backup-2026-04-12.sql.gz | psql -h localhost -U shambala shambala
```

### Atualização

```bash
cd ~/shambala
git pull
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
| Nomes aparecem errados | Não capitalizados | Rodar `maiusculas.py --aplicar` |
| Dashboard vazio | Dia não foi aberto | Acessar `/dia` e abrir dia de trabalho |
| Última chamada não aparece | WebSocket desconectado | Recarregar `/chamada` |
| Erro ao conectar banco | PostgreSQL offline | `sudo systemctl status postgresql` |
| "relation does not exist" | Tabelas não criadas | Rodar `python3 banco.py` |
| Erro ao encerrar dia | Backup falhando | Verificar espaço em disco, permissões |

---

## Endereços importantes (produção)

| O quê | Endereço |
|---|---|
| Sistema (recepção) | `http://<IP-DO-SERVIDOR>:8000` |
| Dashboard | `http://<IP-DO-SERVIDOR>:8000/dia/dashboard` |
| Tela de chamada | `http://<IP-DO-SERVIDOR>:8000/chamada` |
| Login padrão | admin / admin |

A tela de chamada fica num **dispositivo dedicado** conectado ao monitor do auditório,
abrindo `/chamada` em tela cheia (F11). Não precisa de login.

---

## Estrutura de diretórios

```
shambala/
├── main.py                          # App FastAPI principal
├── banco.py                         # Conexões PostgreSQL, tabelas, migrações
├── templates_config.py              # Instância Jinja2 centralizada
├── backup.py                        # Backup via pg_dump
├── maiusculas.py                    # Script: capitaliza nomes
├── normalizar_telefones.py          # Script: padroniza telefones
├── requirements.txt                 # Dependências Python
├── instalar.sh                      # Script de instalação
├── atualizar.sh                     # Script de atualização
├── abrir-chamada.sh                 # Abre tela de chamada no navegador
├── gerar_pacote.sh                  # Gera pacote para transferência
├── .env.example                     # Template de configuração
├── .gitignore                       # Arquivos ignorados pelo git
├── shamballa.service                # Systemd service (USUARIO substituído na instalação)
├── rotas/
│   ├── __init__.py
│   ├── auth.py                      # /login, /logout, sessões
│   ├── pessoas.py                   # /cadastros/pessoas
│   ├── mediuns.py                   # /cadastros/mediuns
│   ├── usuarios.py                  # /cadastros/usuarios (atendentes)
│   ├── trabalhadores.py             # /cadastros/trabalhadores
│   ├── dia.py                       # /dia — painel, dashboard, filas
│   ├── checkin.py                   # /dia/checkin — entrada
│   ├── chamada.py                   # /chamada — WebSocket
│   ├── agenda.py                    # /agenda
│   ├── relatorios.py                # /relatorios
│   ├── configuracoes.py             # /configuracoes
│   ├── mala_direta.py               # /mala-direta
│   ├── financeiro.py                # /financeiro
│   ├── biblioteca.py                # /biblioteca
│   ├── doacoes.py                   # /doacoes
│   └── permissoes.py                # /permissoes (RBAC)
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
│   ├── mala_direta/
│   ├── financeiro/
│   ├── biblioteca/
│   └── doacoes/
├── static/
│   ├── css/base.css
│   ├── js/cep.js
│   └── logos/
└── .venv/                           # Virtual environment (após instalação)
```

---

## Estado do projeto (2026-04-12)

**Em produção. Publicado no GitHub (GPL-3.0).**

### Funcionalidades concluídas
- ✅ Cadastros completos (pessoas, médiuns, usuários, trabalhadores)
- ✅ Dia de trabalho completo (abertura → check-in → filas → chamada → encerramento)
- ✅ Dashboard estilo planilha com Passe, Reiki, Acolhimento e colunas por médium
- ✅ Painel "Última chamada" com WebSocket
- ✅ Validação de senha duplicada e acompanhantes
- ✅ Agendamentos automáticos e planos de tratamento
- ✅ Agenda global com agendamento avulso
- ✅ Relatórios completos
- ✅ Tela de chamada WebSocket em monitor dedicado
- ✅ Dias de atendimento configuráveis
- ✅ Gestão de trabalhadores com presença
- ✅ Mala direta via WhatsApp e email
- ✅ Financeiro: movimentações, mensalidades, PIX
- ✅ Biblioteca: acervo, ISBN, OpenLibrary, empréstimos, vendas
- ✅ Doações: cestas básicas com rastreamento
- ✅ Capitalização inteligente de nomes
- ✅ Banco PostgreSQL com pool de conexões

### Próximos passos recomendados
- 🔄 Completar RBAC (usar tabelas `grupos` e `grupos_permissoes`)
- 📧 Implementar envio de emails via SMTP
- 📊 Expandir relatórios com filtros de período
- 🔐 Logs de auditoria para movimentações financeiras
- 📱 PWA para acesso mobile
