#!/usr/bin/env python3
"""Suíte TASK_012 §5 — consultas, saldos e experiência integrada.

Prova, contra a aplicação real e o banco selado de teste, o contrato da etapa:

1. o saldo de fiado que as TELAS mostram é o mesmo número que o RECEBIMENTO
   escreve: valor original − crédito consumido − alocado, nunca negativo
   (dívida 40 com 15 recebido aparece como "Recebido R$ 15,00 / Saldo R$ 25,00"
   na Visão Geral, no relatório de fiados, no extrato e no detalhe do
   lançamento — uma conta só, no serviço);
2. os totais do recorte são a soma de TODAS as dívidas daquele recorte, não da
   página exibida (52 dívidas → nota com 52, lista com 50);
3. venda a fiado NÃO é receita recebida: o PDV fiado não cria lançamento e não
   move o cartão "Recebimentos"; o dinheiro aparece quando é entregue;
4. obrigação e dinheiro são linhas próprias no relatório (pendente/cancelado não
   somam em "Recebido"), e filtro inválido é RECUSADO com mensagem, não vira
   "nenhum resultado";
5. o relatório de mensalidades tem três perguntas distintas — competência,
   vencimento e data do pagamento — cada uma rotulada na tela, e a competência
   histórica sem registro é marcada como tal;
6. valores em Decimal com a política de arredondamento existente: "1.234,56",
   "abc" e "0" são recusados sem escrever nada, "60,005" vira 60,01, e número
   grande é exibido com ponto de milhar (R$ 1.234.567,89);
7. permissão conferida na URL direta E no POST (leitura sem escrita não vê o
   botão de lançamento e não consegue postar); GET não gera cobrança nem
   movimentação por efeito colateral;
8. apresentação TASK_011 preservada: CSS versionado (`base.css?v=`), classes
   `fin-*` nas telas tocadas, vínculos de ida e volta ao mesmo recorte.

Uso: dentro de tests/rodar_testes.sh (app real + banco selado).
"""
import http.cookiejar
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_tres_passes as tp  # noqa: E402
from test_tres_passes import (  # noqa: E402
    BASE, preparar_banco, limpar_banco, _garantir_admin,
)

from banco import conectar, conta_id_por_tipo  # noqa: E402
import servico_fiado  # noqa: E402
from servico_fiado import moeda  # noqa: E402

PREFIXO = f"T12CONS-{uuid.uuid4().hex[:8]}"
HOJE = date.today()
ADMIN_ID = None
FALHAS = []
APROVADAS = []
F = {}
OP = None

# Toda chave de tentativa que ESTA suíte gerou. É a identidade que a limpeza usa
# para apagar `operacao_idempotencia` sem tocar nas chaves das outras suítes que
# rodam no mesmo banco descartável.
CHAVES_USADAS: set[str] = set()

ZERO = Decimal("0.00")

# Recortes por data usados pelas telas. Nada desta suíte cai fora deles, e as
# datas escolhidas (maio/julho) não colidem com o mês corrente nem com a
# geração automática de mensalidades (mês corrente + 12 futuros).
DE_MAI, ATE_MAI = "2026-05-01", "2026-05-31"
DE_JUL, ATE_JUL = "2026-07-01", "2026-07-31"


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


def chave():
    """Chave de tentativa desta suíte — registrada para a limpeza apagar só o
    que é dela (nunca as chaves de outra suíte no mesmo banco descartável)."""
    k = uuid.uuid4().hex
    CHAVES_USADAS.add(k)
    return k


def escalar(sql, params=(), padrao=0):
    # psycopg2 não aceita params=() (tuple index out of range): sem parâmetro é
    # None — foi o que derrubou a limpeza na primeira corrida da §5.
    with conectar() as conn:
        r = conn.execute(sql, params if params else None).fetchone()
        if not r:
            return padrao
        v = list(r.values())[0]
        return padrao if v is None else v


def linhas_sql(sql, params=()):
    with conectar() as conn:
        return [dict(r) for r in conn.execute(sql, params if params else None).fetchall()]


