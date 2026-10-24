# TASK_006 — PDV: preços e totais calculados no servidor

Status: concluída no DEV — implementação validada (32/32 na suíte nova; runner completo RC=0), aplicada no serviço existente da porta 9000 por reinício controlado com evidência; commits locais, sem push; produção intocada. Pendente apenas a conferência do dono pelo roteiro de homologação abaixo.
Referência: TASK_005_DIAGNOSTICO_FINANCEIRO, achado FIN-01; investigação registrada no commit 70249ee. Conferir o código atual antes de alterar.

## Objetivo

Eliminar a confiança em preço, subtotal e total enviados pelo navegador na finalização de vendas. O servidor deve consultar os produtos, validar quantidades e calcular os valores efetivamente persistidos e utilizados pelo PDV.

Esta é uma correção isolada de integridade. Não redesenhar o financeiro nem corrigir cancelamentos, fechamento de caixa, mensalidades ou pagamentos de fiado nesta tarefa. Não publicar em produção.

## Preparação

1. Ler AGENTS.md, se existir, e normas aplicáveis. Registrar esta TASK_006 em docs/tasks/, preservando alterações alheias.
2. Ler o diagnóstico da TASK_005 e conferir rotas, serviços, templates/JavaScript, schema e testes do PDV atuais. Identificar todos os caminhos que finalizam vendas e recebem valores do cliente.
3. Confirmar configuração efetiva do DEV e banco de teste, sem expor segredos. Não presumir que o processo executa a árvore local.
4. Usar o DEV existente; não criar outra instância persistente, túnel, domínio ou infraestrutura. Testes de escrita somente em PostgreSQL nativo descartável, sem Docker; encerrar recursos ao terminar.
5. Não acessar produção. Não alterar dados reais do DEV, credenciais ou permissões para demonstração. Se uma migração se mostrar indispensável, justificar antes e produzir backup recuperável antes de aplicá-la no DEV; preferir resolver sem mudança de schema.

## Reprodução inicial obrigatória

Antes de corrigir, reproduzir o FIN-01 por requisição ao fluxo real autenticado em banco descartável:

- Produto fictício com preço R$ 12,50 e quantidade 2.
- Enviar preço/subtotal adulterados para produzir R$ 0,02.
- Registrar requisição sanitizada, resposta e fatos persistidos, estoque, venda/itens e efeito na sessão.

Preservar a reprodução como teste de regressão: o estado anterior deve falhar e a implementação corrigida deve passar. Não depender da fixture financeira quebrada ou criar auditoria_eventos fictícia apenas para fazê-la executar.

## Regras de implementação

### Fonte dos valores

- Cliente informa produto e quantidade. Nome, preço, subtotal e total enviados não são autoridade para a escrita.
- Buscar no banco o produto e seu preço vigente na finalização. Gravar snapshot do nome/preço utilizado nos itens, preservando o modelo histórico existente.
- Calcular subtotal de cada item e total da venda no servidor. Usar o mesmo resultado para validações, gravações, crédito utilizado, novo fiado, limite, sessão e comprovante; não deixar algum ramo usando o total original do cliente.
- Não aceitar valor enviado em parâmetro alternativo, campo oculto, endpoint legado ou outro caminho de finalização.
- Preservar vendas antigas: não recalcular documentos históricos pelo preço atual.

### Valores monetários e quantidades

