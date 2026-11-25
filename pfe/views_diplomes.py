"""
Diplômés (registre des diplômes) : liste et statistiques, fiche, import Excel, annulation (scolarité ; la
Direction consulte), journal des vérifications, et la page PUBLIQUE de vérification d'un diplôme.
Règles : pfe/diplomes.py.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import diplomes
from .models import Diplome, Service, VerificationDiplome
from .roles import diplomes_gestion_requise, diplomes_lecture_requise

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _filtres(request):
    g = request.GET
    f = {'q': (g.get('q') or '').strip(), 'annee': g.get('annee') or '', 'programme': g.get('programme') or '',
         'format': g.get('format') if g.get('format') in dict(Diplome.FORMATS) else '',
         'etat': g.get('etat') if g.get('etat') in ('valides', 'annules') else ''}
    qs = Diplome.objects.all()
    if f['annee'].isdigit():
        qs = qs.filter(annee_obtention=int(f['annee']))
    if f['programme']:
        qs = qs.filter(programme=f['programme'])
    if f['format']:
        qs = qs.filter(format=f['format'])
    if f['etat'] == 'valides':
        qs = qs.filter(annule=False)
    elif f['etat'] == 'annules':
        qs = qs.filter(annule=True)
    for terme in f['q'].split()[:4]:
        qs = qs.filter(Q(nom__icontains=terme) | Q(prenoms__icontains=terme) | Q(matricule__icontains=terme)
                       | Q(ine__icontains=terme) | Q(numero__icontains=terme))
    return f, qs


@login_required
@diplomes_lecture_requise
def diplomes_liste(request):
    f, qs = _filtres(request)
    if request.GET.get('export') == 'xlsx':
        return _export(qs)
    if request.GET.get('imprimer') == 'pdf':
        return _imprimer(request, f, qs)
    if request.GET.get('imprimer') == 'stats':
        return _imprimer_stats(request, f, qs)
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    tous = Diplome.objects.all()
    return render(request, 'pfe/diplomes_liste.html', {
        'f': f, 'page': page, 'stats': diplomes.statistiques(qs), 'formats': Diplome.FORMATS,
        'annees': [a for a in tous.order_by('-annee_obtention').values_list('annee_obtention', flat=True).distinct()
                   if a],
        'programmes': [p for p in tous.order_by('programme').values_list('programme', flat=True).distinct() if p],
        'nb_verifications': VerificationDiplome.objects.count(), 'actif': 'diplomes',
    })


def _texte_filtres(f):
    return [('Année d\'obtention', f['annee'] or None), ('Programme', f['programme'] or None),
            ('Format', dict(Diplome.FORMATS).get(f['format'])),
            ('État', {'valides': 'Valides', 'annules': 'Annulés'}.get(f['etat'])), ('Recherche', f['q'] or None)]


def _export(qs):
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Diplomés'
    entetes = ['Numéro', 'Format', 'Matricule', 'INE', 'Civilité', 'Nom', 'Prénoms', 'Né(e) le', 'Né(e) en', 'À', 'Nationalité',
               'Programme', 'Diplôme', 'Grade', 'Finalité', 'Mention', 'Moyenne', 'Année universitaire', 'Promotion',
               'Fait à', 'Délivré le', "Année d'obtention", 'État']
    ws.append(entetes)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='41627C')
    for d in qs:
        ws.append([d.numero or '', d.get_format_display(), d.matricule, d.ine, d.civilite, d.nom, d.prenoms,
                   d.date_naissance, d.annee_naissance, d.lieu_naissance, d.nationalite, d.programme, d.intitule, d.grade, d.finalite,
                   d.mention, d.moyenne, d.annee_universitaire, d.promotion, d.lieu_delivrance, d.date_delivrance,
                   d.annee_obtention, 'Annulé' if d.annule else 'Valide'])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 16
    flux = io.BytesIO()
    wb.save(flux)
    r = HttpResponse(flux.getvalue(), content_type=XLSX)
    r['Content-Disposition'] = 'attachment; filename="diplomes_geped.xlsx"'
    return r


def _imprimer(request, f, qs):
    from .pdf_listes import imprimer_tableau
    colonnes = [('N°', 22), ('Numéro du diplôme', 90), ('Nom et prénoms', 150), ('Matricule / INE', 95),
                ('Programme', 160), ('Mention', 60), ('Obtenu en', 50)]
    lignes = [[str(k), d.numero or '—', f"{d.nom} {d.prenoms}", d.ine or d.matricule, d.programme, d.mention,
               str(d.annee_obtention or '')] for k, d in enumerate(qs, 1)]
    return imprimer_tableau(request, 'diplomes', 'REGISTRE DES DIPLÔMÉS', _texte_filtres(f), colonnes, lignes,
                            paysage=True, total=f"{len(lignes)} diplôme{'s' if len(lignes) > 1 else ''}",
                            vide='Aucun diplôme avec ces filtres.')


def _imprimer_stats(request, f, qs):
    from .pdf_listes import imprimer_sections
    s = diplomes.statistiques(qs)
    t = s['total']
    return imprimer_sections(request, 'statistiques_diplomes', 'STATISTIQUES DES DIPLÔMÉS', _texte_filtres(f),
                             diplomes.sections_pdf(s),
                             total=f"{t['total']} diplômé{'s' if t['total'] > 1 else ''} · {t['h']} hommes · "
                                   f"{t['f']} femmes · {t['verifications']} vérification{'s' if t['verifications'] > 1 else ''}")


@login_required
@diplomes_lecture_requise
def diplome_fiche(request, pk):
    d = get_object_or_404(Diplome.objects.select_related('etudiant', 'filiere', 'importe_par', 'annule_par'), pk=pk)
    return render(request, 'pfe/diplome_fiche.html', {
        'd': d, 'infos': diplomes.infos_publiques(d), 'verifications': d.verifications.all()[:50],
        'actif': 'diplomes'})


@login_required
@diplomes_gestion_requise
@require_POST
def diplome_annuler(request, pk):
    d = get_object_or_404(Diplome, pk=pk)
    if request.POST.get('action') == 'retablir':
        d.annule, d.motif_annulation, d.annule_le, d.annule_par = False, '', None, None
        d.save()
        messages.success(request, f"Le diplôme {d.numero or 'de ' + d.nom} est de nouveau valide.")
    else:
        motif = (request.POST.get('motif') or '').strip()
        if not motif:
            messages.error(request, "Indiquez le motif de l'annulation.")
        else:
            d.annule, d.motif_annulation, d.annule_le, d.annule_par = True, motif, timezone.now(), request.user
            d.save()
            messages.success(request, f"Diplôme {d.numero or 'de ' + d.nom} annulé : la vérification publique l'indiquera.")
    return redirect('diplome_fiche', pk=d.pk)


@login_required
@diplomes_gestion_requise
def diplomes_importer(request):
    if request.GET.get('modele'):
        r = HttpResponse(diplomes.modele_xlsx(), content_type=XLSX)
        r['Content-Disposition'] = 'attachment; filename="modele_import_diplomes.xlsx"'
        return r
    rapport, erreur = None, ''
    valeurs = {c: '' for c in diplomes.DEFAUTS}
    valeurs.update({'lieu_delivrance': 'Mamou', 'format': 'auto'})
    if request.method == 'POST':
        fichier = request.FILES.get('fichier')
        valeurs = {c: (request.POST.get(c) or '').strip() for c in diplomes.DEFAUTS + ['format']}
        defauts = dict(valeurs)
        try:
            for c in ('date_delivrance', 'date_pv'):
                defauts[c] = diplomes.en_date(defauts[c]) if defauts[c] else None
            if not fichier:
                raise ValueError("Choisissez le fichier Excel des diplômés.")
            rapport = diplomes.importer(fichier, defauts, request.user,
                                        simulation=request.POST.get('simulation') == 'on')
        except ValueError as e:
            erreur = str(e)
        else:
            if not rapport.simulation and rapport.crees:
                messages.success(request, f"{rapport.crees} diplôme{'s' if rapport.crees > 1 else ''} enregistré"
                                          f"{'s' if rapport.crees > 1 else ''}.")
    return render(request, 'pfe/diplomes_importer.html', {
        'rapport': rapport, 'erreur': erreur, 'valeurs': valeurs, 'formats': Diplome.FORMATS,
        'programmes': Service.objects.filter(programme=True).order_by('nom'), 'actif': 'diplomes'})


@login_required
@diplomes_lecture_requise
def diplomes_verifications(request):
    q = (request.GET.get('q') or '').strip()
    resultat = request.GET.get('resultat') if request.GET.get('resultat') in dict(VerificationDiplome.RESULTATS) else ''
    qs = VerificationDiplome.objects.select_related('diplome')
    if resultat:
        qs = qs.filter(resultat=resultat)
    for terme in q.split()[:4]:
        qs = qs.filter(Q(structure__icontains=terme) | Q(identifiant__icontains=terme) | Q(numero__icontains=terme)
                       | Q(diplome__nom__icontains=terme))
    page = Paginator(qs, 30).get_page(request.GET.get('page'))
    return render(request, 'pfe/diplomes_verifications.html', {
        'page': page, 'q': q, 'resultat': resultat, 'resultats': VerificationDiplome.RESULTATS, 'actif': 'diplomes'})


# ------------------------------------------------------------------ page publique

@never_cache
def diplome_verifier(request):
    """Page PUBLIQUE : une structure vérifie un diplôme (son nom, son contact, le matricule ou l'INE du diplômé)."""
    from django.conf import settings
    contexte = {'valeurs': {}, 'erreur': '', 'contact': settings.GPED_ATTESTATION}
    if request.method == 'POST':
        p = request.POST
        valeurs = {k: (p.get(k) or '').strip() for k in ('structure', 'contact', 'identifiant')}
        contexte['valeurs'] = valeurs
        attente = diplomes.bloque(request)
        if attente:
            contexte['erreur'] = f"Trop d'essais : réessayez dans {attente // 60 + 1} minute(s)."
        elif not (valeurs['structure'] and valeurs['contact'] and valeurs['identifiant']):
            contexte['erreur'] = ("Indiquez le nom de votre structure, votre contact (e-mail ou téléphone) et le "
                                  "matricule ou l'INE du diplômé.")
        else:
            resultat, trouves = diplomes.verifier(request, valeurs['structure'], valeurs['contact'],
                                                  valeurs['identifiant'])
            contexte.update({'resultat': resultat,
                             'trouves': [(d, diplomes.infos_publiques(d)) for d in trouves]})
    reponse = render(request, 'pfe/diplome_verifier.html', contexte)
    reponse['X-Robots-Tag'] = 'noindex, nofollow'
    return reponse
