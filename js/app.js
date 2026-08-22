import { state, PAGE_SIZE } from "./state.js";
import { loadData, findAlbumByParam } from "./data.js";
import { getFiltered, setFilter, clearFilter, clearAllFilters, renderFilterChips } from "./filters.js";
import { renderGrid, renderTable, renderPagination } from "./grid.js";
import { openModal, closeModal, showModalPrev, showModalNext, shareCurrentAlbum } from "./modal.js";
import { renderDashboard, resizeGenreBubbles, replayProgressAnimation } from "./charts.js";
import { setLang, applyStaticStrings } from "./i18n.js";

function updateLangFlagIcon(){
  document.getElementById("langCurrentFlag").src =
    state.lang === "en" ? "https://flagcdn.com/gb.svg" : "https://flagcdn.com/nl.svg";
}

function selectLang(lang){
  setLang(lang);
  updateLangFlagIcon();
  document.getElementById("langMenu").classList.add("hidden");
  document.getElementById("langCurrent").setAttribute("aria-expanded", "false");
  applyStaticStrings();

  const params = new URLSearchParams(location.search);
  params.set("lang", lang);
  history.replaceState(null, "", "?" + params.toString());

  render();

  const albumParam = new URLSearchParams(location.search).get("album");
  const openAlbum = document.getElementById("modalBackdrop").classList.contains("open")
    ? findAlbumByParam(albumParam) : null;
  if(openAlbum) openModal(openAlbum);
}

function render(){
  const q = document.getElementById("searchInput").value;
  const filtered = getFiltered(q);

  document.getElementById("emptyState").classList.toggle("hidden", filtered.length > 0);
  document.getElementById("grid").classList.toggle("hidden", state.view !== "grid" || filtered.length === 0);
  document.getElementById("table").classList.toggle("hidden", state.view !== "table" || filtered.length === 0);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  if(state.page > totalPages) state.page = totalPages;
  const pageItems = filtered.slice((state.page-1)*PAGE_SIZE, state.page*PAGE_SIZE);

  const rowHandlers = { onOpen: openModal, onArtistClick: (artist)=>{ setFilter("artist", artist); render(); } };
  if(state.view === "grid") renderGrid(pageItems, rowHandlers);
  else renderTable(pageItems, rowHandlers);

  document.querySelectorAll("th[data-key]").forEach(th=>{
    th.classList.toggle("sorted", th.dataset.key === state.sort);
    th.classList.toggle("sort-desc", th.dataset.key === state.sort && state.sortDir === "desc");
  });

  renderPagination(filtered.length, render);
  renderFilterChips((key)=>{ clearFilter(key); render(); });
  renderDashboard(render);
}

function wireEvents(){
  document.getElementById("searchInput").addEventListener("input", ()=>{ state.page = 1; render(); });
  document.getElementById("sortSelect").addEventListener("change", e=>{
    const v = e.target.value;
    if(v === "number_desc"){ state.sort = "number"; state.sortDir = "desc"; }
    else { state.sort = v; state.sortDir = "asc"; }
    render();
  });
  document.getElementById("clearFilters").addEventListener("click", ()=>{ clearAllFilters(); render(); });

  document.getElementById("viewGrid").addEventListener("click", ()=>{
    state.view = "grid";
    document.getElementById("viewGrid").classList.add("active");
    document.getElementById("viewTable").classList.remove("active");
    render();
  });
  document.getElementById("viewTable").addEventListener("click", ()=>{
    state.view = "table";
    document.getElementById("viewTable").classList.add("active");
    document.getElementById("viewGrid").classList.remove("active");
    render();
  });
  document.querySelectorAll("th[data-key]").forEach(th=>{
    th.addEventListener("click", ()=>{
      const key = th.dataset.key;
      if(state.sort === key){ state.sortDir = state.sortDir === "asc" ? "desc" : "asc"; }
      else { state.sort = key; state.sortDir = "asc"; }
      render();
    });
  });

  document.getElementById("modalClose").addEventListener("click", closeModal);
  document.getElementById("modalBackdrop").addEventListener("click", (e)=>{
    if(e.target.id === "modalBackdrop") closeModal();
  });
  document.getElementById("modalNavPrev").addEventListener("click", showModalPrev);
  document.getElementById("modalNavNext").addEventListener("click", showModalNext);
  document.getElementById("modalShare").addEventListener("click", shareCurrentAlbum);

  document.getElementById("langCurrent").addEventListener("click", (e)=>{
    e.stopPropagation();
    const menu = document.getElementById("langMenu");
    const willOpen = menu.classList.contains("hidden");
    menu.classList.toggle("hidden", !willOpen);
    document.getElementById("langCurrent").setAttribute("aria-expanded", String(willOpen));
  });
  document.querySelectorAll("#langMenu li").forEach(li=>{
    li.addEventListener("click", ()=> selectLang(li.dataset.lang));
  });
  document.addEventListener("click", (e)=>{
    if(!document.getElementById("langSwitch").contains(e.target)){
      document.getElementById("langMenu").classList.add("hidden");
      document.getElementById("langCurrent").setAttribute("aria-expanded", "false");
    }
  });

  document.getElementById("infoBtn").addEventListener("click", ()=>{
    document.getElementById("infoBackdrop").classList.add("open");
  });
  document.getElementById("infoClose").addEventListener("click", ()=>{
    document.getElementById("infoBackdrop").classList.remove("open");
  });
  document.getElementById("infoBackdrop").addEventListener("click", (e)=>{
    if(e.target.id === "infoBackdrop") document.getElementById("infoBackdrop").classList.remove("open");
  });

  document.addEventListener("keydown", (e)=>{
    if(e.key === "Escape"){
      closeModal();
      document.getElementById("infoBackdrop").classList.remove("open");
    }
    if(!document.getElementById("modalBackdrop").classList.contains("open")) return;
    if(e.key === "ArrowLeft") showModalPrev();
    if(e.key === "ArrowRight") showModalNext();
  });

  window.addEventListener("resize", ()=> resizeGenreBubbles(render));
  document.querySelector(".knob").addEventListener("click", replayProgressAnimation);
}

async function init(){
  const params = new URLSearchParams(location.search);
  const langParam = params.get("lang");
  setLang(langParam === "en" || langParam === "nl" ? langParam : state.lang);
  updateLangFlagIcon();
  applyStaticStrings();

  wireEvents();
  await loadData();
  render();

  const albumParam = params.get("album");
  const linkedAlbum = findAlbumByParam(albumParam);
  if(linkedAlbum) openModal(linkedAlbum);
}

init();
