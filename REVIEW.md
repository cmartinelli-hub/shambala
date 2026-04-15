# Revisão de Código — Casa Espírita Shambala

**Data:** 2026-04-14  
**Revisor:** Claude (gsd-code-reviewer)  
**Profundidade:** standard (leitura completa de cada arquivo)  
**Arquivos revisados:** 22

---

## Resumo Executivo

| Severidade | Quantidade |
|------------|-----------|
| CRITICAL   | 3         |
| HIGH       | 6         |
| MEDIUM     | 7         |
| LOW        | 5         |
| INFO       | 4         |
| **Total**  | **25**    |

O sistema apresenta **três problemas críticos** que precisam de correção imediata:
1. Hash de senha com SHA-256 simples (sem salt), vulnerável a ataques de rainbow table e dicionário.
2. SQL injection na função `pode_acessar()` via interpolação de string com o parâmetro `acao`.
3. Sessões de autenticação armazenadas apenas em memória — perdidas a cada reinício, sem expiração e sem proteção contra crescimento ilimitado.

Os problemas HIGH incluem ausência de CSRF em todos os formulários POST, XSS em endpoints SVG placeholder, race condition no check-in de trabalhadores, e path traversal no backup de restauração.

---

## CRITICAL

### CR-01: SQL Injection em `pode_acessar()` — interpolação do parâmetro `acao`

**Arquivo:** `rotas/permissoes.py:84`

**Problema:**  
O parâmetro `acao` (valores possíveis: `"ler"`, `"escrever"`, `"apagar"`) é inserido diretamente na string SQL via `%` de string Python antes de o driver executar a query. Um chamador que passe um valor manipulado provoca injeção real de SQL.

```python
# PROBLEMÁTICO — acao é interpolado diretamente na string antes do driver
row = conn.execute(
    "SELECT %s FROM grupos_permissoes WHERE grupo_id = %%s AND modulo = %%s" % acao,
    (grupo_id, modulo)
).fetchone()
```

Embora internamente o código hoje só passe literais fixos (`"ler"`, `"escrever"`, `"apagar"`), a construção é estruturalmente insegura: qualquer refatoração futura ou chamada externa com `acao` controlado pelo usuário resulta em injeção.

**Correção:**

```python
def pode_acessar(grupo_id: int, modulo: str, acao: str = "ler") -> bool:
    ACOES_VALIDAS = {"ler", "escrever", "apagar"}
    if acao not in ACOES_VALIDAS:
        return False
    if grupo_id is None:
        return False
    # Mapear nome da coluna de forma segura (nunca interpolar parâmetro do usuário)
    col_map = {"ler": "ler", "escrever": "escrever", "apagar": "apagar"}
    col = col_map[acao]
    with conectar() as conn:
        row = conn.execute(
            f"SELECT {col} FROM grupos_permissoes WHERE grupo_id = %s AND modulo = %s",
            (grupo_id, modulo)
        ).fetchone()
        if not row:
            return False
        return bool(row[col])
```

Neste caso a whitelist garante que apenas nomes de coluna conhecidos cheguem ao SQL. O ideal é usar uma das três queries fixas ou `CASE` no SQL para eliminar interpolação completamente.

---

### CR-02: Senhas armazenadas com SHA-256 simples (sem salt)

**Arquivo:** `rotas/auth.py:16-17` e `rotas/usuarios.py:24-25`

**Problema:**  
As senhas são hasheadas com `hashlib.sha256` sem salt, tornando-as vulneráveis a ataques de rainbow table, dicionário e lookup instantâneo em bancos de hashes pré-computados.

```python
# PROBLEMÁTICO
def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()
```

A senha padrão `"admin"` (linha 139 de `auth.py`) produz hash idêntico ao de todos os outros sistemas que usam a mesma abordagem.

**Correção:**

```python
import bcrypt

def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()

def verificar_senha(senha: str, hash_armazenado: str) -> bool:
    return bcrypt.checkpw(senha.encode(), hash_armazenado.encode())
```

