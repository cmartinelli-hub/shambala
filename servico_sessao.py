"""TASK_008 — Regra única da sessão de caixa: eventos, destino do recebimento
e fechamento com conferência de dinheiro físico.

Princípios (derivados da reprodução do FIN-04 em
tests/repro_task008_fin04.py):

  • Sessão nova (versão 'eventos') tem UMA fonte de verdade:
    `caixa_sessao_eventos`. Os acumuladores antigos (`total_vendas`,
    `total_sangrias`) NÃO são escritos para ela — ficam em 0 e são
    documentados como legados.
  • `efeito_fisico` é a contribuição assinada para a gaveta: venda em
    espécie (líquido do troco), quitação/mensalidade/doação/adiantamento
    recebidos EM ESPÉCIE nessa sessão somam; PIX, fiado não recebido e
    consumo de crédito anterior somam ZERO; sangria subtrai uma vez.
  • Identidade UNIQUE por evento: repetição do mesmo comando/documento é
    recusada PELO BANCO. A exceção precisa VAZAR do `with conectar()`
    para o rollback ser integral (capturar dentro do bloco faria COMMIT).
  • Sessões 'legado' continuam na regra antiga (acumuladores), nunca são
    recalculadas nem reparadas retroativamente; recebimentos novos nelas
    são explícitos (sem escolha implícita de caixa por ID fixo) e PIX novo
    não entra mais na gaveta contábil.
  • O fechamento 'eventos' usa exatamente a mesma soma do resumo e do
    comprovante: esperado = saldo inicial + Σ efeito_fisico. O dinheiro
    contado é registrado ao lado, com divergência e observação
    obrigatória quando diferente de zero — nunca sobrescreve o esperado.

Todo valor monetário é Decimal com duas casas (ROUND_HALF_UP); instantes
são gravados em America/Sao_Paulo com precisão de segundos.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from psycopg2.errors import UniqueViolation

from banco import conta_id_por_tipo

CENTAVO = Decimal("0.01")
VERSAO_EVENTOS = "eventos"
VERSAO_LEGADO = "legado"

# chave do advisory lock de abertura (parâmetro baixo; caixa_id no alto)
_ADVISORY_ABERTURA = 680000000


class SessaoInvalida(RuntimeError):
    """Destino inexistente/fechado/incompatível — recusa ANTES de qualquer
    efeito. `chave` vira mensagem estável na tela."""

    def __init__(self, chave: str, mensagem: str):
        super().__init__(mensagem)
        self.chave = chave
        self.mensagem = mensagem


class EventoDuplicado(RuntimeError):
    """O mesmo comando/documento já produziu este efeito (unicidade no banco)."""

    def __init__(self, identidade: str):
        super().__init__(f"Este comando já foi registrado (evento {identidade}).")
        self.identidade = identidade


# Chaves de recusa → mensagem curta e sem jargão, para as telas.
MENSAGENS_ERRO = {
    "sessao_necessaria": "O dinheiro recebido em espécie precisa entrar numa "
                         "sessão de caixa aberta — escolha a sessão.",
    "sessao_invalida": "A sessão escolhida não está aberta agora.",
    "sessao_ja_aberta": "Este caixa já tem uma sessão aberta.",
    "sessao_legada": "Esta sessão é do fluxo antigo e não fecha pela regra nova.",
    "sessao_legada_recebimento": "A sessão escolhida é do fluxo antigo e não "
                                 "aceita novos recebimentos — abra uma sessão "
                                 "nova e escolha-a como destino.",
    "conta_invalida": "A conta de destino não é válida para essa forma de pagamento.",
    "destino_obrigatorio": "Escolha a conta que recebe o dinheiro (destino).",
    "destino_invalido": "O destino precisa ser uma conta ativa e diferente da "
                        "gaveta de espécie.",
    "valor_invalido": "Valor inválido.",
    "valor_contado_invalido": "O dinheiro contado precisa ser um valor em reais, "
                              "zero ou positivo.",
    "observacao_obrigatoria": "A contagem difere do esperado: descreva a diferença "
                              "antes de fechar.",
    "recebido_insuficiente": "O valor entregue não cobre a venda mais a doação do troco.",
    "ja_pago": "Esta venda já foi quitada — nada foi lançado de novo.",
    "sem_divida": "Este trabalhador não tem dívidas em aberto.",
    "sem_itens": "Nenhuma pendência válida para receber.",
    "trabalhador_nao_encontrado": "Trabalhador não encontrado.",
    "venda_nao_encontrada": "Venda não encontrada.",
}

# Recusas de tentativa (TASK_012 §2) entram no mesmo vocabulário das telas.
from servico_idempotencia import MENSAGENS_TENTATIVA  # noqa: E402

MENSAGENS_ERRO.update(MENSAGENS_TENTATIVA)


TETO_MOEDA = Decimal("9999999999.99")  # teto do NUMERIC(12,2) usado no schema


def _arredondar_centavos(d: Decimal, campo: str) -> Decimal:
    try:
        return d.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # finito colossal (ex. 1e400): estoura o NUMERIC(12,2); o quantize do
        # Decimal lança InvalidOperation — o parser não pode vazar cru.
        raise SessaoInvalida("valor_invalido", f"Valor fora do limite em {campo}.")


def moeda(valor, campo: str) -> Decimal:
    """Parse defensivo para Decimal de 2 casas; teto NUMERIC(12,2)."""
    try:
        d = Decimal(str(valor).strip().replace(",", "."))
    except Exception:
        raise SessaoInvalida("valor_invalido", f"Valor inválido em {campo}.")
    if not d.is_finite():
        raise SessaoInvalida("valor_invalido", f"Valor inválido em {campo}.")
    d = _arredondar_centavos(d, campo)
    if d <= Decimal("0.00") or d > TETO_MOEDA:
        raise SessaoInvalida("valor_invalido", f"Valor fora do limite em {campo}.")
    return d


def moeda_nao_negativa(valor, campo: str) -> Decimal:
    """Saldo inicial e dinheiro contado: ZERO é legítimo (TASK_010/REV-02).

    Política monetária explícita, sem decisão por acidente:
      * texto inválido/NaN/infinito → recusado (mesma chave `valor_invalido`);
      * NEGATIVO é recusado ANTES do arredondamento — `-0,001` não se salva
        arredondando para zero;
      * duas casas com ROUND_HALF_UP, como no resto do caixa: `0,001` vira
        `0.00` e é ACEITO (contagem com fração de centavo);
      * o teto NUMERIC(12,2) é conferido DEPOIS do arredondamento: um valor
        enviado direto com precisão adicional que arredonda para além do teto
        (ex. `9999999999.995` → `10000000000.00`) é recusado estourando.

    Eventos de recebimento/saída continuam usando `moeda()` — estritamente
    positivos; esta regra não relaxa o evento zero.
    """
    try:
        d = Decimal(str(valor).strip().replace(",", "."))
    except Exception:
        raise SessaoInvalida("valor_invalido", f"Valor inválido em {campo}.")
    if not d.is_finite():
        raise SessaoInvalida("valor_invalido", f"Valor inválido em {campo}.")
    if d < Decimal("0"):
        raise SessaoInvalida("valor_invalido", f"Valor fora do limite em {campo}.")
    d = _arredondar_centavos(d, campo)
    if d > TETO_MOEDA:
        raise SessaoInvalida("valor_invalido", f"Valor fora do limite em {campo}.")
    return d


def eh_sessao_eventos(mov) -> bool:
    return bool(mov) and mov.get("versao") == VERSAO_EVENTOS


def contas_por_tipo(conn, tipo: str) -> list:
    return conn.execute(
        "SELECT id, nome, tipo FROM contas_financeiras WHERE tipo = %s AND ativo = 1 "
        "ORDER BY id", (tipo,)).fetchall()


# ── Contrato de conta × forma de pagamento (TASK_016) ───────────────────────
#
# FORMA DE PAGAMENTO e CONTA DE DESTINO são coisas distintas. A forma diz COMO o
# dinheiro chegou; a conta diz ONDE ele está. "PIX" é forma, não conta: o recurso
# de um PIX entra numa conta bancária (ou na conta técnica `pix`, que é legado e
# segue válida).
#
# A matriz abaixo é a fonte única. Ela responde, para cada forma, QUAIS tipos de
# conta podem receber, e qual exigência de sessão/efeito físico se aplica — sem
# que cada rota decida por conta própria.
#
# | forma   | tipos elegíveis              | sessão exigida | efeito na gaveta |
# |---------|------------------------------|----------------|------------------|
# | especie | especie                      | sim            | sim (dinheiro)   |
# | pix     | pix, banco, poupança         | não            | não (eletrônico) |
#
# Tipos que NÃO são destino de dinheiro em recebimento:
# - `areceber`: conta técnica de OBRIGAÇÃO (títulos a receber). Não é caixa; um
#   recebimento entra na conta real, e a baixa do título é outro fato.
# - `legado`: conta antiga de origem histórica. Continua recebendo apenas o que
#   já recebia (nada novo por aqui); não é oferecida como destino.
# - `outro`: tipo livre sem semântica de destino; não é oferecido.

# Forma → tipos de conta que podem receber.
CONTAS_ELEGIVEIS = {
    "especie": ("especie",),
    "pix": ("pix", "banco", "poupanca"),
}

# Tipos que representam dinheiro disponível (aparecem como "recursos").
# `areceber` fica de fora: é obrigação, não disponibilidade.
TIPOS_DE_RECURSO = ("especie", "pix", "banco", "poupanca", "legado")

# Rótulos para o operador (sem jargão de banco de dados).
ROTULOS_TIPO_CONTA = {
    "especie": "Espécie (dinheiro)",
    "pix": "PIX",
    "banco": "Conta bancária",
    "poupanca": "Poupança / Reserva",
    "legado": "Conta antiga (origem histórica)",
    "areceber": "Valores a receber (obrigações)",
    "outro": "Outro",
}


def conta_e_recurso(conta: dict | None) -> bool:
    """A conta guarda dinheiro disponível? (`areceber` não guarda.)"""
    return bool(conta) and conta.get("tipo") in TIPOS_DE_RECURSO


def _conta_por_id(conn, conta_id):
    return conn.execute(
        """SELECT id, nome, tipo, ativo FROM contas_financeiras
           WHERE id = %s""", (conta_id,)).fetchone()


def contas_de_destino(conn, forma: str) -> list:
    """Contas ATIVAS que podem receber esta forma, em ordem estável de nome.

    Nunca escolhe "a primeira por id": devolve a lista e deixa o chamador decidir
    (padrão quando há uma só, exigência de escolha quando há várias).
    """
    tipos = CONTAS_ELEGIVEIS.get(forma)
    if not tipos:
        return []
    return conn.execute(
        """SELECT id, nome, tipo FROM contas_financeiras
           WHERE ativo = 1 AND tipo = ANY(%s)
           ORDER BY nome""", (list(tipos),)).fetchall()


def conta_de_destino(conn, forma: str, conta_id=None, *, exigir_escolha=True):
    """Resolve a conta de destino de um recebimento, VALIDANDO no servidor.

    Regras (TASK_016 §3):
    - conta informada é conferida: precisa existir, estar ativa e ser de tipo
      elegível para a forma. Conta inexistente/inativa/incompatível é RECUSADA —
      nunca substituída em silêncio por outra;
    - sem conta informada: se houver exatamente UMA elegível, ela é o padrão
      (o chamador mostra o destino na tela); se houver mais de uma, é ambiguidade
      e a recusa pede a escolha;
    - `areceber`, `legado` e `outro` não são destino de dinheiro por esta porta.
    """
    if forma not in CONTAS_ELEGIVEIS:
        raise SessaoInvalida("forma_invalida",
                             "Forma de pagamento não suportada aqui.")
    elegiveis = contas_de_destino(conn, forma)
    if not elegiveis:
        raise SessaoInvalida("conta_invalida",
                             "Não há conta cadastrada que possa receber esta forma "
                             "de pagamento.")

    if conta_id in (None, 0, ""):
        if len(elegiveis) == 1 or not exigir_escolha:
            return elegiveis[0]["id"]
        raise SessaoInvalida(
            "conta_invalida",
            "Há mais de uma conta possível para este recebimento; escolha a "
            "conta de destino.")

    linha = _conta_por_id(conn, conta_id)
    if not linha:
        raise SessaoInvalida("conta_invalida",
                             "A conta informada não existe.")
    if not linha["ativo"]:
        raise SessaoInvalida("conta_inativa",
                             "A conta informada está inativa; escolha outra.")
    if linha["tipo"] not in CONTAS_ELEGIVEIS[forma]:
        raise SessaoInvalida(
            "conta_incompativel",
            "Esta conta não pode receber esta forma de pagamento. "
            "Dinheiro em espécie entra em conta de espécie; PIX entra em conta "
            "bancária, poupança ou na conta PIX.")
    return linha["id"]


def validar_conta_para_forma(conn, forma: str, conta_id: int | None):
    """A conta é conferida NO BANCO pelo tipo — nunca pelo que o navegador
    declarou.

    TASK_016: passou a delegar ao contrato único `conta_de_destino`. Antes só
    `especie` e `pix` eram aceitos e a decisão vivia nesta função; hoje a mesma
    matriz serve a todas as rotas, e vale para banco/poupança no PIX.
    """
    return conta_de_destino(conn, forma, conta_id)


def sessoes_abertas(conn) -> list:
    """Sessões abertas de qualquer caixa — para quem precisa ESCOLHER o
    destino explicitamente (nunca ID fixo presumido)."""
    return conn.execute(
        """SELECT m.id, m.versao, m.caixa_id, m.saldo_inicial, c.nome AS caixa_nome
           FROM caixa_movimentos m JOIN caixas c ON c.id = m.caixa_id
           WHERE m.status = 'aberto'
           ORDER BY c.nome, m.id""").fetchall()


def sessao_aberta_para_especie(conn, sessao_id: int, *, travar: bool = True):
    """A sessão que fisicamente guarda o dinheiro precisa ser aberta e
    indicada explicitamente. Sem ID fixo, sem primeira da lista, sem
    sessão de outro caixa presumida."""
    if not sessao_id:
        raise SessaoInvalida(
            "sessao_necessaria",
            "Recebimento em espécie exige uma sessão de caixa aberta "
            "explicitamente selecionada.")
    sql = ("SELECT m.*, c.nome AS caixa_nome FROM caixa_movimentos m "
           "JOIN caixas c ON c.id = m.caixa_id WHERE m.id = %s AND m.status = 'aberto'")
    if travar:
        sql += " FOR UPDATE"
    mov = conn.execute(sql, (sessao_id,)).fetchone()
    if not mov:
        raise SessaoInvalida(
            "sessao_invalida",
            "A sessão informada não está aberta neste momento (fechada, "
            "inexistente ou de outro uso).")
    return mov


def abrir_sessao(conn, caixa_id: int, saldo_inicial: Decimal, atendente_id: int) -> int:
    """Sessão NOVA na versão eventos: advisory lock por caixa + checagem +
    INSERT numa transação só — duas aberturas concorrentes do mesmo caixa
    não produzem duas sessões abertas."""
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_ADVISORY_ABERTURA, caixa_id))
    existente = conn.execute(
        """SELECT m.id, m.versao FROM caixa_movimentos m
           WHERE m.caixa_id = %s AND m.status = 'aberto'
           ORDER BY m.id DESC LIMIT 1""", (caixa_id,)).fetchone()
    if existente:
        detalhe = (" (legado, feche-a pela regra antiga antes de abrir uma nova.)"
                   if existente["versao"] != VERSAO_EVENTOS else ".")
        raise SessaoInvalida(
            "sessao_ja_aberta",
            f"Este caixa já tem uma sessão aberta{detalhe} Sessão #{existente['id']}.")
    cur = conn.execute(
        """INSERT INTO caixa_movimentos
           (caixa_id, saldo_inicial, status, atendente_id_abertura, versao)
           VALUES (%s, %s, 'aberto', %s, %s) RETURNING id""",
        (caixa_id, saldo_inicial, atendente_id, VERSAO_EVENTOS))
    return cur.fetchone()["id"]


def registrar_evento(conn, *, identidade: str, sessao_id: int, tipo: str,
                     natureza: str, origem_tipo: str, origem_id: int, forma: str,
                     sentido: str, valor: Decimal, efeito_fisico: Decimal,
                     conta_id: int | None = None, venda_id: int | None = None,
                     financeiro_id: int | None = None, atendente_id: int | None = None,
                     observacao: str | None = None):
    """Insere o evento detalhado. UNIQUE(identidade) é a idempotência real:
    repetir o mesmo comando/documento viola a unicidade e o exception
    vaza do `with conectar()` para DESVIAR tudo da transação."""
    conn.execute(
        """INSERT INTO caixa_sessao_eventos
           (identidade, sessao_id, tipo, natureza, origem_tipo, origem_id,
            venda_id, financeiro_id, forma, sentido, valor, efeito_fisico,
            conta_id, atendente_id, observacao)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (identidade, sessao_id, tipo, natureza, origem_tipo, origem_id,
         venda_id, financeiro_id, forma, sentido, valor, efeito_fisico,
         conta_id, atendente_id, observacao))


