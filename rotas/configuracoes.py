from fastapi import APIRouter, Request, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

import os
from banco import conectar
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/configuracoes")

LOGOS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "logos")
os.makedirs(LOGOS_DIR, exist_ok=True)

_NOMES_DIA = {
    0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira",
    3: "Quinta-feira",  4: "Sexta-feira", 5: "Sábado", 6: "Domingo",
}

CHAVES_SMTP = [
    ("smtp_servidor",   "Servidor SMTP",    "smtp.gmail.com"),
    ("smtp_porta",      "Porta",            "587"),
    ("smtp_usuario",    "Usuário / E-mail", ""),
    ("smtp_senha",      "Senha de aplicativo", ""),
    ("smtp_email_de",   "E-mail de envio",  ""),
]

CHAVES_BACKUP = [
    ("backup_host",         "Endereço IP / Host do servidor remoto", ""),
    ("backup_user",         "Usuário SSH",                          ""),
    ("backup_path",         "Caminho remoto para backups",          ""),
    ("backup_ssh_key_path", "Caminho da chave SSH privada",         ""),
]


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


def _ler_config_smtp(conn) -> dict:
    """Lê todas as chaves SMTP e retorna dict."""
    rows = conn.execute(
        "SELECT chave, valor FROM configuracoes_smtp WHERE chave LIKE %s",
        ("smtp_%",)
    ).fetchall()
    config = {}
    for r in rows:
        config[r["chave"]] = r["valor"]
    # Garante valores padrão se não existirem
    for chave, _, padrao in CHAVES_SMTP:
        if chave not in config:
            config[chave] = padrao
            try:
                conn.execute(
                    "INSERT INTO configuracoes_smtp (chave, valor) VALUES (%s, %s) ON CONFLICT (chave) DO NOTHING",
                    (chave, padrao)
                )
            except:
                pass  # Se falhar, apenas continua (já pode estar no BD)
    return config


def _ler_config_backup(conn) -> dict:
    """Lê todas as chaves de backup e retorna dict."""
    rows = conn.execute(
        "SELECT chave, valor FROM configuracoes_backup"
    ).fetchall()
    config = {}
    for r in rows:
        config[r["chave"]] = r["valor"]
    for chave, _, padrao in CHAVES_BACKUP:
        if chave not in config:
            config[chave] = padrao
            try:
                conn.execute(
                    "INSERT INTO configuracoes_backup (chave, valor) VALUES (%s, %s) ON CONFLICT (chave) DO NOTHING",
                    (chave, padrao)
                )
            except:
                pass
    return config


def _ler_config_centro(conn) -> dict:
    """Lê configurações do centro e retorna dict."""
    rows = conn.execute(
        "SELECT chave, valor FROM configuracoes_centro"
    ).fetchall()
    config = {}
    for r in rows:
        config[r["chave"]] = r["valor"]
    config.setdefault("centro_nome", "Centro Espírita")
    config.setdefault("centro_logo", "")
    return config


