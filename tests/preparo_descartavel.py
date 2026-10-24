#!/usr/bin/env python3
"""Preparo de bancos descartáveis com SELO de propriedade (TASK_010, REV-01 §7).

Regra: uma execução de teste só remove bancos que ela reconhecidamente criou.
O reconhecimento é um selo — a tabela `preparo_descartavel` gravada dentro do
próprio banco com o token de quem o criou. Banco preexistente SEM selo, ou com
selo de OUTRA execução, nunca é apagado por convenção de nome: aborta.

Uso esperado: runner (via python -c), launcher e suítes de banco dedicado de
migração. Nenhuma função imprime credenciais.
"""
import os
import subprocess

from trava_isolamento import MARCADOR_OBRIGATORIO, BANCOS_PROIBIDOS, AmbienteInseguro

TABELA_SELO = "preparo_descartavel"


def _host() -> str:
    return os.environ.get("PGADMIN_HOST",
                          os.environ.get("PGHOST", "/var/run/postgresql"))


def _usuario() -> str:
    return os.environ.get("PGADMIN_USER", os.environ.get("PGUSER", "shambala"))


def _psql(db: str, sql: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["psql", "-h", _host(), "-U", _usuario(), "-d", db, "-tAqc", sql],
        capture_output=True, text=True, env={**os.environ})


def nome_descartavel(nome: str) -> bool:
    return bool(nome) and nome not in BANCOS_PROIBIDOS and MARCADOR_OBRIGATORIO in nome


def banco_existe(nome: str) -> bool:
    segura = nome.replace("'", "''")
    r = _psql("postgres", f"SELECT 1 FROM pg_database WHERE datname = '{segura}'")
    return r.returncode == 0 and r.stdout.strip() == "1"


def selo_do_banco(nome: str):
    """Token gravado dentro do banco, ou None se o banco não tem selo legível."""
    if not banco_existe(nome):
        return None
    r = _psql(nome, f"SELECT token FROM {TABELA_SELO} LIMIT 1")
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def criar_banco(nome: str, token: str) -> None:
    """Cria o banco descartável a partir da conexão de manutenção e grava o selo.

    Se o banco já existe, exige selo desta mesma execução (reexecução idempotente
    com o mesmo token) — selo ausente ou de outra execução aborta sem apagar nada.
    """
    if not nome_descartavel(nome):
        raise AmbienteInseguro(
            f"recuso criar/preparar '{nome}': nome sem o marcador "
            f"'{MARCADOR_OBRIGATORIO}' ou banco proibido.")
    if banco_existe(nome):
        selo = selo_do_banco(nome)
        if selo is None:
            raise AmbienteInseguro(
                f"banco '{nome}' já existe SEM selo desta infraestrutura de testes: "
                "não o apago por convenção de nome. Se for resíduo intencional de "
                "execução antiga, remova-o manualmente e rode de novo.")
        if selo != token:
            raise AmbienteInseguro(
                f"banco '{nome}' pertence a OUTRA execução de teste (selo '{selo}', "
                f"desta execução '{token}'): não apago nem sobreponho banco alheio.")
        return
    _criar_e_selar(nome, token)


def recriar_banco(nome: str, token: str) -> None:
    """Modo do runner: recria o banco de teste a cada execução.

    Só substitui um banco existente se o SELO provar que ele foi criado por uma
    execução desta mesma infraestrutura (resíduo de corrida anterior). Banco
    preexistente sem selo — seja DEV disfarçado, seja criação manual — nunca é
    apagado por convenção de nome: aborta sem efeitos.
    """
    if not nome_descartavel(nome):
        raise AmbienteInseguro(
            f"recuso recriar '{nome}': nome sem o marcador "
            f"'{MARCADOR_OBRIGATORIO}' ou banco proibido.")
    if banco_existe(nome):
        selo = selo_do_banco(nome)
        if selo is None:
            raise AmbienteInseguro(
                f"banco '{nome}' preexiste sem selo dos testes: não o apago por "
                "convenção de nome. Remova-o manualmente, ciente do que é, e rode de novo.")
        # selo de qualquer execução nossa (desta ou de corrida anterior): é
        # resíduo desta infraestrutura — apaga e recria limpo.
        remover_se_nosso(nome, selo)
    _criar_e_selar(nome, token)


def garantir_banco_selado(nome: str, token: str) -> None:
    """Garante que o banco existe e é selado por ESTA infraestrutura.

    - inexistente → cria e sela com o token dado;
    - existente com selo de qualquer execução nossa (runner/suíte) → aceita como
      está (é o caso normal sob o runner, que já preparou o alvo);
    - existente SEM selo → aborta: banco alheio não é adotado por convenção.
    """
    if not nome_descartavel(nome):
        raise AmbienteInseguro(
            f"recuso preparar '{nome}': nome sem o marcador "
            f"'{MARCADOR_OBRIGATORIO}' ou banco proibido.")
    if banco_existe(nome):
        if selo_do_banco(nome) is None:
            raise AmbienteInseguro(
                f"banco '{nome}' preexiste sem selo desta infraestrutura de testes: "
                "não o adoto por convenção de nome.")
        return
    _criar_e_selar(nome, token)


def _criar_e_selar(nome: str, token: str) -> None:
    r = _psql("postgres", f'CREATE DATABASE "{nome}"')
    if r.returncode != 0:
        raise AmbienteInseguro(f"não foi possível criar '{nome}': {r.stderr.strip()[:200]}")
    selo_token = token.replace("'", "''")
    selo_ok = _psql(nome,
                    f"CREATE TABLE IF NOT EXISTS {TABELA_SELO} "
                    "(token text NOT NULL, criado_em timestamptz NOT NULL DEFAULT now()); "
                    f"INSERT INTO {TABELA_SELO}(token) SELECT '{selo_token}' "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {TABELA_SELO})")
    if selo_ok.returncode != 0:
        raise AmbienteInseguro(
            f"banco '{nome}' criado mas sem selo gravado: abortando para nunca "
            f"recair em convenção de nome. {selo_ok.stderr.strip()[:200]}")


def remover_se_nosso(nome: str, token: str) -> bool:
    """Apaga o banco SOMENTE se o selo for desta execução. Retorna o que fez."""
    if not banco_existe(nome):
        return False
    selo = selo_do_banco(nome)
    if selo != token:
        raise AmbienteInseguro(
            f"limpeza recusou apagar '{nome}': selo '{selo}' não pertence a esta "
            f"execução ('{token}'). Banco alheio ou sem selo permanece intacto.")
    r = _psql("postgres", f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
    if r.returncode != 0:
        raise AmbienteInseguro(f"falha ao apagar '{nome}' (nosso): {r.stderr.strip()[:200]}")
    return True
