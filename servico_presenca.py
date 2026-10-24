"""Serviço de registro de chegada de trabalhadores (TASK_001).

Regra única, reutilizada pela recepção e, no futuro, pelo terminal QR/código:
uma presença por trabalhador por data local America/Sao_Paulo, determinada
pelo servidor (nunca pelo relógio do dispositivo).

A unicidade é garantida no banco (UNIQUE trabalhador_id, data); concorrência
é resolvida por ON CONFLICT — repetições nunca substituem a primeira chegada.

A origem da chegada (`origem_chegada`) é gravada junto com o horário:
`recepcao`, `terminal_qr` ou `terminal_codigo`. Registros anteriores a este
campo ficam com origem nula — origem desconhecida (legado) — e nunca é
presumida nem preenchida retroativamente.

`correcao_administrativa` (TASK_004) é origem de FATO LANÇADO/CONVERTIDO pelo
fluxo autorizado de correções — quem a escreve é exclusivamente
`servico_correcao_presenca`. `registrar_chegada` a recusa: nem recepção nem
terminal podem fabricar origem administrativa (ou obter tratamento privilegiado).
"""

ORIGENS_CHEGADA = ("recepcao", "terminal_qr", "terminal_codigo",
                   "correcao_administrativa")
ORIGEM_ADMINISTRATIVA = "correcao_administrativa"

# Rótulo por origem para as telas. NULL não tem chave: é "Não informada"
# (legado), nunca presumida.
ROTULOS_ORIGEM = {
    "terminal_qr": "QR",
    "terminal_codigo": "Código digitado",
    "recepcao": "Recepção",
    ORIGEM_ADMINISTRATIVA: "Correção administrativa",
}


def rotulo_origem(origem):
    return ROTULOS_ORIGEM.get(origem, "Não informada")


class OrigemInvalida(Exception):
    pass


class TrabalhadorInexistente(Exception):
    pass


class TrabalhadorInativo(Exception):
    pass


def _agora_sao_paulo(conn, fonte="NOW()"):
    """Data (YYYY-MM-DD) e hora (HH:MM) do servidor em America/Sao_Paulo.

    `fonte` é a expressão SQL do instante (timestamptz); os fluxos normais
    usam NOW(). Testes injetam um instante fixo para conferir a virada
    UTC/São Paulo sem depender do relógio do sistema.
    """
    row = conn.execute(
        f"""SELECT (({fonte} AT TIME ZONE 'America/Sao_Paulo')::date)::text AS data,
                   to_char({fonte} AT TIME ZONE 'America/Sao_Paulo', 'HH24:MI') AS hora"""
    ).fetchone()
    return row["data"], row["hora"]


def registrar_chegada(conn, trabalhador_id: int, origem: str = "recepcao") -> dict:
    """Registra a chegada do trabalhador na data local de São Paulo.

    - Exige trabalhador existente e ativo.
    - Não cria nem exige dia de atendimento (dias_trabalho).
    - Já presente: preserva o horário existente (inclusive histórico sem
      horário — nada é inventado) e também preserva a origem dele; retorna
      criado=False.
    - Ausência explícita na data: converte para presente e registra a chegada
      atual, com a origem desta chegada — é chegada nova, e `criado` é True.
      Ausência sem registro não é falta e não é gerada aqui.
    """
    if origem not in ORIGENS_CHEGADA:
        raise OrigemInvalida(f"origem de chegada desconhecida: {origem!r}")
    if origem == ORIGEM_ADMINISTRATIVA:
        # Origem de correção autorizada, escrita só pelo serviço de correções.
        # Chegada normal (recepção/terminal) não pode produzi-la.
        raise OrigemInvalida("origem administrativa não é chegada normal")

    # A mesma trava de linha que garantir_codigo/trocar_codigo usam: quem chegar
    # junto (terminal, recepção, troca de código) espera a vez e re-lê o estado
    # já escrito, em vez de adivinhar pelo que viu antes do bloqueio.
    trab = conn.execute(
        "SELECT id, nome_completo, ativo FROM trabalhadores WHERE id = %s FOR UPDATE",
        (trabalhador_id,),
    ).fetchone()
    if not trab:
        raise TrabalhadorInexistente(f"trabalhador {trabalhador_id} não existe")
    if not trab["ativo"]:
        raise TrabalhadorInativo(f"trabalhador {trabalhador_id} está inativo")

    data, hora = _agora_sao_paulo(conn)
    # Estado anterior, lido DEPOIS do bloqueio da pessoa: quem chegar junto espera
    # a vez e então enxerga a linha já escrita. A escrita em si continua sendo o
    # upsert atômico abaixo — esta leitura só decide a palavra "registrada".
    antes = conn.execute(
        """SELECT presente FROM trabalhador_presenca
            WHERE trabalhador_id = %s AND data = %s::date""",
        (trabalhador_id, data),
    ).fetchone()
    row = conn.execute(
        """INSERT INTO trabalhador_presenca
               (trabalhador_id, data, presente, hora_chegada, origem_chegada)
           VALUES (%s, %s::date, 1, %s, %s)
           ON CONFLICT (trabalhador_id, data) DO UPDATE
             SET presente = 1,
                 hora_chegada = CASE
                     WHEN trabalhador_presenca.presente = 1
                         THEN trabalhador_presenca.hora_chegada
                     ELSE COALESCE(trabalhador_presenca.hora_chegada,
                                   EXCLUDED.hora_chegada)
                 END,
                 origem_chegada = CASE
                     WHEN trabalhador_presenca.presente = 1
                         THEN trabalhador_presenca.origem_chegada
                     WHEN trabalhador_presenca.hora_chegada IS NOT NULL
                         THEN trabalhador_presenca.origem_chegada
                     ELSE EXCLUDED.origem_chegada
                 END
           RETURNING (xmax = 0) AS criado, presente, hora_chegada,
                     origem_chegada, data::text AS data""",
        (trabalhador_id, data, hora, origem),
    ).fetchone()
    return {
        "trabalhador_id": trab["id"],
        "nome_completo": trab["nome_completo"],
        "data": row["data"],
        "hora_chegada": row["hora_chegada"],
        "presente": row["presente"],
        "origem_chegada": row["origem_chegada"],
        # "criado" = esta chamada estabeleceu a presença: linha nova, ou ausência
        # explícita convertida em chegada. Repetição de presença é False.
        "criado": bool(row["criado"]) or not (antes and antes["presente"]),
    }


