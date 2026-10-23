#!/usr/bin/env python3
"""Prepara os códigos de carteirinha dos trabalhadores ativos (TASK_002).

Chama o MESMO serviço que a tela usa
(`servico_codigo_trabalhador.preparar_codigos_pendentes`) — nenhuma regra
própria aqui. Idempotente: a segunda execução gera zero e preserva tudo o que
já foi emitido.

A ação é atribuída ao ator técnico local (`atendente_id` nulo, origem
`preparacao_local`): o modelo não exige um usuário humano e seria falso
inventar um. Quem clicar na tela fica registrado com o próprio atendente.

Uso:
    .venv/bin/python3 preparar_codigos_trabalhadores.py            # simulação
    .venv/bin/python3 preparar_codigos_trabalhadores.py --aplicar  # emitir

Nunca imprime os códigos — só quantidades.
"""

import os
import sys

from banco import conectar
from servico_codigo_trabalhador import CodigosEsgotados, preparar_codigos_pendentes

APLICAR = "--aplicar" in sys.argv


def main():
    if os.environ.get("SHAMBALA_MODO_TESTE") == "1":
        print("[ERRO] este script é de operação, não de teste. Desative o modo de "
              "teste antes de usá-lo.")
        return 2

    with conectar() as conn:
        info = conn.execute(
            """SELECT current_database() AS banco,
                      (SELECT COUNT(*) FROM trabalhadores WHERE ativo = 1)     AS ativos,
                      (SELECT COUNT(*) FROM trabalhadores WHERE ativo <> 1)     AS inativos,
                      (SELECT COUNT(*) FROM trabalhador_codigo)                 AS emitidos,
                      (SELECT COUNT(*) FROM trabalhador_codigo WHERE estado = 'ativo')
                                                                               AS ativos_com_codigo,
                      (SELECT COUNT(*) FROM trabalhadores t
                        WHERE t.ativo = 1
                          AND NOT EXISTS (SELECT 1 FROM trabalhador_codigo c
                                           WHERE c.trabalhador_id = t.id
                                             AND c.estado = 'ativo'))           AS faltando"""
        ).fetchone()

    print(f"banco ..........: {info['banco']}")
    print(f"ativos .........: {info['ativos']}   inativos: {info['inativos']}")
    print(f"códigos emitidos: {info['emitidos']} (ativos: {info['ativos_com_codigo']})")
    print(f"faltando código .: {info['faltando']}")

    if not APLICAR:
        print("\n[simulação] nada foi escrito. Repita com --aplicar para emitir.")
        return 0

    if info["faltando"] == 0:
        print("\nNada a fazer: todo trabalhador ativo já tem código atual.")
        return 0

    with conectar() as conn:
        try:
            resumo = preparar_codigos_pendentes(conn, atendente_id=None,
                                                 origem="preparacao_local")
        except CodigosEsgotados:
            print("\n[ERRO] espaço de 10.000 códigos esgotado — nenhuma linha foi "
                  "aplicada (transação única).")
            return 1

    print(f"\ngerados ....: {len(resumo['gerados'])}")
    print(f"já existiam .: {resumo['ja_existiam']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
