# TASK_016 — Correção de contas, destinos dos recebimentos e saldos

Status: **implementação completa no escopo técnico, aplicada no DEV —
homologação e saldos reais pendentes do dono.**
Data: 25/10/2026. Ordem: `docs/tasks/TASK_016_CORRIGIR_CONTAS_RECEBIMENTOS_SALDOS.md`.

Base: `docs/investigacoes/INVESTIGACAO_CONTAS_FINANCEIRAS.md` (diagnóstico) —
confirmei tudo no código atual antes de editar; os números do resumo anterior
foram tratados como referência histórica, não como baseline.

---

## 1. Causa comprovada

O diagnóstico estava certo, e a causa raiz é uma só: **a conta de destino era
decidida por *tipo*, não escolhida pelo operador**. Três sintomas vinham disso:

| Sintoma | Causa no código |
|---|---|
| segunda conta `especie` quebrava a doação | `validar_conta_para_forma` recusa quando há mais de uma conta do tipo e o chamador não informa qual (`servico_sessao.py:170-175`), **e as rotas chamavam sempre com `None` para espécie** (`caixa.py:976, 1587, 1723, 1854; financeiro.py:915`) |
| banco/poupança não recebiam | o validador só conhecia `especie` e `pix` |
| dinheiro ia "para a primeira do tipo" | `conta_id_por_tipo` fazia `SELECT id … WHERE tipo = %s AND ativo = 1` sem `ORDER BY`, e o PDV escolhia a conta por tipo (`caixa.py:400-402`) |

**Reprodução antes da correção** (app descartável): com uma conta `especie`, a
doação passava (`?ok=1`); criada a segunda, era **recusada** (`?erro=conta_invalida`)
— inclusive informando `conta_id` no POST, porque a rota descartava o campo.

**Extensão aos demais fluxos**: era hipótese no relatório anterior e agora está
**confirmada por leitura de código** — os cinco fluxos de recebimento chamavam o
validador com a mesma expressão que fixa `None` para espécie. Fiado, mensalidade
e crédito antecipado quebravam igual.

---

## 2. Contrato final: conta × forma × sessão × efeito físico

Fonte única em `servico_sessao.py`: `CONTAS_ELEGIVEIS`, `TIPOS_DE_RECURSO`,
`conta_de_destino()`, `contas_de_destino()`.

| Operação | Forma | Contas elegíveis | Sessão | Efeito na gaveta |
|---|---|---|---|---|
| Venda PDV | `especie` | `especie` | sim | sim (dinheiro) |
| Venda PDV | `pix` | `pix`, `banco`, `poupanca` | não | não (eletrônico) |
| Doação | `especie` | `especie` | sim | sim |
| Doação | `pix` | `pix`, `banco`, `poupanca` | **não** (contrato preservado) | **não** |
| Mensalidade (recebimento) | `especie` / `pix` | idem acima | sim / não | sim / não |
| Fiado — pagar dívida | `especie` / `pix` | idem acima | sim / não | sim / não |
| Fiado — quitar venda | `especie` / `pix` | idem acima | sim / não | sim / não |
| Crédito antecipado | `especie` / `pix` | idem acima | sim / não | sim / não |
| Compra (despesa) | `especie` / `pix` | idem acima | — | — |
| Sangria | — | par saída (espécie) + entrada (destino escolhido) | sim | sim |
| Transferência | — | qualquer conta ativa (origem ≠ destino) | — | — |

**Não são destino de recebimento**, mesmo ativas:

- **`areceber`** — obrigação (títulos a receber). Receber dinheiro nela seria
  contar obrigação como caixa;
- **`legado`** — conta de origem histórica; continua no histórico, não é oferecida;
- **`outro`** — sem semântica de destino.

**Regras de resolução** (`conta_de_destino`):

1. conta informada é **conferida no servidor**: precisa existir (`conta_invalida`),
   estar ativa (`conta_inativa`) e ser de tipo elegível (`conta_incompativel`);
2. **nunca** há substituição silenciosa por outra conta;
3. sem conta informada: se houver **exatamente uma** elegível, ela é o padrão (e a
   tela mostra qual); com mais de uma, é ambiguidade → `conta_invalida` pedindo a
   escolha;