def registrar_ausencia_explicita(conn, trabalhador_id: int) -> dict:
    """Registra ausência explícita na data local, quando isso não altera um fato.

    TASK_004 redesenhou esta função: o upsert antigo rebaixava uma presença
    para ausência em silêncio (sem trava, sem motivo, sem auditoria) e deixava
    o horário velho pendurado na linha. Hoje:

    - sem registro na data: cria a ausência (lançamento normal da recepção,
      `resultado="registrada"`) — não é correção e não gera auditoria;
    - já ausente: não escreve nada (`"ja_ausente"`) — repetição não fabrica
      correção nem muda horário;
    - já presente: RECUSA (`"exige_correcao"`) — alterar um fato já lançado
      só pelo fluxo autorizado de correções, com motivo e auditoria.

    Ausência sem registro não é falta; ausência registrada nunca carrega
    horário de chegada.
    """
    # A mesma trava da chegada: recepção marcando ausente e terminal marcando
    # presença no mesmo instante não podem cruzar entre leitura e escrita.
    trab = conn.execute(
        "SELECT id, nome_completo, ativo FROM trabalhadores WHERE id = %s FOR UPDATE",
        (trabalhador_id,),
    ).fetchone()
    if not trab:
        raise TrabalhadorInexistente(f"trabalhador {trabalhador_id} não existe")
    if not trab["ativo"]:
        raise TrabalhadorInativo(f"trabalhador {trabalhador_id} está inativo")

    data, _hora = _agora_sao_paulo(conn)
    atual = conn.execute(
        """SELECT presente, hora_chegada FROM trabalhador_presenca
            WHERE trabalhador_id = %s AND data = %s::date""",
        (trabalhador_id, data),
    ).fetchone()
    if atual and atual["presente"]:
        return {
            "resultado": "exige_correcao",
            "trabalhador_id": trab["id"],
            "nome_completo": trab["nome_completo"],
            "data": data,
            "hora_chegada": atual["hora_chegada"],
            "presente": 1,
            "criado": False,
        }
    if atual:
        return {
            "resultado": "ja_ausente",
            "trabalhador_id": trab["id"],
            "nome_completo": trab["nome_completo"],
            "data": data,
            "hora_chegada": atual["hora_chegada"],
            "presente": 0,
            "criado": False,
        }
    row = conn.execute(
        """INSERT INTO trabalhador_presenca
               (trabalhador_id, data, presente, hora_chegada, hora_saida)
           VALUES (%s, %s::date, 0, NULL, NULL)
           RETURNING presente, data::text AS data""",
        (trabalhador_id, data),
    ).fetchone()
    return {
        "resultado": "registrada",
        "trabalhador_id": trab["id"],
        "nome_completo": trab["nome_completo"],
        "data": row["data"],
        "hora_chegada": None,
        "presente": 0,
        "criado": True,
    }