def contabilizar_recebimento(conn, *, tipo: str, valor: Decimal, categoria: str,
                             descricao: str, forma: str, conta_id: int,
                             sessao, identidade: str | None = None,
                             atendente_id: int | None = None,
                             venda_id: int | None = None):
    """UM lançamento financeiro + (sessão eventos → UM evento). PIX nunca
    entra na gaveta: efeito físico zero. Sessão legado jamais é alimentada —
    recusa antes de qualquer escrita. Retorna o id do lançamento."""
    if sessao is not None and sessao.get("versao") != VERSAO_EVENTOS:
        raise SessaoInvalida(
            "sessao_legada_recebimento",
            "A sessão escolhida é do fluxo antigo e não aceita novos "
            "recebimentos. Abra uma sessão nova (regra atual) e escolha-a "
            "como destino.")
    cur = conn.execute(
        """INSERT INTO financeiro_movimentacoes
           (tipo, categoria, valor, descricao, caixa_id, forma_pagamento, conta_id, status)
           VALUES ('entrada', %s, %s, %s, %s, %s, %s, 'pago') RETURNING id""",
        (categoria, valor, descricao,
         sessao["caixa_id"] if sessao else None, forma, conta_id))
    fin_id = cur.fetchone()["id"]
    if sessao is None:
        return fin_id
    registrar_evento(
        conn,
        identidade=identidade or f"{tipo}:{fin_id}",
        sessao_id=sessao["id"], tipo=tipo, natureza="recebimento",
        origem_tipo="financeiro_movimentacoes", origem_id=fin_id,
        venda_id=venda_id, financeiro_id=fin_id, forma=forma,
        sentido="entrada", valor=valor,
        efeito_fisico=valor if forma == "especie" else Decimal("0.00"),
        conta_id=conta_id, atendente_id=atendente_id, observacao=descricao)
    return fin_id


