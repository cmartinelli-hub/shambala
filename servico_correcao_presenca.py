"""Correções e lançamentos retroativos de presença (TASK_004).

Ponto único de escrita alterada em `trabalhador_presenca`: tudo o que mexe num
fato já lançado (ou cria um de data passada) passa por `aplicar_correcao`, com
motivo obrigatório e auditoria na MESMA transação — o `conectar()` do banco
comita no fim do bloco do chamador; se a auditoria falhar, o fato também
falha, e vice-versa.

Semânticas decididas com o dono:

- **Anular** remove a linha operacional (ausência de linha já é "Sem registro"
  em todo o código) e a auditoria guarda o fato inteiro — nunca referencia o
  id descartável da presença. Depois de anular hoje, uma chegada legítima do
  terminal/recepção pode recriar a presença pelo fluxo normal; o histórico da
  anulação continua lá, intacto.
- **Origem administrativa** (`correcao_administrativa`) só nasce aqui: para
  conversão de ausência em presença e para inclusão retroativa de presença.
  Ajuste de horário de um fato existente PRESERVA a origem original —
  corrigir a hora não falsifica por onde a pessoa entrou.
- **Horário desconhecido é NULL declarado**, nunca 00:00 nem "agora": em data
  passada não se inventa minuto.
- **Concorrência**: `FOR UPDATE` na linha do trabalhador (a mesma trava da
  chegada e da troca de código) + comparação do `estado_base` apresentado no
  formulário. O estado mudou desde a abertura? `ConflitoCorrecao`, nada gravado.
- Correções valem também para trabalhador **inativo** (histórico), sem
  reativar ninguém e sem tocar em códigos.

Não há movimentação de fato entre pessoas ou datas: pessoa e data vêm fixas no
formulário; o caminho para "registro no lugar errado" é anular e lançar de
novo, cada passo auditado. Não existe alteração em lote.
"""

import re
from datetime import date

from servico_presenca import (
    ORIGEM_ADMINISTRATIVA, _agora_sao_paulo,
)


class ConflitoCorrecao(Exception):
    pass


class MotivoObrigatorio(Exception):
    pass


class DataFutura(Exception):
    pass


class DataInvalida(Exception):
    pass


class HorarioInvalido(Exception):
    pass


class OperacaoInvalida(Exception):
    pass


class FatoInexistente(Exception):
    pass


# Operações do formulário → valor gravado em trabalhador_presenca_correcao.operacao
OPERACOES = ("corrigir_horario", "ausentar", "chegar", "incluir", "anular")

_AUDIT_OPERACAO = {
    "corrigir_horario": "correcao_horario",
    "ausentar": "conversao_ausencia",
    "chegar": "conversao_presenca",
    "incluir": "lancamento_retroativo",
    "anular": "anulacao",
}

ROTULOS_OPERACAO = {
    "lancamento_retroativo": "Lançamento retroativo",
    "correcao_horario": "Correção de horário",
    "conversao_ausencia": "Conversão para ausência registrada",
    "conversao_presenca": "Conversão para presença",
    "anulacao": "Anulação do lançamento",
}

_RE_HORA = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def validar_data(conn, data_txt: str) -> str:
    """YYYY-MM-DD real, hoje ou passada (America/Sao_Paulo do servidor).

    Data futura não é fato corrigível — recusada. Formato ou calendário
    inválidos recusados antes de qualquer SQL.
    """
    if not data_txt or not re.match(r"^\d{4}-\d{2}-\d{2}$", data_txt):
        raise DataInvalida(f"data fora do formato AAAA-MM-DD: {data_txt!r}")
    try:
        date.fromisoformat(data_txt)
    except ValueError:
        raise DataInvalida(f"data inexistente no calendário: {data_txt!r}")
    hoje, _ = _agora_sao_paulo(conn)
    if data_txt > hoje:
        raise DataFutura(f"data futura não pode ser lançada: {data_txt}")
    return data_txt


def validar_horario(texto, permitido_em_branco: bool = True):
    """HH:MM, ou None quando em branco é permitido (horário desconhecido).

    None aqui significa NULL declarado — o oposto de inventar 00:00 ou a hora
    atual para um fato passado.
    """
    if texto is None or str(texto).strip() == "":
        if permitido_em_branco:
            return None
        raise HorarioInvalido("informe o horário ou declare que não sabe")
    texto = str(texto).strip()
    if not _RE_HORA.match(texto):
        raise HorarioInvalido(f"horário fora de HH:MM: {texto!r}")
    return texto


