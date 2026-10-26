from datetime import date, timedelta
import urllib.parse
from fastapi import APIRouter, Request, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar, _normalizar, data_atendimento_valida
import banco
import servico_conclusao_planos
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


def _pode_abrir_agenda_plano(atendente) -> bool:
    from rotas.permissoes import usuario_pode
    return (usuario_pode(atendente["id"], "cadastros.mediuns", "ler")
            or usuario_pode(atendente["id"], "cadastros.mediuns.planos", "ler"))


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
    pessoa_id: int = Query(0),
    erro: str = Query(""),
    msg: str = Query(""),
    plano_ref: int = Query(0),
    plano_med: int = Query(0),
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

        # Bloqueio de plano único apontado na própria tela: o erro vem do POST
        # /novo; reconsultamos (não confiamos em id vindo da URL) os planos
        # ativos da pessoa e oferecemos a agenda deles a quem pode abri-la,
        # por referências humanas (médium e início), nunca "plano 1799".
        plano_ativo = None
        pode_abrir_agenda_plano = False
        if erro and pessoa_id:
            pa = banco.planos_ativos_da_pessoa(conn, pessoa_id)
            if pa:
                plano_ativo = _referencia_plano(conn, pa[0])
                pode_abrir_agenda_plano = _pode_abrir_agenda_plano(atendente)

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
        "msg_externo": msg,
        "link_agenda_plano": (
            f"/cadastros/mediuns/{plano_med}/planos/{plano_ref}/agenda"
            if msg and plano_ref and plano_med and _pode_abrir_agenda_plano(atendente)
            else ""
        ),
        "plano_ativo": plano_ativo,
        "pode_abrir_agenda_plano": pode_abrir_agenda_plano,
        "dias_semana": DIAS_SEMANA,
        "dias_atendimento": dias_atendimento_set,
        "dias_com_agenda": dias_com_agenda,
    })


