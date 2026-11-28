"""
Suivi des cours : CIAQ (Cellule interne de contrôle de qualité), Direction des Études, Direction.

- Avancement (CIAQ, Direction) : par programme, par matière ou par enseignant, les contenus des plans de cours
  dispensés et les heures prévues (emploi du temps) comparées aux heures faites (cours validés par un délégué).
- Situation des enseignants sur l'année (CIAQ, Direction) : permanents (équivalent TD, charge statutaire, heures
  supplémentaires, montant) et vacataires (vacation sur les heures faites), selon l'arrêté A/2024/108/MESRSI/SGG et
  le décret D/2024/0027 ; pfe/heures.py.
- Paramètres des heures : les valeurs de ces deux textes, mises à jour par la CIAQ si un texte change.
- Dates des semestres et cours dispensés (Direction des Études) : relance d'un cours dont le délai (72 h) est
  dépassé, réajustement des heures après la procédure administrative (l'enseignant puis un délégué signent de
  nouveau). La CIAQ et la Direction voient les cours dispensés.
"""
from datetime import date as _date

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import emplois, heures, plans_cours
from .forms import BootstrapMixin
from .models import (DatesSemestre, Enseignant, Matiere, ParametresHeures, PlanCours, ReajustementSeance, SeanceCours,
                     Service, Session)
from .roles import departements_etablissement, get_profil, heures_lecture_requise, parametres_heures_requis, \
    reajustement_requis


def _entier(v):
    return int(v) if str(v or '').isdigit() else None


def _session(request):
    sessions = list(Session.objects.order_by('-annee'))
    sid = _entier(request.GET.get('session'))
    choisie = (next((s for s in sessions if s.pk == sid), None) or next((s for s in sessions if s.en_cours), None)
               or (sessions[0] if sessions else None))
    return choisie, sessions


def _periode(request):
    p = request.GET.get('periode')
    if p in dict(heures.PERIODES):
        return p
    return 'impair' if timezone.localdate().month >= 9 or timezone.localdate().month <= 2 else 'pair'


def _cours_lecture(profil):
    return profil.voit_heures or profil.reajuste_cours


# ------------------------------------------------------------------ avancement

VUES = [('programme', 'Par programme'), ('matiere', 'Par matière'), ('enseignant', 'Par enseignant')]


