#!/usr/bin/env python3
"""Suíte de testes da regra do PLANO ÚNICO + encerramento por 2 faltas.

Regras cobertas (decisão do proprietário, 2026-10):
  • 2 faltas consecutivas vencidas encerram o plano automaticamente
    (rota de encerrar o dia e rota de falta do médium);
  • um novo Acolhimento Fraterno com plano cria plano novo e INVALIDA os
    planos ativos anteriores da pessoa;
  • as demais portas de criação (/agenda/novo, planos do médium) recusam
    pessoa que já tem plano ativo.

Roda contra o banco descartável de teste (trava em test_tres_passes).

Uso (recomendado):
    bash tests/rodar_testes.sh
"""
import sys
import traceback
from datetime import date, timedelta

# A importação abaixo executa a TRAVA DE ISOLAMENTO na ordem obrigatória
# (configurar → validar → importar a aplicação) — ver test_tres_passes.
import test_tres_passes as tp
from test_tres_passes import (
    Ctx, assert_eq, preparar_banco, limpar_banco,
    _http, _login, _garantir_admin, _plano_da_pessoa, _planos_da_pessoa,
    _criar_pela_agenda, _salvar_fraterno,
)

conectar = tp.conectar
banco = tp.banco

RESULTADOS = []


def teste(nome):
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


def _dias_atendimento_de_hoje(ctx):
    """Segunda/terça/quarta + o dia da semana de hoje — as rotas que operam
    "hoje" precisam que hoje seja dia de atendimento."""
    return tuple(sorted({0, 1, 2, date.today().weekday()}))


def _status_plano(ctx, plano_id):
    ctx.conn.commit()
    return ctx.conn.execute(
        "SELECT status FROM planos_tratamento WHERE id = %s", (plano_id,)
    ).fetchone()["status"]


def _pendentes(ctx, plano_id):
    ctx.conn.commit()
    return ctx.conn.execute(
        "SELECT count(*) AS c FROM agendamentos WHERE plano_id=%s AND status='agendado'",
        (plano_id,),
    ).fetchone()["c"]


def _ag(ctx, plano_id, data, status):
    return ctx.conn.execute(
        "INSERT INTO agendamentos (plano_id, data, status, requer_passe, encaixe) "
        "VALUES (%s,%s,%s,1,0) RETURNING id",
        (plano_id, data, status),
    ).fetchone()["id"]


def _sessoes_dia(ctx, plano_id, data):
    ctx.conn.commit()
    return ctx.conn.execute(
        "SELECT status FROM agendamentos WHERE plano_id=%s AND data=%s",
        (plano_id, data),
    ).fetchone()["status"]


HOJE = date.today()
ONTEM_ATENDIMENTO = (HOJE - timedelta(days=7)).isoformat()  # mesmo dia da semana
SEMANA_QUE_VEM = (HOJE + timedelta(days=7)).isoformat()


# ── Regra das 2 faltas ───────────────────────────────────────────────────────