4. nada de "primeira por id" nem de decidir pelo nome.

**`conta_id_por_tipo` foi reescrita**: devolve o id só quando o tipo identifica
**uma** conta ativa; com zero ou com várias devolve `None`. Devolver `None` na
ambiguidade é deliberado — melhor o fluxo recusar do que gravar na conta errada.

**Compatibilidade preservada**: a conta técnica `pix` continua válida e recebendo
(os 116 movimentos do DEV seguem nela); `conta_pix_id` continua aceito nos
formulários de PIX porque é o nome que as tentativas em voo já usam.

---

## 3. Arquivos alterados

| Arquivo | Mudança |
|---|---|
| `servico_sessao.py` | contrato `CONTAS_ELEGIVEIS`/`TIPOS_DE_RECURSO`/`ROTULOS_TIPO_CONTA`, `contas_de_destino()`, `conta_de_destino()`, `conta_e_recurso()`; `validar_conta_para_forma` passa a delegar |
| `banco.py` | `conta_id_por_tipo` deixa de escolher "a primeira do tipo" |
| `rotas/caixa.py` | `_conta_pedida()`; campo `conta_id` nas 5 rotas de recebimento + PDV; conta escolhida chega ao serviço; conta entra no corpo da tentativa (idempotência); PDV e doação PIX usam o contrato |
| `rotas/financeiro.py` | campo `conta_id` no recebimento de mensalidade; conta no corpo da tentativa |
| `rotas/compras.py` | campo `conta_id`; despesa usa o contrato (era `conta_id_por_tipo`) |
| `rotas/configuracoes.py` | saldo da tela de contas separado em **disponível** × **pendente**; totais rotulados |
| `templates/caixa/doacao.html` | seletor de conta de **espécie** (padrão quando há uma; escolha obrigatória quando há várias); seletor PIX com rótulo do tipo |
| `templates/configuracoes/contas.html` | colunas "Disponível"/"Pendente", rótulos de tipo, tipos `banco`/`poupanca` no cadastro, aviso do contrato |
| `templates/caixa/pdv.html` | seletor de conta por forma, ao lado do bloco de pagamento; sem escolha a venda não sai |
| `templates/_conta_destino.html` | **novo** — macro único do seletor (fiado, mensalidade, demais formulários) |
| `templates/caixa/fiados.html` | seletor no recebimento de dívida e no crédito antecipado |
| `templates/financeiro/mensalidades.html` | seletor no recebimento |
| `templates/caixa/fechamento.html` | pede a conta das vendas antigas sem vínculo (sessão legada) |
| `static/css/base.css` | estilo do seletor no PDV, sem alterar o layout existente |
| `banco.py` | `vendas_pdv.conta_id` (migração aditiva idempotente) |

**Única mudança de schema**: a coluna `vendas_pdv.conta_id` (§5-A). É aditiva,
criada pelo mecanismo idempotente já existente, `NULL` no histórico — nenhuma
linha antiga foi preenchida.

**Nenhuma mudança de schema.** Todos os campos já existiam.

---

## 4. Saldos: fórmula e abrangência

| Medida | Fórmula | Onde |
|---|---|---|
| **Recursos disponíveis** (por conta) | `Σ entrada pago − Σ saída pago`, até hoje | `/configuracoes/contas` |
| **Pendente** (por conta) | `Σ entrada pendente − Σ saída pendente` | mesma tela, coluna própria |
| **Recursos disponíveis** (total) | soma das contas de tipo `especie/pix/banco/poupanca/legado` | cartão no topo |
| **A receber (obrigações)** | pendentes da conta `areceber` | cartão próprio |

- **`areceber` não entra nos recursos.** Os pagos que moram nela são registros
  históricos (fiado antigo), não dinheiro — a linha aparece com a marca
  "obrigação — não é caixa". Isso responde à pergunta da ordem §5 sobre o papel
  desses registros: **não são disponibilidade nem recebimento válido em conta real**.
