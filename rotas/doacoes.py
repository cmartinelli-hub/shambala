from datetime import date
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/doacoes")


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


def _obter_tipos(conn):
    """Retorna lista de tipos de doação ordenados por nome."""
    return conn.execute(
        "SELECT id, nome, descricao FROM tipos_doacao WHERE ativo = 1 ORDER BY nome"
    ).fetchall()


def _salvar_itens_doacao(conn, doacao_id, items_dict):
    """Salva os itens de uma doação a partir de dict {tipo_id: quantidade}."""
    # Remove itens antigos
    conn.execute("DELETE FROM doacao_itens WHERE doacao_id = %s", (doacao_id,))
    # Insere novos
    for tipo_id, qtd in items_dict.items():
        if qtd > 0:
            conn.execute(
                "INSERT INTO doacao_itens (doacao_id, tipo_doacao_id, quantidade) VALUES (%s, %s, %s)",
                (doacao_id, tipo_id, qtd)
            )


def _carregar_itens_doacao(conn, doacao_id):
    """Carrega os itens de uma doação como dict {tipo_id: quantidade}."""
    rows = conn.execute(
        "SELECT tipo_doacao_id, quantidade FROM doacao_itens WHERE doacao_id = %s",
        (doacao_id,)
    ).fetchall()
    return {row['tipo_doacao_id']: row['quantidade'] for row in rows}




# ── Tipos de Doação ──────────────────────────────────────────────────────────

