# TASK_011 — Reformular a Visão Geral do Financeiro

Status: **executada e aplicada no DEV; homologação visual do dono reaberta após a correção de CSS (§7)** (login do dono na 9000 seguindo o roteiro ao fim do relatório). Testes: contrato 51/51, navegador 42/42, runner completo 17/17 rc=0. Fatos do DEV preservados (diff zero antes/depois de cada restart, incluindo digital de conteúdo). Sem push; produção intocada.
Referências: diagnóstico TASK_005, regras TASK_006–008, revisão TASK_009 e revalidação TASK_010 (encerrada com ressalvas).

## Objetivo e autorização

Implementar uma melhoria visível e utilizável em Financeiro → Visão Geral. O dono conferiu a tela atual e não percebeu mudanças porque as etapas anteriores se concentraram nas regras internas. Agora a entrega deve tornar fácil entender o período, localizar um lançamento e distinguir dinheiro movimentado de obrigações pendentes.

A tela atual reúne uma lista longa de vendas, doações, mensalidades e fiados; usa rótulos técnicos como venda_pdv/recebimento_fiado e exibe registros históricos em Valores a Receber com status Pago. A imagem é evidência de apresentação confusa, não prova suficiente para corrigir automaticamente o estado desses fatos.

Entregar a interface funcionando no DEV existente, preservando o padrão visual do Shambala. Não basta entregar proposta ou relatório. Não consultar serviços externos para copiar HomeBank: esta tarefa aplica os objetivos já discutidos, sem reproduzir o programa inteiro.

## Ambiente e limites

- Ler normas locais/AGENTS.md se existir, SHAMBALA_CONTEXTO.md e relatórios TASK_005–010. Conferir estado real, rotas e permissões; preservar trabalho alheio.
- Não acessar produção, nem por leitura, SSH, push ou publicação. Não alterar infraestrutura, nginx/TLS/túnel ou criar instância persistente adicional.
- DEV permanece HTTP na porta 9000. Testes em PostgreSQL nativo descartável, sem Docker; usar as proteções de destino da TASK_010 antes de startup/seed/limpeza. Encerrar somente recursos próprios.
- Não alterar fatos financeiros históricos, receber pendências, converter sessões legadas, regenerar mensalidades nem corrigir saldos por heurística. Não criar usuário, trocar senha ou pedir credencial no chat para obter prévia autenticada.
- Não implementar conciliação, importação bancária, recorrência, nova contabilidade, idempotência do PDV ou transição do legado. Essas pendências continuam separadas.

## 1. Reconstruir as consultas antes de redesenhar

Mapear a rota da Visão Geral, templates/CSS e consultas que produzem entradas, saídas, pendentes e saldos por conta. Identificar:

- data usada para cada total (lançamento, recebimento, vencimento ou competência);
- diferenças entre conta patrimonial real, gaveta/sessão e Valores a Receber;
- tratamento de pendentes, cancelamentos, compensações e transferências internas;
- integração dos eventos novos com financeiro_movimentacoes sem somar o mesmo fato duas vezes;
- origem dos saldos atuais e eventuais limitações históricas.

Registrar brevemente o contrato encontrado e o contrato final. Corrigir consultas de leitura necessárias à clareza desta tela, com casos determinísticos. Não transformar divergência histórica em migração silenciosa. Se o banco não permite calcular com segurança um indicador, omitir esse indicador ou rotulá-lo explicitamente como valor registrado; não prometer saldo disponível real.

## 2. Estrutura visual a implementar

### Cabeçalho e navegação

- Título claro "Financeiro", período em destaque e ações existentes organizadas sem uma sequência de botões espremidos. Manter destinos e RBAC; separar navegação de ações de lançamento.
- Usar melhor a largura disponível no desktop, com limite confortável e alinhamento ao restante do sistema. Tipografia legível, espaço adequado, valores monetários alinhados e sem quebra desnecessária. Não reduzir fonte para acomodar toda a tabela.
- Ações principais visíveis: Nova movimentação e navegação existente para mensalidades, fiados, relatórios e transferências. Não criar páginas vazias para compor menu.

### Resumo compreensível

