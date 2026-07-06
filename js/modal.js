import { state } from "./state.js";
import { coverUrl, genreList } from "./data.js";

function extractYouTubeId(url){
  const m = (url||"").match(/(?:v=|youtu\.be\/|embed\/)([a-zA-Z0-9_-]{11})/);
  return m ? m[1] : null;
}

export function openModal(a){
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
  genreList(a).forEach(g=>{
    const b = document.createElement("span");
    b.className = "badge";
    b.textContent = g;
    badges.appendChild(b);
  });

  const mediaEl = document.getElementById("modalMedia");
  mediaEl.innerHTML = "";
  if(a.spotify && a.spotify.spotify_embed_url){
    const wrap = document.createElement("div");
    wrap.className = "media-item";
    const iframe = document.createElement("iframe");
    iframe.height = "152";
    iframe.src = a.spotify.spotify_embed_url;
    iframe.allow = "encrypted-media";
    wrap.appendChild(iframe);
    mediaEl.appendChild(wrap);
  }
  (a.media || []).forEach(m=>{
    const wrap = document.createElement("div");
    wrap.className = "media-item";
    if(m.type === "youtube"){
      const videoId = extractYouTubeId(m.url);
      if(videoId){
        const iframe = document.createElement("iframe");
        iframe.height = "220";
        iframe.src = `https://www.youtube.com/embed/${videoId}`;
        iframe.allowFullscreen = true;
        wrap.appendChild(iframe);
      } else {
        const link = document.createElement("a");
        link.href = m.url; link.target = "_blank"; link.rel = "noopener";
        link.textContent = m.url;
        wrap.appendChild(link);
      }
    } else if(m.type === "spotify"){
      const iframe = document.createElement("iframe");
      iframe.height = "152";
      iframe.src = m.url.replace("open.spotify.com/", "open.spotify.com/embed/");
      iframe.allow = "encrypted-media";
      wrap.appendChild(iframe);
    } else if(m.type === "image"){
      const img = document.createElement("img");
      img.src = m.url; img.alt = "";
      wrap.appendChild(img);
    } else {
      const link = document.createElement("a");
      link.href = m.url; link.target = "_blank"; link.rel = "noopener";
      link.textContent = m.url;
      wrap.appendChild(link);
    }
    if(m.caption){
      const cap = document.createElement("div");
      cap.className = "caption";
      cap.textContent = m.caption;
      wrap.appendChild(cap);
    }
    mediaEl.appendChild(wrap);
  });

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
}
