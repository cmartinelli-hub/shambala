#!/usr/bin/env python3
"""
Script de normalização de telefones para Shambala
Corrige formato (XX) XXXXX-XXXX e adiciona DDD 24 quando falta
"""

import re
import sys

DDD_PADRAO = "24"
APLICAR = "--aplicar" in sys.argv

def so_digitos(tel):
    """Remove caracteres não numéricos."""
    return re.sub(r'\D', '', tel or '')

def formatar(digitos):
    """Formata números em (XX) XXXXX-XXXX ou (XX) XXXX-XXXX."""
    n = len(digitos)
    if n == 10:
        # Fixo: (XX) XXXX-XXXX
        return f'({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}'
    if n == 11:
        # Celular: (XX) XXXXX-XXXX
        return f'({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}'
    return None

def normalizar(tel):
    """
    Normaliza telefone.
    Retorna (novo_valor, status)
    status: 'ok' (já estava certo), 'adicionou_ddd' (adicionou DDD 24), 'revisao' (mal formatado)
    """
    if not tel or not tel.strip():
        return tel, 'vazio'

    digitos = so_digitos(tel)
    n = len(digitos)

    if n == 8:
        # Fixo sem DDD → adicionar DDD 24
        novo = formatar(DDD_PADRAO + digitos)
        return novo, 'adicionou_ddd'

    if n == 9:
        # Celular sem DDD (9 dígitos) → adicionar DDD 24
        novo = formatar(DDD_PADRAO + digitos)
        return novo, 'adicionou_ddd'

    if n == 10:
        # Já tem DDD (fixo com 10 dígitos)
        novo = formatar(digitos)
        return novo, 'ok' if novo and novo != tel else 'reformatado'

    if n == 11:
        # Já tem DDD (celular com 11 dígitos)
        novo = formatar(digitos)
        return novo, 'ok' if novo and novo != tel else 'reformatado'

    # 12+ dígitos ou muito curto
    return tel, 'revisao'


# ─────────────────────────────────────────────────────────────
# Conectar ao banco de dados
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
sys.path.insert(0, script_dir)

try:
    from banco import conectar
except ImportError:
    print("❌ Erro ao importar banco.py")
    sys.exit(1)

# Buscar todos os telefones
try:
    with conectar() as conn:
        rows = conn.execute(
            "SELECT id, nome_completo, telefone FROM pessoas "
            "WHERE telefone IS NOT NULL AND telefone != ''"
        ).fetchall()
except Exception as e:
    print(f"❌ Erro ao conectar ao banco: {e}")
    sys.exit(1)

total = len(rows)
alterados = []
sem_mudanca = []
revisao = []
vazios = []

# Processar cada telefone
for r in rows:
    tel_original = r['telefone']
    novo, status = normalizar(tel_original)

    if status == 'vazio':
        vazios.append((r['id'], r['nome_completo']))
    elif status == 'revisao':
        revisao.append((r['id'], r['nome_completo'], tel_original))
    elif novo == tel_original:
        sem_mudanca.append((r['id'], tel_original))
    else:
        alterados.append((r['id'], r['nome_completo'], tel_original, novo, status))

# ── Relatório ──────────────────────────────────────────────────
print(f"\n{'='*80}")
print(f"  NORMALIZAÇÃO DE TELEFONES — {'SIMULAÇÃO' if not APLICAR else 'APLICANDO'}")
print(f"{'='*80}")
print(f"\n  Total de pessoas com telefone: {total}")
print(f"  Serão alterados               : {len(alterados)}")
print(f"  Já estão corretos             : {len(sem_mudanca)}")
print(f"  Precisam revisão              : {len(revisao)}")
print(f"  Telefones vazios              : {len(vazios)}")

print(f"\n{'─'*80}")
print("  ALTERAÇÕES QUE SERÃO FEITAS:")
print(f"{'─'*80}")

if alterados:
    for pid, nome, antes, depois, status in alterados:
        obs = ""
        if status == 'adicionou_ddd':
            obs = " ← DDD 24 adicionado"
        elif status == 'reformatado':
            obs = " ← Reformatado"
        print(f"  [{pid:4}] {nome[:35]:<35}  {antes:20} → {depois:20}{obs}")
else:
    print("  (nenhuma alteração necessária)")

if revisao:
    print(f"\n{'─'*80}")
    print("  TELEFONES QUE PRECISAM REVISÃO MANUAL (não serão alterados):")
    print(f"{'─'*80}")
    for pid, nome, tel in revisao:
        digitos = so_digitos(tel)
        print(f"  [{pid:4}] {nome[:35]:<35}  {tel:20} ({len(digitos)} dígitos)")

# ── Aplicar ────────────────────────────────────────────────────
if APLICAR and alterados:
    print(f"\n{'─'*80}")
    print("  APLICANDO ALTERAÇÕES...")
    print(f"{'─'*80}")

    try:
        with conectar() as conn:
            for pid, nome, antes, depois, status in alterados:
                conn.execute(
                    "UPDATE pessoas SET telefone = %s WHERE id = %s",
                    (depois, pid)
                )
        print(f"\n  ✅ {len(alterados)} registros atualizados com sucesso!")
    except Exception as e:
        print(f"\n  ❌ Erro ao atualizar: {e}")
        sys.exit(1)

else:
    if alterados:
        print(f"\n{'─'*80}")
        print("  SIMULAÇÃO — nenhum dado foi alterado.")
        print("  Para aplicar as alterações, execute:")
        print("    python3 normalizar_telefones.py --aplicar")
        print(f"{'─'*80}")

if revisao:
    print(f"\n  ⚠️  Revisão manual pendente: {len(revisao)} registros")
    print("     (use SQL diretamente ou corrija manualmente)")

print(f"\n{'='*80}\n")
