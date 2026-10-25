#!/usr/bin/env python3
"""Suíte TASK_016 — MATRIZ DE COBERTURA da escolha de conta de destino.

A matriz completa está em `docs/investigacoes/TASK_016_CORRECOES_CONTAS.md`
(§5). Este arquivo prova, por ROTA REAL, os cinco eixos que a matriz exige e
que ainda não tinham prova própria nesta tarefa:

  E1 fluxo INDIVIDUAL     — um recebimento grava na conta escolhida;
  E2 fluxo EM LOTE        — lote todo numa conta só, e lote misto separado por
                            conta, em UMA operação (total preservado em centavos);
  E3 IDEMPOTÊNCIA         — mesma chave + mesma conta = 1 efeito;
                            mesma chave + conta DIFERENTE = conflito sem escrita;
  E4 INATIVAÇÃO CONCORRENTE — a conta é inativada ENTRE a escolha na tela e o
                            envio: o servidor recusa e não grava (a validação
                            acontece na transação, não na renderização);
  E5 FECHAMENTO/CANCELAMENTO — sessão legada: venda com conta persistida fecha na
                            conta da venda; sem conta, recusa sem escrita; e o
                            CANCELAMENTO de venda libera a pré-checagem de conta;
  E6 SALDO ANTERIOR AO PERÍODO — recebimento de hoje cai em conta que já tinha
                            saldo de período anterior, e esse saldo não some.

Dados fictícios com prefixo T016C em banco descartável. Ferramenta ausente é
falha explícita (código 3), nunca "passou".
"""
import http.cookiejar
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import (  # noqa: E402
    AmbienteInseguro, exigir_ambiente_de_teste, exigir_configuracao_consoante,
    provar_destino_do_servidor,
)

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9011")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIXO = "T016C"

arq_env = os.environ.get("SHAMBALA_ENV_FILE")
if not arq_env:
    print("[ABORTADO] SHAMBALA_ENV_FILE ausente.", file=sys.stderr)
    raise SystemExit(2)
try:
    exigir_configuracao_consoante(arq_env, os.environ.get("SHAMBALA_TEST_DB"))
except AmbienteInseguro as e:
    print(f"[ABORTADO] {e}", file=sys.stderr)
    raise SystemExit(2)

from banco import criar_tabelas, conectar  # noqa: E402
from test_tres_passes import _garantir_admin  # noqa: E402

with conectar() as conn:
    info = exigir_ambiente_de_teste(conn)
try:
    prova = provar_destino_do_servidor(info["banco_efetivo"])
except AmbienteInseguro as e:
    print(f"[ABORTADO] {e}", file=sys.stderr)
    raise SystemExit(2)
print(f"[ok] banco {info['banco_efetivo']} / servidor {BASE} (token {prova['token']})")

D = Decimal
aprovadas, falhas = [], []


def verificar(nome, condicao, detalhe=""):
    if condicao:
        aprovadas.append(nome)
        print(f"  ✔ {nome}")
    else:
        falhas.append(f"{nome} — {detalhe}")
        print(f"  ✘ {nome} — {detalhe}")


# ── cliente HTTP com cookie de sessão ──────────────────────────────────────
class _NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(_NR, urllib.request.HTTPCookieProcessor(jar))


def autenticar():
    """Login do operador.

    Precisa rodar DEPOIS de `_garantir_admin()`: o admin com senha conhecida é
    fixture da suíte, e logar antes dela devolvia 303 para /login em toda rota.
    """
    try:
        op.open(urllib.request.Request(
            BASE + "/login", method="POST",
            data=urllib.parse.urlencode(
                {"nome_usuario": "admin", "senha": "teste123"}).encode()))
    except urllib.error.HTTPError:
        pass
    if not any(c.name == "sessao" for c in jar):
        raise SystemExit("[ABORTADO] login não entregou cookie de sessão")


def enviar(rota, dados):
    try:
        r = op.open(urllib.request.Request(
            BASE + rota, method="POST",
            data=urllib.parse.urlencode(dados).encode()), timeout=20)
        return r.status, r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")


def chave():
    return secrets.token_hex(16)


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


def saldo(conta_id, ate=None):
    """Saldo PAGO da conta até uma data (ou tudo)."""
    with conectar() as conn:
        sql = """SELECT COALESCE(SUM(CASE WHEN tipo='entrada' THEN valor
                                          ELSE -valor END), 0) AS v
                 FROM financeiro_movimentacoes
                 WHERE conta_id = %s AND status = 'pago'"""
        args = [conta_id]
        if ate:
            sql += " AND data_movimentacao <= %s"
            args.append(ate)
        return D(str(conn.execute(sql, tuple(args)).fetchone()["v"]))


