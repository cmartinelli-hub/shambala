"""TASK_016 §7.5 — PDV com duas contas de espécie: escolha, destino e recusa.

Comprova pela ROTA REAL (`/caixa/pdv/finalizar`):
  1. com uma conta de espécie, a tela mostra a conta como padrão e a venda entra nela;
  2. com duas, a tela pede a escolha (select) e SEM escolha a venda é recusada
     sem criar venda nem lançamento;
  3. escolhendo a segunda conta, a venda grava nela — e `vendas_pdv.conta_id`
     fica persistido (é o vínculo que o fechamento legado lê);
  4. PIX com destino `banco` e `poupanca` pela rota do PDV.
"""
import http.cookiejar
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import json
import urllib.request

sys.path.insert(0, "/nc/projetos/shambala")
from dotenv import load_dotenv

load_dotenv("/nc/projetos/shambala/.env.test", override=True)
os.environ["SHAMBALA_MODO_TESTE"] = "1"
from banco import conectar  # noqa: E402

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9084")

with conectar() as _c:
    _p = _c.execute(
        """SELECT id, caixa_id FROM produtos WHERE ativo = 1
           ORDER BY id LIMIT 1""", None).fetchone()
    PRODUTO, CAIXA_TESTE = _p["id"], _p["caixa_id"]
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


def conta(nome, tipo):
    with conectar() as conn:
        r = conn.execute("SELECT id FROM contas_financeiras WHERE nome = %s",
                         (nome,)).fetchone()
        if r:
            conn.execute("UPDATE contas_financeiras SET ativo = 1 WHERE id = %s",
                         (r["id"],))
            return r["id"]
        return conn.execute(
            "INSERT INTO contas_financeiras (nome, tipo) VALUES (%s, %s) RETURNING id",
            (nome, tipo)).fetchone()["id"]


def tela_pdv(caixa_id=None):
    caixa_id = caixa_id or CAIXA_TESTE
    html = op.open(BASE + f"/caixa/pdv?caixa_id={caixa_id}").read().decode()
    m = re.search(r'name="caixa_id" value="(\d+)"', html)
    return html, (m.group(1) if m else None)


def vender(caixa_id, mov_id, forma, valor, conta_pedida=None, itens=None):
    dados = {
        "caixa_id": caixa_id, "caixa_movimento_id": mov_id,
        "forma_pagamento": forma, "valor_recebido": valor if forma == "especie" else "0",
        "itens_json": itens or json.dumps([{
            "produto_id": PRODUTO, "nome": "T016B Item", "quantidade": 1,
            "preco_unitario": float(valor)}]),
        "trabalhador_id": "0", "imprimir": "0", "doacao_valor": "0",
        "idempotencia": secrets.token_hex(16),
    }
    if conta_pedida is not None:
        chave = "conta_id" if forma == "especie" else "conta_pix_id"
        dados[chave] = str(conta_pedida)
    try:
        r = op.open(urllib.request.Request(
            BASE + "/caixa/pdv/finalizar", method="POST",
            data=urllib.parse.urlencode(dados).encode()), timeout=20)
        return r.status, r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")


def contar(caixa_id, mov_id):
    with conectar() as conn:
        v = conn.execute(
            "SELECT count(*) AS c FROM vendas_pdv WHERE caixa_movimento_id = %s",
            (mov_id,)).fetchone()["c"]
        f = conn.execute(
            """SELECT count(*) AS c FROM financeiro_movimentacoes
               WHERE caixa_id = %s AND categoria = 'venda_pdv'""",
            (caixa_id,)).fetchone()["c"]
    return v, f


def nova_sessao(caixa_id):
    with conectar() as conn:
        conn.execute("""UPDATE caixa_movimentos SET status='fechado'
                        WHERE caixa_id=%s AND status='aberto'""", (caixa_id,))
        return conn.execute(
            """INSERT INTO caixa_movimentos
               (caixa_id, saldo_inicial, status, versao, total_vendas)
               VALUES (%s, 0, 'aberto', 'eventos', 0) RETURNING id""",
            (caixa_id,)).fetchone()["id"]


# ── preparação: contas ─────────────────────────────────────────────────────
# caixa 1 é o caixa com sessão disponível; usa a conta de gaveta dele
gavetaA = conta("T016B Gaveta", "especie")
banco = conta("T016B Banco", "banco")
poup = conta("T016B Poupança", "poupanca")
with conectar() as conn:
    n_esp = conn.execute(
        """SELECT count(*) AS c FROM contas_financeiras
           WHERE tipo='especie' AND ativo=1""", None).fetchone()["c"]
