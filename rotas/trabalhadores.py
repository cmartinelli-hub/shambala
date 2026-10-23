from datetime import date, datetime
from urllib.parse import urlencode
from fastapi import APIRouter, Request, Form, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import os
import uuid
from banco import conectar
from rotas.auth import obter_atendente_logado
from rotas.financeiro_mensalidades import sincronizar_mensalidades, atualizar_pendentes_futuras
from servico_presenca import registrar_chegada, registrar_ausencia_explicita, \
    TrabalhadorInexistente, TrabalhadorInativo
from servico_codigo_trabalhador import CodigosEsgotados, TrabalhadorSemCodigo, \
    garantir_codigo, trocar_codigo, preparar_codigos_pendentes, qr_data_uri

from templates_config import templates, centro_config
router = APIRouter(prefix="/cadastros/trabalhadores")

# Configuração de upload de fotos
FOTOS_TRAB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fotos")
os.makedirs(FOTOS_TRAB_DIR, exist_ok=True)
EXTensoes_TRAB = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TAMANHO_MAXIMO_TRAB = 5 * 1024 * 1024  # 5MB

# Carteirinha A4: 2 colunas × 5 linhas de 85,6 × 54 mm (10 mm de margem,
# 1 mm entre linhas, 8 mm entre colunas) — ver static/css/carteirinha.css.
_CARTOES_POR_FOLHA = 10


def _salvar_foto_trab(file: UploadFile, trab_id: int, foto_existente: str = None) -> str:
    """Salva foto do trabalhador e retorna o caminho relativo."""
    if foto_existente and os.path.exists(os.path.join(FOTOS_TRAB_DIR, foto_existente)):
        try:
            os.remove(os.path.join(FOTOS_TRAB_DIR, foto_existente))
        except OSError:
            pass
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in EXTensoes_TRAB:
        ext = ".jpg"
    nome_arquivo = f"trab_{trab_id}_{uuid.uuid4().hex[:8]}{ext}"
    caminho = os.path.join(FOTOS_TRAB_DIR, nome_arquivo)
    with open(caminho, "wb") as f:
        conteudo = file.file.read()
        if len(conteudo) > TAMANHO_MAXIMO_TRAB:
            raise ValueError("Arquivo muito grande (máx. 5MB)")
        f.write(conteudo)
    return nome_arquivo


# Placeholder SVG para trabalhadores
@router.get("/foto-placeholder/{inicial}")
async def foto_placeholder_trab(inicial: str):
    """Gera placeholder SVG com a inicial do trabalhador (verde-água)."""
    import html as _html
    char = _html.escape(inicial[0].upper()) if inicial else "?"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
        <defs>
            <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" style="stop-color:#0d9488"/>
                <stop offset="100%" style="stop-color:#14b8a6"/>
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
    return guarda_modulo(request, "cadastros.trabalhadores")


def _voltar_lista(erro: str = None, ok: str = None):
    """Volta à lista com mensagem (a tela trata por query string)."""
    params = {}
    if erro:
        params["erro"] = erro
    if ok:
        params["ok"] = ok
    url = "/cadastros/trabalhadores"
    if params:
        url += "?" + urlencode(params)
    return RedirectResponse(url=url, status_code=303)


_NOMES_DIA = {
    0: "Segunda-feira",
    1: "Terça-feira",
    2: "Quarta-feira",
    3: "Quinta-feira",
    4: "Sexta-feira",
    5: "Sábado",
    6: "Domingo",
}


def _parse_data(texto: str):
    """Converte DD/MM/AAAA → YYYY-MM-DD. Aceita também YYYY-MM-DD direto.
    Datas inválidas (ex.: 29/02/2001) retornam None — nunca grava data inexistente."""
    t = texto.strip()
    if not t:
        return None
    if "/" in t:
        partes = t.split("/")
        if len(partes) == 3:
            d, m, a = partes
            t = f"{a.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
    try:
        date.fromisoformat(t)
        return t
    except ValueError:
        return None


