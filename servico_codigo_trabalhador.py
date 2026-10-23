"""Serviço de código de identificação do trabalhador (TASK_002).

Regra única de geração, troca e resolução do código de 4 dígitos, para uso
pelo cadastro (agora) e pelo futuro terminal QR/teclado (TASK_003).

Modelo: uma linha por código JÁ EMITIDO em `trabalhador_codigo`.
  - estado='ativo'   → código atual do trabalhador (exatamente um por pessoa,
                       garantido por índice único parcial no banco);
  - estado='retirado'→ código que saiu de circulação, fica reservado para
                       sempre: não é reatribuído a outra pessoa nem ao próprio
                       trabalhador (é o que impede que uma carteirinha antiga
                       identifique outra pessoa).

O código é texto de exatamente 4 caracteres 0-9 — `0047` é `0047` em todo o
fluxo (banco, tela, QR, resolução). Nunca derivado de CPF, telefone,
nascimento ou id do cadastro.

Conteúdo do QR: exatamente os quatro caracteres, como texto. Nada de URL,
nome, documento, dado financeiro, id interno ou token.

Nada aqui registra presença: quem resolve um código chama, depois, o serviço
da TASK_001 (servico_presenca).
"""

import base64
import io

import qrcode

from servico_presenca import TrabalhadorInativo, TrabalhadorInexistente


class CodigosEsgotados(Exception):
    """Não sobrou nenhum dos 10.000 códigos disponíveis."""


class TrabalhadorSemCodigo(Exception):
    """O trabalhador não tem código atual (impressão exige gerar antes)."""


_TENTATIVAS = 8


def _agora_sao_paulo(conn):
    """Instante do servidor como texto 'AAAA-MM-DD HH:MM' em America/Sao_Paulo.

    Igual ao serviço de presença: nunca usar o relógio do sistema do app.
    """
    row = conn.execute(
        """SELECT to_char(NOW() AT TIME ZONE 'America/Sao_Paulo',
                          'YYYY-MM-DD HH24:MI') AS instante"""
    ).fetchone()
    return row["instante"]


def _trabalhador(conn, trabalhador_id, travar=False):
    """Lê a pessoa; `travar=True` fecha a linha para a transação inteira.

    A trava é da linha do trabalhador, não do índice: quem chegar depois espera,
    re-lê o código já emitido (READ COMMITTED) e o preserva em vez de violar o
    índice único parcial e desfazer a transação do concorrente.
    """
    trab = conn.execute(
        "SELECT id, nome_completo, ativo FROM trabalhadores WHERE id = %s"
        + (" FOR UPDATE" if travar else ""),
        (trabalhador_id,),
    ).fetchone()
    if not trab:
        raise TrabalhadorInexistente(f"trabalhador {trabalhador_id} não existe")
    return trab


def codigo_valido(codigo) -> bool:
    """Exatamente quatro caracteres ASCII de 0 a 9 (zeros iniciais incluídos)."""
    return isinstance(codigo, str) and len(codigo) == 4 and codigo.isdigit() and codigo.isascii()


def _emitir(conn, trabalhador_id, atendente_id, origem, instante) -> str:
    """Sorteia um código livre e o emite, dentro da transação do chamador.

    ON CONFLICT DO NOTHING não aborta a transação: se outro processo tomar o
    mesmo código primeiro, a próxima tentativa re-lê o espaço livre (READ
    COMMITTED) e acha outro. Esgotados as tentativas sem sucesso, não há código
    livre — falha controlada, sem reutilizar nada.
    """
    for _ in range(_TENTATIVAS):
        row = conn.execute(
            """INSERT INTO trabalhador_codigo
                       (codigo, trabalhador_id, estado, atendente_id, origem, criado_em)
               SELECT to_char(n, 'FM0000'), %s, 'ativo', %s, %s, %s
                 FROM generate_series(0, 9999) AS n
                WHERE NOT EXISTS (SELECT 1 FROM trabalhador_codigo c
                                   WHERE c.codigo = to_char(n, 'FM0000'))
                ORDER BY random()
                LIMIT 1
               ON CONFLICT (codigo) DO NOTHING
               RETURNING codigo""",
            (trabalhador_id, atendente_id, origem, instante),
        ).fetchone()
        if row:
            return row["codigo"]
    raise CodigosEsgotados("todos os 10.000 códigos já foram emitidos")


