#!/usr/bin/env python3
"""Suíte TASK_018 — o que o OPERADOR VÊ no navegador, com dados fictícios.

As regras (conclusão automática, extensão líquida, vínculo explícito ao plano
ativo) são provadas por serviços e rotas em `test_conclusao_planos_task018.py`.
Aqui se valida o §7.10 da tarefa: a tela de destino, a escolha por médium e
data, o botão de acesso ao plano, a preservação do formulário e a leitura da
situação "Concluído" — em Chromium real, desktop e celular, sem redesign.

Dados fictícios com prefixo T018N em banco descartável; o destino do script E do
servidor HTTP é provado antes de qualquer escrita (TASK_010). Ferramenta ausente
é falha explícita (código 3), nunca "passou".
"""
import contextlib
import http.cookiejar
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import (  # noqa: E402
    AmbienteInseguro, exigir_ambiente_de_teste, exigir_configuracao_consoante,
    provar_destino_do_servidor,
)

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9011")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIXO = "T018N"
EVIDENCIAS = os.path.join(ROOT, "docs", "tasks", "evidencias", "TASK_018", "navegador")

DIMENSOES = [(1366, 900), (1024, 768), (768, 1024), (412, 915)]


def abortar(motivo):
    print(f"[ABORTADO] {motivo}", file=sys.stderr)
    raise SystemExit(2)


arq_env = os.environ.get("SHAMBALA_ENV_FILE")
if not arq_env:
    abortar("SHAMBALA_ENV_FILE ausente — recuso rodar sem arquivo de teste exclusivo.")
try:
    exigir_configuracao_consoante(arq_env, os.environ.get("SHAMBALA_TEST_DB"))
except AmbienteInseguro as e:
    abortar(str(e))

import banco  # noqa: E402
import servico_conclusao_planos as sc  # noqa: E402
from banco import criar_tabelas, conectar  # noqa: E402
from test_tres_passes import _garantir_admin  # noqa: E402

with conectar() as conn:
    info = exigir_ambiente_de_teste(conn)
print(f"[ok] conexão do script: {info}")
try:
    prova = provar_destino_do_servidor(info["banco_efetivo"])
except AmbienteInseguro as e:
    abortar(str(e))
print(f"[ok] servidor HTTP ({BASE}) na execução corrente, mesmo banco efetivo: "
      f"{prova['banco_efetivo']} (token {prova['token']})")

CENARIOS = []
aprovadas = []
falhas = []
HOJE = date.today()


def cenario(nome):
    def wrap(fn):
        CENARIOS.append((nome, fn))
        return fn
    return wrap


def verificar(nome, condicao, detalhe=""):
    if condicao:
        aprovadas.append(nome)
        print(f"  ✔ {nome}")
    else:
        falhas.append(f"{nome} — {detalhe}")
        print(f"  ✘ {nome} — {detalhe}")


def _ferramentas_ausentes():
    faltam = []
    try:
        import playwright  # noqa: F401
    except ImportError:
        faltam.append("Playwright (python -m pip install playwright)")
    caminho = os.environ.get("SHAMBALA_CHROMIUM", "")
    if not caminho or not os.path.exists(caminho):
        caminho = next((c for c in ("/usr/bin/chromium", "/usr/bin/chromium-browser",
                                    "/usr/bin/google-chrome") if os.path.exists(c)), "")
    if not caminho or not os.path.exists(caminho):
        faltam.append("Chromium (a tela precisa rodar num navegador real)")
    else:
        os.environ["SHAMBALA_CHROMIUM"] = caminho
    return faltam


# ── Operador no navegador ────────────────────────────────────────────────────

def _cookie_sessao():
    cj = http.cookiejar.CookieJar()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    op = urllib.request.build_opener(NoRedirect, urllib.request.HTTPCookieProcessor(cj))
    corpo = urllib.parse.urlencode({"nome_usuario": "admin", "senha": "teste123"}).encode()
    try:
        op.open(urllib.request.Request(BASE + "/login", data=corpo, method="POST"))
    except urllib.error.HTTPError as e:
        if e.code != 303:
            raise AssertionError(f"login falhou (status {e.code})")
    for cookie in cj:
        if cookie.name == "sessao":
            return cookie.value
    raise AssertionError("o login não entregou o cookie de sessão")


