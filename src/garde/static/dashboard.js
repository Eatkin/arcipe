// Garde Manger dashboard. Vanilla JS, no build step.
const $ = (id) => document.getElementById(id);
const STORE = "garde.state.v2";
const LAST_PLAN = "garde.lastplan.v1";  // the shown plan, so a reload doesn't lose it
const NO_LIMIT = 125;            // time slider's far right
const AREAS = [
  ["fridge", "Fridge"], ["freezer", "Freezer"], ["cupboard", "Cupboard"], ["counter", "Counter & fruit bowl"],
];
const EFFORT_WORDS = ["", "quick look", "light", "normal", "thorough", "leave no stone unturned"];

let META = null;
let state = null;          // settings, kept in localStorage
let pantry = [];           // [{id, amount, use_by, area}], kept on the server
let dishes = [];           // [{recipe_id, portions, use_by, cooked, grams_per_portion}], same
let lastResult = null;     // the shown plan, as /api/plan returned it
let lastBody = null;       // ...and the settings that made it
let savedPlans = [];       // summaries from /api/plans
let savedPlan = null;      // the saved plan being looked at
let running = null;        // AbortController while a search runs
let hasPlan = false;
let pantryTimer = null, pantrySaving = Promise.resolve();
let pantryVersion = null;  // the server's pantry version we last saw: a save from a stale tab is refused
let shownDay = null;       // the day the page was drawn for: redrawn when the date ticks over

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const ingById = (id) => META.ingredients.find((i) => i.id === id);
const recipeName = (id) => (META.recipes.find((r) => r.id === id) || { name: id }).name;
// Liquids (with a density) show in ml, everything else in grams; the server
// always counts grams
const unitOf = (id) => ((ingById(id) || {}).density ? "ml" : "g");
const toShown = (id, g) => g / ((ingById(id) || {}).density || 1);
const fromShown = (id, v) => v * ((ingById(id) || {}).density || 1);
function amountText(id, g) {
  const unit = unitOf(id);
  const v = toShown(id, g);
  if (v >= 1000) return `${+(v / 1000).toFixed(2)}${unit === "ml" ? "l" : "kg"}`;
  return `${Math.round(v)}${unit}`;
}
const amountHint = (ing) => (ing && ing.unit_weight ? "count" : ing && ing.density ? "ml" : "grams");
const ingByName = (name) => {
  const n = name.trim().toLowerCase();
  return META.ingredients.find((i) => i.name.toLowerCase() === n || i.id === n);
};

// A message at the bottom with an Undo button, for things done in one click
let toastTimer = null;
function toast(text, undo) {
  const box = $("toast");
  box.innerHTML = `<span>${esc(text)}</span>${undo ? `<button>Undo</button>` : ""}`;
  box.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("on"), 8000);
  if (undo) box.querySelector("button").addEventListener("click", async () => {
    box.classList.remove("on");
    try { await undo(); } catch (e) { showError(e.message); }
  });
}

// ------------------------------------------------------------------ dates
const isoDay = (d) => {
  const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
  return z.toISOString().slice(0, 10);
};
const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const addDays = (s, n) => { const d = parseDay(s); d.setDate(d.getDate() + n); return isoDay(d); };
const daysBetween = (a, b) => Math.round((parseDay(b) - parseDay(a)) / 86400000);
const today = () => isoDay(new Date());
const prettyDay = (s) => parseDay(s).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const isWeekend = (s) => [0, 6].includes(parseDay(s).getDay());

// ------------------------------------------------------------------ settings
function defaults() {
  return {
    people: 2, days: 7, meals: ["dinner"], start_date: today(),
    weeknight: 45, weekend: NO_LIMIT, handson: NO_LIMIT, weights: { ...META.weights }, effort: 3, seed: 0,
    shopping: false, pins: {},
  };
}

function save() { localStorage.setItem(STORE, JSON.stringify(state)); }

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE));
    if (raw) {
      const s = { ...defaults(), ...raw, weights: { ...META.weights, ...raw.weights } };
      if (s.start_date < today()) s.start_date = today();
      for (const k of Object.keys(s.pins)) if (k.split("|")[0] < today()) delete s.pins[k];
      return s;
    }
  } catch (e) { /* fall through */ }
  return defaults();
}

// ------------------------------------------------------------------ tabs
function route() {
  const [tab0, planId] = location.hash.slice(1).split("/");
  const tab = ["pantry", "plans"].includes(tab0) ? tab0 : "plan";
  for (const t of ["plan", "plans", "pantry"]) {
    $(`view-${t}`).classList.toggle("on", t === tab);
    $(`tab-${t}`).classList.toggle("on", t === tab);
  }
  if (tab === "plan") renderPantrySummary();
  if (tab === "plans") showPlans(planId).catch((e) => showError(e.message));
}

// ------------------------------------------------------------------ pantry
const areaOf = (id) => (ingById(id) || {}).storage || "fridge";

function defaultUseBy(ing) {
  return ing && ing.shelf_life_days ? addDays(today(), ing.shelf_life_days - 1) : "";
}

function badge(useBy) {
  if (!useBy) return `<span class="badge">keeps</span>`;
  const left = daysBetween(today(), useBy);
  if (left < 0) return `<span class="badge gone">gone off</span>`;
  const text = left === 0 ? "use today" : left === 1 ? "tomorrow" : `${left + 1} days`;
  return `<span class="badge ${left <= 2 ? "soon" : ""}">${text}</span>`;
}

function renderPantry() {
  const box = $("areas");
  box.innerHTML = "";
  for (const [area, title] of AREAS) {
    const rows = pantry.filter((r) => r.area === area);
    const panel = document.createElement("section");
    panel.className = "panel";
    panel.innerHTML = `
      <div class="area-head"><h2>${title}</h2><span class="count" data-count="${area}"></span></div>
      <div class="prow head"><span>Ingredient</span><span>Amount</span><span>Use by</span><span></span><span></span><span></span><span></span></div>
      <div class="rows"></div>
      <datalist id="dl-${area}">${areaOptions(area)}</datalist>
      <button class="add">+ Add</button>`;
    const list = panel.querySelector(".rows");
    rows.forEach((row) => list.appendChild(pantryRow(row, area)));
    panel.querySelector(".add").addEventListener("click", () => {
      pantry.push({ id: "", amount: "", use_by: "", area });
      renderPantry();
      const inputs = $("areas").querySelectorAll(`[data-area="${area}"] input[type=text]`);
      inputs[inputs.length - 2]?.focus();
    });
    box.appendChild(panel);
  }
  areaCounts();
  renderDishes();
  renderGoneOff();
  renderStaples();
}

function areaCounts() {
  for (const [area] of AREAS) {
    const el = document.querySelector(`[data-count="${area}"]`);
    if (el) el.textContent = plural(pantry.filter((r) => r.area === area && r.id).length, "thing");
  }
}

// Take a pantry row off (used up), with an Undo
function removeRow(row) {
  const at = pantry.indexOf(row);
  pantry.splice(at, 1);
  renderPantry(); pantryChanged();
  toast(`${row.id ? ingName(row.id) : "Row"} taken off`, () => {
    pantry.splice(Math.min(at, pantry.length), 0, row);
    renderPantry(); pantryChanged();
  });
}

// Staples assumed in: click one you don't keep and plans shop for it instead
let notKept = [];

const byName = (a, b) => a.name.localeCompare(b.name);

function renderStaples() {
  const staples = META.ingredients.filter((i) => i.unlimited).sort(byName);
  $("staples").innerHTML = staples.map((i) => {
    const off = notKept.includes(i.id);
    return `<li><button class="staple ${off ? "off" : ""}" data-id="${esc(i.id)}" aria-pressed="${!off}"
      title="${off ? "Not kept: plans shop for it. Click if you do keep it." : "Click if you don't keep it"}"><span class="tick">${off ? "" : "✓"}</span>${esc(i.name)}</button></li>`;
  }).join("");
  $("staples-count").textContent = notKept.length ? `${notKept.length} not kept` : "";
  $("staples").querySelectorAll("button").forEach((btn) => btn.addEventListener("click", async () => {
    const id = btn.dataset.id;
    const next = notKept.includes(id) ? notKept.filter((x) => x !== id) : [...notKept, id];
    try {
      notKept = (await api("/api/household", { method: "PUT", body: { not_kept: next } })).not_kept;
      renderStaples();
      markStale();
      $("status").textContent = `${ingName(id)}: ${notKept.includes(id) ? "not kept, plans will shop for it" : "assumed in"}`;
    } catch (e) { showError(e.message); }
  }));
}

