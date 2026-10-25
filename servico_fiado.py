"""Fiado: destino explícito do dinheiro recebido (TASK_012 §4).

Uma entrega de dinheiro é UMA entrada no financeiro (financeiro_movimentacoes)
e pode quitar várias dívidas e ainda deixar excedente como crédito antecipado.
Para onde ela foi é decisão do operador, gravada linha a linha em
`fiado_alocacao`: uma linha por dívida abatida (venda_id) e, quando sobra, uma
única linha de excedente (tipo='credito', venda_id NULL). A soma das linhas é
exatamente o valor recebido — `repartir` é a função que fecha a conta, em
centavos, sem depender de texto, valor aproximado, data ou FIFO presumido.

Sem alocação registrada a dívida continua devendo o valor de face: nada aqui
deduz abate parcial de indícios. O histórico anterior a esta entrega fica
marcado como "sem alocação comprovada" (ver `limitacao_legado`).
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0.00")


def moeda(valor) -> Decimal:
    """Decimal de duas casas, arredondamento comercial — o único tipo que
    fecha a conta do fiado sem centavo perdido (float é recusado)."""
    if isinstance(valor, float):
        valor = f"{valor:.2f}"
    return Decimal(str(valor if valor is not None else 0)).quantize(
        ZERO, rounding=ROUND_HALF_UP)


class FiadoInvalido(Exception):
    """Recusa legível para a tela, antes de qualquer escrita."""

    def __init__(self, chave: str, motivo: str):
        super().__init__(motivo)
        self.chave = chave
        self.motivo = motivo


def dividas_em_aberto(conn, trabalhador_id, *, travar=False, venda_ids=None):
    """Dívidas de fiado em aberto, com valor original, já recebido e saldo.

    `alocado` vem de `fiado_alocacao` (vínculo durável com o lançamento);
    `credito_usado` é o crédito antecipado consumido no ato da compra — os dois
    abatimentos legítimos do valor de face. Dívida sem nenhuma alocação gravada
    tem `alocacoes_registradas = False` e saldo igual ao valor de face: é a
    limitação explícita do legado, não uma presunção de quitação.

    O bloqueio (`travar=True`) percorre as dívidas SEMPRE por id crescente,
    para que duas requisições concorrentes tomem a mesma ordem de locks e não
    deadloquem; a ordem de exibição/distribuição é aplicada depois.
    `venda_ids` restringe e ORDENA a seleção do operador — a distribuição
    respeita essa ordem, não inventa prioridade cronológica.
    """
    if venda_ids:
        seligiveis = [int(v) for v in venda_ids]
    else:
        seligiveis = None

    sql = """SELECT v.id, v.total, v.fiado_credito_usado, v.data_venda,
                    v.fiado_pago, v.status
             FROM vendas_pdv v
             WHERE v.fiado_trabalhador_id = %s
               AND v.fiado_pago = FALSE AND v.status = 'concluida'
               AND (%s::int[] IS NULL OR v.id = ANY(%s))
             ORDER BY v.id"""
    args = (trabalhador_id, seligiveis, seligiveis)
    if travar:
        sql += " FOR UPDATE OF v"
    linhas = [dict(r) for r in conn.execute(sql, args).fetchall()]

    if not linhas:
        return []

    ids = [l["id"] for l in linhas]
    alocadas = {
        r["venda_id"]: moeda(r["alocado"])
        for r in conn.execute(
            """SELECT venda_id, SUM(valor) AS alocado FROM fiado_alocacao
               WHERE tipo = 'divida' AND venda_id = ANY(%s)
               GROUP BY venda_id""", (ids,)).fetchall()
    }

    dividas = []
    for l in linhas:
        face = moeda(l["total"]) - moeda(l["fiado_credito_usado"])
        alocado = alocadas.get(l["id"], ZERO)
        dividas.append({
            "venda_id": l["id"],
            "data_venda": l["data_venda"],
            "total": face,
            "credito_usado": moeda(l["fiado_credito_usado"]),
            "alocado": alocado,
            "alocacoes_registradas": l["id"] in alocadas,
            "saldo": max(ZERO, face - alocado),
        })

    if seligiveis:
        ordem = {vid: i for i, vid in enumerate(seligiveis)}
        dividas.sort(key=lambda d: (ordem.get(d["venda_id"], len(ordem)),
                                    d["data_venda"] or "", d["venda_id"]))
    else:
        # regra já comprovada do sistema: a compra mais antiga primeiro.
        dividas.sort(key=lambda d: (d["data_venda"] or "", d["venda_id"]))
    return dividas


def repartir(dividas, valor):
    """Distribuição determinística e exata em centavos.

    Percorre as dívidas NA ORDEM RECEBIDA e abate saldo por saldo até esgotar o
    valor; o que sobrar depois de todas as dívidas cobertas é excedente (que o
    serviço converte em crédito, vinculado ao mesmo lançamento). Retorna
    (alocacoes, excedente) com as invariantes:

      sum(alocacoes) + excedente == valor      (nenhum centavo perdido)
      0 < alocacao <= saldo da dívida          (não quita o que não existe)
    """
    restante = moeda(valor)
    alocacoes = []
    for d in dividas:
        if restante <= ZERO:
            break
        parte = min(moeda(d["saldo"]), restante)
        if parte > ZERO:
            alocacoes.append((d["venda_id"], parte))
            restante -= parte
    return alocacoes, restante


def total_devido(dividas):
    return sum((moeda(d["saldo"]) for d in dividas), ZERO)


def aplicar_recebimento(conn, *, trabalhador_id, dividas, valor, forma, conta_id,
                        sessao, descricao, atendente_id, identidade=None,
                        venda_id_evento=None):
    """Registra a entrega de dinheiro como UMA entrada e distribui o destino.

    Ordem das escritas: lançamento financeiro (o dinheiro) → alocações por
    dívida → quitação das que ficaram com saldo zero → excedente como crédito.
    Tudo na mesma transação do chamador: se qualquer passo falha, o dinheiro
    também não foi registrado.

    `dividas` deve vir de `dividas_em_aberto(travar=True)` — os saldos lidos
    sob lock são a base da distribuição, então concorrência nunca aloca duas
    vezes o mesmo centavo. `venda_id_evento` é o vínculo posto no evento da
    sessão quando o recebimento diz respeito a uma compra só. Retorna o resumo
    usado na mensagem da tela.
    """
    from servico_sessao import contabilizar_recebimento

    valor_total = moeda(valor)
    if valor_total <= ZERO:
        raise FiadoInvalido("valor_invalido", "Informe um valor maior que zero.")

    alocacoes, excedente = repartir(dividas, valor_total)
    if not alocacoes and excedente == valor_total and dividas:
        # Nada a abater: todas as dívidas escolhidas já estão cobertas por
        # alocações anteriores. Não é receita "contra nada".
        raise FiadoInvalido(
            "sem_saldo",
            "As dívidas escolhidas não têm saldo a receber — nada foi registrado.")

    saldos = {d["venda_id"]: moeda(d["saldo"]) for d in dividas}

    fin_id = contabilizar_recebimento(
        conn, tipo="recebimento_fiado", valor=valor_total,
        categoria="recebimento_fiado", descricao=descricao,
        forma=forma, conta_id=conta_id, sessao=sessao,
        identidade=identidade, atendente_id=atendente_id,
        venda_id=venda_id_evento)

    for venda_id, parte in alocacoes:
        conn.execute(
            """INSERT INTO fiado_alocacao
               (financeiro_id, trabalhador_id, venda_id, tipo, valor)
               VALUES (%s, %s, %s, 'divida', %s)""",
            (fin_id, trabalhador_id, venda_id, parte))

    quitadas = [venda_id for venda_id, parte in alocacoes
                if parte >= saldos.get(venda_id, ZERO)]
    if quitadas:
        conn.execute(
            """UPDATE vendas_pdv
               SET fiado_pago = TRUE, fiado_data_pagamento = %s
               WHERE id = ANY(%s) AND fiado_pago = FALSE""",
            (date.today().isoformat(), quitadas))

    if excedente > ZERO:
        # O crédito é do trabalhador: travar a linha dele impede que duas
        # entregas concorrentes gravem um saldo maior que o dinheiro recebido.
        trab = conn.execute(
            "SELECT fiado_credito FROM trabalhadores WHERE id = %s FOR UPDATE",
            (trabalhador_id,)).fetchone()
        if not trab:
            raise FiadoInvalido("trabalhador_nao_encontrado",
                                "Trabalhador não encontrado.")
        conn.execute(
            "UPDATE trabalhadores SET fiado_credito = fiado_credito + %s WHERE id = %s",
            (excedente, trabalhador_id))
        conn.execute(
            """INSERT INTO fiado_credito_mov
               (trabalhador_id, tipo, valor, descricao, financeiro_id)
               VALUES (%s, 'credito', %s, %s, %s)""",
            (trabalhador_id, excedente,
             "Excedente do recebimento de fiado", fin_id))
        conn.execute(
            """INSERT INTO fiado_alocacao
               (financeiro_id, trabalhador_id, venda_id, tipo, valor)
               VALUES (%s, %s, NULL, 'credito', %s)""",
            (fin_id, trabalhador_id, excedente))

    abatido = sum((p for _, p in alocacoes), ZERO)
    return {
        "financeiro_id": fin_id,
        "valor": valor_total,
        "alocado_dividas": alocacoes,
        "abatido": abatido,
        "excedente": excedente,
        "quitadas": quitadas,
    }


def extrato_fiado(conn, trabalhador_id):
    """Extrato por trabalhador: saldo devedor e crédito SEPARADOS, com links.

    Cada linha de dívida traz valor original, recebido (alocado), crédito
    consumido e saldo, mais os lançamentos que a abateram. Cada linha de
    recebimento traz o dinheiro que entrou e para onde foi. Movimento de
    crédito sem vínculo (`financeiro_id` e `venda_id` nulos) é marcado como
    legado sem alocação comprovada — continua no histórico, não é redistribuído.
    """
    trab = conn.execute(
        """SELECT id, nome_completo, cpf, fiado_limite, fiado_credito,
                  fiado_data_encerramento
           FROM trabalhadores WHERE id = %s""", (trabalhador_id,)).fetchone()
    if not trab:
        return None
    trab = dict(trab)

    dividas_abertas = dividas_em_aberto(conn, trabalhador_id)
    for d in dividas_abertas:
        d["lancamentos"] = [
            {"alocacao_id": r["id"], "financeiro_id": r["financeiro_id"],
             "valor": moeda(r["valor"]), "criado_em": r["criado_em"],
             "descricao": r["descricao"]}
            for r in conn.execute(
                """SELECT a.id, a.financeiro_id, a.valor, a.criado_em,
                          m.descricao
                   FROM fiado_alocacao a
                   JOIN financeiro_movimentacoes m ON m.id = a.financeiro_id
                   WHERE a.tipo = 'divida' AND a.venda_id = %s
                   ORDER BY a.id""", (d["venda_id"],)).fetchall()]

    recebimentos = [
        {
            "financeiro_id": r["id"],
            "valor": moeda(r["valor"]),
            "descricao": r["descricao"],
            "data": r["data_movimentacao"],
            "forma": r["forma_pagamento"],
            "dividas": [{"venda_id": a["venda_id"], "valor": moeda(a["valor"])}
                        for a in conn.execute(
                            """SELECT venda_id, valor FROM fiado_alocacao
                               WHERE financeiro_id = %s AND tipo = 'divida'
                               ORDER BY venda_id""", (r["id"],)).fetchall()],
            "credito": moeda(r["credito"]) if r["credito"] else ZERO,
        }
        for r in conn.execute(
            """SELECT m.id, m.valor, m.descricao, m.data_movimentacao,
                      m.forma_pagamento,
                      (SELECT valor FROM fiado_alocacao
                        WHERE financeiro_id = m.id AND tipo = 'credito') AS credito
               FROM financeiro_movimentacoes m
               WHERE m.categoria IN ('recebimento_fiado', 'credito_anticipado')
                 AND m.id IN (SELECT financeiro_id FROM fiado_alocacao
                              WHERE trabalhador_id = %s)
               ORDER BY m.id DESC LIMIT 50""", (trabalhador_id,)).fetchall()]

    creditos = [
        {
            "id": r["id"], "tipo": r["tipo"], "valor": moeda(r["valor"]),
            "descricao": r["descricao"], "created_at": r["created_at"],
            "venda_id": r["venda_id"], "financeiro_id": r["financeiro_id"],
        }
        for r in conn.execute(
            """SELECT id, tipo, valor, descricao, created_at, venda_id, financeiro_id
               FROM fiado_credito_mov WHERE trabalhador_id = %s
               ORDER BY id DESC LIMIT 50""", (trabalhador_id,)).fetchall()]

    legado = conn.execute(
        """SELECT COUNT(*) AS c, COALESCE(SUM(valor), 0) AS v
           FROM financeiro_movimentacoes
           WHERE categoria IN ('recebimento_fiado', 'credito_anticipado')
             AND id NOT IN (SELECT financeiro_id FROM fiado_alocacao)""").fetchone()

    return {
        "trabalhador": trab,
        "dividas": dividas_abertas,
        "saldo_devedor": total_devido(dividas_abertas),
        "credito": moeda(trab["fiado_credito"]),
        "limite": moeda(trab["fiado_limite"]),
        "recebimentos": recebimentos,
        "creditos": creditos,
        "legado_sem_prova": {"n": legado["c"], "valor": moeda(legado["v"])},
    }
