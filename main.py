from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from banco import criar_tabelas, fechar_pool
from rotas import auth, pessoas, mediuns, atendentes, dia, checkin, chamada, relatorios, agenda, configuracoes, mala_direta, trabalhadores, permissoes, financeiro, doacoes, biblioteca
from rotas.auth import criar_atendente_inicial


@asynccontextmanager
async def lifespan(app: FastAPI):
    criar_tabelas()
    criar_atendente_inicial()
    permissoes.seed_permissoes()
    yield
    fechar_pool()


app = FastAPI(title="Casa Espírita Shambala", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

# Filtro global de data para todos os templates
from fastapi.templating import Jinja2Templates as _J
_templates = _J(directory="templates")

def _fmt_data(valor: str) -> str:
    """Converte AAAA-MM-DD → DD/MM/AAAA"""
    if valor and len(valor) == 10:
        return f"{valor[8:10]}/{valor[5:7]}/{valor[:4]}"
    return valor or ""

_templates.env.filters["data_br"] = _fmt_data

app.include_router(auth.router)
app.include_router(pessoas.router)
app.include_router(mediuns.router)
app.include_router(atendentes.router)
app.include_router(dia.router)
app.include_router(checkin.router)
app.include_router(chamada.router)
app.include_router(relatorios.router)
app.include_router(agenda.router)
app.include_router(configuracoes.router)
app.include_router(mala_direta.router)
app.include_router(trabalhadores.router)
app.include_router(permissoes.router)
app.include_router(financeiro.router)
app.include_router(doacoes.router)
app.include_router(biblioteca.router)
