import os
import qrcode
import base64
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote_plus
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from psycopg2.errors import UniqueViolation

from banco import conectar, _normalizar, conta_id_por_tipo
from rotas.auth import obter_atendente_logado
from rotas.financeiro_mensalidades import sincronizar_mensalidades
from templates_config import templates

router = APIRouter(prefix="/financeiro")


def _guard(request: Request):
    from rotas.permissoes import guarda_modulo
    return guarda_modulo(request, "financeiro")


# ── Visão Geral (TASK_011) ───────────────────────────────────────────────────
#
# Contrato de leitura desta tela (encontrado vs final documentado na ordem):
#  - O livro-caixa é `financeiro_movimentacoes`. Eventos da sessão de caixa
#    NUNCA são somados por cima — servem apenas como proveniência no detalhe
#    (evento.financeiro_id), nunca como total.
#  - "Recebimentos/Pagamentos registrados" excluem transferências internas e
#    sangrias PELO VALOR ARMAZENADO em `categoria` (nunca por coincidência de
#    valor/data/descrição) e linhas lançadas em contas de obrigação
#    (tipo 'areceber'). Cancelados não contam, mas continuam listados.
#  - "A receber/A pagar" são obrigações PENDENTES por vencimento
#    (`data_movimentacao`) dentro da janela exibida — não é dinheiro.
#  - Fiado em aberto vive em `vendas_pdv` (sem vencimento registrado): grupo
#    próprio na aba de pendências, explicitamente fora do indicador A receber.
#  - Nada aqui corrige histórico: registros ambíguos permanecem no banco e são
#    ROTULADOS na tela ("Registro histórico — conferir origem").
#  - GETs não escrevem: nenhuma consulta desta tela sincroniza ou gera fatos.

import calendar

# Rótulos dos tipos de evento de sessão do Caixa (TASK_012 §5). O operador lê
# "Recebimento de fiado", não `recebimento_fiado`.
ROTULOS_MOVIMENTO = {
    "venda": "Venda",
    "venda_doacao": "Venda com doação",
    "venda_credito": "Venda paga com crédito",
    "cancelamento": "Cancelamento de venda",
    "doacao": "Doação",
    "mensalidade": "Mensalidade recebida",
    "recebimento_fiado": "Recebimento de fiado",
    "credito_anticipado": "Adiantamento de crédito",
    "sangria": "Sangria de caixa",
}

# Natureza do evento de sessão, em português (TASK_012 §5).
ROTULOS_NATUREZA = {
    "comercial": "movimento comercial",
    "recebimento": "recebimento",
    "retirada": "retirada",
}

ROTULOS_CATEGORIA = {
    "venda_pdv": "Venda (PDV)",
    "doacao": "Doação",
    "mensalidade": "Mensalidade",
    "recebimento_fiado": "Recebimento de fiado",
    "credito_anticipado": "Adiantamento de crédito",
    "livro": "Livro (biblioteca)",
    "compra": "Compra",
    "transferencia": "Transferência",
    "sangria": "Sangria de caixa",
    "manutencao": "Manutenção",
    "energia": "Energia",
    "agua": "Água",
    "aluguel": "Aluguel",
    "alimentacao": "Alimentação",
    "transporte": "Transporte",
    "outros": "Outros",
}
ROTULOS_SITUACAO = {"pago": "Pago", "pendente": "Pendente", "cancelado": "Cancelado"}
ROTULOS_TIPO_CONTA = {
    "especie": "Dinheiro (espécie)",
    "pix": "PIX",
    "legado": "Conta Antiga (origem histórica)",
    "areceber": "Valores a Receber (obrigações)",
}

# Condição (fixa, sem valores do usuário) que separa "dinheiro registrado" de
# movimento interno/obrigação. Aplicada aos cartões de Recebimentos/Pagamentos.
_SQL_DINHEIRO = (
    "fm.status = 'pago' "
    "AND fm.categoria NOT IN ('transferencia','sangria') "
    "AND (fm.conta_id IS NULL OR NOT EXISTS ("
    "SELECT 1 FROM contas_financeiras cc "
    "WHERE cc.id = fm.conta_id AND cc.tipo = 'areceber'))"
)

_JOINS_MOV = (
    "LEFT JOIN trabalhadores t ON t.id = fm.trabalhador_id "
    "LEFT JOIN pessoas p ON p.id = fm.pessoa_id "
    "LEFT JOIN contas_financeiras c ON c.id = fm.conta_id"
)
# Junções usadas quando a cláusula WHERE toca nome de trabalhador/pessoa (busca).
_JOINS_BUSCA = (
    "LEFT JOIN trabalhadores t ON t.id = fm.trabalhador_id "
    "LEFT JOIN pessoas p ON p.id = fm.pessoa_id"
)

_MSGS_OK = {
    "transferencia_ok": "Transferência registrada entre as contas.",
}


def _ultimo_dia(ano: int, mes: int) -> int:
    return calendar.monthrange(ano, mes)[1]


def _dec(valor) -> Decimal:
    """Decimal de duas casas, arredondamento comercial — a política do
    financeiro. `float` somado a `NUMERIC` é como centavo se perde."""
    if isinstance(valor, float):
        valor = f"{valor:.2f}"
    return Decimal(str(valor if valor is not None else 0)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)


ZERO = Decimal("0.00")


def _periodo_padrao() -> tuple[str, str]:
    """Mês corrente no fuso do negócio (mesma referência dos relatórios)."""
    from rotas.relatorios import _hoje_sp
    hoje = _hoje_sp()
    return (hoje.replace(day=1).isoformat(),
            hoje.replace(day=_ultimo_dia(hoje.year, hoje.month)).isoformat())


def _escapa_like(termo: str) -> str:
    """Escapa curingas do LIKE — o valor entra sempre por placeholder, mas
    `%`/`_` literais não podem alargar a busca."""
    return termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _rotulo_natureza(linha: dict) -> str:
    cat = linha.get("categoria") or ""
    rotulo = ROTULOS_CATEGORIA.get(cat, "Outros")
    if cat == "transferencia" and linha.get("sangria_id") is not None:
        rotulo = "Transferência (contrapartida de sangria)"
    return rotulo


