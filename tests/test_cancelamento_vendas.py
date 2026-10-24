#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suíte TASK_007 — cancelamento de vendas com reversão consistente e rastreável.

Cobre a matriz de elegibilidade pelas ROTAS REAIS autenticadas, inspecionando
o banco antes/depois com valores fictícios conhecidos. Reutiliza os fixtures
da suíte da TASK_006 (mesmo PDV prefixado, mesma sessão de teste), preservando
a regressão daquela entrega (executada separadamente pelo runner).

Testes pulados por falta de ferramenta NÃO contam como aprovados: a suíte
encerra com código 2 e o runner registra PULADO.
"""

import os
import re
import sys
import threading
import traceback
import urllib.parse
import urllib.request
import http.cookiejar
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_tres_passes as tp  # noqa: E402
from test_tres_passes import (  # noqa: E402
    assert_eq, preparar_banco, _garantir_admin, _login, BASE,
)
import test_pdv_precos_servidor as t6  # noqa: E402
from banco import conectar  # noqa: E402
import servico_cancelamento as sc  # noqa: E402

PREFIXO = t6.PREFIXO
RESULTADOS = []
ADMIN_ID = None
REGISTRO_INSTANTE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def teste(nome):
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


def pdv():
    p = t6.pdv()
    # A suíte tem 25 testes e o fixture divide catálogo: repõe o estoque no
    # início de cada teste para a disponibilidade não ser acasode execução.
    with conectar() as conn:
        conn.execute(
            "UPDATE produtos SET quantidade_estoque = 100 "
            "WHERE nome LIKE %s AND ativo = 1", (f"{PREFIXO}%",))
    return p


def _dec(v):
    return Decimal(str(v if v is not None else 0))


# ── helpers ──────────────────────────────────────────────────────────────────

def _jar(novo=False):
    return t6._jar(novo)


def _login_novo(usuario="admin", senha="teste123"):
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        t6._SemRedirect())
    t6._req(opener, "POST", "/login", {"nome_usuario": usuario, "senha": senha})
    return opener


def _nova_sessao(p, caixa=None):
    # TASK_008: no máximo UMA sessão aberta por caixa — fecha a anterior antes
    alvo = caixa or p.catalogo
    with conectar() as conn:
        conn.execute(
            "UPDATE caixa_movimentos SET status = 'fechado' "
            "WHERE caixa_id = %s AND status = 'aberto'", (alvo,))
        cur = conn.execute(
            "INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status) "
            "VALUES (%s, 0, 'aberto') RETURNING id", (alvo,))
        return cur.fetchone()["id"]


def _venda(p, itens, sess, forma="especie", recebido="0", doacao="0", trab=0, caixa=None):
    r = t6._finalizar(caixa_id=caixa or p.catalogo, caixa_movimento_id=sess,
                      itens=itens, forma_pagamento=forma, valor_recebido=recebido,
                      doacao_valor=doacao, trabalhador_id=trab)
    assert r.ok, f"finalização falhou: erro={r.erro} destino={r.destino}"
    return int(r.ok)


def _linha(pid, qtd, preco):
    return t6._linha(pid, qtd, preco)


def _trabalhador(nome, limite=0, credito=0):
    with conectar() as conn:
        cur = conn.execute(
            "INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, fiado_credito) "
            "VALUES (%s, 1, %s, %s) RETURNING id",
            (f"{PREFIXO} {nome}", limite, Decimal(str(credito))))
        return cur.fetchone()["id"]


def _cancelar(venda_id, motivo="a venda foi registrada duas vezes", confirmar="1",
              opener=None, extras=None):
    dados = {"motivo": motivo, "confirmar": confirmar}
    if extras:
        dados.update(extras)
    st, corpo, loc = t6._req(opener or _jar(), "POST",
                             f"/caixa/vendas/{venda_id}/cancelar", dados)
    return st, corpo, loc


def _snapshot(venda_id, produto_ids=(), trabalhador_id=None, sessoes=()):
    """Estado observável de tudo que um cancelamento pode tocar."""
    with conectar() as conn:
        venda = conn.execute("SELECT * FROM vendas_pdv WHERE id=%s", (venda_id,)).fetchone()
        cancelamentos = conn.execute(
            "SELECT count(*) AS c FROM vendas_pdv_cancelamento WHERE venda_id=%s",
            (venda_id,)).fetchone()["c"]
        movs = conn.execute(
            """SELECT tipo, quantidade, saldo_anterior, saldo_posterior
               FROM estoque_movimentos WHERE referencia_id=%s ORDER BY id""",
            (venda_id,)).fetchall()
        estoque = {}
        for pid in produto_ids:
            row = conn.execute(
                "SELECT quantidade_estoque, preco_venda, ativo FROM produtos WHERE id=%s",
                (pid,)).fetchone()
            estoque[pid] = None if not row else (row["quantidade_estoque"],
                                                 _dec(row["preco_venda"]), row["ativo"])
        sess = {}
        for sid in sessoes:
            row = conn.execute(
                "SELECT status, total_vendas FROM caixa_movimentos WHERE id=%s",
                (sid,)).fetchone()
            sess[sid] = None if not row else (row["status"], _dec(row["total_vendas"]))
        fin = conn.execute(
            "SELECT count(*) AS c, COALESCE(SUM(valor),0) AS s FROM financeiro_movimentacoes"
        ).fetchone()
        cred = None
        if trabalhador_id:
            c = conn.execute("SELECT fiado_credito FROM trabalhadores WHERE id=%s",
                             (trabalhador_id,)).fetchone()
            cred = _dec(c["fiado_credito"]) if c else None
            m = conn.execute("""SELECT count(*) AS c FROM fiado_credito_mov
                                WHERE trabalhador_id=%s""", (trabalhador_id,)).fetchone()
            cred = (cred, m["c"])
    return {
        "venda": dict(venda) if venda else None,
        "cancelamentos": cancelamentos,
        "movs": [tuple(str(x) for x in m.values()) for m in movs],
        "estoque": estoque,
        "sessoes": sess,
        "financeiro": (fin["c"], _dec(fin["s"])),
        "credito": cred,
    }


def _sem_efeitos(antes, depois, venda_id):
    """Compara snapshots exigindo IDENTIDADE — bloqueio não pode mover nada."""
    a, d = dict(antes), dict(depois)
    assert_eq(d, a, f"cancelamento bloqueado da venda #{venda_id} produziu efeitos")


# ── M1 — elegível à vista: reversão exata ────────────────────────────────────

@teste("M1 à vista elegível: estoque devolvido uma vez, sessão exata, documentos preservados")
def m1():
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.suco, 2, "12.50"), _linha(p.agua, 1, "3.00")],
                 sess, recebido="50.00", doacao="2.00")
    st, corpo, _ = t6._req(_jar(), "GET", f"/caixa/vendas/{vid}/cancelar")
    assert_eq(st, 200, "tela de confirmação abre")
    assert "CANCELAMENTO INTEGRAL" in corpo, "tela nomeia o comando"
    assert "R$ 28.00" in corpo and "Suco" in corpo, "tela mostra venda e itens"

    antes = _snapshot(vid, (p.suco, p.agua), sessoes=(sess,))
    st, corpo, loc = _cancelar(vid)
    assert_eq(st, 303, f"cancelamento aceito deveria redirecionar (veio {st})")
    assert "cancelado=" in loc, f"destino {loc}"

    depois = _snapshot(vid, (p.suco, p.agua), sessoes=(sess,))
    assert_eq(depois["venda"]["status"], "cancelada", "status")
    assert_eq(depois["estoque"][p.suco][0], antes["estoque"][p.suco][0] + 2, "suco devolvido")
    assert_eq(depois["estoque"][p.agua][0], antes["estoque"][p.agua][0] + 1, "agua devolvida")
    assert_eq(depois["sessoes"][sess][1], Decimal("0.00"), "sessão voltou ao zero (28+2 revertidos)")
    entradas = [m for m in depois["movs"] if m[0] == "entrada"]
    saidas = [m for m in depois["movs"] if m[0] == "saida"]
    assert_eq(len(entradas), 2, f"um movimento compensatório por item: {entradas}")
    assert_eq(len(saidas), 2, "movimentos originais preservados, não apagados")
    assert_eq(depois["cancelamentos"], 1, "prova durável única")
    with conectar() as conn:
        reg = conn.execute("SELECT * FROM vendas_pdv_cancelamento WHERE venda_id=%s",
                           (vid,)).fetchone()
        assert REGISTRO_INSTANTE.match(reg["criado_em"]), f"instante SP com segundos: {reg['criado_em']}"
        assert reg["motivo"].strip() == "a venda foi registrada duas vezes", reg["motivo"]
        assert_eq(reg["atendente_id"], ADMIN_ID, "autor registrado")
        assert_eq(_dec(reg["valor_sessao_revertido"]), Decimal("30.00"), "evidência do valor")
        itens_orig = conn.execute(
            "SELECT count(*) AS c FROM vendas_pdv_itens WHERE venda_id=%s", (vid,)).fetchone()["c"]
        assert_eq(itens_orig, 2, "itens originais intactos")


@teste("M1b comprovante e listagem identificam a cancelada sem apagar o histórico")
def m1b():
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.agua, 1, "3.00")], sess, recebido="10.00")
    _cancelar(vid, motivo="teste de comprovante")
    st, corpo, _ = t6._req(_jar(), "GET", f"/caixa/vendas/{vid}/comprovante")
    assert_eq(st, 200, "comprovante continua consultável")
    assert "V E N D A   C A N C E L A D A" in corpo, "comprovante marcado"
    assert "teste de comprovante" in corpo, "motivo visível no comprovante"
    st, corpo, _ = t6._req(_jar(), "GET", f"/caixa/vendas?caixa_id={p.catalogo}")
    assert_eq(st, 200, "listagem abre")
    assert "Cancelada" in corpo and "teste de comprovante" in corpo, "linha cancelada com motivo"
    assert "Total do dia" in corpo, "cartão de totais presente"


@teste("M2 produto repetido na cesta devolve a quantidade agregada, sem duplicar")
def m2():
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.suco, 1, "12.50"), _linha(p.suco, 1, "12.50")], sess)
    antes = _snapshot(vid, (p.suco,), sessoes=(sess,))
    with conectar() as conn:
        n_itens = conn.execute("SELECT count(*) AS c FROM vendas_pdv_itens WHERE venda_id=%s",
                               (vid,)).fetchone()["c"]
        assert_eq(n_itens, 1, "agregação da TASK_006 em uma linha")
    _cancelar(vid)
    depois = _snapshot(vid, (p.suco,), sessoes=(sess,))
    assert_eq(depois["estoque"][p.suco][0], antes["estoque"][p.suco][0] + 2, "devolve 2 de uma vez")
    entradas = [m for m in depois["movs"] if m[0] == "entrada"]
    assert_eq(len(entradas), 1, f"movimento compensatório único: {entradas}")


@teste("M3 preço mudou ou produto foi inativado depois da venda: usa o fato original")
def m3():
    p = pdv()
    pid = p.novo_produto("12.50", 10, "Suco do m3")  # produto próprio: inativá-lo
    #   não pode atrapalhar as vendas de Suco dos testes seguintes
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(pid, 2, "12.50")], sess, recebido="30.00")
    with conectar() as conn:
        conn.execute("UPDATE produtos SET preco_venda = 99.90, ativo = 0 WHERE id = %s",
                     (pid,))
    antes = _snapshot(vid, (pid,), sessoes=(sess,))
    _cancelar(vid)
    depois = _snapshot(vid, (pid,), sessoes=(sess,))
    assert_eq(depois["estoque"][pid][0], antes["estoque"][pid][0] + 2,
              "produto inativo recebe a devolução")
    assert_eq(depois["estoque"][pid][1], Decimal("99.90"), "preço atual não foi tocado")
    assert_eq(depois["estoque"][pid][2], 0, "produto continua inativo (não reativado)")
    assert_eq(depois["sessoes"][sess][1], Decimal("0.00"), "sessão usou o total de 25,00 persistido")


@teste("M4 outras vendas após a original: devolução soma no estoque atual, extrato preservado")
def m4():
    p = pdv()
    sess = _nova_sessao(p)
    v_a = _venda(p, [_linha(p.suco, 2, "12.50")], sess, recebido="30.00")
    v_b = _venda(p, [_linha(p.suco, 1, "12.50")], sess, recebido="20.00")
    _cancelar(v_a)
    with conectar() as conn:
        prod = conn.execute("SELECT quantidade_estoque FROM produtos WHERE id=%s",
                            (p.suco,)).fetchone()
        assert_eq(prod["quantidade_estoque"], 99, "100 - 2 (A) - 1 (B) + 2 (devolução A)")
        movs_b = conn.execute(
            "SELECT tipo FROM estoque_movimentos WHERE referencia_id=%s", (v_b,)).fetchall()
        assert_eq([m["tipo"] for m in movs_b], ["saida"], "B permanece com a baixa original")
        v_b_row = conn.execute("SELECT status FROM vendas_pdv WHERE id=%s", (v_b,)).fetchone()
        assert_eq(v_b_row["status"], "concluida", "B intacta")


@teste("M5 caixa de preço aberto: só sessão revertida, nenhum movimento de estoque inventado")
def m5():
    p = pdv()
    sess = _nova_sessao(p, caixa=p.livre)
    vid = _venda(p, [{"produto_id": None, "quantidade": 1, "nome": "Pregador velho",
                      "preco_unitario": 7.5, "subtotal": 7.5}],
                 sess, caixa=p.livre, recebido="10.00")
    _cancelar(vid)
    depois = _snapshot(vid, sessoes=(sess,))
    assert_eq(depois["movs"], [], "zero movimentos de estoque")
    assert_eq(depois["sessoes"][sess][1], Decimal("0.00"), "sessão ajustada")
    assert_eq(depois["venda"]["status"], "cancelada", "status final")


@teste("M6 doação vinculada some da sessão junto com a venda; doação independente intocada")
def m6():
    p = pdv()
    sess = _nova_sessao(p)
    with conectar() as conn:
        conn.execute(
            """INSERT INTO financeiro_movimentacoes (tipo, categoria, valor, descricao)
               VALUES ('entrada', 'doacao', 5.00, %s)""",
            (f"{PREFIXO} doação independente",))
    vid = _venda(p, [_linha(p.agua, 1, "3.00")], sess, recebido="10.00", doacao="4.00")
    antes_fin = _snapshot(vid)["financeiro"]
    _cancelar(vid)
    depois = _snapshot(vid, sessoes=(sess,))
    assert_eq(depois["sessoes"][sess][1], Decimal("0.00"), "7,00 revertidos (3+4)")
    with conectar() as conn:
        reg = conn.execute("SELECT doacao_venda, valor_sessao_revertido "
                           "FROM vendas_pdv_cancelamento WHERE venda_id=%s", (vid,)).fetchone()
        assert_eq(_dec(reg["doacao_venda"]), Decimal("4.00"), "doação evidenciada no registro")
        indep = conn.execute("SELECT valor FROM financeiro_movimentacoes WHERE descricao=%s",
                             (f"{PREFIXO} doação independente",)).fetchone()
        assert_eq(_dec(indep["valor"]), Decimal("5.00"), "doação avulsa intocada")
    assert_eq(depois["financeiro"], antes_fin, "nenhum lançamento financeiro novo")


# ── M7/M8 — fiado ────────────────────────────────────────────────────────────

@teste("M7 fiado elegível: sai da dívida atual por status, sem tocar crédito ou recebimentos")
def m7():
    p = pdv()
    trab = _trabalhador("fiado elegível")
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.agua, 1, "3.00")], sess, forma="fiado", trab=trab)
    antes = _snapshot(vid, (p.agua,), trabalhador_id=trab, sessoes=(sess,))
    st, corpo, loc = _cancelar(vid, motivo="fiado lançado em duplicidade")
    assert_eq(st, 303, f"fiado elegível aceito: {loc}")
    depois = _snapshot(vid, (p.agua,), trabalhador_id=trab, sessoes=(sess,))
    assert_eq(depois["venda"]["status"], "cancelada", "venda cancelada")
    assert_eq(depois["estoque"][p.agua][0], antes["estoque"][p.agua][0] + 1, "estoque devolvido")
    assert_eq(depois["credito"], antes["credito"], "crédito e movimentos de crédito intactos")
    st, corpo, _ = t6._req(_jar(), "GET", "/caixa/fiados")
    assert "fiado elegível" not in corpo, \
        "dívida atual não lista mais o trabalhador com única venda cancelada"
    st, corpo, _ = t6._req(_jar(), "GET", f"/financeiro/fiados?trabalhador_id={trab}")
    assert "Cancelada (não somada)" in corpo, "histórico cancelado visível no relatório"
    assert "R$ 0.00" in corpo, "pendente zerado nos totais"


@teste("M8a fiado pago: bloqueado antes de qualquer efeito (FIN-06 é outro fluxo)")
def m8a():
    p = pdv()
    trab = _trabalhador("fiado pago")
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.suco, 1, "12.50")], sess, forma="fiado", trab=trab)
    t6._req(_jar(), "POST", f"/caixa/fiados/{vid}/pagar",
          {"forma_pagamento": "especie", "sessao_id": str(sess)})
    antes = _snapshot(vid, (p.suco,), trabalhador_id=trab, sessoes=(sess,))
    st, corpo, _ = _cancelar(vid, motivo="tentar cancelar fiado pago")
    assert_eq(st, 200, "resposta é a tela com o motivo, não redirecionamento de sucesso")
    assert "pago" in corpo and "outro fluxo" in corpo, "motivo claro na tela"
    _sem_efeitos(antes, _snapshot(vid, (p.suco,), trabalhador_id=trab, sessoes=(sess,)), vid)


@teste("M8b pagamento parcial transformado em crédito solto: bloqueado (fiado_pago não prova nada)")
def m8b():
    p = pdv()
    trab = _trabalhador("fiado parcial")
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.suco, 1, "12.50")], sess, forma="fiado", trab=trab)
    t6._req(_jar(), "POST", f"/caixa/fiados/{trab}/pagar-divida",
            {"valor": "10.00", "forma_pagamento": "especie",
             "sessao_id": str(sess)})
    antes = _snapshot(vid, (p.suco,), trabalhador_id=trab, sessoes=(sess,))
    assert_eq(antes["venda"]["fiado_pago"], False, "venda segue não paga (flag engana)")
    st, corpo, _ = _cancelar(vid, motivo="tentar cancelar fiado com recebimento parcial")
    assert_eq(st, 200, "recusado")
    assert "movimento de crédito" in corpo or "comprovável" in corpo, "motivo claro"
    _sem_efeitos(antes,
                 _snapshot(vid, (p.suco,), trabalhador_id=trab, sessoes=(sess,)), vid)


@teste("M8c fiado que consumiu crédito antecipado: bloqueado")
def m8c():
    p = pdv()
    trab = _trabalhador("fiado com credito", credito=5)
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.suco, 1, "12.50")], sess, forma="fiado", trab=trab)
    with conectar() as conn:
        usado = _dec(conn.execute("SELECT fiado_credito_usado FROM vendas_pdv WHERE id=%s",
                                  (vid,)).fetchone()["fiado_credito_usado"])
        assert_eq(usado, Decimal("5.00"), "premissa: crédito consumido")
    antes = _snapshot(vid, (p.suco,), trabalhador_id=trab, sessoes=(sess,))
    st, corpo, _ = _cancelar(vid, motivo="fiado com crédito consumido")
    assert_eq(st, 200, "recusado")
    assert "crédito" in corpo, "motivo cita o crédito"
    _sem_efeitos(antes, _snapshot(vid, (p.suco,), trabalhador_id=trab, sessoes=(sess,)), vid)


@teste("M8d fiado legado sem instante: bloqueado por falta de evidência temporal")
def m8d():
    p = pdv()
    trab = _trabalhador("fiado legado")
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.agua, 1, "3.00")], sess, forma="fiado", trab=trab)
    with conectar() as conn:
        conn.execute("UPDATE vendas_pdv SET criada_em = NULL WHERE id = %s", (vid,))
    antes = _snapshot(vid, (p.agua,), trabalhador_id=trab, sessoes=(sess,))
    st, corpo, _ = _cancelar(vid, motivo="fiado antigo")
    assert_eq(st, 200, "recusado")
    assert "sem instante" in corpo or "anterior a esta entrega" in corpo, "motivo claro"
    _sem_efeitos(antes, _snapshot(vid, (p.agua,), trabalhador_id=trab, sessoes=(sess,)), vid)


@teste("M8e recebimento lançado nominalmente para a venda: bloqueado")
def m8e():
    p = pdv()
    trab = _trabalhador("fiado com entrada nominal")
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.agua, 1, "3.00")], sess, forma="fiado", trab=trab)
    with conectar() as conn:
        conn.execute(
            """INSERT INTO financeiro_movimentacoes (tipo, categoria, valor, descricao)
               VALUES ('entrada', 'recebimento_fiado', 3.00, %s)""",
            (f"Recebimento fiado — venda #{vid}",))
    antes = _snapshot(vid, (p.agua,), trabalhador_id=trab, sessoes=(sess,))
    st, corpo, _ = _cancelar(vid, motivo="tem recebimento nominal")
    assert_eq(st, 200, "recusado")
    assert "recebimento" in corpo, "motivo cita o recebimento"
    _sem_efeitos(antes, _snapshot(vid, (p.agua,), trabalhador_id=trab, sessoes=(sess,)), vid)


# ── M9 — sessões ─────────────────────────────────────────────────────────────

@teste("M9 sessão fechada, ausente ou alheia: bloqueio sem efeitos; fechamento intacto")
def m9():
    p = pdv()
    sess = _nova_sessao(p)
    v_ok = _venda(p, [_linha(p.agua, 1, "3.00")], sess, recebido="5.00")
    t6._req(_jar(), "POST", "/caixa/fechar", {"caixa_movimento_id": sess, "observacao": "t7"})
    antes = _snapshot(v_ok, (p.agua,), sessoes=(sess,))
    st, corpo, _ = _cancelar(v_ok, motivo="sessão já fechada")
    assert_eq(st, 200, "recusado")
    assert "fechada" in corpo, "motivo cita a sessão fechada"
    _sem_efeitos(antes, _snapshot(v_ok, (p.agua,), sessoes=(sess,)), v_ok)

    # sem vínculo: PIX não exige sessão (TASK_008) e a venda nasce sem gaveta
    v_solta = _venda(p, [_linha(p.suco, 1, "12.50")], 0, forma="pix", recebido="0")
    st, corpo, _ = _cancelar(v_solta, motivo="sem sessão")
    assert_eq(st, 200, "venda sem vínculo recusada")
    assert "não está vinculada" in corpo, "motivo claro"

    # vínculo com sessão de OUTRO caixa (divergência de documento)
    with conectar() as conn:
        outro_caixa = conn.execute(
            "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
            "VALUES (%s, 'sessão alheia', TRUE, 1) RETURNING id",
            (f"{PREFIXO} Caixa alheio",)).fetchone()["id"]
    sess2 = _nova_sessao(p)
    v_estranha = _venda(p, [_linha(p.agua, 1, "3.00")], sess2, recebido="5.00")
    with conectar() as conn:
        conn.execute("UPDATE vendas_pdv SET caixa_id = %s WHERE id = %s",
                     (outro_caixa, v_estranha))
    antes = _snapshot(v_estranha, (p.agua,), sessoes=(sess2,))
    st, corpo, _ = _cancelar(v_estranha, motivo="sessão de outro caixa")
    assert_eq(st, 200, "recusado")
    assert "outro caixa" in corpo, "motivo claro"
    _sem_efeitos(antes, _snapshot(v_estranha, (p.agua,), sessoes=(sess2,)), v_estranha)
    # (a FK vendas_pdv.caixa_movimento_id impede vínculo a sessão inexistente;
    #  a defesa "não existe mais" do serviço permanece como última barreira)


# ── M10 — legado ─────────────────────────────────────────────────────────────

@teste("M10 venda marcada cancelada no legado: sem segunda reversão e sem prova inventada")
def m10():
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.suco, 1, "12.50")], sess, recebido="20.00")
    with conectar() as conn:
        conn.execute("UPDATE vendas_pdv SET status='cancelada' WHERE id=%s", (vid,))
    antes = _snapshot(vid, (p.suco,), sessoes=(sess,))
    st, corpo, _ = _cancelar(vid, motivo="insistir no cancelamento legado")
    assert_eq(st, 200, "recusado, sem redirecionar como sucesso")
    assert "prova de reversão" in corpo, "avisa que não existe prova"
    depois = _snapshot(vid, (p.suco,), sessoes=(sess,))
    assert_eq(depois["cancelamentos"], 0, "nenhum registro de cancelamento fabricado")
    _sem_efeitos(antes, depois, vid)


# ── M11/M12/M13 — idempotência e concorrência ───────────────────────────────

@teste("M11 repetição sequencial e clique duplo concorrente: um único efeito efetivo")
def m11():
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.suco, 1, "12.50")], sess, recebido="20.00")
    _cancelar(vid, motivo="primeiro")
    st, corpo, _ = _cancelar(vid, motivo="segundo")
    assert_eq(st, 200, "repetição não vira sucesso")
    assert "já foi cancelada" in corpo, "mensagem de já cancelada"
    antes = _snapshot(vid, (p.suco,), sessoes=(sess,))

    resultados = {}

    def duplo(n):
        opener = _login_novo()
        st, corpo, loc = t6._req(opener, "POST", f"/caixa/vendas/{vid}/cancelar",
                                 {"motivo": f"concorrente {n}", "confirmar": "1"})
        resultados[n] = (st, loc, corpo)

    ts = [threading.Thread(target=duplo, args=(i,)) for i in (1, 2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    depois = _snapshot(vid, (p.suco,), sessoes=(sess,))
    with conectar() as conn:
        n_reg = conn.execute("SELECT count(*) AS c FROM vendas_pdv_cancelamento "
                             "WHERE venda_id=%s", (vid,)).fetchone()["c"]
        n_ent = conn.execute(
            "SELECT count(*) AS c FROM estoque_movimentos WHERE referencia_id=%s AND tipo='entrada'",
            (vid,)).fetchone()["c"]
    assert_eq(n_reg, 1, "unicidade no banco: um registro efetivo")
    assert_eq(n_ent, 1, "uma única devolução")
    assert_eq(depois, antes, "repetições não moveram absolutamente nada")
    for n, (st, loc, corpo) in resultados.items():
        assert_eq(st, 200, f"clique concorrente {n} não pode virar novo sucesso")
        assert "já foi cancelada" in corpo, f"resposta do concorrente {n}: {corpo[:200]}"


@teste("M12 cancelamento × venda concorrente do mesmo produto: estoque final coerente")
def m12():
    p = pdv()
    sess_a = _nova_sessao(p)
    sess_b = sess_a  # TASK_008: uma única sessão aberta por caixa; a corrida é
    # mesmo válida — cancelamento e venda disputam a linha do produto e o
    # acumulador da sessão
    v_a = _venda(p, [_linha(p.suco, 2, "12.50")], sess_a, recebido="30.00")
    erro = {}

    def cancelar():
        try:
            opener = _login_novo()
            st, corpo, loc = t6._req(opener, "POST", f"/caixa/vendas/{v_a}/cancelar",
                                     {"motivo": "concorrência", "confirmar": "1"})
            assert st == 303, f"cancelamento concorrente falhou: {st} {loc}"
        except Exception as e:
            erro["cancelar"] = e

    t = threading.Thread(target=cancelar)
    t.start()
    v_b = _venda(p, [_linha(p.suco, 1, "12.50")], sess_b, recebido="20.00")
    t.join()
    assert "cancelar" not in erro, f"cancelamento × venda: {erro.get('cancelar')}"
    with conectar() as conn:
        est = conn.execute("SELECT quantidade_estoque FROM produtos WHERE id=%s",
                           (p.suco,)).fetchone()["quantidade_estoque"]
        assert_eq(est, 99, "100 -2 -1 +2 independentemente da interleaving")
        assert_eq(conn.execute("SELECT status FROM vendas_pdv WHERE id=%s", (v_b,)).fetchone()["status"],
                  "concluida", "venda nova preservada")
        dead = conn.execute(
            "SELECT count(*) AS c FROM estoque_movimentos WHERE tipo='entrada' AND referencia_id=%s",
            (v_a,)).fetchone()["c"]
        assert_eq(dead, 1, "devolução única mesmo sob contenção")


@teste("M13 cancelamento × fechamento: antes reverte, depois bloqueia, nunca incoerente")
def m13():
    p = pdv()
    sess = _nova_sessao(p)
    v_a = _venda(p, [_linha(p.suco, 1, "12.50")], sess, recebido="20.00")
    v_b = _venda(p, [_linha(p.agua, 1, "3.00")], sess, recebido="5.00")
    _cancelar(v_a, motivo="cancelar antes do fechar")
    t6._req(_jar(), "POST", "/caixa/fechar", {"caixa_movimento_id": sess, "observacao": "t7"})
    with conectar() as conn:
        mov = conn.execute("SELECT total_vendas, saldo_final FROM caixa_movimentos WHERE id=%s",
                           (sess,)).fetchone()
        assert_eq(_dec(mov["saldo_final"]), _dec(mov["total_vendas"]), "fechamento coerente")
        assert_eq(_dec(mov["total_vendas"]), Decimal("3.00"), "só a venda B permaneceu")
        ent = conn.execute(
            """SELECT COALESCE(SUM(valor),0) AS s FROM financeiro_movimentacoes
               WHERE descricao LIKE %s""", (f"%fechamento #{sess}",)).fetchone()["s"]
        assert_eq(_dec(ent), Decimal("3.00"), "entradas do fechamento batem com o total")

    # agora a ordem inversa: fecha primeiro, cancelamento seguinte é bloqueado
    sess2 = _nova_sessao(p)
    v_c = _venda(p, [_linha(p.agua, 1, "3.00")], sess2, recebido="5.00")
    t6._req(_jar(), "POST", "/caixa/fechar", {"caixa_movimento_id": sess2, "observacao": "t7b"})
    antes = _snapshot(v_c, (p.agua,), sessoes=(sess2,))
    st, corpo, _ = _cancelar(v_c, motivo="depois do fechamento")
    assert_eq(st, 200, "bloqueado após o fechamento")
    _sem_efeitos(antes, _snapshot(v_c, (p.agua,), sessoes=(sess2,)), v_c)


@teste("M14 falha em etapa da reversão derruba a transação inteira (rollback integral)")
def m14():
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.suco, 1, "12.50")], sess, recebido="20.00")
    antes = _snapshot(vid, (p.suco,), sessoes=(sess,))

    orig = sc.avaliar

    def avaliar_com_fantasma(conn, venda_id):
        r = orig(conn, venda_id)
        if venda_id == vid and r["efeitos"]["estoque"]:
            r["efeitos"]["estoque"].append({
                "produto_id": 10 ** 9, "nome": "Produto fantasma", "quantidade": 1,
                "saldo_atual": 0, "ativo": 1})
        return r

    sc.avaliar = avaliar_com_fantasma
    try:
        # a exceção precisa VAZAR do `with conectar()` — é assim que a rota
        # age; capturar dentro do bloco faria COMMIT parcial (o erro buscado)
        try:
            with conectar() as conn:
                sc.cancelar(conn, vid, "injeção de falha", ADMIN_ID)
                raise AssertionError("o serviço deveria ter abortado")
        except sc.CancelamentoBloqueado as e:
            assert "sumiu" in str(e.motivos), e.motivos
            raise
    except sc.CancelamentoBloqueado:
        pass
    finally:
        sc.avaliar = orig

    depois = _snapshot(vid, (p.suco,), sessoes=(sess,))
    assert_eq(depois["venda"]["status"], "concluida", "status não ficou entre os escritos")
    _sem_efeitos(antes, depois, vid)


# ── M15/M16/M17 — recusas ────────────────────────────────────────────────────

@teste("M15 motivo vazio, sem confirmação e venda inexistente: recusa clara, zero escrita")
def m15():
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.agua, 1, "3.00")], sess, recebido="5.00")
    antes = _snapshot(vid, (p.agua,), sessoes=(sess,))
    st, corpo, _ = _cancelar(vid, motivo="   ")
    assert_eq(st, 200, "motivo em branco recusado")
    assert "motivo" in corpo, "mensagem cita o motivo"
    st, corpo, _ = _cancelar(vid, motivo="sem confirmar", confirmar="0")
    assert_eq(st, 200, "sem confirmação recusada")
    st, corpo, _ = _cancelar(999999, motivo="venda que não existe")
    assert_eq(st, 404, "venda inexistente: 404 claro")
    _sem_efeitos(antes, _snapshot(vid, (p.agua,), sessoes=(sess,)), vid)


@teste("M16 anônimo, não-admin e sessão de terminal recusados pelo servidor")
def m16():
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.agua, 1, "3.00")], sess, recebido="5.00")
    # fiado para o teste de não-admin na rota específica; criado já aqui para
    # nenhuma atividade lícita entre o snapshot "antes" e o "depois"
    trab = _trabalhador("fiado nao admin")
    vf = _venda(p, [_linha(p.agua, 1, "3.00")], _nova_sessao(p), forma="fiado", trab=trab)
    antes = _snapshot(vid, (p.agua,), sessoes=(sess,))

    anonimo = _jar(novo=True)
    st, _corpo, loc = t6._req(anonimo, "POST", f"/caixa/vendas/{vid}/cancelar",
                              {"motivo": "anônimo", "confirmar": "1"})
    assert_eq(st, 303, "anônimo redirecionado")
    assert_eq(loc, "/login", f"destino {loc}")
    st, _c, loc = t6._req(anonimo, "GET", f"/caixa/vendas/{vid}/cancelar")
    assert_eq((st, loc), (303, "/login"), "GET da tela também exige sessão")

    # operador com permissão de escrita no caixa, mas NÃO admin
    from rotas.auth import hash_senha
    usuario = f"{PREFIXO}operador-t7"
    with conectar() as conn:
        cur = conn.execute("INSERT INTO grupos (nome, descricao) VALUES (%s, 'caixa sem admin') "
                           "RETURNING id", (f"{PREFIXO} grupo t7",))
        grupo = cur.fetchone()["id"]
        cur = conn.execute(
            """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
               VALUES (%s, %s, %s, 1) RETURNING id""",
            (usuario, "Operador caixa", hash_senha("senha-de-teste")))
        atendente = cur.fetchone()["id"]
        conn.execute("INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, %s)",
                     (atendente, grupo))
        conn.execute(
            """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever)
               VALUES (%s, 'caixa', TRUE, TRUE) ON CONFLICT DO NOTHING""", (grupo,))
    try:
        op = _login_novo(usuario, "senha-de-teste")
        st, _c, loc = t6._req(op, "POST", f"/caixa/vendas/{vid}/cancelar",
                              {"motivo": "não admin", "confirmar": "1"})
        assert_eq(st, 303, "não-admin recuso")
        assert "sem_permissao" in loc, f"destino {loc}"
        st, _c, loc = t6._req(op, "GET", f"/caixa/vendas/{vid}/cancelar")
        assert_eq(st, 303, "tela também")
        assert "sem_permissao" in loc, f"destino GET {loc}"

        # sessão de TERMINAL (A2 da TASK_006): equipamento não cancela
        term = _login_novo()
        t6._req(term, "POST", "/terminal/ativar", {})
        st, _c, loc = t6._req(term, "POST", f"/caixa/vendas/{vid}/cancelar",
                              {"motivo": "terminal", "confirmar": "1"})
        assert_eq(st, 303, "terminal redirecionado")
        assert_eq(loc, "/login", f"terminal não vale para cancelar: {loc}")

        # rota específica de fiado também recusa não-admin
        st, _c, loc = t6._req(op, "POST", f"/caixa/fiados/{vf}/cancelar",
                              {"motivo": "não admin", "confirmar": "1"})
        assert_eq(st, 303, "rota de fiado recusa não-admin")
        assert "sem_permissao" in loc, f"destino {loc}"
        _sem_efeitos(antes, _snapshot(vid, (p.agua,), sessoes=(sess,)), vid)
    finally:
        with conectar() as conn:
            conn.execute("DELETE FROM grupos_permissoes WHERE grupo_id = %s", (grupo,))
            conn.execute("DELETE FROM usuarios_grupos WHERE usuario_id = %s", (atendente,))
            conn.execute("DELETE FROM atendentes WHERE id = %s", (atendente,))
            conn.execute("DELETE FROM grupos WHERE id = %s", (grupo,))


@teste("M17 requisição com valores adulterados: usa os fatos persistidos")
def m17():
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.suco, 1, "12.50")], sess, recebido="20.00")
    antes = _snapshot(vid, (p.suco,), sessoes=(sess,))
    _cancelar(vid, motivo="valores enviados por fora",
              extras={"total": "0.01", "quantidade": "999",
                      "caixa_movimento_id": "1", "doacao_valor": "500.00",
                      "forma_pagamento": "especie"})
    depois = _snapshot(vid, (p.suco,), sessoes=(sess,))
    assert_eq(depois["estoque"][p.suco][0], antes["estoque"][p.suco][0] + 1,
              "devolve a QUANTIDADE PERSISTIDA (1), não a enviada (999)")
    assert_eq(depois["sessoes"][sess][1], Decimal("0.00"),
              "reverte o TOTAL persistido (12,50), não o enviado")
    with conectar() as conn:
        reg = conn.execute("SELECT valor_sessao_revertido, total_venda, doacao_venda "
                           "FROM vendas_pdv_cancelamento WHERE venda_id=%s", (vid,)).fetchone()
        assert_eq(_dec(reg["valor_sessao_revertido"]), Decimal("12.50"), "evidência fiel")
        assert_eq(_dec(reg["doacao_venda"]), Decimal("0.00"), "doação adulterada ignorada")


# ── M18/M19 — metadados prospectivos e rota de fiado ─────────────────────────

@teste("M18 criada_em nasce com a venda; legado nunca é preenchido; migração repetida inofensiva")
def m18():
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.agua, 1, "3.00")], sess, recebido="5.00")
    with conectar() as conn:
        ce = conn.execute("SELECT criada_em FROM vendas_pdv WHERE id=%s", (vid,)).fetchone()["criada_em"]
        assert ce and REGISTRO_INSTANTE.match(ce), f"instante com segundos: {ce!r}"
    # legado: linha sem criada_em continua NULL mesmo depois de repetir o schema
    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento, status)
               VALUES (%s, CURRENT_DATE::text, 1.00, 'especie', 'concluida') RETURNING id""",
            (p.catalogo,))
        legado = cur.fetchone()["id"]
    from banco import criar_tabelas
    criar_tabelas()
    criar_tabelas()
    with conectar() as conn:
        assert conn.execute("SELECT criada_em FROM vendas_pdv WHERE id=%s",
                            (legado,)).fetchone()["criada_em"] is None, "sem backfill heurístico"
        conn.execute("DELETE FROM vendas_pdv WHERE id=%s", (legado,))


