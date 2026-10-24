#!/usr/bin/env python3
"""Suíte TASK_006 — preços e totais calculados pelo SERVIDOR no PDV (FIN-01).

Cobre a matriz de validação da tarefa:

1.  cesta honesta (2×12,50) → total 25,00 persistido;
2.  preço adulterado → recusa, nada gravado;
3.  nome adulterado → snapshot do cadastro;
4.  payload sem campos de preço → valores do servidor;
5.  preço mudou depois da cesta → revisão reprificada, sem escrita;
6.  concorrência (6 vendas simultâneas) → saldo determinístico;
7.  produto inexistente / inativo → recusado;
8.  produto com preço 0 ou negativo → recusado;
9.  quantidades inválidas e teto de quantidade;
10. agregação de itens repetidos do mesmo produto;
11. teto NUMERIC(10,2) → total_estourado;
12. centavos com ROUND_HALF_UP;
13. falha na última escrita → rollback integral;
14. espécie / PIX / fiado com o mesmo cálculo;
15. comprovante = registro (mesmo id do redirect);
16. anônimo, sem permissão e sessão de terminal barrados;
17. caixas de preço aberto (usa_produtos = FALSE) preservados;
18. limite e crédito de fiado pelo valor do servidor.

Roda contra rotas HTTP reais e contra o serviço de precificação. Toda limpeza é
selecionada por PREFIXO próprio; nada toca dados de outros testes.

Uso (recomendado): bash tests/rodar_testes.sh
Uso direto (app de teste já no ar, banco descartável):
    SHAMBALA_ENV_FILE=$PWD/.env.test SHAMBALA_MODO_TESTE=1 \\
    SHAMBALA_TEST_BASE=http://127.0.0.1:9011 SHAMBALA_TEST_DB=shambala_test_passes \\
    PYTHONPATH=$PWD .venv/bin/python3 tests/test_pdv_precos_servidor.py
"""
import http.cookiejar
import json
import os
import re
import sys
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import exigir_arquivo_de_configuracao  # noqa: E402

import test_tres_passes as tp  # noqa: E402
from test_tres_passes import (  # noqa: E402
    assert_eq, preparar_banco, limpar_banco, _garantir_admin,
    _login, BASE,
)

from banco import conectar  # noqa: E402
import servico_pdv  # noqa: E402
from servico_pdv import (  # noqa: E402
    MAX_MOEDA, MAX_QUANTIDADE, CestaInvalida, DivergenciaPreco,
    parse_cesta, precificar, validar_moeda,
)

PREFIXO = f"T6PDV-{uuid.uuid4().hex[:8]}"
ADMIN_ID = None
RESULTADOS = []


def teste(nome):
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


# ── HTTP ──────────────────────────────────────────────────────────────────────

