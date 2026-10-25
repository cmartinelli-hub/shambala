"""TASK_016 — fechamento legado com formas e destinos DISTINTOS na mesma sessão.

A pergunta do dono: o fechamento preserva os vínculos já conhecidos e pede SÓ os
destinos desconhecidos, sem concentrar tudo numa conta?

Cenário montado (uma sessão legada, cinco vendas):

  V1  espécie R$ 10  conta CONHECIDA  → conta A (persistida na venda)
  V2  espécie R$ 20  conta DESCONHECIDA (antiga)  → operador escolhe
  V3  pix     R$ 30  conta CONHECIDA  → conta B (persistida na venda)
  V4  pix     R$ 40  conta DESCONHECIDA (antiga)  → operador escolhe
  V5  fiado   R$ 50  — regra da casa: conta de obrigações

O que precisa valer:
  - a tela pede conta SÓ para espécie e SÓ para PIX (as duas desconhecidas),
    e nunca para fiado nem para as conhecidas;
  - sem as duas escolhas, recusa sem escrever nada;
  - com as escolhas, os lançamentos saem SEPARADOS por conta:
    A = 10,00 · B = 30,00 · escolhida-espécie = 20,00 · escolhida-pix = 40,00 ·
    obrigações = 50,00 — nada concentrado;
  - os vínculos conhecidos foram preservados exatamente.
"""
import os
import sys

sys.path.insert(0, "/nc/projetos/shambala")
from dotenv import load_dotenv

load_dotenv("/nc/projetos/shambala/.env.test", override=True)
os.environ["SHAMBALA_MODO_TESTE"] = "1"
import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal  # noqa: E402

from banco import conectar  # noqa: E402

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9011")
PREFIXO = "T016F"
res = []


def verificar(nome, cond, det=""):
    res.append((nome, bool(cond), det))
    print(f"  {'✔' if cond else '✘'} {nome}" + (f" — {det}" if not cond else ""))


class _NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(_NR, urllib.request.HTTPCookieProcessor(jar))


def autenticar():
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
            data=urllib.parse.urlencode(dados).encode()), timeout=20)
        return r.status, r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")


def conta(nome, tipo):
    with conectar() as conn:
        r = conn.execute("SELECT id FROM contas_financeiras WHERE nome=%s",
                         (nome,)).fetchone()
        if r:
            conn.execute("UPDATE contas_financeiras SET ativo=1 WHERE id=%s", (r["id"],))
            return r["id"]
        return conn.execute(
            """INSERT INTO contas_financeiras (nome, tipo, ativo)
               VALUES (%s, %s, 1) RETURNING id""", (nome, tipo)).fetchone()["id"]


def montar():
    with conectar() as conn:
        cx = conn.execute(
            """INSERT INTO caixas (nome, ativo, usa_produtos)
               VALUES (%s, 1, FALSE) RETURNING id""", (f"{PREFIXO} Caixa",)).fetchone()["id"]
        sess = conn.execute(
            """INSERT INTO caixa_movimentos
               (caixa_id, saldo_inicial, status, versao, total_vendas)
               VALUES (%s, 0, 'aberto', 'legado', 0) RETURNING id""",
            (cx,)).fetchone()["id"]
        obrig = conn.execute(
            "SELECT id FROM contas_financeiras WHERE tipo='areceber' AND ativo=1 "
            "ORDER BY id LIMIT 1", None).fetchone()
        vendas = [
            ("especie", 10, "conhecida_A"),
            ("especie", 20, None),
            ("pix", 30, "conhecida_B"),
            ("pix", 40, None),
            ("fiado", 50, None),
        ]
        ids = []
        for forma, valor, marca in vendas:
            cid = None
            if marca == "conhecida_A":
                cid = FIX["A"]
            elif marca == "conhecida_B":
                cid = FIX["B"]
            ids.append(conn.execute(
                """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
                       troco, status, atendente_id, fiado_pago, fiado_credito_usado,
                       doacao_valor, caixa_movimento_id, conta_id, criada_em)
                   VALUES (%s, '2026-10-20', %s, %s, 0, 'concluida', 1, FALSE, 0, 0,
                           %s, %s, '2026-10-20 09:00:00') RETURNING id""",
                (cx, valor, forma, sess, cid)).fetchone()["id"])
    return cx, sess, ids, (obrig["id"] if obrig else None)


