from datetime import date
from fastapi import APIRouter, Request, Form, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import os
import uuid
import urllib.parse
from banco import conectar, gerar_agendamentos_plano
from rotas.auth import obter_atendente_logado

from templates_config import templates
router = APIRouter(prefix="/cadastros/mediuns")

# Configuração de upload de fotos de médiuns
FOTOS_MEDIUM_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fotos")
os.makedirs(FOTOS_MEDIUM_DIR, exist_ok=True)
EXTENSOES_MEDIUM = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TAMANHO_MAXIMO_MEDIUM = 5 * 1024 * 1024  # 5MB


def _salvar_foto_medium(file: UploadFile, medium_id: int, foto_existente: str = None) -> str:
    """Salva foto do médium e retorna o caminho relativo."""
    if foto_existente and os.path.exists(os.path.join(FOTOS_MEDIUM_DIR, foto_existente)):
        try:
            os.remove(os.path.join(FOTOS_MEDIUM_DIR, foto_existente))
        except OSError:
            pass
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in EXTENSOES_MEDIUM:
        ext = ".jpg"
    nome_arquivo = f"medium_{medium_id}_{uuid.uuid4().hex[:8]}{ext}"
    caminho = os.path.join(FOTOS_MEDIUM_DIR, nome_arquivo)
    with open(caminho, "wb") as f:
        conteudo = file.file.read()
        if len(conteudo) > TAMANHO_MAXIMO_MEDIUM:
            raise ValueError("Arquivo muito grande (máx. 5MB)")
        f.write(conteudo)
    return nome_arquivo


