from datetime import date
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar
from rotas.auth import obter_atendente_logado
from rotas.chamada import gerenciador
from backup import fazer_backup

from templates_config import templates
router = APIRouter(prefix="/dia")

# Expressão SQL de prioridade: idoso 70+, deficiência, prioridade manual
_ORDEM_PRIORIDADE = """(
    p.prioridade + p.deficiencia +
    CASE WHEN p.data_nascimento IS NOT NULL AND
         (EXTRACT(YEAR FROM AGE(CURRENT_DATE, p.data_nascimento::date)) >= 70)
         THEN 1 ELSE 0 END
) DESC, c.hora_checkin ASC"""


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


def _hoje():
    return date.today().isoformat()


def _dia_hoje(conn):
    return conn.execute(
        "SELECT * FROM dias_trabalho WHERE data = %s", (_hoje(),)
    ).fetchone()


# ── Principal ─────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def pagina_dia(request: Request, backup: str = ""):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        dia = _dia_hoje(conn)

        if not dia:
            mediuns = conn.execute(
                "SELECT id, nome_completo FROM mediuns WHERE ativo = 1 ORDER BY nome_completo"
            ).fetchall()
            return templates.TemplateResponse("dia/abrir.html", {
                "request": request,
                "atendente": atendente,
                "mediuns": [dict(m) for m in mediuns],
                "hoje": _hoje(),
            })

        dia = dict(dia)

        if not dia["aberto"]:
            return _resumo_encerrado(request, atendente, conn, dia, backup)

        return _painel(request, atendente, conn, dia)


def _painel(request, atendente, conn, dia):
    mediuns_dia = conn.execute(
        """SELECT m.id, m.nome_completo
           FROM mediuns_dia md JOIN mediuns m ON m.id = md.medium_id
           WHERE md.dia_trabalho_id = %s ORDER BY m.nome_completo""",
        (dia["id"],)
    ).fetchall()
    todos_mediuns = conn.execute(
        "SELECT id, nome_completo FROM mediuns WHERE ativo = 1 ORDER BY nome_completo"
    ).fetchall()

    total_checkins = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s", (dia["id"],)
    ).fetchone()["c"]

    passe_aguardando = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND codigo_passe IS NOT NULL AND passe_realizado = 0",
        (dia["id"],)
    ).fetchone()["c"]

    passe_realizado = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND passe_realizado = 1",
        (dia["id"],)
    ).fetchone()["c"]

    acolh_aguardando = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND codigo_acolhimento IS NOT NULL AND acolhimento_realizado = 0",
        (dia["id"],)
    ).fetchone()["c"]

    acolh_realizado = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND acolhimento_realizado = 1",
        (dia["id"],)
    ).fetchone()["c"]

    atend_aguardando = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND codigo_atendimento IS NOT NULL AND atendimento_realizado = 0",
        (dia["id"],)
    ).fetchone()["c"]

    atend_realizado = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND atendimento_realizado = 1",
        (dia["id"],)
    ).fetchone()["c"]

    reiki_aguardando = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND codigo_reiki IS NOT NULL AND reiki_realizado = 0",
        (dia["id"],)
    ).fetchone()["c"]

    reiki_realizado = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND reiki_realizado = 1",
        (dia["id"],)
    ).fetchone()["c"]

    # Por médium
    filas_mediuns = []
    for m in mediuns_dia:
        aguardando = conn.execute(
            """SELECT COUNT(*) AS c FROM checkins
               WHERE dia_trabalho_id = %s AND medium_id = %s
                 AND atendimento_realizado = 0 AND codigo_atendimento IS NOT NULL""",
            (dia["id"], m["id"])
        ).fetchone()["c"]
        realizados = conn.execute(
            """SELECT COUNT(*) AS c FROM checkins
               WHERE dia_trabalho_id = %s AND medium_id = %s
                 AND atendimento_realizado = 1 AND codigo_atendimento IS NOT NULL""",
            (dia["id"], m["id"])
        ).fetchone()["c"]
        total_usados = aguardando + realizados
        vagas_restantes = None
        fim = False
        if m.get("vagas_dia"):
            vagas_restantes = max(0, m["vagas_dia"] - total_usados)
            fim = vagas_restantes == 0
        filas_mediuns.append({
            "id": m["id"],
            "nome_completo": m["nome_completo"],
            "aguardando": aguardando,
            "vagas_restantes": vagas_restantes,
            "fim": fim,
        })

    return templates.TemplateResponse("dia/painel.html", {
        "request": request,
        "atendente": atendente,
        "dia": dia,
        "mediuns_dia": [dict(m) for m in mediuns_dia],
        "total_checkins": total_checkins,
        "passe_aguardando": passe_aguardando,
        "passe_realizado": passe_realizado,
        "acolh_aguardando": acolh_aguardando,
        "acolh_realizado": acolh_realizado,
        "atend_aguardando": atend_aguardando,
        "atend_realizado": atend_realizado,
        "reiki_aguardando": reiki_aguardando,
        "reiki_realizado": reiki_realizado,
        "filas_mediuns": filas_mediuns,
        "todos_mediuns": [dict(m) for m in todos_mediuns],
    })