class Operador:
    def __init__(self, pw, largura=1366, altura=900):
        self.browser = pw.chromium.launch(executable_path=os.environ["SHAMBALA_CHROMIUM"])
        self.context = self.browser.new_context(viewport={"width": largura, "height": altura})
        host = urllib.parse.urlsplit(BASE).hostname
        self.context.add_cookies([{"name": "sessao", "value": _cookie_sessao(),
                                   "domain": host, "path": "/"}])
        self.pagina = self.context.new_page()

    def abrir(self, caminho):
        self.pagina.goto(BASE + caminho)
        return self.pagina

    def fechar(self):
        with contextlib.suppress(Exception):
            self.browser.close()


@contextlib.contextmanager
def _operador(**opcoes):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        op = Operador(pw, **opcoes)
        try:
            yield op
        finally:
            op.fechar()


def _evidencia(pagina, nome, inteira=True):
    os.makedirs(EVIDENCIAS, exist_ok=True)
    pagina.screenshot(path=os.path.join(EVIDENCIAS, nome + ".png"), full_page=inteira)


def _sem_rolagem_horizontal(pagina) -> bool:
    return pagina.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1")


def _escolher_pessoa_e_agendar(pagina, med_id, nome_pessoa, data_iso, qtd="1"):
    """Percorre a INTERFACE real: abre a Agenda filtrada pelo médium, expande
    "Novo agendamento avulso", seleciona a pessoa pela lista de sugestões, põe a
    data e envia o formulário."""
    pagina.goto(f"{BASE}/agenda?medium_id={med_id}")
    pagina.click(".agenda-novo-avulso summary")
    pagina.fill("#avulso-busca", nome_pessoa)
    pagina.wait_for_selector("#avulso-sugestoes > div", timeout=8000)
    pagina.wait_for_timeout(300)
    pagina.locator("#avulso-sugestoes > div").first.dispatch_event("mousedown")
    pagina.wait_for_timeout(150)
    pagina.fill("#avulso-data", data_iso)
    pagina.fill("#avulso-qtd", qtd)
    pagina.click("#btn-agendar")
    pagina.wait_for_load_state("networkidle")
    return pagina


# ── Fixtures fictícias ───────────────────────────────────────────────────────
FIX = {}


def _plano(ctx, med, pes, total, inicio):
    """Plano nascido sob a regra (regra_conclusao=1), como criam o acolhimento
    e a Agenda depois da TASK_018."""
    cur = ctx.execute(
        """INSERT INTO planos_tratamento
           (medium_id, sessoes_total, sessoes_previstas, data_inicio, frequencia,
            sessoes_com_passe, status, regra_conclusao)
           VALUES (%s,%s,%s,%s,'semanal',%s,'ativo',1) RETURNING id""",
        (med, total, total, inicio.isoformat(),
         banco.calcular_sessoes_com_passe(total, 3)))
    pid = cur.fetchone()["id"]
    ctx.execute("INSERT INTO plano_pessoas (plano_id, pessoa_id) VALUES (%s,%s)",
                (pid, pes))
    banco.gerar_agendamentos_plano(ctx, pid, inicio, "semanal", total, 3)
    return pid


def _datas_livres(ctx, pid, n):
    permitidos = {r["dia_semana"] for r in ctx.execute(
        "SELECT dia_semana FROM dias_atendimento").fetchall()}
    usadas = {r["data"] for r in ctx.execute(
        "SELECT data FROM agendamentos WHERE plano_id = %s", (pid,)).fetchall()}
    out, i = [], 0
    while len(out) < n and i < 120:
        dia = HOJE + timedelta(days=i)
        i += 1
        if dia.weekday() in permitidos and dia.isoformat() not in usadas:
            out.append(dia.isoformat())
    return out


