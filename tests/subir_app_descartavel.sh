#!/usr/bin/env bash
# Sube um app SHAMBALA de teste de forma DESCARTÁVEL.
#
# Esta é a ÚNICA maneira segura de subir um servidor para testes. Ela recusa
# iniciar (com o processo ainda nem começando) quando a configuração não é
# comprovadamente descartável — ANTES de qualquer startup que possa aplicar
# schema. Conferir o log depois não desfaz uma escrita: por isso a checagem é
# prévia. Existiu por causa do incidente em que um restart acidental, feito com
# `uvicorn` direto e sem env de teste, aplicou a migração no banco de DEV.
#
# Uso:
#   bash tests/subir_app_descartavel.sh <ENV_FILE_DESCARTAVEL> [porta]
#
# O ENV_FILE precisa apontar SHAMBALA_DB_NAME para um banco cujo nome contenha
# o marcador "_test" (ex.: shambala_test_passes, shambala_t008_test). O script
# nunca lê nem imprime credenciais; só valida o nome do banco.

set -euo pipefail

ENV_FILE="${1:-}"
PORT="${2:-9014}"

recusar() {
    echo "RECUSADO — ambiente não é comprovadamente descartável:" >&2
    echo "  $1" >&2
    echo "Nenhum processo de teste foi iniciado. Corrija o env e tente de novo." >&2
    exit 2
}

[ -n "$ENV_FILE" ] || recusar "nenhum arquivo de configuração informado (é obrigatório apontar um env descartável)."
[ -f "$ENV_FILE" ] || recusar "arquivo de configuração inexistente: $ENV_FILE"

DB_NAME="$(sed -nE 's/^[[:space:]]*SHAMBALA_DB_NAME[[:space:]]*=[[:space:]]*"?([^"]*)"?.*/\1/p' "$ENV_FILE" | head -1)"
[ -n "$DB_NAME" ] || recusar "o env $ENV_FILE não define SHAMBALA_DB_NAME."

case "$DB_NAME" in
    *_test*) : ;;
    *) recusar "SHAMBALA_DB_NAME='$DB_NAME' não contém o marcador '_test' (banco de DEV/produção?)." ;;
esac

# Segunda camada: a mesma trava que o lifespan do app usa, checada aqui antes
# de subir, para que o startup nem comece em ambiente inseguro.
PY="${SHAMBALA_PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
MODO_TESTE=1 SHAMBALA_DB_NAME="$DB_NAME" "$PY" - <<'PYCODE'
import os
from tests.trava_isolamento import exigir_ambiente_de_teste
os.environ["SHAMBALA_MODO_TESTE"] = "1"
info = exigir_ambiente_de_teste()
print(f"[descartável] banco validado: {info['banco']}", flush=True)
PYCODE

export SHAMBALA_ENV_FILE="$ENV_FILE"
export SHAMBALA_MODO_TESTE=1
echo "[descartável] subindo app de teste: DB='$DB_NAME' porta=$PORT (SHAMBALA_MODO_TESTE=1)" >&2
exec "$PY" -m uvicorn main:app --host 0.0.0.0 --port "$PORT"
