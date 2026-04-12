from datetime import date, datetime
from fastapi import APIRouter, Request, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import os
import uuid
from banco import conectar
from rotas.auth import obter_atendente_logado

from templates_config import templates
router = APIRouter(prefix="/cadastros/trabalhadores")

# Configuração de upload de fotos
FOTOS_TRAB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fotos")
os.makedirs(FOTOS_TRAB_DIR, exist_ok=True)
EXTensoes_TRAB = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TAMANHO_MAXIMO_TRAB = 5 * 1024 * 1024  # 5MB


def _salvar_foto_trab(file: UploadFile, trab_id: int, foto_existente: str = None) -> str:
    """Salva foto do trabalhador e retorna o caminho relativo."""
    if foto_existente and os.path.exists(os.path.join(FOTOS_TRAB_DIR, foto_existente)):
        try:
            os.remove(os.path.join(FOTOS_TRAB_DIR, foto_existente))
        except OSError:
            pass
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in EXTensoes_TRAB:
        ext = ".jpg"
    nome_arquivo = f"trab_{trab_id}_{uuid.uuid4().hex[:8]}{ext}"
    caminho = os.path.join(FOTOS_TRAB_DIR, nome_arquivo)
    with open(caminho, "wb") as f:
        conteudo = file.file.read()
        if len(conteudo) > TAMANHO_MAXIMO_TRAB:
            raise ValueError("Arquivo muito grande (máx. 5MB)")
        f.write(conteudo)
    return nome_arquivo


# Placeholder SVG para trabalhadores
@router.get("/foto-placeholder/{inicial}")
async def foto_placeholder_trab(inicial: str):
    """Gera placeholder SVG com a inicial do trabalhador (verde-água)."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
        <defs>
            <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" style="stop-color:#0d9488"/>
                <stop offset="100%" style="stop-color:#14b8a6"/>
            </linearGradient>
        </defs>
        <circle cx="100" cy="100" r="100" fill="url(#g)"/>
        <text x="100" y="115" text-anchor="middle" fill="white"
              font-family="system-ui,sans-serif" font-size="90"
              font-weight="bold">{inicial.upper()}</text>
    </svg>'''
    return Response(content=svg, media_type="image/svg+xml")


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
    foto: UploadFile = None,
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    dn = _parse_data(data_nascimento)
    try:
        with conectar() as conn:
            cur = conn.execute(
                """INSERT INTO trabalhadores
                   (nome_completo, cpf, rg, data_nascimento, telefone, email,
                    cep, logradouro, numero, complemento, bairro, cidade, uf,
                    valor_mensalidade, dia_vencimento)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (nome_completo.strip(), cpf.strip() or None, rg.strip() or None, dn,
                 telefone.strip(), email.strip().lower(),
                 cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
                 bairro.strip(), cidade.strip(), uf.strip(),
                 float(valor_mensalidade or 0), int(dia_vencimento or 10)),
            )
            trab_id = cur.fetchone()["id"]

            # Salvar foto se enviada
            if foto and getattr(foto, "filename", None):
                try:
                    foto_trab = _salvar_foto_trab(foto, trab_id)
                    conn.execute(
                        "UPDATE trabalhadores SET foto_trabalhador = %s WHERE id = %s",
                        (foto_trab, trab_id)
                    )
                except ValueError:
                    pass
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
    remover_foto: str = Form(""),
    foto: UploadFile = None,
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    dn = _parse_data(data_nascimento)

    with conectar() as conn:
        # Buscar foto existente
        existente = conn.execute(
            "SELECT foto_trabalhador FROM trabalhadores WHERE id = %s", (id,)
        ).fetchone()
        foto_existente = existente["foto_trabalhador"] if existente else None

        # Remover foto se solicitado
        if remover_foto == "1" and foto_existente:
            if os.path.exists(os.path.join(FOTOS_TRAB_DIR, foto_existente)):
                try:
                    os.remove(os.path.join(FOTOS_TRAB_DIR, foto_existente))
                except OSError:
                    pass
            foto_existente = None

        # Salvar nova foto se enviada
        if foto and getattr(foto, "filename", None):
            try:
                foto_existente = _salvar_foto_trab(foto, id, foto_existente)
            except ValueError:
                pass

        try:
            conn.execute(
                """UPDATE trabalhadores SET
                   nome_completo=%s, cpf=%s, rg=%s, data_nascimento=%s,
                   telefone=%s, email=%s,
                   cep=%s, logradouro=%s, numero=%s, complemento=%s,
                   bairro=%s, cidade=%s, uf=%s,
                   valor_mensalidade=%s, dia_vencimento=%s,
                   foto_trabalhador=%s
                   WHERE id=%s""",
                (nome_completo.strip(), cpf.strip() or None, rg.strip() or None, dn,
                 telefone.strip(), email.strip().lower(),
                 cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
                 bairro.strip(), cidade.strip(), uf.strip(),
                 float(valor_mensalidade or 0), int(dia_vencimento or 10),
                 foto_existente, id),
            )
        except Exception:
            with conectar() as conn2:
                row = conn2.execute("SELECT * FROM trabalhadores WHERE id=%s", (id,)).fetchone()
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

