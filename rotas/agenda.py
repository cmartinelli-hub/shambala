from datetime import date, timedelta
import urllib.parse
from fastapi import APIRouter, Request, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/agenda")


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


@router.get("", response_class=HTMLResponse)
async def agenda(
    request: Request,
    medium_id: int = Query(0),
    de: str = Query(""),
    ate: str = Query(""),
    erro: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    hoje = date.today()
    data_de  = de  if de  else hoje.isoformat()
    data_ate = ate if ate else (hoje + timedelta(days=30)).isoformat()

    with conectar() as conn:
        mediuns = conn.execute(
            "SELECT id, nome_completo FROM mediuns WHERE ativo=1 ORDER BY nome_completo"
        ).fetchall()

        filtro_medium = "AND pt.medium_id = %s" if medium_id else ""
        params = [data_de, data_ate]
        if medium_id:
            params.append(medium_id)

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
                GROUP BY a.id, a.data, a.status, a.requer_passe, a.encaixe,
                         a.plano_id, m.id, m.nome_completo,
                         pt.sessoes_realizadas, pt.sessoes_total, pt.frequencia
                ORDER BY a.data, m.nome_completo""",
            params
        ).fetchall()


    # Agrupar por data
    por_data: dict = {}
    for ag in agendamentos:
        d = ag["data"]
        if d not in por_data:
            por_data[d] = []
        por_data[d].append(dict(ag))

    total_sessoes = sum(len(v) for v in por_data.values())

    return templates.TemplateResponse("agenda/index.html", {
        "request": request,
        "atendente": atendente,
        "mediuns": [dict(m) for m in mediuns],
        "medium_id": medium_id,
        "data_de": data_de,
        "data_ate": data_ate,
        "por_data": por_data,
        "hoje": hoje.isoformat(),
        "total_sessoes": total_sessoes,
        "erro_externo": erro,
    })


@router.post("/novo")
async def novo_agendamento(
    request: Request,
    pessoa_id: int = Form(...),
    data: str = Form(...),
    requer_passe: int = Form(0),
    encaixe: int = Form(0),
    medium_id: int = Form(...),
    data_de: str = Form(""),
    data_ate: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    # Parse data (pode vir DD/MM/AAAA ou YYYY-MM-DD)
    if len(data) == 10 and "-" not in data:
        data_parts = data.split("/")
        if len(data_parts) == 3:
            data = f"{data_parts[2]}-{data_parts[1]}-{data_parts[0]}"

    with conectar() as conn:
        # Verificar se a pessoa já tem agendamento "agendado" no mesmo dia
        ja_tem = conn.execute(
            """SELECT 1 FROM agendamentos a
               JOIN planos_tratamento pt ON pt.id = a.plano_id
               JOIN plano_pessoas pp ON pp.plano_id = pt.id
               WHERE pp.pessoa_id = %s AND a.data = %s AND a.status = 'agendado'
               LIMIT 1""",
            (pessoa_id, data)
        ).fetchone()
        if ja_tem:
            msg = urllib.parse.quote("Pessoa já possui agendamento agendado nesta data.")
            return RedirectResponse(
                url=f"/agenda?medium_id={medium_id}&de={data_de}&ate={data_ate}&erro={msg}",
                status_code=303,
            )

        # Cria plano avulso para a pessoa + médium
        cur = conn.execute(
            """INSERT INTO planos_tratamento
               (medium_id, sessoes_total, sessoes_realizadas, data_inicio,
                frequencia, status, sessoes_com_passe)
               VALUES (%s, 1, 0, %s, 'avulso', 'ativo', 0)
               RETURNING id""",
            (medium_id, data)
        )
        plano_id = cur.fetchone()["id"]
        conn.execute(
            "INSERT INTO plano_pessoas (plano_id, pessoa_id) VALUES (%s, %s)",
            (plano_id, pessoa_id)
        )
        conn.execute(
            "INSERT INTO agendamentos (plano_id, data, status, requer_passe, encaixe) VALUES (%s,%s,%s,%s,%s)",
            (plano_id, data, "agendado", requer_passe, encaixe)
        )
    params = f"medium_id={medium_id}&de={data_de}&ate={data_ate}"
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

        # Buscar todas as sessões daquele médium naquela data
        sessoes = conn.execute(
            """
            SELECT a.id as agenda_id, a.data, a.status,
                   pt.id as plano_id
            FROM agendamentos a
            JOIN planos_tratamento pt ON pt.id = a.plano_id
            WHERE pt.medium_id = %s AND a.data = %s
            ORDER BY a.data""",
            (medium_id, dia)
        ).fetchall()

        resultado = []
        for s in sessoes:
            pessoas = conn.execute(
                """SELECT pe.nome_completo, pe.telefone
                   FROM plano_pessoas pp
                   JOIN pessoas pe ON pe.id = pp.pessoa_id
                   WHERE pp.plano_id = %s""",
                (s["plano_id"],)
            ).fetchall()
            resultado.append((dict(s), [dict(p) for p in pessoas]))

    return templates.TemplateResponse("imprimir_agenda.html", {
        "request": request,
        "atendente": atendente,
        "medium": dict(medium),
        "dia": dia,
        "agendamentos": resultado,
    })
