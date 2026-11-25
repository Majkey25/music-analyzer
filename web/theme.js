(() => {
  const root = document.documentElement;
  const name = "music-analyzer-theme";
  const saved = document.cookie.match(/(?:^|;\s*)music-analyzer-theme=(light|dark)(?:;|$)/)?.[1];
  root.dataset.theme = saved ?? "system";
  document.addEventListener("DOMContentLoaded", () => {
    const control = document.getElementById("theme");
    control.value = root.dataset.theme;
    control.addEventListener("change", () => {
      root.dataset.theme = control.value;
      // The launcher can choose a new port, so the preference is host-scoped.
      const age = control.value === "system" ? 0 : 31536000;
      document.cookie = `${name}=${control.value}; Max-Age=${age}; Path=/; SameSite=Strict`;
      document.dispatchEvent(new Event("themechange"));
    });
  });
})();
