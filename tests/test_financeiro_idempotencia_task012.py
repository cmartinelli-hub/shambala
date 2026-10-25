#!/usr/bin/env python3
"""Suíte TASK_012 §2 — idempotência das operações financeiras.

Contrato provado, por família de operação, contra rotas HTTP reais:

1. primeira tentativa produz UM efeito;
2. reenvio byte a byte (resposta perdida, duplo clique) devolve o MESMO
   resultado e não produz efeito novo;
3. duas requisições CONCORRENTES com a mesma chave produzem um único efeito;
4. mesma chave com conteúdo DIVERGENTE é recusada sem escrita;
5. chave NOVA com conteúdo idêntico é uma segunda operação legítima (ou é
   recusada pelo estado do documento — nunca um dinheiro novo silencioso);
6. POST sem identidade é recusado com orientação legível, sem efeito.

Provas complementares: a revisão de preço do PDV não consome a tentativa e a
cesta reprificada é confirmada sem conflito; falha na última escrita libera a
mesma chave; autenticação/RBAC continuam exigidos (chave não é credencial).

Uso: dentro de tests/rodar_testes.sh (app descartável + banco selado).
"""
import http.cookiejar
import json
import os
import re
import secrets
import sys
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal

from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_tres_passes as tp  # noqa: E402
from test_tres_passes import (  # noqa: E402
    assert_eq, preparar_banco, limpar_banco, _garantir_admin, BASE,
)

from banco import conectar, conta_id_por_tipo  # noqa: E402

PREFIXO = f"T12ID-{uuid.uuid4().hex[:8]}"
ADMIN_ID = None
FALHAS = []
APROVADAS = []


def _conferir_banco_do_servidor():
    """Destino do SERVIDOR comprovado nesta execução (TASK_010) — antes de
    qualquer schema, seed ou limpeza."""
    from trava_isolamento import provar_destino_do_servidor, AmbienteInseguro
    esperado = os.environ.get("SHAMBALA_TEST_DB") or tp.BANCO
    try:
        provar_destino_do_servidor(esperado)
    except AmbienteInseguro as e:
        raise SystemExit(f"[ABORTADO] {e}")


def verificar(nome, condição, detalhe=""):
    if condição:
        APROVADAS.append(nome)
        print(f"  PASS  {nome}")
    else:
        FALHAS.append(f"{nome} — {detalhe}")
        print(f"  FALHOU {nome} — {detalhe}")


# ── HTTP real, sem seguir redirect (precisamos do Location) ─────────────────

class _SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _nova_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _SemRedirect())


OP = None
OP_ANON = None


def req(method, path, data=None, o=None):
    corpo = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=corpo, method=method)
    try:
        with (o or OP).open(r) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), \
                resp.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("Location")


def POST(path, dados, o=None):
    return req("POST", path, dados, o)


def POST_multipart(path, campos, arquivo, o=None, cabecalhos=None):
    """multipart/form-data com um arquivo (rota de compra com foto da nota)."""
    borda = "----t12" + secrets.token_hex(8)
    partes = [
        (f'--{borda}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
         f"{v}\r\n").encode() for k, v in campos.items()]
    nome, conteudo = arquivo
    partes.append(
        (f'--{borda}\r\nContent-Disposition: form-data; name="foto"; '
         f'filename="{nome}"\r\nContent-Type: image/jpeg\r\n\r\n').encode()
        + conteudo + b"\r\n")
    partes.append(f"--{borda}--\r\n".encode())
    r = urllib.request.Request(
        BASE + path, data=b"".join(partes), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={borda}",
                 **(cabecalhos or {})})
    try:
        with (o or OP).open(r) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), \
                resp.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("Location")


def envia_form(path, dados, o=None):
    return POST(path, dict(dados), o=o)


def envia_multipart(path, dados, o=None):
    """O item '_foto' (nome, bytes) viaja no dict para que MUTAR o arquivo
    seja mutar conteúdo — mesma chave, nota diferente, precisa dar conflito."""
    campos = {k: v for k, v in dados.items() if k != "_foto"}
    return POST_multipart(path, campos, dados["_foto"], o=o)


def _fotos_estaticos():
    from rotas.compras import FOTOS_DIR
    return FOTOS_DIR


def _caminho_da_nota(nome_arquivo):
    from rotas.compras import _caminho_da_nota as fn
    return fn(nome_arquivo)


# Toda chave que ESTA suíte gera. É a única forma honesta de limpar
# `operacao_idempotencia`: o banco descartável é compartilhado com as outras
# suítes do runner, e "tudo que estiver lá" apagaria a prova alheia.
CHAVES_USADAS: set[str] = set()


def chave():
    """Chave de tentativa gerada ANTES do envio — nunca um id criado depois.

    Registrada em `CHAVES_USADAS` para a limpeza final apagar só o próprio
    rastro (TASK_012 §5/§6).
    """
    k = secrets.token_hex(16)
    CHAVES_USADAS.add(k)
    return k


def escalar(sql, params=(), padrao=0):
    with conectar() as conn:
        # psycopg2 não aceita params=(): sem parâmetro é None.
        r = conn.execute(sql, params if params else None).fetchone()
        if not r:
            return padrao
        v = list(r.values())[0]
        return padrao if v is None else v


def conn_uma(sql, params=()):
    """A linha inteira (dict) ou None — para provar estado coluna a coluna."""
    with conectar() as conn:
        r = conn.execute(sql, params if params else None).fetchone()
        return dict(r) if r else None


def medida(mapas):
    """mapas: {nome: (sql, params)} → dict de contagens."""
    return {nome: escalar(sql, params) for nome, (sql, params) in mapas.items()}


def delta(antes, depois):
    return {k: depois[k] - antes[k] for k in antes}


# ── Fixtures (tudo prefixado; nada de fora é tocado) ────────────────────────

F = {}