def estado_base(fato) -> str:
    """Token opaco do estado apresentado no formulário (anti-clobbering).

    Derivado só do que a tela já mostrou; a comparação depois da trava é o que
    vale. Inclui horário e origem porque ambos interessam ao conflito.
    """
    if fato is None:
        return "sem-registro"
    if fato["presente"]:
        return "presente|%s|%s" % (fato["hora_chegada"] or "", fato["origem_chegada"] or "")
    return "ausente"


def ler_fato(conn, trabalhador_id: int, data: str):
    return conn.execute(
        """SELECT id, presente, hora_chegada, hora_saida, origem_chegada,
                  dia_trabalho_id
             FROM trabalhador_presenca
            WHERE trabalhador_id = %s AND data = %s::date""",
        (trabalhador_id, data),
    ).fetchone()


def operacoes_permitidas(fato) -> list:
    """Quais operações a interface pode oferecer para este estado."""
    if fato is None:
        return ["incluir"]
    if fato["presente"]:
        return ["corrigir_horario", "ausentar", "anular"]
    return ["chegar", "anular"]


def _snapshot_anterior(fato) -> dict:
    if fato is None:
        return {"anterior_presente": None, "anterior_hora_chegada": None,
                "anterior_hora_saida": None, "anterior_origem": None,
                "anterior_dia_trabalho_id": None}
    return {"anterior_presente": fato["presente"],
            "anterior_hora_chegada": fato["hora_chegada"],
            "anterior_hora_saida": fato["hora_saida"],
            "anterior_origem": fato["origem_chegada"],
            "anterior_dia_trabalho_id": fato["dia_trabalho_id"]}


def _snapshot_novo(presente, hora_chegada, hora_saida, origem, dia_trabalho_id):
    if presente is None:  # anulação: não há fato novo; a linha deixou de existir
        return {"novo_presente": None, "novo_hora_chegada": None,
                "novo_hora_saida": None, "novo_origem": None,
                "novo_dia_trabalho_id": None}
    return {"novo_presente": presente, "novo_hora_chegada": hora_chegada,
            "novo_hora_saida": hora_saida, "novo_origem": origem,
            "novo_dia_trabalho_id": dia_trabalho_id}


