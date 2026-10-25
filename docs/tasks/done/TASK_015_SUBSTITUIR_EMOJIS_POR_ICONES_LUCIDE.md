# TASK_015 — Substituir emojis de interface por ícones Lucide

Status: **implementada e disponível no DEV — aguardando conferência visual do dono.**
Data: 25/10/2026. Commit: `11d8715`.
Relatório completo: `docs/investigacoes/TASK_015_SUBSTITUIR_EMOJIS_POR_ICONES_LUCIDE.md`.
Resumo: 639 → 1 emoji de interface; Lucide `0.460.0` (ISC) vendorizado em
`static/icons/` (94 SVGs, sem CDN); helper `{{ icone("nome") }}`; 521 ícones em
73 templates; dashboard e PDV com altura idêntica no antes/depois, menu a −1px;
regressão com 23 suítes em rc=0 e DEV inalterado. Desvio consciente: o traço
ficou o do pacote (2) e não 1,75 — registrado no relatório §1.

## 1. Objetivo e decisão do dono

Substituir os emojis usados como ícones da interface por SVGs da família Lucide, conforme os exemplos aprovados. O dono rejeitou as propostas de reorganização visual: o dashboard precisa manter sua visão única e o funcionamento atual.

Esta tarefa é exclusivamente de iconografia. Preservar layout, posições, dimensões dos cartões e botões, espaçamentos, tipografia, cores, textos, fotos, navegação, permissões e comportamentos. Preservar a estrela dourada e a identidade Shambala — Cidade de Luz. As prévias de redesign anteriores foram descartadas e não são referência de implementação.

## 2. Preparação

Ler AGENTS.md, SHAMBALA_CONTEXTO.md e as instruções locais pertinentes. Conferir estado do Git e preservar alterações de outras tarefas. Inventariar os emojis de navegação, cabeçalhos, cartões, botões e mensagens da interface, incluindo componentes gerados por JavaScript. Registrar arquivo, contexto e substituição escolhida em uma tabela no relatório.

Priorizar a cobertura do menu, dashboard de atendimentos e caixa da lanchonete; concluir também os demais usos de emojis como ícones da interface. Não substituir conteúdo digitado pelos usuários, dados históricos, textos livres, fotos de produtos ou documentos emitidos apenas por conterem emojis. Não fazer substituição textual global sem examinar o contexto.

## 3. Mapeamento aprovado

| Contexto | Nome Lucide |
| --- | --- |
| Dashboard | layout-dashboard |
| Dia de trabalho / agenda | calendar-days |
| Pessoas | users |
| Acolhimento fraterno | handshake |
| Trabalhadores | contact-round |
| Registro de presença | scan-line |
| Financeiro | wallet |
| Caixa da lanchonete | store |
| Mensalidades | receipt |
| Doações | heart-handshake |
| Compras | shopping-basket |
| Biblioteca | book-open |
| Chamar | megaphone |
| Concluir | check |
| Transferir | arrow-right-left |
| Cancelar | x |

Para funções adicionais, escolher equivalentes semânticos da mesma família e registrar no inventário. Não mudar rótulos ou ações para acomodar o ícone. Confirmar que os nomes existem na versão adotada.

## 4. Implementação

- Usar SVGs Lucide servidos localmente, sem CDN, fontes de ícones ou dependência de acesso à internet. Fixar a versão/origem e preservar a licença correspondente. Reaproveitar infraestrutura existente se adequada.
- Preferir um mecanismo pequeno e compartilhado, compatível com os templates atuais; não introduzir framework, build novo ou reescrita de componentes apenas para isso.
- Manter traços consistentes (referência visual: stroke-width 1,75), sem fundos decorativos novos. Usar currentColor e as cores já atribuídas ao elemento. Não recolorir a interface.
- Dimensionar os ícones para caber no espaço já ocupado, sem aumentar cartões, cabeçalhos ou botões, nem reduzir a densidade do dashboard. Ajustar apenas alinhamento e caixa do próprio ícone quando necessário.
- Ícones acompanhados de texto são decorativos (aria-hidden="true"). Controles somente com ícone precisam conservar nome acessível que descreva a ação. Não remover confirmações, tooltips úteis, foco ou áreas clicáveis.
- Garantir que conteúdo atualizado dinamicamente continue mostrando seus ícones sem duplicação e sem quebrar handlers existentes. Preferir SVG disponível na renderização, quando viável.
- Preservar o versionamento de assets já existente para evitar CSS/JS antigo em cache.

