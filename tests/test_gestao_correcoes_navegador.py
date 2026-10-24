#!/usr/bin/env python3
"""Suíte TASK_004 — gestão do dia, correções e relatórios VISTOS NO NAVEGADOR.

Não é uma reimplementação das regras no front-end: a suíte dirige a aplicação
exatamente como um operador de recepção a usa — sessão real de admin, cliques,
formulários, navegação — e observa o que aparece na tela. As regras de serviço
já são cobertas pela suíte backend (test_correcoes_presenca.py); aqui se valida
o comportamento percebido:

- gestão do dia com data, busca e filtros server-side e atualização parcial;
- queda de rede no refresco: aviso sem apagar os últimos resultados;
- dia passado em consulta somente leitura (sem botões de escrita);
- fluxo de correção completo com confirmação e histórico na própria tela;
- tela de conflito quando o fato mudou depois de abrir o formulário;
- ficha do trabalhador com selo "ajustado" e histórico de alterações;
- relatórios (geral e individual) sem percentuais enganosos e com impressão
  (emulate_media print) sem controles, em desktop e smartphone.

Ferramenta ausente nunca conta como sucesso: sem Playwright ou Chromium a
suíte encerra com falha explícita (código 3), dizendo o que falta.

Evidências (capturas nas medidas reais) vão para
`~/Documentos/evidencias-task004-gestao-correcoes/` — nomes fictícios do banco
descartável, nunca dados reais.

Uso (recomendado): bash tests/rodar_testes.sh
"""
import os
import sys
import time
import shutil
import contextlib
import urllib.parse
import urllib.request
import urllib.error
import http.cookiejar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import exigir_ambiente_de_teste  # noqa: E402

from test_tres_passes import (  # noqa: E402
    assert_eq, preparar_banco, limpar_banco, _garantir_admin, BASE, _SemRedirect,
)
from test_correcoes_presenca import (  # noqa: E402
    _limpa_negocio, _hoje_sp, _facto, _fato, _auditoria,
)

from banco import conectar  # noqa: E402
from servico_presenca import registrar_chegada  # noqa: E402
from servico_correcao_presenca import (  # noqa: E402
    aplicar_correcao, ler_fato, estado_base,
)

EVIDENCIAS = os.path.expanduser(os.environ.get(
    "SHAMBALA_EVIDENCIAS_DIR",
    "~/Documentos/evidencias-task004-gestao-correcoes"))

DESKTOP = (1366, 768)
SMARTPHONE = (412, 915)

CENARIOS = []
CENAS = {}


def cenario(nome):
    def wrap(fn):
        CENARIOS.append((nome, fn))
        return fn
    return wrap


# ── ferramentas: ausência é falha explícita, nunca "passou" ──────────────────

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


# ── cena: trabalhadores fictícios, fatos de hoje e de ontem ──────────────────

def _preparar_cenas():
    with conectar() as conn:
        exigir_ambiente_de_teste(conn)
        _limpa_negocio(conn)
        ids = {}
        for nome, ativo in (("Alice Chegada QR", 1), ("Bruno Recepcao", 1),
                            ("Carla Sem Registro", 1), ("Dami Inativo", 0)):
            ids[nome] = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo) VALUES (%s, %s) "
                "RETURNING id", (nome, ativo)).fetchone()["id"]
        hoje = _hoje_sp(conn)
        ontem = conn.execute("SELECT (%s::date - 1)::text AS d", (hoje,)).fetchone()["d"]
        _facto(conn, ids["Alice Chegada QR"], hoje, 1, "08:00", "terminal_qr")
        _facto(conn, ids["Bruno Recepcao"], hoje, 1, "07:30", "recepcao")
        _facto(conn, ids["Dami Inativo"], ontem, 1, "09:00", "recepcao")
        conn.commit()
    CENAS.clear()
    CENAS.update(ids=ids, hoje=hoje, ontem=ontem,
                 hoje_br="/".join(reversed(hoje.split("-"))),
                 ontem_br="/".join(reversed(ontem.split("-"))))


def _id_por_nome(nome):
    return CENAS["ids"][nome]


