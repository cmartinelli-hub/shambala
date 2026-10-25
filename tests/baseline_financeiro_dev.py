#!/usr/bin/env python3
"""Baseline financeiro do DEV (TASK_011 §6) — apenas agregados, sem conteúdo de linhas.

Uso:
    .venv/bin/python tests/baseline_financeiro_dev.py [banco]   # padrão: shambala (DEV)

Companheiro do baseline_dev.py: foca o livro-caixa para provar que a subida da
Visão Geral não alterou fatos (somatórios por tipo/status/categoria, obrigações
pagas ambíguas, pares transferência/sangria e a impressão digital de identidade).
Nunca imprime descrições, nomes ou credenciais.
"""
import subprocess
import sys

BANCO = sys.argv[1] if len(sys.argv) > 1 else "shambala"

QUERIES = [
    ("fm_total", "SELECT COUNT(*) || ' | ' || COALESCE(SUM(valor),0) FROM financeiro_movimentacoes"),
    ("fm_max_id_hash", "SELECT COALESCE(MAX(id),0) || ' | ' || md5(string_agg(id || ':' || tipo || ':' || status || ':' || COALESCE(categoria,'-') || ':' || valor || ':' || data_movimentacao, ',' ORDER BY id)) FROM financeiro_movimentacoes"),
    ("fm_por_tipo_status", "SELECT string_agg(tipo || '/' || status || ': ' || n || ' | ' || v, ' ; ' ORDER BY tipo, status) FROM (SELECT tipo, status, COUNT(*) n, COALESCE(SUM(valor),0) v FROM financeiro_movimentacoes GROUP BY tipo, status) x"),
    ("fm_por_categoria", "SELECT string_agg(categoria || ': ' || n || ' | ' || v, ' ; ' ORDER BY categoria) FROM (SELECT COALESCE(categoria,'(null)') categoria, COUNT(*) n, COALESCE(SUM(valor),0) v FROM financeiro_movimentacoes GROUP BY categoria) x"),
    ("fm_areceber_pago", "SELECT COUNT(*) || ' | ' || COALESCE(SUM(f.valor),0) FROM financeiro_movimentacoes f JOIN contas_financeiras c ON c.id=f.conta_id WHERE c.tipo='areceber' AND f.status='pago'"),
    ("fm_transf_sangria", "SELECT COALESCE(string_agg(categoria || '/' || tipo || ': ' || n || ' | ' || v, ' ; ' ORDER BY categoria, tipo),'-') FROM (SELECT categoria, tipo, COUNT(*) n, COALESCE(SUM(valor),0) v FROM financeiro_movimentacoes WHERE categoria IN ('transferencia','sangria') GROUP BY categoria, tipo) x"),
    ("fm_sem_conta", "SELECT COUNT(*) FROM financeiro_movimentacoes WHERE conta_id IS NULL"),
    ("fm_mensalidades_pend", "SELECT COUNT(*) || ' | ' || COALESCE(SUM(valor),0) FROM financeiro_movimentacoes WHERE categoria='mensalidade' AND status='pendente'"),
    ("contas_por_tipo", "SELECT string_agg(tipo || ': ' || n, ' ; ' ORDER BY tipo) FROM (SELECT tipo, COUNT(*) n FROM contas_financeiras GROUP BY tipo) x"),
    ("vendas_por_forma", "SELECT string_agg(forma_pagamento || '/' || status || ': ' || n || ' | ' || COALESCE(SUM(total),0), ' ; ' ORDER BY forma_pagamento, status) FROM (SELECT forma_pagamento, status, COUNT(*) n, SUM(total) total FROM vendas_pdv GROUP BY forma_pagamento, status) x"),
    ("fiado_aberto", "SELECT COUNT(*) || ' | ' || COALESCE(SUM(total),0) FROM vendas_pdv WHERE forma_pagamento='fiado' AND NOT fiado_pago AND status='concluida'"),
]


def q(sql):
    r = subprocess.run(
        ["psql", "-d", BANCO, "-tAqc", sql],
        capture_output=True, text=True)
    if r.returncode != 0:
        return f"ERRO: {r.stderr.strip()[:160]}"
    return r.stdout.strip() or "(vazio)"


print(f"# baseline financeiro DEV — banco: {q('SELECT current_database()')} "
      f"(conectado como {q('SELECT current_user')})")
for nome, sql in QUERIES:
    print(f"{nome}: {q(sql)}")
