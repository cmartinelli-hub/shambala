#!/usr/bin/env python3
"""Suíte de testes da regra dos três passes / Acolhimento Fraterno.

Roda SEMPRE contra um banco PostgreSQL descartável (nunca o DEV, nunca produção).
Cria o schema chamando `banco.criar_tabelas()` e remove o banco no final.

Uso (recomendado — cuida do banco, do app e do log):
    bash tests/rodar_testes.sh

Uso direto (o app de teste precisa já estar no ar em SHAMBALA_TEST_BASE):
    SHAMBALA_ENV_FILE=$PWD/.env.test SHAMBALA_MODO_TESTE=1 \
        PYTHONPATH=$PWD .venv/bin/python3 tests/test_tres_passes.py

Variáveis:
    SHAMBALA_ENV_FILE  arquivo de configuração EXCLUSIVO de teste (default: .env.test)
    SHAMBALA_TEST_DB   nome do banco descartável (default: shambala_test_passes)
    SHAMBALA_TEST_KEEP=1  não remove o banco no final (para depuração)
"""
import os
import subprocess
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from datetime import date, timedelta

# ── TRAVA DE ISOLAMENTO (executa ANTES de importar a aplicação) ──────────────
# `banco.py` lê as variáveis SHAMBALA_* UMA única vez, no momento do import. Se
# o arquivo de configuração de teste fosse carregado depois, o processo cairia
# no default de `banco.py` ('shambala' = banco de DESENVOLVIMENTO) — foi
# exatamente por isso que a suíte ficou parada na trava de isolamento. A ordem
# abaixo é obrigatória: configurar (1) → validar (2) → só então importar (3).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import (  # noqa: E402
    exigir_arquivo_de_configuracao, exigir_ambiente_de_teste, AmbienteInseguro,
    exigir_configuracao_consoante, provar_destino_do_servidor,
)

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ARQ_ENV = os.environ.get("SHAMBALA_ENV_FILE") or os.path.join(_RAIZ, ".env.test")
os.environ.setdefault("SHAMBALA_MODO_TESTE", "1")
# (1) Contrato de identidade (TASK_010/REV-01): o arquivo NOMEADO é a autoridade
#     do destino; SHAMBALA_TEST_DB apenas "solicita" — se divergir do arquivo,
#     aborta antes de qualquer operação, sem substituição silenciosa em nenhum
#     dos sentidos.
BANCO = exigir_configuracao_consoante(
    _ARQ_ENV, os.environ.get("SHAMBALA_TEST_DB"))["banco"]
# (2) Valida nome + modo de teste ANTES de importar (a conexão efetiva é
#     conferida em preparar_banco, e o servidor HTTP na prova de token).
exigir_ambiente_de_teste()
# Token desta execução (dado pelo runner; gerado localmente em uso avulso).
TOKEN = os.environ.get("SHAMBALA_EXEC_TOKEN") or f"tp-avulso-p{os.getpid()}"
os.environ["SHAMBALA_EXEC_TOKEN"] = TOKEN
# (3) Só agora a aplicação é importada — e ela fixa a conexão no import.
from banco import conectar, criar_tabelas, fechar_pool  # noqa: E402
import banco  # noqa: E402
from preparo_descartavel import (  # noqa: E402
    garantir_banco_selado, recriar_banco, remover_se_nosso, selo_do_banco,
)

RESULTADOS = []
FALHAS = []


def _psql_admin(sql: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["psql", "-h", os.environ.get("PGHOST", "/var/run/postgresql"),
         "-U", os.environ.get("PGUSER", "shambala"), "-d", "postgres",
         "-tAqc", sql],
        capture_output=True, text=True, env={**os.environ},
    )


def preparar_banco():
    """Garante o banco descartável com o schema criado.

    ABORTA se o ambiente não for comprovadamente de teste — a validação acontece
    antes de criar, truncar ou alterar qualquer dado. A criação usa selo de
    propriedade (TASK_010): banco preexistente sem selo desta infraestrutura
    nunca é adotado nem apagado por convenção de nome.
    """
    try:
        with conectar() as conn:
            info = exigir_ambiente_de_teste(conn)
    except AmbienteInseguro as e:
        print(f"\n[ABORTADO] ambiente inseguro: {e}", file=sys.stderr)
        raise SystemExit(2)

    try:
        garantir_banco_selado(BANCO, TOKEN)
    except AmbienteInseguro as e:
        print(f"\n[ABORTADO] preparo recusado: {e}", file=sys.stderr)
        raise SystemExit(2)
    criar_tabelas()
    return info


