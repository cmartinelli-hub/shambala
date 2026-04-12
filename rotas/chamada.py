from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse

from templates_config import templates, _obter_config_centro as obter_config_centro
router = APIRouter()


class GerenciadorConexoes:
    def __init__(self):
        self._conexoes: list[WebSocket] = []
        self.ultimo_codigo: str = ""

    async def conectar(self, ws: WebSocket):
        await ws.accept()
        self._conexoes.append(ws)

    def desconectar(self, ws: WebSocket):
        if ws in self._conexoes:
            self._conexoes.remove(ws)

    async def transmitir(self, codigo: str):
        self.ultimo_codigo = codigo
        mortos = []
        for ws in self._conexoes:
            try:
                await ws.send_text(codigo)
            except Exception:
                mortos.append(ws)
        for ws in mortos:
            if ws in self._conexoes:
                self._conexoes.remove(ws)


gerenciador = GerenciadorConexoes()


@router.get("/chamada/ultimo")
async def ultimo_chamado():
    return JSONResponse({"codigo": gerenciador.ultimo_codigo})


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
