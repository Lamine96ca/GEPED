/*
 * gePed — dépôt d'une pièce du dossier (acte de naissance, bac, relevé…).
 * Deux façons : « Scanner » (appareil photo du téléphone ou du PC, une photo par page) ou
 * « Choisir un fichier » (PDF déjà prêt, ou photos).
 * Chaque photo passe par la fenêtre de RECADRAGE (templates/pfe/_modal_recadrage.html) : cadre libre
 * sur les bords du document, rotation ; toucher une page déjà ajoutée permet de la recadrer à
 * nouveau (depuis la photo d'origine). La page est ensuite réduite ici (1 700 px, JPEG) : beaucoup
 * moins de données sur une connexion lente. Le serveur réunit les pages en un seul PDF (pfe/pieces.py).
 * Bloc attendu : [data-pieces] contenant l'input réellement envoyé [data-pieces-envoi].
 */
(function () {
  'use strict';
  const COTE_MAX = 1700, QUALITE = 0.72, PAGES_MAX = 10, PDF_MAX = 3 * 1024 * 1024;
  const transfertPossible = (function () {
    try { return typeof DataTransfer === 'function' && !!new DataTransfer().items; } catch (e) { return false; }
  })();

  function taille(o) { return o < 1048576 ? Math.max(1, Math.round(o / 1024)) + ' Ko' : (o / 1048576).toFixed(1) + ' Mo'; }
  function estImage(f) { return /^image\/(jpeg|png|webp)$/i.test(f.type) || /\.(jpe?g|png|webp)$/i.test(f.name); }

  function enJpeg(canvas, nom) {
    return new Promise(function (ok) {
      canvas.toBlob(function (blob) {
        ok(new File([blob], (nom || 'page').replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' }));
      }, 'image/jpeg', QUALITE);
    });
  }

  // Réduction simple (sans fenêtre de recadrage disponible)
  function reduire(fichier) {
    return new Promise(function (ok) {
      const url = URL.createObjectURL(fichier);
      const img = new Image();
      img.onload = function () {
        const echelle = Math.min(1, COTE_MAX / Math.max(img.naturalWidth, img.naturalHeight));
        const c = document.createElement('canvas');
        c.width = Math.round(img.naturalWidth * echelle);
        c.height = Math.round(img.naturalHeight * echelle);
        const ctx = c.getContext('2d');
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, c.width, c.height);
        ctx.drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        enJpeg(c, fichier.name).then(function (f) { ok(f.size < fichier.size ? f : fichier); });
      };
      img.onerror = function () { URL.revokeObjectURL(url); ok(fichier); };
      img.src = url;
    });
  }

  // ------------------------------------------------------------------ fenêtre de recadrage
  const Recadrage = (function () {
    const el = document.getElementById('modal-recadrage');
    if (!el || !window.bootstrap) return null;
    const modal = bootstrap.Modal.getOrCreateInstance(el);
    const image = el.querySelector('[data-recadrage-image]');
    const numero = el.querySelector('[data-recadrage-numero]');
    const boutonAnnuler = el.querySelector('[data-recadrage="annuler"]');
    let cropper = null, resoudre = null, resultat = null, urlObjet = null, chargement = null;

    function chargerCropper() {
      if (window.Cropper) return Promise.resolve(true);
      if (!chargement) {
        chargement = new Promise(function (ok) {
          const s = document.createElement('script');
          s.src = el.dataset.cropperSrc;
          s.onload = function () { ok(true); };
          s.onerror = function () { chargement = null; ok(false); };
          document.head.appendChild(s);
        });
      }
      return chargement;
    }

    // La page n'est rendue qu'une fois la fenêtre complètement fermée : la suivante peut alors s'ouvrir
    function fermer(page) {
      resultat = page;
      modal.hide();
    }

    el.addEventListener('hidden.bs.modal', function () {
      if (cropper) { cropper.destroy(); cropper = null; }
      if (urlObjet) { URL.revokeObjectURL(urlObjet); urlObjet = null; }
      image.removeAttribute('src');
      const r = resoudre, page = resultat;
      resoudre = resultat = null;
      if (r) r(page);
    });
    el.addEventListener('click', function (ev) {
      const b = ev.target.closest('[data-recadrage]');
      if (!b) return;
      const a = b.dataset.recadrage;
      if (a === 'annuler') fermer(null);
      else if (!cropper) return;
      else if (a === 'gauche') cropper.rotate(-90);
      else if (a === 'droite') cropper.rotate(90);
      else if (a === 'tout') {
        const d = cropper.getCanvasData();
        cropper.setCropBoxData({ left: d.left, top: d.top, width: d.width, height: d.height });
      } else if (a === 'valider') {
        const canvas = cropper.getCroppedCanvas({ maxWidth: COTE_MAX, maxHeight: COTE_MAX, fillColor: '#fff',
          imageSmoothingEnabled: true, imageSmoothingQuality: 'high' });
        if (!canvas) { fermer(null); return; }
        enJpeg(canvas, 'page').then(fermer);
      }
    });

    // Ouvre la fenêtre sur `fichier` (photo d'origine) ; renvoie la page recadrée (File JPEG) ou null
    async function ouvrir(fichier, libelle, nouvelle) {
      if (!(await chargerCropper())) return null;
      numero.textContent = libelle || '';
      boutonAnnuler.textContent = nouvelle ? 'Ne pas ajouter' : 'Annuler';
      return new Promise(function (ok) {
        resoudre = ok;
        urlObjet = URL.createObjectURL(fichier);
        image.src = urlObjet;
        el.addEventListener('shown.bs.modal', function demarrer() {
          el.removeEventListener('shown.bs.modal', demarrer);
          cropper = new Cropper(image, {
            viewMode: 1, dragMode: 'move', autoCropArea: 0.9, responsive: true, background: false,
            toggleDragModeOnDblclick: false, checkOrientation: true
          });
        });
        modal.show();
      });
    }
    return { ouvrir: ouvrir };
  })();

  // ------------------------------------------------------------------ bloc de dépôt
  function initialiser(bloc) {
    const envoi = bloc.querySelector('[data-pieces-envoi]');
    const camera = bloc.querySelector('[data-pieces-entree-camera]');
    const choix = bloc.querySelector('[data-pieces-entree-fichier]');
    const liste = bloc.querySelector('[data-pieces-liste]');
    const info = bloc.querySelector('[data-pieces-info]');
    if (!envoi) return;
    if (!transfertPossible) {                         // vieux navigateur : simple champ fichier
      envoi.classList.remove('visually-hidden');
      bloc.querySelectorAll('[data-pieces-camera],[data-pieces-fichier]').forEach(function (b) { b.hidden = true; });
      return;
    }
    let pages = [];                                   // { fichier envoyé, original (photo d'origine) }
    let occupe = false;

    function estPdf() { return pages.length && pages[0].fichier.type === 'application/pdf'; }

    function synchroniser() {
      const dt = new DataTransfer();
      pages.forEach(function (p) { dt.items.add(p.fichier); });
      envoi.files = dt.files;
      liste.innerHTML = '';
      pages.forEach(function (p, i) {
        const el = document.createElement('div');
        el.className = 'page-piece';
        if (p.fichier.type === 'application/pdf') {
          el.innerHTML = '<i class="bi bi-file-earmark-pdf"></i>';
        } else {
          el.style.backgroundImage = 'url(' + URL.createObjectURL(p.fichier) + ')';
          if (p.original && Recadrage) {
            el.classList.add('recadrable');
            el.title = 'Toucher pour recadrer';
            el.addEventListener('click', async function (ev) {
              if (ev.target.closest('.retirer') || occupe) return;
              occupe = true;
              const f = await Recadrage.ouvrir(p.original, (i + 1) + ' / ' + pages.length, false);
              occupe = false;
              if (f) { p.fichier = f; synchroniser(); }
            });
          }
        }
        const nom = document.createElement('span');
        nom.className = 'legende';
        nom.textContent = (p.fichier.type === 'application/pdf' ? 'PDF' : 'Page ' + (i + 1)) + ' · ' + taille(p.fichier.size);
        const retirer = document.createElement('button');
        retirer.type = 'button';
        retirer.className = 'retirer';
        retirer.title = 'Retirer';
        retirer.innerHTML = '<i class="bi bi-x-lg"></i>';
        retirer.addEventListener('click', function () { pages.splice(i, 1); synchroniser(); });
        el.appendChild(nom);
        el.appendChild(retirer);
        liste.appendChild(el);
      });
      const total = pages.reduce(function (s, p) { return s + p.fichier.size; }, 0);
      bloc.classList.toggle('a-des-pages', pages.length > 0);
      if (info) {
        info.textContent = pages.length
          ? pages.length + (estPdf() ? ' PDF' : ' page' + (pages.length > 1 ? 's' : '')) + ' · ' + taille(total) +
            (estPdf() ? '' : ' · touchez une page pour la recadrer, ou ajoutez-en d\'autres')
          : (info.dataset.defaut || '');
      }
      envoi.dispatchEvent(new Event('change', { bubbles: true }));
    }

    async function ajouter(fichiers) {
      if (occupe) return;
      occupe = true;
      try {
        for (const f of fichiers) {
          if (f.type === 'application/pdf' || /\.pdf$/i.test(f.name)) {
            if (f.size > PDF_MAX) { alert('PDF trop lourd (' + taille(f.size) + ') : 3 Mo au plus. Scannez en noir et blanc, ou prenez les pages en photo.'); continue; }
            pages = [{ fichier: f, original: null }];        // un PDF remplace les pages
            continue;
          }
          if (estPdf()) pages = [];
          if (pages.length >= PAGES_MAX) { alert(PAGES_MAX + ' pages au plus.'); break; }
          if (!estImage(f)) { pages.push({ fichier: f, original: null }); continue; }
          let page = null;
          if (Recadrage) {
            page = await Recadrage.ouvrir(f, (pages.length + 1) + '', true);
            if (!page) continue;                             // « Ne pas ajouter »
          } else {
            if (info) info.textContent = 'Réduction de la photo…';
            page = await reduire(f);
          }
          pages.push({ fichier: page, original: f });
        }
      } finally {
        occupe = false;
        synchroniser();
      }
    }

    if (info) info.dataset.defaut = info.textContent;
    bloc.querySelector('[data-pieces-camera]')?.addEventListener('click', function () { camera.click(); });
    bloc.querySelector('[data-pieces-fichier]')?.addEventListener('click', function () { choix.click(); });
    [camera, choix].forEach(function (entree) {
      entree?.addEventListener('change', function () { const f = [...entree.files]; entree.value = ''; ajouter(f); });
    });
  }

  document.querySelectorAll('[data-pieces]').forEach(initialiser);
})();
