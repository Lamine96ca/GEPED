/* Emploi du temps : dépôt des matières sur la grille, déplacement et étirement des cours, panneau d'un cours.
   Les conflits viennent du serveur ; en cas de conflit, l'utilisateur peut « Enregistrer quand même ». */
(function () {
  'use strict';
  var D = JSON.parse(document.getElementById('edt-donnees').textContent);
  var grille = document.getElementById('edt-grille');
  var colonnes = Array.prototype.slice.call(grille.querySelectorAll('.edt-colonne'));
  var PAS_PX = parseFloat(getComputedStyle(grille).getPropertyValue('--edt-pas')) || 22;
  var csrf = (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value;
  var couleurs = {};
  (D.matieres || []).forEach(function (m) { couleurs[m.id] = m.couleur; });
  var seances = D.seances;

  function heure(min) { var h = Math.floor(min / 60), m = min % 60; return (h < 10 ? '0' : '') + h + 'h' + (m < 10 ? '0' : '') + m; }
  function el(tag, cls, texte) { var e = document.createElement(tag); if (cls) e.className = cls; if (texte != null) e.textContent = texte; return e; }
  function minutesDepuis(y) { var n = Math.round(y / PAS_PX); return Math.max(D.debut, Math.min(D.fin, D.debut + n * D.pas)); }

  // ------------------------------------------------------------------ dessin
  function dessiner() {
    colonnes.forEach(function (c) { c.innerHTML = ''; });
    var parJour = {};
    seances.forEach(function (s) { (parJour[s.jour] = parJour[s.jour] || []).push(s); });
    Object.keys(parJour).forEach(function (jour) {
      var liste = parJour[jour].sort(function (a, b) { return a.debut - b.debut || a.fin - b.fin; });
      // Couloirs : deux cours qui se chevauchent sont côte à côte
      var couloirs = [];
      liste.forEach(function (s) {
        var k = 0;
        while (couloirs[k] && couloirs[k] > s.debut) k++;
        couloirs[k] = s.fin;
        s._couloir = k;
      });
      liste.forEach(function (s) {
        var n = 1;
        liste.forEach(function (o) { if (o !== s && o.debut < s.fin && s.debut < o.fin) n = Math.max(n, o._couloir + 1, s._couloir + 1); });
        s._couloirs = n;
      });
      var col = colonnes[jour - 1];
      liste.forEach(function (s) { col.appendChild(bloc(s)); });
    });
    var nb = seances.filter(function (s) { return s.conflits.length; }).length;
    var alerte = document.getElementById('edt-alerte');
    if (alerte) {
      alerte.classList.toggle('d-none', !nb);
      alerte.classList.toggle('d-flex', !!nb);
      document.getElementById('edt-nb-conflits').textContent = nb;
    }
  }

  function bloc(s) {
    var b = el('div', 'edt-cours edt-c' + (couleurs[s.matiere] != null ? couleurs[s.matiere] : 9));
    if (s.conflits.length) b.classList.add('conflit');
    if (D.modifiable) b.classList.add('modifiable');
    b.style.top = ((s.debut - D.debut) / D.pas * PAS_PX) + 'px';
    b.style.height = ((s.fin - s.debut) / D.pas * PAS_PX - 2) + 'px';
    var largeur = 100 / s._couloirs;
    b.style.left = 'calc(' + (s._couloir * largeur) + '% + 2px)';
    b.style.right = 'auto';
    b.style.width = 'calc(' + largeur + '% - 4px)';
    b.appendChild(el('div', 'titre', s.nom + ' · ' + (s.type_court || s.type)));
    if (D.mode !== 'classe') b.appendChild(el('div', 'info', s.classe));
    b.appendChild(el('div', 'info', heure(s.debut) + '–' + heure(s.fin)));
    if (s.salle && D.mode !== 'salle') b.appendChild(el('div', 'info', 'Salle ' + s.salle.nom + ' (' + s.salle.batiment + ')'));
    if (s.enseignants.length && D.mode !== 'enseignant') b.appendChild(el('div', 'info ens', s.enseignants.map(function (e) { return e.court || e.nom; }).join(', ')));
    var aide = s.nom + ' (' + D.types[s.type] + ')\n' + heure(s.debut) + '–' + heure(s.fin);
    if (s.enseignants.length) aide += '\n' + s.enseignants.map(function (e) { return e.nom; }).join(', ');
    if (s.conflits.length) aide += '\n\nConflit' + (s.force ? ' (forcé)' : '') + ' :\n• ' + s.conflits.join('\n• ');
    b.title = aide;
    b.dataset.id = s.id;
    if (D.modifiable) {
      var p = el('div', 'poignee');
      p.title = 'Étirer pour changer la durée';
      b.appendChild(p);
      activerSouris(b, s, p);
    } else {
      b.addEventListener('click', function () { ouvrir(s); });
    }
    return b;
  }

  // ------------------------------------------------------------------ serveur
  function envoyer(url, corps) {
    return fetch(url, {method: 'POST', credentials: 'same-origin', body: JSON.stringify(corps || {}),
                       headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}})
      .then(function (r) { return r.json().then(function (j) { j._statut = r.status; return j; }); });
  }
  function recharger() {
    return fetch('/emplois/donnees/' + location.search, {credentials: 'same-origin'})
      .then(function (r) { return r.json(); })
      .then(function (j) { seances = j.seances; dessiner(); });
  }
  function corpsDe(s, changements) {
    var c = {matiere: s.matiere, type: s.type, jour: s.jour, debut: s.debut, fin: s.fin,
             salle: s.salle ? s.salle.id : null, enseignants: s.enseignants.map(function (e) { return e.id; })};
    Object.keys(changements || {}).forEach(function (k) { c[k] = changements[k]; });
    return c;
  }
  // Enregistre ; en cas de conflit, demande « Enregistrer quand même ? » (rappel(true) si enregistré)
  function enregistrer(url, corps, rappel) {
    envoyer(url, corps).then(function (j) {
      if (j._statut === 409) {
        demanderForcer(j.conflits, function () {
          corps.forcer = true;
          envoyer(url, corps).then(function (j2) { if (j2.ok) recharger().then(function () { rappel && rappel(true, j2); }); else alert(j2.erreur || 'Erreur'); });
        }, function () { dessiner(); rappel && rappel(false); });
      } else if (j.ok) {
        recharger().then(function () { rappel && rappel(true, j); });
      } else {
        alert(j.erreur || "L'enregistrement a échoué.");
        dessiner();
      }
    }).catch(function () { alert('Connexion perdue : réessayez.'); dessiner(); });
  }
  var modalConflit = document.getElementById('edt-conflit');
  function demanderForcer(conflits, oui, non) {
    var liste = document.getElementById('edt-conflit-liste');
    liste.innerHTML = '';
    var ul = el('ul', 'text-danger small');
    conflits.forEach(function (c) { ul.appendChild(el('li', null, c)); });
    liste.appendChild(ul);
    var m = bootstrap.Modal.getOrCreateInstance(modalConflit);
    var fait = false;
    document.getElementById('edt-conflit-forcer').onclick = function () { fait = true; m.hide(); oui(); };
    document.getElementById('edt-conflit-annuler').onclick = function () { m.hide(); };
    modalConflit.addEventListener('hidden.bs.modal', function h() { modalConflit.removeEventListener('hidden.bs.modal', h); if (!fait) non(); });
    m.show();
  }

  // ------------------------------------------------------------------ dépôt d'une matière (palette → grille)
  var palette = document.getElementById('edt-matieres');
  if (palette) {
    (D.matieres || []).forEach(function (m) {
      var e = el('div', 'edt-matiere edt-c' + m.couleur);
      e.appendChild(document.createTextNode(m.nom));
      if (m.code) { e.appendChild(document.createElement('br')); e.appendChild(el('span', 'code', m.code)); }
      if (D.modifiable) {
        e.classList.add('deplacable');
        e.title = 'Glissez sur la grille pour placer un cours de 2 h';
        glisserMatiere(e, m);
      }
      palette.appendChild(e);
    });
    if (!(D.matieres || []).length) palette.appendChild(el('div', 'small text-secondary', 'Aucune matière pour cette classe.'));
  }

  // Glisser une matière de la palette vers la grille (souris, doigt ou stylet : événements « pointer »)
  function colonneSous(x, y) {
    for (var i = 0; i < colonnes.length; i++) {
      var r = colonnes[i].getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return colonnes[i];
    }
    return null;
  }
  function glisserMatiere(e, m) {
    e.addEventListener('pointerdown', function (ev) {
      if (ev.button !== 0) return;
      ev.preventDefault();
      e.setPointerCapture(ev.pointerId);
      var etiquette = null, fantome = null, cible = null, debut = null;
      function nettoyer() {
        if (etiquette) etiquette.remove();
        if (fantome) fantome.remove();
        colonnes.forEach(function (c) { c.classList.remove('survol'); });
        etiquette = fantome = null;
      }
      function bouger(p) {
        if (!etiquette) {
          etiquette = e.cloneNode(true);
          etiquette.className = e.className + ' edt-etiquette-volante';
          etiquette.style.width = e.offsetWidth + 'px';
          document.body.appendChild(etiquette);
        }
        etiquette.style.left = (p.clientX + 8) + 'px';
        etiquette.style.top = (p.clientY + 8) + 'px';
        var col = colonneSous(p.clientX, p.clientY);
        colonnes.forEach(function (c) { c.classList.toggle('survol', c === col); });
        if (col !== cible && fantome) { fantome.remove(); fantome = null; }
        cible = col;
        if (!col) return;
        debut = Math.min(minutesDepuis(p.clientY - col.getBoundingClientRect().top - PAS_PX / 2), D.fin - D.pas);
        if (!fantome) { fantome = el('div', 'edt-fantome'); col.appendChild(fantome); }
        fantome.style.top = ((debut - D.debut) / D.pas * PAS_PX) + 'px';
        fantome.style.height = (Math.min(120, D.fin - debut) / D.pas * PAS_PX) + 'px';
      }
      function lacher() {
        e.removeEventListener('pointermove', bouger);
        e.removeEventListener('pointerup', lacher);
        e.removeEventListener('pointercancel', annuler);
        var col = cible, d = debut;
        nettoyer();
        if (!col || d == null) return;
        var corps = {matiere: m.id, type: 'CM', jour: parseInt(col.dataset.jour, 10), debut: d,
                     fin: Math.min(d + 120, D.fin), salle: null, enseignants: []};
        enregistrer('/emplois/seances/', corps, function (ok, j) {
          if (ok && j && j.seance) { var s = seances.filter(function (x) { return x.id === j.seance.id; })[0]; if (s) ouvrir(s); }
        });
      }
      function annuler() {
        e.removeEventListener('pointermove', bouger);
        e.removeEventListener('pointerup', lacher);
        e.removeEventListener('pointercancel', annuler);
        nettoyer();
      }
      e.addEventListener('pointermove', bouger);
      e.addEventListener('pointerup', lacher);
      e.addEventListener('pointercancel', annuler);
    });
  }

  // ------------------------------------------------------------------ déplacer et étirer à la souris
  function activerSouris(b, s, poignee) {
    b.addEventListener('pointerdown', function (ev) {
      if (ev.button !== 0) return;
      var etirer = ev.target === poignee;
      var x0 = ev.clientX, y0 = ev.clientY, bouge = false;
      var top0 = parseFloat(b.style.top), h0 = parseFloat(b.style.height);
      var nouveau = {jour: s.jour, debut: s.debut, fin: s.fin};
      b.setPointerCapture(ev.pointerId);
      function mouvement(e) {
        var dx = e.clientX - x0, dy = e.clientY - y0;
        if (!bouge && Math.abs(dx) + Math.abs(dy) < 5) return;
        bouge = true;
        b.classList.add('deplace');
        var pas = Math.round(dy / PAS_PX) * D.pas;
        if (etirer) {
          nouveau.fin = Math.max(s.debut + D.pas, Math.min(D.fin, s.fin + pas));
          b.style.height = ((nouveau.fin - s.debut) / D.pas * PAS_PX - 2) + 'px';
        } else {
          var duree = s.fin - s.debut;
          nouveau.debut = Math.max(D.debut, Math.min(D.fin - duree, s.debut + pas));
          nouveau.fin = nouveau.debut + duree;
          b.style.top = ((nouveau.debut - D.debut) / D.pas * PAS_PX) + 'px';
          colonnes.forEach(function (c, i) {
            var r = c.getBoundingClientRect();
            if (e.clientX >= r.left && e.clientX <= r.right) nouveau.jour = i + 1;
          });
          var decal = colonnes[nouveau.jour - 1].getBoundingClientRect().left - colonnes[s.jour - 1].getBoundingClientRect().left;
          b.style.transform = 'translateX(' + decal + 'px)';
        }
        b.querySelector('.info').textContent = heure(nouveau.debut) + '–' + heure(nouveau.fin);
      }
      function fin() {
        b.removeEventListener('pointermove', mouvement);
        b.removeEventListener('pointerup', fin);
        b.removeEventListener('pointercancel', fin);
        if (!bouge) { ouvrir(s); return; }
        if (nouveau.jour === s.jour && nouveau.debut === s.debut && nouveau.fin === s.fin) { dessiner(); return; }
        enregistrer('/emplois/seances/' + s.id + '/', corpsDe(s, nouveau));
      }
      b.addEventListener('pointermove', mouvement);
      b.addEventListener('pointerup', fin);
      b.addEventListener('pointercancel', fin);
      void top0; void h0;
    });
  }

  // ------------------------------------------------------------------ panneau d'un cours
  var modal = document.getElementById('edt-modal');
  var choix = {};          // enseignants cochés (id → nom)
  var courant = null;
  function options(select, debut, fin) {
    select.innerHTML = '';
    for (var m = debut; m <= fin; m += D.pas) select.appendChild(new Option(heure(m), m));
  }
  function remplirSalles(s) {
    var sel = document.getElementById('edt-salle');
    sel.innerHTML = '';
    sel.appendChild(new Option('— Aucune salle —', ''));
    var assez = [], petites = [];
    (D.salles || []).forEach(function (x) { (x.capacite >= (D.effectif || 0) ? assez : petites).push(x); });
    [[assez, 'Assez grandes (' + (D.effectif || 0) + ' inscrits)'], [petites, 'Trop petites pour la classe']].forEach(function (g) {
      if (!g[0].length) return;
      var og = document.createElement('optgroup');
      og.label = g[1];
      g[0].sort(function (a, b) { return a.capacite - b.capacite; }).forEach(function (x) {
        og.appendChild(new Option(x.batiment + ' · ' + x.nom + ' — ' + x.capacite + ' places (' + x.type + ')', x.id));
      });
      sel.appendChild(og);
    });
    sel.value = s.salle ? s.salle.id : '';
  }
  function puce(id) {
    var p = el('span', 'pill pill-accent d-inline-flex align-items-center gap-1', choix[id]);
    var x = el('button', 'btn-close', null);
    x.type = 'button';
    x.style.fontSize = '.55rem';
    x.setAttribute('aria-label', 'Retirer');
    x.onclick = function () { delete choix[id]; listeEnseignants(); };
    p.appendChild(x);
    return p;
  }
  function listeEnseignants() {
    var zone = document.getElementById('edt-liste-ens');
    var q = document.getElementById('edt-recherche-ens').value.trim().toLowerCase();
    var choisis = document.getElementById('edt-choisis');
    choisis.innerHTML = '';
    Object.keys(choix).forEach(function (id) { choisis.appendChild(puce(id)); });
    if (!Object.keys(choix).length) choisis.appendChild(el('span', 'small text-secondary', 'Aucun enseignant choisi.'));
    zone.innerHTML = '';
    var ens = (D.enseignants || []).filter(function (e) { return !q || (e.nom + ' ' + e.nom_departement).toLowerCase().indexOf(q) >= 0; });
    var duDep = ens.filter(function (e) { return e.departement === D.departement; });
    var autres = ens.filter(function (e) { return e.departement !== D.departement; });
    [[duDep, 'Département de la classe'], [autres, 'Autres départements']].forEach(function (g) {
      if (!g[0].length) return;
      zone.appendChild(el('div', 'groupe', g[1] + ' (' + g[0].length + ')'));
      g[0].sort(function (a, b) { return a.nom.localeCompare(b.nom, 'fr'); }).forEach(function (e) {
        var l = el('label');
        var c = document.createElement('input');
        c.type = 'checkbox';
        c.className = 'form-check-input mt-0';
        c.checked = !!choix[e.id];
        c.onchange = function () { if (c.checked) choix[e.id] = e.nom; else delete choix[e.id]; listeEnseignants(); };
        l.appendChild(c);
        l.appendChild(el('span', null, e.nom));
        l.appendChild(el('span', 'secondaire', (e.grade ? e.grade + ' · ' : '') + e.nom_departement));
        zone.appendChild(l);
      });
    });
    if (!ens.length) zone.appendChild(el('div', 'small text-secondary p-2', 'Aucun enseignant. La Direction des Études les enregistre dans « Enseignants ».'));
  }
  function afficherConflits(liste, forces) {
    var z = document.getElementById('edt-modal-conflits');
    z.innerHTML = '';
    if (!liste.length) return;
    var a = el('div', 'alert alert-danger py-2 small');
    a.appendChild(el('div', 'fw-semibold mb-1', forces ? 'Conflit forcé :' : 'Conflit :'));
    var ul = el('ul', 'mb-0');
    liste.forEach(function (c) { ul.appendChild(el('li', null, c)); });
    a.appendChild(ul);
    z.appendChild(a);
  }
  function ouvrir(s) {
    courant = s;
    document.getElementById('edt-modal-titre').textContent = s.nom + (s.code ? ' (' + s.code + ')' : '');
    document.getElementById('edt-forcer').classList.add('d-none');
    afficherConflits(s.conflits, s.force);
    var champs = document.getElementById('edt-modal-champs'), lecture = document.getElementById('edt-modal-lecture');
    if (!D.modifiable) {
      champs.classList.add('d-none');
      lecture.classList.remove('d-none');
      lecture.innerHTML = '';
      [['Classe', s.classe], ['Type', D.types[s.type]], ['Horaire', D.jours[s.jour - 1] + ' ' + heure(s.debut) + '–' + heure(s.fin)],
       ['Salle', s.salle ? s.salle.nom + ' (' + s.salle.batiment + ', ' + s.salle.capacite + ' places)' : '—'],
       ['Enseignants', s.enseignants.map(function (e) { return e.nom; }).join(', ') || '—']].forEach(function (r) {
        var p = el('div', 'mb-2'); p.appendChild(el('div', 'small text-secondary', r[0])); p.appendChild(el('div', 'fw-semibold', r[1])); lecture.appendChild(p);
      });
      document.getElementById('edt-supprimer').classList.add('d-none');
      document.getElementById('edt-enregistrer').classList.add('d-none');
      bootstrap.Modal.getOrCreateInstance(modal).show();
      return;
    }
    document.getElementById('edt-type').value = s.type;
    document.getElementById('edt-jour').value = s.jour;
    options(document.getElementById('edt-debut'), D.debut, D.fin - D.pas);
    options(document.getElementById('edt-fin'), D.debut + D.pas, D.fin);
    document.getElementById('edt-debut').value = s.debut;
    document.getElementById('edt-fin').value = s.fin;
    remplirSalles(s);
    choix = {};
    s.enseignants.forEach(function (e) { choix[e.id] = e.nom; });
    document.getElementById('edt-recherche-ens').value = '';
    listeEnseignants();
    bootstrap.Modal.getOrCreateInstance(modal).show();
  }
  function lireFormulaire(forcer) {
    return {matiere: courant.matiere, type: document.getElementById('edt-type').value,
            jour: parseInt(document.getElementById('edt-jour').value, 10),
            debut: parseInt(document.getElementById('edt-debut').value, 10),
            fin: parseInt(document.getElementById('edt-fin').value, 10),
            salle: document.getElementById('edt-salle').value || null,
            enseignants: Object.keys(choix).map(Number), forcer: !!forcer};
  }
  function enregistrerPanneau(forcer) {
    var corps = lireFormulaire(forcer);
    if (corps.fin <= corps.debut) { afficherConflits(["L'heure de fin doit être après l'heure de début."], false); return; }
    envoyer('/emplois/seances/' + courant.id + '/', corps).then(function (j) {
      if (j._statut === 409) {
        afficherConflits(j.conflits, false);
        document.getElementById('edt-forcer').classList.remove('d-none');
      } else if (j.ok) {
        bootstrap.Modal.getOrCreateInstance(modal).hide();
        recharger();
      } else {
        afficherConflits([j.erreur || "L'enregistrement a échoué."], false);
      }
    });
  }
  if (D.modifiable) {
    document.getElementById('edt-recherche-ens').addEventListener('input', listeEnseignants);
    document.getElementById('edt-enregistrer').addEventListener('click', function () { enregistrerPanneau(false); });
    document.getElementById('edt-forcer').addEventListener('click', function () { enregistrerPanneau(true); });
    document.getElementById('edt-supprimer').addEventListener('click', function () {
      if (!courant || !confirm('Retirer ce cours de l’emploi du temps ?')) return;
      envoyer('/emplois/seances/' + courant.id + '/supprimer/').then(function () {
        bootstrap.Modal.getOrCreateInstance(modal).hide();
        recharger();
      });
    });
  }

  dessiner();
})();
