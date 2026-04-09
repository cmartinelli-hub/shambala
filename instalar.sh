#!/bin/bash
set -e

DESTINO="/opt/shamballa"
SERVICO="shamballa.service"
USUARIO=$(whoami)

echo "=== Instalador do Sistema Shambala ==="
echo ""

# Verificar Python 3
if ! command -v python3 &>/dev/null; then
    echo "[ERRO] Python 3 não encontrado. Instale com: sudo apt install python3 python3-venv"
    exit 1
fi

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[OK] Python $PYTHON_VER encontrado"

# Criar diretório de instalação
echo "[...] Criando diretório $DESTINO"
sudo mkdir -p "$DESTINO"
sudo chown "$USUARIO:$USUARIO" "$DESTINO"

# Copiar arquivos da aplicação
echo "[...] Copiando arquivos"
cp -r main.py banco.py templates_config.py backup.py requirements.txt rotas templates static "$DESTINO/"

# Criar ambiente virtual
echo "[...] Criando ambiente virtual"
python3 -m venv "$DESTINO/.venv"

# Instalar dependências
echo "[...] Instalando dependências Python"
"$DESTINO/.venv/bin/pip" install --upgrade pip --quiet
"$DESTINO/.venv/bin/pip" install -r "$DESTINO/requirements.txt" --quiet

# Configurar serviço systemd
echo "[...] Configurando serviço systemd"
sed "s/USUARIO/$USUARIO/g" shamballa.service | sudo tee /etc/systemd/system/shamballa.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable shamballa.service
sudo systemctl start shamballa.service

echo ""
echo "=== Instalação concluída! ==="
echo ""
echo "  Sistema rodando em: http://localhost:8000"
IP=$(hostname -I | awk '{print $1}')
echo "  Na rede local:      http://${IP}:8000"
echo "  Tela de chamada:    http://${IP}:8000/chamada"
echo ""
echo "Outros comandos úteis:"
echo "  sudo systemctl status shamballa   # ver status"
echo "  sudo systemctl restart shamballa  # reiniciar"
echo "  sudo journalctl -u shamballa -f   # ver logs"