@teste("M19 rota específica de fiado encaminha ao serviço único e recusa o resto com motivo")
def m19():
    p = pdv()
    trab = _trabalhador("fiado pela rota")
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.agua, 1, "3.00")], sess, forma="fiado", trab=trab)
    # sem motivo/confirmação: a rota manda para a tela única, sem gravar
    st, _c, loc = t6._req(_jar(), "POST", f"/caixa/fiados/{vid}/cancelar", {})
    assert_eq(st, 303, "encaminhado")
    assert loc == f"/caixa/vendas/{vid}/cancelar", f"destino {loc}"
    antes = _snapshot(vid, (p.agua,), trabalhador_id=trab, sessoes=(sess,))
    _sem_efeitos(antes, _snapshot(vid, (p.agua,), trabalhador_id=trab, sessoes=(sess,)), vid)
    # com motivo/confirmação: o serviço único decide
    st, _c, loc = t6._req(_jar(), "POST", f"/caixa/fiados/{vid}/cancelar",
                          {"motivo": "via rota de fiado", "confirmar": "1"})
    assert_eq(st, 303, "aceito pelo serviço")
    assert "cancelado_ok" in loc, f"destino {loc}"
    depois = _snapshot(vid, (p.agua,), trabalhador_id=trab, sessoes=(sess,))
    assert_eq(depois["venda"]["status"], "cancelada", "revertido pelo serviço central")
    assert_eq(depois["cancelamentos"], 1, "prova registrada")

    # caso bloqueado: a rota de fiado não vira atalho para estorno incorreto
    trab2 = _trabalhador("fiado bloqueado rota")
    sess2 = _nova_sessao(p)
    vid2 = _venda(p, [_linha(p.suco, 1, "12.50")], sess2, forma="fiado", trab=trab2)
    t6._req(_jar(), "POST", f"/caixa/fiados/{vid2}/pagar",
          {"forma_pagamento": "especie", "sessao_id": str(sess2)})
    antes = _snapshot(vid2, (p.suco,), trabalhador_id=trab2, sessoes=(sess2,))
    st, corpo, _ = t6._req(_jar(), "POST", f"/caixa/fiados/{vid2}/cancelar",
                           {"motivo": "tentar o pago", "confirmar": "1"})
    assert_eq(st, 200, "recusado com motivo")
    assert "pago" in corpo, "motivo visível"
    _sem_efeitos(antes, _snapshot(vid2, (p.suco,), trabalhador_id=trab2, sessoes=(sess2,)), vid2)


