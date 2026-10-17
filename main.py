from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import os

# Carrega variáveis de ambiente.
# SHAMBALA_ENV_FILE permite apontar para um arquivo de configuração alternativo
# (usado pelo ambiente de testes, que NUNCA deve ler o .env do projeto — foi a
# ausência dessa separação que permitiu um teste atingir o banco de dev).
_env_file = os.environ.get("SHAMBALA_ENV_FILE")
if _env_file:
    load_dotenv(_env_file)
else:
    load_dotenv()

from banco import criar_tabelas, fechar_pool, conectar
from rotas import auth, pessoas, mediuns, usuarios, dia, checkin, chamada, relatorios, agenda, configuracoes, mala_direta, trabalhadores, permissoes, financeiro, doacoes, biblioteca, compras, caixa
from rotas import financeiro_mensalidades
from rotas.auth import criar_atendente_inicial


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Defesa em profundidade: no modo de teste, recusa iniciar se a conexão
    # efetiva não for comprovadamente um banco descartável. Impede que um app
    # de teste trunque o banco de desenvolvimento (incidente de 2026-10-14).
    if os.environ.get("SHAMBALA_MODO_TESTE") == "1":
        from tests.trava_isolamento import exigir_ambiente_de_teste
        with conectar() as _conn:
            info = exigir_ambiente_de_teste(_conn)
        print(f"[TESTE] Ambiente descartável confirmado: {info}", flush=True)

    criar_tabelas()
    criar_atendente_inicial()
    permissoes.seed_permissoes()
    # Implantação sem retroativo: garante mensalidades (corrente + 12 futuras)
    # para todos os ativos — idempotente, auto-reparadora a cada restart
    financeiro_mensalidades.gerar_todas_ativas()
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
app.include_router(usuarios.router)
app.include_router(dia.router)
app.include_router(checkin.router)
app.include_router(chamada.router)
app.include_router(relatorios.router)
app.include_router(agenda.router)
app.include_router(configuracoes.router)
app.include_router(mala_direta.router)
app.include_router(trabalhadores.router)
app.include_router(trabalhadores.router_checkin_trab)
app.include_router(permissoes.router)
app.include_router(financeiro.router)
app.include_router(doacoes.router)
app.include_router(biblioteca.router)
app.include_router(caixa.router)
app.include_router(compras.router)

# Alias para compatibilidade: /cadastros/atendentes → /cadastros/usuarios
from fastapi import APIRouter as _APIRouter
_compat_router = _APIRouter()

@_compat_router.get("/")
async def redirect_atendentes():
    return RedirectResponse(url="/cadastros/usuarios", status_code=301)

@_compat_router.get("/{path:path}")
async def redirect_atendentes_path(path: str):
    return RedirectResponse(url=f"/cadastros/usuarios/{path}", status_code=301)

app.include_router(_compat_router, prefix="/cadastros/atendentes")