@router.get("/tipos", response_class=HTMLResponse)
async def listar_tipos(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        tipos = conn.execute(
            "SELECT id, nome, descricao, ativo FROM tipos_doacao ORDER BY nome"
        ).fetchall()

    return templates.TemplateResponse("doacoes/tipos.html", {
        "request": request,
        "atendente": atendente,
        "tipos": [dict(t) for t in tipos],
    })


@router.post("/tipos/novo", response_class=HTMLResponse)
async def novo_tipo(
    request: Request,
    nome: str = Form(...),
    descricao: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            "INSERT INTO tipos_doacao (nome, descricao) VALUES (%s, %s)",
            (nome.strip(), descricao.strip() or None)
        )

    return RedirectResponse(url="/doacoes/tipos", status_code=303)


@router.post("/tipos/{id}/editar", response_class=HTMLResponse)
async def editar_tipo(
    request: Request,
    id: int,
    nome: str = Form(...),
    descricao: str = Form(""),
    ativo: int = Form(1),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            "UPDATE tipos_doacao SET nome = %s, descricao = %s, ativo = %s WHERE id = %s",
            (nome.strip(), descricao.strip() or None, ativo, id)
        )

    return RedirectResponse(url="/doacoes/tipos", status_code=303)


@router.post("/tipos/{id}/remover", response_class=HTMLResponse)
async def remover_tipo(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        # Verifica se há doações com este tipo
        tem_doacoes = conn.execute(
            "SELECT COUNT(*) AS c FROM doacao_itens WHERE tipo_doacao_id = %s", (id,)
        ).fetchone()["c"]

        if tem_doacoes > 0:
            # Apenas desativa ao invés de deletar
            conn.execute("UPDATE tipos_doacao SET ativo = 0 WHERE id = %s", (id,))
        else:
            # Se não há doações, pode deletar
            conn.execute("DELETE FROM tipos_doacao WHERE id = %s", (id,))

    return RedirectResponse(url="/doacoes/tipos", status_code=303)


def _tem_endereco_completo(pessoa: dict) -> bool:
    """Verifica se a pessoa tem endereço completo cadastrado."""
    return bool(
        pessoa.get("logradouro") and
        pessoa.get("numero") and
        pessoa.get("bairro") and
        pessoa.get("cidade") and
        pessoa.get("uf")
    )


# ── Lista de doações ──────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def listar_doacoes(
    request: Request,
    data_inicio: str = "",
    data_fim: str = "",
    entregue: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    filtros = []
    params = []

    if data_inicio:
        filtros.append("dc.data_entrega >= %s")
        params.append(data_inicio)

    if data_fim:
        filtros.append("dc.data_entrega <= %s")
        params.append(data_fim)

    if entregue == "s":
        filtros.append("dc.entregue = 1")
    elif entregue == "n":
        filtros.append("dc.entregue = 0")

    where = ""
    if filtros:
        where = "WHERE " + " AND ".join(filtros)

    with conectar() as conn:
        doacoes = conn.execute(
            f"""SELECT dc.*, p.nome_completo AS pessoa_nome
                FROM doacoes_cestas dc
                JOIN pessoas p ON p.id = dc.pessoa_id
                {where}
                ORDER BY dc.data_entrega DESC, dc.id DESC""",
            params,
        ).fetchall()

        # Carrega itens para cada doação
        doacoes_dict = []
        for d in doacoes:
            d_dict = dict(d)
            itens_doacao = conn.execute(
                """SELECT t.nome, di.quantidade
                   FROM doacao_itens di
                   JOIN tipos_doacao t ON t.id = di.tipo_doacao_id
                   WHERE di.doacao_id = %s
                   ORDER BY t.nome""",
                (d["id"],)
            ).fetchall()
            d_dict["itens_list"] = [dict(i) for i in itens_doacao]
            doacoes_dict.append(d_dict)

        # Resumo
        total_cestas = conn.execute(
            f"SELECT COUNT(*) AS c FROM doacoes_cestas dc {where}", params,
        ).fetchone()["c"]

        # Entregues
        where_entregues = f"{where + ' AND ' if where else 'WHERE'} dc.entregue = 1"
        entregues_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM doacoes_cestas dc {where_entregues}",
            params,
        ).fetchone()["c"]

    return templates.TemplateResponse("doacoes/lista.html", {
        "request": request,
        "atendente": atendente,
        "doacoes": doacoes_dict,
        "total_cestas": total_cestas,
        "entregues_count": entregues_count,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "entregue": entregue,
    })


# ── Nova doação ───────────────────────────────────────────────────────────────

@router.get("/nova", response_class=HTMLResponse)
async def form_nova_doacao(
    request: Request,
    pessoa_id: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    pessoa_selecionada = None
    with conectar() as conn:
        pessoas = conn.execute(
            "SELECT * FROM pessoas ORDER BY nome_completo LIMIT 500"
        ).fetchall()
        tipos = _obter_tipos(conn)

        if pessoa_id:
            pessoa_selecionada = conn.execute(
                "SELECT * FROM pessoas WHERE id = %s", (pessoa_id,)
            ).fetchone()

    return templates.TemplateResponse("doacoes/form.html", {
        "request": request,
        "atendente": atendente,
        "pessoas": [dict(p) for p in pessoas],
        "pessoa_selecionada": dict(pessoa_selecionada) if pessoa_selecionada else None,
        "tipos": [dict(t) for t in tipos],
        "itens_selecionados": {},
        "erro": None,
    })


@router.post("/nova", response_class=HTMLResponse)
async def salvar_nova_doacao(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    form_data = await request.form()
    pessoa_id = form_data.get("pessoa_id", "").strip()
    data_entrega = form_data.get("data_entrega", "").strip()
    observacao = form_data.get("observacao", "").strip()
    entregue = form_data.get("entregue", "0")

    if not pessoa_id:
        with conectar() as conn:
            pessoas = conn.execute(
                "SELECT * FROM pessoas ORDER BY nome_completo LIMIT 500"
            ).fetchall()
            tipos = _obter_tipos(conn)
        return templates.TemplateResponse("doacoes/form.html", {
            "request": request,
            "atendente": atendente,
            "pessoas": [dict(p) for p in pessoas],
            "pessoa_selecionada": None,
            "tipos": [dict(t) for t in tipos],
            "itens_selecionados": {},
            "erro": "Selecione uma pessoa.",
        })

    # Coleta itens selecionados (quantidade > 0)
    itens_dict = {}
    for key in form_data:
        if key.startswith("item_"):
            tipo_id = int(key.split("_")[1])
            qtd = int(form_data.get(key) or 0)
            if qtd > 0:
                itens_dict[tipo_id] = qtd

    if not itens_dict:
        with conectar() as conn:
            pessoas = conn.execute(
                "SELECT * FROM pessoas ORDER BY nome_completo LIMIT 500"
            ).fetchall()
            tipos = _obter_tipos(conn)
        return templates.TemplateResponse("doacoes/form.html", {
            "request": request,
            "atendente": atendente,
            "pessoas": [dict(p) for p in pessoas],
            "pessoa_selecionada": None,
            "tipos": [dict(t) for t in tipos],
            "itens_selecionados": {},
            "erro": "Selecione pelo menos um tipo de doação.",
        })

    with conectar() as conn:
        try:
            pessoa = conn.execute(
                "SELECT * FROM pessoas WHERE id = %s", (pessoa_id,)
            ).fetchone()

            # Validação: deve ter endereço completo
            if pessoa and not _tem_endereco_completo(dict(pessoa)):
                pessoas = conn.execute(
                    "SELECT * FROM pessoas ORDER BY nome_completo LIMIT 500"
                ).fetchall()
                tipos = _obter_tipos(conn)
                return templates.TemplateResponse("doacoes/form.html", {
                    "request": request,
                    "atendente": atendente,
                    "pessoas": [dict(p) for p in pessoas],
                    "pessoa_selecionada": dict(pessoa),
                    "tipos": [dict(t) for t in tipos],
                    "itens_selecionados": itens_dict,
                    "erro": f"⚠️ {pessoa['nome_completo']} não tem endereço completo cadastrado. "
                            "Preencha logradouro, número, bairro, cidade e UF antes de registrar a doação.",
                })

            data_ref = data_entrega or date.today().isoformat()
            cur = conn.execute(
                """INSERT INTO doacoes_cestas
                   (pessoa_id, data_entrega, observacao, entregue)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (
                    int(pessoa_id),
                    data_ref,
                    observacao or None,
                    1 if entregue == "1" else 0,
                ),
            )
            doacao_id = cur.fetchone()["id"]

            # Salva os itens (com transação automática - rollback se falhar)
            _salvar_itens_doacao(conn, doacao_id, itens_dict)
        except Exception as e:
            # Erro na transação? Rollback automático ao sair do with
            pessoas = conn.execute(
                "SELECT * FROM pessoas ORDER BY nome_completo LIMIT 500"
            ).fetchall()
            tipos = _obter_tipos(conn)
            return templates.TemplateResponse("doacoes/form.html", {
                "request": request,
                "atendente": atendente,
                "pessoas": [dict(p) for p in pessoas],
                "pessoa_selecionada": None,
                "tipos": [dict(t) for t in tipos],
                "itens_selecionados": itens_dict,
                "erro": f"❌ Erro ao salvar doação: {str(e)}",
            })

    return RedirectResponse(url="/doacoes", status_code=303)


# ── Marcar como entregue ──────────────────────────────────────────────────────

@router.post("/{id}/entregue", response_class=HTMLResponse)
async def marcar_entregue(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            "UPDATE doacoes_cestas SET entregue = 1 WHERE id = %s", (id,)
        )

    return RedirectResponse(url="/doacoes", status_code=303)


@router.post("/{id}/nao-entregue", response_class=HTMLResponse)
async def marcar_nao_entregue(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute(
            "UPDATE doacoes_cestas SET entregue = 0 WHERE id = %s", (id,)
        )

    return RedirectResponse(url="/doacoes", status_code=303)


# ── Editar ────────────────────────────────────────────────────────────────────

@router.get("/{id}/editar", response_class=HTMLResponse)
async def form_editar_doacao(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        doacao = conn.execute(
            """SELECT dc.*, p.nome_completo AS pessoa_nome
               FROM doacoes_cestas dc
               JOIN pessoas p ON p.id = dc.pessoa_id
               WHERE dc.id = %s""",
            (id,),
        ).fetchone()
        if not doacao:
            return RedirectResponse(url="/doacoes", status_code=303)

        tipos = _obter_tipos(conn)
        itens_dict = _carregar_itens_doacao(conn, id)

    return templates.TemplateResponse("doacoes/form_editar.html", {
        "request": request,
        "atendente": atendente,
        "doacao": dict(doacao),
        "tipos": [dict(t) for t in tipos],
        "itens_selecionados": itens_dict,
    })


@router.post("/{id}/editar", response_class=HTMLResponse)
async def salvar_editar_doacao(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    form_data = await request.form()
    data_entrega = form_data.get("data_entrega", "").strip()
    observacao = form_data.get("observacao", "").strip()
    entregue = form_data.get("entregue", "0")

    # Coleta itens selecionados
    itens_dict = {}
    for key in form_data:
        if key.startswith("item_"):
            tipo_id = int(key.split("_")[1])
            qtd = int(form_data.get(key) or 0)
            if qtd > 0:
                itens_dict[tipo_id] = qtd

    data_ref = data_entrega or date.today().isoformat()

    with conectar() as conn:
        conn.execute(
            """UPDATE doacoes_cestas
               SET data_entrega = %s, observacao = %s, entregue = %s
               WHERE id = %s""",
            (data_ref, observacao or None, 1 if entregue == "1" else 0, id),
        )

        # Atualiza itens
        _salvar_itens_doacao(conn, id, itens_dict)

    return RedirectResponse(url="/doacoes", status_code=303)


# ── Remover ───────────────────────────────────────────────────────────────────

@router.post("/{id}/remover", response_class=HTMLResponse)
async def remover_doacao(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        conn.execute("DELETE FROM doacoes_cestas WHERE id = %s", (id,))

    return RedirectResponse(url="/doacoes", status_code=303)


# ── Histórico de doações da pessoa ────────────────────────────────────────────

@router.get("/pessoa/{pessoa_id}", response_class=HTMLResponse)
async def historico_pessoa(request: Request, pessoa_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        pessoa = conn.execute(
            "SELECT id, nome_completo, email FROM pessoas WHERE id = %s",
            (pessoa_id,),
        ).fetchone()
        if not pessoa:
            return RedirectResponse(url="/doacoes", status_code=303)

        doacoes = conn.execute(
            """SELECT dc.*
               FROM doacoes_cestas dc
               WHERE dc.pessoa_id = %s
               ORDER BY dc.data_entrega DESC, dc.id DESC""",
            (pessoa_id,),
        ).fetchall()

        # Carrega itens para cada doação
        doacoes_dict = []
        for d in doacoes:
            d_dict = dict(d)
            itens_doacao = conn.execute(
                """SELECT t.nome, di.quantidade
                   FROM doacao_itens di
                   JOIN tipos_doacao t ON t.id = di.tipo_doacao_id
                   WHERE di.doacao_id = %s
                   ORDER BY t.nome""",
                (d["id"],)
            ).fetchall()
            d_dict["itens_list"] = [dict(i) for i in itens_doacao]
            doacoes_dict.append(d_dict)

    return templates.TemplateResponse("doacoes/pessoa.html", {
        "request": request,
        "atendente": atendente,
        "pessoa": dict(pessoa),
        "doacoes": doacoes_dict,
    })


# ── Relatório de prestações de conta ──────────────────────────────────────────

@router.get("/relatorio", response_class=HTMLResponse)
async def relatorio_doacoes(
    request: Request,
    data_inicio: str = "",
    data_fim: str = "",
    tipo_id: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    hoje = date.today()
    if not data_inicio:
        data_inicio = f"{hoje.year}-01-01"
    if not data_fim:
        data_fim = hoje.isoformat()

    with conectar() as conn:
        # Carrega tipos para filtro
        tipos = _obter_tipos(conn)

        # Query de doações
        filtro_tipo = ""
        params = [data_inicio, data_fim]
        if tipo_id:
            filtro_tipo = " AND EXISTS (SELECT 1 FROM doacao_itens di WHERE di.doacao_id = dc.id AND di.tipo_doacao_id = %s)"
            params.insert(2, int(tipo_id))

        doacoes = conn.execute(
            f"""SELECT dc.*, p.nome_completo AS pessoa_nome,
                      p.logradouro, p.numero, p.bairro, p.cidade, p.uf
               FROM doacoes_cestas dc
               JOIN pessoas p ON p.id = dc.pessoa_id
               WHERE dc.data_entrega >= %s AND dc.data_entrega <= %s {filtro_tipo}
               ORDER BY dc.data_entrega, p.nome_completo""",
            params,
        ).fetchall()

        # Carrega itens para cada doação
        doacoes_dict = []
        resumo_tipos = {}
        for d in doacoes:
            d_dict = dict(d)
            itens_doacao = conn.execute(
                """SELECT t.id, t.nome, di.quantidade
                   FROM doacao_itens di
                   JOIN tipos_doacao t ON t.id = di.tipo_doacao_id
                   WHERE di.doacao_id = %s
                   ORDER BY t.nome""",
                (d["id"],)
            ).fetchall()
            d_dict["itens_list"] = [dict(i) for i in itens_doacao]

            # Acumula resumo por tipo
            for item in itens_doacao:
                tipo_nome = item["nome"]
                resumo_tipos[tipo_nome] = resumo_tipos.get(tipo_nome, 0) + item["quantidade"]

            doacoes_dict.append(d_dict)

        entregues = sum(1 for d in doacoes_dict if d["entregue"])
        pendentes = sum(1 for d in doacoes_dict if not d["entregue"])

    return templates.TemplateResponse("doacoes/relatorio.html", {
        "request": request,
        "atendente": atendente,
        "doacoes": doacoes_dict,
        "tipos": [dict(t) for t in tipos],
        "tipo_id_selecionado": int(tipo_id) if tipo_id else None,
        "entregues": entregues,
        "pendentes": pendentes,
        "resumo_tipos": resumo_tipos,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
    })
