// 個人ページ: 日別歩数のグラフ（目標の線つき）と表。データは /api/users/{id}/daily から読む。
(function () {
  var root = document.getElementById("daily");
  if (!root) return;

  var userId = root.dataset.userId;
  var goal = Number(root.dataset.goal);
  var overloadPercent = Number(root.dataset.overload);
  var today = root.dataset.today; // サーバーの日付（日本時間）を使う。端末の時計に頼らない。
  var status = document.getElementById("daily-status");
  var tbody = document.querySelector("#daily-table tbody");
  var buttons = document.querySelectorAll(".segmented button");
  var WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];
  var chart = null;

  var css = getComputedStyle(document.documentElement);
  function color(name) { return css.getPropertyValue(name).trim(); }
  function fmt(n) { return Number(n).toLocaleString("ja-JP"); }

  function shiftDate(isoDate, days) {
    var d = new Date(isoDate + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
  }

  function label(isoDate) {
    var d = new Date(isoDate + "T00:00:00Z");
    return (d.getUTCMonth() + 1) + "/" + d.getUTCDate() + "（" + WEEKDAYS[d.getUTCDay()] + "）";
  }

  function drawChart(days) {
    var overloadLine = Math.round(goal * overloadPercent / 100);
    var labels = days.map(function (d) { return label(d.date); });
    var steps = days.map(function (d) { return d.steps; });
    var barColors = days.map(function (d) {
      if (d.steps === null) return "transparent";
      if (d.steps >= overloadLine) return color("--danger");
      return d.steps >= goal ? color("--ok") : color("--bar");
    });
    if (chart) chart.destroy();
    Chart.defaults.font.family = color("--font");
    Chart.defaults.font.size = 14;
    Chart.defaults.color = color("--muted");
    chart = new Chart(document.getElementById("daily-chart"), {
      data: {
        labels: labels,
        datasets: [
          { type: "bar", label: "歩数", data: steps, backgroundColor: barColors, borderRadius: 4, order: 2 },
          { type: "line", label: "目標 " + fmt(goal) + "歩", data: days.map(function () { return goal; }),
            borderColor: color("--goal"), borderWidth: 2, borderDash: [6, 4], pointRadius: 0, order: 1 },
          { type: "line", label: "過負荷の目安（目標の" + overloadPercent + "%）", data: days.map(function () { return overloadLine; }),
            borderColor: color("--danger"), borderWidth: 1.5, borderDash: [2, 4], pointRadius: 0, order: 1 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        scales: {
          y: { beginAtZero: true, ticks: { callback: function (v) { return fmt(v); } }, grid: { color: color("--line") } },
          x: { grid: { display: false } }
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                if (ctx.dataset.type === "bar") return ctx.raw === null ? "記録なし" : "歩数 " + fmt(ctx.raw) + "歩";
                return ctx.dataset.label;
              }
            }
          }
        }
      }
    });
  }

  function drawTable(days) {
    tbody.innerHTML = "";
    days.slice().reverse().forEach(function (d) {
      var tr = document.createElement("tr");
      var rate = d.steps === null ? null : Math.round(d.steps * 100 / goal);
      var cells = [
        label(d.date),
        d.steps === null ? "記録なし" : fmt(d.steps) + "歩",
        rate === null ? "—" : rate + "%",
        d.steps === null ? "—" : (d.steps >= goal ? "達成" : "—")
      ];
      cells.forEach(function (text, i) {
        var td = document.createElement("td");
        td.textContent = text;
        if (i === 1 || i === 2) td.className = "num";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function load(days) {
    var from = shiftDate(today, -(days - 1));
    status.textContent = "読み込み中…";
    buttons.forEach(function (b) { b.setAttribute("aria-pressed", String(Number(b.dataset.days) === days)); });
    fetch("/api/users/" + userId + "/daily?from=" + from + "&to=" + today, { credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        drawChart(data.days);
        drawTable(data.days);
        var recorded = data.days.filter(function (d) { return d.steps !== null; }).length;
        var achieved = data.days.filter(function (d) { return d.steps !== null && d.steps >= goal; }).length;
        status.textContent = days + "日間のうち、記録のある日 " + recorded + "日・目標達成 " + achieved + "日";
      })
      .catch(function (err) {
        status.textContent = "データを読み込めませんでした（" + err.message + "）。ページを開き直してください。";
      });
  }

  buttons.forEach(function (b) {
    b.addEventListener("click", function () { load(Number(b.dataset.days)); });
  });
  load(14);
})();