- Usar Decimal a partir de NUMERIC/texto ou centavos inteiros para o cálculo e a persistência deste fluxo. Não converter dinheiro para float no caminho corrigido.
- Declarar a regra de arredondamento, compatível com moeda de duas casas; em ausência de regra existente, usar ROUND_HALF_UP por subtotal e total como soma dos subtotais.
- Conferir a unidade de venda existente. Para produtos vendidos por unidade, exigir quantidade inteira positiva, sem truncar 1,5 para 1. Se o produto realmente suportar frações, preservar essa regra explícita e documentar precisão/limites. Não introduzir venda fracionada nova.
- Recusar quantidade zero, negativa, ausente, malformada, booleana, NaN/infinito e valores que excedam a capacidade dos campos NUMERIC. Recusar cesta vazia e produto inexistente/inativo.
- Produto com preço inválido/negativo impede a finalização. Não transformar preço ausente em zero. Preço zero só pode ser aceito se houver regra explícita existente para produto gratuito; caso contrário, recusar com orientação para corrigir o cadastro.
- Normalizar produtos repetidos na cesta para validar quantidade agregada e evitar contornar estoque/limites por linhas duplicadas. Preservar coerência entre itens, soma e baixa de estoque.
- Não alterar a política de estoque negativo nesta tarefa; conferir o comportamento atual, preservar a regra válida existente e impedir que a normalização crie um caminho alternativo que a contorne.

### Preço alterado durante a venda

O preço usado é o vigente consultado no servidor ao finalizar. Se a tela apresentou valores diferentes, não concluir silenciosamente cobrando outro valor: devolver o orçamento recalculado ou mensagem para revisar a cesta, sem gravar venda, estoque, crédito ou sessão. Na confirmação posterior, o servidor consulta e valida novamente.

Os preços apresentados pelo cliente podem servir apenas para detectar divergência; nunca para gravar. Requisição sem preços deve continuar sendo precificada pelo servidor, respeitando o contrato efetivo da interface. Não manter um caminho inseguro por compatibilidade.

Se já existir desconto, doação adicional, preço aberto ou ajuste autorizado, mapear separadamente: não apagar a funcionalidade nem aceitar esses valores como preço arbitrário de produto. Validar valor, permissão e regra real. Se não houver regra suficiente para preservar um desses fluxos, documentar o caso concreto e pedir somente a decisão necessária antes de implementá-lo.

### Transação e concorrência

- Validação completa antes dos efeitos; gravações em transação única. Qualquer falha reverte venda, itens, estoque, crédito e acumuladores alterados pelo fluxo.
- Consultar produtos/estoques com travas ou mecanismo equivalente compatível com o projeto, em ordem determinística para reduzir deadlocks; respeitar a concorrência existente com crédito/fiado e fechamento de sessão.
- Não alegar correção geral de duplicidade de venda ou fechamento se isso não foi implementado/testado. Idempotência completa de finalização fica fora do escopo, salvo mecanismo já existente que deva ser preservado.
- Manter autenticação e RBAC no servidor; sessão exclusiva do terminal de presença não pode finalizar venda.

## Interface

Manter o fluxo e a aparência atuais, alterando somente o necessário para que os valores exibidos e confirmados correspondam ao cálculo do servidor.

- Erros devem indicar item ou campo a revisar, sem traceback ou mensagem de sucesso.
- Em divergência de preço, preservar a cesta e permitir revisão antes de confirmar novamente.
- Após sucesso, comprovante e tela devem refletir o valor salvo, não a soma antiga do JavaScript.
- A correção não cria cobrança, baixa de mensalidade ou movimentação financeira adicional. Preservar os efeitos atuais não relacionados, registrando os defeitos ainda abertos da TASK_005.

## Testes e critérios de aceite

Nova suíte executável pelo runner, em PostgreSQL nativo descartável, com fixtures alinhadas ao schema real. Testar pelas rotas reais além dos testes de serviço.

