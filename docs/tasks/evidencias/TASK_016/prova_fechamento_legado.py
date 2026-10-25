"""TASK_016 — fechamento de sessão LEGADO: destino por vínculo, nunca por palpite.

Cenário A: venda gravada com `vendas_pdv.conta_id` (vínculo persistido) →
           fechamento lança na conta da venda, sem perguntar nada.
Cenário B: venda antiga (conta_id NULL) → fechamento RECUSA sem escolha e não
           escreve nada; com escolha do operador, valida e grava nela.
"""
import http.cookiejar
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

sys.path.insert(0, "/nc/projetos/shambala")
from dotenv import load_dotenv

load_dotenv("/nc/projetos/shambala/.env.test", override=True)
os.environ["SHAMBALA_MODO_TESTE"] = "1"
from banco import conectar  # noqa: E402

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9082")
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


def post(rota, dados):
    try:
        r = op.open(urllib.request.Request(
            BASE + rota, method="POST",
            data=urllib.parse.urlencode(dados).encode()), timeout=15)
        return r.status, r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")


def sessao_legada(conn, caixa_id):
    # só uma sessão aberta por caixa: fecha a anterior do teste
    conn.execute(
        """UPDATE caixa_movimentos SET status = 'fechado'
           WHERE caixa_id = %s AND status = 'aberto'""", (caixa_id,))
    return conn.execute(
        """INSERT INTO caixa_movimentos
           (caixa_id, saldo_inicial, status, versao, total_vendas)
           VALUES (%s, 0, 'aberto', 'legado', 0) RETURNING id""",
        (caixa_id,)).fetchone()["id"]


with conectar() as conn:
    cx = conn.execute("SELECT id FROM caixas WHERE ativo = 1 LIMIT 1", None).fetchone()["id"]
    def conta(nome, tipo):
        r = conn.execute("SELECT id FROM contas_financeiras WHERE nome = %s",
                         (nome,)).fetchone()
        if r:
            return r["id"]
        return conn.execute(
            "INSERT INTO contas_financeiras (nome, tipo) VALUES (%s, %s) RETURNING id",
            (nome, tipo)).fetchone()["id"]

    gaveta = conta("T016B Gaveta", "especie")
    banco = conta("T016B Banco", "banco")
print("caixa:", cx, "| conta gaveta:", gaveta, "| conta banco:", banco)

# ── A) venda COM conta persistida ───────────────────────────────────────────
with conectar() as conn:
    sA = sessao_legada(conn, cx)
    conn.execute(
        """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
               troco, status, atendente_id, fiado_pago, fiado_credito_usado,
               doacao_valor, caixa_movimento_id, conta_id, criada_em)
           VALUES (%s, '2026-10-20', 11.00, 'especie', 0, 'concluida', 1, FALSE, 0,
                   0, %s, %s, '2026-10-20 09:00:00')""",
        (cx, sA, gaveta))
print(f"\n[A] sessão legado {sA} com venda de 11,00 já vinculada à conta {gaveta}")
st, loc = post("/caixa/fechar", {"caixa_movimento_id": sA, "observacao": ""})
with conectar() as conn:
    lanc = conn.execute(
        """SELECT conta_id, valor FROM financeiro_movimentacoes
           WHERE categoria = 'venda_pdv'
             AND descricao LIKE '%%fechamento #%s%%'""", (sA,)).fetchall()
print(f"    fechamento -> {st} {loc}")
print("    lançamento:", [dict(x) for x in lanc], "| esperado conta", gaveta)

# ── B) venda SEM conta (histórico) ─────────────────────────────────────────
with conectar() as conn:
    sB = sessao_legada(conn, cx)
    conn.execute(
        """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
               troco, status, atendente_id, fiado_pago, fiado_credito_usado,
               doacao_valor, caixa_movimento_id, conta_id, criada_em)
           VALUES (%s, '2026-10-20', 13.00, 'pix', 0, 'concluida', 1, FALSE, 0,
                   0, %s, NULL, '2026-10-20 09:00:00')""",
        (cx, sB))
print(f"\n[B] sessão legado {sB} com venda de 13,00 SEM conta (histórico)")

st, loc = post("/caixa/fechar", {"caixa_movimento_id": sB, "observacao": ""})
with conectar() as conn:
    n = conn.execute(
        """SELECT count(*) AS c FROM financeiro_movimentacoes
           WHERE descricao LIKE '%%fechamento #%s%%'""", (sB,)).fetchone()["c"]
    aberta = conn.execute(
        "SELECT status FROM caixa_movimentos WHERE id = %s", (sB,)).fetchone()["status"]
print(f"    sem escolha -> {st} {loc}")
print(f"    lançamentos: {n} (0 = recusa sem escrita) | sessão: {aberta} (aberto = não fechou)")

# a tela pede a conta?
html = op.open(BASE + f"/caixa/fechar?caixa_movimento_id={sB}").read().decode()
print("    tela pede a conta?", "conta_legado_pix" in html,
      "| opções oferecidas:", html.count('name="conta_legado_pix"'))

st, loc = post("/caixa/fechar", {
    "caixa_movimento_id": sB, "observacao": "",
    "conta_legado_pix": banco})
with conectar() as conn:
    lancB = conn.execute(
        """SELECT conta_id, valor FROM financeiro_movimentacoes
           WHERE descricao LIKE '%%fechamento #%s%%'""", (sB,)).fetchall()
    statusB = conn.execute(
        "SELECT status FROM caixa_movimentos WHERE id = %s", (sB,)).fetchone()["status"]
print(f"\n    com escolha (conta {banco}) -> {st} {loc}")
print("    lançamento:", [dict(x) for x in lancB], "| esperado conta", banco)
print("    sessão:", statusB)

# escolha INVÁLIDA: conta de espécie para PIX
with conectar() as conn:
    sC = sessao_legada(conn, cx)
    conn.execute(
        """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
               troco, status, atendente_id, fiado_pago, fiado_credito_usado,
               doacao_valor, caixa_movimento_id, conta_id, criada_em)
           VALUES (%s, '2026-10-20', 17.00, 'pix', 0, 'concluida', 1, FALSE, 0,
                   0, %s, NULL, '2026-10-20 09:00:00')""",
        (cx, sC))
print(f"\n[C] sessão {sC}: escolha INVÁLIDA (conta de espécie para PIX)")
st, loc = post("/caixa/fechar", {
    "caixa_movimento_id": sC, "observacao": "",
    "conta_legado_pix": gaveta})
with conectar() as conn:
    nC = conn.execute(
        """SELECT count(*) AS c FROM financeiro_movimentacoes
           WHERE descricao LIKE '%%fechamento #%s%%'""", (sC,)).fetchone()["c"]
    stC = conn.execute(
        "SELECT status FROM caixa_movimentos WHERE id = %s", (sC,)).fetchone()["status"]
print(f"    -> {st} {loc}")
print(f"    lançamentos: {nC} (0 = recusa sem escrita) | sessão: {stC}")