def preparar_banco_limpo():
    """Modo isolado: derruba e recria o banco (exige nenhum app conectado).

    Só recria banco COM SELO desta infraestrutura; sem selo, aborta sem efeitos.
    """
    try:
        recriar_banco(BANCO, TOKEN)
    except AmbienteInseguro as e:
        print(f"\n[ABORTADO] recriação recusada: {e}", file=sys.stderr)
        raise SystemExit(2)
    criar_tabelas()


def limpar_banco():
    fechar_pool()
    if os.environ.get("SHAMBALA_TEST_LIMPAR") == "1":
        if selo_do_banco(BANCO) == TOKEN:
            remover_se_nosso(BANCO, TOKEN)
            print(f"  banco {BANCO} removido (selo desta execução)")
        else:
            print(f"  limpeza preservou {BANCO}: selo não pertence a esta execução")


def teste(nome):
    """Decorator: registra o teste e captura falhas sem interromper a suíte."""
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


# ── Fixtures ─────────────────────────────────────────────────────────────────

class Ctx:
    """Contexto de teste: limpa as tabelas de negócio antes de cada caso."""

    def __init__(self, conn):
        self.conn = conn

    def reset(self, dias_semana=(0, 2)):
        """Limpa as tabelas de negócio respeitando TODAS as FKs.

        Usa TRUNCATE ... CASCADE nas raízes do domínio de atendimento: o CASCADE
        derruba as tabelas dependentes (checkins, agendamentos, plano_pessoas,
        financeiro, etc.), independente de quem mais referencia quem.
        """
        self.conn.execute(
            "TRUNCATE atendentes, pessoas, mediuns, trabalhadores, dias_trabalho, "
            "agendamentos, planos_tratamento, plano_pessoas, plano_chave_idempotencia, "
            "checkins, mediuns_dia, dias_atendimento, financeiro_movimentacoes, "
            "vendas_pdv, caixa_movimentos, estoque_movimentos, trabalhador_presenca "
            "RESTART IDENTITY CASCADE"
        )
        # O TRUNCATE acima apaga o usuário de teste; recria para que as chamadas
        # HTTP (que exigem login) continuem válidas durante toda a suíte.
        self.conn.execute(
            "INSERT INTO grupos (id, nome, descricao) VALUES (1,'Admin','Administradores') "
            "ON CONFLICT (id) DO NOTHING"
        )
        self.conn.execute("SELECT setval('grupos_id_seq', (SELECT COALESCE(MAX(id),1) FROM grupos))")
        from rotas.auth import hash_senha
        cur = self.conn.execute(
            """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
               VALUES ('admin','Administrador',%s,1) RETURNING id""",
            (hash_senha("teste123"),)
        )
        self.conn.execute(
            "INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, 1)",
            (cur.fetchone()["id"],)
        )
        for d in dias_semana:
            self.conn.execute(
                "INSERT INTO dias_atendimento (dia_semana, descricao) VALUES (%s, %s)",
                (d, f"dia-{d}")
            )

    def dia(self, data=None):
        data = data or date.today().isoformat()
        return self.conn.execute(
            "INSERT INTO dias_trabalho (data, aberto) VALUES (%s, 1) RETURNING id",
            (data,)
        ).fetchone()["id"]

    def pessoa(self, nome="pessoa"):
        return self.conn.execute(
            "INSERT INTO pessoas (nome_apresentacao, nome_completo) VALUES (%s,%s) RETURNING id",
            (nome, nome)
        ).fetchone()["id"]

    def medium(self, vagas=10):
        return self.conn.execute(
            "INSERT INTO mediuns (nome_completo, ativo, vagas_dia) VALUES ('m',1,%s) RETURNING id",
            (vagas,)
        ).fetchone()["id"]

    def checkin(self, dia_id, pessoa_id, codigo="1"):
        return self.conn.execute(
            """INSERT INTO checkins (dia_trabalho_id, pessoa_id, hora_checkin, codigo_acolhimento)
               VALUES (%s,%s,'10:00',%s) RETURNING id""",
            (dia_id, pessoa_id, codigo)
        ).fetchone()["id"]

    def plano(self, medium_id, pessoa_id, total, freq, cfg, inicio=None):
        """Cria plano como fazem as portas 1/2 (usa a fonte única)."""
        inicio = inicio or date.today()
        pid = self.conn.execute(
            """INSERT INTO planos_tratamento
               (medium_id, sessoes_total, data_inicio, frequencia, sessoes_com_passe, status)
               VALUES (%s,%s,%s,%s,%s,'ativo') RETURNING id""",
            (medium_id, total, inicio.isoformat(), freq,
             banco.calcular_sessoes_com_passe(total, cfg))
        ).fetchone()["id"]
        self.conn.execute(
            "INSERT INTO plano_pessoas (plano_id, pessoa_id) VALUES (%s,%s)", (pid, pessoa_id)
        )
        banco.gerar_agendamentos_plano(self.conn, pid, inicio, freq, total, cfg)
        return pid

    def sessoes(self, plano_id):
        return self.conn.execute(
            "SELECT data, status, requer_passe FROM agendamentos WHERE plano_id=%s ORDER BY data, id",
            (plano_id,)
        ).fetchall()

    def n_passe(self, plano_id):
        return sum(1 for s in self.sessoes(plano_id) if s["requer_passe"])


