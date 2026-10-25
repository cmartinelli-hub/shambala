from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import os


class _TemplatesCompat(Jinja2Templates):
    """Aceita a assinatura antiga (nome, dict) e a nova (request, nome, dict)."""

    def TemplateResponse(self, *args, **kwargs):
        # Assinatura antiga: TemplateResponse("nome.html", {"request": req, ...})
        if args and isinstance(args[0], str):
            name = args[0]
            context = args[1] if len(args) > 1 else kwargs.pop("context", {})
            request = context.get("request") or kwargs.pop("request", None)
            ctx = {k: v for k, v in context.items() if k != "request"}
            return super().TemplateResponse(request, name, ctx, **kwargs)
        # Assinatura nova: TemplateResponse(request, "nome.html", {...})
        return super().TemplateResponse(*args, **kwargs)


templates = _TemplatesCompat(directory="templates")

_PASTA_ESTATICA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _data_br(valor: str) -> str:
    """AAAA-MM-DD → DD/MM/AAAA"""
    if valor and len(valor) == 10:
        return f"{valor[8:10]}/{valor[5:7]}/{valor[:4]}"
    return valor or ""


def _data_hora_br(valor: str) -> str:
    """AAAA-MM-DD HH:MM:SS[.ffffff] → DD/MM/AAAA HH:MM:SS (sem microssegundos)"""
    if valor and len(valor) >= 19:
        return f"{valor[8:10]}/{valor[5:7]}/{valor[:4]} {valor[11:19]}"
    return valor or ""


def _moeda(valor) -> str:
    """R$ 1.234,56 — aceita Decimal, float, int, str; None vira R$ 0,00."""
    from decimal import Decimal, InvalidOperation
    if valor is None:
        d = Decimal("0")
    elif isinstance(valor, Decimal):
        d = valor
    else:
        try:
            d = Decimal(str(valor))
        except (InvalidOperation, ValueError):
            return str(valor)
    inteiro, frac = f"{abs(d):.2f}".split(".")
    milhar = ""
    for i, c in enumerate(reversed(inteiro)):
        if i and i % 3 == 0:
            milhar = "." + milhar
        milhar = c + milhar
    sinal = "-" if d < 0 else ""
    return f"{sinal}R$ {milhar},{frac}"


def _asset_url(caminho: str) -> str:
    """/static/<caminho>?v=<mtime> — evita que o navegador reutilize CSS antigo."""
    try:
        versao = int(os.stat(os.path.join(_PASTA_ESTATICA, caminho)).st_mtime)
    except OSError:
        versao = 0
    return f"/static/{caminho}?v={versao}"


templates.env.filters["data_br"] = _data_br
templates.env.filters["data_hora_br"] = _data_hora_br
templates.env.filters["moeda"] = _moeda
templates.env.globals["asset_url"] = _asset_url


def _obter_config_centro() -> dict:
    """Retorna configurações do centro (nome e logo) do banco."""
    try:
        from banco import conectar
        with conectar() as conn:
            rows = conn.execute(
                "SELECT chave, valor FROM configuracoes_centro"
            ).fetchall()
            config = {}
            for r in rows:
                config[r["chave"]] = r["valor"]
            config.setdefault("centro_nome", "Centro Espírita")
            config.setdefault("centro_logo", "")
            config.setdefault("impressora_url", "http://localhost:9001")
            return config
    except Exception:
        return {"centro_nome": "Centro Espírita", "centro_logo": "", "impressora_url": "http://localhost:9001"}


# Função callable para uso nos templates
def centro_config():
    return _obter_config_centro()


# Tornar disponível como função global nos templates
templates.env.globals["centro_config"] = centro_config