def texto(html) -> str:
    """O que o operador lê: tags fora, espaços colapsados."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def so_a_lista(html) -> str:
    """Só o corpo da LISTA do relatório.

    A página de relatório tem mais de um `tbody` (filtros de trabalhador, visão
    por trabalhador) e uma asserção sobre "o recorte contém o nome B" precisa
    distinguir os dois — por isso o corpo é lido a partir da tabela que tem a
    coluna Competência/Situação, não do primeiro `tbody` da página.
    """
    for m in re.finditer(r"<tbody>(.*?)</tbody>", html, re.S):
        bloco = m.group(1)
        if "<tr>" in bloco and ('class="fin-col-valor"' in bloco
                                or "Situação" in html[:m.start()][-4000:]):
            return bloco
    return ""


def q(txt) -> str:
    return urllib.parse.quote(str(txt), safe="")


def valor_do_card(html, rotulo) -> Decimal | None:
    """Valor de um cartão cujo RÓTULO vem antes do número (telas do Caixa).

    Devolve Decimal, não texto: a tela do Caixa formata com `%.2f` ("25.00") e
    as do financeiro com o filtro `moeda` ("25,00") — o número é o mesmo, e é o
    número que a prova tem de comparar. Sem isso, um "R$ 25,00" em qualquer
    lugar da página passaria a prova, e o extrato lista movimentos do legado.
    """
    m = re.search(re.escape(rotulo) + r'\s*</div>\s*<div[^>]*>\s*R\$\s*([\d.,]+)',
                  html, re.S)
    if not m:
        return None
    bruto = m.group(1).strip()
    if "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    return Decimal(bruto)


def card(html, rotulo) -> str:
    """Valor do cartão (markup TASK_011: valor, depois rótulo).

    O cartão é fechado em `</div>` ANTES do rótulo e pode ter um `<details>` de
    explicação depois — ler só o primeiro `</div>` após o valor é o que impede
    o teste de capturar o texto das explicações dos cartões seguintes.
    """
    # O rótulo pode carregar marcação (data, `<b>%</b>`): o corte é no primeiro
    # `</div>` DO PRÓPRIO stat-label, e o rótulo é conferido no texto dele —
    # sem isso o `.*?` atravessa o cartão inteiro e a asserção passa por engano.
    for m in re.finditer(r'stat-valor[^>]*>\s*(.*?)</div>\s*'
                         r'<div class="stat-label">(.*?)</div>', html, re.S):
        if texto(m.group(2)).strip().startswith(rotulo):
            return texto(m.group(1))
    return ""


# ── Fixtures ─────────────────────────────────────────────────────────────────

def montar_fixtures():
    global ADMIN_ID
    with conectar() as conn:
        ADMIN_ID = conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()["id"]
        F["cx"] = conn.execute(
            "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
            "VALUES (%s, 'suíte TASK_012 §5', %s, 1) RETURNING id",
            (f"{PREFIXO} Mercearia", True)).fetchone()["id"]
        F["prod"] = conn.execute(
            "INSERT INTO produtos (nome, categoria, preco_venda, quantidade_estoque, "
            "ativo, caixa_id) VALUES (%s, 'suíte', '20.00', 500, 1, %s) RETURNING id",
            (f"{PREFIXO} Pacotinho", F["cx"])).fetchone()["id"]
        F["especie"] = conta_id_por_tipo(conn, "especie")
        F["areceber"] = conta_id_por_tipo(conn, "areceber")
        conn.execute("UPDATE contas_financeiras SET ativo = 1 WHERE id = %s",
                     (F["especie"],))
    POST("/caixa/abrir", {"caixa_id": F["cx"], "saldo_inicial": "0"})
    F["sessao"] = escalar(
        "SELECT id FROM caixa_movimentos WHERE caixa_id=%s AND status='aberto' "
        "ORDER BY id DESC LIMIT 1", (F["cx"],))
    assert F["sessao"], "sessão de caixa não abriu"


def novo_trabalhador(rotulo, *, mensalidade="60.00", dia=10) -> tuple:
    nome = f"{PREFIXO} {rotulo}"
    tid = escalar(
        """INSERT INTO trabalhadores (nome_completo, ativo, valor_mensalidade,
                                      dia_vencimento, fiado_limite, fiado_credito)
           VALUES (%s, 1, %s, %s, 1000.00, 0) RETURNING id""",
        (nome, Decimal(mensalidade), dia))
    return tid, nome


def nova_divida(tid, valor, *, data_venda="2026-01-10", credito_usado="0.00") -> int:
    """Dívida de fiado como o PDV a lançaria (venda concluída, sessão vinculada)."""
    with conectar() as conn:
        return conn.execute(
            """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
                     troco, status, atendente_id, fiado_trabalhador_id, fiado_pago,
                     fiado_credito_usado, doacao_valor, caixa_movimento_id, criada_em)
               VALUES (%s, %s, %s, 'fiado', 0, 'concluida', %s, %s, FALSE, %s, 0, %s, %s)
               RETURNING id""",
            (F["cx"], data_venda, Decimal(valor), ADMIN_ID, tid,
             Decimal(credito_usado), F["sessao"], f"{data_venda} 09:00:00")
        ).fetchone()["id"]


def receber(tid, valor, venda_ids, *, k=None):
    dados = {"valor": valor, "forma_pagamento": "especie",
             "sessao_id": F["sessao"], "conta_pix_id": 0,
             "venda_ids": [str(v) for v in venda_ids],
             "idempotencia": k or chave()}
    return POST(f"/caixa/fiados/{tid}/pagar-divida", dados)


def venda_pdv_fiada(tid, quantidade, *, k=None):
    dados = {"caixa_id": F["cx"], "caixa_movimento_id": F["sessao"],
             "forma_pagamento": "fiado", "valor_recebido": "0",
             "itens_json": json.dumps([{"produto_id": F["prod"],
                                        "quantidade": quantidade}]),
             "trabalhador_id": tid, "imprimir": "1", "doacao_valor": "0",
             "idempotencia": k or chave()}
    return POST("/caixa/pdv/finalizar", dados)


def lancar(*, tipo="entrada", categoria="doacao", valor, data, status="pago",
           conta=None, competencia=None, trab=None, descricao=None,
           data_pagamento=None) -> int:
    """Lançamento do livro-caixa como o módulo o escreve (colunas próprias)."""
    with conectar() as conn:
        return conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, competencia, descricao,
                trabalhador_id, status, conta_id, data_pagamento, forma_pagamento)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'especie')
               RETURNING id""",
            (tipo, categoria, Decimal(valor), data, competencia,
             descricao or f"{PREFIXO} lançamento", trab, status, conta,
             data_pagamento)).fetchone()["id"]


def recebimento_fiado_id(tid) -> int:
    """O lançamento do último recebimento de fiado deste trabalhador."""
    return escalar(
        """SELECT m.id FROM financeiro_movimentacoes m
           JOIN fiado_alocacao a ON a.financeiro_id = m.id
           WHERE a.trabalhador_id = %s AND m.categoria = 'recebimento_fiado'
           ORDER BY m.id DESC LIMIT 1""", (tid,))


# ── 1. o saldo parcial é o mesmo número em toda tela ─────────────────────────

def p1_saldo_parcial_em_todas_as_telas():
    tid, nome = novo_trabalhador("Parcial")
    v1 = nova_divida(tid, "40.00")

    status, _, local = receber(tid, "15.00", [v1])
    verificar("fluxo: entrega de 15 para dívida de 40 é aceita",
              status == 303, f"status {status} local {local}")

    st, body, _ = GET(f"/financeiro?aba=pend&q={q(nome)}")
    t = texto(body)
    verificar("Visão Geral: a linha da dívida mostra recebido e saldo parcial",
              st == 200 and '<td class="fin-col-valor fin-pendente">R$ 25,00</td>'
              in body and "R$ 15,00" in t, f"status {st}")
    verificar("Visão Geral: nota do recorte soma original, recebido e saldo",
              "1 dívida(s) em aberto: R$ 40,00 em valor original, R$ 15,00 já "
              "recebido, R$ 25,00 em saldo" in t, t[:300])
    verificar("Visão Geral: dívida com alocação registrada não é chamada de histórica",
              "dívida(s) são anteriores ao registro" not in t)

    st, body2, _ = GET(f"/financeiro/fiados?trabalhador_id={tid}")
    t2 = texto(body2)
    verificar("Fiados: a mesma conta (40 − 15 = 25) na ficha do trabalhador",
              st == 200 and "R$ 25,00" in t2 and "R$ 15,00" in t2, f"status {st}")
    verificar("Fiados: a situação diz 'Recebida em parte' (nem paga, nem pendente)",
              "Recebida em parte" in t2, t2[:400])

    st, body3, _ = GET(f"/caixa/fiados/extrato/{tid}")
    saldo_extrato = valor_do_card(body3, "Saldo devedor (dívidas em aberto)")
    credito_extrato = valor_do_card(body3, "Crédito antecipado disponível")
    verificar("Extrato: o cartão de saldo do trabalhador é o mesmo 25,00 das outras "
              "telas (comparado em Decimal) e o crédito é número separado",
              st == 200 and saldo_extrato == Decimal("25.00")
              and credito_extrato == Decimal("0.00"),
              f"status {st} saldo={saldo_extrato} crédito={credito_extrato}")

    fin_id = recebimento_fiado_id(tid)
    st, body4, _ = GET(f"/financeiro/lancamento/{fin_id}")
    t4 = texto(body4)
    verificar("Detalhe do lançamento: destino do dinheiro rastreado até a dívida",
              st == 200 and "Para onde foi este dinheiro" in t4
              and f"Dívida #{v1}" in t4 and "R$ 15,00 abatido" in t4,
              f"status {st} · {t4[:200]}")


