#!/usr/bin/env python3
"""Suíte de testes do ACESSO DA RECEPÇÃO AOS PLANOS DE ATENDIMENTO (2026-10-26).

Incidente de produção: usuária da Recepção recebia 403 em
/cadastros/mediuns/{id}/planos/{plano_id}/agenda porque a tela inteira estava
sob a permissão única "cadastros.mediuns" (administração de médiuns). Decisão
do dono: a Recepção acessa os planos e lança agendamentos pelo próprio grupo,
sem exigir Cambonas nem vínculo individual com o médium — SEM abrir
administração de médiuns, criação/alta/cancelamento de plano ou exclusões.

Cobertura:
  RP1  Recepção sem Cambonas: abre lista, planos e agenda; grava sessão no banco;
       menu mostra o caminho; ações administrativas não aparecem na tela.
  RP2  Recepção NÃO administra: 403 em novo/editar/toggle/alta/cancelar-plano e
       em criar plano; operações de agenda (cancelar/falta/reagendar) passam.
  RP3  Recepção + Cambonas (sem vínculo): múltiplos grupos não quebram o acesso.
  RP4  Cambona com seu vínculo: fluxo próprio intacto; sem o módulo novo, a tela
       /cadastros/mediuns continua negada (sem ampliação).
  RP5  Usuário sem autorização: 403 em tudo.
  RP6  Troca indevida de médium/plano na URL: leitura redireciona, gravação 403.
  RP7  Recepção somente-leitura: navega, mas POST de agenda é 403.
  RP8  Seed do grupo existente: `semear_permissoes.py --aplicar` concede à
       Recepção preexistente de forma idempotente, sem sobrescrever linhas.

Roda contra o banco descartável de teste (trava em test_tres_passes).

Uso (recomendado):
    bash tests/rodar_testes.sh
"""
import traceback
from datetime import date, timedelta

import sys

# A importação abaixo executa a TRAVA DE ISOLAMENTO na ordem obrigatória
# (configurar → validar → importar a aplicação) — ver test_tres_passes.
import test_tres_passes as tp
from test_tres_passes import (
    Ctx, assert_eq, preparar_banco, limpar_banco,
    _garantir_admin, _login,
)
from test_permissoes import _nova_sessao, _grupo, _perm, _usuario_em_grupo

conectar = tp.conectar
BASE = tp.BASE

RESULTADOS = []
HOJE = date.today()

MODULO_PLANOS = "cadastros.mediuns.planos"


def teste(nome):
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


# ── Apoio ─────────────────────────────────────────────────────────────────────

def _medium(ctx, nome):
    return ctx.conn.execute(
        "INSERT INTO mediuns (nome_completo, ativo, vagas_dia) VALUES (%s, 1, 10) "
        "RETURNING id", (nome,)
    ).fetchone()["id"]


def _recepcao(ctx, nome_grupo="Recepção", nome_usuario="recep1",
              ler=True, escrever=True):
    """Grupo com o módulo novo (sem nenhum outro) + usuário do grupo."""
    g = _grupo(ctx, nome_grupo)
    _perm(ctx, g, MODULO_PLANOS, ler=ler, escrever=escrever)
    uid = _usuario_em_grupo(ctx, nome_usuario, g)
    ctx.conn.commit()
    return g, nome_usuario, uid


def _dias_atendimento(ctx):
    ctx.conn.commit()
    return {r["dia_semana"] for r in ctx.conn.execute(
        "SELECT dia_semana FROM dias_atendimento").fetchall()}


def _datas_da_pessoa(ctx, pessoa_id):
    ctx.conn.commit()
    return {r["data"] for r in ctx.conn.execute(
        """SELECT a.data FROM agendamentos a
           JOIN planos_tratamento pt ON pt.id = a.plano_id
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           WHERE pp.pessoa_id = %s""", (pessoa_id,)
    ).fetchall()}


def _proxima_data_livre(ctx, pessoa_id):
    permitidos = _dias_atendimento(ctx)
    ocupadas = _datas_da_pessoa(ctx, pessoa_id)
    for i in range(0, 40):
        d = HOJE + timedelta(days=i)
        if d.weekday() in permitidos and d.isoformat() not in ocupadas:
            return d.isoformat()
    raise AssertionError("sem dia de atendimento livre em 40 dias")


def _sessoes(ctx, plano_id):
    ctx.conn.commit()
    return ctx.conn.execute(
        "SELECT id, data, status FROM agendamentos WHERE plano_id=%s ORDER BY data, id",
        (plano_id,)
    ).fetchall()


def _cenario(ctx):
    """1 médium, 1 pessoa fictícia, 1 plano ativo com agenda gerada."""
    ctx.reset(dias_semana=(0, 2))
    med = _medium(ctx, "Médium Teste")
    pes = ctx.pessoa("atendida ficticia")
    plano = ctx.plano(med, pes, 4, "semanal", 3, inicio=HOJE)
    ctx.conn.commit()
    return med, pes, plano


