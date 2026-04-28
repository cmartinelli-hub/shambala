import requests
from datetime import date
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from banco import conectar
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/biblioteca")


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


# ── Lista de livros ───────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def listar_livros(request: Request, busca: str = ""):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        if busca.strip():
            termo = f"%{busca.strip().lower()}%"
            livro_rows = conn.execute(
                """SELECT l.*,
                          (SELECT COUNT(*) FROM emprestimos WHERE livro_id = l.id AND data_devolucao IS NULL) AS emprestados
                   FROM livros l
                   WHERE LOWER(l.titulo) LIKE %s OR LOWER(l.autor) LIKE %s OR l.isbn = %s
                   ORDER BY l.titulo""",
                (termo, termo, busca.strip()),
            ).fetchall()
        else:
            livro_rows = conn.execute(
                """SELECT l.*,
                          (SELECT COUNT(*) FROM emprestimos WHERE livro_id = l.id AND data_devolucao IS NULL) AS emprestados
                   FROM livros l
                   ORDER BY l.titulo"""
            ).fetchall()

    livros = [dict(r) for r in livro_rows]
    for lv in livros:
        lv["disponivel"] = lv["quantidade"] - lv["emprestados"]

    return templates.TemplateResponse("biblioteca/livros.html", {
        "request": request,
        "atendente": atendente,
        "livros": livros,
        "busca": busca,
    })


# ── Busca ISBN na API ────────────────────────────────────────────────────────

@router.get("/isbn/{isbn}")
async def buscar_isbn(isbn: str):
    """Busca dados do livro pela API Open Library."""
    dados = {"isbn": isbn, "titulo": "", "autor": "", "editora": "", "ano": None, "edicao": ""}
    try:
        resp = requests.get(f"https://openlibrary.org/isbn/{isbn}.json", timeout=5)
        if resp.status_code == 200:
            info = resp.json()
            dados["titulo"] = info.get("title", "")
            autores = info.get("authors", [])
            if autores:
                author_key = autores[0].get("key", "")
                if author_key:
                    # Busca nome do autor
                    autor_resp = requests.get(f"https://openlibrary.org{author_key}.json", timeout=5)
                    if autor_resp.status_code == 200:
                        dados["autor"] = autor_resp.json().get("name", "")
            dados["editora"] = ", ".join(info.get("publishers", []))[:100] if info.get("publishers") else ""
            datas = info.get("publish_date", "")
            # Tenta extrair ano
            for part in datas.split():
                if part.isdigit() and len(part) == 4:
                    dados["ano"] = int(part)
                    break
            dados["edicao"] = ""
    except Exception:
        return JSONResponse(dados)

    return JSONResponse(dados)


# ── Novo livro ────────────────────────────────────────────────────────────────

