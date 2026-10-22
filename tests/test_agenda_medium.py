#!/usr/bin/env python3
"""Suíte de testes da TELA DA AGENDA DO MÉDIUM (2026-10-22).

Cobertura do fluxo real da cambona (agenda de papel → sistema):
  • busca de pessoa devolvendo o médium do plano ativo (homônimos);
  • vínculo usuário↔médium (atendentes_mediuns) configurado na tela de Usuários;
  • o vínculo é o ESCOPO: só as agendas vinculadas são listadas e o lançamento
    fora delas é recusado (sem vínculo, a tela fica vazia com aviso);
  • permissão própria "agenda.medium" (negação por padrão);
  • POST /agenda/medium/lancar nos três casos: plano ativo no mesmo médium
    (sessão avulsa), sem plano (cria plano) e plano em outro médium (recusa);
  • atendidos do último dia de atendimento.

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
    _http, _login, _garantir_admin, _planos_da_pessoa,
)
from test_permissoes import _nova_sessao, _grupo, _perm, _usuario_em_grupo

conectar = tp.conectar
BASE = tp.BASE

RESULTADOS = []
HOJE = date.today()


def teste(nome):
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


# ── Apoio ─────────────────────────────────────────────────────────────────────

def _medium(ctx, nome, ativo=1):
    return ctx.conn.execute(
        "INSERT INTO mediuns (nome_completo, ativo, vagas_dia) VALUES (%s, %s, 10) "
        "RETURNING id", (nome, ativo)
    ).fetchone()["id"]


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
    """Próximo dia de atendimento (hoje ou depois) sem sessão da pessoa."""
    permitidos = _dias_atendimento(ctx)
    ocupadas = _datas_da_pessoa(ctx, pessoa_id)
    for i in range(0, 40):
        d = HOJE + timedelta(days=i)
        if d.weekday() in permitidos and d.isoformat() not in ocupadas:
            return d.isoformat()
    raise AssertionError("sem dia de atendimento livre em 40 dias")


def _proxima_data_atendimento(ctx):
    permitidos = _dias_atendimento(ctx)
    for i in range(0, 14):
        if (HOJE + timedelta(days=i)).weekday() in permitidos:
            return (HOJE + timedelta(days=i)).isoformat()
    raise AssertionError("nenhum dia de atendimento em 14 dias")


def _proximo_dia_nao_atendimento(ctx):
    permitidos = _dias_atendimento(ctx)
    for i in range(1, 14):
        d = HOJE + timedelta(days=i)
        if d.weekday() not in permitidos:
            return d.isoformat()
    raise AssertionError("todo dia é dia de atendimento")


def _vincular(ctx, usuario_id, medium_ids):
    """Vínculo usuário↔agenda do médium (mesmo efeito da tela de Usuários)."""
    for mid in medium_ids:
        ctx.conn.execute(
            "INSERT INTO atendentes_mediuns (atendente_id, medium_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (usuario_id, mid)
        )


def _grupo_cambona(ctx, mediums=(), nome="Cambonas", usuario="cambona"):
    """Grupo com a permissão própria + usuário do grupo (senha teste123).

    `mediums` são as agendas no escopo do usuário — sem vínculo a tela é vazia
    e o lançamento é recusado, então os casos de lançamento precisam informar.
    Retorna (grupo, usuário, usuário_id).
    """
    g = _grupo(ctx, nome)
    _perm(ctx, g, "agenda.medium", ler=True, escrever=True)
    uid = _usuario_em_grupo(ctx, usuario, g)
    _vincular(ctx, uid, mediums)
    ctx.conn.commit()
    return g, usuario, uid


def _location_pos(usuario, path, dados):
    """POST logado devolvendo (status, Location) — o aviso viaja na query do 303."""
    cj = tp.http.cookiejar.CookieJar()
    opener = tp.urllib.request.build_opener(
        tp.urllib.request.HTTPCookieProcessor(cj), tp._SemRedirect()
    )

    def _post(url, body):
        req = tp.urllib.request.Request(url, data=body, method="POST")
        try:
            with opener.open(req) as r:
                return r.status, r.headers.get("Location", "")
        except tp.urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location", "")

    _post(BASE + "/login",
          tp.urllib.parse.urlencode({"nome_usuario": usuario, "senha": "teste123"}).encode())
    return _post(BASE + path, tp.urllib.parse.urlencode(dados).encode())



def _plano_de(ctx, pessoa_id):
    ctx.conn.commit()
    return ctx.conn.execute(
        """SELECT pt.id, pt.medium_id, pt.status, pt.sessoes_total, pt.sessoes_com_passe
           FROM planos_tratamento pt
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           WHERE pp.pessoa_id = %s""", (pessoa_id,)
    ).fetchall()


def _sessoes(ctx, plano_id):
    ctx.conn.commit()
    return ctx.conn.execute(
        "SELECT data, status, requer_passe FROM agendamentos WHERE plano_id=%s "
        "ORDER BY data, id", (plano_id,)
    ).fetchall()


# ── CB1: busca com o médium do plano ativo ────────────────────────────────────

@teste("CB1 /buscar devolve o médium do plano ativo (e null sem plano)")
def cb1(ctx: Ctx):
    ctx.reset(dias_semana=(0, 2))
    med = _medium(ctx, "Eglaudio / Pai José")
    com_plano = ctx.pessoa("maria cristina souza")
    sem_plano = ctx.pessoa("maria cristina rocha")
    ctx.plano(med, com_plano, 3, "semanal", 3, inicio=HOJE)
    ctx.conn.commit()

    status, corpo = _http("GET", "/cadastros/pessoas/buscar?nome=maria+cristina&excluir=0")
    assert_eq(status, 200, "busca deveria responder 200")
    import json
    resultados = {r["nome"]: r for r in json.loads(corpo)}
    assert "maria cristina souza" in resultados, f"resultado ausente: {corpo}"
    assert_eq(resultados["maria cristina souza"]["medium"], "Eglaudio / Pai José",
              "médium do plano ativo")
    assert_eq(resultados["maria cristina rocha"]["medium"], None, "sem plano → null")


# ── CB2: permissão própria ────────────────────────────────────────────────────

@teste("CB2 sem agenda.medium: 403 na tela e no lançar; com permissão: 200")
def cb2(ctx: Ctx):
    ctx.reset(dias_semana=(0, 2))
    g = _grupo(ctx, "Cambonas")          # grupo SEM a permissão
    _usuario_em_grupo(ctx, "cambona", g)
    ctx.conn.commit()

    sessao = _nova_sessao("cambona", "teste123")
    status, _ = sessao("/agenda/medium")
    assert_eq(status, 403, "tela sem permissão")
    status, _ = sessao.post("/agenda/medium/lancar",
                            {"pessoa_id": 1, "medium_id": 1, "data": HOJE.isoformat()})
    assert_eq(status, 403, "lançar sem permissão")
    status, _ = sessao("/agenda/medium/situacao?pessoa_id=1&medium_id=1")
    assert_eq(status, 403, "situação sem permissão")

    _perm(ctx, g, "agenda.medium", ler=True, escrever=True)
    ctx.conn.commit()
    status, html = _nova_sessao("cambona", "teste123")("/agenda/medium")
    assert_eq(status, 200, "tela com permissão")
    assert "Agenda do Médium" in html, "tela não renderizou o título"
    status, _ = _nova_sessao("cambona", "teste123")("/agenda")
    assert_eq(status, 403, "agenda.medium NÃO libera a /agenda completa")


# ── CB3: plano ativo no mesmo médium → sessão avulsa ──────────────────────────

@teste("CB3 lançar com plano ativo do médium acrescenta 1 sessão sem criar plano")
def cb3(ctx: Ctx):
    ctx.reset(dias_semana=(0, 2))
    med = _medium(ctx, "Eglaudio")
    pes = ctx.pessoa("joana")
    plano = ctx.plano(med, pes, 3, "semanal", 3, inicio=HOJE)
    antes = len(_sessoes(ctx, plano))
    _grupo_cambona(ctx, [med])
    ctx.conn.commit()

    nova = _proxima_data_livre(ctx, pes)
    sessao = _nova_sessao("cambona", "teste123")
    status, _ = sessao.post("/agenda/medium/lancar", {
        "pessoa_id": pes, "medium_id": med, "data": nova,
    })
    assert_eq(status, 303, "lançamento deveria redirecionar")
    depois = _sessoes(ctx, plano)
    assert_eq(len(depois), antes + 1, "exatamente 1 sessão a mais")
    assert nova in {s["data"] for s in depois}, "sessão na data pedida"
    assert_eq(_planos_da_pessoa(ctx, pes), 1, "nenhum plano novo")


@teste("CB3b duplicidade na mesma data é recusada sem inserir")
def cb3b(ctx: Ctx):
    ctx.reset(dias_semana=(0, 2))
    med = _medium(ctx, "Eglaudio")
    pes = ctx.pessoa("joana")
    plano = ctx.plano(med, pes, 3, "semanal", 3, inicio=HOJE)
    ja_existe = _sessoes(ctx, plano)[0]["data"]
    antes = len(_sessoes(ctx, plano))
    _grupo_cambona(ctx, [med])
    ctx.conn.commit()

    status, _ = _nova_sessao("cambona", "teste123").post("/agenda/medium/lancar", {
        "pessoa_id": pes, "medium_id": med, "data": ja_existe,
    })
    assert_eq(status, 303, "recusa via redirect com erro")
    assert_eq(len(_sessoes(ctx, plano)), antes, "nenhuma sessão duplicada")


# ── CB4: sem plano → cria plano com piso de passes ────────────────────────────

@teste("CB4 lançar sem plano cria plano ativo com agenda e piso de 3 passes")
def cb4(ctx: Ctx):
    ctx.reset(dias_semana=(0, 2))
    med = _medium(ctx, "Eglaudio")
    pes = ctx.pessoa("karla")
    _grupo_cambona(ctx, [med])
    ctx.conn.commit()

    inicio = _proxima_data_atendimento(ctx)
    status, _ = _nova_sessao("cambona", "teste123").post("/agenda/medium/lancar", {
        "pessoa_id": pes, "medium_id": med, "data": inicio,
        "qtd_sessoes": 4, "frequencia": "semanal",
    })
    assert_eq(status, 303, "lançamento deveria redirecionar")
    planos = _plano_de(ctx, pes)
    assert_eq(len(planos), 1, "um plano criado")
    assert_eq(planos[0]["status"], "ativo")
    assert_eq(planos[0]["medium_id"], med, "plano no médium escolhido")
    assert_eq(len(_sessoes(ctx, planos[0]["id"])), 4, "4 sessões geradas")
    assert_eq(sum(1 for s in _sessoes(ctx, planos[0]["id"]) if s["requer_passe"]), 3,
              "piso de 3 passes do centro")
    assert inicio in {s["data"] for s in _sessoes(ctx, planos[0]["id"])}, \
        "primeira sessão no dia lançado"


# ── CB5: plano ativo em OUTRO médium ─────────────────────────────────────────

@teste("CB5 pessoa com plano de outro médium é recusada, sem criar nada")
def cb5(ctx: Ctx):
    ctx.reset(dias_semana=(0, 2))
    med_a = _medium(ctx, "Eglaudio")
    med_b = _medium(ctx, "Marta")
    pes = ctx.pessoa("roque")
    ctx.plano(med_a, pes, 3, "semanal", 3, inicio=HOJE)
    _grupo_cambona(ctx, [med_a, med_b])
    ctx.conn.commit()

    data = _proxima_data_livre(ctx, pes)
    status, _ = _nova_sessao("cambona", "teste123").post("/agenda/medium/lancar", {
        "pessoa_id": pes, "medium_id": med_b, "data": data,
    })
    assert_eq(status, 303, "recusa via redirect")
    st2, loc = _location_pos("cambona", "/agenda/medium/lancar", {
        "pessoa_id": pes, "medium_id": med_b, "data": data})
    assert_eq(st2, 303, "recusa via redirect")
    assert "já tem plano ativo com Eglaudio" in tp.urllib.parse.unquote(loc), \
        f"recusa deve apontar o outro médium: {loc}"
    assert_eq(len(_plano_de(ctx, pes)), 1, "nenhum plano extra")
    assert_eq(_plano_de(ctx, pes)[0]["medium_id"], med_a, "plano permanece no médium original")

    # A situação consultada pelo formulário também aponta o outro médium
    st, corpo = _nova_sessao("cambona", "teste123")(
        f"/agenda/medium/situacao?pessoa_id={pes}&medium_id={med_b}")
    assert_eq(st, 200, "situação acessível com leitura")
    import json
    dados = json.loads(corpo)
    assert_eq(dados["tem_plano"], True)
    assert_eq(dados["mesmo_medium"], False)
    assert_eq(dados["medium_nome"], "Eglaudio", "orienta qual médium atende")


# ── CB6: vínculo usuário↔médium (quem cuida das agendas) na tela de Usuários ──────────────────────────

@teste("CB6 admin vincula médiuns do usuário e os chips respeitam o vínculo")
def cb6(ctx: Ctx):
    ctx.reset(dias_semana=(0, 2))
    med_a = _medium(ctx, "Eglaudio")
    med_b = _medium(ctx, "Marta")
    med_c = _medium(ctx, "Outro")
    g, _, _ = _grupo_cambona(ctx)
    ctx.conn.commit()

    status, _ = _http("POST", "/cadastros/usuarios/novo", [
        ("nome_usuario", "cambona2"), ("nome_completo", "Cambona Dois"),
        ("senha", "teste123"), ("telefone", ""), ("email", ""),
        ("grupos", str(g)), ("mediuns", str(med_a)), ("mediuns", str(med_b)),
    ])
    assert_eq(status, 303, "cadastro admin deveria redirecionar")
    ctx.conn.commit()
    uid = ctx.conn.execute(
        "SELECT id FROM atendentes WHERE nome_usuario='cambona2'").fetchone()["id"]
    vinculados = {r["medium_id"] for r in ctx.conn.execute(
        "SELECT medium_id FROM atendentes_mediuns WHERE atendente_id=%s", (uid,)
    ).fetchall()}
    assert_eq(vinculados, {med_a, med_b}, "vínculo salvo na tela de Usuários")

    _, html = _nova_sessao("cambona2", "teste123")("/agenda/medium")
    assert "Eglaudio" in html and "Marta" in html, "chips dos vinculados"
    assert "Outro" not in html, "médium não vinculado não vira chip"

    # sem vínculo: nada de agenda — só o aviso de pedir o vínculo ao admin
    g2 = _grupo(ctx, "Cambonas Sem Vinculo")
    _perm(ctx, g2, "agenda.medium", ler=True, escrever=True)
    _usuario_em_grupo(ctx, "cambona3", g2)
    ctx.conn.commit()
    st, html_sem = _nova_sessao("cambona3", "teste123")("/agenda/medium")
    assert_eq(st, 200, "tela abre mesmo sem vínculo")
    assert "ainda não tem agenda de médium vinculada" in html_sem, "aviso de vínculo vazio"
    assert "Outro" not in html_sem and "Eglaudio" not in html_sem, \
        "sem vínculo não existe chip nem agenda de terceiros"


# ── CB9: o vínculo é o ESCOPO (não só preferência de exibição) ────────────────

@teste("CB9 vínculo limita a agenda listada e recusa lançar fora das suas agendas")
def cb9(ctx: Ctx):
    ctx.reset(dias_semana=(0, 2))
    med = _medium(ctx, "Eglaudio")
    med_fora = _medium(ctx, "Outro")
    minha = ctx.pessoa("minha atendia")
    de_terceiros = ctx.pessoa("atendia de terceiros")
    plano_minha = ctx.plano(med, minha, 4, "semanal", 3, inicio=HOJE)
    plano_fora = ctx.plano(med_fora, de_terceiros, 4, "semanal", 3, inicio=HOJE)
    _grupo_cambona(ctx, [med])
    ctx.conn.commit()

    _, html = _nova_sessao("cambona", "teste123")("/agenda/medium")
    assert "minha atendia" in html, "sessões da minha agenda aparecem"
    assert "atendia de terceiros" not in html, "agenda de outro médium não é listada"
    assert "Outro" not in html, "médium fora do vínculo não vira chip"

    # lançar no médium fora do vínculo: recusa, sem criar sessão nem plano
    data_livre = _proxima_data_livre(ctx, de_terceiros)
    antes = len(_sessoes(ctx, plano_fora))
    st, loc = _location_pos("cambona", "/agenda/medium/lancar", {
        "pessoa_id": de_terceiros, "medium_id": med_fora, "data": data_livre})
    assert_eq(st, 303, "recusa via redirect")
    assert "não está entre as suas agendas" in tp.urllib.parse.unquote(loc), \
        f"motivo da recusa: {loc}"
    assert_eq(len(_sessoes(ctx, plano_fora)), antes, "nenhuma sessão inserida fora do vínculo")

    # o mesmo dado, lançado por quem tem o vínculo, entra
    _vincular(ctx, ctx.conn.execute(
        "SELECT id FROM atendentes WHERE nome_usuario='cambona'").fetchone()["id"], [med_fora])
    ctx.conn.commit()
    st, loc = _location_pos("cambona", "/agenda/medium/lancar", {
        "pessoa_id": de_terceiros, "medium_id": med_fora, "data": data_livre})
    assert_eq(st, 303, "lançamento aceito")
    assert "erro=" not in loc, f"não deveria haver erro: {loc}"
    assert_eq(len(_sessoes(ctx, plano_fora)), antes + 1, "sessão acrescentada no plano")


# ── CB7: data fora do calendário ──────────────────────────────────────────────

@teste("CB7 data em dia sem atendimento é recusada e não cria nada")
def cb7(ctx: Ctx):
    ctx.reset(dias_semana=(0, 2))
    med = _medium(ctx, "Eglaudio")
    pes = ctx.pessoa("samira")
    _grupo_cambona(ctx, [med])
    ctx.conn.commit()

    ruim = _proximo_dia_nao_atendimento(ctx)
    status, _ = _nova_sessao("cambona", "teste123").post("/agenda/medium/lancar", {
        "pessoa_id": pes, "medium_id": med, "data": ruim,
        "qtd_sessoes": 4, "frequencia": "semanal",
    })
    assert_eq(status, 303, "recusa via redirect com erro")
    assert_eq(len(_plano_de(ctx, pes)), 0, "nenhum plano criado")
    assert_eq(_planos_da_pessoa(ctx, pes), 0)


# ── CB8: atendidos do último dia ──────────────────────────────────────────────

@teste("CB8 tela exibe os atendidos do último dia de atendimento do médium")
def cb8(ctx: Ctx):
    ctx.reset(dias_semana=(0, 2))
    med = _medium(ctx, "Eglaudio")
    med_outro = _medium(ctx, "Marta")
    ontem = (HOJE - timedelta(days=1)).isoformat()
    dia_id = ctx.conn.execute(
        "INSERT INTO dias_trabalho (data, aberto) VALUES (%s, 1) RETURNING id",
        (ontem,)
    ).fetchone()["id"]
    atendida = ctx.pessoa("atendida ontem")
    outra_med = ctx.pessoa("atendida pelo outro")
    nao_atendida = ctx.pessoa("so passou no checkin")
    for pessoa, medium, realizado in ((atendida, med, 1), (outra_med, med_outro, 1),
                                      (nao_atendida, med, 0)):
        ctx.conn.execute(
            """INSERT INTO checkins (dia_trabalho_id, pessoa_id, hora_checkin,
                                     codigo_atendimento, medium_id, atendimento_realizado)
               VALUES (%s,%s,'09:30','9',%s,%s)""",
            (dia_id, pessoa, medium, realizado)
        )
    _grupo_cambona(ctx, [med])
    ctx.conn.commit()

    _, html = _nova_sessao("cambona", "teste123")(f"/agenda/medium?medium_id={med}")
    assert "atendida ontem" in html, "atendidos do último dia para o médium"
    assert "atendida pelo outro" not in html, "outro médium fora do filtro"
    assert "so passou no checkin" not in html, "sem atendimento realizado fora"


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
    print(f"\n{ok}/{total} testes passaram (agenda do médium)")
    limpar_banco()
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