@router.get("", response_class=HTMLResponse)
async def pagina_configuracoes(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        dias = conn.execute(
            "SELECT dia_semana, descricao FROM dias_atendimento ORDER BY dia_semana"
        ).fetchall()
        smtp_config = _ler_config_smtp(conn)
        backup_config = _ler_config_backup(conn)
        centro_cfg = _ler_config_centro(conn)
    dias_atuais = {r["dia_semana"] for r in dias}
    return templates.TemplateResponse("configuracoes/index.html", {
        "request": request,
        "atendente": atendente,
        "dias": [dict(r) for r in dias],
        "dias_atuais": dias_atuais,
        "nomes_dia": _NOMES_DIA,
        "smtp_config": smtp_config,
        "backup_config": backup_config,
        "centro_cfg": centro_cfg,
    })


@router.post("/dias/adicionar")
async def adicionar_dia(request: Request, dia_semana: int = Form(...)):
    atendente, redir = _guard(request)
    if redir:
        return redir
    descricao = _NOMES_DIA.get(dia_semana, f"Dia {dia_semana}")
    with conectar() as conn:
        conn.execute(
            """INSERT INTO dias_atendimento (dia_semana, descricao)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (dia_semana, descricao)
        )
    return RedirectResponse(url="/configuracoes", status_code=303)


@router.post("/dias/{dia_semana}/remover")
async def remover_dia(request: Request, dia_semana: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        # Garante que sempre reste ao menos 1 dia configurado
        total = conn.execute("SELECT COUNT(*) AS c FROM dias_atendimento").fetchone()["c"]
        if total > 1:
            conn.execute(
                "DELETE FROM dias_atendimento WHERE dia_semana = %s", (dia_semana,)
            )
    return RedirectResponse(url="/configuracoes", status_code=303)


# ── Configuração SMTP ─────────────────────────────────────────────────────────

@router.post("/smtp")
async def salvar_config_smtp(
    request: Request,
    smtp_servidor: str = Form(""),
    smtp_porta: str = Form("587"),
    smtp_usuario: str = Form(""),
    smtp_senha: str = Form(""),
    smtp_email_de: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    dados = {
        "smtp_servidor": smtp_servidor.strip(),
        "smtp_porta": smtp_porta.strip(),
        "smtp_usuario": smtp_usuario.strip(),
        "smtp_senha": smtp_senha.strip(),
        "smtp_email_de": smtp_email_de.strip(),
    }

    with conectar() as conn:
        for chave, valor in dados.items():
            conn.execute(
                """INSERT INTO configuracoes_smtp (chave, valor)
                   VALUES (%s, %s)
                   ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor""",
                (chave, valor)
            )

    return RedirectResponse(url="/configuracoes", status_code=303)

# ── Configuração de Backup Remoto ─────────────────────────────────────────────

@router.post("/backup")
async def salvar_config_backup(
    request: Request,
    backup_host: str = Form(""),
    backup_user: str = Form("pi"),
    backup_path: str = Form("/home/pi/backup-shamballa"),
    backup_ssh_key_path: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    dados = {
        "backup_host": backup_host.strip(),
        "backup_user": backup_user.strip(),
        "backup_path": backup_path.strip(),
        "backup_ssh_key_path": backup_ssh_key_path.strip(),
    }

    with conectar() as conn:
        for chave, valor in dados.items():
            conn.execute(
                """INSERT INTO configuracoes_backup (chave, valor)
                   VALUES (%s, %s)
                   ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor""",
                (chave, valor)
            )

    return RedirectResponse(url="/configuracoes", status_code=303)

# ── Configuração do Centro (nome e logo) ─────────────────────────────────────

def _salvar_logo_centro(file: UploadFile) -> str:
    """Salva logo do centro e retorna o nome do arquivo."""
    import uuid
    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ".png"
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
        ext = ".png"
    nome_arquivo = f"logo_centro_{uuid.uuid4().hex[:8]}{ext}"
    caminho = os.path.join(LOGOS_DIR, nome_arquivo)

    # Remover logos antigos
    for antigo in os.listdir(LOGOS_DIR):
        if antigo.startswith("logo_centro_"):
            try:
                os.remove(os.path.join(LOGOS_DIR, antigo))
            except OSError:
                pass

    with open(caminho, "wb") as f:
        conteudo = file.file.read()
        f.write(conteudo[:10 * 1024 * 1024])  # Máx 10MB
    return nome_arquivo


@router.post("/centro")
async def salvar_config_centro(
    request: Request,
    centro_nome: str = Form(""),
    logo: UploadFile = None,
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        # Salvar nome
        conn.execute(
            """INSERT INTO configuracoes_centro (chave, valor)
               VALUES ('centro_nome', %s)
               ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor""",
            (centro_nome.strip(),)
        )

        # Salvar logo se enviada
        if logo and getattr(logo, "filename", None):
            nome_logo = _salvar_logo_centro(logo)
            conn.execute(
                """INSERT INTO configuracoes_centro (chave, valor)
                   VALUES ('centro_logo', %s)
                   ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor""",
                (nome_logo,)
            )

    return RedirectResponse(url="/configuracoes", status_code=303)
