/* Kleine SVG-Diagrammsammlung: eine Achse je Diagramm, dünne Marken,
   2 px Abstand zwischen gestapelten Flächen, Hover mit Tooltip. */
(function (global) {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var tooltip = null;
  // Anzeigezeitzone der Anlage; der Browser kann in einer anderen Zone stehen.
  var timeZone = "Europe/Berlin";
  var timeFormat = null;

  function setTimezone(name) {
    timeZone = name || timeZone;
    timeFormat = null;
  }

  function localParts(date) {
    if (!timeFormat) {
      timeFormat = new Intl.DateTimeFormat("de-DE", {
        timeZone: timeZone, hour: "2-digit", minute: "2-digit", hour12: false
      });
    }
    var text = timeFormat.format(date);
    var bits = text.split(":");
    return { hour: parseInt(bits[0], 10) % 24, minute: parseInt(bits[1], 10), text: text };
  }

  function el(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    for (var key in attrs) {
      if (attrs[key] !== null && attrs[key] !== undefined) node.setAttribute(key, attrs[key]);
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  function getTooltip() {
    if (!tooltip) tooltip = document.getElementById("tooltip");
    return tooltip;
  }

  function showTip(host, event, html) {
    var tip = getTooltip();
    if (!tip) return;
    tip.innerHTML = html;
    tip.style.opacity = "1";
    var box = host.getBoundingClientRect();
    var left = event.clientX - box.left + host.scrollLeft + 12;
    var top = event.clientY - box.top + 12;
    if (left + tip.offsetWidth > host.clientWidth + host.scrollLeft) {
      left = event.clientX - box.left + host.scrollLeft - tip.offsetWidth - 12;
    }
    tip.style.left = Math.max(0, left) + "px";
    tip.style.top = Math.max(0, top) + "px";
  }

  function hideTip() {
    var tip = getTooltip();
    if (tip) tip.style.opacity = "0";
  }

  function niceStep(span, target) {
    var raw = span / Math.max(1, target);
    var mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    var candidates = [1, 2, 2.5, 5, 10];
    for (var i = 0; i < candidates.length; i++) {
      if (candidates[i] * mag >= raw) return candidates[i] * mag;
    }
    return 10 * mag;
  }

  function fmt(value, digits) {
    if (value === null || value === undefined || isNaN(value)) return "–";
    return value.toLocaleString("de-DE", {
      minimumFractionDigits: digits, maximumFractionDigits: digits
    });
  }

  function roundedBarPath(x, y, width, height, radius) {
    var r = Math.max(0, Math.min(radius, width / 2, Math.abs(height)));
    if (height >= 0) {
      return "M" + x + "," + (y + height) +
        "V" + (y + r) + "a" + r + "," + r + " 0 0 1 " + r + "," + -r +
        "H" + (x + width - r) + "a" + r + "," + r + " 0 0 1 " + r + "," + r +
        "V" + (y + height) + "Z";
    }
    var bottom = y - height;
    return "M" + x + "," + y +
      "V" + (bottom - r) + "a" + r + "," + r + " 0 0 0 " + r + "," + r +
      "H" + (x + width - r) + "a" + r + "," + r + " 0 0 0 " + r + "," + -r +
      "V" + y + "Z";
  }

  function frame(host, options) {
    host.innerHTML = "";
    var width = options.width || 900;
    var height = options.height || 260;
    var margin = options.margin || { top: 14, right: 16, bottom: 26, left: 46 };
    var svg = el("svg", {
      "class": "chart", viewBox: "0 0 " + width + " " + height,
      preserveAspectRatio: "xMidYMid meet", role: "img",
      "aria-label": options.label || "Diagramm"
    }, host);
    return {
      svg: svg, width: width, height: height, margin: margin,
      plotWidth: width - margin.left - margin.right,
      plotHeight: height - margin.top - margin.bottom
    };
  }

  function yAxis(ctx, min, max, unit, digits) {
    var step = niceStep(max - min || 1, 4);
    var start = Math.floor(min / step) * step;
    var scale = function (value) {
      return ctx.margin.top + ctx.plotHeight * (1 - (value - min) / ((max - min) || 1));
    };
    for (var value = start; value <= max + 1e-9; value += step) {
      if (value < min - 1e-9) continue;
      var y = scale(value);
      el("line", {
        "class": "grid-line", x1: ctx.margin.left, x2: ctx.margin.left + ctx.plotWidth,
        y1: y, y2: y
      }, ctx.svg);
      var text = el("text", { x: ctx.margin.left - 8, y: y + 4, "text-anchor": "end" }, ctx.svg);
      text.textContent = fmt(value + 0, digits === undefined ? 0 : digits);
    }
    if (unit) {
      var label = el("text", { x: ctx.margin.left - 8, y: ctx.margin.top - 4, "text-anchor": "end" }, ctx.svg);
      label.textContent = unit;
    }
    return scale;
  }

  function timeTicks(ctx, times, everyHours) {
    var step = everyHours || 3;
    for (var i = 0; i < times.length; i++) {
      var parts = localParts(times[i]);
      if (parts.minute !== 0 || parts.hour % step !== 0) continue;
      var x = ctx.margin.left + (i / times.length) * ctx.plotWidth;
      var text = el("text", {
        x: x, y: ctx.height - ctx.margin.bottom + 16, "text-anchor": "middle"
      }, ctx.svg);
      text.textContent = ("0" + parts.hour).slice(-2);
    }
    el("line", {
      "class": "axis-line", x1: ctx.margin.left, x2: ctx.margin.left + ctx.plotWidth,
      y1: ctx.height - ctx.margin.bottom, y2: ctx.height - ctx.margin.bottom
    }, ctx.svg);
  }

  function hoverLayer(ctx, host, count, onIndex) {
    var band = ctx.plotWidth / count;
    var marker = el("line", {
      "class": "axis-line", y1: ctx.margin.top, y2: ctx.margin.top + ctx.plotHeight,
      opacity: 0, "stroke-width": 1
    }, ctx.svg);
    var rect = el("rect", {
      x: ctx.margin.left, y: ctx.margin.top, width: ctx.plotWidth, height: ctx.plotHeight,
      fill: "transparent"
    }, ctx.svg);
    rect.addEventListener("mousemove", function (event) {
      var box = ctx.svg.getBoundingClientRect();
      var ratio = (event.clientX - box.left) / box.width;
      var x = ratio * ctx.width;
      var index = Math.floor((x - ctx.margin.left) / band);
      if (index < 0 || index >= count) { hideTip(); marker.setAttribute("opacity", 0); return; }
      var cx = ctx.margin.left + (index + 0.5) * band;
      marker.setAttribute("x1", cx);
      marker.setAttribute("x2", cx);
      marker.setAttribute("opacity", 0.6);
      showTip(host, event, onIndex(index));
    });
    rect.addEventListener("mouseleave", function () {
      hideTip();
      marker.setAttribute("opacity", 0);
    });
  }

  function extent(arrays) {
    var min = Infinity, max = -Infinity;
    arrays.forEach(function (values) {
      values.forEach(function (value) {
        if (value === null || value === undefined || isNaN(value)) return;
        if (value < min) min = value;
        if (value > max) max = value;
      });
    });
    if (min === Infinity) { min = 0; max = 1; }
    if (min === max) { max = min + 1; }
    return [min, max];
  }

  function hhmm(date) {
    return localParts(date).text;
  }

  /* Preisverlauf als Stufenlinie mit Ladefenster-Band und Referenzlinie. */
  function priceChart(host, options) {
    var ctx = frame(host, { height: 260, label: "All-in-Arbeitspreis je Viertelstunde" });
    var values = options.values;
    var times = options.times;
    var bounds = extent([values, [options.reference, 0]]);
    var min = Math.min(0, bounds[0]);
    var max = bounds[1] * 1.08;
    var y = yAxis(ctx, min, max, "ct/kWh", 0);
    var band = ctx.plotWidth / values.length;

    if (options.bandStart !== null && options.bandStart !== undefined && options.bandEnd > options.bandStart) {
      el("rect", {
        x: ctx.margin.left + options.bandStart * band, y: ctx.margin.top,
        width: (options.bandEnd - options.bandStart) * band, height: ctx.plotHeight,
        fill: "var(--band)", rx: 3
      }, ctx.svg);
      var mid = ctx.margin.left + ((options.bandStart + options.bandEnd) / 2) * band;
      var tag = el("text", {
        x: mid, y: ctx.margin.top + 12, "text-anchor": "middle", "class": "label-strong"
      }, ctx.svg);
      tag.textContent = "Ladefenster";
    }

    if (options.reference) {
      var refY = y(options.reference);
      el("line", {
        x1: ctx.margin.left, x2: ctx.margin.left + ctx.plotWidth, y1: refY, y2: refY,
        stroke: "var(--series-2)", "stroke-width": 2, "stroke-dasharray": "5 4"
      }, ctx.svg);
      var refLabel = el("text", {
        x: ctx.margin.left + ctx.plotWidth, y: refY - 6, "text-anchor": "end", "class": "label-strong"
      }, ctx.svg);
      refLabel.textContent = options.referenceLabel || "";
    }

    var path = "";
    for (var i = 0; i < values.length; i++) {
      var x0 = ctx.margin.left + i * band;
      var x1 = x0 + band;
      var yv = y(values[i]);
      path += (i === 0 ? "M" : "L") + x0 + "," + yv + "L" + x1 + "," + yv;
    }
    el("path", {
      d: path, fill: "none", stroke: "var(--series-1)", "stroke-width": 2,
      "stroke-linejoin": "round"
    }, ctx.svg);

    timeTicks(ctx, times);
    hoverLayer(ctx, host, values.length, function (index) {
      return "<b>" + hhmm(times[index]) + "</b><br>" + fmt(values[index], 2) + " ct/kWh" +
        (options.spot ? "<br>Börse " + fmt(options.spot[index], 1) + " €/MWh" : "");
    });
  }

  /* Gestapelte Balken mit überlagerter Linie. */
  function stackChart(host, options) {
    var ctx = frame(host, { height: 250, label: "Verbrauch je Viertelstunde" });
    var count = options.times.length;
    var totals = [];
    for (var i = 0; i < count; i++) {
      var sum = 0;
      options.stacks.forEach(function (serie) { sum += serie.values[i] || 0; });
      totals.push(sum);
    }
    var max = Math.max(extent([totals])[1], extent([options.line ? options.line.values : [0]])[1]) * 1.1;
    var y = yAxis(ctx, 0, max, "kWh", 2);
    var band = ctx.plotWidth / count;
    var barWidth = Math.max(1, band - 1);

    for (var index = 0; index < count; index++) {
      var base = 0;
      for (var s = 0; s < options.stacks.length; s++) {
        var value = options.stacks[s].values[index] || 0;
        if (value <= 0) continue;
        var top = y(base + value);
        var height = y(base) - top;
        if (height <= 0) continue;
        // 2 px Fuge zwischen den Segmenten, damit sie sich nicht berühren.
        var gap = base > 0 ? 2 : 0;
        el("path", {
          d: roundedBarPath(ctx.margin.left + index * band, top, barWidth,
            Math.max(0.5, height - gap), 4),
          fill: options.stacks[s].color
        }, ctx.svg);
        base += value;
      }
    }

    if (options.line) {
      var path = "";
      for (var j = 0; j < count; j++) {
        var cx = ctx.margin.left + (j + 0.5) * band;
        path += (j === 0 ? "M" : "L") + cx + "," + y(options.line.values[j] || 0);
      }
      el("path", {
        d: path, fill: "none", stroke: options.line.color, "stroke-width": 2,
        "stroke-linejoin": "round"
      }, ctx.svg);
    }

    timeTicks(ctx, options.times);
    hoverLayer(ctx, host, count, function (index) {
      var html = "<b>" + hhmm(options.times[index]) + "</b>";
      options.stacks.forEach(function (serie) {
        html += "<br>" + serie.name + ": " + fmt(serie.values[index] || 0, 2) + " kWh";
      });
      if (options.line) {
        html += "<br>" + options.line.name + ": " + fmt(options.line.values[index] || 0, 2) + " kWh";
      }
      return html;
    });
  }

  /* Linien über der Zeit, z. B. Ladezustand oder kumulierte Ersparnis. */
  function lineChart(host, options) {
    var ctx = frame(host, { height: options.height || 220, label: options.label || "Verlauf" });
    var count = options.labels.length;
    var arrays = options.series.map(function (serie) { return serie.values; });
    var bounds = extent(arrays);
    var min = options.zeroBased === false ? bounds[0] : Math.min(0, bounds[0]);
    var max = bounds[1] * 1.08 || 1;
    var y = yAxis(ctx, min, max, options.unit, options.digits === undefined ? 1 : options.digits);
    var band = ctx.plotWidth / Math.max(1, count);

    if (min < 0) {
      el("line", {
        "class": "axis-line", x1: ctx.margin.left, x2: ctx.margin.left + ctx.plotWidth,
        y1: y(0), y2: y(0)
      }, ctx.svg);
    }

    options.series.forEach(function (serie) {
      var path = "";
      var started = false;
      for (var i = 0; i < count; i++) {
        var value = serie.values[i];
        if (value === null || value === undefined || isNaN(value)) continue;
        var cx = ctx.margin.left + (i + 0.5) * band;
        path += (started ? "L" : "M") + cx + "," + y(value);
        started = true;
      }
      if (path) {
        el("path", {
          d: path, fill: "none", stroke: serie.color, "stroke-width": 2,
          "stroke-linejoin": "round", "stroke-dasharray": serie.dashed ? "5 4" : null
        }, ctx.svg);
      }
    });

    var everyLabel = Math.max(1, Math.ceil(count / 8));
    for (var i = 0; i < count; i++) {
      if (i % everyLabel !== 0) continue;
      var text = el("text", {
        x: ctx.margin.left + (i + 0.5) * band, y: ctx.height - ctx.margin.bottom + 16,
        "text-anchor": "middle"
      }, ctx.svg);
      text.textContent = options.labels[i];
    }
    el("line", {
      "class": "axis-line", x1: ctx.margin.left, x2: ctx.margin.left + ctx.plotWidth,
      y1: ctx.height - ctx.margin.bottom, y2: ctx.height - ctx.margin.bottom
    }, ctx.svg);

    hoverLayer(ctx, host, count, function (index) {
      var html = "<b>" + options.labels[index] + "</b>";
      options.series.forEach(function (serie) {
        var value = serie.values[index];
        html += "<br>" + serie.name + ": " +
          (value === null || value === undefined ? "–" : fmt(value, 2) + " " + (options.unit || ""));
      });
      return html;
    });
  }

  /* Gruppierte Balken, z. B. Ersparnis Prognose gegen Ist. */
  function barChart(host, options) {
    var ctx = frame(host, { height: options.height || 230, label: options.label || "Balken" });
    var count = options.labels.length;
    var arrays = options.series.map(function (serie) { return serie.values; });
    var bounds = extent(arrays);
    var min = Math.min(0, bounds[0] * 1.1);
    var max = Math.max(0.01, bounds[1] * 1.1);
    var y = yAxis(ctx, min, max, options.unit, options.digits === undefined ? 1 : options.digits);
    var band = ctx.plotWidth / Math.max(1, count);
    var seriesCount = options.series.length;
    var barWidth = Math.max(1, (band - 2) / seriesCount - 1);
    var zero = y(0);

    el("line", {
      "class": "axis-line", x1: ctx.margin.left, x2: ctx.margin.left + ctx.plotWidth,
      y1: zero, y2: zero
    }, ctx.svg);

    options.series.forEach(function (serie, sIndex) {
      for (var i = 0; i < count; i++) {
        var value = serie.values[i];
        if (value === null || value === undefined || isNaN(value)) continue;
        var x = ctx.margin.left + i * band + 1 + sIndex * (barWidth + 1);
        var top = value >= 0 ? y(value) : zero;
        var height = value >= 0 ? zero - y(value) : y(value) - zero;
        el("path", {
          d: roundedBarPath(x, value >= 0 ? top : zero, barWidth,
            value >= 0 ? Math.max(0.5, height) : -Math.max(0.5, height), 4),
          fill: serie.color
        }, ctx.svg);
      }
    });

    var everyLabel = Math.max(1, Math.ceil(count / 10));
    for (var i = 0; i < count; i++) {
      if (i % everyLabel !== 0) continue;
      var text = el("text", {
        x: ctx.margin.left + (i + 0.5) * band, y: ctx.height - ctx.margin.bottom + 16,
        "text-anchor": "middle"
      }, ctx.svg);
      text.textContent = options.labels[i];
    }

    hoverLayer(ctx, host, count, function (index) {
      var html = "<b>" + options.labels[index] + "</b>";
      options.series.forEach(function (serie) {
        var value = serie.values[index];
        html += "<br>" + serie.name + ": " +
          (value === null || value === undefined ? "–" : fmt(value, 2) + " " + (options.unit || ""));
      });
      return html;
    });
  }

  function legend(host, entries) {
    host.innerHTML = entries.map(function (entry) {
      return '<span class="key"><i style="background:' + entry.color + '"></i>' + entry.name + "</span>";
    }).join("");
  }

  global.nsCharts = {
    priceChart: priceChart, stackChart: stackChart, lineChart: lineChart,
    barChart: barChart, legend: legend, fmt: fmt, setTimezone: setTimezone,
    hhmm: hhmm
  };
})(window);
