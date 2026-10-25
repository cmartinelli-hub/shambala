# -*- coding: utf-8 -*-
"""Lançamento de compras — despesa no financeiro com nota fiscal (foto obrigatória)."""
import hashlib
import os
import time
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
_FALHAS_DE_DISCO = ("preparar", "promover", "morrer", "compensar", "pausar")


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


def _falha_de_disco_pedida(etapa: str, request=None) -> bool:
    """O ambiente descartável pediu esta etapa? (mesma guarda de `_falha_de_disco_injetada`)"""
    habilitado = (os.environ.get(FALHA_DE_DISCO_ENV) or "").strip().lower()
    if not habilitado:
        return False
    if request is not None:
        pedidas = {p.strip() for p in
                   (request.headers.get("x-tentativa-falha-disco") or "")
                   .strip().lower().split(",") if p.strip()}
    else:
        pedidas = {p.strip() for p in habilitado.split(",") if p.strip()}
    return etapa in pedidas and os.environ.get("SHAMBALA_MODO_TESTE") == "1"


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
import contextlib
import contextvars
from contextlib import contextmanager
from psycopg2.extras import RealDictCursor

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


# ── Recuperação do estado deixado por uma morte entre commit e promoção ─────
#
# A regra "compra paga sem arquivo → cancelar" só é segura com limites muito
# estreitos. Sem eles ela cancelaria compra HISTÓRICA por causa de um diretório
# que não foi montado, de um disco removível ausente ou de uma falha de leitura
# — e isso é apagar fato por acidente de ambiente. Os limites implementados:
#
# 1. SÓ TENTATIVAS NOVAS E INTERROMPIDAS. A linha tem de casar com uma tentativa
#    de idempotência (`operacao='compra'`) **concluída**, cujo `documento` é ela,
#    e cuja CONCLUSÃO é recente (`INTERVALO_RECUPERACAO`). Compra antiga, sem
#    tentativa correspondente, nunca é tocada — nem que o arquivo tenha sumido.
# 2. NÃO INTERFERE EM COMPRA EM PROCESSAMENTO. A tentativa em `em_andamento` é
#    outra coisa (requisição em voo) e fica de fora; e a recuperação inteira roda
#    sob `pg_try_advisory_lock`, de modo que dois processos iniciando ao mesmo
#    tempo não trabalham o mesmo estado — quem não pega a trava não age.
# 3. SÓ COM PROVA INDEPENDENTE DE QUE O ARQUIVO NUNCA EXISTIU. Não basta
#    `os.path.exists()` devolver False: isso é o mesmo sintoma de "diretório não
#    montado". O caso interrompido deixa o TEMPORÁRIO ao lado do destino
#    (`<nome>.tmp-<rand>`), porque o conteúdo é escrito antes do commit e a
#    promoção (rename) é o último passo. Sem temporário, sem recuperação.
# 4. UM COMANDO SÓ PARA O QUE ANDA JUNTO. O cancelamento e a anulação da chave
#    são confirmados juntos; se algo falhar, o estado NÃO muda (rollback) e a
#    próxima subida conclui. Nunca "cancelado com tentativa respondendo sucesso".
# 5. SÓ CANCELA — nunca apaga. A linha fica auditável.

# Quantas horas para trás a recuperação olha. Além disso, é histórico.
INTERVALO_RECUPERACAO = "interval '24 hours'"


# Trava por COMPRA em finalização. Uma requisição ativa segura esta trava desde
# antes do commit até a promoção do arquivo terminar. A recuperação só age nas
# compras cuja trava ela CONSEGUE pegar — e não pegar significa "há alguém
# finalizando agora", que é exatamente o que não se pode tocar.
TRAVA_COMPRA = "compra_em_finalizacao"


