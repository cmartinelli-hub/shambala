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


def gerar_agendamentos_plano(conn, plano_id: int, inicio: date,
                              frequencia: str, total: int, sessoes_com_passe: int):
    """Gera agendamentos automáticos para um plano. avulso = sem geração."""
    delta_map = {"semanal": 7, "quinzenal": 14, "mensal": 28}
    delta = delta_map.get(frequencia, 0)
    if delta == 0:
        return
    dias = _dias_atendimento(conn)
    atual = _proxima_data_trabalho(inicio, dias)
    for i in range(total):
        requer_passe = 1 if (sessoes_com_passe < 0 or i < sessoes_com_passe) else 0
        conn.execute(
            """INSERT INTO agendamentos (plano_id, data, status, requer_passe, encaixe)
               VALUES (%s, %s, 'agendado', %s, 0)""",
            (plano_id, atual.isoformat(), requer_passe),
        )
        atual = _proxima_data_trabalho(atual + timedelta(days=delta), dias)


# ── Migrações incrementais ───────────────────────────────────────────────────

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

    # Adiciona UNIQUE em trabalhador_presenca se não existir
    existe_constraint = conn.execute(
        """SELECT COUNT(*) AS c FROM information_schema.table_constraints
           WHERE table_name = 'trabalhador_presenca'
             AND constraint_type = 'UNIQUE'"""
    ).fetchone()["c"]
    if existe_constraint == 0:
        conn.execute(
            "ALTER TABLE trabalhador_presenca "
            "ADD CONSTRAINT trabalhador_presenca_unique UNIQUE (trabalhador_id, dia_trabalho_id)"
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

        # ── Trabalhadores ──
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
                id      SERIAL PRIMARY KEY,
                data    TEXT NOT NULL UNIQUE,
                aberto  INTEGER NOT NULL DEFAULT 1
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
                medium_id           INTEGER NOT NULL REFERENCES mediuns(id),
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
                dia_trabalho_id     INTEGER NOT NULL REFERENCES dias_trabalho(id),
                presente            INTEGER NOT NULL DEFAULT 0,
                hora_chegada        TEXT,
                hora_saida          TEXT,
                UNIQUE(trabalhador_id, dia_trabalho_id)
            )
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

        _migrar(conn)

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


if __name__ == "__main__":
    criar_tabelas()
    print("Banco de dados criado com sucesso.")