// Bulk add: every tracked ingredient A to Z, type amounts for what you've got, add the lot
function renderBulk() {
  const have = (id) => pantry.some((r) => r.id === id && String(r.amount).trim());
  let letter = "";
  $("bulk-list").innerHTML = META.ingredients.filter((i) => !i.unlimited).sort(byName).map((i) => {
    const first = i.name[0].toUpperCase();
    const head = first !== letter ? `<div class="aisle">${esc((letter = first))}</div>` : "";
    return `${head}<label class="bulk-row ${have(i.id) ? "have" : ""}">
      <span>${esc(i.name)}${have(i.id) ? ` <span class="chip">in</span>` : ""}</span>
      <input type="text" data-id="${esc(i.id)}" placeholder="${amountHint(i)}" aria-label="${esc(i.name)} amount">
    </label>`;
  }).join("");
  $("bulk-list").querySelectorAll("input").forEach((el) => el.addEventListener("input", bulkCount));
  bulkCount();
}

function bulkCount() {
  const n = [...$("bulk-list").querySelectorAll("input")].filter((el) => el.value.trim()).length;
  $("bulk-add").disabled = !n;
  $("bulk-add").textContent = n ? `Add ${plural(n, "thing")} to the pantry` : "Add to the pantry";
}

function bulkAdd() {
  const filled = [...$("bulk-list").querySelectorAll("input")].filter((el) => el.value.trim());
  for (const el of filled) {
    const ing = ingById(el.dataset.id);
    pantry.push({ id: ing.id, amount: el.value.trim(), use_by: defaultUseBy(ing), area: areaOf(ing.id) });
  }
  renderPantry();
  pantryChanged();
  renderBulk();
  $("status").textContent = `added ${plural(filled.length, "thing")}`;
}

// Into the freezer (keeps META.freezer_days) or out of it (2 more days)
function freeze(thing, on) {
  thing.frozen = on;
  thing.use_by = addDays(today(), on ? META.freezer_days : 1);
}

const goneOff = (useBy) => useBy && daysBetween(today(), useBy) < 0;
const asItem = (r) => ({ id: r.id, amount: String(r.amount).trim(), use_by: r.use_by || null, frozen: Boolean(r.frozen) });

function renderGoneOff() {
  const items = pantry.filter((r) => r.id && String(r.amount).trim() && goneOff(r.use_by));
  const old = dishes.filter((d) => goneOff(d.use_by));
  $("gone-off").style.display = items.length || old.length ? "" : "none";
  if (!items.length && !old.length) return;
  const names = [...items.map((r) => ingName(r.id)), ...old.map((d) => recipeName(d.recipe_id))];
  $("gone-off-text").textContent = `${names.join(", ")} ${names.length === 1 ? "is" : "are"} past ${names.length === 1 ? "its" : "their"} use-by. The planner ignores ${names.length === 1 ? "it" : "them"}.`;
  $("gone-off-bin").onclick = () => binThings(items, old);
  $("gone-off-keep").onclick = () => {  // still fine: give it a couple more days
    for (const r of items) r.use_by = addDays(today(), 1);
    for (const d of old) d.use_by = addDays(today(), 1);
    renderPantry(); pantryChanged();
  };
}

async function binThings(items, gone) {
  try {
    await flushPantry();
    const data = await api("/api/pantry/bin", {
      method: "POST",
      body: { items: items.map(asItem), dishes: gone, date: today() },
    });
    setPantry(data.pantry);
    renderPantry();
    renderWaste(data.waste);
    markStale();
    const g = data.waste.recent.slice(0, items.length + gone.length).reduce((t, e) => t + e.grams, 0);
    const what = [...items.map((r) => ingName(r.id)), ...gone.map((d) => recipeName(d.recipe_id))];
    toast(`Binned ${what.join(", ")} (${g}g)`, async () => {
      await flushPantry();
      const back = await api("/api/pantry/unbin", { method: "POST", body: { items: items.map(asItem), dishes: gone } });
      setPantry(back.pantry); renderPantry(); renderWaste(back.waste); markStale();
      $("status").textContent = "put back";
    });
  } catch (e) { showError(e.message); }
}

let waste = { month_g: 0 };

function renderWaste(w) {
  waste = w;
  renderPantrySummary();
  $("waste-month").textContent = w.month_g
    ? `Binned this month: ${w.month_g >= 1000 ? (w.month_g / 1000).toFixed(1) + "kg" : w.month_g + "g"}.`
    : "Nothing binned this month.";
}

async function loadWaste() { renderWaste(await api("/api/waste")); }

function renderDishes() {
  $("dishes-count").textContent = plural(dishes.length, "dish").replace("dishs", "dishes");
  if (!dishes.length) {
    $("dishes").innerHTML = `<div class="hint">Nothing cooked waiting. Tick a meal off on a saved plan and spare portions land here.</div>`;
    return;
  }
  $("dishes").innerHTML = `<div class="drow prow head"><span>Dish</span><span>Portions</span><span>Use by</span><span></span><span></span><span></span><span></span></div>`;
  for (const dish of dishes) {
    const el = document.createElement("div");
    el.className = "drow";
    el.innerHTML = `
      <span class="dname" title="${dish.cooked ? `cooked ${esc(prettyDay(dish.cooked))}` : ""}">${esc(recipeName(dish.recipe_id))}</span>
      <input type="number" min="0" step="0.5" value="${esc(dish.portions)}" aria-label="Portions">
      <input type="date" value="${esc(dish.use_by)}" aria-label="Use by">
      ${dish.frozen ? `<span class="badge frozen">frozen</span>` : badge(dish.use_by)}
      <button class="x freeze" title="${dish.frozen ? "Take it out to defrost: good for 2 more days" : "Put it in the freezer"}">${dish.frozen ? "☀" : "❄"}</button>
      <button class="x bin" title="Binned it: counts as waste">🗑</button>
      <button class="x eat" title="Eaten: take it off the list">×</button>`;
    const [portions, useBy] = el.querySelectorAll("input");
    portions.addEventListener("input", () => { dish.portions = Number(portions.value) || 0; pantryChanged(); });
    useBy.addEventListener("change", () => {
      if (!useBy.value) return;
      dish.use_by = useBy.value; renderPantry(); pantryChanged();
    });
    el.querySelector("button.eat").addEventListener("click", () => {
      const at = dishes.indexOf(dish);
      dishes.splice(at, 1); renderPantry(); pantryChanged();
      toast(`${recipeName(dish.recipe_id)} taken off`, () => {
        dishes.splice(Math.min(at, dishes.length), 0, dish); renderPantry(); pantryChanged();
      });
    });
    el.querySelector("button.bin").addEventListener("click", () => binThings([], [dish]));
    el.querySelector("button.freeze").addEventListener("click", () => { freeze(dish, !dish.frozen); renderPantry(); pantryChanged(); });
    $("dishes").appendChild(el);
  }
}

// This area's things first, then everything else
function areaOptions(area) {
  const here = META.ingredients.filter((i) => !i.unlimited && i.storage === area);
  const rest = META.ingredients.filter((i) => !i.unlimited && i.storage !== area);
  return [...here, ...rest].map((i) => `<option value="${esc(i.name)}">`).join("");
}

function pantryRow(row, area) {
  const ing = ingById(row.id);
  const el = document.createElement("div");
  el.className = "prow";
  el.dataset.area = area;
  el.row = row;
  el.innerHTML = `
    <input type="text" list="dl-${area}" value="${esc(ing ? ing.name : row.id)}" aria-label="Ingredient">
    <input type="text" value="${esc(row.amount)}" aria-label="Amount"
      placeholder="${amountHint(ing)}">
    <input type="date" value="${esc(row.use_by)}" aria-label="Use by">
    ${row.frozen ? `<span class="badge frozen">frozen</span>` : badge(row.use_by)}
    <button class="x freeze" title="${row.frozen ? "Take it out to defrost: good for 2 more days" : "Put it in the freezer"}">${row.frozen ? "☀" : "❄"}</button>
    <button class="x bin" title="Binned it: counts as waste">🗑</button>
    <button class="x eat" title="Used up or gone: take it off the list">×</button>
    <div class="problem"></div>`;
  markProblem(el);
  const [name, amount, useBy] = el.querySelectorAll("input");
  // Redraw just this row (same place on the page), then put the cursor back
  const redraw = (focus) => {
    const fresh = pantryRow(row, area);
    el.replaceWith(fresh);
    if (focus !== undefined) fresh.querySelectorAll("input")[focus].focus();
    areaCounts(); renderGoneOff(); renderPantrySummary();
  };
  name.addEventListener("change", () => {
    const found = ingByName(name.value);
    name.classList.toggle("bad", !found);
    name.title = found ? "" : "Not one the planner knows: pick from the list";
    if (!found) return;
    row.id = found.id;
    if (!row.use_by) row.use_by = defaultUseBy(found);
    row.area = found.storage || area;  // moves to its own shelf on the next full redraw
    redraw(String(row.amount).trim() ? undefined : 1);
    pantryChanged();
  });
  amount.addEventListener("input", () => { row.amount = amount.value; pantryChanged(); });
  useBy.addEventListener("change", () => { row.use_by = useBy.value; redraw(); pantryChanged(); });
  el.querySelector("button.eat").addEventListener("click", () => removeRow(row));
  const freezeBtn = el.querySelector("button.freeze");
  freezeBtn.disabled = !row.id || (area === "freezer" && !row.frozen) || ((ingById(row.id) || {}).storage === "freezer");
  freezeBtn.addEventListener("click", () => {
    freeze(row, !row.frozen);
    row.area = row.frozen ? "freezer" : areaOf(row.id);
    renderPantry(); pantryChanged();
  });
  const binBtn = el.querySelector("button.bin");
  binBtn.disabled = !(row.id && String(row.amount).trim());
  binBtn.addEventListener("click", () => binThings([row], []));
  return el;
}

