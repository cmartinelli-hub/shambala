# Ícones Lucide — origem, versão e licença

- **Família**: [Lucide](https://lucide.dev)
- **Pacote**: `lucide-static`
- **Versão fixada**: `0.460.0` (2026-10-25)
- **Origem de cada arquivo**: `https://unpkg.com/lucide-static@0.460.0/icons/<nome>.svg`
- **Licença**: ISC — ver abaixo. O aviso `@license lucide-static v0.460.0 - ISC`
  é preservado no cabeçalho de cada SVG.

Os arquivos são servidos **localmente** (`/static/icons/…`). Não há CDN, fonte de
ícones nem qualquer dependência de acesso à internet em tempo de execução.

## Por que vendorizados

A ordem da TASK_015 exige servir os SVGs localmente, com versão/origem fixadas e
licença preservada. Manter os arquivos no repositório também elimina uma chamada
de rede por página e deixa o ícone disponível na renderização (sem depender de
JavaScript para aparecer).

## Não alterar os traços no arquivo

Os SVGs mantêm `stroke-width="2"` do pacote. A espessura pedida na ordem
(referência 1,75) é aplicada no CSS do projeto — assim a família continua
reproduzível a partir da origem, e a decisão visual fica num lugar só.

---

ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of
Feather (MIT). All other copyright (c) for Lucide are held by Lucide
Contributors 2022.

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
