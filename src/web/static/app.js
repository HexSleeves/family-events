/* ══════════════════════════════════════════════════════════════
   app.js — Alpine.js components + HTMX event handling
   Replaces all inline <script> blocks from base.html
   ══════════════════════════════════════════════════════════════ */

document.addEventListener("alpine:init", () => {
  /* ── Mobile Menu ── */
  Alpine.data("mobileMenu", () => ({
    open: false,
    toggle() {
      this.open = !this.open;
    },
    close() {
      this.open = false;
    },
  }));

  /* ── User Dropdown ── */
  Alpine.data("userDropdown", () => ({
    open: false,
    activeIndex: -1,
    toggle() {
      this.open = !this.open;
      if (this.open) this.activeIndex = 0;
    },
    close() {
      this.open = false;
      this.activeIndex = -1;
    },
    get items() {
      return this.$refs.dropdown
        ? Array.from(this.$refs.dropdown.querySelectorAll('[role="menuitem"]'))
        : [];
    },
    focusItem(idx) {
      const items = this.items;
      if (items[idx]) items[idx].focus();
    },
    onKeydown(e) {
      const items = this.items;
      const idx = items.indexOf(document.activeElement);
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          this.focusItem((idx + 1) % items.length);
          break;
        case "ArrowUp":
          e.preventDefault();
          this.focusItem((idx - 1 + items.length) % items.length);
          break;
        case "Escape":
          e.preventDefault();
          this.close();
          this.$refs.trigger?.focus();
          break;
        case "Home":
          e.preventDefault();
          this.focusItem(0);
          break;
        case "End":
          e.preventDefault();
          this.focusItem(items.length - 1);
          break;
      }
    },
  }));

  /* ── Theme Toggle ── */
  Alpine.data("themeToggle", (initialTheme) => ({
    theme: initialTheme || "auto",
    isDark: document.documentElement.classList.contains("dark"),
    init() {
      this.applyTheme(this.theme);
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
        if (this.theme === "auto") {
          this.setDark(e.matches);
        }
      });
    },
    toggle() {
      const newTheme = this.isDark ? "light" : "dark";
      this.applyTheme(newTheme);
      this.persistTheme(newTheme);
    },
    applyTheme(theme) {
      this.theme = theme;
      const html = document.documentElement;
      html.classList.add("theme-transitioning");

      if (theme === "dark") {
        this.setDark(true);
      } else if (theme === "light") {
        this.setDark(false);
      } else {
        this.setDark(window.matchMedia("(prefers-color-scheme: dark)").matches);
      }

      html.setAttribute("data-theme", theme);
      requestAnimationFrame(() => {
        setTimeout(() => html.classList.remove("theme-transitioning"), 350);
      });
    },
    setDark(dark) {
      this.isDark = dark;
      document.documentElement.classList.toggle("dark", dark);
    },
    persistTheme(theme) {
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
      if (window.htmx) {
        window.htmx.ajax("POST", "/api/profile/theme", {
          values: { theme, csrf_token: csrf },
          target: "#profile-status",
          swap: "innerHTML",
        });
      }
    },
  }));

  /* ── Toast Manager ── */
  Alpine.store("toasts", {
    items: [],
    counter: 0,
    add(message, variant = "info", undo = null) {
      const id = ++this.counter;
      this.items.push({ id, message, variant, undo, visible: true });
      setTimeout(() => this.dismiss(id), 4000);
    },
    dismiss(id) {
      const item = this.items.find((t) => t.id === id);
      if (item) item.visible = false;
      setTimeout(() => {
        this.items = this.items.filter((t) => t.id !== id);
      }, 300);
    },
    handleUndo(undo) {
      if (!undo?.path) return;
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
      if (window.htmx) {
        window.htmx.ajax("POST", undo.path, {
          values: { csrf_token: csrf },
          swap: "none",
        });
      }
    },
  });

  Alpine.data("toastContainer", () => ({
    get toasts() {
      return Alpine.store("toasts").items;
    },
    dismiss(id) {
      Alpine.store("toasts").dismiss(id);
    },
    handleUndo(undo) {
      Alpine.store("toasts").handleUndo(undo);
    },
    iconFor(variant) {
      return (
        { success: "check-circle", error: "x-circle", warning: "alert-triangle", info: "info" }[
          variant
        ] || "info"
      );
    },
    bgFor(variant) {
      return (
        {
          success: "bg-[var(--color-success)] text-white",
          error: "bg-[var(--color-danger)] text-white",
          warning: "bg-[var(--color-warning)] text-white",
          info: "bg-[var(--color-info)] text-white",
        }[variant] || "bg-[var(--color-info)] text-white"
      );
    },
  }));

  /* ── Search Sync ── */
  Alpine.data("searchSync", () => ({
    query: new URLSearchParams(window.location.search).get("q") || "",
    init() {
      this.syncFromUrl();
    },
    syncFromUrl() {
      this.query = new URLSearchParams(window.location.search).get("q") || "";
    },
  }));

  /* ── Command Palette ── */
  Alpine.data("commandPalette", () => ({
    open: false,
    search: "",
    init() {
      window.addEventListener("keydown", (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === "k") {
          e.preventDefault();
          this.toggle();
        }
      });
    },
    toggle() {
      this.open = !this.open;
      if (this.open) {
        this.search = "";
        this.$nextTick(() => this.$refs.searchInput?.focus());
      }
    },
    close() {
      this.open = false;
      this.search = "";
    },
    navigate(url) {
      window.location.href = url;
      this.close();
    },
    get filteredItems() {
      const items = [
        { label: "Discover", url: "/", icon: "compass" },
        { label: "Browse Events", url: "/events", icon: "list" },
        { label: "This Weekend", url: "/weekend", icon: "star" },
        { label: "Calendars", url: "/calendars", icon: "calendar" },
        { label: "Sources", url: "/sources", icon: "radio" },
        { label: "Jobs", url: "/jobs", icon: "wrench" },
        { label: "Settings", url: "/profile", icon: "settings" },
      ];
      if (!this.search) return items;
      const q = this.search.toLowerCase();
      return items.filter((i) => i.label.toLowerCase().includes(q));
    },
  }));

  /* ── Collapsible Panel ── */
  Alpine.data("collapsible", (initialOpen = false) => ({
    open: initialOpen,
    toggle() {
      this.open = !this.open;
    },
  }));

  /* ── Multi-step Form ── */
  Alpine.data("multiStep", (totalSteps) => ({
    step: 1,
    total: totalSteps || 4,
    next() {
      if (this.step < this.total) this.step++;
    },
    prev() {
      if (this.step > 1) this.step--;
    },
    goTo(s) {
      if (s >= 1 && s <= this.total) this.step = s;
    },
    get progress() {
      return Math.round((this.step / this.total) * 100);
    },
  }));

  /* ── Password Strength ── */
  Alpine.data("passwordStrength", () => ({
    password: "",
    get strength() {
      const p = this.password;
      if (!p) return 0;
      let score = 0;
      if (p.length >= 10) score++;
      if (p.length >= 14) score++;
      if (/[A-Z]/.test(p) && /[a-z]/.test(p)) score++;
      if (/\d/.test(p)) score++;
      if (/[^A-Za-z0-9]/.test(p)) score++;
      return Math.min(score, 4);
    },
    get strengthLabel() {
      return ["", "Weak", "Fair", "Good", "Strong"][this.strength];
    },
    get strengthColor() {
      return [
        "bg-[var(--color-edge)]",
        "bg-[var(--color-danger)]",
        "bg-[var(--color-warning)]",
        "bg-[var(--color-info)]",
        "bg-[var(--color-success)]",
      ][this.strength];
    },
  }));

  /* ── Bulk Unattend ── */
  Alpine.data("bulkUnattend", () => ({
    selectAll: false,
    toggleAll() {
      const checkboxes = document.querySelectorAll(".attended-select");
      checkboxes.forEach((cb) => (cb.checked = this.selectAll));
    },
  }));
});