Ajustar o login em `auth.py:64` para usar `verificar_senha()` em vez de comparar hashes diretamente na query SQL. Isso também elimina o timing side-channel de comparar strings de hash no banco.

Instalar dependência: `pip install bcrypt` e adicionar ao `requirements.txt`.

---

### CR-03: Sessões em memória sem expiração e sem persistência

**Arquivo:** `rotas/auth.py:13` e funções `criar_sessao` / `obter_atendente_logado`

**Problema:**  
O dicionário `_sessoes: dict[str, int] = {}` acumula tokens indefinidamente sem expiração. Além disso:
- Qualquer reinício da aplicação invalida todas as sessões dos usuários (UX ruim, não crítico em si).
- Um ataque de sessão fixation ou enumeração pode encher o dicionário sem limite (DoS em memória).
- Não há mecanismo de logout por timeout (sessão válida para sempre até logout manual).

```python
_sessoes: dict[str, int] = {}  # cresce sem limite, sem TTL
```

**Correção mínima viável (sem alterar arquitetura):**

```python
import time
from collections import OrderedDict

_sessoes: dict[str, tuple[int, float]] = {}  # token → (user_id, timestamp)
_SESSION_TTL = 8 * 3600  # 8 horas
_MAX_SESSOES = 1000

def criar_sessao(atendente_id: int) -> str:
    token = secrets.token_hex(32)
    agora = time.time()
    # Limpar sessões expiradas
    expiradas = [t for t, (_, ts) in _sessoes.items() if agora - ts > _SESSION_TTL]
    for t in expiradas:
        del _sessoes[t]
    if len(_sessoes) >= _MAX_SESSOES:
        # Remove a mais antiga
        mais_antiga = min(_sessoes, key=lambda t: _sessoes[t][1])
        del _sessoes[mais_antiga]
    _sessoes[token] = (atendente_id, agora)
    return token

def obter_atendente_logado(request: Request):
    token = request.cookies.get("sessao")
    if not token or token not in _sessoes:
        return None
    user_id, ts = _sessoes[token]
    if time.time() - ts > _SESSION_TTL:
        del _sessoes[token]
        return None
    # ... resto igual
```

---

## HIGH

### HI-01: Ausência total de proteção CSRF

**Arquivos:** Todos os routers (`auth.py`, `dia.py`, `checkin.py`, `financeiro.py`, `configuracoes.py`, `usuarios.py`, etc.)

**Problema:**  
Nenhum formulário POST possui token CSRF. Um atacante pode forjar requisições em nome de um usuário autenticado (o cookie `sessao` é enviado automaticamente pelo navegador). Operações sensíveis afetadas incluem: criar/editar usuários, encerrar o dia de atendimento, marcar pagamentos como "pago", alterar configurações SMTP e de backup.

A proteção `SameSite=lax` no cookie (linha 77 de `auth.py`) mitiga ataques CSRF a partir de navegação cross-site de nível superior, mas não protege contra formulários em subdomínios ou iframes.

**Correção mínima:**

```python
# Em auth.py — gerar e validar token CSRF por sessão
def gerar_csrf_token(token_sessao: str) -> str:
    import hmac, hashlib
    segredo = os.environ.get("SHAMBALA_SECRET_KEY", "mudar-em-producao")
    return hmac.new(segredo.encode(), token_sessao.encode(), hashlib.sha256).hexdigest()

def validar_csrf(request: Request, csrf_form: str) -> bool:
    token_sessao = request.cookies.get("sessao", "")
    return hmac.compare_digest(gerar_csrf_token(token_sessao), csrf_form)
```

Incluir `{{ csrf_token }}` como campo oculto em todos os formulários e validar no início de cada handler POST.

Alternativamente, usar a biblioteca `starlette-csrf` ou `fastapi-csrf-protect`.

---

### HI-02: XSS via parâmetro de URL no SVG placeholder

**Arquivos:** `rotas/pessoas.py:47-62`, `rotas/trabalhadores.py:41-56`, `rotas/mediuns.py:41-56`

