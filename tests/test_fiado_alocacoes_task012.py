#!/usr/bin/env python3
"""Suíte TASK_012 §4 — destino explícito do dinheiro no fiado.

Prova, contra a aplicação real e o banco selado de teste, o contrato da etapa:

1. uma entrega de dinheiro é UM lançamento e pode abater várias dívidas e ainda
   deixar excedente como crédito — sem segunda entrada de dinheiro;
2. `dívida 40 com pagamentos 15 + 25`: a primeira entrega abate e NÃO quita
   (`fiado_pago` continua FALSE, saldo visível); a segunda fecha a dívida;
3. `pagamento 50 para dívida 40` → 40 alocados na dívida + 10 de crédito, ambos
   vinculados ao MESMO lançamento, e a soma das alocações fecha em centavos com
   o valor recebido;
4. `duas dívidas selecionadas`: a ordem escolhida pelo operador manda (a regra
   antiga — mais antiga primeiro — só vale quando não há seleção);
5. `adiantamento sem dívida` tem origem financeira e aparece no extrato; o
   consumo parcial desse crédito fica vinculado à venda que o consumiu e o
   crédito nunca fica negativo;
6. concorrência `pagamento/pagamento` e `consumo/consumo` observada no próprio
   PostgreSQL (espera de lock em `pg_stat_activity`, sem `sleep` apostado);
7. repetição pela mesma chave de tentativa não duplica nada; a última escrita
   falhando desfaz dinheiro, alocações, crédito e tentativa (rollback integral);
8. origem histórica sem alocação NÃO é distribuída retroativamente por nome,
   valor ou data: a dívida continua devendo o valor de face e a limitação fica
   explícita no extrato;
9. os bloqueios de cancelamento da TASK_007 continuam valendo e passam a ser
   justificados pelo vínculo quando existe alocação;
10. dinheiro recebido, lançamentos financeiros, sessão, dívida e crédito são
    conferidos por caminhos SQL independentes e batem entre si.

Uso: dentro de tests/rodar_testes.sh (app real + banco selado).
"""
import http.cookiejar
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_tres_passes as tp  # noqa: E402
from test_tres_passes import (  # noqa: E402
    BASE, preparar_banco, limpar_banco, _garantir_admin,
)

from psycopg2.extras import RealDictCursor  # noqa: E402

from banco import conectar, conta_id_por_tipo  # noqa: E402
import servico_fiado  # noqa: E402
from servico_fiado import moeda  # noqa: E402

PREFIXO = f"T12FIADO-{uuid.uuid4().hex[:8]}"
ADMIN_ID = None
FALHAS = []
APROVADAS = []
F = {}
OP = None

ZERO = Decimal("0.00")


def _conferir_banco_do_servidor():
    """Destino do SERVIDOR comprovado nesta execução (TASK_010)."""
    from trava_isolamento import AmbienteInseguro, provar_destino_do_servidor
    esperado = os.environ.get("SHAMBALA_TEST_DB") or tp.BANCO
    try:
        provar_destino_do_servidor(esperado)
    except AmbienteInseguro as e:
        raise SystemExit(f"[ABORTADO] {e}")


def verificar(nome, condição, detalhe=""):
    if condição:
        APROVADAS.append(nome)
        print(f"  PASS  {nome}")
    else:
        FALHAS.append(f"{nome} — {detalhe}")
        print(f"  FALHOU {nome} — {detalhe}")


class _SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _nova_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        _SemRedirect())


def req(method, path, data=None, o=None):
    # doseq=True: campos repetidos — a seleção de dívidas da tela envia um
    # venda_ids por dívida marcada
    corpo = (urllib.parse.urlencode(data, doseq=True).encode()
             if data is not None else None)
    r = urllib.request.Request(BASE + path, data=corpo, method=method)
    try:
        with (o or OP).open(r) as resp:
            return (resp.status, resp.read().decode("utf-8", "replace"),
                    resp.headers.get("Location"))
    except urllib.error.HTTPError as e:
        return (e.code, e.read().decode("utf-8", "replace"),
                e.headers.get("Location"))


def POST(path, dados, o=None):
    return req("POST", path, dados, o)


def GET(path, o=None):
    return req("GET", path, None, o)


def escalar(sql, params=(), padrao=0):
    with conectar() as conn:
        r = conn.execute(sql, params).fetchone()
        if not r:
            return padrao
        v = list(r.values())[0]
        return padrao if v is None else v


def chave():
    return uuid.uuid4().hex


# ── Fixtures ─────────────────────────────────────────────────────────────────

def montar_fixtures():
    global ADMIN_ID
    with conectar() as conn:
        ADMIN_ID = conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()["id"]
        F["cx"] = conn.execute(
            "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
            "VALUES (%s, 'suíte TASK_012 §4', %s, 1) RETURNING id",
            (f"{PREFIXO} Mercearia", True)).fetchone()["id"]
        F["prod"] = conn.execute(
            "INSERT INTO produtos (nome, categoria, preco_venda, quantidade_estoque, "
            "ativo, caixa_id) VALUES (%s, 'suíte', '20.00', 500, 1, %s) RETURNING id",
            (f"{PREFIXO} Pacotinho", F["cx"])).fetchone()["id"]
        F["especie"] = conta_id_por_tipo(conn, "especie")
        conn.execute("UPDATE contas_financeiras SET ativo = 1 WHERE id = %s",
                     (F["especie"],))
    POST("/caixa/abrir", {"caixa_id": F["cx"], "saldo_inicial": "0"})
    F["sessao"] = escalar(
        "SELECT id FROM caixa_movimentos WHERE caixa_id=%s AND status='aberto' "
        "ORDER BY id DESC LIMIT 1", (F["cx"],))
    assert F["sessao"], "sessão de caixa não abriu"


def novo_trabalhador(rotulo, *, credito="0.00", limite="1000.00") -> tuple:
    """Trabalhador prefixado; devolve (id, nome_completo)."""
    nome = f"{PREFIXO} {rotulo}"
    tid = escalar(
        """INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite,
                                      fiado_credito, fiado_data_encerramento)
           VALUES (%s, 1, %s, %s, NULL) RETURNING id""",
        (nome, Decimal(limite), Decimal(credito)))
    return tid, nome


def nova_divida(tid, valor, *, data_venda="2026-01-10",
                credito_usado="0.00") -> int:
    """Dívida de fiado lançada como o PDV a lançaria (venda concluída, com
    sessão aberta vinculada — é o que a avaliação de cancelamento exige)."""
    with conectar() as conn:
        return conn.execute(
            """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
                     troco, status, atendente_id, fiado_trabalhador_id, fiado_pago,
                     fiado_credito_usado, doacao_valor, caixa_movimento_id, criada_em)
               VALUES (%s, %s, %s, 'fiado', 0, 'concluida', %s, %s, FALSE, %s, 0, %s, %s)
               RETURNING id""",
            (F["cx"], data_venda, Decimal(valor), ADMIN_ID, tid,
             Decimal(credito_usado), F["sessao"],
             f"{data_venda} 09:00:00")).fetchone()["id"]


def receber(tid, nome, valor, *, venda_ids=None, k=None, sessao=None):
    """Entrega de dinheiro no fluxo real da tela (POST pagar-divida)."""
    dados = {"valor": valor, "forma_pagamento": "especie",
             "sessao_id": sessao if sessao is not None else F["sessao"],
             "conta_pix_id": 0, "idempotencia": k or chave()}
    if venda_ids is not None:
        dados["venda_ids"] = [str(v) for v in venda_ids]
    return POST(f"/caixa/fiados/{tid}/pagar-divida", dados)


def adiantar(tid, valor, *, desc=None, k=None):
    dados = {"valor": valor, "descricao": desc or f"{PREFIXO} adiantado {chave()[:6]}",
             "forma_pagamento": "especie", "sessao_id": F["sessao"],
             "conta_pix_id": 0, "idempotencia": k or chave()}
    return POST(f"/caixa/fiados/credito/{tid}", dados)


