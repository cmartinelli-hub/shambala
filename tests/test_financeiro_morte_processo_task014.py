#!/usr/bin/env python3
"""TASK_014 §3 — FIN-09: MORTE REAL DO PROCESSO entre commit e promoção.

Esta suíte existe porque as provas de FIN-09 na suíte de idempotência são de
outro nível: lá o **servidor está vivo** e a exceção simula a parada. Aqui um
processo do app é iniciado à parte e morto com **SIGKILL** enquanto a requisição
está congelada dentro da janela (entre o commit e a promoção do arquivo) — sem
rollback, sem `finally`, sem compensação. É o cenário que a TASK_013 exigiu não
presumir.

O que se demonstra, em nível de processo:

1. a requisição entra na janela (sentinela) e o processo é morto ali;
2. o estado que a morte deixa é o previsto: compra `pago`, arquivo **ausente** e
   temporário no disco, tentativa `concluida` apontando para o documento;
3. o startup seguinte — um processo de verdade, sem injeção — cancela a compra,
   **anula** a chave e remove o temporário, sem ninguém chamar nada à mão;
4. depois da recuperação, reenviar a mesma chave não informa sucesso.

Uso: via `tests/rodar_testes.sh` (destino provado, token da execução).
"""
import contextlib
import http.cookiejar
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trava_isolamento import (  # noqa: E402
    AmbienteInseguro, exigir_ambiente_de_teste, exigir_configuracao_consoante,
)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIXO = f"T014M-{uuid.uuid4().hex[:8]}"
JANELA = "/tmp/t014_na_janela"

arq_env = os.environ.get("SHAMBALA_ENV_FILE")
if not arq_env:
    print("[ABORTADO] SHAMBALA_ENV_FILE ausente", file=sys.stderr)
    raise SystemExit(2)
try:
    exigir_configuracao_consoante(arq_env, os.environ.get("SHAMBALA_TEST_DB"))
except AmbienteInseguro as e:
    print(f"[ABORTADO] {e}", file=sys.stderr)
    raise SystemExit(2)

from banco import conectar, fechar_pool, criar_tabelas  # noqa: E402
from rotas.compras import _caminho_da_nota, FOTOS_DIR  # noqa: E402
from test_tres_passes import _garantir_admin  # noqa: E402

with conectar() as conn:
    info = exigir_ambiente_de_teste(conn)
print(f"[ok] conexão do script: banco={info['banco_efetivo']} "
      f"usuario={info['usuario_efetivo']}")

falhas = []
aprovadas = []


def verificar(nome, cond, detalhe=""):
    if cond:
        aprovadas.append(nome)
        print(f"  PASS  {nome}")
    else:
        falhas.append(f"{nome} — {detalhe}")
        print(f"  FALHOU {nome} — {detalhe}")


def _porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORTA = _porta_livre()
BASE = f"http://127.0.0.1:{PORTA}"
LOG = f"/tmp/t014_morte_{PORTA}.log"


