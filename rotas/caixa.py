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

from banco import conectar, _normalizar
from rotas.auth import obter_atendente_logado, e_admin
from templates_config import templates

router = APIRouter(prefix="/caixa")


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


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
                    precisa_abrir = True
            if caixa_sel:
                atalhos = conn.execute(
                    """SELECT id, nome, preco_venda, foto_produto
                       FROM produtos WHERE atalho=1 AND ativo=1 AND caixa_id=%s ORDER BY nome""",
                    (caixa_id,)
                ).fetchall()

    return templates.TemplateResponse("caixa/pdv.html", {
        "request": request,
        "atendente": atendente,
        "caixas": [dict(c) for c in caixas],
        "caixa_sel": dict(caixa_sel) if caixa_sel else None,
        "movimento": dict(movimento) if movimento else None,
        "precisa_abrir": precisa_abrir,
        "atalhos": [dict(a) for a in atalhos],
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
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    itens = json.loads(itens_json)
    if not itens:
        return RedirectResponse(url=f"/caixa/pdv?caixa_id={caixa_id}", status_code=303)

    total = sum(float(i["subtotal"]) for i in itens)
    recebido = float(valor_recebido or 0)
    troco = max(0, recebido - total) if forma_pagamento == "especie" else 0

    credito_usado = 0.0
    if forma_pagamento == "fiado" and trabalhador_id:
        with conectar() as conn_check:
            trab = conn_check.execute(
                """SELECT fiado_limite, fiado_data_encerramento, fiado_credito
                   FROM trabalhadores WHERE id = %s""",
                (trabalhador_id,)
            ).fetchone()
            if not trab:
                return RedirectResponse(url=f"/caixa/pdv?caixa_id={caixa_id}&erro=trabalhador_nao_encontrado", status_code=303)

            # Verifica data de encerramento
            if trab["fiado_data_encerramento"]:
                if date.today() > date.fromisoformat(trab["fiado_data_encerramento"]):
                    return RedirectResponse(url=f"/caixa/pdv?caixa_id={caixa_id}&erro=fiado_encerrado", status_code=303)

            # Calcula dívida pendente
            pendente = conn_check.execute(
                """SELECT COALESCE(SUM(total - fiado_credito_usado), 0) AS total
                   FROM vendas_pdv
                   WHERE fiado_trabalhador_id = %s AND fiado_pago = FALSE AND status = 'concluida'""",
                (trabalhador_id,)
            ).fetchone()["total"]

            # Calcula quanto usar do crédito antecipado
            credito_usado = min(float(trab["fiado_credito"]), total)

            # Verifica limite
            novo_devido = total - credito_usado
            if float(trab["fiado_limite"]) > 0 and (float(pendente) + novo_devido) > float(trab["fiado_limite"]):
                return RedirectResponse(url=f"/caixa/pdv?caixa_id={caixa_id}&erro=fiado_excede_limite", status_code=303)

    mov_id = caixa_movimento_id if caixa_movimento_id else None
    doacao = float(doacao_valor or 0)

    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO vendas_pdv
               (caixa_id, data_venda, total, forma_pagamento, troco, status, atendente_id,
                fiado_trabalhador_id, fiado_credito_usado, caixa_movimento_id, doacao_valor)
               VALUES (%s, %s, %s, %s, %s, 'concluida', %s, %s, %s, %s, %s)
               RETURNING id""",
            (caixa_id, date.today().isoformat(), total,
             forma_pagamento, troco, atendente["id"],
             trabalhador_id if trabalhador_id else None,
             credito_usado, mov_id, doacao)
        )
        venda_id = cur.fetchone()["id"]

        for item in itens:
            if not item.get("produto_id"):
                continue
            conn.execute(
                """INSERT INTO vendas_pdv_itens
                   (venda_id, produto_id, nome_produto, quantidade, preco_unitario, subtotal)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (venda_id, item["produto_id"], item["nome"],
                 item["quantidade"], float(item["preco_unitario"]),
                 float(item["subtotal"]))
            )

        # Aplica crédito antecipado se houver
        if credito_usado > 0:
            conn.execute(
                "UPDATE trabalhadores SET fiado_credito = fiado_credito - %s WHERE id = %s",
                (credito_usado, trabalhador_id)
            )
            conn.execute(
                """INSERT INTO fiado_credito_mov (trabalhador_id, tipo, valor, descricao)
                   VALUES (%s, 'debito', %s, %s)""",
                (trabalhador_id, credito_usado, f"Compra #{venda_id}")
            )

        # Atualiza total de vendas no movimento de caixa
        if mov_id:
            conn.execute(
                "UPDATE caixa_movimentos SET total_vendas = total_vendas + %s WHERE id = %s",
                (total, mov_id)
            )

        # Deduz estoque (apenas itens com produto real)
        for item in itens:
            if not item.get("produto_id"):
                continue
            conn.execute("""
                UPDATE produtos SET quantidade_estoque = quantidade_estoque - %s
                WHERE id = %s""",
                (item["quantidade"], item["produto_id"])
            )
            conn.execute("""
                INSERT INTO estoque_movimentos
                (produto_id, tipo, quantidade, saldo_anterior, saldo_posterior, referencia_id, atendente_id)
                SELECT %s, 'saida', %s,
                    quantidade_estoque + %s,
                    quantidade_estoque,
                    %s, %s
                FROM produtos WHERE id = %s""",
                (item["produto_id"], item["quantidade"],
                 item["quantidade"], venda_id, atendente["id"],
                 item["produto_id"])
            )

        # Registra doação no financeiro e inclui no total do caixa
        if doacao > 0:
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, descricao, caixa_id)
                   VALUES ('entrada', 'doacao', %s, %s, %s)""",
                (doacao, f"Doação via {'PIX' if forma_pagamento == 'pix' else 'troco'} — venda #{venda_id}", caixa_id)
            )
            if mov_id:
                conn.execute(
                    "UPDATE caixa_movimentos SET total_vendas = total_vendas + %s WHERE id = %s",
                    (doacao, mov_id)
                )

    # Sinaliza ao segundo monitor que a venda foi concluída
    import asyncio
    asyncio.create_task(_gerenciador_qr.transmitir({"tipo": "confirmado"}))

    if imprimir != "0":
        fiado_param = "&fiado=1" if forma_pagamento == "fiado" else ""
        return RedirectResponse(
            url=f"/caixa/pdv?caixa_id={caixa_id}&ok={venda_id}{fiado_param}",
            status_code=303
        )
    return RedirectResponse(
        url=f"/caixa/pdv?caixa_id={caixa_id}",
        status_code=303
    )


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
async def listar_vendas(request: Request, caixa_id: int = 0, data: str = ""):
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
            f"""SELECT v.*, c.nome AS caixa_nome, a.nome_completo AS atendente_nome
                FROM vendas_pdv v
                JOIN caixas c ON c.id = v.caixa_id
                LEFT JOIN atendentes a ON a.id = v.atendente_id
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
            f"""SELECT forma_pagamento, COUNT(*) AS qtd, COALESCE(SUM(total),0) AS total
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
    })


@router.post("/vendas/{id}/cancelar")
async def cancelar_venda(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        v = conn.execute("SELECT caixa_id FROM vendas_pdv WHERE id=%s", (id,)).fetchone()
        conn.execute("UPDATE vendas_pdv SET status='cancelada' WHERE id=%s", (id,))

    caixa_id = v["caixa_id"] if v else 0
    return RedirectResponse(url=f"/caixa/vendas?caixa_id={caixa_id}", status_code=303)


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

    dados = _gerar_bytes_escpos(dict(venda), [dict(i) for i in itens], centro_nome, trabalhador, via == "caixa")
    return Response(content=dados, media_type="application/octet-stream")


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

    recibo = _gerar_recibo(venda, itens, centro_nome, trabalhador, via == "caixa")

    return templates.TemplateResponse("caixa/comprovante.html", {
        "request": request,
        "venda": dict(venda),
        "centro_nome": centro_nome,
        "itens": [dict(i) for i in itens],
        "recibo": recibo,
        "trabalhador": trabalhador,
        "via_caixa": via == "caixa",
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


@router.get("/doacao", response_class=HTMLResponse)
async def form_doacao(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        mov = conn.execute(
            "SELECT id FROM caixa_movimentos WHERE caixa_id=2 AND status='aberto' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return templates.TemplateResponse("caixa/doacao.html", {
        "request": request,
        "atendente": atendente,
        "caixa_aberto": bool(mov),
    })


@router.post("/doacao", response_class=HTMLResponse)
async def registrar_doacao(
    request: Request,
    forma: str = Form("especie"),
    valor: str = Form(""),
    descricao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    try:
        valor_dec = round(float(valor.strip().replace(",", ".")), 2)
    except (ValueError, AttributeError):
        valor_dec = 0.0

    if valor_dec <= 0:
        with conectar() as conn:
            mov = conn.execute(
                "SELECT id FROM caixa_movimentos WHERE caixa_id=2 AND status='aberto' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return templates.TemplateResponse("caixa/doacao.html", {
            "request": request,
            "atendente": atendente,
            "caixa_aberto": bool(mov),
            "erro": "Informe um valor válido maior que zero.",
            "forma_pre": forma,
        })

    desc = descricao.strip() or f"Doação avulsa via {'PIX' if forma == 'pix' else 'Dinheiro'}"
    with conectar() as conn:
        conn.execute(
            """INSERT INTO financeiro_movimentacoes (tipo, categoria, valor, descricao, caixa_id)
               VALUES ('entrada', 'doacao', %s, %s, 2)""",
            (valor_dec, desc)
        )
        mov = conn.execute(
            "SELECT id FROM caixa_movimentos WHERE caixa_id=2 AND status='aberto' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if mov:
            conn.execute(
                "UPDATE caixa_movimentos SET total_vendas = total_vendas + %s WHERE id = %s",
                (valor_dec, mov["id"])
            )

    return RedirectResponse(url=f"/caixa/doacao?ok=1&valor={valor_dec:.2f}&forma={forma}", status_code=303)


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
                          SELECT SUM(v.total - v.fiado_credito_usado)
                          FROM vendas_pdv v
                          WHERE v.fiado_trabalhador_id = t.id
                            AND v.fiado_pago = FALSE
                            AND v.status = 'concluida'
                      ), 0) AS total_pendente
               FROM trabalhadores t
               WHERE norm(t.nome_completo) LIKE %s AND t.ativo = 1
               ORDER BY t.nome_completo LIMIT 10""",
            (termo,)
        ).fetchall()

    hoje = date.today()
    return JSONResponse([
        {
            "id": r["id"],
            "nome": r["nome_completo"],
            "cpf": r["cpf"] or "",
            "fiado_limite": float(r["fiado_limite"]),
            "fiado_credito": float(r["fiado_credito"]),
            "total_pendente": float(r["total_pendente"]),
            "disponivel": max(0, float(r["fiado_limite"]) - float(r["total_pendente"])) if float(r["fiado_limite"]) > 0 else None,
            "fiado_bloqueado": (
                r["fiado_data_encerramento"] is not None
                and r["total_pendente"] > 0
                and date.fromisoformat(r["fiado_data_encerramento"]) < hoje
            ),
        }
        for r in rows
    ])


