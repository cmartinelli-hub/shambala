from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")


def _data_br(valor: str) -> str:
    """AAAA-MM-DD → DD/MM/AAAA"""
    if valor and len(valor) == 10:
        return f"{valor[8:10]}/{valor[5:7]}/{valor[:4]}"
    return valor or ""


templates.env.filters["data_br"] = _data_br
