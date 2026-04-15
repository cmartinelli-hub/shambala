from datetime import date
from fastapi import APIRouter, Request, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import os
import uuid
from banco import conectar, gerar_agendamentos_plano
from rotas.auth import obter_atendente_logado

from templates_config import templates
router = APIRouter(prefix="/cadastros/mediuns")

# Configuração de upload de fotos de médiuns
FOTOS_MEDIUM_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fotos")
os.makedirs(FOTOS_MEDIUM_DIR, exist_ok=True)
EXTENSOES_MEDIUM = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TAMANHO_MAXIMO_MEDIUM = 5 * 1024 * 1024  # 5MB


def _salvar_foto_medium(file: UploadFile, medium_id: int, foto_existente: str = None) -> str:
    """Salva foto do médium e retorna o caminho relativo."""
    if foto_existente and os.path.exists(os.path.join(FOTOS_MEDIUM_DIR, foto_existente)):
        try:
            os.remove(os.path.join(FOTOS_MEDIUM_DIR, foto_existente))
        except OSError:
            pass
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in EXTENSOES_MEDIUM:
        ext = ".jpg"
    nome_arquivo = f"medium_{medium_id}_{uuid.uuid4().hex[:8]}{ext}"
    caminho = os.path.join(FOTOS_MEDIUM_DIR, nome_arquivo)
    with open(caminho, "wb") as f:
        conteudo = file.file.read()
        if len(conteudo) > TAMANHO_MAXIMO_MEDIUM:
            raise ValueError("Arquivo muito grande (máx. 5MB)")
        f.write(conteudo)
    return nome_arquivo


