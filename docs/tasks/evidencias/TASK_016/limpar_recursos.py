"""Limpeza dos recursos desta investigação/tarefa no banco descartável."""
import os
import sys

sys.path.insert(0, "/nc/projetos/shambala")
from dotenv import load_dotenv

load_dotenv("/nc/projetos/shambala/.env.test", override=True)
os.environ["SHAMBALA_MODO_TESTE"] = "1"
from banco import conectar  # noqa: E402

NADA = [-1]

with conectar() as conn:
    trab = [r["id"] for r in conn.execute(
        "SELECT id FROM trabalhadores WHERE nome_completo LIKE 'T016%%'",
        None).fetchall()] or NADA
    contas = [r["id"] for r in conn.execute(
        "SELECT id FROM contas_financeiras WHERE nome LIKE 'T016%%'",
        None).fetchall()] or NADA
    vendas = [r["id"] for r in conn.execute(
        "SELECT id FROM vendas_pdv WHERE fiado_trabalhador_id = ANY(%s)",
        (trab,)).fetchall()] or NADA
    fins = [r["id"] for r in conn.execute(
        """SELECT id FROM financeiro_movimentacoes
           WHERE descricao LIKE 'T016%%' OR trabalhador_id = ANY(%s)
              OR conta_id = ANY(%s)""", (trab, contas)).fetchall()] or NADA

    # 1) dependentes de conta e de lançamento
    conn.execute("DELETE FROM caixa_sessao_eventos WHERE conta_id = ANY(%s)", (contas,))
    conn.execute("DELETE FROM caixa_sessao_eventos WHERE financeiro_id = ANY(%s)", (fins,))
    conn.execute("DELETE FROM caixa_sessao_eventos WHERE venda_id = ANY(%s)", (vendas,))
    # 2) dependentes de venda
    conn.execute("DELETE FROM vendas_pdv_cancelamento WHERE venda_id = ANY(%s)", (vendas,))
    conn.execute("DELETE FROM fiado_alocacao WHERE financeiro_id = ANY(%s)", (fins,))
    conn.execute("DELETE FROM fiado_alocacao WHERE venda_id = ANY(%s)", (vendas,))
    conn.execute("DELETE FROM fiado_alocacao WHERE trabalhador_id = ANY(%s)", (trab,))
    conn.execute("DELETE FROM fiado_credito_mov WHERE financeiro_id = ANY(%s)", (fins,))
    conn.execute("DELETE FROM fiado_credito_mov WHERE trabalhador_id = ANY(%s)", (trab,))
    # 3) documentos
    conn.execute("DELETE FROM financeiro_movimentacoes WHERE id = ANY(%s)", (fins,))
    conn.execute("DELETE FROM vendas_pdv_itens WHERE venda_id = ANY(%s)", (vendas,))
    conn.execute("DELETE FROM vendas_pdv WHERE id = ANY(%s)", (vendas,))
    conn.execute("DELETE FROM trabalhadores WHERE id = ANY(%s)", (trab,))
    n = conn.execute("DELETE FROM contas_financeiras WHERE id = ANY(%s)", (contas,)).rowcount

print("contas removidas:", n)
with conectar() as conn:
    print("contas restantes:", [x["nome"] for x in conn.execute(
        "SELECT nome FROM contas_financeiras ORDER BY id", None).fetchall()])
    for t in ("financeiro_movimentacoes", "trabalhadores", "vendas_pdv",
              "caixa_sessao_eventos", "fiado_alocacao", "fiado_credito_mov"):
        c = conn.execute(f"SELECT count(*) AS c FROM {t}", None).fetchone()["c"]
        print(f"  {t}: {c}")
