from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from banco import conectar, _normalizar
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/cadastros/pessoas")

_PARTICULAS = {"a", "o", "e", "da", "do", "de", "das", "dos", "d"}

def _capitalizar_nome(nome: str) -> str:
    """Capitaliza a primeira letra de cada palavra, mantendo partículas em minúsculas."""
    palavras = nome.lower().split()
    if not palavras:
        return nome
    resultado = [palavras[0].capitalize()]
    for p in palavras[1:]:
        if p in _PARTICULAS:
            resultado.append(p)
        else:
            resultado.append(p.capitalize())
    return " ".join(resultado)


def _validar_nome(nome_completo: str) -> tuple:
    """
    Valida se o nome possui pelo menos nome e sobrenome (2+ palavras).
    Retorna (válido, mensagem_erro)
    """
    nome = nome_completo.strip()
    if not nome:
        return False, "Nome é obrigatório"

    palavras = nome.split()
    if len(palavras) < 2:
        return False, "Por favor, insira nome e sobrenome. Ex: João Silva"

    return True, None


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


def _parse_data(texto: str):
    """Converte DD/MM/AAAA → YYYY-MM-DD. Aceita também YYYY-MM-DD direto."""
    t = texto.strip()
    if not t:
        return None
    if "/" in t:
        partes = t.split("/")
        if len(partes) == 3:
            d, m, a = partes
            return f"{a.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
    return t or None


# ── Busca JSON (autocomplete) ─────────────────────────────────────────────────

@router.get("/buscar", response_class=JSONResponse)
async def buscar_json(request: Request, nome: str = Query(""), excluir: int = Query(0)):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return JSONResponse([], status_code=401)
    if len(nome.strip()) < 2:
        return JSONResponse([])
    with conectar() as conn:
        termo = f"%{_normalizar(nome.strip())}%"
        rows = conn.execute(
            """SELECT id, nome_completo FROM pessoas
               WHERE norm(nome_completo) LIKE %s AND id != %s
               ORDER BY nome_completo LIMIT 10""",
            (termo, excluir)
        ).fetchall()
    return JSONResponse([{"id": r["id"], "nome": r["nome_completo"]} for r in rows])


# ── Lista / Busca ─────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def listar(request: Request, busca: str = Query("")):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        if busca.strip():
            termo = f"%{_normalizar(busca.strip())}%"
            rows = conn.execute(
                "SELECT id, nome_completo, telefone FROM pessoas WHERE norm(nome_completo) LIKE %s ORDER BY nome_completo",
                (termo,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, nome_completo, telefone FROM pessoas ORDER BY nome_completo"
            ).fetchall()
    return templates.TemplateResponse("pessoas/lista.html", {
        "request": request,
        "atendente": atendente,
        "pessoas": [dict(r) for r in rows],
        "busca": busca,
    })


# ── Novo ──────────────────────────────────────────────────────────────────────

@router.get("/novo", response_class=HTMLResponse)
async def form_novo(request: Request, next: str = Query("")):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("pessoas/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": None,
        "erro": None,
        "next": next,
    })


@router.post("/novo", response_class=HTMLResponse)
async def salvar_novo(
    request: Request,
    nome_completo: str = Form(...),
    data_nascimento: str = Form(""),
    deficiencia: int = Form(0),
    prioridade: int = Form(0),
    telefone: str = Form(""),
    email: str = Form(""),
    cep: str = Form(""),
    logradouro: str = Form(""),
    numero: str = Form(""),
    complemento: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    uf: str = Form(""),
    next: str = Form(""),
    acao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    # Validar nome (deve ter pelo menos 2 palavras)
    valido, erro = _validar_nome(nome_completo)
    if not valido:
        return templates.TemplateResponse("pessoas/form.html", {
            "request": request,
            "atendente": atendente,
            "registro": None,
            "erro": erro,
            "next": next,
        })

    nome = _capitalizar_nome(nome_completo.strip())
    dn = _parse_data(data_nascimento)
    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO pessoas (nome_apresentacao, nome_completo, data_nascimento,
               deficiencia, prioridade, telefone, email,
               cep, logradouro, numero, complemento, bairro, cidade, uf)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (nome, nome, dn, deficiencia, prioridade,
             telefone.strip(), email.strip().lower(),
             cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
             bairro.strip(), cidade.strip(), uf.strip()),
        )
        novo_id = cur.fetchone()["id"]
    if acao == "checkin":
        return RedirectResponse(url=f"/dia/checkin/{novo_id}", status_code=303)
    destino = next.strip() or "/cadastros/pessoas"
    return RedirectResponse(url=destino, status_code=303)


# ── Editar ────────────────────────────────────────────────────────────────────

@router.get("/{id}/editar", response_class=HTMLResponse)
async def form_editar(request: Request, id: int, next: str = Query("")):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute("SELECT * FROM pessoas WHERE id = %s", (id,)).fetchone()
    if not row:
        return RedirectResponse(url="/cadastros/pessoas", status_code=303)
    return templates.TemplateResponse("pessoas/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": dict(row),
        "erro": None,
        "next": next,
    })


