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

// Country badge flag lookup — keyed on the exact strings seen in
// musicbrainz.country across the current dataset (checked 2026-07), not a
// general country-name-to-ISO library. MusicBrainz's artist `area` is
// sometimes a city/region rather than a country (e.g. "New York",
// "Scotland") so those need mapping too, same as a real country. Anything
// not in this list just shows no flag — safe no-op, doesn't need
// updating unless re-running the enrichment pipeline turns up a new one.
const COUNTRY_ISO = {
  "United States":"US", "New York":"US", "Memphis":"US", "Los Angeles":"US", "Boston":"US", "Phoenix":"US",
  "United Kingdom":"GB", "England":"GB", "Scotland":"GB", "Wales":"GB", "Northern Ireland":"GB", "London":"GB",
  "Canada":"CA", "Germany":"DE", "Brazil":"BR", "Jamaica":"JM", "Australia":"AU",
  "South Africa":"ZA", "Ladysmith":"ZA", "Ireland":"IE", "India":"IN", "France":"FR", "Sweden":"SE",
  "Cuba":"CU", "Belgium":"BE", "Estonia":"EE", "Nigeria":"NG", "Japan":"JP", "Finland":"FI",
  "Switzerland":"CH", "Senegal":"SN", "Norway":"NO", "Argentina":"AR", "Slovenia":"SI",
};

// Returns a lowercase ISO 3166-1 alpha-2 code (or "" if unmapped) rather
// than a flag emoji — Windows doesn't reliably render regional-indicator
// flag sequences as pictorial flags (shows the two letters as plain text
// instead, even on Windows 11), so the badge renders an actual flag image
// (flagcdn.com) keyed on this code instead.
export function countryIso(country){
  return COUNTRY_ISO[country] || "";
}

export function decadeOf(a){
  const y = parseInt(a.year);
  if(!y) return null;
  return Math.floor(y/10)*10 + "s";
}

function slugify(s){
  return (s || "")
    .toLowerCase()
    .normalize("NFD").replace(new RegExp("[̀-ͯ]", "g"), "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// number-artist-album rather than a bare number: a handful of catalog
// numbers are genuinely reused for two different albums (authoring typos
// in the source posts, see CLAUDE.md), so the number alone can't uniquely
// identify an album.
export function albumSlug(a){
  return `${a.number}-${slugify(`${a.artist} ${a.album}`)}`;
}

export function albumShareUrl(a){
  return `${location.origin}${location.pathname}?album=${albumSlug(a)}`;
}

// Accepts either a full slug or a bare number (e.g. a hand-typed URL) —
// falls back to the first album with a matching number, which only
// matters for the handful of reused catalog numbers noted above.
export function findAlbumByParam(param){
  if(!param) return null;
  const exact = state.albums.find(a => albumSlug(a) === param);
  if(exact) return exact;
  const number = param.split("-")[0];
  return state.albums.find(a => a.number === number) || null;
}
