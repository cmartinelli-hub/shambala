#!/usr/bin/env python3
"""Suíte TASK_003 — o terminal visto pelo navegador: leitor real, câmera simulada.

Esta suíte NÃO injeta texto no callback do decodificador: monta um vídeo (y4m)
com os QRs reais gerados pelo próprio serviço das carteirinhas
(`servico_codigo_trabalhador.qr_data_uri`), entrega esse vídeo ao Chromium como
câmera (`--use-file-for-fake-video-capture`) e deixa a página fazer o resto —
`getUserMedia`, o laço de quadros e o jsQR vendorizado decodificam de verdade.
Depois só se observa o que o navegador fez: o POST saído e a tela mostrada.

Ferramenta ausente nunca conta como sucesso: sem Playwright, Chromium, Pillow ou
ffmpeg a suíte encerra com falha explícita (código 3), dizendo o que falta.

As recusas de câmera (negada, ausente, ocupada) são simuladas na API
`getUserMedia`, porque dependem do estado do hardware/da permissão; isso é
distinto de falsificar a decodificação, que aqui é real.

Evidências (capturas nas medidas reais de smartphone e tablet) vão para
`~/Documentos/evidencias-task003-terminal/` — nomes fictícios do banco
descartável, nunca dados reais.

Uso (recomendado): bash tests/rodar_testes.sh
"""
import os
import sys
import io
import json
import time
import base64
import shutil
import contextlib
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import exigir_ambiente_de_teste  # noqa: E402

from test_tres_passes import (  # noqa: E402
    assert_eq, preparar_banco, limpar_banco, _garantir_admin, BASE, BANCO,
)
from test_codigo_trabalhador import _limpa_negocio  # noqa: E402
from test_terminal_presenca import (  # noqa: E402
    _forcar_codigo, _presencas, _terminal,
)

from banco import conectar  # noqa: E402
from servico_codigo_trabalhador import qr_data_uri, trocar_codigo  # noqa: E402

EVIDENCIAS = os.path.expanduser(os.environ.get(
    "SHAMBALA_EVIDENCIAS_DIR", "~/Documentos/evidencias-task003-terminal"))
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOTOS = os.path.join(RAIZ, "static", "fotos")
FOTO_EVIDENCIA = "trab_evidencia_task003_ficticia.jpg"

FPS = 10
LARGURA_VIDEO, ALTURA_VIDEO = 480, 360

# Medidas reais de aparelho (CSS px): smartphone pequeno, smartphone comum em
# retrato e em paisagem, tablet em retrato e em paisagem.
MEDIDAS = [
    ("smartphone-360x640", 360, 640),
    ("smartphone-412x915", 412, 915),
    ("smartphone-paisagem-915x412", 915, 412),
    ("tablet-768x1024", 768, 1024),
    ("tablet-paisagem-1024x768", 1024, 768),
]

CENARIOS = []


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
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        faltam.append("Pillow (gerar os quadros do vídeo)")
    if not shutil.which("ffmpeg"):
        faltam.append("ffmpeg (montar o vídeo da câmera simulada)")
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
        faltam.append("Chromium (o leitor precisa rodar num navegador real)")
    else:
        os.environ["SHAMBALA_CHROMIUM"] = caminho
    return faltam


# ── vídeo com os QRs reais das carteirinhas ──────────────────────────────────