// A row whose amount the server couldn't read: say so under it
function markProblem(el) {
  const problem = el.row.problem || "";
  el.querySelectorAll("input")[1].classList.toggle("bad", Boolean(problem));
  el.querySelector(".problem").textContent = problem;
}

function pantryChanged() {
  markStale();
  clearTimeout(pantryTimer);
  pantryTimer = setTimeout(savePantry, 500);
}

function savePantry() {
  clearTimeout(pantryTimer);
  pantryTimer = null;
  const rows = pantry.filter((r) => r.id && String(r.amount).trim());
  pantrySaving = fetch("/api/pantry", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: rows.map(asItem), dishes: dishes.filter((d) => d.portions > 0), version: pantryVersion }),
  }).then(async (res) => {
    const data = await res.json();
    if (res.status === 409) {  // changed on another tab or device: theirs wins, show it
      setPantry(data.pantry); renderPantry(); markStale();
      showError("The pantry was changed somewhere else (another tab or the phone), so it's been reloaded. Check your last change went in.");
      return;
    }
    if (!res.ok) throw new Error(data.error);
    pantryVersion = data.version;
    for (const r of pantry) r.problem = null;
    for (const p of data.problems || []) rows[p.index].problem = p.error;
    document.querySelectorAll("#areas .prow[data-area]").forEach((el) => el.row && markProblem(el));
    const bad = data.problems || [];
    showError(bad.length ? `Not saved: ${bad.map((p) => p.error).join(" · ")}` : "");
    $("status").textContent = bad.length ? "pantry saved, apart from that" : "pantry saved";
  }).catch((e) => showError(`Pantry not saved: ${e.message}`));
  return pantrySaving;
}

async function flushPantry() {
  if (pantryTimer) await savePantry();
  else await pantrySaving;
}

function setPantry(data) {
  pantryVersion = data.version || null;
  pantry = data.items.map((i) => ({ ...i, use_by: i.use_by || "", area: i.frozen ? "freezer" : areaOf(i.id) }));
  dishes = data.dishes || [];
}

async function loadPantry() {
  setPantry(await (await fetch("/api/pantry")).json());
  // One-off move of the old in-browser fridge list
  const old = JSON.parse(localStorage.getItem("garde.state.v1") || "null");
  if (!pantry.length && old && old.fridge && old.fridge.length) {
    pantry = old.fridge.filter((r) => r.id && r.amount).map((r) => ({
      id: r.id, amount: String(r.amount), area: areaOf(r.id),
      use_by: r.expires !== "" && r.expires != null
        ? addDays(today(), Number(r.expires) - 1) : defaultUseBy(ingById(r.id)),
    }));
    await savePantry();
  }
  localStorage.removeItem("garde.state.v1");
}

function renderPantrySummary() {
  const behind = savedPlans.filter((p) => p.unticked && p.end_date >= addDays(today(), -7));
  const nag = behind.map((p) => `<p class="nag">${plural(p.unticked, "meal")} on
    <a href="#plans/${esc(p.id)}">${esc(p.name)}</a> not ticked off, so the pantry may be out of date.</p>`).join("");
  $("pantry-nag").innerHTML = nag;
  const have = pantry.filter((r) => r.id && String(r.amount).trim());
  if (!have.length && !dishes.length) {
    $("pantry-summary").innerHTML = `<p class="hint">Nothing in yet. Add what you've got and the plan will use it up.</p>`;
    return;
  }
  const soon = have.filter((r) => r.use_by && daysBetween(today(), r.use_by) <= 2)
    .sort((a, b) => a.use_by.localeCompare(b.use_by));
  const cooked = dishes.length ? `, ${dishes.length} cooked dish${dishes.length === 1 ? "" : "es"}` : "";
  $("pantry-summary").innerHTML = `
    <div class="summary-count">${plural(have.length, "thing")} in${cooked}${soon.length ? `, <b>${soon.length}</b> to use soon` : ""}</div>
    ${waste.month_g ? `<div class="hint">${waste.month_g}g binned this month.</div>` : ""}
    <ul class="summary-list">${dishes.map((d) => `
      <li><span>${esc(recipeName(d.recipe_id))} × ${d.portions}</span>${badge(d.use_by)}</li>`).join("")}
    ${soon.map((r) => `
      <li><span>${esc(ingById(r.id).name)}</span>${badge(r.use_by)}</li>`).join("")}</ul>`;
}

// ------------------------------------------------------------------ settings UI
function minutesWord(v) { return Number(v) >= NO_LIMIT ? "no limit" : `${v} min`; }

function renderKnobs() {
  $("people").value = state.people;
  $("days").value = state.days;
  $("start_date").value = state.start_date;
  $("weeknight").value = state.weeknight;
  $("weekend").value = state.weekend;
  $("handson").value = state.handson;
  $("effort").value = state.effort;
  $("shopping").checked = state.shopping;
  $("meals").innerHTML = META.meals.map((m) => `
    <label><input type="checkbox" value="${m}" ${state.meals.includes(m) ? "checked" : ""}>${m}</label>`).join("");
  $("meals").querySelectorAll("input").forEach((cb) => cb.addEventListener("change", () => {
    state.meals = [...$("meals").querySelectorAll("input:checked")].map((c) => c.value);
    applyTimeFloor();
    changed();
  }));
  $("weights").innerHTML = Object.entries(META.weight_scale).map(([k, s]) => `
    <div class="knob">
      <div class="top"><span class="name">${esc(s.label)}</span></div>
      <input type="range" id="w-${k}" min="0" max="${s.max}" step="${s.max / 100}" value="${state.weights[k]}">
      <div class="ends"><span>${esc(s.low)}</span><span>${esc(s.high)}</span></div>
    </div>`).join("");
  for (const k of Object.keys(META.weight_scale)) {
    $(`w-${k}`).addEventListener("input", (e) => {
      state.weights[k] = Number(e.target.value); knobLabels(); changed();
    });
  }
  applyTimeFloor();
}

// Nothing is quicker than the quickest recipe for each meal picked, so the time
// sliders don't go below it (rounded down to the slider's 5-minute steps)
function timeFloor() {
  const q = META.quickest_minutes || {};
  const slowest = Math.max(10, ...state.meals.map((m) => q[m] || 0));
  return Math.floor(slowest / 5) * 5;
}

function applyTimeFloor() {
  const floor = timeFloor();
  for (const id of ["weeknight", "weekend"]) {
    $(id).min = floor;
    if (state[id] < floor) { state[id] = floor; $(id).value = floor; save(); }
  }
  const h = META.least_hands_on || {};
  const handsFloor = Math.max(5, Math.floor(Math.max(0, ...state.meals.map((m) => h[m] || 0)) / 5) * 5);
  $("handson").min = handsFloor;
  if (state.handson < handsFloor) { state.handson = handsFloor; $("handson").value = handsFloor; save(); }
  const q = META.quickest_minutes || {};
  const slowest = state.meals.filter((m) => q[m]).sort((a, b) => q[b] - q[a])[0];
  $("time-floor").textContent = slowest
    ? `The quickest ${slowest} takes about ${q[slowest]} min, so the sliders start at ${floor}.`
    : "";
  knobLabels();
}

function knobLabels() {
  $("weeknight-val").textContent = minutesWord(state.weeknight);
  $("weekend-val").textContent = minutesWord(state.weekend);
  $("handson-val").textContent = minutesWord(state.handson);
  $("effort-val").textContent = EFFORT_WORDS[state.effort];
  for (const k of Object.keys(META.weight_scale)) {
    $(`w-${k}`).title = `weight ${Number(state.weights[k]).toFixed(1)} (default ${META.weights[k]})`;
  }
}

function changed() { save(); markStale(); renderPinsNote(); }

function markStale() {
  if (hasPlan) $("view-plan").querySelector("main").classList.add("is-stale");
}
const isStale = () => $("view-plan").querySelector("main").classList.contains("is-stale");