@router.get("/livro/novo", response_class=HTMLResponse)
async def form_novo_livro(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("biblioteca/livro_form.html", {
        "request": request,
        "atendente": atendente,
        "registro": None,
    })


@router.post("/livro/novo", response_class=HTMLResponse)
async def salvar_livro(
    request: Request,
    isbn: str = Form(""),
    titulo: str = Form(...),
    autor: str = Form(""),
    editora: str = Form(""),
    ano: str = Form(""),
    edicao: str = Form(""),
    quantidade: str = Form("1"),
    preco_venda: str = Form("0"),
    observacao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            """INSERT INTO livros
               (isbn, titulo, autor, editora, ano, edicao, quantidade, preco_venda, observacao)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                isbn.strip() or None,
                titulo.strip(),
                autor.strip() or None,
                editora.strip() or None,
                int(ano) if ano.strip() else None,
                edicao.strip() or None,
                int(quantidade or 1),
                float(preco_venda or 0),
                observacao.strip() or None,
            ),
        )

    return RedirectResponse(url="/biblioteca", status_code=303)


# ── Editar livro ──────────────────────────────────────────────────────────────

@router.get("/livro/{id}/editar", response_class=HTMLResponse)
async def form_editar_livro(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        row = conn.execute("SELECT * FROM livros WHERE id = %s", (id,)).fetchone()
    if not row:
        return RedirectResponse(url="/biblioteca", status_code=303)

    return templates.TemplateResponse("biblioteca/livro_form.html", {
        "request": request,
        "atendente": atendente,
        "registro": dict(row),
    })


@router.post("/livro/{id}/editar", response_class=HTMLResponse)
async def salvar_edicao_livro(
    request: Request,
    id: int,
    isbn: str = Form(""),
    titulo: str = Form(...),
    autor: str = Form(""),
    editora: str = Form(""),
    ano: str = Form(""),
    edicao: str = Form(""),
    quantidade: str = Form("1"),
    preco_venda: str = Form("0"),
    observacao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            """UPDATE livros SET
               isbn=%s, titulo=%s, autor=%s, editora=%s, ano=%s,
               edicao=%s, quantidade=%s, preco_venda=%s, observacao=%s
               WHERE id=%s""",
            (
                isbn.strip() or None, titulo.strip(), autor.strip() or None,
                editora.strip() or None, int(ano) if ano.strip() else None,
                edicao.strip() or None, int(quantidade or 1),
                float(preco_venda or 0), observacao.strip() or None, id,
            ),
        )

    return RedirectResponse(url="/biblioteca", status_code=303)


# ── Remover livro ─────────────────────────────────────────────────────────────

@router.post("/livro/{id}/remover", response_class=HTMLResponse)
async def remover_livro(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        # Verifica se há empréstimos ativos
        ativos = conn.execute(
            "SELECT COUNT(*) AS c FROM emprestimos WHERE livro_id = %s AND data_devolucao IS NULL",
            (id,),
        ).fetchone()["c"]
        if ativos > 0:
            return RedirectResponse(url="/biblioteca", status_code=303)

        conn.execute("DELETE FROM livros WHERE id = %s", (id,))

    return RedirectResponse(url="/biblioteca", status_code=303)


# ── Emprestar livro ───────────────────────────────────────────────────────────

def _pessoa_cadastro_completo(pessoa: dict) -> tuple[bool, list]:
    """Valida se a pessoa tem CPF, telefone, e-mail e endereço."""
    faltando = []
    if not pessoa.get("cpf"):
        faltando.append("CPF")
    if not pessoa.get("telefone"):
        faltando.append("Telefone")
    if not pessoa.get("email"):
        faltando.append("E-mail")
    if not (pessoa.get("logradouro") and pessoa.get("numero") and pessoa.get("cidade") and pessoa.get("uf")):
        faltando.append("Endereço completo")
    return len(faltando) == 0, faltando


@router.get("/emprestimo/novo", response_class=HTMLResponse)
async def form_novo_emprestimo(
    request: Request,
    livro_id: str = "",
    pessoa_id: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        livros = conn.execute(
            "SELECT id, titulo, autor, quantidade FROM livros WHERE quantidade > 0 ORDER BY titulo"
        ).fetchall()
        pessoas = conn.execute(
            "SELECT id, nome_completo, cpf, telefone, email, logradouro, numero, cidade, uf FROM pessoas ORDER BY nome_completo LIMIT 500"
        ).fetchall()

    livro_sel = None
    if livro_id:
        livro_sel = conn.execute(
            "SELECT * FROM livros WHERE id = %s", (livro_id,)
        ).fetchone()

    return templates.TemplateResponse("biblioteca/emprestimo_form.html", {
        "request": request,
        "atendente": atendente,
        "livros": [dict(l) for l in livros],
        "pessoas": [dict(p) for p in pessoas],
        "livro_selecionado": dict(livro_sel) if livro_sel else None,
        "pessoa_selecionada": None,
        "erro": None,
    })


@router.post("/emprestimo/novo", response_class=HTMLResponse)
async def salvar_emprestimo(
    request: Request,
    livro_id: str = Form(""),
    pessoa_id: str = Form(""),
    observacao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    if not livro_id or not pessoa_id:
        with conectar() as conn:
            livros = conn.execute(
                "SELECT id, titulo, autor, quantidade FROM livros ORDER BY titulo"
            ).fetchall()
            pessoas = conn.execute(
                "SELECT id, nome_completo FROM pessoas ORDER BY nome_completo LIMIT 500"
            ).fetchall()
        return templates.TemplateResponse("biblioteca/emprestimo_form.html", {
            "request": request,
            "atendente": atendente,
            "livros": [dict(l) for l in livros],
            "pessoas": [dict(p) for p in pessoas],
            "livro_selecionado": None,
            "pessoa_selecionada": None,
            "erro": "Selecione livro e pessoa.",
        })

    with conectar() as conn:
        livro = conn.execute(
            "SELECT * FROM livros WHERE id = %s", (livro_id,)
        ).fetchone()
        pessoa = conn.execute(
            "SELECT * FROM pessoas WHERE id = %s", (pessoa_id,)
        ).fetchone()

        if not livro or not pessoa:
            return RedirectResponse(url="/biblioteca", status_code=303)

        # Validação: cadastro completo
        completo, faltando = _pessoa_cadastro_completo(dict(pessoa))
        if not completo:
            pessoas = conn.execute(
                "SELECT id, nome_completo FROM pessoas ORDER BY nome_completo LIMIT 500"
            ).fetchall()
            return templates.TemplateResponse("biblioteca/emprestimo_form.html", {
                "request": request,
                "atendente": atendente,
                "livros": [dict(l) for l in conn.execute("SELECT id, titulo, autor, quantidade FROM livros ORDER BY titulo").fetchall()],
                "pessoas": [dict(p) for p in pessoas],
                "livro_selecionado": dict(livro),
                "pessoa_selecionada": dict(pessoa),
                "erro": f"Faltando no cadastro de {pessoa['nome_completo']}: {', '.join(faltando)}. "
                        "Complete o cadastro antes de emprestar.",
            })

        # Verifica disponibilidade
        disponiveis = conn.execute(
            """SELECT l.quantidade - COALESCE(e.tot, 0) AS disp
               FROM livros l
               LEFT JOIN (SELECT livro_id, COUNT(*) AS tot FROM emprestimos WHERE data_devolucao IS NULL GROUP BY livro_id) e
                 ON e.livro_id = l.id
               WHERE l.id = %s""",
            (livro_id,),
        ).fetchone()

        if not disponiveis or disponiveis["disp"] <= 0:
            return RedirectResponse(url="/biblioteca/emprestimo/novo", status_code=303)

        conn.execute(
            """INSERT INTO emprestimos (livro_id, pessoa_id, observacao)
               VALUES (%s, %s, %s)""",
            (int(livro_id), int(pessoa_id), observacao.strip() or None),
        )

    return RedirectResponse(url="/biblioteca", status_code=303)


# ── Devolver livro ────────────────────────────────────────────────────────────

@router.post("/emprestimo/{id}/devolver", response_class=HTMLResponse)
async def devolver_emprestimo(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            "UPDATE emprestimos SET data_devolucao = %s WHERE id = %s",
            (date.today().isoformat(), id),
        )

    return RedirectResponse(url="/biblioteca", status_code=303)


# ── Vender livro ──────────────────────────────────────────────────────────────

@router.post("/livro/{id}/vender", response_class=HTMLResponse)
async def vender_livro(
    request: Request,
    id: int,
    quantidade: str = Form("1"),
    pessoa_id: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    qtd = int(quantidade or 1)

    with conectar() as conn:
        livro = conn.execute("SELECT * FROM livros WHERE id = %s", (id,)).fetchone()
        if not livro or livro["quantidade"] < qtd:
            return RedirectResponse(url="/biblioteca", status_code=303)

        # Baixa no estoque
        conn.execute(
            "UPDATE livros SET quantidade = quantidade - %s WHERE id = %s",
            (qtd, id),
        )

        # Registra venda
        valor_total = float(livro["preco_venda"]) * qtd
        row = conn.execute(
            """INSERT INTO vendas_livros (livro_id, pessoa_id, quantidade, valor_total, observacao)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING id""",
            (id, int(pessoa_id) if pessoa_id else None, qtd, valor_total, None),
        ).fetchone()

        # Vincula com financeiro
        titulo = livro["titulo"][:50]
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, descricao, pessoa_id, status)
               VALUES ('entrada', 'livro', %s, %s, %s, 'pago')""",
            (valor_total, f"Venda: {titulo}", int(pessoa_id) if pessoa_id else None),
        )

    return RedirectResponse(url="/biblioteca", status_code=303)


# ── Histórico de empréstimos da pessoa ────────────────────────────────────────

@router.get("/pessoa/{pessoa_id}", response_class=HTMLResponse)
async def historico_pessoa(request: Request, pessoa_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        pessoa = conn.execute(
            "SELECT id, nome_completo FROM pessoas WHERE id = %s", (pessoa_id,)
        ).fetchone()
        if not pessoa:
            return RedirectResponse(url="/biblioteca", status_code=303)

        emprestimos = conn.execute(
            """SELECT e.*, l.titulo, l.autor
               FROM emprestimos e
               JOIN livros l ON l.id = e.livro_id
               WHERE e.pessoa_id = %s
               ORDER BY e.data_emprestimo DESC, e.id DESC""",
            (pessoa_id,),
        ).fetchall()

    return templates.TemplateResponse("biblioteca/pessoa.html", {
        "request": request,
        "atendente": atendente,
        "pessoa": dict(pessoa),
        "emprestimos": [dict(e) for e in emprestimos],
    })