# ── RP1: recepção sem Cambonas acessa e grava ─────────────────────────────────

@teste("RP1 Recepção (sem Cambonas, sem vínculo): abre telas e grava sessão")
def rp1(ctx: Ctx):
    med, pes, plano = _cenario(ctx)
    _, usuario, _ = _recepcao(ctx)
    sessao = _nova_sessao(usuario, "teste123")

    status, html = sessao("/menu")
    assert_eq(status, 200, "menu acessível")
    assert 'href="/cadastros/mediuns"' in html, "menu precisa dar o caminho até os planos"
    assert "Planos de atendimento" in html, "card específico da Recepção"

    status, html = sessao("/cadastros/mediuns")
    assert_eq(status, 200, "lista de médiuns para navegar")
    assert "Novo médium" not in html, "sem administração na lista"

    status, html = sessao(f"/cadastros/mediuns/{med}/planos")
    assert_eq(status, 200, "lista de planos")
    assert "atendida ficticia" in html, "plano da pessoa visível"
    assert "Criar plano e gerar agenda" not in html, "sem criação de plano (clínica)"

    status, html = sessao(f"/cadastros/mediuns/{med}/planos/{plano}/agenda")
    assert_eq(status, 200, "tela de agenda abre pelo grupo Recepção")

    antes = len(_sessoes(ctx, plano))
    nova = _proxima_data_livre(ctx, pes)
    status, _ = sessao.post(f"/cadastros/mediuns/{med}/planos/{plano}/agenda/novo",
                            {"data": nova, "requer_passe": 0, "encaixe": 0})
    assert_eq(status, 303, "lançamento deveria redirecionar")
    depois = _sessoes(ctx, plano)
    assert_eq(len(depois), antes + 1, "exatamente 1 sessão gravada em banco")
    assert nova in {s["data"] for s in depois}, "sessão na data pedida"


# ── RP2: restrições preservadas ───────────────────────────────────────────────

@teste("RP2 Recepção não administra médiuns nem plano; operações de agenda passam")
def rp2(ctx: Ctx):
    med, pes, plano = _cenario(ctx)
    _, usuario, _ = _recepcao(ctx, nome_usuario="recep2")
    sessao = _nova_sessao(usuario, "teste123")
    ag = _sessoes(ctx, plano)[0]

    # Administração de médiuns: negada
    assert_eq(sessao("/cadastros/mediuns/novo")[0], 403, "form novo médium")
    assert_eq(sessao.post("/cadastros/mediuns/novo", {"nome_completo": "x", "vagas_dia": 10})[0],
              403, "criar médium")
    assert_eq(sessao(f"/cadastros/mediuns/{med}/editar")[0], 403, "form editar médium")
    assert_eq(sessao.post(f"/cadastros/mediuns/{med}/toggle-ativo")[0], 403, "toggle ativo")
    # Clínica do plano: negada
    assert_eq(sessao.post(f"/cadastros/mediuns/{med}/planos/novo",
                          {"sessoes_total": 6, "pessoas_ids": pes})[0],
              403, "criar plano")
    assert_eq(sessao.post(f"/cadastros/mediuns/{med}/planos/{plano}/alta")[0],
              403, "dar alta")
    assert_eq(sessao.post(f"/cadastros/mediuns/{med}/planos/{plano}/cancelar")[0],
              403, "cancelar plano")
    ctx.conn.commit()
    st = ctx.conn.execute("SELECT status FROM planos_tratamento WHERE id=%s",
                          (plano,)).fetchone()["status"]
    assert_eq(st, "ativo", "plano permanece ativo apesar das tentativas")

    # Operações de agenda: permitidas
    status, _ = sessao.post(f"/cadastros/mediuns/{med}/planos/{plano}/agenda/{ag['id']}/cancelar")
    assert_eq(status, 303, "cancelar agendamento")
    ctx.conn.commit()
    st = ctx.conn.execute("SELECT status FROM agendamentos WHERE id=%s",
                          (ag["id"],)).fetchone()["status"]
    assert_eq(st, "cancelado", "agendamento cancelado por baixo dos panos")

    ag2 = _sessoes(ctx, plano)[1]
    nova = _proxima_data_livre(ctx, pes)
    status, _ = sessao.post(f"/cadastros/mediuns/{med}/planos/{plano}/agenda/{ag2['id']}/reagendar",
                            {"nova_data": nova})
    assert_eq(status, 303, "reagendar")

    ag3 = _sessoes(ctx, plano)[2]
    status, _ = sessao.post(f"/cadastros/mediuns/{med}/planos/{plano}/agenda/{ag3['id']}/falta")
    assert_eq(status, 303, "registrar falta")


