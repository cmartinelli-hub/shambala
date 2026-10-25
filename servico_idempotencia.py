"""Idempotência das operações financeiras (TASK_012 §2).

Contrato: uma tentativa confirmada pelo operador produz UM efeito, mesmo com
duplo clique, reenvio depois de perdida a resposta, timeout ou duas requisições
simultâneas. Duas tentativas novas e legítimas, ainda que com conteúdo
idêntico, continuam sendo duas operações — por isso a chave nasce com a tela
(aberta/renderizada antes do envio) e é mantida enquanto a mesma tentativa for
reenviada; uma operação nova recebe chave nova.

A reserva e a confirmação vivem na MESMA transação dos efeitos: falha
intermediária não deixa chave consumida sem documento, e o reenvio concorrente
aguarda a primeira transação resolver (unicidade em `chave`).

A chave é identidade de tentativa, não autenticação: login, sessão e validação
de valores continuam valendo antes de reservar. Depois de respondida, porém, a
tentativa devolve a resposta original — o reenvio não re-discute com o estado
de hoje (`reconhecer`), porque "já fiz" e "não dá mais para fazer" são
mensagens que levam o operador a tentar de novo.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets

# Desfechos de `reservar`
NOVA = "nova"
REPETIDA = "repetida"
CONFLITO = "conflito"
PENDENTE = "em_andamento"

_CHAVE_OK = re.compile(r"^[0-9a-f]{16,64}$")

# Chave de recusa → mensagem legível (fonte única; as telas replicam o texto).
MENSAGENS_TENTATIVA = {
    "tentativa_sem_chave":
        "A tela não pôde registrar esta operação: falta o identificador da "
        "tentativa. Recarregue a página e tente novamente.",
    "tentativa_conflito":
        "A mesma tentativa foi enviada com conteúdos diferentes. Nada foi "
        "gravado — recarregue a página e refaça a operação.",
    "tentativa_em_andamento":
        "Esta tentativa ainda não foi concluída. Recarregue a página e "
        "confira se a operação já está registrada antes de tentar de novo.",
}


def nova_chave() -> str:
    """Chave de uma tentativa nova (gerada antes do envio, nunca de um id criado)."""
    return secrets.token_hex(16)


def normalizar_chave(valor) -> str | None:
    """Aceita somente chaves de tentativa bem formadas; nada de inventar chave."""
    if valor is None:
        return None
    limpa = str(valor).strip().lower().replace("-", "")
    return limpa if _CHAVE_OK.match(limpa) else None


def _hash_corpo(corpo: dict) -> str:
    """Hash do conteúdo SEMÂNTICO (campos canonizados). Valores que o servidor
    recalcula — preço, subtotal, troco — não entram: quem manda é o servidor."""
    canon = json.dumps(corpo, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def reservar(conn, *, chave: str, operacao: str, corpo: dict,
             autor_id: int | None = None):
    """Reserva a tentativa. Retorna (desfecho, registro).

    NOVA     → siga escrevendo e chame `confirmar` na mesma transação.
    REPETIDA → mesmo resultado/documento (registro["destino"]); nenhum efeito.
    CONFLITO → mesma chave com conteúdo divergente: não escrever.
    PENDENTE → tentativa sem conclusão confirmada: não escrever por cima.
    """
    hash_ = _hash_corpo(corpo)
    inserida = conn.execute(
        """INSERT INTO operacao_idempotencia
               (chave, operacao, corpo_hash, autor_id, status)
           VALUES (%s, %s, %s, %s, 'em_andamento')
           ON CONFLICT (chave) DO NOTHING
           RETURNING id""",
        (chave, operacao, hash_, autor_id),
    ).fetchone()
    if inserida:
        return NOVA, None

    existente = conn.execute(
        """SELECT id, operacao, corpo_hash, status, documento, destino
           FROM operacao_idempotencia WHERE chave = %s FOR UPDATE""",
        (chave,),
    ).fetchone()
    if existente is None:
        return PENDENTE, None
    if existente["status"] != "concluida":
        return PENDENTE, existente
    return (REPETIDA if existente["corpo_hash"] == hash_ else CONFLITO), existente


def confirmar(conn, *, chave: str, documento=None, destino=None):
    """Marca a tentativa como concluída, com o documento e a resposta já
    calculados — o reenvio devolve exatamente esta resposta."""
    conn.execute(
        """UPDATE operacao_idempotencia
           SET status = 'concluida', documento = %s, destino = %s,
               concluido_em = (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp(0)::text
           WHERE chave = %s""",
        (None if documento is None else str(documento),
         None if destino is None else str(destino), chave),
    )


def liberar(conn, *, chave: str):
    """Desiste da reserva (tentativa que vai recusar sem escrever): a mesma
    chave pode ser reenviada em seguida, sem conflito artificial."""
    conn.execute(
        "DELETE FROM operacao_idempotencia WHERE chave = %s AND status = 'em_andamento'",
        (chave,),
    )


class TentativaRecusada(Exception):
    """Recusa de tentativa já traduzível para a tela (sem jargão SQL)."""

    def __init__(self, chave_erro: str):
        super().__init__(MENSAGENS_TENTATIVA[chave_erro])
        self.chave_erro = chave_erro


class TentativaJaRegistrada(Exception):
    """Reenvio da MESMA tentativa já concluída: devolver o resultado original."""

    def __init__(self, chave: str, destino: str | None, documento):
        super().__init__("Esta tentativa já foi registrada.")
        self.chave = chave
        self.destino = destino
        self.documento = documento


def reconhecer(conn, *, idempotencia, corpo: dict) -> str:
    """Confere a identidade da tentativa ANTES das validações de estado.

    Uma tentativa já concluída tem resposta; essa resposta não muda porque o
    mundo mudou depois dela (a pendência baixou, a venda foi quitada, a sessão
    fechou). Devolver hoje o que foi devolvido na primeira vez é o que faz o
    duplo clique — e o "não recebi confirmação, vou enviar de novo" — terminar
    sem segunda operação e sem mensagem enganosa.

    Não escreve nada: quem reserva a tentativa continua sendo `exigir_reserva`,
    depois das validações, para nenhuma chave ficar consumida sem documento.
    Levanta `TentativaJaRegistrada` (reenvio do mesmo corpo) ou
    `TentativaRecusada` (sem chave; corpo divergente da mesma chave).
    """
    chave = normalizar_chave(idempotencia)
    if not chave:
        raise TentativaRecusada("tentativa_sem_chave")
    registrada = conn.execute(
        """SELECT status, corpo_hash, documento, destino
           FROM operacao_idempotencia WHERE chave = %s""",
        (chave,),
    ).fetchone()
    if registrada is None:
        return chave
    if registrada["status"] != "concluida":
        raise TentativaRecusada("tentativa_em_andamento")
    if registrada["corpo_hash"] != _hash_corpo(corpo):
        raise TentativaRecusada("tentativa_conflito")
    raise TentativaJaRegistrada(chave, registrada["destino"], registrada["documento"])


def exigir_reserva(conn, *, idempotencia, operacao: str, corpo: dict,
                   autor_id: int | None = None):
    """Atalho das rotas: valida a chave, reserva e devolve a chave reservada.

    Levanta `TentativaJaRegistrada` quando a tentativa já produziu efeito — o
    chamador devolve o destino original, sem nenhum novo efeito — e
    `TentativaRecusada` quando ela não pode seguir (sem chave, conteúdo
    divergente da mesma chave ou tentativa pendente).
    """
    chave = normalizar_chave(idempotencia)
    if not chave:
        raise TentativaRecusada("tentativa_sem_chave")
    desfecho, registro = reservar(conn, chave=chave, operacao=operacao,
                                  corpo=corpo, autor_id=autor_id)
    if desfecho == REPETIDA:
        raise TentativaJaRegistrada(chave, (registro or {}).get("destino"),
                                    (registro or {}).get("documento"))
    if desfecho == CONFLITO:
        raise TentativaRecusada("tentativa_conflito")
    if desfecho == PENDENTE:
        raise TentativaRecusada("tentativa_em_andamento")
    return chave