def _semear():
    with conectar() as conn:
        if not conn.execute("SELECT 1 FROM dias_atendimento LIMIT 1", None).fetchone():
            for d in (0, 2):
                conn.execute("INSERT INTO dias_atendimento (dia_semana, descricao) "
                             "VALUES (%s, %s)", (d, f"{PREFIXO} dia {d}"))
            FIX["dias_insertados"] = True

        def med(nome):
            return conn.execute(
                "INSERT INTO mediuns (nome_completo, ativo, vagas_dia) "
                "VALUES (%s, 1, 10) RETURNING id", (nome,)).fetchone()["id"]

        def pes(nome):
            return conn.execute(
                "INSERT INTO pessoas (nome_apresentacao, nome_completo) "
                "VALUES (%s, %s) RETURNING id", (nome, nome)).fetchone()["id"]

        FIX["med_a"] = med(f"{PREFIXO} Médio Âncora")
        FIX["med_b"] = med(f"{PREFIXO} Médio Outro")

        # escolha: dois planos ativos do MESMO médium
        FIX["pes_escolha"] = pes(f"{PREFIXO} Atendida Escolha")
        FIX["plano_e1"] = _plano(conn, FIX["med_a"], FIX["pes_escolha"], 4,
                                 HOJE - timedelta(days=56))
        FIX["plano_e2"] = _plano(conn, FIX["med_a"], FIX["pes_escolha"], 4,
                                 HOJE - timedelta(days=14))

        # conflito: plano ativo só com o médium A, agendamento tentado no B
        FIX["pes_conflito"] = pes(f"{PREFIXO} Atendida Conflito")
        FIX["plano_c"] = _plano(conn, FIX["med_a"], FIX["pes_conflito"], 3,
                                HOJE - timedelta(days=21))

        # concluído: todas as sessões cumpridas pela transição real do serviço
        FIX["pes_fim"] = pes(f"{PREFIXO} Atendida Concluída")
        FIX["plano_f"] = _plano(conn, FIX["med_a"], FIX["pes_fim"], 3,
                                HOJE - timedelta(days=35))
        admin = conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario = 'admin'").fetchone()["id"]
        for s in conn.execute(
                "SELECT id FROM agendamentos WHERE plano_id = %s ORDER BY data",
                (FIX["plano_f"],)).fetchall():
            sc.marcar_realizado(conn, FIX["plano_f"], s["id"], admin)
        conn.commit()
        status = conn.execute(
            "SELECT status FROM planos_tratamento WHERE id = %s",
            (FIX["plano_f"],)).fetchone()["status"]
        FIX["plano_f_concluido"] = status == "concluido"
        FIX["data_escolha"] = _datas_livres(conn, FIX["plano_e2"], 1)[0]
        FIX["data_conflito"] = _datas_livres(conn, FIX["plano_c"], 1)[0]
    print(f"[seed] fixtures {PREFIXO} prontas")


def _limpar():
    with conectar() as conn:
        pids = [FIX["plano_e1"], FIX["plano_e2"], FIX["plano_c"], FIX["plano_f"]]
        pessoas = [FIX["pes_escolha"], FIX["pes_conflito"], FIX["pes_fim"]]
        meds = [FIX["med_a"], FIX["med_b"]]
        conn.execute("DELETE FROM checkins WHERE plano_id = ANY(%s)", (pids,))
        conn.execute("DELETE FROM agendamentos WHERE plano_id = ANY(%s)", (pids,))
        conn.execute("DELETE FROM plano_pessoas WHERE plano_id = ANY(%s)", (pids,))
        conn.execute("DELETE FROM plano_chave_idempotencia WHERE plano_id = ANY(%s)",
                     (pids,))
        conn.execute("DELETE FROM planos_tratamento WHERE id = ANY(%s)", (pids,))
        conn.execute("DELETE FROM pessoas WHERE id = ANY(%s)", (pessoas,))
        conn.execute("DELETE FROM mediuns WHERE id = ANY(%s)", (meds,))
        if FIX.get("dias_insertados"):
            conn.execute("DELETE FROM dias_atendimento WHERE descricao LIKE %s",
                         (PREFIXO + "%",))


def _conferir_rastro():
    with conectar() as conn:
        c = conn.execute("SELECT count(*) AS c FROM pessoas WHERE nome_completo LIKE %s",
                         (PREFIXO + "%",)).fetchone()["c"]
        m = conn.execute("SELECT count(*) AS c FROM mediuns WHERE nome_completo LIKE %s",
                         (PREFIXO + "%",)).fetchone()["c"]
        p = conn.execute(
            """SELECT count(*) AS c FROM planos_tratamento pt
               JOIN plano_pessoas pp ON pp.plano_id = pt.id
               WHERE pp.pessoa_id = ANY(%s)""", (
                [FIX["pes_escolha"], FIX["pes_conflito"], FIX["pes_fim"]],
            )).fetchone()["c"]
    assert c == 0 and m == 0 and p == 0, f"resíduo: {c} pessoas, {m} médiuns, {p} planos"


# ── Cenários ─────────────────────────────────────────────────────────────────

