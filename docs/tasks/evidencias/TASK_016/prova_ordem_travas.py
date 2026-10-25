"""TASK_016 — coordenação transacional recebimento × inativação.

Requisito: recebimento e inativação coordenados pela MESMA linha
(`contas_financeiras`), com ordem consistente:

  O1  recebimento obtém a trava primeiro → conclui; a inativação espera.
  O2  inativação obtém a trava primeiro e confirma → o recebimento espera, relê a
      conta JÁ inativa e RECUSA, sem nenhum efeito.

Nenhuma trava durante a interação do usuário: a validação roda dentro da
transação de escrita, nunca na renderização do formulário.

Determinismo: a ordem é imposta por uma transação-âncora que segura a linha da
conta e por filas de quem chega primeiro — sem `sleep` como prova (o `sleep`
curto só garante que a thread já está enfileirada no banco). O caminho de
inativação é o REAL: `POST /configuracoes/contas/{id}/toggle` por HTTP.

Por que O2 termina em recusa: no PostgreSQL, o `SELECT ... FOR UPDATE` que
esperou a trava de um `UPDATE` já confirmado relê a versão nova da linha
(EvalPlanQual), então vê `ativo = 0`.
"""
import os
import sys
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(AQUI))))
sys.path.insert(0, RAIZ)
from dotenv import load_dotenv

load_dotenv(os.path.join(RAIZ, ".env.test"), override=True)
os.environ["SHAMBALA_MODO_TESTE"] = "1"
import http.cookiejar  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

from banco import _ConnCompat, _obter_pool, conectar  # noqa: E402
from servico_sessao import SessaoInvalida, conta_de_destino  # noqa: E402

BASE = os.environ.get("SHAMBALA_TEST_BASE", "http://127.0.0.1:9011")
# a prova roda contra o app descartável do runner; exige o mesmo ambiente de teste
PREFIXO = "T016L"
res = []


def verificar(nome, cond, det=""):
    res.append((nome, bool(cond), det))
    print(f"  {'✔' if cond else '✘'} {nome}" + (f" — {det}" if not cond else ""))


# ── o caminho REAL de inativação ───────────────────────────────────────────
class _NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(_NR, urllib.request.HTTPCookieProcessor(jar))


def autenticar():
    try:
        op.open(urllib.request.Request(
            BASE + "/login", method="POST",
            data=urllib.parse.urlencode(
                {"nome_usuario": "admin", "senha": "teste123"}).encode()))
    except urllib.error.HTTPError:
        pass


def toggle_http(conta_id):
    try:
        r = op.open(urllib.request.Request(
            BASE + f"/configuracoes/contas/{conta_id}/toggle", method="POST",
            data=b""), timeout=30)
        return r.status
    except urllib.error.HTTPError as e:
        return e.code


# ── infraestrutura ─────────────────────────────────────────────────────────
def conexao_dedicada():
    crua = _obter_pool().getconn()
    from psycopg2.extras import RealDictCursor
    crua.cursor_factory = RealDictCursor
    return crua, _ConnCompat(crua)


def conta(nome, tipo="especie"):
    with conectar() as conn:
        r = conn.execute("SELECT id FROM contas_financeiras WHERE nome=%s",
                         (nome,)).fetchone()
        if r:
            conn.execute("UPDATE contas_financeiras SET ativo=1, tipo=%s WHERE id=%s",
                         (tipo, r["id"]))
            return r["id"]
        return conn.execute(
            """INSERT INTO contas_financeiras (nome, tipo, ativo)
               VALUES (%s, %s, 1) RETURNING id""", (nome, tipo)).fetchone()["id"]


def esta_ativo(cid):
    with conectar() as conn:
        return conn.execute("SELECT ativo FROM contas_financeiras WHERE id=%s",
                            (cid,)).fetchone()["ativo"]


def n_mov(desc):
    with conectar() as conn:
        return conn.execute(
            "SELECT count(*) AS c FROM financeiro_movimentacoes WHERE descricao=%s",
            (desc,)).fetchone()["c"]


