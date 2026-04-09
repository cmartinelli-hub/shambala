import os
import qrcode
import base64
import io
from datetime import date, datetime
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/financeiro")


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


# ── Dashboard / Resumo ────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    mes: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    hoje = date.today()
    mes_ref = mes if mes else f"{hoje.year}-{hoje.month:02d}"

    with conectar() as conn:
        # Resumo do mês
        entradas = conn.execute(
            """SELECT COALESCE(SUM(valor), 0) AS total
               FROM financeiro_movimentacoes
               WHERE tipo = 'entrada' AND data_movimentacao LIKE %s AND status = 'pago'""",
            (f"{mes_ref}%",),
        ).fetchone()["total"]

        saidas = conn.execute(
            """SELECT COALESCE(SUM(valor), 0) AS total
               FROM financeiro_movimentacoes
               WHERE tipo = 'saida' AND data_movimentacao LIKE %s AND status = 'pago'""",
            (f"{mes_ref}%",),
        ).fetchone()["total"]

        pendentes = conn.execute(
            """SELECT COALESCE(SUM(valor), 0) AS total
               FROM financeiro_movimentacoes
               WHERE status = 'pendente' AND data_movimentacao <= %s""",
            (hoje.isoformat(),),
        ).fetchone()["total"]

        # Movimentações recentes
        movs = conn.execute(
            """SELECT fm.*,
                      t.nome_completo AS trabalhador_nome,
                      p.nome_completo AS pessoa_nome
               FROM financeiro_movimentacoes fm
               LEFT JOIN trabalhadores t ON t.id = fm.trabalhador_id
               LEFT JOIN pessoas p ON p.id = fm.pessoa_id
               WHERE fm.data_movimentacao LIKE %s
               ORDER BY fm.data_movimentacao DESC, fm.id DESC
               LIMIT 50""",
            (f"{mes_ref}%",),
        ).fetchall()

    return templates.TemplateResponse("financeiro/dashboard.html", {
        "request": request,
        "atendente": atendente,
        "entradas": float(entradas),
        "saidas": float(saidas),
        "pendentes": float(pendentes),
        "mes_ref": mes_ref,
        "movs": [dict(m) for m in movs],
    })


# ── Nova movimentação ─────────────────────────────────────────────────────────

@router.get("/nova", response_class=HTMLResponse)
async def form_nova(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        trabalhadores = conn.execute(
            "SELECT id, nome_completo FROM trabalhadores WHERE ativo = 1 ORDER BY nome_completo"
        ).fetchall()
        pessoas = conn.execute(
            "SELECT id, nome_completo FROM pessoas ORDER BY nome_completo LIMIT 200"
        ).fetchall()

    return templates.TemplateResponse("financeiro/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": None,
        "trabalhadores": [dict(t) for t in trabalhadores],
        "pessoas": [dict(p) for p in pessoas],
    })


@router.post("/nova", response_class=HTMLResponse)
async def salvar_nova(
    request: Request,
    tipo: str = Form(...),
    categoria: str = Form(...),
    valor: str = Form("0"),
    data_movimentacao: str = Form(""),
    descricao: str = Form(""),
    trabalhador_id: str = Form(""),
    pessoa_id: str = Form(""),
    status: str = Form("pago"),
    pix_copiadecola: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    data_ref = data_movimentacao.strip() or date.today().isoformat()

    with conectar() as conn:
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao,
                trabalhador_id, pessoa_id, status, pix_copiadecola)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tipo, categoria, float(valor or 0), data_ref, descricao.strip(),
             int(trabalhador_id) if trabalhador_id else None,
             int(pessoa_id) if pessoa_id else None,
             status, pix_copiadecola.strip()),
        )

    return RedirectResponse(url="/financeiro", status_code=303)


# ── Mensalidades ──────────────────────────────────────────────────────────────

