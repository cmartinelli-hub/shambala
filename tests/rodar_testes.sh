#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Runner do ambiente de TESTES do Shambala — contrato de identidade TASK_010
#
#  Um único destino explícito por execução, consumido por runner, app e suítes:
#    1. O ARQUIVO de configuração nomeado (SHAMBALA_ENV_FILE, padrão .env.test)
#       é a AUTORIDADE do destino. SHAMBALA_TEST_DB apenas SOLICITA; se o
#       pedido divergir do arquivo, o runner ABORTA antes de qualquer efeito
#       (nada é recriado em silêncio — causa do REV-01).
#    2. Nada destrutivo acontece antes de validar o destino: o banco-alvo é
#       preparado via preparo_descartavel, que só recria banco com SELO desta
#       infraestrutura (nunca apaga banco preexistente por convenção de nome).
#    3. Antes do startup, uma conexão efetiva com a MESMA configuração do app
#       confronta current_database()/current_user() com o destino esperado.
#    4. O app nasce com SHAMBALA_EXEC_TOKEN próprio; a confirmação do lifespan
#       ecoa token + banco efetivo, e cada suíte prova o destino do servidor a
#       partir DO LOG DESTA EXECUÇÃO (log antigo não vale).
#    5. A porta é liberada apenas para o processo que o próprio runner registrou
#       (PID_FILE conferido); processos alheios nunca são mortos.
#
#  Uso:
#    bash tests/rodar_testes.sh                                   # .env.test
#    SHAMBALA_ENV_FILE=tests/env_positivo.env bash tests/rodar_testes.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

ARQ_ENV="${SHAMBALA_ENV_FILE:-$PWD/.env.test}"
PORTA="${SHAMBALA_TEST_PORT:-9011}"
BANCO_DEV="${SHAMBALA_DEV_DB:-shambala}"
LOG="/tmp/shambala_test_app.log"
PID_FILE="/tmp/shambala_test_app.pid"
PY=".venv/bin/python3"
TOKEN="t010-$(date +%Y%m%d%H%M%S)-$$"

if [ ! -f "$ARQ_ENV" ]; then
  echo "[ERRO] $ARQ_ENV não existe. A execução de testes exige um arquivo de" >&2
  echo "       configuração exclusivo — o runner não escolhe destino sozinho." >&2
  exit 1
fi

: "${PGADMIN_USER:=shambala}"
: "${PGADMIN_HOST:=/var/run/postgresql}"
export PGPASSWORD="${PGPASSWORD:-}"
export SHAMBALA_ENV_FILE="$ARQ_ENV" PYTHONPATH="$PWD"

PSQL_ADMIN() { psql -h "$PGADMIN_HOST" -U "$PGADMIN_USER" -d postgres -tAqc "$1"; }

# ── 1. Contrato de configuração: o arquivo manda; divergência aborta ─────────
BANCO_TESTE=$("$PY" - <<'PYCODE'
import os, sys
sys.path.insert(0, "tests")
from trava_isolamento import exigir_configuracao_consoante
info = exigir_configuracao_consoante(
    os.environ["SHAMBALA_ENV_FILE"], os.environ.get("SHAMBALA_TEST_DB"), aplicar=False)
print(info["banco"])
PYCODE
) || { echo "[ERRO] configuração incompatível com o destino solicitado — abortado" \
             "antes de qualquer DROP/CREATE/seed/startup." >&2; exit 1; }

echo "== Ambiente de teste (contrato TASK_010) =="
echo "  config ..: $ARQ_ENV (autoridade do destino)"
echo "  banco ...: $BANCO_TESTE"
echo "  porta ...: $PORTA"
echo "  token ...: $TOKEN (identifica ESTA execução nas provas)"

# ── 2. Estado do DEV registrado ANTES de tudo (prova de não interferência) ───
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

# ── 3. Porta: só o NOSSO app anterior pode ser encerrado ────────────────────
MATO_MEU_APP() {
  local pid="$1"
  [ -n "$pid" ] || return 0
  [ -f "/proc/$pid/cmdline" ] || return 0
  if tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q "uvicorn main:app"; then
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
  fi
}
if [ -f "$PID_FILE" ]; then
  MATO_MEU_APP "$(cat "$PID_FILE" 2>/dev/null || true)"
  rm -f "$PID_FILE"
fi
if ss -ltn 2>/dev/null | grep -q ":$PORTA "; then
  echo "[ERRO] a porta $PORTA está ocupada por processo que o runner não registrou" \
               "($PID_FILE): NÃO mato processos alheios. Encerre o ocupante à mão." >&2
  exit 1
fi
echo "  porta $PORTA livre: ok"

APP_PID=""
LIMPEZA_FEITA=0
encerrar() {
  # Encerramento também nos caminhos de erro: o app desta execução morre pelo
  # PID real que registramos; nunca mexemos em banco alheio nem em processo alheio.
  if [ "$LIMPEZA_FEITA" -eq 1 ]; then return; fi
  LIMPEZA_FEITA=1
  if [ -n "$APP_PID" ]; then MATO_MEU_APP "$APP_PID"; fi
  rm -f "$PID_FILE"
}
trap encerrar EXIT