# Placeholder SVG para médiuns
@router.get("/foto-placeholder/{inicial}")
async def foto_placeholder_medium(inicial: str):
    """Gera placeholder SVG com a inicial do médium (roxo)."""
    import html as _html
    char = _html.escape(inicial[0].upper()) if inicial else "?"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
        <defs>
            <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" style="stop-color:#8b5cf6"/>
                <stop offset="100%" style="stop-color:#a78bfa"/>
            </linearGradient>
        </defs>
        <circle cx="100" cy="100" r="100" fill="url(#g)"/>
        <text x="100" y="115" text-anchor="middle" fill="white"
              font-family="system-ui,sans-serif" font-size="90"
              font-weight="bold">{char}</text>
    </svg>'''
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


# ── Lista ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def listar(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        rows = conn.execute(
            "SELECT id, nome_completo, telefone, email, ativo, vagas_dia FROM mediuns ORDER BY nome_completo"
        ).fetchall()
    return templates.TemplateResponse("mediuns/lista.html", {
        "request": request,
        "atendente": atendente,
        "mediuns": [dict(r) for r in rows],
    })


# ── Novo ──────────────────────────────────────────────────────────────────────

@router.get("/novo", response_class=HTMLResponse)
async def form_novo(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("mediuns/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": None,
        "erro": None,
    })


@router.post("/novo", response_class=HTMLResponse)
async def salvar_novo(
    request: Request,
    nome_completo: str = Form(...),
    vagas_dia: int = Form(10),
    telefone: str = Form(""),
    email: str = Form(""),
    cep: str = Form(""),
    logradouro: str = Form(""),
    numero: str = Form(""),
    complemento: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    uf: str = Form(""),
    foto: UploadFile = None,
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO mediuns
               (nome_completo, vagas_dia, telefone, email, cep, logradouro, numero, complemento, bairro, cidade, uf)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (nome_completo.strip(), vagas_dia,
             telefone.strip(), email.strip().lower(),
             cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
             bairro.strip(), cidade.strip(), uf.strip()),
        )
        medium_id = cur.fetchone()["id"]

        # Salvar foto se enviada
        if foto and getattr(foto, "filename", None):
            try:
                foto_medium = _salvar_foto_medium(foto, medium_id)
                conn.execute(
                    "UPDATE mediuns SET foto_medium = %s WHERE id = %s",
                    (foto_medium, medium_id)
                )
            except ValueError:
                pass
    return RedirectResponse(url="/cadastros/mediuns", status_code=303)


# ── Editar ────────────────────────────────────────────────────────────────────

@router.get("/{id}/editar", response_class=HTMLResponse)
async def form_editar(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute("SELECT * FROM mediuns WHERE id = %s", (id,)).fetchone()
    if not row:
        return RedirectResponse(url="/cadastros/mediuns", status_code=303)
    return templates.TemplateResponse("mediuns/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": dict(row),
        "erro": None,
    })


@router.post("/{id}/editar", response_class=HTMLResponse)
async def salvar_editar(
    request: Request,
    id: int,
    nome_completo: str = Form(...),
    vagas_dia: int = Form(10),
    telefone: str = Form(""),
    email: str = Form(""),
    cep: str = Form(""),
    logradouro: str = Form(""),
    numero: str = Form(""),
    complemento: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    uf: str = Form(""),
    remover_foto: str = Form(""),
    foto: UploadFile = None,
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        # Buscar foto existente
        existente = conn.execute(
            "SELECT foto_medium FROM mediuns WHERE id = %s", (id,)
        ).fetchone()
        foto_existente = existente["foto_medium"] if existente else None

        # Remover foto se solicitado
        if remover_foto == "1" and foto_existente:
            if os.path.exists(os.path.join(FOTOS_MEDIUM_DIR, foto_existente)):
                try:
                    os.remove(os.path.join(FOTOS_MEDIUM_DIR, foto_existente))
                except OSError:
                    pass
            foto_existente = None

        # Salvar nova foto se enviada
        if foto and getattr(foto, "filename", None):
            try:
                foto_existente = _salvar_foto_medium(foto, id, foto_existente)
            except ValueError:
                pass

        conn.execute(
            """UPDATE mediuns SET nome_completo=%s, vagas_dia=%s, telefone=%s, email=%s,
               cep=%s, logradouro=%s, numero=%s, complemento=%s, bairro=%s, cidade=%s, uf=%s,
               foto_medium=%s
               WHERE id=%s""",
            (nome_completo.strip(), vagas_dia,
             telefone.strip(), email.strip().lower(),
             cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
             bairro.strip(), cidade.strip(), uf.strip(),
             foto_existente, id),
        )
    return RedirectResponse(url="/cadastros/mediuns", status_code=303)


# ── Planos de tratamento ──────────────────────────────────────────────────────

@router.get("/{id}/planos", response_class=HTMLResponse)
async def listar_planos(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        medium = conn.execute("SELECT * FROM mediuns WHERE id = %s", (id,)).fetchone()
        if not medium:
            return RedirectResponse(url="/cadastros/mediuns", status_code=303)

        planos = conn.execute(
            """SELECT pt.id, pt.sessoes_total, pt.sessoes_realizadas,
                      pt.data_inicio, pt.status, pt.frequencia, pt.sessoes_com_passe
               FROM planos_tratamento pt
               WHERE pt.medium_id = %s
               ORDER BY pt.status, pt.data_inicio DESC""",
            (id,)
        ).fetchall()

        planos_com_pessoas = []
        for p in planos:
            pessoas = conn.execute(
                """SELECT pe.id, pe.nome_completo
                   FROM plano_pessoas pp JOIN pessoas pe ON pe.id = pp.pessoa_id
                   WHERE pp.plano_id = %s""",
                (p["id"],)
            ).fetchall()
            # Próximo agendamento
            prox = conn.execute(
                "SELECT data FROM agendamentos WHERE plano_id = %s AND status='agendado' ORDER BY data LIMIT 1",
                (p["id"],)
            ).fetchone()
            planos_com_pessoas.append({
                **dict(p),
                "pessoas": [dict(x) for x in pessoas],
                "proxima_sessao": prox["data"] if prox else None,
            })

        todas_pessoas = conn.execute(
            "SELECT id, nome_completo FROM pessoas ORDER BY nome_completo"
        ).fetchall()

    return templates.TemplateResponse("mediuns/planos.html", {
        "request": request,
        "atendente": atendente,
        "medium": dict(medium),
        "planos": planos_com_pessoas,
        "todas_pessoas": [dict(p) for p in todas_pessoas],
        "hoje": date.today().isoformat(),
    })


@router.post("/{id}/planos/novo")
async def novo_plano(
    request: Request,
    id: int,
    sessoes_total: int = Form(...),
    frequencia: str = Form("semanal"),
    sessoes_com_passe: int = Form(3),
    data_inicio: str = Form(""),
    pessoas_ids: list[int] = Form(...),
    next: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    from datetime import date as _date
    inicio = _date.fromisoformat(data_inicio) if data_inicio else _date.today()
    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO planos_tratamento
               (medium_id, sessoes_total, data_inicio, frequencia, sessoes_com_passe, status)
               VALUES (%s, %s, %s, %s, %s, 'ativo')
               RETURNING id""",
            (id, sessoes_total, inicio.isoformat(), frequencia, sessoes_com_passe)
        )
        plano_id = cur.fetchone()["id"]
        for pessoa_id in pessoas_ids:
            conn.execute(
                """INSERT INTO plano_pessoas (plano_id, pessoa_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (plano_id, pessoa_id)
            )
        gerar_agendamentos_plano(conn, plano_id, inicio, frequencia, sessoes_total, sessoes_com_passe)
    destino = next.strip() or f"/cadastros/mediuns/{id}/planos"
    return RedirectResponse(url=destino, status_code=303)


@router.post("/{id}/planos/{plano_id}/alta")
async def dar_alta(request: Request, id: int, plano_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "UPDATE planos_tratamento SET status='alta', concluido=1 WHERE id=%s AND medium_id=%s",
            (plano_id, id)
        )
        conn.execute(
            "UPDATE agendamentos SET status='cancelado' WHERE plano_id=%s AND status='agendado'",
            (plano_id,)
        )
    return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos", status_code=303)


@router.post("/{id}/planos/{plano_id}/cancelar")
async def cancelar_plano(request: Request, id: int, plano_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "UPDATE planos_tratamento SET status='cancelado' WHERE id=%s AND medium_id=%s",
            (plano_id, id)
        )
        conn.execute(
            "UPDATE agendamentos SET status='cancelado' WHERE plano_id=%s AND status='agendado'",
            (plano_id,)
        )
    return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos", status_code=303)


# ── Agendamentos do plano ─────────────────────────────────────────────────────

@router.get("/{id}/planos/{plano_id}/agenda", response_class=HTMLResponse)
async def ver_agenda(request: Request, id: int, plano_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        medium = conn.execute("SELECT id, nome_completo FROM mediuns WHERE id=%s", (id,)).fetchone()
        plano = conn.execute(
            """SELECT pt.*, STRING_AGG(pe.nome_completo, ', ') as nomes_pessoas
               FROM planos_tratamento pt
               LEFT JOIN plano_pessoas pp ON pp.plano_id = pt.id
               LEFT JOIN pessoas pe ON pe.id = pp.pessoa_id
               WHERE pt.id=%s AND pt.medium_id=%s
               GROUP BY pt.id""",
            (plano_id, id)
        ).fetchone()
        if not plano or not medium:
            return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos", status_code=303)
        agendamentos = conn.execute(
            "SELECT * FROM agendamentos WHERE plano_id=%s ORDER BY data",
            (plano_id,)
        ).fetchall()
    return templates.TemplateResponse("mediuns/agenda.html", {
        "request": request,
        "atendente": atendente,
        "medium": dict(medium),
        "plano": dict(plano),
        "agendamentos": [dict(a) for a in agendamentos],
        "hoje": date.today().isoformat(),
    })


@router.post("/{id}/planos/{plano_id}/agenda/novo")
async def novo_agendamento(
    request: Request,
    id: int,
    plano_id: int,
    data: str = Form(...),
    requer_passe: int = Form(0),
    encaixe: int = Form(0),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "INSERT INTO agendamentos (plano_id, data, status, requer_passe, encaixe) VALUES (%s,%s,%s,%s,%s)",
            (plano_id, data, "agendado", requer_passe, encaixe)
        )
    return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos/{plano_id}/agenda", status_code=303)


@router.post("/{id}/planos/{plano_id}/agenda/{ag_id}/cancelar")
async def cancelar_agendamento(request: Request, id: int, plano_id: int, ag_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "UPDATE agendamentos SET status='cancelado' WHERE id=%s AND plano_id=%s",
            (ag_id, plano_id)
        )
    return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos/{plano_id}/agenda", status_code=303)


@router.post("/{id}/planos/{plano_id}/agenda/{ag_id}/falta")
async def registrar_falta(request: Request, id: int, plano_id: int, ag_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    from datetime import date as _date
    hoje = _date.today().isoformat()
    with conectar() as conn:
        conn.execute(
            "UPDATE agendamentos SET status='faltou' WHERE id=%s AND plano_id=%s",
            (ag_id, plano_id)
        )
        # Verificar 3 faltas consecutivas
        ultimos = conn.execute(
            """SELECT status FROM agendamentos
               WHERE plano_id = %s AND data <= %s
               ORDER BY data DESC LIMIT 3""",
            (plano_id, hoje)
        ).fetchall()
        if len(ultimos) >= 3 and all(r["status"] == "faltou" for r in ultimos[:3]):
            conn.execute(
                "UPDATE planos_tratamento SET status='cancelado' WHERE id=%s", (plano_id,)
            )
            conn.execute(
                "UPDATE agendamentos SET status='cancelado' WHERE plano_id=%s AND status='agendado'",
                (plano_id,)
            )
    return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos/{plano_id}/agenda", status_code=303)


@router.post("/{id}/planos/{plano_id}/agenda/{ag_id}/reagendar")
async def reagendar(
    request: Request,
    id: int,
    plano_id: int,
    ag_id: int,
    nova_data: str = Form(...),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    from datetime import date as _date, timedelta as _td
    with conectar() as conn:
        ag = conn.execute(
            "SELECT data, requer_passe FROM agendamentos WHERE id=%s AND plano_id=%s",
            (ag_id, plano_id)
        ).fetchone()
        if ag:
            data_original = _date.fromisoformat(ag["data"])
            data_nova = _date.fromisoformat(nova_data)
            delta = (data_nova - data_original).days

            # Atualiza este agendamento
            conn.execute(
                "UPDATE agendamentos SET data=%s WHERE id=%s", (nova_data, ag_id)
            )

            # Ajusta todos os agendamentos futuros do mesmo plano com status 'agendado'
            if delta != 0:
                futuros = conn.execute(
                    """SELECT id, data FROM agendamentos
                       WHERE plano_id=%s AND status='agendado' AND data > %s AND id != %s
                       ORDER BY data""",
                    (plano_id, ag["data"], ag_id)
                ).fetchall()
                for f in futuros:
                    d_fut = _date.fromisoformat(f["data"]) + _td(days=delta)
                    conn.execute(
                        "UPDATE agendamentos SET data=%s WHERE id=%s",
                        (d_fut.isoformat(), f["id"])
                    )
    return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos/{plano_id}/agenda", status_code=303)


# ── Ativar / Desativar ────────────────────────────────────────────────────────

@router.post("/{id}/toggle-ativo")
async def toggle_ativo(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute("UPDATE mediuns SET ativo = 1 - ativo WHERE id = %s", (id,))
    return RedirectResponse(url="/cadastros/mediuns", status_code=303)