Mostrar quatro indicadores do período, com definições curtas acessíveis:

1. Recebimentos registrados.
2. Pagamentos registrados.
3. Resultado do período (recebimentos − pagamentos, conforme contrato documentado).
4. A receber (obrigações pendentes no recorte de vencimento explicitado; se houver despesas pendentes, separar A pagar sem misturá-las).

Usar verbos e nomes coerentes com as evidências persistidas. Totais de registros antigos não comprovadamente realizados devem ser distinguidos; não incluir Valores a Receber como dinheiro realizado só porque o status armazenado é pago.

Transferências internas comprovadas não são receita/despesa global: no consolidado não inflar recebimentos/pagamentos com o par; no extrato da conta mostrar sua entrada/saída e natureza Transferência. Não inferir par por coincidência de valor/data/descrição. Sangrias históricas sem contrapartida comprovada devem permanecer identificáveis, sem fabricar a entrada faltante.

Não chamar resultado mensal de saldo disponível. Se mantiver cartões por conta, separar contas de dinheiro/banco de obrigações, explicitar se são saldos registrados acumulados ou movimentos no período e não misturar gaveta com saldo financeiro. Não prometer conciliação bancária.

### Duas consultas claramente separadas

- **Movimentações**: extrato de registros financeiros efetivos/registrados, com limitações históricas identificadas quando necessário.
- **A receber / A pagar**: obrigações pendentes, por vencimento; não apresentar promessa como entrada já ocorrida. Renderizar só os grupos que o modelo realmente comporta.

Pode implementar como abas na mesma rota com URL própria por estado. Cada aba deve explicar brevemente o universo/data do resumo. Trocar de aba não deve apagar silenciosamente filtros compatíveis. Não criar um novo fluxo de baixa nesta etapa: usar links para telas existentes quando autorizados.

## 3. Filtros e lista

- Período com início/fim, mês atual como padrão e atalhos Mês anterior / Mês atual. Explicitar a data filtrada em cada consulta. Validar datas/intervalo no servidor e preservar valores na recusa.
- Filtros por conta, natureza/categoria e entrada/saída quando aplicáveis; situação na consulta de pendências. Busca por descrição e nome vinculado quando houver vínculo persistido. Não fazer consultas com interpolação SQL insegura.
- Botões Filtrar e Limpar; estado refletido na URL para voltar de um detalhe e compartilhar o mesmo recorte. Parâmetros inválidos devem dar orientação, sem transformar silenciosamente um filtro inválido em Todos.
- Resumos devem refletir o conjunto filtrado integral, não apenas a página, com rótulo claro. Paginação não altera totais. Se algum indicador não usa determinado filtro, explicitar a diferença ou rever a apresentação para evitar ambiguidade.
- Paginação no servidor, padrão 25 itens, opções 25/50/100 com limite no servidor, contagem de registros e intervalo exibido. Ordenação determinística por data e ID; filtros preservados ao paginar; ajustar página fora do intervalo sem erro e sem perder filtro. Evitar renderizar toda a base para ocultá-la com JavaScript.
- Desktop: colunas legíveis de Data, Descrição/Origem, Natureza, Conta, Entrada/Saída ou Valor com sentido, e situação quando relevante. Rótulos legíveis: Venda, Doação, Mensalidade, Recebimento de fiado, Adiantamento de crédito, Transferência etc. Não expor identificadores técnicos como nomes de categoria.
- Smartphone: disposição compacta por linha/cartão, com data, descrição, valor e conta/situação visíveis; detalhes secundários expansíveis quando necessário. Sem rolagem horizontal da página, sobreposição ou ações inacessíveis.
- Estados vazios com explicação e opção de limpar filtros; estado de erro compreensível. Não usar cor como único sinal de entrada/saída/situação. Preservar foco, rótulos dos campos e uso por teclado.

## 4. Histórico e rastreabilidade

