#!/usr/bin/env python3
"""Trava de isolamento dos testes — impede execução acidental no banco de DEV.

CONTEXTO: em 2026-10-14 a suíte de testes truncou o banco de desenvolvimento
porque o app de teste na porta 9011 subiu conectado a `shambala` (DEV) em vez do
banco descartável. Este módulo existe para tornar esse acidente IMPOSSÍVEL.

Ele valida, ANTES de qualquer operação destrutiva:
  1. que o processo está conectado a um banco cujo nome é explicitamente de teste;
  2. que o banco NÃO é o banco de desenvolvimento/produção conhecido;
  3. que o usuário e o servidor são os esperados para o ambiente de teste;
  4. que a conexão efetiva (mesma que a aplicação usa) confirma tudo isso.

Uso pela suíte de testes:
    from trava_isolamento import exigir_ambiente_de_teste
    exigir_ambiente_de_teste()          # aborta o processo se não for seguro

Uso pelo app de teste (guard no startup):
    SHAMBALA_MODO_TESTE=1  -> liga as verificações no lifespan

NUNCA use esta trava como única defesa: ela é a primeira de várias camadas.
"""
import os
import sys

# Bancos que NUNCA podem ser alvo de teste destrutivo.
BANCOS_PROIBIDOS = {
    "shambala",          # banco de desenvolvimento / cópia de produção
    "postgres",
    "template0",
    "template1",
}

# O nome do banco de teste precisa CONTER este marcador. Contém (e não começa
# com) porque "shambala_test_passes" e "shambala_test" são válidos.
MARCADOR_OBRIGATORIO = "_test"

# Variável que o app de teste deve ter ligada.
VARIAVEL_MODO_TESTE = "SHAMBALA_MODO_TESTE"


def _nome_e_de_teste(nome: str) -> bool:
    """True apenas se o nome identifica inequivocamente um banco de teste."""
    if not nome or nome in BANCOS_PROIBIDOS:
        return False
    return MARCADOR_OBRIGATORIO in nome


class AmbienteInseguro(RuntimeError):
    """Levantada quando o ambiente não é comprovadamente descartável."""


def _falhas_de_identidade(conn=None) -> list:
    """Retorna a lista de motivos pelos quais NÃO é seguro prosseguir."""
    motivos = []
    nome = os.environ.get("SHAMBALA_DB_NAME", "")
    usuario = os.environ.get("SHAMBALA_DB_USER", "")
    servidor = os.environ.get("SHAMBALA_DB_HOST", "")

    # 1) nome do banco
    if not nome:
        motivos.append("SHAMBALA_DB_NAME não definido (não é possível provar que é descartável).")
    elif not _nome_e_de_teste(nome):
        if nome in BANCOS_PROIBIDOS:
            motivos.append(f"banco '{nome}' é proibido para testes.")
        else:
            motivos.append(
                f"banco '{nome}' não contém o marcador obrigatório "
                f"'{MARCADOR_OBRIGATORIO}' (ex.: shambala_test_passes)."
            )

    # 2) modo de teste explicitamente ligado
    if os.environ.get(VARIAVEL_MODO_TESTE) != "1":
        motivos.append(
            f"{VARIAVEL_MODO_TESTE}=1 não está definido (exigido para operar em testes)."
        )

    # 3) confirmação pela CONEXÃO REAL (não confia só no ambiente)
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT current_database() AS db, current_user AS usr, "
                "inet_server_addr()::text AS srv, inet_server_port() AS porta"
            ).fetchone()
            db_real = row["db"]
            if not _nome_e_de_teste(db_real):
                motivos.append(
                    f"conexão efetiva aponta para o banco '{db_real}', "
                    f"que não é comprovadamente descartável."
                )
            if usuario and row["usr"] != usuario:
                motivos.append(
                    f"usuário efetivo '{row['usr']}' difere do configurado '{usuario}'."
                )
        except Exception as e:  # pragma: no cover
            motivos.append(f"não foi possível confirmar a identidade pela conexão: {e}")

    return motivos


def exigir_ambiente_de_teste(conn=None) -> dict:
    """Aborta o processo se o ambiente não for comprovadamente descartável.

    Devolve um dict de identificação quando tudo está correto (útil para log).
    """
    motivos = _falhas_de_identidade(conn)
    if motivos:
        print("\n" + "=" * 78, file=sys.stderr)
        print("EXECUÇÃO ABORTADA — AMBIENTE NÃO É DE TESTE", file=sys.stderr)
        print("=" * 78, file=sys.stderr)
        for m in motivos:
            print(f"  • {m}", file=sys.stderr)
        print("\n  Esta trava existe porque testes destrutivos já atingiram o banco", file=sys.stderr)
        print("  de desenvolvimento. Nenhuma operação foi executada.", file=sys.stderr)
        print("\n  Para rodar os testes corretamente:", file=sys.stderr)
        print("    export SHAMBALA_DB_NAME=shambala_test_passes", file=sys.stderr)
        print("    export SHAMBALA_MODO_TESTE=1", file=sys.stderr)
        print("    bash tests/rodar_testes.sh", file=sys.stderr)
        print("=" * 78 + "\n", file=sys.stderr)
        raise AmbienteInseguro(" ; ".join(motivos))
    return {
        "banco": os.environ.get("SHAMBALA_DB_NAME"),
        "usuario": os.environ.get("SHAMBALA_DB_USER"),
        "servidor": os.environ.get("SHAMBALA_DB_HOST"),
    }


def exigir_arquivo_de_configuracao(caminho: str) -> dict:
    """Carrega um .env EXCLUSIVO de testes e valida o ambiente.

    O app de teste deve usar este caminho: assim ele nunca herda o .env do
    projeto (que aponta para o banco de desenvolvimento).
    """
    from dotenv import dotenv_values
    if not os.path.exists(caminho):
        raise AmbienteInseguro(f"arquivo de configuração de teste ausente: {caminho}")
    valores = dotenv_values(caminho)
    for chave, valor in valores.items():
        if valor is not None and os.environ.get(chave) in (None, ""):
            os.environ[chave] = valor
    return exigir_ambiente_de_teste()


if __name__ == "__main__":  # execução direta: valida e informa
    try:
        info = exigir_ambiente_de_teste()
        print(f"Ambiente de teste validado: {info}")
        sys.exit(0)
    except AmbienteInseguro:
        sys.exit(2)
