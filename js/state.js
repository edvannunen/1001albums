export const PAGE_SIZE = 48;

// Single mutable state object shared across modules by reference — avoids
// circular imports between modules that both need to read/write it.
export const state = {
  albums: [],
  lang: localStorage.getItem("lang") === "en" ? "en" : "nl",
  view: "grid",
  sort: "number_desc",
  page: 1,
  filters: {
    decade: null,
    year: null,
    genreMacro: null,
    genre: null,
    country: null,
    artist: null,
  },
  // drill state for the two hierarchical charts — independent of active
  // filters, so you can browse sub-genres/years without necessarily having
  // filtered the list yet.
  decadeDrill: null,   // e.g. "1980s" while showing that decade's years
  genreDrill: null,    // e.g. "Rock" while showing its sub-genre tags
  hideUsUk: false,      // countries chart: hide US/UK so smaller countries are visible
};