- Não reclassificar um fiado histórico como recebido ou pendente apenas pelo nome da conta ou descrição. Pode identificá-lo como registro histórico em conta de obrigações, excluindo-o do resumo de dinheiro quando a conta for comprovadamente dessa natureza, sem alterar o fato armazenado nem inventar a situação da dívida.
- Diferenciar status financeiro armazenado da confirmação de quitação real. Quando ambíguo, usar texto simples como "Registro histórico — conferir origem", em vez de afirmar Recebido/Pago sem lastro.
- Consulta de detalhes em página/painel somente leitura com ID, datas existentes, valor, conta, natureza, situação registrada e links para origem comprovada (venda, mensalidade, recebimento, sessão, transferência). Não reconstruir vínculos por texto/valor/data.
- Exibir cancelamento/compensação comprovados sem apagar original ou descontar duas vezes. Links e detalhes devem respeitar permissões no servidor, inclusive URL direta. Usuário do terminal continua sem acesso financeiro.
- Não expor jargão de schema na apresentação habitual; reservar informação técnica indispensável ao detalhe legível. Não colocar a lista inteira em JSON na página para viabilizar busca.

## 5. Validação orientada ao resultado

Criar dados fictícios exclusivamente no descartável para verificar:

1. Recebimento e pagamento reais, pendências a receber/a pagar, PIX, espécie e transferência interna com par comprovado. Totais e saldo por conta são coerentes e obrigações não entram como dinheiro.
2. Registro histórico em Valores a Receber com status pago: continua preservado, a tela explica a limitação e não o apresenta como recebimento real comprovado.
3. Cancelamento/compensação, mensalidade e fiado com nomes semelhantes sem duplicação por JOIN; evento/financeiro vinculados não são somados duas vezes.
4. Limites de período, data inexistente/intervalo invertido, filtros combinados, conta inválida, busca e caracteres especiais; resultado e resumo coerentes.
5. Mais de 25 registros, ordenação empatada, página vazia/fora do intervalo, navegação voltar e totais iguais entre páginas. Não contar a base inteira antes de filtrar nem carregar a base inteira no navegador.
6. RBAC: leitura autorizada, ações sem permissão ocultas e rotas protegidas, terminal recusado. GETs de consulta não criam fatos nem disparam rotinas de recebimento/geração.

Validar no navegador com desktop e smartphone: percurso Resumo → filtro → página → detalhe → voltar e pendências separadas. Evidências antes/depois no mesmo tamanho de viewport, com dados fictícios; não publicar dados pessoais reais em screenshots. Prévia deve mostrar a mudança visual, não só login ou resposta 200.

Testes de contrato de consultas são necessários porque os indicadores podem alterar a interpretação financeira. Evitar testes que apenas reproduzam CSS/HTML. Rodar suítes afetadas e verificações requeridas pelo projeto; ampliar ao runner completo se consultas compartilhadas/regras alteradas exigirem regressão. Contar casos/asserções corretamente, indicar RC/pulados e provar destino efetivo da execução segundo TASK_010.

## 6. Aplicação no DEV e entrega

- Disponibilizar no serviço existente da 9000 após validação. Sem schema/migração de fatos prevista. Antes do reinício, baseline próprio dos fatos financeiros relevantes e backup recuperável com restauração validada; conferir impacto do startup, não presumir que reiniciar é livre de escrita.
- Conferir fatos preservados antes/depois e que a árvore correta está servida. Não alterar credenciais para obter conferência visual autenticada; validar no descartável e deixar o DEV para login do dono.
- Relatório nesta ordem: arquivos/rotas, contrato de resumo e filtros/datas, regras do histórico, limitações, consultas/testes e evidências visuais antes/depois. Atualizar SHAMBALA_CONTEXTO.md e fornecer URL exata e roteiro curto que permita ao dono constatar as mudanças sem lançar nada.
- Commit local apenas dos arquivos da tarefa, sem push. Encerrar recursos próprios; mover a tarefa para docs/tasks/done/ com status real, distinguindo aplicação no DEV de homologação manual.

## Critérios de aceitação

1. Visão Geral visivelmente reformulada e funcionando no DEV, com hierarquia legível em desktop e smartphone.
2. Movimentações e pendências separadas, rótulos humanos, filtros úteis, paginação e detalhes com origem rastreável.
3. Totais documentados e conferidos sobre todo o recorte, sem duplicação e sem promover obrigação/registro ambíguo a dinheiro confirmado.
4. Nenhuma alteração de fatos históricos, decisão sobre legado ou ampliação de permissões.
5. Evidências visuais fictícias e roteiro de consulta entregue ao dono. Produção não acessada nem publicada.