# ── Fiados / Devedores ─────────────────────────────────────────────────────

@router.get("/fiados", response_class=HTMLResponse)
async def listar_fiados(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
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

        agrupado = defaultdict(lambda: {"vendas": [], "total": 0.0})
        for f in fiados:
            f_dict = dict(f)
            tid = f_dict["fiado_trabalhador_id"]
            agrupado[tid]["trabalhador_nome"] = f_dict["trabalhador_nome"]
            agrupado[tid]["trabalhador_cpf"] = f_dict.get("trabalhador_cpf") or ""
            agrupado[tid]["fiado_limite"] = float(f_dict.get("fiado_limite") or 0)
            agrupado[tid]["fiado_credito"] = float(f_dict.get("fiado_credito") or 0)
            agrupado[tid]["fiado_data_encerramento"] = f_dict.get("fiado_data_encerramento")
            agrupado[tid]["vendas"].append(f_dict)
            agrupado[tid]["total"] += float(f_dict["total"]) - float(f_dict.get("fiado_credito_usado") or 0)

    return templates.TemplateResponse("caixa/fiados.html", {
        "request": request,
        "atendente": atendente,
        "fiados": [dict(f) for f in fiados],
        "agrupado": dict(agrupado),
        "e_admin": e_admin(request),
    })


def _contabilizar_recebimento(conn, valor: float, descricao: str, caixa_id: int = 2):
    """Insere em financeiro_movimentacoes e atualiza o caixa aberto da Lanchonete."""
    conn.execute(
        """INSERT INTO financeiro_movimentacoes (tipo, categoria, valor, descricao, caixa_id)
           VALUES ('entrada', 'recebimento_fiado', %s, %s, %s)""",
        (valor, descricao, caixa_id)
    )
    mov = conn.execute(
        "SELECT id FROM caixa_movimentos WHERE caixa_id=%s AND status='aberto' ORDER BY id DESC LIMIT 1",
        (caixa_id,)
    ).fetchone()
    if mov:
        conn.execute(
            "UPDATE caixa_movimentos SET total_vendas = total_vendas + %s WHERE id = %s",
            (valor, mov["id"])
        )


@router.post("/fiados/{venda_id}/pagar")
async def pagar_fiado(request: Request, venda_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        venda = conn.execute(
            "SELECT total, fiado_credito_usado, fiado_trabalhador_id FROM vendas_pdv WHERE id = %s",
            (venda_id,)
        ).fetchone()

        conn.execute(
            """UPDATE vendas_pdv
               SET fiado_pago = TRUE,
                   fiado_data_pagamento = %s
               WHERE id = %s AND forma_pagamento = 'fiado'""",
            (date.today().isoformat(), venda_id)
        )

        if venda:
            valor_pago = round(float(venda["total"]) - float(venda["fiado_credito_usado"] or 0), 2)
            if valor_pago > 0:
                trab_id = venda["fiado_trabalhador_id"]
                trab = conn.execute(
                    "SELECT nome_completo FROM trabalhadores WHERE id = %s", (trab_id,)
                ).fetchone()
                nome = trab["nome_completo"] if trab else f"#{trab_id}"
                _contabilizar_recebimento(conn, valor_pago, f"Recebimento fiado — venda #{venda_id}")

    return RedirectResponse(url=f"/caixa/fiados?pago_ok={trab_id}&valor={valor_pago:.2f}", status_code=303) if venda else RedirectResponse(url="/caixa/fiados", status_code=303)


@router.post("/fiados/{trabalhador_id}/pagar-divida")
async def pagar_divida_fiado(
    request: Request,
    trabalhador_id: int,
    valor: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        vendas = conn.execute(
            """SELECT id, total, fiado_credito_usado
               FROM vendas_pdv
               WHERE fiado_trabalhador_id = %s AND fiado_pago = FALSE AND status = 'concluida'
               ORDER BY data_venda ASC, id ASC""",
            (trabalhador_id,)
        ).fetchall()

        if not vendas:
            return RedirectResponse(url="/caixa/fiados", status_code=303)

        total_divida = round(sum(
            float(v["total"]) - float(v["fiado_credito_usado"] or 0)
            for v in vendas
        ), 2)

        try:
            valor_dec = round(float(valor.strip().replace(",", ".")), 2)
        except (ValueError, AttributeError):
            valor_dec = total_divida

        if valor_dec <= 0:
            return RedirectResponse(url="/caixa/fiados", status_code=303)

        hoje = date.today().isoformat()

        if valor_dec >= total_divida:
            conn.execute(
                """UPDATE vendas_pdv
                   SET fiado_pago = TRUE, fiado_data_pagamento = %s
                   WHERE fiado_trabalhador_id = %s AND fiado_pago = FALSE AND status = 'concluida'""",
                (hoje, trabalhador_id)
            )
            valor_efetivo = total_divida
        else:
            # FIFO: quita as compras mais antigas primeiro
            restante = valor_dec
            for v in vendas:
                devido = round(float(v["total"]) - float(v["fiado_credito_usado"] or 0), 2)
                if restante >= devido:
                    conn.execute(
                        """UPDATE vendas_pdv
                           SET fiado_pago = TRUE, fiado_data_pagamento = %s
                           WHERE id = %s""",
                        (hoje, v["id"])
                    )
                    restante = round(restante - devido, 2)
                    if restante < 0.01:
                        break
                else:
                    break

            # Sobra que não cobre a próxima compra vai para crédito
            if restante >= 0.01:
                conn.execute(
                    "UPDATE trabalhadores SET fiado_credito = fiado_credito + %s WHERE id = %s",
                    (restante, trabalhador_id)
                )
                conn.execute(
                    """INSERT INTO fiado_credito_mov (trabalhador_id, tipo, valor, descricao)
                       VALUES (%s, 'credito', %s, 'Saldo de pagamento parcial de fiado')""",
                    (trabalhador_id, restante)
                )
            valor_efetivo = valor_dec

        trab = conn.execute(
            "SELECT nome_completo FROM trabalhadores WHERE id = %s", (trabalhador_id,)
        ).fetchone()
        nome = trab["nome_completo"] if trab else f"#{trabalhador_id}"
        _contabilizar_recebimento(conn, valor_efetivo, f"Recebimento fiado — {nome}")

    return RedirectResponse(url=f"/caixa/fiados?pago_ok={trabalhador_id}&valor={valor_efetivo:.2f}", status_code=303)


@router.post("/fiados/{venda_id}/cancelar")
async def cancelar_fiado(request: Request, venda_id: int):
    """Cancela um lançamento de fiado (apenas admin). Restaura crédito usado e marca a venda como cancelada."""
    atendente, redir = _guard(request)
    if redir:
        return redir

    if not e_admin(request):
        return RedirectResponse(url="/caixa/fiados?erro=sem_permissao", status_code=303)

    with conectar() as conn:
        venda = conn.execute(
            """SELECT id, total, fiado_credito_usado, fiado_trabalhador_id,
                      caixa_movimento_id, fiado_pago, fiado_data_pagamento,
                      caixa_id
               FROM vendas_pdv WHERE id = %s AND forma_pagamento = 'fiado' AND status = 'concluida'""",
            (venda_id,)
        ).fetchone()

        if not venda:
            return RedirectResponse(url="/caixa/fiados?erro=nao_encontrada", status_code=303)

        # Restaura crédito antecipado que foi usado nesta compra
        credito = float(venda["fiado_credito_usado"] or 0)
        if credito > 0:
            conn.execute(
                "UPDATE trabalhadores SET fiado_credito = fiado_credito + %s WHERE id = %s",
                (credito, venda["fiado_trabalhador_id"])
            )
            conn.execute(
                """INSERT INTO fiado_credito_mov (trabalhador_id, tipo, valor, descricao)
                   VALUES (%s, 'credito', %s, %s)""",
                (venda["fiado_trabalhador_id"], credito, f"Estorno venda #{venda_id}")
            )

        # Se já foi pago, reverte no financeiro
        if venda["fiado_pago"]:
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, descricao, caixa_id)
                   VALUES ('saida', 'cancelamento_fiado', %s, %s, %s)""",
                (float(venda["total"]), f"Cancelamento venda fiado #{venda_id} — estorno", venda["caixa_id"])
            )

        # Marca a venda como cancelada e limpa flags de fiado
        conn.execute(
            """UPDATE vendas_pdv
               SET status = 'cancelada',
                   fiado_pago = FALSE,
                   fiado_data_pagamento = NULL,
                   observacao = COALESCE(observacao || '; ', '') || %s
               WHERE id = %s""",
            (f"Cancelado por #{atendente['id']} em {date.today().isoformat()}", venda_id)
        )

        # Reverte o total de vendas no movimento de caixa (se ainda aberto)
        if venda["caixa_movimento_id"]:
            conn.execute(
                "UPDATE caixa_movimentos SET total_vendas = total_vendas - %s WHERE id = %s AND status = 'aberto'",
                (float(venda["total"]), venda["caixa_movimento_id"])
            )

    return RedirectResponse(url="/caixa/fiados?cancelado_ok=1", status_code=303)


@router.post("/fiados/credito/{trabalhador_id}")
async def adicionar_credito_fiado(
    request: Request,
    trabalhador_id: int,
    valor: str = Form("0"),
    descricao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    valor_dec = float(valor or 0)
    if valor_dec <= 0:
        return RedirectResponse(url="/caixa/fiados?erro=valor_invalido", status_code=303)

    with conectar() as conn:
        conn.execute(
            "UPDATE trabalhadores SET fiado_credito = fiado_credito + %s WHERE id = %s",
            (valor_dec, trabalhador_id)
        )
        conn.execute(
            """INSERT INTO fiado_credito_mov (trabalhador_id, tipo, valor, descricao)
               VALUES (%s, 'credito', %s, %s)""",
            (trabalhador_id, valor_dec, descricao.strip() or "Adição de crédito")
        )

    return RedirectResponse(url="/caixa/fiados", status_code=303)


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

    with conectar() as conn:
        conn.execute(
            """INSERT INTO caixa_movimentos
               (caixa_id, saldo_inicial, status, atendente_id_abertura)
               VALUES (%s, %s, 'aberto', %s)""",
            (caixa_id, float(saldo_inicial or 0), atendente["id"])
        )

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

    return templates.TemplateResponse("caixa/sangria.html", {
        "request": request,
        "atendente": atendente,
        "movimento": dict(mov),
    })


@router.post("/sangria")
async def salvar_sangria(
    request: Request,
    caixa_movimento_id: int = Form(...),
    valor: str = Form("0"),
    motivo: str = Form(""),
    recebedor_nome: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    valor_dec = float(valor or 0)
    if valor_dec <= 0:
        return RedirectResponse(url=f"/caixa/sangria?caixa_movimento_id={caixa_movimento_id}&erro=valor_invalido", status_code=303)

    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO caixa_sangrias
               (caixa_movimento_id, valor, motivo, atendente_id, recebedor_nome)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING id""",
            (caixa_movimento_id, valor_dec, motivo.strip(), atendente["id"], recebedor_nome.strip())
        )
        sangria_id = cur.fetchone()["id"]

        conn.execute(
            "UPDATE caixa_movimentos SET total_sangrias = total_sangrias + %s WHERE id = %s",
            (valor_dec, caixa_movimento_id)
        )

        mov_data = conn.execute(
            "SELECT caixa_id FROM caixa_movimentos WHERE id=%s", (caixa_movimento_id,)
        ).fetchone()
        if mov_data:
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, descricao, caixa_id)
                   VALUES ('saida', 'sangria', %s, %s, %s)""",
                (valor_dec, motivo.strip() or "Sangria de caixa", mov_data["caixa_id"])
            )

    return RedirectResponse(url=f"/caixa/sangria/{sangria_id}/comprovante", status_code=303)


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
async def relatorio_fechamento(request: Request, caixa_movimento_id: int = Query(0)):
    atendente, redir = _guard(request)
    if redir:
        return redir

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
                      COALESCE(SUM(total), 0) AS total
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

    saldo_esperado = float(mov["saldo_inicial"]) + float(mov["total_vendas"]) - float(mov["total_sangrias"])

    return templates.TemplateResponse("caixa/fechamento.html", {
        "request": request,
        "atendente": atendente,
        "movimento": dict(mov),
        "vendas": [dict(v) for v in vendas],
        "pgto_agrupado": [dict(p) for p in pgto],
        "produtos": [dict(p) for p in produtos],
        "sangrias": [dict(s) for s in sangrias],
        "saldo_esperado": saldo_esperado,
    })


@router.post("/fechar")
async def fechar_caixa(
    request: Request,
    caixa_movimento_id: int = Form(...),
    observacao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        mov = conn.execute(
            "SELECT * FROM caixa_movimentos WHERE id = %s AND status = 'aberto'",
            (caixa_movimento_id,)
        ).fetchone()
        if not mov:
            return RedirectResponse(url="/caixa/pdv", status_code=303)

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

        if float(mov["total_vendas"]) > 0:
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, descricao, caixa_id)
                   VALUES ('entrada', 'venda_pdv', %s, %s, %s)""",
                (float(mov["total_vendas"]),
                 f"Vendas {caixa_nome} — fechamento #{caixa_movimento_id}",
                 mov["caixa_id"])
            )

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
            """SELECT forma_pagamento, COUNT(*) AS qtd, COALESCE(SUM(total),0) AS total
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
    saldo_esp = float(mov["saldo_inicial"]) + float(mov["total_vendas"]) - float(mov["total_sangrias"])
    res_data = [["Descrição","R$"],
                ["Saldo inicial", f"{float(mov['saldo_inicial']):.2f}"],
                ["Total vendas",  f"{float(mov['total_vendas']):.2f}"],
                ["Total sangrias",f"({float(mov['total_sangrias']):.2f})"],
                ["Saldo esperado",f"{saldo_esp:.2f}"]]
    if mov.get("saldo_final") is not None:
        res_data.append(["Saldo final", f"{float(mov['saldo_final']):.2f}"])
    t_res = mini_table(res_data, [W*0.28, W*0.12], bold_last=True)

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

    # Observação
    if mov.get("observacao_fechamento"):
        elems.append(Paragraph(f"<b>Obs:</b> {mov['observacao_fechamento']}", s8))

    doc.build(elems)
    buf.seek(0)

    return Response(
        content=buf.read(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=fechamento_caixa_{mov_id}.pdf"}
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gerar_bytes_escpos(venda: dict, itens: list, centro_nome: str,
                         trabalhador: dict = None, via_caixa: bool = False) -> bytes:
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
    data_str = str(mov.get("data_movimentacao") or "")[:10]
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
                  trabalhador: dict = None, via_caixa: bool = False) -> str:
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
