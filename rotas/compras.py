# -*- coding: utf-8 -*-
"""Lançamento de compras — despesa no financeiro com nota fiscal (foto obrigatória)."""
import hashlib
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

# Injeção de falha de disco para as provas de ciclo de vida do arquivo
# (TASK_012 §6/FIN-09). Existe porque as suítes conversam com o app por HTTP,
# ou seja, em OUTRO processo: um teste não consegue "monkeypatchar" a rota em
# execução. O envio só é lido quando o ambiente é comprovadamente descartável —
# em DEV/produção o código é inerte e nunca dispara.
FALHA_DE_DISCO_ENV = "SHAMBALA_FALHA_DISCO_COMPRA"
_FALHAS_DE_DISCO = ("preparar", "promover", "morrer", "compensar")


def _guard(request: Request):
    """Login + permissão 'financeiro.compras' (import lazy evita import circular)."""
    from rotas.permissoes import guarda_modulo
    return guarda_modulo(request, "financeiro.compras")


def _nome_da_nota(mov_id: int, ext: str) -> str:
    """Nome do arquivo da nota — o `mov_id` só existe depois do INSERT."""
    return f"nota_{mov_id}_{uuid.uuid4().hex[:8]}{ext}"


def _caminho_da_nota(nome_arquivo: str) -> str:
    """Caminho absoluto DENTRO de static/fotos — e a validação que garante isso.

    A limpeza de arquivo só pode tocar o que ela mesma criou; se o nome vier
    de outro lugar (linha antiga, banco adulterado) a função recusa em vez de
    apagar qualquer coisa fora da pasta de fotos.
    """
    alvo = os.path.abspath(os.path.join(FOTOS_DIR, nome_arquivo))
    raiz = os.path.abspath(FOTOS_DIR) + os.sep
    if not alvo.startswith(raiz):
        raise ValueError(f"caminho de nota fora de static/fotos: {nome_arquivo!r}")
    return alvo


def _falha_de_disco_injetada(etapa: str, request=None) -> None:
    """Levanta `OSError` quando o ambiente descartável pediu falha nesta etapa.

    Guardas: precisa de `SHAMBALA_MODO_TESTE=1` E de um banco de teste com o
    selo do runner (`_falhas_de_identidade`). Qualquer outro ambiente segue o
    caminho normal, mesmo que a variável esteja definida por engano.

    O gatilho é o cabeçalho `X-Tentativa-Falha-Disco`, não a variável: a suíte
    manda o cabeçalho só nas próprias requisições, e as suítes que rodam em
    paralelo no mesmo app não são afetadas. A variável apenas habilita.
    """
    habilitado = (os.environ.get(FALHA_DE_DISCO_ENV) or "").strip().lower()
    if habilitado not in ("1", "todas", *_FALHAS_DE_DISCO):
        return
    if request is not None:
        pedida = (request.headers.get("x-tentativa-falha-disco") or "").strip().lower()
    else:
        pedida = habilitado
    # O cabeçalho pode pedir MAIS DE UMA etapa ("promover,compensar"): o caso da
    # compensação que também falha precisa das duas na mesma requisição.
    pedidas = {p.strip() for p in pedida.split(",") if p.strip()}
    if etapa not in pedidas:
        return
    if os.environ.get("SHAMBALA_MODO_TESTE") != "1":
        return
    # Importado do arquivo pelo caminho: o pacote `tests` não é importável como
    # pacote (não tem __init__.py), e isso mantém a dependência explícita.
    import importlib.util
    caminho = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "tests", "trava_isolamento.py")
    spec = importlib.util.spec_from_file_location("_trava_isolamento", caminho)
    trava = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trava)
    if trava._falhas_de_identidade(None):
        return  # não é ambiente descartável: a injeção não vale aqui
    raise OSError(28, f"No space left on device (falha injetada FIN-09: {etapa})")


def _preparar_nota(conteudo: bytes, nome_arquivo: str, request=None) -> str:
    """Escreve o conteúdo num TEMPORÁRIO ao lado do destino, ANTES do commit.

    TASK_012 §6 (FIN-09), a parte que fecha o ciclo do arquivo e da transação:
    assim o disco é testado antes de qualquer linha ser confirmada, mas nada
    aparece em static/fotos/ se a transação não confirmar — o temporário é
    apagado no rollback. A promoção para o nome definitivo é um `rename` no
    mesmo sistema de arquivos, atômico: ou a nota existe inteira, ou não existe.
    """
    _falha_de_disco_injetada("preparar", request)
    alvo = _caminho_da_nota(nome_arquivo)
    temporario = f"{alvo}.tmp-{uuid.uuid4().hex[:8]}"
    with open(temporario, "wb") as f:
        f.write(conteudo)
        f.flush()
        os.fsync(f.fileno())
    return temporario


