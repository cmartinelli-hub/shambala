# Investigação — Agendamento obrigatório de 3 passes após o Acolhimento Fraterno

| Campo | Valor |
|---|---|
| Data | 2026-10-14 |
| Ambiente investigado | DEV `/nc/projetos/shambala` (cópia fiel de produção, commit `e3324c2`, banco restaurado do dump de produção) |
| Produção | **não alterada** — acesso somente leitura |
| Tipo de tarefa | Investigação (sem implementação, sem migração, sem deploy, sem commit) |
| Método | Leitura de código + consultas SQL somente-leitura + 20 testes controlados em **banco temporário descartável** (`shambala_inv_passes`), já removido |
| Resultado | **C (parcialmente implementada) + E (regra de negócio ambígua)** — ver §7 |

---

## 1. Resumo executivo

**A pergunta central da tarefa — "os três passes são agendados automaticamente?" — tem resposta diferente conforme o significado de "agendar":**

1. **Não existe** em nenhuma versão do sistema (produção, DEV commitado, DEV não commitado, branches remotas ou `stash`) código que crie **três passes magnéticos como entidade própria**. Um "passe" **não é um registro de passe**: é o **atributo `requer_passe` de cada agendamento** de sessão do plano de tratamento (`agendamentos.requer_passe`). Não há tabela, fila nem agendamento "de passe" separado.

2. **O que existe** é a regra `requer_passe = 1` nas 3 primeiras sessões, implementada em `banco.py: gerar_agendamentos_plano()` (piso introduzido no commit `7f14dd3`, de 24/08/2026 — **já presente em produção**, pois a árvore de produção é `e3324c2`, que contém esse commit).

3. **Essa geração depende inteiramente da criação de um plano de tratamento.** Não há nenhum caminho que crie passe sem plano.

4. **Há dois defeitos reais com evidência reproduzida:**
   - **D1 — o piso de 3 é contornável** pelo caminho `POST /agenda/novo`: ele grava `sessoes_com_passe = 0` **fixo no código** e usa o `requer_passe` digitado (padrão **0** no formulário), gerando sessões **sem** o piso. Três das quatro portas de criação de plano respeitam o piso; essa não. **Reproduzido em S14.**
   - **D2 — esse mesmo caminho ignora os dias de atendimento da casa**: gera datas em passos de 7 dias a partir da data digitada, sem consultar `dias_atendimento` nem marcar `encaixe`. Reproduzido em **S18** (4 sessões todas em **sábado**), contra **S19** (o mesmo pedido pelo Acolhimento Fraterno caiu corretamente em **segunda**).

5. **D3 (contexto)** — o botão "Realizado" da fila de acolhimento (`POST /dia/acolhimento/{id}/realizado`) marca `acolhimento_realizado = 1` **sem criar plano e sem gerar nenhum passe**. Se a operação real da casa usa esse botão, o esperado de "3 passes após o acolhimento" simplesmente não acontece.

6. **Dados de produção confirmam a lacuna estrutural:** dos 469 acolhimentos fraternos realizados, apenas **248 (53%)** têm plano com `data_inicio` no mesmo dia. E **94 planos** têm 3 ou mais sessões agendadas com **zero** passe marcado.

7. **A pergunta de negócio permanece aberta:** se os 3 passes são **agendamentos independentes** (não amarrados ao plano mediúnico) ou **atributos dos agendamentos do plano**, isso **não está decidido em lugar nenhum** — nem no código, nem na documentação. Como o modelo de dados não tem onde representar um passe independente, essa decisão é pré-requisito de qualquer correção. Ver §8.

---

## 2. Fluxo atual do Acolhimento Fraterno

### 2.1 Entrada na fila (check-in)

`rotas/checkin.py: salvar_checkin()` — a recepção digita o código em `codigo_acolhimento`. Isso coloca a pessoa na fila e cria o vínculo com a senha física:

```sql
INSERT INTO checkins (dia_trabalho_id, pessoa_id, hora_checkin, ..., codigo_acolhimento, ...)
```

### 2.2 Fila de acolhimento

`GET /dia/acolhimento` (`rotas/dia.py:416 fila_acolhimento`) lista três blocos: **na fila** (`codigo_acolhimento IS NOT NULL AND acolhimento_realizado = 0 AND acolhimento_cancelado = 0`), **realizados** e **cancelados**. Cada linha da fila tem dois botões (`templates/dia/acolhimento.html:43-44`):

| Botão | Rota | O que faz |
|---|---|---|
| **🗣 Atender** | `GET /dia/fraterno/{checkin_id}` | Abre a tela do Atendimento Fraterno (fluxo completo) |
| **🖨️ Ficha** | `GET /dia/acolhimento/{id}/imprimir` | Ficha A5 paisagem para preenchimento manual |

A rota `POST /dia/acolhimento/{checkin_id}/chamar` apenas marca `acolhimento_chamado = 1` e transmite a senha ao monitor.

### 2.3 Os DOIS caminhos que concluem o acolhimento

> Este é o ponto central da investigação: existem **duas** formas de marcar o acolhimento como realizado, e elas fazem coisas muito diferentes.

