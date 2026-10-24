#!/usr/bin/env python3
"""Suíte TASK_008 — caixa VISTO NO NAVEGADOR (desktop e smartphone).

As regras de serviço (eventos, vínculos, idempotência, concorrência) já são
provadas pela suíte de rotas test_sessao_eventos_caixa.py. Aqui se valida o
comportamento percebido pelo operador, numa aplicação real com Chromium:

- caixa sem sessão aberta: a tela BLOQUEIA o PDV e diz o que fazer;
- abertura de sessão e venda em espécie pela tela;
- recebimento de fiado separado com escolha de destino na barra da tela
  (e o alerta quando falta o destino — nada é gravado);
- sangria com destino escolhido (par vinculado, comprovante);
- fechamento misto com prévia, divergência de contagem SEM observação
  (recusado) e COM observação (registrada sem sobrescrever o esperado);
- consulta do caixa fechado e exportação em PDF;
- sessão legada recusada pela regra nova com explicação na tela;
- tudo de novo num viewport de smartphone (412×915).

Dados fictícios com prefixo T008N em banco descartável; o destino do script E
do servidor HTTP é provado antes de qualquer escrita. Ferramenta ausente é
falha explícita (código 3), nunca "passou".
"""
import os
import re
import sys
import json
import contextlib
import urllib.parse
import urllib.request
import urllib.error
import http.cookiejar
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import (  # noqa: E402
    exigir_ambiente_de_teste, exigir_configuracao_consoante,
    provar_destino_do_servidor, AmbienteInseguro,
)

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9011")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIXO = "T008N"
EVIDENCIAS = os.path.join(ROOT, "docs", "tasks", "evidencias", "TASK_008")

DESKTOP = (1366, 768)
SMARTPHONE = (412, 915)


def abortar(msg):
    print(f"\n[ABORTADO] {msg}", file=sys.stderr)
    sys.exit(2)


# ── 1. Prova dos dois destinos (script + HTTP) antes de qualquer escrita ────
arq_env = os.environ.get("SHAMBALA_ENV_FILE")
if not arq_env:
    abortar("SHAMBALA_ENV_FILE ausente — recuso rodar sem arquivo de teste exclusivo.")
# TASK_010: o arquivo nomeado é a autoridade; destino SHAMBALA_TEST_DB
# divergente aborta antes de qualquer escrita (REV-01).
try:
    exigir_configuracao_consoante(arq_env, os.environ.get("SHAMBALA_TEST_DB"))
except AmbienteInseguro as e:
    abortar(str(e))

from banco import conectar, criar_tabelas, conta_id_por_tipo  # noqa: E402

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


def dec(v):
    return D(str(v))


# ── 2. HTTP de apoio (semeadura e downloads) ────────────────────────────────
class SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def novo_opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(SemRedirect, urllib.request.HTTPCookieProcessor(jar))


opener = novo_opener()


