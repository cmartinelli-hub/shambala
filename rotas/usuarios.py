import logging
from typing import List
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import psycopg2

from banco import conectar
from rotas.auth import obter_atendente_logado, e_admin, hash_senha
from rotas.permissoes import obter_grupos_usuario, salvar_grupos_usuario
from templates_config import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cadastros/usuarios")


def _guard(request: Request):
    """Verifica se usuário está logado e é admin"""
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    if not e_admin(request):
        return None, HTMLResponse(status_code=403, content="Acesso negado. Apenas administradores.")
    return atendente, None


# ── Lista ────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def listar_usuarios(request: Request):
    usuario, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        rows = conn.execute("""
            SELECT a.id, a.nome_usuario, a.nome_completo, a.telefone, a.email, a.ativo,
                   string_agg(g.nome, ', ' ORDER BY g.nome) as grupos_nome
            FROM atendentes a
            LEFT JOIN usuarios_grupos ug ON ug.usuario_id = a.id
            LEFT JOIN grupos g ON g.id = ug.grupo_id
            GROUP BY a.id
            ORDER BY a.nome_completo
        """).fetchall()
    return templates.TemplateResponse("usuarios/lista.html", {
        "request": request,
        "atendente": usuario,
        "usuarios": [dict(r) for r in rows],
    })


# ── Novo ─────────────────────────────────────────────────────────────────────

@router.get("/novo", response_class=HTMLResponse)
async def form_novo_usuario(request: Request):
    usuario, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        grupos = conn.execute("SELECT id, nome, descricao FROM grupos ORDER BY nome").fetchall()
    return templates.TemplateResponse("usuarios/form.html", {
        "request": request,
        "atendente": usuario,
        "registro": None,
        "grupos": [dict(g) for g in grupos],
        "grupos_selecionados": [],
        "erro": None,
    })


@router.post("/novo", response_class=HTMLResponse)
async def salvar_novo_usuario(
    request: Request,
    nome_usuario: str = Form(...),
    nome_completo: str = Form(...),
    senha: str = Form(...),
    telefone: str = Form(""),
    email: str = Form(""),
    grupos: List[int] = Form(default=[]),
):
    usuario, redir = _guard(request)
    if redir:
        return redir

    if not senha.strip():
        with conectar() as conn:
            grupos_lista = conn.execute("SELECT id, nome, descricao FROM grupos ORDER BY nome").fetchall()
        return templates.TemplateResponse("usuarios/form.html", {
            "request": request,
            "atendente": usuario,
            "registro": None,
            "grupos": [dict(g) for g in grupos_lista],
            "grupos_selecionados": grupos,
            "erro": "A senha é obrigatória.",
        })

    try:
        with conectar() as conn:
            row = conn.execute(
                "INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, telefone, email, ativo) "
                "VALUES (%s, %s, %s, %s, %s, 1) RETURNING id",
                (nome_usuario.strip(), nome_completo.strip(), hash_senha(senha),
                 telefone.strip() or None, email.strip() or None)
            ).fetchone()
            novo_id = row["id"]

        # Salva grupos
        salvar_grupos_usuario(novo_id, grupos)

        return RedirectResponse(url="/cadastros/atendentes", status_code=303)
    except psycopg2.errors.UniqueViolation:
        with conectar() as conn:
            grupos_lista = conn.execute("SELECT id, nome, descricao FROM grupos ORDER BY nome").fetchall()
        return templates.TemplateResponse("usuarios/form.html", {
            "request": request,
            "atendente": usuario,
            "registro": None,
            "grupos": [dict(g) for g in grupos_lista],
            "grupos_selecionados": grupos,
            "erro": "Nome de usuário já existe.",
        })
    except Exception:
        logger.exception("Erro ao criar usuário")
        with conectar() as conn:
            grupos_lista = conn.execute("SELECT id, nome, descricao FROM grupos ORDER BY nome").fetchall()
        return templates.TemplateResponse("usuarios/form.html", {
            "request": request,
            "atendente": usuario,
            "registro": None,
            "grupos": [dict(g) for g in grupos_lista],
            "grupos_selecionados": grupos,
            "erro": "Erro interno ao salvar. Contate o administrador.",
        })


