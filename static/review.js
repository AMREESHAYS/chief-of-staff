/* Motion for the review surface.

   Deliberately smaller than the landing page. This is a document somebody is
   working through — flagging scope, approving drafts — so a message settles
   into place once and then stays put. Nothing re-animates while you read.

   Two things here are load-bearing:

   1. The transform only ever goes on `.entry`. Any transformed ancestor
      becomes the containing block for position:sticky inside it, which would
      break the ledger rail without any error to notice.

   2. htmx replaces action fragments in place. Those live *inside* an entry
      that has already settled, so they inherit its visibility and need no
      handling — but if a whole entry is ever swapped, the observer below
      picks it up again rather than leaving it invisible forever.
*/

(function () {
  "use strict";

  var root = document.documentElement;
  root.classList.add("js");

  var still = window.matchMedia("(prefers-reduced-motion: reduce)");

  function showAll() {
    root.classList.add("no-motion");
    document.querySelectorAll(".entry").forEach(function (el) {
      el.classList.add("settled");
    });
  }

  if (still.matches || !("IntersectionObserver" in window)) {
    showAll();
    return;
  }

  var watcher = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) {
        e.target.classList.add("settled");
        watcher.unobserve(e.target);      // settled means settled
      }
    });
  }, { rootMargin: "0px 0px -6% 0px", threshold: 0.04 });

  function watch(scope) {
    (scope || document).querySelectorAll(".entry:not(.settled)")
      .forEach(function (el) { watcher.observe(el); });
  }

  watch();

  // anything htmx brings in later gets the same treatment
  document.body.addEventListener("htmx:afterSwap", function (e) {
    var entry = e.target.closest ? e.target.closest(".entry") : null;
    if (entry) entry.classList.add("settled");   // already on screen, keep it
    watch();
  });

  still.addEventListener("change", function (e) {
    if (e.matches) {
      watcher.disconnect();
      showAll();
    }
  });
})();