**Problema:**  
O parâmetro `inicial` (capturado da URL) é inserido diretamente no SVG sem escape. SVG suporta JavaScript nativo; um valor como `inicial=<script>alert(1)</script>` ou `inicial=</text><script>alert(1)</script>` é injetado no documento.

```python
# PROBLEMÁTICO — inicial vem diretamente da URL sem escape
svg = f'''...
    <text ...>{inicial.upper()}</text>
</svg>'''
return Response(content=svg, media_type="image/svg+xml")
```

Embora o navegador possa sandbox SVGs dependendo do contexto, SVG servido como `image/svg+xml` direto executa scripts se acessado diretamente por URL.

**Correção:**

```python
import html as _html

@router.get("/foto-placeholder/{inicial}")
async def foto_placeholder(inicial: str):
    # Manter apenas um único caractere alfanumérico
    char = inicial[0] if inicial else "?"
    char_safe = _html.escape(char.upper())
    svg = f'''...
        <text ...>{char_safe}</text>
    </svg>'''
    # Adicionar CSP para SVG inline
    headers = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"}
    return Response(content=svg, media_type="image/svg+xml", headers=headers)
```

---

### HI-03: Race condition no check-in de trabalhadores (router antigo)

**Arquivo:** `rotas/trabalhadores.py:431-452` e `rotas/trabalhadores.py:455-475`

**Problema:**  
Os handlers `marcar_presente` e `marcar_ausente` do router interno (`/cadastros/trabalhadores/dia/...`) executam INSERT sem `ON CONFLICT`, enquanto a constraint `UNIQUE(trabalhador_id, dia_trabalho_id)` existe. Uma dupla submissão simultânea de formulário ou clique duplo resulta em erro 500 não tratado.

```python
# PROBLEMÁTICO — sem ON CONFLICT, viola UNIQUE constraint
conn.execute(
    """INSERT INTO trabalhador_presenca
       (trabalhador_id, dia_trabalho_id, presente, hora_chegada)
       VALUES (%s, %s, 1, %s)""",
    (id, dia["id"], agora),
)
```

Notar que o router novo (`/checkin-trabalhador`) já usa `ON CONFLICT DO UPDATE` corretamente (linha 548). O router antigo não usa.

**Correção:**

```python
conn.execute(
    """INSERT INTO trabalhador_presenca
       (trabalhador_id, dia_trabalho_id, presente, hora_chegada)
       VALUES (%s, %s, 1, %s)
       ON CONFLICT (trabalhador_id, dia_trabalho_id)
       DO UPDATE SET presente = 1, hora_chegada = EXCLUDED.hora_chegada""",
    (id, dia["id"], agora),
)
```

---

### HI-04: Path traversal no backup de restauração

**Arquivo:** `backup.py:285-335`

**Problema:**  
A função `restaurar_backup` recebe um caminho de arquivo (`caminho_sql_gz`) e deriva o caminho do arquivo temporário descomprimido com substituição simples de string:

```python
tmp_sql = caminho_sql_gz.replace(".gz", "")
```

Se `caminho_sql_gz` não terminar em `.gz`, o arquivo é gravado no mesmo caminho, sem a extensão removida, podendo sobrescrever arquivos arbitrários. Além disso, se chamada via API com caminho arbitrário, um atacante poderia apontar para `/etc/passwd.gz` ou similar.

**Correção:**

```python
import tempfile, os

def restaurar_backup(caminho_sql_gz: str) -> list[str]:
    # Validar que o caminho está dentro do diretório de backups esperado
    BACKUP_DIRS = [PASTA_LOCAL, "/media"]
    caminho_abs = os.path.realpath(caminho_sql_gz)
    if not any(caminho_abs.startswith(os.path.realpath(d)) for d in BACKUP_DIRS):
        return [f"Caminho inválido: {caminho_sql_gz}"]

    if not caminho_sql_gz.endswith(".sql.gz"):
        return [f"Formato inválido: esperado .sql.gz"]

    # Usar arquivo temporário em vez de derivar nome
    with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as tmp:
        tmp_sql = tmp.name
    # ... resto da função
```