# ── Check-in de Trabalhadores (Menu Principal - acesso geral) ────────────────
router_checkin_trab = APIRouter(prefix="/checkin-trabalhador")


@router_checkin_trab.get("", response_class=HTMLResponse)
async def checkin_trabalhador_page(request: Request):
    """Página de check-in de trabalhadores (acessível a todos os logados)."""
    atendente = obter_atendente_logado(request)
    if not atendente:
        return RedirectResponse(url="/login", status_code=303)

    from datetime import date as _date
    hoje = _date.today().isoformat()

    with conectar() as conn:
        dia = conn.execute(
            "SELECT * FROM dias_trabalho WHERE data = %s AND aberto = 1", (hoje,)
        ).fetchone()

        if not dia:
            return templates.TemplateResponse("trabalhadores/dia_sem_abertura.html", {
                "request": request,
                "atendente": atendente,
            })

        # Trabalhadores com agenda para hoje (dia da semana configurado)
        dia_semana = _date.today().weekday()
        trabalhadores_agenda = conn.execute(
            """SELECT t.id, t.nome_completo, t.foto_trabalhador
               FROM trabalhadores t
               JOIN trabalhador_dias td ON td.trabalhador_id = t.id
               WHERE t.ativo = 1 AND td.dia_semana = %s
               ORDER BY t.nome_completo""",
            (dia_semana,)
        ).fetchall()

        # Presenças de hoje
        presencas = conn.execute(
            """SELECT trabalhador_id, presente
               FROM trabalhador_presenca
               WHERE dia_trabalho_id = %s""",
            (dia["id"],)
        ).fetchall()

    return templates.TemplateResponse("trabalhadores/checkin.html", {
        "request": request,
        "atendente": atendente,
        "dia": dict(dia) if dia else None,
        "trabalhadores_agenda": [dict(t) for t in trabalhadores_agenda],
        "presencas": [dict(p) for p in presencas],
        "erro": None,
    })


@router_checkin_trab.post("/{id}/presente", response_class=HTMLResponse)
async def marcar_presente(request: Request, id: int):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return RedirectResponse(url="/login", status_code=303)

    from datetime import datetime as _dt
    hoje = _dt.now().strftime("%Y-%m-%d")
    agora = _dt.now().strftime("%H:%M")

    with conectar() as conn:
        dia = conn.execute(
            "SELECT id FROM dias_trabalho WHERE data = %s AND aberto = 1", (hoje,)
        ).fetchone()
        if not dia:
            return RedirectResponse(url="/checkin-trabalhador", status_code=303)

        conn.execute(
            """INSERT INTO trabalhador_presenca
               (trabalhador_id, dia_trabalho_id, presente, hora_chegada)
               VALUES (%s, %s, 1, %s)
               ON CONFLICT (trabalhador_id, dia_trabalho_id)
               DO UPDATE SET presente = 1, hora_chegada = %s""",
            (id, dia["id"], agora, agora)
        )
    return RedirectResponse(url="/checkin-trabalhador", status_code=303)


@router_checkin_trab.post("/{id}/ausente", response_class=HTMLResponse)
async def marcar_ausente(request: Request, id: int):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return RedirectResponse(url="/login", status_code=303)

    from datetime import date as _date
    hoje = _date.today().isoformat()

    with conectar() as conn:
        dia = conn.execute(
            "SELECT id FROM dias_trabalho WHERE data = %s AND aberto = 1", (hoje,)
        ).fetchone()
        if not dia:
            return RedirectResponse(url="/checkin-trabalhador", status_code=303)

        conn.execute(
            """INSERT INTO trabalhador_presenca
               (trabalhador_id, dia_trabalho_id, presente, hora_chegada, hora_saida)
               VALUES (%s, %s, 0, NULL, NULL)
               ON CONFLICT (trabalhador_id, dia_trabalho_id)
               DO UPDATE SET presente = 0""",
            (id, dia["id"])
        )
    return RedirectResponse(url="/checkin-trabalhador", status_code=303)