def subir_app(injecao=None):
    """Sobe um app de verdade. `injecao` fica SÓ neste processo."""
    env = dict(os.environ)
    env.pop("SHAMBALA_FALHA_DISCO_COMPRA", None)
    if injecao:
        env["SHAMBALA_FALHA_DISCO_COMPRA"] = injecao
    env["SHAMBALA_MODO_TESTE"] = "1"
    log = open(LOG, "w")
    proc = subprocess.Popen(
        [".venv/bin/python", "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(PORTA)],
        cwd=RAIZ, env=env, stdout=log, stderr=log, start_new_session=True)
    for _ in range(120):
        try:
            urllib.request.urlopen(BASE + "/login", timeout=1)
            return proc
        except Exception:
            if proc.poll() is not None:
                raise SystemExit(f"o app não subiu — ver {LOG}")
            time.sleep(0.5)
    raise SystemExit(f"o app não respondeu — ver {LOG}")


class _SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def operador():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(_SemRedirect(),
                                     urllib.request.HTTPCookieProcessor(jar))
    corpo = urllib.parse.urlencode(
        {"nome_usuario": "admin", "senha": "teste123"}).encode()
    with contextlib.suppress(urllib.error.HTTPError):
        op.open(urllib.request.Request(BASE + "/login", data=corpo, method="POST"))
    if not [c for c in jar if c.name == "sessao"]:
        raise SystemExit("login do app de morte falhou")
    return op


def enviar_compra(op, descricao, chave_, cabecalhos=None):
    borda = "----t014" + uuid.uuid4().hex[:8]
    campos = {"valor": "33.00", "descricao": descricao,
              "forma_pagamento": "especie", "idempotencia": chave_}
    partes = [(f'--{borda}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
               f"{v}\r\n").encode() for k, v in campos.items()]
    partes.append(
        (f'--{borda}\r\nContent-Disposition: form-data; name="foto"; '
         f'filename="nota.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode()
        + b"\xff\xd8\xffT014" + b"\r\n")
    partes.append(f"--{borda}--\r\n".encode())
    req = urllib.request.Request(
        BASE + "/financeiro/compras", data=b"".join(partes), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={borda}",
                 **(cabecalhos or {})})
    try:
        with op.open(req, timeout=3) as resp:
            return resp.status, resp.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")


def linha_de(descricao):
    with conectar() as conn:
        return conn.execute(
            """SELECT id, status, nota_fiscal_foto FROM financeiro_movimentacoes
               WHERE descricao = %s""", (descricao,)).fetchone()


def chave_de(chave_):
    with conectar() as conn:
        return conn.execute(
            """SELECT status, documento, destino FROM operacao_idempotencia
               WHERE chave = %s""", (chave_,)).fetchone()


def temporarios_de(mov_id):
    return [f for f in os.listdir(FOTOS_DIR)
            if ".tmp-" in f and f.startswith(f"nota_{mov_id}_")]


def main():
    # Schema e admin com senha conhecida: o banco descartável é recriado a cada
    # execução do runner, e sem isso o login do app de morte devolve 401.
    criar_tabelas()
    _garantir_admin()
    with contextlib.suppress(FileNotFoundError):
        os.remove(JANELA)

    # ── 1) morte real dentro da janela ─────────────────────────────────────
    proc = subir_app(injecao="pausar")
    k = uuid.uuid4().hex
    desc = f"{PREFIXO} morte real"
    try:
        op = operador()
        caixa = {}

        def disparar():
            caixa["r"] = enviar_compra(op, desc, k,
                                       {"X-Tentativa-Falha-Disco": "pausar"})

        t = threading.Thread(target=disparar)
        t.start()
        for _ in range(300):
            if os.path.exists(JANELA):
                break
            time.sleep(0.1)
        entrou = os.path.exists(JANELA)

        # o estado ANTES da morte: prova de que a janela é real
        linha_janela = linha_de(desc)
        os.kill(proc.pid, signal.SIGKILL)     # morte real, sem cleanup
        proc.wait()
        t.join(20)

        verificar("a requisição foi congelada DENTRO da janela (depois do commit, "
                  "antes da promoção)", entrou and linha_janela is not None,
                  f"entrou={entrou} linha={dict(linha_janela) if linha_janela else None}")

        linha = linha_de(desc)
        temporarios = temporarios_de(linha["id"]) if linha else []
        verificar("o estado que a morte deixa é o previsto: compra 'pago', arquivo "
                  "AUSENTE e temporário no disco",
                  linha is not None and linha["status"] == "pago"
                  and not os.path.exists(_caminho_da_nota(linha["nota_fiscal_foto"]))
                  and len(temporarios) == 1,
                  f"linha={dict(linha) if linha else None} temporarios={temporarios}")
        chave_morta = chave_de(k)
        verificar("a tentativa ficou 'concluida' apontando para o documento "
                  "(é por isso que status de tentativa não distingue a janela)",
                  chave_morta is not None and chave_morta["status"] == "concluida"
                  and chave_morta["documento"] is not None,
                  str(dict(chave_morta) if chave_morta else None))
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait()

    # ── 2) o startup seguinte recupera, sem ninguém chamar nada ────────────
    proc2 = subir_app()          # SEM injeção: como em produção
    proc2.terminate()
    proc2.wait()
    linha = linha_de(desc)
    chave_depois = chave_de(k)
    verificar("o startup seguinte cancela a compra, ANULA a chave e remove o "
              "temporário — sem chamada manual",
              linha is not None and linha["status"] == "cancelado"
              and chave_depois is not None and chave_depois["status"] == "anulada"
              and chave_depois["documento"] is None
              and temporarios_de(linha["id"]) == [],
              f"linha={dict(linha) if linha else None} "
              f"chave={dict(chave_depois) if chave_depois else None} "
              f"temporarios={temporarios_de(linha['id']) if linha else '?'}")

    # ── 3) reenviar a mesma chave não informa sucesso ──────────────────────
    proc3 = subir_app()
    try:
        op = operador()
        st, loc = enviar_compra(op, desc, k)
        verificar("reenviar a mesma chave depois da recuperação NÃO informa "
                  "sucesso — recebe a recusa legível",
                  st == 303 and "erro=tentativa_anulada" in (loc or ""),
                  f"status={st} loc={loc}")
        with conectar() as conn:
            n = conn.execute(
                """SELECT count(*) AS c FROM financeiro_movimentacoes
                   WHERE descricao = %s""", (desc,)).fetchone()["c"]
        verificar("o reenvio não cria segundo lançamento", n == 1, f"linhas={n}")
    finally:
        if proc3.poll() is None:
            proc3.terminate()
            proc3.wait()

    # limpeza do rastro desta suíte (só o que ela criou)
    with conectar() as conn:
        conn.execute("DELETE FROM operacao_idempotencia WHERE chave = %s", (k,))
        conn.execute("DELETE FROM financeiro_movimentacoes WHERE descricao = %s",
                     (desc,))
    with contextlib.suppress(FileNotFoundError):
        os.remove(JANELA)
    fechar_pool()

    total = len(aprovadas) + len(falhas)
    print(f"\n{len(aprovadas)}/{total} verificações aprovadas "
          "(TASK_014 §3 — morte real do processo)")
    for f in falhas:
        print(f"  - {f}")
    return 0 if not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