| Cenário | Resultado exigido |
|---|---|
| 2 unidades de R$12,50 | Itens e venda somam R$25,00; efeitos usam esse valor |
| Preço/subtotal/total adulterados | Nenhuma venda pelo valor adulterado; divergência exige revisão sem escrita |
| Nome do produto adulterado | Snapshot usa o cadastro do servidor |
| Requisição sem campos de preço | Servidor calcula; não assume zero |
| Preço mudou após montar cesta | Nenhuma conclusão silenciosa; revisão e nova validação |
| Alteração concorrente de preço | Venda coerente com preço validado e regra de confirmação |
| Produto inexistente/inativo ou preço inválido | Recusa sem efeitos parciais |
| Quantidades inválidas e estouro monetário | Recusa sem 500 ou escrita |
| Produto duplicado na cesta | Quantidade agregada e estoque/totais consistentes |
| Cesta com vários itens e centavos | Total igual à soma dos subtotais persistidos |
| Falha ao gravar um item | Rollback de todos os efeitos da venda |
| Espécie, PIX e fiado suportados | Mesmo cálculo canônico, regras atuais preservadas |
| Crédito e limite de fiado | Usam o valor do servidor; adulteração não contorna limite |
| Comprovante | Valores iguais aos persistidos |
| Anônimo/sem permissão/sessão terminal | Finalização recusada |
| Venda histórica após mudar cadastro | Snapshot antigo permanece intacto |

Inspecionar valores antes/depois em vendas, itens, estoque, sessão e crédito. Não provar somente pela resposta HTTP. Usar teste determinístico para alteração concorrente, sem depender de sorte ou sleeps longos.

Executar o runner completo e apresentar resultados exatos. Executar a suíte financeira antiga separadamente quando viável; classificar as falhas remanescentes sem mascará-las. Não ampliar escopo para reparar todas as fixtures antigas nem declarar o financeiro homologado pela aprovação desta tarefa.

Verificar no navegador: venda normal, divergência de preço com cesta preservada, erro de quantidade e comprovante. Usar dados fictícios. Sem credenciais do DEV, validar visualmente em processo de teste temporário e registrar a limitação; não solicitar senha no chat nem criar conta no DEV.

## Fora do escopo

- Cancelamento/estorno e reparação retroativa das vendas existentes.
- Separação de vendas/recebimentos, fechamento de caixa e saldos.
- Modelagem de mensalidade, fiado, crédito e alocação de pagamento.
- Conciliação, importação, categorias, orçamento ou reescrita do financeiro.
- Novas formas de pagamento, descontos ou preço aberto.
- Publicação, push ou acesso à produção.

## Entrega

1. Implementação restrita ao FIN-01 e dependências necessárias.
2. Reprodução anterior e posterior com valores/fatos verificáveis; testes e comandos reproduzíveis.
3. Relatório com arquivos/rotas, contrato de finalização, regra monetária, travas e efeitos preservados.
4. Roteiro curto de homologação no DEV com trabalhador/produtos fictícios, sem alterar registros reais.
5. Resultados das suítes, limitações, defeitos financeiros ainda abertos e confirmação de preservação do DEV.
6. Atualizar SHAMBALA_CONTEXTO.md e esta tarefa, com status real. Commit local apenas dos arquivos da tarefa, sem push. Mover para docs/tasks/done/ após critérios atendidos.

Se for possível confirmar que o serviço DEV usa a árvore corrigida, disponibilizar pelo mesmo serviço existente mediante reinício controlado necessário, sem migrations/escritas inesperadas de startup. Conferir esses efeitos antes. Se não puder garantir isso no ambiente disponível, entregar a alteração validada e informar precisamente o que falta para disponibilizá-la; não afirmar aplicação no DEV sem evidência.

Encerrar processos/bancos descartáveis e registrar produção intocada. Não iniciar a próxima tarefa nem publicar esta correção sem autorização posterior.

---

## Adendo de entrega (2026-10-24)

### Arquivos e rotas alterados

