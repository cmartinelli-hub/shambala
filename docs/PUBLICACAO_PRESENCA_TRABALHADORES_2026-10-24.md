# Publicação — Presença de Trabalhadores (TASK_001–004) em produção

**Data:** 2026-10-24 · **Servidor:** shambala (10.100.0.4) · **Endereço público:** https://shambala.webhop.com.br
**Autorização:** publicação das TASK_001–004 homologadas pelo dono após conferência no DEV, sem a conta exclusiva do aparelho (não implementada — fluxo de ativação existente preservado).

## 1. O que foi publicado

Produção estava exatamente na versão do commit `7238c80` (provado por manifesto md5 arquivo a arquivo — o `.git` do servidor está defasado e não reflete o deploy). A publicação trouxe `7238c80..b5bca85` (17 commits; funcional: TASK_001 presença com data própria, TASK_002 códigos de 4 dígitos e carteirinhas, TASK_003 terminal QR/teclado, TASK_004 gestão do dia, correções auditadas e relatórios, mais os acabamentos e a semântica do seletor homologados).

Conjunto exato aplicado — 30 arquivos de código/templates/estáticos via `git archive HEAD` (tar `/tmp/deploy_2026-10-24.tar`, sha256 `c0eadf11…e1d3`, cópia local removida após o uso), **sem rsync `--delete`** e sem tocar em `static/fotos` (35 arquivos de produção preservados):

- `banco.py`, `main.py`, `templates_config.py`, `preparar_codigos_trabalhadores.py`
- `rotas/`: `permissoes.py`, `relatorios.py`, `terminal.py` (novo), `trabalhadores.py`
- `servico_`: `chegada`, `codigo_trabalhador`, `correcao_presenca`, `presenca` (novos)
- `static/`: `css/base.css`, `css/carteirinha.css` (novo), `css/terminal.css` (novo), `js/terminal.js` (novo), `js/vendor/jsqr-1.4.0/` (novo), `logos/estrela_dourada.svg` (novo)
- `templates/`: relatórios (2), `terminal/leitura.html` (novo), trabalhadores (`_cartao`, `carteirinha`, `carteirinhas_lote`, `correcao` — novos; `checkin`, `form`, `lista`, `presenca` — alterados)
- **Excluído:** `templates/trabalhadores/dia_sem_abertura.html` (removido no servidor, como no git)

`requirements.txt` inalterado no intervalo → nenhuma dependência nova. md5 dos arquivos no servidor confere com `git show HEAD:<arquivo>` (amostra: `main.py`, `banco.py`, `rotas/relatorios.py`, `correcao.html` — idênticos). Dono dos arquivos corrigido para `shambala:shambala`; `__pycache__` limpo; serviço `shambala` reiniciado (unit e porta 8000 existentes, sem nginx/certificado/porta/instância nova).

## 2. Backup e recuperação

Em `shambala:/root/backups/publicacao-2026-10-24/` (espelhado no projeto em `docs/tasks/backups/publicacao-2026-10-24/`, ignorado pelo git por conter dados reais; sha256 conferidos iguais entre as duas cópias):

| Arquivo | sha256 |
|---|---|
| `shambala_pre_publicacao.dump` | `4dbc503c563c210750ca0c6c3b5e8b9e112a3c791f59e8035fbcdb5cf55e337f` |
| `shambala_pre_publicacao.sql` | `a35f5615a28c600aa3aac8a945898cd26a8e8a6fd0b9887373504569c01bcd64` |
| `arquivos_pre_publicacao.tar.gz` (32 MB: main, banco, rotas, templates, static) | `8f89274248ac67667aeba338aeded092056ef5939f039c3a5c5b227c48f6d1ad` |
| `manifest_antes.md5` / `CHECKSUMS.sha256` | inventário da versão anterior |

**Restauração validada em banco descartável:** o dump foi restaurado localmente (`shambala_test_restore`) e reproduziu as contagens de produção exatamente (trabalhadores 70, presenças 20, dias 72, pessoas 678, check-ins 2918, movimentações 862, vendas 1859, 41 tabelas). O banco de ensaio foi removido ao encerrar.

**Recuperação (se necessário):** `pg_restore -c` do dump OU restaurar os arquivos do tar + `systemctl restart shambala`. **Atenção:** restaurar o banco perde tudo o que foi escrito após 2026-10-24 10:0x (presenças registradas no terminal, correções, novos cadastros, códigos emitidos).

## 3. Pré-check de produção (antes de qualquer alteração)

Auditoria somente-leitura no banco real — nada presumido do DEV:

- presenças: 20 linhas, **todas** com `dia_trabalho_id` válido e data legível; nenhuma órfã; nenhuma dupla trabalhador+data derivada; unicidade vigente era `(trabalhador_id, dia_trabalho_id)`.
- trabalhadores: 70 ativos / 0 inativos; zero tabela `trabalhador_codigo` (ainda não existia).
- **Nenhuma ambiguidade bloqueadora** → migração segura autorizada pelos critérios da TASK_001.

## 4. Ensaio completo antes do deploy

O app de HEAD foi subido (porta 9012, descartável local) sobre a cópia restaurada do banco de produção: startup limpo, migração produzindo exatamente o resultado depois observado em produção; preparação de códigos ensaiada (70 gerados; segunda execução, zero). Nada disso foi copiado para produção — apenas o código.

## 5. Migração aplicada em produção (dados reais preservados)

Verificado após o restart, via journalctl (zero traceback/erro) e SQL:

