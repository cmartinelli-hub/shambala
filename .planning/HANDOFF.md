# Handoff: Ficha de Acolhimento Fraterno (Task #5)

## O que fazer
Adicionar botão "Imprimir Ficha" na tela `/dia/acolhimento` que gera documento A5 paisagem com dados preenchidos automaticamente e campos em branco para atendente preencher.

## Arquivo exemplo
- **Localização:** `/home/claudio/projetos/shambala/ficha-acolhimento.svg`
- **Tamanho:** 210mm × 148mm (A5 paisagem)
- **Estrutura:** Título "FICHA DE ACOLHIMENTO FRATERNO" + campos com labels

## Campos a preencher automaticamente
1. **Nome** — `pessoas.nome_completo`
2. **Data do atendimento** — data do dia (TODAY)
3. **Data de Nascimento** — `pessoas.data_nascimento`
4. **Idade** — calculada a partir de data_nascimento
5. **Telefone** — `pessoas.telefone`
6. **E-mail** — `pessoas.email` (se existir campo)

Demais campos (Avaliação, Descrição, Passe Magnético, Médium, Atendente) = em branco para preencher.

## Arquivos a criar/modificar

### 1. Nova rota em `rotas/dia.py` (após linha ~430)
```python
@router.get("/acolhimento/{checkin_id}/imprimir", response_class=HTMLResponse)
async def imprimir_ficha_acolhimento(request: Request, checkin_id: int):
    atendente, redir = _guard(request)
    if redir:
        return redir
    
    with conectar() as conn:
        row = conn.execute("""
            SELECT c.id, c.hora_checkin,
                   p.id as pessoa_id, p.nome_completo, p.data_nascimento,
                   p.telefone, p.email
            FROM checkins c
            JOIN pessoas p ON p.id = c.pessoa_id
            WHERE c.id = %s
        """, (checkin_id,)).fetchone()
        if not row:
            return RedirectResponse(url="/dia/acolhimento", status_code=303)
    
    # Calcular idade
    from datetime import date
    if row['data_nascimento']:
        dn = date.fromisoformat(row['data_nascimento'])
        hoje = date.today()
        idade = hoje.year - dn.year - ((hoje.month, hoje.day) < (dn.month, dn.day))
    else:
        idade = ''
    
    data_atendimento = date.today().strftime('%d/%m/%Y')
    
    return templates.TemplateResponse("dia/imprimir_ficha_acolhimento.html", {
        "request": request,
        "atendente": atendente,
        "pessoa": dict(row),
        "idade": idade,
        "data_atendimento": data_atendimento,
    })
```

### 2. Template `templates/dia/imprimir_ficha_acolhimento.html` (novo)
Criar HTML printável com:
- `@page { size: A5 landscape; margin: 10mm; }`
- Campos preenchidos com dados
- Linhas em branco para campos do atendente
- Botão "Imprimir" (hide on print)

### 3. Template `templates/dia/acolhimento.html` (modificar)
Adicionar botão ao lado de "Atender" para cada fila:
```html
<form method="get" action="/dia/acolhimento/{{ c.id }}/imprimir" style="display:inline">
    <button type="submit" class="btn btn-secundario btn-sm">🖨️ Ficha</button>
</form>
```

## Banco de dados
- Verificar se coluna `email` existe em `pessoas`
- Se não existir, ignorar e-mail na ficha

## Deploy
- Teste em `/dia/acolhimento` → selecionar pessoa → clicar "🖨️ Ficha"
- Deve abrir página imprimível com dados preenchidos
- Ctrl+P → PDF A5 paisagem

## Status
- ✅ Autocomplete pessoa implementado (commit 05d4d84)
- ⏳ Ficha acolhimento: aguardando implementação
