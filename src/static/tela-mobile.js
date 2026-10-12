// Detalhe da vaga no celular (<= 640 px): abre por cima da lista e fecha com "Voltar", Esc ou o voltar do navegador.
// Sem eval nem handlers por atributo (CSP `default-src 'self'`). No desktop não faz nada.
(function () {
  "use strict";

  var celular = window.matchMedia("(max-width: 640px)");
  var cardAberto = null;   // url do card que abriu o detalhe: o foco volta para ele ao fechar
  var empurrados = 0;      // entradas que o hx-push-url pôs no histórico desde que a página abriu

  function detalhe() { return document.getElementById("detalhe"); }

  function aberto() {
    var el = detalhe();
    return !!el && el.hasAttribute("data-aberto") && !el.hasAttribute("data-fechado");
  }

  // Botão "Voltar" como primeiro filho do #detalhe (o swap troca o elemento inteiro; reinjeta a cada vez).
  function garantirBotao() {
    var el = detalhe();
    if (!el || el.querySelector(".voltar")) { return; }
    var botao = document.createElement("button");
    botao.type = "button";
    botao.className = "voltar";
    botao.setAttribute("aria-label", "Voltar para a lista de vagas");
    botao.textContent = "← Voltar";
    el.insertBefore(botao, el.firstChild);
  }

  function focarCard() {
    if (cardAberto === null) { return; }
    var cards = document.querySelectorAll("#lista-vagas .card");
    for (var i = 0; i < cards.length; i++) {
      if (cards[i].getAttribute("data-url") === cardAberto) { cards[i].focus({ preventScroll: true }); return; }
    }
  }

  function fechar() {
    if (!celular.matches || !aberto()) { return; }
    if (empurrados > 0) {
      // O detalhe abriu por um push nesta página: o histórico do navegador volta para a lista.
      empurrados -= 1;
      window.history.back();
      return;
    }
    // Abriu por link direto (/tela?url=...): não há entrada anterior; fecha aqui e limpa a url.
    detalhe().setAttribute("data-fechado", "");
    var u = new URL(window.location.href);
    u.searchParams.delete("url");
    window.history.replaceState(window.history.state, "", u.pathname + u.search);
    focarCard();
  }

  document.addEventListener("htmx:beforeRequest", function (evento) {
    var elt = evento.detail && evento.detail.elt;
    if (elt && elt.classList && elt.classList.contains("card")) {
      cardAberto = elt.getAttribute("data-url");
    }
  });

  document.addEventListener("htmx:pushedIntoHistory", function () { empurrados += 1; });

  document.addEventListener("htmx:afterSettle", function (evento) {
    var el = detalhe();
    if (!el) { return; }
    garantirBotao();
    if (evento.target === el && cardAberto !== null) {
      el.setAttribute("data-aberto", "");  // inclui "vaga não encontrada"
      if (celular.matches) {
        el.setAttribute("tabindex", "-1");
        el.focus({ preventScroll: true });
      }
    }
  });

  // Voltar/avançar do navegador: o htmx recoloca o corpo da página; reinjeta o botão e devolve o foco ao card.
  document.addEventListener("htmx:historyRestore", function () {
    garantirBotao();
    if (celular.matches && !aberto()) { focarCard(); }
  });

  document.addEventListener("click", function (evento) {
    if (evento.target.closest && evento.target.closest(".voltar")) { fechar(); }
  });

  document.addEventListener("keydown", function (evento) {
    if (evento.key === "Escape") { fechar(); }
  });

  // Link direto (/tela?url=...): a pessoa pediu a vaga, então abre. Sem ?url= a vaga já selecionada pelo servidor não abre.
  if (detalhe() && new URL(window.location.href).searchParams.has("url")) { detalhe().setAttribute("data-aberto", ""); }
  garantirBotao();
})();