def sessao_para_recebimento(conn, sessao_id: int, forma: str):
    """Espécie SEMPRE exige sessão aberta (travada); PIX aceita sessão
    opcional apenas como vínculo de auditoria (efeito físico 0).

    TASK_008/revisão do dono: sessões LEGADO não aceitam novos recebimentos —
    nada de alimentar os acumuladores antigos por escolha explícita. A recusa
    acontece ANTES de qualquer escrita (baixa de pendência, lançamento, evento).
    Vendas e sangrias continuam na regra antiga para permitir concluir e fechar
    a gaveta legada."""
    if forma == "especie":
        mov = sessao_aberta_para_especie(conn, sessao_id)
    elif not sessao_id:
        return None
    else:
        mov = conn.execute(
            """SELECT * FROM caixa_movimentos
               WHERE id = %s AND status = 'aberto' FOR UPDATE""",
            (sessao_id,)).fetchone()
        if not mov:
            raise SessaoInvalida(
                "sessao_invalida", "A sessão informada não está aberta.")
    if mov.get("versao") != VERSAO_EVENTOS:
        raise SessaoInvalida(
            "sessao_legada_recebimento",
            "A sessão escolhida é do fluxo antigo e não aceita novos "
            "recebimentos. Abra uma sessão nova (regra atual) e escolha-a "
            "como destino.")
    return mov


