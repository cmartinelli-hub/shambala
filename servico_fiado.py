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


def alocado_por_venda(conn, venda_ids):
    """Quanto cada dívida já recebeu, somado das alocações gravadas (§4).

    É a única forma de ler "recebido" no módulo: as telas de consulta usam a
    mesma função que o recebimento usa para distribuir, então a conta exibida e
    a conta escrita não podem divergir. Dívida ausente do resultado não tem
    alocação registrada — e continua devendo o valor de face.
    """
    ids = [int(v) for v in venda_ids]
    if not ids:
        return {}
    return {
        r["venda_id"]: moeda(r["alocado"])
        for r in conn.execute(
            """SELECT venda_id, SUM(valor) AS alocado FROM fiado_alocacao
               WHERE tipo = 'divida' AND venda_id = ANY(%s)
               GROUP BY venda_id""", (ids,)).fetchall()
    }


def linha_de_saldo(venda, alocado, *, tem_alocacao):
    """Os quatro números de uma dívida, na mesma conta para toda tela.

    Valor original, crédito consumido na compra, recebido (alocado) e saldo —
    o saldo nunca é negativo, e `tem_alocacao` é o que diz se o recebido é
    comprovado ou se a linha vem do histórico sem alocação registrada.
    """
    original = moeda(venda["total"])
    consumido = moeda(venda.get("fiado_credito_usado"))
    recebido = moeda(alocado)
    return {
        "valor_original": original,
        "credito_usado": consumido,
        "recebido": recebido,
        "alocacoes_registradas": tem_alocacao,
        "saldo": max(ZERO, original - consumido - recebido),
    }


def fiados_em_aberto_para_consulta(conn, *, nome_like=None, limite=None):
    """Fiado em aberto para as telas de consulta (TASK_012 §5).

    Mesmo saldo do recebimento: valor original − crédito consumido − alocado,
    nunca negativo. Os totais são calculados sobre TODAS as dívidas do recorte,
    não sobre as exibidas — `limite` corta a lista, nunca o número, senão a tela
    mostraria "total" de uma página. `sem_alocacao` conta as dívidas cujo
    recebido não é comprovável (limitação do legado, exibida como tal).
    """
    filtro = " AND norm(COALESCE(t.nome_completo, '')) LIKE %s" if nome_like else ""
    params_tot = [nome_like] if nome_like else []

    base = ("""FROM vendas_pdv v
              LEFT JOIN trabalhadores t ON t.id = v.fiado_trabalhador_id
              LEFT JOIN (SELECT venda_id, SUM(valor) AS alocado
                         FROM fiado_alocacao WHERE tipo = 'divida'
                         GROUP BY venda_id) a ON a.venda_id = v.id
              WHERE v.forma_pagamento = 'fiado' AND NOT v.fiado_pago
                AND v.status = 'concluida'""" + filtro)

    tot = conn.execute(
        f"""SELECT COUNT(*) AS n,
                   COALESCE(SUM(v.total), 0) AS original,
                   COALESCE(SUM(GREATEST(v.total - v.fiado_credito_usado
                                         - COALESCE(a.alocado, 0), 0)), 0) AS saldo,
                   COALESCE(SUM(COALESCE(a.alocado, 0)), 0) AS recebido,
                   COUNT(*) FILTER (WHERE a.venda_id IS NULL) AS sem_alocacao
            {base}""", params_tot).fetchone()

    sql = ("""SELECT v.id, v.data_venda, v.total, v.fiado_credito_usado,
                     v.fiado_trabalhador_id, t.nome_completo,
                     COALESCE(a.alocado, 0) AS alocado,
                     (a.venda_id IS NOT NULL) AS tem_alocacao
             """ + base + " ORDER BY v.data_venda DESC, v.id DESC")
    params = list(params_tot)
    if limite:
        sql += " LIMIT %s"
        params.append(int(limite))

    linhas = []
    for r in conn.execute(sql, params).fetchall():
        d = dict(r)
        d.update(linha_de_saldo(d, d["alocado"], tem_alocacao=d["tem_alocacao"]))
        linhas.append(d)

    return linhas, {
        "n": tot["n"],
        "original": moeda(tot["original"]),
        "recebido": moeda(tot["recebido"]),
        "saldo": moeda(tot["saldo"]),
        "sem_alocacao": tot["sem_alocacao"],
    }


