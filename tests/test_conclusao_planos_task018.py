#!/usr/bin/env python3
"""Suíte da TASK_018 — conclusão administrativa de planos e sessões adicionais.

Cobre a matriz de validação §7 da tarefa:

  K1  cinco previstos, quinto realizado → uma conclusão; reenvio sem efeito.
  K2  quatro realizados + falta → não conclui; reposição cumprida sem dupla contagem.
  K3  adicional antes da última realização impede conclusão prematura;
      reagendamento e repetição não duplicam a obrigação.
  K4  plano sem sessões, incompleto, com metadado ausente ou legado →
      nenhuma conclusão por suposição.
  K5  /agenda: um plano ativo confirma o destino; vários exigem escolha;
      médium incompatível recusa o vínculo (referências humanas, não ids).
  K6  plano concluído não reabre por inclusão; tentativa obsoleta e reenvio não
      duplicam; a tela lê a situação "Concluído".
  K7  disputa inclusão × última realização nas duas ordens, com evidência da
      coordenação no banco (duas conexões, sem sleeps de conveniência).
  K8  matriz de permissões e POSTs com pessoa/plano/médium incompatíveis.
  K9  migração idempotente: fatos históricos idênticos após startup repetido e
      a regra prospectiva não alcança o legado.
  K10 desfazer reavaliado: reabertura auditada; alta/encerramento manual não
      são revertidos; recusa sem efeitos parciais quando já há outro plano ativo.
  K11 cancelar extensão revoga a extensão; cancelar prevista não reduz a
      previsão nem conta como cumprimento.
  K12 GET/render não avaliam nem fecham plano; a conclusão acontece só na
      transição real.
  K13 os QUATRO criadores de plano (Agenda, lançamento do médium, acolhimento
      fraterno, tela do médium) gravam `regra_conclusao` e `sessoes_previstas`
      pelos caminhos reais e concluem ao cumprir o previsto.
  K14 reposição pelo formulário da agenda do plano: a falta abre vaga, a tela
      oferece a reposição, o POST grava `repoe` sem tocar em `sessoes_adicionais`
      e o plano fecha cumprindo a obrigação original.
  K15 recusas reais: sem vaga a rota explica na tela, tipo inválido não escreve,
      cancelar a reposição reabre a vaga.
  K16 reposição pelos outros dois POSTs da recepção (/agenda/novo com escolha
      por plano e /agenda/medium/lancar, com a JSON de situação informando a
      vaga).

Roda contra o banco descartável de teste (trava em test_tres_passes). Nenhum
atendimento real é tocado.

Uso (recomendado):
    SHAMBALA_SUITES=test_conclusao_planos_task018.py bash tests/rodar_testes.sh
"""
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from datetime import date, timedelta

from psycopg2.extras import RealDictCursor

# A importação abaixo executa a TRAVA DE ISOLAMENTO na ordem obrigatória
# (configurar → validar → importar a aplicação) — ver test_tres_passes.
import test_tres_passes as tp
from test_tres_passes import (
    Ctx, assert_eq, preparar_banco, limpar_banco,
    _http, _login, _garantir_admin, _planos_da_pessoa, BASE, _sessao,
)
from test_permissoes import _nova_sessao, _grupo, _perm, _usuario_em_grupo

import banco
import servico_conclusao_planos as sc

conectar = tp.conectar
HOJE = date.today()

RESULTADOS = []


def teste(nome):
    def wrap(fn):
        RESULTADOS.append((nome, fn))
        return fn
    return wrap


# ── Fixtures e leituras ───────────────────────────────────────────────────────

def _plano_prospectivo(ctx, medium_id, pessoa_id, total, freq="semanal",
                       cfg=3, inicio=None, previstas="auto", regras=1):
    """Plano nascido SOB a regra de conclusão (regra_conclusao=1) — é assim que
    o acolhimento e a Agenda criam planos depois da TASK_018. `previstas=None`
    simula o metadado ausente."""
    inicio = inicio or HOJE
    pv = total if previstas == "auto" else previstas
    cur = ctx.conn.execute(
        """INSERT INTO planos_tratamento
           (medium_id, sessoes_total, sessoes_previstas, data_inicio, frequencia,
            sessoes_com_passe, status, regra_conclusao)
           VALUES (%s,%s,%s,%s,%s,%s,'ativo',%s) RETURNING id""",
        (medium_id, total, pv, inicio.isoformat(), freq,
         banco.calcular_sessoes_com_passe(total, cfg), regras),
    )
    pid = cur.fetchone()["id"]
    ctx.conn.execute(
        "INSERT INTO plano_pessoas (plano_id, pessoa_id) VALUES (%s,%s)",
        (pid, pessoa_id))
    if total:
        banco.gerar_agendamentos_plano(
            ctx.conn, pid, inicio, freq, total,
            banco.calcular_sessoes_com_passe(total, cfg))
    return pid


def _plano(ctx, pid):
    ctx.conn.commit()
    return ctx.conn.execute(
        "SELECT * FROM planos_tratamento WHERE id = %s", (pid,)).fetchone()


def _contagens(ctx, pid):
    ctx.conn.commit()
    r = ctx.conn.execute(
        """SELECT count(*) AS total,
                  count(*) FILTER (WHERE status = 'realizado') AS realizadas,
                  count(*) FILTER (WHERE status = 'agendado')  AS agendadas,
                  count(*) FILTER (WHERE status = 'cancelado') AS canceladas,
                  count(*) FILTER (WHERE status = 'faltou')    AS faltas,
                  count(*) FILTER (WHERE adicionada = 1)       AS extras,
                  count(*) FILTER (WHERE repoe = 1)            AS reposicoes
           FROM agendamentos WHERE plano_id = %s""", (pid,)).fetchone()
    return dict(r)


def _sessoes(ctx, pid):
    ctx.conn.commit()
    return ctx.conn.execute(
        "SELECT id, data, status, adicionada, repoe FROM agendamentos WHERE plano_id=%s "
        "ORDER BY data, id", (pid,)).fetchall()


def _livres(ctx, pid, n=1):
    """Datas de atendimento ainda sem sessão neste plano (para acrescentar)."""
    ctx.conn.commit()
    permitidos = {r["dia_semana"] for r in ctx.conn.execute(
        "SELECT dia_semana FROM dias_atendimento").fetchall()}
    usadas = {r["data"] for r in ctx.conn.execute(
        "SELECT data FROM agendamentos WHERE plano_id = %s", (pid,)).fetchall()}
    out, i = [], 0
    while len(out) < n and i < 120:
        dia = HOJE + timedelta(days=i)
        i += 1
        if dia.weekday() in permitidos and dia.isoformat() not in usadas:
            out.append(dia.isoformat())
    assert_eq(len(out), n, "datas de atendimento livres")
    return out


def _realizar(ctx, pid, med, pes, ag_id, codigo, dia_id):
    """Marca realizado pelo CAMINHO REAL (rota da fila do dia), com checkin
    vinculado ao plano e à sessão."""
    ck = ctx.conn.execute(
        """INSERT INTO checkins (dia_trabalho_id, pessoa_id, hora_checkin,
                                 codigo_atendimento, plano_id, medium_id, agendamento_id)
           VALUES (%s,%s,'10:00',%s,%s,%s,%s) RETURNING id""",
        (dia_id, pes, str(codigo), pid, med, ag_id)).fetchone()["id"]
    ctx.conn.commit()
    st, _ = _http("POST", f"/dia/atendimento/{ck}/realizado")
    assert_eq(st, 303, "POST de realizado")
    return ck


def _post_agenda_novo(pes, med, data, confirmar=0, destino=0, qtd=1, tipo=None):
    campos = {
        "pessoa_id": str(pes), "data": data, "medium_id": str(med),
        "data_de": "", "data_ate": "", "dia_semana": "-1", "pessoa_nome": "",
        "qtd_sessoes": str(qtd), "frequencia": "semanal",
        "confirmar": str(confirmar), "plano_destino": str(destino)}
    if tipo is not None:
        campos[f"tipo_{destino}"] = tipo
    return _http("POST", "/agenda/novo", campos)


def _post_agenda_plano(med, pid, data, tipo="adicao", requer=0, encaixe=0):
    """O formulário "Adicionar sessão ao plano" da agenda do plano."""
    return _http("POST", f"/cadastros/mediuns/{med}/planos/{pid}/agenda/novo", {
        "data": data, "tipo": tipo, "requer_passe": str(requer),
        "encaixe": str(encaixe)})


def _post_lancar_medium(pes, med, data, qtd=10, tipo=None):
    campos = {"pessoa_id": str(pes), "medium_id": str(med), "data": data,
              "qtd_sessoes": str(qtd), "frequencia": "semanal"}
    if tipo is not None:
        campos["tipo"] = tipo
    return _http("POST", "/agenda/medium/lancar", campos)


def _post_fraterno(ck, med, total, inicio, passe=1):
    return _http("POST", f"/dia/fraterno/{ck}", [
        ("medium_id", str(med)), ("sessoes_total", str(total)),
        ("frequencia", "semanal"), ("sessoes_com_passe", str(passe)),
        ("data_inicio", inicio.isoformat()),
        ("observacao_acolhimento", "")])


def _post_plano_medium(med, pes, total, inicio):
    return _http("POST", f"/cadastros/mediuns/{med}/planos/novo", [
        ("sessoes_total", str(total)), ("frequencia", "semanal"),
        ("sessoes_com_passe", "3"), ("data_inicio", inicio.isoformat()),
        ("pessoas_ids", str(pes)), ("next", "")])


