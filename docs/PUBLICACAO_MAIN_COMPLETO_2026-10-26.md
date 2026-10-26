# Publicação — `main` completo (TASK_005–018) em produção

**Data:** 2026-10-26 · **Servidor:** shambala (10.100.0.4) · **Endereço público:** https://shambala.webhop.com.br
**Autorização:** ordem explícita do dono nesta sessão — "atualize totalmente o produção" — após eu registrar que o `main` continha 95 commits além do que estava publicado, e não apenas a TASK_018.

## 1. O que foi publicado

Produção estava em `b5bca85` + patch isolado da TASK_017 (`4352d92`), provado por manifesto md5 arquivo a arquivo: 172/185 conferiam com `b5bca85` e os 8 divergentes eram exatamente os arquivos do patch da TASK_017 (mais `SHAMBALA_CONTEXTO.md`, documento).

A publicação levou a árvore ao HEAD `35cc3cc` — intervalo `b5bca85..35cc3cc`, **95 commits**, contendo todo o restante do `main`:

- TASK_005–010: catálogo financeiro, PDV no servidor (FIN-01), cancelamento com reversão (FIN-02), caixa por eventos (FIN-04), contrato de ambiente de teste com token de execução
- TASK_011–016: Visão Geral do Financeiro, competência de mensalidade, idempotência de entrada de dinheiro, destino explícito do recebimento, correções da auditoria, contas de destino/banco/poupança
- TASK_015: emojis → ícones Lucide (`static/icons/`)
- TASK_017: já estava em produção; o registro no `main` apenas alinha a árvore
- TASK_018 + complemento: conclusão administrativa de planos, extensão × reposição (`agendamentos.adicionada`/`repoe`), destino explícito das sessões

Conjunto aplicado: `git archive 35cc3cc` restrito a código (tar `/tmp/deploy_2026-10-26.tar`, sha256 `42e23a19…e500`, 320 arquivos), extraído **sem `--delete`** e **com `--exclude=static/fotos`** — os 23 arquivos de fotos versionados no git colidem com os 35 de produção, que ficaram intactos (conferido: 35 arquivos após o deploy). Arquivos próprios de produção (`/opt/shambala/.env`, `database.db`, `bkp/`, `01.png`, `.claude/`, `planning/`, `reasonix.toml`) não existem no HEAD e foram preservados. `requirements.txt` idêntico no intervalo → nenhuma dependência instalada. `shambala.service` idêntico (md5 `61356b4a…`) → unit não tocada, sem `daemon-reload`, mesma instância e porta 8000.

Amostra conferida arquivo a arquivo no servidor contra `git cat-file HEAD:<arquivo>`: `main.py` `2d559bc9…`, `banco.py` `f6a59a52…`, `servico_conclusao_planos.py` `5f5c121f…`, `rotas/agenda.py` `83167932…` — idênticos. Dono `shambala:shambala`, `__pycache__` limpo.

## 2. Backup e recuperação

Em `shambala:/root/backups/publicacao-2026-10-26/`, espelhado em `docs/tasks/backups/publicacao-2026-10-26/` (fora do git, contém dados reais). sha256 **iguais** nas duas cópias:

| Arquivo | sha256 |
|---|---|
| `shambala_pre_publicacao.dump` (`pg_dump -Fc`, 486.482 B) | `803e13c7e552fe9477abec891061a31788aa97935d3d54354178deb21b2062b9` |
| `shambala_pre_publicacao.sql` | `67ea6addc25084a6abfb5f4011a4d0b6d2dd32a99519e997c1ec535605ebebc5` |
| `arquivos_pre_publicacao.tar.gz` (32,5 MB: main, banco, rotas, templates, static, scripts) | `673b2c0dafd4b6bd41cf020e020382c570687bba1c8451e94a00481ca9bdb777` |
| `manifest_antes.md5` / `CHECKSUMS.sha256` | inventário da versão anterior |
| `baseline_counts_2026-10-26.txt` (`8aed2585…`) | contagem exata das 42 tabelas antes |
| `fatos_planos_status.txt` / `fatos_agend_status.txt` | planos `alta=17 ativo=1866 cancelado=29`; agendamentos `agendado=222 cancelado=317 faltou=851 realizado=1594` |

**Recuperação:** `pg_restore -c` do dump OU restaurar o tar + `systemctl restart shambala`. Restaurar o banco perde o que foi escrito após 2026-10-26 19:21 (presenças, vendas e estoque registrados no intervalo).

## 3. Ensaio antes do deploy (banco real, código novo)

O dump foi restaurado duas vezes em bancos descartáveis locais (`shambala_test_ensaio_prod`, `shambala_test_ensaio_ref` — nome com o marcador `_test` exigido pela trava; nunca sobre o banco de DEV). Contagens idênticas à baseline de produção (42 tabelas). O app de `35cc3cc` subiu pelo launcher `tests/subir_app_descartavel.sh` na porta 9014, com identidade efetiva confirmada no log (`banco_efetivo=shambala_test_ensaio_prod`, token da execução). Depois da migração, comparado à cópia intacta:

- hashes dos fatos originais **idênticos**: `financeiro_movimentacoes` `c14f3bc0…`, `agendamentos` `5e9e4251…`
- contagens: apenas as 4 tabelas novas criadas, todas em 0
- `planos_tratamento`: nenhum plano fechado (`conclusao_origem IS NOT NULL` = 0); `sessoes_previstas = sessoes_total` em 1912/1912
- `agendamentos`: `addicionada` e `repoe` somam 0; nenhum `concluido`
- rotas nova/legadas: 303 para login (existem), estáticos novos 200, zero traceback