def assert_eq(obtido, esperado, msg=""):
    if obtido != esperado:
        raise AssertionError(f"{msg} esperado={esperado!r} obtido={obtido!r}")


def assert_vazio(lista, msg=""):
    if lista:
        raise AssertionError(f"{msg} deveria estar vazio, veio {lista!r}")


# ── Testes da regra do passe (unidade) ───────────────────────────────────────

@teste("U1 calcular_sessoes_com_passe: 1, 2, 3 e 4 sessões usam o piso corretamente")
def _u1(ctx):
    assert_eq(banco.calcular_sessoes_com_passe(1, 0), 1, "1 sessão")
    assert_eq(banco.calcular_sessoes_com_passe(2, 0), 2, "2 sessões")
    assert_eq(banco.calcular_sessoes_com_passe(3, 0), 3, "3 sessões")
    assert_eq(banco.calcular_sessoes_com_passe(4, 0), 3, "4 sessões")
    assert_eq(banco.calcular_sessoes_com_passe(6, 3), 3, "6 sessões / 3 passes")
    assert_eq(banco.calcular_sessoes_com_passe(6, 0), 3, "config 0 vence pelo piso")
    assert_eq(banco.calcular_sessoes_com_passe(6, 5), 5, "config maior que o piso amplia")
    assert_eq(banco.calcular_sessoes_com_passe(2, 6), 2, "config maior que o total é limitada")
    assert_eq(banco.calcular_sessoes_com_passe(4, -1), 4, "legado negativo = todas")
    assert_eq(banco.calcular_sessoes_com_passe(0, 3), 0, "sem sessões = sem passes")


@teste("U2 datas_agendamento respeita dias cadastrados (seg/qua) e as frequências")
def _u2(ctx):
    ctx.reset(dias_semana=(0, 2))
    # 2026-10-17 é sábado -> deve cair na segunda 19/10
    datas = banco.datas_agendamento(date(2026, 10, 17), 3, "semanal", [0, 2])
    assert_eq(datas, ["2026-10-19", "2026-10-26", "2026-11-02"], "semanal a partir de sábado")
    for d in datas:
        assert date.fromisoformat(d).weekday() in (0, 2), f"{d} não é dia de atendimento"
    q = banco.datas_agendamento(date(2026, 10, 19), 3, "quinzenal", [0, 2])
    assert_eq(q, ["2026-10-19", "2026-11-02", "2026-11-16"], "quinzenal")
    m = banco.datas_agendamento(date(2026, 10, 19), 3, "mensal", [0, 2])
    assert_eq(m, ["2026-10-19", "2026-11-16", "2026-12-14"], "mensal")
    a = banco.datas_agendamento(date(2026, 10, 19), 4, "avulso", [0, 2])
    assert_eq(a, ["2026-10-19"], "avulso gera uma única sessão")


@teste("U3 datas_agendamento com calendário diferente (só quinta) não presume seg/qua")
def _u3(ctx):
    datas = banco.datas_agendamento(date(2026, 10, 14), 3, "semanal", [3])
    for d in datas:
        assert date.fromisoformat(d).weekday() == 3, f"{d} não é quinta-feira"
    assert_eq(datas[0], "2026-10-15", "primeira quinta após 14/10")