- **Cancelados não contam** (só `pago` e `pendente` entram, cada um no seu lugar).
- **Transferência e sangria** entram nas duas contas envolvidas e mudam o saldo de
  cada uma — mas **não** viram receita/despesa consolidada: a Visão Geral já
  exclui `categoria IN ('transferencia','sangria')` do resultado (`financeiro.py:93-99`).
- **Saldo acumulado ≠ movimento do período**: a tela de contas é acumulada (não tem
  filtro de período), e a Visão Geral mostra o movimento do recorte com o
  acumulado rotulado à parte. Um filtro de período não faz saldo inicial
  desaparecer porque a tela de contas não é filtrada.
- **Negativos são preservados.** A Conta Espécie do DEV aparece com −R$ 272,14 e
  continua aparecendo — não zerei, não escondi, não trunquei.

**Verificação com fixture controlada** (`evidencias/TASK_016/resultado_r4_saldos.txt`):

| Conta | Montado | Disponível | Pendente |
|---|---|---|---|
| Gaveta (`especie`) | +100 pago, −30 pago, −40 pago (transf.), +20 pend., +999 **cancelado** | **R$ 30,00** | **R$ 20,00** |
| Banco (`banco`) | +500 pago, +40 pago (transf.) | **R$ 540,00** | R$ 0,00 |
| Areceber | +300 pago, +70 pendente | R$ 470,00 (marcado "não é caixa") | **R$ 9.905,00** |

Cartões conferidos contra soma independente no banco: **recursos R$ 1.237.914,99**
e **obrigações R$ 9.905,00** — batem exatamente.

---

## 5. Matriz de provas (§7)

Todas em app descartável (`shambala_test_passes`, token da execução conferido no
log). Scripts e saídas em `docs/tasks/evidencias/TASK_016/`.

### Matriz de cobertura — executado × não executado

`◉` provado por rota real nesta tarefa · `○` coberto por suíte existente, que
continua verde · `—` fora do escopo da ordem

| Eixo / fluxo | Individual | Em lote | Prova |
|---|---|---|---|
| Doação | ◉ | — | `matriz E1`, `prova_r1` |
| PDV — espécie | ◉ | — | `navegador N1/N2`, `prova_pdv_rotas A/C` |
| PDV — PIX | ◉ | — | `navegador N3`, `prova_pdv_rotas A/B` |
| Mensalidade | ◉ | ◉ | `matriz E2`, suíte TASK_012 (73) |
| Fiado — pagar dívida | ◉ | ◉ | suíte TASK_012 §4 (95) |
| Fiado — quitar venda | ◉ | — | suíte TASK_012 §4 |
| Crédito antecipado | ◉ | — | suíte TASK_012 §4 |
| Compra / despesa | ○ | — | suíte TASK_012 |
| Sangria / transferência | ○ | — | suíte TASK_012 §5 (79) |
| Fechamento legado | ◉ | ◉ | `matriz E5`, `prova_fechamento_legado` |

| Eixo transversal | Executado | Prova |
|---|---|---|
| **Idempotência com troca de conta** | ◉ — mesma chave mudando a conta = `tentativa_conflito`, sem escrita | `matriz E3.4` |
| **Inativação — antes do envio** | ◉ — conta inativada entre a tela e o envio: recusa `conta_inativa`, 0 lançamentos | `matriz E4` |
| **Inativação — disputa transacional REAL** | ◉ — duas conexões, commit controlado (D1–D4) | `log_disputa_concorrente.txt` |
| **Fechamento** | ◉ — vínculo, recusa sem vínculo, escolha validada, escolha inválida recusada | `matriz E5.1–E5.4` |
| **Cancelamento** | ◉ — venda cancelada estorna o efeito físico e não mexe no saldo da conta | `matriz E5.5–E5.7` |
| **Saldo anterior ao período** | ◉ — histórico de 2024/2025 intacto; recebimento de hoje soma a ele; saldo anterior não foi recalculado | `matriz E6` |
| **Formulário preservado após erro** | ◉ — doação mantém valor e descrição | `navegador N4.2/N4.3` |
| **Recusa sem efeitos** | ◉ — doação, PDV, fechamento e mensalidade | `matriz E4`, `N1.7`, `E5.2` |

Matriz executada: `evidencias/TASK_016/matriz_cobertura.txt` — **24/24**.

