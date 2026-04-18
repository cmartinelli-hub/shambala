from fastapi import APIRouter, Request, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

import os
from banco import conectar
from rotas.auth import obter_atendente_logado
from templates_config import templates
from backup_pendrive import (
    montar_dispositivo, desmontar_dispositivo,
    fazer_pg_dump, obter_espaco_disponivel,
    executar_backup_completo, registrar_backup_historico,
    validar_dispositivo, validar_ponto_montagem
)

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


# ── Configuração de Backup em Pendrive ───────────────────────────────────────

def _ler_config_pendrive(conn) -> dict:
    """Lê configuração de backup em pendrive."""
    config = conn.execute(
        "SELECT * FROM configuracoes_backup_pendrive LIMIT 1"
    ).fetchone()
    if not config:
        return {
            "id": None,
            "tipo_backup": "pendrive",
            "dispositivo": "",
            "ponto_montagem": "",
            "ativo": 0,
            "horario_backup": None,
        }
    return dict(config)


def _ler_historico_pendrive(conn) -> list:
    """Lê histórico de backups."""
    rows = conn.execute(
        "SELECT * FROM backup_pendrive_historico ORDER BY data_backup DESC LIMIT 20"
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/backup-pendrive", response_class=HTMLResponse)
async def pagina_backup_pendrive(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return redir
    with conectar() as conn:
        config = _ler_config_pendrive(conn)
        historico = _ler_historico_pendrive(conn)
    return templates.TemplateResponse("configuracoes/backup_pendrive.html", {
        "request": request,
        "atendente": atendente,
        "config": config,
        "historico": historico,
    })


@router.post("/backup-pendrive")
async def salvar_config_pendrive(
    request: Request,
    dispositivo: str = Form(""),
    ponto_montagem: str = Form(""),
    ativo: int = Form(0),
    horario_backup: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    dispositivo = dispositivo.strip()
    ponto_montagem = ponto_montagem.strip()

    # Validações básicas
    if dispositivo and not validar_dispositivo(dispositivo):
        return RedirectResponse(url="/configuracoes/backup-pendrive?erro=dispositivo_invalido", status_code=303)
    if ponto_montagem and not validar_ponto_montagem(ponto_montagem):
        return RedirectResponse(url="/configuracoes/backup-pendrive?erro=montagem_invalida", status_code=303)

    with conectar() as conn:
        existente = _ler_config_pendrive(conn)
        if existente.get("id"):
            # Atualiza existente
            conn.execute(
                """UPDATE configuracoes_backup_pendrive
                   SET dispositivo = %s, ponto_montagem = %s, ativo = %s, horario_backup = %s, atualizado_em = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (dispositivo, ponto_montagem, ativo, horario_backup or None, existente["id"])
            )
        else:
            # Insere novo
            conn.execute(
                """INSERT INTO configuracoes_backup_pendrive
                   (tipo_backup, dispositivo, ponto_montagem, ativo, horario_backup)
                   VALUES (%s, %s, %s, %s, %s)""",
                ("pendrive", dispositivo, ponto_montagem, ativo, horario_backup or None)
            )

    return RedirectResponse(url="/configuracoes/backup-pendrive", status_code=303)


@router.post("/backup-pendrive/testar")
async def testar_backup_pendrive(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return JSONResponse({"erro": "Não autenticado"}, status_code=401)

    with conectar() as conn:
        config = _ler_config_pendrive(conn)

    if not config.get("dispositivo"):
        return JSONResponse({"erro": "Dispositivo não configurado"}, status_code=400)

    # Tenta montar
    ok, msg = montar_dispositivo(config["dispositivo"], config["ponto_montagem"])

    if ok:
        usado, disp = obter_espaco_disponivel(config["ponto_montagem"])
        return JSONResponse({
            "sucesso": True,
            "mensagem": msg,
            "espaco_usado_mb": usado,
            "espaco_disponivel_mb": disp,
        })
    else:
        return JSONResponse({"erro": msg}, status_code=400)


@router.post("/backup-pendrive/executar")
async def executar_backup_pendrive(request: Request):
    atendente, redir = _guard(request)
    if redir:
        return JSONResponse({"erro": "Não autenticado"}, status_code=401)

    with conectar() as conn:
        config = _ler_config_pendrive(conn)

    if not config.get("dispositivo") or not config.get("ponto_montagem"):
        return JSONResponse({"erro": "Dispositivo ou ponto de montagem não configurados"}, status_code=400)

    # Executa backup completo
    resultado = executar_backup_completo(config["dispositivo"], config["ponto_montagem"])

    # Registra no histórico
    with conectar() as conn:
        registrar_backup_historico(
            status="sucesso" if resultado["sucesso"] else "erro",
            caminho_backup=resultado["caminho_backup"],
            tamanho=resultado["tamanho_backup"],
            espaco_disp=resultado["espaco_disponivel"],
            erro="" if resultado["sucesso"] else resultado["mensagem"]
        )

    if resultado["sucesso"]:
        return JSONResponse({
            "sucesso": True,
            "mensagem": resultado["mensagem"],
            "tamanho_mb": resultado["tamanho_backup"] / (1024 * 1024),
            "espaco_disponivel_mb": resultado["espaco_disponivel"],
        })
    else:
        return JSONResponse({"erro": resultado["mensagem"]}, status_code=400)