def recebimento(cid, desc, pausa=None):
    """Transação de escrita do recebimento: valida (sob trava) e grava.

    `pausa` (tupla evento/pronto) segura a transação DEPOIS da validação e ANTES
    do commit — é assim que se prova, sem sleep, que a trava da conta já está
    tomada quando a inativação chega.
    """
    crua, conn = conexao_dedicada()
    try:
        cur = crua.cursor()
        try:
            destino = conta_de_destino(conn, "especie", cid, exigir_escolha=False)
        except SessaoInvalida as e:
            cur.close()
            crua.rollback()
            return ("recusa", e.chave)
        if pausa:
            pausa[1].set()
            pausa[0].wait(30)
        cur.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, status,
                conta_id, forma_pagamento)
               VALUES ('entrada', 'doacao', 6.00, '2026-10-25', %s, 'pago', %s,
                       'especie')""", (desc, destino))
        cur.close()
        crua.commit()
        return ("gravou", destino)
    except Exception as e:
        crua.rollback()
        return ("erro", f"{type(e).__name__}: {e}")
    finally:
        _obter_pool().putconn(crua)


def ancora_linha(cid):
    """Segura a trava da conta e devolve os controles para soltá-la."""
    travada = threading.Event()
    soltar = threading.Event()
    liberada = threading.Event()

    def run():
        crua, conn = conexao_dedicada()
        try:
            cur = crua.cursor()
            cur.execute("SELECT id FROM contas_financeiras WHERE id=%s FOR UPDATE",
                        (cid,))
            travada.set()
            soltar.wait(30)
            cur.close()
            crua.commit()
        finally:
            _obter_pool().putconn(crua)
            liberada.set()

    t = threading.Thread(target=run)
    t.start()
    return travada, soltar, t, liberada


def limpar(cids):
    with conectar() as conn:
        conn.execute("DELETE FROM financeiro_movimentacoes WHERE descricao LIKE %s",
                     (PREFIXO + "%",))
        conn.execute("DELETE FROM contas_financeiras WHERE id = ANY(%s)",
                     (cids or [-1],))


def main():
    autenticar()
    c1 = conta(f"{PREFIXO} Ordem 1")
    c2 = conta(f"{PREFIXO} Ordem 2")
    # a conta c1 não pode ter sido inativada numa execução anterior
    with conectar() as cc:
        cc.execute("UPDATE contas_financeiras SET ativo=1 WHERE id = ANY(%s)",
                   ([c1, c2],))
    print(f"contas de teste: O1={c1} O2={c2}")

    # ══ O1: o RECEBIMENTO chega primeiro e obtém a trava ══════════════════
    print("\n── O1 recebimento obtém a trava primeiro")
    saida = {}
    pausa = (threading.Event(), threading.Event())
    t_rec = threading.Thread(
        target=lambda: saida.update(
            {"rec": recebimento(c1, f"{PREFIXO} O1", pausa=pausa)}))
    t_rec.start()
    # barreira: a transação do recebimento JÁ validou e segura a trava da conta
    if not pausa[1].wait(20):
        print("  ✘ o recebimento não chegou ao ponto de pausa")
        return 1
    t_tog = threading.Thread(
        target=lambda: saida.update({"tog": toggle_http(c1)}))
    t_tog.start()
    time.sleep(0.6)
    bloqueado = t_tog.is_alive()
    verificar("O1.1 a inativação ficou BLOQUEADA na trava da conta",
              bloqueado, "o toggle terminou com o recebimento ainda em voo")
    pausa[0].set()
    t_rec.join(30)
    t_tog.join(30)
    rec, tog = saida.get("rec"), saida.get("tog")
    gravou1 = n_mov(f"{PREFIXO} O1")
    print(f"     recebimento: {rec} | lançamentos: {gravou1} | toggle: {tog} | "
          f"ativo agora: {esta_ativo(c1)}")
    verificar("O1.2 o recebimento CONCLUIU e gravou exatamente uma vez",
              rec is not None and rec[0] == "gravou" and gravou1 == 1,
              f"{rec} gravados={gravou1}")
    verificar("O1.3 gravou na conta certa", rec is not None and rec[1] == c1, str(rec))
    verificar("O1.4 a inativação só aconteceu DEPOIS (conta terminou inativa)",
              esta_ativo(c1) == 0, f"ativo={esta_ativo(c1)}")

    # ══ O2: a INATIVAÇÃO chega primeiro e confirma ════════════════════════
    print("\n── O2 inativação obtém a trava primeiro e confirma")
    travada, soltar, t_anc, liberada = ancora_linha(c2)
    if not travada.wait(15):
        print("  ✘ não consegui segurar a linha da conta")
        return 1
    fila = []
    t_tog2 = threading.Thread(
        target=lambda: fila.append(("toggle", toggle_http(c2))))
    t_tog2.start()
    time.sleep(0.4)   # o toggle já está enfileirado no banco
    t_rec2 = threading.Thread(
        target=lambda: fila.append(("recebimento", recebimento(c2, f"{PREFIXO} O2"))))
    t_rec2.start()
    time.sleep(0.4)   # o recebimento já está enfileirado, ATRÁS do toggle
    ordem_chegada = [n for n, _ in fila] or ["(ambos esperando)"]
    print(f"     chegada (concluídos até aqui): {ordem_chegada}")
    soltar.set()          # o toggle pega a linha, inativa e commita; depois o outro
    t_tog2.join(30)
    t_rec2.join(30)
    t_anc.join(15)
    rec2 = dict(fila).get("recebimento")
    tog2 = dict(fila).get("toggle")
    gravou2 = n_mov(f"{PREFIXO} O2")
    print(f"     inativação: {tog2} | recebimento: {rec2} | lançamentos: {gravou2} | "
          f"ativo: {esta_ativo(c2)}")
    verificar("O2.1 a inativação confirmou e a conta terminou inativa",
              esta_ativo(c2) == 0, f"ativo={esta_ativo(c2)}")
    verificar("O2.2 o recebimento ESPEROU e RECUSOU por conta inativa",
              rec2 is not None and rec2[0] == "recusa" and rec2[1] == "conta_inativa",
              str(rec2))
    verificar("O2.3 NENHUM lançamento foi gravado (sem efeitos)", gravou2 == 0,
              f"gravados={gravou2}")

    limpar([c1, c2])
    falhas = [n for n, ok, _ in res if not ok]
    print(f"\n[resultado] aprovadas={len(res) - len(falhas)} falhas={len(falhas)}")
    for f in falhas:
        print(f"  ✘ {f}")
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
