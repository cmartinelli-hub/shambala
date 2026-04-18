# Configuração de Backup em Pendrive/HD Externo

## Problema

O serviço Shambala roda como usuário `shambala` (não root), mas montar dispositivos requer privilégios elevados.

## Solução

Configure o `sudoers` para permitir que o usuário `shambala` execute `mount` e `umount` sem pedir senha.

### Passo 1: Configure Sudoers (execute como root ou com sudo)

```bash
sudo visudo -f /etc/sudoers.d/shambala-mount
```

### Passo 2: Adicione a seguinte linha

```
shambala ALL=(ALL) NOPASSWD: /bin/mount, /bin/umount, /sbin/mount, /sbin/umount
```

### Passo 3: Salve e saia (Ctrl+X, depois Y, depois Enter)

### Passo 4: Verifique as permissões

```bash
sudo ls -l /etc/sudoers.d/shambala-mount
```

Deverá mostrar: `-r--r----- 1 root root ...`

## Teste

1. Acesse `/configuracoes/backup-pendrive`
2. Configure:
   - **Dispositivo:** `/dev/sdb1` (ou o seu pendrive)
   - **Ponto de Montagem:** `/home/shambala/pendrive`
3. Clique em **"Testar Conexão"**

## Troubleshooting

### Se receber "sudo: comando não encontrado"

Execute como root (não use sudo):

```bash
echo "shambala ALL=(ALL) NOPASSWD: /bin/mount, /bin/umount, /sbin/mount, /sbin/umount" | tee -a /etc/sudoers.d/shambala-mount
chmod 440 /etc/sudoers.d/shambala-mount
```

### Se o pendrive não montar

1. Identifique o dispositivo:
   ```bash
   lsblk
   # ou
   dmesg | tail -20
   ```

2. O dispositivo geralmente é `/dev/sdb1`, `/dev/sdc1`, etc.

3. Teste montar manualmente:
   ```bash
   sudo mount /dev/sdb1 /home/shambala/pendrive
   ```

### Se a pasta não existir

O sistema cria automaticamente, mas pode precisar de permissão:

```bash
sudo mkdir -p /home/shambala/pendrive
sudo chown shambala:shambala /home/shambala/pendrive
```

## Estrutura de Backups

Após o primeiro backup bem-sucedido, os arquivos estarão em:

```
/home/shambala/pendrive/shambala_backup_YYYY-MM-DD/
├── database.sql
└── (outros arquivos, se aplicável)
```

## Automação (Cron)

Para agendar backups automáticos, acesse `/configuracoes/backup-pendrive` e:

1. Ative "Ativar backup automático"
2. Escolha um horário (ex: 22:00)
3. Clique em "Salvar Configuração"

O sistema criará um cron job para executar o backup automaticamente.

## Segurança

⚠️ **Importante:** Nunca execute o serviço Shambala como root. A solução com `sudoers` NOPASSWD é segura porque:
- Limita-se apenas aos comandos `mount` e `umount`
- O usuário `shambala` é isolado (não tem shell interativo)
- Os caminhos de mount são validados no código