def aplicar_correcao(conn, *, trabalhador_id: int, data: str, operacao: str,
                     motivo: str, estado_base_esperado: str, atendente_id: int,
                     nova_hora=None, incluir_estado=None) -> dict:
    """Aplica UMA correção, auditada, na transação do chamador.

    `nova_hora`: HH:MM ou None (desconhecido declarado). `incluir_estado`:
    'presente' | 'ausente', exigido pela operação 'incluir'.
    """
    if operacao not in OPERACOES:
        raise OperacaoInvalida(f"operação desconhecida: {operacao!r}")
    if not motivo or not str(motivo).strip():
        raise MotivoObrigatorio("correção exige motivo")
    motivo = str(motivo).strip()

    data = validar_data(conn, data)
    if operacao in ("corrigir_horario", "chegar", "incluir") and nova_hora is not None:
        nova_hora = validar_horario(nova_hora)
    elif operacao in ("corrigir_horario", "chegar", "incluir"):
        nova_hora = None

    # A mesma trava da chegada/troca de código: correção e terminal/recepção
    # concorrentes se serializam aqui, e a re-leitura abaixo vê o estado já
    # confirmado de quem chegou primeiro.
    trab = conn.execute(
        "SELECT id, nome_completo FROM trabalhadores WHERE id = %s FOR UPDATE",
        (trabalhador_id,),
    ).fetchone()
    if not trab:
        raise FatoInexistente(f"trabalhador {trabalhador_id} não existe")

    fato = ler_fato(conn, trabalhador_id, data)
    if estado_base(fato) != estado_base_esperado:
        raise ConflitoCorrecao(
            "a situação mudou desde que o formulário foi aberto")

    anterior = _snapshot_anterior(fato)
    novo = None
    resultado = None

    if operacao == "corrigir_horario":
        if not fato or not fato["presente"]:
            raise FatoInexistente("não há presença com horário para corrigir nesta data")
        conn.execute(
            """UPDATE trabalhador_presenca SET hora_chegada = %s
                WHERE trabalhador_id = %s AND data = %s::date""",
            (nova_hora, trabalhador_id, data),
        )
        # origem preservada de propósito: ajustar a hora não muda por onde a
        # pessoa entrou.
        novo = _snapshot_novo(1, nova_hora, fato["hora_saida"],
                              fato["origem_chegada"], fato["dia_trabalho_id"])
        resultado = {"presente": 1, "hora_chegada": nova_hora,
                     "origem_chegada": fato["origem_chegada"]}

    elif operacao == "ausentar":
        if not fato or not fato["presente"]:
            raise FatoInexistente("não há presença para converter em ausência nesta data")
        conn.execute(
            """UPDATE trabalhador_presenca
                  SET presente = 0, hora_chegada = NULL, hora_saida = NULL,
                      origem_chegada = NULL
                WHERE trabalhador_id = %s AND data = %s::date""",
            (trabalhador_id, data),
        )
        novo = _snapshot_novo(0, None, None, None, fato["dia_trabalho_id"])
        resultado = {"presente": 0, "hora_chegada": None, "origem_chegada": None}

    elif operacao == "chegar":
        if not fato or fato["presente"]:
            raise FatoInexistente("não há ausência registrada para converter em presença")
        conn.execute(
            """UPDATE trabalhador_presenca
                  SET presente = 1, hora_chegada = %s, origem_chegada = %s
                WHERE trabalhador_id = %s AND data = %s::date""",
            (nova_hora, ORIGEM_ADMINISTRATIVA, trabalhador_id, data),
        )
        novo = _snapshot_novo(1, nova_hora, fato["hora_saida"],
                              ORIGEM_ADMINISTRATIVA, fato["dia_trabalho_id"])
        resultado = {"presente": 1, "hora_chegada": nova_hora,
                     "origem_chegada": ORIGEM_ADMINISTRATIVA}

    elif operacao == "incluir":
        if fato:
            raise ConflitoCorrecao(
                "já existe registro nesta data — o estado mudou ou a data está errada")
        if incluir_estado not in ("presente", "ausente"):
            raise OperacaoInvalida(
                f"estado da inclusão inválido: {incluir_estado!r}")
        presente = 1 if incluir_estado == "presente" else 0
        origem = ORIGEM_ADMINISTRATIVA if presente else None
        conn.execute(
            """INSERT INTO trabalhador_presenca
                   (trabalhador_id, data, presente, hora_chegada, hora_saida,
                    origem_chegada)
               VALUES (%s, %s::date, %s, %s, NULL, %s)""",
            (trabalhador_id, data, presente, nova_hora, origem),
        )
        novo = _snapshot_novo(presente, nova_hora if presente else None,
                              None, origem, None)
        resultado = {"presente": presente,
                     "hora_chegada": nova_hora if presente else None,
                     "origem_chegada": origem}

    elif operacao == "anular":
        if not fato:
            raise FatoInexistente("não há lançamento para anular nesta data")
        conn.execute(
            "DELETE FROM trabalhador_presenca WHERE id = %s", (fato["id"],),
        )
        novo = _snapshot_novo(None, None, None, None, None)
        resultado = {"presente": None, "hora_chegada": None, "origem_chegada": None}

    # A linha de auditoria é a prova do fato — grava junto, na mesma
    # transação. Qualquer falha aqui desfaz a alteração do fato também.
    conn.execute(
        """INSERT INTO trabalhador_presenca_correcao
               (trabalhador_id, data_fato, operacao,
                anterior_presente, anterior_hora_chegada, anterior_hora_saida,
                anterior_origem, anterior_dia_trabalho_id,
                novo_presente, novo_hora_chegada, novo_hora_saida,
                novo_origem, novo_dia_trabalho_id,
                motivo, atendente_id)
           VALUES (%s, %s::date, %s,
                   %s, %s, %s, %s, %s,
                   %s, %s, %s, %s, %s,
                   %s, %s)""",
        (trabalhador_id, data, _AUDIT_OPERACAO[operacao],
         anterior["anterior_presente"], anterior["anterior_hora_chegada"],
         anterior["anterior_hora_saida"], anterior["anterior_origem"],
         anterior["anterior_dia_trabalho_id"],
         novo["novo_presente"], novo["novo_hora_chegada"], novo["novo_hora_saida"],
         novo["novo_origem"], novo["novo_dia_trabalho_id"],
         motivo, atendente_id),
    )

    return {
        "trabalhador_id": trab["id"],
        "nome_completo": trab["nome_completo"],
        "data": data,
        "operacao": _AUDIT_OPERACAO[operacao],
        **resultado,
    }


def historico_da_data(conn, trabalhador_id: int, data: str) -> list:
    """Alterações desta data, em ordem temporal (append-only; nunca editado)."""
    rows = conn.execute(
        """SELECT c.*, a.nome_completo AS operador
             FROM trabalhador_presenca_correcao c
             LEFT JOIN atendentes a ON a.id = c.atendente_id
            WHERE c.trabalhador_id = %s AND c.data_fato = %s::date
            ORDER BY c.id""",
        (trabalhador_id, data),
    ).fetchall()
    return [dict(r) for r in rows]
