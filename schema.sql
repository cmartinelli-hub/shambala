-- SHAMBALA v2.1 - Schema PostgreSQL
-- Execute com: psql -U shambala -d shambala < schema.sql

CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION norm(texto TEXT)
RETURNS TEXT AS $$
    SELECT lower(unaccent(texto))
$$ LANGUAGE SQL IMMUTABLE;

CREATE TABLE IF NOT EXISTS grupos (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE,
    descricao TEXT
);

CREATE TABLE IF NOT EXISTS atendentes (
    id SERIAL PRIMARY KEY,
    nome_usuario TEXT NOT NULL UNIQUE,
    nome_completo TEXT NOT NULL,
    senha_hash TEXT NOT NULL,
    telefone TEXT,
    email TEXT,
    ativo INTEGER NOT NULL DEFAULT 1,
    grupo_id INTEGER REFERENCES grupos(id)
);

CREATE TABLE IF NOT EXISTS usuarios_grupos (
    usuario_id INTEGER NOT NULL REFERENCES atendentes(id) ON DELETE CASCADE,
    grupo_id INTEGER NOT NULL REFERENCES grupos(id) ON DELETE CASCADE,
    PRIMARY KEY (usuario_id, grupo_id)
);

CREATE TABLE IF NOT EXISTS pessoas (
    id SERIAL PRIMARY KEY,
    nome_apresentacao TEXT NOT NULL,
    nome_completo TEXT,
    telefone TEXT,
    email TEXT,
    cep TEXT,
    logradouro TEXT,
    numero TEXT,
    complemento TEXT,
    bairro TEXT,
    cidade TEXT,
    uf TEXT,
    cpf TEXT UNIQUE,
    data_nascimento TEXT,
    deficiencia INTEGER DEFAULT 0,
    prioridade INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tipos_doacao (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE,
    descricao TEXT,
    ativo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS doacoes_cestas (
    id SERIAL PRIMARY KEY,
    pessoa_id INTEGER NOT NULL REFERENCES pessoas(id),
    data_entrega TEXT,
    itens TEXT,
    observacao TEXT,
    entregue INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS doacao_itens (
    id SERIAL PRIMARY KEY,
    doacao_id INTEGER NOT NULL REFERENCES doacoes_cestas(id) ON DELETE CASCADE,
    tipo_doacao_id INTEGER NOT NULL REFERENCES tipos_doacao(id),
    quantidade INTEGER NOT NULL DEFAULT 1,
    UNIQUE (doacao_id, tipo_doacao_id)
);

CREATE TABLE IF NOT EXISTS grupos_permissoes (
    id SERIAL PRIMARY KEY,
    grupo_id INTEGER NOT NULL REFERENCES grupos(id) ON DELETE CASCADE,
    modulo TEXT NOT NULL,
    ler BOOLEAN DEFAULT false,
    escrever BOOLEAN DEFAULT false,
    excluir BOOLEAN DEFAULT false,
    UNIQUE(grupo_id, modulo)
);

-- SEED DE DADOS
INSERT INTO grupos (id, nome, descricao) VALUES (1, 'Admin', 'Administrador') ON CONFLICT DO NOTHING;

-- Usuário padrão: admin / senha: admin (hash SHA256)
-- Para mudar: python3 -c "import hashlib; print(hashlib.sha256('novasenha'.encode()).hexdigest())"
INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash, ativo)
VALUES ('admin', 'Administrador', '8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918', 1)
ON CONFLICT (nome_usuario) DO NOTHING;

INSERT INTO usuarios_grupos (usuario_id, grupo_id) VALUES (1, 1) ON CONFLICT DO NOTHING;

-- Tipos de doação padrão
INSERT INTO tipos_doacao (nome, ativo) VALUES
    ('Cesta Básica', 1),
    ('Roupas', 1),
    ('Calçados', 1),
    ('Brinquedos', 1),
    ('Higiene Pessoal', 1),
    ('Outros', 1)
ON CONFLICT (nome) DO NOTHING;

-- Permissões padrão Admin
INSERT INTO grupos_permissoes (grupo_id, modulo, ler, escrever, excluir)
SELECT 1, modulo, true, true, true FROM (
    SELECT 'dia.painel' as modulo UNION
    SELECT 'dia.checkin' UNION
    SELECT 'dia.passe' UNION
    SELECT 'dia.reiki' UNION
    SELECT 'dia.acolhimento' UNION
    SELECT 'dia.atendimento' UNION
    SELECT 'agenda' UNION
    SELECT 'chamada' UNION
    SELECT 'relatorios' UNION
    SELECT 'cadastros.pessoas' UNION
    SELECT 'cadastros.mediuns' UNION
    SELECT 'cadastros.usuarios' UNION
    SELECT 'cadastros.trabalhadores' UNION
    SELECT 'cadastros.permissoes' UNION
    SELECT 'configuracoes' UNION
    SELECT 'financeiro' UNION
    SELECT 'biblioteca' UNION
    SELECT 'doacoes' UNION
    SELECT 'mala.direta'
) t
ON CONFLICT (grupo_id, modulo) DO NOTHING;

-- Criar índices
CREATE INDEX IF NOT EXISTS idx_usuarios_grupos_usuario ON usuarios_grupos(usuario_id);
CREATE INDEX IF NOT EXISTS idx_usuarios_grupos_grupo ON usuarios_grupos(grupo_id);
CREATE INDEX IF NOT EXISTS idx_doacoes_cestas_pessoa ON doacoes_cestas(pessoa_id);
CREATE INDEX IF NOT EXISTS idx_doacao_itens_doacao ON doacao_itens(doacao_id);
CREATE INDEX IF NOT EXISTS idx_doacao_itens_tipo ON doacao_itens(tipo_doacao_id);
CREATE INDEX IF NOT EXISTS idx_tipos_doacao_ativo ON tipos_doacao(ativo);

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO shambala;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO shambala;