def p2_totais_do_recorte_nao_da_pagina():
    tid, nome = novo_trabalhador("Muitas")
    for _ in range(52):
        nova_divida(tid, "10.00")

    st, body, _ = GET(f"/financeiro?aba=pend&q={q(nome)}")
    t = texto(body)
    verificar("Totais: 52 dívidas somam no recorte ainda que a lista corte em 50",
              st == 200 and "52 dívida(s) em aberto: R$ 520,00 em valor original"
              in t and "R$ 520,00 em saldo" in t, f"status {st} · {t[:300]}")
    verificar("Totais: a lista avisa que a tela é uma página do recorte",
              "Mostrando os 50 mais recentes" in t, t[:400])
    verificar("Totais: o quadro da lista tem exatamente 50 linhas",
              body.count('<td class="fin-col-valor fin-pendente">R$ 10,00</td>') == 50,
              str(body.count('<td class="fin-col-valor fin-pendente">R$ 10,00</td>')))


def p3_legado_sem_prova():
    tid, nome = novo_trabalhador("Legado")
    v = nova_divida(tid, "30.00")

    st, body, _ = GET(f"/financeiro?aba=pend&q={q(nome)}")
    t = texto(body)
    verificar("Legado: dívida sem alocação exibe o valor de face como saldo",
              st == 200 and '<td class="fin-col-valor fin-pendente">R$ 30,00</td>'
              in body, f"status {st}")
    verificar("Legado: a limitação é dita ao operador, sem jargão técnico",
              "1 dívida(s) são anteriores ao registro de destino do recebimento" in t
              and "Nada foi distribuído por suposição" in t, t[:400])

    with conectar() as conn:
        verificar("Legado: o serviço não inventa abate para dívida sem alocação",
                  servico_fiado.alocado_por_venda(conn, [v]) == {}, "havia alocação")


def p4_venda_fiada_nao_e_receita():
    tid, nome = novo_trabalhador("PDV")
    url = f"/financeiro?de={HOJE.isoformat()}&ate={HOJE.isoformat()}&q={q(nome)}"

    st, body, _ = GET(url)
    antes = card(body, "Recebimentos registrados")
    n_antes = escalar(
        "SELECT count(*) FROM financeiro_movimentacoes WHERE trabalhador_id = %s",
        (tid,))

    status, _, local = venda_pdv_fiada(tid, 2)  # 2 × R$ 20,00 a fiado
    verificar("PDV: venda a fiado conclui", status == 303, f"status {status} {local}")

    st, body, _ = GET(url)
    depois = card(body, "Recebimentos registrados")
    n_depois = escalar(
        "SELECT count(*) FROM financeiro_movimentacoes WHERE trabalhador_id = %s",
        (tid,))
    verificar("PDV: venda a fiado não cria lançamento de dinheiro",
              n_depois == n_antes, f"{n_antes} → {n_depois}")
    verificar("PDV: venda a fiado não move o cartão 'Recebimentos registrados'",
              depois == antes, f"'{antes}' → '{depois}'")
    verificar("PDV: a dívida entra no bloco de fiado em aberto, não como receita",
              "Nenhum fiado em aberto" not in texto(body), texto(body)[:300])

    dividas = [r["id"] for r in linhas_sql(
        "SELECT id FROM vendas_pdv WHERE fiado_trabalhador_id = %s", (tid,))]
    status, _, _ = receber(tid, "40.00", dividas)
    verificar("Recebimento: a entrega do dinheiro é que vira entrada",
              status == 303, f"status {status}")

    st, body, _ = GET(url)
    depois2 = card(body, "Recebimentos registrados")
    verificar("Recebimento: 'Recebimentos' sobe exatamente o valor entregue",
              depois2.replace("+", "").strip() == "R$ 40,00",
              f"'{antes}' → '{depois2}'")


