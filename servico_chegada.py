"""Fluxo único de chegada por código (TASK_003) — terminal QR e teclado.

QR e teclado chegam pelo mesmo caminho: valida-se o formato, resolve-se o código
com `servico_codigo_trabalhador.resolver_codigo()` e registra-se com
`servico_presenca.registrar_chegada()`. Nenhuma SQL de presença é repetida aqui
nem nas rotas — este módulo só coordena os serviços existentes.

Concorrência com a troca de código: o trabalhador é travado (`FOR UPDATE`, a
mesma linha que `trocar_codigo` trava) e o código é re-resolvido depois da trava
adquirida. Assim, um código retirado por uma troca confirmada enquanto esperávamos
a trava deixa de ser aceito — não há janela em que a carteirinha velha identifica
a pessoa e grava presença.

Tudo ocorre na transação do chamador: quem chama decide quando confirmar.
"""

from servico_codigo_trabalhador import (
    codigo_valido, resolver_codigo, travar_trabalhador,
)
from servico_presenca import (
    ORIGENS_CHEGADA, OrigemInvalida, TrabalhadorInativo, TrabalhadorInexistente,
    registrar_chegada,
)


# Cada estado do código vira a recusa que o terminal sabe apresentar. 'ativo' é
# o único estado que segue para o registro.
_RECUSAS = {
    "invalido": "invalido",
    "inexistente": "nao_encontrado",
    "retirado": "retirado",
    "inativo": "inativo",
}


def registrar_chegada_por_codigo(conn, codigo, origem: str) -> dict:
    """Registra a chegada do código no terminal, na transação do chamador.

    `origem` é 'terminal_qr' ou 'terminal_codigo'. O retorno é sempre um dicionário
    com `resultado`; só os resultados `registrada` e `ja_registrada` correspondem a
    uma presença gravada.
    """
    if origem not in ORIGENS_CHEGADA or not origem.startswith("terminal_"):
        raise OrigemInvalida(f"origem de terminal inesperada: {origem!r}")

    if not codigo_valido(codigo):
        return {"resultado": "invalido", "gravado": False}

    resolvido = resolver_codigo(conn, codigo)
    recusa = _RECUSAS.get(resolvido["estado"])
    if recusa:
        return {"resultado": recusa, "gravado": False}

    trabalhador_id = resolvido["trabalhador_id"]
    # Fecha a linha do trabalhador e re-resolve à luz do que estiver confirmado:
    # é depois daqui que se pode afirmar que o código ainda é o código atual.
    travar_trabalhador(conn, trabalhador_id)
    atual = resolver_codigo(conn, codigo)
    recusa = _RECUSAS.get(atual["estado"])
    if recusa:
        return {"resultado": recusa, "gravado": False}
    if atual["trabalhador_id"] != trabalhador_id:
        # O código voltou a valer, mas para outra pessoa: a carteirinha
        # apresentada não é mais a desta pessoa.
        return {"resultado": "retirado", "gravado": False}

    try:
        registro = registrar_chegada(conn, trabalhador_id, origem=origem)
    except (TrabalhadorInativo, TrabalhadorInexistente):
        # O cadastro mudou entre a resolução e o registro — nada foi gravado.
        return {"resultado": "inativo", "gravado": False}

    return {
        "resultado": "registrada" if registro["criado"] else "ja_registrada",
        "gravado": True,
        "trabalhador_id": registro["trabalhador_id"],
        "nome_completo": registro["nome_completo"],
        "data": registro["data"],
        "hora_chegada": registro["hora_chegada"],
        "origem_chegada": registro["origem_chegada"],
    }