@login_required
@heures_lecture_requise
def ciaq_avancement(request):
    session, sessions = _session(request)
    periode = _periode(request)
    vue = request.GET.get('vue') if request.GET.get('vue') in dict(VUES) else 'programme'
    programme_id = _entier(request.GET.get('programme'))
    programmes = list(Service.objects.filter(programme=True).order_by('nom'))
    lignes, semaines = ([], None)
    if session:
        matieres = Matiere.objects.filter(session=session).select_related('filiere', 'licence', 'semestre')
        if programme_id:
            matieres = matieres.filter(filiere_id=programme_id)
        matieres = {m.pk: m for m in matieres if heures.dans_periode(m, periode)}
        brutes, semaines = heures.comparaison(session, periode, matieres=list(matieres.values()))
        brutes = [l for l in brutes if l['matiere_id'] in matieres]
        ens = {e.pk: e for e in Enseignant.objects.filter(pk__in={l['enseignant_id'] for l in brutes})}
        plans = {(p.matiere_id, p.enseignant_id): p for p in PlanCours.objects.filter(matiere_id__in=list(matieres))}
        groupes = {}
        for l in brutes:
            m, e = matieres[l['matiere_id']], ens.get(l['enseignant_id'])
            if vue == 'programme':
                cle, nom, detail = m.filiere_id, m.filiere.nom if m.filiere else '—', ''
            elif vue == 'matiere':
                cle, nom, detail = m.pk, m.nom, emplois.libelle_classe(m)
            else:
                cle, nom, detail = l['enseignant_id'], str(e) if e else '—', (e.get_statut_display() if e else '')
            g = groupes.setdefault(cle, {'nom': nom, 'detail': detail, 'hebdo': 0, 'semestre': 0, 'a_ce_jour': 0,
                                         'fait': 0, 'attente': 0, 'faits': 0, 'contenus': 0, 'plans': 0,
                                         'approuves': 0, 'sans_plan': 0, 'enseignants': set(), 'matieres': set()})
            for k in ('hebdo', 'semestre', 'fait', 'attente'):
                g[k] += l[k]
            g['a_ce_jour'] = None if semaines is None else g['a_ce_jour'] + l['a_ce_jour']
            g['enseignants'].add(l['enseignant_id'])
            g['matieres'].add(l['matiere_id'])
            p = plans.get((l['matiere_id'], l['enseignant_id']))
            if p is None:
                g['sans_plan'] += 1
            else:
                g['plans'] += 1
                if p.statut == PlanCours.APPROUVE:
                    g['approuves'] += 1
                    a = p.avancement()
                    g['faits'] += a['confirmees']
                    g['contenus'] += a['total']
        lignes = sorted(heures.avec_barres(list(groupes.values())), key=lambda g: g['nom'])
        for g in lignes:
            g['pct_contenus'] = round(100 * g['faits'] / g['contenus']) if g['contenus'] else None
            g['nb_enseignants'], g['nb_matieres'] = len(g.pop('enseignants')), len(g.pop('matieres'))
            if vue == 'programme':
                g['detail'] = f"{g['nb_matieres']} matière(s) · {g['nb_enseignants']} enseignant(s)"
    if request.GET.get('imprimer') == 'pdf':
        from .pdf_listes import imprimer_tableau
        colonnes = [('N°', 18), (dict(VUES)[vue].replace('Par ', '').capitalize(), 190), ('Détail', 150),
                    ('Prévu', 55), ('Fait', 55), ('%', 30), ('En attente', 45), ('Contenus dispensés', 80),
                    ('Sans plan', 45)]
        rangs = [[k, g['nom'], g['detail'], g['reference_txt'], g['fait_txt'], f"{g['pct']} %" if g['pct'] is not None
                  else '—', g['attente'], f"{g['faits']}/{g['contenus']}" if g['contenus'] else '—', g['sans_plan']]
                 for k, g in enumerate(lignes, start=1)]
        filtres = [('Session', session), ('Période', dict(heures.PERIODES)[periode]),
                   ('Programme', next((p.nom for p in programmes if p.pk == programme_id), None)),
                   ('Prévu', f"à ce jour ({semaines} semaines)" if semaines is not None else 'sur le semestre')]
        return imprimer_tableau(request, 'avancement_cours', 'AVANCEMENT DES COURS', filtres, colonnes, rangs,
                                paysage=True, total=f"{len(rangs)} ligne(s)", vide='Aucun cours pour ces filtres.')
    return render(request, 'pfe/ciaq/avancement.html', {
        'lignes': lignes, 'semaines': semaines, 'session': session, 'sessions': sessions, 'periode': periode,
        'periodes': heures.PERIODES, 'vue': vue, 'vues': VUES, 'programmes': programmes,
        'programme_id': programme_id, 'params': ParametresHeures.actuels(), 'actif': 'suivi_avancement'})


# ------------------------------------------------------------------ situation des enseignants

