#!/usr/bin/env python3
"""Suíte TASK_004 — gestão do dia, correções auditadas e relatórios de presença.

Cobre a matriz de validação obrigatória da etapa 4:
- serviço de correção (motivo obrigatório, datas, horários, travas, conflito);
- auditoria na mesma transação do fato (falha na auditoria ⇒ fato não persiste);
- anulação com recriação legítima posterior, sem perder o histórico;
- redesign de registrar_ausencia_explicita (nunca rebaixa presença);
- permissões do módulo checkin.trabalhadores.correcoes no servidor
  (operador comum, anônimo e sessão de terminal protegidos);
- gestão do dia via HTTP (data consultada, filtros, somente leitura, origem);
- relatórios (7 dias, origem, inativos, sem percentual, validação de período);
- migração idempotente da tabela de correções em banco DEDICADO.

Uso (recomendado): bash tests/rodar_testes.sh
"""
import io
import os
import re
import sys
import json
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
from preparo_descartavel import recriar_banco, remover_se_nosso  # noqa: E402
from servico_presenca import (  # noqa: E402
    registrar_chegada, registrar_ausencia_explicita, OrigemInvalida,
    TrabalhadorInativo, TrabalhadorInexistente, rotulo_origem,
)
from servico_correcao_presenca import (  # noqa: E402
    aplicar_correcao, historico_da_data, ler_fato, estado_base,
    validar_data, validar_horario, operacoes_permitidas,
    ConflitoCorrecao, MotivoObrigatorio, DataFutura, DataInvalida,
    HorarioInvalido, OperacaoInvalida, FatoInexistente,
)

MIG_DB = "shambala_correcao_mig_test"  # dedicado; contém '_test' (trava ok)

RESULTADOS = []


def teste(nome):
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


# ── Fixtures da suíte ─────────────────────────────────────────────────────────

