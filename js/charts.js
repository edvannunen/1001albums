import { state } from "./state.js";
import { decadeOf, genreList } from "./data.js";
import { classifyGenre, GENRE_COLORS } from "./taxonomy.js";
import { normalizeCountry } from "./taxonomy.js";
import { setFilter, clearFilter } from "./filters.js";
import { escapeHtml } from "./grid.js";

let decadeChartInstance, countryChartInstance;

// Fixed hue per decade (by absolute decade, not by rank) so a decade keeps
// its color as new decades get added over time. Drawn from the mood-board's
// "COLOR PALETTE (FROM CHARTS)" swatch set.
const WIDGET_PALETTE = ["#c0392b","#2e6a71","#d4a62a","#5f7f4f","#6e4c7b","#c8b08a","#3e6b4a","#d97a2b","#3f8ca2","#8e3b3b"];
function decadeColor(startYear){
  const idx = Math.floor((startYear - 1950) / 10);
  return WIDGET_PALETTE[((idx % WIDGET_PALETTE.length) + WIDGET_PALETTE.length) % WIDGET_PALETTE.length];
}

// Volume-knob dial: numbered 1 (bottom-left) through 11 (bottom-right), a
// Spinal Tap reference. Reading the dial art, 0% points at "1" (-150° from
// 12 o'clock), 50% points straight up at "6" (0°), 100% points at "11"
// (+150°) — a 300° sweep leaving a 60° dead zone at the bottom, matching the
// small dot marker printed on the knob face between "1" and "11".
function progressAngle(pct){
  return -150 + (pct / 100) * 300;
}