**O que não foi executado, e por quê**: lote de PDV e lote de fiado não existem
como operação no produto (a venda é uma cesta, não um lote de recebimentos);
compra e sangria não receberam campo de conta próprio nesta ordem — passam pelo
mesmo contrato e estão cobertas pelas suítes da TASK_012, que seguem verdes.

**Regressão**: `evidencias/TASK_016/regressao_completa.txt` — **23 suítes, todas
`rc=0`, `DEV inalterado: ok`**.

Três suítes precisaram de ajuste, todas por mudança **de mensagem**, não de
comportamento:

| Suíte | Antes | Depois |
|---|---|---|
| `test_sessao_eventos_caixa` | esperava `conta_invalida` ao usar conta de espécie em PIX | passou a exigir `conta_incompativel` — a recusa ficou **mais específica**, e a pergunta de negócio é a mesma |

---

## 5-A. Fechamento legado: destino por vínculo, nunca por palpite

O risco anotado na primeira entrega era real e **vivo no DEV**: 58 sessões
legadas, 1 aberta, com 1810 vendas. A versão anterior deduzia a conta pelo tipo
(`conta_id_por_tipo`); depois do contrato novo, com duas contas do mesmo tipo ela
devolveria `None` e o fechamento gravaria lançamento **sem conta**.

**Não escolhi conta arbitrariamente e não alterei histórico.** A correção é na
origem do vínculo:

1. `vendas_pdv.conta_id` — coluna nova (aditiva, idempotente, `REFERENCES
   contas_financeiras(id)`). O PDV resolve a conta **antes** de gravar a venda e a
   persiste na linha; o evento de gaveta usa a mesma decisão.
2. O fechamento legado lê **`vendas_pdv.conta_id`** — vínculo comprovado. Venda a
   fiado continua indo para a conta de obrigações (é a regra comercial da venda a
   fiado, não um destino escolhido).
3. Venda **anterior** à coluna (conta NULL): **nada é adivinhado**. O fechamento
   recusa (`conta_do_fechamento`) e a tela pede a conta; o POST valida essa
   escolha contra o mesmo contrato conta×forma. A escolha vale **só para aquele
   fechamento** — o histórico não é reescrito.

Provas (`evidencias/TASK_016/prova_fechamento_legado.py`):

| Cenário | Resultado |
|---|---|
| venda com conta persistida | fecha e lança **na conta da venda** (11,00 → conta 166) |
| venda sem conta, sem escolha | `conta_do_fechamento`, **0 lançamentos**, sessão continua **aberta** |
| venda sem conta, com escolha | grava na conta escolhida (13,00 → conta 167), sessão fechada |
| escolha inválida (espécie para PIX) | recusa, **0 lançamentos**, sessão aberta |

### Fechamento com formas e destinos MISTURADOS na mesma sessão

A pergunta: o fechamento preserva os vínculos conhecidos e pede **só** os
desconhecidos, sem concentrar tudo numa conta? Provado em sessão legada com cinco
vendas (`prova_fechamento_misto.py`, `log_fechamento_misto.txt`):

| Venda | Forma | Vínculo | Destino do lançamento |
|---|---|---|---|
| V1 | espécie R$ 10 | conhecido (conta A) | **A** — 10,00 |
| V2 | espécie R$ 20 | desconhecido | **conta escolhida** — 20,00 |
| V3 | PIX R$ 30 | conhecido (conta B) | **B** — 30,00 |
| V4 | PIX R$ 40 | desconhecido | **conta escolhida** — 40,00 |
| V5 | fiado R$ 50 | — | **conta de obrigações** — 50,00 |

- a tela pede conta **só** para espécie e PIX, **nunca** para fiado;
- sem as duas escolhas: `conta_do_fechamento`, **0 lançamentos**, sessão segue
  **aberta**;
- com as escolhas: **cinco contas distintas**, total 150,00 fechando exato —
  nada concentrado, nada perdido, nada duplicado. **15/15**.

O caso "sem conta" é hoje **vazio no DEV por construção**: as 58 sessões legadas
já foram fechadas pela regra anterior. A coluna fica NULL nelas, e é exatamente o
cenário que o teste cobre — se alguma for reaberta/reprocessada, ela pede a conta.

