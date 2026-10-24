"""Serviço de precificação do PDV (TASK_006 — FIN-01).

O servidor é a única autoridade sobre preço, subtotal e total de uma venda.
O navegador informa produto e quantidade; valores de preço enviados pelo
cliente servem APENAS para detectar divergência de display — nunca para
gravar. Caixas sem catálogo (`usa_produtos = FALSE`, ex.: Bazar/Geral) têm
preço aberto por design: o valor do cliente é aceito como preço declarado,
validado como moeda, e a cesta é bloqueada contra produtos de catálogo.

Regras monetárias: Decimal do texto/NUMERIC (nunca float no caminho de
escrita), 2 casas, ROUND_HALF_UP por subtotal; total = soma dos subtotais.
Teto de campo: NUMERIC(10,2) → R$ 99.999.999,99.

Erros carregam chave estável + índice do item (1-based) para a tela apontar
o que revisar, sem traceback.
"""

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENTAVO = Decimal("0.01")
MAX_MOEDA = Decimal("99999999.99")   # NUMERIC(10,2)
MAX_QUANTIDADE = 1_000_000           # muito acima de qualquer uso real; protege INT/NUMERIC


class CestaInvalida(Exception):
    """Cesta rejeitada ANTES de qualquer escrita. `item` é 1-based."""

    def __init__(self, chave: str, item: int | None = None):
        super().__init__(chave)
        self.chave = chave
        self.item = item


class DivergenciaPreco(Exception):
    """O total que a tela exibiu difere do preço vigente no servidor."""

    def __init__(self, linhas: list, total: Decimal):
        super().__init__("preco_divergente")
        self.linhas = linhas
        self.total = total


def validar_moeda(texto, campo: str, minimo: Decimal = Decimal("0"),
                  item: int | None = None) -> Decimal:
    try:
        valor = Decimal(str(texto).replace(",", "."))
    except (InvalidOperation, AttributeError, ValueError):
        raise CestaInvalida(f"{campo}_invalido", item)
    if not valor.is_finite():
        raise CestaInvalida(f"{campo}_invalido", item)
    valor = valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    if valor < minimo or valor > MAX_MOEDA:
        raise CestaInvalida(f"{campo}_invalido", item)
    return valor


def _validar_quantidade(valor) -> int:
    # bool é subtipo de int em Python — recusar explicitamente.
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise CestaInvalida("quantidade_invalida")
    if valor < 1 or valor > MAX_QUANTIDADE:
        raise CestaInvalida("quantidade_invalida")
    return valor


def _preco_vigente(produto) -> Decimal:
    preco = produto["preco_venda"]
    if not isinstance(preco, Decimal):
        preco = Decimal(str(preco))
    if preco < 0 or not preco.is_finite() or preco > MAX_MOEDA:
        raise CestaInvalida("produto_preco_invalido")
    # Não existe produto gratuito no cadastro — preço zero é dado quebrado,
    # não permissão para vender de graça.
    if preco == 0:
        raise CestaInvalida("produto_preco_invalido")
    return preco


def parse_cesta(itens_json: str) -> list:
    """Validação estrutural do payload; nada do banco é tocado aqui."""
    try:
        bruto = json.loads(itens_json) if itens_json else None
    except (json.JSONDecodeError, TypeError):
        raise CestaInvalida("cesta_invalida")
    if not isinstance(bruto, list) or not bruto:
        raise CestaInvalida("cesta_vazia")

    itens = []
    for n, linha in enumerate(bruto, start=1):
        if not isinstance(linha, dict):
            raise CestaInvalida("cesta_invalida", n)
        pid = linha.get("produto_id")
        if pid is not None and (isinstance(pid, bool) or not isinstance(pid, int) or pid < 1):
            raise CestaInvalida("cesta_invalida", n)
        try:
            quantidade = _validar_quantidade(linha.get("quantidade", 1))
        except CestaInvalida:
            raise CestaInvalida("quantidade_invalida", n)
        itens.append({
            "produto_id": pid,
            "quantidade": quantidade,
            "nome_cliente": linha.get("nome"),
            "preco_cliente": linha.get("preco_unitario"),
            "subtotal_cliente": linha.get("subtotal"),
        })
    return itens


