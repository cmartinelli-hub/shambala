import os
import qrcode
import base64
import io
from datetime import date, datetime
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar, conta_id_por_tipo
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

        # Saldo por conta
        saldos_contas = conn.execute(
            """SELECT c.id, c.nome, c.tipo,
                      COALESCE(SUM(CASE WHEN fm.tipo = 'entrada' THEN fm.valor ELSE 0 END), 0) AS total_entradas,
                      COALESCE(SUM(CASE WHEN fm.tipo = 'saida' THEN fm.valor ELSE 0 END), 0) AS total_saidas
               FROM contas_financeiras c
               LEFT JOIN financeiro_movimentacoes fm ON fm.conta_id = c.id
                   AND fm.data_movimentacao LIKE %s AND fm.status = 'pago'
               WHERE c.ativo = 1
               GROUP BY c.id, c.nome, c.tipo
               ORDER BY c.id""",
            (f"{mes_ref}%",),
        ).fetchall()

        # Movimentações recentes
        movs = conn.execute(
            """SELECT fm.*,
                      t.nome_completo AS trabalhador_nome,
                      p.nome_completo AS pessoa_nome,
                      c.nome AS conta_nome
               FROM financeiro_movimentacoes fm
               LEFT JOIN trabalhadores t ON t.id = fm.trabalhador_id
               LEFT JOIN pessoas p ON p.id = fm.pessoa_id
               LEFT JOIN contas_financeiras c ON c.id = fm.conta_id
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
        "saldos_contas": [dict(sc) for sc in saldos_contas],
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
        contas = conn.execute(
            "SELECT id, nome FROM contas_financeiras WHERE ativo = 1 ORDER BY id"
        ).fetchall()

    return templates.TemplateResponse("financeiro/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": None,
        "trabalhadores": [dict(t) for t in trabalhadores],
        "contas": [dict(c) for c in contas],
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
    conta_id: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    data_ref = data_movimentacao.strip() or date.today().isoformat()

    with conectar() as conn:
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao,
                trabalhador_id, pessoa_id, status, pix_copiadecola, conta_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tipo, categoria, float(valor or 0), data_ref, descricao.strip(),
             int(trabalhador_id) if trabalhador_id else None,
             int(pessoa_id) if pessoa_id else None,
             status, pix_copiadecola.strip(),
             int(conta_id) if conta_id else None),
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
            "data_vencimento": data_venc.isoformat(),
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
    forma_pagamento: str = Form("especie"),
    pix: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        cid = conta_id_por_tipo(conn, 'pix' if forma_pagamento == 'pix' else 'especie')
        conn.execute(
            """UPDATE financeiro_movimentacoes SET status = 'pago', pix_copiadecola = %s,
               forma_pagamento = %s, conta_id = %s WHERE id = %s""",
            (pix.strip(), forma_pagamento, cid, mov_id),
        )
        row = conn.execute(
            "SELECT data_movimentacao, valor FROM financeiro_movimentacoes WHERE id = %s", (mov_id,)
        ).fetchone()
        mes_str = str(row["data_movimentacao"])[:7] if row else ""

        # Atualiza o caixa da Lanchonete (id=2) com o valor da mensalidade
        if row and float(row["valor"]) > 0:
            mov_caixa = conn.execute(
                "SELECT id FROM caixa_movimentos WHERE caixa_id=2 AND status='aberto' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if mov_caixa:
                conn.execute(
                    "UPDATE caixa_movimentos SET total_vendas = total_vendas + %s WHERE id = %s",
                    (float(row["valor"]), mov_caixa["id"])
                )

    mes_param = f"&mes={mes_str}" if mes_str else ""
    return RedirectResponse(url=f"/financeiro/mensalidades?pago={mov_id}{mes_param}", status_code=303)


@router.get("/mensalidades/{mov_id}/pix", response_class=HTMLResponse)
async def pix_mensalidade(request: Request, mov_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    import qrcode as qrlib
    import base64, io

    with conectar() as conn:
        mov = conn.execute(
            """SELECT fm.*, t.nome_completo
               FROM financeiro_movimentacoes fm
               JOIN trabalhadores t ON t.id = fm.trabalhador_id
               WHERE fm.id = %s AND fm.categoria = 'mensalidade'""",
            (mov_id,)
        ).fetchone()
        if not mov:
            return RedirectResponse(url="/financeiro/mensalidades", status_code=303)

        # Busca chave PIX da Lanchonete (caixa_id=2) ou a primeira ativa
        row = conn.execute(
            """SELECT p.chave, p.tipo FROM caixas c
               JOIN chaves_pix p ON p.id = c.chave_pix_id
               WHERE c.id = 2 AND p.ativa = TRUE"""
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT chave, tipo FROM chaves_pix WHERE ativa = TRUE LIMIT 1"
            ).fetchone()

    if not row:
        return HTMLResponse("<p>Nenhuma chave PIX ativa configurada.</p>", status_code=400)

    from rotas.caixa import _normalizar_chave_pix, _gerar_payload_pix
    chave = _normalizar_chave_pix(row["chave"], row["tipo"])
    valor = float(mov["valor"])
    pix_code = _gerar_payload_pix(chave, valor, f"Mensalidade {mov['nome_completo']}")

    qr = qrlib.QRCode(version=1, box_size=10, border=2)
    qr.add_data(pix_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return templates.TemplateResponse("financeiro/pix.html", {
        "request": request,
        "atendente": atendente,
        "pix_code": pix_code,
        "qr_base64": qr_b64,
        "valor": valor,
        "descricao": f"Mensalidade {mov['nome_completo']}",
        "chave": chave,
        "mov_id": mov_id,
    })


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
    conta_id: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    filtros = []
    params = []

    if data_inicio:
        filtros.append("fm.data_movimentacao >= %s")
        params.append(data_inicio)

    if data_fim:
        filtros.append("fm.data_movimentacao <= %s")
        params.append(data_fim)

    if tipo:
        filtros.append("fm.tipo = %s")
        params.append(tipo)

    if categoria:
        filtros.append("fm.categoria = %s")
        params.append(categoria)

    if conta_id:
        filtros.append("fm.conta_id = %s")
        params.append(int(conta_id))

    where = ""
    if filtros:
        where = "WHERE " + " AND ".join(filtros)

    with conectar() as conn:
        total_entradas = conn.execute(
            f"SELECT COALESCE(SUM(CASE WHEN fm.tipo='entrada' THEN fm.valor ELSE 0 END), 0) AS total FROM financeiro_movimentacoes fm {where}",
            params,
        ).fetchone()["total"]

        total_saidas = conn.execute(
            f"SELECT COALESCE(SUM(CASE WHEN fm.tipo='saida' THEN fm.valor ELSE 0 END), 0) AS total FROM financeiro_movimentacoes fm {where}",
            params,
        ).fetchone()["total"]

        movs = conn.execute(
            f"""SELECT fm.*,
                       t.nome_completo AS trabalhador_nome,
                       p.nome_completo AS pessoa_nome,
                       c.nome AS conta_nome
                FROM financeiro_movimentacoes fm
                LEFT JOIN trabalhadores t ON t.id = fm.trabalhador_id
                LEFT JOIN pessoas p ON p.id = fm.pessoa_id
                LEFT JOIN contas_financeiras c ON c.id = fm.conta_id
                {where}
                ORDER BY fm.data_movimentacao DESC, fm.id DESC
                LIMIT 200""",
            params,
        ).fetchall()

        contas = conn.execute(
            "SELECT id, nome FROM contas_financeiras WHERE ativo = 1 ORDER BY id"
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
        "conta_id": conta_id,
        "contas": [dict(c) for c in contas],
    })


# ── Relatório de Fiados por Trabalhador ─────────────────────────────────────

@router.get("/fiados", response_class=HTMLResponse)
async def relatorio_fiados(request: Request, trabalhador_id: int = 0):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        # Lista todos os trabalhadores com fiado
        trabalhadores = conn.execute(
            """SELECT id, nome_completo, cpf,
                      fiado_limite, fiado_credito, fiado_data_encerramento
               FROM trabalhadores
               WHERE ativo = 1
               ORDER BY nome_completo"""
        ).fetchall()

        vendas = []
        trab_sel = None
        total_gasto = 0.0
        total_pago = 0.0
        total_pendente = 0.0

        if trabalhador_id:
            trab_sel = conn.execute(
                """SELECT id, nome_completo, cpf, fiado_limite,
                          fiado_credito, fiado_data_encerramento
                   FROM trabalhadores WHERE id = %s""",
                (trabalhador_id,)
            ).fetchone()

            if trab_sel:
                vendas = conn.execute(
                    """SELECT v.*, c.nome AS caixa_nome
                       FROM vendas_pdv v
                       JOIN caixas c ON c.id = v.caixa_id
                       WHERE v.fiado_trabalhador_id = %s
                         AND v.forma_pagamento = 'fiado'
                       ORDER BY v.data_venda DESC, v.id DESC""",
                    (trabalhador_id,)
                ).fetchall()

                for v in vendas:
                    v_total = float(v["total"])
                    total_gasto += v_total
                    if v["fiado_pago"]:
                        total_pago += v_total
                    else:
                        total_pendente += v_total

    return templates.TemplateResponse("financeiro/fiado_trabalhador.html", {
        "request": request,
        "atendente": atendente,
        "trabalhadores": [dict(t) for t in trabalhadores],
        "trabalhador_sel": dict(trab_sel) if trab_sel else None,
        "vendas": [dict(v) for v in vendas],
        "total_gasto": total_gasto,
        "total_pago": total_pago,
        "total_pendente": total_pendente,
    })


# ── Transferência entre Contas ────────────────────────────────────────────

@router.get("/transferir", response_class=HTMLResponse)
async def form_transferir(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        contas = conn.execute(
            "SELECT id, nome FROM contas_financeiras WHERE ativo = 1 ORDER BY id"
        ).fetchall()

    return templates.TemplateResponse("financeiro/transferir.html", {
        "request": request,
        "atendente": atendente,
        "contas": [dict(c) for c in contas],
    })


@router.post("/transferir", response_class=HTMLResponse)
async def salvar_transferencia(
    request: Request,
    conta_origem: int = Form(...),
    conta_destino: int = Form(...),
    valor: str = Form("0"),
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
        return RedirectResponse(url="/financeiro/transferir?erro=valor_invalido", status_code=303)

    if conta_origem == conta_destino:
        return RedirectResponse(url="/financeiro/transferir?erro=contas_iguais", status_code=303)

    desc = descricao.strip() or "Transferência entre contas"
    hoje = date.today().isoformat()

    with conectar() as conn:
        # Saída da origem
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, conta_id, status)
               VALUES ('saida', 'transferencia', %s, %s, %s, %s, 'pago')""",
            (valor_dec, hoje, f"{desc} (origem)", conta_origem)
        )
        # Entrada no destino
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, conta_id, status)
               VALUES ('entrada', 'transferencia', %s, %s, %s, %s, 'pago')""",
            (valor_dec, hoje, f"{desc} (destino)", conta_destino)
        )

    return RedirectResponse(url="/financeiro?transferencia_ok=1", status_code=303)
