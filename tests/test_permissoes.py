#!/usr/bin/env python3
"""Suíte de testes da APLICAÇÃO REAL das permissões por grupo (2026-10-21).

Correção de segurança: os _guard() dos módulos passaram a cobrar
usuario_pode() — negação por padrão para não-admin (Admin mantém bypass).
Cobertura: bloqueio por módulo, auxiliares que não exigiam login, redireção
para /login quando não logado, menu filtrado e bypass do admin.

Roda contra o banco descartável de teste (trava em test_tres_passes).

Uso (recomendado):
    bash tests/rodar_testes.sh
"""
import sys
import traceback

# A importação abaixo executa a TRAVA DE ISOLAMENTO na ordem obrigatória
# (configurar → validar → importar a aplicação) — ver test_tres_passes.
import test_tres_passes as tp
from test_tres_passes import (
    Ctx, assert_eq, preparar_banco, limpar_banco,
    _http, _login, _garantir_admin,
)

conectar = tp.conectar
BASE = tp.BASE

RESULTADOS = []


def teste(nome):
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


# ── Apoio: sessões HTTP independentes (login por usuário de teste) ───────────

def _nova_sessao(usuario, senha):
    """Opener com cookie jar próprio, logado como `usuario`. Não segue redirect."""
    cj = tp.http.cookiejar.CookieJar()
    opener = tp.urllib.request.build_opener(
        tp.urllib.request.HTTPCookieProcessor(cj), tp._SemRedirect()
    )
    body = tp.urllib.parse.urlencode({"nome_usuario": usuario, "senha": senha}).encode()
    req = tp.urllib.request.Request(BASE + "/login", data=body, method="POST")
    try:
        opener.open(req)
        raise AssertionError(f"login de {usuario} deveria redirecionar (303)")
    except tp.urllib.error.HTTPError as e:
        if e.code != 303:
            raise AssertionError(f"login de {usuario} falhou (status {e.code})")

    def get(path):
        req = tp.urllib.request.Request(BASE + path, method="GET")
        try:
            with opener.open(req) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except tp.urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def post(path, data=None):
        body = tp.urllib.parse.urlencode(data).encode() if data else b""
        req = tp.urllib.request.Request(BASE + path, data=body, method="POST")
        try:
            with opener.open(req) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except tp.urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    get.post = post
    return get


def _grupo(ctx, nome):
    ctx.conn.execute(
        "INSERT INTO grupos (nome, descricao) VALUES (%s, 'teste de permissões') "
        "ON CONFLICT (nome) DO NOTHING", (nome,)
    )
    return ctx.conn.execute(
        "SELECT id FROM grupos WHERE nome = %s", (nome,)
    ).fetchone()["id"]


def _perm(ctx, grupo_id, modulo, ler=True, escrever=False, apagar=False):
    ctx.conn.execute(
        """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (grupo_id, modulo) DO UPDATE
           SET ler = EXCLUDED.ler, escrever = EXCLUDED.escrever, apagar = EXCLUDED.apagar""",
        (grupo_id, modulo, ler, escrever, apagar)
    )


def _usuario_em_grupo(ctx, nome_usuario, grupo_id):
    from rotas.auth import hash_senha
    row = ctx.conn.execute(
        "SELECT id FROM atendentes WHERE nome_usuario = %s", (nome_usuario,)
    ).fetchone()
    uid = row["id"] if row else ctx.conn.execute(
        """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
           VALUES (%s, %s, %s, 1) RETURNING id""",
        (nome_usuario, nome_usuario.title(), hash_senha("teste123"))
    ).fetchone()["id"]
    ctx.conn.execute(
        "INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING", (uid, grupo_id)
    )
    return uid


BLOQUEADOS = ["/financeiro", "/relatorios", "/doacoes", "/biblioteca",
              "/mala-direta", "/caixa/pdv", "/checkin-trabalhador",
              "/cadastros/pessoas"]


# ── Cobrança por módulo ───────────────────────────────────────────────────────

@teste("P1 grupo com apenas agenda: entra em /agenda, 403 em todos os demais módulos")
def p1(ctx: Ctx):
    g = _grupo(ctx, "Só Agenda")
    _perm(ctx, g, "agenda")
    _usuario_em_grupo(ctx, "agendista", g)
    ctx.conn.commit()
    get = _nova_sessao("agendista", "teste123")
    status, _ = get("/agenda")
    assert_eq(status, 200, "GET /agenda com permissão")
    for path in BLOQUEADOS:
        status, _ = get(path)
        assert_eq(status, 403, f"GET {path} sem permissão")


