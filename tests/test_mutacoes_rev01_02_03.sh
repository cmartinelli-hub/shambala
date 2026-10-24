#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  PROVAS ANTES/DEPOIS (TASK_010 §4.1) — mutação temporária SOMENTE em cópia
#  isolada do repositório; a árvore real nunca é alterada.
#
#  Para cada achado da revisão, a mesma prova pertinente é executada duas
#  vezes: na CORREÇÃO (espera-se PASS) e na MUTAÇÃO que reintroduz o
#  comportamento anterior (espera-se FAIL — a prova DETECTA a regressão).
#
#   REV-01 → "escolha fixa de .env.test" reintroduzida no runner: o teste
#            negativo A/B tem de fracassar (o runner avança até o DDL).
#   REV-02 → parser antigo estritamente positivo no POST /caixa/abrir: abrir
#            com R$ 0,00 tem de voltar a ser recusado (a prova de zero falha).
#   REV-03 → launcher que "confirma conexão" sem abrir conexão: com porta
#            de banco morta ele tem de subir mesmo assim (a negativa falha).
#
#  Uso: bash tests/test_mutacoes_rev01_02_03.sh     (RC=0 quando as 6 provas casam)
# ─────────────────════════════════════════════════════════════════════════
set -u
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
PYORIG="$RAIZ/.venv/bin/python"
PSQL() { psql -h "${PGADMIN_HOST:-/var/run/postgresql}" -U "${PGADMIN_USER:-shambala}" -d "$1" -tAqc "$2"; }
FALHAS=0

registro() { # registro(rotulo, esperado, observado)
  if [ "$2" = "$3" ]; then echo "  OK    $1 ($3)"; else
    echo "  FALHA $1: esperado '$2', observado '$3'"; FALHAS=$((FALHAS+1)); fi
}

killpid() { # killpid(arquivo_pid) — só mata PID numérico >1 gravado por NÓS
  local p
  p=$(cat "$1" 2>/dev/null) || return 0
  case "$p" in ''|*[!0-9]*) return 0 ;; esac
  [ "$p" -gt 1 ] && kill -9 "$p" 2>/dev/null || true
}

COPIA=$(mktemp -d /tmp/shambala_t010_mut.XXXXXX)
cleanup() {
  PSQL postgres "DROP DATABASE IF EXISTS shambala_t010_mut_test WITH (FORCE)" >/dev/null 2>&1
  rm -rf "$COPIA"
}
trap cleanup EXIT

echo "== preparando cópia isolada (sem .venv; usa o interpretador do projeto) =="
rsync -a --exclude .venv --exclude .git --exclude 'docs/tasks/evidencias' \
      "$RAIZ/" "$COPIA/" >/dev/null
ln -s "$RAIZ/.venv" "$COPIA/.venv"

# banco descartável exclusivo desta infraestrutura de mutações
PSQL postgres "DROP DATABASE IF EXISTS shambala_t010_mut_test WITH (FORCE)" >/dev/null 2>&1
PSQL postgres "CREATE DATABASE shambala_t010_mut_test" >/dev/null

ENV_MUT=$(mktemp /tmp/shambala_t010_mut_env.XXXX)
chmod 600 "$ENV_MUT"
sed -E 's/^SHAMBALA_DB_NAME=.*/SHAMBALA_DB_NAME=shambala_t010_mut_test/' "$RAIZ/.env.test" > "$ENV_MUT"

# ═══ REV-01 ═══════════════════════════════════════════════════════════════
echo
echo "== REV-01 — escolha fixa de .env.test reintroduzida em cópia isolada =="
DB_A="shambala_t010_sent_a_test"; DB_B="shambala_t010_sent_b_test"
ENV_B=$(mktemp /tmp/shambala_t010_mut_b.XXXX)
sed -E "s/^SHAMBALA_DB_NAME=.*/SHAMBALA_DB_NAME=$DB_B/" "$RAIZ/.env.test" > "$ENV_B"
PSQL postgres "DROP DATABASE IF EXISTS \"$DB_A\" WITH (FORCE)" >/dev/null 2>&1
PSQL postgres "CREATE DATABASE \"$DB_A\"" >/dev/null
PSQL "$DB_A" "CREATE TABLE sentinela (valor text); INSERT INTO sentinela VALUES ('SENT-A')" >/dev/null

