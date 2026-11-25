// Garde Manger dashboard. Vanilla JS, no build step.
const $ = (id) => document.getElementById(id);
const STORE = "garde.state.v2";
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
let ratings = {};          // "day-meal" -> +1/-1 for the shown plan
let running = null;        // AbortController while a search runs
let hasPlan = false;
let pantryTimer = null, pantrySaving = Promise.resolve();

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const ingById = (id) => META.ingredients.find((i) => i.id === id);
const recipeName = (id) => (META.recipes.find((r) => r.id === id) || { name: id }).name;
const ingByName = (name) => {
  const n = name.trim().toLowerCase();
  return META.ingredients.find((i) => i.name.toLowerCase() === n || i.id === n);
};

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
    weeknight: 45, weekend: NO_LIMIT, weights: { ...META.weights }, effort: 3, seed: 0,
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
      <div class="area-head"><h2>${title}</h2><span class="count">${plural(rows.filter((r) => r.id).length, "thing")}</span></div>
      <div class="prow head"><span>Ingredient</span><span>Amount</span><span>Use by</span><span></span><span></span></div>
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
  renderDishes();
  $("staples").innerHTML = META.ingredients.filter((i) => i.unlimited)
    .map((i) => `<span class="chip">${esc(i.name)}</span>`).join("");
}

function renderDishes() {
  $("dishes-count").textContent = plural(dishes.length, "dish").replace("dishs", "dishes");
  if (!dishes.length) {
    $("dishes").innerHTML = `<div class="hint">Nothing cooked waiting. Tick a meal off on a saved plan and spare portions land here.</div>`;
    return;
  }
  $("dishes").innerHTML = `<div class="drow prow head"><span>Dish</span><span>Portions</span><span>Use by</span><span></span><span></span></div>`;
  for (const dish of dishes) {
    const el = document.createElement("div");
    el.className = "drow";
    el.innerHTML = `
      <span class="dname" title="${dish.cooked ? `cooked ${esc(prettyDay(dish.cooked))}` : ""}">${esc(recipeName(dish.recipe_id))}</span>
      <input type="number" min="0" step="0.5" value="${esc(dish.portions)}" aria-label="Portions">
      <input type="date" value="${esc(dish.use_by)}" aria-label="Use by">
      ${badge(dish.use_by)}
      <button class="x" title="Eaten or binned">×</button>`;
    const [portions, useBy] = el.querySelectorAll("input");
    portions.addEventListener("input", () => { dish.portions = Number(portions.value) || 0; pantryChanged(); });
    useBy.addEventListener("change", () => {
      if (!useBy.value) return;
      dish.use_by = useBy.value; renderPantry(); pantryChanged();
    });
    el.querySelector("button").addEventListener("click", () => {
      dishes.splice(dishes.indexOf(dish), 1); renderPantry(); pantryChanged();
    });
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
  el.innerHTML = `
    <input type="text" list="dl-${area}" value="${esc(ing ? ing.name : row.id)}" aria-label="Ingredient">
    <input type="text" value="${esc(row.amount)}" aria-label="Amount"
      placeholder="${ing && ing.unit_weight ? "count" : "grams"}">
    <input type="date" value="${esc(row.use_by)}" aria-label="Use by">
    ${badge(row.use_by)}
    <button class="x" title="Remove">×</button>`;
  const [name, amount, useBy] = el.querySelectorAll("input");
  name.addEventListener("change", () => {
    const found = ingByName(name.value);
    name.style.outline = found ? "" : "2px solid var(--warn)";
    if (!found) return;
    row.id = found.id;
    if (!row.use_by) row.use_by = defaultUseBy(found);
    row.area = found.storage || area;
    renderPantry(); pantryChanged();
  });
  amount.addEventListener("input", () => { row.amount = amount.value; pantryChanged(); });
  useBy.addEventListener("change", () => { row.use_by = useBy.value; renderPantry(); pantryChanged(); });
  el.querySelector("button").addEventListener("click", () => {
    pantry.splice(pantry.indexOf(row), 1); renderPantry(); pantryChanged();
  });
  return el;
}

function pantryChanged() {
  markStale();
  clearTimeout(pantryTimer);
  pantryTimer = setTimeout(savePantry, 500);
}

function savePantry() {
  clearTimeout(pantryTimer);
  pantryTimer = null;
  const items = pantry.filter((r) => r.id && String(r.amount).trim())
    .map((r) => ({ id: r.id, amount: String(r.amount).trim(), use_by: r.use_by || null }));
  pantrySaving = fetch("/api/pantry", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, dishes: dishes.filter((d) => d.portions > 0) }),
  }).then(async (res) => {
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    showError("");
    $("status").textContent = "pantry saved";
  }).catch((e) => showError(`Pantry not saved: ${e.message}`));
  return pantrySaving;
}

