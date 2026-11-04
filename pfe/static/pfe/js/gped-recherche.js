/*
 * Recherche des fonctionnalités (bandeau) : le catalogue reçu (#rf-catalogue) ne contient que les pages
 * que l'utilisateur peut ouvrir (pfe/fonctionnalites.py). Accents et majuscules ignorés ; tous les mots
 * tapés doivent se trouver dans le titre, la rubrique, l'explication ou les mots-clés.
 * Clavier : Ctrl + K (ou « / ») pour chercher, flèches pour choisir, Entrée pour ouvrir, Échap pour fermer.
 */
(function () {
  'use strict';
  var racine = document.querySelector('[data-recherche-fonctions]');
  var source = document.getElementById('rf-catalogue');
  if (!racine || !source) return;

  var champ = racine.querySelector('#rf-champ');
  var liste = racine.querySelector('#rf-liste');
  var MAX = 8;
  var VIDES = ['de', 'du', 'des', 'la', 'le', 'les', 'un', 'une', 'd', 'l', 'et', 'a', 'en', 'pour', 'au', 'aux', 'mon', 'ma', 'mes'];
  var resultats = [];
  var choisi = -1;

  function normaliser(texte) {
    return (texte || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  }

  var catalogue = JSON.parse(source.textContent).map(function (f) {
    f.titreNorm = ' ' + normaliser(f.t);
    f.texteNorm = normaliser([f.t, f.r, f.d, f.k].join(' '));
    return f;
  });

  function chercher(texte) {
    var mots = normaliser(texte).split(/[\s'’\-]+/).filter(function (m) { return m && VIDES.indexOf(m) === -1; });
    if (!mots.length) return [];
    var trouves = [];
    catalogue.forEach(function (f, rang) {
      var score = 0;
      for (var i = 0; i < mots.length; i++) {
        var m = mots[i];
        if (f.texteNorm.indexOf(m) === -1) return;
        score += f.titreNorm.indexOf(' ' + m) !== -1 ? 3 : (f.titreNorm.indexOf(m) !== -1 ? 2 : 1);
      }
      trouves.push({ f: f, score: score, rang: rang });
    });
    trouves.sort(function (a, b) { return b.score - a.score || a.rang - b.rang; });
    return trouves.slice(0, MAX).map(function (t) { return t.f; });
  }

  function element(balise, classe, texte) {
    var e = document.createElement(balise);
    if (classe) e.className = classe;
    if (texte) e.textContent = texte;
    return e;
  }

  function afficher() {
    liste.textContent = '';
    choisi = -1;
    var texte = champ.value.trim();
    if (!texte) { fermerListe(); return; }
    resultats = chercher(texte);
    if (!resultats.length) {
      liste.appendChild(element('li', 'rf-vide', 'Aucune fonctionnalité ne correspond à « ' + texte + ' ».'));
    }
    resultats.forEach(function (f, i) {
      var li = element('li');
      li.setAttribute('role', 'presentation');
      var a = element('a', 'rf-resultat');
      a.href = f.u;
      a.id = 'rf-option-' + i;
      a.setAttribute('role', 'option');
      a.appendChild(element('i', 'bi ' + f.i));
      var corps = element('span', 'rf-corps');
      var haut = element('span', 'rf-haut');
      haut.appendChild(element('span', 'rf-titre', f.t));
      haut.appendChild(element('span', 'rf-rubrique', f.r));
      corps.appendChild(haut);
      corps.appendChild(element('span', 'rf-explication', f.d));
      a.appendChild(corps);
      a.addEventListener('mousemove', function () { choisir(i); });
      li.appendChild(a);
      liste.appendChild(li);
    });
    liste.hidden = false;
    champ.setAttribute('aria-expanded', 'true');
    if (resultats.length) choisir(0);
  }

  function choisir(i) {
    var options = liste.querySelectorAll('.rf-resultat');
    if (!options.length) return;
    choisi = (i + options.length) % options.length;
    options.forEach(function (o, k) {
      o.classList.toggle('actif', k === choisi);
      o.setAttribute('aria-selected', k === choisi ? 'true' : 'false');
    });
    champ.setAttribute('aria-activedescendant', options[choisi].id);
    options[choisi].scrollIntoView({ block: 'nearest' });
  }

  function fermerListe() {
    liste.hidden = true;
    champ.setAttribute('aria-expanded', 'false');
    champ.removeAttribute('aria-activedescendant');
  }

  function ouvrir() {
    racine.classList.add('ouverte');
    champ.focus();
    champ.select();
  }

  function fermer() {
    fermerListe();
    racine.classList.remove('ouverte');
  }

  champ.addEventListener('input', afficher);
  champ.addEventListener('focus', function () { if (champ.value.trim()) afficher(); });
  champ.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); if (liste.hidden) afficher(); else choisir(choisi + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); choisir(choisi - 1); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      if (choisi >= 0 && resultats[choisi]) window.location.href = resultats[choisi].u;
    } else if (e.key === 'Escape') {
      if (!liste.hidden && champ.value) { champ.value = ''; fermerListe(); } else { fermer(); champ.blur(); }
    }
  });

  racine.querySelector('[data-rf-ouvrir]').addEventListener('click', ouvrir);
  racine.querySelector('[data-rf-fermer]').addEventListener('click', fermer);
  document.addEventListener('click', function (e) { if (!racine.contains(e.target)) fermer(); });
  document.addEventListener('keydown', function (e) {
    var dansUnChamp = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); ouvrir(); }
    else if (e.key === '/' && !dansUnChamp) { e.preventDefault(); ouvrir(); }
  });
})();
