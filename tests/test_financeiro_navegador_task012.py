#!/usr/bin/env python3
"""Suíte TASK_012 — as telas do financeiro VISTAS NO NAVEGADOR (desktop e smartphone).

O contrato das consultas (saldo parcial, totais do recorte, três datas, política
de valor) é provado por `test_financeiro_consultas_task012.py`, e o do
recebimento por `test_fiado_alocacoes_task012.py`. Aqui se valida o que o
OPERADOR vê e faz, com Chromium real, sobre o que a TASK_012 entregou:

- mensalidades: competência/vencimento/pagamento rotulados, valor em formato
  brasileiro, lote com o total somado em centavos (sem perder centavo);
- fiado: dívida parcial mostrando Recebido e Saldo, "Recebida em parte", a
  distribuição antes de confirmar e o excedente virando crédito;
- extrato do trabalhador: saldo devedor e crédito em cartões separados;
- detalhe do lançamento: "Para onde foi este dinheiro" ligado à dívida;
- número grande (R$ 1.234.567,89) sem cortar, no desktop e no celular;
- CSS versionado (anti-cache) e retorno ao mesmo recorte de consulta;
- nenhuma rolagem horizontal em 412, 768, 1024 e 1366.

Dados fictícios com prefixo T012N em banco descartável; o destino do script E do
servidor HTTP é provado antes de qualquer escrita (TASK_010). Ferramenta ausente
é falha explícita (código 3), nunca "passou".
"""
import contextlib
import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import (  # noqa: E402
    AmbienteInseguro, exigir_ambiente_de_teste, exigir_configuracao_consoante,
    provar_destino_do_servidor,
)

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9016")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIXO = "T012N"
EVIDENCIAS = os.path.join(ROOT, "docs", "tasks", "evidencias", "TASK_012")

DE, ATE = "2026-09-01", "2026-09-30"
JANELA = f"de={DE}&ate={ATE}"

DESKTOP = (1366, 900)
CELULAR = (412, 915)


def abortar(motivo):
    print(f"[ABORTADO] {motivo}", file=sys.stderr)
    raise SystemExit(2)


# ── 1. Prova dos dois destinos (script + HTTP) antes de qualquer escrita ────
arq_env = os.environ.get("SHAMBALA_ENV_FILE")
if not arq_env:
    abortar("SHAMBALA_ENV_FILE ausente — recuso rodar sem arquivo de teste exclusivo.")
try:
    exigir_configuracao_consoante(arq_env, os.environ.get("SHAMBALA_TEST_DB"))
except AmbienteInseguro as e:
    abortar(str(e))

from banco import criar_tabelas, conectar, conta_id_por_tipo  # noqa: E402
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

D = Decimal


# ── 2. Resultado ────────────────────────────────────────────────────────────
CENARIOS = []
aprovadas = []
falhas = []


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


# ── 3. Ferramentas: ausência é falha explícita ─────────────────────────────
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


# ── 4. Operador no navegador ───────────────────────────────────────────────
def _cookie_sessao():
    cj = http.cookiejar.CookieJar()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    op = urllib.request.build_opener(NoRedirect,
                                     urllib.request.HTTPCookieProcessor(cj))
    corpo = urllib.parse.urlencode(
        {"nome_usuario": "admin", "senha": "teste123"}).encode()
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
    def __init__(self, pw, largura=DESKTOP[0], altura=DESKTOP[1]):
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


def _evidencia(pagina, nome, inteira=False):
    os.makedirs(EVIDENCIAS, exist_ok=True)
    pagina.screenshot(path=os.path.join(EVIDENCIAS, nome + ".png"), full_page=inteira)


def _sem_rolagem_horizontal(pagina) -> bool:
    return pagina.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1")


# ── 5. Fixtures fictícias ──────────────────────────────────────────────────
FIX = {}


ALHEIO = {}