@login_required
@heures_lecture_requise
def ciaq_situation(request):
    """Situation de l'ANNÉE (session) : la charge statutaire de l'arrêté est annuelle et couvre les deux semestres."""
    session, sessions = _session(request)
    onglet = 'vacataires' if request.GET.get('onglet') == 'vacataires' else 'permanents'
    departement_id = _entier(request.GET.get('departement'))
    departements = list(departements_etablissement())
    params = ParametresHeures.actuels()
    permanents, vacataires = [], []
    if session:
        qs = Enseignant.objects.filter(actif=True)
        if departement_id:
            qs = qs.filter(departement_id=departement_id)
        permanents, vacataires = heures.situation(session, list(qs), params)
    lignes = vacataires if onglet == 'vacataires' else permanents
    totaux = {'reel': heures.fmt(sum(l['reel'] for l in lignes)),
              'eq_td': heures.fmt(sum(l['eq_td'] for l in lignes)),
              'edt': heures.fmt(sum(l['edt_annee'] for l in lignes)),
              'hs': heures.fmt(sum(l.get('hs') or 0 for l in lignes)),
              'montant': heures.gnf(sum(l.get('montant') or 0 for l in lignes))}
    if request.GET.get('imprimer') == 'pdf':
        from .pdf_listes import imprimer_tableau
        if onglet == 'vacataires':
            colonnes = [('N°', 18), ('Enseignant', 160), ('Grade', 90), ('Département', 130),
                        ('Prévu (EDT)', 65), ('Fait (validé)', 65), ('Taux', 75), ('Vacation', 90)]
            rangs = [[k, str(l['enseignant']), l['grade'], l['enseignant'].departement.nom, l['edt_annee_txt'],
                      l['reel_txt'], l['taux_txt'], l['montant_txt']] for k, l in enumerate(lignes, start=1)]
        else:
            colonnes = [('N°', 18), ('Enseignant', 130), ('Grade', 72), ('Département', 95), ('Fait', 45),
                        ('Éq. TD', 45), ('Charge', 45), ('H. sup.', 45), ('Taux', 60), ('Montant', 75),
                        ('Observations', 130)]
            rangs = [[k, str(l['enseignant']) + (' (fonction adm.)' if l['enseignant'].fonction_administrative else ''),
                      l['grade'], l['enseignant'].departement.nom, l['reel_txt'], l['eq_td_txt'],
                      l.get('charge_txt', '—'), l.get('hs_txt', '—'), l['taux_txt'], l['montant_txt'],
                      ' ; '.join(l['alertes'])] for k, l in enumerate(lignes, start=1)]
        filtres = [('Année universitaire', session),
                   ('Département', next((d.nom for d in departements if d.pk == departement_id), None)),
                   ('Textes', 'Arrêté A/2024/108/MESRSI/SGG ; décret D/2024/0027')]
        titre = 'SITUATION DES ENSEIGNANTS VACATAIRES' if onglet == 'vacataires' else             'SITUATION DES ENSEIGNANTS PERMANENTS'
        return imprimer_tableau(request, f'situation_{onglet}', titre, filtres, colonnes, rangs, paysage=True,
                                total=f"{len(rangs)} enseignant(s) · montant : {totaux['montant']}",
                                vide='Aucun enseignant pour ces filtres.')
    return render(request, 'pfe/ciaq/situation.html', {
        'lignes': lignes, 'onglet': onglet, 'nb_permanents': len(permanents), 'nb_vacataires': len(vacataires),
        'totaux': totaux, 'session': session, 'sessions': sessions, 'departements': departements,
        'departement_id': departement_id, 'params': params, 'actif': 'suivi_situation'})


# ------------------------------------------------------------------ paramètres des heures

class ParametresHeuresForm(BootstrapMixin, forms.ModelForm):
    """Les valeurs de l'arrêté A/2024/108 et du décret D/2024/0027, et rien d'autre (voir ParametresHeures)."""
    class Meta:
        model = ParametresHeures
        fields = ['semaines_semestre', 'coef_cm', 'coef_tp', 'reduction_fonction', 'hs_plafond_pct',
                  'hs_plafond_hebdo', 'cm_rang_a_pct']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lignes_grades = []
        grades = dict(Enseignant.GRADES)
        for code in ParametresHeures.GRADES_TEXTES:
            g = self.instance.grade(code)
            self.fields[f'charge_{code}'] = forms.DecimalField(
                label=f"Charge annuelle : {grades[code]}", min_value=0, max_digits=6, decimal_places=1,
                initial=g.get('charge'))
            self.fields[f'taux_{code}'] = forms.IntegerField(label=f"Taux : {grades[code]}", min_value=0,
                                                             initial=g.get('taux'))
            for n in (f'charge_{code}', f'taux_{code}'):
                self.fields[n].widget.attrs.update({'class': 'form-control', 'inputmode': 'decimal'})
            semaine = (float(g['charge']) / self.instance.semaines_annee) if g.get('charge') else None
            self.lignes_grades.append((grades[code], self[f'charge_{code}'], self[f'taux_{code}'],
                                       heures.fmt(semaine) if semaine is not None else '—'))

    def save(self, commit=True):
        p = super().save(commit=False)
        p.grades = {code: {'charge': float(self.cleaned_data[f'charge_{code}']),
                           'taux': self.cleaned_data[f'taux_{code}']} for code in ParametresHeures.GRADES_TEXTES}
        if commit:
            p.save()
        return p