def venda_pdv_fiada(tid, quantidade, *, k=None, o=None):
    dados = {"caixa_id": F["cx"], "caixa_movimento_id": F["sessao"],
             "forma_pagamento": "fiado", "valor_recebido": "0",
             "itens_json": json.dumps([{"produto_id": F["prod"],
                                        "quantidade": quantidade}]),
             "trabalhador_id": tid, "imprimir": "1", "doacao_valor": "0",
             "idempotencia": k or chave()}
    return POST("/caixa/pdv/finalizar", dados, o=o)


# ── Conferência independente (dinheiro / lançamentos / sessão / dívida / crédito)

def conferir(tid, nome, *descs):
    """Os cinco números por caminhos SQL distintos — nada lido do serviço.

    `descs` acrescenta descrições próprias do teste (entregas de corrida,
    adiantamentos); a descrição padrão é a que a tela de fiado emite.
    """
    descs_todas = [f"Recebimento fiado — {nome}", *descs]
    dinheiro = escalar(
        """SELECT COALESCE(SUM(m.valor), 0) FROM financeiro_movimentacoes m
           WHERE m.descricao = ANY(%s)
             AND m.categoria IN ('recebimento_fiado', 'credito_anticipado')""",
        (descs_todas,))
    lancamentos = escalar(
        """SELECT count(*) FROM financeiro_movimentacoes m
           WHERE m.descricao = ANY(%s)
             AND m.categoria IN ('recebimento_fiado', 'credito_anticipado')""",
        (descs_todas,))
    sessao = escalar(
        """SELECT COALESCE(SUM(e.efeito_fisico), 0) FROM caixa_sessao_eventos e
           JOIN financeiro_movimentacoes m ON m.id = e.financeiro_id
           WHERE m.descricao = ANY(%s) AND e.sessao_id = %s""",
        (descs_todas, F["sessao"]))
    divida = escalar(
        """SELECT COALESCE(SUM(GREATEST(v.total - v.fiado_credito_usado
                                       - COALESCE(a.alocado, 0), 0)), 0)
           FROM vendas_pdv v
           LEFT JOIN (SELECT venda_id, SUM(valor) AS alocado FROM fiado_alocacao
                      WHERE tipo = 'divida' GROUP BY venda_id) a ON a.venda_id = v.id
           WHERE v.fiado_trabalhador_id = %s AND v.fiado_pago = FALSE
             AND v.status = 'concluida'""", (tid,))
    creditos = escalar(
        """SELECT COALESCE(SUM(CASE WHEN tipo = 'credito' THEN valor ELSE -valor END), 0)
           FROM fiado_credito_mov WHERE trabalhador_id = %s""", (tid,))
    saldo = escalar("SELECT fiado_credito FROM trabalhadores WHERE id = %s", (tid,))
    return {"dinheiro": moeda(dinheiro), "lancamentos": lancamentos,
            "sessao": moeda(sessao), "divida": moeda(divida),
            "creditos_mov": moeda(creditos), "credito": moeda(saldo)}


def alocacoes_de(fin_id):
    with conectar() as conn:
        return {(r["tipo"], r["venda_id"]): moeda(r["valor"])
                for r in conn.execute(
                    """SELECT tipo, venda_id, valor FROM fiado_alocacao
                       WHERE financeiro_id = %s""", (fin_id,)).fetchall()}


def fin_id_por_desc(desc):
    return escalar("SELECT id FROM financeiro_movimentacoes WHERE descricao = %s "
                   "ORDER BY id", (desc,))


def estado_divida(venda_id):
    with conectar() as conn:
        r = conn.execute(
            """SELECT v.fiado_pago, v.fiado_credito_usado, v.total, v.status,
                      v.data_venda,
                      COALESCE((SELECT SUM(a.valor) FROM fiado_alocacao a
                                WHERE a.tipo = 'divida' AND a.venda_id = v.id), 0) AS alocado
               FROM vendas_pdv v WHERE v.id = %s""", (venda_id,)).fetchone()
    return dict(r)


# ── 1. contrato no banco ─────────────────────────────────────────────────────

