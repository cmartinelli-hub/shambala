#!/bin/bash
# Aguarda o desktop e o servidor subirem
sleep 6

# Localiza o executável do Chromium
CHROMIUM=$(which chromium-browser 2>/dev/null || which chromium 2>/dev/null || which google-chrome 2>/dev/null)

if [ -z "$CHROMIUM" ]; then
    notify-send "Shambala" "Chromium não encontrado. Instale com: sudo apt install chromium-browser"
    exit 1
fi

# Descobre a posição X do monitor HDMI-1
X_OFFSET=$(xrandr | grep "HDMI-1 connected" | grep -oP '\d+x\d+\+\d+\+\d+' | grep -oP '(?<=x\d{3}\+)\d+|(?<=x\d{4}\+)\d+' | head -1)

# Fallback: tenta extrair de forma mais simples
if [ -z "$X_OFFSET" ]; then
    X_OFFSET=$(xrandr | grep "HDMI-1 connected" | grep -oP '\+\d+\+\d+' | grep -oP '^\+\d+' | tr -d '+')
fi

# Se ainda não encontrou, usa a largura do monitor principal como offset
if [ -z "$X_OFFSET" ]; then
    X_OFFSET=$(xrandr | grep "LVDS-1 connected" | grep -oP '^\d+' | head -1)
    X_OFFSET=${X_OFFSET:-1366}
fi

"$CHROMIUM" \
    --kiosk \
    --window-position=${X_OFFSET},0 \
    --no-first-run \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    http://localhost:8000/chamada
