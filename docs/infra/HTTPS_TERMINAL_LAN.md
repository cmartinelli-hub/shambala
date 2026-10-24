# Shambala — HTTPS na LAN para o terminal de presença (TASK_003)

O navegador só libera a câmera (getUserMedia) em contexto seguro. O DEV atende em
`http://192.168.15.8:9000`, que é contexto inseguro: no celular da entrada a tela
do terminal funciona pelo teclado, mas o leitor de QR fica barrado. Este arquivo
publica o mesmo app em HTTPS num porta à parte, sem mexer no serviço da porta 9000
e sem domínio público.

Nada aqui foi aplicado: o dono pediu o roteiro, não a mudança no nginx.

## 1. Certificado

A porta 443 do `i5-12gen` já pertence ao MV Financeiro, então se usa **9443**,
com certificado endereçado por IP — certificado para nome de máquina não serve,
porque o celular vai acessar por IP.

```bash
sudo openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout /etc/ssl/private/shambala-lan.key \
  -out /etc/ssl/certs/shambala-lan.crt \
  -subj "/CN=Shambala LAN" \
  -addext "subjectAltName=IP:192.168.15.8"
```

825 dias é o teto que iOS e macOS aceitam para certificado TLS servido por
servidor; Android não impõe limite, mas mantê-los iguais evita surpresa futura.

## 2. Site no nginx

Gravar como `/etc/nginx/sites-available/shambala` e habilitar:

```
server {
    listen 9443 ssl;
    listen [::]:9443 ssl;
    server_name 192.168.15.8;

    ssl_certificate     /etc/ssl/certs/shambala-lan.crt;
    ssl_certificate_key /etc/ssl/private/shambala-lan.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/shambala /etc/nginx/sites-enabled/shambala
sudo nginx -t && sudo systemctl reload nginx
```

O app é o mesmo processo de sempre; o nginx só faz a frente TLS. Nenhuma rota,
porta ou arquivo do Shambala muda.

## 3. Confiar no certificado no aparelho

Certificado autoassinado é contexto seguro válido, mas o navegador exige uma
aceitação manual, e o aviso volta quando o certificado for trocado. O caminho
sem aviso é instalar a CA no aparelho:

- gerar a CA além do certificado de servidor (assinar `shambala-lan.crt` com
  ela) e copiar o `.crt` da CA para o celular;
- Android: Configurações → Segurança → Instalar certificados CA;
- iOS: perfil da CA, depois Ajustes → Geral → Sobre → Acesso ao certificado →
  ativar o interruptor de confiança total.

Sem a CA instalada, basta tocar em "Avançado → Acessar" uma vez; a câmera
funciona igual.

## 4. Cookie de sessão

`SHAMBALA_HTTPS=true` faz os cookies de sessão serem emitidos com `Secure`, que é
o correto quando o acesso passa a ser só por HTTPS. Com `Secure`, o acesso pela
porta 9000 em HTTP passa a não carregar sessão: ao ligar a flag, o terminal e a
administração devem usar `https://192.168.15.8:9443`, não mais o endereço HTTP.

## 5. Endereço do terminal

Com tudo no ar, o terminal é ativado em
`https://192.168.15.8:9443/checkin-trabalhador` (o botão de ativação) e a leitura
acontece em `https://192.168.15.8:9443/terminal/`, onde então a câmera é liberada.
O teclado continua disponível como alternativa, em qualquer um dos dois endereços.

## 6. Conferência rápida

```bash
curl -sk -o /dev/null -w '%{http_code} %{redirect_url}\n' https://127.0.0.1:9443/terminal/
# esperado: 303 https://127.0.0.1:9443/login  (o app redireciona relativo; o curl monta o endereço)
```
