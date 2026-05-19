#!/usr/bin/env python3
"""Agente local de impressão ESC/POS.

Roda no PC do caixa, escuta em localhost:9001.
O browser envia os bytes ESC/POS (buscados do servidor) e o agente
os escreve direto em /dev/ttyUSB0.

Uso:
    python3 agente_impressora.py

Para rodar como serviço, copie agente_impressora.service para
/etc/systemd/system/ e habilite com systemctl enable --now agente_impressora.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler

PORTA      = 9001
DISPOSITIVO = '/dev/ttyUSB0'


class _Handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self._cabecalhos(200)
        self.end_headers()

    def do_POST(self):
        if self.path != '/imprimir':
            self._cabecalhos(404)
            self.end_headers()
            return
        tamanho = int(self.headers.get('Content-Length', 0))
        dados = self.rfile.read(tamanho)
        try:
            with open(DISPOSITIVO, 'wb') as f:
                f.write(dados)
            self._cabecalhos(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
        except PermissionError:
            self._cabecalhos(500)
            self.end_headers()
            self.wfile.write(f'{{"erro": "Sem permissao para {DISPOSITIVO}"}}'.encode())
        except Exception as e:
            self._cabecalhos(500)
            self.end_headers()
            self.wfile.write(f'{{"erro": "{e}"}}'.encode())

    def _cabecalhos(self, code: int):
        self.send_response(code)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Type', 'application/json')

    def log_message(self, fmt, *args):
        pass  # silenciar logs de acesso


if __name__ == '__main__':
    print(f'Agente de impressao rodando em localhost:{PORTA}')
    print(f'Dispositivo: {DISPOSITIVO}')
    HTTPServer(('localhost', PORTA), _Handler).serve_forever()