| Arquivo | Mudança |
|---|---|
| `servico_pdv.py` | **novo** — autoridade de precificação: `parse_cesta`, `precificar`, `validar_moeda`; erros `CestaInvalida(chave, item)` e `DivergenciaPreco(linhas, total)`; constantes `CENTAVO`, `MAX_MOEDA=99 999 999,99`, `MAX_QUANTIDADE=1 000 000` |
| `rotas/caixa.py` | `POST /caixa/pdv/finalizar` reescrita: nada de preço/subtotal/total do navegador entra em escrita; mesmo cálculo alimenta validações, venda, itens, estoque, crédito/fiado, limite, sessão e comprovante |
| `templates/caixa/pdv.html` | banner de revisão quando `aviso_divergencia`; cesta reprificada re-embutida via `#pdv-cesta-seed` (JSON) e relançada no carrinho JS; dicionário de erros por chave+item (17 chaves), sem traceback |
| `tests/test_pdv_precos_servidor.py` | **novo** — 32 testes (ver abaixo), executado pelo runner |
| `tests/rodar_testes.sh` | suíte adicionada ao loop final |
| `SHAMBALA_CONTEXTO.md` | seção de finalização do PDV, tabelas caixas/produtos/vendas_pdv atualizadas, `servico_pdv.py` nos arquivos-chave |

Nenhuma mudança de schema: zero migração nova, zero DDL, zero dado real alterado.

### Contrato de finalização (efetivo)

`caixa_id`, `caixa_movimento_id`, `forma_pagamento` (especie/pix/fiado), `valor_recebido`, `doacao_valor`, `trabalhador_id`, `imprimir`, `itens_json`. Cada linha do `itens_json` é `(produto_id, quantidade)`; `nome`, `preco_unitario` e `subtotal` enviados **só servem para detectar divergência de display** — nunca persistem nem somam. Payload sem campos de preço é aceito e precificado inteiramente pelo servidor. Recusa → `303 /caixa/pdv?caixa_id=N&erro=<chave>[&item=M]`; divergência → `200` com a cesta reprificada para revisão e **zero escrita**; sucesso → `303` com `ok=<id persistido>` (omitido quando `imprimir=0`, como antes).

### Regra monetária e de quantidades

- `Decimal` a partir de texto/NUMERIC (nunca float no caminho corrigido); vírgula decimal aceita; subtotal = `preco_vigente × quantidade` quantizado **ROUND_HALF_UP** a 2 casas; total = soma exata dos subtotais persistidos; estouro de `NUMERIC(10,2)` → `total_estourado` (sem 500).
- Quantidade: inteiro ≥ 1; recusa zero, negativo, fracionário (1,5 não vira 1), booleano, NaN/infinito, malformado e acima de `MAX_QUANTIDADE`. Unidade é a política existente — nenhuma venda fracionada nova.
- Preço do cadastro negativo/ausente → recusa (`produto_preco_invalido`); preço zero só passa se houver produto gratuito explícito no cadastro (a fixture `gratis` prova o caso aceito; a recusa é do caminho corrompido).
- Duplicatas do mesmo produto são agregadas antes de validar estoque/limites (uma linha, um movimento); o `exibido` é medido **antes** da agregação, então `1×10 + 1×10 ≠ 2×12` é detecção de divergência, não arredondamento.

### Travas e transação

Uma única `conectar()` por finalização: valida tudo antes de qualquer escrita; `SELECT … FROM produtos WHERE id IN (…) AND ativo=1 ORDER BY id FOR UPDATE` (ordem determinística), linha da sessão `caixa_movimentos` `FOR UPDATE` com `status='aberto'`, trabalhador `FOR UPDATE` no ramo fiado; estoque escrito por valor absoluto `novo_saldo = saldo_travado − quantidade` com `estoque_movimentos` de saldo anterior/posterior explícito. Exceção em qualquer gravação reverte venda, itens, estoque, crédito e acumuladores (demonstrado por injeção de falha na última escrita — S14/R11). A política de estoque negativo existente foi preservada, não reescrita.

### Efeitos preservados (conferidos contra `git show HEAD~:rotas/caixa.py`)

Troco = `recebido − total` só na espécie (mesmo com "doar troco"), `caixa_movimentos.total_vendas += total + doação`, crédito antecipado/consumo de fiado com `min(credito, total)` e débito em `fiado_credito_mov`, snapshot histórico nome/preço em `vendas_pdv_itens` (vendas antigas nunca recalculadas), QR PIX do fluxo, redirect sem `ok=` quando `imprimir=0`, RBAC `guarda_modulo("caixa")` e bloqueio da sessão exclusiva do terminal de presença. Caixa de preço aberto (`caixas.usa_produtos=FALSE` — Geral/Bazar) mantém o valor declarado como regra real; misturar produto de catálogo nele é recusado (`produto_fora_do_caixa`) — nada apagado, nada novo.

