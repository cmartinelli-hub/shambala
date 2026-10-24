#!/usr/bin/env python3
"""Suíte TASK_001 — presença de trabalhadores com data própria.

Cobre a validação obrigatória da etapa 1:
- serviço de chegada (data local America/Sao_Paulo, sem exigir dias_trabalho);
- unicidade por trabalhador+data, repetições e concorrência (primeira chegada
  preservada, horário histórico ausente nunca inventado);
- migração do modelo antigo (auditoria, backfill, segunda execução, banco
  novo) em banco DEDICADO descartável — nunca no banco principal de teste;
- fluxo HTTP vigente (check-in sem abertura de dia, permissões, rotas legadas
  removidas) e relatórios (período, dias da semana ISODOW, histórico).

Uso (recomendado): bash tests/rodar_testes.sh
"""
import io
import os
import sys
import threading
import traceback
import contextlib
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

# ── TRAVA DE ISOLAMENTO (antes de importar a aplicação — ordem obrigatória) ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import (  # noqa: E402
    exigir_arquivo_de_configuracao, exigir_ambiente_de_teste, AmbienteInseguro,
)

import test_tres_passes as tp  # noqa: E402
from test_tres_passes import (  # noqa: E402
    Ctx, assert_eq, preparar_banco, limpar_banco,
    _http, _login, _garantir_admin, BASE, _SemRedirect,
)

from banco import conectar, criar_tabelas, fechar_pool  # noqa: E402
import banco  # noqa: E402
from servico_presenca import (  # noqa: E402
    registrar_chegada, registrar_ausencia_explicita,
    TrabalhadorInativo, TrabalhadorInexistente, _agora_sao_paulo,
)

MIG_DB = "shambala_presenca_mig_test"  # dedicado; contém '_test' (trava ok)

RESULTADOS = []


def teste(nome):
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


# ── Fixtures da suíte ─────────────────────────────────────────────────────────

def _limpa_negocio(conn):
    """Limpa presença/dias/trabalhadores sem tocar em atendentes (sessão vivo)."""
    conn.execute(
        "TRUNCATE trabalhador_presenca, trabalhador_dias, dias_trabalho, "
        "trabalhadores RESTART IDENTITY CASCADE"
    )
    conn.commit()


def _trab(ctx, nome="Trabalhador Teste", ativo=1):
    return ctx.conn.execute(
        "INSERT INTO trabalhadores (nome_completo, ativo) VALUES (%s, %s) RETURNING id",
        (nome, ativo),
    ).fetchone()["id"]


def _hoje_sp(conn):
    return conn.execute(
        "SELECT ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date)::text AS d"
    ).fetchone()["d"]