# ── B1 — navegador real ──────────────────────────────────────────────────────

@teste("B1 navegador: cancelar pela tela com motivo, confirmação, resultado e bloqueio")
def b1():
    faltam = t6._ferramentas_ausentes()
    if faltam:
        raise t6.PularTeste(f"ferramentas ausentes ({', '.join(faltam)})")
    p = pdv()
    sess = _nova_sessao(p)
    vid = _venda(p, [_linha(p.suco, 1, "12.50")], sess, recebido="20.00")
    trab = _trabalhador("fiado navegador")
    vid_paga = _venda(p, [_linha(p.agua, 1, "3.00")], sess, forma="fiado", trab=trab)
    t6._req(_jar(), "POST", f"/caixa/fiados/{vid_paga}/pagar",
          {"forma_pagamento": "especie", "sessao_id": str(sess)})

    from playwright.sync_api import sync_playwright
    host = urllib.parse.urlsplit(BASE).hostname
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ["SHAMBALA_CHROMIUM"])
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies([{"name": "sessao", "value": t6._sessao_browser(),
                                  "domain": host, "path": "/"}])
            pagina = context.new_page()
            pagina.on("dialog", lambda d: d.accept())

            pagina.goto(BASE + f"/caixa/vendas/{vid}/cancelar")
            texto = pagina.inner_text("body")
            assert "CANCELAMENTO INTEGRAL" in texto, f"tela de confirmação: {texto[:300]!r}"
            assert "Suco" in texto and "R$ 12.50" in texto, "item e total persistidos exibidos"
            pagina.fill("textarea[name=motivo]", "erro de lançamento na homologação")
            pagina.click("button[type=submit]")
            pagina.wait_for_load_state("networkidle")
            texto = pagina.inner_text("body")
            assert f"Venda #{vid} cancelada" in texto, f"banner de sucesso: {texto[:300]!r}"
            assert "erro de lançamento na homologação" in texto, "motivo visível na listagem"

            pagina.goto(BASE + f"/caixa/vendas/{vid_paga}/cancelar")
            texto = pagina.inner_text("body")
            assert "não pode ser executado" in texto and "pago" in texto, \
                f"caso bloqueado com motivo claro: {texto[:400]!r}"
            with conectar() as conn:
                st = conn.execute("SELECT status FROM vendas_pdv WHERE id=%s",
                                  (vid_paga,)).fetchone()["status"]
                assert_eq(st, "concluida", "bloqueio não escreveu nada")
        finally:
            browser.close()