def evento_financeiro_id(conn, identidade: str) -> int | None:
    row = conn.execute(
        "SELECT financeiro_id FROM caixa_sessao_eventos WHERE identidade = %s",
        (identidade,)).fetchone()
    return row["financeiro_id"] if row else None


def vincular_financeiro(conn, identidade: str, financeiro_id: int):
    conn.execute(
        "UPDATE caixa_sessao_eventos SET financeiro_id = %s WHERE identidade = %s",
        (financeiro_id, identidade))


def resumo_sessao(conn, sessao_id: int) -> dict:
    """A MESMA regra para tela, fechamento e impressão: vem dos eventos."""
    mov = conn.execute(
        """SELECT m.*, c.nome AS caixa_nome FROM caixa_movimentos m
           JOIN caixas c ON c.id = m.caixa_id WHERE m.id = %s""",
        (sessao_id,)).fetchone()
    if not mov:
        raise SessaoInvalida("sessao_invalida", "Sessão não encontrada.")
    linhas = conn.execute(
        """SELECT tipo, forma, sentido,
                  COUNT(*) AS qtd,
                  COALESCE(SUM(valor), 0)        AS soma_valor,
                  COALESCE(SUM(efeito_fisico), 0) AS soma_fisico
           FROM caixa_sessao_eventos
           WHERE sessao_id = %s
           GROUP BY tipo, forma, sentido""", (sessao_id,)).fetchall()

    def _d(v):
        return Decimal(str(v))

    acum: dict = {}
    soma_fisico = Decimal("0.00")
    for l in linhas:
        chave = (l["tipo"], l["forma"])
        atual = acum.get(chave, {"qtd": 0, "valor": Decimal("0.00")})
        atual["qtd"] += int(l["qtd"])
        atual["valor"] += _d(l["soma_valor"])
        acum[chave] = atual
        soma_fisico += _d(l["soma_fisico"])

    def _get(tipo, forma=None):
        if forma is not None:
            a = acum.get((tipo, forma))
            return a["valor"] if a else Decimal("0.00")
        return sum((a["valor"] for (t, f), a in acum.items() if t == tipo),
                   Decimal("0.00"))

    def _qtd(tipo):
        return sum(a["qtd"] for (t, f), a in acum.items() if t == tipo)

    vendas_brutas = _get("venda")
    cancelado = _get("cancelamento")
    esperado = _d(mov["saldo_inicial"]) + soma_fisico
    return {
        "movimento": mov,
        "sessao_id": sessao_id,
        "caixa_nome": mov["caixa_nome"],
        "versao": mov.get("versao"),
        "status": mov["status"],
        "saldo_inicial": _d(mov["saldo_inicial"]),
        "vendas_comerciais_brutas": vendas_brutas,
        "vendas_canceladas": cancelado,
        "vendas_especie": _get("venda", "especie"),
        "vendas_pix": _get("venda", "pix"),
        "vendas_fiado": _get("venda", "fiado"),
        "credito_consumido": _get("venda_credito"),
        "doacoes": _get("doacao") + _get("venda_doacao"),
        "mensalidades": _get("mensalidade"),
        "qtd_mensalidades": _qtd("mensalidade"),
        "recebimentos_fiado": _get("recebimento_fiado"),
        "creditos_anticipados": _get("credito_anticipado"),
        "sangrias": _get("sangria"),
        "efeito_fisico_total": soma_fisico,
        "saldo_fisico_esperado": esperado,
    }


