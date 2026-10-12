// Detalhe da vaga no celular (<= 640 px): abre por cima da lista e fecha com "Voltar", Esc ou o voltar do navegador.
// Sem eval nem handlers por atributo (CSP `default-src 'self'`). No desktop não faz nada.
(function () {
  "use strict";

  var celular = window.matchMedia("(max-width: 640px)");
  var cardAberto = null;   // url do card que abriu o detalhe: o foco volta para ele ao fechar
  // Entradas do histórico que um card abriu (hx-push-url), pela chave da Navigation API
  // (`navigation.currentEntry.key`, estável no voltar/avançar). Não dá para guardar no history.state:
  // o htmx o troca por {htmx: true} ao sair e ao voltar para cada entrada. Um contador global também
  // não servia: o push da busca e o voltar/avançar do navegador o desalinhavam.
  var CHAVE = "vagas.abertasPorCard";
  var porCard = lerMarcas();
  var pushDeCard = false; // o beforeHistoryUpdate vem de um card; o pushedIntoHistory logo depois marca a entrada nova

  function lerMarcas() {
    try { return JSON.parse(window.sessionStorage.getItem(CHAVE) || "{}") || {}; } catch (e) { return {}; }
  }

  function gravarMarcas() {
    try { window.sessionStorage.setItem(CHAVE, JSON.stringify(porCard)); } catch (e) { /* só em memória */ }
  }

  function navegacao() {
    var n = window.navigation;
    return n && n.currentEntry && typeof n.entries === "function" ? n : null;
  }

  // Quantos passos para trás saem da sequência de entradas abertas por card e caem onde ela começou
  // (a lista, ou o link direto de onde a pessoa abriu o primeiro card). 0 = esta entrada não abriu por card.
  function passosAteALista() {
    var n = navegacao();
    if (!n) { return 0; }
    var entradas = n.entries();
    var i = n.currentEntry.index;
    var passos = 0;
    while (i - passos > 0 && entradas[i - passos] && porCard[entradas[i - passos].key]) { passos += 1; }
    return passos;
  }

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
    var passos = passosAteALista();
    if (passos > 0) {
      // Esta entrada foi aberta por um card: volta de uma vez para onde a sequência de cards começou.
      window.history.go(-passos);
      return;
    }
    // Não abriu por card (link direto /tela?url=..., entrada da busca ou filtro com o detalhe ainda
    // aberto, ou navegador sem Navigation API): fecha aqui e limpa a url, sem sair do site.
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

  document.addEventListener("htmx:beforeHistoryUpdate", function (evento) {
    var d = evento.detail || {};
    // `detail.elt` aqui é o body (quem dispara o evento); quem fez o request está no requestConfig.
    var elt = d.requestConfig && d.requestConfig.elt;
    pushDeCard = !!(elt && elt.classList && elt.classList.contains("card")) && !!d.history && d.history.type === "push";
  });

  document.addEventListener("htmx:pushedIntoHistory", function () {
    var n = navegacao();
    if (pushDeCard && n) {
      porCard[n.currentEntry.key] = true;
      gravarMarcas();
    }
    pushDeCard = false;
  });

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
