#!/usr/bin/env python3
"""Suíte TASK_003 — terminal de presença por QR e código (sessão + backend).

Cobre a validação obrigatória da etapa 3 que é verificável sem aparelho:

- sessão restrita de terminal: emitida por operador com `checkin.trabalhadores`
  (escrever), encerra a sessão comum DAQUELE navegador, não alcança nenhuma
  outra rota, vale um dia inteiro e expira sem deixar dado pessoal vazar;
- um único fluxo de chegada para QR e teclado, código de quatro caracteres
  ASCII com zeros iniciais preservados (inclusive `0000`);
- código atual / retirado / inexistente / malformado e trabalhador inativo, com
  as escritas coerentes e sem dado pessoal nas recusas;
- chegada sem dia de atendimento, com o dia aberto, com o dia fechado e fora da
  agenda — o atendimento nunca é criado nem alterado;
- repetição, concorrência com a recepção, resposta perdida depois do commit,
  ausência convertida em chegada, troca de código e inativação concorrentes;
- `origem_chegada` com migração idempotente que não inventa origem antiga;
- data e hora medidas pelo servidor em America/Sao_Paulo;
- resposta mínima: nome, foto, data e horário — nunca CPF, telefone, e-mail,
  mensalidade ou fiado.

O leitor de QR visto pelo navegador com câmera simulada está em
tests/test_terminal_leitor.py.

Uso (recomendado): bash tests/rodar_testes.sh
"""
import os
import sys
import json
import time
import threading
import traceback
import contextlib
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

# ── TRAVA DE ISOLAMENTO (antes de importar a aplicação — ordem obrigatória) ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import exigir_ambiente_de_teste  # noqa: E402

from test_tres_passes import (  # noqa: E402
    Ctx, assert_eq, preparar_banco, limpar_banco, _garantir_admin, BASE, TOKEN,
)
from test_codigo_trabalhador import (  # noqa: E402
    _banco_dedicado, _limpa_negocio, _criar_usuario, _trab, _psql_admin, _Sessao,
    _sessao_nova,
)
from preparo_descartavel import remover_se_nosso  # noqa: E402

from banco import conectar, criar_tabelas  # noqa: E402
from servico_codigo_trabalhador import trocar_codigo, resolver_codigo  # noqa: E402
from servico_chegada import registrar_chegada_por_codigo  # noqa: E402
from servico_presenca import (  # noqa: E402
    registrar_chegada, registrar_ausencia_explicita, OrigemInvalida,
)

MIG_DB = "shambala_terminal_mig_test"
TESTES = []


def teste(nome, com_ctx=True):
    def wrap(fn):
        TESTES.append((nome, fn, com_ctx))
        return fn
    return wrap


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _codigo_atual(conn, tid):
    row = conn.execute(
        """SELECT codigo FROM trabalhador_codigo
            WHERE trabalhador_id = %s AND estado = 'ativo'""", (tid,)).fetchone()
    return row["codigo"] if row else None


def _forcar_codigo(conn, tid, codigo):
    """Dá à pessoa exatamente o código querido (o sorteio é do serviço).

    Só move o código da própria pessoa e nunca reatribui código de terceiro: a
    reserva permanente dos retirados e o índice único continuam valendo.
    """
    atual = _codigo_atual(conn, tid)
    if atual == codigo:
        return codigo
    if atual:
        conn.execute(
            """UPDATE trabalhador_codigo
                  SET estado = 'retirado', retirado_em = '2026-10-23 00:00'
                WHERE codigo = %s AND estado = 'ativo'""", (atual,))
    conn.execute(
        """INSERT INTO trabalhador_codigo
                   (codigo, trabalhador_id, estado, origem, criado_em)
           VALUES (%s, %s, 'ativo', 'teste', '2026-10-23 00:00')""", (codigo, tid))
    return codigo


def _presencas(conn, tid):
    return conn.execute(
        """SELECT id, presente, hora_chegada, data::text AS data, dia_trabalho_id,
                  origem_chegada
             FROM trabalhador_presenca WHERE trabalhador_id = %s ORDER BY data, id""",
        (tid,)).fetchall()


def _hoje_sp(conn):
    return conn.execute(
        "SELECT ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date)::text AS d"
    ).fetchone()["d"]


# ── Clientes HTTP ────────────────────────────────────────────────────────────

class _SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER_LOJO = urllib.request.build_opener(_SemRedirect())


def _sem_cookie(path, method="GET", corpo=None, cookie=None, content_type=None):
    """Requisição sem cookie jar: anônima, ou com um cookie forjado à mão."""
    r = urllib.request.Request(BASE + path,
                               data=corpo.encode() if isinstance(corpo, str) else corpo,
                               method=method)
    if content_type:
        r.add_header("Content-Type", content_type)
    if cookie:
        r.add_header("Cookie", cookie)
    try:
        with _OPENER_LOJO.open(r) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class ClienteTerminal(_Sessao):
    """Operador que ativou o terminal: o mesmo frasco de cookies fica só com a
    sessão restrita."""

    def ativar(self):
        return self.post("/terminal/ativar")

    def encerrar(self):
        return self.post("/terminal/encerrar")

    def chegada(self, corpo, content_type="application/json"):
        """POST /terminal/chegada → (status, JSON ou None, texto cru)."""
        dados = corpo if isinstance(corpo, str) else json.dumps(corpo)
        status, texto, _ = self._responder(urllib.request.Request(
            BASE + "/terminal/chegada", data=dados.encode(), method="POST",
            headers={"Content-Type": content_type}))
        with contextlib.suppress(ValueError):
            return status, json.loads(texto), texto
        return status, None, texto