// The knob turn (CSS transition on .knob-line) and the "%" count-up share
// one duration/easing so they finish together — duration lives in the
// --progress-duration CSS custom property (single source of truth) and the
// easing here is a JS approximation of the CSS transition's `ease-out`.
let lastAnimatedPct = null;
function animatePercent(el, to){
  if(lastAnimatedPct === to) return; // value hasn't changed — a re-render
                                      // from an unrelated filter click
                                      // shouldn't replay the count-up.
  const from = lastAnimatedPct == null ? 0 : lastAnimatedPct;
  lastAnimatedPct = to;
  const durationMs = parseFloat(getComputedStyle(document.querySelector(".knob")).getPropertyValue("--progress-duration")) || 1800;
  const start = performance.now();
  function tick(now){
    const t = Math.min(1, (now - start) / durationMs);
    const eased = 1 - Math.pow(1 - t, 3); // cubic ease-out, matches the CSS
    el.textContent = Math.round(from + (to - from) * eased) + "%";
    if(t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function currentProgressPct(){
  return Math.min(100, Math.round((state.albums.length / 1001) * 100));
}

// `onChange` is called after any chart-driven filter mutation so the caller
// (app.js) can re-render the list/chips without this module importing app.js.
export function renderDashboard(onChange){
  const total = state.albums.length;
  const pct = currentProgressPct();
  document.getElementById("progressLine").style.transform =
    `translateX(-50%) rotate(${progressAngle(pct)}deg)`;
  animatePercent(document.getElementById("progressLabel"), pct);
  document.getElementById("doneCount").textContent = total;

  renderDecadeChart(onChange);
  renderGenreBubbles(onChange);
  renderCountryChart(onChange);
}

// Clicking the knob replays the turn + count-up from scratch, even though
// the target value hasn't changed — the CSS transition and animatePercent's
// guard both only fire on a value change, so the pointer is snapped back to
// its 0% rest position (transition disabled, then reflowed) before being
// re-animated to the current value.
export function replayProgressAnimation(){
  const line = document.getElementById("progressLine");
  line.style.transition = "none";
  line.style.transform = "translateX(-50%) rotate(-150deg)";
  void line.offsetWidth; // force reflow so the reset above actually commits
  line.style.transition = "";
  line.style.transform = `translateX(-50%) rotate(${progressAngle(currentProgressPct())}deg)`;
  lastAnimatedPct = null;
  animatePercent(document.getElementById("progressLabel"), currentProgressPct());
}

function renderDecadeChart(onChange){
  const labelEl = document.getElementById("decadeLabel");
  const canvas = document.getElementById("decadeChart");

  if(state.decadeDrill){
    const decade = state.decadeDrill;
    labelEl.innerHTML = `<span class="disc"></span>${decade}<button class="dash-back" id="decadeBack">← Decennia</button>`;
    document.getElementById("decadeBack").addEventListener("click", ()=>{
      clearFilter("decade");
      onChange();
    });

    const startYear = parseInt(decade);
    const counts = {};
    for(let y = startYear; y < startYear + 10; y++) counts[y] = 0;
    state.albums.forEach(a=>{
      const y = parseInt(a.year);
      if(y >= startYear && y < startYear + 10) counts[y] = (counts[y]||0) + 1;
    });
    const yearLabels = Object.keys(counts);
    const yearValues = Object.values(counts);
    const color = decadeColor(startYear);

    if(decadeChartInstance) decadeChartInstance.destroy();
    decadeChartInstance = new Chart(canvas, {
      type:"bar",
      data:{labels:yearLabels, datasets:[{
        data:yearValues,
        backgroundColor:yearLabels.map(y=> state.filters.year===parseInt(y) ? "#2a2620" : color),
        borderRadius:2,
      }]},
      options:{
        onClick:(evt, elements)=>{
          if(!elements.length) return;
          setFilter("year", parseInt(yearLabels[elements[0].index]));
          onChange();
        },
        plugins:{legend:{display:false}},
        scales:{
          x:{ticks:{color:"#7a6f5c",font:{family:"Space Mono",size:10}},grid:{display:false}},
          y:{ticks:{color:"#7a6f5c",font:{family:"Space Mono",size:10}},grid:{color:"rgba(0,0,0,0.08)"}}
        },
        onHover:(evt, elements)=>{ evt.native.target.style.cursor = elements.length ? "pointer" : "default"; }
      }
    });
    return;
  }

  labelEl.innerHTML = `<span class="disc"></span>Albums per decennium`;
  const decadeCounts = {};
  state.albums.forEach(a=>{
    const d = decadeOf(a);
    if(!d) return;
    decadeCounts[d] = (decadeCounts[d]||0) + 1;
  });
  const decadeLabels = Object.keys(decadeCounts).sort();
  const decadeValues = decadeLabels.map(d => decadeCounts[d]);

  if(decadeChartInstance) decadeChartInstance.destroy();
  decadeChartInstance = new Chart(canvas, {
    type:"bar",
    data:{labels:decadeLabels, datasets:[{
      data:decadeValues,
      backgroundColor:decadeLabels.map(d=> state.filters.decade===d ? "#2a2620" : decadeColor(parseInt(d))),
      borderRadius:2,
    }]},
    options:{
      onClick:(evt, elements)=>{
        if(!elements.length) return;
        const decade = decadeLabels[elements[0].index];
        state.decadeDrill = decade;
        setFilter("decade", decade);
        onChange();
      },
      plugins:{legend:{display:false}},
      scales:{
        x:{ticks:{color:"#7a6f5c",font:{family:"Space Mono",size:10}},grid:{display:false}},
        y:{ticks:{color:"#7a6f5c",font:{family:"Space Mono",size:10}},grid:{color:"rgba(0,0,0,0.08)"}}
      },
      onHover:(evt, elements)=>{ evt.native.target.style.cursor = elements.length ? "pointer" : "default"; }
    }
  });
}

function renderGenreBubbles(onChange){
  const labelEl = document.getElementById("genreLabel");
  const container = document.getElementById("genreBubbles");
  const width = container.clientWidth || 260;
  // Was hardcoded to 180 while the box (.bubble-wrap) is 260px tall — the
  // pack layout only ever filled the top ~70% of the visible box. Read the
  // real box height so the circles pack out to the space that's there.
  const height = container.clientHeight || 260;

  let data, colorFor, onBubbleClick, isActive;

  if(state.genreDrill){
    const macro = state.genreDrill;
    labelEl.innerHTML = `<span class="disc"></span>${macro}<button class="dash-back" id="genreBack">← Genres</button>`;
    document.getElementById("genreBack").addEventListener("click", ()=>{
      clearFilter("genreMacro");
      onChange();
    });

    const tagCounts = {};
    state.albums.forEach(a=>{
      genreList(a).forEach(g=>{
        if(classifyGenre(g) === macro) tagCounts[g] = (tagCounts[g]||0) + 1;
      });
    });
    data = Object.entries(tagCounts).map(([name, value])=>({name, value}));
    colorFor = ()=> GENRE_COLORS[macro];
    onBubbleClick = (d)=>{ setFilter("genre", d.name); onChange(); };
    isActive = (d)=> state.filters.genre === d.name;
  } else {
    labelEl.innerHTML = `<span class="disc"></span>Top genres`;
    const macroCounts = {};
    state.albums.forEach(a=>{
      const macros = new Set(genreList(a).map(classifyGenre).filter(Boolean));
      macros.forEach(m => macroCounts[m] = (macroCounts[m]||0) + 1);
    });
    data = Object.entries(macroCounts).map(([name, value])=>({name, value}));
    colorFor = (d)=> GENRE_COLORS[d.name];
    onBubbleClick = (d)=>{
      state.genreDrill = d.name;
      setFilter("genreMacro", d.name);
      onChange();
    };
    isActive = (d)=> state.filters.genreMacro === d.name;
  }

  container.innerHTML = "";
  if(!data.length) return;

  // RYM-style "bold genres only": show at most the 12 most common bubbles
  // per level, dropping the long tail rather than dumping dozens of tiny,
  // unreadable micro-tags — no "Overige" fold-in bucket, same "just don't
  // show it" rule as the top-level taxonomy's dropped "Other" catch-all.
  data = capBubbles(data);

  // Size floor so the smallest bubble is never illegibly tiny relative to
  // the largest — trades exact proportionality for legibility, which is the
  // right call when every bubble carries a text label.
  const maxValue = Math.max(...data.map(d=>d.value));
  const sizedData = data.map(d => ({...d, sizeValue: Math.max(d.value, maxValue*0.15)}));

  const root = d3.hierarchy({children:sizedData}).sum(d=>d.sizeValue);
  d3.pack().size([width, height]).padding(6)(root);

  root.leaves().forEach(leaf=>{
    const el = document.createElement("div");
    el.className = "bubble" + (isActive(leaf.data) ? " active-filter" : "");
    el.style.width = (leaf.r*2) + "px";
    el.style.height = (leaf.r*2) + "px";
    el.style.left = (leaf.x - leaf.r) + "px";
    el.style.top = (leaf.y - leaf.r) + "px";
    el.style.background = colorFor(leaf.data);
    el.style.fontSize = Math.max(10, Math.min(14, leaf.r/2.8)) + "px";
    el.title = `${leaf.data.name} (${leaf.data.value})`;
    el.innerHTML = `<div><div class="bubble-name">${escapeHtml(leaf.data.name)}</div><div class="bubble-count">${leaf.data.value}</div></div>`;
    el.addEventListener("click", ()=> onBubbleClick(leaf.data));
    container.appendChild(el);
  });
}

const MAX_BUBBLES = 12;

function capBubbles(entries){
  return entries.slice().sort((a,b)=> b.value - a.value).slice(0, MAX_BUBBLES);
}

function renderCountryChart(onChange){
  const labelEl = document.getElementById("countryLabel");
  const canvas = document.getElementById("countryChart");

  labelEl.innerHTML = `<span class="disc"></span>Landen` +
    `<button class="dash-back" id="usUkToggle">${state.hideUsUk ? "US &amp; UK tonen" : "US &amp; UK verbergen"}</button>`;
  document.getElementById("usUkToggle").addEventListener("click", ()=>{
    state.hideUsUk = !state.hideUsUk;
    onChange();
  });

  const countryCounts = {};
  state.albums.forEach(a=>{
    const c = normalizeCountry(a.musicbrainz && a.musicbrainz.country);
    if(c) countryCounts[c] = (countryCounts[c]||0) + 1;
  });
  if(state.hideUsUk){
    delete countryCounts["United States"];
    delete countryCounts["United Kingdom"];
  }
  const sorted = Object.entries(countryCounts).sort((a,b)=> b[1]-a[1]);
  const top = sorted.slice(0, 12);
  const labels = top.map(([c])=>c);
  const values = top.map(([,n])=>n);

  if(countryChartInstance) countryChartInstance.destroy();
  countryChartInstance = new Chart(canvas, {
    type:"bar",
    data:{labels, datasets:[{
      data:values,
      backgroundColor:labels.map(c=> state.filters.country===c ? "#2a2620" : "#2e6a71"),
      borderRadius:2,
    }]},
    options:{
      indexAxis:"y",
      maintainAspectRatio:false,
      onClick:(evt, elements)=>{
        if(!elements.length) return;
        setFilter("country", labels[elements[0].index]);
        onChange();
      },
      plugins:{legend:{display:false}},
      scales:{
        x:{ticks:{color:"#7a6f5c",font:{family:"Space Mono",size:10}},grid:{color:"rgba(0,0,0,0.08)"}},
        // autoSkip defaults to true and was silently dropping every other
        // country label to avoid overlap — force every label to render since
        // there are only 12 and the panel now has the height to fit them.
        y:{ticks:{color:"#7a6f5c",font:{family:"Space Mono",size:9},autoSkip:false},grid:{display:false}}
      },
      onHover:(evt, elements)=>{ evt.native.target.style.cursor = elements.length ? "pointer" : "default"; }
    }
  });
}

export function resizeGenreBubbles(onChange){
  if(document.getElementById("genreBubbles")) renderGenreBubbles(onChange);
}