def montar_fixtures():
    with conectar() as conn:
        cx = conn.execute(
            "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
            "VALUES (%s, 'suíte TASK_012', %s, 1) RETURNING id",
            (f"{PREFIXO} Lanchonete", True)).fetchone()["id"]
        prod = conn.execute(
            "INSERT INTO produtos (nome, categoria, preco_venda, quantidade_estoque, "
            "ativo, caixa_id) VALUES (%s, 'suíte', '12.50', 500, 1, %s) RETURNING id",
            (f"{PREFIXO} Café", cx)).fetchone()["id"]
        trab = conn.execute(
            "INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, "
            "fiado_credito) VALUES (%s, 1, 500, 0) RETURNING id",
            (f"{PREFIXO} Devedor",)).fetchone()["id"]
        cid_especie = conta_id_por_tipo(conn, "especie")
        cid_pix = conta_id_por_tipo(conn, "pix")
        conn.execute(
            "UPDATE contas_financeiras SET ativo = 1 WHERE id IN (%s, %s)",
            (cid_especie, cid_pix))
        livro = conn.execute(
            "INSERT INTO livros (titulo, autor, quantidade, preco_venda) "
            "VALUES (%s, 'suíte', 10, '20.00') RETURNING id",
            (f"{PREFIXO} Livro",)).fetchone()["id"]
    F.update(dict(cx=cx, prod=prod, trab=trab, livro=livro,
                  especie=cid_especie, pix=cid_pix))

    # sessão de caixa NOVA pela rota real (versão 'eventos')
    st, _, _ = POST("/caixa/abrir", {"caixa_id": cx, "saldo_inicial": "0"})
    F["sessao"] = escalar(
        "SELECT id FROM caixa_movimentos WHERE caixa_id=%s AND status='aberto' "
        "ORDER BY id DESC LIMIT 1", (cx,))
    assert st == 303 and F["sessao"], "sessão eventos não abriu"
    F["versao_sessao"] = escalar(
        "SELECT versao FROM caixa_movimentos WHERE id=%s", (F["sessao"],))


def nova_sessao():
    """Sessão 'eventos' nova do caixa fixture (fecha a anterior primeiro)."""
    with conectar() as conn:
        conn.execute("UPDATE caixa_movimentos SET status='fechado', saldo_final=0 "
                     "WHERE caixa_id=%s AND status='aberto'", (F["cx"],))
    POST("/caixa/abrir", {"caixa_id": F["cx"], "saldo_inicial": "0"})
    return escalar("SELECT id FROM caixa_movimentos WHERE caixa_id=%s "
                   "AND status='aberto' ORDER BY id DESC LIMIT 1", (F["cx"],))


def venda_fiado(valor="40.00", paga=False):
    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
                     troco, status, atendente_id, fiado_trabalhador_id,
                     fiado_pago, fiado_credito_usado, doacao_valor, criada_em)
               VALUES (%s, '2026-01-10', %s, 'fiado', 0, 'concluida', %s, %s,
                       %s, 0, 0, '2026-01-10') RETURNING id""",
            (F["cx"], Decimal(valor), ADMIN_ID, F["trab"], paga))
        return cur.fetchone()["id"]


def pendencia_mensalidade(valor="50.00", compet="2026-03"):
    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao,
                trabalhador_id, status)
               VALUES ('entrada', 'mensalidade', %s, %s, %s, %s, 'pendente')
               RETURNING id""",
            (Decimal(valor), f"{compet}-10", f"{PREFIXO} mensalidade", F["trab"]))
        return cur.fetchone()["id"]


# ── Famílias de operação ────────────────────────────────────────────────────

def mk_pdv(k, **mud):
    d = {"caixa_id": F["cx"], "caixa_movimento_id": F["sessao"],
         "forma_pagamento": "especie", "valor_recebido": "25.00",
         "itens_json": json.dumps([{"produto_id": F["prod"], "quantidade": 2}]),
         "trabalhador_id": 0, "imprimir": "1", "doacao_valor": "0",
         "idempotencia": k}
    d.update(mud)
    return d


def mk_doacao(k, **mud):
    d = {"forma": "especie", "valor": "10.00",
         "descricao": f"{PREFIXO} doação", "sessao_id": F["sessao"],
         "conta_pix_id": 0, "idempotencia": k}
    d.update(mud)
    return d


def mk_sangria(k, **mud):
    d = {"caixa_movimento_id": F["sessao"], "valor": "20.00",
         "motivo": f"{PREFIXO} fornecedor", "recebedor_nome": "Teste",
         "conta_destino_id": F["pix"], "idempotencia": k}
    d.update(mud)
    return d


def mk_transferencia(k, **mud):
    d = {"conta_origem": F["especie"], "conta_destino": F["pix"],
         "valor": "100.00", "descricao": f"{PREFIXO} transferência",
         "idempotencia": k}
    d.update(mud)
    return d


def mk_nova(k, **mud):
    d = {"tipo": "entrada", "categoria": "outros", "valor": "7.77",
         "data_movimentacao": "2026-04-04", "descricao": f"{PREFIXO} avulso",
         "trabalhador_id": "", "pessoa_id": "", "status": "pago",
         "pix_copiadecola": "", "conta_id": str(F["especie"]), "idempotencia": k}
    d.update(mud)
    return d


FAMILIAS = []


def familia(nome, path, mk, medir, efeito, mutar=None, segunda_duplica=True):
    FAMILIAS.append(dict(nome=nome, path=path, mk=mk, medir=medir, efeito=efeito,
                         mutar=mutar or (lambda d: {**d, "valor": "3.33"}),
                         segunda_duplica=segunda_duplica))


def _m_pdv():
    return medida({
        "vendas": ("SELECT count(*) FROM vendas_pdv WHERE caixa_movimento_id=%s",
                   (F["sessao"],)),
        "itens": ("SELECT count(*) FROM vendas_pdv_itens i JOIN vendas_pdv v "
                  "ON v.id=i.venda_id WHERE v.caixa_movimento_id=%s", (F["sessao"],)),
        "eventos": ("SELECT count(*) FROM caixa_sessao_eventos WHERE sessao_id=%s",
                    (F["sessao"],)),
        "estoque": ("SELECT quantidade_estoque FROM produtos WHERE id=%s",
                    (F["prod"],)),
    })










def _mk_sangria_unicos(k, **mud):
    """Cada tentativa de sangria usa um motivo próprio (efeito mensurável)."""
    d = mk_sangria(k, **mud)
    if "motivo" not in mud:
        d["motivo"] = f"{PREFIXO} fornecedor {k[:8]}"
    return d


def _mk_transf_unicos(k, **mud):
    d = mk_transferencia(k, **mud)
    if "descricao" not in mud:
        d["descricao"] = f"{PREFIXO} transferência {k[:8]}"
    return d