def _plano_da_pessoa(ctx, pes):
    """O plano recém-criado da pessoa (mais recente) — usado para conferir o
    que os criadores gravaram. A contagem é conferida separadamente."""
    ctx.conn.commit()
    return ctx.conn.execute(
        """SELECT pt.* FROM planos_tratamento pt
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           WHERE pp.pessoa_id = %s ORDER BY pt.id DESC LIMIT 1""",
        (pes,)).fetchone()


class _CapturaRedirect(urllib.request.HTTPRedirectHandler):
    local = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.local = urllib.parse.urljoin(req.full_url, newurl or "")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_REDIRECIONA = _CapturaRedirect()
_OPENER_DESTINO = None


def _http_ate_o_destino(method, path, data=None):
    """POST real SEGUINDO o 303 até a página de destino — é assim que a
    recepção efetivamente lê a mensagem (a rota responde com redirect).
    Devolve (status_final, url_de_destino, corpo_renderizado)."""
    global _OPENER_DESTINO
    if _OPENER_DESTINO is None:
        cj = None
        for h in _sessao().handlers:
            if isinstance(h, urllib.request.HTTPCookieProcessor):
                cj = h.cookiejar
        assert cj is not None, "cookie jar da sessão de teste"
        _OPENER_DESTINO = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj), _REDIRECIONA)
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(BASE + path, data=body, method=method)
    _REDIRECIONA.local = None
    try:
        with _OPENER_DESTINO.open(req) as r:
            return r.status, _REDIRECIONA.local, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, _REDIRECIONA.local, e.read().decode("utf-8", "replace")


# ── K1 · cinco previstos → conclusão única ───────────────────────────────────

