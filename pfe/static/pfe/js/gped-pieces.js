/*
 * gePed — dépôt d'une pièce du dossier (acte de naissance, bac, relevé…).
 * Deux façons : « Scanner » (appareil photo du téléphone ou du PC, une photo par page) ou
 * « Choisir un fichier » (PDF déjà prêt, ou photos). Les photos sont réduites ici, avant l'envoi
 * (1 700 px, JPEG) : beaucoup moins de données sur une connexion lente. Le serveur les réunit en
 * un seul PDF compressé (voir pfe/pieces.py).
 * Bloc attendu : [data-pieces] contenant l'input réellement envoyé [data-pieces-envoi].
 */
(function () {
  'use strict';
  const COTE_MAX = 1700, QUALITE = 0.72, PAGES_MAX = 10, PDF_MAX = 3 * 1024 * 1024;
  const transfertPossible = (function () {
    try { return typeof DataTransfer === 'function' && !!new DataTransfer().items; } catch (e) { return false; }
  })();

  function reduire(fichier) {
    // Photo → JPEG réduit ; en cas d'échec (format inconnu), le fichier part tel quel
    return new Promise(function (ok) {
      if (!/^image\/(jpeg|png|webp)$/i.test(fichier.type)) { ok(fichier); return; }
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
        c.toBlob(function (blob) {
          ok(blob && blob.size < fichier.size
            ? new File([blob], fichier.name.replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' }) : fichier);
        }, 'image/jpeg', QUALITE);
      };
      img.onerror = function () { URL.revokeObjectURL(url); ok(fichier); };
      img.src = url;
    });
  }

  function taille(o) { return o < 1048576 ? Math.max(1, Math.round(o / 1024)) + ' Ko' : (o / 1048576).toFixed(1) + ' Mo'; }

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
    let pages = [];

    function synchroniser() {
      const dt = new DataTransfer();
      pages.forEach(function (f) { dt.items.add(f); });
      envoi.files = dt.files;
      liste.innerHTML = '';
      pages.forEach(function (f, i) {
        const el = document.createElement('div');
        el.className = 'page-piece';
        if (f.type === 'application/pdf') {
          el.innerHTML = '<i class="bi bi-file-earmark-pdf"></i>';
        } else {
          const u = URL.createObjectURL(f);
          el.style.backgroundImage = 'url(' + u + ')';
        }
        const nom = document.createElement('span');
        nom.className = 'legende';
        nom.textContent = (f.type === 'application/pdf' ? 'PDF' : 'Page ' + (i + 1)) + ' · ' + taille(f.size);
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
      const total = pages.reduce(function (s, f) { return s + f.size; }, 0);
      bloc.classList.toggle('a-des-pages', pages.length > 0);
      if (info) {
        info.textContent = pages.length
          ? pages.length + (pages[0].type === 'application/pdf' ? ' PDF' : ' page' + (pages.length > 1 ? 's' : '')) +
            ' · ' + taille(total) + (pages[0].type === 'application/pdf' ? '' : ' · vous pouvez ajouter d\'autres pages')
          : (info.dataset.defaut || '');
      }
      envoi.dispatchEvent(new Event('change', { bubbles: true }));
    }

    async function ajouter(fichiers) {
      for (const f of fichiers) {
        if (f.type === 'application/pdf' || /\.pdf$/i.test(f.name)) {
          if (f.size > PDF_MAX) { alert('PDF trop lourd (' + taille(f.size) + ') : 3 Mo au plus. Scannez en noir et blanc, ou prenez les pages en photo.'); continue; }
          pages = [f];                                // un PDF remplace les pages
          continue;
        }
        if (pages.length && pages[0].type === 'application/pdf') pages = [];
        if (pages.length >= PAGES_MAX) { alert(PAGES_MAX + ' pages au plus.'); break; }
        if (info) info.textContent = 'Réduction de la photo…';
        pages.push(await reduire(f));
      }
      synchroniser();
    }

    if (info) info.dataset.defaut = info.textContent;
    bloc.querySelector('[data-pieces-camera]')?.addEventListener('click', function () { camera.click(); });
    bloc.querySelector('[data-pieces-fichier]')?.addEventListener('click', function () { choix.click(); });
    [camera, choix].forEach(function (entree) {
      entree?.addEventListener('change', function () { ajouter([...entree.files]); entree.value = ''; });
    });
  }

  document.querySelectorAll('[data-pieces]').forEach(initialiser);
})();