// Meals locked or skipped by hand on the Plan tab: say how many, with a way out
function renderPinsNote() {
  const all = Object.values(state.pins || {});
  const here = planPins();
  const box = $("pins-note");
  if (!all.length) { box.style.display = "none"; return; }
  const skipped = here.filter((p) => p.pick === "~skip").length;
  const parts = [];
  if (here.length - skipped) parts.push(`${plural(here.length - skipped, "meal")} locked`);
  if (skipped) parts.push(`${skipped} skipped`);
  if (all.length > here.length) parts.push(`${all.length - here.length} more outside these days`);
  box.style.display = "";
  box.innerHTML = `🔒 Fixed by hand: ${parts.join(", ")}. <button class="link" id="pins-clear">Clear them</button>`;
  $("pins-clear").addEventListener("click", () => {
    state.pins = {}; save(); renderPinsNote();
    if (lastResult) render(lastResult, { keepStale: true });
    markStale();
  });
}

// ------------------------------------------------------------------ planning
function requestBody() {
  const limit = (v) => (Number(v) >= NO_LIMIT ? 0 : Number(v));
  return {
    people: state.people, days: state.days, meals: state.meals, start_date: state.start_date,
    weeknight_minutes: limit(state.weeknight), weekend_minutes: limit(state.weekend),
    weeknight_active_minutes: limit(state.handson),
    weights: state.weights, effort: state.effort, seed: state.seed, shopping: state.shopping,
    pins: planPins(),
  };
}

// Meals fixed by hand on the Plan tab, keyed "date|meal" -> pick
const pinKey = (date, meal) => `${date}|${meal}`;

function planPins() {
  const out = [];
  for (const [key, pick] of Object.entries(state.pins || {})) {
    const [date, meal] = key.split("|");
    const day = daysBetween(state.start_date, date) + 1;
    if (day >= 1 && day <= state.days && state.meals.includes(meal)) out.push({ day, meal, pick });
  }
  return out;
}

function setRunning(on) {
  $("plan-btn").textContent = on ? "Stop" : "Plan the week";
  $("plan-btn").classList.toggle("stop", on);
}

// keep: a swap, skip or lock on the shown plan. If the settings haven't changed
// since, the rest of the week stays as it is and only the amounts are worked out
// again; otherwise it's a fresh search.
async function plan(keep = false) {
  if (running) { running.abort(); return; }
  if (!state.meals.length) { showError("Pick at least one meal."); return; }
  showError("");
  await flushPantry();
  running = new AbortController();
  const body = requestBody();
  const send = keep && lastResult && !isStale()
    ? { ...body, start: keep.start || choiceOf(lastResult), search: false } : body;
  setRunning(true);
  progress({ done: 0, total: 1 }, "Starting…");
  try {
    const res = await fetch("/api/plan/stream", {
      method: "POST", signal: running.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(send),
    });
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    await readEvents(res.body, {
      progress: (d) => progress(d),
      result: (d) => {
        lastBody = body;
        render(d);
        rememberPlan();
        progress({ done: 1, total: 1 }, send.search === false
          ? "Done: the rest of the week is as it was."
          : `Done: ${d.evaluations.toLocaleString()} different weeks tried.`);
      },
      error: (d) => { throw new Error(d.error); },
    });
  } catch (e) {
    if (e.name === "AbortError") progress({ done: 0, total: 1 }, "Stopped. The last plan is still shown.");
    else { showError(e.message); progress({ done: 0, total: 1 }, ""); }
  } finally {
    running = null;
    setRunning(false);
  }
}

// Server-Sent Events from a fetch body (EventSource can't POST)
async function readEvents(body, handlers) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const fields = {};
      for (const line of block.split("\n")) {
        const i = line.indexOf(": ");
        if (i > 0) fields[line.slice(0, i)] = line.slice(i + 2);
      }
      handlers[fields.event]?.(JSON.parse(fields.data));
    }
  }
}

function progress(d, text) {
  $("progress-bar").style.width = `${Math.round((100 * d.done) / Math.max(d.total, 1))}%`;
  if (text !== undefined) { $("progress-text").textContent = text; return; }
  const b = d.best;
  $("progress-text").textContent =
    `${d.weeks_tried.toLocaleString()} different weeks tried (${Math.round((100 * d.done) / d.total)}%)` +
    ` · best so far: ${b.wasted_g}g wasted, ${b.unplanned} meal${b.unplanned === 1 ? "" : "s"} missing, ${b.hours}h cooking`;
}

function showError(msg) {
  $("error").textContent = msg;
  $("error").style.display = msg ? "block" : "none";
}

// ------------------------------------------------------------------ results
// Against the greedy plan (one night at a time). `words` = [more, less]: "224g less"
function metric(label, now, before, unit, lowerIsBetter = true, digits = 0, words = ["more", "less"]) {
  const fmt = (v) => `${Number(v).toFixed(digits)}${unit}`;
  const diff = now - before;
  let cls = "same", text = "no different";
  if (Math.abs(diff) > 1e-6) {
    const better = lowerIsBetter ? diff < 0 : diff > 0;
    cls = better ? "good" : "bad";
    text = `${fmt(Math.abs(diff))} ${diff > 0 ? words[0] : words[1]}`;
  }
  return `<div class="metric"><div class="label">${label}</div>
    <div class="value">${fmt(now)}</div><div class="delta ${cls}">${text}</div></div>`;
}

function render(data, { keepStale = false, saved = false } = {}) {
  hasPlan = true;
  lastResult = data;
  $("save-btn").disabled = saved;
  $("save-btn").textContent = saved ? "Saved ✓" : "Save plan";
  $("copy-week").disabled = false;
  if (!keepStale) $("view-plan").querySelector("main").classList.remove("is-stale");
  renderPinsNote();
  const b = data.breakdown, g = data.greedy;
  const meals = countMeals(data);
  $("metrics").innerHTML =
    metric("Food wasted", b.waste_g + b.spill_g, g.waste_g + g.spill_g, "g") +
    metric("Meals planned", meals, meals + b.unplanned - g.unplanned, "", false) +
    metric("Cooking time", b.minutes / 60, g.minutes / 60, "h", true, 1, ["longer", "shorter"]) +
    metric("Sameness", b.variety, g.variety, "", true, 1) +
    (state.shopping ? metric("Spend", b.price_points, g.price_points, " pts") : "");
  renderShopping(data.plan.shopping);

  const grid = $("days-grid");
  grid.innerHTML = "";
  for (const day of data.plan.days) {
    const card = document.createElement("section");
    card.className = "panel day";
    card.innerHTML = `<h3><span>${prettyDay(day.date)}</span>${isWeekend(day.date) ? `<span class="chip">weekend</span>` : ""}</h3>`;
    for (const m of day.meals) card.appendChild(mealEl(day.day, m, planTabOpts(day)));
    grid.appendChild(card);
  }
  renderStock(data.plan.stock, data.plan);
}

// Lock / swap / skip on the Plan tab: pins in the settings, then plan again
function planTabOpts(day) {
  return {
    key: `${day.day}-`,
    locked: (m) => state.pins[pinKey(day.date, m.meal)] !== undefined && !m.skipped,
    onLock: (m, on) => {
      if (on) state.pins[pinKey(day.date, m.meal)] = m.pick;
      else delete state.pins[pinKey(day.date, m.meal)];
      save();
      render(lastResult, { keepStale: isStale(), saved: $("save-btn").disabled });
      rememberPlan();
    },
    onSkip: async (m, on) => {
      const key = pinKey(day.date, m.meal);
      if (on) { state.pins[key] = "~skip"; save(); plan(true); return; }
      // Back on: the best thing for that meal with the rest of the week as it is
      delete state.pins[key];
      save();
      if (isStale()) { plan(); return; }
      const choice = choiceOf(lastResult);
      choice[slotIndex(day.day, m.meal)] = null;
      try {
        const { options } = await api("/api/plan/options", {
          method: "POST", body: { ...lastBody, pins: planPins(), choice, day: day.day, meal: m.meal },
        });
        if (options.length) choice[slotIndex(day.day, m.meal)] = options[0].pick;
        plan({ start: choice });
      } catch (e) { showError(e.message); }
    },
    options: (m) => api("/api/plan/options", {
      method: "POST",
      body: { ...lastBody, pins: planPins(), choice: choiceOf(lastResult), day: day.day, meal: m.meal },
    }),
    onPick: (m, pick) => { state.pins[pinKey(day.date, m.meal)] = pick; save(); plan(true); },
  };
}

// Where a meal sits in a week's list of picks (day by day, meals in order)
function slotIndex(dayNo, meal) {
  let i = 0;
  for (const d of lastResult.plan.days) {
    for (const m of d.meals) {
      if (d.day === dayNo && m.meal === meal) return i;
      i += 1;
    }
  }
  return -1;
}

// The shown plan survives a reload (until its last day has gone)
function rememberPlan() {
  if (!lastResult) return;
  localStorage.setItem(LAST_PLAN, JSON.stringify({
    result: lastResult, body: lastBody, pantry: pantryVersion, saved: $("save-btn").disabled,
  }));
}

