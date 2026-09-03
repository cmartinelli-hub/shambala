"""Geração e manutenção das mensalidades (contas a receber) dos trabalhadores.

Funções recebem a conexão aberta; só SELECT/INSERT/UPDATE idempotentes.
Não importa módulos de rotas (evita import circular).
"""
from datetime import date

from banco import conectar, conta_id_por_tipo

MESES_A_FRENTE = 12  # mês corrente + 12 futuros


def _adicionar_meses(ano: int, mes: int, n: int) -> tuple:
    """Retorna (ano, mes) somando n meses ao mês dado."""
    total = ano * 12 + (mes - 1) + n
    return total // 12, total % 12 + 1


def _data_vencimento(dia: int, ano: int, mes: int) -> str:
    """ISO yyyy-mm-dd do vencimento, com fallback para dia 28 em meses curtos."""
    try:
        return date(ano, mes, dia).isoformat()
    except ValueError:
        return date(ano, mes, 28).isoformat()


def _existe_mes(conn, trabalhador_id: int, ano_m: int, mes_m: int) -> bool:
    """Existe QUALQUER lançamento do mês (pago, pendente ou cancelado).
    Cancelado NÃO é recriado."""
    pref = f"{ano_m:04d}-{mes_m:02d}"
    return conn.execute(
        """SELECT COUNT(*) AS c FROM financeiro_movimentacoes
           WHERE trabalhador_id = %s AND categoria = 'mensalidade'
             AND data_movimentacao LIKE %s""",
        (trabalhador_id, f"{pref}%"),
    ).fetchone()["c"] > 0


def sincronizar_mensalidades(conn, trab: dict) -> None:
    """Idempotente: garante mês corrente + 12 futuros pendentes.
    Não faz nada se inativo ou valor_mensalidade <= 0."""
    valor = float(trab["valor_mensalidade"] or 0)
    if not trab.get("ativo", 1) or valor <= 0:
        return
    dia_venc = int(trab["dia_vencimento"] or 10)
    cid_areceber = conta_id_por_tipo(conn, "areceber")
    hoje = date.today()
    for i in range(0, MESES_A_FRENTE + 1):
        a, m = _adicionar_meses(hoje.year, hoje.month, i)
        if _existe_mes(conn, trab["id"], a, m):
            continue
        pref = f"{a:04d}-{m:02d}"
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao,
                trabalhador_id, status, conta_id)
               VALUES ('entrada', 'mensalidade', %s, %s, %s, %s, 'pendente', %s)""",
            (valor, _data_vencimento(dia_venc, a, m),
             f"Mensalidade {trab['nome_completo']} - {pref}", trab["id"], cid_areceber),
        )


def atualizar_pendentes_futuras(conn, trab: dict) -> None:
    """Aplica novo valor/vencimento nas pendentes do mês corrente em diante.
    Vencidas passadas (pendentes ou pagas) ficam como estavam."""
    hoje = date.today()
    corte = f"{hoje.year:04d}-{hoje.month:02d}-01"
    dia_venc = int(trab["dia_vencimento"] or 10)
    valor = float(trab["valor_mensalidade"] or 0)
    rows = conn.execute(
        """SELECT id, data_movimentacao FROM financeiro_movimentacoes
           WHERE trabalhador_id = %s AND categoria = 'mensalidade'
             AND status = 'pendente' AND data_movimentacao >= %s""",
        (trab["id"], corte),
    ).fetchall()
    for r in rows:
        venc = str(r["data_movimentacao"])[:10]
        ano_m, mes_m = int(venc[:4]), int(venc[5:7])
        pref = f"{ano_m:04d}-{mes_m:02d}"
        conn.execute(
            """UPDATE financeiro_movimentacoes
               SET valor = %s, data_movimentacao = %s, descricao = %s
               WHERE id = %s""",
            (valor, _data_vencimento(dia_venc, ano_m, mes_m),
             f"Mensalidade {trab['nome_completo']} - {pref}", r["id"]),
        )
    sincronizar_mensalidades(conn, trab)  # garante também os 13 meses


def gerar_todas_ativas() -> int:
    """Batch de implantação (startup): corrente + 12 futuros para todos os
    ativos com valor > 0. Idempotente e auto-reparadora."""
    total = 0
    with conectar() as conn:
        rows = conn.execute(
            """SELECT id, nome_completo, valor_mensalidade, dia_vencimento
               FROM trabalhadores WHERE ativo = 1 AND valor_mensalidade > 0"""
        ).fetchall()
        for r in rows:
            trab = dict(r)
            trab["ativo"] = 1
            sincronizar_mensalidades(conn, trab)
            total += 1
    return total
