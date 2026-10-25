#!/usr/bin/env python3
"""Reprodução do FIN-04 (TASK_008) pelas ROTAS REAIS autenticadas.

Mostra, com banco descartável e dados fictícios (prefixo T008REP), que o
fechamento de caixa trata `total_vendas` como dinheiro físico quando ele
acumula vendas de qualquer forma (espécie, PIX, fiado), doações avulsas,
mensalidades e recebimentos de fiado — inclusive dentro de sessões de
OUTRO caixa (destino implícito por ID fixo) — e que o crédito antecipado
em espécie entra na gaveta sem nenhum lançamento financeiro (nexo com
FIN-03).

Destinos comprovados ANTES de qualquer escrita:
  • a conexão do próprio script (current_database + trava de isolamento);
  • o banco do servidor HTTP (linha que o app imprime no startup).
Configuração ausente ou divergente aborta o processo sem gravar nada.
"""
import os
import secrets
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import exigir_ambiente_de_teste, exigir_arquivo_de_configuracao  # noqa: E402

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9014")
PREFIXO = "T008REP"


def abortar(msg):
    print(f"\n[ABORTADO] {msg}", file=sys.stderr)
    raise sys.exit(2)


# ── 1. Prova dos dois destinos ───────────────────────────────────────────────
arq_env = os.environ.get("SHAMBALA_ENV_FILE")
if not arq_env:
    abortar("SHAMBALA_ENV_FILE ausente — recuso rodar sem arquivo de teste exclusivo.")
exigir_arquivo_de_configuracao(arq_env)

from banco import conectar, criar_tabelas, conta_id_por_tipo  # noqa: E402

with conectar() as conn:
    info = exigir_ambiente_de_teste(conn)
print(f"[ok] conexão do script: {info}")

log_app = os.environ.get("SHAMBALA_APP_LOG", "/tmp/shambala_t008_app.log")
if not os.path.exists(log_app):
    abortar(f"log do servidor ausente em {log_app}: não é possível provar o banco do HTTP.")
confirmacoes = re.findall(
    r"Ambiente descartável confirmado: \{.*?'banco': '([^']*)'",
    open(log_app, encoding="utf-8", errors="replace").read(),
)
if not confirmacoes:
    abortar("o app nunca imprimiu 'Ambiente descartável confirmado' — destino não provado.")
banco_servidor = confirmacoes[-1]
if banco_servidor != info["banco"]:
    abortar(f"o servidor HTTP usa o banco '{banco_servidor}', divergente do script "
            f"('{info['banco']}').")
print(f"[ok] servidor HTTP ({BASE}) confirmado no mesmo banco descartável: {banco_servidor}")


# ── 2. Admin fictício + login pela rota real ─────────────────────────────────
def garantir_admin():
    from rotas.auth import hash_senha
    with conectar() as conn:
        row = conn.execute("SELECT id FROM atendentes WHERE nome_usuario = 'admin'").fetchone()
        if row:
            conn.execute("UPDATE atendentes SET senha_hash = %s, ativo = 1 WHERE id = %s",
                         (hash_senha("teste123"), row["id"]))
        else:
            conn.execute(
                """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
                   VALUES ('admin','Administrador',%s,1)""", (hash_senha("teste123"),))
        conn.execute("INSERT INTO grupos (id, nome) VALUES (1,'Admin') ON CONFLICT (id) DO NOTHING")
        conn.execute(
            """INSERT INTO usuarios_grupos (usuario_id, grupo_id)
               SELECT id, 1 FROM atendentes WHERE nome_usuario = 'admin'
               ON CONFLICT DO NOTHING""")


class SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(SemRedirect, urllib.request.HTTPCookieProcessor(jar))


def _com_tentativa(data):
    """Cada POST de teste é uma tentativa NOVA e legítima (TASK_012 §2).

    As rotas financeiras exigem o identificador da tentativa; quem quiser
    provar reenvio/duplicidade passa `idempotencia` explicitamente.
    """
    if isinstance(data, dict) and "idempotencia" not in data:
        return {**data, "idempotencia": secrets.token_hex(16)}
    return data


def req(method, path, data=None):
    data = _com_tentativa(data)
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    try:
        with opener.open(r) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), \
                resp.headers.get("location") or ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("location") or ""