def _promover_nota(temporario: str, nome_arquivo: str, request=None) -> str:
    """Torna o temporário visível com o nome definitivo (renomeação atômica)."""
    _falha_de_disco_injetada("promover", request)
    os.replace(temporario, _caminho_da_nota(nome_arquivo))
    return nome_arquivo


def _falha_de_processo_injetada(etapa: str, request=None) -> None:
    """Simula o processo MORRENDO logo depois do commit (§6, FIN-09).

    É a única forma honesta de provar a recuperação: entre o commit (linha
    'pago' com o nome da nota) e a promoção do arquivo não há transação que
    proteja ninguém — se o processo cair ali, o banco fica dizendo que existe
    uma nota que não existe em disco. O que fecha o buraco é a varredura de
    startup; aqui ela é exercitada com um estado REAL deixado por essa morte.
    """
    _falha_de_disco_injetada("morrer", request)


def _remover_arquivo(caminho: str) -> None:
    """Remove SÓ o arquivo indicado, e só se o caminho for de static/fotos."""
    if not caminho:
        return
    try:
        os.remove(caminho)
    except FileNotFoundError:
        pass


# A requisição corrente da compensação (a função é chamada fora do escopo do
# handler, depois do `with`): guardada para que a injeção de falha — e o log —
# saibam de qual requisição se trata. ContextVar porque o servidor é assíncrono.
import contextvars

request_atual: contextvars.ContextVar = contextvars.ContextVar(
    "compras_request_atual", default=None)


def _descartar_compra(mov_id: int, chave_tentativa: str) -> bool:
    """Compensação: cancela a compra E anula a tentativa, na MESMA transação.

    O lançamento não chegou a ter nota. Em vez de deixar um documento válido sem
    a foto obrigatória, ele é marcado como cancelado (mesma coluna de situação
    do módulo) — nenhuma linha é apagada, o fato fica auditável e não entra em
    conta como despesa paga. Na mesma transação, a tentativa deixa de apontar
    para o documento cancelado (`desfazer`): a chave continua sendo a identidade
    da operação e a linha continua auditável, mas o REENVIO passa a receber a
    recusa legível (`tentativa_anulada`) em vez de "deu certo".

    Devolve False quando nem a compensação passou — caso em que o chamador
    mantém a tentativa CONSUMIDA apontando para o documento cancelado (estado
    coerente e auditável) e o operador recebe o erro em vez de "deu certo".
    """
    from banco import conectar as _conectar
    from servico_idempotencia import desfazer

    # DUAS transações, de propósito: primeiro o CANCELAMENTO do documento (é o
    # efeito que precisa valer mesmo se o resto falhar), depois a ANULAÇÃO da
    # tentativa. Se a anulação falhar, o cancelamento já está confirmado e o
    # estado que sobra é coerente e auditável: documento cancelado + tentativa
    # consumida apontando para ele. (Numa transação só, a exceção derrubaria o
    # cancelamento junto e a compra voltaria a "paga" — a primeira versão desta
    # correção fazia isso, e a prova mostrou.)
    try:
        with _conectar() as conn:
            mudou = conn.execute(
                """UPDATE financeiro_movimentacoes
                   SET status = 'cancelado'
                   WHERE id = %s""", (mov_id,)).rowcount
    except Exception as e:  # noqa: BLE001
        print(f"[compras] cancelamento FALHOU para a compra {mov_id}: "
              f"{type(e).__name__}: {e}", flush=True)
        return False

    try:
        _falha_de_disco_injetada("compensar", request_atual.get())
        with _conectar() as conn:
            desfazer(conn, chave=chave_tentativa)
    except Exception as e:  # noqa: BLE001 - anulação que falha é reportada
        print(f"[compras] anulação da tentativa FALHOU para a compra {mov_id} "
              f"({type(e).__name__}: {e}) — o documento ficou cancelado e a "
              f"tentativa consumida apontando para ele", flush=True)
        return False

    print(f"[compras] compra {mov_id} cancelada e tentativa anulada "
          f"({mudou} linha)", flush=True)
    return True


