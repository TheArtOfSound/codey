/**
 * Codey Structural Health Dashboard — Client
 * Real-time structural health monitoring via WebSocket + REST polling.
 */

(function () {
    "use strict";

    // ── State ────────────────────────────────────────────────────────
    let ws = null;
    let reconnectTimer = null;
    let previousES = null;
    let historyHours = 24;
    let stressData = [];
    let stressSortKey = "stress";
    let stressSortAsc = false;

    // ── DOM refs ─────────────────────────────────────────────────────
    const $ = (sel) => document.querySelector(sel);
    const connDot = $("#conn-dot");
    const connLabel = $("#conn-label");
    const phaseBadge = $("#phase-badge");
    const valKappa = $("#val-kappa");
    const valSigma = $("#val-sigma");
    const valESNum = $("#val-es-num");
    const esTrend = $("#es-trend");
    const barKappa = $("#bar-kappa");
    const barSigma = $("#bar-sigma");
    const barES = $("#bar-es");
    const valNodes = $("#val-nodes");
    const valEdges = $("#val-edges");
    const stressTbody = $("#stress-tbody");
    const changesTbody = $("#changes-tbody");
    const esCanvas = $("#es-chart");
    const chartTooltip = $("#chart-tooltip");

    // ── Formatting helpers ───────────────────────────────────────────

    function fmt(n, d = 3) {
        if (n == null || isNaN(n)) return "—";
        return Number(n).toFixed(d);
    }

    function stressColor(v) {
        if (v >= 0.7) return "stress-high";
        if (v >= 0.4) return "stress-med";
        return "stress-low";
    }

    function phaseClass(phase) {
        const p = (phase || "").toUpperCase();
        if (p === "RIDGE" || p === "STABLE" || p === "HEALTHY") return "ridge";
        if (p === "CAUTION" || p === "WARNING" || p === "WATCH") return "caution";
        if (p === "CRITICAL" || p === "DANGER" || p === "AT RISK") return "critical";
        return "unknown";
    }

    function impactClass(v) {
        if (v > 0.01) return "impact-positive";
        if (v < -0.01) return "impact-negative";
        return "impact-neutral";
    }

    function timeAgo(ts) {
        if (!ts) return "—";
        const d = new Date(ts);
        const now = Date.now();
        const diff = Math.floor((now - d.getTime()) / 1000);
        if (diff < 60) return diff + "s ago";
        if (diff < 3600) return Math.floor(diff / 60) + "m ago";
        if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
        return Math.floor(diff / 86400) + "d ago";
    }

    function shortTime(ts) {
        if (!ts) return "";
        const d = new Date(ts);
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    // ── Gauge bar helpers ────────────────────────────────────────────

    function setBar(bar, value, max = 1) {
        const pct = Math.min(100, Math.max(0, (value / max) * 100));
        bar.style.width = pct + "%";
        bar.className = "gauge-bar";
        if (value / max > 0.7) bar.classList.add("danger");
        else if (value / max > 0.4) bar.classList.add("warn");
    }

    // ── Status panel ─────────────────────────────────────────────────

    function updateStatus(data) {
        const phase = (data.phase || "UNKNOWN").toUpperCase();
        phaseBadge.textContent = phase;
        phaseBadge.className = "phase-badge " + phaseClass(phase);

        valKappa.textContent = fmt(data.kappa, 4);
        valSigma.textContent = fmt(data.sigma, 4);
        valESNum.textContent = fmt(data.es_score, 4);

        setBar(barKappa, data.kappa || 0);
        setBar(barSigma, data.sigma || 0);

        // ES bar is inverted — higher is better
        const esPct = Math.min(100, Math.max(0, (data.es_score || 0) * 100));
        barES.style.width = esPct + "%";
        barES.className = "gauge-bar es-bar";

        // Trend arrow
        if (previousES !== null) {
            const delta = (data.es_score || 0) - previousES;
            if (delta > 0.001) {
                esTrend.textContent = "▲";
                esTrend.className = "trend-arrow up";
            } else if (delta < -0.001) {
                esTrend.textContent = "▼";
                esTrend.className = "trend-arrow down";
            } else {
                esTrend.textContent = "—";
                esTrend.className = "trend-arrow flat";
            }
        }
        previousES = data.es_score || 0;

        valNodes.textContent = (data.node_count || 0).toLocaleString();
        valEdges.textContent = (data.edge_count || 0).toLocaleString();
    }

    // ── Stress table ─────────────────────────────────────────────────

    function renderStressTable() {
        if (!stressData.length) {
            stressTbody.innerHTML = '<tr><td colspan="5" class="empty-msg">Awaiting sweep data…</td></tr>';
            return;
        }
        const sorted = [...stressData].sort((a, b) => {
            const va = a[stressSortKey] ?? 0;
            const vb = b[stressSortKey] ?? 0;
            if (typeof va === "string") return stressSortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
            return stressSortAsc ? va - vb : vb - va;
        });
        stressTbody.innerHTML = sorted
            .map((c) => {
                const sClass = stressColor(c.stress);
                const pct = Math.min(100, (c.stress || 0) * 100);
                return `<tr>
                    <td><span class="comp-name" title="${esc(c.name || c.id)}">${esc(c.name || c.id)}</span></td>
                    <td>
                        <div class="stress-cell">
                            <span>${fmt(c.stress, 4)}</span>
                            <div class="stress-bar-mini"><div class="stress-bar-fill ${sClass}" style="width:${pct}%"></div></div>
                        </div>
                    </td>
                    <td>${fmt(c.coupling, 4)}</td>
                    <td>${fmt(c.cohesion, 4)}</td>
                    <td>${c.cascade_depth ?? "—"}</td>
                </tr>`;
            })
            .join("");
    }

    function updateStress(data) {
        stressData = data.components || [];
        renderStressTable();
    }

    function esc(s) {
        const el = document.createElement("span");
        el.textContent = s;
        return el.innerHTML;
    }

    // ── Sorting ──────────────────────────────────────────────────────

    document.querySelectorAll(".sortable").forEach((th) => {
        th.addEventListener("click", () => {
            const key = th.dataset.key;
            if (stressSortKey === key) {
                stressSortAsc = !stressSortAsc;
            } else {
                stressSortKey = key;
                stressSortAsc = false;
            }
            renderStressTable();
        });
    });

    // ── Changes table ────────────────────────────────────────────────

    function updateChanges(data) {
        const changes = data.changes || [];
        if (!changes.length) {
            changesTbody.innerHTML = '<tr><td colspan="4" class="empty-msg">No changes recorded</td></tr>';
            return;
        }
        changesTbody.innerHTML = changes
            .map((c) => {
                const ic = impactClass(c.impact);
                return `<tr>
                    <td title="${esc(c.timestamp)}">${timeAgo(c.timestamp)}</td>
                    <td>${esc(c.action)}</td>
                    <td><span class="comp-name" title="${esc(c.component)}">${esc(c.component)}</span></td>
                    <td><span class="impact-badge ${ic}">${c.impact >= 0 ? "+" : ""}${fmt(c.impact, 4)}</span></td>
                </tr>`;
            })
            .join("");
    }

    // ── ES History chart (canvas) ────────────────────────────────────

    let historyData = [];

    function drawChart() {
        const ctx = esCanvas.getContext("2d");
        const dpr = window.devicePixelRatio || 1;
        const rect = esCanvas.getBoundingClientRect();
        esCanvas.width = rect.width * dpr;
        esCanvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);

        const W = rect.width;
        const H = rect.height;
        const pad = { top: 20, right: 16, bottom: 30, left: 50 };
        const plotW = W - pad.left - pad.right;
        const plotH = H - pad.top - pad.bottom;

        ctx.clearRect(0, 0, W, H);

        if (!historyData.length) {
            ctx.fillStyle = "#555570";
            ctx.font = "13px sans-serif";
            ctx.textAlign = "center";
            ctx.fillText("No history data", W / 2, H / 2);
            return;
        }

        // Compute bounds
        const times = historyData.map((d) => new Date(d.timestamp).getTime());
        const vals = historyData.map((d) => d.es_score);
        const tMin = Math.min(...times);
        const tMax = Math.max(...times);
        const vMin = Math.min(...vals) * 0.95;
        const vMax = Math.max(...vals) * 1.05 || 1;

        const toX = (t) => pad.left + ((t - tMin) / (tMax - tMin || 1)) * plotW;
        const toY = (v) => pad.top + (1 - (v - vMin) / (vMax - vMin || 1)) * plotH;

        // Grid lines
        ctx.strokeStyle = "rgba(255,255,255,0.04)";
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const y = pad.top + (plotH / 4) * i;
            ctx.beginPath();
            ctx.moveTo(pad.left, y);
            ctx.lineTo(pad.left + plotW, y);
            ctx.stroke();
        }

        // Y-axis labels
        ctx.fillStyle = "#555570";
        ctx.font = "11px monospace";
        ctx.textAlign = "right";
        for (let i = 0; i <= 4; i++) {
            const v = vMax - ((vMax - vMin) / 4) * i;
            const y = pad.top + (plotH / 4) * i;
            ctx.fillText(v.toFixed(3), pad.left - 8, y + 4);
        }

        // X-axis labels
        ctx.textAlign = "center";
        const labelCount = Math.min(6, historyData.length);
        for (let i = 0; i < labelCount; i++) {
            const idx = Math.floor((i / (labelCount - 1 || 1)) * (historyData.length - 1));
            const t = times[idx];
            const x = toX(t);
            ctx.fillText(shortTime(historyData[idx].timestamp), x, H - 6);
        }

        // Gradient fill
        const gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
        gradient.addColorStop(0, "rgba(0,255,136,0.15)");
        gradient.addColorStop(1, "rgba(0,255,136,0.0)");

        ctx.beginPath();
        ctx.moveTo(toX(times[0]), pad.top + plotH);
        for (let i = 0; i < historyData.length; i++) {
            ctx.lineTo(toX(times[i]), toY(vals[i]));
        }
        ctx.lineTo(toX(times[times.length - 1]), pad.top + plotH);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();

        // Line
        ctx.beginPath();
        ctx.strokeStyle = "#00ff88";
        ctx.lineWidth = 2;
        ctx.lineJoin = "round";
        for (let i = 0; i < historyData.length; i++) {
            const x = toX(times[i]);
            const y = toY(vals[i]);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();

        // Dots
        ctx.fillStyle = "#00ff88";
        for (let i = 0; i < historyData.length; i++) {
            const x = toX(times[i]);
            const y = toY(vals[i]);
            ctx.beginPath();
            ctx.arc(x, y, 2.5, 0, Math.PI * 2);
            ctx.fill();
        }

        // Tooltip on hover
        esCanvas.onmousemove = (e) => {
            const cRect = esCanvas.getBoundingClientRect();
            const mx = e.clientX - cRect.left;
            const my = e.clientY - cRect.top;
            let closest = null;
            let closestDist = Infinity;
            for (let i = 0; i < historyData.length; i++) {
                const x = toX(times[i]);
                const y = toY(vals[i]);
                const dist = Math.sqrt((mx - x) ** 2 + (my - y) ** 2);
                if (dist < closestDist) {
                    closestDist = dist;
                    closest = i;
                }
            }
            if (closest !== null && closestDist < 30) {
                const d = historyData[closest];
                chartTooltip.style.display = "block";
                chartTooltip.style.left = (toX(times[closest]) + 10) + "px";
                chartTooltip.style.top = (toY(vals[closest]) - 10) + "px";
                chartTooltip.textContent = `ES: ${fmt(d.es_score, 4)} @ ${shortTime(d.timestamp)}`;
            } else {
                chartTooltip.style.display = "none";
            }
        };

        esCanvas.onmouseleave = () => {
            chartTooltip.style.display = "none";
        };
    }

    function updateHistory(data) {
        historyData = data.history || [];
        drawChart();
    }

    // Resize handler
    window.addEventListener("resize", () => {
        drawChart();
    });

    // Time range buttons
    document.querySelectorAll(".time-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".time-btn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            historyHours = parseInt(btn.dataset.hours, 10);
            fetchHistory();
        });
    });

    // ── Data fetching ────────────────────────────────────────────────

    async function fetchJSON(url) {
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(res.statusText);
            return await res.json();
        } catch (e) {
            console.warn("Fetch failed:", url, e);
            return null;
        }
    }

    async function fetchStatus() {
        const data = await fetchJSON("/api/status");
        if (data) updateStatus(data);
    }

    async function fetchStress() {
        const data = await fetchJSON("/api/stress");
        if (data) updateStress(data);
    }

    async function fetchChanges() {
        const data = await fetchJSON("/api/changes?limit=20");
        if (data) updateChanges(data);
    }

    async function fetchHistory() {
        const data = await fetchJSON("/api/history?hours=" + historyHours);
        if (data) updateHistory(data);
    }

    async function fetchAll() {
        await Promise.all([fetchStatus(), fetchStress(), fetchChanges(), fetchHistory()]);
    }

    // ── WebSocket ────────────────────────────────────────────────────

    function setConnected(connected) {
        connDot.className = "dot " + (connected ? "connected" : "disconnected");
        connLabel.textContent = connected ? "Connected" : "Disconnected";
    }

    function connectWS() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(proto + "//" + location.host + "/ws");

        ws.onopen = () => {
            setConnected(true);
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            fetchAll();
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === "sweep_complete" || msg.type === "update") {
                    if (msg.status) updateStatus(msg.status);
                    if (msg.stress) updateStress(msg.stress);
                    if (msg.changes) updateChanges(msg.changes);
                    if (msg.history) updateHistory(msg.history);
                    // If partial update, fetch what's missing
                    if (!msg.status) fetchStatus();
                    if (!msg.stress) fetchStress();
                }
            } catch (e) {
                console.warn("WS message parse error:", e);
            }
        };

        ws.onclose = () => {
            setConnected(false);
            scheduleReconnect();
        };

        ws.onerror = () => {
            setConnected(false);
            ws.close();
        };
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connectWS();
        }, 3000);
    }

    // ── Init ─────────────────────────────────────────────────────────

    connectWS();

    // Fallback: poll every 30s in case WS is flaky
    setInterval(() => {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            fetchAll();
        }
    }, 30000);
})();