def ok_passo(nome, st, loc=""):
    marca = "✔" if st in (200, 303) else "✘"
    print(f"  {marca} {nome} → HTTP {st}" + (f"  redireciona: {loc}" if loc else ""))
    if st not in (200, 303):
        abortar(f"passo {nome} falhou (HTTP {st})")


def dec(v):
    return Decimal(str(v))


def sessao_de(sid):
    with conectar() as conn:
        return conn.execute(
            "SELECT * FROM caixa_movimentos WHERE id = %s", (sid,)).fetchone()


def mostrar_sessao(titulo, sid):
    m = sessao_de(sid)
    print(f"    {titulo}: caixa={m['caixa_id']} status={m['status']} "
          f"inicial={dec(m['saldo_inicial']):>8} total_vendas={dec(m['total_vendas']):>8} "
          f"sangrias={dec(m['total_sangrias']):>7} saldo_final={m['saldo_final']}")
    return m


# ── 3. Fixtures fictícias ────────────────────────────────────────────────────
criar_tabelas()
garantir_admin()
st, _, _ = req("POST", "/login", {"nome_usuario": "admin", "senha": "teste123"})
if st not in (200, 303):
    abortar(f"login falhou (HTTP {st})")

with conectar() as conn:
    # caixa do operador (NÃO é o id=2): Lanchonete seed é id 2 num banco novo
    cur = conn.execute(
        "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
        "VALUES (%s, 'fixture repro TASK_008', TRUE, 1) RETURNING id",
        (f"{PREFIXO} Cantina Norte",))
    cx_operador = cur.fetchone()["id"]
    lh_id = conn.execute(
        "SELECT id FROM caixas WHERE nome = 'Lanchonete' ORDER BY id LIMIT 1"
    ).fetchone()["id"]
    print(f"[seed] caixa do operador id={cx_operador}; Lanchonete (id fixo dos fluxos "
          f"implícitos) id={lh_id}")

    def produto(caixa, nome, preco, estoque):
        cur = conn.execute(
            """INSERT INTO produtos (nome, categoria, preco_venda, quantidade_estoque,
                                     caixa_id, ativo)
               VALUES (%s, 'outro', %s, %s, %s, 1) RETURNING id""",
            (f"{PREFIXO} {nome}", Decimal(preco), estoque, caixa))
        return cur.fetchone()["id"]

    p_suco = produto(cx_operador, "Suco", "12.50", 10)     # 2× → 25,00 espécie
    p_bolo = produto(cx_operador, "Bolo", "30.00", 5)      # 1× → 30,00 PIX
    p_prato = produto(cx_operador, "Prato", "40.00", 5)    # 1× → 40,00 fiado
    p_pizza = produto(cx_operador, "Pizza", "25.00", 5)    # 1× → 25,00 consumo de crédito

    def trab(nome, limite="500.00", credito="0.00", mensalidade="0.00"):
        cur = conn.execute(
            """INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, fiado_credito,
                                          valor_mensalidade, dia_vencimento)
               VALUES (%s, 1, %s, %s, %s, 10) RETURNING id""",
            (f"{PREFIXO} {nome}", Decimal(limite), Decimal(credito), Decimal(mensalidade)))
        return cur.fetchone()["id"]

    t_fiado = trab("Fiando")
    t_mensa = trab("Mensalista", mensalidade="60.00")

    from datetime import date
    hoje = date.today()
    cid_areceber = conta_id_por_tipo(conn, "areceber")
    cur = conn.execute(
        """INSERT INTO financeiro_movimentacoes
           (tipo, categoria, valor, data_movimentacao, descricao, trabalhador_id,
            status, conta_id)
           VALUES ('entrada', 'mensalidade', %s, %s, %s, %s, 'pendente', %s)
           RETURNING id""",
        (Decimal("60.00"), hoje.isoformat(),
         f"T008REP Mensalidade {hoje:%Y-%m}", t_mensa, cid_areceber))
    mov_mensalidade = cur.fetchone()["id"]

print("\n── passos pelas rotas reais ──")

