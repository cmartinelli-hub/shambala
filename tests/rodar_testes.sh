#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Runner do ambiente de TESTES do Shambala
#
#  Garante isolamento em camadas para que a suíte NUNCA atinja o banco de
#  desenvolvimento/produção:
#    1. usa .env.test exclusivo (nunca o .env do projeto);
#    2. exige SHAMBALA_MODO_TESTE=1 (validado dentro do próprio app);
#    3. o app valida a conexão efetiva no startup e recusa subir se não for
#       um banco descartável;
#    4. a suíte valida o ambiente ANTES de qualquer operação destrutiva;
#    5. porta e nome de banco dedicados, com verificação de que o listener
#       realmente é o processo novo (evita reusar app antigo na mesma porta).
#
#  Criação do banco: usa credenciais administrativas plenas, com fallback
#  documentado. Requer que o role possa criar bancos (CREATEDB).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

BANCO_TESTE="${SHAMBALA_TEST_DB:-shambala_test_passes}"
BANCO_DEV="${SHAMBALA_DEV_DB:-shambala}"
PORTA="${SHAMBALA_TEST_PORT:-9011}"
ARQ_ENV="$PWD/.env.test"
LOG="/tmp/shambala_test_app.log"
PID_FILE="/tmp/shambala_test_app.pid"

if [ ! -f "$ARQ_ENV" ]; then
  echo "[ERRO] $ARQ_ENV não existe. Ele é obrigatório para o isolamento." >&2
  exit 1
fi

# Credenciais administrativas: vêm do ambiente, nunca do código.
: "${PGADMIN_USER:=shambala}"
: "${PGADMIN_HOST:=/var/run/postgresql}"
export PGPASSWORD="${PGPASSWORD:-}"

PSQL_ADMIN() { psql -h "$PGADMIN_HOST" -U "$PGADMIN_USER" -d postgres -tAqc "$1"; }

echo "== Ambiente de teste =="
echo "  banco .....: $BANCO_TESTE"
echo "  porta .....: $PORTA"
echo "  env file ..: $ARQ_ENV"

# ── 0. Estado do DEV registrado ANTES de tudo (prova de não interferência) ───
# O role de teste não enxerga o schema do DEV, então as contagens são lidas por
# CONEXÃO À PRÓPRIA BASE do DEV (nunca pelo catálogo apenas). Se a leitura
# falhar, o valor fica vazio e a comparação final ACUSA DIVERGÊNCIA em vez de
# declarar "inalterado" sem ter provado nada.
LER_DEV() {
  psql -h "$PGADMIN_HOST" -U "$PGADMIN_USER" -d "$BANCO_DEV" -tAqc \
    "SELECT 'pessoas='||(SELECT count(*) FROM pessoas)||' checkins='||(SELECT count(*) FROM checkins)" 2>/dev/null
}

echo "== Registrando o estado do DEV (apenas leitura) =="
DEV_ANTES=$(LER_DEV || true)
if [ -z "$DEV_ANTES" ]; then
  echo "  [ALERTA] não foi possível LER o DEV ($BANCO_DEV) — a conferência final" >&2
  echo "           vai acusar divergência (fail-closed, nunca 'ok' sem prova)." >&2
  DEV_ANTES="ILEGIVEL"
fi
echo "  DEV ($BANCO_DEV): $DEV_ANTES"

# ── 1. Encerrar app de teste anterior e CONFIRMAR que a porta ficou livre ────
if [ -f "$PID_FILE" ]; then
  kill -9 "$(cat "$PID_FILE")" 2>/dev/null || true
  rm -f "$PID_FILE"
fi
for p in $(pgrep -f "uvicorn main:app --host 127.0.0.1 --port $PORTA" || true); do
  kill -9 "$p" 2>/dev/null || true
done
sleep 2
if ss -ltn 2>/dev/null | grep -q ":$PORTA "; then
  echo "[ERRO] a porta $PORTA continua ocupada — aborte para não reutilizar app antigo." >&2
  exit 1
fi
echo "  porta $PORTA livre: ok"

# ── 2. (Re)criar o banco descartável ────────────────────────────────────────
echo "== Recriando o banco de teste =="
PSQL_ADMIN "DROP DATABASE IF EXISTS \"$BANCO_TESTE\"" >/dev/null
PSQL_ADMIN "CREATE DATABASE \"$BANCO_TESTE\"" >/dev/null
echo "  $BANCO_TESTE criado"

# ── 3. Subir o app COM o env de teste e SEM herdar o .env do projeto ────────
echo "== Subindo o app de teste =="
SHAMBALA_ENV_FILE="$ARQ_ENV" \
SHAMBALA_MODO_TESTE=1 \
setsid nohup .venv/bin/python3 -m uvicorn main:app \
  --host 127.0.0.1 --port "$PORTA" > "$LOG" 2>&1 < /dev/null &
echo $! > "$PID_FILE"
# Espera ativa: recriar o schema em banco vazio pode passar de 8s.
INICIO=$SECONDS
until grep -q "startup complete" "$LOG" 2>/dev/null; do
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then break; fi
  if (( SECONDS - INICIO > 90 )); then break; fi
  sleep 1
done

if ! grep -q "startup complete" "$LOG"; then
  echo "[ERRO] o app de teste não iniciou. Log:" >&2
  tail -30 "$LOG" >&2
  exit 1
fi
if ! grep -q "Ambiente descartável confirmado" "$LOG"; then
  echo "[ERRO] a trava de isolamento do app não confirmou o ambiente. Log:" >&2
  tail -30 "$LOG" >&2
  exit 1
fi
echo "  app no ar e ambiente confirmado pelo próprio app"
grep "Ambiente descartável confirmado" "$LOG" | sed 's/^/  /'

# ── 4. Rodar as suítes (cada uma valida o ambiente antes de destruir) ────────
echo "== Executando as suítes =="
set +e
RC=0
for SUITE in test_tres_passes.py test_plano_unico.py test_permissoes.py test_agenda_medium.py test_presenca_trabalhadores.py test_codigo_trabalhador.py; do
  echo "-- $SUITE"
  SHAMBALA_ENV_FILE="$ARQ_ENV" \
  SHAMBALA_MODO_TESTE=1 \
  SHAMBALA_TEST_BASE="http://127.0.0.1:$PORTA" \
  SHAMBALA_TEST_DB="$BANCO_TESTE" \
  PYTHONPATH="$PWD" \
  .venv/bin/python3 "tests/$SUITE"
  RC_SUITE=$?
  if [ "$RC_SUITE" -ne 0 ]; then RC=$RC_SUITE; fi
done
set -e

# ── 5. Encerrar o app e conferir que o DEV não mudou ───────────────────────
kill -9 "$(cat "$PID_FILE")" 2>/dev/null || true
rm -f "$PID_FILE"
sleep 1

DEV_DEPOIS=$(LER_DEV || true)
[ -z "$DEV_DEPOIS" ] && DEV_DEPOIS="ILEGIVEL"
echo "== Conferência final do DEV =="
echo "  antes : $DEV_ANTES"
echo "  depois: $DEV_DEPOIS"
if [ "$DEV_ANTES" = "ILEGIVEL" ] || [ "$DEV_DEPOIS" = "ILEGIVEL" ]; then
  echo "[ALERTA] não foi possível LER o DEV — não há prova de não interferência." >&2
  RC=99
elif [ "$DEV_ANTES" != "$DEV_DEPOIS" ]; then
  echo "[ALERTA] o DEV MUDOU durante os testes — investigar imediatamente!" >&2
  RC=99
else
  echo "  DEV inalterado: ok"
fi

exit $RC
