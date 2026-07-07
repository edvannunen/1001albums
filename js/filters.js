import { state } from "./state.js";
import { genreList, decadeOf } from "./data.js";
import { classifyGenre, normalizeCountry } from "./taxonomy.js";

export function matchesSearch(a, q){
  if(!q) return true;
  q = q.toLowerCase();
  const haystack = [
    a.artist, a.album, a.year,
    a.musicbrainz && a.musicbrainz.country,
    ...genreList(a)
  ].filter(Boolean).join(" ").toLowerCase();
  return haystack.includes(q);
}

export function matchesFilters(a){
  const f = state.filters;
  if(f.year){
    if(parseInt(a.year) !== f.year) return false;
  } else if(f.decade){
    if(decadeOf(a) !== f.decade) return false;
  }
  if(f.genre){
    if(!genreList(a).includes(f.genre)) return false;
  } else if(f.genreMacro){
    const macros = genreList(a).map(classifyGenre);
    if(!macros.includes(f.genreMacro)) return false;
  }
  if(f.country){
    const c = normalizeCountry(a.musicbrainz && a.musicbrainz.country);
    if(c !== f.country) return false;
  }
  if(f.artist){
    if(a.artist !== f.artist) return false;
  }
  return true;
}

export function sortAlbums(list){
  const copy = [...list];
  copy.sort((a,b)=>{
    if(state.sort === "number") return parseInt(a.number) - parseInt(b.number);
    if(state.sort === "number_desc") return parseInt(b.number) - parseInt(a.number);
    if(state.sort === "year") return parseInt(a.year) - parseInt(b.year);
    if(state.sort === "artist") return a.artist.localeCompare(b.artist);
    if(state.sort === "album") return a.album.localeCompare(b.album);
    return 0;
  });
  return copy;
}

export function getFiltered(searchQuery){
  return sortAlbums(state.albums.filter(a => matchesSearch(a, searchQuery) && matchesFilters(a)));
}

// year/decade and genre/genreMacro are mutually exclusive pairs — setting
// one clears its counterpart so the filter-chip bar never shows both at once.
export function setFilter(key, value){
  if(key === "year"){ state.filters.year = value; state.filters.decade = null; }
  else if(key === "decade"){ state.filters.decade = value; state.filters.year = null; }
  else if(key === "genre"){ state.filters.genre = value; state.filters.genreMacro = null; }
  else if(key === "genreMacro"){ state.filters.genreMacro = value; state.filters.genre = null; }
  else state.filters[key] = value;
  state.page = 1;
}

export function clearFilter(key){
  if(key === "year" || key === "decade"){
    state.filters.year = null; state.filters.decade = null; state.decadeDrill = null;
  } else if(key === "genre" || key === "genreMacro"){
    state.filters.genre = null; state.filters.genreMacro = null; state.genreDrill = null;
  } else {
    state.filters[key] = null;
  }
  state.page = 1;
}

export function clearAllFilters(){
  state.filters = { decade:null, year:null, genreMacro:null, genre:null, country:null, artist:null };
  state.decadeDrill = null;
  state.genreDrill = null;
  state.page = 1;
}

export function renderFilterChips(onRemove){
  const bar = document.getElementById("filterBar");
  const chipsEl = document.getElementById("filterChips");
  const f = state.filters;
  const chips = [];
  if(f.year) chips.push(["year", f.year]);
  else if(f.decade) chips.push(["decade", f.decade]);
  if(f.genre) chips.push(["genre", f.genre]);
  else if(f.genreMacro) chips.push(["genreMacro", f.genreMacro]);
  if(f.country) chips.push(["country", f.country]);
  if(f.artist) chips.push(["artist", f.artist]);

  bar.classList.toggle("visible", chips.length > 0);
  chipsEl.innerHTML = "";
  chips.forEach(([key, value])=>{
    const chip = document.createElement("span");
    chip.className = "chip";
    const label = document.createElement("span");
    label.textContent = String(value);
    const btn = document.createElement("button");
    btn.textContent = "×";
    btn.addEventListener("click", ()=> onRemove(key));
    chip.appendChild(label);
    chip.appendChild(btn);
    chipsEl.appendChild(chip);
  });
}