def lancamentos(sess):
    with conectar() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT conta_id, valor, forma_pagamento FROM financeiro_movimentacoes
               WHERE descricao LIKE %s ORDER BY conta_id""",
            (f"%fechamento #{sess}",)).fetchall()]


def limpar(cx, sess, ids):
    with conectar() as conn:
        conn.execute("DELETE FROM caixa_sessao_eventos WHERE sessao_id=%s", (sess,))
        conn.execute("DELETE FROM financeiro_movimentacoes WHERE caixa_id=%s", (cx,))
        conn.execute("DELETE FROM vendas_pdv_itens WHERE venda_id = ANY(%s)", (ids,))
        conn.execute("DELETE FROM vendas_pdv WHERE id = ANY(%s)", (ids,))
        conn.execute("DELETE FROM caixa_movimentos WHERE id=%s", (sess,))
        conn.execute("DELETE FROM caixas WHERE id=%s", (cx,))
        conn.execute("DELETE FROM contas_financeiras WHERE nome LIKE %s",
                     (PREFIXO + "%",))


FIX = {}


def main():
    autenticar()
    FIX["A"] = conta(f"{PREFIXO} Espécie Conhecida", "especie")
    FIX["B"] = conta(f"{PREFIXO} Banco Conhecido", "banco")
    escolhida_esp = conta(f"{PREFIXO} Espécie Escolhida", "especie")
    escolhida_pix = conta(f"{PREFIXO} Poupança Escolhida", "poupanca")
    cx, sess, ids, obrig = montar()
    print(f"sessão legada {sess} | contas conhecidas A={FIX['A']} B={FIX['B']} | "
          f"escolhidas: espécie={escolhida_esp} pix={escolhida_pix} | "
          f"obrigações={obrig}")

    print("\n-- 1. a tela pede conta SÓ para o que é desconhecido")
    html = op.open(BASE + f"/caixa/fechar?caixa_movimento_id={sess}").read().decode()
    pede_esp = "conta_legado_especie" in html
    pede_pix = "conta_legado_pix" in html
    pede_fiado = "conta_legado_fiado" in html
    print(f"   pede espécie? {pede_esp} | pix? {pede_pix} | fiado? {pede_fiado}")
    verificar("1.1 a tela pede a conta de ESPÉCIE (há venda antiga sem destino)",
              pede_esp)
    verificar("1.2 a tela pede a conta de PIX (há venda antiga sem destino)", pede_pix)
    verificar("1.3 a tela NÃO pede conta de fiado (regra da casa: obrigações)",
              not pede_fiado)
    verificar("1.4 a tela não pede conta para as vendas de destino conhecido "
              "(só dois campos de escolha existem)",
              html.count('name="conta_legado_') ==
              len([x for x in ("conta_legado_especie", "conta_legado_pix",
                               "conta_legado_fiado") if f'name="{x}"' in html]),
              "campos de escolha inesperados")

    print("\n-- 2. sem as escolhas: recusa e NADA é escrito")
    st, loc = post("/caixa/fechar", {"caixa_movimento_id": sess, "observacao": ""})
    verificar("2.1 recusa pedindo os destinos desconhecidos",
              "conta_do_fechamento" in (loc or ""), f"{st} {loc}")
    verificar("2.2 nenhum lançamento criado", lancamentos(sess) == [],
              str(lancamentos(sess)))
    with conectar() as conn:
        st_sess = conn.execute("SELECT status FROM caixa_movimentos WHERE id=%s",
                               (sess,)).fetchone()["status"]
    verificar("2.3 a sessão continua aberta", st_sess == "aberto", f"status={st_sess}")

    print("\n-- 3. com as duas escolhas: separa por conta e preserva os vínculos")
    st3, loc3 = post("/caixa/fechar", {
        "caixa_movimento_id": sess, "observacao": "",
        "conta_legado_especie": str(escolhida_esp),
        "conta_legado_pix": str(escolhida_pix)})
    linhas = lancamentos(sess)
    print(f"   resposta: {st3} {loc3}")
    for l in linhas:
        print(f"     conta {l['conta_id']} · {l['forma_pagamento']} · R$ {l['valor']}")
    por_conta = {}
    for l in linhas:
        por_conta.setdefault(l["conta_id"], Decimal("0"))
        por_conta[l["conta_id"]] += Decimal(str(l["valor"]))
    print(f"   por conta: { {k: str(v) for k, v in por_conta.items()} }")

    verificar("3.1 venda de espécie CONHECIDA foi para a conta persistida (A=10,00)",
              por_conta.get(FIX["A"]) == Decimal("10.00"),
              f"A = {por_conta.get(FIX['A'])}")
    verificar("3.2 venda de PIX CONHECIDA foi para a conta persistida (B=30,00)",
              por_conta.get(FIX["B"]) == Decimal("30.00"),
              f"B = {por_conta.get(FIX['B'])}")
    verificar("3.3 espécie DESCONHECIDA foi para a conta escolhida (20,00)",
              por_conta.get(escolhida_esp) == Decimal("20.00"),
              f"escolhida espécie = {por_conta.get(escolhida_esp)}")
    verificar("3.4 PIX DESCONHECIDO foi para a conta escolhida (40,00)",
              por_conta.get(escolhida_pix) == Decimal("40.00"),
              f"escolhida pix = {por_conta.get(escolhida_pix)}")
    verificar("3.5 fiado foi para a conta de obrigações (50,00)",
              por_conta.get(obrig) == Decimal("50.00"),
              f"obrigações = {por_conta.get(obrig)}")
    verificar("3.6 nada foi concentrado numa conta só", len(por_conta) >= 4,
              f"contas usadas: {list(por_conta)}")
    total = sum(por_conta.values())
    verificar("3.7 o total fecha em 150,00 (nada perdido nem duplicado)",
              total == Decimal("150.00"), f"total = {total}")
    with conectar() as conn:
        st_fim = conn.execute("SELECT status FROM caixa_movimentos WHERE id=%s",
                              (sess,)).fetchone()["status"]
    verificar("3.8 a sessão foi fechada", st_fim == "fechado", f"status={st_fim}")

    limpar(cx, sess, ids)
    falhas = [n for n, ok, _ in res if not ok]
    print(f"\n[resultado] aprovadas={len(res) - len(falhas)} falhas={len(falhas)}")
    for f in falhas:
        print(f"  ✘ {f}")
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