cd "$RAIZ"
timeout 40 env SHAMBALA_ENV_FILE="$ENV_B" SHAMBALA_TEST_DB="$DB_A" SHAMBALA_TEST_PORT=9047 \
  bash tests/rodar_testes.sh >/tmp/mut_rev01_ok.log 2>&1
rc_corrigido=$?
sentinelas_ok_apos_correcao=$( [ "$rc_corrigido" -ne 0 ] && \
  [ "$(PSQL "$DB_A" "SELECT valor FROM sentinela" 2>/dev/null)" = "SENT-A" ] && echo SIM || echo NAO )
registro "com a CORREÇÃO o runner recusa A/B antes de qualquer DDL" "SIM" "$sentinelas_ok_apos_correcao"

cd "$COPIA"
# mutação fiel: o runner volta a IGNORAR SHAMBALA_ENV_FILE (escolha fixa de
# .env.test) e passa a CONFAR O QUE O SHELL PEDE, sem confronto algum — o bug
# original do REV-01.
sed -i 's|^ARQ_ENV="${SHAMBALA_ENV_FILE:-\$PWD/\.env\.test}"|ARQ_ENV="$PWD/.env.test"  # MUTACAO REV-01|' tests/rodar_testes.sh
"$PYORIG" - <<'PY'
src = open("tests/rodar_testes.sh", encoding="utf-8").read()
alvo = ('info = exigir_configuracao_consoante(\n'
        '    os.environ["SHAMBALA_ENV_FILE"], os.environ.get("SHAMBALA_TEST_DB"), aplicar=False)')
mut = ('info = {"banco": os.environ.get("SHAMBALA_TEST_DB") or "shambala_test_passes"}  # MUTACAO REV-01')
assert alvo in src, "alvo da mutação REV-01 não encontrado"
open("tests/rodar_testes.sh", "w", encoding="utf-8").write(src.replace(alvo, mut))
PY
grep -q 'MUTACAO REV-01' tests/rodar_testes.sh || { echo "  FALHA mutação REV-01 não aplicada"; FALHAS=$((FALHAS+1)); }
timeout 60 env SHAMBALA_ENV_FILE="$ENV_B" SHAMBALA_TEST_DB="$DB_A" SHAMBALA_TEST_PORT=9047 \
  bash tests/rodar_testes.sh >/tmp/mut_rev01_bad.log 2>&1
rc_mutado=$?
enganou=$(grep -q "divergência de destino\|abortado antes de qualquer DROP" /tmp/mut_rev01_bad.log && echo NAO || echo SIM)
registro "com a MUTAÇÃO a prova negativa falha (não recusa A/B; rc=$rc_mutado, avançou: $enganou)" "SIM" "$enganou"
cd "$RAIZ"

# ═══ REV-02 ═══════════════════════════════════════════════════════════════
echo
echo "== REV-02 — parser estritamente positivo no POST /caixa/abrir =="
limpar_mut_db() {
  local cid
  cid=$(PSQL shambala_t010_mut_test "SELECT id FROM caixas WHERE nome='Mutacao Zero'")
  PSQL shambala_t010_mut_test \
    "DELETE FROM caixa_sessao_eventos WHERE sessao_id IN (SELECT id FROM caixa_movimentos WHERE caixa_id=$cid);
     DELETE FROM caixa_movimentos WHERE caixa_id=$cid;" >/dev/null 2>&1
}

