// ---------------------------------------------------------------------------
// Genre taxonomy — MusicBrainz tags are messy folksonomy data (949 unique
// tags observed across 619 real albums, including non-genre junk like "male
// vocalist", "should be public domain", literal decade tags). This buckets
// them into a small set of macro genres for the top-level bubble view, with
// the raw tags themselves shown as sub-genre bubbles on drill-down.
//
// Priority order matters: hybrid tags ("pop rock", "country rock") are
// resolved by whichever bucket's keyword is checked first, so Rock (the most
// common by far in this dataset) is checked first, then progressively more
// specific buckets.
// ---------------------------------------------------------------------------
export const GENRE_TAXONOMY = [
  { name: "Rock", keywords: ["rock","punk","metal","grunge","garage","hardcore","emo","psychedelic","indie"] },
  { name: "Pop", keywords: ["pop","new wave","disco","synth","dance","new romantic"] },
  { name: "Jazz & Blues", keywords: ["jazz","blues","bebop","bop","swing","big band"] },
  { name: "Electronic", keywords: ["electronic","techno","house","ambient","idm","industrial","ebm"] },
  { name: "Folk & Country", keywords: ["folk","country","americana","bluegrass","singer-songwriter","singer/songwriter"] },
  { name: "Soul, Funk & World", keywords: ["soul","funk","r&b","rnb","reggae","ska","afrobeat","world","latin","samba","bossa nova","calypso","highlife","fado","gospel","salsa"] },
];

export const GENRE_COLORS = {
  "Rock": "var(--genre-rock)",
  "Pop": "var(--genre-pop)",
  "Jazz & Blues": "var(--genre-jazz)",
  "Electronic": "var(--genre-electronic)",
  "Folk & Country": "var(--genre-folk)",
  "Soul, Funk & World": "var(--genre-world)",
  "Other": "var(--genre-other)",
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
  return "Other";
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