class _SemRedirect(urllib.request.HTTPRedirectHandler):
    """Devolve None: o 3xx chega como HTTPError, com Location e cookies já lidos."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _jar(novo=False):
    """Opener com cookie jar; `novo=True` ignora o login da suíte."""
    if novo:
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            _SemRedirect())
    return tp._sessao()


def _req(opener, method, path, data=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(BASE + path, data=body, method=method)
    try:
        with opener.open(req) as r:
            return r.status, r.read().decode("utf-8", "replace"), \
                r.headers.get("location") or ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), \
            e.headers.get("location") or ""


class Resposta:
    """303 do PDV: destino = /caixa/pdv?caixa_id=N&erro=<chave>[&item=M]."""

    def __init__(self, status, corpo, destino):
        self.status = status
        self.corpo = corpo
        self.destino = destino
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(destino).query) if destino else {}
        self.erro = (qs.get("erro") or [None])[0]
        self.item = (qs.get("item") or [None])[0]
        self.ok = (qs.get("ok") or [None])[0]

    @property
    def redirecionada(self):
        return self.status == 303


def _finalizar(**kwargs):
    """POST na rota real, com o MESMO contrato de formulário do navegador."""
    opener = _jar(True) if kwargs.pop("novo", False) else _jar()
    itens = kwargs.pop("itens")
    dados = {
        "caixa_id": kwargs.pop("caixa_id"),
        "caixa_movimento_id": kwargs.pop("caixa_movimento_id", 0),
        "forma_pagamento": kwargs.pop("forma_pagamento", "especie"),
        "valor_recebido": kwargs.pop("valor_recebido", "0"),
        "itens_json": itens if isinstance(itens, str) else json.dumps(itens),
        "trabalhador_id": kwargs.pop("trabalhador_id", 0),
        # imprimir=1: com "0" a rota omite ok= no redirect (comportamento real
        # da tela); os testes precisam do id persistido no destino.
        "imprimir": kwargs.pop("imprimir", "1"),
        "doacao_valor": kwargs.pop("doacao_valor", "0"),
    }
    if kwargs:
        raise AssertionError(f"parâmetros não reconhecidos pelo contrato do PDV: {sorted(kwargs)}")
    status, corpo, destino = _req(opener, "POST", "/caixa/pdv/finalizar", dados)
    return Resposta(status, corpo, destino)


def _get(path, novo=False):
    opener = _jar(novo)
    status, corpo, destino = _req(opener, "GET", path)
    return Resposta(status, corpo, destino)


# ── Fixture ───────────────────────────────────────────────────────────────────

class PDV:
    """Caixas padrão + catálogo + sessão aberta, tudo prefixado."""

    def __init__(self):
        with conectar() as conn:
            def caixa(nome, usa_produtos):
                cur = conn.execute(
                    "INSERT INTO caixas (nome, descricao, usa_produtos, ativo) "
                    "VALUES (%s, %s, %s, 1) RETURNING id",
                    (f"{PREFIXO} {nome}", "fixture da suíte", usa_produtos))
                return cur.fetchone()["id"]

            self.catalogo = caixa("Lanchonete teste", True)
            self.livre = caixa("Bazar teste", False)
            self.inativo = caixa("Fechado teste", True)
            conn.execute("UPDATE caixas SET ativo = 0 WHERE id = %s", (self.inativo,))

            self.suco = self._produto(conn, self.catalogo, "Suco", "12.50", 10)
            self.agua = self._produto(conn, self.catalogo, "Agua", "3.00", 10)
            self.gratis = self._produto(conn, self.catalogo, "Brinde", "0.00", 5)
            self.negativo = self._produto(conn, self.catalogo, "Negativo", "-1.00", 5)
            self.sumido = self._produto(conn, self.catalogo, "Sumido", "5.00", 5, ativo=0)
            self.caro = self._produto(conn, self.catalogo, "Caro", "999999.99", 100)

            cur = conn.execute(
                "INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status) "
                "VALUES (%s, 0, 'aberto') RETURNING id", (self.catalogo,))
            self.sessao = cur.fetchone()["id"]
            cur = conn.execute(
                "INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status) "
                "VALUES (%s, 0, 'fechado') RETURNING id", (self.catalogo,))
            self.sessao_fechada = cur.fetchone()["id"]

            cur = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, fiado_credito) "
                "VALUES (%s, 1, 0, 0) RETURNING id", (f"{PREFIXO} sem limite",))
            self.trab_sem_limite = cur.fetchone()["id"]
            cur = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, fiado_credito) "
                "VALUES (%s, 1, 20, 5) RETURNING id", (f"{PREFIXO} fiado",))
            self.trab_fiado = cur.fetchone()["id"]
            cur = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo, fiado_limite, fiado_credito, "
                "fiado_data_encerramento) VALUES (%s, 1, 0, 0, '2000-01-01') RETURNING id",
                (f"{PREFIXO} encerrado",))
            self.trab_encerrado = cur.fetchone()["id"]

    def _produto(self, conn, caixa_id, nome, preco, estoque, ativo=1):
        cur = conn.execute(
            "INSERT INTO produtos (nome, categoria, preco_venda, quantidade_estoque, "
            "ativo, caixa_id) VALUES (%s, 'suíte', %s, %s, %s, %s) RETURNING id",
            (f"{PREFIXO} {nome}", preco, estoque, ativo, caixa_id))
        return cur.fetchone()["id"]

    def novo_produto(self, preco, estoque=1000, nome="Produto"):
        with conectar() as conn:
            return self._produto(conn, self.catalogo, nome, preco, estoque)

    def nova_sessao(self):
        with conectar() as conn:
            cur = conn.execute(
                "INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status) "
                "VALUES (%s, 0, 'aberto') RETURNING id", (self.catalogo,))
            return cur.fetchone()["id"]

    def produto(self, pid):
        with conectar() as conn:
            return dict(conn.execute("SELECT * FROM produtos WHERE id = %s", (pid,)).fetchone())

    def preco(self, pid, valor):
        with conectar() as conn:
            conn.execute("UPDATE produtos SET preco_venda = %s WHERE id = %s", (valor, pid))

    def estoque(self, pid):
        with conectar() as conn:
            return conn.execute(
                "SELECT quantidade_estoque AS e FROM produtos WHERE id = %s",
                (pid,)).fetchone()["e"]

    def movimentos(self, pid):
        with conectar() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM estoque_movimentos WHERE produto_id = %s ORDER BY id", (pid,))]

    def venda_de(self, venda_id):
        with conectar() as conn:
            v = conn.execute("SELECT * FROM vendas_pdv WHERE id = %s", (venda_id,)).fetchone()
            if not v:
                return None
            v = dict(v)
            v["itens"] = [dict(i) for i in conn.execute(
                "SELECT * FROM vendas_pdv_itens WHERE venda_id = %s ORDER BY id", (venda_id,))]
            return v

    def vendas(self, caixa_id=None):
        com = "WHERE caixa_id = %s" if caixa_id else ""
        args = (caixa_id,) if caixa_id else ()
        with conectar() as conn:
            return [dict(r) for r in conn.execute(
                f"SELECT * FROM vendas_pdv {com} ORDER BY id", args)]

    def n_vendas(self):
        """Vendas registradas pelos caixas da fixture (ignora resto da base)."""
        with conectar() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM vendas_pdv WHERE caixa_id IN "
                "(SELECT id FROM caixas WHERE nome LIKE %s)", (f"{PREFIXO}%",)
            ).fetchone()["c"]

    def sessao_de(self, mov_id):
        with conectar() as conn:
            return dict(conn.execute(
                "SELECT * FROM caixa_movimentos WHERE id = %s", (mov_id,)).fetchone())

    def trabalhador(self, tid):
        with conectar() as conn:
            return dict(conn.execute(
                "SELECT * FROM trabalhadores WHERE id = %s", (tid,)).fetchone())

    def creditos(self, tid):
        with conectar() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM fiado_credito_mov WHERE trabalhador_id = %s ORDER BY id", (tid,))]

    def encerrar_fiado(self, tid, data="2000-01-01"):
        with conectar() as conn:
            conn.execute("UPDATE trabalhadores SET fiado_data_encerramento = %s WHERE id = %s",
                         (data, tid))



def _item(pid, qtd=1, preco=None, subtotal=None, nome=None):
    it = {"produto_id": pid, "quantidade": qtd,
          "nome": nome if nome is not None else f"{PREFIXO} produto"}
    if preco is not None:
        it["preco_unitario"] = preco
    if subtotal is not None:
        it["subtotal"] = subtotal
    return it


def _linha(pid, qtd, preco):
    """Item no formato que a tela envia: preço e subtotal batendo com o cadastro."""
    subtotal = (Decimal(str(preco)) * qtd).quantize(Decimal("0.01"))
    return _item(pid, qtd, float(preco), float(subtotal))


PDV_FIXTURE = None


def pdv():
    return PDV_FIXTURE


def _dec(valor):
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


# ── 1. Serviço de precificação (unidades) ─────────────────────────────────────

@teste("S1 cesta honesta: servidor recalcula e bate com o cadastro")
def s1():
    p = pdv()
    with conectar() as conn:
        linhas, total = precificar(conn, parse_cesta(json.dumps(
            [_linha(p.suco, 2, "12.50")])), True)
    assert_eq(str(total), "25.00", "S1 total")
    assert_eq(linhas[0]["nome"], f"{PREFIXO} Suco", "S1 nome do cadastro")
    assert_eq(str(_dec(linhas[0]["preco_unitario"])), "12.50", "S1 preço")
    assert_eq(str(_dec(linhas[0]["subtotal"])), "25.00", "S1 subtotal")


@teste("S2 preço adulterado: DivergenciaPreco com total do servidor")
def s2():
    p = pdv()
    with conectar() as conn:
        try:
            precificar(conn, parse_cesta(json.dumps(
                [_item(p.suco, 2, 0.01, 0.02, "SUFRAMA ADULTERADA")])), True)
            raise AssertionError("S2 aceitei preço adulterado")
        except DivergenciaPreco as e:
            assert_eq(str(e.total), "25.00", "S2 total reprificado")
            assert_eq(e.linhas[0]["nome"], f"{PREFIXO} Suco", "S2 snapshot")


@teste("S3 nome mentiroso: snapshot usa o nome do cadastro")
def s3():
    p = pdv()
    with conectar() as conn:
        linhas, _ = precificar(conn, parse_cesta(json.dumps(
            [_item(p.suco, 2, 12.50, 25.00, "MENTIRA ENORME")])), True)
    assert_eq(linhas[0]["nome"], f"{PREFIXO} Suco", "S3 nome gravado")


@teste("S4 payload sem campos de preço: servidor é a única fonte")
def s4():
    p = pdv()
    with conectar() as conn:
        linhas, total = precificar(conn, parse_cesta(
            json.dumps([{"produto_id": p.suco, "quantidade": 2}])), True)
    assert_eq(str(total), "25.00", "S4 total sem valores do cliente")
    assert_eq(linhas[0]["nome"], f"{PREFIXO} Suco", "S4 nome")


@teste("S5 duplicatas agregadas: uma linha, quantidade somada")
def s5():
    p = pdv()
    with conectar() as conn:
        linhas, total = precificar(conn, parse_cesta(json.dumps(
            [_linha(p.agua, 1, "3.00"), _linha(p.agua, 2, "3.00")])), True)
    assert_eq(len(linhas), 1, "S5 linhas agregadas")
    assert_eq(linhas[0]["quantidade"], 3, "S5 quantidade")
    assert_eq(str(total), "9.00", "S5 total")


@teste("S6 exibido x servidor: soma linha a linha antes da agregação")
def s6():
    p = pdv()
    with conectar() as conn:
        # 1× e 2× do mesmo produto exibindo 3,00 no total → servidor diz 9,00.
        try:
            precificar(conn, parse_cesta(json.dumps(
                [_item(p.agua, 1, 3.00, 3.00), _item(p.agua, 2, 3.00, 0.00)])), True)
            raise AssertionError("S6 não detectou divergência")
        except DivergenciaPreco as e:
            assert_eq(str(e.total), "9.00", "S6 total do servidor")
            assert_eq(len(e.linhas), 1, "S6 linhas agregadas para revisão")


@teste("S7 produto inexistente ou inativo: recusado")
def s7():
    p = pdv()
    with conectar() as conn:
        for pid, esperado in ((p.sumido, "produto_indisponivel"), (999999, "produto_indisponivel")):
            try:
                precificar(conn, parse_cesta(json.dumps([_item(pid, 1)])), True)
                raise AssertionError(f"S7 aceitei produto {pid}")
            except CestaInvalida as e:
                assert_eq(e.chave, esperado, f"S7 {pid}")
                assert_eq(e.item, 1, "S7 índice do item")


@teste("S8 cadastro com preço zero ou negativo: recusado")
def s8():
    p = pdv()
    with conectar() as conn:
        for pid in (p.gratis, p.negativo):
            try:
                precificar(conn, parse_cesta(json.dumps([_item(pid, 1)])), True)
                raise AssertionError(f"S8 aceitei preço inválido de {pid}")
            except CestaInvalida as e:
                assert_eq(e.chave, "produto_preco_invalido", f"S8 {pid}")


@teste("S9 quantidades inválidas e teto de quantidade")
def s9():
    p = pdv()
    recusos = [0, -1, 1.5, "2", True, False, None, MAX_QUANTIDADE + 1]
    with conectar() as conn:
        for q in recusos:
            try:
                parse_cesta(json.dumps([{"produto_id": p.suco, "quantidade": q}]))
                raise AssertionError(f"S9 aceitei quantidade {q!r}")
            except CestaInvalida as e:
                assert_eq(e.chave, "quantidade_invalida", f"S9 {q!r}")
        try:
            precificar(conn, parse_cesta(json.dumps(
                [{"produto_id": p.caro, "quantidade": 1_000}])), True)
            raise AssertionError("S9 aceitei total estourado")
        except CestaInvalida as e:
            assert_eq(e.chave, "total_estourado", "S9 teto de valor")


@teste("S10 teto NUMERIC(10,2): total acima de R$ 99.999.999,99 é recusado")
def s10():
    p = pdv()
    with conectar() as conn:
        try:
            precificar(conn, parse_cesta(json.dumps([
                {"produto_id": p.caro, "quantidade": MAX_QUANTIDADE,
                 "preco_unitario": 999999.99, "subtotal": 1000000.0 * 999999.99}])), True)
            raise AssertionError("S10 aceitei estouro")
        except CestaInvalida as e:
            assert_eq(e.chave, "total_estourado", "S10")
            assert_eq(str(MAX_MOEDA), "99999999.99", "S10 teto")


@teste("S11 centavos: Decimal com ROUND_HALF_UP, duas casas")
def s11():
    p = pdv()
    assert_eq(str(validar_moeda("0.005", "preco", servico_pdv.CENTAVO)), "0.01", "S11 half-up")
    assert_eq(str(validar_moeda("12,345", "preco", servico_pdv.CENTAVO)), "12.35", "S11 vírgula")
    assert_eq(str(validar_moeda("12.3456", "preco", servico_pdv.CENTAVO)), "12.35", "S11")
    precinho = p.novo_produto("3.335", 1000, nome="Meio centavo")
    with conectar() as conn:
        linhas, total = precificar(conn, parse_cesta(
            json.dumps([{"produto_id": precinho, "quantidade": 3}])), True)
    assert_eq(str(linhas[0]["preco_unitario"]), "3.34", "S11 preço vigente em centavos")
    assert_eq(str(total), "10.02", "S11 total em centavos")
    assert_eq(str(validar_moeda(MAX_MOEDA, "preco")), "99999999.99", "S11 teto ok")
    for ruim in ("abc", "", "NaN", "Infinity", None, {}):
        try:
            validar_moeda(ruim, "valor_recebido")
            raise AssertionError(f"S11 aceitei moeda {ruim!r}")
        except CestaInvalida as e:
            assert_eq(e.chave, "valor_recebido_invalido", f"S11 {ruim!r}")
    try:
        validar_moeda("100000000", "preco")
        raise AssertionError("S11 aceitei valor acima do teto")
    except CestaInvalida as e:
        assert_eq(e.chave, "preco_invalido", "S11 acima do teto")


@teste("S12 caixa de preço aberto aceita valor declarado; catálogo é barrado")
def s12():
    p = pdv()
    livre = [{"produto_id": None, "nome": "Camisola usada", "quantidade": 2,
              "preco_unitario": 15.50, "subtotal": 31.00}]
    with conectar() as conn:
        linhas, total = precificar(conn, parse_cesta(json.dumps(livre)), False)
    assert_eq(str(total), "31.00", "S12 total aberto")
    assert_eq(linhas[0]["nome"], "Camisola usada", "S12 descrição declarada")
    with conectar() as conn:
        try:
            precificar(conn, parse_cesta(json.dumps([
                {"produto_id": None, "nome": "x", "quantidade": 1,
                 "preco_unitario": 0, "subtotal": 0}])), False)
            raise AssertionError("S12 aceitei valor zero no caixa aberto")
        except CestaInvalida as e:
            assert_eq(e.chave, "preco_invalido", "S12 valor mínimo")
        try:
            precificar(conn, parse_cesta(json.dumps([
                {"produto_id": None, "nome": "x", "quantidade": 1,
                 "preco_unitario": 20, "subtotal": 20},
                {"produto_id": p.suco, "quantidade": 1,
                 "preco_unitario": 12.50, "subtotal": 12.50}])), False)
            raise AssertionError("S12 misturei catálogo em caixa aberto")
        except CestaInvalida as e:
            assert_eq(e.chave, "produto_fora_do_caixa", "S12")


@teste("S13 cesta vazia ou malformed: recusada antes de tocar o banco")
def s13():
    recusos = ["[]", "", "nao é json", "null", '{"a":1}', "[[]]"]
    for payload in recusos:
        try:
            parse_cesta(payload)
            raise AssertionError(f"S13 aceitei {payload!r}")
        except CestaInvalida as e:
            assert e.chave in ("cesta_vazia", "cesta_invalida"), f"S13 {payload!r} → {e.chave}"
    # linha sem produto e sem valor: o parse aceita (caixa aberto possível), a
    # precificação recusa — nenhum valor do cliente existe para somar.
    with conectar() as conn:
        try:
            precificar(conn, parse_cesta('[{"quantidade": 1}]'), False)
            raise AssertionError("S13 aceitei linha aberta sem valor")
        except CestaInvalida as e:
            assert_eq(e.chave, "preco_invalido", "S13 linha aberta sem valor")
    with conectar() as conn:
        assert_eq(conn.execute("SELECT COUNT(*) AS c FROM vendas_pdv WHERE caixa_id IN "
                               "(SELECT id FROM caixas WHERE nome LIKE %s)",
                               (f"{PREFIXO}%",)).fetchone()["c"], 0, "S13 nada gravado")


class _ConexaoSobMedida:
    """Contexto como banco.conectar, com um gancho em cada execute.

    `interceptar(texto)` pode lançar; a exceção propaga para a rota e o próprio
    banco faz rollback — é assim que a suíte prova a transação única.
    """

    def __init__(self, interceptar):
        self._interceptar = interceptar
        self._contexto = None

    def __enter__(self):
        self._contexto = conectar()
        conn = self._contexto.__enter__()
        execute_original = conn.execute

        def execute(sql, *a, **k):
            texto = " ".join(str(sql).split())
            if self._interceptar(texto):
                raise RuntimeError("falha injetada pela suíte TASK_006")
            return execute_original(sql, *a, **k)

        conn.execute = execute
        return conn

    def __exit__(self, *exc):
        return self._contexto.__exit__(*exc)


class _ConexoesInjetadas:
    """Aplica o gancho apenas nas `a_partir`-ésima conexão (a venda em teste).

    A mesma requisição pode abrir conexões antes (ex.: telas de erro); assim a
    injeção nunca derruba um acesso innocente que enche o pool de fila.
    """

    def __init__(self, interceptar, a_partir=1):
        self._interceptar = interceptar
        self._restantes = a_partir

    def __call__(self):
        if self._restantes > 0:
            self._restantes -= 1
            return _ConexaoSobMedida(self._interceptar)
        return conectar()


def _injetar_falha_na_escrita(prefixo_sql, a_partir=1):
    alvo = {"feito": False}

    def interceptar(texto):
        if not alvo["feito"] and texto.startswith(prefixo_sql):
            alvo["feito"] = True
            return True
        return False

    return _ConexoesInjetadas(interceptar, a_partir)


def _rodar_rota_sem_http(p, itens, sessao=None, valor_recebido="50"):
    """Chama o corpo real da rota neste processo (para injetar falha de escrita).

    A injeção precisa acontecer no processo que executa a transação; o resto da
    suíte valida pelas rotas HTTP reais. Padrão já usado em
    test_financeiro_integracao: _guard adulterado, request None (o caminho de
    sucesso não toca request), asyncio.run para o create_task final.
    """
    import asyncio
    import rotas.caixa as rc
    original = rc._guard
    rc._guard = lambda request: ({"id": ADMIN_ID}, None)
    try:
        return asyncio.run(rc.finalizar_venda(
            None, caixa_id=p.catalogo, caixa_movimento_id=sessao or 0,
            forma_pagamento="especie", valor_recebido=valor_recebido,
            itens_json=json.dumps(itens), trabalhador_id=0, imprimir="0",
            doacao_valor="0"))
    finally:
        rc._guard = original


@teste("S14 falha na última escrita: venda, sessão e estoque somem juntos")
def s14():
    p = pdv()
    antes_vendas, antes_estoque = p.n_vendas(), p.estoque(p.suco)
    antes_sessao = _dec(p.sessao_de(p.sessao)["total_vendas"])
    import rotas.caixa as rc
    original = rc.conectar
    rc.conectar = _injetar_falha_na_escrita("INSERT INTO estoque_movimentos")
    try:
        try:
            _rodar_rota_sem_http(p, [_linha(p.suco, 2, "12.50")], sessao=p.sessao)
            falhou = False
        except RuntimeError:
            falhou = True
    finally:
        rc.conectar = original
    assert falhou, "S14 a falha injetada deveria interromper a venda"
    assert_eq(p.n_vendas(), antes_vendas, "S14 venda rollback")
    assert_eq(p.estoque(p.suco), antes_estoque, "S14 estoque rollback")
    assert_eq(len(p.movimentos(p.suco)), 0, "S14 movimento rollback")
    assert_eq(_dec(p.sessao_de(p.sessao)["total_vendas"]), antes_sessao, "S14 sessão rollback")


# ── 2. Rotas reais autenticadas ───────────────────────────────────────────────

@teste("R1 venda honesta 2×12,50: total persistido 25,00")
def r1():
    p = pdv()
    r = _finalizar(caixa_id=p.catalogo, caixa_movimento_id=p.sessao,
                   itens=[_linha(p.suco, 2, "12.50")], valor_recebido="50.00")
    assert r.redirecionada and r.ok, f"R1 finalizei? {r.status} {r.destino}"
    venda = p.venda_de(int(r.ok))
    assert_eq(str(_dec(venda["total"])), "25.00", "R1 total")
    assert_eq(str(_dec(venda["troco"])), "25.00", "R1 troco")
    assert_eq(len(venda["itens"]), 1, "R1 item")
    assert_eq(str(_dec(venda["itens"][0]["preco_unitario"])), "12.50", "R1 preço")
    assert_eq(str(_dec(venda["itens"][0]["subtotal"])), "25.00", "R1 subtotal")
    assert_eq(venda["itens"][0]["nome_produto"], f"{PREFIXO} Suco", "R1 snapshot do nome")
    assert_eq(p.estoque(p.suco), 8, "R1 estoque baixado")
    assert_eq(_dec(p.sessao_de(p.sessao)["total_vendas"]), Decimal("25.00"), "R1 sessão")


@teste("R2 FIN-01: preço adulterado não baixa e não cobra")
def r2():
    p = pdv()
    antes, estoque = p.n_vendas(), p.estoque(p.suco)
    r = _finalizar(caixa_id=p.catalogo, caixa_movimento_id=p.sessao,
                   itens=[_item(p.suco, 2, 0.01, 0.02, "SUFRAMA ADULTERADA")],
                   valor_recebido="50.00")
    assert_eq(r.status, 200, f"R2 tela de revisão esperada, veio {r.status} {r.destino}")
    assert_eq(p.n_vendas(), antes, "R2 nenhuma venda gravada")
    assert_eq(p.estoque(p.suco), estoque, "R2 estoque intacto")
    assert "algum produto mudou" in r.corpo, "R2 aviso de revisão visível"
    seed = re.search(r'id="pdv-cesta-seed" type="application/json">(.*?)</script>', r.corpo, re.S)
    assert seed, "R2 cesta reprificada embutida"
    assert_eq(json.loads(seed.group(1))[0]["preco_unitario"], 12.50, "R2 preço reprificado")


@teste("R3 preço mudou depois da cesta: revisão sem escrita")
def r3():
    p = pdv()
    prod = p.novo_produto("10.00", 50, nome="Mudou")
    r = _finalizar(caixa_id=p.catalogo, itens=[{"produto_id": prod, "quantidade": 2}],
                   valor_recebido="20.00")
    assert r.ok, f"R3 primeira venda ok {r.status}"
    p.preco(prod, "12.00")
    antes = p.n_vendas()
    r = _finalizar(caixa_id=p.catalogo, itens=[_linha(prod, 2, "10.00")])
    assert_eq(r.status, 200, "R3 cesta velha vai para revisão")
    assert_eq(p.n_vendas(), antes, "R3 nada gravado na revisão")
    seed = re.search(r'id="pdv-cesta-seed" type="application/json">(.*?)</script>', r.corpo, re.S)
    assert seed, "R3 cesta reprificada embutida"
    reprificada = json.loads(seed.group(1))[0]
    assert_eq(reprificada["preco_unitario"], 12.00, "R3 preço vigente na revisão")
    assert_eq(reprificada["subtotal"], 24.00, "R3 subtotal reprificado")


@teste("R4 valores enviados são ignorados: só produto e quantidade importam")
def r4():
    p = pdv()
    r = _finalizar(caixa_id=p.catalogo, itens=[{"produto_id": p.suco, "quantidade": 3}])
    assert r.ok, f"R4 {r.status} {r.destino}"
    assert_eq(str(_dec(p.venda_de(int(r.ok))["total"])), "37.50", "R4 total do servidor")
    assert_eq(p.estoque(p.suco), 10 - 2 - 3, "R4 estoque coerente com R1")


@teste("R5 duplicados na cesta: um item, baixa única")
def r5():
    p = pdv()
    r = _finalizar(caixa_id=p.catalogo, itens=[_linha(p.agua, 1, "3.00"), _linha(p.agua, 2, "3.00")])
    assert r.ok, f"R5 {r.status}"
    venda = p.venda_de(int(r.ok))
    assert_eq(len(venda["itens"]), 1, "R5 item agregado")
    assert_eq(str(_dec(venda["total"])), "9.00", "R5 total")
    movs = p.movimentos(p.agua)
    assert_eq(len(movs), 1, "R5 um movimento")
    assert_eq(movs[0]["quantidade"], 3, "R5 quantidade baixada")
    assert_eq((movs[0]["saldo_anterior"], movs[0]["saldo_posterior"]), (10, 7), "R5 saldo explícito")


@teste("R6 validações da rota: recusas com chave e item, sem escrita")
def r6():
    p = pdv()
    antes = p.n_vendas()
    casos = [
        ({"caixa_id": p.inativo, "itens": [_item(p.suco, 1)]}, "caixa_indisponivel", None),
        ({"caixa_id": 999999, "itens": [_item(p.suco, 1)]}, "caixa_indisponivel", None),
        ({"caixa_id": p.catalogo, "caixa_movimento_id": p.sessao_fechada,
          "itens": [_item(p.suco, 1)]}, "sessao_caixa_invalida", None),
        ({"caixa_id": p.catalogo, "itens": [_item(p.suco, 0)]}, "quantidade_invalida", 1),
        ({"caixa_id": p.catalogo, "itens": [_item(p.suco, 1.5)]}, "quantidade_invalida", 1),
        ({"caixa_id": p.catalogo, "itens": [_item(p.suco, True)]}, "quantidade_invalida", 1),
        ({"caixa_id": p.catalogo, "itens": [_item(999999, 1)]}, "produto_indisponivel", 1),
        ({"caixa_id": p.catalogo, "itens": [_item(p.gratis, 1)]}, "produto_preco_invalido", None),
        ({"caixa_id": p.catalogo, "itens": []}, "cesta_vazia", None),
        ({"caixa_id": p.catalogo, "itens": "isto não é json"}, "cesta_invalida", None),
        ({"caixa_id": p.livre, "itens": [_item(p.suco, 1)]}, "produto_fora_do_caixa", None),
        ({"caixa_id": p.catalogo, "itens": [{"produto_id": None, "nome": "x",
                                             "quantidade": 1}]}, "item_sem_produto", 1),
        ({"caixa_id": p.catalogo, "itens": [_item(p.suco, 1)],
          "valor_recebido": "abc"}, "valor_recebido_invalido", None),
        ({"caixa_id": p.catalogo, "itens": [_item(p.suco, 1)],
          "doacao_valor": "-10"}, "doacao_invalido", None),
    ]
    for kwargs, chave, item in casos:
        r = _finalizar(**kwargs)
        assert_eq(r.erro, chave, f"R6 {kwargs.get('itens')} → {chave} (veio {r.erro}, destino {r.destino})")
        if item:
            assert_eq(r.item, str(item), f"R6 índice do item em {chave}")
    assert_eq(p.n_vendas(), antes, "R6 nenhuma venda gravada")


@teste("R7 caixa de preço aberto: valor declarado vira total do servidor")
def r7():
    p = pdv()
    livre = {"produto_id": None, "nome": "Camisola usada", "quantidade": 2,
             "preco_unitario": 15.50, "subtotal": 31.00}
    movs_antes, estoque_antes = len(p.movimentos(p.suco)), p.estoque(p.suco)
    r = _finalizar(caixa_id=p.livre, itens=[livre], valor_recebido="50.00")
    assert r.ok, f"R7 {r.status} {r.destino}"
    venda = p.venda_de(int(r.ok))
    assert_eq(str(_dec(venda["total"])), "31.00", "R7 total")
    assert_eq(str(_dec(venda["troco"])), "19.00", "R7 troco")
    assert_eq(len(venda["itens"]), 0, "R7 sem itens de catálogo (modelo atual)")
    assert_eq(len(p.movimentos(p.suco)), movs_antes, "R7 estoque intacto")
    r = _finalizar(caixa_id=p.livre, itens=[livre, _item(p.suco, 1)])
    assert_eq(r.erro, "produto_fora_do_caixa", "R7 catálogo no caixa aberto")
    assert_eq(p.estoque(p.suco), estoque_antes, "R7 catálogo não baixou no barramento")


@teste("R8 espécie, PIX e doação: mesmo cálculo, troco só na espécie")
def r8():
    p = pdv()
    r = _finalizar(caixa_id=p.catalogo, itens=[_item(p.caro, 1)],
                   forma_pagamento="pix", valor_recebido="0")
    assert r.ok, f"R8 pix {r.destino}"
    venda = p.venda_de(int(r.ok))
    assert_eq(str(_dec(venda["total"])), "999999.99", "R8 pix total")
    assert_eq(str(_dec(venda["troco"])), "0.00", "R8 pix sem troco")

    r = _finalizar(caixa_id=p.catalogo, itens=[_linha(p.suco, 1, "12.50")],
                   forma_pagamento="especie", valor_recebido="20.00", doacao_valor="7.50")
    venda = p.venda_de(int(r.ok))
    # semantics preservadas do código anterior: o campo troco guarda o troco
    # CALCULADO (recebido − total); "doar troco" registra o valor na doação e
    # a sessão soma total + doação (é o que entra na gaveta).
    assert_eq(str(_dec(venda["troco"])), "7.50", "R8 troco é o que o servidor calculou")
    assert_eq(str(_dec(venda["doacao_valor"])), "7.50", "R8 doação")
    sessao = p.sessao_de(p.nova_sessao())
    r = _finalizar(caixa_id=p.catalogo, caixa_movimento_id=sessao["id"],
                   itens=[_linha(p.suco, 1, "12.50")], doacao_valor="7.50")
    assert_eq(_dec(p.sessao_de(sessao["id"])["total_vendas"]), Decimal("20.00"),
              "R8 sessão soma total + doação")


@teste("R9 fiado: crédito, limite e bloqueios pelo valor do servidor")
def r9():
    p = pdv()
    r = _finalizar(caixa_id=p.catalogo, forma_pagamento="fiado",
                   trabalhador_id=p.trab_fiado, itens=[_linha(p.suco, 2, "12.50")],
                   valor_recebido="0")
    assert r.ok, f"R9 {r.status} {r.destino}"
    venda = p.venda_de(int(r.ok))
    assert_eq(str(_dec(venda["total"])), "25.00", "R9 total do servidor")
    assert_eq(str(_dec(venda["fiado_credito_usado"])), "5.00", "R9 crédito consumido")
    assert_eq(str(_dec(p.trabalhador(p.trab_fiado)["fiado_credito"])), "0.00", "R9 crédito zerado")
    creditos = p.creditos(p.trab_fiado)
    assert_eq(len(creditos), 1, "R9 um débito registrado")
    assert_eq(str(_dec(creditos[0]["valor"])), "5.00", "R9 valor do débito")

    # 20,00 de limite, deve 25,00 → recusa (mesmo crédito estourado).
    antes = p.n_vendas()
    r = _finalizar(caixa_id=p.catalogo, forma_pagamento="fiado",
                   trabalhador_id=p.trab_fiado, itens=[_linha(p.suco, 2, "12.50")])
    assert_eq(r.erro, "fiado_excede_limite", "R9 limite pelo total do servidor")
    assert_eq(p.n_vendas(), antes, "R9 recusa sem escrita")

    p.encerrar_fiado(p.trab_sem_limite)
    r = _finalizar(caixa_id=p.catalogo, forma_pagamento="fiado",
                   trabalhador_id=p.trab_sem_limite, itens=[_item(p.agua, 1)])
    assert_eq(r.erro, "fiado_encerrado", "R9 fiado encerrado")
    r = _finalizar(caixa_id=p.catalogo, forma_pagamento="fiado",
                   trabalhador_id=999999, itens=[_item(p.agua, 1)])
    assert_eq(r.erro, "trabalhador_nao_encontrado", "R9 trabalhador inexistente")


@teste("R10 concorrência: 6 vendas de 3 unidades, saldo determinístico")
def r10():
    p = pdv()
    prod = p.novo_produto("1.00", 18, nome="Concorrência")
    sessao = p.nova_sessao()
    erros = []

    def vender():
        try:
            r = _finalizar(caixa_id=p.catalogo, caixa_movimento_id=sessao,
                           itens=[{"produto_id": prod, "quantidade": 3}],
                           valor_recebido="3")
            if not r.ok:
                erros.append(f"status {r.status} destino {r.destino}")
        except Exception as e:
            erros.append(repr(e))

    fios = [threading.Thread(target=vender) for _ in range(6)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()
    assert_eq(erros, [], "R10 nenhuma venda recusada")
    assert_eq(p.estoque(prod), 0, "R10 nenhuma baixa perdida")
    assert_eq(_dec(p.sessao_de(sessao)["total_vendas"]), Decimal("18.00"), "R10 sessão somada")
    pares = sorted((m["saldo_anterior"], m["saldo_posterior"]) for m in p.movimentos(prod))
    assert_eq(pares, [(3, 0), (6, 3), (9, 6), (12, 9), (15, 12), (18, 15)],
              "R10 saldos encadeados sem buraco")


@teste("R11 rollback pela rota: exceção na escrita não deixa venda nem estoque")
def r11():
    p = pdv()
    prod = p.novo_produto("2.00", 5, nome="Rollback")
    sessao = p.nova_sessao()
    antes_vendas, antes_movs = p.n_vendas(), len(p.movimentos(prod))

    import rotas.caixa as rc
    original = rc.conectar
    rc.conectar = _injetar_falha_na_escrita("UPDATE produtos SET quantidade_estoque")
    try:
        try:
            _rodar_rota_sem_http(p, [{"produto_id": prod, "quantidade": 2}], sessao=sessao,
                                 valor_recebido="0")
            falhou = False
        except RuntimeError:
            falhou = True
    finally:
        rc.conectar = original
    assert falhou, "R11 a falha injetada interrompe a venda"
    assert_eq(p.n_vendas(), antes_vendas, "R11 venda rollback")
    assert_eq(len(p.movimentos(prod)), antes_movs, "R11 movimentos rollback")
    assert_eq(p.estoque(prod), 5, "R11 estoque rollback")
    assert_eq(_dec(p.sessao_de(sessao)["total_vendas"]), Decimal("0.00"), "R11 sessão rollback")


@teste("R12 redirect de sucesso leva ao id persistido (comprovante)")
def r12():
    p = pdv()
    r = _finalizar(caixa_id=p.catalogo, itens=[_item(p.suco, 1)], imprimir="1")
    assert r.ok, f"R12 {r.status} {r.destino}"
    assert_eq(r.destino.split("ok=")[0], f"/caixa/pdv?caixa_id={p.catalogo}&", "R12 destino")
    venda = p.venda_de(int(r.ok))
    assert_eq(str(_dec(venda["total"])), "12.50", "R12 total impresso = persistido")
    assert_eq(venda["status"], "concluida", "R12 status")
    tela = _get(f"/caixa/pdv?caixa_id={p.catalogo}&ok={r.ok}")
    assert_eq(tela.status, 200, "R12 tela pós-venda")
    assert str(venda["id"]) in tela.corpo, "R12 comprovante usa o id gravado"


@teste("R13 venda histórica: mudar o cadastro não reescreve o snapshot")
def r13():
    p = pdv()
    prod = p.novo_produto("12.50", 50, nome="Ontem")
    r = _finalizar(caixa_id=p.catalogo, itens=[_linha(prod, 2, "12.50")])
    assert r.ok, f"R13 venda original {r.status} {r.destino}"
    venda_antiga = p.venda_de(int(r.ok))
    p.preco(prod, "99.00")
    with conectar() as conn:
        conn.execute("UPDATE produtos SET nome = %s, ativo = 0 WHERE id = %s",
                     (f"{PREFIXO} Ontem renomeado", prod))
    revogada = p.venda_de(int(r.ok))
    assert_eq(revogada, venda_antiga, "R13 venda e itens históricos intactos após mudança")
    item = venda_antiga["itens"][0]
    assert_eq(str(_dec(item["preco_unitario"])), "12.50", "R13 preço do snapshot preservado")
    assert_eq(str(_dec(venda_antiga["total"])), "25.00", "R13 total histórico preservado")
    # venda nova após a mudança usa o cadastro novo — o histórico não contamina
    r = _finalizar(caixa_id=p.catalogo, itens=[{"produto_id": prod, "quantidade": 1}])
    assert_eq(r.erro, "produto_indisponivel", "R13 produto inativo não vende mais")


@teste("R14 cesta multi-itens com centavos: total = soma dos subtotais persistidos")
def r14():
    p = pdv()
    centavo = p.novo_produto("0.05", 1000, nome="Centavo")
    r = _finalizar(caixa_id=p.catalogo, itens=[
        _item(centavo, 7), _item(p.suco, 1), _item(p.agua, 3), _item(p.caro, 1)],
        valor_recebido="1000100.00")
    assert r.ok, f"R14 {r.status} {r.destino}"
    venda = p.venda_de(int(r.ok))
    subtotais = [str(_dec(i["subtotal"])) for i in venda["itens"]]
    assert_eq(subtotais, ["0.35", "12.50", "9.00", "999999.99"], "R14 subtotais por linha")
    soma = sum((_dec(s) for s in subtotais), Decimal("0"))
    assert_eq(str(_dec(venda["total"])), str(soma), "R14 total = soma exata dos subtotais")
    assert_eq(str(_dec(venda["total"])), "1000021.84", "R14 valor final")
    assert_eq(str(_dec(venda["troco"])), "78.16", "R14 troco pelo total do servidor")


# ── 3. Autorização (a venda continua barrada para quem não é operador) ────────

@teste("A1 anônimo não finaliza venda")
def a1():
    p = pdv()
    antes = p.n_vendas()
    r = _finalizar(caixa_id=p.catalogo, itens=[{"produto_id": p.suco, "quantidade": 1}],
                   novo=True)
    assert_eq(r.status, 303, "A1 anônimo é redirecionado")
    assert_eq(r.destino, "/login", f"A1 {r.destino}")
    assert_eq(p.n_vendas(), antes, "A1 nada gravado")


@teste("A2 sessão de terminal não vale para finalizar venda")
def a2():
    p = pdv()
    antes = p.n_vendas()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _SemRedirect())
    _req(opener, "POST", "/login", {"nome_usuario": "admin", "senha": "teste123"})
    _req(opener, "POST", "/terminal/ativar", {})
    status, corpo, destino = _req(opener, "GET", "/terminal/")
    assert_eq(status, 200, f"A2 aparelho virou terminal ({status} {destino})")
    status, corpo, destino = _req(opener, "POST", "/caixa/pdv/finalizar", {
        "caixa_id": p.catalogo, "caixa_movimento_id": 0, "forma_pagamento": "especie",
        "valor_recebido": "0", "itens_json": json.dumps(
            [{"produto_id": p.suco, "quantidade": 1}]),
        "trabalhador_id": 0, "imprimir": "0", "doacao_valor": "0"})
    assert_eq(status, 303, "A2 sessão de terminal não finalizou")
    assert_eq(destino, "/login", f"A2 {destino}")
    assert_eq(p.n_vendas(), antes, "A2 nada gravado")


@teste("A3 usuário autenticado sem permissão não finaliza venda")
def a3():
    p = pdv()
    antes = p.n_vendas()
    from rotas.auth import hash_senha
    usuario = f"{PREFIXO}operador"
    with conectar() as conn:
        cur = conn.execute(
            "INSERT INTO grupos (nome, descricao) VALUES (%s, 'sem caixa') RETURNING id",
            (f"{PREFIXO} grupo",))
        grupo = cur.fetchone()["id"]
        cur = conn.execute(
            """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
               VALUES (%s, %s, %s, 1) RETURNING id""",
            (usuario, "Operador sem permissão", hash_senha("senha-de-teste")))
        atendente = cur.fetchone()["id"]
        conn.execute("INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, %s)",
                     (atendente, grupo))
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _SemRedirect())
    _req(opener, "POST", "/login", {"nome_usuario": usuario, "senha": "senha-de-teste"})
    status, corpo, destino = _req(opener, "POST", "/caixa/pdv/finalizar", {
        "caixa_id": p.catalogo, "caixa_movimento_id": 0, "forma_pagamento": "especie",
        "valor_recebido": "0", "itens_json": json.dumps(
            [{"produto_id": p.suco, "quantidade": 1}]),
        "trabalhador_id": 0, "imprimir": "0", "doacao_valor": "0"})
    assert_eq(status, 403, f"A3 negação por falta de permissão (veio {status} {destino})")
    assert "Acesso negado" in corpo, "A3 corpo da negação"
    assert_eq(p.n_vendas(), antes, "A3 nada gravado")
    with conectar() as conn:
        conn.execute("DELETE FROM usuarios_grupos WHERE usuario_id = %s", (atendente,))
        conn.execute("DELETE FROM atendentes WHERE id = %s", (atendente,))
        conn.execute("DELETE FROM grupos WHERE id = %s", (grupo,))


# ── 4. Navegador real: a cesta reprificada volta à tela ──────────────────────

def _ferramentas_ausentes():
    faltam = []
    try:
        import playwright  # noqa: F401
    except ImportError:
        faltam.append("Playwright (python -m pip install playwright)")
    caminho = os.environ.get("SHAMBALA_CHROMIUM", "")
    if not caminho or not os.path.exists(caminho):
        caminho = next((c for c in ("/usr/bin/chromium", "/usr/bin/chromium-browser",
                                    "/usr/bin/google-chrome") if os.path.exists(c)), "")
        if not caminho:
            import subprocess as sp
            procura = sp.run(["python3", "-c",
                              "from playwright.sync_api import sync_playwright;"
                              "print(sync_playwright().start().chromium.executable_path)"],
                             capture_output=True, text=True)
            caminho = (procura.stdout or "").strip().splitlines()[-1] if procura.returncode == 0 else ""
    if not caminho or not os.path.exists(caminho):
        faltam.append("Chromium (a tela precisa rodar num navegador real)")
    else:
        os.environ["SHAMBALA_CHROMIUM"] = caminho
    return faltam


def _sessao_browser():
    """Login real; devolve o valor do cookie de sessão para injetar no navegador."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj), _SemRedirect())
    _req(opener, "POST", "/login", {"nome_usuario": "admin", "senha": "teste123"})
    for cookie in cj:
        if cookie.name == "sessao":
            return cookie.value
    raise AssertionError("o login não entregou o cookie de sessão")


