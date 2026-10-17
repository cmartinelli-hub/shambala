from datetime import date, timedelta
import urllib.parse
from fastapi import APIRouter, Request, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar, _normalizar
import banco
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/agenda")

DIAS_SEMANA = [
    (0, "Segunda-feira"),
    (1, "Terça-feira"),
    (2, "Quarta-feira"),
    (3, "Quinta-feira"),
    (4, "Sexta-feira"),
    (5, "Sábado"),
    (6, "Domingo"),
]

FREQ_MAP = {
    "semanal": 7,
    "quinzenal": 14,
    "mensal": 28,
}

# Intervalos lidos de banco._INTERVALO_FREQUENCIA (fonte única do calendário)


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


def _dia_semana_iso(data_str: str) -> int:
    """Retorna o dia da semana (0=segunda) de uma data ISO."""
    return date.fromisoformat(data_str).weekday()


@router.get("", response_class=HTMLResponse)
async def agenda(
    request: Request,
    medium_id: int = Query(0),
    de: str = Query(""),
    ate: str = Query(""),
    dia_semana: int = Query(-1),  # -1 = todos
    pessoa_nome: str = Query(""),
    erro: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    hoje = date.today()
    data_de = de if de else hoje.isoformat()
    data_ate = ate if ate else (hoje + timedelta(days=30)).isoformat()

    with conectar() as conn:
        mediuns = conn.execute(
            "SELECT id, nome_completo FROM mediuns WHERE ativo=1 ORDER BY nome_completo"
        ).fetchall()

        # Construir query com filtros
        filtro_medium = "AND pt.medium_id = %s" if medium_id else ""
        filtro_pessoa = ""
        params = [data_de, data_ate]
        if medium_id:
            params.append(medium_id)

        if pessoa_nome.strip():
            filtro_pessoa = "AND norm(pe.nome_completo) LIKE %s"
            params.append(f"%{_normalizar(pessoa_nome.strip())}%")

        agendamentos = conn.execute(
            f"""SELECT
                    a.id, a.data, a.status, a.requer_passe, a.encaixe,
                    a.plano_id,
                    m.id as medium_id, m.nome_completo as medium_nome,
                    STRING_AGG(pe.nome_completo, ', ') as pessoas,
                    MIN(pe.id) as pessoa_id_principal,
                    COUNT(pp.pessoa_id) as qtd_pessoas,
                    pt.sessoes_realizadas, pt.sessoes_total, pt.frequencia
                FROM agendamentos a
                JOIN planos_tratamento pt ON pt.id = a.plano_id
                JOIN mediuns m ON m.id = pt.medium_id
                JOIN plano_pessoas pp ON pp.plano_id = pt.id
                JOIN pessoas pe ON pe.id = pp.pessoa_id
                WHERE a.data BETWEEN %s AND %s
                  AND a.status = 'agendado'
                  {filtro_medium}
                  {filtro_pessoa}
                GROUP BY a.id, a.data, a.status, a.requer_passe, a.encaixe,
                         a.plano_id, m.id, m.nome_completo,
                         pt.sessoes_realizadas, pt.sessoes_total, pt.frequencia
                ORDER BY a.data, m.nome_completo""",
            params
        ).fetchall()

        # Dias da semana que têm atendimento configurado
        dias_atendimento = conn.execute(
            "SELECT dia_semana FROM dias_atendimento ORDER BY dia_semana"
        ).fetchall()
        dias_atendimento_set = {r["dia_semana"] for r in dias_atendimento}

    # Filtrar por dia da semana (após buscar do banco)
    if dia_semana >= 0:
        agendamentos = [
            ag for ag in agendamentos
            if _dia_semana_iso(ag["data"]) == dia_semana
        ]

    # Agrupar por data
    por_data: dict = {}
    for ag in agendamentos:
        d = ag["data"]
        if d not in por_data:
            por_data[d] = []
        por_data[d].append(dict(ag))

    total_sessoes = sum(len(v) for v in por_data.values())

    # Dias da semana com agendamentos no período (para o filtro)
    dias_com_agenda = set()
    for ag in agendamentos:
        dias_com_agenda.add(_dia_semana_iso(ag["data"]))

    return templates.TemplateResponse("agenda/index.html", {
        "request": request,
        "atendente": atendente,
        "mediuns": [dict(m) for m in mediuns],
        "medium_id": medium_id,
        "data_de": data_de,
        "data_ate": data_ate,
        "dia_semana": dia_semana,
        "pessoa_nome": pessoa_nome,
        "por_data": por_data,
        "hoje": hoje.isoformat(),
        "total_sessoes": total_sessoes,
        "erro_externo": erro,
        "dias_semana": DIAS_SEMANA,
        "dias_atendimento": dias_atendimento_set,
        "dias_com_agenda": dias_com_agenda,
    })


@router.post("/novo")
async def novo_agendamento(
    request: Request,
    pessoa_id: int = Form(...),
    data: str = Form(...),
    medium_id: int = Form(...),
    data_de: str = Form(""),
    data_ate: str = Form(""),
    dia_semana: str = Form("-1"),
    pessoa_nome: str = Form(""),
    qtd_sessoes: int = Form(1),
    frequencia: str = Form("semanal"),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    # Parse data (pode vir DD/MM/AAAA ou YYYY-MM-DD)
    if len(data) == 10 and "-" not in data:
        data_parts = data.split("/")
        if len(data_parts) == 3:
            data = f"{data_parts[2]}-{data_parts[1]}-{data_parts[0]}"

    params = (f"medium_id={medium_id}&de={data_de}&ate={data_ate}"
              f"&dia_semana={dia_semana}&pessoa_nome={urllib.parse.quote(pessoa_nome, safe='')}")

    def _voltar(erro: str):
        return RedirectResponse(
            url=f"/agenda?{params}&erro={urllib.parse.quote(erro)}", status_code=303
        )

    with conectar() as conn:
        # Validar data inicial
        try:
            data_inicio = date.fromisoformat(data)
        except ValueError:
            return _voltar("Data inválida.")

        total = max(1, int(qtd_sessoes or 1))
        inicio = banco._proxima_data_trabalho(data_inicio, banco._dias_atendimento(conn))

        # A Agenda NÃO contorna o calendário nem o piso de passe: as datas são
        # as mesmas que o Acolhimento Fraterno usaria, sempre em dia de
        # atendimento cadastrado (R4).
        datas = banco.datas_agendamento(data_inicio, total, frequencia, banco._dias_atendimento(conn))

        # Verificar se já tem agendamento em qualquer uma das datas
        for d in datas:
            ja_tem = conn.execute(
                """SELECT 1 FROM agendamentos a
                   JOIN planos_tratamento pt ON pt.id = a.plano_id
                   JOIN plano_pessoas pp ON pp.plano_id = pt.id
                   WHERE pp.pessoa_id = %s AND a.data = %s AND a.status = 'agendado'
                   LIMIT 1""",
                (pessoa_id, d)
            ).fetchone()
            if ja_tem:
                return _voltar(
                    f"Pessoa já possui agendamento em {date.fromisoformat(d).strftime('%d/%m/%Y')}."
                )

        # Cria o plano da Agenda. `sessoes_com_passe = 0` significa "somente o
        # piso obrigatório do centro" (as 3 primeiras sessões); a partir da 4ª
        # o passe é opcional, conforme R4.
        cur = conn.execute(
            """INSERT INTO planos_tratamento
               (medium_id, sessoes_total, sessoes_realizadas, data_inicio,
                frequencia, status, sessoes_com_passe)
               VALUES (%s, %s, 0, %s, %s, 'ativo', 0)
               RETURNING id""",
            (medium_id, total, inicio.isoformat(), frequencia)
        )
        plano_id = cur.fetchone()["id"]
        conn.execute(
            "INSERT INTO plano_pessoas (plano_id, pessoa_id) VALUES (%s, %s)",
            (plano_id, pessoa_id)
        )

        # Fonte única da regra do passe e do calendário
        banco.gerar_agendamentos_plano(conn, plano_id, inicio, frequencia, total, 0)

    return RedirectResponse(url=f"/agenda?{params}", status_code=303)


# ── Imprimir agenda por médium e dia ──────────────────────────────────────────

@router.get("/imprimir", response_class=HTMLResponse)
async def imprimir_agenda(
    request: Request,
    medium_id: int = Query(0),
    dia: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    hoje = date.today().isoformat()
    dia = dia or hoje

    with conectar() as conn:
        medium = conn.execute(
            "SELECT id, nome_completo FROM mediuns WHERE id = %s", (medium_id,)
        ).fetchone()
        if not medium:
            return RedirectResponse(url="/agenda", status_code=303)

        sessoes = conn.execute(
            """
            SELECT
                a.id          AS agenda_id,
                a.data,
                a.status,
                a.requer_passe,
                a.encaixe,
                pt.id         AS plano_id,
                pt.sessoes_total,
                (SELECT COUNT(*) FROM agendamentos a2
                 WHERE a2.plano_id = a.plano_id AND a2.data <= a.data
                )             AS numero_sessao,
                STRING_AGG(pe.nome_completo, ', ' ORDER BY pe.nome_completo)
                              AS pessoas_nomes
            FROM agendamentos a
            JOIN planos_tratamento pt ON pt.id = a.plano_id
            JOIN plano_pessoas pp     ON pp.plano_id = pt.id
            JOIN pessoas pe           ON pe.id = pp.pessoa_id
            WHERE pt.medium_id = %s AND a.data = %s AND a.status = 'agendado'
            GROUP BY a.id, a.data, a.status, a.requer_passe, a.encaixe,
                     pt.id, pt.sessoes_total
            ORDER BY a.id
            """,
            (medium_id, dia)
        ).fetchall()

        resultado = [dict(s) for s in sessoes]

    return templates.TemplateResponse("imprimir_agenda.html", {
        "request": request,
        "atendente": atendente,
        "medium": dict(medium),
        "dia": dia,
        "agendamentos": resultado,
    })
