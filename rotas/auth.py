import os
import secrets
import time
import logging

import bcrypt
from fastapi import APIRouter, Request, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import HTTPException

from banco import conectar

from templates_config import templates, _obter_config_centro as obter_config_centro
router = APIRouter()

logger = logging.getLogger(__name__)

# Sessões em memória: {token: (atendente_id, timestamp)}
_sessoes: dict[str, tuple[int, float]] = {}
_SESSION_TTL = 8 * 3600   # 8 horas
_MAX_SESSOES = 500


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()


def _verificar_senha(senha: str, hash_armazenado: str) -> bool:
    """Verifica senha suportando hashes bcrypt e SHA-256 legados."""
    try:
        if hash_armazenado.startswith("$2b$") or hash_armazenado.startswith("$2a$"):
            return bcrypt.checkpw(senha.encode(), hash_armazenado.encode())
        # Hash SHA-256 legado (hex de 64 chars): aceita mas deve migrar
        import hashlib
        return secrets.compare_digest(
            hashlib.sha256(senha.encode()).hexdigest(),
            hash_armazenado
        )
    except Exception:
        return False


def _e_hash_legado(hash_armazenado: str) -> bool:
    return not (hash_armazenado.startswith("$2b$") or hash_armazenado.startswith("$2a$"))


def _limpar_sessoes_expiradas():
    agora = time.time()
    expiradas = [t for t, (_, ts) in _sessoes.items() if agora - ts > _SESSION_TTL]
    for t in expiradas:
        del _sessoes[t]


def criar_sessao(atendente_id: int) -> str:
    token = secrets.token_hex(32)
    _limpar_sessoes_expiradas()
    if len(_sessoes) >= _MAX_SESSOES:
        mais_antiga = min(_sessoes, key=lambda t: _sessoes[t][1])
        del _sessoes[mais_antiga]
    _sessoes[token] = (atendente_id, time.time())
    return token


def obter_atendente_logado(request: Request):
    token = request.cookies.get("sessao")
    if not token or token not in _sessoes:
        return None
    atendente_id, ts = _sessoes[token]
    if time.time() - ts > _SESSION_TTL:
        del _sessoes[token]
        return None
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
    return templates.TemplateResponse("login.html", {
        "request": request,
        "erro": None,
        "centro": obter_config_centro(),
    })


@router.post("/login", response_class=HTMLResponse)
async def fazer_login(
    request: Request,
    response: Response,
    nome_usuario: str = Form(...),
    senha: str = Form(...)
):
    with conectar() as conn:
        row = conn.execute(
            "SELECT id, nome_completo, senha_hash FROM atendentes WHERE nome_usuario = %s AND ativo = 1",
            (nome_usuario,)
        ).fetchone()

    if not row or not _verificar_senha(senha, row["senha_hash"]):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "erro": "Usuário ou senha inválidos.", "centro": obter_config_centro()},
            status_code=401
        )

    # Migrar hash SHA-256 legado para bcrypt no login bem-sucedido
    if _e_hash_legado(row["senha_hash"]):
        novo_hash = hash_senha(senha)
        with conectar() as conn:
            conn.execute(
                "UPDATE atendentes SET senha_hash = %s WHERE id = %s",
                (novo_hash, row["id"])
            )

    token = criar_sessao(row["id"])
    resp = RedirectResponse(url="/menu", status_code=303)
    usar_https = os.environ.get("SHAMBALA_HTTPS", "false").lower() == "true"
    resp.set_cookie("sessao", token, httponly=True, samesite="lax", secure=usar_https)
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
    return templates.TemplateResponse("menu.html", {"request": request, "atendente": atendente, "e_admin": e_admin(request)})


@router.get("/", response_class=HTMLResponse)
async def raiz(request: Request):
    return RedirectResponse(url="/menu", status_code=303)


def obter_usuario_logado(request: Request):
    """Alias para obter_atendente_logado para compatibilidade."""
    return obter_atendente_logado(request)


def e_admin(request: Request) -> bool:
    """Verifica se o usuário logado pertence ao grupo Admin (id=1)."""
    usuario = obter_atendente_logado(request)
    if not usuario:
        return False
    with conectar() as conn:
        row = conn.execute(
            "SELECT 1 FROM usuarios_grupos WHERE usuario_id = %s AND grupo_id = 1",
            (usuario["id"],)
        ).fetchone()
    return row is not None


def criar_atendente_inicial():
    """Cria o atendente padrão 'admin' e o grupo Admin se não existirem."""
    with conectar() as conn:
        # Garantir grupo Admin (id=1)
        conn.execute(
            "INSERT INTO grupos (id, nome, descricao) VALUES (1, 'Admin', 'Administradores') "
            "ON CONFLICT (id) DO NOTHING"
        )
        # Garantir sequência alinhada após insert explícito
        conn.execute("SELECT setval('grupos_id_seq', (SELECT MAX(id) FROM grupos))")

        # Criar admin se não existir
        total = conn.execute("SELECT COUNT(*) AS c FROM atendentes").fetchone()["c"]
        if total == 0:
            senha_inicial = secrets.token_urlsafe(12)
            print(f"\n[SHAMBALA] Primeiro acesso — usuário: admin  senha: {senha_inicial}")
            print("[SHAMBALA] Altere esta senha em /cadastros/usuarios\n")
            cur = conn.execute(
                "INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash) "
                "VALUES (%s, %s, %s) RETURNING id",
                ("admin", "Administrador", hash_senha(senha_inicial))
            )
            admin_id = cur.fetchone()["id"]
            conn.execute(
                "INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, 1) "
                "ON CONFLICT DO NOTHING",
                (admin_id,)
            )
        else:
            # Garantir que admin existente esteja no grupo Admin
            admin = conn.execute(
                "SELECT id FROM atendentes WHERE nome_usuario = 'admin'"
            ).fetchone()
            if admin:
                conn.execute(
                    "INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, 1) "
                    "ON CONFLICT DO NOTHING",
                    (admin["id"],)
                )