@teste("B1 navegador: adulteração na tela termina em revisão com a cesta reprificada")
def b1():
    faltam = _ferramentas_ausentes()
    if faltam:
        print(f"        [pulado: {', '.join(faltam)}]")
        return
    p = pdv()
    antes_vendas, estoque_antes = p.n_vendas(), p.estoque(p.suco)

    from playwright.sync_api import sync_playwright
    host = urllib.parse.urlsplit(BASE).hostname
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ["SHAMBALA_CHROMIUM"])
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies([{"name": "sessao", "value": _sessao_browser(),
                                  "domain": host, "path": "/"}])
            pagina = context.new_page()
            pagina.goto(BASE + f"/caixa/pdv?caixa_id={p.catalogo}")
            pagina.wait_for_selector("#form-finalizar", state="attached", timeout=8000)
            # o operador "adultera" a cesta no console e envia o formulário real
            pagina.evaluate(
                """(pid) => {
                    document.getElementById('f-itens-json').value = JSON.stringify(
                        [{produto_id: pid, nome: 'Suco', quantidade: 2,
                          preco_unitario: 0.01, subtotal: 0.02}]);
                    document.getElementById('f-pagamento').value = 'especie';
                    document.getElementById('f-valor-recebido').value = '50.00';
                    document.getElementById('form-finalizar').submit();
                }""", p.suco)
            pagina.wait_for_selector("#pdv-cesta-seed", state="attached", timeout=8000)
            pagina.get_by_text("O preço de algum produto mudou").first.wait_for(timeout=8000)
            linha = pagina.locator(".pdv-item").first
            linha.wait_for(timeout=8000)
            texto = linha.inner_text()
            assert "Suco" in texto and "25,00" in texto, f"B1 linha da cesta: {texto!r}"
            total_tela = pagina.locator("#pdv-total").inner_text()
            assert total_tela == "R$ 25,00", f"B1 total da tela: {total_tela!r}"
        finally:
            browser.close()
    assert_eq(p.n_vendas(), antes_vendas, "B1 a revisão na tela não gravou nada")
    assert_eq(p.estoque(p.suco), estoque_antes, "B1 estoque intacto")