---

### HI-05: Cookie de sessão sem flag `Secure`

**Arquivo:** `rotas/auth.py:77`

**Problema:**  
O cookie de sessão é criado sem a flag `secure=True`. Em redes locais sem HTTPS (o sistema roda em Raspberry Pi na LAN), isso pode ser aceitável temporariamente, mas qualquer tráfego HTTP permite que o cookie seja capturado por eavesdropping.

```python
resp.set_cookie("sessao", token, httponly=True, samesite="lax")
# Falta: secure=True
```

**Correção:**  
Adicionar `secure=True` se HTTPS estiver disponível. Para facilitar a transição:

```python
usar_https = os.environ.get("SHAMBALA_HTTPS", "false").lower() == "true"
resp.set_cookie("sessao", token, httponly=True, samesite="lax", secure=usar_https)
```

---

### HI-06: Erro não tratado expõe detalhes internos ao usuário

**Arquivo:** `rotas/usuarios.py:120` e `rotas/usuarios.py:192`

**Problema:**  
Exceções de banco de dados são convertidas diretamente em mensagens de erro exibidas ao usuário:

```python
except Exception as e:
    # ...
    "erro": f"Nome de usuário já existe ou erro: {str(e)}",  # linha 120
    "erro": f"Erro ao salvar: {str(e)}",  # linha 192
```

`str(e)` de uma exceção psycopg2 pode expor nomes de colunas, constraints, queries internas e stack traces parciais — informação valiosa para reconhecimento por atacante.

**Correção:**

```python
from psycopg2.errors import UniqueViolation
import logging

logger = logging.getLogger(__name__)

except UniqueViolation:
    # Só este erro é esperado; mensagem segura ao usuário
    erro = "Nome de usuário já existe."
except Exception as e:
    logger.exception("Erro ao salvar usuário")
    erro = "Erro interno. Contate o administrador."
```

---

## MEDIUM

### MD-01: `_guard()` em `permissoes.py` usa variável `conn` fora do contexto

**Arquivo:** `rotas/permissoes.py:60-67`

**Problema:**  
A variável `conn` é criada dentro do bloco `with conectar() as conn:` (linha 51-58), mas é referenciada após o `with` fechar (linha 62-65). Após o bloco `with` terminar, a conexão foi devolvida ao pool. A query `conn.execute(...)` na linha 63 usa a conexão devolvida.

```python
def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)

    with conectar() as conn:          # conn válida aqui
        grupo = conn.execute(...).fetchone()

    # BUG: conn foi devolvida ao pool, mas é usada aqui
    if grupo and grupo["grupo_id"] is not None and grupo["grupo_id"] != 1:
        pode = conn.execute(          # linha 63 — uso após fechar o with
            "SELECT ler FROM grupos_permissoes ...",
            (grupo["grupo_id"], "cadastros.permissoes")
        ).fetchone()
```

Com psycopg2 e pool simples, a conexão devolvida pode estar em uso por outro thread, causando comportamento indefinido ou erros de concorrência.

**Correção:**

```python
def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)

    with conectar() as conn:
        grupo = conn.execute(
            """SELECT a.grupo_id, g.nome as nome_grupo
               FROM atendentes a
               LEFT JOIN grupos g ON g.id = a.grupo_id
               WHERE a.id = %s""",
            (atendente["id"],)
        ).fetchone()

        if grupo and grupo["grupo_id"] is not None and grupo["grupo_id"] != 1:
            pode = conn.execute(
                "SELECT ler FROM grupos_permissoes WHERE grupo_id = %s AND modulo = %s",
                (grupo["grupo_id"], "cadastros.permissoes")
            ).fetchone()
            if not pode or not pode["ler"]:
                return None, HTMLResponse(status_code=403, content="Acesso negado.")

    return atendente, None
```

---

### MD-02: Race condition no check-in de pessoas