function restorePlan() {
  try {
    const kept = JSON.parse(localStorage.getItem(LAST_PLAN));
    if (!kept || !kept.result) return;
    const days = kept.result.plan.days;
    if (!days.length || days[days.length - 1].date < today()) { localStorage.removeItem(LAST_PLAN); return; }
    lastBody = kept.body;
    render(kept.result, { saved: kept.saved });
    const now = { ...requestBody(), pins: [] }, then = { ...kept.body, pins: [] };
    if (JSON.stringify(now) !== JSON.stringify(then) || kept.pantry !== pantryVersion) markStale();
    progress({ done: 0, total: 1 }, "The last plan, from before. Press Plan the week to look again.");
  } catch (e) { localStorage.removeItem(LAST_PLAN); }
}

const choiceOf = (data) => data.plan.days.flatMap((d) => d.meals.map((m) => (m.skipped ? "~skip" : m.pick ?? null)));

function countMeals(data) {
  return data.plan.days.reduce((n, d) => n + d.meals.filter((m) => m.recipe_id).length, 0);
}

function howTo(m) {
  const sources = m.sources.length
    ? `<div class="sources"><b>Cook from:</b>${m.sources.map((s) => `
        <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)} <span class="site">${esc(s.site)}</span></a>`).join("")}</div>`
    : `<p class="note">No source recipe yet, so the outline is all there is.</p>`;
  return `<details class="how"><summary>How to make it</summary>
    ${m.description ? `<p>${esc(m.description)}</p>` : ""}
    <ol>${m.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
    <p class="note">A rough outline from the dish's shape, not a tested recipe.</p>
    ${m.notes ? `<p class="note">${esc(m.notes)}</p>` : ""}
    ${sources}
  </details>`;
}

// One meal. opts (all optional): onRate (rate it: only once it's cooked), locked(m),
// onLock(m, on), onSkip(m, on), options(m) -> {options}, onPick(m, pick),
// fixed (ticked off or gone: no changing it), extra(el) to add more
function mealEl(day, m, opts = {}) {
  const el = document.createElement("div");
  el.className = "meal";
  if (m.skipped) {
    el.innerHTML = `<div class="kind">${m.meal}</div><div class="skipped">Skipped: nobody's eating in.</div>`;
  } else if (!m.recipe_id) {
    el.innerHTML = `<div class="kind">${m.meal}</div><div class="unplanned">Nothing to cook.</div>
      <div class="why">${esc(m.why || "Add something to the pantry or turn on shopping.")}</div>`;
  } else if (m.leftovers_from != null) {
    const from = m.leftovers_from_date ? prettyDay(m.leftovers_from_date) : `day ${m.leftovers_from}`;
    const mix = Object.entries(m.leftover_portions || {});
    const name = mix.length > 1
      ? mix.map(([id, n]) => `${recipeName(id)} ×${n}`).join(" + ")
      : m.recipe_name;
    el.innerHTML = `<div class="kind">${m.meal}</div>
      <div class="title"><span class="name">${esc(name)}</span>
      <span class="chip">${mix.length > 1 ? "leftovers, a bit of each" : `leftovers from ${esc(from)}`}</span></div>
      ${m.defrost ? `<div class="ahead">❄ From the freezer: defrost it the night before.</div>` : ""}`;
  } else {
    const slots = m.slots.map((s) => `<li><span class="slot" title="${esc(s.label)}">${esc(s.label)}</span>
      <span>${s.items.map((i) => `${esc(i.name)}${i.grams ? ` <span class="grams">${esc(i.amount || amountText(i.id, i.grams))}</span>` : ""}`).join(", ")}</span></li>`).join("");
    el.innerHTML = `
      <div class="kind">${m.meal}</div>
      <div class="title"><span class="name">${esc(m.recipe_name)}</span>
        <span class="chip">${m.minutes} min · ${m.active_minutes} hands-on</span></div>
      ${m.make_ahead.length ? `<div class="ahead">⏰ Make ahead: ${m.make_ahead.map(esc).join(", ")}</div>` : ""}
      <ul class="slots">${slots}</ul>
      ${howTo(m)}
      ${opts.onRate ? `<div class="rate">
        <span class="ask">How was it?</span>
        <button data-r="1" title="Liked it. Click again to take it back">👍</button>
        <button data-r="-1" title="Not again. Click again to take it back" class="down">👎</button>
      </div>` : ""}`;
    if (opts.onRate) drawRating(el, m);
    el.querySelectorAll(".rate button").forEach((btn) => {
      btn.addEventListener("click", () => rate(m, Number(btn.dataset.r), el, opts.onRate));
    });
  }
  if (opts.extra) opts.extra(el);
  if (!opts.fixed && (opts.onLock || opts.onSkip || opts.options)) el.appendChild(mealTools(m, opts));
  return el;
}

function mealTools(m, opts) {
  const box = document.createElement("div");
  box.className = "tools";
  const locked = opts.locked ? opts.locked(m) : false;
  if (m.skipped) {
    box.innerHTML = `<button data-do="unskip">Bring it back</button>`;
  } else {
    box.innerHTML = `
      ${m.recipe_id && opts.onLock ? `<button data-do="lock" class="${locked ? "on" : ""}" title="Keep this when re-planning">${locked ? "🔒 Locked" : "Lock"}</button>` : ""}
      ${opts.options ? `<button data-do="swap" title="What else could go here">${m.recipe_id ? "Swap…" : "Pick something…"}</button>` : ""}
      ${opts.onSkip ? `<button data-do="skip" title="Everyone's out: don't plan this meal">Skip</button>` : ""}`;
  }
  const list = document.createElement("div");
  list.className = "options";
  box.appendChild(list);
  box.addEventListener("click", async (e) => {
    const what = e.target.closest("button")?.dataset.do;
    if (!what) return;
    if (what === "lock") opts.onLock(m, !locked);
    if (what === "skip") opts.onSkip(m, true);
    if (what === "unskip") opts.onSkip(m, false);
    if (what === "swap") await showOptions(m, opts, list);
  });
  return box;
}

async function showOptions(m, opts, list) {
  if (list.childElementCount) { list.innerHTML = ""; return; }
  list.innerHTML = `<div class="hint">Looking…</div>`;
  try {
    const { options } = await opts.options(m);
    if (!options.length) { list.innerHTML = `<div class="hint">Nothing else can be made here.</div>`; return; }
    list.innerHTML = `<div class="hint">Best first, with the rest of the week as it is:</div>` + options.map((o, i) => `
      <button data-i="${i}" class="option">
        <span>${esc(o.name)}${o.double ? ` <span class="chip">cook double</span>` : ""}</span>
        <span class="grams">${o.minutes} min${wasteWords(o.waste_change_g)}</span>
      </button>`).join("");
    list.querySelectorAll("button.option").forEach((btn) => btn.addEventListener("click", (e) => {
      e.stopPropagation();
      opts.onPick(m, options[Number(btn.dataset.i)].pick);
    }));
  } catch (e) { list.innerHTML = ""; showError(e.message); }
}

const wasteWords = (g) => (g > 0 ? ` · ${g}g more wasted` : g < 0 ? ` · ${-g}g less wasted` : "");

function drawRating(el, m) {
  el.querySelectorAll(".rate button").forEach((btn) => btn.classList.toggle("on", m.rating === Number(btn.dataset.r)));
  el.querySelector(".rate").classList.toggle("asking", !m.rating);
}

// Rate a cooked meal; the other thumb changes it, the same one again takes it back
async function rate(m, clicked, el, onRate) {
  const rating = m.rating === clicked ? 0 : clicked;
  try {
    const data = await api("/api/rate", {
      method: "POST", body: { recipe_id: m.recipe_id, ingredients: m.ingredients, rating, ...onRate() },
    });
    m.rating = rating || null;
    drawRating(el, m);
    $("status").textContent = rating
      ? `saved: ${m.recipe_name} now ${data.recipe_score >= 0 ? "+" : ""}${data.recipe_score.toFixed(2)}`
      : `${m.recipe_name}: rating taken back`;
  } catch (e) { showError(e.message); }
}

const PRICE_DOTS = (n) => "£".repeat(n) || "free";

// Shop sections, in walking-round order
const AISLES = [
  ["fruit_veg", "Fruit & veg"], ["bakery", "Bakery"], ["chilled", "Chilled"],
  ["frozen", "Frozen"], ["cupboard", "Tins, jars & dry goods"],
];
const aisleOf = (id) => (ingById(id) || {}).aisle || "cupboard";

// Rows grouped by aisle: rowHtml(row) for each, under a heading per aisle
function byAisle(rows, rowHtml) {
  return AISLES.map(([aisle, title]) => {
    const here = rows.filter((r) => aisleOf(r.id) === aisle);
    return here.length ? `<div class="aisle">${title}</div><ul class="shop">${here.map(rowHtml).join("")}</ul>` : "";
  }).join("");
}

function listText(title, rows) {
  const lines = [title];
  for (const [aisle, name] of AISLES) {
    const here = rows.filter((r) => aisleOf(r.id) === aisle);
    if (!here.length) continue;
    lines.push("", name);
    for (const r of here) lines.push(`- ${r.packs} × ${r.name} (${r.amount || `${r.grams}g`})`);
  }
  return lines.join("\n");
}

