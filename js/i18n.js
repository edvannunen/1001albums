import { state } from "./state.js";

// UI copy only — genre bucket names (taxonomy.js), country names, and
// artist/album names are deliberately NOT here: they stay in English (or
// as scraped) regardless of the selected UI language.
export const STRINGS = {
  nl: {
    tagline_sub: "EEN PERSOONLIJKE LUISTERTOCHT",
    info_btn_aria: "Over dit project",
    lang_switch_aria: "Taal wijzigen",
    dashboard_hint: "Klik in de widgets om te filteren",
    progress_label: "Voortgang",
    progress_suffix: "van 1001 albums beluisterd en besproken",
    decade_label: "Albums per decennium",
    genre_label: "Top genres",
    country_label: "Landen",
    view_grid: "Grid",
    view_table: "Tabel",
    search_placeholder: "zoek artiest, album, jaar, genre...",
    sort_label: "SORTEER",
    sort_newest: "Nieuwste eerst",
    sort_oldest: "Oudste eerst",
    sort_artist: "Artiest",
    sort_album: "Album",
    filters_label: "FILTERS:",
    clear_filters: "Alles wissen",
    table_artist: "Artiest",
    table_album: "Album",
    table_year: "Jaar",
    table_country: "Land",
    table_genre: "Genre",
    empty_state: "Geen albums gevonden voor deze zoekopdracht.",
    modal_share_aria: "Deel album review",
    modal_prev_aria: "Vorig album",
    modal_next_aria: "Volgend album",
    modal_more_by_artist: "Meer van deze artiest",
    info_body: 'Vanaf november 2019 luister ik alle platen uit het boek "1001 Albums You Must Hear Before You Die" in de chronologische volgorde van het boek, en schrijf een kort stukje over elk album. De bijdrages zijn ook te lezen op <a href="https://bsky.app/profile/edvannunen.bsky.social" target="_blank" rel="noopener">BlueSky</a> en <a href="https://edvannunen.medium.com/" target="_blank" rel="noopener">Medium</a>.',
    pagination_prev: "← Vorige",
    pagination_next: "Volgende →",
    pagination_range: (start, end, total) => `${start}–${end} van ${total}`,
    chart_back_decades: "← Decennia",
    chart_back_genres: "← Genres",
    us_uk_show: "US & UK tonen",
    us_uk_hide: "US & UK verbergen",
    toast_copied: "Albumrecensie gekopieerd naar klembord",
    toast_copy_failed: "Kopiëren naar klembord mislukt",
  },
  en: {
    tagline_sub: "A PERSONAL LISTENING JOURNEY",
    info_btn_aria: "About this project",
    lang_switch_aria: "Change language",
    dashboard_hint: "Click the widgets to filter",
    progress_label: "Progress",
    progress_suffix: "of 1001 albums listened to and reviewed",
    decade_label: "Albums per decade",
    genre_label: "Top genres",
    country_label: "Countries",
    view_grid: "Grid",
    view_table: "Table",
    search_placeholder: "search artist, album, year, genre...",
    sort_label: "SORT",
    sort_newest: "Newest first",
    sort_oldest: "Oldest first",
    sort_artist: "Artist",
    sort_album: "Album",
    filters_label: "FILTERS:",
    clear_filters: "Clear all",
    table_artist: "Artist",
    table_album: "Album",
    table_year: "Year",
    table_country: "Country",
    table_genre: "Genre",
    empty_state: "No albums found for this search.",
    modal_share_aria: "Share album review",
    modal_prev_aria: "Previous album",
    modal_next_aria: "Next album",
    modal_more_by_artist: "More by this artist",
    info_body: 'Since November 2019 I\'ve been listening to every record in the book "1001 Albums You Must Hear Before You Die," in the book\'s own chronological order, and writing a short piece about each one. The write-ups are also posted on <a href="https://bsky.app/profile/edvannunen.bsky.social" target="_blank" rel="noopener">BlueSky</a> and <a href="https://edvannunen.medium.com/" target="_blank" rel="noopener">Medium</a>.',
    pagination_prev: "← Previous",
    pagination_next: "Next →",
    pagination_range: (start, end, total) => `${start}–${end} of ${total}`,
    chart_back_decades: "← Decades",
    chart_back_genres: "← Genres",
    us_uk_show: "Show US & UK",
    us_uk_hide: "Hide US & UK",
    toast_copied: "Album review copied to the clipboard",
    toast_copy_failed: "Could not copy to clipboard",
  },
};

export function t(key){
  return STRINGS[state.lang][key] ?? STRINGS.nl[key] ?? key;
}

export function setLang(lang){
  state.lang = lang === "en" ? "en" : "nl";
  localStorage.setItem("lang", state.lang);
  document.documentElement.lang = state.lang;
}

// Sets textContent/placeholder/aria-label/innerHTML for every element
// carrying a data-i18n* hook — run on init and again whenever the language
// changes, since index.html's markup is otherwise static.
export function applyStaticStrings(){
  document.querySelectorAll("[data-i18n]").forEach(el=>{
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el=>{
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach(el=>{
    el.setAttribute("aria-label", t(el.dataset.i18nAria));
  });
  document.querySelectorAll("[data-i18n-html]").forEach(el=>{
    el.innerHTML = t(el.dataset.i18nHtml);
  });
}