def prova_contrato_no_banco():
    def existe_objeto(nome, tipo):
        return escalar("SELECT count(*) FROM pg_class c "
                       "JOIN pg_namespace n ON n.oid = c.relnamespace "
                       "WHERE c.relname = %s AND n.nspname = 'public' AND c.relkind = %s",
                       (nome, tipo)) == 1

    verificar("banco: tabela de alocação fiado↔lançamento existe",
              existe_objeto("fiado_alocacao", "r"), "")
    verificar("banco: uma linha de dívida por (lançamento, venda)",
              existe_objeto("uq_fiado_alocacao_divida", "i"), "")
    verificar("banco: um único excedente de crédito por lançamento",
              existe_objeto("uq_fiado_alocacao_credito", "i"), "")
    verificar("banco: movimento de crédito aponta a venda que o consumiu",
              escalar("SELECT count(*) FROM information_schema.columns WHERE "
                      "table_name='fiado_credito_mov' AND column_name IN "
                      "('venda_id','financeiro_id')") == 2, "")

    tid, _ = novo_trabalhador("Guarda", credito="10.00")
    negativo_recusado = False
    try:
        with conectar() as conn:
            conn.execute("UPDATE trabalhadores SET fiado_credito = -1 WHERE id = %s",
                         (tid,))
    except Exception:
        negativo_recusado = True
    if not negativo_recusado:
        with conectar() as conn:
            conn.execute("UPDATE trabalhadores SET fiado_credito = 0 WHERE id = %s",
                         (tid,))
    creditos_negativos = escalar(
        "SELECT count(*) FROM trabalhadores WHERE fiado_credito < 0")
    verificar("banco: crédito de fiado negativo não é aceito",
              negativo_recusado or creditos_negativos == 0,
              "a guarda fica ausente só quando o histórico já tem negativo, "
              "que a ordem manda não reescrever")

    # alocação inventada (dívida sem venda, crédito com venda) é recusada
    with conectar() as conn:
        fin = conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, conta_id, status)
               VALUES ('entrada','recebimento_fiado',5.00,'2026-06-01',%s,%s,'pago')
               RETURNING id""",
            (f"{PREFIXO} contrato", F["especie"])).fetchone()["id"]
    for tipo, venda, rotulo in (("divida", None, "dívida sem venda"),
                                ("credito", nova_divida(tid, "5.00"),
                                 "crédito apontando uma venda")):
        recusado = False
        try:
            with conectar() as conn:
                conn.execute("INSERT INTO fiado_alocacao "
                             "(financeiro_id, trabalhador_id, venda_id, tipo, valor) "
                             "VALUES (%s,%s,%s,%s,5.00)", (fin, tid, venda, tipo))
        except Exception:
            recusado = True
        verificar(f"banco: alocação incoerente é recusada ({rotulo})", recusado, "")
    zerado = False
    try:
        with conectar() as conn:
            conn.execute("INSERT INTO fiado_alocacao "
                         "(financeiro_id, trabalhador_id, venda_id, tipo, valor) "
                         "VALUES (%s,%s,NULL,'credito',0)", (fin, tid))
    except Exception:
        zerado = True
    verificar("banco: alocação de zero centavos é recusada", zerado, "")
    verificar("banco: nenhuma das alocações inválidas chegou a ser gravada",
              escalar("SELECT count(*) FROM fiado_alocacao WHERE financeiro_id=%s",
                      (fin,)) == 0, "")
    # a sonda é desfeita: nada de deixar dinheiro sem destino no banco de teste
    with conectar() as conn:
        conn.execute("DELETE FROM fiado_alocacao WHERE financeiro_id = %s", (fin,))
        conn.execute("DELETE FROM financeiro_movimentacoes WHERE id = %s", (fin,))


# ── 2. dívida 40 com pagamentos 15 + 25 ──────────────────────────────────────

def prova_dois_pagamentos_parciais():
    tid, nome = novo_trabalhador("Parcial")
    venda = nova_divida(tid, "40.00")
    zero = conferir(tid, nome)
    antes = {"dinheiro": ZERO, "lancamentos": 0, "sessao": ZERO,
             "divida": moeda("40.00"), "creditos_mov": ZERO, "credito": ZERO}
    verificar("parcial: a dívida nasce devendo o valor de face, sem destino gravado",
              zero == antes, f"conferido={zero}")

    st1, _, loc1 = receber(tid, nome, "15.00")
    c1 = conferir(tid, nome)
    verificar("parcial: 15 abate a dívida e NÃO a quita",
              st1 == 303 and moeda(estado_divida(venda)["alocado"]) == moeda("15.00")
              and estado_divida(venda)["fiado_pago"] is False,
              f"status={st1} loc={loc1} estado={estado_divida(venda)}")
    verificar("parcial: saldo restante continua visível como dívida (25)",
              c1["divida"] == moeda("25.00") and
              c1["dinheiro"] == moeda("15.00") and c1["lancamentos"] == 1,
              f"conferido={c1}")
    verificar("parcial: nada virou crédito quando ainda há saldo devedor",
              c1["credito"] == ZERO and c1["creditos_mov"] == ZERO, f"{c1}")
    verificar("parcial: o dinheiro está na sessão, centavo a centavo",
              c1["sessao"] == moeda("15.00"),
              f"sessao={c1['sessao']} (antes={antes['sessao']})")

    k2 = chave()
    st2, _, loc2 = receber(tid, nome, "25.00", k=k2)
    c2 = conferir(tid, nome)
    verificar("parcial: o segundo pagamento (25) quita exatamente o saldo",
              st2 == 303 and estado_divida(venda)["fiado_pago"] is True
              and c2["divida"] == ZERO and c2["dinheiro"] == moeda("40.00"),
              f"status={st2} conferido={c2}")
    verificar("parcial: duas entregas = dois lançamentos, cada um com sua alocação",
              c2["lancamentos"] == 2 and
              escalar("SELECT count(*) FROM fiado_alocacao WHERE tipo='divida' "
                      "AND venda_id=%s", (venda,)) == 2 and
              moeda(estado_divida(venda)["alocado"]) == moeda("40.00"), "")
    verificar("parcial: sem crédito e sem segunda entrada de dinheiro",
              c2["credito"] == ZERO and c2["sessao"] == moeda("40.00"), f"{c2}")
    verificar("parcial: os dois lançamentos fecham com o valor de face",
              all(moeda(s) == moeda(v)
                  for s, v in somas_por_lancamento(f"Recebimento fiado — {nome}")), "")

    st3, _, loc3 = receber(tid, nome, "10.00")
    verificar("parcial: cobrar de novo quem já não deve é recusado sem escrita",
              "erro=sem_divida" in (loc3 or "") and conferir(tid, nome) == c2,
              f"loc={loc3}")


def somas_por_lancamento(desc):
    with conectar() as conn:
        return [(r["valor"], r["alocado"]) for r in conn.execute(
            """SELECT m.valor, COALESCE(SUM(a.valor), 0) AS alocado
               FROM financeiro_movimentacoes m
               LEFT JOIN fiado_alocacao a ON a.financeiro_id = m.id
               WHERE m.descricao = %s GROUP BY m.id, m.valor""", (desc,)).fetchall()]


# ── 3. pagamento 50 para dívida 40 → crédito 10 ──────────────────────────────

def prova_excedente_vira_credito():
    tid, nome = novo_trabalhador("Excedente")
    venda = nova_divida(tid, "40.00")

    st, _, loc = receber(tid, nome, "50.00")
    c = conferir(tid, nome)
    fin = fin_id_por_desc(f"Recebimento fiado — {nome}")
    verificar("excedente: 50 entregues produzem UM único lançamento",
              st == 303 and c["lancamentos"] == 1 and c["dinheiro"] == moeda("50.00"),
              f"status={st} conferido={c}")
    verificar("excedente: 40 alocados na dívida e 10 no crédito, mesmo lançamento",
              alocacoes_de(fin) == {("divida", venda): moeda("40.00"),
                                    ("credito", None): moeda("10.00")},
              f"alocacoes={alocacoes_de(fin)}")
    verificar("excedente: dívida quitada e crédito de 10 disponível",
              estado_divida(venda)["fiado_pago"] is True and
              c["credito"] == moeda("10.00") and c["divida"] == ZERO, f"{c}")
    verificar("excedente: o movimento de crédito aponta o lançamento que o gerou",
              escalar("SELECT financeiro_id FROM fiado_credito_mov "
                      "WHERE trabalhador_id=%s AND tipo='credito'", (tid,)) == fin, "")
    verificar("excedente: a sessão recebeu os 50 uma única vez",
              c["sessao"] == moeda("50.00") and escalar(
                  "SELECT count(*) FROM caixa_sessao_eventos "
                  "WHERE financeiro_id=%s", (fin,)) == 1, f"{c}")
    verificar("excedente: soma das alocações == dinheiro recebido (sem centavo perdido)",
              moeda(escalar("SELECT COALESCE(SUM(valor),0) FROM fiado_alocacao "
                            "WHERE financeiro_id=%s", (fin,))) == moeda("50.00"), "")
    st2, _, loc2 = receber(tid, nome, "50.00")
    verificar("excedente: repetir a entrega sem dívida em aberto é recusado",
              ("erro=sem_divida" in (loc2 or "")) and conferir(tid, nome) == c,
              f"loc={loc2}")


# ── 4. duas dívidas selecionadas: a ordem do operador manda ──────────────────

def prova_selecao_de_duas_dividas():
    tid, nome = novo_trabalhador("Seleção")
    v1 = nova_divida(tid, "30.00", data_venda="2026-02-05")
    v2 = nova_divida(tid, "20.00", data_venda="2026-01-05")

    st, _, loc = receber(tid, nome, "40.00", venda_ids=[v1, v2])
    c = conferir(tid, nome)
    verificar("seleção: 40 entregues às duas dívidas na ordem escolhida",
              st == 303 and moeda(estado_divida(v1)["alocado"]) == moeda("30.00")
              and moeda(estado_divida(v2)["alocado"]) == moeda("10.00"),
              f"status={st} loc={loc} v1={estado_divida(v1)} v2={estado_divida(v2)}")
    verificar("seleção: a dívida integralmente coberta fica quitada, a parcial não",
              estado_divida(v1)["fiado_pago"] is True
              and estado_divida(v2)["fiado_pago"] is False, "")
    verificar("seleção: nada vira crédito enquanto há saldo (20 - 10 = 10)",
              c["credito"] == ZERO and c["divida"] == moeda("10.00"), f"{c}")
    verificar("seleção: uma única entrada de dinheiro para as duas dívidas",
              c["lancamentos"] == 1 and c["dinheiro"] == moeda("40.00"), f"{c}")

    # uma dívida inexistente na seleção recusa a entrega inteira (nada parcial)
    st3, _, loc3 = receber(tid, nome, "1.00", venda_ids=[v2, 999999])
    verificar("seleção: dívida inexistente na seleção recusa a entrega inteira",
              "erro=selecao_invalida" in (loc3 or "") and
              moeda(estado_divida(v2)["alocado"]) == moeda("10.00"),
              f"loc={loc3}")

    # sem seleção vale a regra comprovada que o sistema já tinha (mais antiga primeiro):
    # restam v2 (janeiro, saldo 10) e v3 (março, 5); 8 não cobre as duas
    v3 = nova_divida(tid, "5.00", data_venda="2026-03-05")
    st2, _, _ = receber(tid, nome, "8.00")
    verificar("seleção: sem escolha explícita a regra antiga (mais antiga primeiro) permanece",
              st2 == 303 and moeda(estado_divida(v2)["alocado"]) == moeda("18.00")
              and estado_divida(v2)["fiado_pago"] is False
              and moeda(estado_divida(v3)["alocado"]) == ZERO
              and moeda(estado_divida(v1)["alocado"]) == moeda("30.00"),
              f"v1={estado_divida(v1)} v2={estado_divida(v2)} v3={estado_divida(v3)}")
    verificar("seleção: nada de crédito enquanto existe saldo por cobrir",
              conferir(tid, nome)["credito"] == ZERO, f"{conferir(tid, nome)}")

    # a tela mostra a distribuição antes de confirmar (prévia client-side)
    st4, corpo, _ = GET("/caixa/fiados")
    verificar("seleção: a tela oferece a marcação por dívida e a prévia da repartição",
              st4 == 200 and 'name="venda_ids"' in corpo
              and "atualizarReparticao" in corpo and "reparticao-" in corpo,
              f"status={st4}")


# ── 5. adiantamento sem dívida + consumo parcial vinculado à venda ───────────

def prova_adiantamento_e_consumo_parcial():
    tid, nome = novo_trabalhador("Adiantado", credito="0.00")
    desc = f"{PREFIXO} adiantamento {chave()[:6]}"

    st, _, loc = adiantar(tid, "30.00", desc=desc)
    c = conferir(tid, nome, desc)
    fin = fin_id_por_desc(desc)
    verificar("adiantamento: dinheiro sem dívida em aberto tem origem financeira própria",
              st == 303 and c["lancamentos"] == 1 and
              c["dinheiro"] == moeda("30.00") and c["divida"] == ZERO,
              f"status={st} loc={loc} conferido={c}")
    verificar("adiantamento: o crédito nasce vinculado ao lançamento (mesmo dinheiro)",
              alocacoes_de(fin) == {("credito", None): moeda("30.00")} and
              escalar("SELECT financeiro_id FROM fiado_credito_mov "
                      "WHERE trabalhador_id=%s AND tipo='credito'", (tid,)) == fin,
              f"alocacoes={alocacoes_de(fin)}")
    verificar("adiantamento: nenhuma dívida foi inventada para receber esse dinheiro",
              escalar("SELECT count(*) FROM fiado_alocacao WHERE tipo='divida' "
                      "AND financeiro_id=%s", (fin,)) == 0
              and escalar("SELECT count(*) FROM vendas_pdv "
                          "WHERE fiado_trabalhador_id=%s", (tid,)) == 0, "")

    # consumo parcial: compra de 60 com 30 de crédito → dívida de face 60, saldo 30
    st2, _, loc2 = venda_pdv_fiada(tid, 3)
    venda = escalar("SELECT id FROM vendas_pdv WHERE fiado_trabalhador_id=%s "
                    "ORDER BY id DESC LIMIT 1", (tid,))
    c2 = conferir(tid, nome, desc)
    verificar("consumo: compra de 60 consome 30 de crédito e deixa dívida de 30",
              st2 == 303 and "erro=" not in (loc2 or "") and
              moeda(estado_divida(venda)["fiado_credito_usado"])
              == moeda("30.00") and c2["divida"] == moeda("30.00")
              and c2["credito"] == ZERO,
              f"status={st2} loc={loc2} venda={venda} conferido={c2}")
    verificar("consumo: o consumo de crédito está vinculado à venda que o gerou",
              escalar("SELECT venda_id FROM fiado_credito_mov "
                      "WHERE trabalhador_id=%s AND tipo='debito'", (tid,)) == venda, "")
    verificar("consumo: crédito consumido não é segunda entrada de dinheiro",
              c2["dinheiro"] == moeda("30.00") and c2["lancamentos"] == 1, f"{c2}")
    verificar("consumo: crédito nunca fica negativo (sobra zero, não -30)",
              escalar("SELECT fiado_credito FROM trabalhadores WHERE id=%s",
                      (tid,)) == ZERO, "")

    st3, _, _ = receber(tid, nome, "30.00")
    c3 = conferir(tid, nome, desc)
    verificar("consumo: o saldo de face 30 é cobrado uma única vez e quita a dívida",
              st3 == 303 and c3["divida"] == ZERO and
              estado_divida(venda)["fiado_pago"] is True, f"status={st3} {c3}")
    verificar("consumo: dinheiro total da compra (30 + 30) bate com o valor de face",
              c3["dinheiro"] == moeda("60.00") and c3["sessao"] == moeda("60.00"),
              f"{c3}")


# ── 6. extrato por trabalhador ───────────────────────────────────────────────

def prova_extrato_separa_divida_e_credito():
    tid, nome = novo_trabalhador("Extrato", credito="0.00")
    venda = nova_divida(tid, "60.00")
    adiantar(tid, "25.00", desc=f"{PREFIXO} extrato crédito")
    receber(tid, nome, "40.00")
    c = conferir(tid, nome, f"{PREFIXO} extrato crédito")
    fin = fin_id_por_desc(f"Recebimento fiado — {nome}")

    st, corpo, _ = GET(f"/caixa/fiados/extrato/{tid}")
    verificar("extrato: tela responde para o trabalhador", st == 200, f"status={st}")
    # TASK_012 §5: o extrato usa o MESMO formato das outras telas do financeiro
    # (vírgula decimal, filtro `moeda`) — antes saía "R$ 20.00" em formato C.
    verificar("extrato: saldo devedor e crédito aparecem separados",
              "Saldo devedor" in corpo and "Crédito antecipado disponível" in corpo
              and "R$ 20,00" in corpo and "R$ 25,00" in corpo
              and "R$ 20.00" not in corpo, "")
    verificar("extrato: a dívida mostra valor original, recebido e saldo",
              "Valor original" in corpo and "R$ 60,00" in corpo
              and "R$ 40,00" in corpo, "")
    verificar("extrato: cada número aponta o lançamento que o origina",
              f"/financeiro/lancamento/{fin}" in corpo
              and f"lançamento {fin}" in corpo, "")
    verificar("extrato: nada no extrato diverge dos números conferidos no banco",
              c["divida"] == moeda("20.00") and c["credito"] == moeda("25.00"), f"{c}")

    st2, _, loc2 = GET("/caixa/fiados/extrato/999999")
    verificar("extrato: trabalhador inexistente é devolvido à lista com motivo",
              st2 == 303 and "erro=trabalhador_nao_encontrado" in (loc2 or ""),
              f"status={st2} loc={loc2}")


# ── 7. repetição pela mesma chave ────────────────────────────────────────────

def prova_repeticao_pela_mesma_chave():
    tid, nome = novo_trabalhador("Chave")
    venda = nova_divida(tid, "40.00")
    k = chave()
    dados = {"valor": "40.00", "forma_pagamento": "especie",
             "sessao_id": F["sessao"], "conta_pix_id": 0, "idempotencia": k}

    st1, _, loc1 = POST(f"/caixa/fiados/{tid}/pagar-divida", dict(dados))
    depois = conferir(tid, nome)
    verificar("repetido: a primeira entrega quita a dívida com um lançamento",
              st1 == 303 and depois["lancamentos"] == 1
              and estado_divida(venda)["fiado_pago"] is True, f"status={st1}")
    st2, _, loc2 = POST(f"/caixa/fiados/{tid}/pagar-divida", dict(dados))
    verificar("repetido: o mesmo envio devolve o mesmo destino, sem efeito novo",
              st2 == 303 and loc2 == loc1 and conferir(tid, nome) == depois,
              f"loc1={loc1} loc2={loc2}")
    verificar("repetido: nenhuma alocação duplicada (índice único por lançamento/venda)",
              escalar("SELECT count(*) FROM fiado_alocacao WHERE venda_id=%s",
                      (venda,)) == 1, "")
    st3, _, loc3 = POST(f"/caixa/fiados/{tid}/pagar-divida",
                        dict({**dados, "valor": "13.00"}))
    verificar("repetido: mesma chave com outro conteúdo é recusada sem escrita",
              "tentativa_conflito" in (loc3 or "") and conferir(tid, nome) == depois,
              f"loc={loc3}")


# ── 8. concorrência pagamento/pagamento (determinística) ─────────────────────

def _abrir():
    from banco import _ConnCompat, _obter_pool
    pool = _obter_pool()
    bruta = pool.getconn()
    bruta.cursor_factory = RealDictCursor
    return bruta, _ConnCompat(bruta), pool


def _espera_de_lock(standard):
    return escalar(
        """SELECT count(*) FROM pg_stat_activity
           WHERE datname = current_database() AND pid <> pg_backend_pid()
             AND wait_event_type = 'Lock' AND state = 'active'
             AND query LIKE %s""", (f"%{standard}%",))


def _aguardar(standard, timeout=20.0):
    limite = time.time() + timeout
    while time.time() < limite:
        if _espera_de_lock(standard) >= 1:
            return True
        time.sleep(0.05)
    return False


def prova_concorrencia_pagamento_pagamento():
    """Duas entregas de 30, na mesma tela, contra uma dívida de 40.

    A transação A trava a dívida e NÃO confirma; B é disparada e fica presa no
    próprio PostgreSQL (espera de lock observada em `pg_stat_activity`, não
    `sleep` apostado). Quando A confirma, B relê o saldo sob lock e distribui os
    10 restantes + 20 de excedente. Resultado esperado sem depender de sorte.
    """
    from servico_idempotencia import exigir_reserva
    from servico_sessao import sessao_para_recebimento

    tid, nome = novo_trabalhador("Corrida")
    venda = nova_divida(tid, "40.00")
    descs = {"A": f"{PREFIXO} corrida A", "B": f"{PREFIXO} corrida B"}
    ordem = {}
    erro = {}

    def entregar(rotulo, k):
        bruta, conn, pool = _abrir()
        try:
            dividas = servico_fiado.dividas_em_aberto(conn, tid, travar=True)
            sessao = sessao_para_recebimento(conn, F["sessao"], "especie")
            exigir_reserva(conn, idempotencia=k, operacao="fiado_pagar_divida",
                           autor_id=ADMIN_ID, corpo={"t": rotulo})
            res = servico_fiado.aplicar_recebimento(
                conn, trabalhador_id=tid, dividas=dividas, valor=moeda("30.00"),
                forma="especie", conta_id=F["especie"], sessao=sessao,
                descricao=descs[rotulo], atendente_id=ADMIN_ID)
            bruta.commit()
            ordem[rotulo] = res
        except Exception as e:
            bruta.rollback()
            erro[rotulo] = f"{type(e).__name__}: {e}"
        finally:
            pool.putconn(bruta)

    bruta_a, A, pool_a = _abrir()
    try:
        dividas_a = servico_fiado.dividas_em_aberto(A, tid, travar=True)
        sessao_a = sessao_para_recebimento(A, F["sessao"], "especie")
        k_a = chave()
        exigir_reserva(A, idempotencia=k_a, operacao="fiado_pagar_divida",
                       autor_id=ADMIN_ID, corpo={"t": "A"})
        ordem["A"] = servico_fiado.aplicar_recebimento(
            A, trabalhador_id=tid, dividas=dividas_a, valor=moeda("30.00"),
            forma="especie", conta_id=F["especie"], sessao=sessao_a,
            descricao=descs["A"], atendente_id=ADMIN_ID)
        # A ainda sem commit: a dívida está travada e o dinheiro invisível.

        tb = threading.Thread(target=entregar, args=("B", chave()))
        tb.start()
        presa = _aguardar("vendas_pdv")
        verificar("corrida pagamento/pagamento: a segunda entrega ficou de fato "
                  "aguardando a primeira no banco", presa,
                  "nenhuma espera de lock em vendas_pdv observada")

        bruta_a.commit()        # A conclui agora, com B presa
        tb.join(60)
        verificar("corrida: nenhuma das duas entregas falhou",
                  not erro.get("B"), erro.get("B", ""))
        verificar("corrida: a segunda acorda, relê o saldo e aloca exatamente os 10 restantes",
                  ordem.get("B") and [(v, moeda(p)) for v, p in ordem["B"]["alocado_dividas"]]
                  == [(venda, moeda("10.00"))] and
                  ordem["B"]["excedente"] == moeda("20.00"),
                  f"B={ordem.get('B')} erro={erro.get('B')}")
    finally:
        pool_a.putconn(bruta_a)

    c = conferir(tid, nome, descs["A"], descs["B"])
    fin_a = fin_id_por_desc(descs["A"])
    fin_b = fin_id_por_desc(descs["B"])
    verificar("corrida: dois entregamentos = dois lançamentos de 30 (dinheiro real)",
              escalar("SELECT count(*) FROM financeiro_movimentacoes "
                      "WHERE descricao = ANY(%s)", ((list(descs.values())),)) == 2
              and moeda(escalar("SELECT COALESCE(SUM(valor),0) FROM financeiro_movimentacoes "
                                "WHERE descricao = ANY(%s)", ((list(descs.values())),)))
              == moeda("60.00"), "")
    verificar("corrida: nenhum centavo foi alocado duas vezes (40 na mesma dívida)",
              moeda(estado_divida(venda)["alocado"]) == moeda("40.00"),
              f"estado={estado_divida(venda)}")
    verificar("corrida: dívida quitada e crédito de 20, uma única vez",
              estado_divida(venda)["fiado_pago"] is True and
              c["credito"] == moeda("20.00"), f"{c}")
    verificar("corrida: cada lançamento fecha com suas alocações",
              alocacoes_de(fin_a) == {("divida", venda): moeda("30.00")} and
              alocacoes_de(fin_b) == {("divida", venda): moeda("10.00"),
                                      ("credito", None): moeda("20.00")},
              f"A={alocacoes_de(fin_a)} B={alocacoes_de(fin_b)}")
    verificar("corrida: sessão recebeu 60 e nada mais",
              c["sessao"] == moeda("60.00"), f"{c}")


# ── 9. concorrência consumo/consumo (determinística) ─────────────────────────

def prova_concorrencia_consumo_consumo():
    """Vendas concorrentes que consomem o MESMO crédito antecipado.

    A linha do trabalhador é travada por uma transação do teste; a primeira
    venda HTTP fica presa nesse lock (espera observada em `pg_stat_activity`).
    Ao liberar, ela é a única a ler o crédito de 50; a segunda venda entra depois
    e vê o estado já consolidado — sem leitura perdida, sem crédito negativo.
    """
    tid, nome = novo_trabalhador("Consumo", credito="50.00")
    bruta_h, H, pool = _abrir()
    respostas = {}

    def vender(rotulo):
        try:
            respostas[rotulo] = venda_pdv_fiada(tid, 2)
        except Exception as e:                      # nunca deixar o join cego
            respostas[rotulo] = (f"exceção {type(e).__name__}: {e}", "", "")

    try:
        H.execute("SELECT id FROM trabalhadores WHERE id = %s FOR UPDATE", (tid,))
        tp1 = threading.Thread(target=vender, args=("1",))
        tp1.start()
        presa = _aguardar("trabalhadores")
        verificar("corrida consumo/consumo: a venda ficou presa no lock do trabalhador",
                  presa, "nenhuma espera de lock em trabalhadores observada")
        bruta_h.commit()
        tp1.join(60)
        verificar("corrida: a primeira venda concluiu depois de liberado o lock",
                  tp1.is_alive() is False and respostas.get("1"),
                  f"respostas={respostas}")
        st1, _, loc1 = respostas["1"]
        # segunda venda: entra depois da primeira ter consolidado o crédito
        st2, _, loc2 = venda_pdv_fiada(tid, 2)
        verificar("corrida: as duas vendas de 40 efetivamente saíram no PDV",
                  st1 == 303 and st2 == 303 and "erro=" not in (loc1 or "")
                  and "erro=" not in (loc2 or ""),
                  f"st1={st1} loc1={loc1} st2={st2} loc2={loc2}")
    finally:
        pool.putconn(bruta_h)

    vendas = [
        dict(r) for r in _linhas(
            """SELECT id, total, fiado_credito_usado, fiado_pago FROM vendas_pdv
               WHERE fiado_trabalhador_id = %s AND status = 'concluida'
               ORDER BY id""", (tid,))]
    consumido = sum((moeda(v["fiado_credito_usado"]) for v in vendas), ZERO)
    c = conferir(tid, nome)
    verificar("corrida: o crédito de 50 foi consumido exatamente uma vez no total",
              consumido == moeda("50.00") and len(vendas) == 2,
              f"consumido={consumido} vendas={vendas}")
    verificar("corrida: 40 + 40 de compras com 50 de crédito → dívida de 30",
              c["divida"] == moeda("30.00"), f"{c}")
    verificar("corrida: crédito final zero, nunca negativo",
              c["credito"] == ZERO and
              escalar("SELECT count(*) FROM trabalhadores WHERE id=%s "
                      "AND fiado_credito < 0", (tid,)) == 0, f"{c}")
    verificar("corrida: cada consumo está vinculado à sua própria venda",
              escalar("SELECT count(*) FROM fiado_credito_mov cm "
                      "WHERE cm.trabalhador_id=%s AND cm.tipo='debito' "
                      "AND cm.venda_id = ANY(%s)",
                      (tid, [v["id"] for v in vendas])) == 2, "")
    verificar("corrida: nenhuma entrada de dinheiro nasceu do consumo de crédito",
              c["dinheiro"] == ZERO and c["lancamentos"] == 0, f"{c}")
    verificar("corrida: consumo de crédito é evento de efeito físico zero na sessão",
              escalar("SELECT count(*) FROM caixa_sessao_eventos WHERE tipo="
                      "'venda_credito' AND efeito_fisico <> 0") == 0, "")


def _linhas(sql, params):
    with conectar() as conn:
        return conn.execute(sql, params).fetchall()


# ── 10. última escrita falhando ──────────────────────────────────────────────

def prova_ultima_escrita_falhando():
    """O dinheiro é a primeira escrita e a alocação de excedente a última.

    Forçando a última escrita a violar a unicidade (segundo excedente para o
    mesmo lançamento), a transação inteira desfaz: sem lançamento, sem evento de
    sessão, sem crédito, sem dívida abatida e sem chave consumida.
    """
    from servico_idempotencia import exigir_reserva
    from servico_sessao import sessao_para_recebimento

    tid, nome = novo_trabalhador("Rollback")
    venda = nova_divida(tid, "40.00")
    k = chave()
    desc = f"{PREFIXO} ultima escrita falha"
    antes = conferir(tid, nome)
    tipo_falha = None
    concluida = False
    try:
        with conectar() as conn:
            dividas = servico_fiado.dividas_em_aberto(conn, tid, travar=True)
            sessao = sessao_para_recebimento(conn, F["sessao"], "especie")
            exigir_reserva(conn, idempotencia=k, operacao="fiado_pagar_divida",
                           autor_id=ADMIN_ID, corpo={"t": desc})
            res = servico_fiado.aplicar_recebimento(
                conn, trabalhador_id=tid, dividas=dividas, valor=moeda("50.00"),
                forma="especie", conta_id=F["especie"], sessao=sessao,
                descricao=desc, atendente_id=ADMIN_ID)
            # última escrita duplicada: o mesmo lançamento não tem dois excedentes
            conn.execute(
                """INSERT INTO fiado_alocacao
                   (financeiro_id, trabalhador_id, venda_id, tipo, valor)
                   VALUES (%s, %s, NULL, 'credito', %s)""",
                (res["financeiro_id"], tid, res["excedente"]))
        concluida = True
    except Exception as e:
        tipo_falha = f"{type(e).__name__}: {e}"
    verificar("última escrita: a violação impediu a transação de concluir",
              concluida is False and tipo_falha is not None, f"tipo={tipo_falha}")

    depois = conferir(tid, nome)
    verificar("última escrita: o dinheiro recebido não existe (rollback integral)",
              depois["dinheiro"] == antes["dinheiro"] and
              depois["lancamentos"] == antes["lancamentos"], f"depois={depois}")
    verificar("última escrita: nada na sessão, nada alocado, nenhum crédito gravado",
              depois["sessao"] == antes["sessao"] and
              escalar("SELECT count(*) FROM fiado_alocacao a "
                      "JOIN financeiro_movimentacoes m ON m.id = a.financeiro_id "
                      "WHERE m.descricao = %s", (desc,)) == 0 and
              depois["credito"] == ZERO, f"{depois}")
    verificar("última escrita: a dívida continua devendo o valor de face",
              moeda(estado_divida(venda)["alocado"]) == ZERO and
              estado_divida(venda)["fiado_pago"] is False and
              depois["divida"] == moeda("40.00"), f"{depois}")
    verificar("última escrita: a chave da tentativa não ficou consumida",
              escalar("SELECT count(*) FROM operacao_idempotencia WHERE chave=%s",
                      (k,)) == 0, "")

    st, _, _ = receber(tid, nome, "50.00")
    c = conferir(tid, nome)
    verificar("última escrita: o reenvio depois da falha registra a entrega uma vez",
              st == 303 and c["dinheiro"] == moeda("50.00") and
              c["lancamentos"] == 1 and c["credito"] == moeda("10.00"),
              f"status={st} {c}")


# ── 11. origem histórica sem backfill ────────────────────────────────────────

def prova_legado_sem_backfill():
    tid, nome = novo_trabalhador("Legado")
    venda = nova_divida(tid, "40.00")
    desc = f"{PREFIXO} legado sem destino"

    # lançamento de fiado do fluxo antigo: dinheiro registrado, destino não
    with conectar() as conn:
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, caixa_id,
                forma_pagamento, conta_id, status)
               VALUES ('entrada','recebimento_fiado',40.00,'2026-05-05',%s,%s,
                       'especie',%s,'pago')""",
            (desc, F["cx"], F["especie"]))

    c = conferir(tid, nome)
    verificar("legado: a dívida continua devendo o valor de face (nada presumido)",
              c["divida"] == moeda("40.00") and
              moeda(estado_divida(venda)["alocado"]) == ZERO, f"{c}")
    verificar("legado: nenhuma alocação foi criada para o lançamento antigo",
              escalar("SELECT count(*) FROM fiado_alocacao a "
                      "JOIN financeiro_movimentacoes m ON m.id = a.financeiro_id "
                      "WHERE m.descricao = %s", (desc,)) == 0, "")
    verificar("legado: nenhum movimento de crédito foi preenchido por suposição",
              escalar("SELECT count(*) FROM fiado_credito_mov WHERE financeiro_id IN "
                      "(SELECT id FROM financeiro_movimentacoes WHERE descricao = %s)",
                      (desc,)) == 0, "")
    verificar("legado: o histórico anterior permanece intacto (não foi apagado)",
              escalar("SELECT status, valor FROM financeiro_movimentacoes "
                      "WHERE descricao = %s", (desc,)) is not None and
              escalar("SELECT count(*) FROM financeiro_movimentacoes "
                      "WHERE descricao = %s AND status = 'pago'", (desc,)) == 1, "")

    st, corpo, _ = GET(f"/caixa/fiados/extrato/{tid}")
    verificar("legado: o extrato declara a limitação em vez de esconder o número",
              st == 200 and "não têm alocação comprovada" in corpo,
              f"status={st}")
    verificar("legado: mesmo assim a dívida é mostrada com o valor de face",
              "R$ 40,00" in corpo and
              "nenhum recebimento registrado" in corpo, "")

    st2, _, _ = receber(tid, nome, "15.00")
    c2 = conferir(tid, nome)
    verificar("legado: o recebimento novo abate a dívida sem tocar no legado",
              st2 == 303 and c2["divida"] == moeda("25.00") and
              moeda(estado_divida(venda)["alocado"]) == moeda("15.00"),
              f"status={st2} {c2}")


