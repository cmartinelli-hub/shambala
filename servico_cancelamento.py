"""Serviço único de cancelamento de vendas (TASK_007 — FIN-02).

Cancelar aqui NÃO é trocar o status: é anular integralmente uma operação
registrada incorretamente, revertendo APENAS efeitos comprováveis, numa
única transação, com prova durável. O comando não presume devolução de
dinheiro nem de mercadoria — devolução parcial, perda e reembolso bancário
são outros fluxos (FIN-03–FIN-09) e permanecem bloqueados.

Efeitos persistidos pela finalização (a56fb57) que esta reversão conhece:
  • produtos.quantidade_estoque −q + estoque_movimentos 'saida'
    (referencia_id = venda_id) por item agregado;
  • caixa_movimentos.total_vendas += (total + doacao_valor) da sessão
    vinculada (quando há vínculo) — apenas em sessões LEGADO;
  • em sessões 'eventos' (TASK_008) a reversão é um evento de
    cancelamento (identidade `venda:{id}:cancelamento`), com efeito físico
    negativo quando a venda havia entrado na gaveta em espécie;
  • crédito antecipado consumido (fiado_credito_usado > 0) — NÃO reversível
    por aqui (bloqueado);
  • fiado não pago sai da dívida atual POR STATUS (nenhum outro toque).

Cada venda admite um único cancelamento efetivo: UNIQUE(venda_id) em
vendas_pdv_cancelamento. Repetição, clique duplo e concorrência chegam a
"Já cancelada" sem segundo efeito nem segunda auditoria.

Ordem de travas (compatível com a finalização da TASK_006):
    vendas_pdv (FOR UPDATE) → produtos (ORDER BY id, FOR UPDATE)
    → caixa_movimentos (FOR UPDATE, status aberto validado depois da trava).
A finalização trava produtos → caixa_movimentos e INSERE a venda; não há
caminho em que uma transação segure a trava que a outra espera em ordem
inversa.

Nenhum valor vem do navegador: quantidade, preço, total, doação e saldos
saem das linhas persistidas da própria venda.
"""

import json
from decimal import Decimal

# Razões de bloqueio estáveis (chave, mensagem) — a tela lista as mensagens.
# Uma venda inelegível não sofre NENHUM efeito parcial.


class CancelamentoBloqueado(Exception):
    """Venda existe mas não pode ser cancelada por este fluxo. `motivos`
    é a lista de mensagens que o operador vê ANTES de qualquer escrita."""

    def __init__(self, motivos: list):
        super().__init__(" ; ".join(motivos))
        self.motivos = motivos


class JaCancelada(Exception):
    """Já existe cancelamento (efetivo) para esta venda — sem segundo efeito.
    `com_prova` diz se há registro de reversão (novo) se a marcação é do
    legado, sem prova do que foi revertido."""

    def __init__(self, com_prova: bool):
        super().__init__("já cancelada")
        self.com_prova = com_prova


class VendaNaoEncontrada(Exception):
    pass


def _moeda(valor) -> Decimal:
    if valor is None:
        return Decimal("0.00")
    return Decimal(str(valor))


