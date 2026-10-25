"""TASK_016 §7.3/§7.4 — PIX para banco/poupança; contas inválidas recusadas.

Roda contra app descartável. Comprova:
  - PIX sem sessão pode entrar em conta `banco` e `poupanca`, com o lançamento
    no livro-caixa e SEM evento de gaveta (não há efeito físico fictício);
  - conta `areceber` (obrigação) e conta INATIVA são recusadas com motivo
    próprio, sem escrita.
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

with conectar() as conn:
    banco = conn.execute(
        """INSERT INTO contas_financeiras (nome, tipo)
           VALUES ('T016 Banco', 'banco') RETURNING id""", None).fetchone()["id"]
    poup = conn.execute(
        """INSERT INTO contas_financeiras (nome, tipo)
           VALUES ('T016 Poupança', 'poupanca') RETURNING id""",
        None).fetchone()["id"]
print("contas criadas: banco =", banco, "| poupanca =", poup)


def doar_pix(conta, desc):
    d = {"forma": "pix", "valor": "9.00", "descricao": desc, "sessao_id": "",
         "conta_pix_id": str(conta), "idempotencia": secrets.token_hex(16)}
    try:
        r = op.open(urllib.request.Request(
            BASE + "/caixa/doacao", method="POST",
            data=urllib.parse.urlencode(d).encode()), timeout=10)
        return r.status, r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")


for rotulo, conta in (("BANCO", banco), ("POUPANÇA", poup)):
    desc = f"T016 pix {rotulo} {secrets.token_hex(3)}"
    st, loc = doar_pix(conta, desc)
    with conectar() as conn:
        m = conn.execute(
            """SELECT conta_id, forma_pagamento FROM financeiro_movimentacoes
               WHERE descricao = %s""", (desc,)).fetchone()
        ev = conn.execute(
            """SELECT efeito_fisico, conta_id FROM caixa_sessao_eventos
               WHERE financeiro_id = (SELECT id FROM financeiro_movimentacoes
                                      WHERE descricao = %s)""", (desc,)).fetchone()
    print(f"\n[{rotulo}] {st} {loc}")
    print("   lançamento:", dict(m) if m else None)
    print("   evento de gaveta:", dict(ev) if ev else "(nenhum — correto para PIX sem sessão)")

print("\n[conta areceber como destino de PIX — deve RECUSAR]")
with conectar() as conn:
    ar = conn.execute(
        "SELECT id FROM contas_financeiras WHERE tipo = 'areceber' LIMIT 1",
        None).fetchone()["id"]
desc_ar = f"T016 pix areceber {secrets.token_hex(3)}"
print("   ", doar_pix(ar, desc_ar))
with conectar() as conn:
    n = conn.execute(
        """SELECT count(*) AS c FROM financeiro_movimentacoes
           WHERE descricao = %s""", (desc_ar,)).fetchone()["c"]
print("    lançamentos gravados:", n, "(0 = recusa sem escrita)")

print("\n[conta INATIVA — deve RECUSAR]")
with conectar() as conn:
    conn.execute("UPDATE contas_financeiras SET ativo = 0 WHERE id = %s", (banco,))
desc_in = f"T016 pix inativa {secrets.token_hex(3)}"
print("   ", doar_pix(banco, desc_in))
with conectar() as conn:
    n2 = conn.execute(
        """SELECT count(*) AS c FROM financeiro_movimentacoes
           WHERE descricao = %s""", (desc_in,)).fetchone()["c"]
print("    lançamentos gravados:", n2, "(0 = recusa sem escrita)")
