/* ═══════════════════════════════════════════════════════════════════════
   Stellar Sorter — client
   Talks to the local Python server. Every request carries the session token
   the server embedded in this page, so a stray browser tab cannot drive it.
   ═══════════════════════════════════════════════════════════════════════ */

(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const state = {
    files: [],            // everything the last scan returned
    view: [],             // files after filters + sort
    root: null,
    sort: { key: "name", reverse: false },
    selected: new Set(),  // absolute paths
    extFilter: new Set(),
    resFilter: new Set(),
    plan: null,
    busy: false,
  };

  /* ── Transport ─────────────────────────────────────────────────────── */

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Sorter-Token": window.SORTER_TOKEN,
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  }

  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });

  /* ── Telemetry log & toast ─────────────────────────────────────────── */

  function log(message, kind = "") {
    const item = document.createElement("li");
    if (kind) item.className = `is-${kind}`;
    const time = document.createElement("time");
    time.textContent = new Date().toLocaleTimeString([], { hour12: false });
    item.append(time, document.createTextNode(message));
    const list = $("#log");
    list.append(item);
    while (list.children.length > 200) list.firstChild.remove();
    list.scrollTop = list.scrollHeight;
  }

  let toastTimer = null;
  function toast(message, kind = "") {
    const el = $("#toast");
    el.textContent = message;
    el.className = `toast${kind ? ` toast--${kind}` : ""}`;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
  }

  function setBusy(busy) {
    state.busy = busy;
    $("#scan-btn").disabled = busy;
    $("#plan-btn").disabled = busy || state.selected.size === 0;
    document.body.style.cursor = busy ? "progress" : "";
  }

  /* ── Formatting ────────────────────────────────────────────────────── */

  const humanSize = (bytes) => {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = bytes, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
    return unit === 0 ? `${value} B` : `${value.toFixed(1)} ${units[unit]}`;
  };

  const humanRuntime = (seconds) => {
    if (!seconds) return "—";
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    if (hours) return `${hours}h ${minutes}m`;
    // Below a minute, "0m" reads like a bug; show the seconds instead.
    return minutes ? `${minutes}m` : `${Math.round(seconds)}s`;
  };

  const humanDate = (epoch) =>
    new Date(epoch * 1000).toLocaleDateString([], { year: "numeric", month: "short", day: "2-digit" });

  const resolutionClass = (label) => {
    if (label === "8K" || label === "4K") return "tag tag--4k";
    if (label === "1440p" || label === "1080p" || label === "720p") return "tag tag--hd";
    return "tag tag--sd";
  };

  /* ── Filtering & sorting (client-side, over the scanned set) ───────── */

  const SORT_VALUE = {
    name: (f) => f.name,
    extension: (f) => f.extension,
    codec: (f) => f.codec,
    size: (f) => f.size,
    duration: (f) => f.duration,
    resolution: (f) => f.pixels,
    modified: (f) => f.modified,
  };

  // "Ep 2" before "Ep 10".
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

  function computeView() {
    const query = $("#filter-query").value.trim().toLowerCase();
    const minSize = Number($("#filter-min-size").value) * 1024 * 1024 || 0;
    const minDuration = Number($("#filter-min-duration").value) * 60 || 0;

    let files = state.files.filter((file) => {
      if (query && !file.path.toLowerCase().includes(query)) return false;
      if (minSize && file.size < minSize) return false;
      // An unprobed file has no measurable length; a length filter excludes it.
      if (minDuration && (file.duration === null || file.duration < minDuration)) return false;
      if (state.extFilter.size && !state.extFilter.has(file.extension)) return false;
      if (state.resFilter.size && !state.resFilter.has(file.resolution_label)) return false;
      return true;
    });

    const value = SORT_VALUE[state.sort.key] || SORT_VALUE.name;
    const known = [], unknown = [];
    files.forEach((f) => (value(f) === null || value(f) === undefined ? unknown : known).push(f));

    known.sort((a, b) => {
      const left = value(a), right = value(b);
      const result = typeof left === "string" ? collator.compare(left, right) : left - right;
      return state.sort.reverse ? -result : result;
    });
    // Unmeasured files park at the end in both directions.
    unknown.sort((a, b) => collator.compare(a.name, b.name));

    state.view = known.concat(unknown);
  }

  /* ── Rendering ─────────────────────────────────────────────────────── */

  function render() {
    computeView();
    renderGrid();
    renderStats();
    renderSortIndicators();
    $("#plan-btn").disabled = state.busy || state.selected.size === 0;
  }

  function renderGrid() {
    const body = $("#grid-body");
    body.textContent = "";
    $("#empty").hidden = state.view.length > 0;

    const fragment = document.createDocumentFragment();
    for (const file of state.view) {
      const row = document.createElement("tr");
      row.dataset.path = file.path;
      if (state.selected.has(file.path)) row.classList.add("is-selected");

      const checkCell = document.createElement("td");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = state.selected.has(file.path);
      check.setAttribute("aria-label", `Select ${file.name}`);
      checkCell.append(check);

      const nameCell = document.createElement("td");
      nameCell.className = "cell-name truncate";
      nameCell.title = file.path;
      nameCell.textContent = file.name;
      if (file.relative !== file.name) {
        const sub = document.createElement("small");
        sub.className = "truncate";
        sub.textContent = file.relative;
        nameCell.append(sub);
      }

      const resCell = document.createElement("td");
      resCell.className = "num";
      const badge = document.createElement("span");
      badge.className = resolutionClass(file.resolution_label);
      badge.textContent = file.resolution_label;
      badge.title = file.resolution;
      resCell.append(badge);

      row.append(
        checkCell,
        nameCell,
        cell(file.extension.toUpperCase(), "num"),
        cell(file.size_human, "num"),
        cell(file.duration_human, "num"),
        resCell,
        cell(file.codec, "num"),
        cell(humanDate(file.modified), "num"),
      );
      fragment.append(row);
    }
    body.append(fragment);
  }

  function cell(text, className = "") {
    const td = document.createElement("td");
    if (className) td.className = className;
    td.textContent = text;
    return td;
  }

  function renderStats() {
    const totalSize = state.view.reduce((sum, f) => sum + f.size, 0);
    const totalTime = state.view.reduce((sum, f) => sum + (f.duration || 0), 0);
    $("#stat-count").textContent = state.view.length;
    $("#stat-size").textContent = humanSize(totalSize);
    $("#stat-duration").textContent = humanRuntime(totalTime);
    $("#stat-selected").textContent = state.selected.size;

    const visible = new Set(state.view.map((f) => f.path));
    const allChecked = state.view.length > 0 &&
      state.view.every((f) => state.selected.has(f.path));
    const head = $("#head-check");
    head.checked = allChecked;
    head.indeterminate = !allChecked &&
      [...state.selected].some((path) => visible.has(path));
  }

  function renderSortIndicators() {
    document.querySelectorAll(".grid thead th[data-sort]").forEach((th) => {
      const active = th.dataset.sort === state.sort.key;
      th.classList.toggle("is-sorted", active);
      th.classList.toggle("is-desc", active && state.sort.reverse);
    });
  }

  function renderFilterPills() {
    const extensions = new Map(), resolutions = new Map();
    for (const file of state.files) {
      extensions.set(file.extension, (extensions.get(file.extension) || 0) + 1);
      resolutions.set(file.resolution_label, (resolutions.get(file.resolution_label) || 0) + 1);
    }
    buildPills($("#filter-extensions"), [...extensions.keys()].sort(), state.extFilter, extensions);
    buildPills($("#filter-resolutions"), orderResolutions([...resolutions.keys()]), state.resFilter, resolutions);
  }

  const RESOLUTION_ORDER = ["8K", "4K", "1440p", "1080p", "720p", "480p", "SD", "Unknown"];
  const orderResolutions = (labels) =>
    labels.sort((a, b) => RESOLUTION_ORDER.indexOf(a) - RESOLUTION_ORDER.indexOf(b));

  function buildPills(container, labels, activeSet, counts) {
    container.textContent = "";
    for (const label of labels) {
      const pill = document.createElement("button");
      pill.type = "button";
      pill.className = "pill";
      pill.textContent = `${label} ${counts.get(label)}`;
      pill.setAttribute("aria-pressed", activeSet.has(label) ? "true" : "false");
      pill.addEventListener("click", () => {
        activeSet.has(label) ? activeSet.delete(label) : activeSet.add(label);
        pill.setAttribute("aria-pressed", activeSet.has(label) ? "true" : "false");
        render();
      });
      container.append(pill);
    }
  }

  /* ── Scanning ──────────────────────────────────────────────────────── */

  async function scan() {
    const path = $("#scan-path").value.trim();
    if (!path) { toast("Set a directory to scan first", "error"); return; }

    setBusy(true);
    log(`scanning ${path}…`);
    try {
      const result = await post("/api/scan", {
        path,
        recursive: $("#opt-recursive").checked,
        probe: $("#opt-probe").checked,
      });
      state.files = result.files;
      state.root = result.root;
      state.selected = new Set(result.files.map((f) => f.path));
      state.extFilter.clear();
      state.resFilter.clear();

      $("#root-status").textContent = result.root;
      $("#root-status").className = "chip chip--live";
      $("#root-status").title = result.root;
      if (!$("#dest-path").value.trim()) {
        $("#dest-path").value = `${result.root}/Sorted`;
      }

      renderFilterPills();
      render();
      log(`${result.summary.count} files · ${humanSize(result.summary.total_size)}`, "ok");
      if (result.summary.count && !result.summary.probed) {
        log("no ffprobe — duration, resolution and codec unavailable");
      }
    } catch (error) {
      log(error.message, "error");
      toast(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  /* ── Planning & applying ───────────────────────────────────────────── */

  function planRequest() {
    return {
      paths: state.view.filter((f) => state.selected.has(f.path)).map((f) => f.path),
      destination: $("#dest-path").value.trim(),
      rule: $("#rule-select").value,
      action: document.querySelector('input[name="action"]:checked').value,
    };
  }

  async function plotCourse() {
    const request = planRequest();
    if (!request.paths.length) { toast("Select at least one file", "error"); return; }
    if (!request.destination) { toast("Set a destination directory", "error"); return; }

    setBusy(true);
    try {
      const plan = await post("/api/plan", request);
      state.plan = plan;
      showPlan(plan);
      log(`plan ready — ${plan.count} ${plan.action}s into ${Object.keys(plan.buckets).length} folders`);
    } catch (error) {
      log(error.message, "error");
      toast(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  function showPlan(plan) {
    $("#plan-summary").textContent =
      `${plan.count} file${plan.count === 1 ? "" : "s"} will be ${plan.action}d into ${plan.destination_root} — ${plan.rule_label.toLowerCase()}.`;

    const buckets = $("#plan-buckets");
    buckets.textContent = "";
    for (const [name, count] of Object.entries(plan.buckets)) {
      const chip = document.createElement("span");
      chip.className = "bucket";
      const strong = document.createElement("b");
      strong.textContent = count;
      chip.append(document.createTextNode(name), strong);
      buckets.append(chip);
    }

    const body = $("#plan-body");
    body.textContent = "";
    for (const move of plan.moves) {
      const row = document.createElement("tr");
      const from = cell(move.name);
      const to = document.createElement("td");
      to.className = move.renamed ? "renamed" : "";
      const arrow = document.createElement("span");
      arrow.className = "arrow";
      arrow.textContent = "→ ";
      to.append(arrow, document.createTextNode(`${move.bucket}/${move.new_name}`));
      if (move.renamed) to.title = "Renamed to avoid overwriting an existing file";
      row.append(from, to);
      body.append(row);
    }

    const renamed = plan.moves.filter((m) => m.renamed).length;
    const parts = [];
    if (renamed) parts.push(`${renamed} renamed to avoid a collision`);
    if (plan.skipped.length) parts.push(`${plan.skipped.length} already in place`);
    $("#plan-warning").textContent = parts.join(" · ");

    $("#apply-btn").disabled = plan.count === 0;
    $("#apply-btn").textContent = "";
    const glyph = document.createElement("span");
    glyph.className = "btn__glyph";
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = "⇥";
    $("#apply-btn").append(glyph, document.createTextNode(` Engage — ${plan.action} ${plan.count}`));

    openModal("#plan-modal");
  }

  async function engage() {
    if (!state.plan) return;
    const request = planRequest();
    setBusy(true);
    $("#apply-btn").disabled = true;
    try {
      // The server re-plans from these inputs; it never trusts a client plan.
      const result = await post("/api/apply", request);
      closeModal("#plan-modal");
      const moved = result.succeeded.length;
      log(`${moved} file${moved === 1 ? "" : "s"} ${result.action}d`, "ok");
      if (result.failed.length) {
        log(`${result.failed.length} failed`, "error");
        result.failed.slice(0, 5).forEach((f) => log(`${f.name}: ${f.error}`, "error"));
      }
      if (result.journal) log(`journal: ${result.journal}`);
      toast(`${moved} file${moved === 1 ? "" : "s"} ${result.action}d`, "ok");
      state.plan = null;
      await scan();  // Re-scan: paths just changed underneath us.
    } catch (error) {
      log(error.message, "error");
      toast(error.message, "error");
      $("#apply-btn").disabled = false;
    } finally {
      setBusy(false);
    }
  }

  /* ── Directory browser ─────────────────────────────────────────────── */

  let browseTarget = "#scan-path";
  let browseCurrent = null;

  async function browse(path) {
    try {
      const query = path ? `?path=${encodeURIComponent(path)}` : "";
      const result = await api(`/api/browse${query}`);
      browseCurrent = result.path;
      $("#browse-path").textContent = result.path;

      const list = $("#browse-list");
      list.textContent = "";
      if (result.parent) {
        const up = document.createElement("li");
        up.className = "is-up";
        up.textContent = "..";
        up.addEventListener("click", () => browse(result.parent));
        list.append(up);
      }
      for (const dir of result.directories) {
        const item = document.createElement("li");
        item.textContent = dir.name;
        item.addEventListener("click", () => browse(dir.path));
        list.append(item);
      }
      if (!result.directories.length && !result.parent) {
        const none = document.createElement("li");
        none.textContent = "(no subdirectories)";
        list.append(none);
      }
      openModal("#browse-modal");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  /* ── Modals ────────────────────────────────────────────────────────── */

  const openModal = (sel) => { $(sel).hidden = false; };
  const closeModal = (sel) => { $(sel).hidden = true; };

  document.addEventListener("click", (event) => {
    if (event.target.matches("[data-close]")) {
      closeModal(`#${event.target.closest(".modal").id}`);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") document.querySelectorAll(".modal").forEach((m) => (m.hidden = true));
  });

  /* ── Wiring ────────────────────────────────────────────────────────── */

  function wire() {
    $("#scan-btn").addEventListener("click", scan);
    $("#plan-btn").addEventListener("click", plotCourse);
    $("#apply-btn").addEventListener("click", engage);

    $("#scan-path").addEventListener("keydown", (e) => { if (e.key === "Enter") scan(); });

    ["#filter-query", "#filter-min-size", "#filter-min-duration"].forEach((sel) =>
      $(sel).addEventListener("input", render)
    );
    $("#clear-filters").addEventListener("click", () => {
      $("#filter-query").value = "";
      $("#filter-min-size").value = "";
      $("#filter-min-duration").value = "";
      state.extFilter.clear();
      state.resFilter.clear();
      renderFilterPills();
      render();
    });

    document.querySelectorAll(".grid thead th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        // Same column toggles direction; a new column starts ascending.
        state.sort = key === state.sort.key
          ? { key, reverse: !state.sort.reverse }
          : { key, reverse: false };
        render();
      });
    });

    // One delegated listener beats one per row.
    $("#grid-body").addEventListener("click", (event) => {
      const row = event.target.closest("tr");
      if (!row) return;
      const path = row.dataset.path;
      const isCheckbox = event.target.matches('input[type="checkbox"]');
      if (!isCheckbox && window.getSelection().toString()) return;  // don't fight text selection
      state.selected.has(path) ? state.selected.delete(path) : state.selected.add(path);
      row.classList.toggle("is-selected", state.selected.has(path));
      row.querySelector('input[type="checkbox"]').checked = state.selected.has(path);
      renderStats();
      $("#plan-btn").disabled = state.busy || state.selected.size === 0;
    });

    $("#head-check").addEventListener("change", (event) => {
      event.target.checked ? selectAllVisible() : clearSelection();
    });
    $("#select-all").addEventListener("click", selectAllVisible);
    $("#select-none").addEventListener("click", clearSelection);

    $("#browse-btn").addEventListener("click", () => {
      browseTarget = "#scan-path";
      browse($("#scan-path").value.trim() || null);
    });
    $("#browse-pick").addEventListener("click", () => {
      if (browseCurrent) $(browseTarget).value = browseCurrent;
      closeModal("#browse-modal");
    });
  }

  function selectAllVisible() {
    state.view.forEach((f) => state.selected.add(f.path));
    render();
  }

  function clearSelection() {
    state.selected.clear();
    render();
  }

  /* ── Starfield ─────────────────────────────────────────────────────── */

  function starfield() {
    const canvas = $("#starfield");
    const ctx = canvas.getContext("2d");
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let stars = [], width = 0, height = 0;

    function seed() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const count = Math.min(340, Math.round((width * height) / 5200));
      stars = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        radius: Math.random() * 1.15 + 0.25,
        // Far stars are dimmer and drift slower — cheap parallax.
        depth: Math.random(),
        phase: Math.random() * Math.PI * 2,
      }));
    }

    function draw(time) {
      ctx.clearRect(0, 0, width, height);
      for (const star of stars) {
        if (!still) {
          star.y += (0.012 + star.depth * 0.05);
          if (star.y > height + 2) { star.y = -2; star.x = Math.random() * width; }
        }
        const twinkle = still ? 0.7 : 0.55 + Math.sin(time / 900 + star.phase) * 0.35;
        const alpha = (0.25 + star.depth * 0.6) * twinkle;
        ctx.beginPath();
        ctx.arc(star.x, star.y, star.radius * (0.55 + star.depth * 0.7), 0, Math.PI * 2);
        ctx.fillStyle = star.depth > 0.86
          ? `rgba(196, 181, 253, ${alpha})`   // a few violet giants
          : `rgba(226, 232, 255, ${alpha})`;
        ctx.fill();
      }
      if (!still) requestAnimationFrame(draw);
    }

    seed();
    requestAnimationFrame(draw);

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { seed(); if (still) draw(0); }, 180);
    });
  }

  /* ── Boot ──────────────────────────────────────────────────────────── */

  async function boot() {
    starfield();
    wire();
    try {
      const info = await api("/api/health");
      const select = $("#rule-select");
      for (const rule of info.rules) {
        const option = document.createElement("option");
        option.value = rule.key;
        option.textContent = rule.label;
        option.selected = rule.key === info.default_rule;
        select.append(option);
      }
      $("#scan-path").value = info.start_path || info.root;
      const probe = $("#probe-status");
      probe.textContent = info.ffprobe ? "deep scan ready" : "probing offline";
      probe.className = `chip ${info.ffprobe ? "chip--live" : "chip--muted"}`;
      if (!info.ffprobe) {
        probe.title = "Install ffmpeg to read duration, resolution and codec";
        $("#opt-probe").checked = false;
      }
      log(`sorter online · confined to ${info.root}`, "ok");
      if (info.start_path) scan();
    } catch (error) {
      log(`cannot reach the sorter service: ${error.message}`, "error");
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