def recuperar_compras_sem_nota() -> dict:
    """Fecha o estado deixado por uma morte entre o commit e a promoção.

    Chamado no startup. O que existe nesse estado: a linha da compra com
    `status='pago'` e `nota_fiscal_foto` preenchida, mas **sem o arquivo** em
    static/fotos (e, muitas vezes, um `.tmp-…` solto ao lado).

    A ação é a mesma da compensação, pela mesma razão: compra paga sem a nota que
    a tela exige não pode ficar valendo como despesa. Em vez de apagar (o fato é
    auditável), a linha é marcada como `cancelado` e a tentativa correspondente é
    anulada — assim o reenvio não responde "deu certo" para documento cancelado.
    Os temporários órfãos que casam com o nome registrado são removidos; nenhum
    outro arquivo é tocado.

    Não faz nada além do que precisa: se a nota está no disco, a compra fica como
    está. Devolve o que fez (para o log de subida).
    """
    from banco import conectar as _conectar
    from servico_idempotencia import DESTINO_ANULADO

    pendentes = []
    with _conectar() as conn:
        pendentes = [dict(r) for r in conn.execute(
            """SELECT id, nota_fiscal_foto FROM financeiro_movimentacoes
               WHERE categoria = 'compra' AND status = 'pago'
                 AND nota_fiscal_foto IS NOT NULL""").fetchall()]

    sem_arquivo = [p for p in pendentes
                   if not os.path.exists(_caminho_da_nota(p["nota_fiscal_foto"]))]
    if not sem_arquivo:
        return {"canceladas": 0, "temporarios_removidos": 0}

    ids = [p["id"] for p in sem_arquivo]
    temporarios = 0
    for p in sem_arquivo:
        alvo = _caminho_da_nota(p["nota_fiscal_foto"])
        pasta, base = os.path.split(alvo)
        try:
            for sobra in os.listdir(pasta):
                if sobra.startswith(base + ".tmp-"):
                    _remover_arquivo(os.path.join(pasta, sobra))
                    temporarios += 1
        except OSError:
            pass

    with _conectar() as conn:
        conn.execute(
            """UPDATE financeiro_movimentacoes SET status = 'cancelado'
               WHERE id = ANY(%s)""", (ids,))
        # a tentativa que produziu esse documento deixa de responder sucesso
        conn.execute(
            """UPDATE operacao_idempotencia
               SET status = 'anulada', documento = NULL, destino = %s,
                   concluido_em = NULL
               WHERE documento = ANY(%s)""",
            (DESTINO_ANULADO, [str(i) for i in ids]))

    return {"canceladas": len(ids), "temporarios_removidos": temporarios}