### Reprodução: antes × depois

- **Antes (FIN-01 comprovado):** suco R$ 12,50 × 2 com `preco_unitario=0.01`/`subtotal=0.02` enviados → gravou venda de **R$ 0,02**, estoque baixado, sessão acumulando o valor adulterado.
- **Depois:** mesma requisição autenticada à rota real → `200` com banner "O preço de algum produto mudou desde que a cesta foi montada. Nenhum valor foi gravado." e cesta reprificada (`preco_unitario 12.50`, subtotal `25.00`); contagem de vendas, estoque e sessão **inalterados**. Cesta honesta → venda R$ 25,00 persistida, snapshot do cadastro, saldos encadeados na sessão.

Evidências navegável: teste B1 conduz o Chromium real contra a instância temporária — adultera o form embutido da própria tela e confirma a revisão reprificada sem escrita.

### Testes e resultados exatos

Suíte nova `tests/test_pdv_precos_servidor.py` — **32/32**:

- S1–S13 unidade de precificação (honesta, divergência, nome mentiroso, sem preços, duplicatas, agregação, indisponível, preço 0/negativo, quantidades, teto, centavos HALF_UP, preço aberto × catálogo, cestas malformed);
- S14 + R11 atomicidade por injeção de falha na escrita (execução in-process da rota — monkeypatch sobre HTTP não afetaria o processo do servidor);
- R1–R14 pelas rotas HTTP reais autenticadas: R2 = regressão do FIN-01, R3 preço mudou após a cesta, R7 preço aberto, R8 espécie/PIX/doação/troco, R9 crédito e limite de fiado intransponíveis por adulteração, R10 concorrência com 6 threads (estoque 3→0, saldos encadeados, sem sleep), R12 comprovante = id persistido, R13 venda histórica intacta após mudar preço/nome/ativo do cadastro, R14 cesta multi-itens com centavos (total = soma exata dos subtotais persistidos);
- A1–A3 autorização (anônimo, sessão de terminal, sem permissão);
- B1 navegador.

Runner completo `bash tests/rodar_testes.sh` — **RC=0, 11 suítes, 220 testes**: 27+12+8+10+19+18+18+24+21+11+32; log integral em `docs/tasks/evidencias/runner_task006.log`. Conferência do banco de desenvolvimento antes/depois: `pessoas=678 checkins=2918` — **inalterado**.

### Suíte financeira antiga, executada separadamente (sem máscara)

`tests/test_financeiro_integracao.py` em banco descartável próprio (`shambala_fin_old_test`, criado e removido): **3 testes, 7 erros, RC=1**. Classificação honesta: as 7 falhas têm uma causa única — `tearDown`/`addCleanup` fazem `DELETE FROM auditoria_eventos`, tabela que não existe no schema base. É a fixture quebrada que a TASK_006 manda não consertar criando `auditoria_eventos` fictícia. Além disso, mesmo com a limpeza resolvida, dois trechos do `test_pdv_ignora_preco_enviado` pertencem a **outro contrato** (esperam que a adulteração conclua com `303` vendendo pelo preço do servidor, enquanto a especificação desta tarefa exige revisão sem escrita; e cobrem cancelamento/estorno = FIN-02, fora do escopo). Nada disso foi escondido ou "aprovado" por ajuste de expectativa antigo.

### Aplicação no DEV — com evidência

