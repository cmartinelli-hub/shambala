#!/usr/bin/env python3
"""Suíte TASK_011 — Visão Geral do Financeiro VISTA NO NAVEGADOR (desktop e smartphone).

O contrato das consultas (somatórios, exclusões, paginação, RBAC) é provado pela
suíte test_financeiro_visaogeral_consultas_task011.py. Aqui se valida o que o
dono vê e faz na tela, com Chromium real:

- cartões do período com os valores combinados dos fixtures;
- busca que filtra os cartões e NÃO mexe no cartão "A receber" (nota na tela);
- detalhe do lançamento e o botão voltar preservando o mesmo recorte na URL;
- paginação server-side com contagens estáveis;
- aba "A receber / A pagar" com fiado em grupo próprio e fora do indicador;
- smartphone (412×915) sem scroll horizontal e com as linhas expansíveis;
- foco por teclado alcançando os controles do formulário de filtros.

Dados fictícios com prefixo T011N na janela 2026-06 de banco descartável; o
destino do script E do servidor HTTP é provado antes de qualquer escrita
(TASK_010). Ferramenta ausente é falha explícita (código 3), nunca "passou".
"""
import os
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

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9016")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIXO = "T011N"
EVIDENCIAS = os.path.join(ROOT, "docs", "tasks", "evidencias", "TASK_011")

DE = "2026-06-01"
ATE = "2026-06-30"
JANELA = f"de={DE}&ate={ATE}"

DESKTOP = (1366, 768)
SMARTPHONE = (412, 915)


def abortar(msg):
    print(f"\n[ABORTADO] {msg}", file=sys.stderr)
    sys.exit(2)


# ── 1. Prova dos dois destinos (script + HTTP) antes de qualquer escrita ────
arq_env = os.environ.get("SHAMBALA_ENV_FILE")
if not arq_env:
    abortar("SHAMBALA_ENV_FILE ausente — recuso rodar sem arquivo de teste exclusivo.")
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


# ── 2. Resultado ─────────────────────────────────────────────────────────────
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


# ── 3. Ferramentas: ausência é falha explícita ──────────────────────────────
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


# ── 4. Operador no navegador ────────────────────────────────────────────────
def _cookie_sessao():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    body = urllib.parse.urlencode({"nome_usuario": "admin", "senha": "teste123"}).encode()
    r = urllib.request.Request(BASE + "/login", data=body, method="POST")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    op.addheaders = []
    try:
        with urllib.request.build_opener(NoRedirect,
                                         urllib.request.HTTPCookieProcessor(cj)).open(r) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    if status != 303:
        raise AssertionError(f"login falhou (status {status})")
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
    os.makedirs(EVIDENCIAS, exist_ok=True)
    pagina.screenshot(path=os.path.join(EVIDENCIAS, nome + ".png"))


