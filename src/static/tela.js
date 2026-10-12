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

  // Depois do POST de vista (204) o card dessa vaga perde o selo "nova".
  function tirarSelo(evento) {
    var form = evento.detail && evento.detail.elt;
    if (!form || form.getAttribute("hx-post") !== "/tela/vagas/vista" || !evento.detail.successful) { return; }
    var campo = form.querySelector("input[name=url]");
    if (!campo) { return; }
    var cards = document.querySelectorAll("#lista-vagas .card");
    for (var i = 0; i < cards.length; i++) {
      if (cards[i].getAttribute("data-url") === campo.value) {
        var selo = cards[i].querySelector(".selo-nova");
        if (selo) { selo.remove(); }
      }
    }
  }

  document.addEventListener("htmx:afterSettle", marcarSelecionado);
  document.addEventListener("htmx:afterRequest", tirarSelo);
  document.addEventListener("htmx:afterRequest", function (evento) {
    // O 422/404/409 contam como `successful` no htmx (responseHandling com error:false). Quando a
    // resposta é o próprio aviso (HX-Retarget "#aviso", de `resposta_de_erro`), escondê-lo apagaria a
    // mensagem; o 404 do detalhe (vaga que sumiu) troca só o #detalhe e limpa o aviso anterior.
    var xhr = evento.detail && evento.detail.xhr;
    if (evento.detail && evento.detail.successful && xhr && xhr.getResponseHeader("HX-Retarget") !== "#aviso") {
      esconderAviso();
    }
  });
  // 5xx e erro de rede não trocam nada (responseHandling): avisa em vez de falhar calado.
  document.addEventListener("htmx:responseError", function () {
    mostrarAviso("Algo deu errado no servidor. Tente de novo.");
  });
  document.addEventListener("htmx:sendError", function () {
    mostrarAviso("Sem conexão com o servidor. Tente de novo.");
  });
})();
