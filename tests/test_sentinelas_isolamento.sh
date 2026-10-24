#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  PROVA NEGATIVA/POSITIVA das sentinelas A/B (TASK_010 §1, provas obrigatórias)
#
#  Cria dois bancos descartáveis A e B, cada um com uma SENTINELA própria, e
#  exerce o runner e o launcher REAL de ponta a ponta (não só a função
#  auxiliar), exigindo que toda situação de ambiente inválido ABORTE antes de
#  qualquer DDL/seed/startup com as sentinelas intactas. Termina com uma subida
#  positiva do launcher num banco de nome fora do padrão, provando identidade
#  efetiva e deixando B intocado.
#
#  Uso: bash tests/test_sentinelas_isolamento.sh      (RC=0 quando tudo casa)
# ─────────────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")/.."

A="shambala_t010_sent_a_test"
B="shambala_t010_sent_b_test"
PSQL() { psql -h "${PGADMIN_HOST:-/var/run/postgresql}" -U "${PGADMIN_USER:-shambala}" -d "$1" -tAqc "$2"; }
FALHAS=0

ficha() { # ficha(nome_banco) -> valor da sentinela, ou AUSENTE/INEXISTENTE
  if [ "$(PSQL postgres "SELECT 1 FROM pg_database WHERE datname='$1'")" != "1" ]; then
    echo "INEXISTENTE"; return
  fi
  local v
  v=$(PSQL "$1" "SELECT valor FROM sentinela" 2>/dev/null)
  [ -n "$v" ] && echo "$v" || echo "SEM-SENTINELA"
}

conferir() { # conferir(rotulo, esperado, observado)
  if [ "$2" = "$3" ]; then echo "  OK    $1"; else
    echo "  FALHA $1: esperado '$2', observado '$3'"; FALHAS=$((FALHAS+1)); fi
}

espera_rc() { # espera_rc(rotulo, comando...) — o comando DEVE terminar com rc != 0
  local rotulo="$1"; shift
  "$@" >/tmp/sentinelas_out.txt 2>&1
  local rc=$?
  # 126/127 são "não encontrei/não pude executar o comando" — a prova nem rodou.
  if [ "$rc" -eq 126 ] || [ "$rc" -eq 127 ]; then
    echo "  FALHA $rotulo: rc=$rc indica comando não executado, não recusa"; FALHAS=$((FALHAS+1))
  elif [ "$rc" -ne 0 ]; then echo "  OK    $rotulo (rc=$rc)"; else
    echo "  FALHA $rotulo: TERMINOU COM RC=0 — deveria abortar"; FALHAS=$((FALHAS+1)); fi
  tail -5 /tmp/sentinelas_out.txt | sed 's/^/        /'
}

echo "== [0] Preparar sentinelas A e B (bancos desta infraestrutura) =="
# Nomes privativos deste script: recriar é limpar recurso próprio.
for dbv in "$A:SENT-A" "$B:SENT-B"; do
  db="${dbv%%:*}"; sent="${dbv##*:}"
  PSQL postgres "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE)" >/dev/null
  PSQL postgres "CREATE DATABASE \"$db\"" >/dev/null
  PSQL "$db" "CREATE TABLE sentinela (valor text); INSERT INTO sentinela VALUES ('$sent')" >/dev/null
done
conferir "sentinela A criada" "SENT-A" "$(ficha "$A")"
conferir "sentinela B criada" "SENT-B" "$(ficha "$B")"

ENV_A=$(mktemp /tmp/shambala_sent_a.XXXX.env)
ENV_B=$(mktemp /tmp/shambala_sent_b.XXXX.env)
ENV_VAZIO=$(mktemp /tmp/shambala_sent_vazio.XXXX.env)
ENV_QUEBRADO=$(mktemp /tmp/shambala_sent_quebrado.XXXX.env)
chmod 600 "$ENV_A" "$ENV_B" "$ENV_VAZIO" "$ENV_QUEBRADO"
sed -E "s/^SHAMBALA_DB_NAME=.*/SHAMBALA_DB_NAME=$A/" .env.test > "$ENV_A"
sed -E "s/^SHAMBALA_DB_NAME=.*/SHAMBALA_DB_NAME=$B/" .env.test > "$ENV_B"
grep -v '^SHAMBALA_DB_NAME' "$ENV_B" > "$ENV_VAZIO"
sed -E 's/^SHAMBALA_DB_PORT=.*/SHAMBALA_DB_PORT=55432/' "$ENV_B" > "$ENV_QUEBRADO"

echo "== [1] NEGATIVA: solicitar destino A com configuração conflitante B =="
echo "     (runner real — deve abortar ANTES de qualquer DROP/CREATE/seed)"
espera_rc "runner: pedido A vs config B" \
  env SHAMBALA_ENV_FILE="$ENV_B" SHAMBALA_TEST_DB="$A" \
      SHAMBALA_TEST_PORT="${SHAMBALA_SENTINELA_PORTA:-9027}" \
  bash tests/rodar_testes.sh
conferir "sentinela A intacta (nada foi recriado)" "SENT-A" "$(ficha "$A")"
conferir "sentinela B intacta (nada foi apagado)"  "SENT-B" "$(ficha "$B")"