def avaliar(conn, venda_id: int) -> dict:
    """Determina elegibilidade e efeitos propostos SEM gravar nada.

    Não trava: é a leitura da tela de confirmação/consulta. A rota de escrita
    reavalia com `cancelar`, que trava na ordem canônica — o que o operador
    vê na prévia não é garantia isolada; a garantia é a reavaliação sob trava.
    """
    venda = conn.execute(
        "SELECT * FROM vendas_pdv WHERE id = %s", (venda_id,)
    ).fetchone()
    if not venda:
        raise VendaNaoEncontrada(f"venda {venda_id} não existe")

    registro = conn.execute(
        "SELECT * FROM vendas_pdv_cancelamento WHERE venda_id = %s", (venda_id,)
    ).fetchone()

    motivos: list = []
    efeitos: dict = {"estoque": [], "sessao": None, "doacao": _moeda(venda["doacao_valor"])}

    if venda["status"] == "cancelada" or registro:
        bloqueado = dict(
            venda=dict(venda), bloqueios=["Esta venda já foi cancelada."],
            efeitos={}, registro=dict(registro) if registro else None,
            ja_cancelada=True, com_prova=registro is not None,
        )
        return bloqueado

    if venda["status"] != "concluida":
        motivos.append(
            f"Venda com status “{venda['status']}” não é cancelável por este fluxo."
        )
        return {"venda": dict(venda), "bloqueios": motivos, "efeitos": efeitos,
                "registro": None, "ja_cancelada": False, "com_prova": False}

    # ── vínculo com sessão de caixa ──────────────────────────────────────────
    sessao = None
    if venda["caixa_movimento_id"] is None:
        motivos.append("A venda não está vinculada a uma sessão de caixa — "
                       "não há como provar nem reverter sua contribuição.")
    else:
        sessao = conn.execute(
            "SELECT id, caixa_id, status, versao FROM caixa_movimentos WHERE id = %s",
            (venda["caixa_movimento_id"],)
        ).fetchone()
        if not sessao:
            motivos.append("A sessão de caixa vinculada à venda não existe mais.")
        elif sessao["caixa_id"] != venda["caixa_id"]:
            motivos.append("A sessão vinculada pertence a outro caixa.")
        elif sessao["status"] != "aberto":
            motivos.append("A sessão da venda já foi fechada — fechamentos "
                           "históricos não são alterados por aqui.")
    if sessao and sessao["status"] == "aberto" and sessao["caixa_id"] == venda["caixa_id"]:
        efeitos["sessao"] = {
            "id": sessao["id"],
            "versao": sessao["versao"],
            "valor": _moeda(venda["total"]) + efeitos["doacao"],
        }

    # ── casos específicos de fiado ───────────────────────────────────────────
    if venda["forma_pagamento"] == "fiado":
        if venda["fiado_pago"]:
            motivos.append("O fiado consta como pago — estorno/recebimento é outro fluxo (FIN-06).")
        elif venda["fiado_trabalhador_id"] is None:
            motivos.append("Fiado sem trabalhador vinculado — documento inconsistente.")
        elif _moeda(venda["fiado_credito_usado"]) > 0:
            motivos.append("A venda consumiu crédito antecipado — devolvê-lo é outro fluxo.")
        elif not venda.get("criada_em"):
            motivos.append("Venda de fiado anterior a esta entrega: sem instante de "
                           "registro não é possível provar que não houve recebimento depois dela.")
        else:
            fin = conn.execute(
                """SELECT COUNT(*) AS c FROM financeiro_movimentacoes
                   WHERE tipo = 'entrada' AND descricao = %s""",
                (f"Recebimento fiado — venda #{venda_id}",)
            ).fetchone()["c"]
            if fin:
                motivos.append("Há recebimento lançado especificamente para esta venda — "
                               "cancelamento com reembolso é outro fluxo.")
            else:
                # criada_em é truncada a segundos (arredonda para CIMA); com uma
                # margem de 1 s nenhum movimento posterior à venda escapa da
                # comparação — política conservadora: sem comprovação, bloqueia.
                pos = conn.execute(
                    """SELECT COUNT(*) AS c FROM fiado_credito_mov
                       WHERE trabalhador_id = %s
                         AND created_at::timestamp
                             >= %s::timestamp - INTERVAL '1 second'""",
                    (venda["fiado_trabalhador_id"], venda["criada_em"])
                ).fetchone()["c"]
                if pos:
                    motivos.append("Há movimento de crédito do trabalhador posterior à venda "
                                   "(ex.: saldo de pagamento parcial) — a relação não é comprovável.")

    # ── reversão de estoque: só com baixa original determinável por item ─────
    itens = conn.execute(
        """SELECT produto_id, nome_produto, quantidade, preco_unitario, subtotal
           FROM vendas_pdv_itens WHERE venda_id = %s ORDER BY id""",
        (venda_id,)
    ).fetchall()
    for it in itens:
        prod = conn.execute(
            "SELECT id, nome, quantidade_estoque, ativo FROM produtos WHERE id = %s",
            (it["produto_id"],)
        ).fetchone()
        if not prod:
            motivos.append(f"Produto “{it['nome_produto']}” não existe mais — "
                           "a devolução ao estoque não é determinável.")
            continue
        saida = conn.execute(
            """SELECT COALESCE(SUM(quantidade), 0) AS q FROM estoque_movimentos
               WHERE produto_id = %s AND referencia_id = %s AND tipo = 'saida'""",
            (it["produto_id"], venda_id)
        ).fetchone()["q"]
        if saida != it["quantidade"]:
            motivos.append(
                f"A baixa original de “{it['nome_produto']}” não é comprovável "
                f"(venda registra {it['quantidade']}, movimentos de saída somam {saida}).")
            continue
        efeitos["estoque"].append({
            "produto_id": prod["id"], "nome": prod["nome"],
            "quantidade": it["quantidade"], "saldo_atual": prod["quantidade_estoque"],
            "ativo": prod["ativo"],
        })

    return {"venda": dict(venda), "bloqueios": motivos, "efeitos": efeitos,
            "registro": None, "ja_cancelada": False, "com_prova": False}