def _situacao_exibida(linha: dict) -> str:
    """Dinheiro confirmado x obrigação x registro histórico ambíguo.

    Uma linha paga lançada em conta de obrigação NÃO é promovida a
    recebimento: recebe rótulo próprio, sem alterar o fato armazenado.
    """
    if (linha.get("status") == "pago"
            and linha.get("conta_tipo") == "areceber"):
        return "Registro histórico — conferir origem"
    return ROTULOS_SITUACAO.get(linha.get("status"), linha.get("status") or "")


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    aba: str = Query(""),
    de: str = Query(""),
    ate: str = Query(""),
    mes: str = Query(""),
    conta: str = Query(""),
    nat: str = Query(""),
    sentido: str = Query(""),
    sit: str = Query(""),
    q: str = Query(""),
    pagina: str = Query("1"),
    por: str = Query("25"),
    msg: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    recusas: list[str] = []

    if aba not in ("mov", "pend"):
        if aba:
            recusas.append(f"Aba “{aba}” não existe — mostrando Movimentações.")
        aba = "mov"

    # ── período: De/Até com alias legado ?mes=AAAA-MM e padrão mês atual ─────
    de_bruto, ate_bruto = de.strip(), ate.strip()
    if not de_bruto and not ate_bruto and mes.strip():
        m = mes.strip()
        try:
            ano, mm = int(m[:4]), int(m[5:7])
            de_bruto = f"{ano:04d}-{mm:02d}-01"
            ate_bruto = f"{ano:04d}-{mm:02d}-{_ultimo_dia(ano, mm):02d}"
        except (ValueError, IndexError):
            recusas.append("Referência de mês inválida — use o formulário de período.")
    pad_de, pad_ate = _periodo_padrao()
    try:
        d_de = date.fromisoformat(de_bruto) if de_bruto else date.fromisoformat(pad_de)
    except ValueError:
        recusas.append(f"Data inicial “{de_bruto}” inválida — a consulta usou o "
                       "período padrão; corrija ou use Limpar.")
        d_de = date.fromisoformat(pad_de)
    try:
        d_ate = date.fromisoformat(ate_bruto) if ate_bruto else date.fromisoformat(pad_ate)
    except ValueError:
        recusas.append(f"Data final “{ate_bruto}” inválida — a consulta usou o "
                       "período padrão; corrija ou use Limpar.")
        d_ate = date.fromisoformat(pad_ate)
    if d_de > d_ate:
        recusas.append("A data inicial é posterior à final — a consulta usou o "
                       "período padrão; corrija ou use Limpar.")
        d_de, d_ate = date.fromisoformat(pad_de), date.fromisoformat(pad_ate)
    de_uso, ate_uso = d_de.isoformat(), d_ate.isoformat()

    # ── demais filtros: whitelist/validação no servidor, valor preservado ───
    conta_uso: int | None = None
    if conta.strip():
        if conta.strip().isdigit():
            with conectar() as conn:
                existe = conn.execute(
                    "SELECT id FROM contas_financeiras WHERE id = %s AND ativo = 1",
                    (int(conta),)).fetchone()
            if existe:
                conta_uso = int(conta)
            else:
                recusas.append(f"Conta “{conta}” não existe — o filtro não foi "
                               "aplicado; o valor permanece no formulário.")
        else:
            recusas.append("Conta inválida — o filtro não foi aplicado.")
    nat_uso = nat if nat in ROTULOS_CATEGORIA else ""
    if nat and not nat_uso:
        recusas.append(f"Natureza “{nat}” desconhecida — o filtro não foi aplicado.")
    sentido_uso = sentido if sentido in ("", "entrada", "saida") else ""
    if sentido and not sentido_uso:
        recusas.append("Sentido fora dos valores permitidos — o filtro não foi aplicado.")
    sit_uso = sit if sit in ("", "pago", "pendente", "cancelado") else ""
    if sit and not sit_uso:
        recusas.append("Situação fora dos valores permitidos — o filtro não foi aplicado.")
    try:
        por_uso = int(por)
        if por_uso not in (25, 50, 100):
            recusas.append(f"{por} itens por página não é uma opção — usei 25.")
            por_uso = 25
    except ValueError:
        recusas.append("Itens por página inválido — usei 25.")
        por_uso = 25
    try:
        pagina_uso = max(1, int(pagina))
    except ValueError:
        pagina_uso = 1

    cond = ["fm.data_movimentacao >= %s", "fm.data_movimentacao <= %s"]
    params: list = [de_uso, ate_uso]
    if conta_uso is not None:
        cond.append("fm.conta_id = %s")
        params.append(conta_uso)
    if nat_uso:
        cond.append("fm.categoria = %s")
        params.append(nat_uso)
    if sentido_uso:
        cond.append("fm.tipo = %s")
        params.append(sentido_uso)
    if sit_uso:
        cond.append("fm.status = %s")
        params.append(sit_uso)
    termo = _normalizar(q.strip()) if q.strip() else ""
    if termo:
        like = f"%{_escapa_like(termo)}%"
        cond.append("(norm(COALESCE(fm.descricao, '')) LIKE %s "
                    "OR norm(COALESCE(t.nome_completo, '')) LIKE %s "
                    "OR norm(COALESCE(p.nome_completo, '')) LIKE %s)")
        params += [like, like, like]
    where = "WHERE " + " AND ".join(cond)
    filtros_ativos = bool(termo or conta_uso is not None or nat_uso
                          or sentido_uso or sit_uso)

    with conectar() as conn:
        tot = conn.execute(
            f"""SELECT COALESCE(SUM(CASE WHEN fm.tipo = 'entrada' THEN fm.valor END), 0) AS receb,
                       COALESCE(SUM(CASE WHEN fm.tipo = 'saida' THEN fm.valor END), 0) AS pag
                FROM financeiro_movimentacoes fm {_JOINS_MOV}
                {where} AND {_SQL_DINHEIRO}""",
            params).fetchone()

        n_total = conn.execute(
            f"SELECT COUNT(*) AS c FROM financeiro_movimentacoes fm {_JOINS_MOV} {where}",
            params).fetchone()["c"]
        paginas = max(1, -(-n_total // por_uso))
        if pagina_uso > paginas:
            pagina_uso = paginas
        movs = conn.execute(
            f"""SELECT fm.*, c.nome AS conta_nome, c.tipo AS conta_tipo,
                       t.nome_completo AS trabalhador_nome,
                       p.nome_completo AS pessoa_nome
                FROM financeiro_movimentacoes fm {_JOINS_MOV}
                {where}
                ORDER BY fm.data_movimentacao DESC, fm.id DESC
                LIMIT %s OFFSET %s""",
            params + [por_uso, (pagina_uso - 1) * por_uso]).fetchall()

        # Obrigações pendentes no recorte de vencimento (universo próprio do
        # cartão "A receber/A pagar": só o período, nunca os demais filtros).
        pend = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN tipo = 'entrada' THEN valor END), 0) AS a_receber,
                      COALESCE(SUM(CASE WHEN tipo = 'saida' THEN valor END), 0) AS a_pagar
               FROM financeiro_movimentacoes
               WHERE status = 'pendente'
                 AND data_movimentacao >= %s AND data_movimentacao <= %s""",
            (de_uso, ate_uso)).fetchone()

        # Movimento por conta no período (substitui o antigo "saldo por
        # conta": transferências aparecem AQUI, nunca nos cartões globais).
        joins_conta = _JOINS_BUSCA if termo else ""
        contas_mov = conn.execute(
            f"""SELECT c.id, c.nome, c.tipo,
                       COALESCE(SUM(CASE WHEN fm.tipo = 'entrada' THEN fm.valor END), 0) AS entradas,
                       COALESCE(SUM(CASE WHEN fm.tipo = 'saida' THEN fm.valor END), 0) AS saidas
                FROM financeiro_movimentacoes fm
                JOIN contas_financeiras c ON c.id = fm.conta_id
                {joins_conta}
                {where} AND fm.status = 'pago'
                GROUP BY c.id, c.nome, c.tipo
                ORDER BY c.id""",
            params).fetchall()

        # Acumulado da conta até a data final do recorte (TASK_012 §5): o
        # período da consulta responde "o que aconteceu", o acumulado responde
        # "quanto há nesta conta" — antes a tela só tinha o primeiro e o
        # operador lia movimento mensal como se fosse saldo. Não é conciliação
        # bancária: é o que os lançamentos pagos dizem, e a transferência entra
        # na conta de origem e na de destino (sai do resultado global, fica no
        # histórico de cada conta).
        acumulado = {
            r["conta_id"]: _dec(r["acumulado"])
            for r in conn.execute(
                """SELECT fm.conta_id,
                          COALESCE(SUM(CASE WHEN fm.tipo = 'entrada'
                                            THEN fm.valor ELSE -fm.valor END), 0) AS acumulado
                   FROM financeiro_movimentacoes fm
                   WHERE fm.status = 'pago' AND fm.conta_id IS NOT NULL
                     AND fm.data_movimentacao <= %s
                   GROUP BY fm.conta_id""", (ate_uso,)).fetchall()
        }
        contas_mov = [dict(cm) | {"acumulado": acumulado.get(cm["id"], ZERO)}
                      for cm in contas_mov]

        historico_ambiguo = conn.execute(
            """SELECT COUNT(*) AS c
               FROM financeiro_movimentacoes fm
               JOIN contas_financeiras cc ON cc.id = fm.conta_id AND cc.tipo = 'areceber'
               WHERE fm.status = 'pago'
                 AND fm.data_movimentacao >= %s AND fm.data_movimentacao <= %s""",
            (de_uso, ate_uso)).fetchone()["c"]

        if aba == "pend":
            n_pend = conn.execute(
                """SELECT COUNT(*) AS c FROM financeiro_movimentacoes
                   WHERE status = 'pendente'
                     AND data_movimentacao >= %s AND data_movimentacao <= %s""",
                (de_uso, ate_uso)).fetchone()["c"]
            paginas_pend = max(1, -(-n_pend // por_uso))
            if pagina_uso > paginas_pend:
                pagina_uso = paginas_pend
            pendentes = conn.execute(
                f"""SELECT fm.*, c.nome AS conta_nome, c.tipo AS conta_tipo,
                           t.nome_completo AS trabalhador_nome,
                           p.nome_completo AS pessoa_nome
                    FROM financeiro_movimentacoes fm {_JOINS_MOV}
                    WHERE fm.status = 'pendente'
                      AND fm.data_movimentacao >= %s AND fm.data_movimentacao <= %s
                    ORDER BY fm.data_movimentacao ASC, fm.id ASC
                    LIMIT %s OFFSET %s""",
                [de_uso, ate_uso, por_uso, (pagina_uso - 1) * por_uso]).fetchall()

            # Fiado em aberto: a conta de saldo é a do recebimento (§4/§5) —
            # valor original − crédito consumido − alocado, nunca negativa. O
            # `limite` corta a LISTA; os totais vêm de todas as dívidas do
            # recorte, senão a nota diria "total" de uma página.
            import servico_fiado
            fiados, fiado_tot = servico_fiado.fiados_em_aberto_para_consulta(
                conn, nome_like=like if termo else None, limite=50)
            n_pend_total = n_pend
        else:
            pendentes, fiados, fiado_tot = [], [], None
            n_pend_total = 0

    # URLs de estado: cada aba tem URL própria; trocar de aba preserva os
    # filtros compatíveis (situação/sentido pertencem ao extrato).
    def url_com(**mudancas) -> str:
        base = {"aba": aba, "de": de_uso, "ate": ate_uso,
                "conta": conta_uso or "", "nat": nat_uso,
                "sentido": sentido_uso, "sit": sit_uso, "q": q.strip(),
                "pagina": pagina_uso, "por": por_uso}
        base.update(mudancas)
        qs = "&".join(f"{k}={quote_plus(str(v))}" for k, v in base.items()
                      if v not in ("", None))
        return f"/financeiro?{qs}"

    hoje_ref = date.fromisoformat(pad_de)
    ano_ant, mes_ant = ((hoje_ref.year, hoje_ref.month - 1)
                        if hoje_ref.month > 1 else (hoje_ref.year - 1, 12))
    url_mes_atual = url_com(de=pad_de, ate=pad_ate, pagina=1,
                            conta="", nat="", sentido="", sit="", q="")
    url_mes_anterior = url_com(
        de=f"{ano_ant:04d}-{mes_ant:02d}-01",
        ate=f"{ano_ant:04d}-{mes_ant:02d}-{_ultimo_dia(ano_ant, mes_ant):02d}",
        pagina=1, conta="", nat="", sentido="", sit="", q="")

    from rotas.permissoes import usuario_pode
    pode_escrever = usuario_pode(atendente["id"], "financeiro", "escrever")

    with conectar() as conn:
        contas_filtro = conn.execute(
            "SELECT id, nome, tipo FROM contas_financeiras WHERE ativo = 1 ORDER BY id"
        ).fetchall()

    def _linhas(rows):
        out = []
        for r in rows:
            d = dict(r)
            d["natureza"] = _rotulo_natureza(d)
            d["situacao"] = _situacao_exibida(d)
            out.append(d)
        return out

    contexto = {
        "request": request,
        "atendente": atendente,
        "pode_escrever": pode_escrever,
        "aba": aba,
        "de_bruto": de_bruto, "ate_bruto": ate_bruto,
        "de_uso": de_uso, "ate_uso": ate_uso,
        "conta_bruto": conta, "conta_uso": conta_uso or "",
        "nat_bruto": nat, "sentido_bruto": sentido, "sit_bruto": sit,
        "q": q, "por": por_uso, "pagina": pagina_uso, "paginas": paginas,
        "recusas": recusas,
        "msg_ok": _MSGS_OK.get(msg, ""),
        "recebimentos": _dec(tot["receb"]),
        "pagamentos": _dec(tot["pag"]),
        "resultado": _dec(tot["receb"]) - _dec(tot["pag"]),
        "a_receber": _dec(pend["a_receber"]),
        "a_pagar": _dec(pend["a_pagar"]),
        "filtros_ativos": filtros_ativos,
        "n_total": n_total,
        "inicio_exibido": (pagina_uso - 1) * por_uso + 1 if n_total else 0,
        "fim_exibido": min(pagina_uso * por_uso, n_total),
        "movs": _linhas(movs),
        "contas_mov": [dict(cm) for cm in contas_mov],
        "historico_ambiguo": historico_ambiguo,
        "pendentes": _linhas(pendentes),
        "n_pend_total": n_pend_total,
        "fiados": [dict(f) for f in fiados],
        "fiado_tot": dict(fiado_tot) if fiado_tot else None,
        "rotulos_categoria": ROTULOS_CATEGORIA,
        "rotulos_tipo_conta": ROTULOS_TIPO_CONTA,
        "contas_filtro": [dict(c) for c in contas_filtro],
        "url_com": url_com,
        "url_mes_atual": url_mes_atual,
        "url_mes_anterior": url_mes_anterior,
        "url_limpar": url_com(de="", ate="", conta="", nat="", sentido="",
                              sit="", q="", pagina=1),
    }
    return templates.TemplateResponse("financeiro/dashboard.html", contexto)


# ── Detalhe somente-leitura de um lançamento ─────────────────────────────────

@router.get("/lancamento/{mov_id}", response_class=HTMLResponse)
async def detalhe_lancamento(request: Request, mov_id: int, volta: str = ""):
    """Página de detalhe rastreável: apenas vínculos PROVADOS no banco
    (FKs e eventos que apontam financeiro_id). Nada é reconstruído por
    texto/valor/data. Link de volta validado para não virar open-redirect."""
    atendente, redir = _guard(request)
    if redir:
        return redir

    if not volta.startswith("/financeiro?"):
        volta = "/financeiro"

    with conectar() as conn:
        linha = conn.execute(
            f"""SELECT fm.*, c.nome AS conta_nome, c.tipo AS conta_tipo,
                       t.nome_completo AS trabalhador_nome,
                       p.nome_completo AS pessoa_nome
                FROM financeiro_movimentacoes fm {_JOINS_MOV}
                WHERE fm.id = %s""",
            (mov_id,)).fetchone()
        if not linha:
            return HTMLResponse(
                status_code=404,
                content=("<h1>Lançamento não encontrado</h1>"
                         f"<p>O lançamento #{mov_id} não existe.</p>"
                         '<p><a href="/financeiro">Voltar à Visão Geral</a></p>'))
        eventos = conn.execute(
            """SELECT id, identidade, tipo, natureza, sessao_id, venda_id, criado_em
               FROM caixa_sessao_eventos WHERE financeiro_id = %s
               ORDER BY id""",
            (mov_id,)).fetchall()

        # Destino do dinheiro (TASK_012 §4/§5): o que este lançamento abatou,
        # lido das alocações gravadas — a mesma fonte dos saldos exibidos.
        alocacoes = conn.execute(
            """SELECT a.id, a.tipo, a.venda_id, a.valor, a.trabalhador_id,
                      v.data_venda, v.fiado_trabalhador_id,
                      t.nome_completo AS trabalhador_nome
               FROM fiado_alocacao a
               LEFT JOIN vendas_pdv v ON v.id = a.venda_id
               LEFT JOIN trabalhadores t ON t.id = COALESCE(v.fiado_trabalhador_id,
                                                            a.trabalhador_id)
               WHERE a.financeiro_id = %s
               ORDER BY a.tipo, a.venda_id""",
            (mov_id,)).fetchall()

    d = dict(linha)
    return templates.TemplateResponse("financeiro/detalhe.html", {
        "request": request,
        "atendente": atendente,
        "linha": d,
        "rotulo_natureza": _rotulo_natureza(d),
        "situacao": _situacao_exibida(d),
        "rotulo_conta": ROTULOS_TIPO_CONTA.get(d.get("conta_tipo"),
                                               d.get("conta_tipo") or ""),
        "eventos": [dict(e) for e in eventos],
        "alocacoes": [dict(a) | {"valor": _dec(a["valor"])} for a in alocacoes],
        "rotulos_movimento": ROTULOS_MOVIMENTO,
        "rotulos_natureza": ROTULOS_NATUREZA,
        "volta": volta,
    })


# ── Nova movimentação ─────────────────────────────────────────────────────────

@router.get("/nova", response_class=HTMLResponse)
async def form_nova(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        trabalhadores = conn.execute(
            "SELECT id, nome_completo FROM trabalhadores WHERE ativo = 1 ORDER BY nome_completo"
        ).fetchall()
        contas = conn.execute(
            "SELECT id, nome FROM contas_financeiras WHERE ativo = 1 ORDER BY id"
        ).fetchall()

    return templates.TemplateResponse("financeiro/form.html", {
        "request": request,
        "atendente": atendente,
        "registro": None,
        "trabalhadores": [dict(t) for t in trabalhadores],
        "contas": [dict(c) for c in contas],
    })


@router.post("/nova", response_class=HTMLResponse)
async def salvar_nova(
    request: Request,
    tipo: str = Form(...),
    categoria: str = Form(...),
    valor: str = Form("0"),
    data_movimentacao: str = Form(""),
    descricao: str = Form(""),
    trabalhador_id: str = Form(""),
    pessoa_id: str = Form(""),
    status: str = Form("pago"),
    pix_copiadecola: str = Form(""),
    conta_id: str = Form(""),
    idempotencia: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    from servico_idempotencia import (
        TentativaJaRegistrada, TentativaRecusada, confirmar, exigir_reserva,
    )
    from rotas.financeiro_mensalidades import normalizar_competencia

    data_ref = data_movimentacao.strip() or date.today().isoformat()

    # Valor é dinheiro: entra pelo parser do financeiro (duas casas, teto do
    # NUMERIC, recusa legível) antes de qualquer reserva de tentativa. `float()`
    # aqui derrubava a requisição (500) ou aceitava `1,234` como mil e duzentos.
    from servico_sessao import moeda as _moeda_form, SessaoInvalida

    def _recusa(cod: str):
        return RedirectResponse(url=f"/financeiro/nova?erro={cod}", status_code=303)

    try:
        valor_dec = _moeda_form(valor, "valor")
    except SessaoInvalida:
        return _recusa("valor_invalido")
    if valor_dec <= ZERO:
        return _recusa("valor_invalido")

    # TASK_012 §3: mensalidade lançada à mão entra pela mesma chave do resto do
    # módulo (trabalhador, competência). Sem período legível não há competência
    # inequívoca — o período é recusado com mensagem simples, e nada é escrito.
    competencia = None
    if categoria == "mensalidade":
        competencia = normalizar_competencia(data_ref)
        if not competencia:
            return RedirectResponse(url="/financeiro/nova?erro=periodo_invalido",
                                    status_code=303)

    with conectar() as conn:
        try:
            chave_tentativa = exigir_reserva(
                conn, idempotencia=idempotencia, operacao="lancamento_novo",
                autor_id=atendente["id"],
                corpo={"tipo": tipo, "categoria": categoria, "valor": str(valor_dec),
                       "data": data_ref, "descricao": descricao.strip(),
                       "trabalhador_id": trabalhador_id or None,
                       "pessoa_id": pessoa_id or None, "status": status,
                       "conta_id": conta_id or None})
        except TentativaRecusada as e:
            return RedirectResponse(url=f"/financeiro/nova?erro={e.chave_erro}",
                                    status_code=303)
        except TentativaJaRegistrada as e:
            return RedirectResponse(url=e.destino or "/financeiro", status_code=303)

        try:
            cur = conn.execute(
                """INSERT INTO financeiro_movimentacoes
                   (tipo, categoria, valor, data_movimentacao, competencia, descricao,
                    trabalhador_id, pessoa_id, status, pix_copiadecola, conta_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tipo, categoria, valor_dec, data_ref, competencia,
                 descricao.strip(),
                 int(trabalhador_id) if trabalhador_id else None,
                 int(pessoa_id) if pessoa_id else None,
                 status, pix_copiadecola.strip(),
                 int(conta_id) if conta_id else None),
            )
        except UniqueViolation:
            # Transação abortada: a reserva desta tentativa também desfaz, então
            # a chave não fica consumida por uma escrita que não aconteceu.
            return RedirectResponse(
                url="/financeiro/nova?erro=mensalidade_ja_registrada", status_code=303)
        confirmar(conn, chave=chave_tentativa, documento=cur.fetchone()["id"],
                  destino="/financeiro")

    return RedirectResponse(url="/financeiro", status_code=303)