# ── Lista ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def listar(request: Request, erro: str = "", ok: str = ""):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        rows = conn.execute(
            """SELECT t.id, t.nome_completo, t.cpf, t.telefone, t.email, t.ativo,
                      c.codigo AS codigo
                 FROM trabalhadores t
                 LEFT JOIN trabalhador_codigo c
                        ON c.trabalhador_id = t.id AND c.estado = 'ativo'
                ORDER BY t.nome_completo"""
        ).fetchall()
        sem_codigo = conn.execute(
            """SELECT COUNT(*) AS n FROM trabalhadores t
                WHERE t.ativo = 1
                  AND NOT EXISTS (SELECT 1 FROM trabalhador_codigo c
                                   WHERE c.trabalhador_id = t.id AND c.estado = 'ativo')"""
        ).fetchone()["n"]
    return templates.TemplateResponse("trabalhadores/lista.html", {
        "request": request,
        "atendente": atendente,
        "trabalhadores": [dict(r) for r in rows],
        "sem_codigo": sem_codigo,
        "erro": erro or None,
        "ok": ok or None,
    })


# ── Códigos: preparação em massa ──────────────────────────────────────────────

@router.post("/preparar-codigos", response_class=HTMLResponse)
async def preparar_codigos(request: Request):
    """Ação explícita: emite os códigos que faltam nos trabalhadores ativos.

    Idempotente — uma segunda execução não gera nada e não altera os já
    emitidos. Escrita exige a permissão do cadastro (não da recepção).
    """
    atendente, redir = _guard(request)
    if redir:
        return redir
    try:
        with conectar() as conn:
            resumo = preparar_codigos_pendentes(conn, atendente_id=atendente["id"])
    except CodigosEsgotados:
        return _voltar_lista(erro="Não há códigos disponíveis (os 10.000 já foram "
                                  "emitidos). Nenhum código foi criado.")
    gerados = len(resumo["gerados"])
    if gerados:
        ok = (f"{gerados} código(s) gerados; {resumo['ja_existiam']} já tinham código "
              f"e foram preservados.")
    else:
        ok = f"Nenhum código precisou ser gerado — {resumo['ja_existiam']} ativos já tinham código."
    return _voltar_lista(ok=ok)


def _foto_url(trab: dict) -> str:
    if trab.get("foto_trabalhador"):
        return f"/static/fotos/{trab['foto_trabalhador']}"
    inicial = (trab["nome_completo"].strip()[:1] or "?")
    return f"/cadastros/trabalhadores/foto-placeholder/{inicial}"


def _montar_cartao(trab: dict) -> dict:
    """Cartão de carteirinha: dados mínimos + QR do código atual.

    O QR contém exatamente os quatro caracteres — nem nome, nem id, nem URL.
    """
    return {
        "id": trab["id"],
        "nome": trab["nome_completo"],
        "foto_url": _foto_url(trab),
        "codigo": trab["codigo"],
        "qr": qr_data_uri(trab["codigo"]),
    }


_SQL_CARTEIRINHA = """
    SELECT t.id, t.nome_completo, t.foto_trabalhador, t.ativo,
           c.codigo AS codigo
      FROM trabalhadores t
      LEFT JOIN trabalhador_codigo c
             ON c.trabalhador_id = t.id AND c.estado = 'ativo'
"""


