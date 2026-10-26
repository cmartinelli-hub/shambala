#!/usr/bin/env python3
"""REPRODUÇÃO do incidente (2026-10-26): bloqueio em /agenda/novo depois de
cancelar as sessões — mesmo caminho de interface do dono.

Cenário: criar plano pela Agenda, cancelar TODAS as sessões pela agenda do
plano, tentar agendar de novo. Espera reproduzir a mensagem
"Pessoa já possui plano ativo…" e provar que quem permanece ativo é o PLANO
(sessions canceladas não encerram o plano).

Uso: SHAMBALA_SUITES=repro_incidente_agenda_plano_sem_sessoes.py bash tests/rodar_testes.sh
(Não está em `SUITE_PADRAO`: é prova de reprodução de incidente, rodada à parte.)
"""
import traceback
from datetime import date, timedelta

import test_tres_passes as tp
from test_tres_passes import Ctx, assert_eq, preparar_banco, limpar_banco, _garantir_admin, _login, _http

conectar = tp.conectar

RESULTADOS = []


def teste(nome):
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


def _datas_livres(ctx, pessoa_id, n, a_partir_de=None):
    permitidos = {r["dia_semana"] for r in ctx.conn.execute(
        "SELECT dia_semana FROM dias_atendimento").fetchall()}
    ctx.conn.commit()
    ocupadas = {r["data"] for r in ctx.conn.execute(
        """SELECT a.data FROM agendamentos a
           JOIN planos_tratamento pt ON pt.id = a.plano_id
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           WHERE pp.pessoa_id = %s""", (pessoa_id,)).fetchall()}
    d = a_partir_de or date.today()
    out, i = [], 0
    while len(out) < n and i < 60:
        dia = d + timedelta(days=i)
        i += 1
        if dia.weekday() in permitidos and dia.isoformat() not in ocupadas:
            out.append(dia.isoformat())
    assert_eq(len(out), n, "datas de atendimento livres")
    return out


def _estado(ctx, pessoa_id):
    ctx.conn.commit()
    planos = ctx.conn.execute(
        """SELECT pt.id, pt.medium_id, pt.status FROM planos_tratamento pt
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           WHERE pp.pessoa_id = %s ORDER BY pt.id""", (pessoa_id,)).fetchall()
    sessoes = ctx.conn.execute(
        """SELECT a.id, a.status FROM agendamentos a
           JOIN planos_tratamento pt ON pt.id = a.plano_id
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           WHERE pp.pessoa_id = %s ORDER BY a.id""", (pessoa_id,)).fetchall()
    return planos, sessoes


def _post_agenda_novo(pessoa_id, med, data):
    return _http("POST", "/agenda/novo", {
        "pessoa_id": str(pessoa_id), "data": data, "medium_id": str(med),
        "data_de": "", "data_ate": "", "dia_semana": "-1",
        "pessoa_nome": "", "qtd_sessoes": "1", "frequencia": "semanal"})