def req(method, path, data=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    try:
        with opener.open(r) as resp:
            return resp.status, resp.read(), resp.headers.get("location") or ""
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("location") or ""


def consulta(sql, params=()):
    with conectar() as conn:
        return conn.execute(sql, params).fetchall()


def escalar(sql, params=(), padrao=0):
    rows = consulta(sql, params)
    if not rows:
        return padrao
    v = list(rows[0].values())[0]
    return padrao if v is None else v


def executar(sql, params=()):
    with conectar() as conn:
        conn.execute(sql, params)


# ── 3. Resultado ─────────────────────────────────────────────────────────────
CENARIOS = []
aprovadas = []
falhas = []


def cenario(nome):
    def wrap(fn):
        CENARIOS.append((nome, fn))
        return fn
    return wrap


def verificar(nome, condição, detalhe=""):
    if condição:
        aprovadas.append(nome)
        print(f"  ✔ {nome}")
    else:
        falhas.append(f"{nome} — {detalhe}")
        print(f"  ✘ {nome} — {detalhe}")


# ── 4. Ferramentas: ausência é falha explícita ──────────────────────────────
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
        if not caminho:
            import subprocess as sp
            procura = sp.run(["python3", "-c",
                              "from playwright.sync_api import sync_playwright;"
                              "print(sync_playwright().start().chromium.executable_path)"],
                             capture_output=True, text=True)
            caminho = (procura.stdout or "").strip().splitlines()[-1] if procura.returncode == 0 else ""
    if not caminho or not os.path.exists(caminho):
        faltam.append("Chromium (a tela precisa rodar num navegador real)")
    else:
        os.environ["SHAMBALA_CHROMIUM"] = caminho
    return faltam


# ── 5. Operador no navegador ────────────────────────────────────────────────
def _cookie_sessao():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), SemRedirect())
    body = urllib.parse.urlencode({"nome_usuario": "admin", "senha": "teste123"}).encode()
    try:
        op.open(urllib.request.Request(BASE + "/login", data=body, method="POST"))
        raise AssertionError("o login deveria redirecionar (303)")
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
        self.alertas = []
        self.pagina.on("dialog", lambda d: (self.alertas.append(d.message), d.accept()))

    def abrir(self, caminho):
        self.pagina.goto(BASE + caminho)
        return self.pagina

    def fechar_tela(self):
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
            op.fechar_tela()


def _esperar_texto(pagina, trecho, segundos=8):
    try:
        pagina.get_by_text(trecho).first.wait_for(timeout=segundos * 1000)
    except Exception:
        raise AssertionError(f"a tela não mostrou {trecho!r}")


def _evidencia(pagina, nome):
    pagina.screenshot(path=os.path.join(EVIDENCIAS, nome + ".png"))


# ── 6. Fixtures fictícias ───────────────────────────────────────────────────
FIX = {}


