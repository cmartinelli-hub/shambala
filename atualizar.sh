#!/bin/bash
set -e

DESTINO="/opt/shamballa"

echo "============================================"
echo "  Atualizador do Sistema Shambala"
echo "============================================"
echo ""

# ── Verificar se esta no diretorio correto ──────────────────────────────────
if [ ! -f "main.py" ]; then
    echo "[ERRO] Execute este script a partir do diretorio do projeto Shambala."
    exit 1
fi

# ── Parar servico ───────────────────────────────────────────────────────────
echo "[...] Parando servico shamballa"
sudo systemctl stop shamballa 2>/dev/null || true

# ── Copiar arquivos atualizados ─────────────────────────────────────────────
echo "[...] Copiando arquivos atualizados para $DESTINO"
for item in main.py banco.py templates_config.py backup.py requirements.txt \
            maiusculas.py normalizar_telefones.py instalar.sh atualizar.sh \
            abrir-chamada.sh gerar_pacote.sh \
            rotas templates static shamballa.service .env.example .gitignore \
            README.md; do
    if [ -e "$item" ]; then
        rsync -a --delete "$item" "$DESTINO/" 2>/dev/null || cp -r "$item" "$DESTINO/"
    fi
done

# ── Atualizar dependencias ──────────────────────────────────────────────────
echo "[...] Verificando dependencias Python"
"$DESTINO/.venv/bin/pip" install --upgrade pip --quiet
"$DESTINO/.venv/bin/pip" install -r "$DESTINO/requirements.txt" --quiet

# ── Reiniciar servico ───────────────────────────────────────────────────────
echo "[...] Reiniciando servico shamballa"
sudo systemctl daemon-reload
sudo systemctl start shamballa

# ── Verificar status ────────────────────────────────────────────────────────
sleep 2
if sudo systemctl is-active --quiet shamballa.service; then
    echo ""
    echo "============================================"
    echo "  Atualizacao concluida com sucesso!"
    echo "============================================"
else
    echo ""
    echo "============================================"
    echo "  ATENCAO: O servico nao iniciou."
    echo "============================================"
    echo "  Verifique: sudo journalctl -u shamballa -n 50"
fi