// The week as plain text, for the fridge door or a message
function weekText(title, days) {
  const lines = [title];
  for (const d of days) {
    lines.push("", prettyDay(d.date));
    for (const m of d.meals) {
      let what = "nothing planned";
      if (m.skipped) what = "skipped";
      else if (m.recipe_id && m.leftovers_from != null) what = `leftovers: ${m.recipe_name}`;
      else if (m.recipe_id) what = `${m.recipe_name} (${m.minutes} min)${m.make_ahead.length ? `, make ahead: ${m.make_ahead.join(", ")}` : ""}`;
      lines.push(`- ${m.meal}: ${what}`);
    }
  }
  return lines.join("\n");
}

async function copyText(text, btn, label) {
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "Copied ✓";
  } catch (e) {  // no clipboard (plain http from a phone): show it to copy by hand
    const area = document.createElement("textarea");
    area.className = "copy-text"; area.value = text; area.rows = Math.min(text.split("\n").length, 30);
    btn.after(area); area.select();
  }
  setTimeout(() => { btn.textContent = label; }, 2000);
}

async function copyList(title, rows, btn) {
  const text = listText(title, rows);
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "Copied ✓";
  } catch (e) {  // no clipboard: show it to copy by hand
    const area = document.createElement("textarea");
    area.className = "copy-text"; area.value = text; area.rows = text.split("\n").length;
    btn.after(area); area.select();
  }
  setTimeout(() => { btn.textContent = "Copy list"; }, 2000);
}

function renderShopping(items) {
  $("shop-panel").style.display = state.shopping ? "block" : "none";
  if (!state.shopping) return;
  if (!items.length) { $("shop").innerHTML = `<div class="empty">Nothing to buy. The pantry covers it.</div>`; return; }
  const total = items.reduce((t, i) => t + i.price * i.packs, 0);
  $("shop").innerHTML = byAisle(items, (i) => `
    <li><span class="n">${i.packs}×</span>
      <span>${esc(i.name)} <span class="g">${esc(i.amount || `${i.grams}g`)}</span></span>
      <span class="price" title="${esc(i.price_word)}"><b>${PRICE_DOTS(i.price)}</b> ${esc(i.price_word)}</span></li>`)
    + `<div class="shop-total">${items.length} things · ${total} price points <button class="copy">Copy list</button></div>`;
  const btn = $("shop").querySelector("button.copy");
  btn.addEventListener("click", () => copyList("Shopping", items, btn));
}

function renderStock(stock, plan) {
  if (!stock.length) { $("stock").innerHTML = `<div class="empty">Nothing tracked. Add things to the pantry.</div>`; return; }
  $("stock").innerHTML = `<table>
    <tr><th>Ingredient</th><th>Had</th><th>Bought / made</th><th>Used</th><th>Left</th><th>Wasted</th><th></th></tr>
    ${stock.map((s) => `<tr>
      <td>${esc(s.name)}</td><td>${amountText(s.id, s.start)}</td><td>${s.bought ? amountText(s.id, s.bought) : s.made ? amountText(s.id, s.made) + " made" : "–"}</td><td>${amountText(s.id, s.used)}</td><td>${amountText(s.id, s.left)}</td>
      <td class="${s.wasted ? "waste" : ""}">${s.wasted ? amountText(s.id, s.wasted) : "–"}</td>
      <td><div class="bar">
        <span class="used" style="width:${pct(s.used, s.start + s.bought + s.made)}%"></span>
        <span class="wasted" style="width:${pct(s.wasted, s.start + s.bought + s.made)}%"></span></div></td>
    </tr>`).join("")}</table>
    ${plan.leftovers_wasted.map((d) => `<div class="empty waste">${esc(d.name)} leftovers went off: ${d.wasted}g</div>`).join("")}
    ${plan.leftovers_left.map((d) => `<div class="empty">Still in the fridge: ${d.portions} portions of ${esc(d.name)}</div>`).join("")}`;
}

const pct = (a, b) => (b ? Math.max(0, Math.min(100, (100 * a) / b)) : 0);

// ------------------------------------------------------------------ saved plans
async function api(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json" },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function savePlan() {
  if (!lastResult) return;
  const btn = $("save-btn");
  try {
    if (btn.dataset.armed !== "1") {
      const days = lastResult.plan.days;
      const [first, last] = [days[0].date, days[days.length - 1].date];
      savedPlans = (await api("/api/plans")).plans;
      const clash = savedPlans.find((p) => p.start_date <= last && p.end_date >= first && p.end_date >= today());
      if (clash) {  // two plans for the same days would both take food out of the pantry
        btn.dataset.armed = "1";
        btn.textContent = `Overlaps “${clash.name}”: save anyway?`;
        setTimeout(() => { if (btn.dataset.armed) { btn.dataset.armed = ""; btn.textContent = "Save plan"; } }, 5000);
        return;
      }
    }
    btn.dataset.armed = "";
    const saved = await api("/api/plans", { method: "POST", body: { result: lastResult, settings: lastBody } });
    $("save-btn").disabled = true;
    $("save-btn").textContent = "Saved ✓";
    rememberPlan();
    $("status").textContent = `saved: ${saved.name}`;
    location.hash = `#plans/${saved.id}`;
  } catch (e) { showError(`Plan not saved: ${e.message}`); }
}

async function showPlans(planId) {
  savedPlans = (await api("/api/plans")).plans;
  const now = savedPlans.find((p) => p.start_date <= today() && p.end_date >= today());
  const id = planId || (now || savedPlans[0] || {}).id;
  savedPlan = id ? await api(`/api/plans/${id}`).catch(() => null) : null;
  renderPlanList();
  renderSaved();
}

function renderPlanList() {
  if (!savedPlans.length) { $("plans-list").innerHTML = `<p class="hint">None yet.</p>`; return; }
  $("plans-list").innerHTML = `<ul class="plan-list">${savedPlans.map((p) => `
    <li><a href="#plans/${esc(p.id)}" class="${savedPlan && savedPlan.id === p.id ? "on" : ""}">
      <div>${esc(p.name)}</div>
      <div class="sub">${p.cooked} of ${plural(p.meals, "meal")} done${p.to_buy ? ` · ${p.bought}/${p.to_buy} bought` : ""} · saved ${esc(prettyDay(p.saved_at.slice(0, 10)))}</div>
    </a></li>`).join("")}</ul>`;
}

function renderSaved() {
  $("saved-empty").style.display = savedPlan ? "none" : "block";
  $("saved").style.display = savedPlan ? "block" : "none";
  if (!savedPlan) return;
  const s = savedPlan.settings || {};
  $("saved-name").textContent = savedPlan.name;
  const last = (savedPlan.replans || []).slice(-1)[0];
  const over = savedPlan.plan.days[savedPlan.plan.days.length - 1].date < today();
  $("replan-box").style.display = over ? "none" : "";
  $("replan-note").textContent = last ? `Last re-planned ${prettyDay(last.on)}.` : "";
  $("saved-meta").textContent = [
    plural(savedPlan.people, "person").replace("persons", "people"),
    plural(savedPlan.plan.days.length, "day"),
    (s.meals || []).join(", "),
  ].filter(Boolean).join(" · ");
  renderUnticked();
  renderSavedShopping();
  const grid = $("saved-grid");
  grid.innerHTML = "";
  for (const day of savedPlan.plan.days) {
    const card = document.createElement("section");
    card.className = "panel day";
    card.innerHTML = `<h3><span>${prettyDay(day.date)}</span>${day.date === today() ? `<span class="chip">today</span>` : ""}</h3>`;
    for (const m of day.meals) card.appendChild(savedMealEl(day, m));
    if (day.date < today()) card.classList.add("past");
    grid.appendChild(card);
  }
}

// Meals from days gone by that were never ticked off: their food still counts
// as in the pantry, so ask
function renderUnticked() {
  const box = $("unticked");
  const missed = savedPlan.plan.days.filter((d) => d.date < today()).flatMap((d) => d.meals
    .filter((m) => m.recipe_id && !m.cooked && !m.skipped).map((m) => ({ d, m })));
  box.style.display = missed.length ? "" : "none";
  if (!missed.length) return;
  box.innerHTML = `<div><b>Not ticked off yet.</b> <span class="hint">Until they are, their food still
      counts as in the pantry.</span></div>
    <ul class="missed">${missed.map(({ d, m }, i) => `<li>
      <span>${esc(prettyDay(d.date))}, ${esc(m.meal)}: ${esc(m.recipe_name)}${m.leftovers_from != null ? " (leftovers)" : ""}</span>
      <button data-i="${i}" data-do="cooked">${m.leftovers_from != null ? "Ate it" : "Cooked it"}</button>
      <button data-i="${i}" data-do="skip">Didn't happen</button></li>`).join("")}</ul>`;
  box.querySelectorAll("button").forEach((btn) => btn.addEventListener("click", async () => {
    const { d, m } = missed[Number(btn.dataset.i)];
    box.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      if (btn.dataset.do === "cooked") {
        await changePlan("cook", { day: d.day, meal: m.meal, cooked: true, date: d.date }, `${m.recipe_name}: done, pantry updated`);
      } else {
        await changeMeal("meal", { day: d.day, meal: m.meal, skipped: true }, `${m.recipe_name}: marked as not made`);
        savedPlans = (await api("/api/plans")).plans;
        renderPlanList(); renderPantrySummary();
      }
    } catch (e) { showError(e.message); renderSaved(); }
  }));
}

