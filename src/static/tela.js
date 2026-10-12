// JS mínimo da tela. Sem eval nem handlers por atributo (a CSP `default-src 'self'` bloqueia os dois).
(function () {
  "use strict";

  var aviso = function () { return document.getElementById("aviso"); };

  function mostrarAviso(texto) {
    var el = aviso();
    if (!el) { return; }
    el.textContent = texto;
    el.hidden = false;
  }

  function esconderAviso() {
    var el = aviso();
    if (el) { el.hidden = true; }
  }

  // O card selecionado é o que tem a url do detalhe aberto (`data-url` do #detalhe).
  function marcarSelecionado() {
    var detalhe = document.getElementById("detalhe");
    var url = detalhe ? detalhe.getAttribute("data-url") : null;
    var cards = document.querySelectorAll("#lista-vagas .card");
    for (var i = 0; i < cards.length; i++) {
      if (url !== null && cards[i].getAttribute("data-url") === url) {
        cards[i].setAttribute("aria-current", "true");
      } else {
        cards[i].removeAttribute("aria-current");
      }
    }
  }

  document.addEventListener("htmx:afterSettle", marcarSelecionado);
  document.addEventListener("htmx:afterRequest", function (evento) {
    if (evento.detail && evento.detail.successful) { esconderAviso(); }
  });
  // 5xx e erro de rede não trocam nada (responseHandling): avisa em vez de falhar calado.
  document.addEventListener("htmx:responseError", function () {
    mostrarAviso("Algo deu errado no servidor. Tente de novo.");
  });
  document.addEventListener("htmx:sendError", function () {
    mostrarAviso("Sem conexão com o servidor. Tente de novo.");
  });
})();
