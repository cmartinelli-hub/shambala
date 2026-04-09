#!/usr/bin/env python3
"""
Script de migração SQLite → PostgreSQL para Shambala
Migra todos os dados históricos para o novo banco PostgreSQL
"""

import sqlite3
import psycopg2
from psycopg2 import sql
import sys
from datetime import datetime

# Configurações
SQLITE_DB = '/home/claudio/projetos/shamballa/bkp/shamballa-2026-04-08.db'
POSTGRES_HOST = 'localhost'
POSTGRES_PORT = 5432
POSTGRES_DB = 'shambala'
POSTGRES_USER = 'shambala'
POSTGRES_PASS = 'Supremacia@735'

# Mapeamento de tabelas em ordem de dependência (respeita foreign keys)
TABELAS_MIGRAR = [
    'atendentes',
    'pessoas',
    'mediuns',
    'lacos',
    'dias_trabalho',
    'dias_atendimento',
    'mediuns_dia',
    'planos_tratamento',
    'plano_pessoas',
    'agendamentos',
    'checkins',
    'trabalhadores',
    'trabalhador_dias',
    'trabalhador_presenca',
]

def migrar_tabela(sqlite_conn, postgres_conn, tabela):
    """Migra uma tabela do SQLite para PostgreSQL."""
    sqlite_cursor = sqlite_conn.cursor()
    postgres_cursor = postgres_conn.cursor()

    try:
        # Buscar todos os dados da tabela SQLite
        sqlite_cursor.execute(f"SELECT * FROM {tabela}")
        colunas_sql = [desc[0] for desc in sqlite_cursor.description]
        linhas = sqlite_cursor.fetchall()

        if not linhas:
            print(f"  ⚠️  {tabela:<35} 0 registros")
            return 0

        # Preparar INSERT para PostgreSQL
        placeholders = ', '.join(['%s'] * len(colunas_sql))
        colunas_str = ', '.join(colunas_sql)

        insert_query = f"""
            INSERT INTO {tabela} ({colunas_str})
            VALUES ({placeholders})
            ON CONFLICT DO NOTHING
        """

        # Inserir linhas
        for linha in linhas:
            try:
                postgres_cursor.execute(insert_query, linha)
            except Exception as e:
                print(f"    ❌ Erro ao inserir linha em {tabela}: {e}")
                return -1

        postgres_conn.commit()

        print(f"  ✅ {tabela:<35} {len(linhas):>6} registros migrados")
        return len(linhas)

    except Exception as e:
        print(f"  ❌ {tabela:<35} ERRO: {e}")
        postgres_conn.rollback()
        return -1

def main():
    print("\n" + "=" * 75)
    print("🔄 MIGRAÇÃO SQLITE → POSTGRESQL")
    print("=" * 75)
    print(f"SQLite:     {SQLITE_DB}")
    print(f"PostgreSQL: {POSTGRES_USER}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}")
    print("=" * 75 + "\n")

    # Conectar ao SQLite
    try:
        sqlite_conn = sqlite3.connect(SQLITE_DB)
        sqlite_conn.row_factory = sqlite3.Row
        print("✅ Conectado ao SQLite\n")
    except Exception as e:
        print(f"❌ Erro ao conectar SQLite: {e}")
        sys.exit(1)

    # Conectar ao PostgreSQL
    try:
        postgres_conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASS
        )
        print("✅ Conectado ao PostgreSQL\n")
    except Exception as e:
        print(f"❌ Erro ao conectar PostgreSQL: {e}")
        sqlite_conn.close()
        sys.exit(1)

    # Desabilitar constraints temporariamente (PostgreSQL)
    postgres_cursor = postgres_conn.cursor()
    try:
        postgres_cursor.execute("SET session_replication_role = 'replica'")
        postgres_conn.commit()
        print("✅ Constraints desabilitadas temporariamente\n")
    except:
        pass

    # Migrar tabelas
    print("📊 MIGRANDO TABELAS:")
    print("-" * 75)

    total_migrados = 0
    total_erros = 0

    for tabela in TABELAS_MIGRAR:
        resultado = migrar_tabela(sqlite_conn, postgres_conn, tabela)
        if resultado > 0:
            total_migrados += resultado
        elif resultado < 0:
            total_erros += 1

    # Reabilitar constraints
    try:
        postgres_cursor.execute("SET session_replication_role = 'origin'")
        postgres_conn.commit()
        print("\n✅ Constraints reabilitadas")
    except:
        pass

    # Resumo final
    print("\n" + "=" * 75)
    print("📈 RESUMO DA MIGRAÇÃO")
    print("=" * 75)
    print(f"✅ Total de registros migrados: {total_migrados}")
    print(f"❌ Tabelas com erro: {total_erros}")
    print("=" * 75 + "\n")

    # Verificação final
    print("🔍 VERIFICAÇÃO FINAL:")
    postgres_cursor = postgres_conn.cursor()

    grand_total = 0
    for tabela in TABELAS_MIGRAR:
        try:
            postgres_cursor.execute(f"SELECT COUNT(*) FROM {tabela}")
            count = postgres_cursor.fetchone()[0]
            grand_total += count
            print(f"  • {tabela:<35} {count:>6} registros")
        except:
            pass

    print(f"\n{'TOTAL FINAL':<37} {grand_total:>6} registros")
    print("=" * 75 + "\n")

    if total_erros == 0:
        print("✅ MIGRAÇÃO CONCLUÍDA COM SUCESSO!")
    else:
        print(f"⚠️  Migração concluída com {total_erros} erros")

    print("=" * 75 + "\n")

    # Fechar conexões
    sqlite_conn.close()
    postgres_conn.close()

if __name__ == '__main__':
    main()
