"""TASK_018 — conclusão administrativa de planos e vínculo de sessões.

Fonte única das transições que mudam a contagem de um plano de tratamento:
acrescentar sessão, marcar/desfazer realizado e cancelar sessão. A conclusão
é automática apenas para planos nascidos sob a regra (`regra_conclusao=1`) e
nunca depende da data corrente — a fronteira é persistida no registro.

Concorrência: todo ponto de entrada começa por `SELECT ... FOR UPDATE` na
linha do plano. Assim, inclusão de sessão e registro da última realização
disputam a mesma trava, na mesma ordem (plano → sessões → avaliação), sem
`sleeps` e sem escrita parcial: quem chega depois enxerga o estado já
confirmado pelo primeiro.
"""

ORIGEM_AUTOMATICA = "cumprimento_automatico"
# Mesmo relógio textual usado pela auditoria existente (operacao_idempotencia).
_AGORA = "(CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp(0)::text"


class ReavaliarRecusado(Exception):
    """Reabertura automática criaria dois planos ativos: desfaz tudo (rollback)."""

    def __init__(self, mensagem):
        super().__init__(mensagem)
        self.mensagem = mensagem


# ── leitura do estado do plano ────────────────────────────────────────────────

def _cumpridas(conn, plano_id):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM agendamentos WHERE plano_id = %s AND status = 'realizado'",
        (plano_id,),
    ).fetchone()["n"]


def _pendentes(conn, plano_id):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM agendamentos WHERE plano_id = %s AND status = 'agendado'",
        (plano_id,),
    ).fetchone()["n"]


def estado_plano(conn, plano_id):
    """Snapshot legível (sem travar) do plano — para telas e mensagens."""
    return conn.execute(
        """SELECT id, medium_id, data_inicio, sessoes_total, sessoes_previstas,
                  sessoes_adicionais, regra_conclusao, status, sessoes_realizadas
           FROM planos_tratamento WHERE id = %s""",
        (plano_id,),
    ).fetchone()


# ── avaliação central ─────────────────────────────────────────────────────────

def _deve_concluir(conn, plano):
    if plano["status"] != "ativo" or (plano["regra_conclusao"] or 0) != 1:
        return False
    previstas = plano["sessoes_previstas"]
    if previstas is None:
        # metadado ausente: não concluímos por suposição
        return False
    a_cumprir = previstas + (plano["sessoes_adicionais"] or 0)
    if a_cumprir <= 0:
        return False
    return _cumpridas(conn, plano["id"]) >= a_cumprir and _pendentes(conn, plano["id"]) == 0


def _outro_plano_ativo_da_pessoa(conn, plano_id):
    """Há outro plano ativo (não este) para qualquer pessoa deste plano?"""
    return conn.execute(
        """SELECT 1 FROM plano_pessoas pp
           JOIN planos_tratamento pt2 ON pt2.id = pp.plano_id
           WHERE pp.pessoa_id IN (SELECT pessoa_id FROM plano_pessoas WHERE plano_id = %s)
             AND pt2.status = 'ativo' AND pt2.id <> %s
           LIMIT 1""",
        (plano_id, plano_id),
    ).fetchone()


def _esta_incompleto(conn, plano):
    """Concluído automaticamente que deixou de cumprir a quota (desfeito/revogado)."""
    if plano["sessoes_previstas"] is None:
        return False
    a_cumprir = plano["sessoes_previstas"] + (plano["sessoes_adicionais"] or 0)
    return _cumpridas(conn, plano["id"]) < a_cumprir


def reavaliar(conn, plano_id, autor_id=None):
    """Aplica a regra de conclusão/reabertura. Chamar com a trava do plano já
    adquirida, dentro da transação da operação que alterou a contagem.

    Retorna 'concluido', 'reaberto' ou 'nada'. Levanta ReavaliarRecusado se a
    reabertura automática implicasse dois planos ativos (o chamador faz
    rollback ao propagar)."""
    plano = conn.execute(
        "SELECT * FROM planos_tratamento WHERE id = %s", (plano_id,)
    ).fetchone()
    if plano is None:
        return "nada"

    if plano["status"] == "concluido":
        # Reabertura só do que foi concluído automaticamente; alta e
        # encerramento manual jamais são revertidos.
        if plano["conclusao_origem"] != ORIGEM_AUTOMATICA:
            return "nada"
        if not _esta_incompleto(conn, plano):
            return "nada"
        if _outro_plano_ativo_da_pessoa(conn, plano_id):
            raise ReavaliarRecusado(
                "A correção reabriria este plano, mas a pessoa já tem outro "
                "plano ativo. Revise manualmente antes de desfazer."
            )
        conn.execute(
            f"""UPDATE planos_tratamento
                SET status = 'ativo',
                    conclusao_desfeita_em = {_AGORA},
                    conclusao_desfeita_por = %s,
                    concluido_em = NULL, concluido_por = NULL,
                    conclusao_origem = NULL
                WHERE id = %s""",
            (autor_id, plano_id),
        )
        return "reaberto"

    if _deve_concluir(conn, plano):
        conn.execute(
            f"""UPDATE planos_tratamento
                SET status = 'concluido',
                    concluido_em = {_AGORA},
                    concluido_por = %s,
                    conclusao_origem = %s
                WHERE id = %s""",
            (autor_id, ORIGEM_AUTOMATICA, plano_id),
        )
        return "concluido"

    return "nada"


