#!/usr/bin/env python3
"""Suíte TASK_011 — contrato de consultas da Visão Geral do Financeiro.

Prova o COMPORTAMENTO OBSERVADO das consultas da tela (cartões, extrato,
pendências, filtros, paginação, RBAC) contra a aplicação real em banco
descartável — sem navegador (a experiência visual é a outra suíte).

Cenários (ordem TASK_011 §5):
  C1 dinheiro x obrigação x movimento interno: par de transferência e par de
     sangria não entram nos cartões globais mas aparecem no extrato da conta;
     recebimentos/pagamentos/resultado/a receber conferidos à mão.
  C2 registro histórico ambíguo (pago em conta de obrigações): preservado no
     banco, rotulado na lista, excluído do dinheiro.
  C3 cancelado listado sem descontar duas vezes; mensalidade e fiado com a
     MESMA descrição somadas cada uma uma vez; evento ligado ao lançamento não
     é somado por cima.
  C4 recusas de filtro: data inválida, intervalo invertido, conta inexistente,
     busca com curingas/acentos preservando valores.
  C5 paginação: 30 registros, ordem determinística, página fora do intervalo
     ajustada sem perder filtros, totais iguais entre páginas, teto de `por`.
  C6 RBAC e leitura sem efeitos: anônimo vai ao login; sem permissão é 403 em
     todas as rotas novas (inclusive URL direta e a busca de mensalidades);
     GETs não criam nem alteram fatos.

Dados fictícios com prefixo T011C; o destino do script E o do servidor HTTP
são provados antes de qualquer escrita (contrato TASK_010).
"""
import os
import re
import sys
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
PREFIXO = "T011C"
DE, ATE = "2026-03-01", "2026-03-31"


def abortar(msg):
    print(f"\n[ABORTADO] {msg}", file=sys.stderr)
    sys.exit(2)


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
print(f"[ok] conexão do script: {info['banco_efetivo']}")

try:
    prova = provar_destino_do_servidor(info["banco_efetivo"])
except AmbienteInseguro as e:
    abortar(str(e))
print(f"[ok] servidor HTTP ({BASE}) na execução corrente, banco {prova['banco_efetivo']}")

D = Decimal


class SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, code, *a, **k):
        return None


def novo_opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(SemRedirect, urllib.request.HTTPCookieProcessor(jar))


def cookie_sessao(usuario, senha):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), SemRedirect())
    body = urllib.parse.urlencode({"nome_usuario": usuario, "senha": senha}).encode()
    try:
        op.open(urllib.request.Request(BASE + "/login", data=body, method="POST"))
        raise AssertionError("o login deveria redirecionar")
    except urllib.error.HTTPError as e:
        if e.code != 303:
            raise AssertionError(f"login falhou ({e.code}) para {usuario}")
    for c in cj:
        if c.name == "sessao":
            return c.value
    raise AssertionError("login sem cookie de sessão")


OP = novo_opener()
OP.addheaders = []


def get(path, opener=None):
    op = opener or OP
    try:
        with op.open(urllib.request.Request(BASE + path)) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.headers.get("location") or ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("location") or ""


def escalar(sql, params=(), padrao=None):
    with conectar() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return padrao
    v = list(row.values())[0]
    return padrao if v is None else v


def executar(sql, params=()):
    with conectar() as conn:
        conn.execute(sql)


CENARIOS = []
aprovadas, falhas = [], []


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


def cartoes(html):
    """Os 4 cartões do topo, na ordem: recebimentos, pagamentos, resultado,
    a receber. Valores moeda → Decimal."""
    raw = re.findall(r'<div class="stat-valor[^"]*">(.*?)</div>', html)
    out = []
    for r in raw[:4]:
        m = re.search(r"R\$\s*([\d.]+),(\d\d)", r)
        out.append(Decimal(m.group(1).replace(".", "") + "." + m.group(2)) if m else None)
    return out


# ── Fixtures ─────────────────────────────────────────────────────────────────
FIX = {}