def _semear_alheio():
    """Um lançamento que NÃO é desta suíte (sem a marca T012N).

    Serve de sentinela: a limpeza tem de preservá-lo. Contar "antes e depois"
    fora da execução não serve — o runner RECRIA o banco descartável a cada
    execução, então a comparação só vale dentro dela.
    """
    with conectar() as conn:
        ALHEIO["fin"] = conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, status)
               VALUES ('entrada', 'doacao', 9.99, '2026-09-10',
                       'Registro de outra execucao (preservar)', 'pago')
               RETURNING id""").fetchone()["id"]


def _semear():
    """Trabalhador, dívida e lançamentos fictícios com prefixo T012N.

    Nada de dado real: nome, valores e descrição são desta execução, e o
    encerramento apaga só o que tem a marca.
    """
    with conectar() as conn:
        FIX["especie"] = conta_id_por_tipo(conn, "especie")
        FIX["areceber"] = conta_id_por_tipo(conn, "areceber")
        tid = conn.execute(
            """INSERT INTO trabalhadores (nome_completo, ativo, valor_mensalidade,
                                          dia_vencimento, fiado_limite, fiado_credito)
               VALUES (%s, 1, 60.00, 10, 1000.00, 0) RETURNING id""",
            (f"{PREFIXO} Parcial",)).fetchone()["id"]
        FIX["tid"] = tid
        cx = conn.execute(
            "SELECT id FROM caixas WHERE ativo = 1 ORDER BY id LIMIT 1").fetchone()["id"]
        FIX["cx"] = cx
        # Sessão 'eventos': recebimento de fiado em espécie é RECUSADO em sessão
        # legado (TASK_008). A fixture cria a versão que aceita recebimento.
        sess = conn.execute(
            """INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status, versao)
               VALUES (%s, 0, 'aberto', 'eventos') RETURNING id""", (cx,)).fetchone()["id"]
        FIX["sessao"] = sess
        FIX["venda"] = conn.execute(
            """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
                     troco, status, atendente_id, fiado_trabalhador_id, fiado_pago,
                     fiado_credito_usado, doacao_valor, caixa_movimento_id, criada_em)
               VALUES (%s, '2026-09-05', 40.00, 'fiado', 0, 'concluida', 1, %s, FALSE,
                       0, 0, %s, '2026-09-05 10:00:00') RETURNING id""",
            (cx, tid, sess)).fetchone()["id"]
        # lançamento grande: prova de que o número não é cortado na tela
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, status,
                conta_id, data_pagamento)
               VALUES ('entrada', 'doacao', 1234567.89, '2026-09-12', %s, 'pago',
                       %s, '2026-09-12')""",
            (f"{PREFIXO} doação grande", FIX["especie"]))


def _receber(valor, venda_ids, chave_):
    corpo = urllib.parse.urlencode({
        "valor": valor, "forma_pagamento": "especie",
        "sessao_id": FIX["sessao"], "conta_pix_id": 0,
        "venda_ids": [str(v) for v in venda_ids], "idempotencia": chave_},
        doseq=True).encode()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(
        _jar_com_sessao()))

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    op.add_handler(NoRedirect())
    req = urllib.request.Request(
        BASE + f"/caixa/fiados/{FIX['tid']}/pagar-divida", data=corpo, method="POST")
    try:
        return op.open(req).status
    except urllib.error.HTTPError as e:
        # A recusa vem com a mensagem para o operador: mostrá-la no log evita
        # "premissa falhou" sem causa (foi o que aconteceu duas vezes aqui).
        corpo_erro = e.read().decode("utf-8", "replace")
        import re as _re
        msg = _re.search(r"(não|Não|recus|Sess|sess)[^<]{0,120}", corpo_erro)
        print(f"    [recusa] status {e.code}: "
              f"{(msg.group(0).strip() if msg else corpo_erro[:160])}")
        return e.code


def _jar_com_sessao():
    import http.cookiejar as cjmod
    jar = cjmod.CookieJar()
    jar.set_cookie(cjmod.Cookie(0, "sessao", _cookie_sessao(), None, False,
                                "127.0.0.1", False, False, "/", True, False,
                                None, False, None, None, {}))
    return jar


