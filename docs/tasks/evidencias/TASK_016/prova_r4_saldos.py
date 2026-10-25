"""TASK_016 §7.7 — saldos: recursos disponíveis × obrigações pendentes.

Fixture controlada no banco descartável, com valores conferidos à mão:
  Gaveta (especie): +100 pago, −30 pago, +40 pago (transferência de saída),
                    +20 pendente, +999 CANCELADO (não conta)
                    → disponível 30,00 · pendente 20,00
  Banco   (banco):  +500 pago, +40 pago (transferência de entrada)
                    → disponível 540,00
  Areceber:         +300 PAGO (registro histórico), +70 pendente
                    → pendente 70,00  (não entra em recursos)
"""
import http.cookiejar
import os
import re
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
    conn.execute("""DELETE FROM financeiro_movimentacoes
                    WHERE descricao LIKE 'T016F%%'""", None)
    conn.execute("""DELETE FROM contas_financeiras
                    WHERE nome LIKE 'T016F%%'""", None)
    gaveta = conn.execute("""INSERT INTO contas_financeiras (nome, tipo)
        VALUES ('T016F Gaveta', 'especie') RETURNING id""", None).fetchone()["id"]
    banco = conn.execute("""INSERT INTO contas_financeiras (nome, tipo)
        VALUES ('T016F Banco', 'banco') RETURNING id""", None).fetchone()["id"]
    arec = conn.execute("""SELECT id FROM contas_financeiras
        WHERE tipo = 'areceber' LIMIT 1""", None).fetchone()["id"]

    def mov(tipo, valor, status, conta, cat="doacao"):
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, status, conta_id)
               VALUES (%s, %s, %s, '2026-10-20', 'T016F mov', %s, %s)""",
            (tipo, cat, valor, status, conta))

    mov("entrada", 100, "pago", gaveta)
    mov("saida", 30, "pago", gaveta)
    mov("entrada", 20, "pendente", gaveta)
    mov("entrada", 999, "cancelado", gaveta)
    mov("entrada", 500, "pago", banco)
    mov("entrada", 70, "pendente", arec)
    mov("entrada", 300, "pago", arec)
    mov("saida", 40, "pago", gaveta, "transferencia")
    mov("entrada", 40, "pago", banco, "transferencia")

html = op.open(BASE + "/configuracoes/contas").read().decode()
tabela = re.search(r"<table.*?</table>", html, re.S).group(0)


def celulas(linha):
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", linha, re.S)]


print("== Linhas da tela (disponível | pendente) ==")
obtido = {}
for linha in re.findall(r"<tr>(.*?)</tr>", tabela, re.S):
    c = celulas(linha)
    if len(c) >= 5 and c[1].startswith("T016F") or (len(c) >= 5 and "Valores a Receber" in c[1]):
        obtido[c[1]] = (c[3], c[4])
        print(f"  {c[1]:20s} disponível={c[3]:28s} pendente={c[4]}")

print("\n== Conferência ==")
esperado = {
    "T016F Gaveta": ("R$ 30,00", "R$ 20,00"),
    "T016F Banco": ("R$ 540,00", "R$ 0,00"),
}
ok = True
for nome, (d, p) in esperado.items():
    real = obtido.get(nome)
    bate = real and real[0].startswith(d) and real[1].startswith(p)
    print(f"  {nome}: esperado {d}/{p} -> {'OK' if bate else 'FALHA'} ({real})")
    ok = ok and bate

# cancelado não conta
print(f"\n  cancelado (999) fora da conta: "
      f"{'OK' if obtido.get('T016F Gaveta', ('', ''))[0].startswith('R$ 30,00') else 'FALHA'}")

# total de recursos bate com a soma manual no banco
with conectar() as conn:
    rec = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN m.tipo='entrada' THEN m.valor
                                  ELSE -m.valor END), 0) AS v
           FROM contas_financeiras c
           JOIN financeiro_movimentacoes m ON m.conta_id = c.id
           WHERE m.status = 'pago'
             AND c.tipo IN ('especie','pix','banco','poupanca','legado')""",
        None).fetchone()["v"]
    obrig = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN m.tipo='entrada' THEN m.valor
                                  ELSE -m.valor END), 0) AS v
           FROM contas_financeiras c
           JOIN financeiro_movimentacoes m ON m.conta_id = c.id
           WHERE m.status = 'pendente' AND c.tipo = 'areceber'""",
        None).fetchone()["v"]
cartoes = dict((b.strip(), a.strip()) for a, b in re.findall(
    r'stat-valor[^>]*>\s*(R\$[^<]*)\s*</div>\s*<div class="stat-label">\s*([^<]+)',
    html))
print(f"\n  cartão 'Recursos disponíveis': {cartoes.get('Recursos disponíveis')} "
      f"| soma no banco: {Decimal(str(rec))}")
print(f"  cartão 'A receber (obrigações pendentes)': "
      f"{cartoes.get('A receber (obrigações pendentes)')} "
      f"| soma no banco: {Decimal(str(obrig))}")
print("\nRESULTADO:", "todas as conferências passaram" if ok else "HÁ DIVERGÊNCIA")
