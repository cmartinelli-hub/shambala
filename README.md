# 🕊️ Shambala — Sistema de Gestão para Centro Espírita

Sistema web completo para gestão de atendimentos, filas, agendamentos, financeiro, biblioteca e doações — desenvolvido para a **Casa Espírita Shambala**, Volta Redonda/RJ.

## 📋 Funcionalidades

### Atendimentos
- **Check-in** na recepção com busca por nome
- **Filas de atendimento**: Passe, Reiki, Acolhimento Fraterno e Mediúnico
- **Dashboard** estilo planilha com todas as filas em tempo real
- **Chamada** por código numérico exibido em monitor via WebSocket
- **Planos de tratamento** com agendamentos automáticos (semanal, quinzenal, mensal)
- **Agenda global** por médium e período

### Cadastros
- Pessoas com ficha completa, laços familiares e histórico
- Médiuns com planos de tratamento e vagas por dia
- Trabalhadores/voluntários com controle de presença

### Módulos Adicionais
- **💰 Financeiro** — receitas, despesas, mensalidades, suporte PIX
- **📚 Biblioteca** — acervo com ISBN, empréstimos, vendas, integração OpenLibrary
- **🛒 Doações** — gestão de cestas básicas com rastreamento de entrega
- **📧 Mala Direta** — envio de mensagens em massa via WhatsApp e email
- **📊 Relatórios** — por dia, por médium, por pessoa, frequência

## 🏗️ Arquitetura

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12+ com FastAPI |
| Banco de Dados | PostgreSQL 12+ (sem ORM) |
| Templates | Jinja2 (server-side rendering) |
| Frontend | HTML + CSS + JavaScript puro |
| Tempo Real | WebSocket (tela de chamada) |
| Serviço | systemd |

## 📸 Telas

| Recepção | Dashboard | Chamada |
|---|---|---|
| Check-in de pessoas | Todas as filas em colunas | Monitor do auditório |

## 🚀 Instalação no Debian

### Pré-requisitos

- Debian 12 (Bookworm) ou superior
- Acesso root ou sudo
- Conexão com internet

### 1. Instalar dependências do sistema

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip postgresql postgresql-client
```

### 2. Configurar o PostgreSQL

```bash
# Iniciar o PostgreSQL
sudo systemctl enable postgresql
sudo systemctl start postgresql

# Criar usuário e banco de dados
sudo -u postgres psql <<EOF
CREATE USER shambala WITH PASSWORD 'sua_senha_aqui';
CREATE DATABASE shambala OWNER shambala;
GRANT ALL PRIVILEGES ON DATABASE shambala TO shambala;
\c shambala
CREATE EXTENSION IF NOT EXISTS unaccent;
ALTER USER shambala CREATEDB;
EOF
```

> **Importante:** Troque `sua_senha_aqui` por uma senha forte.

### 3. Clonar o repositório

```bash
git clone https://github.com/cmartinelli-hub/shambala.git
cd shambala
```

### 4. Criar ambiente virtual e instalar dependências

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

### 5. Configurar variáveis de ambiente

```bash
cp .env.example .env
nano .env
```

Edite o arquivo `.env` com as credenciais do PostgreSQL:

```ini
SHAMBALA_DB_HOST=localhost
SHAMBALA_DB_PORT=5432
SHAMBALA_DB_NAME=shambala
SHAMBALA_DB_USER=shambala
SHAMBALA_DB_PASS=sua_senha_aqui

# Backup remoto (opcional)
SHAMBALA_BACKUP_REMOTE_HOST=
SHAMBALA_BACKUP_REMOTE_USER=
SHAMBALA_BACKUP_REMOTE_PATH=
```

### 6. Testar a instalação

```bash
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Acesse `http://localhost:8000` no navegador. O login padrão é:

- **Usuário:** `admin`
- **Senha:** `admin`

> ⚠️ Troque a senha padrão imediatamente após o primeiro acesso.

### 7. Criar serviço systemd

O projeto inclui o arquivo `shamballa.service`. Instale com o script automatizado:

```bash
sudo bash instalar.sh
```

Ou manualmente:

```bash
USUARIO=$(whoami)
sudo sed "s/USUARIO/$USUARIO/g" shamballa.service | sudo tee /etc/systemd/system/shamballa.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable shamballa
sudo systemctl start shamballa
```

#### Verificar o serviço

```bash
sudo systemctl status shamballa     # status
sudo journalctl -u shamballa -f     # logs ao vivo
sudo systemctl restart shamballa    # reiniciar
```

### 8. Acessar na rede local

O sistema ficará disponível em:

- **Local:** `http://localhost:8000`
- **Rede:** `http://<IP-DO-SERVIDOR>:8000`
- **Tela de chamada:** `http://<IP-DO-SERVIDOR>:8000/chamada`

## 🖥️ Tela de Chamada (Auditório)

A tela de chamada roda em um navegador dedicado (pode ser outro computador), exibindo os códigos chamados em tempo real via WebSocket.

1. Abra `http://<IP-DO-SERVIDOR>:8000/chamada`
2. Pressione **F11** para tela cheia
3. Não requer login

## 🔧 Operação

### Backup do banco de dados

```bash
# Backup manual
pg_dump -h localhost -U shambala shambala | gzip > shambala-$(date +%Y-%m-%d).sql.gz

# Restaurar
gunzip < shambala-2026-04-12.sql.gz | psql -h localhost -U shambala shambala
```

### Manutenção de dados

```bash
# Capitalizar nomes (respeita partículas)
.venv/bin/python maiusculas.py           # simulação
.venv/bin/python maiusculas.py --aplicar # aplicar

# Normalizar telefones para (XX) XXXXX-XXXX
.venv/bin/python normalizar_telefones.py           # simulação
.venv/bin/python normalizar_telefones.py --aplicar # aplicar
```

### Logs

```bash
# Logs do serviço
sudo journalctl -u shamballa --since today

# Logs em tempo real
sudo journalctl -u shamballa -f
```

### Atualização do sistema

```bash
cd ~/shambala
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart shamballa
```

## 📁 Estrutura do Projeto

```
shambala/
├── main.py                  # App FastAPI
├── banco.py                 # Conexões PostgreSQL, tabelas, migrações
├── backup.py                # Backup via pg_dump
├── requirements.txt         # Dependências Python
├── instalar.sh              # Script de instalação automatizada
├── atualizar.sh             # Script de atualização
├── .env.example             # Template de configuração
├── shamballa.service        # Serviço systemd
├── rotas/                   # Módulos de rotas
│   ├── auth.py              # Login/logout/sessões
│   ├── pessoas.py           # Cadastro de pessoas
│   ├── mediuns.py           # Cadastro de médiuns e planos
│   ├── usuarios.py          # Gestão de usuários/atendentes
│   ├── dia.py               # Operações do dia, dashboard, filas
│   ├── checkin.py           # Check-in na recepção
│   ├── chamada.py           # Tela de chamada (WebSocket)
│   ├── agenda.py            # Agenda global
│   ├── relatorios.py        # Relatórios diversos
│   ├── configuracoes.py     # Dias de atendimento
│   ├── trabalhadores.py     # Voluntários e presença
│   ├── financeiro.py        # Receitas, despesas, mensalidades
│   ├── biblioteca.py        # Acervo, empréstimos, vendas
│   ├── doacoes.py           # Cestas básicas
│   ├── mala_direta.py       # Envio em massa
│   └── permissoes.py        # Controle de permissões (RBAC)
├── templates/               # Templates Jinja2
└── static/                  # CSS, JS, imagens
```

## 🔐 Segurança

- Senhas hash com SHA-256
- Sessões em memória com cookie `httponly; samesite=lax`
- Variáveis de ambiente em `.env` (nunca commitar)
- `.gitignore` protege arquivos sensíveis

## 📄 Licença

Este projeto é licenciado sob a [GNU General Public License v3.0](LICENSE).

## 👤 Autor

Desenvolvido para a Casa Espírita Shambala — Volta Redonda, RJ, Brasil.