@router.get("/carteirinhas", response_class=HTMLResponse)
async def carteirinhas_lote(
    request: Request,
    ids: list[int] = Query(None),
    todos: str = Query(""),
):
    """Impressão em lote (A4). Somente leitura: não gera nem altera código."""
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        if todos == "ativos":
            rows = conn.execute(_SQL_CARTEIRINHA + " WHERE t.ativo = 1 ORDER BY t.nome_completo"
                                ).fetchall()
        elif ids:
            rows = conn.execute(_SQL_CARTEIRINHA + " WHERE t.id = ANY(%s) ORDER BY t.nome_completo",
                                (ids,)).fetchall()
        else:
            return _voltar_lista(erro="Selecione ao menos um trabalhador para imprimir.")

    cartoes, inativos, sem_codigo = [], [], []
    for r in rows:
        trab = dict(r)
        if not trab["ativo"]:
            inativos.append(trab["nome_completo"])
        elif not trab["codigo"]:
            sem_codigo.append(trab["nome_completo"])
        else:
            cartoes.append(_montar_cartao(trab))

    if not cartoes:
        partes = []
        if inativos:
            partes.append(f"{len(inativos)} inativo(s) — a impressão de inativos está "
                          f"bloqueada nesta versão")
        if sem_codigo:
            partes.append(f"{len(sem_codigo)} sem código atual — gere o código antes")
        return _voltar_lista(erro="Nada para imprimir: " + "; ".join(partes) + ".")

    return templates.TemplateResponse("trabalhadores/carteirinhas_lote.html", {
        "request": request,
        "atendente": atendente,
        "cartoes": cartoes,
        "folhas": [cartoes[i:i + _CARTOES_POR_FOLHA]
                   for i in range(0, len(cartoes), _CARTOES_POR_FOLHA)],
        "inativos": inativos,
        "sem_codigo": sem_codigo,
        "centro": centro_config(),
    })


# ── Novo ──────────────────────────────────────────────────────────────────────

@router.get("/novo", response_class=HTMLResponse)
async def form_novo(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("trabalhadores/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": None,
        "erro": None,
    })


