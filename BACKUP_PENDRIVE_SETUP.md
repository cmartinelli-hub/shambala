# Configuração de Backup em Pendrive/HD Externo

## Requisito: Executar o serviço como root

O Shambala precisa rodar como `root` para poder montar/desmontar dispositivos USB sem depender de sudo.

## Configuração no Debian/Linux

### Opção 1: Modificar o arquivo systemd (Recomendado)

```bash
sudo nano /etc/systemd/system/shambala.service
```

Altere a seção `[Service]` para:

```ini
[Service]
User=root
Group=root
WorkingDirectory=/opt/shambala
ExecStart=/opt/shambala/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10
```

Depois recarregue e reinicie:

```bash
sudo systemctl daemon-reload
sudo systemctl restart shambala
```

Verifique o status:

```bash
sudo systemctl status shambala
```

### Opção 2: Rodar diretamente como root (teste)

```bash
sudo su -
cd /opt/shambala
./.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

## Após configurar

1. Acesse `/configuracoes/backup-pendrive`
2. Configure:
   - **Dispositivo:** `/dev/sdb1` (identifique com `lsblk`)
   - **Ponto de Montagem:** `/home/shambala/pendrive` ou qualquer outro local
3. Clique em **"Testar Conexão"**
4. Clique em **"Executar Backup Agora"**

## Identificar seu Pendrive

```bash
# Ver lista de discos
lsblk

# Ou ver mensagens do kernel
dmesg | tail -20
```

Procure por algo como:
```
sdb      8:16    1  15.6G  0 disk
└─sdb1   8:17    1  15.6G  0 part
```

Neste caso, o dispositivo é `/dev/sdb1`

## Estrutura de Backups

Após primeiro backup bem-sucedido:

```
/home/shambala/pendrive/
└── shambala_backup_2026-04-17/
    └── database.sql
```

Cada backup cria um diretório com a data do backup.

## Histórico

A página `/configuracoes/backup-pendrive` mostra:
- ✅ Data/hora de cada backup
- ✅ Status (Sucesso/Erro)
- ✅ Tamanho do arquivo
- ✅ Espaço livre no pendrive
- ✅ Mensagens de erro (se houver)

## Segurança

✓ **Seguro em servidor dedicado:** O Shambala é o principal serviço, rodando como root é aceitável
✓ **Sem dependência de sudo:** Simplifica a instalação
✓ **Sem senhas:** Sem necessidade de sudoers configuração
✓ **Validações:** Caminhos de mount são validados no código
