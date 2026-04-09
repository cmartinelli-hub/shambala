import hashlib
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar
from rotas.auth import obter_atendente_logado

from templates_config import templates
router = APIRouter(prefix="/cadastros/atendentes")


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


def _hash(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()


# ── Lista ────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def listar(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        rows = conn.execute(
            "SELECT id, nome_usuario, nome_completo, telefone, email, ativo FROM atendentes ORDER BY nome_completo"
        ).fetchall()
    return templates.TemplateResponse("atendentes/lista.html", {
        "request": request,
        "atendente": atendente,
        "atendentes": [dict(r) for r in rows],
    })


# ── Novo ─────────────────────────────────────────────────────────────────────

@router.get("/novo", response_class=HTMLResponse)
async def form_novo(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("atendentes/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": None,
        "erro": None,
    })


@router.post("/novo", response_class=HTMLResponse)
async def salvar_novo(
    request: Request,
    nome_usuario: str = Form(...),
    nome_completo: str = Form(...),
    senha: str = Form(...),
    telefone: str = Form(""),
    email: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    if not senha.strip():
        return templates.TemplateResponse("atendentes/form.html", {
            "request": request,
            "atendente": atendente,
            "registro": None,
            "erro": "A senha é obrigatória para novos atendentes.",
        })

    try:
        with conectar() as conn:
            conn.execute(
                "INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, telefone, email) VALUES (%s,%s,%s,%s,%s)",
                (nome_usuario.strip(), nome_completo.strip(), _hash(senha), telefone.strip(), email.strip().lower()),
            )
    except Exception:
        return templates.TemplateResponse("atendentes/form.html", {
            "request": request,
            "atendente": atendente,
            "registro": None,
            "erro": f"Nome de usuário '{nome_usuario}' já existe.",
        })

    return RedirectResponse(url="/cadastros/atendentes", status_code=303)


# ── Editar ───────────────────────────────────────────────────────────────────

@router.get("/{id}/editar", response_class=HTMLResponse)
async def form_editar(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(
            "SELECT id, nome_usuario, nome_completo, telefone, email, ativo FROM atendentes WHERE id = %s", (id,)
        ).fetchone()
    if not row:
        return RedirectResponse(url="/cadastros/atendentes", status_code=303)
    return templates.TemplateResponse("atendentes/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": dict(row),
        "erro": None,
    })


@router.post("/{id}/editar", response_class=HTMLResponse)
async def salvar_editar(
    request: Request,
    id: int,
    nome_usuario: str = Form(...),
    nome_completo: str = Form(...),
    senha: str = Form(""),
    telefone: str = Form(""),
    email: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    try:
        with conectar() as conn:
            if senha.strip():
                conn.execute(
                    "UPDATE atendentes SET nome_usuario=%s, nome_completo=%s, senha_hash=%s, telefone=%s, email=%s WHERE id=%s",
                    (nome_usuario.strip(), nome_completo.strip(), _hash(senha), telefone.strip(), email.strip().lower(), id),
                )
            else:
                conn.execute(
                    "UPDATE atendentes SET nome_usuario=%s, nome_completo=%s, telefone=%s, email=%s WHERE id=%s",
                    (nome_usuario.strip(), nome_completo.strip(), telefone.strip(), email.strip().lower(), id),
                )
    except Exception:
        with conectar() as conn:
            row = conn.execute("SELECT * FROM atendentes WHERE id=%s", (id,)).fetchone()
        return templates.TemplateResponse("atendentes/form.html", {
            "request": request,
            "atendente": atendente,
            "registro": dict(row) if row else None,
            "erro": f"Nome de usuário '{nome_usuario}' já existe.",
        })

    return RedirectResponse(url="/cadastros/atendentes", status_code=303)


# ── Ativar / Desativar ────────────────────────────────────────────────────────

@router.post("/{id}/toggle-ativo")
async def toggle_ativo(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute("UPDATE atendentes SET ativo = 1 - ativo WHERE id = %s", (id,))
    return RedirectResponse(url="/cadastros/atendentes", status_code=303)
