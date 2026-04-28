# Plano de Implementação: Backup em Pendrive/HD Externo

**Objetivo:** Permitir backup automático em dispositivos removíveis (pendrive, HD externo) configuráveis pelo administrador.

---

## Análise do Problema

- **Servidor sem GUI:** Não detecta automaticamente pendrives
- **Montagem Manual:** Administrador precisa montar manualmente (`mount /dev/sdb1 /mnt`)
- **Portabilidade GPL:** Sistema será usado em múltiplos locais com diferentes configurações
- **Solução:** Interface de configuração para especificar dispositivo e ponto de montagem

---

## Arquitetura Proposta

### 1. Banco de Dados
```sql
CREATE TABLE IF NOT EXISTS configuracoes_backup (
    id SERIAL PRIMARY KEY,
    tipo_backup VARCHAR(20),  -- 'pendrive' ou 'remoto'
    dispositivo VARCHAR(100),  -- '/dev/sdb1'
    ponto_montagem VARCHAR(255),  -- '/mnt' ou '/media/usuario/pendrive'
    ativo INTEGER DEFAULT 0,
    horario_backup TIME,  -- NULL = manual apenas
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 2. Rota de Configuração (`rotas/configuracoes.py`)
```
GET  /configuracoes/backup           → exibir formulário
POST /configuracoes/backup           → salvar configuração
GET  /configuracoes/backup/testar    → validar dispositivo
POST /configuracoes/backup/executar  → fazer backup agora
```

### 3. Script de Backup (`backup_pendrive.py`)
- Validar dispositivo e ponto de montagem
- Montar dispositivo (se não estiver)
- Fazer dump do PostgreSQL
- Salvar em estrutura de pastas: `/media/.../shambala_backup_YYYY-MM-DD/`
- Desmontar (opcional)
- Retornar status (sucesso/erro/espaço)

### 4. Template de Configuração (`templates/configuracoes/backup.html`)
- Campo para dispositivo (ex: `/dev/sdb1`)
- Campo para ponto de montagem (ex: `/mnt`)
- Botão "Testar Conexão"
- Botão "Fazer Backup Agora"
- Histórico de backups realizados

---

## Fluxo de Uso

1. **Administrador acessa** `/configuracoes/backup`
2. **Configura:**
   - Dispositivo: `/dev/sdb1`
   - Montagem: `/mnt/pendrive`
   - Ativa automático (cron diário às 22h)
3. **Clica "Testar"** → verifica se dispositivo existe
4. **Sistema monta** `/dev/sdb1` em `/mnt/pendrive`
5. **Faz backup** `pg_dump` → `/mnt/pendrive/shambala_backup_2026-04-17/`
6. **Mostra status:** ✅ Backup OK (2.5GB em dispositivo de 16GB)

---

## Validações de Segurança

- ⚠️ Não permitir caminhos com `../` ou `/etc`, `/root`, `/sys`
- ✅ Validar que dispositivo é `/dev/*`
- ✅ Verificar permissões (sudo para montar)
- ✅ Verificar espaço disponível antes de backup
- ✅ Logs de todas as operações

---

## Tarefas para Implementação

- [ ] Adicionar tabela `configuracoes_backup` ao schema.sql
- [ ] Criar rota GET/POST `/configuracoes/backup`
- [ ] Criar script `backup_pendrive.py`
- [ ] Template de configuração
- [ ] Testes com pendrive real
- [ ] Documentação de instalação (permissões sudoers)

---

## Instalação em Servidor (sudoers)

```bash
# Permitir que o usuário shambala monte dispositivos
echo "shambala ALL=(ALL) NOPASSWD: /usr/bin/mount, /usr/bin/umount" | sudo tee -a /etc/sudoers.d/shambala
```

---

## Compatibilidade

✅ Funciona em qualquer distro Linux (Debian, Ubuntu, CentOS...)  
✅ Suporta múltiplos dispositivos (configurar alternância)  
✅ Mantém histórico de backups  
✅ Integração com backup remoto existente (ambos podem estar ativos)

