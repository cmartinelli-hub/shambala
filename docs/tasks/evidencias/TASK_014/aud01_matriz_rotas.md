# AUD-01 — matriz de rotas protegidas (autor/operação)

| arquivo | rota | operação | transições | autor_id |
|---|---|---|---|---|
| `rotas/biblioteca.py` | `vender_livro` | `biblioteca_venda` | reconhece, reserva | sim |
| `rotas/caixa.py` | `adicionar_credito_fiado` | `credito_anticipado` | reconhece, reserva | sim |
| `rotas/caixa.py` | `finalizar_venda` | `pdv_venda` | reconhece, reserva | sim |
| `rotas/caixa.py` | `pagar_divida_fiado` | `fiado_pagar_divida` | reconhece, reserva | sim |
| `rotas/caixa.py` | `pagar_fiado` | `fiado_quitar_venda` | reconhece, reserva | sim |
| `rotas/caixa.py` | `registrar_doacao` | `doacao_avulsa` | reconhece, reserva | sim |
| `rotas/caixa.py` | `salvar_sangria` | `sangria` | reconhece, reserva | sim |
| `rotas/compras.py` | `salvar_compra` | `compra` | reconhece, reserva | sim |
| `rotas/financeiro.py` | `receber_mensalidades` | `mensalidade_receber` | reconhece, reserva | sim |
| `rotas/financeiro.py` | `salvar_nova` | `lancamento_novo` | reserva | sim |
| `rotas/financeiro.py` | `salvar_transferencia` | `transferencia` | reserva | sim |