@login_required
@heures_lecture_requise
def ciaq_parametres(request):
    profil = get_profil(request)
    params = ParametresHeures.actuels()
    if request.method == 'POST':
        if not profil.gere_parametres_heures:
            raise PermissionDenied("Les paramètres des heures sont définis par la CIAQ.")
        form = ParametresHeuresForm(request.POST, instance=params)
        if form.is_valid():
            p = form.save(commit=False)
            p.modifie_par = request.user
            p.save()
            messages.success(request, "Paramètres des heures enregistrés : la situation des enseignants est recalculée.")
            return redirect('ciaq_parametres')
    else:
        form = ParametresHeuresForm(instance=params)
    if not profil.gere_parametres_heures:
        for f in form.fields.values():
            f.disabled = True
    return render(request, 'pfe/ciaq/parametres.html', {'form': form, 'params': params,
                                                        'modifiable': profil.gere_parametres_heures,
                                                        'actif': 'suivi_parametres'})


# ------------------------------------------------------------------ Direction des Études : dates des semestres

@login_required
@reajustement_requis
def dates_semestres(request):
    session, sessions = _session(request)
    dates = {d.periode: d for d in DatesSemestre.objects.filter(session=session)} if session else {}
    erreurs = []
    if request.method == 'POST' and session:
        valeurs = {}
        for code, libelle in DatesSemestre.PERIODES:
            debut, fin = request.POST.get(f'debut_{code}') or '', request.POST.get(f'fin_{code}') or ''
            if not debut and not fin:
                valeurs[code] = None
                continue
            try:
                d, f = _date.fromisoformat(debut), _date.fromisoformat(fin)
            except ValueError:
                erreurs.append(f"{libelle} : indiquez la date de début et la date de fin.")
                continue
            if f <= d:
                erreurs.append(f"{libelle} : la fin doit être après le début.")
            valeurs[code] = (d, f)
        if not erreurs:
            for code, v in valeurs.items():
                if v is None:
                    DatesSemestre.objects.filter(session=session, periode=code).delete()
                else:
                    DatesSemestre.objects.update_or_create(session=session, periode=code,
                                                           defaults={'debut': v[0], 'fin': v[1]})
            messages.success(request, f"Dates des semestres de {session.annee} enregistrées.")
            return redirect(f"{reverse('dates_semestres')}?session={session.pk}")
    params = ParametresHeures.actuels()
    lignes = []
    for code, libelle in DatesSemestre.PERIODES:
        d = dates.get(code)
        lignes.append({'code': code, 'libelle': libelle, 'debut': request.POST.get(f'debut_{code}') if erreurs else
                       (d.debut.isoformat() if d else ''),
                       'fin': request.POST.get(f'fin_{code}') if erreurs else (d.fin.isoformat() if d else ''),
                       'semaines': heures.semaines_ecoulees(session, code, params) if d else None})
    return render(request, 'pfe/ciaq/dates_semestres.html', {
        'lignes': lignes, 'erreurs': erreurs, 'session': session, 'sessions': sessions, 'params': params,
        'actif': 'suivi_dates'})


# ------------------------------------------------------------------ cours dispensés (Direction des Études)

ETATS_COURS = [('attente', 'En attente du délégué'), ('expires', 'Délai dépassé'), ('resigner', 'Réajustés, à signer'),
               ('validee', 'Validés'), ('reajustes', 'Réajustés')]


def _filtrer_cours(request, session):
    g = request.GET
    f = {'etat': g.get('etat') if g.get('etat') in dict(ETATS_COURS) else '', 'q': (g.get('q') or '').strip(),
         'programme': _entier(g.get('programme'))}
    qs = (SeanceCours.objects.filter(matiere__session=session)
          .select_related('matiere__filiere', 'matiere__licence', 'matiere__semestre', 'enseignant', 'validee_par',
                          'plan').prefetch_related('elements__partie', 'reajustements'))
    maintenant = timezone.now()
    if f['etat'] == 'attente':
        qs = qs.filter(statut__in=(SeanceCours.ATTENTE, SeanceCours.ATTENTE_DELEGUE_REAJ)).filter(
            Q(expire_le__isnull=True) | Q(expire_le__gte=maintenant))
    elif f['etat'] == 'expires':
        qs = qs.filter(statut__in=(SeanceCours.ATTENTE, SeanceCours.ATTENTE_DELEGUE_REAJ), expire_le__lt=maintenant)
    elif f['etat'] == 'resigner':
        qs = qs.filter(statut=SeanceCours.A_RESIGNER)
    elif f['etat'] == 'validee':
        qs = qs.filter(statut=SeanceCours.VALIDEE)
    elif f['etat'] == 'reajustes':
        qs = qs.filter(reajustements__isnull=False).distinct()
    if f['programme']:
        qs = qs.filter(matiere__filiere_id=f['programme'])
    for terme in f['q'].split()[:4]:
        qs = qs.filter(Q(enseignant__nom__icontains=terme) | Q(enseignant__prenom__icontains=terme)
                       | Q(matiere__nom__icontains=terme))
    return f, qs.order_by('-date', '-pk')