def _sessao_nova(usuario, senha):
    """Opener com cookie jar próprio, logado como `usuario`; não segue redirect."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj), _SemRedirect()
    )
    body = urllib.parse.urlencode({"nome_usuario": usuario, "senha": senha}).encode()
    req = urllib.request.Request(BASE + "/login", data=body, method="POST")
    try:
        opener.open(req)
        raise AssertionError(f"login de {usuario} deveria redirecionar (303)")
    except urllib.error.HTTPError as e:
        if e.code != 303:
            raise AssertionError(f"login de {usuario} falhou (status {e.code})")

    def get(path):
        r = urllib.request.Request(BASE + path, method="GET")
        try:
            with opener.open(r) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def post(path, data=None):
        b = urllib.parse.urlencode(data).encode() if data else b""
        r = urllib.request.Request(BASE + path, data=b, method="POST")
        try:
            with opener.open(r) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), None
        except urllib.error.HTTPError as e:
            loc = e.headers.get("location") if e.code == 303 else None
            return e.code, e.read().decode("utf-8", "replace"), loc

    get.post = post
    return get


def _psql_admin(sql: str):
    import subprocess
    return subprocess.run(
        ["psql", "-h", os.environ.get("PGHOST", "/var/run/postgresql"),
         "-U", os.environ.get("PGUSER", "shambala"), "-d", "postgres",
         "-tAqc", sql],
        capture_output=True, text=True, env={**os.environ},
    )


@contextlib.contextmanager
def _banco_dedicado(nome: str):
    """Aponta o módulo `banco` (pool) para um banco dedicado descartável.

    O nome DEVE conter '_test' (mesmo marcador da trava de isolamento) e não
    pode ser um banco proibido. Restaura a conexão original ao sair.
    """
    if "_test" not in nome or nome in ("shambala", "postgres", "template0", "template1"):
        raise AmbienteInseguro(f"{nome} não é um banco marcadamente de teste")
    original = banco._DB_NAME
    _psql_admin(f'DROP DATABASE IF EXISTS "{nome}"')
    r = _psql_admin(f'CREATE DATABASE "{nome}"')
    if r.returncode != 0:
        raise RuntimeError(f"não foi possível criar {nome}: {r.stderr}")
    fechar_pool()
    banco._DB_NAME = nome
    try:
        yield
    finally:
        fechar_pool()
        banco._DB_NAME = original


DDL_ANTIGO = """
CREATE TABLE trabalhador_presenca (
    id                  SERIAL PRIMARY KEY,
    trabalhador_id      INTEGER NOT NULL REFERENCES trabalhadores(id),
    dia_trabalho_id     INTEGER NOT NULL REFERENCES dias_trabalho(id),
    presente            INTEGER NOT NULL DEFAULT 0,
    hora_chegada        TEXT,
    hora_saida          TEXT,
    UNIQUE(trabalhador_id, dia_trabalho_id)
)
"""


def _voltar_ao_modelo_antigo(conn):
    """Recria trabalhador_presenca no formato pré-TASK_001 (para testar migração)."""
    conn.execute("DROP TABLE trabalhador_presenca")
    conn.execute(DDL_ANTIGO)
    conn.commit()


def _coluna_info(conn, coluna):
    row = conn.execute(
        """SELECT is_nullable AS n FROM information_schema.columns
           WHERE table_name = 'trabalhador_presenca' AND column_name = %s""",
        (coluna,),
    ).fetchone()
    return row["n"] if row else None


def _constraints_unique(conn):
    rows = conn.execute(
        """SELECT c.conname AS nome, array_agg(a.attname ORDER BY k.ord) AS colunas
           FROM pg_constraint c
           JOIN pg_class rel ON rel.oid = c.conrelid
           JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
           JOIN pg_attribute a ON a.attrelid = rel.oid AND a.attnum = k.attnum
           WHERE rel.relname = 'trabalhador_presenca' AND c.contype = 'u'
           GROUP BY c.conname"""
    ).fetchall()
    return {tuple(r["colunas"]) for r in rows}


# ── 1. Serviço de chegada (regras aprovadas) ─────────────────────────────────

@teste("U1 data sem dias_trabalho: registra presença sem criar dia de atendimento")
def u1(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Ativo Sem Dia")
    r = registrar_chegada(ctx.conn, tid)
    ctx.conn.commit()
    assert_eq(r["criado"], True, "primeiro registro é novo")
    n = ctx.conn.execute("SELECT COUNT(*) AS c FROM trabalhador_presenca").fetchone()["c"]
    assert_eq(n, 1, "uma linha de presença")
    d = ctx.conn.execute("SELECT COUNT(*) AS c FROM dias_trabalho").fetchone()["c"]
    assert_eq(d, 0, "nenhum dia de atendimento foi criado")
    link = ctx.conn.execute(
        "SELECT dia_trabalho_id AS l FROM trabalhador_presenca").fetchone()["l"]
    assert_eq(link, None, "presença nova não exige vínculo com dias_trabalho")


@teste("U2 trabalhador inexistente e U3 inativo: recusa sem escrita indevida")
def u2_u3(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    antes = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_presenca").fetchone()["c"]
    try:
        registrar_chegada(ctx.conn, 999999)
        raise AssertionError("deveria lançar TrabalhadorInexistente")
    except TrabalhadorInexistente:
        pass
    inativo = _trab(ctx, "Inativo", ativo=0)
    try:
        registrar_chegada(ctx.conn, inativo)
        raise AssertionError("deveria lançar TrabalhadorInativo")
    except TrabalhadorInativo:
        pass
    ctx.conn.commit()
    depois = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_presenca").fetchone()["c"]
    assert_eq(depois, antes, "nenhuma escrita para inexistente/inativo")


@teste("U4 repetição: um registro, primeira chegada preservada, criado=False")
def u4(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Repetidor")
    r1 = registrar_chegada(ctx.conn, tid)
    ctx.conn.commit()
    # marca a primeira chegada com hora conhecida
    ctx.conn.execute(
        "UPDATE trabalhador_presenca SET hora_chegada = '08:00' WHERE trabalhador_id = %s",
        (tid,))
    ctx.conn.commit()
    r2 = registrar_chegada(ctx.conn, tid)
    ctx.conn.commit()
    assert_eq(r2["criado"], False, "repetição indica presença já registrada")
    n = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_presenca WHERE trabalhador_id = %s",
        (tid,)).fetchone()["c"]
    assert_eq(n, 1, "uma única linha")
    hora = ctx.conn.execute(
        "SELECT hora_chegada AS h FROM trabalhador_presenca WHERE trabalhador_id = %s",
        (tid,)).fetchone()["h"]
    assert_eq(hora, "08:00", "primeira chegada não é substituída")
    assert_eq(r1["criado"], True, "o primeiro foi novo")


@teste("U5 ausência explícita seguida de chegada: vira presente com horário")
def u5(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Ausente que Chegou")
    registrar_ausencia_explicita(ctx.conn, tid)
    ctx.conn.commit()
    r = registrar_chegada(ctx.conn, tid)
    ctx.conn.commit()
    row = ctx.conn.execute(
        """SELECT presente, hora_chegada AS h, COUNT(*) OVER() AS linhas
           FROM trabalhador_presenca WHERE trabalhador_id = %s""",
        (tid,)).fetchone()
    assert_eq(row["linhas"], 1, "uma única linha coerente")
    assert_eq(row["presente"], 1, "convertida para presente")
    assert row["h"], "chegada atual registrada"
    assert_eq(r["presente"], 1, "serviço devolve presente")


@teste("U6 presença histórica sem horário: repetição não fabrica hora")
def u6(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Histórico Sem Hora")
    hoje = _hoje_sp(ctx.conn)
    ctx.conn.execute(
        """INSERT INTO trabalhador_presenca
           (trabalhador_id, data, presente, hora_chegada)
           VALUES (%s, %s::date, 1, NULL)""", (tid, hoje))
    ctx.conn.commit()
    r = registrar_chegada(ctx.conn, tid)
    ctx.conn.commit()
    assert_eq(r["criado"], False, "já registrada")
    h = ctx.conn.execute(
        "SELECT hora_chegada AS h FROM trabalhador_presenca WHERE trabalhador_id = %s",
        (tid,)).fetchone()["h"]
    assert_eq(h, None, "horário histórico desconhecido permanece desconhecido")


@teste("U7 concorrência: N requisições simultâneas geram exatamente 1 registro")
def u7(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Concorrente")
    ctx.conn.commit()
    resultados, erros = [], []

    def worker():
        try:
            with conectar() as conn:
                resultados.append(registrar_chegada(conn, tid)["criado"])
                conn.commit()
        except Exception as e:  # noqa: BLE001
            erros.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert_eq(erros, [], "nenhuma falha sob concorrência")
    n = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_presenca WHERE trabalhador_id = %s",
        (tid,)).fetchone()["c"]
    assert_eq(n, 1, "exatamente um registro")
    assert_eq(sum(1 for r in resultados if r), 1, "exatamente um 'novo'")


@teste("U8 virada UTC/São Paulo: data é a de São Paulo, não do sistema")
def u8(ctx: Ctx):
    # 02:30 UTC = 23:30 de ANTERIOR em São Paulo
    data, hora = _agora_sao_paulo(
        ctx.conn, fonte="TIMESTAMPTZ '2026-10-23 02:30:00+00'")
    assert_eq(data, "2026-10-22", "data local São Paulo antes da virada")
    assert_eq(hora, "23:30", "hora local São Paulo")
    # 03:30 UTC = 00:30 do MESMO dia em São Paulo
    data2, hora2 = _agora_sao_paulo(
        ctx.conn, fonte="TIMESTAMPTZ '2026-10-23 03:30:00+00'")
    assert_eq(data2, "2026-10-23", "data após a virada paulistana")
    assert_eq(hora2, "00:30", "hora da madrugada local")
    # fuso da SESSÃO não muda nada (prova de independência do relógio/fuso)
    ctx.conn.execute("SET TIME ZONE 'Asia/Tokyo'")
    data3, _ = _agora_sao_paulo(
        ctx.conn, fonte="TIMESTAMPTZ '2026-10-23 02:30:00+00'")
    ctx.conn.execute("RESET TIME ZONE")
    assert_eq(data3, "2026-10-22", "sessão em Tóquio continua vendo São Paulo")


# ── 2. Migração do modelo antigo ─────────────────────────────────────────────

@teste("M1 migração: datas derivadas dos dias antigos, fatos preservados, unicidade trocada")
def m1():
    with _banco_dedicado(MIG_DB):
        criar_tabelas()
        with conectar() as conn:
            tid = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo) VALUES ('Migrado',1) RETURNING id"
            ).fetchone()["id"]
            d1 = conn.execute(
                "INSERT INTO dias_trabalho (data, aberto) VALUES ('2026-04-06',1) RETURNING id"
            ).fetchone()["id"]
            d2 = conn.execute(
                "INSERT INTO dias_trabalho (data, aberto) VALUES ('2026-04-08',0) RETURNING id"
            ).fetchone()["id"]
            conn.commit()
            _voltar_ao_modelo_antigo(conn)
            conn.execute(
                """INSERT INTO trabalhador_presenca
                   (trabalhador_id, dia_trabalho_id, presente, hora_chegada, hora_saida)
                   VALUES (%s, %s, 1, '19:51', NULL),
                          (%s, %s, 0, NULL,  NULL)""",
                (tid, d1, tid, d2))
            conn.commit()

            criar_tabelas()  # caminho real: migração dispara no startup

            linhas = conn.execute(
                """SELECT data::text AS d, presente, hora_chegada, dia_trabalho_id
                   FROM trabalhador_presenca WHERE trabalhador_id = %s ORDER BY data""",
                (tid,)).fetchall()
            assert_eq(len(linhas), 2, "mesmos dois fatos após migrar")
            assert_eq(linhas[0]["d"], "2026-04-06", "data derivada do dia antigo")
            assert_eq(linhas[0]["presente"], 1, "presente preservado")
            assert_eq(linhas[0]["hora_chegada"], "19:51", "chegada preservada")
            assert_eq(linhas[1]["d"], "2026-04-08", "segunda data derivada")
            assert_eq(linhas[1]["presente"], 0, "ausência explícita preservada")
            assert_eq(_coluna_info(conn, "dia_trabalho_id"), "YES",
                      "vínculo com dia passou a opcional")
            assert_eq(_coluna_info(conn, "data"), "NO", "data própria virou NOT NULL")
            uniq = _constraints_unique(conn)
            assert any(set(c) == {"trabalhador_id", "data"} for c in uniq), \
                f"unicidade nova (trabalhador_id, data) ausente: {uniq}"
            antigos = [c for c in uniq if "dia_trabalho_id" in c]
            assert_eq(antigos, [], "unicidade antiga removida")


@teste("M2 segunda execução da migração: sem duplicação, perda ou erro")
def m2():
    with _banco_dedicado(MIG_DB):
        criar_tabelas()
        with conectar() as conn:
            tid = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo) VALUES ('Reexec',1) RETURNING id"
            ).fetchone()["id"]
            d1 = conn.execute(
                "INSERT INTO dias_trabalho (data, aberto) VALUES ('2026-05-06',1) RETURNING id"
            ).fetchone()["id"]
            conn.commit()
            _voltar_ao_modelo_antigo(conn)
            conn.execute(
                """INSERT INTO trabalhador_presenca
                   (trabalhador_id, dia_trabalho_id, presente, hora_chegada)
                   VALUES (%s, %s, 1, '20:02')""", (tid, d1))
            conn.commit()

            criar_tabelas()   # 1ª aplicação
            criar_tabelas()   # 2ª aplicação (startup de verdade)

            n = conn.execute(
                "SELECT COUNT(*) AS c FROM trabalhador_presenca").fetchone()["c"]
            assert_eq(n, 1, "segunda execução não duplica nem perde")
            row = conn.execute(
                """SELECT data::text AS d, presente, hora_chegada AS h
                   FROM trabalhador_presenca WHERE trabalhador_id = %s""",
                (tid,)).fetchone()
            assert_eq((row["d"], row["presente"], row["h"]),
                      ("2026-05-06", 1, "20:02"), "fatos intactos")


@teste("M3 dados ambíguos: relata o caso concreto e NÃO altera o schema")
def m3():
    with _banco_dedicado(MIG_DB):
        criar_tabelas()
        with conectar() as conn:
            tid = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo) VALUES ('Ambíguo',1) RETURNING id"
            ).fetchone()["id"]
            d1 = conn.execute(
                "INSERT INTO dias_trabalho (data, aberto) VALUES ('06/04/dez',1) RETURNING id"
            ).fetchone()["id"]
            conn.commit()
            _voltar_ao_modelo_antigo(conn)
            conn.execute(
                """INSERT INTO trabalhador_presenca
                   (trabalhador_id, dia_trabalho_id, presente, hora_chegada)
                   VALUES (%s, %s, 1, '19:00')""", (tid, d1))
            conn.commit()

            saida = io.StringIO()
            with contextlib.redirect_stdout(saida):
                criar_tabelas()
            relatorio = saida.getvalue()
            assert "DADOS AMBIGUOS" in relatorio, f"deveria relatar: {relatorio}"
            assert_eq(_coluna_info(conn, "data"), None,
                      "schema NÃO foi alterado diante de ambiguidade")
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM trabalhador_presenca").fetchone()["c"]
            assert_eq(n, 1, "registro problemático não foi descartado")


@teste("M4 banco novo: schema nasce compatível com o fluxo (sem dia obrigatório)")
def m4():
    with _banco_dedicado(MIG_DB):
        criar_tabelas()
        with conectar() as conn:
            tid = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo) VALUES ('Do Zero',1) RETURNING id"
            ).fetchone()["id"]
            r = registrar_chegada(conn, tid)   # fluxo completo em banco 0km
            conn.commit()
            assert_eq(r["criado"], True, "registro novo em banco zero")
            assert_eq(_coluna_info(conn, "data"), "NO", "data nasce NOT NULL")
            assert_eq(_coluna_info(conn, "dia_trabalho_id"), "YES",
                      "dia nasce opcional")


# ── 3. Fluxo HTTP e relatórios ───────────────────────────────────────────────

@teste("H1/H2 dia fechado e sem agenda: tela abre, presente registra, repetição avisa")
def h1_h2(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Chega Cedo")
    ctx.conn.commit()
    status, html = _http("GET", "/checkin-trabalhador")
    assert_eq(status, 200, "tela abre mesmo sem dias_trabalho (nenhum dia aberto)")
    assert "Chega Cedo" in html, "trabalhador ativo sem agenda aparece na lista"
    sessao = _sessao_nova("admin", "teste123")
    _, _, loc = sessao.post(f"/checkin-trabalhador/{tid}/presente")
    assert loc and "status=novo" in loc, f"primeiro POST deve avisar registro novo: {loc}"
    _, _, loc2 = sessao.post(f"/checkin-trabalhador/{tid}/presente")
    assert loc2 and "status=ja_registrada" in loc2, f"repetição distingue: {loc2}"
    n = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_presenca WHERE trabalhador_id = %s",
        (tid,)).fetchone()["c"]
    assert_eq(n, 1, "um único registro")
    d = ctx.conn.execute("SELECT COUNT(*) AS c FROM dias_trabalho").fetchone()["c"]
    assert_eq(d, 0, "atendimento permanece sem abertura (nada foi criado)")
    _, html = sessao(loc2)
    assert "já estava presente hoje" in html, "confirmação visível na tela"


@teste("H3 dia aberto permanece aberto: presença não cria nem reabre dias")
def h3(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    ctx.conn.execute(
        "INSERT INTO dias_trabalho (data, aberto) VALUES (%s, 1)", (hoje,))
    tid = _trab(ctx, "Dia Aberto")
    ctx.conn.commit()
    sessao = _sessao_nova("admin", "teste123")
    st, _, loc = sessao.post(f"/checkin-trabalhador/{tid}/presente")
    assert_eq(st, 303, "POST responde redirect")
    row = ctx.conn.execute(
        "SELECT aberto AS a, COUNT(*) OVER () AS c FROM dias_trabalho WHERE data = %s",
        (hoje,)).fetchone()
    assert_eq(row["a"], 1, "dia continua aberto")
    assert_eq(row["c"], 1, "nenhum dia duplicado/criado")


@teste("H4 inativo pela tela: recusa clara, sem escrita")
def h4(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Desligado", ativo=0)
    ctx.conn.commit()
    sessao = _sessao_nova("admin", "teste123")
    _, _, loc = sessao.post(f"/checkin-trabalhador/{tid}/presente")
    assert loc and "status=inativo" in loc, f"deveria avisar inativo: {loc}"
    n = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_presenca").fetchone()["c"]
    assert_eq(n, 0, "sem escrita indevida")


@teste("H5 rotas legadas não gravam mais: URLs antigas dão 404")
def h5(ctx: Ctx):
    st_get, _ = _http("GET", "/cadastros/trabalhadores/dia/trabalhador-checkin")
    assert_eq(st_get, 404, "tela legada removida")
    st_post, _ = _http("POST", "/cadastros/trabalhadores/dia/trabalhador-checkin/1/presente")
    assert_eq(st_post, 404, "POST legado removido")
    st_post2, _ = _http("POST", "/cadastros/trabalhadores/dia/trabalhador-checkin/1/ausente")
    assert_eq(st_post2, 404, "POST legado ausente removido")


@teste("H6 permissões: sem módulo recusa (inclusive legadas); recepção não vira cadastro")
def h6(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Alvo da Recepção")
    from rotas.auth import hash_senha
    for usu in ("semmodulo", "recep1"):
        ctx.conn.execute(
            """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
               VALUES (%s, %s, %s, 1)
               ON CONFLICT (nome_usuario) DO UPDATE SET senha_hash = EXCLUDED.senha_hash, ativo = 1""",
            (usu, usu.title(), hash_senha("teste123")),
        )
    ctx.conn.execute(
        """INSERT INTO grupos (nome, descricao) VALUES ('Recepção TASK001','teste')
           ON CONFLICT (nome) DO NOTHING"""
    )
    g = ctx.conn.execute(
        "SELECT id FROM grupos WHERE nome = 'Recepção TASK001'"
    ).fetchone()["id"]
    ctx.conn.execute(
        """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
           VALUES (%s, 'checkin.trabalhadores', TRUE, TRUE, FALSE)
           ON CONFLICT (grupo_id, modulo) DO UPDATE
           SET ler = TRUE, escrever = TRUE, apagar = FALSE""", (g,))
    for usu in ("recep1",):
        uid = ctx.conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario = %s", (usu,)).fetchone()["id"]
        ctx.conn.execute(
            "INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (uid, g))
    ctx.conn.commit()

    sem = _sessao_nova("semmodulo", "teste123")
    st, _ = sem("/checkin-trabalhador")
    assert_eq(st, 403, "sem permissão: tela recusada")
    st, _, _ = sem.post(f"/checkin-trabalhador/{tid}/presente")
    assert_eq(st, 403, "sem permissão: POST de chegada recusado")

    rec = _sessao_nova("recep1", "teste123")
    st, _, loc = rec.post(f"/checkin-trabalhador/{tid}/presente")
    assert_eq(st, 303, "recepção registra chegada")
    st, _ = rec("/cadastros/trabalhadores")
    assert_eq(st, 403, "recepção NÃO ganha acesso ao cadastro")
    st, _ = rec("/cadastros/trabalhadores/presenca-relatorio-que-nao-existe")
    assert st in (403, 404), "nenhuma rota de cadastro aberta à recepção"


@teste("H7 período vazio no relatório geral: contagens zeradas, sem trazer outros meses")
def h7(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Presente Hoje")
    hoje = _hoje_sp(ctx.conn)
    ctx.conn.execute(
        """INSERT INTO trabalhador_presenca (trabalhador_id, data, presente, hora_chegada)
           VALUES (%s, %s::date, 1, '19:00')""", (tid, hoje))
    ctx.conn.commit()
    st, html = _http("GET",
                     "/relatorios/presenca-trabalhadores?data_ini=2020-01-01&data_fim=2020-01-31")
    assert_eq(st, 200, "relatório responde")
    # o seletor pesquisável de trabalhador lista toda a gente na página; a
    # contagem do período se mede apenas pelas linhas da tabela (sem tabela,
    # nada foi contado)
    linhas = html.split("<table>", 1)[1].split("</table>", 1)[0] if "<table>" in html else ""
    assert "Presente Hoje" not in linhas, "nada fora do período entra na contagem"
    st, html_cheio = _http("GET",
                           f"/relatorios/presenca-trabalhadores?data_ini={hoje}&data_fim={hoje}")
    assert "Presente Hoje" in html_cheio, "no período correto o registro aparece"


@teste("H8 filtro de dias: segunda/quarta/domingo batem com ISODOW (sem deslocamento)")
def h8(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Filtros de Dia")
    # 19/10/2026 = segunda, 21/10/2026 = quarta, 18/10/2026 = domingo
    for d in ("2026-10-19", "2026-10-21", "2026-10-18"):
        ctx.conn.execute(
            """INSERT INTO trabalhador_presenca (trabalhador_id, data, presente, hora_chegada)
               VALUES (%s, %s::date, 1, '19:00')""", (tid, d))
    ctx.conn.commit()
    esperado = {"0": ("19/10/2026", ("18/10/2026", "21/10/2026")),
                "2": ("21/10/2026", ("18/10/2026", "19/10/2026")),
                "6": ("18/10/2026", ("19/10/2026", "21/10/2026"))}
    for ds, (data_br, excluidas) in esperado.items():
        st, html = _http(
            "GET",
            "/relatorios/presenca-trabalhador?trabalhador=Filtros%20de%20Dia"
            f"&data_ini=2026-10-17&data_fim=2026-10-23&dia_semana={ds}")
        assert_eq(st, 200, f"relatório dia_semana={ds}")
        assert data_br in html, f"filtro {ds}: esperado {data_br} presente"
        for excl in excluidas:
            assert excl not in html, f"filtro {ds}: {excl} não deveria aparecer"


@teste("H9 presença sem dia de atendimento aparece no histórico individual")
def h9(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Sem Dia no Histórico")
    ctx.conn.execute(
        """INSERT INTO trabalhador_presenca (trabalhador_id, data, presente, hora_chegada)
           VALUES (%s, '2026-10-19'::date, 1, '18:40')""", (tid,))
    ctx.conn.commit()
    st, html = _http("GET", f"/cadastros/trabalhadores/{tid}/presenca?mes=2026-10")
    assert_eq(st, 200, "histórico responde")
    assert "19/10/2026" in html, "presença sem dias_trabalho aparece no histórico"
    assert "18:40" in html, "chegada exibida"


def main():
    preparar_banco()
    _garantir_admin()
    with conectar() as conn:
        _limpa_negocio(conn)
    _login()
    total, ok = 0, 0
    for nome, fn in RESULTADOS:
        total += 1
        try:
            if nome.startswith("M"):
                fn()  # testes de migração abrem o próprio contexto de banco
            else:
                with conectar() as conn:
                    fn(Ctx(conn))
            ok += 1
            print(f"  PASS  {nome}")
        except Exception as e:
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{ok}/{total} testes passaram (presença TASK_001)")
    with contextlib.suppress(Exception):
        _psql_admin(f'DROP DATABASE IF EXISTS "{MIG_DB}"')
    limpar_banco()
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
