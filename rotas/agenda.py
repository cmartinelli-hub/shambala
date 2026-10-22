from datetime import date, timedelta
import urllib.parse
from fastapi import APIRouter, Request, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar, _normalizar, data_atendimento_valida
import banco
from fastapi.responses import JSONResponse
from rotas.auth import obter_atendente_logado, e_admin
from rotas.mediuns import _reaplicar_piso_passe
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

# Telas com permissão própria: /agenda/medium* exige "agenda.medium"; o
# resto do prefixo fica em "agenda" (padrão de mapa de caminho de dia.py).
_MODULO_AGENDA = [
    ("/agenda/medium", "agenda.medium"),
]


def _guard(request: Request):
    from rotas.permissoes import guarda_modulo
    path = request.url.path
    modulo = next((m for p, m in _MODULO_AGENDA if path.startswith(p)), "agenda")
    return guarda_modulo(request, modulo)


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

        # Regra do plano único: com plano ativo, as sessões devem entrar pela
        # agenda desse plano (encaixe/reagendar) — esta tela cria planos novos.
        if banco.planos_ativos_da_pessoa(conn, pessoa_id):
            return _voltar(
                "Pessoa já possui plano ativo. Use a agenda do plano para encaixar "
                "sessões ou encerre o plano antes de criar um novo."
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


# ── Agenda do Médium (a tela de quem gerencia as agendas) ────────────────────

def _parse_data_medium(valor: str):
    """Aceita DD/MM/AAAA ou YYYY-MM-DD; retorna ISO ou None."""
    v = (valor or "").strip()
    if len(v) == 10 and "/" in v:
        d, m, a = v.split("/")
        v = f"{a.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
    try:
        date.fromisoformat(v)
        return v
    except ValueError:
        return None


def _plano_ativo_da_pessoa_em_medium(conn, pessoa_id: int, medium_id: int):
    """Plano ativo da pessoa no médium (plano único ⇒ no máximo um)."""
    return conn.execute(
        """SELECT pt.* FROM planos_tratamento pt
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           WHERE pp.pessoa_id = %s AND pt.status = 'ativo' AND pt.medium_id = %s
           LIMIT 1""",
        (pessoa_id, medium_id)
    ).fetchone()


def _mediuns_do_usuario(conn, usuario_id: int, eh_admin: bool) -> list:
    """Agendas que este usuário enxerga: só os médiuns vinculados a ele.

    O vínculo (`atendentes_mediuns`) é o escopo do cambona — sem ele a tela fica
    vazia e o administrador precisa marcar as agendas na tela de Usuários.
    Admin é exceção: vê todas, para poder conferir e corrigir.
    """
    if eh_admin:
        return conn.execute(
            "SELECT id, nome_completo FROM mediuns WHERE ativo = 1 ORDER BY nome_completo"
        ).fetchall()
    return conn.execute(
        """SELECT m.id, m.nome_completo FROM atendentes_mediuns cm
           JOIN mediuns m ON m.id = cm.medium_id
           WHERE cm.atendente_id = %s AND m.ativo = 1
           ORDER BY m.nome_completo""",
        (usuario_id,)
    ).fetchall()


@router.get("/medium", response_class=HTMLResponse)
async def agenda_medium(
    request: Request,
    medium_id: int = Query(0),
    msg: str = Query(""),
    erro: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    hoje = date.today()
    data_de = hoje.isoformat()
    data_ate = (hoje + timedelta(days=30)).isoformat()

    with conectar() as conn:
        mediuns_chips = _mediuns_do_usuario(conn, atendente["id"], e_admin(request))
        ids_chips = {m["id"] for m in mediuns_chips}

        # Sem vínculo não há agenda a mostrar — o escopo do cambona É o vínculo.
        agendamentos = []
        dia_ant = None
        atendidos_dia = []

        # Data padrão do formulário: o próximo dia de atendimento (nem sempre é hoje)
        data_sugerida = banco._proxima_data_trabalho(
            hoje, banco._dias_atendimento(conn)).isoformat()

        if ids_chips:
            if medium_id in ids_chips:
                filtro_medium = "AND pt.medium_id = %s"
                params = [data_de, data_ate, medium_id]
            else:
                filtro_medium = "AND pt.medium_id = ANY(%s)"
                params = [data_de, data_ate, sorted(ids_chips)]

            agendamentos = conn.execute(
                f"""SELECT
                        a.id, a.data,
                        a.plano_id,
                        m.id AS medium_id, m.nome_completo AS medium_nome,
                        STRING_AGG(pe.nome_completo, ', ') AS pessoas,
                        MIN(pe.id) AS pessoa_id_principal,
                        pt.sessoes_realizadas, pt.sessoes_total
                    FROM agendamentos a
                    JOIN planos_tratamento pt ON pt.id = a.plano_id
                    JOIN mediuns m ON m.id = pt.medium_id
                    JOIN plano_pessoas pp ON pp.plano_id = pt.id
                    JOIN pessoas pe ON pe.id = pp.pessoa_id
                    WHERE a.data BETWEEN %s AND %s
                      AND a.status = 'agendado'
                      {filtro_medium}
                    GROUP BY a.id, a.data, a.plano_id, m.id, m.nome_completo,
                             pt.sessoes_realizadas, pt.sessoes_total
                    ORDER BY a.data, m.nome_completo""",
                params
            ).fetchall()

            # Último dia de atendimento (dias_trabalho.data ≤ hoje) e seus atendidos
            dia_ant = conn.execute(
                "SELECT id, data FROM dias_trabalho WHERE data <= %s ORDER BY data DESC LIMIT 1",
                (hoje.isoformat(),)
            ).fetchone()
            if dia_ant:
                if medium_id in ids_chips:
                    filtro_dia = "AND c.medium_id = %s"
                    params_dia = [dia_ant["id"], medium_id]
                else:
                    filtro_dia = "AND c.medium_id = ANY(%s)"
                    params_dia = [dia_ant["id"], sorted(ids_chips)]
                atendidos_dia = conn.execute(
                    f"""SELECT c.hora_checkin, p.id AS pessoa_id, p.nome_completo,
                               COALESCE(m.nome_completo, '—') AS medium_nome
                        FROM checkins c
                        JOIN pessoas p ON p.id = c.pessoa_id
                        LEFT JOIN mediuns m ON m.id = c.medium_id
                        WHERE c.dia_trabalho_id = %s
                          AND c.atendimento_realizado = 1
                          AND c.codigo_atendimento IS NOT NULL
                          AND c.id = (
                              SELECT MIN(c2.id) FROM checkins c2
                              WHERE c2.dia_trabalho_id = c.dia_trabalho_id
                                AND c2.codigo_atendimento = c.codigo_atendimento
                                AND c2.atendimento_realizado = 1
                          )
                          {filtro_dia}
                        ORDER BY c.hora_checkin""",
                    params_dia
                ).fetchall()

    por_data: dict = {}
    for ag in agendamentos:
        por_data.setdefault(ag["data"], []).append(dict(ag))

    from rotas.permissoes import usuario_pode
    pode_agenda_completa = usuario_pode(atendente["id"], "agenda", "ler")

    return templates.TemplateResponse("agenda/medium.html", {
        "request": request,
        "atendente": atendente,
        "mediuns": [dict(m) for m in mediuns_chips],
        "medium_id": medium_id if medium_id in ids_chips else 0,
        "por_data": por_data,
        "total_sessoes": sum(len(v) for v in por_data.values()),
        "dia_anterior": dict(dia_ant) if dia_ant else None,
        "atendidos_dia": [dict(r) for r in atendidos_dia],
        "pode_agenda_completa": pode_agenda_completa,
        "msg": msg,
        "erro": erro,
        "hoje": hoje.isoformat(),
        "data_sugerida": data_sugerida,
    })


@router.get("/medium/situacao", response_class=JSONResponse)
async def agenda_medium_situacao(request: Request, pessoa_id: int = Query(0), medium_id: int = Query(0)):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        planos = conn.execute(
            """SELECT pt.medium_id, m.nome_completo
               FROM planos_tratamento pt
               JOIN plano_pessoas pp ON pp.plano_id = pt.id
               JOIN mediuns m ON m.id = pt.medium_id
               WHERE pp.pessoa_id = %s AND pt.status = 'ativo'""",
            (pessoa_id,)
        ).fetchall()
    if not planos:
        return JSONResponse({"tem_plano": False, "medium_id": None, "medium_nome": None})
    p = planos[0]
    return JSONResponse({
        "tem_plano": True,
        "mesmo_medium": p["medium_id"] == medium_id,
        "medium_id": p["medium_id"],
        "medium_nome": p["nome_completo"],
    })


@router.post("/medium/lancar")
async def agenda_medium_lancar(
    request: Request,
    pessoa_id: int = Form(...),
    medium_id: int = Form(...),
    data: str = Form(...),
    qtd_sessoes: int = Form(10),
    frequencia: str = Form("semanal"),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    def _voltar(msg=None, erro=None):
        q = f"?medium_id={medium_id}"
        if msg:
            q += f"&msg={urllib.parse.quote(msg)}"
        if erro:
            q += f"&erro={urllib.parse.quote(erro)}"
        return RedirectResponse(url=f"/agenda/medium{q}", status_code=303)

    data_iso = _parse_data_medium(data)
    if not data_iso:
        return _voltar(erro="Data inválida.")

    if frequencia not in FREQ_MAP:
        frequencia = "semanal"

    with conectar() as conn:
        pessoa = conn.execute(
            "SELECT id, nome_completo FROM pessoas WHERE id = %s", (pessoa_id,)
        ).fetchone()
        medium = conn.execute(
            "SELECT id, nome_completo FROM mediuns WHERE id = %s AND ativo = 1", (medium_id,)
        ).fetchone()
        if not pessoa or not medium:
            return _voltar(erro="Selecione uma pessoa e um médium válidos.")

        # O vínculo também limita o lançamento: o cambona cuida das agendas que
        # são suas (admin pode lançar em qualquer uma).
        meus = {m["id"] for m in _mediuns_do_usuario(conn, atendente["id"], e_admin(request))}
        if medium_id not in meus:
            return _voltar(erro=(
                f"{medium['nome_completo']} não está entre as suas agendas. "
                "Peça ao administrador para vincular este médium a você."
            ))

        # Nada é lançado em dia sem atendimento: melhor avisar a cambona do que
        # deslocar a data da papelada silenciosamente (R4).
        if not data_atendimento_valida(conn, data_iso):
            return _voltar(erro=(
                f"{date.fromisoformat(data_iso).strftime('%d/%m/%Y')} não é dia de atendimento. "
                "Confira a data na papelada ou as Configurações de dias de atendimento."
            ))

        # Caso 1: plano ativo no MÉDIUM selecionado → acrescenta 1 sessão avulsa.
        plano = _plano_ativo_da_pessoa_em_medium(conn, pessoa_id, medium_id)
        if plano:
            duplicado = conn.execute(
                """SELECT 1 FROM agendamentos
                   WHERE plano_id = %s AND data = %s AND status = 'agendado'
                   LIMIT 1""",
                (plano["id"], data_iso)
            ).fetchone()
            if duplicado:
                return _voltar(erro="Pessoa já possui atendimento agendado nesta data.")
            conn.execute(
                "INSERT INTO agendamentos (plano_id, data, status, requer_passe, encaixe) "
                "VALUES (%s, %s, 'agendado', 0, 0)",
                (plano["id"], data_iso)
            )
            _reaplicar_piso_passe(conn, plano["id"])
            return _voltar(msg=f"Sessão de {pessoa['nome_completo']} acrescentada ao plano em {date.fromisoformat(data_iso).strftime('%d/%m/%Y')}.")

        # Caso 2: plano ativo em OUTRO médium → não duplica (regra do plano único).
        outro = conn.execute(
            """SELECT m.nome_completo FROM planos_tratamento pt
               JOIN plano_pessoas pp ON pp.plano_id = pt.id
               JOIN mediuns m ON m.id = pt.medium_id
               WHERE pp.pessoa_id = %s AND pt.status = 'ativo'
               LIMIT 1""",
            (pessoa_id,)
        ).fetchone()
        if outro:
            return _voltar(erro=(
                f"{pessoa['nome_completo']} já tem plano ativo com {outro['nome_completo']} — "
                "valide com a recepção antes de lançar em outro médium."
            ))

        # Caso 3: sem plano ativo → cria plano no médium (mesma regra do /agenda POST /novo).
        total = max(1, int(qtd_sessoes or 10))
        data_inicio = date.fromisoformat(data_iso)
        inicio = banco._proxima_data_trabalho(data_inicio, banco._dias_atendimento(conn))
        datas = banco.datas_agendamento(data_inicio, total, frequencia, banco._dias_atendimento(conn))

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
                return _voltar(erro=(
                    f"{pessoa['nome_completo']} já possui atendimento agendado em "
                    f"{date.fromisoformat(d).strftime('%d/%m/%Y')}."
                ))

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
        banco.gerar_agendamentos_plano(conn, plano_id, inicio, frequencia, total, 0)

    return _voltar(msg=(
        f"Plano criado para {pessoa['nome_completo']} com {medium['nome_completo']} — "
        f"{total} sessões ({frequencia})."
    ))