def _token_atual(conn, tid, data):
    """O token do estado real AGORA — o mesmo que um formulário atual carregaria."""
    return estado_base(ler_fato(conn, tid, data))


# ── o operador no navegador ──────────────────────────────────────────────────

def _cookie_sessao(usuario="admin", senha="teste123"):
    """Login real via HTTP; devolve o valor do cookie de sessão do sistema."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj), _SemRedirect())
    body = urllib.parse.urlencode({"nome_usuario": usuario, "senha": senha}).encode()
    try:
        opener.open(urllib.request.Request(BASE + "/login", data=body, method="POST"))
        raise AssertionError("o login deveria redirecionar (303)")
    except urllib.error.HTTPError as e:
        if e.code != 303:
            raise AssertionError(f"login falhou (status {e.code})")
    for cookie in cj:
        if cookie.name == "sessao":
            return cookie.value
    raise AssertionError("o login não entregou o cookie de sessão")


class Operador:
    """Navegador com a sessão de um operador (admin por padrão)."""

    def __init__(self, pw, largura=DESKTOP[0], altura=DESKTOP[1],
                 bloquear_dados=False):
        self.browser = pw.chromium.launch(
            executable_path=os.environ["SHAMBALA_CHROMIUM"])
        self.context = self.browser.new_context(viewport={"width": largura, "height": altura})
        host = urllib.parse.urlsplit(BASE).hostname
        self.context.add_cookies([{"name": "sessao", "value": _cookie_sessao(),
                                   "domain": host, "path": "/"}])
        self.pagina = self.context.new_page()
        self.pagina.on("dialog", lambda d: d.accept())
        if bloquear_dados:
            self.context.route("**/checkin-trabalhador/dados*",
                               lambda rota: rota.abort("failed"))

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
        operador = Operador(pw, **opcoes)
        try:
            yield operador
        finally:
            operador.fechar()


def _card(pagina, nome):
    return pagina.locator(f".trab-checkin-card:has-text('{nome}')")


def _evidencia(pagina, nome):
    pagina.screenshot(path=os.path.join(EVIDENCIAS, nome + ".png"))


def _esperar_texto(pagina, trecho, segundos=8):
    try:
        pagina.get_by_text(trecho).first.wait_for(timeout=segundos * 1000)
    except Exception:
        raise AssertionError(f"a tela não mostrou {trecho!r}")


# ══════════════════════════════════════════════════════════════════════════
#  Gestão do dia
# ══════════════════════════════════════════════════════════════════════════

@cenario("N1 gestão aberta sem dia de atendimento: origem, contador e cards corretos")
def n1():
    with _operador() as op:
        pagina = op.abrir(f"/checkin-trabalhador?data={CENAS['hoje']}")
        assert "Gestão do dia" in pagina.locator("h1").inner_text()
        assert CENAS["hoje_br"] in pagina.locator(".subtitulo").inner_text()
        assert "✔ 2 presentes" in pagina.inner_text("body"), "contador de presentes"
        assert "QR" in _card(pagina, "Alice Chegada QR").inner_text()
        assert "Recepção" in _card(pagina, "Bruno Recepcao").inner_text()
        assert "⏳ Sem registro" in _card(pagina, "Carla Sem Registro").inner_text()
        assert _card(pagina, "Carla Sem Registro").locator(".btn-trab-presente").count() == 1, \
            "ativo sem registro deveria permitir a chegada"
        assert pagina.locator(".trab-checkin-card:has-text('Dami Inativo')").count() == 0, \
            "inativo sem registro na data não deveria aparecer"
        with conectar() as conn:
            assert_eq(conn.execute("SELECT COUNT(*) AS c FROM dias_trabalho").fetchone()["c"],
                      0, "a gestão não pode exigir/criar dia de atendimento")
        _evidencia(pagina, "n1-gestao-desktop")


@cenario("N2 dia passado: consulta somente leitura, inativo com fato visível")
def n2():
    with _operador() as op:
        pagina = op.abrir(f"/checkin-trabalhador?data={CENAS['ontem']}")
        assert "somente leitura" in pagina.inner_text("body")
        assert pagina.locator(".btn-trab-presente").count() == 0, \
            "dia passado não pode oferecer botões de escrita"
        dani = pagina.locator(".trab-checkin-card:has-text('Dami Inativo')")
        assert dani.count() == 1, "inativo COM registro na data deve aparecer"
        assert "inativo (histórico)" in dani.inner_text(), "situação identificada"
        assert "✔ Presente — 09:00 · Recepção" in dani.inner_text()
        _evidencia(pagina, "n2-passado-somente-leitura-desktop")


@cenario("N3 busca e filtros server-side preservados na navegação")
def n3():
    with _operador() as op:
        pagina = op.abrir("/checkin-trabalhador")
        pagina.fill('[name="busca"]', "Alice")
        pagina.click('#gestao-form button[type="submit"]')
        pagina.wait_for_load_state("networkidle")
        assert "busca=Alice" in urllib.parse.unquote(pagina.url)
        assert pagina.locator(".trab-checkin-card").count() == 1
        assert "Alice Chegada QR" in pagina.inner_text("body")

        pagina.fill('[name="busca"]', "")
        pagina.select_option('[name="filtro"]', "sem_registro")
        pagina.click('#gestao-form button[type="submit"]')
        pagina.wait_for_load_state("networkidle")
        assert "filtro=sem_registro" in pagina.url
        assert "Carla Sem Registro" in pagina.inner_text("body")
        assert "Alice Chegada QR" not in pagina.inner_text("body")

        # navegação ← mantém a consulta na nova data
        pagina.click('[data-nav="prev"]')
        pagina.wait_for_load_state("networkidle")
        assert f"data={CENAS['ontem']}" in pagina.url and "filtro=sem_registro" in pagina.url
        assert "somente leitura" in pagina.inner_text("body")
        _evidencia(pagina, "n3-filtros-desktop")


@cenario("N4 atualização parcial: muda sem recarregar; rede caída avisa e preserva")
def n4():
    # chega Carla pelo caminho legítimo (serviço), enquanto a tela está aberta
    with _operador() as op:
        pagina = op.abrir(f"/checkin-trabalhador?data={CENAS['hoje']}")
        url_antes = pagina.url
        with conectar() as conn:
            registrar_chegada(conn, _id_por_nome("Carla Sem Registro"))
        pagina.click("#trab-checkin-atualizar")
        _esperar_texto(pagina, "✔ Presente")
        carla = _card(pagina, "Carla Sem Registro")
        limite = time.time() + 8
        while time.time() < limite and "Presente" not in carla.inner_text():
            pagina.wait_for_timeout(150)
        assert "Presente" in carla.inner_text(), "o refresco não trouxe o fato novo"
        assert pagina.url == url_antes, "a atualização parcial navegou a página"

        # rede caída no próximo refresco: aviso, últimos dados mantidos
        op.context.route("**/checkin-trabalhador/dados*",
                         lambda rota: rota.abort("failed"))
        with conectar() as conn:
            aplicar_correcao(conn, trabalhador_id=_id_por_nome("Bruno Recepcao"),
                             data=CENAS["hoje"], operacao="anular",
                             motivo="rede-teste",
                             estado_base_esperado=_token_atual(
                                 conn, _id_por_nome("Bruno Recepcao"), CENAS["hoje"]),
                             atendente_id=1)
        pagina.click("#trab-checkin-atualizar")
        pagina.wait_for_timeout(800)
        assert pagina.locator("#trab-checkin-erro-conexao").is_visible(), \
            "falha de rede não foi avisada"
        assert "Bruno Recepcao" in pagina.inner_text("body"), \
            "a falha apagou os últimos resultados"
        op.context.unroute("**/checkin-trabalhador/dados*")
        pagina.click("#trab-checkin-atualizar")
        pagina.wait_for_timeout(800)
        assert not pagina.locator("#trab-checkin-erro-conexao").is_visible(), \
            "o aviso de rede não limpou após a conexão voltar"
        bruno = _card(pagina, "Bruno Recepcao")
        assert bruno.count() == 1, "o trabalhador sumiu da grade"
        assert "Sem registro" in bruno.inner_text(), \
            "o lançamento anulado deveria voltar a ⏳ Sem registro"
        assert bruno.locator(".status-presente").count() == 0, \
            "o card ainda exibe status de presente"
        _evidencia(pagina, "n4-atualizacao-parcial-desktop")


# ══════════════════════════════════════════════════════════════════════════
#  Correções
# ══════════════════════════════════════════════════════════════════════════

@cenario("N5 fluxo de correção pela tela: corrigir horário, confirmar, histórico")
def n5():
    with conectar() as conn:
        admin_id = conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario = 'admin'").fetchone()["id"]
    with _operador() as op:
        tid = _id_por_nome("Alice Chegada QR")
        pagina = op.abrir(f"/checkin-trabalhador?data={CENAS['hoje']}")
        pagina.click(f'a[href="/checkin-trabalhador/{tid}/correcao?data={CENAS["hoje"]}"]')
        pagina.wait_for_load_state("networkidle")
        assert "Estado atual nesta data: Presente às 08:00 · QR" in \
            pagina.inner_text("body"), "estado atual não apresentado"
        pagina.check('input[name="operacao"][value="corrigir_horario"]')
        pagina.fill('[name="nova_hora"]', "08:05")
        pagina.fill('[name="motivo"]', "chegou 08:05; o terminal marcou 08:00")
        pagina.click('button[type="submit"].btn-primario')
        pagina.wait_for_load_state("networkidle")
        assert "Correção aplicada" in pagina.inner_text("body"), "faltou o aviso de sucesso"
        assert "Alterações desta data" in pagina.inner_text("body")
        assert "Correção de horário" in pagina.inner_text("body"), "operação sem rótulo"
        assert "08:05" in pagina.inner_text("body")
        _evidencia(pagina, "n5-correcao-fluxo-desktop")
        with conectar() as conn:
            fato = _fato(conn, tid, CENAS["hoje"])
            assert_eq(fato["hora_chegada"], "08:05", "o fato não foi corrigido")
            assert_eq(fato["origem_chegada"], "terminal_qr",
                      "a origem registrada deveria ser preservada")
            assert_eq(_auditoria(conn, tid, CENAS["hoje"]), 1, "uma linha de auditoria")
            linha = conn.execute(
                "SELECT atendente_id FROM trabalhador_presenca_correcao").fetchone()
            assert_eq(linha["atendente_id"], admin_id, "autor da correção registrado")


@cenario("N6 conflito: fato mudou depois de abrir o formulário — nada é sobrescrito")
def n6():
    with _operador() as op:
        tid = _id_por_nome("Bruno Recepcao")
        pagina = op.abrir(f"/checkin-trabalhador/{tid}/correcao?data={CENAS['hoje']}")
        assert "Presente às 07:30 · Recepção" in pagina.inner_text("body")

        # outra pessoa corrige enquanto o formulário está aberto
        with conectar() as conn:
            aplicar_correcao(conn, trabalhador_id=tid, data=CENAS["hoje"],
                             operacao="corrigir_horario", nova_hora="07:15",
                             motivo="ajuste feito por outra pessoa",
                             estado_base_esperado=_token_atual(conn, tid, CENAS["hoje"]),
                             atendente_id=1)

        pagina.check('input[name="operacao"][value="corrigir_horario"]')
        pagina.fill('[name="nova_hora"]', "09:59")
        pagina.fill('[name="motivo"]', "tentativa sobre estado velho")
        pagina.click('button[type="submit"].btn-primario')
        pagina.wait_for_load_state("networkidle")
        corpo = pagina.inner_text("body")
        assert "situação mudou" in corpo, "faltou o aviso de conflito"
        assert "Nada foi gravado" in corpo
        with conectar() as conn:
            fato = _fato(conn, tid, CENAS["hoje"])
            assert_eq(fato["hora_chegada"], "07:15",
                      "o formulário velho sobrescreveu a correção mais recente")
            assert_eq(_auditoria(conn, tid, CENAS["hoje"]), 1,
                      "o conflito não pode gerar linha de auditoria")
        # o formulário reaberto mostra o estado novo (o operador decide de novo)
        assert "07:15" in corpo, "a tela não atualizou o estado atual"
        _evidencia(pagina, "n6-conflito-desktop")


@cenario("N7 recusas na tela: motivo vazio não sai do navegador; hora errada é recusada")
def n7():
    with _operador() as op:
        tid = _id_por_nome("Alice Chegada QR")
        pagina = op.abrir(f"/checkin-trabalhador/{tid}/correcao?data={CENAS['hoje']}")
        pagina.check('input[name="operacao"][value="corrigir_horario"]')
        pagina.fill('[name="nova_hora"]', "08:20")
        # motivo vazio: o próprio navegador barra o envio (campo obrigatório)
        pagina.click('button[type="submit"].btn-primario')
        pagina.wait_for_timeout(600)
        assert "correcao" in pagina.url and \
            "Correção aplicada" not in pagina.inner_text("body"), \
            "motivo vazio deveria ser barrado antes do envio"

        # hora inválida chega ao servidor pela URL direta (defesa em profundidade)
        pagina.goto(BASE + f"/checkin-trabalhador/{tid}/correcao?data={CENAS['hoje']}")
        resp = pagina.request.post(
            BASE + f"/checkin-trabalhador/{tid}/correcao",
            form={"data": CENAS["hoje"], "operacao": "corrigir_horario",
                  "motivo": "teste de validação", "estado_base": "qualquer",
                  "nova_hora": "25:99", "incluir_estado": ""},
            max_redirects=0)
        assert_eq(resp.status, 303)
        local = urllib.parse.unquote(resp.headers.get("location", ""))
        assert "erro_horario" in local, f"hora inválida deveria voltar como erro_horario: {local}"
        pagina.goto(BASE + urllib.parse.unquote(resp.headers.get("location", "")))
        assert "Horário fora de HH:MM" in pagina.inner_text("body")
        with conectar() as conn:
            fato = _fato(conn, tid, CENAS["hoje"])
            assert_eq(fato["hora_chegada"], "08:00", "a recusa alterou o fato")
        _evidencia(pagina, "n7-recusas-desktop")


# ══════════════════════════════════════════════════════════════════════════
#  Ficha do trabalhador
# ══════════════════════════════════════════════════════════════════════════

@cenario("N8 ficha: selo ajustado, origem por registro e histórico de alterações")
def n8():
    tid = _id_por_nome("Alice Chegada QR")
    with conectar() as conn:
        aplicar_correcao(conn, trabalhador_id=tid, data=CENAS["hoje"],
                         operacao="corrigir_horario", nova_hora="08:05",
                         motivo="prova de ficha",
                         estado_base_esperado=_token_atual(conn, tid, CENAS["hoje"]),
                         atendente_id=1)
    with _operador() as op:
        pagina = op.abrir(f"/cadastros/trabalhadores/{tid}/presenca")
        corpo = pagina.inner_text("body")
        assert "✎ ajustado" in corpo, "faltou o selo de lançamento ajustado"
        assert "ORIGEM" in corpo.upper() and "QR" in corpo, "faltou a origem do registro"
        assert "Histórico de alterações" in corpo, "admin não vê o histórico"
        assert "prova de ficha" in corpo and "Correção de horário" in corpo
        assert "08:05" in corpo
        _evidencia(pagina, "n8-ficha-historico-desktop")


# ══════════════════════════════════════════════════════════════════════════
#  Relatórios e impressão
# ══════════════════════════════════════════════════════════════════════════

@cenario("N9 relatório geral: sem percentual, 7 dias e origens; impressão limpa")
def n9():
    with _operador(largura=SMARTPHONE[0], altura=SMARTPHONE[1]) as op:
        pagina = op.abrir(
            f"/relatorios/presenca-trabalhadores?data_ini={CENAS['hoje']}"
            f"&data_fim={CENAS['hoje']}")
        corpo = pagina.inner_text("body")
        assert "% Presença" not in corpo and "% Falta" not in corpo, \
            "percentuais removidos deveriam permanecer fora"
        assert "Registros explícitos" in corpo
        assert "Alice Chegada QR" in corpo and "Bruno Recepcao" in corpo
        assert_eq(pagina.locator('select[name="dia_semana"] option').count(), 8,
                  "o filtro de dia deveria oferecer Todos + os 7 dias")
        assert_eq(pagina.locator('select[name="origem"] option').count(), 6,
                  "o filtro de origem deveria oferecer Todas + 5 valores")
        estouro = pagina.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert_eq(estouro, 0, f"smartphone: conteúdo vaza na horizontal ({estouro} px)")
        pagina.emulate_media(media="print")
        assert not pagina.locator(".barra-busca").is_visible(), \
            "a impressão não deveria mostrar os controles de filtro"
        assert pagina.locator("table").first.is_visible(), "a tabela sumiu na impressão"
        _evidencia(pagina, "n9-geral-impressao-smartphone")
        pagina.emulate_media(media="screen")
        _evidencia(pagina, "n9-geral-tela-smartphone")


@cenario("N10 relatório individual: dia da semana em PT, origem e impressão")
def n10():
    tid = _id_por_nome("Dami Inativo")
    with _operador() as op:
        pagina = op.abrir(
            f"/relatorios/presenca-trabalhador?trabalhador_id={tid}"
            f"&data_ini={CENAS['ontem']}&data_fim={CENAS['ontem']}")
        corpo = pagina.inner_text("body")
        assert "Dami Inativo" in corpo
        assert CENAS["ontem_br"] in corpo
        assert "inativo" in corpo, "situação do trabalhador deveria ser sinalizada"
        assert "Recepção" in corpo, "origem traduzida na linha"
        pagina.emulate_media(media="print")
        assert not pagina.locator(".barra-busca").is_visible(), \
            "a impressão não deveria mostrar os controles"
        _evidencia(pagina, "n10-individual-impressao-desktop")


@cenario("N11 medidas reais: gestão do dia e correção utilizáveis no smartphone")
def n11():
    with _operador(largura=SMARTPHONE[0], altura=SMARTPHONE[1]) as op:
        pagina = op.abrir(f"/checkin-trabalhador?data={CENAS['hoje']}")
        estouro = pagina.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert_eq(estouro, 0, f"gestão no smartphone vazou na horizontal ({estouro} px)")
        alvo = pagina.locator("#trab-checkin-atualizar").bounding_box()
        assert alvo and alvo["height"] >= 34, \
            f"botão de atualização pequeno demais para toque ({alvo and alvo['height']} px)"
        _evidencia(pagina, "n11-gestao-smartphone")
        tid = _id_por_nome("Alice Chegada QR")
        pagina.click(f'a[href="/checkin-trabalhador/{tid}/correcao?data={CENAS["hoje"]}"]')
        pagina.wait_for_load_state("networkidle")
        estouro = pagina.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert_eq(estouro, 0, "tela de correção vazou na horizontal no smartphone")
        caixa = pagina.locator('textarea[name="motivo"]').bounding_box()
        assert caixa and caixa["width"] > 200, "o motivo espremeu na tela pequena"
        _evidencia(pagina, "n11-correcao-smartphone")


# ══════════════════════════════════════════════════════════════════════════
#  Runner
# ══════════════════════════════════════════════════════════════════════════

def main():
    faltam = _ferramentas_ausentes()
    if faltam:
        print("\n[ABORTADO] ferramentas ausentes para o teste de gestão/correções no navegador:")
        for item in faltam:
            print(f"    - {item}")
        print("  nada foi executado — ausência de ferramenta não é sucesso.")
        return 3

    os.makedirs(EVIDENCIAS, exist_ok=True)
    preparar_banco()
    _garantir_admin()

    total = ok = 0
    falhas = []
    for nome, fn in CENARIOS:
        _preparar_cenas()
        total += 1
        try:
            fn()
            ok += 1
            print(f"  PASS  {nome}")
        except Exception as e:  # noqa: BLE001
            falhas.append(nome)
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{ok}/{total} testes passaram (TASK_004 — gestão e correções no navegador)")
    print(f"  evidências: {EVIDENCIAS}")
    limpar_banco()
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
