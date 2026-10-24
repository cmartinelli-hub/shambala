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

# Token que identifica a EXECUÇÃO corrente do ambiente de teste. Quem sobe o
# app (runner, launcher, suíte) gera e exporta; o lifespan do app eco o token
# na linha de confirmação. Assim a prova tirada do log refere-se ao processo
# desta execução — log antigo de outra execução não vale (REV-01/TASK_010).
VARIAVEL_TOKEN_EXECUCAO = "SHAMBALA_EXEC_TOKEN"


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
    Quando uma conexão é fornecida, o dict inclui a identidade EFETIVA observada
    nela (`current_database()`/`current_user`), além do token da execução — é
    essa identidade que as provas de suíte confrontam, nunca só o ambiente.
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
    info = {
        "banco": os.environ.get("SHAMBALA_DB_NAME"),
        "usuario": os.environ.get("SHAMBALA_DB_USER"),
        "servidor": os.environ.get("SHAMBALA_DB_HOST"),
    }
    if conn is not None:
        row = conn.execute(
            "SELECT current_database() AS db, current_user AS usr").fetchone()
        info["banco_efetivo"] = row["db"]
        info["usuario_efetivo"] = row["usr"]
    info["token"] = os.environ.get(VARIAVEL_TOKEN_EXECUCAO) or None
    return info


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


def exigir_configuracao_consoante(caminho: str, db_solicitado: str | None = None,
                                  aplicar: bool = True) -> dict:
    """Contrato de identidade da execução (TASK_010, REV-01/REV-03).

    O arquivo de configuração NOMEADO é a autoridade do destino: o que o shell
    apenas SUGERE (SHAMBALA_TEST_DB, ou SHAMBALA_DB_NAME prévio) precisa BATER
    com o arquivo, senão aborta antes de qualquer DDL/seed/startup — nunca há
    substituição silenciosa em nenhum dos sentidos.

    Não abre conexão (quem chama valida a conexão efetiva em seguida, com
    exigir_ambiente_de_teste(conn)); não imprime senhas nem conteúdo integral.
    Com aplicar=True os valores do arquivo entram no ambiente com força de
    autoridade (mesma semântica de main.py com SHAMBALA_ENV_FILE definido).
    """
    from dotenv import dotenv_values
    if not caminho or not os.path.exists(caminho):
        raise AmbienteInseguro(
            f"arquivo de configuração da execução ausente: {caminho or '(não informado)'}")
    valores = dotenv_values(caminho)
    db_arquivo = valores.get("SHAMBALA_DB_NAME") or ""
    if not db_arquivo:
        raise AmbienteInseguro(
            f"o arquivo de configuração {caminho} não define SHAMBALA_DB_NAME — "
            "destino ausente; nada foi executado.")
    if not _nome_e_de_teste(db_arquivo):
        raise AmbienteInseguro(
            f"destino '{db_arquivo}' do arquivo {caminho} não é comprovadamente "
            "descartável (precisa conter o marcador '_test' e não ser banco proibido).")
    pedido = (db_solicitado or "").strip()
    if not pedido:
        pedido = (os.environ.get("SHAMBALA_TEST_DB")
                  or os.environ.get("SHAMBALA_DB_NAME") or "").strip()
    if pedido and pedido != db_arquivo:
        raise AmbienteInseguro(
            f"divergência de destino: solicitado '{pedido}', configuração "
            f"nomeada '{caminho}' define '{db_arquivo}'. Nenhuma operação foi "
            "executada — aponte o arquivo certo ou desconcorde o pedido.")
    if aplicar:
        for chave, valor in valores.items():
            if valor is not None:
                os.environ[chave] = valor
    return {"banco": db_arquivo, "arquivo": caminho}


def provar_destino_do_servidor(esperado_banco: str, log: str | None = None,
                               esperado_token: str | None = None) -> dict:
    """Prova de que o SERVIDOR HTTP da EXECUÇÃO CORRENTE está no banco esperado.

    Lê a última confirmação `[TESTE] Ambiente descartável confirmado: {...}` do
    log do app e exige: token da execução corrente (log de execução anterior não
    vale) e banco EFETIVO (current_database do processo do servidor) igual ao
    esperado. Falha de prova = abortar ANTES de seed/escrita — nunca PASS/PULADO.
    """
    import re
    log = log or os.environ.get("SHAMBALA_APP_LOG", "/tmp/shambala_test_app.log")
    token = (esperado_token or os.environ.get(VARIAVEL_TOKEN_EXECUCAO) or "").strip()
    if not os.path.exists(log):
        raise AmbienteInseguro(
            f"log do app de teste ausente ({log}): impossível provar o destino do "
            "servidor HTTP; execute via tests/rodar_testes.sh ou suba o app com "
            "tests/subir_app_descartavel.sh apontando SHAMBALA_APP_LOG.")
    with open(log, encoding="utf-8", errors="replace") as fh:
        linhas = [l for l in fh.read().splitlines()
                  if "Ambiente descartável confirmado" in l]
    if not linhas:
        raise AmbienteInseguro(
            f"o app nunca imprimiu 'Ambiente descartável confirmado' em {log}: "
            "destino do servidor não provado, nada foi escrito.")
    ultima = linhas[-1]

    def _campo(nome):
        m = re.search(rf"'{nome}': (?:'([^']*)'|(None))", ultima)
        if not m:
            return None
        return None if m.group(2) == "None" else m.group(1)

    banco_servidor = _campo("banco_efetivo")
    token_servidor = _campo("token")
    if banco_servidor is None:
        raise AmbienteInseguro(
            "a confirmação do servidor não traz o banco efetivo (current_database) "
            "— app de teste de versão anterior à TASK_010; reinicie-o.")
    if not token:
        raise AmbienteInseguro(
            f"{VARIAVEL_TOKEN_EXECUCAO} ausente no ambiente desta suíte: sem token "
            "não há como provar que o log é da execução corrente.")
    if token_servidor is None:
        raise AmbienteInseguro(
            "o servidor subiu sem token de execução: a confirmação no log não pode "
            "ser vinculada a esta execução.")
    if token_servidor != token:
        raise AmbienteInseguro(
            f"o log pertence a OUTRA execução (token '{token_servidor}' != "
            f"'{token}'): prova de log antigo recusada.")
    if banco_servidor != esperado_banco:
        raise AmbienteInseguro(
            f"o servidor HTTP está no banco efetivo '{banco_servidor}', a suíte "
            f"espera '{esperado_banco}' — destinos divergem, nada foi escrito.")
    return {"banco_efetivo": banco_servidor, "token": token_servidor, "log": log}


if __name__ == "__main__":  # execução direta: valida e informa
    try:
        info = exigir_ambiente_de_teste()
        print(f"Ambiente de teste validado: {info}")
        sys.exit(0)
    except AmbienteInseguro:
        sys.exit(2)
