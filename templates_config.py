from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")


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
