from datetime import date, datetime
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from banco import conectar, _normalizar
from rotas.auth import obter_atendente_logado

from templates_config import templates
router = APIRouter(prefix="/dia/checkin")


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


def _hoje():
    return date.today().isoformat()


def _dia_aberto(conn):
    return conn.execute(
        "SELECT * FROM dias_trabalho WHERE data = %s AND aberto = 1", (_hoje(),)
    ).fetchone()


def _senhas_em_uso(conn, dia_id: int, excluir_pessoa_id: int) -> dict[str, str]:
    """
    Retorna dict {senha: descricao} com todas as senhas já registradas no dia,
    ignorando checkins da própria pessoa (para permitir atualização).
    """
    rows = conn.execute(
        """SELECT codigo_passe, codigo_acolhimento, codigo_atendimento, codigo_reiki
           FROM checkins
           WHERE dia_trabalho_id = %s AND pessoa_id != %s""",
        (dia_id, excluir_pessoa_id)
    ).fetchall()
    em_uso = {}
    rotulos = {
        "codigo_passe": "Passe",
        "codigo_acolhimento": "Fraterno",
        "codigo_atendimento": "Atendimento",
        "codigo_reiki": "Reiki",
    }
    for row in rows:
        for col, rotulo in rotulos.items():
            val = row[col]
            if val and val not in em_uso:
                em_uso[val] = rotulo
    return em_uso


def _situacao_pessoa(conn, pessoa_id: int, hoje: str) -> dict:
    """Retorna situação atual da pessoa: agendamento hoje, plano ativo, etc."""
    # Agendamento hoje para esta pessoa
    agendamento = conn.execute(
        """SELECT a.id, a.plano_id, a.requer_passe, a.encaixe,
                  pt.medium_id, m.nome_completo as medium_nome,
                  pt.sessoes_realizadas, pt.sessoes_total, pt.frequencia
           FROM agendamentos a
           JOIN planos_tratamento pt ON pt.id = a.plano_id
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           JOIN mediuns m ON m.id = pt.medium_id
           WHERE pp.pessoa_id = %s AND a.data = %s AND a.status = 'agendado'
           LIMIT 1""",
        (pessoa_id, hoje)
    ).fetchone()

    # Plano ativo (qualquer)
    plano_ativo = conn.execute(
        """SELECT pt.id, pt.medium_id, pt.sessoes_realizadas, pt.sessoes_total,
                  pt.frequencia, m.nome_completo as medium_nome
           FROM planos_tratamento pt
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           JOIN mediuns m ON m.id = pt.medium_id
           WHERE pp.pessoa_id = %s AND pt.status = 'ativo'
           LIMIT 1""",
        (pessoa_id,)
    ).fetchone()

    # Próximo agendamento (se tiver plano ativo mas não hoje)
    proxima_sessao = None
    if plano_ativo and not agendamento:
        prox = conn.execute(
            "SELECT data FROM agendamentos WHERE plano_id=%s AND status='agendado' AND data>%s ORDER BY data LIMIT 1",
            (plano_ativo["id"], hoje)
        ).fetchone()
        proxima_sessao = prox["data"] if prox else None

    # Vagas disponíveis do médium hoje (para encaixe)
    vagas_info = None
    if plano_ativo and not agendamento:
        m = conn.execute(
            "SELECT vagas_dia FROM mediuns WHERE id=%s", (plano_ativo["medium_id"],)
        ).fetchone()
        if m:
            usadas = conn.execute(
                """SELECT COUNT(*) AS c FROM agendamentos a
                   JOIN planos_tratamento pt ON pt.id = a.plano_id
                   WHERE pt.medium_id = %s AND a.data = %s AND a.status IN ('agendado','realizado')""",
                (plano_ativo["medium_id"], hoje)
            ).fetchone()["c"]
            vagas_info = {
                "total": m["vagas_dia"],
                "usadas": usadas,
                "disponiveis": max(0, m["vagas_dia"] - usadas),
                "medium_id": plano_ativo["medium_id"],
                "medium_nome": plano_ativo["medium_nome"],
                "plano_id": plano_ativo["id"],
            }

    # Precisa de Atendimento Fraterno?
    tem_historico = conn.execute(
        """SELECT 1 FROM planos_tratamento pt
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           WHERE pp.pessoa_id = %s LIMIT 1""",
        (pessoa_id,)
    ).fetchone()

    return {
        "agendamento": dict(agendamento) if agendamento else None,
        "plano_ativo": dict(plano_ativo) if plano_ativo else None,
        "proxima_sessao": proxima_sessao,
        "vagas_info": vagas_info,
        "sugere_fraterno": not tem_historico or (not plano_ativo),
    }