@router.post("/{id}/editar", response_class=HTMLResponse)
async def salvar_editar(
    request: Request,
    id: int,
    nome_completo: str = Form(...),
    data_nascimento: str = Form(""),
    deficiencia: int = Form(0),
    prioridade: int = Form(0),
    telefone: str = Form(""),
    email: str = Form(""),
    cep: str = Form(""),
    logradouro: str = Form(""),
    numero: str = Form(""),
    complemento: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    uf: str = Form(""),
    next: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    # Validar nome (deve ter pelo menos 2 palavras)
    valido, erro = _validar_nome(nome_completo)
    if not valido:
        with conectar() as conn:
            row = conn.execute("SELECT * FROM pessoas WHERE id = %s", (id,)).fetchone()
        return templates.TemplateResponse("pessoas/form.html", {
            "request": request,
            "atendente": atendente,
            "registro": dict(row) if row else None,
            "erro": erro,
            "next": next,
        })

    nome_completo = _capitalizar_nome(nome_completo.strip())
    dn = _parse_data(data_nascimento)
    with conectar() as conn:
        conn.execute(
            """UPDATE pessoas SET nome_completo=%s, nome_apresentacao=%s,
               data_nascimento=%s, deficiencia=%s, prioridade=%s,
               telefone=%s, email=%s,
               cep=%s, logradouro=%s, numero=%s, complemento=%s, bairro=%s, cidade=%s, uf=%s
               WHERE id=%s""",
            (nome_completo.strip(), nome_completo.strip(),
             dn, deficiencia, prioridade,
             telefone.strip(), email.strip().lower(),
             cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
             bairro.strip(), cidade.strip(), uf.strip(), id),
        )
    destino = next.strip() or "/cadastros/pessoas"
    return RedirectResponse(url=destino, status_code=303)


# ── Laços ─────────────────────────────────────────────────────────────────────

@router.get("/{id}/lacos", response_class=HTMLResponse)
async def gerenciar_lacos(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        pessoa = conn.execute("SELECT * FROM pessoas WHERE id = %s", (id,)).fetchone()
        if not pessoa:
            return RedirectResponse(url="/cadastros/pessoas", status_code=303)
        lacos = conn.execute(
            """SELECT l.id, l.tipo_laco, p.nome_completo, p.id as relacionada_id
               FROM lacos l
               JOIN pessoas p ON p.id = l.pessoa_relacionada_id
               WHERE l.pessoa_id = %s""",
            (id,)
        ).fetchall()
    return templates.TemplateResponse("pessoas/lacos.html", {
        "request": request,
        "atendente": atendente,
        "pessoa": dict(pessoa),
        "lacos": [dict(l) for l in lacos],
        "erro": None,
    })


@router.post("/{id}/lacos", response_class=HTMLResponse)
async def adicionar_laco(
    request: Request,
    id: int,
    pessoa_relacionada_id: int = Form(...),
    tipo_laco: str = Form(...),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            """INSERT INTO lacos (pessoa_id, pessoa_relacionada_id, tipo_laco)
               VALUES (%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (id, pessoa_relacionada_id, tipo_laco.strip()),
        )
    return RedirectResponse(url=f"/cadastros/pessoas/{id}/lacos", status_code=303)


@router.post("/{id}/lacos/{laco_id}/remover")
async def remover_laco(request: Request, id: int, laco_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute("DELETE FROM lacos WHERE id = %s AND pessoa_id = %s", (laco_id, id))
    return RedirectResponse(url=f"/cadastros/pessoas/{id}/lacos", status_code=303)


# ── Ficha ──────────────────────────────────────────────────────────────────

@router.get("/{id}", response_class=HTMLResponse)
async def ficha_pessoa(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        pessoa = conn.execute("SELECT * FROM pessoas WHERE id = %s", (id,)).fetchone()
        if not pessoa:
            return RedirectResponse(url="/cadastros/pessoas", status_code=303)
        lacos = conn.execute(
            """SELECT l.tipo_laco, p.nome_completo, p.id as relacionada_id
               FROM lacos l
               JOIN pessoas p ON p.id = l.pessoa_relacionada_id
               WHERE l.pessoa_id = %s""",
            (id,)
        ).fetchall()
        planos = conn.execute(
            """SELECT pt.*, m.nome_completo as medium_nome
               FROM planos_tratamento pt
               JOIN plano_pessoas pp ON pp.plano_id = pt.id
               JOIN mediuns m ON m.id = pt.medium_id
               WHERE pp.pessoa_id = %s
               ORDER BY pt.data_inicio DESC""",
            (id,)
        ).fetchall()
        historico = conn.execute(
            """SELECT dt.data, c.hora_checkin,
                      c.codigo_passe, c.passe_realizado,
                      c.codigo_reiki, c.reiki_realizado,
                      c.codigo_acolhimento, c.acolhimento_realizado,
                      c.codigo_atendimento, c.atendimento_realizado,
                      m.nome_completo as medium_nome
               FROM checkins c
               JOIN dias_trabalho dt ON dt.id = c.dia_trabalho_id
               LEFT JOIN mediuns m ON m.id = c.medium_id
               WHERE c.pessoa_id = %s
               ORDER BY dt.data DESC, c.hora_checkin DESC""",
            (id,)
        ).fetchall()
    return templates.TemplateResponse("pessoas/ficha.html", {
        "request": request,
        "atendente": atendente,
        "pessoa": dict(pessoa),
        "lacos": [dict(l) for l in lacos],
        "planos": [dict(p) for p in planos],
        "historico": [dict(h) for h in historico],
    })


# ── Import tardio para evitar importação circular ──
from rotas.auth import obter_atendente_logado