def _semeadura():
    criar_tabelas()
    from rotas.auth import hash_senha
    with conectar() as conn:
        row = conn.execute("SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()
        if row:
            conn.execute("UPDATE atendentes SET senha_hash=%s, ativo=1 WHERE id=%s",
                         (hash_senha("teste123"), row["id"]))
            aid = row["id"]
        else:
            aid = conn.execute(
                """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
                   VALUES ('admin','Administrador',%s,1) RETURNING id""",
                (hash_senha("teste123"),)).fetchone()["id"]
        conn.execute("INSERT INTO grupos (id, nome) VALUES (1,'Admin') ON CONFLICT (id) DO NOTHING")
        conn.execute("INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s,1) "
                     "ON CONFLICT DO NOTHING", (aid,))

        def caixa(nome):
            return conn.execute(
                "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
                "VALUES (%s,'fixture TASK_008 navegador',TRUE,1) RETURNING id",
                (f"{PREFIXO} {nome}",)).fetchone()["id"]

        def produto(cx, nome, preco):
            return conn.execute(
                """INSERT INTO produtos (nome, categoria, preco_venda, quantidade_estoque,
                                         caixa_id, ativo, atalho)
                   VALUES (%s,'outro',%s,100,%s,1,1) RETURNING id""",
                (f"{PREFIXO} {nome}", D(preco), cx)).fetchone()["id"]

        FIX["cx_a"] = caixa("Navegador Alpha")
        FIX["cx_b"] = caixa("Navegador Beta")
        FIX["cx_leg"] = caixa("Navegador Legado")
        FIX["cx_zero"] = caixa("Navegador Zero")
        FIX["cx_zero2"] = caixa("Navegador Zero 2")
        FIX["cx_pix"] = caixa("Navegador PIX")
        FIX["p_refri"] = produto(FIX["cx_pix"], "Refrigerante", "10.00")
        FIX["p_cafe"] = produto(FIX["cx_a"], "Café", "10.00")
        FIX["p_suco"] = produto(FIX["cx_a"], "Suco", "12.00")
        FIX["p_agua"] = produto(FIX["cx_b"], "Água", "10.00")
        FIX["t_devedor"] = conn.execute(
            "INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, fiado_credito) "
            "VALUES (%s,1,500,0) RETURNING id", (f"{PREFIXO} Devedor",)).fetchone()["id"]
        FIX["admin_id"] = aid
        FIX["cid_especie"] = conta_id_por_tipo(conn, "especie")
        FIX["cid_destino"] = conn.execute(
            "SELECT id FROM contas_financeiras WHERE ativo=1 AND tipo <> 'especie' "
            "ORDER BY id LIMIT 1").fetchone()["id"]


def _cesta(pares):
    return json.dumps([{"produto_id": pid, "quantidade": q, "nome": "", "preco_unitario": 0}
                       for pid, q in pares])


def _sessao_aberta(caixa_id):
    return consulta("SELECT id FROM caixa_movimentos WHERE caixa_id=%s AND status='aberto' "
                    "ORDER BY id DESC LIMIT 1", (caixa_id,))[0]["id"]


# ═════════════════════════════════════════════════════════════════════════════
@cenario("C1 sem sessão aberta: PDV só em PIX; espécie permanece condicionada")
def c1():
    with _operador() as op:
        pagina = op.abrir(f"/caixa/pdv?caixa_id={FIX['cx_a']}")
        # TASK_010/§3: PIX com conta financeira válida dispensa sessão; a tela
        # oferece o PDV em modo PIX-only em vez da antiga tela de bloqueio.
        _esperar_texto(pagina, "apenas por PIX")
        verificar("C1 a tela orienta que só PIX está disponível sem sessão", True)
        verificar("C1 PIX pré-selecionado; espécie e fiado bloqueados sem sessão",
                  pagina.locator("#pgto-pix").is_checked()
                  and pagina.locator("#pgto-especie").is_disabled()
                  and pagina.locator("#pgto-fiado").is_disabled())
        resp = op.context.request.post(
            f"{BASE}/caixa/pdv/finalizar",
            form={"caixa_id": str(FIX["cx_a"]), "caixa_movimento_id": "0",
                  "forma_pagamento": "especie", "valor_recebido": "20.00",
                  "itens_json": _cesta([(FIX["p_cafe"], 1)]), "imprimir": "0"})
        verificar("C1 espécie sem sessão é recusada na rota (sessao_necessaria)",
                  "sessao_necessaria" in resp.url, resp.url)
        verificar("C1 nada foi gravado",
                  escalar("SELECT COUNT(*) FROM vendas_pdv WHERE caixa_id=%s", (FIX["cx_a"],)) == 0)
        _evidencia(pagina, "c1-pdv-sem-sessao-pix-only")


@cenario("C2 abrir sessão pela tela e vender em espécie")
def c2():
    with _operador() as op:
        pagina = op.abrir(f"/caixa/abrir?caixa_id={FIX['cx_a']}")
        pagina.fill("#saldo_inicial", "100.00")
        pagina.click('button[type="submit"]')
        pagina.wait_for_url("**/caixa/pdv**")
        sa = _sessao_aberta(FIX["cx_a"])
        FIX["sa"] = sa
        verificar("C2 sessão nova nasce na versão 'eventos'",
                  escalar("SELECT versao FROM caixa_movimentos WHERE id=%s", (sa,)) == "eventos")

        pagina = op.abrir(f"/caixa/pdv?caixa_id={FIX['cx_a']}")
        pagina.locator(".pdv-atalho-item", has_text=f"{PREFIXO} Café").first.click()
        pagina.fill("#valor-recebido", "10.00")
        pagina.click("#btn-finalizar")
        pagina.wait_for_url("**/caixa/pdv**")
        n_vendas = escalar("SELECT COUNT(*) FROM vendas_pdv WHERE caixa_movimento_id=%s "
                           "AND status='concluida'", (sa,))
        verificar("C2 venda em espécie registrada pela tela", n_vendas == 1, f"{n_vendas}")
        f_ef = escalar("SELECT COALESCE(SUM(efeito_fisico),0) FROM caixa_sessao_eventos "
                       "WHERE sessao_id=%s AND tipo='venda'", (sa,))
        verificar("C2 evento da venda põe 10,00 na gaveta", dec(f_ef) == D("10.00"), f"{f_ef}")
        _evidencia(pagina, "c2-pdv-pos-venda")


@cenario("C3 recebimento de fiado: sem destino a tela alerta; com destino, lança uma vez")
def c3():
    # dívida de 12 em fiado (não precisa de sessão — só a espécie precisa)
    st, _, loc = req("POST", "/caixa/pdv/finalizar", {
        "caixa_id": str(FIX["cx_a"]), "forma_pagamento": "fiado",
        "trabalhador_id": str(FIX["t_devedor"]),
        "itens_json": _cesta(((FIX["p_suco"], 1),)),
        "imprimir": "0"})
    if st not in (200, 303) or "erro" in loc:
        abortar(f"semeadura de dívida fiado falhou (HTTP {st} {loc})")

    with _operador() as op:
        tid = FIX["t_devedor"]
        pagina = op.abrir("/caixa/fiados")
        pagina.select_option("#dest-sessao", "")
        pagina.click(f'button[onclick*="togglePagar({tid},"]')
        pagina.click(f"#pagar-form-{tid} button[type='submit']")
        verificar("C3 espécie sem destino: a tela alerta e não deixa gravar",
                  any("sessão aberta" in a for a in op.alertas), f"{op.alertas}")
        verificar("C3 alerta não gravou nada",
                  escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE "
                          "categoria='recebimento_fiado' AND descricao LIKE %s",
                          (f"%{PREFIXO}%",)) == 0)

        pagina.select_option("#dest-sessao", value=str(FIX["sa"]))
        pagina.click(f"#pagar-form-{tid} button[type='submit']")
        pagina.wait_for_url("**pago_ok**")
        verificar("C3 pagamento aceito com o destino escolhido na barra",
                  "pago_ok" in (pagina.url or ""), pagina.url or "")
        c_fin = escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE "
                        "categoria='recebimento_fiado' AND descricao LIKE %s",
                        (f"%{PREFIXO}% Devedor%",))
        v_fin = escalar("SELECT COALESCE(SUM(valor),0) FROM financeiro_movimentacoes WHERE "
                        "categoria='recebimento_fiado' AND descricao LIKE %s",
                        (f"%{PREFIXO}% Devedor%",))
        verificar("C3 UMA entrada de 12,00 na gaveta",
                  c_fin == 1 and dec(v_fin) == D("12.00"), f"n={c_fin} v={v_fin}")
        f_ev = escalar("SELECT COALESCE(SUM(efeito_fisico),0) FROM caixa_sessao_eventos "
                       "WHERE sessao_id=%s AND tipo='recebimento_fiado'", (FIX["sa"],))
        verificar("C3 evento de recebimento com efeito físico 12",
                  dec(f_ev) == D("12.00"), f"{f_ev}")
        _evidencia(pagina, "c3-fiado-recebido")


