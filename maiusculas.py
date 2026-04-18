#!/usr/bin/env python3
"""
Script de capitalização de nomes para Shambala
Converte nomes para o padrão: primeira letra maiúscula, partículas em minúsculas
Exemplo: "JOSÉ DA SILVA" ou "josé da silva" → "José da Silva"
"""

import sys
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
sys.path.insert(0, script_dir)

try:
    from banco import conectar
except ImportError:
    print("❌ Erro ao importar banco.py")
    sys.exit(1)

APLICAR = "--aplicar" in sys.argv

PARTICULAS = {"a", "o", "e", "da", "do", "de", "das", "dos", "d"}

def capitalizar_nome(nome: str) -> str:
    """
    Capitaliza a primeira letra de cada palavra, mantendo partículas em minúsculas.
    Exemplo: "JOSÉ DA SILVA" → "José da Silva"
    """
    if not nome or not nome.strip():
        return nome

    palavras = nome.lower().split()
    if not palavras:
        return nome

    # Primeira palavra sempre começa com maiúscula
    resultado = [palavras[0].capitalize()]

    for p in palavras[1:]:
        if p in PARTICULAS:
            resultado.append(p)
        else:
            resultado.append(p.capitalize())

    return " ".join(resultado)


# Buscar todas as pessoas
try:
    with conectar() as conn:
        rows = conn.execute(
            "SELECT id, nome_completo FROM pessoas ORDER BY nome_completo"
        ).fetchall()
except Exception as e:
    print(f"❌ Erro ao conectar ao banco: {e}")
    sys.exit(1)

# Encontrar nomes que precisam ser alterados
diferentes = [
    (r["id"], r["nome_completo"], capitalizar_nome(r["nome_completo"]))
    for r in rows if r["nome_completo"] != capitalizar_nome(r["nome_completo"])
]

# Relatório
print(f"\n{'='*80}")
print(f"  CAPITALIZAÇÃO DE NOMES — {'SIMULAÇÃO' if not APLICAR else 'APLICANDO'}")
print(f"{'='*80}")
print(f"\n  Total de pessoas       : {len(rows)}")
print(f"  Serão alteradas        : {len(diferentes)}")

if diferentes:
    print(f"\n{'─'*80}")
    print("  NOMES QUE SERÃO ALTERADOS:")
    print(f"{'─'*80}")
    for pid, antes, depois in diferentes[:50]:  # Mostrar primeiros 50
        print(f"  [{pid:4}]  {antes:<45} → {depois}")
    if len(diferentes) > 50:
        print(f"  ... e mais {len(diferentes) - 50} registros")

# Aplicar mudanças
if APLICAR and diferentes:
    print(f"\n{'─'*80}")
    print("  APLICANDO ALTERAÇÕES...")
    print(f"{'─'*80}")

    try:
        with conectar() as conn:
            for pid, antes, depois in diferentes:
                conn.execute(
                    "UPDATE pessoas SET nome_completo = %s, nome_apresentacao = %s WHERE id = %s",
                    (depois, depois, pid)
                )
        print(f"\n  ✅ {len(diferentes)} registros atualizados com sucesso!")
    except Exception as e:
        print(f"\n  ❌ Erro ao atualizar: {e}")
        sys.exit(1)

else:
    if diferentes:
        print(f"\n{'─'*80}")
        print("  SIMULAÇÃO — nenhum dado foi alterado.")
        print("  Para aplicar as alterações, execute:")
        print("    python3 maiusculas.py --aplicar")
        print(f"{'─'*80}")
    else:
        print(f"\n{'─'*80}")
        print("  ✅ Todos os nomes já estão capitalizados corretamente!")
        print(f"{'─'*80}")

print(f"{'='*80}\n")
