(function () {
  "use strict";

  var form = document.getElementById("form");
  var input = document.getElementById("message");
  var sendBtn = document.getElementById("send-btn");
  var log = document.getElementById("log");
  var status = document.getElementById("status");
  var engineBadge = document.getElementById("engine-badge");
  var ttsBtn = document.getElementById("tts-btn");
  var ttsStatus = document.getElementById("tts-status");
  var resetBtn = document.getElementById("reset-btn");
  var loadingStatus = document.getElementById("loading-status");
  var chips = document.querySelectorAll(".chip[data-prompt]");
  var geoBtn = document.getElementById("geo-btn");
  var livePanel = document.getElementById("live-panel");
  var liveRefreshBtn = document.getElementById("live-refresh");
  var LIVE_REFRESH_MS = 10 * 60 * 1000;
  var liveTimer = null;
  var liveCoords = null;
  var liveLabel = "";
  var lastLevel = null;

  var PROFILE_KEY = "airguide.profile.v1";
  var ALERT_KEY = "airguide.alert.v1";
  var GROUPS = { lung: "a lung condition", heart: "a heart condition", child: "a child", elderly: "age 65+", pregnant: "pregnancy" };

  // localStorage can be unavailable (private mode, blocked): never let it break the app.
  function store(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify(value));
    } catch (e) { /* ignore */ }
  }
  function load(key) {
    try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; }
  }

  // Opt-in: only the health profile (sensitive groups) is remembered, never the location.
  function savedProfile() {
    var p = load(PROFILE_KEY);
    if (!p || !Array.isArray(p.sensitivity)) return null;
    return p.sensitivity.filter(function (g) { return GROUPS.hasOwnProperty(g); });
  }
  function seedState() {
    var groups = savedProfile();
    return groups ? { sensitivity: groups } : null;
  }
  var state = seedState();
  var ttsEnabled = false;

  var VERDICT_LABEL = {
    GO: "Good to go (Normal outdoor activity)",
    GO_WITH_CARE: "Go with care (Take breaks, sensitive take note)",
    MODIFY: "Modify plan (Reduce intensity & duration)",
    AVOID: "Avoid outdoor exertion (Reschedule/indoor)",
    STAY_INDOORS: "Stay indoors (Close windows, air purifier)",
    INDOORS_OK: "Indoors is a good choice (Keep clean air)"
  };

  function announce(text) {
    if (loadingStatus) {
      loadingStatus.textContent = text;
    }
  }

  function formatReplyIntoBubble(container, text) {
    var lines = text.split("\n");
    var currentList = null;

    lines.forEach(function (line) {
      var trimmed = line.trim();
      if (!trimmed) return;

      if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
        if (!currentList) {
          currentList = document.createElement("ul");
          container.appendChild(currentList);
        }
        var li = document.createElement("li");
        li.appendChild(document.createTextNode(trimmed.slice(2)));
        currentList.appendChild(li);
      } else {
        currentList = null;
        var p = document.createElement("p");
        p.appendChild(document.createTextNode(trimmed));
        container.appendChild(p);
      }
    });
  }

  function addMessage(who, text, cls) {
    var msgDiv = document.createElement("div");
    msgDiv.className = "msg " + cls;

    var label = document.createElement("span");
    label.className = "who";
    label.textContent = who + ":";
    msgDiv.appendChild(label);

    var bubble = document.createElement("div");
    bubble.className = "bubble";
    formatReplyIntoBubble(bubble, text);
    msgDiv.appendChild(bubble);

    log.appendChild(msgDiv);
    log.scrollTop = log.scrollHeight;

    if (ttsEnabled && cls === "bot" && window.speechSynthesis) {
      try {
        window.speechSynthesis.cancel();
        var utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.0;
        window.speechSynthesis.speak(utterance);
      } catch (e) {}
    }
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
    if (summary.location) text += " \u00B7 " + summary.location;
    if (summary.verdict) text += " \u00B7 " + (VERDICT_LABEL[summary.verdict] || summary.verdict);
    status.textContent = text;
    status.className = "badge " + badgeClass(summary.aqi);
  }

  function checkHealth() {
    fetch("/healthz")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.provider && engineBadge) {
          engineBadge.textContent = data.provider;
        }
      })
      .catch(function () {});
  }


  // ---- Saved health profile (opt-in) ----
  var rememberBox = document.getElementById("remember-profile");
  var profileSummary = document.getElementById("profile-summary");

  function describeProfile(groups) {
    if (!groups) return "";
    if (!groups.length) return "Saved: no sensitive conditions";
    return "Saved: " + groups.map(function (g) { return GROUPS[g]; }).join(", ");
  }

  function persistProfile() {
    if (!rememberBox || !rememberBox.checked) return;
    if (state && Array.isArray(state.sensitivity)) {
      store(PROFILE_KEY, { sensitivity: state.sensitivity });
      profileSummary.textContent = describeProfile(state.sensitivity);
    }
  }

  if (rememberBox) {
    var existing = savedProfile();
    rememberBox.checked = existing !== null;
    profileSummary.textContent = describeProfile(existing);
    rememberBox.addEventListener("change", function () {
      if (rememberBox.checked) {
        persistProfile();
      } else {
        store(PROFILE_KEY, null);
        profileSummary.textContent = "Profile cleared from this device";
      }
    });
  }

  // ---- AQI threshold alerts ----
  var alertToggle = document.getElementById("alert-toggle");
  var alertSelect = document.getElementById("alert-threshold");
  var alertCfg = load(ALERT_KEY) || { on: false, threshold: 151 };
  var alertedNow = false; // already alerted for the current excursion above the threshold
  var lastLiveData = null; // latest reading, so changing alert settings applies immediately

  function renderAlertControls() {
    if (!alertToggle) return;
    alertToggle.setAttribute("aria-pressed", alertCfg.on ? "true" : "false");
    alertToggle.textContent = "\uD83D\uDD14 Alerts: " + (alertCfg.on ? "On" : "Off");
    alertSelect.value = String(alertCfg.threshold);
  }

  function checkAlert(live, idx) {
    if (!alertCfg.on || live.aqi === null || live.aqi === undefined) return;
    if (live.aqi < alertCfg.threshold) { alertedNow = false; return; }
    if (alertedNow) return;
    alertedNow = true;
    var text = "AQI " + live.aqi + " (" + CATEGORY[idx] + ") in " + (liveLabel || live.place) +
      " has reached your alert level of " + alertCfg.threshold + ".";
    addMessage("AirGuide", "Alert: " + text, "bot");
    announce("Air quality alert. " + text);
    try {
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification("AirGuide air quality alert", { body: text });
      }
    } catch (e) { /* notifications unsupported */ }
  }

  if (alertToggle) {
    renderAlertControls();
    alertToggle.addEventListener("click", function () {
      alertCfg.on = !alertCfg.on;
      alertedNow = false;
      store(ALERT_KEY, alertCfg);
      renderAlertControls();
      if (alertCfg.on && lastLiveData) checkAlert(lastLiveData, levelIndex(lastLiveData.aqi));
      if (alertCfg.on && "Notification" in window && Notification.permission === "default") {
        try { Notification.requestPermission(); } catch (e) { /* ignore */ }
      }
    });
    alertSelect.addEventListener("change", function () {
      alertCfg.threshold = parseInt(alertSelect.value, 10) || 151;
      alertedNow = false;
      store(ALERT_KEY, alertCfg);
      if (alertCfg.on && lastLiveData) checkAlert(lastLiveData, levelIndex(lastLiveData.aqi));
    });
  }

  // ---- Live conditions panel (real-time data) ----
  var CATEGORY = ["Good", "Moderate", "Unhealthy for Sensitive Groups", "Unhealthy", "Very Unhealthy", "Hazardous"];
  var SVG_NS = "http://www.w3.org/2000/svg";

  function levelIndex(aqi) {
    return aqi <= 50 ? 0 : aqi <= 100 ? 1 : aqi <= 150 ? 2 : aqi <= 200 ? 3 : aqi <= 300 ? 4 : 5;
  }

  function hourLabel(iso) {
    var h = parseInt(String(iso).slice(11, 13), 10);
    if (isNaN(h)) return "";
    return (h % 12 || 12) + (h < 12 ? " AM" : " PM");
  }

  function svgEl(name, attrs) {
    var el = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    return el;
  }

  function drawForecast(rows) {
    var chart = document.getElementById("live-chart");
    while (chart.firstChild) chart.removeChild(chart.firstChild);
    if (!rows || !rows.length) return;
    var max = Math.max(150, Math.max.apply(null, rows.map(function (r) { return r.aqi; })));
    rows.forEach(function (r, i) {
      var h = Math.max(3, Math.round((r.aqi / max) * 84));
      var rect = svgEl("rect", {
        x: i * 20, y: 92 - h, width: 16, height: h, rx: 2,
        "class": "f" + levelIndex(r.aqi) + (i === 0 ? " now" : "")
      });
      var title = svgEl("title");
      title.textContent = hourLabel(r.time) + ": AQI " + r.aqi + " (" + CATEGORY[levelIndex(r.aqi)] + ")";
      rect.appendChild(title);
      chart.appendChild(rect);
      if (i % 6 === 0) {
        var label = svgEl("text", { x: i === 0 ? 0 : i * 20 + 8, y: 108, "class": i === 0 ? "axis first" : "axis" });
        label.textContent = i === 0 ? "Now" : hourLabel(r.time);
        chart.appendChild(label);
      }
    });
  }

  function addPollutant(list, name, value, unit) {
    if (value === null || value === undefined) return;
    var box = document.createElement("div");
    var dt = document.createElement("dt");
    dt.textContent = name;
    var dd = document.createElement("dd");
    dd.appendChild(document.createTextNode(Math.round(value * 10) / 10 + " "));
    var small = document.createElement("small");
    small.textContent = unit;
    dd.appendChild(small);
    box.appendChild(dt);
    box.appendChild(dd);
    list.appendChild(box);
  }

  function renderLive(live, label) {
    if (!live || live.aqi === null || live.aqi === undefined) { hideLive(); return; }
    liveCoords = (live.lat !== null && live.lon !== null) ? { lat: live.lat, lon: live.lon } : null;
    liveLabel = label || live.place || liveLabel;
    document.getElementById("live-place").textContent = liveLabel;
    var idx = levelIndex(live.aqi);
    var badge = document.getElementById("live-aqi");
    badge.textContent = "AQI " + live.aqi + " \u00B7 " + CATEGORY[idx];
    badge.className = "badge b" + idx;
    document.getElementById("live-dominant").textContent =
      live.dominant ? "Main pollutant: " + live.dominant : "";
    var list = document.getElementById("live-pollutants");
    list.innerHTML = "";
    addPollutant(list, "PM2.5", live.pm25, "\u00B5g/m\u00B3");
    addPollutant(list, "PM10", live.pm10, "\u00B5g/m\u00B3");
    addPollutant(list, "O\u2083", live.ozone, "\u00B5g/m\u00B3");
    addPollutant(list, "NO\u2082", live.no2, "\u00B5g/m\u00B3");
    addPollutant(list, "SO\u2082", live.so2, "\u00B5g/m\u00B3");
    addPollutant(list, "CO", live.co, "\u00B5g/m\u00B3");
    drawForecast(live.forecast);
    var when = live.observed_at ? "Updated " + hourLabel(live.observed_at) + " local time \u00B7 " : "";
    var note = (lastLevel !== null && lastLevel !== idx) ? " \u00B7 Air quality category changed" : "";
    document.getElementById("live-updated").textContent =
      when + live.provider + " \u00B7 auto-refreshes every 10 min" + note;
    lastLevel = idx;
    lastLiveData = live;
    checkAlert(live, idx);
    livePanel.hidden = false;
    if (!liveTimer) liveTimer = setInterval(refreshLive, LIVE_REFRESH_MS);
  }

  function hideLive() {
    livePanel.hidden = true;
    lastLevel = null;
    alertedNow = false;
    lastLiveData = null;
    if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  }

  function refreshLive() {
    if (!liveCoords) return;
    liveRefreshBtn.disabled = true;
    fetch("/api/live?lat=" + liveCoords.lat + "&lon=" + liveCoords.lon)
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        liveRefreshBtn.disabled = false;
        if (res.ok) renderLive(res.body.live, liveLabel);
        else document.getElementById("live-updated").textContent = res.body.error || "Refresh failed";
      })
      .catch(function () {
        liveRefreshBtn.disabled = false;
        document.getElementById("live-updated").textContent = "Offline: showing the last reading";
      });
  }

  if (liveRefreshBtn) liveRefreshBtn.addEventListener("click", refreshLive);

  function shareLocation() {
    if (!navigator.geolocation) {
      addMessage("AirGuide", "Your browser doesn't support location sharing. Please type your city instead.", "bot");
      return;
    }
    geoBtn.disabled = true;
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        geoBtn.disabled = false;
        sendMessage("What is the air quality at my location?", {
          lat: Math.round(pos.coords.latitude * 10000) / 10000,
          lon: Math.round(pos.coords.longitude * 10000) / 10000
        });
      },
      function () {
        geoBtn.disabled = false;
        addMessage("AirGuide", "I couldn't get your location (permission denied or unavailable). Please type your city instead.", "bot");
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
    );
  }

  if (geoBtn) geoBtn.addEventListener("click", shareLocation);

  function sendMessage(message, coords) {
    if (!message) return;
    addMessage("You", message, "user");
    input.value = "";
    sendBtn.disabled = true;
    log.setAttribute("aria-busy", "true");
    announce("AirGuide is evaluating air quality data and formulating guidance...");

    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message, state: state, coords: coords || undefined })
    })
      .then(function (r) {
        return r.json().then(function (j) { return { ok: r.ok, body: j }; });
      })
      .then(function (res) {
        sendBtn.disabled = false;
        log.setAttribute("aria-busy", "false");
        if (!res.ok) {
          var errMsg = res.body.error || "Something went wrong.";
          addMessage("AirGuide", errMsg, "bot");
          announce("Error: " + errMsg);
          return;
        }
        state = res.body.state;
        persistProfile();
        addMessage("AirGuide", res.body.reply, "bot");
        updateStatus(res.body.summary);
        announce("New guidance received from AirGuide.");
        if (res.body.live) renderLive(res.body.live, res.body.summary && res.body.summary.location);
        else if (!state || state.source !== "live") hideLive();
        if (res.body.provider && engineBadge) {
          engineBadge.textContent = res.body.provider;
        }
      })
      .catch(function () {
        sendBtn.disabled = false;
        log.setAttribute("aria-busy", "false");
        var connErr = "I couldn't reach the server. Please check your connection and try again.";
        addMessage("AirGuide", connErr, "bot");
        announce(connErr);
      });

    input.focus();
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    sendMessage(input.value.trim(), null);
  });

  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var prompt = chip.getAttribute("data-prompt");
      if (prompt) {
        input.value = prompt;
        sendMessage(prompt, null);
      }
    });
  });

  if (ttsBtn && ttsStatus) {
    ttsBtn.addEventListener("click", function () {
      ttsEnabled = !ttsEnabled;
      ttsBtn.setAttribute("aria-pressed", ttsEnabled ? "true" : "false");
      ttsStatus.textContent = ttsEnabled ? "On" : "Off";
      if (!ttsEnabled && window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
      announce(ttsEnabled ? "Voice narration enabled." : "Voice narration disabled.");
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      state = seedState();
      hideLive();
      status.textContent = "No reading yet";
      status.className = "badge badge-none";
      log.innerHTML = "";
      addMessage("AirGuide", "Conversation reset. Where are you located, or what is your current AQI?", "bot");
      announce("Conversation has been reset.");
      input.value = "";
      input.focus();
    });
  }

  checkHealth();
})();
