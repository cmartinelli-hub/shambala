from datetime import date
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar, _normalizar
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/relatorios")


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


def _mes_atual():
    hoje = date.today()
    return hoje.replace(day=1).isoformat(), hoje.isoformat()


# ── Índice ────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def index(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("relatorios/index.html", {
        "request": request,
        "atendente": atendente,
    })


# ── Por Dia ───────────────────────────────────────────────────────────────────

@router.get("/por-dia", response_class=HTMLResponse)
async def por_dia(
    request: Request,
    data_ini: str = Query(""),
    data_fim: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    ini_padrao, fim_padrao = _mes_atual()
    data_ini = data_ini or ini_padrao
    data_fim = data_fim or fim_padrao

    with conectar() as conn:
        rows = conn.execute(
            """
            SELECT
                dt.data,
                COUNT(c.id)                                                  AS total,
                COALESCE(SUM(CASE WHEN c.codigo_passe IS NOT NULL THEN 1 ELSE 0 END), 0)  AS passes_agend,
                COALESCE(SUM(c.passe_realizado), 0)                         AS passes_real,
                COALESCE(SUM(CASE WHEN c.codigo_acolhimento IS NOT NULL THEN 1 ELSE 0 END), 0) AS acolh_agend,
                COALESCE(SUM(c.acolhimento_realizado), 0)                   AS acolh_real,
                COALESCE(SUM(CASE WHEN c.codigo_atendimento IS NOT NULL THEN 1 ELSE 0 END), 0) AS atend_agend,
                COALESCE(SUM(c.atendimento_realizado), 0)                   AS atend_real
            FROM dias_trabalho dt
            LEFT JOIN checkins c ON c.dia_trabalho_id = dt.id
            WHERE dt.data BETWEEN %s AND %s
            GROUP BY dt.id, dt.data
            ORDER BY dt.data DESC
            """,
            (data_ini, data_fim),
        ).fetchall()

    return templates.TemplateResponse("relatorios/por_dia.html", {
        "request": request,
        "atendente": atendente,
        "dias": [dict(r) for r in rows],
        "data_ini": data_ini,
        "data_fim": data_fim,
    })


# ── Por Pessoa ────────────────────────────────────────────────────────────────

@router.get("/por-pessoa", response_class=HTMLResponse)
async def por_pessoa(
    request: Request,
    busca: str = Query(""),
    pessoa_id: int = Query(0),
    data_ini: str = Query(""),
    data_fim: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    ini_padrao, fim_padrao = _mes_atual()
    data_ini = data_ini or ini_padrao
    data_fim = data_fim or fim_padrao

    pessoas_encontradas = []
    pessoa = None
    historico = []

    with conectar() as conn:
        if pessoa_id:
            pessoa = conn.execute(
                "SELECT id, nome_completo FROM pessoas WHERE id = %s", (pessoa_id,)
            ).fetchone()
            if pessoa:
                pessoa = dict(pessoa)
                historico = conn.execute(
                    """
                    SELECT
                        dt.data,
                        c.hora_checkin,
                        c.codigo_passe,       c.passe_realizado,
                        c.codigo_acolhimento, c.acolhimento_realizado,
                        c.codigo_atendimento, c.atendimento_realizado,
                        m.nome_completo       AS medium_nome
                    FROM checkins c
                    JOIN dias_trabalho dt ON dt.id = c.dia_trabalho_id
                    LEFT JOIN mediuns m ON m.id = c.medium_id
                    WHERE c.pessoa_id = %s AND dt.data BETWEEN %s AND %s
                    ORDER BY dt.data DESC, c.hora_checkin
                    """,
                    (pessoa_id, data_ini, data_fim),
                ).fetchall()
                historico = [dict(r) for r in historico]

        elif busca.strip():
            termo = f"%{_normalizar(busca.strip())}%"
            pessoas_encontradas = conn.execute(
                "SELECT id, nome_completo FROM pessoas WHERE norm(nome_completo) LIKE %s ORDER BY nome_completo",
                (termo,),
            ).fetchall()
            pessoas_encontradas = [dict(p) for p in pessoas_encontradas]

    return templates.TemplateResponse("relatorios/por_pessoa.html", {
        "request": request,
        "atendente": atendente,
        "busca": busca,
        "pessoa_id": pessoa_id,
        "pessoa": pessoa,
        "pessoas_encontradas": pessoas_encontradas,
        "historico": historico,
        "data_ini": data_ini,
        "data_fim": data_fim,
    })


# ── Por Médium ────────────────────────────────────────────────────────────────

@router.get("/por-medium", response_class=HTMLResponse)
async def por_medium(
    request: Request,
    data_ini: str = Query(""),
    data_fim: str = Query(""),
    dia_semana: str = Query(""),  # "1"=segunda, "3"=quarta, ""=todos
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    ini_padrao, fim_padrao = _mes_atual()
    data_ini = data_ini or ini_padrao
    data_fim = data_fim or fim_padrao

    filtro_dia = ""
    params: list = [data_ini, data_fim]
    if dia_semana in ("1", "3"):
        filtro_dia = "AND EXTRACT(ISODOW FROM dt.data::date) = %s"
        params.append(int(dia_semana))

    with conectar() as conn:
        rows = conn.execute(
            f"""
            SELECT
                m.nome_completo,
                COUNT(c.id)                                          AS agendados,
                SUM(CASE WHEN c.atendimento_realizado = 1 THEN 1 ELSE 0 END) AS realizados
            FROM mediuns m
            LEFT JOIN checkins c
                ON c.medium_id = m.id
                AND c.dia_trabalho_id IN (
                    SELECT id FROM dias_trabalho dt WHERE dt.data BETWEEN %s AND %s {filtro_dia}
                )
            WHERE m.ativo = 1
            GROUP BY m.id, m.nome_completo
            ORDER BY realizados DESC, m.nome_completo
            """,
            params,
        ).fetchall()

    return templates.TemplateResponse("relatorios/por_medium.html", {
        "request": request,
        "atendente": atendente,
        "mediuns": [dict(r) for r in rows],
        "data_ini": data_ini,
        "data_fim": data_fim,
        "dia_semana": dia_semana,
    })


# ── Frequência Geral ──────────────────────────────────────────────────────────

@router.get("/frequencia", response_class=HTMLResponse)
async def frequencia(
    request: Request,
    data_ini: str = Query(""),
    data_fim: str = Query(""),
    limite: int = Query(50),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    ini_padrao, fim_padrao = _mes_atual()
    data_ini = data_ini or ini_padrao
    data_fim = data_fim or fim_padrao

    with conectar() as conn:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.nome_completo,
                COUNT(c.id)                  AS visitas,
                SUM(c.passe_realizado)        AS passes,
                SUM(c.acolhimento_realizado)  AS acolhimentos,
                SUM(c.atendimento_realizado)  AS atendimentos,
                MAX(dt.data)                  AS ultima_visita
            FROM pessoas p
            JOIN checkins c ON c.pessoa_id = p.id
            JOIN dias_trabalho dt ON dt.id = c.dia_trabalho_id
            WHERE dt.data BETWEEN %s AND %s
            GROUP BY p.id, p.nome_completo
            ORDER BY visitas DESC
            LIMIT %s
            """,
            (data_ini, data_fim, limite),
        ).fetchall()

    return templates.TemplateResponse("relatorios/frequencia.html", {
        "request": request,
        "atendente": atendente,
        "pessoas": [dict(r) for r in rows],
        "data_ini": data_ini,
        "data_fim": data_fim,
        "limite": limite,
    })


# ── Presenças dos Trabalhadores (geral) ───────────────────────────────────────

@router.get("/presenca-trabalhadores", response_class=HTMLResponse)
async def presenca_geral_trabalhadores(
    request: Request,
    data_ini: str = Query(""),
    data_fim: str = Query(""),
    dia_semana: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    ini_padrao, fim_padrao = _mes_atual()
    data_ini = data_ini or ini_padrao
    data_fim = data_fim or fim_padrao

    with conectar() as conn:
        filtro_dia = ""
        params: list = [data_ini, data_fim]
        if dia_semana != "":
            filtro_dia = " AND EXTRACT(ISODOW FROM dt.data::date) = %s"
            params.append(int(dia_semana))

        rows = conn.execute(
            f"""
            SELECT
                t.id,
                t.nome_completo,
                COUNT(tp.id)                                                AS total_dias,
                SUM(CASE WHEN tp.presente = 1 THEN 1 ELSE 0 END)            AS presencas,
                SUM(CASE WHEN tp.presente = 0 THEN 1 ELSE 0 END)            AS faltas
            FROM trabalhadores t
            LEFT JOIN trabalhador_presenca tp ON tp.trabalhador_id = t.id
            LEFT JOIN dias_trabalho dt ON dt.id = tp.dia_trabalho_id
                AND dt.data BETWEEN %s AND %s {filtro_dia}
            WHERE t.ativo = 1
            GROUP BY t.id, t.nome_completo
            ORDER BY t.nome_completo
            """,
            params,
        ).fetchall()

        # Dias configurados para o select do filtro
        dias_atendimento = conn.execute(
            "SELECT dia_semana, descricao FROM dias_atendimento ORDER BY dia_semana"
        ).fetchall()

    return templates.TemplateResponse("relatorios/presenca_trabalhadores.html", {
        "request": request,
        "atendente": atendente,
        "trabalhadores": [dict(r) for r in rows],
        "data_ini": data_ini,
        "data_fim": data_fim,
        "dia_semana": dia_semana,
        "dias_atendimento": [dict(d) for d in dias_atendimento],
    })


# ── Presença Individual do Trabalhador ─────────────────────────────────────────

@router.get("/presenca-trabalhador", response_class=HTMLResponse)
async def presenca_individual(
    request: Request,
    trabalhador_id: int = Query(0),
    data_ini: str = Query(""),
    data_fim: str = Query(""),
    dia_semana: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    ini_padrao, fim_padrao = _mes_atual()
    data_ini = data_ini or ini_padrao
    data_fim = data_fim or fim_padrao

    trabalhadores_lista = []
    trabalhador = None
    registros = []
    presencas = 0
    faltas = 0
    dias_atendimento = []

    with conectar() as conn:
        # Lista de trabalhadores ativos para o select
        trabalhadores_lista = conn.execute(
            "SELECT id, nome_completo FROM trabalhadores WHERE ativo = 1 ORDER BY nome_completo"
        ).fetchall()
        trabalhadores_lista = [dict(r) for r in trabalhadores_lista]

        # Dias configurados para o select do filtro
        dias_atendimento = conn.execute(
            "SELECT dia_semana, descricao FROM dias_atendimento ORDER BY dia_semana"
        ).fetchall()
        dias_atendimento = [dict(d) for d in dias_atendimento]

        if trabalhador_id:
            trabalhador = conn.execute(
                "SELECT id, nome_completo FROM trabalhadores WHERE id = %s", (trabalhador_id,)
            ).fetchone()
            if trabalhador:
                trabalhador = dict(trabalhador)
                filtro_dia = ""
                params: list = [trabalhador_id, data_ini, data_fim]
                if dia_semana != "":
                    filtro_dia = " AND EXTRACT(ISODOW FROM dt.data::date) = %s"
                    params.append(int(dia_semana))

                hist = conn.execute(
                    f"""SELECT tp.presente, tp.hora_chegada, tp.hora_saida, dt.data,
                              TO_CHAR(dt.data::date, 'Day') AS dia_nome
                       FROM trabalhador_presenca tp
                       JOIN dias_trabalho dt ON dt.id = tp.dia_trabalho_id
                       WHERE tp.trabalhador_id = %s AND dt.data BETWEEN %s AND %s{filtro_dia}
                       ORDER BY dt.data DESC""",
                    params,
                ).fetchall()
                registros = [dict(r) for r in hist]
                presencas = sum(1 for r in registros if r["presente"] == 1)
                faltas = sum(1 for r in registros if r["presente"] == 0)

    return templates.TemplateResponse("relatorios/presenca_individual.html", {
        "request": request,
        "atendente": atendente,
        "trabalhador_id": trabalhador_id,
        "trabalhadores": trabalhadores_lista,
        "trabalhador": trabalhador,
        "registros": registros,
        "presencas": presencas,
        "faltas": faltas,
        "data_ini": data_ini,
        "data_fim": data_fim,
        "dia_semana": dia_semana,
        "dias_atendimento": dias_atendimento,
    })
