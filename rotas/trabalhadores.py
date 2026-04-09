from datetime import date
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar
from rotas.auth import obter_atendente_logado

from templates_config import templates
router = APIRouter(prefix="/cadastros/trabalhadores")


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


_NOMES_DIA = {
    0: "Segunda-feira",
    1: "Terça-feira",
    2: "Quarta-feira",
    3: "Quinta-feira",
    4: "Sexta-feira",
    5: "Sábado",
    6: "Domingo",
}


def _parse_data(texto: str):
    """Converte DD/MM/AAAA → YYYY-MM-DD."""
    t = texto.strip()
    if not t:
        return None
    if "/" in t:
        partes = t.split("/")
        if len(partes) == 3:
            d, m, a = partes
            return f"{a.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
    return t or None


# ── Lista ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def listar(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        rows = conn.execute(
            "SELECT id, nome_completo, cpf, telefone, email, ativo FROM trabalhadores ORDER BY nome_completo"
        ).fetchall()
    return templates.TemplateResponse("trabalhadores/lista.html", {
        "request": request,
        "atendente": atendente,
        "trabalhadores": [dict(r) for r in rows],
    })


# ── Novo ──────────────────────────────────────────────────────────────────────

@router.get("/novo", response_class=HTMLResponse)
async def form_novo(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("trabalhadores/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": None,
        "erro": None,
    })


@router.post("/novo", response_class=HTMLResponse)
async def salvar_novo(
    request: Request,
    nome_completo: str = Form(...),
    cpf: str = Form(""),
    rg: str = Form(""),
    data_nascimento: str = Form(""),
    telefone: str = Form(""),
    email: str = Form(""),
    cep: str = Form(""),
    logradouro: str = Form(""),
    numero: str = Form(""),
    complemento: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    uf: str = Form(""),
    valor_mensalidade: str = Form("0"),
    dia_vencimento: str = Form("10"),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    dn = _parse_data(data_nascimento)
    try:
        with conectar() as conn:
            conn.execute(
                """INSERT INTO trabalhadores
                   (nome_completo, cpf, rg, data_nascimento, telefone, email,
                    cep, logradouro, numero, complemento, bairro, cidade, uf,
                    valor_mensalidade, dia_vencimento)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (nome_completo.strip(), cpf.strip() or None, rg.strip() or None, dn,
                 telefone.strip(), email.strip().lower(),
                 cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
                 bairro.strip(), cidade.strip(), uf.strip(),
                 float(valor_mensalidade or 0), int(dia_vencimento or 10)),
            )
    except Exception:
        return templates.TemplateResponse("trabalhadores/form.html", {
            "request": request,
            "atendente": atendente,
            "registro": None,
            "erro": f"CPF '{cpf}' já cadastrado.",
        })
    return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)


# ── Editar ────────────────────────────────────────────────────────────────────

@router.get("/{id}/editar", response_class=HTMLResponse)
async def form_editar(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute("SELECT * FROM trabalhadores WHERE id = %s", (id,)).fetchone()
    if not row:
        return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)
    return templates.TemplateResponse("trabalhadores/form.html", {
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
    cpf: str = Form(""),
    rg: str = Form(""),
    data_nascimento: str = Form(""),
    telefone: str = Form(""),
    email: str = Form(""),
    cep: str = Form(""),
    logradouro: str = Form(""),
    numero: str = Form(""),
    complemento: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    uf: str = Form(""),
    valor_mensalidade: str = Form("0"),
    dia_vencimento: str = Form("10"),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    dn = _parse_data(data_nascimento)
    try:
        with conectar() as conn:
            conn.execute(
                """UPDATE trabalhadores SET
                   nome_completo=%s, cpf=%s, rg=%s, data_nascimento=%s,
                   telefone=%s, email=%s,
                   cep=%s, logradouro=%s, numero=%s, complemento=%s,
                   bairro=%s, cidade=%s, uf=%s,
                   valor_mensalidade=%s, dia_vencimento=%s
                   WHERE id=%s""",
                (nome_completo.strip(), cpf.strip() or None, rg.strip() or None, dn,
                 telefone.strip(), email.strip().lower(),
                 cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
                 bairro.strip(), cidade.strip(), uf.strip(),
                 float(valor_mensalidade or 0), int(dia_vencimento or 10), id),
            )
    except Exception:
        with conectar() as conn:
            row = conn.execute("SELECT * FROM trabalhadores WHERE id=%s", (id,)).fetchone()
        return templates.TemplateResponse("trabalhadores/form.html", {
            "request": request,
            "atendente": atendente,
            "registro": dict(row) if row else None,
            "erro": f"CPF '{cpf}' já cadastrado.",
        })
    return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)


# ── Ativar / Desativar ────────────────────────────────────────────────────────

@router.post("/{id}/toggle-ativo")
async def toggle_ativo(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute("UPDATE trabalhadores SET ativo = 1 - ativo WHERE id = %s", (id,))
    return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)


# ── Dias de trabalho (agenda) ─────────────────────────────────────────────────

@router.get("/{id}/agenda", response_class=HTMLResponse)
async def form_agenda(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        trabalhador = conn.execute(
            "SELECT * FROM trabalhadores WHERE id = %s", (id,)
        ).fetchone()
        if not trabalhador:
            return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)
        dias = conn.execute(
            "SELECT dia_semana FROM trabalhador_dias WHERE trabalhador_id = %s",
            (id,),
        ).fetchall()
    dias_atuais = {r["dia_semana"] for r in dias}
    return templates.TemplateResponse("trabalhadores/agenda.html", {
        "request": request,
        "atendente": atendente,
        "trabalhador": dict(trabalhador),
        "nomes_dia": _NOMES_DIA,
        "dias_atuais": dias_atuais,
    })


@router.post("/{id}/agenda", response_class=HTMLResponse)
async def salvar_agenda(
    request: Request,
    id: int,
    dias_semana: list[int] = Form([]),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "DELETE FROM trabalhador_dias WHERE trabalhador_id = %s", (id,)
        )
        for dia in dias_semana:
            conn.execute(
                """INSERT INTO trabalhador_dias (trabalhador_id, dia_semana)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (id, dia),
            )
    return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)