def _mk_nova_unica(k, **mud):
    d = mk_nova(k, **mud)
    if "descricao" not in mud:
        d["descricao"] = f"{PREFIXO} avulso {k[:8]}"
    return d


def _m_sangria_unica():
    return medida({
        "sangrias": ("SELECT count(DISTINCT s.id) FROM caixa_sangrias s "
                     "JOIN financeiro_movimentacoes f ON f.sangria_id=s.id "
                     "WHERE s.caixa_movimento_id=%s", (F["sessao"],)),
        "fin": ("SELECT count(DISTINCT f.sangria_id) FROM financeiro_movimentacoes f "
                "WHERE f.sangria_id IN (SELECT id FROM caixa_sangrias "
                "WHERE caixa_movimento_id=%s)", (F["sessao"],)),
    })


def _m_transf_unica():
    return medida({
        "fin": ("SELECT count(*) FROM financeiro_movimentacoes "
                "WHERE categoria='transferencia' AND descricao LIKE %s",
                (f"%{PREFIXO} transferência%",)),
    })


def _m_nova_unica():
    return medida({
        "fin": ("SELECT count(*) FROM financeiro_movimentacoes "
                "WHERE descricao LIKE %s", (f"{PREFIXO} avulso%",)),
    })


# ── Provas por família ──────────────────────────────────────────────────────

def provar_familia(esp):
    nome, path, mk, medir, efeito = (esp["nome"], esp["path"], esp["mk"],
                                     esp["medir"], esp["efeito"])
    mutar = esp.get("mutar") or (lambda d: {**d, "valor": "3.33"})
    segunda_duplica = esp.get("segunda_duplica", True)
    enviar = esp.get("enviar") or envia_form

    antes = medir()
    k = chave()
    dados = mk(k)
    st1, _, loc1 = enviar(path, dict(dados))
    depois = medir()
    d1 = delta(antes, depois)
    verificar(f"{nome}: primeira tentativa produz exatamente um efeito",
              st1 == 303 and d1 == efeito, f"status={st1} delta={d1} esperado={efeito}")
    verificar(f"{nome}: primeira tentativa fica registrada como concluída",
              escalar("SELECT count(*) FROM operacao_idempotencia WHERE chave=%s "
                      "AND status='concluida'", (k,)) == 1, "")

    # 2. reenvio byte a byte da MESMA tentativa
    st2, _, loc2 = enviar(path, dict(dados))
    d2 = delta(depois, medir())
    verificar(f"{nome}: reenvio não produz efeito novo", d2 == {x: 0 for x in efeito},
              f"delta={d2}")
    verificar(f"{nome}: reenvio devolve o mesmo destino",
              st2 == 303 and loc2 == loc1 and bool(loc1),
              f"loc1={loc1} loc2={loc2}")

    resultados = []
    trava = threading.Lock()

    def disparar(dados_enviar):
        o = _nova_opener()
        req_login(o)
        st, _, loc = enviar(path, dict(dados_enviar), o=o)
        with trava:
            resultados.append((st, loc))

    def correr_duas(dados_enviar):
        resultados.clear()
        ts = [threading.Thread(target=disparar, args=(dados_enviar,))
              for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        return list(resultados)

    # 3. concorrência: sobre a MESMA tentativa já concluída, duas requisições
    #    simultâneas não podem produzir um segundo efeito
    base3 = medir()
    feitas3 = correr_duas(dados)
    verificar(f"{nome}: as duas requisições concorrentes de fato responderam",
              len(feitas3) == 2 and all(st == 303 for st, _ in feitas3),
              f"respostas={feitas3}")
    verificar(f"{nome}: duas requisições simultâneas de uma tentativa concluída "
              f"não duplicam",
              medir() == base3, f"antes={base3} depois={medir()}")
    verificar(f"{nome}: a concorrência sobre tentativa concluída devolve o "
              f"mesmo destino",
              all(loc == loc1 for _, loc in feitas3), f"respostas={feitas3}")

    # 3b. concorrência: chave nova, duas requisições simultâneas = um efeito
    #     (ou nenhum, quando o documento já não aceita a operação)
    k3 = chave()
    dados3 = mk(k3)
    antes3b = medir()
    feitas3b = correr_duas(dados3)
    depois3b = medir()
    d3 = delta(antes3b, depois3b)
    verificar(f"{nome}: as duas requisições com a chave nova responderam",
              len(feitas3b) == 2 and all(st == 303 for st, _ in feitas3b),
              f"respostas={feitas3b}")
    verificar(f"{nome}: duas requisições simultâneas com a mesma chave nova "
              f"produzem no máximo um efeito",
              d3 in (efeito, {x: 0 for x in efeito}), f"delta={d3}")
    linhas_chave = escalar(
        "SELECT count(*) FROM operacao_idempotencia WHERE chave=%s", (k3,))
    verificar(f"{nome}: a chave concorrente aparece no máximo uma vez no banco",
              linhas_chave <= 1, f"linhas={linhas_chave}")
    if d3 == {x: 0 for x in efeito}:
        verificar(f"{nome}: sem efeito, nenhuma tentativa foi consumida",
                  linhas_chave == 0, f"linhas={linhas_chave}")

    # 4. mesma chave, conteúdo divergente → recusa sem escrita
    antes4 = medir()
    st4, _, loc4 = enviar(path, mutar(dict(dados)))
    d4 = delta(antes4, medir())
    verificar(f"{nome}: mesma chave com conteúdo divergente é recusada sem escrita",
              d4 == {x: 0 for x in efeito} and "tentativa_conflito" in (loc4 or ""),
              f"delta={d4} loc={loc4}")

    # 5. chave nova com conteúdo idêntico → segunda operação legítima (ou recusa
    #    pelo estado do documento; nunca dinheiro novo silencioso)
    k5 = chave()
    antes5 = medir()
    enviar(path, dict(mk(k5)))
    d5 = delta(antes5, medir())
    permitido = [efeito, {x: 0 for x in efeito}]
    verificar(f"{nome}: nova tentativa com conteúdo idêntico é tratada como "
              f"{'segunda operação' if esp.get('segunda_duplica', True) else 'estado já alcançado'}",
              d5 in permitido, f"delta={d5}")

    # 6. POST sem identidade → recusa legível, nenhum efeito
    antes6 = medir()
    sem_chave = dict(dados)
    sem_chave.pop("idempotencia")
    st6, _, loc6 = enviar(path, sem_chave)
    d6 = delta(antes6, medir())
    verificar(f"{nome}: confirmação sem chave é recusada com orientação e sem efeito",
              d6 == {x: 0 for x in efeito} and
              "tentativa_sem_chave" in (loc6 or ""), f"delta={d6} loc={loc6}")


# ── Provas específicas ──────────────────────────────────────────────────────

def prova_revisao_de_preco():
    """A cesta reprificada não conclui venda nem consome a tentativa; a
    confirmação do preço do servidor acontece sem conflito."""
    sess = F["sessao"]
    k = chave()
    dados = {"caixa_id": F["cx"], "caixa_movimento_id": sess,
             "forma_pagamento": "especie", "valor_recebido": "25.00",
             "itens_json": json.dumps([{"produto_id": F["prod"], "quantidade": 2,
                                         "preco_unitario": 1.00, "subtotal": 2.00}]),
             "trabalhador_id": 0, "imprimir": "1", "doacao_valor": "0",
             "idempotencia": k}
    antes = escalar("SELECT count(*) FROM vendas_pdv WHERE caixa_movimento_id=%s", (sess,))
    st, corpo, loc = POST("/caixa/pdv/finalizar", dados)
    depois = escalar("SELECT count(*) FROM vendas_pdv WHERE caixa_movimento_id=%s", (sess,))
    verificar("PDV: divergência de preço não cria venda",
              depois == antes and st == 200, f"status={st}")
    verificar("PDV: a tentativa recusada NÃO é consumida",
              escalar("SELECT count(*) FROM operacao_idempotencia WHERE chave=%s",
                      (k,)) == 0, "")
    verificar("PDV: a tela reprificada devolve chave nova de tentativa",
              'name="idempotencia"' in corpo and
              re.search(r'name="idempotencia" value="([0-9a-f]+)"', corpo) is not None
              and re.search(r'name="idempotencia" value="([0-9a-f]+)"', corpo).group(1) != k,
              "chave não recriada na revisão")
    k2 = re.search(r'name="idempotencia" value="([0-9a-f]+)"', corpo).group(1)
    confirmada = dict(dados, idempotencia=k2,
                      itens_json=json.dumps([{"produto_id": F["prod"],
                                              "quantidade": 2}]))
    st2, _, loc2 = POST("/caixa/pdv/finalizar", confirmada)
    verificar("PDV: confirmar a cesta reprificada conclui a venda sem conflito",
              st2 == 303 and "ok=" in (loc2 or "") and
              escalar("SELECT count(*) FROM vendas_pdv WHERE caixa_movimento_id=%s",
                      (sess,)) == antes + 1, f"status={st2} loc={loc2}")


def prova_ultima_escrita_falha():
    """Falha depois da reserva desfaz a chave: a mesma tentativa pode ser
    reenviada com sucesso (nem chave consumida sem documento, nem documento
    sem proteção)."""
    from servico_idempotencia import confirmar, exigir_reserva
    k = chave()
    corpo = {"descricao": f"{PREFIXO} rollback"}
    antes = escalar("SELECT count(*) FROM financeiro_movimentacoes WHERE descricao=%s",
                    (corpo["descricao"],))
    try:
        with conectar() as conn:
            exigir_reserva(conn, idempotencia=k, operacao="teste_rollback",
                           corpo=corpo, autor_id=ADMIN_ID)
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, descricao, conta_id, status)
                   VALUES ('entrada','outros',1.00,%s,%s,'pago')""",
                (corpo["descricao"], F["especie"]))
            raise RuntimeError("falha simulada na última escrita")
    except RuntimeError:
        pass
    verificar("rollback: nada gravado e a chave não ficou consumida",
              escalar("SELECT count(*) FROM financeiro_movimentacoes WHERE descricao=%s",
                      (corpo["descricao"],)) == antes and
              escalar("SELECT count(*) FROM operacao_idempotencia WHERE chave=%s",
                      (k,)) == 0, "")
    with conectar() as conn:
        chave_ok = exigir_reserva(conn, idempotencia=k, operacao="teste_rollback",
                                  corpo=corpo, autor_id=ADMIN_ID)
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, descricao, conta_id, status)
               VALUES ('entrada','outros',1.00,%s,%s,'pago')""",
            (corpo["descricao"], F["especie"]))
        confirmar(conn, chave=chave_ok, documento="x", destino="/financeiro")
    verificar("rollback: o reenvio da MESMA chave conclui a operação",
              escalar("SELECT count(*) FROM financeiro_movimentacoes WHERE descricao=%s",
                      (corpo["descricao"],)) == antes + 1 and
              escalar("SELECT count(*) FROM operacao_idempotencia WHERE chave=%s "
                      "AND status='concluida'", (k,)) == 1, "")