# ── 12. cancelamento com alocação (bloqueios TASK_007 preservados) ───────────

def prova_cancelamento_respeita_alocacao():
    tid, nome = novo_trabalhador("Cancelamento")
    venda = nova_divida(tid, "40.00")
    receber(tid, nome, "15.00")
    antes_venda = estado_divida(venda)
    antes = conferir(tid, nome)

    st, corpo, _ = POST(f"/caixa/fiados/{venda}/cancelar",
                        {"motivo": f"{PREFIXO} tentaria cancelar", "confirmar": "1"})
    depois = conferir(tid, nome)
    verificar("cancelamento: dívida já paga em parte não é estornada por atalho",
              "já recebeu pagamento" in corpo and "alocado" in corpo,
              f"status={st}")
    verificar("cancelamento: nenhum efeito colateral (venda, dinheiro e alocações intactos)",
              estado_divida(venda) == antes_venda and depois == antes and
              escalar("SELECT status FROM vendas_pdv WHERE id=%s", (venda,))
              == "concluida", "")

    # dívida sem alocação continua cancelável pelo serviço único (TASK_007)
    v2 = nova_divida(tid, "12.00")
    st2, _, loc2 = POST(f"/caixa/fiados/{v2}/cancelar",
                        {"motivo": f"{PREFIXO} sem pagamento", "confirmar": "1"})
    verificar("cancelamento: dívida sem recebimento segue o fluxo de cancelamento",
              st2 == 303 and "cancelado_ok" in (loc2 or "") and
              escalar("SELECT status FROM vendas_pdv WHERE id=%s", (v2,))
              == 'cancelada', f"status={st2} loc={loc2}")


