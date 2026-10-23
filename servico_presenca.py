"""Serviço de registro de chegada de trabalhadores (TASK_001).

Regra única, reutilizada pela recepção e, no futuro, pelo terminal QR/código:
uma presença por trabalhador por data local America/Sao_Paulo, determinada
pelo servidor (nunca pelo relógio do dispositivo).

A unicidade é garantida no banco (UNIQUE trabalhador_id, data); concorrência
é resolvida por ON CONFLICT — repetições nunca substituem a primeira chegada.
"""


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


def registrar_chegada(conn, trabalhador_id: int) -> dict:
    """Registra a chegada do trabalhador na data local de São Paulo.

    - Exige trabalhador existente e ativo.
    - Não cria nem exige dia de atendimento (dias_trabalho).
    - Já presente: preserva o horário existente (inclusive histórico sem
      horário — nada é inventado); retorna criado=False.
    - Ausência explícita na data: converte para presente e registra a chegada
      atual. Ausência sem registro não é falta e não é gerada aqui.
    """
    trab = conn.execute(
        "SELECT id, nome_completo, ativo FROM trabalhadores WHERE id = %s",
        (trabalhador_id,),
    ).fetchone()
    if not trab:
        raise TrabalhadorInexistente(f"trabalhador {trabalhador_id} não existe")
    if not trab["ativo"]:
        raise TrabalhadorInativo(f"trabalhador {trabalhador_id} está inativo")

    data, hora = _agora_sao_paulo(conn)
    row = conn.execute(
        """INSERT INTO trabalhador_presenca
               (trabalhador_id, data, presente, hora_chegada)
           VALUES (%s, %s::date, 1, %s)
           ON CONFLICT (trabalhador_id, data) DO UPDATE
             SET presente = 1,
                 hora_chegada = CASE
                     WHEN trabalhador_presenca.presente = 1
                         THEN trabalhador_presenca.hora_chegada
                     ELSE COALESCE(trabalhador_presenca.hora_chegada,
                                   EXCLUDED.hora_chegada)
                 END
           RETURNING (xmax = 0) AS criado, presente, hora_chegada,
                     data::text AS data""",
        (trabalhador_id, data, hora),
    ).fetchone()
    return {
        "trabalhador_id": trab["id"],
        "nome_completo": trab["nome_completo"],
        "data": row["data"],
        "hora_chegada": row["hora_chegada"],
        "presente": row["presente"],
        "criado": bool(row["criado"]),
    }


def registrar_ausencia_explicita(conn, trabalhador_id: int) -> dict:
    """Marca ausência explícita na data local (mantém o que a tela já oferecia).

    Ausência nunca apaga horário de chegada já registrado; ausência de
    registro não é falta — aqui só existe registro explícito.
    """
    trab = conn.execute(
        "SELECT id, nome_completo, ativo FROM trabalhadores WHERE id = %s",
        (trabalhador_id,),
    ).fetchone()
    if not trab:
        raise TrabalhadorInexistente(f"trabalhador {trabalhador_id} não existe")
    if not trab["ativo"]:
        raise TrabalhadorInativo(f"trabalhador {trabalhador_id} está inativo")

    data, _hora = _agora_sao_paulo(conn)
    row = conn.execute(
        """INSERT INTO trabalhador_presenca
               (trabalhador_id, data, presente, hora_chegada, hora_saida)
           VALUES (%s, %s::date, 0, NULL, NULL)
           ON CONFLICT (trabalhador_id, data) DO UPDATE
             SET presente = 0
           RETURNING (xmax = 0) AS criado, presente, hora_chegada,
                     data::text AS data""",
        (trabalhador_id, data),
    ).fetchone()
    return {
        "trabalhador_id": trab["id"],
        "nome_completo": trab["nome_completo"],
        "data": row["data"],
        "hora_chegada": row["hora_chegada"],
        "presente": row["presente"],
        "criado": bool(row["criado"]),
    }