---

# Relatório de execução (TASK_011)

## 1. Arquivos e rotas

| Arquivo | Mudança |
|---|---|
| `rotas/financeiro.py` | `GET /financeiro` reescrito (contrato de somas, abas por URL, filtros validados, paginação no servidor); nova rota `GET /financeiro/lancamento/{id}` (somente-leitura, `volta` restrito a `/financeiro?…`); mapas `ROTULOS_CATEGORIA`/`ROTULOS_SITUACAO`/`ROTULOS_TIPO_CONTA`; `_guard` adicionado a `GET /financeiro/mensalidades/buscar` (correção incidental: antes qualquer sessão logada recebia JSON com CPF) |
| `templates/financeiro/dashboard.html` | reescrita completa (cabeçalho com período, cartões com microdefinição, abas-âncora, barra de filtros GET, tabela desktop + linhas `<details>` no celular, paginação, extrato por conta, aba de pendências com fiado em grupo próprio) |
| `templates/financeiro/detalhe.html` | novo: lançamento somente-leitura com vínculos comprovados por FK |
| `templates_config.py` | filtro Jinja `moeda` → `R$ 1.234,56` |
| `static/css/base.css` | bloco `Financeiro — visão geral (TASK_011)`; breakpoint móvel 768 px, sem novos pontos |
| `tests/test_financeiro_visaogeral_consultas_task011.py` | nova suíte de contrato (C1–C6, prefixo T011C) |
| `tests/test_financeiro_visaogeral_navegador_task011.py` | nova suíte de navegador (N1–N7, Chromium desktop+smartphone, prefixo T011N) |
| `tests/baseline_financeiro_dev.py` | novo: agregados financeiros do DEV para prova de preservação de fatos |
| `tests/rodar_testes.sh` | as 2 suítes novas entraram na lista fixa |
| `SHAMBALA_CONTEXTO.md` | seção do Financeiro documenta o contrato da Visão Geral |

Nenhum schema alterado; nenhuma escrita em fatos históricos.

## 2. Contrato do resumo, filtros e datas

Universo das somas: **somente `financeiro_movimentacoes`** (o livro-caixa). Eventos
de caixa (`caixa_sessao_eventos`) aparecem apenas como proveniência no detalhe
(via `evento.financeiro_id`) e nunca são somados por cima.

- **Recebimentos registrados** = entradas `status='pago'` no período, EXCLUINDO
  `categoria IN ('transferencia','sangria')` pelo valor ARMAZENADO (nunca por
  coincidência de valor/data/texto) e excluindo linhas cuja conta é
  `tipo='areceber'` (obrigação não é dinheiro).
- **Pagamentos registrados** = saídas pagas com as mesmas exclusões.
- **Resultado do período** = Recebimentos − Pagamentos, com rótulo fixo
  "registro do período — não é saldo disponível; não inclui transferências
  internas, sangrias nem obrigações".
- **A receber / A pagar** = `status='pendente'` por vencimento dentro do período;
  atendem SOMENTE o período (nota na tela quando há filtros extras).
- **Fiado em aberto** (`vendas_pdv` `forma_pagamento='fiado' AND NOT fiado_pago
  AND status='concluida'`) é grupo próprio na aba de pendências, sem vencimento,
  explicitamente FORA do indicador "A receber".
- Parâmetros: `aba=mov|pend`, `de`/`ate` (ISO; `mes=YYYY-MM` mantém-se alias),
  `conta`, `nat`, `sentido`, `sit`, `q`, `pagina`, `por∈{25,50,100}`. Validação no
  servidor com recusa explicada que PRESERVA o valor digitado (nada de virar
  "Todos" em silêncio). Busca `q` normaliza acentos igual ao `norm()` do banco e
  escapa `%`/`_`.
- Paginação no servidor (`ORDER BY data_movimentacao DESC, id DESC LIMIT/OFFSET`);
  totais calculados sobre o recorte inteiro, iguais em todas as páginas; página
  fora do intervalo é ajustada preservando filtros; rótulo "exibindo A–B de N".
