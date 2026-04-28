from fastapi.templating import Jinja2Templates
from starlette.requests import Request


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


def _data_br(valor: str) -> str:
    """AAAA-MM-DD → DD/MM/AAAA"""
    if valor and len(valor) == 10:
        return f"{valor[8:10]}/{valor[5:7]}/{valor[:4]}"
    return valor or ""


templates.env.filters["data_br"] = _data_br


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
            return config
    except Exception:
        return {"centro_nome": "Centro Espírita", "centro_logo": ""}


# Função callable para uso nos templates
def centro_config():
    return _obter_config_centro()


# Tornar disponível como função global nos templates
templates.env.globals["centro_config"] = centro_config