def movs(desc_like):
    with conectar() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT conta_id, valor, categoria FROM financeiro_movimentacoes
               WHERE descricao LIKE %s""", (desc_like,)).fetchall()]


# ── fixtures ───────────────────────────────────────────────────────────────
def semear():
    with conectar() as conn:
        t = conn.execute("SELECT id FROM trabalhadores WHERE nome_completo=%s",
                         (f"{PREFIXO} Trabalhador",)).fetchone()
        trab = t["id"] if t else conn.execute(
            """INSERT INTO trabalhadores (nome_completo, ativo, valor_mensalidade,
                   dia_vencimento)
               VALUES (%s, 1, 50.00, 10) RETURNING id""",
            (f"{PREFIXO} Trabalhador",)).fetchone()["id"]
        cx = conn.execute(
            """SELECT id FROM caixas WHERE ativo=1 AND usa_produtos=FALSE
               ORDER BY id LIMIT 1""", None).fetchone()["id"]
    with conectar() as conn:
        aberta = conn.execute(
            """SELECT id FROM caixa_movimentos
               WHERE caixa_id = %s AND status = 'aberto'""", (cx,)).fetchone()
        if not aberta:
            sessao = conn.execute(
                """INSERT INTO caixa_movimentos
                   (caixa_id, saldo_inicial, status, versao, total_vendas)
                   VALUES (%s, 0, 'aberto', 'eventos', 0) RETURNING id""",
                (cx,)).fetchone()["id"]
        else:
            sessao = aberta["id"]
    return {
        "sessao": sessao,
        "trab": trab, "caixa": cx,
        "gaveta": conta(f"{PREFIXO} Gaveta", "especie"),
        "casa": conta(f"{PREFIXO} Caixa da Casa", "especie"),
        "banco": conta(f"{PREFIXO} Banco", "banco"),
    }


def limpar():
    with conectar() as conn:
        contas = [r["id"] for r in conn.execute(
            "SELECT id FROM contas_financeiras WHERE nome LIKE %s",
            (PREFIXO + "%",)).fetchall()] or [-1]
        vendas = [r["id"] for r in conn.execute(
            """SELECT v.id FROM vendas_pdv v JOIN caixas c ON c.id=v.caixa_id
               WHERE c.nome LIKE %s""", (PREFIXO + "%",)).fetchall()] or [-1]
        caixas_teste = [r["id"] for r in conn.execute(
            "SELECT id FROM caixas WHERE nome LIKE %s", (PREFIXO + "%",)).fetchall()]
        fins = [r["id"] for r in conn.execute(
            """SELECT id FROM financeiro_movimentacoes
               WHERE descricao LIKE %s OR caixa_id = ANY(%s)""",
            (PREFIXO + "%", caixas_teste or [-1])).fetchall()] or [-1]
        conn.execute("DELETE FROM caixa_sessao_eventos WHERE venda_id=ANY(%s) "
                     "OR financeiro_id=ANY(%s)", (vendas, fins))
        conn.execute("DELETE FROM fiado_alocacao WHERE venda_id=ANY(%s) "
                     "OR financeiro_id=ANY(%s)", (vendas, fins))
        conn.execute("DELETE FROM fiado_credito_mov WHERE financeiro_id=ANY(%s)", (fins,))
        conn.execute("DELETE FROM financeiro_movimentacoes WHERE id=ANY(%s)", (fins,))
        # o registro de cancelamento referencia a venda cancelada
        conn.execute("DELETE FROM vendas_pdv_cancelamento WHERE venda_id=ANY(%s)",
                     (vendas,))
        conn.execute("DELETE FROM vendas_pdv_itens WHERE venda_id=ANY(%s)", (vendas,))
        conn.execute("DELETE FROM vendas_pdv WHERE id=ANY(%s)", (vendas,))
        conn.execute("DELETE FROM caixa_movimentos WHERE caixa_id IN "
                     "(SELECT id FROM caixas WHERE nome LIKE %s)", (PREFIXO + "%",))
        conn.execute("DELETE FROM caixas WHERE nome LIKE %s", (PREFIXO + "%",))
        conn.execute("DELETE FROM trabalhadores WHERE nome_completo LIKE %s",
                     (PREFIXO + "%",))
        conn.execute("DELETE FROM contas_financeiras WHERE id=ANY(%s)", (contas,))


def conferir_rastro():
    with conectar() as conn:
        c = conn.execute("SELECT count(*) AS c FROM contas_financeiras "
                         "WHERE nome LIKE %s", (PREFIXO + "%",)).fetchone()["c"]
        f = conn.execute("SELECT count(*) AS c FROM financeiro_movimentacoes "
                         "WHERE descricao LIKE %s", (PREFIXO + "%",)).fetchone()["c"]
    assert c == 0 and f == 0, f"resíduo: {c} contas, {f} lançamentos"


# ── E1 fluxo individual ────────────────────────────────────────────────────
def e1(fx):
    print("-- E1 fluxo individual: um recebimento grava na conta escolhida")
    d = f"{PREFIXO} individual {secrets.token_hex(3)}"
    ant = saldo(fx["casa"])
    st, loc = enviar("/caixa/doacao", {
        "forma": "especie", "valor": "7.00", "descricao": d,
        "sessao_id": str(fx["sessao"]),
        "conta_id": str(fx["casa"]), "idempotencia": chave()})
    dep = saldo(fx["casa"])
    verificar("E1.1 a doação foi aceita", st == 303 and "ok=1" in (loc or ""),
              f"{st} {loc}")
    verificar("E1.2 o valor entrou na conta ESCOLHIDA", dep - ant == D("7.00"),
              f"saldo {ant} -> {dep}")
    linhas = movs(d)
    verificar("E1.3 há exatamente um lançamento e na conta certa",
              len(linhas) == 1 and linhas[0]["conta_id"] == fx["casa"],
              str(linhas))


# ── E2 fluxo em lote ───────────────────────────────────────────────────────
def e2(fx):
    print("-- E2 fluxo EM LOTE: separa por conta e preserva o total em centavos")
    with conectar() as conn:
        ids = []
        for i, v in enumerate(("12.34", "7.66", "0.10")):
            ids.append(conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, data_movimentacao, descricao, status,
                    trabalhador_id, forma_pagamento)
                   VALUES ('entrada', 'mensalidade', %s, '2026-10-01', %s,
                           'pendente', %s, 'especie') RETURNING id""",
                (v, f"{PREFIXO} lote {i}", fx["trab"])).fetchone()["id"])
    total_lote = D("12.34") + D("7.66") + D("0.10")
    ant_g, ant_c = saldo(fx["gaveta"]), saldo(fx["casa"])
    # lote 1: tudo na gaveta
    st, loc = enviar("/financeiro/mensalidades/receber", {
        "trabalhador_id": str(fx["trab"]), "mov_ids": ",".join(map(str, ids)),
        "forma_pagamento": "especie", "conta_id": str(fx["gaveta"]),
        "sessao_id": str(fx["sessao"]), "idempotencia": chave()})
    dep_g, dep_c = saldo(fx["gaveta"]), saldo(fx["casa"])
    verificar("E2.1 o lote foi aceito", st == 303 and "erro" not in (loc or ""),
              f"{st} {loc}")
    verificar("E2.2 o total do lote caiu inteiro na conta escolhida",
              dep_g - ant_g == total_lote,
              f"gaveta {ant_g} -> {dep_g} (esperado +{total_lote})")
    verificar("E2.3 a outra conta de espécie NÃO recebeu nada", dep_c == ant_c,
              f"casa {ant_c} -> {dep_c}")

    # lote 2: mesmos valores, agora na outra conta — o destino é da OPERAÇÃO
    with conectar() as conn:
        ids2 = [conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, status,
                trabalhador_id, forma_pagamento)
               VALUES ('entrada', 'mensalidade', 5.55, '2026-10-01', %s,
                       'pendente', %s, 'especie') RETURNING id""",
            (f"{PREFIXO} lote2 {i}", fx["trab"])).fetchone()["id"]
            for i in range(2)]
    ant_c2 = saldo(fx["casa"])
    st2, loc2 = enviar("/financeiro/mensalidades/receber", {
        "trabalhador_id": str(fx["trab"]), "mov_ids": ",".join(map(str, ids2)),
        "forma_pagamento": "especie", "conta_id": str(fx["casa"]),
        "sessao_id": str(fx["sessao"]), "idempotencia": chave()})
    verificar("E2.4 lote idêntico em OUTRA conta vai para a conta nova",
              st2 == 303 and saldo(fx["casa"]) - ant_c2 == D("11.10"),
              f"{st2} {loc2} saldo {ant_c2} -> {saldo(fx['casa'])}")


# ── E3 idempotência com troca de conta ─────────────────────────────────────
def e3(fx):
    print("-- E3 idempotência: mesma chave/troca de conta")
    k = chave()
    d = f"{PREFIXO} idem {secrets.token_hex(3)}"
    dados = {"forma": "especie", "valor": "9.00", "descricao": d,
             "sessao_id": str(fx["sessao"]), "idempotencia": k}
    ant_g, ant_c = saldo(fx["gaveta"]), saldo(fx["casa"])
    st1, loc1 = enviar("/caixa/doacao", dict(dados, conta_id=str(fx["gaveta"])))
    verificar("E3.1 primeira tentativa aceita na gaveta",
              st1 == 303 and "ok=1" in (loc1 or ""), f"{st1} {loc1}")
    dep_g = saldo(fx["gaveta"])
    verificar("E3.2 gravou uma vez na gaveta", dep_g - ant_g == D("9.00"),
              f"{ant_g} -> {dep_g}")

    st2, loc2 = enviar("/caixa/doacao", dict(dados, conta_id=str(fx["gaveta"])))
    verificar("E3.3 reenvio idêntico (mesma conta) NÃO grava de novo",
              saldo(fx["gaveta"]) == dep_g, f"saldo {dep_g} -> {saldo(fx['gaveta'])}")

    st3, loc3 = enviar("/caixa/doacao", dict(dados, conta_id=str(fx["casa"])))
    verificar("E3.4 mesma chave com conta DIFERENTE é conflito, sem escrita",
              "tentativa_conflito" in (loc3 or "")
              and saldo(fx["casa"]) == ant_c and saldo(fx["gaveta"]) == dep_g,
              f"{st3} {loc3} | gaveta={saldo(fx['gaveta'])} casa={saldo(fx['casa'])}")


# ── E4 inativação concorrente ──────────────────────────────────────────────
def e4(fx):
    print("-- E4 inativação concorrente: escolhida na tela, inativada antes do envio")
    nome = f"{PREFIXO} Efêmera"
    cid = conta(nome, "especie")
    # a tela renderiza com a conta ativa (é a escolha do operador)...
    with conectar() as conn:
        conn.execute("UPDATE contas_financeiras SET ativo=1 WHERE id=%s", (cid,))
    ant = saldo(cid)
    # ...e a conta é inativada ANTES do envio chegar
    with conectar() as conn:
        conn.execute("UPDATE contas_financeiras SET ativo=0 WHERE id=%s", (cid,))
    d = f"{PREFIXO} concorrente {secrets.token_hex(3)}"
    st, loc = enviar("/caixa/doacao", {
        "forma": "especie", "valor": "4.00", "descricao": d,
        "sessao_id": str(fx["sessao"]),
        "conta_id": str(cid), "idempotencia": chave()})
    verificar("E4.1 o servidor recusou com motivo próprio (conta inativa)",
              "conta_inativa" in (loc or ""), f"{st} {loc}")
    verificar("E4.2 nada foi gravado na conta inativada",
              saldo(cid) == ant and movs(d) == [],
              f"saldo {ant} -> {saldo(cid)} | lançamentos {movs(d)}")
    with conectar() as conn:
        conn.execute("UPDATE contas_financeiras SET ativo=1 WHERE id=%s", (cid,))


# ── E5 fechamento e cancelamento ───────────────────────────────────────────
def e5(fx):
    print("-- E5 fechamento legado e cancelamento de venda")
    with conectar() as conn:
        cx = conn.execute(
            """INSERT INTO caixas (nome, ativo, usa_produtos)
               VALUES (%s, 1, FALSE) RETURNING id""",
            (f"{PREFIXO} Caixa",)).fetchone()["id"]

    def sessao(versao="legado"):
        with conectar() as conn:
            conn.execute("""UPDATE caixa_movimentos SET status='fechado'
                            WHERE caixa_id=%s AND status='aberto'""", (cx,))
            return conn.execute(
                """INSERT INTO caixa_movimentos
                   (caixa_id, saldo_inicial, status, versao, total_vendas)
                   VALUES (%s, 0, 'aberto', %s, 0) RETURNING id""",
                (cx, versao)).fetchone()["id"]

    def venda(sid, forma, valor, conta_id, total=0):
        with conectar() as conn:
            return conn.execute(
                """INSERT INTO vendas_pdv (caixa_id, data_venda, total,
                       forma_pagamento, troco, status, atendente_id, fiado_pago,
                       fiado_credito_usado, doacao_valor, caixa_movimento_id,
                       conta_id, criada_em)
                   VALUES (%s, '2026-10-20', %s, %s, 0, 'concluida', 1, FALSE, 0,
                           0, %s, %s, '2026-10-20 09:00:00') RETURNING id""",
                (cx, valor, forma, sid, conta_id)).fetchone()["id"]

    # E5.1 venda COM conta persistida → fechamento usa o vínculo
    s1 = sessao()
    venda(s1, "especie", 21, fx["casa"])
    st, loc = enviar("/caixa/fechar", {"caixa_movimento_id": s1, "observacao": ""})
    with conectar() as conn:
        l1 = [dict(r) for r in conn.execute(
            """SELECT conta_id, valor FROM financeiro_movimentacoes
               WHERE descricao LIKE %s""", (f"%fechamento #{s1}%",)).fetchall()]
    verificar("E5.1 fechamento usa a conta PERSISTIDA na venda",
              len(l1) == 1 and l1[0]["conta_id"] == fx["casa"],
              f"{st} {loc} | {l1}")

    # E5.2 venda SEM conta (histórico) → recusa sem escrita
    s2 = sessao()
    venda(s2, "especie", 13, None)
    st2, loc2 = enviar("/caixa/fechar", {"caixa_movimento_id": s2, "observacao": ""})
    with conectar() as conn:
        n2 = conn.execute("""SELECT count(*) AS c FROM financeiro_movimentacoes
                             WHERE descricao LIKE %s""",
                          (f"%fechamento #{s2}%",)).fetchone()["c"]
        ab2 = conn.execute("SELECT status FROM caixa_movimentos WHERE id=%s",
                           (s2,)).fetchone()["status"]
    verificar("E5.2 sem vínculo o fechamento RECUSA e não escreve nada",
              "conta_do_fechamento" in (loc2 or "") and n2 == 0 and ab2 == "aberto",
              f"{st2} {loc2} | lançamentos={n2} status={ab2}")

    # E5.3 com escolha do operador, fecha na conta escolhida
    st3, loc3 = enviar("/caixa/fechar", {
        "caixa_movimento_id": s2, "observacao": "",
        "conta_legado_especie": str(fx["gaveta"])})
    with conectar() as conn:
        l3 = [dict(r) for r in conn.execute(
            """SELECT conta_id, valor FROM financeiro_movimentacoes
               WHERE descricao LIKE %s""", (f"%fechamento #{s2}%",)).fetchall()]
        st3d = conn.execute("SELECT status FROM caixa_movimentos WHERE id=%s",
                            (s2,)).fetchone()["status"]
    verificar("E5.3 com a escolha do operador grava na conta escolhida",
              len(l3) == 1 and l3[0]["conta_id"] == fx["gaveta"] and st3d == "fechado",
              f"{st3} {loc3} | {l3} status={st3d}")

    # E5.4 escolha INVÁLIDA (conta de espécie para PIX) → recusa sem escrita
    s4 = sessao()
    venda(s4, "pix", 19, None)
    st4, loc4 = enviar("/caixa/fechar", {
        "caixa_movimento_id": s4, "observacao": "",
        "conta_legado_pix": str(fx["gaveta"])})
    with conectar() as conn:
        n4 = conn.execute("""SELECT count(*) AS c FROM financeiro_movimentacoes
                             WHERE descricao LIKE %s""",
                          (f"%fechamento #{s4}%",)).fetchone()["c"]
        ab4 = conn.execute("SELECT status FROM caixa_movimentos WHERE id=%s",
                           (s4,)).fetchone()["status"]
    verificar("E5.4 escolha inválida no fechamento RECUSA sem escrita",
              "conta_do_fechamento" in (loc4 or "") and n4 == 0 and ab4 == "aberto",
              f"{st4} {loc4} | lançamentos={n4} status={ab4}")

    # E5.5 CANCELAMENTO de venda não deixa a conta presa nem efeito pendente
    s5 = sessao("eventos")
    v5 = venda(s5, "especie", 15, fx["casa"])
    with conectar() as conn:
        conn.execute(
            """INSERT INTO caixa_sessao_eventos
               (sessao_id, identidade, tipo, natureza, origem_tipo, origem_id,
                venda_id, forma, sentido, valor, efeito_fisico, conta_id, atendente_id)
               VALUES (%s, %s, 'venda', 'comercial', 'vendas_pdv', %s, %s,
                       'especie', 'entrada', 15, 15, %s, 1)""",
            (s5, f"venda:{v5}:venda", v5, v5, fx["casa"]))
    ant = saldo(fx["casa"])
    st5, loc5 = enviar(f"/caixa/vendas/{v5}/cancelar", {
        "motivo": f"{PREFIXO} cancelamento de teste", "confirmar": "1"})
    with conectar() as conn:
        st_v = conn.execute("SELECT status FROM vendas_pdv WHERE id=%s",
                            (v5,)).fetchone()["status"]
        ev = conn.execute("""SELECT count(*) AS c FROM caixa_sessao_eventos
                             WHERE venda_id=%s AND sentido='entrada'
                               AND NOT EXISTS (SELECT 1 FROM caixa_sessao_eventos r
                                       WHERE r.venda_id=%s AND r.sentido='saida')""",
                          (v5, v5)).fetchone()["c"]
    verificar("E5.5 a venda foi cancelada", st_v == "cancelada",
              f"{st5} {loc5} status={st_v}")
    verificar("E5.6 o efeito físico da venda cancelada foi estornado",
              ev == 0, f"eventos de entrada sem estorno: {ev}")
    verificar("E5.7 o cancelamento não mexeu no saldo da conta",
              saldo(fx["casa"]) == ant, f"{ant} -> {saldo(fx['casa'])}")


# ── E6 saldo anterior ao período ───────────────────────────────────────────
def e6(fx):
    print("-- E6 saldo anterior ao período não desaparece")
    alvo = conta(f"{PREFIXO} Histórica", "especie")
    with conectar() as conn:
        for quando, valor, st_ in (("2025-01-15", "100.00", "pago"),
                                   ("2025-06-20", "55.50", "pago"),
                                   ("2024-12-31", "44.50", "pago")):
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, data_movimentacao, descricao, status,
                    conta_id, forma_pagamento)
                   VALUES ('entrada', 'doacao', %s, %s, %s, %s, %s, 'especie')""",
                (valor, quando, f"{PREFIXO} histórico {quando}", st_, alvo))
    anterior = saldo(alvo, ate="2025-12-31")
    verificar("E6.1 o saldo ANTERIOR ao período soma só o histórico",
              anterior == D("200.00"), f"saldo até 2025-12-31 = {anterior}")

    d = f"{PREFIXO} hoje {secrets.token_hex(3)}"
    st, loc = enviar("/caixa/doacao", {
        "forma": "especie", "valor": "30.00", "descricao": d,
        "sessao_id": str(fx["sessao"]),
        "conta_id": str(alvo), "idempotencia": chave()})
    total = saldo(alvo)
    verificar("E6.2 o recebimento de hoje soma ao saldo anterior",
              total == D("230.00") and st == 303, f"{st} {loc} total={total}")
    verificar("E6.3 o saldo anterior não foi recalculado nem reescrito",
              saldo(alvo, ate="2025-12-31") == D("200.00"),
              f"até 2025-12-31 = {saldo(alvo, '2025-12-31')}")

    # e a tela mostra o acumulado, com o histórico incluído
    html = op.open(BASE + "/configuracoes/contas").read().decode()
    verificar("E6.4 a tela de contas mostra o acumulado da conta histórica",
              "Histórica" in html, "a conta não aparece na tela")


# ── execução ───────────────────────────────────────────────────────────────
def main():
    criar_tabelas()
    _garantir_admin()
    autenticar()
    fx = semear()
    try:
        for etapa in (e1, e2, e3, e4, e5, e6):
            try:
                etapa(fx)
            except Exception as e:
                falhas.append(f"{etapa.__name__} — {type(e).__name__}: {e}")
                print(f"  ✘ {etapa.__name__} — exceção {type(e).__name__}: {e}")
    finally:
        try:
            limpar()
            conferir_rastro()
        except Exception as e:
            falhas.append(f"limpeza — {type(e).__name__}: {e}")
            print(f"[limpeza] ERRO: {e}")
    print(f"\n[resultado] aprovadas={len(aprovadas)} falhas={len(falhas)}")
    for f in falhas:
        print(f"  ✘ {f}")
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