@teste("RB1 fluxo do dono: plano pela Agenda → cancelar TODAS as sessões → novo agendamento é BLOQUEADO")
def rb1(ctx):
    ctx.reset(dias_semana=(0, 2))
    med = ctx.medium()
    pes = ctx.pessoa("atendida ficticia")
    ctx.conn.commit()
    d0 = _datas_livres(ctx, pes, 1)[0]

    st, _ = _post_agenda_novo(pes, med, d0)
    assert_eq(st, 303, "criação pela Agenda")
    planos, sessoes = _estado(ctx, pes)
    assert_eq(len(planos), 1, "1 plano criado")
    plano, medium_plano, status = planos[0]["id"], planos[0]["medium_id"], planos[0]["status"]
    assert_eq(status, "ativo", "plano ativo após criação")
    assert_eq([s["status"] for s in sessoes], ["agendado"], "1 sessão agendada")

    # Interface usada para "remover" a sessão: cancelar na agenda do plano
    st, _ = _http("POST", f"/cadastros/mediuns/{medium_plano}/planos/{plano}/agenda/{sessoes[0]['id']}/cancelar")
    assert_eq(st, 303, "cancelar sessão")
    planos, sessoes = _estado(ctx, pes)
    assert_eq(sessoes[0]["status"], "cancelado", "sessão ficou CANCELADA")
    assert_eq(planos[0]["status"], "ativo", "PLANO continua ativo (cancelar sessão ≠ encerrar plano)")

    # A Agenda não lista sessões canceladas — por isso "parece que não há mais nada"
    st, corpo = _http("GET", "/agenda")
    assert_eq(st, 200, "GET /agenda")
    assert_eq("atendida ficticia" in corpo, False, "pessoa some da listagem (sessão cancelada não é 'agendado')")

    # Tentativa do dono: agendar de novo → antes era BLOQUEIO seco; desde a
    # TASK_018 a Agenda apresenta o plano ativo de destino (página de
    # confirmação, 200) sem criar nada.
    d1 = _datas_livres(ctx, pes, 1)[0]
    st, corpo = _post_agenda_novo(pes, med, d1)
    assert_eq(st, 200, "página de confirmação do plano de destino")
    assert_eq("Confirmar destino do atendimento" in corpo, True, "tela de confirmação")
    planos2, sessoes2 = _estado(ctx, pes)
    assert_eq(len(sessoes2), 1, "NENHUMA sessão nova foi criada antes de confirmar")
    assert_eq(sessoes2[0]["status"], "cancelado", "a única sessão segue cancelada")


@teste("RB2 caminho que destrava (sem apagar nada): agenda do plano aceita encaixe com plano ativo")
def rb2(ctx):
    ctx.reset(dias_semana=(0, 2))
    med = ctx.medium()
    pes = ctx.pessoa("atendida ficticia")
    ctx.conn.commit()
    d0 = _datas_livres(ctx, pes, 1)[0]
    _post_agenda_novo(pes, med, d0)
    planos, sessoes = _estado(ctx, pes)
    plano, medium_plano = planos[0]["id"], planos[0]["medium_id"]
    _http("POST", f"/cadastros/mediuns/{medium_plano}/planos/{plano}/agenda/{sessoes[0]['id']}/cancelar")

    # Encaixe PELA AGENDA DO PLANO (mesma rota validada na TASK_017): deve passar
    d1 = _datas_livres(ctx, pes, 1)[0]
    from datetime import date as _d
    st, _ = _http("POST", f"/cadastros/mediuns/{medium_plano}/planos/{plano}/agenda/novo", {
        "data": d1, "requer_passe": "0", "encaixe": "1"})
    assert_eq(st, 303, "encaixe pela agenda do plano")
    _, sessoes = _estado(ctx, pes)
    assert_eq(sorted(s["status"] for s in sessoes), ["agendado", "cancelado"],
              "nova sessão agendada convive com a cancelada")


@teste("RB3 encerrar o plano (cancelar plano) destrava a Agenda — diferença para cancelar sessão")
def rb3(ctx):
    ctx.reset(dias_semana=(0, 2))
    med = ctx.medium()
    pes = ctx.pessoa("atendida ficticia")
    ctx.conn.commit()
    d0 = _datas_livres(ctx, pes, 1)[0]
    _post_agenda_novo(pes, med, d0)
    planos, sessoes = _estado(ctx, pes)
    plano, medium_plano = planos[0]["id"], planos[0]["medium_id"]
    _http("POST", f"/cadastros/mediuns/{medium_plano}/planos/{plano}/agenda/{sessoes[0]['id']}/cancelar")

    st, _ = _http("POST", f"/cadastros/mediuns/{medium_plano}/planos/{plano}/cancelar")
    assert_eq(st, 303, "cancelar plano (decisão clínica — Admin aqui)")
    planos, _ = _estado(ctx, pes)
    assert_eq(planos[0]["status"], "cancelado", "plano ENCERRADO")

    d1 = _datas_livres(ctx, pes, 1)[0]
    st, _ = _post_agenda_novo(pes, med, d1)
    assert_eq(st, 303, "novo agendamento")
    _, sessoes = _estado(ctx, pes)
    assert_eq(sum(1 for s in sessoes if s["status"] == "agendado"), 1,
              "Agenda volta a criar (novo plano com sessão agendada)")