@cenario("C4 sangria com destino escolhido: par vinculado e comprovante")
def c4():
    with _operador() as op:
        pagina = op.abrir(f"/caixa/sangria?caixa_movimento_id={FIX['sa']}")
        pagina.select_option("#conta_destino_id", index=1)
        pagina.fill("#valor", "20.00")
        pagina.fill("#recebedor_nome", "Homologacao TASK-008 (teste)")
        pagina.fill("#motivo", "Troco para a recepção")
        pagina.click('button[type="submit"]')
        pagina.wait_for_url("**/caixa/sangria/**/comprovante**")
        verificar("C4 a tela encaminha ao comprovante da sangria", True)
        _esperar_texto(pagina, "Homologacao TASK-008")
        row_s = consulta("SELECT COUNT(*) AS c, COALESCE(SUM(valor),0) AS v "
                         "FROM caixa_sangrias WHERE caixa_movimento_id=%s",
                         (FIX["sa"],))[0]
        ns, nv = int(row_s["c"]), row_s["v"]
        verificar("C4 sangria gravada com recebedor e motivo",
                  int(ns) == 1 and dec(nv) == D("20.00"), f"n={ns} v={nv}")
        sid_sangria = escalar("SELECT id FROM caixa_sangrias WHERE caixa_movimento_id=%s",
                              (FIX["sa"],))
        fin_saida = escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE "
                            "tipo='saida' AND categoria='sangria' AND sangria_id=%s",
                            (sid_sangria,))
        fin_ent = escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE "
                          "tipo='entrada' AND categoria='transferencia' AND sangria_id=%s",
                          (sid_sangria,))
        conta_saida = escalar("SELECT conta_id FROM financeiro_movimentacoes WHERE "
                              "categoria='sangria' AND sangria_id=%s", (sid_sangria,))
        conta_ent = escalar("SELECT conta_id FROM financeiro_movimentacoes WHERE "
                            "categoria='transferencia' AND sangria_id=%s", (sid_sangria,))
        verificar("C4 par saída+entrada com destino identificado (efeito global zero)",
                  fin_saida == 1 and fin_ent == 1 and conta_saida == FIX["cid_especie"]
                  and conta_ent == FIX["cid_destino"],
                  f"sai={fin_saida} ent={fin_ent} contas={conta_saida}/{conta_ent}")
        f_ev = escalar("SELECT COALESCE(SUM(efeito_fisico),0) FROM caixa_sessao_eventos "
                       "WHERE sessao_id=%s AND tipo='sangria'", (FIX["sa"],))
        verificar("C4 evento de sangria tira 20 da gaveta", dec(f_ev) == D("-20.00"), f"{f_ev}")
        _evidencia(pagina, "c4-sangria-comprovante")


