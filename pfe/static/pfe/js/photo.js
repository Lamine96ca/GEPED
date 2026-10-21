/*
 * Fenêtre photo gePed (templates/pfe/_modal_photo.html)
 *  - « Prendre une photo » : appareil photo natif sur téléphone ; webcam en direct sur PC
 *    (la webcam exige HTTPS ou localhost : sinon, message et chargement de fichier) ;
 *  - « Charger une photo » : galerie ou fichier ;
 *  - recadrage au format identité 3:4 (Cropper.js), rotation, zoom ;
 *  - compression : 600 × 800 px maximum, JPEG, qualité réduite par paliers jusqu'à 350 Ko.
 *
 * Ouverture depuis un élément [data-photo-ouvrir] :
 *   data-photo-url       → enregistrement immédiat (POST « photo » ou « retirer », réponse JSON {url})
 *   data-photo-input     → sinon : id de l'<input type=file> du formulaire à remplir
 *   data-photo-retirer   → id de la case « retirer la photo » du formulaire
 *   data-photo-cible     → sélecteur des aperçus à mettre à jour (fond = photo)
 *   data-photo-actuelle  → « 1 » si une photo existe (affiche « Retirer »)
 *   data-photo-titre     → titre de la fenêtre
 */
(function () {
  'use strict';
  var LARGEUR = 600, HAUTEUR = 800, POIDS_MAX = 350 * 1024;
  var QUALITES = [0.88, 0.82, 0.76, 0.7, 0.64, 0.58, 0.52];

  var modalEl = document.getElementById('modal-photo');
  if (!modalEl || !window.bootstrap) return;
  var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  var q = function (s) { return modalEl.querySelector(s); };
  var etapes = modalEl.querySelectorAll('[data-etape]');
  var zoneErreur = q('[data-photo-erreur]');
  var video = q('[data-photo-video]');
  var image = q('[data-photo-image]');
  var statut = q('[data-photo-statut]');
  var inputCamera = q('[data-photo-input-camera]');
  var inputFichier = q('[data-photo-input-fichier]');
  var estMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) ||
                  (navigator.maxTouchPoints > 1 && /Macintosh/.test(navigator.userAgent));   // iPad récent
  var config = null, flux = null, cropper = null, urlObjet = null, cameras = [], indexCamera = 0;

  function afficher(nom) {
    etapes.forEach(function (e) { e.classList.toggle('d-none', e.dataset.etape !== nom); });
  }
  function erreur(message) {
    zoneErreur.textContent = message || '';
    zoneErreur.classList.toggle('d-none', !message);
  }
  function arreterCamera() {
    if (flux) { flux.getTracks().forEach(function (t) { t.stop(); }); flux = null; }
    video.srcObject = null;
  }
  function nettoyer() {
    arreterCamera();
    if (cropper) { cropper.destroy(); cropper = null; }
    if (urlObjet) { URL.revokeObjectURL(urlObjet); urlObjet = null; }
  }
  function jeton() { return modalEl.querySelector('[name=csrfmiddlewaretoken]').value; }

  // ------------------------------------------------------------ ouverture
  document.addEventListener('click', function (ev) {
    var d = ev.target.closest('[data-photo-ouvrir]');
    if (!d) return;
    ev.preventDefault();
    config = {
      declencheur: d,
      url: d.dataset.photoUrl || null,
      input: d.dataset.photoInput ? document.getElementById(d.dataset.photoInput) : null,
      retirer: d.dataset.photoRetirer ? document.getElementById(d.dataset.photoRetirer) : null,
      cible: d.dataset.photoCible || null
    };
    q('[data-photo-titre]').textContent = d.dataset.photoTitre || 'Photo';
    q('[data-action="retirer"]').classList.toggle('d-none', d.dataset.photoActuelle !== '1');
    erreur('');
    afficher('choix');
    modal.show();
  });
  modalEl.addEventListener('hidden.bs.modal', nettoyer);

  modalEl.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-action]');
    if (!b) return;
    var a = b.dataset.action;
    if (a === 'prendre') prendre();
    else if (a === 'charger') { inputFichier.value = ''; inputFichier.click(); }
    else if (a === 'capturer') capturer();
    else if (a === 'basculer') { indexCamera = (indexCamera + 1) % cameras.length; demarrerCamera(); }
    else if (a === 'retour') { nettoyer(); erreur(''); afficher('choix'); }
    else if (a === 'rotation-g' && cropper) cropper.rotate(-90);
    else if (a === 'rotation-d' && cropper) cropper.rotate(90);
    else if (a === 'zoom-plus' && cropper) cropper.zoom(0.1);
    else if (a === 'zoom-moins' && cropper) cropper.zoom(-0.1);
    else if (a === 'valider') valider();
    else if (a === 'retirer') retirer();
  });

  // ------------------------------------------------------------ prise de vue
  function prendre() {
    erreur('');
    // Téléphone : appareil photo natif (fonctionne aussi sans HTTPS)
    if (estMobile || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      inputCamera.value = '';
      inputCamera.click();
      return;
    }
    if (!window.isSecureContext) {
      erreur("Sur ordinateur, la webcam n'est disponible qu'en HTTPS (ou sur le poste serveur). " +
             "Choisissez « Charger une photo », ou utilisez un téléphone.");
      return;
    }
    demarrerCamera();
  }

  function demarrerCamera() {
    arreterCamera();
    var video_ = cameras[indexCamera]
      ? { deviceId: { exact: cameras[indexCamera].deviceId }, width: { ideal: 1280 }, height: { ideal: 960 } }
      : { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 960 } };
    navigator.mediaDevices.getUserMedia({ audio: false, video: video_ }).then(function (f) {
      flux = f;
      video.srcObject = f;
      afficher('camera');
      return cameras.length ? null : navigator.mediaDevices.enumerateDevices().then(function (liste) {
        cameras = liste.filter(function (x) { return x.kind === 'videoinput'; });
      });
    }).then(function () {
      q('[data-action="basculer"]').classList.toggle('d-none', cameras.length < 2);
    }).catch(function (e) {
      arreterCamera();
      afficher('choix');
      erreur(e && e.name === 'NotAllowedError'
        ? "Accès à la caméra refusé : autorisez-le dans le navigateur, ou chargez une photo."
        : e && (e.name === 'NotFoundError' || e.name === 'OverconstrainedError')
          ? "Aucune caméra détectée sur cet appareil : chargez une photo."
          : "Impossible d'utiliser la caméra (elle est peut-être utilisée par une autre application) : chargez une photo.");
    });
  }

  function capturer() {
    if (!video.videoWidth) return;
    var c = document.createElement('canvas');
    c.width = video.videoWidth;
    c.height = video.videoHeight;
    c.getContext('2d').drawImage(video, 0, 0);       // sens réel (l'aperçu, lui, est en miroir)
    arreterCamera();
    c.toBlob(function (b) { recadrer(b); }, 'image/jpeg', 0.95);
  }

  [inputCamera, inputFichier].forEach(function (inp) {
    inp.addEventListener('change', function () {
      var f = inp.files[0];
      if (!f) return;
      if (f.type && f.type.indexOf('image/') !== 0) { erreur("Ce fichier n'est pas une image."); return; }
      recadrer(f);
    });
  });

  // ------------------------------------------------------------ recadrage
  function recadrer(blob) {
    nettoyer();
    erreur('');
    urlObjet = URL.createObjectURL(blob);
    afficher('recadrage');
    image.onload = function () {
      cropper = new Cropper(image, {
        aspectRatio: 3 / 4, viewMode: 1, dragMode: 'move', autoCropArea: 0.92,
        background: false, responsive: true, checkOrientation: false   // le navigateur applique déjà l'EXIF
      });
    };
    image.onerror = function () {
      afficher('choix');
      erreur("Ce navigateur ne sait pas lire cette image : essayez une photo JPG ou PNG.");
    };
    image.src = urlObjet;
  }

  // ------------------------------------------------------------ compression
  function versBlob(canvas, qualite) {
    return new Promise(function (ok) { canvas.toBlob(ok, 'image/jpeg', qualite); });
  }
  function compresser(canvas) {
    var i = 0;
    function essai() {
      if (i >= QUALITES.length) {                    // très rare : on réduit la taille et on recommence
        var r = document.createElement('canvas');
        r.width = Math.round(canvas.width * 0.85);
        r.height = Math.round(canvas.height * 0.85);
        r.getContext('2d').drawImage(canvas, 0, 0, r.width, r.height);
        canvas = r; i = 0;
      }
      return versBlob(canvas, QUALITES[i++]).then(function (b) { return b && b.size <= POIDS_MAX ? b : essai(); });
    }
    return essai();
  }

  function valider() {
    if (!cropper) return;
    var canvas = cropper.getCroppedCanvas({
      maxWidth: LARGEUR, maxHeight: HAUTEUR, fillColor: '#fff',
      imageSmoothingEnabled: true, imageSmoothingQuality: 'high'
    });
    afficher('envoi');
    statut.textContent = 'Compression de la photo…';
    compresser(canvas).then(function (blob) {
      var poids = Math.max(1, Math.round(blob.size / 1024)) + ' Ko';
      if (config.url) {
        statut.textContent = 'Enregistrement de la photo (' + poids + ')…';
        var fd = new FormData();
        fd.append('photo', blob, 'photo.jpg');
        poster(fd, 'Photo enregistrée (' + poids + ').');
      } else {
        placerDansFormulaire(blob, poids);
      }
    });
  }

  // ------------------------------------------------------------ résultat
  function majApercus(url) {
    var cibles = config.cible ? document.querySelectorAll(config.cible) : [];
    cibles.forEach(function (el) {
      el.style.backgroundImage = url ? 'url("' + url + '")' : '';
      el.classList.toggle('a-photo', !!url);
    });
    config.declencheur.dataset.photoActuelle = url ? '1' : '0';
    document.querySelectorAll('[data-photo-ouvrir]').forEach(function (d) {
      if (d.dataset.photoUrl && d.dataset.photoUrl === config.url) d.dataset.photoActuelle = url ? '1' : '0';
    });
  }
  function info(texte) {
    var el = document.querySelector('[data-photo-info]');
    if (el) el.textContent = texte;
  }

  function placerDansFormulaire(blob, poids) {
    var fichier = new File([blob], 'photo.jpg', { type: 'image/jpeg' });
    var dt = new DataTransfer();
    dt.items.add(fichier);
    config.input.files = dt.files;
    if (config.retirer) config.retirer.checked = false;
    majApercus(URL.createObjectURL(blob));
    info('Nouvelle photo prête (' + poids + ') : elle sera enregistrée avec le formulaire.');
    modal.hide();
  }

  function poster(fd, message) {
    fetch(config.url, {
      method: 'POST', body: fd, credentials: 'same-origin',
      headers: { 'X-CSRFToken': jeton(), 'X-Requested-With': 'XMLHttpRequest' }
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.erreur || ('Enregistrement impossible (erreur ' + r.status + ').'));
        return d;
      });
    }).then(function (d) {
      majApercus(d.url ? d.url + '?v=' + Date.now() : null);
      info(message);
      modal.hide();
    }).catch(function (e) {
      afficher('choix');
      erreur(e.message);
    });
  }

  function retirer() {
    if (!window.confirm('Retirer la photo ?')) return;
    if (config.url) {
      afficher('envoi');
      statut.textContent = 'Suppression de la photo…';
      var fd = new FormData();
      fd.append('retirer', '1');
      poster(fd, 'Photo retirée.');
    } else {
      config.input.value = '';
      if (config.retirer) config.retirer.checked = true;
      majApercus(null);
      info("La photo sera retirée à l'enregistrement du formulaire.");
      modal.hide();
    }
  }
})();