@router.get("", response_class=HTMLResponse)
async def form_compras(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    return templates.TemplateResponse("compras/form.html", {
        "request": request,
        "atendente": atendente,
        "erro": _mensagem_erro(request.query_params.get("erro")),
    })


def _mensagem_erro(cod: str | None) -> str | None:
    """Recusas de tentativa explicadas em linguagem de operador (TASK_012 §2)."""
    from servico_idempotencia import MENSAGENS_TENTATIVA
    return MENSAGENS_TENTATIVA.get(cod or "")


@router.post("", response_class=HTMLResponse)
async def salvar_compra(
    request: Request,
    valor: str = Form(""),
    descricao: str = Form(""),
    forma_pagamento: str = Form("especie"),
    foto: UploadFile = File(None),
    idempotencia: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir
    request_atual.set(request)

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
    destino_sucesso = (f"/financeiro/compras?ok=1&valor={valor_dec:.2f}"
                       f"&forma={forma_pagamento}")

    # Conteúdo da tentativa: o que foi enviado + a nota em si. Reenviar o
    # mesmo arquivo com os mesmos dados é a MESMA tentativa; outra foto na
    # mesma tentativa é conteúdo divergente.
    corpo_tentativa = {"valor": str(valor_dec), "descricao": desc,
                       "forma": forma_pagamento, "hoje": hoje,
                       "nota": hashlib.sha256(conteudo).hexdigest()}

    from servico_idempotencia import (
        TentativaJaRegistrada, TentativaRecusada, confirmar, exigir_reserva,
    )
    from rotas.tentativa import resposta_de_tentativa

    with conectar() as conn:
        respondida = resposta_de_tentativa(
            conn, idempotencia=idempotencia, corpo=corpo_tentativa,
            erro_para=lambda ce: f"/financeiro/compras?erro={ce}",
            destino_padrao=destino_sucesso)
        if respondida:
            return respondida

        conta_id = conta_id_por_tipo(conn, forma_pagamento)
        if conta_id is None:
            return _erro("Conta financeira não encontrada. Contate o administrador.",
                         valor=valor, descricao=desc, forma=forma_pagamento)

        try:
            chave_tentativa = exigir_reserva(
                conn, idempotencia=idempotencia, operacao="compra",
                autor_id=atendente["id"], corpo=corpo_tentativa)
        except TentativaRecusada as e:
            return RedirectResponse(url=f"/financeiro/compras?erro={e.chave_erro}",
                                    status_code=303)
        except TentativaJaRegistrada as e:
            return RedirectResponse(url=e.destino or destino_sucesso, status_code=303)

        cur = conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, status,
                forma_pagamento, conta_id, data_pagamento)
               VALUES ('saida', 'compra', %s, %s, %s, 'pago', %s, %s, %s)
               RETURNING id""",
            (valor_dec, hoje, desc, forma_pagamento, conta_id, hoje),
        )
        mov_id = cur.fetchone()["id"]

        # TASK_012 §6 (FIN-09): o arquivo NÃO entra na transação.
        #
        # Antes, o nome da nota era calculado e gravado em disco aqui dentro: se
        # o commit falhasse (ou o UPDATE não pegasse), o arquivo ficava em
        # static/fotos/ sem nenhuma linha apontando para ele — órfão que ninguém
        # acha e ninguém apaga. O nome é decidido agora (precisa do mov_id), mas
        # quem grava é o caminho de sucesso, depois do commit. `nome_tentativa`
        # é gravado junto para que o sucesso e a falha falem do MESMO arquivo.
        nome_tentativa = _nome_da_nota(mov_id, ext)  # UMA vez só
        # Disco testado antes da linha ser confirmada: se não cabe, nada é
        # committed e nenhum arquivo definitivo aparece.
        temporario = _preparar_nota(conteudo, nome_tentativa, request)
        try:
            conn.execute(
                "UPDATE financeiro_movimentacoes SET nota_fiscal_foto = %s WHERE id = %s",
                (nome_tentativa, mov_id),
            )
            confirmar(conn, chave=chave_tentativa, documento=mov_id,
                      destino=destino_sucesso)
        except BaseException:
            # Rollback: o temporário desta tentativa vai junto (só ele).
            _remover_arquivo(temporario)
            raise

    # Transação confirmada. Promover é uma renomeação atômica: a partir daqui a
    # nota tem o nome que a linha gravou.
    #
    # Entre o commit e a promoção NÃO existe transação que proteja o par
    # banco↔disco. Se o processo morrer aqui, sobra um documento "pago" cuja
    # nota não está no disco e um temporário solto — estado que
    # `recuperar_compras_sem_nota` (startup) conserta. A injeção abaixo
    # reproduz essa morte para a prova.
    _falha_de_processo_injetada("morrer", request)
    try:
        _promover_nota(temporario, nome_tentativa, request)
    except OSError:
        # Renomear falhou depois do commit. O lançamento não pode ficar "pago"
        # sem a nota que a tela exige: a compra é CANCELADA (não apagada, para
        # o fato ficar auditável) e a tentativa é ANULADA na mesma transação, de
        # modo que reenviar não devolva "deu certo" para documento cancelado.
        # O temporário é removido — só ele, e só se estiver em static/fotos.
        _remover_arquivo(temporario)
        compensou = _descartar_compra(mov_id, chave_tentativa)
        if not compensou:
            # A compensação falhou: a tentativa continua CONSUMIDA apontando
            # para o documento cancelado (rastro preservado, estado coerente) e
            # a varredura de startup fecha o que faltar. O operador recebe erro,
            # nunca "deu certo".
            print(f"[compras] promoção e compensação falharam para a compra "
                  f"{mov_id}; a tentativa segue consumida com o documento",
                  flush=True)
        raise

    return RedirectResponse(url=destino_sucesso, status_code=303)