def _criar_lancamento(conn, tipo: str, categoria: str, valor: Decimal,
                      descricao: str, caixa_id: int | None, forma: str | None,
                      conta_id: int | None, sangria_id: int | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO financeiro_movimentacoes
           (tipo, categoria, valor, descricao, caixa_id, forma_pagamento, conta_id,
            sangria_id, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pago') RETURNING id""",
        (tipo, categoria, valor, descricao, caixa_id, forma, conta_id, sangria_id))
    return cur.fetchone()["id"]


def fechar_sessao_eventos(conn, sessao_id: int, dinheiro_contado: Decimal,
                          observacao: str, atendente_id: int) -> dict:
    """Fechamento da versão eventos, em transação única com a sessão travada.

    - esperado = inicial + Σ efeito_fisico (a mesma fórmula do resumo);
    - lançado financeiramente AGORA, uma vez por evento de venda/doação
      ainda sem lançamento (gatilho de receita só para o que é venda/PIX;
      fiado não recebido e crédito consumido não viram dinheiro);
    - contado ≠ esperado fica registrado como divergência de conferência
      com observação obrigatória; o esperado NUNCA é sobrescrito.
    """
    mov = conn.execute(
        """SELECT * FROM caixa_movimentos
           WHERE id = %s AND status = 'aberto' FOR UPDATE""", (sessao_id,)).fetchone()
    if not mov:
        raise SessaoInvalida("sessao_invalida",
                             "Sessão não encontrada ou já fechada.")
    if mov["versao"] != VERSAO_EVENTOS:
        raise SessaoInvalida(
            "sessao_legada",
            "Esta sessão é do fluxo antigo (legado) e não fecha pela regra nova.")

    res = resumo_sessao(conn, sessao_id)
    esperado = res["saldo_fisico_esperado"]
    divergencia = (dinheiro_contado - esperado).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    if divergencia != Decimal("0.00") and not (observacao or "").strip():
        raise SessaoInvalida(
            "observacao_obrigatoria",
            "A contagem diverge do esperado: descreva a diferença na observação "
            "antes de fechar.")

    # receita realizada das vendas/doações da sessão — uma única vez por
    # origem; eventos já vinculados (ou de vendas canceladas) ficam de fora.
    pendentes = conn.execute(
        """SELECT e.id, e.identidade, e.venda_id, e.tipo, e.forma, e.valor
           FROM caixa_sessao_eventos e
           WHERE e.sessao_id = %s AND e.tipo IN ('venda','venda_doacao')
             AND e.financeiro_id IS NULL
             AND (e.venda_id IS NULL OR NOT EXISTS (
                   SELECT 1 FROM caixa_sessao_eventos c
                   WHERE c.tipo = 'cancelamento' AND c.venda_id = e.venda_id))
           ORDER BY e.id""", (sessao_id,)).fetchall()
    caixa_nome = res["caixa_nome"]
    for ev in pendentes:
        if ev["forma"] == "especie":
            conta = conta_id_por_tipo(conn, "especie")
        elif ev["forma"] == "pix":
            conta = conta_id_por_tipo(conn, "pix")
        else:
            continue  # fiado não recebido: sem dinheiro novo (a quitação lança depois)
        categoria = "venda_pdv" if ev["tipo"] == "venda" else "doacao"
        rotulo = "Venda" if ev["tipo"] == "venda" else "Doação da venda"
        ref = f" #{ev['venda_id']}" if ev["venda_id"] else ""
        mov_id = _criar_lancamento(
            conn, "entrada", categoria, ev["valor"],
            f"{rotulo}{ref} ({ev['forma']}) — fechamento #{sessao_id}",
            mov["caixa_id"], ev["forma"], conta)
        vincular_financeiro(conn, ev["identidade"], mov_id)

    conn.execute(
        """UPDATE caixa_movimentos
           SET status = 'fechado',
               data_fechamento = (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp::text,
               saldo_final = %s,
               dinheiro_contado = %s,
               divergencia_contagem = %s,
               observacao_conferencia = %s,
               atendente_id_fechamento = %s,
               observacao_fechamento = %s
           WHERE id = %s""",
        (esperado, dinheiro_contado, divergencia, (observacao or "").strip(),
         atendente_id, (observacao or "").strip(), sessao_id))
    res.update(saldo_final=esperado, dinheiro_contado=dinheiro_contado,
               divergencia=divergencia, lancados=len(pendentes))
    return res