# Placeholder SVG para médiuns
@router.get("/foto-placeholder/{inicial}")
async def foto_placeholder_medium(inicial: str):
    """Gera placeholder SVG com a inicial do médium (roxo)."""
    import html as _html
    char = _html.escape(inicial[0].upper()) if inicial else "?"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
        <defs>
            <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" style="stop-color:#8b5cf6"/>
                <stop offset="100%" style="stop-color:#a78bfa"/>
            </linearGradient>
        </defs>
        <circle cx="100" cy="100" r="100" fill="url(#g)"/>
        <text x="100" y="115" text-anchor="middle" fill="white"
              font-family="system-ui,sans-serif" font-size="90"
              font-weight="bold">{char}</text>
    </svg>'''
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


def _guard(request: Request):
    from rotas.permissoes import guarda_modulo
    return guarda_modulo(request, "cadastros.mediuns")


def _guard_planos(request: Request):
    """Planos de atendimento e agenda: administração de médiuns OU o módulo
    próprio da Recepção (cadastros.mediuns.planos). Leitura exige 'ler',
    gravação exige 'escrever' em um dos dois."""
    from rotas.permissoes import guarda_modulos
    return guarda_modulos(request, ["cadastros.mediuns", "cadastros.mediuns.planos"])


def _pode_gerir_mediuns(atendente) -> bool:
    """Se pode mostrar ações de administração (formulário, alta/cancelamento)."""
    from rotas.permissoes import usuario_pode
    return usuario_pode(atendente["id"], "cadastros.mediuns", "ler")


def _plano_do_medium(conn, plano_id: int, medium_id: int):
    """O plano pertence ao médium da URL? (URL trocada não atravessa a tela)."""
    return conn.execute(
        "SELECT 1 FROM planos_tratamento WHERE id = %s AND medium_id = %s",
        (plano_id, medium_id)
    ).fetchone() is not None


# ── Regra do passe nas sessões do plano ───────────────────────────────────────

def _agendamentos_do_passe(conn, plano_id: int) -> list:
    """Sessões que participam da regra do passe: realizadas/faltas primeiro
    (a ordem cronológica do tratamento), depois as futuras ainda agendadas."""
    return conn.execute(
        """SELECT id, status, requer_passe FROM agendamentos
           WHERE plano_id = %s AND status IN ('realizado','faltou','agendado')
           ORDER BY CASE WHEN status = 'agendado' THEN 1 ELSE 0 END, data, id""",
        (plano_id,)
    ).fetchall()


def _reaplicar_piso_passe(conn, plano_id: int) -> None:
    """Garante o piso de 3 passes nas 3 primeiras sessões do plano.

    Só ACRESCENTA obrigatoriedade — nunca remove passe de uma sessão. Assim,
    acrescentar ou reagendar uma sessão não pode apagar a regra, e nenhuma
    sessão já marcada com passe perde a marcação.
    """
    sessoes = _agendamentos_do_passe(conn, plano_id)
    alvo = min(3, len(sessoes))
    for i in range(alvo):
        if not sessoes[i]["requer_passe"]:
            conn.execute(
                "UPDATE agendamentos SET requer_passe = 1 WHERE id = %s",
                (sessoes[i]["id"],)
            )


def _reaplicar_requer_passe(conn, plano_id: int, requeridos: list) -> None:
    """Redistribui a obrigatoriedade de passe após o reagendamento.

    Acompanha a ORDEM das sessões (não a data), garantindo que as sessões que
    já exigiam passe continuem exigindo — sem inventar nem perder obrigações.
    """
    sessoes = _agendamentos_do_passe(conn, plano_id)
    for i, sessao in enumerate(sessoes):
        desejado = 1 if (i < len(requeridos) and requeridos[i]) else 0
        if bool(sessao["requer_passe"]) != bool(desejado):
            conn.execute(
                "UPDATE agendamentos SET requer_passe = %s WHERE id = %s",
                (desejado, sessao["id"])
            )


# ── Lista ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def listar(request: Request):
    atendente, redir = _guard_planos(request)
    if redir:
        return redir
    with conectar() as conn:
        rows = conn.execute(
            "SELECT id, nome_completo, telefone, email, ativo, vagas_dia FROM mediuns ORDER BY nome_completo"
        ).fetchall()
    return templates.TemplateResponse("mediuns/lista.html", {
        "request": request,
        "atendente": atendente,
        "mediuns": [dict(r) for r in rows],
        "pode_gerir": _pode_gerir_mediuns(atendente),
    })


# ── Novo ──────────────────────────────────────────────────────────────────────

@router.get("/novo", response_class=HTMLResponse)
async def form_novo(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("mediuns/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": None,
        "erro": None,
    })


@router.post("/novo", response_class=HTMLResponse)
async def salvar_novo(
    request: Request,
    nome_completo: str = Form(...),
    vagas_dia: int = Form(10),
    telefone: str = Form(""),
    email: str = Form(""),
    cep: str = Form(""),
    logradouro: str = Form(""),
    numero: str = Form(""),
    complemento: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    uf: str = Form(""),
    foto: UploadFile = None,
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO mediuns
               (nome_completo, vagas_dia, telefone, email, cep, logradouro, numero, complemento, bairro, cidade, uf)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (nome_completo.strip(), vagas_dia,
             telefone.strip(), email.strip().lower(),
             cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
             bairro.strip(), cidade.strip(), uf.strip()),
        )
        medium_id = cur.fetchone()["id"]

        # Salvar foto se enviada
        if foto and getattr(foto, "filename", None):
            try:
                foto_medium = _salvar_foto_medium(foto, medium_id)
                conn.execute(
                    "UPDATE mediuns SET foto_medium = %s WHERE id = %s",
                    (foto_medium, medium_id)
                )
            except ValueError:
                pass
    return RedirectResponse(url="/cadastros/mediuns", status_code=303)


# ── Editar ────────────────────────────────────────────────────────────────────

@router.get("/{id}/editar", response_class=HTMLResponse)
async def form_editar(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute("SELECT * FROM mediuns WHERE id = %s", (id,)).fetchone()
    if not row:
        return RedirectResponse(url="/cadastros/mediuns", status_code=303)
    return templates.TemplateResponse("mediuns/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": dict(row),
        "erro": None,
    })


@router.post("/{id}/editar", response_class=HTMLResponse)
async def salvar_editar(
    request: Request,
    id: int,
    nome_completo: str = Form(...),
    vagas_dia: int = Form(10),
    telefone: str = Form(""),
    email: str = Form(""),
    cep: str = Form(""),
    logradouro: str = Form(""),
    numero: str = Form(""),
    complemento: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    uf: str = Form(""),
    remover_foto: str = Form(""),
    foto: UploadFile = None,
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        # Buscar foto existente
        existente = conn.execute(
            "SELECT foto_medium FROM mediuns WHERE id = %s", (id,)
        ).fetchone()
        foto_existente = existente["foto_medium"] if existente else None

        # Remover foto se solicitado
        if remover_foto == "1" and foto_existente:
            if os.path.exists(os.path.join(FOTOS_MEDIUM_DIR, foto_existente)):
                try:
                    os.remove(os.path.join(FOTOS_MEDIUM_DIR, foto_existente))
                except OSError:
                    pass
            foto_existente = None

        # Salvar nova foto se enviada
        if foto and getattr(foto, "filename", None):
            try:
                foto_existente = _salvar_foto_medium(foto, id, foto_existente)
            except ValueError:
                pass

        conn.execute(
            """UPDATE mediuns SET nome_completo=%s, vagas_dia=%s, telefone=%s, email=%s,
               cep=%s, logradouro=%s, numero=%s, complemento=%s, bairro=%s, cidade=%s, uf=%s,
               foto_medium=%s
               WHERE id=%s""",
            (nome_completo.strip(), vagas_dia,
             telefone.strip(), email.strip().lower(),
             cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
             bairro.strip(), cidade.strip(), uf.strip(),
             foto_existente, id),
        )
    return RedirectResponse(url="/cadastros/mediuns", status_code=303)


# ── Planos de tratamento ──────────────────────────────────────────────────────

@router.get("/{id}/planos", response_class=HTMLResponse)
async def listar_planos(request: Request, id: int, erro: str = Query("")):
    atendente, redir = _guard_planos(request)
    if redir:
        return redir
    with conectar() as conn:
        medium = conn.execute("SELECT * FROM mediuns WHERE id = %s", (id,)).fetchone()
        if not medium:
            return RedirectResponse(url="/cadastros/mediuns", status_code=303)

        planos = conn.execute(
            """SELECT pt.id, pt.sessoes_total, pt.sessoes_realizadas,
                      pt.data_inicio, pt.status, pt.frequencia, pt.sessoes_com_passe
               FROM planos_tratamento pt
               WHERE pt.medium_id = %s
               ORDER BY pt.status, pt.data_inicio DESC""",
            (id,)
        ).fetchall()

        planos_com_pessoas = []
        for p in planos:
            pessoas = conn.execute(
                """SELECT pe.id, pe.nome_completo
                   FROM plano_pessoas pp JOIN pessoas pe ON pe.id = pp.pessoa_id
                   WHERE pp.plano_id = %s""",
                (p["id"],)
            ).fetchall()
            # Próximo agendamento
            prox = conn.execute(
                "SELECT data FROM agendamentos WHERE plano_id = %s AND status='agendado' ORDER BY data LIMIT 1",
                (p["id"],)
            ).fetchone()
            planos_com_pessoas.append({
                **dict(p),
                "pessoas": [dict(x) for x in pessoas],
                "proxima_sessao": prox["data"] if prox else None,
            })

        todas_pessoas = conn.execute(
            "SELECT id, nome_completo FROM pessoas ORDER BY nome_completo"
        ).fetchall()

    return templates.TemplateResponse("mediuns/planos.html", {
        "request": request,
        "atendente": atendente,
        "medium": dict(medium),
        "planos": planos_com_pessoas,
        "todas_pessoas": [dict(p) for p in todas_pessoas],
        "hoje": date.today().isoformat(),
        "pode_gerir": _pode_gerir_mediuns(atendente),
        "erro": urllib.parse.unquote(erro) if erro else None,
    })


@router.post("/{id}/planos/novo")
async def novo_plano(
    request: Request,
    id: int,
    sessoes_total: int = Form(...),
    frequencia: str = Form("semanal"),
    sessoes_com_passe: int = Form(3),
    data_inicio: str = Form(""),
    pessoas_ids: list[int] = Form(...),
    next: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    from datetime import date as _date
    import banco
    inicio = _date.fromisoformat(data_inicio) if data_inicio else _date.today()
    destino = next.strip() or f"/cadastros/mediuns/{id}/planos"
    with conectar() as conn:
        # Regra do plano único: quem já tem plano ativo só pode receber
        # sessões por aquele plano (editar/reagendar/encaixe) — um plano novo
        # exige encerrar o anterior (alta/cancelar) ou novo acolhimento.
        com_plano = []
        for pid in pessoas_ids:
            if banco.planos_ativos_da_pessoa(conn, pid):
                p = conn.execute(
                    "SELECT nome_completo FROM pessoas WHERE id = %s", (pid,)
                ).fetchone()
                com_plano.append(p["nome_completo"] if p else str(pid))
        if com_plano:
            return RedirectResponse(
                url=destino + "?erro=" + urllib.parse.quote(
                    "Já tem plano ativo: " + ", ".join(com_plano)
                    + ". Encerre o plano atual (alta/cancelar) ou faça novo acolhimento fraterno."
                ),
                status_code=303,
            )
        cur = conn.execute(
            """INSERT INTO planos_tratamento
               (medium_id, sessoes_total, sessoes_previstas, data_inicio,
                frequencia, sessoes_com_passe, status, regra_conclusao)
               VALUES (%s, %s, %s, %s, %s, %s, 'ativo', 1)
               RETURNING id""",
            (id, sessoes_total, sessoes_total, inicio.isoformat(), frequencia, sessoes_com_passe)
        )
        plano_id = cur.fetchone()["id"]
        for pessoa_id in pessoas_ids:
            conn.execute(
                """INSERT INTO plano_pessoas (plano_id, pessoa_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (plano_id, pessoa_id)
            )
        gerar_agendamentos_plano(conn, plano_id, inicio, frequencia, sessoes_total, sessoes_com_passe)
    return RedirectResponse(url=destino, status_code=303)


@router.post("/{id}/planos/{plano_id}/alta")
async def dar_alta(request: Request, id: int, plano_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "UPDATE planos_tratamento SET status='alta', concluido=1 WHERE id=%s AND medium_id=%s",
            (plano_id, id)
        )
        conn.execute(
            "UPDATE agendamentos SET status='cancelado' WHERE plano_id=%s AND status='agendado'",
            (plano_id,)
        )
    return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos", status_code=303)


@router.post("/{id}/planos/{plano_id}/cancelar")
async def cancelar_plano(request: Request, id: int, plano_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "UPDATE planos_tratamento SET status='cancelado' WHERE id=%s AND medium_id=%s",
            (plano_id, id)
        )
        conn.execute(
            "UPDATE agendamentos SET status='cancelado' WHERE plano_id=%s AND status='agendado'",
            (plano_id,)
        )
    return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos", status_code=303)


# ── Agendamentos do plano ─────────────────────────────────────────────────────

_MESES_PT = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
             "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]

@router.get("/{id}/planos/{plano_id}/agenda", response_class=HTMLResponse)
async def ver_agenda(request: Request, id: int, plano_id: int,
                     mes: str = Query(None)):
    atendente, redir = _guard_planos(request)
    if redir:
        return redir

    hoje = date.today()
    if not mes:
        mes = hoje.strftime("%Y-%m")
    try:
        ano_m, num_m = int(mes[:4]), int(mes[5:7])
    except (ValueError, IndexError):
        ano_m, num_m = hoje.year, hoje.month
        mes = hoje.strftime("%Y-%m")

    mes_inicio = f"{ano_m}-{num_m:02d}-01"
    if num_m == 12:
        mes_fim = f"{ano_m + 1}-01-01"
    else:
        mes_fim = f"{ano_m}-{num_m + 1:02d}-01"

    if num_m == 1:
        mes_ant = f"{ano_m - 1}-12"
    else:
        mes_ant = f"{ano_m}-{num_m - 1:02d}"
    if num_m == 12:
        mes_prox = f"{ano_m + 1}-01"
    else:
        mes_prox = f"{ano_m}-{num_m + 1:02d}"

    mes_label = f"{_MESES_PT[num_m - 1]} {ano_m}"

    with conectar() as conn:
        medium = conn.execute("SELECT id, nome_completo FROM mediuns WHERE id=%s", (id,)).fetchone()
        plano = conn.execute(
            """SELECT pt.*, STRING_AGG(pe.nome_completo, ', ') as nomes_pessoas
               FROM planos_tratamento pt
               LEFT JOIN plano_pessoas pp ON pp.plano_id = pt.id
               LEFT JOIN pessoas pe ON pe.id = pp.pessoa_id
               WHERE pt.id=%s AND pt.medium_id=%s
               GROUP BY pt.id""",
            (plano_id, id)
        ).fetchone()
        if not plano or not medium:
            return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos", status_code=303)
        futuros = conn.execute(
            """SELECT * FROM agendamentos
               WHERE plano_id=%s AND status='agendado' AND data >= %s AND data < %s
               ORDER BY data""",
            (plano_id, mes_inicio, mes_fim)
        ).fetchall()
        passados = conn.execute(
            """SELECT * FROM agendamentos
               WHERE plano_id=%s AND status != 'agendado'
               ORDER BY data DESC""",
            (plano_id,)
        ).fetchall()
        tem_ant = conn.execute(
            "SELECT 1 FROM agendamentos WHERE plano_id=%s AND status='agendado' AND data < %s LIMIT 1",
            (plano_id, mes_inicio)
        ).fetchone() is not None
        tem_prox = conn.execute(
            "SELECT 1 FROM agendamentos WHERE plano_id=%s AND status='agendado' AND data >= %s LIMIT 1",
            (plano_id, mes_fim)
        ).fetchone() is not None

    return templates.TemplateResponse("mediuns/agenda.html", {
        "request": request,
        "atendente": atendente,
        "medium": dict(medium),
        "plano": dict(plano),
        "futuros": [dict(a) for a in futuros],
        "passados": [dict(a) for a in passados],
        "hoje": hoje.isoformat(),
        "mes": mes,
        "mes_label": mes_label,
        "mes_ant": mes_ant,
        "mes_prox": mes_prox,
        "tem_ant": tem_ant,
        "tem_prox": tem_prox,
    })


@router.post("/{id}/planos/{plano_id}/agenda/novo")
async def novo_agendamento(
    request: Request,
    id: int,
    plano_id: int,
    data: str = Form(...),
    requer_passe: int = Form(0),
    encaixe: int = Form(0),
):
    atendente, redir = _guard_planos(request)
    if redir:
        return redir
    from banco import data_atendimento_valida
    import servico_conclusao_planos
    destino = f"/cadastros/mediuns/{id}/planos/{plano_id}/agenda"
    with conectar() as conn:
        # O plano precisa pertencer ao médium da URL — troca indevida nega.
        if not _plano_do_medium(conn, plano_id, id):
            return HTMLResponse(status_code=403, content="Acesso negado.")
        # Sessão só cai em dia de atendimento cadastrado (R4). O
        # parâmetro `encaixe` não libera a restrição: qualquer exceção
        # precisaria de decisão expressa do proprietário.
        if not data_atendimento_valida(conn, data):
            return RedirectResponse(
                url=f"{destino}?erro=" + urllib.parse.quote(
                    "Data não é dia de atendimento. Escolha um dia cadastrado em Configurações."
                ),
                status_code=303,
            )
        # TASK_018: a sessão extra entra pela transição única do plano (trava
        # inclusa). Plano concluído/alto/cancelado não se reabre por aqui.
        plano = conn.execute("SELECT status FROM planos_tratamento WHERE id = %s",
                             (plano_id,)).fetchone()
        if not plano or plano["status"] != "ativo":
            return RedirectResponse(
                url=f"{destino}?erro=" + urllib.parse.quote(
                    "Este plano não está ativo — sessões não podem ser "
                    "acrescentadas. Se for caso, faça um novo atendimento."
                ),
                status_code=303,
            )
        res = servico_conclusao_planos.acrescentar_sessao(
            conn, plano_id, data, requer_passe=int(requer_passe),
            encaixe=int(encaixe), tipo="adicao", autor_id=atendente["id"],
        )
        if res == "data_ja_agendada":
            return RedirectResponse(
                url=f"{destino}?erro=" + urllib.parse.quote(
                    "Este plano já tem sessão agendada nesta data."
                ),
                status_code=303,
            )
        if res == "plano_nao_ativo":
            # concluído/alto/cancelado entre a tela e a escrita (ex.: a última
            # realização fechou o plano concorrente) — não reabre, não duplica.
            return RedirectResponse(
                url=f"{destino}?erro=" + urllib.parse.quote(
                    "Este plano acabou de sair da situação ativa. Recarregue a página."
                ),
                status_code=303,
            )
        # A obrigatoriedade das 3 primeiras sessões não pode ser removida por
        # este formulário: reaplica o piso ao conjunto do plano.
        _reaplicar_piso_passe(conn, plano_id)
    return RedirectResponse(url=destino, status_code=303)


@router.post("/{id}/planos/{plano_id}/agenda/{ag_id}/cancelar")
async def cancelar_agendamento(request: Request, id: int, plano_id: int, ag_id: int):
    atendente, redir = _guard_planos(request)
    if redir:
        return redir
    import servico_conclusao_planos
    with conectar() as conn:
        if not _plano_do_medium(conn, plano_id, id):
            return HTMLResponse(status_code=403, content="Acesso negado.")
        # TASK_018: cancelar extensão ainda pendente revoga a extensão
        # (+1−1, registrado); cancelar sessão prevista NÃO vira cumprimento
        # — a obrigação permanece e o plano não conclui por cancelamento.
        servico_conclusao_planos.cancelar_sessao(
            conn, plano_id, ag_id, autor_id=atendente["id"]
        )
    return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos/{plano_id}/agenda", status_code=303)


@router.post("/{id}/planos/{plano_id}/agenda/{ag_id}/falta")
async def registrar_falta(request: Request, id: int, plano_id: int, ag_id: int):
    atendente, redir = _guard_planos(request)
    if redir:
        return redir
    from datetime import date as _date
    import banco
    hoje = _date.today().isoformat()
    with conectar() as conn:
        if not _plano_do_medium(conn, plano_id, id):
            return HTMLResponse(status_code=403, content="Acesso negado.")
        conn.execute(
            "UPDATE agendamentos SET status='faltou' WHERE id=%s AND plano_id=%s",
            (ag_id, plano_id)
        )
        # 2 faltas consecutivas encerram o plano automaticamente
        banco.faltas_consecutivas_encerram(conn, plano_id, hoje)
    return RedirectResponse(url=f"/cadastros/mediuns/{id}/planos/{plano_id}/agenda", status_code=303)


@router.post("/{id}/planos/{plano_id}/agenda/{ag_id}/reagendar")
async def reagendar(
    request: Request,
    id: int,
    plano_id: int,
    ag_id: int,
    nova_data: str = Form(...),
):
    atendente, redir = _guard_planos(request)
    if redir:
        return redir
    from datetime import date as _date, timedelta as _td
    from banco import data_atendimento_valida, datas_agendamento, _dias_atendimento
    destino = f"/cadastros/mediuns/{id}/planos/{plano_id}/agenda"
    with conectar() as conn:
        # O plano precisa pertencer ao médium da URL — troca indevida nega.
        if not _plano_do_medium(conn, plano_id, id):
            return HTMLResponse(status_code=403, content="Acesso negado.")
        ag = conn.execute(
            "SELECT data, requer_passe FROM agendamentos WHERE id=%s AND plano_id=%s",
            (ag_id, plano_id)
        ).fetchone()
        if not ag:
            return RedirectResponse(url=destino, status_code=303)

        # A nova data precisa ser dia de atendimento: reagendar não pode jogar
        # a sessão para sábado/domingo (defeito S9 da investigação).
        if not data_atendimento_valida(conn, nova_data):
            return RedirectResponse(
                url=f"{destino}?erro=" + urllib.parse.quote(
                    "A nova data não é dia de atendimento. Escolha um dia cadastrado."
                ),
                status_code=303,
            )

        data_original = _date.fromisoformat(ag["data"])
        if nova_data == ag["data"]:
            return RedirectResponse(url=destino, status_code=303)

        # Recalcula TODA a agenda do plano a partir da nova data da sessão
        # escolhida, mantendo a quantidade de sessões e a frequência — assim
        # nenhuma sessão futura desliza para fora do calendário.
        plano = conn.execute(
            "SELECT frequencia, sessoes_total FROM planos_tratamento WHERE id=%s",
            (plano_id,)
        ).fetchone()
        if not plano:
            return RedirectResponse(url=destino, status_code=303)

        restantes = conn.execute(
            """SELECT id FROM agendamentos
               WHERE plano_id=%s AND status='agendado' AND data >= %s
               ORDER BY data""",
            (plano_id, ag["data"])
        ).fetchall()
        if not restantes:
            return RedirectResponse(url=destino, status_code=303)

        dias = _dias_atendimento(conn)
        novas = datas_agendamento(
            _date.fromisoformat(nova_data), len(restantes), plano["frequencia"], dias
        )
        # Fecha a agenda futura e regrava a partir da nova data, preservando o
        # requer_passe de cada sessão já marcada.
        requeridos = [r["requer_passe"] for r in conn.execute(
            """SELECT requer_passe FROM agendamentos
               WHERE plano_id=%s AND status='agendado' AND data >= %s ORDER BY data""",
            (plano_id, ag["data"])
        ).fetchall()]
        for idx, r in enumerate(restantes):
            conn.execute(
                "UPDATE agendamentos SET data=%s, status='agendado' WHERE id=%s",
                (novas[idx], r["id"])
            )
        _reaplicar_requer_passe(conn, plano_id, requeridos)
    return RedirectResponse(url=destino, status_code=303)


# ── Ativar / Desativar ────────────────────────────────────────────────────────

@router.post("/{id}/toggle-ativo")
async def toggle_ativo(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute("UPDATE mediuns SET ativo = 1 - ativo WHERE id = %s", (id,))
    return RedirectResponse(url="/cadastros/mediuns", status_code=303)