@router.post("/novo", response_class=HTMLResponse)
async def salvar_novo(
    request: Request,
    nome_completo: str = Form(...),
    cpf: str = Form(""),
    rg: str = Form(""),
    data_nascimento: str = Form(""),
    telefone: str = Form(""),
    email: str = Form(""),
    cep: str = Form(""),
    logradouro: str = Form(""),
    numero: str = Form(""),
    complemento: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    uf: str = Form(""),
    valor_mensalidade: str = Form("0"),
    dia_vencimento: str = Form("10"),
    foto: UploadFile = None,
    fiado_limite: str = Form("0"),
    fiado_data_encerramento: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    dn = _parse_data(data_nascimento)
    try:
        with conectar() as conn:
            cur = conn.execute(
                """INSERT INTO trabalhadores
                   (nome_completo, cpf, rg, data_nascimento, telefone, email,
                    cep, logradouro, numero, complemento, bairro, cidade, uf,
                    valor_mensalidade, dia_vencimento,
                    fiado_limite, fiado_data_encerramento)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (nome_completo.strip(), cpf.strip() or None, rg.strip() or None, dn,
                 telefone.strip(), email.strip().lower(),
                 cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
                 bairro.strip(), cidade.strip(), uf.strip(),
                 float(valor_mensalidade or 0), int(dia_vencimento or 10),
                 float(fiado_limite or 0), fiado_data_encerramento.strip() or None),
            )
            trab_id = cur.fetchone()["id"]

            # Código de identificação (TASK_002) — na MESMA transação do
            # cadastro: se não houver código disponível, o cadastro não fica
            # registrado pela metade.
            garantir_codigo(conn, trab_id, atendente_id=atendente["id"])

            # Salvar foto se enviada
            if foto and getattr(foto, "filename", None):
                try:
                    foto_trab = _salvar_foto_trab(foto, trab_id)
                    conn.execute(
                        "UPDATE trabalhadores SET foto_trabalhador = %s WHERE id = %s",
                        (foto_trab, trab_id)
                    )
                except ValueError:
                    pass

            # Gera mensalidades futuras (conta a receber) se houver valor mensal
            if float(valor_mensalidade or 0) > 0:
                sincronizar_mensalidades(conn, {
                    "id": trab_id,
                    "nome_completo": nome_completo.strip(),
                    "valor_mensalidade": float(valor_mensalidade or 0),
                    "dia_vencimento": int(dia_vencimento or 10),
                    "ativo": 1,
                })
    except CodigosEsgotados:
        return templates.TemplateResponse("trabalhadores/form.html", {
            "request": request,
            "atendente": atendente,
            "registro": None,
            "erro": "Não há códigos disponíveis (os 10.000 já foram emitidos). "
                    "O cadastro não foi gravado.",
        })
    except Exception:
        return templates.TemplateResponse("trabalhadores/form.html", {
            "request": request,
            "atendente": atendente,
            "registro": None,
            "erro": f"CPF '{cpf}' já cadastrado.",
        })
    return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)


# ── Editar ────────────────────────────────────────────────────────────────────

@router.get("/{id}/editar", response_class=HTMLResponse)
async def form_editar(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(
            """SELECT t.*, c.codigo AS codigo_atual
                 FROM trabalhadores t
                 LEFT JOIN trabalhador_codigo c
                        ON c.trabalhador_id = t.id AND c.estado = 'ativo'
                WHERE t.id = %s""",
            (id,),
        ).fetchone()
    if not row:
        return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)
    return templates.TemplateResponse("trabalhadores/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": dict(row),
        "erro": None,
        "aviso": None,
    })


@router.post("/{id}/editar", response_class=HTMLResponse)
async def salvar_editar(
    request: Request,
    id: int,
    nome_completo: str = Form(...),
    cpf: str = Form(""),
    rg: str = Form(""),
    data_nascimento: str = Form(""),
    telefone: str = Form(""),
    email: str = Form(""),
    cep: str = Form(""),
    logradouro: str = Form(""),
    numero: str = Form(""),
    complemento: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    uf: str = Form(""),
    valor_mensalidade: str = Form("0"),
    dia_vencimento: str = Form("10"),
    remover_foto: str = Form(""),
    foto: UploadFile = None,
    fiado_limite: str = Form("0"),
    fiado_data_encerramento: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    dn = _parse_data(data_nascimento)

    with conectar() as conn:
        # Buscar foto existente
        existente = conn.execute(
            "SELECT foto_trabalhador FROM trabalhadores WHERE id = %s", (id,)
        ).fetchone()
        foto_existente = existente["foto_trabalhador"] if existente else None

        # Valores anteriores (para decidir se atualiza as pendentes futuras)
        anterior = conn.execute(
            "SELECT valor_mensalidade, dia_vencimento, ativo FROM trabalhadores WHERE id = %s",
            (id,),
        ).fetchone()

        # Remover foto se solicitado
        if remover_foto == "1" and foto_existente:
            if os.path.exists(os.path.join(FOTOS_TRAB_DIR, foto_existente)):
                try:
                    os.remove(os.path.join(FOTOS_TRAB_DIR, foto_existente))
                except OSError:
                    pass
            foto_existente = None

        # Salvar nova foto se enviada
        if foto and getattr(foto, "filename", None):
            try:
                foto_existente = _salvar_foto_trab(foto, id, foto_existente)
            except ValueError:
                pass

        try:
            conn.execute(
                """UPDATE trabalhadores SET
                   nome_completo=%s, cpf=%s, rg=%s, data_nascimento=%s,
                   telefone=%s, email=%s,
                   cep=%s, logradouro=%s, numero=%s, complemento=%s,
                   bairro=%s, cidade=%s, uf=%s,
                   valor_mensalidade=%s, dia_vencimento=%s,
                   foto_trabalhador=%s,
                   fiado_limite=%s, fiado_data_encerramento=%s
                   WHERE id=%s""",
                (nome_completo.strip(), cpf.strip() or None, rg.strip() or None, dn,
                 telefone.strip(), email.strip().lower(),
                 cep.strip(), logradouro.strip(), numero.strip(), complemento.strip(),
                 bairro.strip(), cidade.strip(), uf.strip(),
                 float(valor_mensalidade or 0), int(dia_vencimento or 10),
                 foto_existente,
                 float(fiado_limite or 0), fiado_data_encerramento.strip() or None,
                 id),
            )
        except Exception:
            with conectar() as conn2:
                row = conn2.execute(
                    """SELECT t.*, c.codigo AS codigo_atual
                         FROM trabalhadores t
                         LEFT JOIN trabalhador_codigo c
                                ON c.trabalhador_id = t.id AND c.estado = 'ativo'
                        WHERE t.id = %s""",
                    (id,),
                ).fetchone()
            return templates.TemplateResponse("trabalhadores/form.html", {
                "request": request,
                "atendente": atendente,
                "registro": dict(row) if row else None,
                "erro": f"CPF '{cpf}' já cadastrado.",
                "aviso": None,
            })

        # Mensalidades a receber: se valor/dia mudaram, atualiza as pendentes
        # do mês corrente em diante; garante os 13 meses (corrente + 12 futuros)
        novo_valor = float(valor_mensalidade or 0)
        novo_dia = int(dia_vencimento or 10)
        if anterior and anterior["ativo"] and novo_valor > 0:
            trab = {
                "id": id,
                "nome_completo": nome_completo.strip(),
                "valor_mensalidade": novo_valor,
                "dia_vencimento": novo_dia,
                "ativo": 1,
            }
            mudou = (
                float(anterior["valor_mensalidade"] or 0) != novo_valor
                or int(anterior["dia_vencimento"] or 10) != novo_dia
            )
            if mudou:
                atualizar_pendentes_futuras(conn, trab)
            else:
                sincronizar_mensalidades(conn, trab)

    return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)


# ── Ativar / Desativar ────────────────────────────────────────────────────────

@router.post("/{id}/toggle-ativo")
async def toggle_ativo(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        antes = conn.execute("SELECT ativo FROM trabalhadores WHERE id = %s", (id,)).fetchone()
        conn.execute("UPDATE trabalhadores SET ativo = 1 - ativo WHERE id = %s", (id,))
        # Reativou e não tem código atual? Emite agora (inativação nunca mexe
        # no código — ele fica reservado com a pessoa).
        if antes and not antes["ativo"]:
            garantir_codigo(conn, id, atendente_id=atendente["id"])
    return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)


# ── Código de identificação (TASK_002) ────────────────────────────────────────

@router.post("/{id}/codigo/gerar", response_class=HTMLResponse)
async def codigo_gerar(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    try:
        with conectar() as conn:
            r = garantir_codigo(conn, id, atendente_id=atendente["id"])
    except TrabalhadorInativo:
        return _voltar_lista(erro="O trabalhador está inativo — reative-o antes de "
                                  "emitir código.")
    except CodigosEsgotados:
        return _voltar_lista(erro="Não há códigos disponíveis (os 10.000 já foram "
                                  "emitidos).")
    except TrabalhadorInexistente:
        return _voltar_lista(erro="Trabalhador não encontrado.")
    msg = (f"Código {r['codigo']} gerado para {r['nome_completo']}."
           if r["criado"] else
           f"{r['nome_completo']} já tinha o código {r['codigo']} — preservado.")
    return _voltar_lista(ok=msg)


@router.post("/{id}/codigo/trocar", response_class=HTMLResponse)
async def codigo_trocar(request: Request, id: int, confirmar: str = Form("")):
    atendente, redir = _guard(request)
    if redir:
        return redir
    if confirmar != "1":
        return _voltar_lista(erro="A troca de código precisa ser confirmada — a "
                                  "carteirinha anterior deixa de funcionar.")
    try:
        with conectar() as conn:
            r = trocar_codigo(conn, id, atendente_id=atendente["id"])
    except TrabalhadorSemCodigo:
        return _voltar_lista(erro="Este trabalhador não tem código atual — gere um antes "
                                  "de trocar.")
    except CodigosEsgotados:
        return _voltar_lista(erro="Não foi possível gerar o novo código (os 10.000 já "
                                  "foram emitidos). O código atual continua válido.")
    except TrabalhadorInexistente:
        return _voltar_lista(erro="Trabalhador não encontrado.")
    return _voltar_lista(ok=f"Código trocado: {r['anterior']} saiu de circulação e "
                            f"{r['novo']} é o novo código de {r['nome_completo']}. "
                            f"A carteirinha anterior não funciona mais.")


@router.get("/{id}/carteirinha", response_class=HTMLResponse)
async def carteirinha_individual(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        row = conn.execute(_SQL_CARTEIRINHA + " WHERE t.id = %s", (id,)).fetchone()
    if not row:
        return _voltar_lista(erro="Trabalhador não encontrado.")
    trab = dict(row)
    if not trab["ativo"]:
        return _voltar_lista(erro=f"{trab['nome_completo']} está inativo — a impressão de "
                                  f"carteirinha de inativo está bloqueada nesta versão.")
    if not trab["codigo"]:
        return _voltar_lista(erro=f"{trab['nome_completo']} ainda não tem código — gere um "
                                  f"antes de imprimir.")
    return templates.TemplateResponse("trabalhadores/carteirinha.html", {
        "request": request,
        "atendente": atendente,
        "cartao": _montar_cartao(trab),
        "centro": centro_config(),
    })


# ── Dias de trabalho (agenda) ─────────────────────────────────────────────────

@router.get("/{id}/agenda", response_class=HTMLResponse)
async def form_agenda(request: Request, id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        trabalhador = conn.execute(
            "SELECT * FROM trabalhadores WHERE id = %s", (id,)
        ).fetchone()
        if not trabalhador:
            return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)
        dias = conn.execute(
            "SELECT dia_semana FROM trabalhador_dias WHERE trabalhador_id = %s",
            (id,),
        ).fetchall()
    dias_atuais = {r["dia_semana"] for r in dias}
    return templates.TemplateResponse("trabalhadores/agenda.html", {
        "request": request,
        "atendente": atendente,
        "trabalhador": dict(trabalhador),
        "nomes_dia": _NOMES_DIA,
        "dias_atuais": dias_atuais,
    })


@router.post("/{id}/agenda", response_class=HTMLResponse)
async def salvar_agenda(
    request: Request,
    id: int,
    dias_semana: list[int] = Form([]),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            "DELETE FROM trabalhador_dias WHERE trabalhador_id = %s", (id,)
        )
        for dia in dias_semana:
            conn.execute(
                """INSERT INTO trabalhador_dias (trabalhador_id, dia_semana)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (id, dia),
            )
    return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)


