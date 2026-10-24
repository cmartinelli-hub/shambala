# -*- coding: utf-8 -*-
"""Terminal fixo de presença — QR da carteirinha ou código digitado (TASK_003).

Uma pessoa autorizada ativa o terminal no aparelho; depois disso o voluntário não
faz login. A sessão de terminal é PRÓPRIA e restrita:

  - emitida apenas por ação autenticada de operador com `checkin.trabalhadores`
    na ação escrever (mesma permissão do check-in da recepção);
  - vive em cookie dedicado (`sessao_terminal`), httpOnly, SameSite=Lax, com o
    mesmo padrão de cookie do projeto (Secure quando SHAMBALA_HTTPS=true);
  - só abre a tela de leitura e registra chegada por código. Não é procurada por
    nenhuma outra rota, portanto não herda acesso a cadastro, financeiro nem
    menu; e a sessão comum do navegador é encerrada na transição, para não
    sobrar administração acessível a quem passa na entrada;
  - vale 24 horas desde a ativação (um dia inteiro de operação, sem depender de
    abrir o atendimento) e é encerrada a pedido.

Não há endpoint anônimo: sem sessão válida nada é retornado nem gravado.
"""

import os
import re
import time
import secrets
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from banco import conectar
from rotas.auth import obter_atendente_logado, _sessoes
from rotas.permissoes import usuario_pode
from servico_chegada import registrar_chegada_por_codigo
from servico_presenca import OrigemInvalida
from templates_config import templates, centro_config

router = APIRouter(prefix="/terminal")

logger = logging.getLogger(__name__)

_COOKIE = "sessao_terminal"
_TTL = 24 * 3600          # um dia inteiro de operação, desde a ativação
_MAX_SESSOES = 50         # aparelhos de terminal são poucos; nada de fila complexa

# token → (atendente_id que ativou, instante da ativação)
_sessoes_terminal: dict[str, tuple[int, float]] = {}

# Nome de foto é gerado pelo próprio upload (trab_<id>_<hex>.jpg). Aceitamos só
# esse formato ao montar a referência, para que nada do cadastro vire caminho.
_RE_FOTO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Mensagens do servidor nunca explicam o cadastro: só o resultado do registro.
_RESULTADOS_ERRO = {"nao_encontrado", "retirado", "inativo", "invalido"}


def _criar_sessao_terminal(atendente_id: int) -> str:
    token = secrets.token_hex(32)
    agora = time.time()
    for t in [t for t, (_, ts) in _sessoes_terminal.items() if agora - ts > _TTL]:
        del _sessoes_terminal[t]
    if len(_sessoes_terminal) >= _MAX_SESSOES:
        mais_antiga = min(_sessoes_terminal, key=lambda t: _sessoes_terminal[t][1])
        del _sessoes_terminal[mais_antiga]
    _sessoes_terminal[token] = (atendente_id, agora)
    return token


def obter_terminal_logado(request: Request):
    """Sessão de terminal válida → dict do aparelho-logado; senão None.

    Autorização conferida em cada requisição: além do token e do prazo, a pessoa
    que ativou precisa continuar existindo e ativa. Sem isso, nenhum dado
    pessoal é retornado e nenhuma presença é gravada.
    """
    token = request.cookies.get(_COOKIE)
    if not token:
        return None
    entrada = _sessoes_terminal.get(token)
    if not entrada:
        return None
    atendente_id, ativado_em = entrada
    if time.time() - ativado_em > _TTL:
        _sessoes_terminal.pop(token, None)
        return None
    with conectar() as conn:
        row = conn.execute(
            "SELECT id, nome_completo, nome_usuario FROM atendentes "
            "WHERE id = %s AND ativo = 1",
            (atendente_id,),
        ).fetchone()
    if not row:
        _sessoes_terminal.pop(token, None)
        return None
    return {"id": row["id"], "nome_completo": row["nome_completo"],
            "ativado_em": ativado_em}


def _sem_sessao_terminal():
    return JSONResponse(status_code=401,
                        content={"resultado": "sessao_encerrada", "nome": None,
                                 "foto": None, "data": None, "hora": None})


def _referencia_foto(nome) -> str | None:
    if not nome or not _RE_FOTO.match(nome) or ".." in nome or "/" in nome:
        return None
    return f"/static/fotos/{nome}"