# ── travas de entrada (todas começam pela linha do plano) ────────────────────

def _travar(conn, plano_id):
    return conn.execute(
        "SELECT id FROM planos_tratamento WHERE id = %s FOR UPDATE", (plano_id,)
    ).fetchone()


def marcar_realizado(conn, plano_id, agendamento_id, autor_id=None):
    """Registra a realização de uma sessão sob a trava do plano. Retorna o
    efeito da avaliação ('concluido'/'nada'). A MESMA linha de agenda não é
    contada duas vezes (transição de status guardada)."""
    if plano_id:
        _travar(conn, plano_id)
    contar = True
    if agendamento_id:
        ja = conn.execute(
            "SELECT status FROM agendamentos WHERE id = %s", (agendamento_id,)
        ).fetchone()
        if ja and ja["status"] == "realizado":
            contar = False
        else:
            conn.execute(
                "UPDATE agendamentos SET status = 'realizado' WHERE id = %s",
                (agendamento_id,),
            )
    if plano_id and contar:
        conn.execute(
            "UPDATE planos_tratamento SET sessoes_realizadas = sessoes_realizadas + 1 WHERE id = %s",
            (plano_id,),
        )
    if plano_id:
        return reavaliar(conn, plano_id, autor_id)
    return "nada"


def desfazer_realizado(conn, plano_id, agendamento_id, autor_id=None):
    """Desfaz a realização. Pode reabrir um plano concluído automaticamente
    (ReavaliarRecusado se isso criasse dois ativos)."""
    if plano_id:
        _travar(conn, plano_id)
    contar = True
    if agendamento_id:
        ja = conn.execute(
            "SELECT status FROM agendamentos WHERE id = %s", (agendamento_id,)
        ).fetchone()
        if ja and ja["status"] != "realizado":
            contar = False
        else:
            conn.execute(
                "UPDATE agendamentos SET status = 'agendado' WHERE id = %s",
                (agendamento_id,),
            )
    if plano_id and contar:
        conn.execute(
            "UPDATE planos_tratamento SET sessoes_realizadas = GREATEST(0, sessoes_realizadas - 1) WHERE id = %s",
            (plano_id,),
        )
    if plano_id:
        return reavaliar(conn, plano_id, autor_id)
    return "nada"


def acrescentar_sessao(conn, plano_id, data, requer_passe=0, encaixe=0,
                       tipo="adicao", autor_id=None):
    """Acrescenta uma sessão ao plano. `tipo='adicao'` (sessão extra) aumenta
    a extensão líquida; `tipo='reposicao'` (substituir uma já vencida) não.
    Retorna 'ok', 'data_ja_agendada' ou 'plano_nao_ativo'. O status é conferido
    DEPOIS da trava: se a última realização concluir o plano entre a tela e
    esta escrita, a inclusão espera e é recusada (sem reabrir, sem parcial)."""
    _travar(conn, plano_id)
    plano = conn.execute(
        "SELECT status, regra_conclusao FROM planos_tratamento WHERE id = %s", (plano_id,)
    ).fetchone()
    if not plano or plano["status"] != "ativo":
        return "plano_nao_ativo"
    duplicada = conn.execute(
        """SELECT 1 FROM agendamentos
           WHERE plano_id = %s AND data = %s AND status = 'agendado' LIMIT 1""",
        (plano_id, data),
    ).fetchone()
    if duplicada:
        return "data_ja_agendada"
    conn.execute(
        """INSERT INTO agendamentos
           (plano_id, data, status, requer_passe, encaixe, adicionada)
           VALUES (%s, %s, 'agendado', %s, %s, %s)""",
        (plano_id, data, 1 if requer_passe else 0, 1 if encaixe else 0,
         1 if tipo == "adicao" else 0),
    )
    if tipo == "adicao":
        conn.execute(
            "UPDATE planos_tratamento SET sessoes_adicionais = sessoes_adicionais + 1 WHERE id = %s",
            (plano_id,),
        )
    reavaliar(conn, plano_id, autor_id)
    return "ok"


def cancelar_sessao(conn, plano_id, agendamento_id, autor_id=None):
    """Cancela uma sessão. Cancelar extensão ainda não realizada revoga a
    extensão (+1 volta); cancelar sessão prevista NÃO reduz a previsão —
    cancelamento não é cumprimento. Retorna a linha afetada para auditoria."""
    _travar(conn, plano_id)
    linha = conn.execute(
        "SELECT status, adicionada FROM agendamentos WHERE id = %s AND plano_id = %s",
        (agendamento_id, plano_id),
    ).fetchone()
    if not linha or linha["status"] == "cancelado":
        return "nada"
    conn.execute(
        "UPDATE agendamentos SET status = 'cancelado' WHERE id = %s AND plano_id = %s",
        (agendamento_id, plano_id),
    )
    if linha["adicionada"] and linha["status"] == "agendado":
        conn.execute(
            "UPDATE planos_tratamento SET sessoes_adicionais = GREATEST(0, sessoes_adicionais - 1) WHERE id = %s",
            (plano_id,),
        )
    reavaliar(conn, plano_id, autor_id)
    return "cancelado"