@cenario("C5 fechamento misto: prévia, divergência sem observação é recusada")
def c5():
    with _operador() as op:
        pagina = op.abrir(f"/caixa/fechar?caixa_movimento_id={FIX['sa']}")
        _esperar_texto(pagina, "Dinheiro físico esperado")
        verificar("C5 a prévia mostra o esperado 102.00 (100 + 10 + 12 − 20)",
                  "R$ 102.00" in pagina.inner_text("body"),
                  pagina.inner_text("body")[:400])
        pagina.fill("#dinheiro_contado", "100.00")
        pagina.click('button[type="submit"]')
        pagina.wait_for_url("**/caixa/fechar**")
        _esperar_texto(pagina, "descreva a diferença na observação")
        verificar("C5 contagem divergente SEM observação é recusada na tela", True)
        verificar("C5 sessão continua aberta",
                  escalar("SELECT status FROM caixa_movimentos WHERE id=%s", (FIX["sa"],))
                  == "aberto")
        _evidencia(pagina, "c5-divergencia-recusada")


@cenario("C6 fechar com divergência e observação: registrado sem sobrescrever o esperado")
def c6():
    with _operador() as op:
        pagina = op.abrir(f"/caixa/fechar?caixa_movimento_id={FIX['sa']}")
        pagina.fill("#dinheiro_contado", "100.00")
        pagina.fill("#observacao", "faltaram 2,00 — conferência da homologação")
        pagina.click('button[type="submit"]')
        pagina.wait_for_url("**/caixa/fechar**")
        _esperar_texto(pagina, "Sessão fechada")
        m = consulta("SELECT * FROM caixa_movimentos WHERE id=%s", (FIX["sa"],))[0]
        verificar("C6 esperado NUNCA é sobrescrito (saldo_final = 102,00 dos eventos)",
                  dec(m["saldo_final"]) == D("102.00"), f"{m['saldo_final']}")
        verificar("C6 contado 100,00 com divergência −2,00 e observação registradas",
                  dec(m["dinheiro_contado"]) == D("100.00")
                  and dec(m["divergencia_contagem"]) == D("-2.00")
                  and "faltaram 2,00" in (m["observacao_conferencia"] or ""),
                  f"{m['dinheiro_contado']} / {m['divergencia_contagem']}")
        _esperar_texto(pagina, "faltaram 2,00")
        _evidencia(pagina, "c6-fechamento-divergente")
        resp = op.context.request.get(f"{BASE}/caixa/fechar/{FIX['sa']}/pdf")
        verificar("C6 PDF do caixa fechado exportado pelo navegador",
                  resp.status == 200 and resp.body()[:4] == b"%PDF", f"HTTP {resp.status}")