# ── Busca ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def busca(request: Request, nome: str = Query(""), ok: str = Query("")):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        if not _dia_aberto(conn):
            return RedirectResponse(url="/dia", status_code=303)
        pessoas = []
        if nome.strip():
            termo = f"%{_normalizar(nome.strip())}%"
            pessoas = conn.execute(
                """SELECT id, nome_completo, telefone
                   FROM pessoas WHERE norm(nome_completo) LIKE %s
                   ORDER BY nome_completo""",
                (termo,)
            ).fetchall()
            pessoas = [dict(p) for p in pessoas]
    return templates.TemplateResponse("checkin/busca.html", {
        "request": request,
        "atendente": atendente,
        "nome": nome,
        "pessoas": pessoas,
        "ok": ok,
    })


# ── Formulário de check-in ────────────────────────────────────────────────────

@router.get("/{pessoa_id}", response_class=HTMLResponse)
async def form_checkin(request: Request, pessoa_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        dia = _dia_aberto(conn)
        if not dia:
            return RedirectResponse(url="/dia", status_code=303)

        pessoa = conn.execute("SELECT * FROM pessoas WHERE id = %s", (pessoa_id,)).fetchone()
        if not pessoa:
            return RedirectResponse(url="/dia/checkin", status_code=303)

        checkin_existente = conn.execute(
            "SELECT * FROM checkins WHERE dia_trabalho_id = %s AND pessoa_id = %s",
            (dia["id"], pessoa_id)
        ).fetchone()

        mediuns_hoje = conn.execute(
            """SELECT m.id, m.nome_completo FROM mediuns_dia md
               JOIN mediuns m ON m.id = md.medium_id
               WHERE md.dia_trabalho_id = %s ORDER BY m.nome_completo""",
            (dia["id"],)
        ).fetchall()

        situacao = _situacao_pessoa(conn, pessoa_id, _hoje())

    return templates.TemplateResponse("checkin/form.html", {
        "request": request,
        "atendente": atendente,
        "pessoa": dict(pessoa),
        "mediuns_hoje": [dict(m) for m in mediuns_hoje],
        "checkin_existente": dict(checkin_existente) if checkin_existente else None,
        "situacao": situacao,
        "erro": None,
    })


@router.post("/{pessoa_id}", response_class=HTMLResponse)
async def salvar_checkin(
    request: Request,
    pessoa_id: int,
    codigo_passe: str = Form(""),
    codigo_acolhimento: str = Form(""),
    codigo_atendimento: str = Form(""),
    codigo_reiki: str = Form(""),
    medium_id: str = Form(""),
    agendamento_id: str = Form(""),
    acompanhante_id: list[str] = Form(default=[]),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    codigo_passe = codigo_passe.strip() or None
    codigo_acolhimento = codigo_acolhimento.strip() or None
    codigo_atendimento = codigo_atendimento.strip() or None
    codigo_reiki = codigo_reiki.strip() or None
    medium_id_int = int(medium_id) if medium_id.strip() else None
    agendamento_id_int = int(agendamento_id) if agendamento_id.strip() else None
    acompanhantes = [int(x) for x in acompanhante_id if x.strip() and x.strip() != str(pessoa_id)]

    if not codigo_passe and not codigo_acolhimento and not codigo_atendimento and not codigo_reiki:
        with conectar() as conn:
            dia = _dia_aberto(conn)
            pessoa = conn.execute("SELECT * FROM pessoas WHERE id = %s", (pessoa_id,)).fetchone()
            mediuns_hoje = conn.execute(
                """SELECT m.id, m.nome_completo FROM mediuns_dia md
                   JOIN mediuns m ON m.id = md.medium_id
                   WHERE md.dia_trabalho_id = %s ORDER BY m.nome_completo""",
                (dia["id"],)
            ).fetchall()
            situacao = _situacao_pessoa(conn, pessoa_id, _hoje())
        return templates.TemplateResponse("checkin/form.html", {
            "request": request,
            "atendente": atendente,
            "pessoa": dict(pessoa),
            "mediuns_hoje": [dict(m) for m in mediuns_hoje],
            "checkin_existente": None,
            "situacao": situacao,
            "erro": "Informe ao menos uma senha (passe, Reiki, fraterno ou atendimento).",
        })

    # Atendimento mediúnico exige médium selecionado
    if codigo_atendimento and not medium_id_int:
        with conectar() as conn:
            dia = _dia_aberto(conn)
            pessoa = conn.execute("SELECT * FROM pessoas WHERE id = %s", (pessoa_id,)).fetchone()
            mediuns_hoje = conn.execute(
                """SELECT m.id, m.nome_completo FROM mediuns_dia md
                   JOIN mediuns m ON m.id = md.medium_id
                   WHERE md.dia_trabalho_id = %s ORDER BY m.nome_completo""",
                (dia["id"],)
            ).fetchall()
            situacao = _situacao_pessoa(conn, pessoa_id, _hoje())
        return templates.TemplateResponse("checkin/form.html", {
            "request": request,
            "atendente": atendente,
            "pessoa": dict(pessoa),
            "mediuns_hoje": [dict(m) for m in mediuns_hoje],
            "checkin_existente": None,
            "situacao": situacao,
            "erro": "Atendimento mediúnico exige a seleção de um médium responsável.",
        })

    # Médium atingiu limite de vagas?
    if codigo_atendimento and medium_id_int:
        with conectar() as conn:
            dia = _dia_aberto(conn)
            if dia:
                lim = conn.execute(
                    "SELECT vagas_dia FROM mediuns_dia WHERE dia_trabalho_id = %s AND medium_id = %s",
                    (dia["id"], medium_id_int)
                ).fetchone()
                if lim and lim["vagas_dia"]:
                    usados = conn.execute(
                        """SELECT COUNT(*) AS c FROM checkins
                           WHERE dia_trabalho_id = %s AND medium_id = %s
                             AND codigo_atendimento IS NOT NULL""",
                        (dia["id"], medium_id_int)
                    ).fetchone()["c"]
                    if usados >= lim["vagas_dia"]:
                        pessoa = conn.execute("SELECT * FROM pessoas WHERE id = %s", (pessoa_id,)).fetchone()
                        mediuns_hoje = conn.execute(
                            """SELECT m.id, m.nome_completo FROM mediuns_dia md
                               JOIN mediuns m ON m.id = md.medium_id
                               WHERE md.dia_trabalho_id = %s ORDER BY m.nome_completo""",
                            (dia["id"],)
                        ).fetchall()
                        situacao = _situacao_pessoa(conn, pessoa_id, _hoje())
                        return templates.TemplateResponse("checkin/form.html", {
                            "request": request,
                            "atendente": atendente,
                            "pessoa": dict(pessoa),
                            "mediuns_hoje": [dict(m) for m in mediuns_hoje],
                            "checkin_existente": None,
                            "situacao": situacao,
                            "erro": f"O médium atingiu o limite de {lim['vagas_dia']} atendimentos hoje.",
                        })

    hora = datetime.now().strftime("%H:%M")

    with conectar() as conn:
        dia = _dia_aberto(conn)
        if not dia:
            return RedirectResponse(url="/dia", status_code=303)

        # ── Validar senhas duplicadas no dia ──────────────────────────────────
        em_uso = _senhas_em_uso(conn, dia["id"], pessoa_id)
        conflitos = []
        for senha, campo in [
            (codigo_passe,        "Passe"),
            (codigo_acolhimento,  "Fraterno"),
            (codigo_atendimento,  "Atendimento"),
            (codigo_reiki,        "Reiki"),
        ]:
            if senha and senha in em_uso:
                conflitos.append(f'Senha "{senha}" ({campo}) já está em uso como {em_uso[senha]} neste dia.')

        if conflitos:
            pessoa = conn.execute("SELECT * FROM pessoas WHERE id = %s", (pessoa_id,)).fetchone()
            mediuns_hoje = conn.execute(
                """SELECT m.id, m.nome_completo FROM mediuns_dia md
                   JOIN mediuns m ON m.id = md.medium_id
                   WHERE md.dia_trabalho_id = %s ORDER BY m.nome_completo""",
                (dia["id"],)
            ).fetchall()
            checkin_existente = conn.execute(
                "SELECT * FROM checkins WHERE dia_trabalho_id = %s AND pessoa_id = %s",
                (dia["id"], pessoa_id)
            ).fetchone()
            situacao = _situacao_pessoa(conn, pessoa_id, _hoje())
            return templates.TemplateResponse("checkin/form.html", {
                "request": request,
                "atendente": atendente,
                "pessoa": dict(pessoa),
                "mediuns_hoje": [dict(m) for m in mediuns_hoje],
                "checkin_existente": dict(checkin_existente) if checkin_existente else None,
                "situacao": situacao,
                "erro": " | ".join(conflitos),
            })

        # Descobrir plano_id pelo agendamento ou pelo plano ativo
        plano_id_int = None
        if agendamento_id_int:
            ag = conn.execute("SELECT plano_id FROM agendamentos WHERE id=%s", (agendamento_id_int,)).fetchone()
            if ag:
                plano_id_int = ag["plano_id"]
        elif medium_id_int and codigo_atendimento:
            pl = conn.execute(
                """SELECT pt.id FROM planos_tratamento pt
                   JOIN plano_pessoas pp ON pp.plano_id = pt.id
                   WHERE pp.pessoa_id = %s AND pt.medium_id = %s AND pt.status = 'ativo'
                   LIMIT 1""",
                (pessoa_id, medium_id_int)
            ).fetchone()
            if pl:
                plano_id_int = pl["id"]

        existente = conn.execute(
            "SELECT id FROM checkins WHERE dia_trabalho_id = %s AND pessoa_id = %s",
            (dia["id"], pessoa_id)
        ).fetchone()

        if existente:
            conn.execute(
                """UPDATE checkins SET
                   codigo_passe        = COALESCE(codigo_passe, %s),
                   codigo_acolhimento  = COALESCE(codigo_acolhimento, %s),
                   codigo_atendimento  = COALESCE(codigo_atendimento, %s),
                   codigo_reiki        = COALESCE(codigo_reiki, %s),
                   medium_id           = COALESCE(medium_id, %s),
                   agendamento_id      = COALESCE(agendamento_id, %s),
                   plano_id            = COALESCE(plano_id, %s)
                   WHERE id = %s""",
                (codigo_passe, codigo_acolhimento, codigo_atendimento, codigo_reiki,
                 medium_id_int, agendamento_id_int, plano_id_int, existente["id"])
            )
            checkin_id = existente["id"]
        else:
            cur = conn.execute(
                """INSERT INTO checkins
                   (dia_trabalho_id, pessoa_id, hora_checkin,
                    codigo_passe, codigo_acolhimento, codigo_atendimento, codigo_reiki,
                    medium_id, agendamento_id, plano_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (dia["id"], pessoa_id, hora,
                 codigo_passe, codigo_acolhimento, codigo_atendimento, codigo_reiki,
                 medium_id_int, agendamento_id_int, plano_id_int)
            )
            checkin_id = cur.fetchone()["id"]

        # Acompanhantes — check-in com a mesma senha de atendimento
        if acompanhantes and codigo_atendimento:
            for ac_id in acompanhantes:
                if ac_id == pessoa_id:
                    continue
                ex_ac = conn.execute(
                    "SELECT id FROM checkins WHERE dia_trabalho_id = %s AND pessoa_id = %s",
                    (dia["id"], ac_id)
                ).fetchone()
                if ex_ac:
                    conn.execute(
                        """UPDATE checkins SET
                           codigo_atendimento = COALESCE(codigo_atendimento, %s),
                           medium_id = COALESCE(medium_id, %s),
                           plano_id  = COALESCE(plano_id, %s)
                           WHERE id = %s""",
                        (codigo_atendimento, medium_id_int, plano_id_int, ex_ac["id"])
                    )
                else:
                    conn.execute(
                        """INSERT INTO checkins
                           (dia_trabalho_id, pessoa_id, hora_checkin,
                            codigo_atendimento, medium_id, plano_id)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (dia["id"], ac_id, hora,
                         codigo_atendimento, medium_id_int, plano_id_int)
                    )

    with conectar() as conn:
        p = conn.execute("SELECT nome_completo FROM pessoas WHERE id=%s", (pessoa_id,)).fetchone()
        nome_ok = p["nome_completo"] if p else ""
    import urllib.parse
    return RedirectResponse(url=f"/dia/checkin?ok={urllib.parse.quote(nome_ok)}", status_code=303)
