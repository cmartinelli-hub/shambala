#!/usr/bin/env python3
"""
Seed de permissões para os grupos criados antes da cobrança real (2026-10).

Ao ativar a negação por padrão nas rotas, grupos que nunca precisaram ter
permissões lançadas ficariam bloqueados. Este utilitário insere apenas o que
o dono decidiu (2026-10-21):

  Caixas    → caixa (ler, escrever)
  Financeiro→ relatorios (ler, escrever), caixa (ler, escrever)
  Recepção  → checkin.trabalhadores (ler, escrever),
              cadastros.mediuns.planos (ler, escrever) — decisão do dono
              (2026-10-26): a Recepção acessa os planos de atendimento dos
              médiuns e lança agendamentos pelo próprio grupo, sem Cambonas
              e sem vínculo individual; não administração de médiuns.
  Cambonas  → cadastros.pessoas (ler), agenda.medium (ler, escrever)

Nunca altera linhas existentes (ON CONFLICT DO NOTHING) e nunca toca no
grupo Admin (bypass no código).

Uso:
    .venv/bin/python3 semear_permissoes.py            # simulação (relatório)
    .venv/bin/python3 semear_permissoes.py --aplicar  # efetivar
"""

import sys

from banco import conectar

APLICAR = "--aplicar" in sys.argv

SEED = [
    # (grupo, modulo, ler, escrever, apagar)
    ("Caixas",     "caixa",                 True,  True,  False),
    ("Financeiro", "relatorios",            True,  True,  False),
    ("Financeiro", "caixa",                 True,  True,  False),
    ("Recepção",   "checkin.trabalhadores", True,  True,  False),
    ("Recepção",   "cadastros.mediuns.planos", True, True,  False),
    ("Cambonas",   "cadastros.pessoas",     True,  False, False),
    ("Cambonas",   "agenda.medium",        True,  True,  False),
]


def main():
    with conectar() as conn:
        grupos = {g["nome"]: g["id"] for g in conn.execute(
            "SELECT id, nome FROM grupos"
        ).fetchall()}

        faltando = sorted({nome for nome, *_ in SEED if nome not in grupos})
        if faltando:
            print(f"Grupo(s) inexistente(s) — serão ignorados: {', '.join(faltando)}\n")

        inseridas, ja_existentes = [], []
        for nome, modulo, ler, escrever, apagar in SEED:
            if nome not in grupos:
                continue
            gid = grupos[nome]
            row = conn.execute(
                "SELECT ler, escrever, apagar FROM grupos_permissoes "
                "WHERE grupo_id = %s AND modulo = %s", (gid, modulo)
            ).fetchone()
            if row:
                ja_existentes.append(
                    f"  {nome:12s} {modulo:24s} já existe "
                    f"(ler={row['ler']} escrever={row['escrever']} apagar={row['apagar']})"
                )
                continue
            inseridas.append(f"  {nome:12s} {modulo:24s} ler={'T' if ler else '-'} "
                             f"escrever={'T' if escrever else '-'} apagar={'T' if apagar else '-'}")
            if APLICAR:
                conn.execute(
                    """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (grupo_id, modulo) DO NOTHING""",
                    (gid, modulo, ler, escrever, apagar)
                )

        print(f"{len(inseridas)} permissão(ões) a inserir"
              + ("  [APLICANDO]" if APLICAR else "  [SIMULAÇÃO — nada foi alterado]"))
        for linha in inseridas:
            print(linha)
        if ja_existentes:
            print("\nJá existentes (intocadas):")
            for linha in ja_existentes:
                print(linha)
        if not APLICAR and inseridas:
            print("\nUse --aplicar para efetivar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
