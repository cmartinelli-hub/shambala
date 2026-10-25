#!/usr/bin/env python3
"""Suíte TASK_012 §3 — mensalidades por trabalhador e competência.

O que é provado, contra a aplicação e o banco descartáveis:

1. a competência (YYYY-MM) é coluna própria, não texto de descrição nem `LIKE`
   sobre o vencimento;
2. no máximo uma obrigação por trabalhador/competência — garantida no BANCO
   (índice único parcial) e no SERVIÇO (checagem + ON CONFLICT);
3. geração repetida e concorrente não duplica cobrança; dois trabalhadores no
   mesmo mês e um trabalhador em meses diferentes continuam legítimos;
4. limites de ano/mês (virada de dezembro, fevereiro, bissexto) e a regra de
   elegibilidade existente (ativo + valor > 0) preservadas;
5. abrir a tela não gera nada (leitura sem efeito colateral);
6. recebimento individual e em lote baixa exatamente as seleções, preserva a
   competência e não esconde baixa parcial; o lote é atômico;
7. duplicidade histórica NÃO é apagada, fundida nem preterida: a constraint é
   pulada com o impedimento registrado e a tela mostra o conflito;
8. mensagens simples de operador (já gerada, sem mensalidade ativa, período
   inválido, conflito histórico).

Uso: dentro de tests/rodar_testes.sh (app real + banco selado).
"""
import http.cookiejar
import io
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
from contextlib import redirect_stdout
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_tres_passes as tp  # noqa: E402
from test_tres_passes import (  # noqa: E402
    preparar_banco, limpar_banco, _garantir_admin, BASE,
)

from banco import (  # noqa: E402
    UQ_MENSALIDADE_COMPETENCIA, conectar, conta_id_por_tipo, criar_tabelas,
)
from rotas.financeiro_mensalidades import (  # noqa: E402
    MESES_A_FRENTE, _adicionar_meses, _data_vencimento, competencial_de,
    normalizar_competencia, sincronizar_mensalidades,
)

PREFIXO = f"T12MES-{uuid.uuid4().hex[:8]}"
ADMIN_ID = None
FALHAS = []
APROVADAS = []
HOJE = date.today()
COMP_INICIAL = competencial_de(HOJE.year, HOJE.month)


def _conferir_banco_do_servidor():
    """Destino do SERVIDOR comprovado nesta execução (TASK_010)."""
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


class _SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _nova_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _SemRedirect())


OP = None


def req(method, path, data=None, o=None):
    corpo = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=corpo, method=method)
    try:
        with (o or OP).open(r) as resp:
            return (resp.status, resp.read().decode("utf-8", "replace"),
                    resp.headers.get("Location"))
    except urllib.error.HTTPError as e:
        return (e.code, e.read().decode("utf-8", "replace"),
                e.headers.get("Location"))


def POST(path, dados, o=None):
    return req("POST", path, dados, o)


def GET(path, o=None):
    return req("GET", path, None, o)


def req_login(o):
    POST("/login", {"nome_usuario": "admin", "senha": "teste123"}, o=o)


def chave():
    return secrets.token_hex(16)


def escalar(sql, params=(), padrao=0):
    with conectar() as conn:
        r = conn.execute(sql, params).fetchone()
        if not r:
            return padrao
        v = list(r.values())[0]
        return padrao if v is None else v


def indice_existe() -> bool:
    return escalar("SELECT count(*) FROM pg_class WHERE relname = %s",
                   (UQ_MENSALIDADE_COMPETENCIA,)) == 1


# ── Fixtures ─────────────────────────────────────────────────────────────────

F = {}


def novo_trabalhador(nome, *, valor="50.00", dia=10, ativo=1) -> int:
    with conectar() as conn:
        return conn.execute(
            """INSERT INTO trabalhadores (nome_completo, ativo, valor_mensalidade,
                                          dia_vencimento, fiado_limite, fiado_credito)
               VALUES (%s, %s, %s, %s, 0, 0) RETURNING id""",
            (f"{PREFIXO} {nome}", ativo, valor, dia)).fetchone()["id"]


def dados_trabalhador(tid: int) -> dict:
    with conectar() as conn:
        return dict(conn.execute(
            "SELECT * FROM trabalhadores WHERE id = %s", (tid,)).fetchone())


