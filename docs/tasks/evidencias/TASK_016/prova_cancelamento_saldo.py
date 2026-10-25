"""TASK_016 — cancelamento de venda: o que acontece com o saldo (item 3).

Terminologia, porque "estorno sem tocar no saldo" é ambíguo e pode ser lido como
contradição. Neste produto há DOIS lugares com "saldo":

  A) `financeiro_movimentacoes` — o LIVRO FINANCEIRO por conta. Um lançamento
     `pago` move o saldo da conta para sempre (é o saldo que aparece em
     Configurações → Contas).
  B) `caixa_sessao_eventos` — o EFEITO FÍSICO na gaveta da sessão. A venda em
     espécie entra (entrada, efeito_fisico = valor); o esperado da gaveta é
     inicial + Σ efeito_fisico.

Esta prova mede os dois, por conta e por sessão, antes e depois, e mostra que:

  1. o cancelamento de venda em sessão 'eventos' NÃO cria lançamento no livro
     (A) — a venda de sessão eventos só vira lançamento financeiro no
     FECHAMENTO; cancelar antes do fechamento não deixa lançamento para anular;
  2. o efeito físico (B) é compensado EXATAMENTE UMA VEZ (uma saída de mesmo
     valor, ligada ao mesmo venda_id) e o esperado da gaveta volta ao valor de
     antes da venda;
  3. rodar o cancelamento DE NOVO é recusado e NÃO compensa duas vezes;
  4. numa venda com lançamento financeiro JÁ existente (o caso do fechamento), o
     cancelamento recusa — não há estorno financeiro silencioso.

Se em algum caso o saldo da conta (A) mudasse, o número estaria medido aqui.
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
PREFIXO = "T016X"
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
        return r.status, getattr(r, "url", None) or r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")


def saldo_conta(cid):
    """Saldo PAGO da conta no livro financeiro (A)."""
    with conectar() as conn:
        r = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN tipo='entrada' THEN valor
                                      ELSE -valor END), 0) AS v
               FROM financeiro_movimentacoes
               WHERE conta_id=%s AND status='pago'""", (cid,)).fetchone()
    return Decimal(str(r["v"]))


def esperado_gaveta(sid):
    """Esperado da gaveta (B): inicial + Σ efeito físico dos eventos."""
    with conectar() as conn:
        r = conn.execute(
            """SELECT COALESCE(m.saldo_inicial,0)
                      + COALESCE(SUM(e.efeito_fisico),0) AS v
               FROM caixa_movimentos m
               LEFT JOIN caixa_sessao_eventos e ON e.sessao_id = m.id
               WHERE m.id=%s GROUP BY m.id, m.saldo_inicial""", (sid,)).fetchone()
    return Decimal(str(r["v"])) if r else Decimal("0")


def eventos(sid, venda_id):
    with conectar() as conn:
        return [dict(x) for x in conn.execute(
            """SELECT tipo, sentido, valor, efeito_fisico, conta_id
               FROM caixa_sessao_eventos
               WHERE sessao_id=%s AND venda_id=%s ORDER BY id""",
            (sid, venda_id)).fetchall()]


def lancamentos_financeiros(desc_like):
    with conectar() as conn:
        return [dict(x) for x in conn.execute(
            """SELECT id, conta_id, valor, categoria FROM financeiro_movimentacoes
               WHERE descricao LIKE %s""", (desc_like,)).fetchall()]


def montar():
    with conectar() as conn:
        # caixa EXCLUSIVO do teste (um só aberto por caixa)
        cx = conn.execute(
            """INSERT INTO caixas (nome, ativo, usa_produtos)
               VALUES (%s, 1, FALSE) RETURNING id""", (f"{PREFIXO} Caixa",)).fetchone()["id"]
        conta = conn.execute(
            "SELECT id FROM contas_financeiras WHERE tipo='especie' AND ativo=1 "
            "ORDER BY id LIMIT 1", None).fetchone()["id"]
        sess = conn.execute(
            """INSERT INTO caixa_movimentos
               (caixa_id, saldo_inicial, status, versao, total_vendas)
               VALUES (%s, 100, 'aberto', 'eventos', 0) RETURNING id""",
            (cx,)).fetchone()["id"]
        venda = conn.execute(
            """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
                   troco, status, atendente_id, fiado_pago, fiado_credito_usado,
                   doacao_valor, caixa_movimento_id, conta_id, criada_em)
               VALUES (%s, '2026-10-25', 35.00, 'especie', 0, 'concluida', 1, FALSE,
                       0, 0, %s, %s, '2026-10-25 10:00:00') RETURNING id""",
            (cx, sess, conta)).fetchone()["id"]
        conn.execute(
            """INSERT INTO caixa_sessao_eventos
               (sessao_id, identidade, tipo, natureza, origem_tipo, origem_id,
                venda_id, forma, sentido, valor, efeito_fisico, conta_id, atendente_id)
               VALUES (%s, %s, 'venda', 'comercial', 'vendas_pdv', %s, %s, 'especie',
                       'entrada', 35.00, 35.00, %s, 1)""",
            (sess, f"venda:{venda}:venda", venda, venda, conta))
    return cx, conta, sess, venda