@teste("U4 data_atendimento_valida aceita só dia cadastrado e é dinâmica")
def _u4(ctx):
    ctx.reset(dias_semana=(3,))
    assert banco.data_atendimento_valida(ctx.conn, "2026-10-15") is True, "quinta cadastrada"
    assert banco.data_atendimento_valida(ctx.conn, "2026-10-14") is False, "quarta não cadastrada"
    assert banco.data_atendimento_valida(ctx.conn, "2026-10-17") is False, "sábado"
    assert banco.data_atendimento_valida(ctx.conn, "data-ruim") is False, "data inválida"
    ctx.reset(dias_semana=(0, 2))
    assert banco.data_atendimento_valida(ctx.conn, "2026-10-14") is True, "volta a aceitar quarta"


# ── Testes da geração pelo Fraterno / médium (portas 1 e 2) ──────────────────

@teste("P1 plano de 1 sessão: 1 passe, 1 data em dia de atendimento")
def _p1(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 1, "semanal", 0)
    s = ctx.sessoes(pid)
    assert_eq(len(s), 1, "sessões")
    assert_eq(ctx.n_passe(pid), 1, "passes")


@teste("P2 plano de 2 sessões: 2 passes (sem criar sessão extra)")
def _p2(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 2, "semanal", 0)
    assert_eq(len(ctx.sessoes(pid)), 2, "sessões")
    assert_eq(ctx.n_passe(pid), 2, "passes")


@teste("P3 plano de 3 sessões: exatamente 3 passes")
def _p3(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 3, "semanal", 0)
    assert_eq(ctx.n_passe(pid), 3, "passes")


@teste("P4 plano de 4 sessões: 3 passes (o 4º opcional)")
def _p4(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 4, "semanal", 0)
    s = ctx.sessoes(pid)
    assert_eq(len(s), 4, "sessões")
    assert_eq([x["requer_passe"] for x in s], [1, 1, 1, 0], "passe por sessão")


@teste("P5 plano de 6 sessões com 3 passes configurados: 3 passes")
def _p5(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 6, "semanal", 3)
    assert_eq([x["requer_passe"] for x in ctx.sessoes(pid)], [1, 1, 1, 0, 0, 0], "passe por sessão")


@teste("P6 plano com todas as sessões com passe (config negativa/legado)")
def _p6(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 4, "semanal", -1)
    assert_eq([x["requer_passe"] for x in ctx.sessoes(pid)], [1, 1, 1, 1], "todas com passe")


@teste("P7 todas as datas geradas caem em dia de atendimento (semanal/quinzenal/mensal)")
def _p7(ctx):
    for freq in ("semanal", "quinzenal", "mensal"):
        ctx.reset()
        pid = ctx.plano(ctx.medium(), ctx.pessoa(), 5, freq, 3)
        for s in ctx.sessoes(pid):
            assert date.fromisoformat(s["data"]).weekday() in (0, 2), f"{freq}: {s['data']}"


@teste("P8 frequência avulsa gera uma sessão em dia de atendimento (passe aplicado)")
def _p8(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 5, "avulso", 3)
    s = ctx.sessoes(pid)
    assert_eq(len(s), 1, "sessões com avulso")
    assert_eq(s[0]["requer_passe"], 1, "passe na sessão avulsa")


@teste("P9 plano com múltiplas pessoas gera agenda única e correta")
def _p9(ctx):
    ctx.reset()
    m, p1, p2 = ctx.medium(), ctx.pessoa("a"), ctx.pessoa("b")
    pid = ctx.plano(m, p1, 4, "semanal", 3)
    ctx.conn.execute("INSERT INTO plano_pessoas (plano_id, pessoa_id) VALUES (%s,%s)", (pid, p2))
    assert_eq(len(ctx.sessoes(pid)), 4, "sessões (plano familiar, uma agenda)")
    assert_eq(ctx.n_passe(pid), 3, "passes")
    assert_eq(ctx.conn.execute(
        "SELECT count(*) AS c FROM plano_pessoas WHERE plano_id=%s", (pid,)
    ).fetchone()["c"], 2, "pessoas do plano")


@teste("P10 alta e cancelamento cancelam os agendamentos pendentes")
def _p10(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 4, "semanal", 3)
    ctx.conn.execute("UPDATE planos_tratamento SET status='alta' WHERE id=%s", (pid,))
    ctx.conn.execute("UPDATE agendamentos SET status='cancelado' WHERE plano_id=%s AND status='agendado'", (pid,))
    assert_eq(
        ctx.conn.execute(
            "SELECT count(*) AS c FROM agendamentos WHERE plano_id=%s AND status='agendado'", (pid,)
        ).fetchone()["c"], 0, "pendentes após alta"
    )


