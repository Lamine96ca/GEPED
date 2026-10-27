/*
 * gePed — cadre de signature (espace étudiant) : l'étudiant signe au doigt ou à la souris.
 * Le tracé est copié, à chaque trait, dans le champ caché [data-signature-valeur] (image PNG) ;
 * le serveur la vérifie (pas vide), la rogne et l'enregistre (voir forms.preparer_signature).
 * Bloc attendu : [data-signature] contenant un <canvas>, un bouton [data-signature-effacer]
 * et, dans le même formulaire, le champ caché [data-signature-valeur].
 */
(function () {
  'use strict';
  document.querySelectorAll('[data-signature]').forEach(function (bloc) {
    const toile = bloc.querySelector('canvas');
    const champ = bloc.closest('form').querySelector('[data-signature-valeur]');
    const etat = bloc.querySelector('[data-signature-etat]');
    if (!toile || !champ) return;
    const ctx = toile.getContext('2d');
    let trace = false, enCours = false, dernier = null;

    function dimensionner() {
      // Résolution réelle de l'écran (nette sur téléphone) ; le dessin en cours est effacé
      const r = toile.getBoundingClientRect();
      const ratio = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
      toile.width = Math.round(r.width * ratio);
      toile.height = Math.round(r.height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.lineCap = ctx.lineJoin = 'round';
      ctx.strokeStyle = '#111827';
      ctx.lineWidth = 2.4;
      effacer(false);
    }

    function effacer(vider) {
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, toile.width, toile.height);
      ctx.restore();
      if (vider !== false) {
        trace = false;
        champ.value = '';
        bloc.classList.remove('signe');
        if (etat) etat.textContent = etat.dataset.vide || '';
      }
    }

    function point(ev) {
      const r = toile.getBoundingClientRect();
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    }

    toile.addEventListener('pointerdown', function (ev) {
      ev.preventDefault();
      try { toile.setPointerCapture(ev.pointerId); } catch (e) { /* capture impossible : le tracé continue */ }
      enCours = true;
      dernier = point(ev);
      ctx.beginPath();
      ctx.arc(dernier.x, dernier.y, 1.1, 0, Math.PI * 2);
      ctx.fillStyle = '#111827';
      ctx.fill();
    });
    toile.addEventListener('pointermove', function (ev) {
      if (!enCours) return;
      ev.preventDefault();
      const p = point(ev);
      ctx.beginPath();
      ctx.moveTo(dernier.x, dernier.y);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
      dernier = p;
      trace = true;
    });
    function finir() {
      if (!enCours) return;
      enCours = false;
      if (trace) {
        champ.value = toile.toDataURL('image/png');
        bloc.classList.add('signe');
        if (etat) etat.textContent = 'Signature prête : elle sera enregistrée avec votre demande.';
      }
    }
    toile.addEventListener('pointerup', finir);
    toile.addEventListener('pointercancel', finir);
    toile.addEventListener('pointerleave', finir);

    bloc.querySelector('[data-signature-effacer]')?.addEventListener('click', function () { effacer(); });
    if (etat) etat.dataset.vide = etat.textContent;
    dimensionner();
    // Rotation de l'écran : le cadre change de taille, la signature est à refaire
    let largeur = toile.getBoundingClientRect().width;
    window.addEventListener('resize', function () {
      const l = toile.getBoundingClientRect().width;
      if (Math.abs(l - largeur) > 2) { largeur = l; dimensionner(); champ.value = ''; bloc.classList.remove('signe'); }
    });
  });
})();
