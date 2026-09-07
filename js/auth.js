// admin_ui is a plain, non-sensitive cookie set by GET /admin/login after a
// successful HTTP Basic prompt — purely a UI flag telling this page to show
// the edit pencil. It grants no access on its own; the real authorization
// boundary is the server's Depends(require_admin) on the write endpoint.
export function isLoggedIn(){
  return document.cookie.split("; ").includes("admin_ui=1");
}