def _referencia_plano(conn, plano) -> dict:
    """Referência humana de um plano para mensagens: médium, início e situação
    das sessões. IDs internos ficam só nas rotas/auditoria (TASK_018 §4)."""
    nome = conn.execute(
        "SELECT nome_completo FROM mediuns WHERE id = %s", (plano["medium_id"],)
    ).fetchone()
    previstas = plano["sessoes_previstas"]
    if previstas is None:
        previstas = plano["sessoes_total"]
    extras = plano["sessoes_adicionais"] or 0
    resumo = f"{plano['sessoes_realizadas']} cumpridas de {previstas}"
    if extras:
        resumo += f" + {extras} extra{'s' if extras > 1 else ''} "
        resumo += "(acrescentada)" if extras == 1 else "(acrescentadas)"
    return {
        "id": plano["id"],
        "medium_id": plano["medium_id"],
        "medium_nome": nome["nome_completo"] if nome else "médium não cadastrado",
        "inicio_br": date.fromisoformat(plano["data_inicio"]).strftime("%d/%m/%Y"),
        "resumo": resumo,
        "legado": (plano["regra_conclusao"] or 0) != 1,
        # TASK_018 (complemento §2): vagas em aberto é o que permite oferecer
        # "reposição" nesta tela — sem supor nada no POST.
        "vagas_repor": servico_conclusao_planos.vagas_a_repor(conn, plano["id"]),
    }


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
    confirmar: int = Form(0),
    plano_destino: int = Form(0),
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
              f"&dia_semana={dia_semana}&pessoa_id={pessoa_id}"
              f"&pessoa_nome={urllib.parse.quote(pessoa_nome, safe='')}")

    def _voltar(erro: str):
        return RedirectResponse(
            url=f"/agenda?{params}&erro={urllib.parse.quote(erro)}", status_code=303
        )

    ctx_base = {
        "request": request,
        "atendente": atendente,
        "data": data,
        "medium_id": medium_id,
        "pessoa_id": pessoa_id,
        "data_de": data_de,
        "data_ate": data_ate,
        "dia_semana": dia_semana,
        "pessoa_nome": pessoa_nome,
        "qtd_sessoes": qtd_sessoes,
        "frequencia": frequencia,
    }

    with conectar() as conn:
        # Validar data inicial
        try:
            data_inicio = date.fromisoformat(data)
        except ValueError:
            return _voltar("Data inválida.")

        pessoa = conn.execute(
            "SELECT id, nome_completo FROM pessoas WHERE id = %s", (pessoa_id,)
        ).fetchone()
        if not pessoa:
            return _voltar("Selecione uma pessoa válida.")
        medium = conn.execute(
            "SELECT id, nome_completo FROM mediuns WHERE id = %s", (medium_id,)
        ).fetchone()
        if not medium:
            return _voltar("Selecione um médium válido.")

        def _pagina(modo, refs, erro=""):
            # A página intermediária preserva os campos preenchidos (ocultos no
            # próprio formulário) — nada é escrito até a confirmação.
            ctx = dict(ctx_base)
            ctx.update({
                "pessoa_nome_completo": pessoa["nome_completo"],
                "medium_nome": medium["nome_completo"],
                "modo": modo,
                "planos": refs,
                "erro_externo": erro,
                "pode_abrir_agenda_plano": _pode_abrir_agenda_plano(atendente),
            })
            return templates.TemplateResponse("agenda/confirmar_plano.html", ctx)

        # ── Caminho habitual com plano ativo (TASK_018 §4): a sessão entra no
        # plano da pessoa, de forma explícita — nunca em plano paralelo
        # silencioso. Nada é escrito antes da confirmação.
        planos_ativos = banco.planos_ativos_da_pessoa(conn, pessoa_id)
        if planos_ativos:
            compat = [p for p in planos_ativos if p["medium_id"] == medium_id]

            if not compat:
                # Plano ativo de OUTRO médium: não anexar ao médium errado,
                # não criar plano para contornar plano único — explicar o
                # conflito e oferecer acesso ao plano permitido.
                return _pagina("conflito", [_referencia_plano(conn, p) for p in planos_ativos])

            if not confirmar:
                # Um ou vários planos: escolha/apresentação explícita por
                # referências humanas (médium, início, situação). Sem escolha
                # automática por id, linha ou recência.
                refs = [_referencia_plano(conn, p) for p in compat]
                return _pagina("escolha" if len(refs) > 1 else "confirmar", refs)

            # Confirmação: revalidar no servidor (id do navegador não é
            # autorização) pessoa↔plano, médium e situação ativa.
            refs = [_referencia_plano(conn, p) for p in compat]
            alvo = [p for p in compat if p["id"] == plano_destino]
            if not alvo:
                return _pagina(
                    "escolha" if len(refs) > 1 else "confirmar", refs,
                    erro="Este plano não está mais entre os planos ativos da "
                         "pessoa. Recarregue a agenda e tente novamente.",
                )
            # O tipo (extra × reposição) é escolhido POR plano na tela — com
            # vários planos, um único campo global não representaria a escolha.
            form = await request.form()
            tipo = str(form.get(f"tipo_{alvo[0]['id']}", "adicao") or "adicao")
            if not data_atendimento_valida(conn, data):
                return _pagina(
                    "escolha" if len(refs) > 1 else "confirmar", refs,
                    erro=f"{data_inicio.strftime('%d/%m/%Y')} não é dia de "
                         "atendimento. Escolha um dia cadastrado em Configurações.",
                )
            res = servico_conclusao_planos.acrescentar_sessao(
                conn, alvo[0]["id"], data, requer_passe=0, encaixe=0,
                tipo=tipo, autor_id=atendente["id"],
            )
            if res == "data_ja_agendada":
                return _pagina(
                    "escolha" if len(refs) > 1 else "confirmar", refs,
                    erro=f"Já existe atendimento agendado neste plano em "
                         f"{data_inicio.strftime('%d/%m/%Y')}.",
                )
            if res == "sem_vaga_para_repor":
                return _pagina(
                    "escolha" if len(refs) > 1 else "confirmar", refs,
                    erro="Este plano não tem sessão prevista em aberto para repor "
                         "— as que não aconteceram já têm substituta. O que se "
                         "acrescenta agora é sessão EXTRA (extensão).",
                )
            if res == "tipo_invalido":
                return _pagina(
                    "escolha" if len(refs) > 1 else "confirmar", refs,
                    erro="Tipo de sessão desconhecido. Recarregue a agenda e "
                         "escolha entre sessão extra e reposição.",
                )
            if res == "plano_nao_ativo":
                # Plano saiu de ativo entre a tela e a escrita (ex.: a última
                # realização concorrente o concluiu). Não reabre, não cria
                # outro, não escreve nada parcial — orientar recarregar.
                return _voltar(
                    "O plano acabou de sair da situação ativa (concluído ou "
                    "encerrado). Recarregue a agenda antes de agendar."
                )
            _reaplicar_piso_passe(conn, alvo[0]["id"])
            ref = _referencia_plano(conn, alvo[0])
            msg = (
                f"Atendimento de {pessoa['nome_completo']} "
                + ("reposto no" if tipo == "reposicao" else "acrescentado ao")
                + f" plano com {ref['medium_nome']}, iniciado em {ref['inicio_br']}."
            )
            return RedirectResponse(
                url=f"/agenda?{params}&msg={urllib.parse.quote(msg)}"
                    f"&plano_ref={ref['id']}&plano_med={ref['medium_id']}",
                status_code=303,
            )

        # ── Sem plano ativo: fluxo existente de novo atendimento (esta tela
        # cria planos; a tarefa não concede poder geral de criar plano clínico).
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
        # o passe é opcional, conforme R4. `regra_conclusao=1` marca o plano
        # como nascido sob a regra prospectiva de conclusão (TASK_018).
        cur = conn.execute(
            """INSERT INTO planos_tratamento
               (medium_id, sessoes_total, sessoes_previstas, sessoes_realizadas,
                data_inicio, frequencia, status, sessoes_com_passe, regra_conclusao)
               VALUES (%s, %s, %s, 0, %s, %s, 'ativo', 0, 1)
               RETURNING id""",
            (medium_id, total, total, inicio.isoformat(), frequencia)
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
            """SELECT pt.id, pt.medium_id, m.nome_completo
               FROM planos_tratamento pt
               JOIN plano_pessoas pp ON pp.plano_id = pt.id
               JOIN mediuns m ON m.id = pt.medium_id
               WHERE pp.pessoa_id = %s AND pt.status = 'ativo'""",
            (pessoa_id,)
        ).fetchall()
        # TASK_018 (complemento §2): a tela só oferece "reposição" onde há
        # sessão prevista não cumprida em aberto neste plano.
        mesmo = bool(planos) and planos[0]["medium_id"] == medium_id
        vagas_repor = (servico_conclusao_planos.vagas_a_repor(conn, planos[0]["id"])
                       if mesmo else 0)
    if not planos:
        return JSONResponse({"tem_plano": False, "medium_id": None, "medium_nome": None})
    p = planos[0]
    return JSONResponse({
        "tem_plano": True,
        "mesmo_medium": mesmo,
        "medium_id": p["medium_id"],
        "medium_nome": p["nome_completo"],
        "vagas_repor": vagas_repor,
    })