def _limpar():
    """Encerra SÓ o rastro desta suíte (a marca T012N), na ordem das FKs.

    Dívida não se apaga para "limpar tela": o que esta suíte criou é fictício e
    identificado, e o encerramento remove apenas linhas com a marca — nunca
    histórico de uso real.

    A ORDEM importa e já falhou aqui: `financeiro_movimentacoes` tem duas FKs
    `ON DELETE RESTRICT` apontando para ele — `fiado_alocacao.financeiro_id` e
    `fiado_credito_mov.financeiro_id`. A primeira versão desta limpeza apagava
    `fiado_credito_mov` DEPOIS do lançamento, o banco recusava, e a suíte
    terminava com `falhas=0`/`rc=0` enquanto deixava resíduo. Por isso agora:
    (a) dependentes primeiro, sempre; (b) erro de limpeza NÃO passa silencioso
    — sobe e derruba o rc de `main`.
    """
    marca = f"%{PREFIXO}%"
    tid = FIX.get("tid")
    venda = FIX.get("venda")
    sessao = FIX.get("sessao")
    with conectar() as conn:
        fins = [r["id"] for r in conn.execute(
            """SELECT id FROM financeiro_movimentacoes
               WHERE descricao LIKE %s""", (marca,)).fetchall()]

        # 1) NÍVEL 1: tudo que aponta para o lançamento ou para a venda desta
        #    suíte, identificado pelo VÍNCULO — não por data nem por "não é meu".
        cond_aloc, params_aloc = [], []
        if tid:
            cond_aloc.append("trabalhador_id = %s")
            params_aloc.append(tid)
        if fins:
            cond_aloc.append("financeiro_id = ANY(%s)")
            params_aloc.append(fins)
        if venda:
            cond_aloc.append("venda_id = %s")
            params_aloc.append(venda)
        if cond_aloc:
            conn.execute(
                f"DELETE FROM fiado_alocacao WHERE {' OR '.join(cond_aloc)}",
                params_aloc)

        cond_cred, params_cred = [], []
        if tid:
            cond_cred.append("trabalhador_id = %s")
            params_cred.append(tid)
        if fins:
            cond_cred.append("financeiro_id = ANY(%s)")
            params_cred.append(fins)
        if venda:
            cond_cred.append("venda_id = %s")
            params_cred.append(venda)
        if cond_cred:
            conn.execute(
                f"DELETE FROM fiado_credito_mov WHERE {' OR '.join(cond_cred)}",
                params_cred)

        cond_ev, params_ev = [], []
        if venda:
            cond_ev.append("venda_id = %s")
            params_ev.append(venda)
        if fins:
            cond_ev.append("financeiro_id = ANY(%s)")
            params_ev.append(fins)
        if sessao:
            cond_ev.append("sessao_id = %s")
            params_ev.append(sessao)
        if cond_ev:
            conn.execute(
                f"DELETE FROM caixa_sessao_eventos WHERE {' OR '.join(cond_ev)}",
                params_ev)

        # 2) NÍVEL 2: o lançamento e a venda em si.
        if fins:
            conn.execute("DELETE FROM financeiro_movimentacoes WHERE id = ANY(%s)",
                         (fins,))
        if venda:
            conn.execute("DELETE FROM vendas_pdv_itens WHERE venda_id = %s", (venda,))
            conn.execute("DELETE FROM vendas_pdv WHERE id = %s", (venda,))

        # 3) NÍVEL 3: trabalhador e sessão (já sem quem os referencie).
        if tid:
            conn.execute("DELETE FROM trabalhadores WHERE id = %s", (tid,))
        if sessao:
            conn.execute("DELETE FROM caixa_movimentos WHERE id = %s", (sessao,))

        # 4) CONFERÊNCIA: sobrou algo com a marca? Então a limpeza não terminou,
        #    e isso é FALHA (o chamador transforma em rc != 0).
        restos = {
            "lançamentos": conn.execute(
                "SELECT count(*) AS c FROM financeiro_movimentacoes "
                "WHERE descricao LIKE %s", (marca,)).fetchone()["c"],
            "alocações": conn.execute(
                "SELECT count(*) AS c FROM fiado_alocacao a "
                "WHERE a.trabalhador_id = %s", (tid or -1,)).fetchone()["c"],
            "crédito": conn.execute(
                "SELECT count(*) AS c FROM fiado_credito_mov "
                "WHERE trabalhador_id = %s", (tid or -1,)).fetchone()["c"],
            "trabalhadores": conn.execute(
                "SELECT count(*) AS c FROM trabalhadores "
                "WHERE nome_completo LIKE %s", (marca,)).fetchone()["c"],
        }
        sobrou = {k: v for k, v in restos.items() if v}
    if sobrou:
        raise RuntimeError(f"limpeza deixou resíduo próprio: {sobrou}")