def _limpa_negocio(conn):
    """Presença/auditoria/dias/trabalhadores limpos; atendentes vivos."""
    conn.execute(
        "TRUNCATE trabalhador_presenca, trabalhador_presenca_correcao, "
        "trabalhador_dias, dias_trabalho, trabalhadores RESTART IDENTITY CASCADE"
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


def _facto(conn, tid, data, presente=1, hora=None, origem=None):
    conn.execute(
        """INSERT INTO trabalhador_presenca
           (trabalhador_id, data, presente, hora_chegada, origem_chegada)
           VALUES (%s, %s::date, %s, %s, %s)""",
        (tid, data, presente, hora, origem),
    )
    conn.commit()


def _auditoria(conn, tid=None, data=None):
    sql = "SELECT COUNT(*) AS c FROM trabalhador_presenca_correcao"
    cond, params = [], []
    if tid is not None:
        cond.append("trabalhador_id = %s"); params.append(tid)
    if data is not None:
        cond.append("data_fato = %s::date"); params.append(data)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    return conn.execute(sql, tuple(params)).fetchone()["c"]


def _fato(conn, tid, data):
    row = conn.execute(
        """SELECT presente, hora_chegada, hora_saida, origem_chegada
           FROM trabalhador_presenca WHERE trabalhador_id = %s AND data = %s::date""",
        (tid, data),
    ).fetchone()
    return dict(row) if row else None


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


def _anonimo():
    """Opener sem cookies: representação de acesso anônimo direto à URL."""
    opener = urllib.request.build_opener(_SemRedirect())

    def get(path):
        r = urllib.request.Request(BASE + path, method="GET")
        try:
            with opener.open(r) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
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
    if "_test" not in nome or nome in ("shambala", "postgres", "template0", "template1"):
        raise AmbienteInseguro(f"{nome} não é um banco marcadamente de teste")
    original = banco._DB_NAME
    recriar_banco(nome, tp.TOKEN)  # TASK_010: só recria banco com selo da infraestrutura
    fechar_pool()
    banco._DB_NAME = nome
    try:
        yield
    finally:
        fechar_pool()
        banco._DB_NAME = original


# ── 1. Serviço de origem e ausência (redesign) ───────────────────────────────

@teste("C1 origem administrativa é recusada na chegada normal")
def c1(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Tenta Origem Admin")
    for origem in ("correcao_administrativa", "hacker", ""):
        try:
            registrar_chegada(ctx.conn, tid, origem=origem)
            raise AssertionError(f"origem {origem!r} deveria ser recusada")
        except OrigemInvalida:
            pass
    ctx.conn.commit()
    n = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_presenca").fetchone()["c"]
    assert_eq(n, 0, "recusa não escreve")


@teste("C2 repetição normal: primeiro horário e origem preservados, zero auditoria")
def c2(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Repetidor Sem Auditoria")
    r1 = registrar_chegada(ctx.conn, tid, origem="terminal_qr")
    ctx.conn.execute(
        "UPDATE trabalhador_presenca SET hora_chegada = '07:58' WHERE trabalhador_id = %s",
        (tid,))
    ctx.conn.commit()
    r2 = registrar_chegada(ctx.conn, tid, origem="recepcao")
    ctx.conn.commit()
    assert_eq(r2["criado"], False, "repetição não é chegada nova")
    f = _fato(ctx.conn, tid, _hoje_sp(ctx.conn))
    assert_eq(f["hora_chegada"], "07:58", "primeiro horário preservado")
    assert_eq(f["origem_chegada"], "terminal_qr", "primeira origem preservada")
    assert_eq(_auditoria(ctx.conn), 0, "repetição NÃO fabrica linha de auditoria")


@teste("C3 ausência explícita redesenhada: criada, repetida e recusada sobre presença")
def c3(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    tid = _trab(ctx, "Ausente do Jeito Certo")
    r = registrar_ausencia_explicita(ctx.conn, tid)
    ctx.conn.commit()
    assert_eq(r["resultado"], "registrada", "sem fato anterior: cria ausência")
    assert_eq(_auditoria(ctx.conn), 0, "lançamento normal não é correção")

    r2 = registrar_ausencia_explicita(ctx.conn, tid)
    ctx.conn.commit()
    assert_eq(r2["resultado"], "ja_ausente", "repetição não reescreve nada")

    # presença lançada: ausência antiga NUNCA rebaixa em silêncio
    tid2 = _trab(ctx, "Presente Protegido")
    registrar_chegada(ctx.conn, tid2, origem="recepcao")
    ctx.conn.execute(
        "UPDATE trabalhador_presenca SET hora_chegada = '08:10' WHERE trabalhador_id = %s",
        (tid2,))
    ctx.conn.commit()
    r3 = registrar_ausencia_explicita(ctx.conn, tid2)
    ctx.conn.commit()
    assert_eq(r3["resultado"], "exige_correcao", "fato lançado só muda pelo fluxo autorizado")
    f = _fato(ctx.conn, tid2, hoje)
    assert_eq(f["presente"], 1, "presença permanece de pé")
    assert_eq(f["hora_chegada"], "08:10", "horário intacto")
    assert_eq(_auditoria(ctx.conn), 0, "recusa não grava nada")


# ── 2. Serviço de correção ───────────────────────────────────────────────────

@teste("C4 corrigir horário preserva origem; auditoria guarda antes/depois")
def c4(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    tid = _trab(ctx, "Relógio Ajustado")
    _facto(ctx.conn, tid, hoje, 1, "09:40", "terminal_codigo")
    token = estado_base(_fato(ctx.conn, tid, hoje))
    r = aplicar_correcao(ctx.conn, trabalhador_id=tid, data=hoje,
                        operacao="corrigir_horario", motivo="chegou às 08:05",
                        estado_base_esperado=token, atendente_id=ctx.conn.execute(
                            "SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()["id"],
                        nova_hora="08:05")
    ctx.conn.commit()
    f = _fato(ctx.conn, tid, hoje)
    assert_eq(f["hora_chegada"], "08:05", "horário corrigido")
    assert_eq(f["origem_chegada"], "terminal_codigo", "origem NÃO falsificada")
    hist = historico_da_data(ctx.conn, tid, hoje)
    assert_eq(len(hist), 1, "uma linha de auditoria")
    assert_eq(hist[0]["operacao"], "correcao_horario")
    assert_eq(hist[0]["anterior_hora_chegada"], "09:40", "valor anterior guardado")
    assert_eq(hist[0]["novo_hora_chegada"], "08:05")
    assert_eq(hist[0]["anterior_origem"], "terminal_codigo")
    assert hist[0]["motivo"].strip(), "motivo persistido"
    assert hist[0]["criado_em"], "instante do servidor registrado"


@teste("C5 presente→ausente limpa horários no fato; anteriores vivem na auditoria")
def c5(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    tid = _trab(ctx, "Virou Ausente")
    _facto(ctx.conn, tid, hoje, 1, "08:00", "recepcao")
    token = estado_base(_fato(ctx.conn, tid, hoje))
    aplicar_correcao(ctx.conn, trabalhador_id=tid, data=hoje,
                    operacao="ausentar", motivo="saiu mais cedo, erro de lançamento",
                    estado_base_esperado=token, atendente_id=1)
    ctx.conn.commit()
    f = _fato(ctx.conn, tid, hoje)
    assert_eq(f["presente"], 0, "ausência explícita")
    assert_eq(f["hora_chegada"], None, "horário não compõe mais o fato atual")
    assert_eq(f["origem_chegada"], None)
    h = historico_da_data(ctx.conn, tid, hoje)[0]
    assert_eq(h["anterior_hora_chegada"], "08:00", "anterior preservado na auditoria")
    assert_eq(h["anterior_origem"], "recepcao")
    assert_eq(h["novo_presente"], 0)


@teste("C6 ausência→presença e inclusão retroativa usam origem administrativa")
def c6(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    ontem = ctx.conn.execute(
        "SELECT (%s::date - 1)::text AS d", (hoje,)).fetchone()["d"]
    tid = _trab(ctx, "Convertido e Incluído")

    _facto(ctx.conn, tid, hoje, 0, None, None)
    token = estado_base(_fato(ctx.conn, tid, hoje))
    aplicar_correcao(ctx.conn, trabalhador_id=tid, data=hoje, operacao="chegar",
                    motivo="esteve aqui, recepção havia marcado errado",
                    estado_base_esperado=token, atendente_id=1, nova_hora=None)
    ctx.conn.commit()
    f = _fato(ctx.conn, tid, hoje)
    assert_eq(f["presente"], 1)
    assert_eq(f["origem_chegada"], "correcao_administrativa",
              "conversão administrativa tem origem clara")
    assert_eq(f["hora_chegada"], None, "sem horário = desconhecido NULL declarado")

    # inclusão retroativa de ausência: origem NULL (ausência não tem chegada)
    token_none = estado_base(_fato(ctx.conn, tid, ontem))
    assert_eq(token_none, "sem-registro")
    aplicar_correcao(ctx.conn, trabalhador_id=tid, data=ontem, operacao="incluir",
                    motivo="faltou o lançamento do dia", estado_base_esperado="sem-registro",
                    atendente_id=1, incluir_estado="ausente")
    ctx.conn.commit()
    f = _fato(ctx.conn, tid, ontem)
    assert_eq((f["presente"], f["origem_chegada"]), (0, None))
    h = historico_da_data(ctx.conn, tid, ontem)[0]
    assert_eq(h["operacao"], "lancamento_retroativo")
    assert_eq(h["anterior_presente"], None, "não havia fato anterior")


@teste("C7 recusas: motivo vazio, hora inválida, data futura — sem escrita parcial")
def c7(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    amanha = ctx.conn.execute(
        "SELECT (%s::date + 1)::text AS d", (hoje,)).fetchone()["d"]
    tid = _trab(ctx, "Recusado em Tudo")
    _facto(ctx.conn, tid, hoje, 1, "08:00", "recepcao")
    token = estado_base(_fato(ctx.conn, tid, hoje))
    antes = _fato(ctx.conn, tid, hoje)
    aud_before = _auditoria(ctx.conn)

    casos = [
        (dict(operacao="corrigir_horario", motivo="   "), MotivoObrigatorio),
        (dict(operacao="corrigir_horario", motivo="x", nova_hora="25:99"), HorarioInvalido),
        (dict(operacao="corrigir_horario", motivo="x", nova_hora="8:0"), HorarioInvalido),
        (dict(operacao="corrigir_horario", motivo="x", data=amanha), DataFutura),
        (dict(operacao="corrigir_horario", motivo="x", data="2026-02-30"), DataInvalida),
        (dict(operacao="pintar_o_muro", motivo="x"), OperacaoInvalida),
        (dict(operacao="incluir", motivo="x", incluir_estado="presente"), ConflitoCorrecao),
        (dict(operacao="anular", motivo="x", estado_base_esperado="ausente"), ConflitoCorrecao),
    ]
    for extra, exc in casos:
        kw = dict(trabalhador_id=tid, data=hoje, estado_base_esperado=token,
                  atendente_id=1, motivo="x")
        kw.update(extra)
        try:
            aplicar_correcao(ctx.conn, **kw)
            raise AssertionError(f"{extra} deveria lançar {exc.__name__}")
        except exc:
            pass
        ctx.conn.rollback()
    assert_eq(_fato(ctx.conn, tid, hoje), antes, "fato intocado em todas as recusas")
    assert_eq(_auditoria(ctx.conn), aud_before, "nenhuma auditoria fabricada")


@teste("C8 horário desconhecido continua desconhecido até correção explícita")
def c8(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    tid = _trab(ctx, "Sem Hora Na Origem")
    _facto(ctx.conn, tid, hoje, 1, None, None)
    # chegada normal não inventa hora (herança TASK_001, reconfirmada)
    r = registrar_chegada(ctx.conn, tid, origem="recepcao")
    ctx.conn.commit()
    assert_eq(_fato(ctx.conn, tid, hoje)["hora_chegada"], None,
              "repetição não fabrica 00:00 nem 'agora'")
    # correção explícita preenche
    token = estado_base(_fato(ctx.conn, tid, hoje))
    aplicar_correcao(ctx.conn, trabalhador_id=tid, data=hoje,
                    operacao="corrigir_horario", motivo="conferido com o próprio",
                    estado_base_esperado=token, atendente_id=1, nova_hora="08:30")
    ctx.conn.commit()
    assert_eq(_fato(ctx.conn, tid, hoje)["hora_chegada"], "08:30")
    # e corrigir para vazio volta a NULL declarado, nunca 00:00
    token = estado_base(_fato(ctx.conn, tid, hoje))
    aplicar_correcao(ctx.conn, trabalhador_id=tid, data=hoje,
                    operacao="corrigir_horario", motivo="hora real não lembrada",
                    estado_base_esperado=token, atendente_id=1, nova_hora="")
    ctx.conn.commit()
    assert_eq(_fato(ctx.conn, tid, hoje)["hora_chegada"], None)


@teste("C9 anulação: volta a Sem registro; chegada legítima recria sem apagar histórico")
def c9(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    tid = _trab(ctx, "Anulado e Recriado")
    _facto(ctx.conn, tid, hoje, 1, "08:00", "terminal_qr")
    token = estado_base(_fato(ctx.conn, tid, hoje))
    aplicar_correcao(ctx.conn, trabalhador_id=tid, data=hoje, operacao="anular",
                    motivo="lançamento errado, pessoa não veio neste dia",
                    estado_base_esperado=token, atendente_id=1)
    ctx.conn.commit()
    assert_eq(_fato(ctx.conn, tid, hoje), None, "sem linha = Sem registro")
    assert_eq(_auditoria(ctx.conn, tid, hoje), 1)
    h = historico_da_data(ctx.conn, tid, hoje)[0]
    assert_eq(h["operacao"], "anulacao")
    assert_eq(h["novo_presente"], None, "anulação não inventa fato novo")
    assert_eq(h["anterior_hora_chegada"], "08:00", "evidência do fato anulado")

    # chegada legítima posterior RECRIOU o fato pelo fluxo normal
    r = registrar_chegada(ctx.conn, tid, origem="terminal_codigo")
    ctx.conn.commit()
    assert_eq(r["criado"], True, "nova chegada é chegada nova")
    assert_eq(_fato(ctx.conn, tid, hoje)["presente"], 1)
    assert_eq(_auditoria(ctx.conn, tid, hoje), 1, "a anulação continua lá, intacta")


@teste("C10 conflito: formulário aberto sobre estado velho não sobrescreve nada")
def c10(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    tid = _trab(ctx, "Dois Formulários")
    _facto(ctx.conn, tid, hoje, 1, "08:00", "recepcao")
    token_aberto = estado_base(_fato(ctx.conn, tid, hoje))

    # Correção A (mesmo token, mas o fato mudou depois de aberto)
    aplicar_correcao(ctx.conn, trabalhador_id=tid, data=hoje,
                    operacao="corrigir_horario", motivo="ajuste A",
                    estado_base_esperado=token_aberto, atendente_id=1,
                    nova_hora="08:15")
    ctx.conn.commit()
    try:
        aplicar_correcao(ctx.conn, trabalhador_id=tid, data=hoje,
                         operacao="corrigir_horario", motivo="ajuste B por cima",
                         estado_base_esperado=token_aberto, atendente_id=1,
                         nova_hora="09:00")
        raise AssertionError("segundo formulário com estado velho deveria conflitar")
    except ConflitoCorrecao:
        pass
    ctx.conn.rollback()
    assert_eq(_fato(ctx.conn, tid, hoje)["hora_chegada"], "08:15",
              "conflito não sobrescreveu a correção A")
    assert_eq(_auditoria(ctx.conn, tid, hoje), 1, "só a correção aplicada gerou linha")


@teste("C11 concorrência correção x chegada do terminal: serializa, um resultado coerente")
def c11(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    tid = _trab(ctx, "Terminal Contra Correção")
    ctx.conn.commit()
    erros, resultados = [], []

    def chegada():
        try:
            with conectar() as c:
                resultados.append(("chegada", registrar_chegada(c, tid, origem="terminal_qr")["criado"]))
        except Exception as e:
            erros.append(f"chegada: {type(e).__name__}: {e}")

    def correcao():
        try:
            with conectar() as c:
                r = aplicar_correcao(c, trabalhador_id=tid, data=hoje,
                                     operacao="incluir", motivo="lançamento manual concorrente",
                                     estado_base_esperado="sem-registro", atendente_id=1,
                                     incluir_estado="presente", nova_hora="07:00")
                resultados.append(("correcao", r["presente"]))
        except ConflitoCorrecao:
            resultados.append(("correcao", "conflito"))
        except Exception as e:
            erros.append(f"correcao: {type(e).__name__}: {e}")

    ts = [threading.Thread(target=f) for f in (chegada, correcao)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert_eq(erros, [], "nenhuma falha inesperada")
    f = _fato(ctx.conn, tid, hoje)
    assert_eq(f["presente"], 1, "presença vigente no fim")
    n = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_presenca WHERE trabalhador_id = %s",
        (tid,)).fetchone()["c"]
    assert_eq(n, 1, "exatamente uma linha")
    origens_validas = ("terminal_qr", "correcao_administrativa")
    assert f["origem_chegada"] in origens_validas, f"origem coerente: {f['origem_chegada']}"
    audit = _auditoria(ctx.conn, tid, hoje)
    assert audit in (0, 1), "no máximo uma auditoria (a correção aplicada)"
    qtd = sum(1 for r in resultados if r[1] == "conflito")
    if qtd:
        assert_eq(audit, 0, "quem conflitou não gravou nada")


@teste("C12 falha ao escrever a auditoria: nenhuma alteração de presença persiste")
def c12(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    tid = _trab(ctx, "Transação Atômica")
    _facto(ctx.conn, tid, hoje, 1, "08:00", "recepcao")
    token = estado_base(_fato(ctx.conn, tid, hoje))

    ctx.conn.execute("""
        CREATE OR REPLACE FUNCTION trg_boom_audit() RETURNS trigger AS $f$
        BEGIN
            IF NEW.motivo = 'BOOM' THEN
                RAISE EXCEPTION 'falha simulada ao gravar a auditoria';
            END IF;
            RETURN NEW;
        END$f$ LANGUAGE plpgsql""")
    ctx.conn.execute("""
        CREATE TRIGGER tg_audit_boom BEFORE INSERT ON trabalhador_presenca_correcao
        FOR EACH ROW EXECUTE FUNCTION trg_boom_audit()""")
    ctx.conn.commit()
    try:
        try:
            with conectar() as c:
                aplicar_correcao(c, trabalhador_id=tid, data=hoje,
                                 operacao="corrigir_horario", motivo="BOOM",
                                 estado_base_esperado=token, atendente_id=1,
                                 nova_hora="09:59")
            raise AssertionError("trigger deveria derrubar a transação")
        except Exception as e:
            assert "falha simulada" in str(e), f"erro inesperado: {e}"
        f = _fato(ctx.conn, tid, hoje)
        assert_eq(f["hora_chegada"], "08:00",
                  "fato NÃO persistiu junto com a falha da auditoria")
        assert_eq(_auditoria(ctx.conn, tid, hoje), 0)
    finally:
        ctx.conn.execute("DROP TRIGGER IF EXISTS tg_audit_boom ON trabalhador_presenca_correcao")
        ctx.conn.execute("DROP FUNCTION IF EXISTS trg_boom_audit()")
        ctx.conn.commit()


@teste("C13 inativo com histórico: corrigível, sem reativar e sem código novo")
def c13(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    ontem = ctx.conn.execute("SELECT (%s::date - 1)::text AS d", (hoje,)).fetchone()["d"]
    tid = _trab(ctx, "Veterano Inativo", ativo=0)
    cod_antes = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_codigo WHERE trabalhador_id = %s",
        (tid,)).fetchone()["c"]
    _facto(ctx.conn, tid, ontem, 1, "08:00", "recepcao")

    # chegada normal continua proibida para inativo
    try:
        registrar_chegada(ctx.conn, tid, origem="recepcao")
        raise AssertionError("inativo não faz chegada normal")
    except TrabalhadorInativo:
        pass
    ctx.conn.rollback()

    token = estado_base(_fato(ctx.conn, tid, ontem))
    aplicar_correcao(ctx.conn, trabalhador_id=tid, data=ontem,
                    operacao="corrigir_horario", motivo="histórico conferido",
                    estado_base_esperado=token, atendente_id=1, nova_hora="08:20")
    ctx.conn.commit()
    assert_eq(_fato(ctx.conn, tid, ontem)["hora_chegada"], "08:20")
    ativo = ctx.conn.execute("SELECT ativo AS a FROM trabalhadores WHERE id = %s",
                             (tid,)).fetchone()["a"]
    assert_eq(ativo, 0, "correção não reativou ninguém")
    cod_depois = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_codigo WHERE trabalhador_id = %s",
        (tid,)).fetchone()["c"]
    assert_eq(cod_depois, cod_antes, "nenhum código emitido pela correção")


# ── 3. Gestão do dia via HTTP ────────────────────────────────────────────────

@teste("C14 gestão: data consultada, origem traduzida, contagem e somente leitura no passado")
def c14(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    ontem = ctx.conn.execute("SELECT (%s::date - 1)::text AS d", (hoje,)).fetchone()["d"]
    t1 = _trab(ctx, "Zélia Do Terminal")
    t2 = _trab(ctx, "Beto Da Recepção")
    t3 = _trab(ctx, "Nuno Sem Nada")
    t4 = _trab(ctx, "Ines Esquecida", ativo=0)
    _facto(ctx.conn, t1, hoje, 1, "08:01", "terminal_qr")
    _facto(ctx.conn, t2, hoje, 0, None, None)
    _facto(ctx.conn, t4, ontem, 1, "09:00", "recepcao")
    ctx.conn.commit()

    sessao = _sessao_nova("admin", "teste123")
    st, html = sessao(f"/checkin-trabalhador?data={hoje}")
    assert_eq(st, 200)
    assert "Gestão do dia" in html
    assert "QR" in html and "Zélia Do Terminal" in html, "origem QR traduzida"
    assert "✔ 1 presente" in html, "contagem de presentes na data"
    assert "Nuno Sem Nada" in html, "ativo sem agenda aparece"
    assert "Ines Esquecida" not in html, "inativo SEM registro na data não aparece"
    assert "Corrigir/lançar nesta data" in html, "admin vê o link de correção"

    st, html = sessao(f"/checkin-trabalhador?data={ontem}")
    assert_eq(st, 200)
    assert "Ines Esquecida" in html and "inativo (histórico)" in html, \
        "inativo COM registro na data aparece identificado"
    assert "somente leitura" in html, "dia passado é consulta"
    assert f"/checkin-trabalhador/{t1}/presente" not in html, \
        "sem botões de chegada em dia passado"

    st, html = sessao(f"/checkin-trabalhador?busca=Z%C3%A9lia")
    assert "Zélia Do Terminal" in html and "Beto Da Recepção" not in html, \
        "busca server-side com acento"
    st, html = sessao(f"/checkin-trabalhador?filtro=ausencias")
    assert "Beto Da Recepção" in html and "Zélia Do Terminal" not in html, \
        "filtro de ausências explícitas"
    st, html = sessao(f"/checkin-trabalhador?filtro=sem_registro")
    assert "Nuno Sem Nada" in html and "Zélia Do Terminal" not in html

    st, body = sessao(f"/checkin-trabalhador/dados?data={hoje}")
    assert_eq(st, 200, "endpoint JSON da grade")
    dados = json.loads(body)
    assert_eq(dados["qtd_presentes"], 1)
    assert any(l["nome_completo"] == "Zélia Do Terminal" for l in dados["lista"])
    assert_eq(dados["somente_leitura"], False)

    st, html = sessao("/checkin-trabalhador?data=31/02/2026")
    assert "Data inválida" in html and "/".join(reversed(hoje.split("-"))) in html, \
        "data inválida cai para hoje com aviso, sem 500"


@teste("C15 POST ausente pela tela: desfechos do serviço traduzidos em aviso")
def c15(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Ausente Pela Tela")
    ctx.conn.commit()
    sessao = _sessao_nova("admin", "teste123")
    _, _, loc = sessao.post(f"/checkin-trabalhador/{tid}/ausente")
    assert loc and "status=ausente" in loc, f"primeira ausência registrada: {loc}"
    _, _, loc2 = sessao.post(f"/checkin-trabalhador/{tid}/ausente")
    assert loc2 and "status=ausente_ja" in loc2, f"repetição não reescreve: {loc2}"
    _, _, loc3 = sessao.post(f"/checkin-trabalhador/{tid}/presente")
    assert loc3 and "status=novo" in loc3, "ausência convertida por chegada normal"
    _, _, loc4 = sessao.post(f"/checkin-trabalhador/{tid}/ausente")
    assert loc4 and "status=ausente_existe" in loc4, \
        f"presente não é rebaixado: {loc4}"
    hoje = _hoje_sp(ctx.conn)
    f = _fato(ctx.conn, tid, hoje)
    assert_eq(f["presente"], 1, "presença intacta depois da recusa")


# ── 4. Permissões no servidor ────────────────────────────────────────────────

@teste("C16 correções protegidas: anônimo, sem módulo, só-leitura e terminal")
def c16(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    from rotas.auth import hash_senha
    tid = _trab(ctx, "Alvo Protegido")
    hoje = _hoje_sp(ctx.conn)
    _facto(ctx.conn, tid, hoje, 1, "08:00", "recepcao")
    for usu in ("semmodulo2", "recep2", "leitor_corr"):
        ctx.conn.execute(
            """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
               VALUES (%s, %s, %s, 1)
               ON CONFLICT (nome_usuario) DO UPDATE
               SET senha_hash = EXCLUDED.senha_hash, ativo = 1""",
            (usu, usu.title(), hash_senha("teste123")),
        )
    ctx.conn.execute(
        """INSERT INTO grupos (nome, descricao) VALUES ('Recepção TASK004','teste')
           ON CONFLICT (nome) DO NOTHING""")
    ctx.conn.execute(
        """INSERT INTO grupos (nome, descricao) VALUES ('Leitores TASK004','teste')
           ON CONFLICT (nome) DO NOTHING""")
    g = ctx.conn.execute("SELECT id FROM grupos WHERE nome='Recepção TASK004'").fetchone()["id"]
    gl = ctx.conn.execute("SELECT id FROM grupos WHERE nome='Leitores TASK004'").fetchone()["id"]
    ctx.conn.execute(
        """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
           VALUES (%s, 'checkin.trabalhadores', TRUE, TRUE, FALSE)
           ON CONFLICT (grupo_id, modulo) DO UPDATE SET ler=TRUE, escrever=TRUE, apagar=FALSE""",
        (g,))
    ctx.conn.execute(
        """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
           VALUES (%s, 'checkin.trabalhadores.correcoes', TRUE, FALSE, FALSE)
           ON CONFLICT (grupo_id, modulo) DO UPDATE SET ler=TRUE, escrever=FALSE, apagar=FALSE""",
        (gl,))
    for usu, gid in (("recep2", g), ("leitor_corr", gl)):
        uid = ctx.conn.execute("SELECT id FROM atendentes WHERE nome_usuario=%s",
                               (usu,)).fetchone()["id"]
        ctx.conn.execute(
            "INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (uid, gid))
    ctx.conn.commit()

    token = estado_base(_fato(ctx.conn, tid, hoje))
    form = {"data": hoje, "operacao": "corrigir_horario", "motivo": "se intrusion",
            "estado_base": token, "nova_hora": "10:00"}

    anon = _anonimo()
    st, _ = anon(f"/checkin-trabalhador/{tid}/correcao?data={hoje}")
    assert_eq(st, 303, "anônimo vai ao login")

    rec = _sessao_nova("recep2", "teste123")
    st, _ = rec(f"/checkin-trabalhador/{tid}/correcao?data={hoje}")
    assert_eq(st, 403, "recepção sem módulo de correção: GET recusado")
    st, _, _ = rec.post(f"/checkin-trabalhador/{tid}/correcao", form)
    assert_eq(st, 403, "recepção: POST recusado")
    assert_eq(_fato(ctx.conn, tid, hoje)["hora_chegada"], "08:00", "nada mudou")

    lei = _sessao_nova("leitor_corr", "teste123")
    st, html = lei(f"/checkin-trabalhador/{tid}/correcao?data={hoje}")
    assert_eq(st, 200, "só-leitura enxerga o histórico")
    assert "Aplicar correção" not in html, "sem escrita: formulário não é exibido"
    st, _, _ = lei.post(f"/checkin-trabalhador/{tid}/correcao", form)
    assert_eq(st, 403, "só-leitura não aplica correção")

    # sessão do terminal (cookie sessao_terminal) não autentic nada daqui
    opener = urllib.request.build_opener(_SemRedirect())
    r = urllib.request.Request(BASE + f"/checkin-trabalhador/{tid}/correcao?data={hoje}",
                               headers={"Cookie": "sessao_terminal=qualquer-coisa"})
    try:
        with opener.open(r) as resp:
            st = resp.status
    except urllib.error.HTTPError as e:
        st = e.code
    assert_eq(st, 303, "terminal sem sessão de sistema: login exigido")

    sem = _sessao_nova("semmodulo2", "teste123")
    st, _ = sem(f"/checkin-trabalhador?data={hoje}")
    assert_eq(st, 403, "gestão continua fechada para quem não tem checkin")


# ── 5. Correção via HTTP (fluxo completo) ────────────────────────────────────

@teste("C17 fluxo HTTP do formulário: corrigir, anular, conflito e recusa com motivo vazio")
def c17(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    tid = _trab(ctx, "Corrigido Pela Web")
    _facto(ctx.conn, tid, hoje, 1, "09:40", "terminal_codigo")
    ctx.conn.commit()
    sessao = _sessao_nova("admin", "teste123")

    st, html = sessao(f"/checkin-trabalhador/{tid}/correcao?data={hoje}")
    assert_eq(st, 200)
    assert "Corrigir o horário de chegada" in html and "Anular o lançamento" in html
    assert "09:40" in html and "Código digitado" in html, "estado atual exibido"
    token = "presente|09:40|terminal_codigo"

    st, _, loc = sessao.post(f"/checkin-trabalhador/{tid}/correcao", {
        "data": hoje, "operacao": "corrigir_horario", "motivo": "",
        "estado_base": token, "nova_hora": "08:05"})
    assert loc and "status=erro_motivo" in loc, f"motivo vazio recusado: {loc}"
    assert_eq(_fato(ctx.conn, tid, hoje)["hora_chegada"], "09:40", "nada gravado")

    st, _, loc = sessao.post(f"/checkin-trabalhador/{tid}/correcao", {
        "data": hoje, "operacao": "corrigir_horario", "motivo": "hora real 08:05",
        "estado_base": token, "nova_hora": "08:05"})
    assert loc and "status=corrigido" in loc, loc
    st, html = sessao(urllib.parse.urlparse(loc).path + "?" + urllib.parse.urlparse(loc).query)
    assert "Correção aplicada" in html and "08:05" in html
    assert "hora real 08:05" in html and "Alterações desta data" in html, "histórico na tela"

    # token velho agora conflita
    st, _, loc = sessao.post(f"/checkin-trabalhador/{tid}/correcao", {
        "data": hoje, "operacao": "corrigir_horario", "motivo": "por cima",
        "estado_base": token, "nova_hora": "09:00"})
    assert loc and "status=conflito" in loc, f"conflito sinalizado: {loc}"
    assert "mudou desde que o formulário foi aberto" in sessao(
        urllib.parse.urlparse(loc).path + "?" + urllib.parse.urlparse(loc).query
    )[1]

    # data futura recusada no servidor
    amanha = ctx.conn.execute("SELECT (%s::date + 1)::text AS d", (hoje,)).fetchone()["d"]
    st, _, loc = sessao.post(f"/checkin-trabalhador/{tid}/correcao", {
        "data": amanha, "operacao": "incluir", "motivo": "invenção",
        "estado_base": "sem-registro", "incluir_estado": "presente"})
    assert loc and "status=erro_data" in loc, loc

    # anular pela tela e recriar com chegada normal
    token2 = "presente|08:05|terminal_codigo"
    st, _, loc = sessao.post(f"/checkin-trabalhador/{tid}/correcao", {
        "data": hoje, "operacao": "anular", "motivo": "registro duplicado",
        "estado_base": token2})
    assert loc and "status=corrigido" in loc, loc
    assert_eq(_fato(ctx.conn, tid, hoje), None)
    _, _, loc = sessao.post(f"/checkin-trabalhador/{tid}/presente")
    assert loc and "status=novo" in loc, "após anular, chegada legítima recria"
    assert_eq(_auditoria(ctx.conn, tid, hoje), 2, "histórico das duas correções intacto")

    # ficha mostra origem, selo ajustado e histórico para quem tem correções:ler
    st, html = sessao(f"/cadastros/trabalhadores/{tid}/presenca?mes={hoje[:7]}")
    assert_eq(st, 200)
    assert "ajustado" in html, "selo de ajuste na ficha"
    assert "Histórico de alterações" in html
    assert "Registro duplicado" in html or "registro duplicado" in html


# ── 6. Relatórios ────────────────────────────────────────────────────────────

@teste("C18 geral: sem percentual, 7 dias no filtro, inativo com fato listado, origem filtra")
def c18(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    t1 = _trab(ctx, "Aura QR")
    t2 = _trab(ctx, "Bento Recepção")
    t3 = _trab(ctx, "Cida Inativa", ativo=0)
    _facto(ctx.conn, t1, hoje, 1, "08:00", "terminal_qr")
    _facto(ctx.conn, t2, hoje, 1, "08:30", "recepcao")
    _facto(ctx.conn, t3, hoje, 1, "09:00", "recepcao")
    ctx.conn.commit()

    st, html = _http("GET", f"/relatorios/presenca-trabalhadores?data_ini={hoje}&data_fim={hoje}")
    assert_eq(st, 200)
    assert "% Presença" not in html and "assiduidade" not in html, "sem percentual"
    assert "Aura QR" in html and "Bento Recepção" in html and "Cida Inativa" in html, \
        "inativo com fato no período não desapareceu"
    assert "inativo" in html, "situação identificada"
    for dia in ("Segunda-feira", "Sábado", "Domingo"):
        assert f">{dia}</option>" in html or dia in html, f"dia {dia} no filtro"
    assert "Ausência" in html or "ausente" in html.lower()

    st, html = _http("GET",
        f"/relatorios/presenca-trabalhadores?data_ini={hoje}&data_fim={hoje}&origem=terminal_qr")
    # O seletor pesquisável lista toda a gente no datalist e no JSON do campo
    # — quem passou pelo filtro se mede apenas pelas linhas da tabela.
    linhas = (html.split("<table>", 1)[1].split("</table>", 1)[0]
              if "<table>" in html else "")
    assert "Aura QR" in linhas, "filtro de origem mantém o fato correspondente"
    assert "Bento Recepção" not in linhas and "Cida Inativa" not in linhas, \
        "fatos de outra origem não entram"


@teste("C19 individual: dia da semana correto em PT, origem traduzida, sem contar anulados")
def c19(ctx: Ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Segunda Confirmada")
    # 19/10/2026 = segunda; 17/10/2026 = sábado (suíte TASK_001 fixou o calendário)
    ctx.conn.execute(
        """INSERT INTO trabalhador_presenca (trabalhador_id, data, presente, hora_chegada, origem_chegada)
           VALUES (%s, '2026-10-19'::date, 1, '08:40', 'terminal_codigo')""", (tid,))
    ctx.conn.execute(
        """INSERT INTO trabalhador_presenca (trabalhador_id, data, presente)
           VALUES (%s, '2026-10-17'::date, 0)""", (tid,))
    ctx.conn.commit()
    st, html = _http("GET",
        f"/relatorios/presenca-trabalhador?trabalhador=Segunda%20Confirmada"
        "&data_ini=2026-10-15&data_fim=2026-10-20")
    assert_eq(st, 200)
    assert "Código digitado" in html, "origem traduzida"
    assert "Presenças: 1" in html and "Ausências registradas: 1" in html

    # O dia da semana EXIBIDO na linha bate com o filtro ISODOW correspondente:
    # só-segunda mantém 19/10 e corta 17/10; só-sábado corta 19/10.
    st, html_s = _http("GET",
        f"/relatorios/presenca-trabalhador?trabalhador=Segunda%20Confirmada"
        "&data_ini=2026-10-15&data_fim=2026-10-20&dia_semana=0")
    assert "19/10/2026" in html_s and "17/10/2026" not in html_s, "filtro segunda"
    st, html_b = _http("GET",
        f"/relatorios/presenca-trabalhador?trabalhador=Segunda%20Confirmada"
        "&data_ini=2026-10-15&data_fim=2026-10-20&dia_semana=5")
    assert "17/10/2026" in html_b and "19/10/2026" not in html_b, "filtro sábado"
    # na linha de 19/10 o dia exibido é Segunda-feira (e 17/10 é Sábado)
    linha_seg = html_s.split("19/10/2026")[1][:200]
    assert "Segunda" in linha_seg, f"dia exibido errado: {linha_seg[:120]}"
    linha_sab = html_b.split("17/10/2026")[1][:200]
    assert "Sábado" in linha_sab, f"dia exibido errado: {linha_sab[:120]}"

    # anular um fato: some do relatório; auditoria não infla nada
    token = "presente|08:40|terminal_codigo"
    aplicar_correcao(ctx.conn, trabalhador_id=tid, data="2026-10-19",
                    operacao="anular", motivo="era de outro dia",
                    estado_base_esperado=token, atendente_id=1)
    ctx.conn.commit()
    st, html = _http("GET",
        f"/relatorios/presenca-trabalhador?trabalhador=Segunda%20Confirmada"
        "&data_ini=2026-10-15&data_fim=2026-10-20")
    assert "19/10/2026" not in html, "fato anulado não participa do relatório"
    assert "Presenças: 0" in html, "linha de auditoria não conta como presença"
    st, html = _http("GET",
        f"/relatorios/presenca-trabalhadores?data_ini=2026-10-15&data_fim=2026-10-20")
    assert "Segunda Confirmada" in html, "ainda tem ausência explícita no período"


@teste("C20 relatórios validam período e parâmetros sem 500")
def c20(ctx: Ctx):
    for qs, marca in (
        ("data_ini=2026-05-10&data_fim=2026-05-01", "posterior"),
        ("data_ini=banana&data_fim=2026-05-01", "AAAA-MM-DD"),
        ("data_ini=2026-05-01&data_fim=2026-02-30", "AAAA-MM-DD"),
    ):
        st, html = _http("GET", f"/relatorios/presenca-trabalhadores?{qs}")
        assert_eq(st, 200, f"sem 500 para {qs}")
        assert marca in html, f"mensagem clara esperada ({marca}) em {qs}"
        st, html = _http("GET", f"/relatorios/presenca-trabalhador?{qs}")
        assert_eq(st, 200)
        assert marca in html
    st, html = _http("GET",
        "/relatorios/presenca-trabalhadores?data_ini=2026-05-01&data_fim=2026-05-31"
        "&dia_semana=9&origem=hacker")
    assert_eq(st, 200, "filtro inválido não derruba a rota")
    assert "permitidos" in html or "desconhecida" in html
    # seletor: texto fora da lista é recusado nos dois relatórios — a
    # consulta nunca roda como "todos". A mensagem precisa estar na TELA:
    # o literal do script do navegador não pode contar como prova.
    for rota in ("/relatorios/presenca-trabalhadores",
                 "/relatorios/presenca-trabalhador"):
        st, html = _http("GET",
            f"{rota}?data_ini=2026-05-01&data_fim=2026-05-31"
            "&trabalhador=" + urllib.parse.quote("Ninguém Qualquer Silva"))
        assert_eq(st, 200, f"recusa de nome fora da lista sem 500 em {rota}")
        visivel = re.sub(r"<script\b.*?</script>", "", html, flags=re.S)
        assert "Selecione um trabalhador da lista" in visivel, \
            f"{rota} deveria pedir a seleção da lista"


# ── 7. Migração ──────────────────────────────────────────────────────────────

@teste("M1 tabela de correções nasce no schema novo e é idempotente")
def m1():
    with _banco_dedicado(MIG_DB):
        criar_tabelas()
        with conectar() as conn:
            cols = {r["c"] for r in conn.execute(
                "SELECT column_name AS c FROM information_schema.columns "
                "WHERE table_name = 'trabalhador_presenca_correcao'").fetchall()}
            assert {"operacao", "motivo", "anterior_hora_chegada",
                    "novo_origem", "criado_em", "data_fato"} <= cols, cols
            tid = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo) VALUES ('Migrado',1) RETURNING id"
            ).fetchone()["id"]
            conn.execute(
                """INSERT INTO trabalhador_presenca_correcao
                   (trabalhador_id, data_fato, operacao, anterior_presente,
                    novo_presente, motivo, atendente_id)
                   VALUES (%s, '2026-06-01', 'anulacao', 1, NULL, 'prova', NULL)""",
                (tid,))
            conn.commit()
            criar_tabelas()  # segunda subida: nada muda
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM trabalhador_presenca_correcao").fetchone()["c"]
            assert_eq(n, 1, "segunda execução não duplica nem apaga auditoria")
            # CHECK de operação e motivo obrigatório
            import psycopg2
            try:
                conn.execute(
                    """INSERT INTO trabalhador_presenca_correcao
                       (trabalhador_id, data_fato, operacao, motivo)
                       VALUES (%s, '2026-06-01', 'pintar_o_muro', 'x')""", (tid,))
                raise AssertionError("CHECK de operacao não barrou valor inválido")
            except psycopg2.Error:
                conn.rollback()
            try:
                conn.execute(
                    """INSERT INTO trabalhador_presenca_correcao
                       (trabalhador_id, data_fato, operacao, motivo)
                       VALUES (%s, '2026-06-01', 'anulacao', '')""", (tid,))
                raise AssertionError("motivo vazio passou no CHECK")
            except psycopg2.Error:
                conn.rollback()
            # FK RESTRICT: trabalhador com auditoria não é apagável em silêncio
            try:
                conn.execute("DELETE FROM trabalhadores WHERE id = %s", (tid,))
                raise AssertionError("FK deveria restringir a exclusão")
            except psycopg2.Error:
                conn.rollback()


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
                fn()
            else:
                with conectar() as conn:
                    fn(Ctx(conn))
            ok += 1
            print(f"  PASS  {nome}")
        except Exception as e:
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{ok}/{total} testes passaram (correções TASK_004)")
    with contextlib.suppress(Exception):
        remover_se_nosso(MIG_DB, tp.TOKEN)
    limpar_banco()
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
