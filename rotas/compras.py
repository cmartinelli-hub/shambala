# -*- coding: utf-8 -*-
"""Lançamento de compras — despesa no financeiro com nota fiscal (foto obrigatória)."""
import os
import uuid
from datetime import date

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar, conta_id_por_tipo
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/financeiro/compras")

FOTOS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fotos")
os.makedirs(FOTOS_DIR, exist_ok=True)
EXTENSOES_PERMITIDAS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TAMANHO_MAXIMO = 5 * 1024 * 1024  # 5MB
FORMAS_PERMITIDAS = {"especie", "pix"}


def _guard(request: Request):
    """Login + permissão 'financeiro.compras' (import lazy evita import circular)."""
    from rotas.permissoes import usuario_pode
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    if not usuario_pode(atendente["id"], "financeiro.compras"):
        return None, HTMLResponse(status_code=403, content="Acesso negado.")
    return atendente, None


def _salvar_foto_nota(conteudo: bytes, mov_id: int, ext: str) -> str:
    """Grava a foto da nota em static/fotos/ e retorna o nome do arquivo."""
    nome_arquivo = f"nota_{mov_id}_{uuid.uuid4().hex[:8]}{ext}"
    with open(os.path.join(FOTOS_DIR, nome_arquivo), "wb") as f:
        f.write(conteudo)
    return nome_arquivo


@router.get("", response_class=HTMLResponse)
async def form_compras(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("compras/form.html", {
        "request": request,
        "atendente": atendente,
    })


@router.post("", response_class=HTMLResponse)
async def salvar_compra(
    request: Request,
    valor: str = Form(""),
    descricao: str = Form(""),
    forma_pagamento: str = Form("especie"),
    foto: UploadFile = File(None),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    def _erro(mensagem: str, **valores):
        """Re-renderiza o form com a mensagem, preservando os campos digitados."""
        return templates.TemplateResponse("compras/form.html", {
            "request": request,
            "atendente": atendente,
            "erro": mensagem,
            "valor_anterior": valores.get("valor", ""),
            "descricao_anterior": valores.get("descricao", ""),
            "forma_anterior": valores.get("forma", "especie"),
        })

    # 1) Valor — aceita vírgula decimal
    try:
        valor_dec = round(float((valor or "").strip().replace(",", ".")), 2)
    except ValueError:
        valor_dec = 0.0
    if valor_dec <= 0:
        return _erro("Informe um valor válido maior que zero.",
                     descricao=descricao, forma=forma_pagamento)

    # 2) Descrição
    desc = descricao.strip()
    if not desc:
        return _erro("Informe a descrição da compra.",
                     valor=valor, forma=forma_pagamento)

    # 3) Forma de pagamento (whitelist)
    if forma_pagamento not in FORMAS_PERMITIDAS:
        forma_pagamento = "especie"

    # 4) Foto obrigatória — valida tudo em memória antes de tocar banco/disco
    if not foto or not (foto.filename or "").strip():
        return _erro("A foto da nota fiscal é obrigatória.",
                     valor=valor, descricao=desc, forma=forma_pagamento)
    ext = os.path.splitext(foto.filename)[1].lower()
    if ext not in EXTENSOES_PERMITIDAS:
        return _erro("Formato de imagem não permitido. Use JPG, PNG, GIF ou WEBP.",
                     valor=valor, descricao=desc, forma=forma_pagamento)
    conteudo = await foto.read()
    if len(conteudo) > TAMANHO_MAXIMO:
        return _erro("A foto da nota excede 5MB.",
                     valor=valor, descricao=desc, forma=forma_pagamento)

    hoje = date.today().isoformat()

    with conectar() as conn:
        conta_id = conta_id_por_tipo(conn, forma_pagamento)
        if conta_id is None:
            return _erro("Conta financeira não encontrada. Contate o administrador.",
                         valor=valor, descricao=desc, forma=forma_pagamento)

        cur = conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, status,
                forma_pagamento, conta_id, data_pagamento)
               VALUES ('saida', 'compra', %s, %s, %s, 'pago', %s, %s, %s)
               RETURNING id""",
            (valor_dec, hoje, desc, forma_pagamento, conta_id, hoje),
        )
        mov_id = cur.fetchone()["id"]

        nome_arquivo = _salvar_foto_nota(conteudo, mov_id, ext)
        conn.execute(
            "UPDATE financeiro_movimentacoes SET nota_fiscal_foto = %s WHERE id = %s",
            (nome_arquivo, mov_id),
        )

    return RedirectResponse(
        url=f"/financeiro/compras?ok=1&valor={valor_dec:.2f}&forma={forma_pagamento}",
        status_code=303,
    )