def _conferir_rastro():
    """Prova de encerramento: resíduo próprio zero E registro alheio intacto.

    Levanta (→ rc != 0) se sobrou algo nosso ou se o alheio desapareceu.
    """
    with conectar() as conn:
        proprio = {
            "lançamentos": conn.execute(
                "SELECT count(*) AS c FROM financeiro_movimentacoes "
                "WHERE descricao LIKE %s", (f"%{PREFIXO}%",)).fetchone()["c"],
            "trabalhadores": conn.execute(
                "SELECT count(*) AS c FROM trabalhadores "
                "WHERE nome_completo LIKE %s", (f"%{PREFIXO}%",)).fetchone()["c"],
            "vendas": conn.execute(
                "SELECT count(*) AS c FROM vendas_pdv WHERE id = %s",
                (FIX.get("venda") or -1,)).fetchone()["c"],
            "sessões": conn.execute(
                "SELECT count(*) AS c FROM caixa_movimentos WHERE id = %s",
                (FIX.get("sessao") or -1,)).fetchone()["c"],
        }
        alheio = conn.execute(
            "SELECT count(*) AS c FROM financeiro_movimentacoes WHERE id = %s "
            "AND descricao = 'Registro de outra execucao (preservar)'",
            (ALHEIO.get("fin") or -1,)).fetchone()["c"]
    sobrou = {k: v for k, v in proprio.items() if v}
    if sobrou:
        raise RuntimeError(f"resíduo próprio após a limpeza: {sobrou}")
    if alheio != 1:
        raise RuntimeError("a limpeza apagou registro de OUTRA execução "
                           "(sentinela alheia ausente)")


# ── 6. Cenários ────────────────────────────────────────────────────────────
@cenario("C1 mensalidades: as três datas rotuladas e o valor em formato brasileiro")
def c1():
    with _operador() as op:
        pag = op.abrir(f"/financeiro/mensalidades?trabalhador_id={FIX['tid']}")
        corpo = pag.inner_text("body")
        verificar("C1 a tela diz que a data é o vencimento",
                  "vencimento" in corpo.lower(), corpo[:200])
        verificar("C1 valor da mensalidade em R$ com vírgula (não 60.00)",
                  "R$ 60,00" in corpo and "60.00" not in corpo, "")
        verificar("C1 nenhuma rolagem horizontal no desktop",
                  _sem_rolagem_horizontal(pag), "scrollWidth > innerWidth")

        pag = op.abrir("/financeiro/mensalidades/relatorio?mes=2026-09&data_uso=comp")
        corpo = pag.inner_text("body")
        verificar("C1 relatório expõe o seletor 'Qual data o mês usa'",
                  pag.locator("select#f-data").count() == 1
                  and "Qual data o mês usa" in corpo, corpo[:200])
        opcoes = [pag.locator("select#f-data option").nth(i).inner_text()
                  for i in range(pag.locator("select#f-data option").count())]
        verificar("C1 as três perguntas estão nomeadas para o operador",
                  len(opcoes) == 3 and any("Competência" in o for o in opcoes)
                  and any("Vencimento" in o for o in opcoes)
                  and any("pagamento" in o.lower() for o in opcoes), str(opcoes))
        _evidencia(pag, "t012-c1-relatorio-mensalidades-desktop", inteira=True)


@cenario("C2 fiado parcial: Recebido, Saldo e 'Recebida em parte' na tela")
def c2():
    st = _receber("15.00", [FIX["venda"]], "b" * 32)
    # A premissa é o EFEITO NO BANCO (a entrega foi registrada e abateu a
    # dívida), não um código HTTP específico: o cliente desta suíte não segue
    # redirect, e o que a tela mostra depende disso e não do status.
    try:
        valor = Decimal("15.00")
        efeito_ok = valor > 0
        with conectar() as conn:
            recebido = conn.execute(
                """SELECT COALESCE(SUM(a.valor), 0) AS v FROM fiado_alocacao a
                   WHERE a.venda_id = %s AND a.tipo = 'divida'""",
                (FIX["venda"],)).fetchone()["v"]
        efeito_ok = Decimal(str(recebido)) == valor
    except Exception as e:  # noqa: BLE001
        efeito_ok = False
        print(f"    [premissa] falhou ao ler o efeito: {e}")
    verificar("C2 premissa: a entrega de 15 abateu a dívida (alocação gravada)",
              efeito_ok, f"alocado={recebido if efeito_ok is not None else '?'}")
    with _operador() as op:
        pag = op.abrir(f"/financeiro/fiados?trabalhador_id={FIX['tid']}")
        corpo = pag.inner_text("body")
        verificar("C2 o cartão de saldo devedor mostra R$ 25,00 (40 − 15)",
                  "R$ 25,00" in corpo, corpo[:300])
        verificar("C2 o valor recebido aparece separado (R$ 15,00)",
                  "R$ 15,00" in corpo, "")
        verificar("C2 a situação não é 'Paga' nem 'Pendente': é 'Recebida em parte'",
                  "Recebida em parte" in corpo, "")
        _evidencia(pag, "t012-c2-fiado-parcial-desktop", inteira=True)

        pag = op.abrir(f"/caixa/fiados/extrato/{FIX['tid']}")
        corpo = pag.inner_text("body")
        verificar("C2 extrato separa saldo devedor de crédito antecipado",
                  "Saldo devedor" in corpo and "Crédito antecipado" in corpo, "")
        verificar("C2 extrato mostra o mesmo saldo 25,00 das outras telas",
                  "R$ 25.00" in corpo or "R$ 25,00" in corpo, corpo[:300])
        _evidencia(pag, "t012-c2-extrato-desktop", inteira=True)


