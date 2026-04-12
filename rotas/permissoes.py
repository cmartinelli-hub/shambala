# -*- coding: utf-8 -*-
"""
Módulo de permissões — grupos e rotas acessíveis.

Cada atendente pertence a um grupo (admin ou outro criado).
Cada grupo tem permissões granulares por módulo no estilo Unix:
  - ler (leitura/visualização)
  - escrever (criação/edição)
  - apagar (exclusão)
"""
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from banco import conectar
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/cadastros/permissoes")

# Mapa de rotas → etiquetas legíveis
ROTULOS_ROTA = {
    "menu": "Menu",
    "dia.painel": "Painel do Dia",
    "dia.dashboard": "Dashboard",
    "dia.checkin": "Check-in",
    "dia.passe": "Fila de Passe",
    "dia.reiki": "Fila de Reiki",
    "dia.acolhimento": "Fila de Acolhimento",
    "dia.atendimento": "Fila de Atendimento",
    "dia.fraterno": "Atendimento Fraterno",
    "cadastros.pessoas": "Cadastros → Pessoas",
    "cadastros.mediuns": "Cadastros → Médiuns",
    "cadastros.usuarios": "Cadastros → Usuários",
    "cadastros.trabalhadores": "Cadastros → Trabalhadores",
    "cadastros.permissoes": "Cadastros → Permissões",
    "agenda": "Agenda",
    "chamada": "Tela de Chamada",
    "relatorios": "Relatórios",
    "configuracoes": "Configurações",
    "mala.direta": "Mala Direta",
    "financeiro": "Financeiro",
    "biblioteca": "Biblioteca",
    "doacoes": "Doações",
}


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)

    with conectar() as conn:
        grupo = conn.execute(
            """SELECT a.grupo_id, g.nome as nome_grupo
               FROM atendentes a
               LEFT JOIN grupos g ON g.id = a.grupo_id
               WHERE a.id = %s""",
            (atendente["id"],)
        ).fetchone()

    # Admin (grupo_id=1) tem acesso total; demais precisam de permissão
    if grupo and grupo["grupo_id"] is not None and grupo["grupo_id"] != 1:
        pode = conn.execute(
            "SELECT ler FROM grupos_permissoes WHERE grupo_id = %s AND modulo = %s",
            (grupo["grupo_id"], "cadastros.permissoes")
        ).fetchone()
        if not pode or not pode["ler"]:
            return None, HTMLResponse(status_code=403, content="Acesso negado.")

    return atendente, None


def pode_acessar(grupo_id: int, modulo: str, acao: str = "ler") -> bool:
    """Verifica se um grupo tem permissão para uma ação em um módulo.

    Args:
        grupo_id: ID do grupo
        modulo: nome do módulo (ex: "cadastros.pessoas")
        acao: tipo de permissão ("ler", "escrever", "apagar")
    """
    if grupo_id is None:
        return False
    with conectar() as conn:
        row = conn.execute(
            "SELECT %s FROM grupos_permissoes WHERE grupo_id = %%s AND modulo = %%s" % acao,
            (grupo_id, modulo)
        ).fetchone()
        if not row:
            return False
        val = row[acao]
        # psycopg2 retorna boolean como bool; sqlite como int
        return bool(val)


def obter_atendente_com_grupo(request: Request):
    """Retorna dict do atendente com campos extras 'grupo_id' e 'nome_grupo'."""
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None

    with conectar() as conn:
        row = conn.execute(
            """SELECT a.grupo_id, g.nome as nome_grupo
               FROM atendentes a
               LEFT JOIN grupos g ON g.id = a.grupo_id
               WHERE a.id = %s""",
            (atendente["id"],)
        ).fetchone()
        if row:
            atendente["grupo_id"] = row["grupo_id"]
            atendente["nome_grupo"] = row["nome_grupo"]

    return atendente


