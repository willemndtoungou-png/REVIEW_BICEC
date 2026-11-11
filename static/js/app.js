// Comportements de l'interface. Aucun script en ligne dans le HTML (politique CSP stricte).
(function () {
  "use strict";

  const EXTENSIONS = [".csv", ".xlsx"];

  function taille(octets) {
    if (octets < 1024) return octets + " o";
    if (octets < 1024 * 1024) return (octets / 1024).toFixed(1).replace(".", ",") + " Ko";
    return (octets / (1024 * 1024)).toFixed(1).replace(".", ",") + " Mo";
  }

  function extension(nom) {
    const i = nom.lastIndexOf(".");
    return i >= 0 ? nom.slice(i).toLowerCase() : "";
  }

  // ---- Dépôt des fichiers ----
  const form = document.querySelector("form.depot");
  if (form) {
    const zone = form.querySelector("[data-zone]");
    const input = form.querySelector("[data-input]");
    const selection = form.querySelector("[data-selection]");
    const liste = form.querySelector("[data-liste]");
    const resume = form.querySelector("[data-resume]");
    const erreur = form.querySelector("[data-erreur]");
    const envoyer = form.querySelector("[data-envoyer]");
    const vider = form.querySelector("[data-vider]");
    const maxOctets = Number(form.dataset.maxOctets);
    const maxFichiers = Number(form.dataset.maxFichiers);

    function afficher() {
      const fichiers = Array.from(input.files);
      liste.replaceChildren();
      let total = 0;
      const refuses = [];
      fichiers.forEach(function (f) {
        total += f.size;
        const ext = extension(f.name);
        const ok = EXTENSIONS.includes(ext);
        if (!ok) refuses.push(f.name);
        const li = document.createElement("li");
        const fmt = document.createElement("span");
        fmt.className = "format" + (ok ? "" : " format--refuse");
        fmt.textContent = ok ? ext.slice(1).toUpperCase() : "Refusé";
        const nom = document.createElement("span");
        nom.className = "selection__nom";
        nom.textContent = f.name;   // textContent : le nom n'est jamais interprété comme du HTML
        nom.title = f.name;
        const t = document.createElement("span");
        t.className = "selection__taille";
        t.textContent = taille(f.size);
        li.append(fmt, nom, t);
        liste.append(li);
      });

      const problemes = [];
      if (refuses.length) problemes.push(refuses.length + " fichier(s) dans un format non pris en charge. Seuls .csv et .xlsx sont acceptés.");
      if (fichiers.length > maxFichiers) problemes.push("Trop de fichiers : la limite est de " + maxFichiers + ".");
      if (total > maxOctets) problemes.push("Le total (" + taille(total) + ") dépasse la limite de " + taille(maxOctets) + ".");

      selection.hidden = fichiers.length === 0;
      resume.textContent = fichiers.length + " fichier" + (fichiers.length > 1 ? "s" : "") + " sélectionné" + (fichiers.length > 1 ? "s" : "") + ", " + taille(total);
      erreur.hidden = problemes.length === 0;
      erreur.textContent = problemes.join(" ");
      envoyer.disabled = fichiers.length === 0 || problemes.length > 0;
    }

    input.addEventListener("change", afficher);

    ["dragenter", "dragover"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) { e.preventDefault(); zone.classList.add("zone--survol"); });
    });
    ["dragleave", "dragend", "drop"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        if (evt === "dragleave" && zone.contains(e.relatedTarget)) return;
        zone.classList.remove("zone--survol");
      });
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      // On ajoute les fichiers déposés à ceux déjà choisis
      const dt = new DataTransfer();
      Array.from(input.files).forEach(function (f) { dt.items.add(f); });
      Array.from(e.dataTransfer.files).forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;
      afficher();
    });

    vider.addEventListener("click", function () { input.value = ""; afficher(); });

    form.addEventListener("submit", function () {
      envoyer.disabled = true;
      envoyer.classList.add("bouton--attente");
      envoyer.textContent = "Analyse en cours…";
    });
  }

  // ---- Confirmation des actions définitives ----
  document.querySelectorAll("form[data-confirmer]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!window.confirm(f.dataset.confirmer)) e.preventDefault();
    });
  });
})();