abrir_zero() { # abrir_zero(pasta_do_app, porta) -> echo ACEITO | RECUSADO | FALHA
  local pasta="$1" porta="$2"
  limpar_mut_db
  ( cd "$pasta" && setsid bash -c \
      "bash tests/subir_app_descartavel.sh '$ENV_MUT' $porta > /tmp/mut_rev02_app_$porta.log 2>&1 & echo \$! > /tmp/mut_rev02_pid_$porta" )
  local ini=$SECONDS
  until grep -q "startup complete" /tmp/mut_rev02_app_$porta.log 2>/dev/null; do
    if (( SECONDS - ini > 60 )); then
      killpid "/tmp/mut_rev02_pid_$porta"
      echo FALHA; return
    fi
    sleep 1
  done
  local cxjar=/tmp/mut_rev02_cookies_$porta.txt
  curl -s -c "$cxjar" -o /dev/null -X POST "http://127.0.0.1:$porta/login" \
       -d "nome_usuario=admin&senha=teste123"
  local cx
  cx=$(PSQL shambala_t010_mut_test "SELECT id FROM caixas WHERE nome='Mutacao Zero'")
  local loc
  loc=$(curl -s -b "$cxjar" -o /dev/null -w '%{redirect_url}' \
        -X POST "http://127.0.0.1:$porta/caixa/abrir" \
        -d "caixa_id=$cx&saldo_inicial=0.00")
  echo "$loc" > /tmp/mut_rev02_loc_$porta.log
  killpid "/tmp/mut_rev02_pid_$porta"
  sleep 1
  [ -n "$loc" ] || { echo FALHA; return; }
  case "$loc" in
    *valor_invalido*) echo RECUSADO ;;
    *)                echo ACEITO  ;;
  esac
}

