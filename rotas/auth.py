import hashlib
import secrets
from fastapi import APIRouter, Request, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import HTTPException

from banco import conectar

from templates_config import templates
router = APIRouter()

# Sessões em memória: {token: atendente_id}
_sessoes: dict[str, int] = {}


def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()


def criar_sessao(atendente_id: int) -> str:
    token = secrets.token_hex(32)
    _sessoes[token] = atendente_id
    return token


def obter_atendente_logado(request: Request):
    token = request.cookies.get("sessao")
    if not token or token not in _sessoes:
        return None
    atendente_id = _sessoes[token]
    with conectar() as conn:
        row = conn.execute(
            "SELECT id, nome_completo, nome_usuario FROM atendentes WHERE id = %s AND ativo = 1",
            (atendente_id,)
        ).fetchone()
    return dict(row) if row else None


def exige_login(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        raise HTTPException(status_code=401)
    return atendente


@router.get("/login", response_class=HTMLResponse)
async def pagina_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "erro": None})


@router.post("/login", response_class=HTMLResponse)
async def fazer_login(
    request: Request,
    response: Response,
    nome_usuario: str = Form(...),
    senha: str = Form(...)
):
    with conectar() as conn:
        row = conn.execute(
            "SELECT id, nome_completo FROM atendentes WHERE nome_usuario = %s AND senha_hash = %s AND ativo = 1",
            (nome_usuario, hash_senha(senha))
        ).fetchone()

    if not row:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "erro": "Usuário ou senha inválidos."},
            status_code=401
        )

    token = criar_sessao(row["id"])
    resp = RedirectResponse(url="/menu", status_code=303)
    resp.set_cookie("sessao", token, httponly=True, samesite="lax")
    return resp


@router.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("sessao")
    if token:
        _sessoes.pop(token, None)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("sessao")
    return resp


@router.get("/menu", response_class=HTMLResponse)
async def pagina_menu(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("menu.html", {"request": request, "atendente": atendente})


@router.get("/", response_class=HTMLResponse)
async def raiz(request: Request):
    return RedirectResponse(url="/menu", status_code=303)


def criar_atendente_inicial():
    """Cria o atendente padrão 'admin' se não existir nenhum."""
    with conectar() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM atendentes").fetchone()["c"]
        if total == 0:
            conn.execute(
                "INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash) VALUES (%s, %s, %s)",
                ("admin", "Administrador", hash_senha("admin"))
            )