@teste("G1 rota do médium: 2ª falta consecutiva encerra o plano e cancela pendentes")
def g1(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med, pes = ctx.medium(), ctx.pessoa("g1")
    p1 = ctx.plano(med, pes, 5, "semanal", 3, inicio=HOJE - timedelta(days=28))
    ss = ctx.sessoes(p1)
    # faltas nas DUAS sessões vencidas MAIS RECENTES (a regra olha de trás
    # para frente; marcar as mais antigas deixaria uma vencida 'agendado')
    vencidas = sorted(
        [s for s in ss if s["data"] <= HOJE.isoformat()],
        key=lambda s: s["data"], reverse=True,
    )[:2]
    pendentes = _pendentes(ctx, p1)
    for s in vencidas:
        ag_id = ctx.conn.execute(
            "SELECT id FROM agendamentos WHERE plano_id=%s AND data=%s",
            (p1, s["data"]),
        ).fetchone()["id"]
        ctx.conn.commit()
        status, _ = _http(
            "POST", f"/cadastros/mediuns/{med}/planos/{p1}/agenda/{ag_id}/falta"
        )
        assert_eq(status, 303, "rota de falta deveria responder 303")
    ctx.conn.commit()
    assert_eq(_status_plano(ctx, p1), "cancelado", "plano após 2 faltas")
    assert_eq(_pendentes(ctx, p1), 0, "pendentes cancelados")
    assert pendentes > 0, "fixture sem pendentes — teste não prova nada"


@teste("G2 encerrar_dia: última sessão agendada de hoje vira falta e fecha o plano")
def g2(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med, pes = ctx.medium(), ctx.pessoa("g2")
    ctx.conn.commit()
    plano_id = ctx.conn.execute(
        """INSERT INTO planos_tratamento
           (medium_id, sessoes_total, data_inicio, frequencia, sessoes_com_passe, status)
           VALUES (%s, 5, %s, 'semanal', 3, 'ativo') RETURNING id""",
        (med, ONTEM_ATENDIMENTO),
    ).fetchone()["id"]
    _ag(ctx, plano_id, ONTEM_ATENDIMENTO, "faltou")
    ag_hoje = _ag(ctx, plano_id, HOJE.isoformat(), "agendado")
    dia_id = ctx.dia(HOJE.isoformat())
    ctx.conn.commit()
    status, _ = _http("POST", "/dia/encerrar")
    assert_eq(status, 303, "encerrar deveria responder 303")
    ctx.conn.commit()
    st = ctx.conn.execute(
        "SELECT status FROM agendamentos WHERE id=%s", (ag_hoje,)
    ).fetchone()["status"]
    assert_eq(st, "faltou", "sessão de hoje marcada como falta")
    assert_eq(_status_plano(ctx, plano_id), "cancelado", "plano encerrado por 2 faltas")


@teste("G3 falta alternada com realização não encerra (precisa ser consecutiva)")
def g3(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med, pes = ctx.medium(), ctx.pessoa("g3")
    p1 = ctx.plano(med, pes, 5, "semanal", 3, inicio=HOJE - timedelta(days=28))
    ctx.conn.execute(
        "UPDATE agendamentos SET status='faltou' "
        "WHERE plano_id=%s AND data <= %s AND id NOT IN "
        "(SELECT id FROM agendamentos WHERE plano_id=%s AND data <= %s "
        " ORDER BY data DESC, id DESC LIMIT 2)",
        (p1, HOJE.isoformat(), p1, HOJE.isoformat()),
    )
    vencidas = ctx.conn.execute(
        "SELECT id, data FROM agendamentos WHERE plano_id=%s AND data <= %s "
        "ORDER BY data DESC, id DESC LIMIT 2",
        (p1, HOJE.isoformat()),
    ).fetchall()
    ctx.conn.execute(
        "UPDATE agendamentos SET status='faltou' WHERE id=%s", (vencidas[0]["id"],)
    )  # mais recente falta; a segunda mais recente fica 'agendado'
    ctx.conn.commit()
    assert_eq(banco.faltas_consecutivas_encerram(ctx.conn, p1, HOJE.isoformat()),
              False, "faltas não consecutivas não devem encerrar")
    ctx.conn.commit()
    assert_eq(_status_plano(ctx, p1), "ativo")


@teste("G4 agendamentos futuros não contam para a regra")
def g4(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med, pes = ctx.medium(), ctx.pessoa("g4")
    p1 = ctx.plano(med, pes, 3, "semanal", 3, inicio=HOJE)  # 1ª hoje, 2ª/3ª futuro
    _ag(ctx, p1, ONTEM_ATENDIMENTO, "faltou")               # falta vencida
    ctx.conn.commit()
    assert_eq(banco.faltas_consecutivas_encerram(ctx.conn, p1, HOJE.isoformat()),
              False, "uma falta vencida + futuro agendado não encerra")
    ctx.conn.commit()
    assert_eq(_status_plano(ctx, p1), "ativo")


@teste("G5 unidade: exatamente 2 faltas vencidas encerram (fonte única)")
def g5(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med, pes = ctx.medium(), ctx.pessoa("g5")
    p1 = ctx.plano(med, pes, 4, "semanal", 3, inicio=HOJE - timedelta(days=28))
    vencidas = ctx.conn.execute(
        "SELECT id FROM agendamentos WHERE plano_id=%s AND data <= %s "
        "ORDER BY data DESC, id DESC LIMIT 2",
        (p1, HOJE.isoformat()),
    ).fetchall()
    assert_eq(len(vencidas), 2, "fixture: plano precisa de 2 sessões vencidas")
    for v in vencidas[:2]:
        ctx.conn.execute("UPDATE agendamentos SET status='faltou' WHERE id=%s",
                         (v["id"],))
    ctx.conn.commit()
    assert_eq(banco.faltas_consecutivas_encerram(ctx.conn, p1, HOJE.isoformat()),
              True, "2 faltas consecutivas devem encerrar")
    ctx.conn.commit()
    assert_eq(_status_plano(ctx, p1), "cancelado")


# ── Fraterno: plano novo invalida os ativos anteriores ───────────────────────

@teste("F1 novo acolhimento cria plano e invalida o plano ativo anterior")
def f1(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med, pes = ctx.medium(), ctx.pessoa("f1")
    velho = ctx.plano(med, pes, 5, "semanal", 3, inicio=HOJE - timedelta(days=28))
    ctx.conn.commit()
    assert _pendentes(ctx, velho) > 0, "fixture: velho precisa ter pendentes"
    dia_id = ctx.dia()
    ck = ctx.checkin(dia_id, pes, "11")
    novo = _salvar_fraterno(ctx, ck, med, total=10, inicio=HOJE.isoformat())
    assert novo and novo != velho, f"plano novo deveria ser criado (obtido {novo})"
    assert_eq(_status_plano(ctx, velho), "cancelado", "antigo invalidado")
    assert_eq(_pendentes(ctx, velho), 0, "pendentes do antigo cancelados")
    assert_eq(_status_plano(ctx, novo), "ativo")


@teste("F2 reenvio da MESMA sessão de acolhimento não duplica nem cancela o próprio plano")
def f2(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med, pes = ctx.medium(), ctx.pessoa("f2")
    dia_id = ctx.dia()
    ck = ctx.checkin(dia_id, pes, "12")
    novo = _salvar_fraterno(ctx, ck, med, total=4, inicio=HOJE.isoformat())
    antes = _planos_da_pessoa(ctx, pes)
    _salvar_fraterno(ctx, ck, med, total=4, inicio=HOJE.isoformat())  # 2º POST
    assert_eq(_planos_da_pessoa(ctx, pes), antes, "reenvio não pode duplicar")
    assert_eq(_status_plano(ctx, novo), "ativo", "plano da própria sessão não se auto-invalida")


@teste("F3 acolhimento SEM médium não toca nos planos ativos")
def f3(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med, pes = ctx.medium(), ctx.pessoa("f3")
    velho = ctx.plano(med, pes, 3, "semanal", 3, inicio=HOJE - timedelta(days=14))
    ctx.conn.commit()
    dia_id = ctx.dia()
    ck = ctx.checkin(dia_id, pes, "13")
    _salvar_fraterno(ctx, ck, None)
    ctx.conn.commit()
    assert_eq(_status_plano(ctx, velho), "ativo", "sem plano novo, nada é invalidado")
    assert_eq(_planos_da_pessoa(ctx, pes), 1)


# ── Portas alternativas de criação: recusa com plano ativo ───────────────────

@teste("N1 /agenda/novo recusa pessoa que já tem plano ativo")
def n1(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med, pes = ctx.medium(), ctx.pessoa("n1a")
    ctx.plano(med, pes, 3, "semanal", 3, inicio=HOJE - timedelta(days=14))
    ctx.conn.commit()
    _criar_pela_agenda(ctx, total=2, pessoa=pes, espera_erro=True)
    assert_eq(_planos_da_pessoa(ctx, pes), 1, "nenhum plano extra criado")


@teste("N2 planos do médium recusa lote com pessoa em plano ativo (sem criação parcial)")
def n2(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med = ctx.medium()
    livre = ctx.pessoa("n2-livre")
    ocupada = ctx.pessoa("n2-ocupada")
    ctx.plano(med, ocupada, 3, "semanal", 3, inicio=HOJE - timedelta(days=14))
    ctx.conn.commit()
    def n_planos():
        ctx.conn.commit()
        return ctx.conn.execute("SELECT count(*) AS c FROM planos_tratamento").fetchone()["c"]
    antes = n_planos()
    status, _ = _http("POST", f"/cadastros/mediuns/{med}/planos/novo", [
        ("sessoes_total", "3"), ("frequencia", "semanal"),
        ("sessoes_com_passe", "3"), ("data_inicio", HOJE.isoformat()),
        ("pessoas_ids", str(livre)), ("pessoas_ids", str(ocupada)),
        ("next", f"/cadastros/mediuns/{med}/planos"),
    ])
    assert_eq(status, 303, "deve redirecionar com erro")
    assert_eq(n_planos(), antes, "nenhum plano criado — nada parcial")
    ctx.conn.commit()
    assert_eq(_planos_da_pessoa(ctx, livre), 0, "pessoa livre não entrou")


# ── Cancelar plano na ficha (somente admin) ─────────────────────────────────

def _http_nao_admin(method, path, data=None):
    """Chamada com sessão própria, logada como usuário NÃO-admin."""
    cj = tp.http.cookiejar.CookieJar()
    opener = tp.urllib.request.build_opener(
        tp.urllib.request.HTTPCookieProcessor(cj), tp._SemRedirect()
    )

    def chamar(p, d, m="POST"):
        body = tp.urllib.parse.urlencode(d).encode() if d else None
        req = tp.urllib.request.Request(tp.BASE + p, data=body, method=m)
        try:
            with opener.open(req) as r:
                return r.status
        except tp.urllib.error.HTTPError as e:
            return e.code

    chamar("/login", {"nome_usuario": "rec1", "senha": "teste123"})
    return chamar(path, data, method)


@teste("C1 cancelar plano: admin cancela o plano e os pendentes")
def c1(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    med, pes = ctx.medium(), ctx.pessoa("c1")
    p1 = ctx.plano(med, pes, 5, "semanal", 3, inicio=HOJE - timedelta(days=14))
    ctx.conn.commit()
    assert _pendentes(ctx, p1) > 0, "fixture: plano precisa de pendentes"
    status, _ = _http("POST", f"/cadastros/pessoas/{pes}/planos/{p1}/cancelar")
    assert_eq(status, 303, "rota deveria responder 303")
    assert_eq(_status_plano(ctx, p1), "cancelado", "plano cancelado pelo admin")
    assert_eq(_pendentes(ctx, p1), 0, "pendentes cancelados")


@teste("C2 cancelar plano: não-admin recebe 403 e o plano permanece ativo")
def c2(ctx: Ctx):
    ctx.reset(dias_semana=_dias_atendimento_de_hoje(ctx))
    from rotas.auth import hash_senha
    med, pes = ctx.medium(), ctx.pessoa("c2")
    p1 = ctx.plano(med, pes, 5, "semanal", 3, inicio=HOJE - timedelta(days=14))
    # usa a MESMA conexão da suíte: abrir uma segunda com a transação da
    # primeira ainda aberta trava em lock (deadlock no próprio teste)
    ctx.conn.execute(
        """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
           VALUES ('rec1','Teste Nao Admin',%s,1)
           ON CONFLICT (nome_usuario) DO NOTHING""",
        (hash_senha("teste123"),)
    )
    ctx.conn.commit()
    status = _http_nao_admin("POST", f"/cadastros/pessoas/{pes}/planos/{p1}/cancelar")
    assert_eq(status, 403, "não-admin deve receber acesso negado")
    assert_eq(_status_plano(ctx, p1), "ativo", "plano intacto após recusa")


# ── Execução ─────────────────────────────────────────────────────────────────

def main():
    preparar_banco()
    _garantir_admin()
    _login()
    total, ok = 0, 0
    for nome, fn in RESULTADOS:
        total += 1
        try:
            with conectar() as conn:
                fn(Ctx(conn))
            ok += 1
            print(f"  PASS  {nome}")
        except Exception as e:
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{ok}/{total} testes passaram (plano único)")
    limpar_banco()
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