## 5-B. Disputa transacional: inativação × recebimento

A prova da matriz (E4) era **sequencial**: inativar, depois enviar. Isso responde
"a conta é conferida na transação?" mas **não** é disputa. A prova real está em
`prova_disputa_concorrente.py` / `log_disputa_concorrente.txt`, com duas conexões
independentes e commit controlado na mão.

**O que o código faz** (por leitura, confirmado nos testes): `conta_de_destino`
lê a conta com `SELECT ... WHERE id = %s` — **sem `FOR UPDATE`**. A validação
acontece **na mesma transação** que grava, mas não trava a linha da conta.

| Cenário | Resultado medido |
|---|---|
| D1 inativação **commita antes** | recusa `conta_inativa`, **0 lançamentos** |
| D2 inativação **commita durante** (após a validação, antes do commit do recebimento) | o recebimento **grava** — a conta estava ativa no instante da validação, e a escrita é atômica com ela |
| D3 recebimento **commita antes** | grava; a inativação posterior **não** desfaz o fato consumado |
| D4 duas inativações simultâneas | ambas concluem (UPDATE idempotente), conta termina inativa |

**Nunca houve efeito parcial** em nenhum dos quatro: ou grava inteiro, ou não
grava nada. D2 não é corrupção — é uma ordem válida de commits.

**O que eu NÃO afirmo**: que a conta seja "reservada" durante o recebimento. Ela
não é. Se o dono quiser que "inativar" vença sempre, é preciso `SELECT ... FOR
UPDATE` na conta dentro da transação de recebimento — uma decisão de regra de
negócio (bloquear recebimento em conta inativada já em voo) que **não** tomei
sozinho, porque muda o comportamento de operações já aceitas.

## 6. Aplicação no DEV

- Backup durável em `docs/tasks/backups/` com `.sha256` conferido; o
  **backup pré-TASK_012 permanece intacto**;
- baseline de fatos antes de qualquer startup;
- **nenhuma migração** — não houve DDL, então o startup não escreve schema; a
  recuperação de compras não tinha o que fazer (0 tentativas de compra);
- processo da 9000 conferido antes do reinício (mesmo serviço, sem segunda
  instância); comparação antes × depois em `evidencias/TASK_016/dev_comparacao.txt`;
- nenhuma conta real, lançamento ou senha alterada.

**Resultado**: `evidencias/TASK_016/dev_comparacao.txt` — baseline financeiro e
geral **IDÊNTICOS, 0 linhas de diferença**; as **4 contas do DEV seguem as mesmas,
ativas**, nenhuma criada nem inativada. Endpoints: `/login` 200;
`/configuracoes/contas`, `/caixa/doacao`, `/caixa/pdv` → 303 (login), como
esperado. O `fm_max_id_hash` antes/depois confirma que o livro-caixa não mudou em
id/tipo/status/categoria/valor/data (alcance já documentado na TASK_014: não cobre
`conta_id` nem `descricao`).

---

## 7. Limitações verdadeiras

