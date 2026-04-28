#!/usr/bin/env python3
"""
Script de migração SQLite → PostgreSQL v2
Migra apenas as colunas comuns entre os dois bancos
"""

import sqlite3
import psycopg2
from psycopg2 import sql
import sys

# Configurações
SQLITE_DB = '/home/claudio/projetos/shamballa/bkp/shamballa-2026-04-08.db'
POSTGRES_HOST = 'localhost'
POSTGRES_PORT = 5432
POSTGRES_DB = 'shambala'
POSTGRES_USER = 'shambala'
POSTGRES_PASS = 'Supremacia@735'

# Tabelas em ordem de dependência
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

def obter_colunas_sqlite(cursor, tabela):
    """Obtém lista de colunas do SQLite."""
    cursor.execute(f"PRAGMA table_info({tabela})")
    return [row[1] for row in cursor.fetchall()]

def obter_colunas_postgres(cursor, tabela):
    """Obtém lista de colunas do PostgreSQL."""
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
    """, (tabela,))
    return [row[0] for row in cursor.fetchall()]

def migrar_tabela(sqlite_conn, postgres_conn, tabela):
    """Migra uma tabela encontrando colunas comuns."""
    sqlite_cursor = sqlite_conn.cursor()
    postgres_cursor = postgres_conn.cursor()

    try:
        # Obter colunas
        colunas_sqlite = obter_colunas_sqlite(sqlite_cursor, tabela)
        colunas_postgres = obter_colunas_postgres(postgres_cursor, tabela)

        # Encontrar colunas comuns
        colunas_comuns = [c for c in colunas_sqlite if c in colunas_postgres]

        if not colunas_comuns:
            print(f"  ⚠️  {tabela:<35} Nenhuma coluna comum!")
            return 0

        # Buscar dados
        colunas_str = ', '.join(colunas_comuns)
        sqlite_cursor.execute(f"SELECT {colunas_str} FROM {tabela}")
        linhas = sqlite_cursor.fetchall()

        if not linhas:
            print(f"  ✅ {tabela:<35} 0 registros (tabela vazia)")
            return 0

        # Preparar INSERT
        placeholders = ', '.join(['%s'] * len(colunas_comuns))
        colunas_insert = ', '.join(colunas_comuns)

        insert_query = f"""
            INSERT INTO {tabela} ({colunas_insert})
            VALUES ({placeholders})
            ON CONFLICT DO NOTHING
        """

        # Inserir linhas
        inseridos = 0
        erros = 0

        for linha in linhas:
            try:
                postgres_cursor.execute(insert_query, linha)
                inseridos += 1
            except Exception as e:
                erros += 1
                if erros <= 3:  # Mostrar apenas os primeiros 3 erros
                    print(f"    ⚠️  {tabela}: {str(e)[:80]}")

        postgres_conn.commit()

        if erros > 0:
            print(f"  ⚠️  {tabela:<35} {inseridos:>6} ok, {erros} erros")
        else:
            print(f"  ✅ {tabela:<35} {inseridos:>6} registros migrados")

        return inseridos

    except Exception as e:
        print(f"  ❌ {tabela:<35} ERRO: {str(e)[:60]}")
        postgres_conn.rollback()
        return -1

def main():
    print("\n" + "=" * 80)
    print("🔄 MIGRAÇÃO SQLITE → POSTGRESQL (v2 - Colunas Comuns)")
    print("=" * 80)
    print(f"SQLite:     {SQLITE_DB}")
    print(f"PostgreSQL: {POSTGRES_USER}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}")
    print("=" * 80 + "\n")

    # Conectar
    try:
        sqlite_conn = sqlite3.connect(SQLITE_DB)
        postgres_conn = psycopg2.connect(
            host=POSTGRES_HOST, port=POSTGRES_PORT, database=POSTGRES_DB,
            user=POSTGRES_USER, password=POSTGRES_PASS
        )
        print("✅ Conectado a ambos os bancos\n")
    except Exception as e:
        print(f"❌ Erro de conexão: {e}")
        sys.exit(1)

    # Desabilitar constraints
    try:
        postgres_cursor = postgres_conn.cursor()
        postgres_cursor.execute("SET session_replication_role = 'replica'")
        postgres_conn.commit()
    except:
        pass

    # Migrar tabelas
    print("📊 MIGRANDO TABELAS:")
    print("-" * 80)

    total_migrados = 0
    tabelas_erro = 0

    for tabela in TABELAS_MIGRAR:
        resultado = migrar_tabela(sqlite_conn, postgres_conn, tabela)
        if resultado > 0:
            total_migrados += resultado
        elif resultado < 0:
            tabelas_erro += 1

    # Reabilitar constraints
    try:
        postgres_cursor = postgres_conn.cursor()
        postgres_cursor.execute("SET session_replication_role = 'origin'")
        postgres_conn.commit()
    except:
        pass

    # Resumo
    print("\n" + "=" * 80)
    print("📈 RESUMO DA MIGRAÇÃO")
    print("=" * 80)
    print(f"✅ Total de registros migrados: {total_migrados}")
    print(f"❌ Tabelas com erro crítico: {tabelas_erro}")
    print("=" * 80 + "\n")

    # Verificação final
    print("🔍 VERIFICAÇÃO FINAL:")
    postgres_cursor = postgres_conn.cursor()
    grand_total = 0

    for tabela in TABELAS_MIGRAR:
        try:
            postgres_cursor.execute(f"SELECT COUNT(*) FROM {tabela}")
            count = postgres_cursor.fetchone()[0]
            grand_total += count
            if count > 0:
                print(f"  ✅ {tabela:<35} {count:>6} registros")
        except:
            pass

    print(f"\n{'TOTAL GERAL':<37} {grand_total:>6} registros")
    print("=" * 80 + "\n")

    if tabelas_erro == 0 and total_migrados > 0:
        print("✅ MIGRAÇÃO CONCLUÍDA COM SUCESSO!")
    else:
        print(f"⚠️  Migração concluída com {tabelas_erro} erros críticos")

    print("=" * 80 + "\n")

    # Fechar
    sqlite_conn.close()
    postgres_conn.close()

if __name__ == '__main__':
    main()
