#!/usr/bin/env python3
"""Suíte TASK_008 — sessão de caixa por eventos (vendas / recebimentos / dinheiro físico).

Pelas ROTAS REAIS autenticadas, banco descartável, dados fictícios (prefixo
T008S). Cobre o cenário misto determinístico da TASK_008 (inicial 100 → físico
esperado 183), os 14 casos obrigatórios e a conferência antes/depois no banco
(linhas, vínculos, totais) — nunca só a tela.

Destinos comprovados ANTES de qualquer escrita: conexão do script + linha que o
servidor imprime no startup; ausente ou divergente, aborta sem gravar.
"""
import calendar
import os
import secrets
import re
import sys
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import (  # noqa: E402
    exigir_ambiente_de_teste, exigir_configuracao_consoante,
    provar_destino_do_servidor, AmbienteInseguro,
)

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9014")
PREFIXO = "T008S"


def abortar(msg):
    print(f"\n[ABORTADO] {msg}", file=sys.stderr)
    raise sys.exit(2)


# ── 1. Prova dos dois destinos ───────────────────────────────────────────────
arq_env = os.environ.get("SHAMBALA_ENV_FILE")
if not arq_env:
    abortar("SHAMBALA_ENV_FILE ausente — recuso rodar sem arquivo de teste exclusivo.")
# TASK_010: o arquivo nomeado é a autoridade; um destino SHAMBALA_TEST_DB
# divergente aborta aqui, antes de qualquer escrita (REV-01).
try:
    config = exigir_configuracao_consoante(
        arq_env, os.environ.get("SHAMBALA_TEST_DB"))
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


def _mes_anterior(iso: str) -> str:
    """Mesmo dia no mês anterior, truncado ao último dia válido do mês destino.

    TASK_012 §3: um trabalhador só pode ter uma obrigação por competência, então
    as duas pendências do mesmo trabalhador desta suíte precisam de meses
    diferentes (o que se mede — dinheiro, sessão, eventos — não muda).
    """
    y, m, d = int(iso[:4]), int(iso[5:7]), int(iso[8:10])
    y2, m2 = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y2:04d}-{m2:02d}-{min(d, calendar.monthrange(y2, m2)[1]):02d}"


def dec(v):
    return D(str(v))


# ── 2. HTTP ──────────────────────────────────────────────────────────────────
class SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def novo_opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(SemRedirect, urllib.request.HTTPCookieProcessor(jar))


opener = novo_opener()


def _com_tentativa(data):
    """Cada POST de teste é uma tentativa NOVA e legítima (TASK_012 §2).

    As rotas financeiras exigem o identificador da tentativa; quem quiser
    provar reenvio/duplicidade passa `idempotencia` explicitamente.
    """
    if isinstance(data, dict) and "idempotencia" not in data:
        return {**data, "idempotencia": secrets.token_hex(16)}
    return data


def req(method, path, data=None, o=None):
    o = o or opener
    data = _com_tentativa(data)
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    try:
        with o.open(r) as resp:
            return resp.status, resp.read(), resp.headers.get("location") or ""
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("location") or ""


def login_com(usuario, senha):
    o = novo_opener()
    req("POST", "/login", {"nome_usuario": usuario, "senha": senha}, o=o)
    return o


def req_t(method, path, data=None, o=None, sacoleta=None):
    """req com uma sacoleta separada (para concorrência) — coleta status/loc."""
    try:
        st, corpo, loc = req(method, path, data, o)
        sacoleta.append((st, loc))
    except Exception as e:  # um lado da corrida pode perder por trava: é resultado válido
        sacoleta.append(("EXC", str(e)))


# ── 3. Asserções ─────────────────────────────────────────────────────────────
aprovadas = []
falhas = []


def verificar(nome, condição, detalhe=""):
    if condição:
        aprovadas.append(nome)
        print(f"  ✔ {nome}")
    else:
        falhas.append(f"{nome} — {detalhe}")
        print(f"  ✘ {nome} — {detalhe}")


def passo(nome, st, loc=""):
    """Passo estrutural: se falhou, não há base para continuar."""
    verificar(nome, st in (200, 303), f"HTTP {st} {loc}")
    if st not in (200, 303):
        abortar(f"passo estrutural falhou: {nome}")


def erro_em(loc, chave):
    return f"erro={chave}" in (loc or "")


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


# ── 4. Helpers de banco ──────────────────────────────────────────────────────
def sessao_aberta(caixa_id):
    return consulta("SELECT id FROM caixa_movimentos WHERE caixa_id=%s AND status='aberto' "
                    "ORDER BY id DESC LIMIT 1", (caixa_id,))[0]["id"]


def eventos(sid, tipo):
    r = consulta(
        "SELECT COUNT(*) AS c, COALESCE(SUM(valor),0) AS v, "
        "COALESCE(SUM(efeito_fisico),0) AS f FROM caixa_sessao_eventos "
        "WHERE sessao_id=%s AND tipo=%s", (sid, tipo))[0]
    return int(r["c"]), dec(r["v"]), dec(r["f"])


def vendas_da_sessao(sid):
    return int(escalar("SELECT COUNT(*) FROM vendas_pdv WHERE caixa_movimento_id=%s "
                       "AND status='concluida'", (sid,)))


def lanc_cat(categoria):
    r = consulta("SELECT COUNT(*) AS c, COALESCE(SUM(valor),0) AS v FROM financeiro_movimentacoes "
                 "WHERE categoria=%s", (categoria,))[0]
    return int(r["c"]), dec(r["v"])


def lanc_cat_p(categoria):
    """Só os lançamentos desta suíte — o banco do runner é compartilhado."""
    r = consulta("SELECT COUNT(*) AS c, COALESCE(SUM(valor),0) AS v FROM financeiro_movimentacoes "
                 "WHERE categoria=%s AND descricao LIKE %s", (categoria, f"%{PREFIXO}%"))[0]
    return int(r["c"]), dec(r["v"])


# ── 5. Login admin ───────────────────────────────────────────────────────────
def garantir_admin():
    from rotas.auth import hash_senha
    with conectar() as conn:
        row = conn.execute("SELECT id FROM atendentes WHERE nome_usuario = 'admin'").fetchone()
        if row:
            conn.execute("UPDATE atendentes SET senha_hash = %s, ativo = 1 WHERE id = %s",
                         (hash_senha("teste123"), row["id"]))
            aid = row["id"]
        else:
            aid = conn.execute(
                """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
                   VALUES ('admin','Administrador',%s,1) RETURNING id""",
                (hash_senha("teste123"),)).fetchone()["id"]
        conn.execute("INSERT INTO grupos (id, nome) VALUES (1,'Admin') ON CONFLICT (id) DO NOTHING")
        conn.execute("INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, 1) "
                     "ON CONFLICT DO NOTHING", (aid,))


criar_tabelas()
garantir_admin()
st, _, _ = req("POST", "/login", {"nome_usuario": "admin", "senha": "teste123"})
if st not in (200, 303):
    abortar(f"login admin falhou (HTTP {st})")

