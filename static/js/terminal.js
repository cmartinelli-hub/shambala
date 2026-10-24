/* ── Terminal fixo de presença (TASK_003) ──────────────────────────────────
   Lê o QR da carteirinha (jsQR, biblioteca local vendorizada — sem CDN e sem
   serviço externo) ou aceita os mesmos quatro números pelo teclado. O vídeo
   nunca sai do aparelho: nada é enviado ao servidor além do código, e nenhuma
   imagem é armazenada.

   QR e teclado seguem pelo MESMO caminho no servidor (/terminal/chegada),
   protegido pela sessão restrita do terminal. */
(function () {
    'use strict';

    var $ = function (id) { return document.getElementById(id); };

    var EL = {
        convite: $('tm-convite'),
        leitor: $('tm-leitor'),
        teclado: $('tm-teclado'),
        resultado: $('tm-resultado'),
        video: $('tm-video'),
        canvas: $('tm-canvas'),
        enquadramento: $('tm-enquadramento'),
        estadoCamera: $('tm-estado-camera'),
        msgBloqueio: $('tm-msg-bloqueio'),
        seletor: $('tm-seletor-camera'),
        reiniciar: $('tm-reiniciar-camera'),
        irTeclado: $('tm-ir-teclado'),
        voltarCamera: $('tm-voltar-camera'),
        posicoes: Array.prototype.slice.call(document.querySelectorAll('.tm-posicao')),
        teclas: $('tm-teclas'),
        confirmar: $('tm-confirmar'),
        msgTeclado: $('tm-msg-teclado'),
        foto: $('tm-foto'),
        msgResultado: $('tm-resultado-mensagem'),
        nome: $('tm-resultado-nome'),
        hora: $('tm-resultado-hora'),
        conexao: $('tm-conexao'),
        telaCheia: $('tm-tela-cheia')
    };

    var TEXTO = {
        iniciando: 'Iniciando câmera…',
        pronta: 'Câmera pronta — apresente a carteirinha',
        enviando: 'Confirmando…',
        desligada: 'Câmera desligada — você está digitando',
        aguardando: 'Terminal desligado — procure a recepção',
        negada: 'A permissão da câmera foi negada. Toque em Reiniciar câmera para pedir de novo, ou digite o código.',
        ausente: 'Nenhuma câmera encontrada neste aparelho. Use o teclado.',
        ocupada: 'A câmera está em uso por outro aplicativo. Feche-o e toque em Reiniciar câmera, ou digite o código.',
        inseguro: 'Este navegador só libera a câmera em endereço seguro (HTTPS). Use o teclado até a Casa ativar o HTTPS neste endereço.',
        generica: 'Câmera indisponível neste momento. Use o teclado.'
    };

    var MENSAGENS = {
        registrada: 'Presença registrada',
        ja_registrada: 'Presença já registrada',
        nao_encontrado: 'Código não encontrado.',
        retirado: 'Carteirinha substituída; procure a recepção.',
        inativo: 'Cadastro inativo; procure a recepção.',
        invalido: 'Código não encontrado.',
        sem_resposta: 'Não foi possível confirmar. Tente novamente.',
        sessao: 'Terminal precisa ser ativado novamente. Procure a recepção.'
    };

    var TEMPO_EXIBICAO = 3000;
    var TEMPO_LIMITE_ENVIO = 12000;
    var LARGURA_DECODIFICACAO = 640;
    var QUADROS_SEM_QR_PARA_REARMAR = 2;
    var CHAVE_CAMERA = 'shambala_terminal_camera_id';

    var camera = null;                 // MediaStream viva
    var quadro = 0;                    // requestAnimationFrame do decodificador
    var modo = 'camera';               // 'camera' | 'teclado' | 'resultado'
    var digitado = '';
    var enviando = false;
    var pedido = 0;                    // sequência: resposta antiga não substitui a atual
    var codigoExibido = null;
    var precisaSairDoEnquadro = false;
    var quadrosSemQr = 0;
    var timerExibicao = null;
    var sessaoEncerrada = false;
    var audio = null;
    var preferida = null;
    try { preferida = window.localStorage.getItem(CHAVE_CAMERA); } catch (e) { preferida = null; }

    // ── utilitários ─────────────────────────────────────────────────────────

    function ehCodigo(conteudo) {
        return typeof conteudo === 'string' && /^[0-9]{4}$/.test(conteudo);
    }

    function definirCamera(estado, texto) {
        EL.enquadramento.setAttribute('data-camera', estado);
        EL.estadoCamera.setAttribute('data-estado', estado);
        EL.estadoCamera.textContent = texto;
    }

    function mostrarBloqueio(texto) {
        EL.msgBloqueio.textContent = texto || '';
        EL.msgBloqueio.hidden = !texto;
    }

    function avisoTeclado(texto) {
        EL.msgTeclado.textContent = texto || '';
        EL.msgTeclado.hidden = !texto;
    }

    function tocarSom(tipo) {
        // Som é conseqüência, nunca pré-condição: falha aqui não bloqueia nada.
        try {
            var Contexto = window.AudioContext || window.webkitAudioContext;
            if (!Contexto) return;
            audio = audio || new Contexto();
            if (audio.state === 'suspended') { audio.resume(); }
            var notas = tipo === 'sucesso' ? [[880, 0], [1174, 0.11]] : [[392, 0]];
            notas.forEach(function (n) {
                var osc = audio.createOscillator();
                var ganho = audio.createGain();
                osc.type = 'sine';
                osc.frequency.value = n[0];
                osc.connect(ganho);
                ganho.connect(audio.destination);
                var inicio = audio.currentTime + n[1];
                ganho.gain.setValueAtTime(0.0001, inicio);
                ganho.gain.linearRampToValueAtTime(0.22, inicio + 0.012);
                ganho.gain.exponentialRampToValueAtTime(0.0001, inicio + 0.14);
                osc.start(inicio);
                osc.stop(inicio + 0.16);
            });
        } catch (e) { /* aparelho sem áudio, gesto pendente, navegador ocupado */ }
    }

    function habilitarSomNoToque() {
        // Alguns navegadores só liberam áudio depois de um gesto do usuário.
        var acordar = function () {
            try {
                var Contexto = window.AudioContext || window.webkitAudioContext;
                if (!Contexto) return;
                audio = audio || new Contexto();
                if (audio.state === 'suspended') { audio.resume(); }
            } catch (e) { /* idem */ }
        };
        document.addEventListener('pointerdown', acordar);
        document.addEventListener('keydown', acordar);
    }

    function pedirTelaLiga() {
        // Recurso de conveniência: a recusa não interfere no terminal.
        try {
            if (navigator.wakeLock && navigator.wakeLock.request) {
                navigator.wakeLock.request('screen');
            }
        } catch (e) { /* sem manutenção de tela */ }
    }

    // ── câmera ──────────────────────────────────────────────────────────────

    function pararQuadro() {
        if (quadro) { cancelAnimationFrame(quadro); quadro = 0; }
    }

    function soltarCamera() {
        pararQuadro();
        if (camera) {
            camera.getTracks().forEach(function (trilha) { trilha.stop(); });
            camera = null;
        }
        EL.video.srcObject = null;
    }

    function restricoes(deviceId) {
        var base = { width: { ideal: 1280 }, height: { ideal: 720 } };
        if (deviceId) {
            base.deviceId = { exact: deviceId };
        } else {
            base.facingMode = { ideal: 'environment' };
        }
        return base;
    }

    async function ligarCamera(deviceId) {
        if (sessaoEncerrada) return;
        if (!window.isSecureContext || !navigator.mediaDevices
            || !navigator.mediaDevices.getUserMedia) {
            definirCamera('indisponivel', 'Câmera não disponível neste endereço');
            mostrarBloqueio(TEXTO.inseguro);
            return;
        }

        soltarCamera();
        definirCamera('iniciando', TEXTO.iniciando);
        mostrarBloqueio('');

        var alvo = (deviceId === undefined) ? preferida : deviceId;
        var erro;
        try {
            camera = await navigator.mediaDevices.getUserMedia({ video: restricoes(alvo), audio: false });
        } catch (e) {
            if (alvo && (e.name === 'OverconstrainedError' || e.name === 'NotFoundError')) {
                // A câmera preferida sumiu do aparelho: tenta a que houver.
                try {
                    camera = await navigator.mediaDevices.getUserMedia({ video: restricoes(null), audio: false });
                    preferida = null;
                    try { window.localStorage.removeItem(CHAVE_CAMERA); } catch (ignorado) { }
                } catch (e2) { erro = e2; }
            } else {
                erro = e;
            }
        }

        if (!camera) {
            var nome = (erro && erro.name) || '';
            if (nome === 'NotAllowedError' || nome === 'SecurityError') {
                definirCamera('indisponivel', 'Câmera bloqueada');
                mostrarBloqueio(TEXTO.negada);
            } else if (nome === 'NotFoundError') {
                definirCamera('indisponivel', 'Sem câmera');
                mostrarBloqueio(TEXTO.ausente);
            } else if (nome === 'NotReadableError' || nome === 'TrackStartError' || nome === 'AbortError') {
                definirCamera('indisponivel', 'Câmera indisponível');
                mostrarBloqueio(TEXTO.ocupada);
            } else if (!window.isSecureContext) {
                definirCamera('indisponivel', 'Câmera não disponível neste endereço');
                mostrarBloqueio(TEXTO.inseguro);
            } else {
                definirCamera('indisponivel', 'Câmera indisponível');
                mostrarBloqueio(TEXTO.generica);
            }
            return;
        }

        EL.video.srcObject = camera;
        try { await EL.video.play(); } catch (ignorado) { /* o quadro seguintes cuida */ }
        definirCamera('pronta', TEXTO.pronta);
        preencherSeletor();
        pedirTelaLiga();
        iniciarQuadro();
    }

    async function preencherSeletor() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
        var dispositivos;
        try { dispositivos = await navigator.mediaDevices.enumerateDevices(); }
        catch (e) { return; }
        var cameras = dispositivos.filter(function (d) { return d.kind === 'videoinput'; });
        if (!cameras.length) return;

        var trilha = camera && camera.getVideoTracks()[0];
        var atual = trilha ? trilha.getSettings().deviceId : null;
        var escolhida = atual || preferida || cameras[0].deviceId;

        while (EL.seletor.firstChild) { EL.seletor.removeChild(EL.seletor.firstChild); }
        cameras.forEach(function (c, i) {
            var opcao = document.createElement('option');
            opcao.value = c.deviceId;
            opcao.textContent = c.label || ('Câmera ' + (i + 1));
            EL.seletor.appendChild(opcao);
        });
        EL.seletor.value = escolhida;
        if (EL.seletor.value !== escolhida) { EL.seletor.value = cameras[0].deviceId; }
    }

    // ── laço de decodificação ───────────────────────────────────────────────

    function iniciarQuadro() {
        pararQuadro();
        quadrosSemQr = 0;
        var contexto = EL.canvas.getContext('2d', { willReadFrequently: true });

        var passo = function () {
            quadro = requestAnimationFrame(passo);
            if (modo !== 'camera' || enviando || sessaoEncerrada) return;
            if (!EL.video.videoWidth) return;

            var escala = Math.min(1, LARGURA_DECODIFICACAO / EL.video.videoWidth);
            var largura = Math.max(1, Math.round(EL.video.videoWidth * escala));
            var altura = Math.max(1, Math.round(EL.video.videoHeight * escala));
            if (EL.canvas.width !== largura || EL.canvas.height !== altura) {
                EL.canvas.width = largura;
                EL.canvas.height = altura;
            }
            contexto.drawImage(EL.video, 0, 0, largura, altura);
            var imagem = contexto.getImageData(0, 0, largura, altura);

            var conteudo = null;
            try {
                var lido = window.jsQR(imagem.data, largura, altura, { inversionAttempts: 'dontInvert' });
                if (lido && lido.data) { conteudo = lido.data; }
            } catch (e) { /* quadro ilegível: simplesmente tenta o próximo */ }

            if (conteudo === null) {
                quadrosSemQr++;
                // A mesma carteirinha só volta a valer depois de sair do enquadro
                // — sem som nem envio incessante enquanto estiver na tela.
                if (quadrosSemQr >= QUADROS_SEM_QR_PARA_REARMAR) { precisaSairDoEnquadro = false; }
                return;
            }
            quadrosSemQr = 0;
            tratarLeitura(conteudo);
        };
        quadro = requestAnimationFrame(passo);
    }

    function tratarLeitura(conteudo) {
        if (enviando || (precisaSairDoEnquadro && conteudo === codigoExibido)) return;

        // O QR da carteirinha contém exatamente os quatro dígitos. Qualquer
        // outro conteúdo é recusado aqui: nenhum link é aberto e nada vai ao
        // servidor com o que vier de um código de outra origem.
        if (!ehCodigo(conteudo)) {
            codigoExibido = conteudo;
            precisaSairDoEnquadro = true;
            exibirResultado({ resultado: 'invalido' });
            return;
        }
        enviar(conteudo, 'terminal_qr');
    }

    // ── envio ───────────────────────────────────────────────────────────────

    async function enviar(codigo, origem) {
        if (enviando) return;
        if (sessaoEncerrada) { exibirResultado({ resultado: 'sessao' }); return; }
        if (navigator.onLine === false) {
            exibirResultado({ resultado: 'sem_resposta' });
            return;
        }

        enviando = true;
        pedido++;
        var meuPedido = pedido;
        var controlador = new AbortController();
        var limite = setTimeout(function () { controlador.abort(); }, TEMPO_LIMITE_ENVIO);
        definirCamera('enviando', TEXTO.enviando);
        if (modo === 'teclado') { EL.confirmar.disabled = true; }

        var dados = null;
        var semResposta = false;
        try {
            var resposta = await fetch('/terminal/chegada', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ codigo: codigo, origem: origem }),
                credentials: 'same-origin',
                signal: controlador.signal
            });
            var tipo = (resposta.headers.get('content-type') || '').toLowerCase();
            if (resposta.status >= 500) {
                // O servidor falhou, não a ativação: diz que não foi possível
                // confirmar e libera a interface; o código pode ter sido gravado.
                semResposta = true;
            } else if (resposta.status === 401 || resposta.redirected
                       || tipo.indexOf('json') < 0) {
                // A tela de login no lugar do JSON: o terminal perdeu a ativação.
                semResposta = false;
                dados = { resultado: 'sessao' };
            } else {
                dados = await resposta.json();
            }
        } catch (e) {
            // Timeout ou conexão caída: a escrita PODE ter ocorrido; a repetição
            // é segura pela unicidade e devolve o primeiro horário.
            semResposta = true;
        } finally {
            clearTimeout(limite);
            enviando = false;
            if (modo === 'teclado') { desenharDigitado(); }
        }

        if (meuPedido !== pedido) return;   // resposta de pedido antigo

        if (dados && dados.resultado === 'sessao') {
            sessaoEncerrada = true;
            soltarCamera();
            exibirResultado(dados);
            return;
        }
        if (semResposta || !dados || !dados.resultado) {
            // O código fica marcado como exibido: a carteirinha segurada no
            // enquadro não reenvia sozinha (nem toca sem parar). Retirar e
            // apresentar de novo é o "tente novamente" da tela.
            exibirResultado({ resultado: 'sem_resposta' }, codigo);
            return;
        }
        exibirResultado(dados, codigo);
    }

    // ── resultado ───────────────────────────────────────────────────────────

    function exibirResultado(dados, codigo) {
        if (codigo !== undefined && codigo !== null) { codigoExibido = codigo; }
        precisaSairDoEnquadro = true;
        quadrosSemQr = 0;
        pararQuadro();
        clearTimeout(timerExibicao);

        EL.leitor.hidden = true;
        EL.teclado.hidden = true;
        EL.convite.hidden = true;
        EL.resultado.hidden = false;
        modo = 'resultado';

        var sucesso = dados.resultado === 'registrada' || dados.resultado === 'ja_registrada';
        EL.resultado.setAttribute('data-tipo', sucesso ? 'sucesso' : 'erro');

        if (dados.resultado === 'ja_registrada') {
            // Presença antiga sem horário: confirma sem inventar um.
            EL.msgResultado.textContent = dados.hora
                ? MENSAGENS.ja_registrada + ' às ' + dados.hora
                : MENSAGENS.ja_registrada;
        } else {
            EL.msgResultado.textContent = MENSAGENS[dados.resultado] || MENSAGENS.sem_resposta;
        }

        EL.nome.textContent = sucesso ? (dados.nome || '') : '';
        EL.hora.textContent = sucesso ? linhaDoDia(dados) : '';

        if (sucesso && dados.foto) {
            EL.foto.src = dados.foto;
            EL.foto.hidden = false;
        } else {
            EL.foto.removeAttribute('src');
            EL.foto.hidden = true;
        }

        // Só se chega aqui com a resposta do servidor; sucesso nunca é antecipado.
        tocarSom(sucesso ? 'sucesso' : 'erro');

        if (dados.resultado === 'sessao') {
            // Sem sessão não há o que ler: o aviso fica até alguém reativar.
            return;
        }
        timerExibicao = setTimeout(voltarAObservar, TEMPO_EXIBICAO);
    }

    function dataPorExtenso(data) {
        if (!data || data.length !== 10) return '';
        return data.slice(8, 10) + '/' + data.slice(5, 7) + '/' + data.slice(0, 4);
    }

    function linhaDoDia(dados) {
        // Data e horário confirmados pelo servidor; sem horário (presença antiga
        // registrada sem hora) mostra só a data — nunca se inventa um.
        var dia = dataPorExtenso(dados.data);
        if (dados.hora) {
            return dia ? dia + ' às ' + dados.hora : 'às ' + dados.hora;
        }
        return dia;
    }

    function voltarAObservar() {
        EL.resultado.hidden = true;
        limparDigitado();
        modo = 'camera';
        EL.convite.hidden = false;
        EL.leitor.hidden = false;
        if (camera) {
            definirCamera('pronta', TEXTO.pronta);
            iniciarQuadro();
        } else if (!sessaoEncerrada) {
            ligarCamera();
        } else {
            definirCamera('off', TEXTO.aguardando);
        }
    }

    function mostrarAvisoSessao() {
        EL.resultado.hidden = false;
        EL.resultado.setAttribute('data-tipo', 'erro');
        EL.msgResultado.textContent = MENSAGENS.sessao;
        EL.nome.textContent = '';
        EL.hora.textContent = '';
        EL.foto.hidden = true;
        modo = 'resultado';
    }

    // ── teclado numérico ────────────────────────────────────────────────────

    function desenharDigitado() {
        EL.posicoes.forEach(function (el, i) {
            el.textContent = digitado[i] || '';
            el.classList.toggle('ativa', i === digitado.length && digitado.length < 4);
        });
        EL.confirmar.disabled = digitado.length !== 4 || enviando;
    }

    function limparDigitado() {
        digitado = '';
        avisoTeclado('');
        desenharDigitado();
    }

    function abrirTeclado() {
        modo = 'teclado';
        pararQuadro();
        soltarCamera();
        definirCamera('off', TEXTO.desligada);
        EL.convite.hidden = false;
        EL.leitor.hidden = true;
        EL.resultado.hidden = true;
        EL.teclado.hidden = false;
        desenharDigitado();
    }

    function fecharTeclado() {
        limparDigitado();
        EL.teclado.hidden = true;
        modo = 'camera';
        if (sessaoEncerrada) {
            definirCamera('off', TEXTO.aguardando);
            EL.leitor.hidden = false;
            EL.convite.hidden = false;
            return;
        }
        EL.leitor.hidden = false;
        EL.convite.hidden = false;
        ligarCamera();
    }

    function adicionarDigito(digito) {
        if (digitado.length >= 4) return;
        digitado += digito;
        avisoTeclado('');
        desenharDigitado();
    }

    function confirmarDigitado() {
        if (enviando) return;
        if (digitado.length !== 4) {
            avisoTeclado('Digite os quatro números da carteirinha.');
            return;
        }
        if (sessaoEncerrada) { mostrarAvisoSessao(); return; }
        var codigo = digitado;
        limparDigitado();
        enviar(codigo, 'terminal_codigo');
    }

    // ── estado do aparelho ──────────────────────────────────────────────────

    function atualizarConexao() {
        EL.conexao.hidden = navigator.onLine !== false;
    }

    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState !== 'visible') {
            pararQuadro();
            return;
        }
        pedirTelaLiga();
        if (modo === 'camera' && !enviando) {
            if (sessaoEncerrada) { definirCamera('off', TEXTO.aguardando); return; }
            if (camera) { iniciarQuadro(); } else { ligarCamera(); }
        }
    });

    window.addEventListener('online', atualizarConexao);
    window.addEventListener('offline', atualizarConexao);

    if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
        navigator.mediaDevices.addEventListener('devicechange', function () {
            if (modo === 'camera' && !enviando && !sessaoEncerrada) { ligarCamera(); }
        });
    }

    // ── ligações de interface ───────────────────────────────────────────────

    EL.irTeclado.addEventListener('click', abrirTeclado);
    EL.voltarCamera.addEventListener('click', fecharTeclado);
    EL.reiniciar.addEventListener('click', function () {
        if (sessaoEncerrada) { mostrarAvisoSessao(); return; }
        ligarCamera();
    });
    EL.confirmar.addEventListener('click', confirmarDigitado);

    EL.seletor.addEventListener('change', function () {
        preferida = EL.seletor.value;
        try { window.localStorage.setItem(CHAVE_CAMERA, preferida); } catch (e) { /* preferência local */ }
        ligarCamera(preferida);
    });

    EL.teclas.addEventListener('click', function (evento) {
        var botao = evento.target.closest('button');
        if (!botao) return;
        if (botao.dataset.digito) { adicionarDigito(botao.dataset.digito); return; }
        if (botao.dataset.acao === 'apagar') {
            digitado = digitado.slice(0, -1);
            desenharDigitado();
            return;
        }
        if (botao.dataset.acao === 'limpar') { limparDigitado(); }
    });

    document.addEventListener('keydown', function (evento) {
        if (modo !== 'teclado') return;
        if (/^[0-9]$/.test(evento.key)) {
            evento.preventDefault();
            adicionarDigito(evento.key);
        } else if (evento.key === 'Backspace') {
            evento.preventDefault();
            digitado = digitado.slice(0, -1);
            desenharDigitado();
        } else if (evento.key === 'Enter') {
            evento.preventDefault();
            confirmarDigitado();
        } else if (evento.key === 'Escape') {
            evento.preventDefault();
            fecharTeclado();
        }
    });

    if (document.documentElement.requestFullscreen) {
        EL.telaCheia.addEventListener('click', function () {
            try {
                if (document.fullscreenElement) { document.exitFullscreen(); }
                else { document.documentElement.requestFullscreen(); }
            } catch (e) { /* tela cheia é opcional */ }
        });
    } else {
        EL.telaCheia.remove();
    }

    // ── partida ─────────────────────────────────────────────────────────────

    atualizarConexao();
    habilitarSomNoToque();
    desenharDigitado();
    definirCamera('iniciando', TEXTO.iniciando);
    ligarCamera();
})();
