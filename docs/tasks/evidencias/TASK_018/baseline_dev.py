"""TASK_018 — baseline do DEV, somente leitura.

Imprime os fatos que a aplicação desta tarefa não pode alterar (planos,
agendamentos, checkins, pessoas) como contagens, distribuição por status e
**hash de conteúdo** linha a linha, mais o metadado de schema (colunas) que
*deve* mudar pela migração. Serve para separar, na conferência antes/depois,
o que é schema/metadado esperado do que é fato intocado.

Uso:  .venv/bin/python3 docs/tasks/evidencias/TASK_018/baseline_dev.py
Recusa rodar sob SHAMBALA_MODO_TESTE=1 (a baseline é do banco do DEV real).
"""
import hashlib
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(AQUI))))
sys.path.insert(0, RAIZ)
from dotenv import load_dotenv

load_dotenv(os.path.join(RAIZ, ".env"), override=True)

if os.environ.get("SHAMBALA_MODO_TESTE") == "1":
    print("RECUSADO: baseline é do banco do DEV, não do ambiente de teste")
    sys.exit(2)

# `--db NOME`: confronta um banco restaurado do backup com o DEV (mesma fórmula
# de hash). Precisa vir antes do `import banco`, que congela o destino.
if len(sys.argv) > 2 and sys.argv[1] == "--db":
    os.environ["SHAMBALA_DB_NAME"] = sys.argv[2]

import banco  # noqa: E402

COLUNAS_NOVAS = ("sessoes_previstas", "sessoes_adicionais", "regra_conclusao",
                 "concluido_em", "concluido_por", "conclusao_origem",
                 "conclusao_desfeita_em", "conclusao_desfeita_por")


def md5(txt):
    return hashlib.md5(txt.encode()).hexdigest()


def snapshot(conn, tabela, colunas):
    """Contagem + hash do conteúdo (colunas dadas, ordem por id)."""
    linhas = conn.execute(
        f"SELECT id, {', '.join(colunas)} FROM {tabela} ORDER BY id").fetchall()
    corpo = "|".join(
        ";".join("" if l[c] is None else str(l[c]) for c in colunas)
        for l in linhas)
    return len(linhas), md5(corpo)


def por_status(conn, tabela):
    return {r["status"]: r["n"] for r in conn.execute(
        f"SELECT status, count(*) AS n FROM {tabela} GROUP BY status ORDER BY 1")}


def cols(conn, tabela):
    return [r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (tabela,))]


def main():
    with banco.conectar() as conn:
        alvo = conn.execute("SELECT current_database() AS d").fetchone()
        print(f"# baseline DEV — banco efetivo {alvo['d']} "
              f"(config: {os.environ.get('SHAMBALA_DB_HOST')}:"
              f"{os.environ.get('SHAMBALA_DB_PORT')})")

        n, h = snapshot(conn, "planos_tratamento", [
            "medium_id", "sessoes_total", "sessoes_realizadas", "data_inicio",
            "concluido", "frequencia", "status", "sessoes_com_passe"])
        print(f"\nplanos_tratamento: {n} linhas  conteudo_md5={h}")
        print(f"  por status: {por_status(conn, 'planos_tratamento')}")

        na, ha = snapshot(conn, "agendamentos", [
            "plano_id", "data", "status", "requer_passe", "encaixe"])
        print(f"agendamentos:      {na} linhas  conteudo_md5={ha}")
        print(f"  por status: {por_status(conn, 'agendamentos')}")

        ck = conn.execute("SELECT count(*) AS n, "
                          "count(*) FILTER (WHERE atendimento_realizado=1) AS r "
                          "FROM checkins").fetchone()
        print(f"checkins:          {ck['n']} linhas ({ck['r']} realizadas)")

        pe = conn.execute("SELECT count(*) AS n FROM pessoas").fetchone()
        print(f"pessoas:           {pe['n']} linhas")

        # O "avulso" da tela não é uma tabela à parte: cada sessão avulsa é um
        # plano de uma sessão (plano fino) criado por /agenda/novo — medido aqui
        # para que a conferência antes/depois prove que nada foi movido.
        av = conn.execute("SELECT count(*) AS n FROM planos_tratamento "
                          "WHERE sessoes_total = 1").fetchone()
        pp = conn.execute("SELECT count(*) AS n FROM plano_pessoas").fetchone()
        print(f"planos de 1 sessão (o \"avulso\"): {av['n']}   "
              f"plano_pessoas: {pp['n']}")

        pt_cols = cols(conn, "planos_tratamento")
        ag_cols = cols(conn, "agendamentos")
        print(f"\nschema planos_tratamento:  {len(pt_cols)} colunas "
              f"md5={md5(','.join(pt_cols))}")
        print(f"schema agendamentos:       {len(ag_cols)} colunas "
              f"md5={md5(','.join(ag_cols))}")
        print("colunas TASK_018 presentes: " + ", ".join(
            f"{c}={'sim' if c in pt_cols else 'nao'}" for c in COLUNAS_NOVAS)
            + f", adicionada={'sim' if 'adicionada' in ag_cols else 'nao'}"
            + f", repoe={'sim' if 'repoe' in ag_cols else 'nao'}")

        if "sessoes_previstas" in pt_cols:
            m = conn.execute(
                "SELECT count(*) FILTER (WHERE sessoes_previstas IS NULL) AS nulo,"
                " count(*) FILTER (WHERE sessoes_adicionais<>0) AS com_extra,"
                " count(*) FILTER (WHERE regra_conclusao=1) AS prospectivo,"
                " count(*) FILTER (WHERE status='concluido') AS concluidos,"
                " count(*) FILTER (WHERE concluido_em IS NOT NULL) AS com_data "
                "FROM planos_tratamento").fetchone()
            print(f"  previstas NULL={m['nulo']}  com_extra={m['com_extra']}  "
                  f"prospectivo={m['prospectivo']}  concluido={m['concluidos']}  "
                  f"concluido_em={m['com_data']}")
        for c in ("adicionada", "repoe"):
            if c in ag_cols:
                ex = conn.execute(f"SELECT count(*) AS n FROM agendamentos "
                                  f"WHERE {c}=1").fetchone()
                print(f"  agendamentos {c}=1: {ex['n']}")
        if "repoe" in ag_cols and "adicionada" in ag_cols:
            # Só leitura: quantas obrigações originais estão sem substituta hoje.
            v = conn.execute(
                "SELECT count(*) FILTER (WHERE adicionada=0 AND repoe=0 "
                "  AND status IN ('faltou','cancelado')) AS descobertas,"
                " count(*) FILTER (WHERE repoe=1 "
                "  AND status IN ('agendado','realizado')) AS repostas "
                "FROM agendamentos").fetchone()
            print(f"  vagas a repor no DEV (somente leitura): "
                  f"descobertas={v['descobertas']} repostas={v['repostas']} "
                  f"saldo={max(0, v['descobertas'] - v['repostas'])}")


if __name__ == "__main__":
    main()
