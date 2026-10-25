// Identificador da tentativa (TASK_012 §2).
// A chave nasce com a tela e NÃO é recriada a cada envio: duplo clique, reenvio
// depois de perdida a resposta ou duas abas da MESMA tela usam a mesma chave, e
// o servidor registra um único efeito. Uma operação nova abre uma tela nova,
// que traz chave nova. Rede/URL não é origem de chave.
(function () {
  function chave() {
    var aleatorio = new Uint8Array(16);
    if (window.crypto && crypto.getRandomValues) {
      crypto.getRandomValues(aleatorio);
    } else {
      for (var i = 0; i < 16; i++) aleatorio[i] = Math.floor(Math.random() * 256);
    }
    var hex = '';
    for (var j = 0; j < aleatorio.length; j++) {
      hex += (aleatorio[j] < 16 ? '0' : '') + aleatorio[j].toString(16);
    }
    return hex;
  }

  document.addEventListener('submit', function (evento) {
    var form = evento.target;
    if (!form || !form.querySelector) return;
    var campo = form.querySelector('input[name="idempotencia"]');
    if (campo && !campo.value) campo.value = chave();
  }, true);
})();