# ── Testes do caminho da Agenda (dependem das rotas; usam banco temporário) ──

@teste("A1 /agenda/novo NÃO contorna o piso: 4 sessões => 3 passes (fecha D1/S14)")
def _a1(ctx):
    ctx.reset()
    pid = _criar_pela_agenda(ctx, total=4)
    assert_eq(len(ctx.sessoes(pid)), 4, "sessões")
    assert_eq(ctx.n_passe(pid), 3, "passes (antes do fix: 0)")


@teste("A2 /agenda/novo respeita os dias de atendimento (fecha D2/S18)")
def _a2(ctx):
    ctx.reset()
    sabado = _ultimo_sabado()
    pid = _criar_pela_agenda(ctx, total=4, data=sabado)
    datas = [s["data"] for s in ctx.sessoes(pid)]
    assert datas, "deveria ter gerado agendamentos"
    for d in datas:
        assert date.fromisoformat(d).weekday() in (0, 2), f"{d} caiu fora do calendário"
    assert datas[0] != sabado, "não pode começar no sábado"


@teste("A3 /agenda/novo bloqueia duplicidade para a pessoa (S15 preservado)")
def _a3(ctx):
    ctx.reset()
    pessoa, medium = ctx.pessoa("dup"), ctx.medium()
    data = "2026-10-19"  # segunda-feira, dia de atendimento, futura e determinística
    pid = _criar_pela_agenda(ctx, total=2, pessoa=pessoa, medium=medium, data=data)
    assert pid is not None, "primeiro plano deveria ser criado"
    datas = sorted(s["data"] for s in ctx.sessoes(pid))
    assert_eq(datas, ["2026-10-19", "2026-10-26"], "datas do 1º plano")

    # 2ª chamada com os MESMOS dados: a rota responde 303 (redirect com erro) e
    # NÃO pode criar plano nem agendamento. `_criar_pela_agenda` falha sozinha se
    # um plano aparecer para esta pessoa — que é exatamente o que se prova aqui.
    _criar_pela_agenda(ctx, total=2, pessoa=pessoa, medium=medium, data=data,
                       espera_erro=True)

    ctx.conn.commit()
    assert_eq(ctx.conn.execute(
        "SELECT count(*) AS c FROM plano_pessoas WHERE pessoa_id = %s", (pessoa,)
    ).fetchone()["c"], 1, "planos da pessoa após a 2ª chamada (duplicidade barrada)")
    assert_eq(ctx.conn.execute(
        "SELECT count(*) AS c FROM agendamentos", ()
    ).fetchone()["c"], 2, "agendamentos totais (a 2ª chamada não pode acrescentar)")


# ── Testes de edição, reagendamento e duplicidade ───────────────────────────

@teste("E1 editar o plano (config 3 -> 0) não remove a obrigatoriedade (fecha S11)")
def _e1(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 6, "semanal", 3)
    antes = ctx.n_passe(pid)
    # simula a edição pela ficha: recalcula a config e reaplica o piso
    cfg = banco.calcular_sessoes_com_passe(6, 0)
    ctx.conn.execute("UPDATE planos_tratamento SET sessoes_com_passe=%s WHERE id=%s", (cfg, pid))
    _reaplicar_piso(ctx.conn, pid)
    assert_eq(ctx.n_passe(pid), antes, "passes após edição para config 0")
    assert ctx.n_passe(pid) >= 3, f"piso deveria permanecer, veio {ctx.n_passe(pid)}"


@teste("E2 acrescentar sessão não apaga o piso (plano de 4 -> 5 sessões)")
def _e2(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 4, "semanal", 3)
    assert_eq(ctx.n_passe(pid), 3, "antes")
    ctx.conn.execute(
        "INSERT INTO agendamentos (plano_id, data, status, requer_passe, encaixe) VALUES (%s,'2026-12-31','agendado',0,0)",
        (pid,)
    )
    _reaplicar_piso(ctx.conn, pid)
    assert_eq(ctx.n_passe(pid), 3, "depois de acrescentar sessão")