# ── Presença (histórico) ──────────────────────────────────────────────────────

@router.get("/{id}/presenca", response_class=HTMLResponse)
async def historico_presenca(request: Request, id: int, mes: str = ""):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        trabalhador = conn.execute(
            "SELECT * FROM trabalhadores WHERE id = %s", (id,)
        ).fetchone()
        if not trabalhador:
            return RedirectResponse(url="/cadastros/trabalhadores", status_code=303)

        hoje = conn.execute(
            "SELECT ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date)::text AS d"
        ).fetchone()["d"]
        mes_atual = hoje[:7]
        mes_busca = mes if mes else mes_atual

        registros = conn.execute(
            """SELECT tp.id, tp.trabalhador_id, tp.dia_trabalho_id, tp.presente,
                      tp.hora_chegada, tp.hora_saida, tp.data::text AS data
               FROM trabalhador_presenca tp
               WHERE tp.trabalhador_id = %s
                 AND to_char(tp.data, 'YYYY-MM') = %s
               ORDER BY tp.data DESC""",
            (id, mes_busca),
        ).fetchall()

    return templates.TemplateResponse("trabalhadores/presenca.html", {
        "request": request,
        "atendente": atendente,
        "trabalhador": dict(trabalhador),
        "registros": [dict(r) for r in registros],
        "mes_atual": mes_busca,
    })