@teste("RB4 correção TASK_018: destino apresentado por referência humana, com porta para o plano")
def rb4(ctx):
    ctx.reset(dias_semana=(0, 2))
    med = ctx.medium()
    med_nome = ctx.conn.execute("SELECT nome_completo FROM mediuns WHERE id=%s", (med,)).fetchone()["nome_completo"]
    pes = ctx.pessoa("atendida ficticia")
    ctx.conn.commit()
    d0 = _datas_livres(ctx, pes, 1)[0]
    _post_agenda_novo(pes, med, d0)
    planos, sessoes = _estado(ctx, pes)
    plano, medium_plano = planos[0]["id"], planos[0]["medium_id"]
    _http("POST", f"/cadastros/mediuns/{medium_plano}/planos/{plano}/agenda/{sessoes[0]['id']}/cancelar")

    d1 = _datas_livres(ctx, pes, 1)[0]
    st, corpo = _post_agenda_novo(pes, med, d1)
    assert_eq(st, 200, "confirmação de destino (sem bloqueio seco)")
    assert_eq(f"planos/{plano}/agenda" in corpo, True,
              "oferece a porta do plano ativo (admin pode abrir)")
    assert_eq("Abrir agenda deste plano" in corpo, True, "botão com referência ao plano")
    assert_eq(med_nome in corpo, True, "médium do plano informado na tela")
    assert_eq("iniciado em" in corpo, True, "data de início do plano informada")
    assert_eq(f"plano {plano}" in corpo, False, "id interno não vira instrução operacional")
    # Nada foi escrito até confirmar
    _, sessoes = _estado(ctx, pes)
    assert_eq(len(sessoes), 1, "nenhuma sessão criada pela simples apresentação")


def _q(s):
    import urllib.parse
    return urllib.parse.quote(s)


@teste("RB5 permissão respeitada: sem acesso às telas de plano, a oferta do link não aparece")
def rb5(ctx):
    from test_permissoes import _grupo, _perm, _usuario_em_grupo, _nova_sessao
    ctx.reset(dias_semana=(0, 2))
    med = ctx.medium()
    pes = ctx.pessoa("atendida ficticia")
    g = _grupo(ctx, "Agenda simples")
    _perm(ctx, g, "agenda", ler=True, escrever=True)
    _usuario_em_grupo(ctx, "agsimp", g)
    ctx.conn.commit()

    d0 = _datas_livres(ctx, pes, 1)[0]
    _post_agenda_novo(pes, med, d0)  # admin cria o plano ativo

    sess = _nova_sessao("agsimp", "teste123")  # retorna a função get (com .post)
    st, corpo = sess(f"/agenda?pessoa_id={pes}&erro={_q('x')}")
    assert_eq(st, 200, "agenda abre para o grupo simples")
    st, corpo = sess.post("/agenda/novo", {
        "pessoa_id": str(pes), "data": _datas_livres(ctx, pes, 1)[0],
        "medium_id": str(med), "data_de": "", "data_ate": "", "dia_semana": "-1",
        "pessoa_nome": "", "qtd_sessoes": "1", "frequencia": "semanal",
        "confirmar": "0", "plano_destino": "0"})
    assert_eq(st, 200, "o grupo simples também recebe a confirmação de destino")
    assert_eq("Abrir agenda deste plano" in corpo, False,
              "sem cadastros.mediuns(.planos) não se oferece a porta que ele não atravessa")


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
            print(f"  PASS  {nome}")
            ok += 1
        except Exception as e:
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{ok}/{total} testes passaram (reprodução do bloqueio)")
    limpar_banco()
    return 0 if ok == total else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