@cenario("C7 sessão legada é recusada pela regra nova, com explicação na tela")
def c7():
    with conectar() as conn:
        sl = conn.execute(
            """INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status,
                                             atendente_id_abertura, versao)
               VALUES (%s, 10, 'aberto', 1, 'legado') RETURNING id""",
            (FIX["cx_leg"],)).fetchone()["id"]
    with _operador() as op:
        pagina = op.abrir(f"/caixa/fechar?caixa_movimento_id={sl}")
        verificar("C7 a tela legada NÃO expõe a contagem da regra nova",
                  pagina.locator("#dinheiro_contado").count() == 0)
        resp = op.context.request.post(
            f"{BASE}/caixa/fechar",
            form={"caixa_movimento_id": str(sl), "dinheiro_contado": "10.00"})
        verificar("C7 POST com contagem nova é recusado (sessao_legada)",
                  "sessao_legada" in resp.url, resp.url)
        pagina.goto(resp.url)
        _esperar_texto(pagina, "fluxo antigo")
        verificar("C7 tela explica que a sessão legada não fecha pela regra nova", True)
        verificar("C7 sessão legada continua aberta (nada foi gravado)",
                  escalar("SELECT status FROM caixa_movimentos WHERE id=%s", (sl,)) == "aberto")
        pagina.goto(BASE + "/caixa/fiados")
        _esperar_texto(pagina, "Fiados")
        valores = pagina.locator("#dest-sessao option").evaluate_all(
            "els => els.map(e => e.value)")
        verificar("C7 sessão legada NÃO aparece na barra de destino de recebimento",
                  str(sl) not in valores, f"{valores}")
        _evidencia(pagina, "c7-legado-bloqueado")


@cenario("C8 smartphone (412×915): abrir, vender e fechar usando só a tela")
def c8():
    with _operador(largura=SMARTPHONE[0], altura=SMARTPHONE[1]) as op:
        pagina = op.abrir(f"/caixa/abrir?caixa_id={FIX['cx_b']}")
        pagina.fill("#saldo_inicial", "50.00")
        pagina.click('button[type="submit"]')
        sb = _sessao_aberta(FIX["cx_b"])

        pagina = op.abrir(f"/caixa/pdv?caixa_id={FIX['cx_b']}")
        pagina.locator(".pdv-atalho-item", has_text=f"{PREFIXO} Água").first.click()
        pagina.fill("#valor-recebido", "10.00")
        pagina.click("#btn-finalizar")
        pagina.wait_for_url("**/caixa/pdv**")
        verificar("C8 venda registrada no smartphone",
                  escalar("SELECT COUNT(*) FROM vendas_pdv WHERE caixa_movimento_id=%s "
                          "AND status='concluida'", (sb,)) == 1)
        _evidencia(pagina, "c8-smartphone-pdv")

        pagina = op.abrir(f"/caixa/fechar?caixa_movimento_id={sb}")
        _esperar_texto(pagina, "R$ 60.00")
        caixa_campo = pagina.locator("#dinheiro_contado").bounding_box()
        verificar("C8 campos do fechamento cabem na tela do smartphone",
                  caixa_campo is not None and caixa_campo["width"] <= SMARTPHONE[0],
                  f"{caixa_campo}")
        pagina.fill("#dinheiro_contado", "60.00")
        pagina.click('button[type="submit"]')
        pagina.wait_for_url("**/caixa/fechar**")
        _esperar_texto(pagina, "Sessão fechada")
        _evidencia(pagina, "c8-smartphone-fechado")