- GETs não escrevem: o dashboard não chama `sincronizar_mensalidades` nem o
  gerador de mensalidades (provado por snapshot de contagens antes/depois em C6).

## 3. Regras do histórico (nada foi migrado)

- Linha da conta "Valores a Receber" com status pago: permanece no banco intacta,
  é excluída dos totais de dinheiro e rotulada "Registro histórico — conferir
  origem" na lista; aviso com a contagem dessas linhas acima do extrato.
- Conta `tipo='legado'` exibida como "Conta Antiga (origem histórica)" —
  rotulada, não excluída.
- Cancelados continuam listados com situação própria e fora dos totais.
- Saldos acumulados por conta (não comprováveis) saíram da tela; no lugar,
  "Movimento por conta no período" com a faixa "conta de obrigações — valores
  registrados, não dinheiro" nas contas `areceber`.

## 4. Limitações assumidas (declaradas, não escondidas)

- Saldo disponível real e conciliação bancária: fora de escopo (a tela avisa).
- Par de transferência do legado sem vínculo (`transferir` não grava FK): o
  detalhe diz "o banco não guarda vínculo com o par".
- Venda PIX citada em texto livre não vira link (proibição de inferência).
- Decisão sobre o legado: pendente do dono (herança da TASK_010).

## 5. Consultas, testes e evidências

- Suíte de contrato `test_financeiro_visaogeral_consultas_task011.py`: C1 cenário
  completo (pares excluídos, contrapartida, fiado separado) · C2 histórico
  preservado/rotulado/excluído · C3 sem contagem dupla (cancelado, gêmeos
  mensalidade×fiado, evento vinculado) · C4 recusas com valores preservados ·
  C5 paginação determinística · C6 RBAC e GETs sem efeitos. **51 asserções,
  0 falhas.**
- Suíte de navegador `test_financeiro_visaogeral_navegador_task011.py`: N1 cartões
  com soma combinada à mão (+178,00 / −30,00 / 148,00 / 60,00 sobre 37 linhas) ·
  N2 busca filtra cartões sem tocar em A receber · N3 detalhe + voltar com o MESMO
  recorte na URL · N4 paginação 1–25/26–37 e troca de tamanho · N5 pendências com
  fiado fora do indicador · N6 smartphone 412×915 sem scroll horizontal e linhas
  expansíveis · N7 foco por teclado em todos os filtros. **34 asserções,
  0 falhas.**
- Provas de destino TASK_010 (arquivo de env exclusivo, banco selado, token do
  log do servidor) em ambas as suítes; dados 100% fictícios (prefixos T011C/T011N).
- Runner completo com as suítes novas na lista (`docs/tasks/evidencias/TASK_011/runner_completo.log`): 17/17 suítes rc=0, incluindo as duas novas (51 e 34 asserções, 0 falhas) e a suíte de navegador da TASK_008 sem regressão; conferência final provou o DEV inalterado durante a regressão (pessoas=678, checkins=2918 antes e depois). Nenhum pulamento: ferramentas presentes, destino provado por selo/token nesta execução.
- Evidências visuais (mesmos fixtures, mesmos viewport, dados fictícios) em
  `docs/tasks/evidencias/TASK_011/`: `antes-resumo-desktop.png` /
  `antes-resumo-smartphone.png` (código antigo: entradas infladas por
  transferência, rótulos técnicos, "Pago" em Valores a Receber) versus
  `depois-resumo-desktop.png`, `depois-resumo-smartphone.png`,
  `depois-pendencias-desktop.png`, `depois-detalhe-desktop.png`.

## 6. Aplicação no DEV (porta 9000)

