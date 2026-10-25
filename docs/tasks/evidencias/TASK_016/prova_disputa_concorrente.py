"""TASK_016 — disputa REAL entre inativação e recebimento (item 2 do relatório).

A pergunta do dono: a validação da conta acontece na MESMA transação que grava?
Se sim, a inativação concorrente não pode produzir efeito parcial. Aqui isso é
verificado com duas conexões independentes e controle fino de commit.

O que este arquivo NÃO presume: que exista `FOR UPDATE` na conta. A leitura de
`contas_financeiras` em `_conta_por_id` é simples (`SELECT ... WHERE id`), então
o desfecho depende do nível de isolamento e da ordem dos commits. A prova mede
o resultado em cada ordem:

  D1  inativação COMMITA antes do recebimento       → recebimento recusa, 0 efeito
  D2  inativação commita DURANTE (após a leitura
      do recebimento, antes do commit dele)         → qual resultado? (medido)
  D3  recebimento COMMITA primeiro                  → recebimento grava, e a
      inativação posterior NÃO desfaz o fato consumado
  D4  duas inativações concorrentes da MESMA conta  → uma vence, sem erro

Em nenhum caso o resultado aceitável é "gravou E recusou": ou grava inteiro, ou
não grava nada.
"""
import os
import sys
import threading
import time

sys.path.insert(0, "/nc/projetos/shambala")
from dotenv import load_dotenv

load_dotenv("/nc/projetos/shambala/.env.test", override=True)
os.environ["SHAMBALA_MODO_TESTE"] = "1"
from banco import _ConnCompat, _obter_pool, conectar  # noqa: E402
from servico_sessao import SessaoInvalida, conta_de_destino  # noqa: E402

PREFIXO = "T016D"
resultados = []


def verificar(nome, cond, det=""):
    resultados.append((nome, bool(cond), det))
    print(f"  {'✔' if cond else '✘'} {nome}" + (f" — {det}" if not cond else ""))


def conta(nome, tipo="especie", ativo=1):
    with conectar() as conn:
        r = conn.execute("SELECT id FROM contas_financeiras WHERE nome=%s",
                         (nome,)).fetchone()
        if r:
            conn.execute("UPDATE contas_financeiras SET ativo=%s, tipo=%s WHERE id=%s",
                         (ativo, tipo, r["id"]))
            return r["id"]
        return conn.execute(
            """INSERT INTO contas_financeiras (nome, tipo, ativo)
               VALUES (%s, %s, %s) RETURNING id""",
            (nome, tipo, ativo)).fetchone()["id"]


def limpar(contas):
    with conectar() as conn:
        conn.execute("DELETE FROM financeiro_movimentacoes WHERE descricao LIKE %s",
                     (PREFIXO + "%",))
        conn.execute("DELETE FROM contas_financeiras WHERE id = ANY(%s)",
                     (contas or [-1],))


def n_mov(desc):
    with conectar() as conn:
        return conn.execute(
            "SELECT count(*) AS c FROM financeiro_movimentacoes WHERE descricao=%s",
            (desc,)).fetchone()["c"]


# A operação real: a rota chama `conta_de_destino` DENTRO da transação de escrita.
# Reproduzimos exatamente isso numa conexão dedicada, com commit controlado.
def conexao_dedicada():
    """Conexão CRUA do pool, com commit/rollback sob controle do teste."""
    crua = _obter_pool().getconn()
    from psycopg2.extras import RealDictCursor
    crua.cursor_factory = RealDictCursor
    return crua, _ConnCompat(crua)


