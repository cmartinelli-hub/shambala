# TASK_016 — Correção de contas, destinos dos recebimentos e saldos

Status: **implementada e aplicada no DEV — homologação do dono pendente.**
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

| # | Prova | Resultado |
|---|---|---|
| 1 | segunda conta `especie`: sem escolha **recusa** (`conta_invalida`); escolhendo a nova grava **nela** (id 145); escolhendo a antiga grava **nela** (id 2) | **ok** |
| 2 | fiado com excedente escolhendo a conta nova: abatido R$ 40, **crédito R$ 10**, conta gravada = a escolhida | **ok** |
| 3 | PIX sem sessão em `banco` e `poupanca`: lançamento na conta certa, **nenhum evento de gaveta** | **ok** |
| 4 | `areceber` como destino → `conta_incompativel`, **0 lançamentos**; conta inativa → `conta_inativa`, **0 lançamentos** | **ok** |
| 5 | idempotência: mesma chave + mesma conta → 1 efeito; mesma chave + **conta diferente** → **`tentativa_conflito`**, sem efeito novo | **ok** |
| 7 | saldos com pagos/pendentes/cancelado/transferência: valores exatos por conta, pendências separadas, total sem duplicação | **ok** |
| 9 | navegador: ver §7 | parcial (ver limitações) |

**Provado por fluxo, não generalizado**: doação, fiado (com excedente) e PIX foram
exercitados por rota real. PDV, mensalidade, crédito e compras passaram pela
mesma função, o que a suíte de eventos (162 asserções) e a de preços (33)
confirmam no caminho — mas **não** montaram o cenário de duas contas de espécie
cada um.

**Regressão**: `evidencias/TASK_016/regressao_completa.txt` — **23 suítes, todas
`rc=0`, `DEV inalterado: ok`**.

Três suítes precisaram de ajuste, todas por mudança **de mensagem**, não de
comportamento:

| Suíte | Antes | Depois |
|---|---|---|
| `test_sessao_eventos_caixa` | esperava `conta_invalida` ao usar conta de espécie em PIX | passou a exigir `conta_incompativel` — a recusa ficou **mais específica**, e a pergunta de negócio é a mesma |

---

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

1. **Navegador não revalidado nesta tarefa.** A ordem pede (§7.9) seleção visível,
   troca de forma e erro preservando formulário em desktop e celular. Eu **não**
   executei a suíte de navegador contra um cenário de duas contas de espécie: a
   verificação foi de comportamento por HTTP e de renderização de template. A
   homologação visual é do dono.
2. **PDV não pergunta a conta quando há duas de espécie.** Nesta entrega o PDV usa
   o contrato, mas a tela não tem seletor: com duas contas de espécie ativas e sem
   escolha, a venda é **recusada** (`conta_invalida`) em vez de gravar na conta
   errada. É seguro, mas não é confortável — a tela do PDV ganhar o seletor é o
   próximo passo natural (fora do escopo desta ordem, que preserva o layout).
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