def prova_concorrencia_na_reserva():
    """Duas transações ABERTAS ao mesmo tempo, mesma chave de tentativa.

    A segunda fica presa no próprio PostgreSQL (token de inserção especulativa
    da unicidade de `chave`) até a primeira resolver. O bloqueio é conferido em
    `pg_stat_activity` — não é `sleep` apostado. Quando a primeira confirma, a
    segunda acorda e recebe a resposta original, sem escrever segundo
    documento.
    """
    import time

    from banco import _ConnCompat, _obter_pool
    from servico_idempotencia import (
        TentativaJaRegistrada, TentativaRecusada, confirmar, exigir_reserva,
    )

    def abrir():
        bruta = _obter_pool().getconn()
        bruta.cursor_factory = RealDictCursor
        return bruta, _ConnCompat(bruta)

    def bloqueadas():
        return escalar(
            """SELECT count(*) FROM pg_stat_activity
               WHERE datname = current_database() AND pid <> pg_backend_pid()
                 AND wait_event_type = 'Lock' AND state = 'active'
                 AND query LIKE '%operacao_idempotencia%'""", params=None)

    desc = f"{PREFIXO} corrida {chave()[:8]}"
    corpo = {"descricao": desc}
    k = chave()
    bruta_a, A = abrir()
    try:
        exigir_reserva(A, idempotencia=k, operacao="teste_corrida",
                       corpo=corpo, autor_id=ADMIN_ID)
        doc_a = A.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, descricao, conta_id, status)
               VALUES ('entrada','outros',1.00,%s,%s,'pago') RETURNING id""",
            (desc, F["especie"])).fetchone()["id"]
        confirmar(A, chave=k, documento=doc_a, destino=f"/financeiro?corrida={doc_a}")
        # A ainda NÃO deu commit: a tentativa está reservada e invisível.

        desfecho = {}

        def correr_B():
            bruta_b, B = abrir()
            try:
                exigir_reserva(B, idempotencia=k, operacao="teste_corrida",
                               corpo=corpo, autor_id=ADMIN_ID)
                B.execute(
                    """INSERT INTO financeiro_movimentacoes
                       (tipo, categoria, valor, descricao, conta_id, status)
                       VALUES ('entrada','outros',1.00,%s,%s,'pago')""",
                    (desc + " B", F["especie"]))
                desfecho["resultado"] = "NOVA"
            except TentativaJaRegistrada as e:
                desfecho["resultado"] = "REPETIDA"
                desfecho["destino"] = e.destino
                desfecho["documento"] = e.documento
            except TentativaRecusada as e:
                desfecho["resultado"] = e.chave_erro
            finally:
                _obter_pool().putconn(bruta_b)

        tb = threading.Thread(target=correr_B)
        tb.start()

        esperado = False
        for _ in range(200):
            if bloqueadas() >= 1:
                esperado = True
                break
            time.sleep(0.05)
        verificar("corrida: a segunda transação ficou de fato aguardando a primeira",
                  esperado, "nenhuma espera de lock observada em pg_stat_activity")

        bruta_a.commit()          # a primeira conclui AGORA, com a segunda presa
        tb.join(60)
        verificar("corrida: a segunda acorda e recebe a resposta original",
                  desfecho.get("resultado") == "REPETIDA"
                  and desfecho.get("destino") == f"/financeiro?corrida={doc_a}"
                  and str(desfecho.get("documento")) == str(doc_a),
                  f"desfecho={desfecho}")
        verificar("corrida: um único documento para a tentativa disputada",
                  escalar("SELECT count(*) FROM financeiro_movimentacoes WHERE descricao=%s",
                          (desc,)) == 1 and
                  escalar("SELECT count(*) FROM financeiro_movimentacoes "
                          "WHERE descricao = %s", (desc + " B",)) == 0, "")
        verificar("corrida: a tentativa disputada tem exatamente uma linha",
                  escalar("SELECT count(*) FROM operacao_idempotencia WHERE chave=%s",
                          (k,)) == 1 and
                  escalar("SELECT status FROM operacao_idempotencia WHERE chave=%s",
                          (k,)) == "concluida", "")
    finally:
        _obter_pool().putconn(bruta_a)


def prova_mensalidades_lote():
    """Duas competências do mesmo trabalhador: o lote baixa exatamente as
    selecionadas; reenvio da mesma tentativa não gera dinheiro novo."""
    sess = F["sessao"]
    m1 = pendencia_mensalidade("50.00", "2026-03")
    m2 = pendencia_mensalidade("20.00", "2026-04")
    m3 = pendencia_mensalidade("10.00", "2026-05")

    def medir():
        return medida({
            "pagas": ("SELECT count(*) FROM financeiro_movimentacoes "
                      "WHERE id IN (%s,%s) AND status='pago'", (m1, m2)),
            "eventos": ("SELECT count(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
                        "AND tipo='mensalidade'", (sess,)),
            "pendente3": ("SELECT count(*) FROM financeiro_movimentacoes "
                          "WHERE id=%s AND status='pendente'", (m3,)),
        })

    esp_efeito = {"pagas": 2, "eventos": 2, "pendente3": 0}
    k = chave()
    dados = {"trabalhador_id": F["trab"], "mov_ids": f"{m1},{m2},",
             "forma_pagamento": "especie", "pix_copiadecola": "",
             "sessao_id": sess, "conta_pix_id": 0, "idempotencia": k}
    antes = medir()
    st, _, loc = POST("/financeiro/mensalidades/receber", dict(dados))
    d1 = delta(antes, medir())
    verificar("mensalidade lote: baixa exatamente as selecionadas (uma por competência)",
              st == 303 and d1 == esp_efeito, f"delta={d1} status={st}")
    depois = medir()
    st3, _, loc3 = POST("/financeiro/mensalidades/receber", dict(dados))
    verificar("mensalidade lote: reenvio da mesma tentativa não muda nada",
              medir() == depois, f"antes={depois} agora={medir()}")
    verificar("mensalidade lote: reenvio devolve o mesmo recibo",
              st3 == 303 and loc3 == loc and "pago=" in (loc3 or ""),
              f"loc={loc3}")
    k_nova = chave()
    POST("/financeiro/mensalidades/receber", dict({**dados, "idempotencia": k_nova}))
    verificar("mensalidade lote: nova tentativa sobre as mesmas pendências "
              "não inventa dinheiro", medir() == depois, f"agora={medir()}")


def prova_fiado_parcial_e_credito():
    """Recebimento de R$ 60 em dívida de R$ 40 → uma entrada de dinheiro,
    crédito de R$ 20 vinculado; reenvio da mesma tentativa não duplica."""
    sess = F["sessao"]
    venda = venda_fiado("40.00")

    def medir():
        return medida({
            "fin": ("SELECT count(*) FROM financeiro_movimentacoes "
                    "WHERE categoria='recebimento_fiado' AND caixa_id=%s", (F["cx"],)),
            "creditos": ("SELECT count(*) FROM fiado_credito_mov WHERE trabalhador_id=%s",
                         (F["trab"],)),
            "pagas": ("SELECT count(*) FROM vendas_pdv WHERE id=%s AND fiado_pago",
                      (venda,)),
            "credito": ("SELECT coalesce(fiado_credito,0) FROM trabalhadores WHERE id=%s",
                        (F["trab"],)),
        })

    efeito = {"fin": 1, "creditos": 1, "pagas": 1, "credito": 20}
    mutar_fiado = lambda d: {**d, "valor": "13.00"}  # noqa: E731
    k = chave()
    dados = {"valor": "60.00", "forma_pagamento": "especie", "sessao_id": sess,
             "conta_pix_id": 0, "idempotencia": k}
    antes = medir()
    st, _, loc = POST(f"/caixa/fiados/{F['trab']}/pagar-divida", dict(dados))
    d1 = delta(antes, medir())
    verificar("fiado: um recebimento com excedente vira crédito, uma única entrada",
              st == 303 and d1 == efeito, f"delta={d1} status={st} loc={loc}")
    depois = medir()
    st2, _, loc2 = POST(f"/caixa/fiados/{F['trab']}/pagar-divida", dict(dados))
    verificar("fiado: reenvio da mesma tentativa não duplica dinheiro nem crédito",
              medir() == depois and loc2 == loc,
              f"depois={depois} agora={medir()} loc={loc2}")
    verificar("fiado: a tentativa única ficou registrada como concluída",
              escalar("SELECT count(*) FROM operacao_idempotencia WHERE chave=%s "
                      "AND status='concluida'", (k,)) == 1, "")
    st3, _, loc3 = POST(f"/caixa/fiados/{F['trab']}/pagar-divida",
                        dict(mutar_fiado(dados)))
    verificar("fiado: mesma chave com outro valor é recusada sem escrita",
              medir() == depois and "tentativa_conflito" in (loc3 or ""),
              f"loc={loc3}")


def prova_credito_adiantado():
    sess = F["sessao"]

    def medir():
        return medida({
            "fin": ("SELECT count(*) FROM financeiro_movimentacoes "
                    "WHERE categoria='credito_anticipado' AND caixa_id=%s", (F["cx"],)),
            "mov": ("SELECT count(*) FROM fiado_credito_mov WHERE trabalhador_id=%s "
                    "AND tipo='credito'", (F["trab"],)),
        })

    esp = dict(
        nome="crédito antecipado",
        path=f"/caixa/fiados/credito/{F['trab']}",
        mk=lambda k, **mud: {"valor": "30.00", "descricao": f"{PREFIXO} adiantado {k[:8]}",
                             "forma_pagamento": "especie", "sessao_id": sess,
                             "conta_pix_id": 0, "idempotencia": k, **mud},
        medir=lambda: medida({
            "fin": ("SELECT count(*) FROM financeiro_movimentacoes "
                    "WHERE categoria='credito_anticipado' "
                    "AND descricao LIKE %s", (f"{PREFIXO} adiantado%",)),
            "mov": ("SELECT count(*) FROM fiado_credito_mov "
                    "WHERE trabalhador_id=%s AND tipo='credito' "
                    "AND descricao LIKE %s",
                    (F["trab"], f"{PREFIXO} adiantado%")),
        }),
        efeito={"fin": 1, "mov": 1},
    )
    return esp


def prova_quitar_venda_fiada():
    sess = F["sessao"]
    venda = venda_fiado("25.00")

    def medir():
        return medida({
            "fin": ("SELECT count(*) FROM financeiro_movimentacoes "
                    "WHERE descricao=%s", (f"Recebimento fiado — venda #{venda}",)),
            "paga": ("SELECT count(*) FROM vendas_pdv WHERE id=%s AND fiado_pago",
                     (venda,)),
        })

    esp = dict(
        nome="quitar venda de fiado",
        path=f"/caixa/fiados/{venda}/pagar",
        mk=lambda k, **mud: {"forma_pagamento": "especie", "sessao_id": sess,
                             "conta_pix_id": 0, "idempotencia": k, **mud},
        medir=medir,
        efeito={"fin": 1, "paga": 1},
        mutar=lambda d: {**d, "forma_pagamento": "pix",
                         "conta_pix_id": str(F["pix"])},
        segunda_duplica=False,
    )
    return esp


def prova_compra_com_nota():
    """Entrada de dinheiro via multipart (despesa com foto da nota fiscal).

    A nota integra o conteúdo da tentativa: trocar o arquivo na mesma chave é
    conteúdo divergente e precisa ser recusado sem escrita.
    """
    foto = (b"\xff\xd8\xff" + b"T12-nota-original") * 4

    def medir():
        return medida({
            "fin": ("SELECT count(*) FROM financeiro_movimentacoes "
                    "WHERE categoria='compra' AND descricao LIKE %s",
                    (f"{PREFIXO} compra%",)),
            "anexada": ("SELECT count(*) FROM financeiro_movimentacoes "
                        "WHERE categoria='compra' AND descricao LIKE %s "
                        "AND nota_fiscal_foto IS NOT NULL",
                        (f"{PREFIXO} compra%",)),
        })

    return dict(
        nome="compra com nota (multipart)",
        path="/financeiro/compras",
        enviar=envia_multipart,
        mk=lambda k, **mud: {"valor": "18.40",
                             "descricao": f"{PREFIXO} compra {k[:8]}",
                             "forma_pagamento": "especie",
                             "idempotencia": k, "_foto": ("nota.jpg", foto), **mud},
        medir=medir,
        efeito={"fin": 1, "anexada": 1},
        mutar=lambda d: {**d, "_foto": ("nota.jpg", b"\xff\xd8\xff" + b"T12-outra-nota")},
    )


def prova_nota_fora_da_transacao():
    """TASK_012 §6 (FIN-09): o arquivo da nota não vive dentro da transação.

    ANTES, a foto era gravada em static/fotos/ com o `mov_id` do INSERT, ainda
    DENTRO do `with conectar()`: commit que falhasse depois disso deixava o
    arquivo no disco sem nenhuma linha apontando para ele — órfão que ninguém
    encontra. AGORA o conteúdo vai para um temporário ao lado do destino, a linha
    é confirmada e só então ele é promovido por renomeação atômica.

    As falhas são injetadas NO PROCESSO DO SERVIDOR (é ele que executa a rota —
    um monkeypatch no processo da suíte não alcançaria a requisição HTTP):

    - `preparar` falha → nada confirmado, nada em static/fotos/, nenhum órfão;
    - commit recusado por um TRIGGER do banco descartável (falha real, no meio
      da transação, depois de o temporário já existir) → rollback: o lançamento
      não existe e o temporário é removido;
    - `promover` falha depois do commit → a compra é cancelada (linha
      preservada, nada "pago" sem nota) e nenhum arquivo sobra.

    O (1) prova o caminho bom com os dois lados do fato: a linha e o arquivo.
    """
    from rotas import compras as mod

    def fotos() -> set:
        return set(os.listdir(_fotos_estaticos()))

    def enviar(descricao, valor, k, falha=None):
        campos = {"valor": valor, "descricao": descricao,
                  "forma_pagamento": "especie", "idempotencia": k}
        return POST_multipart("/financeiro/compras", campos,
                              ("nota.jpg", b"\xff\xd8\xff" + b"T12-fin09"),
                              cabecalhos=({"X-Tentativa-Falha-Disco": falha}
                                          if falha else None))

    # A injeção precisa estar habilitada NO PROCESSO DO SERVIDOR (o runner a
    # liga). Sem ela, os casos 2 e 4 não teriam como falhar de propósito — e a
    # prova diria "passou" sem exercitar nada.
    if os.environ.get("SHAMBALA_FALHA_DISCO_HABILITADA") != "1":
        verificar("FIN-09: injeção de falha de disco habilitada no app de teste "
                  "(runner)", False,
                  "rode por tests/rodar_testes.sh: sem a injeção os casos de "
                  "falha não são exercitados")
        return

    # ── 1) sucesso: linha e arquivo, os dois vivos ──────────────────────────
    antes = fotos()
    k = chave()
    desc = f"{PREFIXO} compra fin09 {k[:8]}"
    st, _, _ = enviar(desc, "12.00", k)
    nome = escalar("SELECT nota_fiscal_foto FROM financeiro_movimentacoes "
                   "WHERE descricao = %s", (desc,), None)
    novos = fotos() - antes
    verificar("FIN-09 sucesso: a compra fica com a nota registrada e o arquivo existe",
              st == 303 and bool(nome) and novos == {nome}
              and os.path.exists(_caminho_da_nota(nome)),
              f"status {st} nome={nome} novos={sorted(novos)}")

    # ── 2) falha ANTES do commit (no preparo): nada em disco, nada no banco ─
    antes2 = fotos()
    k2 = chave()
    desc2 = f"{PREFIXO} compra fin09 preparo {k2[:8]}"
    st2, _, _ = enviar(desc2, "13.00", k2, falha="preparar")
    gravadas2 = escalar("SELECT count(*) FROM financeiro_movimentacoes "
                        "WHERE descricao = %s", (desc2,))
    verificar("FIN-09 preparo falhando: nenhum arquivo (nem temporário) em static/fotos/",
              st2 == 500 and sorted(fotos() - antes2) == [],
              f"status {st2} sobras={sorted(fotos() - antes2)}")
    verificar("FIN-09 preparo falhando: o lançamento não é gravado e a chave não "
              "fica consumida com documento",
              gravadas2 == 0
              and escalar("SELECT count(*) FROM operacao_idempotencia WHERE chave=%s",
                          (k2,)) == 0, f"linhas={gravadas2}")

    # ── 3) commit recusado por um TRIGGER: rollback com temporário em disco ──
    antes3 = fotos()
    k3 = chave()
    desc3 = f"{PREFIXO} compra fin09 rollback {k3[:8]}"
    with conectar() as conn:
        conn.execute(
            """CREATE OR REPLACE FUNCTION t12_fin09_recusa() RETURNS trigger AS $$
               BEGIN
                   IF NEW.descricao LIKE %s THEN
                       RAISE EXCEPTION 'recusa injetada FIN-09 (commit recusado)';
                   END IF;
                   RETURN NEW;
               END $$ LANGUAGE plpgsql""", (f"{PREFIXO} compra fin09 rollback%",))
        conn.execute(
            """CREATE TRIGGER t12_fin09_recusa BEFORE INSERT
               ON financeiro_movimentacoes FOR EACH ROW
               EXECUTE FUNCTION t12_fin09_recusa()""")
    try:
        st3, _, _ = enviar(desc3, "14.00", k3)
    finally:
        with conectar() as conn:
            conn.execute("DROP TRIGGER IF EXISTS t12_fin09_recusa "
                         "ON financeiro_movimentacoes")
            conn.execute("DROP FUNCTION IF EXISTS t12_fin09_recusa()")
    gravadas3 = escalar("SELECT count(*) FROM financeiro_movimentacoes "
                        "WHERE descricao = %s", (desc3,))
    verificar("FIN-09 rollback: transação recusada (o temporário já estava em "
              "disco) não deixa arquivo em static/fotos/ nem temporário",
              st3 == 500 and sorted(fotos() - antes3) == [],
              f"status {st3} sobras={sorted(fotos() - antes3)}")
    verificar("FIN-09 rollback: nenhum lançamento e nenhuma chave consumida "
              "com documento",
              gravadas3 == 0
              and escalar("SELECT count(*) FROM operacao_idempotencia WHERE chave=%s",
                          (k3,)) == 0, f"linhas={gravadas3}")

    # ── 4) promoção falha DEPOIS do commit: cancelada, sem órfão ────────────
    antes4 = fotos()
    k4 = chave()
    desc4 = f"{PREFIXO} compra fin09 promocao {k4[:8]}"
    st4, _, _ = enviar(desc4, "15.00", k4, falha="promover")
    linha4 = conn_uma("SELECT status, nota_fiscal_foto FROM financeiro_movimentacoes "
                      "WHERE descricao = %s", (desc4,))
    verificar("FIN-09 promoção falhando após o commit: a compra não fica 'paga' "
              "sem a nota — é cancelada, com a linha preservada para auditoria",
              st4 == 500 and linha4 is not None and linha4["status"] == "cancelado",
              f"status {st4} linha={linha4}")
    verificar("FIN-09 promoção falhando após o commit: nenhum arquivo (nem "
              "temporário) fica no disco",
              sorted(fotos() - antes4) == [], f"sobras={sorted(fotos() - antes4)}")

    # ── limpeza do rastro desta prova (só o que ela criou) ──────────────────
    if nome:
        try:
            os.remove(_caminho_da_nota(nome))
        except OSError:
            pass
    with conectar() as conn:
        conn.execute("DELETE FROM financeiro_movimentacoes WHERE descricao LIKE %s",
                     (f"{PREFIXO} compra fin09%",))
        conn.execute("DELETE FROM operacao_idempotencia WHERE chave = ANY(%s)",
                     (sorted(CHAVES_USADAS),))


def prova_venda_de_livro():
    """Entrada de dinheiro pela biblioteca: estoque + venda + lançamento."""
    def medir():
        return medida({
            "estoque": ("SELECT quantidade FROM livros WHERE id=%s", (F["livro"],)),
            "vendas": ("SELECT count(*) FROM vendas_livros WHERE livro_id=%s",
                       (F["livro"],)),
            "fin": ("SELECT count(*) FROM financeiro_movimentacoes "
                    "WHERE descricao LIKE %s", (f"Venda: {PREFIXO}%",)),
        })

    return dict(
        nome="venda de livro",
        path=f"/biblioteca/livro/{F['livro']}/vender",
        mk=lambda k, **mud: {"quantidade": "1", "pessoa_id": "",
                             "idempotencia": k, **mud},
        medir=medir,
        efeito={"estoque": -1, "vendas": 1, "fin": 1},
        mutar=lambda d: {**d, "quantidade": "2"},
    )


def prova_autenticacao_e_pendentes():
    """A chave não é credencial: anônimo sem login não escreve."""
    antes = escalar("SELECT count(*) FROM financeiro_movimentacoes WHERE descricao=%s",
                    (f"{PREFIXO} anonimo",))
    o_anon = _nova_opener()
    st, _, loc = POST("/financeiro/nova", {
        "tipo": "entrada", "categoria": "outros", "valor": "5.00",
        "data_movimentacao": "2026-04-05", "descricao": f"{PREFIXO} anonimo",
        "trabalhador_id": "", "pessoa_id": "", "status": "pago",
        "pix_copiadecola": "", "conta_id": str(F["especie"]),
        "idempotencia": chave()}, o=o_anon)
    depois = escalar("SELECT count(*) FROM financeiro_movimentacoes WHERE descricao=%s",
                     (f"{PREFIXO} anonimo",))
    verificar("chave não substitui autenticação: anônimo não escreve",
              depois == antes and (st in (303, 307)) and "/login" in (loc or ""),
              f"status={st} loc={loc}")


def req_login(o):
    POST("/login", {"nome_usuario": "admin", "senha": "teste123"}, o=o)


def _m_unica_doacao():
    return medida({
        "fin": ("SELECT count(*) FROM financeiro_movimentacoes WHERE descricao LIKE %s",
                (f"{PREFIXO} doação%",)),
    })


def main():
    global OP, OP_ANON, ADMIN_ID
    _conferir_banco_do_servidor()
    preparar_banco()
    _garantir_admin()
    with conectar() as conn:
        ADMIN_ID = conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()["id"]
    OP = _nova_opener()
    req_login(OP)
    OP_ANON = OP

    montar_fixtures()
    verificar("fixtures: sessão de caixa na versão 'eventos' (regra atual)",
              F["versao_sessao"] == "eventos", F["versao_sessao"])

    sess = F["sessao"]

    # sangria/transferência/lançamento avulso: descrição única por tentativa
    familia("PDV venda à vista", "/caixa/pdv/finalizar", mk_pdv, _m_pdv,
            {"vendas": 1, "itens": 1, "eventos": 1, "estoque": -2},
            mutar=lambda d: {**d, "itens_json": json.dumps(
                [{"produto_id": F["prod"], "quantidade": 1}])})
    familia("doação avulsa", "/caixa/doacao",
            lambda k, **mud: {**mk_doacao(k), "descricao": f"{PREFIXO} doação {k[:8]}",
                              **mud},
            _m_unica_doacao, {"fin": 1},
            mutar=lambda d: {**d, "valor": "3.00"})
    familia("sangria", "/caixa/sangria", _mk_sangria_unicos, _m_sangria_unica,
            {"sangrias": 1, "fin": 1},
            mutar=lambda d: {**d, "valor": "5.00"})
    familia("transferência entre contas", "/financeiro/transferir",
            _mk_transf_unicos, _m_transf_unica, {"fin": 2},
            mutar=lambda d: {**d, "valor": "5.00"})
    familia("lançamento avulso", "/financeiro/nova", _mk_nova_unica,
            _m_nova_unica, {"fin": 1}, mutar=lambda d: {**d, "valor": "3.30"})

    for esp in FAMILIAS:
        try:
            provar_familia(esp)
        except Exception as e:
            FALHAS.append(f"{esp['nome']} — exceção {type(e).__name__}: {e}")
            print(f"  FALHOU {esp['nome']} (exceção)\n        {e}")
            traceback.print_exc()

    for extra in (prova_revisao_de_preco, prova_ultima_escrita_falha,
                  prova_concorrencia_na_reserva,
                  prova_mensalidades_lote, prova_fiado_parcial_e_credito,
                  prova_autenticacao_e_pendentes,
                  prova_nota_fora_da_transacao):
        try:
            extra()
        except Exception as e:
            FALHAS.append(f"{extra.__name__} — exceção {type(e).__name__}: {e}")
            print(f"  FALHOU {extra.__name__}\n        {e}")
            traceback.print_exc()

    fotos_antes = set(os.listdir(_fotos_estaticos()))
    for esp in (prova_credito_adiantado(), prova_quitar_venda_fiada(),
                prova_compra_com_nota(), prova_venda_de_livro()):
        try:
            provar_familia(esp)
        except Exception as e:
            FALHAS.append(f"{esp['nome']} — exceção {type(e).__name__}: {e}")
            print(f"  FALHOU {esp['nome']}\n        {e}")
            traceback.print_exc()

    # A compra grava a foto da nota em disco; o banco é descartável, os
    # arquivos desta suíte também devem ser. O recorte é o que esta suíte criou
    # na janela da corrida (`fotos_antes`), e cada arquivo é removido pelo
    # caminho validado do módulo — nunca por nome vindo do banco ou de fora.
    from rotas.compras import _caminho_da_nota
    for novo in set(os.listdir(_fotos_estaticos())) - fotos_antes:
        try:
            os.remove(_caminho_da_nota(novo))
        except (OSError, ValueError):
            pass

    total = len(APROVADAS) + len(FALHAS)
    print(f"\n{len(APROVADAS)}/{total} verificações aprovadas (TASK_012 §2 — idempotência)")
    if FALHAS:
        print("\nFalhas:")
        for f in FALHAS:
            print(f"  - {f}")
    limpar_banco()
    return 0 if not FALHAS else 1


if __name__ == "__main__":
    sys.exit(main())