**Arquivo:** `rotas/checkin.py:270-302`

**Problema:**  
A verificação de limite de vagas e o INSERT/UPDATE do checkin ocorrem em transações separadas. Entre a verificação (`usados >= lim["vagas_dia"]`) e o INSERT, outro worker pode inserir o mesmo checkin, ultrapassando o limite.

```python
# Transação 1: verifica vagas
with conectar() as conn:
    usados = conn.execute(...).fetchone()["c"]
    if usados >= lim["vagas_dia"]:
        return erro  # OK

# Transação 2 (diferente): insere
with conectar() as conn:   # linha 306 — nova transação!
    dia = _dia_aberto(conn)
    # ... INSERT sem re-verificar o limite
```

**Correção:**  
Unificar verificação e INSERT em uma única transação usando `SELECT ... FOR UPDATE` ou `INSERT ... WHERE NOT EXISTS (SELECT ... COUNT > limite)`:

```python
with conectar() as conn:
    dia = _dia_aberto(conn)
    if not dia:
        return RedirectResponse(url="/dia", status_code=303)

    # Verificar limite dentro da mesma transação
    if codigo_atendimento and medium_id_int:
        lim = conn.execute(
            "SELECT vagas_dia FROM mediuns_dia WHERE dia_trabalho_id = %s AND medium_id = %s FOR UPDATE",
            (dia["id"], medium_id_int)
        ).fetchone()
        if lim and lim["vagas_dia"]:
            usados = conn.execute(
                "SELECT COUNT(*) AS c FROM checkins WHERE dia_trabalho_id = %s AND medium_id = %s AND codigo_atendimento IS NOT NULL",
                (dia["id"], medium_id_int)
            ).fetchone()["c"]
            if usados >= lim["vagas_dia"]:
                return ... # retornar erro

    # INSERT aqui — mesma transação
```

---

### MD-03: SQL dinâmico com f-string em `relatorios.py` e `financeiro.py`

**Arquivo:** `rotas/relatorios.py:176-192`, `rotas/financeiro.py:453-475`

**Problema:**  
Fragmentos WHERE são construídos com f-string, porém os parâmetros são passados corretamente via `%s`. O risco real aqui não é injeção (parâmetros estão protegidos), mas a construção com f-string pode confundir futuros mantenedores e introduzir injeção acidentalmente se um filtro for adicionado incorretamente.

```python
# relatorios.py:177 — filtro_dia com f-string mas valor fixo "= %s" — OK por enquanto
filtro_dia = "AND EXTRACT(ISODOW FROM dt.data::date) = %s"
rows = conn.execute(
    f"""... {filtro_dia} ...""",
    params,
).fetchall()
```

A mesma estrutura em `financeiro.py` (linhas 453-475) com `where` construído dinamicamente:

```python
where = "WHERE " + " AND ".join(filtros)  # filtros contêm apenas "campo = %s" — OK
conn.execute(f"SELECT ... FROM financeiro_movimentacoes {where}", params)
```

Estes são seguros porque `filtros` nunca contém dados do usuário, apenas predicados fixos. Contudo, o código da `mala_direta.py` (linhas 106-113) é diferente:

```python
# mala_direta.py:106 — where_email construído mas nunca recebe dados de usuário diretos
# O risco real está em where_tel que poderia receber busca se o padrão mudar
com_email = conn.execute(
    f"SELECT ... FROM pessoas {where_email} ORDER BY nome_completo",
    params,
).fetchall()
```

**Recomendação:** Usar sempre `psycopg2.sql` para construção dinâmica de SQL, eliminando riscos futuros:

```python
from psycopg2 import sql
query = sql.SQL("SELECT ... FROM financeiro_movimentacoes {where}").format(
    where=sql.SQL(where) if where else sql.SQL("")
)
```

---

### MD-04: Upload de arquivo salvo antes da validação de tamanho

**Arquivo:** `rotas/pessoas.py:37-41`, `rotas/trabalhadores.py:32-36`, `rotas/mediuns.py:32-36`