# ── Mensalidades ──────────────────────────────────────────────────────────────

@router.get("/mensalidades", response_class=HTMLResponse)
async def mensalidades(
    request: Request,
    trabalhador_id: int = 0,
    pago: str = "",
    total: str = "",
    geradas: str = "",
    erro: str = "",
):
    """Consulta de mensalidades. TASK_012 §3: ler não gera cobrança — a
    criação das obrigações é ação explícita (`POST /mensalidades/gerar`,
    cadastro/reativação do trabalhador ou startup), nunca um efeito colateral
    de abrir a tela."""
    atendente, redir = _guard(request)
    if redir:
        return redir

    hoje = date.today()
    mes_atual = f"{hoje.year}-{hoje.month:02d}"

    trabalhador = None
    pendentes = []
    pagos_recentes = []
    conflito = []

    with conectar() as conn:
        if trabalhador_id:
            trab = conn.execute(
                """SELECT id, nome_completo, valor_mensalidade, dia_vencimento, ativo
                   FROM trabalhadores WHERE id = %s""",
                (trabalhador_id,),
            ).fetchone()
            if not trab:
                erro = "trabalhador_nao_encontrado"
            else:
                trabalhador = dict(trab)

                pend = conn.execute(
                    """SELECT id, data_movimentacao, competencia, valor
                       FROM financeiro_movimentacoes
                       WHERE trabalhador_id = %s AND categoria = 'mensalidade'
                         AND status = 'pendente'
                       ORDER BY competencia NULLS LAST, data_movimentacao""",
                    (trabalhador_id,),
                ).fetchall()
                hoje_iso = hoje.isoformat()
                for r in pend:
                    venc = str(r["data_movimentacao"])[:10]
                    comp = r["competencia"] or venc[:7]
                    pendentes.append({
                        "id": r["id"],
                        "competencia": comp,
                        "mes_ref": comp,
                        "data_vencimento": venc,
                        "valor": _dec(r["valor"]),
                        "vencido": venc < hoje_iso,
                        "corrente": comp == mes_atual,
                        "futuro": comp > mes_atual,
                    })

                # Conflito histórico (linhas demais no mesmo mês) é mostrado, não
                # resolvido: nada é fundido, apagado ou escolhido por aqui.
                duplicadas = conn.execute(
                    """SELECT competencia FROM financeiro_movimentacoes
                       WHERE trabalhador_id = %s AND categoria = 'mensalidade'
                         AND competencia IS NOT NULL
                       GROUP BY competencia HAVING COUNT(*) > 1
                       ORDER BY competencia""", (trabalhador_id,)).fetchall()
                conflito = [d["competencia"] for d in duplicadas]

                pagos = conn.execute(
                    """SELECT id, data_movimentacao, data_pagamento, competencia, valor
                       FROM financeiro_movimentacoes
                       WHERE trabalhador_id = %s AND categoria = 'mensalidade'
                         AND status = 'pago'
                       ORDER BY COALESCE(data_pagamento, data_movimentacao) DESC, id DESC
                       LIMIT 10""",
                    (trabalhador_id,),
                ).fetchall()
                for r in pagos:
                    comp = r["competencia"] or str(r["data_movimentacao"])[:7]
                    pagos_recentes.append({
                        "id": r["id"],
                        "competencia": comp,
                        "mes_ref": comp,
                        "data_vencimento": str(r["data_movimentacao"])[:10],
                        "data_pagamento": (str(r["data_pagamento"])[:10]
                                           if r["data_pagamento"] else None),
                        "valor": _dec(r["valor"]),
                    })

        from servico_sessao import contas_por_tipo, sessoes_abertas
        todas = [dict(s) for s in sessoes_abertas(conn)]
        sessoes = [s for s in todas if s["versao"] == "eventos"]
        sessoes_legadas = [s for s in todas if s["versao"] != "eventos"]
        contas_pix = [dict(c) for c in contas_por_tipo(conn, "pix")]

    return templates.TemplateResponse("financeiro/mensalidades.html", {
        "request": request,
        "atendente": atendente,
        "trabalhador": trabalhador,
        "valor_mensal": _dec(trabalhador["valor_mensalidade"]) if trabalhador else ZERO,
        "dia_vencimento": (trabalhador or {}).get("dia_vencimento") or 10,
        "pendentes": pendentes,
        "conflito": conflito,
        "geradas": geradas,
        "mes_atual": mes_atual,
        "pagos_recentes": pagos_recentes,
        "pago": pago,
        "total": total,
        "erro": erro,
        "sessoes": sessoes,
        "sessoes_legadas": sessoes_legadas,
        "contas_pix": contas_pix,
    })