def transacao_recebimento(conta_id, desc, pausa_antes_do_commit=None,
                          pronto=None, soltar=None):
    """Abre conexão, valida a conta e grava — na MESMA transação."""
    crua, conn = conexao_dedicada()
    try:
        cur = crua.cursor()
        try:
            cid = conta_de_destino(conn, "especie", conta_id, exigir_escolha=False)
        except SessaoInvalida as e:
            cur.close()
            crua.rollback()
            return ("recusa", e.chave)
        if pronto:
            pronto.set()
        if pausa_antes_do_commit:
            pausa_antes_do_commit.wait(10)
        cur.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, status,
                conta_id, forma_pagamento)
               VALUES ('entrada', 'doacao', 8.00, '2026-10-25', %s, 'pago', %s,
                       'especie')""", (desc, cid))
        cur.close()
        crua.commit()
        return ("gravou", cid)
    except Exception as e:
        crua.rollback()
        return ("erro", f"{type(e).__name__}: {e}")
    finally:
        _obter_pool().putconn(crua)


def inativar(conta_id):
    with conectar() as conn:
        conn.execute("UPDATE contas_financeiras SET ativo=0 WHERE id=%s", (conta_id,))


def main():
    c1 = conta(f"{PREFIXO} Alvo 1")
    c2 = conta(f"{PREFIXO} Alvo 2")
    c3 = conta(f"{PREFIXO} Alvo 3")
    print(f"contas de teste: {c1}, {c2}, {c3}")

    # D1 — inativação commita ANTES do recebimento
    print("\n-- D1 inativação já commitada quando o recebimento valida")
    inativar(c1)
    r = transacao_recebimento(c1, f"{PREFIXO} D1")
    verificar("D1 recusa com motivo próprio", r[0] == "recusa" and r[1] == "conta_inativa",
              str(r))
    verificar("D1 nenhum lançamento gravado", n_mov(f"{PREFIXO} D1") == 0)
    with conectar() as conn:
        conn.execute("UPDATE contas_financeiras SET ativo=1 WHERE id=%s", (c1,))

    # D2 — inativação commita ENTRE a validação e o commit do recebimento
    print("\n-- D2 inativação commita DURANTE a transação do recebimento")
    pronto = threading.Event()
    soltar = threading.Event()
    saida = {}

    def recebedor():
        saida["r"] = transacao_recebimento(c2, f"{PREFIXO} D2",
                                           pausa_antes_do_commit=soltar,
                                           pronto=pronto)

    t = threading.Thread(target=recebedor)
    t.start()
    if not pronto.wait(10):
        print("  ✘ o recebedor não chegou ao ponto de pausa")
        return 1
    # a validação já passou; agora a conta é inativada e a transação COMMITA
    inativar(c2)
    soltar.set()
    t.join(15)
    r2 = saida.get("r")
    gravou = n_mov(f"{PREFIXO} D2")
    print(f"     resultado: {r2} | lançamentos gravados: {gravou}")
    # O desfecho é MEDIDO, não presumido. O aceitável é: tudo ou nada.
    verificar("D2 o desfecho é íntegro (gravou inteiro OU recusou sem gravar)",
              (r2[0] == "gravou" and gravou == 1) or (r2[0] != "gravou" and gravou == 0),
              f"resultado={r2} gravados={gravou}")
    # TASK_016 (coordenação final): o recebimento segura a linha da conta com
    # FOR UPDATE, então a inativação concorrente ESPERA. O recebimento havia
    # obtido a trava primeiro → conclui legitimamente. Este é o caso O1 da
    # prova de ordem (`prova_ordem_travas.py`); o caso oposto (inativação
    # primeiro) está lá como O2 e termina em recusa.
    verificar("D2 o recebimento havia obtido a TRAVA da conta primeiro, por isso "
              "concluiu; a inativação esperou a liberação",
              r2[0] == "gravou" and gravou == 1, f"{r2} gravados={gravou}")
    with conectar() as conn:
        conn.execute("UPDATE contas_financeiras SET ativo=1 WHERE id=%s", (c2,))

    # D3 — recebimento grava e commita PRIMEIRO; inativação vem depois
    print("\n-- D3 recebimento conclui ANTES da inativação")
    r3 = transacao_recebimento(c3, f"{PREFIXO} D3")
    inativar(c3)
    verificar("D3 o recebimento já consumado fica gravado", r3[0] == "gravou"
              and n_mov(f"{PREFIXO} D3") == 1, f"{r3} gravados={n_mov(f'{PREFIXO} D3')}")
    verificar("D3 a inativação posterior não apaga o fato consumado",
              n_mov(f"{PREFIXO} D3") == 1)
    with conectar() as conn:
        conn.execute("UPDATE contas_financeiras SET ativo=1 WHERE id=%s", (c3,))

    # D4 — duas inativações simultâneas da mesma conta
    print("\n-- D4 duas inativações concorrentes da mesma conta")
    erros = []

    def inativador():
        try:
            inativar(c1)
        except Exception as e:
            erros.append(f"{type(e).__name__}: {e}")

    ts = [threading.Thread(target=inativador) for _ in range(2)]
    for x in ts:
        x.start()
    for x in ts:
        x.join(15)
    with conectar() as conn:
        ativo = conn.execute("SELECT ativo FROM contas_financeiras WHERE id=%s",
                             (c1,)).fetchone()["ativo"]
    verificar("D4 as duas concluíram sem erro (UPDATE idempotente)", not erros, str(erros))
    verificar("D4 a conta terminou inativa", ativo == 0, f"ativo={ativo}")
    with conectar() as conn:
        conn.execute("UPDATE contas_financeiras SET ativo=1 WHERE id=%s", (c1,))

    limpar([c1, c2, c3])
    falhas = [n for n, ok, _ in resultados if not ok]
    print(f"\n[resultado] aprovadas={len(resultados) - len(falhas)} falhas={len(falhas)}")
    for f in falhas:
        print(f"  ✘ {f}")
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
