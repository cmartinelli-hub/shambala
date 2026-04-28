import urllib.parse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from banco import conectar
from rotas.auth import obter_atendente_logado
from templates_config import templates

router = APIRouter(prefix="/mala-direta")


def _guard(request: Request):
    atendente = obter_atendente_logado(request)
    if not atendente:
        return None, RedirectResponse(url="/login", status_code=303)
    return atendente, None


def _formatar_telefone(telefone: str) -> str:
    """Limpa o telefone deixando apenas dígitos."""
    digits = "".join(c for c in telefone if c.isdigit())
    if digits.startswith("55"):
        return digits
    if len(digits) == 13 and digits[:2] == "24":
        return "55" + digits
    if len(digits) == 11 and digits[:2] == "24":
        return "55" + digits
    if len(digits) == 10 and digits[:2] == "24":
        return "55" + digits
    return "55" + digits


def _ler_config_smtp(conn) -> dict:
    """Lê configurações SMTP do banco."""
    rows = conn.execute(
        "SELECT chave, valor FROM configuracoes_smtp WHERE chave LIKE 'smtp_%'"
    ).fetchall()
    config = {}
    for r in rows:
        config[r["chave"]] = r["valor"]
    return config


def _enviar_email_smtp(destinatario: str, assunto: str, mensagem: str,
                       smtp_config: dict) -> tuple[bool, str]:
    """Envia um e-mail via SMTP. Retorna (sucesso, mensagem_erro)."""
    servidor = smtp_config.get("smtp_servidor", "").strip()
    porta_str = smtp_config.get("smtp_porta", "587").strip()
    usuario = smtp_config.get("smtp_usuario", "").strip()
    senha = smtp_config.get("smtp_senha", "").strip()
    email_de = smtp_config.get("smtp_email_de", "").strip()

    if not servidor or not usuario or not senha or not email_de:
        return False, "SMTP não configurado. Configure em /configuracoes."

    try:
        porta = int(porta_str)
    except ValueError:
        return False, f"Porta SMTP inválida: {porta_str}"

    msg = MIMEMultipart()
    msg["From"] = email_de
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.attach(MIMEText(mensagem, "plain", "utf-8"))

    try:
        if porta == 465:
            server = smtplib.SMTP_SSL(servidor, porta)
        else:
            server = smtplib.SMTP(servidor, porta)
            server.starttls()
        server.login(usuario, senha)
        server.sendmail(email_de, [destinatario], msg.as_string())
        server.quit()
        return True, ""
    except Exception as e:
        return False, str(e)