@router.get("/mensalidades", response_class=HTMLResponse)
async def mensalidades(
    request: Request,
    mes: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    hoje = date.today()
    mes_ref = mes if mes else f"{hoje.year}-{hoje.month:02d}"
    ano, mes_num = mes_ref.split("-")
    ano, mes_num = int(ano), int(mes_num)

    with conectar() as conn:
        rows = conn.execute(
            """SELECT t.*,
                      fm.id AS mov_id, fm.status AS mov_status,
                      fm.data_movimentacao AS mov_data
               FROM trabalhadores t
               LEFT JOIN financeiro_movimentacoes fm
                 ON fm.trabalhador_id = t.id
                 AND fm.categoria = 'mensalidade'
                 AND fm.data_movimentacao LIKE %s
               WHERE t.ativo = 1 AND t.valor_mensalidade > 0
               ORDER BY t.nome_completo""",
            (f"{mes_ref}%",),
        ).fetchall()

    lista = []
    for t in rows:
        dia_venc = t["dia_vencimento"] or 10
        try:
            data_venc = date(ano, mes_num, dia_venc)
        except ValueError:
            data_venc = date(ano, mes_num, 28)

        lista.append({
            "trabalhador": t,
            "valor": float(t["valor_mensalidade"]),
            "dia_vencimento": dia_venc,
            "data_vencimento": data_venc,
            "mov_id": t["mov_id"],
            "mov_status": t["mov_status"],
            "vencido": data_venc < hoje and t["mov_status"] != "pago",
        })

    return templates.TemplateResponse("financeiro/mensalidades.html", {
        "request": request,
        "atendente": atendente,
        "lista": lista,
        "mes_ref": mes_ref,
    })


@router.post("/mensalidades/gerar", response_class=HTMLResponse)
async def gerar_mensalidades(
    request: Request,
    trabalhador_id: int = Form(...),
    mes_ref: str = Form(...),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    ano_val, mes_n = mes_ref.split("-")
    ano_val, mes_n = int(ano_val), int(mes_n)

    with conectar() as conn:
        t = conn.execute(
            "SELECT * FROM trabalhadores WHERE id = %s", (trabalhador_id,)
        ).fetchone()
        if not t:
            return RedirectResponse(url="/financeiro/mensalidades", status_code=303)

        dia = t["dia_vencimento"] or 10
        try:
            data_venc = date(ano_val, mes_n, dia)
        except ValueError:
            data_venc = date(ano_val, mes_n, 28)

        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao,
                trabalhador_id, status)
               VALUES ('entrada', 'mensalidade', %s, %s, %s, %s, 'pendente')""",
            (
                float(t["valor_mensalidade"]),
                data_venc.isoformat(),
                f"Mensalidade {t['nome_completo']} - {mes_ref}",
                trabalhador_id,
            ),
        )

    return RedirectResponse(url="/financeiro/mensalidades", status_code=303)


@router.post("/mensalidades/{mov_id}/baixar", response_class=HTMLResponse)
async def baixar_mensalidade(
    request: Request,
    mov_id: int,
    pix: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            """UPDATE financeiro_movimentacoes SET status = 'pago', pix_copiadecola = %s WHERE id = %s""",
            (pix.strip(), mov_id),
        )

    return RedirectResponse(url="/financeiro/mensalidades", status_code=303)


# ── PIX Estático ──────────────────────────────────────────────────────────────

@router.get("/pix", response_class=HTMLResponse)
async def gerar_pix(
    request: Request,
    valor: str = "0",
    descricao: str = "",
    chave: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    valor_float = float(valor or 0)

    # Se não tem chave, busca do banco
    if not chave:
        with conectar() as conn:
            row = conn.execute(
                "SELECT valor FROM configuracoes_smtp WHERE chave = 'smtp_pix_chave'"
            ).fetchone()
            chave = row["valor"] if row else ""

    pix_code = gerar_payload_pix(chave, valor_float, descricao)

    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(pix_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_base64 = base64.b64encode(buf.getvalue()).decode()

    return templates.TemplateResponse("financeiro/pix.html", {
        "request": request,
        "atendente": atendente,
        "pix_code": pix_code,
        "qr_base64": qr_base64,
        "valor": valor_float,
        "descricao": descricao,
        "chave": chave,
    })


def _tlv(tag: str, valor: str) -> str:
    """Codifica um campo no formato Tag-Size-Value."""
    size = f"{len(valor):02d}"
    return f"{tag}{size}{valor}"


def gerar_payload_pix(chave: str, valor: float = 0, descricao: str = "") -> str:
    """Gera payload do PIX estático no padrão EMV (BR Code)."""
    # Merchant Account Information
    merchant_info = _tlv("00", "br.gov.bcb.pix") + _tlv("01", chave)
    payload = _tlv("00", "01")
    payload += _tlv("26", merchant_info)
    payload += _tlv("52", "0000")  # Merchant Category Code
    payload += _tlv("53", "986")   # Currency (986 = BRL)

    if valor > 0:
        payload += _tlv("54", f"{valor:.2f}")

    payload += _tlv("58", "BR")
    payload += _tlv("59", descricao[:25] if descricao else "Shambala")
    payload += _tlv("60", "Volta Redonda")

    # Additional Data Field Template
    payload += _tlv("62", _tlv("05", "***"))

    # CRC placeholder
    payload += "6304"

    # CRC16-CCITT
    crc = _crc16(payload)
    return payload + crc


def _crc16(data: str) -> str:
    """Calcula CRC16-CCITT."""
    crc = 0xFFFF
    for byte in data.encode():
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


# ── Histórico por pessoa ──────────────────────────────────────────────────────

@router.get("/pessoa/{pessoa_id}", response_class=HTMLResponse)
async def historico_pessoa(request: Request, pessoa_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        pessoa = conn.execute(
            "SELECT id, nome_completo, email FROM pessoas WHERE id = %s", (pessoa_id,)
        ).fetchone()
        if not pessoa:
            return RedirectResponse(url="/financeiro", status_code=303)

        movs = conn.execute(
            """SELECT fm.*
               FROM financeiro_movimentacoes fm
               WHERE fm.pessoa_id = %s
               ORDER BY fm.data_movimentacao DESC, fm.id DESC""",
            (pessoa_id,),
        ).fetchall()

    return templates.TemplateResponse("financeiro/pessoa.html", {
        "request": request,
        "atendente": atendente,
        "pessoa": dict(pessoa),
        "movs": [dict(m) for m in movs],
    })


# ── Histórico por trabalhador ─────────────────────────────────────────────────

@router.get("/trabalhador/{trabalhador_id}", response_class=HTMLResponse)
async def historico_trabalhador(request: Request, trabalhador_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        trabalhador = conn.execute(
            "SELECT id, nome_completo, valor_mensalidade, dia_vencimento FROM trabalhadores WHERE id = %s",
            (trabalhador_id,),
        ).fetchone()
        if not trabalhador:
            return RedirectResponse(url="/financeiro", status_code=303)

        movs = conn.execute(
            """SELECT fm.*
               FROM financeiro_movimentacoes fm
               WHERE fm.trabalhador_id = %s
               ORDER BY fm.data_movimentacao DESC, fm.id DESC""",
            (trabalhador_id,),
        ).fetchall()

    return templates.TemplateResponse("financeiro/trabalhador.html", {
        "request": request,
        "atendente": atendente,
        "trabalhador": dict(trabalhador),
        "movs": [dict(m) for m in movs],
    })


# ── Relatórios ────────────────────────────────────────────────────────────────

@router.get("/relatorios", response_class=HTMLResponse)
async def relatorios_financeiro(
    request: Request,
    data_inicio: str = "",
    data_fim: str = "",
    tipo: str = "",
    categoria: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    filtros = []
    params = []

    if data_inicio:
        filtros.append("data_movimentacao >= %s")
        params.append(data_inicio)

    if data_fim:
        filtros.append("data_movimentacao <= %s")
        params.append(data_fim)

    if tipo:
        filtros.append("tipo = %s")
        params.append(tipo)

    if categoria:
        filtros.append("categoria = %s")
        params.append(categoria)

    where = ""
    if filtros:
        where = "WHERE " + " AND ".join(filtros)

    with conectar() as conn:
        total_entradas = conn.execute(
            f"SELECT COALESCE(SUM(CASE WHEN tipo='entrada' THEN valor ELSE 0 END), 0) AS total FROM financeiro_movimentacoes {where}",
            params,
        ).fetchone()["total"]

        total_saidas = conn.execute(
            f"SELECT COALESCE(SUM(CASE WHEN tipo='saida' THEN valor ELSE 0 END), 0) AS total FROM financeiro_movimentacoes {where}",
            params,
        ).fetchone()["total"]

        movs = conn.execute(
            f"""SELECT fm.*,
                       t.nome_completo AS trabalhador_nome,
                       p.nome_completo AS pessoa_nome
                FROM financeiro_movimentacoes fm
                LEFT JOIN trabalhadores t ON t.id = fm.trabalhador_id
                LEFT JOIN pessoas p ON p.id = fm.pessoa_id
                {where}
                ORDER BY fm.data_movimentacao DESC, fm.id DESC
                LIMIT 200""",
            params,
        ).fetchall()

    return templates.TemplateResponse("financeiro/relatorios.html", {
        "request": request,
        "atendente": atendente,
        "total_entradas": float(total_entradas),
        "total_saidas": float(total_saidas),
        "movs": [dict(m) for m in movs],
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "tipo": tipo,
        "categoria": categoria,
    })
