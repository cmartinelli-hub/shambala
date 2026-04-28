#!/usr/bin/env python3
"""
Script para migrar dados do SQLite para PostgreSQL.
Uso:
  PYTHONPATH=. python3 scripts/migrar_sqlite_pg.py caminho/do/shamballa.db

Requer psycopg2-binary instalado.
"""
import sqlite3
import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values


def carregar_sqlite(caminho_sqlite: str):
    """Lê todos os dados do SQLite."""
    conn = sqlite3.connect(caminho_sqlite)
    conn.row_factory = sqlite3.Row

    tabelas = [
        "pessoas", "lacos", "mediuns", "atendentes",
        "dias_trabalho", "mediuns_dia", "planos_tratamento",
        "plano_pessoas", "checkins", "agendamentos",
        "dias_atendimento", "trabalhadores",
        "trabalhador_dias", "trabalhador_presenca",
    ]

    dados = {}
    for tabela in tabelas:
        rows = conn.execute(f"SELECT * FROM {tabela}").fetchall()
        dados[tabela] = [dict(r) for r in rows]
        print(f"  {tabela}: {len(dados[tabela])} registros")

    conn.close()
    return dados


def inserir_pg(conn, tabela, registros):
    """Insere registros no PostgreSQL, redefinindo a sequência SERIAL."""
    if not registros:
        return

    colunas = list(registros[0].keys())
    valores = [tuple(r[c] for c in colunas) for r in registros]

    cols_sql = ", ".join(colunas)
    val_placeholder = ", ".join("%s" for _ in colunas)

    cur = conn.cursor()
    execute_values(
        cur,
        f"INSERT INTO {tabela} ({cols_sql}) VALUES %s",
        valores,
    )

    # Reset da sequência SERIAL para evitar colisão
    seq_col = "id"
    if seq_col in colunas:
        cur.execute(f"SELECT setval(pg_get_serial_sequence('{tabela}', '{seq_col}'), (SELECT MAX({seq_col}) FROM {tabela}))")

    conn.commit()
    print(f"  {tabela}: {len(registros)} registros inseridos")


def main():
    if len(sys.argv) < 2:
        print("Uso: python3 scripts/migrar_sqlite_pg.py caminho/shamballa.db")
        sys.exit(1)

    caminho_sqlite = sys.argv[1]
    if not os.path.exists(caminho_sqlite):
        print(f"Arquivo não encontrado: {caminho_sqlite}")
        sys.exit(1)

    print(f"Lendo SQLite: {caminho_sqlite}")
    dados = carregar_sqlite(caminho_sqlite)

    from banco import conectar

    print("\nConectando ao PostgreSQL...")
    with conectar() as conn:
        print("\nLimpando tabelas existentes (se houver)...")
        tabelas_ordem = [
            "trabalhador_presenca", "trabalhador_dias",
            "checkins", "agendamentos", "plano_pessoas",
            "planos_tratamento", "mediuns_dia", "dias_trabalho",
            "lacos", "pessoas", "mediuns", "atendentes",
            "dias_atendimento", "trabalhadores",
        ]
        for t in tabelas_ordem:
            conn.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE")

        print("\nInserindo dados...")
        inserir_pg(conn, "pessoas", dados.get("pessoas", []))
        inserir_pg(conn, "lacos", dados.get("lacos", []))
        inserir_pg(conn, "mediuns", dados.get("mediuns", []))
        inserir_pg(conn, "atendentes", dados.get("atendentes", []))
        inserir_pg(conn, "dias_trabalho", dados.get("dias_trabalho", []))
        inserir_pg(conn, "mediuns_dia", dados.get("mediuns_dia", []))
        inserir_pg(conn, "planos_tratamento", dados.get("planos_tratamento", []))
        inserir_pg(conn, "plano_pessoas", dados.get("plano_pessoas", []))
        inserir_pg(conn, "checkins", dados.get("checkins", []))
        inserir_pg(conn, "agendamentos", dados.get("agendamentos", []))
        inserir_pg(conn, "dias_atendimento", dados.get("dias_atendimento", []))
        inserir_pg(conn, "trabalhadores", dados.get("trabalhadores", []))
        inserir_pg(conn, "trabalhador_dias", dados.get("trabalhador_dias", []))
        inserir_pg(conn, "trabalhador_presenca", dados.get("trabalhador_presenca", []))

    total = sum(len(d) for d in dados.values())
    print(f"\nMigração concluída! {total} registros migrados.")


if __name__ == "__main__":
    main()
