import { state } from "./state.js";
import { decadeOf, genreList } from "./data.js";
import { classifyGenre, GENRE_COLORS } from "./taxonomy.js";
import { normalizeCountry } from "./taxonomy.js";
import { setFilter } from "./filters.js";
import { escapeHtml } from "./grid.js";

let decadeChartInstance, countryChartInstance;

// `onChange` is called after any chart-driven filter mutation so the caller
// (app.js) can re-render the list/chips without this module importing app.js.
export function renderDashboard(onChange){
  const total = state.albums.length;
  const pct = Math.min(100, Math.round((total / 1001) * 100));
  const circumference = 238.76;
  const offset = circumference - (circumference * pct / 100);
  document.getElementById("progressRing").setAttribute("stroke-dashoffset", offset);
  document.getElementById("progressLabel").textContent = pct + "%";
  document.getElementById("doneCount").textContent = total;

  renderDecadeChart(onChange);
  renderGenreBubbles(onChange);
  renderCountryChart(onChange);
}

function renderDecadeChart(onChange){
  const labelEl = document.getElementById("decadeLabel");
  const canvas = document.getElementById("decadeChart");

  if(state.decadeDrill){
    const decade = state.decadeDrill;
    labelEl.innerHTML = `<span class="disc"></span>${decade}<button class="dash-back" id="decadeBack">← Decennia</button>`;
    document.getElementById("decadeBack").addEventListener("click", ()=>{
      state.decadeDrill = null;
      renderDecadeChart(onChange);
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

    if(decadeChartInstance) decadeChartInstance.destroy();
    decadeChartInstance = new Chart(canvas, {
      type:"bar",
      data:{labels:yearLabels, datasets:[{
        data:yearValues,
        backgroundColor:yearLabels.map(y=> state.filters.year===parseInt(y) ? "#ffffff" : "#d1a02c"),
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
          x:{ticks:{color:"#948b79",font:{family:"Space Mono",size:10}},grid:{display:false}},
          y:{ticks:{color:"#948b79",font:{family:"Space Mono",size:10}},grid:{color:"#3a352c"}}
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
    data:{labels:decadeLabels, datasets:[{data:decadeValues, backgroundColor:"#3b7a70", borderRadius:2}]},
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
        x:{ticks:{color:"#948b79",font:{family:"Space Mono",size:10}},grid:{display:false}},
        y:{ticks:{color:"#948b79",font:{family:"Space Mono",size:10}},grid:{color:"#3a352c"}}
      },
      onHover:(evt, elements)=>{ evt.native.target.style.cursor = elements.length ? "pointer" : "default"; }
    }
  });
}

function renderGenreBubbles(onChange){
  const labelEl = document.getElementById("genreLabel");
  const container = document.getElementById("genreBubbles");
  const width = container.clientWidth || 260;
  const height = 180;

  let data, colorFor, onBubbleClick, isActive;

  if(state.genreDrill){
    const macro = state.genreDrill;
    labelEl.innerHTML = `<span class="disc"></span>${macro}<button class="dash-back" id="genreBack">← Genres</button>`;
    document.getElementById("genreBack").addEventListener("click", ()=>{
      state.genreDrill = null;
      renderGenreBubbles(onChange);
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
    labelEl.innerHTML = `<span class="disc"></span>Genres`;
    const macroCounts = {};
    state.albums.forEach(a=>{
      const macros = new Set(genreList(a).map(classifyGenre).filter(Boolean));
      macros.forEach(m => macroCounts[m] = (macroCounts[m]||0) + 1);
    });
    data = Object.entries(macroCounts).map(([name, value])=>({name, value}));
    colorFor = (d)=> GENRE_COLORS[d.name] || GENRE_COLORS["Other"];
    onBubbleClick = (d)=>{
      state.genreDrill = d.name;
      setFilter("genreMacro", d.name);
      onChange();
    };
    isActive = (d)=> state.filters.genreMacro === d.name;
  }

  container.innerHTML = "";
  if(!data.length) return;

  // RYM-style "bold genres only": cap to the 8 most common bubbles per level
  // (both macro and sub-genre) and fold the long tail into one "Overige"
  // bubble, rather than dumping dozens of tiny, unreadable micro-tags.
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
    const overige = !!leaf.data.isOverige;
    el.className = "bubble" + (isActive(leaf.data) ? " active-filter" : "") + (overige ? " bubble-overige" : "");
    el.style.width = (leaf.r*2) + "px";
    el.style.height = (leaf.r*2) + "px";
    el.style.left = (leaf.x - leaf.r) + "px";
    el.style.top = (leaf.y - leaf.r) + "px";
    el.style.background = overige ? "var(--genre-other)" : colorFor(leaf.data);
    el.style.fontSize = Math.max(10, Math.min(14, leaf.r/2.8)) + "px";
    el.innerHTML = `<div><div class="bubble-name">${escapeHtml(leaf.data.name)}</div><div class="bubble-count">${leaf.data.value}</div></div>`;
    if(!overige) el.addEventListener("click", ()=> onBubbleClick(leaf.data));
    container.appendChild(el);
  });
}

const MAX_BUBBLES = 8;

function capBubbles(entries){
  const sorted = entries.slice().sort((a,b)=> b.value - a.value);
  if(sorted.length <= MAX_BUBBLES) return sorted;
  const top = sorted.slice(0, MAX_BUBBLES - 1);
  const rest = sorted.slice(MAX_BUBBLES - 1);
  const restValue = rest.reduce((sum,e)=> sum + e.value, 0);
  top.push({ name: `Overige (${rest.length})`, value: restValue, isOverige: true });
  return top;
}

function renderCountryChart(onChange){
  const canvas = document.getElementById("countryChart");
  const countryCounts = {};
  state.albums.forEach(a=>{
    const c = normalizeCountry(a.musicbrainz && a.musicbrainz.country);
    if(c) countryCounts[c] = (countryCounts[c]||0) + 1;
  });
  const sorted = Object.entries(countryCounts).sort((a,b)=> b[1]-a[1]);
  const top = sorted.slice(0, 12);
  const labels = top.map(([c])=>c);
  const values = top.map(([,n])=>n);

  if(countryChartInstance) countryChartInstance.destroy();
  countryChartInstance = new Chart(canvas, {
    type:"bar",
    data:{labels, datasets:[{
      data:values,
      backgroundColor:labels.map(c=> state.filters.country===c ? "#ffffff" : "#8b6bb5"),
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
        x:{ticks:{color:"#948b79",font:{family:"Space Mono",size:10}},grid:{color:"#3a352c"}},
        // autoSkip defaults to true and was silently dropping every other
        // country label to avoid overlap — force every label to render since
        // there are only 12 and the panel now has the height to fit them.
        y:{ticks:{color:"#948b79",font:{family:"Space Mono",size:9},autoSkip:false},grid:{display:false}}
      },
      onHover:(evt, elements)=>{ evt.native.target.style.cursor = elements.length ? "pointer" : "default"; }
    }
  });
}

export function resizeGenreBubbles(onChange){
  if(document.getElementById("genreBubbles")) renderGenreBubbles(onChange);
}
