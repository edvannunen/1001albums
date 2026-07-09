let hideTimer = null;

export function showToast(message){
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.add("visible");
  clearTimeout(hideTimer);
  hideTimer = setTimeout(()=> el.classList.remove("visible"), 2400);
}