# ── Runner ────────────────────────────────────────────────────────────────────

def _limpar():
    with conectar() as conn:
        conn.execute(
            "DELETE FROM vendas_pdv_itens WHERE venda_id IN "
            "(SELECT id FROM vendas_pdv WHERE caixa_id IN "
            "(SELECT id FROM caixas WHERE nome LIKE %s))", (f"{PREFIXO}%",))
        conn.execute("DELETE FROM vendas_pdv WHERE caixa_id IN "
                     "(SELECT id FROM caixas WHERE nome LIKE %s)", (f"{PREFIXO}%",))
        conn.execute("DELETE FROM caixa_sangrias WHERE caixa_movimento_id IN "
                     "(SELECT id FROM caixa_movimentos WHERE caixa_id IN "
                     "(SELECT id FROM caixas WHERE nome LIKE %s))", (f"{PREFIXO}%",))
        conn.execute("DELETE FROM caixa_movimentos WHERE caixa_id IN "
                     "(SELECT id FROM caixas WHERE nome LIKE %s)", (f"{PREFIXO}%",))
        conn.execute("DELETE FROM estoque_movimentos WHERE produto_id IN "
                     "(SELECT id FROM produtos WHERE nome LIKE %s)", (f"{PREFIXO}%",))
        conn.execute("DELETE FROM fiado_credito_mov WHERE trabalhador_id IN "
                     "(SELECT id FROM trabalhadores WHERE nome_completo LIKE %s)",
                     (f"{PREFIXO}%",))
        conn.execute("DELETE FROM produtos WHERE nome LIKE %s", (f"{PREFIXO}%",))
        conn.execute("DELETE FROM trabalhadores WHERE nome_completo LIKE %s", (f"{PREFIXO}%",))
        conn.execute("DELETE FROM caixas WHERE nome LIKE %s", (f"{PREFIXO}%",))


def main():
    global ADMIN_ID, PDV_FIXTURE
    preparar_banco()
    _garantir_admin()
    with conectar() as conn:
        ADMIN_ID = conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario = 'admin'"
        ).fetchone()["id"]
    _login()
    PDV_FIXTURE = PDV()
    total, ok = 0, 0
    for nome, fn in RESULTADOS:
        total += 1
        try:
            fn()
            ok += 1
            print(f"  PASS  {nome}")
        except Exception as e:
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{ok}/{total} testes passaram (TASK_006 — PDV no servidor)")
    try:
        _limpar()
    except Exception as e:
        print(f"  [limpeza] {e}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