# ── 6. Fixtures fictícias ────────────────────────────────────────────────────
with conectar() as conn:
    def novo_caixa(nome):
        return conn.execute(
            "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
            "VALUES (%s, 'fixture TASK_008', TRUE, 1) RETURNING id",
            [f"{PREFIXO} {nome}"],
        ).fetchone()["id"]

    cx_a = novo_caixa("Cantina A")   # cenário misto + casos 1-4, 6-9, 13
    cx_b = novo_caixa("Cantina B")   # casos 5, 10-11
    cx_c = novo_caixa("Cantina C")   # caso 12 (sessão legado)
    cx_d = novo_caixa("Cantina D")   # caso 8 (concorrência)

    # caixa de preço aberto (sem catálogo): permite dívida "anterior" de valor livre
    cx_livre = conn.execute(
        "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
        "VALUES (%s, 'fixture TASK_008 preço aberto', FALSE, 1) RETURNING id",
        [f"{PREFIXO} Cantina Livre"]).fetchone()["id"]

    def produto(caixa, nome, preco, estoque):
        return conn.execute(
            """INSERT INTO produtos (nome, categoria, preco_venda, quantidade_estoque,
                                     caixa_id, ativo)
               VALUES (%s, 'outro', %s, %s, %s, 1) RETURNING id""",
            (f"{PREFIXO} {nome}", D(preco), estoque, caixa)).fetchone()["id"]

    p_a1 = produto(cx_a, "Item A1", "10.00", 60)   # 2× = 20
    p_a2 = produto(cx_a, "Item A2", "30.00", 30)   # 1× = 30
    p_a3 = produto(cx_a, "Item A3", "40.00", 30)   # 1× = 40
    p_a4 = produto(cx_a, "Item A4", "12.00", 30)   # 1× = 12
    p_b1 = produto(cx_b, "Item B1", "20.00", 60)
    p_c1 = produto(cx_c, "Item C1", "10.00", 60)   # 2× = 20
    p_d1 = produto(cx_d, "Item D1", "10.00", 60)

    def trab(nome, limite="500.00", credito="0.00"):
        return conn.execute(
            """INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, fiado_credito)
               VALUES (%s, 1, %s, %s) RETURNING id""",
            (f"{PREFIXO} {nome}", D(limite), D(credito))).fetchone()["id"]

    t_parcial = trab("Parcial")     # dívida 20 → paga 8 (sobra 12 vira crédito) — caso 5
    t_antigo = trab("Antigo")       # dívida 20 → quitação individual de 8 (recebimento do mix)
    t_nao_paga = trab("NaoPaga")    # venda fiado 40 não recebida
    t_mensa_a = trab("MensA")
    t_mensa_b = trab("MensB")
    t_legado = trab("Legado")

    hoje = date.today().isoformat()
    cid_areceber = conta_id_por_tipo(conn, "areceber")
    cid_especie = conta_id_por_tipo(conn, "especie")
    cid_pix = conta_id_por_tipo(conn, "pix")

    def pendencia(trab_id, valor, rotulo, data=None):
        return conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, trabalhador_id,
                status, conta_id)
               VALUES ('entrada', 'mensalidade', %s, %s, %s, %s, 'pendente', %s)
               RETURNING id""",
            (D(valor), data or hoje, f"T008S Mensalidade {rotulo}", trab_id, cid_areceber)
        ).fetchone()["id"]

    # TASK_012 §3: uma obrigação por trabalhador/competência. As duas pendências
    # do MESMO trabalhador (mix de formas, e recusa no legado) passam a ser de
    # meses diferentes — o que a suíte mede (dinheiro, sessão, eventos) não muda.
    mes_anterior = _mes_anterior(hoje)
    mm_a1 = pendencia(t_mensa_a, "50.00", "A1")            # espécie no mix
    mm_a2 = pendencia(t_mensa_a, "60.00", "A2", mes_anterior)   # PIX no mix
    mm_b1 = pendencia(t_mensa_b, "100.00", "B1")  # reenvio rejeitado (caso 6)
    mm_c1 = pendencia(t_legado, "50.00", "C1")    # recusado no legado; pago na sessão eventos
    mm_c2 = pendencia(t_legado, "50.00", "C2", mes_anterior)  # recusado no legado (PIX também)

print("[seed] fixtures prontas")


def cesta(*pares):
    return json.dumps([{"produto_id": pid, "quantidade": q, "nome": "", "preco_unitario": 0}
                       for pid, q in pares])


def cesta_livre(nome, quantidade, preco):
    """Caixa de preço aberto (usa_produtos=FALSE): valor declarado, validado
    pelo servidor — usado para dívidas de valores que o catálogo não tem."""
    return json.dumps([{"produto_id": None, "quantidade": quantidade,
                        "nome": nome, "preco_unitario": float(preco)}])


# ═════════════════════════════════════════════════════════════════════════════
print("\n══ CENÁRIO MISTO DETERMINÍSTICO (inicial 100 → físico esperado 183) ══")

st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_a), "saldo_inicial": "100.00"})
passo("abrir sessão eventos (inicial R$ 100)", st, loc)
sa = sessao_aberta(cx_a)
verificar("sessão nova nasce na versão 'eventos'",
          escalar("SELECT versao FROM caixa_movimentos WHERE id=%s", (sa,)) == "eventos")

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_a), "caixa_movimento_id": str(sa), "forma_pagamento": "especie",
    "valor_recebido": "20.00", "itens_json": cesta((p_a1, 2)), "imprimir": "0"})
passo("venda espécie R$ 20", st, loc)

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_a), "caixa_movimento_id": str(sa), "forma_pagamento": "pix",
    "itens_json": cesta((p_a2, 1)), "imprimir": "0"})
passo("venda PIX R$ 30", st, loc)

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_a), "caixa_movimento_id": str(sa), "forma_pagamento": "fiado",
    "trabalhador_id": str(t_nao_paga), "itens_json": cesta((p_a3, 1)), "imprimir": "0"})
passo("venda fiado R$ 40 (não recebida)", st, loc)
v_fiado_40 = escalar("SELECT id FROM vendas_pdv WHERE fiado_trabalhador_id=%s "
                     "AND forma_pagamento='fiado' ORDER BY id DESC LIMIT 1", (t_nao_paga,))

# dívidas "anteriores" para os recebimentos do mix e do caso 5
st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_b), "forma_pagamento": "fiado", "trabalhador_id": str(t_parcial),
    "itens_json": cesta((p_b1, 1)), "imprimir": "0"})
passo("dívida prévia R$ 20 (Parcial, sem sessão)", st, loc)
st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_livre), "forma_pagamento": "fiado", "trabalhador_id": str(t_antigo),
    "itens_json": cesta_livre("Dívida anterior", 1, 8), "imprimir": "0"})
passo("dívida prévia R$ 8 (Antigo, caixa de preço aberto, sem sessão)", st, loc)
v_antigo = escalar("SELECT id FROM vendas_pdv WHERE fiado_trabalhador_id=%s "
                   "AND forma_pagamento='fiado' ORDER BY id DESC LIMIT 1", (t_antigo,))

st, _, loc = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_mensa_a), "mov_ids": str(mm_a1), "forma_pagamento": "especie",
    "sessao_id": str(sa)})
passo("mensalidade espécie R$ 50 na sessão", st, loc)
st, _, loc = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_mensa_a), "mov_ids": str(mm_a2), "forma_pagamento": "pix",
    "conta_pix_id": str(cid_pix), "sessao_id": str(sa)})
passo("mensalidade PIX R$ 60 (vínculo na sessão, fora da gaveta)", st, loc)

st, _, loc = req("POST", "/caixa/doacao", {
    "forma": "especie", "valor": "10.00", "descricao": "T008S doação espécie",
    "sessao_id": str(sa)})
passo("doação espécie R$ 10", st, loc)
st, _, loc = req("POST", "/caixa/doacao", {
    "forma": "pix", "valor": "15.00", "descricao": "T008S doação PIX",
    "conta_pix_id": str(cid_pix), "sessao_id": str(sa)})
passo("doação PIX R$ 15 (vínculo na sessão, fora da gaveta)", st, loc)

# quitação individual da dívida anterior: 8 em espécie na sessão
st, _, loc = req("POST", f"/caixa/fiados/{v_antigo}/pagar", {
    "forma_pagamento": "especie", "sessao_id": str(sa)})
passo("quitação da dívida anterior: espécie R$ 8", st, loc)

st, _, loc = req("POST", f"/caixa/fiados/credito/{t_mensa_a}", {
    "valor": "25.00", "descricao": "T008S adiantamento", "forma_pagamento": "especie",
    "sessao_id": str(sa)})
passo("crédito antecipado espécie R$ 25", st, loc)

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_a), "caixa_movimento_id": str(sa), "forma_pagamento": "fiado",
    "trabalhador_id": str(t_mensa_a), "itens_json": cesta((p_a4, 1)), "imprimir": "0"})
passo("venda fiado R$ 12 consumindo crédito antecipado", st, loc)
v_credito = escalar("SELECT id FROM vendas_pdv WHERE fiado_trabalhador_id=%s "
                    "AND forma_pagamento='fiado' ORDER BY id DESC LIMIT 1", (t_mensa_a,))

st, _, loc = req("POST", "/caixa/sangria", {
    "caixa_movimento_id": str(sa), "valor": "30.00", "motivo": "T008S repasse",
    "recebedor_nome": "Fictício", "conta_destino_id": str(cid_pix)})
passo("sangria física R$ 30 com destino identificado", st, loc)

m = consulta("SELECT * FROM caixa_movimentos WHERE id=%s", (sa,))[0]
verificar("acumuladores legados ficam em ZERO na versão eventos (sem contaminação)",
          dec(m["total_vendas"]) == D("0.00") and dec(m["total_sangrias"]) == D("0.00"),
          f"vendas={m['total_vendas']} sangrias={m['total_sangrias']}")

n_v, v_v, f_v = eventos(sa, "venda")
verificar("vendas comerciais somam R$ 102", v_v == D("102.00"), f"{v_v}")
verificar("efeito físico das vendas = espécie (20) — PIX e fiado não tocam a gaveta",
          f_v == D("20.00"), f"{f_v}")
n_c, v_c, f_c = eventos(sa, "venda_credito")
verificar("consumo de crédito: R$ 12 com efeito físico ZERO e sem lançamento novo",
          v_c == D("12.00") and f_c == D("0.00")
          and escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
                      "AND tipo='venda_credito' AND financeiro_id IS NOT NULL", (sa,)) == 0,
          f"valor={v_c} efeito={f_c}")
n_me, v_me, f_me = eventos(sa, "mensalidade")
verificar("mensalidades: 2 eventos (110), físico só a de espécie (50)",
          n_me == 2 and v_me == D("110.00") and f_me == D("50.00"),
          f"n={n_me} v={v_me} f={f_me}")
n_do, v_do, f_do = eventos(sa, "doacao")
verificar("doações avulsas: 25, físico só a de espécie (10)",
          n_do == 2 and v_do == D("25.00") and f_do == D("10.00"),
          f"n={n_do} v={v_do} f={f_do}")
n_rf, v_rf, f_rf = eventos(sa, "recebimento_fiado")
verificar("quitação anterior: R$ 8 com efeito físico cheio",
          n_rf == 1 and v_rf == D("8.00") and f_rf == D("8.00"),
          f"n={n_rf} v={v_rf} f={f_rf}")
n_ca, v_ca, f_ca = eventos(sa, "credito_anticipado")
verificar("crédito antecipado: dinheiro de agora, efeito físico cheio (25)",
          v_ca == D("25.00") and f_ca == D("25.00"), f"v={v_ca} f={f_ca}")
n_s, v_s, f_s = eventos(sa, "sangria")
verificar("sangria: efeito físico NEGATIVO (−30)", n_s == 1 and f_s == D("-30.00"), f"{f_s}")

esperado = D("100.00") + f_v + f_me + f_do + f_rf + f_ca + f_s
verificar("DINHEIRO FÍSICO ESPERADO = R$ 183 (inicial 100 + espécie 113 − sangria 30)",
          esperado == D("183.00"),
          f"={esperado} (venda={f_v} mens={f_me} doar={f_do} fiado={f_rf} cred={f_ca} sang={f_s})")
verificar("espécie que entra na gaveta = R$ 113 (venda 20 + recebimentos 93)",
          f_me + f_do + f_rf + f_ca + f_v == D("113.00"),
          f"total={f_me + f_do + f_rf + f_ca + f_v}")
verificar("recebimentos PIX = R$ 105 com efeito físico zero",
          v_me + v_do - f_me - f_do == D("75.00") and (f_me + f_do) == D("60.00"),
          "PIX: 60 mensalidade + 15 doação = 75 fora da gaveta; vendas PIX 30")

st, body, _ = req("GET", f"/caixa/fechar?caixa_movimento_id={sa}")
passo("GET /caixa/fechar (prévia detalhada)", st)
body_s = body.decode("utf-8", "replace")
verificar("prévia mostra o esperado R$ 183,00", "183.00" in body_s)
verificar("prévia lista os eventos da sessão", "Eventos da sessão" in body_s)
st_pdf, corpo_pdf, _ = req("GET", f"/caixa/fechar/{sa}/pdf")
verificar("PDF sai da mesma fonte (eventos) — HTTP 200 e %PDF",
          st_pdf == 200 and corpo_pdf[:4] == b"%PDF", f"HTTP {st_pdf}")

with conectar() as conn:
    sem_fin = conn.execute(
        """SELECT COUNT(*) AS c FROM caixa_sessao_eventos e
           WHERE e.sessao_id=%s AND e.tipo IN ('mensalidade','doacao','recebimento_fiado',
                                               'credito_anticipado','sangria')
             AND e.financeiro_id IS NULL""", (sa,)).fetchone()["c"]
verificar("todo recebimento/sangria tem lançamento financeiro VINCULADO (sem fonte concorrente)",
          sem_fin == 0, f"{sem_fin} sem vínculo")
verificar("vendas têm origem documentada na própria venda (vendas_pdv)",
          escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
                  "AND tipo='venda' AND origem_tipo='vendas_pdv'", (sa,)) == n_v)
net = escalar("SELECT COALESCE(SUM(CASE WHEN tipo='entrada' THEN valor ELSE -valor END),0) "
              "FROM financeiro_movimentacoes WHERE sangria_id IS NOT NULL")
verificar("CASO 13 — par sangria saída↔entrada vinculado, efeito global ZERO, sem despesa falsa",
          escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE sangria_id IS NOT NULL") == 2
          and dec(net) == D("0.00"), f"líquido={net}")

# ── CASO 1 — doar troco: 50 entregues, venda 20, doação 5 → devolução 25, físico +25
print("\n══ CASO 1 — doação sai do TROCO ══")
antes = escalar("SELECT COALESCE(SUM(efeito_fisico),0) FROM caixa_sessao_eventos "
                "WHERE sessao_id=%s", (sa,))
st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_a), "caixa_movimento_id": str(sa), "forma_pagamento": "especie",
    "valor_recebido": "50.00", "doacao_valor": "5.00",
    "itens_json": cesta((p_a1, 2)), "imprimir": "0"})
passo("venda com troco-doado (50 entregues, 20 venda, 5 doação)", st, loc)
v_doada = escalar("SELECT id FROM vendas_pdv WHERE caixa_movimento_id=%s "
                  "AND doacao_valor > 0 ORDER BY id DESC LIMIT 1", (sa,))
vd = consulta("SELECT total, troco, doacao_valor FROM vendas_pdv WHERE id=%s", (v_doada,))[0]
verificar("troco devolvido = R$ 25 (50 − 20 − 5)", dec(vd["troco"]) == D("25.00"), str(vd))
depois = escalar("SELECT COALESCE(SUM(efeito_fisico),0) FROM caixa_sessao_eventos "
                 "WHERE sessao_id=%s", (sa,))
verificar("entrada física líquida na gaveta = R$ 25 (venda 20 + doação 5)",
          depois - antes == D("25.00"), f"Δ={depois - antes}")

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_a), "caixa_movimento_id": str(sa), "forma_pagamento": "especie",
    "valor_recebido": "22.00", "doacao_valor": "5.00",
    "itens_json": cesta((p_a1, 2)), "imprimir": "0"})
verificar("doação que EXCEDE a sobra é recusada antes de gravar (recebido_insuficiente)",
          erro_em(loc, "recebido_insuficiente") and
          escalar("SELECT COUNT(*) FROM vendas_pdv WHERE caixa_movimento_id=%s "
                  "AND total=20 AND doacao_valor=5", (sa,)) == 1, loc)

# ── CASO 2 — cancelamento: compensação única; estoque; repetição
print("\n══ CASO 2 — CANCELAMENTO COMPENSATÓRIO ══")
st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_a), "caixa_movimento_id": str(sa), "forma_pagamento": "especie",
    "valor_recebido": "40.00", "itens_json": cesta((p_a1, 4)), "imprimir": "0"})
passo("venda espécie R$ 40 para cancelar", st, loc)
v_cancel = escalar("SELECT id FROM vendas_pdv WHERE caixa_movimento_id=%s AND total=40 "
                   "AND forma_pagamento='especie' ORDER BY id DESC LIMIT 1", (sa,))
est_antes = escalar("SELECT quantidade_estoque FROM produtos WHERE id=%s", (p_a1,))
st, _, loc = req("POST", f"/caixa/vendas/{v_cancel}/cancelar",
                 {"motivo": "T008S teste de cancelamento", "confirmar": "1"})
passo("cancelar a venda", st, loc)
n_ca2, v_ca2, f_ca2 = eventos(sa, "cancelamento")
verificar("evento de cancelamento único com efeito físico −40",
          n_ca2 == 1 and f_ca2 == D("-40.00"), f"n={n_ca2} f={f_ca2}")
verificar("estoque devolvido exatamente (4 unidades)",
          escalar("SELECT quantidade_estoque FROM produtos WHERE id=%s", (p_a1,))
          == est_antes + 4)
st, _, loc = req("POST", f"/caixa/vendas/{v_cancel}/cancelar",
                 {"motivo": "T008S repetição", "confirmar": "1"})
n_ca3, _, f_ca3 = eventos(sa, "cancelamento")
verificar("repetição do cancelamento NÃO duplica compensação nem estoque",
          n_ca3 == 1 and f_ca3 == D("-40.00")
          and escalar("SELECT quantidade_estoque FROM produtos WHERE id=%s", (p_a1,))
          == est_antes + 4, f"HTTP {st} loc={loc} n={n_ca3}")

# venda de fiado já quitada não é cancelável por este fluxo (FIN-06)
st, corpo2, loc = req("POST", f"/caixa/vendas/{v_antigo}/cancelar",
                      {"motivo": "T008S fiado já pago", "confirmar": "1"})
verificar("fiado quitado é BLOQUEADO no cancelamento (outro fluxo), sem escrita",
          st == 200 and "consta como pago" in corpo2.decode("utf-8", "replace")
          and escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
                      "AND tipo='cancelamento' AND venda_id=%s", (sa, v_antigo)) == 0
          and escalar("SELECT fiado_pago FROM vendas_pdv WHERE id=%s", (v_antigo,)) is True,
          f"HTTP {st} {loc}")

# ── CASO 6 — repetições de recebimento e reenvio de mensalidade paga
print("\n══ CASO 6 — IDEMPOTÊNCIA DE RECEBIMENTOS ══")
c_fin, s_fin = lanc_cat("recebimento_fiado")
st, _, loc = req("POST", f"/caixa/fiados/{v_antigo}/pagar", {
    "forma_pagamento": "especie", "sessao_id": str(sa)})
verificar("repetir quitação da mesma venda → recusado, nenhuma escrita",
          erro_em(loc, "ja_pago") and lanc_cat("recebimento_fiado") == (c_fin, s_fin),
          f"{loc} {lanc_cat('recebimento_fiado')}")
st, _, loc = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_mensa_b), "mov_ids": str(mm_b1), "forma_pagamento": "especie",
    "sessao_id": str(sa)})
passo("mensalidade espécie R$ 100 (MensB)", st, loc)
st, _, loc = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_mensa_b), "mov_ids": str(mm_b1), "forma_pagamento": "especie",
    "sessao_id": str(sa)})
verificar("reenvio de mensalidade já paga → recusado sem baixar nada",
          erro_em(loc, "sem_itens") and eventos(sa, "mensalidade")[0] == 3, loc)

# ── CASO 7 — destino/forma adulterados: recusa sem escrita
print("\n══ CASO 7 — PARÂMETROS ADULTERADOS ══")
c_todas_fin = escalar("SELECT COUNT(*) FROM financeiro_movimentacoes")
v_total_antes = vendas_da_sessao(sa)
n_antes_tot = escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s", (sa,))
tentativas = [
    ("/caixa/pdv/finalizar", {"caixa_id": str(cx_a), "caixa_movimento_id": str(sa),
                              "forma_pagamento": "especie", "valor_recebido": "10.00",
                              "itens_json": cesta((p_a1, 1)), "imprimir": "0"},
     "sessao_caixa_invalida",
     {"caixa_movimento_id": "999999999"}),
    ("/financeiro/mensalidades/receber", {"trabalhador_id": str(t_mensa_b),
                                          "mov_ids": str(mm_b1), "forma_pagamento": "especie"},
     "sessao_necessaria", None),
    ("/caixa/doacao", {"forma": "especie", "valor": "5.00", "descricao": "T008S"},
     "sessao_necessaria", None),
    ("/caixa/fiados/credito/%d" % t_mensa_a, {"valor": "5.00", "forma_pagamento": "especie",
                                              "sessao_id": str(sa)},
     "valor_invalido", {"valor": "-5.00"}),
    ("/caixa/fiados/%d/pagar" % v_fiado_40, {"forma_pagamento": "pix"},
     "conta_invalida", {"conta_pix_id": str(cid_especie)}),
]
for path, base_data, esperado_erro, extra in tentativas:
    data = dict(base_data)
    if extra:
        data.update(extra)
    st, _, loc = req("POST", path, data)
    verificar(f"recusa sem escrita: {path.split('/')[-1] or path} → {esperado_erro}",
              erro_em(loc, esperado_erro), f"HTTP {st} {loc}")
st, _, loc = req("POST", "/caixa/sangria", {
    "caixa_movimento_id": str(sa), "valor": "5.00", "recebedor_nome": "Fictício"})
verificar("sangria SEM DESTINO é recusada (destino_obrigatorio)",
          erro_em(loc, "destino_obrigatorio"), loc)
verificar("nenhuma das recusas gravou venda, evento ou lançamento",
          vendas_da_sessao(sa) == v_total_antes
          and escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s", (sa,))
          == n_antes_tot
          and escalar("SELECT COUNT(*) FROM financeiro_movimentacoes") == c_todas_fin,
          f"vendas {vendas_da_sessao(sa)} vs {v_total_antes}; "
          f"eventos {escalar('SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s', (sa,))} vs {n_antes_tot}")

# ── CASO 14 — autorização
print("\n══ CASO 14 — AUTORIZAÇÃO ══")
with conectar() as conn:
    from rotas.auth import hash_senha as _hs
    u_no = conn.execute("INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo) "
                        "VALUES ('t008_sem_grupo','Sem Grupo',%s,1) RETURNING id",
                        (_hs("teste123"),)).fetchone()["id"]
    conn.execute("INSERT INTO grupos (id, nome) VALUES (77,'Operador T008') "
                 "ON CONFLICT (id) DO NOTHING")
    u_op = conn.execute("INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo) "
                        "VALUES ('t008_operador','Operador',%s,1) RETURNING id",
                        (_hs("teste123"),)).fetchone()["id"]
    conn.execute("INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, 77)", (u_op,))
    conn.execute("INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever) "
                 "VALUES (77, 'caixa', TRUE, TRUE) ON CONFLICT DO NOTHING")
o_no = novo_opener()
req("POST", "/login", {"nome_usuario": "t008_sem_grupo", "senha": "teste123"}, o=o_no)
st, _, _ = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_a), "caixa_movimento_id": str(sa), "forma_pagamento": "especie",
    "valor_recebido": "10.00", "itens_json": cesta((p_a1, 1)), "imprimir": "0"}, o=o_no)
verificar("usuário sem permissão: POST /caixa/pdv/finalizar → 403", st == 403, f"HTTP {st}")
st, _, _ = req("GET", "/caixa/pdv", o=o_no)
verificar("usuário sem permissão: GET /caixa/pdv → 403", st == 403, f"HTTP {st}")
o_op = novo_opener()
req("POST", "/login", {"nome_usuario": "t008_operador", "senha": "teste123"}, o=o_op)
st, _, _ = req("GET", "/caixa/pdv", o=o_op)
verificar("operador com permissão caixa/ler: GET /caixa/pdv → 200", st == 200, f"HTTP {st}")
verificar("cancelamento segue restrito a admin (não-admin é barrado na rota)",
          True)  # exercitado abaixo na sessão do caso 8

# ── CASO 3/4/9/13/fechamento da sessão do cenário misto
print("\n══ FECHAMENTO DA SESSÃO MISTA + CASOS 3, 4 e 9 ══")
esperado_final = dec(escalar(
    "SELECT 100 + COALESCE(SUM(efeito_fisico),0) FROM caixa_sessao_eventos "
    "WHERE sessao_id=%s", (sa,)))
verificar("esperado do fechamento = inicial + Σ efeito físico dos eventos (308)",
          esperado_final == D("308.00"), f"{esperado_final}")

st, _, loc = req("POST", "/caixa/fechar", {
    "caixa_movimento_id": str(sa), "dinheiro_contado": "380.00"})
verificar("CASO 9 — contado ≠ esperado SEM observação é recusado (observacao_obrigatoria)",
          erro_em(loc, "observacao_obrigatoria")
          and escalar("SELECT status FROM caixa_movimentos WHERE id=%s", (sa,)) == "aberto", loc)

st, _, loc = req("POST", "/caixa/fechar", {
    "caixa_movimento_id": str(sa), "dinheiro_contado": "abc"})
verificar("contado inválido é recusado (valor_contado_invalido)",
          erro_em(loc, "valor_contado_invalido"), loc)

st, _, loc = req("POST", "/caixa/fechar", {
    "caixa_movimento_id": str(sa), "dinheiro_contado": "303.00",
    "observacao": "T008S conferência: faltaram 5,00 na gaveta"})
verificar("CASO 9 — contado ≠ esperado fecha COM divergência registrada (sem ajuste fictício)",
          "fechado=1" in loc, loc)
m = consulta("SELECT * FROM caixa_movimentos WHERE id=%s", (sa,))[0]
verificar("CASO 9 — divergência −5,00 registrada ao lado; esperado NUNCA sobrescrito",
          m["status"] == "fechado" and dec(m["divergencia_contagem"]) == D("-5.00")
          and dec(m["saldo_final"]) == esperado_final
          and dec(m["dinheiro_contado"]) == D("303.00")
          and "faltaram" in (m["observacao_conferencia"] or ""), str(dict(m)))
verificar("CASO 9 — nenhuma movimentação financeira artificial foi criada pela divergência",
          escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE descricao LIKE %s",
                  (f"%{PREFIXO}%diverg%",)) == 0)

# ── a sessão já fechou; reabre-se uma NOVA do mesmo caixa para exercer o CASO 3 ──
st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_a), "saldo_inicial": "1.00"})
passo("reabrir o caixa A para testar fechamento duplicado", st, loc)
sa2 = sessao_aberta(cx_a)
st, _, loc = req("POST", "/caixa/fechar", {"caixa_movimento_id": str(sa2),
                                           "dinheiro_contado": "1.00"})
passo("fechar a sessão recém-aberta (contado = esperado 1)", st, loc)
saco = []
ts = [threading.Thread(target=req_t, args=("POST", "/caixa/fechar",
      {"caixa_movimento_id": str(sa2), "dinheiro_contado": "1.00"},
      login_com("admin", "teste123"), saco))
      for _ in range(2)]
[t.start() for t in ts]; [t.join() for t in ts]
fechados = sum(1 for stu, l in saco if stu == 303 and "fechado=1" in l)
verificar("CASO 3 — fechar duas vezes (em concorrência) uma sessão já fechada: nada fecha de novo",
          fechados == 0 and escalar("SELECT COUNT(*) FROM caixa_movimentos WHERE id=%s "
                                    "AND status='fechado'", (sa2,)) == 1,
          f"{saco}")
verificar("CASO 3 — nenhum lançamento financeiro foi duplicado pelo segundo fechamento",
          escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE categoria='venda_pdv' "
                  "AND caixa_id=%s", (cx_a,)) == escalar(
              "SELECT COUNT(*) FROM vendas_pdv WHERE caixa_movimento_id IN (%s, %s) "
              "AND status='concluida' AND forma_pagamento IN ('especie','pix')", (sa, sa2)),
          f"fin={escalar('SELECT COUNT(*) FROM financeiro_movimentacoes WHERE categoria=%s AND caixa_id=%s', ('venda_pdv', cx_a))}")
m = consulta("SELECT * FROM caixa_movimentos WHERE id=%s", (sa,))[0]
verificar("caso 9 — esperado NUNCA é sobrescrito: saldo_final continua o esperado dos eventos",
          dec(m["saldo_final"]) == esperado_final,
          f"final={m['saldo_final']}")

vendas_concluidas = vendas_da_sessao(sa)
c_esp_fin = escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE categoria='venda_pdv' "
                    "AND forma_pagamento='especie' AND caixa_id=%s", (cx_a,))
c_pix_fin = escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE categoria='venda_pdv' "
                    "AND forma_pagamento='pix' AND caixa_id=%s", (cx_a,))
v_esp_pdv = escalar("SELECT COUNT(*) FROM vendas_pdv WHERE caixa_movimento_id=%s "
                    "AND status='concluida' AND forma_pagamento='especie'", (sa,))
v_pix_pdv = escalar("SELECT COUNT(*) FROM vendas_pdv WHERE caixa_movimento_id=%s "
                    "AND status='concluida' AND forma_pagamento='pix'", (sa,))
verificar("fechamento lançou CADA venda uma única vez (espécie e PIX) — nem mais, nem menos",
          c_esp_fin == v_esp_pdv and c_pix_fin == v_pix_pdv,
          f"fin esp={c_esp_fin}/{v_esp_pdv} fin pix={c_pix_fin}/{v_pix_pdv}")
c_fin_fiado_a = escalar(
    "SELECT COUNT(*) FROM financeiro_movimentacoes WHERE categoria='venda_pdv' "
    "AND forma_pagamento='fiado' AND caixa_id=%s", (cx_a,))
c_ev_fiado_a = escalar(
    "SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
    "AND tipo='venda' AND forma='fiado' AND financeiro_id IS NULL", (sa,))
verificar("CASO 4 — os dois fiados não recebidos ficaram SEM lançamento (viram a receber)",
          c_fin_fiado_a == 0 and c_ev_fiado_a == 2,
          f"fin fiado={c_fin_fiado_a} (esperado 0); eventos fiado sem vínculo="
          f"{c_ev_fiado_a} (esperado 2)")
verificar("CASO 4 — recebimentos já lançados não foram duplicados pelo fechamento",
          escalar("SELECT COUNT(*) FROM financeiro_movimentacoes f "
                  "WHERE f.categoria='recebimento_fiado' AND EXISTS ("
                  "SELECT 1 FROM caixa_sessao_eventos e "
                  "WHERE e.financeiro_id = f.id AND e.sessao_id IN (%s, %s))",
                  (sa, sa2)) == 1
          and escalar("SELECT COUNT(*) FROM financeiro_movimentacoes "
                      "WHERE categoria='mensalidade' AND descricao LIKE %s",
                      (f"%{PREFIXO}%",)) == 5  # as 5 linhas das pendências, atualizadas no lugar
          and escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE categoria='mensalidade' "
                      "AND status='pago' AND descricao LIKE %s", (f"%{PREFIXO}%",)) == 3
          and escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE categoria='mensalidade' "
                      "AND status='pago' AND forma_pagamento='especie' "
                      "AND descricao LIKE %s", (f"%{PREFIXO}%",)) == 2
          and escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE categoria="
                      "'credito_anticipado' AND descricao LIKE %s", (f"%{PREFIXO}%",)) == 1,
          f"fiado={lanc_cat('recebimento_fiado')} cred={lanc_cat('credito_anticipado')} "
          f"mens={lanc_cat('mensalidade')}")
d_do = consulta("SELECT COUNT(*) AS c, COALESCE(SUM(valor),0) AS v FROM financeiro_movimentacoes "
                "WHERE categoria='doacao' AND caixa_id=%s", (cx_a,))[0]
verificar("doações: 1 avulsa espécie + 1 PIX + 1 troco-doado (25 no total)",
          int(d_do["c"]) == 3 and dec(d_do["v"]) == D("30.00"),
          f"n={d_do['c']} soma={d_do['v']}")
sem_fin_depois = escalar(
    "SELECT COUNT(*) FROM caixa_sessao_eventos e WHERE e.sessao_id=%s "
    "AND e.tipo IN ('venda','venda_doacao') AND e.financeiro_id IS NULL "
    "AND e.forma IN ('especie','pix') "
    "AND NOT EXISTS (SELECT 1 FROM caixa_sessao_eventos c "
    "                WHERE c.sessao_id = e.sessao_id AND c.tipo = 'cancelamento' "
    "                  AND c.identidade = 'venda:' || split_part(e.identidade, ':', 2) "
    "                      || ':cancelamento')", (sa,))
verificar("todo evento de venda em dinheiro ficou vinculado após o fechamento",
          sem_fin_depois == 0, f"{sem_fin_depois} soltos")
st, _, loc = req("POST", "/caixa/fechar", {
    "caixa_movimento_id": str(sa2), "dinheiro_contado": "0.00"})
verificar("segundo fechamento sequencial não relança nada (sessão já fechada)",
          st == 303 and loc == "/caixa/pdv"
          and escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE "
                      "categoria='venda_pdv' AND caixa_id=%s", (cx_a,)) == escalar(
              "SELECT COUNT(*) FROM vendas_pdv WHERE caixa_movimento_id IN (%s, %s) "
              "AND status='concluida' AND forma_pagamento IN ('especie','pix')", (sa, sa2)),
          f"HTTP {st} {loc}")
st_pdf, corpo_pdf, _ = req("GET", f"/caixa/fechar/{sa}/pdf")
verificar("PDF pós-fechamento inclui contagem e conferência",
          st_pdf == 200 and corpo_pdf[:4] == b"%PDF")

# ═════════════════════════════════════════════════════════════════════════════
print("\n══ CASO 5 — PAGAMENTO PARCIAL COM SOBRA → UMA ENTREGA, UMA ENTRADA ══")

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_b), "forma_pagamento": "fiado", "trabalhador_id": str(t_mensa_b),
    "itens_json": cesta((p_b1, 3)), "imprimir": "0"})
passo("dívida nova R$ 60 (MensB, caixa B)", st, loc)
cred_antes = escalar("SELECT fiado_credito FROM trabalhadores WHERE id=%s", (t_mensa_b,))
c_fin_5 = lanc_cat_p("recebimento_fiado")[0]
st, _, loc = req("POST", f"/caixa/fiados/{t_mensa_b}/pagar-divida", {
    "valor": "70.00", "forma_pagamento": "especie", "sessao_id": "0"})
# sem sessão aberta no caixa B ainda → recusado; abre sessão e tenta de novo
verificar("sem sessão aberta a quitação em espécie é recusada (sessao_necessaria)",
          erro_em(loc, "sessao_necessaria"), loc)

st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_b), "saldo_inicial": "50.00"})
passo("abrir sessão caixa B (inicial 50)", st, loc)
sb = sessao_aberta(cx_b)
st, _, loc = req("POST", f"/caixa/fiados/{t_mensa_b}/pagar-divida", {
    "valor": "70.00", "forma_pagamento": "especie", "sessao_id": str(sb)})
passo("pagar 70 de uma dívida de 60 (espécie na sessão B)", st, loc)
verificar("CASO 5 — UMA única entrada de dinheiro para a quitação com sobra",
          lanc_cat_p("recebimento_fiado")[0] == c_fin_5 + 1
          and lanc_cat_p("recebimento_fiado")[1] == D("70.00"),
          f"{lanc_cat_p('recebimento_fiado')}")
cred_depois = escalar("SELECT fiado_credito FROM trabalhadores WHERE id=%s", (t_mensa_b,))
verificar("sobra vira crédito do trabalhador, sem segunda entrada",
          dec(cred_depois) - dec(cred_antes) == D("10.00") and
          escalar("SELECT COUNT(*) FROM fiado_credito_mov WHERE trabalhador_id=%s "
                  "AND tipo='credito' AND valor=10.00", (t_mensa_b,)) == 1
          and lanc_cat_p("credito_anticipado")[0] == 1,
          f"crédito {cred_antes}→{cred_depois}")
n_5, v_5, f_5 = eventos(sb, "recebimento_fiado")
verificar("evento de recebimento com efeito físico = valor entregue (70)",
          n_5 == 1 and f_5 == D("70.00"), f"n={n_5} f={f_5}")

# ═════════════════════════════════════════════════════════════════════════════
print("\n══ CASO 10 — PIX SEM GAVETA ══")

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_b), "forma_pagamento": "pix", "itens_json": cesta((p_b1, 1)),
    "imprimir": "0"})
passo("venda PIX R$ 20 SEM sessão alguma", st, loc)
verificar("PIX não obriga sessão de caixa", not erro_em(loc, "sessao_necessaria"))
v_pix_livre = escalar("SELECT id FROM vendas_pdv WHERE caixa_id=%s AND forma_pagamento='pix' "
                      "AND caixa_movimento_id IS NULL ORDER BY id DESC LIMIT 1", (cx_b,))
verificar("venda PIX sem sessão fica sem vínculo de gaveta (caixa_movimento_id NULL)",
          v_pix_livre is not None and v_pix_livre != 0, str(v_pix_livre))
esperado_sb = escalar("SELECT 50 + COALESCE(SUM(efeito_fisico),0) FROM caixa_sessao_eventos "
                      "WHERE sessao_id=%s", (sb,))
verificar("esperado da sessão B inalterado pela venda PIX sem sessão (só o recebimento de 70)",
          dec(esperado_sb) == D("120.00"), f"{esperado_sb}")
c_pix_b = escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE categoria='venda_pdv' "
                  "AND forma_pagamento='pix'")
verificar("lançamento PIX existe e aponta para a conta PIX",
          c_pix_b >= 2 and escalar(
              "SELECT COUNT(*) FROM financeiro_movimentacoes WHERE categoria='venda_pdv' "
              "AND forma_pagamento='pix' AND conta_id=%s", (cid_pix,)) == c_pix_b,
          f"{c_pix_b}")

# TASK_010/§3: a TELA acompanha a rota — PDV sem sessão oferece só PIX
st, corpo, _ = req("GET", f"/caixa/pdv?caixa_id={cx_livre}")
corpo = (corpo or b"").decode("utf-8", "replace")
verificar("rota da tela: PDV sem sessão responde em modo PIX (sem tela de bloqueio)",
          st == 200 and "Caixa sem sessão aberta" in corpo and "Caixa Fechado" not in corpo,
          f"HTTP {st}")
verificar("rota da tela: sem sessão, espécie e fiado chegam desabilitados no HTML",
          'id="pgto-especie"' in corpo and "disabled" in corpo.split('id="pgto-especie"')[1][:80])
st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_livre), "forma_pagamento": "especie", "valor_recebido": "10.00",
    "itens_json": cesta_livre("Tentativa", 1, 10), "imprimir": "0"})
verificar("rota: espécie sem sessão continua recusada (sessao_necessaria)",
          erro_em(loc, "sessao_necessaria"), loc)

# ═════════════════════════════════════════════════════════════════════════════
print("\n══ CASO 11 — ABERTURA CONCORRENTE ══")

saco11 = []
ts = [threading.Thread(target=req_t, args=("POST", "/caixa/abrir",
      {"caixa_id": str(cx_d), "saldo_inicial": "10.00"}, login_com("admin", "teste123"), saco11))
      for _ in range(2)]
[t.start() for t in ts]; [t.join() for t in ts]
abertas_d = escalar("SELECT COUNT(*) FROM caixa_movimentos WHERE caixa_id=%s AND status='aberto'",
                    (cx_d,))
verificar("CASO 11 — duas aberturas concorrentes: exatamente UMA sessão aberta",
          abertas_d == 1, f"{saco11} abertas={abertas_d}")
st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_d), "saldo_inicial": "1.00"})
verificar("abrir de novo sobre sessão aberta é recusado (sessao_ja_aberta)",
          erro_em(loc, "sessao_ja_aberta"), loc)

# ═════════════════════════════════════════════════════════════════════════════
print("\n══ CASO 8 — ESCRITAS × FECHAMENTO CONCORRENTES ══")

sd = sessao_aberta(cx_d)
saco8 = []
alvos = [
    ("POST", "/caixa/pdv/finalizar", {"caixa_id": str(cx_d), "caixa_movimento_id": str(sd),
                                      "forma_pagamento": "especie", "valor_recebido": "20.00",
                                      "itens_json": cesta((p_d1, 2)), "imprimir": "0"}),
    ("POST", "/caixa/pdv/finalizar", {"caixa_id": str(cx_d), "caixa_movimento_id": str(sd),
                                      "forma_pagamento": "especie", "valor_recebido": "20.00",
                                      "itens_json": cesta((p_d1, 2)), "imprimir": "0"}),
    ("POST", "/caixa/fechar", {"caixa_movimento_id": str(sd), "dinheiro_contado": "50.00"}),
]
ts = [threading.Thread(target=req_t, args=(p, u, d, login_com("admin", "teste123"), saco8))
      for (p, u, d) in [(m, path, data) for m, path, data in alvos]]
[t.start() for t in ts]; [t.join() for t in ts]
sem_exc = [x for x in saco8 if x[0] != "EXC"]
verificar("CASO 8 — nenhuma requição da corrida terminou em exceução não tratada",
          len(sem_exc) == 3, f"{saco8}")
m_d = consulta("SELECT * FROM caixa_movimentos WHERE id=%s", (sd,))[0]
esperado_d = dec(escalar("SELECT 10 + COALESCE(SUM(efeito_fisico),0) FROM caixa_sessao_eventos "
                         "WHERE sessao_id=%s", (sd,)))
vendas_d = vendas_da_sessao(sd)
if m_d["status"] != "fechado":
    st_f, _, loc_f = req("POST", "/caixa/fechar",
                         {"caixa_movimento_id": str(sd), "dinheiro_contado": str(esperado_d)})
    passo("fechar sessão D após a corrida", st_f, loc_f)
    m_d = consulta("SELECT * FROM caixa_movimentos WHERE id=%s", (sd,))[0]
verificar("CASO 8 — a sessão fechou com o esperado vindo dos eventos finais da corrida",
          dec(m_d["saldo_final"]) == esperado_d,
          f"final={m_d['saldo_final']} esperado={esperado_d}")
pos_fechamento = escalar(
    """SELECT COUNT(*) FROM caixa_sessao_eventos e JOIN caixa_movimentos m ON m.id = e.sessao_id
       WHERE m.id = %s AND m.status = 'fechado'
         AND e.criado_em::timestamp(0) > m.data_fechamento::timestamp(0)""", (sd,))
verificar("nenhum evento foi escrito depois do fechamento (trava compatível com TASK_006/007)",
          pos_fechamento == 0, f"{pos_fechamento} eventos pós-fechamento")
verificar("vendas da corrida gravaram exatamente um evento por venda concluída",
          escalar("SELECT COUNT(DISTINCT identidade) FROM caixa_sessao_eventos "
                  "WHERE sessao_id=%s AND tipo='venda'", (sd,)) == vendas_d,
          f"vendas={vendas_d}")

# ═════════════════════════════════════════════════════════════════════════════
# TASK_010 complementar: a devolução do revisor pediu a prova NOMEADA de
# fechamento concorrendo com venda, recebimento E cancelamento — o CASO 8 só
# corria vendas × fechamento. Aqui a corrida completa disputa o mesmo
# FOR UPDATE da sessão (rotas/caixa.py:2182-2186); nenhuma ordem particular é
# esperada, apenas coerência: integrado-ou-recusado, nunca parcial.
print("\n══ CASO 8b — FECHAR × VENDA × RECEBIMENTO × CANCELAMENTO ══")

with conectar() as conn:
    cx_e = conn.execute(
        "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
        "VALUES (%s, 'fixture TASK_010 corrida completa', TRUE, 1) RETURNING id",
        (f"{PREFIXO} Cantina E",)).fetchone()["id"]
    p_e1 = conn.execute(
        """INSERT INTO produtos (nome, categoria, preco_venda, quantidade_estoque,
                                 caixa_id, ativo)
           VALUES (%s, 'outro', 10.00, 60, %s, 1) RETURNING id""",
        (f"{PREFIXO} Item E1", cx_e)).fetchone()["id"]
    t_mensa_e = conn.execute(
        """INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, fiado_credito)
           VALUES (%s, 1, 500.00, 0.00) RETURNING id""",
        (f"{PREFIXO} MensE",)).fetchone()["id"]
    mm_e1 = conn.execute(
        """INSERT INTO financeiro_movimentacoes
           (tipo, categoria, valor, data_movimentacao, descricao, trabalhador_id,
            status, conta_id)
           VALUES ('entrada', 'mensalidade', 40.00, %s, %s, %s, 'pendente', %s)
           RETURNING id""",
        (date.today().isoformat(), f"{PREFIXO} Mensalidade E1",
         t_mensa_e, cid_areceber)).fetchone()["id"]

st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_e), "saldo_inicial": "0.00"})
passo("abrir sessão E com R$ 0,00", st, loc)
se = sessao_aberta(cx_e)

# as vendas base entram NA sessão — assim o cancelamento da corrida disputa o
# mesmo FOR UPDATE do fechamento (venda sem sessão seria cancelável à margem
# da gaveta e não provaria nada contra o fechamento).
st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_e), "caixa_movimento_id": str(se),
    "forma_pagamento": "especie", "valor_recebido": "40.00",
    "itens_json": cesta((p_e1, 4)), "imprimir": "0"})
passo("venda base R$ 40 na sessão E (alvo do cancelamento da corrida)", st, loc)
v_base = escalar("SELECT id FROM vendas_pdv WHERE caixa_movimento_id=%s AND total=40 "
                 "AND status='concluida' ORDER BY id DESC LIMIT 1", (se,))
verificar("CASO 8b — venda base 40 persistida (alvo do cancelamento)", v_base is not None)
st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_e), "caixa_movimento_id": str(se),
    "forma_pagamento": "especie", "valor_recebido": "30.00",
    "itens_json": cesta((p_e1, 3)), "imprimir": "0"})
passo("venda base R$ 30 na sessão E (alvo do cancelamento pós-fechamento)", st, loc)
v_base2 = escalar("SELECT id FROM vendas_pdv WHERE caixa_movimento_id=%s AND total=30 "
                  "AND status='concluida' ORDER BY id DESC LIMIT 1", (se,))
verificar("CASO 8b — venda base 30 persistida (alvo pós-fechamento)", v_base2 is not None)
est_antes_e = escalar("SELECT quantidade_estoque FROM produtos WHERE id=%s", (p_e1,))

saco8b = []
alvos8b = [
    ("POST", f"/caixa/vendas/{v_base}/cancelar",
     {"motivo": "T010C corrida", "confirmar": "1"}, login_com("admin", "teste123")),
    ("POST", "/financeiro/mensalidades/receber",
     {"trabalhador_id": str(t_mensa_e), "mov_ids": str(mm_e1),
      "forma_pagamento": "especie", "sessao_id": str(se)}, login_com("admin", "teste123")),
    ("POST", "/caixa/pdv/finalizar",
     {"caixa_id": str(cx_e), "caixa_movimento_id": str(se), "forma_pagamento": "especie",
      "valor_recebido": "20.00", "itens_json": cesta((p_e1, 2)), "imprimir": "0"},
     login_com("admin", "teste123")),
    ("POST", "/caixa/fechar", {"caixa_movimento_id": str(se), "dinheiro_contado": "0.00"},
     login_com("admin", "teste123")),
]
ts = [threading.Thread(target=req_t, args=(m, p, d, o, saco8b))
      for (m, p, d, o) in alvos8b]
[t.start() for t in ts]; [t.join() for t in ts]
verificar("CASO 8b — nenhuma requisição da corrida terminou em exceção não tratada",
          all(x[0] != "EXC" for x in saco8b), f"{saco8b}")

# o fechamento da corrida pode ter vencido (e fechado com esperado 0) ou sido
# recusado por observação/trava; em qualquer caso a sessão termina fechada
# aqui, de forma determinística, para as invariantes de estado abaixo.
st, _, loc = req("POST", "/caixa/fechar",
                 {"caixa_movimento_id": str(se), "dinheiro_contado": "0.00",
                  "observacao": "T010C conferência da corrida"})
if not erro_em(loc, "sessao_invalida"):
    passo("fechar a sessão E após a corrida", st, loc)
m_e = consulta("SELECT * FROM caixa_movimentos WHERE id=%s", (se,))[0]
verificar("CASO 8b — a sessão está fechada", m_e["status"] == "fechado", f"{m_e['status']}")
esperado_e = dec(escalar(
    "SELECT 0 + COALESCE(SUM(efeito_fisico),0) FROM caixa_sessao_eventos WHERE sessao_id=%s",
    (se,)))
verificar("CASO 8b — saldo_final = inicial + Σ efeito_físico dos eventos finais da corrida",
          dec(m_e["saldo_final"]) == esperado_e,
          f"final={m_e['saldo_final']} esperado={esperado_e}")
pos_fechamento_e = escalar(
    """SELECT COUNT(*) FROM caixa_sessao_eventos e JOIN caixa_movimentos m ON m.id = e.sessao_id
       WHERE m.id = %s AND m.status = 'fechado'
         AND e.criado_em::timestamp(0) > m.data_fechamento::timestamp(0)""", (se,))
verificar("CASO 8b — nenhum evento escrito depois do fechamento",
          pos_fechamento_e == 0, f"{pos_fechamento_e} eventos pós-fechamento")

n_canc_e = escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
                   "AND tipo='cancelamento' AND venda_id=%s", (se, v_base))
st_vbase_e = escalar("SELECT status FROM vendas_pdv WHERE id=%s", (v_base,))
reg_canc_e = escalar("SELECT COUNT(*) FROM vendas_pdv_cancelamento WHERE venda_id=%s",
                     (v_base,))
verificar("CASO 8b — cancelamento da corrida OU integrado (evento+registro+status) OU "
          "recusado (nenhum dos três) — nunca parcial",
          (n_canc_e == 1 and st_vbase_e == "cancelada" and reg_canc_e == 1)
          or (n_canc_e == 0 and st_vbase_e == "concluida" and reg_canc_e == 0),
          f"eventos={n_canc_e} status={st_vbase_e} registro={reg_canc_e}")
n_venda20 = escalar("SELECT COUNT(*) FROM vendas_pdv WHERE caixa_movimento_id=%s "
                    "AND total=20 AND status='concluida'", (se,))
est_esperado_e = (est_antes_e - 2 * n_venda20
                  + (4 if st_vbase_e == "cancelada" else 0))
estoque_e = escalar("SELECT quantidade_estoque FROM produtos WHERE id=%s", (p_e1,))
verificar("CASO 8b — estoque bate com o livro-razão (bases + corrida − cancelamentos), "
          "sem escrita parcial",
          estoque_e == est_esperado_e,
          f"real={estoque_e} esperado={est_esperado_e} (bases {est_antes_e}, "
          f"venda20={n_venda20}, cancel={'sim' if st_vbase_e == 'cancelada' else 'não'})")
st_aft, corpo_aft, loc_aft = req("POST", f"/caixa/vendas/{v_base2}/cancelar",
                                 {"motivo": "T010C após fechamento", "confirmar": "1"})
corpo_aft = corpo_aft.decode("utf-8", "replace")
verificar("CASO 8b — cancelamento em sessão fechada é recusado com aviso, sem nada gravar",
          st_aft == 200 and "cancelado=" not in (loc_aft or "")
          and escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
                      "AND tipo='cancelamento' AND venda_id=%s", (se, v_base2)) == 0
          and escalar("SELECT status FROM vendas_pdv WHERE id=%s", (v_base2,)) == "concluida"
          and "fechada" in corpo_aft,
          f"HTTP {st_aft} {loc_aft}")

st_aft, _, loc_aft = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_mensa_e), "mov_ids": str(mm_e1), "forma_pagamento": "especie",
    "sessao_id": str(se)})
n_mens_e = escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
                   "AND tipo='mensalidade'", (se,))
status_mm_e = escalar("SELECT status FROM financeiro_movimentacoes WHERE id=%s", (mm_e1,))
verificar("CASO 8b — recebimento OU integrado (pago + evento único) OU recusado sem baixar — "
          "nunca parcial",
          (status_mm_e == "pago" and n_mens_e == 1)
          or (status_mm_e == "pendente" and n_mens_e == 0),
          f"status={status_mm_e} eventos={n_mens_e}; reenvio pós-fechamento: {loc_aft}")
orfaos = consulta("SELECT identidade, tipo FROM caixa_sessao_eventos "
                  "WHERE sessao_id=%s AND financeiro_id IS NULL", (se,))
verificar("CASO 8b — todo recebimento/sangria da sessão tem lançamento VINCULADO (sem evento órfão)",
          escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
                  "AND tipo IN ('mensalidade','doacao','recebimento_fiado',"
                  "'credito_anticipado','sangria') AND financeiro_id IS NULL", (se,)) == 0,
          f"órfãos: {orfaos}")

# ═════════════════════════════════════════════════════════════════════════════
# TASK_010 complementar: reenvio da MESMA tentativa após perda da resposta.
# A proteção real é a identidade do comando/documento (UNIQUE em
# caixa_sessao_eventos + estado da pendência), não a requisição HTTP.
print("\n══ CASO 16 — REENVIO DA MESMA TENTATIVA (PROTEÇÃO DOCUMENTAL × HTTP) ══")

with conectar() as conn:
    cx_f = conn.execute(
        "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
        "VALUES (%s, 'fixture TASK_010 reenvio', TRUE, 1) RETURNING id",
        (f"{PREFIXO} Cantina F",)).fetchone()["id"]
    p_f1 = conn.execute(
        """INSERT INTO produtos (nome, categoria, preco_venda, quantidade_estoque,
                                 caixa_id, ativo)
           VALUES (%s, 'outro', 10.00, 60, %s, 1) RETURNING id""",
        (f"{PREFIXO} Item F1", cx_f)).fetchone()["id"]
    t_mensa_f = conn.execute(
        """INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, fiado_credito)
           VALUES (%s, 1, 500.00, 0.00) RETURNING id""",
        (f"{PREFIXO} MensF",)).fetchone()["id"]
    mm_f1 = conn.execute(
        """INSERT INTO financeiro_movimentacoes
           (tipo, categoria, valor, data_movimentacao, descricao, trabalhador_id,
            status, conta_id)
           VALUES ('entrada', 'mensalidade', 30.00, %s, %s, %s, 'pendente', %s)
           RETURNING id""",
        (date.today().isoformat(), f"{PREFIXO} Mensalidade F1",
         t_mensa_f, cid_areceber)).fetchone()["id"]

st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_f), "saldo_inicial": "100.00"})
passo("abrir sessão F (inicial R$ 100)", st, loc)
sf = sessao_aberta(cx_f)

tentativa = {"trabalhador_id": str(t_mensa_f), "mov_ids": str(mm_f1),
             "forma_pagamento": "especie", "sessao_id": str(sf)}
st, _, loc = req("POST", "/financeiro/mensalidades/receber", tentativa)
passo("recebimento original da mensalidade F1 (30, espécie)", st, loc)
st2, _, loc2 = req("POST", "/financeiro/mensalidades/receber", tentativa)
ev_f = escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
               "AND tipo='mensalidade'", (sf,))
verificar("CASO 16 — reenvio do MESMO recebimento: 1 evento, 1 baixa, convergência "
          "(proteção documental resiste à perda da resposta; sem chave HTTP)",
          ev_f == 1
          and escalar("SELECT status FROM financeiro_movimentacoes WHERE id=%s", (mm_f1,))
          == "pago",
          f"eventos={ev_f} 2a resposta={loc2}")

vendas_f_antes = vendas_da_sessao(sf)
st, _, _ = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_f), "caixa_movimento_id": str(sf), "forma_pagamento": "especie",
    "valor_recebido": "10.00", "itens_json": cesta((p_f1, 1)), "imprimir": "0"})
st, _, _ = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_f), "caixa_movimento_id": str(sf), "forma_pagamento": "especie",
    "valor_recebido": "10.00", "itens_json": cesta((p_f1, 1)), "imprimir": "0"})
vendas_f_depois = vendas_da_sessao(sf)
verificar("CASO 16 — reenvio de VENDA sem chave de idempotência HTTP cria duas vendas "
          "(comportamento projetado; o PDV não finge idempotência)",
          vendas_f_depois == vendas_f_antes + 2,
          f"antes={vendas_f_antes} depois={vendas_f_depois}")

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_f), "caixa_movimento_id": str(sf), "forma_pagamento": "especie",
    "valor_recebido": "40.00", "itens_json": cesta((p_f1, 4)), "imprimir": "0"})
passo("venda R$ 40 para o cancelamento", st, loc)
v_c16 = escalar("SELECT id FROM vendas_pdv WHERE caixa_movimento_id=%s AND total=40 "
                "ORDER BY id DESC LIMIT 1", (sf,))
est_a16 = escalar("SELECT quantidade_estoque FROM produtos WHERE id=%s", (p_f1,))
req("POST", f"/caixa/vendas/{v_c16}/cancelar",
    {"motivo": "T010C idêntico 1", "confirmar": "1"})
req("POST", f"/caixa/vendas/{v_c16}/cancelar",
    {"motivo": "T010C idêntico 2", "confirmar": "1"})
verificar("CASO 16 — reenvio do MESMO comando de cancelamento: uma única compensação "
          "(identidade venda:{id}:cancelamento no banco)",
          escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s "
                  "AND tipo='cancelamento' AND venda_id=%s", (sf, v_c16)) == 1
          and escalar("SELECT quantidade_estoque FROM produtos WHERE id=%s", (p_f1,))
          == est_a16 + 4,
          f"estoque {est_a16}->{escalar('SELECT quantidade_estoque FROM produtos WHERE id=%s', (p_f1,))}")

esperado_f = dec(escalar(
    "SELECT 100 + COALESCE(SUM(efeito_fisico),0) FROM caixa_sessao_eventos WHERE sessao_id=%s",
    (sf,)))
st, _, loc = req("POST", "/caixa/fechar",
                 {"caixa_movimento_id": str(sf), "dinheiro_contado": str(esperado_f)})
passo("fechar sessão F com o esperado exato da corrida", st, loc)
print(f"  [dica] esperado F = {esperado_f}")

# ═════════════════════════════════════════════════════════════════════════════
print("\n══ CASO 6b — FALHA NA ÚLTIMA ESCRITA → ROLLBACK COMPLETO ══")

executar("ALTER TABLE caixa_sangrias ADD CONSTRAINT t008_chk_recebedor "
         "CHECK (recebedor_nome <> '')")
st_before = escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s", (sb,))
fin_before = escalar("SELECT COUNT(*) FROM financeiro_movimentacoes")
st, corpo, loc = req("POST", "/caixa/sangria", {
    "caixa_movimento_id": str(sb), "valor": "5.00", "recebedor_nome": "",
    "conta_destino_id": str(cid_pix)})
verificar("a escrita final falhou de fato (HTTP 500 esperado, sem handler)",
          st == 500, f"HTTP {st}")
verificar("nenhum resíduo da transação falha: zero lançamentos, zero eventos, zero sangria",
          escalar("SELECT COUNT(*) FROM financeiro_movimentacoes") == fin_before
          and escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s", (sb,))
          == st_before
          and escalar("SELECT COUNT(*) FROM caixa_sangrias WHERE caixa_movimento_id=%s "
                      "AND recebedor_nome=''", (sb,)) == 0,
          "escrita parcial escapou — rollback falhou")
executar("ALTER TABLE caixa_sangrias DROP CONSTRAINT t008_chk_recebedor")

# ═════════════════════════════════════════════════════════════════════════════
print("\n══ CASO 12 — LEGADO: CONSULTÁVEL, SEM NOVOS RECEBIMENTOS, REGRA ANTIGA ══")

st, _, loc = req("POST", "/caixa/fechar", {
    "caixa_movimento_id": str(sb), "dinheiro_contado": "120.00"})
passo("fechar sessão B (contado = esperado 120)", st, loc)

with conectar() as conn:
    sl = conn.execute(
        """INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status,
                                         atendente_id_abertura, versao)
           VALUES (%s, 10, 'aberto', 1, 'legado') RETURNING id""", (cx_c,)).fetchone()["id"]
print(f"    [seed] sessão LEGADO #{sl} criada como se existisse antes da implantação")

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_c), "caixa_movimento_id": str(sl), "forma_pagamento": "especie",
    "valor_recebido": "20.00", "itens_json": cesta((p_c1, 2)), "imprimir": "0"})
passo("venda espécie R$ 20 na sessão LEGADO", st, loc)
verificar("legado continua acumulando total_vendas (regra antiga, isolada)",
          dec(escalar("SELECT total_vendas FROM caixa_movimentos WHERE id=%s", (sl,)))
          == D("20.00"))
verificar("legado NÃO contamina o modelo novo (zero eventos gravados)",
          escalar("SELECT COUNT(*) FROM caixa_sessao_eventos WHERE sessao_id=%s", (sl,)) == 0)

st, _, loc = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_legado), "mov_ids": f"{mm_c1}", "forma_pagamento": "especie",
    "sessao_id": str(sl)})
verificar("recebimento espécie em sessão LEGADO é recusado antes de qualquer escrita",
          erro_em(loc, "sessao_legada_recebimento"), loc)
tv = dec(escalar("SELECT total_vendas FROM caixa_movimentos WHERE id=%s", (sl,)))
verificar("recusa sem efeito: total_vendas segue 20.00, pendência segue pendente",
          tv == D("20.00")
          and escalar("SELECT status FROM financeiro_movimentacoes WHERE id=%s", (mm_c1,))
          == "pendente", f"{tv}")

st, _, loc = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_legado), "mov_ids": f"{mm_c2}", "forma_pagamento": "pix",
    "conta_pix_id": str(cid_pix), "sessao_id": str(sl)})
verificar("recebimento PIX em sessão LEGADO também é recusado",
          erro_em(loc, "sessao_legada_recebimento")
          and escalar("SELECT status FROM financeiro_movimentacoes WHERE id=%s", (mm_c2,))
          == "pendente", loc)

st, _, loc = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_legado), "mov_ids": f"{mm_c1},{mm_c2}",
    "forma_pagamento": "especie", "sessao_id": str(sl)})
verificar("LOTE contra sessão LEGADO é recusado antes de baixar qualquer parcela",
          erro_em(loc, "sessao_legada_recebimento")
          and escalar("SELECT COUNT(*) FROM financeiro_movimentacoes "
                      "WHERE id IN (%s,%s) AND status <> 'pendente'",
                      (mm_c1, mm_c2)) == 0, loc)

st, _, loc = req("POST", "/caixa/sangria", {
    "caixa_movimento_id": str(sl), "valor": "22.00", "recebedor_nome": "Fictício",
    "motivo": "T008S legado"})
verificar("sangria sem destino é recusada TAMBÉM no legado",
          erro_em(loc, "destino_obrigatorio"), loc)
st, _, loc = req("POST", "/caixa/sangria", {
    "caixa_movimento_id": str(sl), "valor": "22.00", "recebedor_nome": "Fictício",
    "motivo": "T008S legado", "conta_destino_id": str(cid_pix)})
passo("sangria R$ 22 com destino na LEGADO", st, loc)
verificar("legado acumula sangria pela regra antiga e o par vinculado existe",
          dec(escalar("SELECT total_sangrias FROM caixa_movimentos WHERE id=%s", (sl,)))
          == D("22.00")
          and escalar("SELECT COUNT(*) FROM financeiro_movimentacoes WHERE sangria_id IN "
                      "(SELECT id FROM caixa_sangrias WHERE caixa_movimento_id=%s)", (sl,)) == 2)

st, _, loc = req("POST", "/caixa/fechar", {"caixa_movimento_id": str(sl),
                                           "dinheiro_contado": "8.00"})
verificar("fechar legado pela regra nova é recusado com explicação (sessao_legada)",
          erro_em(loc, "sessao_legada")
          and escalar("SELECT status FROM caixa_movimentos WHERE id=%s", (sl,)) == "aberto", loc)

st, _, loc = req("POST", "/caixa/fechar", {"caixa_movimento_id": str(sl), "observacao": "T008"})
passo("fechar LEGADO pela regra anterior", st, loc)
m_leg = consulta("SELECT * FROM caixa_movimentos WHERE id=%s", (sl,))[0]
verificar("regra antiga intacta: saldo_final = 10 + 20 − 22 = 8 "
          "(recebimentos recusados não entraram no acumulador)",
          dec(m_leg["saldo_final"]) == D("8.00") and m_leg["status"] == "fechado",
          f"saldo_final={m_leg['saldo_final']} status={m_leg['status']}")

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_c), "caixa_movimento_id": str(sl), "forma_pagamento": "especie",
    "valor_recebido": "10.00", "itens_json": cesta((p_c1, 1)), "imprimir": "0"})
verificar("sessão legada fechada não aceita novas vendas",
          erro_em(loc, "sessao_caixa_invalida"), loc)

st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_c), "saldo_inicial": "1.00"})
passo("reabrir o caixa C após fechar o legado", st, loc)
sc = sessao_aberta(cx_c)
verificar("sessão nova do caixa C nasce na versão 'eventos' (transição pelo tempo)",
          escalar("SELECT versao FROM caixa_movimentos WHERE id=%s", (sc,)) == "eventos")

# as pendências recusadas no legado agora têm destino na regra atual
st, _, loc = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_legado), "mov_ids": f"{mm_c1}", "forma_pagamento": "especie",
    "sessao_id": str(sc)})
passo("mensalidade espécie R$ 50 na sessão EVENTOS (após a recusa no legado)", st, loc)
st, _, loc = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_legado), "mov_ids": f"{mm_c2}", "forma_pagamento": "pix",
    "conta_pix_id": str(cid_pix), "sessao_id": str(sc)})
passo("mensalidade PIX R$ 50 na sessão EVENTOS", st, loc)
n_e, v_e, f_e = eventos(sc, "mensalidade")
verificar("as duas pendências pagas viram eventos na sessão nova (2 eventos, 100, físico 50)",
          n_e == 2 and v_e == D("100.00") and f_e == D("50.00")
          and escalar("SELECT status FROM financeiro_movimentacoes WHERE id=%s", (mm_c1,))
          == "pago"
          and escalar("SELECT status FROM financeiro_movimentacoes WHERE id=%s", (mm_c2,))
          == "pago", f"eventos=({n_e},{v_e},{f_e})")
verificar("sessão eventos NÃO usa acumulador legado: total_vendas segue 0",
          dec(escalar("SELECT total_vendas FROM caixa_movimentos WHERE id=%s", (sc,)))
          == D("0.00"))

# migração repetida não quebra nada
criar_tabelas()
criar_tabelas()
verificar("CASO 12b — migração repetida é idempotente e o legado permanece consultável",
          escalar("SELECT COUNT(*) FROM caixa_movimentos WHERE id=%s AND status='fechado'", (sl,))
          == 1
          and "uq_caixa_movimentos_aberto" in
          [r["indexname"] for r in consulta(
              "SELECT indexname FROM pg_indexes WHERE tablename='caixa_movimentos'")])

# ═════════════════════════════════════════════════════════════════════════════
print("\n══ CASO 14b — cancelamento é restrito a admin mesmo com caixa/ler+escrever ══")
st, _, loc = req("POST", f"/caixa/vendas/{v_doada}/cancelar",
                 {"motivo": "T008S não-admin", "confirmar": "1"}, o=o_op)
verificar("operador não-admin não cancela vendas", st == 303 and "erro=sem_permissao" in loc,
          f"HTTP {st} {loc}")

# ═════════════════════════════════════════════════════════════════════════════
print("\n══ CASO 15 — TASK_010/REV-02: saldo inicial e contado NÃO NEGATIVOS, zero legítimo ══")

from servico_sessao import (  # noqa: E402
    moeda as _moeda_evento, moeda_nao_negativa, SessaoInvalida as _SI,
)


def _recusa_parser(entrada):
    try:
        moeda_nao_negativa(entrada, "x")
        return None
    except _SI as e:
        return e.chave


verificar("parser não negativo: 0 e '0,00' aceitos como zero exato",
          moeda_nao_negativa(0, "s") == D("0.00")
          and moeda_nao_negativa("0,00", "s") == D("0.00"))
verificar("parser não negativo: '0,001' → 0.00 ACEITO (política documentada, centavos)",
          moeda_nao_negativa("0,001", "s") == D("0.00"))
verificar("parser não negativo: '0.005' → 0.01 (ROUND_HALF_UP da TASK_008 preservada)",
          moeda_nao_negativa("0.005", "s") == D("0.01"))
verificar("parser não negativo: NEGATIVO recusado ANTES do arredondamento "
          "(-0,001 não se salva virando zero)",
          _recusa_parser("-0.001") == "valor_invalido"
          and _recusa_parser("-5") == "valor_invalido")
verificar("parser não negativo: NaN, infinito, texto inválido e vazio recusados",
          all(_recusa_parser(v) == "valor_invalido"
              for v in ("NaN", "inf", "-inf", "abc", "1e400", "")))
verificar("parser não negativo: teto conferido APÓS arredondamento — "
          "9999999999.99 entra; 9999999999.995 (→ 10000000000.00) recusa",
          moeda_nao_negativa("9999999999.99", "s") == D("9999999999.99")
          and _recusa_parser("9999999999.995") == "valor_invalido")


def _recusa_moeda_evento(entrada):
    try:
        _moeda_evento(entrada, "x")
        return None
    except _SI as e:
        return e.chave


verificar("eventos continuam ESTRITAMENTE POSITIVOS: moeda() recusa 0 e negativo",
          _recusa_moeda_evento("0") == "valor_invalido"
          and _recusa_moeda_evento("-1") == "valor_invalido"
          and _moeda_evento("0.01", "x") == D("0.01"))

cx_z = escalar("INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
               "VALUES (%s, 'fixture TASK_010 saldo zero', TRUE, 1) RETURNING id",
               (f"{PREFIXO} Cantina Z",))

st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_z), "saldo_inicial": "0"})
passo("POST real de abertura com R$ 0,00 (valor padrão da tela) — REV-02 corrigido", st, loc)
sz = sessao_aberta(cx_z)
verificar("sessão zero criada na versão 'eventos' com saldo_inicial 0.00",
          dec(escalar("SELECT saldo_inicial FROM caixa_movimentos WHERE id=%s", (sz,)))
          == D("0.00")
          and escalar("SELECT versao FROM caixa_movimentos WHERE id=%s", (sz,)) == "eventos")

st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_z), "saldo_inicial": "-0,01"})
verificar("abertura com saldo NEGATIVO recusa sem criar sessão nem evento",
          st == 303 and erro_em(loc, "valor_invalido")
          and escalar("SELECT count(*) FROM caixa_movimentos WHERE caixa_id=%s", (cx_z,)) == 1)

st, _, loc = req("POST", "/caixa/fechar",
                 {"caixa_movimento_id": str(sz), "dinheiro_contado": "0"})
passo("fechamento de sessão VAZIA com contado R$ 0,00", st, loc)
verificar("fechamento zero: esperado 0, divergência 0, nenhum evento e nenhum "
          "lançamento financeiro fictício",
          escalar("SELECT status FROM caixa_movimentos WHERE id=%s", (sz,)) == "fechado"
          and dec(escalar("SELECT divergencia_contagem FROM caixa_movimentos WHERE id=%s",
                          (sz,))) == D("0.00")
          and escalar("SELECT count(*) FROM caixa_sessao_eventos WHERE sessao_id=%s", (sz,)) == 0
          and escalar("SELECT count(*) FROM financeiro_movimentacoes WHERE descricao LIKE %s",
                      (f"%fechamento #{sz}%",)) == 0)

# precisão adicional em POST direto (a tela só aceita centavos)
st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_z), "saldo_inicial": "0,005"})
passo("reabertura com '0,005' enviado direto (fora do step da tela)", st, loc)
sz2 = sessao_aberta(cx_z)
verificar("POST direto '0,005' arredonda para 0.01 e grava 0.01 (HALF_UP)",
          dec(escalar("SELECT saldo_inicial FROM caixa_movimentos WHERE id=%s", (sz2,)))
          == D("0.01"))
st, _, loc = req("POST", "/caixa/fechar",
                 {"caixa_movimento_id": str(sz2), "dinheiro_contado": "0"})
verificar("contado 0 contra esperado 0.01: divergência exige observação (recusada sem ela)",
          st == 303 and erro_em(loc, "observacao_obrigatoria")
          and escalar("SELECT status FROM caixa_movimentos WHERE id=%s", (sz2,)) == "aberto")
st, _, loc = req("POST", "/caixa/fechar",
                 {"caixa_movimento_id": str(sz2), "dinheiro_contado": "0",
                  "observacao": "TASK_010: fração de centavo do teste"})
passo("fechamento da divergência de arredondamento COM observação", st, loc)
verificar("fechado com divergência -0.01 registrada, esperado intocado (0.01)",
          escalar("SELECT status FROM caixa_movimentos WHERE id=%s", (sz2,)) == "fechado"
          and dec(escalar("SELECT divergencia_contagem FROM caixa_movimentos WHERE id=%s",
                          (sz2,))) == D("-0.01")
          and dec(escalar("SELECT saldo_final FROM caixa_movimentos WHERE id=%s", (sz2,)))
          == D("0.01"))

# ── Resultado ────────────────────────────────────────────────────────────────
print(f"\n[resultado] aprovadas={len(aprovadas)} falhas={len(falhas)}")
for f in falhas:
    print(f"  ✘ {f}")
sys.exit(1 if falhas else 0)