function tookText(done) {
  const parts = [];
  if (done.instead && done.instead.recipe_id) parts.push(`Made ${recipeName(done.instead.recipe_id)} instead`);
  else if (done.instead) parts.push("Cooked with other amounts");
  if (done.taken.length) {
    const names = [...new Set(done.taken.map((i) => (ingById(i.id) || { name: i.id }).name))];
    parts.push(`Took out ${names.join(", ")}`);
  }
  for (const i of done.added) parts.push(`put away ${(ingById(i.id) || { name: i.id }).name}`);
  if (done.dish) parts.push(`put away ${plural(done.dish.portions, "portion")}`);
  const eaten = done.eaten.reduce((n, d) => n + d.portions, 0);
  if (done.eaten.length) parts.push(`${plural(eaten, "portion")} out of the fridge`);
  return parts.length ? `${parts.join("; ")}.` : "Nothing in the pantry to take out.";
}

function savedMealEl(day, m) {
  const gone = day.date < today();
  const opts = {
    onRate: m.cooked && m.leftovers_from == null
      ? () => ({ plan_id: savedPlan.id, day: day.day, meal: m.meal }) : null,
    fixed: Boolean(m.cooked) || gone,
    locked: (x) => Boolean(x.locked),
    onLock: (x, on) => changeMeal("meal", { day: day.day, meal: x.meal, locked: on }, on ? "locked" : "unlocked"),
    onSkip: (x, on) => (on
      ? replan({ set: { day: day.day, meal: x.meal, pick: "~skip" }, search: false }, `${x.meal} skipped`)
      : replan({ unskip: { day: day.day, meal: x.meal } }, `${x.meal} back on, rest of the week re-planned`)),
    options: (x) => api(`/api/plans/${savedPlan.id}/options`, { method: "POST", body: { day: day.day, meal: x.meal, date: today() } }),
    onPick: (x, pick) => replan({ set: { day: day.day, meal: x.meal, pick }, search: false }, "swapped"),
    extra: (el) => {
      if (!m.recipe_id) {
        if (!m.skipped && !gone && !m.cooked) el.appendChild(cookedDifferently(day, m));
        if (m.cooked) {
          el.insertAdjacentHTML("beforeend", `<div class="took">${esc(tookText(m.cooked))}</div>`);
          const undo = document.createElement("label");
          undo.className = "cook";
          undo.innerHTML = `<input type="checkbox" checked> Cooked <span class="chip">${esc(prettyDay(m.cooked.on))}</span>`;
          const tick = undo.querySelector("input");
          tick.addEventListener("change", () => cookMeal(day.day, { ...m, recipe_name: "that meal" }, tick));
          el.insertBefore(undo, el.querySelector(".kind").nextSibling);
        }
        return;
      }
      el.classList.toggle("done", Boolean(m.cooked));
      const leftovers = m.leftovers_from != null;
      const box = document.createElement("div");
      box.innerHTML = `<label class="cook"><input type="checkbox" ${m.cooked ? "checked" : ""}>
          ${leftovers ? "Eaten" : "Cooked"}${m.cooked ? ` <span class="chip">${esc(prettyDay(m.cooked.on))}</span>` : ""}</label>
        ${m.cooked ? `<div class="took">${esc(tookText(m.cooked))}</div>` : ""}`;
      el.insertBefore(box, el.querySelector(".kind").nextSibling);
      const tick = box.querySelector("input");
      tick.addEventListener("change", () => cookMeal(day.day, m, tick));
      if (!m.cooked && !leftovers) el.appendChild(cookedDifferently(day, m));
      const spare = m.cooked && m.cooked.dish && dishes.find((d) => d.recipe_id === m.cooked.dish.recipe_id
        && d.cooked === m.cooked.dish.cooked && !d.frozen);
      if (spare) {
        const btn = document.createElement("button");
        btn.className = "freeze-spare";
        btn.textContent = `❄ Freeze the ${plural(spare.portions, "spare portion")}`;
        btn.addEventListener("click", async () => {
          freeze(spare, true); renderPantry(); await savePantry(); renderSaved();
          $("status").textContent = `${recipeName(spare.recipe_id)}: in the freezer`;
        });
        el.querySelector(".took")?.after(btn);
      }
    },
  };
  return mealEl(day.day, m, opts);
}

// "Cooked it differently?": edit the grams, or say it was another dish, then tick
function cookedDifferently(day, m) {
  const box = document.createElement("details");
  box.className = "differently";
  box.innerHTML = `<summary>${m.recipe_id ? "Cooked it differently?" : "Made something anyway?"}</summary>
    <label class="field">Made instead
      <select class="dish"><option value="">${m.recipe_id ? esc(m.recipe_name) + " (as planned)" : "pick a dish"}</option>
      ${META.recipes.filter((r) => r.meal_type.includes(m.meal) && r.id !== m.recipe_id)
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join("")}</select></label>
    <div class="hint">What came out of the pantry (grams, or ml for liquids):</div>
    <div class="grams-list"></div>
    <div class="prow add-extra"><input type="text" list="all-ingredients" placeholder="something else used" class="more-name">
      <input type="text" placeholder="g or ml" class="more-g"><span></span><button class="more-add">Add</button></div>
    <button class="primary tick-instead">Tick off with these</button>`;
  // Staples you don't keep track of aren't worth listing
  const tracked = (grams) => Object.fromEntries(Object.entries(grams || {})
    .filter(([id]) => !(ingById(id) || {}).unlimited || pantry.some((r) => r.id === id)));
  let instead = { consumed: tracked(m.consumed) };
  const list = box.querySelector(".grams-list");
  const draw = () => {
    list.innerHTML = Object.entries(instead.consumed).map(([id, g]) => `
      <div class="grow"><span>${esc(ingName(id))}</span><input type="number" min="0" step="${unitOf(id) === "ml" ? 5 : 10}" data-id="${esc(id)}" value="${Math.round(toShown(id, g))}" aria-label="${unitOf(id)}"><span class="unit">${unitOf(id)}</span></div>`).join("")
      || `<div class="hint">Nothing from the pantry.</div>`;
    list.querySelectorAll("input").forEach((inp) => inp.addEventListener("input", () => {
      instead.consumed[inp.dataset.id] = fromShown(inp.dataset.id, Math.max(0, Number(inp.value) || 0));
    }));
  };
  draw();
  box.querySelector(".dish").addEventListener("change", async (e) => {
    const id = e.target.value;
    if (!id) { instead = { consumed: tracked(m.consumed) }; draw(); return; }
    try {
      const meal = await api(`/api/plans/${savedPlan.id}/preview`, { method: "POST", body: { recipe_id: id, date: today() } });
      instead = { recipe_id: id, consumed: tracked(meal.consumed), made: meal.made, servings: meal.servings };
      draw();
    } catch (err) { showError(err.message); }
  });
  box.querySelector(".more-add").addEventListener("click", () => {
    const found = ingByName(box.querySelector(".more-name").value);
    if (!found) { showError("Pick an ingredient from the list."); return; }
    instead.consumed[found.id] = fromShown(found.id, parseFloat(box.querySelector(".more-g").value) || 0);
    box.querySelector(".more-name").value = ""; box.querySelector(".more-g").value = "";
    draw();
  });
  box.querySelector(".tick-instead").addEventListener("click", async () => {
    if (!m.recipe_id && !instead.recipe_id) { showError("Pick the dish you made."); return; }
    try {
      await changePlan("cook", { day: day.day, meal: m.meal, cooked: true, instead },
        `${instead.recipe_id ? recipeName(instead.recipe_id) : m.recipe_name}: done, pantry updated`);
    } catch (err) { showError(err.message); }
  });
  return box;
}

async function changeMeal(what, body, done) {
  try {
    const data = await api(`/api/plans/${savedPlan.id}/${what}`, { method: "POST", body });
    savedPlan = data.plan;
    renderSaved();
    $("status").textContent = done;
  } catch (e) { showError(e.message); }
}

async function replan(body, done) {
  $("status").textContent = "re-planning…";
  document.body.classList.add("busy");
  try {
    await changePlan("replan", body, done);
  } catch (e) { showError(e.message); $("status").textContent = ""; } finally { document.body.classList.remove("busy"); }
}