**Problema:**  
O arquivo é lido inteiro na memória e gravado em disco antes de verificar o tamanho. Se um atacante enviar um arquivo de vários GB, ele é carregado completamente na RAM:

```python
conteudo = file.file.read()        # lê tudo na memória primeiro
if len(conteudo) > TAMANHO_MAXIMO: # só DEPOIS verifica
    raise ValueError("Arquivo muito grande (máx. 5MB)")
f.write(conteudo)
```

**Correção:**

```python
# Ler em blocos e abortar cedo
TAMANHO_MAXIMO = 5 * 1024 * 1024
conteudo = b""
for chunk in iter(lambda: file.file.read(64 * 1024), b""):
    conteudo += chunk
    if len(conteudo) > TAMANHO_MAXIMO:
        raise ValueError("Arquivo muito grande (máx. 5MB)")
```

Ou usar `UploadFile` com `max_size` via middleware.

---

### MD-05: Tipo de arquivo validado apenas pela extensão, não pelo conteúdo

**Arquivo:** `rotas/pessoas.py:31`, `rotas/configuracoes.py:225-226`

**Problema:**  
A validação do tipo de upload de fotos e logo verifica apenas a extensão do nome do arquivo. Um atacante pode renomear `malware.php` para `malware.jpg` ou enviar um HTML com extensão `.png`.

```python
ext = os.path.splitext(file.filename)[1].lower()
if ext not in EXTENSOES_PERMITIDAS:
    ext = ".jpg"  # silenciosamente aceita com extensão diferente
```

**Correção:**

```python
# Verificar magic bytes (primeiros bytes do arquivo)
MAGIC_BYTES = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG": ".png",
    b"GIF8": ".gif",
    b"RIFF": ".webp",
    b"<svg": ".svg",
}
conteudo_inicio = conteudo[:8]
tipo_detectado = None
for magic, extensao in MAGIC_BYTES.items():
    if conteudo_inicio.startswith(magic):
        tipo_detectado = extensao
        break
if tipo_detectado is None:
    raise ValueError("Tipo de arquivo não suportado.")
```

Ou usar a biblioteca `python-magic`.

---

### MD-06: Senha padrão `admin`/`admin` criada automaticamente

**Arquivo:** `rotas/auth.py:136-141`

**Problema:**  
Se não houver nenhum usuário no banco, o sistema cria automaticamente o usuário `admin` com senha `admin`:

```python
cur = conn.execute(
    "INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash) "
    "VALUES (%s, %s, %s) RETURNING id",
    ("admin", "Administrador", hash_senha("admin"))  # senha padrão "admin"
)
```

Essa senha padrão é previsível e idêntica em todas as instalações. Combinado com CR-02 (SHA-256 sem salt), o hash é pré-computável.

**Correção:**  
Gerar uma senha aleatória no primeiro acesso e exibi-la no console do servidor, forçando a troca:

```python
if total == 0:
    senha_inicial = secrets.token_urlsafe(12)
    print(f"\n[SHAMBALA] Senha inicial do admin: {senha_inicial}")
    print("[SHAMBALA] Altere esta senha imediatamente em /cadastros/usuarios\n")
    cur = conn.execute(
        "INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash) "
        "VALUES (%s, %s, %s) RETURNING id",
        ("admin", "Administrador", hash_senha(senha_inicial))
    )
```

---

### MD-07: WebSocket da chamada sem autenticação

**Arquivo:** `rotas/chamada.py:50-57`

**Problema:**  
O endpoint WebSocket `/chamada/ws` aceita conexões de qualquer cliente sem verificar autenticação. O endpoint `/chamada/ultimo` (linha 37) também é público.

```python
@router.websocket("/chamada/ws")
async def ws_chamada(ws: WebSocket):
    await gerenciador.conectar(ws)  # sem verificar sessão
```

Em uma rede local com o sistema projetado, o risco é baixo, mas qualquer dispositivo na rede pode se conectar e receber os códigos de chamada.

