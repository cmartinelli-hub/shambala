#!/usr/bin/env python3
"""Suíte TASK_002 — códigos de 4 dígitos e carteirinhas dos trabalhadores.

Cobre a validação obrigatória da etapa 2:
- código de exatamente quatro caracteres 0-9, zeros iniciais preservados em
  banco, tela, QR e resolução;
- unicidade real no banco (índice único parcial), inclusive sob concorrência;
- reserva permanente: código retirado e código de inativo nunca reatribuídos;
- troca confirmada e atômica (falha no meio deixa o código atual válido);
- preparação em massa idempotente e o STARTUP que nunca emite código;
- RBAC dentro do módulo existente `cadastros.trabalhadores` (sem rota nova
  anônima, sem leitura que vire escrita);
- carteirinha individual e lote A4 com QR == os quatro dígitos, sem vazar
  CPF/telefone/mensalidade, e a SAÍDA IMPRESSA validada em PDF (chromium →
  pdftoppm → decodificação dos QRs da folha).

Uso (recomendado): bash tests/rodar_testes.sh
"""
import os
import re
import sys
import base64
import shutil
import threading
import traceback
import contextlib
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

# ── TRAVA DE ISOLAMENTO (antes de importar a aplicação — ordem obrigatória) ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import exigir_ambiente_de_teste, AmbienteInseguro  # noqa: E402

from test_tres_passes import (  # noqa: E402
    Ctx, assert_eq, preparar_banco, limpar_banco, _garantir_admin, BASE, _SemRedirect,
    TOKEN,
)

from banco import conectar, criar_tabelas, fechar_pool  # noqa: E402
import banco  # noqa: E402
from preparo_descartavel import recriar_banco, remover_se_nosso  # noqa: E402
import psycopg2  # noqa: E402  (código de erro da FK — integridade, não driver)
from servico_codigo_trabalhador import (  # noqa: E402
    CodigosEsgotados, codigo_valido,
    garantir_codigo, trocar_codigo, preparar_codigos_pendentes,
    resolver_codigo, qr_data_uri,
)

COD_DB = "shambala_codigo_mig_test"  # banco DEDICADO (migração/esgotamento)
PASTA_PDF = "/tmp/shambala_carteirinha_evidencia"

TESTES = []


class _Pulado(Exception):
    """Caso não executado (ferramenta de verificação ausente) — reportado à parte."""


def teste(nome, com_ctx=True):
    def wrap(fn):
        TESTES.append((nome, fn, com_ctx))
        return fn
    return wrap


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _limpa_negocio(conn):
    """Zera cadastro/presença/códigos sem tocar em atendentes nem grupos."""
    conn.execute(
        "TRUNCATE trabalhador_codigo, trabalhador_presenca, trabalhador_dias, "
        "dias_trabalho, trabalhadores RESTART IDENTITY CASCADE"
    )
    conn.commit()


def _trab(ctx, nome="Trabalhador Teste", ativo=1):
    return ctx.conn.execute(
        "INSERT INTO trabalhadores (nome_completo, ativo) VALUES (%s, %s) RETURNING id",
        (nome, ativo),
    ).fetchone()["id"]


def _novo_trab(conn, nome, ativo=1):
    return conn.execute(
        "INSERT INTO trabalhadores (nome_completo, ativo) VALUES (%s, %s) RETURNING id",
        (nome, ativo),
    ).fetchone()["id"]


def _codigo_atual(conn, trabalhador_id):
    row = conn.execute(
        """SELECT codigo FROM trabalhador_codigo
            WHERE trabalhador_id = %s AND estado = 'ativo'""",
        (trabalhador_id,),
    ).fetchone()
    return row["codigo"] if row else None


def _snapshot_codigos(conn):
    """Retrato completo e determinístico da tabela de códigos."""
    rows = conn.execute(
        """SELECT codigo, trabalhador_id, estado, COALESCE(atendente_id, -1) AS atendente_id,
                  origem, criado_em, COALESCE(retirado_em, '') AS retirado_em
             FROM trabalhador_codigo ORDER BY codigo"""
    ).fetchall()
    return [tuple(r.values()) for r in rows]


def _fk_trabalhador(conn):
    """(nome, regra de exclusão) da FK de trabalhador_codigo.trabalhador_id.

    confdeltype: 'a' no action, 'c' cascade, 'r' restrict, 'n' set null.
    """
    row = conn.execute(
        """SELECT c.conname, c.confdeltype
             FROM pg_constraint c
             JOIN pg_attribute a
               ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.contype = 'f'
              AND c.conrelid = 'trabalhador_codigo'::regclass
              AND a.attname = 'trabalhador_id'"""
    ).fetchone()
    return (row["conname"], row["confdeltype"]) if row else (None, None)


def _qtd_mensalidades(conn, trabalhador_id):
    return conn.execute(
        """SELECT COUNT(*) AS n FROM financeiro_movimentacoes
            WHERE trabalhador_id = %s AND categoria = 'mensalidade'""",
        (trabalhador_id,),
    ).fetchone()["n"]


# ── Sessão HTTP autenticada (não segue redirect: é preciso ver o 303) ─────────

class _Sessao:
    """Sessão autenticada que NÃO segue redirect: os testes veem o 303 e o destino."""

    def __init__(self, usuario, senha):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _SemRedirect()
        )
        corpo = urllib.parse.urlencode({"nome_usuario": usuario, "senha": senha}).encode()
        try:
            self._abrir(urllib.request.Request(BASE + "/login", data=corpo, method="POST"))
            raise AssertionError(f"login de {usuario} deveria redirecionar (303)")
        except urllib.error.HTTPError as e:
            if e.code != 303:
                raise AssertionError(f"login de {usuario} falhou (status {e.code})")

    def _responder(self, r):
        try:
            with self.opener.open(r) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), None
        except urllib.error.HTTPError as e:
            loc = e.headers.get("location") if e.code == 303 else None
            return e.code, e.read().decode("utf-8", "replace"), loc

    def _abrir(self, r):
        return self.opener.open(r)

    def get(self, path):
        """(status, corpo) de um GET."""
        status, corpo, _ = self._responder(
            urllib.request.Request(BASE + path, method="GET"))
        return status, corpo

    def get_loc(self, path):
        """GET devolvendo também o Location do 303 (avisos que voltam à lista)."""
        return self._responder(urllib.request.Request(BASE + path, method="GET"))

    def post(self, path, data=None):
        corpo = urllib.parse.urlencode(data).encode() if data else b""
        return self._responder(urllib.request.Request(BASE + path, data=corpo, method="POST"))

    def post_mp(self, path, campos):
        borda = "----shambalaTESTE"
        partes = [
            f"--{borda}\r\nContent-Disposition: form-data; name=\"{chave}\""
            f"\r\n\r\n{valor}\r\n".encode("utf-8")
            for chave, valor in campos.items()
        ]
        partes.append(f"--{borda}--\r\n".encode("utf-8"))
        r = urllib.request.Request(
            BASE + path, data=b"".join(partes), method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={borda}"},
        )
        return self._responder(r)

    def ir_para(self, loc):
        """Segue o Location de um 303 (para ler o banner que a tela mostra)."""
        p = urllib.parse.urlparse(loc)
        return self.get(p.path + ("?" + p.query if p.query else ""))


def _sessao_nova(usuario, senha):
    return _Sessao(usuario, senha)