def saldo_por_trabalhador(conn):
    """Saldo devedor atual de cada trabalhador com fiado em aberto (§5).

    As telas que listam trabalhadores (relatório de fiados, ficha do
    trabalhador) exibem o MESMO número do extrato — a conta é feita aqui, uma
    vez, e não copiada página a página (era assim que divergia).
    """
    saida = {}
    for r in conn.execute(
        """SELECT v.fiado_trabalhador_id AS tid,
                  COUNT(*) AS n,
                  COALESCE(SUM(GREATEST(v.total - v.fiado_credito_usado
                                        - COALESCE(a.alocado, 0), 0)), 0) AS saldo,
                  COUNT(*) FILTER (WHERE a.venda_id IS NULL) AS sem_alocacao
           FROM vendas_pdv v
           LEFT JOIN (SELECT venda_id, SUM(valor) AS alocado
                      FROM fiado_alocacao WHERE tipo = 'divida'
                      GROUP BY venda_id) a ON a.venda_id = v.id
           WHERE v.forma_pagamento = 'fiado' AND NOT v.fiado_pago
             AND v.status = 'concluida' AND v.fiado_trabalhador_id IS NOT NULL
           GROUP BY v.fiado_trabalhador_id""").fetchall():
        saida[r["tid"]] = {"dividas": r["n"], "saldo": moeda(r["saldo"]),
                           "sem_alocacao": r["sem_alocacao"]}
    return saida


def historico_fiado(conn, trabalhador_id):
    """Histórico do fiado de um trabalhador para relatório (TASK_012 §5).

    Traz quitadas e canceladas também, cada venda com valor original, crédito
    consumido, recebido e saldo — o destino do dinheiro aparece ligado ao
    lançamento, não deduzido do texto. Cancelada não compõe os totais (regra da
    TASK_007) mas continua listada. `crédito` do trabalhador é devolvido à
    parte: crédito NÃO abate saldo devedor exibido, cada um no seu número.
    """
    vendas = [dict(r) for r in conn.execute(
        """SELECT v.id, v.total, v.fiado_credito_usado, v.data_venda,
                  v.fiado_pago, v.fiado_data_pagamento, v.status,
                  v.forma_pagamento, c.nome AS caixa_nome
           FROM vendas_pdv v
           JOIN caixas c ON c.id = v.caixa_id
           WHERE v.fiado_trabalhador_id = %s AND v.forma_pagamento = 'fiado'
           ORDER BY v.data_venda DESC, v.id DESC""", (trabalhador_id,)).fetchall()]

    alocadas = alocado_por_venda(conn, [v["id"] for v in vendas])
    gasto = recebido_em_dividas = em_aberto = ZERO
    quitadas = sem_alocacao = 0
    for v in vendas:
        v.update(linha_de_saldo(v, alocadas.get(v["id"], ZERO),
                               tem_alocacao=v["id"] in alocadas))
        if v["status"] == "cancelada":
            continue
        gasto += v["valor_original"]
        recebido_em_dividas += v["recebido"]
        if v["fiado_pago"]:
            quitadas += 1
        elif not v["alocacoes_registradas"]:
            sem_alocacao += 1
        em_aberto += v["saldo"]

    return vendas, {
        "gasto": gasto,
        "recebido": recebido_em_dividas,
        "em_aberto": em_aberto,
        "quitadas": quitadas,
        "sem_alocacao": sem_alocacao,
    }


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
    alocadas = alocado_por_venda(conn, ids)

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
