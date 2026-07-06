// ---------------------------------------------------------------------------
// Genre taxonomy — MusicBrainz tags are messy folksonomy data (949 unique
// tags observed across 619 real albums, including non-genre junk like "male
// vocalist", mood descriptors ("melancholic", "anxious", "surreal") and
// literal decade tags). This buckets them into 12 macro genres for the
// top-level bubble view, with the raw tags themselves shown as sub-genre
// bubbles on drill-down.
//
// There is deliberately no catch-all "Other" bucket: an album whose tags are
// all mood/junk descriptors (no real genre signal) simply doesn't appear in
// this chart, rather than inflating a meaningless leftover bubble.
//
// Priority order matters: hybrid tags ("folk rock", "country rock", "blues
// rock", "post-punk") are resolved by whichever bucket's keyword is checked
// first. Specific/compound genre buckets are checked BEFORE the two generic
// rock catch-alls (Hard Rock, Classic Rock) so a hybrid gets its more
// specific identity instead of being swallowed by a bare "rock" match.
// ---------------------------------------------------------------------------
export const GENRE_TAXONOMY = [
  { name: "Folk & Country", keywords: ["folk rock","folk-rock","country rock","country-rock","americana","bluegrass","singer-songwriter","singer/songwriter","contemporary folk","folk","country"] },
  { name: "Jazz & Blues", keywords: ["jazz-rock","jazz rock","jazz pop","blues rock","blues-rock","jazz","blues","bebop","bop","swing","big band"] },
  { name: "Soul & Funk", keywords: ["funk soul","pop soul","blue-eyed soul","rhythm & blues","r&b","rnb","soul","funk"] },
  { name: "New Wave", keywords: ["synth-pop","synthpop","new wave","new romantic","dance-rock","dance-pop","punk/new wave"] },
  { name: "Electronic", keywords: ["electronic","techno","house","ambient","idm","industrial","ebm","disco","downtempo","electro","dance"] },
  { name: "World & Latin", keywords: ["world","latin","reggae","ska","afrobeat","samba","bossa nova","calypso","highlife","fado","salsa","gospel"] },
  { name: "Progressive", keywords: ["art rock","art punk","art pop","progressive rock","prog-rock","progressive","avant-garde","experimental rock","experimental","concept album","krautrock","psychedelic rock","psychedelic pop","psychedelic"] },
  { name: "Alternative", keywords: ["alternative rock","alternative/indie rock","indie rock","college rock","jangle pop","alternative pop/rock","alternative and punk","post-punk","alternative"] },
  { name: "Punk", keywords: ["punk rock","punk","hardcore","grunge","emo","garage rock","garage","proto-punk"] },
  { name: "Hard Rock", keywords: ["heavy metal","metal","hard rock","glam rock","glam","acid rock"] },
  { name: "Classic Rock", keywords: ["classic rock","album rock","arena rock","southern rock","rock and roll","rock & roll","rock"] },
  { name: "Pop", keywords: ["baroque pop","power pop","am pop","sophisti-pop","adult contemporary","pop"] },
];

// Fixed hue per bucket (array order = the CVD-safety mechanism, never
// cycled). Extends the mood-board's 10-swatch brand palette with 2 additions
// (rose, navy) to reach 12 — validated with the dataviz skill's script
// against this widget's cream surface (#f3e9db), --pairs all (any two
// bubbles can be neighbors in a pack layout):
//   node validate_palette.js "<12 hexes>" --mode light --surface "#f3e9db" --pairs all
// Result: worst all-pairs CVD ΔE 8.2 (floor band, legal since every bubble
// carries a direct name+count label); 6 of the original brand hues sit below
// the chroma floor and 3 are sub-3:1 on the cream surface — both accepted as
// characteristics of the supplied brand palette (same call made for the
// decade/country widgets), mitigated the same way by the direct labels.
export const GENRE_COLORS = {
  "Folk & Country": "#c8b08a",
  "Jazz & Blues": "#2e6a71",
  "Soul & Funk": "#8e3b3b",
  "New Wave": "#6e4c7b",
  "Electronic": "#3f8ca2",
  "World & Latin": "#d97a2b",
  "Progressive": "#3e6b4a",
  "Alternative": "#b8356b",
  "Punk": "#c0392b",
  "Hard Rock": "#3a4f96",
  "Classic Rock": "#5f7f4f",
  "Pop": "#d4a62a",
};

// non-genre descriptor/junk tags to drop entirely from the genre charts
const GENRE_STOPWORDS = new Set([
  "male vocalist","female vocalist","energetic","melodic","passionate","playful",
  "quirky","bittersweet","poetic","introspective","rhythmic","urban",
  "should be public domain","offizielle charts","english","american","canadian",
  "dutch","german","french","dense","lush","manic","nocturnal","uplifting",
  "improvisation","acoustic",
]);

export function classifyGenre(tag){
  const t = tag.toLowerCase().trim();
  if (GENRE_STOPWORDS.has(t) || /^(19|20)\d{2}$/.test(t) || /^\d0s$/.test(t)) return null;
  for (const bucket of GENRE_TAXONOMY){
    if (bucket.keywords.some(kw => t.includes(kw))) return bucket.name;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Country normalization — MusicBrainz's artist.area field returns whatever
// granularity is known for that artist (country, region, or even city), so
// raw data mixes "United Kingdom" with "England"/"Scotland"/"London" and
// "United States" with "New York"/"Los Angeles"/etc. Best-effort mapping to
// the parent country for chart purposes (not exhaustive).
// ---------------------------------------------------------------------------
const COUNTRY_ALIASES = {
  "England": "United Kingdom", "Scotland": "United Kingdom", "Wales": "United Kingdom",
  "Northern Ireland": "United Kingdom", "London": "United Kingdom",
  "New York": "United States", "Los Angeles": "United States", "Boston": "United States",
  "Phoenix": "United States", "Memphis": "United States", "Chicago": "United States",
  "Ladysmith": "South Africa",
};

export function normalizeCountry(raw){
  if (!raw) return null;
  return COUNTRY_ALIASES[raw] || raw;
}
