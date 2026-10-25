"""TASK_016 §7.1 — segunda conta de espécie não quebra; escolha registra o destino.

Roda contra app descartável (SHAMBALA_TEST_BASE). Comprova:
  1. uma conta de espécie: recebimento sem escolha é aceito;
  2. criada a segunda conta do mesmo tipo: sem escolha é RECUSADO (ambiguidade);
  3. escolhendo a conta nova: grava NELA;
  4. escolhendo a conta antiga: grava NELA.
"""
import http.cookiejar
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, "/nc/projetos/shambala")
from dotenv import load_dotenv

load_dotenv("/nc/projetos/shambala/.env.test", override=True)
os.environ["SHAMBALA_MODO_TESTE"] = "1"
from banco import conectar  # noqa: E402

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9081")
jar = http.cookiejar.CookieJar()


class NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


op = urllib.request.build_opener(NR(), urllib.request.HTTPCookieProcessor(jar))
try:
    op.open(urllib.request.Request(
        BASE + "/login", method="POST",
        data=urllib.parse.urlencode(
            {"nome_usuario": "admin", "senha": "teste123"}).encode()))
except urllib.error.HTTPError:
    pass


def doar(extra=None, desc=None):
    d = {"forma": "especie", "valor": "7.00",
         "descricao": desc or f"T016 {secrets.token_hex(3)}",
         "sessao_id": "", "conta_pix_id": "0",
         "idempotencia": secrets.token_hex(16)}
    d.update(extra or {})
    try:
        r = op.open(urllib.request.Request(
            BASE + "/caixa/doacao", method="POST",
            data=urllib.parse.urlencode(d).encode()), timeout=10)
        return r.status, r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")


def destino_de(desc):
    with conectar() as conn:
        r = conn.execute(
            "SELECT conta_id FROM financeiro_movimentacoes WHERE descricao = %s",
            (desc,)).fetchone()
        return r["conta_id"] if r else None


with conectar() as conn:
    sess = conn.execute(
        """INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status, versao)
           VALUES ((SELECT id FROM caixas WHERE ativo = 1 LIMIT 1), 0, 'aberto',
                   'eventos') RETURNING id""", None).fetchone()["id"]

print("[1] UMA conta de espécie — sem escolha, deve ACEITAR")
print("   ", doar({"sessao_id": str(sess)}))

print("\n[2] cria a SEGUNDA conta de espécie (o que o dono faria)")
with conectar() as conn:
    nova = conn.execute(
        """INSERT INTO contas_financeiras (nome, tipo)
           VALUES ('T016 Caixa da casa', 'especie') RETURNING id""",
        None).fetchone()["id"]
print("    conta nova id:", nova)

print("\n[3] sem informar conta — AMBÍGUO, deve RECUSAR pedindo escolha")
print("   ", doar({"sessao_id": str(sess)}))

print("\n[4] informando a conta NOVA — aceita e grava NELA")
d4 = f"T016 conta nova {secrets.token_hex(3)}"
print("   ", doar({"sessao_id": str(sess), "conta_id": str(nova)}, d4))
print("    destino gravado:", destino_de(d4), "| esperado:", nova)

print("\n[5] informando a conta ANTIGA — aceita e grava NELA")
with conectar() as conn:
    antiga = conn.execute(
        """SELECT id FROM contas_financeiras
           WHERE tipo = 'especie' AND id <> %s AND ativo = 1""",
        (nova,)).fetchone()["id"]
d5 = f"T016 conta antiga {secrets.token_hex(3)}"
print("   ", doar({"sessao_id": str(sess), "conta_id": str(antiga)}, d5))
print("    destino gravado:", destino_de(d5), "| esperado:", antiga)