@cenario("C9 desktop: abrir com R$ 0,00 e fechar sessão vazia com contado 0 (REV-02)")
def c9():
    with _operador() as op:
        pagina = op.abrir(f"/caixa/abrir?caixa_id={FIX['cx_zero']}")
        pagina.fill("#saldo_inicial", "0.00")
        pagina.click('button[type="submit"]')
        pagina.wait_for_url("**/caixa**")
        sz = _sessao_aberta(FIX["cx_zero"])
        m = consulta("SELECT * FROM caixa_movimentos WHERE id=%s", (sz,))[0]
        verificar("C9 abertura padrão R$ 0,00 aceita pela tela",
                  m["versao"] == "eventos" and dec(m["saldo_inicial"]) == D("0.00"),
                  f"{m['versao']} / {m['saldo_inicial']}")
        _evidencia(pagina, "c9-desktop-aberto-zero")

        pagina = op.abrir(f"/caixa/fechar?caixa_movimento_id={sz}")
        _esperar_texto(pagina, "R$ 0.00")
        pagina.fill("#dinheiro_contado", "0.00")
        pagina.click('button[type="submit"]')
        pagina.wait_for_url("**/caixa/fechar**")
        _esperar_texto(pagina, "Sessão fechada")
        m = consulta("SELECT * FROM caixa_movimentos WHERE id=%s", (sz,))[0]
        verificar("C9 sessão vazia fechada com contado 0 e divergência 0",
                  m["status"] == "fechado" and dec(m["dinheiro_contado"]) == D("0.00")
                  and dec(m["divergencia_contagem"]) == D("0.00")
                  and dec(m["saldo_final"]) == D("0.00"),
                  f"{m['status']} / {m['dinheiro_contado']} / {m['divergencia_contagem']}")
        verificar("C9 fechamento sem eventos não inventa lançamentos financeiros",
                  escalar("SELECT COUNT(*) FROM caixa_sessao_eventos "
                          "WHERE sessao_id=%s", (sz,)) == 0
                  and escalar("SELECT COUNT(*) FROM financeiro_movimentacoes "
                              "WHERE descricao LIKE %s", (f"%fechamento #{sz}%",)) == 0)
        _evidencia(pagina, "c9-desktop-fechado-zero")


@cenario("C10 smartphone (412×915): abertura e fechamento com zero pela tela")
def c10():
    with _operador(largura=SMARTPHONE[0], altura=SMARTPHONE[1]) as op:
        pagina = op.abrir(f"/caixa/abrir?caixa_id={FIX['cx_zero2']}")
        pagina.fill("#saldo_inicial", "0.00")
        pagina.click('button[type="submit"]')
        pagina.wait_for_url("**/caixa**")
        sz = _sessao_aberta(FIX["cx_zero2"])
        verificar("C10 saldo inicial zero gravado no smartphone",
                  dec(consulta("SELECT saldo_inicial FROM caixa_movimentos WHERE id=%s",
                               (sz,))[0]["saldo_inicial"]) == D("0.00"))
        pagina = op.abrir(f"/caixa/fechar?caixa_movimento_id={sz}")
        caixa_campo = pagina.locator("#dinheiro_contado").bounding_box()
        verificar("C10 campo de contagem cabe na tela do smartphone",
                  caixa_campo is not None and caixa_campo["width"] <= SMARTPHONE[0],
                  f"{caixa_campo}")
        pagina.fill("#dinheiro_contado", "0")
        pagina.click('button[type="submit"]')
        pagina.wait_for_url("**/caixa/fechar**")
        _esperar_texto(pagina, "Sessão fechada")
        verificar("C10 fechado com contado 0 (texto '0' aceito)",
                  escalar("SELECT status FROM caixa_movimentos WHERE id=%s", (sz,)) == "fechado")
        _evidencia(pagina, "c10-smartphone-fechado-zero")