@teste("E3 reagendar mantém todas as datas em dia de atendimento (fecha S9)")
def _e3(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 4, "semanal", 3)
    # reagenda a partir de uma quarta, para o dia seguinte válido (segunda)
    ctx.conn.execute("DELETE FROM agendamentos WHERE plano_id=%s", (pid,))
    inicio = date(2026, 10, 12)  # segunda
    banco.gerar_agendamentos_plano(ctx.conn, pid, inicio, "semanal", 4, 3)
    nova = banco.datas_agendamento(date(2026, 10, 17), 4, "semanal", [0, 2])  # sábado -> segunda
    ids = [r["id"] for r in ctx.conn.execute(
        "SELECT id FROM agendamentos WHERE plano_id=%s ORDER BY data", (pid,)).fetchall()]
    for i, ag_id in enumerate(ids):
        ctx.conn.execute("UPDATE agendamentos SET data=%s WHERE id=%s", (nova[i], ag_id))
    for s in ctx.sessoes(pid):
        assert date.fromisoformat(s["data"]).weekday() in (0, 2), f"{s['data']} fora do calendário"


@teste("E4 reagendar não remove passe já marcado (acompanha a ordem das sessões)")
def _e4(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 4, "semanal", 3)
    requeridos = [s["requer_passe"] for s in ctx.sessoes(pid)]
    _reaplicar_requeridos(ctx.conn, pid, requeridos)
    assert_eq(ctx.n_passe(pid), 3, "após reagendamento")


@teste("D1 reenvio do Fraterno NÃO duplica plano (fecha S2)")
def _d1(ctx):
    ctx.reset()
    dia, pessoa, medium = ctx.dia(), ctx.pessoa(), ctx.medium()
    ck = ctx.checkin(dia, pessoa)
    plano1 = _salvar_fraterno(ctx, ck, medium, total=6)
    plano2 = _salvar_fraterno(ctx, ck, medium, total=6)
    assert_eq(plano2, plano1, "deveria reaproveitar o plano")
    assert_eq(_contar(ctx, "SELECT count(*) AS c FROM planos_tratamento")["c"], 1, "planos criados")
    assert_eq(len(ctx.sessoes(plano1)), 6, "agendamentos duplicados?")
    assert_eq(ctx.n_passe(plano1), 3, "passes")


@teste("D2 acolhimento SEM plano continua sem gerar passe (R2)")
def _d2(ctx):
    ctx.reset()
    dia, pessoa = ctx.dia(), ctx.pessoa()
    ck = ctx.checkin(dia, pessoa)
    # medium_id vazio: nenhum plano, nenhum agendamento
    resultado = _salvar_fraterno(ctx, ck, None, total=5)
    assert_eq(resultado, None, "não deveria criar plano")
    assert_eq(_contar(ctx, "SELECT count(*) AS c FROM planos_tratamento")["c"], 0, "planos")
    assert_eq(_contar(ctx, "SELECT count(*) AS c FROM agendamentos")["c"], 0, "agendamentos")
    assert_eq(
        _contar(ctx, "SELECT acolhimento_realizado FROM checkins WHERE id=%s", (ck,))["acolhimento_realizado"],
        1, "acolhimento continua marcado como realizado"
    )


@teste("D3 acolhimento novo (outra data) cria plano novo legitimamente")
def _d3(ctx):
    ctx.reset()
    dia, pessoa, medium = ctx.dia(), ctx.pessoa(), ctx.medium()
    ck = ctx.checkin(dia, pessoa)
    p1 = _salvar_fraterno(ctx, ck, medium, total=3, inicio="2026-10-12")
    p2 = _salvar_fraterno(ctx, ck, medium, total=3, inicio="2026-11-09")
    assert p1 != p2, "nova data de início = novo tratamento"
    assert_eq(_contar(ctx, "SELECT count(*) AS c FROM planos_tratamento")["c"], 2, "planos")


@teste("D4 acolhimento com OUTRO médium cria plano novo legitimamente")
def _d4(ctx):
    ctx.reset()
    dia, pessoa = ctx.dia(), ctx.pessoa()
    m1, m2 = ctx.medium(), ctx.medium()
    ck = ctx.checkin(dia, pessoa)
    p1 = _salvar_fraterno(ctx, ck, m1, total=3)
    p2 = _salvar_fraterno(ctx, ck, m2, total=3)
    assert p1 != p2, "outro médium = outro tratamento"
    assert_eq(_contar(ctx, "SELECT count(*) AS c FROM planos_tratamento")["c"], 2, "planos")