@teste("P2 auxiliares que não exigiam nada agora exigem o módulo")
def p2(ctx: Ctx):
    g = _grupo(ctx, "Só Agenda")
    _perm(ctx, g, "agenda")
    _usuario_em_grupo(ctx, "agendista", g)
    ctx.conn.commit()
    status, _ = _nova_sessao("agendista", "teste123")(
        "/cadastros/pessoas/api/similares?q=ma")
    assert_eq(status, 403, "api/similares sem permissão de pessoas")
    status, _ = _nova_sessao("agendista", "teste123")("/biblioteca/isbn/9788550900019")
    assert_eq(status, 403, "isbn sem permissão de biblioteca")


@teste("P3 não logado cai em /login (303), não em 403")
def p3(ctx: Ctx):
    opener = tp.urllib.request.build_opener(tp._SemRedirect())
    req = tp.urllib.request.Request(BASE + "/financeiro", method="GET")
    try:
        with opener.open(req) as r:
            status = r.status
    except tp.urllib.error.HTTPError as e:
        status = e.code
    assert_eq(status, 303, "sem sessão deve redirecionar para /login")


@teste("P4 admin mantém bypass em todos os módulos")
def p4(ctx: Ctx):
    # sessão global da harness já está logada como admin (main fez _login)
    for path in ["/financeiro", "/relatorios", "/caixa/pdv", "/doacoes",
                 "/biblioteca", "/mala-direta", "/checkin-trabalhador",
                 "/cadastros/pessoas"]:
        status, _ = _http("GET", path)
        assert_eq(status, 200, f"admin em {path}")


@teste("P7 POST cobra 'escrever': somente-leitura navega mas não grava")
def p7(ctx: Ctx):
    g = _grupo(ctx, "Leitura Pessoas")
    _perm(ctx, g, "cadastros.pessoas", ler=True, escrever=False)
    _usuario_em_grupo(ctx, "leitora", g)
    ctx.conn.commit()
    sessao = _nova_sessao("leitora", "teste123")
    status, _ = sessao("/cadastros/pessoas")
    assert_eq(status, 200, "GET com leitura")
    status, _ = sessao.post("/cadastros/pessoas/1/planos/1/alta")
    assert_eq(status, 403, "POST sem escrever")


@teste("P8 grupo com escrever: POST passa pelo guard")
def p8(ctx: Ctx):
    g = _grupo(ctx, "Edição Pessoas")
    _perm(ctx, g, "cadastros.pessoas", ler=True, escrever=True)
    _usuario_em_grupo(ctx, "editora", g)
    ctx.conn.commit()
    # alta de plano inexistente: após o guard a rota só redireciona (303)
    status, _ = _nova_sessao("editora", "teste123").post(
        "/cadastros/pessoas/1/planos/999999/alta")
    assert_eq(status, 303, "POST com escrever chega à rota")


# ── Menu filtrado ─────────────────────────────────────────────────────────────

@teste("P5 menu: usuário restrito vê só Agenda; admin vê tudo")
def p5(ctx: Ctx):
    g = _grupo(ctx, "Só Agenda")
    _perm(ctx, g, "agenda")
    _usuario_em_grupo(ctx, "agendista", g)
    ctx.conn.commit()
    _, html_restrito = _nova_sessao("agendista", "teste123")("/menu")
    assert 'href="/agenda"' in html_restrito, "card Agenda deveria aparecer"
    for link in ['href="/financeiro"', 'href="/caixa/pdv"',
                 'href="/checkin-trabalhador"', 'href="/cadastros/pessoas"',
                 'href="/relatorios"', 'href="/dia"']:
        assert link not in html_restrito, f"card {link} não deveria aparecer"
    _, html_admin = _http("GET", "/menu")
    for link in ['href="/financeiro"', 'href="/caixa/pdv"',
                 'href="/checkin-trabalhador"']:
        assert link in html_admin, f"admin deveria ver {link}"


@teste("P6 pessoas com leitura do módulo entram; escrita não é exigida pelo guard")
def p6(ctx: Ctx):
    g = _grupo(ctx, "Recepção Teste")
    _perm(ctx, g, "cadastros.pessoas")
    _usuario_em_grupo(ctx, "recep", g)
    ctx.conn.commit()
    status, _ = _nova_sessao("recep", "teste123")("/cadastros/pessoas")
    assert_eq(status, 200, "leitura de Pessoas basta para o guard")


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
    print(f"\n{ok}/{total} testes passaram (permissões)")
    limpar_banco()
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
