#!/usr/bin/env python3
"""Baseline do DEV (TASK_010 §5) — contagens e somas de fatos, sem valores sensíveis.

Uso:
    .venv/bin/python tests/baseline_dev.py [banco]   # padrão: shambala (DEV)

Imprime apenas agregados (contagens, somas de valor, identidade de sessão legada).
Nunca imprime senhas, hashes ou conteúdo de linhas.
"""
import subprocess
import sys

BANCO = sys.argv[1] if len(sys.argv) > 1 else "shambala"

QUERIES = [
    ("pessoas", "SELECT COUNT(*) FROM pessoas"),
    ("checkins", "SELECT COUNT(*) FROM checkins"),
    ("trab_total", "SELECT COUNT(*) FROM trabalhadores"),
    ("trab_ativos", "SELECT COUNT(*) FROM trabalhadores WHERE ativo=1"),
    ("codigos", "SELECT (SELECT COUNT(*) FROM trabalhador_codigo) || ' (' || COALESCE((SELECT string_agg(estado || ':' || n, ' ') FROM (SELECT estado, COUNT(*) n FROM trabalhador_codigo GROUP BY estado) x), '-') || ')'"),
    ("presencas", "SELECT COUNT(*) FROM trabalhador_presenca"),
    ("correcoes", "SELECT COUNT(*) FROM trabalhador_presenca_correcao"),
    ("vendas", "SELECT COUNT(*), COALESCE(SUM(total),0) FROM vendas_pdv"),
    ("vendas_canceladas", "SELECT COUNT(*) FROM vendas_pdv WHERE status='cancelada'"),
    ("financeiro", "SELECT COUNT(*), COALESCE(SUM(valor),0) FROM financeiro_movimentacoes"),
    ("caixas", "SELECT COUNT(*) FROM caixas"),
    ("sessoes", "SELECT COUNT(*), COUNT(*) FILTER (WHERE status='aberto') FROM caixa_movimentos"),
    ("sessao_aberta_ids", "SELECT COALESCE(string_agg(id::text, ','), '-') FROM caixa_movimentos WHERE status='aberto'"),
    ("eventos", "SELECT COUNT(*) FROM caixa_sessao_eventos"),
    ("sangrias", "SELECT COUNT(*) FROM caixa_sangrias"),
    ("atendentes", "SELECT COUNT(*) FROM atendentes"),
    ("grupos_permissoes", "SELECT COUNT(*) FROM grupos_permissoes"),
    ("contas", "SELECT COUNT(*) FROM contas_financeiras"),
    ("produtos", "SELECT COUNT(*) FROM produtos"),
    ("mensalidades", "SELECT COUNT(*) FROM financeiro_movimentacoes WHERE tipo ILIKE '%mensalidade%' OR categoria ILIKE '%mensalidade%'"),
]


def q(sql):
    r = subprocess.run(
        ["psql", "-h", "/var/run/postgresql", "-U", "shambala", "-d", BANCO, "-tAc", sql],
        capture_output=True, text=True)
    return r.stdout.strip() or f"ERRO: {r.stderr.strip().splitlines()[0] if r.stderr else '?'}"


for rotulo, sql in QUERIES:
    print(f"{rotulo:22} {q(sql)}")
