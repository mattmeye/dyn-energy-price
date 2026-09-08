/* Oberfläche des Add-ons: lädt die JSON-Endpunkte und zeichnet die Ansichten. */
(function () {
  "use strict";

  var C = window.nsCharts;
  var state = { settings: null, entities: null, forecast: null };

  function $(id) { return document.getElementById(id); }

  function api(path, options) {
    return fetch(path, options).then(function (response) {
      if (!response.ok) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          throw new Error(body.error || ("HTTP " + response.status));
        });
      }
      return response.json();
    });
  }

  function post(path, payload) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    });
  }

  function fmt(value, digits) { return C.fmt(value, digits === undefined ? 2 : digits); }

  function hhmm(iso) {
    return iso ? C.hhmm(new Date(iso)) : "–";
  }

  function dayLabel(iso) {
    if (!iso) return "–";
    var parts = iso.split("-");
    return parts[2] + "." + parts[1] + ".";
  }

  var VERDICT = {
    verschiebbar: { text: "ausreichend", cls: "good" },
    teilweise: { text: "limitiert", cls: "warn" },
    nicht_verschiebbar: { text: "nicht ausreichend", cls: "bad" },
    nicht_noetig: { text: "keine Verschiebung nötig", cls: "neutral" }
  };

  /* ------------------------------------------------------------ Navigation */

  function initTabs() {
    var buttons = document.querySelectorAll("nav button");
    Array.prototype.forEach.call(buttons, function (button) {
      button.addEventListener("click", function () {
        Array.prototype.forEach.call(buttons, function (other) {
          other.setAttribute("aria-current", other === button ? "true" : "false");
        });
        ["overview", "history", "setup", "log"].forEach(function (name) {
          $("tab-" + name).hidden = name !== button.dataset.tab;
        });
        if (button.dataset.tab === "history") loadHistory();
        if (button.dataset.tab === "log") loadRuns();
        if (button.dataset.tab === "setup" && !state.entities) loadEntities();
      });
    });
  }

  /* -------------------------------------------------------------- Übersicht */

  function tile(label, value, foot, hero) {
    return '<div class="tile"><div class="label">' + label + '</div><div class="value' +
      (hero ? " hero" : "") + '">' + value + '</div>' +
      (foot ? '<div class="foot">' + foot + "</div>" : "") + "</div>";
  }

  function renderWarnings(payload) {
    var host = $("warnings");
    var items = (payload.warnings || []).map(function (text) {
      var bad = text.indexOf("teurer") >= 0;
      return '<div class="alert' + (bad ? " bad" : "") + '">' + text + "</div>";
    });
    host.innerHTML = items.join("");
  }

  function renderTiles(payload) {
    var savings = payload.savings;
    var storage = payload.storage;
    var window_ = payload.window;
    var verdict = VERDICT[storage.verdict] || { text: storage.verdict, cls: "neutral" };
    var badge = '<span class="badge ' + verdict.cls + '"><span class="dot"></span>' + verdict.text + "</span>";

    $("tiles").innerHTML = [
      tile("Ersparnis gegenüber Fixtarif",
        '<span style="color:' + (savings.vs_fixed_eur < 0 ? "var(--critical)" : "inherit") + '">' +
        fmt(savings.vs_fixed_eur) + " €</span>",
        "ohne Verschiebung " + fmt(savings.vs_fixed_unshifted_eur) + " € · ohne Speichergrenzen " +
        fmt(savings.vs_fixed_ideal_eur) + " €", true),
      tile("Günstigstes Ladefenster",
        hhmm(window_.start_utc) + "–" + hhmm(window_.end_utc),
        "Ø " + fmt(window_.avg_price_ct) + " ct/kWh"),
      tile("Ø Preis ohne Verschiebung", fmt(payload.prices.day_avg_ct) + " ct",
        "günstigste Viertelstunde " + fmt(payload.prices.cheapest_slot_ct) + " ct"),
      tile("Erwarteter Netzbezug", fmt(payload.energy.import_kwh, 1) + " kWh",
        "PV-Prognose " + fmt(payload.energy.pv_kwh, 1) + " kWh"),
      tile("Speicher", badge,
        fmt(storage.shifted_kwh, 1) + " von " + fmt(storage.needed_kwh, 1) + " kWh verschiebbar"),
      tile("Kosten des Tages", fmt(payload.costs.smart_shifted_eur) + " €",
        "Fixtarif " + fmt(payload.costs.fixed_eur) + " € · Grundpreisanteil " +
        fmt(payload.costs.base_price_delta_eur) + " €")
    ].join("");
  }

  function renderStorage(payload) {
    var storage = payload.storage;
    var verdict = VERDICT[storage.verdict] || { text: storage.verdict, cls: "neutral" };
    $("storage-verdict").innerHTML =
      "Ergebnis: <b>" + verdict.text + "</b> – " + fmt(storage.shifted_kwh, 1) + " kWh von " +
      fmt(storage.needed_kwh, 1) + " kWh lassen sich in das Fenster verschieben, " +
      fmt(storage.not_shifted_kwh, 1) + " kWh bleiben am Netz.";

    var rows = [
      ["Benötigte Speicherladung", fmt(storage.capacity_needed_kwh, 1) + " kWh",
        "Freie Kapazität zu Fensterbeginn", fmt(storage.capacity_available_kwh, 1) + " kWh"],
      ["Belegt durch Ladezustand", fmt(storage.soc_at_window_start_kwh, 1) + " kWh",
        "Für PV freigehalten", fmt(storage.pv_reserved_kwh, 1) + " kWh"],
      ["Benötigte Netzladeleistung", fmt(storage.charge_power_needed_kw, 1) + " kW",
        "Verfügbare Netzladeleistung", fmt(storage.charge_power_available_kw, 1) + " kW"],
      ["Bezug über Entladeleistung", fmt(storage.discharge_blocked_kwh, 1) + " kWh",
        "Im Fenster ungenutzte Kapazität", fmt(storage.unused_capacity_kwh, 1) + " kWh"]
    ];
    $("storage-table").innerHTML =
      "<tbody>" + rows.map(function (row) {
        return "<tr><td>" + row[0] + "</td><td>" + row[1] + "</td><td>" + row[2] +
          "</td><td>" + row[3] + "</td></tr>";
      }).join("") + "</tbody>";

    $("storage-reasons").innerHTML = (storage.reasons || []).map(function (text) {
      return "<li>" + text + "</li>";
    }).join("");
  }

  function renderCharts(payload) {
    var series = payload.series;
    var times = payload.slots_utc.map(function (iso) { return new Date(iso); });
    var window_ = payload.window;
    var startIndex = null, endIndex = null;
    if (window_.start_utc) {
      startIndex = payload.slots_utc.indexOf(window_.start_utc);
      var endSlot = new Date(window_.end_utc).getTime();
      endIndex = payload.slots_utc.length;
      for (var i = 0; i < payload.slots_utc.length; i++) {
        if (new Date(payload.slots_utc[i]).getTime() >= endSlot) { endIndex = i; break; }
      }
    }

    C.legend($("legend-price"), [
      { name: "All-in-Arbeitspreis smart", color: "var(--series-1)" },
      { name: "Fixtarif", color: "var(--series-2)" }
    ]);
    C.priceChart($("chart-price"), {
      times: times, values: series.all_in_ct, spot: series.spot_eur_mwh,
      bandStart: startIndex, bandEnd: endIndex,
      reference: state.settings ? state.settings.tariff.fixed_price_ct : 31,
      referenceLabel: "Fixtarif"
    });

    var stacks = [
      { name: "Haushalt", color: "var(--series-1)", values: series.household_kwh },
      { name: "Auto", color: "var(--series-2)", values: series.ev_kwh }
    ];
    var hasHeatpump = series.heatpump_kwh.some(function (value) { return value > 0; });
    if (hasHeatpump) {
      stacks.push({ name: "Wärmepumpe", color: "var(--series-3)", values: series.heatpump_kwh });
    }
    C.legend($("legend-load"), stacks.concat([
      { name: "Netzbezug", color: "var(--text-secondary)" }
    ]));
    C.stackChart($("chart-load"), {
      times: times, stacks: stacks,
      line: { name: "Netzbezug", color: "var(--text-secondary)", values: series.grid_import_kwh }
    });

    C.lineChart($("chart-soc"), {
      labels: times.map(function (date) { return C.hhmm(date); }),
      series: [{ name: "Speicher", color: "var(--series-1)", values: series.soc_kwh }],
      unit: "kWh", digits: 0, height: 200
    });
  }

  function loadForecast(day) {
    var query = day ? "?day=" + encodeURIComponent(day) : "";
    return api("api/forecast" + query).then(function (data) {
      if (data.status !== "ok") {
        $("result-day").textContent = data.day || "–";
        $("result-sources").textContent = "Für diesen Tag liegt noch keine Bewertung vor.";
        $("tiles").innerHTML = "";
        $("storage-table").innerHTML = "";
        $("storage-reasons").innerHTML = "";
        $("storage-verdict").textContent = "";
        ["chart-price", "chart-load", "chart-soc"].forEach(function (id) { $(id).innerHTML = ""; });
        return;
      }
      state.forecast = data.forecast;
      var payload = data.forecast;
      $("result-day").textContent = payload.day +
        (payload.scenario !== "standard" ? " (Szenario " + payload.scenario + ")" : "");
      $("result-sources").textContent =
        "Last: " + payload.sources.load + " · PV: " + payload.sources.pv +
        " · erstellt " + new Date(payload.generated_at).toLocaleString("de-DE");
      $("day-picker").value = payload.day;
      renderWarnings(payload);
      renderTiles(payload);
      renderStorage(payload);
      renderCharts(payload);
    });
  }

  /* ---------------------------------------------------------------- Verlauf */

  function renderMonths(tableId, rows) {
    if (!rows.length) { $(tableId).innerHTML = "<tbody><tr><td>Noch keine Daten</td></tr></tbody>"; return; }
    var head = "<thead><tr><th>Zeitraum</th><th>Tage</th><th>Prognose €</th><th>Ist €</th>" +
      "<th>Abw. €</th><th>ohne Versch. €</th><th>ideal €</th><th>Kap.-Limit</th>" +
      "<th>Leist.-Limit</th><th>Ø ungenutzt kWh</th><th>Bezug kWh</th></tr></thead>";
    var body = rows.map(function (row) {
      return "<tr><td>" + row.label + "</td><td>" + row.days + "</td><td>" +
        fmt(row.forecast_saving_eur) + "</td><td>" +
        (row.days_with_actual ? fmt(row.realised_saving_eur) : "–") + "</td><td>" +
        (row.days_with_actual ? fmt(row.saving_error_eur) : "–") + "</td><td>" +
        fmt(row.unshifted_saving_eur) + "</td><td>" + fmt(row.ideal_saving_eur) + "</td><td>" +
        Math.round(row.share_capacity_limited * 100) + " %</td><td>" +
        Math.round(row.share_power_limited * 100) + " %</td><td>" +
        fmt(row.avg_unused_capacity_kwh, 1) + "</td><td>" +
        fmt(row.forecast_import_kwh, 0) + "</td></tr>";
    }).join("");
    $(tableId).innerHTML = head + "<tbody>" + body + "</tbody>";
  }

  function loadHistory() {
    return api("api/history?days=400").then(function (data) {
      var days = data.days || [];
      var labels = days.map(function (row) { return dayLabel(row.day); });

      C.legend($("legend-daily"), [
        { name: "Prognose", color: "var(--series-1)" },
        { name: "Ist", color: "var(--series-3)" }
      ]);
      C.barChart($("chart-daily"), {
        labels: labels, unit: "€", digits: 1,
        series: [
          { name: "Prognose", color: "var(--series-1)", values: days.map(function (r) { return r.forecast_saving_eur; }) },
          { name: "Ist", color: "var(--series-3)", values: days.map(function (r) { return r.actual_saving_eur; }) }
        ]
      });

      C.legend($("legend-cum"), [
        { name: "Prognose kumuliert", color: "var(--series-1)" },
        { name: "Ist kumuliert", color: "var(--series-3)" }
      ]);
      C.lineChart($("chart-cum"), {
        labels: labels, unit: "€", digits: 0,
        series: [
          { name: "Prognose kumuliert", color: "var(--series-1)", values: days.map(function (r) { return r.cumulative_forecast_eur; }) },
          { name: "Ist kumuliert", color: "var(--series-3)", values: days.map(function (r) { return r.cumulative_actual_eur; }) }
        ]
      });

      renderMonths("months-table", data.months || []);
      renderMonths("years-table", data.years || []);
      $("conclusion").innerHTML = (data.conclusion || []).map(function (line) {
        return "<li>" + line + "</li>";
      }).join("");
    });
  }

  /* ------------------------------------------------------------ Einrichtung */

  function setValue(path, value) {
    var input = $(path);
    if (!input) return;
    if (input.type === "checkbox") input.checked = !!value;
    else input.value = value;
  }

  function collect() {
    var payload = JSON.parse(JSON.stringify(state.settings || {}));
    Array.prototype.forEach.call(document.querySelectorAll("#setup-form input, #setup-form select"),
      function (input) {
        if (!input.id) return;
        var value = input.type === "checkbox" ? input.checked :
          (input.type === "number" ? parseFloat(input.value) : input.value);
        if (input.type === "number" && isNaN(value)) value = 0;
        var parts = input.id.split(".");
        var target = payload;
        for (var i = 0; i < parts.length - 1; i++) {
          if (typeof target[parts[i]] !== "object" || target[parts[i]] === null) target[parts[i]] = {};
          target = target[parts[i]];
        }
        target[parts[parts.length - 1]] = value;
      });
    return payload;
  }

  function entityFallbackRows() {
    // Ohne Verbindung zu Home Assistant lassen sich die IDs wenigstens eintippen.
    var selected = (state.settings || {}).entities || {};
    var labels = {
      grid_import: "Netzbezug (Zähler) *", grid_export: "Netzeinspeisung",
      house_consumption: "Hausverbrauch", pv_production: "PV-Erzeugung",
      pv_forecast_tomorrow: "PV-Prognose Folgetag *", pv_forecast_today: "PV-Prognose heute",
      battery_soc: "Speicher-Ladezustand *", battery_charge_energy: "Speicher-Ladung",
      battery_discharge_energy: "Speicher-Entladung", wallbox_energy: "Wallbox",
      battery_charge_limit: "Ladestromgrenze (CCL)",
      battery_discharge_limit: "Entladestromgrenze (DCL)",
      battery_voltage: "Batteriespannung", battery_soc_min: "Mindest-Ladezustand",
      ac_input_limit: "Eingangsstrombegrenzung", heatpump_energy: "Wärmepumpe"
    };
    $("entity-rows").innerHTML = Object.keys(labels).map(function (key) {
      return '<div class="row"><label for="entities.' + key + '">' + labels[key] +
        '</label><input id="entities.' + key + '" type="text" placeholder="sensor.…" value="' +
        (selected[key] || "") + '"></div>';
    }).join("");
  }

  function renderEntityRows() {
    if (!state.settings) return;
    if (!state.entities || state.entities.error) { entityFallbackRows(); return; }
    var selected = state.settings.entities || {};
    var all = state.entities.entities || [];
    var html = (state.entities.roles || []).map(function (role) {
      var current = selected[role.key] || "";
      var seen = {};
      var options = ['<option value="">– nicht gesetzt –</option>'];
      role.candidates.forEach(function (candidate) {
        seen[candidate.entity_id] = true;
        options.push('<option value="' + candidate.entity_id + '"' +
          (candidate.entity_id === current ? " selected" : "") + ">" +
          candidate.entity_id + " – " + candidate.name + " (" + candidate.state + " " + candidate.unit + ")</option>");
      });
      var rest = all.filter(function (item) { return !seen[item.entity_id]; });
      if (rest.length) {
        options.push('<optgroup label="alle weiteren Sensoren">');
        rest.forEach(function (item) {
          options.push('<option value="' + item.entity_id + '"' +
            (item.entity_id === current ? " selected" : "") + ">" + item.entity_id + " – " + item.name + "</option>");
        });
        options.push("</optgroup>");
      }
      if (current && !seen[current] && !all.some(function (i) { return i.entity_id === current; })) {
        options.push('<option value="' + current + '" selected>' + current + " (gespeichert)</option>");
      }
      return '<div class="row"><label for="entities.' + role.key + '">' + role.label +
        (role.required ? " *" : "") + '</label><select id="entities.' + role.key + '">' +
        options.join("") + "</select></div>" +
        '<div class="row"><span class="hint">' + role.hint + "</span></div>";
    }).join("");
    $("entity-rows").innerHTML = html;
    if (state.entities.error) {
      $("discovery-note").textContent = "Entitäten konnten nicht gelesen werden: " + state.entities.error;
    }
  }

  function renderMonthlyRows() {
    var names = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
      "September", "Oktober", "November", "Dezember"];
    var reference = (state.settings.forecast || {}).monthly_reference_kwh || {};
    $("monthly-rows").innerHTML = names.map(function (name, index) {
      var key = String(index + 1);
      return '<div class="row"><label for="forecast.monthly_reference_kwh.' + key + '">' + name +
        '</label><input id="forecast.monthly_reference_kwh.' + key + '" type="number" step="1" value="' +
        (reference[key] !== undefined ? reference[key] : 0) + '"></div>';
    }).join("");
  }

  function renderSettings() {
    var settings = state.settings;
    ["battery", "ev", "tariff", "forecast", "heatpump", "scenarios"].forEach(function (group) {
      Object.keys(settings[group] || {}).forEach(function (key) {
        setValue(group + "." + key, settings[group][key]);
      });
    });
    setValue("publish_sensors", settings.publish_sensors);
    renderMonthlyRows();
    renderEntityRows();
  }

  function loadEntities() {
    $("discovery-note").textContent = "Entitäten werden gelesen …";
    return api("api/entities").then(function (data) {
      state.entities = data;
      $("discovery-note").textContent = data.error
        ? "Entitäten konnten nicht gelesen werden: " + data.error
        : "Vorschläge stammen aus der automatischen Erkennung. Pflichtangaben sind mit * markiert.";
      renderEntityRows();
    });
  }

  /* -------------------------------------------------------------- Protokoll */

  function loadRuns() {
    return api("api/runs").then(function (data) {
      var rows = data.runs || [];
      if (!rows.length) { $("runs-table").innerHTML = "<tbody><tr><td>Noch keine Läufe</td></tr></tbody>"; return; }
      $("runs-table").innerHTML =
        "<thead><tr><th>Start</th><th>Tag</th><th>Status</th><th>Meldung</th></tr></thead><tbody>" +
        rows.map(function (row) {
          return "<tr><td>" + new Date(row.started_at).toLocaleString("de-DE") + "</td><td>" +
            (row.day || "–") + "</td><td>" + row.status + "</td><td>" + (row.message || "") + "</td></tr>";
        }).join("") + "</tbody>";
    });
  }

  /* ------------------------------------------------------------------ Start */

  function loadStatus() {
    return api("api/status").then(function (data) {
      var scheduler = data.scheduler || {};
      C.setTimezone(data.timezone);
      $("header-sub").textContent = (scheduler.next_run_at
        ? "Lauf täglich um " + scheduler.next_run_at
        : "Zeitplan nicht aktiv") + " · " + data.timezone +
        (scheduler.last_success ? " · zuletzt erfolgreich " + scheduler.last_success
          : (scheduler.next_run_at ? " · noch kein Lauf" : ""));
      var badge = $("ha-badge");
      badge.className = "badge " + (data.home_assistant ? "good" : "bad");
      badge.lastElementChild.textContent = data.home_assistant
        ? "Home Assistant verbunden" : "Home Assistant nicht erreichbar";
      if (!data.configured) {
        $("run-status").textContent = "Noch nicht eingerichtet – bitte unter Einrichtung die Entitäten wählen.";
      }
    });
  }

  function init() {
    initTabs();
    $("run-now").addEventListener("click", function () {
      var button = $("run-now");
      button.disabled = true;
      $("run-status").textContent = "Bewertung läuft …";
      post("api/run", { day: $("day-picker").value || null }).then(function (result) {
        $("run-status").textContent = result.status === "ok"
          ? "Fertig." : "Nicht möglich: " + (result.message || result.status);
        return loadForecast(result.day);
      }).catch(function (error) {
        $("run-status").textContent = "Fehler: " + error.message;
      }).then(function () { button.disabled = false; });
    });

    $("day-picker").addEventListener("change", function () {
      loadForecast($("day-picker").value);
    });

    $("reload-entities").addEventListener("click", loadEntities);

    $("estimate-efficiency").addEventListener("click", function () {
      $("efficiency-status").textContent = "Speicherdaten werden ausgewertet …";
      api("api/efficiency?days=30").then(function (data) {
        setValue("battery.roundtrip_efficiency", data.ac_roundtrip.toFixed(2));
        var herkunft = data.measured
          ? "gemessen über " + data.days + " Tage: " + fmt(data.charged_kwh, 0) + " kWh geladen, " +
            fmt(data.discharged_kwh, 0) + " kWh entladen → Speicher " +
            fmt(data.dc_roundtrip * 100, 1) + " %"
          : "keine Messung möglich, Datenblattwert";
        var wandler = data.measurement_side === "ac" ? "" :
          " · mit Ladegerät " + fmt(data.charger_efficiency * 100, 0) + " % und Wechselrichter " +
          fmt(data.inverter_efficiency * 100, 0) + " %";
        $("efficiency-status").textContent =
          "Vorschlag " + data.ac_roundtrip.toFixed(2) + " (" + herkunft + wandler + ")" +
          (data.notes.length ? " – " + data.notes.join("; ") : "") +
          ". Noch nicht gespeichert.";
      }).catch(function (error) {
        $("efficiency-status").textContent = "Fehler: " + error.message;
      });
    });

    $("setup-form").addEventListener("submit", function (event) {
      event.preventDefault();
      $("setup-status").textContent = "Speichern …";
      post("api/settings", collect()).then(function (data) {
        state.settings = data.settings;
        $("setup-status").textContent = "Gespeichert.";
        return loadStatus();
      }).catch(function (error) {
        $("setup-status").textContent = "Fehler: " + error.message;
      });
    });

    api("api/settings").then(function (settings) {
      state.settings = settings;
      renderSettings();
      return loadStatus();
    }).then(function () {
      return loadForecast(null);
    }).catch(function (error) {
      $("run-status").textContent = "Fehler beim Laden: " + error.message;
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
