"""Geração e manutenção das mensalidades (contas a receber) dos trabalhadores.

Funções recebem a conexão aberta; só SELECT/INSERT/UPDATE idempotentes.
Não importa módulos de rotas (evita import circular).

TASK_012 §3: a competência (YYYY-MM) passou a ser coluna própria
(`financeiro_movimentacoes.competencia`) e deixou de ser lida do texto da
descrição nem inferida por `LIKE` sobre o vencimento. A chave
(trabalhador_id, competencia) para mensalidade é garantida pelo banco (índice
único parcial criado no startup, quando não há duplicidade histórica) e pelo
serviço (checagem + `ON CONFLICT DO NOTHING`), de forma que repetição e
concorrência da geração não criem segunda cobrança.
"""
import re
from datetime import date
from decimal import Decimal

from banco import (
    PREDICADO_MENSALIDADE, UQ_MENSALIDADE_COMPETENCIA, conectar, conta_id_por_tipo,
)

MESES_A_FRENTE = 12  # mês corrente + 12 futuros
_COMPETENCIA_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


def competencial_de(ano: int, mes: int) -> str:
    return f"{ano:04d}-{mes:02d}"


def normalizar_competencia(texto) -> str | None:
    """'2026-3' / '2026-03' / '2026-03-10' → '2026-03'; resto → None.

    Não adivinha: sem mês entre 01 e 12 o período é inválido e quem recebe a
    decisão é o operador, com mensagem simples.
    """
    t = (texto or "").strip()
    m = _COMPETENCIA_RE.match(t[:7])
    if not m:
        return None
    ano, mes = int(m.group(1)), int(m.group(2))
    if not 1 <= mes <= 12:
        return None
    return competencial_de(ano, mes)


def _indice_ativo(conn) -> bool:
    """A guarda do banco existe NESTE banco? (startup pode ter pulado o índice
    por duplicidade histórica — o serviço continua protegido pela checagem)."""
    return conn.execute(
        "SELECT COUNT(*) AS c FROM pg_class WHERE relname = %s",
        (UQ_MENSALIDADE_COMPETENCIA,)).fetchone()["c"] > 0


def _adicionar_meses(ano: int, mes: int, n: int) -> tuple:
    """Retorna (ano, mes) somando n meses ao mês dado."""
    total = ano * 12 + (mes - 1) + n
    return total // 12, total % 12 + 1


def _data_vencimento(dia: int, ano: int, mes: int) -> str:
    """ISO yyyy-mm-dd do vencimento, com fallback para dia 28 em meses curtos."""
    try:
        return date(ano, mes, dia).isoformat()
    except ValueError:
        return date(ano, mes, 28).isoformat()


def _existe_mes(conn, trabalhador_id: int, competencia: str) -> bool:
    """Existe QUALQUER lançamento do mês (pago, pendente ou cancelado).
    Cancelado NÃO é recriado — é a regra existente, agora pela coluna.

    A segunda perna (competencia IS NULL + mês do vencimento) só alcança linhas
    antigas de vencimento ilegível que o rótulo do startup não pôde rotular:
    ela as protege de virar segunda cobrança, não reinterpreta nenhum fato.
    """
    return conn.execute(
        """SELECT COUNT(*) AS c FROM financeiro_movimentacoes
           WHERE trabalhador_id = %s AND categoria = 'mensalidade'
             AND (competencia = %s
                  OR (competencia IS NULL
                      AND left(data_movimentacao, 7) = %s))""",
        (trabalhador_id, competencia, competencia),
    ).fetchone()["c"] > 0


def inserir_mensalidade(conn, *, trabalhador_id: int, competencia: str,
                        vencimento: str, valor, descricao: str,
                        conta_id) -> int | None:
    """Cria a obrigação do mês. Retorna o id, ou None quando o mês já tem
    obrigação (corrida entre duas gerações simultâneas, restart com o serviço
    antigo ainda no ar, etc.) — nunca um segundo lançamento."""
    col_conf = "ON CONFLICT (trabalhador_id, competencia) " \
               f"WHERE {PREDICADO_MENSALIDADE} DO NOTHING RETURNING id" \
        if _indice_ativo(conn) else "RETURNING id"
    sql = f"""INSERT INTO financeiro_movimentacoes
              (tipo, categoria, valor, data_movimentacao, competencia, descricao,
               trabalhador_id, status, conta_id)
              VALUES ('entrada', 'mensalidade', %s, %s, %s, %s, %s, 'pendente', %s)
              {col_conf}"""
    row = conn.execute(sql, (valor, vencimento, competencia, descricao,
                             trabalhador_id, conta_id)).fetchone()
    return row["id"] if row else None