@contextmanager
def _compra_em_finalizacao(mov_id: int):
    """Segura a trava da compra numa conexão dedicada, do início ao fim.

    Trava de sessão pertence à CONEXÃO, não à transação: se fosse tomada numa
    conexão do pool e solta em outra, não protegeria nada (e ficaria presa até a
    conexão morrer). Por isso aqui há uma conexão só, aberta antes do commit e
    fechada no fim — o `finally` solta a trava pelo fechamento da conexão.
    """
    from banco import _obter_pool
    conn = _obter_pool().getconn()
    try:
        conn.cursor_factory = RealDictCursor
        cur = conn.cursor()   # RealDictCursor: a coluna vem por nome, não por índice
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s), %s) AS ok",
                    (TRAVA_COMPRA, int(mov_id)))
        linha = cur.fetchone()
        segurou = bool(linha and linha["ok"])
        cur.close()
        yield segurou
    finally:
        # Soltar a trava EXPLICITAMENTE antes de devolver a conexão: `putconn`
        # devolve a sessão ao pool sem encerrá-la, e uma trava de sessão presa
        # numa conexão do pool sobrevive ao `putconn` — foi assim que a compra
        # ficou "em finalização" para sempre na primeira versão desta trava.
        try:
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s), %s)",
                        (TRAVA_COMPRA, int(mov_id)))
            cur.close()
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        _obter_pool().putconn(conn)


def _segurar_compra(conn, mov_id: int) -> bool:
    """Marca esta compra como "em finalização" nesta sessão do banco.

    Trava de SESSÃO (`pg_advisory_lock`), de propósito: ela precisa sobreviver ao
    commit da transação e só cair no `finally` da requisição. `pg_try_advisory_lock`
    não bloqueia: se outra sessão já a tem, devolve False.
    """
    linha = conn.execute(
        "SELECT pg_try_advisory_lock(hashtext(%s), %s) AS ok",
        (TRAVA_COMPRA, int(mov_id))).fetchone()
    return bool(linha and linha["ok"])


def _soltar_compra(conn, mov_id: int) -> None:
    try:
        conn.execute("SELECT pg_advisory_unlock(hashtext(%s), %s)",
                     (TRAVA_COMPRA, int(mov_id)))
    except Exception:  # noqa: BLE001 - soltar trava nunca derruba a resposta
        pass


def _pode_recuperar(conn, mov_id: int) -> bool:
    """A recuperação pode mexer nesta compra?

    Só se NINGUÉM estiver finalizando — isto é, se ela conseguir tomar a trava
    da compra. A trava fica presa à conexão da recuperação até o fim dela (é o
    que impede uma requisição de entrar no meio); quem não consegue tomar
    significa "há alguém finalizando agora" e a compra é preservada.
    """
    linha = conn.execute(
        "SELECT pg_try_advisory_lock(hashtext(%s), %s) AS ok",
        (TRAVA_COMPRA, int(mov_id))).fetchone()
    return bool(linha and linha["ok"])


def _trava_de_recuperacao(conn) -> bool:
    """Trava de execução única da recuperação (não bloqueante).

    `pg_try_advisory_lock` devolve True para o primeiro processo e False para os
    demais: dois startups simultâneos não recuperam o mesmo estado duas vezes.
    A trava é de SESSÃO e é liberada no fim de `recuperar_compras_sem_nota`.
    """
    chave = conn.execute(
        "SELECT pg_try_advisory_lock(hashtext('recuperacao_compra_sem_nota')) AS ok"
    ).fetchone()
    return bool(chave and chave["ok"])


def _estado_interrompido(linha: dict) -> dict | None:
    """A linha é de uma tentativa NOVA e INTERROMPIDA? Devolve a evidência.

    Precisa das três coisas juntas: arquivo definitivo ausente, TEMPORÁRIO da
    mesma tentativa presente, e tentativa de idempotência recente apontando para
    esta linha. Faltando qualquer uma, não é o caso — e não se mexe.
    """
    nome = linha["nota_fiscal_foto"]
    if not nome:
        return None
    try:
        alvo = _caminho_da_nota(nome)
    except ValueError:
        return None                      # nome fora da pasta: não é nosso caso
    if os.path.exists(alvo):
        return None                      # arquivo está lá: nada aconteceu
    pasta, base = os.path.split(alvo)
    try:
        temporarios = [t for t in os.listdir(pasta) if t.startswith(base + ".tmp-")]
    except OSError:
        return None                      # não conseguimos LER: não é prova de nada
    if not temporarios:
        return None                      # sem temporário: arquivo sumiu, não interrompeu
    return {"alvo": alvo, "temporarios": temporarios}