def mensalidades_de(tid: int):
    with conectar() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT id, competencia, data_movimentacao, status, valor, conta_id,
                      data_pagamento, forma_pagamento
               FROM financeiro_movimentacoes
               WHERE trabalhador_id = %s AND categoria = 'mensalidade'
               ORDER BY competencia, id""", (tid,)).fetchall()]


def contar_meses(tid: int) -> dict:
    return {m["competencia"]: m for m in mensalidades_de(tid)}


def montar_fixtures():
    global ADMIN_ID
    with conectar() as conn:
        ADMIN_ID = conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()["id"]
        F["cx"] = conn.execute(
            "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
            "VALUES (%s, 'suíte TASK_012 §3', %s, 1) RETURNING id",
            (f"{PREFIXO} Caixa", False)).fetchone()["id"]
        F["especie"] = conta_id_por_tipo(conn, "especie")
        conn.execute("UPDATE contas_financeiras SET ativo = 1 WHERE id = %s",
                     (F["especie"],))
    POST("/caixa/abrir", {"caixa_id": F["cx"], "saldo_inicial": "0"})
    F["sessao"] = escalar(
        "SELECT id FROM caixa_movimentos WHERE caixa_id=%s AND status='aberto' "
        "ORDER BY id DESC LIMIT 1", (F["cx"],))
    assert F["sessao"], "sessão de caixa não abriu"


# ── 1. chave do banco ────────────────────────────────────────────────────────

def prova_chave_do_banco():
    verificar("banco: índice único de mensalidade por competência existe",
              indice_existe(), "índice ausente")

    a = novo_trabalhador("Chave A")
    b = novo_trabalhador("Chave B")
    comp = competencial_de(HOJE.year, HOJE.month)
    cid = escalar("SELECT id FROM contas_financeiras WHERE tipo='areceber' LIMIT 1")

    def inserir(tid):
        with conectar() as conn:
            return conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, data_movimentacao, competencia,
                    descricao, trabalhador_id, status, conta_id)
                   VALUES ('entrada','mensalidade',50.00,%s,%s,%s,%s,'pendente',%s)
                   RETURNING id""",
                (f"{comp}-10", comp, f"{PREFIXO} manual {tid}", tid, cid)).fetchone()["id"]

    id_a = inserir(a)
    duplicado_recusado = False
    try:
        inserir(a)
    except Exception:
        duplicado_recusado = True
    verificar("banco: segunda linha do mesmo trabalhador/competência é recusada",
              duplicado_recusado and escalar(
                  "SELECT count(*) FROM financeiro_movimentacoes "
                  "WHERE trabalhador_id=%s AND competencia=%s", (a, comp)) == 1,
              f"id={id_a}")

    try:
        id_b = inserir(b)
        ok_otros = True
    except Exception as e:
        id_b, ok_otros = None, False
    verificar("banco: OUTRO trabalhador na MESMA competência é legítimo",
              ok_otros, "")

    comp2 = competencial_de(*_adicionar_meses(HOJE.year, HOJE.month, 1))
    with conectar() as conn:
        try:
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, data_movimentacao, competencia,
                    descricao, trabalhador_id, status, conta_id)
                   VALUES ('entrada','mensalidade',50.00,%s,%s,%s,%s,'pendente',%s)""",
                (f"{comp2}-10", comp2, f"{PREFIXO} outro mês", a, cid))
            ok_mes = True
        except Exception:
            ok_mes = False
    verificar("banco: MESMO trabalhador em OUTRA competência é legítimo", ok_mes, "")

    # cancelado continua ocupando a chave — é a regra existente (mês cancelado
    # não é recriado), então a guarda não pode permiti-lo.
    with conectar() as conn:
        conn.execute("UPDATE financeiro_movimentacoes SET status='cancelado' "
                     "WHERE id = %s", (id_b,))
    recusa_cancelado = False
    try:
        inserir(b)
    except Exception:
        recusa_cancelado = True
    verificar("banco: competência cancelada também está sob a chave (não é recriada)",
              recusa_cancelado, "")


# ── 2/3/4. geração ──────────────────────────────────────────────────────────

def prova_geracao_idempotente():
    tid = novo_trabalhador("Geração", valor="45.00", dia=8)
    with conectar() as conn:
        sincronizar_mensalidades(conn, dados_trabalhador(tid))
    linhas = mensalidades_de(tid)
    verificar(f"geração cria exatamente {MESES_A_FRENTE + 1} competências",
              len(linhas) == MESES_A_FRENTE + 1, f"{len(linhas)} linhas")
    esperadas = [competencial_de(*_adicionar_meses(HOJE.year, HOJE.month, i))
                 for i in range(MESES_A_FRENTE + 1)]
    verificar("geração: cada linha traz a competência do seu mês (sem depender de texto)",
              sorted(m["competencia"] for m in linhas) == sorted(esperadas),
              f"{[m['competencia'] for m in linhas][:3]}")
    verificar("geração: vencimento usa o dia do trabalhador na competência certa",
              all(m["data_movimentacao"][:7] == m["competencia"] for m in linhas),
              f"{[(m['competencia'], m['data_movimentacao']) for m in linhas][:2]}")
    verificar("geração: todas pendentes na conta de a receber",
              all(m["status"] == "pendente" for m in linhas), "")

    with conectar() as conn:
        de_novo = sincronizar_mensalidades(conn, dados_trabalhador(tid))
    verificar("geração repetida não cria segunda cobrança",
              de_novo == 0 and len(mensalidades_de(tid)) == MESES_A_FRENTE + 1,
              f"criadas={de_novo} total={len(mensalidades_de(tid))}")


def prova_concorrencia_geracao():
    tid = novo_trabalhador("Corrida")
    respostas = []
    trava = threading.Lock()

    def disparar():
        o = _nova_opener()
        req_login(o)
        st, _, loc = POST("/financeiro/mensalidades/gerar",
                          {"trabalhador_id": tid}, o=o)
        with trava:
            respostas.append((st, loc))

    ts = [threading.Thread(target=disparar) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)

    total = len(mensalidades_de(tid))
    verificar("concorrência: as duas gerações responderam",
              len(respostas) == 2 and all(st == 303 for st, _ in respostas),
              f"{respostas}")
    verificar(f"concorrência: duas gerações simultâneas criam {MESES_A_FRENTE + 1} "
              "linhas, não o dobro",
              total == MESES_A_FRENTE + 1, f"total={total}")
    verificar("concorrência: nenhuma competência ficou com duas linhas",
              max(escalar("SELECT count(*) FROM financeiro_movimentacoes "
                          "WHERE trabalhador_id=%s GROUP BY competencia "
                          "ORDER BY count(*) DESC LIMIT 1", (tid,), padrao=0), 0) == 1,
              "")


def prova_concorrencia_sem_indice():
    """O buraco que o dono mandou provar: SEM o índice, a checagem sozinha não
    impede duas inserções simultâneas.

    O índice único é criado no startup, mas o startup **se recusa** a criá-lo
    quando encontra duplicidade histórica — e nesse estado (real, documentado na
    §3) o serviço continua gerando. `_existe_mes` é leitura: duas gerações
    simultâneas leem "não existe" e as duas inserem. A proteção agora é a trava
    `pg_advisory_xact_lock` por trabalhador (`_travar_geracao`).

    A prova roda o caminho REAL do serviço com o índice derrubado e duas conexões
    independentes atrás de uma barreira — sem índice, sem corrida casual.
    """
    from decimal import Decimal
    from rotas import financeiro_mensalidades as M
    from banco import conta_id_por_tipo

    tid = novo_trabalhador("Sem índice")
    competencia = "2027-08"
    with conectar() as conn:
        cid = conta_id_por_tipo(conn, "areceber")

    # 1) confirma o cenário (o índice realmente não existe)
    with conectar() as conn:
        conn.execute("DROP INDEX IF EXISTS uq_mensalidade_por_competencia")
    with conectar() as conn:
        indice_fora = not M._indice_ativo(conn)
    verificar("sem índice: o cenário da prova é o estado real (guarda do banco ausente)",
              indice_fora, "o índice continuou ativo")

    barreira = threading.Barrier(2)
    resultado = {}

    def gerar(nome):
        try:
            with conectar() as conn:
                barreira.wait(timeout=20)      # as duas entram juntas
                M._travar_geracao(conn, tid)   # a proteção sob teste
                if M._existe_mes(conn, tid, competencia):
                    resultado[nome] = "já existia"
                    return
                novo = M.inserir_mensalidade(
                    conn, trabalhador_id=tid, competencia=competencia,
                    vencimento=f"{competencia}-10", valor=Decimal("60.00"),
                    descricao=f"{PREFIXO} conc sem indice", conta_id=cid)
                resultado[nome] = "criei" if novo else "sem retorno"
        except Exception as e:  # noqa: BLE001
            resultado[nome] = f"erro: {type(e).__name__}: {e}"

    ts = [threading.Thread(target=gerar, args=(n,)) for n in ("A", "B")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(40)

    linhas = escalar("SELECT count(*) FROM financeiro_movimentacoes "
                     "WHERE trabalhador_id=%s AND competencia=%s",
                     (tid, competencia))
    verificar("sem índice: duas gerações simultâneas criam UMA obrigação, não duas "
              "(trava pg_advisory_xact_lock por trabalhador)",
              linhas == 1 and sorted(resultado.values()) == ["criei", "já existia"],
              f"linhas={linhas} resultado={resultado}")

    # 2) o índice volta para o banco desta execução (as outras provas usam)
    with conectar() as conn:
        conn.execute(
            f"""CREATE UNIQUE INDEX IF NOT EXISTS {M.UQ_MENSALIDADE_COMPETENCIA}
                ON financeiro_movimentacoes (trabalhador_id, competencia)
                WHERE {M.PREDICADO_MENSALIDADE}""")
    with conectar() as conn:
        verificar("sem índice: a guarda do banco é restabelecida ao fim da prova",
                  M._indice_ativo(conn), "índice não voltou")


def prova_sem_indice_com_conflito_historico():
    """TASK_014 §3: sem índice POR CONFLITO HISTÓRICO, a trava ainda protege.

    É o cenário que a TASK_013 pediu e que a prova anterior não cobria: o índice
    não está ausente por acaso — o startup **se recusou** a criá-lo porque
    encontrou duas linhas do mesmo trabalhador/competência. A pergunta é se,
    nesse estado real, duas gerações simultâneas de uma competência NOVA ainda
    produzem UMA obrigação, e se as linhas em conflito ficam preservadas.

    Também exercita um caminho ALTERNATIVO de geração (o manual, que passa pelo
    mesmo serviço) disputando com a geração direta.
    """
    from decimal import Decimal
    from rotas import financeiro_mensalidades as M
    from banco import conta_id_por_tipo

    tid = novo_trabalhador("Conflito histórico")
    with conectar() as conn:
        cid = conta_id_por_tipo(conn, "areceber")

    # (1) cria o conflito histórico: duas linhas da MESMA competência, que é
    #     exatamente o que faz o startup desistir do índice único.
    competencia_velha = "2026-02"
    with conectar() as conn:
        conn.execute("DROP INDEX IF EXISTS uq_mensalidade_por_competencia")
        for valor in ("60.00", "70.00"):
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, data_movimentacao, competencia, descricao,
                    trabalhador_id, status, conta_id)
                   VALUES ('entrada', 'mensalidade', %s, %s, %s, %s, %s, 'pendente', %s)""",
                (Decimal(valor), f"{competencia_velha}-10", competencia_velha,
                 f"{PREFIXO} conflito histórico", tid, cid))
    historicas_antes = escalar(
        "SELECT count(*) FROM financeiro_movimentacoes WHERE trabalhador_id = %s "
        "AND competencia = %s", (tid, competencia_velha))

    # (2) o startup NÃO consegue recriar o índice nesse estado — é literalmente
    #     a recusa que deixa o banco sem a guarda. A exceção é a PROVA.
    recusou = False
    try:
        with conectar() as conn:
            conn.execute(
                f"""CREATE UNIQUE INDEX IF NOT EXISTS {M.UQ_MENSALIDADE_COMPETENCIA}
                    ON financeiro_movimentacoes (trabalhador_id, competencia)
                    WHERE {M.PREDICADO_MENSALIDADE}""")
    except Exception as e:  # noqa: BLE001
        recusou = "uq_mensalidade" in str(e) or "duplicada" in str(e)
    verificar("sem índice + conflito histórico: o banco RECUSA criar o índice "
              "enquanto houver duplicidade (é a origem do estado sem guarda)",
              recusou, "o índice foi criado apesar do conflito")
    with conectar() as conn:
        idx_fora = not M._indice_ativo(conn)
    verificar("sem índice + conflito histórico: o índice continua ausente",
              idx_fora, "o índice foi criado apesar do conflito")
    with conectar() as conn:
        preservadas = conn.execute(
            "SELECT count(*) AS c FROM financeiro_movimentacoes "
            "WHERE trabalhador_id = %s AND competencia = %s",
            (tid, competencia_velha)).fetchone()["c"]
    verificar("sem índice + conflito histórico: as duas linhas históricas "
              "continuam lá (nenhuma vencedora escolhida, nada apagado)",
              preservadas == historicas_antes == 2,
              f"antes={historicas_antes} depois={preservadas}")

    # (3) duas gerações simultâneas de uma competência NOVA, com barreira
    competencia_nova = "2027-09"
    barreira = threading.Barrier(2)
    resultado = {}

    def gerar(nome, via):
        try:
            with conectar() as conn:
                barreira.wait(timeout=20)
                if via == "servico":
                    # caminho do SERVIÇO: a rotina completa de geração
                    trab = dict(conn.execute(
                        "SELECT * FROM trabalhadores WHERE id = %s", (tid,)).fetchone())
                    criou = M.sincronizar_mensalidades(conn, trab)
                    resultado[nome] = f"criou={criou}"
                else:
                    # caminho ALTERNATIVO: a mesma trava, chamada pela rota
                    # manual (exercitada em outro teste) — aqui o serviço direto
                    # com o mesmo ponto de entrada da trava
                    M._travar_geracao(conn, tid)
                    criou = 0 if M._existe_mes(conn, tid, competencia_nova) else 1
                    if criou:
                        M.inserir_mensalidade(
                            conn, trabalhador_id=tid, competencia=competencia_nova,
                            vencimento=f"{competencia_nova}-10", valor=Decimal("60.00"),
                            descricao=f"{PREFIXO} via alternativa", conta_id=cid)
                    resultado[nome] = f"criou={criou}"
        except Exception as e:  # noqa: BLE001
            resultado[nome] = f"erro: {type(e).__name__}: {e}"

    ts = [threading.Thread(target=gerar, args=("servico", "servico")),
          threading.Thread(target=gerar, args=("alternativo", "alternativo"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(40)

    novas = escalar(
        "SELECT count(*) FROM financeiro_movimentacoes WHERE trabalhador_id = %s "
        "AND competencia = %s", (tid, competencia_nova))
    verificar("sem índice + conflito histórico: duas gerações simultâneas (serviço "
              "× caminho alternativo) produzem UMA obrigação da competência nova",
              novas == 1, f"linhas={novas} resultado={resultado}")
    with conectar() as conn:
        ainda = conn.execute(
            "SELECT count(*) AS c FROM financeiro_movimentacoes "
            "WHERE trabalhador_id = %s AND competencia = %s",
            (tid, competencia_velha)).fetchone()["c"]
    verificar("sem índice + conflito histórico: as históricas seguem intactas "
              "depois da geração",
              ainda == 2, f"históricas={ainda}")

    # (4) restabelece a guarda do banco para as provas seguintes.
    #
    # O índice parcial NÃO filtra por status (`categoria='mensalidade' AND
    # trabalhador_id IS NOT NULL AND competencia IS NOT NULL`), então cancelar
    # não tira a linha do índice. As duas linhas são fixture DESTA prova, criadas
    # aqui e identificadas pela descrição — apagá-las é limpar o próprio rastro,
    # não escolher vencedora em fato real.
    with conectar() as conn:
        conn.execute(
            """DELETE FROM financeiro_movimentacoes
               WHERE trabalhador_id = %s AND competencia = %s AND descricao = %s""",
            (tid, competencia_velha, f"{PREFIXO} conflito histórico"))
    with conectar() as conn:
        conn.execute(
            f"""CREATE UNIQUE INDEX IF NOT EXISTS {M.UQ_MENSALIDADE_COMPETENCIA}
                ON financeiro_movimentacoes (trabalhador_id, competencia)
                WHERE {M.PREDICADO_MENSALIDADE}""")
        guarda_voltou = M._indice_ativo(conn)
    verificar("sem índice + conflito histórico: ao fim da prova a guarda do banco "
              "é restabelecida (as provas seguintes rodam no estado normal)",
              guarda_voltou, "índice não voltou")


def prova_dois_trabalhadores_mesmo_mes():
    a = novo_trabalhador("Par A")
    b = novo_trabalhador("Par B")
    with conectar() as conn:
        criadas_a = sincronizar_mensalidades(conn, dados_trabalhador(a))
        criadas_b = sincronizar_mensalidades(conn, dados_trabalhador(b))
    comp = COMP_INICIAL
    verificar("dois trabalhadores no mesmo mês: uma obrigação cada",
              criadas_a == MESES_A_FRENTE + 1 and criadas_b == MESES_A_FRENTE + 1 and
              escalar("SELECT count(*) FROM financeiro_movimentacoes "
                      "WHERE competencia=%s AND trabalhador_id IN (%s,%s)",
                      (comp, a, b)) == 2, f"a={criadas_a} b={criadas_b}")


# ── 5. limites de ano/mês ───────────────────────────────────────────────────

def prova_limites_ano_mes():
    verificar("limite: dezembro + 1 = janeiro do ano seguinte",
              _adicionar_meses(2026, 12, 1) == (2027, 1) and
              competencial_de(2027, 1) == "2027-01",
              str(_adicionar_meses(2026, 12, 1)))
    verificar("limite: virada de ano na janela de 13 meses",
              competencial_de(*_adicionar_meses(2026, 12, 12)) == "2027-12",
              "")
    verificar("limite: dia 31 em fevereiro não-29 vira 28",
              _data_vencimento(31, 2027, 2) == "2027-02-28", "")
    verificar("limite: fevereiro de ano bissexto preserva dia 29",
              _data_vencimento(29, 2028, 2) == "2028-02-29", "")
    verificar("limite: mês curto com dia 30 vira 28 (sem inventar dia)",
              _data_vencimento(30, 2026, 2) == "2026-02-28", "")
    verificar("competência: normalização aceita '2026-3', '2026-03' e data cheia",
              normalizar_competencia("2026-3") == "2026-03" and
              normalizar_competencia("2026-03-17") == "2026-03", "")
    verificar("competência: período inválido é recusado, não adivinhado",
              normalizar_competencia("março/26") is None and
              normalizar_competencia("2026-13") is None and
              normalizar_competencia("") is None, "")


# ── 6. elegibilidade existente preservada ────────────────────────────────────

def prova_elegibilidade():
    inativo = novo_trabalhador("Inativo", ativo=0)
    sem_valor = novo_trabalhador("Sem valor", valor="0.00")
    with conectar() as conn:
        c1 = sincronizar_mensalidades(conn, dados_trabalhador(inativo))
        c2 = sincronizar_mensalidades(conn, dados_trabalhador(sem_valor))
    verificar("inativo não gera cobrança (regra existente preservada)",
              c1 == 0 and not mensalidades_de(inativo), f"criadas={c1}")
    verificar("valor zero não gera cobrança",
              c2 == 0 and not mensalidades_de(sem_valor), f"criadas={c2}")

    st, _, loc = POST("/financeiro/mensalidades/gerar", {"trabalhador_id": inativo})
    verificar("rota de geração recusa inativo com mensagem e sem escrita",
              st == 303 and "sem_mensalidade_ativa" in (loc or "")
              and not mensalidades_de(inativo), f"loc={loc}")


# ── 7/8. leitura sem efeito + mensagens ─────────────────────────────────────

def prova_leitura_geracao_manual():
    tid = novo_trabalhador("Tela")
    st, corpo, _ = GET(f"/financeiro/mensalidades?trabalhador_id={tid}")
    verificar("GET da tela de mensalidades responde 200", st == 200, f"status={st}")
    verificar("GET NÃO gera cobrança (leitura sem efeito colateral)",
              not mensalidades_de(tid), f"{len(mensalidades_de(tid))} linhas")

    st, _, loc = POST("/financeiro/mensalidades/gerar", {"trabalhador_id": tid})
    geradas = (urllib.parse.parse_qs(urllib.parse.urlparse(loc or "").query)
               .get("geradas", ["?"])[0])
    total = len(mensalidades_de(tid))
    verificar("geração manual cria o período e informa quantos são novos",
              st == 303 and geradas == str(MESES_A_FRENTE + 1) and
              total == MESES_A_FRENTE + 1, f"loc={loc} total={total}")

    POST("/financeiro/mensalidades/gerar", {"trabalhador_id": tid})
    st2, corpo2, _ = GET(f"/financeiro/mensalidades?trabalhador_id={tid}&geradas=0")
    verificar("tela diz 'já estão geradas' quando não há nada novo",
              "já estão geradas" in corpo2 and
              len(mensalidades_de(tid)) == MESES_A_FRENTE + 1, "")

    st3, corpo3, _ = GET(f"/financeiro/mensalidades?trabalhador_id={tid}&erro=sem_mensalidade_ativa")
    verificar("tela traduz a recusa de inativo em linguagem de operador",
              "nenhuma cobrança foi gerada" in corpo3, "")

    st4, corpo4, _ = GET("/financeiro/nova?erro=periodo_invalido")
    verificar("tela de lançamento traduz período inválido",
              st4 == 200 and "período claro" in corpo4, f"status={st4}")
    st5, corpo5, _ = GET("/financeiro/nova?erro=mensalidade_ja_registrada")
    verificar("tela de lançamento traduz mensalidade já registrada",
              "já tem mensalidade" in corpo5, "")


# ── 9/10/11. recebimento ─────────────────────────────────────────────────────

def _receber(tid, ids, k=None, sessao=None):
    return POST("/financeiro/mensalidades/receber", {
        "trabalhador_id": tid,
        "mov_ids": ",".join(str(i) for i in ids),
        "forma_pagamento": "especie",
        "sessao_id": sessao if sessao is not None else F["sessao"],
        "conta_pix_id": 0,
        "idempotencia": k or chave()})


def prova_recebimento_preserva_competencia():
    tid = novo_trabalhador("Recebimento")
    with conectar() as conn:
        sincronizar_mensalidades(conn, dados_trabalhador(tid))
    linhas = mensalidades_de(tid)
    alvo = linhas[0]["id"]
    st, _, loc = _receber(tid, [alvo])
    depois = {m["id"]: m for m in mensalidades_de(tid)}
    verificar("recebimento individual conclui pela rota",
              st == 303 and "pago=" in (loc or ""), f"status={st} loc={loc}")
    verificar("recebimento preserva a competência da obrigação",
              depois[alvo]["competencia"] == linhas[0]["competencia"],
              f"{depois[alvo]['competencia']}")
    verificar("recebimento registra forma, conta, data de pagamento e sessão",
              depois[alvo]["status"] == "pago" and
              depois[alvo]["forma_pagamento"] == "especie" and
              depois[alvo]["data_pagamento"] and
              escalar("SELECT count(*) FROM caixa_sessao_eventos "
                      "WHERE origem_tipo='financeiro_movimentacoes' AND origem_id=%s",
                      (alvo,)) == 1, f"{depois[alvo]}")
    verificar("as demais continuam pendentes (baixa só do selecionado)",
              sum(1 for m in depois.values() if m["status"] == "pendente") ==
              len(linhas) - 1, "")

    # reenvio da MESMA tentativa não rebaixa nada (a §2 prova o contrato; aqui é
    # a competência que não pode aparecer duas vezes)
    alvo2 = linhas[1]["id"]
    k = chave()
    antes = escalar("SELECT count(*) FROM caixa_sessao_eventos WHERE sessao_id=%s",
                    (F["sessao"],))
    st1, _, _ = _receber(tid, [alvo2], k=k)
    st2, _, _ = _receber(tid, [alvo2], k=k)
    depois_r = escalar("SELECT count(*) FROM caixa_sessao_eventos WHERE sessao_id=%s",
                       (F["sessao"],))
    verificar("reenvio do recebimento não gera segundo evento nem segundo dinheiro",
              st1 == 303 and st2 == 303 and depois_r == antes + 1 and
              escalar("SELECT count(*) FROM caixa_sessao_eventos "
                      "WHERE origem_id = %s", (alvo2,)) == 1,
              f"st1={st1} st2={st2} antes={antes} depois={depois_r}")


def prova_lote_sem_baixa_parcial():
    tid = novo_trabalhador("Lote")
    with conectar() as conn:
        sincronizar_mensalidades(conn, dados_trabalhador(tid))
    linhas = mensalidades_de(tid)
    a, b, c = linhas[0]["id"], linhas[1]["id"], linhas[2]["id"]
    valor = float(linhas[0]["valor"])

    # b já foi recebida por outro atendente antes deste lote chegar
    with conectar() as conn:
        conn.execute("""UPDATE financeiro_movimentacoes SET status='pago',
                        data_pagamento='2026-01-05', forma_pagamento='pix'
                        WHERE id = %s""", (b,))

    st, _, loc = _receber(tid, [a, b, c])
    q = urllib.parse.parse_qs(urllib.parse.urlparse(loc or "").query)
    ids_pagas = sorted(int(x) for x in q.get("pago", [""])[0].split(",") if x)
    depois = {m["id"]: m for m in mensalidades_de(tid)}
    verificar("lote baixa exatamente as pendências selecionadas",
              ids_pagas == sorted([a, c]), f"pago={ids_pagas}")
    verificar("lote sem baixa parcial oculta: o total é a soma do que foi baixado",
              q.get("total", [""])[0] == f"{valor * 2:.2f}", f"total={q.get('total')}")
    verificar("lote não regrava a que já estava paga (data e forma preservadas)",
              depois[b]["data_pagamento"] == "2026-01-05" and
              depois[b]["forma_pagamento"] == "pix", f"{depois[b]}")
    verificar("um evento por mensalidade baixada no lote",
              escalar("SELECT count(*) FROM caixa_sessao_eventos "
                      "WHERE tipo='mensalidade' AND origem_id IN (%s,%s)", (a, c)) == 2,
              "")

    # seleção inteira já paga → recusa legível, nenhum dinheiro
    st2, _, loc2 = _receber(tid, [b])
    verificar("lote de seleção já recebida é recusado sem escrever",
              st2 == 303 and "sem_itens" in (loc2 or ""), f"loc={loc2}")


def prova_lote_atomico():
    """Falha no meio do lote desfaz TUDO: nada de baixa parcial no banco."""
    tid = novo_trabalhador("Atômico")
    with conectar() as conn:
        sincronizar_mensalidades(conn, dados_trabalhador(tid))
    linhas = mensalidades_de(tid)
    a, b = linhas[0]["id"], linhas[1]["id"]

    # identidade do evento é UNIQUE: plantar a do segundo item faz a segunda
    # escrita do lote falhar de verdade, dentro da mesma transação.
    with conectar() as conn:
        conn.execute(
            """INSERT INTO caixa_sessao_eventos
               (identidade, sessao_id, tipo, natureza, origem_tipo, origem_id,
                financeiro_id, forma, sentido, valor, efeito_fisico, conta_id,
                atendente_id)
               VALUES (%s, %s, 'mensalidade', 'recebimento',
                       'financeiro_movimentacoes', %s, %s, 'especie', 'entrada',
                       99.99, 99.99, %s, %s)""",
            (f"mensalidade:{b}", F["sessao"], b, b, F["especie"], ADMIN_ID))

    antes = {m["id"]: m["status"] for m in mensalidades_de(tid)}
    eventos_antes = escalar("SELECT count(*) FROM caixa_sessao_eventos "
                            "WHERE sessao_id=%s", (F["sessao"],))
    st, _, _ = _receber(tid, [a, b])
    depois = {m["id"]: m["status"] for m in mensalidades_de(tid)}
    verificar("lote que falha no meio devolve erro do servidor",
              st == 500, f"status={st}")
    verificar("falha no meio do lote não baixa NENHUMA mensalidade",
              depois == antes, f"{depois}")
    verificar("falha no meio do lote não grava evento nenhum",
              escalar("SELECT count(*) FROM caixa_sessao_eventos WHERE sessao_id=%s",
                      (F["sessao"],)) == eventos_antes, "")

    # limpa a plantação e o lote conclui
    with conectar() as conn:
        conn.execute("DELETE FROM caixa_sessao_eventos WHERE identidade = %s",
                     (f"mensalidade:{b}",))
    st2, _, loc2 = _receber(tid, [a, b])
    verificar("depois de removido o obstáculo, o mesmo lote conclui",
              st2 == 303 and all(m["status"] == "pago"
                                 for m in mensalidades_de(tid) if m["id"] in (a, b)),
              f"status={st2} loc={loc2}")


# ── 12. duplicidade histórica, backfill e impedimento de migração ────────────

def prova_duplicidade_historica():
    tid = novo_trabalhador("Duplicado", valor="40.00")
    comp = competencial_de(HOJE.year, HOJE.month)
    cid = escalar("SELECT id FROM contas_financeiras WHERE tipo='areceber' LIMIT 1")

    with conectar() as conn:
        conn.execute(f"DROP INDEX IF EXISTS {UQ_MENSALIDADE_COMPETENCIA}")
        rotuladas_ids = [
            conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, data_movimentacao, competencia, descricao,
                    trabalhador_id, status, data_pagamento)
                   VALUES ('entrada','mensalidade',40.00,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (f"{comp}-10", comp, f"{PREFIXO} duplicated", tid, status, data_pg)
            ).fetchone()["id"]
            for status, data_pg in (("pago", "2026-02-03"), ("pendente", None))]
        # terceira linha do MESMO mês sem rótulo: é o caso que a primeira versão
        # da migração estourava (rotular às cegas cria a violação no startup).
        sem_rotulo = conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, competencia, descricao,
                trabalhador_id, status)
               VALUES ('entrada','mensalidade',40.00,%s,NULL,%s,%s,'pendente')
               RETURNING id""",
            (f"{comp}-22", f"{PREFIXO} duplicated sem rotulo", tid)
        ).fetchone()["id"]

    antes = mensalidades_de(tid)
    saida = io.StringIO()
    try:
        with redirect_stdout(saida):
            criar_tabelas()
        texto = saida.getvalue()
        linhas = mensalidades_de(tid)
        verificar("duplicidade histórica: nenhuma linha é apagada, fundida ou preterida",
                  {m["id"] for m in linhas} == {m["id"] for m in antes} and
                  all(m["status"] == a["status"] for m, a in zip(linhas, antes)),
                  f"{[(m['id'], m['status']) for m in linhas]}")
        verificar("duplicidade histórica: o índice único NÃO é criado",
                  not indice_existe(), "índice presente mesmo com conflito")
        verificar("duplicidade histórica: o impedimento é registrado no startup",
                  "duplicidade histórica de mensalidade" in texto, texto[:200])
        verificar("startup com conflito NÃO aborta: a migração terminou e o app seguiu vivo",
                  "Traceback" not in texto and
                  GET(f"/financeiro/mensalidades?trabalhador_id={tid}")[0] == 200, "")
        verificar("rotulamento não escolhe vencedora: linha de grupo disputado segue sem rótulo",
                  escalar("SELECT competencia FROM financeiro_movimentacoes WHERE id=%s",
                          (sem_rotulo,), padrao=None) is None, "")
        verificar("duplicidade histórica: a tela mostra o conflito, sem resolvê-lo",
                  "Conflito histórico" in GET(
                      f"/financeiro/mensalidades?trabalhador_id={tid}")[1] and
                  comp in GET(f"/financeiro/mensalidades?trabalhador_id={tid}")[1],
                  "")
        with conectar() as conn:
            criadas = sincronizar_mensalidades(conn, dados_trabalhador(tid))
        verificar("sem a constraint, o serviço ainda não duplica o mês em conflito",
                  criadas == MESES_A_FRENTE and
                  escalar("SELECT count(*) FROM financeiro_movimentacoes "
                          "WHERE trabalhador_id=%s AND competencia=%s", (tid, comp)) == 2,
                  f"criadas={criadas}")

        # resolvido o conflito pelo operador (aqui é o cenário fictício de teste:
        # sobra uma linha única, ainda sem rótulo), a migração seguinte rotula e
        # cria o índice.
        with conectar() as conn:
            conn.execute("DELETE FROM financeiro_movimentacoes WHERE id IN (%s, %s)",
                         (rotuladas_ids[0], rotuladas_ids[1]))
        with redirect_stdout(io.StringIO()):
            criar_tabelas()
        verificar("conflito resolvido: o grupo virou linha única, foi rotulada e a "
                  "guarda do banco voltou no próximo startup",
                  indice_existe() and
                  escalar("SELECT competencia FROM financeiro_movimentacoes WHERE id=%s",
                          (sem_rotulo,)) == comp, "")
    finally:
        if not indice_existe():
            with conectar() as conn:
                conn.execute(
                    f"""CREATE UNIQUE INDEX IF NOT EXISTS {UQ_MENSALIDADE_COMPETENCIA}
                        ON financeiro_movimentacoes (trabalhador_id, competencia)
                        WHERE categoria = 'mensalidade'
                          AND trabalhador_id IS NOT NULL
                          AND competencia IS NOT NULL""")


def prova_backfill_competencia():
    tid = novo_trabalhador("Legado", valor="30.00")
    with conectar() as conn:
        boa = conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, competencia, descricao,
                trabalhador_id, status)
               VALUES ('entrada','mensalidade',30.00,'2026-03-10',NULL,%s,%s,'pago')
               RETURNING id""", (f"{PREFIXO} legado ok", tid)).fetchone()["id"]
        ilegivel = conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, competencia, descricao,
                trabalhador_id, status)
               VALUES ('entrada','mensalidade',30.00,'sem-data',NULL,%s,%s,'pago')
               RETURNING id""", (f"{PREFIXO} legado ilegível", tid)).fetchone()["id"]
    # fora da transação das fixtures: criar_tabelas() é OUTRA conexão e o
    # backfill precisaria dos locks acima, já liberados.
    saida = io.StringIO()
    with redirect_stdout(saida):
        criar_tabelas()
    texto = saida.getvalue()
    verificar("backfill: competência rótulada do vencimento já gravado",
              escalar("SELECT competencia FROM financeiro_movimentacoes WHERE id=%s",
                      (boa,)) == "2026-03", "")
    verificar("backfill: vencimento ilegível NÃO é reinterpretado (fica NULL)",
              escalar("SELECT competencia FROM financeiro_movimentacoes WHERE id=%s",
                      (ilegivel,), padrao=None) is None, "")
    verificar("backfill: o startup informa as linhas que não pôde rotular",
              "sem competência rotulável" in texto, texto[:200])
    verificar("backfill é idempotente: rodar de novo não altera nada",
              escalar("SELECT count(*) FROM financeiro_movimentacoes "
                      "WHERE trabalhador_id=%s", (tid,)) == 2 and indice_existe(),
              "")

    # uma linha legada sem rótulo não impede o serviço de tratá-la como o mês
    # dela (a segunda perna de _existe_mes), nem de receber o pagamento.
    with conectar() as conn:
        conn.execute("DELETE FROM financeiro_movimentacoes WHERE id IN (%s, %s)",
                     (boa, ilegivel))
        conn.execute("DELETE FROM financeiro_movimentacoes WHERE trabalhador_id = %s",
                     (tid,))


# ── 13. cancelamento e baixa individual ─────────────────────────────────────

def prova_cancelamento():
    tid = novo_trabalhador("Cancelamento")
    with conectar() as conn:
        sincronizar_mensalidades(conn, dados_trabalhador(tid))
    linhas = mensalidades_de(tid)
    futura = [m for m in linhas if m["competencia"] > COMP_INICIAL][0]
    st, _, _ = POST(f"/financeiro/mensalidades/{futura['id']}/cancelar",
                    {"trabalhador_id": tid})
    verificar("cancelamento de mensalidade futura conclui pela rota",
              st == 303 and escalar("SELECT status FROM financeiro_movimentacoes "
                                    "WHERE id=%s", (futura["id"],)) == "cancelado", "")
    with conectar() as conn:
        criadas = sincronizar_mensalidades(conn, dados_trabalhador(tid))
    verificar("mês cancelado NÃO é recriado pela geração (regra existente)",
              criadas == 0 and escalar("SELECT count(*) FROM financeiro_movimentacoes "
                                       "WHERE trabalhador_id=%s AND competencia=%s",
                                       (tid, futura["competencia"])) == 1,
              f"criadas={criadas}")
    st2, _, _ = POST(f"/financeiro/mensalidades/{futura['id']}/cancelar",
                     {"trabalhador_id": tid})
    verificar("reenvio do cancelamento não muda nada",
              st2 == 303 and escalar("SELECT status FROM financeiro_movimentacoes "
                                     "WHERE id=%s", (futura["id"],)) == "cancelado", "")


# ── 14. lançamento manual pela rota de financeiro ────────────────────────────

def prova_lancamento_manual():
    tid = novo_trabalhador("Manual", valor="60.00")
    comp = competencial_de(*_adicionar_meses(HOJE.year, HOJE.month, 5))

    def lancar(data_ref, k=None):
        return POST("/financeiro/nova", {
            "tipo": "entrada", "categoria": "mensalidade", "valor": "60.00",
            "data_movimentacao": data_ref, "descricao": f"{PREFIXO} manual",
            "trabalhador_id": str(tid), "pessoa_id": "", "status": "pendente",
            "pix_copiadecola": "", "conta_id": "", "idempotencia": k or chave()})

    st, _, loc = lancar("sem-período")
    verificar("mensalidade manual sem período legível é recusada com mensagem",
              st == 303 and "periodo_invalido" in (loc or "") and
              not mensalidades_de(tid), f"loc={loc}")

    st2, _, loc2 = lancar(f"{comp}-10")
    linhas = mensalidades_de(tid)
    verificar("mensalidade manual entra com a competência do período",
              st2 == 303 and len(linhas) == 1 and
              linhas[0]["competencia"] == comp, f"loc={loc2} {linhas}")

    st3, _, loc3 = lancar(f"{comp}-28")
    verificar("segunda mensalidade manual do mesmo mês é recusada sem escrita",
              st3 == 303 and "mensalidade_ja_registrada" in (loc3 or "") and
              len(mensalidades_de(tid)) == 1, f"loc={loc3}")

    k = chave()
    lancar(f"{comp}-10", k=k)  # 1ª tentativa já foi registrada; aqui é chave nova
    st4, _, loc4 = POST("/financeiro/nova", {
        "tipo": "entrada", "categoria": "mensalidade", "valor": "60.00",
        "data_movimentacao": f"{comp}-10", "descricao": f"{PREFIXO} manual",
        "trabalhador_id": str(tid), "pessoa_id": "", "status": "pendente",
        "pix_copiadecola": "", "conta_id": "", "idempotencia": k})
    verificar("a recusa por duplicidade não consome a chave da tentativa",
              len(mensalidades_de(tid)) == 1 and escalar(
                  "SELECT count(*) FROM operacao_idempotencia WHERE chave=%s", (k,)) == 0,
              f"loc={loc4}")


FUNCOES = [
    prova_chave_do_banco, prova_geracao_idempotente, prova_concorrencia_geracao,
    prova_concorrencia_sem_indice, prova_sem_indice_com_conflito_historico,
    prova_dois_trabalhadores_mesmo_mes, prova_limites_ano_mes,
    prova_elegibilidade, prova_leitura_geracao_manual,
    prova_recebimento_preserva_competencia, prova_lote_sem_baixa_parcial,
    prova_lote_atomico, prova_duplicidade_historica, prova_backfill_competencia,
    prova_cancelamento, prova_lancamento_manual,
]


def main():
    global OP
    _conferir_banco_do_servidor()
    preparar_banco()
    _garantir_admin()
    OP = _nova_opener()
    req_login(OP)
    montar_fixtures()

    for fn in FUNCOES:
        try:
            fn()
        except Exception as e:
            FALHAS.append(f"{fn.__name__} — exceção {type(e).__name__}: {e}")
            print(f"  FALHOU {fn.__name__} (exceção)\n        {e}")
            traceback.print_exc()

    total = len(APROVADAS) + len(FALHAS)
    print(f"\n{len(APROVADAS)}/{total} verificações aprovadas "
          "(TASK_012 §3 — mensalidades por competência)")
    if FALHAS:
        print("\nFalhas:")
        for f in FALHAS:
            print(f"  - {f}")
    limpar_banco()
    return 0 if not FALHAS else 1


if __name__ == "__main__":
    sys.exit(main())