def p5_relatorios_separam_obrigacao_de_dinheiro():
    lancar(valor="100.00", data="2026-05-05", categoria="doacao",
           descricao=f"{PREFIXO} doação paga")
    lancar(valor="50.00", data="2026-05-20", categoria="doacao", status="pendente",
           descricao=f"{PREFIXO} doação a receber")
    lancar(valor="7.00", data="2026-05-07", categoria="doacao", status="cancelado",
           descricao=f"{PREFIXO} doação cancelada")
    lancar(valor="20.00", data="2026-05-06", tipo="saida", categoria="energia",
           descricao=f"{PREFIXO} conta de luz")

    st, body, _ = GET(f"/financeiro/relatorios?data_inicio={DE_MAI}"
                      f"&data_fim={ATE_MAI}&categoria=doacao")
    t = texto(body)
    verificar("Relatórios: 'Recebido' soma só o pago (R$ 100,00), não 150 nem 157",
              st == 200 and card(body, "Recebido (entradas pagas)") == "R$ 100,00",
              f"status {st} · card={card(body, 'Recebido (entradas pagas)')}")
    verificar("Relatórios: obrigação pendente tem linha própria, rotulada",
              card(body, "A receber (pendentes)") == "R$ 50,00"
              and "não é dinheiro recebido" in t.lower(),
              f"card={card(body, 'A receber (pendentes)')}")
    verificar("Relatórios: cancelado não soma e é informado à parte",
              card(body, "Cancelados (não somam)") == "R$ 7,00",
              f"card={card(body, 'Cancelados (não somam)')}")
    verificar("Relatórios: a tela diz qual data o filtro de data usa",
              "vencimento / data do lançamento" in t, t[:300])

    st2, body2, _ = GET(f"/financeiro/relatorios?data_inicio={DE_MAI}"
                        f"&data_fim={ATE_MAI}&categoria=energia")
    verificar("Relatórios: saídas pagas em cartão próprio",
              st2 == 200 and card(body2, "Pago (saídas pagas)") == "R$ 20,00",
              f"status {st2} · card={card(body2, 'Pago (saídas pagas)')}")

    lancar(valor="40.00", data="2026-05-08", categoria="transferencia",
           conta=F["especie"], descricao=f"{PREFIXO} transferência interna")
    st3, body3, _ = GET(f"/financeiro/relatorios?data_inicio={DE_MAI}"
                        f"&data_fim={ATE_MAI}&categoria=transferencia")
    verificar("Relatórios: transferência é contada como movimento interno do centro",
              st3 == 200 and "1 são transferência/sangria entre contas do próprio "
              "centro" in texto(body3), f"status {st3} · {texto(body3)[:300]}")


def p6_filtros_invalidos_recusados():
    st, body, _ = GET(f"/financeiro/relatorios?data_inicio={DE_MAI}"
                      f"&data_fim={ATE_MAI}&conta_id=abc")
    t = texto(body)
    verificar("Relatórios: conta inválida é recusada com mensagem, sem esvaziar a tela",
              st == 200 and "Conta inválida — o filtro não foi aplicado" in t
              and "R$ 100,00" in t, f"status {st} · {t[:200]}")

    st2, body2, _ = GET(f"/financeiro/relatorios?data_inicio=31/05/2026"
                        f"&data_fim={ATE_MAI}")
    verificar("Relatórios: data fora de AAAA-MM-DD é recusada (não vira 'nada encontrado')",
              st2 == 200 and "Data inicial “31/05/2026” inválida" in texto(body2),
              f"status {st2}")

    st3, body3, _ = GET(f"/financeiro/relatorios?tipo=qualquer"
                        f"&data_inicio={DE_MAI}&data_fim={ATE_MAI}")
    verificar("Relatórios: tipo desconhecido é recusado e o resto do recorte vale",
              st3 == 200 and "Tipo fora de Entrada/Saída" in texto(body3),
              f"status {st3}")

    st4, body4, _ = GET("/financeiro/mensalidades/relatorio?mes=2026-13")
    verificar("Mensalidades: período inválido é recusado com mensagem simples",
              st4 == 200 and "não é um mês válido" in texto(body4), f"status {st4}")

    st5, body5, _ = GET("/financeiro?por=33&nat=quimera&sentido=tudo&sit=x")
    t5 = texto(body5)
    verificar("Visão Geral: cada filtro inválido tem a própria recusa",
              st5 == 200 and "33 itens por página não é uma opção" in t5
              and "Natureza “quimera” desconhecida" in t5
              and "Sentido fora dos valores permitidos" in t5
              and "Situação fora dos valores permitidos" in t5, f"status {st5}")


def p7_tres_datas_diferentes():
    tid_a, nome_a = novo_trabalhador("Competência A", mensalidade="60.00")
    tid_b, nome_b = novo_trabalhador("Competência B", mensalidade="60.00")

    r1 = lancar(categoria="mensalidade", valor="60.00", data="2026-05-10",
                competencia="2026-05", trab=tid_a, status="pago",
                data_pagamento="2026-06-02",
                descricao=f"{PREFIXO} mensalidade 2026-05")
    lancar(categoria="mensalidade", valor="60.00", data="2026-06-10",
           competencia="2026-06", trab=tid_b, status="pendente",
           descricao=f"{PREFIXO} mensalidade 2026-06")
    lancar(categoria="mensalidade", valor="55.00", data="2026-05-15",
           competencia=None, trab=tid_a, status="pendente",
           descricao=f"{PREFIXO} mensalidade histórica")

    st, body, _ = GET("/financeiro/mensalidades/relatorio?mes=2026-05&data_uso=comp")
    lista, t = so_a_lista(body), texto(body)
    verificar("Mensalidades: COMPETÊNCIA traz o mês a que a cobrança se refere",
              st == 200 and "Competência — o mês a que a cobrança se refere" in t
              and "10/05/2026" in lista and "15/05/2026" in lista
              and nome_b not in lista, f"status {st}")
    verificar("Mensalidades: linha histórica sem competência é marcada como tal",
              "histórica: sem competência registrada" in t, t[:400])

    st2, body2, _ = GET("/financeiro/mensalidades/relatorio?mes=2026-06&data_uso=venc")
    lista2, t2 = so_a_lista(body2), texto(body2)
    verificar("Mensalidades: VENCIMENTO traz outra lista (o B, não o A)",
              st2 == 200 and "Vencimento — a data em que a mensalidade vence" in t2
              and nome_b in lista2 and nome_a not in lista2, f"status {st2}")

    st3, body3, _ = GET("/financeiro/mensalidades/relatorio?mes=2026-06&data_uso=pag")
    lista3, t3 = so_a_lista(body3), texto(body3)
    verificar("Mensalidades: DATA DO PAGAMENTO traz o dinheiro que entrou naquele mês",
              st3 == 200 and "Data do pagamento — o mês em que o dinheiro entrou" in t3
              and "02/06/2026" in lista3 and nome_b not in lista3, f"status {st3}")
    verificar("Mensalidades: os três recortes são perguntas de fato diferentes",
              lista != lista2 and lista2 != lista3, "recortes idênticos")

    st4, body4, _ = GET("/financeiro/mensalidades/relatorio?mes=2026-06&data_uso=comp")
    venc = card(body4, "Vencidas até hoje ")
    verificar("Mensalidades: 'Vencidas até hoje' é sempre pelo vencimento "
              "(o rótulo diz a data e a explicação separa os recortes)",
              st4 == 200 and venc == "R$ 60,00"
              and "aqui é sempre o vencimento" in texto(body4),
              f"card={venc!r}")

    st5, body5, _ = GET("/financeiro/mensalidades/relatorio?mes=2026-05"
                        "&data_uso=comp&visao=trabalhador")
    verificar("Mensalidades: visão por trabalhador soma o MESMO recorte",
              st5 == 200 and nome_a in texto(body5)
              and "R$ 60,00" in texto(body5), f"status {st5}")

    st6, body6, _ = GET(f"/financeiro/lancamento/{r1}")
    verificar("Detalhe do lançamento: competência lida da coluna própria",
              st6 == 200 and "Competência" in texto(body6)
              and "2026-05" in texto(body6), f"status {st6}")

    st7, body7, _ = GET(f"/financeiro/mensalidades?trabalhador_id={tid_a}")
    verificar("Tela de mensalidades: valor em formato brasileiro, sem float no texto",
              st7 == 200 and "R$ 60,00" in texto(body7) and "R$ 60.00" not in body7,
              f"status {st7}")