# ── RP3: múltiplos grupos ─────────────────────────────────────────────────────

@teste("RP3 Recepção + Cambonas (sem vínculo) mantém acesso pelo grupo Recepção")
def rp3(ctx: Ctx):
    med, pes, plano = _cenario(ctx)
    g_rec, usuario, uid = _recepcao(ctx, nome_grupo="Recepção Mix",
                                    nome_usuario="recep3")
    g_cam = _grupo(ctx, "Cambonas Mix")
    _perm(ctx, g_cam, "agenda.medium", ler=True, escrever=True)
    _usuario_em_grupo(ctx, usuario, g_cam)     # mesmo usuário nos dois grupos
    ctx.conn.commit()

    sessao = _nova_sessao(usuario, "teste123")
    status, _ = sessao(f"/cadastros/mediuns/{med}/planos/{plano}/agenda")
    assert_eq(status, 200, "agenda abre sem nenhum vínculo atendentes_mediuns")
    nova = _proxima_data_livre(ctx, pes)
    status, _ = sessao.post(f"/cadastros/mediuns/{med}/planos/{plano}/agenda/novo",
                            {"data": nova})
    assert_eq(status, 303, "lançamento aceito com múltiplos grupos")
    assert nova in {s["data"] for s in _sessoes(ctx, plano)}, "sessão gravada"


# ── RP4: cambona com vínculo — sem ampliação ─────────────────────────────────

@teste("RP4 Cambona com vínculo: lança na própria tela; telas de médiuns seguem negadas")
def rp4(ctx: Ctx):
    med, pes, plano = _cenario(ctx)
    g = _grupo(ctx, "Cambonas Só")
    _perm(ctx, g, "agenda.medium", ler=True, escrever=True)
    uid = _usuario_em_grupo(ctx, "cambona1", g)
    ctx.conn.execute(
        "INSERT INTO atendentes_mediuns (atendente_id, medium_id) VALUES (%s,%s) "
        "ON CONFLICT DO NOTHING", (uid, med))
    ctx.conn.commit()

    sessao = _nova_sessao("cambona1", "teste123")
    status, _ = sessao("/agenda/medium")
    assert_eq(status, 200, "tela própria da cambona com vínculo")
    nova = _proxima_data_livre(ctx, pes)
    status, _ = sessao.post("/agenda/medium/lancar",
                            {"pessoa_id": pes, "medium_id": med, "data": nova})
    assert_eq(status, 303, "lançamento pelo vínculo continua funcionando")
    assert nova in {s["data"] for s in _sessoes(ctx, plano)}, "sessão no plano"

    # Sem cadastros.mediuns.planos, a tela de planos continua negada (sem ampliação)
    assert_eq(sessao(f"/cadastros/mediuns/{med}/planos")[0], 403, "planos negados à cambona")
    assert_eq(sessao(f"/cadastros/mediuns/{med}/planos/{plano}/agenda")[0], 403, "agenda negada")


# ── RP5: sem autorização ──────────────────────────────────────────────────────

@teste("RP5 usuário sem nenhuma dessas permissões: 403 em todas as rotas")
def rp5(ctx: Ctx):
    med, pes, plano = _cenario(ctx)
    g = _grupo(ctx, "Só Checkin")
    _perm(ctx, g, "checkin.trabalhadores", ler=True, escrever=True)
    _usuario_em_grupo(ctx, "semacesso", g)
    ctx.conn.commit()

    sessao = _nova_sessao("semacesso", "teste123")
    for path in ["/cadastros/mediuns", f"/cadastros/mediuns/{med}/planos",
                 f"/cadastros/mediuns/{med}/planos/{plano}/agenda"]:
        assert_eq(sessao(path)[0], 403, f"GET {path}")
    assert_eq(sessao.post(f"/cadastros/mediuns/{med}/planos/{plano}/agenda/novo",
                          {"data": HOJE.isoformat()})[0], 403, "POST sem permissão")


# ── RP6: troca indevida médium/plano na URL ───────────────────────────────────

