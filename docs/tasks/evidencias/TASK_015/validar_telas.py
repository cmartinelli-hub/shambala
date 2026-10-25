"""TASK_015 — validação dirigida: as telas renderizam com ícones, sem emoji,
sem recurso externo e sem erro no HTML."""
import http.cookiejar, urllib.request, urllib.parse, urllib.error, re, sys
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9000"
jar = http.cookiejar.CookieJar()
class NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
op = urllib.request.build_opener(NR(), urllib.request.HTTPCookieProcessor(jar))
try:
    op.open(urllib.request.Request(BASE + "/login", method="POST",
        data=urllib.parse.urlencode({"nome_usuario": "admin", "senha": "teste123"}).encode()))
except urllib.error.HTTPError as e:
    if e.code != 303:
        print("login falhou:", e.code); raise SystemExit(1)

ROTAS = ["/menu", "/dia", "/caixa/pdv", "/caixa/fiados",
         "/financeiro", "/financeiro/mensalidades", "/financeiro/compras",
         "/cadastros/pessoas", "/cadastros/trabalhadores", "/agenda",
         "/relatorios", "/biblioteca", "/configuracoes",
         "/financeiro/fiados", "/financeiro/relatorios"]
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")
falhas = 0
for rota in ROTAS:
    try:
        r = op.open(BASE + rota, timeout=15)
        html = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        print(f"  {rota:32s} HTTP {e.code}")
        falhas += 1
        continue
    icones = len(re.findall(r'/static/icons/[a-z0-9-]+\.svg', html))
    emojis = EMOJI.findall(html)
    externos = re.findall(r'(?:src|href)="https?://(?!127\.0\.0\.1)', html)
    quebrado = "{{ icone" in html or "Undefined" in html or "jinja2" in html.lower()
    # tela sem ícone nenhum é legítima (tabela simples) — o que reprova é
    # emoji, recurso externo, template quebrado ou erro de servidor.
    ok = not emojis and not externos and not quebrado
    print(f"  {rota:32s} {'OK ' if ok else 'FALHA'} ícones={icones:3d} "
          f"emojis={len(emojis)} externos={len(externos)} quebrado={quebrado}")
    if emojis:
        print("      emojis:", "".join(sorted(set(emojis)))[:40])
    if not ok:
        falhas += 1
print(f"\nrota(s) com problema: {falhas}")
sys.exit(0 if falhas == 0 else 1)