**Correção mínima:**

```python
@router.websocket("/chamada/ws")
async def ws_chamada(ws: WebSocket, sessao: str = Cookie(None)):
    if not sessao or sessao not in _sessoes:
        await ws.close(code=1008)
        return
    await gerenciador.conectar(ws)
    # ...
```

---

## LOW

### LO-01: `_ConnCompat.__exit__` não fecha cursor nem reverte em exceções

**Arquivo:** `banco.py:33-34`

**Problema:**  
O `__exit__` do wrapper de conexão não faz nada. O tratamento de exceção (rollback) está corretamente no `conectar()` context manager (linha 89-91), mas se alguém instanciar `_ConnCompat` diretamente (o que é improvável mas possível), não haverá tratamento.

```python
def __exit__(self, *args):
    pass  # não faz nada
```

**Recomendação:**  
Documentar explicitamente que `_ConnCompat` só deve ser usado via `conectar()`. Ou implementar:

```python
def __exit__(self, exc_type, exc_val, exc_tb):
    if exc_type:
        self._conn.rollback()
```

---

### LO-02: `data_nascimento` não validada como data real

**Arquivo:** `rotas/pessoas.py:103-113`, `rotas/trabalhadores.py:77-87`

**Problema:**  
A função `_parse_data` converte o formato mas não valida se a data existe:

```python
def _parse_data(texto: str):
    if "/" in t:
        partes = t.split("/")
        if len(partes) == 3:
            d, m, a = partes
            return f"{a.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
    return t or None
```

Valores como `"99/99/9999"` são aceitos e armazenados. A query de prioridade por idade em `dia.py:14-19` usa `p.data_nascimento::date`, que falhará com erro de cast para datas inválidas, podendo quebrar o painel do dia.

**Correção:**

```python
from datetime import date as _date

def _parse_data(texto: str):
    t = texto.strip()
    if not t:
        return None
    try:
        if "/" in t:
            partes = t.split("/")
            if len(partes) == 3:
                d, m, a = partes
                iso = f"{a.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
                _date.fromisoformat(iso)  # valida a data
                return iso
        _date.fromisoformat(t)  # valida formato ISO
        return t
    except ValueError:
        return None  # data inválida → não armazenar
```

---

### LO-03: `mes_ref` não validado em `financeiro.py`

**Arquivo:** `rotas/financeiro.py:157` e `rotas/financeiro.py:210`

**Problema:**  
O parâmetro `mes` (ex: `"2024-13"`) é passado diretamente ao `split("-")` e ao `date()`:

```python
ano, mes_num = mes_ref.split("-")
ano, mes_num = int(ano), int(mes_num)
# ...
data_venc = date(ano, mes_num, dia)  # ValueError se mes_num > 12
```

Mês inválido lança `ValueError` não tratado, resultando em HTTP 500.

**Correção:**

```python
try:
    ano, mes_num = mes_ref.split("-")
    ano, mes_num = int(ano), int(mes_num)
    if not (1 <= mes_num <= 12):
        raise ValueError
except (ValueError, AttributeError):
    hoje = date.today()
    ano, mes_num = hoje.year, hoje.month
    mes_ref = f"{ano}-{mes_num:02d}"
```

---

### LO-04: Arquivo temporário SQL pode sobrar em disco após erro no backup

**Arquivo:** `backup.py:56-85`

**Problema:**  
No bloco `_pg_dump`, o arquivo temporário SQL é criado e, se a compressão falhar depois do `pg_dump`, o `tmp_sql.name` é limpo no `except`. Porém, se `gzip.open` falhar ao escrever (disco cheio), `tmp_gz` pode ficar sem ser limpo porque é criado como string (`tmp_gz = tmp_sql.name + ".gz"`) antes da abertura.

```python
with open(tmp_sql.name, "rb") as f_in:
    with gzip.open(tmp_gz, "wb") as f_out:  # se falhar aqui...
        shutil.copyfileobj(f_in, f_out)      # tmp_gz pode ficar incompleto
os.unlink(tmp_sql.name)                      # tmp_sql pode não ser limpo
```