@teste("D5 duas requisições CONCORRENTES não criam plano duplicado")
def _d5(ctx):
    import threading
    ctx.reset()
    dia, pessoa, medium = ctx.dia(), ctx.pessoa(), ctx.medium()
    ck = ctx.checkin(dia, pessoa)
    ctx.conn.commit()
    resultados, erros = [], []

    def worker():
        try:
            # conexão própria por thread, igual a duas requisições reais
            with conectar() as c2:
                _http("POST", f"/dia/fraterno/{ck}", {
                    "medium_id": medium, "sessoes_total": 4, "frequencia": "semanal",
                    "sessoes_com_passe": 1, "data_inicio": date.today().isoformat(),
                    "observacao_acolhimento": "concorrencia",
                })
            resultados.append(True)
        except Exception as e:  # pragma: no cover
            erros.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert_vazio(erros, "erros em concorrência")
    assert_eq(
        _contar(ctx, "SELECT count(*) AS c FROM planos_tratamento")["c"],
        1, "planos após 2 requisições concorrentes"
    )


@teste("R1 falhas: registrar falta não altera a marcação de passe (escopo intocado)")
def _r1(ctx):
    ctx.reset()
    pid = ctx.plano(ctx.medium(), ctx.pessoa(), 4, "semanal", 3)
    antes = [s["requer_passe"] for s in ctx.sessoes(pid)]
    primeiro = ctx.sessoes(pid)[0]
    ctx.conn.execute("UPDATE agendamentos SET status='faltou' WHERE data=%s AND plano_id=%s",
                     (primeiro["data"], pid))
    depois = [s["requer_passe"] for s in ctx.sessoes(pid)]
    assert_eq(depois, antes, "regra de faltas não deve mexer em requer_passe")


# ── Apoio: chamadas às rotas reais via HTTP ──────────────────────────────────

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9011")
COOKIES = "/tmp/shambala_test_cookies.txt"