def _travar_geracao(conn, trabalhador_id: int) -> bool:
    """Trava a geração de mensalidades de UM trabalhador pelo tempo da transação.

    Por que existe (TASK_012 §3, medido): sem o índice único — o estado que o
    startup deixa quando encontra duplicidade histórica e se recusa a criar a
    guarda —, `_existe_mes` é uma LEITURA e não trava nada. Duas gerações
    simultâneas leem "não existe" e as duas inserem: **duas cobranças do mesmo
    mês**. Prova em `tests/test_financeiro_mensalidades_task012.py`
    (`concorrência sem o índice`), que roda este caminho com duas conexões e
    barreira.

    A trava é `pg_advisory_xact_lock` com chave derivada do trabalhador: serializa
    só a geração daquele trabalhador (dois trabalhadores diferentes não esperam um
    pelo outro), é liberada automaticamente no commit/rollback e não depende de
    existir o índice, de tabela de controle ou de coluna nova. Com o índice
    presente a garantia continua sendo do banco; esta trava é o que cobre o
    estado SEM índice.
    """
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('mensalidade_geracao'), %s)",
                 (int(trabalhador_id),))
    return True


def sincronizar_mensalidades(conn, trab: dict) -> int:
    """Idempotente: garante mês corrente + 12 futuros pendentes.
    Não faz nada se inativo ou valor_mensalidade <= 0.

    Retorna quantas obrigações NOVAS este chamador criou (0 = já gerada)."""
    valor = Decimal(str(trab["valor_mensalidade"] or 0))
    if not trab.get("ativo", 1) or valor <= 0:
        return 0
    dia_venc = int(trab["dia_vencimento"] or 10)
    cid_areceber = conta_id_por_tipo(conn, "areceber")
    hoje = date.today()
    criadas = 0
    # Serializa a geração DESTE trabalhador antes de olhar o que existe: sem
    # isso, a checagem e o INSERT são duas operações sem nada entre elas.
    _travar_geracao(conn, trab["id"])
    for i in range(0, MESES_A_FRENTE + 1):
        a, m = _adicionar_meses(hoje.year, hoje.month, i)
        competencia = competencial_de(a, m)
        if _existe_mes(conn, trab["id"], competencia):
            continue
        if inserir_mensalidade(
                conn, trabalhador_id=trab["id"], competencia=competencia,
                vencimento=_data_vencimento(dia_venc, a, m), valor=valor,
                descricao=f"Mensalidade {trab['nome_completo']} - {competencia}",
                conta_id=cid_areceber) is not None:
            criadas += 1
    return criadas


def atualizar_pendentes_futuras(conn, trab: dict) -> None:
    """Aplica novo valor/vencimento nas pendentes do mês corrente em diante.
    Vencidas passadas (pendentes ou pagas) ficam como estavam."""
    hoje = date.today()
    corte = f"{hoje.year:04d}-{hoje.month:02d}-01"
    dia_venc = int(trab["dia_vencimento"] or 10)
    valor = Decimal(str(trab["valor_mensalidade"] or 0))
    rows = conn.execute(
        """SELECT id, data_movimentacao, competencia FROM financeiro_movimentacoes
           WHERE trabalhador_id = %s AND categoria = 'mensalidade'
             AND status = 'pendente' AND data_movimentacao >= %s""",
        (trab["id"], corte),
    ).fetchall()
    for r in rows:
        venc = str(r["data_movimentacao"])[:10]
        competencia = r["competencia"] or normalizar_competencia(venc)
        if not competencia:
            continue  # vencimento ilegível: nada é reescrito por suposição
        conn.execute(
            """UPDATE financeiro_movimentacoes
               SET valor = %s, data_movimentacao = %s, competencia = %s, descricao = %s
               WHERE id = %s""",
            (valor, _data_vencimento(int(competencia[:4]), int(competencia[5:7]),
                                     dia_venc), competencia,
             f"Mensalidade {trab['nome_completo']} - {competencia}", r["id"]),
        )
    sincronizar_mensalidades(conn, trab)  # garante também os 13 meses


def gerar_todas_ativas() -> int:
    """Batch de implantação (startup): corrente + 12 futuros para todos os
    ativos com valor > 0. Idempotente e auto-reparadora."""
    total = 0
    with conectar() as conn:
        rows = conn.execute(
            """SELECT id, nome_completo, valor_mensalidade, dia_vencimento
               FROM trabalhadores WHERE ativo = 1 AND valor_mensalidade > 0"""
        ).fetchall()
        for r in rows:
            trab = dict(r)
            trab["ativo"] = 1
            sincronizar_mensalidades(conn, trab)
            total += 1
    return total