O bloco `except` limpa ambos, então o risco é baixo — mas depende de que a exceção seja lançada antes do `unlink`.

**Recomendação:**  
Usar `tempfile.NamedTemporaryFile` para o `.gz` também, e limpar com `finally`.

---

### LO-05: `_migrar` usa `ALTER TABLE` com interpolação de string

**Arquivo:** `banco.py:198`

**Problema:**  
Ao adicionar colunas, os valores `tabela`, `coluna` e `tipo` são inseridos diretamente via f-string:

```python
conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
```

Os valores vêm de uma lista hardcoded no código (linha 151-188), então não há risco de injeção na prática. Porém, se alguém adicionar uma entrada à lista com valor controlado por usuário no futuro, haverá injeção. Adicionalmente, o `tipo` inclui strings como `"INTEGER REFERENCES mediuns(id)"`, que se inseridas erradas causariam quebra silenciosa.

**Risco real:** Baixo (lista é hardcoded). **Recomendação:** Documentar que esses valores jamais devem vir de entrada de usuário.

---

## INFO

### IN-01: Duplicação de código em três módulos de upload de foto

**Arquivos:** `rotas/pessoas.py`, `rotas/trabalhadores.py`, `rotas/mediuns.py`

As funções `_salvar_foto`, `_salvar_foto_trab` e `_salvar_foto_medium` são praticamente idênticas. Centralizar em um utilitário reduziria manutenção:

```python
# utils/fotos.py
def salvar_foto(file, prefixo: str, entity_id: int, foto_existente: str = None,
                fotos_dir: str = FOTOS_DIR, tamanho_max: int = 5*1024*1024) -> str:
    ...
```

---

### IN-02: `import urllib.parse` dentro de função

**Arquivo:** `rotas/checkin.py:428`, `rotas/dia.py:291`

Imports dentro de funções dificultam análise estática e são executados a cada chamada. Mover para o topo do arquivo.

---

### IN-03: Constantes mágicas espalhadas

**Arquivo:** `rotas/auth.py:139` (`"admin"`), `banco.py:113` (`[0, 2]` — dias default), `financeiro.py:99` (`LIMIT 200`)

Valores como o limite de 200 pessoas no form de nova movimentação e os dias default [0, 2] devem ser constantes nomeadas.

---

### IN-04: Cookie `sessao` sem prefixo `__Host-`

**Arquivo:** `rotas/auth.py:77`

Em sistemas com HTTPS, usar o prefixo `__Host-` no nome do cookie (`__Host-sessao`) impede que o cookie seja enviado a subdomínios e força `Secure` e `Path=/`. Melhoria de hardening para quando HTTPS for habilitado.

---

## Prioridades de Correção

| Prioridade | Item | Esforço estimado |
|------------|------|-----------------|
| 1 — Imediato | CR-02: Migrar para bcrypt | Médio (migração de hashes existentes) |
| 2 — Imediato | CR-01: Corrigir SQL injection em `pode_acessar` | Baixo |
| 3 — Imediato | CR-03: Adicionar TTL e limite às sessões | Baixo |
| 4 — Esta semana | HI-01: Implementar CSRF | Alto |
| 5 — Esta semana | MD-01: Bug `conn` fora do `with` em `permissoes.py` | Baixo |
| 6 — Esta semana | HI-02: Escapar XSS nos SVG placeholders | Baixo |
| 7 — Esta semana | MD-06: Senha padrão aleatória | Baixo |
| 8 — Próximo sprint | HI-03: ON CONFLICT no checkin de trabalhadores (router antigo) | Baixo |
| 9 — Próximo sprint | HI-04: Validar path no restaurar_backup | Baixo |
| 10 — Próximo sprint | MD-02 a MD-07: restantes | Variável |

---

_Revisado em 2026-04-14_  
_Revisor: Claude (gsd-code-reviewer) — análise estática manual, sem execução do código_
