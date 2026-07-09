import { state } from "./state.js";
import { coverUrl, genreList, albumSlug, albumShareUrl } from "./data.js";
import { getFiltered, sortAlbums } from "./filters.js";
import { showToast } from "./toast.js";

function extractYouTubeId(url){
  const m = (url||"").match(/(?:v=|youtu\.be\/|embed\/)([a-zA-Z0-9_-]{11})/);
  return m ? m[1] : null;
}

// The navigable list for the </> buttons — the current search/sort/filter
// result, so paging through the modal matches what's on screen. Falls back
// to the full sorted list when the opened album isn't in that result (e.g.
// "More by artist" jumping to an album excluded by an active filter) so
// navigation still has something sensible to do instead of just disabling.
let modalList = [];
let modalIndex = -1;

function updateNav(a){
  const q = document.getElementById("searchInput").value;
  modalList = getFiltered(q);
  modalIndex = modalList.indexOf(a);
  if(modalIndex === -1){
    modalList = sortAlbums(state.albums);
    modalIndex = modalList.indexOf(a);
  }
  document.getElementById("modalNavPrev").disabled = modalIndex <= 0;
  document.getElementById("modalNavNext").disabled = modalIndex === -1 || modalIndex >= modalList.length - 1;
}

export function showModalPrev(){
  if(modalIndex > 0) openModal(modalList[modalIndex - 1]);
}

export function showModalNext(){
  if(modalIndex !== -1 && modalIndex < modalList.length - 1) openModal(modalList[modalIndex + 1]);
}

export function shareCurrentAlbum(){
  const a = modalIndex !== -1 ? modalList[modalIndex] : null;
  if(!a) return;
  const text = `#1001Albums ${a.number} ${a.artist} - ${a.album} (${a.year})\n\n${albumShareUrl(a)}`;
  navigator.clipboard.writeText(text)
    .then(()=> showToast("Album review copied to the clipboard"))
    .catch(()=> showToast("Could not copy to clipboard"));
}

export function openModal(a){
  updateNav(a);
  history.replaceState(null, "", "?album=" + albumSlug(a));
  document.getElementById("modalCatalog").textContent = "#" + a.number;
  document.getElementById("modalArtist").textContent = a.artist;
  document.getElementById("modalAlbum").textContent = `${a.album} (${a.year})`;
  document.getElementById("modalCover").style.backgroundImage = `url('${coverUrl(a)}')`;
  document.getElementById("modalText").textContent = a.text || "";

  const badges = document.getElementById("modalBadges");
  badges.innerHTML = "";
  const country = a.musicbrainz && a.musicbrainz.country;
  if(country){
    const b = document.createElement("span");
    b.className = "badge";
    b.textContent = country;
    badges.appendChild(b);
  }
  genreList(a).slice(0, 10).forEach(g=>{
    const b = document.createElement("span");
    b.className = "badge";
    b.textContent = g;
    badges.appendChild(b);
  });

  const mediaEl = document.getElementById("modalMedia");
  mediaEl.innerHTML = "";

  // Grouped by type rather than the post's original interleaved order:
  // images, then all YouTube clips (each with its caption if it has one),
  // then all Spotify embeds (the enrichment stage's whole-album match, if
  // any, followed by any Spotify links Ed embedded directly in the post),
  // then any other link-preview cards.
  function appendMedia(build, caption){
    const wrap = document.createElement("div");
    wrap.className = "media-item";
    build(wrap);
    if(caption){
      const cap = document.createElement("div");
      cap.className = "caption";
      cap.textContent = caption;
      wrap.appendChild(cap);
    }
    mediaEl.appendChild(wrap);
  }

  const media = a.media || [];
  const images = media.filter(m => m.type === "image");
  const youtubes = media.filter(m => m.type === "youtube");
  const spotifyLinks = media.filter(m => m.type === "spotify");
  const otherLinks = media.filter(m => !["image", "youtube", "spotify"].includes(m.type));

  images.forEach(m => appendMedia(wrap => {
    const img = document.createElement("img");
    img.src = m.url; img.alt = "";
    wrap.appendChild(img);
  }, m.caption));

  youtubes.forEach(m => appendMedia(wrap => {
    const videoId = extractYouTubeId(m.url);
    if(videoId){
      const iframe = document.createElement("iframe");
      iframe.height = "440";
      iframe.src = `https://www.youtube.com/embed/${videoId}`;
      iframe.allowFullscreen = true;
      wrap.appendChild(iframe);
    } else {
      const link = document.createElement("a");
      link.href = m.url; link.target = "_blank"; link.rel = "noopener";
      link.textContent = m.url;
      wrap.appendChild(link);
    }
  }, m.caption));

  if(a.spotify && a.spotify.spotify_embed_url){
    appendMedia(wrap => {
      const iframe = document.createElement("iframe");
      iframe.height = "152";
      iframe.src = a.spotify.spotify_embed_url;
      iframe.allow = "encrypted-media";
      wrap.appendChild(iframe);
    });
  }

  spotifyLinks.forEach(m => appendMedia(wrap => {
    const iframe = document.createElement("iframe");
    iframe.height = "152";
    iframe.src = m.url.replace("open.spotify.com/", "open.spotify.com/embed/");
    iframe.allow = "encrypted-media";
    wrap.appendChild(iframe);
  }, m.caption));

  otherLinks.forEach(m => appendMedia(wrap => {
    const link = document.createElement("a");
    link.href = m.url; link.target = "_blank"; link.rel = "noopener";
    link.textContent = m.url;
    wrap.appendChild(link);
  }, m.caption));

  const others = state.albums.filter(x => x.artist === a.artist && x !== a);
  const moreBlock = document.getElementById("modalMoreByArtist");
  const moreList = document.getElementById("modalMoreByArtistList");
  moreList.innerHTML = "";
  if(others.length){
    moreBlock.classList.remove("hidden");
    others.sort((x,y)=> parseInt(x.number) - parseInt(y.number)).forEach(o=>{
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.textContent = `#${o.number} — ${o.album} (${o.year})`;
      link.addEventListener("click", ()=> openModal(o));
      li.appendChild(link);
      moreList.appendChild(li);
    });
  } else {
    moreBlock.classList.add("hidden");
  }

  document.getElementById("modalBackdrop").classList.add("open");
}

export function closeModal(){
  document.getElementById("modalBackdrop").classList.remove("open");
  history.replaceState(null, "", location.pathname);
}