@router.get("", response_class=HTMLResponse)
async def pagina_mala_direta(
    request: Request,
    busca: str = Query(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    with conectar() as conn:
        if busca.strip():
            termo = f"%{busca.strip().lower()}%"
            where_email = " WHERE (LOWER(nome_completo) LIKE %s OR LOWER(email) LIKE %s) AND email IS NOT NULL AND email != '' "
            where_tel = " WHERE (LOWER(nome_completo) LIKE %s OR LOWER(email) LIKE %s) AND telefone IS NOT NULL AND telefone != '' "
            params: tuple = (termo, termo)
        else:
            where_email = " WHERE email IS NOT NULL AND email != '' "
            where_tel = " WHERE telefone IS NOT NULL AND telefone != '' "
            params = ()

        com_email = conn.execute(
            f"SELECT id, nome_completo, email FROM pessoas {where_email} ORDER BY nome_completo",
            params,
        ).fetchall()

        com_telefone = conn.execute(
            f"SELECT id, nome_completo, telefone FROM pessoas {where_tel} ORDER BY nome_completo",
            params,
        ).fetchall()

    return templates.TemplateResponse("mala_direta/index.html", {
        "request": request,
        "atendente": atendente,
        "com_email": [dict(r) for r in com_email],
        "com_telefone": [dict(r) for r in com_telefone],
        "busca": busca,
        "total_email": len(com_email),
        "total_telefone": len(com_telefone),
    })


@router.post("/enviar", response_class=HTMLResponse)
async def enviar_mala_direta(
    request: Request,
    assunto: str = Form(""),
    mensagem: str = Form(""),
    emails_selecionados: str = Form(""),
    telefones_selecionados: str = Form(""),
    canal: str = Form("email"),
    acao: str = Form("gerar"),  # "gerar" ou "enviar"
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    # Carrega os dados completos dos selecionados
    lista_emails = []
    if emails_selecionados.strip():
        ids_email = [int(x) for x in emails_selecionados.split(",") if x.strip()]
        if ids_email:
            with conectar() as conn:
                placeholders = ",".join("%s" for _ in ids_email)
                rows = conn.execute(
                    f"SELECT id, nome_completo, email FROM pessoas WHERE id IN ({placeholders}) AND email IS NOT NULL AND email != '' ORDER BY nome_completo",
                    ids_email,
                ).fetchall()
                lista_emails = [dict(r) for r in rows]

    lista_telefones = []
    if telefones_selecionados.strip():
        ids_tel = [int(x) for x in telefones_selecionados.split(",") if x.strip()]
        if ids_tel:
            with conectar() as conn:
                placeholders = ",".join("%s" for _ in ids_tel)
                rows = conn.execute(
                    f"SELECT id, nome_completo, telefone FROM pessoas WHERE id IN ({placeholders}) AND telefone IS NOT NULL AND telefone != '' ORDER BY nome_completo",
                    ids_tel,
                ).fetchall()
                lista_telefones = [dict(r) for r in rows]

    # ── Envio real por SMTP ──
    enviados = 0
    falhas = []
    smtp_config = {}

    if acao == "enviar" and lista_emails and canal == "email":
        with conectar() as conn:
            smtp_config = _ler_config_smtp(conn)

        for pessoa in lista_emails:
            ok, erro = _enviar_email_smtp(
                pessoa["email"], assunto, mensagem, smtp_config
            )
            if ok:
                enviados += 1
            else:
                falhas.append({"nome": pessoa["nome_completo"], "email": pessoa["email"], "erro": erro})

    # Prepara dados para o template de resultado
    emails_str = ";".join(r["email"] for r in lista_emails) if lista_emails else ""
    emails_str_comma = ", ".join(r["email"] for r in lista_emails) if lista_emails else ""

    telefones_formatados = []
    telefones_wa = []
    for r in lista_telefones:
        tel_limpo = "".join(c for c in r["telefone"] if c.isdigit())
        tel_wa = _formatar_telefone(r["telefone"])
        telefones_formatados.append(tel_limpo)
        telefones_wa.append({"nome": r["nome_completo"], "tel_wa": tel_wa, "tel_display": r["telefone"]})

    telefones_lista = ", ".join(telefones_formatados) if telefones_formatados else ""

    # Gera links wa.me para cada telefone
    mensagem_url = urllib.parse.quote(mensagem) if mensagem else ""

    mailto_subject = urllib.parse.quote(assunto) if assunto else ""
    mailto_body = urllib.parse.quote(mensagem) if mensagem else ""
    mailto_link = ""
    if emails_str_comma and assunto and mensagem:
        mailto_link = f"mailto:{emails_str_comma}?subject={mailto_subject}&body={mailto_body}"

    return templates.TemplateResponse("mala_direta/resultado.html", {
        "request": request,
        "atendente": atendente,
        "canal": canal,
        "assunto": assunto,
        "mensagem": mensagem,
        "emails_str": emails_str,
        "emails_str_comma": emails_str_comma,
        "lista_emails": lista_emails,
        "telefones_lista": telefones_lista,
        "telefones_wa": telefones_wa,
        "lista_telefones": lista_telefones,
        "mailto_link": mailto_link,
        "mensagem_url": mensagem_url,
        "enviados": enviados,
        "falhas": falhas,
    })


@router.post("/whatsapp-abrir", response_class=HTMLResponse)
async def whatsapp_abrir(
    request: Request,
    numero: str = Form(""),
    mensagem: str = Form(""),
):
    atendente, redir = _guard(request)
    if redir:
        return redir

    tel_wa = _formatar_telefone(numero)
    msg_url = urllib.parse.quote(mensagem)
    url_wa = f"https://wa.me/{tel_wa}?text={msg_url}"

    return RedirectResponse(url=url_wa, status_code=302)