def p8_politica_de_valor():
    antes = escalar("SELECT count(*) FROM financeiro_movimentacoes")

    for envoi in ("1.234,56", "abc", "0", "-10"):
        st, _, local = POST("/financeiro/nova", {
            "tipo": "entrada", "categoria": "outros", "valor": envoi,
            "data_movimentacao": "2026-07-05", "status": "pago",
            "descricao": f"{PREFIXO} valor recusado", "idempotencia": chave()})
        verificar(f"Formulário: “{envoi}” é recusado sem escrever nada",
                  st == 303 and local and "erro=valor_invalido" in local,
                  f"status {st} local {local}")
    verificar("Formulário: nenhuma das recusas criou lançamento",
              escalar("SELECT count(*) FROM financeiro_movimentacoes") == antes,
              "linhas novas")

    st, _, _ = POST("/financeiro/nova", {
        "tipo": "entrada", "categoria": "outros", "valor": "60,005",
        "data_movimentacao": "2026-07-05", "status": "pago",
        "descricao": f"{PREFIXO} arredondamento", "idempotencia": chave()})
    guardado = escalar(
        "SELECT valor FROM financeiro_movimentacoes WHERE descricao = %s",
        (f"{PREFIXO} arredondamento",))
    verificar("Formulário: 60,005 fecha em 60,01 (duas casas, arredondamento comercial)",
              st == 303 and moeda(guardado) == Decimal("60.01"),
              f"status {st} guardado {guardado}")

    POST("/financeiro/nova", {
        "tipo": "entrada", "categoria": "outros", "valor": "1234567.89",
        "data_movimentacao": "2026-07-06", "status": "pago",
        "conta_id": F["especie"],
        "descricao": f"{PREFIXO} valor grande", "idempotencia": chave()})

    st2, body2, _ = GET(f"/financeiro?de={DE_JUL}&ate={ATE_JUL}"
                        f"&q={q(PREFIXO + ' valor grande')}")
    verificar("Visão Geral: número grande exibido com ponto de milhar",
              st2 == 200 and "R$ 1.234.567,89" in texto(body2),
              f"status {st2} · {texto(body2)[:300]}")

    st3, body3, _ = GET(f"/financeiro/relatorios?data_inicio={DE_JUL}"
                        f"&data_fim={ATE_JUL}&categoria=outros")
    verificar("Relatórios: o mesmo número, sem float nem vírgula invertida",
              st3 == 200 and "R$ 1.234.567,89" in texto(body3)
              and "1234567.89" not in body3, f"status {st3}")

    st4, body4, _ = GET(f"/financeiro?de={DE_JUL}&ate={ATE_JUL}")
    t4 = texto(body4)
    # O acumulado da conta só pode existir rotulado: Σ do valor + a data até a
    # qual ele conta, senão o operador lê o acumulado como se fosse o período.
    acumulados = re.findall(r'Σ\s*(R\$[^<]*)', body4)
    verificar("Visão Geral: acumulado por conta vem com Σ e declara até qual data conta",
              st4 == 200 and bool(acumulados) and "não só o período exibido" in t4,
              f"status {st4} acumulados={acumulados}")


def p9_permissoes_na_url_e_no_post():
    from rotas.auth import hash_senha

    with conectar() as conn:
        gid_caixa = conn.execute(
            "INSERT INTO grupos (nome, descricao) VALUES (%s, 'suíte §5') RETURNING id",
            (f"{PREFIXO} grupo caixa",)).fetchone()["id"]
        conn.execute(
            """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
               VALUES (%s, 'caixa', TRUE, TRUE, FALSE)""", (gid_caixa,))
        gid_lei = conn.execute(
            "INSERT INTO grupos (nome, descricao) VALUES (%s, 'suíte §5') RETURNING id",
            (f"{PREFIXO} grupo leitura",)).fetchone()["id"]
        conn.execute(
            """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
               VALUES (%s, 'financeiro', TRUE, FALSE, FALSE)""", (gid_lei,))
        F["grupos"] = [gid_caixa, gid_lei]
        for usuario, gid in (("t12cons_caixa", gid_caixa),
                            ("t12cons_leitura", gid_lei)):
            uid = conn.execute(
                """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
                   VALUES (%s, %s, %s, 1) RETURNING id""",
                (usuario, usuario, hash_senha("teste123"))).fetchone()["id"]
            conn.execute(
                "INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, %s)",
                (uid, gid))

    def logar(usuario):
        o = _nova_opener()
        POST("/login", {"nome_usuario": usuario, "senha": "teste123"}, o=o)
        return o

    o_caixa = logar("t12cons_caixa")
    for rota in ("/financeiro", "/financeiro/relatorios", "/financeiro/fiados",
                 "/financeiro/mensalidades/relatorio", "/financeiro/mensalidades"):
        st, body, _ = GET(rota, o=o_caixa)
        verificar(f"Permissão: {rota} é negada para quem só tem Caixa",
                  st == 403 and "Acesso negado" in texto(body), f"status {st}")

    st, _, _ = POST("/financeiro/mensalidades/gerar",
                    {"trabalhador_id": 1, "idempotencia": chave()}, o=o_caixa)
    verificar("Permissão: POST direto de outro módulo também é negado",
              st == 403, f"status {st}")

    o_lei = logar("t12cons_leitura")
    st, body, _ = GET("/financeiro", o=o_lei)
    verificar("Permissão: leitura entra na Visão Geral", st == 200, f"status {st}")
    verificar("Permissão: sem escrita, o botão de novo lançamento não aparece",
              "Nova movimentação" not in texto(body), "botão visível")

    st2, _, _ = POST("/financeiro/nova", {
        "tipo": "entrada", "categoria": "outros", "valor": "10.00",
        "data_movimentacao": "2026-07-08", "status": "pago",
        "descricao": f"{PREFIXO} leitura tentando escrever",
        "idempotencia": chave()}, o=o_lei)
    verificar("Permissão: ler não permite postar — nem URL, nem escrita",
              st2 == 403 and escalar(
                  "SELECT count(*) FROM financeiro_movimentacoes WHERE descricao = %s",
                  (f"{PREFIXO} leitura tentando escrever",)) == 0, f"status {st2}")


