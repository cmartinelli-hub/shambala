#!/bin/bash
# Instala o agente de impressão ESC/POS no PC do caixa.
# Execute como root: sudo bash instalar_agente.sh

set -e

SERVIDOR="10.100.0.4"
DESTINO="/opt/agente_impressora"
USUARIO="agente-impressora"
SERVICO="agente-impressora"

echo "=== Instalando agente de impressão Shambala ==="

# 1. Criar diretório
mkdir -p "$DESTINO"

# 2. Copiar o script do servidor
scp "root@${SERVIDOR}:/opt/shambala/agente_impressora.py" "$DESTINO/"
chmod +x "$DESTINO/agente_impressora.py"

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
echo "Teste: curl -X POST http://localhost:9001/imprimir --data-binary '' -v"
