#!/usr/bin/env python3
"""Reprodução do FIN-02 (TASK_007) pelas ROTAS REAIS autenticadas.

Mostra, com banco descartável e dados fictícios, que o cancelamento atual
apenas muda o status da venda e deixa para trás os efeitos reais:
estoque baixado, sessão de caixa inchada, recebimento de fiado órfão e
fechamento incoerente.

Destinos comprovados ANTES de qualquer escrita:
  • a conexão do próprio script (current_database + trava de isolamento);
  • o banco do servidor HTTP (linha que o app imprime no startup).
Configuração ausente ou divergente aborta o processo sem gravar nada.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import exigir_ambiente_de_teste, exigir_arquivo_de_configuracao  # noqa: E402

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9013")
PREFIXO = "T007REP"


def abortar(msg):
    print(f"\n[ABORTADO] {msg}", file=sys.stderr)
    raise SystemExit(2)


# ── 1. Prova dos dois destinos, antes de qualquer escrita ───────────────────
arq_env = os.environ.get("SHAMBALA_ENV_FILE")
if not arq_env:
    abortar("SHAMBALA_ENV_FILE ausente — recuso rodar sem arquivo de teste exclusivo.")
exigir_arquivo_de_configuracao(arq_env)

from banco import conectar, criar_tabelas  # noqa: E402  (carrega depois do env)

with conectar() as conn:
    info = exigir_ambiente_de_teste(conn)
print(f"[ok] conexão do script: {info}")

log_app = os.environ.get("SHAMBALA_APP_LOG", "/tmp/shambala_t007_app.log")
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


# ── 2. Admin fictício do banco descartável + login pela rota real ───────────
def garantir_admin():
    from rotas.auth import hash_senha
    with conectar() as conn:
        row = conn.execute("SELECT id FROM atendentes WHERE nome_usuario = 'admin'").fetchone()
        if row:
            conn.execute("UPDATE atendentes SET senha_hash = %s, ativo = 1 WHERE id = %s",
                         (hash_senha("teste123"), row["id"]))
        else:
            cur = conn.execute(
                """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
                   VALUES ('admin','Administrador',%s,1) RETURNING id""",
                (hash_senha("teste123"),))
            aid = cur.fetchone()["id"]
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


def req(method, path, data=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    try:
        with opener.open(r) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), \
                resp.headers.get("location") or ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("location") or ""


# ── 3. Fixtures fictícias ────────────────────────────────────────────────────
def seed():
    criar_tabelas()  # idempotente — o startup do app já rodou; garante schema p/ o script
    garantir_admin()
    st, _, _ = req("POST", "/login", {"nome_usuario": "admin", "senha": "teste123"})
    if st not in (200, 303):
        abortar(f"login falhou (HTTP {st})")
    with conectar() as conn:
        cur = conn.execute(
            "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
            "VALUES (%s, 'fixture repro TASK_007', TRUE, 1) RETURNING id",
            (f"{PREFIXO} Lanchonete",))
        caixa = cur.fetchone()["id"]

        def produto(nome, preco, estoque):
            cur = conn.execute(
                """INSERT INTO produtos (nome, categoria, preco_venda, quantidade_estoque,
                                         ativo, caixa_id)
                   VALUES (%s, 'repro', %s, %s, 1, %s) RETURNING id""",
                (f"{PREFIXO} {nome}", preco, estoque, caixa))
            return cur.fetchone()["id"]

        suco = produto("Suco", "12.50", 10)
        agua = produto("Agua", "3.00", 10)
        cur = conn.execute(
            "INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status) "
            "VALUES (%s, 0, 'aberto') RETURNING id", (caixa,))
        sessao = cur.fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, fiado_credito) "
            "VALUES (%s, 1, 100, 0) RETURNING id", (f"{PREFIXO} Trabalhador Fictcio",))
        trab = cur.fetchone()["id"]
    return {"caixa": caixa, "suco": suco, "agua": agua, "sessao": sessao, "trab": trab}


def linha(pid, nome, qtd, preco):
    from decimal import Decimal
    subtotal = float((Decimal(str(preco)) * qtd).quantize(Decimal("0.01")))
    return {"produto_id": pid, "quantidade": qtd, "nome": nome,
            "preco_unitario": float(preco), "subtotal": subtotal}


def finalizar(f, itens, forma="especie", recebido="0", doacao="0", trabalhador=0):
    # o navegador passa a sessão aberta no formulário; a rota aceita 0 e grava
    # a venda SEM vínculo — comportamento real que a política precisa cobrir.
    st, corpo, loc = req("POST", "/caixa/pdv/finalizar", {
        "caixa_id": f["caixa"], "caixa_movimento_id": f["sessao"],
        "forma_pagamento": forma, "valor_recebido": recebido,
        "itens_json": json.dumps(itens), "trabalhador_id": trabalhador,
        "imprimir": "1", "doacao_valor": doacao})
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query) if loc else {}
    vid = (qs.get("ok") or [None])[0]
    if not vid:
        abortar(f"finalização não produziu venda (HTTP {st}, destino {loc})")
    return int(vid)


def foto(titulo, f, venda_id=None, trab=None):
    with conectar() as conn:
        suco_q = conn.execute("SELECT quantidade_estoque FROM produtos WHERE id=%s",
                              (f["suco"],)).fetchone()["quantidade_estoque"]
        agua_q = conn.execute("SELECT quantidade_estoque FROM produtos WHERE id=%s",
                              (f["agua"],)).fetchone()["quantidade_estoque"]
        sess = conn.execute(
            "SELECT status, total_vendas FROM caixa_movimentos WHERE id=%s",
            (f["sessao"],)).fetchone()
        movs = conn.execute(
            """SELECT tipo, quantidade, referencia_id, motivo FROM estoque_movimentos
               WHERE produto_id IN (%s, %s) ORDER BY id""",
            (f["suco"], f["agua"])).fetchall()
        v = None
        if venda_id:
            v = conn.execute(
                """SELECT id, status, total, doacao_valor, forma_pagamento,
                          fiado_pago, fiado_credito_usado, caixa_movimento_id
                   FROM vendas_pdv WHERE id=%s""", (venda_id,)).fetchone()
        fin = conn.execute(
            """SELECT id, tipo, categoria, valor, descricao FROM financeiro_movimentacoes
               ORDER BY id""").fetchall()
        cred = None
        credmovs = []
        if trab:
            c = conn.execute("SELECT fiado_credito FROM trabalhadores WHERE id=%s",
                             (trab,)).fetchone()
            cred = c["fiado_credito"] if c else None
            credmovs = conn.execute(
                """SELECT tipo, valor, descricao FROM fiado_credito_mov
                   WHERE trabalhador_id=%s ORDER BY id""", (trab,)).fetchall()
    print(f"\n===== {titulo} =====")
    print(f"  estoque suco={suco_q}  agua={agua_q}   (esperado pós-venda honesta: suco 10-2=8, agua 10-1=9)")
    if v:
        print(f"  venda #{v['id']}: status={v['status']} forma={v['forma_pagamento']} "
              f"total={v['total']} doacao={v['doacao_valor']} "
              f"fiado_pago={v['fiado_pago']} credito_usado={v['fiado_credito_usado']} "
              f"sessao={v['caixa_movimento_id']}")
    print(f"  sessão {f['sessao']}: status={sess['status']} total_vendas={sess['total_vendas']}")
    print(f"  movimentos de estoque: {[(m['tipo'], m['quantidade'], m['referencia_id']) for m in movs]}")
    if cred is not None:
        print(f"  crédito antecipado do trabalhador: {cred}")
        print(f"  movimentos de crédito: {[(m['tipo'], m['valor'], m['descricao']) for m in credmovs]}")
    print(f"  financeiro: {[(m['id'], m['tipo'], m['valor'], m['descricao']) for m in fin]}")


def main():
    f = seed()
    print("\n########## CENÁRIO 1 — venda à vista (espécie) com doação, cancelada pela rota genérica ##########")
    v1 = finalizar(f, [linha(f["suco"], "Suco", 2, "12.50"),
                       linha(f["agua"], "Agua", 1, "3.00")],
                   recebido="50.00", doacao="2.00")
    foto("DEPOIS da venda honesta / ANTES do cancelamento (C1)", f, venda_id=v1)
    st, _, loc = req("POST", f"/caixa/vendas/{v1}/cancelar")
    print(f"\n  POST /caixa/vendas/{v1}/cancelar -> HTTP {st} -> {loc}")
    foto("DEPOIS do cancelamento genérico (C1)", f, venda_id=v1)
    with conectar() as conn:
        total_dia = conn.execute(
            """SELECT COALESCE(SUM(total + COALESCE(doacao_valor,0)),0) AS t
               FROM vendas_pdv WHERE caixa_movimento_id=%s AND status='concluida'""",
            (f["sessao"],)).fetchone()["t"]
    print(f"  soma 'concluida' da sessão agora: {total_dia} (a venda saiu dela), "
          "mas total_vendas e estoque NÃO foram tocados — inconsistência entre os dois")

    print("\n########## CENÁRIO 2 — fechar a sessão depois do cancelamento ##########")
    st, _, loc = req("POST", "/caixa/fechar",
                     {"caixa_movimento_id": f["sessao"], "observacao": "repro TASK_007"})
    print(f"  POST /caixa/fechar -> HTTP {st} -> {loc}")
    with conectar() as conn:
        sess = conn.execute(
            "SELECT status, total_vendas, saldo_final FROM caixa_movimentos WHERE id=%s",
            (f["sessao"],)).fetchone()
        entradas = conn.execute(
            """SELECT valor, descricao FROM financeiro_movimentacoes
               WHERE categoria='venda_pdv' ORDER BY id""").fetchall()
    print(f"  sessão fechada: total_vendas={sess['total_vendas']} saldo_final={sess['saldo_final']}")
    print(f"  entradas geradas no fechamento: {[(e['valor'], e['descricao']) for e in entradas]}")
    print("  => saldo_final inclui os R$ 30,00 da venda CANCELADA (total_vendas nunca foi revertido);")
    print("     as entradas por forma EXCLUEM a cancelada (só somam status='concluida'). Fechamento incoerente.")
    foto("DEPOIS do fechamento (C2)", f, venda_id=v1)

    print("\n########## CENÁRIO 3 — fiado com pagamento parcial, cancelado pela rota de fiado ##########")
    with conectar() as conn:
        cur = conn.execute(
            "INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status) VALUES (%s, 0, 'aberto') "
            "RETURNING id", (f["caixa"],))
        f["sessao"] = cur.fetchone()["id"]
    v3 = finalizar(f, [linha(f["suco"], "Suco", 1, "12.50")],
                   forma="fiado", trabalhador=f["trab"])
    foto("DEPOIS da venda fiado (C3)", f, venda_id=v3, trab=f["trab"])
    st, _, loc = req("POST", f"/caixa/fiados/{f['trab']}/pagar-divida",
                     {"valor": "10.00", "forma_pagamento": "especie"})
    print(f"  POST /caixa/fiados/{f['trab']}/pagar-divida valor=10,00 -> HTTP {st} -> {loc}")
    foto("DEPOIS do pagamento parcial de 10,00 (C3)", f, venda_id=v3, trab=f["trab"])
    st, _, loc = req("POST", f"/caixa/fiados/{v3}/cancelar")
    print(f"  POST /caixa/fiados/{v3}/cancelar -> HTTP {st} -> {loc}")
    foto("DEPOIS do cancelamento do fiado (C3)", f, venda_id=v3, trab=f["trab"])
    print("  => a venda foi cancelada, mas o recebimento de R$ 10,00 permaneceu em financeiro")
    print("     (entrada 'Recebimento fiado — T007REP…') e os R$ 10,00 viraram crédito antecipado")
    print("     SOLTO do trabalhador: nada liga o crédito nem o dinheiro à venda anulada.")

    print("\n########## CENÁRIO 4 — relatório de fiados conta venda cancelada ##########")
    v4 = finalizar(f, [linha(f["agua"], "Agua", 1, "3.00")],
                   forma="fiado", trabalhador=f["trab"])
    st, _, _ = req("POST", f"/caixa/vendas/{v4}/cancelar")
    print(f"  venda fiado #{v4} (3,00) criada e cancelada pela rota genérica (HTTP {st})")
    st, corpo, _ = req("GET", f"/financeiro/fiados?trabalhador_id={f['trab']}")
    nums = re.findall(r"R\$ ([\d]+\.[\d]{2})", corpo)
    print(f"  GET /financeiro/fiados?trabalhador_id={f['trab']} -> HTTP {st}; "
          f"cartões do relatório: gasto={nums[0] if nums else '?'} "
          f"pago={nums[1] if len(nums) > 1 else '?'} pendente={nums[2] if len(nums) > 2 else '?'} "
          f"crédito={nums[3] if len(nums) > 3 else '?'}")
    with conectar() as conn:
        linhas = conn.execute(
            """SELECT id, status, total, fiado_pago, fiado_credito_usado FROM vendas_pdv
               WHERE fiado_trabalhador_id = %s AND forma_pagamento = 'fiado'
               ORDER BY id""", (f["trab"],)).fetchall()
    print(f"  vendas fiado do trabalhador no banco: "
          f"{[(l['id'], l['status'], l['total'], l['fiado_pago'], l['fiado_credito_usado']) for l in linhas]}")
    print("  => o relatório soma TODAS as linhas fiado sem filtrar status: as canceladas continuam")
    print("     compondo gasto/pendente do trabalhador (e a #cancelada que USOU crédito antecipado")
    print("     consumiu R$ 3,00 do crédito sem nenhuma contrapartida registrada).")

    print("\n########## CONCLUSÃO DO FIN-02 ##########")
    print("  O cancelamento atual (POST /caixa/vendas/{id}/cancelar) apenas executa")
    print("  UPDATE vendas_pdv SET status='cancelada'. Persistem sem reversão:")
    print("    • estoque baixado (movimento 'saida' original sem contrapartida);")
    print("    • caixa_movimentos.total_vendas da sessão (fechamento fica incoerente);")
    print("    • recebimentos de fiado em financeiro e crédito antecipado solto;")
    print("    • relatórios (fiados) que contam a venda cancelada nos totais.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
