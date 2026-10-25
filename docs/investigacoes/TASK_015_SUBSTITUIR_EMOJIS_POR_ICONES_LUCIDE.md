# TASK_015 — Substituir emojis de interface por ícones Lucide

Status: **implementada e disponível no DEV — aguardando conferência visual do dono.**
Data: 25/10/2026. Commit de referência ao final.

Alteração exclusivamente de iconografia: layout, posições, dimensões, espaçamento,
tipografia, cores, textos, fotos, navegação, permissões e comportamentos foram
preservados. A estrela dourada e a identidade "Shambala — Cidade de Luz" estão
intactas.

---

## 1. Mecanismo e versão adotados

| Item | Decisão |
|---|---|
| Família | [Lucide](https://lucide.dev) — a família dos exemplos aprovados |
| Pacote / versão | `lucide-static@0.460.0`, **fixada** |
| Origem | `https://unpkg.com/lucide-static@0.460.0/icons/<nome>.svg`, baixados uma vez |
| Onde ficam | `static/icons/*.svg` (**94 arquivos**), servidos por `/static/icons/…` |
| Licença | ISC, preservada no cabeçalho de cada SVG e em `static/icons/LICENSE.md` |
| Dependência de rede | **nenhuma** em execução: sem CDN, sem fonte de ícones |
| Cache | `asset_url` já existente (`?v=<mtime>`), reaproveitado |

**Helper compartilhado** (`templates_config.py`): `{{ icone("wallet") }}` devolve

```html
<img src="/static/icons/wallet.svg?v=…" class="icone" alt="" aria-hidden="true"
     width="24" height="24" loading="lazy" decoding="async">
```

Por que `<img>` e não SVG embutido: o arquivo já está no servidor, o navegador o
busca por URL (com cache próprio) e o template não carrega ~20 linhas de markup
nos 521 usos. Também permite `asset_url`, mantendo o versionamento contra cache
antigo. Um nome inexistente **levanta erro na renderização** — sem isso o ícone
sumiria em silêncio.

**Acessibilidade**: decorativo por padrão (`alt=""` + `aria-hidden="true"`),
porque há texto ao lado. Controle que ficou **só com ícone** recebeu
`aria-label`: 16 botões, aproveitando o `title` que já existia (ex.: "Marcar como
realizado", "Cancelar da fila") ou um rótulo explícito quando não havia nenhum.

**CSS** (`static/css/base.css`, bloco "Ícones Lucide"): o ícone ocupa o espaço que
o emoji ocupava (`.menu-card-icon` com 2,2rem de altura fixa), sem aumentar
cartão, botão ou cabeçalho. Ajustar só isso levou o menu de +19px para **−1px**
de altura em relação ao original.

**Sobre o `stroke-width` 1,75 da ordem**: os SVGs mantêm o traço do pacote (2)
para continuarem reproduzíveis a partir da origem. Forçar 1,75 exigiria embutir o
SVG em cada uso (ou 521 arquivos derivados), derrubando o ponto principal de
manter um mecanismo pequeno. **Registrado como desvio consciente** — se o dono
preferir 1,75, é uma passada no CSS/arquivos, não uma mudança de desenho.

---

## 2. Inventário

- **639 → 1** emoji de interface nos templates (o restante está justificado em §5).
- **521 ícones** aplicados em **73 templates**, com **70 ícones distintos**.
- Inventário completo (emoji → ícone → usos → contexto) em
  `docs/tasks/evidencias/TASK_015/inventario.md`.

A tabela aprovada na ordem (§3) foi seguida à risca no menu e nos contextos
correspondentes: `layout-dashboard`, `calendar-days`, `users`, `handshake`,
`contact-round`, `scan-line`, `wallet`, `store`, `receipt`, `heart-handshake`,
`shopping-basket`, `book-open`, `megaphone`, `check`, `arrow-right-left`, `x`.
Funções adicionais receberam equivalentes semânticos da **mesma família**
(`printer` para imprimir, `banknote` para espécie, `scan-line` para presença,
`ban` para proibido, `triangle-alert` para aviso…). **Nenhum rótulo ou ação foi
mudado para acomodar o ícone.**

---

## 3. Substituições por área (prioridade da ordem)

| Área | Arquivos | Ícones |
|---|---|---|
| **Menu** | `templates/menu.html` | 16 (todos os cartões + seção Administração) |
| **Dashboard de atendimentos** | `dia/dashboard.html`, `dia/painel.html`, `dia/fraterno.html`, `dia/acolhimento.html` | 37 |
| **Caixa da lanchonete** | `caixa/pdv.html`, `caixa/fiados.html`, `caixa/vendas.html`, `caixa/fechamento.html`, `caixa/sangria.html`, `caixa/doacao.html`, `caixa/estoque*.html`, `caixa/produtos.html` | 63 |
| Financeiro | `financeiro/*.html` | 24 |
| Pessoas / trabalhadores / médiuns | `pessoas/*.html`, `trabalhadores/*.html`, `mediuns/*.html` | 94 |
| Configurações, agenda, relatórios, biblioteca, mala direta, permissões, usuários | respectivos diretórios | 287 |

---

## 4. Verificação

### 4.1 Telas renderizadas (ambiente descartável, login de teste)

`docs/tasks/evidencias/TASK_015/validacao_telas.txt` — 15 telas com o app real:

```
/menu 16 ícones · /caixa/fiados 54 · /cadastros/trabalhadores 149 ·
/configuracoes 20 · /caixa/pdv 6 · /relatorios 5 · /financeiro/mensalidades 5 …
rota(s) com problema: 0
```

Para **todas**: zero emojis de interface, **zero recursos externos**
(`src`/`href` para domínio de fora), nenhum template quebrado (`{{ icone` visível
ou `Undefined`).

### 4.2 Antes × depois (navegador, dados fictícios)

`docs/tasks/evidencias/TASK_015/antes-*.png` e `depois-*.png` — menu, dashboard,
PDV e menu no celular (412px):

| Tela | Antes | Depois | Variação |
|---|---|---|---|
| menu 1366px | 1366×978 | 1366×977 | **−1px** |
| dashboard 1366px | 1366×900 | 1366×900 | **0** |
| PDV 1366px | 1366×1481 | 1366×1481 | **0** |
| menu 412px | 412×2634 | 412×2633 | **−1px** |

O dashboard e o PDV ficaram **byte a byte** na mesma altura: a visão única do
dashboard, a ordem das filas e a densidade estão preservadas. O menu variou 1px
(arredondamento de linha), depois do ajuste de CSS descrito em §1.

Rolagem horizontal: **nenhuma** em desktop nem em 412px. Erros de console: zero —
o único registrado (`/favicon.ico` 404) aparece **igualmente antes e depois**, é
pré-existente e não tem relação com esta tarefa.

### 4.3 Regressão

`docs/tasks/evidencias/TASK_015/regressao_completa.txt` — **23 suítes, todas
`rc=0`, `DEV inalterado: ok`**.

Três suítes precisaram de ajuste porque **liam o emoji no texto**, não porque o
comportamento mudou:

| Suíte | O que verificava | Ajuste |
|---|---|---|
| `test_gestao_correcoes_navegador` (N1) | `"✔ 2 presentes"`, `"⏳ Sem registro"` | passaram a exigir `"2 presentes"` e `"Sem registro"` — o fato de negócio é o mesmo, sem depender do glifo |
| `test_correcoes_presenca` (C14) | `"✔ 1 presente"` | `"1 presente"` |
| `test_codigo_trabalhador` (K13) | `html.count("<img") == 2` na **página** | a contagem passou a ser feita **no bloco do cartão** (o botão "Imprimir" agora tem ícone, um `<img>` na página) — o contrato "estrela + QR, sem foto" segue idêntico |

Nenhuma asserção foi enfraquecida: as perguntas de negócio continuam as mesmas, e
o K13 ficou **mais** preciso (escopo no cartão, não na página).

---

## 5. Emojis preservados, e por quê

Busca final nos templates: **1 ocorrência**.

| Onde | Emoji | Motivo |
|---|---|---|
| `caixa/comprovante.html` (marcador "⚠ FIADO" no cupom) | ⚠ | **documento emitido** — a ordem manda preservar (não é interface) |

Preservados por decisão, sem ocorrência restante nos templates:

- **estrela dourada** ★/⭐ da identidade e do atalho — proibido pela ordem (§5);
- **fotos** do PDV, `medium.svg`, `logo.svg` — não são emojis e não foram tocadas
  (`medium.svg` continua nos 4 cartões do menu onde já estava);
- **emojis em texto digitado pelo usuário** e em dados históricos — não
  substituídos; a mala direta tem emojis na **mensagem composta pelo operador**,
  que permanece como ele escreveu;
- **strings de mensagem ao operador** onde o emoji era pontuação no meio da frase
  (ex.: `'✓ Impresso'` em retorno de botão) — virou texto simples, sem ícone, para
  não duplicar o ícone que o próprio botão já tem ao lado.

---

## 6. Aplicação no DEV

- Processo da porta 9000 conferido antes do reinício (o mesmo serviço, sem
  segunda instância, sem nginx/TLS): `.venv/bin/python -m uvicorn main:app
  --host 0.0.0.0 --port 9000`, cwd neste workspace.
- Reinício feito apenas para carregar os templates. **Nenhum dado de negócio
  escrito**, nenhuma alteração de autenticação/permissões.
- Endpoints depois do reinício: `/login` 200; `/menu`, `/dia`, `/caixa/pdv` →
  303 (login), como esperado sem sessão.
- **Sem acesso autenticado ao DEV, a conferência visual do DEV fica com o dono**;
  as capturas acima são de ambiente descartável com dados fictícios e estão
  identificadas como tal.

---

## 7. Roteiro curto para o dono (no DEV)

1. **Menu**: os cartões devem ter traços finos no lugar dos emojis, mesma ordem,
   mesmo tamanho de cartão, e as fotos dos médiuns/trabalhadores onde já estavam.
2. **Dashboard de atendimentos** (`/dia`): mesma visão única, mesma ordem de
   filas; os botões ✓/✕ de cada pessoa continuam no mesmo lugar (agora com
   traço, e com o mesmo tooltip).
3. **PDV** (`/caixa/pdv`): fotos dos produtos intactas, carrinho e pagamentos nos
   mesmos lugares, botões respondendo igual.
4. Abrir as três também **no celular**: nada deve cortar nem criar rolagem
   lateral.

Se algum ícone parecer "grande demais" ou "deslocado", é ajuste de uma linha no
CSS (`.icone` e `.menu-card-icon`) — não é mudança de estrutura.

---

## 8. Limitações e pendências

1. **Aprovação visual é do dono** — não há publicação em produção e a tarefa não
   autoriza push.
2. **`stroke-width` 1,75** não foi aplicado (ver §1): os SVGs ficaram com o traço
   do pacote. É decisão consciente, registrada para o dono escolher.
3. **Emoji do cupom** (⚠ FIADO) mantido por ser documento emitido.
4. A conferência aqui é de ambiente descartável; o DEV autenticado é do dono.
5. Não foi criada suíte nova para "reproduzir a lista de ícones" — a ordem pede
   validação proporcional, e a verificação foi dirigida (telas + capturas +
   regressão existente).