def obter_grupos_usuario(usuario_id: int):
    """Retorna lista de grupos a que um usuário pertence."""
    with conectar() as conn:
        rows = conn.execute(
            """SELECT g.id, g.nome, g.descricao
               FROM grupos g
               INNER JOIN usuarios_grupos ug ON ug.grupo_id = g.id
               WHERE ug.usuario_id = %s
               ORDER BY g.nome""",
            (usuario_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def salvar_grupos_usuario(usuario_id: int, grupo_ids: list[int]):
    """Salva os grupos a que um usuário pertence (N:N relationship)."""
    with conectar() as conn:
        # Remove grupos antigos
        conn.execute(
            "DELETE FROM usuarios_grupos WHERE usuario_id = %s",
            (usuario_id,)
        )
        # Insere novos grupos
        for grupo_id in grupo_ids:
            conn.execute(
                "INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, %s)",
                (usuario_id, grupo_id)
            )


def seed_permissoes():
    """Cria grupo Admin com acesso total se não existir."""
    with conectar() as conn:
        existe_grupo = conn.execute("SELECT COUNT(*) AS c FROM grupos").fetchone()["c"]
        if existe_grupo == 0:
            cur = conn.execute(
                "INSERT INTO grupos (nome, descricao) VALUES (%s, %s) RETURNING id",
                ("Admin", "Acesso total ao sistema")
            )
            admin_id = cur.fetchone()["id"]

            modulos = list(ROTULOS_ROTA.keys())
            for modulo in modulos:
                conn.execute(
                    """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
                       VALUES (%s, %s, TRUE, TRUE, TRUE)""",
                    (admin_id, modulo)
                )

            # Atribuir atendentes sem grupo ao Admin
            conn.execute(
                "UPDATE atendentes SET grupo_id = %s WHERE grupo_id IS NULL",
                (admin_id,)
            )


# ── Lista de grupos ───────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def listar_grupos(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        grupos = conn.execute(
            "SELECT id, nome, descricao FROM grupos ORDER BY nome"
        ).fetchall()

        # Buscar resumo de permissões por grupo
        perms_por_grupo = {}
        for g in grupos:
            rows = conn.execute(
                """SELECT modulo, ler, escrever, apagar
                   FROM grupos_permissoes
                   WHERE grupo_id = %s
                   ORDER BY modulo""",
                (g["id"],)
            ).fetchall()
            total = len(rows)
            com_leitura = sum(1 for r in rows if r["ler"])
            com_escrita = sum(1 for r in rows if r["escrever"])
            com_exclusao = sum(1 for r in rows if r["apagar"])
            perms_por_grupo[g["id"]] = {
                "total": total,
                "ler": com_leitura,
                "escrever": com_escrita,
                "apagar": com_exclusao,
            }

    return templates.TemplateResponse("permissoes/grupos.html", {
        "request": request,
        "atendente": atendente,
        "grupos": [dict(g) for g in grupos],
        "todos_modulos": ROTULOS_ROTA,
        "perms_por_grupo": perms_por_grupo,
    })


@router.post("/novo")
async def novo_grupo(
    request: Request,
    nome: str = Form(...),
    descricao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            "INSERT INTO grupos (nome, descricao) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (nome.strip(), descricao.strip()),
        )

    return RedirectResponse(url="/cadastros/permissoes", status_code=303)


@router.get("/{id}/editar", response_class=HTMLResponse)
async def editar_grupo(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        grupo = conn.execute(
            "SELECT id, nome, descricao FROM grupos WHERE id = %s", (id,)
        ).fetchone()
        if not grupo:
            return RedirectResponse(url="/cadastros/permissoes", status_code=303)

        # Permissões atuais como mapa {modulo: {ler, escrever, apagar}}
        permissoes_map = {}
        rows = conn.execute(
            "SELECT modulo, ler, escrever, apagar FROM grupos_permissoes WHERE grupo_id = %s",
            (id,)
        ).fetchall()
        for r in rows:
            permissoes_map[r["modulo"]] = {
                "ler": bool(r["ler"]),
                "escrever": bool(r["escrever"]),
                "apagar": bool(r["apagar"]),
            }

    return templates.TemplateResponse("permissoes/editar.html", {
        "request": request,
        "atendente": atendente,
        "grupo": dict(grupo),
        "todos_modulos": ROTULOS_ROTA,
        "permissoes_map": permissoes_map,
    })


@router.post("/{id}/editar")
async def salvar_grupo(
    request: Request,
    id: int,
    nome: str = Form(...),
    descricao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    form = await request.form()

    with conectar() as conn:
        conn.execute(
            "UPDATE grupos SET nome=%s, descricao=%s WHERE id=%s",
            (nome.strip(), descricao.strip(), id)
        )
        # Remover permissões antigas e inserir novas
        conn.execute("DELETE FROM grupos_permissoes WHERE grupo_id = %s", (id,))

        for modulo in ROTULOS_ROTA:
            ler = form.get(f"{modulo}_ler") == "1"
            escrever = form.get(f"{modulo}_escrever") == "1"
            apagar = form.get(f"{modulo}_apagar") == "1"

            # Só insere se pelo menos uma permissão estiver marcada
            if ler or escrever or apagar:
                conn.execute(
                    """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (id, modulo, ler, escrever, apagar)
                )

    return RedirectResponse(url="/cadastros/permissoes", status_code=303)


@router.post("/{id}/remover")
async def remover_grupo(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        # Impedir remoção do grupo Admin (id=1)
        grupo = conn.execute("SELECT nome FROM grupos WHERE id = %s", (id,)).fetchone()
        if grupo and grupo["nome"] == "Admin":
            return RedirectResponse(url="/cadastros/permissoes", status_code=303)

        conn.execute("DELETE FROM grupos WHERE id = %s", (id,))

    return RedirectResponse(url="/cadastros/permissoes", status_code=303)