Os dois bancos e o env de ensaio foram removidos ao final; nada do ensaio foi copiado para produção.

## 4. Migração aplicada em produção (dados reais preservados)

A migração roda no startup do serviço (código idempotente de `banco.py`), como no ensaio. Conferido depois, contra a baseline de 19:21:

| Verificação | Resultado |
|---|---|
| Hash `financeiro_movimentacoes` (id, tipo, categoria, valor, descrição, status, data, conta) | `c14f3bc0…` — **idêntico** ao da cópia restaurada |
| Hash `agendamentos` (id, data, status, requer_passe, encaixe, plano_id) | `5e9e4251…` — **idêntico** |
| Status de `planos_tratamento` | `alta=17 ativo=1866 cancelado=29` — inalterado |
| Tabelas novas | `caixa_sessao_eventos`, `fiado_alocacao`, `operacao_idempotencia`, `vendas_pdv_cancelamento` — criadas, **0 linhas** |
| Colunas novas de `planos_tratamento` | `sessoes_previstas`, `sessoes_adicionais`, `regra_conclusao`, `concluido_em`, `conclusao_origem` presentes; `sessoes_previstas ≠ sessoes_total` em 0; `regra_conclusao=1` em 0 → **todo plano existente é tratado como legado, nada fechou automaticamente** |
| Colunas novas de `agendamentos` | `adicionada` e `repoe` com 0 linhas em 1 |
| Competência de mensalidade | 517 linhas rotuladas com `left(data_movimentacao,7)` — só onde estava nulo; nenhum valor, status, conta ou data alterado |
| Contagens das 42 tabelas | iguais à baseline **exceto** `checkins` +2, `trabalhador_presenca` +1, `vendas_pdv` +1, `vendas_pdv_itens` +1, `estoque_movimentos` +1 — acréscimos de linhas de operação ao vivo entre o baseline (19:21) e a medição (19:29), nenhuma linha existente modificada |

**Nota de medição (não é defeito do deploy):** no PostgreSQL 17.11 do servidor, `sum(addicionada)`/`count(addicionada)` em consultinha avulsa retorna `coluna "addicionada" não existe`, enquanto `select adicionada from agendamentos limit 1` e `count(*) where adicionada=1` funcionam. Verifiquei que **nenhum código do app agrega essas colunas** (busca por `sum|count|max|min|coalesce` sobre `adicionada|repoe` em `*.py`: sem ocorrências) — o efeito atingiu apenas as minhas consultas de conferência, e as verificações foram refeitas pelas formas que funcionam.

## 5. Verificação pós-publicação

`https://shambala.webhop.com.br` pelo túnel Cloudflare existente (cloudflared e unit `shambala` preexistentes, nada instalado):

| Verificação | Resultado |
|---|---|
| `GET /` | 303 → `/menu` |
| `GET /login` | 200 |
| `GET /static/css/base.css` | 200 |
| `GET /static/icons/wallet.svg` (ícones Lucide, TASK_015) | 200 |
| Rotas protegidas | 303 → login; nenhuma 404 entre as telas novas (`/cadastros/trabalhadores/carteirinhas`, `/cadastros/mediuns/1/planos`, `/agenda/medium`, `/terminal/`, `/caixa/pdv`, `/dia/dashboard`, `/financeiro`) |
| `journalctl` do processo novo (PID 279410/279434) | startup completo, **zero traceback** |
| `static/fotos` | 35 arquivos preservados |

Durante a troca de arquivos, antes do `systemctl restart`, o **processo antigo** (que ainda tinha o `templates_config` sem o filtro `asset_url` carregado) registrou 500 em `/dia/dashboard` ao renderizar templates novos. É a razão do intervalo entre copiar arquivos e reiniciar; o processo atual não reproduz o erro. Recomendação para as próximas publicações: parar o serviço antes de extrair o pacote, para nenhum usuário receber página meio-trocada.

## 6. Git

Push do `main` para os dois remotos, feito nesta publicação:
- `github` (GPL-3.0, público): `8a995e2..35cc3cc`
- `gitproprio` (`leal:/home/git/projetos/shambala/`): `7238c80..35cc3cc`

## 7. Pendências que a publicação não resolve

- **Homologação pelo dono**: TASK_010–016 (financeiro), TASK_015 (ícones), TASK_017 e TASK_018 continuam pendentes de conferência do dono — agora conferível **em produção**, não só no DEV.
- **Planos legados** (1155 vagas a repor, 9 planos do incidente, 743 planos `ativo` legados): decisão humana da TASK_018 §17, não executada. `consolidar_planos.py` está no projeto mas **não é versionado** (`git status`: untracked) e **não foi copiado** para produção — nada foi consolidado aqui.
- **Permissões**: cabe ao dono conceder `checkin.trabalhadores.correcoes` e revisar as concessões do módulo `cadastros.mediuns.planos`.
- **Cookies `Secure`**: `SHAMBALA_HTTPS` segue desligado em produção (mesma observação de 2026-10-24); fora do escopo desta publicação.
- Sessões em memória: o restart deslogou todos os usuários (comportamento conhecido).