def garantir_codigo(conn, trabalhador_id, atendente_id=None, origem="tela") -> dict:
    """Código atual do trabalhador, gerando se faltar. `criado` diz o que houve."""
    trab = _trabalhador(conn, trabalhador_id, travar=True)
    if not trab["ativo"]:
        raise TrabalhadorInativo(f"trabalhador {trabalhador_id} está inativo")

    atual = conn.execute(
        """SELECT codigo FROM trabalhador_codigo
            WHERE trabalhador_id = %s AND estado = 'ativo'""",
        (trabalhador_id,),
    ).fetchone()
    if atual:
        return {"trabalhador_id": trab["id"], "nome_completo": trab["nome_completo"],
                "codigo": atual["codigo"], "criado": False}

    codigo = _emitir(conn, trabalhador_id, atendente_id, origem, _agora_sao_paulo(conn))
    return {"trabalhador_id": trab["id"], "nome_completo": trab["nome_completo"],
            "codigo": codigo, "criado": True}


def trocar_codigo(conn, trabalhador_id, atendente_id=None, origem="tela") -> dict:
    """Troca o código atual — atômica, na transação do chamador.

    Retira o antigo e só então emite o novo (inserir antes violaria a unicidade
    do código atual). Se a emissão falhar — colisão persistente ou espaço
    esgotado — a transação inteira desfaz e o código atual continua válido.
    """
    trab = _trabalhador(conn, trabalhador_id, travar=True)
    atual = conn.execute(
        """SELECT codigo FROM trabalhador_codigo
            WHERE trabalhador_id = %s AND estado = 'ativo'""",
        (trabalhador_id,),
    ).fetchone()
    if not atual:
        raise TrabalhadorSemCodigo(
            f"trabalhador {trabalhador_id} não tem código atual — gere um antes de trocar"
        )

    conn.execute(
        """UPDATE trabalhador_codigo SET estado = 'retirado', retirado_em = %s
            WHERE codigo = %s AND estado = 'ativo'""",
        (_agora_sao_paulo(conn), atual["codigo"]),
    )
    novo = _emitir(conn, trabalhador_id, atendente_id, origem, _agora_sao_paulo(conn))
    return {"trabalhador_id": trab["id"], "nome_completo": trab["nome_completo"],
            "anterior": atual["codigo"], "novo": novo}


def preparar_codigos_pendentes(conn, atendente_id=None, origem="tela") -> dict:
    """Emite os códigos que faltam nos trabalhadores ativos (ação explícita).

    Idempotente: uma segunda execução não gera nada e não altera os já
    emitidos. Uma transação só — se faltar código no meio, nada é aplicado.
    """
    pendentes = conn.execute(
        """SELECT t.id, t.nome_completo
             FROM trabalhadores t
            WHERE t.ativo = 1
              AND NOT EXISTS (SELECT 1 FROM trabalhador_codigo c
                               WHERE c.trabalhador_id = t.id AND c.estado = 'ativo')
            ORDER BY t.id
            FOR UPDATE OF t"""
    ).fetchall()
    ja_existiam = conn.execute(
        """SELECT COUNT(*) AS n FROM trabalhadores t
            WHERE t.ativo = 1
              AND EXISTS (SELECT 1 FROM trabalhador_codigo c
                           WHERE c.trabalhador_id = t.id AND c.estado = 'ativo')"""
    ).fetchone()["n"]

    instante = _agora_sao_paulo(conn)
    gerados = []
    for trab in pendentes:
        codigo = _emitir(conn, trab["id"], atendente_id, origem, instante)
        gerados.append({"trabalhador_id": trab["id"],
                        "nome_completo": trab["nome_completo"],
                        "codigo": codigo})
    return {"gerados": gerados, "ja_existiam": ja_existiam}


def resolver_codigo(conn, codigo) -> dict:
    """Resolve o código para o trabalhador — somente leitura (contrato TASK_003).

    Estados: invalido (formato), ativo, inativo (código é da pessoa, mas ela
    está inativada), retirado (saiu de circulação) e inexistente.
    """
    if not codigo_valido(codigo):
        return {"estado": "invalido"}

    row = conn.execute(
        """SELECT c.codigo, t.id AS trabalhador_id, t.nome_completo, t.ativo
             FROM trabalhador_codigo c
             JOIN trabalhadores t ON t.id = c.trabalhador_id
            WHERE c.codigo = %s AND c.estado = 'ativo'""",
        (codigo,),
    ).fetchone()
    if row:
        r = dict(row)
        return {"estado": "ativo" if r["ativo"] else "inativo", **r}

    emitido = conn.execute(
        "SELECT 1 AS x FROM trabalhador_codigo WHERE codigo = %s", (codigo,)
    ).fetchone()
    return {"estado": "retirado" if emitido else "inexistente"}


def qr_data_uri(codigo: str) -> str:
    """PNG do QR como data URI, preto sobre branco, com margem livre de 4 módulos.

    Conteúdo codificado = exatamente os quatro caracteres. Gerado localmente
    (biblioteca `qrcode`, já dependência do projeto) — sem serviço externo e
    sem CDN.
    """
    if not codigo_valido(codigo):
        raise ValueError(f"código inválido para QR: {codigo!r}")
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(codigo)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