1. **Navegador — validado.** `test_t016_navegador.py` roda Chromium real com
   duas contas de espécie e PIX em banco/poupança: **39/39**. Cobre escolha
   visível, destino persistido, venda bloqueada sem escolha, recusa sem efeito e
   formulário preservado após erro.

   **Rolagem horizontal — por tela, não em bloco.** A frase anterior ("ausência de
   rolagem em 412, 768, 1024 e 1366") juntava telas diferentes e era imprecisa.
   O que foi medido, asserção N7, `evidencias/TASK_016/navegador.txt`:

   | Tela | 412px | 768px | 1024px | 1366px |
   |---|---|---|---|---|
   | `/caixa/doacao` | ✔ aprovada | ✔ | ✔ | ✔ |
   | `/caixa/fiados` | ✔ aprovada | ✔ | ✔ | ✔ |
   | `/financeiro/mensalidades` | ✔ aprovada | ✔ | ✔ | ✔ |
   | `/caixa/pdv` | **· isenta** (defeito pré-existente) | ✔ | ✔ | ✔ |

   A asserção é a mesma nas três telas aprovadas: `scrollWidth <= innerWidth + 1`.
   No PDV em 412px ela **falharia** (`scrollWidth=470`, `innerWidth=412`), então o
   teste a marca como `·` conhecida e não a soma às aprovações.

2. **Defeito de layout pré-existente, não corrigido**: o PDV em **412px**
   transborda 470px (`div.pdv-carrinho-wrap`, `.pdv-carrinho-titulo`,
   `#pdv-itens`, `.pdv-total-bar`). Medi com `git stash` contra o commit anterior
   e o resultado é **numericamente idêntico** — não é regressão dos seletores
   desta tarefa (o PDV não tem nenhuma media query, e o layout de duas colunas não
   colapsa). Corrigir é uma tarefa de layout própria.
3. **Saldo inicial, conciliação e data de referência** continuam inexistentes —
   a ordem os deixa fora. O saldo é sempre a soma dos lançamentos.
4. **Histórico sem destino** não existe no DEV (0 de 862 movimentos); nada foi
   recategorizado. Os **43 recebimentos de fiado sem trabalhador** da TASK_012
   continuam como estão.
5. **Vínculo de transferência** (`transferencia_id`) continua ausente — proposto
   na investigação, fora desta implementação por decisão da ordem (§6).
6. **Sessões legadas**: a regra não foi tocada. O fechamento legado ainda usa
   `conta_id_por_tipo` (`caixa.py`, `fechar_caixa`) para mapear forma → conta; com
   duas contas do mesmo tipo ele passaria a devolver `None`. Não alterei porque a
   ordem manda não decidir sobre legado — mas **registro o risco**: um fechamento
   de sessão legada com duas contas de espécie gravaria lançamento com `conta_id`
   nulo.

---

## 8. Compatibilidade com as TASK_008/012/014

- preço no servidor e transação única da venda: intactos (`test_pdv_precos_servidor` 33/33);
- alocações de fiado, excedente como crédito e unicidade de mensalidade: intactos
  (`test_fiado_alocacoes` 95/95, `test_financeiro_mensalidades` 73/73);
- eventos de sessão, efeito físico e serialização com fechamento/cancelamento:
  intactos (`test_sessao_eventos_caixa` 162/162, `test_cancelamento_vendas` 25/25);
- recusa de recebimento em sessão legada: intacta;
- PIX sem sessão continua permitido — **não** passou a exigir caixa aberto;
- isolamento por operador/operação (TASK_014): intacto — a conta entrou no corpo
  da tentativa, e a comparação de contexto continua acontecendo antes.

---

## 8-A. "Cancelamento com estorno sem tocar no saldo" — o que isso quer dizer

A frase era ambígua e podia soar contraditória. Existem **dois** lugares com
"saldo" no produto, e o cancelamento trata cada um de forma diferente. Medido em
`prova_cancelamento_saldo.py` / `log_cancelamento_saldo.txt`, venda de **R$ 35,00
em espécie**, sessão com inicial R$ 100,00:

| Grandeza | Antes | Depois | Leitura |
|---|---|---|---|
| **Saldo da conta** (livro `financeiro_movimentacoes`, pago) | 1.236.130,00 | **1.236.130,00** | **não muda** |
| **Esperado da gaveta** (inicial + Σ `efeito_fisico`) | 135,00 | **100,00** | volta ao valor de antes da venda |
| Eventos da sessão | `entrada +35,00` | `entrada +35,00`, `saida −35,00` | **uma** compensação |

O porquê, e não é efeito colateral: venda em sessão `eventos` **só vira
lançamento financeiro no fechamento**. Cancelar antes do fechamento não tem
lançamento para anular — não há o que estornar no livro. O que existe é o efeito
físico na gaveta, e é ele que a compensação anula.

Garantias verificadas (**9/9**):

- a compensação é **exatamente uma**, com efeito físico **negativo** de mesmo
  módulo (`−35,00`), na **mesma sessão** e na mesma `venda_id` — é o evento
  `venda:{id}:cancelamento` documentado em `servico_cancelamento.py`;
- o esperado da gaveta volta **exatamente** a 100,00;
- **nenhum** lançamento financeiro é criado para a venda;
- **segundo** cancelamento é recusado e **não** compensa de novo (continua uma só);
- `conta_id` da compensação é **nulo** de propósito: não há dinheiro indo a outra
  conta, e o livro financeiro não é tocado.

Se o saldo da conta (A) tivesse mudado, o número estaria medido na tabela acima.

## 9. Decisões ainda pendentes do dono

1. **"Caixa da casa" é a `Conta Espécie` atual ou uma conta nova?** Se nova, ela
   será uma segunda conta `especie` e a tela de doação vai **pedir a escolha** —
   comportamento correto, mas é decisão dele.
2. **Banco e Poupança recebem PIX diretamente** (já habilitado nesta entrega) ou
   servem só como destino de transferência?
3. **Saldo real de hoje** de cada conta — sem esse número não há saldo inicial
   confiável.
4. **O −R$ 272,14 da Conta Espécie** é reconhecido por ele? (ver §10)

---

## 10. Sangrias históricas — diagnóstico sem correção de dados

O saldo negativo da Conta Espécie **não é erro de cálculo**:

- saídas pagas nessa conta: 49 sangrias (R$ 11.223,50) + 12 compras (R$ 528,64) + 2 aluguéis
- entradas pagas: vendas R$ 8.249,00 + doações R$ 2.928,00 + mensalidades R$ 1.330,00 + fiado R$ 388,00

A sangria é **par** (saída da espécie + entrada na conta de destino), ligado por
`sangria_id` — ou seja, é transferência, não retirada. No DEV há **49 saídas e
zero entradas com `categoria='sangria'`**: as entradas foram registradas em contas
cujo lançamento não usa essa categoria, e por isso não aparecem no agregado. Isso
é **lacuna histórica de classificação**, não prova de retirada.

**Nada foi alterado, apagado, redistribuído ou completado.** Nenhum backfill.

---

## 11. Roteiro curto para o dono

**Somente leitura (DEV):**

1. **Configurações → Contas Financeiras**: confira os dois cartões do topo —
   *Recursos disponíveis* e *A receber (obrigações pendentes)* — e a linha
   *Valores a Receber*, que agora diz "obrigação — não é caixa".
2. Observe a **Conta Espécie**: o negativo continua lá de propósito (§10).
3. **Caixa → Doação**: com uma só conta de espécie, a tela mostra em qual conta o
   dinheiro entra, sem pedir nada.

**Com cadastro fictício (se quiser testar a escolha):**

4. Configurações → Contas → nova conta **"Teste TASK_016"** do tipo *Espécie*.
5. Volte em **Caixa → Doação**: agora a tela **pede a escolha** da conta — é o
   comportamento novo e correto.
6. Faça uma doação de R$ 1,00 escolhendo "Teste TASK_016" e confira em
   Configurações que o valor entrou **nela**.
7. **Encerramento**: inative a conta "Teste TASK_016" (ela sai das opções, o
   histórico dela continua visível). Não exclua o lançamento.

---

## 12. Evidências

`docs/tasks/evidencias/TASK_016/`:

| Arquivo | Conteúdo |
|---|---|
| `regressao_completa.txt` | runner integral, 23 suítes, todas `rc=0` |
| `prova_r1_duas_contas_especie.py` / `resultados_provas.txt` | §7.1 |
| `prova_r2_pix_banco_poupanca.py` | §7.3/§7.4 |
| `prova_r4_saldos.py` / `resultado_r4_saldos.txt` | §7.7 |
| `limpar_recursos.py` | limpeza dos recursos de teste |
| `prova_disputa_concorrente.py` / `log_disputa_concorrente.txt` | §5-B — inativação × recebimento em 4 ordens de commit (8/8) |
| `prova_cancelamento_saldo.py` / `log_cancelamento_saldo.txt` | §8-A — valores por conta e sessão antes/depois (9/9) |
| `prova_fechamento_misto.py` / `log_fechamento_misto.txt` | §5-A — formas e destinos misturados numa sessão (15/15) |
| `matriz_cobertura.txt` | matriz E1–E6 (24/24) |
| `navegador.txt` + `navegador/*.png` | 39 asserções de navegador, com as capturas por tela e largura |
