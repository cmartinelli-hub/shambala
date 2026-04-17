import asyncio
import json
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse

from templates_config import templates, _obter_config_centro as obter_config_centro
router = APIRouter()

TEMPO_MINIMO_SEGUNDOS = 20


class GerenciadorConexoes:
    def __init__(self):
        self._conexoes: list[WebSocket] = []
        self.ultimo_codigo: str = ""
        self.historico: list[str] = []
        self._ultima_transmissao: float = 0.0
        self._fila: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None

    async def conectar(self, ws: WebSocket):
        await ws.accept()
        self._conexoes.append(ws)

    def desconectar(self, ws: WebSocket):
        if ws in self._conexoes:
            self._conexoes.remove(ws)

    def _garantir_worker(self):
        if self._fila is None:
            self._fila = asyncio.Queue()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._processar_fila())

    async def _enviar_agora(self, codigo: str):
        if self.ultimo_codigo:
            self.historico = [self.ultimo_codigo] + self.historico[:2]
        self.ultimo_codigo = codigo
        self._ultima_transmissao = time.monotonic()
        texto = json.dumps({"codigo": codigo, "historico": self.historico})
        mortos = []
        for ws in self._conexoes:
            try:
                await ws.send_text(texto)
            except Exception:
                mortos.append(ws)
        for ws in mortos:
            if ws in self._conexoes:
                self._conexoes.remove(ws)

    async def _processar_fila(self):
        while True:
            codigo = await self._fila.get()
            agora = time.monotonic()
            espera = TEMPO_MINIMO_SEGUNDOS - (agora - self._ultima_transmissao)
            if espera > 0:
                await asyncio.sleep(espera)
            await self._enviar_agora(codigo)
            self._fila.task_done()

    async def transmitir(self, codigo: str):
        self._garantir_worker()
        while not self._fila.empty():
            try:
                self._fila.get_nowait()
                self._fila.task_done()
            except asyncio.QueueEmpty:
                break
        await self._fila.put(codigo)


gerenciador = GerenciadorConexoes()


@router.get("/chamada/ultimo")
async def ultimo_chamado():
    return JSONResponse({"codigo": gerenciador.ultimo_codigo, "historico": gerenciador.historico})


@router.get("/chamada", response_class=HTMLResponse)
async def pagina_chamada(request: Request):
    return templates.TemplateResponse("chamada.html", {
        "request": request,
        "centro": obter_config_centro(),
    })


@router.websocket("/chamada/ws")
async def ws_chamada(ws: WebSocket):
    await gerenciador.conectar(ws)
    try:
        while True:
            await ws.receive_text()  # mantém a conexão viva
    except WebSocketDisconnect:
        gerenciador.desconectar(ws)