def _quadro(conteudo):
    """Um quadro 480×360 com o QR real da carteirinha no centro (ou só o fundo)."""
    from PIL import Image
    fundo = Image.new("RGB", (LARGURA_VIDEO, ALTURA_VIDEO), (232, 232, 232))
    if conteudo is None:
        return fundo
    if len(conteudo) == 4 and conteudo.isdigit():
        png = base64.b64decode(qr_data_uri(conteudo).split(",", 1)[1])
    else:
        import qrcode
        qr = qrcode.QRCode(box_size=8, border=4)      # versão ajustada ao conteúdo
        qr.add_data(conteudo)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(buf, "PNG")
        png = buf.getvalue()
    img = Image.open(io.BytesIO(png)).convert("RGB").resize((240, 240), Image.NEAREST)
    fundo.paste(img, ((LARGURA_VIDEO - 240) // 2, (ALTURA_VIDEO - 240) // 2))
    return fundo


def _video(pasta, nome, trechos):
    """Gera o y4m: `trechos` = [(conteudo_ou_None, segundos), ...]."""
    quadros = os.path.join(pasta, "quadros-" + nome)
    shutil.rmtree(quadros, ignore_errors=True)
    os.makedirs(quadros)
    n = 0
    for conteudo, segundos in trechos:
        quadro = _quadro(conteudo)
        for _ in range(max(1, int(round(segundos * FPS)))):
            n += 1
            quadro.save(os.path.join(quadros, "f%03d.png" % n))
    saida = os.path.join(pasta, nome + ".y4m")
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
         "-i", os.path.join(quadros, "f%03d.png"), "-pix_fmt", "yuv420p", saida],
        capture_output=True, text=True)
    shutil.rmtree(quadros, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg não montou o vídeo: " + (r.stderr or "")[:300])
    return saida


# ── o aparelho ───────────────────────────────────────────────────────────────

class Aparelho:
    """Navegador com câmera simulada, na pele do terminal da entrada."""

    def __init__(self, pw, video=None, liberar_camera=True, pedir_permissoes=True,
                 recusa=None, rota=None, largura=412, altura=915):
        args = ["--use-fake-device-for-media-stream"]
        if pedir_permissoes:
            args.append("--use-fake-ui-for-media-stream")
        if video:
            args.append("--use-file-for-fake-video-capture=" + video)
        self.browser = pw.chromium.launch(
            executable_path=os.environ["SHAMBALA_CHROMIUM"], args=args)
        self.context = self.browser.new_context(viewport={"width": largura, "height": altura})
        if liberar_camera:
            self.context.grant_permissions(["camera"], origin=BASE)
        self.pagina = self.context.new_page()
        self.envios = []
        if rota:
            self.pagina.route("**/terminal/chegada", rota)
        self.pagina.on("request", self._ao_enviar)
        # Um invólucro sobre a API: conta as câmeras abertas e permite simular as
        # recusas do navegador (negada, ausente, ocupada) sem tocar no decodificador.
        self.pagina.add_init_script(f"""
            window.__trilhas = [];
            window.__recusaCamera = {json.dumps(recusa)};
            const verdadeira = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = function (opcoes) {{
              if (window.__recusaCamera) {{
                return Promise.reject(new DOMException('recusa simulada', window.__recusaCamera));
              }}
              return verdadeira(opcoes).then(function (fluxo) {{
                window.__trilhas.push(fluxo);
                return fluxo;
              }});
            }};
        """)

    def _ao_enviar(self, requisicao):
        if requisicao.url.endswith("/terminal/chegada") and requisicao.method == "POST":
            self.envios.append(json.loads(requisicao.post_data or "{}"))

    def ativar(self, usuario="admin", senha="teste123"):
        """A ativação é ação de operador; o aparelho herda só o cookie do terminal."""
        cliente = _terminal(usuario, senha)
        host, _, porta = BASE.split("//", 1)[1].partition(":")
        self.context.add_cookies([{"name": "sessao_terminal", "value": _cookie_de(cliente),
                                   "domain": host, "path": "/"}])
        return cliente

    def abrir(self, caminho="/terminal/"):
        self.pagina.goto(BASE + caminho)
        return self.pagina

    def texto(self, seletor):
        return self.pagina.locator(seletor).inner_text()

    def visivel(self, seletor):
        return self.pagina.locator(seletor).is_visible()

    def fechar(self):
        with contextlib.suppress(Exception):
            self.browser.close()


def _cookie_de(cliente, nome="sessao_terminal"):
    for tratamento in cliente.opener.handlers:
        frasco = getattr(tratamento, "cookiejar", None) or getattr(tratamento, "jar", None)
        if isinstance(frasco, http.cookiejar.CookieJar):
            for cookie in frasco:
                if cookie.name == nome:
                    return cookie.value
    raise AssertionError(f"o aparelho não recebeu o cookie {nome}")


@contextlib.contextmanager
def _aparelho(**opcoes):
    from playwright.sync_api import sync_playwright
    aparelho = None
    with sync_playwright() as pw:
        try:
            aparelho = Aparelho(pw, **opcoes)
            aparelho.ativar()
            yield aparelho
        finally:
            if aparelho:
                aparelho.fechar()


# ── esperas ──────────────────────────────────────────────────────────────────

def _mensagem(aparelho, trecho, segundos=15):
    """A faixa de resultado mostra `trecho`; falha dizendo o que estava lá."""
    limite = time.time() + segundos
    while time.time() < limite:
        if aparelho.pagina.locator("#tm-resultado").is_visible():
            texto = aparelho.texto("#tm-resultado-mensagem")
            if trecho in texto:
                return texto
        aparelho.pagina.wait_for_timeout(120)
    atual = (aparelho.texto("#tm-resultado-mensagem")
             if aparelho.pagina.locator("#tm-resultado").is_visible()
             else "(nenhum resultado na tela)")
    raise AssertionError(f"a tela não mostrou {trecho!r}; está em {atual!r}")


def _esperar_envios(aparelho, quantidade, segundos=15):
    limite = time.time() + segundos
    while time.time() < limite and len(aparelho.envios) < quantidade:
        aparelho.pagina.wait_for_timeout(120)
    return len(aparelho.envios)


def _nome_na_tela(aparelho, nome, segundos=20):
    """A confirmação de QUEM foi lido — responde depois, não quando se envia."""
    limite = time.time() + segundos
    while time.time() < limite:
        if aparelho.pagina.locator("#tm-resultado").is_visible() and \
           nome in aparelho.texto("#tm-resultado-nome"):
            return
        aparelho.pagina.wait_for_timeout(120)
    raise AssertionError(f"a tela não mostrou {nome!r} como confirmação")


def _trilhas_vivas(aparelho):
    return aparelho.pagina.evaluate(
        """() => window.__trilhas.reduce(
                 (n, f) => n + f.getVideoTracks().filter(t => t.readyState === 'live').length,
                 0)""")


def _evidencia(aparelho, nome):
    _evidencia_em(aparelho.pagina, nome)


def _evidencia_em(pagina, nome):
    pagina.screenshot(path=os.path.join(EVIDENCIAS, nome + ".png"))


# ══════════════════════════════════════════════════════════════════════════
#  Câmera: leitura real pelo jsQR
# ══════════════════════════════════════════════════════════════════════════

@cenario("L1 QR real da carteirinha, com zero inicial, é decodificado e registra")
def l1(pasta):
    video = _video(pasta, "sozinha", [("0047", 2.0)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Presença registrada")
        assert_eq(len(aparelho.envios), 1,
                  "a mesma carteirinha no enquadro gerou mais de um envio")
        assert_eq(aparelho.envios[0]["codigo"], "0047", "o zero inicial se perdeu")
        assert_eq(aparelho.envios[0]["origem"], "terminal_qr", "origem da leitura")
        assert_eq(aparelho.texto("#tm-resultado-nome"), "Trabalhadora Zero Inicial")
        with conectar() as conn:
            linha = _presencas(conn, _id_por_nome(conn, "Trabalhadora Zero Inicial"))[0]
        assert linha["hora_chegada"] in aparelho.texto("#tm-resultado-hora"), \
            "a tela não mostra o horário confirmado pelo servidor"
        assert_eq(linha["origem_chegada"], "terminal_qr", "origem gravada")
        _evidencia(aparelho, "sucesso-smartphone")


@cenario("L2 carteirinha mantida no enquadro: um envio só, sem som incessante")
def l2(pasta):
    video = _video(pasta, "segurar", [("0047", 4.0)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Presença registrada")
        aparelho.pagina.wait_for_timeout(7000)      # vários loops do mesmo QR
        assert_eq(len(aparelho.envios), 1,
                  "a carteirinha segurada voltou a enviar (e a tocar som)")
        assert_eq(_trilhas_vivas(aparelho), 1, "a câmera ficou com trilhas duplicadas")


@cenario("L3 duas carteirinhas consecutivas: a segunda é atendida na hora")
def l3(pasta):
    video = _video(pasta, "duas", [("0047", 2.0), (None, 1.5), ("0048", 2.0), (None, 1.5)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Presença registrada")
        primeiro_nome = aparelho.texto("#tm-resultado-nome")
        _esperar_envios(aparelho, 2, segundos=20)
        assert_eq([e["codigo"] for e in aparelho.envios[:2]], ["0047", "0048"],
                  "a ordem das leituras não é a dos cartões")
        assert_eq(aparelho.envios[1]["origem"], "terminal_qr", "origem da segunda leitura")
        _nome_na_tela(aparelho, "Trabalhador Seguido")
        assert_eq(primeiro_nome, "Trabalhadora Zero Inicial",
                  "a primeira confirmação não foi a da primeira carteirinha")
        assert_eq(_trilhas_vivas(aparelho), 1, "a segunda leitura abriu outra câmera")


@cenario("L4 repetição: mantém o primeiro horário e diz que já estava registrada")
def l4(pasta):
    video = _video(pasta, "repetida", [("0047", 1.5), (None, 1.5)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Presença registrada")
        primeira_hora = aparelho.texto("#tm-resultado-hora")
        _mensagem(aparelho, "Presença já registrada", segundos=25)
        mensagem = aparelho.texto("#tm-resultado-mensagem")
        assert "às" in mensagem, f"a repetição não informou o horário: {mensagem}"
        assert_eq(aparelho.texto("#tm-resultado-hora"), primeira_hora,
                  "a repetição mostrou outro horário")
        with conectar() as conn:
            linhas = _presencas(conn, _id_por_nome(conn, "Trabalhadora Zero Inicial"))
        assert_eq(len(linhas), 1, "a repetição criou presença nova")
        _evidencia(aparelho, "repeticao-smartphone")


@cenario("L5 QR de outra coisa (URL): recusado, sem abrir link e sem envio")
def l5(pasta):
    video = _video(pasta, "url", [("https://shambala.example/p/0047", 2.0)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Código não encontrado")
        assert_eq(aparelho.envios, [], "conteúdo estranho ao cartão foi enviado ao servidor")
        assert_eq(aparelho.pagina.url, BASE + "/terminal/", "a tela navegou para o link do QR")
        _evidencia(aparelho, "erro-conteudoestranho-smartphone")


@cenario("L6 código inexistente: 'Código não encontrado' e nada gravado")
def l6(pasta):
    video = _video(pasta, "inexistente", [("9999", 2.0)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Código não encontrado")
        assert_eq(len(aparelho.envios), 1, "a recusa não veio do servidor")
        assert_eq(aparelho.texto("#tm-resultado-nome"), "", "a recusa revelou alguém")
        with conectar() as conn:
            assert_eq(_total_presencas(conn), 0, "código inexistente gravou presença")


def _total_presencas(conn):
    return conn.execute("SELECT count(*) AS n FROM trabalhador_presenca").fetchone()["n"]


@cenario("L7 carteirinha substituída: aviso próprio, sem revelar o nome")
def l7(pasta):
    video = _video(pasta, "substituida", [("0049", 2.0)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Carteirinha substituída; procure a recepção")
        assert_eq(aparelho.texto("#tm-resultado-nome"), "", "a recusa revelou o nome")
        assert "Trabalhador Preterido" not in aparelho.pagina.content(), "a tela mostrou o cadastro"


@cenario("L8 cadastro inativo lido por câmera: 'Cadastro inativo; procure a recepção'")
def l8(pasta):
    video = _video(pasta, "inativo", [("0050", 2.0)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Cadastro inativo; procure a recepção")
        with conectar() as conn:
            assert_eq(_presencas(conn, _id_por_nome(conn, "Trabalhador Inativo na Porta")), [],
                      "inativo teve presença gravada")


@cenario("L9 foto do cadastro aparece na confirmação (referência local, sem CDN)")
def l9(pasta):
    video = _video(pasta, "com-foto", [("0051", 2.0)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Presença registrada")
        assert aparelho.visivel("#tm-foto"), "a foto disponível não apareceu"
        src = aparelho.pagina.locator("#tm-foto").get_attribute("src")
        assert src.startswith("/static/fotos/"), f"foto servida de outro lugar: {src}"
        _evidencia(aparelho, "sucesso-com-foto-smartphone")


# ══════════════════════════════════════════════════════════════════════════
#  Teclado do mesmo aparelho — o MESMO caminho do servidor
# ══════════════════════════════════════════════════════════════════════════

@cenario("L10 teclado: quatro posições, apagar, limpar, teclado físico, zeros preservados")
def l10(pasta):
    with _aparelho(video=None) as aparelho:
        aparelho.abrir()
        aparelho.pagina.click("#tm-ir-teclado")
        assert aparelho.visivel("#tm-teclado"), "o teclado não abriu"
        posicoes = aparelho.pagina.locator(".tm-posicao")
        assert_eq(posicoes.count(), 4, "as posições visíveis não são quatro")

        for digito in ("0", "0"):
            aparelho.pagina.click(f'.tm-tecla[data-digito="{digito}"]')
        assert_eq([posicoes.nth(i).inner_text() for i in range(4)],
                  ["0", "0", "", ""], "as posições não mostram o digitado")
        assert aparelho.pagina.locator("#tm-confirmar").is_disabled(), \
            "confirmar habilitou com entrada incompleta"
        assert_eq(aparelho.envios, [], "a entrada incompleta foi enviada sozinha")

        aparelho.pagina.click('.tm-tecla[data-acao="apagar"]')
        assert_eq(posicoes.nth(1).inner_text(), "", "apagar não tirou o último dígito")
        aparelho.pagina.click('.tm-tecla[data-acao="limpar"]')

        for digito in "0047":
            aparelho.pagina.keyboard.press(digito)
        assert_eq([posicoes.nth(i).inner_text() for i in range(4)],
                  ["0", "0", "4", "7"], "o teclado físico não preservou os zeros")
        _evidencia(aparelho, "digitacao-smartphone")
        aparelho.pagina.keyboard.press("Enter")
        _mensagem(aparelho, "Presença registrada")
        assert_eq(aparelho.envios[0]["codigo"], "0047", "o código enviado pelo teclado")
        assert_eq(aparelho.envios[0]["origem"], "terminal_codigo", "origem da digitação")
        with conectar() as conn:
            linha = _presencas(conn, _id_por_nome(conn, "Trabalhadora Zero Inicial"))[0]
        assert_eq(linha["origem_chegada"], "terminal_codigo", "origem gravada da digitação")


@cenario("L11 entrada incompleta não é completada: confirmar pede os quatro números")
def l11(pasta):
    with _aparelho(video=None) as aparelho:
        aparelho.abrir()
        aparelho.pagina.click("#tm-ir-teclado")
        for digito in "004":
            aparelho.pagina.click(f'.tm-tecla[data-digito="{digito}"]')
        posicoes = [aparelho.pagina.locator(".tm-posicao").nth(i).inner_text()
                    for i in range(4)]
        assert aparelho.pagina.locator("#tm-confirmar").is_disabled(), \
            "confirmar habilitou com três dígitos"
        aparelho.pagina.keyboard.press("Enter")      # o mesmo guardião do botão
        aparelho.pagina.wait_for_timeout(400)
        assert "quatro" in aparelho.texto("#tm-msg-teclado"), \
            "não avisou que faltou dígito"
        assert_eq(aparelho.envios, [], "enviou entrada incompleta")
        # NADA de completar sozinha: 004 continua 004 (zero inicial preservado,
        # sem virar 0040 nem número inteiro).
        assert_eq(posicoes, ["0", "0", "4", ""], "a entrada incompleta foi completada")
        aparelho.pagina.click('.tm-tecla[data-acao="limpar"]')
        assert_eq([aparelho.pagina.locator(".tm-posicao").nth(i).inner_text()
                   for i in range(4)], ["", "", "", ""], "limpar não esvaziou")


@cenario("L12 teclado com 0000: os quatro caracteres chegam intactos ao servidor")
def l12(pasta):
    with _aparelho(video=None) as aparelho:
        aparelho.abrir()
        aparelho.pagina.click("#tm-ir-teclado")
        for _ in range(4):
            aparelho.pagina.click('.tm-tecla[data-digito="0"]')
        aparelho.pagina.click("#tm-confirmar")
        _mensagem(aparelho, "Presença registrada")
        assert_eq(aparelho.envios[0]["codigo"], "0000", "0000 virou outra coisa")


# ══════════════════════════════════════════════════════════════════════════
#  Falhas de rede, de servidor e de sessão
# ══════════════════════════════════════════════════════════════════════════

@cenario("L13 envio em andamento pausa as duas entradas; sem resposta, a tela não trava")
def l13(pasta):
    # A requisição fica SEGURADA (não respondida nem abortada): é o estado real de
    # uma conexão lenta, e nada depende de dormir dentro do manipulador.
    seguradas = []

    def segurar(rota):
        seguradas.append(rota)

    video = _video(pasta, "lento", [("0047", 3.0), (None, 1.0)])
    with _aparelho(video=video, rota=segurar) as aparelho:
        aparelho.abrir()
        _esperar_envios(aparelho, 1, segundos=15)         # a leitura saiu, sem resposta
        aparelho.pagina.click("#tm-ir-teclado")
        for digito in "0048":
            aparelho.pagina.keyboard.press(digito)
        assert aparelho.pagina.locator("#tm-confirmar").is_disabled(), \
            "havia envio em andamento e o teclado liberou nova confirmação"
        aparelho.pagina.keyboard.press("Enter")
        aparelho.pagina.wait_for_timeout(1500)
        assert_eq(len(aparelho.envios), 1,
                  "teclado e câmera mandaram requisições sobrepostas")
        assert_eq(aparelho.envios[0]["codigo"], "0047", "o envio parado não é o da carteirinha")
        if aparelho.pagina.locator("#tm-resultado").is_visible():
            assert "Presença" not in aparelho.texto("#tm-resultado-mensagem"), \
                "houve sucesso antes de qualquer resposta"
        # O limite de 12 s devolve a interface ao voluntário: nada de
        # carregamento infinito quando a conexão nunca responde.
        _mensagem(aparelho, "Não foi possível confirmar", segundos=22)
        for rota in seguradas:                 # libera o que ficou segurado
            with contextlib.suppress(Exception):
                rota.abort()


@cenario("L14 sem resposta do servidor: 'Não foi possível confirmar', sem falso sucesso")
def l14(pasta):
    def derrubar(rota):
        rota.abort("failed")

    video = _video(pasta, "caduc", [("0047", 2.0)])
    with _aparelho(video=video, rota=derrubar) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Não foi possível confirmar. Tente novamente.")
        assert "Presença registrada" not in aparelho.pagina.content(), \
            "houve sucesso sem resposta do servidor"
        aparelho.pagina.wait_for_timeout(5000)     # a carteirinha continua no enquadro
        assert_eq(len(aparelho.envios), 1,
                  "sem resposta, a carteirinha segurada reenviou e repôs o som")
        _evidencia(aparelho, "erro-semresposta-smartphone")


@cenario("L15 HTML de login no lugar do JSON: pede reativação, sem carregar para sempre")
def l15(pasta):
    def como_login(rota):
        rota.fulfill(status=200, content_type="text/html; charset=utf-8",
                     body="<!doctype html><title>Acesso</title><h1>Login</h1>")

    video = _video(pasta, "login", [("0047", 2.0)])
    with _aparelho(video=video, rota=como_login) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Terminal precisa ser ativado novamente")
        assert "Presença registrada" not in aparelho.pagina.content(), \
            "a tela de login virou presença registrada"
        aparelho.pagina.wait_for_timeout(4000)      # não pode se limpar sozinha
        assert aparelho.visivel("#tm-resultado"), "o aviso sumiu — tela em branco eterno"
        assert_eq(_trilhas_vivas(aparelho), 0, "a câmera seguiu ligada sem sessão")


@cenario("L16 servidor 500: mensagem clara, interface liberada, teclado como alternativa")
def l16(pasta):
    def quinhentos(rota):
        rota.fulfill(status=500, content_type="text/html", body="Internal Server Error")

    video = _video(pasta, "quinhentos", [("0047", 2.0)])
    with _aparelho(video=video, rota=quinhentos) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Não foi possível confirmar")
        assert "ativado novamente" not in aparelho.texto("#tm-resultado-mensagem"), \
            "falha do servidor foi contada como sessão expirada"
        _mensagem_esperar_observando(aparelho)
        aparelho.pagina.click("#tm-ir-teclado")
        assert aparelho.visivel("#tm-teclado"), "a interface travou depois do 500"
        for digito in "0047":
            aparelho.pagina.keyboard.press(digito)
        assert not aparelho.pagina.locator("#tm-confirmar").is_disabled(), \
            "com o servidor fora do ar o teclado ficou inutilizável"


def _mensagem_esperar_observando(aparelho):
    """Depois de um erro simples, a tela volta a observar (~3 s)."""
    limite = time.time() + 8
    while time.time() < limite:
        if not aparelho.pagina.locator("#tm-resultado").is_visible():
            return
        aparelho.pagina.wait_for_timeout(150)
    raise AssertionError("a tela não voltou a observar depois do erro")


# ══════════════════════════════════════════════════════════════════════════
#  Câmera negada, ausente, ocupada, alternada
# ══════════════════════════════════════════════════════════════════════════

@cenario("L17 permissão negada pelo navegador: aviso e teclado disponível")
def l17(pasta):
    with _aparelho(video=None, recusa="NotAllowedError",
                   liberar_camera=False, pedir_permissoes=False) as aparelho:
        aparelho.abrir()
        aparelho.pagina.wait_for_timeout(1500)
        aviso = aparelho.texto("#tm-msg-bloqueio")
        assert "negada" in aviso.lower(), f"aviso de permissão negada: {aviso!r}"
        assert "digite" in aviso.lower() or "teclado" in aviso.lower(), \
            f"o aviso não aponta o teclado: {aviso!r}"
        aparelho.pagina.click("#tm-ir-teclado")
        assert aparelho.visivel("#tm-teclado"), "sem câmera o teclado ficou indisponível"
        _evidencia(aparelho, "erro-cameranegada-smartphone")


@cenario("L18 nenhuma câmera no aparelho: avisa e segue operando pelo teclado")
def l18(pasta):
    with _aparelho(video=None, recusa="NotFoundError") as aparelho:
        aparelho.abrir()
        aparelho.pagina.wait_for_timeout(1200)
        aviso = aparelho.texto("#tm-msg-bloqueio")
        assert "Nenhuma câmera" in aviso and "teclado" in aviso.lower(), aviso
        assert aparelho.visivel("#tm-ir-teclado"), "a tela travou sem câmera"


@cenario("L19 câmera ocupada: diz para liberar e Reiniciar câmera resolve")
def l19(pasta):
    video = _video(pasta, "ocupada", [("0047", 2.0)])
    with _aparelho(video=video, recusa="NotReadableError") as aparelho:
        aparelho.abrir()
        aparelho.pagina.wait_for_timeout(1200)
        aviso = aparelho.texto("#tm-msg-bloqueio")
        assert "em uso" in aviso, aviso
        assert "Reiniciar câmera" in aviso, "o aviso não oferece o reinício"
        _evidencia(aparelho, "erro-cameraocupada-smartphone")
        # O outro aplicativo fechou: a mesma página, sem recarregar, volta a ler.
        aparelho.pagina.evaluate("() => { window.__recusaCamera = null; }")
        aparelho.pagina.click("#tm-reiniciar-camera")
        _mensagem(aparelho, "Presença registrada")
        assert_eq(aparelho.pagina.url, BASE + "/terminal/", "reiniciar recarregou a página")


@cenario("L20 alternar câmera, teclado e aba: sem trilha nem laço duplicado")
def l20(pasta):
    video = _video(pasta, "alternar", [("0048", 2.0), (None, 1.0)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        aparelho.pagina.wait_for_timeout(900)
        for _ in range(4):
            aparelho.pagina.click("#tm-ir-teclado")
            aparelho.pagina.click("#tm-voltar-camera")
            aparelho.pagina.wait_for_timeout(250)
        aparelho.pagina.evaluate("""() => {
            Object.defineProperty(document, 'visibilityState',
                {configurable: true, get: () => 'hidden'});
            document.dispatchEvent(new Event('visibilitychange'));
            Object.defineProperty(document, 'visibilityState',
                {configurable: true, get: () => 'visible'});
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        aparelho.pagina.wait_for_timeout(800)
        assert_eq(_trilhas_vivas(aparelho), 1, "sobrou trilha de câmera depois de alternar")
        antes = len(aparelho.envios)
        _esperar_envios(aparelho, antes + 1, segundos=15)
        aparelho.pagina.wait_for_timeout(2000)
        assert_eq(len(aparelho.envios) - antes, 1,
                  "a leitura duplicou — laço de decodificação em paralelo")


# ══════════════════════════════════════════════════════════════════════════
#  Sessão que expira no meio do uso
# ══════════════════════════════════════════════════════════════════════════

@cenario("L21 quem ativou perde o acesso: o aparelho para e pede nova ativação")
def l21(pasta):
    video = _video(pasta, "caindo", [("0047", 1.5), (None, 1.5)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Presença registrada")
        admin = _id_admin()
        with conectar() as conn:
            conn.execute("UPDATE atendentes SET ativo = 0 WHERE id = %s", (admin,))
        try:
            _mensagem(aparelho, "Terminal precisa ser ativado novamente", segundos=25)
            tela = aparelho.pagina.content()
            assert "Trabalhadora Zero Inicial" not in tela, "deu nome com a sessão encerrada"
            assert_eq(_trilhas_vivas(aparelho), 0, "a câmera seguiu ligada sem sessão")
            _evidencia(aparelho, "erro-sessaoexpirada-smartphone")
        finally:
            with conectar() as conn:
                conn.execute("UPDATE atendentes SET ativo = 1 WHERE id = %s", (admin,))


def _id_admin():
    with conectar() as conn:
        return conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario = 'admin'").fetchone()["id"]


# ══════════════════════════════════════════════════════════════════════════
#  Medidas reais de smartphone e tablet
# ══════════════════════════════════════════════════════════════════════════

@cenario("L22 retrato e paisagem, smartphone e tablet: nada cortado, nada escondido")
def l22(pasta):
    # Sem QR na câmera: o terminal fica observando, que é o estado que se mede.
    with _aparelho(video=None) as aparelho:
        for nome, largura, altura in MEDIDAS:
            aparelho.pagina.set_viewport_size({"width": largura, "height": altura})
            aparelho.abrir()
            _esperar_estado_camera(aparelho, "pronta", nome)
            _conferir_medidas(aparelho, nome, "leitura")
            _evidencia(aparelho, f"leitura-{nome}")
            aparelho.pagina.click("#tm-ir-teclado")
            for digito in "0047":
                caixa = aparelho.pagina.locator(f'.tm-tecla[data-digito="{digito}"]').bounding_box()
                assert caixa and caixa["height"] >= 44, \
                    f"{nome}: tecla com alvo tátil menor que 44 px ({caixa and caixa['height']})"
            _conferir_medidas(aparelho, nome, "teclado")
            _evidencia(aparelho, f"teclado-{nome}")
            aparelho.pagina.click("#tm-voltar-camera")
            _esperar_estado_camera(aparelho, "pronta", nome)
            aparelho.pagina.wait_for_timeout(400)


def _esperar_estado_camera(aparelho, estado, rotulo):
    limite = time.time() + 10
    while time.time() < limite:
        if aparelho.pagina.locator(f'#tm-estado-camera[data-estado="{estado}"]').count():
            return
        aparelho.pagina.wait_for_timeout(150)
    raise AssertionError(f"{rotulo}: a câmera nunca ficou {estado} — "
                         f"{aparelho.texto('#tm-estado-camera')!r}")


def _conferir_medidas(aparelho, rotulo, aba):
    pagina = aparelho.pagina
    estouro = pagina.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert_eq(estouro, 0, f"{rotulo}/{aba}: conteúdo vaza na horizontal ({estouro} px)")
    sobra = pagina.evaluate(
        """() => { const rodape = document.querySelector('.tm-rodape');
                   const caixa = rodape.getBoundingClientRect();
                   return Math.round(window.innerHeight - caixa.bottom); }""")
    assert sobra >= 0, f"{rotulo}/{aba}: o rodapé caiu fora da tela ({sobra} px)"
    assert "Apresente sua carteirinha" in pagina.text_content("#tm-convite"), \
        f"{rotulo}/{aba}: faltou o convite"
    assert "Shambala" in pagina.text_content(".tm-centro"), \
        f"{rotulo}: falta a identidade no cabeçalho"
    estrela = pagina.locator(".tm-estrela").bounding_box()
    assert estrela and estrela["width"] >= 24, f"{rotulo}: estrela minúscula"

    alvo = "#tm-confirmar" if aba == "teclado" else "#tm-ir-teclado"
    caixa = pagina.locator(alvo).bounding_box()
    assert caixa and caixa["height"] >= 44, \
        f"{rotulo}/{aba}: {alvo} com alvo tátil menor que 44 px ({caixa and caixa['height']})"
    assert caixa["y"] + caixa["height"] <= pagina.evaluate("window.innerHeight") + 1, \
        f"{rotulo}/{aba}: {alvo} abaixo da dobra"

    aba_aberta = "#tm-teclado" if aba == "teclado" else "#tm-leitor"
    assert pagina.locator(aba_aberta).is_visible(), f"{rotulo}: a aba {aba} não abriu"
    if aba == "teclado":
        assert pagina.locator("#tm-posicoes").is_visible(), f"{rotulo}: sumiram as posições"
        assert pagina.locator("#tm-voltar-camera").is_visible(), f"{rotulo}: sumiu voltar à câmera"
    else:
        assert pagina.locator("#tm-estado-camera").is_visible(), \
            f"{rotulo}: sumiu o estado da câmera"


# ══════════════════════════════════════════════════════════════════════════
#  O que a tela nunca mostra
# ══════════════════════════════════════════════════════════════════════════

@cenario("L23 a tela não tem navegação geral nem dado de cadastro")
def l23(pasta):
    video = _video(pasta, "discrecao", [("0047", 2.0)])
    with _aparelho(video=video) as aparelho:
        aparelho.abrir()
        _mensagem(aparelho, "Presença registrada")
        tela = aparelho.pagina.content()
        for vedado in ("/menu", 'href="/"', 'href="/menu"', "Sair", "mensalidade",
                       "fiado", "CPF", "111.222", "98888"):
            assert vedado not in tela, f"a tela trouxe {vedado!r}"
        assert "Trabalhadora Zero Inicial" in tela, "a confirmação perdeu o nome"
        assert "Apresente sua carteirinha" in tela
        links = aparelho.pagina.evaluate(
            "() => [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))")
        assert_eq(links, [], "a tela de leitura tem links de navegação")


# ══════════════════════════════════════════════════════════════════════════
#  O endereço real do DEV: HTTP na LAN, onde o navegador NÃO libera a câmera
# ══════════════════════════════════════════════════════════════════════════

PORTA_INSEGURA = 9013


def _endereco_lan():
    """Endereço roteável da máquina, sem enviar tráfego (conexão UDP conectada)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(1.5)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return ""


@cenario("L24 endereço HTTP da LAN (o do DEV): câmera barrada pelo navegador, teclado registra")
def l24(pasta):
    endereco = _endereco_lan()
    if not endereco:
        raise AssertionError("sem endereço de LAN nesta máquina — não é possível reproduzir o caso")
    origem = f"http://{endereco}:{PORTA_INSEGURA}"
    log = "/tmp/shambala_test_app_inseguro.log"

    arquivo_env = os.environ.get("SHAMBALA_ENV_FILE") or os.path.join(RAIZ, ".env.test")
    ambiente = {**os.environ, "SHAMBALA_ENV_FILE": arquivo_env,
                "SHAMBALA_MODO_TESTE": "1", "PYTHONPATH": RAIZ}
    processo = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", endereco,
         "--port", str(PORTA_INSEGURA)],
        cwd=RAIZ, env=ambiente, stdout=open(log, "wb"), stderr=subprocess.STDOUT)
    try:
        _aguardar_app(origem, processo)
        # TASK_010/REV-01: prova do destino EFETIVO deste sub-processo, pelo log
        # dele e com o token da execução corrente (herdado do runner).
        from trava_isolamento import provar_destino_do_servidor, AmbienteInseguro
        try:
            provar_destino_do_servidor(BANCO, log=log)
        except AmbienteInseguro as e:
            raise AssertionError(f"sub-app da LAN sem destino provado: {e}")
        frasco = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(frasco))
        corpo = urllib.parse.urlencode({"nome_usuario": "admin", "senha": "teste123"}).encode()
        try:
            opener.open(urllib.request.Request(origem + "/login", data=corpo, method="POST"))
        except urllib.error.HTTPError:
            pass
        try:
            opener.open(urllib.request.Request(origem + "/terminal/ativar", data=b"", method="POST"))
        except urllib.error.HTTPError as e:
            assert_eq(e.code, 303, "ativação no endereço da LAN")

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.environ["SHAMBALA_CHROMIUM"],
                args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
            contexto = browser.new_context(viewport={"width": 412, "height": 915})
            # Sem grant_permissions de propósito: o navegador recusa conceder
            # câmera a uma origem insegura — é o bloqueio real que se reproduz.
            for cookie in frasco:
                if cookie.name == "sessao_terminal":
                    contexto.add_cookies([{"name": cookie.name, "value": cookie.value,
                                           "domain": endereco, "path": "/"}])
            pagina = contexto.new_page()
            try:
                pagina.goto(origem + "/terminal/")
                assert_eq(pagina.evaluate("() => window.isSecureContext"), False,
                          "o endereço da LAN deveria ser um contexto inseguro")
                limite = time.time() + 10
                while time.time() < limite and not pagina.locator(
                        '#tm-estado-camera[data-estado="indisponivel"]').count():
                    pagina.wait_for_timeout(150)
                aviso = pagina.locator("#tm-msg-bloqueio").inner_text()
                assert "HTTPS" in aviso and "teclado" in aviso.lower(), \
                    f"o aviso de contexto inseguro não orienta o teclado: {aviso!r}"
                _evidencia_em(pagina, "erro-contextoinseguro-smartphone")

                # A alternativa funciona de verdade: digitar registra no servidor.
                pagina.click("#tm-ir-teclado")
                for digito in "0048":
                    pagina.keyboard.press(digito)
                pagina.keyboard.press("Enter")
                limite = time.time() + 15
                while time.time() < limite and not pagina.locator("#tm-resultado").is_visible():
                    pagina.wait_for_timeout(150)
                assert "Presença registrada" in pagina.locator(
                    "#tm-resultado-mensagem").inner_text(), "o teclado não registrou no HTTP da LAN"
                assert "Trabalhador Seguido" in pagina.locator("#tm-resultado-nome").inner_text()
                with conectar() as conn:
                    linha = _presencas(conn, _id_por_nome(conn, "Trabalhador Seguido"))[0]
                assert_eq(linha["origem_chegada"], "terminal_codigo",
                          "origem gravada pelo teclado no endereço da LAN")

                # E a sessão de terminal não abre nada do cadastro, nesse endereço também.
                resposta = pagina.request.get(origem + "/cadastros/trabalhadores",
                                              max_redirects=0)
                assert_eq(resposta.status, 303, "o terminal abriu o cadastro na LAN")
                assert "/login" in resposta.headers.get("location", ""), \
                    f"o cadastro desviou para outro lugar: {resposta.headers.get('location')}"
            finally:
                browser.close()
    finally:
        processo.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            processo.wait(timeout=15)
        if processo.poll() is None:
            processo.kill()


def _aguardar_app(origem, processo, segundos=90):
    limite = time.time() + segundos
    while time.time() < limite:
        if processo.poll() is not None:
            raise AssertionError("o app descartável do endereço da LAN morreu ao subir")
        try:
            with urllib.request.urlopen(origem + "/login", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise AssertionError("o app descartável do endereço da LAN não respondeu a tempo")

def _preparar_cenas():
    """Nomes fictícios, códigos controlados, foto de evidência descartável."""
    with conectar() as conn:
        exigir_ambiente_de_teste(conn)
        _limpa_negocio(conn)
        for nome, codigo, ativo in [
            ("Trabalhadora Zero Inicial", "0047", 1),
            ("Trabalhador Seguido", "0048", 1),
            ("Trabalhador Preterido", "0049", 1),
            ("Trabalhador Inativo na Porta", "0050", 0),
            ("Trabalhadora Com Foto", "0051", 1),
            ("Trabalhador Zeros", "0000", 1),
        ]:
            tid = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo) VALUES (%s, %s) "
                "RETURNING id", (nome, ativo)).fetchone()["id"]
            _forcar_codigo(conn, tid, codigo)
        preterido = _id_por_nome(conn, "Trabalhador Preterido")
        trocar_codigo(conn, preterido, origem="teste")     # o 0049 vira retirado
        com_foto = _id_por_nome(conn, "Trabalhadora Com Foto")
        conn.execute("UPDATE trabalhadores SET foto_trabalhador = %s WHERE id = %s",
                     (FOTO_EVIDENCIA, com_foto))
        conn.commit()
    os.makedirs(FOTOS, exist_ok=True)
    _quadro("0051").save(os.path.join(FOTOS, FOTO_EVIDENCIA), "JPEG", quality=70)


def _limpar_cenas():
    with contextlib.suppress(OSError):
        os.remove(os.path.join(FOTOS, FOTO_EVIDENCIA))


def _id_por_nome(conn, nome):
    return conn.execute("SELECT id FROM trabalhadores WHERE nome_completo = %s",
                        (nome,)).fetchone()["id"]


# ══════════════════════════════════════════════════════════════════════════
#  Runner
# ══════════════════════════════════════════════════════════════════════════

def main():
    faltam = _ferramentas_ausentes()
    if faltam:
        print("\n[ABORTADO] ferramentas ausentes para o teste do leitor no navegador:")
        for item in faltam:
            print(f"    - {item}")
        print("  nada foi executado — ausência de ferramenta não é sucesso.")
        return 3

    os.makedirs(EVIDENCIAS, exist_ok=True)
    pasta = "/tmp/shambala-terminal-leitor"
    shutil.rmtree(pasta, ignore_errors=True)
    os.makedirs(pasta)

    preparar_banco()
    _garantir_admin()

    total = ok = 0
    falhas = []
    for nome, fn in CENARIOS:
        _preparar_cenas()
        total += 1
        try:
            fn(pasta)
            ok += 1
            print(f"  PASS  {nome}")
        except Exception as e:  # noqa: BLE001
            falhas.append(nome)
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
        _limpar_cenas()

    print(f"\n{ok}/{total} testes passaram (TASK_003 terminal — leitor no navegador)")
    print(f"  evidências: {EVIDENCIAS}")
    shutil.rmtree(pasta, ignore_errors=True)
    limpar_banco()
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