@router.post("/mensalidades/gerar", response_class=HTMLResponse)
async def gerar_mensalidades(request: Request, trabalhador_id: int = Form(...)):
    """Geração manual (botão da tela): mês corrente + 12 futuros, idempotente.

    Rodar duas vezes não cria segunda cobrança — a chave do banco é
    (trabalhador, competência). Concorrência da mesma geração é absorvida pelo
    `ON CONFLICT DO NOTHING` do serviço.
    """
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        trab = conn.execute(
            """SELECT id, nome_completo, valor_mensalidade, dia_vencimento, ativo
               FROM trabalhadores WHERE id = %s""", (trabalhador_id,)).fetchone()
        if not trab:
            return RedirectResponse(
                url=f"/financeiro/mensalidades?trabalhador_id={trabalhador_id}"
                    "&erro=trabalhador_nao_encontrado", status_code=303)
        dados = dict(trab)
        if not dados["ativo"] or float(dados["valor_mensalidade"] or 0) <= 0:
            return RedirectResponse(
                url=f"/financeiro/mensalidades?trabalhador_id={trabalhador_id}"
                    "&erro=sem_mensalidade_ativa", status_code=303)
        criadas = sincronizar_mensalidades(conn, dados)

    return RedirectResponse(
        url=f"/financeiro/mensalidades?trabalhador_id={trabalhador_id}&geradas={criadas}",
        status_code=303)