def _preparer(s):
    s.type_court = SeanceCours.COURTS.get(s.type, s.type)
    s.duree_txt = heures.fmt(s.duree) if s.duree is not None else '—'
    s.reajustement = next(iter(s.reajustements.all()), None)
    par_chapitre = {}
    for e in s.elements.all():
        par_chapitre.setdefault(e.partie, []).append(e)
    s.par_chapitre = sorted(par_chapitre.items(), key=lambda kv: (kv[0].ordre, kv[0].pk))
    return s


@login_required
def cours_dispenses(request):
    profil = get_profil(request)
    if not _cours_lecture(profil):
        raise PermissionDenied("Cours dispensés : réservé à la Direction des Études, à la CIAQ et à la Direction.")
    session, sessions = _session(request)
    if request.method == 'POST':
        if not profil.reajuste_cours:
            raise PermissionDenied("Seule la Direction des Études relance un cours.")
        s = get_object_or_404(SeanceCours.objects.select_related('matiere', 'enseignant'),
                              pk=_entier(request.POST.get('cours')))
        if s.statut in (SeanceCours.ATTENTE, SeanceCours.ATTENTE_DELEGUE_REAJ):
            _relancer(request, s)
        return redirect(request.POST.get('suivant') or reverse('cours_dispenses'))
    f, qs = _filtrer_cours(request, session) if session else ({'etat': '', 'q': '', 'programme': None},
                                                              SeanceCours.objects.none())
    from django.core.paginator import Paginator
    page = Paginator(qs, 50).get_page(request.GET.get('page'))
    for s in page:
        _preparer(s)
    maintenant = timezone.now()
    base = SeanceCours.objects.filter(matiere__session=session) if session else SeanceCours.objects.none()
    compteurs = {'attente': base.filter(statut__in=(SeanceCours.ATTENTE, SeanceCours.ATTENTE_DELEGUE_REAJ))
                 .filter(Q(expire_le__isnull=True) | Q(expire_le__gte=maintenant)).count(),
                 'expires': base.filter(statut__in=(SeanceCours.ATTENTE, SeanceCours.ATTENTE_DELEGUE_REAJ),
                                        expire_le__lt=maintenant).count(),
                 'resigner': base.filter(statut=SeanceCours.A_RESIGNER).count(),
                 'validee': base.filter(statut=SeanceCours.VALIDEE).count()}
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'pfe/ciaq/cours_dispenses.html', {
        'page': page, 'filtres': f, 'etats': ETATS_COURS, 'compteurs': compteurs, 'session': session,
        'sessions': sessions, 'programmes': Service.objects.filter(programme=True).order_by('nom'),
        'params': params.urlencode(), 'actif': 'suivi_cours'})


def _relancer(request, s):
    try:
        adresses = plans_cours.envoyer_liens(request, s)
    except Exception as e:                                     # SMTP indisponible…
        messages.error(request, f"Le lien n'a pas pu être envoyé ({e}).")
        return
    if adresses:
        messages.success(request, f"Validation relancée : lien envoyé à {', '.join(adresses)}, à valider avant le "
                                  f"{timezone.localtime(s.expire_le):%d/%m/%Y à %H:%M}.")
    else:
        messages.warning(request, "Aucun délégué avec une adresse e-mail pour cette classe.")


def _heure(v):
    from datetime import datetime
    try:
        return datetime.strptime((v or '').strip(), '%H:%M').time()
    except ValueError:
        return None


