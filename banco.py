import os
import unicodedata
from datetime import date, timedelta
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool, sql
from psycopg2.extras import RealDictCursor
from psycopg2.errors import DuplicateColumn

# ── Wrapper para compatibilidade sqlite3 ↔ psycopg2 ──────────────────────────

class _ConnCompat:
    """Envolve a conexão psycopg2 e fornece interface compatível com sqlite3."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=()):
        cur = self._conn.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, value):
        pass  # já usamos RealDictCursor

    def cursor(self):
        return self._conn.cursor()


# ── Conexão via pool ─────────────────────────────────────────────────────────

_DB_HOST = os.environ.get("SHAMBALA_DB_HOST", "localhost")
_DB_PORT = int(os.environ.get("SHAMBALA_DB_PORT", "5432"))
_DB_NAME = os.environ.get("SHAMBALA_DB_NAME", "shambala")
_DB_USER = os.environ.get("SHAMBALA_DB_USER", "shambala")
_DB_PASS = os.environ.get("SHAMBALA_DB_PASS", "")

_pool: pool.SimpleConnectionPool = None


def _obter_pool() -> pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            host=_DB_HOST,
            port=_DB_PORT,
            dbname=_DB_NAME,
            user=_DB_USER,
            password=_DB_PASS,
        )
    return _pool


def fechar_pool():
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None


@contextmanager
def conectar():
    """Context manager que devolve conexão compatível com sqlite3."""
    conn = _obter_pool().getconn()
    conn.cursor_factory = RealDictCursor
    try:
        yield _ConnCompat(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _obter_pool().putconn(conn)


# ── Normalização de texto ────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    """Remove acentos e coloca em minúsculas para busca."""
    if texto is None:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode()
    return sem_acento.lower()


# ── Dias de atendimento ──────────────────────────────────────────────────────

def _dias_atendimento(conn) -> list:
    """Retorna lista de dias da semana com atendimento (0=seg … 6=dom)."""
    rows = conn.execute(
        "SELECT dia_semana FROM dias_atendimento ORDER BY dia_semana"
    ).fetchall()
    return [r["dia_semana"] for r in rows] if rows else [0, 2]


def _proxima_data_trabalho(a_partir: date, dias_semana: list = None) -> date:
    """Retorna a próxima data de atendimento a partir de 'a_partir', inclusive."""
    if dias_semana is None:
        dias_semana = [0, 2]
    d = a_partir
    for _ in range(14):
        if d.weekday() in dias_semana:
            return d
        d += timedelta(days=1)
    return d


# Intervalo entre sessões por frequência. "avulso" (ou valor desconhecido) = 0,
# isto é, uma única sessão na primeira data de atendimento disponível.
_INTERVALO_FREQUENCIA = {
    "semanal": 7,
    "quinzenal": 14,
    "mensal": 28,
    "avulso": 0,
}


# ── Regra do passe magnético ──────────────────────────────────────────────────

PASSE_CHAVE_VAZIA = "nenhum"


def calcular_sessoes_com_passe(total: int, configurado: int) -> int:
    """Quantas das `total` sessões exigem passe magnético.

    Regra do centro (decidida pelo proprietário — ver
    docs/investigacoes/INVESTIGACAO_TRES_PASSES_ACOLHIMENTO.md, R1/R4):
    as TRÊS primeiras sessões de todo plano sempre exigem passe,
    independentemente do valor configurado no plano. O valor configurado só
    amplia a obrigatoriedade a partir da quarta sessão.

    - plano com 1 ou 2 sessões: todas exigem passe (sem criar sessões extras);
    - configuração 0 vinda de tela antiga: o piso de 3 vence;
    - configuração negativa (legado): todas as sessões exigem passe.
    """
    if total <= 0:
        return 0
    if configurado < 0:
        return total
    return min(max(3, configurado), total)


def datas_agendamento(inicio: date, quantidade: int,
                      frequencia: str, dias_semana: list) -> list:
    """Datas (ISO) das sessões, sempre em dias de atendimento cadastrados.

    `frequencia` avulsa (ou desconhecida) gera UMA única data, na primeira data
    de atendimento — não há série a repetir. É a fonte única do calendário de
    agendamentos, usada pelo Acolhimento Fraterno, pelo cadastro de médium e
    pela Agenda.
    """
    if quantidade <= 0:
        return []
    delta = _INTERVALO_FREQUENCIA.get(frequencia, 0)
    if delta == 0:
        return [_proxima_data_trabalho(inicio, dias_semana).isoformat()]
    atual = _proxima_data_trabalho(inicio, dias_semana)
    datas = []
    for _ in range(quantidade):
        datas.append(atual.isoformat())
        atual = _proxima_data_trabalho(atual + timedelta(days=delta), dias_semana)
    return datas


def data_atendimento_valida(conn, valor: str) -> bool:
    """A data informada cai em um dia de atendimento cadastrado?

    O calendário é dinâmico: lido de `dias_atendimento` a cada chamada, nunca
    fixado em segunda/quarta.
    """
    try:
        data = date.fromisoformat(valor)
    except (TypeError, ValueError):
        return False
    return data.weekday() in _dias_atendimento(conn)


def gerar_agendamentos_plano(conn, plano_id: int, inicio: date,
                              frequencia: str, total: int, sessoes_com_passe: int):
    """Gera agendamentos automáticos de um plano (fonte única da regra).

    - quantidade de sessões e quantidade de passes são conceitos diferentes;
    - a obrigatoriedade de passe é calculada por `calcular_sessoes_com_passe`;
    - as datas respeitam sempre os dias de atendimento cadastrados;
    - frequência avulsa gera uma única sessão, na data de atendimento mais
      próxima (o comportamento de "não gerar nada" escondia as sessões).
    """
    dias = _dias_atendimento(conn)
    com_passe = calcular_sessoes_com_passe(total, sessoes_com_passe)
    for i, data_iso in enumerate(datas_agendamento(inicio, total, frequencia, dias)):
        conn.execute(
            """INSERT INTO agendamentos (plano_id, data, status, requer_passe, encaixe)
               VALUES (%s, %s, 'agendado', %s, 0)""",
            (plano_id, data_iso, 1 if i < com_passe else 0),
        )


def planos_ativos_da_pessoa(conn, pessoa_id: int) -> list:
    """Planos de tratamento com status='ativo' nos quais a pessoa está vinculada."""
    return conn.execute(
        """SELECT pt.id, pt.medium_id, pt.data_inicio, pt.sessoes_total,
                  pt.sessoes_realizadas, pt.status
           FROM planos_tratamento pt
           JOIN plano_pessoas pp ON pp.plano_id = pt.id
           WHERE pp.pessoa_id = %s AND pt.status = 'ativo'
           ORDER BY pt.data_inicio DESC, pt.id DESC""",
        (pessoa_id,),
    ).fetchall()


def encerrar_planos_ativos(conn, pessoa_id: int, exceto_plano_id: int = None) -> list:
    """Invalida os planos ativos da pessoa (regra: um plano por pessoa por vez).

    Chamada quando um NOVO plano é criado pelo Acolhimento Fraterno: os planos
    antigos ainda marcados como ativos deixam de valer — status 'cancelado' e
    agendamentos pendentes cancelados. O histórico (realizado/faltou) é
    preservado. Retorna os ids encerrados.
    """
    rows = planos_ativos_da_pessoa(conn, pessoa_id)
    encerrados = [r["id"] for r in rows if r["id"] != exceto_plano_id]
    for plano_id in encerrados:
        conn.execute(
            "UPDATE planos_tratamento SET status='cancelado' WHERE id = %s",
            (plano_id,),
        )
        conn.execute(
            "UPDATE agendamentos SET status='cancelado' WHERE plano_id = %s AND status='agendado'",
            (plano_id,),
        )
    return encerrados


def faltas_consecutivas_encerram(conn, plano_id: int, hoje: str, limite: int = 2) -> bool:
    """Verifica a regra de encerramento automático do plano.

    `limite` faltas consecutivas NAS ÚLTIMAS sessões vencidas (só datas <=
    hoje; agendamentos futuros não contam) encerram o plano e cancelam os
    agendamentos pendentes. Fonte única — usada pelo encerramento do dia e
    pelo registro manual de falta na agenda do médium.
    """
    ultimos = conn.execute(
        """SELECT status FROM agendamentos
           WHERE plano_id = %s AND data <= %s
           ORDER BY data DESC, id DESC LIMIT %s""",
        (plano_id, hoje, limite),
    ).fetchall()
    if len(ultimos) >= limite and all(r["status"] == "faltou" for r in ultimos[:limite]):
        conn.execute(
            "UPDATE planos_tratamento SET status='cancelado' WHERE id = %s AND status='ativo'",
            (plano_id,),
        )
        conn.execute(
            "UPDATE agendamentos SET status='cancelado' WHERE plano_id = %s AND status='agendado'",
            (plano_id,),
        )
        return True
    return False



# ── Migrações incrementais ───────────────────────────────────────────────────

def _presenca_tem_constraint(conn, colunas: list[str]) -> bool:
    """Há constraint UNIQUE exatamente sobre `colunas` em trabalhador_presenca?"""
    rows = conn.execute(
        """SELECT c.conname AS conname, a.attname AS col
           FROM pg_constraint c
           JOIN pg_class rel ON rel.oid = c.conrelid
           JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
           JOIN pg_attribute a ON a.attrelid = rel.oid AND a.attnum = k.attnum
           WHERE rel.relname = 'trabalhador_presenca' AND c.contype = 'u'
           ORDER BY c.conname, k.ord"""
    ).fetchall()
    atuais: dict[str, list[str]] = {}
    for r in rows:
        atuais.setdefault(r["conname"], []).append(r["col"])
    return any(sorted(v) == sorted(colunas) for v in atuais.values())


def _migrar_presenca_data(conn):
    """Presença de trabalhadores ganha data própria (TASK_001).

    Modelo antigo: a data só existia via dias_trabalho (presença exigia dia de
    atendimento). Modelo novo: coluna `data` (DATE) na própria presença, com
    UNIQUE (trabalhador_id, data); `dia_trabalho_id` vira vínculo histórico
    opcional.

    Segurança: audita ANTES de alterar qualquer coisa. Dados ambíguos (data
    ilegível, linha órfã, trabalhador duplicado na mesma data derivada) são
    relatados caso a caso e a migração NÃO mexe no schema — nada é descartado
    ou mesclado em silêncio. Idempotente: pode rodar de novo no startup.
    """
    existe_tabela = conn.execute(
        """SELECT COUNT(*) AS c FROM information_schema.tables
           WHERE table_name = 'trabalhador_presenca'"""
    ).fetchone()["c"]
    if existe_tabela == 0:
        return

    # ── 1. Auditoria (somente leitura) ──────────────────────────────────────
    tem_data = conn.execute(
        """SELECT COUNT(*) AS c FROM information_schema.columns
           WHERE table_name = 'trabalhador_presenca' AND column_name = 'data'"""
    ).fetchone()["c"] > 0

    problemas = []
    if not tem_data:
        candidatas = conn.execute(
            """SELECT tp.id, tp.trabalhador_id, tp.dia_trabalho_id, dt.data AS data_texto
               FROM trabalhador_presenca tp
               LEFT JOIN dias_trabalho dt ON dt.id = tp.dia_trabalho_id
               ORDER BY tp.id"""
        ).fetchall()
        derivadas: dict[tuple, list[int]] = {}
        from datetime import date as _date
        for r in candidatas:
            if r["dia_trabalho_id"] is None:
                problemas.append(f"linha {r['id']}: sem vínculo com dia de trabalho — "
                                 "não é possível derivar a data")
                continue
            texto = r["data_texto"] or ""
            try:
                derivada = _date.fromisoformat(texto).isoformat()
            except ValueError:
                problemas.append(f"linha {r['id']} (trabalhador {r['trabalhador_id']}): "
                                 f"data do dia de trabalho ilegível: {texto!r}")
                continue
            chave = (r["trabalhador_id"], derivada)
            derivadas.setdefault(chave, []).append(r["id"])
        for (trab, dia), ids in sorted(derivadas.items()):
            if len(ids) > 1:
                problemas.append(f"trabalhador {trab} tem {len(ids)} registros na data "
                                 f"{dia} (linhas {ids}) — duplicidade na unicidade nova")
        if problemas:
            print("[MIGRACAO PRESENCA] DADOS AMBIGUOS — schema NAO alterado:", flush=True)
            for p in problemas:
                print(f"  - {p}", flush=True)
            return

    # ── 2. Aplicação (idempotente) ──────────────────────────────────────────
    if not tem_data:
        conn.execute("ALTER TABLE trabalhador_presenca ADD COLUMN data DATE")
        # Deriva a data dos registros antigos a partir do dia de trabalho
        # vinculado (mesma validação da auditoria — valores já confirmados).
        antigas = conn.execute(
            """SELECT tp.id, dt.data AS data_texto
               FROM trabalhador_presenca tp
               JOIN dias_trabalho dt ON dt.id = tp.dia_trabalho_id
               WHERE tp.data IS NULL ORDER BY tp.id"""
        ).fetchall()
        for r in antigas:
            conn.execute(
                "UPDATE trabalhador_presenca SET data = %s::date WHERE id = %s",
                (r["data_texto"], r["id"]),
            )

    # Vínculo com o dia de atendimento passa a ser histórico/opcional
    nullable_dia = conn.execute(
        """SELECT is_nullable AS n FROM information_schema.columns
           WHERE table_name = 'trabalhador_presenca' AND column_name = 'dia_trabalho_id'"""
    ).fetchone()
    if nullable_dia and nullable_dia["n"] == "NO":
        conn.execute(
            "ALTER TABLE trabalhador_presenca ALTER COLUMN dia_trabalho_id DROP NOT NULL"
        )

    # `data` só vira NOT NULL quando não houver linha sem data
    sem_data = conn.execute(
        "SELECT COUNT(*) AS c FROM trabalhador_presenca WHERE data IS NULL"
    ).fetchone()["c"]
    if sem_data > 0:
        print(f"[MIGRACAO PRESENCA] {sem_data} linha(s) sem data derivável — "
              "unicidade nova NÃO criada; registre a data manualmente e "
              "reinicie.", flush=True)
        return
    nullable_data = conn.execute(
        """SELECT is_nullable AS n FROM information_schema.columns
           WHERE table_name = 'trabalhador_presenca' AND column_name = 'data'"""
    ).fetchone()
    if nullable_data and nullable_data["n"] == "YES":
        conn.execute("ALTER TABLE trabalhador_presenca ALTER COLUMN data SET NOT NULL")

    # Troca a unicidade (trabalhador_id, dia_trabalho_id) por (trabalhador_id, data)
    if not _presenca_tem_constraint(conn, ["trabalhador_id", "data"]):
        antigas_u = conn.execute(
            """SELECT c.conname AS nome
               FROM pg_constraint c
               JOIN pg_class rel ON rel.oid = c.conrelid
               JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
               JOIN pg_attribute a ON a.attrelid = rel.oid AND a.attnum = k.attnum
               WHERE rel.relname = 'trabalhador_presenca' AND c.contype = 'u'
               GROUP BY c.conname, c.conkey
               HAVING array_agg(a.attname::text ORDER BY k.ord) =
                      ARRAY['trabalhador_id', 'dia_trabalho_id']"""
        ).fetchall()
        for r in antigas_u:
            conn.execute(
                f"ALTER TABLE trabalhador_presenca DROP CONSTRAINT {r['nome']}"
            )
        conn.execute(
            "ALTER TABLE trabalhador_presenca "
            "ADD CONSTRAINT trabalhador_presenca_data_unique "
            "UNIQUE (trabalhador_id, data)"
        )


def _migrar(conn):
    """Migrações incrementais — seguras para rodar múltiplas vezes."""
    para_adicionar = [
        ("checkins", "medium_id", "INTEGER REFERENCES mediuns(id)"),
        ("checkins", "codigo_acolhimento", "TEXT"),
        ("checkins", "acolhimento_realizado", "INTEGER NOT NULL DEFAULT 0"),
        ("mediuns", "vagas_dia", "INTEGER NOT NULL DEFAULT 10"),
        ("planos_tratamento", "frequencia", "TEXT NOT NULL DEFAULT 'semanal'"),
        ("planos_tratamento", "status", "TEXT NOT NULL DEFAULT 'ativo'"),
        ("planos_tratamento", "sessoes_com_passe", "INTEGER NOT NULL DEFAULT 3"),
        ("checkins", "agendamento_id", "INTEGER REFERENCES agendamentos(id)"),
        ("pessoas", "data_nascimento", "TEXT"),
        ("pessoas", "deficiencia", "INTEGER NOT NULL DEFAULT 0"),
        ("pessoas", "prioridade", "INTEGER NOT NULL DEFAULT 0"),
        ("checkins", "codigo_reiki", "TEXT"),
        ("checkins", "reiki_realizado", "INTEGER NOT NULL DEFAULT 0"),
        ("checkins", "acolhimento_chamado", "INTEGER NOT NULL DEFAULT 0"),
        ("atendentes", "grupo_id", "INTEGER REFERENCES grupos(id)"),
        ("trabalhadores", "cpf", "TEXT UNIQUE"),
        ("grupos_permissoes", "ler", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("grupos_permissoes", "escrever", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("grupos_permissoes", "apagar", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("pessoas", "foto_pessoa", "TEXT"),
        ("mediuns", "foto_medium", "TEXT"),
        ("trabalhadores", "foto_trabalhador", "TEXT"),
        ("trabalhadores", "rg", "TEXT"),
        ("trabalhadores", "data_nascimento", "TEXT"),
        ("trabalhadores", "cep", "TEXT"),
        ("trabalhadores", "logradouro", "TEXT"),
        ("trabalhadores", "numero", "TEXT"),
        ("trabalhadores", "complemento", "TEXT"),
        ("trabalhadores", "bairro", "TEXT"),
        ("trabalhadores", "cidade", "TEXT"),
        ("trabalhadores", "uf", "TEXT"),
        ("trabalhadores", "valor_mensalidade", "NUMERIC(10,2) DEFAULT 0"),
        ("trabalhadores", "dia_vencimento", "INTEGER DEFAULT 10"),
        ("financeiro_movimentacoes", "status", "TEXT NOT NULL DEFAULT 'pago'"),
        ("pessoas", "cpf", "TEXT UNIQUE"),
        ("mediuns_dia", "vagas_dia", "INTEGER"),
        ("financeiro_movimentacoes", "caixa_id", "INTEGER REFERENCES caixas(id)"),
        ("caixas", "chave_pix_id", "INTEGER REFERENCES chaves_pix(id)"),
        ("vendas_pdv", "fiado_trabalhador_id", "INTEGER REFERENCES trabalhadores(id)"),
        ("vendas_pdv", "fiado_pago", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("vendas_pdv", "fiado_data_pagamento", "TEXT"),
        ("trabalhadores", "fiado_limite", "NUMERIC(10,2) NOT NULL DEFAULT 0"),
        ("trabalhadores", "fiado_data_encerramento", "DATE"),
        ("trabalhadores", "fiado_credito", "NUMERIC(10,2) NOT NULL DEFAULT 0"),
        ("vendas_pdv", "fiado_credito_usado", "NUMERIC(10,2) NOT NULL DEFAULT 0"),
        ("vendas_pdv", "caixa_movimento_id", "INTEGER REFERENCES caixa_movimentos(id)"),
        ("produtos", "foto_produto", "TEXT"),
        ("produtos", "atalho", "INTEGER NOT NULL DEFAULT 0"),
        ("produtos", "quantidade_estoque", "INTEGER NOT NULL DEFAULT 0"),
        ("produtos", "caixa_id", "INTEGER REFERENCES caixas(id)"),
        ("caixas", "usa_produtos", "BOOLEAN NOT NULL DEFAULT TRUE"),
        ("vendas_pdv", "doacao_valor", "NUMERIC(10,2) NOT NULL DEFAULT 0"),
        # TASK_007: instante (America/Sao_Paulo, segundos) gravado só na
        # finalização de vendas NOVAS. Linhas antigas ficam NULL — nunca é
        # preenchido por heurística; em fiado, NULL significa "sem prova de
        # temporalidade" e bloqueia o cancelamento reversível.
        ("vendas_pdv", "criada_em", "TEXT"),
        ("financeiro_movimentacoes", "forma_pagamento", "TEXT"),
        ("financeiro_movimentacoes", "conta_id", "INTEGER REFERENCES contas_financeiras(id)"),
        ("financeiro_movimentacoes", "data_pagamento", "TEXT"),
        ("financeiro_movimentacoes", "nota_fiscal_foto", "TEXT"),
        ("dias_trabalho", "reiki_ativo", "INTEGER NOT NULL DEFAULT 0"),
        ("checkins", "posicao", "INTEGER"),
        ("checkins", "observacao_acolhimento", "TEXT"),
        ("checkins", "acolhimento_cancelado", "INTEGER NOT NULL DEFAULT 0"),
        # Origem da primeira chegada (TASK_003). TEXT puro, sem DEFAULT e sem
        # backfill: linha antiga continua NULL — origem desconhecida (legado),
        # que nunca é presumida. A recepção grava 'recepcao', o terminal grava
        # 'terminal_qr'/'terminal_codigo'.
        ("trabalhador_presenca", "origem_chegada", "TEXT"),
    ]

    for tabela, coluna, tipo in para_adicionar:
        existe = conn.execute(
            """SELECT COUNT(*) AS c FROM information_schema.columns
               WHERE table_name = %s AND column_name = %s""",
            (tabela, coluna)
        ).fetchone()["c"]
        if existe > 0:
            continue
        conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")

    # Sincroniza status com campo legado concluido
    conn.execute(
        "UPDATE planos_tratamento SET status='alta' "
        "WHERE concluido=1 AND status='ativo'"
    )

    # Unifica nome_apresentacao → nome_completo
    conn.execute(
        """UPDATE pessoas SET nome_completo = nome_apresentacao
           WHERE (nome_completo IS NULL OR nome_completo = '')
             AND nome_apresentacao IS NOT NULL AND nome_apresentacao != ''"""
    )

    # Migra permissões existentes para modelo Unix (ler=TRUE, escrever=TRUE, apagar=TRUE)
    conn.execute(
        """UPDATE grupos_permissoes
           SET ler = TRUE, escrever = TRUE, apagar = TRUE
           WHERE ler = FALSE AND escrever = FALSE AND apagar = FALSE"""
    )

    # Presença de trabalhadores: data própria + unicidade (trabalhador_id, data)
    _migrar_presenca_data(conn)

    # Cria tabela chaves_pix se não existir (bancos existentes sem ela)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chaves_pix (
            id          SERIAL PRIMARY KEY,
            nome        TEXT NOT NULL,
            tipo        TEXT NOT NULL,
            chave       TEXT NOT NULL,
            cidade      TEXT,
            ativa       BOOLEAN NOT NULL DEFAULT TRUE,
            criado_em   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Usuário↔Médium: quais agendas do médium a pessoa gerencia (flexível N:N —
    # um cambona pode cuidar de vários médiuns e um médium pode ter vários).
    # É também o ESCOPO da tela "Agenda do Médium": quem não tem vínculo não vê
    # nenhuma agenda.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS atendentes_mediuns (
            id            SERIAL PRIMARY KEY,
            atendente_id  INTEGER NOT NULL REFERENCES atendentes(id) ON DELETE CASCADE,
            medium_id     INTEGER NOT NULL REFERENCES mediuns(id) ON DELETE CASCADE,
            UNIQUE(atendente_id, medium_id)
        )
    """)

    # ── Código de identificação do trabalhador (TASK_002) ──
    # Uma linha por código JÁ EMITIDO. `estado='ativo'` é o código atual do
    # trabalhador; `retirado` fica reservado para sempre (nunca é reatribuído,
    # nem à mesma pessoa), o que impede que uma carteirinha antiga identifique
    # outra pessoa. Único lugar onde o código existe — não há coluna em
    # `trabalhadores` (evita segunda fonte de verdade).
    #
    # DDL apenas: o startup NUNCA emite nem troca códigos. A emissão inicial dos
    # ativos é ação explícita (servico_codigo_trabalhador).
    #
    # ON DELETE RESTRICT: apagar fisicamente um trabalhador não pode levar os
    # códigos dele embora — a reserva permanente é o que impede que um código
    # volte a ser emitido e identifique outra pessoa na carteirinha. A saída é
    # inativar (o código fica reservado com a pessoa), não excluir.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trabalhador_codigo (
            codigo          TEXT    PRIMARY KEY CHECK (codigo ~ '^[0-9]{4}$'),
            trabalhador_id  INTEGER NOT NULL REFERENCES trabalhadores(id) ON DELETE RESTRICT,
            estado          TEXT    NOT NULL DEFAULT 'ativo'
                                    CHECK (estado IN ('ativo', 'retirado')),
            atendente_id    INTEGER REFERENCES atendentes(id),
            origem          TEXT    NOT NULL DEFAULT 'tela',
            criado_em       TEXT    NOT NULL,
            retirado_em     TEXT
        )
    """)
    # A regra de exclusão vive na constraint de coluna, não na cláusula CHECK,
    # então CREATE TABLE IF NOT EXISTS não a corrige em banco já criado: troca-se
    # a FK aqui, sem apagar, regenerar ou reordenar nenhum código. Idempotente
    # (reexecutar não encontra mais o desvio) e transacional com o resto do
    # startup — se falhar, nada do schema fica pela metade.
    #
    # Os %% são % literais: o texto passa pela interpolação do psycopg2.
    conn.execute("""
        DO $mig$
        DECLARE
            nome  text;
            regra "char";
        BEGIN
            SELECT c.conname, c.confdeltype INTO nome, regra
              FROM pg_constraint c
              JOIN pg_attribute a
                ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
             WHERE c.contype = 'f'
               AND c.conrelid = 'trabalhador_codigo'::regclass
               AND a.attname = 'trabalhador_id';
            IF nome IS NOT NULL AND regra <> 'r' THEN
                EXECUTE format('ALTER TABLE trabalhador_codigo DROP CONSTRAINT %%I', nome);
                EXECUTE format('ALTER TABLE trabalhador_codigo '
                               'ADD CONSTRAINT %%I FOREIGN KEY (trabalhador_id) '
                               'REFERENCES trabalhadores(id) ON DELETE RESTRICT', nome);
                RAISE NOTICE 'FK trabalhador_codigo.trabalhador_id: regra %%s -> RESTRICT', regra;
            END IF;
        END
        $mig$
    """)
    # Exatamente um código atual por trabalhador (as linhas retiradas coexistem).
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_trabalhador_codigo_atual
            ON trabalhador_codigo (trabalhador_id)
            WHERE estado = 'ativo'
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_trabalhador_codigo_trab
            ON trabalhador_codigo (trabalhador_id)
    """)

    # Índices para consultas de fiado
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendas_fiado_trab ON vendas_pdv(fiado_trabalhador_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendas_fiado_pendentes ON vendas_pdv(fiado_trabalhador_id) WHERE forma_pagamento = 'fiado' AND fiado_pago = FALSE")

    # Índice parcial para mensalidades por trabalhador
    conn.execute("CREATE INDEX IF NOT EXISTS idx_financeiro_mensalidade_trab ON financeiro_movimentacoes(trabalhador_id) WHERE categoria = 'mensalidade'")

    # Migrar dados históricos para Conta Antiga
    conn.execute(
        "UPDATE financeiro_movimentacoes SET conta_id = (SELECT id FROM contas_financeiras WHERE tipo = 'legado') WHERE conta_id IS NULL"
    )

    # Mensalidades pendentes passam a pertencer à conta "Valores a Receber"
    conn.execute(
        """UPDATE financeiro_movimentacoes fm
           SET conta_id = (SELECT id FROM contas_financeiras WHERE tipo = 'areceber')
           WHERE fm.categoria = 'mensalidade' AND fm.status = 'pendente'
             AND (fm.conta_id IS NULL
                  OR fm.conta_id = (SELECT id FROM contas_financeiras WHERE tipo = 'legado'))"""
    )


# ── Criação de tabelas ───────────────────────────────────────────────────────

def criar_tabelas():
    with conectar() as conn:
        # Extensão para remoção de acentos em buscas SQL
        conn.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

        # Função SQL norm() equivalente ao sqlite3.create_function
        conn.execute(
            """CREATE OR REPLACE FUNCTION norm(texto TEXT)
               RETURNS TEXT AS $$
                   SELECT lower(unaccent(texto))
               $$ LANGUAGE SQL IMMUTABLE"""
        )

        # Função data_valida(): cast seguro de texto para date — datas inválidas
        # (ex.: 2001-02-29) retornam NULL em vez de erro de conversão
        conn.execute(
            """CREATE OR REPLACE FUNCTION data_valida(texto TEXT)
               RETURNS DATE AS $$
               BEGIN
                   IF texto IS NULL OR texto = '' THEN RETURN NULL; END IF;
                   RETURN texto::date;
               EXCEPTION WHEN OTHERS THEN RETURN NULL;
               END;
               $$ LANGUAGE plpgsql IMMUTABLE"""
        )

        # ── Tabelas principais ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pessoas (
                id                  SERIAL PRIMARY KEY,
                nome_apresentacao   TEXT NOT NULL,
                nome_completo       TEXT,
                telefone            TEXT,
                email               TEXT,
                cep                 TEXT,
                logradouro          TEXT,
                numero              TEXT,
                complemento         TEXT,
                bairro              TEXT,
                cidade              TEXT,
                uf                  TEXT,
                foto_pessoa         TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS lacos (
                id                      SERIAL PRIMARY KEY,
                pessoa_id               INTEGER NOT NULL REFERENCES pessoas(id),
                pessoa_relacionada_id   INTEGER NOT NULL REFERENCES pessoas(id),
                tipo_laco               TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS mediuns (
                id              SERIAL PRIMARY KEY,
                nome_completo   TEXT NOT NULL,
                telefone        TEXT,
                email           TEXT,
                cep             TEXT,
                logradouro      TEXT,
                numero          TEXT,
                complemento     TEXT,
                bairro          TEXT,
                cidade          TEXT,
                uf              TEXT,
                ativo           INTEGER NOT NULL DEFAULT 1,
                vagas_dia       INTEGER NOT NULL DEFAULT 10,
                foto_medium     TEXT
            )
        """)

        # ── Grupos e Permissões ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grupos (
                id          SERIAL PRIMARY KEY,
                nome        TEXT NOT NULL UNIQUE,
                descricao   TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS grupos_permissoes (
                id          SERIAL PRIMARY KEY,
                grupo_id    INTEGER NOT NULL REFERENCES grupos(id) ON DELETE CASCADE,
                modulo      TEXT NOT NULL,
                ler         BOOLEAN NOT NULL DEFAULT FALSE,
                escrever    BOOLEAN NOT NULL DEFAULT FALSE,
                apagar      BOOLEAN NOT NULL DEFAULT FALSE,
                UNIQUE(grupo_id, modulo)
            )
        """)

        # ── Trabalhadores ──
        # (criada aqui porque fiado_credito_mov a referencia)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trabalhadores (
                id                  SERIAL PRIMARY KEY,
                nome_completo       TEXT NOT NULL,
                cpf                 TEXT UNIQUE,
                rg                  TEXT,
                data_nascimento     TEXT,
                telefone            TEXT,
                email               TEXT,
                cep                 TEXT,
                logradouro          TEXT,
                numero              TEXT,
                complemento         TEXT,
                bairro              TEXT,
                cidade              TEXT,
                uf                  TEXT,
                valor_mensalidade   NUMERIC(10,2) DEFAULT 0,
                dia_vencimento      INTEGER DEFAULT 10,
                ativo               INTEGER NOT NULL DEFAULT 1,
                foto_trabalhador    TEXT,
                created_at          TEXT NOT NULL DEFAULT (CURRENT_DATE AT TIME ZONE 'America/Sao_Paulo')::date::text
            )
        """)

        # ── Atendentes ──
        # (criada aqui porque vendas_pdv, caixa_movimentos, caixa_sangrias
        #  e estoque_movimentos a referenciam)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS atendentes (
                id              SERIAL PRIMARY KEY,
                nome_usuario    TEXT NOT NULL UNIQUE,
                nome_completo   TEXT NOT NULL,
                senha_hash      TEXT NOT NULL,
                telefone        TEXT,
                email           TEXT,
                ativo           INTEGER NOT NULL DEFAULT 1,
                grupo_id        INTEGER REFERENCES grupos(id)
            )
        """)

        # ── Configurações SMTP ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS configuracoes_smtp (
                id              SERIAL PRIMARY KEY,
                chave           TEXT NOT NULL UNIQUE,
                valor           TEXT NOT NULL DEFAULT ''
            )
        """)

        # ── Configurações de Backup Remoto ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS configuracoes_backup (
                id          SERIAL PRIMARY KEY,
                chave       TEXT NOT NULL UNIQUE,
                valor       TEXT NOT NULL DEFAULT ''
            )
        """)

        # ── Configurações do Centro (nome e logo) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS configuracoes_centro (
                id          SERIAL PRIMARY KEY,
                chave       TEXT NOT NULL UNIQUE,
                valor       TEXT NOT NULL DEFAULT ''
            )
        """)

        # ── Chaves PIX ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chaves_pix (
                id          SERIAL PRIMARY KEY,
                nome        TEXT NOT NULL,
                tipo        TEXT NOT NULL,
                chave       TEXT NOT NULL,
                cidade      TEXT,
                ativa       BOOLEAN NOT NULL DEFAULT TRUE,
                criado_em   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Caixas (PDV) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS caixas (
                id            SERIAL PRIMARY KEY,
                nome          TEXT NOT NULL UNIQUE,
                descricao     TEXT,
                ativo         INTEGER NOT NULL DEFAULT 1,
                chave_pix_id  INTEGER REFERENCES chaves_pix(id) ON DELETE SET NULL
            )
        """)

        # ── Contas Financeiras ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contas_financeiras (
                id          SERIAL PRIMARY KEY,
                nome        TEXT NOT NULL UNIQUE,
                tipo        TEXT NOT NULL,
                ativo       INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL DEFAULT (CURRENT_DATE AT TIME ZONE 'America/Sao_Paulo')::date::text
            )
        """)

        # ── Produtos (PDV) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS produtos (
                id              SERIAL PRIMARY KEY,
                nome            TEXT NOT NULL,
                categoria       TEXT NOT NULL DEFAULT 'outro',
                preco_custo     NUMERIC(10,2) NOT NULL DEFAULT 0,
                preco_venda     NUMERIC(10,2) NOT NULL DEFAULT 0,
                codigo_barras   TEXT UNIQUE,
                ativo           INTEGER NOT NULL DEFAULT 1
            )
        """)

        # ── Vendas PDV ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vendas_pdv (
                id              SERIAL PRIMARY KEY,
                caixa_id        INTEGER NOT NULL REFERENCES caixas(id),
                data_venda      TEXT NOT NULL DEFAULT (CURRENT_DATE AT TIME ZONE 'America/Sao_Paulo')::date::text,
                total           NUMERIC(10,2) NOT NULL DEFAULT 0,
                forma_pagamento TEXT NOT NULL DEFAULT 'especie',
                troco           NUMERIC(10,2) NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'concluida',
                atendente_id    INTEGER REFERENCES atendentes(id),
                observacao      TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS vendas_pdv_itens (
                id              SERIAL PRIMARY KEY,
                venda_id        INTEGER NOT NULL REFERENCES vendas_pdv(id) ON DELETE CASCADE,
                produto_id      INTEGER NOT NULL REFERENCES produtos(id),
                nome_produto    TEXT NOT NULL,
                quantidade      INTEGER NOT NULL DEFAULT 1,
                preco_unitario  NUMERIC(10,2) NOT NULL,
                subtotal        NUMERIC(10,2) NOT NULL
            )
        """)

        # ── Movimentações de crédito fiado ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fiado_credito_mov (
                id              SERIAL PRIMARY KEY,
                trabalhador_id  INTEGER NOT NULL REFERENCES trabalhadores(id),
                tipo            TEXT NOT NULL CHECK (tipo IN ('credito','debito')),
                valor           NUMERIC(10,2) NOT NULL,
                descricao       TEXT,
                created_at      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp::text
            )
        """)

        # ── Movimentos de caixa (abertura/fechamento) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS caixa_movimentos (
                id                      SERIAL PRIMARY KEY,
                caixa_id                INTEGER NOT NULL REFERENCES caixas(id),
                data_abertura           TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp::text,
                data_fechamento         TEXT,
                saldo_inicial           NUMERIC(10,2) NOT NULL DEFAULT 0,
                saldo_final             NUMERIC(10,2),
                total_vendas            NUMERIC(10,2) DEFAULT 0,
                total_sangrias          NUMERIC(10,2) DEFAULT 0,
                status                  TEXT NOT NULL DEFAULT 'aberto',
                atendente_id_abertura   INTEGER REFERENCES atendentes(id),
                atendente_id_fechamento INTEGER REFERENCES atendentes(id),
                observacao_fechamento   TEXT
            )
        """)

        # ── Sangrias (retirada de dinheiro do caixa) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS caixa_sangrias (
                id                  SERIAL PRIMARY KEY,
                caixa_movimento_id  INTEGER NOT NULL REFERENCES caixa_movimentos(id),
                valor               NUMERIC(10,2) NOT NULL,
                motivo              TEXT NOT NULL DEFAULT '',
                data_hora           TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp::text,
                atendente_id        INTEGER REFERENCES atendentes(id),
                recebedor_nome      TEXT NOT NULL DEFAULT ''
            )
        """)

        # ── Movimentações de estoque ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS estoque_movimentos (
                id              SERIAL PRIMARY KEY,
                produto_id      INTEGER NOT NULL REFERENCES produtos(id),
                tipo            TEXT NOT NULL CHECK (tipo IN ('entrada','saida','ajuste')),
                quantidade      INTEGER NOT NULL,
                saldo_anterior  INTEGER NOT NULL DEFAULT 0,
                saldo_posterior INTEGER NOT NULL DEFAULT 0,
                motivo          TEXT,
                referencia_id   INTEGER,
                atendente_id    INTEGER REFERENCES atendentes(id),
                created_at      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp::text
            )
        """)

        # ── Cancelamento de venda (TASK_007 / FIN-02) ──
        # Uma linha por cancelamento EFETIVO: UNIQUE(venda_id) garante que
        # repetição/clique duplo/concorrência nunca produz segunda reversão.
        # FK sem CASCADE: apagar a venda não apaga a prova do cancelamento.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vendas_pdv_cancelamento (
                id                      SERIAL PRIMARY KEY,
                venda_id                INTEGER NOT NULL UNIQUE
                                        REFERENCES vendas_pdv(id),
                atendente_id            INTEGER NOT NULL REFERENCES atendentes(id),
                motivo                  TEXT NOT NULL CHECK (motivo <> ''),
                criado_em               TEXT NOT NULL,
                sessao_movimento_id     INTEGER REFERENCES caixa_movimentos(id),
                valor_sessao_revertido  NUMERIC(12,2) NOT NULL DEFAULT 0,
                total_venda             NUMERIC(12,2) NOT NULL,
                doacao_venda            NUMERIC(12,2) NOT NULL DEFAULT 0,
                forma_pagamento         TEXT NOT NULL,
                estoque_revertido       TEXT NOT NULL DEFAULT '[]'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS financeiro_movimentacoes (
                id                  SERIAL PRIMARY KEY,
                tipo                TEXT NOT NULL,  -- 'entrada' ou 'saida'
                categoria           TEXT NOT NULL,  -- 'mensalidade', 'doacao', 'livro', etc
                valor               NUMERIC(10,2)   NOT NULL DEFAULT 0,
                data_movimentacao   TEXT            NOT NULL DEFAULT (CURRENT_DATE AT TIME ZONE 'America/Sao_Paulo')::date::text,
                descricao           TEXT,
                trabalhador_id      INTEGER REFERENCES trabalhadores(id),
                pessoa_id           INTEGER REFERENCES pessoas(id),
                pix_copiadecola     TEXT,
                nota_fiscal_foto    TEXT,
                status              TEXT            NOT NULL DEFAULT 'pago'  -- 'pago', 'pendente', 'cancelado'
            )
        """)

        # ── Doações de cestas ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS doacoes_cestas (
                id                  SERIAL PRIMARY KEY,
                pessoa_id           INTEGER NOT NULL REFERENCES pessoas(id),
                data_entrega        TEXT            NOT NULL,
                itens               TEXT,
                observacao          TEXT,
                entregue            INTEGER         NOT NULL DEFAULT 0
            )
        """)

        # ── Tipos de doação (produtos doados) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tipos_doacao (
                id          SERIAL PRIMARY KEY,
                nome        TEXT NOT NULL UNIQUE,
                descricao   TEXT,
                ativo       INTEGER NOT NULL DEFAULT 1
            )
        """)

        # Seed padrão (ignora se já existir)
        for nome_tipo in ("Cesta Básica", "Roupas", "Calçados", "Brinquedos", "Higiene Pessoal", "Outros"):
            try:
                conn.execute(
                    "INSERT INTO tipos_doacao (nome) VALUES (%s) ON CONFLICT (nome) DO NOTHING",
                    (nome_tipo,)
                )
            except Exception:
                pass

        # ── Itens de cada doação (M:N doações × tipos) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS doacao_itens (
                id              SERIAL PRIMARY KEY,
                doacao_id       INTEGER NOT NULL REFERENCES doacoes_cestas(id) ON DELETE CASCADE,
                tipo_doacao_id  INTEGER NOT NULL REFERENCES tipos_doacao(id),
                quantidade      INTEGER NOT NULL DEFAULT 1,
                UNIQUE (doacao_id, tipo_doacao_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios_grupos (
                id          SERIAL PRIMARY KEY,
                usuario_id  INTEGER NOT NULL REFERENCES atendentes(id) ON DELETE CASCADE,
                grupo_id    INTEGER NOT NULL REFERENCES grupos(id) ON DELETE CASCADE,
                UNIQUE(usuario_id, grupo_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS dias_trabalho (
                id          SERIAL PRIMARY KEY,
                data        TEXT NOT NULL UNIQUE,
                aberto      INTEGER NOT NULL DEFAULT 1,
                reiki_ativo INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS mediuns_dia (
                id              SERIAL PRIMARY KEY,
                dia_trabalho_id INTEGER NOT NULL REFERENCES dias_trabalho(id),
                medium_id       INTEGER NOT NULL REFERENCES mediuns(id),
                vagas_dia       INTEGER,
                UNIQUE(dia_trabalho_id, medium_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS planos_tratamento (
                id                  SERIAL PRIMARY KEY,
                medium_id           INTEGER REFERENCES mediuns(id),
                sessoes_total       INTEGER NOT NULL,
                sessoes_realizadas  INTEGER NOT NULL DEFAULT 0,
                data_inicio         TEXT NOT NULL,
                concluido           INTEGER NOT NULL DEFAULT 0,
                frequencia          TEXT NOT NULL DEFAULT 'semanal',
                status              TEXT NOT NULL DEFAULT 'ativo',
                sessoes_com_passe   INTEGER NOT NULL DEFAULT 3
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS agendamentos (
                id              SERIAL PRIMARY KEY,
                plano_id        INTEGER NOT NULL REFERENCES planos_tratamento(id),
                data            TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'agendado',
                requer_passe    INTEGER NOT NULL DEFAULT 1,
                encaixe         INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS plano_pessoas (
                id          SERIAL PRIMARY KEY,
                plano_id    INTEGER NOT NULL REFERENCES planos_tratamento(id),
                pessoa_id   INTEGER NOT NULL REFERENCES pessoas(id),
                UNIQUE(plano_id, pessoa_id)
            )
        """)

        # ── Idempotência da criação de plano ──
        # Evita que reenvio do formulário (duplo clique, retry HTTP, F5) crie
        # planos e agendamentos duplicados. A chave representa a OPERAÇÃO de
        # criação (ex.: acolhimento:{checkin}:{médium}:{data}), nunca a pessoa.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plano_chave_idempotencia (
                chave       TEXT PRIMARY KEY,
                plano_id    INTEGER REFERENCES planos_tratamento(id) ON DELETE CASCADE,
                criado_em   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkins (
                id                      SERIAL PRIMARY KEY,
                dia_trabalho_id         INTEGER NOT NULL REFERENCES dias_trabalho(id),
                pessoa_id               INTEGER NOT NULL REFERENCES pessoas(id),
                hora_checkin            TEXT NOT NULL,
                codigo_passe            TEXT,
                codigo_atendimento      TEXT,
                plano_id                INTEGER REFERENCES planos_tratamento(id),
                medium_id               INTEGER REFERENCES mediuns(id),
                passe_realizado         INTEGER NOT NULL DEFAULT 0,
                atendimento_realizado   INTEGER NOT NULL DEFAULT 0,
                codigo_acolhimento      TEXT,
                acolhimento_realizado   INTEGER NOT NULL DEFAULT 0,
                agendamento_id          INTEGER REFERENCES agendamentos(id),
                codigo_reiki            TEXT,
                reiki_realizado         INTEGER NOT NULL DEFAULT 0,
                acolhimento_chamado     INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS dias_atendimento (
                dia_semana  INTEGER PRIMARY KEY,
                descricao   TEXT NOT NULL
            )
        """)

        # ── Configuração de Backup em Pendrive ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS configuracoes_backup_pendrive (
                id                  SERIAL PRIMARY KEY,
                tipo_backup         VARCHAR(20),
                dispositivo         VARCHAR(100),
                ponto_montagem      VARCHAR(255),
                ativo               INTEGER DEFAULT 0,
                horario_backup      TIME,
                criado_em           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                atualizado_em       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Histórico de backups em pendrive ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_pendrive_historico (
                id                  SERIAL PRIMARY KEY,
                data_backup         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status              VARCHAR(20),
                caminho_backup      VARCHAR(255),
                tamanho_backup      BIGINT,
                espaco_disponivel   BIGINT,
                mensagem_erro       TEXT
            )
        """)

        # ── Índices para consultas frequentes ──
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checkins_dia ON checkins(dia_trabalho_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checkins_pessoa ON checkins(pessoa_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checkins_medium ON checkins(medium_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checkins_passe_realizado ON checkins(passe_realizado)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agendamentos_plano ON agendamentos(plano_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agendamentos_data ON agendamentos(data)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agendamentos_status ON agendamentos(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_planos_medium ON planos_tratamento(medium_id)")

        # ── Índices adicionais para performance ──
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_grupos_usuario ON usuarios_grupos(usuario_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_grupos_grupo ON usuarios_grupos(grupo_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_doacoes_cestas_pessoa ON doacoes_cestas(pessoa_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_doacao_itens_doacao ON doacao_itens(doacao_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_doacao_itens_tipo ON doacao_itens(tipo_doacao_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tipos_doacao_ativo ON tipos_doacao(ativo)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_caixa_movimentos_caixa_status ON caixa_movimentos(caixa_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_caixa_sangrias_movimento ON caixa_sangrias(caixa_movimento_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS trabalhador_dias (
                id              SERIAL PRIMARY KEY,
                trabalhador_id  INTEGER NOT NULL REFERENCES trabalhadores(id),
                dia_semana      INTEGER NOT NULL,
                UNIQUE(trabalhador_id, dia_semana)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS trabalhador_presenca (
                id                  SERIAL PRIMARY KEY,
                trabalhador_id      INTEGER NOT NULL REFERENCES trabalhadores(id),
                data                DATE NOT NULL,
                dia_trabalho_id     INTEGER REFERENCES dias_trabalho(id),
                presente            INTEGER NOT NULL DEFAULT 0,
                hora_chegada        TEXT,
                hora_saida          TEXT,
                UNIQUE(trabalhador_id, data)
            )
        """)

        # ── Auditoria de correções de presença (TASK_004) ──
        # Trilha append-only de quem corrigiu/lançou retroativo/anulou. Não é uma
        # plataforma geral de auditoria: é a tabela própria deste fato, no mesmo
        # padrão de estoque_movimentos (anterior/posterior + motivo + atendente +
        # instante do servidor em SP). Auto-suficiente por linha: guarda o fato
        # inteiro (anterior e novo), inclusive quando a linha operacional some
        # (anulação). Por isso referencia trabalhador_id + data_fato, nunca o id
        # descartável de trabalhador_presenca, e a FK da pessoa é RESTRICT — a
        # auditoria não desaparece junto com nada. Sem UPDATE/DELETE pela interface.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trabalhador_presenca_correcao (
                id                       SERIAL PRIMARY KEY,
                trabalhador_id           INTEGER NOT NULL REFERENCES trabalhadores(id) ON DELETE RESTRICT,
                data_fato                DATE NOT NULL,
                operacao                 TEXT NOT NULL CHECK (operacao IN
                    ('lancamento_retroativo','correcao_horario','conversao_ausencia',
                     'conversao_presenca','anulacao')),
                anterior_presente        INTEGER,
                anterior_hora_chegada    TEXT,
                anterior_hora_saida      TEXT,
                anterior_origem          TEXT,
                anterior_dia_trabalho_id INTEGER,
                novo_presente            INTEGER,
                novo_hora_chegada        TEXT,
                novo_hora_saida          TEXT,
                novo_origem              TEXT,
                novo_dia_trabalho_id     INTEGER,
                motivo                   TEXT NOT NULL CHECK (motivo <> ''),
                atendente_id             INTEGER REFERENCES atendentes(id),
                criado_em                TEXT NOT NULL DEFAULT
                    (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::timestamp::text
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS ix_presenca_correcao_trab_data
                ON trabalhador_presenca_correcao (trabalhador_id, data_fato)
        """)

        # ── Biblioteca ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS livros (
                id              SERIAL PRIMARY KEY,
                isbn            TEXT UNIQUE,
                titulo          TEXT NOT NULL,
                autor           TEXT,
                editora         TEXT,
                ano             INTEGER,
                edicao          TEXT,
                quantidade      INTEGER NOT NULL DEFAULT 1,
                preco_venda     NUMERIC(10,2) DEFAULT 0,
                observacao      TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS emprestimos (
                id                  SERIAL PRIMARY KEY,
                livro_id            INTEGER NOT NULL REFERENCES livros(id),
                pessoa_id           INTEGER NOT NULL REFERENCES pessoas(id),
                data_emprestimo     TEXT NOT NULL DEFAULT (CURRENT_DATE AT TIME ZONE 'America/Sao_Paulo')::date::text,
                data_devolucao      TEXT,
                observacao          TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS vendas_livros (
                id                  SERIAL PRIMARY KEY,
                livro_id            INTEGER NOT NULL REFERENCES livros(id),
                pessoa_id           INTEGER REFERENCES pessoas(id),
                quantidade          INTEGER NOT NULL DEFAULT 1,
                valor_total         NUMERIC(10,2) NOT NULL DEFAULT 0,
                data_venda          TEXT NOT NULL DEFAULT (CURRENT_DATE AT TIME ZONE 'America/Sao_Paulo')::date::text,
                observacao          TEXT
            )
        """)

        # Seed: contas financeiras padrão (antes de _migrar para UPDATE histórico funcionar)
        for nome_ct, tipo_ct in [
            ("Conta Antiga", "legado"),
            ("Conta Espécie", "especie"),
            ("Conta PIX", "pix"),
            ("Valores a Receber", "areceber"),
        ]:
            conn.execute(
                "INSERT INTO contas_financeiras (nome, tipo) VALUES (%s, %s) ON CONFLICT (nome) DO NOTHING",
                (nome_ct, tipo_ct)
            )

        _migrar(conn)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_vendas_caixa_movimento ON vendas_pdv(caixa_movimento_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_financeiro_conta ON financeiro_movimentacoes(conta_id)")

        # Seed: caixas padrão
        for nome_cx, desc_cx in [
            ("Geral",       "Movimentações gerais do centro"),
            ("Lanchonete",  "Caixa da lanchonete"),
            ("Biblioteca",  "Caixa da biblioteca"),
            ("Bazar",       "Caixa do bazar de roupas e afins"),
        ]:
            conn.execute(
                "INSERT INTO caixas (nome, descricao) VALUES (%s, %s) ON CONFLICT (nome) DO NOTHING",
                (nome_cx, desc_cx)
            )

        # Caixas sem catálogo de produtos
        conn.execute("UPDATE caixas SET usa_produtos = FALSE WHERE nome IN ('Geral', 'Bazar')")

        # Todos os produtos existentes sem caixa_id pertencem à Lanchonete
        conn.execute("""
            UPDATE produtos SET caixa_id = (SELECT id FROM caixas WHERE nome = 'Lanchonete')
            WHERE caixa_id IS NULL
        """)

        # Seed: segunda e quarta se ainda não há registros
        existe = conn.execute("SELECT COUNT(*) AS c FROM dias_atendimento").fetchone()["c"]
        if existe == 0:
            conn.execute(
                """INSERT INTO dias_atendimento (dia_semana, descricao)
                   VALUES (0, 'Segunda-feira')
                   ON CONFLICT (dia_semana) DO NOTHING"""
            )
            conn.execute(
                """INSERT INTO dias_atendimento (dia_semana, descricao)
                   VALUES (2, 'Quarta-feira')
                   ON CONFLICT (dia_semana) DO NOTHING"""
            )


def conta_id_por_tipo(conn, tipo: str) -> int | None:
    """Retorna o id da conta financeira pelo tipo (legado, especie, pix, areceber)."""
    row = conn.execute(
        "SELECT id FROM contas_financeiras WHERE tipo = %s AND ativo = 1",
        (tipo,)
    ).fetchone()
    return row["id"] if row else None


if __name__ == "__main__":
    criar_tabelas()
    print("Banco de dados criado com sucesso.")