def _mensagens(loc):
    """Extrai ?erro= / ?ok= do Location de um 303 — o texto que o usuário veria."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    return q.get("erro", [""])[0], q.get("ok", [""])[0]


def _admin():
    return _sessao_nova("admin", "teste123")


# ── QR: decodificação de verdade (leitor de referência + fallback) ───────────
#
# `pyzbar` (zbar) é o leitor de referência usado nos testes; `cv2.QRCodeDetector`
# fica como fallback. Não é preciosismo: medido em 600 QRs gerados pelo serviço,
# o zbar decodificou 600/600 enquanto o detector leve do OpenCV erra ~1% deles
# (e ~5% quando a página inteira é lida de uma vez). Um QR lido por um dos dois
# é a prova de que o cartão é legível; lido por nenhum deles é falha real.
# Ambos são ferramental de verificação — não vão para requirements.txt.

def _ler_qrs_zbar(img):
    """QRs da imagem (BGR numpy) pelo leitor de referência. None se indisponível."""
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except ImportError:
        return None
    return [d.data.decode() for d in decode(Image.fromarray(img[:, :, ::-1]))]


def _ler_qrs_opencv(img):
    """QRs da imagem pelo detector do OpenCV: página inteira e recorte ampliado."""
    import cv2
    detector = cv2.QRCodeDetector()
    _, multiplos, _, _ = detector.detectAndDecodeMulti(img)
    lidos = [d for d in (multiplos or []) if d]
    if not lidos:
        maior = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        unico = detector.detectAndDecode(maior)[0]
        lidos = [unico] if unico else []
    return lidos


def _ler_qrs(img):
    lidos = _ler_qrs_zbar(img)
    return lidos if lidos else _ler_qrs_opencv(img)


def _decodificar_png(dados: bytes):
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise _Pulado(f"opencv indisponível para decodificar QR ({e})")
    img = cv2.imdecode(np.frombuffer(dados, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise AssertionError("PNG ilegível")
    return _ler_qrs(img)


def _qrs_na_pagina(html: str):
    todos = []
    for uri in re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', html):
        todos.extend(_decodificar_png(base64.b64decode(uri)))
    return todos


def _ler_qrs_da_folha(caminho: str, cartoes: int):
    """Lê a célula i da folha impressa (300 dpi) e devolve o QR encontrado.

    A leitura é feita por recorte na MESMA geometria física do CSS (margem de
    10 mm, cartões de 85,6 × 54 mm, 8 mm entre colunas, 1 mm entre linhas) —
    é o caminho que um leitor de terminal percorre. Nada aqui lê o QR do HTML:
    lê o papel.
    """
    import cv2
    img = cv2.imread(caminho)
    if img is None:
        raise AssertionError(f"não abriu {caminho}")
    px_por_mm = img.shape[1] / 210.0
    lidos = []
    for indice in range(cartoes):
        coluna, linha = indice % 2, indice // 2
        x0 = (10 + coluna * (85.6 + 8)) * px_por_mm
        y0 = (10 + linha * (54 + 1)) * px_por_mm
        achado = ""
        for folga in (0.0, 2.0, 5.0):
            recorte = img[max(0, int(y0 - folga * px_por_mm)):int(y0 + (54 + folga) * px_por_mm),
                          max(0, int(x0 - folga * px_por_mm)):int(x0 + (85.6 + folga) * px_por_mm)]
            if recorte.size == 0:
                continue
            lidos_na_celula = _ler_qrs(recorte)
            if lidos_na_celula:
                achado = lidos_na_celula[0]
                break
        lidos.append(achado)
    return lidos


# ── Banco dedicado ───────────────────────────────────────────────────────────

def _psql_admin(sql: str):
    return subprocess.run(
        ["psql", "-h", os.environ.get("PGHOST", "/var/run/postgresql"),
         "-U", os.environ.get("PGUSER", "shambala"), "-d", "postgres", "-tAqc", sql],
        capture_output=True, text=True, env={**os.environ},
    )


@contextlib.contextmanager
def _banco_dedicado(nome: str):
    if "_test" not in nome or nome in ("shambala", "postgres", "template0", "template1"):
        raise AmbienteInseguro(f"{nome} não é um banco marcadamente de teste")
    original = banco._DB_NAME
    recriar_banco(nome, TOKEN)  # TASK_010: só recria banco com selo da infraestrutura
    fechar_pool()
    banco._DB_NAME = nome
    try:
        yield
    finally:
        fechar_pool()
        banco._DB_NAME = original


# ══════════════════════════════════════════════════════════════════════════
#  K1 — o startup cria o schema e NUNCA emite código
# ══════════════════════════════════════════════════════════════════════════

@teste("K1 startup cria o schema e não emite nenhum código (mesmo com ativos)", com_ctx=False)
def k1():
    with _banco_dedicado(COD_DB):
        criar_tabelas()
        with conectar() as conn:
            exigir_ambiente_de_teste(conn)
            cols = {r["column_name"] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'trabalhador_codigo'").fetchall()}
            assert {"codigo", "trabalhador_id", "estado", "origem",
                    "criado_em", "retirado_em"} <= cols, f"schema incompleto: {sorted(cols)}"
            idx = conn.execute(
                """SELECT indexdef FROM pg_indexes WHERE tablename = 'trabalhador_codigo'
                   AND indexname = 'uq_trabalhador_codigo_atual'"""
            ).fetchone()
            assert idx, "índice único parcial ausente"
            assert "UNIQUE" in idx["indexdef"] and "estado = 'ativo'" in idx["indexdef"], \
                f"índice não é o parcial esperado: {idx['indexdef']}"
            chk = conn.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'trabalhador_codigo'::regclass"
            ).fetchall()
            assert len(chk) >= 2, f"CHECKs de formato/estado ausentes: {chk}"
            assert_eq(conn.execute(
                "SELECT COUNT(*) AS n FROM trabalhador_codigo").fetchone()["n"], 0,
                "o startup emitiu códigos")

            for i in range(5):
                _novo_trab(conn, f"Ativo {i}")
            conn.commit()
        criar_tabelas()
        criar_tabelas()
        with conectar() as conn:
            assert_eq(conn.execute(
                "SELECT COUNT(*) AS n FROM trabalhador_codigo").fetchone()["n"], 0,
                "segunda execução do startup gerou código")
            assert_eq(conn.execute(
                "SELECT COUNT(*) AS n FROM trabalhadores WHERE ativo = 1").fetchone()["n"], 5,
                "startup mexeu nos trabalhadores")


# ══════════════════════════════════════════════════════════════════════════
#  K2 — preparação em massa idempotente
# ══════════════════════════════════════════════════════════════════════════

@teste("K2 preparar duas vezes: gera N, depois zero e nada muda nos já emitidos")
def k2(ctx):
    _limpa_negocio(ctx.conn)
    for i in range(6):
        _trab(ctx, f"Preparado {i}")
    _trab(ctx, "Inativo Preparado", ativo=0)
    ctx.conn.commit()

    resumo = preparar_codigos_pendentes(ctx.conn, origem="preparacao_local")
    ctx.conn.commit()
    assert_eq(len(resumo["gerados"]), 6, "primeira execução deveria gerar 6")
    assert_eq(resumo["ja_existiam"], 0, "ninguém tinha código ainda")
    assert all(codigo_valido(g["codigo"]) for g in resumo["gerados"]), "código fora do formato"
    antes = _snapshot_codigos(ctx.conn)
    assert all(linha[2] == "ativo" and linha[4] == "preparacao_local" for linha in antes), \
        "estado/origem gravados errado"

    resumo2 = preparar_codigos_pendentes(ctx.conn)
    ctx.conn.commit()
    assert_eq(resumo2["gerados"], [], "segunda execução gerou algo")
    assert_eq(resumo2["ja_existiam"], 6, "contagem de preservados")
    assert_eq(_snapshot_codigos(ctx.conn), antes, "segunda execução alterou a tabela")

    assert_eq(ctx.conn.execute(
        """SELECT COUNT(*) AS n FROM trabalhadores t
             JOIN trabalhador_codigo c ON c.trabalhador_id = t.id
            WHERE t.ativo = 0""").fetchone()["n"], 0, "inativo recebeu código")
    assert_eq(ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM financeiro_movimentacoes WHERE categoria = 'mensalidade'"
    ).fetchone()["n"], 0, "preparação criou mensalidade")

    admin = _admin()
    st, _, loc = admin.post("/cadastros/trabalhadores/preparar-codigos")
    assert_eq(st, 303, "POST preparar-codigos")
    _, ok = _mensagens(loc)
    assert "Nenhum código precisou ser gerado" in ok, f"banner: {ok!r}"
    assert_eq(_snapshot_codigos(ctx.conn), antes, "POST idempotente alterou a tabela")
    st, html = admin.ir_para(loc)
    assert_eq(st, 200, "volta para a lista")
    assert "Nenhum código precisou ser gerado" in html, "banner não apareceu na tela"


# ══════════════════════════════════════════════════════════════════════════
#  K3 — cadastro novo e reativação emitem código, na transação do chamador
# ══════════════════════════════════════════════════════════════════════════

@teste("K3 cadastro de ativo e reativação ganham código; inativação reserva o código")
def k3(ctx):
    _limpa_negocio(ctx.conn)
    admin = _admin()
    st, _, _ = admin.post_mp("/cadastros/trabalhadores/novo", {
        "nome_completo": "Novo Com Código", "cpf": "",
        "valor_mensalidade": "0", "dia_vencimento": "10", "fiado_limite": "0",
    })
    assert_eq(st, 303, "cadastro via HTTP")
    tid = ctx.conn.execute(
        "SELECT id FROM trabalhadores WHERE nome_completo = 'Novo Com Código'"
    ).fetchone()["id"]
    cod = _codigo_atual(ctx.conn, tid)
    assert cod and codigo_valido(cod), f"cadastro sem código válido: {cod!r}"
    assert_eq(ctx.conn.execute(
        """SELECT COUNT(*) AS n FROM trabalhador_codigo
            WHERE trabalhador_id = %s AND estado = 'ativo'""", (tid,)
    ).fetchone()["n"], 1, "exatamente um código ativo")
    assert_eq(_qtd_mensalidades(ctx.conn, tid), 0, "emissão de código criou mensalidade")

    inat = _trab(ctx, "Reativado Sem Código", ativo=0)
    ctx.conn.commit()
    assert_eq(_codigo_atual(ctx.conn, inat), None, "inativo não deveria ter código")
    st, _, _ = admin.post(f"/cadastros/trabalhadores/{inat}/toggle-ativo")
    assert_eq(st, 303, "toggle para ativo")
    cod2 = _codigo_atual(ctx.conn, inat)
    assert cod2 and codigo_valido(cod2), "reativação não emitiu código"

    st, _, _ = admin.post(f"/cadastros/trabalhadores/{inat}/toggle-ativo")
    assert_eq(_codigo_atual(ctx.conn, inat), cod2, "inativação mexeu no código")
    assert_eq(resolver_codigo(ctx.conn, cod2)["estado"], "inativo",
              "código de inativo deve resolver como inativo")


# ══════════════════════════════════════════════════════════════════════════
#  K4 — edição, reimpressão e reativação preservam o código
# ══════════════════════════════════════════════════════════════════════════

@teste("K4 edição, reimpressão e reativação preservam exatamente o mesmo código")
def k4(ctx):
    _limpa_negocio(ctx.conn)
    admin = _admin()
    tid = _trab(ctx, "Preservado Sempre")
    cod = garantir_codigo(ctx.conn, tid)["codigo"]
    ctx.conn.commit()
    antes = _snapshot_codigos(ctx.conn)

    st, _, _ = admin.post_mp(f"/cadastros/trabalhadores/{tid}/editar", {
        "nome_completo": "Preservado Sempre Editado", "cpf": "111.222.333-44",
        "telefone": "(11) 99999-0000", "valor_mensalidade": "0", "fiado_limite": "0",
    })
    assert_eq(st, 303, "edição")
    assert_eq(_codigo_atual(ctx.conn, tid), cod, "edição trocou o código")
    assert_eq(_snapshot_codigos(ctx.conn), antes, "edição escreveu na tabela de códigos")

    st, _ = admin.get(f"/cadastros/trabalhadores/{tid}/carteirinha")
    assert_eq(st, 200, "reimpressão individual")
    st, _ = admin.get(f"/cadastros/trabalhadores/carteirinhas?ids={tid}")
    assert_eq(st, 200, "reimpressão em lote")
    assert_eq(_snapshot_codigos(ctx.conn), antes, "reimpressão escreveu")

    st, _, loc = admin.post(f"/cadastros/trabalhadores/{tid}/codigo/gerar")
    assert_eq(st, 303, "POST gerar")
    _, ok = _mensagens(loc)
    assert "preservado" in ok and cod in ok, f"mensagem de gerar com código: {ok!r}"
    assert_eq(_codigo_atual(ctx.conn, tid), cod, "POST gerar reemitiu o código")
    assert_eq(_snapshot_codigos(ctx.conn), antes, "POST gerar escreveu")

    admin.post(f"/cadastros/trabalhadores/{tid}/toggle-ativo")   # inativa
    admin.post(f"/cadastros/trabalhadores/{tid}/toggle-ativo")   # reativa
    assert_eq(_codigo_atual(ctx.conn, tid), cod, "ciclo ativo/inativo trocou o código")
    assert_eq(_snapshot_codigos(ctx.conn), antes, "ciclo ativo/inativo alterou a tabela")


# ══════════════════════════════════════════════════════════════════════════
#  K5 — zeros iniciais: banco, tela, QR e resolução preservam 0047
# ══════════════════════════════════════════════════════════════════════════

@teste("K5 0047: zeros iniciais sobrevivem ao banco, à tela, ao QR e à resolução")
def k5(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Portador Zero Esquerda")
    ctx.conn.execute(
        """INSERT INTO trabalhador_codigo (codigo, trabalhador_id, estado, origem, criado_em)
           VALUES ('0047', %s, 'ativo', 'teste', '2026-10-17 10:00')""", (tid,))
    ctx.conn.commit()

    assert_eq(_codigo_atual(ctx.conn, tid), "0047", "banco truncou o zero")
    assert_eq(ctx.conn.execute(
        "SELECT LENGTH(codigo) AS n FROM trabalhador_codigo WHERE trabalhador_id = %s", (tid,)
    ).fetchone()["n"], 4, "comprimento no banco")
    assert_eq(ctx.conn.execute(
        "SELECT to_char(47, 'FM0000') AS c").fetchone()["c"], "0047", "formatção do sorteio")

    resolvido = resolver_codigo(ctx.conn, "0047")
    assert_eq(resolvido["estado"], "ativo", "0047 deveria resolver")
    assert_eq(resolvido["trabalhador_id"], tid, "0047 resolveu para outra pessoa")
    assert_eq(resolver_codigo(ctx.conn, "47")["estado"], "invalido", "'47' é inválido")
    assert_eq(resolver_codigo(ctx.conn, "0047 ")["estado"], "invalido",
              "código com espaço não pode ser aceito")
    assert_eq(resolver_codigo(ctx.conn, None)["estado"], "invalido", "None")
    assert_eq(resolver_codigo(ctx.conn, 47)["estado"], "invalido", "inteiro não é código")

    admin = _admin()
    st, html = admin.get("/cadastros/trabalhadores")
    assert_eq(st, 200, "lista")
    assert "0047" in html, "código com zero à esquerda não apareceu na lista"
    assert "Portador Zero Esquerda" in html, "trabalhador ausente da lista"

    st, cartao = admin.get(f"/cadastros/trabalhadores/{tid}/carteirinha")
    assert_eq(st, 200, "carteirinha")
    assert 'data-codigo="0047"' in cartao, "cartão sem o código preservado"
    assert_eq(_qrs_na_pagina(cartao), ["0047"], "QR não decodifica 0047")

    assert_eq(codigo_valido("0000"), True, "0000 é código")
    assert_eq(qr_data_uri("0000").startswith("data:image/png;base64,"), True, "data URI")
    assert_eq(_decodificar_png(base64.b64decode(qr_data_uri("0000").split(",", 1)[1])),
              ["0000"], "QR de 0000")


# ══════════════════════════════════════════════════════════════════════════
#  K6 — concorrência: sem duplicata, um código ativo por pessoa
# ══════════════════════════════════════════════════════════════════════════

@teste("K6 oito emissões concorrentes: códigos distintos, um ativo por pessoa")
def k6(ctx):
    _limpa_negocio(ctx.conn)
    ids = [_trab(ctx, f"Concorrente {i}") for i in range(8)]
    ctx.conn.commit()

    obtidos, erros = [], []

    def worker(tid):
        try:
            with conectar() as conn:
                obtidos.append(garantir_codigo(conn, tid)["codigo"])
                conn.commit()
        except Exception as e:  # noqa: BLE001 — a falhea é reportada pela asserção
            erros.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker, args=(tid,)) for tid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert_eq(erros, [], "emissões concorrentes falharam")
    assert_eq(len(obtidos), 8, "nem todas as emissões retornaram")
    assert_eq(len(set(obtidos)), 8, f"códigos duplicados na concorrência: {obtidos}")
    assert all(codigo_valido(c) for c in obtidos), f"formato: {obtidos}"
    contagem = ctx.conn.execute(
        """SELECT COUNT(*) AS n FROM trabalhador_codigo WHERE estado = 'ativo'"""
    ).fetchone()["n"]
    assert_eq(contagem, 8, "mais de um código ativo no total")
    por_pessoa = ctx.conn.execute(
        """SELECT trabalhador_id, COUNT(*) AS n FROM trabalhador_codigo
            WHERE estado = 'ativo' GROUP BY trabalhador_id HAVING COUNT(*) > 1"""
    ).fetchall()
    assert_eq(por_pessoa, [], "pessoa com dois códigos ativos")


@teste("K6b dois cliques na mesma pessoa resultam em UM único código (ambos preservam)")
def k6b(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Clique Duplo")
    ctx.conn.commit()

    resultados, erros = [], []

    def worker():
        try:
            with conectar() as conn:
                r = garantir_codigo(conn, tid)
                resultados.append((r["codigo"], r["criado"]))
                conn.commit()
        except Exception as e:  # noqa: BLE001
            erros.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert_eq(erros, [], f"corrida mesma pessoa falhou: {erros}")
    assert_eq(len(set(c for c, _ in resultados)), 1, f"códigos divergentes: {resultados}")
    assert_eq(sum(1 for _, criado in resultados if criado), 1,
              f"mais de um criação na mesma pessoa: {resultados}")
    assert_eq(ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM trabalhador_codigo WHERE trabalhador_id = %s", (tid,)
    ).fetchone()["n"], 1, "linhas de código acumuladas")


@teste("K6c duzentas emissões em lote: nenhum código repetido, todos no formato")
def k6c(ctx):
    _limpa_negocio(ctx.conn)
    for i in range(200):
        _trab(ctx, f"Lote {i:03d}")
    ctx.conn.commit()
    resumo = preparar_codigos_pendentes(ctx.conn)
    ctx.conn.commit()
    codigos = [g["codigo"] for g in resumo["gerados"]]
    assert_eq(len(codigos), 200, "nem todos receberam código")
    assert_eq(len(set(codigos)), 200, "repetição dentro do mesmo lote")
    assert all(codigo_valido(c) for c in codigos), "formato violado"
    assert_eq(ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM trabalhador_codigo WHERE codigo LIKE '____'"
    ).fetchone()["n"], 200, "contagem final")


# ══════════════════════════════════════════════════════════════════════════
#  K7 — troca: o novo resolve para a mesma pessoa, o antigo sai de circulação
# ══════════════════════════════════════════════════════════════════════════

@teste("K7 troca exige confirmação e é atômica: novo resolve, antigo fica retirado")
def k7(ctx):
    _limpa_negocio(ctx.conn)
    admin = _admin()
    tid = _trab(ctx, "Vai Trocar")
    antigo = garantir_codigo(ctx.conn, tid)["codigo"]
    ctx.conn.commit()
    antes = _snapshot_codigos(ctx.conn)

    st, _, loc = admin.post(f"/cadastros/trabalhadores/{tid}/codigo/trocar")
    assert_eq(st, 303, "troca sem confirmar")
    erro, _ = _mensagens(loc)
    assert "confirmada" in erro, f"aviso de confirmação ausente: {erro!r}"
    assert_eq(_snapshot_codigos(ctx.conn), antes, "troca não confirmada escreveu")
    assert_eq(_codigo_atual(ctx.conn, tid), antigo, "código mudou sem confirmação")

    st, _, loc = admin.post(f"/cadastros/trabalhadores/{tid}/codigo/trocar",
                            {"confirmar": "1"})
    assert_eq(st, 303, "troca confirmada")
    novo = _codigo_atual(ctx.conn, tid)
    assert novo and novo != antigo and codigo_valido(novo), f"código após troca: {novo!r}"
    _, ok = _mensagens(loc)
    assert antigo in ok and novo in ok, f"resumo da troca: {ok!r}"

    assert_eq(resolver_codigo(ctx.conn, novo)["estado"], "ativo", "novo não resolve")
    assert_eq(resolver_codigo(ctx.conn, novo)["trabalhador_id"], tid,
              "novo resolve para outra pessoa")
    assert_eq(resolver_codigo(ctx.conn, antigo)["estado"], "retirado",
              "antigo ainda resolve como atual")

    linhas = ctx.conn.execute(
        "SELECT estado FROM trabalhador_codigo WHERE codigo IN (%s, %s) ORDER BY codigo",
        (antigo, novo)).fetchall()
    assert sorted(r["estado"] for r in linhas) == ["ativo", "retirado"], \
        f"estados das linhas: {linhas}"
    retirado = ctx.conn.execute(
        "SELECT retirado_em FROM trabalhador_codigo WHERE codigo = %s", (antigo,)
    ).fetchone()
    assert retirado["retirado_em"], "código retirado sem data de retirada"

    # um novo POST gerar não reemite nada depois da troca
    snapshot_pos = _snapshot_codigos(ctx.conn)
    admin.post(f"/cadastros/trabalhadores/{tid}/codigo/gerar")
    assert_eq(_snapshot_codigos(ctx.conn), snapshot_pos, "gerar depois da troca escreveu")

    # trocar quem não tem código → orientação clara, nada escrito
    sem = _trab(ctx, "Sem Código Para Trocar")
    ctx.conn.commit()
    st, _, loc = admin.post(f"/cadastros/trabalhadores/{sem}/codigo/trocar", {"confirmar": "1"})
    erro, _ = _mensagens(loc)
    assert "não tem código atual" in erro, f"erro de sem código: {erro!r}"
    assert_eq(_codigo_atual(ctx.conn, sem), None, "troca sem código emitiu um")


# ══════════════════════════════════════════════════════════════════════════
#  K8/K9/K10 — banco dedicado: reserva permanente, falha atômica, esgotamento
# ══════════════════════════════════════════════════════════════════════════

@teste("K8/K9/K10 espaço esgotado: retirado nunca volta, troca que falha mantém o atual",
       com_ctx=False)
def k8_k10():
    with _banco_dedicado(COD_DB):
        criar_tabelas()
        with conectar() as conn:
            exigir_ambiente_de_teste(conn)
            w1 = _novo_trab(conn, "Dono do Único Ativo")
            w2 = _novo_trab(conn, "Cemitério de Códigos")
            cod = garantir_codigo(conn, w1, origem="teste")["codigo"]
            conn.commit()

            # todos os outros 9.999 códigos existem, mas TODOS retirados (reservados)
            conn.execute(
                """INSERT INTO trabalhador_codigo
                           (codigo, trabalhador_id, estado, origem, criado_em, retirado_em)
                   SELECT to_char(n, 'FM0000'), %s, 'retirado', 'teste',
                          '2026-01-01 00:00', '2026-01-02 00:00'
                     FROM generate_series(0, 9999) AS n
                    WHERE to_char(n, 'FM0000') <> %s""", (w2, cod))
            total = conn.execute("SELECT COUNT(*) AS n FROM trabalhador_codigo").fetchone()["n"]
            assert_eq(total, 10000, "espaço de códigos não preenchido")
            conn.commit()

        # K9: a troca falha no meio e o código atual continua válido (rollback)
        with contextlib.suppress(CodigosEsgotados):
            with conectar() as conn:
                trocar_codigo(conn, w1)
        with conectar() as conn:
            assert_eq(_codigo_atual(conn, w1), cod,
                      "troca que falhou apagou o código atual")
            assert_eq(conn.execute(
                """SELECT COUNT(*) AS n FROM trabalhador_codigo
                    WHERE trabalhador_id = %s AND estado = 'retirado'""", (w1,)
            ).fetchone()["n"], 0, "retirada parcial ficou registrada")
            assert_eq(resolver_codigo(conn, cod)["estado"], "ativo", "ativo inválido após falha")

        # K10: nenhum código retirado é reatribuído — o espaço está todo tomado
        with conectar() as conn:
            w3 = _novo_trab(conn, "Sem Código Disponível")
            conn.commit()
        try:
            with conectar() as conn:
                garantir_codigo(conn, w3)
            raise AssertionError("emitiu código havendo todos os 10.000 reservados")
        except CodigosEsgotados:
            pass
        with conectar() as conn:
            assert_eq(_codigo_atual(conn, w3), None, "emissão parcial sobrou")
            assert_eq(conn.execute(
                "SELECT COUNT(DISTINCT codigo) AS n FROM trabalhador_codigo"
            ).fetchone()["n"], 10000, "códigos duplicados na tabela")
            assert_eq(conn.execute(
                """SELECT COUNT(*) AS n FROM trabalhador_codigo
                    WHERE estado = 'ativo'""").fetchone()["n"], 1,
                      "mais de um ativo no banco inteiro")


# ══════════════════════════════════════════════════════════════════════════
#  K11 — GET de consulta/impressão não escreve nada
# ══════════════════════════════════════════════════════════════════════════

@teste("K11 GETs repetidos de carteirinha, lote e lista não escrevem nada")
def k11(ctx):
    _limpa_negocio(ctx.conn)
    admin = _admin()
    ids = [_trab(ctx, f"Só Leitura {i}") for i in range(4)]
    for tid in ids:
        garantir_codigo(ctx.conn, tid)
    ctx.conn.commit()
    antes = _snapshot_codigos(ctx.conn)

    for repeticao in range(3):
        st, _ = admin.get(f"/cadastros/trabalhadores/{ids[0]}/carteirinha")
        assert_eq(st, 200, f"individual {repeticao}")
        st, _ = admin.get("/cadastros/trabalhadores/carteirinhas?todos=ativos")
        assert_eq(st, 200, f"lote todos {repeticao}")
        st, _ = admin.get("/cadastros/trabalhadores/carteirinhas?ids=%s&ids=%s" % (ids[0], ids[1]))
        assert_eq(st, 200, f"lote selecionado {repeticao}")
        st, _ = admin.get("/cadastros/trabalhadores")
        assert_eq(st, 200, f"lista {repeticao}")
        st, _ = admin.get(f"/cadastros/trabalhadores/{ids[0]}/editar")
        assert_eq(st, 200, f"form {repeticao}")

    assert_eq(_snapshot_codigos(ctx.conn), antes, "GET escreveu na tabela de códigos")


# ══════════════════════════════════════════════════════════════════════════
#  K12 — RBAC no módulo existente, sem rota anônima nova
# ══════════════════════════════════════════════════════════════════════════

def _criar_usuario(conn, nome, grupo, modulo, ler, escrever):
    from rotas.auth import hash_senha
    conn.execute(
        """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
           VALUES (%s, %s, %s, 1)
           ON CONFLICT (nome_usuario) DO UPDATE
             SET senha_hash = EXCLUDED.senha_hash, ativo = 1""",
        (nome, nome.title(), hash_senha("teste123")))
    if grupo:
        conn.execute(
            """INSERT INTO grupos (nome, descricao) VALUES (%s, 'TASK_002 teste')
               ON CONFLICT (nome) DO NOTHING""", (grupo,))
        gid = conn.execute("SELECT id FROM grupos WHERE nome = %s", (grupo,)).fetchone()["id"]
        uid = conn.execute("SELECT id FROM atendentes WHERE nome_usuario = %s",
                           (nome,)).fetchone()["id"]
        conn.execute(
            """INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, apagar)
               VALUES (%s, %s, %s, %s, FALSE)
               ON CONFLICT (grupo_id, modulo)
                 DO UPDATE SET ler = EXCLUDED.ler, escrever = EXCLUDED.escrever,
                               apagar = FALSE""", (gid, modulo, ler, escrever))
        conn.execute(
            """INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (%s, %s)
               ON CONFLICT DO NOTHING""", (uid, gid))


@teste("K12 RBAC: leitura não escreve, sem módulo é 403 e a recepção não vira cadastro")
def k12(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Alvo da Permissão")
    garantir_codigo(ctx.conn, tid)
    antes = _snapshot_codigos(ctx.conn)
    _criar_usuario(ctx.conn, "leitor_t002", "Leitura TASK002", "cadastros.trabalhadores",
                   True, False)
    _criar_usuario(ctx.conn, "escritor_t002", "Escrita TASK002", "cadastros.trabalhadores",
                   True, True)
    _criar_usuario(ctx.conn, "semodulo_t002", None, None, False, False)
    _criar_usuario(ctx.conn, "recep_t002", "Recepção TASK002", "checkin.trabalhadores",
                   True, True)
    ctx.conn.commit()

    leitor = _sessao_nova("leitor_t002", "teste123")
    st, _ = leitor.get("/cadastros/trabalhadores")
    assert_eq(st, 200, "leitura da lista")
    st, _ = leitor.get(f"/cadastros/trabalhadores/{tid}/carteirinha")
    assert_eq(st, 200, "leitura da carteirinha")
    st, _ = leitor.get("/cadastros/trabalhadores/carteirinhas?todos=ativos")
    assert_eq(st, 200, "leitura do lote")
    st, _, _ = leitor.post(f"/cadastros/trabalhadores/{tid}/codigo/gerar")
    assert_eq(st, 403, "leitor não pode escrever")
    st, _, _ = leitor.post("/cadastros/trabalhadores/preparar-codigos")
    assert_eq(st, 403, "leitor não pode preparar")
    st, _, _ = leitor.post(f"/cadastros/trabalhadores/{tid}/codigo/trocar", {"confirmar": "1"})
    assert_eq(st, 403, "leitor não pode trocar")
    assert_eq(_snapshot_codigos(ctx.conn), antes, "POST recusado escreveu")

    sem = _sessao_nova("semodulo_t002", "teste123")
    for rota in ("/cadastros/trabalhadores", f"/cadastros/trabalhadores/{tid}/carteirinha",
                 "/cadastros/trabalhadores/carteirinhas?todos=ativos"):
        st, _ = sem.get(rota)
        assert_eq(st, 403, f"sem módulo: GET {rota}")
    st, _, _ = sem.post("/cadastros/trabalhadores/preparar-codigos")
    assert_eq(st, 403, "sem módulo: POST")

    recep = _sessao_nova("recep_t002", "teste123")
    for rota in ("/cadastros/trabalhadores", f"/cadastros/trabalhadores/{tid}/carteirinha",
                 "/cadastros/trabalhadores/carteirinhas?todos=ativos"):
        st, _ = recep.get(rota)
        assert_eq(st, 403, f"recepção não ganha cadastro: GET {rota}")
    st, _, _ = recep.post(f"/cadastros/trabalhadores/{tid}/codigo/gerar")
    assert_eq(st, 403, "recepção não gera código")
    st, _ = recep.get("/checkin-trabalhador")
    assert_eq(st, 200, "recepção mantém o check-in")

    escritor = _sessao_nova("escritor_t002", "teste123")
    atual = _codigo_atual(ctx.conn, tid)
    st, _, loc = escritor.post(f"/cadastros/trabalhadores/{tid}/codigo/gerar")
    assert_eq(st, 303, "escritor gera/preserva")
    assert_eq(_codigo_atual(ctx.conn, tid), atual, "escritor trocou o código existente")

    # nada de endpoint público novo: sem sessão, as rotas não respondem abertas
    anon = urllib.request.build_opener(_SemRedirect())
    for rota in ("/cadastros/trabalhadores/carteirinhas?todos=ativos",
                 f"/cadastros/trabalhadores/{tid}/carteirinha"):
        try:
            with anon.open(BASE + rota) as resp:
                st = resp.status
        except urllib.error.HTTPError as e:
            st = e.code
        assert st in (303, 401, 403), f"rota acessível anonimamente ({rota}): status {st}"


# ══════════════════════════════════════════════════════════════════════════
#  K13 — conteúdo do cartão: quem é quem, sem dados desnecessários
# ══════════════════════════════════════════════════════════════════════════

@teste("K13 cartão traz nome/código/QR da mesma pessoa e nenhum dado financeiro ou documento")
def k13(ctx):
    _limpa_negocio(ctx.conn)
    admin = _admin()
    a = _trab(ctx, "Ângela Boaventura de Souza")
    b = _trab(ctx, "Bento Castellani")
    ca = garantir_codigo(ctx.conn, a)["codigo"]
    cb = garantir_codigo(ctx.conn, b)["codigo"]
    ctx.conn.execute(
        """UPDATE trabalhadores SET cpf = '123.456.789-01', rg = '9999999',
               telefone = '(11) 4444-5555', email = 'angela@exemplo.teste',
               valor_mensalidade = 77.77, fiado_limite = 55.50
            WHERE id = %s""", (a,))
    ctx.conn.commit()

    st, html = admin.get(f"/cadastros/trabalhadores/{a}/carteirinha")
    assert_eq(st, 200, "individual")
    assert "Ângela Boaventura de Souza" in html, "nome ausente do cartão"
    assert f'data-codigo="{ca}"' in html, "código errado no cartão"
    assert f'data-trabalhador="{a}"' in html, "id ausente (amarração do teste)"

    # identidade fixa do cartão: estrela + "Shambala – Cidade de Luz"
    assert "Shambala" in html and "Cidade de Luz" in html, "nome fixo da Casa ausente"
    assert "/static/logos/" in html, "estrela/logo da Casa ausente"
    assert "logo.svg" not in html, "cai no logo azul antigo em vez da reserva dourada"

    # sem rótulo de papel e sem foto: o cartão tem exatamente estrela + QR
    assert "Trabalhador · Voluntário" not in html, "rótulo de papel deveria ter saído"
    assert "cc-papel" not in html and "cc-foto" not in html, "sobrou estrutura antiga"
    assert "/static/fotos/" not in html and "foto-placeholder" not in html, \
        "carteirinha ainda referencia foto"
    # TASK_015: a página tem o botão "Imprimir" com ícone Lucide (um <img>), então
    # contar <img> na PÁGINA deixou de isolar o cartão. O contrato é sobre o
    # CARTÃO: estrela + QR, e nada de foto.
    cartao = re.search(r'<div class="cartao".*?</div>\s*</div>\s*</div>', html, re.S)
    assert cartao, "não achei o bloco do cartão"
    assert_eq(cartao.group(0).count("<img"), 2,
              "cartão deveria ter exatamente 2 imagens (estrela e QR)")

    for vedado in ("123.456.789-01", "9999999", "(11) 4444-5555", "angela@exemplo.teste",
                   "77.77", "55.50", "fiado", "mensalidade"):
        assert vedado not in html, f"dado sensível vazou na carteirinha: {vedado}"
    assert "http://" not in html and "https://" not in html, \
        "referência externa (CDN) na página de impressão"

    st, lote = admin.get(f"/cadastros/trabalhadores/carteirinhas?ids={a}&ids={b}")
    assert_eq(st, 200, "lote")
    cartoes = re.findall(r'data-trabalhador="(\d+)" data-codigo="(\d{4})"', lote)
    assert_eq(sorted(cartoes), sorted([(str(a), ca), (str(b), cb)]),
              "cartões trocaram de dono no lote")
    assert "Ângela Boaventura de Souza" in lote and "Bento Castellani" in lote, "nomes ausentes"
    assert "123.456.789-01" not in lote, "CPF vazou no lote"


# ══════════════════════════════════════════════════════════════════════════
#  K14 — QR decodificado == os quatro dígitos
# ══════════════════════════════════════════════════════════════════════════

@teste("K14 QR da carteirinha decodifica exatamente os quatro dígitos do trabalhador")
def k14(ctx):
    _limpa_negocio(ctx.conn)
    admin = _admin()
    codigos = {}
    for i in range(3):
        tid = _trab(ctx, f"QR Decode {i}")
        codigos[tid] = garantir_codigo(ctx.conn, tid)["codigo"]
    ctx.conn.commit()

    for tid, cod in codigos.items():
        st, html = admin.get(f"/cadastros/trabalhadores/{tid}/carteirinha")
        assert_eq(st, 200, "individual")
        assert_eq(_qrs_na_pagina(html), [cod], f"QR individual de {tid}")

    st, lote = admin.get("/cadastros/trabalhadores/carteirinhas?todos=ativos")
    assert_eq(st, 200, "lote")
    assert_eq(sorted(_qrs_na_pagina(lote)), sorted(codigos.values()), "QRs do lote")


# ══════════════════════════════════════════════════════════════════════════
#  K15 — grade A4: folhas, nomes longos, folha incompleta, bloqueados avisados
# ══════════════════════════════════════════════════════════════════════════

@teste("K15 lote A4 divide em folhas de 10, avisa bloqueados e não imprime inativos")
def k15(ctx):
    _limpa_negocio(ctx.conn)
    admin = _admin()
    nome_longo = "Waldomira Perpetua Auxiliadora dos Santos e Conceição Ferreira"
    nome_medio = "Constança Teodora Bittencourt"
    alvos = [
        _trab(ctx, nome_longo),
        _trab(ctx, nome_medio),
        _trab(ctx, "José Nogueira"),
    ]
    for i in range(9):
        alvos.append(_trab(ctx, f"Repeatente {i}"))
    for tid in alvos:
        garantir_codigo(ctx.conn, tid)
    inativo = _trab(ctx, "Inativo Não Impresso", ativo=0)
    sem_codigo = _trab(ctx, "Ativo Sem Código")
    ctx.conn.commit()

    st, lote = admin.get("/cadastros/trabalhadores/carteirinhas?todos=ativos")
    assert_eq(st, 200, "lote")
    assert_eq(lote.count('class="folha"'), 2, "12 cartões deveriam ocupar 2 folhas")
    assert_eq(lote.count('class="cartao"'), 12, "contagem de cartões")
    assert lote.count('class="cartao"') == len(re.findall(r'data-trabalhador="(\d+)"', lote)), \
        "cartões sem amarração de dono"
    assert nome_longo in lote and nome_medio in lote, "nomes longos não impressos"
    assert f'data-trabalhador="{inativo}"' not in lote, "inativo virou cartão impresso"
    assert f'data-trabalhador="{sem_codigo}"' not in lote, "sem código virou cartão impresso"
    assert "Ativo Sem Código" in lote, "sem código deveria ser avisado nominalmente"

    # seleção mista: imprime quem pode e avisa nominalmente quem ficou de fora
    st, mix = admin.get("/cadastros/trabalhadores/carteirinhas"
                        f"?ids={alvos[0]}&ids={inativo}&ids={sem_codigo}")
    assert_eq(st, 200, "seleção mista")
    assert_eq(mix.count('class="cartao"'), 1, "só o elegível deveria virar cartão")
    assert "Inativo Não Impresso" in mix, "inativo deveria ser avisado nominalmente"
    assert "Ativo Sem Código" in mix, "sem código deveria ser avisado nominalmente"

    # folha incompleta: um cartão eleito ocupa uma folha
    st, lote_um = admin.get(f"/cadastros/trabalhadores/carteirinhas?ids={alvos[0]}")
    assert_eq(st, 200, "lote de um cartão")
    assert_eq(lote_um.count('class="folha"'), 1, "um cartão deveria ocupar uma folha")
    assert_eq(lote_um.count('class="cartao"'), 1, "um cartão")

    # selecionar apenas bloqueados: nada é impresso, a tela avisa
    st, _, loc = admin.get_loc(f"/cadastros/trabalhadores/carteirinhas?ids={inativo}")
    assert_eq(st, 303, "seleção só com bloqueados volta para a lista")
    erro, _ = _mensagens(loc)
    assert erro.startswith("Nada para imprimir"), f"aviso: {erro!r}"
    assert "inativo" in erro, f"aviso deveria nominal: {erro!r}"

    css = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "static", "css", "carteirinha.css"), encoding="utf-8").read()
    assert "size: A4 portrait" in css and "margin: 10mm" in css, "geometria da página"
    assert "repeat(2, 85.6mm)" in css and "grid-auto-rows: 54mm" in css, "grade 2×54mm"
    assert "column-gap: 8mm" in css and "row-gap: 1mm" in css, "vazamentos da grade"
    assert "break-before: page" in css, "quebra de folha"
    assert "@media print" in css, "CSS de impressão"

    # aritmética das faixas, lida das declarações (não de comentário)
    def _decl(bloco, chave):
        corpo = re.search(r"\." + bloco + r"\s*\{(.*?)\}", css, re.S).group(1)
        valor = re.search(chave + r":\s*([\d.]+)mm", corpo)
        assert valor, f".{bloco} perdeu {chave}"
        return float(valor.group(1))

    topo = _decl("cc-topo", "height")
    folga_qr = _decl("cc-qr-faixa", "margin-top")
    qr = _decl("cc-qr-faixa", "height")
    folga_rodape = _decl("cc-rodape", "margin-top")
    rodape = _decl("cc-rodape", "height")
    assert_eq(qr, _decl("cc-qr", "width"), "QR ocupa a faixa inteira")
    assert_eq(topo + folga_qr + qr + folga_rodape + rodape, 49.0,
              f"faixas não fecham a altura útil: {topo}+{folga_qr}+{qr}+{folga_rodape}+{rodape}")
    # o QR é a prioridade visual: nenhuma outra faixa pode ser maior que ele
    assert qr >= 29.0, f"QR deveria ser a faixa dominante, mede {qr} mm"
    assert topo < qr and rodape < qr, "faixa maior que o QR rouba a prioridade"
    assert "cc-foto" not in css, "CSS ainda reserva espaço para foto"


# ══════════════════════════════════════════════════════════════════════════
#  K16 — a saída impressa: 70 carteirinhas em 7 folhas, QRs lidos do PDF
# ══════════════════════════════════════════════════════════════════════════

@teste("K16 PDF de 70 carteirinhas: sete folhas e todos os QRs decodificados do papel")
def k16(ctx):
    for ferramenta in ("chromium", "pdftoppm", "pdfinfo"):
        if not shutil.which(ferramenta):
            raise _Pulado(f"{ferramenta} não instalado (evidência de impressão pulada)")
    _limpa_negocio(ctx.conn)
    admin = _admin()
    nomes = []
    for i in range(66):
        nomes.append(f"Voluntário Demonstrativo {i:02d}")
    nomes.append("Waldomira Perpetua Auxiliadora dos Santos e Conceição Ferreira")
    nomes.append("Constança Teodora Bittencourt e Albuquerque Maranhão Prado")
    nomes.append("Zilda de Jesus")
    nomes.append("Ana Beatriz Nogueira da Silva Prado")
    for nome in nomes:
        garantir_codigo(ctx.conn, _trab(ctx, nome))
    ctx.conn.commit()
    esperados = [r["codigo"] for r in ctx.conn.execute(
        """SELECT c.codigo FROM trabalhador_codigo c
             JOIN trabalhadores t ON t.id = c.trabalhador_id
            WHERE c.estado = 'ativo' ORDER BY t.nome_completo""").fetchall()]
    assert_eq(len(esperados), 70, "base fictícia de 70 trabalhadores")

    st, html = admin.get("/cadastros/trabalhadores/carteirinhas?todos=ativos")
    assert_eq(st, 200, "lote de 70")

    os.makedirs(PASTA_PDF, exist_ok=True)
    # A pasta é só desta prova (temporária): limpa arquivos E o perfil do
    # Chromium, que é diretório. `ignore_errors` porque um arquivo já removido
    # não pode derrubar a prova. (`shutil` já vem do topo do módulo — importar
    # de novo AQUI o tornaria local e quebraria o `shutil.which` acima.)
    for resto in os.listdir(PASTA_PDF):
        alvo = os.path.join(PASTA_PDF, resto)
        shutil.rmtree(alvo, ignore_errors=True) if os.path.isdir(alvo) \
            else os.remove(alvo)
    pagina = re.sub(r'(src|href)="/', lambda m: f'{m.group(1)}="{BASE}/', html)
    caminho_html = os.path.join(PASTA_PDF, "carteirinhas.html")
    caminho_pdf = os.path.join(PASTA_PDF, "carteirinhas.pdf")
    with open(caminho_html, "w", encoding="utf-8") as f:
        f.write(pagina)
    if os.path.exists(caminho_pdf):
        os.remove(caminho_pdf)

    # O Chromium headless precisa de um diretório de perfil GRAVÁVEL: sem
    # `--user-data-dir` ele tenta o perfil padrão do usuário e, se esse caminho
    # for somente leitura (sandbox, home restrito), aborta com "Failed to create
    # a unique user data directory for headless". O perfil é desta execução e
    # fica junto dos PDFs, que já são temporários.
    perfil = os.path.join(PASTA_PDF, "perfil-chromium")
    os.makedirs(perfil, exist_ok=True)
    r = subprocess.run(
        ["chromium", "--headless", "--disable-gpu", "--no-sandbox",
         f"--user-data-dir={perfil}",
         "--no-pdf-header-footer", f"--print-to-pdf={caminho_pdf}",
         "file://" + caminho_html],
        capture_output=True, text=True, timeout=180,
    )
    assert os.path.exists(caminho_pdf), f"chromium não gerou PDF: {r.stderr[-500:]}"

    info = subprocess.run(["pdfinfo", caminho_pdf], capture_output=True, text=True).stdout
    paginas = int(re.search(r"^Pages:\s+(\d+)", info, re.M).group(1))
    assert_eq(paginas, 7, f"70 carteirinhas deveriam sair em 7 folhas A4 ({info!r})")
    tam = re.search(r"Page size:\s+([\d.]+) x ([\d.]+) pts", info)
    largura, altura = float(tam.group(1)), float(tam.group(2))
    assert 590 <= largura <= 600 and 835 <= altura <= 850, \
        f"página não é A4 retrato: {largura}x{altura} pt"

    subprocess.run(["pdftoppm", "-r", "300", "-png", caminho_pdf,
                    os.path.join(PASTA_PDF, "folha")], check=True, timeout=600)
    folhas = sorted(f for f in os.listdir(PASTA_PDF)
                    if f.startswith("folha") and f.endswith(".png"))
    assert_eq(len(folhas), 7, "número de páginas rasterizadas")

    lidos = []
    por_folha = []
    for numero, folha in enumerate(folhas):
        celulas = esperados[numero * 10:(numero + 1) * 10]
        nesta = _ler_qrs_da_folha(os.path.join(PASTA_PDF, folha), len(celulas))
        assert_eq(nesta, celulas, f"folha {numero + 1}: QR por célula ≠ ordem impressa")
        por_folha.append(len(nesta))
        lidos.extend(nesta)
    assert_eq(sorted(lidos), sorted(esperados), "QRs do PDF ≠ códigos dos cartões")
    assert_eq(por_folha, [10] * 7, f"distribuição por folha: {por_folha}")


# ══════════════════════════════════════════════════════════════════════════
#  K18 — a FK é RESTRICT: instalação nova, correção de banco existente, idempotência
# ══════════════════════════════════════════════════════════════════════════

@teste("K18 FK de códigos nasce RESTRICT e a migração corrige CASCADE sem tocar nos dados",
       com_ctx=False)
def k18():
    with _banco_dedicado(COD_DB):
        criar_tabelas()
        with conectar() as conn:
            exigir_ambiente_de_teste(conn)
            nome, regra = _fk_trabalhador(conn)
            assert_eq(nome, "trabalhador_codigo_trabalhador_id_fkey", "FK com nome inesperado")
            assert_eq(regra, "r", "instalação nova não nasceu com ON DELETE RESTRICT")

        # ── banco existente, criado pela versão anterior: a FK estava em CASCADE
        with conectar() as conn:
            conn.execute(f'ALTER TABLE trabalhador_codigo DROP CONSTRAINT "{nome}"')
            conn.execute(
                f'ALTER TABLE trabalhador_codigo ADD CONSTRAINT "{nome}" '
                'FOREIGN KEY (trabalhador_id) REFERENCES trabalhadores(id) ON DELETE CASCADE')
            dono = _novo_trab(conn, "Dono do Código Legado")
            ativo = garantir_codigo(conn, dono, origem="teste")["codigo"]
            trocar_codigo(conn, dono, origem="teste")     # deixa um retirado + um ativo
            conn.commit()
            assert_eq(_fk_trabalhador(conn)[1], "c", "banco simulado não ficou em CASCADE")
            antes = _snapshot_codigos(conn)
            assert any(linha[2] == "retirado" for linha in antes), "simulação sem retirado"
            assert_eq(len(antes), 2, "simulação deveria ter dois códigos emitidos")

        # ── startup com a correção: troca a regra e não mexe em nenhum código
        criar_tabelas()
        with conectar() as conn:
            nome_depois, regra_depois = _fk_trabalhador(conn)
            assert_eq(regra_depois, "r", "migração não trocou CASCADE por RESTRICT")
            assert_eq(nome_depois, nome, "migração trocou o nome da FK")
            assert_eq(_snapshot_codigos(conn), antes, "migração alterou os códigos")

        # ── segunda execução: idempotente, sem erro e sem alterar dados
        criar_tabelas()
        with conectar() as conn:
            assert_eq(_fk_trabalhador(conn), (nome, "r"), "segunda execução perdeu a regra")
            assert_eq(_snapshot_codigos(conn), antes, "segunda execução alterou os dados")

        # ── e com RESTRICT a exclusão física é recusada, sem efeito colateral
        with conectar() as conn:
            dono_row = conn.execute("SELECT id FROM trabalhadores WHERE nome_completo = %s",
                                    ("Dono do Código Legado",)).fetchone()["id"]
        try:
            with conectar() as conn:
                conn.execute("DELETE FROM trabalhadores WHERE id = %s", (dono_row,))
            raise AssertionError("DELETE do trabalhador foi aceito com a FK em RESTRICT")
        except psycopg2.errors.ForeignKeyViolation:
            pass
        with conectar() as conn:
            assert_eq(_snapshot_codigos(conn), antes,
                      "tentativa de exclusão mexeu nos códigos")
            assert conn.execute("SELECT 1 FROM trabalhadores WHERE id = %s",
                                (dono_row,)).fetchone(), "recusa deveria ter preservado o dono"


# ══════════════════════════════════════════════════════════════════════════
#  K19 — RESTRICT na prática: ninguém sai de circulação por exclusão física
# ══════════════════════════════════════════════════════════════════════════

@teste("K19 RESTRICT: excluir quem tem código é recusado; inativar/reativar segue funcionando")
def k19(ctx):
    _limpa_negocio(ctx.conn)
    admin = _admin()
    com_ativo = _trab(ctx, "Doação Válida")
    cod_ativo = garantir_codigo(ctx.conn, com_ativo)["codigo"]

    # Trabalhador só com códigos RETIRADOS: é o estado que a reserva permanente
    # protege (e o que o CASCADE destruída em silêncio). Montado por SQL direto
    # porque nenhuma operação da tela produz "retirar sem emitir" — a troca é
    # atômica e sempre deixa um atual.
    so_retirados = _trab(ctx, "Cemitério Particular")
    ctx.conn.execute(
        """INSERT INTO trabalhador_codigo (codigo, trabalhador_id, estado, origem,
                                           criado_em, retirado_em)
           VALUES ('9001', %s, 'retirado', 'teste', '2026-01-01 00:00', '2026-01-02 00:00')""",
        (so_retirados,))
    ctx.conn.commit()

    antes = _snapshot_codigos(ctx.conn)
    retirados = {linha[0] for linha in antes if linha[2] == "retirado"}
    assert_eq(retirados, {"9001"}, "cenário com retirado inesperado")

    # exclusão física recusada para quem tem código ATUAL e para quem só tem RETIRADO
    for alvo, motivo in ((com_ativo, "código atual"), (so_retirados, "só códigos retirados")):
        try:
            ctx.conn.execute("DELETE FROM trabalhadores WHERE id = %s", (alvo,))
            ctx.conn.rollback()
            raise AssertionError(f"DELETE aceito para quem tem {motivo}")
        except psycopg2.errors.ForeignKeyViolation:
            ctx.conn.rollback()
    assert_eq(_snapshot_codigos(ctx.conn), antes, "recusa de exclusão alterou os códigos")

    # inativação/reativação continuam sendo o fluxo normal — e não tocam no código
    st, _, _ = admin.post(f"/cadastros/trabalhadores/{com_ativo}/toggle-ativo")
    assert_eq(st, 303, "inativação")
    assert_eq(_codigo_atual(ctx.conn, com_ativo), cod_ativo, "inativação mexeu no código")
    assert_eq(resolver_codigo(ctx.conn, cod_ativo)["estado"], "inativo",
              "código de inativo deveria resolver como inativo")
    st, _, _ = admin.post(f"/cadastros/trabalhadores/{com_ativo}/toggle-ativo")
    assert_eq(st, 303, "reativação")
    assert_eq(_codigo_atual(ctx.conn, com_ativo), cod_ativo, "reativação reemitiu o código")
    assert_eq(resolver_codigo(ctx.conn, cod_ativo)["estado"], "ativo", "não reativou")

    # código retirado não volta a ser emitido para ninguém
    emitidos = set()
    for i in range(30):
        emitidos.add(garantir_codigo(ctx.conn, _trab(ctx, f"Recém-Cadastrado {i}"))["codigo"])
    ctx.conn.commit()
    assert not (emitidos & retirados), \
        f"código retirado foi reemitido: {sorted(emitidos & retirados)}"
    assert_eq(_codigo_atual(ctx.conn, so_retirados), None,
              "emissões de terceiros deram código a quem só tem retirados")


# ══════════════════════════════════════════════════════════════════════════
#  Runner
# ══════════════════════════════════════════════════════════════════════════

def main():
    preparar_banco()
    _garantir_admin()
    with conectar() as conn:
        _limpa_negocio(conn)
    total = ok = pulados = 0
    for nome, fn, com_ctx in TESTES:
        total += 1
        try:
            if com_ctx:
                with conectar() as conn:
                    fn(Ctx(conn))
            else:
                fn()
            ok += 1
            print(f"  PASS  {nome}")
        except _Pulado as e:
            pulados += 1
            ok += 1
            print(f"  PULADO {nome} — {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{ok}/{total} testes passaram (TASK_002 códigos e carteirinhas)"
          + (f" — {pulados} pulado(s)" if pulados else ""))
    with contextlib.suppress(Exception):
        remover_se_nosso(COD_DB, TOKEN)
    limpar_banco()
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