# ── 4. Recriar o banco descartável com SELO de propriedade ──────────────────
echo "== Recriando o banco de teste (conexão de manutenção validada) =="
BANCO_ALVO="$BANCO_TESTE" TOKEN_EXEC="$TOKEN" "$PY" - <<'PYCODE'
import os, sys
sys.path.insert(0, "tests")
from preparo_descartavel import recriar_banco
recriar_banco(os.environ["BANCO_ALVO"], os.environ["TOKEN_EXEC"])
print(f"  {os.environ['BANCO_ALVO']} criado e selado para esta execução")
PYCODE

# ── 5. Identidade efetiva ANTES do startup ──────────────────────────────────
echo "== Confrontando a identidade efetiva (mesma configuração do app) =="
BANCO_ALVO="$BANCO_TESTE" TOKEN_EXEC="$TOKEN" "$PY" - <<'PYCODE'
import os, sys
sys.path.insert(0, "tests")
from dotenv import load_dotenv
load_dotenv(os.environ["SHAMBALA_ENV_FILE"], override=True)
os.environ["SHAMBALA_MODO_TESTE"] = "1"
os.environ["SHAMBALA_EXEC_TOKEN"] = os.environ["TOKEN_EXEC"]
from banco import conectar, fechar_pool
from trava_isolamento import exigir_ambiente_de_teste, AmbienteInseguro
with conectar() as conn:
    info = exigir_ambiente_de_teste(conn)
fechar_pool()
if info.get("banco_efetivo") != os.environ["BANCO_ALVO"]:
    raise AmbienteInseguro(
        "identidade efetiva '%s' difere do destino '%s'"
        % (info.get("banco_efetivo"), os.environ["BANCO_ALVO"]))
print(f"  [ok] banco efetivo={info['banco_efetivo']} "
      f"usuário efetivo={info['usuario_efetivo']} token={info['token']}")
PYCODE

# ── 6. Subir o app com a configuração DA EXECUÇÃO e token ───────────────────
echo "== Subindo o app de teste =="
: > "$LOG"
SHAMBALA_ENV_FILE="$ARQ_ENV" \
SHAMBALA_MODO_TESTE=1 \
SHAMBALA_EXEC_TOKEN="$TOKEN" \
setsid nohup "$PY" -m uvicorn main:app \
  --host 127.0.0.1 --port "$PORTA" > "$LOG" 2>&1 < /dev/null &
APP_PID=$!
echo "$APP_PID" > "$PID_FILE"
INICIO=$SECONDS
until grep -q "startup complete" "$LOG" 2>/dev/null; do
  if ! kill -0 "$APP_PID" 2>/dev/null; then break; fi
  if (( SECONDS - INICIO > 90 )); then break; fi
  sleep 1
done
if ! grep -q "startup complete" "$LOG"; then
  echo "[ERRO] o app de teste não iniciou. Log:" >&2
  tail -30 "$LOG" >&2
  exit 1
fi
if ! grep "Ambiente descartável confirmado" "$LOG" \
     | grep -F "'banco_efetivo': '$BANCO_TESTE'" \
     | grep -F "'token': '$TOKEN'"; then
  echo "[ERRO] a confirmação do app não amarra ESTA execução (token '$TOKEN')" >&2
  echo "       ao banco efetivo '$BANCO_TESTE' — ambiente não provado." >&2
  tail -30 "$LOG" >&2
  exit 1
fi
echo "  app no ar: destino confirmado pelo próprio app, vinculado ao token"

# ── 7. Suítes: cada uma recebe a MESMA configuração e prova o token ─────────
echo "== Executando as suítes =="
set +e
RC=0
SUMARIO=""
for SUITE in test_tres_passes.py test_plano_unico.py test_permissoes.py test_agenda_medium.py test_presenca_trabalhadores.py test_codigo_trabalhador.py test_terminal_presenca.py test_terminal_leitor.py test_correcoes_presenca.py test_gestao_correcoes_navegador.py test_pdv_precos_servidor.py test_cancelamento_vendas.py test_sessao_eventos_caixa.py test_caixa_navegador_task008.py teste_negativo_isolamento.py; do
  echo "-- $SUITE"
  SHAMBALA_ENV_FILE="$ARQ_ENV" \
  SHAMBALA_MODO_TESTE=1 \
  SHAMBALA_EXEC_TOKEN="$TOKEN" \
  SHAMBALA_TEST_BASE="http://127.0.0.1:$PORTA" \
  SHAMBALA_TEST_DB="$BANCO_TESTE" \
  SHAMBALA_APP_LOG="$LOG" \
  PYTHONPATH="$PWD" \
  "$PY" "tests/$SUITE"
  RC_SUITE=$?
  SUMARIO="${SUMARIO}${SUITE}: rc=${RC_SUITE}"$'\n'
  if [ "$RC_SUITE" -ne 0 ]; then RC=$RC_SUITE; fi
done
set -e
echo "== Sumário por suíte (contagens reais nos logs acima) =="
printf '%s' "$SUMARIO"

# ── 8. Encerrar o app e conferir que o DEV não mudou ────────────────────────
MATO_MEU_APP "$APP_PID"
APP_PID=""
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