def p10_get_nao_movimenta_dinheiro():
    tid, nome = novo_trabalhador("Somente leitura")
    nova_divida(tid, "12.00")

    def retrato():
        return {
            "lancamentos": escalar("SELECT count(*) FROM financeiro_movimentacoes"),
            "alocacoes": escalar("SELECT count(*) FROM fiado_alocacao"),
            "eventos": escalar("SELECT count(*) FROM caixa_sessao_eventos"),
            "vendas": escalar("SELECT count(*) FROM vendas_pdv"),
            "credito": escalar(
                "SELECT COALESCE(SUM(fiado_credito), 0) FROM trabalhadores"),
            "mensalidades": escalar(
                """SELECT count(*) FROM financeiro_movimentacoes
                   WHERE categoria = 'mensalidade'"""),
        }

    telas = [
        f"/financeiro?aba=pend&q={q(nome)}",
        "/financeiro?aba=pend",
        "/financeiro/relatorios",
        "/financeiro/relatorios?data_inicio=2026-01-01&data_fim=2026-12-31"
        "&tipo=entrada",
        "/financeiro/fiados",
        f"/financeiro/fiados?trabalhador_id={tid}",
        "/financeiro/mensalidades/relatorio?mes=2026-05&data_uso=venc",
        f"/financeiro/mensalidades?trabalhador_id={tid}",
        f"/caixa/fiados/extrato/{tid}",
        "/financeiro?aba=pend&q=" + q("nome que nao existe"),
    ]

    antes = retrato()
    statuses = []
    for _ in range(2):
        for rota in telas:
            statuses.append(GET(rota)[0])
    depois = retrato()

    verificar("Consulta: nenhuma tela responde 500",
              all(s != 500 for s in statuses), str(sorted(set(statuses))))
    verificar("Consulta: abrir as telas não cria nem altera lançamento, alocação, "
              "evento, venda, crédito ou cobrança",
              antes == depois, f"{antes} → {depois}")


def p11_apresentacao_task011_preservada():
    st, body, _ = GET("/financeiro?aba=pend")
    verificar("TASK_011: CSS servido com versão (anti-cache) na Visão Geral",
              st == 200 and re.search(r"base\.css\?v=[1-9]\d{8,}", body) is not None,
              f"status {st}")
    verificar("TASK_011: vocabulário de classes reutilizado, sem interface genérica nova",
              'class="grade-cards fin-cards"' in body and "fin-nav" in body)

    for rota in ("/financeiro/relatorios", "/financeiro/fiados",
                 "/financeiro/mensalidades/relatorio"):
        st2, body2, _ = GET(rota)
        verificar(f"TASK_011: {rota} usa a apresentação da Visão Geral",
                  st2 == 200 and "base.css?v=" in body2
                  and ("fin-cabecalho" in body2 or "fin-cards" in body2),
                  f"status {st2}")

    recorte = f"/financeiro?de={DE_JUL}&ate={ATE_JUL}"  # forma que a rota aceita
    mov = lancar(valor="3.00", data="2026-07-09",
                 descricao=f"{PREFIXO} volta ao recorte")
    # O detalhe pode responder a página (200) com o link de volta, ou mandar de
    # volta para a Visão Geral (303) — em nenhum dos dois o recorte se perde.
    st3, body3, volta3 = GET(f"/financeiro/lancamento/{mov}?volta={q(recorte)}")
    # A página devolve o link já com o HTML-escaping do `&` (validade no nav),
    # ou um 303 cujo destino é o recorte — em nenhum dos dois o recorte se perde.
    verificar("TASK_011: retorno do detalhe preserva o recorte consultado",
              st3 in (200, 303) and (recorte in body3 or recorte.replace("&", "&amp;") in body3
                                     or (volta3 or "").endswith(recorte)),
              f"status {st3} volta={volta3}")

    st4, body4, _ = GET("/financeiro/lancamento/1?volta=" + q("https://exemplo.invalid/x"))
    verificar("TASK_011: link de volta forâneo não vira redirecionamento",
              "exemplo.invalid" not in body4, f"status {st4}")


def p12_servico_e_a_unica_conta():
    """As telas não podem divergir do serviço: é a mesma função, o mesmo número."""
    tid, nome = novo_trabalhador("Unificação")
    v1 = nova_divida(tid, "40.00")
    v2 = nova_divida(tid, "10.00")
    receber(tid, "15.00", [v1])

    with conectar() as conn:
        linhas, tot = servico_fiado.fiados_em_aberto_para_consulta(conn)
    meu = {l["id"]: l for l in linhas if l["fiado_trabalhador_id"] == tid}

    verificar("Serviço: dívida parcial fica com 25 de saldo e 15 recebido",
              meu[v1]["saldo"] == Decimal("25.00")
              and meu[v1]["recebido"] == Decimal("15.00"), str(meu.get(v1)))
    verificar("Serviço: dívida intocada continua de face e marcada sem prova",
              meu[v2]["saldo"] == Decimal("10.00")
              and meu[v2]["alocacoes_registradas"] is False, str(meu.get(v2)))
    verificar("Serviço: saldo do recorte é a soma das dívidas e nunca é negativo",
              tot["saldo"] == sum((l["saldo"] for l in linhas), ZERO)
              and tot["saldo"] >= ZERO, str(tot))

    with conectar() as conn:
        por_trab = servico_fiado.saldo_por_trabalhador(conn)
    verificar("Serviço: o saldo por trabalhador bate com o da consulta",
              por_trab[tid]["saldo"] == meu[v1]["saldo"] + meu[v2]["saldo"],
              str(por_trab.get(tid)))

    st, body, _ = GET(f"/financeiro/fiados?trabalhador_id={tid}")
    verificar("Tela: o relatório usa o número do serviço (35,00 = 25 + 10)",
              st == 200 and "R$ 35,00" in texto(body), f"status {st}")

    adiantar = POST(f"/caixa/fiados/credito/{tid}", {
        "valor": "20.00", "descricao": f"{PREFIXO} adiantado",
        "forma_pagamento": "especie", "sessao_id": F["sessao"],
        "conta_pix_id": 0, "idempotencia": chave()})
    st2, body2, _ = GET(f"/financeiro/fiados?trabalhador_id={tid}")
    t2 = texto(body2)
    verificar("Tela: crédito antecipado aparece à parte e não abate o saldo devedor",
              adiantar[0] == 303 and st2 == 200 and "R$ 35,00" in t2
              and "R$ 20,00" in t2, f"{adiantar[0]} / {st2}")