@cenario("C11 TASK_010/§3: PIX funciona sem sessão aberta; espécie segue condicionada")
def c11():
    with _operador() as op:
        pagina = op.abrir(f"/caixa/pdv?caixa_id={FIX['cx_pix']}")
        _esperar_texto(pagina, "Carrinho")
        verificar("C11 o PDV responde sem sessão aberta (não é mais a tela de bloqueio)",
                  pagina.locator("#btn-finalizar").count() == 1)
        verificar("C11 sem sessão: PIX pré-selecionado, espécie e fiado bloqueados",
                  pagina.locator("#pgto-pix").is_checked()
                  and pagina.locator("#pgto-especie").is_disabled()
                  and pagina.locator("#pgto-fiado").is_disabled())
        _esperar_texto(pagina, "apenas por PIX")

        pagina.locator(".pdv-atalho-item", has_text=f"{PREFIXO} Refrigerante").first.click()
        pagina.click("#btn-finalizar")
        pagina.wait_for_selector("#btn-finalizar-pix", state="visible", timeout=10000)
        pagina.click("#btn-finalizar-pix")
        pagina.wait_for_url("**/caixa/pdv**")
        v = consulta("SELECT id, caixa_movimento_id, forma_pagamento, status "
                     "FROM vendas_pdv WHERE caixa_id=%s ORDER BY id DESC LIMIT 1",
                     (FIX["cx_pix"],))
        verificar("C11 venda PIX sem sessão concluída, gaveta desvinculada (NULL)",
                  v and v[0]["forma_pagamento"] == "pix" and v[0]["status"] == "concluida"
                  and v[0]["caixa_movimento_id"] is None, f"{v}")
        verificar("C11 vender por PIX não abriu sessão de caixa",
                  escalar("SELECT COUNT(*) FROM caixa_movimentos WHERE caixa_id=%s",
                          (FIX["cx_pix"],)) == 0)
        _evidencia(pagina, "c11-pix-sem-sessao")

        # espécie sem sessão: a tela bloqueia e o POST direto é recusado
        resp = op.context.request.post(
            f"{BASE}/caixa/pdv/finalizar",
            form={"caixa_id": str(FIX["cx_pix"]), "caixa_movimento_id": "0",
                  "forma_pagamento": "especie", "valor_recebido": "10.00",
                  "itens_json": _cesta([(FIX["p_refri"], 1)]), "imprimir": "0"})
        verificar("C11 espécie sem sessão é recusada (sessao_necessaria)",
                  "sessao_necessaria" in resp.url, resp.url)
        verificar("C11 a recusa de espécie não criou nada",
                  escalar("SELECT COUNT(*) FROM vendas_pdv WHERE caixa_id=%s",
                          (FIX["cx_pix"],)) == 1)


# ═════════════════════════════════════════════════════════════════════════════
def main():
    faltam = _ferramentas_ausentes()
    if faltam:
        print("\n[ABORTADO] ferramentas ausentes para a validação no navegador:")
        for item in faltam:
            print(f"    - {item}")
        print("  nada foi executado — ausência de ferramenta não é sucesso.")
        return 3

    os.makedirs(EVIDENCIAS, exist_ok=True)
    _semeadura()
    st, _, _ = req("POST", "/login", {"nome_usuario": "admin", "senha": "teste123"})
    if st not in (200, 303):
        abortar(f"login do opener de apoio falhou (HTTP {st})")
    print("[seed] fixtures T008N prontas")

    for nome, fn in CENARIOS:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            falhas.append(f"{nome} — {type(e).__name__}: {e}")
            print(f"  ✘ {nome} levantou {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n[resultado] aprovadas={len(aprovadas)} falhas={len(falhas)}")
    for f in falhas:
        print(f"  ✘ {f}")
    print(f"  evidências: {EVIDENCIAS}")
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