print("contas de espécie ativas:", n_esp, "| gavetaA:", gavetaA,
      "| banco:", banco, "| poupança:", poup)

html, cx = tela_pdv(CAIXA_TESTE)
print("caixa do PDV:", cx)

# ── A) PIX para BANCO pela rota do PDV ─────────────────────────────────────
movA = nova_sessao(cx)
st, loc = vender(cx, movA, "pix", "12.00", banco)
with conectar() as conn:
    v = conn.execute(
        """SELECT conta_id FROM vendas_pdv WHERE caixa_movimento_id=%s
           ORDER BY id DESC LIMIT 1""", (movA,)).fetchone()
print(f"\n[A] PIX 12,00 -> conta banco {banco}: {st} {loc}")
print("    vendas_pdv.conta_id:", v["conta_id"] if v else None, "| esperado:", banco)

# ── B) PIX para POUPANÇA ───────────────────────────────────────────────────
movB = nova_sessao(cx)
st, loc = vender(cx, movB, "pix", "8.00", poup)
with conectar() as conn:
    v = conn.execute(
        """SELECT conta_id FROM vendas_pdv WHERE caixa_movimento_id=%s
           ORDER BY id DESC LIMIT 1""", (movB,)).fetchone()
print(f"\n[B] PIX 8,00 -> conta poupança {poup}: {st} {loc}")
print("    vendas_pdv.conta_id:", v["conta_id"] if v else None, "| esperado:", poup)

# ── C) duas contas de espécie: tela pede escolha ───────────────────────────
gavetaB = conta("T016B Caixa da Casa", "especie")
html, cx = tela_pdv(CAIXA_TESTE)
tem_select = 'id="sel-conta-especie"' in html
opcoes = re.findall(r'<option value="(\d+)">([^<]+)</option>',
                    html[html.find('id="sel-conta-especie"'):html.find('</select>', html.find('id="sel-conta-especie"'))]) \
    if tem_select else []
print(f"\n[C] duas contas de espécie ({gavetaA}, {gavetaB}) — tela pede escolha? {tem_select}")
print("    opções na tela:", [(i, n.strip()) for i, n in opcoes])

movC = nova_sessao(cx)
v0, f0 = contar(cx, movC)
st, loc = vender(cx, movC, "especie", "20.00")          # SEM conta
v1, f1 = contar(cx, movC)
print(f"\n    SEM escolha -> {st} {loc}")
print(f"    vendas {v0}->{v1} | lançamentos {f0}->{f1} (ambos devem ficar iguais)")

st, loc = vender(cx, movC, "especie", "20.00", gavetaB)  # escolhendo a SEGUNDA
v2, f2 = contar(cx, movC)
with conectar() as conn:
    v = conn.execute(
        """SELECT conta_id FROM vendas_pdv WHERE caixa_movimento_id=%s
           ORDER BY id DESC LIMIT 1""", (movC,)).fetchone()
print(f"\n    escolhendo a conta {gavetaB} -> {st} {loc}")
print(f"    vendas {v1}->{v2} | lançamentos {f1}->{f2}")
print("    vendas_pdv.conta_id:", v["conta_id"] if v else None, "| esperado:", gavetaB)

st, loc = vender(cx, movC, "especie", "20.00", gavetaA)  # escolhendo a PRIMEIRA
with conectar() as conn:
    v = conn.execute(
        """SELECT conta_id FROM vendas_pdv WHERE caixa_movimento_id=%s
           ORDER BY id DESC LIMIT 1""", (movC,)).fetchone()
print(f"\n    escolhendo a conta {gavetaA} -> {st} {loc}")
print("    vendas_pdv.conta_id:", v["conta_id"] if v else None, "| esperado:", gavetaA)

# ── D) conta inválida no PDV: recusa sem venda ─────────────────────────────
movD = nova_sessao(cx)
v0, f0 = contar(cx, movD)
st, loc = vender(cx, movD, "pix", "5.00", gavetaA)       # espécie num PIX
v1, f1 = contar(cx, movD)
print(f"\n[D] PIX com conta de ESPÉCIE -> {st} {loc}")
print(f"    vendas {v0}->{v1} | lançamentos {f0}->{f1} (ambos devem ficar iguais)")