# ── Editar ───────────────────────────────────────────────────────────────────

@router.get("/{id}/editar", response_class=HTMLResponse)
async def form_editar_usuario(request: Request, id: int):
    usuario, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        registro = conn.execute("SELECT id, nome_usuario, nome_completo, telefone, email, ativo FROM atendentes WHERE id = %s", (id,)).fetchone()
        if not registro:
            return RedirectResponse(url="/cadastros/atendentes", status_code=303)
        grupos = conn.execute("SELECT id, nome, descricao FROM grupos ORDER BY nome").fetchall()
        grupos_do_usuario = obter_grupos_usuario(id)
    return templates.TemplateResponse("usuarios/form.html", {
        "request": request,
        "atendente": usuario,
        "registro": dict(registro),
        "grupos": [dict(g) for g in grupos],
        "grupos_selecionados": [g["id"] for g in grupos_do_usuario],
        "erro": None,
    })


@router.post("/{id}/editar", response_class=HTMLResponse)
async def salvar_edicao_usuario(
    request: Request,
    id: int,
    nome_usuario: str = Form(...),
    nome_completo: str = Form(...),
    senha: str = Form(""),
    telefone: str = Form(""),
    email: str = Form(""),
    grupos: List[int] = Form(default=[]),
):
    usuario, redir = _guard(request)
    if redir:
        return redir

    try:
        with conectar() as conn:
            if senha.strip():
                conn.execute(
                    "UPDATE atendentes SET nome_usuario=%s, nome_completo=%s, senha_hash=%s, telefone=%s, email=%s WHERE id=%s",
                    (nome_usuario.strip(), nome_completo.strip(), hash_senha(senha),
                     telefone.strip() or None, email.strip() or None, id)
                )
            else:
                conn.execute(
                    "UPDATE atendentes SET nome_usuario=%s, nome_completo=%s, telefone=%s, email=%s WHERE id=%s",
                    (nome_usuario.strip(), nome_completo.strip(),
                     telefone.strip() or None, email.strip() or None, id)
                )

        # Salva grupos
        salvar_grupos_usuario(id, grupos)

        return RedirectResponse(url="/cadastros/atendentes", status_code=303)
    except psycopg2.errors.UniqueViolation:
        with conectar() as conn:
            registro = conn.execute("SELECT * FROM atendentes WHERE id=%s", (id,)).fetchone()
            grupos_lista = conn.execute("SELECT id, nome, descricao FROM grupos ORDER BY nome").fetchall()
        return templates.TemplateResponse("usuarios/form.html", {
            "request": request,
            "atendente": usuario,
            "registro": dict(registro) if registro else None,
            "grupos": [dict(g) for g in grupos_lista],
            "grupos_selecionados": grupos,
            "erro": "Nome de usuário já existe.",
        })
    except Exception:
        logger.exception("Erro ao editar usuário id=%s", id)
        with conectar() as conn:
            registro = conn.execute("SELECT * FROM atendentes WHERE id=%s", (id,)).fetchone()
            grupos_lista = conn.execute("SELECT id, nome, descricao FROM grupos ORDER BY nome").fetchall()
        return templates.TemplateResponse("usuarios/form.html", {
            "request": request,
            "atendente": usuario,
            "registro": dict(registro) if registro else None,
            "grupos": [dict(g) for g in grupos_lista],
            "grupos_selecionados": grupos,
            "erro": "Erro interno ao salvar. Contate o administrador.",
        })


# ── Ativar/Desativar ─────────────────────────────────────────────────────────

@router.post("/{id}/toggle-ativo")
async def toggle_ativo(request: Request, id: int):
    usuario, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute("UPDATE atendentes SET ativo = 1 - ativo WHERE id = %s", (id,))
    return RedirectResponse(url="/cadastros/atendentes", status_code=303)