O serviço existente da porta 9000 roda a partir de `/nc/projetos/shambala` (`/proc/<pid>/cwd` confere). O processo de antes (pid 2673126, iniciado 10:38) carregava a árvore **pré-correção**; os `.pyc` de `servico_pdv` e `rotas/caixa` foram gerados 11:12 e o processo novo é de 11:46. Antes do reinício, conferidos os efeitos de startup: `criar_tabelas()` é idempotente e esta entrega não tem DDL — nenhum migração ou escrita inesperada. Reinício controlado do MESMO serviço (mesmo comando, mesma porta, sem nova instância/túnel/página de infra): `GET /login` → 200; baseline de fatos idêntico antes e depois — `trabalhadores=70, presença=20, códigos=70, ativos=70, correções=0, vendas_pdv=1859, soma de vendas=R$ 25 886,75, sessão aberta=1`. **O DEV passa a servir a correção.**

### Roteiro de homologação no DEV (somente dados fictícios)

Sem conta de demonstração nova, sem credencial no chat, nada escrito em fatos reais:

1. Entrar no DEV (`http://<máquina>:9000`) como um operador que já tenha o módulo `caixa` — a correção não cria usuário nem altera permissão.
2. Criar um produto fictício em `/caixa/produtos`: "Homologacao TASK-006 (teste)", preço R$ 12,50, estoque 10.
3. No PDV desse caixa, montar cesta com 2 unidades e finalizar: a venda deve sair por **R$ 25,00**, com baixa de 2 no estoque e sessão somando R$ 25,00.
4. Adulterar a requisição para provar o FIN-01 fechado: com `curl` autenticado (ou DevTools), reenviar a mesma cesta com `preco_unitario: 0.01` — a tela deve voltar **com a cesta reprificada em R$ 25,00 e o aviso de revisão**, e nada pode mudar em vendas/estoque/sessão.
5. Em outra aba, mudar o preço do produto para R$ 99,00 e finalizar a cesta antiga: de novo revisão, depois confirmar e conferir que a venda usa o preço **vigente consultado**.
6. Conferir o comprovante (`/caixa/vendas/{id}/comprovante`) igual ao valor persistido, e o caixa de preço aberto (Bazar/Geral) continuando aceito valor declarado.
7. Cancelar a venda fictícia pelo fluxo existente (a correção **não** mexe em cancelamento — FIN-02 segue aberto) e **inativar** o produto fictício; não excluir, para o snapshot histórico sobreviver.

### Limitações registradas

- **Incidente de contaminação do DEV (já revertido integralmente):** numa etapa de reprodução deste ciclo, um script de seed rodou sem `SHAMBALA_ENV_FILE` e escreveu no banco `shambala` local (cópia fresca de produção). Os registros criados foram desfeitos na hora e verificados por diferencça; desde então todo helper da suíte asserts `current_database()` e exporta o env explícito. Nada disso tocou produção. Registrado aqui porque aconteceu.
- **Validação visual no navegador:** o DEV não tem credencial usável por política (não pedir senha no chat, não criar conta); o B1 exercitou a tela real num processo de teste temporário com dados fictícios — limitação declarada conforme a própria especificação.
- Homologação com operador real do caixa fica com o dono (roteiro acima); a correção não prova cancelamento, fechamento nem financeiro — ver escopo.

### Defeitos financeiros ainda abertos (TASK_005, não tocados por esta entrega)

FIN-02 (cancelamento não reverte estoque/sessão), FIN-03 (crédito antecipado sem lançamento), FIN-04 (fechamento mistura espécie/PIX/fiado), FIN-05 (race na mensalidade), FIN-06 (estorno de fiado pago), FIN-07–FIN-09 (relatórios, constraints/auditoria, foto em transação nas compras). A suíte nova não homologa o financeiro — prova apenas o FIN-01.

### Encerramento de recursos

Instância temporária da 9013 parada; bancos descartáveis `shambala_pdv_task006_test` e `shambala_test_passes` removidos; artefatos `/tmp/shambala_pdv_repro.*` apagados; `.env.pdv_repro` não versionado. Produção conferida por leitura: `/opt/shambala` em `7f14dd3` (commit publicado), serviço `active`, `vendas_pdv=1859`, nenhum arquivo da TASK_006 lá — **nada publicado**. Commits locais, sem push.
