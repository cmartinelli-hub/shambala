#!/bin/bash
# ============================================================
#  preparar_pendrive.sh
#  Roda na máquina de desenvolvimento.
#  Gera um pacote completo e copia para o pendrive.
#
#  Uso:
#    bash preparar_pendrive.sh [/caminho/do/pendrive]
#
#  Se o caminho não for informado, o pacote é criado em ../pendrive-shamballa/
# ============================================================
set -e

DESTINO_BASE="${1:-../pendrive-shamballa}"
VERSAO=$(date +%Y%m%d-%H%M)
PACOTE="shamballa-$VERSAO.tar.gz"
DIR_SCRIPT="$(cd "$(dirname "$0")" && pwd)"

echo "╔══════════════════════════════════════╗"
echo "║   Preparar Pendrive — Shamballa      ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  Versão  : $VERSAO"
echo "  Destino : $DESTINO_BASE"
echo ""

# ── 1. Gerar o pacote .tar.gz do código ──────────────────────────────────────
echo "[1/3] Gerando pacote de código..."
cd "$DIR_SCRIPT"

tar -czf "/tmp/$PACOTE" \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='shamballa.db' \
    --exclude='*.db' \
    --exclude='*.tar.gz' \
    --exclude='.git' \
    --exclude='SHAMBALLA_CONTEXTO.md' \
    --exclude='SHAMBALLA.pdf' \
    --exclude='SHAMBALLA.txt' \
    --exclude='Star_of_David.svg' \
    --exclude='modelos.py' \
    --exclude='AJUSTES-POS-TESTE.md' \
    --exclude='pendrive-shamballa' \
    -C .. shamballa

echo "    Pacote: /tmp/$PACOTE  ($(du -sh /tmp/$PACOTE | cut -f1))"

# ── 2. Preparar o destino (pendrive) ─────────────────────────────────────────
echo ""
echo "[2/3] Preparando destino..."
mkdir -p "$DESTINO_BASE"

cp "/tmp/$PACOTE" "$DESTINO_BASE/$PACOTE"
cp "$DIR_SCRIPT/atualizar.sh" "$DESTINO_BASE/atualizar.sh"
chmod +x "$DESTINO_BASE/atualizar.sh"

# Gerar um arquivo README rápido
cat > "$DESTINO_BASE/LEIA-ME.txt" << EOF
=== Atualização Shamballa — $VERSAO ===

COMO ATUALIZAR NO RASPBERRY PI:
----------------------------------------
1. Conecte este pendrive no Raspberry Pi
2. Abra um terminal e localize o pendrive:
     ls /media/\$(whoami)/

3. Execute (substitua NOME_PENDRIVE):
     cd /media/\$(whoami)/NOME_PENDRIVE
     bash atualizar.sh $PACOTE

4. Aguarde a mensagem "Atualização concluída!"

Em caso de erro, veja os logs:
     sudo journalctl -u shamballa -n 30

EOF

echo "    Conteúdo do pendrive:"
ls -lh "$DESTINO_BASE/"

# ── 3. Limpeza e resumo ───────────────────────────────────────────────────────
echo ""
echo "[3/3] Limpando temporários..."
rm -f "/tmp/$PACOTE"

DESTINO_ABS="$(realpath "$DESTINO_BASE")"
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   Pendrive pronto!                               ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "  Pasta: $DESTINO_ABS"
echo ""
echo "  Copie o CONTEÚDO desta pasta para o pendrive."
echo "  Se o pendrive já estiver montado, passe o caminho:"
echo ""
echo "    bash preparar_pendrive.sh /media/\$(whoami)/NOME_PENDRIVE"
echo ""