@router.get("/mensalidades/buscar", response_class=JSONResponse)
async def buscar_mensalidade(request: Request, q: str = Query("")):
    # TASK_011: a busca devolve CPF — exige a permissão de leitura do módulo
    # como o resto do financeiro (antes qualquer sessão logada acessava).
    atendente, redir = _guard(request)
    if redir:
        return redir
    if len(q.strip()) < 2:
        return JSONResponse([])

    with conectar() as conn:
        termo = f"%{_normalizar(q.strip())}%"
        rows = conn.execute(
            """SELECT t.id, t.nome_completo, t.cpf, t.valor_mensalidade, t.dia_vencimento
               FROM trabalhadores t
               WHERE norm(t.nome_completo) LIKE %s
                 AND t.ativo = 1 AND t.valor_mensalidade > 0
               ORDER BY t.nome_completo LIMIT 10""",
            (termo,),
        ).fetchall()

    return JSONResponse([
        {
            "id": r["id"],
            "nome": r["nome_completo"],
            "cpf": r["cpf"] or "",
            "valor_mensalidade": float(r["valor_mensalidade"]),
            "dia_vencimento": r["dia_vencimento"] or 10,
        }
        for r in rows
    ])


@router.post("/mensalidades/receber", response_class=HTMLResponse)
async def receber_mensalidades(
    request: Request,
    trabalhador_id: int = Form(...),
    mov_ids: str = Form(""),
    forma_pagamento: str = Form("especie"),
    pix_copiadecola: str = Form(""),
    sessao_id: int = Form(0),
    conta_pix_id: int = Form(0),
    conta_id: int = Form(0),
    idempotencia: str = Form(""),
):
    """Recebimento de mensalidades (TASK_008): destino explícito, sem
    depósito implícito no caixa 2.

    Espécie exige uma sessão de caixa aberta e identificada — o dinheiro
    entra NAQUELA gaveta. PIX exige conta PIX válida e não toca gaveta
    alguma. Em sessão 'eventos' cada mensalidade paga gera UM evento
    `mensalidade:{mov_id}`: o banco recusa contar a mesma competência duas
    vezes. Sessão legado (regra antiga) não aceita novos recebimentos — a
    escolha dela é recusada antes de qualquer escrita."""
    atendente, redir = _guard(request)
    if redir:
        return redir

    from decimal import Decimal
    from servico_sessao import (
        SessaoInvalida, VERSAO_EVENTOS, registrar_evento,
        sessao_para_recebimento, validar_conta_para_forma,
    )

    if forma_pagamento not in ("especie", "pix"):
        forma_pagamento = "especie"

    hoje = date.today()
    data_pgto = hoje.isoformat()

    def _erro(cod: str):
        return RedirectResponse(
            url=f"/financeiro/mensalidades?trabalhador_id={trabalhador_id}&erro={cod}",
            status_code=303,
        )

    def _ids_selecionados(txt: str):
        return sorted(int(s) for s in (txt or "").split(",") if s.strip().isdigit())

    corpo_tentativa = {"trabalhador_id": trabalhador_id,
                       "mov_ids": _ids_selecionados(mov_ids),
                       "forma": forma_pagamento, "sessao_id": sessao_id,
                       "conta_pix_id": conta_pix_id or None,
                       # TASK_016: a conta entra na identidade da tentativa —
                       # trocar o destino com a mesma chave é CONFLITO, nunca
                       # reaproveitar o resultado do outro destino.
                       "conta_id": conta_id or None}

    from servico_idempotencia import (
        TentativaJaRegistrada, TentativaRecusada, confirmar, exigir_reserva,
    )
    from rotas.tentativa import resposta_de_tentativa

    try:
        with conectar() as conn:
            respondida = resposta_de_tentativa(
                conn, idempotencia=idempotencia, corpo=corpo_tentativa,
                erro_para=_erro,
                destino_padrao=f"/financeiro/mensalidades?trabalhador_id={trabalhador_id}",
                operacao="mensalidade_receber", autor_id=atendente["id"])
            if respondida:
                return respondida

            trab = conn.execute(
                "SELECT * FROM trabalhadores WHERE id = %s", (trabalhador_id,)
            ).fetchone()
            if not trab:
                return _erro("trabalhador_nao_encontrado")

            # Destino validado ANTES de baixar qualquer pendência — fora de
            # PIX, o servidor ignora a conta enviada pelo navegador (a barra
            # de destino injeta conta_pix_id mesmo quando a forma é espécie).
            sessao = sessao_para_recebimento(conn, sessao_id, forma_pagamento)
            cid = validar_conta_para_forma(
                conn, forma_pagamento,
                (conta_id or None) or
                ((conta_pix_id or None) if forma_pagamento == "pix" else None))

            # Pendentes selecionadas (só as que ainda estão pendentes e do mesmo trabalhador)
            a_pagar = []
            for sid in _ids_selecionados(mov_ids):
                mov = conn.execute(
                    """SELECT id, valor FROM financeiro_movimentacoes
                       WHERE id = %s AND categoria = 'mensalidade'
                         AND trabalhador_id = %s AND status = 'pendente'
                       FOR UPDATE""",
                    (sid, trabalhador_id),
                ).fetchone()
                if mov:
                    a_pagar.append(mov)

            if not a_pagar:
                return _erro("sem_itens")

            total = sum((Decimal(str(m["valor"])) for m in a_pagar), Decimal("0.00"))

            try:
                chave_tentativa = exigir_reserva(
                    conn, idempotencia=idempotencia, operacao="mensalidade_receber",
                    autor_id=atendente["id"], corpo=corpo_tentativa)
            except TentativaRecusada as e:
                return _erro(e.chave_erro)
            except TentativaJaRegistrada as e:
                return RedirectResponse(
                    url=e.destino or f"/financeiro/mensalidades?trabalhador_id={trabalhador_id}",
                    status_code=303)

            for m in a_pagar:
                conn.execute(
                    """UPDATE financeiro_movimentacoes
                       SET status = 'pago', forma_pagamento = %s, conta_id = %s,
                           data_pagamento = %s, pix_copiadecola = %s
                       WHERE id = %s""",
                    (forma_pagamento, cid, data_pgto, pix_copiadecola.strip(), m["id"]),
                )
                if sessao is not None and sessao["versao"] == VERSAO_EVENTOS:
                    registrar_evento(
                        conn, identidade=f"mensalidade:{m['id']}",
                        sessao_id=sessao["id"], tipo="mensalidade",
                        natureza="recebimento",
                        origem_tipo="financeiro_movimentacoes",
                        origem_id=m["id"], financeiro_id=m["id"],
                        forma=forma_pagamento, sentido="entrada",
                        valor=Decimal(str(m["valor"])),
                        efeito_fisico=Decimal(str(m["valor"]))
                        if forma_pagamento == "especie" else Decimal("0.00"),
                        conta_id=cid, atendente_id=atendente["id"])

            ids_pagas = [m["id"] for m in a_pagar]
            destino = (f"/financeiro/mensalidades?trabalhador_id={trabalhador_id}"
                       f"&pago={','.join(str(i) for i in ids_pagas)}"
                       f"&total={total:.2f}")
            confirmar(conn, chave=chave_tentativa,
                      documento=ids_pagas, destino=destino)
    except SessaoInvalida as e:
        # exceção vazou do with → nenhuma pendência baixada, nenhum evento
        return _erro(e.chave)

    return RedirectResponse(url=destino, status_code=303)