- `trabalhador_presenca` ganhou `origem_chegada` e `data`; **20/20 presenças com data derivada do dia de atendimento, nenhuma nula**; `dia_trabalho_id` agora opcional.
- Unicidade nova `(trabalhador_id, data)` criada (`trabalhador_presenca_data_unique`); antiga substituída.
- `trabalhador_codigo` e `trabalhador_presenca_correcao` criadas; FK de `trabalhador_codigo` com `confdeltype='r'` (RESTRICT).
- Contagens após tudo: `70 | 20 | 72 | 678 | 2918 | 862 | 1859 | atendentes 22` — idênticas à baseline. Nenhuma presença, pessoa, check-in, financeiro ou venda alterado.
- Nenhum dado do DEV (banco, códigos, usuários, sessões, configurações) foi copiado para produção.

## 6. Códigos de carteirinha (execução explícita no servidor)

`preparar_codigos_trabalhadores.py` como usuário `shambala`, banco `shambala` — somente quantidades:

| Execução | Resultado |
|---|---|
| simulação | ativos 70, emitidos 0, faltando 70 (nada escrito) |
| `--aplicar` | **gerados 70**, já existiam 0 |
| segunda execução | emitidos 70 (ativos 70), faltando 0 — **zero novos** (idempotente) |

Atribuição real registrada: `atendente_id` nulo + `origem = preparacao_local` (execução técnica de publicação, como o modelo prevê — sem atribuir a usuário arbitrário). 70 códigos únicos de 4 dígitos.

## 7. Verificação pós-publicação via túnel Cloudflare existente

`https://shambala.webhop.com.br` (cloudflared e unit `shambala` preexistentes; nothing installed):

| Verificação | Resultado |
|---|---|
| `GET /login` | 200 |
| `GET /terminal/` sem sessão de terminal | 303 → login |
| `GET /relatorios/presenca-trabalhadores`, `/checkin-trabalhador` sem sessão | 303 (rotas protegidas) |
| `POST /terminal/chegada` sem sessão de terminal | **401 `sessao_encerrada`** (isolamento da sessão do terminal) |
| `POST /terminal/ativar` sem sessão de administrador | 303 |
| Estáticos novos (`terminal.css`, `jsQR.js`, `estrela_dourada.svg`, `carteirinha.css`) | 200 |
| `static/fotos` de produção | 35 arquivos preservados |
| Log do serviço após restart | startup completo, zero erro |

## 8. Limitações e constatações registradas

- **Sessões em memória:** o restart deslogou todos os usuários (comportamento conhecido do sistema).
- **Cookies sem flag `Secure`:** a app só marca `Secure` com `SHAMBALA_HTTPS=true`, não definido em produção. Os cookies são httpOnly + SameSite=Lax e a sessão do terminal é cookie dedicado isolado da sessão comum. **Não alteramos a configuração** (fora do escopo autorizado); recomendação: ligar a variável num momento de manutenção aprovado pelo dono — atrás do túnel HTTPS isso não desloga ninguém, apenas passa a exigir HTTPS na transmissão.
- **Conferência visual autenticada em produção (relatórios e carteirinhas com sessão real):** é do dono — a entrega não recebe credenciais e não criamos contas nem alteramos permissões.
- **Permissões:** `checkin.trabalhadores` já tinha concessão (grupo Recepção, ler+escrever); `checkin.trabalhadores.correcoes` segue **sem concessão a nenhum grupo** (como no DEV) — cabe ao dono conceder a quem corrigirá presença.
- **Conta exclusiva do aparelho:** discutida, **não implementada** — não faz parte desta publicação; o fluxo de ativação existente foi preservado.
- **Câmera do terminal:** não pode ser homologada por testes simulados; vale apenas o teste da etapa 9 no aparelho físico.

## 9. Ativação do terminal no aparelho da entrada (roteiro curto)

Endereço: **https://shambala.webhop.com.br/terminal** (o HTTPS do túnel libera o acesso à câmera — no DEV HTTP ele não existe; por isso o teste de câmera só tem sentido aqui).

1. No **aparelho fixo da entrada**, abrir o navegador e entrar em `https://shambala.webhop.com.br` com uma conta do grupo que tem `checkin.trabalhadores` (ex.: um atendente da Recepção).
2. Abrir a tela **Presença de trabalhadores** (`/checkin-trabalhador`) e clicar **Ativar terminal neste aparelho**. Confirmar o aviso: a sessão de administração desse navegador será encerrada e o aparelho passa a ter só a sessão do terminal (24 h).
3. O navegador do aparelho abre a tela do terminal (`/terminal`). Recarregue se pedir login — não peça login de novo nesse aparelho até reativar.
4. **Teste de câmera:** permitir o acesso à câmera quando o navegador pedir. Apontar para o QR da carteirinha de um trabalhador (as carteirinhas já podem ser impressas em `/trabalhadores/carteirinhas` com sessão autorizada) e conferir a chegada registrada na tela; depois testar a digitação manual do código de 4 dígitos como alternativa.
5. Para devolver o aparelho ao uso normal (ou trocar de operador): encerrar o terminal na própria tela; para renovar um terminal ativo, reativar pelo passo 2. Se o dia não fechar, a sessão expira sozinha em 24 h.

**Homologação da câmera = resultado deste teste no aparelho físico**, a cargo do dono.

## 10. Encerramento

- Recursos descartáveis encerrados: banco de ensaio `shambala_test_restore` removido, app de ensaio (porta 9012) finalizado, tarballs e SQL temporários locais e do servidor apagados. O DEV (porta 9000) não foi tocado por esta publicação.
- Produção na versão publicada `b5bca85` (conjunto das TASK_001–004 + acabamentos homologados), banco migrado, códigos emitidos, serviço ativo e limpo.
