#!/bin/bash
set -e

VERSAO=$(date +%Y%m%d)
PACOTE="shamballa-$VERSAO.tar.gz"

echo "[...] Gerando pacote $PACOTE"

tar -czf "../$PACOTE" \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='shamballa.db' \
    --exclude='*.tar.gz' \
    --exclude='.git' \
    --exclude='SHAMBALLA_CONTEXTO.md' \
    --exclude='SHAMBALLA.pdf' \
    --exclude='SHAMBALLA.txt' \
    --exclude='Star_of_David.svg' \
    --exclude='modelos.py' \
    -C .. shamballa

echo "[OK] Pacote gerado: $(realpath ../$PACOTE)"
echo "     Tamanho: $(du -sh ../$PACOTE | cut -f1)"