- Backup antes de qualquer ação: `docs/tasks/backups/shambala_pre_task011_20261024_225502.sql.gz` (SHA-256 `9231e906ad554a368ac7c127da903e0de7f38d65a96dca732ed1e1ba7f58f05c`, em `backup_sha256.txt`). Restauração VALIDADA em `shambala_t011_restauracao_test` (banco selado TASK_010): os 19 agregados de `baseline_dev.py` e os 11 agregados de `baseline_financeiro_dev.py` — incluindo a digital de conteúdo `max_id|md5(linhas)` do livro-caixa — idênticos ao DEV.
- Baseline de fatos ANTES do restart (`baseline_dev_antes.txt` / `baseline_financeiro_antes.txt`: 862 lançamentos, soma 69.446,04, digital `864|efdd7e23…`) e DEPOIS (`*_depois.txt`): **diff zero** — o startup idempotente (`criar_tabelas`, `criar_atendente_inicial`, `seed_permissoes`, `gerar_todas_ativas`) não criou nenhuma linha, pois as mensalidades já existiam da subida anterior. Nenhum fato alterado.
- Restart exclusivo do serviço da 9000 (uvicorn plain, mesmo comando; PID 1169423 → 1729434) com ambiente limpo de variáveis de teste; log `dev-startup-task011.log` sem nenhum rastro de modo de teste. Árvore servida = commit `feat` desta tarefa.
- Smoke sem credenciais (proibido criar usuário/senha para prévia): `GET /financeiro` → 303 `/login`; `GET /financeiro/lancamento/1` → 303 `/login` (rota NOVA existindo — prova de código novo; antes daria 404); `GET /financeiro/mensalidades/buscar` → 303 `/login` (antes respondia JSON a qualquer sessão). Homologação visual autenticada: com o dono (roteiro acima), usando o login dele.
- Produção: não acessada nem por leitura; nenhum push.

## Roteiro do dono (nada é lançado)

URL exata: **http://<host-dev>:9000/financeiro** (login próprio do dono).