# 1. duas sessões abertas: a do operador e a da Lanchonete (destino implícito)
st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(cx_operador), "saldo_inicial": "100"})
ok_passo("POST /caixa/abrir (Cantina, inicial R$ 100)", st, loc)
st, _, loc = req("POST", "/caixa/abrir", {"caixa_id": str(lh_id), "saldo_inicial": "0"})
ok_passo("POST /caixa/abrir (Lanchonete, inicial R$ 0)", st, loc)
with conectar() as conn:
    s_op = conn.execute(
        "SELECT id FROM caixa_movimentos WHERE caixa_id=%s AND status='aberto' "
        "ORDER BY id DESC LIMIT 1", (cx_operador,)).fetchone()["id"]
    s_lh = conn.execute(
        "SELECT id FROM caixa_movimentos WHERE caixa_id=%s AND status='aberto' "
        "ORDER BY id DESC LIMIT 1", (lh_id,)).fetchone()["id"]
print(f"    sessão do operador = {s_op}; sessão Lanchonete = {s_lh}")


def cesta(*pares):
    import json as _json
    return _json.dumps([{"produto_id": pid, "quantidade": q, "nome": "", "preco_unitario": 0}
                        for pid, q in pares])


st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_operador), "caixa_movimento_id": str(s_op),
    "forma_pagamento": "especie", "valor_recebido": "25.00",
    "itens_json": cesta((p_suco, 2)), "imprimir": "0"})
ok_passo("venda 1: 2×Suco = R$ 25,00 ESPÉCIE (na sessão do operador)", st, loc)

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_operador), "caixa_movimento_id": str(s_op),
    "forma_pagamento": "pix", "itens_json": cesta((p_bolo, 1)), "imprimir": "0"})
ok_passo("venda 2: 1×Bolo = R$ 30,00 PIX (na sessão do operador)", st, loc)

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_operador), "caixa_movimento_id": str(s_op),
    "forma_pagamento": "fiado", "trabalhador_id": str(t_fiado),
    "itens_json": cesta((p_prato, 1)), "imprimir": "0"})
ok_passo("venda 3: 1×Prato = R$ 40,00 FIADO não pago (sessão do operador)", st, loc)
with conectar() as conn:
    v3 = conn.execute(
        "SELECT id FROM vendas_pdv WHERE fiado_trabalhador_id = %s "
        "AND forma_pagamento = 'fiado' ORDER BY id DESC LIMIT 1",
        (t_fiado,)).fetchone()["id"]

st, _, loc = req("POST", f"/caixa/fiados/credito/{t_fiado}",
                 {"valor": "25.00", "descricao": "T008REP adiantamento em espécie"})
ok_passo("crédito antecipado R$ 25,00 em ESPÉCIE depositado na gaveta do operador", st, loc)

st, _, loc = req("POST", "/caixa/pdv/finalizar", {
    "caixa_id": str(cx_operador), "caixa_movimento_id": str(s_op),
    "forma_pagamento": "fiado", "trabalhador_id": str(t_fiado),
    "itens_json": cesta((p_pizza, 1)), "imprimir": "0"})
ok_passo("venda 4: 1×Pizza = R$ 25,00 paga com CRÉDITO ANTERIOR (nada novo em dinheiro)", st, loc)

st, _, loc = req("POST", "/financeiro/mensalidades/receber", {
    "trabalhador_id": str(t_mensa), "mov_ids": str(mov_mensalidade),
    "forma_pagamento": "especie"})
ok_passo("mensalidade R$ 60,00 recebida em ESPÉCIE", st, loc)

st, _, loc = req("POST", "/caixa/doacao", {"forma": "especie", "valor": "10.00",
                                           "descricao": "T008REP doação espécie"})
ok_passo("doação avulsa R$ 10,00 ESPÉCIE", st, loc)
st, _, loc = req("POST", "/caixa/doacao", {"forma": "pix", "valor": "15.00",
                                           "descricao": "T008REP doação PIX"})
ok_passo("doação avulsa R$ 15,00 PIX", st, loc)

st, _, loc = req("POST", f"/caixa/fiados/{v3}/pagar", {"forma_pagamento": "especie"})
ok_passo("quitação do fiado da venda 3: R$ 40,00 ESPÉCIE recebidos", st, loc)

st, _, loc = req("POST", "/caixa/sangria", {
    "caixa_movimento_id": str(s_op), "valor": "30.00",
    "motivo": "T008REP reforço tesouraria", "recebedor_nome": "Caixa central (teste)"})