# ── runner da suíte ──────────────────────────────────────────────────────────

def _limpar():
    # A prova de cancelamento referencia a venda (FK RESTRICT) e o financeiro
    # referencia os caixas: ambas as saídas acontecem antes da limpeza t6.
    with conectar() as conn:
        conn.execute(
            "DELETE FROM vendas_pdv_cancelamento WHERE venda_id IN "
            "(SELECT id FROM vendas_pdv WHERE caixa_id IN "
            "(SELECT id FROM caixas WHERE nome LIKE %s))", (f"{PREFIXO}%",))
        conn.execute(
            """DELETE FROM financeiro_movimentacoes
               WHERE caixa_id IN (SELECT id FROM caixas WHERE nome LIKE %s)
                  OR descricao LIKE %s
                  OR descricao LIKE 'Recebimento fiado — venda #%%'""",
            (f"{PREFIXO}%", f"%{PREFIXO}%"))
    t6._limpar()


def main():
    global ADMIN_ID
    t6._conferir_banco_do_servidor()   # aborta ANTES de criar schema ou escrever
    preparar_banco()
    _garantir_admin()
    with conectar() as conn:
        ADMIN_ID = conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario = 'admin'").fetchone()["id"]
    _login()
    t6.PDV_FIXTURE = t6.PDV()
    total = ok = pulados = 0
    for nome, fn in RESULTADOS:
        total += 1
        try:
            fn()
            ok += 1
            print(f"  PASS   {nome}")
        except t6.PularTeste as e:
            pulados += 1
            print(f"  PULADO {nome} — {e}")
        except Exception as e:
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{ok}/{total} testes passaram, {pulados} pulados (TASK_007 — cancelamento rastreável)")
    try:
        _limpar()
    except Exception as e:
        print(f"  [limpeza] {e}")
    if pulados:
        return 2
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