def cancelar(conn, venda_id: int, motivo: str, atendente_id: int) -> dict:
    """Executa o cancelamento integral COM reversões, na transação do caller.

    Todas as validações acontecem sob as travas, imediatamente antes da
    escrita; qualquer erro derruba a transação inteira (caller conecta com
    `with conectar()`). Bloqueios não produzem NENHUM efeito parcial.
    """
    motivo = (motivo or "").strip()
    if not motivo:
        raise CancelamentoBloqueado(["Informe o motivo do cancelamento."])

    # 1) trava da venda — serializa dois cancelamentos da mesma venda
    venda = conn.execute(
        "SELECT * FROM vendas_pdv WHERE id = %s FOR UPDATE", (venda_id,)
    ).fetchone()
    if not venda:
        raise VendaNaoEncontrada(f"venda {venda_id} não existe")

    registro = conn.execute(
        "SELECT * FROM vendas_pdv_cancelamento WHERE venda_id = %s", (venda_id,)
    ).fetchone()
    if registro or venda["status"] == "cancelada":
        raise JaCancelada(com_prova=registro is not None)

    # 2) reavaliação sob trava: 1) produtos, 2) sessão
    itens = conn.execute(
        """SELECT produto_id, nome_produto, quantidade
           FROM vendas_pdv_itens WHERE venda_id = %s ORDER BY produto_id""",
        (venda_id,)
    ).fetchall()
    produtos_presos = {}
    for it in itens:
        prod = conn.execute(
            "SELECT id, nome, quantidade_estoque, ativo FROM produtos WHERE id = %s FOR UPDATE",
            (it["produto_id"],)
        ).fetchone()
        produtos_presos[it["produto_id"]] = prod

    sessao = None
    if venda["caixa_movimento_id"] is not None:
        sessao = conn.execute(
            """SELECT id, caixa_id, status, total_vendas, versao FROM caixa_movimentos
               WHERE id = %s FOR UPDATE""",
            (venda["caixa_movimento_id"],)
        ).fetchone()

    veredito = avaliar(conn, venda_id)
    if veredito["ja_cancelada"]:
        raise JaCancelada(com_prova=veredito["com_prova"])
    if veredito["bloqueios"]:
        raise CancelamentoBloqueado(veredito["bloqueios"])

    efeitos = veredito["efeitos"]

    # 3) escrita: estoque (sobre o saldo ATUAL travado), sessão, status, prova
    estoque_revertido = []
    for efeito in efeitos["estoque"]:
        pid = efeito["produto_id"]
        prod = produtos_presos.get(pid)
        if not prod:
            raise CancelamentoBloqueado(
                [f"Produto {pid} sumiu durante a operação — nada foi gravado."])
        quantidade = next(i["quantidade"] for i in itens if i["produto_id"] == pid)
        saldo_ant = prod["quantidade_estoque"]
        saldo_novo = saldo_ant + quantidade
        conn.execute(
            "UPDATE produtos SET quantidade_estoque = %s WHERE id = %s",
            (saldo_novo, pid)
        )
        conn.execute(
            """INSERT INTO estoque_movimentos
               (produto_id, tipo, quantidade, saldo_anterior, saldo_posterior,
                motivo, referencia_id, atendente_id)
               VALUES (%s, 'entrada', %s, %s, %s, %s, %s, %s)""",
            (pid, quantidade, saldo_ant, saldo_novo,
             f"Cancelamento venda #{venda_id} — devolução ao estoque",
             venda_id, atendente_id)
        )
        estoque_revertido.append({
            "produto_id": pid, "nome": prod["nome"], "quantidade": quantidade,
            "saldo_anterior": saldo_ant, "saldo_posterior": saldo_novo,
        })

    valor_sessao = Decimal("0.00")
    sessao_eventos = False
    if efeitos["sessao"]:
        valor_sessao = efeitos["sessao"]["valor"]
        sessao_eventos = efeitos["sessao"].get("versao") == "eventos"
        if not sessao_eventos:
            conn.execute(
                """UPDATE caixa_movimentos
                   SET total_vendas = total_vendas - %s
                   WHERE id = %s AND status = 'aberto'""",
                (valor_sessao, efeitos["sessao"]["id"])
            )
        # Sessão 'eventos' (TASK_008): nada de subtração em acumulador — a
        # compensação é UM evento de cancelamento ligado à prova, com efeito
        # físico negativo quando a venda havia entrado na gaveta.

    conn.execute(
        "UPDATE vendas_pdv SET status = 'cancelada' WHERE id = %s AND status = 'concluida'",
        (venda_id,)
    )

    # 4) prova durável (UNIQUE(venda_id) — última barreira contra duplicidade)
    cur = conn.execute(
        """INSERT INTO vendas_pdv_cancelamento
           (venda_id, atendente_id, motivo, criado_em, sessao_movimento_id,
            valor_sessao_revertido, total_venda, doacao_venda, forma_pagamento,
            estoque_revertido)
           VALUES (%s, %s, %s,
                   (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp(0)::text,
                   %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (venda_id, atendente_id, motivo,
         efeitos["sessao"]["id"] if efeitos["sessao"] else None,
         valor_sessao, _moeda(venda["total"]), _moeda(venda["doacao_valor"]),
         venda["forma_pagamento"], json.dumps(estoque_revertido, ensure_ascii=False))
    )
    registro_id = cur.fetchone()["id"]

    if sessao_eventos:
        from servico_sessao import registrar_evento
        forma = venda["forma_pagamento"]
        registrar_evento(
            conn, identidade=f"venda:{venda_id}:cancelamento",
            sessao_id=efeitos["sessao"]["id"], tipo="cancelamento",
            natureza="comercial", origem_tipo="vendas_pdv_cancelamento",
            origem_id=registro_id, venda_id=venda_id, forma=forma,
            sentido="saida", valor=valor_sessao,
            efeito_fisico=-valor_sessao if forma == "especie" else Decimal("0.00"),
            atendente_id=atendente_id,
            observacao=f"Cancelamento — {motivo}")

    return {
        "registro_id": registro_id,
        "venda_id": venda_id,
        "valor_sessao_revertido": valor_sessao,
        "estoque_revertido": estoque_revertido,
        "motivo": motivo,
    }