# ── Ativação e encerramento ──────────────────────────────────────────────────

@router.post("/ativar", response_class=HTMLResponse)
async def ativar(request: Request):
    """Operador autenticado habilita o modo terminal neste aparelho.

    Exige login comum + escrita em `checkin.trabalhadores`. A sessão comum
    daquele navegador é encerrada na transição (as de outros aparelhos ficam
    intactas), para que não sobre administração acessível a quem passa na
    entrada.
    """
    atendente = obter_atendente_logado(request)
    if not atendente:
        return RedirectResponse(url="/login", status_code=303)
    if not usuario_pode(atendente["id"], "checkin.trabalhadores", "escrever"):
        return HTMLResponse(status_code=403, content="Acesso negado.")

    # Uma ativação por aparelho: a sessão de terminal anterior deste navegador
    # deixa de existir antes de emitir a nova.
    token_anterior = request.cookies.get(_COOKIE)
    if token_anterior:
        _sessoes_terminal.pop(token_anterior, None)

    token = _criar_sessao_terminal(atendente["id"])
    token_comum = request.cookies.get("sessao")
    if token_comum:
        _sessoes.pop(token_comum, None)

    resp = RedirectResponse(url="/terminal/", status_code=303)
    resp.delete_cookie("sessao", path="/")
    usar_https = os.environ.get("SHAMBALA_HTTPS", "false").lower() == "true"
    resp.set_cookie(_COOKIE, token, max_age=_TTL, httponly=True,
                    samesite="lax", secure=usar_https, path="/")
    return resp


@router.post("/encerrar", response_class=HTMLResponse)
async def encerrar(request: Request):
    """Desliga o terminal deste aparelho. Voltar a administrar exige login normal."""
    token = request.cookies.get(_COOKIE)
    if token:
        _sessoes_terminal.pop(token, None)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(_COOKIE, path="/")
    return resp


# ── Tela ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def tela(request: Request):
    """Página do terminal, sem navegação geral — só com sessão de terminal."""
    if not obter_terminal_logado(request):
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("terminal/leitura.html", {
        "request": request,
        "centro": centro_config(),
        "expira_em": _TTL,
    })


# ── Chegada por código (QR e teclado pelo mesmo caminho) ─────────────────────

@router.post("/chegada")
async def chegada(request: Request):
    """Registra a chegada do código apresentado no terminal.

    Formato e sessão validados no servidor; resolução do código e registro na
    MESMA transação, confirmada antes da resposta de sucesso. O corpo é JSON
    `{codigo, origem}` com origem `terminal_qr` ou `terminal_codigo`.
    """
    if not obter_terminal_logado(request):
        return _sem_sessao_terminal()

    try:
        corpo = await request.json()
    except Exception:
        return JSONResponse(status_code=400,
                            content={"resultado": "invalido", "nome": None,
                                     "foto": None, "data": None, "hora": None})
    if not isinstance(corpo, dict):
        return JSONResponse(status_code=400,
                            content={"resultado": "invalido", "nome": None,
                                     "foto": None, "data": None, "hora": None})

    codigo = corpo.get("codigo")
    origem = corpo.get("origem")
    try:
        with conectar() as conn:
            resultado = registrar_chegada_por_codigo(conn, codigo, origem)
            foto = None
            if resultado.get("gravado"):
                row = conn.execute(
                    "SELECT foto_trabalhador FROM trabalhadores WHERE id = %s",
                    (resultado["trabalhador_id"],),
                ).fetchone()
                foto = _referencia_foto(row["foto_trabalhador"] if row else None)
    except OrigemInvalida:
        # Origem que não é de terminal: recusa sem tentar gravar nada.
        return JSONResponse(status_code=400,
                            content={"resultado": "invalido", "nome": None,
                                     "foto": None, "data": None, "hora": None})

    if resultado["resultado"] in _RESULTADOS_ERRO:
        # Nada foi gravado. Não devolvemos nome nem foto nesses casos.
        return JSONResponse(status_code=200,
                            content={"resultado": resultado["resultado"], "nome": None,
                                     "foto": None, "data": None, "hora": None})

    return JSONResponse(status_code=200, content={
        "resultado": resultado["resultado"],
        "nome": resultado["nome_completo"],
        "foto": foto,
        "data": resultado["data"],
        "hora": resultado["hora_chegada"],
    })
