// Fold the documentation menu groups. "Desktop app" starts open, the other
// groups start folded, and the group that holds the current page is always
// open. A reader's own choices are remembered in this browser.
(function () {
  "use strict";
  var KEY = "adsfolio-docs-open-groups";

  function load() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(KEY));
      return saved && typeof saved === "object" ? saved : {};
    } catch (error) {
      return {};
    }
  }

  function save(state) {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(state));
    } catch (error) {
      // Storage can be unavailable, for example in a private window.
    }
  }

  function setup() {
    var menu = document.querySelector(".wy-menu-vertical");
    if (!menu) {
      return;
    }
    var state = load();
    var captions = menu.querySelectorAll("p.caption");
    captions.forEach(function (caption, index) {
      var list = caption.nextElementSibling;
      if (!list || list.tagName !== "UL") {
        return;
      }
      var name = caption.textContent.trim();
      var holdsCurrent = list.querySelector("li.current") !== null;
      var open = holdsCurrent || (name in state ? state[name] : index === 0);
      var id = "nav-group-" + index;
      list.id = id;
      caption.classList.add("nav-group-toggle");
      caption.setAttribute("role", "button");
      caption.setAttribute("tabindex", "0");
      caption.setAttribute("aria-controls", id);

      function apply(value) {
        caption.setAttribute("aria-expanded", value ? "true" : "false");
        list.hidden = !value;
      }

      function toggle() {
        var value = caption.getAttribute("aria-expanded") !== "true";
        apply(value);
        state[name] = value;
        save(state);
      }

      apply(open);
      caption.addEventListener("click", toggle);
      caption.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
})();
