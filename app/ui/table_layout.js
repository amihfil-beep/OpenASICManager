(() => {
    "use strict";

    const STORAGE_KEY = "openasicmanager.minerTable.columnWidths.v1";
    const MIN_WIDTH = 64;
    const SELECT_WIDTH = 46;

    function loadSavedWidths(columnCount) {
        try {
            const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
            if (!Array.isArray(value) || value.length !== columnCount) {
                return null;
            }
            if (!value.every((width) => Number.isFinite(width) && width >= 1)) {
                return null;
            }
            return value;
        } catch (_) {
            return null;
        }
    }

    function saveWidths(widths) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(widths));
        } catch (_) {
            // The table remains usable when browser storage is unavailable.
        }
    }

    function clearSavedWidths() {
        try {
            localStorage.removeItem(STORAGE_KEY);
        } catch (_) {
            // Ignore storage failures; reset still applies to this page.
        }
    }

    function tableWidth(widths) {
        return Math.ceil(widths.reduce((sum, width) => sum + width, 0));
    }

    function initializeMinerTableLayout() {
        const table = document.querySelector("table.miner-table");
        if (!table || table.dataset.columnResizeReady === "1") {
            return;
        }

        const wrap = table.closest(".table-wrap");
        const headers = Array.from(table.querySelectorAll("thead th"));
        if (!wrap || headers.length === 0) {
            return;
        }

        table.dataset.columnResizeReady = "1";

        const naturalWidths = headers.map((header, index) => {
            const minimum = index === 0 ? SELECT_WIDTH : MIN_WIDTH;
            return Math.max(minimum, Math.ceil(header.getBoundingClientRect().width));
        });
        let widths = loadSavedWidths(headers.length) || naturalWidths.slice();

        const colgroup = document.createElement("colgroup");
        const columns = widths.map((width) => {
            const col = document.createElement("col");
            col.style.width = `${width}px`;
            colgroup.appendChild(col);
            return col;
        });
        table.insertBefore(colgroup, table.firstChild);
        table.classList.add("asic-table-resizable");

        const toolbar = document.createElement("div");
        toolbar.className = "asic-table-layout-toolbar";

        const resetButton = document.createElement("button");
        resetButton.type = "button";
        resetButton.className = "secondary mini";
        resetButton.textContent = "RESET COLUMNS";
        resetButton.title = "Restore default ASIC table column widths";
        toolbar.appendChild(resetButton);

        const topScroll = document.createElement("div");
        topScroll.className = "asic-table-top-scroll";
        topScroll.setAttribute("aria-label", "ASIC table horizontal scroll");
        const topScrollInner = document.createElement("div");
        topScrollInner.className = "asic-table-top-scroll-inner";
        topScroll.appendChild(topScrollInner);

        wrap.parentNode.insertBefore(toolbar, wrap);
        wrap.parentNode.insertBefore(topScroll, wrap);

        function applyWidths(nextWidths, persist) {
            widths = nextWidths.map((width, index) => {
                const minimum = index === 0 ? SELECT_WIDTH : MIN_WIDTH;
                return Math.max(minimum, Math.round(width));
            });

            columns.forEach((col, index) => {
                col.style.width = `${widths[index]}px`;
            });

            const width = tableWidth(widths);
            table.style.width = `${width}px`;
            table.style.minWidth = `${width}px`;
            topScrollInner.style.width = `${Math.max(width, wrap.clientWidth)}px`;

            if (persist) {
                saveWidths(widths);
            }
        }

        let syncing = false;
        function syncScroll(source, target) {
            if (syncing) {
                return;
            }
            syncing = true;
            target.scrollLeft = source.scrollLeft;
            requestAnimationFrame(() => {
                syncing = false;
            });
        }

        topScroll.addEventListener("scroll", () => syncScroll(topScroll, wrap));
        wrap.addEventListener("scroll", () => syncScroll(wrap, topScroll));

        headers.forEach((header, index) => {
            const resizer = document.createElement("span");
            resizer.className = "asic-column-resizer";
            resizer.setAttribute("role", "separator");
            resizer.setAttribute("aria-orientation", "vertical");
            resizer.title = "Drag to resize column";

            resizer.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
            });

            resizer.addEventListener("pointerdown", (event) => {
                if (event.button !== 0) {
                    return;
                }
                event.preventDefault();
                event.stopPropagation();

                const startX = event.clientX;
                const startWidth = widths[index];
                const minimum = index === 0 ? SELECT_WIDTH : MIN_WIDTH;
                resizer.classList.add("active");
                document.body.classList.add("asic-column-resizing");
                resizer.setPointerCapture(event.pointerId);

                const onMove = (moveEvent) => {
                    const next = widths.slice();
                    next[index] = Math.max(
                        minimum,
                        startWidth + moveEvent.clientX - startX,
                    );
                    applyWidths(next, false);
                };

                const onEnd = () => {
                    resizer.classList.remove("active");
                    document.body.classList.remove("asic-column-resizing");
                    resizer.removeEventListener("pointermove", onMove);
                    resizer.removeEventListener("pointerup", onEnd);
                    resizer.removeEventListener("pointercancel", onEnd);
                    saveWidths(widths);
                };

                resizer.addEventListener("pointermove", onMove);
                resizer.addEventListener("pointerup", onEnd);
                resizer.addEventListener("pointercancel", onEnd);
            });

            header.appendChild(resizer);
        });

        resetButton.addEventListener("click", () => {
            clearSavedWidths();
            applyWidths(naturalWidths.slice(), false);
        });

        window.addEventListener("resize", () => {
            topScrollInner.style.width = `${Math.max(table.scrollWidth, wrap.clientWidth)}px`;
        });

        applyWidths(widths, false);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializeMinerTableLayout);
    } else {
        initializeMinerTableLayout();
    }
})();