async function flushPantry() {
  if (pantryTimer) await savePantry();
  else await pantrySaving;
}

function setPantry(data) {
  pantry = data.items.map((i) => ({ ...i, use_by: i.use_by || "", area: areaOf(i.id) }));
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
  $("effort-val").textContent = EFFORT_WORDS[state.effort];
  for (const k of Object.keys(META.weight_scale)) {
    $(`w-${k}`).title = `weight ${Number(state.weights[k]).toFixed(1)} (default ${META.weights[k]})`;
  }
}

function changed() { save(); markStale(); }

function markStale() {
  if (hasPlan) document.querySelector("main").classList.add("is-stale");
}

// ------------------------------------------------------------------ planning
function requestBody() {
  const limit = (v) => (Number(v) >= NO_LIMIT ? 0 : Number(v));
  return {
    people: state.people, days: state.days, meals: state.meals, start_date: state.start_date,
    weeknight_minutes: limit(state.weeknight), weekend_minutes: limit(state.weekend),
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

async function plan() {
  if (running) { running.abort(); return; }
  if (!state.meals.length) { showError("Pick at least one meal."); return; }
  showError("");
  await flushPantry();
  running = new AbortController();
  const body = requestBody();
  setRunning(true);
  progress({ done: 0, total: 1 }, "Starting…");
  try {
    const res = await fetch("/api/plan/stream", {
      method: "POST", signal: running.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    await readEvents(res.body, {
      progress: (d) => progress(d),
      result: (d) => {
        lastBody = body;
        render(d);
        progress({ done: 1, total: 1 }, `Done: ${d.evaluations.toLocaleString()} different weeks tried.`);
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

function render(data) {
  hasPlan = true;
  lastResult = data;
  $("save-btn").disabled = false;
  $("save-btn").textContent = "Save plan";
  document.querySelector("main").classList.remove("is-stale");
  ratings = {};
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
      render(lastResult);
    },
    onSkip: (m, on) => {
      if (on) state.pins[pinKey(day.date, m.meal)] = "~skip";
      else delete state.pins[pinKey(day.date, m.meal)];
      save(); plan();
    },
    options: (m) => api("/api/plan/options", {
      method: "POST",
      body: { ...lastBody, choice: choiceOf(lastResult), day: day.day, meal: m.meal },
    }),
    onPick: (m, pick) => { state.pins[pinKey(day.date, m.meal)] = pick; save(); plan(); },
  };
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

// One meal. opts (all optional): key (for ratings), rating, onRate, locked(m),
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
    el.innerHTML = `<div class="kind">${m.meal}</div>
      <div class="title"><span class="name">${esc(m.recipe_name)}</span>
      <span class="chip">leftovers from ${esc(from)}</span></div>`;
  } else {
    const slots = m.slots.map((s) => `<li><span class="slot" title="${esc(s.label)}">${esc(s.label)}</span>
      <span>${s.items.map((i) => `${esc(i.name)}${i.grams ? ` <span class="grams">${Math.round(i.grams)}g</span>` : ""}`).join(", ")}</span></li>`).join("");
    el.innerHTML = `
      <div class="kind">${m.meal}</div>
      <div class="title"><span class="name">${esc(m.recipe_name)}</span>
        <span class="chip">${m.minutes} min · ${m.active_minutes} hands-on</span></div>
      ${m.make_ahead.length ? `<div class="ahead">⏰ Make ahead: ${m.make_ahead.map(esc).join(", ")}</div>` : ""}
      <ul class="slots">${slots}</ul>
      ${howTo(m)}
      <div class="rate">
        <span class="ask">How was it?</span>
        <button data-r="1" title="Cooked it, liked it">👍</button>
        <button data-r="-1" title="Cooked it, not again" class="down">👎</button>
      </div>`;
    const key = `${opts.key || ""}${day}-${m.meal}`;
    const given = opts.rating !== undefined ? opts.rating : ratings[key];
    el.querySelectorAll(".rate button").forEach((btn) => {
      if (given === Number(btn.dataset.r)) btn.classList.add("on");
      btn.addEventListener("click", () => rate(key, m, Number(btn.dataset.r), el, opts.onRate));
    });
    if (given !== undefined && given !== null) el.querySelector(".rate").classList.add("rated");
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

async function rate(key, m, rating, el, onRate) {
  if (el.querySelector(".rate").classList.contains("rated")) return;  // one vote per meal per plan
  const res = await fetch("/api/rate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recipe_id: m.recipe_id, ingredients: m.ingredients, rating, ...(onRate ? onRate() : {}) }),
  });
  const data = await res.json();
  if (!res.ok) { showError(data.error); return; }
  ratings[key] = rating;
  if (onRate && savedPlan) {
    const meal = savedPlan.plan.days.flatMap((d) => d.meals).find((x) => x === m);
    if (meal) meal.rating = rating;
  }
  el.querySelector(".rate").classList.add("rated");
  el.querySelector(`.rate button[data-r="${rating}"]`).classList.add("on");
  $("status").textContent = `saved: ${m.recipe_name} now ${data.recipe_score >= 0 ? "+" : ""}${data.recipe_score.toFixed(2)}`;
}

const PRICE_DOTS = (n) => "£".repeat(n) || "free";

function renderShopping(items) {
  $("shop-panel").style.display = state.shopping ? "block" : "none";
  if (!state.shopping) return;
  if (!items.length) { $("shop").innerHTML = `<div class="empty">Nothing to buy. The pantry covers it.</div>`; return; }
  const total = items.reduce((t, i) => t + i.price * i.packs, 0);
  $("shop").innerHTML = `<ul class="shop">${items.map((i) => `
    <li><span class="n">${i.packs}×</span>
      <span>${esc(i.name)} <span class="g">${i.grams}g</span></span>
      <span class="price" title="${esc(i.price_word)}"><b>${PRICE_DOTS(i.price)}</b> ${esc(i.price_word)}</span></li>`).join("")}</ul>
    <div class="shop-total">${items.length} things · ${total} price points</div>`;
}

function renderStock(stock, plan) {
  if (!stock.length) { $("stock").innerHTML = `<div class="empty">Nothing tracked. Add things to the pantry.</div>`; return; }
  $("stock").innerHTML = `<table>
    <tr><th>Ingredient</th><th>Had</th><th>Bought / made</th><th>Used</th><th>Left</th><th>Wasted</th><th></th></tr>
    ${stock.map((s) => `<tr>
      <td>${esc(s.name)}</td><td>${s.start}g</td><td>${s.bought ? s.bought + "g" : s.made ? s.made + "g made" : "–"}</td><td>${s.used}g</td><td>${s.left}g</td>
      <td class="${s.wasted ? "waste" : ""}">${s.wasted ? s.wasted + "g" : "–"}</td>
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
  try {
    const saved = await api("/api/plans", { method: "POST", body: { result: lastResult, settings: lastBody } });
    $("save-btn").disabled = true;
    $("save-btn").textContent = "Saved ✓";
    $("status").textContent = `saved: ${saved.name}`;
    location.hash = `#plans/${saved.id}`;
  } catch (e) { showError(`Plan not saved: ${e.message}`); }
}

async function showPlans(planId) {
  savedPlans = (await api("/api/plans")).plans;
  const id = planId || (savedPlans[0] && savedPlans[0].id);
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
    key: `${savedPlan.id}-`,
    rating: m.rating,
    onRate: () => ({ plan_id: savedPlan.id, day: day.day, meal: m.meal }),
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
      if (m.cooked && m.rating == null && m.leftovers_from == null) el.querySelector(".rate")?.classList.add("asking");
      const leftovers = m.leftovers_from != null;
      const box = document.createElement("div");
      box.innerHTML = `<label class="cook"><input type="checkbox" ${m.cooked ? "checked" : ""}>
          ${leftovers ? "Eaten" : "Cooked"}${m.cooked ? ` <span class="chip">${esc(prettyDay(m.cooked.on))}</span>` : ""}</label>
        ${m.cooked ? `<div class="took">${esc(tookText(m.cooked))}</div>` : ""}`;
      el.insertBefore(box, el.querySelector(".kind").nextSibling);
      const tick = box.querySelector("input");
      tick.addEventListener("change", () => cookMeal(day.day, m, tick));
      if (!m.cooked && !leftovers) el.appendChild(cookedDifferently(day, m));
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
    <div class="hint">What came out of the pantry, in grams:</div>
    <div class="grams-list"></div>
    <div class="prow add-extra"><input type="text" list="all-ingredients" placeholder="something else used" class="more-name">
      <input type="text" placeholder="grams" class="more-g"><span></span><button class="more-add">Add</button></div>
    <button class="primary tick-instead">Tick off with these</button>`;
  // Staples you don't keep track of aren't worth listing
  const tracked = (grams) => Object.fromEntries(Object.entries(grams || {})
    .filter(([id]) => !(ingById(id) || {}).unlimited || pantry.some((r) => r.id === id)));
  let instead = { consumed: tracked(m.consumed) };
  const list = box.querySelector(".grams-list");
  const draw = () => {
    list.innerHTML = Object.entries(instead.consumed).map(([id, g]) => `
      <div class="grow"><span>${esc(ingName(id))}</span><input type="number" min="0" step="10" data-id="${esc(id)}" value="${Math.round(g)}"></div>`).join("")
      || `<div class="hint">Nothing from the pantry.</div>`;
    list.querySelectorAll("input").forEach((inp) => inp.addEventListener("input", () => {
      instead.consumed[inp.dataset.id] = Math.max(0, Number(inp.value) || 0);
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
    instead.consumed[found.id] = Number(box.querySelector(".more-g").value) || 0;
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
    method: "POST", body: { date: today(), ...body },
  });
  savedPlan = data.plan;
  setPantry(data.pantry);
  renderPantry();
  markStale();
  savedPlans = (await api("/api/plans")).plans;
  renderPlanList();
  renderSaved();
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
  $("saved-shop").innerHTML = rows.length ? `<ul class="shop">${rows.map((r) => `
    <li class="tickable ${r.bought ? "got" : ""}">
      <input type="checkbox" data-id="${esc(r.id)}" ${r.bought ? "checked" : ""} aria-label="Bought ${esc(r.name)}">
      <span class="n">${r.packs}×</span>
      <span>${esc(r.name)} <span class="g">${r.grams}g</span></span>
      <span class="price">${r.bought ? `in the pantry, ${esc(useByText(r.bought.use_by))}` : esc(r.price_word || "")}</span></li>`).join("")}</ul>`
    : `<p class="hint">Nothing to buy on this plan.</p>`;
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

// ------------------------------------------------------------------ boot
async function boot() {
  META = await (await fetch("/api/meta")).json();
  state = load();
  await loadPantry();
  renderKnobs();
  renderPantry();
  route();
  window.addEventListener("hashchange", route);

  for (const id of ["people", "days"]) {
    $(id).addEventListener("input", (e) => { state[id] = Number(e.target.value) || 1; changed(); });
  }
  $("start_date").addEventListener("change", (e) => { state.start_date = e.target.value || today(); changed(); });
  for (const id of ["weeknight", "weekend", "effort"]) {
    $(id).addEventListener("input", (e) => { state[id] = Number(e.target.value); knobLabels(); changed(); });
  }
  $("shopping").addEventListener("change", (e) => { state.shopping = e.target.checked; changed(); });
  $("reshuffle").addEventListener("click", () => { state.seed = Math.floor(Math.random() * 1e9); changed(); plan(); });
  $("reset-weights").addEventListener("click", () => { state.weights = { ...META.weights }; renderKnobs(); changed(); });
  $("plan-btn").addEventListener("click", plan);
  $("save-btn").addEventListener("click", savePlan);
  $("extra-add").addEventListener("click", addExtra);
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