# ── 13. fecho: as contas da suíte batem entre si ─────────────────────────────

def prova_contas_batem():
    desequilibrio = escalar(
        """SELECT count(*) FROM financeiro_movimentacoes m
           WHERE m.descricao LIKE %s
             AND m.categoria IN ('recebimento_fiado','credito_anticipado')
             AND m.descricao NOT LIKE %s
             AND COALESCE((SELECT SUM(a.valor) FROM fiado_alocacao a
                           WHERE a.financeiro_id = m.id), 0) <> m.valor""",
        (f"%{PREFIXO}%", f"{PREFIXO} legado%"))
    verificar("fecho: toda entrega desta suíte tem alocações que somam o valor recebido",
              desequilibrio == 0, f"{desequilibrio} lançamento(s) desnivelados")

    divergentes = escalar(
        """SELECT count(*) FROM financeiro_movimentacoes m
           JOIN caixa_sessao_eventos e ON e.financeiro_id = m.id
           WHERE m.descricao LIKE %s AND m.forma_pagamento = 'especie'
             AND e.efeito_fisico <> m.valor""", (f"%{PREFIXO}%",))
    verificar("fecho: o efeito físico na sessão é exatamente o dinheiro recebido",
              divergentes == 0, f"{divergentes} evento(s) divergente(s)")

    orphan = escalar(
        """SELECT count(*) FROM fiado_alocacao a
           LEFT JOIN financeiro_movimentacoes m ON m.id = a.financeiro_id
           WHERE m.id IS NULL""")
    verificar("fecho: nenhuma alocação existe sem o lançamento que a originou",
              orphan == 0, f"{orphan} alocação(ões) órfã(s)")

    sem_dono = escalar(
        """SELECT count(*) FROM fiado_credito_mov cm
           WHERE cm.trabalhador_id IN (SELECT id FROM trabalhadores
                                       WHERE nome_completo LIKE %s)
             AND cm.tipo = 'debito' AND cm.venda_id IS NULL""", (f"{PREFIXO}%",))
    verificar("fecho: todo consumo de crédito desta suíte aponta a venda consumida",
              sem_dono == 0, f"{sem_dono} consumo(s) sem venda")

    negativos = escalar("SELECT count(*) FROM trabalhadores WHERE fiado_credito < 0")
    verificar("fecho: nenhum trabalhador do banco ficou com crédito negativo",
              negativos == 0, f"{negativos} negativo(s)")