def _resumo_encerrado(request, atendente, conn, dia, backup: str = ""):
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s", (dia["id"],)
    ).fetchone()["c"]
    passes = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND passe_realizado = 1", (dia["id"],)
    ).fetchone()["c"]
    atends = conn.execute(
        "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND atendimento_realizado = 1", (dia["id"],)
    ).fetchone()["c"]
    msgs_backup = [m.strip() for m in backup.split("|")] if backup else []
    return templates.TemplateResponse("dia/encerrado.html", {
        "request": request,
        "atendente": atendente,
        "dia": dia,
        "total": total,
        "passes": passes,
        "atends": atends,
        "msgs_backup": msgs_backup,
    })


# ── Abrir dia ─────────────────────────────────────────────────────────────────

@router.post("/abrir")
async def abrir_dia(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    formdata = await request.form()
    mediuns_ids = [int(x) for x in formdata.getlist("mediuns_ids")]
    vagas_map = {}
    for key, val in formdata.items():
        if key.startswith("vagas_") and val.strip():
            mid = int(key.split("_")[1])
            vagas_map[mid] = int(val)

    with conectar() as conn:
        cur = conn.execute(
            "INSERT INTO dias_trabalho (data, aberto) VALUES (%s, 1) RETURNING id", (_hoje(),)
        )
        dia_id = cur.fetchone()["id"]
        for mid in mediuns_ids:
            vagas = vagas_map.get(mid, None)
            conn.execute(
                "INSERT INTO mediuns_dia (dia_trabalho_id, medium_id, vagas_dia) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (dia_id, mid, vagas),
            )
    return RedirectResponse(url="/dia", status_code=303)


@router.post("/adicionar-medium")
async def adicionar_medium_dia(request: Request, medium_id: int = Form(...)):
    atendente, redir = _guard(request)
    if redir:
        return redir

    formdata = await request.form()
    vagas = formdata.get("vagas_dia", "").strip()
    vagas_int = int(vagas) if vagas else None

    with conectar() as conn:
        dia = _dia_hoje(conn)
        if not dia or not dia["aberto"]:
            return RedirectResponse(url="/dia", status_code=303)
        conn.execute(
            """INSERT INTO mediuns_dia (dia_trabalho_id, medium_id, vagas_dia)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            (dia["id"], medium_id, vagas_int)
        )
    return RedirectResponse(url="/dia", status_code=303)


# ── Encerrar dia ──────────────────────────────────────────────────────────────

@router.post("/encerrar")
async def encerrar_dia(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    hoje = _hoje()
    with conectar() as conn:
        conn.execute("UPDATE dias_trabalho SET aberto = 0 WHERE data = %s", (hoje,))

        # Marcar como "faltou" todos os agendamentos de hoje ainda em aberto
        agendamentos_abertos = conn.execute(
            "SELECT id, plano_id FROM agendamentos WHERE data = %s AND status = 'agendado'",
            (hoje,)
        ).fetchall()

        planos_afetados = set()
        for ag in agendamentos_abertos:
            conn.execute(
                "UPDATE agendamentos SET status='faltou' WHERE id=%s", (ag["id"],)
            )
            planos_afetados.add(ag["plano_id"])

        # Verificar 3 faltas consecutivas por plano
        for plano_id in planos_afetados:
            ultimos = conn.execute(
                """SELECT status FROM agendamentos
                   WHERE plano_id = %s AND data <= %s
                   ORDER BY data DESC LIMIT 3""",
                (plano_id, hoje)
            ).fetchall()
            if len(ultimos) >= 3 and all(r["status"] == "faltou" for r in ultimos[:3]):
                conn.execute(
                    "UPDATE planos_tratamento SET status='cancelado' WHERE id=%s",
                    (plano_id,)
                )
                conn.execute(
                    "UPDATE agendamentos SET status='cancelado' WHERE plano_id=%s AND status='agendado'",
                    (plano_id,)
                )

    msgs_backup = fazer_backup()
    import urllib.parse
    backup_info = urllib.parse.quote(" | ".join(msgs_backup))
    return RedirectResponse(url=f"/dia?backup={backup_info}", status_code=303)


# ── Fila de passe ─────────────────────────────────────────────────────────────

@router.get("/passe", response_class=HTMLResponse)
async def fila_passe(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if not dia or not dia["aberto"]:
            return RedirectResponse(url="/dia", status_code=303)
        fila = conn.execute(
            f"""SELECT c.id, c.codigo_passe, c.hora_checkin, p.nome_completo,
                       p.prioridade, p.deficiencia, p.data_nascimento
                FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
                WHERE c.dia_trabalho_id = %s AND c.codigo_passe IS NOT NULL AND c.passe_realizado = 0
                ORDER BY {_ORDEM_PRIORIDADE}""",
            (dia["id"],)
        ).fetchall()
        realizados = conn.execute(
            """SELECT c.id, c.codigo_passe, c.hora_checkin, p.nome_completo
               FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
               WHERE c.dia_trabalho_id = %s AND c.passe_realizado = 1
               ORDER BY c.hora_checkin""",
            (dia["id"],)
        ).fetchall()
    return templates.TemplateResponse("dia/passe.html", {
        "request": request,
        "atendente": atendente,
        "fila": [dict(r) for r in fila],
        "realizados": [dict(r) for r in realizados],
        "hoje": _hoje(),
    })


@router.post("/passe/{checkin_id}/chamar")
async def chamar_passe(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(
            "SELECT codigo_passe FROM checkins WHERE id = %s", (checkin_id,)
        ).fetchone()
    if row and row["codigo_passe"]:
        await gerenciador.transmitir(row["codigo_passe"])
    return RedirectResponse(url="/dia/passe", status_code=303)


@router.post("/passe/{checkin_id}/realizado")
async def passe_realizado(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "UPDATE checkins SET passe_realizado = 1 WHERE id = %s", (checkin_id,)
        )
    return RedirectResponse(url="/dia/passe", status_code=303)


@router.post("/passe/{checkin_id}/desfazer")
async def passe_desfazer(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "UPDATE checkins SET passe_realizado = 0 WHERE id = %s", (checkin_id,)
        )
    return RedirectResponse(url="/dia/passe", status_code=303)


# ── Fila de acolhimento ───────────────────────────────────────────────────────

@router.get("/acolhimento", response_class=HTMLResponse)
async def fila_acolhimento(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if not dia or not dia["aberto"]:
            return RedirectResponse(url="/dia", status_code=303)
        fila = conn.execute(
            f"""SELECT c.id, c.codigo_acolhimento, c.hora_checkin, p.nome_completo,
                       p.prioridade, p.deficiencia, p.data_nascimento, p.id as pessoa_id,
                       c.acolhimento_chamado
                FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
                WHERE c.dia_trabalho_id = %s AND c.codigo_acolhimento IS NOT NULL AND c.acolhimento_realizado = 0
                ORDER BY c.acolhimento_chamado ASC, {_ORDEM_PRIORIDADE}""",
            (dia["id"],)
        ).fetchall()
        realizados = conn.execute(
            """SELECT c.id, c.codigo_acolhimento, c.hora_checkin, p.nome_completo
               FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
               WHERE c.dia_trabalho_id = %s AND c.acolhimento_realizado = 1
               ORDER BY c.hora_checkin""",
            (dia["id"],)
        ).fetchall()
    return templates.TemplateResponse("dia/acolhimento.html", {
        "request": request,
        "atendente": atendente,
        "fila": [dict(r) for r in fila],
        "realizados": [dict(r) for r in realizados],
        "hoje": _hoje(),
    })


@router.post("/acolhimento/{checkin_id}/chamar")
async def chamar_acolhimento(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(
            "SELECT codigo_acolhimento FROM checkins WHERE id = %s", (checkin_id,)
        ).fetchone()
        conn.execute(
            "UPDATE checkins SET acolhimento_chamado = 1 WHERE id = %s", (checkin_id,)
        )
    if row and row["codigo_acolhimento"]:
        await gerenciador.transmitir(row["codigo_acolhimento"])
    return RedirectResponse(url="/dia/acolhimento", status_code=303)


@router.post("/acolhimento/{checkin_id}/realizado")
async def acolhimento_realizado(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "UPDATE checkins SET acolhimento_realizado = 1 WHERE id = %s", (checkin_id,)
        )
    return RedirectResponse(url="/dia/acolhimento", status_code=303)


# ── Fila por médium ───────────────────────────────────────────────────────────

@router.get("/mediuns/{medium_id}", response_class=HTMLResponse)
async def fila_atendimento(request: Request, medium_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if not dia or not dia["aberto"]:
            return RedirectResponse(url="/dia", status_code=303)
        medium = conn.execute(
            "SELECT id, nome_completo FROM mediuns WHERE id = %s", (medium_id,)
        ).fetchone()
        if not medium:
            return RedirectResponse(url="/dia", status_code=303)

        # Deduplicar por codigo_atendimento: mostrar apenas o titular (menor id por código)
        fila = conn.execute(
            f"""SELECT c.id, c.codigo_atendimento, c.hora_checkin, p.nome_completo,
                       p.prioridade, p.deficiencia, p.data_nascimento
                FROM checkins c
                JOIN pessoas p ON p.id = c.pessoa_id
                WHERE c.dia_trabalho_id = %s AND c.medium_id = %s
                  AND c.codigo_atendimento IS NOT NULL AND c.atendimento_realizado = 0
                  AND c.id = (
                      SELECT MIN(c2.id) FROM checkins c2
                      WHERE c2.dia_trabalho_id = c.dia_trabalho_id
                        AND c2.codigo_atendimento = c.codigo_atendimento
                        AND c2.atendimento_realizado = 0
                  )
                ORDER BY {_ORDEM_PRIORIDADE}""",
            (dia["id"], medium_id)
        ).fetchall()

        realizados = conn.execute(
            """SELECT c.id, c.codigo_atendimento, c.hora_checkin, p.nome_completo
               FROM checkins c
               JOIN pessoas p ON p.id = c.pessoa_id
               WHERE c.dia_trabalho_id = %s AND c.medium_id = %s AND c.atendimento_realizado = 1
                 AND c.id = (
                     SELECT MIN(c2.id) FROM checkins c2
                     WHERE c2.dia_trabalho_id = c.dia_trabalho_id
                       AND c2.codigo_atendimento = c.codigo_atendimento
                       AND c2.atendimento_realizado = 1
                 )
               ORDER BY c.hora_checkin""",
            (dia["id"], medium_id)
        ).fetchall()

        # Agendados para hoje deste médium que ainda não fizeram check-in
        hoje = _hoje()
        agendados_sem_checkin = conn.execute(
            """SELECT a.id as agendamento_id, pe.id as pessoa_id, pe.nome_completo,
                      a.requer_passe, a.encaixe, pt.id as plano_id
               FROM agendamentos a
               JOIN planos_tratamento pt ON pt.id = a.plano_id
               JOIN plano_pessoas pp ON pp.plano_id = pt.id
               JOIN pessoas pe ON pe.id = pp.pessoa_id
               WHERE a.data = %s AND pt.medium_id = %s AND a.status = 'agendado'
                 AND NOT EXISTS (
                     SELECT 1 FROM checkins c
                     WHERE c.dia_trabalho_id = %s AND c.pessoa_id = pe.id
                 )
               ORDER BY pe.nome_completo""",
            (hoje, medium_id, dia["id"])
        ).fetchall()

        outros_mediuns = conn.execute(
            """SELECT m.id, m.nome_completo FROM mediuns_dia md
               JOIN mediuns m ON m.id = md.medium_id
               WHERE md.dia_trabalho_id = %s AND m.id != %s
               ORDER BY m.nome_completo""",
            (dia["id"], medium_id)
        ).fetchall()

    return templates.TemplateResponse("dia/atendimento.html", {
        "request": request,
        "atendente": atendente,
        "medium": dict(medium),
        "fila": [dict(r) for r in fila],
        "realizados": [dict(r) for r in realizados],
        "agendados_sem_checkin": [dict(r) for r in agendados_sem_checkin],
        "outros_mediuns": [dict(m) for m in outros_mediuns],
        "hoje": hoje,
    })


@router.post("/atendimento/{checkin_id}/chamar")
async def chamar_atendimento(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(
            "SELECT codigo_atendimento, medium_id FROM checkins WHERE id = %s",
            (checkin_id,)
        ).fetchone()
    if row and row["codigo_atendimento"]:
        await gerenciador.transmitir(row["codigo_atendimento"])
        return RedirectResponse(url=f"/dia/mediuns/{row['medium_id']}", status_code=303)
    return RedirectResponse(url="/dia", status_code=303)


@router.post("/atendimento/{checkin_id}/realizado")
async def atendimento_realizado(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(
            "SELECT dia_trabalho_id, medium_id, agendamento_id, plano_id, codigo_atendimento FROM checkins WHERE id = %s",
            (checkin_id,)
        ).fetchone()
        if row:
            # Marca o titular e todos os acompanhantes com o mesmo código
            conn.execute(
                "UPDATE checkins SET atendimento_realizado = 1 WHERE dia_trabalho_id = %s AND codigo_atendimento = %s",
                (row["dia_trabalho_id"], row["codigo_atendimento"])
            )
            medium_id = row["medium_id"]
            if row["agendamento_id"]:
                conn.execute(
                    "UPDATE agendamentos SET status='realizado' WHERE id=%s",
                    (row["agendamento_id"],)
                )
            if row["plano_id"]:
                conn.execute(
                    "UPDATE planos_tratamento SET sessoes_realizadas = sessoes_realizadas + 1 WHERE id=%s",
                    (row["plano_id"],)
                )
        else:
            medium_id = None
    if medium_id:
        return RedirectResponse(url=f"/dia/mediuns/{medium_id}", status_code=303)
    return RedirectResponse(url="/dia", status_code=303)


@router.post("/atendimento/{checkin_id}/desfazer")
async def atendimento_desfazer(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(
            "SELECT dia_trabalho_id, medium_id, agendamento_id, plano_id, codigo_atendimento FROM checkins WHERE id = %s",
            (checkin_id,)
        ).fetchone()
        if row:
            # Reabre o titular e todos os acompanhantes com o mesmo código
            conn.execute(
                "UPDATE checkins SET atendimento_realizado = 0 WHERE dia_trabalho_id = %s AND codigo_atendimento = %s",
                (row["dia_trabalho_id"], row["codigo_atendimento"])
            )
            medium_id = row["medium_id"]
            if row["agendamento_id"]:
                conn.execute(
                    "UPDATE agendamentos SET status='agendado' WHERE id=%s",
                    (row["agendamento_id"],)
                )
            if row["plano_id"]:
                conn.execute(
                    "UPDATE planos_tratamento SET sessoes_realizadas = GREATEST(0, sessoes_realizadas - 1) WHERE id=%s",
                    (row["plano_id"],)
                )
        else:
            medium_id = None
    if medium_id:
        return RedirectResponse(url=f"/dia/mediuns/{medium_id}", status_code=303)
    return RedirectResponse(url="/dia", status_code=303)


@router.post("/atendimento/{checkin_id}/transferir")
async def transferir_atendimento(
    request: Request,
    checkin_id: int,
    novo_medium_id: int = Form(...),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(
            "SELECT medium_id FROM checkins WHERE id = %s", (checkin_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE checkins SET medium_id = %s WHERE id = %s",
                (novo_medium_id, checkin_id)
            )
            medium_id = row["medium_id"]
        else:
            medium_id = None
    if medium_id:
        return RedirectResponse(url=f"/dia/mediuns/{medium_id}", status_code=303)
    return RedirectResponse(url="/dia", status_code=303)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if not dia or not dia["aberto"]:
            return RedirectResponse(url="/dia", status_code=303)
        dia = dict(dia)

        fila_passe = conn.execute(
            f"""SELECT c.id, c.codigo_passe AS codigo, c.hora_checkin,
                       p.nome_completo, p.prioridade, p.deficiencia, p.data_nascimento
                FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
                WHERE c.dia_trabalho_id = %s AND c.codigo_passe IS NOT NULL AND c.passe_realizado = 0
                ORDER BY {_ORDEM_PRIORIDADE}""",
            (dia["id"],)
        ).fetchall()

        fila_acolhimento = conn.execute(
            f"""SELECT c.id, c.codigo_acolhimento AS codigo, c.hora_checkin,
                       p.nome_completo, p.prioridade, p.deficiencia, p.data_nascimento
                FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
                WHERE c.dia_trabalho_id = %s AND c.codigo_acolhimento IS NOT NULL AND c.acolhimento_realizado = 0
                ORDER BY {_ORDEM_PRIORIDADE}""",
            (dia["id"],)
        ).fetchall()

        mediuns_dia = conn.execute(
            """SELECT m.id, m.nome_completo, md.vagas_dia
               FROM mediuns_dia md JOIN mediuns m ON m.id = md.medium_id
               WHERE md.dia_trabalho_id = %s ORDER BY m.nome_completo""",
            (dia["id"],)
        ).fetchall()

        fila_reiki = conn.execute(
            f"""SELECT c.id, c.codigo_reiki AS codigo, c.hora_checkin,
                       p.nome_completo, p.prioridade, p.deficiencia, p.data_nascimento
                FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
                WHERE c.dia_trabalho_id = %s AND c.codigo_reiki IS NOT NULL AND c.reiki_realizado = 0
                ORDER BY {_ORDEM_PRIORIDADE}""",
            (dia["id"],)
        ).fetchall()

        colunas_mediuns = []
        for m in mediuns_dia:
            fila = conn.execute(
                f"""SELECT c.id, c.codigo_atendimento AS codigo, c.hora_checkin,
                           p.nome_completo, p.prioridade, p.deficiencia, p.data_nascimento
                    FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
                    WHERE c.dia_trabalho_id = %s AND c.medium_id = %s
                      AND c.codigo_atendimento IS NOT NULL AND c.atendimento_realizado = 0
                      AND c.id = (
                          SELECT MIN(c2.id) FROM checkins c2
                          WHERE c2.dia_trabalho_id = c.dia_trabalho_id
                            AND c2.codigo_atendimento = c.codigo_atendimento
                            AND c2.atendimento_realizado = 0
                      )
                    ORDER BY {_ORDEM_PRIORIDADE}""",
                (dia["id"], m["id"])
            ).fetchall()
            total = conn.execute(
                """SELECT COUNT(*) AS c FROM checkins
                   WHERE dia_trabalho_id = %s AND medium_id = %s AND codigo_atendimento IS NOT NULL""",
                (dia["id"], m["id"])
            ).fetchone()["c"]
            vagas_restantes = None
            if m.get("vagas_dia"):
                vagas_restantes = max(0, m["vagas_dia"] - total)
            colunas_mediuns.append({
                "medium_id": m["id"],
                "nome": m["nome_completo"],
                "fila": [dict(r) for r in fila],
                "vagas_restantes": vagas_restantes,
            })

    return templates.TemplateResponse("dia/dashboard.html", {
        "request": request,
        "atendente": atendente,
        "dia": dia,
        "hoje": _hoje(),
        "fila_passe": [dict(r) for r in fila_passe],
        "fila_acolhimento": [dict(r) for r in fila_acolhimento],
        "fila_reiki": [dict(r) for r in fila_reiki],
        "colunas_mediuns": colunas_mediuns,
    })


@router.post("/dashboard/passe/{checkin_id}/chamar")
async def dashboard_chamar_passe(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute("SELECT codigo_passe FROM checkins WHERE id=%s", (checkin_id,)).fetchone()
    if row and row["codigo_passe"]:
        await gerenciador.transmitir(row["codigo_passe"])
    return RedirectResponse(url="/dia/dashboard", status_code=303)


@router.post("/dashboard/passe/{checkin_id}/realizado")
async def dashboard_passe_realizado(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute("UPDATE checkins SET passe_realizado=1 WHERE id=%s", (checkin_id,))
    return RedirectResponse(url="/dia/dashboard", status_code=303)


@router.post("/dashboard/acolhimento/{checkin_id}/chamar")
async def dashboard_chamar_acolhimento(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute("SELECT codigo_acolhimento FROM checkins WHERE id=%s", (checkin_id,)).fetchone()
    if row and row["codigo_acolhimento"]:
        await gerenciador.transmitir(row["codigo_acolhimento"])
    return RedirectResponse(url="/dia/dashboard", status_code=303)


@router.post("/dashboard/acolhimento/{checkin_id}/realizado")
async def dashboard_acolhimento_realizado(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute("UPDATE checkins SET acolhimento_realizado=1 WHERE id=%s", (checkin_id,))
    return RedirectResponse(url="/dia/dashboard", status_code=303)


@router.post("/dashboard/atendimento/{checkin_id}/chamar")
async def dashboard_chamar_atendimento(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute("SELECT codigo_atendimento FROM checkins WHERE id=%s", (checkin_id,)).fetchone()
    if row and row["codigo_atendimento"]:
        await gerenciador.transmitir(row["codigo_atendimento"])
    return RedirectResponse(url="/dia/dashboard", status_code=303)


@router.post("/dashboard/atendimento/{checkin_id}/realizado")
async def dashboard_atendimento_realizado(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(
            "SELECT dia_trabalho_id, agendamento_id, plano_id, codigo_atendimento FROM checkins WHERE id=%s",
            (checkin_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE checkins SET atendimento_realizado=1 WHERE dia_trabalho_id=%s AND codigo_atendimento=%s",
                (row["dia_trabalho_id"], row["codigo_atendimento"])
            )
            if row["agendamento_id"]:
                conn.execute("UPDATE agendamentos SET status='realizado' WHERE id=%s", (row["agendamento_id"],))
            if row["plano_id"]:
                conn.execute(
                    "UPDATE planos_tratamento SET sessoes_realizadas = sessoes_realizadas + 1 WHERE id=%s",
                    (row["plano_id"],)
                )
    return RedirectResponse(url="/dia/dashboard", status_code=303)


@router.post("/dashboard/reiki/{checkin_id}/chamar")
async def dashboard_chamar_reiki(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute("SELECT codigo_reiki FROM checkins WHERE id=%s", (checkin_id,)).fetchone()
    if row and row["codigo_reiki"]:
        await gerenciador.transmitir(row["codigo_reiki"])
    return RedirectResponse(url="/dia/dashboard", status_code=303)


@router.post("/dashboard/reiki/{checkin_id}/realizado")
async def dashboard_reiki_realizado(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute("UPDATE checkins SET reiki_realizado=1 WHERE id=%s", (checkin_id,))
    return RedirectResponse(url="/dia/dashboard", status_code=303)


# ── Cancelar atendimento no dashboard ─────────────────────────────────────

def _cancelar_checkin(conn, checkin_id: int, dia_id: int, tipo: str):
    """Remove um checkin pelo código de um tipo específico, incluindo acompanhantes."""
    mapas = {
        "passe": "codigo_passe",
        "reiki": "codigo_reiki",
        "acolhimento": "codigo_acolhimento",
        "atendimento": "codigo_atendimento",
    }
    campo = mapas[tipo]
    row = conn.execute(
        f"SELECT {campo} FROM checkins WHERE id = %s AND dia_trabalho_id = %s",
        (checkin_id, dia_id)
    ).fetchone()
    if not row or not row[campo]:
        return
    codigo = row[campo]
    conn.execute(
        f"DELETE FROM checkins WHERE dia_trabalho_id = %s AND {campo} = %s",
        (dia_id, codigo)
    )


@router.post("/dashboard/passe/{checkin_id}/cancelar")
async def dashboard_cancelar_passe(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if dia:
            _cancelar_checkin(conn, checkin_id, dia["id"], "passe")
    return RedirectResponse(url="/dia/dashboard", status_code=303)


@router.post("/dashboard/reiki/{checkin_id}/cancelar")
async def dashboard_cancelar_reiki(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if dia:
            _cancelar_checkin(conn, checkin_id, dia["id"], "reiki")
    return RedirectResponse(url="/dia/dashboard", status_code=303)


@router.post("/dashboard/acolhimento/{checkin_id}/cancelar")
async def dashboard_cancelar_acolhimento(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if dia:
            _cancelar_checkin(conn, checkin_id, dia["id"], "acolhimento")
    return RedirectResponse(url="/dia/dashboard", status_code=303)


@router.post("/dashboard/atendimento/{checkin_id}/cancelar")
async def dashboard_cancelar_atendimento(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if dia:
            _cancelar_checkin(conn, checkin_id, dia["id"], "atendimento")
    return RedirectResponse(url="/dia/dashboard", status_code=303)


# ── Fila de Reiki ─────────────────────────────────────────────────────────────

@router.get("/reiki", response_class=HTMLResponse)
async def fila_reiki(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if not dia or not dia["aberto"]:
            return RedirectResponse(url="/dia", status_code=303)
        fila = conn.execute(
            f"""SELECT c.id, c.codigo_reiki, c.hora_checkin, p.nome_completo,
                       p.prioridade, p.deficiencia, p.data_nascimento
                FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
                WHERE c.dia_trabalho_id = %s AND c.codigo_reiki IS NOT NULL AND c.reiki_realizado = 0
                ORDER BY {_ORDEM_PRIORIDADE}""",
            (dia["id"],)
        ).fetchall()
        realizados = conn.execute(
            """SELECT c.id, c.codigo_reiki, c.hora_checkin, p.nome_completo
               FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
               WHERE c.dia_trabalho_id = %s AND c.reiki_realizado = 1
               ORDER BY c.hora_checkin""",
            (dia["id"],)
        ).fetchall()
    return templates.TemplateResponse("dia/reiki.html", {
        "request": request,
        "atendente": atendente,
        "fila": [dict(r) for r in fila],
        "realizados": [dict(r) for r in realizados],
        "hoje": _hoje(),
    })


@router.post("/reiki/{checkin_id}/chamar")
async def chamar_reiki(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(
            "SELECT codigo_reiki FROM checkins WHERE id = %s", (checkin_id,)
        ).fetchone()
    if row and row["codigo_reiki"]:
        await gerenciador.transmitir(row["codigo_reiki"])
    return RedirectResponse(url="/dia/reiki", status_code=303)


@router.post("/reiki/{checkin_id}/realizado")
async def reiki_realizado(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "UPDATE checkins SET reiki_realizado = 1 WHERE id = %s", (checkin_id,)
        )
    return RedirectResponse(url="/dia/reiki", status_code=303)


@router.post("/reiki/{checkin_id}/desfazer")
async def reiki_desfazer(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "UPDATE checkins SET reiki_realizado = 0 WHERE id = %s", (checkin_id,)
        )
    return RedirectResponse(url="/dia/reiki", status_code=303)


# ── Lista por tipo (painel clicável) ──────────────────────────────────────────

@router.get("/lista", response_class=HTMLResponse)
async def lista_dia(request: Request, tipo: str = "checkins"):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if not dia:
            return RedirectResponse(url="/dia", status_code=303)
        dia = dict(dia)

        mapa = {
            "checkins":              ("SELECT p.nome_completo, c.hora_checkin FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id WHERE c.dia_trabalho_id = %s ORDER BY c.hora_checkin", "Check-ins"),
            "passe-aguardando":      (f"SELECT p.nome_completo, c.hora_checkin, c.codigo_passe as senha FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id WHERE c.dia_trabalho_id = %s AND c.codigo_passe IS NOT NULL AND c.passe_realizado = 0 ORDER BY {_ORDEM_PRIORIDADE}", "Passes aguardando"),
            "passe-realizado":       ("SELECT p.nome_completo, c.hora_checkin, c.codigo_passe as senha FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id WHERE c.dia_trabalho_id = %s AND c.passe_realizado = 1 ORDER BY c.hora_checkin", "Passes realizados"),
            "acolhimento-aguardando":("SELECT p.nome_completo, c.hora_checkin, c.codigo_acolhimento as senha FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id WHERE c.dia_trabalho_id = %s AND c.codigo_acolhimento IS NOT NULL AND c.acolhimento_realizado = 0 ORDER BY c.hora_checkin", "Acolhimentos aguardando"),
            "acolhimento-realizado": ("SELECT p.nome_completo, c.hora_checkin, c.codigo_acolhimento as senha FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id WHERE c.dia_trabalho_id = %s AND c.acolhimento_realizado = 1 ORDER BY c.hora_checkin", "Acolhimentos realizados"),
            "atendimento-aguardando":("SELECT p.nome_completo, c.hora_checkin, c.codigo_atendimento as senha, m.nome_completo as medium_nome FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id LEFT JOIN mediuns m ON m.id = c.medium_id WHERE c.dia_trabalho_id = %s AND c.codigo_atendimento IS NOT NULL AND c.atendimento_realizado = 0 ORDER BY c.hora_checkin", "Atendimentos aguardando"),
            "atendimento-realizado": ("SELECT p.nome_completo, c.hora_checkin, c.codigo_atendimento as senha, m.nome_completo as medium_nome FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id LEFT JOIN mediuns m ON m.id = c.medium_id WHERE c.dia_trabalho_id = %s AND c.atendimento_realizado = 1 ORDER BY c.hora_checkin", "Atendimentos realizados"),
            "reiki-aguardando":      ("SELECT p.nome_completo, c.hora_checkin, c.codigo_reiki as senha FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id WHERE c.dia_trabalho_id = %s AND c.codigo_reiki IS NOT NULL AND c.reiki_realizado = 0 ORDER BY c.hora_checkin", "Reiki aguardando"),
            "reiki-realizado":       ("SELECT p.nome_completo, c.hora_checkin, c.codigo_reiki as senha FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id WHERE c.dia_trabalho_id = %s AND c.reiki_realizado = 1 ORDER BY c.hora_checkin", "Reiki realizado"),
        }

        sql, titulo = mapa.get(tipo, mapa["checkins"])
        rows = conn.execute(sql, (dia["id"],)).fetchall()

    return templates.TemplateResponse("dia/lista.html", {
        "request": request,
        "atendente": atendente,
        "dia": dia,
        "titulo": titulo,
        "tipo": tipo,
        "pessoas": [dict(r) for r in rows],
    })


# ── Atendimento Fraterno ──────────────────────────────────────────────────────

@router.get("/fraterno/{checkin_id}", response_class=HTMLResponse)
async def tela_fraterno(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dia = _dia_hoje(conn)
        if not dia:
            return RedirectResponse(url="/dia", status_code=303)
        checkin = conn.execute(
            """SELECT c.*, p.nome_completo, p.data_nascimento, p.deficiencia, p.prioridade
               FROM checkins c JOIN pessoas p ON p.id = c.pessoa_id
               WHERE c.id = %s""",
            (checkin_id,)
        ).fetchone()
        if not checkin:
            return RedirectResponse(url="/dia/acolhimento", status_code=303)
        mediuns = conn.execute(
            "SELECT id, nome_completo FROM mediuns WHERE ativo=1 ORDER BY nome_completo"
        ).fetchall()
        # Planos anteriores da pessoa
        planos_anteriores = conn.execute(
            """SELECT pt.id, pt.status, pt.sessoes_total, pt.sessoes_realizadas,
                      pt.data_inicio, m.nome_completo as medium_nome
               FROM planos_tratamento pt
               JOIN plano_pessoas pp ON pp.plano_id = pt.id
               JOIN mediuns m ON m.id = pt.medium_id
               WHERE pp.pessoa_id = %s
               ORDER BY pt.data_inicio DESC LIMIT 5""",
            (checkin["pessoa_id"],)
        ).fetchall()
    from datetime import date as _date
    hoje = _date.today().isoformat()
    return templates.TemplateResponse("dia/fraterno.html", {
        "request": request,
        "atendente": atendente,
        "checkin": dict(checkin),
        "mediuns": [dict(m) for m in mediuns],
        "planos_anteriores": [dict(p) for p in planos_anteriores],
        "hoje": hoje,
    })


@router.post("/fraterno/{checkin_id}")
async def salvar_fraterno(
    request: Request,
    checkin_id: int,
    medium_id: int = Form(...),
    sessoes_total: int = Form(6),
    frequencia: str = Form("semanal"),
    sessoes_com_passe: int = Form(3),
    data_inicio: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    from datetime import date as _date
    from banco import gerar_agendamentos_plano
    inicio = _date.fromisoformat(data_inicio) if data_inicio else _date.today()
    with conectar() as conn:
        checkin = conn.execute(
            "SELECT pessoa_id FROM checkins WHERE id=%s", (checkin_id,)
        ).fetchone()
        if not checkin:
            return RedirectResponse(url="/dia/acolhimento", status_code=303)
        cur = conn.execute(
            """INSERT INTO planos_tratamento
               (medium_id, sessoes_total, data_inicio, frequencia, sessoes_com_passe, status)
               VALUES (%s,%s,%s,%s,%s,'ativo')
               RETURNING id""",
            (medium_id, sessoes_total, inicio.isoformat(), frequencia, sessoes_com_passe)
        )
        plano_id = cur.fetchone()["id"]
        conn.execute(
            """INSERT INTO plano_pessoas (plano_id, pessoa_id)
               VALUES (%s,%s) ON CONFLICT DO NOTHING""",
            (plano_id, checkin["pessoa_id"])
        )
        gerar_agendamentos_plano(conn, plano_id, inicio, frequencia, sessoes_total, sessoes_com_passe)
        # Marcar acolhimento como realizado
        conn.execute(
            "UPDATE checkins SET acolhimento_realizado=1 WHERE id=%s", (checkin_id,)
        )
    return RedirectResponse(url="/dia/acolhimento", status_code=303)
