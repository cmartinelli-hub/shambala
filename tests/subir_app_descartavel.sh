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
# TASK_010/REV-03: a "segunda camada" deixou de ser nominal. O launcher agora:
#   (a) exige o arquivo de configuração NOMEADO como autoridade do destino e
#       confere divergência com o que o shell pede (SHAMBALA_TEST_DB /
#       SHAMBALA_DB_NAME) — divergir aborta sem efeitos;
#   (b) só IMPORTA `banco` depois de aplicar o arquivo (import não faz DDL, mas
#       resolve a configuração — ordem obrigatória);
#   (c) abre a CONEXÃO EFETIVA e confronta current_database()/current_user()
#       antes de executar o uvicorn — a identidade validada é a do servidor.
#
# Uso:
#   bash tests/subir_app_descartavel.sh <ENV_FILE_DESCARTAVEL> [porta]
#
# O script nunca lê nem imprime credenciais; valida e mostra apenas identidade.

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

cd "$(dirname "$0")/.."
PY="${SHAMBALA_PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

# (a)+(b)+(c) em um só processo de validação: contrato de configuração,
# aplicação do arquivo com autoridade, conexão efetiva, identidade observada.
SHAMBALA_ENV_FILE="$ENV_FILE" MODO_VALIDACAO=1 "$PY" - <<'PYCODE' || recusar "validação de identidade efetiva falhou (detalhes acima)."
import os, sys
sys.path.insert(0, "tests")
from trava_isolamento import (
    exigir_configuracao_consoante, exigir_ambiente_de_teste,
)
os.environ["SHAMBALA_MODO_TESTE"] = "1"
# (a) destino solicitado x arquivo nomeado — divergir aborta sem efeitos
info_cfg = exigir_configuracao_consoante(
    os.environ["SHAMBALA_ENV_FILE"], os.environ.get("SHAMBALA_TEST_DB"))
print(f"[descartável] config autoritativa: {info_cfg['arquivo']} -> "
      f"banco '{info_cfg['banco']}'", flush=True)
# (b) banco é importado SÓ DEPOIS do arquivo aplicado (consoante aplica env)
from banco import conectar, fechar_pool
# (c) conexão EFETIVA com a mesma configuração do app que vai subir
with conectar() as conn:
    info = exigir_ambiente_de_teste(conn)
fechar_pool()
if info.get("banco_efetivo") != info_cfg["banco"]:
    from trava_isolamento import AmbienteInseguro
    raise AmbienteInseguro(
        "a conexão efetiva resolveu '%s', divergente do destino configurado '%s'"
        % (info.get("banco_efetivo"), info_cfg["banco"]))
print("[descartável] identidade efetiva: banco=%s usuario=%s token=%s"
      % (info["banco_efetivo"], info["usuario_efetivo"], info["token"]), flush=True)
PYCODE

export SHAMBALA_ENV_FILE="$ENV_FILE"
export SHAMBALA_MODO_TESTE=1
echo "[descartável] subindo app de teste na porta $PORT (SHAMBALA_MODO_TESTE=1; destino pela config validada acima)" >&2
exec "$PY" -m uvicorn main:app --host 0.0.0.0 --port "$PORT"
