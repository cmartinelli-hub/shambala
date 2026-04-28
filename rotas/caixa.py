import base64
import io
import json
from datetime import date

import qrcode
from fastapi import APIRouter, Request, Form, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from banco import conectar
from rotas.auth import obter_atendente_logado
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
        if caixa_id:
            caixa_sel = conn.execute(
                "SELECT * FROM caixas WHERE id=%s AND ativo=1", (caixa_id,)
            ).fetchone()

    return templates.TemplateResponse("caixa/pdv.html", {
        "request": request,
        "atendente": atendente,
        "caixas": [dict(c) for c in caixas],
        "caixa_sel": dict(caixa_sel) if caixa_sel else None,
    })


@router.post("/pdv/finalizar")
async def finalizar_venda(
    request: Request,
    caixa_id: int = Form(...),
    forma_pagamento: str = Form(...),
    valor_recebido: str = Form("0"),
    itens_json: str = Form("[]"),
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

    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO vendas_pdv
               (caixa_id, data_venda, total, forma_pagamento, troco, status, atendente_id)
               VALUES (%s, %s, %s, %s, %s, 'concluida', %s)
               RETURNING id""",
            (caixa_id, date.today().isoformat(), total,
             forma_pagamento, troco, atendente["id"])
        )
        venda_id = cur.fetchone()["id"]

        for item in itens:
            conn.execute(
                """INSERT INTO vendas_pdv_itens
                   (venda_id, produto_id, nome_produto, quantidade, preco_unitario, subtotal)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (venda_id, item["produto_id"], item["nome"],
                 item["quantidade"], float(item["preco_unitario"]),
                 float(item["subtotal"]))
            )

    # Sinaliza ao segundo monitor que a venda foi concluída
    import asyncio
    asyncio.create_task(_gerenciador_qr.transmitir({"tipo": "confirmado"}))

    return RedirectResponse(
        url=f"/caixa/pdv?caixa_id={caixa_id}&ok={venda_id}",
        status_code=303
    )


# ── API: gerar QR Code PIX ────────────────────────────────────────────────────

@router.get("/pix-qr", response_class=JSONResponse)
async def gerar_pix_qr(request: Request, valor: str = "0"):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse({}, status_code=401)

    valor_float = float(valor or 0)

    with conectar() as conn:
        row = conn.execute(
            "SELECT valor FROM configuracoes_smtp WHERE chave = 'smtp_pix_chave'"
        ).fetchone()
        chave = row["valor"] if row else ""

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

    return templates.TemplateResponse("caixa/vendas.html", {
        "request": request,
        "atendente": atendente,
        "caixas": [dict(c) for c in caixas],
        "vendas": [dict(v) for v in vendas],
        "caixa_id": caixa_id,
        "data_ref": data_ref,
        "total_dia": total_dia,
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
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            """INSERT INTO produtos (nome, categoria, preco_custo, preco_venda, codigo_barras, ativo)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (nome.strip(), categoria, float(preco_custo or 0),
             float(preco_venda or 0), codigo_barras.strip() or None,
             1 if ativo == "1" else 0)
        )
    return RedirectResponse(url="/caixa/produtos", status_code=303)


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
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            """UPDATE produtos
               SET nome=%s, categoria=%s, preco_custo=%s, preco_venda=%s,
                   codigo_barras=%s, ativo=%s
               WHERE id=%s""",
            (nome.strip(), categoria, float(preco_custo or 0),
             float(preco_venda or 0), codigo_barras.strip() or None,
             1 if ativo == "1" else 0, id)
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


# ── Caixas ────────────────────────────────────────────────────────────────────

@router.get("/caixas", response_class=HTMLResponse)
async def listar_caixas(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        caixas = conn.execute("SELECT * FROM caixas ORDER BY nome").fetchall()

    return templates.TemplateResponse("caixa/caixas.html", {
        "request": request,
        "atendente": atendente,
        "caixas": [dict(c) for c in caixas],
    })


@router.post("/caixas/novo")
async def novo_caixa(
    request: Request,
    nome: str = Form(...),
    descricao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            "INSERT INTO caixas (nome, descricao) VALUES (%s, %s) ON CONFLICT (nome) DO NOTHING",
            (nome.strip(), descricao.strip())
        )
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_produto(p: dict) -> dict:
    return {
        "id": p["id"],
        "nome": p["nome"],
        "preco_venda": float(p["preco_venda"]),
        "categoria": p["categoria"],
        "codigo_barras": p["codigo_barras"],
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


_CATEGORIAS = [
    ("salgado_assado", "Salgado Assado"),
    ("salgado_frito",  "Salgado Frito"),
    ("bebida",         "Bebida"),
    ("doce",           "Doce"),
    ("outro",          "Outro"),
]
