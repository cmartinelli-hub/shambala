from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar, _normalizar
from rotas.auth import obter_atendente_logado
from servico_presenca import rotulo_origem
from templates_config import templates

router = APIRouter(prefix="/relatorios")


def _guard(request: Request):
    from rotas.permissoes import guarda_modulo
    return guarda_modulo(request, "relatorios")


def _hoje_sp() -> date:
    """Hoje no fuso do servidor de negócio — não no fuso do processo."""
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def _mes_atual():
    hoje = _hoje_sp()
    return hoje.replace(day=1).isoformat(), hoje.isoformat()


def _validar_periodo(ini: str, fim: str) -> str | None:
    """Validação no servidor: data inválida ou invertida dá mensagem clara,
    nunca 500."""
    try:
        d_ini = date.fromisoformat(ini)
        d_fim = date.fromisoformat(fim)
    except ValueError:
        return "Período inválido — use datas no formato AAAA-MM-DD."
    if d_ini > d_fim:
        return "A data inicial é posterior à final — verifique o período."
    return None


_NOMES_DIA_REL = ["Segunda-feira", "Terça-feira", "Quarta-feira",
                  "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]

# Origens aceitas no filtro de relatório; "nao_informada" é o legado NULL.
_ORIGENS_RELATORIO = ("recepcao", "terminal_qr", "terminal_codigo",
                      "correcao_administrativa", "nao_informada")


def _filtro_relatorio(dia_semana: str, origem: str, params: list):
    """Devolve (trecho_sql_dia, trecho_sql_origem, erro) validando os filtros.

    Os fragmentos são fixos ou placeholders %s com valor anexado em `params`
    — nada do usuário entra interpolado no SQL.
    """
    filtro_dia = ""
    if dia_semana != "":
        if dia_semana not in ("0", "1", "2", "3", "4", "5", "6"):
            return "", "", "Dia da semana fora dos valores permitidos."
        # filtro 0=Segunda..6=Domingo; SQL é ISODOW (1=Segunda..7=Domingo)
        filtro_dia = " AND EXTRACT(ISODOW FROM tp.data) = %s"
        params.append(int(dia_semana) + 1)
    filtro_origem = ""
    if origem:
        if origem not in _ORIGENS_RELATORIO:
            return filtro_dia, "", "Origem desconhecida no filtro."
        if origem == "nao_informada":
            filtro_origem = " AND tp.origem_chegada IS NULL"
        else:
            filtro_origem = " AND tp.origem_chegada = %s"
            params.append(origem)
    return filtro_dia, filtro_origem, None


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
# Fatos vigentes apenas: a tabela operacional não guarda anulações (fato
# anulado deixa de existir; a auditoria fica fora das contagens por
# construção). Sem registro NÃO é falta — as colunas contam apenas os
# registros explícitos de presença e de ausência.

@router.get("/presenca-trabalhadores", response_class=HTMLResponse)
async def presenca_geral_trabalhadores(
    request: Request,
    data_ini: str = Query(""),
    data_fim: str = Query(""),
    dia_semana: str = Query(""),
    origem: str = Query(""),
    trabalhador_id: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    # O seletor pesquisável pode enviar vazio (todos) ou um id escolhido.
    try:
        trabalhador_id = int(trabalhador_id) if trabalhador_id else 0
    except ValueError:
        trabalhador_id = 0

    ini_padrao, fim_padrao = _mes_atual()
    data_ini = data_ini or ini_padrao
    data_fim = data_fim or fim_padrao

    with conectar() as conn:
        opcoes_trabalhadores = [
            dict(r) for r in conn.execute(
                "SELECT id, nome_completo FROM trabalhadores "
                "ORDER BY nome_completo"
            ).fetchall()
        ]

    contexto_base = {
        "request": request,
        "atendente": atendente,
        "data_ini": data_ini,
        "data_fim": data_fim,
        "dia_semana": dia_semana,
        "origem": origem,
        "trabalhador_id": trabalhador_id,
        "opcoes_trabalhadores": opcoes_trabalhadores,
        "nomes_dia": _NOMES_DIA_REL,
        "origens_filtro": _ORIGENS_RELATORIO,
        "rotulo_origem": rotulo_origem,
    }

    erro = _validar_periodo(data_ini, data_fim)
    if erro:
        return templates.TemplateResponse(
            "relatorios/presenca_trabalhadores.html",
            {**contexto_base, "trabalhadores": [], "erro": erro})

    params: list = [data_ini, data_fim]
    filtro_dia, filtro_origem, erro = _filtro_relatorio(dia_semana, origem, params)
    filtro_trab = ""
    if trabalhador_id:
        filtro_trab = " AND t.id = %s"
        params.append(trabalhador_id)
    if erro:
        return templates.TemplateResponse(
            "relatorios/presenca_trabalhadores.html",
            {**contexto_base, "trabalhadores": [], "erro": erro})

    with conectar() as conn:
        rows = conn.execute(
            f"""
            SELECT
                t.id,
                t.nome_completo,
                t.ativo,
                COUNT(tp.id)                                                AS total_dias,
                SUM(CASE WHEN tp.presente = 1 THEN 1 ELSE 0 END)            AS presencas,
                SUM(CASE WHEN tp.presente = 0 THEN 1 ELSE 0 END)            AS ausencias
            FROM trabalhadores t
            LEFT JOIN trabalhador_presenca tp
                ON tp.trabalhador_id = t.id
                AND tp.data BETWEEN %s::date AND %s::date {filtro_dia} {filtro_origem}
            WHERE 1=1 {filtro_trab}
            GROUP BY t.id, t.nome_completo, t.ativo
            -- só quem tem lançamento explícito no período (presença OU
            -- ausência registrada); inativo com fato continua listado —
            -- inativar alguém não apaga o passado
            HAVING COUNT(tp.id) > 0
            ORDER BY t.nome_completo
            """,
            params,
        ).fetchall()

    return templates.TemplateResponse("relatorios/presenca_trabalhadores.html", {
        **contexto_base,
        "trabalhadores": [dict(r) for r in rows],
        "erro": None,
    })


# ── Presença Individual do Trabalhador ─────────────────────────────────────────

@router.get("/presenca-trabalhador", response_class=HTMLResponse)
async def presenca_individual(
    request: Request,
    trabalhador_id: str = Query(""),
    data_ini: str = Query(""),
    data_fim: str = Query(""),
    dia_semana: str = Query(""),
    origem: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    try:
        trabalhador_id = int(trabalhador_id) if trabalhador_id else 0
    except ValueError:
        trabalhador_id = 0

    ini_padrao, fim_padrao = _mes_atual()
    data_ini = data_ini or ini_padrao
    data_fim = data_fim or fim_padrao

    with conectar() as conn:
        # Lista completa para o seletor: inativos continuam consultáveis —
        # o passado não desaparece após a inativação.
        trabalhadores_lista = conn.execute(
            "SELECT id, nome_completo, ativo FROM trabalhadores "
            "ORDER BY nome_completo"
        ).fetchall()
        trabalhadores_lista = [dict(r) for r in trabalhadores_lista]

    from rotas.permissoes import usuario_pode
    contexto_base = {
        "request": request,
        "atendente": atendente,
        "trabalhador_id": trabalhador_id,
        "data_ini": data_ini,
        "data_fim": data_fim,
        "dia_semana": dia_semana,
        "origem": origem,
        "nomes_dia": _NOMES_DIA_REL,
        "origens_filtro": _ORIGENS_RELATORIO,
        "rotulo_origem": rotulo_origem,
        # O selo "ajustado" não revela nada de auditoria — só indica que o
        # registro já foi corrigido; a consulta do histórico é outro módulo.
        "pode_ver_auditoria": usuario_pode(
            atendente["id"], "checkin.trabalhadores.correcoes", "ler"),
    }

    erro = _validar_periodo(data_ini, data_fim)
    if erro:
        return templates.TemplateResponse(
            "relatorios/presenca_individual.html",
            {**contexto_base, "trabalhadores": trabalhadores_lista,
             "trabalhador": None,
             "registros": [], "presencas": 0, "ausencias": 0, "erro": erro})

    params_filtro: list = []
    filtro_dia, filtro_origem, erro = _filtro_relatorio(
        dia_semana, origem, params_filtro)
    if erro:
        return templates.TemplateResponse(
            "relatorios/presenca_individual.html",
            {**contexto_base, "trabalhadores": trabalhadores_lista,
             "trabalhador": None,
             "registros": [], "presencas": 0, "ausencias": 0, "erro": erro})

    trabalhador = None
    registros = []
    presencas = 0
    ausencias = 0

    with conectar() as conn:
        if trabalhador_id:
            trabalhador = conn.execute(
                "SELECT id, nome_completo, ativo FROM trabalhadores WHERE id = %s",
                (trabalhador_id,),
            ).fetchone()
            if trabalhador:
                trabalhador = dict(trabalhador)
                params = [trabalhador_id, data_ini, data_fim] + params_filtro
                hist = conn.execute(
                    f"""SELECT tp.presente, tp.hora_chegada, tp.hora_saida,
                              tp.origem_chegada, tp.data::text AS data,
                              EXISTS (
                                  SELECT 1 FROM trabalhador_presenca_correcao c
                                   WHERE c.trabalhador_id = tp.trabalhador_id
                                     AND c.data_fato = tp.data
                              ) AS ajustado
                       FROM trabalhador_presenca tp
                       WHERE tp.trabalhador_id = %s
                         AND tp.data BETWEEN %s::date AND %s::date
                         {filtro_dia} {filtro_origem}
                       ORDER BY tp.data DESC""",
                    params,
                ).fetchall()
                registros = [dict(r) for r in hist]
                # Dia da semana calculado em Python (0=Segunda..6=Domingo) —
                # to_char(...,'Day') depende de locale e ainda vem com padding.
                for r in registros:
                    r["dia_nome"] = _NOMES_DIA_REL[
                        date.fromisoformat(r["data"]).weekday()]
                presencas = sum(1 for r in registros if r["presente"] == 1)
                ausencias = sum(1 for r in registros if r["presente"] == 0)

    return templates.TemplateResponse("relatorios/presenca_individual.html", {
        **contexto_base,
        "trabalhadores": trabalhadores_lista,
        "trabalhador": trabalhador,
        "registros": registros,
        "presencas": presencas,
        "ausencias": ausencias,
        "erro": None,
    })