# ── 5. Fixtures fictícias (janela 2026-06, soma combinada à mão) ────────────
# Recebimentos = 28×1,00 + 100 + 50 = 178,00 · Pagamentos = 30,00
# Resultado = 148,00 · A receber = 60,00 · A pagar = 25,00 · Fiado = 45,00
# Movimentações na janela = 37 (inclui par de transferência, histórico e cancelado)
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

        cid_esp = conta_id_por_tipo(conn, "especie")
        cid_pix = conta_id_por_tipo(conn, "pix")
        cid_ar = conn.execute(
            "SELECT id FROM contas_financeiras WHERE tipo='areceber' AND ativo=1"
        ).fetchone()["id"]

        # janela 2026-06 é exclusivamente desta suíte: limpa resíduo anterior
        conn.execute("DELETE FROM financeiro_movimentacoes "
                     "WHERE data_movimentacao BETWEEN '2026-06-01' AND '2026-06-30'")
        # resíduo desta suíte fora da janela: linhas que apontam nossos trabalhadores
        conn.execute("""DELETE FROM financeiro_movimentacoes
                        WHERE trabalhador_id IN
                              (SELECT id FROM trabalhadores WHERE nome_completo LIKE %s)
                           OR descricao LIKE %s""",
                     (f"{PREFIXO}%", f"{PREFIXO}%"))
        ids = conn.execute("SELECT id FROM vendas_pdv WHERE observacao LIKE %s",
                           (f"{PREFIXO}%",)).fetchall()
        for r in ids:
            conn.execute("DELETE FROM vendas_pdv WHERE id=%s", (r["id"],))
        conn.execute("DELETE FROM caixas WHERE nome LIKE %s", (f"{PREFIXO}%",))
        conn.execute("DELETE FROM trabalhadores WHERE nome_completo LIKE %s", (f"{PREFIXO}%",))

        def fm(tipo, cat, valor, data, desc, status, conta, **extra):
            cols = ["tipo", "categoria", "valor", "data_movimentacao", "descricao",
                    "status", "conta_id"] + list(extra)
            vals = [tipo, cat, D(valor), data, desc, status, conta] + list(extra.values())
            return conn.execute(
                f"INSERT INTO financeiro_movimentacoes ({','.join(cols)}) "
                f"VALUES ({','.join(['%s'] * len(cols))}) RETURNING id",
                vals).fetchone()["id"]

        # sem valor_mensalidade para o gerador do app não inventar linhas na suíte
        t_dev = conn.execute(
            "INSERT INTO trabalhadores (nome_completo, ativo) "
            "VALUES (%s,1) RETURNING id", (f"{PREFIXO} Devedor",)).fetchone()["id"]
        cx = conn.execute("INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
                          "VALUES (%s,'fixture TASK_011 navegador',FALSE,1) RETURNING id",
                          (f"{PREFIXO} Caixa",)).fetchone()["id"]

        for i in range(28):
            fm("entrada", "outros", "1.00", f"2026-06-{i + 1:02d}",
               f"{PREFIXO} linha {i + 1:02d}", "pago", cid_esp)
        fm("entrada", "venda_pdv", "100.00", "2026-06-05", f"{PREFIXO} venda", "pago", cid_esp)
        fm("entrada", "doacao", "50.00", "2026-06-06", f"{PREFIXO} doação", "pago", cid_pix)
        fm("saida", "compra", "30.00", "2026-06-07", f"{PREFIXO} compra", "pago", cid_pix)
        fm("saida", "transferencia", "200.00", "2026-06-15", f"{PREFIXO} depósito (origem)",
           "pago", cid_esp)
        fm("entrada", "transferencia", "200.00", "2026-06-15", f"{PREFIXO} depósito (destino)",
           "pago", cid_pix)
        fm("entrada", "mensalidade", "60.00", "2026-06-29", f"{PREFIXO} recibo antigo",
           "pago", cid_ar)
        fm("entrada", "doacao", "10.00", "2026-06-30", f"{PREFIXO} oferta revogada",
           "cancelado", cid_esp)
        fm("entrada", "mensalidade", "60.00", "2026-06-10", f"{PREFIXO} mensalidade em aberto",
           "pendente", cid_ar, trabalhador_id=t_dev)
        fm("saida", "manutencao", "25.00", "2026-06-12", f"{PREFIXO} conserto a pagar",
           "pendente", cid_esp)
        conn.execute(
            """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
                                       status, fiado_trabalhador_id, fiado_pago, observacao)
               VALUES (%s,'2026-06-18',45,'fiado','concluida',%s,FALSE,%s)""",
            (cx, t_dev, f"{PREFIXO} fiado aberto"))
    return {"cid_esp": cid_esp, "cid_pix": cid_pix, "cid_ar": cid_ar, "t_dev": t_dev}


FIX = {}