ok_passo("sangria R$ 30,00 da sessão do operador", st, loc)

print("\n── estado das sessões ANTES do fechamento ──")
mostrar_sessao("sessão do operador", s_op)
mostrar_sessao("sessão Lanchonete", s_lh)
print("    gaveta física real do operador: 100 + 25 (espécie) + 25 (adiantamento "
      "espécie, NUNCA lançado) − 30 (sangria) = R$ 120,00")
print("    → o sistema NÃO sabe dos R$ 25 de crédito antecipado: nenhuma linha em "
      "financeiro_movimentacoes, nenhum efeito de sessão (caixa.py:1477)")

print("\n── fechamento das duas sessões pelas rotas ──")
st, corpo, loc = req("GET", f"/caixa/fechar?caixa_movimento_id={s_op}")
esperado_op = re.search(r"Saldo (esperado|final)[^0-9]*([0-9.,]+)", corpo)
print(f"  ✔ GET /caixa/fechar (operador) → HTTP {st}")
st, _, loc = req("POST", "/caixa/fechar", {"caixa_movimento_id": str(s_op),
                                           "observacao": "T008REP fechamento teste"})
ok_passo("POST /caixa/fechar (operador)", st, loc)
st, _, loc = req("POST", "/caixa/fechar", {"caixa_movimento_id": str(s_lh),
                                           "observacao": "T008REP fechamento teste"})
ok_passo("POST /caixa/fechar (Lanchonete)", st, loc)

print("\n── DIVERGÊNCIAS DEMONSTRADAS ──")
m = mostrar_sessao("sessão do operador FECHADA", s_op)
esperado_fisico = dec(m["saldo_inicial"]) + Decimal("25.00") - dec(m["total_sangrias"])
print(f"    esperado na regra nova: inicial 100,00 + espécie vendida 25,00 − sangria 30,00 "
      f"= R$ {esperado_fisico:.2f}")
print(f"    saldo_final do sistema: R$ {dec(m['saldo_final']):.2f} "
      f"→ inflado em R$ {dec(m['saldo_final']) - esperado_fisico:.2f} "
      "(PIX 30,00 + fiado 40,00 + consumo de crédito 25,00 contam como dinheiro físico)")
ml = mostrar_sessao("sessão Lanchonete FECHADA", s_lh)
print(f"    a Lanchonete (onde NADA foi operado) recebeu em sua gaveta contábil: mensalidade 60 + "
      f"doação 10 + doação PIX 15 = total_vendas R$ {dec(ml['total_vendas']):.2f} "
      f"(destino implícito por ID fixo, caixa_id={lh_id}); o PIX de 15,00 também virou físico")

with conectar() as conn:
    linhas = conn.execute(
        """SELECT tipo, categoria, valor, descricao, forma_pagamento, conta_id
           FROM financeiro_movimentacoes
           WHERE descricao LIKE %s OR categoria IN ('venda_pdv','recebimento_fiado','doacao','mensalidade')
           ORDER BY id""", (f"%{PREFIXO}%",)).fetchall()
print("\n    lançamentos financeiros depois dos fechamentos:")
for l in linhas:
    print(f"      {l['tipo']:<7} {l['categoria']:<18} {dec(l['valor']):>9,.2f}  "
          f"forma={l['forma_pagamento'] or '—':<7} conta={l['conta_id'] or '—'}  "
          f"{l['descricao'][:72]}")
print("\n── conclusão da reprodução (FIN-04) ──")
print("    1. total_vendas soma TODAS as formas de venda e também doação/mensalidade/"
      "quitação de fiado, e o fechamento usa esse acúmulo como saldo físico da gaveta.")
print("    2. PIX entra como dinheiro físico (venda 30,00 + doação 15,00).")
print("    3. fiado não pago vira entrada 'a receber' no fechamento E, quando quitado, "
      "gera segunda entrada 'recebimento_fiado' — a mesma dívida contada duas vezes.")
print("    4. crédito antecipado em espécie não é lançado em lugar nenhum (omissão "
      "FIN-03 no fluxo) e o consumo desse crédito vira venda somada à gaveta de novo.")
print("    5. mensalidade/doação/recebimento de fiado escolhem implicitamente a sessão "
      "aberta do caixa id=2, independentemente do caixa em que o operador estava.")