def prova_pagamento_x_fechamento_da_sessao():
    """TASK_014 §3: pagamento de fiado × fechamento da sessão, disputando.

    O recebimento toca a sessão (evento + efeito físico) e o fechamento também.
    A prova prende a sessão numa transação que NÃO confirma, dispara o
    recebimento (que fica esperando no próprio PostgreSQL — espera observada em
    `pg_stat_activity`, não `sleep` apostado) e só então confirma o fechamento.
    Depois do fechamento, nada de efeito tardio: o recebimento é recusado ou
    termina antes, mas o estado final tem de ser coerente — sem evento em sessão
    fechada e sem recebimento "meio aplicado".
    """
    from decimal import Decimal
    from servico_idempotencia import exigir_reserva
    from servico_sessao import sessao_para_recebimento
    import servico_fiado

    tid, nome = novo_trabalhador("Pag x Fechamento")
    venda = nova_divida(tid, "40.00")
    marca = f"{PREFIXO} pagxfech"
    saida = {}
    # A sessão é compartilhada pelas provas desta suíte: o que se mede é o DELTA
    # desta corrida, não o total histórico de eventos.
    eventos_antes = escalar("SELECT count(*) FROM caixa_sessao_eventos "
                            "WHERE sessao_id = %s", (F["sessao"],))

    # A: trava a sessão (como o fechamento faz) e NÃO confirma ainda
    bruta_a, conn_a, pool_a = _abrir()
    try:
        conn_a.execute("SELECT id FROM caixa_movimentos WHERE id = %s FOR UPDATE",
                       (F["sessao"],))
        def entregar():
            bruta_b, conn_b, pool_b = _abrir()
            try:
                dividas = servico_fiado.dividas_em_aberto(conn_b, tid, travar=True)
                sessao = sessao_para_recebimento(conn_b, F["sessao"], "especie")
                saida["recebimento"] = "aplicou"
            except Exception as e:  # noqa: BLE001
                saida["recebimento"] = f"recusou: {type(e).__name__}"
            finally:
                pool_b.putconn(bruta_b)
        t = threading.Thread(target=entregar)
        t.start()
        preso = _aguardar("FOR UPDATE", timeout=10) or _espera_de_lock("caixa_movimentos") >= 1
        # fecha a sessão e confirma
        conn_a.execute(
            "UPDATE caixa_movimentos SET status = 'fechado' WHERE id = %s",
            (F["sessao"],))
        conn_a.commit()
        t.join(40)
        aberta = escalar("SELECT count(*) FROM caixa_movimentos "
                         "WHERE id = %s AND status = 'aberto'", (F["sessao"],))
        eventos = escalar("SELECT count(*) FROM caixa_sessao_eventos "
                          "WHERE sessao_id = %s", (F["sessao"],))
        eventos_novos = eventos - eventos_antes
        verificar("pagamento × fechamento: o recebimento ficou preso na sessão "
                  "(espera de lock observada no PostgreSQL, sem sleep)",
                  preso, f"recebimento={saida.get('recebimento')}")
        # O recebimento pode ter concluído ANTES do fechamento (caso legítimo,
        # preso no lock e liberado quando A confirmou) ou sido recusado. O que
        # não pode existir é evento criado na sessão DEPOIS de fechada.
        eventos_apos_fechar = escalar(
            """SELECT count(*) FROM caixa_sessao_eventos
               WHERE sessao_id = %s
                 AND criado_em > (SELECT data_fechamento FROM caixa_movimentos
                                  WHERE id = %s)""", (F["sessao"], F["sessao"]))
        verificar("pagamento × fechamento: a sessão termina fechada e nenhum "
                  "evento é registrado depois do fechamento",
                  aberta == 0 and eventos_apos_fechar == 0,
                  f"aberta={aberta} eventos_novos={eventos_novos} "
                  f"depois_de_fechar={eventos_apos_fechar} "
                  f"recebimento={saida.get('recebimento')}")
    finally:
        pool_a.putconn(bruta_a)
        # devolve a sessão ao estado que a suíte usa
        with conectar() as conn:
            conn.execute("UPDATE caixa_movimentos SET status = 'aberto' WHERE id = %s",
                         (F["sessao"],))