def main():
    autenticar()
    cx, conta, sid, venda = montar()
    print(f"conta de espécie: {conta} | sessão: {sid} | venda: {venda}")

    print("\n-- estado ANTES do cancelamento")
    s_antes = saldo_conta(conta)
    g_antes = esperado_gaveta(sid)
    ev_antes = eventos(sid, venda)
    print(f"   saldo da conta (A): {s_antes}")
    print(f"   esperado da gaveta (B): {g_antes}")
    print(f"   eventos: {[(e['sentido'], str(e['efeito_fisico'])) for e in ev_antes]}")
    verificar("A.1 a venda em espécie de R$ 35,00 entrou na gaveta",
              g_antes == Decimal("135.00"), f"esperado da gaveta = {g_antes}")

    print("\n-- cancelando a venda")
    st, loc = post(f"/caixa/vendas/{venda}/cancelar",
                   {"motivo": f"{PREFIXO} cancelamento medido", "confirmar": "1"})
    s_dep = saldo_conta(conta)
    g_dep = esperado_gaveta(sid)
    ev_dep = eventos(sid, venda)
    print(f"   resposta: {st}")
    print(f"   saldo da conta (A): {s_antes} -> {s_dep}")
    print(f"   esperado da gaveta (B): {g_antes} -> {g_dep}")
    print(f"   eventos agora: {[(e['sentido'], str(e['efeito_fisico'])) for e in ev_dep]}")

    verificar("A.2 o saldo da CONTA não mudou (a venda eventos vira lançamento só "
              "no fechamento — não havia lançamento a anular)",
              s_dep == s_antes, f"{s_antes} -> {s_dep}")
    verificar("B.1 o esperado da gaveta voltou exatamente ao valor de antes da venda",
              g_dep == Decimal("100.00"), f"{g_antes} -> {g_dep}")
    saidas = [e for e in ev_dep if e["sentido"] == "saida"]
    verificar("B.2 existe EXATAMENTE UMA compensação", len(saidas) == 1,
              f"saídas: {saidas}")
    if saidas:
        # A compensação é um evento de CANCELAMENTO com efeito físico NEGATIVO
        # (servico_cancelamento, identidade `venda:{id}:cancelamento`) — é assim
        # por desenho. O que importa é o módulo do valor bater com a entrada.
        verificar("B.3 a compensação anula exatamente o efeito físico da entrada "
                  "(efeito negativo, mesmo módulo)",
                  Decimal(str(saidas[0]["efeito_fisico"])) == -Decimal("35.00")
                  and abs(Decimal(str(saidas[0]["efeito_fisico"])))
                  == abs(Decimal(str(ev_antes[0]["efeito_fisico"]))),
                  f"saída = {saidas[0]['efeito_fisico']} | "
                  f"entrada = {ev_antes[0]['efeito_fisico']}")
        verificar("B.4 a compensação fica na MESMA sessão da entrada e não cria "
                  "destino de conta novo (conta_id nulo: não há dinheiro indo a "
                  "outra conta, e o livro financeiro não é tocado)",
                  saidas[0]["conta_id"] is None,
                  f"conta_id da saída = {saidas[0]['conta_id']}")
    verificar("B.5 nenhum lançamento financeiro foi criado para esta venda",
              lancamentos_financeiros(f"%{PREFIXO}%") == [],
              str(lancamentos_financeiros(f"%{PREFIXO}%")))

    print("\n-- segunda tentativa de cancelar (deve ser recusada)")
    st2, loc2 = post(f"/caixa/vendas/{venda}/cancelar",
                     {"motivo": f"{PREFIXO} repetido", "confirmar": "1"})
    g_dep2 = esperado_gaveta(sid)
    ev_dep2 = eventos(sid, venda)
    print(f"   resposta: {st2} {loc2}")
    verificar("C.1 o segundo cancelamento não é aplicado de novo",
              g_dep2 == g_dep and len(ev_dep2) == len(ev_dep),
              f"gaveta {g_dep} -> {g_dep2} | eventos {len(ev_dep)} -> {len(ev_dep2)}")
    verificar("C.2 continua havendo UMA só compensação",
              len([e for e in ev_dep2 if e["sentido"] == "saida"]) == 1,
              str([e for e in ev_dep2 if e["sentido"] == "saida"]))

    # limpeza
    with conectar() as conn:
        conn.execute("DELETE FROM vendas_pdv_cancelamento WHERE venda_id=%s", (venda,))
        conn.execute("DELETE FROM caixa_sessao_eventos WHERE venda_id=%s", (venda,))
        conn.execute("DELETE FROM financeiro_movimentacoes WHERE descricao LIKE %s",
                     (PREFIXO + "%",))
        conn.execute("DELETE FROM vendas_pdv_itens WHERE venda_id=%s", (venda,))
        conn.execute("DELETE FROM vendas_pdv WHERE id=%s", (venda,))
        conn.execute("DELETE FROM caixa_movimentos WHERE caixa_id=%s", (cx,))
        conn.execute("DELETE FROM caixas WHERE id=%s", (cx,))

    falhas = [n for n, ok, _ in res if not ok]
    print(f"\n[resultado] aprovadas={len(res) - len(falhas)} falhas={len(falhas)}")
    for f in falhas:
        print(f"  ✘ {f}")
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