@teste("RP6 plano de outro médium na URL: leitura redireciona, gravação 403")
def rp6(ctx: Ctx):
    med_a, pes, plano_a = _cenario(ctx)
    med_b = _medium(ctx, "Outro Médium")
    ctx.conn.commit()
    _, usuario, _ = _recepcao(ctx, nome_grupo="Recepção URL", nome_usuario="recep6")
    sessao = _nova_sessao(usuario, "teste123")

    status, _ = sessao(f"/cadastros/mediuns/{med_b}/planos/{plano_a}/agenda")
    assert_eq(status, 303, "leitura não vaza plano de outro médium")

    antes = len(_sessoes(ctx, plano_a))
    status, _ = sessao.post(f"/cadastros/mediuns/{med_b}/planos/{plano_a}/agenda/novo",
                            {"data": _proxima_data_livre(ctx, pes)})
    assert_eq(status, 403, "gravação com plano de outro médium é negada")
    assert_eq(len(_sessoes(ctx, plano_a)), antes, "nenhuma sessão inserida pela URL trocada")

    ag = _sessoes(ctx, plano_a)[0]
    status, _ = sessao.post(f"/cadastros/mediuns/{med_b}/planos/{plano_a}/agenda/{ag['id']}/cancelar")
    assert_eq(status, 403, "cancelar pela URL trocada é negado")
    ctx.conn.commit()
    st = ctx.conn.execute("SELECT status FROM agendamentos WHERE id=%s",
                          (ag["id"],)).fetchone()["status"]
    assert_eq(st, "agendado", "agendamento intacto")


# ── RP7: somente-leitura não grava ────────────────────────────────────────────
@teste("RP7 Recepção somente-leitura: navega mas POST de agenda é 403")
def rp7(ctx: Ctx):
    med, pes, plano = _cenario(ctx)
    _, usuario, _ = _recepcao(ctx, nome_grupo="Recepção Leitura",
                              nome_usuario="recep7", ler=True, escrever=False)
    sessao = _nova_sessao(usuario, "teste123")
    assert_eq(sessao(f"/cadastros/mediuns/{med}/planos/{plano}/agenda")[0],
              200, "leitura aberta")
    antes = len(_sessoes(ctx, plano))
    status, _ = sessao.post(f"/cadastros/mediuns/{med}/planos/{plano}/agenda/novo",
                            {"data": _proxima_data_livre(ctx, pes)})
    assert_eq(status, 403, "POST sem escrever")
    assert_eq(len(_sessoes(ctx, plano)), antes, "nada gravado")


# ── RP8: seed do grupo EXISTENTE, idempotente ────────────────────────────────

@teste("RP8 seed: concede ao grupo Recepção existente sem sobrescrever nada")
def rp8(ctx: Ctx):
    """O `semear_permissoes.py --aplicar` roda contra o banco desta execução e
    vale para instalação já em uso (grupo preexistente), não só para instalações
    novas: inserir onde falta, nunca alterar linhas existentes."""
    import os
    import subprocess

    ctx.reset(dias_semana=(0, 2))
    g = _grupo(ctx, "Recepção")                     # grupo EXISTENTE
    _perm(ctx, g, "checkin.trabalhadores", ler=True, escrever=True)  #seed histórico
    g2 = _grupo(ctx, "Grupo Parcial")
    _perm(ctx, g2, MODULO_PLANOS, ler=True, escrever=False)          # linha existente
    ctx.conn.commit()

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    saida = subprocess.run([sys.executable, "semear_permissoes.py", "--aplicar"],
                           cwd=raiz, env={**os.environ},
                           capture_output=True, text=True)
    assert_eq(saida.returncode, 0, f"seed deveria rodar limpo: {saida.stdout}{saida.stderr}")
    ctx.conn.commit()

    def _linha(grupo_id):
        return ctx.conn.execute(
            "SELECT ler, escrever, apagar, COUNT(*) OVER () AS n FROM grupos_permissoes "
            "WHERE grupo_id = %s AND modulo = %s", (grupo_id, MODULO_PLANOS)).fetchone()

    l = _linha(g)
    assert l is not None, "seed precisa conceder ao grupo Recepção já existente"
    assert_eq((l["ler"], l["escrever"], l["apagar"]), (True, True, False),
              "concessão da decisão: ler+escrever, nunca apagar")
    assert_eq(_linha(g2)["escrever"], False, "linha existente não é sobrescrita")
    ck = ctx.conn.execute("SELECT ler, escrever FROM grupos_permissoes "
                          "WHERE grupo_id = %s AND modulo = 'checkin.trabalhadores'",
                          (g,)).fetchone()
    assert_eq((ck["ler"], ck["escrever"]), (True, True), "permissões preexistentes intactas")

    # segunda execução: idempotência (mesma linha única, mesmos valores)
    saida2 = subprocess.run([sys.executable, "semear_permissoes.py", "--aplicar"],
                            cwd=raiz, env={**os.environ},
                            capture_output=True, text=True)
    assert_eq(saida2.returncode, 0, f"seed reprocessado deveria ser limpo: {saida2.stderr}")
    ctx.conn.commit()
    l2 = _linha(g)
    assert_eq(l2["n"], 1, "nenhuma linha duplicada na segunda execução")
    assert_eq((l2["ler"], l2["escrever"], l2["apagar"]), (True, True, False),
              "valores estáveis entre execuções")


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
    print(f"\n{ok}/{total} testes passaram (recepção nos planos)")
    limpar_banco()
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