def semeadura():
    criar_tabelas()
    from rotas.auth import hash_senha
    with conectar() as conn:
        row = conn.execute("SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()
        if row:
            conn.execute("UPDATE atendentes SET senha_hash=%s, ativo=1 WHERE id=%s",
                         (hash_senha("teste123"), row["id"]))
        else:
            conn.execute("INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo) "
                         "VALUES ('admin','Administrador',%s,1)", (hash_senha("teste123"),))
        conn.execute("INSERT INTO grupos (id, nome) VALUES (1,'Admin') ON CONFLICT (id) DO NOTHING")
        aid = conn.execute("SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()["id"]
        conn.execute("INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s,1) "
                     "ON CONFLICT DO NOTHING", (aid,))

        FIX["cid_esp"] = conta_id_por_tipo(conn, "especie")
        FIX["cid_pix"] = conta_id_por_tipo(conn, "pix")
        FIX["cid_ar"] = conn.execute(
            "SELECT id FROM contas_financeiras WHERE tipo='areceber' AND ativo=1"
        ).fetchone()["id"]

        # conta de obrigações e dinheiro têm naturezas diferentes por construção
        def fm(tipo, cat, valor, data, desc, status, conta, **extra):
            cols = ["tipo", "categoria", "valor", "data_movimentacao", "descricao",
                    "status", "conta_id"] + list(extra)
            vals = [tipo, cat, D(valor), data, desc, status, conta] + list(extra.values())
            return conn.execute(
                f"INSERT INTO financeiro_movimentacoes ({','.join(cols)}) "
                f"VALUES ({','.join(['%s'] * len(cols))}) RETURNING id",
                vals).fetchone()["id"]

        conn.execute("DELETE FROM caixa_sessao_eventos WHERE identidade LIKE %s", (f"{PREFIXO}:%",))
        for tab, col in (("financeiro_movimentacoes", "descricao"), ("vendas_pdv", "observacao")):
            ids = conn.execute(f"SELECT id FROM {tab} WHERE {col} LIKE %s", (f"{PREFIXO}%",)).fetchall()
            for r in ids:
                conn.execute(f"DELETE FROM {tab} WHERE id=%s", (r["id"],))
        conn.execute("DELETE FROM caixa_sangrias WHERE motivo LIKE %s", (f"{PREFIXO}%",))
        conn.execute("DELETE FROM caixa_movimentos WHERE observacao_fechamento LIKE %s",
                     (f"{PREFIXO}%",))
        conn.execute("DELETE FROM caixas WHERE nome LIKE %s", (f"{PREFIXO}%",))
        conn.execute("DELETE FROM trabalhadores WHERE nome_completo LIKE %s", (f"{PREFIXO}%",))

        FIX["t_devedor"] = conn.execute(
            "INSERT INTO trabalhadores (nome_completo, ativo) VALUES (%s,1) RETURNING id",
            (f"{PREFIXO} Devedor",)).fetchone()["id"]
        cx = conn.execute("INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
                          "VALUES (%s,'fixture TASK_011',FALSE,1) RETURNING id",
                          (f"{PREFIXO} Caixa",)).fetchone()["id"]
        FIX["cx"] = cx
        sessao = conn.execute(
            "INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status, "
            "observacao_fechamento) VALUES (%s, 0, 'fechado', %s) RETURNING id",
            (cx, f"{PREFIXO} sessão fixture")).fetchone()["id"]
        FIX["sessao"] = sessao
        sangria = conn.execute(
            "INSERT INTO caixa_sangrias (caixa_movimento_id, valor, motivo, recebedor_nome) "
            "VALUES (%s, 40, %s, %s) RETURNING id",
            (sessao, f"{PREFIXO} sangria", "destino fictício")).fetchone()["id"]
        FIX["sangria"] = sangria

        FIX["e1"] = fm("entrada", "venda_pdv", "100.00", "2026-03-05", f"{PREFIXO} venda", "pago", FIX["cid_esp"])
        FIX["e2"] = fm("entrada", "doacao", "50.00", "2026-03-06", f"{PREFIXO} doação", "pago", FIX["cid_pix"])
        FIX["e3"] = fm("entrada", "recebimento_fiado", "70.00", "2026-03-12", f"{PREFIXO} gêmeos", "pago", FIX["cid_esp"])
        FIX["e4"] = fm("entrada", "outros", "2.00", "2026-03-13", f"{PREFIXO} café com leite", "pago", FIX["cid_esp"])
        FIX["s1"] = fm("saida", "compra", "30.00", "2026-03-07", f"{PREFIXO} compra", "pago", FIX["cid_esp"])
        FIX["tr_s"] = fm("saida", "transferencia", "200.00", "2026-03-10", f"{PREFIXO} depósito (origem)", "pago", FIX["cid_esp"])
        FIX["tr_e"] = fm("entrada", "transferencia", "200.00", "2026-03-10", f"{PREFIXO} depósito (destino)", "pago", FIX["cid_pix"])
        FIX["sg_s"] = fm("saida", "sangria", "40.00", "2026-03-11", f"{PREFIXO} sangria", "pago", FIX["cid_esp"],
                         sangria_id=sangria)
        FIX["sg_e"] = fm("entrada", "transferencia", "40.00", "2026-03-11", f"{PREFIXO} contrapartida sangria",
                         "pago", FIX["cid_pix"], sangria_id=sangria)
        FIX["ar"] = fm("entrada", "mensalidade", "60.00", "2026-03-02", f"{PREFIXO} recibo antigo", "pago", FIX["cid_ar"])
        FIX["p1"] = fm("entrada", "mensalidade", "60.00", "2026-03-15", f"{PREFIXO} gêmeos", "pendente", FIX["cid_ar"])
        FIX["p2"] = fm("entrada", "mensalidade", "60.00", "2026-04-15", f"{PREFIXO} fora da janela", "pendente", FIX["cid_ar"])
        FIX["p3"] = fm("saida", "manutencao", "25.00", "2026-03-20", f"{PREFIXO} conserto", "pendente", FIX["cid_esp"])
        FIX["x1"] = fm("entrada", "doacao", "10.00", "2026-03-08", f"{PREFIXO} doação cancelada", "cancelado", FIX["cid_esp"])
        FIX["fev"] = fm("entrada", "venda_pdv", "999.00", "2026-02-05", f"{PREFIXO} fevereiro", "pago", FIX["cid_esp"])

        # evento que aponta para um lançamento: a tela NUNCA soma isso por cima
        conn.execute(
            """INSERT INTO caixa_sessao_eventos
               (identidade, sessao_id, tipo, natureza, origem_tipo, origem_id,
                financeiro_id, forma, sentido, valor, efeito_fisico, conta_id)
               VALUES (%s,%s,'venda','comercial','financeiro_movimentacoes',%s,%s,
                       'especie','entrada',100,100,%s)""",
            (f"{PREFIXO}:ev1", sessao, FIX["e1"], FIX["e1"], FIX["cid_esp"]))

        conn.execute(
            """INSERT INTO vendas_pdv (caixa_id, data_venda, total, forma_pagamento,
                                       status, fiado_trabalhador_id, fiado_pago, observacao)
               VALUES (%s,'2026-03-09',45,'fiado','concluida',%s,FALSE,%s)""",
            (cx, FIX["t_devedor"], f"{PREFIXO} fiado aberto"))

        # janela da paginação (2026-05) — fora de TODAS as somas de C1..C4
        for i in range(30):
            fm("entrada", "outros", "1.00", "2026-05-10", f"{PREFIXO} pg{i:02d}", "pago", FIX["cid_esp"])

        # atendentes para RBAC
        conn.execute("INSERT INTO grupos (id, nome) VALUES (7711,'T011C Leitura') ON CONFLICT (id) DO NOTHING")
        conn.execute("INSERT INTO grupos (id, nome) VALUES (7712,'T011C Sem Financeiro') ON CONFLICT (id) DO NOTHING")
        conn.execute("""INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
                        VALUES (7711,'financeiro',TRUE,FALSE,FALSE)
                        ON CONFLICT (grupo_id, modulo) DO UPDATE SET ler=TRUE, escrever=FALSE""")
        for nome, grupo in (("t011c_leitor", 7711), ("t011c_negado", 7712)):
            r = conn.execute("SELECT id FROM atendentes WHERE nome_usuario=%s", (nome,)).fetchone()
            if r:
                uid = r["id"]
                conn.execute("UPDATE atendentes SET senha_hash=%s, ativo=1 WHERE id=%s",
                             (hash_senha("teste123"), uid))
            else:
                uid = conn.execute("INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo) "
                                   "VALUES (%s,%s,%s,1) RETURNING id",
                                   (nome, nome, hash_senha("teste123"))).fetchone()["id"]
            conn.execute("INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s,%s) "
                         "ON CONFLICT DO NOTHING", (uid, grupo))
            FIX[nome] = uid

        # cartões de 2026-03 conferidos à mão (ver docstring do contrato):
        FIX["receb"] = D("222.00")   # e1+e2+e3+e4 — sem transferência/sangria/areceber/cancelado
        FIX["pag"] = D("30.00")      # s1 — sem saída de transferência nem sangria
        FIX["resultado"] = D("192.00")
        FIX["a_receber"] = D("60.00")  # p1 (p2 vence em abril)
        FIX["a_pagar"] = D("25.00")    # p3


semeadura()
COOKIE_ADMIN = cookie_sessao("admin", "teste123")
COOKIE_LEITOR = cookie_sessao("t011c_leitor", "teste123")
COOKIE_NEGADO = cookie_sessao("t011c_negado", "teste123")


def com_cookie(valor):
    op = novo_opener()
    op.addheaders = [("Cookie", f"sessao={valor}")]
    return op


def tela(path, cookie=COOKIE_ADMIN):
    st, corpo, loc = get(path, com_cookie(cookie))
    return st, corpo, loc


# ═════════════════════════════════════════════════════════════════════════════
@cenario("C1 dinheiro x obrigação x movimento interno nos cartões e no extrato")
def c1():
    st, h, _ = tela(f"/financeiro?de={DE}&ate={ATE}")
    verificar("C1 tela 200", st == 200, str(st))
    c = cartoes(h)
    verificar("C1 Recebimentos exclui par de transferência, par de sangria e conta de obrigações",
              c[0] == FIX["receb"], f"{c[0]} != {FIX['receb']}")
    verificar("C1 Pagamentos exclui saídas de transferência/sangria e pendentes",
              c[1] == FIX["pag"], f"{c[1]} != {FIX['pag']}")
    verificar("C1 Resultado = Recebimentos − Pagamentos", c[2] == FIX["resultado"], f"{c[2]}")
    verificar("C1 A receber = pendentes de entrada com vencimento na janela",
              c[3] == FIX["a_receber"], f"{c[3]}")
    verificar("C1 A pagar dito no cartão sem misturar com A receber",
              "R$ 25,00 a pagar" in h)
    verificar("C1 resultado não é chamado de saldo disponível",
              "Não é saldo" in h and "saldo disponível:" not in h)

    st, hp, _ = tela(f"/financeiro?aba=pend&de={DE}&ate={ATE}")
    verificar("C1 aba de pendências separada do extrato", st == 200 and "A receber e a pagar" in hp)
    verificar("C1 fiado em aberto listado como grupo próprio", "Fiado em aberto" in hp)
    verificar("C1 fiado fora do indicador A receber",
              "fora do indicador" in hp and f"{PREFIXO} Devedor" in hp)

    pix = FIX["cid_pix"]
    st, hpx, _ = tela(f"/financeiro?de={DE}&ate={ATE}&conta={pix}")
    cpx = cartoes(hpx)
    verificar("C1 filtro de conta PIX: recebimentos só do PIX (transferência entra na conta, não no cartão)",
              cpx[0] == D("50.00"), f"{cpx[0]}")
    verificar("C1 extrato da conta PIX mostra a transferência com natureza Transferência",
              f"{PREFIXO} depósito (destino)" in hpx and "Transferência" in hpx)
    esp = FIX["cid_esp"]
    st, hesp, _ = tela(f"/financeiro?de={DE}&ate={ATE}&conta={esp}")
    verificar("C1 extrato da conta espécie mostra sangria pela natureza",
              "Sangria de caixa" in hesp)
    verificar("C1 contrapartida de sangria identificada pelo vínculo sangria_id",
              "Transferência (contrapartida de sangria)" in h)


@cenario("C2 registro histórico pago em conta de obrigações: preservado, rotulado, fora do dinheiro")
def c2():
    n = escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE id=%s", (FIX["ar"],))
    verificar("C2 o fato armazenado permanece intacto no banco", n == 1)
    st, h, _ = tela(f"/financeiro?de={DE}&ate={ATE}")
    verificar("C2 lista rotula como registro histórico a conferir",
              "Registro histórico — conferir origem" in h)
    verificar("C2 o cartão não promoveu a obrigação a dinheiro recebido",
              cartoes(h)[0] == FIX["receb"])
    verificar("C2 aviso de limitação histórica presente",
              "Valores a Receber" in h and "excluído" in h)


@cenario("C3 cancelado, gêmeos mensalidade/fiado e evento vinculado — nada conta duas vezes")
def c3():
    st, h, _ = tela(f"/financeiro?de={DE}&ate={ATE}")
    verificar("C3 cancelado continua listado com situação própria",
              f"{PREFIXO} doação cancelada" in h and "Cancelado" in h)
    st, hc, _ = tela(f"/financeiro?de={DE}&ate={ATE}&sit=cancelado")
    verificar("C3 filtro situação isola o cancelado (1 registro)",
              "exibindo 1–1 de 1 registros" in hc, hc[hc.find("exibindo"):][:60])
    verificar("C3 com situação cancelada os cartões de dinheiro ficam em zero",
              cartoes(hc) == [D("0"), D("0"), D("0"), FIX["a_receber"]])
    verificar("C3 cancelado não desconta duas vezes do total",
              cartoes(h)[0] == FIX["receb"])

    hg = urllib.parse.quote(f"{PREFIXO} gêmeos")
    st, hgm, _ = tela(f"/financeiro?de={DE}&ate={ATE}&q={hg}")
    verificar("C3 busca por descrição idêntica traz recebimento de fiado E mensalidade pendente",
              "Recebimento de fiado" in hgm and "Mensalidade" in hgm
              and "exibindo 1–2 de 2 registros" in hgm)
    verificar("C3 recebimento de fiado somado exatamente uma vez",
              cartoes(hgm)[0] == D("70.00"), str(cartoes(hgm)))

    st, h2, _ = tela(f"/financeiro?de={DE}&ate={ATE}")
    verificar("C3 evento da sessão (financeiro_id) não infla os cartões",
              cartoes(h2)[0] == FIX["receb"] and cartoes(h2)[1] == FIX["pag"])
    st, hd, _ = tela(f"/financeiro/lancamento/{FIX['e1']}")
    verificar("C3 detalhe rastreia o evento pelo vínculo provado",
              st == 200 and f"{PREFIXO}:ev1" in hd and "sessão" in hd)


@cenario("C4 filtros recusados com orientação, preservando o valor digitado")
def c4():
    st, h, _ = tela("/financeiro?de=2026-13-99")
    verificar("C4 data impossível recusada sem 500", st == 200 and "inválida" in h)
    verificar("C4 valor inválido preservado no formulário", 'value="2026-13-99"' in h)
    st, h, _ = tela(f"/financeiro?de={ATE}&ate={DE}")
    verificar("C4 intervalo invertido recusado com orientação", st == 200 and "posterior" in h)
    st, h, _ = tela(f"/financeiro?de={DE}&ate={ATE}&conta=999999")
    verificar("C4 conta inexistente recusa sem virar 'Todas' silencioso",
              st == 200 and "não existe" in h)
    st, h, _ = tela(f"/financeiro?de={DE}&ate={ATE}&nat=nao_existe_na_tela")
    verificar("C4 natureza fora da whitelist recusada", st == 200 and "desconhecida" in h)
    st, h, _ = tela(f"/financeiro?de={DE}&ate={ATE}&por=100000")
    verificar("C4 por acima do teto é recusado com aviso (e usa 25)",
              st == 200 and "não é uma opção" in h)
    st, h, _ = tela(f"/financeiro?de={DE}&ate={ATE}&q=%25")
    verificar("C4 busca com % não vira varredura da base",
              st == 200 and "Nenhuma movimentação" in h)
    st, h, _ = tela(f"/financeiro?de={DE}&ate={ATE}&q=%5F")
    verificar("C4 busca com _ literal não casa tudo", st == 200 and "Nenhuma movimentação" in h)
    qcafe = urllib.parse.quote("café")
    st, h, _ = tela(f"/financeiro?de={DE}&ate={ATE}&q={qcafe}")
    verificar("C4 busca acentuada encontra o lançamento pelo texto",
              f"{PREFIXO} café com leite" in h)


@cenario("C5 paginação no servidor: ordem determinística, totais estáveis, página ajustada")
def c5():
    j = "de=2026-05-01&ate=2026-05-31"
    st, h1, _ = tela(f"/financeiro?{j}")
    verificar("C5 página 1 mostra 25 de 30", "exibindo 1–25 de 30 registros" in h1)
    ids1 = re.findall(r"/financeiro/lancamento/(\d+)\?volta=", h1)[:25]
    verificar("C5 ordem determinística por data e id (desc)",
              ids1 == sorted(ids1, key=int, reverse=True), "sequência de ids")
    st, h2, _ = tela(f"/financeiro?{j}&pagina=2")
    verificar("C5 página 2 mostra as 5 restantes", "exibindo 26–30 de 30 registros" in h2)
    ids2 = re.findall(r"/financeiro/lancamento/(\d+)\?volta=", h2)[:5]
    verificar("C5 nenhuma linha se repete entre páginas", not set(ids1) & set(ids2))
    verificar("C5 totais iguais nas duas páginas", cartoes(h1)[:2] == cartoes(h2)[:2] == [D("30.00"), D("0.00")],
              str(cartoes(h1)))
    st, h9, _ = tela(f"/financeiro?{j}&pagina=999")
    verificar("C5 página fora do intervalo ajustada sem erro nem perda de filtro",
              st == 200 and "exibindo 26–30 de 30" in h9 and "ate=2026-05-31" in h9)
    st, hf, _ = tela(f"/financeiro?{j}&q=pg0&por=50")
    verificar("C5 filtros preservados ao paginar/trocar por", "por=50" in hf and "q=pg0" in hf)


@cenario("C6 RBAC nas rotas novas e leituras sem efeitos colaterais")
def c6():
    st, _, loc = get("/financeiro")
    verificar("C6 anônimo vai ao login", st == 303 and "/login" in loc, f"{st} {loc}")
    for caminho in (f"/financeiro?de={DE}&ate={ATE}",
                    f"/financeiro/lancamento/{FIX['e1']}",
                    "/financeiro/mensalidades/buscar?q=T011"):
        st, _, _ = tela(caminho, COOKIE_NEGADO)
        verificar(f"C6 sem permissão financeira é 403 em {caminho.split('?')[0]}", st == 403, str(st))
    st, h, _ = tela(f"/financeiro?de={DE}&ate={ATE}", COOKIE_LEITOR)
    verificar("C6 leitura autorizada pelo grupo (não-admin) funciona", st == 200 and "Recebimentos" in h)
    st, _, _ = tela(f"/financeiro/lancamento/{FIX['e1']}", COOKIE_LEITOR)
    verificar("C6 detalhe também liberado para leitura", st == 200)
    st, _, _ = tela("/financeiro/mensalidades/buscar?q=T011", COOKIE_NEGADO)
    verificar("C6 busca de mensalidades (CPF) deixou de ser aberta a qualquer sessão", st == 403)

    antes = (escalar("SELECT COUNT(*) FROM financeiro_movimentacoes"),
             escalar("SELECT COUNT(*) FROM caixa_sessao_eventos"),
             escalar("SELECT COUNT(*) FROM vendas_pdv"),
             escalar("SELECT COUNT(*) FROM caixa_movimentos"))
    for caminho in (f"/financeiro?de={DE}&ate={ATE}",
                    f"/financeiro?aba=pend&de={DE}&ate={ATE}",
                    f"/financeiro?de={DE}&ate={ATE}&pagina=2",
                    "/financeiro?mes=2026-03",
                    f"/financeiro/lancamento/{FIX['e1']}"):
        tela(caminho)
    depois = (escalar("SELECT COUNT(*) FROM financeiro_movimentacoes"),
              escalar("SELECT COUNT(*) FROM caixa_sessao_eventos"),
              escalar("SELECT COUNT(*) FROM vendas_pdv"),
              escalar("SELECT COUNT(*) FROM caixa_movimentos"))
    verificar("C6 GETs de consulta não criaram nem alteraram fatos", antes == depois, f"{antes} -> {depois}")

    st, h, _ = tela("/financeiro?mes=2026-03")
    verificar("C6 alias legado ?mes= continua funcionando (expande para a janela)",
              st == 200 and cartoes(h)[0] == FIX["receb"])


def main():
    for nome, fn in CENARIOS:
        print(f"\n== {nome}")
        try:
            fn()
        except AssertionError as e:
            falhas.append(f"{nome} — asserção: {e}")
            print(f"  ✘ {nome} lançou asserção: {e}")
        except Exception as e:  # falha de infraestrutura é falha, nunca skip
            falhas.append(f"{nome} — exceção: {e!r}")
            print(f"  ✘ {nome} lançou {e!r}")
    print(f"\n[resultado] aprovadas={len(aprovadas)} falhas={len(falhas)}")
    for f in falhas:
        print(f"  FAIL: {f}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