@login_required
def cours_dispense(request, pk):
    """Un cours dispensé : contenus, heures, signatures, réajustements ; réajustement par la Direction des Études."""
    profil = get_profil(request)
    if not _cours_lecture(profil):
        raise PermissionDenied("Réservé à la Direction des Études, à la CIAQ et à la Direction.")
    s = get_object_or_404(SeanceCours.objects.select_related(
        'matiere__filiere', 'matiere__licence', 'matiere__semestre', 'matiere__session', 'enseignant', 'validee_par',
        'plan').prefetch_related('elements__partie', 'reajustements__par'), pk=pk)
    erreurs = []
    if request.method == 'POST':
        if not profil.reajuste_cours:
            raise PermissionDenied("Seule la Direction des Études réajuste les heures d'un cours.")
        if request.POST.get('action') == 'relancer':
            if s.statut in (SeanceCours.ATTENTE, SeanceCours.ATTENTE_DELEGUE_REAJ):
                _relancer(request, s)
            return redirect('cours_dispense', pk=s.pk)
        arrivee, depart = _heure(request.POST.get('arrivee')), _heure(request.POST.get('depart'))
        motif = (request.POST.get('motif') or '').strip()
        piece = request.FILES.get('justificatif')
        if not (arrivee and depart):
            erreurs.append("Indiquez les nouvelles heures d'arrivée et de départ (ex. 08:00).")
        elif depart <= arrivee:
            erreurs.append("L'heure de départ doit être après l'heure d'arrivée.")
        if not motif:
            erreurs.append("Indiquez le motif (la procédure administrative suivie).")
        if piece and (not piece.name.lower().endswith('.pdf') or piece.size > 10 * 1024 * 1024):
            erreurs.append("La pièce justificative doit être un PDF de 10 Mo au plus.")
        if not erreurs:
            with transaction.atomic():
                r = ReajustementSeance(seance=s, ancienne_arrivee=s.heure_arrivee, ancien_depart=s.heure_depart,
                                       nouvelle_arrivee=arrivee, nouveau_depart=depart, motif=motif[:3000],
                                       par=request.user)
                if piece:
                    r.justificatif.save(f"reajustement_{s.pk}.pdf", piece, save=False)
                r.save()
                s.heure_arrivee, s.heure_depart = arrivee, depart
                s.statut, s.validee_le, s.validee_par, s.expire_le = SeanceCours.A_RESIGNER, None, None, None
                s.save()
            _avertir_enseignant(request, s)
            messages.success(request, "Heures réajustées : l'enseignant doit les signer, puis un délégué.")
            return redirect('cours_dispense', pk=s.pk)
    _preparer(s)
    return render(request, 'pfe/ciaq/cours_dispense.html', {
        's': s, 'reajustements': list(s.reajustements.all()), 'erreurs': erreurs, 'post': request.POST,
        'reajuste': profil.reajuste_cours, 'actif': 'suivi_cours'})


def _avertir_enseignant(request, s):
    """E-mail à l'enseignant : ses heures ont été réajustées, il doit signer de nouveau."""
    from django.conf import settings
    from django.core.mail import send_mail
    from .comptes import _adresse_site
    adresse = (s.enseignant.email or '').strip()
    if '@' not in adresse:
        return
    lien = f"{_adresse_site(request)}{reverse('plan_cours', args=[s.matiere_id])}?resigner={s.pk}#signature"
    texte = (f"Bonjour {s.enseignant.prenom} {s.enseignant.nom},\n\nLa Direction des Études a réajusté les heures de "
             f"votre cours « {s.matiere.nom} » du {s.date:%d/%m/%Y} : {s.heure_arrivee:%H:%M} – {s.heure_depart:%H:%M}."
             f"\nSignez ces heures dans votre espace enseignant ; un délégué les signera ensuite :\n{lien}\n\n"
             f"GePed · Institut Supérieur de Technologie de Mamou\n")
    try:
        send_mail(f"GePed : heures réajustées · {s.matiere.nom} du {s.date:%d/%m/%Y}", texte,
                  settings.DEFAULT_FROM_EMAIL, [adresse])
    except Exception as e:                                     # SMTP indisponible : l'accueil de l'enseignant l'affiche
        messages.warning(request, f"L'e-mail à l'enseignant n'a pas pu être envoyé ({e}) ; il verra le cours à signer "
                                  f"dans son espace.")
