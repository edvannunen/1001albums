import { state } from "./state.js";

/*
  Data contract (from enrich_1001_albums.py output, albums_enriched.json):
  {
    number, artist, album, year, text,
    media: [{type: "youtube"|"spotify"|"image"|"other", url, caption}],
    spotify: {spotify_url, spotify_embed_url, cover_art_url, matched_artist_name,
              matched_album_name, matched_release_year, exact_year_match} | null,
    musicbrainz: {country, genres: [...], cover_art_archive_url} | null
  }
*/

const SAMPLE_DATA = [
  {number:"615", artist:"Everything But The Girl", album:"Idlewild", year:"1988",
   text:"Sample tekst — vervang met echte data uit albums_enriched.json.",
   media:[{type:"youtube", url:"https://www.youtube.com/watch?v=dQw4w9WgXcQ", caption:"Sample clip"}],
   spotify:{cover_art_url:"", spotify_embed_url:""},
   musicbrainz:{country:"United Kingdom", genres:["sophisti-pop","jazz pop"]}},
  {number:"616", artist:"Talk Talk", album:"Spirit of Eden", year:"1988",
   text:"Sample tekst.", media:[], spotify:{cover_art_url:"", spotify_embed_url:""},
   musicbrainz:{country:"United Kingdom", genres:["post-rock","art rock"]}},
  {number:"617", artist:"Talk Talk", album:"The Colour of Spring", year:"1986",
   text:"Sample tekst.", media:[], spotify:{cover_art_url:"", spotify_embed_url:""},
   musicbrainz:{country:"United Kingdom", genres:["art rock","pop rock"]}},
  {number:"618", artist:"Voice of the Beehive", album:"Let It Bee", year:"1988",
   text:"Sample tekst.", media:[], spotify:{cover_art_url:"", spotify_embed_url:""},
   musicbrainz:{country:"United States", genres:["pop rock"]}},
  {number:"12", artist:"Miles Davis", album:"Birth of the Cool", year:"1957",
   text:"Sample tekst.", media:[], spotify:{cover_art_url:"", spotify_embed_url:""},
   musicbrainz:{country:"United States", genres:["jazz","bebop"]}},
  {number:"9", artist:"Fela Kuti", album:"Zombie", year:"1976",
   text:"Sample tekst.", media:[], spotify:{cover_art_url:"", spotify_embed_url:""},
   musicbrainz:{country:"Nigeria", genres:["afrobeat","funk"]}},
];

export async function loadData(){
  try{
    const res = await fetch("albums_enriched.json");
    if(!res.ok) throw new Error("not found");
    state.albums = await res.json();
  }catch(e){
    console.warn("albums_enriched.json not found — showing sample data. Place the real file next to index.html.");
    state.albums = SAMPLE_DATA;
  }
}

export function coverUrl(a){
  return (a.spotify && a.spotify.cover_art_url)
    || (a.musicbrainz && a.musicbrainz.cover_art_archive_url)
    || "";
}

export function genreList(a){
  return (a.musicbrainz && a.musicbrainz.genres) ? a.musicbrainz.genres : [];
}

export function decadeOf(a){
  const y = parseInt(a.year);
  if(!y) return null;
  return Math.floor(y/10)*10 + "s";
}