# ── Presença (histórico) ──────────────────────────────────────────────────────

@router.get("/{id}/presenca", response_class=HTMLResponse)
async def historico_presenca(request: Request, id: int, mes: str = ""):
    atendente, redir = _guard(request)
    if redir:
        return redir
    hoje = date.today()
    mes_atual = f"{hoje.year}-{hoje.month:02d}"
    mes_busca = mes if mes else mes_atual

    with conectar() as conn:
        trabalhador = conn.execute(
            "SELECT * FROM trabalhadores WHERE id = %s", (id,)
        ).fetchone()
        if not trabalhador:
            return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)

        registros = conn.execute(
            """SELECT tp.*, dt.data
               FROM trabalhador_presenca tp
               JOIN dias_trabalho dt ON dt.id = tp.dia_trabalho_id
               WHERE tp.trabalhador_id = %s AND dt.data LIKE %s
               ORDER BY dt.data DESC""",
            (id, f"{mes_busca}%"),
        ).fetchall()

    return templates.TemplateResponse("trabalhadores/presenca.html", {
        "request": request,
        "atendente": atendente,
        "trabalhador": dict(trabalhador),
        "registros": [dict(r) for r in registros],
        "mes_atual": mes_busca,
    })


# ── Check-in de trabalhadores (dia de trabalho) ──────────────────────────────

@router.get("/dia/trabalhador-checkin", response_class=HTMLResponse)
async def tela_checkin_trabalhadores(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    hoje = date.today().isoformat()
    with conectar() as conn:
        dia = conn.execute(
            "SELECT * FROM dias_trabalho WHERE data = %s", (hoje,)
        ).fetchone()
        if not dia:
            return templates.TemplateResponse("trabalhadores/checkin.html", {
                "request": request,
                "atendente": atendente,
                "erro": "Nenhum dia de trabalho aberto hoje. Abra o dia primeiro.",
                "trabalhadores_agenda": [],
                "presencas": [],
                "dia": None,
            })

        dia_semana_hoje = date.today().weekday()
        trabalhadores_agenda = conn.execute(
            """SELECT t.id, t.nome_completo
               FROM trabalhadores t
               JOIN trabalhador_dias td ON td.trabalhador_id = t.id
               WHERE t.ativo = 1 AND td.dia_semana = %s
               ORDER BY t.nome_completo""",
            (dia_semana_hoje,),
        ).fetchall()

        presencas = conn.execute(
            """SELECT tp.*, t.nome_completo
               FROM trabalhador_presenca tp
               JOIN trabalhadores t ON t.id = tp.trabalhador_id
               WHERE tp.dia_trabalho_id = %s
               ORDER BY t.nome_completo""",
            (dia["id"],),
        ).fetchall()

    return templates.TemplateResponse("trabalhadores/checkin.html", {
        "request": request,
        "atendente": atendente,
        "erro": None,
        "trabalhadores_agenda": [dict(t) for t in trabalhadores_agenda],
        "presencas": [dict(p) for p in presencas],
        "dia": dict(dia),
    })


@router.post("/dia/trabalhador-checkin/{id}/presente", response_class=HTMLResponse)
async def marcar_presente(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    hoje = date.today().isoformat()
    agora = date.today().strftime("%H:%M")

    with conectar() as conn:
        dia = conn.execute(
            "SELECT * FROM dias_trabalho WHERE data = %s", (hoje,)
        ).fetchone()
        if not dia:
            return RedirectResponse(url="/cadastros/trabalhadores/dia/trabalhador-checkin", status_code=303)

        conn.execute(
            """INSERT INTO trabalhador_presenca
               (trabalhador_id, dia_trabalho_id, presente, hora_chegada)
               VALUES (%s, %s, 1, %s)""",
            (id, dia["id"], agora),
        )
    return RedirectResponse(url="/cadastros/trabalhadores/dia/trabalhador-checkin", status_code=303)


@router.post("/dia/trabalhador-checkin/{id}/ausente", response_class=HTMLResponse)
async def marcar_ausente(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    hoje = date.today().isoformat()

    with conectar() as conn:
        dia = conn.execute(
            "SELECT * FROM dias_trabalho WHERE data = %s", (hoje,)
        ).fetchone()
        if not dia:
            return RedirectResponse(url="/cadastros/trabalhadores/dia/trabalhador-checkin", status_code=303)

        conn.execute(
            """INSERT INTO trabalhador_presenca
               (trabalhador_id, dia_trabalho_id, presente)
               VALUES (%s, %s, 0)""",
            (id, dia["id"]),
        )
    return RedirectResponse(url="/cadastros/trabalhadores/dia/trabalhador-checkin", status_code=303)