garante_admin() {
  ( cd "$RAIZ" && SHAMBALA_ENV_FILE="$ENV_MUT" "$PYORIG" - <<'PY'
import os
from dotenv import load_dotenv
load_dotenv(os.environ["SHAMBALA_ENV_FILE"], override=True)
from banco import conectar, criar_tabelas
criar_tabelas()
from rotas.auth import hash_senha
with conectar() as conn:
    row = conn.execute("SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()
    if row:
        conn.execute("UPDATE atendentes SET senha_hash=%s, ativo=1 WHERE id=%s",
                     (hash_senha("teste123"), row["id"]))
    else:
        conn.execute("INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo) "
                     "VALUES ('admin','Administrador',%s,1)", (hash_senha("teste123"),))
    if not conn.execute("SELECT 1 FROM caixas WHERE nome='Mutacao Zero'").fetchone():
        conn.execute("INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
                     "VALUES ('Mutacao Zero','prova REV-02',TRUE,1)")
    conn.execute("DELETE FROM caixa_movimentos WHERE caixa_id IN "
                 "(SELECT id FROM caixas WHERE nome='Mutacao Zero')")
PY
  )
}
garante_admin || { echo "  FALHA preparar admin/caixa no banco da mutação"; FALHAS=$((FALHAS+1)); }

RESULT_OK=$(abrir_zero "$RAIZ" 9048)
registro "com a CORREÇÃO abrir R\$ 0,00 é ACEITO (REV-02)" "ACEITO" "$RESULT_OK"
sed -i 's|inicial = moeda_nao_negativa(saldo_inicial or "0", "saldo_inicial")|inicial = moeda(saldo_inicial, "saldo_inicial")  # MUTACAO REV-02|' \
   "$COPIA/rotas/caixa.py"
# a rota importa só o parser novo; o mutante usa o antigo — repor o import como
# no código pré-TASK_010 (mesma lista de nomes que a rota carregava antes).
sed -i 's|from servico_sessao import SessaoInvalida, abrir_sessao, moeda_nao_negativa|from servico_sessao import SessaoInvalida, abrir_sessao, moeda  # MUTACAO REV-02i|' \
   "$COPIA/rotas/caixa.py"
grep -q 'MUTACAO REV-02' "$COPIA/rotas/caixa.py" || { echo "  FALHA mutação REV-02 não aplicada"; FALHAS=$((FALHAS+1)); }
grep -q 'MUTACAO REV-02i' "$COPIA/rotas/caixa.py" || { echo "  FALHA mutação REV-02 (import) não aplicada"; FALHAS=$((FALHAS+1)); }
RESULT_BAD=$(abrir_zero "$COPIA" 9049)
registro "com a MUTAÇÃO abrir R\$ 0,00 volta a ser RECUSADO (a prova detecta)" "RECUSADO" "$RESULT_BAD"

# ═══ REV-03 ═══════════════════════════════════════════════════════════════
echo
echo "== REV-03 — launcher sem conexão efetiva =="
ENV_QUEBRADO=$(mktemp /tmp/shambala_t010_mut_q.XXXX)
sed -E 's/^SHAMBALA_DB_PORT=.*/SHAMBALA_DB_PORT=55432/' "$ENV_MUT" > "$ENV_QUEBRADO"

cd "$RAIZ"
env SHAMBALA_ENV_FILE="$ENV_QUEBRADO" bash tests/subir_app_descartavel.sh "$ENV_QUEBRADO" 9050 \
  >/tmp/mut_rev03_ok.log 2>&1
rc_corrigido=$?
registro "com a CORREÇÃO o launcher aborta com conexão indisponível (rc≠0, sem startup)" \
  "SIM" "$( [ "$rc_corrigido" -ne 0 ] && grep -q "Nenhum processo de teste foi iniciado" /tmp/mut_rev03_ok.log && echo SIM || echo NAO )"

cd "$COPIA"
# mutação fiel ao REV-03 original: "confirma" nominalmente, sem abrir conexão —
# troca o bloco de validação (heredoc PYCODE) por um echo que só lê o arquivo.
"$PYORIG" - <<'PY'
src = open("tests/subir_app_descartavel.sh", encoding="utf-8").read()
ini = src.index('SHAMBALA_ENV_FILE="$ENV_FILE" MODO_VALIDACAO=1')
fim = src.index('PYCODE\n', ini) + len('PYCODE\n')
fake = ('# MUTACAO REV-03: identidade nominal, sem abrir conexão\n'
        'echo "[descartável] identidade (MUTACAO REV-03: só lê o arquivo)"\n'
        'grep -o "SHAMBALA_DB_NAME=.*" "$ENV_FILE" || true\n')
open("tests/subir_app_descartavel.sh", "w", encoding="utf-8").write(
    src[:ini] + fake + src[fim:])
PY
grep -q 'MUTACAO REV-03' tests/subir_app_descartavel.sh || { echo "  FALHA mutação REV-03 não aplicada"; FALHAS=$((FALHAS+1)); }
timeout 40 env SHAMBALA_ENV_FILE="$ENV_QUEBRADO" bash tests/subir_app_descartavel.sh "$ENV_QUEBRADO" 9050 \
  >/tmp/mut_rev03_bad.log 2>&1
rc_mutado=$?
passou_validacao=$(grep -q "MUTACAO REV-03: só lê o arquivo" /tmp/mut_rev03_bad.log && \
                   ! grep -q "Nenhum processo de teste foi iniciado" /tmp/mut_rev03_bad.log && echo SIM || echo NAO)
registro "com a MUTAÇÃO o launcher chega ao startup sem conexão válida (a prova detecta; rc=$rc_mutado)" "SIM" "$passou_validacao"
killpid "/tmp/mut_rev03_pid"
cd "$RAIZ"

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "MUTAÇÕES: as três provas antes/depois casaram (correção passa, mutação falha)."
  exit 0
fi
echo "MUTAÇÕES: $FALHAS prova(s) divergente(s)."
exit 1