@router.post("/mensalidades/{mov_id}/cancelar")
async def cancelar_mensalidade(request: Request, mov_id: int, trabalhador_id: int = Form(0)):
    """Cancela um lançamento pendente (ex.: mês futuro gerado por engano).
    Some do saldo de Valores a Receber e não é recriado."""
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        conn.execute(
            """UPDATE financeiro_movimentacoes
               SET status = 'cancelado', conta_id = NULL
               WHERE id = %s AND categoria = 'mensalidade' AND status = 'pendente'""",
            (mov_id,),
        )
    return RedirectResponse(url=f"/financeiro/mensalidades?trabalhador_id={trabalhador_id}", status_code=303)


# ── PIX Estático ──────────────────────────────────────────────────────────────

@router.get("/pix", response_class=HTMLResponse)
async def gerar_pix(
    request: Request,
    valor: str = "0",
    descricao: str = "",
    chave: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    # PIX estático é uma consulta: não pode derrubar a aplicação por causa de
    # texto no campo. Valor ilegível → mensagem, nenhum QR é gerado.
    try:
        valor_dec = _dec(valor)
    except InvalidOperation:
        return HTMLResponse(
            "<h1>Valor inválido</h1><p>O valor do PIX não é um número. "
            "Nenhum código foi gerado.</p>", status_code=400)
    if valor_dec <= ZERO:
        return HTMLResponse(
            "<h1>Valor insuficiente</h1><p>Informe um valor maior que zero "
            "para gerar o PIX.</p>", status_code=400)

    # Se não tem chave, busca do banco
    if not chave:
        with conectar() as conn:
            row = conn.execute(
                "SELECT valor FROM configuracoes_smtp WHERE chave = 'smtp_pix_chave'"
            ).fetchone()
            chave = row["valor"] if row else ""

    pix_code = gerar_payload_pix(chave, float(valor_dec), descricao)

    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(pix_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_base64 = base64.b64encode(buf.getvalue()).decode()

    return templates.TemplateResponse("financeiro/pix.html", {
        "request": request,
        "atendente": atendente,
        "pix_code": pix_code,
        "qr_base64": qr_base64,
        "valor": valor_float,
        "descricao": descricao,
        "chave": chave,
    })


def _tlv(tag: str, valor: str) -> str:
    """Codifica um campo no formato Tag-Size-Value."""
    size = f"{len(valor):02d}"
    return f"{tag}{size}{valor}"


def gerar_payload_pix(chave: str, valor: float = 0, descricao: str = "") -> str:
    """Gera payload do PIX estático no padrão EMV (BR Code)."""
    # Merchant Account Information
    merchant_info = _tlv("00", "br.gov.bcb.pix") + _tlv("01", chave)
    payload = _tlv("00", "01")
    payload += _tlv("26", merchant_info)
    payload += _tlv("52", "0000")  # Merchant Category Code
    payload += _tlv("53", "986")   # Currency (986 = BRL)

    if valor > 0:
        payload += _tlv("54", f"{valor:.2f}")

    payload += _tlv("58", "BR")
    payload += _tlv("59", descricao[:25] if descricao else "Shambala")
    payload += _tlv("60", "Volta Redonda")

    # Additional Data Field Template
    payload += _tlv("62", _tlv("05", "***"))

    # CRC placeholder
    payload += "6304"

    # CRC16-CCITT
    crc = _crc16(payload)
    return payload + crc


def _crc16(data: str) -> str:
    """Calcula CRC16-CCITT."""
    crc = 0xFFFF
    for byte in data.encode():
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


# ── Histórico por pessoa ──────────────────────────────────────────────────────

@router.get("/pessoa/{pessoa_id}", response_class=HTMLResponse)
async def historico_pessoa(request: Request, pessoa_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        pessoa = conn.execute(
            "SELECT id, nome_completo, email FROM pessoas WHERE id = %s", (pessoa_id,)
        ).fetchone()
        if not pessoa:
            return RedirectResponse(url="/financeiro", status_code=303)

        movs = conn.execute(
            """SELECT fm.*
               FROM financeiro_movimentacoes fm
               WHERE fm.pessoa_id = %s
               ORDER BY fm.data_movimentacao DESC, fm.id DESC""",
            (pessoa_id,),
        ).fetchall()

    return templates.TemplateResponse("financeiro/pessoa.html", {
        "request": request,
        "atendente": atendente,
        "pessoa": dict(pessoa),
        "movs": [dict(m) for m in movs],
    })


# ── Histórico por trabalhador ─────────────────────────────────────────────────

@router.get("/trabalhador/{trabalhador_id}", response_class=HTMLResponse)
async def historico_trabalhador(request: Request, trabalhador_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        trabalhador = conn.execute(
            "SELECT id, nome_completo, valor_mensalidade, dia_vencimento FROM trabalhadores WHERE id = %s",
            (trabalhador_id,),
        ).fetchone()
        if not trabalhador:
            return RedirectResponse(url="/financeiro", status_code=303)

        movs = conn.execute(
            """SELECT fm.*
               FROM financeiro_movimentacoes fm
               WHERE fm.trabalhador_id = %s
               ORDER BY fm.data_movimentacao DESC, fm.id DESC""",
            (trabalhador_id,),
        ).fetchall()

    return templates.TemplateResponse("financeiro/trabalhador.html", {
        "request": request,
        "atendente": atendente,
        "trabalhador": dict(trabalhador),
        "movs": [dict(m) for m in movs],
    })


# ── Relatórios ────────────────────────────────────────────────────────────────

@router.get("/relatorios", response_class=HTMLResponse)
async def relatorios_financeiro(
    request: Request,
    data_inicio: str = "",
    data_fim: str = "",
    tipo: str = "",
    categoria: str = "",
    conta_id: str = "",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    filtros = []
    params = []
    recusas = []

    # Datas validadas antes de virar filtro: texto solto no LIKE/comparação é
    # a tela exibindo "nenhum resultado" para o que era um erro de digitação.
    for rotulo, valor in (("inicial", data_inicio), ("final", data_fim)):
        if valor:
            try:
                date.fromisoformat(valor.strip())
            except ValueError:
                recusas.append(f"Data {rotulo} “{valor}” inválida — o filtro não "
                               "foi aplicado.")
                if rotulo == "inicial":
                    data_inicio = ""
                else:
                    data_fim = ""

    if data_inicio:
        filtros.append("fm.data_movimentacao >= %s")
        params.append(data_inicio)

    if data_fim:
        filtros.append("fm.data_movimentacao <= %s")
        params.append(data_fim)

    if tipo in ("entrada", "saida"):
        filtros.append("fm.tipo = %s")
        params.append(tipo)
    elif tipo:
        recusas.append("Tipo fora de Entrada/Saída — o filtro não foi aplicado.")
        tipo = ""

    if categoria in ROTULOS_CATEGORIA:
        filtros.append("fm.categoria = %s")
        params.append(categoria)
    elif categoria:
        recusas.append("Categoria desconhecida — o filtro não foi aplicado.")
        categoria = ""

    if conta_id:
        if conta_id.strip().isdigit():
            filtros.append("fm.conta_id = %s")
            params.append(int(conta_id))
        else:
            recusas.append("Conta inválida — o filtro não foi aplicado.")
            conta_id = ""

    where = ""
    if filtros:
        where = "WHERE " + " AND ".join(filtros)

    with conectar() as conn:
        # Um só varrimento, com os totais SEPARADOS por situação (TASK_012 §5):
        # "Total Entradas" somando pendente+cancelado é lido como dinheiro
        # recebido, e não é. A tela passa a mostrar obrigação e dinheiro como
        # linhas próprias, e o recorte de data diz qual data é.
        tot = conn.execute(
            f"""SELECT COALESCE(SUM(CASE WHEN fm.tipo='entrada' AND fm.status='pago'
                                         THEN fm.valor END), 0) AS entradas_pagas,
                       COALESCE(SUM(CASE WHEN fm.tipo='saida' AND fm.status='pago'
                                         THEN fm.valor END), 0) AS saidas_pagas,
                       COALESCE(SUM(CASE WHEN fm.tipo='entrada' AND fm.status='pendente'
                                         THEN fm.valor END), 0) AS entradas_pendentes,
                       COALESCE(SUM(CASE WHEN fm.tipo='saida' AND fm.status='pendente'
                                         THEN fm.valor END), 0) AS saidas_pendentes,
                       COALESCE(SUM(CASE WHEN fm.status='cancelado' THEN fm.valor END), 0) AS cancelados,
                       COUNT(*) FILTER (WHERE fm.categoria IN ('transferencia','sangria')) AS transferencias,
                       COUNT(*) AS n
                FROM financeiro_movimentacoes fm {where}""",
            params,
        ).fetchone()

        movs = conn.execute(
            f"""SELECT fm.*,
                       t.nome_completo AS trabalhador_nome,
                       p.nome_completo AS pessoa_nome,
                       c.nome AS conta_nome
                FROM financeiro_movimentacoes fm
                LEFT JOIN trabalhadores t ON t.id = fm.trabalhador_id
                LEFT JOIN pessoas p ON p.id = fm.pessoa_id
                LEFT JOIN contas_financeiras c ON c.id = fm.conta_id
                {where}
                ORDER BY fm.data_movimentacao DESC, fm.id DESC
                LIMIT 200""",
            params,
        ).fetchall()

        contas = conn.execute(
            "SELECT id, nome FROM contas_financeiras WHERE ativo = 1 ORDER BY id"
        ).fetchall()

    return templates.TemplateResponse("financeiro/relatorios.html", {
        "request": request,
        "atendente": atendente,
        "tot": {k: (v if k in ("n", "transferencias") else _dec(v))
                for k, v in dict(tot).items()},
        "movs": [dict(m) for m in movs],
        "recusas": recusas,
        "rotulos_categoria": ROTULOS_CATEGORIA,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "tipo": tipo,
        "categoria": categoria,
        "conta_id": conta_id,
        "contas": [dict(c) for c in contas],
    })


# ── Relatório de Mensalidades (extrato do mês / não pagas / por trabalhador) ──

# Qual data o filtro "Mês" usa. A ordem exige que o filtro explicite a data:
# competência é o mês A QUE A COBRANÇA SE REFERE; vencimento é quando deveria
# ser paga; pagamento é quando o dinheiro entrou. São perguntas diferentes e a
# tela diz qual está fazendo.
_DATA_USO = {
    "comp": ("Competência — o mês a que a cobrança se refere",
             "(fm.competencia = %s OR (fm.competencia IS NULL "
             "AND substr(fm.data_movimentacao, 1, 7) = %s))"),
    "venc": ("Vencimento — a data em que a mensalidade vence",
             "substr(fm.data_movimentacao, 1, 7) = %s"),
    "pag": ("Data do pagamento — o mês em que o dinheiro entrou",
            "substr(fm.data_pagamento, 1, 7) = %s"),
}


@router.get("/mensalidades/relatorio", response_class=HTMLResponse)
async def relatorio_mensalidades(
    request: Request,
    mes: str = "",
    status: str = "",
    trabalhador_id: int = 0,
    visao: str = "mes",
    data_uso: str = "comp",
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    from rotas.relatorios import _hoje_sp
    from rotas.financeiro_mensalidades import normalizar_competencia
    hoje = _hoje_sp()
    recusa = ""
    if not data_uso or data_uso not in _DATA_USO:
        data_uso = "comp"
    mes_ref = normalizar_competencia(mes) if mes.strip() else ""
    if not mes_ref:
        if mes.strip():
            recusa = (f"Período “{mes.strip()}” não é um mês válido (AAAA-MM) — o "
                      "relatório usou o mês corrente.")
        mes_ref = f"{hoje.year}-{hoje.month:02d}"
    rotulo_data, clausula = _DATA_USO[data_uso]

    with conectar() as conn:
        trabalhadores = conn.execute(
            "SELECT id, nome_completo FROM trabalhadores ORDER BY nome_completo"
        ).fetchall()

        if data_uso == "comp":
            params_mes = [mes_ref, mes_ref]
        else:
            params_mes = [mes_ref]
        filtros = ["fm.categoria = 'mensalidade'", clausula]
        params = list(params_mes)
        if status:
            filtros.append("fm.status = %s")
            params.append(status)
        if trabalhador_id:
            filtros.append("fm.trabalhador_id = %s")
            params.append(int(trabalhador_id))
        where = "WHERE " + " AND ".join(filtros)

        if visao == "trabalhador":
            linhas = [dict(r) for r in conn.execute(
                f"""SELECT t.id, t.nome_completo,
                           COALESCE(SUM(CASE WHEN fm.status='pago' THEN fm.valor ELSE 0 END),0) AS pago,
                           COALESCE(SUM(CASE WHEN fm.status='pendente' THEN fm.valor ELSE 0 END),0) AS pendente,
                           COALESCE(SUM(CASE WHEN fm.status='pendente' AND fm.data_movimentacao < %s THEN fm.valor ELSE 0 END),0) AS vencido
                    FROM financeiro_movimentacoes fm
                    JOIN trabalhadores t ON t.id = fm.trabalhador_id
                    {where}
                    GROUP BY t.id, t.nome_completo
                    ORDER BY t.nome_completo""",
                [hoje.isoformat()] + params,
            ).fetchall()]
            resumo = None
        else:
            linhas = []
            for r in conn.execute(
                f"""SELECT fm.*, t.nome_completo AS trabalhador_nome
                    FROM financeiro_movimentacoes fm
                    LEFT JOIN trabalhadores t ON t.id = fm.trabalhador_id
                    {where}
                    ORDER BY COALESCE(fm.competencia, substr(fm.data_movimentacao, 1, 7)),
                             fm.data_movimentacao, t.nome_completo""",
                params,
            ).fetchall():
                d = dict(r)
                d["competencia"] = d["competencia"] or str(d["data_movimentacao"])[:7]
                d["competencia_legada"] = r["competencia"] is None
                linhas.append(d)
            resumo = conn.execute(
                f"""SELECT COALESCE(SUM(CASE WHEN fm.status='pago' THEN fm.valor ELSE 0 END),0) AS total_pago,
                           COALESCE(SUM(CASE WHEN fm.status='pendente' THEN fm.valor ELSE 0 END),0) AS total_pendente,
                           COALESCE(SUM(CASE WHEN fm.status='pendente' AND fm.data_movimentacao < %s THEN fm.valor ELSE 0 END),0) AS total_vencido
                    FROM financeiro_movimentacoes fm {where}""",
                [hoje.isoformat()] + params,
            ).fetchone()

    return templates.TemplateResponse("financeiro/relatorio_mensalidades.html", {
        "request": request,
        "atendente": atendente,
        "mes_ref": mes_ref,
        "status": status,
        "visao": visao,
        "data_uso": data_uso,
        "rotulos_data": [(chave, _DATA_USO[chave][0]) for chave in _DATA_USO],
        "rotulo_data": rotulo_data,
        "recusa": recusa,
        "hoje_sp": hoje.isoformat(),
        "trabalhador_id": trabalhador_id,
        "trabalhadores": [dict(t) for t in trabalhadores],
        "linhas": linhas,
        "resumo": dict(resumo) if resumo else None,
    })


# ── Relatório de Fiados por Trabalhador ─────────────────────────────────────

@router.get("/fiados", response_class=HTMLResponse)
async def relatorio_fiados(request: Request, trabalhador_id: int = 0):
    """Relatório de fiado por trabalhador (TASK_012 §5).

    Cada venda mostra valor original, crédito consumido na compra, o que já foi
    RECEBIDO contra ela (das alocações ligadas ao lançamento) e o saldo — e não
    um único "pendente" que tratava dívida abatida em parte como se estivesse
    inteira. Crédito do trabalhador aparece à parte: crédito não quita dívida
    exibida, são números de naturezas diferentes.
    """
    atendente, redir = _guard(request)
    if redir:
        return redir

    import servico_fiado

    with conectar() as conn:
        trabalhadores = [dict(t) for t in conn.execute(
            """SELECT id, nome_completo, cpf,
                      fiado_limite, fiado_credito, fiado_data_encerramento
               FROM trabalhadores
               WHERE ativo = 1
               ORDER BY nome_completo"""
        ).fetchall()]

        saldos = servico_fiado.saldo_por_trabalhador(conn)
        for t in trabalhadores:
            t["saldo"] = saldos.get(t["id"], {}).get("saldo", ZERO)
            t["dividas"] = saldos.get(t["id"], {}).get("dividas", 0)

        trab_sel = None
        vendas, totais = [], None
        if trabalhador_id:
            trab_sel = conn.execute(
                """SELECT id, nome_completo, cpf, fiado_limite,
                          fiado_credito, fiado_data_encerramento
                   FROM trabalhadores WHERE id = %s""",
                (trabalhador_id,)
            ).fetchone()

            if trab_sel:
                trab_sel = dict(trab_sel)
                trab_sel["fiado_credito"] = _dec(trab_sel["fiado_credito"])
                trab_sel["fiado_limite"] = _dec(trab_sel["fiado_limite"])
                vendas, totais = servico_fiado.historico_fiado(conn, trabalhador_id)

    return templates.TemplateResponse("financeiro/fiado_trabalhador.html", {
        "request": request,
        "atendente": atendente,
        "trabalhadores": trabalhadores,
        "trabalhador_sel": trab_sel,
        "vendas": vendas,
        "totais": totais,
    })


# ── Transferência entre Contas ────────────────────────────────────────────

@router.get("/transferir", response_class=HTMLResponse)
async def form_transferir(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        contas = conn.execute(
            "SELECT id, nome FROM contas_financeiras WHERE ativo = 1 ORDER BY id"
        ).fetchall()

    return templates.TemplateResponse("financeiro/transferir.html", {
        "request": request,
        "atendente": atendente,
        "contas": [dict(c) for c in contas],
    })


@router.post("/transferir", response_class=HTMLResponse)
async def salvar_transferencia(
    request: Request,
    conta_origem: int = Form(...),
    conta_destino: int = Form(...),
    valor: str = Form("0"),
    descricao: str = Form(""),
    idempotencia: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    # Mesmo parser do lançamento novo: duas casas ROUND_HALF_UP e teto do
    # NUMERIC. O `quantize` solto arredondava meio centavo para par.
    from servico_idempotencia import (
        TentativaJaRegistrada, TentativaRecusada, confirmar, exigir_reserva,
    )
    from servico_sessao import SessaoInvalida, moeda as _moeda_form
    try:
        valor_dec = _moeda_form(valor, "valor")
    except SessaoInvalida:
        return RedirectResponse(url="/financeiro/transferir?erro=valor_invalido",
                                status_code=303)

    if conta_origem == conta_destino:
        return RedirectResponse(url="/financeiro/transferir?erro=contas_iguais", status_code=303)

    desc = descricao.strip() or "Transferência entre contas"
    hoje = date.today().isoformat()

    with conectar() as conn:
        try:
            chave_tentativa = exigir_reserva(
                conn, idempotencia=idempotencia, operacao="transferencia",
                autor_id=atendente["id"],
                corpo={"origem": conta_origem, "destino_conta": conta_destino,
                       "valor": str(valor_dec), "descricao": desc})
        except TentativaRecusada as e:
            return RedirectResponse(url=f"/financeiro/transferir?erro={e.chave_erro}",
                                    status_code=303)
        except TentativaJaRegistrada as e:
            return RedirectResponse(url=e.destino or "/financeiro", status_code=303)

        # Saída da origem
        cur = conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, conta_id, status)
               VALUES ('saida', 'transferencia', %s, %s, %s, %s, 'pago')
               RETURNING id""",
            (valor_dec, hoje, f"{desc} (origem)", conta_origem)
        )
        saida_id = cur.fetchone()["id"]
        # Entrada no destino
        conn.execute(
            """INSERT INTO financeiro_movimentacoes
               (tipo, categoria, valor, data_movimentacao, descricao, conta_id, status)
               VALUES ('entrada', 'transferencia', %s, %s, %s, %s, 'pago')""",
            (valor_dec, hoje, f"{desc} (destino)", conta_destino)
        )
        confirmar(conn, chave=chave_tentativa, documento=saida_id,
                  destino="/financeiro?transferencia_ok=1")

    return RedirectResponse(url="/financeiro?transferencia_ok=1", status_code=303)