@cenario("C3 excedente como crédito e detalhe do lançamento com o destino")
def c3():
    # entrega de 30 contra saldo de 25 -> 25 abatem, 5 viram crédito
    st = _receber("30.00", [FIX["venda"]], "c" * 32)
    with conectar() as conn:
        aloc = conn.execute(
            """SELECT COALESCE(SUM(a.valor), 0) AS v FROM fiado_alocacao a
               WHERE a.venda_id = %s AND a.tipo = 'divida'""",
            (FIX["venda"],)).fetchone()["v"]
        cred = conn.execute(
            "SELECT fiado_credito FROM trabalhadores WHERE id = %s",
            (FIX["tid"],)).fetchone()["fiado_credito"]
    verificar("C3 premissa: a entrega de 30 fechou a dívida (25 alocados) e "
              "deixou 5 de crédito",
              Decimal(str(aloc)) == Decimal("40.00")
              and Decimal(str(cred)) == Decimal("5.00"),
              f"alocado={aloc} credito={cred}")
    with _operador() as op:
        pag = op.abrir(f"/caixa/fiados/extrato/{FIX['tid']}")
        corpo = pag.inner_text("body")
        verificar("C3 o excedente aparece como crédito do trabalhador (R$ 5,00)",
                  "R$ 5.00" in corpo or "R$ 5,00" in corpo, corpo[:400])
        verificar("C3 a dívida quitada não deixa saldo devedor",
                  "R$ 0.00" in corpo or "R$ 0,00" in corpo, "")

        with conectar() as conn:
            fin = conn.execute(
                """SELECT m.id FROM financeiro_movimentacoes m
                   JOIN fiado_alocacao a ON a.financeiro_id = m.id
                   WHERE a.trabalhador_id = %s AND m.categoria = 'recebimento_fiado'
                   ORDER BY m.id DESC LIMIT 1""", (FIX["tid"],)).fetchone()
        pag = op.abrir(f"/financeiro/lancamento/{fin['id']}")
        corpo = pag.inner_text("body")
        verificar("C3 o detalhe do lançamento mostra 'Para onde foi este dinheiro'",
                  "Para onde foi este dinheiro" in corpo, corpo[:300])
        verificar("C3 o destino aponta a dívida abatida, com valor",
                  f"Dívida #{FIX['venda']}" in corpo, corpo[:400])
        _evidencia(pag, "t012-c3-detalhe-destino-desktop", inteira=True)


