from fastapi import APIRouter, Request, Form, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import os
import uuid
from pathlib import Path
from banco import conectar, _normalizar
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/cadastros/pessoas")

# Configuração de upload de fotos
FOTOS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fotos")
os.makedirs(FOTOS_DIR, exist_ok=True)
EXTENSOES_PERMITIDAS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TAMANHO_MAXIMO = 5 * 1024 * 1024  # 5MB


def _salvar_foto(file: UploadFile, pessoa_id: int, foto_existente: str = None) -> str:
    """Salva foto da pessoa e retorna o caminho relativo. Remove foto antiga se existir."""
    # Remover foto antiga
    if foto_existente and os.path.exists(os.path.join(FOTOS_DIR, foto_existente)):
        try:
            os.remove(os.path.join(FOTOS_DIR, foto_existente))
        except OSError:
            pass

    # Gerar nome único
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in EXTENSOES_PERMITIDAS:
        ext = ".jpg"
    nome_arquivo = f"pessoa_{pessoa_id}_{uuid.uuid4().hex[:8]}{ext}"
    caminho = os.path.join(FOTOS_DIR, nome_arquivo)

    # Salvar arquivo
    with open(caminho, "wb") as f:
        conteudo = file.file.read()
        if len(conteudo) > TAMANHO_MAXIMO:
            raise ValueError("Arquivo muito grande (máx. 5MB)")
        f.write(conteudo)

    return nome_arquivo


# Rota para placeholder SVG (fallback quando não há foto)
@router.get("/foto-placeholder/{inicial}")
async def foto_placeholder(inicial: str):
    """Gera placeholder SVG com a inicial da pessoa."""
    import html as _html
    char = _html.escape(inicial[0].upper()) if inicial else "?"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
        <defs>
            <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" style="stop-color:#1a5fa8"/>
                <stop offset="100%" style="stop-color:#2d7dd2"/>
            </linearGradient>
        </defs>
        <circle cx="100" cy="100" r="100" fill="url(#g)"/>
        <text x="100" y="115" text-anchor="middle" fill="white"
              font-family="system-ui,sans-serif" font-size="90"
              font-weight="bold">{char}</text>
    </svg>'''
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

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


# ── Autocomplete ──────────────────────────────────────────────────────────────

@router.get("/api/similares", response_class=JSONResponse)
async def api_similares(q: str = Query("")):
    if len(q.strip()) < 2:
        return JSONResponse([])
    with conectar() as conn:
        termo = f"%{_normalizar(q.strip())}%"
        rows = conn.execute(
            "SELECT id, nome_completo FROM pessoas WHERE norm(nome_completo) LIKE %s ORDER BY nome_completo LIMIT 10",
            (termo,)
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
                "SELECT id, nome_completo, telefone, foto_pessoa FROM pessoas WHERE norm(nome_completo) LIKE %s ORDER BY nome_completo",
                (termo,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, nome_completo, telefone, foto_pessoa FROM pessoas ORDER BY nome_completo"
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
    foto: UploadFile = File(None),
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
    foto_pessoa = None

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

        # Salvar foto se enviada
        if foto and getattr(foto, "filename", None):
            try:
                foto_pessoa = _salvar_foto(foto, novo_id)
                conn.execute(
                    "UPDATE pessoas SET foto_pessoa = %s WHERE id = %s",
                    (foto_pessoa, novo_id)
                )
            except ValueError as e:
                pass  # Silenciosamente ignora erro de tamanho

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
    remover_foto: str = Form(""),
    foto: UploadFile = File(None),
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
        # Buscar foto existente
        existente = conn.execute(
            "SELECT foto_pessoa FROM pessoas WHERE id = %s", (id,)
        ).fetchone()
        foto_existente = existente["foto_pessoa"] if existente else None

        # Remover foto se solicitado
        if remover_foto == "1" and foto_existente:
            if os.path.exists(os.path.join(FOTOS_DIR, foto_existente)):
                try:
                    os.remove(os.path.join(FOTOS_DIR, foto_existente))
                except OSError:
                    pass
            foto_existente = None

        # Salvar nova foto se enviada
        if foto and getattr(foto, "filename", None):
            try:
                foto_existente = _salvar_foto(foto, id, foto_existente)
            except ValueError:
                pass

        conn.execute(
            """UPDATE pessoas SET nome_completo=%s, nome_apresentacao=%s,
               data_nascimento=%s, deficiencia=%s, prioridade=%s,
               telefone=%s, email=%s,
               cep=%s, logradouro=%s, numero=%s, complemento=%s, bairro=%s, cidade=%s, uf=%s,
               foto_pessoa=%s
               WHERE id=%s""",
            (nome_completo.strip(), nome_completo.strip(),
             dn, deficiencia, prioridade,
             telefone.strip(), email.strip().lower(),
             cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
             bairro.strip(), cidade.strip(), uf.strip(),
             foto_existente, id),
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
        "e_admin": obter_atendente_logado(request) and _e_admin(request),
    })


# ── Exclusão ────────────────────────────────────────────────────────────────

@router.post("/{id}/remover", response_class=HTMLResponse)
async def remover_pessoa(request: Request, id: int):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return RedirectResponse(url="/login", status_code=303)
    if not _e_admin(request):
        return HTMLResponse(status_code=403, content="Acesso negado.")

    with conectar() as conn:
        pessoa = conn.execute(
            "SELECT id, nome_completo, foto_pessoa FROM pessoas WHERE id = %s", (id,)
        ).fetchone()
        if not pessoa:
            return RedirectResponse(url="/cadastros/pessoas", status_code=303)

        vinculos = {
            "check-ins": conn.execute(
                "SELECT COUNT(*) AS c FROM checkins WHERE pessoa_id = %s", (id,)
            ).fetchone()["c"],
            "planos de tratamento": conn.execute(
                "SELECT COUNT(*) AS c FROM plano_pessoas WHERE pessoa_id = %s", (id,)
            ).fetchone()["c"],
            "movimentações financeiras": conn.execute(
                "SELECT COUNT(*) AS c FROM financeiro_movimentacoes WHERE pessoa_id = %s", (id,)
            ).fetchone()["c"],
            "empréstimos": conn.execute(
                "SELECT COUNT(*) AS c FROM emprestimos WHERE pessoa_id = %s", (id,)
            ).fetchone()["c"],
            "doações": conn.execute(
                "SELECT COUNT(*) AS c FROM doacoes_cestas WHERE pessoa_id = %s", (id,)
            ).fetchone()["c"],
        }
        impedimentos = [k for k, v in vinculos.items() if v > 0]
        if impedimentos:
            msg = "Não é possível excluir: pessoa possui " + ", ".join(impedimentos)
            return RedirectResponse(
                url=f"/cadastros/pessoas/{id}?erro={msg}", status_code=303
            )

        if pessoa["foto_pessoa"]:
            foto_path = Path("static/fotos") / pessoa["foto_pessoa"]
            if foto_path.exists():
                foto_path.unlink()

        conn.execute(
            "DELETE FROM lacos WHERE pessoa_id = %s OR pessoa_relacionada_id = %s",
            (id, id)
        )
        conn.execute("DELETE FROM pessoas WHERE id = %s", (id,))

    return RedirectResponse(url="/cadastros/pessoas", status_code=303)


# ── Import tardio para evitar importação circular ──
from rotas.auth import obter_atendente_logado, e_admin as _e_admin