@teste("K1 cinco previstos: o 5º realizado conclui uma vez e o reenvio não repete o efeito")
def k1(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k1")
    dia_id = ctx.dia()
    pid = _plano_prospectivo(ctx, med, pes, 5)
    ctx.conn.commit()

    ags = [s["id"] for s in _sessoes(ctx, pid)]
    assert_eq(len(ags), 5, "5 sessões geradas")
    checks = []
    for i, ag in enumerate(ags):
        checks.append(_realizar(ctx, pid, med, pes, ag, i + 1, dia_id))
        p = _plano(ctx, pid)
        esperado = "concluido" if i == 4 else "ativo"
        assert_eq(p["status"], esperado, f"status após a {i+1}ª realizada")
    p = _plano(ctx, pid)
    assert_eq(p["conclusao_origem"], sc.ORIGEM_AUTOMATICA, "origem da conclusão")
    assert_eq(bool(p["concluido_em"]), True, "instante da conclusão registrado")
    assert_eq(p["concluido_por"] is not None, True, "autoria registrada")
    assert_eq(p["sessoes_realizadas"], 5, "contador do plano")
    assert_eq(_contagens(ctx, pid)["realizadas"], 5, "5 sessões realizadas")
    instante = p["concluido_em"]

    # Reenvio (duplo clique / retry) da última: nada é contado duas vezes.
    _http("POST", f"/dia/atendimento/{checks[-1]}/realizado")
    for ck in checks:
        _http("POST", f"/dia/atendimento/{ck}/realizado")
    p2 = _plano(ctx, pid)
    c2 = _contagens(ctx, pid)
    assert_eq(p2["status"], "concluido", "situação inalterada")
    assert_eq(p2["sessoes_realizadas"], 5, "sem dupla contagem")
    assert_eq(c2["realizadas"], 5, "nenhuma sessão extra realizada")
    assert_eq(p2["concluido_em"], instante, "nenhum segundo evento de conclusão")


# ── K2 · falta não cumpre; reposição conta uma vez ───────────────────────────

@teste("K2 4 realizados + 1 falta não conclui; reposição cumprida conclui sem dupla contagem")
def k2(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k2")
    dia_id = ctx.dia()
    pid = _plano_prospectivo(ctx, med, pes, 5)
    ctx.conn.commit()

    ags = [s["id"] for s in _sessoes(ctx, pid)]
    st, _ = _http("POST", f"/cadastros/mediuns/{med}/planos/{pid}/agenda/{ags[0]}/falta")
    assert_eq(st, 303, "rota de falta")
    for i, ag in enumerate(ags[1:]):
        _realizar(ctx, pid, med, pes, ag, i + 2, dia_id)
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq((p["status"], c["realizadas"], c["faltas"]), ("ativo", 4, 1),
              "falta não conta como cumprimento — plano segue aberto")
    assert_eq(_livres(ctx, pid, 1)[0] is not None, True, "fixture ok")

    # Reposição da sessão vencida: entra sem aumentar a previsão e, quando
    # cumprida, fecha o plano — contada uma única vez.
    d_rep = _livres(ctx, pid, 1)[0]
    res = sc.acrescentar_sessao(ctx.conn, pid, d_rep, tipo="reposicao",
                                autor_id=None)
    ctx.conn.commit()
    assert_eq(res, "ok", "reposição inserida")
    p = _plano(ctx, pid)
    assert_eq(p["sessoes_adicionais"], 0, "reposição não é extensão")
    c = _contagens(ctx, pid)
    assert_eq(c["agendadas"], 1, "1 reposição pendente")
    nova = [s for s in _sessoes(ctx, pid) if s["data"] == d_rep][0]["id"]
    _realizar(ctx, pid, med, pes, nova, 99, dia_id)
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq(p["status"], "concluido", "quota cumprida pela reposição")
    assert_eq((c["realizadas"], p["sessoes_realizadas"]), (5, 5),
              "5 cumpridas (a falta não virou realizada dupla)")
    assert_eq(c["faltas"], 1, "a falta permanece registrada como falta")


# ── K3 · extensão antes da última realização ─────────────────────────────────

@teste("K3 sessão extra antes da última realização impede conclusão prematura; reagendar não duplica")
def k3(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k3")
    dia_id = ctx.dia()
    pid = _plano_prospectivo(ctx, med, pes, 5)
    ctx.conn.commit()
    ags = [s["id"] for s in _sessoes(ctx, pid)]

    for i, ag in enumerate(ags[:4]):
        _realizar(ctx, pid, med, pes, ag, i + 1, dia_id)
    assert_eq(_plano(ctx, pid)["status"], "ativo", "4 de 5")

    d_extra = _livres(ctx, pid, 1)[0]
    st, _ = _http("POST", f"/cadastros/mediuns/{med}/planos/{pid}/agenda/novo",
                  {"data": d_extra, "requer_passe": "0", "encaixe": "0"})
    assert_eq(st, 303, "inclusão extra pela agenda do plano")
    p = _plano(ctx, pid)
    assert_eq(p["sessoes_adicionais"], 1, "extensão líquida registrada")

    # Última realizada PREVISTA: com a extra pendente, o plano NÃO pode fechar.
    _realizar(ctx, pid, med, pes, ags[4], 5, dia_id)
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq(p["status"], "ativo", "sem conclusão prematura (falta a extra)")
    assert_eq((c["realizadas"], c["agendadas"]), (5, 1), "5 cumpridas, 1 pendente")

    # Reenvio da mesma inclusão: recusado pela data já ocupada — a obrigação
    # não se duplica (nem o número de extensões, nem o de sessões).
    st, _ = _http("POST", f"/cadastros/mediuns/{med}/planos/{pid}/agenda/novo",
                  {"data": d_extra, "requer_passe": "0", "encaixe": "0"})
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq(p["sessoes_adicionais"], 1, "extensão não duplicada")
    assert_eq(c["total"], 6, "nenhuma sessão extra gravada")

    # Reagendar a extra (mesma linha, outra data) continua uma só obrigação.
    d_nova = _livres(ctx, pid, 1)[0]
    extra_id = [s["id"] for s in _sessoes(ctx, pid) if s["data"] == d_extra][0]
    st, _ = _http("POST",
                  f"/cadastros/mediuns/{med}/planos/{pid}/agenda/{extra_id}/reagendar",
                  {"nova_data": d_nova})
    assert_eq(st, 303, "reagendamento")
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq((p["sessoes_adicionais"], c["total"], c["agendadas"]), (1, 6, 1),
              "reagendamento não cria obrigação duplicada")

    # Cumprida a extra, o plano fecha com 6 cumpridas.
    _realizar(ctx, pid, med, pes, extra_id, 6, dia_id)
    p = _plano(ctx, pid)
    assert_eq((p["status"], p["sessoes_realizadas"]), ("concluido", 6),
              "conclusão somente após cumprida a extensão")


# ── K4 · nenhuma conclusão por suposição ─────────────────────────────────────

@teste("K4 sem sessões, incompleto, metadado ausente ou legado: nunca conclui por suposição")
def k4(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med = ctx.medium()
    p_a, p_b, p_c, p_d = (ctx.pessoa(n) for n in ("k4a", "k4b", "k4c", "k4d"))

    vazio = _plano_prospectivo(ctx, med, p_a, 0)                    # sem sessões
    parcial = _plano_prospectivo(ctx, med, p_b, 3)                  # incompleto
    sem_meta = _plano_prospectivo(ctx, med, p_c, 2, previstas=None)  # metadado ausente
    legado = ctx.plano(med, p_d, 3, "semanal", 3)                    # regra 0

    # tudo cumprido nos três últimos (e no legado)
    for pid in (parcial, sem_meta, legado):
        for s in _sessoes(ctx, pid):
            ctx.conn.execute("UPDATE agendamentos SET status='realizado' WHERE id=%s",
                             (s["id"],))
        ctx.conn.execute(
            "UPDATE planos_tratamento SET sessoes_realizadas = "
            "(SELECT count(*) FROM agendamentos WHERE plano_id=%s AND status='realizado') "
            "WHERE id=%s", (pid, pid))
    ctx.conn.commit()

    assert_eq(sc.reavaliar(ctx.conn, vazio), "nada", "plano sem sessões")
    assert_eq(sc.reavaliar(ctx.conn, parcial), "concluido", "recém-cumprido conclui")
    assert_eq(_plano(ctx, parcial)["status"], "concluido")
    assert_eq(sc.reavaliar(ctx.conn, sem_meta), "nada", "sem quota estável não se conclui")
    assert_eq(_plano(ctx, sem_meta)["status"], "ativo", "metadado ausente: intacto")
    assert_eq(sc.reavaliar(ctx.conn, legado), "nada",
              "legado (regra 0) jamais conclui automaticamente")
    assert_eq(_plano(ctx, legado)["status"], "ativo")
    assert_eq(_plano(ctx, vazio)["status"], "ativo")


# ── K5 · destino escolhido na tela habitual ──────────────────────────────────

@teste("K5 /agenda: um plano pede confirmação, vários exigem escolha, médium incompatível recusa")
def k5(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med = ctx.medium()
    med2 = ctx.medium()

    # (a) um plano ativo, mesmo médium
    pes = ctx.pessoa("k5-um")
    pid = _plano_prospectivo(ctx, med, pes, 5, inicio=HOJE - timedelta(days=30))
    for i, s in enumerate(_sessoes(ctx, pid)[:3]):
        ctx.conn.execute("UPDATE agendamentos SET status='realizado' WHERE id=%s",
                         (s["id"],))
    ctx.conn.commit()

    d = _livres(ctx, pid, 1)[0]
    st, corpo = _post_agenda_novo(pes, med, d)
    assert_eq(st, 200, "página de confirmação (nada escrito ainda)")
    assert_eq("Confirmar destino do atendimento" in corpo, True, "pede confirmação")
    assert_eq("Nada é gravado até você confirmar" in corpo, True, "avisa que não gravou")
    assert_eq(_contagens(ctx, pid)["total"], 5, "nenhuma sessão criada antes de confirmar")
    assert_eq(_planos_da_pessoa(ctx, pes), 1, "nenhum plano criado antes de confirmar")

    st, _ = _post_agenda_novo(pes, med, d, confirmar=1, destino=pid)
    assert_eq(st, 303, "confirmação grava")
    c = _contagens(ctx, pid)
    assert_eq((c["total"], c["extras"], c["agendadas"]), (6, 1, 3),
              "sessão entra NO plano ativo existente")
    assert_eq(_planos_da_pessoa(ctx, pes), 1, "sem plano paralelo")
    assert_eq(_plano(ctx, pid)["sessoes_adicionais"], 1, "extensão registrada")

    # (b) vários planos ativos do MESMO médium exige escolha explícita
    pes2 = ctx.pessoa("k5-varios")
    pb1 = _plano_prospectivo(ctx, med, pes2, 4, inicio=HOJE - timedelta(days=60))
    pb2 = _plano_prospectivo(ctx, med, pes2, 4, inicio=HOJE - timedelta(days=20))
    ctx.conn.commit()
    d2 = _livres(ctx, pb2, 1)[0]
    st, corpo = _post_agenda_novo(pes2, med, d2)
    assert_eq(st, 200, "tela de escolha")
    assert_eq(corpo.count('name="plano_destino"'), 2, "um rádio por plano ativo")
    for pid_ref in (pb1, pb2):
        inicio_br = _plano(ctx, pid_ref)["data_inicio"]
        inicio_br = date.fromisoformat(inicio_br).strftime("%d/%m/%Y")
        assert_eq(inicio_br in corpo, True, "plano referido pelo início, não por id")
    assert_eq(_contagens(ctx, pb1)["total"] + _contagens(ctx, pb2)["total"], 8,
              "nada escrito sem escolha")
    st, _ = _post_agenda_novo(pes2, med, d2, confirmar=1, destino=pb2)
    assert_eq(st, 303, "escolha aceita")
    assert_eq(_contagens(ctx, pb2)["total"], 5, "entrou no plano escolhido")
    assert_eq(_contagens(ctx, pb1)["total"], 4, "o outro plano não foi tocado")

    # (c) plano ativo de OUTRO médium: recusa o vínculo incorreto, sem criar às escondidas
    pes3 = ctx.pessoa("k5-outro")
    pc = _plano_prospectivo(ctx, med, pes3, 3)
    ctx.conn.commit()
    d3 = _livres(ctx, pc, 1)[0]
    antes = _planos_da_pessoa(ctx, pes3)
    st, corpo = _post_agenda_novo(pes3, med2, d3)
    assert_eq(st, 200, "conflito apresentado na tela")
    assert_eq(_planos_da_pessoa(ctx, pes3), antes, "nenhum plano paralelo criado")
    assert_eq(_contagens(ctx, pc)["total"], 3, "nenhuma sessão em plano algum")
    assert_eq("plano ativo com outro médium" in corpo, True,
              "explica o conflito e aponta o plano existente")

    # (d) plano obsoleto na confirmação (id do navegador não é autorização)
    st, corpo = _post_agenda_novo(pes, med, _livres(ctx, pid, 1)[0],
                                  confirmar=1, destino=pc)
    assert_eq(st, 200, "rejeita plano que não é da pessoa")
    assert_eq(_contagens(ctx, pc)["total"], 3, "nada gravado")


# ── K6 · concluído não reabre ────────────────────────────────────────────────

@teste("K6 plano concluído não reabre por inclusão; tela lê 'Concluído'; caminho habitual cria plano novo")
def k6(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k6")
    dia_id = ctx.dia()
    pid = _plano_prospectivo(ctx, med, pes, 3)
    ctx.conn.commit()
    ags = [s["id"] for s in _sessoes(ctx, pid)]
    for i, ag in enumerate(ags):
        _realizar(ctx, pid, med, pes, ag, i + 1, dia_id)
    assert_eq(_plano(ctx, pid)["status"], "concluido", "concluído pelo cumprimento")

    # inclusão na agenda do plano: recusada, sem reabrir e sem gravar
    d = _livres(ctx, pid, 1)[0]
    st, _ = _http("POST", f"/cadastros/mediuns/{med}/planos/{pid}/agenda/novo",
                  {"data": d, "requer_passe": "0", "encaixe": "0"})
    assert_eq(st, 303, "redirect com erro")
    assert_eq(_contagens(ctx, pid)["total"], 3, "nenhuma sessão acrescentada")
    assert_eq((_plano(ctx, pid)["status"], _plano(ctx, pid)["sessoes_adicionais"]),
              ("concluido", 0), "não reabre, não acumula extensão")
    assert_eq(sc.acrescentar_sessao(ctx.conn, pid, _livres(ctx, pid, 1)[0]),
              "plano_nao_ativo", "a transição recusa no serviço (fonte única)")
    ctx.conn.rollback()

    # a tela do plano mostra a situação nova (não a trata como cancelada)
    st, corpo = _http("GET", f"/cadastros/mediuns/{med}/planos/{pid}/agenda")
    assert_eq(st, 200, "agenda do plano abre")
    assert_eq("Concluído pelo cumprimento" in corpo, True, "status Concluído legível")
    assert_eq("Cancelado" in corpo, False, "não é apresentado como cancelado")

    # necessidade real de atendimento: plano NOVO pela porta habitual
    d2 = _livres(ctx, pid, 1)[0]
    st, _ = _post_agenda_novo(pes, med, d2, qtd=2)
    assert_eq(st, 303, "novo plano criado")
    ctx.conn.commit()
    planos = ctx.conn.execute(
        """SELECT pt.id, pt.status, pt.regra_conclusao, pt.sessoes_previstas
           FROM planos_tratamento pt JOIN plano_pessoas pp ON pp.plano_id = pt.id
           WHERE pp.pessoa_id = %s ORDER BY pt.id""", (pes,)).fetchall()
    assert_eq(len(planos), 2, "o antigo segue existindo, o novo está ativo")
    antigo = [p for p in planos if p["id"] == pid][0]
    novo = [p for p in planos if p["id"] != pid][0]
    assert_eq((antigo["status"], novo["status"]), ("concluido", "ativo"),
              "concluído não reabre; novo atendimento é plano novo")
    assert_eq((novo["regra_conclusao"], novo["sessoes_previstas"]), (1, 2),
              "plano novo nasce sob a regra, com quota estável")

    # reenvio da última realizada não altera nada
    _http("POST", f"/dia/atendimento/{_checkins_de(ctx, pid)[-1]}/realizado")
    p = _plano(ctx, pid)
    assert_eq((p["status"], p["sessoes_realizadas"], p["concluido_em"]),
              ("concluido", 3, p["concluido_em"]), "reenvio sem efeito duplicado")


def _checkins_de(ctx, pid):
    ctx.conn.commit()
    return [r["id"] for r in ctx.conn.execute(
        "SELECT id FROM checkins WHERE plano_id = %s ORDER BY id", (pid,)).fetchall()]


# ── K7 · disputa inclusão × última realização ────────────────────────────────

class _Soltas:
    """Conexões do pool fora do contexto, para a prova de concorrência."""

    def __init__(self):
        self.raw = banco._obter_pool().getconn()
        self.raw.cursor_factory = RealDictCursor
        self.conn = banco._ConnCompat(self.raw)

    @property
    def pid(self):
        return self.raw.get_backend_pid()

    def commit(self):
        self.raw.commit()

    def fechar(self):
        try:
            self.raw.rollback()
        except Exception:
            pass
        banco._obter_pool().putconn(self.raw)


def _esperada(conn, pid_espera):
    """Evidência no banco: há back-end bloqueado por lock (coordenação real)."""
    conn.commit()
    r = conn.execute(
        "SELECT wait_event_type, wait_event FROM pg_stat_activity WHERE pid = %s",
        (pid_espera,)).fetchone()
    return r and r["wait_event_type"] == "Lock"


def _fixture_k7(ctx, med, pes):
    """Plano prospectivo com 4 cumpridas e a última pendente (a sessão que
    fecharia o plano) — o estado exato da disputa."""
    pid = _plano_prospectivo(ctx, med, pes, 5)
    ctx.conn.commit()
    ags = [s["id"] for s in _sessoes(ctx, pid)]
    for ag in ags[:4]:
        ctx.conn.execute("UPDATE agendamentos SET status='realizado' WHERE id=%s",
                         (ag,))
    ctx.conn.execute("UPDATE planos_tratamento SET sessoes_realizadas=4 WHERE id=%s",
                     (pid,))
    ctx.conn.commit()
    return pid, ags[4], _livres(ctx, pid, 1)[0]


@teste("K7 disputa (ordem 1): realização vence a inclusão → plano conclui e a inclusão é recusada")
def k7_ordem1(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k7a")
    pid, ag_ultima, d_extra = _fixture_k7(ctx, med, pes)
    admin = ctx.conn.execute(
        "SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()["id"]
    ctx.conn.commit()

    a, b = _Soltas(), _Soltas()
    travou, liberar = threading.Event(), threading.Event()
    saida = {}

    def thread_realiza():
        try:
            sc._travar(a.conn, pid)
            travou.set()
            liberar.wait(20)
            saida["realizacao"] = sc.marcar_realizado(a.conn, pid, ag_ultima, admin)
            a.commit()
        except Exception as e:
            saida["erro_a"] = f"{type(e).__name__}: {e}"
            travou.set()

    def thread_inclui():
        try:
            saida["inclusao"] = sc.acrescentar_sessao(b.conn, pid, d_extra,
                                                      tipo="adicao", autor_id=admin)
            b.commit()
        except Exception as e:
            saida["erro_b"] = f"{type(e).__name__}: {e}"

    ta = threading.Thread(target=thread_realiza)
    tb = threading.Thread(target=thread_inclui)
    ta.start()
    assert travou.wait(10), "thread A não travou o plano"
    tb.start()

    # Evidência da coordenação: B espera o lock do plano (sem dormir por suposição).
    espera = time.monotonic()
    while time.monotonic() - espera < 10 and not _esperada(ctx.conn, b.pid):
        time.sleep(0.02)
    assert_eq(_esperada(ctx.conn, b.pid), True,
              "inclusão bloqueada pela mesma trava do plano")
    liberar.set()
    ta.join(20)
    tb.join(20)
    a.fechar()
    b.fechar()

    assert_eq(saida.get("erro_a"), None, f"thread A falhou: {saida.get('erro_a')}")
    assert_eq(saida.get("erro_b"), None, f"thread B falhou: {saida.get('erro_b')}")
    assert_eq(saida.get("realizacao"), "concluido", "última realizada concluiu")
    assert_eq(saida.get("inclusao"), "plano_nao_ativo",
              "quem chegou depois vê o estado confirmado e é recusado")
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq(p["status"], "concluido", "situação final")
    assert_eq((c["total"], c["realizadas"], c["agendadas"], c["extras"]),
              (5, 5, 0, 0), "efeitos integrais: nada parcial, nada duplicado")
    assert_eq(p["sessoes_adicionais"], 0, "extensão não ficou acumulada")


@teste("K7 disputa (ordem 2): inclusão vence a última realização → plano segue aberto, sem fechar cedo")
def k7_ordem2(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k7b")
    pid, ag_ultima, d_extra = _fixture_k7(ctx, med, pes)
    admin = ctx.conn.execute(
        "SELECT id FROM atendentes WHERE nome_usuario='admin'").fetchone()["id"]
    ctx.conn.commit()

    a, b = _Soltas(), _Soltas()
    travou, liberar = threading.Event(), threading.Event()
    saida = {}

    def thread_inclui():
        try:
            sc._travar(a.conn, pid)
            travou.set()
            liberar.wait(20)
            saida["inclusao"] = sc.acrescentar_sessao(a.conn, pid, d_extra,
                                                      tipo="adicao", autor_id=admin)
            a.commit()
        except Exception as e:
            saida["erro_a"] = f"{type(e).__name__}: {e}"
            travou.set()

    def thread_realiza():
        try:
            saida["realizacao"] = sc.marcar_realizado(b.conn, pid, ag_ultima, admin)
            b.commit()
        except Exception as e:
            saida["erro_b"] = f"{type(e).__name__}: {e}"

    ta = threading.Thread(target=thread_inclui)
    tb = threading.Thread(target=thread_realiza)
    ta.start()
    assert travou.wait(10), "thread A não travou o plano"
    tb.start()
    espera = time.monotonic()
    while time.monotonic() - espera < 10 and not _esperada(ctx.conn, b.pid):
        time.sleep(0.02)
    assert_eq(_esperada(ctx.conn, b.pid), True,
              "última realização bloqueada pela mesma trava do plano")
    liberar.set()
    ta.join(20)
    tb.join(20)
    a.fechar()
    b.fechar()

    assert_eq(saida.get("erro_a"), None, f"thread A falhou: {saida.get('erro_a')}")
    assert_eq(saida.get("erro_b"), None, f"thread B falhou: {saida.get('erro_b')}")
    assert_eq(saida.get("inclusao"), "ok", "extensão gravada")
    assert_eq(saida.get("realizacao"), "nada",
              "com a extra pendente, a última prevista não conclui o plano")
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq(p["status"], "ativo", "plano permanece aberto")
    assert_eq((c["total"], c["realizadas"], c["agendadas"]), (6, 5, 1),
              "sessão extra convive com as cumpridas")
    assert_eq((p["sessoes_adicionais"], p["sessoes_realizadas"]), (1, 5),
              "contadores coerentes")


# ── K8 · permissões ──────────────────────────────────────────────────────────

MODULO_PLANOS = "cadastros.mediuns.planos"


@teste("K8 permissões: Recepção grava sessão no plano; somente-leitura e sem autorização não")
def k8(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, med2 = ctx.medium(), ctx.medium()
    pes = ctx.pessoa("k8-paciente")
    pid = _plano_prospectivo(ctx, med, pes, 3)
    ctx.conn.commit()
    d = _livres(ctx, pid, 1)[0]

    # (a) Recepção com o módulo da TASK_017: acrescenta sessão no plano
    g = _grupo(ctx, "Recepção K8")
    _perm(ctx, g, MODULO_PLANOS, ler=True, escrever=True)
    _usuario_em_grupo(ctx, "recep_k8", g)
    ctx.conn.commit()
    sess = _nova_sessao("recep_k8", "teste123")
    st, _ = sess.post(f"/cadastros/mediuns/{med}/planos/{pid}/agenda/novo",
                      {"data": d, "requer_passe": "0", "encaixe": "0"})
    assert_eq(st, 303, "Recepção lança pela agenda do plano")
    c = _contagens(ctx, pid)
    assert_eq((c["total"], c["extras"]), (4, 1), "sessão gravada no plano")
    assert_eq(_plano(ctx, pid)["sessoes_adicionais"], 1, "extensão registrada")

    # (b) Recepção somente-leitura: navega, mas POST é 403 e nada muda
    g2 = _grupo(ctx, "Recepção leitura K8")
    _perm(ctx, g2, MODULO_PLANOS, ler=True, escrever=False)
    _usuario_em_grupo(ctx, "leitura_k8", g2)
    ctx.conn.commit()
    sess2 = _nova_sessao("leitura_k8", "teste123")
    st, _ = sess2(f"/cadastros/mediuns/{med}/planos/{pid}/agenda")
    assert_eq(st, 200, "leitura liberada")
    st, _ = sess2.post(f"/cadastros/mediuns/{med}/planos/{pid}/agenda/novo",
                       {"data": _livres(ctx, pid, 1)[0], "requer_passe": "0",
                        "encaixe": "0"})
    assert_eq(st, 403, "gravação negada a somente-leitura")
    assert_eq(_contagens(ctx, pid)["total"], 4, "nada gravado")

    # (c) usuário sem autorização: 403 na tela e no POST
    g3 = _grupo(ctx, "Sem acesso K8")
    _usuario_em_grupo(ctx, "nada_k8", g3)
    ctx.conn.commit()
    sess3 = _nova_sessao("nada_k8", "teste123")
    st, _ = sess3(f"/cadastros/mediuns/{med}/planos/{pid}/agenda")
    assert_eq(st in (403, 303), True, "tela negada sem autorização")
    st, _ = sess3.post(f"/cadastros/mediuns/{med}/planos/{pid}/agenda/novo",
                       {"data": d, "requer_passe": "0", "encaixe": "0"})
    assert_eq(st, 403, "POST negado sem autorização")
    assert_eq(_contagens(ctx, pid)["total"], 4, "nada gravado")

    # (d) troca indevida de médium na URL (plano não é do médium da rota)
    st, _ = sess.post(f"/cadastros/mediuns/{med2}/planos/{pid}/agenda/novo",
                      {"data": _livres(ctx, pid, 1)[0], "requer_passe": "0",
                       "encaixe": "0"})
    assert_eq(st, 403, "plano de outro médium na URL: negado")
    assert_eq(_contagens(ctx, pid)["total"], 4, "nada gravado")

    # (e) quem só tem "agenda" escreve pela Agenda, mas não recebe a porta
    #     que não pode atravessar (link para a agenda do plano)
    g4 = _grupo(ctx, "Só Agenda K8")
    _perm(ctx, g4, "agenda", ler=True, escrever=True)
    _usuario_em_grupo(ctx, "agendista_k8", g4)
    ctx.conn.commit()
    sess4 = _nova_sessao("agendista_k8", "teste123")
    st, corpo = sess4(f"/agenda?pessoa_id={pes}")
    assert_eq(st, 200, "agenda abre")
    st, corpo = sess4.post("/agenda/novo", {
        "pessoa_id": str(pes), "data": _livres(ctx, pid, 1)[0],
        "medium_id": str(med), "data_de": "", "data_ate": "", "dia_semana": "-1",
        "pessoa_nome": "", "qtd_sessoes": "1", "frequencia": "semanal",
        "confirmar": "0", "plano_destino": "0"})
    assert_eq(st, 200, "página de confirmação")
    assert_eq("Abrir agenda deste plano" in corpo, False,
              "não se oferece porta que o usuário não tem")
    assert_eq(_contagens(ctx, pid)["total"], 4, "nada gravado sem confirmar")


# ── K9 · migração idempotente e fronteira prospectiva ────────────────────────

@teste("K9 migração repetida não altera fatos; legado não passa a concluir por acidente")
def k9(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med = ctx.medium()
    pes = ctx.pessoa("k9-legado")
    pid = ctx.plano(med, pes, 5, "semanal", 3, inicio=HOJE - timedelta(days=40))
    # fatos históricos variados (realizado, falta, cancelado, pendente)
    sg = _sessoes(ctx, pid)
    ctx.conn.execute("UPDATE agendamentos SET status='realizado' WHERE id=%s", (sg[0]["id"],))
    ctx.conn.execute("UPDATE agendamentos SET status='faltou' WHERE id=%s", (sg[1]["id"],))
    ctx.conn.execute("UPDATE agendamentos SET status='cancelado' WHERE id=%s", (sg[2]["id"],))
    ctx.conn.execute("UPDATE planos_tratamento SET sessoes_realizadas=1 WHERE id=%s", (pid,))
    # um plano legado já dado por alta (bandeira int antiga)
    alta = ctx.plano(med, ctx.pessoa("k9-alta"), 2, "semanal", 2,
                     inicio=HOJE - timedelta(days=60))
    ctx.conn.execute("UPDATE planos_tratamento SET concluido=1 WHERE id=%s", (alta,))
    ctx.conn.commit()

    antes_plano = dict(_plano(ctx, pid))
    antes_alta = dict(_plano(ctx, alta))
    antes_sessoes = [dict(r) for r in _sessoes(ctx, pid)] + \
                    [dict(r) for r in _sessoes(ctx, alta)]

    banco._migrar(ctx.conn)          # startup
    ctx.conn.commit()
    banco._migrar(ctx.conn)          # startup repetido (idempotência)
    ctx.conn.commit()

    depois_sessoes = [dict(r) for r in _sessoes(ctx, pid)] + \
                     [dict(r) for r in _sessoes(ctx, alta)]
    assert_eq(depois_sessoes, antes_sessoes,
              "agendamentos históricos idênticos após migrações repetidas")
    p = dict(_plano(ctx, pid))
    a = dict(_plano(ctx, alta))
    p_pop = {k: v for k, v in p.items() if k in ("sessoes_previstas", "regra_conclusao")}
    p_sem_novos = {k: v for k, v in p.items()
                   if k not in ("sessoes_previstas", "sessoes_adicionais",
                                "regra_conclusao", "concluido_em", "concluido_por",
                                "conclusao_origem", "conclusao_desfeita_em",
                                "conclusao_desfeita_por")}
    assert_eq(p_sem_novos, {k: v for k, v in antes_plano.items()
                            if k in p_sem_novos},
              "fatos do plano legado preservados")
    assert_eq(p_pop["regra_conclusao"], 0, "legado fica FORA da regra prospectiva")
    assert_eq(p_pop["sessoes_previstas"], antes_plano["sessoes_total"],
              "quota original copiada uma vez (backfill)")
    assert_eq((a["status"], a["regra_conclusao"]), ("alta", 0),
              "alta legada preservada, sem virar conclusão automática")

    # mesmo com tudo cumprido, o legado não fecha sozinho
    for s in _sessoes(ctx, pid):
        ctx.conn.execute("UPDATE agendamentos SET status='realizado' WHERE id=%s",
                         (s["id"],))
    ctx.conn.commit()
    assert_eq(sc.reavaliar(ctx.conn, pid), "nada", "legado não conclui automaticamente")
    assert_eq(_plano(ctx, pid)["status"], "ativo")

    # e a Agenda não passa a exigir destino de plano legado por engano:
    # o resumo humano continua dizendo a situação
    st, corpo = _post_agenda_novo(pes, med, _livres(ctx, pid, 1)[0])
    assert_eq(st, 200, "confirmação apresentada")
    assert_eq("iniciado antes da regra de" in corpo, True,
              "avisa que o plano é legado (não fecha sozinho)")


# ── K10 · desfazer reavaliado ────────────────────────────────────────────────

@teste("K10 desfazer a última realizada reabre conclusão automática, de forma auditada")
def k10a(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k10a")
    dia_id = ctx.dia()
    pid = _plano_prospectivo(ctx, med, pes, 3)
    ctx.conn.commit()
    ags = [s["id"] for s in _sessoes(ctx, pid)]
    cks = [_realizar(ctx, pid, med, pes, ag, i + 1, dia_id)
           for i, ag in enumerate(ags)]
    assert_eq(_plano(ctx, pid)["status"], "concluido", "concluído")

    st, _ = _http("POST", f"/dia/atendimento/{cks[-1]}/desfazer")
    assert_eq(st, 303, "desfazer")
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq(p["status"], "ativo", "reaberto pela correção do fato")
    assert_eq((c["realizadas"], c["agendadas"], p["sessoes_realizadas"]),
              (2, 1, 2), "sessão volta a ser pendente, contador coerente")
    assert_eq(p["conclusao_origem"], None, "conclusão desfeita: origem limpa")
    assert_eq(p["concluido_em"], None, "sem registro de conclusão vigente")
    assert_eq(bool(p["conclusao_desfeita_em"]), True, "desfazimento auditado")
    assert_eq(p["conclusao_desfeita_por"] is not None, True, "autoria do desfazer")
    ctx.conn.commit()
    feito = ctx.conn.execute(
        "SELECT atendimento_realizado FROM checkins WHERE id=%s",
        (cks[-1],)).fetchone()["atendimento_realizado"]
    assert_eq(feito, 0, "o atendimento em si também foi desfeito")


@teste("K10 alta clínica e encerramento manual não são revertidos pelo desfazer")
def k10b(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k10b")
    pid = _plano_prospectivo(ctx, med, pes, 3)
    ctx.conn.commit()
    for s in _sessoes(ctx, pid):
        ctx.conn.execute("UPDATE agendamentos SET status='realizado' WHERE id=%s",
                         (s["id"],))
    ctx.conn.execute("UPDATE planos_tratamento SET sessoes_realizadas=3 WHERE id=%s",
                     (pid,))
    # encerramento/clínica: status decidido por humano, sem origem automática
    ctx.conn.execute(
        "UPDATE planos_tratamento SET status='alta', conclusao_origem=NULL "
        "WHERE id=%s", (pid,))
    ctx.conn.commit()
    assert_eq(sc.reavaliar(ctx.conn, pid), "nada", "alta não se reavalia sozinha")
    assert_eq(_plano(ctx, pid)["status"], "alta")
    ctx.conn.execute(
        "UPDATE planos_tratamento SET status='concluido', conclusao_origem=NULL "
        "WHERE id=%s", (pid,))
    ctx.conn.commit()
    assert_eq(sc.reavaliar(ctx.conn, pid), "nada",
              "conclusão manual não é desfeita automaticamente")
    assert_eq(_plano(ctx, pid)["status"], "concluido")


@teste("K10 desfazer que reabriaria plano com outro ativo é recusado, sem efeitos parciais")
def k10c(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k10c")
    dia_id = ctx.dia()
    pid = _plano_prospectivo(ctx, med, pes, 3)
    ctx.conn.commit()
    ags = [s["id"] for s in _sessoes(ctx, pid)]
    cks = [_realizar(ctx, pid, med, pes, ag, i + 1, dia_id)
           for i, ag in enumerate(ags)]
    assert_eq(_plano(ctx, pid)["status"], "concluido", "concluído automaticamente")

    # surge outro plano ativo para a mesma pessoa (acolhimento novo)
    outro = _plano_prospectivo(ctx, med, pes, 4, inicio=HOJE + timedelta(days=7))
    ctx.conn.commit()
    antes = dict(_plano(ctx, pid))
    c_antes = _contagens(ctx, pid)

    st, _ = _http("POST", f"/dia/atendimento/{cks[-1]}/desfazer")
    assert_eq(st, 303, "redirect com orientação de revisão")
    p = dict(_plano(ctx, pid))
    c = _contagens(ctx, pid)
    assert_eq(p["status"], "concluido", "dois ativos não são criados em silêncio")
    assert_eq({k: p[k] for k in antes}, antes, "plano intacto — rollback total")
    assert_eq((c["realizadas"], c["agendadas"], p["sessoes_realizadas"]),
              (c_antes["realizadas"], c_antes["agendadas"], antes["sessoes_realizadas"]),
              "nenhum efeito parcial: nem sessão, nem contador")
    ctx.conn.commit()
    feito = ctx.conn.execute(
        "SELECT atendimento_realizado FROM checkins WHERE id=%s",
        (cks[-1],)).fetchone()["atendimento_realizado"]
    assert_eq(feito, 1, "o desfazer do checkin também foi desfeito (rollback)")
    assert_eq(_plano(ctx, outro)["status"], "ativo", "o plano novo não foi tocado")


# ── K11 · cancelamento ───────────────────────────────────────────────────────

@teste("K11 cancelar extensão ainda pendente revoga a extensão; cancelar prevista não reduz a previsão")
def k11(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k11")
    dia_id = ctx.dia()
    pid = _plano_prospectivo(ctx, med, pes, 3)
    ctx.conn.commit()
    ags = [s["id"] for s in _sessoes(ctx, pid)]
    for i, ag in enumerate(ags[:2]):
        _realizar(ctx, pid, med, pes, ag, i + 1, dia_id)

    d_extra = _livres(ctx, pid, 1)[0]
    st, _ = _http("POST", f"/cadastros/mediuns/{med}/planos/{pid}/agenda/novo",
                  {"data": d_extra, "requer_passe": "0", "encaixe": "0"})
    assert_eq(st, 303, "extensão incluída")
    assert_eq(_plano(ctx, pid)["sessoes_adicionais"], 1, "extensão contada")
    extra = [s for s in _sessoes(ctx, pid) if s["data"] == d_extra][0]
    assert_eq(extra["adicionada"], 1, "a linha sabe que é extra")

    st, _ = _http("POST",
                  f"/cadastros/mediuns/{med}/planos/{pid}/agenda/{extra['id']}/cancelar")
    assert_eq(st, 303, "cancelamento da extensão")
    p = _plano(ctx, pid)
    assert_eq(p["sessoes_adicionais"], 0, "cancelar extensão revoga a extensão")
    assert_eq(p["sessoes_previstas"], 3, "a previsão original não é reduzida")

    # cumprindo as três previstas, o plano fecha (a extra revogada não pesa)
    _realizar(ctx, pid, med, pes, ags[2], 3, dia_id)
    p = _plano(ctx, pid)
    assert_eq((p["status"], p["sessoes_realizadas"]), ("concluido", 3),
              "quota real cumprida fecha o plano")

    # cancelar sessão PREVISTA não vira cumprimento nem encurta a obrigação
    med2, pes2 = ctx.medium(), ctx.pessoa("k11b")
    pid2 = _plano_prospectivo(ctx, med2, pes2, 3)
    ctx.conn.commit()
    sg2 = _sessoes(ctx, pid2)
    for i, s in enumerate(sg2[:2]):
        _realizar(ctx, pid2, med2, pes2, s["id"], i + 1, dia_id)
    st, _ = _http("POST",
                  f"/cadastros/mediuns/{med2}/planos/{pid2}/agenda/{sg2[2]['id']}/cancelar")
    assert_eq(st, 303, "cancelamento de prevista")
    p2 = _plano(ctx, pid2)
    c2 = _contagens(ctx, pid2)
    assert_eq(p2["status"], "ativo", "cancelamento não conclui")
    assert_eq((p2["sessoes_previstas"], p2["sessoes_adicionais"]), (3, 0),
              "obrigação permanece; cancelamento não é cumprido nem perdoado")
    assert_eq((c2["realizadas"], c2["canceladas"]), (2, 1), "fatos preservados")
    assert_eq(sc.reavaliar(ctx.conn, pid2), "nada", "e não fecha por suposição")
    ctx.conn.commit()


# ── K12 · leitura nunca conclui ──────────────────────────────────────────────

@teste("K12 GET/render/abertura de tela não avaliam nem fecham plano; a conclusão é só na transição")
def k12(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k12")
    dia_id = ctx.dia()
    pid = _plano_prospectivo(ctx, med, pes, 3)
    ctx.conn.commit()
    sg = _sessoes(ctx, pid)
    # estado "concluível" montado por fatos reais via transição, exceto o último:
    cks = [_realizar(ctx, pid, med, pes, s["id"], i + 1, dia_id)
           for i, s in enumerate(sg[:2])]
    assert_eq(_plano(ctx, pid)["status"], "ativo", "2 de 3")
    # a 3ª realizada é gravada como fato, sem passar pela transição
    ctx.conn.execute("UPDATE agendamentos SET status='realizado' WHERE id=%s",
                     (sg[2]["id"],))
    ctx.conn.commit()

    for path in (f"/cadastros/mediuns/{med}/planos/{pid}/agenda", f"/agenda?pessoa_id={pes}",
                 "/dia", f"/cadastros/pessoas/{pes}", f"/agenda/medium?medium_id={med}",
                 "/dia/acolhimento"):
        st, _ = _http("GET", path)
        assert_eq(st in (200, 303), True, f"GET {path}")
    p = _plano(ctx, pid)
    assert_eq(p["status"], "ativo",
              "nenhuma conclusão no caminho de leitura/render")
    assert_eq(p["concluido_em"], None, "sem evento de conclusão em GET")

    # a transição real (desfazer e refazer a última) é o que avalia e fecha
    _http("POST", f"/dia/atendimento/{cks[-1]}/desfazer")
    _http("POST", f"/dia/atendimento/{cks[-1]}/realizado")
    p = _plano(ctx, pid)
    assert_eq(p["status"], "concluido",
              "a conclusão acontece no ponto de transição, não na tela")


# ── K13 · os quatro criadores de plano, pelos caminhos reais ─────────────────

def _datas_atendimento(ctx, n=1, a_partir=0):
    """Datas que são dia de atendimento cadastrado (sem depender de plano)."""
    ctx.conn.commit()
    permitidos = {r["dia_semana"] for r in ctx.conn.execute(
        "SELECT dia_semana FROM dias_atendimento").fetchall()}
    out, i = [], 0
    while len(out) < n and i < 200:
        dia = HOJE + timedelta(days=a_partir + i)
        i += 1
        if dia.weekday() in permitidos:
            out.append(dia)
    assert_eq(len(out), n, "datas de atendimento disponíveis")
    return out


def _conferir_plano_novo(ctx, nome, pid, med, pes, dia_id, total, base):
    """O plano criado vive sob a regra: metadado persistido E conclusão
    automática ao cumprir o previsto — não basta a coluna estar preenchida."""
    p = _plano(ctx, pid)
    assert_eq(p["regra_conclusao"], 1, f"{nome}: nasce sob a regra de conclusão")
    assert_eq(p["sessoes_previstas"], total, f"{nome}: quota original persistida")
    assert_eq(p["sessoes_total"], total, f"{nome}: total coincide com a quota")
    assert_eq(p["sessoes_adicionais"], 0, f"{nome}: nasce sem extensão")
    assert_eq(p["status"], "ativo", f"{nome}: nasce ativo")
    sg = _sessoes(ctx, pid)
    assert_eq(len(sg), total, f"{nome}: agenda gerada com o previsto")
    for i, s in enumerate(sg):
        _realizar(ctx, pid, med, pes, s["id"], base + i, dia_id)
    p = _plano(ctx, pid)
    assert_eq((p["status"], p["sessoes_realizadas"]), ("concluido", total),
              f"{nome}: cumprido o previsto, conclui automaticamente")
    assert_eq(p["conclusao_origem"], sc.ORIGEM_AUTOMATICA, f"{nome}: origem registrada")


@teste("K13 os quatro criadores de plano gravam regra_conclusao e sessoes_previstas pelos caminhos reais")
def k13(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med = ctx.medium()
    dia_id = ctx.dia()
    total = 3

    # 1 · POST /agenda/novo sem plano ativo — a tela cria o plano (caso do
    #     incidente: era aqui que nascia o plano paralelo).
    pes1 = ctx.pessoa("k13-agenda")
    d1 = _datas_atendimento(ctx, 1)[0]
    st, _ = _post_agenda_novo(pes1, med, d1.isoformat(), qtd=total)
    assert_eq(st, 303, "POST /agenda/novo criou o plano")
    assert_eq(_planos_da_pessoa(ctx, pes1), 1, "um plano só")
    pid1 = _plano_da_pessoa(ctx, pes1)["id"]
    _conferir_plano_novo(ctx, "/agenda/novo", pid1, med, pes1, dia_id, total, 11)

    # 2 · POST /agenda/medium/lancar sem plano ativo — lançamento de papel.
    pes2 = ctx.pessoa("k13-medium")
    d2 = _datas_atendimento(ctx, 1)[0]
    st, _ = _post_lancar_medium(pes2, med, d2.isoformat(), qtd=total)
    assert_eq(st, 303, "POST /agenda/medium/lancar criou o plano")
    assert_eq(_planos_da_pessoa(ctx, pes2), 1, "um plano só")
    pid2 = _plano_da_pessoa(ctx, pes2)["id"]
    _conferir_plano_novo(ctx, "/agenda/medium/lancar", pid2, med, pes2, dia_id,
                         total, 21)

    # 3 · POST /dia/fraterno/{checkin} — acolhimento fraterno.
    pes3 = ctx.pessoa("k13-fraterno")
    ck3 = ctx.checkin(dia_id, pes3, "301")
    d3 = _datas_atendimento(ctx, 1)[0]
    st, _ = _post_fraterno(ck3, med, total, d3)
    assert_eq(st, 303, "POST /dia/fraterno criou o plano")
    assert_eq(_planos_da_pessoa(ctx, pes3), 1, "um plano só")
    pid3 = _plano_da_pessoa(ctx, pes3)["id"]
    _conferir_plano_novo(ctx, "/dia/fraterno", pid3, med, pes3, dia_id, total, 31)

    # 4 · POST /cadastros/mediuns/{id}/planos/novo — plano pela tela do médium.
    pes4 = ctx.pessoa("k13-medium-plano")
    d4 = _datas_atendimento(ctx, 1)[0]
    st, _ = _post_plano_medium(med, pes4, total, d4)
    assert_eq(st, 303, "POST /cadastros/mediuns/{m}/planos/novo criou o plano")
    assert_eq(_planos_da_pessoa(ctx, pes4), 1, "um plano só")
    pid4 = _plano_da_pessoa(ctx, pes4)["id"]
    _conferir_plano_novo(ctx, "/cadastros/mediuns/{m}/planos/novo", pid4, med,
                         pes4, dia_id, total, 41)

    # Reenvio do acolhimento (mesmo checkin+médium+início) não cria segundo
    # plano nem metadado divergente.
    st, _ = _post_fraterno(ck3, med, total, d3)
    assert_eq(_planos_da_pessoa(ctx, pes3), 1, "idempotência preservada")
    assert_eq(_plano(ctx, pid3)["sessoes_previstas"], total, "quota inalterada")


# ── K14 · reposição pelo formulário da agenda do plano ───────────────────────

@teste("K14 reposição pelo formulário da recepção: cumpre a obrigação original sem virar extensão")
def k14(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k14")
    dia_id = ctx.dia()
    pid = _plano_prospectivo(ctx, med, pes, 5)
    ctx.conn.commit()
    sg = _sessoes(ctx, pid)
    agenda = f"/cadastros/mediuns/{med}/planos/{pid}/agenda"

    assert_eq(sc.vagas_a_repor(ctx.conn, pid), 0, "sem falta, nenhuma vaga")
    st, corpo = _http("GET", agenda)
    assert_eq(st, 200, "GET da agenda do plano")
    assert_eq('value="reposicao"' in corpo, False,
              "sem vaga em aberto o formulário não oferece reposição")

    # Falta registrada pela rota real da agenda do plano.
    st, _ = _http("POST", f"{agenda}/{sg[0]['id']}/falta")
    assert_eq(st, 303, "rota de falta")
    ctx.conn.commit()
    assert_eq(sc.vagas_a_repor(ctx.conn, pid), 1, "a falta abriu vaga de reposição")
    st, corpo = _http("GET", agenda)
    assert_eq('value="reposicao"' in corpo, True,
              "a tela passou a oferecer a reposição")
    assert_eq("Reposição de sessão não cumprida" in corpo, True,
              "a opção nomeia o que é, sem jargão de extensão")

    # POST exatamente como o formulário o envia (mesmo action e campos).
    d_rep = _livres(ctx, pid, 1)[0]
    st, local, corpo = _http_ate_o_destino(
        "POST", f"{agenda}/novo",
        {"data": d_rep, "tipo": "reposicao", "requer_passe": "0", "encaixe": "0"})
    assert_eq(st, 200, "o redirect levou a uma página renderizada")
    assert_eq(agenda in (local or ""), True, f"voltou para a agenda do plano: {local}")
    assert_eq("Reposição agendada" in corpo, True,
              "a mensagem de reposição aparece na tela")
    linhas = _sessoes(ctx, pid)
    rep = [s for s in linhas if s["repoe"] == 1]
    assert_eq(len(rep), 1, "uma linha marcada como reposição")
    assert_eq(rep[0]["adicionada"], 0, "reposição não é linha de extensão")
    assert_eq(rep[0]["data"], d_rep, "na data escolhida")
    p = _plano(ctx, pid)
    assert_eq((p["sessoes_adicionais"], p["sessoes_previstas"]), (0, 5),
              "nada aumentou o que precisa ser cumprido")
    assert_eq(sc.vagas_a_repor(ctx.conn, pid), 0,
              "a vaga passou a estar coberta pela reposição em curso")

    # As outras quatro previstas + a reposição fecham o plano em 5 cumpridas.
    previstas_restantes = [x for x in linhas
                           if x["status"] == "agendado" and x["repoe"] == 0]
    assert_eq(len(previstas_restantes), 4, "quatro previstas ainda em aberto")
    for i, s in enumerate(previstas_restantes):
        _realizar(ctx, pid, med, pes, s["id"], 200 + i, dia_id)
    p = _plano(ctx, pid)
    assert_eq(p["status"], "ativo", "4 cumpridas de 5 — ainda deve a reposição")
    _realizar(ctx, pid, med, pes, rep[0]["id"], 299, dia_id)
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq(p["status"], "concluido",
              "a reposição cumpriu a obrigação original e o plano fechou")
    assert_eq((p["sessoes_adicionais"], p["sessoes_previstas"],
               p["sessoes_realizadas"]), (0, 5, 5),
              "sem extensão: 5 de 5, não 5 de 6")
    assert_eq((c["realizadas"], c["faltas"], c["reposicoes"]), (5, 1, 1),
              "a falta continua falta; a reposição é uma linha própria")


# ── K15 · recusas e reabertura da vaga, pela rota real ───────────────────────

@teste("K15 sem vaga a rota recusa com mensagem; cancelar a reposição reabre a vaga; tipo inválido não escreve")
def k15(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k15")
    pid = _plano_prospectivo(ctx, med, pes, 3)
    ctx.conn.commit()
    agenda = f"/cadastros/mediuns/{med}/planos/{pid}/agenda"
    sg = _sessoes(ctx, pid)

    # (a) sem vaga: recusa, nada gravado, mensagem legível na tela
    d = _livres(ctx, pid, 1)[0]
    st, local, corpo = _http_ate_o_destino(
        "POST", f"{agenda}/novo",
        {"data": d, "tipo": "reposicao", "requer_passe": "0", "encaixe": "0"})
    assert_eq(st, 200, "pagina de destino renderizada")
    assert_eq("Não há sessão prevista em aberto para repor" in corpo, True,
              "a recusa diz o porquê")
    c = _contagens(ctx, pid)
    p = _plano(ctx, pid)
    assert_eq((c["total"], c["reposicoes"], c["extras"]), (3, 0, 0),
              "nenhuma linha foi gravada na recusa")
    assert_eq((p["sessoes_adicionais"], p["status"]), (0, "ativo"),
              "e o plano não mudou")

    # (b) tipo que não existe no contrato: recusado, sem escrita
    st, local, corpo = _http_ate_o_destino(
        "POST", f"{agenda}/novo",
        {"data": d, "tipo": "invencao", "requer_passe": "0", "encaixe": "0"})
    assert_eq("Tipo de sessão desconhecido" in corpo, True, "aviso de tipo inválido")
    assert_eq(_contagens(ctx, pid)["total"], 3, "nada escrito por tipo inválido")

    # (c) falta → vaga → reposição → cancelar a reposição reabre a vaga
    st, _ = _http("POST", f"{agenda}/{sg[0]['id']}/falta")
    assert_eq(st, 303, "falta registrada")
    ctx.conn.commit()
    assert_eq(sc.vagas_a_repor(ctx.conn, pid), 1, "vaga aberta pela falta")
    st, local, corpo = _http_ate_o_destino(
        "POST", f"{agenda}/novo",
        {"data": d, "tipo": "reposicao", "requer_passe": "0", "encaixe": "0"})
    assert_eq("Reposição agendada" in corpo, True, "reposição aceita")
    ctx.conn.commit()
    assert_eq(sc.vagas_a_repor(ctx.conn, pid), 0, "vaga coberta")
    assert_eq(_plano(ctx, pid)["sessoes_adicionais"], 0, "segue sem extensão")

    rep_id = [s["id"] for s in _sessoes(ctx, pid) if s["repoe"] == 1][0]
    st, _ = _http("POST", f"{agenda}/{rep_id}/cancelar")
    assert_eq(st, 303, "cancelamento da reposição")
    ctx.conn.commit()
    assert_eq(sc.vagas_a_repor(ctx.conn, pid), 1,
              "cancelar a reposição reabre a vaga — a obrigação volta a pedir substituta")
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq((p["sessoes_adicionais"], p["sessoes_previstas"]), (0, 3),
              "cancelar reposição não cria nem revoga extensão")
    assert_eq((c["total"], c["canceladas"], c["reposicoes"]), (4, 1, 1),
              "a linha repostas fica registrada como cancelada (histórico preservado)")
    st, corpo = _http("GET", agenda)
    assert_eq('value="reposicao"' in corpo, True,
              "a tela volta a oferecer a reposição")

    # (d) e a reposição reagendada no lugar: cancelar a PREVISTA também abre vaga
    med2, pes2 = ctx.medium(), ctx.pessoa("k15b")
    pid2 = _plano_prospectivo(ctx, med2, pes2, 3)
    ctx.conn.commit()
    agenda2 = f"/cadastros/mediuns/{med2}/planos/{pid2}/agenda"
    sg2 = _sessoes(ctx, pid2)
    st, _ = _http("POST", f"{agenda2}/{sg2[0]['id']}/cancelar")
    assert_eq(st, 303, "cancelamento de sessão prevista")
    ctx.conn.commit()
    assert_eq(sc.vagas_a_repor(ctx.conn, pid2), 1,
              "sessão prevista cancelada fica aguardando reposição")
    assert_eq(_plano(ctx, pid2)["sessoes_previstas"], 3,
              "cancelar prevista não encurta a obrigação")


# ── K16 · reposição pelos outros dois POSTs da recepção ──────────────────────

@teste("K16 a confirmação da Agenda e o lançamento do médium repõem por escolha explícita, um plano de cada vez")
def k16(ctx):
    ctx.reset(dias_semana=(0, 1, 2, 3, 4))
    med, pes = ctx.medium(), ctx.pessoa("k16")
    dia_id = ctx.dia()
    pid = _plano_prospectivo(ctx, med, pes, 5)
    ctx.conn.commit()
    sg = _sessoes(ctx, pid)
    st, _ = _http("POST",
                  f"/cadastros/mediuns/{med}/planos/{pid}/agenda/{sg[0]['id']}/falta")
    assert_eq(st, 303, "falta abriu vaga")
    ctx.conn.commit()

    # ── (a) /agenda/novo: a página de confirmação oferece a escolha e o POST
    #         respeita o que foi escolhido.
    d = _livres(ctx, pid, 1)[0]
    st, corpo = _post_agenda_novo(pes, med, d, qtd=1)
    assert_eq(st, 200, "página de confirmação")
    assert_eq(f'name="tipo_{pid}"' in corpo, True,
              "a confirmação pergunta o tipo de sessão deste plano")
    assert_eq('value="reposicao"' in corpo, True,
              "com vaga em aberto, a reposição é oferecida")
    st, _ = _post_agenda_novo(pes, med, d, confirmar=1, destino=pid,
                              tipo="reposicao")
    assert_eq(st, 303, "POST confirmado")
    linhas = _sessoes(ctx, pid)
    rep = [s for s in linhas if s["repoe"] == 1]
    assert_eq(len(rep), 1, "reposição gravada pela Agenda")
    assert_eq(rep[0]["adicionada"], 0, "não é extensão")
    p = _plano(ctx, pid)
    assert_eq((p["sessoes_adicionais"], p["sessoes_previstas"]), (0, 5),
              "a obrigação original permanece 5, não virou 6")
    _realizar(ctx, pid, med, pes, rep[0]["id"], 320, dia_id)
    assert_eq(_plano(ctx, pid)["status"], "ativo", "ainda faltam as outras previstas")
    for i, s in enumerate([x for x in _sessoes(ctx, pid)
                           if x["status"] == "agendado"]):
        _realizar(ctx, pid, med, pes, s["id"], 330 + i, dia_id)
    p = _plano(ctx, pid)
    c = _contagens(ctx, pid)
    assert_eq((p["status"], c["realizadas"], p["sessoes_realizadas"]),
              ("concluido", 5, 5), "5 cumpridas de 5, com a falta preservada")

    # ── (b) dois planos ativos: a escolha é POR plano, e só o escolhido repõe.
    med2, pes2 = ctx.medium(), ctx.pessoa("k16b")
    a = _plano_prospectivo(ctx, med2, pes2, 4)
    b = _plano_prospectivo(ctx, med2, pes2, 3, inicio=HOJE + timedelta(days=7))
    ctx.conn.commit()
    sa, sb = _sessoes(ctx, a), _sessoes(ctx, b)
    for pid_x, sg_x in ((a, sa), (b, sb)):
        st, _ = _http("POST",
                      f"/cadastros/mediuns/{med2}/planos/{pid_x}/agenda/{sg_x[0]['id']}/falta")
        assert_eq(st, 303, "falta em cada plano")
    ctx.conn.commit()
    d2 = _livres(ctx, a, 1)[0]
    st, corpo = _post_agenda_novo(pes2, med2, d2, qtd=1)
    assert_eq(st, 200, "escolha de plano")
    assert_eq(f'name="tipo_{a}"' in corpo and f'name="tipo_{b}"' in corpo, True,
              "cada linha da escolha tem o seu próprio tipo de sessão")
    st, _ = _post_agenda_novo(pes2, med2, d2, confirmar=1, destino=b,
                              tipo="reposicao")
    assert_eq(st, 303, "reposição no plano escolhido")
    ctx.conn.commit()
    assert_eq([s["repoe"] for s in _sessoes(ctx, b)].count(1), 1,
              "o plano escolhido recebeu a reposição")
    assert_eq([s["repoe"] for s in _sessoes(ctx, a)].count(1), 0,
              "o outro plano não foi tocado")
    assert_eq((_plano(ctx, b)["sessoes_adicionais"],
               _plano(ctx, a)["sessoes_adicionais"]), (0, 0),
              "nenhum dos dois virou extensão")
    assert_eq(sc.vagas_a_repor(ctx.conn, a), 1, "a vaga do outro plano segue aberta")

    # ── (c) /agenda/medium/lancar: a JSON de situação informa a vaga e o POST
    #         do formulário repõe.
    med3, pes3 = ctx.medium(), ctx.pessoa("k16c")
    pid3 = _plano_prospectivo(ctx, med3, pes3, 3)
    ctx.conn.commit()
    sg3 = _sessoes(ctx, pid3)
    st, _ = _http("POST",
                  f"/cadastros/mediuns/{med3}/planos/{pid3}/agenda/{sg3[0]['id']}/cancelar")
    assert_eq(st, 303, "sessão prevista cancelada")
    ctx.conn.commit()
    st, corpo = _http("GET", f"/agenda/medium/situacao?pessoa_id={pes3}&medium_id={med3}")
    assert_eq(st, 200, "situação do lançamento")
    import json as _json
    situacao = _json.loads(corpo)
    assert_eq((situacao["tem_plano"], situacao["mesmo_medium"]), (True, True),
              "plano ativo no médium escolhido")
    assert_eq(situacao["vagas_repor"], 1,
              "a tela é informada de que há vaga de reposição")
    d3 = _livres(ctx, pid3, 1)[0]
    st, local, corpo = _http_ate_o_destino(
        "POST", "/agenda/medium/lancar",
        {"pessoa_id": str(pes3), "medium_id": str(med3), "data": d3,
         "qtd_sessoes": "1", "frequencia": "semanal", "tipo": "reposicao"})
    assert_eq(st, 200, "página do medium renderizada")
    assert_eq("reposta no plano" in corpo, True, f"mensagem de reposição: {corpo[:200]}")
    linhas3 = _sessoes(ctx, pid3)
    assert_eq([s["repoe"] for s in linhas3].count(1), 1, "reposição gravada")
    assert_eq(_plano(ctx, pid3)["sessoes_adicionais"], 0, "sem extensão")

    # sem vaga, o mesmo POST recusa; e sem o campo, o comportamento continua
    # sendo o de sempre (sessão extra), sem adivinhar nada.
    st, local, corpo = _http_ate_o_destino(
        "POST", "/agenda/medium/lancar",
        {"pessoa_id": str(pes3), "medium_id": str(med3),
         "data": _livres(ctx, pid3, 1)[0], "qtd_sessoes": "1",
         "frequencia": "semanal", "tipo": "reposicao"})
    assert_eq("não tem sessão prevista em aberto para repor" in corpo, True,
              "recusa explicada na tela")
    st, local, corpo = _http_ate_o_destino(
        "POST", "/agenda/medium/lancar",
        {"pessoa_id": str(pes3), "medium_id": str(med3),
         "data": _livres(ctx, pid3, 1)[0], "qtd_sessoes": "1",
         "frequencia": "semanal"})
    ctx.conn.commit()
    assert_eq(_plano(ctx, pid3)["sessoes_adicionais"], 1,
              "sem escolher reposição, entra como extensão (comportamento habitual)")
    assert_eq([s["adicionada"] for s in _sessoes(ctx, pid3)].count(1), 1,
              "a linha sabe que é extra")


# ── Execução ──────────────────────────────────────────────────────────────────
def main():
    preparar_banco()
    _garantir_admin()
    _login()
    total, ok = 0, 0
    for nome, fn in RESULTADOS:
        total += 1
        try:
            with conectar() as conn:
                fn(Ctx(conn))
            ok += 1
            print(f"  PASS  {nome}")
        except Exception as e:
            print(f"  FALHOU {nome}\n        {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{ok}/{total} testes passaram (conclusão de planos — TASK_018)")
    limpar_banco()
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
