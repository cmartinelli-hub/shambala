# TASK_014 — Correções da auditoria financeira

Ordem: `docs/tasks/TASK_014_CORRECOES_AUDITORIA_FINANCEIRO.md`.
Referência auditada: TASK_013 (`docs/investigacoes/TASK_013_AUDITORIA_FINANCEIRO_ANTES_HOMOLOGACAO.md`), commit `f7ef016`.
Data: 2026-10-25.

**Status: em execução.** AUD-01 corrigido e provado; AUD-02 e §3 em andamento.

---

## 1. AUD-01 — isolamento de chave por operador e operação

### Achado → causa → arquivo/função

`operacao_idempotencia` guardava `autor_id`, mas as duas funções que decidem
devolver um resultado persistido conferiam **apenas a chave**:

- `servico_idempotencia.reconhecer()` — `SELECT ... WHERE chave = %s`, sem
  comparar `operacao` nem `autor_id`;
- `servico_idempotencia.reservar()` — mesma coisa na corrida, e devolvia
  `REPETIDA` com `destino`/`documento` de quem chegou primeiro;
- `exigir_reserva()` só repassava o autor ao `INSERT`, nunca o comparava.

### Prova do defeito ANTES da correção

Contra app descartável real (porta 9018, banco `shambala_test_passes`, token
`t014-aud01`), dois atendentes distintos com permissão de escrita
(`evidencias/TASK_014/aud01_antes_da_correcao.txt`):

```
A envia: (303, '/financeiro')     → 1 lançamento, tentativa com autor_id=4 (A)
B REENVIA a mesma chave e o mesmo corpo: (303, '/financeiro')
  lançamentos depois: 1   (B não criou efeito: recebeu a resposta de A)
```

O segundo operador recebeu o destino/documento do primeiro. Chave não é
credencial — e a entropia não corrige reutilização por tela, log, suporte ou
dispositivo compartilhado.

### Correção

`servico_idempotencia.py`:

- **`_contexto_confere(registro, operacao, autor_id)`** — compara a operação da
  rota/serviço e o autor autenticado. Registro com `autor_id` nulo ou operação
  vazia **não** confere: recusa segura, sem atribuir autoria retroativa.
- **`reconhecer(..., operacao, autor_id)`** — confere o contexto **antes** de
  qualquer resposta persistida; se não bater, levanta `TentativaRecusada`
  (`tentativa_contexto`) sem revelar destino, documento, autor ou conteúdo.
- **`reservar()`** — a corrida na reserva também confere: contexto divergente
  devolve um desfecho próprio **`CONTEXTO`** (antes caía em `CONFLITO`, e a
  mensagem dizia "conteúdo divergente" quando o problema era outro operador).
- **`exigir_reserva()`** — `autor_id` passou a ser **obrigatório**: sem ele não
  há como distinguir "o dono repetindo" de "outro atendente com a chave", e a
  função prefere recusar.
- **`rotas/tentativa.resposta_de_tentativa(..., operacao, autor_id)`** — as nove
  rotas que reconhecem passaram a informar o contexto.

Unicidade global da chave preservada: a colisão de contexto **não** vira operação
nova silenciosa sob a mesma chave — é recusa.

### Matriz de rotas (autor/operação)

`evidencias/TASK_014/aud01_matriz_rotas.md` — gerada a partir do código, não à
mão. Onze rotas financeiras protegidas da TASK_012, todas com `autor_id` e com a
**mesma operação** no reconhecimento e na reserva:

| rota | operação | transições |
|---|---|---|
| `biblioteca.vender_livro` | `biblioteca_venda` | reconhece, reserva |
| `caixa.adicionar_credito_fiado` | `credito_anticipado` | reconhece, reserva |
| `caixa.finalizar_venda` | `pdv_venda` | reconhece, reserva |
| `caixa.pagar_divida_fiado` | `fiado_pagar_divida` | reconhece, reserva |
| `caixa.pagar_fiado` | `fiado_quitar_venda` | reconhece, reserva |
| `caixa.registrar_doacao` | `doacao_avulsa` | reconhece, reserva |
| `caixa.salvar_sangria` | `sangria` | reconhece, reserva |
| `compras.salvar_compra` | `compra` | reconhece, reserva |
| `financeiro.receber_mensalidades` | `mensalidade_receber` | reconhece, reserva |
| `financeiro.salvar_nova` | `lancamento_novo` | reserva |
| `financeiro.salvar_transferencia` | `transferencia` | reserva |

Duas rotas só reservam (`salvar_nova`, `salvar_transferencia`): não têm caminho
alternativo de reconhecimento, e a conferência acontece no `reservar`.

**Defeito encontrado ao montar a matriz** (e corrigido): duas rotas de `caixa.py`
ficaram com a operação trocada na primeira passada — `pagar_fiado` recebia
`credito_anticipado`. Como o reconhecimento passou a comparar a operação, isso
fez três asserções antigas falharem (`quitar venda de fiado`): o próprio
endurecimento detectou o mapeamento errado. Hoje a verificação é programática
(cada rota reconhece a MESMA operação que reserva).

### Provas DEPOIS da correção

`evidencias/TASK_014/aud01_depois_da_correcao.txt` — suíte
`test_financeiro_idempotencia_task012.py`, **182/182**:

| Prova | Resultado |
|---|---|
| A conclui e a tentativa fica com o autor dele | ok |
| B, com a mesma chave e o mesmo corpo, é recusado por contexto — sem destino e sem documento alheio | ok |
| Com destino específico (transferência), o segundo operador recebe a recusa e **não** o destino gravado do primeiro | ok |
| A recusa de B não cria efeito e não altera a tentativa do A | ok |
| A recusa não revela o operador original no corpo | ok |
| A continua podendo repetir a própria tentativa (mesmo resultado) | ok |
| Mesma chave em **outra operação**, mesmo operador: recusa sem escrita, corpo estruturalmente parecido | ok |
| Tentativa histórica com autor nulo: recusada, sem atribuir autoria retroativa e sem apagar identidade | ok |
| Concorrência (chave **nova**) entre dois contextos: um vencedor, o outro recusado | ok |
| Concorrência (chave **já concluída**): só o dono recebe o resultado; nada duplicado | ok |

Duas asserções foram endurecidas durante a prova, não afrouxadas: comparar o
`loc` com o destino do dono era fraco porque `/financeiro` é prefixo de
`/financeiro/nova` — o teste passou a usar uma operação com destino específico
(transferência) e a exigir a recusa pelo nome.

### Notas de implementação

1. **Registros históricos.** O DEV tem, hoje, **zero** tentativas de compra e as
   demais são desta e da execução anterior de testes; nenhuma com `autor_id`
   nulo em uso real (verificado antes de decidir). A regra escolhida —
   contexto nulo **recusa** — é a conservadora: não inventa autoria retroativa
   nem apaga identidade.
2. **Sem schema novo.** Todos os campos usados (`operacao`, `autor_id`) já
   existiam desde a TASK_012; nenhuma migração de identidade.
3. **Efeito colateral esperado.** Uma tela que guarde a chave e seja usada por
   outro operador agora recebe `tentativa_contexto` com orientação de recarregar
   e refazer — comportamento desejado, e a mensagem está em
   `MENSAGENS_TENTATIVA`.
4. **Revisão de preço do PDV** preservada: a comparação de contexto acontece no
   reconhecimento/reserva, antes das validações de estado; o caminho de
   reprificação não mudou (a suíte de preços continua verde).

---
