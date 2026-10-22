/*
 * gePed — confort sur une connexion faible ou instable (chargé sur toutes les pages) :
 *  1. barre de progression en haut de l'écran pendant le chargement d'une page (après 0,3 s) ;
 *     au bout de 8 s, message « Connexion lente, merci de patienter… » ;
 *  2. boutons protégés contre le double clic : après l'envoi d'un formulaire, ses boutons se grisent
 *     et affichent « Patientez… » (évite deux inscriptions, deux attestations…) ;
 *  3. bandeau « hors connexion » quand Internet coupe, puis « connexion rétablie ».
 * Ne s'applique pas aux liens et formulaires qui ouvrent un nouvel onglet ou un téléchargement
 * (PDF, Excel), ni à ceux marqués data-sans-attente.
 */
(function () {
  'use strict';
  var DELAI_BARRE = 300, DELAI_LENT = 8000, DELAI_ABANDON = 60000;

  // ------------------------------------------------------------ éléments
  function creer(classe, html) {
    var el = document.createElement('div');
    el.className = classe;
    el.innerHTML = html || '';
    document.body.appendChild(el);
    return el;
  }
  var barre = creer('gped-barre', '<span></span>');
  var lenteur = creer('gped-lenteur', '<span class="spinner-border spinner-border-sm"></span> Connexion lente, merci de patienter…');
  var horsLigne = creer('gped-hors-ligne', '');
  barre.setAttribute('aria-hidden', 'true');
  lenteur.setAttribute('role', 'status');
  horsLigne.setAttribute('role', 'alert');

  var minuteurs = [];
  function arreter() {
    minuteurs.forEach(clearTimeout);
    minuteurs = [];
    barre.classList.remove('active', 'fin');
    lenteur.classList.remove('visible');
  }
  function demarrer() {
    arreter();
    minuteurs.push(setTimeout(function () { barre.classList.add('active'); }, DELAI_BARRE));
    minuteurs.push(setTimeout(function () { lenteur.classList.add('visible'); }, DELAI_LENT));
    // Page qui ne répond jamais (coupure) : on rend la main
    minuteurs.push(setTimeout(function () { arreter(); reactiverBoutons(); }, DELAI_ABANDON));
  }
  // Retour arrière (page servie depuis le cache) : tout est remis à zéro
  window.addEventListener('pageshow', function () { arreter(); reactiverBoutons(); });

  // Utilisable par les autres scripts (ex. liste des étudiants chargée en arrière-plan)
  window.gpedReseau = {
    lente: function (oui) { lenteur.classList.toggle('visible', !!oui); }
  };

  // ------------------------------------------------------------ 1. navigation
  function ouvreAilleurs(el) {
    var cible = el.getAttribute('target');
    return (cible && cible !== '_self') || el.hasAttribute('download') || el.hasAttribute('data-sans-attente');
  }
  function telechargement(url) {
    return /[?&](export|format)=|\.pdf(\?|$)|\.xlsx(\?|$)|\/modele\/|\/pdf\/|fiche\.pdf/.test(url);
  }
  document.addEventListener('click', function (ev) {
    if (ev.defaultPrevented || ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
    var a = ev.target.closest('a[href]');
    if (!a || ouvreAilleurs(a)) return;
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#' || /^(javascript|mailto|tel):/i.test(href)) return;
    if (a.origin !== window.location.origin || telechargement(a.href)) return;
    if (a.pathname === window.location.pathname && a.search === window.location.search && a.hash) return;
    demarrer();
  });

  // ------------------------------------------------------------ 2. formulaires
  function reactiverBoutons() {
    document.querySelectorAll('[data-gped-attente]').forEach(function (b) {
      b.disabled = false;
      if (b.dataset.gpedTexte !== undefined) b.innerHTML = b.dataset.gpedTexte;
      b.removeAttribute('data-gped-attente');
    });
    document.querySelectorAll('input[data-gped-soumetteur]').forEach(function (i) { i.remove(); });
  }
  document.addEventListener('submit', function (ev) {
    var form = ev.target;
    // Envoi annulé (confirmation refusée, formulaire invalide…) : rien à faire
    if (ev.defaultPrevented || form.hasAttribute('data-sans-attente')) return;
    var nouvelOnglet = ouvreAilleurs(form);
    var url = form.getAttribute('action') || window.location.href;
    if (!nouvelOnglet && !telechargement(url) && (form.method || 'get').toLowerCase() === 'post') demarrer();
    if ((form.method || 'get').toLowerCase() !== 'post') return;       // une recherche peut être relancée
    // Le bouton cliqué garde sa valeur (ex. action=verrouiller) même une fois désactivé
    var soumetteur = ev.submitter;
    if (soumetteur && soumetteur.name) {
      var cache = document.createElement('input');
      cache.type = 'hidden';
      cache.name = soumetteur.name;
      cache.value = soumetteur.value;
      cache.setAttribute('data-gped-soumetteur', '');
      form.appendChild(cache);
    }
    setTimeout(function () {             // après l'envoi effectif (les valeurs sont déjà prises)
      form.querySelectorAll('button[type=submit], button:not([type]), input[type=submit]').forEach(function (b) {
        if (b.disabled) return;
        b.setAttribute('data-gped-attente', '');
        b.disabled = true;
        if (b.tagName === 'BUTTON' && b === soumetteur) {
          b.dataset.gpedTexte = b.innerHTML;
          b.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Patientez…';
        }
      });
      // Nouvel onglet (PDF…) : la page reste affichée, on rend la main après quelques secondes
      if (nouvelOnglet) setTimeout(reactiverBoutons, 4000);
    }, 0);
  });

  // ------------------------------------------------------------ 3. hors connexion
  var minuteurRetour = null;
  function etat() {
    clearTimeout(minuteurRetour);
    if (!navigator.onLine) {
      horsLigne.className = 'gped-hors-ligne visible';
      horsLigne.innerHTML = '<i class="bi bi-wifi-off"></i> Vous êtes hors connexion : vos modifications ne seront pas envoyées tant qu\'Internet n\'est pas revenu.';
    } else if (horsLigne.classList.contains('visible')) {
      horsLigne.className = 'gped-hors-ligne visible retour';
      horsLigne.innerHTML = '<i class="bi bi-wifi"></i> Connexion rétablie.';
      minuteurRetour = setTimeout(function () { horsLigne.className = 'gped-hors-ligne'; }, 3000);
    }
  }
  window.addEventListener('offline', etat);
  window.addEventListener('online', etat);
  if (!navigator.onLine) etat();
})();
