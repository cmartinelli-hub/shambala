#!/usr/bin/env python3
"""TESTE NEGATIVO do isolamento (exigência B.7).

Demonstra que a trava REJEITA a conexão ao banco de desenvolvimento ANTES de
qualquer operação destrutiva — e demonstra isso SEM executar nada destrutivo no
banco real.

Estratégia: a trava é uma função pura sobre (nome do banco, usuário, servidor,
conexão efetiva). Aqui ela é exercitada com ambientes simulados perigosos e com
a conexão efetiva real, apenas em modo leitura. O banco `shambala` é consultado
somente com SELECT para provar que a rejeição ocorre; nada é escrito, criado,
truncado ou removido.

Uso:
    PYTHONPATH=. .venv/bin/python3 tests/teste_negativo_isolamento.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import (  # noqa: E402
    exigir_ambiente_de_teste, AmbienteInseguro, BANCOS_PROIBIDOS,
    exigir_configuracao_consoante, provar_destino_do_servidor,
    VARIAVEL_TOKEN_EXECUCAO,
)

RESULTADOS = []


def caso(nome, ambiente, esperado_seguro, conn=None):
    """Executa um cenário e classifica como seguro/inseguro."""
    anterior = {k: os.environ.get(k) for k in
                ("SHAMBALA_DB_NAME", "SHAMBALA_DB_USER",
                 "SHAMBALA_DB_HOST", "SHAMBALA_MODO_TESTE")}
    os.environ.pop("SHAMBALA_MODO_TESTE", None)
    for k, v in ambiente.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        exigir_ambiente_de_teste(conn)
        seguro = True
        detalhe = "aceito"
    except AmbienteInseguro as e:
        seguro = False
        detalhe = str(e)[:90]
    finally:
        for k, v in anterior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    ok = (seguro == esperado_seguro)
    RESULTADOS.append((nome, ok, seguro, esperado_seguro, detalhe))
    marca = "OK  " if ok else "FALHA"
    print(f"  {marca} {nome}")
    print(f"        seguro={seguro} (esperado {esperado_seguro}) | {detalhe}")
    return ok


def main():
    print("=" * 78)
    print("TESTE NEGATIVO — a trava rejeita o banco de desenvolvimento?")
    print("=" * 78)

    # ── Cenários de rejeição (ambientes perigosos) ──────────────────────────
    print("\n[1] Ambientes que DEVEM ser rejeitados:")

    caso("banco de DEV 'shambala' com modo de teste ligado",
         {"SHAMBALA_DB_NAME": "shambala", "SHAMBALA_DB_USER": "shambala",
          "SHAMBALA_DB_HOST": "/var/run/postgresql", "SHAMBALA_MODO_TESTE": "1"},
         esperado_seguro=False)

    caso("banco 'postgres' (administrativo)",
         {"SHAMBALA_DB_NAME": "postgres", "SHAMBALA_DB_USER": "shambala",
          "SHAMBALA_DB_HOST": "/var/run/postgresql", "SHAMBALA_MODO_TESTE": "1"},
         esperado_seguro=False)

    caso("banco de produção disfarçado de teste ('shambala_prod')",
         {"SHAMBALA_DB_NAME": "shambala_prod", "SHAMBALA_DB_USER": "shambala",
          "SHAMBALA_DB_HOST": "/var/run/postgresql", "SHAMBALA_MODO_TESTE": "1"},
         esperado_seguro=False)

    caso("modo de teste DESLIGADO (mesmo com banco correto)",
         {"SHAMBALA_DB_NAME": "shambala_test_passes", "SHAMBALA_DB_USER": "shambala",
          "SHAMBALA_DB_HOST": "/var/run/postgresql", "SHAMBALA_MODO_TESTE": None},
         esperado_seguro=False)

    caso("nome de banco vazio",
         {"SHAMBALA_DB_NAME": None, "SHAMBALA_DB_USER": "shambala",
          "SHAMBALA_DB_HOST": "/var/run/postgresql", "SHAMBALA_MODO_TESTE": "1"},
         esperado_seguro=False)

    # ── Cenário que DEVE ser aceito ────────────────────────────────────────
    print("\n[2] Ambiente que DEVE ser aceito:")
    caso("banco descartável 'shambala_test_passes' com modo de teste ligado",
         {"SHAMBALA_DB_NAME": "shambala_test_passes", "SHAMBALA_DB_USER": "shambala",
          "SHAMBALA_DB_HOST": "/var/run/postgresql", "SHAMBALA_MODO_TESTE": "1"},
         esperado_seguro=True)

    # ── Prova com a CONEXÃO EFETIVA, sem tocar no DEV ───────────────────────
    print("\n[3] Prova com a CONEXÃO REAL (somente leitura, nada é alterado):")
    try:
        os.environ["SHAMBALA_DB_NAME"] = "shambala"
        os.environ["SHAMBALA_MODO_TESTE"] = "1"
        from banco import conectar
        with conectar() as conn:
            ident = conn.execute(
                "SELECT current_database() AS db, current_user AS usr"
            ).fetchone()
            print(f"        conexão efetiva: banco='{ident['db']}' usuário='{ident['usr']}'")
            print("        (nenhuma escrita executada — apenas SELECT de identidade)")
            rejeitado = False
            try:
                exigir_ambiente_de_teste(conn)
            except AmbienteInseguro as e:
                rejeitado = True
                print(f"        REJEITADO corretamente: {str(e)[:100]}")
            RESULTADOS.append(("conexão efetiva no DEV é rejeitada", rejeitado,
                               not rejeitado, False,
                               "rejeitado pela conexão real" if rejeitado else "aceito"))
            # Confirma que a operação destrutiva jamais seria alcançada:
            print("        >>> Nenhum TRUNCATE/DROP/ALTER é executado antes desta checagem.")
    except Exception as e:  # pragma: no cover
        print(f"        não foi possível abrir conexão de prova: {e}")
        RESULTADOS.append(("conexão efetiva no DEV é rejeitada", False, None, False, str(e)))

    # ── [4] Contrato de identidade da execução (TASK_010/REV-01, REV-03) ────
    # Puramente sobre arquivos/env sintéticos: nenhum banco é tocado aqui.
    print("\n[4] Contrato de configuração e prova de execução corrente:")
    temporarios = []

    def _arquivo_env(db):
        fd, caminho = tempfile.mkstemp(suffix=".env", prefix="shambala_neg_")
        with os.fdopen(fd, "w") as fh:
            fh.write(f"SHAMBALA_DB_HOST=/var/run/postgresql\n"
                     f"SHAMBALA_DB_PORT=5432\n"
                     f"SHAMBALA_DB_NAME={db}\n"
                     f"SHAMBALA_DB_USER=shambala\nSHAMBALA_DB_PASS=sintetica\n")
        temporarios.append(caminho)
        return caminho

    def _recusa(nome, fn):
        try:
            fn()
            RESULTADOS.append((nome, False, True, False, "ACEITOU (indevidamente)"))
            print(f"  FALHA {nome} — aceitou o que devia recusar")
            return False
        except AmbienteInseguro as e:
            RESULTADOS.append((nome, True, False, False, str(e)[:90]))
            print(f"  OK    {nome} — recusado: {str(e)[:90]}")
            return True

    def _aceite(nome, fn):
        try:
            r = fn()
            RESULTADOS.append((nome, True, True, True, str(r)[:90]))
            print(f"  OK    {nome} — aceito: {str(r)[:90]}")
            return r
        except AmbienteInseguro as e:
            RESULTADOS.append((nome, False, False, True, str(e)[:90]))
            print(f"  FALHA {nome} — recusou o que devia aceitar: {e}")
            return None

    _recusa("arquivo de configuração ausente",
            lambda: exigir_configuracao_consoante("/nao/existe/.env"))
    _recusa("arquivo sem SHAMBALA_DB_NAME",
            lambda: exigir_configuracao_consoante(_arquivo_env("")))
    _recusa("arquivo apontando banco de DEV ('shambala')",
            lambda: exigir_configuracao_consoante(_arquivo_env("shambala")))
    _recusa("destino solicitado A diverge do arquivo B (sentinelas A/B)",
            lambda: exigir_configuracao_consoante(_arquivo_env("shambala_b_test"),
                                                  "shambala_a_test"))
    _aceite("destino solicitado bate com o arquivo",
            lambda: exigir_configuracao_consoante(_arquivo_env("shambala_a_test"),
                                                  "shambala_a_test", aplicar=False))

    def _log_sintetico(linha):
        fd, caminho = tempfile.mkstemp(prefix="shambala_neg_log_")
        with os.fdopen(fd, "w") as fh:
            if linha:
                fh.write(linha + "\n")
        temporarios.append(caminho)
        return caminho

    _recusa("log sem confirmação alguma (destino não provado)",
            lambda: provar_destino_do_servidor(
                "shambala_a_test", log=_log_sintetico(""),
                esperado_token="tok-desta-execucao"))
    _recusa("log de OUTRA execução (token antigo)",
            lambda: provar_destino_do_servidor(
                "shambala_a_test",
                log=_log_sintetico(
                    "[TESTE] Ambiente descartável confirmado: {'banco': "
                    "'shambala_a_test', 'usuario': 'shambala', 'servidor': 'x', "
                    "'banco_efetivo': 'shambala_a_test', 'usuario_efetivo': "
                    "'shambala', 'token': 'tok-ANTIGA'}"),
                esperado_token="tok-desta-execucao"))
    _recusa("token corrente mas banco efetivo divergente do esperado",
            lambda: provar_destino_do_servidor(
                "shambala_a_test",
                log=_log_sintetico(
                    "[TESTE] Ambiente descartável confirmado: {'banco': "
                    "'shambala_outra_test', 'usuario': 'shambala', 'servidor': 'x', "
                    "'banco_efetivo': 'shambala_outra_test', 'usuario_efetivo': "
                    "'shambala', 'token': 'tok-desta-execucao'}"),
                esperado_token="tok-desta-execucao"))
    _aceite("execução corrente provada (token + banco efetivo)",
            lambda: provar_destino_do_servidor(
                "shambala_a_test",
                log=_log_sintetico(
                    "[TESTE] Ambiente descartável confirmado: {'banco': "
                    "'shambala_a_test', 'usuario': 'shambala', 'servidor': 'x', "
                    "'banco_efetivo': 'shambala_a_test', 'usuario_efetivo': "
                    "'shambala', 'token': 'tok-desta-execucao'}"),
                esperado_token="tok-desta-execucao"))

    for _tmp in temporarios:
        try:
            os.unlink(_tmp)
        except OSError:
            pass

    print("\n" + "=" * 78)
    ok = sum(1 for r in RESULTADOS if r[1])
    print(f"RESULTADO: {ok}/{len(RESULTADOS)} cenários corretos")
    if ok == len(RESULTADOS):
        print("A trava REJEITA o banco de desenvolvimento em todos os cenários testados.")
        print("Nenhum dado foi alterado neste teste (somente SELECT).")
        print("=" * 78)
        return 0
    print("ATENÇÃO: há cenários divergentes — revisar antes de prosseguir.")
    print("=" * 78)
    return 1


if __name__ == "__main__":
    sys.exit(main())
