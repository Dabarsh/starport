// Talks to the Python side. Python calls window.render(state); pages call send(cmd, data).
(function () {
  window.send = function (cmd, data) {
    const msg = JSON.stringify(Object.assign({ cmd: cmd }, data || {}));
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.starport) {
      window.webkit.messageHandlers.starport.postMessage(msg);
    } else {
      console.log("send", msg);
    }
  };

  window.$ = (sel, root) => (root || document).querySelector(sel);
  window.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  window.esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  window.STAR_PATH = "M12 3l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.8z";

  window.starText = (n) => {
    if (n == null || n < 0) return "";
    return '<span class="stars">' + "★".repeat(n) + '<span class="off">' + "★".repeat(5 - n) + "</span></span>";
  };

  // Disable the web view's own context menu and text selection outside inputs.
  document.addEventListener("contextmenu", (e) => {
    if (!(e.target instanceof HTMLInputElement)) e.preventDefault();
  });

  window.addEventListener("DOMContentLoaded", () => send("ready"));
})();