/* ══════════════════════════════════════════════════════════════
   HTMX EVENT HANDLERS
   ══════════════════════════════════════════════════════════════ */

document.body.addEventListener("htmx:afterRequest", (evt) => {
  const trigger = evt.detail.xhr?.getResponseHeader("HX-Trigger");
  if (!trigger) return;
  try {
    const data = JSON.parse(trigger);
    if (data.changeTheme) {
      const themeComp = document.querySelector('[x-data*="themeToggle"]');
      if (themeComp && themeComp.__x) {
        themeComp.__x.$data.applyTheme(data.changeTheme.theme);
      } else {
        applyThemeFallback(data.changeTheme.theme);
      }
    }
    if (data.showToast) {
      Alpine.store("toasts").add(
        data.showToast.message,
        data.showToast.variant,
        data.showToast.undo,
      );
    }
  } catch (e) {
    /* not JSON, ignore */
  }
});

function applyThemeFallback(theme) {
  const html = document.documentElement;
  html.setAttribute("data-theme", theme);
  if (theme === "dark") {
    html.classList.add("dark");
  } else if (theme === "light") {
    html.classList.remove("dark");
  } else {
    html.classList.toggle("dark", window.matchMedia("(prefers-color-scheme: dark)").matches);
  }
}

/* Copy URL handler */
document.addEventListener("click", (e) => {
  const copyBtn = e.target.closest("[data-copy-url]");
  if (copyBtn) {
    navigator.clipboard.writeText(copyBtn.getAttribute("data-copy-url") || "").then(() => {
      Alpine.store("toasts").add("Link copied", "success");
    });
  }
});