1. Abra `/financeiro`: os 4 cartões do mês atual dizem o que contam ("O que
   conta" abre a definição; o Resultado declara "Não é saldo disponível").
2. Use "Mês atual"/"Mês anterior" ou De/Até; filtre por Conta/Natureza e busque
   por descrição ou nome (acentos funcionam).
3. Vire a página (25/50/100) — os totais não mudam entre páginas.
4. Clique **Ver** numa linha: a tela do lançamento mostra só vínculos provados no
   banco; **← Voltar** devolve exatamente ao mesmo recorte.
5. Abra a aba **A receber / A pagar**: obrigações pendentes com vencimento no
   período e, à parte, o fiado em aberto (fora do indicador). Nenhuma baixa
   acontece aqui — as ações levam às telas de Mensalidades e Fiados.

---

## 7. Adendo — Correção da homologação visual (CSS servido)

A homologação encontrou divergência entre a tela na 9000 e as prévias aprovadas
(abas, cores, espaçamento). Investigação com evidência, nesta ordem:

1. **O CSS em disco e o CSS servido pela 9000 são idênticos byte a byte**
   (`md5sum static/css/base.css` = `curl http://127.0.0.1:9000/static/css/base.css | md5sum`).
2. **A árvore servida é a nova** — `GET /financeiro/lancamento/1` responde
   303 (rota que só existe desde esta TASK_011), então não era código antigo no servidor.
3. **`templates/base.html` referenciava `/static/css/base.css` sem versão** e o
   `StaticFiles` envia apenas `ETag`/`Last-Modified`, nunca `Cache-Control`.
   Sem instrução de revalidação, o navegador aplica *cache heurístico* e
   continuou usando o `base.css` **anterior à TASK_011** — que não tem as
   classes `fin-*`. As prévias foram tiradas em contextos Playwright novos, por
   isso mostravam o CSS correto. **Causa raiz: cache do lado do cliente, não o
   servidor nem os seletores.**
4. Conferida também a ordem de carregamento: o bloco da TASK_011 é o último do
   arquivo e não há conflito de especificidade com `.stat-label`/`.grade-cards`
   (o que havia era compatível e já aparecia nas prévias aprovadas).

### O que mudou

- **Assets versionados** — global Jinja `asset_url(caminho)` em
  `templates_config.py` gera `/static/<caminho>?v=<mtime>` (com `v=0` se o
  arquivo não for legível). Aplicado nos 5 links de stylesheet do projeto
  (`base.html`, `login.html`, `terminal/leitura.html`,
  `trabalhadores/carteirinha.html`, `trabalhadores/carteirinhas_lote.html`).
  Editar o CSS muda o `?v=` e o navegador busca o novo sem esperar a expiração
  heurística — a divergência deixa de poder se repetir.
- **Valores monetários grandes** — `.fin-cards .stat-valor` media 1,6 rem com
  `white-space: nowrap` dentro de colunas de no mínimo 140 px: um
  `R$ 1.234.567,89` estourava o cartão e gerava rolagem horizontal no celular.
  Os cartões viram contêineres de consulta (`container-type: inline-size`) e a
  fonte se ajusta à largura do cartão (`min(1.6rem, 8.6cqi)`; faixa
  `clamp(1.1rem, 4.5vw, 1.6rem)` como reserva onde `cqi` não existir), com
  grade `minmax(220px,1fr)`, duas colunas ≤768 px e coluna única ≤480 px. O
  número nunca quebra linha nem é cortado.
- **Extrato em larguras estreitas** — as tabelas do desktop ganharam o invólucro
  `.fin-rolo` (`overflow-x: auto`): abaixo de ~860 px o extrato rola dentro de
  si, e a página como um todo não ganha barra horizontal.
- **Faixa "Movimento por conta"** (`.fin-contas`) e **atalhos de mês** (links em
  coluna, sem o separador órfão) ajustados pelo mesmo critério.

### Provas

- Fixture nova de `R$ 1.234.567,89` na suíte de navegador (janela 2026-06): os
  cartões passam a `+ R$ 1.234.745,89` / `− R$ 30,00` / `R$ 1.234.715,89` sobre
  **38** linhas, com paginação `1–25 de 38` e `26–38 de 38`.
- Asserções novas: o HTML servido referencia `css/base.css?v=` (anti-cache), o
  valor grande aparece com ponto de milhar no celular, nenhum `.stat-valor`
  estoura o próprio cartão (`scrollWidth > clientWidth`) e **N8** repete a
  conferência em 1366/1024/820/768/412 px. Suíte: **42 asserções, 0 falhas**;
  contrato continua **51/51**.
- Runner completo depois da correção: **17/17 suítes rc=0**
  (`docs/tasks/evidencias/TASK_011/runner_correcao_css.log`), DEV inalterado
  durante a regressão (pessoas=678, checkins=2918 antes e depois). Evidências
  `depois-*.png` re-capturadas com o valor grande nas fixtures.

### Aplicação no DEV (9000)

- Backup antes da mudança: `docs/tasks/backups/shambala_pre_task011_css_20261024_232935.sql.gz`,
  SHA-256 `5221e00bf259674ebbfabdf07b5225bdc148a76a552a2e00dca9ae31805a7056`.
  Restauração **validada** em `shambala_t011_restauracao_test` (banco selado,
  descartado em seguida): os 11 agregados financeiros — incluindo a digital de
  conteúdo `max_id|md5(linhas)` — e o `baseline_dev.py` saíram idênticos ao DEV
  (`restauracao_validada.log`).
- Baseline de fatos antes (`baseline_dev_antes2.txt` /
  `baseline_financeiro_antes2.txt`) e depois (`*_depois2.txt`) do restart:
  **diff zero**. Restart apenas do serviço da 9000 (mesmo comando uvicorn, PID
  1729434 → 1869627) com ambiente limpo de variáveis de teste; log
  `dev-startup-task011-css.log`.
- Smoke sem credenciais: `GET /financeiro` → 303 `/login`;
  `GET /financeiro/lancamento/1` → 303 `/login`; `md5` do CSS servido = `md5`
  do arquivo em disco (`42d46ebb…`); o HTML de `/login` já traz
  `href="/static/css/base.css?v=1792894836"` e a URL versionada responde 200.

### Limitação que resta (declarada)

O `?v=` só vale a partir **desta** subida: quem já estava com o CSS antigo em
cache o usa até a primeira requisição à página já versionada — na prática, um
recarregar simples (`Ctrl+Shift+R`) resolve e depois disso o versionamento
cuida sozinho. Nenhum dado financeiro foi alterado em nenhuma etapa; nenhuma
credencial foi criada, lida ou alterada.