FUNCOES = [
    p1_saldo_parcial_em_todas_as_telas,
    p2_totais_do_recorte_nao_da_pagina,
    p3_legado_sem_prova,
    p4_venda_fiada_nao_e_receita,
    p5_relatorios_separam_obrigacao_de_dinheiro,
    p6_filtros_invalidos_recusados,
    p7_tres_datas_diferentes,
    p8_politica_de_valor,
    p9_permissoes_na_url_e_no_post,
    p10_get_nao_movimenta_dinheiro,
    p11_apresentacao_task011_preservada,
    p12_servico_e_a_unica_conta,
]


def _colunas_brutas(conn, tabela: str) -> set:
    """Colunas que a tabela tem HOJE — o esquema evolui entre execuções
    (TASK_008/009/012) e a limpeza não pode citar coluna que já não exista."""
    return {r["column_name"] for r in conn.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s""", (tabela,)).fetchall()}


# Tabelas observadas pelo piso. `tipos_doacao` é catálogo (a suíte nunca o
# apaga): serve de SENTINELA — se o piso dela cair, quem limpou não foi esta
# suíte, e a prova não pode passar em silêncio sobre isso.
TABELAS_DO_PISO = ("operacao_idempotencia", "financeiro_movimentacoes",
                   "vendas_pdv", "trabalhadores", "caixa_movimentos",
                   "fiado_alocacao", "caixa_sessao_eventos", "tipos_doacao")


def _contagens_do_banco() -> dict:
    """Retrato das tabelas que a suíte toca — e da sentinela de catálogo."""
    return {t: escalar(f"SELECT count(*) FROM {t}") for t in TABELAS_DO_PISO}


def limite_inferior_antes():
    """O piso de linhas de cada tabela ANTES da suíte escrever.

    A limpeza tem de devolver o banco ao piso (ou acima dele, se outra suíte
    rodou no meio) — abaixo do piso significa que apagamos dado que não era
    nosso. Chamado em `main()`, antes de qualquer fixture.
    """
    return _contagens_do_banco()


def limpar_suíte():
    """Apaga só o que ESTA suíte criou (dependentes primeiro — FK RESTRICT).

    O banco descartável é compartilhado entre as suítes do mesmo runner; por
    isso nada aqui pode ser apagado "por tabela". Cada DELETE se apoia numa
    chave que esta execução criou: a marca `PREFIXO` (nome de trabalhador,
    descrição de lançamento, nome do caixa), o id das fixtures, ou a chave de
    tentativa registrada em `CHAVES_USADAS`. Dado de outra execução fica onde
    está — e as contagens abaixo devolvem isso por escrito.
    """
    padrao = f"{PREFIXO}%"
    contagem = {}
    with conectar() as conn:
        vendas = [r["id"] for r in conn.execute(
            """SELECT id FROM vendas_pdv WHERE fiado_trabalhador_id IN
               (SELECT id FROM trabalhadores WHERE nome_completo LIKE %s)
               OR caixa_id = %s""", (padrao, F.get("cx") or -1)).fetchall()]
        fins = [r["id"] for r in conn.execute(
            """SELECT id FROM financeiro_movimentacoes
               WHERE descricao LIKE %s""", (f"%{PREFIXO}%",)).fetchall()]
        trab = [r["id"] for r in conn.execute(
            "SELECT id FROM trabalhadores WHERE nome_completo LIKE %s",
            (padrao,)).fetchall()]
        sessoes = [r["id"] for r in conn.execute(
            "SELECT id FROM caixa_movimentos WHERE caixa_id = %s",
            (F.get("cx") or -1,)).fetchall()]
        usuarios = [r["id"] for r in conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario LIKE 't12cons_%'", None).fetchall()]

        # ── de onde saem as chaves de tentativa, antes de apagar as fixtures ──
        documentos = {str(i) for i in vendas} | {str(i) for i in fins}
        chaves = {r["chave"] for r in conn.execute(
            """SELECT chave FROM operacao_idempotencia
               WHERE documento = ANY(%s)""",
            (sorted(documentos) or ["-"],)).fetchall()}
        chaves |= set(CHAVES_USADAS)

        # ── alocações: as do trabalhador da suíte e as que apontam para
        #    lançamento/venda da suíte (um mesmo nunca é de outra execução) ──
        cond_aloc, params_aloc = [], []
        if trab:
            cond_aloc.append("trabalhador_id = ANY(%s)")
            params_aloc.append(trab)
        if fins:
            cond_aloc.append("financeiro_id = ANY(%s)")
            params_aloc.append(fins)
        if vendas:
            cond_aloc.append("venda_id = ANY(%s)")
            params_aloc.append(vendas)
        if cond_aloc:
            contagem["fiado_alocacao"] = conn.execute(
                f"DELETE FROM fiado_alocacao WHERE {' OR '.join(cond_aloc)}",
                params_aloc).rowcount

        for tabela in ("fiado_credito_mov",):
            cols = _colunas_brutas(conn, tabela)
            cond, params = [], []
            if trab and "trabalhador_id" in cols:
                cond.append("trabalhador_id = ANY(%s)")
                params.append(trab)
            if fins and "financeiro_id" in cols:
                cond.append("financeiro_id = ANY(%s)")
                params.append(fins)
            if cond:
                contagem[tabela] = conn.execute(
                    f"DELETE FROM {tabela} WHERE {' OR '.join(cond)}",
                    params).rowcount

        cols = _colunas_brutas(conn, "caixa_sessao_eventos")
        cond, params = [], []
        for coluna, ids in (("venda_id", vendas), ("financeiro_id", fins),
                            ("sessao_id", sessoes)):
            if ids and coluna in cols:
                cond.append(f"{coluna} = ANY(%s)")
                params.append(ids)
        if cond:
            contagem["caixa_sessao_eventos"] = conn.execute(
                f"DELETE FROM caixa_sessao_eventos WHERE {' OR '.join(cond)}",
                params).rowcount

        if vendas:
            contagem["vendas_pdv_itens"] = conn.execute(
                "DELETE FROM vendas_pdv_itens WHERE venda_id = ANY(%s)",
                (vendas,)).rowcount
            conn.execute("DELETE FROM vendas_pdv_cancelamento WHERE venda_id = ANY(%s)",
                         (vendas,))
            contagem["vendas_pdv"] = conn.execute(
                "DELETE FROM vendas_pdv WHERE id = ANY(%s)", (vendas,)).rowcount

        cols = _colunas_brutas(conn, "estoque_movimentos")
        if F.get("prod") and "produto_id" in cols:
            contagem["estoque_movimentos"] = conn.execute(
                "DELETE FROM estoque_movimentos WHERE produto_id = %s",
                (F["prod"],)).rowcount
        if sessoes:
            cols = _colunas_brutas(conn, "caixa_sangrias")
            if "caixa_movimento_id" in cols:
                conn.execute("DELETE FROM caixa_sangrias WHERE caixa_movimento_id = ANY(%s)",
                             (sessoes,))
        if F.get("cx"):
            contagem["caixa_movimentos"] = conn.execute(
                "DELETE FROM caixa_movimentos WHERE caixa_id = %s",
                (F["cx"],)).rowcount

        if fins:
            contagem["financeiro_movimentacoes"] = conn.execute(
                "DELETE FROM financeiro_movimentacoes WHERE id = ANY(%s)",
                (fins,)).rowcount
        if F.get("prod"):
            conn.execute("DELETE FROM produtos WHERE id = %s", (F["prod"],))
        if trab:
            contagem["trabalhadores"] = conn.execute(
                "DELETE FROM trabalhadores WHERE id = ANY(%s)", (trab,)).rowcount
        if F.get("cx"):
            conn.execute("DELETE FROM caixas WHERE id = %s", (F["cx"],))
        if usuarios:
            conn.execute("DELETE FROM usuarios_grupos WHERE usuario_id = ANY(%s)",
                         (usuarios,))
            conn.execute("DELETE FROM atendentes WHERE id = ANY(%s)", (usuarios,))
        if F.get("grupos"):
            conn.execute("DELETE FROM grupos_permissoes WHERE grupo_id = ANY(%s)",
                         (F["grupos"],))
            conn.execute("DELETE FROM grupos WHERE id = ANY(%s)", (F["grupos"],))

        # ── por último a identidade das tentativas: só as desta execução ──
        if chaves:
            contagem["operacao_idempotencia"] = conn.execute(
                "DELETE FROM operacao_idempotencia WHERE chave = ANY(%s)",
                (sorted(chaves),)).rowcount
    return contagem


def conferir_rastro(piso_antes: dict) -> bool:
    """A limpeza devolve o banco ao que era — nunca abaixo do piso da suíte.

    Distingue as duas coisas que podem ter acontecido e que NÃO são a mesma
    prova: (a) esta limpeza apagou além do próprio rastro → FALHA; (b) o banco
    foi recriado por outro processo durante a corrida (o runner recria o banco
    descartável a cada execução) → o piso não vale mais, e a prova tem de dizer
    isso em vez de "verde".
    """
    depois = _contagens_do_banco()
    abaixo = {t: (piso_antes[t], depois[t]) for t in depois
              if depois[t] < piso_antes[t]}
    if not abaixo:
        verificar("Limpeza: nenhuma tabela fica com menos linhas que o piso "
                  "anterior à suíte (nada de outra execução foi apagado)", True)
        return True
    # O catálogo-sentinela caiu: esta suíte não o apaga, logo outro processo
    # recriou o banco no meio — não é prova de limpeza, é corrida de ambiente.
    sentinela = "tipos_doacao" in abaixo
    FALHAS.append(
        "conferência do rastro: "
        + ("banco recriado por outro processo durante a corrida (sentinela "
           "tipos_doacao abaixo do piso) — repetir a corrida isolada, sem outra "
           "execução concorrente no mesmo banco descartável"
           if sentinela else
           f"a limpeza apagou abaixo do piso: {abaixo}"))
    print(f"  FALHOU conferência do rastro: {abaixo}"
          f"{' (sentinela caída: banco recriado por outro processo)' if sentinela else ''}")
    return False


def main():
    global OP
    _conferir_banco_do_servidor()
    preparar_banco()
    _garantir_admin()
    # Piso de linhas de cada tabela ANTES de escrever: a limpeza final tem de
    # devolver o banco a este piso (prova de que ela apagou só o próprio rastro).
    piso_antes = limite_inferior_antes()
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

    try:
        contagem = limpar_suíte()
        print("\n[limpeza] rastro desta suíte removido: "
              + ", ".join(f"{t}={n}" for t, n in sorted(contagem.items())))
    except Exception as e:
        contagem = {}
        FALHAS.append(f"limpeza — exceção {type(e).__name__}: {e}")
        print(f"  [limpeza] {e}")
        traceback.print_exc()
    try:
        conferir_rastro(piso_antes)
    except Exception as e:
        FALHAS.append(f"conferência do rastro — exceção {type(e).__name__}: {e}")
        print(f"  [conferência] {e}")

    total = len(APROVADAS) + len(FALHAS)
    print(f"\n{len(APROVADAS)}/{total} verificações aprovadas "
          "(TASK_012 §5 — consultas, saldos e experiência integrada)")
    if FALHAS:
        print("\nFalhas:")
        for f in FALHAS:
            print(f"  - {f}")
    limpar_banco()
    return 0 if not FALHAS else 1


if __name__ == "__main__":
    sys.exit(main())