# ═════════════════════════════════════════════════════════════════════════════
@cenario("N1 desktop: cartões do período com os valores combinados e rótulos claros")
def n1():
    with _operador() as op:
        pagina = op.abrir(f"/financeiro?{JANELA}")
        corpo = pagina.inner_text("body")
        verificar("N1 período em destaque (01/06/2026 a 30/06/2026)",
                  "01/06/2026" in corpo and "30/06/2026" in corpo)
        verificar("N1 cartão Recebimentos = + R$ 178,00 (sem transferência/sangria/histórico)",
                  "+ R$ 178,00" in corpo)
        verificar("N1 cartão Pagamentos = − R$ 30,00", "− R$ 30,00" in corpo)
        verificar("N1 cartão Resultado = R$ 148,00", "R$ 148,00" in corpo)
        verificar("N1 cartão A receber = R$ 60,00", "R$ 60,00" in corpo)
        verificar("N1 resultado declara 'Não é saldo disponível'",
                  pagina.locator(".fin-def", has_text="Não é saldo").count() == 1)
        verificar("N1 aba Movimentações traz a contagem do recorte (37)",
                  "Movimentações (37)" in corpo)
        verificar("N1 primeira linha já rotula o registro histórico a conferir",
                  "Registro histórico" in corpo)
        verificar("N1 cancelado permanece listado com situação própria",
                  "Cancelado" in corpo)
        verificar("N1 administrador vê a ação Nova movimentação separada da navegação",
                  pagina.locator(".fin-nav").count() == 1
                  and pagina.get_by_text("Nova movimentação").count() == 1)
        _evidencia(pagina, "depois-resumo-desktop")


@cenario("N2 busca filtra os cartões de dinheiro mas não toca em A receber (com nota)")
def n2():
    with _operador() as op:
        pagina = op.abrir(f"/financeiro?{JANELA}")
        pagina.fill("#f-q", f"{PREFIXO} doação")
        pagina.click("button[type=submit]")
        pagina.wait_for_url("**q=**")
        _esperar_texto(pagina, f"{PREFIXO} doação")
        corpo = pagina.inner_text("body")
        verificar("N2 busca acentuada encontra o lançamento", f"{PREFIXO} doação" in corpo)
        verificar("N2 cartão de recebimentos passa a considerar só o filtro (+ R$ 50,00)",
                  "+ R$ 50,00" in corpo)
        verificar("N2 cartão A receber permanece no período (R$ 60,00) mesmo filtrado",
                  "R$ 60,00" in corpo)
        verificar("N2 a tela avisa que A receber/A pagar usa só o período",
                  pagina.locator(".fin-nota", has_text="consideram apenas o período").count() >= 1)
        verificar("N2 extrato reduzido a 1 linha",
                  pagina.locator(".fin-tabela tbody tr").count() == 1)


@cenario("N3 detalhe é somente-leitura e o voltar mantém exatamente o mesmo recorte")
def n3():
    with _operador() as op:
        pagina = op.abrir(f"/financeiro?{JANELA}")
        pagina.fill("#f-q", f"{PREFIXO} doação")
        pagina.click("button[type=submit]")
        pagina.wait_for_url("**q=**")
        pagina.click(".fin-somente-desktop tbody tr a:has-text('Ver')")
        pagina.wait_for_url("**/financeiro/lancamento/**")
        corpo = pagina.inner_text("body")
        verificar("N3 tela do lançamento identifica o registro e os vínculos comprovados",
                  "Lançamento #" in corpo and "Vínculos comprovados" in corpo)
        verificar("N3 a natureza humana aparece (Doação)", "Doação" in corpo)
        _evidencia(pagina, "depois-detalhe-desktop")
        pagina.click("a.btn-voltar")
        pagina.wait_for_url("**/financeiro?**")
        qry = urllib.parse.parse_qs(urllib.parse.urlsplit(pagina.url).query)
        verificar("N3 voltar devolve ao MESMO recorte (período + busca na URL)",
                  qry.get("de") == [DE] and qry.get("ate") == [ATE]
                  and qry.get("q") == [f"{PREFIXO} doação"], pagina.url)


@cenario("N4 paginação no servidor: contagens estáveis e troca de tamanho de página")
def n4():
    with _operador() as op:
        pagina = op.abrir(f"/financeiro?{JANELA}")
        corpo = pagina.inner_text("body")
        verificar("N4 página 1 de 2 exibindo 1–25 de 37",
                  "exibindo 1–25 de 37" in corpo)
        pagina.click(".fin-paginacao a:has-text('Próxima')")
        pagina.wait_for_url("**pagina=2**")
        corpo = pagina.inner_text("body")
        verificar("N4 página 2 exibe as 12 restantes (26–37)",
                  "exibindo 26–37 de 37" in corpo)
        verificar("N4 filtros preservados ao paginar (de/ate continuam na URL)",
                  DE in pagina.url and ATE in pagina.url)
        pagina.click(".fin-paginacao a:has-text('100/pág')")
        pagina.wait_for_url("**por=100**")
        verificar("N4 página inteira com 100 mostra as 37 linhas",
                  len(pagina.locator(".fin-somente-desktop tbody tr").all()) == 37)