# ── Check-in de Trabalhadores (Menu Principal) ───────────────────────────────
# Rotas legadas /cadastros/trabalhadores/dia/trabalhador-checkin* foram
# removidas na TASK_001: gravavam pelo modelo antigo (presença exigindo dia
# de atendimento). O registro passa pelo serviço único (servico_presenca).
router_checkin_trab = APIRouter(prefix="/checkin-trabalhador")


def _guard_checkin(request: Request):
    """Check-in de trabalhadores exige a permissão 'checkin.trabalhadores'."""
    from rotas.permissoes import guarda_modulo
    return guarda_modulo(request, "checkin.trabalhadores")


_MSG_STATUS = {
    "novo": "Chegada de {nome} registrada às {hora}.",
    "ja_registrada": "{nome} já estava presente hoje (chegada das {hora}) — nada foi alterado.",
    "ausente": "Ausência de {nome} registrada para hoje.",
    "inativo": "Trabalhador inativo — presença não registrada.",
    "inexistente": "Trabalhador não encontrado.",
}


@router_checkin_trab.get("", response_class=HTMLResponse)
async def checkin_trabalhador_page(
    request: Request, status: str = "", nome: str = "", hora: str = "",
):
    """Check-in de presença — independe de abertura do dia de atendimento.

    Lista todos os ativos (a agenda da semana é indicação, não restrição) e
    marca quem já tem registro na data local de São Paulo.
    """
    atendente, redir = _guard_checkin(request)
    if redir:
        return redir

    aviso = None
    modelo = _MSG_STATUS.get(status)
    if modelo:
        aviso = modelo.format(nome=nome, hora=hora) if nome or hora else None

    with conectar() as conn:
        hoje = conn.execute(
            "SELECT ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date)::text AS d"
        ).fetchone()["d"]
        # Agenda de hoje: trabalhador_dias.dia_semana é 0=Segunda..6=Domingo
        dia_semana = conn.execute(
            "SELECT EXTRACT(ISODOW FROM %s::date)::int - 1 AS d", (hoje,)
        ).fetchone()["d"]

        trabalhadores_agenda = conn.execute(
            """SELECT t.id, t.nome_completo, t.foto_trabalhador,
                      (td.id IS NOT NULL) AS na_agenda
               FROM trabalhadores t
               LEFT JOIN trabalhador_dias td
                      ON td.trabalhador_id = t.id AND td.dia_semana = %s
               WHERE t.ativo = 1
               ORDER BY t.nome_completo""",
            (dia_semana,),
        ).fetchall()

        presencas = conn.execute(
            """SELECT trabalhador_id, presente, hora_chegada
               FROM trabalhador_presenca
               WHERE data = %s::date""",
            (hoje,),
        ).fetchall()

    return templates.TemplateResponse("trabalhadores/checkin.html", {
        "request": request,
        "atendente": atendente,
        "dia": {"data": hoje},
        "trabalhadores_agenda": [dict(t) for t in trabalhadores_agenda],
        "presencas": [dict(p) for p in presencas],
        "aviso": aviso,
        "erro": None,
    })