def _terminal(usuario, senha):
    cliente = ClienteTerminal(usuario, senha)
    status, _, local = cliente.ativar()
    assert_eq(status, 303, f"ativação por {usuario} deveria redirecionar")
    assert_eq(local, BASE + "/terminal/" if local and local.startswith("http") else "/terminal/",
              "destino da ativação")
    return cliente


# ══════════════════════════════════════════════════════════════════════════
#  T1 — ativação e encerramento da sessão restrita
# ══════════════════════════════════════════════════════════════════════════

@teste("T1 ativação emite a sessão restrita e encerra a sessão comum daquele navegador")
def t1(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Trabalhador da Ativação")
    _forcar_codigo(ctx.conn, tid, "1101")
    ctx.conn.commit()

    aparelho = _terminal("admin", "teste123")

    status, pagina = aparelho.get("/terminal/")
    assert_eq(status, 200, "tela do terminal com sessão válida")
    assert "Apresente sua carteirinha" in pagina, "tela sem o convite"
    assert "/menu" not in pagina and "Sair" not in pagina, "a tela trouxe navegação geral"

    st, dados, _ = aparelho.chegada({"codigo": "1101", "origem": "terminal_qr"})
    assert_eq(st, 200, "chegada com a sessão de terminal")
    assert_eq(dados["resultado"], "registrada", "a chegada não registrou")

    # A sessão comum daquele navegador deixou de existir no servidor.
    for rota in ("/menu", "/checkin-trabalhador", "/cadastros/trabalhadores"):
        st, _ = aparelho.get(rota)
        assert_eq(st, 303, f"sessão comum deveria ter acabado em {rota}")

    # Outros aparelhos do mesmo operador seguem funcionando.
    outro = _sessao_nova("admin", "teste123")
    st, _ = outro.get("/menu")
    assert_eq(st, 200, "sessões de outros aparelhos foram derrubadas")


@teste("T1b encerrar derruba só o aparelho que encerrou")
def t1b(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Trabalhador do Encerramento")
    _forcar_codigo(ctx.conn, tid, "1102")
    ctx.conn.commit()

    encerrado = _terminal("admin", "teste123")
    vizinho = _terminal("admin", "teste123")

    st, _, _ = encerrado.encerrar()
    assert_eq(st, 303, "encerramento")
    st, _ = encerrado.get("/terminal/")
    assert_eq(st, 303, "a tela deveria voltar ao login depois de encerrar")
    st, dados, _ = encerrado.chegada({"codigo": "1102", "origem": "terminal_codigo"})
    assert_eq(st, 401, "escrita depois de encerrar")
    assert_eq(dados["resultado"], "sessao_encerrada", "corpo da recusa")
    assert_eq(_presencas(ctx.conn, tid), [], "escreveu presença após encerrar")

    st, dados, _ = vizinho.chegada({"codigo": "1102", "origem": "terminal_codigo"})
    assert_eq(st, 200, "outro aparelho foi derrubado junto")
    assert_eq(dados["resultado"], "registrada", "chegada no aparelho vizinho")


@teste("T1a ativação recusada sem permissão de escrita e para anônimo")
def t1a(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Trabalhador da Recusa")
    _forcar_codigo(ctx.conn, tid, "1103")
    _criar_usuario(ctx.conn, "leitor_t003", "Leitura TASK003", "checkin.trabalhadores",
                   True, False)
    _criar_usuario(ctx.conn, "semodulo_t003", None, None, False, False)
    ctx.conn.commit()

    st, corpo = _sem_cookie("/terminal/ativar", method="POST", corpo=b"")
    assert_eq(st, 303, "ativação anônima")
    st, _ = _sem_cookie("/terminal/")
    assert_eq(st, 303, "tela do terminal para anônimo")
    st, corpo = _sem_cookie("/terminal/chegada", method="POST",
                            corpo='{"codigo":"1103","origem":"terminal_qr"}',
                            content_type="application/json")
    assert_eq(st, 401, "chegada anônima")
    assert_eq(json.loads(corpo)["nome"], None, "chegada anônima devolveu nome")

    for usuario in ("leitor_t003", "semodulo_t003"):
        s = ClienteTerminal(usuario, "teste123")
        st, corpo, _ = s.post("/terminal/ativar")
        assert_eq(st, 403, f"ativação de {usuario} deveria ser negada")
        assert "Acesso negado" in corpo, corpo[:160]
        st, dados, _ = s.chegada({"codigo": "1103", "origem": "terminal_qr"})
        assert_eq(st, 401, f"{usuario} escreveu sem sessão de terminal")
    assert_eq(_presencas(ctx.conn, tid), [], "houve escrita nas recusas de ativação")


# ══════════════════════════════════════════════════════════════════════════
#  T2 — sessão expirada e verificação em cada requisição
# ══════════════════════════════════════════════════════════════════════════

@teste("T2 token forjado e sessão vencida: nada de dado pessoal, nada de escrita")
def t2(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Trabalhador Forjado")
    codigo = _forcar_codigo(ctx.conn, tid, "1104")
    ctx.conn.execute(
        """UPDATE trabalhadores SET foto_trabalhador = 'trab_%s_ab12.jpg',
                  cpf = '123.456.789-00', telefone = '11 99999-0000'
            WHERE id = %s""", (tid, tid))
    ctx.conn.commit()

    st, corpo = _sem_cookie("/terminal/chegada", method="POST",
                            corpo=json.dumps({"codigo": codigo, "origem": "terminal_qr"}),
                            content_type="application/json",
                            cookie="sessao_terminal=" + "a" * 64)
    assert_eq(st, 401, "token com a forma certa, mas nunca emitido")
    assert "Trabalhador Forjado" not in corpo and "trab_" not in corpo, corpo[:200]
    st, _ = _sem_cookie("/terminal/", cookie="sessao_terminal=" + "a" * 64)
    assert_eq(st, 303, "token forjado abriu a tela")
    assert_eq(_presencas(ctx.conn, tid), [], "token forjado escreveu presença")

    _prazo_da_sessao_de_terminal()


def _prazo_da_sessao_de_terminal():
    """O prazo de um dia é o que o app verifica em cada requisição."""
    from rotas import terminal as mod

    with conectar() as conn:
        atendente_id = conn.execute(
            "SELECT id FROM atendentes WHERE nome_usuario = 'admin'").fetchone()["id"]

    class Pedido:
        def __init__(self, cookies):
            self.cookies = cookies

    def vale(token):
        return mod.obter_terminal_logado(Pedido({"sessao_terminal": token}))

    token = mod._criar_sessao_terminal(atendente_id)
    assert vale(token), "sessão recém-criada não vale"

    mod._sessoes_terminal[token] = (atendente_id, time.time() - mod._TTL + 60)
    assert vale(token), "sessão dentro do dia inteiro de operação não vale"

    mod._sessoes_terminal[token] = (atendente_id, time.time() - mod._TTL - 1)
    assert_eq(vale(token), None, "sessão vencida ainda autoriza")
    assert_eq(token in mod._sessoes_terminal, False, "sessão vencida não foi retirada")

    # Quem ativou deixou de poder: inativado o operador, o terminal cai e a
    # sessão é destruída — reativá-lo não ressuscita o aparelho (seria preciso
    # uma nova ativação, feita por alguém autenticado no aparelho).
    token2 = mod._criar_sessao_terminal(atendente_id)
    with conectar() as conn:
        conn.execute("UPDATE atendentes SET ativo = 0 WHERE id = %s", (atendente_id,))
    assert_eq(vale(token2), None, "operador inativado manteve o terminal de pé")
    assert_eq(token2 in mod._sessoes_terminal, False, "sessão revogada ficou na memória")
    with conectar() as conn:
        conn.execute("UPDATE atendentes SET ativo = 1 WHERE id = %s", (atendente_id,))
    assert_eq(vale(token2), None, "sessão revogada voltou a valer sem nova ativação")


# ══════════════════════════════════════════════════════════════════════════
#  T3 — a sessão do terminal não herda nada
# ══════════════════════════════════════════════════════════════════════════

@teste("T3 terminal não alcança cadastro, carteirinha, financeiro, menu nem relatórios")
def t3(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Trabalhador do Vazamento")
    codigo = _forcar_codigo(ctx.conn, tid, "1105")
    ctx.conn.commit()

    aparelho = _terminal("admin", "teste123")
    rotas = [
        "/menu", "/cadastros/trabalhadores", f"/cadastros/trabalhadores/{tid}/editar",
        f"/cadastros/trabalhadores/{tid}/carteirinha",
        f"/cadastros/trabalhadores/carteirinhas?ids={tid}",
        f"/cadastros/trabalhadores/{tid}/presenca", f"/cadastros/trabalhadores/{tid}/agenda",
        "/checkin-trabalhador", "/financeiro", "/financeiro/mensalidades", "/relatorios",
        "/dia", "/cadastros/pessoas", "/caixa/pdv", "/configuracoes", "/cadastros/usuarios",
    ]
    for rota in rotas:
        st, corpo = aparelho.get(rota)
        assert st in (303, 403), f"terminal alcançou {rota} (status {st})"
        assert "mensalidade" not in corpo.lower(), f"{rota} devolveu conteúdo financeiro"

    st, _, _ = aparelho.post(f"/checkin-trabalhador/{tid}/presente")
    assert st in (303, 403), "terminal escreveu pelo check-in da recepção"
    st, _, _ = aparelho.post(f"/cadastros/trabalhadores/{tid}/codigo/trocar",
                             {"confirmar": "1"})
    assert st in (303, 403), "terminal trocou código pelo cadastro"

    st, dados, _ = aparelho.chegada({"codigo": codigo, "origem": "terminal_qr"})
    assert_eq(dados["resultado"], "registrada", "o fluxo próprio do terminal quebrou")


# ══════════════════════════════════════════════════════════════════════════
#  T4 — QR e teclado pelo mesmo caminho, zeros iniciais
# ══════════════════════════════════════════════════════════════════════════

@teste("T4 QR e teclado identificam a mesma pessoa; zeros iniciais não viram inteiro")
def t4(ctx):
    _limpa_negocio(ctx.conn)
    pessoas = []
    for codigo in ("0047", "0000", "0001"):
        tid = _trab(ctx, f"Trabalhador {codigo}")
        _forcar_codigo(ctx.conn, tid, codigo)
        pessoas.append((tid, codigo))
    ctx.conn.commit()

    apareilho_qr = _terminal("admin", "teste123")
    apareilho_teclado = _terminal("admin", "teste123")

    for i, (tid, codigo) in enumerate(pessoas):
        origem = "terminal_qr" if i % 2 == 0 else "terminal_codigo"
        aparelho = apareilho_qr if origem == "terminal_qr" else apareilho_teclado
        st, dados, _ = aparelho.chegada({"codigo": codigo, "origem": origem})
        assert_eq(st, 200, f"chegada {codigo}")
        assert_eq(dados["resultado"], "registrada", f"resultado de {codigo}")
        assert_eq(dados["nome"], f"Trabalhador {codigo}", f"nome de {codigo}")
        assert_eq(dados["data"], _hoje_sp(ctx.conn), f"data de {codigo}")

        linhas = _presencas(ctx.conn, tid)
        assert_eq(len(linhas), 1, f"{codigo} não gerou exatamente uma presença")
        assert_eq(linhas[0]["origem_chegada"], origem, f"origem de {codigo}")
        assert_eq(_codigo_atual(ctx.conn, tid), codigo, "zeros iniciais alterados no banco")

    # Os mesmos quatro caracteres pelos dois caminhos são a mesma pessoa.
    tid, codigo = pessoas[0]
    st, dados, _ = apareilho_teclado.chegada({"codigo": codigo,
                                             "origem": "terminal_codigo"})
    assert_eq(dados["resultado"], "ja_registrada", "segundo caminho achou outra pessoa")
    assert_eq(dados["nome"], "Trabalhador 0047", "nome trocado entre caminhos")
    assert_eq(len(_presencas(ctx.conn, tid)), 1, "os dois caminhos duplicaram a presença")


# ══════════════════════════════════════════════════════════════════════════
#  T5 — estados do código
# ══════════════════════════════════════════════════════════════════════════

@teste("T5 atual, retirado, inexistente, malformado e inativo: mensagens e escritas")
def t5(ctx):
    _limpa_negocio(ctx.conn)
    ativo = _trab(ctx, "Ativo do Terminal")
    cod_ativo = _forcar_codigo(ctx.conn, ativo, "2001")
    inativo = _trab(ctx, "Inativo do Terminal", ativo=0)
    _forcar_codigo(ctx.conn, inativo, "2002")
    ctx.conn.commit()

    aparelho = _terminal("admin", "teste123")

    casos = [
        ({"codigo": cod_ativo, "origem": "terminal_qr"}, "registrada"),
        ({"codigo": "2003", "origem": "terminal_qr"}, "nao_encontrado"),
        ({"codigo": "2002", "origem": "terminal_qr"}, "inativo"),
        ({"codigo": "200", "origem": "terminal_qr"}, "invalido"),
        ({"codigo": "20010", "origem": "terminal_qr"}, "invalido"),
        ({"codigo": "20a1", "origem": "terminal_qr"}, "invalido"),
        ({"codigo": " 2001", "origem": "terminal_qr"}, "invalido"),
        ({"codigo": "", "origem": "terminal_qr"}, "invalido"),
        ({"codigo": None, "origem": "terminal_qr"}, "invalido"),
        ({"codigo": 2001, "origem": "terminal_qr"}, "invalido"),
        ({"codigo": "٢٠٠١", "origem": "terminal_qr"}, "invalido"),
        ({"origem": "terminal_qr"}, "invalido"),
    ]
    for corpo, resultado in casos:
        st, dados, texto = aparelho.chegada(corpo)
        assert_eq(st, 200, f"status para {corpo}")
        assert_eq(dados["resultado"], resultado, f"{corpo} → {texto[:160]}")
        if resultado == "registrada":
            assert_eq(dados["nome"], "Ativo do Terminal", f"{corpo} não devolveu o nome")
        else:
            assert_eq(dados["nome"], None, f"{corpo} devolveu nome")
            assert_eq(dados["foto"], None, f"{corpo} devolveu foto")

    assert_eq(len(_presencas(ctx.conn, ativo)), 1, "escrita além da chegada válida")
    assert_eq(_presencas(ctx.conn, inativo), [], "inativo teve presença gravada")

    # Retirado por troca confirmada: recusa e nenhuma escrita.
    with conectar() as conn:
        trocar_codigo(conn, ativo, origem="teste")
    novo = _codigo_atual(ctx.conn, ativo)
    assert novo and novo != cod_ativo, "a troca não produziu código novo"
    for origem in ("terminal_qr", "terminal_codigo"):
        st, dados, _ = aparelho.chegada({"codigo": cod_ativo, "origem": origem})
        assert_eq(dados["resultado"], "retirado", f"código retirado aceito ({origem})")
        assert_eq(dados["nome"], None, "código retirado devolveu nome")
    assert_eq(len(_presencas(ctx.conn, ativo)), 1, "código retirado escreveu presença")

    # Origem é decisão do servidor, não do cliente.
    st, dados, _ = aparelho.chegada({"codigo": novo, "origem": "recepcao"})
    assert_eq(st, 400, f"origem forjada aceita (status {st})")
    assert_eq(dados["resultado"], "invalido", "corpo da recusa de origem")
    st, dados, _ = aparelho.chegada({"codigo": novo, "origem": "terminal_qr"})
    assert_eq(st, 200, "origem válida recusada depois da troca de origem")
    # A pessoa já tinha chegado no dia (os próprios casos acima): a repetição
    # confirma sem duplicar e sem trocar a origem da primeira chegada.
    assert_eq(dados["resultado"], "ja_registrada", f"chegada válida: {dados}")
    assert_eq(_presencas(ctx.conn, ativo)[0]["origem_chegada"], "terminal_qr",
              "a repetição mudou a origem da primeira chegada")
    for origem in ("terminal_inventado", "OUTRA", "", None, 7):
        st, _, _ = aparelho.chegada({"codigo": novo, "origem": origem})
        assert_eq(st, 400, f"origem {origem!r} aceita")

    # Corpo que nem JSON é — recusa em JSON, sem página de login.
    st, dados, texto = aparelho.chegada("isso não é json")
    assert_eq(st, 400, "corpo malformado")
    assert_eq(dados["resultado"], "invalido", texto[:160])
    st, _, _ = aparelho.chegada("[1, 2, 3]")
    assert_eq(st, 400, "lista no lugar do objeto")


# ══════════════════════════════════════════════════════════════════════════
#  T6 — dia de atendimento e agenda
# ══════════════════════════════════════════════════════════════════════════

@teste("T6 sem dia, dia aberto e dia fechado: presença aceita, atendimento intocado")
def t6(ctx):
    _limpa_negocio(ctx.conn)
    hoje = _hoje_sp(ctx.conn)
    alvos = []
    for i, (aberto, com_dia) in enumerate([(None, False), (1, True), (0, True)]):
        tid = _trab(ctx, f"Trabalhador do Dia {i}")
        codigo = _forcar_codigo(ctx.conn, tid, f"300{i}")
        if com_dia:
            ctx.conn.execute(
                """INSERT INTO dias_trabalho (data, aberto) VALUES (%s, %s)
                   ON CONFLICT (data) DO UPDATE SET aberto = EXCLUDED.aberto""",
                (hoje, aberto))
        alvos.append((tid, codigo))
    sem_agenda = _trab(ctx, "Trabalhador Fora da Agenda")
    _forcar_codigo(ctx.conn, sem_agenda, "3009")
    ctx.conn.commit()

    antes = ctx.conn.execute(
        "SELECT id, data, aberto, reiki_ativo FROM dias_trabalho ORDER BY id").fetchall()
    antes = [tuple(r.values()) for r in antes]

    for tid, codigo in alvos + [(sem_agenda, "3009")]:
        aparelho = _terminal("admin", "teste123")
        st, dados, _ = aparelho.chegada({"codigo": codigo, "origem": "terminal_qr"})
        assert_eq(dados["resultado"], "registrada", f"chegada recusada (código {codigo})")
        linha = _presencas(ctx.conn, tid)[0]
        assert_eq(linha["dia_trabalho_id"], None, "presença vinculada a um dia de atendimento")

    depois = ctx.conn.execute(
        "SELECT id, data, aberto, reiki_ativo FROM dias_trabalho ORDER BY id").fetchall()
    assert_eq([tuple(r.values()) for r in depois], antes,
              "a chegada criou, abriu ou fechou dia de atendimento")


# ══════════════════════════════════════════════════════════════════════════
#  T7 — repetição e concorrência
# ══════════════════════════════════════════════════════════════════════════

@teste("T7 repetição por QR e teclado preserva a primeira chegada e a origem")
def t7(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Repetido no Terminal")
    codigo = _forcar_codigo(ctx.conn, tid, "5001")
    ctx.conn.commit()

    aparelho = _terminal("admin", "teste123")
    st, primeira, _ = aparelho.chegada({"codigo": codigo, "origem": "terminal_qr"})
    for _ in range(5):
        st, repetida, _ = aparelho.chegada({"codigo": codigo,
                                           "origem": "terminal_codigo"})
        assert_eq(repetida["resultado"], "ja_registrada", "repetição não avisou")
        assert_eq(repetida["hora"], primeira["hora"], "repetição mudou o horário")

    linhas = _presencas(ctx.conn, tid)
    assert_eq(len(linhas), 1, "repetições duplicaram a presença")
    assert_eq(linhas[0]["origem_chegada"], "terminal_qr", "origem da primeira chegada")
    assert_eq(linhas[0]["hora_chegada"], primeira["hora"], "horário trocou na repetição")

    # Presença antiga sem horário: confirma sem inventar um.
    outro = _trab(ctx, "Histórico Sem Horário")
    outro_codigo = _forcar_codigo(ctx.conn, outro, "5005")
    ctx.conn.execute(
        """INSERT INTO trabalhador_presenca
                   (trabalhador_id, data, presente, hora_chegada)
           VALUES (%s, (%s AT TIME ZONE 'America/Sao_Paulo')::date, 1, NULL)""",
        (outro, "NOW()"))
    ctx.conn.commit()
    st, dados, _ = aparelho.chegada({"codigo": outro_codigo, "origem": "terminal_qr"})
    assert_eq(dados["resultado"], "ja_registrada", "histórico sem horário não confirma")
    assert_eq(dados["hora"], None, "a resposta inventou um horário")
    assert_eq(_presencas(ctx.conn, outro)[0]["hora_chegada"], None,
              "a repetição fabricou hora_chegada")


@teste("T7b concorrência terminal × recepção: uma presença e a primeira chegada")
def t7b(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Concorrente do Terminal")
    codigo = _forcar_codigo(ctx.conn, tid, "5002")
    recepcao = _sessao_nova("admin", "teste123")
    aparelhos = [_terminal("admin", "teste123") for _ in range(4)]
    ctx.conn.commit()

    respostas = []

    def pelo_terminal(aparelho):
        respostas.append(aparelho.chegada({"codigo": codigo,
                                          "origem": "terminal_qr"})[1])

    def pela_recepcao():
        recepcao.post(f"/checkin-trabalhador/{tid}/presente")

    linhas = [threading.Thread(target=pelo_terminal, args=(a,)) for a in aparelhos]
    linhas.append(threading.Thread(target=pela_recepcao))
    linhas.append(threading.Thread(target=pelo_terminal, args=(aparelhos[0],)))
    for l in linhas:
        l.start()
    for l in linhas:
        l.join(30)

    presencas = _presencas(ctx.conn, tid)
    assert_eq(len(presencas), 1, "a concorrência duplicou a presença")
    novas = [r for r in respostas if r and r["resultado"] == "registrada"]
    assert_eq(len(novas), 1, f"mais de um registro se disse novo: {respostas}")

    origem = presencas[0]["origem_chegada"]
    assert origem in ("terminal_qr", "terminal_codigo", "recepcao"), f"origem {origem}"
    # A origem gravada tem de bater com quem de fato chegou primeiro.
    assert_eq(origem == "recepcao", len(novas) == 0,
              f"origem {origem} não combina com as respostas: {respostas}")
    assert_eq(presencas[0]["hora_chegada"], novas[0]["hora"] if novas
              else presencas[0]["hora_chegada"], "horário da primeira chegada")


# ══════════════════════════════════════════════════════════════════════════
#  T8 — resposta perdida, ausência, troca e inativação concorrentes
# ══════════════════════════════════════════════════════════════════════════

@teste("T8 escrita confirmada e resposta perdida: repetir recupera sem duplicar")
def t8(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Resposta Perdida")
    codigo = _forcar_codigo(ctx.conn, tid, "6001")
    ctx.conn.commit()

    # A transação confirma e a resposta não chega ao aparelho (queda de rede,
    # proxy, timeout): o cliente repete e a unicidade devolve o primeiro horário.
    with conectar() as conn:
        primeiro = registrar_chegada_por_codigo(conn, codigo, "terminal_qr")
    assert_eq(primeiro["resultado"], "registrada", "primeira gravação")

    aparelho = _terminal("admin", "teste123")
    st, repetida, _ = aparelho.chegada({"codigo": codigo, "origem": "terminal_codigo"})
    assert_eq(repetida["resultado"], "ja_registrada", "repetição após perda de resposta")
    assert_eq(repetida["hora"], primeiro["hora_chegada"], "a repetição achou outro horário")
    assert_eq(len(_presencas(ctx.conn, tid)), 1, "a repetição duplicou a presença")


@teste("T8b ausência explícita seguida de chegada: presente com horário e origem")
def t8b(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Ausente que Chega")
    codigo = _forcar_codigo(ctx.conn, tid, "6002")
    registrar_ausencia_explicita(ctx.conn, tid)
    ctx.conn.commit()

    linha = _presencas(ctx.conn, tid)[0]
    assert_eq(linha["presente"], 0, "a ausência não ficou como ausência")
    assert_eq(linha["origem_chegada"], None, "a ausência inventou origem de chegada")

    aparelho = _terminal("admin", "teste123")
    st, dados, _ = aparelho.chegada({"codigo": codigo, "origem": "terminal_codigo"})
    assert_eq(dados["resultado"], "registrada", "chegada depois da ausência")
    linha = _presencas(ctx.conn, tid)[0]
    assert_eq(linha["presente"], 1, "a ausência não virou presente")
    assert_eq(linha["origem_chegada"], "terminal_codigo", "origem da chegada efetiva")
    assert_eq(linha["hora_chegada"], dados["hora"], "horário divergente")
    assert_eq(len(_presencas(ctx.conn, tid)), 1, "ausência + chegada virou duas linhas")


@teste("T9 troca de código concorrente: o retirado não é aceito depois da troca")
def t9(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Troca Concorrente")
    codigo = _forcar_codigo(ctx.conn, tid, "7001")
    ctx.conn.commit()

    # Guarda aberta (transação em curso, sem confirmar) trava o trabalhador. A
    # chegada pelo código antigo espera; quando a troca se confirma, o código é
    # re-resolvido e recusado — o que já tinha passado antes da trava se
    # registrou enquanto o código ainda era o atual.
    guardiao = conectar()
    conn_g = guardiao.__enter__()
    trocar_codigo(conn_g, tid, origem="teste")

    chegada = conectar()
    conn_a = chegada.__enter__()
    resposta = {}

    def executar():
        resposta["dados"] = registrar_chegada_por_codigo(conn_a, codigo, "terminal_qr")

    executando = threading.Thread(target=executar)
    executando.start()
    time.sleep(1.5)
    assert executando.is_alive(), "a chegada não esperou a troca em curso (sem trava)"

    conn_g.commit()          # a troca passa a existir para os demais
    executando.join(30)
    guardiao.__exit__(None, None, None)
    assert_eq(resposta["dados"]["resultado"], "retirado",
              "código retirado durante a espera foi aceito")
    chegada.__exit__(None, None, None)
    assert_eq(_presencas(ctx.conn, tid), [], "presença gravada pelo código retirado")

    novo = _codigo_atual(ctx.conn, tid)
    assert novo and novo != codigo, "a troca não deixou código novo"
    with conectar() as conn:
        r = registrar_chegada_por_codigo(conn, novo, "terminal_qr")
    assert_eq(r["resultado"], "registrada", "código novo recusado depois da troca")
    assert_eq(len(_presencas(ctx.conn, tid)), 1, "chegada pelo código novo duplicou")


@teste("T9b inativação concorrente: chegada recusada, nada gravado")
def t9b(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Inativado na Chegada")
    codigo = _forcar_codigo(ctx.conn, tid, "7002")
    ctx.conn.commit()

    # A pessoa estava ativa quando o código foi resolvido e foi inativada antes
    # de o terminal registrar: o serviço de presença recusa.
    with conectar() as conn:
        assert_eq(resolver_codigo(conn, codigo)["estado"], "ativo", "código ativo")
        with conectar() as outra:
            outra.execute("UPDATE trabalhadores SET ativo = 0 WHERE id = %s", (tid,))
        r = registrar_chegada_por_codigo(conn, codigo, "terminal_qr")
        assert_eq(r["resultado"], "inativo", "inativação concorrente aceitou a chegada")
        assert_eq(r["gravado"], False, "chegada de inativo se disse gravada")
    assert_eq(_presencas(ctx.conn, tid), [], "presença de inativo gravada")


# ══════════════════════════════════════════════════════════════════════════
#  T10 — migração, origem e forma da resposta
# ══════════════════════════════════════════════════════════════════════════

@teste("T10 migração duas vezes: coluna nova, histórico intacto, origem não inventada",
       com_ctx=False)
def t10():
    with _banco_dedicado(MIG_DB):
        criar_tabelas()
        with conectar() as conn:
            exigir_ambiente_de_teste(conn)
            tid = conn.execute(
                "INSERT INTO trabalhadores (nome_completo, ativo) VALUES ('Legado', 1) "
                "RETURNING id").fetchone()["id"]
            conn.execute(
                """INSERT INTO trabalhador_presenca
                           (trabalhador_id, data, presente, hora_chegada, origem_chegada)
                   VALUES (%s, '2026-04-06'::date, 1, '19:51', NULL),
                          (%s, '2026-04-08'::date, 0, NULL,  NULL)""", (tid, tid))
            conn.execute(
                """INSERT INTO trabalhador_codigo
                           (codigo, trabalhador_id, estado, origem, criado_em)
                   VALUES ('8001', %s, 'ativo', 'tela', '2026-04-06 00:00')""", (tid,))
            conn.commit()
            retrato_antes = conn.execute(
                """SELECT data::text AS d, presente, hora_chegada, dia_trabalho_id
                     FROM trabalhador_presenca WHERE trabalhador_id = %s ORDER BY data""",
                (tid,)).fetchall()
            # Banco no modelo anterior à TASK_003: sem a coluna de origem.
            conn.execute("ALTER TABLE trabalhador_presenca DROP COLUMN origem_chegada")
            conn.commit()

        criar_tabelas()   # startup 1 — aplica a migração
        criar_tabelas()   # startup 2 — idempotência

        with conectar() as conn:
            linhas = conn.execute(
                """SELECT data::text AS d, presente, hora_chegada, dia_trabalho_id,
                          origem_chegada
                     FROM trabalhador_presenca WHERE trabalhador_id = %s ORDER BY data""",
                (tid,)).fetchall()
            assert_eq(len(linhas), 2, "a migração perdeu fatos")
            assert_eq([tuple(r[k] for k in ("d", "presente", "hora_chegada",
                                           "dia_trabalho_id")) for r in linhas],
                      [tuple(r.values()) for r in retrato_antes],
                      "a migração alterou o histórico")
            assert_eq(linhas[0]["origem_chegada"], None, "origem antiga foi inventada")
            assert_eq(linhas[1]["origem_chegada"], None, "origem antiga foi inventada")

            r = registrar_chegada_por_codigo(conn, "8001", "terminal_codigo")
            assert_eq(r["resultado"], "registrada", "chegada no banco migrado")
            hoje = conn.execute(
                """SELECT origem_chegada, presente FROM trabalhador_presenca
                    WHERE trabalhador_id = %s AND data = %s::date""",
                (tid, _hoje_sp(conn))).fetchone()
            assert_eq(hoje["origem_chegada"], "terminal_codigo", "origem da chegada nova")
            assert_eq(hoje["presente"], 1, "a chegada nova não ficou presente")
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM trabalhador_presenca WHERE trabalhador_id = %s",
                (tid,)).fetchone()["n"]
            assert_eq(total, 3, "a chegada nova substituiu o histórico")

        criar_tabelas()   # startup 3 — com a coluna já preenchida
        with conectar() as conn:
            origens = conn.execute(
                """SELECT origem_chegada FROM trabalhador_presenca
                    WHERE trabalhador_id = %s ORDER BY data""", (tid,)).fetchall()
            assert_eq([r["origem_chegada"] for r in origens],
                      [None, None, "terminal_codigo"],
                      "migração repetida alterou as origens")


@teste("T10b registro manual da recepção tem origem 'recepcao'; origem inválida é recusada")
def t10b(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Manual da Recepção")
    outro = _trab(ctx, "Serviço Rígido")
    ctx.conn.commit()                  # as outras conexões precisam ver as pessoas

    st, _, _ = _sessao_nova("admin", "teste123").post(f"/checkin-trabalhador/{tid}/presente")
    assert_eq(st, 303, "check-in da recepção")
    assert_eq(_presencas(ctx.conn, tid)[0]["origem_chegada"], "recepcao",
              "origem do registro manual")

    with conectar() as conn:
        for origem in ("terminal_inventado", "terminal_qr_x", "", None, 7):
            try:
                registrar_chegada(conn, outro, origem=origem)
                raise AssertionError(f"origem {origem!r} aceita pelo serviço")
            except OrigemInvalida:
                pass
        registrar_chegada(conn, outro)
    assert_eq(_presencas(ctx.conn, outro)[0]["origem_chegada"], "recepcao",
              "origem padrão do serviço")
    assert_eq(len(_presencas(ctx.conn, outro)), 1, "as recusas escreveram presença")


@teste("T11 data e hora do servidor em America/Sao_Paulo, não o fuso da sessão")
def t11(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Trabalhador do Fuso")
    codigo = _forcar_codigo(ctx.conn, tid, "9001")
    ctx.conn.commit()

    aparelho = _terminal("admin", "teste123")
    st, dados, _ = aparelho.chegada({"codigo": codigo, "origem": "terminal_qr"})
    assert_eq(dados["data"], _hoje_sp(ctx.conn), "a data devolvida não é a de São Paulo")
    assert_eq(len(dados["hora"] or ""), 5, f"horário fora de HH:MM: {dados['hora']}")

    # Mesmo servidor, sessão em outro fuso: a data local continua a paulistana.
    outro = _trab(ctx, "Trabalhador do Fuso 2")
    ctx.conn.commit()                 # a outra conexão precisa ver a pessoa criada
    with conectar() as conn:
        conn.execute("SET TIME ZONE 'Asia/Tokyo'")
        outro_codigo = _forcar_codigo(conn, outro, "9002")
        r = registrar_chegada_por_codigo(conn, outro_codigo, "terminal_qr")
        conn.execute("RESET TIME ZONE")
    assert_eq(r["data"], _hoje_sp(ctx.conn), "o fuso da sessão mudou a data local")

    # Fronteira do dia: 02:30 UTC ainda é ontem em São Paulo.
    with conectar() as conn:
        data, hora = conn.execute(
            """SELECT ((%s AT TIME ZONE 'America/Sao_Paulo')::date)::text AS d,
                      to_char(%s AT TIME ZONE 'America/Sao_Paulo', 'HH24:MI') AS h""",
            ("2026-10-23 02:30+00", "2026-10-23 02:30+00")).fetchone().values()
        assert_eq(data, "2026-10-22", "a virada UTC/São Paulo não é a local")
        assert_eq(hora, "23:30", "hora local da fronteira")


@teste("T12 sucesso devolve só nome, foto, data e horário; nada do cadastro")
def t12(ctx):
    _limpa_negocio(ctx.conn)
    tid = _trab(ctx, "Trabalhador Vigiado")
    codigo = _forcar_codigo(ctx.conn, tid, "9003")
    ctx.conn.execute(
        """UPDATE trabalhadores SET cpf = '111.222.333-44', telefone = '11 98888-7777',
                  email = 'vigiado@exemplo.test', valor_mensalidade = 123.45,
                  fiado_limite = 999,
                  foto_trabalhador = 'trab_%s_ab12cd34.jpg'
            WHERE id = %s""", (tid, tid))
    ctx.conn.commit()

    aparelho = _terminal("admin", "teste123")
    st, dados, texto = aparelho.chegada({"codigo": codigo, "origem": "terminal_qr"})
    assert_eq(st, 200, "chegada com foto")
    assert_eq(sorted(dados.keys()), ["data", "foto", "hora", "nome", "resultado"],
              f"campos além do combinado: {sorted(dados)}")
    assert_eq(dados["nome"], "Trabalhador Vigiado", "nome")
    assert_eq(dados["foto"], f"/static/fotos/trab_{tid}_ab12cd34.jpg", "foto existente")
    for vedado in ("111.222", "98888", "vigiado@", "123.45", "999", "cpf",
                   "mensalidade", "fiado", "telefone", "email"):
        assert vedado not in texto, f"a resposta trouxe {vedado}"

    # Sem foto o campo vem nulo; nome de arquivo duvidoso não vira caminho.
    sem_foto = _trab(ctx, "Sem Foto no Terminal")
    _forcar_codigo(ctx.conn, sem_foto, "9004")
    ctx.conn.commit()
    st, dados, _ = aparelho.chegada({"codigo": "9004", "origem": "terminal_qr"})
    assert_eq(dados["foto"], None, "sem foto deveria devolver nulo")

    ctx.conn.execute("UPDATE trabalhadores SET foto_trabalhador = %s WHERE id = %s",
                     ("../../etc/passwd", sem_foto))
    ctx.conn.commit()
    st, dados, _ = aparelho.chegada({"codigo": "9004", "origem": "terminal_codigo"})
    assert_eq(dados["foto"], None, "caminho pai-a-pai virou URL")
    ctx.conn.execute("UPDATE trabalhadores SET foto_trabalhador = %s WHERE id = %s",
                     ("duas/pastas.jpg", sem_foto))
    ctx.conn.commit()
    st, dados, _ = aparelho.chegada({"codigo": "9004", "origem": "terminal_codigo"})
    assert_eq(dados["foto"], None, "caminho com barra virou URL")


# ══════════════════════════════════════════════════════════════════════════
#  Runner
# ══════════════════════════════════════════════════════════════════════════

def main():
    preparar_banco()
    _garantir_admin()
    with conectar() as conn:
        _limpa_negocio(conn)

    total = ok = 0
    falhas = []
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
        except Exception as e:  # noqa: BLE001
            falhas.append(nome)
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            traceback.print_exc()

    print(f"\n{ok}/{total} testes passaram (TASK_003 terminal — sessão e backend)")
    with contextlib.suppress(Exception):
        remover_se_nosso(MIG_DB, TOKEN)
    limpar_banco()
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