def prova_cancelamento_elegivel_x_pagamento():
    """TASK_014 §3: cancelamento ELEGÍVEL ≠ cancelamento de dívida paga/parcial.

    Separa os dois cenários que a ordem pede para não confundir:
    - dívida já paga / parcial / com crédito → cancelamento BLOQUEADO (TASK_007);
    - venda elegível (sem recebimento) → cancelamento aceito.
    E confere a conta física: saldo inicial + soma dos efeitos válidos, não
    apenas a soma dos eventos.
    """
    from decimal import Decimal

    # (1) dívida parcial: cancelamento bloqueado, sem efeito nenhum
    tid, nome = novo_trabalhador("Canc parcial")
    venda = nova_divida(tid, "40.00")
    st, _, _ = receber(tid, nome, "15.00", venda_ids=[venda])
    antes = {t: escalar(f"SELECT count(*) FROM {t}") for t in
             ("vendas_pdv", "caixa_sessao_eventos", "fiado_alocacao",
              "financeiro_movimentacoes")}
    st_c, corpo_c, _ = POST(f"/caixa/vendas/{venda}/cancelar",
                            {"motivo": "teste de bloqueio", "confirmar": "1"})
    depois = {t: escalar(f"SELECT count(*) FROM {t}") for t in antes}
    status_venda = escalar("SELECT status FROM vendas_pdv WHERE id = %s", (venda,))
    verificar("cancelamento de dívida PARCIAL continua bloqueado, sem efeito",
              st_c == 200 and status_venda == "concluida" and antes == depois,
              f"status={st_c} venda={status_venda} delta={antes} -> {depois}")

    # (2) venda elegível: cancelamento aceito, estoque devolvido, dívida sai
    tid2, nome2 = novo_trabalhador("Canc elegivel")
    venda2 = nova_divida(tid2, "20.00")
    eventos_antes = escalar("SELECT count(*) FROM caixa_sessao_eventos "
                            "WHERE venda_id = %s", (venda2,))
    st2, _, _ = POST(f"/caixa/vendas/{venda2}/cancelar",
                     {"motivo": "fiado lançado em duplicidade", "confirmar": "1"})
    status2 = escalar("SELECT status FROM vendas_pdv WHERE id = %s", (venda2,))
    eventos2 = escalar("SELECT count(*) FROM caixa_sessao_eventos "
                       "WHERE venda_id = %s", (venda2,))
    saldo_aberto = escalar(
        """SELECT count(*) FROM vendas_pdv
           WHERE fiado_trabalhador_id = %s AND forma_pagamento = 'fiado'
             AND NOT fiado_pago AND status = 'concluida'""", (tid2,))
    verificar("cancelamento ELEGÍVEL é aceito: venda cancelada sai da dívida "
              "em aberto e o evento de cancelamento é registrado",
              st2 == 303 and status2 == "cancelada" and saldo_aberto == 0
              and eventos2 == eventos_antes + 1,
              f"status={st2} venda={status2} aberto={saldo_aberto} "
              f"eventos {eventos_antes}->{eventos2}")


