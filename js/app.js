import { state, PAGE_SIZE } from "./state.js";
import { loadData } from "./data.js";
import { getFiltered, setFilter, clearFilter, clearAllFilters, renderFilterChips } from "./filters.js";
import { renderGrid, renderTable, renderPagination } from "./grid.js";
import { openModal, closeModal } from "./modal.js";
import { renderDashboard, resizeGenreBubbles, replayProgressAnimation } from "./charts.js";

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

  renderPagination(filtered.length, render);
  renderFilterChips((key)=>{ clearFilter(key); render(); });
  renderDashboard(render);
}

function wireEvents(){
  document.getElementById("searchInput").addEventListener("input", ()=>{ state.page = 1; render(); });
  document.getElementById("sortSelect").addEventListener("change", e=>{ state.sort = e.target.value; render(); });
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
    th.addEventListener("click", ()=>{ state.sort = th.dataset.key; render(); });
  });

  document.getElementById("modalClose").addEventListener("click", closeModal);
  document.getElementById("modalBackdrop").addEventListener("click", (e)=>{
    if(e.target.id === "modalBackdrop") closeModal();
  });
  document.addEventListener("keydown", (e)=>{
    if(e.key === "Escape") closeModal();
  });

  window.addEventListener("resize", ()=> resizeGenreBubbles(render));
  document.querySelector(".knob").addEventListener("click", replayProgressAnimation);
}

async function init(){
  wireEvents();
  await loadData();
  render();
}

init();