def _agregar(itens: list) -> list:
    """Normaliza produtos repetidos: soma a quantidade por produto, preserva
    a ordem de entrada e o item mais antigo para nomes/totais do cliente."""
    ordem: list = []
    vistos: dict = {}
    livres: list = []
    for it in itens:
        if it["produto_id"] is None:
            livres.append(it)
            continue
        if it["produto_id"] in vistos:
            vistos[it["produto_id"]]["quantidade"] += it["quantidade"]
            if vistos[it["produto_id"]]["quantidade"] > MAX_QUANTIDADE:
                raise CestaInvalida("quantidade_invalida")
        else:
            agregado = dict(it)
            vistos[it["produto_id"]] = agregado
            ordem.append(agregado)
    return ordem + livres


def precificar(conn, itens: list, usa_produtos: bool):
    """Blocos de preço do servidor. Retorna (linhas, total) ou lança.

    `linhas` é o snapshot autorizado para gravação: nome/preço do cadastro
    (catálogo) ou valor validado (caixa de preço aberto).
    """
    # Divergência de display usa os valores que a tela MOSTROU, por linha,
    # antes de qualquer agregação (linhas duplicadas contam cada uma).
    exibido = _total_exibido(itens)
    itens = _agregar(itens)

    produtos = {}
    ids_catalogo = sorted({i["produto_id"] for i in itens if i["produto_id"] is not None})
    if ids_catalogo and not usa_produtos:
        raise CestaInvalida("produto_fora_do_caixa")
    if ids_catalogo:
        placeholders = ", ".join(["%s"] * len(ids_catalogo))
        rows = conn.execute(
            f"""SELECT id, nome, preco_venda, quantidade_estoque, ativo
                FROM produtos WHERE id IN ({placeholders}) AND ativo = 1
                ORDER BY id FOR UPDATE""",
            ids_catalogo,
        ).fetchall()
        produtos = {r["id"]: dict(r) for r in rows}

    linhas = []
    total = Decimal("0.00")
    for n, it in enumerate(itens, start=1):
        if it["produto_id"] is None:
            if not usa_produtos:
                # preço aberto autorizado do caixa: valor declarado, validado
                valor = validar_moeda(it["preco_cliente"], "preco",
                                      minimo=CENTAVO, item=n)
                subtotal = (valor * it["quantidade"]).quantize(CENTAVO, ROUND_HALF_UP)
                if subtotal > MAX_MOEDA:
                    raise CestaInvalida("preco_estourado", n)
                nome = (str(it["nome_cliente"]).strip() if it["nome_cliente"] else "")[:200] or "Venda"
                linhas.append({"produto_id": None, "nome": nome,
                               "quantidade": it["quantidade"],
                               "preco_unitario": valor, "subtotal": subtotal})
                total += subtotal
            else:
                raise CestaInvalida("item_sem_produto", n)
            continue

        produto = produtos.get(it["produto_id"])
        if produto is None:
            raise CestaInvalida("produto_indisponivel", n)
        preco = _preco_vigente(produto)
        subtotal = (preco * it["quantidade"]).quantize(CENTAVO, ROUND_HALF_UP)
        if subtotal > MAX_MOEDA or total + subtotal > MAX_MOEDA:
            raise CestaInvalida("total_estourado", n)
        linhas.append({"produto_id": produto["id"], "nome": produto["nome"],
                       "quantidade": it["quantidade"], "preco_unitario": preco,
                       "subtotal": subtotal, "estoque": produto["quantidade_estoque"]})
        total += subtotal

    total = total.quantize(CENTAVO, ROUND_HALF_UP)

    if exibido is not None and exibido != total:
        raise DivergenciaPreco(linhas, total)
    return linhas, total


def _total_exibido(itens: list) -> Decimal | None:
    soma = Decimal("0.00")
    for it in itens:
        valor = it.get("subtotal_cliente")
        if valor is None:
            return None
        try:
            soma += Decimal(str(valor)).quantize(CENTAVO, ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None
        if soma > MAX_MOEDA * 10:
            return None
    return soma.quantize(CENTAVO, ROUND_HALF_UP)
