#!/bin/bash
# ============================================================
#  atualizar.sh — Atualização do Sistema Shamballa
#  Deve ser executado NO Raspberry Pi a partir do pendrive.
#
#  Uso:
#    bash atualizar.sh shamballa-YYYYMMDD-HHMM.tar.gz
#
#  O arquivo .tar.gz deve estar na mesma pasta que este script.
# ============================================================
set -e

DESTINO="/opt/shamballa"
SERVICO="shamballa"
DIR_SCRIPT="$(cd "$(dirname "$0")" && pwd)"
PACOTE="${1:-}"

# ── Cabeçalho ─────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Atualização — Sistema Shamballa    ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Validações ────────────────────────────────────────────────────────────────

# Se não foi passado o nome do pacote, tentar encontrar automaticamente
if [ -z "$PACOTE" ]; then
    PACOTE=$(ls "$DIR_SCRIPT"/shamballa-*.tar.gz 2>/dev/null | sort | tail -n1)
    if [ -z "$PACOTE" ]; then
        echo "  ERRO: Nenhum arquivo shamballa-*.tar.gz encontrado."
        echo "  Uso: bash atualizar.sh shamballa-YYYYMMDD-HHMM.tar.gz"
        exit 1
    fi
    PACOTE=$(basename "$PACOTE")
fi

PACOTE_PATH="$DIR_SCRIPT/$PACOTE"

if [ ! -f "$PACOTE_PATH" ]; then
    echo "  ERRO: Arquivo não encontrado: $PACOTE_PATH"
    exit 1
fi

if [ ! -d "$DESTINO" ]; then
    echo "  ERRO: Diretório do sistema não encontrado: $DESTINO"
    echo "  Certifique-se de que o Shamballa está instalado."
    exit 1
fi

echo "  Pacote  : $PACOTE"
echo "  Destino : $DESTINO"
echo "  Data    : $(date '+%d/%m/%Y %H:%M')"
echo ""

# ── 1. Parar o serviço ────────────────────────────────────────────────────────
echo "[1/5] Parando o serviço..."
if sudo systemctl is-active --quiet "$SERVICO" 2>/dev/null; then
    sudo systemctl stop "$SERVICO"
    echo "      Serviço parado."
else
    echo "      (serviço já estava parado)"
fi

# ── 2. Fazer backup do banco atual ────────────────────────────────────────────
echo ""
echo "[2/5] Fazendo backup do banco de dados..."
BACKUP_DB="$DESTINO/shamballa-pre-atualizacao-$(date +%Y%m%d-%H%M).db"
if [ -f "$DESTINO/shamballa.db" ]; then
    cp "$DESTINO/shamballa.db" "$BACKUP_DB"
    echo "      Backup: $BACKUP_DB"
else
    echo "      (banco não encontrado, pulando backup)"
fi

# ── 3. Extrair e copiar arquivos ──────────────────────────────────────────────
echo ""
echo "[3/5] Copiando arquivos atualizados..."
TMPDIR=$(mktemp -d)
trap "rm -rf '$TMPDIR'" EXIT

tar -xzf "$PACOTE_PATH" -C "$TMPDIR"

# O tar.gz contém uma pasta 'shamballa/' dentro
FONTE="$TMPDIR/shamballa"

if [ ! -d "$FONTE" ]; then
    echo "  ERRO: Estrutura inesperada no pacote. Pasta 'shamballa/' não encontrada."
    exit 1
fi

# Copiar arquivos de código (preserva shamballa.db e .venv)
rsync -a --delete \
    --exclude='.venv' \
    --exclude='shamballa.db' \
    --exclude='*.db' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    "$FONTE/" "$DESTINO/"

echo "      Arquivos copiados com sucesso."

# ── 4. Atualizar banco de dados (migrações) ───────────────────────────────────
echo ""
echo "[4/5] Atualizando banco de dados..."
cd "$DESTINO"
"$DESTINO/.venv/bin/python" -c "
from banco import criar_tabelas
criar_tabelas()
print('      Banco de dados atualizado.')
"

# ── 5. Reiniciar o serviço ────────────────────────────────────────────────────
echo ""
echo "[5/5] Iniciando o serviço..."
sudo systemctl start "$SERVICO"
sleep 3

STATUS=$(sudo systemctl is-active "$SERVICO" 2>/dev/null || echo "inativo")

echo ""
if [ "$STATUS" = "active" ]; then
    IP=$(hostname -I | awk '{print $1}')
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║   Atualização concluída com sucesso!                 ║"
    echo "╚══════════════════════════════════════════════════════╝"
    echo ""
    echo "  Sistema disponível em: http://${IP}:8000"
    echo ""
    echo "  Novidades nesta versão:"
    echo "   • Botão 'Salvar + Check-in' no cadastro de pessoas"
    echo "   • Registro manual de falta na agenda do plano"
    echo "   • Máscara de telefone nos formulários"
    echo "   • Fila do médium: lista de agendados sem check-in"
    echo "   • Agenda: botão de check-in direto"
    echo "   • Acolhimento: cor diferente ao chamar"
    echo "   • Transferência de pessoas entre médiuns"
    echo "   • Reagendar desloca todas as sessões seguintes"
    echo "   • Configurações: dias de atendimento configuráveis"
    echo "   • Mala Direta: envio por e-mail e WhatsApp"
    echo "   • Cadastro de Trabalhadores com dias de atuação"
    echo "   • Check-in de trabalhadores (presença/falta)"
    echo "   • Relatórios de presença por período e dia da semana"
    echo ""
else
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║   ATENÇÃO: o serviço não iniciou corretamente.      ║"
    echo "╚══════════════════════════════════════════════════════╝"
    echo ""
    echo "  Veja os logs de erro:"
    echo "    sudo journalctl -u $SERVICO -n 40"
    echo ""
    echo "  Para restaurar o banco anterior, se necessário:"
    echo "    sudo systemctl stop $SERVICO"
    echo "    cp $BACKUP_DB $DESTINO/shamballa.db"
    echo "    sudo systemctl start $SERVICO"
    echo ""
    sudo journalctl -u "$SERVICO" -n 20 --no-pager
    exit 1
fi