**Caminho A — `POST /dia/acolhimento/{checkin_id}/realizado`** (`rotas/dia.py:477 acolhimento_realizado`)

```python
conn.execute("UPDATE checkins SET acolhimento_realizado = 1 WHERE id = %s", (checkin_id,))
```

- Conclui o acolhimento **sem exigir médium, sem criar plano, sem gerar agendamento algum**. Nenhum passe é criado.
- É o que acontece se a operação usar o botão "Realizado" da fila (o template também oferece o botão "🗣 Atender" e o "🖨️ Ficha", mas o caminho de conclusão de 1 clique existe e é funcional).

**Caminho B — `GET/POST /dia/fraterno/{checkin_id}`** (`rotas/dia.py:1394 tela_fraterno` / `:1437 salvar_fraterno`) — o fluxo completo

1. Mostra os dados da pessoa (prioridade/deficiência/idoso), histórico dos últimos 5 tratamentos e o formulário do plano (`templates/dia/fraterno.html`).
2. O formulário traz: **Médium responsável (opcional)**, **Nº de sessões** (padrão `1`), **Passes iniciais** (padrão `1`, mínimo `0`), **Frequência** (semanal/quinzenal/mensal/**avulso**), **Data da 1ª sessão** (padrão hoje) e **Observações do acolhimento**.
3. Ao salvar (`salvar_fraterno`):

```python
if medium_id:                                    # ← sem médium: NADA é criado
    cur = conn.execute("""INSERT INTO planos_tratamento
        (medium_id, sessoes_total, data_inicio, frequencia, sessoes_com_passe, status)
        VALUES (%s,%s,%s,%s,%s,'ativo') RETURNING id""", (...))
    conn.execute("INSERT INTO plano_pessoas (plano_id, pessoa_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", ...)
    gerar_agendamentos_plano(conn, plano_id, inicio, frequencia, sessoes_total, sessoes_com_passe)   # ← ÚNICO ponto que agenda

conn.execute("UPDATE checkins SET acolhimento_realizado=1, observacao_acolhimento=%s WHERE id=%s", ...)
```

**Logo:** o agendamento de sessões (com o passe embutido) só existe no Caminho B **e** com médium selecionado.

### 2.4 As QUATRO portas de criação de plano

| # | Porta | Arquivo:linha | Respeita o piso de 3? | Respeita `dias_atendimento`? |
|---|---|---|---|---|
| 1 | **Acolhimento Fraterno** | `rotas/dia.py:1474` → `banco.gerar_agendamentos_plano` | ✅ **sim** | ✅ sim (`_proxima_data_trabalho`) |
| 2 | **Médium → Planos → Novo** | `rotas/mediuns.py:309` → mesma função | ✅ **sim** | ✅ sim |
| 3 | **Agenda → Novo agendamento** | `rotas/agenda.py:206-225` (SQL próprio) | ❌ **NÃO** | ❌ **NÃO** |
| 4 | **Médium → Agenda do plano → Novo** | `rotas/mediuns.py:450-453` (INSERT manual) | ➖ *somente o agendamento individual, com o `requer_passe` digitado* | ➖ idem |

> As portas 1 e 2 são as únicas que passam pela função central `gerar_agendamentos_plano()`. A porta 3 reimplementa a criação "na mão" e diverge da regra.

---

## 3. Funcionamento atual dos agendamentos de passes

### 3.1 Onde a regra é decidida — `banco.py:128-152`

```python
def gerar_agendamentos_plano(conn, plano_id, inicio, frequencia, total, sessoes_com_passe):
    """Gera agendamentos automáticos para um plano. avulso = sem geração."""
    delta_map = {"semanal": 7, "quinzenal": 14, "mensal": 28}
    delta = delta_map.get(frequencia, 0)
    if delta == 0:
        return                                    # avulso => NÃO gera nada
    dias = _dias_atendimento(conn)                 # lê dias_atendimento
    atual = _proxima_data_trabalho(inicio, dias)   # 1ª data útil (inclusive hoje)
    # As 3 primeiras sessões SEMPRE exigem passe (piso), independente de
    # sessoes_com_passe. A partir da 4ª segue o valor configurado.
    # sessoes_com_passe < 0 mantém o comportamento legado de "todas com passe".
    minimo_passe = 3
    if sessoes_com_passe < 0:
        efetivas_com_passe = total
    else:
        efetivas_com_passe = max(minimo_passe, sessoes_com_passe)
    for i in range(total):
        requer_passe = 1 if i < efetivas_com_passe else 0
        conn.execute("""INSERT INTO agendamentos (plano_id, data, status, requer_passe, encaixe)
                        VALUES (%s, %s, 'agendado', %s, 0)""", (plano_id, atual.isoformat(), requer_passe))
        atual = _proxima_data_trabalho(atual + timedelta(days=delta), dias)
```

### 3.2 Respostas objetivas às 10 perguntas

| # | Pergunta | Resposta | Evidência |
|---|---|---|---|
| 1 | Existe código para gerar automaticamente **três passes** após o Acolhimento Fraterno? | **Não como entidade "passe".** Existe marcação `requer_passe=1` nos agendamentos das 3 primeiras sessões **do plano**. Nenhum registro de passe é criado/enfileirado. | `banco.py:128-152`; ausência de qualquer tabela/rota de passe avulso; `git log -S'requer_passe' --all` |
| 2 | A geração depende da criação de um plano de tratamento? | **Sim, integralmente.** Sem `plano_id` não há agendamento — e sem médium não há plano no fraterno. | `rotas/dia.py:1460-1474` |
| 3 | Os três passes são agendamentos independentes ou atributos dos agendamentos mediúnicos? | **Atributos.** `agendamentos.requer_passe` pertence ao agendamento da sessão do plano. A independência é uma hipótese **não confirmada** pelo responsável. | schema de `agendamentos`; §8 |
| 4 | O sistema cria registros persistentes ou apenas sinaliza a obrigatoriedade? | **Apenas sinaliza** (coluna `requer_passe` em um agendamento já existente). O passe **executado** é registrado em outro lugar: `checkins.passe_realizado` + `checkins.codigo_passe`. | `banco.py:705,729`; `rotas/dia.py:402` |
| 5 | Como são calculadas as datas? | `_proxima_data_trabalho(inicio)` para a 1ª, e depois `_proxima_data_trabalho(data + delta)` com `delta` = 7/14/28 dias. | `banco.py:108-125,145-152` |
| 6 | A geração respeita os dias de funcionamento cadastrados? | **Sim no fraterno e no cadastro de médium** (via `_dias_atendimento`, com *fallback* `[0,2]` quando a tabela está vazia). **Não** no caminho `/agenda/novo`. | `banco.py:108-125`; teste S18 vs S19 |
| 7 | O que acontece quando o acolhimento é registrado **sem plano**? | `acolhimento_realizado=1` + observação são salvos; **nenhum plano e nenhum agendamento** são criados (`if medium_id:` guarda todo o bloco). | `rotas/dia.py:1460-1480`; teste S3 (HTTP 303, planos 2→2) |
| 8 | O que acontece quando um plano é **editado, cancelado ou recebe alta**? | **Alta/cancelamento**: plano muda de status e todos os `agendamentos` com `status='agendado'` passam a `'cancelado'`. **Edição**: altera `sessoes_total/frequencia/sessoes_com_passe` do plano **sem tocar nos agendamentos já gerados** — a configuração passa a divergir do que está agendado. | `rotas/mediuns.py:314-345`; `rotas/pessoas.py:486-542`; testes S11, S12 |
| 9 | Existe proteção contra duplicidade? | **Parcial.** `/agenda/novo` tem *lock* de "pessoa já tem agendamento nesta data" (bloqueia a 2ª execução — S15). Mas **reenviar o mesmo formulário do fraterno cria um segundo plano e um segundo lote de 6 agendamentos na mesma data** (S2). Não há UNIQUE em `checkins` nem em `agendamentos`. | `rotas/agenda.py:188-201`; teste S2, S15 |
| 10 | A implementação corresponde ao esperado ou existe lacuna de negócio? | **Lacuna.** Três defeitos concretos (D1, D2, D3) + ambiguidade de modelo (§8). | §7 |

### 3.3 Como um "passe obrigatório" se distingue de um passe agendado ou realizado

| Estado | Onde vive | Significado |
|---|---|---|
| **Passe obrigatório (previsto)** | `agendamentos.requer_passe = 1` | Aquela sessão do plano exige passe antes/depois |
| **Passe agendado** | não existe como conceito próprio | O agendamento **é** o agendamento da sessão; o passe está "embutido" nele |
| **Passe marcado como exigido no dia** | `checkins.codigo_passe` preenchido | A recepção deu uma senha de passe para a pessoa naquele dia |
| **Passe realizado** | `checkins.passe_realizado = 1` | A fila de passe marcou como atendido (`POST /dia/passe/{id}/realizado`) |
| **Encaixe (sem agendamento prévio)** | `agendamentos.encaixe = 1` | Sessão encaixada; na agenda é permitido (o campo é gravado, mas o *lock* de duplicidade ignora `encaixe`) |

---

## 4. Comparação DEV × produção

Método: comparação por **md5 da árvore inteira** (não por `git log`), conforme orientação da tarefa.

| Item | Resultado |
|---|---|
| **Árvore de produção** | `/opt/shambala` — 202 arquivos, **todos com md5 idêntico** ao commit `e3324c2` do DEV. Confirmado que o `.git` do servidor está em `7f14dd3` (12 commits atrás) por resíduo de deploy via `rsync` — o `.git` **não** reflete o que está implantado. |
| **Versão implantada realmente** | `e3324c2`, que **inclui** o commit `7f14dd3` ("passe obrigatório nas 3 primeiras sessões de todo plano (piso)", 24/08/2026). |
| **Diferenças nas funções de agendamento** | **Nenhuma.** `gerar_agendamentos_plano`, `_proxima_data_trabalho`, `_dias_atendimento` e `rotas/agenda.py:novo_agendamento` são byte-idênticos entre DEV e produção. |
| **Implementações somente no DEV** | As 21 alterações **não commitadas** (endurecimento de segurança/auditoria/hardening do PDV). Verificação dirigida: o patch contém **0 ocorrências** de `passe`, `acolhimento`, `agendamento` ou `plano` — **não há correção pendente relacionada a passes**. |
| **Commit `7f14dd3` (piso do passe)** | Toca apenas `banco.py` (+9/-1), alterando a linha do `requer_passe`. **Já está em produção** (está na árvore implantada). |
| **`stash` e branch órfã `master`** | O `stash` não menciona passe/acolhimento/agendamento. A `master` (histórico não relacionado) tem a versão **antiga** de `gerar_agendamentos_plano` (`i < sessoes_com_passe`, sem piso) e nenhuma criação de passe. |
| **Commits relacionados (todas as refs)** | `7f14dd3` (piso do passe), `42f71d7`/`58e255d` (ficha de acolhimento — já em `main`), `e3324c2`/`3407621` (acolhimento) — todos **já em produção**. |
| **Remotos** | `github/main` está 13 commits atrás de `e3324c2`; `gitproprio/main` 20 atrás. Nenhuma referência remota contém implementação de passe mais recente que a local. |

**Conclusão da comparação: produção ≡ DEV commitado. Não existe correção de passes "presa" no DEV.** O defeito está em produção e também no DEV — não é um problema de sincronização.

---

## 5. Evidências encontradas

### 5.1 Código — ponto exato de cada falha

**D1 — piso contornável em `/agenda/novo`** (`rotas/agenda.py:206-225`):

```python
# Cria plano avulso para a pessoa + médium
cur = conn.execute(
    """INSERT INTO planos_tratamento
       (medium_id, sessoes_total, sessoes_realizadas, data_inicio,
        frequencia, status, sessoes_com_passe)
       VALUES (%s, %s, 0, %s, %s, 'ativo', 0)          # ← sessoes_com_passe = 0 HARDCODED
       RETURNING id""", (medium_id, qtd_sessoes, data_inicio.isoformat(), frequencia))

# Criar agendamentos
for d in datas:
    conn.execute(
        "INSERT INTO agendamentos (plano_id, data, status, requer_passe, encaixe) VALUES (%s,%s,%s,%s,%s)",
        (plano_id, d, "agendado", requer_passe, encaixe)   # ← requer_passe vem do formulário (padrão 0)
    )
```

- Nunca chama `gerar_agendamentos_plano()` → o `max(3, ...)` do piso **nunca é avaliado**.
- `requer_passe: int = Form(0)` (`rotas/agenda.py:152`) → o formulário (`templates/agenda/index.html`) tem a marcação **desmarcada por padrão**.
- Assinatura: `requer_passe` é um único valor aplicado a **todas** as sessões — incompatível com "as 3 primeiras".

**D2 — `/agenda/novo` ignora os dias de atendimento** (`rotas/agenda.py:183-203`):

```python
datas = []
data_atual = data_inicio
for i in range(qtd_sessoes):
    ...
    datas.append(data_atual.isoformat())
    data_atual += timedelta(days=delta_dias)      # ← passo fixo, sem _proxima_data_trabalho
```

Nenhuma consulta a `dias_atendimento`; o campo `encaixe` é gravado mas não normaliza a data.

**D3 — botão "Realizado" não gera nada** (`rotas/dia.py:477-485`): apenas `UPDATE checkins SET acolhimento_realizado = 1`. Sem plano, sem agendamento, sem passe.

**Ponto positivo a preservar:** o piso realmente funciona nas portas 1 e 2 — `minimo_passe = 3` e `efetivas_com_passe = max(3, sessoes_com_passe)` (`banco.py:140-146`).

### 5.2 Banco de produção (somente leitura, sem dados pessoais)

| Métrica | Valor | Leitura |
|---|---:|---|
| Agendamentos com `requer_passe=1` | 730 de 2.867 | 25% do total |
| Acolhimentos fraternos realizados (`acolhimento_realizado=1` + `codigo_acolhimento`) | 469 | base da regra |
| Destes, com plano iniciado **no mesmo dia** | **248 (53%)** | **47% sem plano no dia do acolhimento** |
| Planos com ≥3 agendamentos e **zero** passe | **94** | piso não aplicado |
| Planos com `sessoes_com_passe = 0` | **1.401 de 1.855 (76%)** | maioria é do caminho `/agenda/novo` |
| Planos com `sessoes_com_passe >= 3` mas `requer_passe < 3` | **24** | gerados antes de 24/08/2026 (sem retroatividade) |
| Planos com `data_inicio` posterior ao piso (24/08/2026) | 187 com ≥3 sessões — **59** com piso correto, **17** sem | efeito combinado das 4 portas |
| Sessões com `requer_passe=1` e status `realizado` | 345 | passe exigido chega a ser cumprido |

**Assinatura inequívoca do caminho `/agenda/novo`** (`sessoes_com_passe=0` → `requer_passe=0` em todas as sessões): 1.119 planos com 1 sessão/0 passes, 121 com 2/0, 50 com 3/0, 30 com 4/0, 7 com 5/0, 3 com 7/0, 2 com 6/0. Enquanto isso, os planos com `sessoes_com_passe=3` aparecem com **exatamente 3** passes (73 planos de 6 sessões, 4 planos de 7 sessões) — o piso funcionando.

### 5.3 Retrocompatibilidade

O piso entrou em 24/08/2026. Planos criados antes mantêm a geração antiga: dos 1.393 planos anteriores, 95 têm piso e 106 não. **Não há retroação** — o esperado, mas explica parte dos 94 planos sem passe.

---

## 6. Resultado dos testes controlados

**Ambiente:** banco temporário `shambala_inv_passes` (recriado do zero, schema gerado pela própria aplicação), app uvicorn em `127.0.0.1:9010`, login HTTP real. **O banco de DEV não foi tocado** (permaneceu com 1.855 planos / 2.867 agendamentos / 664 pessoas / 2.807 check-ins). Ao final, **app encerrado e banco temporário removido**.

| # | Cenário | Resultado observado | Veredito |
|---|---|---|---|
| **S1** | Fraterno com plano: 6 sessões, `sessoes_com_passe=1` (padrão da tela), semanal | 6 agendamentos, **3 com `requer_passe=1`**, todas as datas em **quarta** | ✅ piso funciona |
| **S2** | Reenviar o MESMO fraterno | **Criou um 2º plano e mais 6 agendamentos nas mesmas datas** | ❌ **sem proteção de duplicidade** |
| **S3** | Fraterno **sem** médium (`medium_id` vazio) | HTTP 303; `acolhimento_realizado=1`, observação salva; **0 planos, 0 agendamentos** | ⚠️ comportamento definido, mas silencioso |
| **S4** | Plano com **2** sessões, `sessoes_com_passe=1` | 2 agendamentos, **2 com passe** (piso limitado ao total) | ✅ coerente |
| **S5** | `sessoes_com_passe=0` com 5 sessões | 5 agendamentos, **3 com passe** (piso venceu o 0) | ✅ piso correto |
| **S6** | `sessoes_com_passe=-1` com 4 sessões | 4 agendamentos, **4 com passe** (legado) | ✅ coerente |
| **S7** | Frequência **avulso** | **0 agendamentos** | ✅ coerente (`delta == 0 → return`) |
| **S8** | Dias de atendimento = só **quinta** | 4 agendamentos, todas as datas em **Thursday**, 3 com passe | ✅ respeita `dias_atendimento` |
| **S9** | Reagendar o 1º agendamento (+3 dias, cai em **sábado**) | **Deslocou os 6 agendamentos para sábados** (mesmo dia da semana) | ❌ reagendamento fura `dias_atendimento` |
| **S10** | Cancelar um agendamento | Status → `cancelado`, sem renumeração | ✅ |
| **S11** | Editar plano (`sessoes_com_passe` 1→3) na ficha da pessoa | `sessoes_com_passe` mudou para 3, **agendamentos intactos** (alguns podem ficar sem passe) | ⚠️ configuração passa a divergir do agendado |
| **S12** | Alta no plano | `status='alta'`; **todos** os agendamentos → `cancelado` | ✅ |
| **S13/S16** | 3 "faltas" onde a 3ª está no **futuro** | Plano **permanece `ativo`** | ⚠️ ver S20 |
| **S14** | **`/agenda/novo`** com 4 sessões, caixa "requer passe" desmarcada | **4 agendamentos, 0 com passe** e `sessoes_com_passe=0` gravado no plano | ❌ **D1 confirmado** |
| **S15** | Repetir `/agenda/novo` para a mesma pessoa/data | Bloqueado (pessoa permaneceu com 1 plano) | ✅ *lock* funciona |
| **S17** | 3 faltas, 3ª agendada no **futuro** | Plano **não** foi cancelado (R1) | ⚠️ ambíguo |
| **S18** | `/agenda/novo` partindo de um **sábado** | **4 agendamentos, todos em Saturday** | ❌ **D2 confirmado** |
| **S19** | Mesmo pedido pelo **fraterno** partindo do sábado | 1ª sessão em **Monday** (`_proxima_data_trabalho`) | ✅ contraste que isola o defeito |
| **S20** | 3 faltas **reais**, todas no passado | Plano **`cancelado`** após a 3ª; 1 agendamento futuro cancelado | ⚠️ difere de S17 |

**Conclusões dos testes:**
- O núcleo da regra (`gerar_agendamentos_plano`) está **correto** e é **determinístico** (S1, S4–S8).
- O defeito está **no caminho paralelo** `/agenda/novo` (S14, S18) — que é justamente o usado pela tela **Agenda**.
- Reagendamento (S9) e edição de plano (S11) não revalidam a regra.
- Sem médium (S3) ou pelo botão "Realizado" (D3), nada é gerado.
- R1 (faltas): o `LIMIT 3` não filtra `data <= hoje` de forma confiável; comportamento observado é inconsistente entre S17 e S20 — **registrado como dúvida, não como defeito afirmado** (a leitura de `status` das 3 últimas linhas por data pode incluir agendamentos futuros já marcados).

---

## 7. Diagnóstico e causa provável

### Classificação: **C — parcialmente implementada**, com **E — ambiguidade de regra de negócio** em aberto

**Não pode ser A** (produção ≡ DEV commitado, sem correção pendente) nem **D** (a marcação existe e funciona em duas portas).

### Causa provável mais direta do relato operacional

O sintoma "os três passes não estão sendo agendados" decorre de **uma de três portas**, todas com causa identificada:

| Situação operacional | Causa | Evidência |
|---|---|---|
| Usaram a tela **Agenda → Novo agendamento** | `sessoes_com_passe` gravado como **0 fixo** + `requer_passe` do formulário (padrão 0) — o piso nunca é avaliado | D1; S14 |
| Usaram **"Realizado"** na fila de acolhimento | A rota só marca o acolhimento como realizado; não cria plano nem agendamento | D3; `rotas/dia.py:477` |
| Usaram o fraterno **sem escolher médium** | `if medium_id:` → nada é criado; o `acolhimento_realizado` é salvo normalmente, sem aviso | S3; `rotas/dia.py:1460` |

E, ainda que o piso funcione, ele **não cria três passes**: apenas marca 3 dos agendamentos do plano. **Se a expectativa do responsável for "3 passes independentes do tratamento mediúnico", então a funcionalidade é, de fato, inexistente (cenário D).**

---

## 8. Regras de negócio que precisam de confirmação

> Nenhuma destas perguntas tem resposta no código ou na documentação. **Não implementar antes de decidir.**

| # | Dúvida | Por que importa |
|---|---|---|
| **R1** | Os 3 passes são **agendamentos independentes** (não vinculados ao plano mediúnico) ou **atributos das 3 primeiras sessões do plano**? | Determina se a correção é ajustar a marcação (pequena) ou criar modelo/tabela/fila de passe (grande). O modelo atual **não tem onde representar** um passe independente. |
| **R2** | Passe obrigatório exige **plano**? Um acolhimento **sem** plano deve gerar 3 passes mesmo assim? | Hoje não gera nada. Se a resposta for "sim, gera", é preciso decidir a que o passe se vincula (pessoa? dia? senha?). |
| **R3** | O botão **"Realizado"** da fila deve criar o plano/os passes, ou deve ser removido/renomeado para deixar claro que é só a conclusão do acolhimento? | Hoje é uma porta silenciosa para "acolhimento sem consequência". |
| **R4** | O piso de 3 deve ser aplicado **também** no caminho `/agenda/novo` (Agenda)? E também quando `qtd_sessoes < 3`? | Se sim, é correção pontual em `rotas/agenda.py`. |
| **R5** | Um passe deve poder ser **reagendado** para um dia sem atendimento? | S9 mostrou que o reagendamento desloca tudo para o mesmo dia da semana, sem validar `dias_atendimento`. |
| **R6** | Ao **editar** um plano, os agendamentos já gerados devem ser recalculados (inclusive `requer_passe`)? | S11 mostrou que não são; a configuração passa a divergir. |
| **R7** | A configuração "Passes iniciais" (padrão **1** no fraterno) deve continuar existindo se o piso é sempre 3? Ou deve ser removida/renomeada? | O campo é redundante com o piso e induz o operador ao erro. |
| **R8** | Duplicidade: reenviar o formulário do fraterno deve ser bloqueado? | S2 criou plano duplicado e 6 agendamentos repetidos sem aviso. |
| **R9** | As 3 faltas consecutivas devem considerar apenas sessões **passadas**? | S17 vs S20 divergiram. |

---

## 9. Proposta técnica de correção (sem implementação)

> **Ordem de execução: primeiro decidir R1 (e R2/R3); a escolha entre os blocos A e B depende disso.**

### Bloco A — Correções pontuais (aplicáveis independentemente de R1)

**A1. Fazer `/agenda/novo` passar pela regra central** — `rotas/agenda.py:206-225`
Substituir o `INSERT` manual de agendamentos pela chamada a `banco.gerar_agendamentos_plano()`, e gravar `sessoes_com_passe` coerente com o formulário (ou remover o parâmetro do formulário). Efeito: o caminho Agenda passa a respeitar piso **e** `dias_atendimento` de uma só vez (fecha D1 e D2). *Atenção: hoje `/agenda/novo` permite datas fora dos dias de atendimento de propósito? Se sim, precisa de `encaixe=1`.*

**A2. Alinhar a assinatura do formulário** — `templates/agenda/index.html` + `rotas/agenda.py:152`
`requer_passe` é um único valor para todas as sessões. Se o piso é do sistema, o campo deveria ser suprimido (ou reinterpretado como "quantas iniciais com passe", mínimo 3), espelhando o fraterno.

**A3. Validação de piso em um único lugar**
Extrair `efetivas_com_passe = max(3, sessoes_com_passe)` para uma função única em `banco.py` e usá-la em **todas** as inserções de agendamento (hoje há 3 pontos: `banco.py:145`, `rotas/agenda.py:221`, `rotas/mediuns.py:450`). Isso impede que um 5º caminho reintroduza o defeito.

**A4. Reagendamento não deve furar os dias de atendimento** — `rotas/mediuns.py:500-541`
Ou rejeitar/ajustar datas fora de `dias_atendimento` (ou exigir `encaixe=1` explícito).

**A5. Recalcular agendamentos ao editar o plano** — `rotas/pessoas.py:486-518`
Reaplicar `requer_passe` às sessões futuras `agendado` conforme a nova configuração, ou bloquear a edição de `sessoes_com_passe` quando já houver agendamentos gerados.

**A6. Bloqueio de duplicidade do fraterno** — `rotas/dia.py:1437`
Antes de criar o plano, verificar se a pessoa já tem plano `ativo` iniciado no dia (ou já concluído com o mesmo médium na mesma data) e redirecionar com mensagem.

**A7. Tornar o caminho "sem médium" explícito** — `rotas/dia.py:1437-1480`
Se R2 definir que passe sem plano é válido, implementar; se não, dar retorno visual claro ("acolhimento concluído sem plano — nenhum passe gerado"), para não parecer que funcionou.

### Bloco B — Se R1 definir "3 passes independentes do plano mediúnico" (mudança de modelo)

1. **Modelo de dados:** decidir onde o passe vive. Opções a avaliar (não escolher sem a decisão de negócio):
   - (i) `agendamentos` com um tipo (`tipo='passe'`), permitindo passe com ou sem `plano_id`;
   - (ii) tabela própria `passes_agendados` (pessoa, data, status, médium opcional, `encaixe`);
   - (iii) reaproveitar `checkins` (senha de passe + `agendamentos` desvinculados).
2. **Migração idempotente** em `banco._migrar()` (padrão do projeto: `ALTER TABLE ... ADD COLUMN` verificado em `information_schema`; novas tabelas em `criar_tabelas()`).
3. **Gatilho único:** criar os 3 passes no mesmo ponto em que o acolhimento é concluído (idealmente em uma função única `concluir_acolhimento()` chamada pelos dois caminhos), com parâmetro explícito de médium/plano opcional.
4. **Idempotência:** chave natural (pessoa + data + origem) para não duplicar em reenvios.
5. **Integração com as filas:** decidir se o passe agendado aparece na fila `/dia/passe` do dia ou se é apenas um compromisso de agenda (hoje a fila depende de `checkins.codigo_passe`).
6. **Telas:** exibir os passes no dashboard/agenda e no histórico do plano.
7. **Compatibilidade:** decidir o que fazer com os 1.401 planos com `sessoes_com_passe=0` e os 94 planos sem passe — **não corrigir retroativamente sem autorização**.

---

## 10. Riscos de regressão e cuidados para uma futura publicação

| # | Risco | Mitigação sugerida |
|---|---|---|
| **1** | **`/agenda/novo` é usado de propósito** para marcar datas fora dos dias de atendimento (encaixe manual). Passar a normalizar datas pode **mudar o comportamento esperado** pela recepção. | Confirmar com o responsável (R4) **antes**; se for intencional, manter a data e gravar `encaixe=1`, aplicando o piso de passe separadamente. |
| **2** | `sessoes_com_passe = 0` é o default de **1.401 planos em produção**. Aplicar o piso retroativamente mudaria relatórios e contagens de passe. | **Não** fazer retroativo; aplicar só em novos planos e, se desejado, oferecer um utilitário explícito e auditável. |
| **3** | Alterar `gerar_agendamentos_plano` afeta **as duas portas que hoje funcionam** (fraterno e cadastro de médium) — é o núcleo do sistema de agenda. | Extrair a regra para uma função (`A3`) e cobrir com testes **antes** de mexer; comparar contagem de agendamentos gerados em planos de teste. |
| **4** | O botão "Realizado" (D3) pode estar **em uso real**; mudar seu efeito alteraria o fluxo da recepção em pleno dia de atendimento. | Publicar fora do horário de atendimento; se for criar plano no clique, exigir médium na tela (hoje a fila não pede). |
| **5** | Recalcular `requer_passe` ao editar (A5) pode **remover passe** de sessões já realizadas. | Limitar a recalculação às sessões com `status='agendado'` e **data futura**; nunca tocar em `realizado`/`faltou`. |
| **6** | A migração roda **automaticamente no startup** (`criar_tabelas` → `_migrar`) — em produção, reiniciar já aplica schema. | Qualquer coluna/tabela nova deve ser segura e idempotente; **testar o boot** em cópia do banco de produção antes do deploy. |
| **7** | Sessões em memória: o reinício do serviço **desloga todos os usuários** e, se houver dia aberto, a operação é interrompida. | Publicar em janela combinada; avisar a recepção. |
| **8** | Deploy é feito por **`rsync`** (`atualizar.sh`), não por git — o `.git` do servidor não reflete a versão. | Não confiar em `git log` na produção (já confirmado nesta investigação); validar por md5 da árvore após publicar. |
| **9** | Existem hoje **21 arquivos modificados e não commitados** no DEV (endurecimento de segurança). Publicar qualquer correção de passe antes disso mistura dois assuntos. | Separar: commitar/publicar o hardening em conjunto próprio; a correção de passe deve entrar como mudança isolada e revisável. |
| **10** | Nenhuma suíte de testes automatizados existe no repositório. | Antes da correção, criar testes (pytest) para os cenários S1–S20 desta investigação, usando banco temporário — o procedimento usado aqui já é reutilizável. |

### Cuidados de operação (restrições desta tarefa, mantidas)

- **Produção intacta:** nenhuma escrita, nenhum restart, nenhum deploy.
- **DEV intacto:** banco de DEV com as contagens originais; nada commitado; `stash` preservado.
- **Testes** rodaram em banco descartável `shambala_inv_passes` e app na porta `9010`, **ambos já removidos**.
- Nenhum comando de `git reset`, `clean` ou `stash` foi executado.

---

## Anexo A — Mapa de funções relevantes

| Função | Arquivo:linha | Papel |
|---|---|---|
| `chamar_acolhimento` | `rotas/dia.py:460` | Marca `acolhimento_chamado=1` e transmite a senha |
| **`acolhimento_realizado`** | `rotas/dia.py:477` | **D3** — só marca realizado (sem plano/passe) |
| `acolhimento_cancelar` / `acolhimento_desfazer_cancelamento` | `rotas/dia.py:489` / `:508` | Cancela/reativa por senha (titular + acompanhantes) |
| `fila_acolhimento` | `rotas/dia.py:416` | Lista fila / realizados / cancelados |
| **`tela_fraterno`** | `rotas/dia.py:1394` | Formulário do plano (médium opcional, sessões, passes, frequência, data) |
| **`salvar_fraterno`** | `rotas/dia.py:1437` | **Porta 1** — cria plano + chama `gerar_agendamentos_plano` |
| **`gerar_agendamentos_plano`** | `banco.py:128` | **Núcleo da regra do passe** (piso 3) |
| `_dias_atendimento` | `banco.py:108` | Lê dias de atendimento (fallback `[0,2]`) |
| `_proxima_data_trabalho` | `banco.py:116` | Próxima data útil (inclusive a de partida) |
| `novo_plano` (médium) | `rotas/mediuns.py:278` | **Porta 2** — cria plano + gera agendamentos |
| **`novo_agendamento` (agenda)** | `rotas/agenda.py:147` | **Porta 3 — D1/D2** (`sessoes_com_passe=0`, sem `dias_atendimento`) |
| `novo_agendamento` (agenda do plano) | `rotas/mediuns.py:437` | **Porta 4** — INSERT individual |
| `dar_alta` / `cancelar_plano` | `rotas/mediuns.py:314` / `:331` | Status do plano + cancela agendamentos `agendado` |
| `registrar_falta` | `rotas/mediuns.py:470` | 3 faltas → cancelamento (R9) |
| `reagendar` | `rotas/mediuns.py:500` | Desloca este + futuros (S9) |
| `editar_plano` / `alta_plano` | `rotas/pessoas.py:486` / `:521` | Edição sem recalcular agendamentos (S11) |
| `fila_passe` / `passe_realizado` / `passe_desfazer` | `rotas/dia.py:381` / `:391` / `:402` | Passe **realizado** (depende de `checkins.codigo_passe`) |

## Anexo B — Como reproduzir

```bash
# banco temporário (nunca usar o banco de DEV)
createdb -O shambala shambala_inv_passes
cd /nc/projetos/shambala
SHAMBALA_DB_NAME=shambala_inv_passes SHAMBALA_DB_HOST=/var/run/postgresql \
  .venv/bin/python3 -m uvicorn main:app --host 127.0.0.1 --port 9010
# scripts dos cenários: /tmp/inv_tests.sh, /tmp/inv_tests2.sh, /tmp/inv_tests3.sh
# ao final:
dropdb shambala_inv_passes
```

Consulta que isola o defeito D1 (somente leitura, roda em produção):

```sql
WITH x AS (SELECT plano_id, count(*) n, count(*) FILTER (WHERE requer_passe=1) c1
           FROM agendamentos GROUP BY 1)
SELECT pt.sessoes_com_passe AS cfg, x.n, x.c1, count(*) AS planos
FROM planos_tratamento pt JOIN x ON x.plano_id = pt.id
WHERE pt.sessoes_com_passe = 0
GROUP BY 1,2,3 ORDER BY planos DESC;
-- resultado em produção: 1.119 planos (n=1,c1=0), 121 (2,0), 50 (3,0), 30 (4,0)...
-- => planos com 3+ sessões e ZERO passe, todos com cfg=0 (caminho /agenda/novo)
```

---

## Aguardando definição

Esta investigação **para aqui**, conforme a tarefa. Os pontos que dependem de decisão do responsável são **R1, R2, R3 e R4** (§8) — em especial **R1**, que define se a correção é o Bloco A (ajustar a marcação existente) ou o Bloco B (criar o conceito de passe independente). Nenhuma alteração foi implementada.
