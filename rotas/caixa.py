import base64
import io
import json
import os
import re
import secrets
from collections import defaultdict
from datetime import date

import qrcode
from fastapi import APIRouter, Request, Form, Query, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from banco import conectar, _normalizar, conta_id_por_tipo
from servico_idempotencia import (
    TentativaJaRegistrada, TentativaRecusada, confirmar, exigir_reserva,
)
from rotas.auth import obter_atendente_logado, e_admin
from rotas.tentativa import resposta_de_tentativa
from templates_config import templates

router = APIRouter(prefix="/caixa")


def _conta_obrigacoes(conn):
    """Id da conta ATIVA de obrigações (`areceber`), ou None se não houver UMA.

    Venda a fiado não é dinheiro recebido: o valor fica na conta de obrigações.
    A escolha não é arbitrária — se houver mais de uma conta desse tipo, nenhuma
    é escolhida e o chamador recusa, mantendo a mesma regra da TASK_016 (nunca
    "a primeira por id").
    """
    linhas = conn.execute(
        """SELECT id FROM contas_financeiras
           WHERE tipo = 'areceber' AND ativo = 1 ORDER BY id LIMIT 2""",
        None).fetchall()
    return linhas[0]["id"] if len(linhas) == 1 else None


def _conta_pedida(forma: str, conta_id, conta_pix_id):
    """Qual conta o formulário pediu.

    `conta_id` é o campo novo (TASK_016) e vale para qualquer forma. Para PIX,
    `conta_pix_id` continua aceito porque é o nome que os formulários e as
    tentativas já usam desde a TASK_008 — trocar o nome quebraria o corpo das
    tentativas em voo e os reenvios. Nenhum dos dois é confiado: quem valida é
    `servico_sessao.conta_de_destino`.
    """
    if conta_id:
        return conta_id
    if forma == "pix" and conta_pix_id:
        return conta_pix_id
    return None


def _guard(request: Request):
    from rotas.permissoes import guarda_modulo
    return guarda_modulo(request, "caixa")


# ── WebSocket: segundo monitor (QR Code PIX) ──────────────────────────────────

class _GerenciadorQR:
    def __init__(self):
        self._conexoes: list[WebSocket] = []
        self.ultimo: dict = {}

    async def conectar(self, ws: WebSocket):
        await ws.accept()
        self._conexoes.append(ws)

    def desconectar(self, ws: WebSocket):
        if ws in self._conexoes:
            self._conexoes.remove(ws)

    async def transmitir(self, dados: dict):
        self.ultimo = dados
        mortos = []
        for ws in self._conexoes:
            try:
                await ws.send_text(json.dumps(dados))
            except Exception:
                mortos.append(ws)
        for ws in mortos:
            self.desconectar(ws)


_gerenciador_qr = _GerenciadorQR()


@router.websocket("/ws")
async def ws_pdv(ws: WebSocket):
    await _gerenciador_qr.conectar(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _gerenciador_qr.desconectar(ws)


# ── Segundo monitor ───────────────────────────────────────────────────────────

@router.get("/qrcode", response_class=HTMLResponse)
async def tela_qrcode(request: Request):
    return templates.TemplateResponse("caixa/qrcode.html", {"request": request})


# ── PDV ───────────────────────────────────────────────────────────────────────

@router.get("/pdv", response_class=HTMLResponse)
async def pdv(request: Request, caixa_id: int = 0):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        caixas = conn.execute(
            "SELECT * FROM caixas WHERE ativo=1 ORDER BY nome"
        ).fetchall()
        caixa_sel = None
        movimento = None
        precisa_abrir = False
        sem_sessao = False
        atalhos = []
        if caixa_id:
            caixa_sel = conn.execute(
                "SELECT * FROM caixas WHERE id=%s AND ativo=1", (caixa_id,)
            ).fetchone()
            if caixa_sel:
                movimento = conn.execute(
                    """SELECT * FROM caixa_movimentos
                       WHERE caixa_id=%s AND status='aberto'
                       ORDER BY id DESC LIMIT 1""",
                    (caixa_id,)
                ).fetchone()
                if not movimento:
                    # TASK_010 §3: PIX com conta financeira válida é recebimento
                    # legítimo sem sessão de caixa (o POST /pdv/finalizar já
                    # aceita isso); espécie continua condicionada à sessão.
                    # Sem conta PIX disponível, a tela segue exigindo abertura.
                    if conta_id_por_tipo(conn, "pix") is not None:
                        sem_sessao = True
                    else:
                        precisa_abrir = True
            if caixa_sel:
                atalhos = conn.execute(
                    """SELECT id, nome, preco_venda, foto_produto
                       FROM produtos WHERE atalho=1 AND ativo=1 AND caixa_id=%s ORDER BY nome""",
                    (caixa_id,)
                ).fetchall()

    # TASK_016: o PDV oferece a conta de destino por forma. `fiado` não entra:
    # venda a fiado não é dinheiro recebido (vai para a conta de obrigações).
    from servico_sessao import ROTULOS_TIPO_CONTA, contas_de_destino
    with conectar() as conn:
        contas_por_forma = {f: [dict(c) for c in contas_de_destino(conn, f)]
                            for f in ("especie", "pix")}

    return templates.TemplateResponse("caixa/pdv.html", {
        "request": request,
        "atendente": atendente,
        "caixas": [dict(c) for c in caixas],
        "caixa_sel": dict(caixa_sel) if caixa_sel else None,
        "movimento": dict(movimento) if movimento else None,
        "precisa_abrir": precisa_abrir,
        "sem_sessao": sem_sessao,
        "atalhos": [dict(a) for a in atalhos],
        "contas_por_forma": contas_por_forma,
        "rotulos_tipo_conta": ROTULOS_TIPO_CONTA,
    })


@router.post("/pdv/finalizar")
async def finalizar_venda(
    request: Request,
    caixa_id: int = Form(...),
    caixa_movimento_id: int = Form(0),
    forma_pagamento: str = Form(...),
    valor_recebido: str = Form("0"),
    itens_json: str = Form("[]"),
    trabalhador_id: int = Form(0),
    imprimir: str = Form("1"),
    doacao_valor: str = Form("0"),
    idempotencia: str = Form(""),
    conta_id: int = Form(0),
    conta_pix_id: int = Form(0),
):
    """Finaliza venda com o servidor como única autoridade de valores.

    Preço, subtotal, total, crédito usado, limite de fiado, sessão e
    comprovante saem todos do mesmo cálculo `servico_pdv.precificar`. Os
    preços do cliente servem apenas para detectar divergência de display:
    em divergência nada é gravado e a cesta volta reprificada para revisão.
    """
    atendente, redir = _guard(request)
    if redir:
        return redir

    from decimal import Decimal
    from servico_pdv import (
        CENTAVO, CestaInvalida, DivergenciaPreco, parse_cesta,
        precificar, validar_moeda,
    )

    def _url_erro(chave: str, item=None):
        base = f"/caixa/pdv?caixa_id={caixa_id}&erro={chave}"
        return RedirectResponse(
            url=base + (f"&item={item}" if item else ""), status_code=303)

    # Conteúdo da tentativa, canonizado a partir do que foi enviado — não dos
    # valores que o servidor recalcula (preço, subtotal, troco).
    corpo_tentativa = {
        "caixa_id": caixa_id, "sessao_id": caixa_movimento_id,
        "forma": forma_pagamento, "trabalhador_id": trabalhador_id,
        "recebido": str(valor_recebido or "0"), "doacao": str(doacao_valor or "0"),
        "itens": itens_json,
        # TASK_016: a conta de destino faz parte da identidade da tentativa
        "conta_id": _conta_pedida(forma_pagamento, conta_id, conta_pix_id),
    }

    try:
        itens = parse_cesta(itens_json)
    except CestaInvalida as e:
        return _url_erro(e.chave, e.item)

    with conectar() as conn:
        respondida = resposta_de_tentativa(
            conn, idempotencia=idempotencia, corpo=corpo_tentativa,
            erro_para=lambda ce: f"/caixa/pdv?caixa_id={caixa_id}&erro={ce}",
            destino_padrao=f"/caixa/pdv?caixa_id={caixa_id}",
            operacao="pdv_venda", autor_id=atendente["id"])
        if respondida:
            return respondida

        caixa = conn.execute(
            "SELECT * FROM caixas WHERE id = %s AND ativo = 1", (caixa_id,)
        ).fetchone()
        if not caixa:
            return _url_erro("caixa_indisponivel")

        # TASK_016: contas de destino — usadas aqui e na tela reprificada
        from servico_sessao import ROTULOS_TIPO_CONTA, contas_de_destino
        contas_por_forma = {f: [dict(c) for c in contas_de_destino(conn, f)]
                            for f in ("especie", "pix")}

        try:
            linhas, total = precificar(conn, itens, bool(caixa["usa_produtos"]))
        except DivergenciaPreco as e:
            # Preço vigente difere do exibido: sem venda, sem estoque, sem
            # sessão — devolve a cesta reprificada para revisão.
            caixas = conn.execute(
                "SELECT * FROM caixas WHERE ativo=1 ORDER BY nome").fetchall()
            movimento = conn.execute(
                """SELECT * FROM caixa_movimentos
                   WHERE caixa_id=%s AND status='aberto'
                   ORDER BY id DESC LIMIT 1""", (caixa_id,)).fetchone()
            atalhos = conn.execute(
                """SELECT id, nome, preco_venda, foto_produto
                   FROM produtos WHERE atalho=1 AND ativo=1 AND caixa_id=%s
                   ORDER BY nome""", (caixa_id,)).fetchall()
            cesta = [{
                "produto_id": l["produto_id"], "nome": l["nome"],
                "quantidade": l["quantidade"],
                "preco_unitario": float(l["preco_unitario"]),
                "subtotal": float(l["subtotal"]),
            } for l in e.linhas]
            return templates.TemplateResponse("caixa/pdv.html", {
                "request": request, "atendente": atendente,
                "caixas": [dict(c) for c in caixas],
                "caixa_sel": dict(caixa),
                "movimento": dict(movimento) if movimento else None,
                "precisa_abrir": False,
                "atalhos": [dict(a) for a in atalhos],
                "cesta_revisar": cesta,
                "aviso_divergencia": True,
                # a tela reprificada é a MESMA tela: também precisa do seletor
                # de conta, senão a revisão de preço vira erro 500 (TASK_016)
                "contas_por_forma": contas_por_forma,
                "rotulos_tipo_conta": ROTULOS_TIPO_CONTA,
            })
        except CestaInvalida as e:
            return _url_erro(e.chave, e.item)

        credito_usado = Decimal("0.00")
        if forma_pagamento == "fiado" and trabalhador_id:
            trab = conn.execute(
                """SELECT fiado_limite, fiado_data_encerramento, fiado_credito
                   FROM trabalhadores WHERE id = %s FOR UPDATE""",
                (trabalhador_id,)
            ).fetchone()
            if not trab:
                return _url_erro("trabalhador_nao_encontrado")
            if trab["fiado_data_encerramento"] and \
                    date.today() > date.fromisoformat(str(trab["fiado_data_encerramento"])):
                return _url_erro("fiado_encerrado")

            # Dívida em aberto para o limite: valor de face menos crédito
            # consumido na compra menos o que os recebimentos já alocaram
            # (TASK_012 §4). Leitura sem lock, como antes — o vínculo com o
            # trabalhador é que está travado acima.
            pendente = conn.execute(
                """SELECT COALESCE(SUM(GREATEST(
                           v.total - v.fiado_credito_usado - COALESCE(a.alocado, 0), 0)), 0) AS total
                   FROM vendas_pdv v
                   LEFT JOIN (SELECT venda_id, SUM(valor) AS alocado
                              FROM fiado_alocacao WHERE tipo = 'divida'
                              GROUP BY venda_id) a ON a.venda_id = v.id
                   WHERE v.fiado_trabalhador_id = %s AND v.fiado_pago = FALSE
                     AND v.status = 'concluida'""",
                (trabalhador_id,)
            ).fetchone()["total"]
            credito_usado = min(Decimal(str(trab["fiado_credito"])), total)
            novo_devido = total - credito_usado
            limite = Decimal(str(trab["fiado_limite"]))
            if limite > 0 and (Decimal(str(pendente)) + novo_devido) > limite:
                return _url_erro("fiado_excede_limite")

        try:
            recebido = validar_moeda(valor_recebido or 0, "valor_recebido")
            doacao = validar_moeda(doacao_valor or 0, "doacao")
        except CestaInvalida as e:
            return _url_erro(e.chave, e.item)

        mov_id = None
        sessao_eventos = False
        if caixa_movimento_id:
            movimento = conn.execute(
                """SELECT id, versao FROM caixa_movimentos
                   WHERE id = %s AND caixa_id = %s AND status = 'aberto'
                   FOR UPDATE""",
                (caixa_movimento_id, caixa_id)
            ).fetchone()
            if not movimento:
                return _url_erro("sessao_caixa_invalida")
            mov_id = movimento["id"]
            sessao_eventos = movimento["versao"] == "eventos"

        conta_pix_solta = None
        if forma_pagamento == "pix" and mov_id is None:
            # PIX sem sessão de caixa é recebimento válido, mas precisa de
            # conta financeira real — nunca destino indeterminado (TASK_008)
            from servico_sessao import SessaoInvalida as _SI
            from servico_sessao import conta_de_destino as _conta_destino
            try:
                conta_pix_solta = _conta_destino(
                    conn, "pix", _conta_pedida("pix", conta_id, conta_pix_id),
                    # venda é escolha explícita: com várias contas elegíveis o
                    # operador precisa dizer para onde o dinheiro vai
                    exigir_escolha=True)
            except _SI as e:
                # TASK_016: o motivo do contrato chega à tela (conta_invalida,
                # conta_inativa, conta_incompativel) em vez de virar erro 500
                return _url_erro(e.chave)

        if forma_pagamento == "especie" and mov_id is None:
            # dinheiro físico sem sessão aberta = destino indeterminável —
            # vale para qualquer versão de sessão (TASK_008)
            return _url_erro("sessao_necessaria")
        if sessao_eventos:
            # TASK_008: doação sai do TROCO (sobra de espécie entregue), nunca
            # de dinheiro "a mais" invisível: recebido ≥ total + doação, e o
            # efeito físico na gaveta é exatamente recebido − troco devolvido.
            if forma_pagamento == "especie" and recebido < total + doacao:
                return _url_erro("recebido_insuficiente")
            troco = max(Decimal("0.00"), recebido - total - doacao)
        else:
            troco = max(Decimal("0.00"), recebido - total) \
                if forma_pagamento == "especie" else Decimal("0.00")

        # ── validações concluídas: a partir daqui é transação única de escrita ──
        # A tentativa é reservada antes do primeiro efeito e confirmada no fim
        # da MESMA transação: duplo clique, reenvio com a resposta perdida e
        # duas requisições simultâneas produzem uma única venda (TASK_012 §2).
        try:
            chave_tentativa = exigir_reserva(
                conn, idempotencia=idempotencia, operacao="pdv_venda",
                autor_id=atendente["id"], corpo=corpo_tentativa)
        except TentativaRecusada as e:
            return _url_erro(e.chave_erro)
        except TentativaJaRegistrada as e:
            return RedirectResponse(
                url=e.destino or f"/caixa/pdv?caixa_id={caixa_id}", status_code=303)

        # TASK_016: a conta de destino da venda é resolvida ANTES de gravar a
        # venda e fica PERSISTIDA na linha (`vendas_pdv.conta_id`). Assim o
        # fechamento de sessão legado tem de onde ler o destino por vínculo
        # comprovado, em vez de adivinhar por tipo.
        from servico_sessao import SessaoInvalida as _SI
        from servico_sessao import conta_de_destino as _conta_destino
        conta_venda = None
        if forma_pagamento in ("especie", "pix"):
            try:
                conta_venda = _conta_destino(
                    conn, forma_pagamento,
                    _conta_pedida(forma_pagamento, conta_id, conta_pix_id),
                    # TASK_016: a venda sempre diz onde o dinheiro entra. Com
                    # UMA conta elegível a escolha é implícita; com várias, o
                    # operador precisa escolher — o padrão "primeira do tipo"
                    # nunca se aplica a uma venda.
                    exigir_escolha=True)
            except _SI as e:
                # sem conta de destino válida a venda NÃO acontece, e o motivo
                # (ambiguidade, inativa, incompatível) volta na tela
                return _url_erro(e.chave)

        cur = conn.execute(
            """INSERT INTO vendas_pdv
               (caixa_id, data_venda, total, forma_pagamento, troco, status, atendente_id,
                fiado_trabalhador_id, fiado_credito_usado, caixa_movimento_id, doacao_valor,
                conta_id, criada_em)
               VALUES (%s, %s, %s, %s, %s, 'concluida', %s, %s, %s, %s, %s, %s,
                       (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp(0)::text)
               RETURNING id""",
            (caixa_id, date.today().isoformat(), total,
             forma_pagamento, troco, atendente["id"],
             trabalhador_id if trabalhador_id else None,
             credito_usado, mov_id, doacao, conta_venda)
        )
        venda_id = cur.fetchone()["id"]

        if conta_pix_solta is not None:
            # PIX sem sessão: o lançamento nasce aqui (não há fechamento de
            # gaveta que o crie depois) — UMA entrada, na conta PIX válida
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, descricao, caixa_id, forma_pagamento,
                    conta_id, status)
                   VALUES ('entrada', 'venda_pdv', %s, %s, %s, 'pix', %s, 'pago')""",
                (total, f"Venda PDV #{venda_id} — PIX sem sessão de caixa",
                 caixa_id, conta_pix_solta)
            )

        for linha in linhas:
            if linha["produto_id"] is None:
                continue  # caixa de preço aberto: sem catálogo, sem item (modelo atual)
            conn.execute(
                """INSERT INTO vendas_pdv_itens
                   (venda_id, produto_id, nome_produto, quantidade, preco_unitario, subtotal)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (venda_id, linha["produto_id"], linha["nome"],
                 linha["quantidade"], linha["preco_unitario"], linha["subtotal"])
            )

        if credito_usado > 0:
            conn.execute(
                "UPDATE trabalhadores SET fiado_credito = fiado_credito - %s WHERE id = %s",
                (credito_usado, trabalhador_id)
            )
            # Vínculo do consumo com a venda que o gerou (TASK_012 §4): sem
            # isto, o extrato só teria o texto "Compra #id" para deduzir.
            conn.execute(
                """INSERT INTO fiado_credito_mov
                   (trabalhador_id, tipo, valor, descricao, venda_id)
                   VALUES (%s, 'debito', %s, %s, %s)""",
                (trabalhador_id, credito_usado, f"Compra #{venda_id}", venda_id)
            )

        if mov_id and not sessao_eventos:
            conn.execute(
                "UPDATE caixa_movimentos SET total_vendas = total_vendas + %s WHERE id = %s",
                (total + doacao, mov_id)
            )
        elif mov_id and sessao_eventos:
            # TASK_008: sessão 'eventos' — fonte única de verdade é o evento
            # detalhado; os acumuladores antigos ficam intactos em 0.
            from servico_sessao import registrar_evento
            efeito = total if forma_pagamento == "especie" else Decimal("0.00")
            # TASK_016: o destino da venda vem do contrato de conta×forma, e a
            # escolha da tela é respeitada. Antes, `conta_id_por_tipo` pegava a
            # PRIMEIRA conta do tipo — com duas contas de espécie, o dinheiro da
            # venda ia sempre para a de menor id.
            # `conta_venda` já foi resolvida antes do INSERT da venda e está
            # persistida na linha — o evento usa a MESMA decisão.
            registrar_evento(
                conn, identidade=f"venda:{venda_id}:venda", sessao_id=mov_id,
                tipo="venda", natureza="comercial", origem_tipo="vendas_pdv",
                origem_id=venda_id, venda_id=venda_id, forma=forma_pagamento,
                sentido="entrada", valor=total, efeito_fisico=efeito,
                conta_id=conta_venda, atendente_id=atendente["id"])
            if doacao > 0:
                registrar_evento(
                    conn, identidade=f"venda:{venda_id}:doacao", sessao_id=mov_id,
                    tipo="venda_doacao", natureza="recebimento",
                    origem_tipo="vendas_pdv", origem_id=venda_id,
                    venda_id=venda_id, forma=forma_pagamento, sentido="entrada",
                    valor=doacao,
                    efeito_fisico=doacao if forma_pagamento == "especie" else Decimal("0.00"),
                    conta_id=conta_venda, atendente_id=atendente["id"])
            if credito_usado > 0:
                registrar_evento(
                    conn, identidade=f"venda:{venda_id}:credito", sessao_id=mov_id,
                    tipo="venda_credito", natureza="recebimento",
                    origem_tipo="vendas_pdv", origem_id=venda_id,
                    venda_id=venda_id, forma="credito", sentido="entrada",
                    valor=credito_usado, efeito_fisico=Decimal("0.00"),
                    atendente_id=atendente["id"],
                    observacao="Consumo de crédito antecipado — não é dinheiro novo")

        for linha in linhas:
            if linha["produto_id"] is None:
                continue
            # Produto está travado (FOR UPDATE) desde a precificação: o saldo
            # abaixo é o valor absoluto consistente, preservando estoque
            # negativo quando a política atual já o permite.
            novo_saldo = linha["estoque"] - linha["quantidade"]
            conn.execute(
                "UPDATE produtos SET quantidade_estoque = %s WHERE id = %s",
                (novo_saldo, linha["produto_id"])
            )
            conn.execute(
                """INSERT INTO estoque_movimentos
                   (produto_id, tipo, quantidade, saldo_anterior, saldo_posterior,
                    referencia_id, atendente_id)
                   VALUES (%s, 'saida', %s, %s, %s, %s, %s)""",
                (linha["produto_id"], linha["quantidade"], linha["estoque"],
                 novo_saldo, venda_id, atendente["id"])
            )

        if imprimir != "0":
            fiado_param = "&fiado=1" if forma_pagamento == "fiado" else ""
            destino = f"/caixa/pdv?caixa_id={caixa_id}&ok={venda_id}{fiado_param}"
        else:
            destino = f"/caixa/pdv?caixa_id={caixa_id}"
        confirmar(conn, chave=chave_tentativa, documento=venda_id, destino=destino)

    # Sinaliza ao segundo monitor que a venda foi concluída
    import asyncio
    asyncio.create_task(_gerenciador_qr.transmitir({"tipo": "confirmado"}))

    return RedirectResponse(url=destino, status_code=303)