FUNCOES = [
    prova_contrato_no_banco,
    prova_dois_pagamentos_parciais,
    prova_excedente_vira_credito,
    prova_selecao_de_duas_dividas,
    prova_pagamento_x_fechamento_da_sessao,
    prova_cancelamento_elegivel_x_pagamento,
    prova_adiantamento_e_consumo_parcial,
    prova_extrato_separa_divida_e_credito,
    prova_repeticao_pela_mesma_chave,
    prova_concorrencia_pagamento_pagamento,
    prova_concorrencia_consumo_consumo,
    prova_ultima_escrita_falhando,
    prova_legado_sem_backfill,
    prova_cancelamento_respeita_alocacao,
    prova_contas_batem,
]


def limpar_suíte():
    """Apaga só o que esta suíte criou (dependentes primeiro — FK RESTRICT)."""
    padrao = f"{PREFIXO}%"
    with conectar() as conn:
        vendas = [r["id"] for r in conn.execute(
            """SELECT id FROM vendas_pdv WHERE fiado_trabalhador_id IN
               (SELECT id FROM trabalhadores WHERE nome_completo LIKE %s)
               OR caixa_id = %s""", (padrao, F["cx"])).fetchall()]
        fins = [r["id"] for r in conn.execute(
            """SELECT id FROM financeiro_movimentacoes
               WHERE descricao LIKE %s OR caixa_id = %s""",
            (f"%{PREFIXO}%", F["cx"])).fetchall()]
        trab = [r["id"] for r in conn.execute(
            "SELECT id FROM trabalhadores WHERE nome_completo LIKE %s",
            (padrao,)).fetchall()]
        sessoes = [r["id"] for r in conn.execute(
            "SELECT id FROM caixa_movimentos WHERE caixa_id = %s", (F["cx"],)).fetchall()]

        if trab:
            conn.execute("DELETE FROM fiado_credito_mov WHERE trabalhador_id = ANY(%s)",
                         (trab,))
        if vendas:
            conn.execute("DELETE FROM vendas_pdv_itens WHERE venda_id = ANY(%s)", (vendas,))
            conn.execute("DELETE FROM caixa_sessao_eventos WHERE venda_id = ANY(%s)",
                         (vendas,))
        if fins:
            conn.execute("DELETE FROM caixa_sessao_eventos WHERE financeiro_id = ANY(%s)",
                         (fins,))
        if sessoes:
            conn.execute("DELETE FROM caixa_sessao_eventos WHERE sessao_id = ANY(%s)",
                         (sessoes,))
        # `fiado_alocacao.trabalhador_id` é NOT NULL: todo vínculo desta suíte
        # pertence a um trabalhador desta suíte (inclusive o da prova de contrato)
        if trab:
            conn.execute("DELETE FROM fiado_alocacao WHERE trabalhador_id = ANY(%s)",
                         (trab,))
        if vendas:
            conn.execute("DELETE FROM vendas_pdv_cancelamento WHERE venda_id = ANY(%s)",
                         (vendas,))
            conn.execute("DELETE FROM vendas_pdv WHERE id = ANY(%s)", (vendas,))
        conn.execute("DELETE FROM estoque_movimentos WHERE produto_id = %s", (F["prod"],))
        if sessoes:
            conn.execute("DELETE FROM caixa_sangrias WHERE caixa_movimento_id = ANY(%s)",
                         (sessoes,))
        conn.execute("DELETE FROM caixa_movimentos WHERE caixa_id = %s", (F["cx"],))
        if fins:
            conn.execute("DELETE FROM financeiro_movimentacoes WHERE id = ANY(%s)",
                         (fins,))
        conn.execute("DELETE FROM produtos WHERE id = %s", (F["prod"],))
        if trab:
            conn.execute("DELETE FROM trabalhadores WHERE id = ANY(%s)", (trab,))
        conn.execute("DELETE FROM caixas WHERE id = %s", (F["cx"],))


def main():
    global OP
    _conferir_banco_do_servidor()
    preparar_banco()
    _garantir_admin()
    OP = _nova_opener()
    POST("/login", {"nome_usuario": "admin", "senha": "teste123"})
    montar_fixtures()

    for fn in FUNCOES:
        try:
            fn()
        except Exception as e:
            FALHAS.append(f"{fn.__name__} — exceção {type(e).__name__}: {e}")
            print(f"  FALHOU {fn.__name__} (exceção)\n        {e}")
            traceback.print_exc()

    total = len(APROVADAS) + len(FALHAS)
    print(f"\n{len(APROVADAS)}/{total} verificações aprovadas "
          "(TASK_012 §4 — destino explícito do dinheiro no fiado)")
    if FALHAS:
        print("\nFalhas:")
        for f in FALHAS:
            print(f"  - {f}")
    try:
        limpar_suíte()
    except Exception as e:
        print(f"  [limpeza] {e}")
    limpar_banco()
    return 0 if not FALHAS else 1


if __name__ == "__main__":
    sys.exit(main())
