(() => {
    "use strict";

    let calendarData = null;
    let calendarRefreshTimer = null;
    let minerRowsObserver = null;

    function esc(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function localDateTime(value) {
        if (!value) {
            return "-";
        }

        const date = new Date(value);

        if (Number.isNaN(date.getTime())) {
            return esc(value);
        }

        return date.toLocaleString();
    }

    function sourceHTML(item) {
        if (item.source === "RECURRING") {
            const rule = item.rule_id
                ? ` #${Number(item.rule_id)}`
                : "";

            return `
                <span class="operations-calendar-source operations-calendar-source-recurring">
                    RECURRING${rule}
                </span>
            `;
        }

        return `
            <span class="operations-calendar-source operations-calendar-source-one-off">
                ONE-OFF
            </span>
        `;
    }

    function membershipHTML(item) {
        if (item.scope !== "GROUP") {
            return "";
        }

        const count = Number(item.member_count || 0);
        const mode = item.membership_mode === "SNAPSHOT"
            ? "snapshot"
            : "current preview";

        return `
            <div class="operations-calendar-membership">
                ${esc(mode)} · ${count} ASIC(s)
            </div>
        `;
    }

    function rowHTML(item) {
        const status = String(item.status || "UNKNOWN");
        const statusClass = `operations-calendar-status-${status.toLowerCase()}`;

        return `
            <tr>
                <td>${sourceHTML(item)}</td>
                <td>
                    ${esc(item.target || item.scope || "-")}
                    ${membershipHTML(item)}
                </td>
                <td>${localDateTime(item.starts_at_iso)}</td>
                <td>${localDateTime(item.ends_at_iso)}</td>
                <td class="${statusClass}">${esc(status)}</td>
                <td class="operations-calendar-note">${esc(item.note || "-")}</td>
            </tr>
        `;
    }

    function rowsHTML(items) {
        if (!Array.isArray(items) || !items.length) {
            return `
                <tr>
                    <td colspan="6" class="operations-calendar-empty">
                        No maintenance entries in this view.
                    </td>
                </tr>
            `;
        }

        return items.map(rowHTML).join("");
    }

    function renderCalendar(data) {
        calendarData = data;

        const active = Array.isArray(data.active)
            ? data.active
            : [];
        const upcoming = Array.isArray(data.upcoming)
            ? data.upcoming
            : [];
        const recent = Array.isArray(data.recent)
            ? data.recent
            : [];

        document.getElementById(
            "operationsCalendarActiveCount"
        ).textContent = active.length;

        document.getElementById(
            "operationsCalendarUpcomingCount"
        ).textContent = upcoming.length;

        document.getElementById(
            "operationsCalendarRecentCount"
        ).textContent = recent.length;

        document.getElementById(
            "operationsCalendarActiveRows"
        ).innerHTML = rowsHTML(active);

        document.getElementById(
            "operationsCalendarUpcomingRows"
        ).innerHTML = rowsHTML(upcoming);

        document.getElementById(
            "operationsCalendarRecentRows"
        ).innerHTML = rowsHTML(recent);

        const button = document.getElementById(
            "operationsCalendarButton"
        );

        if (button) {
            button.textContent = `OPERATIONS ${active.length + upcoming.length}`;
        }

        const status = document.getElementById(
            "operationsCalendarStatus"
        );

        if (status) {
            const timezone = data.timezone || "configured timezone";
            status.textContent =
                `Plan/fact view · ${timezone} · recurring GROUP entries use current membership until materialized.`;
        }

        decorateMinerRows();
    }

    async function loadOperationsCalendar() {
        const scope = document.getElementById(
            "operationsCalendarScope"
        )?.value || "ALL";

        const upcomingDays = document.getElementById(
            "operationsCalendarUpcomingDays"
        )?.value || "14";

        const recentDays = document.getElementById(
            "operationsCalendarRecentDays"
        )?.value || "7";

        const status = document.getElementById(
            "operationsCalendarStatus"
        );

        if (status) {
            status.textContent = "Loading operations calendar...";
        }

        const query = new URLSearchParams({
            scope,
            upcoming_days: upcomingDays,
            recent_days: recentDays,
            limit: "200",
        });

        try {
            const response = await fetch(
                `/api/maintenance/calendar?${query.toString()}`
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(
                    data.detail || `HTTP ${response.status}`
                );
            }

            renderCalendar(data);
        }
        catch (error) {
            if (status) {
                status.textContent =
                    `Operations calendar error: ${error.message}`;
            }
        }
    }

    function upcomingCountForMiner(minerId) {
        if (!calendarData || !Array.isArray(calendarData.upcoming)) {
            return 0;
        }

        let count = 0;

        for (const item of calendarData.upcoming) {
            if (item.affects_all) {
                count += 1;
                continue;
            }

            const ids = Array.isArray(item.affected_miner_ids)
                ? item.affected_miner_ids
                : [];

            if (ids.some(id => Number(id) === Number(minerId))) {
                count += 1;
            }
        }

        return count;
    }

    function decorateMinerRows() {
        const boxes = Array.from(
            document.querySelectorAll(
                ".bulk-miner-checkbox[data-bulk-miner-id]"
            )
        );

        for (const box of boxes) {
            const cell = box.closest("td");

            if (!cell) {
                continue;
            }

            const old = cell.querySelector(
                ".operations-upcoming-mini"
            );

            if (old) {
                old.remove();
            }

            const minerId = Number(
                box.dataset.bulkMinerId
            );
            const count = upcomingCountForMiner(
                minerId
            );

            if (!count) {
                continue;
            }

            const badge = document.createElement("span");
            badge.className = "operations-upcoming-mini";
            badge.textContent = `UPCOMING ${count}`;
            badge.title =
                `${count} upcoming maintenance occurrence(s) affect this ASIC`;

            cell.appendChild(badge);
        }
    }

    function openOperationsCalendar() {
        const backdrop = document.getElementById(
            "operationsCalendarBackdrop"
        );

        if (backdrop) {
            backdrop.classList.add("open");
        }

        loadOperationsCalendar();
    }

    function closeOperationsCalendar() {
        const backdrop = document.getElementById(
            "operationsCalendarBackdrop"
        );

        if (backdrop) {
            backdrop.classList.remove("open");
        }
    }

    function modalHTML() {
        return `
            <div id="operationsCalendarBackdrop" class="modal-backdrop">
                <div class="history-modal operations-calendar-modal">
                    <div class="history-header">
                        <div>
                            <h2>Operations Calendar</h2>
                            <div class="muted small">
                                Planned Maintenance plan/fact view. One-off windows and recurring forecasts remain separate from ASIC control.
                            </div>
                        </div>
                        <button class="close-button" onclick="closeOperationsCalendar()">
                            CLOSE
                        </button>
                    </div>

                    <div class="operations-calendar-controls">
                        <div class="operations-calendar-control">
                            <label for="operationsCalendarScope">Scope</label>
                            <select id="operationsCalendarScope" onchange="loadOperationsCalendar()">
                                <option value="ALL">ALL</option>
                                <option value="FARM">FARM</option>
                                <option value="GROUP">GROUP</option>
                                <option value="MINER">MINER</option>
                            </select>
                        </div>

                        <div class="operations-calendar-control">
                            <label for="operationsCalendarUpcomingDays">Upcoming horizon</label>
                            <select id="operationsCalendarUpcomingDays" onchange="loadOperationsCalendar()">
                                <option value="7">7 days</option>
                                <option value="14" selected>14 days</option>
                                <option value="30">30 days</option>
                                <option value="90">90 days</option>
                            </select>
                        </div>

                        <div class="operations-calendar-control">
                            <label for="operationsCalendarRecentDays">Recent history</label>
                            <select id="operationsCalendarRecentDays" onchange="loadOperationsCalendar()">
                                <option value="7" selected>7 days</option>
                                <option value="30">30 days</option>
                                <option value="90">90 days</option>
                            </select>
                        </div>

                        <button class="secondary" onclick="loadOperationsCalendar()">
                            REFRESH
                        </button>
                    </div>

                    <div id="operationsCalendarStatus" class="muted small">
                        Loading operations calendar...
                    </div>

                    <div class="operations-calendar-summary">
                        <div class="operations-calendar-summary-card">
                            <div class="muted small">ACTIVE NOW</div>
                            <div id="operationsCalendarActiveCount" class="operations-calendar-summary-value">0</div>
                        </div>
                        <div class="operations-calendar-summary-card">
                            <div class="muted small">UPCOMING</div>
                            <div id="operationsCalendarUpcomingCount" class="operations-calendar-summary-value">0</div>
                        </div>
                        <div class="operations-calendar-summary-card">
                            <div class="muted small">RECENT</div>
                            <div id="operationsCalendarRecentCount" class="operations-calendar-summary-value">0</div>
                        </div>
                    </div>

                    ${sectionHTML("Active now", "operationsCalendarActiveRows")}
                    ${sectionHTML("Upcoming", "operationsCalendarUpcomingRows")}
                    ${sectionHTML("Recent", "operationsCalendarRecentRows")}
                </div>
            </div>
        `;
    }

    function sectionHTML(title, bodyId) {
        return `
            <div class="operations-calendar-section">
                <h3>${esc(title)}</h3>
                <div class="table-wrap">
                    <table class="operations-calendar-table">
                        <thead>
                            <tr>
                                <th>Source</th>
                                <th>Target</th>
                                <th>Starts</th>
                                <th>Ends</th>
                                <th>Status</th>
                                <th>Note</th>
                            </tr>
                        </thead>
                        <tbody id="${bodyId}"></tbody>
                    </table>
                </div>
            </div>
        `;
    }

    function installButton() {
        if (document.getElementById("operationsCalendarButton")) {
            return;
        }

        const maintenanceButton = document.getElementById(
            "maintenanceButton"
        );

        if (!maintenanceButton) {
            return;
        }

        const button = document.createElement("button");
        button.id = "operationsCalendarButton";
        button.className = "secondary";
        button.textContent = "OPERATIONS 0";
        button.title = "Operations calendar and upcoming maintenance";
        button.addEventListener(
            "click",
            openOperationsCalendar
        );

        maintenanceButton.insertAdjacentElement(
            "afterend",
            button
        );
    }

    function installModal() {
        if (document.getElementById("operationsCalendarBackdrop")) {
            return;
        }

        document.body.insertAdjacentHTML(
            "beforeend",
            modalHTML()
        );

        const backdrop = document.getElementById(
            "operationsCalendarBackdrop"
        );

        backdrop.addEventListener("click", event => {
            if (event.target === backdrop) {
                closeOperationsCalendar();
            }
        });
    }

    function installMinerObserver() {
        const minerRows = document.getElementById("minerRows");

        if (!minerRows || minerRowsObserver) {
            return;
        }

        minerRowsObserver = new MutationObserver(() => {
            decorateMinerRows();
        });

        minerRowsObserver.observe(
            minerRows,
            {
                childList: true,
                subtree: true,
            }
        );
    }

    function initialize() {
        installButton();
        installModal();
        installMinerObserver();
        loadOperationsCalendar();

        if (!calendarRefreshTimer) {
            calendarRefreshTimer = window.setInterval(
                loadOperationsCalendar,
                60_000
            );
        }
    }

    window.openOperationsCalendar = openOperationsCalendar;
    window.closeOperationsCalendar = closeOperationsCalendar;
    window.loadOperationsCalendar = loadOperationsCalendar;

    if (document.readyState === "loading") {
        document.addEventListener(
            "DOMContentLoaded",
            initialize
        );
    }
    else {
        initialize();
    }
})();