# ── API: gerar QR Code PIX ────────────────────────────────────────────────────

@router.get("/pix-qr", response_class=JSONResponse)
async def gerar_pix_qr(request: Request, valor: str = "0", caixa_id: int = 0):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse({}, status_code=401)

    valor_float = float(valor or 0)

    with conectar() as conn:
        if caixa_id:
            row = conn.execute(
                """SELECT p.chave, p.tipo FROM caixas c
                   JOIN chaves_pix p ON p.id = c.chave_pix_id
                   WHERE c.id = %s AND p.ativa = TRUE""",
                (caixa_id,)
            ).fetchone()
        else:
            row = None
        if not row:
            row = conn.execute(
                "SELECT chave, tipo FROM chaves_pix WHERE ativa = TRUE LIMIT 1"
            ).fetchone()
        if not row:
            return JSONResponse({"erro": "Nenhuma chave PIX ativa configurada"}, status_code=400)
        chave = _normalizar_chave_pix(row["chave"], row["tipo"])

    pix_code = _gerar_payload_pix(chave, valor_float, "Shambala")

    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(pix_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    dados = {"tipo": "pix", "valor": valor_float, "qr_base64": qr_b64, "pix_code": pix_code}

    import asyncio
    asyncio.create_task(_gerenciador_qr.transmitir(dados))

    return JSONResponse({"qr_base64": qr_b64, "pix_code": pix_code})


# ── Histórico de vendas ───────────────────────────────────────────────────────

@router.get("/vendas", response_class=HTMLResponse)
async def listar_vendas(request: Request, caixa_id: int = 0, data: str = "",
                        cancelado: int = 0, erro: str = ""):
    atendente, redir = _guard(request)
    if redir:
        return redir

    data_ref = data or date.today().isoformat()

    with conectar() as conn:
        caixas = conn.execute("SELECT * FROM caixas ORDER BY nome").fetchall()

        filtros = ["v.data_venda = %s"]
        params: list = [data_ref]
        if caixa_id:
            filtros.append("v.caixa_id = %s")
            params.append(caixa_id)

        vendas = conn.execute(
            f"""SELECT v.*, c.nome AS caixa_nome, a.nome_completo AS atendente_nome,
                       x.motivo AS cancelamento_motivo,
                       x.criado_em AS cancelamento_em,
                       xa.nome_completo AS cancelante_nome
                FROM vendas_pdv v
                JOIN caixas c ON c.id = v.caixa_id
                LEFT JOIN atendentes a ON a.id = v.atendente_id
                LEFT JOIN vendas_pdv_cancelamento x ON x.venda_id = v.id
                LEFT JOIN atendentes xa ON xa.id = x.atendente_id
                WHERE {' AND '.join(filtros)}
                ORDER BY v.id DESC""",
            params
        ).fetchall()

        total_dia = sum(float(v["total"]) for v in vendas if v["status"] == "concluida")

        pgto_params: list = [data_ref]
        pgto_filtros = ["v.data_venda = %s", "v.status = 'concluida'"]
        if caixa_id:
            pgto_filtros.append("v.caixa_id = %s")
            pgto_params.append(caixa_id)
        por_pgto = conn.execute(
            f"""SELECT forma_pagamento, COUNT(*) AS qtd, COALESCE(SUM(total + COALESCE(doacao_valor, 0)),0) AS total
                FROM vendas_pdv v
                WHERE {' AND '.join(pgto_filtros)}
                GROUP BY forma_pagamento ORDER BY total DESC""",
            pgto_params
        ).fetchall()

    return templates.TemplateResponse("caixa/vendas.html", {
        "request": request,
        "atendente": atendente,
        "caixas": [dict(c) for c in caixas],
        "vendas": [dict(v) for v in vendas],
        "caixa_id": caixa_id,
        "data_ref": data_ref,
        "total_dia": total_dia,
        "por_pgto": [dict(p) for p in por_pgto],
        "cancelado": cancelado,
        "erro": erro,
        "e_admin": e_admin(request),
    })


@router.post("/vendas/{id}/cancelar")
async def cancelar_venda(request: Request, id: int,
                         motivo: str = Form(""), confirmar: str = Form("")):
    """Cancelamento integral rastreável (TASK_007): serviço único, motivo
    obrigatório, confirmação explícita e reversões apenas comprováveis."""
    atendente, redir = _guard(request)
    if redir:
        return redir
    if not e_admin(request):
        return RedirectResponse(url="/caixa/vendas?erro=sem_permissao", status_code=303)

    avisos: list = []
    from servico_cancelamento import JaCancelada, CancelamentoBloqueado, VendaNaoEncontrada, cancelar
    try:
        with conectar() as conn:
            if not confirmar or confirmar != "1":
                raise CancelamentoBloqueado(["Confirme explicitamente o cancelamento integral."])
            resultado = cancelar(conn, id, motivo, atendente["id"])
            caixa_id = conn.execute("SELECT caixa_id FROM vendas_pdv WHERE id=%s",
                                    (id,)).fetchone()["caixa_id"]
        return RedirectResponse(
            url=f"/caixa/vendas?caixa_id={caixa_id}&cancelado={resultado['venda_id']}",
            status_code=303)
    except CancelamentoBloqueado as e:
        avisos = e.motivos
    except JaCancelada as e:
        avisos = (["Esta venda já foi cancelada com reversão registrada."]
                  if e.com_prova else
                  ["Esta venda já consta como cancelada no histórico legado — "
                   "não existe prova de reversão associada. Nenhum efeito novo foi produzido."])
    except VendaNaoEncontrada:
        return HTMLResponse("<p>Venda não encontrada.</p>", status_code=404)
    return await _pagina_cancelar(request, atendente, id, avisos=avisos,
                                  motivo_digitado=motivo)


async def _pagina_cancelar(request: Request, atendente, id: int,
                           avisos: list | None = None, motivo_digitado: str = ""):
    """Tela de confirmação: venda, data, forma, total e efeitos propostos —
    ou os motivos que bloqueiam o cancelamento. Nada é gravado aqui."""
    from servico_cancelamento import avaliar, VendaNaoEncontrada
    with conectar() as conn:
        try:
            aval = avaliar(conn, id)
        except VendaNaoEncontrada:
            return HTMLResponse("<p>Venda não encontrada.</p>", status_code=404)
        venda = aval["venda"]
        caixa = conn.execute("SELECT nome FROM caixas WHERE id=%s",
                             (venda["caixa_id"],)).fetchone()
        itens = conn.execute(
            """SELECT nome_produto, quantidade, preco_unitario, subtotal
               FROM vendas_pdv_itens WHERE venda_id = %s ORDER BY id""", (id,)).fetchall()
    return templates.TemplateResponse("caixa/cancelar_venda.html", {
        "request": request,
        "atendente": atendente,
        "venda": venda,
        "caixa_nome": caixa["nome"] if caixa else f"#{venda['caixa_id']}",
        "itens": [dict(i) for i in itens],
        "efeitos": aval["efeitos"],
        "bloqueios": (aval["bloqueios"] if not avisos else avisos),
        "ja_cancelada": aval["ja_cancelada"],
        "motivo_digitado": motivo_digitado,
    })


@router.get("/vendas/{id}/cancelar", response_class=HTMLResponse)
async def cancelar_venda_pagina(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    if not e_admin(request):
        return RedirectResponse(url="/caixa/vendas?erro=sem_permissao", status_code=303)
    return await _pagina_cancelar(request, atendente, id)


@router.get("/vendas/{id}/escpos")
async def escpos_venda(request: Request, id: int, via: str = ""):
    """Retorna os bytes ESC/POS da venda para impressão pelo agente local do caixa."""
    from fastapi.responses import Response
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse({}, status_code=401)

    with conectar() as conn:
        venda = conn.execute(
            """SELECT v.*, c.nome AS caixa_nome, a.nome_completo AS atendente_nome
               FROM vendas_pdv v
               JOIN caixas c ON c.id = v.caixa_id
               LEFT JOIN atendentes a ON a.id = v.atendente_id
               WHERE v.id = %s""",
            (id,)
        ).fetchone()
        if not venda:
            return JSONResponse({"erro": "Venda não encontrada"}, status_code=404)

        cancelamento = _consulta_cancelamento(conn, id)

        itens = conn.execute(
            "SELECT nome_produto, quantidade, preco_unitario, subtotal "
            "FROM vendas_pdv_itens WHERE venda_id = %s ORDER BY id",
            (id,)
        ).fetchall()

        centro = conn.execute("SELECT chave, valor FROM configuracoes_centro").fetchall()
        centro_nome = {r["chave"]: r["valor"] for r in centro}.get("centro_nome", "Centro Espírita")

        trabalhador = None
        if venda.get("fiado_trabalhador_id"):
            trab_row = conn.execute(
                "SELECT nome_completo, cpf FROM trabalhadores WHERE id = %s",
                (venda["fiado_trabalhador_id"],)
            ).fetchone()
            if trab_row:
                trabalhador = dict(trab_row)

    dados = _gerar_bytes_escpos(dict(venda), [dict(i) for i in itens], centro_nome, trabalhador,
                                via == "caixa", cancelamento=cancelamento)
    return Response(content=dados, media_type="application/octet-stream")


def _consulta_cancelamento(conn, venda_id: int):
    """Prova do cancelamento efetivo (TASK_007), para comprovante/consulta.
    Retorna None quando não há registro — inclusive para cancelamentos do
    legado, que seguem sem prova de reversão (não se inventa histórico)."""
    row = conn.execute(
        """SELECT x.motivo, x.criado_em, x.valor_sessao_revertido,
                  x.estoque_revertido, a.nome_completo AS autor_nome
           FROM vendas_pdv_cancelamento x
           LEFT JOIN atendentes a ON a.id = x.atendente_id
           WHERE x.venda_id = %s""",
        (venda_id,)
    ).fetchone()
    return dict(row) if row else None


@router.get("/vendas/{id}/comprovante", response_class=HTMLResponse)
async def comprovante_venda(request: Request, id: int, via: str = ""):
    with conectar() as conn:
        venda = conn.execute(
            """SELECT v.*, c.nome AS caixa_nome, a.nome_completo AS atendente_nome
               FROM vendas_pdv v
               JOIN caixas c ON c.id = v.caixa_id
               LEFT JOIN atendentes a ON a.id = v.atendente_id
               WHERE v.id = %s""",
            (id,)
        ).fetchone()

        if not venda:
            return HTMLResponse("<p>Venda nao encontrada.</p>", status_code=404)

        itens = conn.execute(
            """SELECT nome_produto, quantidade, preco_unitario, subtotal
               FROM vendas_pdv_itens
               WHERE venda_id = %s
               ORDER BY id""",
            (id,)
        ).fetchall()

        centro = conn.execute(
            "SELECT chave, valor FROM configuracoes_centro"
        ).fetchall()
        cfg = {}
        for r in centro:
            cfg[r["chave"]] = r["valor"]
        centro_nome = cfg.get("centro_nome", "Centro Espirita")

        # Dados do trabalhador se for fiado
        trabalhador = None
        if venda.get("fiado_trabalhador_id"):
            trab_row = conn.execute(
                "SELECT nome_completo, cpf FROM trabalhadores WHERE id = %s",
                (venda["fiado_trabalhador_id"],)
            ).fetchone()
            if trab_row:
                trabalhador = dict(trab_row)

        cancelamento = _consulta_cancelamento(conn, id)

    recibo = _gerar_recibo(venda, itens, centro_nome, trabalhador, via == "caixa",
                           cancelamento=cancelamento)

    return templates.TemplateResponse("caixa/comprovante.html", {
        "request": request,
        "venda": dict(venda),
        "centro_nome": centro_nome,
        "itens": [dict(i) for i in itens],
        "recibo": recibo,
        "trabalhador": trabalhador,
        "via_caixa": via == "caixa",
        "cancelamento": cancelamento,
    })


# ── Produtos ──────────────────────────────────────────────────────────────────

@router.get("/produtos/buscar", response_class=JSONResponse)
async def buscar_produto(request: Request, q: str = Query("")):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse([], status_code=401)
    if not q.strip():
        return JSONResponse([])

    with conectar() as conn:
        por_barras = conn.execute(
            "SELECT * FROM produtos WHERE codigo_barras = %s AND ativo = 1",
            (q.strip(),)
        ).fetchone()
        if por_barras:
            p = dict(por_barras)
            return JSONResponse([_fmt_produto(p)])

        termo = f"%{q.strip().lower()}%"
        rows = conn.execute(
            """SELECT * FROM produtos
               WHERE lower(nome) LIKE %s AND ativo = 1
               ORDER BY nome LIMIT 10""",
            (termo,)
        ).fetchall()

    return JSONResponse([_fmt_produto(dict(p)) for p in rows])


@router.get("/produtos", response_class=HTMLResponse)
async def listar_produtos(request: Request, q: str = ""):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        if q:
            termo = f"%{q.lower()}%"
            produtos = conn.execute(
                """SELECT * FROM produtos
                   WHERE (lower(nome) LIKE %s OR codigo_barras LIKE %s)
                   ORDER BY categoria, nome""",
                (termo, termo)
            ).fetchall()
        else:
            produtos = conn.execute(
                "SELECT * FROM produtos ORDER BY categoria, nome"
            ).fetchall()

    return templates.TemplateResponse("caixa/produtos.html", {
        "request": request,
        "atendente": atendente,
        "produtos": [dict(p) for p in produtos],
        "q": q,
        "categorias": _CATEGORIAS,
    })


@router.get("/produtos/novo", response_class=HTMLResponse)
async def form_novo_produto(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("caixa/produto_form.html", {
        "request": request,
        "atendente": atendente,
        "produto": None,
        "categorias": _CATEGORIAS,
    })


@router.post("/produtos/novo")
async def salvar_novo_produto(
    request: Request,
    nome: str = Form(...),
    categoria: str = Form("outro"),
    preco_custo: str = Form("0"),
    preco_venda: str = Form("0"),
    codigo_barras: str = Form(""),
    ativo: str = Form("1"),
    atalho: str = Form("0"),
    foto: UploadFile = File(None),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO produtos (nome, categoria, preco_custo, preco_venda, codigo_barras, ativo, atalho)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (nome.strip(), categoria, float(preco_custo or 0),
             float(preco_venda or 0), codigo_barras.strip() or None,
             1 if ativo == "1" else 0, 1 if atalho == "1" else 0)
        )
        prod_id = cur.fetchone()["id"]

        # Salva foto depois de inserir (precisa do id)
        if foto and foto.filename:
            ext = os.path.splitext(foto.filename)[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                suffix = secrets.token_hex(4)
                nome_arquivo = f"prod_{prod_id}_{suffix}{ext}"
                caminho = os.path.join("static", "fotos", nome_arquivo)
                content = await foto.read()
                with open(caminho, "wb") as f:
                    f.write(content)
                conn.execute(
                    "UPDATE produtos SET foto_produto = %s WHERE id = %s",
                    (f"/static/fotos/{nome_arquivo}", prod_id)
                )

    return RedirectResponse(url="/caixa/produtos", status_code=303)


@router.get("/produtos/tabela-precos", response_class=HTMLResponse)
async def tabela_precos(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        produtos = conn.execute(
            """SELECT nome, preco_venda FROM produtos
               WHERE ativo = 1 ORDER BY nome""",
        ).fetchall()
        centro = conn.execute(
            "SELECT chave, valor FROM configuracoes_centro"
        ).fetchall()
        centro_nome = {r["chave"]: r["valor"] for r in centro}.get("centro_nome", "Centro Espírita")

    return templates.TemplateResponse("caixa/tabela_precos.html", {
        "request": request,
        "produtos": [dict(p) for p in produtos],
        "centro_nome": centro_nome,
    })


def _pagina_doacao(request, atendente, erro=None, forma_pre="especie"):
    from servico_sessao import (
        MENSAGENS_ERRO, ROTULOS_TIPO_CONTA, contas_de_destino, sessoes_abertas,
    )
    with conectar() as conn:
        todas = [dict(s) for s in sessoes_abertas(conn)]
        # recebimento novo só entra em sessão 'eventos'; legado é listado
        # apenas como aviso — escolher uma sessão antiga é recusado no POST
        sessoes = [s for s in todas if s["versao"] == "eventos"]
        sessoes_legadas = [s for s in todas if s["versao"] != "eventos"]
        # TASK_016: o destino é escolhido pelo contrato conta×forma. Espécie e
        # PIX têm listas próprias; com uma só conta ela já vem como padrão.
        contas_especie = [dict(c) for c in contas_de_destino(conn, "especie")]
        contas_pix = [dict(c) for c in contas_de_destino(conn, "pix")]
    return templates.TemplateResponse("caixa/doacao.html", {
        "request": request,
        "atendente": atendente,
        "sessoes": sessoes,
        "sessoes_legadas": sessoes_legadas,
        "contas_especie": contas_especie,
        "contas_pix": contas_pix,
        "rotulos_tipo_conta": ROTULOS_TIPO_CONTA,
        "erro": MENSAGENS_ERRO.get(erro, erro) if erro else None,
        "forma_pre": forma_pre,
    })


@router.get("/doacao", response_class=HTMLResponse)
async def form_doacao(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return _pagina_doacao(request, atendente,
                          erro=request.query_params.get("erro"),
                          forma_pre=request.query_params.get("forma", "especie"))


@router.post("/doacao", response_class=HTMLResponse)
async def registrar_doacao(
    request: Request,
    forma: str = Form("especie"),
    valor: str = Form(""),
    descricao: str = Form(""),
    sessao_id: int = Form(0),
    conta_pix_id: int = Form(0),
    conta_id: int = Form(0),
    idempotencia: str = Form(""),
):
    """Doação avulsa sem cesta (TASK_008): o destino é declarado, não presumido.

    Espécie precisa de uma sessão aberta NOVA ('eventos') explicitamente
    escolhida — o dinheiro físico existe dentro de uma gaveta. PIX precisa de
    uma conta PIX válida e NÃO entra em gaveta alguma. Sessão do fluxo antigo
    ('legado') recusa o recebimento antes de qualquer escrita: nada alimenta
    os acumuladores legados por escolha explícita. A escolha implícita do
    caixa 2 foi removida.
    """
    atendente, redir = _guard(request)
    if redir:
        return redir

    from decimal import Decimal
    from servico_sessao import (
        SessaoInvalida, moeda, registrar_evento, sessao_para_recebimento,
        validar_conta_para_forma,
    )

    if forma not in ("especie", "pix"):
        forma = "especie"
    try:
        valor_dec = moeda(valor, "valor")
    except SessaoInvalida as e:
        return RedirectResponse(url=f"/caixa/doacao?erro={e.chave}&forma={forma}",
                                status_code=303)

    desc = descricao.strip() or f"Doação avulsa via {'PIX' if forma == 'pix' else 'Dinheiro'}"
    corpo_tentativa = {"forma": forma, "valor": str(valor_dec), "descricao": desc,
                       "sessao_id": sessao_id, "conta_pix_id": conta_pix_id or None,
                       # TASK_016: trocar a conta na mesma chave é CONFLITO
                       "conta_id": _conta_pedida(forma, conta_id, conta_pix_id)}
    destino_doacao = f"/caixa/doacao?ok=1&valor={valor_dec:.2f}&forma={forma}"

    def _erro_doacao(chave_erro: str) -> str:
        return f"/caixa/doacao?erro={chave_erro}&forma={forma}"

    try:
        with conectar() as conn:
            respondida = resposta_de_tentativa(
                conn, idempotencia=idempotencia, corpo=corpo_tentativa,
                erro_para=_erro_doacao, destino_padrao=destino_doacao,
                operacao="doacao_avulsa", autor_id=atendente["id"])
            if respondida:
                return respondida

            mov = sessao_para_recebimento(conn, sessao_id, forma)
            conta = validar_conta_para_forma(
                conn, forma, _conta_pedida(forma, conta_id, conta_pix_id))

            try:
                chave_tentativa = exigir_reserva(
                    conn, idempotencia=idempotencia, operacao="doacao_avulsa",
                    autor_id=atendente["id"], corpo=corpo_tentativa)
            except TentativaRecusada as e:
                return RedirectResponse(url=_erro_doacao(e.chave_erro), status_code=303)
            except TentativaJaRegistrada as e:
                return RedirectResponse(url=e.destino or destino_doacao, status_code=303)

            cur = conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, descricao, caixa_id, forma_pagamento, conta_id, status)
                   VALUES ('entrada', 'doacao', %s, %s, %s, %s, %s, 'pago')
                   RETURNING id""",
                (valor_dec, desc, mov["caixa_id"] if mov else None, forma, conta))
            fin_id = cur.fetchone()["id"]

            if mov:
                registrar_evento(
                    conn, identidade=f"doacao:{fin_id}", sessao_id=mov["id"],
                    tipo="doacao", natureza="recebimento",
                    origem_tipo="financeiro_movimentacoes", origem_id=fin_id,
                    financeiro_id=fin_id, forma=forma, sentido="entrada",
                    valor=valor_dec,
                    efeito_fisico=valor_dec if forma == "especie" else Decimal("0.00"),
                    conta_id=conta, atendente_id=atendente["id"],
                    observacao=desc)
            confirmar(conn, chave=chave_tentativa, documento=fin_id,
                      destino=destino_doacao)
    except SessaoInvalida as e:
        # A exceção vazou do `with conectar()` → rollback integral: nada desta
        # doação permanece gravado.
        return RedirectResponse(url=f"/caixa/doacao?erro={e.chave}&forma={forma}",
                                status_code=303)

    return RedirectResponse(url=destino_doacao, status_code=303)



@router.get("/produtos/{id}/editar", response_class=HTMLResponse)
async def form_editar_produto(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        produto = conn.execute("SELECT * FROM produtos WHERE id=%s", (id,)).fetchone()
    if not produto:
        return RedirectResponse(url="/caixa/produtos", status_code=303)

    return templates.TemplateResponse("caixa/produto_form.html", {
        "request": request,
        "atendente": atendente,
        "produto": dict(produto),
        "categorias": _CATEGORIAS,
    })


@router.post("/produtos/{id}/editar")
async def salvar_produto(
    request: Request,
    id: int,
    nome: str = Form(...),
    categoria: str = Form("outro"),
    preco_custo: str = Form("0"),
    preco_venda: str = Form("0"),
    codigo_barras: str = Form(""),
    ativo: str = Form("1"),
    atalho: str = Form("0"),
    foto: UploadFile = File(None),
    remover_foto: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        # Remover foto se solicitado
        if remover_foto == "1":
            old = conn.execute(
                "SELECT foto_produto FROM produtos WHERE id=%s", (id,)
            ).fetchone()
            if old and old["foto_produto"]:
                caminho_old = old["foto_produto"].lstrip("/")
                if os.path.exists(caminho_old):
                    os.remove(caminho_old)
            conn.execute(
                "UPDATE produtos SET foto_produto = NULL WHERE id = %s", (id,)
            )

        # Upload de nova foto
        if foto and foto.filename:
            ext = os.path.splitext(foto.filename)[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                suffix = secrets.token_hex(4)
                nome_arquivo = f"prod_{id}_{suffix}{ext}"
                caminho = os.path.join("static", "fotos", nome_arquivo)
                content = await foto.read()
                with open(caminho, "wb") as f:
                    f.write(content)
                conn.execute(
                    "UPDATE produtos SET foto_produto = %s WHERE id = %s",
                    (f"/static/fotos/{nome_arquivo}", id)
                )

        conn.execute(
            """UPDATE produtos
               SET nome=%s, categoria=%s, preco_custo=%s, preco_venda=%s,
                   codigo_barras=%s, ativo=%s, atalho=%s
               WHERE id=%s""",
            (nome.strip(), categoria, float(preco_custo or 0),
             float(preco_venda or 0), codigo_barras.strip() or None,
             1 if ativo == "1" else 0, 1 if atalho == "1" else 0, id)
        )
    return RedirectResponse(url="/caixa/produtos", status_code=303)


@router.post("/produtos/{id}/toggle-ativo")
async def toggle_ativo_produto(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            "UPDATE produtos SET ativo = CASE WHEN ativo=1 THEN 0 ELSE 1 END WHERE id=%s",
            (id,)
        )
    return RedirectResponse(url="/caixa/produtos", status_code=303)


# ── Atalhos (JSON) ──────────────────────────────────────────────────────────────

@router.get("/produtos/atalhos", response_class=JSONResponse)
async def listar_atalhos(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse([], status_code=401)

    with conectar() as conn:
        rows = conn.execute(
            """SELECT id, nome, preco_venda, foto_produto
               FROM produtos WHERE atalho=1 AND ativo=1 ORDER BY nome"""
        ).fetchall()

    return JSONResponse([
        {
            "id": r["id"],
            "nome": r["nome"],
            "preco_venda": float(r["preco_venda"]),
            "foto_produto": r["foto_produto"] or "",
        }
        for r in rows
    ])


# ── Estoque ─────────────────────────────────────────────────────────────────────

@router.get("/estoque", response_class=HTMLResponse)
async def pagina_estoque(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        produtos = conn.execute(
            """SELECT * FROM produtos ORDER BY quantidade_estoque ASC, nome"""
        ).fetchall()

    return templates.TemplateResponse("caixa/estoque.html", {
        "request": request,
        "atendente": atendente,
        "produtos": [dict(p) for p in produtos],
    })


@router.get("/estoque/entrada", response_class=HTMLResponse)
async def form_estoque_entrada(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        produtos = conn.execute(
            "SELECT id, nome, quantidade_estoque FROM produtos ORDER BY nome"
        ).fetchall()

    return templates.TemplateResponse("caixa/estoque_entrada.html", {
        "request": request,
        "atendente": atendente,
        "produtos": [dict(p) for p in produtos],
    })


@router.post("/estoque/entrada")
async def registrar_entrada(
    request: Request,
    produto_id: int = Form(...),
    quantidade: int = Form(...),
    motivo: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    if quantidade <= 0:
        return RedirectResponse(url="/caixa/estoque/entrada?erro=qtde_invalida", status_code=303)

    with conectar() as conn:
        # Saldo anterior
        ant = conn.execute(
            "SELECT quantidade_estoque FROM produtos WHERE id = %s", (produto_id,)
        ).fetchone()
        saldo_ant = int(ant["quantidade_estoque"]) if ant else 0
        saldo_novo = saldo_ant + quantidade

        conn.execute(
            "UPDATE produtos SET quantidade_estoque = %s WHERE id = %s",
            (saldo_novo, produto_id)
        )
        conn.execute(
            """INSERT INTO estoque_movimentos
               (produto_id, tipo, quantidade, saldo_anterior, saldo_posterior, motivo, atendente_id)
               VALUES (%s, 'entrada', %s, %s, %s, %s, %s)""",
            (produto_id, quantidade, saldo_ant, saldo_novo, motivo.strip(), atendente["id"])
        )

    return RedirectResponse(url="/caixa/estoque", status_code=303)


@router.get("/estoque/perda", response_class=HTMLResponse)
async def form_estoque_perda(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        produtos = conn.execute(
            "SELECT id, nome, quantidade_estoque FROM produtos WHERE ativo=1 ORDER BY nome"
        ).fetchall()
    return templates.TemplateResponse("caixa/estoque_perda.html", {
        "request": request,
        "atendente": atendente,
        "produtos": [dict(p) for p in produtos],
    })


@router.post("/estoque/perda")
async def registrar_perda(
    request: Request,
    produto_id: int = Form(...),
    quantidade: int = Form(...),
    motivo: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    if quantidade <= 0:
        return RedirectResponse(url="/caixa/estoque/perda?erro=qtde_invalida", status_code=303)

    with conectar() as conn:
        ant = conn.execute(
            "SELECT quantidade_estoque FROM produtos WHERE id = %s", (produto_id,)
        ).fetchone()
        saldo_ant = int(ant["quantidade_estoque"]) if ant else 0

        if quantidade > saldo_ant:
            return RedirectResponse(
                url=f"/caixa/estoque/perda?erro=sem_estoque&produto_id={produto_id}",
                status_code=303
            )

        saldo_novo = saldo_ant - quantidade
        conn.execute(
            "UPDATE produtos SET quantidade_estoque = %s WHERE id = %s",
            (saldo_novo, produto_id)
        )
        conn.execute(
            """INSERT INTO estoque_movimentos
               (produto_id, tipo, quantidade, saldo_anterior, saldo_posterior, motivo, atendente_id)
               VALUES (%s, 'saida', %s, %s, %s, %s, %s)""",
            (produto_id, quantidade, saldo_ant, saldo_novo,
             f"Perda: {motivo.strip()}" if motivo.strip() else "Perda", atendente["id"])
        )

    return RedirectResponse(url="/caixa/estoque", status_code=303)


@router.get("/estoque/{produto_id}/mov", response_class=JSONResponse)
async def movimentacoes_estoque(request: Request, produto_id: int):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse([], status_code=401)

    with conectar() as conn:
        produto = conn.execute(
            "SELECT nome, quantidade_estoque FROM produtos WHERE id = %s",
            (produto_id,)
        ).fetchone()
        if not produto:
            return JSONResponse({"erro": "Produto não encontrado"}, status_code=404)

        movs = conn.execute(
            """SELECT m.*, a.nome_completo AS atendente_nome
               FROM estoque_movimentos m
               LEFT JOIN atendentes a ON a.id = m.atendente_id
               WHERE m.produto_id = %s
               ORDER BY m.id DESC LIMIT 100""",
            (produto_id,)
        ).fetchall()

    return JSONResponse({
        "produto_nome": produto["nome"],
        "quantidade_atual": int(produto["quantidade_estoque"]),
        "movimentos": [
            {
                "id": m["id"],
                "tipo": m["tipo"],
                "quantidade": int(m["quantidade"]),
                "saldo_anterior": int(m["saldo_anterior"]),
                "saldo_posterior": int(m["saldo_posterior"]),
                "motivo": m["motivo"],
                "atendente_nome": m["atendente_nome"],
                "created_at": m["created_at"],
            }
            for m in movs
        ],
    })


# ── Caixas ────────────────────────────────────────────────────────────────────

@router.get("/caixas", response_class=HTMLResponse)
async def listar_caixas(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        caixas = conn.execute(
            """SELECT c.*, p.nome AS pix_nome, p.chave AS pix_chave
               FROM caixas c
               LEFT JOIN chaves_pix p ON p.id = c.chave_pix_id
               ORDER BY c.nome"""
        ).fetchall()
        chaves_pix = conn.execute(
            "SELECT id, nome, chave FROM chaves_pix WHERE ativa = TRUE ORDER BY nome"
        ).fetchall()

    return templates.TemplateResponse("caixa/caixas.html", {
        "request": request,
        "atendente": atendente,
        "caixas": [dict(c) for c in caixas],
        "chaves_pix": [dict(p) for p in chaves_pix],
    })


@router.post("/caixas/novo")
async def novo_caixa(
    request: Request,
    nome: str = Form(...),
    descricao: str = Form(""),
    chave_pix_id: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    pix_id = int(chave_pix_id) if chave_pix_id else None
    with conectar() as conn:
        conn.execute(
            "INSERT INTO caixas (nome, descricao, chave_pix_id) VALUES (%s, %s, %s) ON CONFLICT (nome) DO NOTHING",
            (nome.strip(), descricao.strip(), pix_id)
        )
    return RedirectResponse(url="/caixa/caixas", status_code=303)


@router.post("/caixas/{id}/pix")
async def atualizar_pix_caixa(request: Request, id: int, chave_pix_id: str = Form("")):
    atendente, redir = _guard(request)
    if redir:
        return redir

    pix_id = int(chave_pix_id) if chave_pix_id else None
    with conectar() as conn:
        conn.execute("UPDATE caixas SET chave_pix_id = %s WHERE id = %s", (pix_id, id))
    return RedirectResponse(url="/caixa/caixas", status_code=303)


@router.post("/caixas/{id}/toggle-ativo")
async def toggle_ativo_caixa(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            "UPDATE caixas SET ativo = CASE WHEN ativo=1 THEN 0 ELSE 1 END WHERE id=%s",
            (id,)
        )
    return RedirectResponse(url="/caixa/caixas", status_code=303)


# ── Busca de trabalhadores (autocomplete para fiado) ────────────────────────

@router.get("/trabalhadores/buscar", response_class=JSONResponse)
async def buscar_trabalhador_fiado(request: Request, q: str = Query("")):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse([], status_code=401)
    if len(q.strip()) < 2:
        return JSONResponse([])

    with conectar() as conn:
        termo = f"%{_normalizar(q.strip())}%"
        rows = conn.execute(
            """SELECT t.id, t.nome_completo, t.cpf,
                      t.fiado_limite, t.fiado_credito,
                      t.fiado_data_encerramento,
                      COALESCE((
                          SELECT SUM(GREATEST(v.total - v.fiado_credito_usado
                                              - COALESCE(a.alocado, 0), 0))
                          FROM vendas_pdv v
                          LEFT JOIN (SELECT venda_id, SUM(valor) AS alocado
                                     FROM fiado_alocacao WHERE tipo = 'divida'
                                     GROUP BY venda_id) a ON a.venda_id = v.id
                          WHERE v.fiado_trabalhador_id = t.id
                            AND v.fiado_pago = FALSE
                            AND v.status = 'concluida'
                      ), 0) AS total_pendente
               FROM trabalhadores t
               WHERE norm(t.nome_completo) LIKE %s AND t.ativo = 1
               ORDER BY t.nome_completo LIMIT 10""",
            (termo,)
        ).fetchall()

    import servico_fiado

    hoje = date.today()
    linhas = []
    for r in rows:
        limite = servico_fiado.moeda(r["fiado_limite"])
        pendente = servico_fiado.moeda(r["total_pendente"])
        # O que a tela mostra é o SALDO depois das alocações registradas; a
        # conta do limite é feita com Decimal, não com float de NUMERIC.
        disponivel = max(servico_fiado.ZERO, limite - pendente) if limite > 0 else None
        linhas.append({
            "id": r["id"],
            "nome": r["nome_completo"],
            "cpf": r["cpf"] or "",
            "fiado_limite": float(limite),
            "fiado_credito": float(servico_fiado.moeda(r["fiado_credito"])),
            "total_pendente": float(pendente),
            "disponivel": float(disponivel) if disponivel is not None else None,
            "fiado_bloqueado": (
                r["fiado_data_encerramento"] is not None
                and pendente > 0
                and date.fromisoformat(r["fiado_data_encerramento"]) < hoje
            ),
        })
    return JSONResponse(linhas)


# ── Fiados / Devedores ─────────────────────────────────────────────────────

@router.get("/fiados", response_class=HTMLResponse)
async def listar_fiados(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        from decimal import Decimal
        from servico_sessao import contas_por_tipo, sessoes_abertas
        todas = [dict(s) for s in sessoes_abertas(conn)]
        sessoes = [s for s in todas if s["versao"] == "eventos"]
        sessoes_legadas = [s for s in todas if s["versao"] != "eventos"]
        contas_pix = [dict(c) for c in contas_por_tipo(conn, "pix")]
        fiados = conn.execute(
            """SELECT v.*, c.nome AS caixa_nome,
                      t.nome_completo AS trabalhador_nome,
                      t.cpf AS trabalhador_cpf,
                      t.fiado_limite, t.fiado_credito,
                      t.fiado_data_encerramento
               FROM vendas_pdv v
               JOIN caixas c ON c.id = v.caixa_id
               JOIN trabalhadores t ON t.id = v.fiado_trabalhador_id
               WHERE v.forma_pagamento = 'fiado'
                 AND v.fiado_pago = FALSE
                 AND v.status = 'concluida'
               ORDER BY v.data_venda DESC, v.id DESC"""
        ).fetchall()

        # TASK_012 §4/§5: o que já foi recebido de cada dívida vem das alocações
        # gravadas junto ao lançamento — e a fórmula do saldo é a do serviço, a
        # mesma das telas do financeiro. Dívida sem alocação deve o valor de
        # face e é marcada como tal (`alocacoes_registradas = False`).
        ids_abertos = [f["id"] for f in fiados]
        import servico_fiado
        alocado_por_venda = servico_fiado.alocado_por_venda(conn, ids_abertos)

        agrupado = defaultdict(lambda: {"vendas": [], "total": Decimal("0.00")})
        for f in fiados:
            f_dict = dict(f)
            tid = f_dict["fiado_trabalhador_id"]
            linha = servico_fiado.linha_de_saldo(
                f_dict, alocado_por_venda.get(f_dict["id"], Decimal("0.00")),
                tem_alocacao=f_dict["id"] in alocado_por_venda)
            f_dict["saldo"] = linha["saldo"]
            f_dict["alocado"] = linha["recebido"]
            f_dict["alocacoes_registradas"] = linha["alocacoes_registradas"]
            f_dict["sem_alocacao"] = not linha["alocacoes_registradas"]
            agrupado[tid]["trabalhador_nome"] = f_dict["trabalhador_nome"]
            agrupado[tid]["trabalhador_cpf"] = f_dict.get("trabalhador_cpf") or ""
            agrupado[tid]["fiado_limite"] = Decimal(str(f_dict.get("fiado_limite") or 0))
            agrupado[tid]["fiado_credito"] = Decimal(str(f_dict.get("fiado_credito") or 0))
            agrupado[tid]["fiado_data_encerramento"] = f_dict.get("fiado_data_encerramento")
            agrupado[tid]["vendas"].append(f_dict)
            agrupado[tid]["total"] += f_dict["saldo"]
        # A lista de cada trabalhador sai da mais antiga para a mais recente:
        # é a ordem que o serviço usa quando o operador não escolhe, e é a
        # ordem que a tela mostra — seleção e regra coincidem à vista.
        for grupo in agrupado.values():
            grupo["vendas"].sort(key=lambda v: (v["data_venda"] or "", v["id"]))

    # TASK_016: escolha explícita da conta de destino nos formulários de
    # recebimento (pagar dívida e crédito antecipado).
    from servico_sessao import ROTULOS_TIPO_CONTA, contas_de_destino
    with conectar() as conn:
        contas_por_forma = {f: [dict(c) for c in contas_de_destino(conn, f)]
                            for f in ("especie", "pix")}

    return templates.TemplateResponse("caixa/fiados.html", {
        "request": request,
        "atendente": atendente,
        "fiados": [dict(f) for f in fiados],
        "agrupado": dict(agrupado),
        "sessoes": sessoes,
        "sessoes_legadas": sessoes_legadas,
        "contas_pix": contas_pix,
        "e_admin": e_admin(request),
        "contas_por_forma": contas_por_forma,
        "rotulos_tipo_conta": ROTULOS_TIPO_CONTA,
    })


@router.post("/fiados/{venda_id}/pagar")
async def pagar_fiado(
    request: Request,
    venda_id: int,
    forma_pagamento: str = Form("especie"),
    sessao_id: int = Form(0),
    conta_pix_id: int = Form(0),
    conta_id: int = Form(0),
    idempotencia: str = Form(""),
):
    """Quitação de dívida individual no fiado (TASK_008 / TASK_012 §4).

    O dinheiro recebido tem destino EXPLÍCITO: espécie exige sessão aberta
    (entra na gaveta dela); PIX exige conta PIX válida e nunca entra em
    gaveta. Repetir o pagamento da mesma venda é recusado pelo estado
    fiado_pago E pela identidade do evento (`fiadopagar:{venda_id}`) —
    a mesma dívida não conta duas vezes.

    §4: o cobrado é o SALDO (valor de face menos crédito consumido menos o que
    alocações anteriores já receberam) e a quitação fica registrada como
    vínculo durável lançamento↔dívida. Dívida abatida em parte não é cobrada
    duas vezes."""
    atendente, redir = _guard(request)
    if redir:
        return redir
    if forma_pagamento not in ("especie", "pix"):
        forma_pagamento = "especie"

    from decimal import Decimal
    import servico_fiado
    from servico_sessao import (
        SessaoInvalida, sessao_para_recebimento,
        validar_conta_para_forma,
    )

    corpo_tentativa = {"venda_id": venda_id, "forma": forma_pagamento,
                       "sessao_id": sessao_id,
                       "conta_pix_id": conta_pix_id or None,
                       "conta_id": _conta_pedida(forma_pagamento, conta_id,
                                                 conta_pix_id)}

    def _erro_fiado(chave_erro: str) -> str:
        return f"/caixa/fiados?erro={chave_erro}"

    try:
        with conectar() as conn:
            respondida = resposta_de_tentativa(
                conn, idempotencia=idempotencia, corpo=corpo_tentativa,
                erro_para=_erro_fiado, destino_padrao="/caixa/fiados",
                operacao="fiado_quitar_venda", autor_id=atendente["id"])
            if respondida:
                return respondida

            venda = conn.execute(
                """SELECT fiado_trabalhador_id, fiado_pago, forma_pagamento
                   FROM vendas_pdv WHERE id = %s FOR UPDATE""",
                (venda_id,)).fetchone()
            if not venda or venda["forma_pagamento"] != "fiado":
                raise SessaoInvalida("venda_nao_encontrada",
                                     "Venda de fiado não encontrada.")
            if venda["fiado_pago"]:
                raise SessaoInvalida("ja_pago",
                                     "Esta dívida já consta como paga — nada a receber.")

            trab_id = venda["fiado_trabalhador_id"]
            dividas = servico_fiado.dividas_em_aberto(
                conn, trab_id, travar=True, venda_ids=[venda_id])
            if not dividas:
                raise SessaoInvalida("ja_pago",
                                     "Esta dívida não está mais em aberto.")
            valor_pago = dividas[0]["saldo"]

            sessao = sessao_para_recebimento(conn, sessao_id, forma_pagamento)
            conta = validar_conta_para_forma(
                conn, forma_pagamento,
                _conta_pedida(forma_pagamento, conta_id, conta_pix_id))

            try:
                chave_tentativa = exigir_reserva(
                    conn, idempotencia=idempotencia, operacao="fiado_quitar_venda",
                    autor_id=atendente["id"], corpo=corpo_tentativa)
            except TentativaRecusada as e:
                return RedirectResponse(url=_erro_fiado(e.chave_erro), status_code=303)
            except TentativaJaRegistrada as e:
                return RedirectResponse(url=e.destino or "/caixa/fiados",
                                        status_code=303)

            fin_id = None
            if valor_pago > Decimal("0.00"):
                fin_id = servico_fiado.aplicar_recebimento(
                    conn, trabalhador_id=trab_id, dividas=dividas,
                    valor=valor_pago, forma=forma_pagamento, conta_id=conta,
                    sessao=sessao,
                    descricao=f"Recebimento fiado — venda #{venda_id}",
                    atendente_id=atendente["id"],
                    identidade=f"fiadopagar:{venda_id}",
                    venda_id_evento=venda_id)["financeiro_id"]
            else:
                # A compra foi coberta por crédito antecipado consumido no PDV:
                # não há dinheiro novo a lançar, só encerrar o débito.
                conn.execute(
                    """UPDATE vendas_pdv
                       SET fiado_pago = TRUE, fiado_data_pagamento = %s
                       WHERE id = %s AND forma_pagamento = 'fiado'""",
                    (date.today().isoformat(), venda_id))
            destino = f"/caixa/fiados?pago_ok={trab_id}&valor={valor_pago:.2f}"
            confirmar(conn, chave=chave_tentativa,
                      documento=fin_id or venda_id, destino=destino)
    except SessaoInvalida as e:
        return RedirectResponse(url=f"/caixa/fiados?erro={e.chave}", status_code=303)

    return RedirectResponse(url=destino, status_code=303)


@router.post("/fiados/{trabalhador_id}/pagar-divida")
async def pagar_divida_fiado(
    request: Request,
    trabalhador_id: int,
    valor: str = Form(""),
    forma_pagamento: str = Form("especie"),
    sessao_id: int = Form(0),
    conta_pix_id: int = Form(0),
    conta_id: int = Form(0),
    venda_ids: list[str] = Form(default=[]),
    idempotencia: str = Form(""),
):
    """Entrega de dinheiro para o fiado (total ou parcial) — TASK_008/§4.

    O valor entregue vira UMA única entrada no financeiro com destino explícito
    (sessão para espécie, conta PIX para PIX) e o destino de cada centavo é
    registrado em `fiado_alocacao`: uma linha por dívida abatida e, se sobrar,
    uma linha de excedente que vira crédito antecipado — sem segunda entrada de
    dinheiro (o consumo futuro desse crédito é evento sem efeito físico).

    A seleção do operador é respeitada na ordem recebida. Sem seleção, vale a
    regra que o sistema já tinha e que fica documentada: a compra mais antiga
    primeiro. Pagamento menor que a dívida escolhida ABATE a dívida (o saldo
    restante continua visível e `fiado_pago` segue FALSE) — só vira crédito o
    que sobrar depois de cobertas as dívidas selecionadas."""
    atendente, redir = _guard(request)
    if redir:
        return redir
    if forma_pagamento not in ("especie", "pix"):
        forma_pagamento = "especie"

    import servico_fiado
    from decimal import InvalidOperation
    from servico_sessao import (
        SessaoInvalida, sessao_para_recebimento, validar_conta_para_forma,
    )

    def _erro_divida(chave_erro: str) -> str:
        return f"/caixa/fiados?erro={chave_erro}"

    try:
        selecao = [int(str(v).strip()) for v in venda_ids if str(v).strip()]
    except ValueError:
        return RedirectResponse(url=_erro_divida("selecao_invalida"),
                                status_code=303)

    corpo_tentativa = {"trabalhador_id": trabalhador_id,
                       "valor": (valor or "").strip().replace(",", "."),
                       "forma": forma_pagamento, "sessao_id": sessao_id,
                       "conta_pix_id": conta_pix_id or None,
                       "dividas": selecao,
                       "conta_id": _conta_pedida(forma_pagamento, conta_id,
                                                 conta_pix_id)}

    try:
        with conectar() as conn:
            respondida = resposta_de_tentativa(
                conn, idempotencia=idempotencia, corpo=corpo_tentativa,
                erro_para=_erro_divida, destino_padrao="/caixa/fiados",
                operacao="fiado_pagar_divida", autor_id=atendente["id"])
            if respondida:
                return respondida

            if not conn.execute("SELECT id FROM trabalhadores WHERE id = %s",
                                (trabalhador_id,)).fetchone():
                raise SessaoInvalida("trabalhador_nao_encontrado",
                                     "Trabalhador não encontrado.")

            # Ordem dos locks: dívidas primeiro (por id, sempre), depois a
            # sessão. Dois recebimentos concorrentes sobre o mesmo trabalhador
            # caminham na mesma ordem e não deadlockam; os saldos lidos aqui
            # valem sob lock, então a distribuição nunca aloca o mesmo centavo
            # duas vezes.
            dividas = servico_fiado.dividas_em_aberto(
                conn, trabalhador_id, travar=True,
                venda_ids=selecao if selecao else None)
            if not dividas:
                raise SessaoInvalida("sem_divida",
                                     "Este trabalhador não tem dívidas de fiado em aberto.")
            if selecao and len(dividas) != len(selecao):
                raise SessaoInvalida(
                    "selecao_invalida",
                    "Alguma das dívidas escolhidas não está mais em aberto — "
                    "nada foi registrado. Recarregue a lista e confira a seleção.")

            total_devido = servico_fiado.total_devido(dividas)
            texto = (valor or "").strip().replace(",", ".")
            if not texto:
                valor_recebido = total_devido
            else:
                try:
                    valor_recebido = servico_fiado.moeda(texto)
                except (InvalidOperation, ValueError):
                    raise SessaoInvalida("valor_invalido", "Valor inválido.")
            if valor_recebido <= servico_fiado.ZERO:
                raise SessaoInvalida("valor_invalido", "Valor inválido.")

            sessao = sessao_para_recebimento(conn, sessao_id, forma_pagamento)
            conta = validar_conta_para_forma(
                conn, forma_pagamento,
                _conta_pedida(forma_pagamento, conta_id, conta_pix_id))

            try:
                chave_tentativa = exigir_reserva(
                    conn, idempotencia=idempotencia, operacao="fiado_pagar_divida",
                    autor_id=atendente["id"], corpo=corpo_tentativa)
            except TentativaRecusada as e:
                return RedirectResponse(url=_erro_divida(e.chave_erro), status_code=303)
            except TentativaJaRegistrada as e:
                return RedirectResponse(url=e.destino or "/caixa/fiados",
                                        status_code=303)

            trab = conn.execute(
                "SELECT nome_completo FROM trabalhadores WHERE id = %s", (trabalhador_id,)
            ).fetchone()
            nome = trab["nome_completo"] if trab else f"#{trabalhador_id}"
            resumo = servico_fiado.aplicar_recebimento(
                conn, trabalhador_id=trabalhador_id, dividas=dividas,
                valor=valor_recebido, forma=forma_pagamento, conta_id=conta,
                sessao=sessao, descricao=f"Recebimento fiado — {nome}",
                atendente_id=atendente["id"],
                venda_id_evento=(dividas[0]["venda_id"] if len(dividas) == 1
                                 else None))
            destino = (f"/caixa/fiados?pago_ok={trabalhador_id}"
                       f"&valor={resumo['valor']:.2f}"
                       f"&abatido={resumo['abatido']:.2f}"
                       f"&credito={resumo['excedente']:.2f}"
                       f"&quitadas={len(resumo['quitadas'])}")
            confirmar(conn, chave=chave_tentativa,
                      documento=resumo["financeiro_id"], destino=destino)
    except SessaoInvalida as e:
        return RedirectResponse(url=f"/caixa/fiados?erro={e.chave}", status_code=303)
    except servico_fiado.FiadoInvalido as e:
        return RedirectResponse(url=_erro_divida(e.chave), status_code=303)

    return RedirectResponse(url=destino, status_code=303)


@router.post("/fiados/{venda_id}/cancelar")
async def cancelar_fiado(request: Request, venda_id: int,
                         motivo: str = Form(""), confirmar: str = Form("")):
    """Cancelamento de fiado: apenas o serviço único (TASK_007).

    Esta rota já foi um atalho que estornava valores por conta própria
    (inclusive o total inteiro de um fiado pago — FIN-06). Agora ela
    encaminha o caso elegível ao mesmo serviço da rota de vendas e recusa
    qualquer outro com motivo claro. Não há reembolso de fiado pago aqui.
    """
    atendente, redir = _guard(request)
    if redir:
        return redir
    if not e_admin(request):
        return RedirectResponse(url="/caixa/fiados?erro=sem_permissao", status_code=303)

    if not confirmar or confirmar != "1" or not (motivo or "").strip():
        # sem motivo/confirmação: manda para a tela única de confirmação
        return RedirectResponse(url=f"/caixa/vendas/{venda_id}/cancelar", status_code=303)

    from servico_cancelamento import JaCancelada, CancelamentoBloqueado, VendaNaoEncontrada, cancelar
    avisos: list = []
    try:
        with conectar() as conn:
            resultado = cancelar(conn, venda_id, motivo, atendente["id"])
        return RedirectResponse(url="/caixa/fiados?cancelado_ok="
                                f"{resultado['venda_id']}", status_code=303)
    except CancelamentoBloqueado as e:
        avisos = e.motivos
    except JaCancelada as e:
        avisos = (["Esta venda já foi cancelada com reversão registrada."]
                  if e.com_prova else
                  ["Esta venda já consta como cancelada no histórico legado — "
                   "não existe prova de reversão associada. Nenhum efeito novo foi produzido."])
    except VendaNaoEncontrada:
        return HTMLResponse("<p>Venda não encontrada.</p>", status_code=404)
    return await _pagina_cancelar(request, atendente, venda_id, avisos=avisos,
                                  motivo_digitado=motivo)


@router.post("/fiados/credito/{trabalhador_id}")
async def adicionar_credito_fiado(
    request: Request,
    trabalhador_id: int,
    valor: str = Form("0"),
    descricao: str = Form(""),
    forma_pagamento: str = Form("especie"),
    sessao_id: int = Form(0),
    conta_pix_id: int = Form(0),
    conta_id: int = Form(0),
    idempotencia: str = Form(""),
):
    """Crédito antecipado (TASK_008 / FIN-03): o dinheiro entregue AGORA
    ganha entrada no financeiro com destino explícito — espécie na sessão
    aberta escolhida (entra na gaveta), PIX na conta PIX (não entra em
    gaveta). O crédito em si fica no trabalhador e o consumo futuro desse
    crédito é registrado como evento de efeito físico zero, sem segunda
    entrada de dinheiro."""
    atendente, redir = _guard(request)
    if redir:
        return redir
    if forma_pagamento not in ("especie", "pix"):
        forma_pagamento = "especie"

    from servico_sessao import (
        SessaoInvalida, contabilizar_recebimento, moeda,
        sessao_para_recebimento, validar_conta_para_forma,
    )

    try:
        valor_dec = moeda(valor, "valor")
    except SessaoInvalida as e:
        return RedirectResponse(url=f"/caixa/fiados?erro={e.chave}", status_code=303)

    desc = descricao.strip() or "Adição de crédito antecipado"
    corpo_tentativa = {"trabalhador_id": trabalhador_id, "valor": str(valor_dec),
                       "descricao": desc, "forma": forma_pagamento,
                       "sessao_id": sessao_id, "conta_pix_id": conta_pix_id or None,
                       "conta_id": _conta_pedida(forma_pagamento, conta_id,
                                                 conta_pix_id)}

    def _erro_credito(chave_erro: str) -> str:
        return f"/caixa/fiados?erro={chave_erro}"

    try:
        with conectar() as conn:
            respondida = resposta_de_tentativa(
                conn, idempotencia=idempotencia, corpo=corpo_tentativa,
                erro_para=_erro_credito, destino_padrao="/caixa/fiados",
                operacao="credito_anticipado", autor_id=atendente["id"])
            if respondida:
                return respondida

            sessao = sessao_para_recebimento(conn, sessao_id, forma_pagamento)
            conta = validar_conta_para_forma(
                conn, forma_pagamento,
                _conta_pedida(forma_pagamento, conta_id, conta_pix_id))

            if not conn.execute("SELECT id FROM trabalhadores WHERE id = %s",
                                (trabalhador_id,)).fetchone():
                raise SessaoInvalida("trabalhador_nao_encontrado",
                                     "Trabalhador não encontrado.")

            try:
                chave_tentativa = exigir_reserva(
                    conn, idempotencia=idempotencia,
                    operacao="credito_anticipado", autor_id=atendente["id"],
                    corpo=corpo_tentativa)
            except TentativaRecusada as e:
                return RedirectResponse(url=_erro_credito(e.chave_erro), status_code=303)
            except TentativaJaRegistrada as e:
                return RedirectResponse(url=e.destino or "/caixa/fiados",
                                        status_code=303)

            # TASK_012 §4: a linha do trabalhador é travada antes de somar —
            # duas entregas de crédito concorrentes não podem gravar mais saldo
            # do que o dinheiro efetivamente recebido.
            conn.execute("SELECT id FROM trabalhadores WHERE id = %s FOR UPDATE",
                         (trabalhador_id,)).fetchone()
            conn.execute(
                "UPDATE trabalhadores SET fiado_credito = fiado_credito + %s WHERE id = %s",
                (valor_dec, trabalhador_id)
            )
            cur = conn.execute(
                """INSERT INTO fiado_credito_mov (trabalhador_id, tipo, valor, descricao)
                   VALUES (%s, 'credito', %s, %s) RETURNING id""",
                (trabalhador_id, valor_dec, desc)
            )
            cred_id = cur.fetchone()["id"]
            fin_id = contabilizar_recebimento(
                conn, tipo="credito_anticipado", valor=valor_dec,
                categoria="credito_anticipado", descricao=desc,
                forma=forma_pagamento, conta_id=conta, sessao=sessao,
                identidade=f"credito:{cred_id}", atendente_id=atendente["id"])
            # O vínculo do crédito com o dinheiro que o gerou (mesmo
            # lançamento, não uma segunda entrada).
            conn.execute("UPDATE fiado_credito_mov SET financeiro_id = %s WHERE id = %s",
                         (fin_id, cred_id))
            conn.execute(
                """INSERT INTO fiado_alocacao
                   (financeiro_id, trabalhador_id, venda_id, tipo, valor)
                   VALUES (%s, %s, NULL, 'credito', %s)""",
                (fin_id, trabalhador_id, valor_dec))
            destino = f"/caixa/fiados?credito_ok={trabalhador_id}"
            confirmar(conn, chave=chave_tentativa, documento=cred_id,
                      destino=destino)
    except SessaoInvalida as e:
        return RedirectResponse(url=f"/caixa/fiados?erro={e.chave}", status_code=303)

    return RedirectResponse(url=destino, status_code=303)


@router.get("/fiados/credito/{trabalhador_id}/mov", response_class=JSONResponse)
async def historico_credito_fiado(request: Request, trabalhador_id: int):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse([], status_code=401)

    with conectar() as conn:
        rows = conn.execute(
            """SELECT id, tipo, valor, descricao, created_at
               FROM fiado_credito_mov
               WHERE trabalhador_id = %s
               ORDER BY id DESC LIMIT 50""",
            (trabalhador_id,)
        ).fetchall()

    return JSONResponse([
        {"id": r["id"], "tipo": r["tipo"], "valor": float(r["valor"]),
         "descricao": r["descricao"], "created_at": r["created_at"]}
        for r in rows
    ])


@router.get("/fiados/extrato/{trabalhador_id}", response_class=HTMLResponse)
async def extrato_fiado(request: Request, trabalhador_id: int):
    """Extrato do fiado de um trabalhador (TASK_012 §4).

    Saldo DEVEDOR e CRÉDITO aparecem separados, cada número com o link do
    lançamento ou da venda que o origina. O que não tem vínculo durável
    (histórico anterior a esta entrega) continua visível como limitação
    explícita — não é redistribuído por nome, valor nem data."""
    atendente, redir = _guard(request)
    if redir:
        return redir

    import servico_fiado
    with conectar() as conn:
        dados = servico_fiado.extrato_fiado(conn, trabalhador_id)
    if not dados:
        return RedirectResponse(url="/caixa/fiados?erro=trabalhador_nao_encontrado",
                                status_code=303)

    return templates.TemplateResponse("caixa/fiado_extrato.html", {
        "request": request,
        "atendente": atendente,
        "e_admin": e_admin(request),
        **dados,
    })


# ── Abertura de Caixa ───────────────────────────────────────────────────────

@router.get("/abrir", response_class=HTMLResponse)
async def form_abrir_caixa(request: Request, caixa_id: int = Query(0)):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        caixa = conn.execute(
            "SELECT * FROM caixas WHERE id=%s AND ativo=1", (caixa_id,)
        ).fetchone()
        if not caixa:
            return RedirectResponse(url="/caixa/pdv", status_code=303)

    return templates.TemplateResponse("caixa/abrir.html", {
        "request": request,
        "atendente": atendente,
        "caixa": dict(caixa),
    })


@router.post("/abrir")
async def salvar_abertura(
    request: Request,
    caixa_id: int = Form(...),
    saldo_inicial: str = Form("0"),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    from servico_sessao import SessaoInvalida, abrir_sessao, moeda_nao_negativa
    try:
        # REV-02/TASK_010: saldo inicial zero é legítimo (caixa sem fundo de
        # troco); negativos/NaN/estouro seguem recusados pelo parser explícito.
        inicial = moeda_nao_negativa(saldo_inicial or "0", "saldo_inicial")
    except SessaoInvalida as e:
        return RedirectResponse(url=f"/caixa/abrir?caixa_id={caixa_id}&erro=valor_invalido",
                                status_code=303)
    erro = None
    sessao_id = None
    try:
        with conectar() as conn:
            sessao_id = abrir_sessao(conn, caixa_id, inicial, atendente["id"])
    except SessaoInvalida as e:
        erro = e.chave
    if erro:
        return RedirectResponse(url=f"/caixa/pdv?caixa_id={caixa_id}&erro={erro}",
                                status_code=303)
    return RedirectResponse(url=f"/caixa/pdv?caixa_id={caixa_id}", status_code=303)


# ── Sangria ──────────────────────────────────────────────────────────────────

@router.get("/sangria", response_class=HTMLResponse)
async def form_sangria(request: Request, caixa_movimento_id: int = Query(0)):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        mov = conn.execute(
            """SELECT m.*, c.nome AS caixa_nome
               FROM caixa_movimentos m
               JOIN caixas c ON c.id = m.caixa_id
               WHERE m.id = %s AND m.status = 'aberto'""",
            (caixa_movimento_id,)
        ).fetchone()
        if not mov:
            return RedirectResponse(url="/caixa/pdv", status_code=303)
        destinos = conn.execute(
            """SELECT id, nome, tipo FROM contas_financeiras
               WHERE ativo = 1 AND tipo <> 'especie' ORDER BY nome""",
        ).fetchall()
        esperado = None
        if mov["versao"] == "eventos":
            from servico_sessao import resumo_sessao
            esperado = resumo_sessao(conn, caixa_movimento_id)["saldo_fisico_esperado"]

    return templates.TemplateResponse("caixa/sangria.html", {
        "request": request,
        "atendente": atendente,
        "movimento": dict(mov),
        "destinos": [dict(d) for d in destinos],
        "esperado": float(esperado) if esperado is not None else None,
    })


@router.post("/sangria")
async def salvar_sangria(
    request: Request,
    caixa_movimento_id: int = Form(...),
    valor: str = Form("0"),
    motivo: str = Form(""),
    recebedor_nome: str = Form(""),
    conta_destino_id: int = Form(0),
    idempotencia: str = Form(""),
):
    """Sangria (TASK_008): só existe com DESTINO IDENTIFICADO. O dinheiro
    físico sai da gaveta e entra na conta escolhida como transferência
    interna — par saída/entrada vinculado pelo mesmo sangria_id, efeito
    global zero no financeiro (nada vira despesa falsa). Sem destino a
    operação é recusada antes de gravar. Sessão 'eventos' registra o evento
    sangria com efeito físico −valor; sessão legado mantém o acumulador que
    o fechamento antigo continua usando."""
    atendente, redir = _guard(request)
    if redir:
        return redir

    from servico_sessao import (
        SessaoInvalida, VERSAO_EVENTOS, moeda, registrar_evento,
        validar_conta_para_forma,
    )

    erro_base = f"/caixa/sangria?caixa_movimento_id={caixa_movimento_id}&erro="
    try:
        valor_dec = moeda(valor, "valor")
    except SessaoInvalida as e:
        return RedirectResponse(url=erro_base + e.chave, status_code=303)

    corpo_tentativa = {"sessao_id": caixa_movimento_id, "valor": str(valor_dec),
                       "motivo": motivo.strip(),
                       "recebedor_nome": recebedor_nome.strip(),
                       "conta_destino_id": conta_destino_id}

    try:
        with conectar() as conn:
            respondida = resposta_de_tentativa(
                conn, idempotencia=idempotencia, corpo=corpo_tentativa,
                erro_para=lambda ce: erro_base + ce,
                destino_padrao=(f"/caixa/sangria"
                                f"?caixa_movimento_id={caixa_movimento_id}"),
                operacao="sangria", autor_id=atendente["id"])
            if respondida:
                return respondida

            mov = conn.execute(
                """SELECT * FROM caixa_movimentos
                   WHERE id = %s AND status = 'aberto' FOR UPDATE""",
                (caixa_movimento_id,)
            ).fetchone()
            if not mov:
                raise SessaoInvalida("sessao_invalida",
                                     "A sessão informada não está aberta.")

            if not conta_destino_id:
                raise SessaoInvalida(
                    "destino_obrigatorio",
                    "Sangria exige destino: escolha a conta que receberá o valor.")
            conta_destino = conn.execute(
                "SELECT id, nome, tipo FROM contas_financeiras WHERE id = %s AND ativo = 1",
                (conta_destino_id,)).fetchone()
            if not conta_destino:
                raise SessaoInvalida("destino_invalido",
                                     "A conta de destino não é uma conta ativa.")
            conta_saida = validar_conta_para_forma(conn, "especie", None)
            if conta_destino["id"] == conta_saida:
                raise SessaoInvalida(
                    "destino_invalido",
                    "O destino precisa ser uma conta diferente da conta espécie "
                    "da gaveta.")

            try:
                chave_tentativa = exigir_reserva(
                    conn, idempotencia=idempotencia, operacao="sangria",
                    autor_id=atendente["id"], corpo=corpo_tentativa)
            except TentativaRecusada as e:
                return RedirectResponse(url=erro_base + e.chave_erro, status_code=303)
            except TentativaJaRegistrada as e:
                return RedirectResponse(
                    url=e.destino or f"/caixa/sangria"
                                      f"?caixa_movimento_id={caixa_movimento_id}",
                    status_code=303)

            cur = conn.execute(
                """INSERT INTO caixa_sangrias
                   (caixa_movimento_id, valor, motivo, atendente_id, recebedor_nome)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id""",
                (caixa_movimento_id, valor_dec, motivo.strip(),
                 atendente["id"], recebedor_nome.strip())
            )
            sangria_id = cur.fetchone()["id"]
            desc = motivo.strip() or "Sangria de caixa"

            fin_saida_id = conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, descricao, caixa_id, forma_pagamento,
                    conta_id, sangria_id, status)
                   VALUES ('saida', 'sangria', %s, %s, %s, 'especie', %s, %s, 'pago')
                   RETURNING id""",
                (valor_dec, desc, mov["caixa_id"], conta_saida, sangria_id)
            ).fetchone()["id"]
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, descricao, caixa_id, conta_id,
                    sangria_id, status)
                   VALUES ('entrada', 'transferencia', %s, %s, NULL, %s, %s, 'pago')""",
                (valor_dec, f"Transferência interna da sangria — {desc}",
                 conta_destino["id"], sangria_id)
            )

            if mov["versao"] == VERSAO_EVENTOS:
                registrar_evento(
                    conn, identidade=f"sangria:{sangria_id}", sessao_id=mov["id"],
                    tipo="sangria", natureza="retirada",
                    origem_tipo="caixa_sangrias", origem_id=sangria_id,
                    financeiro_id=fin_saida_id,
                    forma="especie", sentido="saida", valor=valor_dec,
                    efeito_fisico=-valor_dec, conta_id=conta_destino["id"],
                    atendente_id=atendente["id"], observacao=desc)
            else:
                conn.execute(
                    "UPDATE caixa_movimentos SET total_sangrias = total_sangrias + %s WHERE id = %s",
                    (valor_dec, mov["id"])
                )
            destino = f"/caixa/sangria/{sangria_id}/comprovante"
            confirmar(conn, chave=chave_tentativa, documento=sangria_id,
                      destino=destino)
    except SessaoInvalida as e:
        return RedirectResponse(url=erro_base + e.chave, status_code=303)

    return RedirectResponse(url=destino, status_code=303)


@router.get("/sangria/{sangria_id}/comprovante", response_class=HTMLResponse)
async def comprovante_sangria(request: Request, sangria_id: int):
    with conectar() as conn:
        sangria = conn.execute(
            """SELECT s.*, c.nome AS caixa_nome, a.nome_completo AS atendente_nome
               FROM caixa_sangrias s
               JOIN caixa_movimentos m ON m.id = s.caixa_movimento_id
               JOIN caixas c ON c.id = m.caixa_id
               LEFT JOIN atendentes a ON a.id = s.atendente_id
               WHERE s.id = %s""",
            (sangria_id,)
        ).fetchone()
        if not sangria:
            return HTMLResponse("<p>Sangria não encontrada.</p>", status_code=404)

        centro = conn.execute(
            "SELECT chave, valor FROM configuracoes_centro"
        ).fetchall()
        cfg = {r["chave"]: r["valor"] for r in centro}
        centro_nome = cfg.get("centro_nome", "Centro Espirita")

    return templates.TemplateResponse("caixa/sangria_comprovante.html", {
        "request": request,
        "sangria": dict(sangria),
        "centro_nome": centro_nome,
    })


@router.get("/sangria/{sangria_id}/escpos")
async def escpos_sangria(request: Request, sangria_id: int):
    from fastapi.responses import Response

    with conectar() as conn:
        sangria = conn.execute(
            """SELECT s.*, c.nome AS caixa_nome, a.nome_completo AS atendente_nome
               FROM caixa_sangrias s
               JOIN caixa_movimentos m ON m.id = s.caixa_movimento_id
               JOIN caixas c ON c.id = m.caixa_id
               LEFT JOIN atendentes a ON a.id = s.atendente_id
               WHERE s.id = %s""",
            (sangria_id,)
        ).fetchone()
        if not sangria:
            return JSONResponse({"erro": "Sangria não encontrada"}, status_code=404)

        centro = conn.execute(
            "SELECT chave, valor FROM configuracoes_centro"
        ).fetchall()
        cfg = {r["chave"]: r["valor"] for r in centro}
        centro_nome = cfg.get("centro_nome", "Centro Espírita")

    dados = _gerar_bytes_escpos_sangria(dict(sangria), centro_nome)
    return Response(content=dados, media_type="application/octet-stream")


@router.get("/mensalidades/{mov_id}/escpos")
async def escpos_mensalidade(request: Request, mov_id: int):
    from fastapi.responses import Response
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse({}, status_code=401)

    with conectar() as conn:
        mov = conn.execute(
            """SELECT fm.*, t.nome_completo, t.cpf
               FROM financeiro_movimentacoes fm
               JOIN trabalhadores t ON t.id = fm.trabalhador_id
               WHERE fm.id = %s AND fm.categoria = 'mensalidade'""",
            (mov_id,)
        ).fetchone()
        if not mov:
            return JSONResponse({"erro": "Movimentação não encontrada"}, status_code=404)

        centro = conn.execute("SELECT chave, valor FROM configuracoes_centro").fetchall()
        centro_nome = {r["chave"]: r["valor"] for r in centro}.get("centro_nome", "Centro Espírita")

    trabalhador = {"nome_completo": mov["nome_completo"], "cpf": mov["cpf"]}
    dados = _gerar_bytes_escpos_mensalidade(dict(mov), trabalhador, centro_nome)
    return Response(content=dados, media_type="application/octet-stream")


@router.get("/fiados/{trabalhador_id}/recibo-escpos")
async def escpos_recibo_fiado(request: Request, trabalhador_id: int, valor: str = "0"):
    """Retorna bytes ESC/POS para recibo de pagamento de fiado (2 vias)."""
    from fastapi.responses import Response
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse({}, status_code=401)

    with conectar() as conn:
        trab = conn.execute(
            "SELECT nome_completo, cpf FROM trabalhadores WHERE id = %s", (trabalhador_id,)
        ).fetchone()
        if not trab:
            return JSONResponse({"erro": "Trabalhador não encontrado"}, status_code=404)

        centro = conn.execute("SELECT chave, valor FROM configuracoes_centro").fetchall()
        centro_nome = {r["chave"]: r["valor"] for r in centro}.get("centro_nome", "Centro Espírita")

    dados = _gerar_bytes_escpos_recibo_fiado(dict(trab), float(valor or 0), centro_nome)
    return Response(content=dados, media_type="application/octet-stream")


# ── Fechamento de Caixa ─────────────────────────────────────────────────────

@router.get("/fechar", response_class=HTMLResponse)
async def relatorio_fechamento(request: Request, caixa_movimento_id: int = Query(0),
                               erro: str = ""):
    atendente, redir = _guard(request)
    if redir:
        return redir

    from servico_sessao import VERSAO_EVENTOS, resumo_sessao

    with conectar() as conn:
        mov = conn.execute(
            """SELECT m.*, c.nome AS caixa_nome,
                      a1.nome_completo AS atendente_abertura_nome,
                      a2.nome_completo AS atendente_fechamento_nome
               FROM caixa_movimentos m
               JOIN caixas c ON c.id = m.caixa_id
               LEFT JOIN atendentes a1 ON a1.id = m.atendente_id_abertura
               LEFT JOIN atendentes a2 ON a2.id = m.atendente_id_fechamento
               WHERE m.id = %s""",
            (caixa_movimento_id,)
        ).fetchone()
        if not mov:
            return RedirectResponse(url="/caixa/pdv", status_code=303)

        eventos = []
        resumo = None
        if mov["versao"] == VERSAO_EVENTOS:
            resumo = resumo_sessao(conn, caixa_movimento_id)
            eventos = conn.execute(
                """SELECT e.*, a.nome_completo AS atendente_nome,
                          cf.nome AS conta_nome
                   FROM caixa_sessao_eventos e
                   LEFT JOIN atendentes a ON a.id = e.atendente_id
                   LEFT JOIN contas_financeiras cf ON cf.id = e.conta_id
                   WHERE e.sessao_id = %s ORDER BY e.id""",
                (caixa_movimento_id,)
            ).fetchall()
            eventos = [dict(e) for e in eventos]

        # Vendas do movimento
        vendas = conn.execute(
            """SELECT v.*, c.nome AS caixa_nome, a.nome_completo AS atendente_nome
               FROM vendas_pdv v
               JOIN caixas c ON c.id = v.caixa_id
               LEFT JOIN atendentes a ON a.id = v.atendente_id
               WHERE v.caixa_movimento_id = %s AND v.status = 'concluida'
               ORDER BY v.id""",
            (caixa_movimento_id,)
        ).fetchall()

        # Total por forma de pagamento
        pgto = conn.execute(
            """SELECT forma_pagamento,
                      COUNT(*) AS qtd,
                      COALESCE(SUM(total + COALESCE(doacao_valor, 0)), 0) AS total
               FROM vendas_pdv
               WHERE caixa_movimento_id = %s AND status = 'concluida'
               GROUP BY forma_pagamento""",
            (caixa_movimento_id,)
        ).fetchall()

        # Total por produto
        produtos = conn.execute(
            """SELECT i.nome_produto,
                      SUM(i.quantidade) AS qtd,
                      COALESCE(SUM(i.subtotal), 0) AS total
               FROM vendas_pdv_itens i
               JOIN vendas_pdv v ON v.id = i.venda_id
               WHERE v.caixa_movimento_id = %s AND v.status = 'concluida'
               GROUP BY i.nome_produto
               ORDER BY qtd DESC""",
            (caixa_movimento_id,)
        ).fetchall()

        # Sangrias do movimento
        sangrias = conn.execute(
            """SELECT s.*, a.nome_completo AS atendente_nome
               FROM caixa_sangrias s
               LEFT JOIN atendentes a ON a.id = s.atendente_id
               WHERE s.caixa_movimento_id = %s
               ORDER BY s.id""",
            (caixa_movimento_id,)
        ).fetchall()

    if resumo is not None:
        # versão eventos: a prévia vem TODA dos eventos — mesma fórmula do
        # fechamento e do comprovante (esperado = inicial + Σ efeito físico).
        saldo_esperado = float(resumo["saldo_fisico_esperado"])
    else:
        saldo_esperado = float(mov["saldo_inicial"]) + float(mov["total_vendas"]) - float(mov["total_sangrias"])

    # TASK_016: sessão legada com vendas ANTERIORES à coluna `vendas_pdv.conta_id`
    # não tem destino persistido. O fechamento recusa adivinhar — a tela pede a
    # conta aqui, e o POST valida contra o contrato conta×forma.
    from servico_sessao import ROTULOS_TIPO_CONTA, contas_de_destino

    faltando_conta = []
    if mov["versao"] != VERSAO_EVENTOS:
        with conectar() as conn2:
            faltando_conta = [dict(r) for r in conn2.execute(
                """SELECT DISTINCT forma_pagamento FROM vendas_pdv
                   WHERE caixa_movimento_id = %s AND status = 'concluida'
                     AND conta_id IS NULL
                     AND forma_pagamento IN ('especie', 'pix')
                   ORDER BY forma_pagamento""", (caixa_movimento_id,)).fetchall()]
            opcoes_conta = {f["forma_pagamento"]:
                            [dict(c) for c in contas_de_destino(conn2, f["forma_pagamento"])]
                            for f in faltando_conta}

    return templates.TemplateResponse("caixa/fechamento.html", {
        "request": request,
        "atendente": atendente,
        "movimento": dict(mov),
        "vendas": [dict(v) for v in vendas],
        "pgto_agrupado": [dict(p) for p in pgto],
        "produtos": [dict(p) for p in produtos],
        "sangrias": [dict(s) for s in sangrias],
        "saldo_esperado": saldo_esperado,
        "resumo": resumo,
        "eventos": eventos,
        "erro": erro,
        "faltando_conta": faltando_conta,
        "opcoes_conta": opcoes_conta if faltando_conta else {},
        "rotulos_tipo_conta": ROTULOS_TIPO_CONTA,
    })


@router.post("/fechar")
async def fechar_caixa(
    request: Request,
    caixa_movimento_id: int = Form(...),
    observacao: str = Form(""),
    dinheiro_contado: str = Form(""),
    conta_legado_especie: int = Form(0),
    conta_legado_pix: int = Form(0),
):
    """Fechamento (TASK_008): sessões 'eventos' conferem a gaveta — o
    esperado vem dos eventos (inicial + Σ efeito físico), o contado é
    registrado ao lado com divergência, e contado ≠ esperado exige
    observação; o esperado NUNCA é sobrescrito. Sessões iniciadas antes da
    implantação fecham exatamente pela regra anterior (acumuladores)."""
    atendente, redir = _guard(request)
    if redir:
        return redir

    from decimal import Decimal
    from servico_sessao import (
        SessaoInvalida, VERSAO_EVENTOS, fechar_sessao_eventos, moeda_nao_negativa,
    )

    try:
        # REV-02/TASK_010: mesma regra explícita do saldo inicial — contado zero
        # é legítimo, negativo recusa ANTES do arredondamento, teto NUMERIC(12,2)
        # conferido depois dele (parser único, sem política por acidente).
        contado = moeda_nao_negativa(dinheiro_contado or "0", "dinheiro_contado")
    except SessaoInvalida:
        return RedirectResponse(
            url=f"/caixa/fechar?caixa_movimento_id={caixa_movimento_id}"
                "&erro=valor_contado_invalido", status_code=303)

    try:
        with conectar() as conn:
            # FOR UPDATE: serializa com o cancelamento (TASK_007) e com a
            # sangria. Ou a operação entra antes do fechamento (e aparece no
            # resumo), ou o fechamento trava a sessão primeiro e a operação
            # seguinte é recusada.
            mov = conn.execute(
                "SELECT * FROM caixa_movimentos WHERE id = %s AND status = 'aberto' FOR UPDATE",
                (caixa_movimento_id,)
            ).fetchone()
            if not mov:
                return RedirectResponse(url="/caixa/pdv", status_code=303)

            if mov["versao"] == VERSAO_EVENTOS:
                fechar_sessao_eventos(conn, caixa_movimento_id, contado,
                                      observacao, atendente["id"])
                return RedirectResponse(
                    url=f"/caixa/fechar?caixa_movimento_id={caixa_movimento_id}"
                        "&fechado=1", status_code=303)

            # ── sessão LEGADO: regra anterior intacta ──
            if (dinheiro_contado or "").strip():
                # o formulário novo (contagem de gaveta) não se aplica a
                # sessões abertas antes da implantação
                return RedirectResponse(
                    url=f"/caixa/fechar?caixa_movimento_id={caixa_movimento_id}"
                        "&erro=sessao_legada", status_code=303)
            saldo_final = float(mov["saldo_inicial"]) + float(mov["total_vendas"]) - float(mov["total_sangrias"])

            conn.execute(
                """UPDATE caixa_movimentos
                   SET status = 'fechado',
                       data_fechamento = (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp::text,
                       saldo_final = %s,
                       atendente_id_fechamento = %s,
                       observacao_fechamento = %s
                   WHERE id = %s""",
                (saldo_final, atendente["id"], observacao.strip(), caixa_movimento_id)
            )

            caixa_info = conn.execute("SELECT nome FROM caixas WHERE id=%s", (mov["caixa_id"],)).fetchone()
            caixa_nome = caixa_info["nome"] if caixa_info else f"#{mov['caixa_id']}"

            # escolhas do operador para vendas SEM conta persistida (ver abaixo)
            # só as formas que o formulário oferece; `fiado` tem regra própria
            contas_legado = {"especie": conta_legado_especie,
                             "pix": conta_legado_pix}
            from servico_sessao import conta_de_destino as _conta_destino

            # ── destino do dinheiro por VÍNCULO PERSISTIDO (TASK_016) ────────
            #
            # A versão anterior deduzia a conta pelo TIPO da forma de pagamento
            # (`conta_id_por_tipo`). Depois da TASK_016 isso deixou de funcionar
            # com mais de uma conta do mesmo tipo — devolvia None e o lançamento
            # de fechamento ficaria SEM conta, o que é pior que falhar.
            #
            # Agora o destino vem de `vendas_pdv.conta_id`, gravado no momento da
            # venda (vínculo comprovado). Vendas anteriores a essa coluna têm
            # NULL: para elas, nada é adivinhado — o fechamento EXIGE a escolha
            # do operador, que entra em `conta_legado_<forma>` no formulário.
            vendas_por_pgto = conn.execute(
                """SELECT forma_pagamento, conta_id,
                          COALESCE(SUM(total + COALESCE(doacao_valor, 0)), 0) AS total
                   FROM vendas_pdv
                   WHERE caixa_movimento_id = %s AND status = 'concluida'
                   GROUP BY forma_pagamento, conta_id
                   ORDER BY forma_pagamento, conta_id""",
                (caixa_movimento_id,)
            ).fetchall()

            for vp in vendas_por_pgto:
                total_pgto = float(vp["total"])
                if total_pgto <= 0:
                    continue
                forma = vp["forma_pagamento"]
                if vp["conta_id"] is not None:
                    cid = vp["conta_id"]
                elif forma == "fiado":
                    # Fiado NÃO é dinheiro recebido: o valor mora na conta de
                    # obrigações, e é a própria regra da venda a fiado.
                    cid = _conta_obrigacoes(conn)
                    if cid is None:
                        raise SessaoInvalida(
                            "conta_obrigacoes_ausente",
                            "Não há conta de Valores a Receber ativa para registrar "
                            "as vendas a fiado deste fechamento.")
                else:
                    pedida = contas_legado.get(forma)
                    if not pedida:
                        raise SessaoInvalida(
                            "conta_do_fechamento",
                            "Estas vendas foram registradas antes de a conta de "
                            "destino ser guardada. Escolha para qual conta o valor "
                            f"de {forma} deve entrar.")
                    try:
                        cid = _conta_destino(conn, forma, pedida, exigir_escolha=False)
                    except SessaoInvalida:
                        raise SessaoInvalida(
                            "conta_do_fechamento",
                            "A conta escolhida para o fechamento não é válida para "
                            f"{forma}. Escolha outra.")
                conn.execute(
                    """INSERT INTO financeiro_movimentacoes
                       (tipo, categoria, valor, descricao, caixa_id, forma_pagamento, conta_id)
                       VALUES ('entrada', 'venda_pdv', %s, %s, %s, %s, %s)""",
                    (total_pgto,
                     f"Vendas {caixa_nome} ({forma}) — fechamento #{caixa_movimento_id}",
                     mov["caixa_id"], forma, cid)
                )
    except SessaoInvalida as e:
        return RedirectResponse(
            url=f"/caixa/fechar?caixa_movimento_id={caixa_movimento_id}"
                f"&erro={e.chave}", status_code=303)

    return RedirectResponse(url=f"/caixa/fechar?caixa_movimento_id={caixa_movimento_id}", status_code=303)


@router.get("/fechar/{mov_id}/pdf")
async def exportar_pdf_fechamento(request: Request, mov_id: int):
    from fastapi.responses import Response
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from io import BytesIO

    with conectar() as conn:
        mov = conn.execute(
            """SELECT m.*, c.nome AS caixa_nome,
                      a1.nome_completo AS atendente_abertura_nome,
                      a2.nome_completo AS atendente_fechamento_nome
               FROM caixa_movimentos m
               JOIN caixas c ON c.id = m.caixa_id
               LEFT JOIN atendentes a1 ON a1.id = m.atendente_id_abertura
               LEFT JOIN atendentes a2 ON a2.id = m.atendente_id_fechamento
               WHERE m.id = %s""",
            (mov_id,)
        ).fetchone()
        if not mov:
            return HTMLResponse("<p>Movimento não encontrado.</p>", status_code=404)

        centro = conn.execute(
            "SELECT chave, valor FROM configuracoes_centro"
        ).fetchall()
        cfg = {r["chave"]: r["valor"] for r in centro}
        centro_nome = cfg.get("centro_nome", "Centro Espírita")

        vendas = conn.execute(
            "SELECT * FROM vendas_pdv WHERE caixa_movimento_id = %s AND status = 'concluida' ORDER BY id",
            (mov_id,)
        ).fetchall()

        pgto = conn.execute(
            """SELECT forma_pagamento, COUNT(*) AS qtd, COALESCE(SUM(total + COALESCE(doacao_valor, 0)),0) AS total
               FROM vendas_pdv WHERE caixa_movimento_id = %s AND status = 'concluida'
               GROUP BY forma_pagamento""",
            (mov_id,)
        ).fetchall()

        produtos = conn.execute(
            """SELECT i.nome_produto, SUM(i.quantidade) AS qtd, COALESCE(SUM(i.subtotal),0) AS total
               FROM vendas_pdv_itens i
               JOIN vendas_pdv v ON v.id = i.venda_id
               WHERE v.caixa_movimento_id = %s AND v.status = 'concluida'
               GROUP BY i.nome_produto ORDER BY qtd DESC""",
            (mov_id,)
        ).fetchall()

        sangrias = conn.execute(
            "SELECT * FROM caixa_sangrias WHERE caixa_movimento_id = %s ORDER BY id",
            (mov_id,)
        ).fetchall()

        resumo_v2 = None
        eventos_v2 = []
        if mov["versao"] == "eventos":
            from servico_sessao import resumo_sessao
            resumo_v2 = resumo_sessao(conn, mov_id)
            eventos_v2 = conn.execute(
                """SELECT e.*, cf.nome AS conta_nome
                   FROM caixa_sessao_eventos e
                   LEFT JOIN contas_financeiras cf ON cf.id = e.conta_id
                   WHERE e.sessao_id = %s ORDER BY e.id""",
                (mov_id,)
            ).fetchall()
            eventos_v2 = [dict(e) for e in eventos_v2]

    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm

    styles = getSampleStyleSheet()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            topMargin=12*mm, bottomMargin=12*mm,
                            leftMargin=15*mm, rightMargin=15*mm)

    s8  = ParagraphStyle("s8",  parent=styles["Normal"], fontSize=8,  leading=10)
    s9b = ParagraphStyle("s9b", parent=styles["Normal"], fontSize=9,  leading=11, fontName="Helvetica-Bold")
    s10b= ParagraphStyle("s10b",parent=styles["Normal"], fontSize=10, leading=12, fontName="Helvetica-Bold")

    TS_HEADER = ("BACKGROUND", (0,0),(-1,0), colors.HexColor("#1a5fa8"))
    TS_WHITE  = ("TEXTCOLOR",  (0,0),(-1,0), colors.white)
    TS_HBOLD  = ("FONTNAME",   (0,0),(-1,0), "Helvetica-Bold")
    TS_GRID   = ("GRID", (0,0),(-1,-1), 0.4, colors.grey)
    TS_PAD    = [("BOTTOMPADDING",(0,0),(-1,-1),3),("TOPPADDING",(0,0),(-1,-1),3)]
    TS_FS8    = ("FONTSIZE",(0,0),(-1,-1),8)
    TS_ARIGHT = ("ALIGN",(1,0),(-1,-1),"RIGHT")

    def mini_table(data, widths, bold_last=False):
        t = Table(data, colWidths=widths)
        style = [TS_HEADER, TS_WHITE, TS_HBOLD, TS_GRID, TS_FS8, TS_ARIGHT] + TS_PAD
        if bold_last:
            style.append(("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"))
        t.setStyle(TableStyle(style))
        return t

    W = doc.width
    elems = []

    # Cabeçalho
    elems.append(Paragraph(f"<b>{centro_nome} — Fechamento de Caixa</b>", s10b))
    elems.append(Spacer(1, 4))

    # Info do movimento em 2 colunas lado a lado
    left = [
        ["Caixa:", mov["caixa_nome"]],
        ["Abertura:", (mov["data_abertura"] or "-")[:19]],
        ["Fechamento:", (mov.get("data_fechamento") or "-")[:19]],
    ]
    right = [
        ["Atendente abertura:", mov.get("atendente_abertura_nome") or "-"],
        ["Atendente fechamento:", mov.get("atendente_fechamento_nome") or "-"],
        ["Status:", "FECHADO" if mov["status"] == "fechado" else "ABERTO"],
    ]
    tl = Table(left,  colWidths=[W*0.18, W*0.32])
    tr = Table(right, colWidths=[W*0.22, W*0.28])
    for t in (tl, tr):
        t.setStyle(TableStyle([
            ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,-1),8),
            ("BOTTOMPADDING",(0,0),(-1,-1),2),
            ("TOPPADDING",(0,0),(-1,-1),2),
        ]))
    elems.append(Table([[tl, tr]], colWidths=[W*0.5, W*0.5]))
    elems.append(Spacer(1, 6))

    # Resumo + Pgto lado a lado
    if resumo_v2:
        r = resumo_v2
        saldo_esp = float(r["saldo_fisico_esperado"])
        res_data = [["Descrição", "R$"],
                    ["Saldo inicial", f"{float(r['saldo_inicial']):.2f}"],
                    ["Vendas em espécie", f"{float(r['vendas_especie']):.2f}"],
                    ["Vendas PIX (conta bancária)", f"{float(r['vendas_pix']):.2f}"],
                    ["Vendas fiado (a receber)", f"{float(r['vendas_fiado']):.2f}"]]
        if float(r["vendas_canceladas"]) > 0:
            res_data.append(["Vendas canceladas", f"({float(r['vendas_canceladas']):.2f})"])
        if float(r["credito_consumido"]) > 0:
            res_data.append(["Consumo de crédito antecipado", f"{float(r['credito_consumido']):.2f}"])
        if float(r["doacoes"]) > 0:
            res_data.append(["Doações", f"{float(r['doacoes']):.2f}"])
        if float(r["mensalidades"]) > 0:
            res_data.append([f"Mensalidades ({int(r['qtd_mensalidades'])})", f"{float(r['mensalidades']):.2f}"])
        if float(r["recebimentos_fiado"]) > 0:
            res_data.append(["Quitaciones de fiado", f"{float(r['recebimentos_fiado']):.2f}"])
        if float(r["creditos_anticipados"]) > 0:
            res_data.append(["Créditos antecipados", f"{float(r['creditos_anticipados']):.2f}"])
        res_data.append(["Sangrias", f"({float(r['sangrias']):.2f})"])
        res_data.append(["Dinheiro físico esperado", f"{saldo_esp:.2f}"])
        if mov.get("dinheiro_contado") is not None:
            res_data.append(["Dinheiro contado", f"{float(mov['dinheiro_contado']):.2f}"])
            res_data.append(["Divergência de contagem", f"{float(mov['divergencia_contagem']):.2f}"])
    else:
        saldo_esp = float(mov["saldo_inicial"]) + float(mov["total_vendas"]) - float(mov["total_sangrias"])
        res_data = [["Descrição","R$"],
                    ["Saldo inicial", f"{float(mov['saldo_inicial']):.2f}"],
                    ["Total vendas",  f"{float(mov['total_vendas']):.2f}"],
                    ["Total sangrias",f"({float(mov['total_sangrias']):.2f})"],
                    ["Saldo esperado",f"{saldo_esp:.2f}"]]
    if mov.get("saldo_final") is not None:
        res_data.append(["Saldo final", f"{float(mov['saldo_final']):.2f}"])
    t_res = mini_table(res_data, [W*0.34, W*0.12], bold_last=True)

    mapa_pgto = {"especie":"Espécie","pix":"PIX","fiado":"Fiado"}
    if pgto:
        pg_data = [["Pagamento","Qtd","R$"]]
        for p in pgto:
            pg_data.append([mapa_pgto.get(p["forma_pagamento"], p["forma_pagamento"]),
                            str(p["qtd"]), f"{float(p['total']):.2f}"])
        t_pg = mini_table(pg_data, [W*0.22, W*0.08, W*0.10])
        elems.append(Table([[t_res, "", t_pg]], colWidths=[W*0.42, W*0.06, W*0.52]))
    else:
        elems.append(t_res)
    elems.append(Spacer(1, 6))

    # Produtos vendidos
    if produtos:
        prod_data = [["Produto","Qtd","R$"]]
        for p in produtos:
            prod_data.append([p["nome_produto"], str(int(p["qtd"])), f"{float(p['total']):.2f}"])
        elems.append(Paragraph("<b>Produtos Vendidos</b>", s9b))
        elems.append(Spacer(1, 3))
        elems.append(mini_table(prod_data, [W*0.65, W*0.12, W*0.23]))
        elems.append(Spacer(1, 6))

    # Sangrias
    if sangrias:
        sang_data = [["Data","Valor","Motivo","Recebedor"]]
        for s in sangrias:
            sang_data.append([
                (s.get("data_hora") or "")[:16],
                f"{float(s['valor']):.2f}",
                s.get("motivo") or "-",
                s.get("recebedor_nome") or "-",
            ])
        elems.append(Paragraph("<b>Sangrias</b>", s9b))
        elems.append(Spacer(1, 3))
        elems.append(mini_table(sang_data, [W*0.20, W*0.12, W*0.35, W*0.33]))
        elems.append(Spacer(1, 6))

    # Eventos detalhados (sessões 'eventos' — TASK_008)
    if eventos_v2:
        ev_data = [["#", "Tipo", "Forma", "Valor", "Efeito gaveta", "Conta", "Instante"]]
        for e in eventos_v2:
            ev_data.append([
                str(e["id"]), e["tipo"], e["forma"],
                f"{float(e['valor']):.2f}", f"{float(e['efeito_fisico']):.2f}",
                e.get("conta_nome") or "-", (e.get("criado_em") or "")[:19],
            ])
        elems.append(Paragraph("<b>Eventos da sessão (fonte dos totais)</b>", s9b))
        elems.append(Spacer(1, 3))
        elems.append(mini_table(ev_data, [W*0.05, W*0.18, W*0.10, W*0.12, W*0.14, W*0.21, W*0.20]))
        elems.append(Spacer(1, 6))

    # Observação
    if mov.get("observacao_fechamento"):
        elems.append(Paragraph(f"<b>Obs:</b> {mov['observacao_fechamento']}", s8))
    if mov.get("observacao_conferencia"):
        elems.append(Paragraph(f"<b>Conferência:</b> {mov['observacao_conferencia']}", s8))

    doc.build(elems)
    buf.seek(0)

    return Response(
        content=buf.read(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=fechamento_caixa_{mov_id}.pdf"}
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gerar_bytes_escpos(venda: dict, itens: list, centro_nome: str,
                         trabalhador: dict = None, via_caixa: bool = False,
                         cancelamento: dict = None) -> bytes:
    ESC = b'\x1b'
    GS  = b'\x1d'
    LF  = b'\n'

    INIT          = ESC + b'@'
    ALINHAR_ESQU  = ESC + b'a\x00'
    ALINHAR_CENT  = ESC + b'a\x01'
    ALINHAR_DIR   = ESC + b'a\x02'
    NEGRITO_ON    = ESC + b'E\x01'
    NEGRITO_OFF   = ESC + b'E\x00'
    DUPLO_ON      = GS  + b'!\x11'   # largura×2, altura×2
    DUPLO_OFF     = GS  + b'!\x00'
    SUBLINHADO_ON = ESC + b'-\x01'
    SUBLINHADO_OFF= ESC + b'-\x00'
    CORTAR        = GS  + b'V\x42\x05'  # partial cut + avanço 5mm

    COLS = 42  # colunas na fonte normal (80 mm)

    def txt(s: str) -> bytes:
        return s.encode('cp860', errors='replace')

    def linha(s: str = '') -> bytes:
        return txt(s) + LF

    def separador(c: str = '-') -> bytes:
        return linha(c * COLS)

    buf = bytearray()
    buf += INIT
    buf += ALINHAR_ESQU

    if (venda.get('status') or '') == 'cancelada' or cancelamento:
        buf += ALINHAR_CENT
        buf += DUPLO_ON + NEGRITO_ON
        buf += linha("CANCELADA")
        buf += NEGRITO_OFF + DUPLO_OFF
        buf += ALINHAR_ESQU
        if cancelamento:
            motivo = (cancelamento.get('motivo') or '')[:38]
            buf += linha(f"Motivo: {motivo}")
            buf += linha(f"Em: {(cancelamento.get('criado_em') or '')[:19]}")
        else:
            buf += linha("Cancelamento legado: sem prova de reversao")
        buf += separador()

    # ── Itens em destaque (fonte dupla) ───────────────────────────────────────
    COLS_D = COLS // 2  # 21 colunas efetivas em fonte 2×2
    buf += separador()
    buf += DUPLO_ON
    for item in itens:
        nome = item['nome_produto'].upper()
        qtd  = item['quantidade']
        sub  = _fmt_valor(float(item['subtotal']))

        prefixo = f"{qtd}x "
        espaco_nome = COLS_D - len(prefixo)
        primeira = nome[:espaco_nome]
        resto    = nome[espaco_nome:]
        buf += linha(prefixo + primeira)
        while resto:
            buf += linha(f"   {resto[:COLS_D - 3]}")
            resto = resto[COLS_D - 3:]

        buf += linha(sub.rjust(COLS_D))

    buf += DUPLO_OFF
    buf += separador()

    # ── Total em negrito (fonte normal) ───────────────────────────────────────
    buf += ALINHAR_CENT
    buf += NEGRITO_ON
    buf += linha(f"TOTAL {_fmt_valor(float(venda['total']))}")
    buf += NEGRITO_OFF
    buf += LF

    # ── Forma de pagamento ─────────────────────────────────────────────────────
    buf += ALINHAR_ESQU
    mapa_pgto = {'especie': 'Espécie', 'pix': 'PIX', 'fiado': 'Fiado'}
    pgto = mapa_pgto.get(venda['forma_pagamento'], venda['forma_pagamento'])
    buf += NEGRITO_ON
    buf += linha(f"Pagamento: {pgto}")
    buf += NEGRITO_OFF
    if venda['forma_pagamento'] == 'especie':
        troco = float(venda.get('troco') or 0)
        if troco > 0:
            recebido = float(venda['total']) + troco
            buf += linha(f"Recebido:  {_fmt_valor(recebido)}")
            buf += linha(f"Troco:     {_fmt_valor(troco)}")

    # ── Fiado ──────────────────────────────────────────────────────────────────
    if venda['forma_pagamento'] == 'fiado' and trabalhador:
        buf += separador()
        buf += ALINHAR_ESQU
        buf += NEGRITO_ON
        buf += linha("FIADO")
        buf += NEGRITO_OFF
        buf += linha(f"Devedor: {trabalhador['nome_completo'][:36]}")
        cpf_trab = trabalhador.get('cpf') or '---'
        buf += linha(f"CPF: {cpf_trab[:36]}")
        if via_caixa:
            buf += ALINHAR_CENT
            buf += separador()
            buf += NEGRITO_ON
            buf += linha("VIA DO CAIXA")
            buf += NEGRITO_OFF
            buf += linha("COMPROVANTE DE FIADO")
            buf += LF
            buf += linha("Assinatura do Devedor:")
            buf += linha("_________________________")
            buf += LF
        buf += separador()

    buf += LF
    buf += CORTAR

    return bytes(buf)


def _gerar_bytes_escpos_sangria(sangria: dict, centro_nome: str) -> bytes:
    """Gera bytes ESC/POS para comprovante de sangria em 2 vias."""
    ESC = b'\x1b'
    GS  = b'\x1d'
    LF  = b'\n'

    INIT          = ESC + b'@'
    ALINHAR_ESQU  = ESC + b'a\x00'
    ALINHAR_CENT  = ESC + b'a\x01'
    NEGRITO_ON    = ESC + b'E\x01'
    NEGRITO_OFF   = ESC + b'E\x00'
    DUPLO_ON      = GS  + b'!\x11'
    DUPLO_OFF     = GS  + b'!\x00'
    CORTAR        = GS  + b'V\x42\x05'

    COLS = 42

    def txt(s: str) -> bytes:
        return s.encode('cp860', errors='replace')

    def linha(s: str = '') -> bytes:
        return txt(s) + LF

    def separador(c: str = '-') -> bytes:
        return linha(c * COLS)

    valor = float(sangria["valor"])
    data_hora = (sangria.get("data_hora") or "")[:19]
    recebedor = sangria.get("recebedor_nome") or ""
    motivo = sangria.get("motivo") or ""
    caixa_nome = sangria.get("caixa_nome") or ""
    atendente_nome = sangria.get("atendente_nome") or ""

    buf = bytearray()
    buf += INIT

    for via in [1, 2]:
        if via == 2:
            buf += CORTAR
            buf += INIT
            # Avança papel entre as vias
            buf += LF * 3

        buf += ALINHAR_CENT
        buf += NEGRITO_ON
        buf += linha(centro_nome[:COLS])
        buf += NEGRITO_OFF
        buf += linha("COMPROVANTE DE SANGRIA")
        buf += LF

        buf += ALINHAR_CENT
        buf += NEGRITO_ON
        buf += DUPLO_ON
        buf += linha(f"R$ {_fmt_valor(valor)}")
        buf += DUPLO_OFF
        buf += NEGRITO_OFF
        buf += LF

        buf += ALINHAR_ESQU
        buf += separador()
        buf += linha(f"Caixa: {caixa_nome[:36]}")
        if atendente_nome:
            buf += linha(f"Atendente: {atendente_nome[:36]}")
        buf += linha(f"Data/Hora: {data_hora}")
        if recebedor:
            buf += linha(f"Retirado por: {recebedor[:36]}")
        if motivo:
            buf += linha(f"Motivo: {motivo[:36]}")
        buf += separador()
        buf += LF

        if via == 1:
            # VIA DO CAIXA
            buf += ALINHAR_CENT
            buf += NEGRITO_ON
            buf += linha("VIA DO CAIXA")
            buf += NEGRITO_OFF
            buf += linha("Retirei o valor acima e assino:")
            buf += LF * 3
            buf += ALINHAR_ESQU
            if recebedor:
                buf += linha(f"Nome: {recebedor[:36]}")
            buf += separador()
        else:
            # VIA DO RETIRANTE
            buf += ALINHAR_CENT
            buf += NEGRITO_ON
            buf += linha("VIA DO RETIRANTE")
            buf += NEGRITO_OFF
            buf += linha("Recebido por (carimbo e assinatura do caixa):")
            buf += LF * 3
            buf += ALINHAR_ESQU
            if atendente_nome:
                buf += linha(f"Caixa: {atendente_nome[:36]}")
            buf += separador()

    buf += LF
    buf += CORTAR
    return bytes(buf)


def _gerar_bytes_escpos_mensalidade(mov: dict, trabalhador: dict, centro_nome: str) -> bytes:
    """Gera bytes ESC/POS para recibo de mensalidade em 2 vias (trabalhador + caixa)."""
    ESC = b'\x1b'
    GS  = b'\x1d'
    LF  = b'\n'

    INIT          = ESC + b'@'
    ALINHAR_ESQU  = ESC + b'a\x00'
    ALINHAR_CENT  = ESC + b'a\x01'
    NEGRITO_ON    = ESC + b'E\x01'
    NEGRITO_OFF   = ESC + b'E\x00'
    DUPLO_ON      = GS  + b'!\x11'
    DUPLO_OFF     = GS  + b'!\x00'
    CORTAR        = GS  + b'V\x42\x05'

    COLS = 42

    MESES_PT = ["", "JAN", "FEV", "MAR", "ABR", "MAI", "JUN",
                "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]

    def txt(s: str) -> bytes:
        return s.encode('cp860', errors='replace')

    def linha(s: str = '') -> bytes:
        return txt(s) + LF

    def separador(c: str = '-') -> bytes:
        return linha(c * COLS)

    valor = float(mov["valor"])
    # Data de pagamento real (fallback: vencimento para registros antigos)
    data_str = str(mov.get("data_pagamento") or mov.get("data_movimentacao") or "")[:10]
    try:
        ano, mes_n, dia = data_str.split("-")
        mes_ext = f"{MESES_PT[int(mes_n)]}/{ano}"
        data_fmt = f"{dia}/{mes_n}/{ano}"
    except Exception:
        mes_ext = data_str[:7]
        data_fmt = data_str

    nome = (trabalhador.get("nome_completo") or "").upper()
    cpf  = trabalhador.get("cpf") or ""

    buf = bytearray()
    buf += INIT

    for via in [1, 2]:
        if via == 2:
            buf += CORTAR
            buf += INIT

        buf += ALINHAR_CENT
        buf += NEGRITO_ON
        buf += linha(centro_nome[:COLS])
        buf += NEGRITO_OFF
        buf += linha("RECIBO DE MENSALIDADE")
        buf += LF

        buf += ALINHAR_CENT
        buf += DUPLO_ON
        buf += linha(f"R$ {_fmt_valor(valor)}")
        buf += DUPLO_OFF
        buf += LF

        buf += ALINHAR_ESQU
        buf += separador()
        buf += NEGRITO_ON
        buf += linha(nome[:COLS])
        buf += NEGRITO_OFF
        if cpf:
            buf += linha(f"CPF: {cpf}")
        buf += linha(f"Referencia: {mes_ext}")
        buf += linha(f"Data pgto:  {data_fmt}")
        buf += separador()
        buf += LF

        buf += ALINHAR_CENT
        buf += NEGRITO_ON
        if via == 1:
            buf += linha("1a VIA - TRABALHADOR")
            buf += NEGRITO_OFF
            buf += linha("Assinatura do Caixa:")
            buf += LF * 2
            buf += ALINHAR_ESQU
            buf += linha("_" * 36)
        else:
            buf += linha("2a VIA - CAIXA")
            buf += NEGRITO_OFF
            buf += linha("Assinatura do Trabalhador:")
            buf += LF * 2
            buf += ALINHAR_ESQU
            buf += linha("_" * 36)
        buf += separador()

    buf += LF
    buf += CORTAR
    return bytes(buf)


def _fmt_produto(p: dict) -> dict:
    return {
        "id": p["id"],
        "nome": p["nome"],
        "preco_venda": float(p["preco_venda"]),
        "categoria": p["categoria"],
        "codigo_barras": p["codigo_barras"],
        "foto_produto": p.get("foto_produto") or "",
    }


def _tlv(tag: str, valor: str) -> str:
    return f"{tag}{len(valor):02d}{valor}"


def _crc16(data: str) -> str:
    crc = 0xFFFF
    for byte in data.encode():
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def _normalizar_chave_pix(chave: str, tipo: str) -> str:
    chave = chave.strip()
    if tipo in ("cpf", "cnpj"):
        return re.sub(r'\D', '', chave)
    if tipo == "telefone":
        digits = re.sub(r'\D', '', chave)
        if chave.startswith('+'):
            return '+' + digits
        if len(digits) <= 11:
            return '+55' + digits
        return '+' + digits
    if tipo == "email":
        return chave.lower()
    return chave


def _gerar_payload_pix(chave: str, valor: float, descricao: str = "") -> str:
    merchant_info = _tlv("00", "br.gov.bcb.pix") + _tlv("01", chave)
    payload = _tlv("00", "01")
    payload += _tlv("26", merchant_info)
    payload += _tlv("52", "0000")
    payload += _tlv("53", "986")
    if valor > 0:
        payload += _tlv("54", f"{valor:.2f}")
    payload += _tlv("58", "BR")
    payload += _tlv("59", descricao[:25] if descricao else "Shambala")
    payload += _tlv("60", "Volta Redonda")
    payload += _tlv("62", _tlv("05", "***"))
    payload += "6304"
    return payload + _crc16(payload)


def _gerar_recibo(venda, itens, centro_nome: str,
                  trabalhador: dict = None, via_caixa: bool = False,
                  cancelamento: dict = None) -> str:
    """Gera texto formatado para impressora de cupom 80 colunas."""
    from datetime import datetime

    L = 66
    now = datetime.now()
    data_str = now.strftime("%d/%m/%Y")
    hora_str = now.strftime("%H:%M")

    linhas = []
    linhas.append(centro_nome.center(L))
    linhas.append("")
    linhas.append(f"COMPROVANTE DE VENDA #{venda['id']}".center(L))
    if (venda.get("status") or "") == "cancelada" or cancelamento:
        linhas.append(">>> V E N D A   C A N C E L A D A <<<".center(L))
        if cancelamento:
            instante = cancelamento.get("criado_em") or ""
            try:
                instante = datetime.strptime(instante[:19], "%Y-%m-%d %H:%M:%S") \
                             .strftime("%d/%m/%Y %H:%M:%S")
            except (ValueError, TypeError):
                pass
            linhas.append(f"Cancelada em: {instante}")
            linhas.append(f"Por: {cancelamento.get('autor_nome') or '—'}")
            linhas.append(f"Motivo: {(cancelamento.get('motivo') or '')[:60]}")
        else:
            linhas.append("Cancelamento do legado: sem prova de reversão.")
    linhas.append(f"Data: {data_str}    Hora: {hora_str}")
    linhas.append(f"Caixa: {venda['caixa_nome']}")
    if venda.get("atendente_nome"):
        linhas.append(f"Atendente: {venda['atendente_nome']}")
    linhas.append("-" * L)
    linhas.append(f"{'Qtd':>3}  {'Produto':<36}  {'Pr.Un.':>8}  {'Subtotal':>10}")
    linhas.append("-" * L)

    for item in itens:
        nome = item["nome_produto"][:36]
        qtd = item["quantidade"]
        pu = _fmt_valor(float(item["preco_unitario"]))
        sub = _fmt_valor(float(item["subtotal"]))
        linhas.append(f"{qtd:>3}  {nome:<36}  {pu:>8}  {sub:>10}")

    linhas.append("-" * L)
    total_str = _fmt_valor(float(venda["total"]))
    linhas.append(f"TOTAL:".rjust(L - len(total_str) - 1) + " " + total_str)
    linhas.append("")

    mapa_pgto = {"especie": "Especie", "pix": "PIX", "fiado": "Fiado"}
    pgto = mapa_pgto.get(venda["forma_pagamento"], venda["forma_pagamento"])
    linhas.append(f"Forma de pagamento: {pgto}")
    if venda["forma_pagamento"] == "especie":
        recebido = float(venda.get("total", 0)) + float(venda.get("troco", 0))
        linhas.append(f"Valor recebido: {_fmt_valor(recebido)}")
        if float(venda.get("troco", 0)) > 0:
            linhas.append(f"Troco: {_fmt_valor(float(venda['troco']))}")

    if venda["forma_pagamento"] == "fiado" and trabalhador:
        linhas.append("")
        linhas.append("FIADO".center(L))
        linhas.append(f"Devedor: {trabalhador['nome_completo']}")
        linhas.append(f"CPF: {trabalhador.get('cpf', '---')}")
        if via_caixa:
            linhas.append("-" * L)
            linhas.append("VIA DO CAIXA".center(L))
            linhas.append("COMPROVANTE DE FIADO".center(L))
            linhas.append("")
            linhas.append("Assinatura do Devedor:")
            linhas.append("_" * 40)

    linhas.append("")
    linhas.append("-" * L)
    linhas.append("OBRIGADO PELA PREFERENCIA!".center(L))
    linhas.append("Volte sempre!".center(L))

    return "\n".join(linhas)


def _gerar_bytes_escpos_recibo_fiado(trabalhador: dict, valor: float, centro_nome: str) -> bytes:
    """Gera bytes ESC/POS para recibo de pagamento de fiado em 2 vias."""
    ESC = b'\x1b'
    GS  = b'\x1d'
    LF  = b'\n'

    INIT          = ESC + b'@'
    ALINHAR_ESQU  = ESC + b'a\x00'
    ALINHAR_CENT  = ESC + b'a\x01'
    NEGRITO_ON    = ESC + b'E\x01'
    NEGRITO_OFF   = ESC + b'E\x00'
    DUPLO_ON      = GS  + b'!\x11'
    DUPLO_OFF     = GS  + b'!\x00'
    CORTAR        = GS  + b'V\x42\x05'

    COLS = 42

    def txt(s: str) -> bytes:
        return s.encode('cp860', errors='replace')

    def linha(s: str = '') -> bytes:
        return txt(s) + LF

    def separador(c: str = '-') -> bytes:
        return linha(c * COLS)

    from datetime import datetime
    now = datetime.now()
    data_str = now.strftime("%d/%m/%Y %H:%M")
    nome = (trabalhador.get("nome_completo") or "").upper()
    cpf = trabalhador.get("cpf") or ""

    buf = bytearray()
    buf += INIT

    for via in [1, 2]:
        if via == 2:
            buf += CORTAR
            buf += INIT
            buf += LF * 3

        buf += ALINHAR_CENT
        buf += NEGRITO_ON
        buf += linha(centro_nome[:COLS])
        buf += NEGRITO_OFF
        buf += linha("RECIBO DE PAGAMENTO — FIADO")
        buf += LF

        buf += ALINHAR_CENT
        buf += DUPLO_ON
        buf += linha(f"R$ {_fmt_valor(valor)}")
        buf += DUPLO_OFF
        buf += LF

        buf += ALINHAR_ESQU
        buf += separador()
        buf += NEGRITO_ON
        buf += linha(f"DEVEDOR: {nome[:38]}")
        buf += NEGRITO_OFF
        if cpf:
            buf += linha(f"CPF: {cpf[:38]}")
        buf += linha(f"Data: {data_str}")
        buf += separador()
        buf += LF

        buf += ALINHAR_CENT
        buf += NEGRITO_ON
        if via == 1:
            buf += linha("VIA DO CAIXA")
            buf += NEGRITO_OFF
            buf += linha("Assinatura do Devedor:")
            buf += LF * 3
            buf += ALINHAR_ESQU
            buf += linha("_" * 36)
        else:
            buf += linha("VIA DO DEVEDOR")
            buf += NEGRITO_OFF
            buf += linha("Recebido por (carimbo e assinatura do caixa):")
            buf += LF * 3
            buf += ALINHAR_ESQU
            buf += linha("_" * 36)
        buf += separador()

    buf += LF
    buf += CORTAR
    return bytes(buf)


def _fmt_valor(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


_CATEGORIAS = [
    ("salgado_assado", "Salgado Assado"),
    ("salgado_frito",  "Salgado Frito"),
    ("bebida",         "Bebida"),
    ("doce",           "Doce"),
    ("outro",          "Outro"),
]