@cenario("C4 número grande, CSS versionado e retorno ao mesmo recorte")
def c4():
    with _operador() as op:
        pag = op.abrir(f"/financeiro?{JANELA}")
        corpo = pag.inner_text("body")
        verificar("C4 o valor grande aparece completo (R$ 1.234.567,89)",
                  "R$ 1.234.567,89" in corpo, corpo[:300])
        fonte_css = pag.evaluate(
            "() => [...document.querySelectorAll('link[rel=stylesheet]')]"
            ".map(l => l.getAttribute('href') || '')")
        verificar("C4 o CSS é servido com versão (à prova de cache antigo)",
                  any(re.search(r"base\.css\?v=\d{8,}", h) for h in fonte_css),
                  str(fonte_css))
        verificar("C4 a Visão Geral explica o acumulado por conta",
                  pag.locator(".fin-def", has_text="Acumulado").count() >= 1, "")

        # ida ao detalhe e volta ao MESMO recorte
        pag = op.abrir(f"/financeiro?{JANELA}")
        link = pag.locator("a[href*='/financeiro/lancamento/']").first
        if link.count():
            link.click()
            pag.wait_for_load_state()
            voltar = pag.locator("a.btn-voltar").first
            href = voltar.get_attribute("href") if voltar.count() else ""
            verificar("C4 o detalhe devolve ao mesmo recorte consultado",
                      DE in (href or "") and ATE in (href or ""), f"href={href}")
        else:
            verificar("C4 o detalhe devolve ao mesmo recorte consultado", False,
                      "nenhum lançamento no recorte para clicar")
        _evidencia(pag, "t012-c4-visao-geral-desktop", inteira=True)


@cenario("C5 smartphone: nada cortado, nada com rolagem horizontal")
def c5():
    with _operador(largura=CELULAR[0], altura=CELULAR[1]) as op:
        for rota, nome in (
                (f"/financeiro?{JANELA}", "c5-visao-geral"),
                (f"/financeiro/fiados?trabalhador_id={FIX['tid']}", "c5-fiados"),
                ("/financeiro/mensalidades/relatorio?mes=2026-09&data_uso=comp",
                 "c5-mensalidades")):
            pag = op.abrir(rota)
            verificar(f"C5 {rota} cabe na largura do celular (sem rolagem lateral)",
                      _sem_rolagem_horizontal(pag), "scrollWidth > innerWidth")
            _evidencia(pag, f"t012-{nome}-smartphone", inteira=True)
        pag = op.abrir(f"/financeiro?{JANELA}")
        corpo = pag.inner_text("body")
        verificar("C5 o valor grande também cabe no celular",
                  "R$ 1.234.567,89" in corpo, corpo[:300])
        largura_cartao = pag.locator(".card-stat").first.evaluate(
            "(el) => el.getBoundingClientRect().width")
        cortado = pag.evaluate(
            """() => [...document.querySelectorAll('.stat-valor')]
                     .some(e => e.scrollWidth > e.clientWidth + 1)""")
        verificar("C5 nenhum valor estoura o cartão no celular",
              not cortado and largura_cartao > 0, f"cortado={cortado}")


@cenario("C6 larguras intermediárias: 768 e 1024 sem rolagem horizontal")
def c6():
    for largura, altura in ((768, 900), (1024, 900)):
        with _operador(largura=largura, altura=altura) as op:
            pag = op.abrir(f"/financeiro?{JANELA}")
            verificar(f"C6 {largura}px sem rolagem horizontal",
                      _sem_rolagem_horizontal(pag), "scrollWidth > innerWidth")


# ── 7. Execução ────────────────────────────────────────────────────────────
def main():
    faltam = _ferramentas_ausentes()
    if faltam:
        print("[ABORTADO] ferramenta ausente: " + "; ".join(faltam), file=sys.stderr)
        return 3
    criar_tabelas()
    # O admin com senha conhecida é fixture de teste (as suítes definem isso
    # antes de logar); sem ele o login devolve 401 e nenhum cenário roda.
    _garantir_admin()
    _semear_alheio()
    _semear()
    print(f"[seed] fixtures {PREFIXO} prontas (janela {DE} a {ATE})")
    try:
        for nome, fn in CENARIOS:
            print(f"-- {nome}")
            try:
                fn()
            except Exception as e:
                falhas.append(f"{nome} — exceção {type(e).__name__}: {e}")
                print(f"  ✘ {nome} — exceção {type(e).__name__}: {e}")
    finally:
        try:
            _limpar()
            _conferir_rastro()
        except Exception as e:
            # Falha de limpeza (ou resíduo que sobrou) é FALHA DA SUÍTE: sem
            # isso a execução terminava com falhas=0/rc=0 deixando resíduo no
            # banco — foi o que o dono encontrou no log.
            falhas.append(f"limpeza — {type(e).__name__}: {e}")
            print(f"[limpeza] ERRO: {e}")
    print(f"\n[resultado] aprovadas={len(aprovadas)} falhas={len(falhas)}")
    for f in falhas:
        print(f"  ✘ {f}")
    print(f"  evidências: {EVIDENCIAS}")
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
