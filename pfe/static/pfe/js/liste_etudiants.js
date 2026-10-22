/*
 * Liste des étudiants gePed : pagination, tri, recherche et filtres côté serveur.
 * Le navigateur ne reçoit qu'une page à la fois (25 à 500 lignes) : la liste reste
 * fluide quel que soit le nombre d'étudiants (10 000 et plus).
 */
(function () {
  'use strict';

  const cfg = window.GPED;
  const form = document.getElementById('filtres');
  const corps = document.getElementById('corps');
  const tableau = document.getElementById('tableau');
  const info = document.getElementById('info');
  const pagination = document.getElementById('pagination');
  const selectTaille = document.getElementById('taille');
  const sousTitre = document.getElementById('sous-titre');
  const nbColonnes = tableau.querySelectorAll('thead th').length;

  const etat = { page: 1, taille: 50, tri: 'nom', ordre: 'asc' };
  let requeteEnCours = null;
  let minuterie = null;

  // Page « Préinscriptions » : filtre statut figé (champ caché data-fixe)
  const modePreinscription = !!form.querySelector('[data-fixe]');
  const selection = new Set();                    // inscriptions cochées (toutes pages)
  const boutonSelection = document.getElementById('inscrire-selection');
  const toutCocher = document.getElementById('tout-cocher');

  // ---------------------------------------------------------- état <-> URL
  function lireUrl() {
    const p = new URLSearchParams(location.search);
    for (const el of form.elements) {
      if (el.hasAttribute('data-fixe')) continue;
      if (el.name && p.has(el.name)) el.value = p.get(el.name);
      // valeur absente des options (ex. lien périmé) : on revient au premier choix
      if (el.tagName === 'SELECT' && el.selectedIndex < 0) el.selectedIndex = 0;
    }
    etat.page = parseInt(p.get('page'), 10) || 1;
    etat.taille = parseInt(p.get('taille'), 10) || 50;
    etat.tri = p.get('tri') || 'nom';
    etat.ordre = p.get('ordre') === 'desc' ? 'desc' : 'asc';
    selectTaille.value = String(etat.taille);
    if (!selectTaille.value) { selectTaille.value = '50'; etat.taille = 50; }
  }

  function parametres(avecPage) {
    const p = new URLSearchParams();
    for (const el of form.elements) {
      if (el.name && el.value) p.set(el.name, el.value.trim());
    }
    p.set('tri', etat.tri);
    p.set('ordre', etat.ordre);
    if (avecPage) {
      p.set('page', etat.page);
      p.set('taille', etat.taille);
    }
    return p;
  }

  // ---------------------------------------------------------- rendu
  const ECHAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const e = (v) => (v === null || v === undefined || v === '') ? '' : String(v).replace(/[&<>"']/g, (c) => ECHAP[c]);
  const tiret = '<span class="text-muted">—</span>';

  function ligneHtml(r, rang) {
    // r = [id, matricule, prenom, nom, sexe, telephone, licence, semestre, programme, promotion,
    //      redouble, cycle, preinscrit]
    const f = r[4] === 'F';
    const initiales = ((r[2] || '').charAt(0) + (r[3] || '').charAt(0)).toUpperCase();
    const sexe = f ? 'F' : 'M';
    const redouble = (r[12] && !modePreinscription ? ' <span class="pill pill-accent ms-1">Préinscrit</span>' : '') +
      (r[10] ? ' <span class="pill pill-attention ms-1">Redoublant</span>' : '');
    const cycle = r[11] ? e(cfg.cycles[r[11]] || r[11]) : tiret;
    const fiche = cfg.urlFiche.replace('/0/', '/' + r[0] + '/');
    let actions = '';
    if (cfg.peutInscrire) {
      actions += '<button type="button" class="btn btn-sm btn-or-plein me-1" data-inscrire="' + r[0] + '" title="Finaliser l\'inscription">' +
        '<i class="bi bi-check2-circle"></i> Inscrire</button>';
    }
    actions += '<a class="btn btn-icone" title="Voir la fiche" href="' + fiche + '"><i class="bi bi-eye"></i></a>';
    if (cfg.estScolarite) {
      actions += '<a class="btn btn-icone" title="Modifier" href="' +
        cfg.urlModifier.replace('/0/', '/' + r[0] + '/') + '"><i class="bi bi-pencil"></i></a>';
    }
    const coche = cfg.peutInscrire
      ? '<td><input type="checkbox" class="form-check-input" data-cocher="' + r[0] + '"' +
        (selection.has(String(r[0])) ? ' checked' : '') + ' aria-label="Sélectionner"></td>'
      : '';
    return '<tr data-id="' + r[0] + '">' + coche +
      '<td class="text-secondary">' + rang + '</td>' +
      '<td>' + (r[1] ? '<span class="matricule">' + e(r[1]) + '</span>' : tiret) + '</td>' +
      '<td><div class="nom-complet"><span class="ini">' + e(initiales) + '</span>' +
        '<span><b>' + e(r[3]) + '</b> ' + e(r[2]) + redouble + '</span></div></td>' +
      '<td>' + sexe + '</td>' +
      '<td class="d-none d-xxl-table-cell">' + (e(r[5]) || tiret) + '</td>' +
      '<td class="d-none d-xl-table-cell">' + cycle + '</td>' +
      '<td>' + (r[6] ? '<span class="etiquette">' + e(r[6]) + '</span>' : tiret) + '</td>' +
      '<td class="d-none d-lg-table-cell">' + (e(r[7]) || tiret) + '</td>' +
      '<td>' + (r[8] ? '<span class="etiquette etiquette-bleu">' + e(r[8]) + '</span>' : tiret) + '</td>' +
      '<td class="d-none d-lg-table-cell">' + (e(r[9]) || tiret) + '</td>' +
      '<td class="text-end">' + actions + '</td></tr>';
  }

  function afficher(d) {
    const debut = (d.page - 1) * d.taille;
    if (!d.lignes.length) {
      corps.innerHTML = '<tr><td colspan="' + nbColonnes + '"><div class="etat-vide">' +
        (modePreinscription
          ? '<i class="bi bi-check2-all"></i><div class="fw-semibold text-body">Aucun préinscrit</div>' +
            '<div class="small">Tous les étudiants correspondant à ces filtres sont inscrits.</div>'
          : '<i class="bi bi-search"></i><div class="fw-semibold text-body">Aucun étudiant trouvé</div>' +
            '<div class="small">Modifiez la recherche ou réinitialisez les filtres.</div>') +
        '</div></td></tr>';
    } else {
      // Une seule écriture dans le DOM pour toute la page
      const html = new Array(d.lignes.length);
      for (let i = 0; i < d.lignes.length; i++) html[i] = ligneHtml(d.lignes[i], debut + i + 1);
      corps.innerHTML = html.join('');
    }
    const fin = Math.min(debut + d.taille, d.total);
    info.textContent = d.total
      ? (debut + 1).toLocaleString('fr-FR') + '–' + fin.toLocaleString('fr-FR') + ' sur ' +
        d.total.toLocaleString('fr-FR') + ' étudiant' + (d.total > 1 ? 's' : '')
      : '0 étudiant';
    if (sousTitre) {
      sousTitre.textContent = modePreinscription
        ? d.total.toLocaleString('fr-FR') + ' préinscrit' + (d.total > 1 ? 's' : '') + ' en attente d\'inscription'
        : d.total.toLocaleString('fr-FR') + ' étudiant' + (d.total > 1 ? 's' : '') +
          ' correspondant' + (d.total > 1 ? 's' : '') + ' aux filtres';
    }
    majSelection();
    // Met en évidence les filtres utilisés
    for (const el of form.elements) {
      if (el.tagName === 'SELECT' && el.name !== 'session') el.classList.toggle('filtre-actif', !!el.value);
    }
    etat.page = d.page;
    afficherPagination(d.page, d.pages);
    tableau.closest('.table-etudiants-wrap').scrollTop = 0;
  }

  function afficherPagination(page, pages) {
    const item = (p, label, actif, desactive) =>
      '<li class="page-item' + (actif ? ' active' : '') + (desactive ? ' disabled' : '') + '">' +
      '<a class="page-link" href="#" data-page="' + p + '">' + label + '</a></li>';
    let html = item(page - 1, '<i class="bi bi-chevron-left"></i>', false, page <= 1);
    const vus = new Set([1, pages, page - 1, page, page + 1]);
    let precedent = 0;
    for (const p of [...vus].filter((x) => x >= 1 && x <= pages).sort((a, b) => a - b)) {
      if (p - precedent > 1) html += '<li class="page-item disabled"><span class="page-link">…</span></li>';
      html += item(p, p, p === page, false);
      precedent = p;
    }
    html += item(page + 1, '<i class="bi bi-chevron-right"></i>', false, page >= pages);
    pagination.innerHTML = html;
  }

  function afficherTri() {
    tableau.querySelectorAll('th[data-tri]').forEach((th) => {
      const icone = th.querySelector('.bi');
      const actif = th.dataset.tri === etat.tri;
      th.classList.toggle('trie', actif);
      icone.className = 'bi ' + (actif ? (etat.ordre === 'asc' ? 'bi-sort-down-alt' : 'bi-sort-up') : 'bi-arrow-down-up');
    });
  }

  // ---------------------------------------------------------- chargement
  function charger() {
    if (requeteEnCours) requeteEnCours.abort();     // annule la requête précédente
    const requete = requeteEnCours = new AbortController();
    const p = parametres(true);
    history.replaceState(null, '', '?' + p.toString());
    tableau.classList.add('chargement');
    afficherTri();
    // Connexion faible : message au bout de 8 s, abandon au bout de 45 s (avec « Réessayer »)
    let expiree = false;
    const lente = setTimeout(() => window.gpedReseau && window.gpedReseau.lente(true), 8000);
    const expiration = setTimeout(() => { expiree = true; requete.abort(); }, 45000);

    fetch(cfg.urlDonnees + '?' + p.toString(), {
      signal: requete.signal,
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
      .then((r) => { if (!r.ok) throw new Error('erreur ' + r.status + ' du serveur'); return r.json(); })
      .then(afficher)
      .catch((err) => {
        if (err.name === 'AbortError' && !expiree) return;          // remplacée par une requête plus récente
        const cause = !navigator.onLine ? "Pas de connexion Internet."
          : expiree ? "Le serveur met trop de temps à répondre (connexion lente)."
          : 'Le chargement a échoué (' + e(err.message) + ').';
        corps.innerHTML = '<tr><td colspan="' + nbColonnes + '" class="text-center py-5">' +
          '<div class="text-danger mb-2"><i class="bi bi-wifi-off"></i> ' + cause + '</div>' +
          '<button type="button" class="btn btn-sm btn-primary" data-reessayer><i class="bi bi-arrow-clockwise"></i> Réessayer</button></td></tr>';
      })
      .finally(() => {
        clearTimeout(lente); clearTimeout(expiration);
        if (window.gpedReseau) window.gpedReseau.lente(false);
        if (requeteEnCours === requete) tableau.classList.remove('chargement');
      });
  }
  corps.addEventListener('click', (ev) => { if (ev.target.closest('[data-reessayer]')) charger(); });

  function rechargerDepuisPage1() { etat.page = 1; charger(); }

  // ---------------------------------------------------------- événements
  form.addEventListener('input', (ev) => {
    clearTimeout(minuterie);
    // Recherche : on attend que l'utilisateur ait fini de taper
    minuterie = setTimeout(rechargerDepuisPage1, ev.target.name === 'q' ? 300 : 0);
  });

  document.getElementById('reinitialiser').addEventListener('click', () => {
    for (const el of form.elements) {
      if (el.hasAttribute('data-fixe')) continue;
      if (el.name === 'session') el.selectedIndex = 0;
      else if (el.name) el.value = '';
    }
    rechargerDepuisPage1();
  });

  // ---------------------------------------------------------- inscrire des préinscrits
  function majSelection() {
    if (!cfg.peutInscrire) return;
    document.getElementById('nb-selection').textContent = selection.size;
    boutonSelection.disabled = selection.size === 0;
    const cases = corps.querySelectorAll('[data-cocher]');
    toutCocher.checked = cases.length > 0 && [...cases].every((c) => c.checked);
  }

  function notifier(message, type) {
    const n = document.createElement('div');
    n.className = 'notif-gped ' + (type || 'succes');
    n.innerHTML = '<i class="bi ' + (type === 'erreur' ? 'bi-exclamation-circle' : 'bi-check-circle-fill') + '"></i> ' + e(message);
    document.body.appendChild(n);
    setTimeout(() => n.classList.add('sortie'), 3200);
    setTimeout(() => n.remove(), 3700);
  }

  function inscrire(ids, bouton) {
    if (!ids.length) return;
    if (ids.length > 1 && !confirm('Inscrire les ' + ids.length + ' étudiants sélectionnés ?')) return;
    const donnees = new FormData();
    ids.forEach((id) => donnees.append('ids', id));
    if (bouton) { bouton.disabled = true; bouton.innerHTML = '<span class="spinner-border spinner-border-sm"></span>'; }
    fetch(cfg.urlInscrire, {
      method: 'POST',
      body: donnees,
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
      },
    })
      .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then((d) => {
        ids.forEach((id) => selection.delete(String(id)));
        notifier(d.inscrits === 1 ? 'Étudiant inscrit.' : d.inscrits + ' étudiants inscrits.');
        const compteur = document.querySelector('.gped-nav .compteur');   // compteur du menu
        if (compteur) {
          const reste = Math.max(0, parseInt(compteur.textContent, 10) - d.inscrits);
          if (reste) compteur.textContent = reste; else compteur.remove();
        }
        charger();                                   // les inscrits disparaissent de la liste
      })
      .catch((err) => {
        notifier('Inscription impossible (' + err.message + ').', 'erreur');
        charger();
      });
  }

  if (cfg.peutInscrire) {
    corps.addEventListener('change', (ev) => {
      const c = ev.target.closest('[data-cocher]');
      if (!c) return;
      if (c.checked) selection.add(c.dataset.cocher); else selection.delete(c.dataset.cocher);
      majSelection();
    });
    toutCocher.addEventListener('change', () => {
      corps.querySelectorAll('[data-cocher]').forEach((c) => {
        c.checked = toutCocher.checked;
        if (c.checked) selection.add(c.dataset.cocher); else selection.delete(c.dataset.cocher);
      });
      majSelection();
    });
    boutonSelection.addEventListener('click', () => inscrire([...selection], null));
  }

  selectTaille.addEventListener('change', () => {
    etat.taille = parseInt(selectTaille.value, 10);
    rechargerDepuisPage1();
  });

  pagination.addEventListener('click', (ev) => {
    const a = ev.target.closest('a[data-page]');
    if (!a) return;
    ev.preventDefault();
    if (a.parentElement.classList.contains('disabled')) return;
    etat.page = parseInt(a.dataset.page, 10);
    charger();
  });

  tableau.querySelector('thead').addEventListener('click', (ev) => {
    const th = ev.target.closest('th[data-tri]');
    if (!th) return;
    if (etat.tri === th.dataset.tri) etat.ordre = etat.ordre === 'asc' ? 'desc' : 'asc';
    else { etat.tri = th.dataset.tri; etat.ordre = 'asc'; }
    rechargerDepuisPage1();
  });

  corps.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-inscrire]');
    if (b) { inscrire([b.dataset.inscrire], b); return; }
    if (ev.target.closest('a, input, button')) return;
    const tr = ev.target.closest('tr[data-id]');
    if (!tr) return;
    const url = cfg.urlFiche.replace('/0/', '/' + tr.dataset.id + '/');
    if (ev.ctrlKey || ev.metaKey) window.open(url, '_blank'); else location.href = url;
  });

  document.querySelectorAll('[data-export]').forEach((a) => {
    a.addEventListener('click', (ev) => {
      ev.preventDefault();
      const p = parametres(false);
      p.set('format', a.dataset.export);
      location.href = cfg.urlExport + '?' + p.toString();   // exporte exactement la liste filtrée
    });
  });

  lireUrl();
  charger();
})();
