(function () {
  "use strict";
  var form = document.getElementById("form");
  var input = document.getElementById("message");
  var log = document.getElementById("log");
  var status = document.getElementById("status");
  var state = null;

  var VERDICT_LABEL = {
    GO: "Good to go",
    GO_WITH_CARE: "Go with care",
    MODIFY: "Modify your plan",
    AVOID: "Avoid outdoor exertion",
    STAY_INDOORS: "Stay indoors",
    INDOORS_OK: "Indoors is a good choice"
  };

  function addMessage(who, text, cls) {
    var p = document.createElement("p");
    p.className = "msg " + cls;
    var label = document.createElement("span");
    label.className = "who";
    label.textContent = who + ": ";
    p.appendChild(label);
    p.appendChild(document.createTextNode(text)); // textContent only: no HTML injection
    log.appendChild(p);
    log.scrollTop = log.scrollHeight;
  }

  function badgeClass(aqi) {
    if (aqi <= 50) return "b0";
    if (aqi <= 100) return "b1";
    if (aqi <= 150) return "b2";
    if (aqi <= 200) return "b3";
    if (aqi <= 300) return "b4";
    return "b5";
  }

  function updateStatus(summary) {
    if (!summary || summary.aqi === null || summary.aqi === undefined) return;
    var text = "AQI " + summary.aqi;
    if (summary.location) text += " · " + summary.location;
    if (summary.verdict) text += " · " + (VERDICT_LABEL[summary.verdict] || "");
    status.textContent = text; // text is always shown, colour is only a reinforcement
    status.className = "badge " + badgeClass(summary.aqi);
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var message = input.value.trim();
    if (!message) return;
    addMessage("You", message, "user");
    input.value = "";
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message, state: state })
    })
      .then(function (r) {
        return r.json().then(function (j) { return { ok: r.ok, body: j }; });
      })
      .then(function (res) {
        if (!res.ok) { addMessage("AirGuide", res.body.error || "Something went wrong.", "bot"); return; }
        state = res.body.state;
        addMessage("AirGuide", res.body.reply, "bot");
        updateStatus(res.body.summary);
      })
      .catch(function () {
        addMessage("AirGuide", "I couldn't reach the server. Please try again.", "bot");
      });
    input.focus();
  });
})();
