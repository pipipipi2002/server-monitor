// Time formatting
function relativeLocal(iso) {
    if (!iso) return "";
    const dt = new Date(iso);
    const diff = Math.round((Date.now() - dt.getTime()) / 1000);
    if (diff < 60) return diff + "s ago";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    return Math.floor(diff / 3600) + "h ago";
}
function applyRelative(scope) {
    (scope || document).querySelectorAll("[data-iso]").forEach(el => {
        el.textContent = relativeLocal(el.getAttribute("data-iso"));
    });
}

// Refresh strategy: when an SSE event arrives that affects what we're showing,
// HTMX swaps the relevant fragment. For now we do a coarse-grained refresh of
// the whole dashboard grid since at <=20 servers it's negligible.
function refreshGrid() {
    const grid = document.getElementById("server-grid");
    if (!grid) return;
    fetch("/?fragment=grid", { headers: { "HX-Request": "true" } })
        .then(r => r.text())
        .then(html => {
            grid.outerHTML = html;
            applyRelative();
        })
        .catch(() => { /* silent */ });
}

// React to SSE events
window.addEventListener("sm:event", function (e) {
    const evt = e.detail || {};
    if (evt.type === "report" || evt.type === "alias.updated"
        || evt.type === "server.online" || evt.type === "server.offline") {
        if (document.getElementById("server-grid")) refreshGrid();
    }
});

document.addEventListener("DOMContentLoaded", () => applyRelative());
document.addEventListener("htmx:afterSwap", e => applyRelative(e.target));
setInterval(() => applyRelative(), 30 * 1000);
