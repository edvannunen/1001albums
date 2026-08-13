import { state, PAGE_SIZE } from "./state.js";
import { coverUrl, genreList } from "./data.js";
import { t } from "./i18n.js";

export function escapeHtml(s){
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

export function renderGrid(list, { onOpen, onArtistClick }){
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  list.forEach(a=>{
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="cover-wrap">
        <div class="cover" style="background-image:url('${coverUrl(a)}')">
          <div class="catalog mono">${a.number}</div>
        </div>
      </div>
      <div class="meta">
        <div class="artist">${escapeHtml(a.artist)}</div>
        <div class="album">${escapeHtml(a.album)}</div>
        <div class="year">${a.year}${a.musicbrainz && a.musicbrainz.country ? " · " + escapeHtml(a.musicbrainz.country) : ""}</div>
      </div>
    `;
    card.addEventListener("click", ()=> onOpen(a));
    card.querySelector(".artist").addEventListener("click", (e)=>{
      e.stopPropagation();
      onArtistClick(a.artist);
    });
    grid.appendChild(card);
  });
}

export function renderTable(list, { onOpen, onArtistClick }){
  const body = document.getElementById("tableBody");
  body.innerHTML = "";
  list.forEach(a=>{
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${a.number}</td>
      <td class="artist-cell">${escapeHtml(a.artist)}</td>
      <td><em>${escapeHtml(a.album)}</em></td>
      <td class="mono">${a.year}</td>
      <td>${(a.musicbrainz && a.musicbrainz.country) ? escapeHtml(a.musicbrainz.country) : "—"}</td>
      <td>${genreList(a).slice(0, 10).join(", ") || "—"}</td>
    `;
    tr.addEventListener("click", ()=> onOpen(a));
    tr.querySelector(".artist-cell").addEventListener("click", (e)=>{
      e.stopPropagation();
      onArtistClick(a.artist);
    });
    body.appendChild(tr);
  });
}

export function renderPagination(total, onPageChange){
  const el = document.getElementById("pagination");
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if(state.page > totalPages) state.page = totalPages;

  if(total <= PAGE_SIZE){ el.innerHTML = ""; return; }
  const start = (state.page-1)*PAGE_SIZE + 1;
  const end = Math.min(state.page*PAGE_SIZE, total);
  el.innerHTML = "";

  const prev = document.createElement("button");
  prev.textContent = t("pagination_prev");
  prev.disabled = state.page === 1;
  prev.addEventListener("click", ()=>{ state.page--; onPageChange(); window.scrollTo({top:0, behavior:"smooth"}); });

  const label = document.createElement("span");
  label.className = "mono";
  label.textContent = t("pagination_range")(start, end, total);

  const next = document.createElement("button");
  next.textContent = t("pagination_next");
  next.disabled = state.page === totalPages;
  next.addEventListener("click", ()=>{ state.page++; onPageChange(); window.scrollTo({top:0, behavior:"smooth"}); });

  el.appendChild(prev);
  el.appendChild(label);
  el.appendChild(next);
}
