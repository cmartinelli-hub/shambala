"""Contratos de integração para o futuro redesenho Financeiro/Caixa.

Execução deliberadamente segura:

    SHAMBALA_TEST_DB=shambala_test .venv/bin/python -m unittest -v \
        tests.test_financeiro_integracao

O módulo se recusa a conectar caso o banco não termine em ``_test``. Ele usa
PostgreSQL real e não depende de pytest nem de cliente HTTP externo.
"""
import asyncio
import json
import os
import unittest
import uuid


NOME_BANCO_TESTE = os.environ.get("SHAMBALA_TEST_DB", "")
PERMITIR_BANCO_DEV = os.environ.get("SHAMBALA_ALLOW_DEV_DB_TESTS") == "1"
BANCO_SEGURO = NOME_BANCO_TESTE.endswith("_test") or (
    NOME_BANCO_TESTE == "shambala" and PERMITIR_BANCO_DEV
)


@unittest.skipUnless(
    BANCO_SEGURO,
    "Use banco *_test; para o ambiente dev `shambala`, defina também SHAMBALA_ALLOW_DEV_DB_TESTS=1.",
)
class FinanceiroIntegracaoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # banco.py lê as variáveis no import. O nome seguro é imposto acima.
        os.environ["SHAMBALA_DB_NAME"] = NOME_BANCO_TESTE
        from banco import criar_tabelas, conectar, fechar_pool

        fechar_pool()
        criar_tabelas()
        cls.conectar = staticmethod(conectar)
        cls.prefixo = f"TESTE_FIN_{uuid.uuid4().hex[:10]}"

        from rotas import caixa, financeiro
        cls.caixa = caixa
        cls.financeiro = financeiro

    def setUp(self):
        with self.conectar() as conn:
            cur = conn.execute(
                """INSERT INTO atendentes (nome_usuario, nome_completo, senha_hash)
                   VALUES (%s, %s, %s) RETURNING id""",
                (f"{self.prefixo}_{uuid.uuid4().hex[:6]}", "Operador de teste", "teste"),
            )
            self.atendente_id = cur.fetchone()["id"]

    def tearDown(self):
        # Executa as limpezas específicas (venda/trabalhador) antes de remover
        # o operador, que é referenciado pelos registros de caixa.
        self.doCleanups()
        # Limpeza é intencionalmente por chaves de teste; não toca dados alheios.
        with self.conectar() as conn:
            conn.execute("DELETE FROM auditoria_eventos WHERE atendente_id=%s", (self.atendente_id,))
            conn.execute("UPDATE caixa_movimentos SET atendente_id_abertura=NULL, atendente_id_fechamento=NULL WHERE atendente_id_abertura=%s OR atendente_id_fechamento=%s", (self.atendente_id, self.atendente_id))
            conn.execute("UPDATE vendas_pdv SET atendente_id=NULL WHERE atendente_id=%s", (self.atendente_id,))
            conn.execute("UPDATE estoque_movimentos SET atendente_id=NULL WHERE atendente_id=%s", (self.atendente_id,))
            conn.execute("DELETE FROM atendentes WHERE id=%s", (self.atendente_id,))

    def _criar_trabalhador(self, valor=50):
        with self.conectar() as conn:
            cur = conn.execute(
                """INSERT INTO trabalhadores (nome_completo, valor_mensalidade, dia_vencimento, ativo)
                   VALUES (%s, %s, 10, 1) RETURNING id""",
                (f"{self.prefixo} trabalhador {uuid.uuid4().hex[:5]}", valor),
            )
            trabalhador_id = cur.fetchone()["id"]
        self.addCleanup(self._remover_trabalhador, trabalhador_id)
        return trabalhador_id

    def _remover_trabalhador(self, trabalhador_id):
        with self.conectar() as conn:
            conn.execute("DELETE FROM auditoria_eventos WHERE entidade='trabalhador' AND entidade_id=%s", (trabalhador_id,))
            conn.execute("DELETE FROM financeiro_movimentacoes WHERE trabalhador_id=%s", (trabalhador_id,))
            conn.execute("DELETE FROM fiado_credito_mov WHERE trabalhador_id=%s", (trabalhador_id,))
            conn.execute("DELETE FROM trabalhadores WHERE id=%s", (trabalhador_id,))

    def test_geracao_de_mensalidades_e_idempotente(self):
        from rotas.financeiro_mensalidades import sincronizar_mensalidades

        trabalhador_id = self._criar_trabalhador(valor=75)
        with self.conectar() as conn:
            trab = conn.execute("SELECT * FROM trabalhadores WHERE id=%s", (trabalhador_id,)).fetchone()
            sincronizar_mensalidades(conn, dict(trab))
            sincronizar_mensalidades(conn, dict(trab))
            linhas = conn.execute(
                """SELECT data_movimentacao, status, conta_id FROM financeiro_movimentacoes
                   WHERE trabalhador_id=%s AND categoria='mensalidade' ORDER BY data_movimentacao""",
                (trabalhador_id,),
            ).fetchall()

        self.assertEqual(len(linhas), 13)
        self.assertEqual(len({str(x["data_movimentacao"])[:7] for x in linhas}), 13)
        self.assertTrue(all(x["status"] == "pendente" and x["conta_id"] for x in linhas))

    def test_recebimento_baixa_apenas_parcela_pendente_e_audita(self):
        from rotas.financeiro_mensalidades import sincronizar_mensalidades

        trabalhador_id = self._criar_trabalhador(valor=80)
        with self.conectar() as conn:
            trab = conn.execute("SELECT * FROM trabalhadores WHERE id=%s", (trabalhador_id,)).fetchone()
            sincronizar_mensalidades(conn, dict(trab))
            mov_id = conn.execute(
                """SELECT id FROM financeiro_movimentacoes WHERE trabalhador_id=%s
                   AND categoria='mensalidade' AND status='pendente' ORDER BY id LIMIT 1""",
                (trabalhador_id,),
            ).fetchone()["id"]

        original_guard = self.financeiro._guard
        self.financeiro._guard = lambda request: ({"id": self.atendente_id}, None)
        self.addCleanup(setattr, self.financeiro, "_guard", original_guard)
        resposta = asyncio.run(self.financeiro.receber_mensalidades(
            None, trabalhador_id=trabalhador_id, mov_ids=str(mov_id), forma_pagamento="pix",
            pix_copiadecola="",
        ))
        self.assertEqual(resposta.status_code, 303)

        with self.conectar() as conn:
            mov = conn.execute("SELECT status, forma_pagamento, data_pagamento FROM financeiro_movimentacoes WHERE id=%s", (mov_id,)).fetchone()
            auditoria = conn.execute(
                """SELECT detalhes FROM auditoria_eventos
                   WHERE atendente_id=%s AND evento='mensalidades_recebidas'""",
                (self.atendente_id,),
            ).fetchone()
        self.assertEqual(mov["status"], "pago")
        self.assertEqual(mov["forma_pagamento"], "pix")
        self.assertTrue(mov["data_pagamento"])
        self.assertIn(str(mov_id), auditoria["detalhes"])

    def test_pdv_ignora_preco_enviado_e_cancelamento_restitui_estoque(self):
        with self.conectar() as conn:
            caixa_id = conn.execute("SELECT id FROM caixas WHERE nome='Lanchonete'").fetchone()["id"]
            cur = conn.execute(
                """INSERT INTO caixa_movimentos (caixa_id, saldo_inicial, status, atendente_id_abertura)
                   VALUES (%s, 0, 'aberto', %s) RETURNING id""",
                (caixa_id, self.atendente_id),
            )
            movimento_id = cur.fetchone()["id"]
            cur = conn.execute(
                """INSERT INTO produtos (nome, preco_venda, quantidade_estoque, caixa_id, ativo)
                   VALUES (%s, 12.50, 4, %s, 1) RETURNING id""",
                (f"{self.prefixo} produto", caixa_id),
            )
            produto_id = cur.fetchone()["id"]
        self.addCleanup(self._remover_cenario_pdv, produto_id, movimento_id)

        original_guard = self.caixa._guard
        self.caixa._guard = lambda request: ({"id": self.atendente_id}, None)
        self.addCleanup(setattr, self.caixa, "_guard", original_guard)
        resposta = asyncio.run(self.caixa.finalizar_venda(
            None, caixa_id=caixa_id, caixa_movimento_id=movimento_id, forma_pagamento="especie",
            valor_recebido="25.00", itens_json=json.dumps([{
                "produto_id": produto_id, "nome": "preço adulterado", "quantidade": 2,
                "preco_unitario": 0.01, "subtotal": 0.02,
            }]), trabalhador_id=0, doacao_valor="0", imprimir="0",
        ))
        self.assertEqual(resposta.status_code, 303)

        with self.conectar() as conn:
            venda = conn.execute("SELECT id, total FROM vendas_pdv WHERE caixa_movimento_id=%s ORDER BY id DESC LIMIT 1", (movimento_id,)).fetchone()
            produto = conn.execute("SELECT quantidade_estoque FROM produtos WHERE id=%s", (produto_id,)).fetchone()
        self.assertEqual(float(venda["total"]), 25.00)
        self.assertEqual(produto["quantidade_estoque"], 2)

        asyncio.run(self.caixa.cancelar_venda(None, venda["id"]))
        with self.conectar() as conn:
            produto = conn.execute("SELECT quantidade_estoque FROM produtos WHERE id=%s", (produto_id,)).fetchone()
            movimento = conn.execute("SELECT total_vendas FROM caixa_movimentos WHERE id=%s", (movimento_id,)).fetchone()
            venda = conn.execute("SELECT status FROM vendas_pdv WHERE id=%s", (venda["id"],)).fetchone()
        self.assertEqual(produto["quantidade_estoque"], 4)
        self.assertEqual(float(movimento["total_vendas"]), 0.0)
        self.assertEqual(venda["status"], "cancelada")

    def _remover_cenario_pdv(self, produto_id, movimento_id):
        with self.conectar() as conn:
            vendas = conn.execute("SELECT id FROM vendas_pdv WHERE caixa_movimento_id=%s", (movimento_id,)).fetchall()
            for venda in vendas:
                conn.execute("DELETE FROM vendas_pdv_itens WHERE venda_id=%s", (venda["id"],))
                conn.execute("DELETE FROM auditoria_eventos WHERE entidade='venda_pdv' AND entidade_id=%s", (venda["id"],))
            conn.execute("DELETE FROM vendas_pdv WHERE caixa_movimento_id=%s", (movimento_id,))
            conn.execute("DELETE FROM estoque_movimentos WHERE produto_id=%s", (produto_id,))
            conn.execute("DELETE FROM produtos WHERE id=%s", (produto_id,))
            conn.execute("DELETE FROM caixa_movimentos WHERE id=%s", (movimento_id,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