echo "== [2] NEGATIVA: launcher com o mesmo conflito A/B =="
espera_rc "launcher: pedido A vs config B" \
  env SHAMBALA_TEST_DB="$A" bash tests/subir_app_descartavel.sh "$ENV_B" \
      "${SHAMBALA_SENTINELA_PORTA:-9027}"
conferir "sentinela A intacta" "SENT-A" "$(ficha "$A")"
conferir "sentinela B intacta" "SENT-B" "$(ficha "$B")"

echo "== [3] NEGATIVA: banco preexistente SEM selo nunca é apagado por nome =="
echo "     (destino A bate com o arquivo, mas A preexiste sem selo: o runner"
echo "      deve recusar a recriação antes do startup)"
espera_rc "runner: alvo preexistente sem selo" \
  env SHAMBALA_ENV_FILE="$ENV_A" SHAMBALA_TEST_PORT="${SHAMBALA_SENTINELA_PORTA:-9027}" \
  bash tests/rodar_testes.sh
conferir "sentinela A preservada (recusa de adoção)" "SENT-A" "$(ficha "$A")"
conferir "sentinela B intacta" "SENT-B" "$(ficha "$B")"

echo "== [4] NEGATIVA: configuração incompleta / arquivo ausente =="
espera_rc "launcher sem DB_NAME no arquivo" \
  bash tests/subir_app_descartavel.sh "$ENV_VAZIO" "${SHAMBALA_SENTINELA_PORTA:-9027}"
espera_rc "launcher com arquivo inexistente" \
  bash tests/subir_app_descartavel.sh /tmp/nao-existe-t010.env "${SHAMBALA_SENTINELA_PORTA:-9027}"
espera_rc "runner com arquivo inexistente" \
  env SHAMBALA_ENV_FILE=/tmp/nao-existe-t010.env bash tests/rodar_testes.sh

echo "== [5] NEGATIVA: conexão indisponível aborta antes do startup =="
espera_rc "launcher com porta de banco errada" \
  bash tests/subir_app_descartavel.sh "$ENV_QUEBRADO" "${SHAMBALA_SENTINELA_PORTA:-9027}"
conferir "sentinela B intacta" "SENT-B" "$(ficha "$B")"

echo "== [6] POSITIVA: destino fora do padrão sobe com identidade provada =="
# A preexiste sem selo de caso negativo; este script é o dono reconhecido do
# nome — recria A vazia para a subida positiva do launcher.
PSQL postgres "DROP DATABASE IF EXISTS \"$A\" WITH (FORCE)" >/dev/null
PSQL postgres "CREATE DATABASE \"$A\"" >/dev/null
LOGPOS=/tmp/shambala_sent_pos_app.log
# Subida oficial: launcher real (a checagem prévia de conexão efetiva é o
# objeto da prova — nunca uvicorn direto).
SHAMBALA_EXEC_TOKEN="sent-pos-$$" setsid bash -c \
  "bash tests/subir_app_descartavel.sh '$ENV_A' ${SHAMBALA_SENTINELA_PORTA:-9027} > '$LOGPOS' 2>&1 & echo \$! > /tmp/shambala_sent_pos.pid"
sleep 1
INICIO=$SECONDS
until grep -q "startup complete" "$LOGPOS" 2>/dev/null; do
  if (( SECONDS - INICIO > 60 )); then break; fi
  sleep 1
done
RCHTTP=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${SHAMBALA_SENTINELA_PORTA:-9027}/login" || echo 000)
conferir "app positivo no ar (/login 200)" "200" "$RCHTTP"
if grep "Ambiente descartável confirmado" "$LOGPOS" \
   | grep -F "'banco_efetivo': '$A'" | grep -F "sent-pos-$$"; then
  echo "  OK    confirmação do lifespan amarra execução à identidade efetiva ($A)"
  grep "Ambiente descartável confirmado" "$LOGPOS" | tail -1 | sed 's/^/        /'
else
  echo "  FALHA confirmação de identidade efetiva ausente no log positivo"
  FALHAS=$((FALHAS+1))
fi
PIDPOS=$(cat /tmp/shambala_sent_pos.pid 2>/dev/null || true)
if [ -n "$PIDPOS" ]; then
  # matar APENAS a árvore deste script: launcher virou uvicorn por exec.
  kill -9 "$PIDPOS" 2>/dev/null || true
fi
sleep 1
conferir "sentinela B intacta após a positiva" "SENT-B" "$(ficha "$B")"

echo "== encerramento =="
PSQL postgres "DROP DATABASE IF EXISTS \"$A\" WITH (FORCE)" >/dev/null
PSQL postgres "DROP DATABASE IF EXISTS \"$B\" WITH (FORCE)" >/dev/null
rm -f "$ENV_A" "$ENV_B" "$ENV_VAZIO" "$ENV_QUEBRADO" /tmp/sentinelas_out.txt \
      /tmp/shambala_sent_pos_app.log /tmp/shambala_sent_pos.pid

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "SENTINELAS: todas as provas (negativas e positiva) casaram."
  exit 0
fi
echo "SENTINELAS: $FALHAS prova(s) divergente(s) — investigar."
exit 1