/* Sync search inputs after HTMX swaps */
document.body.addEventListener("htmx:afterSwap", () => {
  const q = new URLSearchParams(window.location.search).get("q") || "";
  document.querySelectorAll("[data-global-event-search]").forEach((input) => {
    if (document.activeElement !== input) input.value = q;
  });
  const pageSearch = document.querySelector('#events-form input[name="q"]');
  if (pageSearch && document.activeElement !== pageSearch) pageSearch.value = q;
});

window.addEventListener("popstate", () => {
  const q = new URLSearchParams(window.location.search).get("q") || "";
  document.querySelectorAll("[data-global-event-search]").forEach((input) => {
    if (document.activeElement !== input) input.value = q;
  });
});

/* Bulk unattend HTMX integration */
document.body.addEventListener("htmx:configRequest", (evt) => {
  const target = evt.detail.elt;
  if (target && target.id === "bulk-unattend-form") {
    const ids = Array.from(target.querySelectorAll(".attended-select:checked")).map(
      (el) => el.value,
    );
    if (!ids.length) {
      evt.preventDefault();
      Alpine.store("toasts").add("Select at least one event", "warning");
      return;
    }
    evt.detail.parameters.event_ids = ids;
  }
});

document.body.addEventListener("htmx:afterRequest", (evt) => {
  const target = evt.detail.elt;
  if (target && target.id === "bulk-unattend-form") {
    const eventsForm = document.getElementById("events-form");
    if (eventsForm && window.htmx) {
      window.htmx.trigger(eventsForm, "submit");
    }
  }
});

/* Initialize Lucide icons after DOM ready and HTMX swaps */
function initLucideIcons() {
  if (window.lucide) window.lucide.createIcons();
}

document.addEventListener("DOMContentLoaded", initLucideIcons);
document.body.addEventListener("htmx:afterSwap", initLucideIcons);
document.body.addEventListener("htmx:afterSettle", initLucideIcons);

/* Global showToast function for backward compatibility */
window.showToast = function (message, variant, undo) {
  Alpine.store("toasts").add(message, variant || "info", undo);
};
