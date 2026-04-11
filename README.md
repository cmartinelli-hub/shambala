# Shambala v2.1 - Casa Espírita Management System

Sistema web para gerenciar operações de Casa Espírita com PostgreSQL, FastAPI e Jinja2.

## 🚀 Quick Start

### Local
```bash
cd projetos/shamballa
.venv/bin/pip install -r requirements.txt
.venv/bin/python3 -m uvicorn main:app --reload --port 8000
```

### Produção (192.168.15.73:8000)
```bash
ssh root@192.168.15.73
systemctl status shambala
systemctl restart shambala
sudo journalctl -u shambala -f
```

## 🔐 Credenciais Padrão
- **Usuário:** admin
- **Senha:** admin
- **DB Password:** Supremacia@735

## 📊 Banco de Dados
- **Motor:** PostgreSQL 16
- **User:** shambala
- **DB:** shambala
- **Schema:** schema.sql

## 📁 Estrutura

```
shamballa/
├── main.py              # FastAPI app
├── banco.py             # PostgreSQL conexões e tabelas
├── requirements.txt     # Dependências
├── rotas/               # Endpoints (auth, usuarios, doacoes, etc)
├── templates/           # Jinja2 HTML
├── static/              # CSS, JS, SVG
├── schema.sql           # Schema PostgreSQL + seed data
└── SHAMBALA_CONTEXTO.md # Especificações do projeto
```

## 🔑 Funcionalidades

✅ Autenticação (sessão em cookie httponly)
✅ Permissões por grupos (Admin, Atendentes, etc)
✅ Menu separado para Administração (Admins only)
✅ Cadastros: Pessoas, Médiuns, Trabalhadores, Usuários
✅ Agenda, Relatórios, Financeiro, Biblioteca, Doações
✅ Migração SQLite → PostgreSQL completa

## 🔧 Configuração

Criar `.env`:
```
SHAMBALA_DB_HOST=localhost
SHAMBALA_DB_PORT=5432
SHAMBALA_DB_NAME=shambala
SHAMBALA_DB_USER=shambala
SHAMBALA_DB_PASS=Supremacia@735
```

## 📦 Dependências Principais

- fastapi==0.135.1
- uvicorn==0.41.0
- psycopg2-binary==2.9.10
- python-dotenv==1.0.1
- Jinja2==3.1.6

## 🐛 Troubleshooting

**Erro: "atendente is undefined"**
→ Variável `atendente` não passada para template

**Erro: "duplicate key violates unique constraint"**
→ Resetar sequência: `SELECT setval('atendentes_id_seq', 9);`

**Erro: ImportError dotenv**
→ `pip install python-dotenv`

## 📝 Git

- **Repositório próprio:** git@leal:/home/git/projetos/shambala/
- **Branch:** master
- **Últimos commits:** Menu Admin, limpeza, PostgreSQL fixes

## 📞 Contato

Desenvolvido com Claude Code
Documentação: SHAMBALA_CONTEXTO.md