@router_checkin_trab.post("/{id}/presente", response_class=HTMLResponse)
async def marcar_presente(request: Request, id: int):
    atendente, redir = _guard_checkin(request)
    if redir:
        return redir

    with conectar() as conn:
        try:
            resultado = registrar_chegada(conn, id)
        except TrabalhadorInativo:
            return RedirectResponse(
                url="/checkin-trabalhador?" + urlencode({"status": "inativo"}),
                status_code=303,
            )
        except TrabalhadorInexistente:
            return RedirectResponse(
                url="/checkin-trabalhador?" + urlencode({"status": "inexistente"}),
                status_code=303,
            )

    status = "novo" if resultado["criado"] else "ja_registrada"
    return RedirectResponse(url="/checkin-trabalhador?" + urlencode({
        "status": status,
        "nome": resultado["nome_completo"],
        "hora": resultado["hora_chegada"] or "—",
    }), status_code=303)


@router_checkin_trab.post("/{id}/ausente", response_class=HTMLResponse)
async def marcar_ausente(request: Request, id: int):
    atendente, redir = _guard_checkin(request)
    if redir:
        return redir

    with conectar() as conn:
        try:
            resultado = registrar_ausencia_explicita(conn, id)
        except TrabalhadorInativo:
            return RedirectResponse(
                url="/checkin-trabalhador?" + urlencode({"status": "inativo"}),
                status_code=303,
            )
        except TrabalhadorInexistente:
            return RedirectResponse(
                url="/checkin-trabalhador?" + urlencode({"status": "inexistente"}),
                status_code=303,
            )

    return RedirectResponse(url="/checkin-trabalhador?" + urlencode({
        "status": "ausente",
        "nome": resultado["nome_completo"],
    }), status_code=303)
