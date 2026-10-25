"""Resposta de tentativa já registrada — usado pelas rotas de dinheiro.

A lógica de reconhecer a chave vive em `servico_idempotencia`; aqui só se
traduz o desfecho para a língua das rotas HTTP: um RedirectResponse quando a
tentativa já tem resposta (ou não pode seguir), None quando a rota pode passar
às validações de estado e reservar a tentativa.
"""
from fastapi.responses import RedirectResponse

from servico_idempotencia import (
    TentativaJaRegistrada, TentativaRecusada, reconhecer,
)


def resposta_de_tentativa(conn, *, idempotencia, corpo, erro_para, destino_padrao,
                          operacao=None, autor_id=None):
    """Devolve a resposta original da tentativa, ou None se ela é nova.

    `erro_para(chave)` monta a URL de recusa da rota; `destino_padrao` responde
    quando a tentativa concluída não registrou destino.

    AUD-01 (TASK_014): `operacao` e `autor_id` identificam o CONTEXTO da
    requisição. A tentativa registrada só é devolvida quando o contexto confere;
    caso contrário a rota recebe a recusa `tentativa_contexto` — sem destino,
    documento nem autoria do outro operador.
    """
    try:
        reconhecer(conn, idempotencia=idempotencia, corpo=corpo,
                   operacao=operacao, autor_id=autor_id)
    except TentativaRecusada as e:
        return RedirectResponse(url=erro_para(e.chave_erro), status_code=303)
    except TentativaJaRegistrada as e:
        return RedirectResponse(url=e.destino or destino_padrao, status_code=303)
    return None