// POST a change to the saved plan; it comes back with the pantry it changed
async function changePlan(what, body, done) {
  await flushPantry();
  const data = await api(`/api/plans/${savedPlan.id}/${what}`, {
    method: "POST", body: { date: today(), ...body },  // a past meal can carry its own date
  });
  savedPlan = data.plan;
  setPantry(data.pantry);
  renderPantry();
  markStale();
  savedPlans = (await api("/api/plans")).plans;
  renderPlanList();
  renderSaved();
  renderPantrySummary();
  $("status").textContent = done;
}

async function cookMeal(day, m, tick) {
  tick.disabled = true;
  try {
    await changePlan("cook", { day, meal: m.meal, cooked: tick.checked },
      tick.checked ? `${m.recipe_name}: done, pantry updated` : `${m.recipe_name}: put back`);
  } catch (e) {
    showError(e.message);
    tick.checked = !tick.checked;
    tick.disabled = false;
  }
}

const ingName = (id) => (ingById(id) || { name: id }).name;
const useByText = (d) => (d ? `use by ${prettyDay(d)}` : "keeps");

function renderSavedShopping() {
  const rows = savedPlan.plan.shopping || [];
  const extras = savedPlan.extras || [];
  const got = rows.filter((r) => r.bought).length;
  $("shop-count").textContent = rows.length ? `${got} of ${rows.length} bought` : "";
  const toBuy = rows.filter((r) => !r.bought);
  $("saved-shop").innerHTML = rows.length ? byAisle(rows, (r) => `
    <li class="tickable ${r.bought ? "got" : ""}">
      <input type="checkbox" data-id="${esc(r.id)}" data-bought="${r.bought ? 1 : ""}" ${r.bought ? "checked" : ""} aria-label="Bought ${esc(r.name)}">
      <span class="n">${r.packs}×</span>
      <span>${esc(r.name)} <span class="g">${esc(r.amount || `${r.grams}g`)}</span></span>
      <span class="price">${r.bought ? `in the pantry, ${esc(useByText(r.bought.use_by))}` : `${r.listed ? "top-up · " : ""}${esc(r.price_word || "")}`}</span></li>`)
      + (toBuy.length ? `<div class="shop-total"><button class="copy">Copy list</button> the ${plural(toBuy.length, "thing")} still to buy</div>` : "")
    : `<p class="hint">Nothing to buy on this plan.</p>`;
  const copy = $("saved-shop").querySelector("button.copy");
  if (copy) copy.addEventListener("click", () => copyList(`Shopping: ${savedPlan.name}`, toBuy, copy));
  $("saved-shop").querySelectorAll("input").forEach((box) => box.addEventListener("change", async () => {
    box.disabled = true;
    const name = ingName(box.dataset.id);
    try {
      await changePlan("shop", { id: box.dataset.id, bought: box.checked },
        box.checked ? `${name}: in the pantry` : `${name}: taken back out`);
    } catch (e) { showError(e.message); box.checked = !box.checked; box.disabled = false; }
  }));
  $("saved-extras").innerHTML = extras.length ? `<ul class="shop">${extras.map((x, i) => `
    <li><span>${esc(ingName(x.id))} <span class="g">${esc(x.amount)}</span></span>
      <span class="price">${esc(useByText(x.use_by))}</span>
      <button class="x" data-i="${i}" title="Take it back out">×</button></li>`).join("")}</ul>` : "";
  $("saved-extras").querySelectorAll("button").forEach((btn) => btn.addEventListener("click", () =>
    changePlan("shop", { extra_index: Number(btn.dataset.i) }, "taken back out")
      .catch((e) => showError(e.message))));
}

async function addExtra() {
  const found = ingByName($("extra-name").value);
  if (!found) { showError(`Don't know "${$("extra-name").value}". Pick one from the list.`); return; }
  const extra = { id: found.id, amount: $("extra-amount").value.trim(), use_by: $("extra-use-by").value || null };
  try {
    await changePlan("shop", { extra }, `${found.name}: in the pantry`);
    showError("");
    for (const id of ["extra-name", "extra-amount", "extra-use-by"]) $(id).value = "";
    $("extra-name").focus();
  } catch (e) { showError(e.message); }
}

async function renamePlan() {
  const title = $("saved-name");
  if (title.querySelector("input")) return;
  title.innerHTML = `<input type="text" maxlength="80" value="${esc(savedPlan.name)}" aria-label="Plan name">`;
  const input = title.querySelector("input");
  input.focus(); input.select();
  const finish = async (keep) => {
    if (!title.contains(input)) return;
    const name = input.value.trim();
    if (keep && name && name !== savedPlan.name) {
      await changeMeal("rename", { name }, "renamed");
      savedPlans = (await api("/api/plans")).plans;
      renderPlanList();
    } else title.textContent = savedPlan.name;
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

async function deletePlan() {
  if (!savedPlan) return;
  const btn = $("saved-delete");
  if (btn.dataset.armed !== "1") {  // two clicks, no browser dialog
    btn.dataset.armed = "1"; btn.textContent = "Really delete?";
    setTimeout(() => { btn.dataset.armed = ""; btn.textContent = "Delete"; }, 3000);
    return;
  }
  btn.dataset.armed = ""; btn.textContent = "Delete";
  await api(`/api/plans/${savedPlan.id}`, { method: "DELETE" });
  savedPlan = null;
  if (location.hash === "#plans") await showPlans();
  else location.hash = "#plans";
}

// Back on the tab (or every ten minutes): pick up a new day, and pantry changes
// made on another device
async function catchUp() {
  if (today() !== shownDay) {
    shownDay = today();
    if (state.start_date < today()) { state.start_date = today(); renderKnobs(); save(); markStale(); }
    renderPantry();
    renderPantrySummary();
    if (savedPlan) renderSaved();
  }
  if (pantryTimer || document.activeElement?.closest?.("#areas")) return;  // mid-edit
  try {
    const data = await api("/api/pantry");
    if (data.version !== pantryVersion) { setPantry(data); renderPantry(); renderPantrySummary(); markStale(); }
    savedPlans = (await api("/api/plans")).plans;
    renderPantrySummary();
    if (location.hash.startsWith("#plans")) route();
  } catch (e) { /* offline: try next time */ }
}

// ------------------------------------------------------------------ boot
async function boot() {
  META = await (await fetch("/api/meta")).json();
  state = load();
  await loadPantry();
  await loadWaste();
  notKept = (await api("/api/household")).not_kept;
  savedPlans = (await api("/api/plans")).plans;
  shownDay = today();
  renderKnobs();
  renderPantry();
  restorePlan();
  renderPinsNote();
  route();
  window.addEventListener("hashchange", route);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) catchUp(); });
  setInterval(catchUp, 10 * 60 * 1000);

  for (const id of ["people", "days"]) {
    const box = $(id);
    box.addEventListener("input", () => {
      const n = Number(box.value);
      if (box.value === "" || !Number.isFinite(n)) return;  // still typing
      state[id] = Math.min(Number(box.max), Math.max(1, Math.round(n)));
      changed();
    });
    box.addEventListener("change", () => { box.value = state[id]; });
  }
  $("start_date").addEventListener("change", (e) => { state.start_date = e.target.value || today(); changed(); });
  for (const id of ["weeknight", "weekend", "handson", "effort"]) {
    $(id).addEventListener("input", (e) => { state[id] = Number(e.target.value); knobLabels(); changed(); });
  }
  $("shopping").addEventListener("change", (e) => { state.shopping = e.target.checked; changed(); });
  $("reshuffle").addEventListener("click", () => { state.seed = Math.floor(Math.random() * 1e9); changed(); plan(); });
  $("reset-weights").addEventListener("click", () => { state.weights = { ...META.weights }; renderKnobs(); changed(); });
  $("plan-btn").addEventListener("click", plan);
  $("save-btn").addEventListener("click", savePlan);
  $("copy-week").addEventListener("click", () => lastResult
    && copyText(weekText("This week", lastResult.plan.days), $("copy-week"), "Copy the week"));
  $("saved-copy").addEventListener("click", () => savedPlan
    && copyText(weekText(savedPlan.name, savedPlan.plan.days), $("saved-copy"), "Copy the week"));
  $("extra-add").addEventListener("click", addExtra);
  $("bulk-add").addEventListener("click", bulkAdd);
  $("bulk").addEventListener("toggle", () => { if ($("bulk").open) renderBulk(); });
  $("extra-amount").addEventListener("keydown", (e) => { if (e.key === "Enter") addExtra(); });
  $("all-ingredients").innerHTML = META.ingredients.filter((i) => !i.unlimited)
    .map((i) => `<option value="${esc(i.name)}">`).join("");
  $("saved-delete").addEventListener("click", () => deletePlan().catch((e) => showError(e.message)));
  $("replan-btn").addEventListener("click", () => replan(
    { search: true, shopping: $("replan-shop").checked }, "rest of the week re-planned from the pantry"));
  $("saved-rename").addEventListener("click", renamePlan);
  $("status").textContent = "";
}
boot().catch((e) => showError(`Couldn't reach the planner: ${e.message}`));