def recuperar_compras_sem_nota() -> dict:
    """Fecha SÓ o estado que uma tentativa nova e interrompida deixou.

    Chamado no startup, uma vez por processo (e protegido contra dois processos
    ao mesmo tempo). Devolve o que fez, para o log de subida:
    `{"canceladas", "temporarios_removidos", "puladas", "executou"}`.

    `puladas` são as linhas pago/sem arquivo que NÃO casaram com o caso
    interrompido (arquivo sumiu, diretório não montado, compra histórica): elas
    ficam como estão, e o número aparece no log para não passarem em silêncio.
    """
    from banco import conectar as _conectar
    from servico_idempotencia import DESTINO_ANULADO

    feito = {"executou": False, "canceladas": 0,
             "temporarios_removidos": 0, "puladas": 0, "em_finalizacao": 0,
             "fora_do_prazo": 0}
    with _conectar() as conn:
        if not _trava_de_recuperacao(conn):
            # Outro processo já está recuperando: não interfere.
            return feito
        recuperadas: list[int] = []
        try:
            candidatas = [dict(r) for r in conn.execute(
                f"""SELECT fm.id, fm.nota_fiscal_foto,
                           fm.data_movimentacao, oi.chave
                    FROM financeiro_movimentacoes fm
                    JOIN operacao_idempotencia oi
                      ON oi.documento = fm.id::text
                     AND oi.operacao = 'compra'
                     AND oi.status = 'concluida'
                   WHERE fm.categoria = 'compra'
                     AND fm.status = 'pago'
                     AND fm.nota_fiscal_foto IS NOT NULL
                     AND COALESCE(oi.concluido_em,
                                 oi.criado_em)::timestamp
                         >= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')
                            - {INTERVALO_RECUPERACAO}
                   FOR UPDATE OF fm""").fetchall()]

            recuperaveis, puladas, travadas, temporarios = [], 0, 0, 0
            for linha in candidatas:
                # 0) A COMPRA ESTÁ SENDO FINALIZADA AGORA? A tentativa dela já
                #    está `concluida` na janela entre o commit e o arquivo, então
                #    status de tentativa não distingue nada. Quem distingue é a
                #    trava: se a requisição ativa a segura, `_pode_recuperar`
                #    devolve False e a compra NÃO é tocada.
                if not _pode_recuperar(conn, linha["id"]):
                    travadas += 1
                    continue
                estado = _estado_interrompido(linha)
                if estado is None:
                    puladas += 1
                    continue
                recuperaveis.append(linha)

            # Fora do prazo: tentativa interrompida antiga NÃO entra na
            # recuperação automática. Fica contada (e vai para o log) para não
            # desaparecer: são tentativas identificáveis por
            # `operacao_idempotencia WHERE operacao='compra' AND status='concluida'
            #  AND documento IS NOT NULL` com conclusão anterior ao intervalo,
            # cujo documento esteja pago sem arquivo e com temporário ao lado.
            feito["fora_do_prazo"] = conn.execute(
                f"""SELECT count(*) AS c
                    FROM financeiro_movimentacoes fm
                    JOIN operacao_idempotencia oi
                      ON oi.documento = fm.id::text
                     AND oi.operacao = 'compra' AND oi.status = 'concluida'
                   WHERE fm.categoria = 'compra' AND fm.status = 'pago'
                     AND fm.nota_fiscal_foto IS NOT NULL
                     AND COALESCE(oi.concluido_em, oi.criado_em)::timestamp
                         < (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')
                           - {INTERVALO_RECUPERACAO}""").fetchone()["c"]

            if not recuperaveis:
                feito["puladas"] = puladas
                feito["em_finalizacao"] = travadas
                feito["executou"] = True
                return feito

            ids = [l["id"] for l in recuperaveis]
            chaves = [l["chave"] for l in recuperaveis]
            # Cancelamento E anulação no MESMO commit: ou os dois, ou nenhum.
            # Um estado "cancelado com a chave respondendo sucesso" é justamente
            # o que não pode existir.
            conn.execute(
                """UPDATE financeiro_movimentacoes SET status = 'cancelado'
                   WHERE id = ANY(%s) AND status = 'pago'""", (ids,))
            conn.execute(
                """UPDATE operacao_idempotencia
                   SET status = 'anulada', documento = NULL, destino = %s,
                       concluido_em = NULL
                   WHERE chave = ANY(%s)""", (DESTINO_ANULADO, chaves))

            # Só depois do commit: os temporários desta tentativa (e só eles).
            for linha in recuperaveis:
                estado = _estado_interrompido(linha)
                if estado is None:
                    continue
                for t in estado["temporarios"]:
                    _remover_arquivo(os.path.join(os.path.dirname(estado["alvo"]), t))
                    temporarios += 1

            feito.update(executou=True, canceladas=len(recuperaveis),
                         temporarios_removidos=temporarios, puladas=puladas,
                         em_finalizacao=travadas)
            recuperadas = [l["id"] for l in recuperaveis]
        finally:
            for mid in recuperadas:
                try:
                    conn.execute("SELECT pg_advisory_unlock(hashtext(%s), %s)",
                                 (TRAVA_COMPRA, int(mid)))
                except Exception:  # noqa: BLE001
                    pass
            conn.execute(
                "SELECT pg_advisory_unlock(hashtext('recuperacao_compra_sem_nota'))")
    return feito


