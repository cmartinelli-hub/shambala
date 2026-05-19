#!/bin/bash
# Instala o agente de impressão ESC/POS no PC do caixa.
# Execute como root: sudo bash instalar_agente.sh [IP_DO_SERVIDOR]
#
# O script baixa agente_impressora.py do servidor Shambala via HTTP.
# Se não conseguir, coloque o arquivo na mesma pasta e rode de novo.

set -e

SERVIDOR="${1:-192.168.0.2}"
PORTA="8000"
DESTINO="/opt/agente_impressora"
USUARIO="agente-impressora"
SERVICO="agente-impressora"

echo "=== Instalando agente de impressão Shambala ==="

# 1. Criar diretório
mkdir -p "$DESTINO"

# 2. Obter agente_impressora.py
SCRIPT_LOCAL="$(dirname "$0")/agente_impressora.py"
if [ -f "$SCRIPT_LOCAL" ]; then
    echo "Usando agente_impressora.py do diretório atual."
    cp "$SCRIPT_LOCAL" "$DESTINO/"
else
    echo "Baixando agente_impressora.py de http://${SERVIDOR}:${PORTA}/static/agente_impressora.py ..."
    if ! curl -fsSL "http://${SERVIDOR}:${PORTA}/static/agente_impressora.py" -o "$DESTINO/agente_impressora.py" 2>/dev/null; then
        echo ""
        echo "ERRO: Não foi possível baixar o arquivo automaticamente."
        echo "Copie agente_impressora.py para esta pasta e execute o script novamente:"
        echo "  cp /caminho/agente_impressora.py $(dirname "$0")/"
        echo "  sudo bash $0"
        exit 1
    fi
fi
chmod +x "$DESTINO/agente_impressora.py"
echo "Arquivo instalado em $DESTINO/agente_impressora.py"

# 3. Criar usuário dedicado (sem login, sem home)
if ! id "$USUARIO" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$USUARIO"
    echo "Usuário '$USUARIO' criado."
fi

# 4. Adicionar ao grupo dialout (acesso à impressora USB serial)
usermod -aG dialout "$USUARIO"
echo "Usuário '$USUARIO' adicionado ao grupo dialout."

# 5. Instalar service do systemd
cat > /etc/systemd/system/agente-impressora.service <<EOF
[Unit]
Description=Agente de impressao ESC/POS — Shambala
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${DESTINO}/agente_impressora.py
Restart=on-failure
RestartSec=5
User=${USUARIO}

[Install]
WantedBy=multi-user.target
EOF

# 6. Habilitar e iniciar
systemctl daemon-reload
systemctl enable --now "$SERVICO"

echo ""
echo "=== Concluído ==="
systemctl status "$SERVICO" --no-pager
echo ""
echo "Para testar: curl -s http://localhost:9001/imprimir -X POST | cat"