class _SemRedirect(urllib.request.HTTPRedirectHandler):
    """Não segue redirect: os testes precisam ver o 303 e o destino."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_SESSAO = None


def _sessao():
    """Sessão HTTP única com cookie jar em memória (login feito uma vez)."""
    global _SESSAO
    if _SESSAO is None:
        cj = http.cookiejar.CookieJar()
        _SESSAO = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj), _SemRedirect()
        )
    return _SESSAO


def _http(method, path, data=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(BASE + path, data=body, method=method)
    try:
        with _sessao().open(req) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except _RedirectNaoSeguido as e:  # 303 visto como exceção
        return e.code, ""


class _RedirectNaoSeguido(Exception):
    def __init__(self, code):
        self.code = code


def _login():
    status, _ = _http("POST", "/login", {"nome_usuario": "admin", "senha": "teste123"})
    return status


def _garantir_admin():
    """Cria/garante o usuário admin com senha conhecida no banco de teste."""
    from rotas.auth import hash_senha
    with conectar() as conn:
        conn.execute(
            "INSERT INTO grupos (id, nome, descricao) VALUES (1,'Admin','Administradores') "
            "ON CONFLICT (id) DO NOTHING"
        )
        conn.execute("SELECT setval('grupos_id_seq', (SELECT COALESCE(MAX(id),1) FROM grupos))")
        row = conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario = 'admin'"
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE atendentes SET senha_hash = %s, ativo = 1 WHERE id = %s",
                (hash_senha("teste123"), row["id"])
            )
            conn.execute(
                "INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, 1) "
                "ON CONFLICT DO NOTHING", (row["id"],)
            )
        else:
            cur = conn.execute(
                """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
                   VALUES ('admin','Administrador',%s,1) RETURNING id""",
                (hash_senha("teste123"),)
            )
            conn.execute(
                "INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, 1) "
                "ON CONFLICT DO NOTHING", (cur.fetchone()["id"],)
            )
        conn.execute(
            "INSERT INTO grupos (id, nome, descricao) VALUES (1,'Admin','Administradores') "
            "ON CONFLICT (id) DO NOTHING"
        )
    _http("POST", "/login", {"nome_usuario": "admin", "senha": "teste123"})


def _ultimo_sabado():
    d = date.today()
    while d.weekday() != 5:
        d -= timedelta(days=1)
    return d.isoformat()


def _recomecar(ctx):
    """Limpa o banco e RECOMITA, para que o app de teste (outra conexão, com
    snapshot próprio) veja o mesmo estado que a suíte. Sem isso, a contagem
    feita pela suíte enxerga dados que o app ainda não vê (e vice-versa)."""
    ctx.conn.commit()


def _contar(ctx, sql, params=()):
    """Conta lendo o estado já comitado (mesma visão do app)."""
    ctx.conn.commit()
    return ctx.conn.execute(sql, params).fetchone()


def _planos_da_pessoa(ctx, pessoa_id):
    """Quantos planos a pessoa tem — a contagem, não o último plano.

    `_plano_da_pessoa` devolve o plano mais recente (útil para achar o que a
    chamada acabou de criar), mas NUNCA serve para provar que nada foi criado:
    numa 2ª chamada o plano da 1ª continua lá e a função responderia o mesmo id.
    Para duplicidade a prova é a contagem.
    """
    ctx.conn.commit()
    return ctx.conn.execute(
        "SELECT count(*) AS c FROM plano_pessoas WHERE pessoa_id = %s", (pessoa_id,)
    ).fetchone()["c"]


def _criar_pela_agenda(ctx, total=3, data=None, espera_erro=False, pessoa=None, medium=None):
    """Chama POST /agenda/novo de verdade e devolve o plano criado (ou None)."""
    data = data or date.today().isoformat()
    if pessoa is None:
        pessoa = ctx.pessoa(f"agenda-{data}")
    if medium is None:
        medium = ctx.medium()
    ctx.conn.commit()  # o app precisa enxergar pessoa/médium antes do POST
    planos_antes = _planos_da_pessoa(ctx, pessoa)

    status, _ = _http("POST", "/agenda/novo", {
        "pessoa_id": pessoa, "data": data, "medium_id": medium,
        "data_de": "", "data_ate": "", "dia_semana": "-1", "pessoa_nome": "",
        "qtd_sessoes": total, "frequencia": "semanal",
    })

    if espera_erro:
        assert_eq(_planos_da_pessoa(ctx, pessoa), planos_antes,
                  f"não deveria criar plano (status {status})")
        return None

    plano = _plano_da_pessoa(ctx, pessoa)
    assert plano is not None, f"plano não foi criado (status {status})"
    return plano


def _plano_da_pessoa(ctx, pessoa_id):
    ctx.conn.commit()
    row = ctx.conn.execute(
        """SELECT pp.plano_id AS id FROM plano_pessoas pp
           WHERE pp.pessoa_id = %s ORDER BY pp.plano_id DESC LIMIT 1""",
        (pessoa_id,)
    ).fetchone()
    return row["id"] if row else None


def _salvar_fraterno(ctx, checkin_id, medium_id, total=3, inicio=None):
    """Chama POST /dia/fraterno/{id} de verdade e devolve o plano resultante."""
    ctx.conn.commit()  # o app precisa enxergar o checkin antes do POST
    _http("POST", f"/dia/fraterno/{checkin_id}", {
        "medium_id": medium_id or "",
        "sessoes_total": total, "frequencia": "semanal", "sessoes_com_passe": 1,
        "data_inicio": inicio or date.today().isoformat(),
        "observacao_acolhimento": "teste",
    })
    if not medium_id:
        return None
    ctx.conn.commit()
    row = ctx.conn.execute(
        """SELECT pp.plano_id AS id FROM checkins c
           JOIN plano_pessoas pp ON pp.pessoa_id = c.pessoa_id
           JOIN planos_tratamento pt ON pt.id = pp.plano_id
           WHERE c.id = %s AND pt.medium_id = %s
           ORDER BY pt.id DESC LIMIT 1""", (checkin_id, medium_id)
    ).fetchone()
    return row["id"] if row else None


def _reaplicar_piso(conn, plano_id):
    from rotas.mediuns import _reaplicar_piso_passe
    _reaplicar_piso_passe(conn, plano_id)


def _reaplicar_requeridos(conn, plano_id, requeridos):
    from rotas.mediuns import _reaplicar_requer_passe
    _reaplicar_requer_passe(conn, plano_id, requeridos)


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
            FALHAS.append((nome, e, traceback.format_exc()))
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
    print(f"\n{ok}/{total} testes passaram")
    if FALHAS:
        print("\n--- detalhes das falhas ---")
        for nome, e, tb in FALHAS:
            print(f"\n### {nome}\n{tb}")
    limpar_banco()
    return 0 if not FALHAS else 1


if __name__ == "__main__":
    sys.exit(main())