@router.get("/pendencias", response_class=HTMLResponse)
async def pendencias_de_nota(request: Request):
    """Diagnóstico SOMENTE LEITURA das compras sem a nota em disco (§4).

    O log de subida é técnico demais para o operador: ele precisa saber QUAIS
    compras estão nesse estado, com link para o documento, e o que NÃO fazer. Não
    existe botão de reparo: a página só mostra e explica.

    Distingue duas situações que o log confunde:
    - **tentativa interrompida** (há temporário + tentativa recente): seria
      recuperada no próximo startup;
    - **arquivo ausente sem prova** (sem temporário): pode ser volume não
      montado/perda de armazenamento — NÃO é interrupção comprovada;
    - **fora do prazo** (interrupção antiga): a varredura automática não age.
    """
    atendente, redir = _guard(request)
    if redir:
        return redir

    from servico_idempotencia import DESTINO_ANULADO
    from datetime import datetime, timedelta

    with conectar() as conn:
        linhas = [dict(r) for r in conn.execute(
            """SELECT fm.id, fm.descricao, fm.valor, fm.data_movimentacao,
                      fm.nota_fiscal_foto, fm.status,
                      oi.chave, oi.concluido_em, oi.criado_em, oi.status AS st_tent
                 FROM financeiro_movimentacoes fm
                 LEFT JOIN operacao_idempotencia oi
                   ON oi.documento = fm.id::text AND oi.operacao = 'compra'
                WHERE fm.categoria = 'compra'
                  AND fm.nota_fiscal_foto IS NOT NULL
                ORDER BY fm.id DESC""").fetchall()]

    limite = datetime.now() - timedelta(hours=24)
    itens = []
    for linha in linhas:
        try:
            alvo = _caminho_da_nota(linha["nota_fiscal_foto"])
        except ValueError:
            continue
        if os.path.exists(alvo):
            continue
        pasta, base = os.path.split(alvo)
        try:
            temporarios = [t for t in os.listdir(pasta) if t.startswith(base + ".tmp-")]
        except OSError:
            # Não conseguir LER o diretório não é prova de nada: é problema de
            # acesso ao armazenamento.
            itens.append(dict(linha, situacao="sem_acesso",
                              temporarios=[], pode_automatico=False))
            continue
        momento = linha["concluido_em"] or linha["criado_em"]
        recente = False
        with contextlib.suppress(Exception):
            recente = datetime.fromisoformat(str(momento)[:19]) >= limite
        if temporarios and recente and linha["st_tent"] == "concluida":
            situacao = "interrompida"
        elif temporarios and not recente:
            situacao = "fora_do_prazo"
        else:
            situacao = "sem_prova"
        itens.append(dict(linha, situacao=situacao, temporarios=temporarios,
                          pode_automatico=(situacao == "interrompida")))

    return templates.TemplateResponse("compras/pendencias.html", {
        "request": request,
        "atendente": atendente,
        "itens": itens,
        "fotos_dir": FOTOS_DIR,
    })


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
    conta_id: int = Form(0),
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
            destino_padrao=destino_sucesso,
            operacao="compra", autor_id=atendente["id"])
        if respondida:
            return respondida

        # TASK_016: a conta da compra segue o mesmo contrato conta×forma dos
        # recebimentos (era `conta_id_por_tipo`, que pegava a primeira do tipo).
        from servico_sessao import SessaoInvalida as _SI
        from servico_sessao import conta_de_destino as _conta_destino
        try:
            conta_id = _conta_destino(
                conn, forma_pagamento, conta_id or None, exigir_escolha=False)
        except _SI:
            return _erro("Não há conta cadastrada que possa pagar esta forma. "
                         "Confira as contas em Configurações.",
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
        # Trava por compra, tomada ANTES do commit e segurada até a promoção
        # terminar. É ela que separa "requisição ativa" de "tentativa
        # interrompida": na janela entre o commit e o arquivo a tentativa JÁ
        # está `concluida` — excluir `em_andamento` não prova proteção sozinho
        # (foi o que o dono mostrou). Quem está na janela SEGURA a trava; a
        # recuperação só age no que ela conseguir travar.
        with _compra_em_finalizacao(mov_id):
            try:
                conn.execute(
                    """UPDATE financeiro_movimentacoes
                       SET nota_fiscal_foto = %s WHERE id = %s""",
                    (nome_tentativa, mov_id),
                )
                confirmar(conn, chave=chave_tentativa, documento=mov_id,
                          destino=destino_sucesso)
            except BaseException:
                # Rollback: o temporário desta tentativa vai junto (só ele).
                _remover_arquivo(temporario)
                raise

            # A transação tem de estar CONFIRMADA aqui: a janela entre o commit
            # e o arquivo é justamente o que esta trava protege, e enquanto o
            # `with conectar()` não sai, nada foi confirmado ainda. Commit
            # explícito agora; o `with` externo só fecha a conexão depois.
            conn.commit()

            # Promover é uma renomeação atômica: a partir daqui a nota tem o
            # nome que a linha gravou.
            #
            # Entre o commit e a promoção NÃO existe transação que proteja o par
            # banco↔disco. Se o processo morrer aqui, sobra um documento "pago"
            # cuja nota não está no disco e um temporário solto — estado que
            # `recuperar_compras_sem_nota` fecha, porque a trava desta compra
            # morre junto com o processo. A injeção abaixo reproduz essa morte.
            _falha_de_processo_injetada("morrer", request)
            # "pausar": mantém a requisição VIVA dentro da janela (depois do
            # commit, antes da promoção) por alguns segundos, para a prova matar
            # o processo com SIGKILL nesse ponto exato — morte REAL, não
            # simulação por exceção. Mesma guarda das outras injeções.
            if _falha_de_disco_pedida("pausar", request):
                # Sentinela num caminho previsível pela suíte; o `sleep` mantém a
                # requisição viva na janela para a prova matar o processo.
                caminho = os.environ.get("SHAMBALA_JANELA_SENTINELA",
                                         "/tmp/t014_na_janela")
                with open(caminho, "w") as _f:
                    _f.write(str(mov_id))
                time.sleep(10)
            try:
                _promover_nota(temporario, nome_tentativa, request)
            except OSError:
                # Renomear falhou depois do commit: o lançamento não pode ficar
                # "pago" sem a nota. A compra é CANCELADA (não apagada) e a
                # tentativa ANULADA na mesma transação — reenviar não devolve
                # "deu certo" para documento cancelado.
                _remover_arquivo(temporario)
                if not _descartar_compra(mov_id, chave_tentativa):
                    print(f"[compras] promoção e compensação falharam para a "
                          f"compra {mov_id}; a tentativa segue consumida com o "
                          f"documento", flush=True)
                raise

    return RedirectResponse(url=destino_sucesso, status_code=303)