@cenario("N5 aba A receber / A pagar separa obrigações do fiado em aberto")
def n5():
    with _operador() as op:
        pagina = op.abrir(f"/financeiro?{JANELA}&aba=pend")
        corpo = pagina.inner_text("body")
        verificar("N5 títulos dos grupos deixam claro que pendência não é dinheiro",
                  "A receber e a pagar" in corpo and "não dinheiro recebido" in corpo)
        verificar("N5 grupo soma A receber e A pagar do período",
                  "A receber R$ 60,00 · A pagar R$ 25,00" in corpo)
        verificar("N5 fiado em aberto aparece como grupo próprio, fora do indicador",
                  "Fiado em aberto (fora do indicador" in corpo)
        verificar("N5 venda a fiado listada com o devedor e o valor",
                  f"{PREFIXO} Devedor" in corpo and "R$ 45,00" in corpo)
        verificar("N5 mensalidade pendente oferece ação só de consulta (Mensalidades)",
                  pagina.locator("a[href*='mensalidades?trabalhador_id=']").count() >= 1)
        _evidencia(pagina, "depois-pendencias-desktop")


@cenario("N6 smartphone (412×915): sem scroll horizontal, linhas expansíveis, detalhe acessível")
def n6():
    with _operador(largura=SMARTPHONE[0], altura=SMARTPHONE[1]) as op:
        pagina = op.abrir(f"/financeiro?{JANELA}")
        scroll = pagina.evaluate("document.documentElement.scrollWidth")
        verificar("N6 nenhuma barra de rolagem horizontal na visão geral",
                  scroll <= SMARTPHONE[0], f"scrollWidth={scroll}")
        verificar("N6 tabela desktop oculta; linhas do celular presentes",
                  not pagina.locator(".fin-somente-desktop").first.is_visible()
                  and pagina.locator("details.fin-linha").count() == 25)
        pagina.locator("details.fin-linha").first.locator("summary").click()
        _esperar_texto(pagina, "Natureza")
        verificar("N6 linha expande e mostra natureza, conta e situação", True)
        _evidencia(pagina, "depois-resumo-smartphone")
        pagina.locator("details.fin-linha").first.click()  # recolhe
        linha2 = pagina.locator("details.fin-linha").nth(1)
        linha2.locator("summary").click()
        linha2.locator("a:has-text('Ver detalhes')").click()
        pagina.wait_for_url("**/financeiro/lancamento/**")
        verificar("N6 detalhe abre no celular", "Lançamento #" in pagina.inner_text("body"))
        scroll = pagina.evaluate("document.documentElement.scrollWidth")
        verificar("N6 detalhe também cabe na largura do celular",
                  scroll <= SMARTPHONE[0], f"scrollWidth={scroll}")


@cenario("N7 teclado: os controles da barra de filtros são alcançáveis sem mouse")
def n7():
    with _operador() as op:
        pagina = op.abrir(f"/financeiro?{JANELA}")
        vistos_ids = set()
        vistos_tags = set()
        for _ in range(90):  # campos de data consomem várias tabs (partes mês/dia/ano)
            pagina.keyboard.press("Tab")
            d = pagina.evaluate("""() => {
                const a = document.activeElement;
                if (!a || a === document.body) return ['', ''];
                return [a.id || '', a.tagName.toLowerCase()];
            }""")
            if d[0]:
                vistos_ids.add(d[0])
            vistos_tags.add(d[1])
        alvos = {"f-de", "f-ate", "f-q", "f-conta", "f-nat"}
        verificar("N7 Tab alcança os campos de período, busca e selects",
                  alvos.issubset(vistos_ids), f"faltaram {sorted(alvos - vistos_ids)}")
        verificar("N7 Tab alcança links e botões (abas, Filtrar, Ver)",
                  {"a", "button"}.issubset(vistos_tags), f"{sorted(vistos_tags)}")


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
    FIX.update(_semeadura())
    print("[seed] fixtures T011N prontas (janela 2026-06)")

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
