#!/bin/bash
set -e

DESTINO="/opt/shamballa"
SERVICO="shamballa.service"
USUARIO=$(whoami)

echo "============================================"
echo "  Instalador do Sistema Shambala"
echo "  Casa Espirita Shambala - Volta Redonda/RJ"
echo "============================================"
echo ""

# ── Verificar root ──────────────────────────────────────────────────────────
if [ "$EUID" -eq 0 ]; then
    echo "[ERRO] Nao execute este script como root."
    echo "Execute como usuario normal: bash instalar.sh"
    exit 1
fi

# ── Verificar Python 3 ──────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "[ERRO] Python 3 nao encontrado."
    echo "Instale com: sudo apt install python3 python3-venv"
    exit 1
fi

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[OK] Python $PYTHON_VER encontrado"

# ── Verificar PostgreSQL ────────────────────────────────────────────────────
if ! command -v psql &>/dev/null; then
    echo "[ERRO] PostgreSQL nao encontrado."
    echo "Instale com: sudo apt install postgresql postgresql-client"
    exit 1
fi

PSQL_VER=$(psql --version | awk '{print $3}')
echo "[OK] PostgreSQL $PSQL_VER encontrado"

# ── Verificar se PostgreSQL esta rodando ─────────────────────────────────────
if ! pg_isready -q 2>/dev/null; then
    echo "[!] PostgreSQL nao esta rodando. Tentando iniciar..."
    sudo systemctl start postgresql 2>/dev/null || true
    sleep 2
    if ! pg_isready -q 2>/dev/null; then
        echo "[AVISO] Nao foi possível verificar o PostgreSQL. Verifique manualmente."
    else
        echo "[OK] PostgreSQL esta rodando"
    fi
fi

# ── Criar diretorio de instalacao ───────────────────────────────────────────
echo ""
echo "[...] Criando diretorio $DESTINO"
sudo mkdir -p "$DESTINO"
sudo chown "$USUARIO:$USUARIO" "$DESTINO"

# ── Copiar arquivos da aplicacao ────────────────────────────────────────────
echo "[...] Copiando arquivos"
for item in main.py banco.py templates_config.py backup.py requirements.txt \
            maiusculas.py normalizar_telefones.py instalar.sh atualizar.sh \
            abrir-chamada.sh gerar_pacote.sh \
            rotas templates static shamballa.service .env.example .gitignore \
            README.md; do
    if [ -e "$item" ]; then
        cp -r "$item" "$DESTINO/"
    fi
done

# ── Criar ambiente virtual ──────────────────────────────────────────────────
echo "[...] Criando ambiente virtual"
python3 -m venv "$DESTINO/.venv"

# ── Instalar dependencias ───────────────────────────────────────────────────
echo "[...] Instalando dependencias Python"
"$DESTINO/.venv/bin/pip" install --upgrade pip --quiet
"$DESTINO/.venv/bin/pip" install -r "$DESTINO/requirements.txt" --quiet

# ── Configurar .env ─────────────────────────────────────────────────────────
if [ ! -f "$DESTINO/.env" ]; then
    echo "[...] Copiando .env.example para .env"
    cp "$DESTINO/.env.example" "$DESTINO/.env"
    echo ""
    echo "============================================"
    echo "  ATENCAO: Edite o arquivo /opt/shamballa/.env"
    echo "  com as credenciais do PostgreSQL antes de continuar."
    echo "============================================"
    echo ""
    echo "Exemplo minimo:"
    echo "  SHAMBALA_DB_HOST=localhost"
    echo "  SHAMBALA_DB_PORT=5432"
    echo "  SHAMBALA_DB_NAME=shambala"
    echo "  SHAMBALA_DB_USER=shambala"
    echo "  SHAMBALA_DB_PASS=sua_senha"
    echo ""
    read -p "Pressione ENTER quando tiver editado o .env... "
fi

# ── Configurar servico systemd ──────────────────────────────────────────────
echo "[...] Configurando servico systemd"
sed "s/USUARIO/$USUARIO/g" "$DESTINO/shamballa.service" | sudo tee /etc/systemd/system/shamballa.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable shamballa.service
sudo systemctl start shamballa.service

# ── Verificar status ────────────────────────────────────────────────────────
sleep 2
if sudo systemctl is-active --quiet shamballa.service; then
    echo ""
    echo "============================================"
    echo "  Instalacao concluida com sucesso!"
    echo "============================================"
    echo ""
    echo "  Sistema rodando em: http://localhost:8000"
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -n "$IP" ]; then
        echo "  Na rede local:      http://${IP}:8000"
        echo "  Tela de chamada:    http://${IP}:8000/chamada"
    fi
    echo ""
    echo "  Login padrao: admin / admin"
    echo "  (troque a senha apos o primeiro acesso!)"
    echo ""
    echo "  Comandos uteis:"
    echo "    sudo systemctl status shamballa   # ver status"
    echo "    sudo systemctl restart shamballa  # reiniciar"
    echo "    sudo journalctl -u shamballa -f   # ver logs"
else
    echo ""
    echo "============================================"
    echo "  ATENCAO: O servico nao iniciou."
    echo "============================================"
    echo ""
    echo "Verifique:"
    echo "  1. O arquivo .env esta configurado? (/opt/shamballa/.env)"
    echo "  2. O PostgreSQL esta rodando? (sudo systemctl status postgresql)"
    echo "  3. Logs do servico: sudo journalctl -u shamballa -n 50"
    echo ""
    echo "  Comandos uteis:"
    echo "    sudo systemctl status shamballa   # ver status detalhado"
    echo "    sudo journalctl -u shamballa -n 50 # ultimas 50 linhas de log"
fi