@cenario("vários planos ativos: a tela de destino pede a escolha, por médium e data de início")
def n_escolha():
    with _operador() as op:
        pagina = _escolher_pessoa_e_agendar(
            op.pagina, FIX["med_a"], f"{PREFIXO} Atendida Escolha", FIX["data_escolha"])
        texto = pagina.inner_text("body")
        verificar("chama a tela de destino",
                  "Confirmar destino do atendimento" in texto, pagina.url)
        verificar("diz que nada foi gravado", "nada é gravado" in texto.lower())
        verificar("oferece um rádio por plano ativo",
                  pagina.locator('input[name="plano_destino"]').count() == 2)
        verificar("refere os planos pelo início (DD/MM/AAAA), não por id",
                  pagina.inner_text("body").count(
                      (HOJE - timedelta(days=56)).strftime("%d/%m/%Y")) >= 1
                  and pagina.inner_text("body").count(
                      (HOJE - timedelta(days=14)).strftime("%d/%m/%Y")) >= 1)
        verificar("oferece o botão de acesso ao plano",
                  "Abrir agenda deste plano" in texto)
        verificar("sem id de plano virando instrução operacional",
                  not any(f"plano {pid}" in texto
                          for pid in (FIX["plano_e1"], FIX["plano_e2"])))
        _evidencia(pagina, "01-escolha-destino")

        with conectar() as conn:
            antes = conn.execute(
                "SELECT count(*) AS c FROM agendamentos WHERE plano_id = ANY(%s)",
                ([FIX["plano_e1"], FIX["plano_e2"]],)).fetchone()["c"]
        verificar("nada foi escrito antes de confirmar", antes == 8, f"{antes} sessões")

        # escolhe o plano mais recente e confirma
        pagina.locator('input[name="plano_destino"]').nth(0).click()
        pagina.click("button:has-text('Confirmar e acrescentar sessão')")
        pagina.wait_for_load_state("networkidle")
        texto = pagina.inner_text("body")
        verificar("confirmação grava no plano escolhido",
                  "acrescentado ao plano com" in texto, texto[:200])
        verificar("o aviso de sucesso traz a referência do plano",
                  (HOJE - timedelta(days=14)).strftime("%d/%m/%Y") in texto
                  or (HOJE - timedelta(days=56)).strftime("%d/%m/%Y") in texto)
        with conectar() as conn:
            depois = conn.execute(
                "SELECT count(*) AS c FROM agendamentos WHERE plano_id = ANY(%s)",
                ([FIX["plano_e1"], FIX["plano_e2"]],)).fetchone()["c"]
            extras = conn.execute(
                """SELECT count(*) AS c FROM planos_tratamento
                   WHERE id = ANY(%s) AND sessoes_adicionais = 1""",
                ([FIX["plano_e1"], FIX["plano_e2"]],)).fetchone()["c"]
        verificar("exatamente uma sessão a mais, em um dos planos",
                  depois == 9 and extras == 1, f"depois={depois} extras={extras}")
        _evidencia(pagina, "02-destino-confirmado")


@cenario("plano ativo de outro médium: a tela explica o conflito e aponta o plano permitido")
def n_conflito():
    with _operador() as op:
        pagina = _escolher_pessoa_e_agendar(
            op.pagina, FIX["med_b"], f"{PREFIXO} Atendida Conflito", FIX["data_conflito"])
        texto = pagina.inner_text("body")
        verificar("explica o conflito de médium",
                  "plano ativo com outro médium" in texto, texto[:300])
        verificar("não criou plano paralelo em segredo",
                  "Confirmar e acrescentar sessão" not in texto)
        verificar("aponta o plano existente com o outro médium",
                  f"{PREFIXO} Médio Âncora" in texto)
        with conectar() as conn:
            n = conn.execute(
                "SELECT count(*) AS c FROM agendamentos WHERE plano_id = %s",
                (FIX["plano_c"],)).fetchone()["c"]
            planos = conn.execute(
                "SELECT count(*) AS c FROM plano_pessoas WHERE pessoa_id = %s",
                (FIX["pes_conflito"],)).fetchone()["c"]
        verificar("nada foi gravado", n == 3 and planos == 1, f"{n} sessões, {planos} planos")
        _evidencia(pagina, "03-conflito-medio")


