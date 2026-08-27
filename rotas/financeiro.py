import os
import qrcode
import base64
import io
from datetime import date, datetime
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from banco import conectar, _normalizar, conta_id_por_tipo
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

def _adicionar_meses(ano: int, mes: int, n: int) -> tuple:
    """Retorna (ano, mes) somando n meses ao mês dado."""
    total = ano * 12 + (mes - 1) + n
    return total // 12, total % 12 + 1


def _data_vencimento(dia: int, ano: int, mes: int) -> str:
    """ISO yyyy-mm-dd do vencimento, com fallback para dia 28 em meses curtos."""
    try:
        return date(ano, mes, dia).isoformat()
    except ValueError:
        return date(ano, mes, 28).isoformat()


@router.get("/mensalidades", response_class=HTMLResponse)
async def mensalidades(
    request: Request,
    trabalhador_id: int = 0,
    pago: str = "",
    total: str = "",
    erro: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    hoje = date.today()
    mes_atual = f"{hoje.year}-{hoje.month:02d}"

    trabalhador = None
    pendentes = []
    mes_atual_nao_gerado = False
    meses_existentes = []
    pagos_recentes = []

    with conectar() as conn:
        if trabalhador_id:
            trab = conn.execute(
                """SELECT id, nome_completo, valor_mensalidade, dia_vencimento, ativo
                   FROM trabalhadores WHERE id = %s""",
                (trabalhador_id,),
            ).fetchone()
            if not trab:
                erro = "trabalhador_nao_encontrado"
            else:
                trabalhador = dict(trab)

                exist = conn.execute(
                    """SELECT data_movimentacao FROM financeiro_movimentacoes
                       WHERE trabalhador_id = %s AND categoria = 'mensalidade'""",
                    (trabalhador_id,),
                ).fetchall()
                meses_existentes = sorted({str(r["data_movimentacao"])[:7] for r in exist})
                mes_atual_nao_gerado = mes_atual not in meses_existentes

                pend = conn.execute(
                    """SELECT id, data_movimentacao, valor FROM financeiro_movimentacoes
                       WHERE trabalhador_id = %s AND categoria = 'mensalidade'
                         AND status = 'pendente'
                       ORDER BY data_movimentacao""",
                    (trabalhador_id,),
                ).fetchall()
                hoje_iso = hoje.isoformat()
                for r in pend:
                    venc = str(r["data_movimentacao"])[:10]
                    pendentes.append({
                        "id": r["id"],
                        "mes_ref": venc[:7],
                        "data_vencimento": venc,
                        "valor": float(r["valor"]),
                        "vencido": venc < hoje_iso,
                    })

                pagos = conn.execute(
                    """SELECT id, data_movimentacao, data_pagamento, valor
                       FROM financeiro_movimentacoes
                       WHERE trabalhador_id = %s AND categoria = 'mensalidade'
                         AND status = 'pago'
                       ORDER BY COALESCE(data_pagamento, data_movimentacao) DESC, id DESC
                       LIMIT 10""",
                    (trabalhador_id,),
                ).fetchall()
                for r in pagos:
                    pagos_recentes.append({
                        "id": r["id"],
                        "mes_ref": str(r["data_movimentacao"])[:7],
                        "valor": float(r["valor"]),
                    })

    ano_prox, mes_prox = _adicionar_meses(hoje.year, hoje.month, 1)
    ano_lim, mes_lim = _adicionar_meses(hoje.year, hoje.month, 12)

    return templates.TemplateResponse("financeiro/mensalidades.html", {
        "request": request,
        "atendente": atendente,
        "trabalhador": trabalhador,
        "valor_mensal": float(trabalhador["valor_mensalidade"] or 0) if trabalhador else 0,
        "dia_vencimento": (trabalhador or {}).get("dia_vencimento") or 10,
        "pendentes": pendentes,
        "mes_atual": mes_atual,
        "mes_atual_nao_gerado": mes_atual_nao_gerado,
        "meses_existentes": meses_existentes,
        "pagos_recentes": pagos_recentes,
        "mes_min": f"{ano_prox:04d}-{mes_prox:02d}",
        "mes_max": f"{ano_lim:04d}-{mes_lim:02d}",
        "pago": pago,
        "total": total,
        "erro": erro,
    })


@router.get("/mensalidades/buscar", response_class=JSONResponse)
async def buscar_mensalidade(request: Request, q: str = Query("")):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse([], status_code=401)
    if len(q.strip()) < 2:
        return JSONResponse([])

    with conectar() as conn:
        termo = f"%{_normalizar(q.strip())}%"
        rows = conn.execute(
            """SELECT t.id, t.nome_completo, t.cpf, t.valor_mensalidade, t.dia_vencimento
               FROM trabalhadores t
               WHERE norm(t.nome_completo) LIKE %s
                 AND t.ativo = 1 AND t.valor_mensalidade > 0
               ORDER BY t.nome_completo LIMIT 10""",
            (termo,),
        ).fetchall()

    return JSONResponse([
        {
            "id": r["id"],
            "nome": r["nome_completo"],
            "cpf": r["cpf"] or "",
            "valor_mensalidade": float(r["valor_mensalidade"]),
            "dia_vencimento": r["dia_vencimento"] or 10,
        }
        for r in rows
    ])


@router.post("/mensalidades/receber", response_class=HTMLResponse)
async def receber_mensalidades(
    request: Request,
    trabalhador_id: int = Form(...),
    mov_ids: str = Form(""),
    incluir_mes_atual: str = Form("0"),
    mes_antecipar: str = Form(""),
    forma_pagamento: str = Form("especie"),
    pix_copiadecola: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    if forma_pagamento not in ("especie", "pix"):
        forma_pagamento = "especie"

    hoje = date.today()
    data_pgto = hoje.isoformat()

    def _erro(cod: str):
        return RedirectResponse(
            url=f"/financeiro/mensalidades?trabalhador_id={trabalhador_id}&erro={cod}",
            status_code=303,
        )

    with conectar() as conn:
        trab = conn.execute(
            "SELECT * FROM trabalhadores WHERE id = %s", (trabalhador_id,)
        ).fetchone()
        if not trab:
            return _erro("trabalhador_nao_encontrado")

        # Valida antecipação (formato + limite de 12 meses) antes de qualquer escrita
        mes_antec = ""
        if mes_antecipar.strip():
            try:
                ano_a, mes_a = mes_antecipar.strip().split("-")
                ano_a, mes_a = int(ano_a), int(mes_a)
                if mes_a < 1 or mes_a > 12:
                    raise ValueError
                if (ano_a, mes_a) <= (hoje.year, hoje.month):
                    raise ValueError
                ano_lim, mes_lim = _adicionar_meses(hoje.year, hoje.month, 12)
                if (ano_a, mes_a) > (ano_lim, mes_lim):
                    raise ValueError
                mes_antec = f"{ano_a:04d}-{mes_a:02d}"
            except ValueError:
                return _erro("antecipacao_limite")

        # Inativo: pode quitar pendentes, mas não gerar novos meses
        if not trab["ativo"] and (incluir_mes_atual == "1" or mes_antec):
            return _erro("trabalhador_inativo")

        valor_mensal = float(trab["valor_mensalidade"] or 0)
        dia_venc = trab["dia_vencimento"] or 10

        def _existe_mes(ano_m: int, mes_m: int) -> bool:
            pref = f"{ano_m:04d}-{mes_m:02d}"
            return conn.execute(
                """SELECT COUNT(*) AS c FROM financeiro_movimentacoes
                   WHERE trabalhador_id = %s AND categoria = 'mensalidade'
                     AND data_movimentacao LIKE %s""",
                (trabalhador_id, f"{pref}%"),
            ).fetchone()["c"] > 0

        def _criar_mensalidade(ano_m: int, mes_m: int) -> dict:
            pref = f"{ano_m:04d}-{mes_m:02d}"
            return conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, data_movimentacao, descricao,
                    trabalhador_id, status)
                   VALUES ('entrada', 'mensalidade', %s, %s, %s, %s, 'pendente')
                   RETURNING id, valor""",
                (valor_mensal, _data_vencimento(dia_venc, ano_m, mes_m),
                 f"Mensalidade {trab['nome_completo']} - {pref}", trabalhador_id),
            ).fetchone()

        # 1) Pendentes selecionadas (só as que ainda estão pendentes e do mesmo trabalhador)
        a_pagar = []
        vistos = set()
        for sid in mov_ids.split(","):
            sid = sid.strip()
            if not sid.isdigit():
                continue
            mov = conn.execute(
                """SELECT id, valor FROM financeiro_movimentacoes
                   WHERE id = %s AND categoria = 'mensalidade'
                     AND trabalhador_id = %s AND status = 'pendente'""",
                (int(sid), trabalhador_id),
            ).fetchone()
            if mov and mov["id"] not in vistos:
                vistos.add(mov["id"])
                a_pagar.append(mov)

        # 2) Mês corrente não gerado (pré-marcado na tela)
        if incluir_mes_atual == "1" and not _existe_mes(hoje.year, hoje.month):
            a_pagar.append(_criar_mensalidade(hoje.year, hoje.month))

        # 3) Antecipação: cria cada mês até o alvo, pulando os que já têm mov (qualquer status)
        if mes_antec:
            ano_m, mes_m = hoje.year, hoje.month
            while f"{ano_m:04d}-{mes_m:02d}" < mes_antec:
                ano_m, mes_m = _adicionar_meses(ano_m, mes_m, 1)
                if _existe_mes(ano_m, mes_m):
                    continue
                novo = _criar_mensalidade(ano_m, mes_m)
                if novo and novo["id"] not in vistos:
                    vistos.add(novo["id"])
                    a_pagar.append(novo)

        if not a_pagar:
            return _erro("sem_itens")

        total = round(sum(float(m["valor"]) for m in a_pagar), 2)
        cid = conta_id_por_tipo(conn, 'pix' if forma_pagamento == 'pix' else 'especie')

        ids_pagos = []
        for m in a_pagar:
            conn.execute(
                """UPDATE financeiro_movimentacoes
                   SET status = 'pago', forma_pagamento = %s, conta_id = %s,
                       data_pagamento = %s, pix_copiadecola = %s
                   WHERE id = %s""",
                (forma_pagamento, cid, data_pgto, pix_copiadecola.strip(), m["id"]),
            )
            ids_pagos.append(m["id"])

        # Caixa Lanchonete somado UMA vez (lote inteiro); caixa fechado não falha
        aviso = ""
        mov_caixa = conn.execute(
            "SELECT id FROM caixa_movimentos WHERE caixa_id=2 AND status='aberto' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if mov_caixa:
            conn.execute(
                "UPDATE caixa_movimentos SET total_vendas = total_vendas + %s WHERE id = %s",
                (total, mov_caixa["id"]),
            )
        else:
            aviso = "&aviso=caixa_fechado"

    ids_str = ",".join(str(i) for i in ids_pagos)
    return RedirectResponse(
        url=f"/financeiro/mensalidades?trabalhador_id={trabalhador_id}&pago={ids_str}&total={total:.2f}{aviso}",
        status_code=303,
    )


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
