/* Scroll behaviour for the landing page.

   Two effects, both built from the same idea: these are sheets of paper on a
   desk. Nothing here spins or bounces — paper does not.

   Everything degrades to a plain, fully-readable page. If this script never
   runs, or the visitor asked for reduced motion, the sheets simply sit where
   they belong and every word is legible. */

(function () {
  "use strict";

  var still = window.matchMedia("(prefers-reduced-motion: reduce)");

  function settle() {
    // the honest fallback: show everything, animate nothing
    document.querySelectorAll(".reveal, .deck, .stage")
      .forEach(function (el) { el.classList.add("shown", "settled"); });
  }

  if (still.matches || !("IntersectionObserver" in window)) {
    settle();
    return;
  }

  /* --- sections rise as they come into view ------------------------- */

  var watcher = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("shown");
        watcher.unobserve(entry.target);   // once it has arrived, leave it
      }
    });
  }, { rootMargin: "0px 0px -12% 0px", threshold: 0.08 });

  document.querySelectorAll(".reveal, .deck").forEach(function (el) {
    watcher.observe(el);
  });

  /* --- the email lifts off the clause underneath it ------------------ */

  var stage = document.querySelector(".stage");
  // the custom property goes on the section, not the stage: the caption is a
  // sibling of the stage and would never inherit a variable set on it
  var pair = document.querySelector(".reveal-pair");
  var deck = document.querySelector(".deck");
  var ticking = false;

  function progress(el, from, to) {
    // 0 while the element is below `from` of the viewport, 1 once it has
    // travelled up to `to`. Clamped, so nothing overshoots.
    var box = el.getBoundingClientRect();
    var h = window.innerHeight || 1;
    var p = (h * from - box.top) / (h * (from - to));
    return Math.max(0, Math.min(1, p));
  }

  function frame() {
    ticking = false;

    if (stage && pair) {
      pair.style.setProperty("--lift", progress(stage, 0.85, 0.35).toFixed(3));
    }
    if (deck) {
      // the leaves fan out as the deck crosses the middle of the screen
      deck.style.setProperty("--fan", progress(deck, 0.9, 0.4).toFixed(3));
    }
  }

  function onScroll() {
    if (!ticking) {
      ticking = true;
      window.requestAnimationFrame(frame);
    }
  }

  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll, { passive: true });
  frame();

  // if the visitor turns motion off while the page is open, stop moving
  still.addEventListener("change", function (e) {
    if (e.matches) {
      window.removeEventListener("scroll", onScroll);
      if (pair) pair.style.removeProperty("--lift");
      if (deck) deck.style.removeProperty("--fan");
      settle();
    }
  });
})();