## 5. Limites obrigatórios

Não alterar regras de presença, atendimento, financeiro, vendas, idempotência, permissões ou dados. Não criar migração. Não alterar a disposição das filas, adicionar abas, recolher filas, paginar atendimentos ou reorganizar menus. Não trocar fotos do PDV por desenhos. Não alterar a estrela dourada.

Não criar usuários, mudar credenciais ou solicitar senha no chat. Não acessar produção, nem por leitura. Não fazer push ou deploy. Manter DEV HTTP na porta 9000, sem nginx, TLS, CA ou nova porta permanente.

## 6. Validação proporcional

Esta é uma alteração visual reversível; não criar uma grande suíte que apenas reproduza a lista de ícones. Reaproveitar verificações existentes e fazer conferência visual dirigida em ambiente isolado quando exigir dados fictícios ou login de teste.

Conferir:

1. Menu e telas afetadas antes/depois, mantendo conteúdo e largura equivalentes.
2. Dashboard cheio, com várias filas e muitas pessoas: mesma visão, ordem, dimensões, densidade e ações. Não inserir atendimentos fictícios no DEV real para produzir a prova.
3. PDV com produtos, carrinho e pagamentos: fotos preservadas, controles sem deslocamento e ações existentes funcionando em banco descartável.
4. Desktop e celular, sem corte, sobreposição ou rolagem nova causada pelos ícones.
5. Nomes acessíveis dos botões, ícones dinâmicos, ausência de recursos externos e erros de console.
6. Busca final por emojis de interface restantes, com justificativa dos que forem preservados por identidade ou conteúdo.

Executar os checks pertinentes aos templates/JavaScript alterados. Se a norma local exigir runner completo, executá-lo com as proteções de isolamento existentes. Nunca interpretar teste pulado ou falha de limpeza como aprovação. Registrar somente verificações realmente realizadas.

## 7. Aplicação no DEV e entrega

Após validar, disponibilizar no serviço existente da porta 9000, com reinício somente se necessário. Confirmar antes a propriedade e configuração do processo; não subir segunda instância permanente. Não escrever dados de negócio nem modificar autenticação para validar a entrega. Sem acesso autenticado, declarar a limitação e deixar a conferência visual do DEV ao dono, usando ambiente descartável como evidência complementar.

Entregar na própria tarefa:

- Inventário das substituições, arquivos alterados e mecanismo/versão Lucide adotado.
- Imagens comparáveis antes/depois do menu, dashboard cheio e PDV, com dados fictícios quando necessário; não apresentar simulação como captura do DEV.
- Resultado dos checks, limitações e confirmação de que layout, ações e dados foram preservados.
- Roteiro curto para o dono conferir as três telas no DEV.
- Commits locais somente dos arquivos desta tarefa, sem push; limpeza dos recursos descartáveis criados nesta execução.

Atualizar SHAMBALA_CONTEXTO.md apenas com a convenção de ícones necessária às próximas tarefas. Ao concluir, registrar status real (implementada no DEV, aguardando conferência visual do dono) e mover para docs/tasks/done/TASK_015_SUBSTITUIR_EMOJIS_POR_ICONES_LUCIDE.md.

## 8. Critério de aceite

Os emojis de interface foram substituídos por Lucide de forma consistente; a estrela dourada e as fotos permanecem; as telas mantêm a organização atual, especialmente a visão única do dashboard. Nenhuma regra, dado ou permissão mudou. A aprovação visual final pertence ao dono e não autoriza publicação em produção.