@router.post("/medium/lancar")
async def agenda_medium_lancar(
    request: Request,
    pessoa_id: int = Form(...),
    medium_id: int = Form(...),
    data: str = Form(...),
    qtd_sessoes: int = Form(10),
    frequencia: str = Form("semanal"),
    tipo: str = Form("adicao"),
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

        # Caso 1: plano ativo no MÉDIUM selecionado → acrescenta 1 sessão ao
        # plano pela transição única do serviço (trava + extensão, TASK_018).
        plano = _plano_ativo_da_pessoa_em_medium(conn, pessoa_id, medium_id)
        if plano:
            res = servico_conclusao_planos.acrescentar_sessao(
                conn, plano["id"], data_iso, requer_passe=0, encaixe=0,
                tipo=tipo, autor_id=atendente["id"],
            )
            if res == "data_ja_agendada":
                return _voltar(erro="Pessoa já possui atendimento agendado nesta data.")
            if res == "sem_vaga_para_repor":
                return _voltar(erro=(
                    "Este plano não tem sessão prevista em aberto para repor — as "
                    "que não aconteceram já têm substituta. Lance como sessão extra "
                    "se for isso mesmo."
                ))
            if res == "tipo_invalido":
                return _voltar(erro=(
                    "Tipo de sessão desconhecido. Recarregue a agenda e escolha "
                    "entre sessão extra e reposição."
                ))
            if res == "plano_nao_ativo":
                return _voltar(erro=(
                    "O plano acabou de sair da situação ativa. "
                    "Recarregue a agenda antes de lançar."
                ))
            _reaplicar_piso_passe(conn, plano["id"])
            return _voltar(msg=(
                f"Sessão de {pessoa['nome_completo']} "
                + ("reposta no" if tipo == "reposicao" else "acrescentada ao")
                + f" plano em {date.fromisoformat(data_iso).strftime('%d/%m/%Y')}"
                + (" — sem virar extensão." if tipo == "reposicao" else ".")
            ))

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
               (medium_id, sessoes_total, sessoes_previstas, sessoes_realizadas,
                data_inicio, frequencia, status, sessoes_com_passe, regra_conclusao)
               VALUES (%s, %s, %s, 0, %s, %s, 'ativo', 0, 1)
               RETURNING id""",
            (medium_id, total, total, inicio.isoformat(), frequencia)
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