@cenario("recusa na confirmação preserva o formulário (data, pessoa e médium continuam lá)")
def n_preservacao():
    with conectar() as conn:
        # data livre para o plano que a tela apresenta primeiro (início mais recente)
        data = _datas_livres(conn, FIX["plano_e2"], 1)[0]
    with _operador() as op:
        pagina = _escolher_pessoa_e_agendar(
            op.pagina, FIX["med_a"], f"{PREFIXO} Atendida Escolha", data)
        pagina.locator('input[name="plano_destino"]').nth(0).click()
        pagina.click("button:has-text('Confirmar e acrescentar sessão')")
        pagina.wait_for_load_state("networkidle")
        verificar("primeira inclusão aceita",
                  "acrescentado ao plano com" in pagina.inner_text("body"),
                  pagina.url)

        # repete a MESMA data: recusa, reapresentando tudo o que foi digitado
        pagina = _escolher_pessoa_e_agendar(
            op.pagina, FIX["med_a"], f"{PREFIXO} Atendida Escolha", data)
        pagina.locator('input[name="plano_destino"]').nth(0).click()
        pagina.click("button:has-text('Confirmar e acrescentar sessão')")
        pagina.wait_for_load_state("networkidle")
        texto = pagina.inner_text("body")
        verificar("recusa a duplicidade na mesma data",
                  "Já existe atendimento agendado neste plano" in texto, texto[:300])
        valor_data = pagina.input_value('input[name="data"]')
        verificar("preserva a data digitada", valor_data == data, valor_data)
        verificar("preserva a pessoa escolhida",
                  pagina.input_value('input[name="pessoa_id"]') == str(FIX["pes_escolha"]))
        verificar("preserva o médium",
                  pagina.input_value('input[name="medium_id"]') == str(FIX["med_a"]))
        _evidencia(pagina, "04-recusa-formulario-preservado")


@cenario("status Concluído é lido nas telas de plano, de médiuns e da ficha da pessoa")
def n_concluido():
    if not FIX["plano_f_concluido"]:
        verificar("plano de exemplo concluído pelo cumprimento", False,
                  "a conclusão automática não ocorreu no seed")
        return
    with _operador() as op:
        pagina = op.abrir(
            f"/cadastros/mediuns/{FIX['med_a']}/planos/{FIX['plano_f']}/agenda")
        texto = pagina.inner_text("body")
        verificar("agenda do plano diz Concluído pelo cumprimento",
                  "Concluído pelo cumprimento" in texto, texto[:200])
        verificar("não apresenta como cancelado", "Cancelado" not in texto)
        verificar("não oferece acrescentar sessão em plano concluído",
                  pagina.locator('input[name="data"]').count() == 0)
        _evidencia(pagina, "05-agenda-plano-concluido")

        pagina = op.abrir(f"/cadastros/mediuns/{FIX['med_a']}/planos")
        # o histórico é uma seção recolhida: abrir antes de ler
        pagina.click("summary:has-text('Histórico')")
        pagina.wait_for_timeout(150)
        verificar("lista de planos do médium traz a situação",
                  "Concluído" in pagina.inner_text("body"),
                  pagina.inner_text("body")[:200])

        pagina = op.abrir(f"/cadastros/pessoas/{FIX['pes_fim']}")
        texto_ficha = pagina.inner_text("body")
        # a ficha mostra o status em caixa alta (CSS), então a comparação é folgada
        verificar("ficha da pessoa traz a situação Concluído",
                  "concluído" in texto_ficha.lower(), texto_ficha[:400])
        _evidencia(pagina, "06-ficha-pessoa-concluida")


@cenario("layout: sem rolagem horizontal em 1366, 1024, 768 e 412 nas telas novas")
def n_celular():
    for largura, altura in DIMENSOES:
        with _operador(largura=largura, altura=altura) as op:
            pagina = _escolher_pessoa_e_agendar(
                op.pagina, FIX["med_a"], f"{PREFIXO} Atendida Escolha",
                FIX["data_escolha"])
            ok = _sem_rolagem_horizontal(pagina)
            verificar(f"sem rolagem horizontal em {largura}", ok,
                      f"largura do documento em {largura}px")
            if largura in (1366, 412):
                _evidencia(pagina, f"07-destino-{largura}")


def main():
    faltam = _ferramentas_ausentes()
    if faltam:
        print("[ABORTADO] ferramenta ausente: " + "; ".join(faltam), file=sys.stderr)
        return 3
    criar_tabelas()
    _garantir_admin()
    _semear()
    try:
        for nome, fn in CENARIOS:
            print(f"-- {nome}")
            try:
                fn()
            except Exception as e:
                falhas.append(f"{nome} — exceção {type(e).__name__}: {e}")
                print(f"  ✘ {nome} — exceção {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
    finally:
        try:
            _limpar()
            _conferir_rastro()
        except Exception as e:
            falhas.append(f"limpeza — {type(e).__name__}: {e}")
            print(f"[limpeza] ERRO: {e}")
    print(f"\n[resultado] aprovadas={len(aprovadas)} falhas={len(falhas)}")
    for f in falhas:
        print(f"  ✘ {f}")
    print(f"  evidências: {EVIDENCIAS}")
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
